from __future__ import annotations

import asyncio
import io
import time
import wave
from pathlib import Path

import httpx

from app.jobs import ModelGateway
from app.loudness import LoudnessMetrics, ProgramLoudnessPolicy
from app.main import create_app


def wav_bytes(sample_rate: int = 16_000, seconds: float = 10.0) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * int(sample_rate * seconds))
    return buffer.getvalue()


class CatalogGateway(ModelGateway):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, int]] = []

    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        return wav_bytes()

    def generate_voxcpm_with_options(self, text: str, voice_prompt: str, cfg_value: float, inference_timesteps: int) -> bytes:
        self.calls.append((text, voice_prompt, cfg_value, inference_timesteps))
        return wav_bytes()

    def runtime_status(self) -> dict[str, object]:
        return {"launcher_managed": True, "services": {}}


class PassThroughLoudness:
    def process_segment_bytes(self, audio: bytes, policy: ProgramLoudnessPolicy) -> tuple[bytes, LoudnessMetrics]:
        return audio, LoudnessMetrics(processor="test", status="unchanged", applied_gain_db=0)

    def rolling_gain_db(self, policy: ProgramLoudnessPolicy, program_id: str | None = None) -> float:
        return 0

    def record(self, metrics: LoudnessMetrics | None, program_id: str | None = None) -> None:
        return None


async def request(app: object, method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def wait_for_catalog_version(app: object, asset_id: str, version_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = asyncio.run(request(app, "GET", f"/api/voice-catalog/assets/{asset_id}"))
        versions = response.json()["versions"]
        version = next(item for item in versions if item["version_id"] == version_id)
        if version["status"] in {"complete", "failed"}:
            return version
        time.sleep(0.01)
    raise AssertionError("catalog version did not finish")


def test_voice_catalog_seeds_structured_quality_profiles(tmp_path: Path) -> None:
    application = create_app(tmp_path)

    response = asyncio.run(request(application, "GET", "/api/voice-catalog"))

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["assets"]) == 24
    assert {preset["preset_id"] for preset in payload["presets"]} == {
        "quality_voice_catalog",
        "stable_voice_catalog",
        "explore_voice_catalog",
    }
    assert len(payload["reference_texts"]) == 3
    first = payload["assets"][0]
    assert first["voice_prompt"]
    assert len(first["voice_prompt"]) > 120
    assert first["timbre_tags"]
    assert first["delivery_tags"]
    assert first["voice_constraints"]
    assert first["use_cases"]
    assert all(len(asset["voice_prompt"]) > 120 for asset in payload["assets"])


def test_voice_catalog_match_is_ranked_and_explainable(tmp_path: Path) -> None:
    application = create_app(tmp_path)

    response = asyncio.run(
        request(
            application,
            "POST",
            "/api/voice-catalog/match",
            json={
                "gender": "female",
                "age_range": "young_adult",
                "timbre_tags": ["清亮"],
                "delivery_tags": ["咬字清晰"],
                "limit": 3,
            },
        )
    )

    assert response.status_code == 200
    matches = response.json()
    assert len(matches) == 3
    assert matches[0]["asset_id"] == "female-young-clear-natural"
    assert matches[0]["score"] >= matches[1]["score"]
    assert "性别匹配" in matches[0]["reasons"]
    assert matches[0]["ready"] is False

    ready_only = asyncio.run(
        request(application, "POST", "/api/voice-catalog/match", json={"require_ready": True})
    )
    assert ready_only.status_code == 200
    assert ready_only.json() == []


def test_voice_catalog_generates_archives_and_derives_versions(tmp_path: Path) -> None:
    gateway = CatalogGateway()
    application = create_app(tmp_path, model_gateway=gateway, loudness_processor=PassThroughLoudness())
    catalog = asyncio.run(request(application, "GET", "/api/voice-catalog")).json()
    asset_id = catalog["assets"][0]["asset_id"]

    generated = asyncio.run(request(application, "POST", f"/api/voice-catalog/assets/{asset_id}/generate", json={"preset_id": "quality_voice_catalog"}))

    assert generated.status_code == 202
    base_version = generated.json()["versions"][-1]
    complete = wait_for_catalog_version(application, asset_id, base_version["version_id"])
    assert complete["status"] == "complete"
    assert complete["duration_seconds"] == 10.0
    assert complete["audio_url"].startswith("/media/outputs/voice-catalog/audio/")
    assert gateway.calls[-1][2:] == (2.4, 24)

    derived = asyncio.run(request(application, "POST", f"/api/voice-catalog/assets/{asset_id}/derive", json={
        "parent_version_id": base_version["version_id"],
        "variant_name": "克制紧张",
        "description": "保持身份，停顿更短，句尾轻微收紧",
        "emotion": "紧张",
        "intensity": 0.4,
    }))

    assert derived.status_code == 202
    derivative = derived.json()["versions"][-1]
    derivative_complete = wait_for_catalog_version(application, asset_id, derivative["version_id"])
    assert derivative_complete["status"] == "complete"
    assert derivative_complete["parent_version_id"] == base_version["version_id"]

    protected = asyncio.run(request(application, "DELETE", f"/api/voice-catalog/assets/{asset_id}/versions/{derivative['version_id']}"))
    assert protected.status_code == 409

    activated = asyncio.run(request(application, "POST", f"/api/voice-catalog/assets/{asset_id}/versions/{base_version['version_id']}/activate"))
    assert activated.status_code == 200
    deleted = asyncio.run(request(application, "DELETE", f"/api/voice-catalog/assets/{asset_id}/versions/{derivative['version_id']}"))
    assert deleted.status_code == 200
    assert all(item["version_id"] != derivative["version_id"] for item in deleted.json()["versions"])
