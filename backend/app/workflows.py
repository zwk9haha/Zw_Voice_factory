from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from .jobs import JobProblem, JobRequest, JobService
from .fast_route import FAST_VOICES
from .platform import PlatformProblem, PlatformService, analyze_text_segments
from .preparation import CharacterEvidencePack, PreparationService
from .production import QUALITY_MODEL_SPECS
from .voice_analysis import RuleBasedVoiceAnalyzer
from .voice_catalog import VoiceCatalogProblem, VoiceCatalogService


WorkflowNodeStatus = Literal[
    "pending",
    "queued",
    "running",
    "awaiting_review",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "skipped",
]
WorkflowRunStatus = Literal["draft", "queued", "running", "awaiting_review", "paused", "completed", "failed", "cancelled"]
WorkflowReviewDecision = Literal["approved", "rejected"]
WorkflowEdgeKind = Literal["data", "branch"]
WorkflowNodeSkipReason = Literal["cache_hit", "dependency_failed", "branch_not_selected", "upstream_not_selected"]
WorkflowConditionLogic = Literal["all", "any"]
WorkflowConditionOperator = Literal[
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "greater_than",
    "greater_or_equal",
    "less_than",
    "less_or_equal",
    "is_empty",
    "is_not_empty",
    "is_true",
    "is_false",
]
WorkflowEventLevel = Literal["info", "warning", "error"]
WorkflowLifecycle = Literal["draft", "published", "archived"]
WorkflowValueType = Literal[
    "string",
    "number",
    "boolean",
    "json",
    "text",
    "text_segment",
    "text_segments",
    "voice_profile",
    "voice_matches",
    "voice_snapshot",
    "audio_asset",
    "audio_assets",
    "job_ids",
    "any",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _is_empty_workflow_value(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, (list, tuple, dict, set)) and not value)


def _workflow_truthy(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0", ""}:
            return False
    return bool(value)


def _workflow_value_at_path(value: Any, path: str) -> Any:
    normalized = path.strip()
    if normalized in {"", "$"}:
        return value
    if normalized.startswith("$."):
        normalized = normalized[2:]
    current = value
    for part in normalized.split("."):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
            continue
        if isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
            continue
        return None
    return current


def _evaluate_workflow_condition(value: Any, rule: WorkflowConditionRule) -> bool:
    operator = rule.operator
    expected = rule.compare_value
    if operator == "equals":
        return value == expected
    if operator == "not_equals":
        return value != expected
    if operator in {"contains", "not_contains"}:
        try:
            contains = expected in value
        except (TypeError, ValueError):
            contains = False
        return contains if operator == "contains" else not contains
    if operator in {"is_empty", "is_not_empty"}:
        empty = _is_empty_workflow_value(value)
        return empty if operator == "is_empty" else not empty
    if operator in {"is_true", "is_false"}:
        truthy = _workflow_truthy(value)
        return truthy if operator == "is_true" else not truthy
    try:
        left: Any = float(value)
        right: Any = float(expected)
    except (TypeError, ValueError):
        left = str(value)
        right = str(expected)
    if operator == "greater_than":
        return left > right
    if operator == "greater_or_equal":
        return left >= right
    if operator == "less_than":
        return left < right
    if operator == "less_or_equal":
        return left <= right
    raise ValueError(f"不支持的条件运算符：{operator}")


def _evaluate_workflow_condition_group(value: Any, config: WorkflowConditionConfig) -> tuple[bool, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    for rule in config.effective_rules():
        actual = _workflow_value_at_path(value, rule.path)
        matched = _evaluate_workflow_condition(actual, rule)
        results.append(
            {
                "path": rule.path,
                "operator": rule.operator,
                "compare_value": _jsonable(rule.compare_value),
                "actual_value": _jsonable(actual),
                "matched": matched,
            }
        )
    matched = all(item["matched"] for item in results) if config.logic == "all" else any(item["matched"] for item in results)
    return matched, results


def _workflow_job_ids(value: Any) -> list[str]:
    """Normalize job references passed between workflow nodes."""
    if isinstance(value, str):
        values: list[Any] = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        raise ValueError("音频后处理节点需要非空 job_ids 列表")
    result: list[str] = []
    for item in values:
        if isinstance(item, dict):
            item = item.get("job_id")
        if not isinstance(item, str) or not item.strip():
            raise ValueError("音频后处理节点的 job_ids 必须是非空任务 ID")
        result.append(item.strip())
    if not result:
        raise ValueError("音频后处理节点需要非空 job_ids 列表")
    return list(dict.fromkeys(result))


_WORKFLOW_FAST_VOICE_IDS = tuple(voice.voice_id for voice in FAST_VOICES)


def _workflow_fast_voice_for_speaker(
    speaker_name: str,
    speaker_kind: str,
    voice_map: dict[str, Any],
) -> str:
    configured = voice_map.get(speaker_name)
    if configured is not None:
        voice_id = str(configured).strip()
        if voice_id not in _WORKFLOW_FAST_VOICE_IDS:
            raise ValueError(f"说话人 {speaker_name} 的 fast_voice_id 无效：{voice_id}")
        return voice_id
    if speaker_kind == "narration" or speaker_name == "旁白":
        return "gunian"
    digest = hashlib.sha1(speaker_name.encode("utf-8")).digest()[0]
    return _WORKFLOW_FAST_VOICE_IDS[digest % len(_WORKFLOW_FAST_VOICE_IDS)]


def _workflow_voice_plan(raw_segments: Any, voice_map: Any = None, reference_map: Any = None) -> list[dict[str, Any]]:
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError("声线分配需要非空 segments 列表")
    voice_map = {} if voice_map is None else voice_map
    reference_map = {} if reference_map is None else reference_map
    if not isinstance(voice_map, dict) or not isinstance(reference_map, dict):
        raise ValueError("voice_map 和 reference_map 必须是对象")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_segments):
        if not isinstance(raw, dict) or not isinstance(raw.get("text"), str) or not raw["text"].strip():
            raise ValueError(f"segments[{index}] 缺少非空 text")
        speaker = raw.get("speaker") if isinstance(raw.get("speaker"), dict) else {}
        name = str(speaker.get("speaker_name") or raw.get("speaker_name") or "未指定说话人").strip()
        kind = str(speaker.get("speaker_kind") or raw.get("speaker_kind") or "unknown")
        reference = reference_map.get(name, {})
        if not isinstance(reference, dict):
            raise ValueError(f"角色 {name} 的参考音频配置必须是对象")
        result.append({
            **raw, **reference,
            "text": raw["text"].strip(), "index": raw.get("index", index),
            "segment_id": str(raw.get("segment_id") or f"segment-{index + 1:04d}"),
            "speaker_name": name, "speaker_kind": kind,
            "character_id": str(reference.get("character_id") or raw.get("character_id") or f"workflow-character-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:12]}"),
            "fast_voice_id": _workflow_fast_voice_for_speaker(name, kind, voice_map),
        })
    return result


class WorkflowNodeDefinition(BaseModel):
    node_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    node_type: str = Field(min_length=1, max_length=120)
    version: str = Field(default="1.0", min_length=1, max_length=32)
    title: str | None = Field(default=None, max_length=120)
    description: str = Field(default="", max_length=500)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 80.0, "y": 80.0})
    inputs: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=32)


class WorkflowPortDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    label: str = Field(default="", max_length=80)
    type: WorkflowValueType = "any"
    required: bool = False
    multiple: bool = False
    description: str = Field(default="", max_length=240)


class WorkflowEdgeDefinition(BaseModel):
    edge_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    source_node: str = Field(min_length=1, max_length=120)
    source_port: str = Field(min_length=1, max_length=80)
    target_node: str = Field(min_length=1, max_length=120)
    target_port: str = Field(min_length=1, max_length=80)
    kind: WorkflowEdgeKind = "data"

    @model_validator(mode="after")
    def validate_kind(self) -> "WorkflowEdgeDefinition":
        if self.kind == "branch" and self.target_port != "__route__":
            raise ValueError("分支连线的目标端口必须是 __route__")
        if self.kind == "data" and self.target_port == "__route__":
            raise ValueError("数据连线不能使用 __route__ 目标端口")
        return self


class WorkflowInputDefinition(WorkflowPortDefinition):
    default: Any = None
    widget: Literal["auto", "text", "textarea", "file", "select", "json"] = "auto"
    options: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_widget(self) -> "WorkflowInputDefinition":
        if self.widget == "file" and self.type not in {"string", "text"}:
            raise ValueError("TXT 文件输入必须使用文本类型")
        if self.widget == "select" and (self.type != "string" or not self.options):
            raise ValueError("选择输入需要字符串类型及非空选项")
        return self


class WorkflowOutputBinding(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    type: WorkflowValueType = "any"
    source_node: str = Field(min_length=1, max_length=120)
    source_port: str = Field(min_length=1, max_length=80)
    label: str = Field(default="", max_length=80)
    display: Literal["auto", "text", "json", "audio"] = "auto"


class WorkflowNodeExecutionPolicy(BaseModel):
    mode: Literal["sync", "job", "human", "control"] = "sync"
    resource_group: str = "cpu"
    retryable: bool = True
    idempotent: bool = True
    cacheable: bool = True
    requires_human_review: bool = False


class WorkflowConditionRule(BaseModel):
    path: str = Field(default="$", min_length=1, max_length=240)
    operator: WorkflowConditionOperator = "equals"
    compare_value: Any = None


class WorkflowConditionConfig(BaseModel):
    logic: WorkflowConditionLogic = "all"
    conditions: list[WorkflowConditionRule] = Field(default_factory=list, max_length=32)
    # Legacy single-condition fields remain readable for persisted v1 workflows.
    operator: WorkflowConditionOperator = "equals"
    compare_value: Any = None

    def effective_rules(self) -> list[WorkflowConditionRule]:
        if self.conditions:
            return self.conditions
        return [WorkflowConditionRule(operator=self.operator, compare_value=self.compare_value)]


class WorkflowNodeManifest(BaseModel):
    type: str
    version: str
    label: str
    category: str
    description: str
    inputs: list[WorkflowPortDefinition] = Field(default_factory=list)
    outputs: list[WorkflowPortDefinition] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    execution: WorkflowNodeExecutionPolicy = Field(default_factory=WorkflowNodeExecutionPolicy)


class WorkflowGraphValidation(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    unreachable_nodes: list[str] = Field(default_factory=list)


class WorkflowDefinition(BaseModel):
    workflow_id: str
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(default="1.0", min_length=1, max_length=32)
    description: str = Field(default="", max_length=1_000)
    nodes: list[WorkflowNodeDefinition] = Field(min_length=1, max_length=128)
    inputs: list[WorkflowInputDefinition] = Field(default_factory=list, max_length=64)
    edges: list[WorkflowEdgeDefinition] = Field(default_factory=list, max_length=512)
    outputs: list[WorkflowOutputBinding] = Field(default_factory=list, max_length=64)
    lifecycle: WorkflowLifecycle = "draft"
    published_at: datetime | None = None
    allow_pause: bool = True
    allow_retry: bool = True
    allow_recovery: bool = True
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDefinition":
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("工作流节点 ID 必须唯一")
        known = set(node_ids)
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("工作流连线 ID 必须唯一")
        input_names = [item.name for item in self.inputs]
        if len(input_names) != len(set(input_names)):
            raise ValueError("工作流输入名称必须唯一")
        output_names = [item.name for item in self.outputs]
        if len(output_names) != len(set(output_names)):
            raise ValueError("工作流输出名称必须唯一")
        for edge in self.edges:
            if edge.source_node not in known or edge.target_node not in known:
                raise ValueError(f"连线 {edge.edge_id} 引用了不存在的节点")
        for output in self.outputs:
            if output.source_node not in known:
                raise ValueError(f"工作流输出 {output.name} 引用了不存在的节点")
        for node in self.nodes:
            dependencies = set(node.depends_on)
            for value in node.inputs.values():
                dependencies.update(_referenced_nodes(value))
            missing = dependencies - known
            if missing:
                raise ValueError(f"节点 {node.node_id} 引用了不存在的依赖：{', '.join(sorted(missing))}")
        # Kahn's algorithm catches cycles at definition time, before a run can get stuck.
        remaining = {
            node.node_id: _node_dependencies(self, node)
            for node in self.nodes
        }
        visited: set[str] = set()
        while remaining:
            ready = [node_id for node_id, deps in remaining.items() if not deps - visited]
            if not ready:
                raise ValueError("工作流节点依赖存在循环")
            visited.update(ready)
            for node_id in ready:
                remaining.pop(node_id)
        return self


class CreateWorkflowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(default="1.0", min_length=1, max_length=32)
    description: str = Field(default="", max_length=1_000)
    nodes: list[WorkflowNodeDefinition] = Field(min_length=1, max_length=128)
    inputs: list[WorkflowInputDefinition] = Field(default_factory=list, max_length=64)
    edges: list[WorkflowEdgeDefinition] = Field(default_factory=list, max_length=512)
    outputs: list[WorkflowOutputBinding] = Field(default_factory=list, max_length=64)
    allow_pause: bool = True
    allow_retry: bool = True
    allow_recovery: bool = True


class CreateWorkflowRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    auto_start: bool = True


class WorkflowNodeReview(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    decision: WorkflowReviewDecision | None = None
    comment: str | None = None
    requested_at: datetime = Field(default_factory=_now)
    reviewed_at: datetime | None = None


class WorkflowNodeReviewRequest(BaseModel):
    decision: WorkflowReviewDecision
    comment: str | None = Field(default=None, max_length=1_000)
    edits: dict[str, Any] | None = None


class WorkflowNodeRun(BaseModel):
    node_id: str
    node_type: str
    version: str
    status: WorkflowNodeStatus = "pending"
    skip_reason: WorkflowNodeSkipReason | None = None
    progress: int = Field(default=0, ge=0, le=100)
    message: str = "等待依赖"
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    review: WorkflowNodeReview | None = None
    error: str | None = None
    cache_key: str | None = None
    attempt: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: float = Field(default=0, ge=0)


class WorkflowEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"workflow-event-{uuid.uuid4().hex[:12]}")
    level: WorkflowEventLevel = "info"
    message: str
    node_id: str | None = None
    created_at: datetime = Field(default_factory=_now)


class WorkflowRun(BaseModel):
    run_id: str
    workflow_id: str
    workflow_version: str | None = None
    project_id: str | None = None
    project_bound: bool = False
    status: WorkflowRunStatus = "draft"
    progress: int = Field(default=0, ge=0, le=100)
    message: str = "等待启动"
    input: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    node_runs: list[WorkflowNodeRun] = Field(default_factory=list)
    events: list[WorkflowEvent] = Field(default_factory=list, max_length=256)
    error: str | None = None
    retry_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class WorkflowProjectBinding(BaseModel):
    """The published workflow currently used by one Voice Project."""

    project_id: str = Field(min_length=1, max_length=200)
    workflow_id: str
    workflow_version: str
    bound_at: datetime
    updated_at: datetime


class ProjectWorkflowPayload(BaseModel):
    """Project-facing workflow surface: binding, published definition and runs."""

    project_id: str
    binding: WorkflowProjectBinding | None = None
    workflow: WorkflowDefinition | None = None
    runs: list[WorkflowRun] = Field(default_factory=list)


class BindProjectWorkflowRequest(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=200)


class WorkflowProblem(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _referenced_nodes(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        node_id = value.get("from_node") or value.get("node_id")
        if isinstance(node_id, str) and node_id:
            references.add(node_id)
        source = value.get("$from")
        if isinstance(source, str) and source:
            references.add(source.split(".", 1)[0])
        for item in value.values():
            references.update(_referenced_nodes(item))
    elif isinstance(value, list):
        for item in value:
            references.update(_referenced_nodes(item))
    elif isinstance(value, str) and value.startswith("$"):
        source = value[1:].split(".", 1)[0]
        if source and source != "input":
            references.add(source)
    return references


def _node_data_dependencies(definition: WorkflowDefinition, node: WorkflowNodeDefinition) -> set[str]:
    """Return dependencies that provide data or belong to the legacy graph form."""
    dependencies = set(node.depends_on) | _referenced_nodes(node.inputs)
    dependencies.update(
        edge.source_node
        for edge in definition.edges
        if edge.target_node == node.node_id and edge.kind == "data"
    )
    return dependencies


def _incoming_branch_edges(definition: WorkflowDefinition, node_id: str) -> list[WorkflowEdgeDefinition]:
    return [edge for edge in definition.edges if edge.target_node == node_id and edge.kind == "branch"]


def _node_dependencies(definition: WorkflowDefinition, node: WorkflowNodeDefinition) -> set[str]:
    """Return all ordering dependencies from data, branch and legacy bindings."""
    dependencies = _node_data_dependencies(definition, node)
    dependencies.update(edge.source_node for edge in _incoming_branch_edges(definition, node.node_id))
    return dependencies


_PORT_HELP = {
    "text": ("文本", "剧本或对白的文本内容，可连接到分析或语音生成节点。"),
    "file_path": ("文件路径", "工作区内 TXT 文件的相对路径；指定路径时读取该文件。"),
    "file_name": ("文件名", "导入文本的文件显示名称，用于记录来源。"),
    "source_name": ("来源名称", "实际导入的文件名或文本来源名称，供下游记录与展示。"),
    "project_id": ("项目 ID", "音频任务所属项目的唯一标识，下游节点应使用同一项目。"),
    "text_length": ("文本字数", "导入后去掉首尾空白的文本字符数。"),
    "segments": ("文本片段", "按原文顺序排列的片段，包含文本、说话人和情绪信息。"),
    "speakers": ("说话人", "识别或渲染得到的说话人信息，用于查看角色分配。"),
    "characters": ("角色画像", "识别到的角色列表，包括对白证据、声线画像和声音描述。"),
    "character_count": ("角色数量", "识别到的具名角色数量，不包含旁白和未确定的说话人。"),
    "value": ("判断值", "条件节点比较的原始值，可为文本、数字或结构化数据。"),
    "true": ("条件成立", "条件成立时输出 true，可连接目标节点的路由入口。"),
    "false": ("条件不成立", "条件不成立时输出 true，可连接另一分支的路由入口。"),
    "result": ("判断结果", "条件比较的布尔结果：成立为 true，否则为 false。"),
    "evidence_pack": ("角色证据", "角色的对白与上下文证据，用于分析年龄、性别、音色和表达习惯。"),
    "profile": ("声线画像", "角色的结构化声线要求，用于匹配或生成合适的声音。"),
    "voice_prompt": ("声音描述", "描述角色声音特征的提示词，供参考音频生成使用。"),
    "speaker_key": ("角色标识", "项目内角色的稳定标识，用于匹配并复用同一角色声音。"),
    "decision": ("声音决策", "声音匹配或生成的结果，包括候选来源及待审核状态。"),
    "voice_snapshot": ("声音快照", "项目已固定的角色声音信息，保证后续渲染复用同一声音。"),
    "reference_audio_url": ("参考音频", "高质量渲染使用的参考音频地址。"),
    "catalog_version_id": ("声音库版本", "指定声音库中一个固定版本，避免声音更新影响已发布流程。"),
    "audio_asset": ("音频资源", "单个音频的资源信息，包括可播放或下载的音频地址。"),
    "audio_assets": ("音频资源列表", "按处理顺序排列的音频资源信息，可查看每段音频及其任务。"),
    "job_id": ("任务 ID", "本节点生成音频的任务标识，用于查询状态或后续处理。"),
    "job_ids": ("任务列表", "按原文顺序排列的音频任务 ID，传给响度和合并节点时保留此顺序。"),
    "voice_map": ("角色声线映射", "以角色名为键、轻量声线 ID 为值，为每个角色指定声音。"),
    "fast_speed": ("语速", "轻量语音生成的速度倍率，范围 0.5–2.0，默认 1.0。"),
    "fast_rvc_enabled": ("启用变声", "是否启用 RVC 音色转换；开启时需要对应角色的可用模型。"),
    "audio_urls": ("音频地址列表", "逐段生成的音频播放或下载地址，顺序与文本片段一致。"),
    "route": ("生成路线", "指定合并的音频路线：fast 为极速路线，quality 为高质量路线。"),
    "audio_url": ("音频地址", "最终合并节目文件的播放或下载地址。"),
    "render_settings": ("推理设置", "生成路线、推理模型、语速与生成参数，由推理设置节点统一提供。"),
    "reference_map": ("角色参考音频", "按角色指定已确认的参考音频、参考文本或声音库版本，供质量路线使用。"),
    "reuse_run_id": ("复用已完成运行", "可选的已完成运行 ID；仅复用文本、角色和推理参数相同且音频仍存在的任务。"),
    "enabled": ("启用处理", "是否执行本节点的可选音频处理；关闭时按顺序传递任务列表。"),
    "quality_model": ("质量推理模型", "质量路线使用的 GPT-SoVITS 版本或 IndexTTS2，需本机已安装对应模型。"),
}


def _port(name: str, value_type: WorkflowValueType = "any", required: bool = False, multiple: bool = False, description: str = "") -> WorkflowPortDefinition:
    label, help_text = _PORT_HELP[name]
    return WorkflowPortDefinition(name=name, label=label, type=value_type, required=required, multiple=multiple, description=description or help_text)


class WorkflowNodeRegistry:
    """The single manifest seam shared by the canvas, validator and runtime."""

    def __init__(self) -> None:
        self._manifests = self._build_manifests()

    @staticmethod
    def _build_manifests() -> dict[str, WorkflowNodeManifest]:
        return {
            "text.import": WorkflowNodeManifest(
                type="text.import", version="1.0", label="导入文本", category="input",
                description="从运行输入或工作区内的 TXT 文件导入剧本文本，并传递项目上下文。",
                inputs=[
                    _port("text", "text", description="直接传入的文本内容"),
                    _port("file_path", "string", description="工作区内 TXT 文件的相对路径"),
                    _port("file_name", "string", description="导入文件显示名称"),
                    _port("project_id", "string", description="下游音频任务使用的项目 ID"),
                ],
                outputs=[
                    _port("text", "text", True),
                    _port("source_name", "string", True),
                    _port("project_id", "string"),
                    _port("text_length", "number"),
                ],
                config_schema={
                    "text": {"type": "string", "description": "选择 TXT 后保存的完整文本"},
                    "file_path": {"type": "string", "description": "工作区内 TXT 文件的相对路径"},
                    "file_name": {"type": "string", "description": "导入文件显示名称"},
                },
                execution=WorkflowNodeExecutionPolicy(mode="sync", resource_group="cpu", cacheable=False),
            ),
            "text.input": WorkflowNodeManifest(
                type="text.input", version="1.0", label="输入文本", category="input",
                description="接收短文本、剧本或源文档内容。",
                inputs=[_port("text", "text", True)], outputs=[_port("text", "text", True)],
                execution=WorkflowNodeExecutionPolicy(mode="sync", resource_group="cpu", cacheable=True),
            ),
            "text.analysis": WorkflowNodeManifest(
                type="text.analysis", version="1.0", label="文本分析", category="analysis",
                description="切分文本并识别说话人、情绪与证据。",
                inputs=[_port("text", "text", True)], outputs=[_port("segments", "text_segments", True), _port("speakers", "json")],
                config_schema={"speaker_names": {"type": "array", "items": {"type": "string"}, "description": "可选角色名提示；用于分开旁白和对白，不生成声线画像。"}},
                execution=WorkflowNodeExecutionPolicy(mode="sync", resource_group="cpu", cacheable=True),
            ),
            "human.speakers": WorkflowNodeManifest(
                type="human.speakers", version="1.0", label="校正说话人", category="voice",
                description="暂停工作流，逐句校正说话人；明确确认后才向下游传递。",
                inputs=[_port("segments", "text_segments", True)],
                outputs=[_port("segments", "text_segments", True), _port("speakers", "json")],
                execution=WorkflowNodeExecutionPolicy(mode="human", resource_group="cpu", cacheable=False, requires_human_review=True),
            ),
            "human.voices": WorkflowNodeManifest(
                type="human.voices", version="1.0", label="试听并确认声线", category="voice",
                description="暂停工作流，试听并确认每个角色的轻量声线；选择结果随审核记录保存。",
                inputs=[_port("segments", "text_segments", True)],
                outputs=[_port("segments", "text_segments", True), _port("speakers", "json")],
                execution=WorkflowNodeExecutionPolicy(mode="human", resource_group="cpu", cacheable=False, requires_human_review=True),
            ),
            "voice.character_analysis": WorkflowNodeManifest(
                type="voice.character_analysis", version="1.0", label="角色识别与画像", category="analysis",
                description="识别文本中的说话人，整理对白证据并生成角色声线画像。",
                inputs=[_port("text", "text", True)],
                outputs=[
                    _port("segments", "text_segments", True),
                    _port("characters", "json", True),
                    _port("character_count", "number"),
                ],
                config_schema={
                    "speaker_names": {"type": "array", "items": {"type": "string"}, "description": "小说角色名提示；将旁白与引号内对白分开，无法明确归属的对白保留为未指定说话人。"},
                    "profile_mode": {"type": "string", "enum": ["configured", "rules"], "default": "configured", "description": "configured 使用当前分析服务；rules 使用保守的规则画像，适合预置声线配音且不调用云端。"},
                },
                execution=WorkflowNodeExecutionPolicy(mode="sync", resource_group="analysis", cacheable=True),
            ),
            "control.condition": WorkflowNodeManifest(
                type="control.condition", version="1.0", label="条件分支", category="control",
                description="比较输入值并选择 true 或 false 控制流分支。",
                inputs=[_port("value", "any", True, description="需要判断的值")],
                outputs=[
                    _port("true", "boolean", description="条件命中分支"),
                    _port("false", "boolean", description="条件未命中分支"),
                    _port("result", "boolean", description="判断结果"),
                    _port("value", "any", description="原始判断值"),
                ],
                config_schema={
                    "logic": {"type": "string", "enum": ["all", "any"], "default": "all"},
                    "conditions": {
                        "type": "array",
                        "maxItems": 32,
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "default": "$"},
                                "operator": {"type": "string"},
                                "compare_value": {"type": "any"},
                            },
                        },
                    },
                    "operator": {
                        "type": "string",
                        "enum": [
                            "equals", "not_equals", "contains", "not_contains",
                            "greater_than", "greater_or_equal", "less_than", "less_or_equal",
                            "is_empty", "is_not_empty", "is_true", "is_false",
                        ],
                        "default": "equals",
                    },
                    "compare_value": {"type": "any"},
                },
                execution=WorkflowNodeExecutionPolicy(mode="control", resource_group="cpu", cacheable=True),
            ),
            "voice.profile": WorkflowNodeManifest(
                type="voice.profile", version="1.0", label="声线画像", category="voice",
                description="从证据包生成结构化 Character Voice Profile。",
                inputs=[_port("text", "text", True), _port("evidence_pack", "json")], outputs=[_port("profile", "voice_profile", True), _port("voice_prompt", "string")],
                execution=WorkflowNodeExecutionPolicy(mode="sync", resource_group="analysis", cacheable=True),
            ),
            "voice.match_or_generate": WorkflowNodeManifest(
                type="voice.match_or_generate", version="1.0", label="匹配或生成声音", category="voice",
                description="优先匹配项目快照和声音库，缺失时提出生成候选并等待审核。",
                inputs=[_port("profile", "voice_profile", True), _port("project_id", "string"), _port("speaker_key", "string")],
                outputs=[_port("decision", "json", True), _port("voice_snapshot", "voice_snapshot")],
                execution=WorkflowNodeExecutionPolicy(mode="human", resource_group="cpu", cacheable=False, requires_human_review=True),
            ),
            "voice.reference_audio": WorkflowNodeManifest(
                type="voice.reference_audio", version="1.0", label="生成参考音频", category="voice",
                description="生成可审核的 Canonical Reference 候选。",
                inputs=[_port("text", "text", True), _port("voice_prompt", "string", True), _port("project_id", "string")], outputs=[_port("audio_asset", "audio_asset", True), _port("job_id", "string", True)],
                execution=WorkflowNodeExecutionPolicy(mode="job", resource_group="gpu", cacheable=True, requires_human_review=True),
            ),
            "voice.quality_render": WorkflowNodeManifest(
                type="voice.quality_render", version="1.0", label="质量渲染", category="render",
                description="使用已批准项目声音或锁定的声音库版本生成高质量音频。",
                inputs=[_port("text", "text", True), _port("reference_audio_url", "string"), _port("catalog_version_id", "string"), _port("voice_snapshot", "voice_snapshot")], outputs=[_port("audio_asset", "audio_asset", True), _port("job_id", "string", True)],
                execution=WorkflowNodeExecutionPolicy(mode="job", resource_group="gpu", cacheable=True),
            ),
            "voice.fast_render": WorkflowNodeManifest(
                type="voice.fast_render", version="1.0", label="极速渲染", category="render",
                description="使用轻量 TTS 和可选 RVC 身份层快速生成音频。",
                inputs=[_port("text", "text", True), _port("project_id", "string", True)], outputs=[_port("audio_asset", "audio_asset", True), _port("job_id", "string", True)],
                execution=WorkflowNodeExecutionPolicy(mode="job", resource_group="gpu", cacheable=True),
            ),
            "voice.multi_role_fast_render": WorkflowNodeManifest(
                type="voice.multi_role_fast_render", version="1.0", label="多角色极速渲染", category="render",
                description="按角色分配轻量声线，逐句渲染识别出的文本片段并返回任务列表。",
                inputs=[
                    _port("segments", "text_segments", True),
                    _port("project_id", "string", True),
                    _port("voice_map", "json"),
                    _port("fast_speed", "number"),
                    _port("fast_rvc_enabled", "boolean"),
                ],
                outputs=[
                    _port("segments", "json", True, description="已渲染的片段列表，包括角色、声线 ID、任务 ID 和音频地址。"),
                    _port("job_ids", "job_ids", True),
                    _port("audio_urls", "audio_assets", True),
                    _port("audio_assets", "audio_assets"),
                    _port("speakers", "json"),
                ],
                config_schema={
                    "voice_map": {"type": "object", "description": "按识别到的说话人名称指定 fast_voice_id", "voice_options": [voice.model_dump(mode="json") for voice in FAST_VOICES]},
                    "fast_speed": {"type": "number", "default": 1.0, "minimum": 0.5, "maximum": 2.0},
                    "fast_rvc_enabled": {"type": "boolean", "default": False},
                },
                execution=WorkflowNodeExecutionPolicy(mode="job", resource_group="gpu", cacheable=True),
            ),
            "voice.assign_voices": WorkflowNodeManifest(
                type="voice.assign_voices", version="1.0", label="角色声线分配", category="voice",
                description="为文本片段分配角色声线和参考音频，生成配音计划，不执行语音生成。",
                inputs=[_port("segments", "text_segments", True), _port("voice_map", "json"), _port("reference_map", "json")],
                outputs=[_port("segments", "text_segments", True, description="已分配角色、声线和参考音频的配音片段，交给批量语音生成。"), _port("speakers", "json")],
                config_schema={
                    "voice_map": {"type": "object", "description": "角色名到轻量声线 ID 的映射", "voice_options": [voice.model_dump(mode="json") for voice in FAST_VOICES]},
                    "reference_map": {"type": "object", "description": "角色名到已确认参考音频信息的映射，包含 reference_audio_url、reference_text 或 catalog_version_id"},
                },
                execution=WorkflowNodeExecutionPolicy(mode="sync", resource_group="cpu", cacheable=True),
            ),
            "voice.render_settings": WorkflowNodeManifest(
                type="voice.render_settings", version="1.0", label="推理模型与参数", category="render",
                description="选择极速或质量路线、质量推理模型和语速，并把设置传给生成节点。",
                outputs=[_port("render_settings", "json", True), _port("route", "string", True), _port("quality_model", "string")],
                config_schema={
                    "route": {"type": "string", "enum": ["fast", "quality"], "enum_labels": {"fast": "极速路线 · 轻量中文 TTS", "quality": "质量路线 · 参考音频克隆"}, "default": "fast", "description": "选择语音生成路线"},
                    "quality_model": {"type": "string", "enum": [model.model_id for model in QUALITY_MODEL_SPECS], "enum_labels": {model.model_id: model.label for model in QUALITY_MODEL_SPECS}, "default": "gpt_sovits_v2", "description": "质量路线使用的推理模型"},
                    "fast_speed": {"type": "number", "default": 1.0, "minimum": 0.5, "maximum": 2.0, "description": "轻量语音生成的速度倍率"},
                    "render_options": {"type": "object", "description": "质量推理的高级参数"},
                },
                execution=WorkflowNodeExecutionPolicy(mode="sync", resource_group="cpu", cacheable=True),
            ),
            "voice.batch_render": WorkflowNodeManifest(
                type="voice.batch_render", version="1.0", label="批量语音生成", category="render",
                description="执行已分配的配音片段，依据独立推理设置生成极速或质量音频。",
                inputs=[_port("segments", "text_segments", True), _port("project_id", "string", True), _port("render_settings", "json", True), _port("reuse_run_id", "string")],
                outputs=[_port("segments", "json", True), _port("job_ids", "job_ids", True), _port("audio_assets", "audio_assets", True), _port("speakers", "json"), _port("route", "string", True)],
                config_schema={"reuse_run_id": {"type": "string", "default": "", "description": "可选已完成运行 ID；未应用 RVC 的极速片段在文本和声线参数相同时可复用"}},
                execution=WorkflowNodeExecutionPolicy(mode="job", resource_group="gpu", cacheable=False),
            ),
            "audio.rvc": WorkflowNodeManifest(
                type="audio.rvc", version="1.0", label="RVC 音色处理", category="audio",
                description="在生成后单独应用已批准的 RVC 身份或稳定层；关闭时直接传递音频任务。",
                inputs=[_port("project_id", "string", True), _port("job_ids", "job_ids", True, True), _port("route", "string", True), _port("enabled", "boolean")],
                outputs=[_port("job_ids", "job_ids", True), _port("audio_assets", "audio_assets", True)],
                config_schema={"enabled": {"type": "boolean", "default": False, "description": "使用已批准的角色 RVC，仍遵循 RVC 总开关；模型未就绪时保留原声音并记录结果"}},
                execution=WorkflowNodeExecutionPolicy(mode="job", resource_group="cpu", cacheable=False),
            ),
            "audio.loudness": WorkflowNodeManifest(
                type="audio.loudness", version="1.0", label="节目响度", category="audio",
                description="对一组已完成音频执行节目级响度处理。",
                inputs=[_port("project_id", "string", True), _port("job_ids", "job_ids", True, True)],
                outputs=[_port("audio_assets", "audio_assets", True), _port("job_ids", "job_ids", True, description="完成响度处理的任务 ID，保持输入顺序，可直接连接合并节点。")],
                execution=WorkflowNodeExecutionPolicy(mode="job", resource_group="cpu", cacheable=True),
            ),
            "audio.merge": WorkflowNodeManifest(
                type="audio.merge", version="1.0", label="合并导出", category="output",
                description="按路线合并音频并输出可下载节目文件。",
                inputs=[_port("project_id", "string", True), _port("route", "string", True), _port("job_ids", "job_ids", True, True)], outputs=[_port("audio_asset", "audio_asset", True), _port("audio_url", "string", True)],
                execution=WorkflowNodeExecutionPolicy(mode="job", resource_group="cpu", cacheable=True),
            ),
        }

    def list(self) -> list[WorkflowNodeManifest]:
        return [item.model_copy(deep=True) for item in self._manifests.values()]

    def get(self, node_type: str) -> WorkflowNodeManifest | None:
        return self._manifests.get(node_type)


class WorkflowService:
    """Small, persisted DAG runner used as the platform's first orchestration seam.

    It deliberately executes one ready node at a time inside each run. Existing TTS
    jobs already own the single-GPU queue, while this service owns dependency state,
    recovery and cache identity. That keeps the first kernel deterministic without
    introducing another model scheduler.
    """

    def __init__(
        self,
        workspace_root: Path,
        preparation: PreparationService,
        jobs: JobService,
        runtime_logger: logging.Logger | None = None,
        catalog: VoiceCatalogService | None = None,
        node_registry: WorkflowNodeRegistry | None = None,
        platform: PlatformService | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.root = self.workspace_root / "outputs" / "workflows"
        self.definition_root = self.root / "definitions"
        self.run_root = self.root / "runs"
        self.cache_root = self.root / "cache"
        self.project_binding_path = self.root / "project-bindings.json"
        for path in (self.definition_root, self.run_root, self.cache_root):
            path.mkdir(parents=True, exist_ok=True)
        self.preparation = preparation
        self.jobs = jobs
        self.catalog = catalog
        self.platform = platform or PlatformService(self.workspace_root, preparation, catalog=catalog, jobs=jobs)
        self.node_registry = node_registry or WorkflowNodeRegistry()
        self.runtime_logger = runtime_logger or logging.getLogger("zw_voice_factory")
        self._lock = threading.RLock()
        self._definitions: dict[str, WorkflowDefinition] = {}
        self._runs: dict[str, WorkflowRun] = {}
        self._project_bindings: dict[str, WorkflowProjectBinding] = {}
        self._futures: dict[str, Future[None]] = {}
        self._pause_events: dict[str, threading.Event] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="zw-workflow")
        self._load_definitions()
        self._load_runs()
        self._load_project_bindings()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def list_definitions(self) -> list[WorkflowDefinition]:
        with self._lock:
            return sorted(
                (item.model_copy(deep=True) for item in self._definitions.values()),
                key=lambda item: item.updated_at,
                reverse=True,
            )

    def get_definition(self, workflow_id: str) -> WorkflowDefinition:
        with self._lock:
            definition = self._definitions.get(workflow_id)
            if definition is None:
                raise WorkflowProblem(404, "工作流定义不存在")
            return definition.model_copy(deep=True)

    def get_project_workflow(self, project_id: str) -> ProjectWorkflowPayload:
        project_id = project_id.strip()
        if not project_id:
            raise WorkflowProblem(422, "项目 ID 不能为空")
        self._require_project(project_id)
        with self._lock:
            binding = self._project_bindings.get(project_id)
            workflow = self._definitions.get(binding.workflow_id) if binding else None
            runs = sorted(
                (item.model_copy(deep=True) for item in self._runs.values() if item.project_id == project_id),
                key=lambda item: item.created_at, reverse=True,
            )
            return ProjectWorkflowPayload(
                project_id=project_id,
                binding=binding.model_copy(deep=True) if binding else None,
                workflow=workflow.model_copy(deep=True) if workflow else None,
                runs=runs,
            )

    def bind_project_workflow(self, project_id: str, workflow_id: str) -> ProjectWorkflowPayload:
        project_id = project_id.strip()
        workflow_id = workflow_id.strip()
        if not project_id:
            raise WorkflowProblem(422, "项目 ID 不能为空")
        self._require_project(project_id)
        with self._lock:
            workflow = self._definitions.get(workflow_id)
            if workflow is None:
                raise WorkflowProblem(404, "工作流定义不存在")
            if workflow.lifecycle != "published":
                raise WorkflowProblem(409, "只有已发布工作流才能绑定到项目")
            now = _now()
            previous = self._project_bindings.get(project_id)
            binding = WorkflowProjectBinding(
                project_id=project_id,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.version,
                bound_at=previous.bound_at if previous and previous.workflow_id == workflow.workflow_id else now,
                updated_at=now,
            )
            bindings = {**self._project_bindings, project_id: binding}
            self._persist_project_bindings(bindings)
            self._project_bindings = bindings
        return self.get_project_workflow(project_id)

    def list_project_runs(self, project_id: str) -> list[WorkflowRun]:
        return self.get_project_workflow(project_id).runs

    def create_project_run(self, project_id: str, request: CreateWorkflowRunRequest) -> WorkflowRun:
        project_id = project_id.strip()
        self._require_project(project_id)
        with self._lock:
            binding = self._project_bindings.get(project_id)
            if binding is None:
                raise WorkflowProblem(409, "项目尚未绑定已发布工作流")
            definition = self._get_definition_locked(binding.workflow_id)
            if definition.lifecycle != "published":
                raise WorkflowProblem(409, "项目绑定的工作流未发布")
            run_input = {**request.input, "project_id": project_id}
            return self.create_run(binding.workflow_id, request.model_copy(update={"input": run_input}), project_bound=True)

    def _require_project(self, project_id: str) -> None:
        try:
            self.platform.get_project(project_id)
        except PlatformProblem as error:
            raise WorkflowProblem(error.status_code, error.detail) from error

    def create_definition(self, request: CreateWorkflowRequest) -> WorkflowDefinition:
        workflow_id = f"workflow-{uuid.uuid4().hex[:16]}"
        now = _now()
        try:
            definition = WorkflowDefinition(
                workflow_id=workflow_id,
                name=request.name.strip(),
                version=request.version,
                description=request.description,
                nodes=request.nodes,
                inputs=request.inputs,
                edges=request.edges,
                outputs=request.outputs,
                allow_pause=request.allow_pause,
                allow_retry=request.allow_retry,
                allow_recovery=request.allow_recovery,
                created_at=now,
                updated_at=now,
            )
        except ValueError as error:
            raise WorkflowProblem(422, str(error)) from error
        if not definition.name:
            raise WorkflowProblem(422, "工作流名称不能为空")
        validation = self.validate_definition(request)
        if not validation.valid:
            raise WorkflowProblem(422, "工作流校验失败：" + "；".join(validation.errors))
        with self._lock:
            self._definitions[workflow_id] = definition
            self._persist_definition(definition)
        return definition.model_copy(deep=True)

    def validate_definition(self, request: CreateWorkflowRequest) -> WorkflowGraphValidation:
        """Validate a draft without persisting it, for canvas feedback."""
        errors: list[str] = []
        warnings: list[str] = []
        try:
            candidate = WorkflowDefinition(
                workflow_id="validation",
                name=request.name.strip() or "未命名工作流",
                version=request.version,
                description=request.description,
                nodes=request.nodes,
                inputs=request.inputs,
                edges=request.edges,
                outputs=request.outputs,
                allow_pause=request.allow_pause,
                allow_retry=request.allow_retry,
                allow_recovery=request.allow_recovery,
                created_at=_now(),
                updated_at=_now(),
            )
        except (ValueError, TypeError) as error:
            return WorkflowGraphValidation(valid=False, errors=[str(error)], node_count=len(request.nodes), edge_count=len(request.edges))
        known = {node.node_id for node in candidate.nodes}

        # Manifest-aware checks are intentionally scoped to the explicit graph
        # form.  Legacy JSON workflows often omit ports that are optional in the
        # runtime implementation; keeping those drafts valid preserves the v1
        # contract while making the canvas fail fast on malformed v2 edges.
        explicit_graph = bool(candidate.edges)
        node_by_id = {node.node_id: node for node in candidate.nodes}
        incoming: dict[tuple[str, str], list[WorkflowEdgeDefinition]] = {}
        for edge in candidate.edges:
            source_manifest = self.node_registry.get(node_by_id[edge.source_node].node_type)
            target_manifest = self.node_registry.get(node_by_id[edge.target_node].node_type)
            source_port = None
            if source_manifest is None:
                warnings.append(f"节点类型 {node_by_id[edge.source_node].node_type} 没有 Manifest，无法校验输出端口 {edge.source_port}")
            else:
                source_port = next((port for port in source_manifest.outputs if port.name == edge.source_port), None)
                if source_port is None:
                    errors.append(f"连线 {edge.edge_id} 的源端口不存在：{edge.source_node}.{edge.source_port}")

            if edge.kind == "branch":
                if source_manifest is not None and source_manifest.execution.mode != "control":
                    errors.append(f"分支连线 {edge.edge_id} 必须从控制流节点发出")
                if source_port is not None and source_port.type != "boolean":
                    errors.append(f"分支连线 {edge.edge_id} 的源端口必须是 boolean 类型")
                continue

            if target_manifest is None:
                warnings.append(f"节点类型 {node_by_id[edge.target_node].node_type} 没有 Manifest，无法校验输入端口 {edge.target_port}")
            elif not any(port.name == edge.target_port for port in target_manifest.inputs):
                errors.append(f"连线 {edge.edge_id} 的目标端口不存在：{edge.target_node}.{edge.target_port}")
            incoming.setdefault((edge.target_node, edge.target_port), []).append(edge)

            if source_manifest is not None and target_manifest is not None:
                target_port = next((port for port in target_manifest.inputs if port.name == edge.target_port), None)
                if source_port is not None and target_port is not None and source_port.type != "any" and target_port.type != "any":
                    compatible = source_port.type == target_port.type
                    # Text is the wire-level representation used by both text
                    # and string ports, and JSON can carry all structured values.
                    compatible = compatible or {source_port.type, target_port.type} <= {"text", "string"}
                    compatible = compatible or source_port.type == "json" or target_port.type == "json"
                    if not compatible:
                        errors.append(
                            f"连线 {edge.edge_id} 类型不兼容：{source_port.type} → {target_port.type}"
                        )

        for (target_node, target_port), bindings in incoming.items():
            if len(bindings) <= 1:
                continue
            manifest = self.node_registry.get(node_by_id[target_node].node_type)
            port = next((item for item in manifest.inputs if item.name == target_port), None) if manifest else None
            if port is not None and not port.multiple:
                errors.append(f"输入端口只能接收一条连线：{target_node}.{target_port}")

        if explicit_graph:
            for node in candidate.nodes:
                manifest = self.node_registry.get(node.node_type)
                if manifest is None:
                    continue
                # Input nodes receive their values from the workflow invocation
                # payload, so their required ports are satisfied implicitly.
                if manifest.category == "input":
                    continue
                configured = set(node.inputs) | set(node.config)
                bound = {port for (target, port) in incoming if target == node.node_id}
                for port in manifest.inputs:
                    if port.required and port.name not in configured and port.name not in bound:
                        errors.append(f"节点 {node.node_id} 缺少必填输入端口：{port.name}")
                if manifest.execution.mode == "control":
                    branch_ports = {
                        edge.source_port
                        for edge in candidate.edges
                        if edge.source_node == node.node_id and edge.kind == "branch"
                    }
                    for branch_port in ("true", "false"):
                        if branch_port not in branch_ports:
                            warnings.append(f"条件节点 {node.node_id} 的 {branch_port} 分支尚未连接")

        for node in candidate.nodes:
            if node.node_type != "control.condition":
                continue
            try:
                WorkflowConditionConfig.model_validate(node.config)
            except (ValueError, TypeError) as error:
                errors.append(f"条件节点 {node.node_id} 配置无效：{error}")

        for output in candidate.outputs:
            source_manifest = self.node_registry.get(node_by_id[output.source_node].node_type)
            if source_manifest is not None and not any(port.name == output.source_port for port in source_manifest.outputs):
                errors.append(f"工作流输出 {output.name} 的源端口不存在：{output.source_node}.{output.source_port}")

        reachable = set()
        roots = {node.node_id for node in candidate.nodes if not _node_dependencies(candidate, node)}
        if explicit_graph:
            input_roots = {
                node.node_id
                for node in candidate.nodes
                if (self.node_registry.get(node.node_type) is not None and self.node_registry.get(node.node_type).category == "input")
                or any(isinstance(value, str) and value.startswith("$input.") for value in node.inputs.values())
            }
            if input_roots:
                roots = input_roots
        frontier = list(roots)
        while frontier:
            node_id = frontier.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            frontier.extend(edge.target_node for edge in candidate.edges if edge.source_node == node_id)
            for node in candidate.nodes:
                if node_id in _node_dependencies(candidate, node):
                    frontier.append(node.node_id)
        unreachable = sorted(known - reachable)
        if unreachable and explicit_graph:
            warnings.append(f"以下节点无法从输入根节点到达：{', '.join(unreachable)}")
        for node in candidate.nodes:
            if self.node_registry.get(node.node_type) is None:
                warnings.append(f"节点类型 {node.node_type} 没有已注册 Manifest，将按兼容模式执行")
        if not candidate.outputs:
            warnings.append("尚未声明工作流输出，运行结果只能从节点详情中查看")
        return WorkflowGraphValidation(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            node_count=len(candidate.nodes),
            edge_count=len(candidate.edges),
            unreachable_nodes=unreachable,
        )

    def node_manifests(self) -> list[WorkflowNodeManifest]:
        return self.node_registry.list()

    def publish(self, workflow_id: str) -> WorkflowDefinition:
        with self._lock:
            definition = self._get_definition_locked(workflow_id)
            validation = self.validate_definition(
                CreateWorkflowRequest(
                    name=definition.name,
                    version=definition.version,
                    description=definition.description,
                    nodes=definition.nodes,
                    inputs=definition.inputs,
                    edges=definition.edges,
                    outputs=definition.outputs,
                    allow_pause=definition.allow_pause,
                    allow_retry=definition.allow_retry,
                    allow_recovery=definition.allow_recovery,
                )
            )
            if not validation.valid:
                raise WorkflowProblem(422, "工作流校验失败：" + "；".join(validation.errors))
            definition.lifecycle = "published"
            definition.published_at = definition.published_at or _now()
            definition.updated_at = _now()
            self._persist_definition(definition)
            return definition.model_copy(deep=True)

    def update_definition(self, workflow_id: str, request: CreateWorkflowRequest) -> WorkflowDefinition:
        with self._lock:
            current = self._get_definition_locked(workflow_id)
            if current.lifecycle == "published":
                raise WorkflowProblem(409, "已发布工作流不可直接编辑，请复制为新的草稿版本")
            try:
                updated = WorkflowDefinition(
                    workflow_id=workflow_id,
                    name=request.name.strip(),
                    version=request.version,
                    description=request.description,
                    nodes=request.nodes,
                    inputs=request.inputs,
                    edges=request.edges,
                    outputs=request.outputs,
                    lifecycle="draft",
                    allow_pause=request.allow_pause,
                    allow_retry=request.allow_retry,
                    allow_recovery=request.allow_recovery,
                    created_at=current.created_at,
                    updated_at=_now(),
                )
            except (ValueError, TypeError) as error:
                raise WorkflowProblem(422, str(error)) from error
            validation = self.validate_definition(request)
            if not validation.valid:
                raise WorkflowProblem(422, "工作流校验失败：" + "；".join(validation.errors))
            self._definitions[workflow_id] = updated
            self._persist_definition(updated)
            return updated.model_copy(deep=True)

    def list_runs(self, workflow_id: str | None = None) -> list[WorkflowRun]:
        with self._lock:
            values = self._runs.values()
            if workflow_id is not None:
                values = (item for item in values if item.workflow_id == workflow_id)
            return sorted(
                (item.model_copy(deep=True) for item in values),
                key=lambda item: item.updated_at,
                reverse=True,
            )

    def get_run(self, run_id: str) -> WorkflowRun:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise WorkflowProblem(404, "工作流运行不存在")
            return run.model_copy(deep=True)

    def create_run(self, workflow_id: str, request: CreateWorkflowRunRequest, *, project_bound: bool = False) -> WorkflowRun:
        definition = self.get_definition(workflow_id)
        now = _now()
        normalized_input = dict(request.input)
        for input_definition in definition.inputs:
            if input_definition.name not in normalized_input and input_definition.default is not None:
                normalized_input[input_definition.name] = _jsonable(input_definition.default)
            value = normalized_input.get(input_definition.name)
            if input_definition.required and _is_empty_workflow_value(value):
                raise WorkflowProblem(422, f"请填写工作流输入：{input_definition.label or input_definition.name}")
            if value is None or value == "":
                continue
            invalid = (
                (input_definition.type in {"string", "text"} and not isinstance(value, str))
                or (input_definition.type == "boolean" and not isinstance(value, bool))
                or (input_definition.type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)))
                or (input_definition.widget == "select" and value not in input_definition.options)
            )
            if invalid:
                raise WorkflowProblem(422, f"工作流输入类型或选项不符：{input_definition.label or input_definition.name}")
        run = WorkflowRun(
            run_id=f"workflow-run-{uuid.uuid4().hex[:16]}",
            workflow_id=definition.workflow_id,
            workflow_version=definition.version,
            project_id=_optional_string(normalized_input.get("project_id")),
            project_bound=project_bound,
            status="queued" if request.auto_start else "draft",
            message="等待调度" if request.auto_start else "已创建，等待启动",
            input=_jsonable(normalized_input),
            node_runs=[
                WorkflowNodeRun(node_id=node.node_id, node_type=node.node_type, version=node.version)
                for node in definition.nodes
            ],
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._runs[run.run_id] = run
            self._pause_events[run.run_id] = threading.Event()
            self._cancel_events[run.run_id] = threading.Event()
            self._record_event_locked(run, "info", run.message)
            self._persist_run(run)
            if request.auto_start:
                self._schedule_locked(run)
        return run.model_copy(deep=True)

    def start(self, run_id: str) -> WorkflowRun:
        with self._lock:
            run = self._get_run_locked(run_id)
            definition = self._get_definition_locked(run.workflow_id)
            if run.status not in {"draft", "paused"}:
                raise WorkflowProblem(409, f"当前状态 {run.status} 不能启动工作流")
            if run.status == "paused":
                self._pause_events.setdefault(run_id, threading.Event()).clear()
            if not definition.allow_recovery and run.status == "paused":
                raise WorkflowProblem(409, "此工作流不允许恢复")
            run.status = "queued"
            run.message = "等待调度"
            run.error = None
            run.updated_at = _now()
            self._record_event_locked(run, "info", run.message)
            self._persist_run(run)
            self._schedule_locked(run)
            return run.model_copy(deep=True)

    def pause(self, run_id: str) -> WorkflowRun:
        with self._lock:
            run = self._get_run_locked(run_id)
            definition = self._get_definition_locked(run.workflow_id)
            if not definition.allow_pause:
                raise WorkflowProblem(409, "此工作流不允许暂停")
            if run.status not in {"queued", "running"}:
                raise WorkflowProblem(409, f"当前状态 {run.status} 不能暂停")
            self._pause_events.setdefault(run_id, threading.Event()).set()
            run.message = "将在当前节点完成后暂停"
            run.updated_at = _now()
            self._record_event_locked(run, "info", run.message)
            self._persist_run(run)
            return run.model_copy(deep=True)

    def resume(self, run_id: str) -> WorkflowRun:
        with self._lock:
            run = self._get_run_locked(run_id)
            if run.status != "paused":
                raise WorkflowProblem(409, f"当前状态 {run.status} 不能恢复")
            self._pause_events.setdefault(run_id, threading.Event()).clear()
            run.status = "queued"
            run.message = "恢复调度中"
            run.error = None
            run.updated_at = _now()
            self._record_event_locked(run, "info", run.message)
            self._persist_run(run)
            self._schedule_locked(run)
            return run.model_copy(deep=True)

    def retry(self, run_id: str) -> WorkflowRun:
        with self._lock:
            run = self._get_run_locked(run_id)
            definition = self._get_definition_locked(run.workflow_id)
            if not definition.allow_retry:
                raise WorkflowProblem(409, "此工作流不允许重试")
            if run.status != "failed":
                raise WorkflowProblem(409, f"当前状态 {run.status} 不能重试")
            failed_ids = {node.node_id for node in run.node_runs if node.status in {"failed", "skipped"}}
            reset_ids = _downstream_nodes(definition, failed_ids)
            reset_ids.update(failed_ids)
            for node in run.node_runs:
                if node.node_id not in reset_ids:
                    continue
                node.status = "pending"
                node.skip_reason = None
                node.progress = 0
                node.message = "等待重试"
                node.outputs = {}
                node.error = None
                node.finished_at = None
                node.elapsed_seconds = 0
            run.retry_count += 1
            run.status = "queued"
            run.progress = _run_progress(run)
            run.message = "失败节点等待重试"
            run.error = None
            run.finished_at = None
            run.updated_at = _now()
            self._record_event_locked(run, "warning", run.message)
            self._cancel_events.setdefault(run_id, threading.Event()).clear()
            self._pause_events.setdefault(run_id, threading.Event()).clear()
            self._persist_run(run)
            self._schedule_locked(run)
            return run.model_copy(deep=True)

    def review_node(self, run_id: str, node_id: str, request: WorkflowNodeReviewRequest) -> WorkflowRun:
        with self._lock:
            run = self._get_run_locked(run_id)
            node = self._node_run(run, node_id)
            if run.status != "awaiting_review" or node.status != "awaiting_review" or node.review is None:
                raise WorkflowProblem(409, f"节点 {node_id} 当前不在等待审核状态")
            if node.review.decision is not None:
                raise WorkflowProblem(409, f"节点 {node_id} 已完成审核")

            # Validate changes before recording approval; a rejected edit leaves the checkpoint open.
            payload = self._review_outputs(node, request.edits) if request.decision == "approved" else node.review.payload
            now = _now()
            node.review.payload = payload
            node.review.decision = request.decision
            node.review.comment = _optional_string(request.comment)
            node.review.reviewed_at = now
            if request.decision == "approved":
                node.outputs = _jsonable(node.review.payload)
                node.status = "completed"
                node.progress = 100
                node.message = "人工审核已批准"
                node.finished_at = now
                run.status = "queued"
                run.progress = _run_progress(run)
                run.message = "审核已批准，等待继续执行"
                run.error = None
                run.updated_at = now
                self._record_event_locked(run, "info", node.message, node.node_id)
                self._persist_run(run)
                self._schedule_locked(run)
            else:
                node.status = "cancelled"
                node.progress = 0
                node.message = "人工审核已拒绝"
                node.finished_at = now
                for pending in run.node_runs:
                    if pending.status in {"pending", "queued"}:
                        pending.status = "cancelled"
                        pending.message = "上游人工审核已拒绝"
                        pending.finished_at = now
                run.status = "cancelled"
                run.message = "人工审核已拒绝，工作流已终止"
                run.error = None
                run.finished_at = now
                run.updated_at = now
                self._cancel_events.setdefault(run_id, threading.Event()).set()
                self._record_event_locked(run, "warning", node.message, node.node_id)
                self._persist_run(run)
            return run.model_copy(deep=True)

    @staticmethod
    def _review_outputs(node: WorkflowNodeRun, edits: dict[str, Any] | None) -> dict[str, Any]:
        assert node.review is not None
        payload = dict(node.review.payload)
        if not edits:
            return payload
        field = {"human.speakers": "speakers", "human.voices": "voices"}.get(node.node_type)
        if field is None or set(edits) != {field} or not isinstance(edits[field], dict):
            raise WorkflowProblem(422, "审核修改字段无效")
        changes = edits[field]
        segments = [dict(segment) for segment in payload["segments"]]
        allowed = {str(segment["segment_id"]) for segment in segments} if field == "speakers" else {segment["speaker_name"] for segment in segments}
        if not set(changes).issubset(allowed):
            raise WorkflowProblem(422, "审核修改引用了不存在的片段或角色")
        for key, value in changes.items():
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
                raise WorkflowProblem(422, f"审核值无效：{key}")
            if field == "voices" and value not in _WORKFLOW_FAST_VOICE_IDS:
                raise WorkflowProblem(422, f"声线不存在：{value}")
        for segment in segments:
            if field == "speakers" and segment["segment_id"] in changes:
                name = changes[segment["segment_id"]].strip()
                kind = "narration" if name == "旁白" else "unknown" if name == "未指定说话人" else "named"
                segment.update(speaker_name=name, speaker_kind=kind,
                               character_id=f"workflow-character-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:12]}")
                segment["speaker"] = {**segment.get("speaker", {}), "speaker_name": name, "speaker_kind": kind}
            elif field == "voices" and segment["speaker_name"] in changes:
                segment["fast_voice_id"] = changes[segment["speaker_name"]]
        payload.update(segments=segments, speakers=list(dict.fromkeys(segment["speaker_name"] for segment in segments)))
        return payload

    def cancel(self, run_id: str) -> WorkflowRun:
        with self._lock:
            run = self._get_run_locked(run_id)
            if run.status in {"completed", "cancelled"}:
                raise WorkflowProblem(409, f"当前状态 {run.status} 不能取消")
            self._cancel_events.setdefault(run_id, threading.Event()).set()
            for node in run.node_runs:
                if node.status in {"pending", "queued", "running", "awaiting_review"}:
                    node.status = "cancelled"
                    node.message = "工作流已取消"
                    node.finished_at = _now()
            run.status = "cancelled"
            run.message = "工作流已取消"
            run.error = None
            run.finished_at = _now()
            run.updated_at = _now()
            self._record_event_locked(run, "warning", run.message)
            self._persist_run(run)
            return run.model_copy(deep=True)

    def _schedule_locked(self, run: WorkflowRun) -> None:
        future = self._futures.get(run.run_id)
        if future is not None and not future.done():
            return
        self._futures[run.run_id] = self._executor.submit(self._drive, run.run_id)

    def _drive(self, run_id: str) -> None:
        try:
            while True:
                with self._lock:
                    run = self._runs.get(run_id)
                    if run is None or run.status in {"completed", "cancelled", "awaiting_review"}:
                        return
                    definition = self._definitions.get(run.workflow_id)
                    if definition is None:
                        self._fail_run_locked(run, "工作流定义已丢失")
                        return
                    pause_event = self._pause_events.setdefault(run_id, threading.Event())
                    cancel_event = self._cancel_events.setdefault(run_id, threading.Event())
                    if cancel_event.is_set() or run.status == "cancelled":
                        self._cancel_pending_locked(run)
                        return
                    if pause_event.is_set():
                        self._pause_pending_locked(run)
                        return
                    if run.status in {"queued", "draft", "paused"}:
                        run.status = "running"
                        run.started_at = run.started_at or _now()
                        run.message = "工作流运行中"
                        run.updated_at = _now()
                        self._record_event_locked(run, "info", run.message)
                        self._persist_run(run)
                    ready = self._next_ready_node_locked(run, definition)
                    if ready is None:
                        if any(node.status == "failed" for node in run.node_runs):
                            self._fail_run_locked(run, run.error or "节点执行失败")
                        elif all(node.status in {"completed", "skipped"} for node in run.node_runs):
                            run.outputs = _resolve_workflow_outputs(definition, run)
                            run.status = "completed"
                            run.progress = 100
                            run.message = "工作流运行完成"
                            run.finished_at = _now()
                            run.updated_at = _now()
                            self._record_event_locked(run, "info", run.message)
                            self._persist_run(run)
                        else:
                            self._fail_run_locked(run, "工作流节点依赖无法满足")
                        return
                    ready.status = "queued"
                    ready.message = "等待节点执行"
                    node_definition = self._node_definition(definition, ready.node_id)
                    ready.inputs = _jsonable(
                        _resolve_node_inputs(
                            node_definition,
                            _resolve_inputs(node_definition.inputs, run.input, run.node_runs),
                            definition.edges,
                            run.node_runs,
                        )
                    )
                    if run.project_bound and run.project_id:
                        manifest = self.node_registry.get(node_definition.node_type)
                        if manifest and any(port.name == "project_id" for port in manifest.inputs):
                            ready.inputs["project_id"] = run.project_id
                    ready.cache_key = self._cache_key(definition, ready, run)
                    self._record_event_locked(run, "info", "节点等待执行", ready.node_id)
                    self._persist_run(run)
                    resolved_inputs = ready.inputs
                    cache_key = ready.cache_key
                self._execute_node(run_id, node_definition, resolved_inputs, cache_key)
        except Exception as error:
            self.runtime_logger.exception("[WORKFLOW %s] runner crashed", run_id)
            with self._lock:
                run = self._runs.get(run_id)
                if run is not None and run.status not in {"completed", "cancelled"}:
                    self._fail_run_locked(run, str(error))
        finally:
            with self._lock:
                self._futures.pop(run_id, None)
                run = self._runs.get(run_id)
                if run is not None and run.status == "queued":
                    self._schedule_locked(run)

    def _execute_node(
        self,
        run_id: str,
        definition: WorkflowNodeDefinition,
        resolved_inputs: dict[str, Any],
        cache_key: str | None,
    ) -> None:
        started = time.perf_counter()
        with self._lock:
            run = self._runs[run_id]
            node = self._node_run(run, definition.node_id)
            node.status = "running"
            node.progress = 5
            node.message = f"正在执行 {definition.node_type}"
            node.skip_reason = None
            node.attempt += 1
            node.started_at = _now()
            node.error = None
            self._record_event_locked(run, "info", node.message, node.node_id)
            self._persist_run(run)
        try:
            manifest = self.node_registry.get(definition.node_type)
            requires_review = manifest is not None and manifest.execution.mode == "human"
            cache_enabled = manifest is None or manifest.execution.cacheable
            cached = self._read_cache(cache_key) if cache_key and cache_enabled else None
            if cached is not None:
                outputs = cached
                node_status: WorkflowNodeStatus = "skipped"
                skip_reason: WorkflowNodeSkipReason | None = "cache_hit"
                message = "命中节点缓存，跳过执行"
            else:
                outputs = self._run_node(run_id, definition, resolved_inputs)
                if cache_key and cache_enabled and not requires_review:
                    self._write_cache(cache_key, outputs)
                node_status = "completed"
                skip_reason = None
                message = "节点执行完成"
            with self._lock:
                run = self._runs[run_id]
                node = self._node_run(run, definition.node_id)
                if run.status == "cancelled" or self._cancel_events.get(run_id, threading.Event()).is_set():
                    node.status = "cancelled"
                    node.progress = 0
                    node.message = "工作流已取消"
                    node.finished_at = _now()
                    node.elapsed_seconds = round(time.perf_counter() - started, 3)
                    self._record_event_locked(run, "warning", node.message, node.node_id)
                    self._persist_run(run)
                    return
                if requires_review:
                    node.status = "awaiting_review"
                    node.progress = 95
                    node.message = "候选结果等待人工审核"
                    node.outputs = {}
                    node.review = WorkflowNodeReview(payload=_jsonable(outputs))
                    node.elapsed_seconds = round(time.perf_counter() - started, 3)
                    run.status = "awaiting_review"
                    run.progress = _run_progress(run)
                    run.message = f"节点 {definition.node_id} 等待人工审核"
                    run.updated_at = _now()
                    self._record_event_locked(run, "info", node.message, node.node_id)
                    self._persist_run(run)
                    return
                node.status = node_status
                node.skip_reason = skip_reason
                node.progress = 100
                node.message = message
                node.outputs = _jsonable(outputs)
                node.finished_at = _now()
                node.elapsed_seconds = round(time.perf_counter() - started, 3)
                run.progress = _run_progress(run)
                run.message = f"已完成 {sum(item.status in {'completed', 'skipped'} for item in run.node_runs)}/{len(run.node_runs)} 个节点"
                run.updated_at = _now()
                self._record_event_locked(run, "info", node.message, node.node_id)
                self._persist_run(run)
        except _WorkflowPaused:
            with self._lock:
                run = self._runs[run_id]
                node = self._node_run(run, definition.node_id)
                node.status = "pending"
                node.progress = 0
                node.message = "等待恢复"
                run.status = "paused"
                run.message = "工作流已暂停"
                run.updated_at = _now()
                self._record_event_locked(run, "info", run.message, node.node_id)
                self._persist_run(run)
        except _WorkflowCancelled:
            with self._lock:
                run = self._runs[run_id]
                node = self._node_run(run, definition.node_id)
                node.status = "cancelled"
                node.message = "工作流已取消"
                node.finished_at = _now()
                run.status = "cancelled"
                run.message = "工作流已取消"
                run.finished_at = _now()
                run.updated_at = _now()
                self._record_event_locked(run, "warning", run.message, node.node_id)
                self._persist_run(run)
        except Exception as error:
            with self._lock:
                run = self._runs[run_id]
                node = self._node_run(run, definition.node_id)
                node.status = "failed"
                node.progress = 100
                node.message = "节点执行失败"
                node.error = str(error)
                node.finished_at = _now()
                node.elapsed_seconds = round(time.perf_counter() - started, 3)
                run.progress = _run_progress(run)
                self._record_event_locked(run, "error", node.error or node.message, node.node_id)
                self._fail_run_locked(run, f"节点 {definition.node_id} 失败：{error}")

    @staticmethod
    def _evidence_pack(
        inputs: dict[str, Any],
        config: dict[str, Any],
        missing_message: str,
    ) -> CharacterEvidencePack:
        evidence_value = inputs.get("evidence_pack")
        if isinstance(evidence_value, CharacterEvidencePack):
            return evidence_value
        if isinstance(evidence_value, dict):
            return CharacterEvidencePack.model_validate(evidence_value)
        text = inputs.get("text") or config.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(missing_message)
        character_id = str(inputs.get("character_id") or config.get("character_id") or "workflow-character")
        display_name = str(inputs.get("display_name") or config.get("display_name") or character_id)
        aliases = inputs.get("aliases", config.get("aliases", []))
        if isinstance(aliases, str):
            aliases = [aliases]
        evidence = inputs.get("evidence", config.get("evidence", [text]))
        if isinstance(evidence, str):
            evidence = [evidence]
        if not isinstance(evidence, list) or not evidence:
            evidence = [text]
        return CharacterEvidencePack(
            character_id=character_id,
            display_name=display_name,
            aliases=[str(item) for item in aliases],
            mention_count=int(inputs.get("mention_count", config.get("mention_count", 1))),
            dialogue_count=int(inputs.get("dialogue_count", config.get("dialogue_count", 1))),
            gender_hint=inputs.get("gender_hint", config.get("gender_hint", "unknown")),
            evidence=[str(item) for item in evidence[:8]],
            user_attributes=inputs.get("user_attributes", config.get("user_attributes")),
        )

    def _run_node(self, run_id: str, definition: WorkflowNodeDefinition, inputs: dict[str, Any]) -> dict[str, Any]:
        node_type = definition.node_type
        if node_type in {"control.condition", "control.if_else", "workflow.condition"}:
            value = inputs.get("value")
            config = WorkflowConditionConfig.model_validate(definition.config)
            result, condition_results = _evaluate_workflow_condition_group(value, config)
            return {
                "true": result,
                "false": not result,
                "result": result,
                "value": _jsonable(value),
                "logic": config.logic,
                "condition_results": condition_results,
            }

        if node_type in {"text.import", "voice.text_import", "text.file_import"}:
            text_value = inputs.get("text") or definition.config.get("text")
            file_path_value = inputs.get("file_path") or definition.config.get("file_path")
            file_name_value = inputs.get("file_name") or definition.config.get("file_name")
            project_id_value = inputs.get("project_id") or definition.config.get("project_id")
            with self._lock:
                run_input = self._runs[run_id].input
            text_value = text_value or run_input.get("text")
            file_path_value = file_path_value or run_input.get("file_path") or run_input.get("source_path")
            file_name_value = file_name_value or run_input.get("file_name") or run_input.get("source_name")
            project_id_value = project_id_value or run_input.get("project_id")
            source_name = str(file_name_value or "运行输入文本").strip() or "运行输入文本"
            if file_path_value:
                candidate = Path(str(file_path_value).strip())
                if not candidate.is_absolute():
                    candidate = self.workspace_root / candidate
                try:
                    resolved = candidate.resolve()
                    resolved.relative_to(self.workspace_root)
                except (OSError, ValueError) as error:
                    raise ValueError("导入文本节点只能读取工作区内的文件") from error
                if resolved.suffix.casefold() != ".txt":
                    raise ValueError("导入文本节点仅支持 TXT 文件")
                try:
                    raw = resolved.read_bytes()
                except OSError as error:
                    raise ValueError(f"导入文本文件读取失败：{resolved.name}") from error
                try:
                    text_value = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    try:
                        text_value = raw.decode("gb18030")
                    except UnicodeDecodeError as error:
                        raise ValueError("导入文本文件不是 UTF-8 或 GB18030 编码") from error
                source_name = resolved.name
            if not isinstance(text_value, str) or not text_value.strip():
                raise ValueError("导入文本节点需要文本内容或工作区内 TXT 文件")
            normalized = text_value.strip()
            return {
                "text": normalized,
                "source_name": source_name,
                "project_id": str(project_id_value).strip() if project_id_value else None,
                "text_length": len(normalized),
            }

        if node_type in {"text.input", "voice.text_ingest", "text.ingest"}:
            text = inputs.get("text", definition.config.get("text"))
            if text is None:
                with self._lock:
                    text = self._runs[run_id].input.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("文本输入节点需要非空 text")
            normalized = text.strip()
            return {"text": normalized, "text_length": len(normalized)}

        if node_type in {"text.analysis", "text.analyze", "voice.text_analysis"}:
            text = inputs.get("text") or definition.config.get("text")
            if not isinstance(text, str) or not text.strip():
                with self._lock:
                    text = self._runs[run_id].input.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("文本分析节点需要非空 text")
            speaker_names = definition.config.get("speaker_names")
            if speaker_names is not None and (not isinstance(speaker_names, list) or not all(isinstance(name, str) for name in speaker_names)):
                raise ValueError("文本分析节点 speaker_names 必须是角色名称数组")
            segments = analyze_text_segments(text.strip(), speaker_names=speaker_names)
            payload = [_jsonable(segment) for segment in segments]
            return {
                "segments": payload,
                "segment_count": len(payload),
                "speaker_assignments": [segment["speaker"] for segment in payload],
                "speakers": [segment["speaker"] for segment in payload],
            }

        if node_type == "voice.character_analysis":
            text = inputs.get("text") or definition.config.get("text")
            if not isinstance(text, str) or not text.strip():
                with self._lock:
                    text = self._runs[run_id].input.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("角色分析节点需要非空 text")
            speaker_names = definition.config.get("speaker_names")
            if speaker_names is not None and (not isinstance(speaker_names, list) or not all(isinstance(name, str) for name in speaker_names)):
                raise ValueError("角色分析节点 speaker_names 必须是角色名称数组")
            segments = analyze_text_segments(text.strip(), speaker_names=speaker_names)
            grouped: dict[str, list[str]] = {}
            for segment in segments:
                speaker = segment.speaker
                if speaker.speaker_kind == "named" and speaker.speaker_name:
                    grouped.setdefault(speaker.speaker_name, []).append(segment.text)
            characters: list[dict[str, Any]] = []
            profile_mode = definition.config.get("profile_mode", "configured")
            if profile_mode not in {"configured", "rules"}:
                raise ValueError("角色分析节点 profile_mode 只能是 configured 或 rules")
            rules_analyzer = RuleBasedVoiceAnalyzer(self.workspace_root) if profile_mode == "rules" else None
            for display_name, evidence in grouped.items():
                character_id = f"character-{hashlib.sha1(display_name.encode('utf-8')).hexdigest()[:12]}"
                evidence_pack = CharacterEvidencePack(
                    character_id=character_id,
                    display_name=display_name,
                    mention_count=len(evidence),
                    dialogue_count=len(evidence),
                    evidence=evidence[:8],
                )
                profile = rules_analyzer.analyze(evidence_pack) if rules_analyzer else self.preparation.preview_voice_profile(evidence_pack)
                characters.append(
                    {
                        "character_id": character_id,
                        "display_name": display_name,
                        "evidence_pack": _jsonable(evidence_pack),
                        "profile": _jsonable(profile),
                        "voice_prompt": profile.voice_prompt,
                    }
                )
            payload = [_jsonable(segment) for segment in segments]
            return {
                "segments": payload,
                "segment_count": len(payload),
                "characters": characters,
                "character_count": len(characters),
            }

        if node_type in {"voice.profile", "voice.character_profile", "voice.voice_profile"}:
            evidence_pack = self._evidence_pack(inputs, definition.config, "声线画像节点需要 text 或 evidence_pack")
            profile = self.preparation.preview_voice_profile(evidence_pack)
            return {"profile": _jsonable(profile), "voice_prompt": profile.voice_prompt, "character_id": evidence_pack.character_id}

        if node_type in {"voice.reference_text", "voice.reference_text_generation", "reference.text"}:
            evidence_pack = self._evidence_pack(inputs, definition.config, "参考文本节点需要 text 或 evidence_pack")
            prompt = inputs.get("voice_prompt") or definition.config.get("voice_prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                profile_value = inputs.get("profile")
                if isinstance(profile_value, dict):
                    prompt = profile_value.get("voice_prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("参考文本节点需要 voice_prompt 或 profile")
            draft = self.preparation.preview_reference_text(evidence_pack, prompt.strip())
            return {
                "reference_text": draft.text,
                "rationale": draft.rationale,
                "backend": draft.backend,
                "model": draft.model,
                "character_id": evidence_pack.character_id,
            }

        if node_type in {"voice.match", "voice.catalog_match", "voice.voice_match", "voice.match_or_generate"}:
            if self.catalog is None:
                raise ValueError("声音匹配节点尚未配置声音库服务")
            profile_value = inputs.get("profile")
            profile = profile_value if isinstance(profile_value, dict) else {}
            matches = self.catalog.match_assets(
                gender=inputs.get("gender") or profile.get("gender") or definition.config.get("gender"),
                age_range=inputs.get("age_range") or profile.get("age_range") or definition.config.get("age_range"),
                timbre_tags=inputs.get("timbre_tags") or profile.get("timbre_tags") or definition.config.get("timbre_tags", []),
                delivery_tags=inputs.get("delivery_tags") or profile.get("delivery_tags") or definition.config.get("delivery_tags", []),
                use_cases=inputs.get("use_cases") or definition.config.get("use_cases", []),
                voice_prompt=inputs.get("voice_prompt") or profile.get("voice_prompt") or definition.config.get("voice_prompt"),
                limit=int(inputs.get("limit") or definition.config.get("limit", 8)),
                require_ready=bool(inputs.get("require_ready", definition.config.get("require_ready", False))),
            )
            serialized = [_jsonable(match) for match in matches]
            if node_type == "voice.match_or_generate":
                selected = serialized[0] if serialized else None
                return {
                    "matches": serialized,
                    "match_count": len(serialized),
                    "top_match": selected,
                    "decision": {
                        "action": "catalog_match" if selected else "generate_candidate",
                        "selected_match": selected,
                        "reason": "匹配到项目可用音色" if selected else "暂无可复用音色，将生成候选",
                    },
                    "voice_snapshot": None,
                }
            return {
                "matches": serialized,
                "match_count": len(serialized),
                "top_match": serialized[0] if serialized else None,
            }

        if node_type in {"voice.reference_audio", "voice.reference_generation", "reference.audio"}:
            text = inputs.get("text") or definition.config.get("text")
            prompt = inputs.get("voice_prompt") or definition.config.get("voice_prompt")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("参考音频节点需要非空 text")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("参考音频节点需要非空 voice_prompt")
            project_id = inputs.get("project_id") or definition.config.get("project_id")
            reference_id = inputs.get("reference_id") or definition.config.get("reference_id")
            if bool(project_id) != bool(reference_id):
                raise ValueError("project_id 和 reference_id 必须同时提供")
            try:
                current = self._submit_and_wait(
                    run_id,
                    JobRequest(
                        kind="voxcpm_reference",
                        project_id=str(project_id) if project_id else None,
                        reference_id=str(reference_id) if reference_id else None,
                        text=text.strip(),
                        voice_prompt=prompt.strip(),
                    ),
                )
            except JobProblem as error:
                raise ValueError(error.detail) from error
            return {
                "job_id": current.job_id,
                "status": current.status,
                "audio_url": current.output_url,
                "audio_asset": {
                    "asset_type": "audio/wav",
                    "source_job_id": current.job_id,
                    "output_url": current.output_url,
                },
                "message": current.message,
            }

        if node_type in {"voice.quality_render", "tts.quality", "quality.render"}:
            text = inputs.get("text") or definition.config.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("质量渲染节点需要非空 text")
            catalog_asset_id = inputs.get("catalog_asset_id") or definition.config.get("catalog_asset_id")
            catalog_version_id = inputs.get("catalog_version_id") or definition.config.get("catalog_version_id")
            reference_audio_url = inputs.get("reference_audio_url") or definition.config.get("reference_audio_url")
            reference_text = inputs.get("reference_text") or definition.config.get("reference_text")
            voice_prompt = inputs.get("voice_prompt") or definition.config.get("voice_prompt") or "自然、清晰、稳定"
            resolved_catalog: dict[str, Any] | None = None
            if catalog_asset_id:
                if not catalog_version_id:
                    raise ValueError("工作流使用声音库渲染时必须锁定 catalog_version_id")
                if self.catalog is None:
                    raise ValueError("质量渲染节点尚未配置声音库服务")
                try:
                    reference = self.catalog.resolve_reference(str(catalog_asset_id), str(catalog_version_id) if catalog_version_id else None)
                except VoiceCatalogProblem as error:
                    raise ValueError(error.detail) from error
                reference_audio_url = reference.audio_url
                reference_text = reference_text or reference.reference_text
                voice_prompt = inputs.get("voice_prompt") or reference.voice_prompt
                resolved_catalog = {
                    "asset_id": reference.asset_id,
                    "version_id": reference.version_id,
                    "display_name": reference.display_name,
                }
                catalog_version_id = reference.version_id
            if not isinstance(reference_audio_url, str) or not reference_audio_url.strip():
                raise ValueError("质量渲染节点需要 reference_audio_url 或已完成的 catalog 音色版本")
            request = JobRequest(
                kind="quality_render",
                text=text.strip(),
                project_id=_optional_string(inputs.get("project_id") or definition.config.get("project_id")),
                reference_id=_optional_string(inputs.get("reference_id") or definition.config.get("reference_id")),
                character_id=_optional_string(inputs.get("character_id") or definition.config.get("character_id")),
                segment_id=_optional_string(inputs.get("segment_id") or definition.config.get("segment_id")),
                reference_audio_url=reference_audio_url.strip(),
                reference_text=_optional_string(reference_text),
                voice_prompt=str(voice_prompt),
                quality_model=str(inputs.get("quality_model") or definition.config.get("quality_model") or "gpt_sovits_v2"),
                emotion_description=_optional_string(inputs.get("emotion_description") or definition.config.get("emotion_description")),
            )
            current = self._submit_and_wait(run_id, request)
            return self._job_outputs(current, resolved_catalog)

        if node_type in {"human.speakers", "human.voices"}:
            raw = inputs.get("segments")
            if not isinstance(raw, list) or not raw:
                raise ValueError("人工审核需要非空文本片段")
            if node_type == "human.speakers":
                plan = _workflow_voice_plan(raw)
            else:
                plan = [dict(segment) for segment in raw]
                if any(segment.get("fast_voice_id") not in _WORKFLOW_FAST_VOICE_IDS for segment in plan):
                    raise ValueError("声线审核需要已分配的轻量声线")
            return {"segments": plan, "speakers": list(dict.fromkeys(segment["speaker_name"] for segment in plan))}

        if node_type == "voice.assign_voices":
            plan = _workflow_voice_plan(inputs.get("segments") or definition.config.get("segments"), inputs.get("voice_map", definition.config.get("voice_map")), inputs.get("reference_map", definition.config.get("reference_map")))
            return {"segments": plan, "speakers": list(dict.fromkeys(segment["speaker_name"] for segment in plan))}

        if node_type == "voice.render_settings":
            route = definition.config.get("route", "fast")
            model = definition.config.get("quality_model", "gpt_sovits_v2")
            if route not in {"fast", "quality"} or model not in {item.model_id for item in QUALITY_MODEL_SPECS}:
                raise ValueError("推理设置的路线或质量模型无效")
            speed = float(definition.config.get("fast_speed", 1.0))
            if not 0.5 <= speed <= 2.0:
                raise ValueError("推理设置语速必须介于 0.5 和 2.0 之间")
            settings = {"route": route, "quality_model": model, "fast_speed": speed, "render_options": definition.config.get("render_options", {})}
            return {"render_settings": settings, "route": route, "quality_model": model}

        if node_type == "voice.batch_render":
            return self._render_voice_plan(
                run_id, definition.node_id, inputs.get("segments"), inputs.get("project_id"),
                inputs.get("render_settings"), inputs.get("reuse_run_id") or definition.config.get("reuse_run_id"),
            )

        if node_type in {"voice.multi_role_fast_render", "voice.multi_role_render", "tts.multi_role_fast"}:
            plan = _workflow_voice_plan(
                inputs.get("segments") or definition.config.get("segments"),
                inputs.get("voice_map", definition.config.get("voice_map")),
            )
            return self._render_voice_plan(
                run_id, definition.node_id, plan, inputs.get("project_id") or definition.config.get("project_id"),
                {"route": "fast", "fast_speed": inputs.get("fast_speed", definition.config.get("fast_speed", 1.0)),
                 "fast_rvc_enabled": inputs.get("fast_rvc_enabled", definition.config.get("fast_rvc_enabled", False))},
            )

        if node_type in {"voice.fast_render", "tts.fast", "fast.render"}:
            text = inputs.get("text") or definition.config.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("极速渲染节点需要非空 text")
            project_id = inputs.get("project_id") or definition.config.get("project_id")
            if not isinstance(project_id, str) or not project_id.strip():
                raise ValueError("极速渲染节点需要 project_id")
            request = JobRequest(
                kind="fast_render",
                text=text.strip(),
                project_id=project_id.strip(),
                character_id=_optional_string(inputs.get("character_id") or definition.config.get("character_id")),
                reference_id=_optional_string(inputs.get("reference_id") or definition.config.get("reference_id")),
                segment_id=_optional_string(inputs.get("segment_id") or definition.config.get("segment_id")),
                fast_voice_id=str(inputs.get("fast_voice_id") or definition.config.get("fast_voice_id") or "suyingxue"),
                fast_speed=float(inputs.get("fast_speed") or definition.config.get("fast_speed") or 1.0),
                fast_rvc_enabled=bool(inputs.get("fast_rvc_enabled", definition.config.get("fast_rvc_enabled", False))),
            )
            current = self._submit_and_wait(run_id, request)
            return self._job_outputs(current)

        if node_type in {"voice.program_loudness", "audio.loudness", "voice.loudness"}:
            project_id = _optional_string(inputs.get("project_id") or definition.config.get("project_id"))
            if not project_id:
                raise ValueError("节目响度节点需要 project_id")
            job_ids = _workflow_job_ids(inputs.get("job_ids", definition.config.get("job_ids")))
            try:
                records = self.jobs.reprocess_loudness(project_id, job_ids)
            except JobProblem as error:
                raise ValueError(error.detail) from error
            serialized = [_jsonable(record) for record in records]
            return {
                "project_id": project_id,
                "job_ids": [record.job_id for record in records],
                "job_count": len(records),
                "jobs": serialized,
                "audio_urls": [record.output_url for record in records if record.output_url],
                "loudness_metrics": [_jsonable(record.loudness_metrics) for record in records],
                "audio_assets": [self._job_outputs(record)["audio_asset"] for record in records],
            }

        if node_type in {"voice.rvc_stability", "audio.rvc", "voice.rvc"}:
            project_id = _optional_string(inputs.get("project_id") or definition.config.get("project_id"))
            if not project_id:
                raise ValueError("RVC 稳定层节点需要 project_id")
            job_ids = _workflow_job_ids(inputs.get("job_ids", definition.config.get("job_ids")))
            route = inputs.get("route", definition.config.get("route", "quality"))
            if route not in {"fast", "quality"}:
                raise ValueError("RVC 路线只能是 quality 或 fast")
            enabled = inputs.get("enabled", definition.config.get("enabled", node_type != "audio.rvc"))
            if not isinstance(enabled, bool):
                raise ValueError("RVC enabled 必须是布尔值")
            try:
                if enabled:
                    records = self.jobs.reprocess_rvc(project_id, job_ids, route)
                else:
                    records = [self.jobs.get(job_id) for job_id in job_ids]
                    expected_kind = "fast_render" if route == "fast" else "quality_render"
                    if any(record.project_id != project_id or record.kind != expected_kind or record.status != "complete" for record in records):
                        raise ValueError("关闭 RVC 时也必须传入同一项目、同一路线的已完成音频")
            except JobProblem as error:
                raise ValueError(error.detail) from error
            serialized = [_jsonable(record) for record in records]
            return {
                "project_id": project_id,
                "enabled": enabled,
                "route": route,
                "job_ids": [record.job_id for record in records],
                "job_count": len(records),
                "jobs": serialized,
                "rvc_statuses": [record.rvc_status for record in records],
                "rvc_model_ids": [record.rvc_model_id for record in records],
                "audio_urls": [record.output_url for record in records if record.output_url],
                "base_audio_urls": [record.base_output_url for record in records if record.base_output_url],
                "rvc_audio_urls": [record.rvc_output_url for record in records if record.rvc_output_url],
                "audio_assets": [self._job_outputs(record)["audio_asset"] for record in records],
            }

        if node_type in {"voice.merge", "audio.export", "audio.merge", "voice.export"}:
            project_id = _optional_string(inputs.get("project_id") or definition.config.get("project_id"))
            if not project_id:
                raise ValueError("音频合并节点需要 project_id")
            route = inputs.get("route") or definition.config.get("route")
            if route not in {"quality", "fast"}:
                raise ValueError("音频合并节点 route 只能是 quality 或 fast")
            job_ids = _workflow_job_ids(inputs.get("job_ids", definition.config.get("job_ids")))
            kind: Literal["quality_render", "fast_render"] = (
                "quality_render" if route == "quality" else "fast_render"
            )
            try:
                merged = self.jobs.merge(project_id, job_ids, kind)
            except JobProblem as error:
                raise ValueError(error.detail) from error
            payload = _jsonable(merged)
            return {
                **payload,
                "project_id": project_id,
                "route": route,
                "kind": kind,
                "audio_url": merged.output_url,
                "audio_asset": {
                    "asset_type": "audio/wav",
                    "output_url": merged.output_url,
                    "project_id": project_id,
                    "route": route,
                    "segment_count": merged.segment_count,
                    "duration_seconds": merged.duration_seconds,
                    "loudness_metrics": _jsonable(merged.loudness_metrics),
                },
            }

        raise ValueError(f"暂不支持的工作流节点类型：{node_type}")

    def _render_voice_plan(
        self, run_id: str, node_id: str, plan: Any, project_id: Any, settings: Any,
        reuse_run_id: Any = None,
    ) -> dict[str, Any]:
        if not isinstance(plan, list) or not plan:
            raise ValueError("批量语音生成需要非空配音计划")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("批量语音生成需要 project_id")
        if not isinstance(settings, dict) or settings.get("route") not in {"fast", "quality"}:
            raise ValueError("批量语音生成需要有效的推理设置")
        project_id = project_id.strip()
        route = settings["route"]
        requests: list[JobRequest] = []
        for segment in plan:
            if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
                raise ValueError("配音计划片段必须包含 text")
            values = {
                "kind": "fast_render" if route == "fast" else "quality_render",
                "text": segment["text"].strip(), "project_id": project_id,
                "character_id": segment.get("character_id"), "segment_id": segment.get("segment_id"),
                "reference_id": segment.get("reference_id"),
            }
            if route == "fast":
                if not segment.get("fast_voice_id"):
                    raise ValueError("极速配音计划缺少声线，请先连接角色声线分配节点")
                values.update(fast_voice_id=segment["fast_voice_id"], fast_speed=settings.get("fast_speed", 1.0),
                              fast_rvc_enabled=settings.get("fast_rvc_enabled", False))
            else:
                audio_url = segment.get("reference_audio_url")
                reference_text = segment.get("reference_text")
                voice_prompt = segment.get("voice_prompt") or "自然、清晰、稳定"
                asset_id, version_id = segment.get("catalog_asset_id"), segment.get("catalog_version_id")
                if asset_id:
                    if not version_id or self.catalog is None:
                        raise ValueError("质量配音使用声音库时必须锁定已确认 catalog_version_id")
                    try:
                        reference = self.catalog.resolve_reference(str(asset_id), str(version_id))
                    except VoiceCatalogProblem as error:
                        raise ValueError(error.detail) from error
                    audio_url = reference.audio_url
                    reference_text = reference_text or reference.reference_text
                    voice_prompt = segment.get("voice_prompt") or reference.voice_prompt
                if not audio_url:
                    raise ValueError(f"角色 {segment.get('speaker_name', '未指定')} 缺少质量路线参考音频，请在声线分配节点配置 reference_map")
                values.update(reference_audio_url=audio_url, reference_text=reference_text, voice_prompt=voice_prompt,
                              catalog_asset_id=asset_id, catalog_version_id=version_id,
                              quality_model=settings.get("quality_model", "gpt_sovits_v2"),
                              quality_rvc_enabled=False,
                              render_options=settings.get("render_options", {}),
                              emotion_description=segment.get("emotion_description"))
            requests.append(JobRequest(**values))

        previous: dict[str, dict[str, Any]] = {}
        if reuse_run_id:
            prior = self.get_run(str(reuse_run_id))
            if prior.status != "completed":
                raise ValueError("复用音频需要一个已完成的工作流运行")
            for node in prior.node_runs:
                if node.status not in {"completed", "skipped"}:
                    continue
                for segment in node.outputs.get("segments", []):
                    if isinstance(segment, dict) and segment.get("job_id"):
                        previous[str(segment.get("segment_id"))] = segment

        rendered: list[dict[str, Any]] = []
        reused = 0
        for index, (segment, request) in enumerate(zip(plan, requests)):
            if self._cancel_requested(run_id):
                raise _WorkflowCancelled()
            fingerprint = hashlib.sha256(json.dumps(request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            current = self._reusable_fast_job(previous.get(str(request.segment_id)), request, fingerprint)
            cache_hit = current is not None
            if current is None:
                current = self._submit_and_wait(run_id, request)
            else:
                reused += 1
            output = self._job_outputs(current)
            rendered.append({
                **segment, "job_id": current.job_id, "audio_url": current.output_url,
                "audio_asset": output["audio_asset"], "request_fingerprint": fingerprint, "cache_hit": cache_hit,
            })
            if index % 5 == 0 or index + 1 == len(plan):
                with self._lock:
                    run = self._runs[run_id]
                    node = self._node_run(run, node_id)
                    node.progress = min(95, round(5 + 90 * (index + 1) / len(plan)))
                    node.message = f"已完成 {index + 1}/{len(plan)} 段，复用 {reused} 段"
                    run.progress = _run_progress(run)
                    run.updated_at = _now()
                    self._persist_run(run)
        speakers = list(dict.fromkeys(segment.get("speaker_name", "未指定说话人") for segment in rendered))
        return {
            "project_id": project_id, "route": route, "segments": rendered,
            "segment_count": len(rendered), "speaker_count": len(speakers), "speakers": speakers,
            "job_ids": [segment["job_id"] for segment in rendered],
            "audio_urls": [segment["audio_url"] for segment in rendered if segment["audio_url"]],
            "audio_assets": [segment["audio_asset"] for segment in rendered], "reused_job_count": reused,
        }

    def _reusable_fast_job(self, previous: dict[str, Any] | None, request: JobRequest, fingerprint: str) -> Any:
        # Quality/RVC depend on mutable approved assets; only reuse untouched fast renders.
        if previous is None or request.kind != "fast_render" or request.fast_rvc_enabled:
            return None
        if previous.get("request_fingerprint") and previous["request_fingerprint"] != fingerprint:
            return None
        if previous.get("text", "").strip() != request.text:
            return None
        try:
            record = self.jobs.get(previous["job_id"])
        except JobProblem:
            return None
        fields = ("kind", "project_id", "segment_id", "character_id", "reference_id", "fast_voice_id", "fast_speed", "fast_rvc_enabled")
        if record.status != "complete" or any(getattr(record, field) != getattr(request, field) for field in fields):
            return None
        if record.rvc_status not in {"not_requested", "bypassed"} or record.rvc_output_url:
            return None
        if record.output_url != f"/media/outputs/audio/jobs/{record.job_id}.wav" or not (self.jobs.audio_root / f"{record.job_id}.wav").is_file():
            return None
        return record

    def _submit_and_wait(self, run_id: str, request: JobRequest) -> Any:
        try:
            job = self.jobs.submit(request)
        except (JobProblem, ValueError, RuntimeError) as error:
            raise ValueError(error.detail if isinstance(error, JobProblem) else str(error)) from error
        while True:
            current = self.jobs.get(job.job_id)
            if current.status in {"complete", "failed", "cancelled"}:
                if current.status != "complete":
                    raise ValueError(current.error or current.message)
                return current
            if self._cancel_requested(run_id):
                raise _WorkflowCancelled()
            time.sleep(0.05)

    @staticmethod
    def _job_outputs(job: Any, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = job.model_dump(mode="json") if isinstance(job, BaseModel) else _jsonable(job)
        return {
            "job_id": job.job_id,
            "status": job.status,
            "audio_url": job.output_url,
            "base_audio_url": job.base_output_url,
            "rvc_audio_url": job.rvc_output_url,
            "loudness_metrics": _jsonable(job.loudness_metrics),
            "job": payload,
            "audio_asset": {
                "asset_type": "audio/wav",
                "source_job_id": job.job_id,
                "output_url": job.output_url,
                "base_output_url": job.base_output_url,
                "rvc_output_url": job.rvc_output_url,
                "catalog": catalog,
            },
        }

    def _next_ready_node_locked(self, run: WorkflowRun, definition: WorkflowDefinition) -> WorkflowNodeRun | None:
        runs = {node.node_id: node for node in run.node_runs}
        for node_definition in definition.nodes:
            node = runs[node_definition.node_id]
            if node.status != "pending":
                continue
            dependencies = _node_dependencies(definition, node_definition)
            if any(runs[item].status == "failed" for item in dependencies):
                node.status = "skipped"
                node.skip_reason = "dependency_failed"
                node.progress = 100
                node.message = "依赖节点失败"
                node.error = "依赖节点失败"
                node.finished_at = _now()
                continue
            if not all(runs[item].status in {"completed", "skipped"} for item in dependencies):
                continue

            branch_edges = _incoming_branch_edges(definition, node_definition.node_id)
            if branch_edges:
                branch_selected = any(
                    _node_produced_result(runs[edge.source_node])
                    and _workflow_truthy(runs[edge.source_node].outputs.get(edge.source_port))
                    for edge in branch_edges
                )
                if not branch_selected:
                    node.status = "skipped"
                    node.skip_reason = "branch_not_selected"
                    node.progress = 100
                    node.message = "条件分支未选中"
                    node.error = None
                    node.finished_at = _now()
                    continue

            data_dependencies = _node_data_dependencies(definition, node_definition)
            if data_dependencies and not any(_node_produced_result(runs[item]) for item in data_dependencies):
                node.status = "skipped"
                node.skip_reason = "upstream_not_selected"
                node.progress = 100
                node.message = "上游分支未产生结果"
                node.error = None
                node.finished_at = _now()
                continue
            return node
        return None

    @staticmethod
    def _node_definition(definition: WorkflowDefinition, node_id: str) -> WorkflowNodeDefinition:
        return next(node for node in definition.nodes if node.node_id == node_id)

    @staticmethod
    def _node_run(run: WorkflowRun, node_id: str) -> WorkflowNodeRun:
        return next(node for node in run.node_runs if node.node_id == node_id)

    def _get_run_locked(self, run_id: str) -> WorkflowRun:
        run = self._runs.get(run_id)
        if run is None:
            raise WorkflowProblem(404, "工作流运行不存在")
        return run

    def _get_definition_locked(self, workflow_id: str) -> WorkflowDefinition:
        definition = self._definitions.get(workflow_id)
        if definition is None:
            raise WorkflowProblem(404, "工作流定义不存在")
        return definition

    def _cache_key(self, definition: WorkflowDefinition, node: WorkflowNodeRun, run: WorkflowRun) -> str:
        node_definition = self._node_definition(definition, node.node_id)
        payload = {
            "workflow_version": definition.version,
            "node_id": node.node_id,
            "node_type": node.node_type,
            "node_version": node.version,
            "inputs": node.inputs,
            "config": node_definition.config,
        }
        return hashlib.sha256(json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def _read_cache(self, cache_key: str) -> dict[str, Any] | None:
        path = self.cache_root / f"{cache_key}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None

    def _write_cache(self, cache_key: str, outputs: dict[str, Any]) -> None:
        target = self.cache_root / f"{cache_key}.json"
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(_jsonable(outputs), ensure_ascii=False, indent=2), encoding="utf-8")
        self._replace_with_retry(temporary, target)

    def _persist_definition(self, definition: WorkflowDefinition) -> None:
        target = self.definition_root / f"{definition.workflow_id}.json"
        temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(definition.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        self._replace_with_retry(temporary, target)

    def _persist_run(self, run: WorkflowRun) -> None:
        run.updated_at = _now()
        target = self.run_root / f"{run.run_id}.json"
        temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        self._replace_with_retry(temporary, target)

    @staticmethod
    def _replace_with_retry(temporary: Path, target: Path) -> None:
        """Atomically publish JSON while tolerating transient Windows file locks."""
        try:
            for attempt in range(5):
                try:
                    os.replace(temporary, target)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.03 * (attempt + 1))
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _load_definitions(self) -> None:
        for path in self.definition_root.glob("*.json"):
            try:
                definition = WorkflowDefinition.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                self.runtime_logger.warning("忽略无效工作流定义：%s", path)
                continue
            self._definitions[definition.workflow_id] = definition

    def _load_runs(self) -> None:
        for path in self.run_root.glob("*.json"):
            try:
                run = WorkflowRun.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                self.runtime_logger.warning("忽略无效工作流运行：%s", path)
                continue
            if run.status in {"queued", "running"}:
                run.status = "failed"
                run.error = "服务重启时工作流中断"
                run.message = "服务重启时中断，可执行重试或恢复"
                for node in run.node_runs:
                    if node.status in {"queued", "running"}:
                        node.status = "failed"
                        node.error = run.error
                        node.message = "服务重启时中断"
                self._persist_run(run)
            self._runs[run.run_id] = run
            if run.project_id is None:
                run.project_id = _optional_string(run.input.get("project_id"))
            if run.workflow_version is None and run.workflow_id in self._definitions:
                run.workflow_version = self._definitions[run.workflow_id].version
            self._pause_events[run.run_id] = threading.Event()
            self._cancel_events[run.run_id] = threading.Event()

    def _load_project_bindings(self) -> None:
        if not self.project_binding_path.is_file():
            return
        try:
            payload = json.loads(self.project_binding_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            self.runtime_logger.warning("忽略无效项目工作流绑定文件：%s", self.project_binding_path)
            return
        items = payload if isinstance(payload, list) else []
        for item in items:
            try:
                binding = WorkflowProjectBinding.model_validate(item)
            except (ValueError, TypeError):
                self.runtime_logger.warning("忽略无效项目工作流绑定：%s", item)
                continue
            self._project_bindings[binding.project_id] = binding

    def _persist_project_bindings(self, bindings: dict[str, WorkflowProjectBinding]) -> None:
        payload = [item.model_dump(mode="json") for item in bindings.values()]
        temporary = self.project_binding_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._replace_with_retry(temporary, self.project_binding_path)

    def _cancel_requested(self, run_id: str) -> bool:
        with self._lock:
            return self._cancel_events.get(run_id, threading.Event()).is_set()

    def _pause_requested(self, run_id: str) -> bool:
        with self._lock:
            return self._pause_events.get(run_id, threading.Event()).is_set()

    def _cancel_pending_locked(self, run: WorkflowRun) -> None:
        for node in run.node_runs:
            if node.status in {"pending", "queued", "awaiting_review"}:
                node.status = "cancelled"
                node.message = "工作流已取消"
                node.finished_at = _now()
        run.status = "cancelled"
        run.message = "工作流已取消"
        run.finished_at = _now()
        self._persist_run(run)

    def _pause_pending_locked(self, run: WorkflowRun) -> None:
        for node in run.node_runs:
            if node.status == "queued":
                node.status = "pending"
                node.message = "等待恢复"
        run.status = "paused"
        run.message = "工作流已暂停"
        self._persist_run(run)

    def _fail_run_locked(self, run: WorkflowRun, detail: str) -> None:
        run.status = "failed"
        run.error = detail
        run.message = "工作流执行失败"
        run.finished_at = _now()
        run.updated_at = _now()
        self._record_event_locked(run, "error", detail)
        self._persist_run(run)

    def _record_event_locked(
        self,
        run: WorkflowRun,
        level: WorkflowEventLevel,
        message: str,
        node_id: str | None = None,
    ) -> None:
        run.events.append(WorkflowEvent(level=level, message=message, node_id=node_id))
        if len(run.events) > 256:
            del run.events[:-256]
        getattr(self.runtime_logger, level)(
            "[WORKFLOW %s]%s %s",
            run.run_id,
            f" [{node_id}]" if node_id else "",
            message,
        )


class _WorkflowPaused(Exception):
    pass


class _WorkflowCancelled(Exception):
    pass


def _resolve_inputs(value: Any, run_input: dict[str, Any], node_runs: list[WorkflowNodeRun]) -> Any:
    outputs = {node.node_id: node.outputs for node in node_runs}
    if isinstance(value, dict):
        node_id = value.get("from_node") or value.get("node_id")
        output_name = value.get("output")
        if isinstance(node_id, str) and isinstance(output_name, str):
            return outputs.get(node_id, {}).get(output_name)
        source = value.get("$from")
        if isinstance(source, str):
            parts = source.split(".", 1)
            return outputs.get(parts[0], {}).get(parts[1], None) if len(parts) == 2 else outputs.get(parts[0], {})
        return {key: _resolve_inputs(item, run_input, node_runs) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_inputs(item, run_input, node_runs) for item in value]
    if isinstance(value, str) and value.startswith("$"):
        source = value[1:]
        if source.startswith("input."):
            return run_input.get(source[6:])
        parts = source.split(".", 1)
        return outputs.get(parts[0], {}).get(parts[1], None) if len(parts) == 2 else outputs.get(parts[0], {})
    return value


def _resolve_node_inputs(
    definition: WorkflowNodeDefinition,
    base_inputs: Any,
    edges: list[WorkflowEdgeDefinition],
    node_runs: list[WorkflowNodeRun],
) -> dict[str, Any]:
    """Apply explicit v2 edge bindings on top of legacy input expressions."""
    resolved: dict[str, Any] = dict(base_inputs) if isinstance(base_inputs, dict) else {}
    outputs = {node.node_id: node.outputs for node in node_runs}
    grouped: dict[str, list[Any]] = {}
    for edge in edges:
        if edge.target_node != definition.node_id or edge.kind != "data":
            continue
        source = outputs.get(edge.source_node, {})
        value: Any = None
        found = False
        if edge.source_port in source:
            value = source[edge.source_port]
            found = True
        elif edge.source_port == "output":
            value = source
            found = True
        if found:
            grouped.setdefault(edge.target_port, []).append(value)
    for target_port, values in grouped.items():
        # The validator rejects fan-in to scalar ports.  Keeping the runtime
        # tolerant here makes custom manifests useful as well: one value stays
        # scalar, while a fan-in is represented as an ordered list.
        resolved[target_port] = values[0] if len(values) == 1 else values
    return resolved


def _resolve_workflow_outputs(definition: WorkflowDefinition, run: WorkflowRun) -> dict[str, Any]:
    """Project declared output bindings onto the completed run payload."""
    outputs = {node.node_id: node.outputs for node in run.node_runs}
    result: dict[str, Any] = {}
    for binding in definition.outputs:
        source = outputs.get(binding.source_node, {})
        if binding.source_port in source:
            result[binding.name] = _jsonable(source[binding.source_port])
        else:
            result[binding.name] = None
    return result


def _downstream_nodes(definition: WorkflowDefinition, roots: set[str]) -> set[str]:
    result = set(roots)
    changed = True
    while changed:
        changed = False
        for node in definition.nodes:
            dependencies = _node_dependencies(definition, node)
            if dependencies & result and node.node_id not in result:
                result.add(node.node_id)
                changed = True
    return result


def _node_produced_result(node: WorkflowNodeRun) -> bool:
    """Distinguish a cache-hit ``skipped`` node from a branch that produced nothing."""
    if node.status == "completed":
        return True
    if node.status != "skipped":
        return False
    if node.skip_reason == "cache_hit":
        return True
    # Runs persisted before skip_reason existed can still be interpreted from outputs.
    return node.skip_reason is None and bool(node.outputs)


def _run_progress(run: WorkflowRun) -> int:
    if not run.node_runs:
        return 100
    return round(sum(node.progress for node in run.node_runs) / len(run.node_runs))


def _http_error(error: WorkflowProblem) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)


def create_workflow_router(service: WorkflowService) -> APIRouter:
    router = APIRouter(prefix="/api/workflows", tags=["workflows"])

    @router.get("", response_model=list[WorkflowDefinition])
    def list_workflows() -> list[WorkflowDefinition]:
        return service.list_definitions()

    @router.post("", response_model=WorkflowDefinition, status_code=status.HTTP_201_CREATED)
    def create_workflow(request: CreateWorkflowRequest) -> WorkflowDefinition:
        try:
            return service.create_definition(request)
        except WorkflowProblem as error:
            raise _http_error(error) from error

    @router.get("/node-types", response_model=list[WorkflowNodeManifest])
    def list_node_types() -> list[WorkflowNodeManifest]:
        return service.node_manifests()

    @router.post("/validate", response_model=WorkflowGraphValidation)
    def validate_workflow(request: CreateWorkflowRequest) -> WorkflowGraphValidation:
        return service.validate_definition(request)

    @router.get("/projects/{project_id}", response_model=ProjectWorkflowPayload)
    def get_project_workflow(project_id: str) -> ProjectWorkflowPayload:
        try:
            return service.get_project_workflow(project_id)
        except WorkflowProblem as error:
            raise _http_error(error) from error

    @router.put("/projects/{project_id}/binding", response_model=ProjectWorkflowPayload)
    def bind_project_workflow(project_id: str, request: BindProjectWorkflowRequest) -> ProjectWorkflowPayload:
        try:
            return service.bind_project_workflow(project_id, request.workflow_id)
        except WorkflowProblem as error:
            raise _http_error(error) from error

    @router.get("/projects/{project_id}/runs", response_model=list[WorkflowRun])
    def list_project_workflow_runs(project_id: str) -> list[WorkflowRun]:
        try:
            return service.list_project_runs(project_id)
        except WorkflowProblem as error:
            raise _http_error(error) from error

    @router.post("/projects/{project_id}/runs", response_model=WorkflowRun, status_code=status.HTTP_202_ACCEPTED)
    def create_project_workflow_run(project_id: str, request: CreateWorkflowRunRequest) -> WorkflowRun:
        try:
            return service.create_project_run(project_id, request)
        except WorkflowProblem as error:
            raise _http_error(error) from error

    @router.get("/runs", response_model=list[WorkflowRun])
    def list_runs() -> list[WorkflowRun]:
        return service.list_runs()

    @router.get("/{workflow_id}", response_model=WorkflowDefinition)
    def get_workflow(workflow_id: str) -> WorkflowDefinition:
        try:
            return service.get_definition(workflow_id)
        except WorkflowProblem as error:
            raise _http_error(error) from error

    @router.put("/{workflow_id}", response_model=WorkflowDefinition)
    def update_workflow(workflow_id: str, request: CreateWorkflowRequest) -> WorkflowDefinition:
        try:
            return service.update_definition(workflow_id, request)
        except WorkflowProblem as error:
            raise _http_error(error) from error

    @router.post("/{workflow_id}/publish", response_model=WorkflowDefinition)
    def publish_workflow(workflow_id: str) -> WorkflowDefinition:
        try:
            return service.publish(workflow_id)
        except WorkflowProblem as error:
            raise _http_error(error) from error

    @router.get("/{workflow_id}/runs", response_model=list[WorkflowRun])
    def list_workflow_runs(workflow_id: str) -> list[WorkflowRun]:
        try:
            service.get_definition(workflow_id)
            return service.list_runs(workflow_id)
        except WorkflowProblem as error:
            raise _http_error(error) from error

    @router.post("/{workflow_id}/runs", response_model=WorkflowRun, status_code=status.HTTP_202_ACCEPTED)
    def create_workflow_run(workflow_id: str, request: CreateWorkflowRunRequest) -> WorkflowRun:
        try:
            return service.create_run(workflow_id, request)
        except WorkflowProblem as error:
            raise _http_error(error) from error

    @router.post("/{workflow_id}/runs/start", response_model=WorkflowRun, status_code=status.HTTP_202_ACCEPTED)
    def start_workflow_run(workflow_id: str, request: CreateWorkflowRunRequest) -> WorkflowRun:
        try:
            return service.create_run(workflow_id, request.model_copy(update={"auto_start": True}))
        except WorkflowProblem as error:
            raise _http_error(error) from error

    return _create_run_action_routes(router, service)


def _create_run_action_routes(router: APIRouter, service: WorkflowService) -> APIRouter:
    @router.get("/runs/{run_id}", response_model=WorkflowRun)
    def get_run(run_id: str) -> WorkflowRun:
        try:
            return service.get_run(run_id)
        except WorkflowProblem as error:
            raise _http_error(error) from error

    @router.post(
        "/runs/{run_id}/nodes/{node_id}/review",
        response_model=WorkflowRun,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def review_run_node(run_id: str, node_id: str, request: WorkflowNodeReviewRequest) -> WorkflowRun:
        try:
            return service.review_node(run_id, node_id, request)
        except WorkflowProblem as error:
            raise _http_error(error) from error

    def action(name: str, handler: Any) -> None:
        @router.post(f"/runs/{{run_id}}/{name}", response_model=WorkflowRun, status_code=status.HTTP_202_ACCEPTED)
        def run_action(run_id: str) -> WorkflowRun:
            try:
                return handler(run_id)
            except WorkflowProblem as error:
                raise _http_error(error) from error

    action("start", service.start)
    action("pause", service.pause)
    action("resume", service.resume)
    action("retry", service.retry)
    action("cancel", service.cancel)
    return router
