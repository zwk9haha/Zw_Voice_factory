from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .continuous_production import ContinuousProductionService, create_continuous_production_router
from .fast_route import FastRouteService, create_fast_route_router
from .jobs import HttpModelGateway, JobService, ModelGateway, create_jobs_router
from .loudness import LoudnessProcessor
from .preparation import PreparationProblem, PreparationService, create_preparation_router
from .production import ProductionService, create_production_router
from .resources import create_resources_router
from .rvc import RvcInferenceRunner, RvcService, RvcTrainingRunner, create_rvc_router
from .runtime_logs import RuntimeLogsService, configure_runtime_logger, create_runtime_logs_router
from .voice_analysis import VoiceAnalyzer, create_voice_analyzer
from .workspace import WorkspacePayload, WorkspaceProject, WorkspaceSummary, build_workspace


DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def create_app(
    workspace_root: Path | None = None,
    model_gateway: ModelGateway | None = None,
    voice_analyzer: VoiceAnalyzer | None = None,
    rvc_runner: RvcTrainingRunner | None = None,
    rvc_inference_runner: RvcInferenceRunner | None = None,
    loudness_processor: LoudnessProcessor | None = None,
) -> FastAPI:
    root = (workspace_root or DEFAULT_WORKSPACE_ROOT).resolve()
    runtime_logs = RuntimeLogsService(root)
    runtime_logger = configure_runtime_logger(runtime_logs)
    selected_voice_analyzer = voice_analyzer or create_voice_analyzer(
        root,
        default_backend="rules" if workspace_root is not None else None,
        runtime_logger=runtime_logger,
    )
    selected_model_gateway = model_gateway or HttpModelGateway()
    preparation = PreparationService(root, selected_voice_analyzer, runtime_logger=runtime_logger)
    fast_route = FastRouteService(root)
    production = ProductionService(root)
    selected_loudness_processor = loudness_processor or LoudnessProcessor(root, runtime_logger=runtime_logger)
    rvc = RvcService(
        root,
        rvc_runner,
        rvc_inference_runner,
        material_generator=selected_model_gateway.generate_voxcpm,
        preview_gateway=selected_model_gateway,
        runtime_logger=runtime_logger,
    )
    jobs = JobService(
        root,
        selected_model_gateway,
        reference_event_handler=preparation.record_reference_job,
        emotion_event_handler=preparation.record_emotion_job,
        quality_stability_handler=rvc.apply_quality_stability,
        fast_route_handler=rvc.apply_fast_route,
        gpu_lock=rvc.gpu_lock,
        loudness_processor=selected_loudness_processor,
        loudness_policy_provider=production.loudness_policy,
        runtime_logger=runtime_logger,
    )
    continuous_production = ContinuousProductionService(
        root,
        preparation,
        jobs,
        rvc,
        runtime_logger=runtime_logger,
    )
    application = FastAPI(title="Zw Voice Factory API", version="0.2.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    voice_samples_root = root / "assets" / "voice_samples"
    if voice_samples_root.is_dir():
        application.mount(
            "/media/voice-samples",
            StaticFiles(directory=voice_samples_root),
            name="voice-samples",
        )
    application.mount(
        "/media/outputs/audio",
        StaticFiles(directory=root / "outputs" / "audio"),
        name="output-audio",
    )
    application.mount(
        "/media/outputs/projects",
        StaticFiles(directory=root / "outputs" / "projects"),
        name="project-assets",
    )
    application.mount(
        "/media/outputs/rvc-previews",
        StaticFiles(directory=root / "outputs" / "rvc" / "previews"),
        name="rvc-previews",
    )
    application.include_router(create_preparation_router(preparation))
    application.include_router(create_fast_route_router(fast_route))
    application.include_router(create_production_router(production))
    application.include_router(create_rvc_router(rvc))
    application.include_router(create_jobs_router(jobs))
    application.include_router(create_continuous_production_router(continuous_production))
    application.include_router(create_resources_router())
    application.include_router(create_runtime_logs_router(runtime_logs))
    application.router.add_event_handler("shutdown", jobs.close)
    application.router.add_event_handler("shutdown", continuous_production.close)
    application.router.add_event_handler("shutdown", rvc.close)

    @application.get("/api/health")
    def health() -> dict[str, str | bool]:
        return {
            "status": "ok",
            "service": "zw-voice-factory",
            "launcher_managed": os.getenv("ZW_VOICE_LAUNCHER_MANAGED") == "1",
        }

    @application.get("/api/workspace", response_model=WorkspacePayload)
    def workspace() -> WorkspacePayload:
        sources = preparation.list_sources()
        source = next((item for item in sources if item.status == "director_ready"), sources[0] if sources else None)
        project = (
            WorkspaceProject(id=source.project_id, name=source.display_name, route="quality")
            if source is not None
            else None
        )
        configured_backend = preparation.voice_analysis_configuration().backend
        inference_mode = configured_backend if configured_backend in {"cloud", "hybrid", "local"} else "local"
        summary = WorkspaceSummary()
        if source is not None:
            try:
                preview = preparation.preview(source.project_id)
            except (OSError, ValueError, PreparationProblem):
                preview = None
            if preview is not None:
                summary.characters = len(preview.character_voice_bible.characters) if preview.character_voice_bible else 0
                summary.accepted_references = (
                    sum(1 for item in preview.reference_plan.items if item.status == "generated" and item.audio_url)
                    if preview.reference_plan
                    else 0
                )
                summary.segments = len(preview.director_doc.segments) if preview.director_doc else 0
            summary.generated = sum(
                1
                for item in jobs.list(5_000, project_id=source.project_id)
                if item.kind in {"quality_render", "fast_render"} and item.status == "complete"
            )
        return build_workspace(
            project,
            inference_mode=inference_mode,
            preparation_status=source.status if source is not None else "empty",
            summary=summary,
        )

    return application


app = create_app()
