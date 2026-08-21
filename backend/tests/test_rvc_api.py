from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import subprocess
import time
import wave
from pathlib import Path

import httpx
import pytest

from app.main import create_app
from app.jobs import ModelGateway
from app.rvc import (
    RvcInferenceProfile,
    RvcService,
    RvcSettingsUpdate,
    RvcTrainingArtifact,
    RvcTrainingOptions,
    RvcTrainingSpec,
    WorkerRvcInferenceRunner,
)


def load_rvc_train_worker():
    worker_path = Path(__file__).parents[2] / "model_workers" / "rvc_train_worker.py"
    module_spec = importlib.util.spec_from_file_location("rvc_train_worker", worker_path)
    assert module_spec is not None and module_spec.loader is not None
    worker = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(worker)
    return worker


def test_rvc_training_defaults_and_epoch_progress_parser() -> None:
    worker = load_rvc_train_worker()

    options = RvcTrainingOptions()
    assert options.epochs == 20
    assert options.save_every_epochs == 5

    parsed = worker.parse_training_progress("Train Epoch: 3 [50%]", options.epochs)
    assert parsed is not None
    progress, epoch, log_line = parsed
    assert progress == 61
    assert epoch == 3
    assert log_line == "Train Epoch: 3 [50%]"

    assert worker.parse_training_progress("loading checkpoint", options.epochs) is None


def write_wav(path: Path, duration_seconds: float, sample_rate: int = 8_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes((1_200).to_bytes(2, "little", signed=True) * int(duration_seconds * sample_rate))


def write_reference_plan(workspace_root: Path, project_id: str, audio_url: str) -> None:
    project_root = workspace_root / "outputs" / "projects" / project_id
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "reference_plan.json").write_text(
        json.dumps(
            {
                "project_id": project_id,
                "items": [
                    {
                        "reference_id": "reference-xiao-yan",
                        "source_character_id": "character-xiao-yan",
                        "display_name": "萧炎",
                        "gender": "male",
                        "selected": True,
                        "audio_url": audio_url,
                        "active_audio_version_id": "reference-v1",
                        "audio_versions": [
                            {
                                "version_id": "reference-v1",
                                "audio_url": audio_url,
                                "source": "generated",
                                "decision": "accepted",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


async def request(
    workspace_root: Path,
    method: str,
    path: str,
    *,
    model_gateway: object | None = None,
    rvc_runner: object | None = None,
    rvc_inference_runner: object | None = None,
    **kwargs: object,
) -> httpx.Response:
    transport = httpx.ASGITransport(
        app=create_app(
            workspace_root,
            model_gateway=model_gateway,
            rvc_runner=rvc_runner,
            rvc_inference_runner=rvc_inference_runner,
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


async def request_app(application: object, method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def install_model_pair(workspace_root: Path, name: str = "萧炎") -> None:
    rvc_root = workspace_root / "models" / "vc_tools" / "rvc-webui"
    weights = rvc_root / "assets" / "weights"
    indices = rvc_root / "assets" / "indices"
    weights.mkdir(parents=True, exist_ok=True)
    indices.mkdir(parents=True, exist_ok=True)
    (weights / f"{name}.pth").write_bytes(b"model")
    (indices / f"added_IVF49_Flat_nprobe_1_{name}_v2.index").write_bytes(b"index")


def approve_model_route(
    service: RvcService,
    project_id: str,
    model_id: str,
    route: str,
) -> None:
    model = next(item for item in service.workspace(project_id).models if item.model_id == model_id)
    service._write_model_manifest(
        model.model_copy(
            update={
                "character_id": "character-xiao-yan",
                "status": "approved",
                "approved_routes": [route],
            }
        )
    )


def test_rvc_workspace_pairs_existing_weight_and_index(tmp_path: Path) -> None:
    install_model_pair(tmp_path)
    write_wav(tmp_path / "outputs" / "audio" / "jobs" / "sample.wav", 3)
    write_reference_plan(tmp_path, "project-demo", "/media/outputs/audio/jobs/sample.wav")

    response = asyncio.run(request(tmp_path, "GET", "/api/projects/project-demo/rvc/workspace"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_available"] is True
    assert payload["characters"][0]["display_name"] == "萧炎"
    assert payload["characters"][0]["material_count"] == 1
    assert payload["characters"][0]["material_duration_seconds"] == 3
    assert payload["models"][0]["index_path"].endswith("萧炎_v2.index")


def test_rvc_workspace_keeps_male_and_female_narrators_separate(tmp_path: Path) -> None:
    project_root = tmp_path / "outputs" / "projects" / "project-demo"
    project_root.mkdir(parents=True)
    (project_root / "reference_plan.json").write_text(
        json.dumps(
            {
                "project_id": "project-demo",
                "items": [
                    {"reference_id": "male", "source_character_id": "narrator", "display_name": "男旁白", "gender": "male", "selected": True, "audio_versions": []},
                    {"reference_id": "female", "source_character_id": "narrator", "display_name": "女旁白", "gender": "female", "selected": True, "audio_versions": []},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = asyncio.run(request(tmp_path, "GET", "/api/projects/project-demo/rvc/workspace"))

    assert response.status_code == 200
    assert [item["character_id"] for item in response.json()["characters"]] == [
        "narrator-male",
        "narrator-female",
    ]


def test_rvc_character_binding_and_quality_stability_are_persisted(tmp_path: Path) -> None:
    install_model_pair(tmp_path)
    write_wav(tmp_path / "outputs" / "audio" / "jobs" / "sample.wav", 3)
    write_reference_plan(tmp_path, "project-demo", "/media/outputs/audio/jobs/sample.wav")
    initial = asyncio.run(request(tmp_path, "GET", "/api/projects/project-demo/rvc/workspace")).json()
    model_id = initial["models"][0]["model_id"]

    rejected = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            "/api/projects/project-demo/rvc/settings",
            json={
                "character_id": "character-xiao-yan",
                "train_enabled": True,
                "selected_model_id": model_id,
                "stability_enabled": True,
            },
        )
    )
    assert rejected.status_code == 409
    assert "质量路线基准" in rejected.json()["detail"]

    service = RvcService(tmp_path)
    approve_model_route(service, "project-demo", model_id, "quality")
    service.close()

    bound = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            "/api/projects/project-demo/rvc/settings",
            json={
                "character_id": "character-xiao-yan",
                "train_enabled": True,
                "selected_model_id": model_id,
                "stability_enabled": True,
            },
        )
    )
    assert bound.status_code == 200

    enabled = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            "/api/projects/project-demo/rvc/settings",
            json={"quality_stability_enabled": True},
        )
    )
    assert enabled.status_code == 200
    assert enabled.json()["settings"]["quality_stability_enabled"] is True

    restored = asyncio.run(request(tmp_path, "GET", "/api/projects/project-demo/rvc/workspace"))
    character = restored.json()["characters"][0]
    assert character["train_enabled"] is True
    assert character["stability_enabled"] is True
    assert character["selected_model_id"] == model_id

    profile_changed = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/project-demo/rvc/models/{model_id}/profiles/quality",
            json={
                "profile": {
                    "preset": "custom",
                    "f0_method": "rmvpe",
                    "f0_up_key": 0,
                    "index_rate": 0.4,
                    "filter_radius": 3,
                    "resample_sr": 48000,
                    "rms_mix_rate": 0.2,
                    "protect": 0.45,
                }
            },
        )
    )
    assert profile_changed.status_code == 200
    changed_payload = profile_changed.json()
    changed_model = next(item for item in changed_payload["models"] if item["model_id"] == model_id)
    assert changed_model["status"] == "candidate"
    assert changed_model["approved_routes"] == []
    assert changed_payload["characters"][0]["stability_enabled"] is False
    assert changed_payload["settings"]["quality_stability_enabled"] is False


class FakeRvcRunner:
    def __init__(self) -> None:
        self.specs: list[RvcTrainingSpec] = []

    def run(
        self,
        spec: RvcTrainingSpec,
        progress: object,
        is_cancelled: object,
    ) -> RvcTrainingArtifact:
        self.specs.append(spec)
        progress(35, "正在提取音高与特征")
        spec.output_model_path.parent.mkdir(parents=True, exist_ok=True)
        spec.output_model_path.write_bytes(b"trained-model")
        spec.output_index_path.write_bytes(b"trained-index")
        progress(92, "正在整理模型资产")
        return RvcTrainingArtifact(
            model_path=spec.output_model_path,
            index_path=spec.output_index_path,
        )

    def cancel(self, job_id: str) -> None:
        return None

    def close(self) -> None:
        return None


class FakeRvcMaterialGateway(ModelGateway):
    def __init__(self) -> None:
        self.material_calls = 0

    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        raise AssertionError("VoxCPM2 material must not enter an anchored training set automatically")

    def generate_quality(self, *args: object, **kwargs: object) -> bytes:
        self.material_calls += 1
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(100)
            wav.writeframes((1_200).to_bytes(2, "little", signed=True) * 100 * 90)
        return output.getvalue()

    def runtime_status(self) -> dict[str, object]:
        return {}


def test_rvc_training_rejects_a_provisional_reference(tmp_path: Path) -> None:
    write_wav(tmp_path / "outputs" / "audio" / "jobs" / "sample.wav", 8)
    write_reference_plan(tmp_path, "project-demo", "/media/outputs/audio/jobs/sample.wav")
    plan_path = tmp_path / "outputs" / "projects" / "project-demo" / "reference_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["items"][0]["audio_versions"][0]["decision"] = "provisional"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    response = asyncio.run(
        request(
            tmp_path,
            "POST",
            "/api/projects/project-demo/rvc/training",
            rvc_runner=FakeRvcRunner(),
            json={"character_id": "character-xiao-yan"},
        )
    )

    assert response.status_code == 409
    assert "标准参考" in response.json()["detail"]


def test_rvc_training_auto_generates_missing_material_before_training(tmp_path: Path) -> None:
    write_wav(tmp_path / "outputs" / "audio" / "jobs" / "sample.wav", 8)
    write_reference_plan(tmp_path, "project-demo", "/media/outputs/audio/jobs/sample.wav")
    gateway = FakeRvcMaterialGateway()
    runner = FakeRvcRunner()
    application = create_app(tmp_path, model_gateway=gateway, rvc_runner=runner)

    submitted = asyncio.run(
        request_app(
            application,
            "POST",
            "/api/projects/project-demo/rvc/training",
            json={"character_id": "character-xiao-yan", "options": {"epochs": 80}},
        )
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]

    deadline = time.monotonic() + 2
    status_payload: dict[str, object] = {}
    while time.monotonic() < deadline:
        status_payload = asyncio.run(
            request_app(application, "GET", f"/api/rvc/jobs/{job_id}")
        ).json()
        if status_payload["status"] == "complete":
            break
        time.sleep(0.02)

    assert status_payload["status"] == "complete"
    assert status_payload["material_duration_seconds"] >= 180
    assert gateway.material_calls == 2
    assert len(runner.specs) == 1
    assert len(runner.specs[0].input_audio_paths) == 3
    assert runner.specs[0].experiment_name.startswith("zw_character-xiao-yan_")
    workspace = asyncio.run(
        request_app(application, "GET", "/api/projects/project-demo/rvc/workspace")
    ).json()
    character = workspace["characters"][0]
    assert character["training_ready"] is True
    assert character["train_enabled"] is True


class FakeRvcInferenceRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.profiles: list[RvcInferenceProfile] = []
        self.sample_value = 1_300
        self.error: str | None = None

    def run(
        self,
        model_path: Path,
        index_path: Path,
        input_path: Path,
        output_path: Path,
        profile: RvcInferenceProfile,
    ) -> None:
        self.calls += 1
        self.profiles.append(profile.model_copy(deep=True))
        if self.error:
            raise RuntimeError(self.error)
        audio = bytearray(input_path.read_bytes())
        if len(audio) >= 46:
            audio[-2:] = self.sample_value.to_bytes(2, "little", signed=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)

    def close(self) -> None:
        return None


def test_rvc_inference_uses_protobuf_compatibility_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_path = tmp_path / "model_workers" / "rvc_infer_worker.py"
    worker_path.parent.mkdir(parents=True)
    worker_path.write_text("", encoding="utf-8")
    runner = WorkerRvcInferenceRunner(tmp_path)
    runner.python_path = tmp_path / "python.exe"
    captured_environment: dict[str, str] = {}

    captured_command: dict[str, object] = {}

    class FakeStdin:
        def write(self, value: str) -> int:
            payload = json.loads(value)
            if payload.get("command") == "shutdown":
                return len(value)
            captured_command.update(payload)
            write_wav(Path(payload["output_path"]), 1)
            Path(payload["response_path"]).write_text(
                json.dumps({"ok": True, "request_id": payload["request_id"]}),
                encoding="utf-8",
            )
            return len(value)

        def flush(self) -> None:
            return None

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.returncode = None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.returncode = -9

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        captured_environment.update(kwargs["env"])
        captured_command["process_command"] = args[0]
        return FakeProcess()

    monkeypatch.setattr("app.rvc.subprocess.Popen", fake_popen)
    profile = RvcInferenceProfile(index_rate=0.42)
    runner.run(
        tmp_path / "model.pth",
        tmp_path / "model.index",
        tmp_path / "input.wav",
        tmp_path / "output.wav",
        profile,
    )
    runner.close()

    assert captured_environment["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] == "python"
    assert captured_environment["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] == "1"
    assert captured_command["profile"]["index_rate"] == 0.42
    assert "--serve" in captured_command["process_command"]


class FakeRvcPreviewGateway(ModelGateway):
    def __init__(self) -> None:
        self.voxcpm_calls: list[tuple[str, str]] = []
        self.quality_calls: list[tuple[str, Path, str]] = []

    @staticmethod
    def _audio(duration_seconds: float) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8_000)
            wav.writeframes((1_200).to_bytes(2, "little", signed=True) * int(8_000 * duration_seconds))
        return output.getvalue()

    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        self.voxcpm_calls.append((text, voice_prompt))
        return self._audio(1)

    def generate_fast(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        return self._audio(1.5)

    def generate_quality(
        self,
        text: str,
        reference_audio_path: Path,
        quality_model: str,
        *args: object,
        **kwargs: object,
    ) -> bytes:
        self.quality_calls.append((text, reference_audio_path, quality_model))
        return self._audio(1.5)

    def runtime_status(self) -> dict[str, object]:
        return {}


def test_rvc_preview_generates_baseline_and_processed_audio(tmp_path: Path) -> None:
    install_model_pair(tmp_path)
    sample_path = tmp_path / "outputs" / "audio" / "jobs" / "sample.wav"
    write_wav(sample_path, 3)
    write_reference_plan(tmp_path, "project-demo", "/media/outputs/audio/jobs/sample.wav")
    gateway = FakeRvcPreviewGateway()
    inference_runner = FakeRvcInferenceRunner()
    application = create_app(
        tmp_path,
        model_gateway=gateway,
        rvc_runner=FakeRvcRunner(),
        rvc_inference_runner=inference_runner,
    )
    workspace = asyncio.run(
        request_app(application, "GET", "/api/projects/project-demo/rvc/workspace")
    ).json()
    model_id = workspace["models"][0]["model_id"]
    bound = asyncio.run(
        request_app(
            application,
            "PATCH",
            "/api/projects/project-demo/rvc/settings",
            json={"character_id": "character-xiao-yan", "selected_model_id": model_id},
        )
    )
    assert bound.status_code == 200

    voxcpm = asyncio.run(
        request_app(
            application,
            "POST",
            "/api/projects/project-demo/rvc/preview",
            json={"character_id": "character-xiao-yan", "text": "试听文本", "source": "voxcpm2"},
        )
    )
    assert voxcpm.status_code == 200
    voxcpm_payload = voxcpm.json()
    assert voxcpm_payload["source_label"] == "VoxCPM2"
    assert voxcpm_payload["rvc_model_id"] == model_id
    assert gateway.voxcpm_calls[0][0] == "试听文本"
    base_audio = asyncio.run(request_app(application, "GET", voxcpm_payload["base_audio_url"]))
    rvc_audio = asyncio.run(request_app(application, "GET", voxcpm_payload["rvc_audio_url"]))
    assert base_audio.status_code == 200
    assert rvc_audio.status_code == 200
    assert base_audio.content != rvc_audio.content

    quality = asyncio.run(
        request_app(
            application,
            "POST",
            "/api/projects/project-demo/rvc/preview",
            json={
                "character_id": "character-xiao-yan",
                "text": "另一段试听文本",
                "source": "gpt_sovits_v2_pro_plus",
            },
        )
    )
    assert quality.status_code == 200
    assert quality.json()["source_label"] == "GPT-SoVITS V2 Pro Plus"
    assert gateway.quality_calls[0][2] == "gpt_sovits_v2_pro_plus"
    assert gateway.quality_calls[0][1] == sample_path.resolve()
    assert inference_runner.calls == 2


def test_rvc_route_requires_complete_benchmark_and_human_approval(tmp_path: Path) -> None:
    install_model_pair(tmp_path)
    sample_path = tmp_path / "outputs" / "audio" / "jobs" / "sample.wav"
    write_wav(sample_path, 3)
    write_reference_plan(tmp_path, "project-demo", "/media/outputs/audio/jobs/sample.wav")
    application = create_app(
        tmp_path,
        model_gateway=FakeRvcPreviewGateway(),
        rvc_runner=FakeRvcRunner(),
        rvc_inference_runner=FakeRvcInferenceRunner(),
    )
    workspace = asyncio.run(
        request_app(application, "GET", "/api/projects/project-demo/rvc/workspace")
    ).json()
    model_id = workspace["models"][0]["model_id"]

    rejected = asyncio.run(
        request_app(
            application,
            "PATCH",
            "/api/projects/project-demo/rvc/settings",
            json={
                "character_id": "character-xiao-yan",
                "selected_model_id": model_id,
                "stability_enabled": True,
            },
        )
    )
    assert rejected.status_code == 409

    submitted = asyncio.run(
        request_app(
            application,
            "POST",
            "/api/projects/project-demo/rvc/benchmarks",
            json={
                "character_id": "character-xiao-yan",
                "model_id": model_id,
                "route": "quality",
            },
        )
    )
    assert submitted.status_code == 202
    benchmark_id = submitted.json()["benchmark_id"]
    deadline = time.monotonic() + 5
    benchmark: dict[str, object] = {}
    while time.monotonic() < deadline:
        benchmark = asyncio.run(
            request_app(application, "GET", f"/api/rvc/benchmarks/{benchmark_id}")
        ).json()
        if benchmark["status"] in {"complete", "failed"}:
            break
        time.sleep(0.02)

    assert benchmark["status"] == "complete"
    assert benchmark["automatic_pass"] is True
    assert len(benchmark["samples"]) == 24

    approved = asyncio.run(
        request_app(
            application,
            "POST",
            f"/api/rvc/benchmarks/{benchmark_id}/review",
            json={
                "approved": True,
                "preference_percent": 78,
                "identity_improved": True,
                "intelligibility_preserved": True,
                "expression_preserved": True,
                "notes": "整组盲听通过",
            },
        )
    )
    assert approved.status_code == 200
    approved_model = next(item for item in approved.json()["models"] if item["model_id"] == model_id)
    assert approved_model["status"] == "approved"
    assert approved_model["approved_routes"] == ["quality"]

    bound = asyncio.run(
        request_app(
            application,
            "PATCH",
            "/api/projects/project-demo/rvc/settings",
            json={
                "character_id": "character-xiao-yan",
                "selected_model_id": model_id,
                "stability_enabled": True,
            },
        )
    )
    assert bound.status_code == 200
    enabled = asyncio.run(
        request_app(
            application,
            "PATCH",
            "/api/projects/project-demo/rvc/settings",
            json={"quality_stability_enabled": True},
        )
    )
    assert enabled.status_code == 200
    assert enabled.json()["settings"]["quality_stability_enabled"] is True


    changed_reference = tmp_path / "outputs" / "audio" / "jobs" / "changed-reference.wav"
    write_wav(changed_reference, 3)
    changed_audio = bytearray(changed_reference.read_bytes())
    changed_audio[-2:] = (1_500).to_bytes(2, "little", signed=True)
    changed_reference.write_bytes(changed_audio)
    write_reference_plan(
        tmp_path,
        "project-demo",
        "/media/outputs/audio/jobs/changed-reference.wav",
    )
    stale_workspace = asyncio.run(
        request_app(application, "GET", "/api/projects/project-demo/rvc/workspace")
    ).json()
    assert stale_workspace["characters"][0]["quality_approved"] is False
    assert stale_workspace["characters"][0]["stability_enabled"] is False

    stale_service = RvcService(tmp_path, FakeRvcRunner(), FakeRvcInferenceRunner())
    stale_result = stale_service.apply_quality_stability(
        "project-demo",
        "character-xiao-yan",
        "reference-xiao-yan",
        changed_reference.read_bytes(),
    )
    stale_service.close()
    assert stale_result.status == "fallback"
    assert "批准已过期" in str(stale_result.error)


def test_enabled_quality_stability_processes_the_rendered_wav(tmp_path: Path) -> None:
    install_model_pair(tmp_path)
    sample_path = tmp_path / "outputs" / "audio" / "jobs" / "sample.wav"
    write_wav(sample_path, 3)
    write_reference_plan(tmp_path, "project-demo", "/media/outputs/audio/jobs/sample.wav")
    inference_runner = FakeRvcInferenceRunner()
    service = RvcService(tmp_path, FakeRvcRunner(), inference_runner)
    model_id = service.workspace("project-demo").models[0].model_id
    approve_model_route(service, "project-demo", model_id, "quality")
    service.update_settings(
        "project-demo",
        RvcSettingsUpdate(
            character_id="character-xiao-yan",
            selected_model_id=model_id,
            stability_enabled=True,
        ),
    )
    service.update_settings(
        "project-demo",
        RvcSettingsUpdate(quality_stability_enabled=True),
    )

    original = sample_path.read_bytes()
    processed = service.apply_quality_stability(
        "project-demo",
        "character-xiao-yan",
        "reference-xiao-yan",
        original,
    )
    service.close()

    assert inference_runner.calls == 1
    assert processed.status == "applied"
    assert processed.audio != original
    assert processed.audio[:4] == b"RIFF"
    assert inference_runner.profiles[0].preset == "conservative"


def test_quality_stability_fails_open_to_the_base_render(tmp_path: Path) -> None:
    install_model_pair(tmp_path)
    sample_path = tmp_path / "outputs" / "audio" / "jobs" / "sample.wav"
    write_wav(sample_path, 3)
    write_reference_plan(tmp_path, "project-demo", "/media/outputs/audio/jobs/sample.wav")
    inference_runner = FakeRvcInferenceRunner()
    inference_runner.error = "simulated converter crash"
    service = RvcService(tmp_path, FakeRvcRunner(), inference_runner)
    model_id = service.workspace("project-demo").models[0].model_id
    approve_model_route(service, "project-demo", model_id, "quality")
    service.update_settings(
        "project-demo",
        RvcSettingsUpdate(
            character_id="character-xiao-yan",
            selected_model_id=model_id,
            stability_enabled=True,
        ),
    )
    service.update_settings("project-demo", RvcSettingsUpdate(quality_stability_enabled=True))

    original = sample_path.read_bytes()
    processed = service.apply_quality_stability(
        "project-demo",
        "character-xiao-yan",
        "reference-xiao-yan",
        original,
    )
    service.close()

    assert processed.status == "fallback"
    assert processed.audio == original
    assert processed.model_id == model_id
    assert processed.error == "simulated converter crash"


def test_quality_cache_reapplies_rvc_from_base_render_without_rerunning_tts(tmp_path: Path) -> None:
    install_model_pair(tmp_path)
    sample_path = tmp_path / "outputs" / "audio" / "jobs" / "sample.wav"
    write_wav(sample_path, 3)
    write_reference_plan(tmp_path, "project-demo", "/media/outputs/audio/jobs/sample.wav")
    manifest_service = RvcService(tmp_path)
    model_id = manifest_service.workspace("project-demo").models[0].model_id
    approve_model_route(manifest_service, "project-demo", model_id, "quality")
    manifest_service.close()

    gateway = FakeRvcPreviewGateway()
    inference_runner = FakeRvcInferenceRunner()
    application = create_app(
        tmp_path,
        model_gateway=gateway,
        rvc_runner=FakeRvcRunner(),
        rvc_inference_runner=inference_runner,
    )
    bound = asyncio.run(
        request_app(
            application,
            "PATCH",
            "/api/projects/project-demo/rvc/settings",
            json={
                "character_id": "character-xiao-yan",
                "selected_model_id": model_id,
                "stability_enabled": True,
            },
        )
    )
    assert bound.status_code == 200
    assert asyncio.run(
        request_app(
            application,
            "PATCH",
            "/api/projects/project-demo/rvc/settings",
            json={"quality_stability_enabled": True},
        )
    ).status_code == 200

    submitted = asyncio.run(
        request_app(
            application,
            "POST",
            "/api/jobs",
            json={
                "kind": "quality_render",
                "project_id": "project-demo",
                "reference_id": "reference-xiao-yan",
                "character_id": "character-xiao-yan",
                "segment_id": "segment-001",
                "text": "基础渲染只应生成一次。",
                "reference_audio_url": "/media/outputs/audio/jobs/sample.wav",
            },
        )
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]
    deadline = time.monotonic() + 5
    completed: dict[str, object] = {}
    while time.monotonic() < deadline:
        completed = asyncio.run(request_app(application, "GET", f"/api/jobs/{job_id}")).json()
        if completed["status"] in {"complete", "failed"}:
            break
        time.sleep(0.02)
    assert completed["status"] == "complete"
    assert completed["rvc_status"] == "applied"
    assert len(gateway.quality_calls) == 1
    first_rvc = asyncio.run(request_app(application, "GET", str(completed["rvc_output_url"]))).content

    inference_runner.sample_value = 1_500
    reprocessed = asyncio.run(
        request_app(
            application,
            "POST",
            "/api/projects/project-demo/quality/rvc/reprocess",
            json={"job_ids": [job_id]},
        )
    )
    assert reprocessed.status_code == 200
    payload = reprocessed.json()[0]
    assert payload["rvc_status"] == "applied"
    assert payload["base_output_url"] == completed["base_output_url"]
    assert len(gateway.quality_calls) == 1
    assert inference_runner.calls == 2
    second_rvc = asyncio.run(request_app(application, "GET", payload["rvc_output_url"])).content
    assert second_rvc != first_rvc


def test_enabled_fast_route_processes_lightweight_tts_with_bound_character(tmp_path: Path) -> None:
    install_model_pair(tmp_path)
    sample_path = tmp_path / "outputs" / "audio" / "jobs" / "sample.wav"
    write_wav(sample_path, 3)
    write_reference_plan(tmp_path, "project-demo", "/media/outputs/audio/jobs/sample.wav")
    inference_runner = FakeRvcInferenceRunner()
    service = RvcService(tmp_path, FakeRvcRunner(), inference_runner)
    model_id = service.workspace("project-demo").models[0].model_id
    approve_model_route(service, "project-demo", model_id, "fast")
    service.update_settings(
        "project-demo",
        RvcSettingsUpdate(
            character_id="character-xiao-yan",
            selected_model_id=model_id,
            fast_character_enabled=True,
        ),
    )
    service.update_settings("project-demo", RvcSettingsUpdate(fast_route_enabled=True))

    original = sample_path.read_bytes()
    processed = service.apply_fast_route(
        "project-demo",
        "character-xiao-yan",
        "reference-xiao-yan",
        original,
    )
    service.close()

    assert inference_runner.calls == 1
    assert processed.status == "applied"
    assert processed.audio != original
    assert processed.audio[:4] == b"RIFF"
    assert inference_runner.profiles[0].preset == "strong"


def test_rvc_training_job_completes_with_managed_runner(tmp_path: Path) -> None:
    write_wav(tmp_path / "outputs" / "audio" / "jobs" / "sample.wav", 181, sample_rate=100)
    write_reference_plan(tmp_path, "project-demo", "/media/outputs/audio/jobs/sample.wav")
    runner = FakeRvcRunner()
    application = create_app(tmp_path, rvc_runner=runner)

    submitted = asyncio.run(
        request_app(
            application,
            "POST",
            "/api/projects/project-demo/rvc/training",
            json={"character_id": "character-xiao-yan", "options": {"epochs": 80}},
        )
    )
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]

    deadline = time.monotonic() + 2
    status_payload: dict[str, object] = {}
    while time.monotonic() < deadline:
        status_response = asyncio.run(
            request_app(application, "GET", f"/api/rvc/jobs/{job_id}")
        )
        status_payload = status_response.json()
        if status_payload["status"] == "complete":
            break
        time.sleep(0.02)

    assert status_payload["status"] == "complete"
    assert status_payload["progress"] == 100
    workspace = asyncio.run(
        request_app(application, "GET", "/api/projects/project-demo/rvc/workspace")
    ).json()
    trained = next(model for model in workspace["models"] if model["source"] == "trained")
    assert trained["status"] == "candidate"
    assert trained["approved_routes"] == []
    assert workspace["training_sets"][0]["status"] == "ready"
