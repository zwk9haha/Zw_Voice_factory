from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .domain import (
    InferenceTemplate,
    ProductionStage,
    ProductionStageStatus,
    QualityRouteConfiguration,
    StabilityPolicy,
)


class WorkspaceProject(BaseModel):
    id: str
    name: str
    route: Literal["fast", "quality"]


class WorkspaceSummary(BaseModel):
    characters: int = 0
    accepted_references: int = 0
    segments: int = 0
    generated: int = 0


class WorkspacePayload(BaseModel):
    project: WorkspaceProject
    summary: WorkspaceSummary
    workflow: list[ProductionStage]
    available_templates: list[InferenceTemplate]
    active_template: InferenceTemplate


def build_workspace(
    project: WorkspaceProject | None = None,
    inference_mode: Literal["cloud", "hybrid", "local"] = "local",
    preparation_status: Literal["empty", "imported", "analyzed", "characters_ready", "director_ready"] = "empty",
    summary: WorkspaceSummary | None = None,
) -> WorkspacePayload:
    quality_route = QualityRouteConfiguration(
        reference_backend="voxcpm2",
        render_backend="gpt_sovits",
        stability_backend="rvc",
        stability_policy=StabilityPolicy.benchmark_gated,
    )
    templates = [
        InferenceTemplate(
            template_id="cloud_full_analysis",
            display_name="完全由云端 API 推理",
            inference_mode="cloud",
            analysis_profile="precision_first",
            segmentation_profile="audiobook",
            reference_text_profile="phoneme_coverage",
            quality_route=quality_route,
        ),
        InferenceTemplate(
            template_id="hybrid_local_cloud",
            display_name="本地初步筛选后交由云端 API 推理",
            inference_mode="hybrid",
            analysis_profile="balanced",
            segmentation_profile="dialogue_dense",
            reference_text_profile="emotion_contrast",
            quality_route=quality_route,
        ),
        InferenceTemplate(
            template_id="local_private_analysis",
            display_name="本地模型推理",
            inference_mode="local",
            analysis_profile="character_recall",
            segmentation_profile="long_form",
            reference_text_profile="phoneme_coverage",
            quality_route=quality_route,
        ),
    ]
    stage_definitions = [
        ("template", "推理模板"),
        ("source", "小说导入"),
        ("casting", "角色审核"),
        ("references", "标准参考"),
        ("emotions", "情绪派生"),
        ("director", "导演脚本"),
        ("quality_render", "质量渲染"),
    ]
    current_stage = {
        "empty": "source",
        "imported": "source",
        "analyzed": "casting",
        "characters_ready": "references",
        "director_ready": "quality_render",
    }[preparation_status]
    current_index = next(index for index, (stage_id, _) in enumerate(stage_definitions) if stage_id == current_stage)
    workflow = [
        ProductionStage(
            stage_id=stage_id,
            label=label,
            status=(
                ProductionStageStatus.complete
                if index < current_index
                else ProductionStageStatus.current
                if index == current_index
                else ProductionStageStatus.blocked
            ),
        )
        for index, (stage_id, label) in enumerate(stage_definitions)
    ]

    return WorkspacePayload(
        project=project or WorkspaceProject(id="", name="未创建项目", route="quality"),
        summary=summary or WorkspaceSummary(),
        workflow=workflow,
        available_templates=templates,
        active_template=next(template for template in templates if template.inference_mode == inference_mode),
    )
