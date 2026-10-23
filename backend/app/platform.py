from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .jobs import JobProblem, JobRequest, JobService
from .preparation import PreparationService, SourceSummary
from .voice_catalog import VoiceCatalogMatch, VoiceCatalogProblem, VoiceCatalogService


PlatformProjectType = Literal["short_text", "novel", "script", "subtitle", "document"]
PlatformProjectStatus = Literal["draft", "ready", "running", "complete", "failed"]
VoiceTaskType = Literal["quick_preview", "long_form", "render"]
VoiceTaskStatus = Literal["draft", "ready_for_review", "running", "complete", "failed"]
SpeakerKind = Literal["narration", "named", "unknown"]
ProjectVoiceSnapshotSource = Literal["catalog", "generated", "archetype"]
ProjectVoiceSnapshotStatus = Literal["pending", "approved", "superseded", "revoked", "failed"]
VoiceGenerationStatus = Literal["not_started", "queued", "running", "complete", "failed"]
MatchOrGenerateAction = Literal["reuse_snapshot", "catalog_match", "generate_candidate", "generation_started"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PlatformProject(BaseModel):
    project_id: str
    name: str
    content_type: PlatformProjectType
    status: PlatformProjectStatus
    source_text_length: int = 0
    task_count: int = 0
    created_at: datetime
    updated_at: datetime


class CreatePlatformProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    content_type: PlatformProjectType = "short_text"
    text: str = Field(min_length=1, max_length=200_000)


class SpeakerAssignment(BaseModel):
    speaker_kind: SpeakerKind
    speaker_name: str
    confidence: float = Field(ge=0, le=1)
    voice_status: Literal["unmatched", "archetype_available", "project_snapshot", "catalog_match"]


class TextSegment(BaseModel):
    segment_id: str
    index: int
    text: str
    speaker: SpeakerAssignment
    emotion: str
    emotion_confidence: float = Field(ge=0, le=1)


class VoiceTask(BaseModel):
    task_id: str
    project_id: str
    task_type: VoiceTaskType
    status: VoiceTaskStatus
    input_text_length: int
    segment_count: int
    matched_voice_count: int
    generated_voice_count: int
    message: str
    created_at: datetime
    updated_at: datetime
    segments: list[TextSegment] = Field(default_factory=list)


class ProjectVoiceSnapshot(BaseModel):
    """Project-owned binding to one immutable catalog version or local candidate."""

    snapshot_id: str
    project_id: str
    speaker_key: str
    display_name: str
    source: ProjectVoiceSnapshotSource
    status: ProjectVoiceSnapshotStatus
    catalog_asset_id: str | None = None
    catalog_version_id: str | None = None
    job_id: str | None = None
    audio_url: str | None = None
    reference_text: str | None = None
    voice_prompt: str = "自然、清晰、稳定"
    generation_status: VoiceGenerationStatus = "not_started"
    match_score: float | None = Field(default=None, ge=0, le=1)
    match_reasons: list[str] = Field(default_factory=list, max_length=8)
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None = None


class ProjectVoiceSnapshotRequest(BaseModel):
    speaker_key: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=120)
    source: Literal["catalog", "generated"] = "catalog"
    catalog_asset_id: str | None = Field(default=None, max_length=120)
    catalog_version_id: str | None = Field(default=None, max_length=120)
    job_id: str | None = Field(default=None, max_length=120)
    voice_prompt: str | None = Field(default=None, max_length=1_000)
    reference_text: str | None = Field(default=None, max_length=220)
    match_score: float | None = Field(default=None, ge=0, le=1)
    match_reasons: list[str] = Field(default_factory=list, max_length=8)


class MatchOrGenerateRequest(BaseModel):
    speaker_key: str | None = Field(default=None, max_length=120)
    character_id: str | None = Field(default=None, max_length=120)
    display_name: str = Field(min_length=1, max_length=120)
    profile: dict[str, Any] = Field(default_factory=dict)
    gender: str | None = Field(default=None, max_length=40)
    age_range: str | None = Field(default=None, max_length=80)
    timbre_tags: list[str] = Field(default_factory=list, max_length=8)
    delivery_tags: list[str] = Field(default_factory=list, max_length=8)
    use_cases: list[str] = Field(default_factory=list, max_length=8)
    voice_prompt: str | None = Field(default=None, max_length=1_000)
    reference_text: str | None = Field(default=None, max_length=220)
    limit: int = Field(default=8, ge=1, le=32)
    minimum_score: float = Field(default=0.58, ge=0, le=1)

    @property
    def resolved_speaker_key(self) -> str:
        return (self.speaker_key or self.character_id or self.display_name).strip()


class VoiceGenerationPlan(BaseModel):
    status: VoiceGenerationStatus
    required: bool = True
    job_id: str | None = None
    snapshot_id: str | None = None
    reference_text: str
    voice_prompt: str


class ProjectVoiceDecision(BaseModel):
    decision_id: str
    project_id: str
    speaker_key: str
    display_name: str
    action: MatchOrGenerateAction
    source: Literal["project_snapshot", "catalog", "generate"]
    reason: str
    generation_required: bool = False
    snapshot: ProjectVoiceSnapshot | None = None
    selected_match: VoiceCatalogMatch | None = None
    candidates: list[VoiceCatalogMatch] = Field(default_factory=list)
    generation: VoiceGenerationPlan | None = None
    created_at: datetime
    updated_at: datetime


class PlatformProjectWorkspace(BaseModel):
    project: PlatformProject
    tasks: list[VoiceTask] = Field(default_factory=list)
    voice_snapshots: list[ProjectVoiceSnapshot] = Field(default_factory=list)
    voice_decisions: list[ProjectVoiceDecision] = Field(default_factory=list)


class PlatformService:
    def __init__(
        self,
        workspace_root: Path,
        preparation: PreparationService,
        catalog: VoiceCatalogService | None = None,
        jobs: JobService | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.root = self.workspace_root / "outputs" / "platform"
        self.project_root = self.root / "projects"
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.preparation = preparation
        self.catalog = catalog
        self.jobs = jobs
        self._voice_lock = threading.RLock()

    def list_projects(self) -> list[PlatformProject]:
        projects = [self._read_project(path) for path in self.project_root.glob("*/project.json")]
        projects.extend(self._source_project(source) for source in self.preparation.list_sources())
        return sorted(projects, key=lambda item: item.updated_at, reverse=True)

    def get_project(self, project_id: str) -> PlatformProject:
        if not project_id or any(mark in project_id for mark in ("/", "\\", ":", "\0")) or project_id in {".", ".."}:
            raise PlatformProblem(404, "音声项目不存在")
        return self._find_project(project_id)

    def create_project(self, request: CreatePlatformProjectRequest) -> PlatformProject:
        name = re.sub(r"\s+", " ", request.name).strip()
        if any(mark in name for mark in ("/", "\\", ":", "\0")):
            raise PlatformProblem(400, "项目名称包含不安全字符")
        if any(item.name.casefold() == name.casefold() for item in self.list_projects()):
            raise PlatformProblem(409, "同名项目已存在")
        project_id = f"voice-project-{uuid.uuid4().hex[:16]}"
        project_dir = self.project_root / project_id
        project_dir.mkdir(parents=True, exist_ok=False)
        now = datetime.now(timezone.utc)
        project = PlatformProject(
            project_id=project_id,
            name=name,
            content_type=request.content_type,
            status="ready",
            source_text_length=len(request.text),
            created_at=now,
            updated_at=now,
        )
        (project_dir / "source.txt").write_text(request.text, encoding="utf-8")
        self._write_json(project_dir / "project.json", project.model_dump(mode="json"))
        (project_dir / "tasks").mkdir()
        (project_dir / "voice" / "snapshots").mkdir(parents=True)
        (project_dir / "voice" / "decisions").mkdir(parents=True)
        return project

    def workspace(self, project_id: str) -> PlatformProjectWorkspace:
        project = self._find_project(project_id)
        if project.content_type == "novel":
            return PlatformProjectWorkspace(project=project)
        tasks_dir = self.project_root / project_id / "tasks"
        tasks = [self._read_task(path) for path in tasks_dir.glob("*.json")]
        return PlatformProjectWorkspace(
            project=project,
            tasks=sorted(tasks, key=lambda item: item.updated_at, reverse=True),
            voice_snapshots=self.list_voice_snapshots(project_id),
            voice_decisions=self.list_voice_decisions(project_id),
        )

    def quick_preview(self, project_id: str) -> VoiceTask:
        project = self._find_project(project_id)
        if project.content_type == "novel":
            raise PlatformProblem(409, "小说项目请从小说工作台进入，不能使用短文本预览")
        source_path = self.project_root / project_id / "source.txt"
        try:
            text = source_path.read_text(encoding="utf-8")
        except OSError as error:
            raise PlatformProblem(404, "项目源文本不存在") from error
        segments = self._analyze_segments(text)
        now = datetime.now(timezone.utc)
        task = VoiceTask(
            task_id=f"voice-task-{uuid.uuid4().hex[:16]}",
            project_id=project_id,
            task_type="quick_preview",
            status="ready_for_review",
            input_text_length=len(text),
            segment_count=len(segments),
            matched_voice_count=sum(item.speaker.voice_status != "unmatched" for item in segments),
            generated_voice_count=0,
            message="文本已完成初步分析，请确认说话人和声音后进入渲染",
            created_at=now,
            updated_at=now,
            segments=segments,
        )
        task_dir = self.project_root / project_id / "tasks"
        task_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(task_dir / f"{task.task_id}.json", task.model_dump(mode="json"))
        project.task_count += 1
        project.updated_at = now
        self._write_json(self.project_root / project_id / "project.json", project.model_dump(mode="json"))
        return task

    def list_voice_snapshots(self, project_id: str) -> list[ProjectVoiceSnapshot]:
        self._require_owned_project(project_id)
        with self._voice_lock:
            snapshots = [self._read_snapshot(path) for path in self._snapshot_dir(project_id).glob("*.json")]
            refreshed = [self._refresh_snapshot(snapshot) for snapshot in snapshots]
            return sorted(refreshed, key=lambda item: item.updated_at, reverse=True)

    def accept_voice_snapshot(
        self,
        project_id: str,
        request: ProjectVoiceSnapshotRequest,
    ) -> ProjectVoiceSnapshot:
        self._require_owned_project(project_id)
        speaker_key = request.speaker_key.strip()
        display_name = request.display_name.strip()
        existing_candidate: ProjectVoiceSnapshot | None = None
        if request.source == "catalog":
            if not request.catalog_asset_id or not request.catalog_version_id:
                raise PlatformProblem(422, "项目声音快照必须锁定 catalog_asset_id 和 catalog_version_id")
            if self.catalog is None:
                raise PlatformProblem(503, "声音库服务尚未配置")
            try:
                reference = self.catalog.resolve_reference(request.catalog_asset_id, request.catalog_version_id)
            except VoiceCatalogProblem as error:
                raise PlatformProblem(error.status_code, error.detail) from error
            audio_url = reference.audio_url
            reference_text = request.reference_text or reference.reference_text
            voice_prompt = request.voice_prompt or reference.voice_prompt
            generation_status: VoiceGenerationStatus = "complete"
        else:
            if not request.job_id:
                raise PlatformProblem(422, "生成型项目声音快照必须提供 job_id")
            if self.jobs is None:
                raise PlatformProblem(503, "音频任务服务尚未配置")
            try:
                job = self.jobs.get(request.job_id)
            except JobProblem as error:
                raise PlatformProblem(error.status_code, error.detail) from error
            if job.kind != "voxcpm_reference":
                raise PlatformProblem(409, "只能接受由参考音频任务生成的项目声音候选")
            if job.status != "complete" or not job.output_url:
                raise PlatformProblem(409, "生成候选尚未完成，暂不能批准项目声音快照")
            with self._voice_lock:
                existing_candidate = next(
                    (
                        item
                        for item in self._read_snapshots_locked(project_id)
                        if item.job_id == request.job_id
                        and item.speaker_key == speaker_key
                        and item.source == "generated"
                        and item.status == "pending"
                    ),
                    None,
                )
            if existing_candidate is None:
                raise PlatformProblem(409, "此生成任务不是当前项目和说话人的待审核候选")
            audio_url = job.output_url
            reference_text = request.reference_text or existing_candidate.reference_text
            voice_prompt = request.voice_prompt or existing_candidate.voice_prompt
            generation_status = "complete"

        now = _now()
        snapshot_id = existing_candidate.snapshot_id if existing_candidate else f"snapshot-{uuid.uuid4().hex[:16]}"
        stored_audio_url = self._materialize_snapshot_audio(project_id, snapshot_id, audio_url)
        snapshot = ProjectVoiceSnapshot(
            snapshot_id=snapshot_id,
            project_id=project_id,
            speaker_key=speaker_key,
            display_name=display_name,
            source=request.source,
            status="approved",
            catalog_asset_id=request.catalog_asset_id,
            catalog_version_id=request.catalog_version_id,
            job_id=request.job_id,
            audio_url=stored_audio_url,
            reference_text=reference_text,
            voice_prompt=voice_prompt,
            generation_status=generation_status,
            match_score=request.match_score,
            match_reasons=list(request.match_reasons),
            created_at=existing_candidate.created_at if existing_candidate else now,
            updated_at=now,
            approved_at=now,
        )
        with self._voice_lock:
            snapshots = self._read_snapshots_locked(project_id)
            for previous in snapshots:
                if previous.snapshot_id != snapshot.snapshot_id and previous.speaker_key == speaker_key and previous.status == "approved":
                    superseded = previous.model_copy(update={"status": "superseded", "updated_at": now})
                    self._write_snapshot(superseded)
            self._write_snapshot(snapshot)
        return snapshot

    def match_or_generate(
        self,
        project_id: str,
        request: MatchOrGenerateRequest,
    ) -> ProjectVoiceDecision:
        self._require_owned_project(project_id)
        speaker_key = request.resolved_speaker_key
        profile = request.profile

        with self._voice_lock:
            snapshots = self._read_snapshots_locked(project_id)
            approved = [
                item
                for item in snapshots
                if item.speaker_key == speaker_key and item.status == "approved"
            ]
        if approved:
            snapshot = self._refresh_snapshot(sorted(approved, key=lambda item: item.updated_at, reverse=True)[0])
            if snapshot.status == "approved" and snapshot.audio_url:
                return self._persist_decision(
                    ProjectVoiceDecision(
                        decision_id=f"decision-{uuid.uuid4().hex[:16]}",
                        project_id=project_id,
                        speaker_key=speaker_key,
                        display_name=request.display_name.strip(),
                        action="reuse_snapshot",
                        source="project_snapshot",
                        reason="已找到当前项目已批准的声音快照，优先复用并保持版本锁定。",
                        snapshot=snapshot,
                        created_at=_now(),
                        updated_at=_now(),
                    )
                )

        candidates: list[VoiceCatalogMatch] = []
        if self.catalog is not None:
            try:
                candidates = self.catalog.match_assets(
                    gender=request.gender or profile.get("gender"),
                    age_range=request.age_range or profile.get("age_range"),
                    timbre_tags=request.timbre_tags or profile.get("timbre_tags", []),
                    delivery_tags=request.delivery_tags or profile.get("delivery_tags", []),
                    use_cases=request.use_cases or profile.get("use_cases", []),
                    voice_prompt=request.voice_prompt or profile.get("voice_prompt"),
                    limit=request.limit,
                    require_ready=True,
                )
            except VoiceCatalogProblem as error:
                raise PlatformProblem(error.status_code, error.detail) from error
        if candidates and candidates[0].score >= request.minimum_score:
            return self._persist_decision(
                ProjectVoiceDecision(
                    decision_id=f"decision-{uuid.uuid4().hex[:16]}",
                    project_id=project_id,
                    speaker_key=speaker_key,
                    display_name=request.display_name.strip(),
                    action="catalog_match",
                    source="catalog",
                    reason="已找到达到最低匹配分数的声音库版本，等待用户明确接受后绑定项目。",
                    generation_required=False,
                    selected_match=candidates[0],
                    candidates=candidates,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )

        prompt = (request.voice_prompt or profile.get("voice_prompt") or "自然、清晰、稳定").strip()
        reference_text = (request.reference_text or "").strip() or (
            f"我是{request.display_name.strip()}，请保持自然、清晰、稳定的表达，完整读出这段参考文本。"
        )
        reason = "没有找到达到最低匹配分数的已完成声音版本，生成候选是下一步选项；系统不会自动生成或批准。"
        return self._persist_decision(
            ProjectVoiceDecision(
                decision_id=f"decision-{uuid.uuid4().hex[:16]}",
                project_id=project_id,
                speaker_key=speaker_key,
                display_name=request.display_name.strip(),
                action="generate_candidate",
                source="generate",
                reason=reason,
                generation_required=True,
                candidates=candidates,
                generation=VoiceGenerationPlan(
                    status="not_started",
                    reference_text=reference_text,
                    voice_prompt=prompt,
                ),
                created_at=_now(),
                updated_at=_now(),
            )
        )

    def generate_from_decision(self, project_id: str, decision_id: str) -> ProjectVoiceDecision:
        self._require_owned_project(project_id)
        with self._voice_lock:
            decision = self._read_decision(self._decision_dir(project_id) / f"{decision_id}.json")
        if decision.action not in {"generate_candidate", "generation_started"} or decision.generation is None:
            raise PlatformProblem(409, "只有需要生成候选的决策才能启动生成")
        if decision.generation.snapshot_id:
            snapshots = {item.snapshot_id: item for item in self.list_voice_snapshots(project_id)}
            snapshot = snapshots.get(decision.generation.snapshot_id)
            if snapshot is not None:
                return decision.model_copy(update={"snapshot": snapshot})
        if self.jobs is None:
            raise PlatformProblem(503, "音频任务服务尚未配置")
        try:
            job = self.jobs.submit(
                JobRequest(
                    kind="voxcpm_reference",
                    text=decision.generation.reference_text,
                    voice_prompt=decision.generation.voice_prompt,
                )
            )
        except (JobProblem, ValueError, RuntimeError) as error:
            detail = error.detail if isinstance(error, JobProblem) else str(error)
            raise PlatformProblem(getattr(error, "status_code", 409), detail) from error
        now = _now()
        snapshot = ProjectVoiceSnapshot(
            snapshot_id=f"snapshot-{uuid.uuid4().hex[:16]}",
            project_id=project_id,
            speaker_key=decision.speaker_key,
            display_name=decision.display_name,
            source="generated",
            status="pending",
            job_id=job.job_id,
            reference_text=decision.generation.reference_text,
            voice_prompt=decision.generation.voice_prompt,
            generation_status="queued",
            created_at=now,
            updated_at=now,
        )
        with self._voice_lock:
            self._write_snapshot(snapshot)
            updated = decision.model_copy(
                update={
                    "action": "generation_started",
                    "snapshot": snapshot,
                    "generation": decision.generation.model_copy(
                        update={"status": "queued", "job_id": job.job_id, "snapshot_id": snapshot.snapshot_id}
                    ),
                    "updated_at": now,
                }
            )
            self._write_decision(updated)
        return updated

    def list_voice_decisions(self, project_id: str) -> list[ProjectVoiceDecision]:
        self._require_owned_project(project_id)
        with self._voice_lock:
            decisions = [self._read_decision(path) for path in self._decision_dir(project_id).glob("*.json")]
        snapshots = {item.snapshot_id: item for item in self.list_voice_snapshots(project_id)}
        refreshed: list[ProjectVoiceDecision] = []
        for decision in decisions:
            if decision.snapshot is not None:
                snapshot = snapshots.get(decision.snapshot.snapshot_id, decision.snapshot)
                decision = decision.model_copy(update={"snapshot": snapshot})
                if decision.generation is not None and snapshot.job_id:
                    decision = decision.model_copy(
                        update={"generation": decision.generation.model_copy(update={"status": snapshot.generation_status})}
                    )
            refreshed.append(decision)
        return sorted(refreshed, key=lambda item: item.updated_at, reverse=True)

    def get_voice_decision(self, project_id: str, decision_id: str) -> ProjectVoiceDecision:
        self._require_owned_project(project_id)
        path = self._decision_dir(project_id) / f"{decision_id}.json"
        if not path.is_file():
            raise PlatformProblem(404, "声音决策不存在")
        decisions = {item.decision_id: item for item in self.list_voice_decisions(project_id)}
        decision = decisions.get(decision_id)
        if decision is None:
            raise PlatformProblem(404, "声音决策不存在")
        return decision

    def _persist_decision(self, decision: ProjectVoiceDecision) -> ProjectVoiceDecision:
        with self._voice_lock:
            self._write_decision(decision)
        return decision

    def _require_owned_project(self, project_id: str) -> PlatformProject:
        path = self.project_root / project_id / "project.json"
        if not path.is_file():
            raise PlatformProblem(409, "只有平台项目才能绑定项目声音快照")
        self._ensure_voice_dirs(project_id)
        return self._read_project(path)

    def _ensure_voice_dirs(self, project_id: str) -> None:
        voice_root = self.project_root / project_id / "voice"
        (voice_root / "snapshots").mkdir(parents=True, exist_ok=True)
        (voice_root / "decisions").mkdir(parents=True, exist_ok=True)

    def _snapshot_dir(self, project_id: str) -> Path:
        self._ensure_voice_dirs(project_id)
        return self.project_root / project_id / "voice" / "snapshots"

    def _decision_dir(self, project_id: str) -> Path:
        self._ensure_voice_dirs(project_id)
        return self.project_root / project_id / "voice" / "decisions"

    def _read_snapshots_locked(self, project_id: str) -> list[ProjectVoiceSnapshot]:
        return [self._refresh_snapshot(self._read_snapshot(path)) for path in self._snapshot_dir(project_id).glob("*.json")]

    def _refresh_snapshot(self, snapshot: ProjectVoiceSnapshot) -> ProjectVoiceSnapshot:
        if snapshot.source != "generated" or not snapshot.job_id or snapshot.status in {"approved", "superseded", "revoked"}:
            return snapshot
        if self.jobs is None:
            return snapshot
        try:
            job = self.jobs.get(snapshot.job_id)
        except JobProblem:
            return snapshot
        status_map: dict[str, VoiceGenerationStatus] = {
            "queued": "queued",
            "running": "running",
            "complete": "complete",
            "failed": "failed",
            "cancelled": "failed",
        }
        generation_status = status_map[job.status]
        status: ProjectVoiceSnapshotStatus = "failed" if job.status in {"failed", "cancelled"} else snapshot.status
        audio_url = snapshot.audio_url
        if job.output_url and not (audio_url or "").startswith("/media/outputs/platform/"):
            try:
                audio_url = self._materialize_snapshot_audio(snapshot.project_id, snapshot.snapshot_id, job.output_url)
            except PlatformProblem:
                audio_url = job.output_url
        updated = snapshot.model_copy(
            update={
                "status": status,
                "generation_status": generation_status,
                "audio_url": audio_url,
                "updated_at": _now(),
            }
        )
        if updated != snapshot:
            self._write_snapshot(updated)
        return updated

    def _write_snapshot(self, snapshot: ProjectVoiceSnapshot) -> None:
        self._write_json(self._snapshot_dir(snapshot.project_id) / f"{snapshot.snapshot_id}.json", snapshot.model_dump(mode="json"))

    def _materialize_snapshot_audio(self, project_id: str, snapshot_id: str, media_url: str) -> str:
        parsed = urlsplit(media_url)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise PlatformProblem(400, "项目声音只能引用当前工作区媒体")
        decoded = unquote(parsed.path)
        roots = {
            "/media/voice-samples/": self.workspace_root / "assets" / "voice_samples",
            "/media/outputs/audio/": self.workspace_root / "outputs" / "audio",
            "/media/outputs/voice-catalog/": self.workspace_root / "outputs" / "voice_catalog",
            "/media/outputs/platform/": self.workspace_root / "outputs" / "platform",
        }
        source: Path | None = None
        for prefix, root in roots.items():
            if not decoded.startswith(prefix):
                continue
            relative = PurePosixPath(decoded[len(prefix) :])
            if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
                break
            candidate = root.joinpath(*relative.parts).resolve()
            if candidate.is_relative_to(root.resolve()) and candidate.is_file():
                source = candidate
            break
        if source is None:
            raise PlatformProblem(400, "项目声音媒体路径无效或文件不存在")
        target = self.project_root / project_id / "voice" / "audio" / f"{snapshot_id}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".wav.tmp")
        shutil.copy2(source, temporary)
        temporary.replace(target)
        return f"/media/outputs/platform/projects/{project_id}/voice/audio/{snapshot_id}.wav"

    def _read_snapshot(self, path: Path) -> ProjectVoiceSnapshot:
        return ProjectVoiceSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

    def _write_decision(self, decision: ProjectVoiceDecision) -> None:
        self._write_json(self._decision_dir(decision.project_id) / f"{decision.decision_id}.json", decision.model_dump(mode="json"))

    @staticmethod
    def _read_decision(path: Path) -> ProjectVoiceDecision:
        return ProjectVoiceDecision.model_validate_json(path.read_text(encoding="utf-8"))

    def _find_project(self, project_id: str) -> PlatformProject:
        own_path = self.project_root / project_id / "project.json"
        if own_path.is_file():
            return self._read_project(own_path)
        for source in self.preparation.list_sources():
            if source.project_id == project_id:
                return self._source_project(source)
        raise PlatformProblem(404, "音声项目不存在")

    def _source_project(self, source: SourceSummary) -> PlatformProject:
        timestamp = datetime.now(timezone.utc)
        status: PlatformProjectStatus = "complete" if source.status == "director_ready" else "ready"
        return PlatformProject(
            project_id=source.project_id,
            name=source.display_name,
            content_type="novel",
            status=status,
            source_text_length=max(0, source.size_bytes),
            task_count=0,
            created_at=timestamp,
            updated_at=timestamp,
        )

    @staticmethod
    def _analyze_segments(text: str) -> list[TextSegment]:
        return analyze_text_segments(text)

    @staticmethod
    def _segment(index: int, text: str) -> TextSegment:
        quoted = any(mark in text for mark in ("“", "「", "‘", "’", '"', "'"))
        speaker_match = re.search(
            # Keep the name group non-greedy so the trailing "说/问/道" in
            # an attribution verb is not swallowed as part of the name.
            r"([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·]{0,11}?)(?:说道|说着|问道|喊道|答道|笑道|道|说|问|回答)",
            text,
        )
        if speaker_match and quoted:
            speaker = SpeakerAssignment(
                speaker_kind="named",
                speaker_name=speaker_match.group(1),
                confidence=0.76,
                voice_status="unmatched",
            )
        elif quoted:
            speaker = SpeakerAssignment(
                speaker_kind="unknown",
                speaker_name="未指定说话人",
                confidence=0.42,
                voice_status="archetype_available",
            )
        else:
            speaker = SpeakerAssignment(
                speaker_kind="narration",
                speaker_name="旁白",
                confidence=0.92,
                voice_status="archetype_available",
            )
        emotion, confidence = PlatformService._emotion(text)
        return TextSegment(
            segment_id=f"segment-{index + 1:04d}",
            index=index,
            text=text,
            speaker=speaker,
            emotion=emotion,
            emotion_confidence=confidence,
        )

    @staticmethod
    def _emotion(text: str) -> tuple[str, float]:
        for emotion, keywords in (
            ("愤怒", ("怒", "吼", "滚", "该死")),
            ("紧张", ("颤", "慌", "急", "危险")),
            ("悲伤", ("哭", "泪", "悲", "痛苦")),
            ("喜悦", ("笑", "开心", "高兴", "哈哈")),
        ):
            if any(keyword in text for keyword in keywords):
                return emotion, 0.68
        return "自然", 0.55

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _read_project(path: Path) -> PlatformProject:
        return PlatformProject.model_validate_json(path.read_text(encoding="utf-8"))

    @staticmethod
    def _read_task(path: Path) -> VoiceTask:
        return VoiceTask.model_validate_json(path.read_text(encoding="utf-8"))


def analyze_text_segments(text: str, speaker_names: list[str] | None = None) -> list[TextSegment]:
    """Analyze plain text through the same contract used by quick previews.

    Workflow nodes use this public seam instead of reaching into the page
    service's private implementation. Keeping the parser here means a quick
    preview and a reusable workflow produce identical segments.
    """
    if speaker_names:
        return _analyze_named_dialogue(text, speaker_names)
    raw_segments: list[str] = []
    for item in re.split(r"(?<=[。！？!?；;\n])", text):
        item = item.strip()
        if not item:
            continue
        # A standalone closing quote is not a speakable TTS segment.
        if not re.search(r"[\w\u4e00-\u9fff]", item) and raw_segments:
            raw_segments[-1] += item
        else:
            raw_segments.append(item)
    if not raw_segments and text.strip():
        raw_segments = [text.strip()]
    return [PlatformService._segment(index, item) for index, item in enumerate(raw_segments)]


def _analyze_named_dialogue(text: str, speaker_names: list[str]) -> list[TextSegment]:
    """Separate novel narration from quoted speech using explicit name hints.

    Hints constrain attribution to real character names; they do not assign
    unmarked dialogue. Ambiguous dialogue remains visible as unknown.
    """
    names = sorted({name.strip() for name in speaker_names if name.strip()}, key=lambda name: (-len(name), name))
    quotes = re.compile(r'“([^”]+)”|「([^」]+)」|『([^』]+)』|"([^"\n]+)"|‘([^’]+)’')
    speech_verb = r"(?:说道|问道|喊道|答道|笑道|低声|轻声|冷声|沉声|嘀咕|喃喃|喝道|开口|道|说|问|回答)"
    result: list[TextSegment] = []

    def attribution(context: str, prefer_last: bool) -> str | None:
        matches: list[tuple[int, str]] = []
        for name in names:
            for match in re.finditer(rf"{re.escape(name)}[^。！？!?；;“”\n]{{0,16}}?{speech_verb}", context):
                matches.append((match.end() if prefer_last else -match.start(), name))
        return max(matches)[1] if matches else None

    def append(content: str, kind: SpeakerKind, name: str) -> None:
        for sentence in re.findall(r"[^。！？!?；;\n]+[。！？!?；;]?", content):
            sentence = sentence.strip()
            if not re.search(r"[\w\u4e00-\u9fff]", sentence):
                continue
            segment = PlatformService._segment(len(result), sentence)
            segment.speaker = SpeakerAssignment(
                speaker_kind=kind, speaker_name=name,
                confidence=0.86 if kind == "named" else 0.92 if kind == "narration" else 0.42,
                voice_status="unmatched" if kind == "named" else "archetype_available",
            )
            result.append(segment)

    previous_line = ""
    for line in text.splitlines():
        cursor = 0
        last_speaker: str | None = None
        matches = list(quotes.finditer(line))
        for index, match in enumerate(matches):
            before = line[cursor:match.start()]
            append(before, "narration", "旁白")
            next_start = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            after = line[match.end():next_start]
            speaker = attribution(before[-160:], True) or attribution(after[:160], False)
            if speaker is None and not before.strip():
                speaker = last_speaker or (attribution(previous_line[-160:], True) if not quotes.search(previous_line) else None)
            content = next(value for value in match.groups() if value is not None)
            append(content, "named" if speaker else "unknown", speaker or "未指定说话人")
            last_speaker = speaker
            cursor = match.end()
        append(line[cursor:], "narration", "旁白")
        if line.strip():
            previous_line = line
    return result


class PlatformProblem(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def create_platform_router(service: PlatformService) -> APIRouter:
    router = APIRouter(prefix="/api/platform", tags=["voice-platform"])

    def handle(problem: PlatformProblem) -> HTTPException:
        return HTTPException(status_code=problem.status_code, detail=problem.detail)

    @router.get("/projects", response_model=list[PlatformProject])
    def list_projects() -> list[PlatformProject]:
        try:
            return service.list_projects()
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=500, detail=f"读取音声项目失败：{error}") from error

    @router.post("/projects", response_model=PlatformProject, status_code=201)
    def create_project(request: CreatePlatformProjectRequest) -> PlatformProject:
        try:
            return service.create_project(request)
        except PlatformProblem as problem:
            raise handle(problem) from problem

    @router.get("/projects/{project_id}/workspace", response_model=PlatformProjectWorkspace)
    def project_workspace(project_id: str) -> PlatformProjectWorkspace:
        try:
            return service.workspace(project_id)
        except PlatformProblem as problem:
            raise handle(problem) from problem

    @router.get("/projects/{project_id}/voice-snapshots", response_model=list[ProjectVoiceSnapshot])
    def list_voice_snapshots(project_id: str) -> list[ProjectVoiceSnapshot]:
        try:
            return service.list_voice_snapshots(project_id)
        except PlatformProblem as problem:
            raise handle(problem) from problem

    @router.post("/projects/{project_id}/voice-snapshots", response_model=ProjectVoiceSnapshot, status_code=201)
    def accept_voice_snapshot(project_id: str, request: ProjectVoiceSnapshotRequest) -> ProjectVoiceSnapshot:
        try:
            return service.accept_voice_snapshot(project_id, request)
        except PlatformProblem as problem:
            raise handle(problem) from problem

    @router.get("/projects/{project_id}/voice-decisions", response_model=list[ProjectVoiceDecision])
    def list_voice_decisions(project_id: str) -> list[ProjectVoiceDecision]:
        try:
            return service.list_voice_decisions(project_id)
        except PlatformProblem as problem:
            raise handle(problem) from problem

    @router.get("/projects/{project_id}/voice-decisions/{decision_id}", response_model=ProjectVoiceDecision)
    def get_voice_decision(project_id: str, decision_id: str) -> ProjectVoiceDecision:
        try:
            return service.get_voice_decision(project_id, decision_id)
        except PlatformProblem as problem:
            raise handle(problem) from problem

    @router.post("/projects/{project_id}/voice-decisions/match-or-generate", response_model=ProjectVoiceDecision, status_code=201)
    def match_or_generate(project_id: str, request: MatchOrGenerateRequest) -> ProjectVoiceDecision:
        try:
            return service.match_or_generate(project_id, request)
        except PlatformProblem as problem:
            raise handle(problem) from problem

    @router.post("/projects/{project_id}/voice-decisions/{decision_id}/generate", response_model=ProjectVoiceDecision, status_code=202)
    def generate_from_decision(project_id: str, decision_id: str) -> ProjectVoiceDecision:
        try:
            return service.generate_from_decision(project_id, decision_id)
        except PlatformProblem as problem:
            raise handle(problem) from problem

    @router.post("/projects/{project_id}/tasks/quick-preview", response_model=VoiceTask, status_code=201)
    def quick_preview(project_id: str) -> VoiceTask:
        try:
            return service.quick_preview(project_id)
        except PlatformProblem as problem:
            raise handle(problem) from problem

    return router
