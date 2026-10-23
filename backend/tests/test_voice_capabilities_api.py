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


def wav_bytes(sample_rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * sample_rate)
    return buffer.getvalue()


class CapabilityGateway(ModelGateway):
    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        return wav_bytes()

    def generate_voxcpm_with_options(self, text: str, voice_prompt: str, cfg_value: float, inference_timesteps: int) -> bytes:
        return wav_bytes()

    def generate_quality(
        self,
        text: str,
        reference_audio_path: Path,
        quality_model: str,
        emotion_description: str | None = None,
        render_options: object | None = None,
        reference_text: str | None = None,
    ) -> bytes:
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
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def wait_for_job(app: object, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = asyncio.run(request(app, "GET", f"/api/jobs/{job_id}"))
        payload = response.json()
        if payload["status"] in {"complete", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("voice capability job did not finish")


def test_voice_capability_descriptor_exposes_composable_nodes(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=CapabilityGateway())

    response = asyncio.run(request(application, "GET", "/api/capabilities/voice"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["capability_id"] == "voice.factory"
    assert payload["accepted_content_types"] == ["short_text", "novel", "script", "subtitle", "document"]
    assert {node["node_id"] for node in payload["nodes"]} >= {
        "voice.text_ingest",
        "voice.match",
        "voice.reference_text",
        "voice.reference_generation",
        "voice.quality_render",
        "voice.rvc_stability",
        "voice.program_loudness",
        "voice.merge",
    }
    assert any(node["supports_streaming"] for node in payload["nodes"])


def test_voice_capability_can_submit_external_quality_render(tmp_path: Path) -> None:
    sample = tmp_path / "assets" / "voice_samples" / "adapter-reference.wav"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(wav_bytes())
    application = create_app(
        tmp_path,
        model_gateway=CapabilityGateway(),
        loudness_processor=PassThroughLoudness(),
    )

    response = asyncio.run(request(application, "POST", "/api/capabilities/voice/synthesize", json={
        "text": "这是一段由外部能力模块提交的试听文本。",
        "reference_audio_url": "/media/voice-samples/adapter-reference.wav",
        "reference_text": "这是参考音频对应的文本。",
        "quality_model": "gpt_sovits_v2",
    }))

    assert response.status_code == 202
    submitted = response.json()
    assert submitted["capability_id"] == "voice.factory"
    result = wait_for_job(application, submitted["job_id"])
    assert result["status"] == "complete"
    assert result["output_url"].startswith("/media/outputs/audio/jobs/")


def test_voice_capability_reuses_completed_catalog_version(tmp_path: Path) -> None:
    application = create_app(
        tmp_path,
        model_gateway=CapabilityGateway(),
        loudness_processor=PassThroughLoudness(),
    )
    catalog = asyncio.run(request(application, "GET", "/api/voice-catalog")).json()
    asset_id = catalog["assets"][0]["asset_id"]
    generated = asyncio.run(
        request(
            application,
            "POST",
            f"/api/voice-catalog/assets/{asset_id}/generate",
            json={},
        )
    )
    assert generated.status_code == 202
    version_id = generated.json()["versions"][-1]["version_id"]
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        asset = asyncio.run(request(application, "GET", f"/api/voice-catalog/assets/{asset_id}")).json()
        version = next(item for item in asset["versions"] if item["version_id"] == version_id)
        if version["status"] == "complete":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("catalog reference did not finish")

    response = asyncio.run(request(application, "POST", "/api/capabilities/voice/synthesize", json={
        "text": "复用音色库版本生成这一句试听。",
        "catalog_asset_id": asset_id,
        "catalog_version_id": version_id,
        "quality_model": "gpt_sovits_v2",
    }))

    assert response.status_code == 202
    submitted = response.json()
    assert submitted["catalog_asset_id"] == asset_id
    assert submitted["catalog_version_id"] == version_id
    assert wait_for_job(application, submitted["job_id"])["status"] == "complete"
