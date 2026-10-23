import asyncio
import io
import time
import wave
from pathlib import Path

import httpx

from app.jobs import ModelGateway
from app.loudness import LoudnessMetrics, ProgramLoudnessPolicy
from app.main import create_app
from app.platform import analyze_text_segments


def test_named_novel_dialogue_preserves_text_and_separates_narration() -> None:
    text = '萧炎笑道：“我们走。现在就走！”\n“好。”萧薰儿轻声说道。\n“谁在那里？”\n风吹过山谷。'
    segments = analyze_text_segments(text, speaker_names=["萧炎", "萧薰儿"])
    assert [(segment.text, segment.speaker.speaker_name) for segment in segments] == [
        ("萧炎笑道：", "旁白"), ("我们走。", "萧炎"), ("现在就走！", "萧炎"),
        ("好。", "萧薰儿"), ("萧薰儿轻声说道。", "旁白"),
        ("谁在那里？", "未指定说话人"), ("风吹过山谷。", "旁白"),
    ]
    assert not any(segment.text in {"“", "”", "！", "？"} for segment in segments)


def wav_bytes(seconds: float = 0.05) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * int(16_000 * seconds))
    return buffer.getvalue()


class DecisionGateway(ModelGateway):
    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        del text, voice_prompt
        return wav_bytes()

    def generate_voxcpm_with_options(self, text: str, voice_prompt: str, cfg_value: float, inference_timesteps: int) -> bytes:
        del text, voice_prompt, cfg_value, inference_timesteps
        return wav_bytes()

    def runtime_status(self) -> dict[str, object]:
        return {"launcher_managed": True, "services": {}}


class PassThroughLoudness:
    def process_segment_bytes(self, audio: bytes, policy: ProgramLoudnessPolicy) -> tuple[bytes, LoudnessMetrics]:
        del policy
        return audio, LoudnessMetrics(processor="test", status="unchanged", applied_gain_db=0)

    def rolling_gain_db(self, policy: ProgramLoudnessPolicy, program_id: str | None = None) -> float:
        del policy, program_id
        return 0

    def record(self, metrics: LoudnessMetrics | None, program_id: str | None = None) -> None:
        del metrics, program_id


def test_platform_can_create_short_text_and_persist_quick_preview(tmp_path: Path) -> None:
    application = create_app(tmp_path)

    async def request() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            project_response = await client.post(
                "/api/platform/projects",
                json={
                    "name": "短文本试听",
                    "content_type": "short_text",
                    "text": "夜色渐深。‘你终于来了！’萧炎说道。",
                },
            )
            project_id = project_response.json()["project_id"]
            task_response = await client.post(f"/api/platform/projects/{project_id}/tasks/quick-preview")
            workspace_response = await client.get(f"/api/platform/projects/{project_id}/workspace")
            return project_response, task_response, workspace_response

    project_response, task_response, workspace_response = asyncio.run(request())

    assert project_response.status_code == 201
    assert project_response.json()["content_type"] == "short_text"
    assert task_response.status_code == 201
    task = task_response.json()
    assert task["status"] == "ready_for_review"
    assert task["segment_count"] >= 2
    assert task["segments"][0]["speaker"]["speaker_kind"] == "narration"
    assert any(segment["speaker"]["speaker_kind"] == "named" for segment in task["segments"])
    assert workspace_response.status_code == 200
    assert workspace_response.json()["tasks"][0]["task_id"] == task["task_id"]


def test_platform_lists_existing_novel_projects_as_scenario_adapters(tmp_path: Path) -> None:
    application = create_app(tmp_path)
    source_root = tmp_path / "input"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "novel.txt").write_text("第一章。", encoding="utf-8")

    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/api/platform/projects")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json()[0]["content_type"] == "novel"
    assert response.json()[0]["name"] == "novel"


def _wait_for_decision_generation(application: object, project_id: str, decision_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = asyncio.run(
            _request(application, "GET", f"/api/platform/projects/{project_id}/voice-decisions/{decision_id}")
        )
        payload = response.json()
        generation = payload.get("generation") or {}
        if generation.get("status") in {"complete", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("voice candidate generation did not finish")


async def _request(app: object, method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_platform_match_or_generate_requires_explicit_generation_and_acceptance(tmp_path: Path) -> None:
    application = create_app(
        tmp_path,
        model_gateway=DecisionGateway(),
        loudness_processor=PassThroughLoudness(),
    )
    project = asyncio.run(
        _request(
            application,
            "POST",
            "/api/platform/projects",
            json={"name": "显式声音决策", "content_type": "short_text", "text": "林默说道：‘现在出发。’"},
        )
    ).json()
    project_id = project["project_id"]

    decision_response = asyncio.run(
        _request(
            application,
            "POST",
            f"/api/platform/projects/{project_id}/voice-decisions/match-or-generate",
            json={"speaker_key": "lin-mo", "display_name": "林默", "gender": "male", "minimum_score": 0.99},
        )
    )
    assert decision_response.status_code == 201
    decision = decision_response.json()
    assert decision["action"] == "generate_candidate"
    assert decision["generation_required"] is True
    assert decision["generation"]["status"] == "not_started"

    started = asyncio.run(
        _request(
            application,
            "POST",
            f"/api/platform/projects/{project_id}/voice-decisions/{decision['decision_id']}/generate",
        )
    )
    assert started.status_code == 202
    generated = _wait_for_decision_generation(application, project_id, decision["decision_id"])
    assert generated["generation"]["status"] == "complete"
    candidate = generated["snapshot"]
    assert candidate["status"] == "pending"
    assert candidate["audio_url"].startswith("/media/outputs/platform/projects/")

    approved = asyncio.run(
        _request(
            application,
            "POST",
            f"/api/platform/projects/{project_id}/voice-snapshots",
            json={
                "speaker_key": "lin-mo",
                "display_name": "林默",
                "source": "generated",
                "job_id": candidate["job_id"],
            },
        )
    )
    assert approved.status_code == 201
    snapshot = approved.json()
    assert snapshot["status"] == "approved"
    assert snapshot["snapshot_id"] == candidate["snapshot_id"]

    reused = asyncio.run(
        _request(
            application,
            "POST",
            f"/api/platform/projects/{project_id}/voice-decisions/match-or-generate",
            json={"speaker_key": "lin-mo", "display_name": "林默", "minimum_score": 0.99},
        )
    ).json()
    assert reused["action"] == "reuse_snapshot"
    assert reused["source"] == "project_snapshot"
    assert reused["snapshot"]["snapshot_id"] == snapshot["snapshot_id"]


def test_platform_catalog_match_can_be_explicitly_bound_to_project_snapshot(tmp_path: Path) -> None:
    application = create_app(
        tmp_path,
        model_gateway=DecisionGateway(),
        loudness_processor=PassThroughLoudness(),
    )
    project = asyncio.run(
        _request(
            application,
            "POST",
            "/api/platform/projects",
            json={"name": "目录绑定", "content_type": "short_text", "text": "她平静地说。"},
        )
    ).json()
    catalog = asyncio.run(_request(application, "GET", "/api/voice-catalog")).json()
    asset_id = catalog["assets"][0]["asset_id"]
    generated_asset = asyncio.run(
        _request(
            application,
            "POST",
            f"/api/voice-catalog/assets/{asset_id}/generate",
            json={"preset_id": "stable_voice_catalog"},
        )
    ).json()
    version_id = generated_asset["versions"][-1]["version_id"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = asyncio.run(_request(application, "GET", f"/api/voice-catalog/assets/{asset_id}")).json()
        version = next(item for item in current["versions"] if item["version_id"] == version_id)
        if version["status"] == "complete":
            break
        time.sleep(0.01)
    decision = asyncio.run(
        _request(
            application,
            "POST",
            f"/api/platform/projects/{project['project_id']}/voice-decisions/match-or-generate",
            json={"speaker_key": "narrator", "display_name": "旁白", "gender": catalog["assets"][0]["gender"], "minimum_score": 0.2},
        )
    ).json()
    assert decision["action"] == "catalog_match"
    assert decision["selected_match"]["version_id"] == version_id
    snapshot = asyncio.run(
        _request(
            application,
            "POST",
            f"/api/platform/projects/{project['project_id']}/voice-snapshots",
            json={
                "speaker_key": "narrator",
                "display_name": "旁白",
                "source": "catalog",
                "catalog_asset_id": asset_id,
                "catalog_version_id": version_id,
            },
        )
    )
    assert snapshot.status_code == 201
    assert snapshot.json()["catalog_version_id"] == version_id
    assert snapshot.json()["audio_url"].startswith("/media/outputs/platform/projects/")
