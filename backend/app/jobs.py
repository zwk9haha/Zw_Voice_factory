from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Literal
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field


JobKind = Literal["voxcpm_reference", "quality_render"]
JobStatus = Literal["queued", "running", "complete", "failed"]


class JobRequest(BaseModel):
    kind: JobKind
    text: str = Field(min_length=1, max_length=2_000)
    project_id: str | None = Field(default=None, max_length=120)
    reference_id: str | None = Field(default=None, max_length=120)
    character_id: str | None = Field(default=None, max_length=120)
    segment_id: str | None = Field(default=None, max_length=120)
    voice_prompt: str = Field(default="自然、清晰、稳定", max_length=1_000)
    reference_audio_url: str | None = Field(default=None, max_length=2_000)


class JobRecord(BaseModel):
    job_id: str
    kind: JobKind
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    message: str
    project_id: str | None = None
    reference_id: str | None = None
    character_id: str | None = None
    segment_id: str | None = None
    output_url: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobProblem(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ModelGateway:
    """Boundary between the application queue and the two CUDA model services."""

    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        raise NotImplementedError

    def generate_quality(self, text: str, reference_audio_path: Path) -> bytes:
        raise NotImplementedError

    def runtime_status(self) -> dict[str, object]:
        raise NotImplementedError


class HttpModelGateway(ModelGateway):
    def __init__(self) -> None:
        self.voxcpm_url = os.getenv("ZW_VOICE_VOXCPM_URL", "http://127.0.0.1:9881").rstrip("/")
        self.gpt_sovits_url = os.getenv("ZW_VOICE_GPT_SOVITS_URL", "http://127.0.0.1:9880").rstrip("/")

    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        return self._post_audio(
            f"{self.voxcpm_url}/generate",
            {"text": text, "voice_prompt": voice_prompt},
            "VoxCPM2",
        )

    def generate_quality(self, text: str, reference_audio_path: Path) -> bytes:
        return self._post_audio(
            f"{self.gpt_sovits_url}/tts",
            {
                "text": text,
                "text_lang": "zh",
                "ref_audio_path": str(reference_audio_path),
                "prompt_lang": "zh",
                "prompt_text": "",
                "media_type": "wav",
                "streaming_mode": False,
            },
            "GPT-SoVITS",
        )

    def runtime_status(self) -> dict[str, object]:
        return {
            "launcher_managed": os.getenv("ZW_VOICE_LAUNCHER_MANAGED") == "1",
            "services": {
                "voxcpm2": self._probe(self.voxcpm_url, "/health"),
                "gpt_sovits": self._probe(self.gpt_sovits_url, "/openapi.json"),
            },
        }

    def _post_audio(self, url: str, payload: dict[str, object], service: str) -> bytes:
        for attempt in range(2):
            response = httpx.post(
                url,
                json=payload,
                timeout=httpx.Timeout(900, connect=5),
                trust_env=False,
            )
            if response.status_code not in {502, 503, 504} or attempt == 1:
                self._raise_for_model_error(response, service)
                return self._require_wav(response.content, service)
            print(f"[MODEL] {service} 返回 HTTP {response.status_code}，1 秒后重试", flush=True)
            time.sleep(1)
        raise RuntimeError(f"{service} 请求未完成")

    @staticmethod
    def _raise_for_model_error(response: httpx.Response, service: str) -> None:
        if response.is_success:
            return
        detail = response.text.strip().replace("\n", " ")[:400]
        raise RuntimeError(f"{service} 返回 HTTP {response.status_code}: {detail}")

    @staticmethod
    def _require_wav(content: bytes, service: str) -> bytes:
        if len(content) < 44 or content[:4] != b"RIFF" or content[8:12] != b"WAVE":
            raise RuntimeError(f"{service} 未返回有效 WAV 音频")
        return content

    @staticmethod
    def _probe(base_url: str, path: str) -> dict[str, str]:
        try:
            response = httpx.get(f"{base_url}{path}", timeout=1.5)
            ready = response.is_success
        except httpx.HTTPError:
            ready = False
        return {"status": "ready" if ready else "unavailable", "url": base_url}


ReferenceEventHandler = Callable[[str, str, str, JobStatus, str | None, str | None], None]


class JobService:
    def __init__(
        self,
        workspace_root: Path,
        gateway: ModelGateway,
        reference_event_handler: ReferenceEventHandler | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.gateway = gateway
        self.reference_event_handler = reference_event_handler
        self.audio_root = self.workspace_root / "outputs" / "audio" / "jobs"
        self.state_root = self.workspace_root / "outputs" / "jobs"
        self.audio_root.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, JobRecord] = {}
        self._requests: dict[str, tuple[JobRequest, Path | None]] = {}
        self._lock = threading.Lock()
        # Both workers share one GPU. Serial dispatch prevents inference-time OOMs.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="zw-audio-job")

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def submit(self, request: JobRequest) -> JobRecord:
        if bool(request.project_id) != bool(request.reference_id):
            raise JobProblem(422, "项目参考任务必须同时提供 project_id 和 reference_id")
        reference_path: Path | None = None
        if request.kind == "quality_render":
            if not request.reference_audio_url:
                raise JobProblem(422, "质量渲染需要已确认的参考音频")
            reference_path = self._resolve_reference(request.reference_audio_url)

        now = datetime.now(timezone.utc)
        job_id = uuid.uuid4().hex[:12]
        record = JobRecord(
            job_id=job_id,
            kind=request.kind,
            status="queued",
            progress=0,
            message="已进入 GPU 队列",
            project_id=request.project_id,
            reference_id=request.reference_id,
            character_id=request.character_id,
            segment_id=request.segment_id,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job_id] = record
            self._requests[job_id] = (request, reference_path)
            self._persist(record)
        try:
            self._notify_reference(request, job_id, "queued")
        except Exception as exc:
            with self._lock:
                self._jobs.pop(job_id, None)
                self._requests.pop(job_id, None)
                (self.state_root / f"{job_id}.json").unlink(missing_ok=True)
            raise JobProblem(getattr(exc, "status_code", 409), str(exc)) from exc
        self._log(record)
        self._executor.submit(self._run, job_id)
        return record.model_copy(deep=True)

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise JobProblem(404, "任务不存在或服务已重启")
            return record.model_copy(deep=True)

    def list(self, limit: int) -> list[JobRecord]:
        with self._lock:
            records = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)[:limit]
            return [record.model_copy(deep=True) for record in records]

    def _run(self, job_id: str) -> None:
        try:
            request, reference_path = self._requests[job_id]
            service_name = "VoxCPM2" if request.kind == "voxcpm_reference" else "GPT-SoVITS"
            self._update(job_id, status="running", progress=15, message=f"正在提交到 {service_name}")
            self._notify_reference(request, job_id, "running")
            if request.kind == "voxcpm_reference":
                audio = self.gateway.generate_voxcpm(request.text, request.voice_prompt)
            else:
                assert reference_path is not None
                audio = self.gateway.generate_quality(request.text, reference_path)
            self._update(job_id, status="running", progress=90, message="模型已返回，正在写入 WAV")
            output = self.audio_root / f"{job_id}.wav"
            temporary = output.with_suffix(".wav.tmp")
            temporary.write_bytes(audio)
            temporary.replace(output)
            output_url = f"/media/outputs/audio/jobs/{job_id}.wav"
            self._notify_reference(request, job_id, "complete", output_url=output_url)
            self._update(
                job_id,
                status="complete",
                progress=100,
                message="音频生成完成",
                output_url=output_url,
            )
        except Exception as exc:  # The failure is surfaced through the job record and console.
            try:
                request, _ = self._requests[job_id]
                self._notify_reference(request, job_id, "failed", error=str(exc))
            except Exception:
                pass
            self._update(job_id, status="failed", message="音频生成失败", error=str(exc))
        finally:
            with self._lock:
                self._requests.pop(job_id, None)

    def _update(self, job_id: str, **changes: object) -> None:
        with self._lock:
            record = self._jobs[job_id]
            updated = record.model_copy(update={**changes, "updated_at": datetime.now(timezone.utc)})
            self._jobs[job_id] = updated
            self._persist(updated)
        self._log(updated)

    def _resolve_reference(self, media_url: str) -> Path:
        parsed = urlsplit(media_url)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise JobProblem(400, "参考音频必须来自当前工作区媒体库")
        decoded = unquote(parsed.path)
        prefix_map = {
            "/media/voice-samples/": self.workspace_root / "assets" / "voice_samples",
            "/media/outputs/audio/": self.workspace_root / "outputs" / "audio",
        }
        for prefix, root in prefix_map.items():
            if decoded.startswith(prefix):
                relative = PurePosixPath(decoded[len(prefix) :])
                if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
                    break
                candidate = root.joinpath(*relative.parts).resolve()
                resolved_root = root.resolve()
                if candidate.is_relative_to(resolved_root) and candidate.is_file():
                    return candidate
                break
        raise JobProblem(400, "参考音频路径无效或文件不存在")

    def _notify_reference(
        self,
        request: JobRequest,
        job_id: str,
        status: JobStatus,
        output_url: str | None = None,
        error: str | None = None,
    ) -> None:
        if (
            request.kind == "voxcpm_reference"
            and request.project_id
            and request.reference_id
            and self.reference_event_handler
        ):
            self.reference_event_handler(
                request.project_id,
                request.reference_id,
                job_id,
                status,
                output_url,
                error,
            )

    def _persist(self, record: JobRecord) -> None:
        target = self.state_root / f"{record.job_id}.json"
        target.write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _log(record: JobRecord) -> None:
        suffix = f" | {record.error}" if record.error else ""
        print(f"[JOB {record.job_id}] {record.progress:3d}% {record.message}{suffix}", flush=True)


def create_jobs_router(service: JobService) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["audio-jobs"])

    @router.post("/jobs", response_model=JobRecord, status_code=status.HTTP_202_ACCEPTED)
    def submit_job(request: JobRequest) -> JobRecord:
        try:
            return service.submit(request)
        except JobProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/jobs", response_model=list[JobRecord])
    def list_jobs(limit: int = Query(default=50, ge=1, le=200)) -> list[JobRecord]:
        return service.list(limit)

    @router.get("/jobs/{job_id}", response_model=JobRecord)
    def get_job(job_id: str) -> JobRecord:
        try:
            return service.get(job_id)
        except JobProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/runtime")
    def runtime() -> dict[str, object]:
        return service.gateway.runtime_status()

    return router
