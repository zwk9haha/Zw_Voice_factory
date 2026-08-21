from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import struct
import threading
import time
import uuid
import wave
from array import array
from contextlib import nullcontext
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, Literal
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .loudness import LoudnessMetrics, LoudnessProcessingError, LoudnessProcessor, ProgramLoudnessPolicy
from .production import QualityModelId, QualityRenderOptions, quality_model_spec
from .rvc_contracts import RvcApplyResult, RvcApplyStatus

JobKind = Literal["voxcpm_reference", "emotion_variant", "quality_render", "fast_render"]
JobStatus = Literal["queued", "running", "complete", "failed", "cancelled"]
STREAM_FRAME_METADATA = 1
STREAM_FRAME_AUDIO = 2
STREAM_FRAME_ERROR = 3
STREAM_END = object()


@dataclass
class PcmAudioStream:
    sample_rate: int
    channels: int
    sample_width: int
    chunks: Iterator[bytes]
    close: Callable[[], None]


@dataclass
class QualityStreamSession:
    job_id: str
    frames: Iterator[bytes]


def _resample_pcm16(frames: bytes, channels: int, source_rate: int, target_rate: int) -> bytes:
    if source_rate == target_rate or not frames:
        return frames
    samples = array("h")
    samples.frombytes(frames)
    source_frames = len(samples) // channels
    if source_frames <= 1:
        return frames
    target_frames = max(1, round(source_frames * target_rate / source_rate))
    output = array("h", [0]) * (target_frames * channels)
    position_scale = (source_frames - 1) / max(1, target_frames - 1)
    for target_frame in range(target_frames):
        source_position = target_frame * position_scale
        left_frame = int(source_position)
        right_frame = min(left_frame + 1, source_frames - 1)
        fraction = source_position - left_frame
        for channel in range(channels):
            left_sample = samples[left_frame * channels + channel]
            right_sample = samples[right_frame * channels + channel]
            output[target_frame * channels + channel] = round(
                left_sample + (right_sample - left_sample) * fraction
            )
    return output.tobytes()


class JobRequest(BaseModel):
    kind: JobKind
    text: str = Field(min_length=1, max_length=2_000)
    project_id: str | None = Field(default=None, max_length=120)
    reference_id: str | None = Field(default=None, max_length=120)
    variant_id: str | None = Field(default=None, max_length=120)
    character_id: str | None = Field(default=None, max_length=120)
    segment_id: str | None = Field(default=None, max_length=120)
    voice_prompt: str = Field(default="自然、清晰、稳定", max_length=1_000)
    reference_audio_url: str | None = Field(default=None, max_length=2_000)
    reference_text: str | None = Field(default=None, max_length=1_000)
    quality_model: QualityModelId = "gpt_sovits_v2"
    emotion_description: str | None = Field(default=None, max_length=1_000)
    render_options: QualityRenderOptions = Field(default_factory=QualityRenderOptions)
    fast_voice_id: str = Field(default="suyingxue", max_length=80)
    fast_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    fast_rvc_enabled: bool = True


class JobRecord(BaseModel):
    job_id: str
    kind: JobKind
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    message: str
    project_id: str | None = None
    reference_id: str | None = None
    variant_id: str | None = None
    character_id: str | None = None
    segment_id: str | None = None
    reference_audio_url: str | None = None
    quality_model: QualityModelId | None = None
    render_options: QualityRenderOptions | None = None
    fast_voice_id: str | None = None
    fast_speed: float | None = None
    fast_rvc_enabled: bool | None = None
    streaming: bool = False
    loudness_policy: ProgramLoudnessPolicy = Field(default_factory=ProgramLoudnessPolicy)
    loudness_metrics: LoudnessMetrics | None = None
    base_output_url: str | None = None
    rvc_output_url: str | None = None
    rvc_status: RvcApplyStatus = "not_requested"
    rvc_model_id: str | None = None
    rvc_profile_fingerprint: str | None = None
    rvc_error: str | None = None
    raw_output_url: str | None = None
    output_url: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class MergeRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=5_000)


class MergedAudio(BaseModel):
    project_id: str
    output_url: str
    segment_count: int
    duration_seconds: float
    loudness_metrics: LoudnessMetrics | None = None


class CancelledJobs(BaseModel):
    project_id: str
    cancelled_count: int
    cancelled_jobs: list[JobRecord]


class QualityCacheDeleteRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=5_000)


class LoudnessReprocessRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=5_000)


class RvcReprocessRequest(BaseModel):
    job_ids: list[str] = Field(min_length=1, max_length=5_000)


class DeletedQualityCache(BaseModel):
    project_id: str
    deleted_count: int
    deleted_bytes: int
    deleted_jobs: list[JobRecord]


class JobProblem(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ModelGateway:
    """Boundary between the application queue and the two CUDA model services."""

    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        raise NotImplementedError

    def generate_fast(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        raise NotImplementedError

    def generate_quality(
        self,
        text: str,
        reference_audio_path: Path,
        quality_model: QualityModelId,
        emotion_description: str | None = None,
        render_options: QualityRenderOptions | None = None,
        reference_text: str | None = None,
    ) -> bytes:
        raise NotImplementedError

    def generate_quality_stream(
        self,
        text: str,
        reference_audio_path: Path,
        quality_model: QualityModelId,
        emotion_description: str | None = None,
        render_options: QualityRenderOptions | None = None,
        reference_text: str | None = None,
    ) -> PcmAudioStream:
        raise NotImplementedError

    def runtime_status(self) -> dict[str, object]:
        raise NotImplementedError


class HttpModelGateway(ModelGateway):
    def __init__(self) -> None:
        self.voxcpm_url = os.getenv("ZW_VOICE_VOXCPM_URL", "http://127.0.0.1:9881").rstrip("/")
        self.fast_tts_url = os.getenv("ZW_VOICE_FAST_TTS_URL", "http://127.0.0.1:9883").rstrip("/")
        self.gpt_sovits_url = os.getenv("ZW_VOICE_GPT_SOVITS_URL", "http://127.0.0.1:9880").rstrip("/")
        self.indextts_url = os.getenv("ZW_VOICE_INDEXTTS_URL", "http://127.0.0.1:9882").rstrip("/")
        self._active_gpt_sovits_model: QualityModelId = "gpt_sovits_v2"
        self._quality_model_lock = threading.Lock()

    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        return self._post_audio(
            f"{self.voxcpm_url}/generate",
            {"text": text, "voice_prompt": voice_prompt},
            "VoxCPM2",
        )

    def generate_fast(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        return self._post_audio(
            f"{self.fast_tts_url}/generate",
            {"text": text, "voice_id": voice_id, "speed": speed},
            "轻量 TTS",
        )

    def generate_quality(
        self,
        text: str,
        reference_audio_path: Path,
        quality_model: QualityModelId,
        emotion_description: str | None = None,
        render_options: QualityRenderOptions | None = None,
        reference_text: str | None = None,
    ) -> bytes:
        options = render_options or QualityRenderOptions()
        spec = quality_model_spec(quality_model)
        if spec.renderer == "indextts2":
            return self._post_audio(
                f"{self.indextts_url}/generate",
                {
                    "text": text,
                    "reference_audio_path": str(reference_audio_path),
                    "emotion_text": emotion_description,
                    "emotion_strength": options.emotion_strength,
                    "chunk_length": options.chunk_length,
                    "top_k": options.top_k,
                    "top_p": options.top_p,
                    "temperature": options.temperature,
                    "repetition_penalty": options.repetition_penalty,
                    "fragment_interval": options.fragment_interval,
                    "seed": options.seed,
                },
                "IndexTTS2",
            )
        if quality_model in {"gpt_sovits_v3", "gpt_sovits_v4"} and not (
            reference_text and reference_text.strip()
        ):
            raise RuntimeError(f"{spec.label} 需要与参考音频一致的参考文本")
        self._select_gpt_sovits_model(quality_model)
        return self._post_audio(
            f"{self.gpt_sovits_url}/tts",
            self._gpt_sovits_payload(
                text,
                reference_audio_path,
                options,
                reference_text,
                streaming=False,
            ),
            spec.label,
        )

    def generate_quality_stream(
        self,
        text: str,
        reference_audio_path: Path,
        quality_model: QualityModelId,
        emotion_description: str | None = None,
        render_options: QualityRenderOptions | None = None,
        reference_text: str | None = None,
    ) -> PcmAudioStream:
        options = render_options or QualityRenderOptions()
        spec = quality_model_spec(quality_model)
        if spec.renderer != "gpt_sovits":
            raise RuntimeError(f"{spec.label} 不支持 GPT-SoVITS 分段音频流")
        if quality_model in {"gpt_sovits_v3", "gpt_sovits_v4"} and not (
            reference_text and reference_text.strip()
        ):
            raise RuntimeError(f"{spec.label} 需要与参考音频一致的参考文本")
        self._select_gpt_sovits_model(quality_model)
        return self._open_pcm_wav_stream(
            f"{self.gpt_sovits_url}/tts",
            self._gpt_sovits_payload(
                text,
                reference_audio_path,
                options,
                reference_text,
                streaming=True,
            ),
            spec.label,
        )

    @staticmethod
    def _gpt_sovits_payload(
        text: str,
        reference_audio_path: Path,
        options: QualityRenderOptions,
        reference_text: str | None = None,
        *,
        streaming: bool,
    ) -> dict[str, object]:
        return {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": str(reference_audio_path),
            "prompt_lang": "zh",
            "prompt_text": reference_text.strip() if reference_text else "",
            "text_split_method": "cut5" if options.chunk_length <= 80 else "cut2" if options.chunk_length <= 180 else "cut0",
            "top_k": options.top_k,
            "top_p": options.top_p,
            "temperature": options.temperature,
            "batch_size": options.batch_size,
            "split_bucket": options.split_bucket,
            "speed_factor": options.speed_factor,
            "fragment_interval": options.fragment_interval,
            "seed": options.seed,
            "repetition_penalty": options.repetition_penalty,
            "media_type": "wav",
            "streaming_mode": 1 if streaming else 0,
        }

    def _select_gpt_sovits_model(self, quality_model: QualityModelId) -> None:
        if quality_model == self._active_gpt_sovits_model:
            return
        spec = quality_model_spec(quality_model)
        if spec.renderer != "gpt_sovits" or not spec.gpt_weights or not spec.sovits_weights:
            raise RuntimeError(f"{spec.label} 不是 GPT-SoVITS 权重配置")
        with self._quality_model_lock:
            if quality_model == self._active_gpt_sovits_model:
                return
            self._set_gpt_sovits_weight("set_sovits_weights", spec.sovits_weights, spec.label)
            self._set_gpt_sovits_weight("set_gpt_weights", spec.gpt_weights, spec.label)
            self._active_gpt_sovits_model = quality_model

    def _set_gpt_sovits_weight(self, endpoint: str, weights_path: str, service: str) -> None:
        response = httpx.get(
            f"{self.gpt_sovits_url}/{endpoint}",
            params={"weights_path": weights_path},
            timeout=httpx.Timeout(900, connect=5),
            trust_env=False,
        )
        self._raise_for_model_error(response, service)

    def runtime_status(self) -> dict[str, object]:
        return {
            "launcher_managed": os.getenv("ZW_VOICE_LAUNCHER_MANAGED") == "1",
            "services": {
                "voxcpm2": self._probe(self.voxcpm_url, "/health"),
                "fast_tts": self._probe(self.fast_tts_url, "/health"),
                "gpt_sovits": self._probe(self.gpt_sovits_url, "/openapi.json"),
                "indextts2": self._probe(self.indextts_url, "/health"),
            },
        }

    def _open_pcm_wav_stream(
        self,
        url: str,
        payload: dict[str, object],
        service: str,
    ) -> PcmAudioStream:
        for attempt in range(2):
            client = httpx.Client(timeout=httpx.Timeout(900, connect=5), trust_env=False)
            response: httpx.Response | None = None
            try:
                response = client.send(client.build_request("POST", url, json=payload), stream=True)
                if response.status_code in {502, 503, 504} and attempt == 0:
                    response.close()
                    client.close()
                    print(f"[MODEL] {service} 返回 HTTP {response.status_code}，1 秒后重试流式请求", flush=True)
                    time.sleep(1)
                    continue
                self._raise_for_model_error(response, service)
                source_chunks = response.iter_bytes()
                buffered = bytearray()
                parsed: tuple[int, int, int, int] | None = None
                while parsed is None:
                    try:
                        buffered.extend(next(source_chunks))
                    except StopIteration as error:
                        raise RuntimeError(f"{service} 未返回 WAV 流头") from error
                    parsed = self._parse_pcm_wav_header(buffered, service)
                header_size, channels, sample_width, sample_rate = parsed
                initial_audio = bytes(buffered[header_size:])

                def audio_chunks() -> Iterator[bytes]:
                    if initial_audio:
                        yield initial_audio
                    yield from source_chunks

                def close() -> None:
                    response.close()
                    client.close()

                return PcmAudioStream(
                    sample_rate=sample_rate,
                    channels=channels,
                    sample_width=sample_width,
                    chunks=audio_chunks(),
                    close=close,
                )
            except Exception:
                if response is not None:
                    response.close()
                client.close()
                raise
        raise RuntimeError(f"{service} 流式请求未完成")

    @staticmethod
    def _parse_pcm_wav_header(data: bytes | bytearray, service: str) -> tuple[int, int, int, int] | None:
        if len(data) < 12:
            return None
        if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            raise RuntimeError(f"{service} 未返回有效 WAV 流")
        position = 12
        audio_format: tuple[int, int, int] | None = None
        while position + 8 <= len(data):
            chunk_id = bytes(data[position : position + 4])
            chunk_size = int.from_bytes(data[position + 4 : position + 8], "little")
            chunk_start = position + 8
            chunk_end = chunk_start + chunk_size
            if chunk_end > len(data):
                return None
            if chunk_id == b"fmt ":
                if chunk_size < 16:
                    raise RuntimeError(f"{service} WAV 流格式块无效")
                format_code, channels, sample_rate, _, _, bits_per_sample = struct.unpack_from(
                    "<HHIIHH",
                    data,
                    chunk_start,
                )
                if format_code != 1 or bits_per_sample != 16 or channels not in {1, 2}:
                    raise RuntimeError(f"{service} 流式音频必须是单/双声道 PCM16")
                audio_format = (channels, bits_per_sample // 8, sample_rate)
            elif chunk_id == b"data":
                if audio_format is None:
                    raise RuntimeError(f"{service} WAV 流缺少格式信息")
                return chunk_start, *audio_format
            position = chunk_end + (chunk_size % 2)
        return None

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
EmotionEventHandler = Callable[[str, str, str, JobStatus, str | None, str | None], None]
QualityStabilityHandler = Callable[[str, str | None, str | None, bytes], RvcApplyResult]
FastRouteHandler = Callable[[str, str | None, str | None, bytes], RvcApplyResult]


class JobService:
    def __init__(
        self,
        workspace_root: Path,
        gateway: ModelGateway,
        reference_event_handler: ReferenceEventHandler | None = None,
        emotion_event_handler: EmotionEventHandler | None = None,
        quality_stability_handler: QualityStabilityHandler | None = None,
        fast_route_handler: FastRouteHandler | None = None,
        gpu_lock: threading.RLock | None = None,
        loudness_processor: LoudnessProcessor | None = None,
        loudness_policy_provider: Callable[[], ProgramLoudnessPolicy] | None = None,
        runtime_logger: logging.Logger | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.gateway = gateway
        self.reference_event_handler = reference_event_handler
        self.emotion_event_handler = emotion_event_handler
        self.quality_stability_handler = quality_stability_handler
        self.fast_route_handler = fast_route_handler
        self.gpu_lock = gpu_lock
        self.runtime_logger = runtime_logger or logging.getLogger("zw_voice_factory")
        self.loudness_processor = loudness_processor or LoudnessProcessor(self.workspace_root, self.runtime_logger)
        self.loudness_policy_provider = loudness_policy_provider or ProgramLoudnessPolicy
        self.audio_root = self.workspace_root / "outputs" / "audio" / "jobs"
        self.base_audio_root = self.workspace_root / "outputs" / "audio" / "base"
        self.rvc_audio_root = self.workspace_root / "outputs" / "audio" / "rvc"
        self.raw_audio_root = self.workspace_root / "outputs" / "audio" / "raw"
        self.merged_audio_root = self.workspace_root / "outputs" / "audio" / "merged"
        self.state_root = self.workspace_root / "outputs" / "jobs"
        self.audio_root.mkdir(parents=True, exist_ok=True)
        self.base_audio_root.mkdir(parents=True, exist_ok=True)
        self.rvc_audio_root.mkdir(parents=True, exist_ok=True)
        self.raw_audio_root.mkdir(parents=True, exist_ok=True)
        self.merged_audio_root.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, JobRecord] = {}
        self._requests: dict[str, tuple[JobRequest, Path | None]] = {}
        self._futures: dict[str, Future[None]] = {}
        self._lock = threading.Lock()
        # Both workers share one GPU. Serial dispatch prevents inference-time OOMs.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="zw-audio-job")
        self._load_persisted_jobs()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def submit(self, request: JobRequest) -> JobRecord:
        reference_path = self._validate_request(request)
        record = self._register_job(request, reference_path, streaming=False, keep_request=True)
        job_id = record.job_id
        with self._lock:
            future = self._executor.submit(self._run, job_id)
            self._futures[job_id] = future
        future.add_done_callback(lambda _: self._forget_future(job_id))
        return record

    def stream_quality(self, request: JobRequest) -> QualityStreamSession:
        if request.kind != "quality_render":
            raise JobProblem(422, "流式端点仅接受质量渲染任务")
        if request.quality_model not in {"gpt_sovits_v3", "gpt_sovits_v4"}:
            raise JobProblem(409, "新分段音频流仅用于 GPT-SoVITS V3/V4")
        reference_path = self._validate_request(request)
        assert reference_path is not None
        record = self._register_job(request, reference_path, streaming=True, keep_request=False)
        frame_queue: queue.Queue[bytes | object] = queue.Queue(maxsize=16)
        stop_event = threading.Event()
        with self._lock:
            future = self._executor.submit(
                self._produce_quality_stream,
                record.job_id,
                request,
                reference_path,
                frame_queue,
                stop_event,
            )
            self._futures[record.job_id] = future
        future.add_done_callback(
            lambda completed: self._finish_quality_stream(
                record.job_id,
                frame_queue,
                stop_event,
                completed,
            )
        )
        return QualityStreamSession(
            job_id=record.job_id,
            frames=self._consume_quality_stream(record.job_id, frame_queue, stop_event),
        )

    def _validate_request(self, request: JobRequest) -> Path | None:
        if request.kind == "voxcpm_reference" and bool(request.project_id) != bool(request.reference_id):
            raise JobProblem(422, "项目参考任务必须同时提供 project_id 和 reference_id")
        if request.kind == "emotion_variant" and (not request.project_id or not request.variant_id):
            raise JobProblem(422, "情绪派生任务必须同时提供 project_id 和 variant_id")
        reference_path: Path | None = None
        if request.kind == "quality_render":
            if not request.reference_audio_url:
                raise JobProblem(422, "质量渲染需要已确认的参考音频")
            if request.quality_model in {"gpt_sovits_v3", "gpt_sovits_v4"} and not (
                request.reference_text and request.reference_text.strip()
            ):
                raise JobProblem(422, "GPT-SoVITS V3/V4 需要与参考音频一致的参考文本")
            reference_path = self._resolve_reference(request.reference_audio_url)
        if request.kind == "fast_render" and not request.project_id:
            raise JobProblem(422, "极速渲染任务必须提供 project_id")
        return reference_path

    def _register_job(
        self,
        request: JobRequest,
        reference_path: Path | None,
        *,
        streaming: bool,
        keep_request: bool,
    ) -> JobRecord:
        now = datetime.now(timezone.utc)
        job_id = uuid.uuid4().hex[:12]
        loudness_policy = self.loudness_policy_provider().model_copy(deep=True)
        record = JobRecord(
            job_id=job_id,
            kind=request.kind,
            status="queued",
            progress=0,
            message="已进入渲染队列",
            project_id=request.project_id,
            reference_id=request.reference_id,
            variant_id=request.variant_id,
            character_id=request.character_id,
            segment_id=request.segment_id,
            reference_audio_url=request.reference_audio_url if request.kind == "quality_render" else None,
            quality_model=request.quality_model if request.kind == "quality_render" else None,
            render_options=request.render_options if request.kind == "quality_render" else None,
            fast_voice_id=request.fast_voice_id if request.kind == "fast_render" else None,
            fast_speed=request.fast_speed if request.kind == "fast_render" else None,
            fast_rvc_enabled=request.fast_rvc_enabled if request.kind == "fast_render" else None,
            streaming=streaming,
            loudness_policy=loudness_policy,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job_id] = record
            if keep_request:
                self._requests[job_id] = (request, reference_path)
            self._persist(record)
        try:
            self._notify_project_asset(request, job_id, "queued")
        except Exception as exc:
            with self._lock:
                self._jobs.pop(job_id, None)
                self._requests.pop(job_id, None)
                (self.state_root / f"{job_id}.json").unlink(missing_ok=True)
            raise JobProblem(getattr(exc, "status_code", 409), str(exc)) from exc
        self._log(record)
        return record.model_copy(deep=True)

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise JobProblem(404, "任务不存在或服务已重启")
            return record.model_copy(deep=True)

    def list(self, limit: int, project_id: str | None = None, kind: JobKind | None = None) -> list[JobRecord]:
        with self._lock:
            records = self._jobs.values()
            if project_id is not None:
                records = (record for record in records if record.project_id == project_id)
            if kind is not None:
                records = (record for record in records if record.kind == kind)
            records = sorted(records, key=lambda item: item.created_at, reverse=True)[:limit]
            return [record.model_copy(deep=True) for record in records]

    def cancel_jobs(self, project_id: str, kind: Literal["quality_render", "fast_render"]) -> CancelledJobs:
        now = datetime.now(timezone.utc)
        futures: list[tuple[str, Future[None] | None]] = []
        cancelled: list[JobRecord] = []
        with self._lock:
            for job_id, record in self._jobs.items():
                if record.project_id != project_id or record.kind != kind or record.status not in {"queued", "running"}:
                    continue
                updated = record.model_copy(
                    update={
                        "status": "cancelled",
                        "message": "已取消旧的极速渲染队列" if kind == "fast_render" else "已取消旧的质量渲染队列",
                        "error": None,
                        "updated_at": now,
                    }
                )
                self._jobs[job_id] = updated
                self._persist(updated)
                futures.append((job_id, self._futures.get(job_id)))
                cancelled.append(updated.model_copy(deep=True))

        for job_id, future in futures:
            if future is not None and future.cancel():
                with self._lock:
                    self._requests.pop(job_id, None)
                    self._futures.pop(job_id, None)
        for record in cancelled:
            self._log(record)
        return CancelledJobs(
            project_id=project_id,
            cancelled_count=len(cancelled),
            cancelled_jobs=cancelled,
        )

    def cancel_quality(self, project_id: str) -> CancelledJobs:
        return self.cancel_jobs(project_id, "quality_render")

    def delete_cache(
        self,
        project_id: str,
        job_ids: list[str],
        kind: Literal["quality_render", "fast_render"],
    ) -> DeletedQualityCache:
        unique_job_ids = list(dict.fromkeys(job_ids))
        with self._lock:
            records = [self._jobs.get(job_id) for job_id in unique_job_ids]
            if any(record is None for record in records):
                raise JobProblem(404, "缓存列表中存在已丢失的任务")
            quality_records = [record for record in records if record is not None]
            if any(record.project_id != project_id for record in quality_records):
                raise JobProblem(409, "不能删除其他项目的音频缓存")
            if any(record.kind != kind for record in quality_records):
                raise JobProblem(409, "只能删除同一路线的音频缓存")
            if any(record.status in {"queued", "running"} for record in quality_records):
                raise JobProblem(409, "任务仍在生成，不能删除缓存")

            deleted_bytes = 0
            deleted: list[JobRecord] = []
            now = datetime.now(timezone.utc)
            for record in quality_records:
                output = self.audio_root / f"{record.job_id}.wav"
                base_output = self.base_audio_root / f"{record.job_id}.wav"
                rvc_output = self.rvc_audio_root / f"{record.job_id}.wav"
                raw_output = self.raw_audio_root / f"{record.job_id}.wav"
                for cached_path in (output, base_output, rvc_output, raw_output):
                    if cached_path.is_file():
                        deleted_bytes += cached_path.stat().st_size
                        cached_path.unlink()
                updated = record.model_copy(
                    update={
                        "output_url": None,
                        "base_output_url": None,
                        "rvc_output_url": None,
                        "raw_output_url": None,
                        "message": "生成缓存已删除",
                        "updated_at": now,
                    }
                )
                self._jobs[record.job_id] = updated
                self._persist(updated)
                deleted.append(updated.model_copy(deep=True))

        for record in deleted:
            self._log(record)
        return DeletedQualityCache(
            project_id=project_id,
            deleted_count=len(deleted),
            deleted_bytes=deleted_bytes,
            deleted_jobs=deleted,
        )

    def delete_quality_cache(self, project_id: str, job_ids: list[str]) -> DeletedQualityCache:
        return self.delete_cache(project_id, job_ids, "quality_render")

    def reprocess_loudness(self, project_id: str, job_ids: list[str]) -> list[JobRecord]:
        unique_job_ids = list(dict.fromkeys(job_ids))
        with self._lock:
            records = [self._jobs.get(job_id) for job_id in unique_job_ids]
        if any(record is None for record in records):
            raise JobProblem(404, "响度处理列表中存在已丢失的任务")
        completed = [record for record in records if record is not None]
        if any(record.project_id != project_id for record in completed):
            raise JobProblem(409, "不能处理其他项目的音频缓存")
        if any(record.kind not in {"quality_render", "fast_render"} for record in completed):
            raise JobProblem(409, "响度处理仅支持质量路线或极速路线缓存")
        if any(record.status != "complete" or not record.output_url for record in completed):
            raise JobProblem(409, "只能重新处理已完成的音频缓存")

        policy = self.loudness_policy_provider()
        updated_records: list[JobRecord] = []
        for record in completed:
            output = self.audio_root / f"{record.job_id}.wav"
            raw_output = self.raw_audio_root / f"{record.job_id}.wav"
            if not output.is_file():
                raise JobProblem(404, f"音频缓存不存在：{record.job_id}")
            if not raw_output.is_file():
                shutil.copyfile(output, raw_output)
            try:
                audio, metrics = self.loudness_processor.process_segment_bytes(raw_output.read_bytes(), policy)
            except LoudnessProcessingError as error:
                audio = raw_output.read_bytes()
                metrics = LoudnessMetrics(processor="ffmpeg_loudnorm", status="failed", detail=str(error))
            temporary = output.with_suffix(".wav.tmp")
            temporary.write_bytes(audio)
            temporary.replace(output)
            updated = self._update(
                record.job_id,
                status="complete",
                progress=100,
                message="节目响度已重新统一",
                loudness_policy=policy.model_copy(deep=True),
                loudness_metrics=metrics,
                raw_output_url=f"/media/outputs/audio/raw/{record.job_id}.wav",
            )
            self.loudness_processor.record(metrics, record.project_id)
            updated_records.append(updated)
        return updated_records

    def reprocess_quality_rvc(self, project_id: str, job_ids: list[str]) -> list[JobRecord]:
        if self.quality_stability_handler is None:
            raise JobProblem(409, "RVC 质量稳定层不可用")
        unique_job_ids = list(dict.fromkeys(job_ids))
        with self._lock:
            records = [self._jobs.get(job_id) for job_id in unique_job_ids]
        if any(record is None for record in records):
            raise JobProblem(404, "RVC 处理列表中存在已丢失的任务")
        completed = [record for record in records if record is not None]
        if any(record.project_id != project_id for record in completed):
            raise JobProblem(409, "不能处理其他项目的音频缓存")
        if any(record.kind != "quality_render" for record in completed):
            raise JobProblem(409, "RVC 质量稳定层只能处理质量路线缓存")
        if any(record.status != "complete" for record in completed):
            raise JobProblem(409, "只能重新处理已完成的质量音频缓存")

        policy = self.loudness_policy_provider()
        updated_records: list[JobRecord] = []
        for record in completed:
            base_output = self.base_audio_root / f"{record.job_id}.wav"
            if not base_output.is_file():
                raise JobProblem(404, f"基础渲染缓存不存在：{record.job_id}")
            base_audio = base_output.read_bytes()
            result = self.quality_stability_handler(
                project_id,
                record.character_id,
                record.reference_id,
                base_audio,
            )
            selected_audio = result.audio
            rvc_output = self.rvc_audio_root / f"{record.job_id}.wav"
            if result.status == "applied":
                temporary_rvc = rvc_output.with_suffix(".wav.tmp")
                temporary_rvc.write_bytes(selected_audio)
                temporary_rvc.replace(rvc_output)
                rvc_output_url: str | None = f"/media/outputs/audio/rvc/{record.job_id}.wav"
            else:
                rvc_output.unlink(missing_ok=True)
                rvc_output_url = None

            raw_output = self.raw_audio_root / f"{record.job_id}.wav"
            temporary_raw = raw_output.with_suffix(".wav.tmp")
            temporary_raw.write_bytes(selected_audio)
            temporary_raw.replace(raw_output)
            try:
                audio, metrics = self.loudness_processor.process_segment_bytes(selected_audio, policy)
            except LoudnessProcessingError as error:
                audio = selected_audio
                metrics = LoudnessMetrics(processor="ffmpeg_loudnorm", status="failed", detail=str(error))
            output = self.audio_root / f"{record.job_id}.wav"
            temporary_output = output.with_suffix(".wav.tmp")
            temporary_output.write_bytes(audio)
            temporary_output.replace(output)
            message = {
                "applied": "已从基础渲染重新应用 RVC 并统一响度",
                "fallback": f"RVC 处理失败，已保留基础渲染：{result.error or '未知错误'}",
                "bypassed": "当前角色未启用已批准的 RVC，已保留基础渲染",
                "not_requested": "质量稳定层总开关未启用，已保留基础渲染",
            }[result.status]
            updated = self._update(
                record.job_id,
                status="complete",
                progress=100,
                message=message,
                output_url=f"/media/outputs/audio/jobs/{record.job_id}.wav",
                base_output_url=f"/media/outputs/audio/base/{record.job_id}.wav",
                rvc_output_url=rvc_output_url,
                rvc_status=result.status,
                rvc_model_id=result.model_id,
                rvc_profile_fingerprint=result.profile_fingerprint,
                rvc_error=result.error,
                raw_output_url=f"/media/outputs/audio/raw/{record.job_id}.wav",
                loudness_policy=policy.model_copy(deep=True),
                loudness_metrics=metrics,
            )
            self.loudness_processor.record(metrics, project_id)
            updated_records.append(updated)
        return updated_records

    def merge(self, project_id: str, job_ids: list[str], kind: Literal["quality_render", "fast_render"]) -> MergedAudio:
        with self._lock:
            records = [self._jobs.get(job_id) for job_id in job_ids]
        if any(record is None for record in records):
            raise JobProblem(404, "合并列表中存在已丢失的任务")
        completed = [record for record in records if record is not None]
        if any(record.project_id != project_id for record in completed):
            raise JobProblem(409, "不能合并其他项目的音频任务")
        if any(record.kind != kind or record.status != "complete" or not record.output_url for record in completed):
            raise JobProblem(409, "只能合并同一路线已完成的渲染任务")

        output = self.merged_audio_root / f"{uuid.uuid4().hex[:12]}.wav"
        temporary = output.with_suffix(".wav.tmp")
        audio_format: tuple[int, int, str, str] | None = None
        target_rate = 0
        total_frames = 0
        frames: list[tuple[bytes, int]] = []
        for record in completed:
            assert record.output_url is not None
            source_path = self._resolve_reference(record.output_url)
            with wave.open(str(source_path), "rb") as source:
                current_format = (
                    source.getnchannels(),
                    source.getsampwidth(),
                    source.getcomptype(),
                    source.getcompname(),
                )
                if audio_format is None:
                    audio_format = current_format
                elif current_format != audio_format:
                    raise JobProblem(409, "待合并音频的位深、声道或编码不一致，请使用同一路线重新生成")
                source_rate = source.getframerate()
                target_rate = max(target_rate, source_rate)
                frames.append((source.readframes(source.getnframes()), source_rate))
        assert audio_format is not None
        if any(source_rate != target_rate for _, source_rate in frames) and (
            audio_format[1] != 2 or audio_format[2] != "NONE"
        ):
            raise JobProblem(409, "待合并音频采样率不一致，且当前编码无法自动重采样")
        with wave.open(str(temporary), "wb") as merged:
            merged.setnchannels(audio_format[0])
            merged.setsampwidth(audio_format[1])
            merged.setframerate(target_rate)
            merged.setcomptype(audio_format[2], audio_format[3])
            for chunk, source_rate in frames:
                chunk = _resample_pcm16(chunk, audio_format[0], source_rate, target_rate)
                total_frames += len(chunk) // (audio_format[0] * audio_format[1])
                merged.writeframesraw(chunk)
        loudness_metrics = self._normalize_merged_audio(temporary, output)
        return MergedAudio(
            project_id=project_id,
            output_url=f"/media/outputs/audio/merged/{output.name}",
            segment_count=len(completed),
            duration_seconds=round(total_frames / target_rate, 3),
            loudness_metrics=loudness_metrics,
        )

    def _load_persisted_jobs(self) -> None:
        for path in self.state_root.glob("*.json"):
            try:
                record = JobRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if record.status in {"queued", "running"}:
                record = record.model_copy(
                    update={
                        "status": "failed",
                        "message": "服务重启前任务未完成",
                        "error": "任务已中断，请重新生成",
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
                self._persist(record)
            self._jobs[record.job_id] = record
            self.loudness_processor.record(record.loudness_metrics, record.project_id)

    @staticmethod
    def _stream_frame(frame_type: int, payload: bytes) -> bytes:
        return struct.pack(">BI", frame_type, len(payload)) + payload

    @staticmethod
    def _put_stream_item(
        frame_queue: queue.Queue[bytes | object],
        item: bytes | object,
        stop_event: threading.Event,
    ) -> bool:
        while not stop_event.is_set():
            try:
                frame_queue.put(item, timeout=0.2)
                return True
            except queue.Full:
                continue
        return False

    def _produce_quality_stream(
        self,
        job_id: str,
        request: JobRequest,
        reference_path: Path,
        frame_queue: queue.Queue[bytes | object],
        stop_event: threading.Event,
    ) -> None:
        pcm_stream: PcmAudioStream | None = None
        output = self.audio_root / f"{job_id}.wav"
        base_output = self.base_audio_root / f"{job_id}.wav"
        raw_output = self.raw_audio_root / f"{job_id}.wav"
        temporary = self.audio_root / f".{job_id}.{uuid.uuid4().hex}.wav.tmp"
        try:
            service_name = quality_model_spec(request.quality_model).label
            if self._update(job_id, status="running", progress=15, message=f"正在连接 {service_name} 音频流").status == "cancelled":
                return
            with self.gpu_lock if self.gpu_lock is not None else nullcontext():
                pcm_stream = self.gateway.generate_quality_stream(
                    request.text,
                    reference_path,
                    request.quality_model,
                    request.emotion_description,
                    request.render_options,
                    request.reference_text,
                )
                metadata = json.dumps(
                    {
                        "job_id": job_id,
                        "sample_rate": pcm_stream.sample_rate,
                        "channels": pcm_stream.channels,
                        "sample_width": pcm_stream.sample_width,
                        "format": "pcm_s16le",
                        "rolling_gain_db": self._rolling_gain_db(job_id),
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                if not self._put_stream_item(
                    frame_queue,
                    self._stream_frame(STREAM_FRAME_METADATA, metadata),
                    stop_event,
                ):
                    return
                self._update(job_id, status="running", progress=35, message="首个音频分段已就绪，正在流式朗读")
                block_align = pcm_stream.channels * pcm_stream.sample_width
                pending = bytearray()
                chunk_count = 0
                with wave.open(str(temporary), "wb") as cached:
                    cached.setnchannels(pcm_stream.channels)
                    cached.setsampwidth(pcm_stream.sample_width)
                    cached.setframerate(pcm_stream.sample_rate)
                    for chunk in pcm_stream.chunks:
                        if stop_event.is_set() or self._is_cancelled(job_id):
                            return
                        if not chunk:
                            continue
                        pending.extend(chunk)
                        aligned_size = len(pending) - len(pending) % block_align
                        if aligned_size == 0:
                            continue
                        audio_chunk = bytes(pending[:aligned_size])
                        del pending[:aligned_size]
                        cached.writeframesraw(audio_chunk)
                        if not self._put_stream_item(
                            frame_queue,
                            self._stream_frame(STREAM_FRAME_AUDIO, audio_chunk),
                            stop_event,
                        ):
                            return
                        chunk_count += 1
                        if chunk_count == 1:
                            self._update(job_id, status="running", progress=55, message="正在播放首段并生成后续音频")
                if pending:
                    raise RuntimeError(f"{service_name} 返回了未对齐的 PCM 音频数据")

            if stop_event.is_set() or self._is_cancelled(job_id):
                return
            if self._update(job_id, status="running", progress=92, message="正在统一节目响度").status == "cancelled":
                return
            raw_audio = temporary.read_bytes()
            base_output.write_bytes(raw_audio)
            raw_output.write_bytes(raw_audio)
            audio, loudness_metrics = self._normalize_generated_audio(job_id, raw_audio)
            temporary.write_bytes(audio)
            temporary.replace(output)
            output_url = f"/media/outputs/audio/jobs/{job_id}.wav"
            raw_output_url = f"/media/outputs/audio/raw/{job_id}.wav"
            updated = self._update(
                job_id,
                status="complete",
                progress=100,
                message="流式音频已播放并写入缓存",
                output_url=output_url,
                base_output_url=f"/media/outputs/audio/base/{job_id}.wav",
                rvc_status="not_requested",
                raw_output_url=raw_output_url,
                loudness_metrics=loudness_metrics,
            )
            self.loudness_processor.record(loudness_metrics, updated.project_id)
            if updated.status == "cancelled":
                output.unlink(missing_ok=True)
                base_output.unlink(missing_ok=True)
                raw_output.unlink(missing_ok=True)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            output.unlink(missing_ok=True)
            base_output.unlink(missing_ok=True)
            raw_output.unlink(missing_ok=True)
            if self._is_cancelled(job_id) or stop_event.is_set():
                return
            self._update(job_id, status="failed", message="流式音频生成失败", error=str(exc))
            error_payload = json.dumps({"message": str(exc)}, ensure_ascii=False).encode("utf-8")
            self._put_stream_item(
                frame_queue,
                self._stream_frame(STREAM_FRAME_ERROR, error_payload),
                stop_event,
            )
        finally:
            if pcm_stream is not None:
                pcm_stream.close()
            temporary.unlink(missing_ok=True)
            with self._lock:
                final_record = self._jobs.get(job_id)
            if stop_event.is_set() and final_record is not None and final_record.status in {"queued", "running"}:
                self._update(job_id, status="cancelled", message="客户端已停止流式播放", error=None)
            self._put_stream_item(frame_queue, STREAM_END, stop_event)

    def _rolling_gain_db(self, job_id: str) -> float:
        with self._lock:
            record = self._jobs[job_id]
        return self.loudness_processor.rolling_gain_db(record.loudness_policy, record.project_id)

    def _normalize_generated_audio(
        self,
        job_id: str,
        audio: bytes,
    ) -> tuple[bytes, LoudnessMetrics | None]:
        with self._lock:
            record = self._jobs[job_id]
        if record.kind not in {"quality_render", "fast_render"}:
            return audio, None
        try:
            return self.loudness_processor.process_segment_bytes(audio, record.loudness_policy)
        except LoudnessProcessingError as error:
            self.runtime_logger.warning("[LOUDNESS %s] processing skipped: %s", job_id, error)
            return audio, LoudnessMetrics(
                processor="ffmpeg_loudnorm",
                status="failed",
                detail=str(error),
            )

    def _normalize_merged_audio(self, source: Path, target: Path) -> LoudnessMetrics:
        try:
            metrics = self.loudness_processor.normalize_program_file(
                source,
                target,
                self.loudness_policy_provider(),
            )
        except LoudnessProcessingError as error:
            self.runtime_logger.warning("[LOUDNESS MERGE] processing skipped: %s", error)
            source.replace(target)
            return LoudnessMetrics(
                processor="ffmpeg_loudnorm",
                status="failed",
                final_program_pass=True,
                detail=str(error),
            )
        source.unlink(missing_ok=True)
        return metrics

    def _consume_quality_stream(
        self,
        job_id: str,
        frame_queue: queue.Queue[bytes | object],
        stop_event: threading.Event,
    ) -> Iterator[bytes]:
        try:
            while True:
                item = frame_queue.get()
                if item is STREAM_END:
                    return
                assert isinstance(item, bytes)
                yield item
        finally:
            stop_event.set()
            with self._lock:
                record = self._jobs.get(job_id)
            if record is not None and record.status in {"queued", "running"}:
                self._update(job_id, status="cancelled", message="客户端已断开流式播放", error=None)

    def _finish_quality_stream(
        self,
        job_id: str,
        frame_queue: queue.Queue[bytes | object],
        stop_event: threading.Event,
        future: Future[None],
    ) -> None:
        if future.cancelled() and not stop_event.is_set():
            try:
                frame_queue.put_nowait(STREAM_END)
            except queue.Full:
                pass
        self._forget_future(job_id)

    def _run(self, job_id: str) -> None:
        try:
            with self._lock:
                record = self._jobs[job_id]
                queued_request = self._requests.get(job_id)
            if record.status == "cancelled" or queued_request is None:
                return
            request, reference_path = queued_request
            if request.kind in {"voxcpm_reference", "emotion_variant"}:
                service_name = "VoxCPM2"
            elif request.kind == "fast_render":
                service_name = "轻量 TTS"
            else:
                service_name = quality_model_spec(request.quality_model).label
            if self._update(job_id, status="running", progress=15, message=f"正在提交到 {service_name}").status == "cancelled":
                return
            self._notify_project_asset(request, job_id, "running")
            rvc_result = RvcApplyResult(audio=b"", status="not_requested")
            with self.gpu_lock if self.gpu_lock is not None else nullcontext():
                if request.kind in {"voxcpm_reference", "emotion_variant"}:
                    audio = self.gateway.generate_voxcpm(request.text, request.voice_prompt)
                elif request.kind == "fast_render":
                    audio = self.gateway.generate_fast(request.text, request.fast_voice_id, request.fast_speed)
                else:
                    assert reference_path is not None
                    audio = self.gateway.generate_quality(
                        request.text,
                        reference_path,
                        request.quality_model,
                        request.emotion_description,
                        request.render_options,
                        request.reference_text,
                    )
            base_audio = audio
            if request.kind == "fast_render" and request.fast_rvc_enabled and self.fast_route_handler is not None and request.project_id:
                rvc_result = self.fast_route_handler(
                    request.project_id,
                    request.character_id,
                    request.reference_id,
                    base_audio,
                )
                audio = rvc_result.audio
            elif request.kind == "quality_render" and self.quality_stability_handler is not None and request.project_id:
                rvc_result = self.quality_stability_handler(
                    request.project_id,
                    request.character_id,
                    request.reference_id,
                    base_audio,
                )
                audio = rvc_result.audio
            if self._update(job_id, status="running", progress=90, message="模型已返回，正在写入 WAV").status == "cancelled":
                return
            output = self.audio_root / f"{job_id}.wav"
            base_output = self.base_audio_root / f"{job_id}.wav"
            rvc_output = self.rvc_audio_root / f"{job_id}.wav"
            raw_output = self.raw_audio_root / f"{job_id}.wav"
            temporary = output.with_suffix(".wav.tmp")
            temporary.write_bytes(audio)
            raw_output.write_bytes(audio)
            base_output_url: str | None = None
            rvc_output_url: str | None = None
            if request.kind in {"quality_render", "fast_render"}:
                base_output.write_bytes(base_audio)
                base_output_url = f"/media/outputs/audio/base/{job_id}.wav"
                if rvc_result.status == "applied":
                    rvc_output.write_bytes(audio)
                    rvc_output_url = f"/media/outputs/audio/rvc/{job_id}.wav"
            if self._is_cancelled(job_id):
                temporary.unlink(missing_ok=True)
                raw_output.unlink(missing_ok=True)
                base_output.unlink(missing_ok=True)
                rvc_output.unlink(missing_ok=True)
                return
            if self._update(job_id, status="running", progress=92, message="正在统一节目响度").status == "cancelled":
                temporary.unlink(missing_ok=True)
                raw_output.unlink(missing_ok=True)
                base_output.unlink(missing_ok=True)
                rvc_output.unlink(missing_ok=True)
                return
            audio, loudness_metrics = self._normalize_generated_audio(job_id, audio)
            temporary.write_bytes(audio)
            temporary.replace(output)
            output_url = f"/media/outputs/audio/jobs/{job_id}.wav"
            raw_output_url = f"/media/outputs/audio/raw/{job_id}.wav"
            self._notify_project_asset(request, job_id, "complete", output_url=output_url)
            updated = self._update(
                job_id,
                status="complete",
                progress=100,
                message=(
                    f"音频生成完成，RVC 已回退基础渲染：{rvc_result.error}"
                    if rvc_result.status == "fallback"
                    else "音频生成完成"
                ),
                output_url=output_url,
                base_output_url=base_output_url,
                rvc_output_url=rvc_output_url,
                rvc_status=rvc_result.status,
                rvc_model_id=rvc_result.model_id,
                rvc_profile_fingerprint=rvc_result.profile_fingerprint,
                rvc_error=rvc_result.error,
                raw_output_url=raw_output_url,
                loudness_metrics=loudness_metrics,
            )
            self.loudness_processor.record(loudness_metrics, updated.project_id)
            if updated.status == "cancelled":
                output.unlink(missing_ok=True)
                raw_output.unlink(missing_ok=True)
                base_output.unlink(missing_ok=True)
                rvc_output.unlink(missing_ok=True)
        except Exception as exc:  # The failure is surfaced through the job record and console.
            if self._is_cancelled(job_id):
                return
            try:
                request, _ = self._requests[job_id]
                self._notify_project_asset(request, job_id, "failed", error=str(exc))
            except Exception:
                pass
            self._update(job_id, status="failed", message="音频生成失败", error=str(exc))
        finally:
            with self._lock:
                self._requests.pop(job_id, None)

    def _update(self, job_id: str, **changes: object) -> JobRecord:
        with self._lock:
            record = self._jobs[job_id]
            if record.status == "cancelled" and changes.get("status") != "cancelled":
                return record.model_copy(deep=True)
            updated = record.model_copy(update={**changes, "updated_at": datetime.now(timezone.utc)})
            self._jobs[job_id] = updated
            self._persist(updated)
        self._log(updated)
        return updated.model_copy(deep=True)

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs.get(job_id)
            return record is not None and record.status == "cancelled"

    def _forget_future(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)

    def _resolve_reference(self, media_url: str) -> Path:
        parsed = urlsplit(media_url)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise JobProblem(400, "参考音频必须来自当前工作区媒体库")
        decoded = unquote(parsed.path)
        prefix_map = {
            "/media/voice-samples/": self.workspace_root / "assets" / "voice_samples",
            "/media/outputs/audio/": self.workspace_root / "outputs" / "audio",
            "/media/outputs/projects/": self.workspace_root / "outputs" / "projects",
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

    def _notify_project_asset(
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
        if (
            request.kind == "emotion_variant"
            and request.project_id
            and request.variant_id
            and self.emotion_event_handler
        ):
            self.emotion_event_handler(
                request.project_id,
                request.variant_id,
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

    def _log(self, record: JobRecord) -> None:
        suffix = f" | {record.error}" if record.error else ""
        self.runtime_logger.info("[JOB %s] %3d%% %s%s", record.job_id, record.progress, record.message, suffix)


def create_jobs_router(service: JobService) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["audio-jobs"])

    @router.post("/jobs", response_model=JobRecord, status_code=status.HTTP_202_ACCEPTED)
    def submit_job(request: JobRequest) -> JobRecord:
        try:
            return service.submit(request)
        except JobProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post("/jobs/quality-stream")
    def stream_quality_job(request: JobRequest) -> StreamingResponse:
        try:
            session = service.stream_quality(request)
        except JobProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return StreamingResponse(
            session.frames,
            media_type="application/x-zw-pcm-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Zw-Stream-Job-Id": session.job_id,
                "X-Zw-Stream-Protocol": "1",
            },
        )

    @router.get("/jobs", response_model=list[JobRecord])
    def list_jobs(
        limit: int = Query(default=50, ge=1, le=5_000),
        project_id: str | None = Query(default=None, max_length=120),
        kind: JobKind | None = Query(default=None),
    ) -> list[JobRecord]:
        return service.list(limit, project_id, kind)

    @router.get("/jobs/{job_id}", response_model=JobRecord)
    def get_job(job_id: str) -> JobRecord:
        try:
            return service.get(job_id)
        except JobProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.get("/runtime")
    def runtime() -> dict[str, object]:
        return service.gateway.runtime_status()

    @router.post("/projects/{project_id}/quality/cancel", response_model=CancelledJobs)
    def cancel_quality_jobs(project_id: str) -> CancelledJobs:
        return service.cancel_quality(project_id)

    @router.post(
        "/projects/{project_id}/quality/cache/delete",
        response_model=DeletedQualityCache,
    )
    def delete_quality_cache(
        project_id: str,
        request: QualityCacheDeleteRequest,
    ) -> DeletedQualityCache:
        try:
            return service.delete_quality_cache(project_id, request.job_ids)
        except JobProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post(
        "/projects/{project_id}/quality/loudness/reprocess",
        response_model=list[JobRecord],
    )
    def reprocess_quality_loudness(
        project_id: str,
        request: LoudnessReprocessRequest,
    ) -> list[JobRecord]:
        try:
            return service.reprocess_loudness(project_id, request.job_ids)
        except JobProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post(
        "/projects/{project_id}/quality/rvc/reprocess",
        response_model=list[JobRecord],
    )
    def reprocess_quality_rvc(
        project_id: str,
        request: RvcReprocessRequest,
    ) -> list[JobRecord]:
        try:
            return service.reprocess_quality_rvc(project_id, request.job_ids)
        except JobProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post("/projects/{project_id}/quality/merge", response_model=MergedAudio)
    def merge_quality_audio(project_id: str, request: MergeRequest) -> MergedAudio:
        try:
            return service.merge(project_id, request.job_ids, "quality_render")
        except JobProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post("/projects/{project_id}/fast/cancel", response_model=CancelledJobs)
    def cancel_fast_jobs(project_id: str) -> CancelledJobs:
        return service.cancel_jobs(project_id, "fast_render")

    @router.post(
        "/projects/{project_id}/fast/cache/delete",
        response_model=DeletedQualityCache,
    )
    def delete_fast_cache(
        project_id: str,
        request: QualityCacheDeleteRequest,
    ) -> DeletedQualityCache:
        try:
            return service.delete_cache(project_id, request.job_ids, "fast_render")
        except JobProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.post("/projects/{project_id}/fast/merge", response_model=MergedAudio)
    def merge_fast_audio(project_id: str, request: MergeRequest) -> MergedAudio:
        try:
            return service.merge(project_id, request.job_ids, "fast_render")
        except JobProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return router
