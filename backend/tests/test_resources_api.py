from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.main import create_app


def test_system_resources_have_a_stable_ui_contract(tmp_path: Path) -> None:
    async def fetch() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(tmp_path))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/system/resources")

    response = asyncio.run(fetch())

    assert response.status_code == 200
    payload = response.json()
    assert 0 <= payload["cpu"]["percent"] <= 100
    assert 0 <= payload["memory"]["percent"] <= 100
    assert payload["memory"]["total_gb"] > 0
    assert isinstance(payload["gpu"]["available"], bool)
    assert payload["timestamp"].endswith("Z") or "+00:00" in payload["timestamp"]
