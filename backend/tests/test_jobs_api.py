from __future__ import annotations

import asyncio
import json
import struct
import threading
import time
import wave
from pathlib import Path

import httpx
import pytest

from app.jobs import HttpModelGateway, ModelGateway, PcmAudioStream
from app.loudness import LoudnessMetrics, ProgramLoudnessPolicy
from app.main import create_app
from app.production import QualityRenderOptions


def wav_bytes(sample_rate: int = 16_000) -> bytes:
    output = bytearray()
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * 160)
    output.extend(buffer.getvalue())
    return bytes(output)


class FakeModelGateway(ModelGateway):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.quality_options: list[QualityRenderOptions] = []
        self.quality_reference_texts: list[str | None] = []

    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        self.calls.append(("voxcpm_reference", text, voice_prompt))
        return wav_bytes()

    def generate_fast(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        self.calls.append(("fast_render", text, f"{voice_id}:{speed}"))
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
        self.calls.append((quality_model, text, str(reference_audio_path)))
        self.quality_options.append(render_options or QualityRenderOptions())
        self.quality_reference_texts.append(reference_text)
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
        self.calls.append((quality_model, text, str(reference_audio_path)))
        self.quality_options.append(render_options or QualityRenderOptions())
        self.quality_reference_texts.append(reference_text)
        audio = b"\x00\x00" * 320
        return PcmAudioStream(
            sample_rate=16_000,
            channels=1,
            sample_width=2,
            chunks=iter((audio[:240], audio[240:])),
            close=lambda: None,
        )

    def runtime_status(self) -> dict[str, object]:
        return {
            "launcher_managed": True,
            "services": {
                "voxcpm2": {"status": "ready", "url": "http://127.0.0.1:9881"},
                "gpt_sovits": {"status": "ready", "url": "http://127.0.0.1:9880"},
            },
        }


class FakeLoudnessProcessor:
    def __init__(self) -> None:
        self.segment_policies: list[ProgramLoudnessPolicy] = []
        self.program_policies: list[ProgramLoudnessPolicy] = []
        self.recorded: list[LoudnessMetrics] = []

    def process_segment_bytes(
        self,
        audio: bytes,
        policy: ProgramLoudnessPolicy,
    ) -> tuple[bytes, LoudnessMetrics]:
        self.segment_policies.append(policy.model_copy(deep=True))
        return audio, LoudnessMetrics(
            processor="fake_loudness",
            status="corrected",
            input_lufs=-22.0,
            output_lufs=policy.target_lufs,
            input_true_peak_dbtp=-6.0,
            output_true_peak_dbtp=policy.true_peak_dbtp,
            loudness_range_lu=policy.target_lra,
            applied_gain_db=min(4.0, policy.max_segment_gain_db),
        )

    def normalize_program_file(
        self,
        source: Path,
        target: Path,
        policy: ProgramLoudnessPolicy,
    ) -> LoudnessMetrics:
        self.program_policies.append(policy.model_copy(deep=True))
        target.write_bytes(source.read_bytes())
        return LoudnessMetrics(
            processor="fake_loudness",
            status="corrected",
            input_lufs=-20.0,
            output_lufs=policy.target_lufs,
            input_true_peak_dbtp=-4.0,
            output_true_peak_dbtp=policy.true_peak_dbtp,
            loudness_range_lu=policy.target_lra,
            applied_gain_db=2.0,
            final_program_pass=True,
        )

    def rolling_gain_db(self, policy: ProgramLoudnessPolicy, program_id: str | None = None) -> float:
        return min(1.25, policy.max_segment_gain_db)

    def record(self, metrics: LoudnessMetrics | None, program_id: str | None = None) -> None:
        if metrics is not None:
            self.recorded.append(metrics.model_copy(deep=True))


class BlockingQualityGateway(FakeModelGateway):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def generate_quality(
        self,
        text: str,
        reference_audio_path: Path,
        quality_model: str,
        emotion_description: str | None = None,
        render_options: QualityRenderOptions | None = None,
        reference_text: str | None = None,
    ) -> bytes:
        self.calls.append((quality_model, text, str(reference_audio_path)))
        self.quality_options.append(render_options or QualityRenderOptions())
        self.quality_reference_texts.append(reference_text)
        self.started.set()
        if not self.release.wait(5):
            raise RuntimeError("test gateway was not released")
        return wav_bytes()


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


def decode_stream_frames(content: bytes) -> list[tuple[int, bytes]]:
    frames: list[tuple[int, bytes]] = []
    position = 0
    while position < len(content):
        assert len(content) - position >= 5
        frame_type, payload_size = struct.unpack_from(">BI", content, position)
        position += 5
        payload = content[position : position + payload_size]
        assert len(payload) == payload_size
        position += payload_size
        frames.append((frame_type, payload))
    return frames


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


def test_fast_route_job_uses_lightweight_voice_and_route_cache(tmp_path: Path) -> None:
    gateway = FakeModelGateway()
    app = create_app(tmp_path, model_gateway=gateway)

    queued = asyncio.run(
        request(
            app,
            "POST",
            "/api/jobs",
            json={
                "kind": "fast_render",
                "project_id": "project-fast",
                "segment_id": "segment-001",
                "character_id": "character-xiao-yan",
                "text": "先确认方向，再继续向前。",
                "fast_voice_id": "bazong",
                "fast_speed": 1.15,
                "fast_rvc_enabled": False,
            },
        )
    )

    assert queued.status_code == 202
    completed = wait_for_job(app, queued.json()["job_id"])
    assert completed["status"] == "complete"
    assert completed["kind"] == "fast_render"
    assert completed["fast_voice_id"] == "bazong"
    assert completed["fast_speed"] == 1.15
    assert gateway.calls[0] == ("fast_render", "先确认方向，再继续向前。", "bazong:1.15")

    merged = asyncio.run(
        request(
            app,
            "POST",
            "/api/projects/project-fast/fast/merge",
            json={"job_ids": [completed["job_id"]]},
        )
    )
    assert merged.status_code == 200
    assert merged.json()["segment_count"] == 1

    deleted = asyncio.run(
        request(
            app,
            "POST",
            "/api/projects/project-fast/fast/cache/delete",
            json={"job_ids": [completed["job_id"]]},
        )
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted_count"] == 1


def test_fast_route_merge_resamples_mixed_rvc_and_tts_outputs(tmp_path: Path) -> None:
    gateway = FakeModelGateway()
    app = create_app(tmp_path, model_gateway=gateway)
    completed_jobs: list[dict[str, object]] = []
    for index in range(2):
        queued = asyncio.run(
            request(
                app,
                "POST",
                "/api/jobs",
                json={
                    "kind": "fast_render",
                    "project_id": "project-fast-mixed",
                    "segment_id": f"segment-{index}",
                    "text": f"Fast route segment {index}",
                    "fast_voice_id": "bazong",
                },
            )
        )
        completed_jobs.append(wait_for_job(app, queued.json()["job_id"]))

    first_output = tmp_path / str(completed_jobs[0]["output_url"]).removeprefix("/media/")
    first_output.write_bytes(wav_bytes(40_000))
    merged = asyncio.run(
        request(
            app,
            "POST",
            "/api/projects/project-fast-mixed/fast/merge",
            json={"job_ids": [job["job_id"] for job in completed_jobs]},
        )
    )

    assert merged.status_code == 200
    merged_output = tmp_path / merged.json()["output_url"].removeprefix("/media/")
    with wave.open(str(merged_output), "rb") as audio:
        assert audio.getframerate() == 40_000
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getnframes() == 560


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
    assert updated["active_audio_version_id"] == completed["job_id"]
    assert updated["audio_versions"][-1]["audio_url"] == completed["output_url"]
    assert updated["audio_versions"][-1]["decision"] == "provisional"

    reviewed = asyncio.run(
        request(
            app,
            "POST",
            f"/api/projects/{project_id}/references/{reference['reference_id']}/audio/{completed['job_id']}/review",
            json={"decision": "accepted"},
        )
    )
    assert reviewed.status_code == 200
    accepted = next(
        item for item in reviewed.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert accepted["audio_versions"][-1]["decision"] == "accepted"


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
                "reference_text": "雨后的长街渐渐安静下来。",
                "quality_model": "gpt_sovits_v4",
                "segment_id": "s001",
                "character_id": "xiao_yan",
                "project_id": "project-quality",
                "render_options": {"top_k": 33, "seed": 7},
            },
        )
    )

    completed = wait_for_job(app, response.json()["job_id"])
    assert completed["status"] == "complete"
    assert gateway.calls[0] == ("gpt_sovits_v4", "开始吧。", str(reference.resolve()))
    assert gateway.quality_options[0].top_k == 33
    assert gateway.quality_options[0].seed == 7
    assert completed["reference_audio_url"] == "/media/voice-samples/curated/reference.wav"

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


def test_quality_job_uses_reused_audio_stored_inside_project_assets(tmp_path: Path) -> None:
    reference = tmp_path / "outputs" / "projects" / "project-quality" / "assets" / "references" / "hero" / "reused.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(wav_bytes())
    gateway = FakeModelGateway()
    app = create_app(tmp_path, model_gateway=gateway)
    reference_url = "/media/outputs/projects/project-quality/assets/references/hero/reused.wav"

    response = asyncio.run(
        request(
            app,
            "POST",
            "/api/jobs",
            json={
                "kind": "quality_render",
                "project_id": "project-quality",
                "reference_id": "reference-hero",
                "segment_id": "segment-reused",
                "character_id": "hero",
                "text": "使用复用声线。",
                "reference_audio_url": reference_url,
            },
        )
    )

    assert response.status_code == 202
    completed = wait_for_job(app, response.json()["job_id"])
    assert completed["status"] == "complete"
    assert completed["reference_audio_url"] == reference_url
    assert gateway.calls[0][2] == str(reference.resolve())


def test_quality_loudness_keeps_raw_audio_reprocesses_and_normalizes_merge(tmp_path: Path) -> None:
    reference = tmp_path / "assets" / "voice_samples" / "reference.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(wav_bytes())
    loudness = FakeLoudnessProcessor()
    app = create_app(tmp_path, model_gateway=FakeModelGateway(), loudness_processor=loudness)  # type: ignore[arg-type]

    queued = asyncio.run(
        request(
            app,
            "POST",
            "/api/jobs",
            json={
                "kind": "quality_render",
                "project_id": "project-loudness",
                "segment_id": "segment-001",
                "character_id": "narrator",
                "text": "A program loudness test.",
                "reference_audio_url": "/media/voice-samples/reference.wav",
            },
        )
    )
    completed = wait_for_job(app, queued.json()["job_id"])

    assert completed["status"] == "complete"
    assert completed["raw_output_url"].startswith("/media/outputs/audio/raw/")
    assert completed["loudness_metrics"]["output_lufs"] == -18.0
    raw = asyncio.run(request(app, "GET", completed["raw_output_url"]))
    processed = asyncio.run(request(app, "GET", completed["output_url"]))
    assert raw.status_code == 200
    assert raw.content == processed.content

    processed_path = tmp_path / completed["output_url"].removeprefix("/media/")
    processed_path.write_bytes(b"stale derivative")
    settings = asyncio.run(
        request(
            app,
            "PATCH",
            "/api/production/settings",
            json={"loudness_policy": {"target_lufs": -16.0}},
        )
    )
    assert settings.status_code == 200
    reprocessed = asyncio.run(
        request(
            app,
            "POST",
            "/api/projects/project-loudness/quality/loudness/reprocess",
            json={"job_ids": [completed["job_id"]]},
        )
    )
    assert reprocessed.status_code == 200
    assert reprocessed.json()[0]["loudness_metrics"]["output_lufs"] == -16.0
    assert processed_path.read_bytes()[:4] == b"RIFF"
    assert loudness.segment_policies[-1].target_lufs == -16.0

    merged = asyncio.run(
        request(
            app,
            "POST",
            "/api/projects/project-loudness/quality/merge",
            json={"job_ids": [completed["job_id"]]},
        )
    )
    assert merged.status_code == 200
    assert merged.json()["loudness_metrics"]["final_program_pass"] is True
    assert merged.json()["loudness_metrics"]["output_lufs"] == -16.0
    assert loudness.program_policies[-1].target_lufs == -16.0


def test_v3_v4_stream_pcm_and_persist_wav_cache(tmp_path: Path) -> None:
    reference = tmp_path / "assets" / "voice_samples" / "curated" / "reference.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(wav_bytes())
    gateway = FakeModelGateway()
    app = create_app(tmp_path, model_gateway=gateway)
    model_ids = ("gpt_sovits_v3", "gpt_sovits_v4")

    for index, model_id in enumerate(model_ids, start=1):
        response = asyncio.run(
            request(
                app,
                "POST",
                "/api/jobs/quality-stream",
                json={
                    "kind": "quality_render",
                    "project_id": "project-quality",
                    "segment_id": f"segment-{index:03d}",
                    "character_id": "character-xiao-yan",
                        "text": f"第 {index} 句流式测试。",
                        "reference_audio_url": "/media/voice-samples/curated/reference.wav",
                        "reference_text": "雨后的长街渐渐安静下来。",
                        "quality_model": model_id,
                },
            )
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-zw-pcm-stream")
        assert response.headers["x-zw-stream-protocol"] == "1"
        job_id = response.headers["x-zw-stream-job-id"]
        frames = decode_stream_frames(response.content)
        metadata = json.loads(next(payload for frame_type, payload in frames if frame_type == 1))
        audio = b"".join(payload for frame_type, payload in frames if frame_type == 2)
        assert metadata == {
            "job_id": job_id,
            "sample_rate": 16_000,
            "channels": 1,
            "sample_width": 2,
            "format": "pcm_s16le",
            "rolling_gain_db": 0.0,
        }
        assert len(audio) == 640

        completed = asyncio.run(request(app, "GET", f"/api/jobs/{job_id}")).json()
        assert completed["status"] == "complete"
        assert completed["streaming"] is True
        assert completed["quality_model"] == model_id
        cached = asyncio.run(request(app, "GET", str(completed["output_url"])))
        assert cached.status_code == 200
        assert cached.content[:4] == b"RIFF"

    assert [call[0] for call in gateway.calls] == list(model_ids)
    assert gateway.quality_reference_texts == ["雨后的长街渐渐安静下来。"] * 2


def test_quality_stream_accepts_only_v3_v4_and_requires_reference_text(tmp_path: Path) -> None:
    reference = tmp_path / "assets" / "voice_samples" / "reference.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(wav_bytes())
    app = create_app(tmp_path, model_gateway=FakeModelGateway())

    for model_id in ("gpt_sovits_v1", "gpt_sovits_v2_pro_plus", "indextts2"):
        response = asyncio.run(
            request(
                app,
                "POST",
                "/api/jobs/quality-stream",
                json={
                    "kind": "quality_render",
                    "text": "不应进入 V3/V4 新流。",
                    "reference_audio_url": "/media/voice-samples/reference.wav",
                    "quality_model": model_id,
                },
            )
        )
        assert response.status_code == 409

    missing_text = asyncio.run(
        request(
            app,
            "POST",
            "/api/jobs/quality-stream",
            json={
                "kind": "quality_render",
                "text": "V3 必须携带参考文本。",
                "reference_audio_url": "/media/voice-samples/reference.wav",
                "quality_model": "gpt_sovits_v3",
            },
        )
    )
    assert missing_text.status_code == 422


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


def test_gpt_sovits_stream_payload_uses_compatible_fragment_mode(tmp_path: Path) -> None:
    gateway = HttpModelGateway()
    options = QualityRenderOptions(chunk_length=60, batch_size=3, split_bucket=True)

    streaming = gateway._gpt_sovits_payload(
        "开始吧。",
        tmp_path / "reference.wav",
        options,
        "雨后的长街渐渐安静下来。",
        streaming=True,
    )
    regular = gateway._gpt_sovits_payload("开始吧。", tmp_path / "reference.wav", options, streaming=False)

    assert streaming["streaming_mode"] == 1
    assert streaming["prompt_text"] == "雨后的长街渐渐安静下来。"
    assert regular["streaming_mode"] == 0
    assert streaming["text_split_method"] == "cut5"
    assert streaming["batch_size"] == 3
    assert streaming["split_bucket"] is True


def test_http_gateway_switches_gpt_sovits_weights_for_the_selected_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_get(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("GET", url)
        weights_path = str((kwargs.get("params") or {}).get("weights_path"))  # type: ignore[union-attr]
        calls.append((url, weights_path))
        return httpx.Response(200, json={"message": "success"}, request=request)

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        calls.append((url, "audio"))
        return httpx.Response(200, content=wav_bytes(), request=request)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)

    audio = HttpModelGateway().generate_quality(
        "开始吧。",
        tmp_path / "reference.wav",
        "gpt_sovits_v4",
        "克制",
        QualityRenderOptions(top_k=21, top_p=0.82, seed=5),
        "雨后的长街渐渐安静下来。",
    )

    assert audio[:4] == b"RIFF"
    assert calls == [
        (
            "http://127.0.0.1:9880/set_sovits_weights",
            "GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth",
        ),
        ("http://127.0.0.1:9880/set_gpt_weights", "GPT_SoVITS/pretrained_models/s1v3.ckpt"),
        ("http://127.0.0.1:9880/tts", "audio"),
    ]


def test_quality_jobs_can_be_filtered_restored_and_merged(tmp_path: Path) -> None:
    reference = tmp_path / "assets" / "voice_samples" / "reference.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(wav_bytes())
    app = create_app(tmp_path, model_gateway=FakeModelGateway())
    completed_jobs: list[dict[str, object]] = []
    for segment_id, text in (("seg-001", "First line"), ("seg-002", "Second line")):
        queued = asyncio.run(
            request(
                app,
                "POST",
                "/api/jobs",
                json={
                    "kind": "quality_render",
                    "project_id": "project-quality",
                    "segment_id": segment_id,
                    "character_id": "narrator",
                    "text": text,
                    "reference_audio_url": "/media/voice-samples/reference.wav",
                },
            )
        )
        completed_jobs.append(wait_for_job(app, queued.json()["job_id"]))

    filtered = asyncio.run(
        request(app, "GET", "/api/jobs?project_id=project-quality&kind=quality_render&limit=10")
    )
    assert filtered.status_code == 200
    assert {item["segment_id"] for item in filtered.json()} == {"seg-001", "seg-002"}

    restored_app = create_app(tmp_path, model_gateway=FakeModelGateway())
    restored = asyncio.run(
        request(restored_app, "GET", "/api/jobs?project_id=project-quality&kind=quality_render&limit=10")
    )
    assert len(restored.json()) == 2

    merged = asyncio.run(
        request(
            restored_app,
            "POST",
            "/api/projects/project-quality/quality/merge",
            json={"job_ids": [item["job_id"] for item in completed_jobs]},
        )
    )
    assert merged.status_code == 200
    assert merged.json()["segment_count"] == 2
    assert merged.json()["duration_seconds"] > 0
    audio = asyncio.run(request(restored_app, "GET", merged.json()["output_url"]))
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"

    deleted = asyncio.run(
        request(
            restored_app,
            "POST",
            "/api/projects/project-quality/quality/cache/delete",
            json={"job_ids": [item["job_id"] for item in completed_jobs]},
        )
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted_count"] == 2
    assert deleted.json()["deleted_bytes"] > 0
    assert all(item["output_url"] is None for item in deleted.json()["deleted_jobs"])
    assert all(item["message"] == "生成缓存已删除" for item in deleted.json()["deleted_jobs"])
    for item in completed_jobs:
        assert asyncio.run(request(restored_app, "GET", str(item["output_url"]))).status_code == 404


def test_quality_queue_can_cancel_running_and_queued_jobs_before_restarting(tmp_path: Path) -> None:
    reference = tmp_path / "assets" / "voice_samples" / "reference.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(wav_bytes())
    gateway = BlockingQualityGateway()
    app = create_app(tmp_path, model_gateway=gateway)
    queued_jobs: list[dict[str, object]] = []

    for segment_id in ("seg-001", "seg-002", "seg-003"):
        response = asyncio.run(
            request(
                app,
                "POST",
                "/api/jobs",
                json={
                    "kind": "quality_render",
                    "project_id": "project-stream",
                    "segment_id": segment_id,
                    "text": segment_id,
                    "reference_audio_url": "/media/voice-samples/reference.wav",
                },
            )
        )
        assert response.status_code == 202
        queued_jobs.append(response.json())

    assert gateway.started.wait(1)
    cancelled = asyncio.run(
        request(app, "POST", "/api/projects/project-stream/quality/cancel")
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled_count"] == 3
    assert {job["status"] for job in cancelled.json()["cancelled_jobs"]} == {"cancelled"}
    gateway.release.set()
    time.sleep(0.05)
    for queued in queued_jobs:
        current = asyncio.run(request(app, "GET", f"/api/jobs/{queued['job_id']}"))
        assert current.json()["status"] == "cancelled"

    restarted = asyncio.run(
        request(
            app,
            "POST",
            "/api/jobs",
            json={
                "kind": "quality_render",
                "project_id": "project-stream",
                "segment_id": "seg-010",
                "text": "restart",
                "reference_audio_url": "/media/voice-samples/reference.wav",
            },
        )
    )
    assert wait_for_job(app, restarted.json()["job_id"])["status"] == "complete"
    assert [call[1] for call in gateway.calls] == ["seg-001", "restart"]


def test_emotion_variant_job_updates_the_project_emotion_plan(tmp_path: Path) -> None:
    gateway = FakeModelGateway()
    app = create_app(tmp_path, model_gateway=gateway)
    imported = asyncio.run(
        request(
            app,
            "POST",
            "/api/sources",
            files={"file": ("情绪任务.txt", "萧炎说道：\"开始吧。\"".encode(), "text/plain")},
        )
    ).json()
    project_id = imported["project_id"]
    asyncio.run(request(app, "POST", f"/api/projects/{project_id}/preparation", json={"action": "analyze"}))
    extracted = asyncio.run(
        request(app, "POST", f"/api/projects/{project_id}/preparation", json={"action": "extract_characters"})
    ).json()
    variant = next(
        item for item in extracted["emotion_plan"]["items"]
        if item["emotion_name"] == "愤怒" and item["selected"]
    )

    queued = asyncio.run(
        request(
            app,
            "POST",
            "/api/jobs",
            json={
                "kind": "emotion_variant",
                "project_id": project_id,
                "variant_id": variant["variant_id"],
                "character_id": variant["source_character_id"],
                "text": variant["reference_text"],
                "voice_prompt": variant["voice_prompt"],
            },
        )
    )

    assert queued.status_code == 202
    completed = wait_for_job(app, queued.json()["job_id"])
    assert completed["status"] == "complete"
    preview = asyncio.run(request(app, "GET", f"/api/projects/{project_id}/preparation/preview")).json()
    updated = next(item for item in preview["emotion_plan"]["items"] if item["variant_id"] == variant["variant_id"])
    assert updated["status"] == "generated"
    assert updated["audio_url"] == completed["output_url"]
