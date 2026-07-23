import asyncio

import httpx

from app.main import app


async def get_workspace() -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/workspace")


async def get_path(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


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


def test_workspace_preview_audio_is_served_from_the_declared_media_path() -> None:
    payload = asyncio.run(get_workspace()).json()
    preview_url = payload["characters"][1]["preview_audio_url"]

    response = asyncio.run(get_path(preview_url))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
