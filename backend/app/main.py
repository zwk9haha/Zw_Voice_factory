from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .jobs import HttpModelGateway, JobService, ModelGateway, create_jobs_router
from .preparation import PreparationService, create_preparation_router
from .workspace import WorkspacePayload, build_demo_workspace


DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def create_app(workspace_root: Path | None = None, model_gateway: ModelGateway | None = None) -> FastAPI:
    root = (workspace_root or DEFAULT_WORKSPACE_ROOT).resolve()
    jobs = JobService(root, model_gateway or HttpModelGateway())
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
    application.include_router(create_preparation_router(PreparationService(root)))
    application.include_router(create_jobs_router(jobs))
    application.router.add_event_handler("shutdown", jobs.close)

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "zw-voice-factory"}

    @application.get("/api/workspace", response_model=WorkspacePayload)
    def workspace() -> WorkspacePayload:
        return build_demo_workspace()

    return application


app = create_app()
