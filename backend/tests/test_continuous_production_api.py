from __future__ import annotations

import asyncio
import io
import threading
import time
import wave
from pathlib import Path

import httpx

import app.continuous_production as continuous_production_module
from app.jobs import ModelGateway, PcmAudioStream
from app.main import create_app
from app.preparation import PreparationService
from app.production import QualityRenderOptions
from app.rvc import RvcTrainingArtifact, RvcTrainingSpec
from app.voice_analysis import (
    CharacterEvidencePack,
    CharacterVoiceProfile,
    DirectorAnalysisDraft,
    DirectorCharacter,
    DirectorPassageDecision,
    DirectorPassageEvidence,
    VoiceAnalysisStatus,
)


def wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 320)
    return buffer.getvalue()


class ContinuousProductionGateway(ModelGateway):
    def __init__(self) -> None:
        self.reference_calls = 0

    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        del text, voice_prompt
        self.reference_calls += 1
        return wav_bytes()

    def generate_fast(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        del text, voice_id, speed
        return wav_bytes()

    def generate_quality(
        self,
        text: str,
        reference_audio_path: Path,
        quality_model: str,
        emotion_description: str | None = None,
        render_options: QualityRenderOptions | None = None,
        reference_text: str | None = None,
    ) -> bytes:
        del text, reference_audio_path, quality_model, emotion_description, render_options, reference_text
        return wav_bytes()

    def generate_quality_stream(
        self,
        text: str,
        reference_audio_path: Path,
        quality_model: str,
        emotion_description: str | None = None,
        render_options: QualityRenderOptions | None = None,
        reference_text: str | None = None,
    ) -> PcmAudioStream:
        del text, reference_audio_path, quality_model, emotion_description, render_options, reference_text
        return PcmAudioStream(16_000, 1, 2, iter((b"\x00\x00" * 320,)), lambda: None)

    def runtime_status(self) -> dict[str, object]:
        return {"launcher_managed": True, "services": {}}


class ContinuousRvcRunner:
    available = True
    detail = "continuous RVC test runner"

    def run(self, spec: RvcTrainingSpec, progress: object, is_cancelled: object) -> RvcTrainingArtifact:
        del is_cancelled
        progress(50, "训练中")
        spec.output_model_path.parent.mkdir(parents=True, exist_ok=True)
        spec.output_model_path.write_bytes(b"model")
        spec.output_index_path.write_bytes(b"index")
        return RvcTrainingArtifact(model_path=spec.output_model_path, index_path=spec.output_index_path)

    def cancel(self, job_id: str) -> None:
        del job_id

    def close(self) -> None:
        return None


class BlockingWindowVoiceAnalyzer:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.later_character_started = threading.Event()
        self.release_later_character = threading.Event()

    def status(self) -> VoiceAnalysisStatus:
        return VoiceAnalysisStatus(
            backend="rules",
            available=True,
            detail="window analyzer",
            taxonomy_version=1,
        )

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        self.calls.append(evidence_pack.display_name)
        if evidence_pack.display_name == "药老":
            self.later_character_started.set()
            assert self.release_later_character.wait(5)
        return CharacterVoiceProfile(
            gender=evidence_pack.gender_hint,
            age_range="adult",
            personality_tags=["沉稳"],
            timbre_tags=["中音区", "适中", "均衡", "干净", "混合共鸣"],
            delivery_tags=["吐字清楚", "气息平稳", "语速平稳", "动态自然"],
            voice_constraints=["保持自然口语"],
            voice_prompt=f"{evidence_pack.display_name} 的稳定声线",
            confidence=0.9,
            rationale="window test profile",
            backend="rules",
        )

    def analyze_director(
        self,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> DirectorAnalysisDraft:
        default_speaker = characters[0].display_name if characters else "未知角色"
        return DirectorAnalysisDraft(
            decisions=[
                DirectorPassageDecision(
                    passage_id=passage.passage_id,
                    speaker=passage.explicit_speaker or default_speaker,
                    emotion="natural",
                    emotion_intensity=0.5,
                    tone="natural",
                    confidence=0.9,
                    rationale="window test director",
                )
                for passage in passages
            ],
            backend="rules",
        )


class BlockingDirectorMicroBatchAnalyzer(BlockingWindowVoiceAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.director_calls = 0
        self.second_director_batch_started = threading.Event()
        self.release_second_director_batch = threading.Event()

    def analyze_director(
        self,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> DirectorAnalysisDraft:
        self.director_calls += 1
        if self.director_calls == 2:
            self.second_director_batch_started.set()
            assert self.release_second_director_batch.wait(8)
        return super().analyze_director(passages, characters)


async def request(app: object, method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def import_project(app: object, text: str) -> str:
    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/sources",
            files={"file": ("连续生产.txt", text.encode("utf-8"), "text/plain")},
            data={"project_name": "连续生产测试"},
        )
    )
    assert response.status_code == 201
    return response.json()["project_id"]


def wait_for_run(app: object, project_id: str, states: set[str], timeout: float = 5) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = asyncio.run(
            request(app, "GET", f"/api/projects/{project_id}/continuous-production")
        )
        assert response.status_code == 200
        payload = response.json()
        if payload["state"] in states:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"continuous production did not reach {states}")


def wait_for_ready_slice_count(
    app: object,
    project_id: str,
    ready_count: int,
    timeout: float = 5,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    ready_states = {"render_ready", "rendering", "playing", "complete"}
    while time.monotonic() < deadline:
        response = asyncio.run(
            request(app, "GET", f"/api/projects/{project_id}/continuous-production")
        )
        assert response.status_code == 200
        payload = response.json()
        if sum(item["state"] in ready_states for item in payload["slices"]) >= ready_count:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"continuous production did not prepare {ready_count} slices")


def test_continuous_production_reaches_render_ready_and_persists(tmp_path: Path) -> None:
    gateway = ContinuousProductionGateway()
    app = create_app(tmp_path, model_gateway=gateway)
    project_id = import_project(app, "第一章 初见\n萧炎说道：\"开始吧。\"\n旁白继续讲述。")

    started = asyncio.run(
        request(
            app,
            "POST",
            f"/api/projects/{project_id}/continuous-production",
            json={"emotion_policy": "skip", "prefetch_slices": 1, "auto_play": False},
        )
    )

    assert started.status_code == 202
    completed = wait_for_run(app, project_id, {"render_ready", "complete"})
    assert completed["slices"][0]["state"] == "render_ready"
    assert completed["slices"][0]["segment_count"] > 0
    assert completed["settings"]["emotion_policy"] == "skip"
    assert completed["settings"]["rvc_stability_policy"] == "skip"
    assert gateway.reference_calls > 0
    assert (tmp_path / "outputs" / "projects" / project_id / "continuous_production.json").is_file()

    restored_app = create_app(tmp_path, model_gateway=ContinuousProductionGateway())
    restored = asyncio.run(
        request(restored_app, "GET", f"/api/projects/{project_id}/continuous-production")
    )
    assert restored.status_code == 200
    assert restored.json()["run_id"] == completed["run_id"]
    assert restored.json()["slices"][0]["state"] == "render_ready"


def test_continuous_rvc_policy_waits_for_reference_review_without_blocking_render(tmp_path: Path) -> None:
    app = create_app(tmp_path, model_gateway=ContinuousProductionGateway())
    project_id = import_project(app, "第一章 初见\n萧炎说道：\"开始吧。\"\n旁白继续讲述。")

    started = asyncio.run(
        request(
            app,
            "POST",
            f"/api/projects/{project_id}/continuous-production",
            json={
                "emotion_policy": "skip",
                "rvc_stability_policy": "prepare_candidates",
                "prefetch_slices": 1,
                "auto_play": False,
            },
        )
    )

    assert started.status_code == 202
    ready = wait_for_run(app, project_id, {"render_ready", "complete"})
    assert ready["slices"][0]["state"] == "render_ready"
    assert ready["settings"]["rvc_stability_policy"] == "prepare_candidates"
    assert ready["rvc_tasks"]
    assert {task["status"] for task in ready["rvc_tasks"]} <= {"waiting_reference", "skipped", "deferred"}
    assert not list((tmp_path / "outputs" / "rvc" / "jobs").glob("*.json"))


def test_accepting_reference_starts_deferred_rvc_preparation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(continuous_production_module, "RVC_FOREGROUND_GRACE_SECONDS", 0)
    app = create_app(
        tmp_path,
        model_gateway=ContinuousProductionGateway(),
        rvc_runner=ContinuousRvcRunner(),
    )
    project_id = import_project(app, "第一章 初见\n萧炎说道：\"开始吧。\"\n旁白继续讲述。")
    base = f"/api/projects/{project_id}/continuous-production"

    started = asyncio.run(
        request(
            app,
            "POST",
            base,
            json={"emotion_policy": "skip", "rvc_stability_policy": "prepare_candidates"},
        )
    )
    assert started.status_code == 202
    ready = wait_for_run(app, project_id, {"render_ready", "complete"})
    assert ready["rvc_tasks"]

    preview = asyncio.run(request(app, "GET", f"/api/projects/{project_id}/preparation/preview")).json()
    reference = next(
        item
        for item in preview["reference_plan"]["items"]
        if item["selection_mode"] == "narrator_default" and item["audio_versions"]
    )
    version_id = reference["active_audio_version_id"]
    reviewed = asyncio.run(
        request(
            app,
            "POST",
            f"/api/projects/{project_id}/references/{reference['reference_id']}/audio/{version_id}/review",
            json={"decision": "accepted"},
        )
    )
    assert reviewed.status_code == 200

    deadline = time.monotonic() + 3
    task: dict[str, object] | None = None
    while time.monotonic() < deadline:
        payload = asyncio.run(request(app, "GET", base)).json()
        task = next(
            (item for item in payload["rvc_tasks"] if item["reference_id"] == reference["reference_id"]),
            None,
        )
        if task and task["training_job_id"]:
            break
        time.sleep(0.02)
    assert task is not None
    assert task["training_job_id"]

    refreshed = asyncio.run(request(app, "GET", base))
    assert refreshed.status_code == 200
    refreshed_task = next(
        item for item in refreshed.json()["rvc_tasks"] if item["reference_id"] == reference["reference_id"]
    )
    assert refreshed_task["training_job_id"] == task["training_job_id"]


def test_repeated_start_reuses_active_run_and_existing_assets(tmp_path: Path) -> None:
    gateway = ContinuousProductionGateway()
    app = create_app(tmp_path, model_gateway=gateway)
    project_id = import_project(app, "第一章\n萧炎说道：\"继续。\"")
    path = f"/api/projects/{project_id}/continuous-production"

    first = asyncio.run(request(app, "POST", path, json={"emotion_policy": "skip"}))
    assert first.status_code == 202
    completed = wait_for_run(app, project_id, {"render_ready", "complete"})
    reference_calls = gateway.reference_calls

    second = asyncio.run(request(app, "POST", path, json={"emotion_policy": "skip"}))
    assert second.status_code == 202
    assert second.json()["run_id"] == completed["run_id"]
    time.sleep(0.05)
    assert gateway.reference_calls == reference_calls


def test_repeated_start_rebuilds_deleted_preparation_artifacts(tmp_path: Path) -> None:
    gateway = ContinuousProductionGateway()
    app = create_app(tmp_path, model_gateway=gateway)
    project_id = import_project(app, "第一章\n萧炎说道：\"重新生成导演文件。\"")
    path = f"/api/projects/{project_id}/continuous-production"

    first = asyncio.run(request(app, "POST", path, json={"emotion_policy": "skip"}))
    assert first.status_code == 202
    completed = wait_for_run(app, project_id, {"render_ready", "complete"})

    deleted = asyncio.run(request(app, "DELETE", f"/api/projects/{project_id}/director"))
    assert deleted.status_code == 200
    assert deleted.json()["director_doc"] is None

    restarted = asyncio.run(request(app, "POST", path, json={"emotion_policy": "skip"}))
    assert restarted.status_code == 202
    assert restarted.json()["run_id"] != completed["run_id"]
    rebuilt = wait_for_run(app, project_id, {"render_ready", "complete"})
    assert rebuilt["run_id"] == restarted.json()["run_id"]

    preview = asyncio.run(request(app, "GET", f"/api/projects/{project_id}/preparation/preview"))
    assert preview.status_code == 200
    assert preview.json()["director_doc"] is not None


def test_continuous_production_pause_resume_and_cancel_commands_are_idempotent(tmp_path: Path) -> None:
    app = create_app(tmp_path, model_gateway=ContinuousProductionGateway())
    project_id = import_project(app, "第一章\n萧炎说道：\"测试控制。\"")
    base = f"/api/projects/{project_id}/continuous-production"

    missing_pause = asyncio.run(request(app, "POST", f"{base}/pause"))
    assert missing_pause.status_code == 404

    started = asyncio.run(request(app, "POST", base, json={"emotion_policy": "skip"}))
    assert started.status_code == 202
    paused = asyncio.run(request(app, "POST", f"{base}/pause"))
    assert paused.status_code == 200
    assert paused.json()["state"] in {"pausing", "paused", "render_ready"}

    resumed = asyncio.run(request(app, "POST", f"{base}/resume"))
    assert resumed.status_code == 200
    assert resumed.json()["run_id"] == started.json()["run_id"]

    cancelled = asyncio.run(request(app, "POST", f"{base}/cancel"))
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] in {"cancelled", "render_ready"}


def test_long_form_continuous_production_only_prepares_the_rolling_window(tmp_path: Path) -> None:
    gateway = ContinuousProductionGateway()
    app = create_app(tmp_path, model_gateway=gateway)
    paragraph = "萧炎说道：\"继续前进。\"旁白描述众人穿过长街，远处灯火仍然清晰。\n"
    project_id = import_project(app, paragraph * 700)

    settings = asyncio.run(
        request(
            app,
            "PATCH",
            f"/api/projects/{project_id}/analysis-settings",
            json={
                "mode": "characters",
                "long_text_threshold": 20_000,
                "characters_per_batch": 10_000,
                "parallelism": 2,
            },
        )
    )
    assert settings.status_code == 200

    started = asyncio.run(
        request(
            app,
            "POST",
            f"/api/projects/{project_id}/continuous-production",
            json={"emotion_policy": "skip", "prefetch_slices": 1, "auto_play": False},
        )
    )
    assert started.status_code == 202
    first_ready = wait_for_run(app, project_id, {"render_ready"}, timeout=10)
    assert first_ready["slices"][0]["state"] == "render_ready"

    run = wait_for_ready_slice_count(app, project_id, 2, timeout=10)

    assert len(run["slices"]) >= 3
    assert [item["state"] for item in run["slices"][:2]] == ["render_ready", "render_ready"]
    assert run["slices"][2]["state"] not in {"render_ready", "rendering", "playing", "complete"}
    assert run["slices"][2]["segment_count"] == 0


def test_long_form_releases_first_slice_before_scanning_the_next_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    second_scan_started = threading.Event()
    release_second_scan = threading.Event()
    original_scan = PreparationService._scan_candidates

    def blocking_scan(service: PreparationService, text: str):
        if "第二切片标记" in text:
            second_scan_started.set()
            assert release_second_scan.wait(5)
        return original_scan(service, text)

    monkeypatch.setattr(PreparationService, "_scan_candidates", blocking_scan)
    app = create_app(tmp_path, model_gateway=ContinuousProductionGateway())
    first = "萧炎说道：\"第一切片继续。\"第一切片标记，长街灯火保持明亮。\n" * 420
    second = "药老说道：\"第二切片继续。\"第二切片标记，远处风声逐渐平静。\n" * 420
    third = "萧炎说道：\"第三切片继续。\"第三切片标记，众人继续向前。\n" * 420
    project_id = import_project(app, first + second + third)
    settings = asyncio.run(
        request(
            app,
            "PATCH",
            f"/api/projects/{project_id}/analysis-settings",
            json={
                "mode": "characters",
                "long_text_threshold": 20_000,
                "characters_per_batch": 10_000,
                "parallelism": 1,
            },
        )
    )
    assert settings.status_code == 200

    started = asyncio.run(
        request(
            app,
            "POST",
            f"/api/projects/{project_id}/continuous-production",
            json={"emotion_policy": "skip", "prefetch_slices": 1, "auto_play": False},
        )
    )
    assert started.status_code == 202
    first_ready = wait_for_run(app, project_id, {"render_ready"}, timeout=10)
    assert first_ready["slices"][0]["state"] == "render_ready"
    assert first_ready["slices"][0]["candidate_count"] > 0
    assert second_scan_started.wait(5)

    blocked_view = asyncio.run(
        request(app, "GET", f"/api/projects/{project_id}/continuous-production")
    ).json()
    assert blocked_view["slices"][0]["state"] == "render_ready"
    assert blocked_view["slices"][1]["state"] not in {"render_ready", "rendering", "playing", "complete"}
    release_second_scan.set()
    resumed = wait_for_ready_slice_count(app, project_id, 2, timeout=10)
    assert resumed["slices"][1]["reused_character_count"] >= 1


def test_fifty_chapter_slice_releases_partial_director_window_before_slice_finishes(tmp_path: Path) -> None:
    analyzer = BlockingDirectorMicroBatchAnalyzer()
    app = create_app(
        tmp_path,
        model_gateway=ContinuousProductionGateway(),
        voice_analyzer=analyzer,
    )
    chapters = []
    for chapter in range(1, 51):
        dialogue = "\n".join(f'“第{chapter}章第{line}句现在由谁来说？”' for line in range(1, 9))
        narration = "长街上的风声逐渐平静，远处灯火仍然明亮。" * 80
        chapters.append(f"第{chapter:03d}章 测试章节\n{dialogue}\n{narration}")
    project_id = import_project(app, "\n".join(chapters))
    settings = asyncio.run(
        request(
            app,
            "PATCH",
            f"/api/projects/{project_id}/analysis-settings",
            json={
                "mode": "chapters",
                "long_text_threshold": 20_000,
                "chapters_per_batch": 50,
                "parallelism": 1,
            },
        )
    )
    assert settings.status_code == 200

    started = asyncio.run(
        request(
            app,
            "POST",
            f"/api/projects/{project_id}/continuous-production",
            json={"emotion_policy": "skip", "prefetch_slices": 1, "auto_play": False},
        )
    )
    assert started.status_code == 202
    assert analyzer.second_director_batch_started.wait(5)
    try:
        partial = wait_for_run(app, project_id, {"render_ready"}, timeout=2)
    finally:
        analyzer.release_second_director_batch.set()

    assert len(partial["slices"]) == 1
    assert partial["slices"][0]["chapter_start"] == 1
    assert partial["slices"][0]["chapter_end"] == 50
    assert partial["slices"][0]["segment_count"] > 0


def test_long_form_releases_first_slice_then_profiles_only_new_background_characters(tmp_path: Path) -> None:
    analyzer = BlockingWindowVoiceAnalyzer()
    app = create_app(
        tmp_path,
        model_gateway=ContinuousProductionGateway(),
        voice_analyzer=analyzer,
    )
    first = ("萧炎说道：\"第一切片继续前进。\"远处灯火保持明亮。\n" * 420)
    second = ("药老说道：\"第二切片放慢脚步。\"长街上的风逐渐平静。\n" * 420)
    third = ("萧炎说道：\"第三切片继续前进。\"众人越过安静长街。\n" * 420)
    project_id = import_project(app, first + second + third)
    settings = asyncio.run(
        request(
            app,
            "PATCH",
            f"/api/projects/{project_id}/analysis-settings",
            json={
                "mode": "characters",
                "long_text_threshold": 20_000,
                "characters_per_batch": 10_000,
                "parallelism": 1,
            },
        )
    )
    assert settings.status_code == 200

    started = asyncio.run(
        request(
            app,
            "POST",
            f"/api/projects/{project_id}/continuous-production",
            json={"emotion_policy": "skip", "prefetch_slices": 2, "auto_play": False},
        )
    )
    assert started.status_code == 202
    first_ready = wait_for_run(app, project_id, {"render_ready"}, timeout=10)

    assert first_ready["slices"][0]["state"] == "render_ready"
    assert analyzer.later_character_started.wait(5)
    assert analyzer.calls.count("萧炎") == 1
    preview_responses: list[httpx.Response] = []
    preview_thread = threading.Thread(
        target=lambda: preview_responses.append(
            asyncio.run(
                request(app, "GET", f"/api/projects/{project_id}/preparation/preview")
            )
        ),
        daemon=True,
    )
    preview_thread.start()
    preview_thread.join(0.5)
    preview_was_blocked = preview_thread.is_alive()
    analyzer.release_later_character.set()
    preview_thread.join(5)

    assert not preview_was_blocked, "后台角色画像不能持有产物锁并阻塞准备预览"
    assert preview_responses and preview_responses[0].status_code == 200
    all_ready = wait_for_ready_slice_count(app, project_id, 3, timeout=10)

    assert [item["state"] for item in all_ready["slices"][:3]] == [
        "render_ready",
        "render_ready",
        "render_ready",
    ]
    assert analyzer.calls.count("萧炎") == 1
    assert analyzer.calls.count("药老") == 1
