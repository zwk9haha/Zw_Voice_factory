import asyncio
import io
import json
import threading
import time
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app.main import create_app
from app.preparation import AnalysisActivityView, AnalysisAudit, AnalysisStructure, CharacterCandidate, PreparationService
from app.voice_analysis import (
    CharacterCandidateScreeningDecision,
    CharacterCandidateScreeningDraft,
    CharacterEvidencePack,
    CharacterVoiceProfile,
    CloudAnalysisEvent,
    ConfigurableVoiceAnalyzer,
    DirectorAnalysisDraft,
    DirectorPassageDecision,
    DirectorPassageEvidence,
    DirectorCharacter,
    ReferenceTextDraft,
    VoiceAnalysisCloudProfileUpdate,
    VoiceAnalysisConfigurationUpdate,
    VoiceAnalysisError,
    VoiceAnalysisStatus,
)


def test_atomic_json_write_retries_transient_permission_error(tmp_path: Path) -> None:
    path = tmp_path / "project.json"
    original_replace = Path.replace
    calls = 0

    def flaky_replace(source: Path, target: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(13, "sharing violation")
        return original_replace(source, target)

    with patch.object(Path, "replace", flaky_replace):
        PreparationService._write_json_file(path, {"status": "ok"})

    assert calls == 3
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "ok"}


class RegeneratingVoiceAnalyzer:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.evidence_packs: list[CharacterEvidencePack] = []

    def status(self) -> VoiceAnalysisStatus:
        return VoiceAnalysisStatus(
            backend="rules",
            available=True,
            detail="test analyzer",
            taxonomy_version=1,
        )

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        self.calls.append(evidence_pack.display_name)
        self.evidence_packs.append(evidence_pack)
        return CharacterVoiceProfile(
            gender=evidence_pack.gender_hint,
            age_range="young_adult",
            personality_tags=["重新分析"],
            timbre_tags=["中低音区", "适中", "均衡", "清亮", "温润", "混合共鸣"],
            delivery_tags=["咬字利落", "气息平稳", "语速平稳", "停连分明", "动态克制", "从容克制"],
            voice_constraints=["避免播音腔", "保持中性参考"],
            voice_prompt=f"重新生成的{evidence_pack.display_name}声线描述",
            confidence=0.91,
            rationale=f"依据 {len(evidence_pack.evidence)} 条角色证据重新分析",
            backend="rules",
        )

    def generate_reference_text(
        self,
        evidence_pack: CharacterEvidencePack,
        voice_prompt: str,
    ) -> ReferenceTextDraft:
        self.evidence_packs.append(evidence_pack)
        return ReferenceTextDraft(
            text=f"清晨的风穿过安静长街，{evidence_pack.display_name}从容确认方向，然后平稳说完这段中性参考句。",
            rationale="覆盖停连、轻重和常见发音组合",
            backend="rules",
        )


class CloudProgressVoiceAnalyzer(RegeneratingVoiceAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.director_batches: list[int] = []

    def status(self) -> VoiceAnalysisStatus:
        return VoiceAnalysisStatus(
            backend="cloud",
            available=True,
            model="gpt-progress-test",
            detail="test cloud analyzer",
            taxonomy_version=1,
        )

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        profile = super().analyze(evidence_pack)
        return profile.model_copy(update={"backend": "cloud", "model": "gpt-progress-test"})

    def analyze_director(
        self,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> DirectorAnalysisDraft:
        self.director_batches.append(len(passages))
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
                    rationale="test cloud director decision",
                )
                for passage in passages
            ],
            backend="cloud",
            model="gpt-progress-test",
        )


class ParallelCloudVoiceAnalyzer(CloudProgressVoiceAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.lock = threading.Lock()
        self.character_active = 0
        self.character_max_active = 0
        self.director_active = 0
        self.director_max_active = 0

    def cloud_analysis_parallelism(self) -> int:
        return 3

    def cloud_director_batch_size(self) -> int:
        return 4

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        with self.lock:
            self.character_active += 1
            self.character_max_active = max(self.character_max_active, self.character_active)
        try:
            time.sleep(0.04)
            return super().analyze(evidence_pack)
        finally:
            with self.lock:
                self.character_active -= 1

    def analyze_director(
        self,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> DirectorAnalysisDraft:
        with self.lock:
            self.director_active += 1
            self.director_max_active = max(self.director_max_active, self.director_active)
        try:
            time.sleep(0.04)
            return super().analyze_director(passages, characters)
        finally:
            with self.lock:
                self.director_active -= 1


class HybridCandidateScreeningVoiceAnalyzer(CloudProgressVoiceAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.screening_calls = 0

    def status(self) -> VoiceAnalysisStatus:
        return VoiceAnalysisStatus(
            backend="hybrid",
            available=True,
            model="local-test -> cloud-test",
            detail="test hybrid analyzer",
            taxonomy_version=1,
        )

    def screen_character_candidates(self, project_id, candidates, canonical_anchors):
        del project_id
        self.screening_calls += 1
        all_candidates = [*candidates, *canonical_anchors]
        decisions = []
        for candidate in candidates:
            action = "keep"
            canonical_candidate_id = None
            if candidate.display_name == "带着薰儿":
                action = "reject"
            elif candidate.display_name == "熏儿":
                action = "merge"
                canonical_candidate_id = next(
                    item.candidate_id for item in all_candidates if item.display_name == "萧熏儿"
                )
            decisions.append(
                CharacterCandidateScreeningDecision(
                    candidate_id=candidate.candidate_id,
                    action=action,
                    canonical_candidate_id=canonical_candidate_id,
                    confidence=0.95,
                    rationale="test local candidate screening",
                )
            )
        return CharacterCandidateScreeningDraft(
            decisions=decisions,
            backend="local",
            model="local-test",
        )


class FailOnceCharacterVoiceAnalyzer(RegeneratingVoiceAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.attempts: list[str] = []
        self.failed = False

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        self.attempts.append(evidence_pack.display_name)
        if not self.failed and len(self.attempts) == 2:
            self.failed = True
            raise VoiceAnalysisError("test character interruption")
        return super().analyze(evidence_pack)


class AlwaysFailCharacterVoiceAnalyzer(RegeneratingVoiceAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.attempts: list[str] = []

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        self.attempts.append(evidence_pack.display_name)
        raise VoiceAnalysisError("test exhausted cloud profile queue")


class BlockingCharacterVoiceAnalyzer(RegeneratingVoiceAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.attempts: list[str] = []

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        self.attempts.append(evidence_pack.display_name)
        if len(self.attempts) == 1:
            self.started.set()
            if not self.release.wait(timeout=5):
                raise VoiceAnalysisError("test cancellation release timed out")
        return super().analyze(evidence_pack)


class FailOnceDirectorVoiceAnalyzer(RegeneratingVoiceAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.director_attempts: list[int] = []
        self.failed = False

    def analyze_director(
        self,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> DirectorAnalysisDraft:
        self.director_attempts.append(len(passages))
        if not self.failed and len(self.director_attempts) == 2:
            self.failed = True
            raise RuntimeError("test director interruption")
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
                    rationale="test resumable director decision",
                )
                for passage in passages
            ],
            backend="rules",
        )


class RecordingLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str, *args: object) -> None:
        self.messages.append(message % args)


async def request(
    workspace_root: Path,
    method: str,
    path: str,
    voice_analyzer: RegeneratingVoiceAnalyzer | None = None,
    **kwargs: object,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app(workspace_root, voice_analyzer=voice_analyzer))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def import_source(
    workspace_root: Path,
    content: bytes,
    filename: str = "测试小说.txt",
    project_name: str | None = None,
) -> dict[str, object]:
    data = {"project_name": project_name} if project_name is not None else None
    response = asyncio.run(
        request(
            workspace_root,
            "POST",
            "/api/sources",
            data=data,
            files={"file": (filename, content, "text/plain")},
        )
    )
    assert response.status_code == 201
    return response.json()


def run_action(
    workspace_root: Path,
    project_id: str,
    action: str,
    *,
    revision_id: str | None = None,
    resume: bool = False,
    voice_analyzer: RegeneratingVoiceAnalyzer | None = None,
) -> httpx.Response:
    payload: dict[str, object] = {"action": action}
    if revision_id is not None:
        payload["revision_id"] = revision_id
    if resume:
        payload["resume"] = True
    return asyncio.run(
        request(
            workspace_root,
            "POST",
            f"/api/projects/{project_id}/preparation",
            voice_analyzer=voice_analyzer,
            json=payload,
        )
    )


def reference_wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 320)
    return buffer.getvalue()


def test_txt_upload_is_listed_with_detected_gb18030_encoding(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode("gb18030"))

    response = asyncio.run(request(tmp_path, "GET", "/api/sources"))

    assert response.status_code == 200
    assert response.json() == [
        {
            "project_id": imported["project_id"],
            "file_name": "测试小说.txt",
            "display_name": "测试小说",
            "size_bytes": 32,
            "encoding": "gb18030",
            "status": "imported",
        }
    ]


def test_named_project_keeps_source_and_manifest_inside_project_directory(tmp_path: Path) -> None:
    imported = import_source(
        tmp_path,
        "第一章 初见\n萧炎说道：\"开始吧。\"".encode(),
        filename="正文.txt",
        project_name="斗破苍穹 · 第一部",
    )
    project_id = str(imported["project_id"])
    project_dir = tmp_path / "outputs" / "projects" / project_id

    assert imported["display_name"] == "斗破苍穹 · 第一部"
    assert (project_dir / "source" / "正文.txt").read_text(encoding="utf-8").startswith("第一章")
    manifest = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    assert manifest["source_path"] == "source/正文.txt"
    assert manifest["display_name"] == "斗破苍穹 · 第一部"

    listed = asyncio.run(request(tmp_path, "GET", "/api/sources"))
    assert listed.status_code == 200
    assert listed.json()[0]["display_name"] == "斗破苍穹 · 第一部"

    duplicate = asyncio.run(
        request(
            tmp_path,
            "POST",
            "/api/sources",
            data={"project_name": "斗破苍穹 · 第一部"},
            files={"file": ("另一份.txt", b"text", "text/plain")},
        )
    )
    assert duplicate.status_code == 409


def test_concurrent_artifact_updates_serialize_project_manifest_writes(tmp_path: Path) -> None:
    service = PreparationService(tmp_path)
    source = service.import_source("story.txt", "第一章\n萧炎说道：\"开始。\"".encode(), "并发写入测试")
    manifest_path = tmp_path / "outputs" / "projects" / source.project_id / "project.json"
    original_write_json_file = service._write_json_file
    state_lock = threading.Lock()
    start_barrier = threading.Barrier(8)
    active_writes = 0
    max_active_writes = 0
    errors: list[BaseException] = []

    def guarded_write(path: Path, payload: object) -> None:
        nonlocal active_writes, max_active_writes
        is_manifest = path == manifest_path
        if is_manifest:
            with state_lock:
                active_writes += 1
                max_active_writes = max(max_active_writes, active_writes)
            time.sleep(0.01)
        try:
            original_write_json_file(path, payload)
        finally:
            if is_manifest:
                with state_lock:
                    active_writes -= 1

    service._write_json_file = guarded_write  # type: ignore[method-assign]

    def touch_manifest() -> None:
        try:
            start_barrier.wait()
            service._touch_project_manifest(source.project_id)
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=touch_manifest) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert errors == []
    assert max_active_writes == 1
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["project_id"] == source.project_id


def test_analysis_preview_survives_a_new_app_instance(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])

    response = run_action(tmp_path, project_id, "analyze")
    assert response.status_code == 200

    preview = asyncio.run(request(tmp_path, "GET", f"/api/projects/{project_id}/preparation/preview"))
    audit = preview.json()["analysis_audit"]
    assert audit["engine"] == "rule_based_preview"
    assert audit["structure"]["chapter_count"] == 1
    assert audit["structure"]["character_count"] == 18


def test_voice_analysis_status_exposes_the_test_safe_rules_backend(tmp_path: Path) -> None:
    response = asyncio.run(request(tmp_path, "GET", "/api/voice-analysis/status"))

    assert response.status_code == 200
    assert response.json() == {
        "backend": "rules",
        "available": True,
        "model": None,
        "detail": "规则兼容模式",
        "taxonomy_version": 1,
        "model_store": None,
    }


def test_voice_analysis_configuration_api_saves_cloud_settings_without_returning_key(tmp_path: Path) -> None:
    response = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            "/api/voice-analysis/config",
            json={
                "backend": "cloud",
                "provider": "qwen",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-plus",
                "api_protocol": "chat_completions",
                "api_key": "private-key",
            },
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert {key: payload[key] for key in (
        "backend", "provider", "base_url", "model", "api_protocol", "api_key_configured"
    )} == {
        "backend": "cloud",
        "provider": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "api_protocol": "chat_completions",
        "api_key_configured": True,
    }
    assert payload["failover_enabled"] is True
    assert payload["cloud_parallelism"] == 4
    assert payload["cloud_director_batch_size"] == 48
    assert len(payload["profiles"]) == 1
    assert payload["profiles"][0]["priority"] == 1
    assert payload["profiles"][0]["name"] == "默认云端 API"
    assert "private-key" not in response.text

    fetched = asyncio.run(request(tmp_path, "GET", "/api/voice-analysis/config"))
    assert fetched.json() == payload


def test_voice_analysis_models_api_uses_draft_credentials_without_exposing_key(tmp_path: Path) -> None:
    def respond(model_request: httpx.Request) -> httpx.Response:
        assert model_request.url.path == "/v1/models"
        assert model_request.headers["Authorization"] == "Bearer draft-key"
        return httpx.Response(200, json={"data": [{"id": "gpt-5.6-luna", "owned_by": "custom"}]})

    analyzer = ConfigurableVoiceAnalyzer(
        tmp_path,
        default_backend="rules",
        cloud_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    async def run_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(tmp_path, voice_analyzer=analyzer))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/voice-analysis/models",
                json={
                    "provider": "custom",
                    "base_url": "https://analysis.example/v1",
                    "api_key": "draft-key",
                },
            )

    response = asyncio.run(run_request())

    assert response.status_code == 200
    assert response.json()["models"][0]["id"] == "gpt-5.6-luna"
    assert "draft-key" not in response.text


def test_voice_analysis_profile_test_updates_only_the_selected_endpoint_health(tmp_path: Path) -> None:
    calls: list[str] = []

    def respond(model_request: httpx.Request) -> httpx.Response:
        calls.append(model_request.url.host or "")
        assert model_request.headers["Authorization"] == "Bearer secondary-key"
        body = json.loads(model_request.content)
        assert list(body["response_format"]["json_schema"]["schema"]["properties"]) == ["ok"]
        assert body["max_tokens"] == 16
        assert sum(len(message["content"]) for message in body["messages"]) < 140
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"ok":true}'
                        }
                    }
                ]
            },
        )

    analyzer = ConfigurableVoiceAnalyzer(
        tmp_path,
        default_backend="rules",
        cloud_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    analyzer.update_configuration(
        VoiceAnalysisConfigurationUpdate(
            backend="cloud",
            profiles=[
                VoiceAnalysisCloudProfileUpdate(
                    profile_id="primary",
                    name="Primary",
                    provider="custom",
                    base_url="https://primary.example/v1",
                    model="primary-model",
                    api_protocol="chat_completions",
                    api_key="primary-key",
                ),
                VoiceAnalysisCloudProfileUpdate(
                    profile_id="secondary",
                    name="Secondary",
                    provider="custom",
                    base_url="https://secondary.example/v1",
                    model="secondary-model",
                    api_protocol="chat_completions",
                    api_key="secondary-key",
                ),
            ],
        )
    )

    async def run_request() -> httpx.Response:
        transport = httpx.ASGITransport(app=create_app(tmp_path, voice_analyzer=analyzer))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/voice-analysis/profiles/secondary/test")

    response = asyncio.run(run_request())

    assert response.status_code == 200
    payload = response.json()
    assert calls == ["secondary.example"]
    assert payload["profiles"][0]["health"] == "unknown"
    assert payload["profiles"][1]["health"] == "healthy"
    assert "primary-key" not in response.text
    assert "secondary-key" not in response.text


def test_character_extraction_separates_accepted_identity_from_false_positive(tmp_path: Path) -> None:
    text = "第一章 初见\n萧炎说道：\"开始吧。\"\n少年点了点头。\n药老问道：\"准备好了吗？\""
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")

    response = run_action(tmp_path, project_id, "extract_characters")

    assert response.status_code == 200
    payload = response.json()
    assert [character["display_name"] for character in payload["character_voice_bible"]["characters"]] == [
        "旁白",
        "萧炎",
        "药老",
    ]
    assert any(
        candidate["display_name"] == "少年" and candidate["decision"] == "rejected"
        for candidate in payload["analysis_audit"]["candidates"]
    )
    bible = payload["character_voice_bible"]
    assert bible["schema_version"] == 2
    assert bible["analysis_backend"] == "rules"
    xiao_yan = next(character for character in bible["characters"] if character["display_name"] == "萧炎")
    assert xiao_yan["timbre_tags"] == ["中音区", "适中", "均衡", "干净", "混合共鸣"]
    assert xiao_yan["delivery_tags"]
    assert xiao_yan["voice_constraints"] == ["保持自然口语", "中性参考不携带场景情绪"]
    assert xiao_yan["voice_profile_confidence"] == 0.45


def test_hybrid_character_extraction_screens_candidates_before_cloud_profiles(tmp_path: Path) -> None:
    analyzer = HybridCandidateScreeningVoiceAnalyzer()
    service = PreparationService(tmp_path, analyzer)
    imported = service.import_source(
        "long-fiction.txt",
        "第一章 初见\n萧炎说道：\"开始吧。\"\n萧熏儿答道：\"我会跟上。\"".encode(),
    )
    service.run(imported.project_id, "analyze")
    audit_path = tmp_path / "outputs" / "projects" / imported.project_id / "analysis_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["candidates"] = [
        {
            "candidate_id": "candidate-xiao-yan",
            "display_name": "萧炎",
            "decision": "pending",
            "confidence": 0.98,
            "mention_count": 120,
            "dialogue_count": 18,
            "peak_batch_mentions": 40,
            "peak_batch_dialogue_count": 8,
            "batch_presence_count": 4,
            "local_importance": 0.95,
            "evidence": ["萧炎说道：\"开始吧。\""],
            "reason": "rule candidate",
        },
        {
            "candidate_id": "candidate-xun-er",
            "display_name": "萧熏儿",
            "decision": "pending",
            "confidence": 0.98,
            "mention_count": 80,
            "dialogue_count": 12,
            "peak_batch_mentions": 30,
            "peak_batch_dialogue_count": 6,
            "batch_presence_count": 3,
            "local_importance": 0.88,
            "evidence": ["萧熏儿答道：\"我会跟上。\""],
            "reason": "rule candidate",
        },
        {
            "candidate_id": "candidate-xun-er-alias",
            "display_name": "熏儿",
            "decision": "pending",
            "confidence": 0.84,
            "mention_count": 24,
            "dialogue_count": 4,
            "peak_batch_mentions": 10,
            "peak_batch_dialogue_count": 2,
            "batch_presence_count": 2,
            "local_importance": 0.55,
            "evidence": ["熏儿轻声说道：\"我知道。\""],
            "reason": "rule candidate",
        },
        {
            "candidate_id": "candidate-false",
            "display_name": "带着薰儿",
            "decision": "pending",
            "confidence": 0.76,
            "mention_count": 2,
            "dialogue_count": 1,
            "peak_batch_mentions": 2,
            "peak_batch_dialogue_count": 1,
            "batch_presence_count": 1,
            "local_importance": 0.28,
            "evidence": ["带着薰儿，萧炎说道：\"走吧。\""],
            "reason": "rule candidate",
        },
    ]
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    preview = service.run(imported.project_id, "extract_characters")

    assert analyzer.screening_calls == 1
    assert analyzer.calls == ["萧炎", "萧熏儿"]
    assert [character.display_name for character in preview.character_voice_bible.characters] == [
        "旁白",
        "萧炎",
        "萧熏儿",
    ]
    xun_er = next(character for character in preview.character_voice_bible.characters if character.display_name == "萧熏儿")
    assert xun_er.aliases == ["熏儿"]
    screened_audit = preview.analysis_audit
    assert screened_audit is not None
    assert screened_audit.candidate_screening_backend == "local"
    assert screened_audit.candidate_screening_input_count == 1
    assert screened_audit.candidate_deterministic_kept_count == 2
    assert screened_audit.candidate_deterministic_rejected_count == 1
    assert screened_audit.candidate_screening_kept_count == 2
    rejected = next(candidate for candidate in screened_audit.candidates if candidate.display_name == "带着薰儿")
    assert rejected.decision == "rejected"
    merged = next(candidate for candidate in screened_audit.candidates if candidate.display_name == "熏儿")
    assert merged.decision == "rejected"
    assert merged.canonical_candidate_id == "candidate-xun-er"


def test_candidate_scan_removes_speech_manner_suffixes_and_rejects_non_entities(tmp_path: Path) -> None:
    service = PreparationService(tmp_path)

    candidates = service._scan_candidates(
        "萧炎干笑道：\"继续。\"\n萧炎冷笑道：\"不会停。\"\n"
        "薄怒说道：\"错误候选。\"\n薄怒说道：\"仍是错误候选。\""
    )
    by_name = {candidate.display_name: candidate for candidate in candidates}

    assert "萧炎" in by_name
    assert by_name["萧炎"].dialogue_count == 2
    assert "萧炎干" not in by_name
    assert by_name["薄怒"].decision == "rejected"
    assert by_name["薄怒"].entity_confidence <= 0.02


def test_candidate_routes_keep_clear_names_reject_actions_and_model_screen_uncertain_aliases() -> None:
    candidates = [
        CharacterCandidate(
            candidate_id="candidate-xiao-yan",
            display_name="萧炎",
            decision="pending",
            confidence=0.94,
            mention_count=8,
            dialogue_count=3,
            peak_batch_dialogue_count=2,
            batch_presence_count=2,
            entity_confidence=0.9,
            evidence=["萧炎说道。"],
            reason="candidate",
        ),
        CharacterCandidate(
            candidate_id="candidate-xun-er",
            display_name="熏儿",
            decision="pending",
            confidence=0.9,
            mention_count=100,
            dialogue_count=20,
            peak_batch_dialogue_count=12,
            batch_presence_count=4,
            entity_confidence=0.9,
            evidence=["熏儿说道。"],
            reason="candidate",
        ),
        CharacterCandidate(
            candidate_id="candidate-gan-xiao",
            display_name="干笑",
            decision="pending",
            confidence=0.95,
            mention_count=200,
            dialogue_count=30,
            entity_confidence=0.9,
            evidence=["干笑道。"],
            reason="candidate",
        ),
        CharacterCandidate(
            candidate_id="candidate-yun-shan",
            display_name="云山",
            decision="pending",
            confidence=0.82,
            mention_count=1,
            dialogue_count=1,
            entity_confidence=0.72,
            evidence=["云山说道。"],
            reason="candidate",
        ),
    ]

    PreparationService._apply_deterministic_candidate_routes(candidates)

    by_name = {candidate.display_name: candidate for candidate in candidates}
    assert by_name["萧炎"].screening_route == "deterministic_keep"
    assert by_name["干笑"].screening_route == "deterministic_reject"
    assert by_name["熏儿"].screening_route == "model"
    assert by_name["云山"].screening_route == "model"


def test_candidate_priority_favors_first_slice_and_cross_batch_presence_over_local_spike() -> None:
    spike = CharacterCandidate(
        candidate_id="candidate-spike",
        display_name="萧峰",
        decision="pending",
        confidence=0.9,
        mention_count=100,
        dialogue_count=100,
        peak_batch_mentions=100,
        peak_batch_dialogue_count=100,
        batch_presence_count=1,
        entity_confidence=0.9,
        evidence=["萧峰说道。"],
        reason="single batch spike",
    )
    rolling = CharacterCandidate(
        candidate_id="candidate-rolling",
        display_name="云山",
        decision="pending",
        confidence=0.9,
        mention_count=60,
        dialogue_count=40,
        peak_batch_mentions=15,
        peak_batch_dialogue_count=10,
        batch_presence_count=4,
        first_batch_mentions=15,
        first_batch_dialogue_count=10,
        entity_confidence=0.9,
        evidence=["云山说道。"],
        reason="rolling role",
    )

    PreparationService._score_candidates([spike, rolling], batch_count=4)

    assert rolling.production_priority > spike.production_priority
    assert spike.local_importance == 0.95
    assert rolling.local_importance < spike.local_importance


def test_historical_analysis_audit_defaults_new_candidate_routing_fields() -> None:
    audit = AnalysisAudit.model_validate(
        {
            "schema_version": 3,
            "project_id": "project-legacy",
            "source_file": "legacy.txt",
            "structure": {
                "chapter_count": 1,
                "character_count": 20,
                "nonempty_line_count": 1,
                "estimated_segment_count": 1,
                "dialogue_count": 1,
            },
            "candidates": [
                {
                    "candidate_id": "candidate-legacy",
                    "display_name": "萧炎",
                    "decision": "pending",
                    "confidence": 0.8,
                    "mention_count": 1,
                    "dialogue_count": 1,
                    "reason": "legacy",
                }
            ],
        }
    )

    assert audit.candidates[0].entity_confidence == 0
    assert audit.candidates[0].production_priority == 0
    assert audit.candidates[0].screening_route is None


def test_long_form_immediate_profiles_are_bounded_and_deferred_roles_use_archetypes(tmp_path: Path) -> None:
    analyzer = CloudProgressVoiceAnalyzer()
    service = PreparationService(tmp_path, analyzer)
    imported = service.import_source("large-cast.txt", "萧炎说道：\"开始。\"".encode())
    service.run(imported.project_id, "analyze")
    audit_path = tmp_path / "outputs" / "projects" / imported.project_id / "analysis_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["long_form_plan"]["is_long_form"] = True
    audit["candidates"] = [
        {
            "candidate_id": f"candidate-{index:02d}",
            "display_name": f"萧{chr(0x4e00 + index)}",
            "decision": "accepted",
            "confidence": 0.9,
            "mention_count": 40 - index,
            "dialogue_count": 40 - index,
            "peak_batch_mentions": 40 - index,
            "peak_batch_dialogue_count": 40 - index,
            "batch_presence_count": 2,
            "first_batch_mentions": 40 - index,
            "first_batch_dialogue_count": 40 - index,
            "entity_confidence": 0.9,
            "production_priority": round(0.98 - index * 0.01, 3),
            "local_importance": 0.95,
            "evidence": [f"萧{chr(0x4e00 + index)}说道：\"开始。\""],
            "reason": "test candidate",
        }
        for index in range(30)
    ]
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    preview = service.run(imported.project_id, "extract_characters")

    assert len(analyzer.calls) == 24
    roles = [character for character in preview.character_voice_bible.characters if character.character_id != "narrator"]
    assert len(roles) == 30
    assert sum(character.archetype_id is not None for character in roles) == 6
    reference_by_character = {
        item.source_character_id: item
        for item in preview.reference_plan.items
        if item.selection_mode != "narrator_default"
    }
    assert all(
        reference_by_character[character.character_id].selected is False
        for character in roles
        if character.archetype_id is not None
    )


def test_character_extraction_bounds_analysis_aliases_but_preserves_project_aliases(tmp_path: Path) -> None:
    analyzer = CloudProgressVoiceAnalyzer()
    service = PreparationService(tmp_path, analyzer)
    imported = service.import_source(
        "many-aliases.txt",
        "第一章 初见\n药老说道：\"准备好了。\"".encode(),
    )
    service.run(imported.project_id, "analyze")
    audit_path = tmp_path / "outputs" / "projects" / imported.project_id / "analysis_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    canonical_id = "candidate-yao-lao"
    aliases = [f"药老别名{index:02d}" for index in range(28)]
    audit["candidates"] = [
        {
            "candidate_id": canonical_id,
            "display_name": "药老",
            "decision": "accepted",
            "confidence": 0.98,
            "mention_count": 100,
            "dialogue_count": 20,
            "local_importance": 0.95,
            "evidence": ["药老平静地说道：\"准备好了。\""],
            "reason": "canonical character",
            "screening_action": "keep",
        },
        *[
            {
                "candidate_id": f"candidate-alias-{index:02d}",
                "display_name": alias,
                "decision": "rejected",
                "confidence": 0.8,
                "mention_count": 1,
                "dialogue_count": 1,
                "local_importance": 0.3,
                "evidence": [f"{alias}说道：\"准备好了。\""],
                "reason": "merged alias",
                "screening_action": "merge",
                "canonical_candidate_id": canonical_id,
            }
            for index, alias in enumerate(aliases)
        ],
    ]
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    preview = service.run(imported.project_id, "extract_characters")

    character = next(item for item in preview.character_voice_bible.characters if item.display_name == "药老")
    assert character.aliases == aliases
    assert analyzer.evidence_packs[0].aliases == aliases[:12]


def test_candidate_screening_rejects_unrelated_merge_targets() -> None:
    candidates = {
        "candidate-yao-lao": CharacterCandidate(
            candidate_id="candidate-yao-lao",
            display_name="药老",
            decision="accepted",
            confidence=0.98,
            mention_count=100,
            dialogue_count=20,
            evidence=["药老说道。"],
            reason="canonical",
            screening_action="keep",
        ),
        "candidate-yun-shan": CharacterCandidate(
            candidate_id="candidate-yun-shan",
            display_name="云山",
            decision="pending",
            confidence=0.9,
            mention_count=20,
            dialogue_count=5,
            evidence=["云山说道。"],
            reason="candidate",
        ),
        "candidate-man-zui": CharacterCandidate(
            candidate_id="candidate-man-zui",
            display_name="满嘴",
            decision="pending",
            confidence=0.7,
            mention_count=2,
            dialogue_count=1,
            evidence=["满嘴说道。"],
            reason="fragment",
        ),
        "candidate-xun-er": CharacterCandidate(
            candidate_id="candidate-xun-er",
            display_name="熏儿",
            decision="accepted",
            confidence=0.98,
            mention_count=80,
            dialogue_count=12,
            evidence=["熏儿说道。"],
            reason="canonical",
            screening_action="keep",
        ),
        "candidate-xiao-xun-er": CharacterCandidate(
            candidate_id="candidate-xiao-xun-er",
            display_name="萧薰儿",
            decision="pending",
            confidence=0.9,
            mention_count=20,
            dialogue_count=5,
            evidence=["萧薰儿说道。"],
            reason="alias",
        ),
    }
    batch = [
        candidates["candidate-yun-shan"],
        candidates["candidate-man-zui"],
        candidates["candidate-xiao-xun-er"],
    ]
    draft = CharacterCandidateScreeningDraft(
        decisions=[
            CharacterCandidateScreeningDecision(
                candidate_id=candidate.candidate_id,
                action="merge",
                canonical_candidate_id=(
                    "candidate-xun-er" if candidate.display_name == "萧薰儿" else "candidate-yao-lao"
                ),
                confidence=0.95,
                rationale="model requested merge",
            )
            for candidate in batch
        ],
        backend="local",
        model="local-test",
    )

    PreparationService._apply_candidate_screening_draft(candidates, batch, draft)

    assert candidates["candidate-yun-shan"].screening_action == "keep"
    assert candidates["candidate-yun-shan"].canonical_candidate_id is None
    assert candidates["candidate-man-zui"].screening_action == "reject"
    assert candidates["candidate-man-zui"].canonical_candidate_id is None
    assert candidates["candidate-xiao-xun-er"].screening_action == "merge"
    assert candidates["candidate-xiao-xun-er"].canonical_candidate_id == "candidate-xun-er"


def test_old_unrelated_merges_only_rescreen_plausible_names() -> None:
    audit = AnalysisAudit(
        project_id="project-old-merges",
        source_file="novel.txt",
        structure=AnalysisStructure(
            chapter_count=1,
            character_count=100,
            nonempty_line_count=3,
            estimated_segment_count=3,
            dialogue_count=2,
        ),
        candidates=[
            CharacterCandidate(
                candidate_id="candidate-yao-lao",
                display_name="药老",
                decision="accepted",
                confidence=0.98,
                mention_count=100,
                dialogue_count=20,
                evidence=["药老说道。"],
                reason="canonical",
                screening_action="keep",
            ),
            CharacterCandidate(
                candidate_id="candidate-yun-shan",
                display_name="云山",
                decision="rejected",
                confidence=0.9,
                mention_count=20,
                dialogue_count=5,
                evidence=["云山说道。"],
                reason="old merge",
                screening_action="merge",
                canonical_candidate_id="candidate-yao-lao",
            ),
            CharacterCandidate(
                candidate_id="candidate-man-zui",
                display_name="满嘴",
                decision="rejected",
                confidence=0.7,
                mention_count=3,
                dialogue_count=1,
                evidence=["满嘴苦涩地说道。"],
                reason="old merge",
                screening_action="merge",
                canonical_candidate_id="candidate-yao-lao",
            ),
        ],
    )

    PreparationService._invalidate_unrelated_candidate_merges(audit)

    yun_shan = next(item for item in audit.candidates if item.display_name == "云山")
    man_zui = next(item for item in audit.candidates if item.display_name == "满嘴")
    assert yun_shan.decision == "pending"
    assert yun_shan.screening_action is None
    assert man_zui.decision == "rejected"
    assert man_zui.screening_action == "reject"
    assert man_zui.screening_confidence == 1.0


def test_character_extraction_reports_cloud_progress_to_runtime_logger(tmp_path: Path) -> None:
    analyzer = CloudProgressVoiceAnalyzer()
    runtime_logger = RecordingLogger()
    service = PreparationService(tmp_path, analyzer, runtime_logger=runtime_logger)
    imported = service.import_source(
        "cloud-progress.txt",
        "第一章 初见\n萧炎说道：\"开始吧。\"\n药老问道：\"准备好了吗？\"".encode(),
    )
    service.run(imported.project_id, "analyze")

    service.run(imported.project_id, "extract_characters")

    progress = "\n".join(runtime_logger.messages)
    assert "[ANALYSIS" in progress
    assert "云端角色音色分析开始" in progress
    assert "正在分析 1/2" in progress
    assert "已完成 2/2" in progress
    assert "100% 云端角色音色分析完成" in progress
    assert "model=gpt-progress-test" in progress
    assert "[----------------------------]" in progress
    assert "[############################]" in progress


def test_project_analysis_activity_exposes_progress_and_cloud_io(tmp_path: Path) -> None:
    analyzer = CloudProgressVoiceAnalyzer()
    service = PreparationService(tmp_path, analyzer)
    imported = service.import_source(
        "analysis-activity.txt",
        "第一章 初见\n萧炎说道：\"开始吧。\"".encode(),
    )
    service.run(imported.project_id, "analyze")
    service.run(imported.project_id, "extract_characters")
    service.record_cloud_analysis_event(
        CloudAnalysisEvent(
            project_id=imported.project_id,
            call_id="call-1",
            direction="INPUT",
            operation="character_profile",
            provider="custom",
            protocol="responses",
            model="gpt-test",
            attempt=1,
            structured_mode="json_schema",
            total_chars=18,
            preview="compact request body",
        )
    )
    service.record_cloud_analysis_event(
        CloudAnalysisEvent(
            project_id=imported.project_id,
            call_id="call-1",
            direction="OUTPUT",
            operation="character_profile",
            provider="custom",
            protocol="responses",
            model="gpt-test",
            attempt=1,
            structured_mode="json_schema",
            total_chars=16,
            preview="compact response",
            status_code=200,
            elapsed_seconds=1.25,
        )
    )

    response = asyncio.run(
        request(tmp_path, "GET", f"/api/projects/{imported.project_id}/analysis-activity")
    )

    assert response.status_code == 200
    activity = response.json()
    assert activity["state"] == "complete"
    assert activity["percent"] == 100
    assert activity["backend"] == "cloud"
    assert activity["input_events"][0]["preview"] == "compact request body"
    assert activity["output_events"][0]["elapsed_seconds"] == 1.25


def test_interrupted_analysis_activity_stops_elapsed_time_and_can_resume(tmp_path: Path) -> None:
    service = PreparationService(tmp_path, RegeneratingVoiceAnalyzer())
    imported = service.import_source(
        "interrupted-analysis.txt",
        "第一章 初见\n萧炎说道：\"开始吧。\"".encode(),
    )
    started_at = datetime.now(timezone.utc) - timedelta(minutes=12)
    last_checkpoint = started_at + timedelta(seconds=95)
    service._write_model(
        imported.project_id,
        "analysis_activity.json",
        AnalysisActivityView(
            project_id=imported.project_id,
            action="generate_director",
            state="running",
            percent=11,
            message="已完成批次 5/42",
            started_at=started_at,
            elapsed_seconds=95,
            updated_at=last_checkpoint,
        ),
    )

    restarted = PreparationService(tmp_path, RegeneratingVoiceAnalyzer())
    activity = restarted.analysis_activity(imported.project_id)

    assert activity.state == "failed"
    assert activity.percent == 11
    assert activity.elapsed_seconds == 95
    assert activity.completed_at == last_checkpoint
    assert "继续" in activity.message


def test_local_director_uses_larger_serial_batches(tmp_path: Path) -> None:
    service = PreparationService(tmp_path, RegeneratingVoiceAnalyzer())
    status = VoiceAnalysisStatus(
        backend="local",
        available=True,
        model="zw-voice-analyzer:4b",
        detail="test local analyzer",
        taxonomy_version=1,
    )

    assert service._project_analysis_parallelism("missing-project", status) == 1
    assert service._cloud_director_batch_size(status) == 24


def test_cloud_director_analysis_uses_larger_batches_and_reports_progress(tmp_path: Path) -> None:
    analyzer = CloudProgressVoiceAnalyzer()
    runtime_logger = RecordingLogger()
    service = PreparationService(tmp_path, analyzer, runtime_logger=runtime_logger)
    dialogue_lines = [f"\"这是第{index + 1}句待判定对白。\"" for index in range(49)]
    imported = service.import_source(
        "cloud-director-progress.txt",
        ("第一章 初见\n萧炎说道：\"角色锚点。\"\n" + "\n".join(dialogue_lines)).encode(),
    )
    service.run(imported.project_id, "analyze")
    service.run(imported.project_id, "extract_characters")
    runtime_logger.messages.clear()

    service.run(imported.project_id, "generate_director")

    assert sorted(analyzer.director_batches) == [1, 48]
    progress = "\n".join(runtime_logger.messages)
    assert "云端导演分析开始" in progress
    assert "正在分析批次 1/2" in progress
    assert "已完成批次 2/2" in progress
    assert "100% 云端导演分析完成" in progress
    assert "[----------------------------]" in progress
    assert "[############################]" in progress


def test_director_rule_bypass_requires_unambiguous_speaker_attribution() -> None:
    assert PreparationService._has_unambiguous_director_attribution(
        DirectorPassageEvidence(
            passage_id="direct",
            text="开始吧。",
            context="萧炎平静地说道：\"开始吧。\"",
            explicit_speaker="萧炎",
        )
    )
    assert not PreparationService._has_unambiguous_director_attribution(
        DirectorPassageEvidence(
            passage_id="ambiguous",
            text="开始吧。",
            context="薰儿看着萧炎哥哥笑着说道：\"开始吧。\"",
            explicit_speaker="萧炎",
        )
    )


def test_cloud_character_and_director_analysis_use_bounded_parallelism(tmp_path: Path) -> None:
    analyzer = ParallelCloudVoiceAnalyzer()
    service = PreparationService(tmp_path, analyzer)
    dialogue_lines = [
        f'{name}说道："{name}的第{round_index + 1}句对白。"'
        for round_index in range(2)
        for name in ("萧炎", "药老", "萧薰儿", "纳兰嫣然")
    ]
    ambiguous_lines = [f'"第{index + 1}句待判定对白。"' for index in range(8)]
    imported = service.import_source(
        "cloud-parallel.txt",
        ("第一章 初见\n" + "\n".join([*dialogue_lines, *ambiguous_lines])).encode(),
    )
    service.run(imported.project_id, "analyze")
    service.run(imported.project_id, "extract_characters")

    assert analyzer.character_max_active >= 2

    analyzer.director_batches.clear()
    analyzer.director_max_active = 0
    service.run(imported.project_id, "generate_director")

    assert sorted(analyzer.director_batches) == [4, 4]
    assert analyzer.director_max_active >= 2


def test_character_extraction_builds_reference_plan_from_importance(tmp_path: Path) -> None:
    text = (
        "第一章 初见\n"
        "萧炎说道：\"开始吧。\"\n"
        "萧炎问道：\"准备好了吗？\"\n"
        "萧炎答道：\"那就出发。\"\n"
        "药老说道：\"且慢。\""
    )
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")

    response = run_action(tmp_path, project_id, "extract_characters")

    assert response.status_code == 200
    assert response.json()["reference_plan"]["automatic_threshold"] == 0.1
    items = response.json()["reference_plan"]["items"]
    by_name = {item["display_name"]: item for item in items}
    assert by_name["男旁白"]["selection_mode"] == "narrator_default"
    assert by_name["女旁白"]["selection_mode"] == "narrator_default"
    assert by_name["男旁白"]["selected"] is True
    assert by_name["女旁白"]["locked"] is True
    assert "成年男性长篇旁白基线" in by_name["男旁白"]["voice_prompt"]
    assert "成年女性长篇旁白基线" in by_name["女旁白"]["voice_prompt"]
    assert "长篇一致性" in by_name["男旁白"]["voice_prompt"]
    assert "长篇一致性" in by_name["女旁白"]["voice_prompt"]
    assert by_name["萧炎"]["selection_mode"] == "automatic"
    assert by_name["萧炎"]["selected"] is True
    assert by_name["药老"]["selection_mode"] == "automatic"
    assert by_name["药老"]["reuse_reference_id"] == by_name["男旁白"]["reference_id"]

    adjusted = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/reference-settings",
            json={"automatic_threshold": 0.75},
        )
    )
    assert adjusted.status_code == 200
    adjusted_by_name = {
        item["display_name"]: item
        for item in adjusted.json()["reference_plan"]["items"]
    }
    assert adjusted_by_name["萧炎"]["selection_mode"] == "automatic"
    assert adjusted_by_name["药老"]["selection_mode"] == "optional"
    assert adjusted_by_name["药老"]["selected"] is False


def test_only_optional_reference_items_can_be_toggled(tmp_path: Path) -> None:
    text = (
        "第一章 初见\n"
        "萧炎说道：\"开始吧。\"\n"
        "萧炎问道：\"准备好了吗？\"\n"
        "萧炎答道：\"那就出发。\"\n"
        "纳兰嫣然说道：\"她会准时到场。\""
    )
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    extracted = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/reference-settings",
            json={"automatic_threshold": 0.75},
        )
    ).json()
    by_name = {item["display_name"]: item for item in extracted["reference_plan"]["items"]}

    optional = by_name["纳兰嫣然"]
    assert optional["gender"] == "female"
    assert optional["reuse_reference_id"] == by_name["女旁白"]["reference_id"]
    selected = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/references/{optional['reference_id']}",
            json={"selected": True},
        )
    )
    assert selected.status_code == 200
    selected_item = next(
        item for item in selected.json()["reference_plan"]["items"]
        if item["reference_id"] == optional["reference_id"]
    )
    assert selected_item["selected"] is True

    automatic = by_name["萧炎"]
    locked = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/references/{automatic['reference_id']}",
            json={"selected": False},
        )
    )
    assert locked.status_code == 409


def test_reference_voice_prompt_updates_the_reference_and_character_bible(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    reference = next(
        item for item in extracted["reference_plan"]["items"]
        if item["display_name"] == "萧炎"
    )

    response = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/references/{reference['reference_id']}",
            json={"voice_prompt": "青年男声，清亮克制，吐字自然，保持中性情绪"},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    updated_reference = next(
        item for item in payload["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    updated_character = next(
        item for item in payload["character_voice_bible"]["characters"]
        if item["character_id"] == reference["source_character_id"]
    )
    assert updated_reference["voice_prompt"] == "青年男声，清亮克制，吐字自然，保持中性情绪"
    assert updated_character["voice_prompt"] == updated_reference["voice_prompt"]


def test_automatic_reference_can_be_toggled_after_the_selection_lock_is_disabled(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    reference = next(
        item for item in extracted["reference_plan"]["items"]
        if item["display_name"] == "萧炎"
    )
    assert extracted["reference_plan"]["automatic_items_locked"] is True
    assert reference["locked"] is True

    unlocked = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/reference-settings",
            json={"automatic_items_locked": False},
        )
    )

    assert unlocked.status_code == 200
    unlocked_items = unlocked.json()["reference_plan"]["items"]
    unlocked_reference = next(item for item in unlocked_items if item["reference_id"] == reference["reference_id"])
    assert unlocked_reference["locked"] is False
    assert all(item["locked"] is True for item in unlocked_items if item["selection_mode"] == "narrator_default")

    deselected = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/references/{reference['reference_id']}",
            json={"selected": False},
        )
    )
    assert deselected.status_code == 200
    updated = next(
        item for item in deselected.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert updated["selection_mode"] == "automatic"
    assert updated["selected"] is False


def test_character_extraction_does_not_turn_speech_modifiers_into_names(tmp_path: Path) -> None:
    text = (
        "第一章 初见\n"
        "萧炎说道：\"开始吧。\"\n"
        "\"谁知道这件事？\"萧炎苦涩的道。\n"
        "纳兰嫣然淡淡的道：\"我明白。\"\n"
        "想要知道答案并不容易，前途可以慢慢考虑。"
    )
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")

    response = run_action(tmp_path, project_id, "extract_characters")

    assert [character["display_name"] for character in response.json()["character_voice_bible"]["characters"]] == [
        "旁白",
        "萧炎",
        "纳兰嫣然",
    ]


def test_character_extraction_merges_variant_spelling_as_an_alias(tmp_path: Path) -> None:
    text = "第一章 初见\n萧薰儿说道：\"走吧。\"\n熏儿问道：\"萧炎哥哥呢？\""
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")

    response = run_action(tmp_path, project_id, "extract_characters")

    characters = response.json()["character_voice_bible"]["characters"]
    assert [character["display_name"] for character in characters] == ["旁白", "萧薰儿"]
    assert characters[1]["aliases"] == ["熏儿"]

    director = run_action(tmp_path, project_id, "generate_director").json()["director_doc"]
    alias_segment = next(segment for segment in director["segments"] if "萧炎哥哥呢" in segment["text"])
    assert alias_segment["character_id"] == characters[1]["character_id"]


def test_director_generation_references_stable_character_ids(tmp_path: Path) -> None:
    text = "第一章 初见\n萧炎说道：\"开始吧。\"\n药老问道：\"准备好了吗？\""
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    run_action(tmp_path, project_id, "extract_characters")

    response = run_action(tmp_path, project_id, "generate_director")

    assert response.status_code == 200
    segments = response.json()["director_doc"]["segments"]
    characters = response.json()["character_voice_bible"]["characters"]
    identity_ids = {character["display_name"]: character["character_id"] for character in characters}
    character_ids = {segment["character_id"] for segment in segments}
    assert identity_ids["萧炎"] in character_ids
    assert identity_ids["药老"] in character_ids
    assert all(segment["segment_id"].startswith("seg-") for segment in segments)


def test_director_generation_separates_dialogue_from_narration_and_resolves_cross_line_speaker(tmp_path: Path) -> None:
    text = (
        "第一章 初见\n"
        "萧熏儿说道：\"我会陪着你。\"\n"
        "萧熏儿微笑着柔声道，略微稚嫩的嗓音，却是暖人心肺。\n"
        "“萧炎哥哥。”\n"
        "望着面前这张已经成长为家族中最璀璨的明珠，萧炎苦涩的道，她是在自己落魄后，极为少数还保持尊敬的人。"
    )
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()

    response = run_action(tmp_path, project_id, "generate_director")

    assert response.status_code == 200
    director = response.json()["director_doc"]
    characters = extracted["character_voice_bible"]["characters"]
    identity_ids = {character["display_name"]: character["character_id"] for character in characters}
    addressed_dialogue = next(segment for segment in director["segments"] if "萧炎哥哥" in segment["text"])
    narrative = next(segment for segment in director["segments"] if "望着面前这张" in segment["text"])
    assert addressed_dialogue["text"] == "萧炎哥哥。"
    assert addressed_dialogue["segment_type"] == "dialogue"
    assert addressed_dialogue["character_id"] == identity_ids["萧熏儿"]
    assert narrative["segment_type"] == "narration"
    assert narrative["character_id"] == "narrator"


def test_director_cache_can_be_deleted_without_removing_character_artifacts(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    run_action(tmp_path, project_id, "extract_characters")
    run_action(tmp_path, project_id, "generate_director")

    response = asyncio.run(request(tmp_path, "DELETE", f"/api/projects/{project_id}/director"))

    assert response.status_code == 200
    assert response.json()["status"] == "characters_ready"
    assert response.json()["director_doc"] is None
    assert response.json()["character_voice_bible"] is not None


def test_director_sentence_can_override_its_voice_reference(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    director = run_action(tmp_path, project_id, "generate_director").json()["director_doc"]
    segment = director["segments"][0]
    female_narrator = next(
        item for item in extracted["reference_plan"]["items"]
        if item["display_name"] == "女旁白"
    )

    updated = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/director/{segment['segment_id']}/voice",
            json={"voice_reference_id": female_narrator["reference_id"]},
        )
    )

    assert updated.status_code == 200
    updated_segment = next(
        item for item in updated.json()["director_doc"]["segments"]
        if item["segment_id"] == segment["segment_id"]
    )
    assert updated_segment["voice_reference_id"] == female_narrator["reference_id"]


def test_upload_rejects_non_txt_and_path_like_names(tmp_path: Path) -> None:
    wrong_type = asyncio.run(
        request(tmp_path, "POST", "/api/sources", files={"file": ("novel.md", b"text", "text/plain")})
    )
    unsafe_name = asyncio.run(
        request(tmp_path, "POST", "/api/sources", files={"file": ("../novel.txt", b"text", "text/plain")})
    )

    assert wrong_type.status_code == 415
    assert unsafe_name.status_code == 400


def test_reference_audio_upload_updates_the_project_reference(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    reference = next(item for item in extracted["reference_plan"]["items"] if item["display_name"] == "萧炎")

    uploaded = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{project_id}/references/{reference['reference_id']}/audio",
            data={"source": "recorded"},
            files={"file": ("recording.wav", reference_wav_bytes(), "audio/wav")},
        )
    )

    assert uploaded.status_code == 200
    updated = next(
        item for item in uploaded.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert updated["status"] == "generated"
    assert updated["audio_source"] == "recorded"
    assert updated["audio_url"].startswith("/media/outputs/audio/references/")
    assert len(updated["audio_versions"]) == 1
    assert updated["active_audio_version_id"] == updated["audio_versions"][0]["version_id"]
    first_version = updated["audio_versions"][0]
    assert first_version["decision"] == "accepted"
    audio = asyncio.run(request(tmp_path, "GET", updated["audio_url"]))
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"

    second_upload = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{project_id}/references/{reference['reference_id']}/audio",
            data={"source": "uploaded"},
            files={"file": ("second.wav", reference_wav_bytes(), "audio/wav")},
        )
    )
    second_item = next(
        item for item in second_upload.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert len(second_item["audio_versions"]) == 2
    assert second_item["audio_url"] != first_version["audio_url"]
    second_version = second_item["audio_versions"][1]
    assert second_version["decision"] == "accepted"
    assert second_item["audio_versions"][0]["decision"] == "superseded"

    activated = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/references/{reference['reference_id']}/audio/{first_version['version_id']}",
        )
    )
    active_item = next(
        item for item in activated.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert active_item["audio_url"] == first_version["audio_url"]
    assert active_item["audio_source"] == "recorded"

    deleted_second = asyncio.run(
        request(
            tmp_path,
            "DELETE",
            f"/api/projects/{project_id}/references/{reference['reference_id']}/audio/{second_version['version_id']}",
        )
    )
    after_second_delete = next(
        item for item in deleted_second.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert len(after_second_delete["audio_versions"]) == 1
    assert asyncio.run(request(tmp_path, "GET", second_version["audio_url"])).status_code == 404

    deleted_last = asyncio.run(
        request(
            tmp_path,
            "DELETE",
            f"/api/projects/{project_id}/references/{reference['reference_id']}/audio/{first_version['version_id']}",
        )
    )
    empty_item = next(
        item for item in deleted_last.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert empty_item["audio_versions"] == []
    assert empty_item["audio_url"] is None
    assert empty_item["status"] == "not_generated"

    invalid = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{project_id}/references/{reference['reference_id']}/audio",
            data={"source": "uploaded"},
            files={"file": ("broken.wav", b"not audio", "audio/wav")},
        )
    )
    assert invalid.status_code == 415


def test_similar_voice_resource_can_be_copied_between_named_projects_and_cleared(tmp_path: Path) -> None:
    text = "第一章 初见\n萧炎说道：\"开始吧。\"".encode()
    source_project = import_source(tmp_path, text, filename="来源.txt", project_name="历史声线库")
    target_project = import_source(tmp_path, text, filename="新作.txt", project_name="新小说")
    source_project_id = str(source_project["project_id"])
    target_project_id = str(target_project["project_id"])

    run_action(tmp_path, source_project_id, "analyze")
    source_extracted = run_action(tmp_path, source_project_id, "extract_characters").json()
    run_action(tmp_path, target_project_id, "analyze")
    target_extracted = run_action(tmp_path, target_project_id, "extract_characters").json()
    source_reference = next(
        item for item in source_extracted["reference_plan"]["items"]
        if item["display_name"] == "萧炎"
    )
    target_reference = next(
        item for item in target_extracted["reference_plan"]["items"]
        if item["display_name"] == "萧炎"
    )

    uploaded = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{source_project_id}/references/{source_reference['reference_id']}/audio",
            data={"source": "uploaded"},
            files={"file": ("voice.wav", reference_wav_bytes(), "audio/wav")},
        )
    )
    assert uploaded.status_code == 200
    uploaded_reference = next(
        item for item in uploaded.json()["reference_plan"]["items"]
        if item["reference_id"] == source_reference["reference_id"]
    )
    source_version = uploaded_reference["audio_versions"][0]
    assert source_version["audio_url"].startswith(f"/media/outputs/projects/{source_project_id}/assets/")

    matches = asyncio.run(
        request(
            tmp_path,
            "GET",
            f"/api/projects/{target_project_id}/references/{target_reference['reference_id']}/matches",
        )
    )
    assert matches.status_code == 200
    match = matches.json()[0]
    assert match["source_project_id"] == source_project_id
    assert match["source_project_name"] == "历史声线库"
    assert match["similarity"] > 0.5

    reused = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{target_project_id}/references/{target_reference['reference_id']}/reuse",
            json={
                "source_project_id": match["source_project_id"],
                "source_reference_id": match["source_reference_id"],
                "source_version_id": match["source_version_id"],
            },
        )
    )
    assert reused.status_code == 200
    reused_reference = next(
        item for item in reused.json()["reference_plan"]["items"]
        if item["reference_id"] == target_reference["reference_id"]
    )
    copied_version = reused_reference["audio_versions"][0]
    assert copied_version["source"] == "reused"
    assert copied_version["audio_url"].startswith(f"/media/outputs/projects/{target_project_id}/assets/")

    deleted_source = asyncio.run(
        request(
            tmp_path,
            "DELETE",
            f"/api/projects/{source_project_id}/references/{source_reference['reference_id']}/audio/{source_version['version_id']}",
        )
    )
    assert deleted_source.status_code == 200
    assert asyncio.run(request(tmp_path, "GET", copied_version["audio_url"])).status_code == 200

    cleared = asyncio.run(
        request(
            tmp_path,
            "DELETE",
            f"/api/projects/{target_project_id}/references/{target_reference['reference_id']}/audio",
        )
    )
    assert cleared.status_code == 200
    cleared_reference = next(
        item for item in cleared.json()["reference_plan"]["items"]
        if item["reference_id"] == target_reference["reference_id"]
    )
    assert cleared_reference["audio_versions"] == []
    assert asyncio.run(request(tmp_path, "GET", copied_version["audio_url"])).status_code == 404


def test_reference_text_supports_local_generation_edits_history_and_deletion(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    reference = next(item for item in extracted["reference_plan"]["items"] if item["display_name"] == "萧炎")
    assert len(reference["reference_text_versions"]) == 1
    initial_version = reference["reference_text_versions"][0]
    analyzer = RegeneratingVoiceAnalyzer()

    generated = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{project_id}/references/{reference['reference_id']}/text/generate",
            voice_analyzer=analyzer,
        )
    )
    assert generated.status_code == 200
    generated_item = next(
        item for item in generated.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert len(generated_item["reference_text_versions"]) == 2
    assert generated_item["reference_text_versions"][-1]["source"] == "generated"
    generated_version = generated_item["reference_text_versions"][-1]
    assert generated_item["reference_text"] == generated_version["text"]

    edited_text = "窗外的雨声渐渐停下，我调整呼吸，把接下来的安排清楚而自然地说完。"
    edited = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/references/{reference['reference_id']}",
            json={"reference_text": edited_text},
        )
    )
    edited_item = next(
        item for item in edited.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert len(edited_item["reference_text_versions"]) == 3
    assert edited_item["reference_text_versions"][-1]["source"] == "edited"
    assert edited_item["reference_text"] == edited_text

    activated = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/references/{reference['reference_id']}/text/{generated_version['version_id']}",
        )
    )
    active_item = next(
        item for item in activated.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert active_item["reference_text"] == generated_version["text"]

    deleted = asyncio.run(
        request(
            tmp_path,
            "DELETE",
            f"/api/projects/{project_id}/references/{reference['reference_id']}/text/{initial_version['version_id']}",
        )
    )
    deleted_item = next(
        item for item in deleted.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert len(deleted_item["reference_text_versions"]) == 2


def test_emotion_plan_supports_skip_selection_controls_and_custom_variants(tmp_path: Path) -> None:
    imported = import_source(
        tmp_path,
        "第一章 初见\n萧炎说道：\"开始吧。\"\n药老问道：\"准备好了吗？\"".encode(),
    )
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()

    emotion_plan = extracted["emotion_plan"]
    assert emotion_plan["skipped"] is False
    assert emotion_plan["automatic_items_locked"] is True
    parent = next(item for item in extracted["reference_plan"]["items"] if item["display_name"] == "萧炎")
    variants = [item for item in emotion_plan["items"] if item["parent_reference_id"] == parent["reference_id"]]
    assert [item["emotion_name"] for item in variants] == ["自然", "愤怒", "悲伤", "紧张", "激动"]
    assert all(item["selected"] for item in variants)

    skipped = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/emotion-settings",
            json={"skipped": True, "automatic_items_locked": False, "automatic_threshold": 0.8},
        )
    )
    assert skipped.status_code == 200
    assert skipped.json()["emotion_plan"]["skipped"] is True

    created = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{project_id}/emotions",
            json={
                "parent_reference_id": parent["reference_id"],
                "emotion_name": "克制期待",
                "description": "压低音量，语气克制，但保留即将行动的期待感",
                "intensity": 0.67,
            },
        )
    )
    assert created.status_code == 201
    custom = next(item for item in created.json()["emotion_plan"]["items"] if item["emotion_name"] == "克制期待")
    assert custom["selection_mode"] == "custom"
    assert custom["selected"] is True
    assert custom["locked"] is False


def test_single_character_voice_profile_can_be_regenerated_without_resetting_reference_state(tmp_path: Path) -> None:
    text = (
        "第一章 初见\n"
        "萧炎说道：\"开始吧。\"\n"
        "萧炎问道：\"准备好了吗？\"\n"
        "药老说道：\"先稳住气息。\""
    )
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    references = {item["display_name"]: item for item in extracted["reference_plan"]["items"]}
    target = references["萧炎"]
    untouched_prompt = references["药老"]["voice_prompt"]
    analyzer = RegeneratingVoiceAnalyzer()

    response = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{project_id}/voice-profiles/regenerate",
            voice_analyzer=analyzer,
            json={
                "character_id": target["source_character_id"],
                "custom_attributes": "年轻但不轻浮，句尾带一点压住锋芒的笑意",
            },
        )
    )

    assert response.status_code == 200
    payload = response.json()
    updated = next(
        item for item in payload["reference_plan"]["items"]
        if item["reference_id"] == target["reference_id"]
    )
    assert analyzer.calls == ["萧炎"]
    assert analyzer.evidence_packs[0].user_attributes == "年轻但不轻浮，句尾带一点压住锋芒的笑意"
    assert updated["voice_prompt"] == "重新生成的萧炎声线描述"
    assert updated["custom_voice_attributes"] == "年轻但不轻浮，句尾带一点压住锋芒的笑意"
    assert updated["selected"] == target["selected"]
    assert updated["locked"] == target["locked"]
    assert updated["status"] == target["status"]
    assert next(item for item in payload["reference_plan"]["items"] if item["display_name"] == "药老")["voice_prompt"] == untouched_prompt
    character = next(
        item for item in payload["character_voice_bible"]["characters"]
        if item["character_id"] == target["source_character_id"]
    )
    assert character["voice_prompt"] == updated["voice_prompt"]
    assert character["voice_profile_confidence"] == 0.91
    variants = [
        item for item in payload["emotion_plan"]["items"]
        if item["parent_reference_id"] == target["reference_id"]
    ]
    assert variants
    assert all(item["voice_prompt"].startswith(updated["voice_prompt"]) for item in variants)


def test_default_narrator_description_requires_unlock_before_regeneration(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    narrator = next(item for item in extracted["reference_plan"]["items"] if item["display_name"] == "男旁白")
    analyzer = RegeneratingVoiceAnalyzer()

    locked = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{project_id}/voice-profiles/regenerate",
            voice_analyzer=analyzer,
            json={"reference_id": narrator["reference_id"]},
        )
    )
    assert locked.status_code == 409
    assert analyzer.calls == []

    unlocked = asyncio.run(
        request(
            tmp_path,
            "PATCH",
            f"/api/projects/{project_id}/references/{narrator['reference_id']}",
            json={"voice_prompt_locked": False},
        )
    )
    unlocked_narrator = next(
        item for item in unlocked.json()["reference_plan"]["items"]
        if item["reference_id"] == narrator["reference_id"]
    )
    assert unlocked_narrator["locked"] is True
    assert unlocked_narrator["voice_prompt_locked"] is False

    regenerated = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{project_id}/voice-profiles/regenerate",
            voice_analyzer=analyzer,
            json={"reference_id": narrator["reference_id"]},
        )
    )
    assert regenerated.status_code == 200
    updated = next(
        item for item in regenerated.json()["reference_plan"]["items"]
        if item["reference_id"] == narrator["reference_id"]
    )
    assert analyzer.calls == ["男旁白"]
    assert updated["voice_prompt"] == "重新生成的男旁白声线描述"


def test_all_character_voice_profiles_can_be_regenerated_together(tmp_path: Path) -> None:
    text = (
        "第一章 初见\n"
        "萧炎说道：\"开始吧。\"\n"
        "萧炎问道：\"准备好了吗？\"\n"
        "药老说道：\"先稳住气息。\""
    )
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    expected_names = {
        item["display_name"]
        for item in extracted["character_voice_bible"]["characters"]
        if item["character_id"] != "narrator"
    }
    analyzer = RegeneratingVoiceAnalyzer()

    response = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{project_id}/voice-profiles/regenerate",
            voice_analyzer=analyzer,
            json={},
        )
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(analyzer.calls) == expected_names
    regenerated = [
        item for item in payload["character_voice_bible"]["characters"]
        if item["character_id"] != "narrator"
    ]
    assert all(item["voice_prompt"] == f"重新生成的{item['display_name']}声线描述" for item in regenerated)


def test_analysis_revisions_restore_stage_cache_without_deleting_project_assets(tmp_path: Path) -> None:
    text = "第一章 初见\n萧炎说道：\"开始吧。\"\n药老问道：\"准备好了吗？\""
    imported = import_source(
        tmp_path,
        text.encode(),
        filename="长篇项目.txt",
        project_name="长篇配音项目",
    )
    project_id = str(imported["project_id"])
    source_path = tmp_path / "outputs" / "projects" / project_id / "source" / "长篇项目.txt"

    first_analysis = run_action(tmp_path, project_id, "analyze")
    assert first_analysis.status_code == 200
    first_workspace = asyncio.run(request(tmp_path, "GET", f"/api/projects/{project_id}/revisions")).json()
    first_revision_id = first_workspace["active_revision_id"]
    assert first_revision_id
    assert len(first_workspace["revisions"]) == 1

    extracted = run_action(tmp_path, project_id, "extract_characters").json()
    reference = next(item for item in extracted["reference_plan"]["items"] if item["display_name"] == "萧炎")
    uploaded = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{project_id}/references/{reference['reference_id']}/audio",
            data={"source": "uploaded"},
            files={"file": ("voice.wav", reference_wav_bytes(), "audio/wav")},
        )
    )
    uploaded_reference = next(
        item for item in uploaded.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    audio_url = uploaded_reference["audio_url"]
    assert audio_url.startswith(f"/media/outputs/projects/{project_id}/assets/references/")
    audio_path = tmp_path / audio_url.removeprefix("/media/")
    assert audio_path.is_file()

    second_analysis = run_action(tmp_path, project_id, "analyze")
    assert second_analysis.status_code == 200
    assert second_analysis.json()["character_voice_bible"] is None
    second_workspace = asyncio.run(request(tmp_path, "GET", f"/api/projects/{project_id}/revisions")).json()
    second_revision_id = second_workspace["active_revision_id"]
    assert second_revision_id != first_revision_id
    assert len(second_workspace["revisions"]) == 2

    restored = asyncio.run(
        request(
            tmp_path,
            "POST",
            f"/api/projects/{project_id}/revisions/{first_revision_id}/activate",
        )
    )
    assert restored.status_code == 200
    restored_reference = next(
        item for item in restored.json()["reference_plan"]["items"]
        if item["reference_id"] == reference["reference_id"]
    )
    assert restored_reference["audio_url"] == audio_url

    deleted = asyncio.run(
        request(
            tmp_path,
            "DELETE",
            f"/api/projects/{project_id}/revisions/{first_revision_id}",
        )
    )
    assert deleted.status_code == 200
    assert deleted.json()["active_revision_id"] == second_revision_id
    assert [item["revision_id"] for item in deleted.json()["revisions"]] == [second_revision_id]
    assert source_path.is_file()
    assert audio_path.is_file()


def test_character_analysis_retries_transient_profile_failure_without_restart(tmp_path: Path) -> None:
    text = "第一章 初见\n萧炎说道：\"开始吧。\"\n药老问道：\"准备好了吗？\""
    imported = import_source(tmp_path, text.encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    analyzer = FailOnceCharacterVoiceAnalyzer()

    completed = run_action(tmp_path, project_id, "extract_characters", voice_analyzer=analyzer)

    assert completed.status_code == 200
    checkpoint_path = tmp_path / "outputs" / "projects" / project_id / "character_analysis_checkpoint.json"
    assert analyzer.attempts == ["萧炎", "药老", "药老"]
    assert analyzer.calls == ["萧炎", "药老"]
    assert not checkpoint_path.exists()


def test_character_profile_failure_retries_then_uses_archetype_and_continues(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    analyzer = AlwaysFailCharacterVoiceAnalyzer()

    response = run_action(tmp_path, project_id, "extract_characters", voice_analyzer=analyzer)

    assert response.status_code == 200
    assert analyzer.attempts == ["萧炎", "萧炎", "萧炎"]
    payload = response.json()
    character = next(
        item for item in payload["character_voice_bible"]["characters"]
        if item["display_name"] == "萧炎"
    )
    reference = next(
        item for item in payload["reference_plan"]["items"]
        if item["source_character_id"] == character["character_id"]
    )
    assert character["archetype_id"].startswith("archetype-")
    assert reference["selected"] is False
    assert any("萧炎" in warning and "3 次" in warning for warning in payload["analysis_audit"]["warnings"])


def test_character_analysis_can_be_cancelled_and_resumed_from_checkpoint(tmp_path: Path) -> None:
    bootstrap = PreparationService(tmp_path)
    imported = bootstrap.import_source(
        "cancel-character.txt",
        "第一章 初见\n萧炎说道：\"开始吧。\"\n药老问道：\"准备好了吗？\"".encode(),
    )
    bootstrap.run(imported.project_id, "analyze")
    analyzer = BlockingCharacterVoiceAnalyzer()
    application = create_app(tmp_path, voice_analyzer=analyzer)

    async def scenario() -> tuple[httpx.Response, httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            run_task = asyncio.create_task(
                client.post(
                    f"/api/projects/{imported.project_id}/preparation",
                    json={"action": "extract_characters"},
                )
            )
            started = await asyncio.to_thread(analyzer.started.wait, 2)
            assert started
            try:
                cancelled = await client.post(
                    f"/api/projects/{imported.project_id}/preparation/cancel",
                )
            finally:
                analyzer.release.set()
            stopped = await asyncio.wait_for(run_task, timeout=5)
            activity = await client.get(f"/api/projects/{imported.project_id}/analysis-activity")
            checkpoint_path = (
                tmp_path
                / "outputs"
                / "projects"
                / imported.project_id
                / "character_analysis_checkpoint.json"
            )
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            assert len(checkpoint["profiles"]) == 1
            revisions = await client.get(f"/api/projects/{imported.project_id}/revisions")
            resumed = await client.post(
                f"/api/projects/{imported.project_id}/preparation",
                json={
                    "action": "extract_characters",
                    "revision_id": revisions.json()["active_revision_id"],
                    "resume": True,
                },
            )
            return cancelled, stopped, activity, resumed

    cancelled, stopped, activity, resumed = asyncio.run(scenario())

    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "running"
    assert "保留" in cancelled.json()["message"]
    assert stopped.status_code == 409
    assert "已终止" in stopped.json()["detail"]
    assert activity.status_code == 200
    assert activity.json()["state"] == "cancelled"
    checkpoint_path = (
        tmp_path
        / "outputs"
        / "projects"
        / imported.project_id
        / "character_analysis_checkpoint.json"
    )
    assert len(analyzer.attempts) == 2
    assert resumed.status_code == 200
    assert not checkpoint_path.exists()


def test_director_analysis_resume_reuses_completed_batches(tmp_path: Path) -> None:
    lines = [f"\"第 {index} 句待判定对白。\"" for index in range(1, 26)]
    imported = import_source(
        tmp_path,
        ("第一章 初见\n萧炎说道：\"角色锚点。\"\n" + "\n".join(lines)).encode(),
    )
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    run_action(tmp_path, project_id, "extract_characters")
    workspace = asyncio.run(request(tmp_path, "GET", f"/api/projects/{project_id}/revisions")).json()
    revision_id = str(workspace["active_revision_id"])
    analyzer = FailOnceDirectorVoiceAnalyzer()

    failed = run_action(tmp_path, project_id, "generate_director", voice_analyzer=analyzer)
    assert failed.status_code == 500
    checkpoint_path = tmp_path / "outputs" / "projects" / project_id / "director_analysis_checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert len(checkpoint["completed_batches"]) == 1

    resumed = run_action(
        tmp_path,
        project_id,
        "generate_director",
        revision_id=revision_id,
        resume=True,
        voice_analyzer=analyzer,
    )
    assert resumed.status_code == 200
    assert analyzer.director_attempts == [24, 1, 1]
    dialogue_segments = [
        item for item in resumed.json()["director_doc"]["segments"]
        if item["segment_type"] == "dialogue"
    ]
    assert len(dialogue_segments) == 26
    assert not checkpoint_path.exists()


def test_legacy_narrator_prompts_migrate_to_long_form_templates(tmp_path: Path) -> None:
    imported = import_source(tmp_path, "第一章 初见\n萧炎说道：\"开始吧。\"".encode())
    project_id = str(imported["project_id"])
    run_action(tmp_path, project_id, "analyze")
    run_action(tmp_path, project_id, "extract_characters")
    plan_path = tmp_path / "outputs" / "projects" / project_id / "reference_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["schema_version"] = 5
    for item in plan["items"]:
        if item["selection_mode"] == "narrator_default":
            item["voice_prompt"] = "成熟、清晰、稳定的旁白声线"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    migrated = asyncio.run(request(tmp_path, "GET", f"/api/projects/{project_id}/preparation/preview"))

    assert migrated.status_code == 200
    migrated_plan = migrated.json()["reference_plan"]
    assert migrated_plan["schema_version"] == 7
    prompts = {
        item["display_name"]: item["voice_prompt"]
        for item in migrated_plan["items"]
        if item["selection_mode"] == "narrator_default"
    }
    assert "成年男性长篇旁白基线" in prompts["男旁白"]
    assert "成年女性长篇旁白基线" in prompts["女旁白"]
    assert all("长篇一致性" in prompt and "中性参考约束" in prompt for prompt in prompts.values())
