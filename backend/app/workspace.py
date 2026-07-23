from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .domain import (
    CharacterTier,
    InferenceTemplate,
    ProductionStage,
    ProductionStageStatus,
    QualityRouteConfiguration,
    ReviewStatus,
    StabilityPolicy,
)


class WorkspaceProject(BaseModel):
    id: str
    name: str
    route: Literal["fast", "quality"]


class WorkspaceSummary(BaseModel):
    characters: int
    accepted_references: int
    segments: int
    generated: int


class WorkspaceCharacter(BaseModel):
    character_id: str
    display_name: str
    tier: CharacterTier
    importance: float
    voice_prompt: str
    reference_status: ReviewStatus
    reference_backend: Literal["voxcpm2", "indextts2", "uploaded"]
    preview_audio_url: str | None = None
    emotion_variants: list[str]
    color: Literal["teal", "violet", "gold"]


class WorkspaceSegment(BaseModel):
    segment_id: str
    character_id: str
    speaker: str
    emotion: str
    text: str


class WorkspacePayload(BaseModel):
    project: WorkspaceProject
    summary: WorkspaceSummary
    workflow: list[ProductionStage]
    available_templates: list[InferenceTemplate]
    active_template: InferenceTemplate
    characters: list[WorkspaceCharacter]
    segments: list[WorkspaceSegment]


def build_demo_workspace() -> WorkspacePayload:
    quality_route = QualityRouteConfiguration(
        reference_backend="voxcpm2",
        render_backend="gpt_sovits",
        stability_backend="rvc",
        stability_policy=StabilityPolicy.benchmark_gated,
    )
    templates = [
        InferenceTemplate(
            template_id="quality_character_consistency",
            display_name="质量 · 角色一致性",
            analysis_profile="balanced",
            segmentation_profile="audiobook",
            reference_text_profile="phoneme_coverage",
            quality_route=quality_route,
        ),
        InferenceTemplate(
            template_id="quality_dialogue_dense",
            display_name="质量 · 对话密集",
            analysis_profile="character_recall",
            segmentation_profile="dialogue_dense",
            reference_text_profile="emotion_contrast",
            quality_route=quality_route,
        ),
        InferenceTemplate(
            template_id="quality_long_form",
            display_name="质量 · 长篇稳态",
            analysis_profile="precision_first",
            segmentation_profile="long_form",
            reference_text_profile="phoneme_coverage",
            quality_route=quality_route,
        ),
    ]
    workflow = [
        ProductionStage(stage_id="template", label="推理模板", status=ProductionStageStatus.complete),
        ProductionStage(stage_id="source", label="小说导入", status=ProductionStageStatus.complete),
        ProductionStage(stage_id="casting", label="角色审核", status=ProductionStageStatus.complete),
        ProductionStage(stage_id="references", label="标准参考", status=ProductionStageStatus.complete),
        ProductionStage(stage_id="emotions", label="情绪派生", status=ProductionStageStatus.complete),
        ProductionStage(stage_id="director", label="导演脚本", status=ProductionStageStatus.complete),
        ProductionStage(stage_id="quality_render", label="质量渲染", status=ProductionStageStatus.current),
    ]

    return WorkspacePayload(
        project=WorkspaceProject(id="doupo_demo", name="斗破苍穹", route="quality"),
        summary=WorkspaceSummary(characters=3, accepted_references=2, segments=500, generated=3),
        workflow=workflow,
        available_templates=templates,
        active_template=templates[0],
        characters=[
            WorkspaceCharacter(
                character_id="narrator",
                display_name="旁白",
                tier=CharacterTier.core,
                importance=1.0,
                voice_prompt="成熟、清晰、稳定的男声，叙述克制，具有空间感",
                reference_status=ReviewStatus.accepted,
                reference_backend="voxcpm2",
                preview_audio_url="/media/voice-samples/curated/elder/male/voice_ref_34d05b99307a9c.wav",
                emotion_variants=["自然", "庄重", "紧张"],
                color="teal",
            ),
            WorkspaceCharacter(
                character_id="xiao_yan",
                display_name="萧炎",
                tier=CharacterTier.core,
                importance=0.94,
                voice_prompt="青年男声，清亮但有韧劲，克制中保留爆发力",
                reference_status=ReviewStatus.accepted,
                reference_backend="voxcpm2",
                preview_audio_url="/media/voice-samples/curated/young_adult/male/voice_ref_955e37aef1a1b7.wav",
                emotion_variants=["自然", "愤怒", "悲伤"],
                color="violet",
            ),
            WorkspaceCharacter(
                character_id="test_officer",
                display_name="测验员",
                tier=CharacterTier.supporting,
                importance=0.42,
                voice_prompt="中年男声，冷淡、清晰、公式化",
                reference_status=ReviewStatus.pending,
                reference_backend="voxcpm2",
                preview_audio_url="/media/voice-samples/curated/elder/male/voice_ref_0f3bba4cd9d384.wav",
                emotion_variants=[],
                color="gold",
            ),
        ],
        segments=[
            WorkspaceSegment(
                segment_id="s001",
                character_id="narrator",
                speaker="旁白",
                emotion="紧张",
                text="望着测验魔石碑上闪亮的五个大字，少年面无表情。",
            ),
            WorkspaceSegment(
                segment_id="s002",
                character_id="test_officer",
                speaker="测验员",
                emotion="冷淡",
                text="萧炎，斗之力，三段。级别，低级。",
            ),
            WorkspaceSegment(
                segment_id="s003",
                character_id="xiao_yan",
                speaker="萧炎",
                emotion="克制",
                text="三十年河东，三十年河西，莫欺少年穷。",
            ),
        ],
    )
