from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .workspace import WorkspacePayload, build_demo_workspace


app = FastAPI(title="Zw Voice Factory API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "zw-voice-factory"}


@app.get("/api/workspace", response_model=WorkspacePayload)
def workspace() -> WorkspacePayload:
    return build_demo_workspace()
