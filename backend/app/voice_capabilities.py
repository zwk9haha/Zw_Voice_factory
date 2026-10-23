from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from .jobs import JobProblem, JobRequest, JobService
from .production import QualityModelId
from .voice_catalog import VoiceCatalogAsset, VoiceCatalogProblem, VoiceCatalogService


class VoiceCapabilityNode(BaseModel):
    node_id: str
    display_name: str
    description: str
    stage: Literal["ingest", "understand", "voice", "render", "postprocess"]
    input_types: list[str] = Field(default_factory=list)
    output_types: list[str] = Field(default_factory=list)
    supports_streaming: bool = False
    supports_background: bool = True


class VoiceCapabilityDescriptor(BaseModel):
    capability_id: str = "voice.factory"
    version: str = "1.0"
    display_name: str = "Zw 音色工厂"
    description: str = "将文本转换为可审核、可复用、可流式播放的音频资产。"
    accepted_content_types: list[str]
    invocation_modes: list[str]
    output_types: list[str]
    nodes: list[VoiceCapabilityNode]


class VoiceCapabilityAsset(BaseModel):
    asset_id: str
    display_name: str
    gender: str
    age_range: str
    tags: list[str]
    voice_prompt: str
    reference_text: str
    version_id: str | None = None
    audio_url: str | None = None
    duration_seconds: float | None = None
    ready: bool


class VoiceSynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    catalog_asset_id: str | None = Field(default=None, max_length=120)
    catalog_version_id: str | None = Field(default=None, max_length=120)
    reference_audio_url: str | None = Field(default=None, max_length=2_000)
    reference_text: str | None = Field(default=None, max_length=1_000)
    voice_prompt: str | None = Field(default=None, max_length=1_000)
    quality_model: QualityModelId = "gpt_sovits_v2"
    emotion_description: str | None = Field(default=None, max_length=1_000)


class VoiceSynthesisResponse(BaseModel):
    capability_id: str = "voice.factory"
    job_id: str
    status: str
    message: str
    catalog_asset_id: str | None = None
    catalog_version_id: str | None = None


VOICE_NODES = [
    VoiceCapabilityNode(
        node_id="voice.text_ingest",
        display_name="文本接入",
        description="接收短文本、小说、剧本、字幕或普通文档，并统一为文本片段。",
        stage="ingest",
        input_types=["text/plain", "text/novel", "text/script", "text/subtitle"],
        output_types=["voice.text_segments"],
    ),
    VoiceCapabilityNode(
        node_id="voice.character_analysis",
        display_name="角色与声线分析",
        description="识别说话人、角色特征、声线画像和片段级表达要求。",
        stage="understand",
        input_types=["voice.text_segments"],
        output_types=["voice.character_profiles", "voice.speaker_assignments"],
    ),
    VoiceCapabilityNode(
        node_id="voice.reference_text",
        display_name="标准参考文本",
        description="依据角色证据和稳定声线画像生成中性、可审核的参考文本。",
        stage="voice",
        input_types=["voice.character_profiles", "voice.character_evidence"],
        output_types=["voice.reference_text"],
    ),
    VoiceCapabilityNode(
        node_id="voice.reference_generation",
        display_name="参考音频生成",
        description="根据结构化声线画像生成可审核、可复用的参考音频版本。",
        stage="voice",
        input_types=["voice.character_profiles", "voice.catalog_asset"],
        output_types=["voice.reference_audio", "voice.catalog_version"],
    ),
    VoiceCapabilityNode(
        node_id="voice.match",
        display_name="声音库匹配",
        description="按声线画像、模型兼容性和场景标签排序全局声音库候选，不自动替换项目绑定。",
        stage="voice",
        input_types=["voice.character_profiles", "voice.voice_profile"],
        output_types=["voice.catalog_matches"],
    ),
    VoiceCapabilityNode(
        node_id="voice.director_annotation",
        display_name="导演标注",
        description="为句子保留说话人、情绪、语气、停连和渲染提示。",
        stage="understand",
        input_types=["voice.text_segments", "voice.character_profiles"],
        output_types=["voice.director_document"],
    ),
    VoiceCapabilityNode(
        node_id="voice.quality_render",
        display_name="质量渲染",
        description="使用已确认的参考音频生成角色或旁白音频。",
        stage="render",
        input_types=["voice.director_document", "voice.reference_audio"],
        output_types=["audio/wav", "voice.render_job"],
        supports_streaming=True,
    ),
    VoiceCapabilityNode(
        node_id="voice.fast_render",
        display_name="极速渲染",
        description="使用轻量 TTS 与可选 RVC 身份层快速生成试听或草稿。",
        stage="render",
        input_types=["voice.director_document", "voice.voice_assignment"],
        output_types=["audio/wav", "voice.render_job"],
        supports_streaming=True,
    ),
    VoiceCapabilityNode(
        node_id="voice.rvc_stability",
        display_name="RVC 稳定层",
        description="对基础音频应用已审核的角色身份模型，并保留回退到基础音频的能力。",
        stage="render",
        input_types=["audio/wav", "voice.rvc_asset"],
        output_types=["audio/wav", "voice.rvc_status"],
    ),
    VoiceCapabilityNode(
        node_id="voice.program_loudness",
        display_name="节目响度处理",
        description="对片段和节目级音频执行统一响度、峰值和导出检查。",
        stage="postprocess",
        input_types=["audio/wav"],
        output_types=["audio/wav", "voice.loudness_metrics"],
    ),
    VoiceCapabilityNode(
        node_id="voice.merge",
        display_name="节目合并导出",
        description="按质量或极速路线合并已完成片段，并输出节目级 WAV 与响度指标。",
        stage="postprocess",
        input_types=["voice.render_job"],
        output_types=["audio/wav", "voice.loudness_metrics"],
    ),
]


class VoiceCapabilityService:
    def __init__(self, catalog: VoiceCatalogService, jobs: JobService) -> None:
        self.catalog = catalog
        self.jobs = jobs

    @staticmethod
    def descriptor() -> VoiceCapabilityDescriptor:
        return VoiceCapabilityDescriptor(
            accepted_content_types=["short_text", "novel", "script", "subtitle", "document"],
            invocation_modes=["catalog_reuse", "reference_audio", "quality_render_job"],
            output_types=["audio/wav", "voice.render_job", "voice.catalog_version"],
            nodes=[node.model_copy(deep=True) for node in VOICE_NODES],
        )

    def assets(self) -> list[VoiceCapabilityAsset]:
        payload = self.catalog.payload()
        result: list[VoiceCapabilityAsset] = []
        for asset in payload.assets:
            version = next((item for item in asset.versions if item.version_id == asset.active_version_id), None)
            result.append(
                VoiceCapabilityAsset(
                    asset_id=asset.asset_id,
                    display_name=asset.display_name,
                    gender=asset.gender,
                    age_range=asset.age_range,
                    tags=[*asset.timbre_tags, *asset.delivery_tags],
                    voice_prompt=asset.voice_prompt,
                    reference_text=asset.reference_text,
                    version_id=version.version_id if version else None,
                    audio_url=version.audio_url if version else None,
                    duration_seconds=version.duration_seconds if version else None,
                    ready=bool(version and version.status == "complete" and version.audio_url),
                )
            )
        return result

    def synthesize(self, request: VoiceSynthesisRequest) -> VoiceSynthesisResponse:
        asset: VoiceCatalogAsset | None = None
        version_id = request.catalog_version_id
        reference_audio_url = request.reference_audio_url
        reference_text = request.reference_text
        voice_prompt = request.voice_prompt or "自然、清晰、稳定"
        if request.catalog_version_id and not request.catalog_asset_id:
            raise VoiceCapabilityProblem(422, "catalog_version_id 必须与 catalog_asset_id 一起提供")
        if request.catalog_asset_id:
            try:
                reference = self.catalog.resolve_reference(request.catalog_asset_id, version_id)
                asset = self.catalog.get_asset(request.catalog_asset_id)
            except VoiceCatalogProblem as exc:
                raise VoiceCapabilityProblem(exc.status_code, exc.detail) from exc
            version_id = reference.version_id
            reference_audio_url = reference.audio_url
            reference_text = reference_text or reference.reference_text
            voice_prompt = request.voice_prompt or reference.voice_prompt
        if not reference_audio_url:
            raise VoiceCapabilityProblem(422, "需要提供 reference_audio_url 或已完成的 catalog 音色版本")
        try:
            job = self.jobs.submit(
                JobRequest(
                    kind="quality_render",
                    text=request.text,
                    reference_audio_url=reference_audio_url,
                    reference_text=reference_text,
                    voice_prompt=voice_prompt,
                    quality_model=request.quality_model,
                    emotion_description=request.emotion_description,
                )
            )
        except JobProblem as exc:
            raise VoiceCapabilityProblem(exc.status_code, exc.detail) from exc
        return VoiceSynthesisResponse(
            job_id=job.job_id,
            status=job.status,
            message="已提交 Voice Adapter 质量渲染任务",
            catalog_asset_id=asset.asset_id if asset else None,
            catalog_version_id=version_id,
        )


class VoiceCapabilityProblem(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def create_voice_capability_router(service: VoiceCapabilityService) -> APIRouter:
    router = APIRouter(prefix="/api/capabilities/voice", tags=["voice-capability"])

    @router.get("", response_model=VoiceCapabilityDescriptor)
    def describe() -> VoiceCapabilityDescriptor:
        return service.descriptor()

    @router.get("/nodes", response_model=list[VoiceCapabilityNode])
    def nodes() -> list[VoiceCapabilityNode]:
        return service.descriptor().nodes

    @router.get("/assets", response_model=list[VoiceCapabilityAsset])
    def assets() -> list[VoiceCapabilityAsset]:
        return service.assets()

    @router.post("/synthesize", response_model=VoiceSynthesisResponse, status_code=status.HTTP_202_ACCEPTED)
    def synthesize(request: VoiceSynthesisRequest) -> VoiceSynthesisResponse:
        try:
            return service.synthesize(request)
        except VoiceCapabilityProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return router
