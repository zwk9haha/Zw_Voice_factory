import asyncio
import hashlib
import json
from pathlib import Path

import httpx

from app.main import app, create_app


async def get_workspace() -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/workspace")


async def get_project_workspace(workspace_root: Path) -> tuple[httpx.Response, httpx.Response]:
    application = create_app(workspace_root)
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        workspace_response = await client.get("/api/workspace")
        project_id = workspace_response.json()["project"]["id"]
        rvc_response = await client.get(f"/api/projects/{project_id}/rvc/workspace")
        return workspace_response, rvc_response


def test_quality_workspace_exposes_template_and_ordered_production_stages() -> None:
    response = asyncio.run(get_workspace())

    assert response.status_code == 200
    payload = response.json()
    assert [stage["stage_id"] for stage in payload["workflow"]] == [
        "template",
        "source",
        "casting",
        "references",
        "emotions",
        "director",
        "quality_render",
    ]
    assert payload["active_template"]["quality_route"] == {
        "reference_backend": "voxcpm2",
        "render_backend": "gpt_sovits",
        "stability_backend": "rvc",
        "stability_policy": "benchmark_gated",
    }


def test_empty_workspace_contains_no_demo_project_or_generated_summary(tmp_path: Path) -> None:
    application = create_app(tmp_path)
    transport = httpx.ASGITransport(app=application)

    async def request() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/workspace")

    response = asyncio.run(request())

    assert response.status_code == 200
    payload = response.json()
    assert payload["project"] == {"id": "", "name": "未创建项目", "route": "quality"}
    assert payload["summary"] == {
        "characters": 0,
        "accepted_references": 0,
        "segments": 0,
        "generated": 0,
    }
    assert "characters" not in payload
    assert "segments" not in payload


def test_workspace_project_resolves_to_the_same_project_used_by_rvc(tmp_path: Path) -> None:
    file_name = "novel.txt"
    project_id = f"project-{hashlib.sha1(file_name.encode('utf-8')).hexdigest()[:12]}"
    source_root = tmp_path / "input"
    project_root = tmp_path / "outputs" / "projects" / project_id
    source_root.mkdir(parents=True)
    project_root.mkdir(parents=True)
    (source_root / file_name).write_text("test content", encoding="utf-8")
    (project_root / "director_doc.json").write_text("{}", encoding="utf-8")
    (project_root / "reference_plan.json").write_text(
        json.dumps(
            {
                "project_id": project_id,
                "items": [
                    {
                        "reference_id": "reference-narrator",
                        "source_character_id": "narrator",
                        "display_name": "Narrator",
                        "gender": "male",
                        "selected": True,
                        "audio_versions": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    workspace_response, rvc_response = asyncio.run(get_project_workspace(tmp_path))

    assert workspace_response.status_code == 200
    assert workspace_response.json()["project"] == {
        "id": project_id,
        "name": "novel",
        "route": "quality",
    }
    assert rvc_response.status_code == 200
    assert [item["display_name"] for item in rvc_response.json()["characters"]] == ["Narrator"]
