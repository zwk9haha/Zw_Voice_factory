from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .long_form import (
    TextHeadingCandidate,
    TextStructureDraft,
    TextStructureSelection,
    heuristic_heading_ids,
)


AnalyzerMode = Literal["local", "hybrid", "cloud", "rules"]
ConfigurableAnalyzerMode = Literal["local", "hybrid", "cloud"]
VoiceAnalysisProvider = Literal["custom", "qwen", "kimi", "doubao", "gemini"]
VoiceAnalysisApiProtocol = Literal["chat_completions", "responses"]
Gender = Literal["male", "female", "unknown"]
SpeakerKind = Literal["named", "extra", "unknown"]
CharacterCandidateScreeningAction = Literal["keep", "reject", "merge"]
CompactCharacterCandidateScreeningAction = Literal["k", "r", "m"]
CharacterCandidateScreeningReason = Literal[
    "named_identity",
    "stable_title",
    "plausible_low_frequency",
    "alias",
    "action_phrase",
    "connector_fragment",
    "generic_role",
    "addressee",
    "truncated_name",
    "non_character",
    "uncertain",
]
CLOUD_API_LOG_PREVIEW_CHARS = 12_000
CLOUD_API_MAX_ATTEMPTS = 3
DEFAULT_CLOUD_PARALLELISM = 4
DEFAULT_CLOUD_DIRECTOR_BATCH_SIZE = 48
ANALYSIS_ALIAS_LIMIT = 12

CLOUD_PROVIDER_BASE_URLS: dict[VoiceAnalysisProvider, str] = {
    "custom": "",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "kimi": "https://api.moonshot.cn/v1",
    "doubao": "https://ark.cn-beijing.volces.com/api/v3",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}

CLOUD_PROVIDER_PROTOCOLS: dict[VoiceAnalysisProvider, VoiceAnalysisApiProtocol] = {
    "custom": "responses",
    "qwen": "chat_completions",
    "kimi": "chat_completions",
    "doubao": "chat_completions",
    "gemini": "chat_completions",
}


class VoiceAnalysisError(RuntimeError):
    pass


class VoiceAnalysisTransportError(VoiceAnalysisError):
    pass


def _bounded_analysis_aliases(value: object) -> object:
    if not isinstance(value, (list, tuple, set)):
        return value
    aliases: list[object] = []
    seen: set[str] = set()
    for item in value:
        normalized = item.strip() if isinstance(item, str) else item
        if normalized == "":
            continue
        key = normalized.casefold() if isinstance(normalized, str) else repr(normalized)
        if key in seen:
            continue
        seen.add(key)
        aliases.append(normalized)
        if len(aliases) == ANALYSIS_ALIAS_LIMIT:
            break
    return aliases


class CharacterEvidencePack(BaseModel):
    project_id: str | None = Field(default=None, exclude=True)
    character_id: str
    display_name: str
    aliases: list[str] = Field(default_factory=list, max_length=12)
    mention_count: int = Field(ge=0)
    dialogue_count: int = Field(ge=0)
    gender_hint: Gender = "unknown"
    evidence: list[str] = Field(min_length=1, max_length=8)
    user_attributes: str | None = Field(default=None, max_length=500)
    local_screening: str | None = Field(default=None, max_length=1_500)

    @field_validator("aliases", mode="before")
    @classmethod
    def bound_aliases(cls, value: object) -> object:
        return _bounded_analysis_aliases(value)


class CharacterCandidateScreeningInput(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=80)
    mention_count: int = Field(ge=0)
    dialogue_count: int = Field(ge=0)
    peak_batch_mentions: int = Field(default=0, ge=0)
    peak_batch_dialogue_count: int = Field(default=0, ge=0)
    batch_presence_count: int = Field(default=1, ge=1)
    confidence: float = Field(default=0.5, ge=0, le=1)
    entity_confidence: float = Field(default=0.0, ge=0, le=1)
    production_priority: float = Field(default=0.0, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list, max_length=4)


class CharacterCandidateScreeningDecision(BaseModel):
    candidate_id: str
    action: CharacterCandidateScreeningAction
    canonical_candidate_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=100)


class CompactCharacterCandidateScreeningDecision(BaseModel):
    a: CompactCharacterCandidateScreeningAction
    t: str | None = None
    c: float = Field(ge=0, le=1)
    r: CharacterCandidateScreeningReason


class CharacterCandidateScreeningSelection(BaseModel):
    d: dict[str, CompactCharacterCandidateScreeningDecision]


class CharacterCandidateScreeningDraft(BaseModel):
    decisions: list[CharacterCandidateScreeningDecision]
    backend: AnalyzerMode
    model: str | None = None


class CharacterVoiceProfile(BaseModel):
    gender: Gender
    age_range: str
    personality_tags: list[str]
    timbre_tags: list[str]
    delivery_tags: list[str]
    voice_constraints: list[str]
    voice_prompt: str
    confidence: float = Field(ge=0, le=1)
    rationale: str
    backend: AnalyzerMode
    model: str | None = None


class ReferenceTextDraft(BaseModel):
    text: str = Field(min_length=20, max_length=180)
    rationale: str = Field(min_length=1, max_length=240)
    backend: AnalyzerMode
    model: str | None = None


class ReferenceTextSelection(BaseModel):
    text: str = Field(min_length=20, max_length=180)
    rationale: str = Field(min_length=1, max_length=240)


class DirectorCharacter(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=12)
    gender: Gender = "unknown"

    @field_validator("aliases", mode="before")
    @classmethod
    def bound_aliases(cls, value: object) -> object:
        return _bounded_analysis_aliases(value)


class DirectorPassageEvidence(BaseModel):
    project_id: str | None = Field(default=None, exclude=True)
    passage_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1_000)
    context: str = Field(min_length=1, max_length=2_000)
    explicit_speaker: str | None = Field(default=None, max_length=80)


class DirectorPassageDecision(BaseModel):
    passage_id: str
    speaker: str
    speaker_gender: Gender = "unknown"
    speaker_kind: SpeakerKind = "unknown"
    emotion: str
    emotion_intensity: float = Field(ge=0, le=1)
    tone: str
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=240)


class DirectorDecisionSelection(BaseModel):
    decisions: list[DirectorPassageDecision]


class CompactDirectorPassageDecision(BaseModel):
    i: str
    s: str
    g: Gender = "unknown"
    k: SpeakerKind = "unknown"
    e: str
    v: float = Field(default=0.5, ge=0, le=1)
    t: str
    c: float = Field(default=0.8, ge=0, le=1)


class CompactDirectorDecisionSelection(BaseModel):
    d: list[CompactDirectorPassageDecision]


class LocalCompactDirectorPassageDecision(BaseModel):
    s: str
    e: str
    t: str


class LocalCompactDirectorDecisionSelection(BaseModel):
    d: list[LocalCompactDirectorPassageDecision]


class CloudAnalysisEvent(BaseModel):
    project_id: str
    call_id: str
    direction: Literal["INPUT", "OUTPUT", "ERROR"]
    operation: str
    provider: VoiceAnalysisProvider
    protocol: VoiceAnalysisApiProtocol
    model: str
    attempt: int
    structured_mode: str
    total_chars: int
    preview: str
    status_code: int | None = None
    elapsed_seconds: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


CloudAnalysisEventHandler = Callable[[CloudAnalysisEvent], None]


class DirectorAnalysisDraft(BaseModel):
    decisions: list[DirectorPassageDecision]
    backend: AnalyzerMode
    model: str | None = None


class VoiceAnalysisStatus(BaseModel):
    backend: AnalyzerMode
    available: bool
    model: str | None = None
    detail: str
    taxonomy_version: int
    model_store: str | None = None


class VoiceAnalysisCloudProfile(BaseModel):
    profile_id: str = Field(default_factory=lambda: f"cloud-{uuid.uuid4().hex[:12]}", min_length=1, max_length=120)
    name: str = Field(default="默认云端 API", min_length=1, max_length=80)
    provider: VoiceAnalysisProvider = "custom"
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    api_protocol: VoiceAnalysisApiProtocol = "chat_completions"
    api_key: str = Field(default="", max_length=2_000)
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def infer_provider_protocol(cls, value: object) -> object:
        if not isinstance(value, dict) or "api_protocol" in value:
            return value
        provider = value.get("provider", "custom")
        if not isinstance(provider, str):
            provider = "custom"
        return {**value, "api_protocol": CLOUD_PROVIDER_PROTOCOLS.get(provider, "chat_completions")}


class VoiceAnalysisCloudProfileView(BaseModel):
    profile_id: str
    name: str
    provider: VoiceAnalysisProvider
    base_url: str
    model: str
    api_protocol: VoiceAnalysisApiProtocol
    api_key_configured: bool
    enabled: bool
    priority: int
    health: Literal["unknown", "healthy", "failed", "cooldown"] = "unknown"
    last_error: str | None = None


class VoiceAnalysisCloudProfileUpdate(BaseModel):
    profile_id: str | None = Field(default=None, max_length=120)
    name: str = Field(min_length=1, max_length=80)
    provider: VoiceAnalysisProvider = "custom"
    base_url: str = Field(max_length=500)
    model: str = Field(max_length=200)
    api_protocol: VoiceAnalysisApiProtocol | None = None
    api_key: str | None = Field(default=None, max_length=2_000)
    clear_api_key: bool = False
    enabled: bool = True


class VoiceAnalysisConfiguration(BaseModel):
    backend: AnalyzerMode = "local"
    failover_enabled: bool = True
    cloud_parallelism: int = Field(default=DEFAULT_CLOUD_PARALLELISM, ge=1, le=8)
    cloud_director_batch_size: int = Field(default=DEFAULT_CLOUD_DIRECTOR_BATCH_SIZE, ge=8, le=96)
    profiles: list[VoiceAnalysisCloudProfile] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def migrate_single_profile_configuration(cls, value: object) -> object:
        if not isinstance(value, dict) or "profiles" in value:
            return value
        legacy_keys = {"provider", "base_url", "model", "api_protocol", "api_key"}
        if not legacy_keys.intersection(value) and value.get("backend") not in {"cloud", "hybrid"}:
            return value
        provider = value.get("provider", "custom")
        if not isinstance(provider, str):
            provider = "custom"
        profile = {
            "profile_id": "legacy-default",
            "name": "默认云端 API",
            "provider": provider,
            "base_url": value.get("base_url", ""),
            "model": value.get("model", ""),
            "api_protocol": value.get("api_protocol", CLOUD_PROVIDER_PROTOCOLS.get(provider, "chat_completions")),
            "api_key": value.get("api_key", ""),
            "enabled": True,
        }
        return {
            "backend": value.get("backend", "local"),
            "failover_enabled": value.get("failover_enabled", True),
            "cloud_parallelism": value.get("cloud_parallelism", DEFAULT_CLOUD_PARALLELISM),
            "cloud_director_batch_size": value.get("cloud_director_batch_size", DEFAULT_CLOUD_DIRECTOR_BATCH_SIZE),
            "profiles": [profile],
        }


class VoiceAnalysisConfigurationView(BaseModel):
    backend: AnalyzerMode
    provider: VoiceAnalysisProvider
    base_url: str
    model: str
    api_protocol: VoiceAnalysisApiProtocol
    api_key_configured: bool
    failover_enabled: bool = True
    cloud_parallelism: int = DEFAULT_CLOUD_PARALLELISM
    cloud_director_batch_size: int = DEFAULT_CLOUD_DIRECTOR_BATCH_SIZE
    profiles: list[VoiceAnalysisCloudProfileView] = Field(default_factory=list)


class VoiceAnalysisConfigurationUpdate(BaseModel):
    backend: ConfigurableAnalyzerMode
    provider: VoiceAnalysisProvider | None = None
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    api_protocol: VoiceAnalysisApiProtocol | None = None
    api_key: str | None = Field(default=None, max_length=2_000)
    clear_api_key: bool = False
    failover_enabled: bool | None = None
    cloud_parallelism: int | None = Field(default=None, ge=1, le=8)
    cloud_director_batch_size: int | None = Field(default=None, ge=8, le=96)
    profiles: list[VoiceAnalysisCloudProfileUpdate] | None = Field(default=None, max_length=20)


class VoiceAnalysisModelCatalogRequest(BaseModel):
    profile_id: str | None = Field(default=None, max_length=120)
    provider: VoiceAnalysisProvider | None = None
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=2_000)


class VoiceAnalysisModelOption(BaseModel):
    id: str
    owned_by: str | None = None
    supported_endpoint_types: list[str] = Field(default_factory=list)


class VoiceAnalysisModelCatalog(BaseModel):
    provider: VoiceAnalysisProvider
    base_url: str
    models: list[VoiceAnalysisModelOption]


class VoiceAttributeSelection(BaseModel):
    gender: Gender
    age_range: str
    personality_tags: list[str] = Field(min_length=1, max_length=4)
    pitch: str
    weight: str
    brightness: str
    texture: list[str] = Field(min_length=1, max_length=2)
    resonance: str
    articulation: str
    breath: str
    pace: str
    rhythm: str
    dynamics: str
    baseline: str
    constraints: list[str] = Field(min_length=1, max_length=3)
    signature_core: str = Field(min_length=20, max_length=100)
    signature_habits: list[str] = Field(min_length=2, max_length=3)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=240)

    @model_validator(mode="before")
    @classmethod
    def bound_controlled_arrays(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        bounded = dict(value)
        for field, limit in {
            "personality_tags": 4,
            "texture": 2,
            "constraints": 3,
            "signature_habits": 3,
        }.items():
            items = bounded.get(field)
            if isinstance(items, str):
                items = [items]
            if not isinstance(items, list):
                continue
            unique: list[object] = []
            seen: set[str] = set()
            for item in items:
                marker = item.strip() if isinstance(item, str) else repr(item)
                if not marker or marker in seen:
                    continue
                seen.add(marker)
                unique.append(item.strip() if isinstance(item, str) else item)
                if len(unique) >= limit:
                    break
            bounded[field] = unique
        return bounded

    @field_validator("personality_tags", "texture", "constraints", "signature_habits", mode="before")
    @classmethod
    def normalize_string_arrays(cls, value: object) -> object:
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return value

    @field_validator("signature_core")
    @classmethod
    def clean_signature_core(cls, value: str) -> str:
        cleaned = value.strip().rstrip("；。")
        if len(cleaned) < 20:
            raise ValueError("signature_core 必须为 20-100 个汉字")
        return cleaned

    @field_validator("signature_habits")
    @classmethod
    def validate_signature_habits(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip().rstrip("；。").replace("；", "，").replace("。", "，") for value in values]
        if any(len(value) < 6 or len(value) > 48 for value in cleaned):
            raise ValueError("signature_habits 每项必须为 6-48 个汉字")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("signature_habits 不得重复")
        return cleaned


class VoiceAnalyzer(Protocol):
    def status(self) -> VoiceAnalysisStatus: ...

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile: ...

    def screen_character_candidates(
        self,
        project_id: str,
        candidates: list[CharacterCandidateScreeningInput],
        canonical_anchors: list[CharacterCandidateScreeningInput],
    ) -> CharacterCandidateScreeningDraft: ...

    def generate_reference_text(
        self,
        evidence_pack: CharacterEvidencePack,
        voice_prompt: str,
    ) -> ReferenceTextDraft: ...

    def analyze_director(
        self,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> DirectorAnalysisDraft: ...

    def analyze_text_structure(
        self,
        project_id: str,
        candidates: list[TextHeadingCandidate],
        total_characters: int,
    ) -> TextStructureDraft: ...


def taxonomy_path(workspace_root: Path) -> Path:
    return skill_reference_path(workspace_root, "voice-attribute-taxonomy.json")


def runtime_prompt_path(workspace_root: Path) -> Path:
    return skill_reference_path(workspace_root, "runtime-system-prompt.md")


def skill_reference_path(workspace_root: Path, filename: str) -> Path:
    return named_skill_reference_path(workspace_root, "analyze-character-voice", filename)


def named_skill_reference_path(workspace_root: Path, skill_name: str, filename: str) -> Path:
    workspace_path = workspace_root / "skills" / skill_name / "references" / filename
    if workspace_path.is_file():
        return workspace_path
    return Path(__file__).resolve().parents[2] / "skills" / skill_name / "references" / filename


def load_taxonomy(workspace_root: Path) -> dict[str, object]:
    path = taxonomy_path(workspace_root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VoiceAnalysisError(f"无法读取音色属性分类法：{path}") from error


def load_runtime_prompt(workspace_root: Path) -> str:
    path = runtime_prompt_path(workspace_root)
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise VoiceAnalysisError(f"无法读取角色音色分析运行时提示：{path}") from error
    if not prompt:
        raise VoiceAnalysisError(f"角色音色分析运行时提示为空：{path}")
    return prompt


def load_director_runtime_prompt(workspace_root: Path) -> str:
    path = named_skill_reference_path(
        workspace_root,
        "analyze-fiction-director",
        "runtime-system-prompt.md",
    )
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise VoiceAnalysisError(f"无法读取小说导演分析运行时提示：{path}") from error
    if not prompt:
        raise VoiceAnalysisError(f"小说导演分析运行时提示为空：{path}")
    return prompt


def load_long_form_runtime_prompt(workspace_root: Path) -> str:
    path = named_skill_reference_path(
        workspace_root,
        "analyze-long-fiction",
        "runtime-system-prompt.md",
    )
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise VoiceAnalysisError(f"无法读取长篇小说结构分析运行时提示：{path}") from error
    if not prompt:
        raise VoiceAnalysisError(f"长篇小说结构分析运行时提示为空：{path}")
    return prompt


def load_character_screening_runtime_prompt(workspace_root: Path) -> str:
    path = named_skill_reference_path(
        workspace_root,
        "screen-fiction-characters",
        "runtime-system-prompt.md",
    )
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise VoiceAnalysisError(f"无法读取小说角色候选粗筛运行时提示：{path}") from error
    if not prompt:
        raise VoiceAnalysisError(f"小说角色候选粗筛运行时提示为空：{path}")
    return prompt


def text_structure_response_schema(candidates: list[TextHeadingCandidate]) -> dict[str, object]:
    candidate_ids = [candidate.candidate_id for candidate in candidates]
    return {
        "type": "object",
        "properties": {
            "heading_ids": {
                "type": "array",
                "items": {"type": "string", "enum": candidate_ids},
                "maxItems": len(candidate_ids),
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string", "minLength": 1, "maxLength": 240},
        },
        "required": ["heading_ids", "confidence", "rationale"],
        "additionalProperties": False,
    }


class RuleBasedVoiceAnalyzer:
    def __init__(self, workspace_root: Path) -> None:
        self.taxonomy = load_taxonomy(workspace_root)

    def status(self) -> VoiceAnalysisStatus:
        return VoiceAnalysisStatus(
            backend="rules",
            available=True,
            detail="规则兼容模式",
            taxonomy_version=int(self.taxonomy["schema_version"]),
        )

    def screen_character_candidates(
        self,
        project_id: str,
        candidates: list[CharacterCandidateScreeningInput],
        canonical_anchors: list[CharacterCandidateScreeningInput],
    ) -> CharacterCandidateScreeningDraft:
        del project_id, canonical_anchors
        return CharacterCandidateScreeningDraft(
            decisions=[
                CharacterCandidateScreeningDecision(
                    candidate_id=candidate.candidate_id,
                    action="keep",
                    confidence=candidate.confidence,
                    rationale="规则预览已完成基础名称过滤",
                )
                for candidate in candidates
            ],
            backend="rules",
        )

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        gender_label = {"male": "男性", "female": "女性", "unknown": "性别不限定"}[evidence_pack.gender_hint]
        return CharacterVoiceProfile(
            gender=evidence_pack.gender_hint,
            age_range="adult",
            personality_tags=["沉着"],
            timbre_tags=["中音区", "适中", "均衡", "干净", "混合共鸣"],
            delivery_tags=["自然口语咬字", "气息平稳", "语速平稳", "节奏均匀", "动态自然", "中性自然"],
            voice_constraints=["保持自然口语", "中性参考不携带场景情绪"],
            voice_prompt=f"成年{gender_label}声线；中音区，声线重量适中、明暗均衡，质感干净，以混合共鸣为主；自然口语咬字，气息平稳，语速与节奏稳定，动态自然；中性参考保持自然口语，不携带场景情绪。",
            confidence=0.45,
            rationale="规则兼容模式未调用语言模型，仅根据性别提示生成保守基线。",
            backend="rules",
        )

    def generate_reference_text(
        self,
        evidence_pack: CharacterEvidencePack,
        voice_prompt: str,
    ) -> ReferenceTextDraft:
        return ReferenceTextDraft(
            text="清晨的风穿过长街，远处的钟声渐渐清晰，我们仍按原定的方向从容前行。",
            rationale="规则兼容模式使用覆盖常见声母、韵母与停连结构的中性句式。",
            backend="rules",
        )

    def analyze_director(
        self,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> DirectorAnalysisDraft:
        known_names = {character.display_name for character in characters}
        gender_by_name = {character.display_name: character.gender for character in characters}
        decisions = [
            DirectorPassageDecision(
                passage_id=passage.passage_id,
                speaker=passage.explicit_speaker if passage.explicit_speaker in known_names else "未知角色",
                speaker_gender=gender_by_name.get(passage.explicit_speaker or "", "unknown"),
                speaker_kind="named" if passage.explicit_speaker in known_names else "unknown",
                emotion="natural",
                emotion_intensity=0.5,
                tone="natural",
                confidence=0.98 if passage.explicit_speaker in known_names else 0.2,
                rationale="使用文本中的明确说话归属" if passage.explicit_speaker in known_names else "规则模式缺少足够的说话人证据",
            )
            for passage in passages
        ]
        return DirectorAnalysisDraft(decisions=decisions, backend="rules")

    def analyze_text_structure(
        self,
        project_id: str,
        candidates: list[TextHeadingCandidate],
        total_characters: int,
    ) -> TextStructureDraft:
        del project_id, total_characters
        heading_ids = heuristic_heading_ids(candidates)
        return TextStructureDraft(
            heading_ids=heading_ids,
            confidence=0.72 if heading_ids else 0.25,
            rationale="使用标题格式、编号和空行边界筛选疑似章节" if heading_ids else "规则证据不足，保留字数切分",
            backend="rules",
        )


class OllamaVoiceAnalyzer:
    backend_mode: AnalyzerMode = "local"
    backend_display_name = "本地模型"
    allow_taxonomy_fallback = False

    def __init__(
        self,
        workspace_root: Path,
        base_url: str | None = None,
        model: str | None = None,
        client: httpx.Client | None = None,
        retry_delay_seconds: float = 0.5,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.base_url = (base_url or os.getenv("ZW_VOICE_OLLAMA_URL", "http://127.0.0.1:11435")).rstrip("/")
        self.model = model or os.getenv("ZW_VOICE_OLLAMA_MODEL", "zw-voice-analyzer:4b")
        self.model_store = self.workspace_root / "local_models" / "ollama"
        self.taxonomy = load_taxonomy(self.workspace_root)
        load_runtime_prompt(self.workspace_root)
        load_director_runtime_prompt(self.workspace_root)
        load_character_screening_runtime_prompt(self.workspace_root)
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(180, connect=5),
            trust_env=False,
        )
        self.retry_delay_seconds = retry_delay_seconds

    def status(self) -> VoiceAnalysisStatus:
        try:
            response = self.client.get(f"{self.base_url}/api/tags", timeout=3)
            response.raise_for_status()
            installed = {item.get("name") for item in response.json().get("models", [])}
            available = self.model in installed
            detail = "本地模型已就绪" if available else f"未安装模型 {self.model}"
        except (httpx.HTTPError, ValueError, TypeError) as error:
            available = False
            detail = f"Ollama 不可用：{error}"
        return VoiceAnalysisStatus(
            backend="local",
            available=available,
            model=self.model,
            detail=detail,
            taxonomy_version=int(self.taxonomy["schema_version"]),
            model_store=str(self.model_store),
        )

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": self._evidence_prompt(evidence_pack)},
        ]
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                content = self._request_content(messages, project_id=evidence_pack.project_id)
                selection = VoiceAttributeSelection.model_validate_json(content)
                self._normalize_selection(selection)
                self._validate_selection(selection)
                return self._compile_profile(selection)
            except VoiceAnalysisTransportError:
                raise
            except (KeyError, TypeError, ValueError, ValidationError, VoiceAnalysisError) as error:
                last_error = error
                if attempt == 0:
                    messages.append({"role": "user", "content": f"上一次输出未通过契约校验：{error}。只返回修正后的 JSON。"})
        raise VoiceAnalysisError(f"{self.backend_display_name}未能生成有效音色画像：{last_error}") from last_error

    def screen_character_candidates(
        self,
        project_id: str,
        candidates: list[CharacterCandidateScreeningInput],
        canonical_anchors: list[CharacterCandidateScreeningInput],
    ) -> CharacterCandidateScreeningDraft:
        if not candidates:
            return CharacterCandidateScreeningDraft(
                decisions=[],
                backend=self.backend_mode,
                model=self.model,
            )
        candidate_handles = {f"c{index:02d}": candidate for index, candidate in enumerate(candidates, start=1)}
        anchor_handles = {
            f"a{index:02d}": candidate
            for index, candidate in enumerate(canonical_anchors[:12], start=1)
        }
        candidate_payload = {
            handle: {
                "n": candidate.display_name,
                "m": candidate.mention_count,
                "d": candidate.dialogue_count,
                "p": candidate.batch_presence_count,
                "pm": candidate.peak_batch_mentions,
                "pd": candidate.peak_batch_dialogue_count,
                "c": round(candidate.confidence, 2),
                "e": [item[:96] for item in candidate.evidence[:2]],
            }
            for handle, candidate in candidate_handles.items()
        }
        anchor_payload = {
            handle: {
                "n": candidate.display_name,
                "m": candidate.mention_count,
                "d": candidate.dialogue_count,
            }
            for handle, candidate in anchor_handles.items()
        }
        messages = [
            {"role": "system", "content": load_character_screening_runtime_prompt(self.workspace_root)},
            {
                "role": "user",
                "content": (
                    "审核 C 中每个候选。输出 d 必须包含 C 的每个短键且只出现一次。"
                    "A 是已保留规范角色，只能作为合并目标。"
                    "字段：n=名称,m=出现数,d=对白数,p=批次覆盖,pm/pd=峰值,c=解析置信度,e=证据。"
                    "动作：k=保留,r=拒绝,m=合并；t=合并目标短键，非合并必须为 null。\n"
                    f"A={json.dumps(anchor_payload, ensure_ascii=False, separators=(',', ':'))}\n"
                    f"C={json.dumps(candidate_payload, ensure_ascii=False, separators=(',', ':'))}"
                ),
            },
        ]
        schema = self._candidate_screening_schema(list(candidate_handles), list(anchor_handles))
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                content = self._request_content(
                    messages,
                    schema,
                    keep_alive="2m",
                    project_id=project_id,
                )
                selection = CharacterCandidateScreeningSelection.model_validate_json(content)
                decisions = self._validate_candidate_screening(
                    selection,
                    candidate_handles,
                    anchor_handles,
                )
                return CharacterCandidateScreeningDraft(
                    decisions=decisions,
                    backend=self.backend_mode,
                    model=self.model,
                )
            except VoiceAnalysisTransportError:
                raise
            except (TypeError, ValueError, ValidationError, VoiceAnalysisError) as error:
                last_error = error
                if attempt == 0:
                    messages.append({"role": "user", "content": f"上一次输出未通过契约校验：{error}。只返回修正后的 JSON。"})
        raise VoiceAnalysisError(f"{self.backend_display_name}未能完成角色候选粗筛：{last_error}") from last_error

    def generate_reference_text(
        self,
        evidence_pack: CharacterEvidencePack,
        voice_prompt: str,
    ) -> ReferenceTextDraft:
        messages = [
            {
                "role": "system",
                "content": (
                    "你负责为中文语音克隆生成中性标准参考文本。句子必须自然、可表演、无专有名词，"
                    "覆盖清晰的声母韵母、长短停连和轻重变化；不得携带剧情结论、强烈情绪或角色口头禅。"
                    "只返回符合 JSON Schema 的对象。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "根据角色证据与声线描述生成一条 35-90 个汉字的中性参考句。\n"
                    f"Character Evidence Pack:\n{evidence_pack.model_dump_json(indent=2)}\n"
                    f"Voice Prompt:\n{voice_prompt.strip()}"
                ),
            },
        ]
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                content = self._request_content(
                    messages,
                    self._reference_text_schema(),
                    project_id=evidence_pack.project_id,
                )
                selection = ReferenceTextSelection.model_validate_json(content)
                return ReferenceTextDraft(
                    text=selection.text,
                    rationale=selection.rationale,
                    backend=self.backend_mode,
                    model=self.model,
                )
            except VoiceAnalysisTransportError:
                raise
            except (TypeError, ValueError, ValidationError, VoiceAnalysisError) as error:
                last_error = error
                if attempt == 0:
                    messages.append({"role": "user", "content": f"上一次输出未通过契约校验：{error}。只返回修正后的 JSON。"})
        raise VoiceAnalysisError(f"{self.backend_display_name}未能生成有效标准参考文本：{last_error}") from last_error

    def analyze_director(
        self,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> DirectorAnalysisDraft:
        if not passages:
            return DirectorAnalysisDraft(decisions=[], backend=self.backend_mode, model=self.model)
        known_names = [character.display_name for character in characters]
        character_payload = [
            {"n": character.display_name, "a": character.aliases, "g": character.gender}
            for character in characters
        ]
        contexts, passage_context_indexes = self._compact_director_contexts(passages, max_context_chars=260)
        passage_payload = [
            {
                "i": index + 1,
                "x": passage.text,
                "c": passage_context_indexes[index],
                "s": passage.explicit_speaker,
            }
            for index, passage in enumerate(passages)
        ]
        messages = [
            {"role": "system", "content": load_director_runtime_prompt(self.workspace_root)},
            {
                "role": "user",
                "content": (
                    "使用本地极速协议裁决对白。角色字段：n=姓名,a=别名,g=性别；"
                    "C=共享上下文数组；对白字段：i=编号,x=文本,c=C索引,s=明确说话人。"
                    "输出仅含 d 数组，长度必须与 P 相同并严格保持 P 的顺序；"
                    "每项只允许 s=说话人,e=情绪,t=语气。"
                    "说话人只能选择角色姓名、男路人、女路人或未知角色；不得输出理由、强度、置信度和额外字段。\n"
                    f"R={json.dumps(character_payload, ensure_ascii=False, separators=(',', ':'))}\n"
                    f"C={json.dumps(contexts, ensure_ascii=False, separators=(',', ':'))}\n"
                    f"P={json.dumps(passage_payload, ensure_ascii=False, separators=(',', ':'))}"
                ),
            },
        ]
        schema = self._local_compact_director_response_schema(len(passages), known_names)
        gender_by_name = {character.display_name: character.gender for character in characters}
        intensity_by_emotion = {
            "natural": 0.5,
            "tender": 0.62,
            "joyful": 0.7,
            "sad": 0.64,
            "angry": 0.78,
            "tense": 0.68,
            "fearful": 0.72,
            "surprised": 0.7,
            "solemn": 0.6,
            "sarcastic": 0.64,
        }
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                content = self._request_content(
                    messages,
                    schema,
                    keep_alive="2m",
                    project_id=passages[0].project_id,
                )
                compact = LocalCompactDirectorDecisionSelection.model_validate_json(content)
                if len(compact.d) != len(passages):
                    raise VoiceAnalysisError("本地导演裁决数量必须与输入对白一致")
                decisions: list[DirectorPassageDecision] = []
                for passage, decision in zip(passages, compact.d, strict=True):
                    if decision.s == "男路人":
                        speaker = "未知角色"
                        speaker_gender: Gender = "male"
                        speaker_kind: SpeakerKind = "extra"
                    elif decision.s == "女路人":
                        speaker = "未知角色"
                        speaker_gender = "female"
                        speaker_kind = "extra"
                    elif decision.s in gender_by_name:
                        speaker = decision.s
                        speaker_gender = gender_by_name[decision.s]
                        speaker_kind = "named"
                    else:
                        speaker = "未知角色"
                        speaker_gender = "unknown"
                        speaker_kind = "unknown"
                    decisions.append(
                        DirectorPassageDecision(
                            passage_id=passage.passage_id,
                            speaker=speaker,
                            speaker_gender=speaker_gender,
                            speaker_kind=speaker_kind,
                            emotion=decision.e,
                            emotion_intensity=intensity_by_emotion.get(decision.e, 0.5),
                            tone=decision.t,
                            confidence=0.88 if speaker_kind == "named" else 0.72 if speaker_kind == "extra" else 0.52,
                            rationale="本地极速协议根据局部上下文完成裁决",
                        )
                    )
                selection = DirectorDecisionSelection(decisions=decisions)
                decisions = self._validate_director_decisions(selection, passages, characters)
                return DirectorAnalysisDraft(decisions=decisions, backend=self.backend_mode, model=self.model)
            except VoiceAnalysisTransportError:
                raise
            except (TypeError, ValueError, ValidationError, VoiceAnalysisError) as error:
                last_error = error
                if attempt == 0:
                    messages.append({"role": "user", "content": f"上一次紧凑输出未通过校验：{error}。只返回修正后的 d 对象 JSON。"})
        raise VoiceAnalysisError(f"{self.backend_display_name}未能生成有效导演裁决：{last_error}") from last_error

    @classmethod
    def _compact_director_contexts(
        cls,
        passages: list[DirectorPassageEvidence],
        *,
        max_context_chars: int | None = None,
    ) -> tuple[list[str], list[int]]:
        contexts: list[str] = []
        passage_indexes: list[int] = []
        for passage in passages:
            context = passage.context.strip()
            if max_context_chars is not None and len(context) > max_context_chars:
                passage_offset = context.find(passage.text)
                if passage_offset >= 0:
                    start = max(0, passage_offset - round(max_context_chars * 0.7))
                    end = min(len(context), start + max_context_chars)
                    start = max(0, end - max_context_chars)
                    context = context[start:end]
                else:
                    context = context[-max_context_chars:]
            matched_index: int | None = None
            for index, shared_context in enumerate(contexts):
                if context in shared_context:
                    matched_index = index
                    break
                if shared_context in context:
                    contexts[index] = context
                    matched_index = index
                    break
            if matched_index is None and contexts:
                overlap = cls._suffix_prefix_overlap(contexts[-1], context)
                if overlap >= 24 and len(contexts[-1]) + len(context) - overlap <= 16_000:
                    contexts[-1] += context[overlap:]
                    matched_index = len(contexts) - 1
            if matched_index is None:
                contexts.append(context)
                matched_index = len(contexts) - 1
            passage_indexes.append(matched_index)
        return contexts, passage_indexes

    @staticmethod
    def _suffix_prefix_overlap(left: str, right: str) -> int:
        minimum_overlap = 24
        if len(left) < minimum_overlap or len(right) < minimum_overlap:
            return 0
        search_start = max(0, len(left) - len(right))
        marker = right[:minimum_overlap]
        candidate = left.find(marker, search_start)
        while candidate >= 0:
            overlap = len(left) - candidate
            if overlap <= len(right) and left[candidate:] == right[:overlap]:
                return overlap
            candidate = left.find(marker, candidate + 1)
        return 0

    @staticmethod
    def _local_compact_director_response_schema(
        passage_count: int,
        known_names: list[str],
    ) -> dict[str, object]:
        decision_properties: dict[str, object] = {
            "s": {"type": "string", "enum": [*known_names, "男路人", "女路人", "未知角色"]},
            "e": {
                "type": "string",
                "enum": ["natural", "tender", "joyful", "sad", "angry", "tense", "fearful", "surprised", "solemn", "sarcastic"],
            },
            "t": {
                "type": "string",
                "enum": ["natural", "soft", "firm", "restrained", "urgent", "cold", "bright", "low", "trembling", "playful"],
            },
        }
        return {
            "type": "object",
            "properties": {
                "d": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": decision_properties,
                        "required": list(decision_properties),
                        "additionalProperties": False,
                    },
                    "minItems": passage_count,
                    "maxItems": passage_count,
                }
            },
            "required": ["d"],
            "additionalProperties": False,
        }

    def analyze_text_structure(
        self,
        project_id: str,
        candidates: list[TextHeadingCandidate],
        total_characters: int,
    ) -> TextStructureDraft:
        if len(candidates) < 2:
            return TextStructureDraft(
                confidence=0.2,
                rationale="疑似标题不足两个，不能形成章节序列",
                backend=self.backend_mode,
                model=self.model,
            )
        payload = [
            {
                "id": candidate.candidate_id,
                "line": candidate.line_number,
                "offset": candidate.start_char,
                "title": candidate.title,
                "score": candidate.score,
            }
            for candidate in candidates
        ]
        messages = [
            {"role": "system", "content": load_long_form_runtime_prompt(self.workspace_root)},
            {
                "role": "user",
                "content": (
                    f"全文字符数={total_characters}。只判断候选行是否为章节边界，不补写标题。\n"
                    f"候选={json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
                ),
            },
        ]
        try:
            content = self._request_content(
                messages,
                text_structure_response_schema(candidates),
                keep_alive="2m",
                project_id=project_id,
            )
            selection = TextStructureSelection.model_validate_json(content)
        except (TypeError, ValueError, ValidationError, VoiceAnalysisError) as error:
            raise VoiceAnalysisError(f"本地模型未能确认长篇章节边界：{error}") from error
        allowed_ids = {candidate.candidate_id for candidate in candidates}
        return TextStructureDraft(
            heading_ids=list(dict.fromkeys(item for item in selection.heading_ids if item in allowed_ids)),
            confidence=selection.confidence,
            rationale=selection.rationale,
            backend=self.backend_mode,
            model=self.model,
        )

    def _request_content(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, object] | None = None,
        keep_alive: str = "0s",
        project_id: str | None = None,
    ) -> str:
        del project_id
        last_error: httpx.HTTPError | None = None
        for attempt in range(4):
            try:
                self._preload_model()
                response = self.client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": messages,
                        "format": response_schema or self._response_schema(),
                        "stream": False,
                        "think": False,
                        "keep_alive": keep_alive,
                        "options": {
                            "temperature": 0.15,
                            "top_p": 0.8,
                            "num_ctx": 8192,
                            "seed": 3407,
                        },
                    },
                )
                response.raise_for_status()
                return str(response.json()["message"]["content"])
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500:
                    raise VoiceAnalysisTransportError(f"Ollama rejected the voice analysis request: {error}") from error
                last_error = error
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = error
            if attempt < 3 and self.retry_delay_seconds > 0:
                time.sleep(self.retry_delay_seconds)
        raise VoiceAnalysisTransportError(f"Ollama did not become ready after a cold start: {last_error}") from last_error

    def release_model(self) -> None:
        try:
            self.client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": "",
                    "stream": False,
                    "keep_alive": "0s",
                },
                timeout=30,
            )
        except httpx.HTTPError:
            pass

    def _preload_model(self) -> None:
        last_error: httpx.HTTPError | None = None
        response_detail = ""
        for attempt in range(4):
            try:
                response = self.client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": "",
                        "stream": False,
                        "keep_alive": "2m",
                    },
                )
                response.raise_for_status()
                return
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500:
                    raise VoiceAnalysisTransportError(
                        f"Ollama rejected the voice analysis model preload: {error}"
                    ) from error
                last_error = error
                response_detail = error.response.text.strip()[:400]
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = error
            if attempt < 3 and self.retry_delay_seconds > 0:
                base_delay = max(2.0, self.retry_delay_seconds)
                time.sleep(min(base_delay * (2**attempt), 30.0))
        raise VoiceAnalysisTransportError(
            f"Ollama could not preload the voice analysis model: {last_error}"
            + (f" | {response_detail}" if response_detail else "")
        ) from last_error

    def _system_prompt(self) -> str:
        taxonomy = json.dumps(self.taxonomy, ensure_ascii=False, separators=(",", ":"))
        runtime_instructions = load_runtime_prompt(self.workspace_root)
        return f"{runtime_instructions}\n\n## 受控分类法\n\n{taxonomy}"

    @staticmethod
    def _evidence_prompt(evidence_pack: CharacterEvidencePack) -> str:
        priority = (
            "用户自定义属性是明确的表演方向；在不违背文本证据和生理可实现性的前提下优先吸收，"
            "并把它落实为可听见的声学属性与稳定表达习惯。\n"
            if evidence_pack.user_attributes
            else ""
        )
        screening = (
            "local_screening 是本地模型的初筛结果，只可作为待复核线索；必须结合原始证据独立裁决，"
            "发现冲突时以文本证据为准。\n"
            if evidence_pack.local_screening
            else ""
        )
        return priority + screening + "分析以下 Character Evidence Pack，并严格按 JSON Schema 返回：\n" + evidence_pack.model_dump_json(indent=2)

    @staticmethod
    def _reference_text_schema() -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "35-90 个汉字的自然中文中性参考句",
                    "minLength": 20,
                    "maxLength": 180,
                },
                "rationale": {
                    "type": "string",
                    "description": "简述该句的发音覆盖与停连设计",
                    "minLength": 1,
                    "maxLength": 240,
                },
            },
            "required": ["text", "rationale"],
            "additionalProperties": False,
        }

    @staticmethod
    def _candidate_screening_schema(
        candidate_handles: list[str],
        anchor_handles: list[str],
    ) -> dict[str, object]:
        target_handles = [*candidate_handles, *anchor_handles]
        reasons = [
            "named_identity",
            "stable_title",
            "plausible_low_frequency",
            "alias",
            "action_phrase",
            "connector_fragment",
            "generic_role",
            "addressee",
            "truncated_name",
            "non_character",
            "uncertain",
        ]
        decisions: dict[str, object] = {}
        for handle in candidate_handles:
            properties: dict[str, object] = {
                "a": {"type": "string", "enum": ["k", "r", "m"]},
                "t": {"type": ["string", "null"], "enum": [*target_handles, None]},
                "c": {"type": "number", "minimum": 0, "maximum": 1},
                "r": {"type": "string", "enum": reasons},
            }
            decisions[handle] = {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            }
        return {
            "type": "object",
            "properties": {
                "d": {
                    "type": "object",
                    "properties": decisions,
                    "required": candidate_handles,
                    "additionalProperties": False,
                }
            },
            "required": ["d"],
            "additionalProperties": False,
        }

    @staticmethod
    def _validate_candidate_screening(
        selection: CharacterCandidateScreeningSelection,
        candidate_handles: dict[str, CharacterCandidateScreeningInput],
        anchor_handles: dict[str, CharacterCandidateScreeningInput],
    ) -> list[CharacterCandidateScreeningDecision]:
        if set(selection.d) != set(candidate_handles):
            raise VoiceAnalysisError("角色候选粗筛结果键必须与输入候选完整一致")
        targets = {**candidate_handles, **anchor_handles}
        action_map: dict[CompactCharacterCandidateScreeningAction, CharacterCandidateScreeningAction] = {
            "k": "keep",
            "r": "reject",
            "m": "merge",
        }
        rationale_map: dict[CharacterCandidateScreeningReason, str] = {
            "named_identity": "名称形态与说话证据支持独立具名角色",
            "stable_title": "证据支持这是跨场景稳定专名身份",
            "plausible_low_frequency": "低频但具名形态与明确说话证据成立",
            "alias": "这是已保留规范角色的别名或称呼变体",
            "action_phrase": "动作或介词短语，不是独立说话人",
            "connector_fragment": "连接词、叙述词或错误截取片段",
            "generic_role": "普通身份类别，不是稳定具名角色",
            "addressee": "被称呼者或动作对象被误判为说话人",
            "truncated_name": "名称被截断或混入修饰语",
            "non_character": "证据不支持这是人物身份",
            "uncertain": "证据有限，保守保留供后续复核",
        }
        decisions: list[CharacterCandidateScreeningDecision] = []
        for handle, candidate in candidate_handles.items():
            compact = selection.d[handle]
            action = action_map[compact.a]
            if action == "merge":
                if compact.t not in targets or compact.t == handle:
                    raise VoiceAnalysisError(f"角色候选 {candidate.candidate_id} 的合并目标无效")
                target_compact = selection.d.get(compact.t or "")
                if target_compact is not None and target_compact.a != "k":
                    raise VoiceAnalysisError(f"角色候选 {candidate.candidate_id} 只能合并到保留角色")
                canonical_candidate_id = targets[compact.t].candidate_id
            else:
                if compact.t is not None:
                    raise VoiceAnalysisError(f"角色候选 {candidate.candidate_id} 仅在合并时允许指定目标")
                canonical_candidate_id = None
            decisions.append(
                CharacterCandidateScreeningDecision(
                    candidate_id=candidate.candidate_id,
                    action=action,
                    canonical_candidate_id=canonical_candidate_id,
                    confidence=compact.c,
                    rationale=rationale_map[compact.r],
                )
            )
        return decisions

    @staticmethod
    def _connection_test_schema() -> dict[str, object]:
        return {
            "type": "object",
            "properties": {
                "ok": {"type": "boolean"},
            },
            "required": ["ok"],
            "additionalProperties": False,
        }

    @staticmethod
    def _director_response_schema(
        passages: list[DirectorPassageEvidence],
        known_names: list[str],
    ) -> dict[str, object]:
        passage_ids = [passage.passage_id for passage in passages]
        decision_properties: dict[str, object] = {
            "passage_id": {"type": "string", "enum": passage_ids},
            "speaker": {"type": "string", "enum": [*known_names, "未知角色"]},
            "speaker_gender": {"type": "string", "enum": ["male", "female", "unknown"]},
            "speaker_kind": {"type": "string", "enum": ["named", "extra", "unknown"]},
            "emotion": {
                "type": "string",
                "enum": ["natural", "tender", "joyful", "sad", "angry", "tense", "fearful", "surprised", "solemn", "sarcastic"],
            },
            "emotion_intensity": {"type": "number", "minimum": 0, "maximum": 1},
            "tone": {
                "type": "string",
                "enum": ["natural", "soft", "firm", "restrained", "urgent", "cold", "bright", "low", "trembling", "playful"],
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string", "minLength": 1, "maxLength": 240},
        }
        return {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": decision_properties,
                        "required": list(decision_properties),
                        "additionalProperties": False,
                    },
                    "minItems": len(passages),
                    "maxItems": len(passages),
                }
            },
            "required": ["decisions"],
            "additionalProperties": False,
        }

    @staticmethod
    def _validate_director_decisions(
        selection: DirectorDecisionSelection,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> list[DirectorPassageDecision]:
        known_names = [character.display_name for character in characters]
        gender_by_name = {character.display_name: character.gender for character in characters}
        passage_by_id = {passage.passage_id: passage for passage in passages}
        decision_by_id = {decision.passage_id: decision for decision in selection.decisions}
        if len(decision_by_id) != len(selection.decisions) or set(decision_by_id) != set(passage_by_id):
            raise VoiceAnalysisError("导演裁决必须完整且每个 passage_id 只出现一次")
        allowed_speakers = {*known_names, "未知角色"}
        ordered: list[DirectorPassageDecision] = []
        for passage in passages:
            decision = decision_by_id[passage.passage_id]
            if decision.speaker not in allowed_speakers:
                raise VoiceAnalysisError(f"导演裁决包含未知说话人：{decision.speaker}")
            if passage.explicit_speaker in known_names and decision.speaker != passage.explicit_speaker:
                decision.speaker = passage.explicit_speaker
                decision.confidence = max(decision.confidence, 0.98)
                decision.rationale = "文本含明确说话归属，已覆盖模型裁决"
            if decision.speaker in known_names:
                decision.speaker_kind = "named"
                known_gender = gender_by_name.get(decision.speaker, "unknown")
                if known_gender != "unknown":
                    decision.speaker_gender = known_gender
            elif decision.speaker_kind == "named":
                decision.speaker_kind = "unknown"
            ordered.append(decision)
        return ordered

    def _response_schema(self) -> dict[str, object]:
        dimensions = self.taxonomy["dimensions"]
        if not isinstance(dimensions, dict):
            raise VoiceAnalysisError("音色分类法 dimensions 无效")

        def keys(group: str) -> list[str]:
            values = dimensions.get(group)
            if not isinstance(values, dict):
                raise VoiceAnalysisError(f"音色分类法缺少 {group}")
            return list(values)

        personality_tags = self.taxonomy["personality_tags"]
        age_ranges = self.taxonomy["age_ranges"]
        constraints = self.taxonomy["constraints"]
        if not all(isinstance(value, dict) for value in (personality_tags, age_ranges, constraints)):
            raise VoiceAnalysisError("音色分类法枚举无效")
        properties: dict[str, object] = {
            "gender": {"type": "string", "enum": ["male", "female", "unknown"]},
            "age_range": {"type": "string", "enum": list(age_ranges)},
            "personality_tags": {"type": "array", "items": {"type": "string", "enum": list(personality_tags)}, "minItems": 1, "maxItems": 4, "uniqueItems": True},
            "pitch": {"type": "string", "enum": keys("pitch")},
            "weight": {"type": "string", "enum": keys("weight")},
            "brightness": {"type": "string", "enum": keys("brightness")},
            "texture": {"type": "array", "items": {"type": "string", "enum": keys("texture")}, "minItems": 1, "maxItems": 2, "uniqueItems": True},
            "resonance": {"type": "string", "enum": keys("resonance")},
            "articulation": {"type": "string", "enum": keys("articulation")},
            "breath": {"type": "string", "enum": keys("breath")},
            "pace": {"type": "string", "enum": keys("pace")},
            "rhythm": {"type": "string", "enum": keys("rhythm")},
            "dynamics": {"type": "string", "enum": keys("dynamics")},
            "baseline": {"type": "string", "enum": keys("baseline")},
            "constraints": {"type": "array", "items": {"type": "string", "enum": list(constraints)}, "minItems": 1, "maxItems": 3, "uniqueItems": True},
            "signature_core": {
                "type": "string",
                "description": "连接声学落点与稳定表达行为的角色辨识核心，不得只复述人格标签",
                "minLength": 20,
                "maxLength": 100,
            },
            "signature_habits": {
                "type": "array",
                "description": "证据支持且可跨场景表演的稳定说话动作",
                "items": {"type": "string", "minLength": 6, "maxLength": 48},
                "minItems": 2,
                "maxItems": 3,
                "uniqueItems": True,
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string", "minLength": 1, "maxLength": 240},
        }
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    def _validate_selection(self, selection: VoiceAttributeSelection) -> None:
        properties = self._response_schema()["properties"]
        if not isinstance(properties, dict):
            raise VoiceAnalysisError("音色画像 Schema 无效")
        for field, value in selection.model_dump().items():
            specification = properties.get(field)
            if not isinstance(specification, dict):
                continue
            allowed = specification.get("enum")
            if allowed is not None and value not in allowed:
                raise VoiceAnalysisError(f"{field} 使用了分类法外的值 {value}")
            item_specification = specification.get("items")
            if isinstance(value, list) and isinstance(item_specification, dict):
                item_enum = item_specification.get("enum")
                invalid_items = [item for item in value if item_enum is not None and item not in item_enum]
                if invalid_items:
                    raise VoiceAnalysisError(f"{field} 包含分类法外的值：{invalid_items}")

    def _normalize_selection(self, selection: VoiceAttributeSelection) -> None:
        dimensions = self.taxonomy["dimensions"]
        if not isinstance(dimensions, dict):
            raise VoiceAnalysisError("音色分类法 dimensions 无效")

        def canonical(group: str, value: str, *, dimension: bool = False) -> str | None:
            source = dimensions.get(group) if dimension else self.taxonomy.get(group)
            if not isinstance(source, dict):
                return None
            if value in source:
                return value
            normalized = value.strip().casefold()
            for identifier, label in source.items():
                if isinstance(identifier, str) and isinstance(label, str):
                    if normalized in {identifier.casefold(), label.strip().casefold()}:
                        return identifier
            return None

        selection.age_range = canonical("age_ranges", selection.age_range) or selection.age_range
        for field in (
            "pitch",
            "weight",
            "brightness",
            "resonance",
            "articulation",
            "breath",
            "pace",
            "rhythm",
            "dynamics",
            "baseline",
        ):
            value = getattr(selection, field)
            setattr(selection, field, canonical(field, value, dimension=True) or value)

        def normalize_values(
            group: str,
            values: list[str],
            fallback: str,
            *,
            dimension: bool = False,
        ) -> list[str]:
            normalized_values: list[str] = []
            for value in values:
                normalized = canonical(group, value, dimension=dimension)
                if normalized is not None:
                    normalized_values.append(normalized)
                elif not self.allow_taxonomy_fallback:
                    normalized_values.append(value)
            unique_values = list(dict.fromkeys(normalized_values))
            if unique_values:
                return unique_values
            return [fallback] if self.allow_taxonomy_fallback else values

        original_count = len(selection.personality_tags) + len(selection.texture) + len(selection.constraints)
        selection.personality_tags = normalize_values("personality_tags", selection.personality_tags, "calm")
        selection.texture = normalize_values("texture", selection.texture, "clean", dimension=True)
        selection.constraints = normalize_values("constraints", selection.constraints, "preserve_natural_speech")
        normalized_count = len(selection.personality_tags) + len(selection.texture) + len(selection.constraints)
        if self.allow_taxonomy_fallback and normalized_count < original_count:
            selection.confidence = max(0, selection.confidence - min(0.2, 0.05 * (original_count - normalized_count)))

    def _compile_profile(self, selection: VoiceAttributeSelection) -> CharacterVoiceProfile:
        dimensions = self.taxonomy["dimensions"]
        if not isinstance(dimensions, dict):
            raise VoiceAnalysisError("音色分类法 dimensions 无效")

        def dimension_label(group: str, value: str) -> str:
            options = dimensions[group]
            if not isinstance(options, dict):
                raise VoiceAnalysisError(f"音色分类法缺少 {group}")
            return str(options[value])

        def labels(group: str, values: list[str]) -> list[str]:
            options = self.taxonomy[group]
            if not isinstance(options, dict):
                raise VoiceAnalysisError(f"音色分类法缺少 {group}")
            return [str(options[value]) for value in values]

        gender_label = labels("gender", [selection.gender])[0]
        age_label = labels("age_ranges", [selection.age_range])[0]
        personality_labels = labels("personality_tags", selection.personality_tags)
        texture_labels = [dimension_label("texture", value) for value in selection.texture]
        timbre_tags = [
            dimension_label("pitch", selection.pitch),
            dimension_label("weight", selection.weight),
            dimension_label("brightness", selection.brightness),
            *texture_labels,
            dimension_label("resonance", selection.resonance),
        ]
        delivery_tags = [
            dimension_label("articulation", selection.articulation),
            dimension_label("breath", selection.breath),
            dimension_label("pace", selection.pace),
            dimension_label("rhythm", selection.rhythm),
            dimension_label("dynamics", selection.dynamics),
            dimension_label("baseline", selection.baseline),
        ]
        constraint_labels = labels("constraints", selection.constraints)
        voice_prompt = (
            f"角色辨识核心：{selection.signature_core}；稳定表达习惯：{'；'.join(selection.signature_habits)}；"
            f"基础声学画像：{age_label}{gender_label}声线，音域为{timbre_tags[0]}，声线重量{timbre_tags[1]}、明暗{timbre_tags[2]}，"
            f"带有{'与'.join(texture_labels)}质感，以{timbre_tags[-1]}为主；{delivery_tags[0]}，{delivery_tags[1]}，"
            f"{delivery_tags[2]}、{delivery_tags[3]}，{delivery_tags[4]}；稳定基调为{delivery_tags[5]}，"
            f"人物表达呈现{'、'.join(personality_labels)}；中性参考要求{'、'.join(constraint_labels)}。"
        )
        return CharacterVoiceProfile(
            gender=selection.gender,
            age_range=selection.age_range,
            personality_tags=personality_labels,
            timbre_tags=timbre_tags,
            delivery_tags=delivery_tags,
            voice_constraints=constraint_labels,
            voice_prompt=voice_prompt,
            confidence=selection.confidence,
            rationale=selection.rationale,
            backend=self.backend_mode,
            model=self.model,
        )


class OpenAICompatibleVoiceAnalyzer(OllamaVoiceAnalyzer):
    backend_mode: AnalyzerMode = "cloud"
    backend_display_name = "云端模型"
    allow_taxonomy_fallback = True

    def __init__(
        self,
        workspace_root: Path,
        *,
        provider: VoiceAnalysisProvider,
        base_url: str,
        model: str,
        api_key: str,
        api_protocol: VoiceAnalysisApiProtocol = "chat_completions",
        client: httpx.Client | None = None,
        retry_delay_seconds: float = 0.5,
        runtime_logger: logging.Logger | None = None,
        analysis_event_handler: CloudAnalysisEventHandler | None = None,
        structured_mode_cache: dict[str, str] | None = None,
        structured_mode_lock: threading.RLock | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.provider = provider
        self.base_url = base_url.strip().rstrip("/")
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.api_protocol = api_protocol
        self.model_store = None
        self.taxonomy = load_taxonomy(self.workspace_root)
        load_runtime_prompt(self.workspace_root)
        load_director_runtime_prompt(self.workspace_root)
        load_character_screening_runtime_prompt(self.workspace_root)
        self.client = client or httpx.Client(timeout=httpx.Timeout(180, connect=10))
        self.retry_delay_seconds = retry_delay_seconds
        self.runtime_logger = runtime_logger or logging.getLogger("zw_voice_factory")
        self.analysis_event_handler = analysis_event_handler
        self.structured_mode_cache = structured_mode_cache if structured_mode_cache is not None else {}
        self.structured_mode_lock = structured_mode_lock or threading.RLock()
        self.max_attempts = CLOUD_API_MAX_ATTEMPTS
        self.fast_fail_transient = False
        if not self.base_url:
            raise VoiceAnalysisError("云端 API Base URL 不能为空")
        if not self.model:
            raise VoiceAnalysisError("云端模型名称不能为空")
        if not self.api_key:
            raise VoiceAnalysisError("云端 API Key 尚未配置")

    def status(self) -> VoiceAnalysisStatus:
        return VoiceAnalysisStatus(
            backend="cloud",
            available=True,
            model=self.model,
            detail=f"{self.provider_label()} · {self.protocol_label()} API 已配置",
            taxonomy_version=int(self.taxonomy["schema_version"]),
        )

    def list_models(self) -> VoiceAnalysisModelCatalog:
        call_id = uuid.uuid4().hex[:8]
        started_at = time.perf_counter()
        self._log_cloud_api_event(
            call_id,
            "INPUT",
            "model_catalog",
            {"method": "GET"},
        )
        try:
            response = self.client.get(
                self._models_url(),
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
            self._log_cloud_api_event(
                call_id,
                "OUTPUT",
                "model_catalog",
                payload,
                status_code=response.status_code,
                elapsed_seconds=time.perf_counter() - started_at,
            )
        except httpx.HTTPStatusError as error:
            self._log_cloud_api_event(
                call_id,
                "ERROR",
                "model_catalog",
                error.response.text,
                status_code=error.response.status_code,
                elapsed_seconds=time.perf_counter() - started_at,
            )
            if error.response.status_code in {401, 403}:
                raise VoiceAnalysisTransportError("云端 API 拒绝鉴权，请检查 API Key") from error
            detail = error.response.text.strip()[:400]
            raise VoiceAnalysisTransportError(
                f"读取可用模型失败（HTTP {error.response.status_code}）：{detail or '无错误详情'}"
            ) from error
        except (httpx.TimeoutException, httpx.NetworkError) as error:
            self._log_cloud_api_event(
                call_id,
                "ERROR",
                "model_catalog",
                str(error),
                elapsed_seconds=time.perf_counter() - started_at,
            )
            raise VoiceAnalysisTransportError(f"读取可用模型失败：{error}") from error
        except (TypeError, ValueError) as error:
            self._log_cloud_api_event(
                call_id,
                "ERROR",
                "model_catalog",
                str(error),
                elapsed_seconds=time.perf_counter() - started_at,
            )
            raise VoiceAnalysisTransportError(f"模型目录返回格式无效：{error}") from error

        raw_models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_models, list):
            raise VoiceAnalysisTransportError("模型目录返回格式无效：data 不是列表")
        models: list[VoiceAnalysisModelOption] = []
        for item in raw_models:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            supported = item.get("supported_endpoint_types")
            models.append(
                VoiceAnalysisModelOption(
                    id=item["id"].strip(),
                    owned_by=item.get("owned_by") if isinstance(item.get("owned_by"), str) else None,
                    supported_endpoint_types=[
                        value for value in supported or [] if isinstance(value, str)
                    ] if isinstance(supported, list) else [],
                )
            )
        models = sorted((model for model in models if model.id), key=lambda model: model.id.casefold())
        if not models:
            raise VoiceAnalysisTransportError("云端 API 未返回任何可用模型")
        return VoiceAnalysisModelCatalog(
            provider=self.provider,
            base_url=self.base_url,
            models=models,
        )

    def test_connection(self) -> None:
        content = self._request_content(
            [
                {
                    "role": "system",
                    "content": "仅测试连接，不分析项目文本。只返回指定 JSON。",
                },
                {"role": "user", "content": "返回 ok=true。"},
            ],
            self._connection_test_schema(),
        )
        try:
            payload = json.loads(content)
        except (TypeError, ValueError) as error:
            raise VoiceAnalysisError("云端连接测试返回了无效 JSON") from error
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise VoiceAnalysisError("云端连接测试返回内容不符合探针协议")

    def analyze_text_structure(
        self,
        project_id: str,
        candidates: list[TextHeadingCandidate],
        total_characters: int,
    ) -> TextStructureDraft:
        if len(candidates) < 2:
            return TextStructureDraft(
                confidence=0.2,
                rationale="疑似标题不足两个，不能形成章节序列",
                backend=self.backend_mode,
                model=self.model,
            )
        payload = [
            {
                "i": candidate.candidate_id,
                "l": candidate.line_number,
                "o": candidate.start_char,
                "t": candidate.title,
                "s": candidate.score,
            }
            for candidate in candidates
        ]
        messages = [
            {"role": "system", "content": load_long_form_runtime_prompt(self.workspace_root)},
            {
                "role": "user",
                "content": (
                    "使用紧凑候选协议确认章节边界。i=候选ID,l=行号,o=字符偏移,t=候选标题,s=规则分。"
                    "只返回真实章节标题的 ID，不得创建新 ID。\n"
                    f"N={total_characters}\nH={json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
                ),
            },
        ]
        try:
            content = self._request_content(
                messages,
                text_structure_response_schema(candidates),
                project_id=project_id,
            )
            selection = TextStructureSelection.model_validate_json(content)
        except VoiceAnalysisTransportError:
            raise
        except (TypeError, ValueError, ValidationError, VoiceAnalysisError) as error:
            raise VoiceAnalysisError(f"云端模型未能确认长篇章节边界：{error}") from error
        allowed_ids = {candidate.candidate_id for candidate in candidates}
        return TextStructureDraft(
            heading_ids=list(dict.fromkeys(item for item in selection.heading_ids if item in allowed_ids)),
            confidence=selection.confidence,
            rationale=selection.rationale,
            backend=self.backend_mode,
            model=self.model,
        )

    def analyze_director(
        self,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> DirectorAnalysisDraft:
        if not passages:
            return DirectorAnalysisDraft(decisions=[], backend=self.backend_mode, model=self.model)
        known_names = [character.display_name for character in characters]
        character_payload = [
            {"n": character.display_name, "a": character.aliases, "g": character.gender}
            for character in characters
        ]
        contexts, passage_context_indexes = self._compact_director_contexts(passages)
        passage_payload = [
            {
                "i": passage.passage_id,
                "x": passage.text,
                "c": passage_context_indexes[index],
                "s": passage.explicit_speaker,
            }
            for index, passage in enumerate(passages)
        ]
        messages = [
            {"role": "system", "content": load_director_runtime_prompt(self.workspace_root)},
            {
                "role": "user",
                "content": (
                    "使用紧凑协议裁决每条对白。角色字段：n=姓名,a=别名,g=性别；"
                    "上下文字段：C=共享上下文数组；对白字段：i=编号,x=文本,c=C数组索引,s=明确说话人。"
                    "输出字段：d=裁决数组，i=编号,s=说话人,g=说话人性别,k=说话人类型，"
                    "e=情绪,v=情绪强度,t=语气,c=置信度。k 只能是 named、extra、unknown。"
                    "不得输出解释或额外字段。\n"
                    f"R={json.dumps(character_payload, ensure_ascii=False, separators=(',', ':'))}\n"
                    f"C={json.dumps(contexts, ensure_ascii=False, separators=(',', ':'))}\n"
                    f"P={json.dumps(passage_payload, ensure_ascii=False, separators=(',', ':'))}"
                ),
            },
        ]
        schema = self._compact_director_response_schema(passages, known_names)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                content = self._request_content(
                    messages,
                    schema,
                    project_id=passages[0].project_id,
                )
                compact = CompactDirectorDecisionSelection.model_validate_json(content)
                selection = DirectorDecisionSelection(
                    decisions=[
                        DirectorPassageDecision(
                            passage_id=decision.i,
                            speaker=decision.s,
                            speaker_gender=decision.g,
                            speaker_kind=decision.k,
                            emotion=decision.e,
                            emotion_intensity=decision.v,
                            tone=decision.t,
                            confidence=decision.c,
                            rationale="云端紧凑协议根据局部上下文完成裁决",
                        )
                        for decision in compact.d
                    ]
                )
                decisions = self._validate_director_decisions(selection, passages, characters)
                return DirectorAnalysisDraft(decisions=decisions, backend=self.backend_mode, model=self.model)
            except VoiceAnalysisTransportError:
                raise
            except (TypeError, ValueError, ValidationError, VoiceAnalysisError) as error:
                last_error = error
                if attempt == 0:
                    messages.append(
                        {
                            "role": "user",
                            "content": f"上一次紧凑 JSON 未通过校验：{error}。只返回修正后的紧凑 JSON。",
                        }
                    )
        raise VoiceAnalysisError(f"{self.backend_display_name}未能生成有效导演裁决：{last_error}") from last_error

    @classmethod
    def _compact_director_contexts(
        cls,
        passages: list[DirectorPassageEvidence],
    ) -> tuple[list[str], list[int]]:
        contexts: list[str] = []
        passage_indexes: list[int] = []
        for passage in passages:
            context = passage.context.strip()
            matched_index: int | None = None
            for index, shared_context in enumerate(contexts):
                if context in shared_context:
                    matched_index = index
                    break
                if shared_context in context:
                    contexts[index] = context
                    matched_index = index
                    break
            if matched_index is None and contexts:
                overlap = cls._suffix_prefix_overlap(contexts[-1], context)
                if overlap >= 24 and len(contexts[-1]) + len(context) - overlap <= 16_000:
                    contexts[-1] += context[overlap:]
                    matched_index = len(contexts) - 1
            if matched_index is None:
                contexts.append(context)
                matched_index = len(contexts) - 1
            passage_indexes.append(matched_index)
        return contexts, passage_indexes

    @staticmethod
    def _suffix_prefix_overlap(left: str, right: str) -> int:
        minimum_overlap = 24
        if len(left) < minimum_overlap or len(right) < minimum_overlap:
            return 0
        search_start = max(0, len(left) - len(right))
        marker = right[:minimum_overlap]
        candidate = left.find(marker, search_start)
        while candidate >= 0:
            overlap = len(left) - candidate
            if overlap <= len(right) and left[candidate:] == right[:overlap]:
                return overlap
            candidate = left.find(marker, candidate + 1)
        return 0

    @staticmethod
    def _compact_director_response_schema(
        passages: list[DirectorPassageEvidence],
        known_names: list[str],
    ) -> dict[str, object]:
        decision_properties: dict[str, object] = {
            "i": {"type": "string", "enum": [passage.passage_id for passage in passages]},
            "s": {"type": "string", "enum": [*known_names, "未知角色"]},
            "g": {"type": "string", "enum": ["male", "female", "unknown"]},
            "k": {"type": "string", "enum": ["named", "extra", "unknown"]},
            "e": {
                "type": "string",
                "enum": ["natural", "tender", "joyful", "sad", "angry", "tense", "fearful", "surprised", "solemn", "sarcastic"],
            },
            "v": {"type": "number", "minimum": 0, "maximum": 1},
            "t": {
                "type": "string",
                "enum": ["natural", "soft", "firm", "restrained", "urgent", "cold", "bright", "low", "trembling", "playful"],
            },
            "c": {"type": "number", "minimum": 0, "maximum": 1},
        }
        return {
            "type": "object",
            "properties": {
                "d": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": decision_properties,
                        "required": list(decision_properties),
                        "additionalProperties": False,
                    },
                    "minItems": len(passages),
                    "maxItems": len(passages),
                }
            },
            "required": ["d"],
            "additionalProperties": False,
        }

    def _request_content(
        self,
        messages: list[dict[str, str]],
        response_schema: dict[str, object] | None = None,
        keep_alive: str = "0s",
        project_id: str | None = None,
    ) -> str:
        del keep_alive
        schema = response_schema or self._response_schema()
        operation = self._cloud_operation(schema)
        call_id = uuid.uuid4().hex[:8]
        capability_key = f"{self.base_url}|{self.api_protocol}|{self.model}"
        with self.structured_mode_lock:
            structured_mode = self.structured_mode_cache.get(capability_key, "json_schema")
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            if structured_mode == "json_schema":
                response_format: dict[str, object] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "zw_voice_analysis",
                        "strict": True,
                        "schema": schema,
                    },
                }
            else:
                response_format = {"type": "json_object"}
            if self.api_protocol == "responses":
                request_payload = self._responses_payload(messages, response_format)
                request_payload["max_output_tokens"] = self._max_output_tokens(schema)
            else:
                request_payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.15,
                    "top_p": 0.8,
                    "stream": False,
                    "response_format": response_format,
                }
                request_payload["max_tokens"] = self._max_output_tokens(schema)
            started_at = time.perf_counter()
            self._log_cloud_api_event(
                call_id,
                "INPUT",
                operation,
                request_payload,
                attempt=attempt + 1,
                structured_mode=structured_mode,
                project_id=project_id,
            )
            try:
                if self.api_protocol == "responses":
                    response = self.client.post(
                        self._responses_url(),
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=request_payload,
                    )
                else:
                    response = self.client.post(
                        self._chat_completions_url(),
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=request_payload,
                    )
                response.raise_for_status()
                if self.api_protocol == "responses":
                    content = self._extract_responses_content(response)
                else:
                    content = self._extract_chat_content(response)
                self._log_cloud_api_event(
                    call_id,
                    "OUTPUT",
                    operation,
                    content,
                    attempt=attempt + 1,
                    structured_mode=structured_mode,
                    status_code=response.status_code,
                    elapsed_seconds=time.perf_counter() - started_at,
                    project_id=project_id,
                )
                return content
            except httpx.HTTPStatusError as error:
                status_code = error.response.status_code
                self._log_cloud_api_event(
                    call_id,
                    "ERROR",
                    operation,
                    error.response.text,
                    attempt=attempt + 1,
                    structured_mode=structured_mode,
                    status_code=status_code,
                    elapsed_seconds=time.perf_counter() - started_at,
                    project_id=project_id,
                )
                if status_code in {401, 403}:
                    raise VoiceAnalysisTransportError("云端 API 拒绝鉴权，请检查 API Key") from error
                if status_code in {400, 422} and structured_mode == "json_schema":
                    structured_mode = "json_object"
                    with self.structured_mode_lock:
                        self.structured_mode_cache[capability_key] = structured_mode
                    continue
                if status_code != 429 and status_code < 500:
                    detail = error.response.text.strip()[:400]
                    raise VoiceAnalysisTransportError(
                        f"云端 API 拒绝分析请求（HTTP {status_code}）：{detail or '无错误详情'}"
                    ) from error
                detail = error.response.text.strip()[:400]
                last_error = VoiceAnalysisTransportError(
                    f"HTTP {status_code}：{detail or '无错误详情'}"
                )
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = error
                self._log_cloud_api_event(
                    call_id,
                    "ERROR",
                    operation,
                    str(error),
                    attempt=attempt + 1,
                    structured_mode=structured_mode,
                    elapsed_seconds=time.perf_counter() - started_at,
                    project_id=project_id,
                )
            except (KeyError, TypeError, ValueError) as error:
                self._log_cloud_api_event(
                    call_id,
                    "ERROR",
                    operation,
                    str(error),
                    attempt=attempt + 1,
                    structured_mode=structured_mode,
                    status_code=response.status_code,
                    elapsed_seconds=time.perf_counter() - started_at,
                    project_id=project_id,
                )
                raise VoiceAnalysisTransportError(f"云端 API 返回格式无效：{error}") from error
            if self.fast_fail_transient and last_error is not None:
                break
            if attempt + 1 < self.max_attempts and self.retry_delay_seconds > 0:
                time.sleep(self.retry_delay_seconds * (2**attempt))
        raise VoiceAnalysisTransportError(f"云端 API 多次请求失败：{last_error}") from last_error

    @staticmethod
    def _max_output_tokens(schema: dict[str, object]) -> int:
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return 1_200
        if "ok" in properties:
            return 16
        candidate_decisions = properties.get("candidate_decisions")
        if isinstance(candidate_decisions, dict):
            item_count = candidate_decisions.get("maxItems", 1)
            if isinstance(item_count, int):
                return max(512, min(8_192, item_count * 128))
        decisions = properties.get("d")
        if isinstance(decisions, dict):
            item_count = decisions.get("maxItems", 1)
            if isinstance(item_count, int):
                return max(512, min(8_192, item_count * 96))
        if "text" in properties:
            return 384
        return 1_200

    @staticmethod
    def _cloud_operation(schema: dict[str, object]) -> str:
        properties = schema.get("properties")
        if isinstance(properties, dict):
            if "ok" in properties:
                return "connection_test"
            if "heading_ids" in properties:
                return "text_structure"
            if "candidate_decisions" in properties:
                return "candidate_screening"
            if "d" in properties:
                return "director_analysis"
            if "text" in properties:
                return "reference_text"
        return "character_profile"

    def _log_cloud_api_event(
        self,
        call_id: str,
        direction: str,
        operation: str,
        payload: object,
        *,
        attempt: int = 1,
        structured_mode: str = "-",
        status_code: int | None = None,
        elapsed_seconds: float | None = None,
        project_id: str | None = None,
    ) -> None:
        if isinstance(payload, str):
            serialized = payload
        else:
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if self.api_key:
            serialized = serialized.replace(self.api_key, "[REDACTED]")
        total_chars = len(serialized)
        preview = serialized[:CLOUD_API_LOG_PREVIEW_CHARS]
        if total_chars > CLOUD_API_LOG_PREVIEW_CHARS:
            preview += f"\n... [truncated {total_chars - CLOUD_API_LOG_PREVIEW_CHARS} chars]"
        details = [
            f"operation={operation}",
            f"provider={self.provider}",
            f"protocol={self.api_protocol}",
            f"model={self.model}",
            f"attempt={attempt}",
            f"mode={structured_mode}",
            f"chars={total_chars}",
        ]
        if status_code is not None:
            details.append(f"status={status_code}")
        if elapsed_seconds is not None:
            details.append(f"elapsed={elapsed_seconds:.2f}s")
        if project_id is not None and self.analysis_event_handler is not None:
            try:
                self.analysis_event_handler(
                    CloudAnalysisEvent(
                        project_id=project_id,
                        call_id=call_id,
                        direction=direction,
                        operation=operation,
                        provider=self.provider,
                        protocol=self.api_protocol,
                        model=self.model,
                        attempt=attempt,
                        structured_mode=structured_mode,
                        total_chars=total_chars,
                        preview=preview,
                        status_code=status_code,
                        elapsed_seconds=elapsed_seconds,
                    )
                )
            except Exception as error:
                self.runtime_logger.warning("analysis event handler failed: %s", error)
        self.runtime_logger.info(
            "[CLOUD API %s %s] %s\n%s",
            call_id,
            direction,
            " ".join(details),
            preview,
        )

    def release_model(self) -> None:
        return None

    def provider_label(self) -> str:
        return {
            "custom": "自定义服务",
            "qwen": "通义千问",
            "kimi": "Kimi",
            "doubao": "豆包",
            "gemini": "Gemini",
        }[self.provider]

    def protocol_label(self) -> str:
        return "Responses" if self.api_protocol == "responses" else "Chat Completions"

    def _models_url(self) -> str:
        if self.base_url.endswith("/models"):
            return self.base_url
        return f"{self.base_url}/models"

    def _chat_completions_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _responses_url(self) -> str:
        if self.base_url.endswith("/responses"):
            return self.base_url
        return f"{self.base_url}/responses"

    def _responses_payload(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, object],
    ) -> dict[str, object]:
        text_format = response_format
        if response_format.get("type") == "json_schema":
            schema = response_format.get("json_schema")
            if isinstance(schema, dict):
                text_format = {"type": "json_schema", **schema}
        return {
            "model": self.model,
            "input": [
                {
                    "role": message["role"],
                    "content": [{"type": "input_text", "text": message["content"]}],
                }
                for message in messages
            ],
            "stream": True,
            "text": {"format": text_format},
        }

    @staticmethod
    def _extract_chat_content(response: httpx.Response) -> str:
        content = response.json()["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        if not isinstance(content, str) or not content.strip():
            raise ValueError("choices[0].message.content 为空")
        cleaned = content.strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned = cleaned[3:-3].strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        return cleaned

    @classmethod
    def _extract_responses_content(cls, response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" not in content_type and not response.text.lstrip().startswith(("event:", "data:")):
            return cls._clean_content(cls._responses_output_text(response.json()))

        chunks: list[str] = []
        completed_response: dict[str, object] | None = None
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            event = json.loads(data)
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "response.output_text.delta" and isinstance(event.get("delta"), str):
                chunks.append(event["delta"])
            elif event_type == "response.completed" and isinstance(event.get("response"), dict):
                completed_response = event["response"]
            elif event_type in {"response.failed", "response.error", "error"}:
                raise ValueError(event.get("error") or event)
        if chunks:
            return cls._clean_content("".join(chunks))
        if completed_response is not None:
            return cls._clean_content(cls._responses_output_text(completed_response))
        raise ValueError("Responses SSE 未包含输出文本")

    @staticmethod
    def _responses_output_text(payload: object) -> str:
        if not isinstance(payload, dict):
            raise ValueError("Responses 响应不是对象")
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct
        chunks: list[str] = []
        output = payload.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict) or not isinstance(item.get("content"), list):
                    continue
                for content in item["content"]:
                    if isinstance(content, dict) and content.get("type") == "output_text":
                        text = content.get("text")
                        if isinstance(text, str):
                            chunks.append(text)
        if not chunks:
            raise ValueError("Responses 响应未包含 output_text")
        return "".join(chunks)

    @staticmethod
    def _clean_content(content: str) -> str:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("模型输出为空")
        cleaned = content.strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned = cleaned[3:-3].strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        return cleaned


class FailoverVoiceAnalyzer:
    backend_mode: AnalyzerMode = "cloud"

    def __init__(
        self,
        profiles: list[VoiceAnalysisCloudProfile],
        analyzer_factory: object,
        runtime_logger: logging.Logger,
        profile_health: dict[str, tuple[str, str | None]],
        profile_cooldowns: dict[str, float],
        profile_probe_locks: dict[str, threading.Lock],
        state_lock: threading.RLock,
        *,
        failover_enabled: bool,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.profiles = [profile for profile in profiles if profile.enabled]
        self.analyzer_factory = analyzer_factory
        self.runtime_logger = runtime_logger
        self.profile_health = profile_health
        self.profile_cooldowns = profile_cooldowns
        self.profile_probe_locks = profile_probe_locks
        self.state_lock = state_lock
        self.failover_enabled = failover_enabled
        self.cooldown_seconds = cooldown_seconds
        if not self.profiles:
            raise VoiceAnalysisError("云端 API 队列中没有已启用的端点")

    def status(self) -> VoiceAnalysisStatus:
        queue = " -> ".join(
            f"P{index + 1} {profile.name} ({profile.model})"
            for index, profile in enumerate(self.profiles)
        )
        return VoiceAnalysisStatus(
            backend="cloud",
            available=True,
            model=self.profiles[0].model,
            detail=f"故障转移队列：{queue}" if self.failover_enabled else f"云端端点：{queue}",
            taxonomy_version=1,
        )

    def test_connection(self) -> None:
        self._call("connection_test", "test_connection")

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        return self._call("character_profile", "analyze", evidence_pack)

    def screen_character_candidates(
        self,
        project_id: str,
        candidates: list[CharacterCandidateScreeningInput],
        canonical_anchors: list[CharacterCandidateScreeningInput],
    ) -> CharacterCandidateScreeningDraft:
        return self._call(
            "candidate_screening",
            "screen_character_candidates",
            project_id,
            candidates,
            canonical_anchors,
        )

    def generate_reference_text(
        self,
        evidence_pack: CharacterEvidencePack,
        voice_prompt: str,
    ) -> ReferenceTextDraft:
        return self._call("reference_text", "generate_reference_text", evidence_pack, voice_prompt)

    def analyze_director(
        self,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> DirectorAnalysisDraft:
        return self._call("director_analysis", "analyze_director", passages, characters)

    def analyze_text_structure(
        self,
        project_id: str,
        candidates: list[TextHeadingCandidate],
        total_characters: int,
    ) -> TextStructureDraft:
        return self._call(
            "text_structure",
            "analyze_text_structure",
            project_id,
            candidates,
            total_characters,
        )

    def release_model(self) -> None:
        return None

    def _call(self, operation: str, method_name: str, *args: object) -> object:
        with self.state_lock:
            candidates = self._candidate_profiles()
        errors: list[str] = []
        for candidate_index, profile in enumerate(candidates):
            queue_index = self.profiles.index(profile) + 1
            probe_lock: threading.Lock | None = None
            with self.state_lock:
                cooldown_until = self.profile_cooldowns.get(profile.profile_id, 0)
                health = self.profile_health.get(profile.profile_id, ("unknown", None))[0]
                if cooldown_until > time.monotonic():
                    continue
                if health != "healthy":
                    probe_lock = self.profile_probe_locks.setdefault(profile.profile_id, threading.Lock())
            if probe_lock is not None:
                probe_lock.acquire()
                with self.state_lock:
                    cooldown_until = self.profile_cooldowns.get(profile.profile_id, 0)
                    health = self.profile_health.get(profile.profile_id, ("unknown", None))[0]
                if cooldown_until > time.monotonic():
                    probe_lock.release()
                    continue
                if health == "healthy":
                    probe_lock.release()
                    probe_lock = None
            self.runtime_logger.info(
                "[CLOUD ROUTE] operation=%s selected=P%d profile=%s model=%s",
                operation,
                queue_index,
                profile.name,
                profile.model,
            )
            analyzer = self.analyzer_factory(profile)
            if self.failover_enabled and len(candidates) > 1 and hasattr(analyzer, "fast_fail_transient"):
                analyzer.fast_fail_transient = True
            try:
                result = getattr(analyzer, method_name)(*args)
            except VoiceAnalysisError as error:
                message = str(error)
                errors.append(f"P{queue_index} {profile.name}: {message}")
                with self.state_lock:
                    self.profile_health[profile.profile_id] = ("cooldown", message)
                    self.profile_cooldowns[profile.profile_id] = time.monotonic() + self.cooldown_seconds
                if probe_lock is not None:
                    probe_lock.release()
                has_next = candidate_index + 1 < len(candidates)
                self.runtime_logger.info(
                    "[CLOUD FAILOVER] operation=%s failed=P%d profile=%s cooldown=%.0fs next=%s error=%s",
                    operation,
                    queue_index,
                    profile.name,
                    self.cooldown_seconds,
                    "yes" if has_next else "no",
                    message,
                )
                if not has_next:
                    raise VoiceAnalysisTransportError(
                        "云端 API 队列全部失败：" + " | ".join(errors)
                    ) from error
                continue
            except Exception:
                if probe_lock is not None:
                    probe_lock.release()
                raise
            with self.state_lock:
                self.profile_health[profile.profile_id] = ("healthy", None)
                self.profile_cooldowns.pop(profile.profile_id, None)
            if probe_lock is not None:
                probe_lock.release()
            if candidate_index:
                self.runtime_logger.info(
                    "[CLOUD FAILOVER] operation=%s recovered=P%d profile=%s",
                    operation,
                    queue_index,
                    profile.name,
                )
            return result
        raise VoiceAnalysisTransportError("云端 API 队列中没有可用端点")

    def _candidate_profiles(self) -> list[VoiceAnalysisCloudProfile]:
        if not self.failover_enabled:
            return self.profiles[:1]
        now = time.monotonic()
        ready = [
            profile
            for profile in self.profiles
            if self.profile_cooldowns.get(profile.profile_id, 0) <= now
        ]
        return ready or list(self.profiles)


class HybridVoiceAnalyzer:
    backend_mode: AnalyzerMode = "hybrid"

    def __init__(
        self,
        local_analyzer: VoiceAnalyzer,
        cloud_analyzer: VoiceAnalyzer,
        runtime_logger: logging.Logger,
    ) -> None:
        self.local_analyzer = local_analyzer
        self.cloud_analyzer = cloud_analyzer
        self.runtime_logger = runtime_logger

    def status(self) -> VoiceAnalysisStatus:
        local_status = self.local_analyzer.status()
        cloud_status = self.cloud_analyzer.status()
        return VoiceAnalysisStatus(
            backend="hybrid",
            available=local_status.available and cloud_status.available,
            model=f"{local_status.model or 'local'} -> {cloud_status.model or 'cloud'}",
            detail=f"本地初筛：{local_status.detail}；云端精推：{cloud_status.detail}",
            taxonomy_version=max(local_status.taxonomy_version, cloud_status.taxonomy_version),
            model_store=local_status.model_store,
        )

    def test_connection(self) -> None:
        local_status = self.local_analyzer.status()
        if not local_status.available:
            raise VoiceAnalysisError(local_status.detail)
        test_connection = getattr(self.cloud_analyzer, "test_connection", None)
        if callable(test_connection):
            test_connection()

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        if evidence_pack.local_screening:
            profile = self.cloud_analyzer.analyze(evidence_pack)
            local_model = getattr(self.local_analyzer, "model", None)
            return profile.model_copy(
                update={"backend": "hybrid", "model": self._model_label(local_model, profile.model)}
            )
        local_profile = self.local_analyzer.analyze(evidence_pack)
        local_summary = (
            "本地初筛结果，仅作为云端复核依据，不得机械照抄："
            f"性别={local_profile.gender}；年龄={local_profile.age_range}；"
            f"人格={','.join(local_profile.personality_tags)}；音色={','.join(local_profile.timbre_tags)}；"
            f"表达={','.join(local_profile.delivery_tags)}；描述={local_profile.voice_prompt}"
        )
        cloud_input = evidence_pack.model_copy(update={"local_screening": local_summary[:1_500]})
        profile = self.cloud_analyzer.analyze(cloud_input)
        return profile.model_copy(
            update={"backend": "hybrid", "model": self._model_label(local_profile.model, profile.model)}
        )

    def screen_character_candidates(
        self,
        project_id: str,
        candidates: list[CharacterCandidateScreeningInput],
        canonical_anchors: list[CharacterCandidateScreeningInput],
    ) -> CharacterCandidateScreeningDraft:
        draft = self.local_analyzer.screen_character_candidates(project_id, candidates, canonical_anchors)
        return draft.model_copy(update={"backend": "local"})

    def generate_reference_text(
        self,
        evidence_pack: CharacterEvidencePack,
        voice_prompt: str,
    ) -> ReferenceTextDraft:
        local_draft = self.local_analyzer.generate_reference_text(evidence_pack, voice_prompt)
        cloud_prompt = (
            f"{voice_prompt.strip()}\n"
            f"本地初筛参考句（仅供覆盖与停连复核，不要求照抄）：{local_draft.text}"
        )
        draft = self.cloud_analyzer.generate_reference_text(evidence_pack, cloud_prompt)
        return draft.model_copy(
            update={"backend": "hybrid", "model": self._model_label(local_draft.model, draft.model)}
        )

    def analyze_director(
        self,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> DirectorAnalysisDraft:
        local_draft = self.local_analyzer.analyze_director(passages, characters)
        local_by_id = {decision.passage_id: decision for decision in local_draft.decisions}
        enriched: list[DirectorPassageEvidence] = []
        for passage in passages:
            local_decision = local_by_id.get(passage.passage_id)
            if local_decision is None:
                enriched.append(passage)
                continue
            hint = (
                f"[本地初筛，仅供复核] 说话人={local_decision.speaker}，类型={local_decision.speaker_kind}，"
                f"性别={local_decision.speaker_gender}，情绪={local_decision.emotion}，"
                f"置信度={local_decision.confidence:.2f}。"
            )
            enriched.append(passage.model_copy(update={"context": f"{hint}\n{passage.context}"[:2_000]}))
        draft = self.cloud_analyzer.analyze_director(enriched, characters)
        return draft.model_copy(
            update={"backend": "hybrid", "model": self._model_label(local_draft.model, draft.model)}
        )

    def analyze_text_structure(
        self,
        project_id: str,
        candidates: list[TextHeadingCandidate],
        total_characters: int,
    ) -> TextStructureDraft:
        local_draft = self.local_analyzer.analyze_text_structure(project_id, candidates, total_characters)
        local_ids = set(local_draft.heading_ids)
        enriched = [
            candidate.model_copy(update={"score": min(10, candidate.score + (2 if candidate.candidate_id in local_ids else 0))})
            for candidate in candidates
        ]
        draft = self.cloud_analyzer.analyze_text_structure(project_id, enriched, total_characters)
        return draft.model_copy(
            update={"backend": "hybrid", "model": self._model_label(local_draft.model, draft.model)}
        )

    def release_model(self) -> None:
        release_local = getattr(self.local_analyzer, "release_model", None)
        if callable(release_local):
            release_local()

    @staticmethod
    def _model_label(local_model: str | None, cloud_model: str | None) -> str:
        return f"{local_model or 'local'} -> {cloud_model or 'cloud'}"


class ConfigurableVoiceAnalyzer:
    def __init__(
        self,
        workspace_root: Path,
        *,
        default_backend: AnalyzerMode = "rules",
        local_analyzer: VoiceAnalyzer | None = None,
        cloud_client: httpx.Client | None = None,
        runtime_logger: logging.Logger | None = None,
        analysis_event_handler: CloudAnalysisEventHandler | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.settings_path = self.workspace_root / "outputs" / "settings" / "voice_analysis.json"
        self.default_backend = default_backend
        self.local_analyzer = local_analyzer or OllamaVoiceAnalyzer(self.workspace_root)
        self.rules_analyzer = RuleBasedVoiceAnalyzer(self.workspace_root)
        self.cloud_client = cloud_client
        self.runtime_logger = runtime_logger or logging.getLogger("zw_voice_factory")
        self.analysis_event_handler = analysis_event_handler
        self.profile_health: dict[str, tuple[str, str | None]] = {}
        self.profile_cooldowns: dict[str, float] = {}
        self.profile_probe_locks: dict[str, threading.Lock] = {}
        self.cloud_state_lock = threading.RLock()
        self.structured_mode_cache: dict[str, str] = {}

    def set_analysis_event_handler(
        self,
        handler: CloudAnalysisEventHandler | None,
    ) -> None:
        self.analysis_event_handler = handler

    def configuration(self) -> VoiceAnalysisConfigurationView:
        return self._configuration_view(self._load_configuration())

    def update_configuration(
        self,
        update: VoiceAnalysisConfigurationUpdate,
    ) -> VoiceAnalysisConfigurationView:
        current = self._load_configuration()
        if update.profiles is not None:
            profiles = self._updated_profiles(update.profiles, current.profiles)
        else:
            profiles = list(current.profiles)
            if profiles or any(
                value is not None
                for value in (update.provider, update.base_url, update.model, update.api_protocol, update.api_key)
            ) or update.clear_api_key:
                existing = profiles[0] if profiles else VoiceAnalysisCloudProfile()
                provider = update.provider or existing.provider
                provider_changed = provider != existing.provider
                base_url = existing.base_url if update.base_url is None else update.base_url.strip()
                if provider_changed and update.base_url is None:
                    base_url = CLOUD_PROVIDER_BASE_URLS[provider]
                api_protocol = update.api_protocol or (
                    CLOUD_PROVIDER_PROTOCOLS[provider] if provider_changed else existing.api_protocol
                )
                api_key = existing.api_key
                if update.clear_api_key:
                    api_key = ""
                elif update.api_key is not None and update.api_key.strip():
                    api_key = update.api_key.strip()
                primary = existing.model_copy(
                    update={
                        "provider": provider,
                        "base_url": base_url,
                        "model": existing.model if update.model is None else update.model.strip(),
                        "api_protocol": api_protocol,
                        "api_key": api_key,
                    }
                )
                profiles = [primary, *profiles[1:]]
        self._validate_profiles(profiles, update.backend)
        configuration = VoiceAnalysisConfiguration(
            backend=update.backend,
            failover_enabled=current.failover_enabled if update.failover_enabled is None else update.failover_enabled,
            cloud_parallelism=current.cloud_parallelism if update.cloud_parallelism is None else update.cloud_parallelism,
            cloud_director_batch_size=current.cloud_director_batch_size if update.cloud_director_batch_size is None else update.cloud_director_batch_size,
            profiles=profiles,
        )
        active_ids = {profile.profile_id for profile in profiles}
        with self.cloud_state_lock:
            for profile_id in list(self.profile_health):
                if profile_id not in active_ids:
                    del self.profile_health[profile_id]
            for profile_id in list(self.profile_cooldowns):
                if profile_id not in active_ids:
                    del self.profile_cooldowns[profile_id]
        self._save_configuration(configuration)
        return self._configuration_view(configuration)

    def _updated_profiles(
        self,
        updates: list[VoiceAnalysisCloudProfileUpdate],
        current_profiles: list[VoiceAnalysisCloudProfile],
    ) -> list[VoiceAnalysisCloudProfile]:
        current_by_id = {profile.profile_id: profile for profile in current_profiles}
        profiles: list[VoiceAnalysisCloudProfile] = []
        seen_ids: set[str] = set()
        for update in updates:
            profile_id = update.profile_id or f"cloud-{uuid.uuid4().hex[:12]}"
            if profile_id in seen_ids:
                raise VoiceAnalysisError(f"云端 API 队列存在重复 ID：{profile_id}")
            seen_ids.add(profile_id)
            existing = current_by_id.get(profile_id)
            api_key = existing.api_key if existing is not None else ""
            if update.clear_api_key:
                api_key = ""
            elif update.api_key is not None and update.api_key.strip():
                api_key = update.api_key.strip()
            provider = update.provider
            base_url = update.base_url.strip() or CLOUD_PROVIDER_BASE_URLS[provider]
            profiles.append(
                VoiceAnalysisCloudProfile(
                    profile_id=profile_id,
                    name=update.name.strip(),
                    provider=provider,
                    base_url=base_url,
                    model=update.model.strip(),
                    api_protocol=update.api_protocol or CLOUD_PROVIDER_PROTOCOLS[provider],
                    api_key=api_key,
                    enabled=update.enabled,
                )
            )
        return profiles

    @staticmethod
    def _validate_profiles(profiles: list[VoiceAnalysisCloudProfile], backend: ConfigurableAnalyzerMode) -> None:
        enabled = [profile for profile in profiles if profile.enabled]
        if backend in {"cloud", "hybrid"} and not enabled:
            raise VoiceAnalysisError("云端或混合模式至少需要一个已启用的 API 端点")
        if backend not in {"cloud", "hybrid"}:
            return
        for profile in enabled:
            if not profile.base_url:
                raise VoiceAnalysisError(f"{profile.name} 的 Base URL 不能为空")
            if not profile.model:
                raise VoiceAnalysisError(f"{profile.name} 的模型名称不能为空")
            if not profile.api_key:
                raise VoiceAnalysisError(f"{profile.name} 的 API Key 尚未配置")

    def list_models(
        self,
        request: VoiceAnalysisModelCatalogRequest,
    ) -> VoiceAnalysisModelCatalog:
        current = self._load_configuration()
        selected_profile = next(
            (profile for profile in current.profiles if profile.profile_id == request.profile_id),
            current.profiles[0] if current.profiles else None,
        )
        provider = request.provider or (selected_profile.provider if selected_profile else "custom")
        base_url = request.base_url.strip() if request.base_url is not None else (selected_profile.base_url if selected_profile else "")
        if not base_url:
            base_url = CLOUD_PROVIDER_BASE_URLS[provider]
        api_key = request.api_key.strip() if request.api_key and request.api_key.strip() else (selected_profile.api_key if selected_profile else "")
        if not base_url:
            raise VoiceAnalysisError("云端 API Base URL 不能为空")
        if not api_key:
            raise VoiceAnalysisError("云端 API Key 尚未配置")
        analyzer = OpenAICompatibleVoiceAnalyzer(
            self.workspace_root,
            provider=provider,
            base_url=base_url,
            model=selected_profile.model if selected_profile and selected_profile.model else "model-catalog",
            api_key=api_key,
            api_protocol=selected_profile.api_protocol if selected_profile else CLOUD_PROVIDER_PROTOCOLS[provider],
            client=self.cloud_client,
            retry_delay_seconds=0 if self.cloud_client is not None else 0.5,
            runtime_logger=self.runtime_logger,
            analysis_event_handler=self.analysis_event_handler,
            structured_mode_cache=self.structured_mode_cache,
            structured_mode_lock=self.cloud_state_lock,
        )
        return analyzer.list_models()

    def test_configuration(self) -> VoiceAnalysisStatus:
        analyzer = self._selected_analyzer()
        test_connection = getattr(analyzer, "test_connection", None)
        if callable(test_connection):
            test_connection()
        status = analyzer.status()
        if not status.available:
            raise VoiceAnalysisError(status.detail)
        return status

    def test_profile(self, profile_id: str) -> VoiceAnalysisConfigurationView:
        configuration = self._load_configuration()
        profile = next((item for item in configuration.profiles if item.profile_id == profile_id), None)
        if profile is None:
            raise VoiceAnalysisError("云端 API 端点不存在")
        analyzer = self._build_cloud_analyzer(profile)
        try:
            analyzer.test_connection()
        except VoiceAnalysisError as error:
            self.profile_health[profile_id] = ("failed", str(error))
            self.profile_cooldowns[profile_id] = time.monotonic() + 60.0
            raise
        self.profile_health[profile_id] = ("healthy", None)
        self.profile_cooldowns.pop(profile_id, None)
        return self._configuration_view(configuration)

    def status(self) -> VoiceAnalysisStatus:
        try:
            return self._selected_analyzer().status()
        except VoiceAnalysisError as error:
            configuration = self._load_configuration()
            primary = configuration.profiles[0] if configuration.profiles else None
            return VoiceAnalysisStatus(
                backend=configuration.backend,
                available=False,
                model=primary.model if primary else None,
                detail=str(error),
                taxonomy_version=int(self.rules_analyzer.taxonomy["schema_version"]),
            )

    def analyze(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        return self._selected_analyzer().analyze(evidence_pack)

    def screen_character_candidates(
        self,
        project_id: str,
        candidates: list[CharacterCandidateScreeningInput],
        canonical_anchors: list[CharacterCandidateScreeningInput],
    ) -> CharacterCandidateScreeningDraft:
        return self._selected_analyzer().screen_character_candidates(project_id, candidates, canonical_anchors)

    def generate_reference_text(
        self,
        evidence_pack: CharacterEvidencePack,
        voice_prompt: str,
    ) -> ReferenceTextDraft:
        return self._selected_analyzer().generate_reference_text(evidence_pack, voice_prompt)

    def analyze_director(
        self,
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> DirectorAnalysisDraft:
        return self._selected_analyzer().analyze_director(passages, characters)

    def analyze_text_structure(
        self,
        project_id: str,
        candidates: list[TextHeadingCandidate],
        total_characters: int,
    ) -> TextStructureDraft:
        return self._selected_analyzer().analyze_text_structure(project_id, candidates, total_characters)

    def cloud_analysis_parallelism(self) -> int:
        configuration = self._load_configuration()
        return configuration.cloud_parallelism if configuration.backend in {"cloud", "hybrid"} else 1

    def cloud_director_batch_size(self) -> int:
        configuration = self._load_configuration()
        return configuration.cloud_director_batch_size

    def release_model(self) -> None:
        release_model = getattr(self._selected_analyzer(), "release_model", None)
        if callable(release_model):
            release_model()

    def _selected_analyzer(self) -> VoiceAnalyzer:
        configuration = self._load_configuration()
        if configuration.backend == "local":
            return self.local_analyzer
        if configuration.backend in {"cloud", "hybrid"}:
            cloud_analyzer = self._cloud_router(configuration)
            if configuration.backend == "hybrid":
                return HybridVoiceAnalyzer(self.local_analyzer, cloud_analyzer, self.runtime_logger)
            return cloud_analyzer
        return self.rules_analyzer

    def _cloud_router(self, configuration: VoiceAnalysisConfiguration) -> FailoverVoiceAnalyzer:
        return FailoverVoiceAnalyzer(
            configuration.profiles,
            self._build_cloud_analyzer,
            self.runtime_logger,
            self.profile_health,
            self.profile_cooldowns,
            self.profile_probe_locks,
            self.cloud_state_lock,
            failover_enabled=configuration.failover_enabled,
        )

    def _build_cloud_analyzer(self, profile: VoiceAnalysisCloudProfile) -> OpenAICompatibleVoiceAnalyzer:
        return OpenAICompatibleVoiceAnalyzer(
            self.workspace_root,
            provider=profile.provider,
            base_url=profile.base_url,
            model=profile.model,
            api_key=profile.api_key,
            api_protocol=profile.api_protocol,
            client=self.cloud_client,
            retry_delay_seconds=0 if self.cloud_client is not None else 0.5,
            runtime_logger=self.runtime_logger,
            analysis_event_handler=self.analysis_event_handler,
            structured_mode_cache=self.structured_mode_cache,
            structured_mode_lock=self.cloud_state_lock,
        )

    def _load_configuration(self) -> VoiceAnalysisConfiguration:
        if os.getenv("ZW_VOICE_ANALYZER_FORCE_BACKEND") == "local":
            return VoiceAnalysisConfiguration(backend="local")
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
            configuration = VoiceAnalysisConfiguration.model_validate(raw)
            if isinstance(raw, dict) and "profiles" not in raw:
                self._save_configuration(configuration)
            return configuration
        except FileNotFoundError:
            return VoiceAnalysisConfiguration(backend=self.default_backend)
        except (OSError, ValidationError, ValueError) as error:
            raise VoiceAnalysisError(f"文本分析配置无效：{error}") from error

    def _save_configuration(self, configuration: VoiceAnalysisConfiguration) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.settings_path.with_suffix(".tmp")
        temporary_path.write_text(configuration.model_dump_json(indent=2), encoding="utf-8")
        temporary_path.replace(self.settings_path)

    def _configuration_view(
        self,
        configuration: VoiceAnalysisConfiguration,
    ) -> VoiceAnalysisConfigurationView:
        primary = configuration.profiles[0] if configuration.profiles else None
        now = time.monotonic()
        with self.cloud_state_lock:
            health_snapshot = dict(self.profile_health)
            cooldown_snapshot = dict(self.profile_cooldowns)
        profile_views: list[VoiceAnalysisCloudProfileView] = []
        for priority, profile in enumerate(configuration.profiles, start=1):
            health, last_error = health_snapshot.get(profile.profile_id, ("unknown", None))
            if health == "cooldown" and cooldown_snapshot.get(profile.profile_id, 0) <= now:
                health = "failed"
            profile_views.append(
                VoiceAnalysisCloudProfileView(
                    profile_id=profile.profile_id,
                    name=profile.name,
                    provider=profile.provider,
                    base_url=profile.base_url,
                    model=profile.model,
                    api_protocol=profile.api_protocol,
                    api_key_configured=bool(profile.api_key),
                    enabled=profile.enabled,
                    priority=priority,
                    health=health,
                    last_error=last_error,
                )
            )
        return VoiceAnalysisConfigurationView(
            backend=configuration.backend,
            provider=primary.provider if primary else "custom",
            base_url=primary.base_url if primary else "",
            model=primary.model if primary else "",
            api_protocol=primary.api_protocol if primary else "chat_completions",
            api_key_configured=bool(primary and primary.api_key),
            failover_enabled=configuration.failover_enabled,
            cloud_parallelism=configuration.cloud_parallelism,
            cloud_director_batch_size=configuration.cloud_director_batch_size,
            profiles=profile_views,
        )


def create_voice_analyzer(
    workspace_root: Path,
    *,
    default_backend: AnalyzerMode | None = None,
    runtime_logger: logging.Logger | None = None,
) -> VoiceAnalyzer:
    backend = (default_backend or os.getenv("ZW_VOICE_ANALYZER_BACKEND", "rules")).strip().lower()
    if backend not in {"local", "hybrid", "cloud", "rules"}:
        raise VoiceAnalysisError(f"不支持的音色分析后端：{backend}")
    if backend == "local":
        selected_backend: AnalyzerMode = "local"
    elif backend == "hybrid":
        selected_backend = "hybrid"
    elif backend == "cloud":
        selected_backend = "cloud"
    else:
        selected_backend = "rules"
    return ConfigurableVoiceAnalyzer(
        workspace_root,
        default_backend=selected_backend,
        runtime_logger=runtime_logger,
    )
