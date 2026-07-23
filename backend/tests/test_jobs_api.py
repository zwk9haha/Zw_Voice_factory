from __future__ import annotations

import asyncio
import time
import wave
from pathlib import Path

import httpx
import pytest

from app.jobs import HttpModelGateway, ModelGateway
from app.main import create_app


def wav_bytes() -> bytes:
    output = bytearray()
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 160)
    output.extend(buffer.getvalue())
    return bytes(output)


class FakeModelGateway(ModelGateway):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        self.calls.append(("voxcpm_reference", text, voice_prompt))
        return wav_bytes()

    def generate_quality(self, text: str, reference_audio_path: Path) -> bytes:
        self.calls.append(("quality_render", text, str(reference_audio_path)))
        return wav_bytes()

    def runtime_status(self) -> dict[str, object]:
        return {
            "launcher_managed": True,
            "services": {
                "voxcpm2": {"status": "ready", "url": "http://127.0.0.1:9881"},
                "gpt_sovits": {"status": "ready", "url": "http://127.0.0.1:9880"},
            },
        }


async def request(app: object, method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def wait_for_job(app: object, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        response = asyncio.run(request(app, "GET", f"/api/jobs/{job_id}"))
        payload = response.json()
        if payload["status"] in {"complete", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_voxcpm_job_is_queued_generated_and_served(tmp_path: Path) -> None:
    gateway = FakeModelGateway()
    app = create_app(tmp_path, model_gateway=gateway)

    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/jobs",
            json={
                "kind": "voxcpm_reference",
                "text": "雨后的长街渐渐安静下来。",
                "voice_prompt": "青年男声，清亮而克制",
                "character_id": "xiao_yan",
            },
        )
    )

    assert response.status_code == 202
    completed = wait_for_job(app, response.json()["job_id"])
    assert completed["status"] == "complete"
    assert completed["progress"] == 100
    assert completed["output_url"].startswith("/media/outputs/audio/jobs/")
    assert gateway.calls[0][:2] == ("voxcpm_reference", "雨后的长街渐渐安静下来。")

    audio = asyncio.run(request(app, "GET", str(completed["output_url"])))
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")


def test_voxcpm_reference_job_updates_the_project_reference_plan(tmp_path: Path) -> None:
    gateway = FakeModelGateway()
    app = create_app(tmp_path, model_gateway=gateway)
    imported = asyncio.run(
        request(
            app,
            "POST",
            "/api/sources",
            files={
                "file": (
                    "参考任务.txt",
                    "第一章 初见\n萧炎说道：\"开始吧。\"".encode(),
                    "text/plain",
                )
            },
        )
    ).json()
    project_id = imported["project_id"]
    asyncio.run(request(app, "POST", f"/api/projects/{project_id}/preparation", json={"action": "analyze"}))
    extracted = asyncio.run(
        request(
            app,
            "POST",
            f"/api/projects/{project_id}/preparation",
            json={"action": "extract_characters"},
        )
    ).json()
    reference = next(item for item in extracted["reference_plan"]["items"] if item["display_name"] == "萧炎")

    queued = asyncio.run(
        request(
            app,
            "POST",
            "/api/jobs",
            json={
                "kind": "voxcpm_reference",
                "project_id": project_id,
                "reference_id": reference["reference_id"],
                "character_id": reference["source_character_id"],
                "text": reference["reference_text"],
                "voice_prompt": reference["voice_prompt"],
            },
        )
    )

    assert queued.status_code == 202
    completed = wait_for_job(app, queued.json()["job_id"])
    assert completed["status"] == "complete"
    preview = asyncio.run(request(app, "GET", f"/api/projects/{project_id}/preparation/preview")).json()
    updated = next(
        item for item in preview["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert updated["status"] == "generated"
    assert updated["job_id"] == completed["job_id"]
    assert updated["audio_url"] == completed["output_url"]


def test_quality_job_resolves_only_workspace_media_paths(tmp_path: Path) -> None:
    reference = tmp_path / "assets" / "voice_samples" / "curated" / "reference.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(wav_bytes())
    gateway = FakeModelGateway()
    app = create_app(tmp_path, model_gateway=gateway)

    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/jobs",
            json={
                "kind": "quality_render",
                "text": "开始吧。",
                "reference_audio_url": "/media/voice-samples/curated/reference.wav",
                "segment_id": "s001",
                "character_id": "xiao_yan",
            },
        )
    )

    completed = wait_for_job(app, response.json()["job_id"])
    assert completed["status"] == "complete"
    assert gateway.calls[0] == ("quality_render", "开始吧。", str(reference.resolve()))

    unsafe = asyncio.run(
        request(
            app,
            "POST",
            "/api/jobs",
            json={
                "kind": "quality_render",
                "text": "不应执行。",
                "reference_audio_url": "/media/voice-samples/../../outside.wav",
            },
        )
    )
    assert unsafe.status_code == 400


def test_runtime_reports_model_preload_state(tmp_path: Path) -> None:
    app = create_app(tmp_path, model_gateway=FakeModelGateway())

    response = asyncio.run(request(app, "GET", "/api/runtime"))

    assert response.status_code == 200
    assert response.json()["services"]["voxcpm2"]["status"] == "ready"
    assert response.json()["services"]["gpt_sovits"]["status"] == "ready"


def test_http_gateway_retries_one_transient_model_response(monkeypatch: pytest.MonkeyPatch) -> None:
    request = httpx.Request("POST", "http://127.0.0.1:9881/generate")
    responses = [
        httpx.Response(502, request=request),
        httpx.Response(200, content=wav_bytes(), request=request),
    ]

    def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        return responses.pop(0)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr("app.jobs.time.sleep", lambda _: None)

    audio = HttpModelGateway().generate_voxcpm("开始吧。", "自然声线")

    assert audio[:4] == b"RIFF"
    assert responses == []
