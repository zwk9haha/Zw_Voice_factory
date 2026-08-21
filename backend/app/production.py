from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from .loudness import ProgramLoudnessPolicy


QualityModelId = Literal[
    "gpt_sovits_v1",
    "gpt_sovits_v2",
    "gpt_sovits_v2_pro",
    "gpt_sovits_v2_pro_plus",
    "gpt_sovits_v3",
    "gpt_sovits_v4",
    "indextts2",
]


@dataclass(frozen=True)
class QualityModelSpec:
    model_id: QualityModelId
    label: str
    effect: str
    renderer: Literal["gpt_sovits", "indextts2"]
    required_paths: tuple[str, ...]
    gpt_weights: str | None = None
    sovits_weights: str | None = None


QUALITY_MODEL_SPECS: tuple[QualityModelSpec, ...] = (
    QualityModelSpec(
        model_id="gpt_sovits_v1",
        label="GPT-SoVITS V1",
        effect="音色贴近、风格直接，适合旧版角色资产",
        renderer="gpt_sovits",
        required_paths=(
            "models/tts_tools/gpt_sovits/GPT_SoVITS/pretrained_models/s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt",
            "models/tts_tools/gpt_sovits/GPT_SoVITS/pretrained_models/s2G488k.pth",
        ),
        gpt_weights="GPT_SoVITS/pretrained_models/s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt",
        sovits_weights="GPT_SoVITS/pretrained_models/s2G488k.pth",
    ),
    QualityModelSpec(
        model_id="gpt_sovits_v2",
        label="GPT-SoVITS V2",
        effect="稳定均衡、长篇一致性好，当前默认",
        renderer="gpt_sovits",
        required_paths=(
            "models/tts_tools/gpt_sovits/GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
            "models/tts_tools/gpt_sovits/GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth",
        ),
        gpt_weights="GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
        sovits_weights="GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth",
    ),
    QualityModelSpec(
        model_id="gpt_sovits_v2_pro",
        label="GPT-SoVITS V2 Pro",
        effect="V2 音色稳定性增强，细节和咬字更精细",
        renderer="gpt_sovits",
        required_paths=(
            "models/tts_tools/gpt_sovits/GPT_SoVITS/pretrained_models/s1v3.ckpt",
            "models/tts_tools/gpt_sovits/GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth",
        ),
        gpt_weights="GPT_SoVITS/pretrained_models/s1v3.ckpt",
        sovits_weights="GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth",
    ),
    QualityModelSpec(
        model_id="gpt_sovits_v2_pro_plus",
        label="GPT-SoVITS V2 Pro Plus",
        effect="V2 Pro 的高质量版本，提升自然度与长句稳定性",
        renderer="gpt_sovits",
        required_paths=(
            "models/tts_tools/gpt_sovits/GPT_SoVITS/pretrained_models/s1v3.ckpt",
            "models/tts_tools/gpt_sovits/GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth",
        ),
        gpt_weights="GPT_SoVITS/pretrained_models/s1v3.ckpt",
        sovits_weights="GPT_SoVITS/pretrained_models/v2Pro/s2Gv2ProPlus.pth",
    ),
    QualityModelSpec(
        model_id="gpt_sovits_v3",
        label="GPT-SoVITS V3",
        effect="细节和气息更丰富，适合高表现力对白",
        renderer="gpt_sovits",
        required_paths=(
            "models/tts_tools/gpt_sovits/GPT_SoVITS/pretrained_models/s1v3.ckpt",
            "models/tts_tools/gpt_sovits/GPT_SoVITS/pretrained_models/s2Gv3.pth",
        ),
        gpt_weights="GPT_SoVITS/pretrained_models/s1v3.ckpt",
        sovits_weights="GPT_SoVITS/pretrained_models/s2Gv3.pth",
    ),
    QualityModelSpec(
        model_id="gpt_sovits_v4",
        label="GPT-SoVITS V4",
        effect="高采样率、自然度更高，适合最终质量输出",
        renderer="gpt_sovits",
        required_paths=(
            "models/tts_tools/gpt_sovits/GPT_SoVITS/pretrained_models/s1v3.ckpt",
            "models/tts_tools/gpt_sovits/GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth",
        ),
        gpt_weights="GPT_SoVITS/pretrained_models/s1v3.ckpt",
        sovits_weights="GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth",
    ),
    QualityModelSpec(
        model_id="indextts2",
        label="IndexTTS2",
        effect="自然语言情绪控制最强，按需加载但生成较慢",
        renderer="indextts2",
        required_paths=(
            "models/tts_tools/indextts2/.venv/Scripts/python.exe",
            "models/tts_tools/indextts2/indextts/infer_v2.py",
            "models/tts/IndexTeam--IndexTTS-2/config.yaml",
            "models/tts/IndexTeam--IndexTTS-2/gpt.pth",
        ),
    ),
)

QUALITY_MODEL_INDEX = {spec.model_id: spec for spec in QUALITY_MODEL_SPECS}


class QualityModelOption(BaseModel):
    model_id: QualityModelId
    label: str
    effect: str
    renderer: Literal["gpt_sovits", "indextts2"]
    available: bool
    unavailable_reason: str | None = None


class QualityRenderOptions(BaseModel):
    chunk_length: int = Field(default=120, ge=20, le=300)
    top_k: int = Field(default=30, ge=1, le=100)
    top_p: float = Field(default=0.8, ge=0.1, le=1)
    temperature: float = Field(default=0.8, ge=0.1, le=1.5)
    repetition_penalty: float = Field(default=1.35, ge=1, le=12)
    speed_factor: float = Field(default=1, ge=0.6, le=1.5)
    fragment_interval: float = Field(default=0.3, ge=0.05, le=1)
    batch_size: int = Field(default=1, ge=1, le=16)
    split_bucket: bool = True
    seed: int = Field(default=-1, ge=-1, le=2_147_483_647)
    emotion_strength: float = Field(default=0.75, ge=0, le=1)


class ProductionSettings(BaseModel):
    selected_quality_model: QualityModelId = "gpt_sovits_v2"
    render_options: QualityRenderOptions = Field(default_factory=QualityRenderOptions)
    loudness_policy: ProgramLoudnessPolicy = Field(default_factory=ProgramLoudnessPolicy)
    narrator_gender: Literal["male", "female"] = "male"
    auto_delete_played_cache: bool = False
    cache_keep_sentences: int = Field(default=20, ge=1, le=1_000)


class ProductionSettingsView(ProductionSettings):
    quality_models: list[QualityModelOption]


class ProductionSettingsUpdate(BaseModel):
    selected_quality_model: QualityModelId | None = None
    render_options: QualityRenderOptions | None = None
    loudness_policy: ProgramLoudnessPolicy | None = None
    narrator_gender: Literal["male", "female"] | None = None
    auto_delete_played_cache: bool | None = None
    cache_keep_sentences: int | None = Field(default=None, ge=1, le=1_000)

    @model_validator(mode="after")
    def require_change(self) -> "ProductionSettingsUpdate":
        if (
            self.selected_quality_model is None
            and self.render_options is None
            and self.loudness_policy is None
            and self.narrator_gender is None
            and self.auto_delete_played_cache is None
            and self.cache_keep_sentences is None
        ):
            raise ValueError("至少需要提交一项生产设置")
        return self


class ProductionProblem(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def quality_model_spec(model_id: QualityModelId) -> QualityModelSpec:
    return QUALITY_MODEL_INDEX[model_id]


class ProductionService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.settings_path = self.workspace_root / "outputs" / "settings" / "production.json"
        self._lock = threading.Lock()

    def get(self) -> ProductionSettingsView:
        with self._lock:
            settings = self._read()
            return ProductionSettingsView(
                selected_quality_model=settings.selected_quality_model,
                render_options=settings.render_options,
                loudness_policy=settings.loudness_policy,
                narrator_gender=settings.narrator_gender,
                auto_delete_played_cache=settings.auto_delete_played_cache,
                cache_keep_sentences=settings.cache_keep_sentences,
                quality_models=self._catalog(),
            )

    def update(self, request: ProductionSettingsUpdate) -> ProductionSettingsView:
        with self._lock:
            current = self._read()
            selected_quality_model = request.selected_quality_model or current.selected_quality_model
            if request.selected_quality_model is not None:
                option = next(item for item in self._catalog() if item.model_id == selected_quality_model)
                if not option.available:
                    raise ProductionProblem(409, option.unavailable_reason or "质量模型当前不可用")
            settings = ProductionSettings(
                selected_quality_model=selected_quality_model,
                render_options=request.render_options or current.render_options,
                loudness_policy=request.loudness_policy or current.loudness_policy,
                narrator_gender=request.narrator_gender or current.narrator_gender,
                auto_delete_played_cache=(
                    request.auto_delete_played_cache
                    if request.auto_delete_played_cache is not None
                    else current.auto_delete_played_cache
                ),
                cache_keep_sentences=request.cache_keep_sentences or current.cache_keep_sentences,
            )
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.settings_path)
            return ProductionSettingsView(
                selected_quality_model=settings.selected_quality_model,
                render_options=settings.render_options,
                loudness_policy=settings.loudness_policy,
                narrator_gender=settings.narrator_gender,
                auto_delete_played_cache=settings.auto_delete_played_cache,
                cache_keep_sentences=settings.cache_keep_sentences,
                quality_models=self._catalog(),
            )

    def _catalog(self) -> list[QualityModelOption]:
        items: list[QualityModelOption] = []
        for spec in QUALITY_MODEL_SPECS:
            missing = [path for path in spec.required_paths if not (self.workspace_root / path).is_file()]
            items.append(
                QualityModelOption(
                    model_id=spec.model_id,
                    label=spec.label,
                    effect=spec.effect,
                    renderer=spec.renderer,
                    available=not missing,
                    unavailable_reason="缺少本地模型文件" if missing else None,
                )
            )
        return items

    def loudness_policy(self) -> ProgramLoudnessPolicy:
        with self._lock:
            return self._read().loudness_policy.model_copy(deep=True)

    def _read(self) -> ProductionSettings:
        if not self.settings_path.is_file():
            return ProductionSettings()
        try:
            return ProductionSettings.model_validate_json(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ProductionSettings()


def create_production_router(service: ProductionService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/production/settings", response_model=ProductionSettingsView)
    def get_settings() -> ProductionSettingsView:
        return service.get()

    @router.patch("/api/production/settings", response_model=ProductionSettingsView)
    def update_settings(request: ProductionSettingsUpdate) -> ProductionSettingsView:
        try:
            return service.update(request)
        except ProductionProblem as problem:
            raise HTTPException(status_code=problem.status_code, detail=problem.detail) from problem

    return router
