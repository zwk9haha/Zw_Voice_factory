from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.main import create_app


async def request(app: object, method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def write_character_bible(workspace_root: Path, project_id: str) -> None:
    project_root = workspace_root / "outputs" / "projects" / project_id
    project_root.mkdir(parents=True)
    (project_root / "character_voice_bible.json").write_text(
        json.dumps(
            {
                "characters": [
                    {"character_id": "narrator", "display_name": "旁白", "gender": "unknown"},
                    {"character_id": "character-xiao-yan", "display_name": "萧炎", "gender": "male"},
                    {"character_id": "character-xuner", "display_name": "萧薰儿", "gender": "female"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_fast_route_assigns_and_persists_lightweight_voices(tmp_path: Path) -> None:
    write_character_bible(tmp_path, "project-fast")
    app = create_app(tmp_path)

    initial = asyncio.run(request(app, "GET", "/api/projects/project-fast/fast/workspace"))

    assert initial.status_code == 200
    payload = initial.json()
    assignments = {item["character_id"]: item["voice_id"] for item in payload["settings"]["assignments"]}
    assert payload["engine"] == "sherpa-onnx-vits-zh-ll"
    assert len(payload["voices"]) == 5
    assert assignments["character-xiao-yan"] == "bazong"
    assert assignments["character-xuner"] in {"suyingxue", "gunian", "fushiyu", "bingjiao"}

    updated = asyncio.run(
        request(
            app,
            "PATCH",
            "/api/projects/project-fast/fast/settings",
            json={"character_id": "character-xuner", "voice_id": "bingjiao"},
        )
    )
    assert updated.status_code == 200
    restored = asyncio.run(request(app, "GET", "/api/projects/project-fast/fast/workspace")).json()
    restored_assignments = {item["character_id"]: item["voice_id"] for item in restored["settings"]["assignments"]}
    assert restored_assignments["character-xuner"] == "bingjiao"
    assert (tmp_path / "outputs" / "projects" / "project-fast" / "fast_route_settings.json").is_file()
