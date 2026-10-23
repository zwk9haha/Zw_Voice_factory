from __future__ import annotations

import asyncio
import io
import time
import wave
from pathlib import Path

import httpx
import pytest

from app.jobs import ModelGateway
from app.loudness import LoudnessMetrics, ProgramLoudnessPolicy
from app.main import create_app
from app.voice_analysis import RuleBasedVoiceAnalyzer
from app.rvc_contracts import RvcApplyResult


def wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 160)
    return buffer.getvalue()


class WorkflowGateway(ModelGateway):
    def __init__(self, delay: float = 0) -> None:
        self.delay = delay
        self.calls = 0

    def generate_voxcpm(self, text: str, voice_prompt: str) -> bytes:
        del text, voice_prompt
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
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
        del text, reference_audio_path, quality_model, emotion_description, render_options, reference_text
        self.calls += 1
        return wav_bytes()

    def generate_fast(self, text: str, voice_id: str, speed: float = 1.0) -> bytes:
        del text, voice_id, speed
        self.calls += 1
        return wav_bytes()

    def runtime_status(self) -> dict[str, object]:
        return {"launcher_managed": True, "services": {}}


class SlowVoiceAnalyzer:
    def __init__(self, workspace_root: Path, delay: float = 0.25) -> None:
        self.inner = RuleBasedVoiceAnalyzer(workspace_root)
        self.delay = delay

    def status(self):
        return self.inner.status()

    def analyze(self, evidence_pack):
        time.sleep(self.delay)
        return self.inner.analyze(evidence_pack)


class PassThroughLoudness:
    def process_segment_bytes(self, audio: bytes, policy: ProgramLoudnessPolicy) -> tuple[bytes, LoudnessMetrics]:
        del policy
        return audio, LoudnessMetrics(processor="test", status="unchanged", applied_gain_db=0)

    def rolling_gain_db(self, policy: ProgramLoudnessPolicy, program_id: str | None = None) -> float:
        del policy, program_id
        return 0

    def normalize_program_file(self, source: Path, target: Path, policy: ProgramLoudnessPolicy) -> LoudnessMetrics:
        del policy
        target.write_bytes(source.read_bytes())
        return LoudnessMetrics(processor="test", status="unchanged", final_program_pass=True)

    def record(self, metrics: LoudnessMetrics | None, program_id: str | None = None) -> None:
        del metrics, program_id


async def request(app: object, method: str, path: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def wait_for_run(app: object, run_id: str, states: set[str] | None = None, timeout: float = 5) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = asyncio.run(request(app, "GET", f"/api/workflows/runs/{run_id}")).json()
        if states is None and payload["status"] in {"completed", "failed", "cancelled", "paused"}:
            return payload
        if states and payload["status"] in states:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"workflow run {run_id} did not reach {states or 'a terminal state'}")


def workflow_payload() -> dict[str, object]:
    return {
        "name": "短文本参考音频最小流程",
        "nodes": [
            {
                "node_id": "text",
                "node_type": "text.input",
                "inputs": {"text": "$input.text"},
            },
            {
                "node_id": "profile",
                "node_type": "voice.profile",
                "depends_on": ["text"],
                "inputs": {
                    "text": "$text.text",
                    "character_id": "hero",
                    "display_name": "主角",
                    "gender_hint": "male",
                },
            },
            {
                "node_id": "reference",
                "node_type": "voice.reference_audio",
                "depends_on": ["profile"],
                "inputs": {
                    "text": "$input.reference_text",
                    "voice_prompt": "$profile.voice_prompt",
                },
            },
        ],
    }


def analysis_match_workflow_payload() -> dict[str, object]:
    return {
        "name": "文本分析与声音匹配",
        "nodes": [
            {
                "node_id": "text",
                "node_type": "text.input",
                "inputs": {"text": "$input.text"},
            },
            {
                "node_id": "analysis",
                "node_type": "text.analysis",
                "depends_on": ["text"],
                "inputs": {"text": "$text.text"},
            },
            {
                "node_id": "profile",
                "node_type": "voice.profile",
                "depends_on": ["analysis"],
                "inputs": {
                    "text": "$text.text",
                    "character_id": "hero",
                    "display_name": "主角",
                    "gender_hint": "female",
                },
            },
            {
                "node_id": "match",
                "node_type": "voice.match",
                "depends_on": ["profile"],
                "inputs": {
                    "profile": "$profile.profile",
                    "voice_prompt": "$profile.voice_prompt",
                    "limit": 4,
                },
            },
        ],
    }


def test_workflow_text_analysis_and_voice_match_nodes(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    created = asyncio.run(request(application, "POST", "/api/workflows", json=analysis_match_workflow_payload()))
    assert created.status_code == 201
    run = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{created.json()['workflow_id']}/runs",
            json={"input": {"text": "林默说道：‘清晨的风穿过长街’。"}},
        )
    )
    completed = wait_for_run(application, run.json()["run_id"])
    assert completed["status"] == "completed"
    analysis = completed["node_runs"][1]["outputs"]
    assert analysis["segment_count"] >= 1
    assert any(segment["speaker"]["speaker_name"] == "林默" for segment in analysis["segments"])
    matched = completed["node_runs"][3]["outputs"]
    assert matched["match_count"] == 4
    assert matched["top_match"]["asset_id"]
    assert matched["top_match"]["reasons"]


def test_project_binds_published_workflow_and_runs_fixed_project_context(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    created_project = asyncio.run(request(application, "POST", "/api/platform/projects", json={
        "name": "项目运行演示",
        "content_type": "script",
        "text": "项目文本",
    }))
    assert created_project.status_code == 201
    project_id = created_project.json()["project_id"]
    created = asyncio.run(request(application, "POST", "/api/workflows", json={
        "name": "项目运行外壳",
        "inputs": [{"name": "text", "label": "文本", "type": "text", "required": True}],
        "nodes": [{"node_id": "input", "node_type": "text.input", "inputs": {"text": "$input.text"}}],
        "outputs": [{"name": "text", "type": "text", "source_node": "input", "source_port": "text"}],
    }))
    assert created.status_code == 201
    workflow_id = created.json()["workflow_id"]
    published = asyncio.run(request(application, "POST", f"/api/workflows/{workflow_id}/publish"))
    assert published.status_code == 200

    empty = asyncio.run(request(application, "GET", f"/api/workflows/projects/{project_id}"))
    assert empty.status_code == 200
    assert empty.json()["workflow"] is None

    bound = asyncio.run(request(application, "PUT", f"/api/workflows/projects/{project_id}/binding", json={"workflow_id": workflow_id}))
    assert bound.status_code == 200
    assert bound.json()["binding"]["workflow_id"] == workflow_id
    assert bound.json()["workflow"]["lifecycle"] == "published"

    started = asyncio.run(request(application, "POST", f"/api/workflows/projects/{project_id}/runs", json={"input": {"text": "项目文本"}}))
    assert started.status_code == 202
    completed = wait_for_run(application, started.json()["run_id"])
    assert completed["status"] == "completed"
    assert completed["input"]["project_id"] == project_id
    assert completed["outputs"]["text"] == "项目文本"

    history = asyncio.run(request(application, "GET", f"/api/workflows/projects/{project_id}"))
    assert history.json()["runs"][0]["run_id"] == started.json()["run_id"]


def test_workflow_human_checkpoint_approval_and_rejection(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    payload = {
        "name": "人工审核关卡",
        "nodes": [
            {"node_id": "profile", "node_type": "voice.profile", "inputs": {"text": "$input.text", "character_id": "hero", "display_name": "主角"}},
            {"node_id": "checkpoint", "node_type": "voice.match_or_generate", "depends_on": ["profile"], "inputs": {"profile": "$profile.profile"}},
            {"node_id": "after", "node_type": "text.input", "depends_on": ["checkpoint"], "inputs": {"text": "审核后继续"}},
        ],
    }
    created = asyncio.run(request(application, "POST", "/api/workflows", json=payload))
    assert created.status_code == 201
    workflow_id = created.json()["workflow_id"]

    first = asyncio.run(request(application, "POST", f"/api/workflows/{workflow_id}/runs", json={"input": {"text": "待审核角色线索"}}))
    awaiting = wait_for_run(application, first.json()["run_id"], {"awaiting_review"})
    checkpoint = next(node for node in awaiting["node_runs"] if node["node_id"] == "checkpoint")
    assert checkpoint["status"] == "awaiting_review"
    assert checkpoint["outputs"] == {}
    assert checkpoint["review"]["payload"]["decision"]["action"] in {"catalog_match", "generate_candidate"}

    restarted = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    persisted = asyncio.run(request(restarted, "GET", f"/api/workflows/runs/{first.json()['run_id']}"))
    assert persisted.json()["status"] == "awaiting_review"
    approved = asyncio.run(request(restarted, "POST", f"/api/workflows/runs/{first.json()['run_id']}/nodes/checkpoint/review", json={"decision": "approved", "comment": "接受候选"}))
    assert approved.status_code == 202
    completed = wait_for_run(restarted, first.json()["run_id"])
    assert completed["status"] == "completed"
    assert completed["node_runs"][1]["review"]["decision"] == "approved"
    assert completed["node_runs"][2]["status"] == "completed"
    duplicate = asyncio.run(request(restarted, "POST", f"/api/workflows/runs/{first.json()['run_id']}/nodes/checkpoint/review", json={"decision": "rejected"}))
    assert duplicate.status_code == 409

    second = asyncio.run(request(restarted, "POST", f"/api/workflows/{workflow_id}/runs", json={"input": {"text": "另一条待审核线索"}}))
    wait_for_run(restarted, second.json()["run_id"], {"awaiting_review"})
    rejected = asyncio.run(request(restarted, "POST", f"/api/workflows/runs/{second.json()['run_id']}/nodes/checkpoint/review", json={"decision": "rejected", "comment": "候选不合适"}))
    assert rejected.status_code == 202
    cancelled = wait_for_run(restarted, second.json()["run_id"], {"cancelled"})
    assert cancelled["status"] == "cancelled"
    assert cancelled["node_runs"][1]["review"]["decision"] == "rejected"
    assert cancelled["node_runs"][2]["status"] == "cancelled"


def test_workflow_condition_routes_only_the_selected_branch(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    payload = {
        "name": "条件分支路由",
        "nodes": [
            {"node_id": "input", "node_type": "text.input", "inputs": {"text": "$input.route"}},
            {"node_id": "condition", "node_type": "control.condition", "config": {"operator": "equals", "compare_value": "quality"}},
            {"node_id": "quality", "node_type": "text.input", "inputs": {"text": "质量路线"}},
            {"node_id": "quality_tail", "node_type": "text.input"},
            {"node_id": "fast", "node_type": "text.input", "inputs": {"text": "极速路线"}},
        ],
        "edges": [
            {"edge_id": "input-condition", "source_node": "input", "source_port": "text", "target_node": "condition", "target_port": "value"},
            {"edge_id": "condition-quality", "source_node": "condition", "source_port": "true", "target_node": "quality", "target_port": "__route__", "kind": "branch"},
            {"edge_id": "condition-fast", "source_node": "condition", "source_port": "false", "target_node": "fast", "target_port": "__route__", "kind": "branch"},
            {"edge_id": "quality-tail", "source_node": "quality", "source_port": "text", "target_node": "quality_tail", "target_port": "text"},
        ],
    }
    created = asyncio.run(request(application, "POST", "/api/workflows", json=payload))
    assert created.status_code == 201
    workflow_id = created.json()["workflow_id"]

    quality_run = asyncio.run(request(application, "POST", f"/api/workflows/{workflow_id}/runs", json={"input": {"route": "quality"}}))
    quality = wait_for_run(application, quality_run.json()["run_id"])
    quality_nodes = {node["node_id"]: node for node in quality["node_runs"]}
    assert quality["status"] == "completed"
    assert quality_nodes["condition"]["outputs"]["result"] is True
    assert quality_nodes["quality"]["status"] == "completed"
    assert quality_nodes["quality_tail"]["status"] == "completed"
    assert quality_nodes["fast"]["status"] == "skipped"
    assert quality_nodes["fast"]["skip_reason"] == "branch_not_selected"

    fast_run = asyncio.run(request(application, "POST", f"/api/workflows/{workflow_id}/runs", json={"input": {"route": "fast"}}))
    fast = wait_for_run(application, fast_run.json()["run_id"])
    fast_nodes = {node["node_id"]: node for node in fast["node_runs"]}
    assert fast["status"] == "completed"
    assert fast_nodes["condition"]["outputs"]["result"] is False
    assert fast_nodes["quality"]["status"] == "skipped"
    assert fast_nodes["quality"]["skip_reason"] == "branch_not_selected"
    assert fast_nodes["quality_tail"]["status"] == "skipped"
    assert fast_nodes["quality_tail"]["skip_reason"] == "upstream_not_selected"
    assert fast_nodes["fast"]["status"] == "completed"


def test_workflow_condition_group_supports_all_any_and_nested_paths(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())

    def condition_workflow(name: str, logic: str, conditions: list[dict[str, object]]) -> dict[str, object]:
        return {
            "name": name,
            "nodes": [
                {
                    "node_id": "condition",
                    "node_type": "control.condition",
                    "inputs": {"value": "$input.payload"},
                    "config": {"logic": logic, "conditions": conditions},
                },
                {"node_id": "accepted", "node_type": "text.input", "inputs": {"text": f"{name}：命中条件"}},
                {"node_id": "rejected", "node_type": "text.input", "inputs": {"text": f"{name}：未命中条件"}},
            ],
            "edges": [
                {"edge_id": "condition-accepted", "source_node": "condition", "source_port": "true", "target_node": "accepted", "target_port": "__route__", "kind": "branch"},
                {"edge_id": "condition-rejected", "source_node": "condition", "source_port": "false", "target_node": "rejected", "target_port": "__route__", "kind": "branch"},
            ],
        }

    all_created = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json=condition_workflow(
                "全部满足条件组",
                "all",
                [
                    {"path": "$.profile.score", "operator": "greater_or_equal", "compare_value": 0.8},
                    {"path": "$.profile.tags", "operator": "contains", "compare_value": "stable"},
                    {"path": "$.profile.optional", "operator": "is_empty"},
                ],
            ),
        )
    )
    assert all_created.status_code == 201
    all_workflow_id = all_created.json()["workflow_id"]

    all_match_run = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{all_workflow_id}/runs",
            json={"input": {"payload": {"profile": {"score": 0.9, "tags": ["warm", "stable"]}}}},
        )
    )
    all_match = wait_for_run(application, all_match_run.json()["run_id"])
    all_match_nodes = {node["node_id"]: node for node in all_match["node_runs"]}
    condition_output = all_match_nodes["condition"]["outputs"]
    assert condition_output["logic"] == "all"
    assert condition_output["result"] is True
    assert condition_output["condition_results"] == [
        {"path": "$.profile.score", "operator": "greater_or_equal", "compare_value": 0.8, "actual_value": 0.9, "matched": True},
        {"path": "$.profile.tags", "operator": "contains", "compare_value": "stable", "actual_value": ["warm", "stable"], "matched": True},
        {"path": "$.profile.optional", "operator": "is_empty", "compare_value": None, "actual_value": None, "matched": True},
    ]
    assert all_match_nodes["accepted"]["status"] == "completed"
    assert all_match_nodes["rejected"]["status"] == "skipped"

    all_miss_run = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{all_workflow_id}/runs",
            json={"input": {"payload": {"profile": {"score": 0.7, "tags": ["warm", "stable"]}}}},
        )
    )
    all_miss = wait_for_run(application, all_miss_run.json()["run_id"])
    all_miss_nodes = {node["node_id"]: node for node in all_miss["node_runs"]}
    assert all_miss_nodes["condition"]["outputs"]["result"] is False
    assert all_miss_nodes["accepted"]["status"] == "skipped"
    assert all_miss_nodes["rejected"]["status"] == "completed"

    any_created = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json=condition_workflow(
                "任一满足条件组",
                "any",
                [
                    {"path": "$.profile.score", "operator": "greater_or_equal", "compare_value": 0.8},
                    {"path": "$.profile.tags", "operator": "contains", "compare_value": "expressive"},
                ],
            ),
        )
    )
    assert any_created.status_code == 201
    any_run = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{any_created.json()['workflow_id']}/runs",
            json={"input": {"payload": {"profile": {"score": 0.6, "tags": ["expressive"]}}}},
        )
    )
    any_match = wait_for_run(application, any_run.json()["run_id"])
    any_nodes = {node["node_id"]: node for node in any_match["node_runs"]}
    assert any_nodes["condition"]["outputs"]["logic"] == "any"
    assert [item["matched"] for item in any_nodes["condition"]["outputs"]["condition_results"]] == [False, True]
    assert any_nodes["condition"]["outputs"]["result"] is True
    assert any_nodes["accepted"]["status"] == "completed"
    assert any_nodes["rejected"]["status"] == "skipped"


def test_workflow_condition_group_rejects_invalid_rule_limits_and_paths(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    invalid = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows/validate",
            json={
                "name": "非法条件组",
                "nodes": [
                    {
                        "node_id": "condition",
                        "node_type": "control.condition",
                        "inputs": {"value": "$input.payload"},
                        "config": {
                            "conditions": [
                                {"path": "" if index == 0 else f"$.value{index}", "operator": "equals", "compare_value": index}
                                for index in range(33)
                            ]
                        },
                    }
                ],
            },
        )
    )
    assert invalid.status_code == 200
    assert invalid.json()["valid"] is False
    assert any("条件节点 condition 配置无效" in item for item in invalid.json()["errors"])


def test_workflow_branch_edges_require_control_boolean_outputs(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    invalid = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows/validate",
            json={
                "name": "非法分支",
                "nodes": [
                    {"node_id": "source", "node_type": "text.input", "inputs": {"text": "$input.text"}},
                    {"node_id": "target", "node_type": "text.input", "inputs": {"text": "目标"}},
                ],
                "edges": [
                    {"edge_id": "not-control", "source_node": "source", "source_port": "text", "target_node": "target", "target_port": "__route__", "kind": "branch"},
                ],
            },
        )
    )
    assert invalid.status_code == 200
    assert invalid.json()["valid"] is False
    assert any("必须从控制流节点发出" in item for item in invalid.json()["errors"])
    assert any("必须是 boolean 类型" in item for item in invalid.json()["errors"])


def test_workflow_character_analysis_node_emits_evidence_and_profiles(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    created = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json={
                "name": "角色分析节点",
                "nodes": [
                    {"node_id": "analysis", "node_type": "voice.character_analysis", "inputs": {"text": "$input.text"}},
                ],
            },
        )
    )
    run = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{created.json()['workflow_id']}/runs",
            json={"input": {"text": "林默说道：‘我们走。’苏晚问道：‘现在吗？’"}},
        )
    )
    completed = wait_for_run(application, run.json()["run_id"])
    assert completed["status"] == "completed"
    output = completed["node_runs"][0]["outputs"]
    assert output["character_count"] == 2
    assert {item["display_name"] for item in output["characters"]} == {"林默", "苏晚"}
    assert all(item["profile"]["voice_prompt"] for item in output["characters"])


def test_workflow_ports_provide_labels_and_help_on_both_sides(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway())
    response = asyncio.run(request(application, "GET", "/api/workflows/node-types"))
    assert response.status_code == 200
    for manifest in response.json():
        for port in [*manifest["inputs"], *manifest["outputs"]]:
            assert port["label"].strip(), (manifest["type"], port["name"])
            assert port["description"].strip(), (manifest["type"], port["name"])


def test_workflow_rule_profiles_do_not_call_unavailable_configured_analyzer(tmp_path: Path) -> None:
    class UnavailableAnalyzer(RuleBasedVoiceAnalyzer):
        def analyze(self, evidence_pack):
            raise RuntimeError("配置的云端线路不可用")

    application = create_app(tmp_path, model_gateway=WorkflowGateway(), voice_analyzer=UnavailableAnalyzer(tmp_path))
    created = asyncio.run(request(application, "POST", "/api/workflows", json={
        "name": "规则画像无需云端",
        "nodes": [{"node_id": "analysis", "node_type": "voice.character_analysis",
                   "inputs": {"text": '萧炎笑道：“出发。”\n“好。”萧薰儿说道。'},
                   "config": {"speaker_names": ["萧炎", "萧薰儿"], "profile_mode": "rules"}}],
    }))
    assert created.status_code == 201
    started = asyncio.run(request(application, "POST", f"/api/workflows/{created.json()['workflow_id']}/runs", json={"input": {}}))
    completed = wait_for_run(application, started.json()["run_id"])
    assert completed["status"] == "completed"
    outputs = completed["node_runs"][0]["outputs"]
    assert {character["display_name"] for character in outputs["characters"]} == {"萧炎", "萧薰儿"}
    assert all(character["profile"]["backend"] == "rules" for character in outputs["characters"])
    assert {segment["speaker"]["speaker_name"] for segment in outputs["segments"]} == {"旁白", "萧炎", "萧薰儿"}


def test_workflow_import_keeps_picked_text_across_save_and_reload(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway())
    created = asyncio.run(request(application, "POST", "/api/workflows", json={
        "name": "节点内选择 TXT",
        "nodes": [{"node_id": "source", "node_type": "text.import", "config": {
            "text": "萧炎说道：“我们走。”", "file_name": "斗破苍穹测试.txt", "port_language": "en",
        }}],
        "outputs": [{"name": "text", "type": "text", "source_node": "source", "source_port": "text"}],
    }))
    assert created.status_code == 201
    workflow_id = created.json()["workflow_id"]
    restarted = create_app(tmp_path, model_gateway=WorkflowGateway())
    loaded = asyncio.run(request(restarted, "GET", f"/api/workflows/{workflow_id}"))
    assert loaded.json()["nodes"][0]["config"]["port_language"] == "en"
    started = asyncio.run(request(restarted, "POST", f"/api/workflows/{workflow_id}/runs", json={"input": {}}))
    completed = wait_for_run(restarted, started.json()["run_id"])
    assert completed["status"] == "completed"
    assert completed["outputs"]["text"] == "萧炎说道：“我们走。”"
    assert completed["node_runs"][0]["outputs"]["source_name"] == "斗破苍穹测试.txt"


def test_workflow_text_import_node_normalizes_run_input_and_workspace_file(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    source = tmp_path / "demo-script.txt"
    source.write_bytes("旁白：雨声落在窗边。\n林默说道：‘我们走。’".encode("utf-8"))
    created = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json={
                "name": "导入文本节点",
                "nodes": [
                    {
                        "node_id": "import",
                        "node_type": "text.import",
                            "inputs": {"file_path": "$input.file_path", "project_id": "$input.project_id"},
                    },
                ],
                "outputs": [
                    {"name": "text", "type": "text", "source_node": "import", "source_port": "text"},
                    {"name": "source_name", "type": "string", "source_node": "import", "source_port": "source_name"},
                ],
            },
        )
    )
    assert created.status_code == 201
    run = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{created.json()['workflow_id']}/runs",
            json={"input": {"project_id": "demo", "file_path": "demo-script.txt"}},
        )
    )
    completed = wait_for_run(application, run.json()["run_id"])
    assert completed["status"] == "completed"
    assert completed["outputs"] == {
        "text": "旁白：雨声落在窗边。\n林默说道：‘我们走。’",
        "source_name": "demo-script.txt",
    }
    assert completed["node_runs"][0]["outputs"]["project_id"] == "demo"

    direct = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{created.json()['workflow_id']}/runs",
            json={"input": {"project_id": "demo", "text": "直接输入的剧本。", "file_name": "inline.txt"}},
        )
    )
    direct_completed = wait_for_run(application, direct.json()["run_id"])
    assert direct_completed["status"] == "completed"
    assert direct_completed["outputs"]["text"] == "直接输入的剧本。"
    assert direct_completed["outputs"]["source_name"] == "inline.txt"


def test_workflow_multi_role_demo_maps_detected_speakers_to_rendered_audio(tmp_path: Path) -> None:
    gateway = WorkflowGateway()
    application = create_app(tmp_path, model_gateway=gateway, loudness_processor=PassThroughLoudness())
    created = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json={
                "name": "多角色配音 Demo",
                "inputs": [
                    {"name": "text", "type": "text", "required": True},
                    {"name": "project_id", "type": "string", "required": True},
                ],
                "nodes": [
                    {"node_id": "source", "node_type": "text.import", "inputs": {"text": "$input.text", "project_id": "$input.project_id", "file_name": "$input.file_name"}},
                    {"node_id": "analysis", "node_type": "voice.character_analysis", "inputs": {"text": "$source.text"}},
                    {
                        "node_id": "multi_role",
                        "node_type": "voice.multi_role_fast_render",
                        "inputs": {
                            "segments": "$analysis.segments",
                            "project_id": "$source.project_id",
                        },
                        "config": {
                            "voice_map": {"旁白": "gunian", "林默": "bazong", "苏晚": "suyingxue"},
                            "fast_rvc_enabled": False,
                        },
                    },
                    {
                        "node_id": "loudness",
                        "node_type": "audio.loudness",
                        "inputs": {"project_id": "$source.project_id", "job_ids": "$multi_role.job_ids"},
                    },
                    {
                        "node_id": "merge",
                        "node_type": "audio.merge",
                        "depends_on": ["loudness"],
                        "inputs": {
                            "project_id": "$source.project_id",
                            "route": "fast",
                            "job_ids": "$loudness.job_ids",
                        },
                    },
                ],
                "edges": [
                    {"edge_id": "source-analysis", "source_node": "source", "source_port": "text", "target_node": "analysis", "target_port": "text"},
                    {"edge_id": "analysis-multi", "source_node": "analysis", "source_port": "segments", "target_node": "multi_role", "target_port": "segments"},
                    {"edge_id": "multi-loudness", "source_node": "multi_role", "source_port": "job_ids", "target_node": "loudness", "target_port": "job_ids"},
                    {"edge_id": "loudness-merge", "source_node": "loudness", "source_port": "job_ids", "target_node": "merge", "target_port": "job_ids"},
                ],
                "outputs": [{"name": "audio_url", "type": "string", "source_node": "merge", "source_port": "audio_url"}],
            },
        )
    )
    assert created.status_code == 201
    started = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{created.json()['workflow_id']}/runs",
            json={
                "input": {
                    "project_id": "workflow-multi-role-demo",
                    "file_name": "demo.txt",
                    "text": "雨声落在窗边。林默说道：“我们走。”苏晚问道：“现在吗？”",
                }
            },
        )
    )
    assert started.status_code == 202
    completed = wait_for_run(application, started.json()["run_id"])
    assert completed["status"] == "completed"
    nodes = {node["node_id"]: node for node in completed["node_runs"]}
    analysis = nodes["analysis"]["outputs"]
    assert analysis["character_count"] == 2
    multi_role = nodes["multi_role"]["outputs"]
    assert multi_role["segment_count"] == len(analysis["segments"])
    assert multi_role["speaker_count"] == 3
    assert multi_role["job_ids"] and len(multi_role["job_ids"]) == multi_role["segment_count"]
    assert {item["fast_voice_id"] for item in multi_role["segments"]} >= {"gunian", "bazong", "suyingxue"}
    assert nodes["loudness"]["status"] == "completed"
    assert nodes["loudness"]["outputs"]["job_ids"] == multi_role["job_ids"]
    assert nodes["merge"]["outputs"]["segment_count"] == multi_role["segment_count"]
    assert completed["outputs"]["audio_url"].startswith("/media/outputs/audio/merged/")
    assert gateway.calls == multi_role["segment_count"]


def split_workflow_payload() -> dict[str, object]:
    return {
        "name": "分步配音", "nodes": [
            {"node_id": "cast", "node_type": "voice.assign_voices", "inputs": {"segments": "$input.segments", "voice_map": "$input.voice_map", "reference_map": "$input.reference_map"}},
            {"node_id": "settings", "node_type": "voice.render_settings", "config": {"route": "fast", "fast_speed": 1.2}},
            {"node_id": "render", "node_type": "voice.batch_render", "inputs": {"project_id": "$input.project_id", "reuse_run_id": "$input.reuse_run_id"}},
            {"node_id": "rvc", "node_type": "audio.rvc", "inputs": {"project_id": "$input.project_id", "enabled": "$input.rvc_enabled"}},
            {"node_id": "loudness", "node_type": "audio.loudness", "inputs": {"project_id": "$input.project_id"}},
            {"node_id": "merge", "node_type": "audio.merge", "inputs": {"project_id": "$input.project_id"}},
        ],
        "edges": [
            {"edge_id": "cast-render", "source_node": "cast", "source_port": "segments", "target_node": "render", "target_port": "segments"},
            {"edge_id": "settings-render", "source_node": "settings", "source_port": "render_settings", "target_node": "render", "target_port": "render_settings"},
            {"edge_id": "render-rvc", "source_node": "render", "source_port": "job_ids", "target_node": "rvc", "target_port": "job_ids"},
            {"edge_id": "settings-rvc", "source_node": "settings", "source_port": "route", "target_node": "rvc", "target_port": "route"},
            {"edge_id": "rvc-loudness", "source_node": "rvc", "source_port": "job_ids", "target_node": "loudness", "target_port": "job_ids"},
            {"edge_id": "loudness-merge", "source_node": "loudness", "source_port": "job_ids", "target_node": "merge", "target_port": "job_ids"},
            {"edge_id": "settings-merge", "source_node": "settings", "source_port": "route", "target_node": "merge", "target_port": "route"},
        ],
        "outputs": [{"name": "audio_url", "type": "string", "source_node": "merge", "source_port": "audio_url"}],
    }


def run_split_workflow(application, payload, inputs):
    created = asyncio.run(request(application, "POST", "/api/workflows", json=payload))
    assert created.status_code == 201, created.text
    started = asyncio.run(request(application, "POST", f"/api/workflows/{created.json()['workflow_id']}/runs", json={"input": inputs}))
    assert started.status_code == 202, started.text
    return wait_for_run(application, started.json()["run_id"])


def test_split_workflow_transmits_voice_speed_and_rvc_switch(tmp_path: Path, monkeypatch) -> None:
    seen = []

    class RecordingGateway(WorkflowGateway):
        def generate_fast(self, text, voice_id, speed=1.0):
            seen.append((text, voice_id, speed))
            return super().generate_fast(text, voice_id, speed)

    rvc_calls = []

    def apply_fast(self, project_id, character_id, reference_id, audio):
        rvc_calls.append((project_id, character_id, reference_id))
        return RvcApplyResult(audio=audio, status="applied", model_id="approved-test-model")

    from app.rvc import RvcService
    monkeypatch.setattr(RvcService, "apply_fast_route", apply_fast)
    application = create_app(tmp_path, model_gateway=RecordingGateway(), loudness_processor=PassThroughLoudness())
    inputs = {"project_id": "split", "segments": [{"text": "第一句。", "speaker_name": "甲"}, {"text": "第二句。", "speaker_name": "乙"}], "voice_map": {"甲": "bazong", "乙": "suyingxue"}, "rvc_enabled": False}
    completed = run_split_workflow(application, split_workflow_payload(), inputs)
    assert completed["status"] == "completed", completed["error"]
    assert seen == [("第一句。", "bazong", 1.2), ("第二句。", "suyingxue", 1.2)]
    assert rvc_calls == []
    enabled = run_split_workflow(application, split_workflow_payload(), {**inputs, "rvc_enabled": True})
    assert enabled["status"] == "completed", enabled["error"]
    assert len(rvc_calls) == 2
    rvc = next(node for node in enabled["node_runs"] if node["node_id"] == "rvc")["outputs"]
    assert rvc["rvc_statuses"] == ["applied", "applied"]
    assert enabled["outputs"]["audio_url"].startswith("/media/outputs/audio/merged/")


@pytest.mark.parametrize("change, expected_reuse", [("none", 2), ("text", 1), ("voice", 1), ("missing_audio", 1), ("project", 0)])
def test_split_workflow_only_reuses_matching_existing_audio(tmp_path: Path, change: str, expected_reuse: int) -> None:
    gateway = WorkflowGateway()
    application = create_app(tmp_path, model_gateway=gateway, loudness_processor=PassThroughLoudness())
    inputs = {"project_id": "cache", "segments": [{"text": "第一句。", "speaker_name": "甲"}, {"text": "第二句。", "speaker_name": "乙"}], "voice_map": {"甲": "bazong", "乙": "suyingxue"}, "rvc_enabled": False}
    original = run_split_workflow(application, split_workflow_payload(), inputs)
    assert original["status"] == "completed", original["error"]
    inputs["reuse_run_id"] = original["run_id"]
    if change == "text":
        inputs["segments"][0]["text"] = "第一句已修改。"
    elif change == "voice":
        inputs["voice_map"]["甲"] = "gunian"
    elif change == "missing_audio":
        render = next(node for node in original["node_runs"] if node["node_id"] == "render")["outputs"]
        (tmp_path / "outputs" / "audio" / "jobs" / f"{render['job_ids'][0]}.wav").unlink()
    elif change == "project":
        inputs["project_id"] = "other-project"
    completed = run_split_workflow(application, split_workflow_payload(), inputs)
    assert completed["status"] == "completed", completed["error"]
    render = next(node for node in completed["node_runs"] if node["node_id"] == "render")["outputs"]
    assert render["reused_job_count"] == expected_reuse
    assert gateway.calls == 4 - expected_reuse
    assert sum(segment["cache_hit"] for segment in render["segments"]) == expected_reuse


def test_split_quality_workflow_uses_selected_model_and_requires_reference(tmp_path: Path, monkeypatch) -> None:
    seen = []

    class RecordingGateway(WorkflowGateway):
        def generate_quality(self, text, reference_audio_path, quality_model, emotion_description=None, render_options=None, reference_text=None):
            seen.append((text, quality_model, reference_text, reference_audio_path.name))
            return wav_bytes()

    reference = tmp_path / "assets" / "voice_samples" / "approved.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(wav_bytes())
    from app.rvc import RvcService

    def unexpected_rvc(*args):
        raise AssertionError("RVC is disabled in this workflow")

    monkeypatch.setattr(RvcService, "apply_quality_stability", unexpected_rvc)
    application = create_app(tmp_path, model_gateway=RecordingGateway(), loudness_processor=PassThroughLoudness())
    payload = split_workflow_payload()
    payload["nodes"][1]["config"] = {"route": "quality", "quality_model": "gpt_sovits_v4"}
    inputs = {"project_id": "quality-split", "segments": [{"text": "你好。", "speaker_name": "甲"}], "rvc_enabled": False}
    failed = run_split_workflow(application, payload, inputs)
    assert failed["status"] == "failed"
    assert "缺少质量路线参考音频" in failed["error"]
    inputs["reference_map"] = {"甲": {"reference_audio_url": "/media/voice-samples/approved.wav", "reference_text": "参考台词。"}}
    completed = run_split_workflow(application, payload, inputs)
    assert completed["status"] == "completed", completed["error"]
    assert seen == [("你好。", "gpt_sovits_v4", "参考台词。", "approved.wav")]


def test_split_workflow_reuses_legacy_render_and_reads_current_txt(tmp_path: Path) -> None:
    gateway = WorkflowGateway()
    application = create_app(tmp_path, model_gateway=gateway, loudness_processor=PassThroughLoudness())
    source = tmp_path / "input" / "demo.txt"
    source.parent.mkdir(exist_ok=True)
    source.write_text("甲说道：“第一句。”", encoding="utf-8")
    payload = {
        "name": "旧版生成", "nodes": [
            {"node_id": "source", "node_type": "text.import", "config": {"file_path": "input/demo.txt"}},
            {"node_id": "analysis", "node_type": "text.analysis", "inputs": {"text": "$source.text"}, "config": {"speaker_names": ["甲"]}},
            {"node_id": "render", "node_type": "voice.multi_role_fast_render", "inputs": {"segments": "$analysis.segments", "project_id": "legacy"}, "config": {"voice_map": {"甲": "bazong"}, "fast_rvc_enabled": False, "fast_speed": 1.2}},
        ],
    }
    original = run_split_workflow(application, payload, {})
    assert original["status"] == "completed", original["error"]
    legacy_segments = original["node_runs"][-1]["outputs"]["segments"]
    # Older saved runs have no request fingerprint but retain render metadata.
    for segment in legacy_segments:
        segment.pop("request_fingerprint", None)
    import json
    run_path = next((tmp_path / "outputs" / "workflows" / "runs").glob(f"{original['run_id']}.json"))
    saved = json.loads(run_path.read_text(encoding="utf-8"))
    saved["node_runs"][-1]["outputs"]["segments"] = legacy_segments
    run_path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    application = create_app(tmp_path, model_gateway=gateway, loudness_processor=PassThroughLoudness())
    completed = run_split_workflow(application, split_workflow_payload(), {"project_id": "legacy", "segments": legacy_segments, "voice_map": {"甲": "bazong"}, "rvc_enabled": False, "reuse_run_id": original["run_id"]})
    assert completed["status"] == "completed", completed["error"]
    rendered = next(node for node in completed["node_runs"] if node["node_id"] == "render")["outputs"]
    assert rendered["reused_job_count"] == len(legacy_segments)
    source.write_text("甲说道：“修改了文本。”", encoding="utf-8")
    reloaded = run_split_workflow(application, payload, {})
    assert reloaded["status"] == "completed", reloaded["error"]
    assert reloaded["node_runs"][0]["outputs"]["text"] == "甲说道：“修改了文本。”"


def test_split_workflow_rejects_cross_project_rvc_input(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    inputs = {"project_id": "owner", "segments": [{"text": "你好。", "speaker_name": "甲"}], "rvc_enabled": False}
    original = run_split_workflow(application, split_workflow_payload(), inputs)
    render = next(node for node in original["node_runs"] if node["node_id"] == "render")["outputs"]
    payload = {"name": "跨项目 RVC", "nodes": [{"node_id": "rvc", "node_type": "audio.rvc", "inputs": {"project_id": "other", "route": "fast", "job_ids": render["job_ids"]}, "config": {"enabled": False}}]}
    failed = run_split_workflow(application, payload, {})
    assert failed["status"] == "failed"
    assert "同一项目" in failed["error"]


def test_workflow_reference_text_quality_and_fast_render_nodes(tmp_path: Path) -> None:
    reference = tmp_path / "assets" / "voice_samples" / "workflow-reference.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(wav_bytes())
    gateway = WorkflowGateway()
    application = create_app(tmp_path, model_gateway=gateway, loudness_processor=PassThroughLoudness())
    created = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json={
                "name": "参考文本与双路线渲染",
                "nodes": [
                    {"node_id": "text", "node_type": "text.input", "inputs": {"text": "$input.text"}},
                    {
                        "node_id": "profile",
                        "node_type": "voice.profile",
                        "inputs": {"text": "$text.text", "character_id": "hero", "gender_hint": "male"},
                    },
                    {
                        "node_id": "reference_text",
                        "node_type": "voice.reference_text",
                        "inputs": {
                            "text": "$text.text",
                            "character_id": "hero",
                            "profile": "$profile.profile",
                        },
                    },
                    {
                        "node_id": "quality",
                        "node_type": "voice.quality_render",
                        "inputs": {
                            "text": "$input.render_text",
                            "reference_audio_url": "$input.reference_audio_url",
                            "reference_text": "$reference_text.reference_text",
                            "voice_prompt": "$profile.voice_prompt",
                            "quality_model": "gpt_sovits_v2",
                        },
                    },
                    {
                        "node_id": "fast",
                        "node_type": "voice.fast_render",
                        "inputs": {
                            "text": "$input.render_text",
                            "project_id": "$input.project_id",
                            "fast_voice_id": "bazong",
                            "fast_rvc_enabled": False,
                        },
                    },
                ],
            },
        )
    )
    assert created.status_code == 201
    started = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{created.json()['workflow_id']}/runs",
            json={
                "input": {
                    "text": "他沉着地作出决定。",
                    "render_text": "我们现在出发。",
                    "reference_audio_url": "/media/voice-samples/workflow-reference.wav",
                    "project_id": "workflow-project",
                }
            },
        )
    )
    completed = wait_for_run(application, started.json()["run_id"])
    assert completed["status"] == "completed"
    reference_output = completed["node_runs"][2]["outputs"]
    assert len(reference_output["reference_text"]) >= 20
    assert reference_output["backend"] == "rules"
    quality_output = completed["node_runs"][3]["outputs"]
    fast_output = completed["node_runs"][4]["outputs"]
    assert quality_output["audio_url"].startswith("/media/outputs/audio/jobs/")
    assert fast_output["audio_url"].startswith("/media/outputs/audio/jobs/")
    assert quality_output["audio_asset"]["source_job_id"] == quality_output["job_id"]
    assert fast_output["job"]["fast_voice_id"] == "bazong"
    assert gateway.calls == 2


def test_workflow_catalog_quality_render_requires_locked_version(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    created = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json={
                "name": "声音库版本锁定",
                "nodes": [
                    {
                        "node_id": "quality",
                        "node_type": "voice.quality_render",
                        "inputs": {
                            "text": "$input.text",
                            "catalog_asset_id": "$input.catalog_asset_id",
                        },
                    }
                ],
            },
        )
    )
    assert created.status_code == 201

    started = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{created.json()['workflow_id']}/runs",
            json={"input": {"text": "版本必须固定。", "catalog_asset_id": "female-young-clear-natural"}},
        )
    )
    failed = wait_for_run(application, started.json()["run_id"])

    assert failed["status"] == "failed"
    assert failed["node_runs"][0]["status"] == "failed"
    assert "必须锁定 catalog_version_id" in failed["node_runs"][0]["error"]


def test_workflow_runs_text_profile_reference_and_reuses_cache(tmp_path: Path) -> None:
    gateway = WorkflowGateway()
    application = create_app(tmp_path, model_gateway=gateway, loudness_processor=PassThroughLoudness())

    created = asyncio.run(request(application, "POST", "/api/workflows", json=workflow_payload()))
    assert created.status_code == 201
    workflow_id = created.json()["workflow_id"]
    assert asyncio.run(request(application, "GET", "/api/workflows")).json()[0]["workflow_id"] == workflow_id

    first = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{workflow_id}/runs",
            json={"input": {"text": "这是角色的身份线索。", "reference_text": "清晨的风穿过长街，我们仍从容前行。"}},
        )
    )
    assert first.status_code == 202
    completed = wait_for_run(application, first.json()["run_id"])
    assert completed["status"] == "completed"
    assert [node["status"] for node in completed["node_runs"]] == ["completed"] * 3
    assert completed["node_runs"][1]["outputs"]["profile"]["voice_prompt"]
    assert gateway.calls == 1

    second = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{workflow_id}/runs",
            json={"input": {"text": "这是角色的身份线索。", "reference_text": "清晨的风穿过长街，我们仍从容前行。"}},
        )
    )
    cached = wait_for_run(application, second.json()["run_id"])
    assert cached["status"] == "completed"
    assert [node["status"] for node in cached["node_runs"]] == ["skipped"] * 3
    assert gateway.calls == 1
    assert len(asyncio.run(request(application, "GET", "/api/workflows/runs")).json()) == 2

    restarted = create_app(tmp_path, model_gateway=gateway, loudness_processor=PassThroughLoudness())
    persisted = asyncio.run(request(restarted, "GET", f"/api/workflows/runs/{first.json()['run_id']}"))
    assert persisted.status_code == 200
    assert persisted.json()["status"] == "completed"


def test_workflow_pause_resume_and_cancel_are_persisted(tmp_path: Path) -> None:
    application = create_app(
        tmp_path,
        model_gateway=WorkflowGateway(),
        voice_analyzer=SlowVoiceAnalyzer(tmp_path),
        loudness_processor=PassThroughLoudness(),
    )
    created = asyncio.run(request(application, "POST", "/api/workflows", json=workflow_payload()))
    workflow_id = created.json()["workflow_id"]

    started = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{workflow_id}/runs",
            json={"input": {"text": "角色正在等待命令。", "reference_text": "清晨的风穿过长街，我们仍从容前行。"}},
        )
    )
    run_id = started.json()["run_id"]
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        current = asyncio.run(request(application, "GET", f"/api/workflows/runs/{run_id}")).json()
        if current["node_runs"][1]["status"] == "running":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("profile node did not start")

    paused = asyncio.run(request(application, "POST", f"/api/workflows/runs/{run_id}/pause"))
    assert paused.status_code == 202
    assert wait_for_run(application, run_id, {"paused"})["status"] == "paused"
    resumed = asyncio.run(request(application, "POST", f"/api/workflows/runs/{run_id}/resume"))
    assert resumed.status_code == 202
    assert wait_for_run(application, run_id)["status"] == "completed"

    second = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{workflow_id}/runs",
            json={"input": {"text": "这次运行将被取消。", "reference_text": "清晨的风穿过长街，我们仍从容前行。"}},
        )
    )
    cancel_id = second.json()["run_id"]
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        current = asyncio.run(request(application, "GET", f"/api/workflows/runs/{cancel_id}")).json()
        if current["node_runs"][1]["status"] == "running":
            break
        time.sleep(0.01)
    cancelled = asyncio.run(request(application, "POST", f"/api/workflows/runs/{cancel_id}/cancel"))
    assert cancelled.status_code == 202
    assert wait_for_run(application, cancel_id, {"cancelled"})["status"] == "cancelled"


def test_workflow_validation_and_failed_run_can_retry(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    invalid = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json={"name": "循环", "nodes": [{"node_id": "a", "node_type": "text.input", "depends_on": ["b"]}, {"node_id": "b", "node_type": "text.input", "depends_on": ["a"]}]},
        )
    )
    assert invalid.status_code == 422
    created = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json={"name": "失败后重试", "nodes": [{"node_id": "broken", "node_type": "unsupported.node"}]},
        )
    )
    workflow_id = created.json()["workflow_id"]
    started = asyncio.run(request(application, "POST", f"/api/workflows/{workflow_id}/runs", json={"input": {}}))
    failed = wait_for_run(application, started.json()["run_id"])
    assert failed["status"] == "failed"
    assert failed["node_runs"][0]["status"] == "failed"
    retried = asyncio.run(request(application, "POST", f"/api/workflows/runs/{started.json()['run_id']}/retry"))
    assert retried.status_code == 202
    failed_again = wait_for_run(application, started.json()["run_id"])
    assert failed_again["status"] == "failed"
    assert failed_again["retry_count"] == 1
    assert failed_again["node_runs"][0]["attempt"] == 2


def test_workflow_program_loudness_and_rvc_nodes_reprocess_quality_job(tmp_path: Path) -> None:
    reference = tmp_path / "assets" / "voice_samples" / "postprocess-reference.wav"
    reference.parent.mkdir(parents=True)
    reference.write_bytes(wav_bytes())
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    created = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json={
                "name": "质量后处理节点",
                "nodes": [
                    {
                        "node_id": "quality",
                        "node_type": "voice.quality_render",
                        "inputs": {
                            "text": "$input.text",
                            "project_id": "$input.project_id",
                            "reference_audio_url": "$input.reference_audio_url",
                            "voice_prompt": "沉着、清晰",
                        },
                    },
                    {
                        "node_id": "loudness",
                        "node_type": "voice.program_loudness",
                        "depends_on": ["quality"],
                        "inputs": {
                            "project_id": "$input.project_id",
                            "job_ids": ["$quality.job_id"],
                        },
                    },
                    {
                        "node_id": "rvc",
                        "node_type": "voice.rvc_stability",
                        "depends_on": ["loudness"],
                        "inputs": {
                            "project_id": "$input.project_id",
                            "job_ids": ["$quality.job_id"],
                        },
                    },
                ],
            },
        )
    )
    assert created.status_code == 201
    started = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{created.json()['workflow_id']}/runs",
            json={
                "input": {
                    "text": "后处理节点测试。",
                    "project_id": "postprocess-project",
                    "reference_audio_url": "/media/voice-samples/postprocess-reference.wav",
                }
            },
        )
    )
    completed = wait_for_run(application, started.json()["run_id"])
    assert completed["status"] == "completed"
    loudness = completed["node_runs"][1]["outputs"]
    assert loudness["job_count"] == 1
    assert loudness["audio_urls"][0].startswith("/media/outputs/audio/jobs/")
    assert loudness["jobs"][0]["loudness_metrics"]["processor"] == "test"
    rvc = completed["node_runs"][2]["outputs"]
    assert rvc["job_count"] == 1
    assert rvc["rvc_statuses"] == ["not_requested"]
    assert rvc["jobs"][0]["rvc_status"] == "not_requested"


def test_workflow_merge_node_outputs_program_audio(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    created = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json={
                "name": "极速合并导出",
                "nodes": [
                    {
                        "node_id": "first",
                        "node_type": "voice.fast_render",
                        "inputs": {"text": "第一段。", "project_id": "$input.project_id", "fast_voice_id": "bazong"},
                    },
                    {
                        "node_id": "second",
                        "node_type": "voice.fast_render",
                        "inputs": {"text": "第二段。", "project_id": "$input.project_id", "fast_voice_id": "bazong"},
                    },
                    {
                        "node_id": "merge",
                        "node_type": "voice.merge",
                        "depends_on": ["first", "second"],
                        "inputs": {
                            "project_id": "$input.project_id",
                            "route": "fast",
                            "job_ids": ["$first.job_id", "$second.job_id"],
                        },
                    },
                ],
            },
        )
    )
    assert created.status_code == 201
    started = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{created.json()['workflow_id']}/runs",
            json={"input": {"project_id": "merge-project"}},
        )
    )
    completed = wait_for_run(application, started.json()["run_id"])
    assert completed["status"] == "completed"
    output = completed["node_runs"][2]["outputs"]
    assert output["route"] == "fast"
    assert output["kind"] == "fast_render"
    assert output["segment_count"] == 2
    assert output["duration_seconds"] > 0
    assert output["audio_url"].startswith("/media/outputs/audio/merged/")
    assert output["audio_asset"]["segment_count"] == 2


def test_workflow_audio_nodes_persist_route_and_project_validation_errors(tmp_path: Path) -> None:
    application = create_app(tmp_path, model_gateway=WorkflowGateway(), loudness_processor=PassThroughLoudness())
    wrong_route = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json={
                "name": "错误路线",
                "nodes": [
                    {
                        "node_id": "merge",
                        "node_type": "audio.export",
                        "inputs": {"project_id": "$input.project_id", "route": "draft", "job_ids": ["missing"]},
                    }
                ],
            },
        )
    )
    run = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{wrong_route.json()['workflow_id']}/runs",
            json={"input": {"project_id": "validation-project"}},
        )
    )
    failed = wait_for_run(application, run.json()["run_id"])
    assert failed["status"] == "failed"
    assert failed["node_runs"][0]["status"] == "failed"
    assert "route 只能是 quality 或 fast" in failed["node_runs"][0]["error"]

    cross_project = asyncio.run(
        request(
            application,
            "POST",
            "/api/workflows",
            json={
                "name": "跨项目响度",
                "nodes": [
                    {
                        "node_id": "loudness",
                        "node_type": "audio.loudness",
                        "inputs": {"project_id": "$input.project_id", "job_ids": "$input.job_ids"},
                    }
                ],
            },
        )
    )
    run = asyncio.run(
        request(
            application,
            "POST",
            f"/api/workflows/{cross_project.json()['workflow_id']}/runs",
            json={"input": {"project_id": "other-project", "job_ids": ["missing-job"]}},
        )
    )
    failed = wait_for_run(application, run.json()["run_id"])
    assert failed["status"] == "failed"
    assert "响度处理列表中存在已丢失的任务" in failed["node_runs"][0]["error"]
