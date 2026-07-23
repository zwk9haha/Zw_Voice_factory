import asyncio

import httpx

from app.main import app


async def get_workspace() -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/workspace")


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
