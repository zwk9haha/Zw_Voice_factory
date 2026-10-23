from __future__ import annotations

import time
import uuid
import wave
import os
from pathlib import Path
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .jobs import ModelGateway
from .production import QualityModelId, QualityRenderOptions


class GenieCompareRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    project_id: str | None = Field(default=None, max_length=120)
    reference_audio_url: str | None = Field(default=None, max_length=2_000)
    reference_audio_path: Path | None = None
    reference_text: str = Field(min_length=1, max_length=1_000)
    voice_id: str = Field(default="v2ProPlus/feibi", min_length=1, max_length=120)
    model_dir: Path | None = None
    character_name: str = Field(default="ab-test", min_length=1, max_length=120)
    language: str = Field(default="zh", max_length=40)
    gsv_model: QualityModelId = "gpt_sovits_v2_pro_plus"
    render_options: QualityRenderOptions = Field(default_factory=QualityRenderOptions)


class GenieVoiceOption(BaseModel):
    voice_id: str
    label: str


class GenieCompareResult(BaseModel):
    experiment_id: str
    text: str
    gsv_audio_url: str
    genie_audio_url: str
    gsv_elapsed_ms: int
    genie_elapsed_ms: int
    gsv_duration_seconds: float
    genie_duration_seconds: float
    genie_worker_url: str


class GenieExperimentService:
    def __init__(self, workspace_root: Path, gateway: ModelGateway) -> None:
        self.workspace_root = workspace_root.resolve()
        self.gateway = gateway
        self.worker_url = os.getenv("ZW_VOICE_GENIE_URL", "http://127.0.0.1:9884").rstrip("/")
        self.output_root = self.workspace_root / "outputs" / "runtime" / "genie-ab-test"

    def compare(self, request: GenieCompareRequest) -> GenieCompareResult:
        reference_audio = self._resolve_reference_audio(request)
        model_dir = self._resolve_model_dir(request)
        experiment_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.output_root.mkdir(parents=True, exist_ok=True)
        gsv_path = self.output_root / f"{experiment_id}-gsv.wav"
        genie_path = self.output_root / f"{experiment_id}-genie.wav"
        started = time.perf_counter()
        gsv_audio = self.gateway.generate_quality(
            request.text,
            reference_audio,
            request.gsv_model,
            render_options=request.render_options,
            reference_text=request.reference_text,
        )
        gsv_elapsed = round((time.perf_counter() - started) * 1000)
        gsv_path.write_bytes(gsv_audio)
        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{self.worker_url}/generate",
                json={
                    "text": request.text,
                    "character_name": request.character_name,
                    "model_dir": str(model_dir),
                    "reference_audio_path": str(reference_audio),
                    "reference_text": request.reference_text,
                    "language": request.language,
                    "split_sentence": True,
                },
                timeout=httpx.Timeout(900, connect=5),
                trust_env=False,
            )
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail=response.text[:2_000])
            genie_path.write_bytes(response.content)
        except httpx.HTTPError as error:
            raise HTTPException(status_code=502, detail=f"Genie-TTS Worker 不可用：{error}") from error
        genie_elapsed = round((time.perf_counter() - started) * 1000)
        return GenieCompareResult(
            experiment_id=experiment_id,
            text=request.text,
            gsv_audio_url=f"/media/outputs/experiments/{gsv_path.name}",
            genie_audio_url=f"/media/outputs/experiments/{genie_path.name}",
            gsv_elapsed_ms=gsv_elapsed,
            genie_elapsed_ms=genie_elapsed,
            gsv_duration_seconds=self._duration(gsv_path),
            genie_duration_seconds=self._duration(genie_path),
            genie_worker_url=self.worker_url,
        )

    def voices(self) -> list[GenieVoiceOption]:
        try:
            response = httpx.get(f"{self.worker_url}/voices", timeout=5, trust_env=False)
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail=response.text[:2_000])
            return [GenieVoiceOption.model_validate(item) for item in response.json()]
        except HTTPException:
            raise
        except httpx.HTTPError as error:
            raise HTTPException(status_code=503, detail=f"Genie-TTS Worker 不可用：{error}") from error

    def _resolve_reference_audio(self, request: GenieCompareRequest) -> Path:
        if request.reference_audio_url:
            parsed = urlsplit(request.reference_audio_url)
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                raise HTTPException(status_code=400, detail="参考音频必须来自当前工作区媒体库")
            decoded = unquote(parsed.path)
            prefix_map = {
                "/media/voice-samples/": self.workspace_root / "assets" / "voice_samples",
                "/media/outputs/audio/": self.workspace_root / "outputs" / "audio",
                "/media/outputs/projects/": self.workspace_root / "outputs" / "projects",
            }
            for prefix, root in prefix_map.items():
                if not decoded.startswith(prefix):
                    continue
                relative = PurePosixPath(decoded[len(prefix):])
                if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
                    break
                candidate = root.joinpath(*relative.parts).resolve()
                if candidate.is_relative_to(root.resolve()) and candidate.is_file():
                    return candidate
                break
            raise HTTPException(status_code=400, detail="参考音频路径无效或文件不存在")

        if request.reference_audio_path is None:
            raise HTTPException(status_code=422, detail="必须提供参考音频")
        reference_audio = request.reference_audio_path.resolve()
        try:
            reference_audio.relative_to(self.workspace_root)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="参考音频必须位于项目目录内") from error
        if not reference_audio.is_file():
            raise HTTPException(status_code=404, detail="参考音频不存在")
        return reference_audio

    def _resolve_model_dir(self, request: GenieCompareRequest) -> Path:
        if request.model_dir is not None:
            model_dir = request.model_dir.resolve()
        else:
            voice_parts = request.voice_id.split("/")
            if len(voice_parts) != 2 or any(not part or part in {".", ".."} for part in voice_parts):
                raise HTTPException(status_code=400, detail="Genie 音色标识无效")
            model_dir = (
                self.workspace_root
                / "models" / "tts_tools" / "genie-tts" / "portable" / "Genie-TTS GUI"
                / "CharacterModels" / voice_parts[0] / voice_parts[1] / "tts_models"
            ).resolve()
        try:
            model_dir.relative_to(self.workspace_root)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Genie 模型目录必须位于项目目录内") from error
        if not model_dir.is_dir():
            raise HTTPException(status_code=404, detail="Genie 模型目录不存在")
        return model_dir

    @staticmethod
    def _duration(path: Path) -> float:
        with wave.open(str(path), "rb") as source:
            return round(source.getnframes() / max(1, source.getframerate()), 3)


def create_genie_router(service: GenieExperimentService) -> APIRouter:
    router = APIRouter(prefix="/api/experiments/genie", tags=["genie-experiments"])

    @router.post("/compare", response_model=GenieCompareResult)
    def compare(request: GenieCompareRequest) -> GenieCompareResult:
        return service.compare(request)

    @router.get("/voices", response_model=list[GenieVoiceOption])
    def voices() -> list[GenieVoiceOption]:
        return service.voices()

    return router
