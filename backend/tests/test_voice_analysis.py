from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.voice_analysis import (
    CharacterCandidateScreeningInput,
    CharacterCandidateScreeningDecision,
    CharacterCandidateScreeningDraft,
    CharacterEvidencePack,
    ConfigurableVoiceAnalyzer,
    DirectorCharacter,
    DirectorPassageEvidence,
    HybridVoiceAnalyzer,
    OllamaVoiceAnalyzer,
    OpenAICompatibleVoiceAnalyzer,
    VoiceAnalysisCloudProfileUpdate,
    VoiceAnalysisError,
    VoiceAnalysisModelCatalogRequest,
    VoiceAnalysisTransportError,
    VoiceAnalysisConfigurationUpdate,
)


def test_analysis_contract_bounds_aliases_before_length_validation() -> None:
    aliases = [f"别名{index:02d}" for index in range(14)] + ["别名00", "  别名01  "]

    evidence_pack = CharacterEvidencePack(
        character_id="character-many-aliases",
        display_name="药老",
        aliases=aliases,
        mention_count=100,
        dialogue_count=20,
        evidence=["药老平静地说道。"],
    )
    director_character = DirectorCharacter(
        display_name="药老",
        aliases=aliases,
        gender="male",
    )

    assert evidence_pack.aliases == [f"别名{index:02d}" for index in range(12)]
    assert director_character.aliases == evidence_pack.aliases


class StubHybridAnalyzer:
    def __init__(self, backend: str, model: str) -> None:
        self.backend = backend
        self.model = model
        self.character_inputs: list[CharacterEvidencePack] = []
        self.screening_inputs: list[list[CharacterCandidateScreeningInput]] = []
        self.director_inputs: list[list[DirectorPassageEvidence]] = []

    def status(self):
        from app.voice_analysis import VoiceAnalysisStatus

        return VoiceAnalysisStatus(
            backend=self.backend,
            available=True,
            model=self.model,
            detail=f"{self.backend} ready",
            taxonomy_version=1,
        )

    def analyze(self, evidence_pack: CharacterEvidencePack):
        from app.voice_analysis import CharacterVoiceProfile

        self.character_inputs.append(evidence_pack)
        return CharacterVoiceProfile(
            gender="male",
            age_range="young_adult",
            personality_tags=["克制"],
            timbre_tags=["中低音区"],
            delivery_tags=["咬字利落"],
            voice_constraints=["保持自然口语"],
            voice_prompt=f"{self.backend} voice prompt",
            confidence=0.8,
            rationale=f"{self.backend} rationale",
            backend=self.backend,
            model=self.model,
        )

    def screen_character_candidates(self, project_id, candidates, canonical_anchors):
        del project_id, canonical_anchors
        self.screening_inputs.append(candidates)
        return CharacterCandidateScreeningDraft(
            decisions=[
                CharacterCandidateScreeningDecision(
                    candidate_id=candidate.candidate_id,
                    action="reject" if candidate.display_name == "带着薰儿" else "keep",
                    canonical_candidate_id=None,
                    confidence=0.94,
                    rationale="local screening",
                )
                for candidate in candidates
            ],
            backend=self.backend,
            model=self.model,
        )

    def generate_reference_text(self, evidence_pack, voice_prompt):
        from app.voice_analysis import ReferenceTextDraft

        del evidence_pack
        return ReferenceTextDraft(
            text="清晨的风穿过长街，远处的钟声渐渐清晰，我们仍按原定方向从容前行。",
            rationale=voice_prompt[:120] or "neutral",
            backend=self.backend,
            model=self.model,
        )

    def analyze_director(self, passages, characters):
        from app.voice_analysis import DirectorAnalysisDraft, DirectorPassageDecision

        del characters
        self.director_inputs.append(passages)
        return DirectorAnalysisDraft(
            decisions=[
                DirectorPassageDecision(
                    passage_id=passage.passage_id,
                    speaker="萧炎",
                    speaker_gender="male",
                    speaker_kind="named",
                    emotion="calm",
                    emotion_intensity=0.5,
                    tone="steady",
                    confidence=0.9,
                    rationale=f"{self.backend} decision",
                )
                for passage in passages
            ],
            backend=self.backend,
            model=self.model,
        )

    def analyze_text_structure(self, project_id, candidates, total_characters):
        from app.long_form import TextStructureDraft

        del project_id, total_characters
        return TextStructureDraft(
            heading_ids=[candidates[0].candidate_id] if candidates else [],
            confidence=0.8,
            rationale=f"{self.backend} structure",
            backend=self.backend,
            model=self.model,
        )


def test_hybrid_analyzer_passes_local_screening_to_cloud_and_marks_final_backend() -> None:
    local = StubHybridAnalyzer("local", "local-model")
    cloud = StubHybridAnalyzer("cloud", "cloud-model")
    analyzer = HybridVoiceAnalyzer(local, cloud, RecordingLogger())
    evidence = CharacterEvidencePack(
        project_id="project-hybrid",
        character_id="character-xiao-yan",
        display_name="萧炎",
        mention_count=12,
        dialogue_count=4,
        evidence=["萧炎平静地说道：我已经决定了。"],
    )

    profile = analyzer.analyze(evidence)

    assert len(local.character_inputs) == 1
    assert len(cloud.character_inputs) == 1
    assert cloud.character_inputs[0].local_screening is not None
    assert "本地初筛结果" in cloud.character_inputs[0].local_screening
    assert profile.backend == "hybrid"
    assert profile.model == "local-model -> cloud-model"


def test_hybrid_analyzer_reuses_candidate_screening_without_second_local_inference() -> None:
    local = StubHybridAnalyzer("local", "local-model")
    cloud = StubHybridAnalyzer("cloud", "cloud-model")
    analyzer = HybridVoiceAnalyzer(local, cloud, RecordingLogger())
    evidence = CharacterEvidencePack(
        project_id="project-hybrid",
        character_id="character-xiao-yan",
        display_name="萧炎",
        mention_count=120,
        dialogue_count=18,
        evidence=["萧炎说道：\"开始吧。\""],
        local_screening="本地候选粗筛已确认具名角色，置信度 0.98。",
    )

    profile = analyzer.analyze(evidence)

    assert local.character_inputs == []
    assert len(cloud.character_inputs) == 1
    assert cloud.character_inputs[0].local_screening == evidence.local_screening
    assert profile.backend == "hybrid"
    assert profile.model == "local-model -> cloud-model"


def test_hybrid_candidate_screening_uses_local_model_without_cloud_call() -> None:
    local = StubHybridAnalyzer("local", "local-model")
    cloud = StubHybridAnalyzer("cloud", "cloud-model")
    analyzer = HybridVoiceAnalyzer(local, cloud, RecordingLogger())
    candidates = [
        CharacterCandidateScreeningInput(
            candidate_id="candidate-xiao-yan",
            display_name="萧炎",
            mention_count=120,
            dialogue_count=18,
            batch_presence_count=4,
            evidence=["萧炎说道：\"开始吧。\""],
        ),
        CharacterCandidateScreeningInput(
            candidate_id="candidate-false",
            display_name="带着薰儿",
            mention_count=2,
            dialogue_count=1,
            batch_presence_count=1,
            evidence=["带着薰儿，萧炎说道：\"走吧。\""],
        ),
    ]

    draft = analyzer.screen_character_candidates("project-hybrid", candidates, candidates[:1])

    assert len(local.screening_inputs) == 1
    assert cloud.screening_inputs == []
    assert [decision.action for decision in draft.decisions] == ["keep", "reject"]
    assert draft.backend == "local"
    assert draft.model == "local-model"


def test_hybrid_director_keeps_local_decision_as_review_context() -> None:
    local = StubHybridAnalyzer("local", "local-model")
    cloud = StubHybridAnalyzer("cloud", "cloud-model")
    analyzer = HybridVoiceAnalyzer(local, cloud, RecordingLogger())
    passage = DirectorPassageEvidence(
        project_id="project-hybrid",
        passage_id="passage-1",
        text="我已经决定了。",
        context="萧炎抬起头，平静地看向众人。",
    )

    draft = analyzer.analyze_director(
        [passage],
        [DirectorCharacter(display_name="萧炎", gender="male")],
    )

    assert "本地初筛，仅供复核" in cloud.director_inputs[0][0].context
    assert draft.backend == "hybrid"
    assert draft.model == "local-model -> cloud-model"


def analyzer_response(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "gender": "male",
        "age_range": "young_adult",
        "personality_tags": ["restrained", "decisive"],
        "pitch": "mid_low",
        "weight": "medium",
        "brightness": "neutral",
        "texture": ["clean", "warm"],
        "resonance": "mixed",
        "articulation": "crisp",
        "breath": "balanced",
        "pace": "steady",
        "rhythm": "punctuated",
        "dynamics": "restrained",
        "baseline": "composed",
        "constraints": ["avoid_announcer_tone", "preserve_identity_neutral"],
        "signature_core": "中低声区始终保持收束，不靠响度压人；压力下句尾常压住情绪，承诺时才突然收紧咬字",
        "signature_habits": ["先短暂停顿再给结论", "压力下仍使用完整短句", "承诺句末略微加重"],
        "confidence": 0.82,
        "rationale": "多段对话反复表现出克制、果断和清楚利落的表达。",
    }
    payload.update(overrides)
    return payload


class RecordingLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str, *args: object) -> None:
        self.messages.append(message % args)


def test_local_analyzer_compiles_controlled_attributes_into_a_neutral_prompt() -> None:
    preloads = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal preloads
        if request.url.path == "/api/generate":
            preloads += 1
            body = json.loads(request.content)
            assert body["prompt"] == ""
            assert body["keep_alive"] == "2m"
            return httpx.Response(200, json={"done": True})
        if request.url.path == "/api/chat":
            body = json.loads(request.content)
            assert body["think"] is False
            assert body["keep_alive"] == "0s"
            assert body["format"]["additionalProperties"] is False
            assert "signature_core" in body["format"]["properties"]
            assert "去名检验" in body["messages"][0]["content"]
            assert "半数常见角色" in body["messages"][0]["content"]
            assert "年轻但不轻浮" in body["messages"][1]["content"]
            assert "用户自定义属性是明确的表演方向" in body["messages"][1]["content"]
            return httpx.Response(
                200,
                json={"message": {"content": json.dumps(analyzer_response(), ensure_ascii=False)}},
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(respond))
    analyzer = OllamaVoiceAnalyzer(Path.cwd().parent, client=client)

    profile = analyzer.analyze(
        CharacterEvidencePack(
            character_id="character-xiao-yan",
            display_name="萧炎",
            mention_count=12,
            dialogue_count=3,
            gender_hint="male",
            evidence=["萧炎平静地说道：\"我已经决定了。\""],
            user_attributes="年轻但不轻浮，句尾带一点压住锋芒的笑意",
        )
    )

    assert profile.backend == "local"
    assert profile.model == "zw-voice-analyzer:4b"
    assert profile.age_range == "young_adult"
    assert profile.personality_tags == ["克制", "果断"]
    assert profile.timbre_tags == ["中低音区", "适中", "均衡", "干净", "温润", "混合共鸣"]
    assert profile.delivery_tags == ["咬字利落", "气息平稳", "语速平稳", "停连分明", "动态克制", "从容克制"]
    assert "角色辨识核心：中低声区始终保持收束" in profile.voice_prompt
    assert "稳定表达习惯：先短暂停顿再给结论；压力下仍使用完整短句；承诺句末略微加重" in profile.voice_prompt
    assert "基础声学画像：青年男性声线" in profile.voice_prompt
    assert "场景情绪" in profile.voice_prompt
    assert "愤怒" not in profile.voice_prompt
    assert preloads == 1


def test_local_analyzer_rejects_values_outside_the_taxonomy_after_retry() -> None:
    requests = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        requests += 1
        invalid = analyzer_response(texture=["magnetic"])
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(invalid, ensure_ascii=False)}},
        )

    analyzer = OllamaVoiceAnalyzer(
        Path.cwd().parent,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    with pytest.raises(VoiceAnalysisError, match="本地模型未能生成有效音色画像"):
        analyzer.analyze(
            CharacterEvidencePack(
                character_id="character-test",
                display_name="测试角色",
                mention_count=1,
                dialogue_count=1,
                evidence=["测试角色说道：\"你好。\""],
            )
        )

    assert requests == 2


def test_local_analyzer_retries_transient_server_errors_during_cold_load() -> None:
    requests = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        requests += 1
        if requests < 3:
            return httpx.Response(500, json={"error": "model runner is still loading"})
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(analyzer_response(), ensure_ascii=False)}},
        )

    analyzer = OllamaVoiceAnalyzer(
        Path.cwd().parent,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_delay_seconds=0,
    )

    profile = analyzer.analyze(
        CharacterEvidencePack(
            character_id="character-cold-start",
            display_name="Cold start character",
            mention_count=3,
            dialogue_count=2,
            evidence=["The character asks everyone to wait calmly."],
        )
    )

    assert profile.backend == "local"
    assert requests == 3


def test_local_analyzer_retries_transient_model_preload_errors() -> None:
    preloads = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal preloads
        if request.url.path == "/api/generate":
            preloads += 1
            if preloads < 3:
                return httpx.Response(502, json={"error": "model runner is still starting"})
            return httpx.Response(200, json={"done": True})
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(analyzer_response(), ensure_ascii=False)}},
        )

    analyzer = OllamaVoiceAnalyzer(
        Path.cwd().parent,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_delay_seconds=0,
    )

    profile = analyzer.analyze(
        CharacterEvidencePack(
            character_id="character-preload-retry",
            display_name="Preload retry character",
            mention_count=3,
            dialogue_count=2,
            evidence=["The character gives a concise instruction."],
        )
    )

    assert profile.backend == "local"
    assert preloads == 3


def test_local_analyzer_status_checks_the_dedicated_model_repository() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "zw-voice-analyzer:4b"}]})

    analyzer = OllamaVoiceAnalyzer(
        Path.cwd().parent,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    status = analyzer.status()

    assert status.available is True
    assert status.backend == "local"
    assert status.model == "zw-voice-analyzer:4b"
    assert status.model_store is not None and status.model_store.endswith("local_models\\ollama")


def test_local_analyzer_loads_the_bundled_runtime_skill_as_fallback(tmp_path: Path) -> None:
    analyzer = OllamaVoiceAnalyzer(tmp_path, client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))))
    system_prompt = analyzer._system_prompt()

    assert "signature_habits" in system_prompt
    assert "去名检验" in system_prompt


def test_local_analyzer_resolves_director_speakers_with_the_dedicated_skill() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        body = json.loads(request.content)
        assert "被称呼者不是说话人" in body["messages"][0]["content"]
        assert set(body["format"]["properties"]) == {"d"}
        decisions = body["format"]["properties"]["d"]
        assert decisions["minItems"] == 1
        assert decisions["maxItems"] == 1
        assert set(decisions["items"]["properties"]) == {"s", "e", "t"}
        assert decisions["items"]["properties"]["s"]["enum"] == [
            "萧炎",
            "萧熏儿",
            "男路人",
            "女路人",
            "未知角色",
        ]
        prompt = body["messages"][1]["content"]
        assert '"i":1' in prompt
        assert 'C=["萧熏儿微笑着柔声道，略微稚嫩的嗓音，却是暖人心肺。\\n“萧炎哥哥。”"]' in prompt
        assert '"passage_id"' not in prompt
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "d": [
                                {
                                    "s": "萧熏儿",
                                    "e": "tender",
                                    "t": "soft",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                }
            },
        )

    analyzer = OllamaVoiceAnalyzer(
        Path.cwd().parent,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    result = analyzer.analyze_director(
        [
            DirectorPassageEvidence(
                passage_id="passage-2",
                text="萧炎哥哥。",
                context="萧熏儿微笑着柔声道，略微稚嫩的嗓音，却是暖人心肺。\n“萧炎哥哥。”",
            )
        ],
        [
            DirectorCharacter(display_name="萧炎", aliases=[], gender="male"),
            DirectorCharacter(display_name="萧熏儿", aliases=["熏儿"], gender="female"),
        ],
    )

    assert result.backend == "local"
    assert result.model == "zw-voice-analyzer:4b"
    assert result.decisions[0].speaker == "萧熏儿"
    assert result.decisions[0].speaker_gender == "female"
    assert result.decisions[0].speaker_kind == "named"
    assert result.decisions[0].emotion_intensity == 0.62
    assert result.decisions[0].tone == "soft"


def test_local_analyzer_screens_character_candidates_with_dedicated_skill() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        body = json.loads(request.content)
        assert "动作和介词短语" in body["messages"][0]["content"]
        schema = body["format"]
        decisions = schema["properties"]["d"]
        assert decisions["required"] == ["c01", "c02"]
        assert decisions["additionalProperties"] is False
        assert set(decisions["properties"]) == {"c01", "c02"}
        assert decisions["properties"]["c01"]["properties"]["a"]["enum"] == ["k", "r", "m"]
        assert "candidate_id" not in decisions["properties"]["c01"]["properties"]
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "d": {
                                "c01": {
                                    "a": "k",
                                    "t": None,
                                    "c": 0.98,
                                    "r": "named_identity",
                                },
                                "c02": {
                                    "a": "r",
                                    "t": None,
                                    "c": 0.97,
                                    "r": "action_phrase",
                                },
                            }
                        },
                        ensure_ascii=False,
                    )
                }
            },
        )

    analyzer = OllamaVoiceAnalyzer(
        Path.cwd().parent,
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    result = analyzer.screen_character_candidates(
        "project-screening",
        [
            CharacterCandidateScreeningInput(
                candidate_id="candidate-xiao-yan",
                display_name="萧炎",
                mention_count=120,
                dialogue_count=18,
                batch_presence_count=4,
                evidence=["萧炎说道：\"开始吧。\""],
            ),
            CharacterCandidateScreeningInput(
                candidate_id="candidate-false",
                display_name="带着薰儿",
                mention_count=2,
                dialogue_count=1,
                batch_presence_count=1,
                evidence=["带着薰儿，萧炎说道：\"走吧。\""],
            ),
        ],
        [],
    )

    assert result.backend == "local"
    assert result.model == "zw-voice-analyzer:4b"
    assert [decision.action for decision in result.decisions] == ["keep", "reject"]


def test_cloud_analyzer_sends_authorization_and_compiles_character_profile() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer secret-key"
        body = json.loads(request.content)
        assert body["model"] == "fiction-analyst"
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(analyzer_response(), ensure_ascii=False)}}
                ]
            },
        )

    analyzer = OpenAICompatibleVoiceAnalyzer(
        Path.cwd().parent,
        provider="custom",
        base_url="https://analysis.example/v1",
        model="fiction-analyst",
        api_key="secret-key",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_delay_seconds=0,
    )

    profile = analyzer.analyze(
        CharacterEvidencePack(
            character_id="character-xiao-yan",
            display_name="萧炎",
            mention_count=12,
            dialogue_count=3,
            gender_hint="male",
            evidence=["萧炎平静地说道：\"我已经决定了。\""],
        )
    )

    assert profile.backend == "cloud"
    assert profile.model == "fiction-analyst"
    assert profile.personality_tags == ["克制", "果断"]


def test_cloud_analyzer_logs_request_input_and_response_without_api_key() -> None:
    logger = RecordingLogger()

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "cloud input marker" in body["messages"][1]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(analyzer_response(), ensure_ascii=False)}}
                ]
            },
        )

    analyzer = OpenAICompatibleVoiceAnalyzer(
        Path.cwd().parent,
        provider="custom",
        base_url="https://analysis.example/v1",
        model="fiction-analyst",
        api_key="secret-key",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_delay_seconds=0,
        runtime_logger=logger,
    )

    analyzer.analyze(
        CharacterEvidencePack(
            character_id="character-log",
            display_name="Log Character",
            mention_count=2,
            dialogue_count=1,
            gender_hint="unknown",
            evidence=["cloud input marker"],
        )
    )

    output = "\n".join(logger.messages)
    assert "[CLOUD API " in output
    assert "INPUT" in output
    assert "OUTPUT" in output
    assert "operation=character_profile" in output
    assert "cloud input marker" in output
    assert '"signature_core"' in output
    assert "status=200" in output
    assert "elapsed=" in output
    assert "secret-key" not in output


def test_cloud_responses_analyzer_parses_streamed_structured_output() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        assert request.headers["Authorization"] == "Bearer secret-key"
        body = json.loads(request.content)
        assert body["model"] == "fiction-analyst"
        assert body["stream"] is True
        assert body["input"][0]["content"][0]["type"] == "input_text"
        assert body["text"]["format"]["type"] == "json_schema"
        content = json.dumps(analyzer_response(texture="clean"), ensure_ascii=False)
        events = [
            f'data: {json.dumps({"type": "response.output_text.delta", "delta": content}, ensure_ascii=False)}',
            'data: {"type":"response.completed","response":{"status":"completed"}}',
            "data: [DONE]",
        ]
        return httpx.Response(200, text="\n\n".join(events), headers={"content-type": "text/event-stream"})

    analyzer = OpenAICompatibleVoiceAnalyzer(
        Path.cwd().parent,
        provider="custom",
        base_url="https://analysis.example/v1",
        model="fiction-analyst",
        api_key="secret-key",
        api_protocol="responses",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_delay_seconds=0,
    )

    profile = analyzer.analyze(
        CharacterEvidencePack(
            character_id="character-xiao-yan",
            display_name="萧炎",
            mention_count=12,
            dialogue_count=3,
            gender_hint="male",
            evidence=["萧炎平静地说道：\"我已经决定了。\""],
        )
    )

    assert profile.backend == "cloud"
    assert profile.model == "fiction-analyst"
    assert profile.personality_tags == ["克制", "果断"]


def test_cloud_connection_test_uses_a_small_probe_contract() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]["schema"]
        assert list(schema["properties"]) == ["ok"]
        assert body["max_tokens"] == 16
        messages = body["messages"]
        assert len(messages) == 2
        assert sum(len(message["content"]) for message in messages) < 140
        assert all("Character Evidence Pack" not in message["content"] for message in messages)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"ok":true}'}}
                ]
            },
        )

    analyzer = OpenAICompatibleVoiceAnalyzer(
        Path.cwd().parent,
        provider="custom",
        base_url="https://analysis.example/v1",
        model="fiction-analyst",
        api_key="secret-key",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_delay_seconds=0,
    )

    analyzer.test_connection()


def test_cloud_connection_test_limits_responses_output() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["max_output_tokens"] == 16
        assert list(body["text"]["format"]["schema"]["properties"]) == ["ok"]
        input_text = "".join(
            content["text"]
            for message in body["input"]
            for content in message["content"]
        )
        assert len(input_text) < 140
        return httpx.Response(200, json={"output_text": '{"ok":true}'})

    analyzer = OpenAICompatibleVoiceAnalyzer(
        Path.cwd().parent,
        provider="custom",
        base_url="https://analysis.example/v1",
        model="fiction-analyst",
        api_key="secret-key",
        api_protocol="responses",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_delay_seconds=0,
    )

    analyzer.test_connection()


def test_cloud_analyzer_normalizes_taxonomy_labels_and_ignores_free_personality_tags() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        payload = analyzer_response(
            personality_tags=["沉着", "protective"],
            texture="干净",
            constraints=["保持自然口语"],
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
        )

    analyzer = OpenAICompatibleVoiceAnalyzer(
        Path.cwd().parent,
        provider="custom",
        base_url="https://analysis.example/v1",
        model="fiction-analyst",
        api_key="secret-key",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_delay_seconds=0,
    )

    profile = analyzer.analyze(
        CharacterEvidencePack(
            character_id="character-xiao-zhan",
            display_name="萧战",
            mention_count=10,
            dialogue_count=3,
            gender_hint="male",
            evidence=["萧战语气沉稳地安慰儿子。"],
        )
    )

    assert profile.personality_tags == ["沉着"]
    assert "干净" in profile.timbre_tags
    assert profile.voice_constraints == ["保持自然口语"]


def test_cloud_analyzer_trims_oversized_controlled_lists_before_validation() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        payload = analyzer_response(
            constraints=[
                "preserve_identity_neutral",
                "avoid_flat_delivery",
                "preserve_clear_articulation",
                "avoid_announcer_tone",
            ]
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
        )

    analyzer = OpenAICompatibleVoiceAnalyzer(
        Path.cwd().parent,
        provider="custom",
        base_url="https://analysis.example/v1",
        model="fiction-analyst",
        api_key="secret-key",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_delay_seconds=0,
    )

    profile = analyzer.analyze(
        CharacterEvidencePack(
            character_id="character-oversized-constraints",
            display_name="萧炎",
            mention_count=12,
            dialogue_count=3,
            gender_hint="male",
            evidence=["萧炎平静地说道：\"我已经决定了。\""],
        )
    )

    assert profile.voice_constraints == ["中性参考不携带场景情绪", "避免表达平直", "保持吐字清楚"]


def test_configurable_analyzer_reads_available_cloud_models(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.headers["Authorization"] == "Bearer draft-key"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-5.6-terra", "owned_by": "custom"},
                    {
                        "id": "gpt-5.6-luna",
                        "owned_by": "custom",
                        "supported_endpoint_types": ["openai"],
                    },
                ]
            },
        )

    manager = ConfigurableVoiceAnalyzer(
        tmp_path,
        default_backend="rules",
        cloud_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    catalog = manager.list_models(
        VoiceAnalysisModelCatalogRequest(
            provider="custom",
            base_url="https://analysis.example/v1",
            api_key="draft-key",
        )
    )

    assert [model.id for model in catalog.models] == ["gpt-5.6-luna", "gpt-5.6-terra"]
    assert catalog.models[0].supported_endpoint_types == ["openai"]


def test_cloud_analyzer_falls_back_to_json_object_and_resolves_speaker() -> None:
    response_formats: list[str] = []
    request_bodies: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        request_bodies.append(body)
        response_formats.append(body["response_format"]["type"])
        if len(response_formats) == 1:
            return httpx.Response(400, json={"error": "json_schema is unsupported"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "d": [
                                        {
                                            "i": "passage-2",
                                            "s": "萧熏儿",
                                            "g": "female",
                                            "k": "named",
                                            "e": "tender",
                                            "v": 0.62,
                                            "t": "soft",
                                            "c": 0.94,
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    analyzer = OpenAICompatibleVoiceAnalyzer(
        Path.cwd().parent,
        provider="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-flash",
        api_key="secret-key",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_delay_seconds=0,
    )

    result = analyzer.analyze_director(
        [
            DirectorPassageEvidence(
                passage_id="passage-2",
                text="萧炎哥哥。",
                context="萧熏儿微笑着柔声道。\n“萧炎哥哥。”",
            )
        ],
        [
            DirectorCharacter(display_name="萧炎", gender="male"),
            DirectorCharacter(display_name="萧熏儿", aliases=["熏儿"], gender="female"),
        ],
    )

    assert response_formats == ["json_schema", "json_object"]
    schema = request_bodies[0]["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"d"}
    prompt = request_bodies[0]["messages"][1]["content"]
    assert '"i":"passage-2"' in prompt
    assert 'C=["萧熏儿微笑着柔声道。\\n“萧炎哥哥。”"]' in prompt
    assert '"c":0' in prompt
    assert 'g=说话人性别' in prompt
    assert '"passage_id"' not in prompt
    assert result.backend == "cloud"
    assert result.decisions[0].speaker == "萧熏儿"
    assert result.decisions[0].speaker_gender == "female"
    assert result.decisions[0].speaker_kind == "named"
    assert result.decisions[0].rationale == "云端紧凑协议根据局部上下文完成裁决"


def test_cloud_analyzer_caches_json_object_capability_after_schema_rejection() -> None:
    response_formats: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        response_formats.append(body["response_format"]["type"])
        if response_formats == ["json_schema"]:
            return httpx.Response(400, json={"error": "json_schema is unsupported"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "d": [
                                        {
                                            "i": "passage-cache",
                                            "s": "萧炎",
                                            "g": "male",
                                            "k": "named",
                                            "e": "natural",
                                            "v": 0.5,
                                            "t": "natural",
                                            "c": 0.9,
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    shared_cache: dict[str, str] = {}
    analyzer = OpenAICompatibleVoiceAnalyzer(
        Path.cwd().parent,
        provider="custom",
        base_url="https://analysis.example/v1",
        model="schema-cache-model",
        api_key="secret-key",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_delay_seconds=0,
        structured_mode_cache=shared_cache,
    )
    passages = [
        DirectorPassageEvidence(
            passage_id="passage-cache",
            text="开始吧。",
            context="萧炎说道：\"开始吧。\"",
            explicit_speaker="萧炎",
        )
    ]
    characters = [DirectorCharacter(display_name="萧炎", gender="male")]

    analyzer.analyze_director(passages, characters)
    analyzer.analyze_director(passages, characters)

    assert response_formats == ["json_schema", "json_object", "json_object"]


def test_cloud_analyzer_reports_authentication_failure_without_retry() -> None:
    requests = 0

    def respond(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(401, json={"error": "invalid key"})

    analyzer = OpenAICompatibleVoiceAnalyzer(
        Path.cwd().parent,
        provider="qwen",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
        api_key="wrong-key",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
        retry_delay_seconds=0,
    )

    with pytest.raises(VoiceAnalysisTransportError, match="API Key"):
        analyzer.analyze(
            CharacterEvidencePack(
                character_id="character-test",
                display_name="测试角色",
                mention_count=1,
                dialogue_count=1,
                evidence=["测试角色说道：\"你好。\""],
            )
        )

    assert requests == 1


def test_cloud_director_contexts_merge_overlapping_sliding_windows() -> None:
    shared = "乙" * 80
    contexts, passage_indexes = OpenAICompatibleVoiceAnalyzer._compact_director_contexts(
        [
            DirectorPassageEvidence(
                passage_id="passage-1",
                text="第一句。",
                context=f"{'甲' * 80}{shared}",
            ),
            DirectorPassageEvidence(
                passage_id="passage-2",
                text="第二句。",
                context=f"{shared}{'丙' * 80}",
            ),
            DirectorPassageEvidence(
                passage_id="passage-3",
                text="第三句。",
                context="独立上下文" * 20,
            ),
        ]
    )

    assert contexts == [f"{'甲' * 80}{shared}{'丙' * 80}", "独立上下文" * 20]
    assert passage_indexes == [0, 0, 1]


def test_configurable_analyzer_persists_secret_without_exposing_it(tmp_path: Path) -> None:
    manager = ConfigurableVoiceAnalyzer(tmp_path, default_backend="rules")

    view = manager.update_configuration(
        VoiceAnalysisConfigurationUpdate(
            backend="cloud",
            provider="kimi",
            base_url="https://api.moonshot.cn/v1",
            model="moonshot-v1-32k",
            api_key="private-key",
        )
    )

    assert view.api_key_configured is True
    assert view.cloud_parallelism == 4
    assert view.cloud_director_batch_size == 48
    assert "private-key" not in view.model_dump_json()
    settings_path = tmp_path / "outputs" / "settings" / "voice_analysis.json"
    assert json.loads(settings_path.read_text(encoding="utf-8"))["profiles"][0]["api_key"] == "private-key"

    local_view = manager.update_configuration(
        VoiceAnalysisConfigurationUpdate(
            backend="local",
            provider="kimi",
            clear_api_key=True,
            cloud_parallelism=2,
            cloud_director_batch_size=32,
        )
    )
    assert local_view.api_key_configured is False
    assert local_view.cloud_parallelism == 2
    assert local_view.cloud_director_batch_size == 32
    assert json.loads(settings_path.read_text(encoding="utf-8"))["profiles"][0]["api_key"] == ""


def test_configurable_analyzer_fails_over_and_cools_down_failed_primary(tmp_path: Path) -> None:
    calls = {"primary": 0, "secondary": 0}
    logger = RecordingLogger()

    def respond(request: httpx.Request) -> httpx.Response:
        endpoint = "primary" if request.url.host == "primary.example" else "secondary"
        calls[endpoint] += 1
        if endpoint == "primary":
            return httpx.Response(503, json={"error": "primary unavailable"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(analyzer_response(), ensure_ascii=False)}}
                ]
            },
        )

    manager = ConfigurableVoiceAnalyzer(
        tmp_path,
        default_backend="rules",
        cloud_client=httpx.Client(transport=httpx.MockTransport(respond)),
        runtime_logger=logger,
    )
    manager.update_configuration(
        VoiceAnalysisConfigurationUpdate(
            backend="cloud",
            failover_enabled=True,
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
    evidence = CharacterEvidencePack(
        character_id="character-failover",
        display_name="Failover Character",
        mention_count=2,
        dialogue_count=1,
        evidence=["Fail over to the secondary endpoint."],
    )

    first = manager.analyze(evidence)
    second = manager.analyze(evidence)

    assert first.model == "secondary-model"
    assert second.model == "secondary-model"
    assert calls == {"primary": 1, "secondary": 2}
    view = manager.configuration()
    assert view.profiles[0].health == "cooldown"
    assert view.profiles[1].health == "healthy"
    logs = "\n".join(logger.messages)
    assert "[CLOUD FAILOVER]" in logs
    assert "failed=P1" in logs
    assert "selected=P2" in logs
    assert "primary-key" not in logs
    assert "secondary-key" not in logs
