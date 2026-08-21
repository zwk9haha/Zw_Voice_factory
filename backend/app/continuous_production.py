from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from .jobs import JobProblem, JobRequest, JobService
from .preparation import (
    EmotionPlanItem,
    PreparationPreview,
    PreparationProblem,
    PreparationService,
    ReferencePlanItem,
)
from .rvc import RvcBenchmarkRequest, RvcProblem, RvcService, RvcSettingsUpdate, RvcTrainingRequest


EmotionPolicy = Literal["skip", "background", "required_before_render"]
RVC_FOREGROUND_GRACE_SECONDS = 30
RVC_AUTOMATIC_TRAINING_LIMIT = 8
RvcStabilityPolicy = Literal["skip", "prepare_candidates"]
RvcPreparationStatus = Literal[
    "waiting_reference",
    "reused",
    "queued",
    "building_material",
    "training",
    "benchmarking",
    "awaiting_review",
    "approved",
    "deferred",
    "skipped",
    "rejected",
    "failed",
]
ContinuousRunState = Literal[
    "starting",
    "running",
    "pausing",
    "paused",
    "render_ready",
    "complete",
    "failed",
    "cancelled",
]
ProductionSliceStatus = Literal[
    "pending",
    "analyzing",
    "casting",
    "references",
    "emotions",
    "directing",
    "render_ready",
    "rendering",
    "playing",
    "complete",
    "blocked",
    "failed",
    "skipped",
]
ContinuousStage = Literal["analysis", "casting", "references", "emotions", "director", "quality_render"]
EventKind = Literal[
    "run_state_changed",
    "slice_state_changed",
    "stage_progress",
    "artifact_reused",
    "fallback_applied",
    "slice_render_ready",
    "run_attention_required",
    "rvc_state_changed",
]


class ContinuousProductionSettings(BaseModel):
    emotion_policy: EmotionPolicy = "background"
    rvc_stability_policy: RvcStabilityPolicy = "skip"
    prefetch_slices: int = Field(default=1, ge=1, le=2)
    auto_play: bool = False


class ContinuousProductionSettingsUpdate(BaseModel):
    emotion_policy: EmotionPolicy | None = None
    rvc_stability_policy: RvcStabilityPolicy | None = None
    prefetch_slices: int | None = Field(default=None, ge=1, le=2)
    auto_play: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "ContinuousProductionSettingsUpdate":
        if not self.model_fields_set:
            raise ValueError("至少需要修改一项连续生产设置")
        return self


class ProductionFallback(BaseModel):
    fallback_id: str
    slice_id: str
    stage: ContinuousStage
    target_asset_id: str
    actual_asset_id: str | None = None
    reason: str
    rerender_required: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProductionSliceState(BaseModel):
    slice_id: str
    slice_revision_id: str
    index: int = Field(ge=1)
    title: str
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    character_count: int = Field(ge=0)
    chapter_start: int | None = Field(default=None, ge=1)
    chapter_end: int | None = Field(default=None, ge=1)
    candidate_count: int = Field(default=0, ge=0)
    new_character_count: int = Field(default=0, ge=0)
    reused_character_count: int = Field(default=0, ge=0)
    director_completed_passages: int = Field(default=0, ge=0)
    director_total_passages: int = Field(default=0, ge=0)
    content_fingerprint: str
    state: ProductionSliceStatus = "pending"
    current_stage: ContinuousStage = "analysis"
    progress: int = Field(default=0, ge=0, le=100)
    message: str = "等待分析"
    segment_count: int = Field(default=0, ge=0)
    completed_segment_count: int = Field(default=0, ge=0)
    provisional_reference_ids: list[str] = Field(default_factory=list)
    fallbacks: list[ProductionFallback] = Field(default_factory=list)
    error: str | None = None
    render_ready_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RvcPreparationTask(BaseModel):
    character_id: str
    reference_id: str
    display_name: str
    priority: float = Field(default=0.0, ge=0)
    status: RvcPreparationStatus = "waiting_reference"
    progress: int = Field(default=0, ge=0, le=100)
    message: str = "等待已接受的标准参考"
    canonical_audio_version_id: str | None = None
    training_job_id: str | None = None
    model_id: str | None = None
    benchmark_id: str | None = None
    retry_count: int = Field(default=0, ge=0, le=1)
    benchmark_retry_count: int = Field(default=0, ge=0, le=1)
    eligible_after: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(seconds=RVC_FOREGROUND_GRACE_SECONDS)
    )
    error: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ContinuousProductionEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: EventKind
    message: str
    slice_id: str | None = None
    stage: ContinuousStage | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ContinuousProductionRun(BaseModel):
    schema_version: int = 1
    run_id: str
    project_id: str
    source_fingerprint: str
    state: ContinuousRunState = "starting"
    resume_state: ContinuousRunState | None = None
    settings: ContinuousProductionSettings
    slices: list[ProductionSliceState] = Field(default_factory=list)
    current_slice_id: str | None = None
    current_stage: ContinuousStage = "analysis"
    progress: int = Field(default=0, ge=0, le=100)
    message: str = "正在创建连续生产任务"
    failed_count: int = Field(default=0, ge=0)
    rvc_tasks: list[RvcPreparationTask] = Field(default_factory=list)
    rvc_progress: int = Field(default=0, ge=0, le=100)
    events: list[ContinuousProductionEvent] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    elapsed_seconds: float = Field(default=0.0, ge=0)


class ContinuousProductionProblem(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _RunInterrupted(Exception):
    pass


class ContinuousProductionService:
    def __init__(
        self,
        workspace_root: Path,
        preparation: PreparationService,
        jobs: JobService,
        rvc: RvcService,
        runtime_logger: logging.Logger | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.preparation = preparation
        self.jobs = jobs
        self.rvc = rvc
        self.runtime_logger = runtime_logger or logging.getLogger("zw_voice_factory")
        self._lock = threading.RLock()
        self._rvc_sync_lock = threading.Lock()
        self._runs: dict[str, ContinuousProductionRun] = {}
        self._futures: dict[str, Future[None]] = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="zw-continuous-production")
        self._load_runs()

    def close(self) -> None:
        with self._lock:
            for run in self._runs.values():
                if run.state in {"starting", "running", "pausing"}:
                    run.resume_state = "running"
                    run.state = "paused"
                    run.message = "启动器已关闭，可在下次启动后继续"
                    self._save_locked(run)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def start(self, project_id: str, settings: ContinuousProductionSettings) -> ContinuousProductionRun:
        preview = self._preview(project_id)
        fingerprint = self._source_fingerprint(preview)
        with self._lock:
            existing = self._runs.get(project_id)
            if (
                existing is not None
                and existing.source_fingerprint == fingerprint
                and existing.state != "cancelled"
                and self._can_reuse_existing_run(project_id, existing, preview)
            ):
                if existing.settings != settings:
                    existing.settings = settings
                    existing.updated_at = datetime.now(timezone.utc)
                    self._save_locked(existing)
                if existing.state in {"failed", "paused"}:
                    existing.resume_state = None
                    existing.state = "running"
                    existing.message = "正在继续连续生产"
                    self._append_event(existing, "run_state_changed", existing.message)
                    self._save_locked(existing)
                    self._schedule_locked(existing)
                elif existing.state == "complete" and settings.rvc_stability_policy == "prepare_candidates":
                    self._schedule_locked(existing)
                return self._view_locked(existing)
            run = ContinuousProductionRun(
                run_id=f"continuous-{uuid.uuid4().hex[:12]}",
                project_id=project_id,
                source_fingerprint=fingerprint,
                state="starting",
                settings=settings,
                message="正在分析项目并建立生产切片",
            )
            run.slices = [self._placeholder_slice(run)]
            run.current_slice_id = run.slices[0].slice_id
            self._append_event(run, "run_state_changed", run.message)
            self._runs[project_id] = run
            self._save_locked(run)
            self._schedule_locked(run)
            return self._view_locked(run)

    def get(self, project_id: str) -> ContinuousProductionRun:
        with self._lock:
            run = self._runs.get(project_id)
            if run is None:
                raise ContinuousProductionProblem(404, "该项目还没有连续生产任务")
        self._refresh_render_progress(project_id)
        self._refresh_rvc_preparation(project_id)
        with self._lock:
            run = self._runs[project_id]
            if self._needs_prefetch(run) or self._needs_background_emotion(project_id, run):
                self._schedule_locked(run)
            return self._view_locked(run)

    def pause(self, project_id: str) -> ContinuousProductionRun:
        with self._lock:
            run = self._require_run_locked(project_id)
            if run.state in {"complete", "failed", "cancelled", "paused"}:
                return self._view_locked(run)
            run.resume_state = run.state if run.state in {"render_ready", "running"} else "running"
            future = self._futures.get(project_id)
            run.state = "pausing" if future is not None and not future.done() else "paused"
            run.message = "正在完成当前原子任务后暂停" if run.state == "pausing" else "连续生产已暂停"
            self._append_event(run, "run_state_changed", run.message)
            self._save_locked(run)
            return self._view_locked(run)

    def resume(self, project_id: str) -> ContinuousProductionRun:
        with self._lock:
            run = self._require_run_locked(project_id)
            if run.state == "cancelled":
                raise ContinuousProductionProblem(409, "已取消的连续生产任务不能继续")
            if run.state == "complete":
                return self._view_locked(run)
            run.state = run.resume_state or ("render_ready" if self._has_render_ready(run) else "running")
            run.resume_state = None
            run.message = "正在继续连续生产"
            self._append_event(run, "run_state_changed", run.message)
            self._save_locked(run)
            self._schedule_locked(run)
            return self._view_locked(run)

    def retry(self, project_id: str) -> ContinuousProductionRun:
        with self._lock:
            run = self._require_run_locked(project_id)
            if run.state == "cancelled":
                raise ContinuousProductionProblem(409, "已取消的连续生产任务不能重试")
            for item in run.slices:
                if item.state in {"failed", "blocked"}:
                    item.state = "pending"
                    item.error = None
                    item.message = "等待重试"
            run.failed_count = 0
            run.state = "running"
            run.message = "正在重试失败阶段"
            self._append_event(run, "run_state_changed", run.message)
            self._save_locked(run)
            self._schedule_locked(run)
            return self._view_locked(run)

    def skip_problem_slice(self, project_id: str) -> ContinuousProductionRun:
        with self._lock:
            run = self._require_run_locked(project_id)
            item = next((candidate for candidate in run.slices if candidate.state in {"blocked", "failed"}), None)
            if item is None:
                raise ContinuousProductionProblem(409, "当前没有可跳过的问题切片")
            item.state = "skipped"
            item.message = "已由用户跳过"
            item.updated_at = datetime.now(timezone.utc)
            run.failed_count = sum(candidate.state in {"blocked", "failed"} for candidate in run.slices)
            run.state = "render_ready" if self._has_render_ready(run) else "running"
            run.message = f"已跳过 {item.title}"
            self._append_event(run, "slice_state_changed", run.message, slice_id=item.slice_id)
            self._save_locked(run)
            self._schedule_locked(run)
            return self._view_locked(run)

    def cancel(self, project_id: str) -> ContinuousProductionRun:
        with self._lock:
            run = self._require_run_locked(project_id)
            if run.state == "complete":
                return self._view_locked(run)
            run.state = "cancelled"
            run.resume_state = None
            run.message = "连续生产及后台预取已取消"
            run.completed_at = datetime.now(timezone.utc)
            self._append_event(run, "run_state_changed", run.message)
            self._save_locked(run)
            run_id = run.run_id
        self._stop_rvc_preparation(project_id, run_id)
        with self._lock:
            return self._view_locked(self._require_run_locked(project_id))

    def update_settings(
        self,
        project_id: str,
        update: ContinuousProductionSettingsUpdate,
    ) -> ContinuousProductionRun:
        with self._lock:
            run = self._require_run_locked(project_id)
            values = run.settings.model_dump()
            values.update(update.model_dump(exclude_none=True, exclude_unset=True))
            run.settings = ContinuousProductionSettings.model_validate(values)
            run.updated_at = datetime.now(timezone.utc)
            self._append_event(run, "run_state_changed", "连续生产设置已更新")
            self._save_locked(run)
        self._refresh_rvc_preparation(project_id)
        with self._lock:
            return self._view_locked(self._require_run_locked(project_id))

    def _schedule_locked(self, run: ContinuousProductionRun) -> None:
        current = self._futures.get(run.project_id)
        if current is not None and not current.done():
            return
        future = self._executor.submit(self._drive, run.project_id, run.run_id)
        self._futures[run.project_id] = future
        future.add_done_callback(lambda completed: self._forget_future(run.project_id, completed))

    def _can_reuse_existing_run(
        self,
        project_id: str,
        run: ContinuousProductionRun,
        preview: PreparationPreview,
    ) -> bool:
        if run.state not in {"render_ready", "complete"}:
            return True
        if (
            preview.analysis_audit is None
            or preview.character_voice_bible is None
            or preview.reference_plan is None
            or preview.director_doc is None
        ):
            return False
        if any(
            item.selected and not self._reference_resolves(item, preview.reference_plan.items)
            for item in preview.reference_plan.items
        ):
            return False
        if run.settings.emotion_policy == "required_before_render":
            if preview.emotion_plan is None or preview.emotion_plan.skipped:
                return False
            if any(
                item.selected
                and item.selection_mode != "base"
                and not item.audio_url
                and item.status != "failed"
                for item in preview.emotion_plan.items
            ):
                return False
        return self._director_window_ready(project_id, preview)

    def _drive(self, project_id: str, run_id: str) -> None:
        try:
            self._checkpoint(project_id, run_id)
            preview = self.preparation.preview(project_id)
            with self._lock:
                analysis_batch_limit = self._director_ready_limit(self._matching_run_locked(project_id, run_id))
            analysis_plan = preview.analysis_audit.long_form_plan if preview.analysis_audit else None
            analysis_window_ready = bool(
                analysis_plan
                and analysis_plan.batches
                and all(batch.state != "pending" for batch in analysis_plan.batches[:analysis_batch_limit])
            )
            if not analysis_window_ready:
                self._stage(
                    project_id,
                    run_id,
                    "analysis",
                    "analyzing",
                    8,
                    f"正在扫描前 {analysis_batch_limit} 个文本切片",
                )
                preview = self.preparation.prepare_analysis_window(project_id, analysis_batch_limit)
            else:
                self._reuse(project_id, run_id, "analysis", f"复用前 {analysis_batch_limit} 个切片的扫描结果")
            self._sync_slices(project_id, run_id, preview)

            self._checkpoint(project_id, run_id)
            preview = self.preparation.preview(project_id)
            with self._lock:
                run = self._matching_run_locked(project_id, run_id)
                profile_batch_limit = self._director_ready_limit(run)
            self._stage(
                project_id,
                run_id,
                "casting",
                "casting",
                28,
                (
                    "正在复用已有角色并分析当前切片新增角色"
                    if preview.character_voice_bible is not None
                    else "正在筛选首切片角色并建立项目声线圣经"
                ),
            )
            preview = self.preparation.prepare_character_window(project_id, profile_batch_limit)
            self._sync_slices(project_id, run_id, preview)

            self._checkpoint(project_id, run_id)
            self._stage(project_id, run_id, "references", "references", 48, "正在准备缺失的标准参考")
            preview = self._prepare_references(project_id, run_id)

            self._checkpoint(project_id, run_id)
            if self._settings(project_id).emotion_policy == "skip":
                self.preparation.update_emotion_settings(project_id, True, None, None)
                self._reuse(project_id, run_id, "emotions", "情绪派生已按策略跳过")
            elif self._settings(project_id).emotion_policy == "required_before_render":
                self.preparation.update_emotion_settings(project_id, False, None, None)
                self._stage(project_id, run_id, "emotions", "emotions", 62, "正在生成首轮必需情绪参考")
                preview = self._prepare_emotions(project_id, run_id, wait=True)
            else:
                self.preparation.update_emotion_settings(project_id, False, None, None)
                self._stage(project_id, run_id, "emotions", "emotions", 62, "情绪派生将在质量渲染期间后台补齐")

            self._checkpoint(project_id, run_id)
            preview = self.preparation.preview(project_id)
            if not self._director_window_ready(project_id, preview):
                self._stage(project_id, run_id, "director", "directing", 68, "正在生成导演文件并滚动释放生产切片")
                self._run_director_with_progress(project_id, run_id)
            else:
                self._reuse(project_id, run_id, "director", "复用现有 Director Document")
                self._sync_slices(project_id, run_id, preview)

            self._checkpoint(project_id, run_id)
            self._mark_available_slices_ready(project_id, run_id, self.preparation.preview(project_id))
            if self._settings(project_id).emotion_policy == "background":
                self._prepare_emotions(project_id, run_id, wait=False)
            self._finish_preparation(project_id, run_id)
            self._refresh_rvc_preparation(project_id)
        except _RunInterrupted:
            return
        except (PreparationProblem, JobProblem, ContinuousProductionProblem) as error:
            self._fail(project_id, run_id, str(error))
        except Exception as error:
            self._fail(project_id, run_id, f"连续生产执行异常：{error}")

    def _run_director_with_progress(self, project_id: str, run_id: str) -> None:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="zw-continuous-director")
        with self._lock:
            ready_batch_limit = self._director_ready_limit(self._matching_run_locked(project_id, run_id))
        future = executor.submit(self.preparation.prepare_director_window, project_id, ready_batch_limit)
        try:
            while not future.done():
                self._checkpoint(project_id, run_id)
                self._mark_available_slices_ready(project_id, run_id, self.preparation.preview(project_id))
                time.sleep(0.1)
            preview = future.result()
            self._mark_available_slices_ready(project_id, run_id, preview)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _prepare_references(self, project_id: str, run_id: str) -> PreparationPreview:
        preview = self.preparation.preview(project_id)
        plan = preview.reference_plan
        if plan is None:
            raise ContinuousProductionProblem(409, "角色参考计划不存在")
        generated_before = [item.reference_id for item in plan.items if item.audio_url]
        if generated_before:
            self._reuse(project_id, run_id, "references", f"复用 {len(generated_before)} 个已有标准参考")
        pending: list[str] = []
        for item in plan.items:
            if not item.selected or item.audio_url:
                continue
            existing = self._existing_job(item.job_id)
            if existing is not None and existing.status in {"queued", "running"}:
                pending.append(existing.job_id)
                continue
            if existing is not None and existing.status == "complete":
                continue
            record = self.jobs.submit(
                JobRequest(
                    kind="voxcpm_reference",
                    project_id=project_id,
                    reference_id=item.reference_id,
                    character_id=item.source_character_id,
                    text=item.reference_text,
                    voice_prompt=item.voice_prompt,
                )
            )
            pending.append(record.job_id)
        self._wait_jobs(project_id, run_id, pending)
        preview = self.preparation.preview(project_id)
        self._record_reference_fallbacks(project_id, run_id, preview)
        required = [item for item in preview.reference_plan.items if item.selected] if preview.reference_plan else []
        if required and not any(self._reference_resolves(item, preview.reference_plan.items) for item in required):
            raise ContinuousProductionProblem(409, "所有标准参考均生成失败，质量渲染没有可用声线")
        return preview

    def _prepare_emotions(self, project_id: str, run_id: str, *, wait: bool) -> PreparationPreview:
        preview = self.preparation.preview(project_id)
        plan = preview.emotion_plan
        if plan is None or plan.skipped:
            return preview
        pending: list[str] = []
        for item in plan.items:
            if item.selection_mode == "base" or not item.selected or item.audio_url:
                continue
            if not wait and self._quality_jobs_active(project_id):
                break
            existing = self._existing_job(item.job_id)
            if existing is not None and existing.status in {"queued", "running"}:
                if wait:
                    pending.append(existing.job_id)
                    continue
                return preview
            if existing is not None and existing.status == "complete":
                continue
            if item.status == "failed" or (existing is not None and existing.status == "failed"):
                self._fallback(
                    project_id,
                    run_id,
                    "emotions",
                    item.variant_id,
                    item.parent_reference_id,
                    item.error or existing.error or "情绪参考生成失败，暂用中性父参考",
                )
                continue
            record = self.jobs.submit(
                JobRequest(
                    kind="emotion_variant",
                    project_id=project_id,
                    variant_id=item.variant_id,
                    character_id=item.source_character_id,
                    text=item.reference_text,
                    voice_prompt=item.voice_prompt,
                )
            )
            pending.append(record.job_id)
            if not wait:
                break
        if wait:
            self._wait_jobs(project_id, run_id, pending)
            preview = self.preparation.preview(project_id)
            self._record_emotion_fallbacks(project_id, run_id, preview)
        return preview

    def _wait_jobs(self, project_id: str, run_id: str, job_ids: list[str]) -> None:
        remaining = set(job_ids)
        while remaining:
            self._checkpoint(project_id, run_id)
            for job_id in list(remaining):
                try:
                    record = self.jobs.get(job_id)
                except JobProblem:
                    remaining.remove(job_id)
                    continue
                if record.status in {"complete", "failed", "cancelled"}:
                    remaining.remove(job_id)
            if remaining:
                time.sleep(0.05)

    def _record_reference_fallbacks(
        self,
        project_id: str,
        run_id: str,
        preview: PreparationPreview,
    ) -> None:
        if preview.reference_plan is None:
            return
        items = preview.reference_plan.items
        by_id = {item.reference_id: item for item in items}
        narrators = [item for item in items if item.selection_mode == "narrator_default" and item.audio_url]
        for item in items:
            if not item.selected or item.audio_url or item.status != "failed":
                continue
            actual = by_id.get(item.reuse_reference_id or "")
            if actual is None or not actual.audio_url:
                actual = next((candidate for candidate in narrators if candidate.gender == item.gender), None)
            if actual is None and narrators:
                actual = narrators[0]
            self._fallback(
                project_id,
                run_id,
                "references",
                item.reference_id,
                actual.reference_id if actual else None,
                item.error or "标准参考生成失败，暂用可用旁白参考",
            )

    def _record_emotion_fallbacks(
        self,
        project_id: str,
        run_id: str,
        preview: PreparationPreview,
    ) -> None:
        if preview.emotion_plan is None:
            return
        for item in preview.emotion_plan.items:
            if item.selected and item.selection_mode != "base" and item.status == "failed":
                self._fallback(
                    project_id,
                    run_id,
                    "emotions",
                    item.variant_id,
                    item.parent_reference_id,
                    item.error or "情绪参考生成失败，暂用中性父参考",
                )

    def _fallback(
        self,
        project_id: str,
        run_id: str,
        stage: ContinuousStage,
        target_asset_id: str,
        actual_asset_id: str | None,
        reason: str,
    ) -> None:
        with self._lock:
            run = self._matching_run_locked(project_id, run_id)
            slice_item = run.slices[0]
            if any(item.stage == stage and item.target_asset_id == target_asset_id for item in slice_item.fallbacks):
                return
            fallback = ProductionFallback(
                fallback_id=f"fallback-{uuid.uuid4().hex[:10]}",
                slice_id=slice_item.slice_id,
                stage=stage,
                target_asset_id=target_asset_id,
                actual_asset_id=actual_asset_id,
                reason=reason,
            )
            slice_item.fallbacks.append(fallback)
            self._append_event(
                run,
                "fallback_applied",
                reason,
                slice_id=slice_item.slice_id,
                stage=stage,
            )
            self._save_locked(run)

    def _sync_slices(self, project_id: str, run_id: str, preview: PreparationPreview) -> None:
        plan = preview.analysis_audit.long_form_plan if preview.analysis_audit else None
        if plan is None:
            return
        workspace = self.preparation.revision_workspace(project_id)
        revision_id = workspace.active_revision_id or run_id
        with self._lock:
            run = self._matching_run_locked(project_id, run_id)
            existing = {item.content_fingerprint: item for item in run.slices}
            slices: list[ProductionSliceState] = []
            for batch in plan.batches:
                fingerprint = hashlib.sha256(
                    f"{run.source_fingerprint}:{plan.plan_id}:{batch.batch_id}:{batch.start_char}:{batch.end_char}".encode()
                ).hexdigest()
                item = existing.get(fingerprint) or ProductionSliceState(
                    slice_id=batch.batch_id,
                    slice_revision_id=f"{revision_id}:{batch.batch_id}:{fingerprint[:10]}",
                    index=batch.index,
                    title=batch.title,
                    start_char=batch.start_char,
                    end_char=batch.end_char,
                    character_count=batch.character_count,
                    chapter_start=batch.chapter_start,
                    chapter_end=batch.chapter_end,
                    candidate_count=len(batch.candidate_ids),
                    new_character_count=batch.new_character_count,
                    reused_character_count=batch.reused_character_count,
                    director_completed_passages=batch.director_completed_passages,
                    director_total_passages=batch.director_total_passages,
                    content_fingerprint=fingerprint,
                )
                item.candidate_count = len(batch.candidate_ids)
                item.new_character_count = batch.new_character_count
                item.reused_character_count = batch.reused_character_count
                item.director_completed_passages = batch.director_completed_passages
                item.director_total_passages = batch.director_total_passages
                if (
                    batch.state == "ready" or batch.director_completed_passages > 0
                ) and item.state in {"render_ready", "rendering", "playing", "complete"}:
                    item.error = None
                if item.state not in {"render_ready", "rendering", "playing", "complete", "skipped"}:
                    director_progress = (
                        round(batch.director_completed_passages * 17 / batch.director_total_passages)
                        if batch.director_total_passages
                        else 0
                    )
                    state_by_batch = {
                        "pending": ("pending", "analysis", 5, "等待分析"),
                        "analyzed": ("casting", "casting", 25, "文档分析完成，等待角色画像"),
                        "characters_ready": ("references", "references", 45, "角色画像完成，等待标准参考"),
                        "director_running": (
                            "directing",
                            "director",
                            75 + director_progress,
                            (
                                f"片内导演窗口 {batch.director_completed_passages}/{batch.director_total_passages}"
                                if batch.director_total_passages
                                else "正在生成切片导演文件"
                            ),
                        ),
                        "ready": ("directing", "director", 92, "导演切片已完成，正在检查准入条件"),
                        "failed": ("failed", "director", 75, "切片导演分析失败"),
                    }
                    state, stage, progress, message = state_by_batch.get(
                        batch.state,
                        ("pending", "analysis", 0, "等待分析"),
                    )
                    item.state = state  # type: ignore[assignment]
                    item.current_stage = stage  # type: ignore[assignment]
                    item.progress = progress
                    item.message = message
                item.updated_at = datetime.now(timezone.utc)
                slices.append(item)
            run.slices = slices
            run.failed_count = sum(item.state in {"failed", "blocked"} for item in slices)
            run.current_slice_id = next(
                (item.slice_id for item in slices if item.state not in {"complete", "skipped"}),
                slices[-1].slice_id if slices else None,
            )
            self._save_locked(run)

    def _mark_available_slices_ready(
        self,
        project_id: str,
        run_id: str,
        preview: PreparationPreview,
    ) -> None:
        self._sync_slices(project_id, run_id, preview)
        if preview.director_doc is None:
            return
        counts: dict[str, int] = {}
        for segment in preview.director_doc.segments:
            if segment.analysis_batch_id:
                counts[segment.analysis_batch_id] = counts.get(segment.analysis_batch_id, 0) + 1
        batches = (
            preview.analysis_audit.long_form_plan.batches
            if preview.analysis_audit and preview.analysis_audit.long_form_plan
            else []
        )
        available_batches = {
            batch.batch_id: batch
            for batch in batches
            if batch.state == "ready" or batch.director_completed_passages > 0
        }
        provisional_ids = [
            item.reference_id
            for item in (preview.reference_plan.items if preview.reference_plan else [])
            if item.selected and item.audio_source == "generated" and item.audio_url
        ]
        with self._lock:
            run = self._matching_run_locked(project_id, run_id)
            changed = False
            for item in run.slices:
                item.segment_count = counts.get(item.slice_id, 0)
                batch = available_batches.get(item.slice_id)
                if batch is None or item.segment_count == 0:
                    continue
                if item.state not in {"render_ready", "rendering", "playing", "complete"}:
                    item.state = "render_ready"
                    item.current_stage = "quality_render"
                    item.progress = 100
                    item.message = (
                        "切片已完成并进入质量渲染队列"
                        if batch.state == "ready"
                        else "首个片内导演窗口已可朗读，后台继续补齐本切片"
                    )
                    item.provisional_reference_ids = provisional_ids
                    item.render_ready_at = datetime.now(timezone.utc)
                    item.updated_at = item.render_ready_at
                    self._append_event(
                        run,
                        "slice_render_ready",
                        f"{item.title} 已释放首个可渲染窗口",
                        slice_id=item.slice_id,
                        stage="quality_render",
                        progress=100,
                    )
                    changed = True
            if changed or self._has_render_ready(run):
                run.state = "render_ready"
                run.current_stage = "quality_render"
                run.progress = max(run.progress, self._overall_progress(run))
                run.message = "首个切片已可渲染，后台继续准备后续切片"
                self._save_locked(run)

    def _refresh_render_progress(self, project_id: str) -> None:
        with self._lock:
            run = self._runs.get(project_id)
            if run is None or not any(item.state in {"render_ready", "rendering", "playing"} for item in run.slices):
                return
        try:
            preview = self.preparation.preview(project_id)
            records = self.jobs.list(5_000, project_id=project_id, kind="quality_render")
        except (PreparationProblem, JobProblem):
            return
        segment_batch = {
            segment.segment_id: segment.analysis_batch_id
            for segment in (preview.director_doc.segments if preview.director_doc else [])
        }
        latest: dict[str, object] = {}
        for record in records:
            if record.segment_id and record.segment_id not in latest:
                latest[record.segment_id] = record
        with self._lock:
            run = self._runs.get(project_id)
            if run is None:
                return
            for item in run.slices:
                segment_ids = [segment_id for segment_id, batch_id in segment_batch.items() if batch_id == item.slice_id]
                slice_jobs = [latest[segment_id] for segment_id in segment_ids if segment_id in latest]
                item.completed_segment_count = sum(getattr(record, "status") == "complete" for record in slice_jobs)
                if any(getattr(record, "status") in {"queued", "running"} for record in slice_jobs):
                    item.state = "rendering"
                    item.message = "质量渲染进行中"
                elif segment_ids and item.completed_segment_count == len(segment_ids):
                    item.state = "complete"
                    item.message = "切片音频已全部生成"
                item.updated_at = datetime.now(timezone.utc)
            if run.slices and all(item.state in {"complete", "skipped"} for item in run.slices):
                run.state = "complete"
                run.completed_at = datetime.now(timezone.utc)
                run.message = "全部生产切片已完成"
            run.progress = self._overall_progress(run)
            self._save_locked(run)

    def _finish_preparation(self, project_id: str, run_id: str) -> None:
        with self._lock:
            run = self._matching_run_locked(project_id, run_id)
            if self._has_render_ready(run):
                run.state = "render_ready"
                run.current_stage = "quality_render"
                run.message = "可用切片已加入质量渲染队列"
            elif run.slices and all(item.state == "skipped" for item in run.slices):
                run.state = "complete"
                run.completed_at = datetime.now(timezone.utc)
                run.message = "所有问题切片均已跳过"
            else:
                raise ContinuousProductionProblem(409, "导演文件已完成，但没有可渲染的生产切片")
            run.progress = self._overall_progress(run)
            self._append_event(run, "run_state_changed", run.message)
            self._save_locked(run)

    def _stage(
        self,
        project_id: str,
        run_id: str,
        stage: ContinuousStage,
        slice_state: ProductionSliceStatus,
        progress: int,
        message: str,
    ) -> None:
        with self._lock:
            run = self._matching_run_locked(project_id, run_id)
            if run.state not in {"render_ready"}:
                run.state = "running"
            run.current_stage = stage
            run.progress = max(run.progress, progress)
            run.message = message
            for item in self._active_window(run):
                if item.state not in {"render_ready", "rendering", "playing", "complete", "skipped"}:
                    item.state = slice_state
                    item.current_stage = stage
                    item.progress = progress
                    item.message = message
                    item.updated_at = datetime.now(timezone.utc)
            self._append_event(run, "stage_progress", message, stage=stage, progress=progress)
            self._save_locked(run)

    def _reuse(self, project_id: str, run_id: str, stage: ContinuousStage, message: str) -> None:
        with self._lock:
            run = self._matching_run_locked(project_id, run_id)
            self._append_event(run, "artifact_reused", message, stage=stage)
            run.message = message
            self._save_locked(run)

    def _checkpoint(self, project_id: str, run_id: str) -> None:
        while True:
            with self._lock:
                run = self._matching_run_locked(project_id, run_id)
                if run.state == "cancelled":
                    raise _RunInterrupted
                if run.state == "pausing":
                    run.state = "paused"
                    run.message = "连续生产已暂停"
                    self._append_event(run, "run_state_changed", run.message)
                    self._save_locked(run)
                if run.state != "paused":
                    return
            time.sleep(0.1)

    def _fail(self, project_id: str, run_id: str, detail: str) -> None:
        with self._lock:
            try:
                run = self._matching_run_locked(project_id, run_id)
            except ContinuousProductionProblem:
                return
            if run.state == "cancelled":
                return
            run.state = "failed"
            run.failed_count += 1
            run.message = detail
            item = next(
                (candidate for candidate in run.slices if candidate.state not in {"render_ready", "complete", "skipped"}),
                run.slices[0] if run.slices else None,
            )
            if item is not None:
                item.state = "failed"
                item.error = detail
                item.message = detail
                item.updated_at = datetime.now(timezone.utc)
            self._append_event(
                run,
                "run_attention_required",
                detail,
                slice_id=item.slice_id if item else None,
                stage=run.current_stage,
            )
            self._save_locked(run)
        self.runtime_logger.error("[CONTINUOUS %s] %s", project_id, detail)

    def _active_window(self, run: ContinuousProductionRun) -> list[ProductionSliceState]:
        current_index = next(
            (index for index, item in enumerate(run.slices) if item.state not in {"complete", "skipped"}),
            0,
        )
        return run.slices[current_index : current_index + run.settings.prefetch_slices + 1]

    def _quality_jobs_active(self, project_id: str) -> bool:
        return any(
            record.status in {"queued", "running"}
            for record in self.jobs.list(5_000, project_id=project_id, kind="quality_render")
        )

    def _refresh_rvc_preparation(self, project_id: str) -> None:
        if not self._rvc_sync_lock.acquire(blocking=False):
            return
        try:
            with self._lock:
                run = self._runs.get(project_id)
                if run is None:
                    return
                run_id = run.run_id
                policy = run.settings.rvc_stability_policy
                cancelled = run.state == "cancelled"
            if policy == "skip" or cancelled:
                self._stop_rvc_preparation(project_id, run_id)
                return
            try:
                preview = self.preparation.preview(project_id)
                workspace = self.rvc.workspace(project_id)
            except (PreparationProblem, RvcProblem):
                return
            self._sync_rvc_inventory(project_id, run_id, preview, workspace)
            with self._lock:
                current = self._runs.get(project_id)
                character_ids = [task.character_id for task in current.rvc_tasks] if current else []
            for character_id in character_ids:
                self._advance_rvc_task(project_id, run_id, character_id)
            self._update_rvc_progress(project_id, run_id)
        finally:
            self._rvc_sync_lock.release()

    def _sync_rvc_inventory(
        self,
        project_id: str,
        run_id: str,
        preview: PreparationPreview,
        workspace: object,
    ) -> None:
        plan = preview.reference_plan
        if plan is None:
            return
        bible_by_id = {
            character.character_id: character
            for character in (preview.character_voice_bible.characters if preview.character_voice_bible else [])
        }
        approved_ids = {
            character.character_id
            for character in getattr(workspace, "characters", [])
            if character.quality_approved
        }
        candidates: list[tuple[ReferencePlanItem, str, float, str | None, bool]] = []
        for reference in plan.items:
            if not reference.selected:
                continue
            narrator = reference.selection_mode == "narrator_default" or reference.source_character_id == "narrator"
            profile = bible_by_id.get(reference.source_character_id)
            tier = getattr(getattr(profile, "tier", None), "value", getattr(profile, "tier", None))
            if not narrator and tier not in {"core", "supporting"}:
                continue
            character_id = (
                f"narrator-{reference.gender}"
                if reference.source_character_id == "narrator"
                else reference.source_character_id
            )
            active_version = next(
                (
                    version
                    for version in reference.audio_versions
                    if version.version_id == reference.active_audio_version_id
                ),
                None,
            )
            accepted_version_id = (
                active_version.version_id
                if active_version is not None and active_version.decision == "accepted"
                else None
            )
            reused = bool(
                reference.reuse_reference_id
                or (active_version is not None and active_version.source == "reused")
            )
            priority = (10.0 if narrator else 0.0) + reference.importance
            candidates.append((reference, character_id, priority, accepted_version_id, reused))
        candidates.sort(key=lambda item: item[2], reverse=True)
        trainable_ids = [
            character_id
            for _, character_id, _, _, reused in candidates
            if not reused and character_id not in approved_ids
        ][:RVC_AUTOMATIC_TRAINING_LIMIT]
        now = datetime.now(timezone.utc)
        with self._lock:
            run = self._matching_run_locked(project_id, run_id)
            existing = {task.character_id: task for task in run.rvc_tasks}
            next_tasks: list[RvcPreparationTask] = []
            for reference, character_id, priority, accepted_version_id, reused in candidates:
                task = existing.get(character_id)
                if task is None:
                    task = RvcPreparationTask(
                        character_id=character_id,
                        reference_id=reference.reference_id,
                        display_name=reference.display_name,
                        priority=priority,
                        canonical_audio_version_id=accepted_version_id,
                    )
                canonical_changed = task.canonical_audio_version_id != accepted_version_id
                if canonical_changed:
                    task = task.model_copy(
                        update={
                            "status": "waiting_reference" if accepted_version_id is None else "queued",
                            "progress": 0,
                            "message": "等待已接受的标准参考" if accepted_version_id is None else "标准参考已确认，等待训练窗口",
                            "canonical_audio_version_id": accepted_version_id,
                            "training_job_id": None,
                            "model_id": None,
                            "benchmark_id": None,
                            "retry_count": 0,
                            "benchmark_retry_count": 0,
                            "eligible_after": now + timedelta(seconds=RVC_FOREGROUND_GRACE_SECONDS),
                            "error": None,
                            "updated_at": now,
                        }
                    )
                if reused:
                    task = task.model_copy(
                        update={
                            "status": "skipped",
                            "progress": 100,
                            "message": "复用声线不重复训练角色专属 RVC",
                            "error": None,
                            "updated_at": now,
                        }
                    )
                elif character_id not in approved_ids and character_id not in trainable_ids:
                    task = task.model_copy(
                        update={
                            "status": "deferred",
                            "progress": 100,
                            "message": "超过本次自动训练预算，可在 RVC 工作台手动加入",
                            "error": None,
                            "updated_at": now,
                        }
                    )
                elif accepted_version_id is None and task.status not in {"skipped", "deferred"}:
                    task = task.model_copy(
                        update={
                            "status": "waiting_reference",
                            "progress": 0,
                            "message": "等待用户接受标准参考音频",
                            "updated_at": now,
                        }
                    )
                next_tasks.append(task.model_copy(update={"priority": priority, "display_name": reference.display_name}))
            run.rvc_tasks = next_tasks
            self._save_locked(run)

    def _advance_rvc_task(self, project_id: str, run_id: str, character_id: str) -> None:
        with self._lock:
            run = self._matching_run_locked(project_id, run_id)
            task = next((item for item in run.rvc_tasks if item.character_id == character_id), None)
            if task is None or task.status in {"reused", "approved", "deferred", "skipped", "rejected"}:
                return
            snapshot = task.model_copy(deep=True)
        if snapshot.canonical_audio_version_id is None:
            return
        try:
            approved_model = self.rvc.current_approved_model(project_id, character_id, "quality")
            if approved_model is not None:
                self._enable_rvc_quality_model(project_id, character_id, approved_model.model_id)
                terminal_status: RvcPreparationStatus = "approved" if snapshot.training_job_id else "reused"
                self._set_rvc_task(
                    project_id,
                    run_id,
                    character_id,
                    status=terminal_status,
                    progress=100,
                    model_id=approved_model.model_id,
                    message="质量稳定层已批准并启用" if terminal_status == "approved" else "已复用批准有效的 RVC 稳定层",
                    error=None,
                )
                return

            if snapshot.training_job_id:
                job = self.rvc.get_job(snapshot.training_job_id)
                if job.status in {"queued", "running"}:
                    status_value: RvcPreparationStatus = (
                        "queued" if job.status == "queued" else "building_material" if job.progress < 30 else "training"
                    )
                    self._set_rvc_task(
                        project_id,
                        run_id,
                        character_id,
                        status=status_value,
                        progress=min(82, round(job.progress * 0.82)),
                        message=job.message,
                        error=None,
                    )
                    return
                if job.status == "failed":
                    if snapshot.retry_count < 1:
                        snapshot = self._set_rvc_task(
                            project_id,
                            run_id,
                            character_id,
                            status="queued",
                            progress=0,
                            training_job_id=None,
                            retry_count=snapshot.retry_count + 1,
                            message="训练失败，正在执行一次自动重试",
                            error=job.error,
                        )
                    else:
                        self._set_rvc_task(
                            project_id,
                            run_id,
                            character_id,
                            status="failed",
                            progress=100,
                            message="RVC 训练失败，质量渲染继续使用 Base Render",
                            error=job.error,
                        )
                        return
                elif job.status == "cancelled":
                    self._set_rvc_task(
                        project_id,
                        run_id,
                        character_id,
                        status="failed",
                        progress=100,
                        message="RVC 训练已取消",
                    )
                    return
                elif job.status == "complete" and job.model_id:
                    if snapshot.model_id != job.model_id or snapshot.benchmark_id is None:
                        snapshot = self._set_rvc_task(
                            project_id,
                            run_id,
                            character_id,
                            status="benchmarking",
                            progress=82,
                            model_id=job.model_id,
                            message="候选模型已生成，正在准备质量基准",
                            error=None,
                        )
                    else:
                        snapshot = snapshot.model_copy(update={"model_id": job.model_id})

            if snapshot.model_id:
                report = None
                if snapshot.benchmark_id:
                    try:
                        report = self.rvc.get_benchmark(snapshot.benchmark_id)
                    except RvcProblem:
                        report = None
                workspace = self.rvc.workspace(project_id)
                matching_reports = [
                    item
                    for item in workspace.benchmarks
                    if item.character_id == character_id
                    and item.model_id == snapshot.model_id
                    and item.route == "quality"
                ]
                latest_report = max(matching_reports, key=lambda item: item.created_at, default=None)
                if report is None or (
                    latest_report is not None
                    and report.status == "failed"
                    and latest_report.created_at > report.created_at
                ):
                    report = latest_report
                if report is not None:
                    if report.decision == "approved":
                        self._enable_rvc_quality_model(project_id, character_id, snapshot.model_id)
                        self._set_rvc_task(
                            project_id,
                            run_id,
                            character_id,
                            status="approved",
                            progress=100,
                            benchmark_id=report.benchmark_id,
                            message="质量稳定层已批准并启用",
                            error=None,
                        )
                        return
                    if report.decision == "rejected":
                        self._set_rvc_task(
                            project_id,
                            run_id,
                            character_id,
                            status="rejected",
                            progress=100,
                            benchmark_id=report.benchmark_id,
                            message="质量基准已拒绝，继续使用 Base Render",
                        )
                        return
                    if report.status in {"queued", "running"}:
                        self._set_rvc_task(
                            project_id,
                            run_id,
                            character_id,
                            status="benchmarking",
                            progress=82 + round(report.progress * 0.13),
                            benchmark_id=report.benchmark_id,
                            message=report.message,
                            error=None,
                        )
                        return
                    if report.status == "complete":
                        self._set_rvc_task(
                            project_id,
                            run_id,
                            character_id,
                            status="awaiting_review",
                            progress=95,
                            benchmark_id=report.benchmark_id,
                            message="24 句质量基准已完成，等待人工审核",
                            error=None,
                        )
                        return
                    if report.status == "failed" and snapshot.benchmark_retry_count >= 1:
                        self._set_rvc_task(
                            project_id,
                            run_id,
                            character_id,
                            status="failed",
                            progress=100,
                            message="RVC 基准失败，质量渲染继续使用 Base Render",
                            error=report.error,
                        )
                        return
                    if report.status == "failed":
                        snapshot = self._set_rvc_task(
                            project_id,
                            run_id,
                            character_id,
                            benchmark_id=None,
                            benchmark_retry_count=snapshot.benchmark_retry_count + 1,
                            message="基准失败，正在执行一次自动重试",
                            error=report.error,
                        )
                if self._quality_jobs_active(project_id):
                    return
                report = self.rvc.submit_benchmark(
                    project_id,
                    RvcBenchmarkRequest(character_id=character_id, model_id=snapshot.model_id, route="quality"),
                )
                self._set_rvc_task(
                    project_id,
                    run_id,
                    character_id,
                    status="benchmarking",
                    progress=82,
                    benchmark_id=report.benchmark_id,
                    message="已进入 24 句质量基准队列",
                    error=None,
                )
                return

            if self._quality_jobs_active(project_id) or datetime.now(timezone.utc) < snapshot.eligible_after:
                self._set_rvc_task(
                    project_id,
                    run_id,
                    character_id,
                    status="queued",
                    progress=0,
                    message="等待质量渲染空闲窗口",
                )
                return
            job = self.rvc.submit(
                project_id,
                RvcTrainingRequest(character_id=character_id, purpose="quality_stability"),
            )
            self._set_rvc_task(
                project_id,
                run_id,
                character_id,
                status="queued",
                progress=0,
                training_job_id=job.job_id,
                message="已进入 RVC 稳定层训练队列",
                error=None,
            )
        except RvcProblem as error:
            self._set_rvc_task(
                project_id,
                run_id,
                character_id,
                status="failed",
                progress=100,
                message="RVC 准备失败，质量渲染继续使用 Base Render",
                error=str(error),
            )

    def _enable_rvc_quality_model(self, project_id: str, character_id: str, model_id: str) -> None:
        workspace = self.rvc.update_settings(
            project_id,
            RvcSettingsUpdate(
                character_id=character_id,
                train_enabled=True,
                selected_model_id=model_id,
                stability_enabled=True,
            ),
        )
        if not workspace.settings.quality_stability_enabled:
            self.rvc.update_settings(project_id, RvcSettingsUpdate(quality_stability_enabled=True))

    def _set_rvc_task(
        self,
        project_id: str,
        run_id: str,
        character_id: str,
        **changes: object,
    ) -> RvcPreparationTask:
        with self._lock:
            run = self._matching_run_locked(project_id, run_id)
            task = next(item for item in run.rvc_tasks if item.character_id == character_id)
            previous_status = task.status
            updated = task.model_copy(update={**changes, "updated_at": datetime.now(timezone.utc)})
            run.rvc_tasks = [updated if item.character_id == character_id else item for item in run.rvc_tasks]
            if updated.status != previous_status:
                self._append_event(
                    run,
                    "rvc_state_changed",
                    f"{updated.display_name}：{updated.message}",
                    stage="quality_render",
                    progress=updated.progress,
                )
            self._save_locked(run)
            return updated.model_copy(deep=True)

    def _update_rvc_progress(self, project_id: str, run_id: str) -> None:
        with self._lock:
            run = self._matching_run_locked(project_id, run_id)
            run.rvc_progress = (
                round(sum(task.progress for task in run.rvc_tasks) / len(run.rvc_tasks))
                if run.rvc_tasks
                else 0
            )
            self._save_locked(run)

    def _stop_rvc_preparation(self, project_id: str, run_id: str) -> None:
        with self._lock:
            run = self._matching_run_locked(project_id, run_id)
            active_jobs = [
                task.training_job_id
                for task in run.rvc_tasks
                if task.training_job_id and task.status in {"queued", "building_material", "training"}
            ]
        for job_id in active_jobs:
            try:
                self.rvc.cancel(job_id)
            except RvcProblem:
                continue
        with self._lock:
            run = self._matching_run_locked(project_id, run_id)
            if run.settings.rvc_stability_policy == "skip":
                run.rvc_tasks = []
                run.rvc_progress = 0
                self._save_locked(run)
            elif run.state == "cancelled":
                run.rvc_tasks = [
                    task
                    if task.status in {"reused", "approved", "deferred", "skipped", "rejected", "failed"}
                    else task.model_copy(
                        update={
                            "status": "skipped",
                            "progress": 100,
                            "message": "连续生产已取消",
                            "updated_at": datetime.now(timezone.utc),
                        }
                    )
                    for task in run.rvc_tasks
                ]
                run.rvc_progress = (
                    round(sum(task.progress for task in run.rvc_tasks) / len(run.rvc_tasks))
                    if run.rvc_tasks
                    else 0
                )
                self._save_locked(run)

    def _existing_job(self, job_id: str | None):
        if not job_id:
            return None
        try:
            return self.jobs.get(job_id)
        except JobProblem:
            return None

    @staticmethod
    def _reference_resolves(item: ReferencePlanItem, items: list[ReferencePlanItem]) -> bool:
        by_id = {candidate.reference_id: candidate for candidate in items}
        visited: set[str] = set()
        current: ReferencePlanItem | None = item
        while current is not None and current.reference_id not in visited:
            if current.audio_url:
                return True
            visited.add(current.reference_id)
            current = by_id.get(current.reuse_reference_id or "")
        return False

    @staticmethod
    def _all_director_batches_ready(preview: PreparationPreview) -> bool:
        plan = preview.analysis_audit.long_form_plan if preview.analysis_audit else None
        return bool(plan and plan.batches and all(batch.state == "ready" for batch in plan.batches))

    def _director_window_ready(self, project_id: str, preview: PreparationPreview) -> bool:
        plan = preview.analysis_audit.long_form_plan if preview.analysis_audit else None
        if plan is None or not plan.batches or preview.director_doc is None:
            return False
        with self._lock:
            limit = self._director_ready_limit(self._require_run_locked(project_id))
        return all(batch.state == "ready" for batch in plan.batches[:limit])

    @staticmethod
    def _director_ready_limit(run: ContinuousProductionRun) -> int:
        if not ContinuousProductionService._has_render_ready(run):
            return min(len(run.slices), 1)
        current_index = next(
            (index for index, item in enumerate(run.slices) if item.state not in {"complete", "skipped"}),
            max(len(run.slices) - 1, 0),
        )
        return min(len(run.slices), current_index + run.settings.prefetch_slices + 1)

    def _needs_prefetch(self, run: ContinuousProductionRun) -> bool:
        if run.state not in {"render_ready", "complete"} or not run.slices:
            return False
        if all(item.state in {"complete", "skipped"} for item in run.slices):
            return False
        ready_limit = self._director_ready_limit(run)
        return any(
            item.state not in {"render_ready", "rendering", "playing", "complete", "skipped"}
            for item in run.slices[:ready_limit]
        )

    def _needs_background_emotion(self, project_id: str, run: ContinuousProductionRun) -> bool:
        if run.settings.emotion_policy != "background" or run.state not in {"render_ready", "complete"}:
            return False
        if self._quality_jobs_active(project_id):
            return False
        preview = self.preparation.preview(project_id)
        plan = preview.emotion_plan
        if plan is None or plan.skipped:
            return False
        for item in plan.items:
            if item.selection_mode == "base" or not item.selected or item.audio_url or item.status == "failed":
                continue
            existing = self._existing_job(item.job_id)
            if existing is not None and existing.status in {"queued", "running"}:
                return False
            return True
        return False

    def _settings(self, project_id: str) -> ContinuousProductionSettings:
        with self._lock:
            return self._require_run_locked(project_id).settings.model_copy(deep=True)

    def _preview(self, project_id: str) -> PreparationPreview:
        try:
            return self.preparation.preview(project_id)
        except PreparationProblem as error:
            raise ContinuousProductionProblem(error.status_code, error.detail) from error

    @staticmethod
    def _source_fingerprint(preview: PreparationPreview) -> str:
        return hashlib.sha256(
            f"{preview.source.file_name}:{preview.source.size_bytes}:{preview.source.encoding}".encode()
        ).hexdigest()

    @staticmethod
    def _placeholder_slice(run: ContinuousProductionRun) -> ProductionSliceState:
        return ProductionSliceState(
            slice_id="pending-plan",
            slice_revision_id=f"{run.run_id}:pending-plan",
            index=1,
            title="正在建立切片计划",
            start_char=0,
            end_char=0,
            character_count=0,
            content_fingerprint=f"{run.source_fingerprint}:pending",
            state="analyzing",
            progress=2,
        )

    @staticmethod
    def _has_render_ready(run: ContinuousProductionRun) -> bool:
        return any(item.state in {"render_ready", "rendering", "playing", "complete"} for item in run.slices)

    @staticmethod
    def _overall_progress(run: ContinuousProductionRun) -> int:
        if not run.slices:
            return run.progress
        return round(sum(item.progress for item in run.slices) / len(run.slices))

    def _matching_run_locked(self, project_id: str, run_id: str) -> ContinuousProductionRun:
        run = self._require_run_locked(project_id)
        if run.run_id != run_id:
            raise _RunInterrupted
        return run

    def _require_run_locked(self, project_id: str) -> ContinuousProductionRun:
        run = self._runs.get(project_id)
        if run is None:
            raise ContinuousProductionProblem(404, "该项目还没有连续生产任务")
        return run

    def _append_event(
        self,
        run: ContinuousProductionRun,
        kind: EventKind,
        message: str,
        *,
        slice_id: str | None = None,
        stage: ContinuousStage | None = None,
        progress: int | None = None,
    ) -> None:
        run.events.append(
            ContinuousProductionEvent(
                kind=kind,
                message=message,
                slice_id=slice_id,
                stage=stage,
                progress=progress,
            )
        )
        run.events = run.events[-100:]

    def _view_locked(self, run: ContinuousProductionRun) -> ContinuousProductionRun:
        view = run.model_copy(deep=True)
        end = view.completed_at or datetime.now(timezone.utc)
        view.elapsed_seconds = max(view.elapsed_seconds, (end - view.started_at).total_seconds())
        return view

    def _path(self, project_id: str) -> Path:
        return self.workspace_root / "outputs" / "projects" / project_id / "continuous_production.json"

    def _save_locked(self, run: ContinuousProductionRun) -> None:
        run.updated_at = datetime.now(timezone.utc)
        target = self._path(run.project_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(target)

    def _load_runs(self) -> None:
        root = self.workspace_root / "outputs" / "projects"
        if not root.is_dir():
            return
        for path in root.glob("*/continuous_production.json"):
            try:
                run = ContinuousProductionRun.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if run.state in {"starting", "running", "pausing"}:
                run.resume_state = "running"
                run.state = "paused"
                run.message = "服务重启前任务未完成，可继续复用已有阶段"
                self._save_locked(run)
            self._runs[run.project_id] = run

    def _forget_future(self, project_id: str, completed: Future[None]) -> None:
        with self._lock:
            if self._futures.get(project_id) is completed:
                self._futures.pop(project_id, None)
                run = self._runs.get(project_id)
                if run is not None and self._needs_prefetch(run):
                    self._schedule_locked(run)


def create_continuous_production_router(service: ContinuousProductionService) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/continuous-production", tags=["continuous-production"])

    def handle(error: ContinuousProductionProblem) -> HTTPException:
        return HTTPException(status_code=error.status_code, detail=error.detail)

    @router.post("", response_model=ContinuousProductionRun, status_code=status.HTTP_202_ACCEPTED)
    def start(project_id: str, settings: ContinuousProductionSettings) -> ContinuousProductionRun:
        try:
            return service.start(project_id, settings)
        except ContinuousProductionProblem as error:
            raise handle(error) from error

    @router.get("", response_model=ContinuousProductionRun)
    def get(project_id: str) -> ContinuousProductionRun:
        try:
            return service.get(project_id)
        except ContinuousProductionProblem as error:
            raise handle(error) from error

    @router.patch("/settings", response_model=ContinuousProductionRun)
    def update_settings(
        project_id: str,
        update: ContinuousProductionSettingsUpdate,
    ) -> ContinuousProductionRun:
        try:
            return service.update_settings(project_id, update)
        except ContinuousProductionProblem as error:
            raise handle(error) from error

    @router.post("/pause", response_model=ContinuousProductionRun)
    def pause(project_id: str) -> ContinuousProductionRun:
        try:
            return service.pause(project_id)
        except ContinuousProductionProblem as error:
            raise handle(error) from error

    @router.post("/resume", response_model=ContinuousProductionRun)
    def resume(project_id: str) -> ContinuousProductionRun:
        try:
            return service.resume(project_id)
        except ContinuousProductionProblem as error:
            raise handle(error) from error

    @router.post("/retry", response_model=ContinuousProductionRun)
    def retry(project_id: str) -> ContinuousProductionRun:
        try:
            return service.retry(project_id)
        except ContinuousProductionProblem as error:
            raise handle(error) from error

    @router.post("/skip", response_model=ContinuousProductionRun)
    def skip(project_id: str) -> ContinuousProductionRun:
        try:
            return service.skip_problem_slice(project_id)
        except ContinuousProductionProblem as error:
            raise handle(error) from error

    @router.post("/cancel", response_model=ContinuousProductionRun)
    def cancel(project_id: str) -> ContinuousProductionRun:
        try:
            return service.cancel(project_id)
        except ContinuousProductionProblem as error:
            raise handle(error) from error

    return router
