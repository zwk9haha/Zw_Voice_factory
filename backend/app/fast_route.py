from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


class FastVoiceOption(BaseModel):
    voice_id: str
    label: str
    gender: Literal["male", "female", "unknown"]
    effect: str


FAST_VOICES = (
    FastVoiceOption(voice_id="suyingxue", label="苏映雪", gender="female", effect="清亮、克制"),
    FastVoiceOption(voice_id="gunian", label="顾念", gender="female", effect="自然、平稳"),
    FastVoiceOption(voice_id="fushiyu", label="傅诗语", gender="female", effect="温和、柔润"),
    FastVoiceOption(voice_id="bingjiao", label="高张力女声", gender="female", effect="高情绪张力"),
    FastVoiceOption(voice_id="bazong", label="沉稳男声", gender="male", effect="低沉、有力度"),
)


class FastVoiceAssignment(BaseModel):
    character_id: str
    voice_id: str


class FastRouteSettings(BaseModel):
    default_male_voice_id: str = "bazong"
    default_female_voice_id: str = "gunian"
    default_unknown_voice_id: str = "suyingxue"
    assignments: list[FastVoiceAssignment] = Field(default_factory=list)


class FastRouteWorkspace(BaseModel):
    project_id: str
    engine: str = "sherpa-onnx-vits-zh-ll"
    voices: list[FastVoiceOption]
    settings: FastRouteSettings


class FastRouteUpdate(BaseModel):
    character_id: str = Field(min_length=1, max_length=120)
    voice_id: str = Field(min_length=1, max_length=80)


class FastRouteProblem(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class FastRouteService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def workspace(self, project_id: str) -> FastRouteWorkspace:
        project_root = self._project_root(project_id)
        settings = self._read_settings(project_root)
        valid_voice_ids = {voice.voice_id for voice in FAST_VOICES}
        assignments = {
            item.character_id: item.voice_id
            for item in settings.assignments
            if item.voice_id in valid_voice_ids
        }
        for character in self._characters(project_root):
            character_id = str(character.get("character_id") or "")
            if not character_id or character_id in assignments:
                continue
            assignments[character_id] = self._default_voice(character, settings)
        settings = settings.model_copy(
            update={
                "assignments": [
                    FastVoiceAssignment(character_id=character_id, voice_id=voice_id)
                    for character_id, voice_id in sorted(assignments.items())
                ]
            }
        )
        return FastRouteWorkspace(
            project_id=project_id,
            voices=list(FAST_VOICES),
            settings=settings,
        )

    def update(self, project_id: str, request: FastRouteUpdate) -> FastRouteWorkspace:
        project_root = self._project_root(project_id)
        valid_voice_ids = {voice.voice_id for voice in FAST_VOICES}
        if request.voice_id not in valid_voice_ids:
            raise FastRouteProblem(422, "选择的轻量 TTS 声线不存在")
        workspace = self.workspace(project_id)
        assignments = {item.character_id: item.voice_id for item in workspace.settings.assignments}
        assignments[request.character_id] = request.voice_id
        settings = workspace.settings.model_copy(
            update={
                "assignments": [
                    FastVoiceAssignment(character_id=character_id, voice_id=voice_id)
                    for character_id, voice_id in sorted(assignments.items())
                ]
            }
        )
        self._write_settings(project_root, settings)
        return FastRouteWorkspace(
            project_id=project_id,
            voices=list(FAST_VOICES),
            settings=settings,
        )

    def _project_root(self, project_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", project_id):
            raise FastRouteProblem(400, "项目 ID 无效")
        root = (self.workspace_root / "outputs" / "projects" / project_id).resolve()
        projects_root = (self.workspace_root / "outputs" / "projects").resolve()
        if not root.is_relative_to(projects_root) or not root.is_dir():
            raise FastRouteProblem(404, "项目不存在")
        return root

    @staticmethod
    def _characters(project_root: Path) -> list[dict[str, object]]:
        bible_path = project_root / "character_voice_bible.json"
        if not bible_path.is_file():
            return []
        try:
            payload = json.loads(bible_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        characters = payload.get("characters")
        return characters if isinstance(characters, list) else []

    @staticmethod
    def _default_voice(character: dict[str, object], settings: FastRouteSettings) -> str:
        gender = character.get("gender")
        if gender == "male":
            return settings.default_male_voice_id
        if gender == "female":
            female_voices = [voice.voice_id for voice in FAST_VOICES if voice.gender == "female"]
            digest = hashlib.sha1(str(character.get("character_id") or "").encode("utf-8")).digest()[0]
            return female_voices[digest % len(female_voices)]
        return settings.default_unknown_voice_id

    @staticmethod
    def _read_settings(project_root: Path) -> FastRouteSettings:
        path = project_root / "fast_route_settings.json"
        if not path.is_file():
            return FastRouteSettings()
        try:
            return FastRouteSettings.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return FastRouteSettings()

    @staticmethod
    def _write_settings(project_root: Path, settings: FastRouteSettings) -> None:
        path = project_root / "fast_route_settings.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)


def create_fast_route_router(service: FastRouteService) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["fast-route"])

    @router.get("/projects/{project_id}/fast/workspace", response_model=FastRouteWorkspace)
    def fast_workspace(project_id: str) -> FastRouteWorkspace:
        try:
            return service.workspace(project_id)
        except FastRouteProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    @router.patch("/projects/{project_id}/fast/settings", response_model=FastRouteWorkspace)
    def update_fast_settings(project_id: str, request: FastRouteUpdate) -> FastRouteWorkspace:
        try:
            return service.update(project_id, request)
        except FastRouteProblem as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return router
