from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
import threading
import time
import uuid
import wave
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, model_validator

from .domain import (
    CharacterEvidence,
    CharacterTier,
    CharacterVoice,
    CharacterVoiceBible,
    DirectorDocument,
    DirectorSegment,
    PerformanceDirection,
)
from .long_form import (
    DEFAULT_ANALYSIS_PARALLELISM,
    LongFormAnalysisSettings,
    LongFormAnalysisSettingsUpdate,
    LongFormPlan,
    STANDARD_CHAPTER_PATTERN,
    TextStructureDraft,
    TextWindow,
    build_long_form_plan,
    find_heading_candidates,
    heuristic_heading_ids,
    windows_from_plan,
)
from .voice_analysis import (
    AnalyzerMode,
    CharacterCandidateScreeningDraft,
    CharacterCandidateScreeningInput,
    CharacterEvidencePack,
    CharacterVoiceProfile,
    CloudAnalysisEvent,
    DirectorAnalysisDraft,
    DirectorCharacter,
    DirectorPassageDecision,
    DirectorPassageEvidence,
    DEFAULT_CLOUD_DIRECTOR_BATCH_SIZE,
    DEFAULT_CLOUD_PARALLELISM,
    RuleBasedVoiceAnalyzer,
    VoiceAnalysisError,
    VoiceAnalysisConfigurationUpdate,
    VoiceAnalysisConfigurationView,
    VoiceAnalysisModelCatalog,
    VoiceAnalysisModelCatalogRequest,
    VoiceAnalysisStatus,
    VoiceAnalyzer,
)


PreparationStatus = Literal["imported", "analyzed", "characters_ready", "director_ready"]
CandidateDecision = Literal["pending", "accepted", "rejected"]
CandidateScreeningAction = Literal["keep", "reject", "merge"]
CandidateScreeningRoute = Literal["deterministic_keep", "deterministic_reject", "model"]
PreparationAction = Literal["analyze", "extract_characters", "generate_director"]
ReferenceSelectionMode = Literal["automatic", "optional", "narrator_default"]
ReferenceGenerationStatus = Literal["not_generated", "queued", "running", "generated", "failed"]
EmotionSelectionMode = Literal["base", "automatic", "optional", "custom"]
EmotionGenerationStatus = Literal["not_generated", "queued", "running", "generated", "failed"]
ReferenceAudioSource = Literal["generated", "uploaded", "recorded", "reused"]
ReferenceAudioDecision = Literal["provisional", "accepted", "rejected", "superseded"]
ReferenceTextSource = Literal["initial", "generated", "edited"]
ProjectRevisionStatus = Literal["running", "analyzed", "characters_ready", "director_ready", "failed"]

REFERENCE_PLAN_SCHEMA_VERSION = 7
REFERENCE_GENERATION_THRESHOLD = 0.10
REFERENCE_TEXT = "雨后的长街渐渐安静下来，我望着远处的灯火，平稳地说出今天的决定。"
EMOTION_PLAN_SCHEMA_VERSION = 1
DIRECTOR_SCHEMA_VERSION = 5
DIRECTOR_ANALYSIS_BATCH_SIZE = 24
CHARACTER_SCREENING_BATCH_SIZE = 24
LONG_FORM_IMMEDIATE_PROFILE_LIMIT = 24
LONG_FORM_BACKGROUND_PROFILE_LIMIT = 6
CHARACTER_PROFILE_MAX_ATTEMPTS = 3
DIRECTOR_CLOUD_ANALYSIS_BATCH_SIZE = DEFAULT_CLOUD_DIRECTOR_BATCH_SIZE
EMOTION_GENERATION_THRESHOLD = 0.10
DEFAULT_EMOTIONS = (
    ("愤怒", "压低声线后爆发，力度明确但保持吐字清晰", 0.72),
    ("悲伤", "气息放缓，声音低落克制，保留清晰度", 0.58),
    ("紧张", "呼吸略急，语速稍快，保持警觉和不安", 0.64),
    ("激动", "能量上扬，节奏加快，表达明显的兴奋感", 0.81),
)
FEMALE_CLUES = ("她", "女子", "少女", "小姐", "母亲", "姐姐", "妹妹", "妻子", "薰", "熏", "嫣", "妃", "仙")
MALE_CLUES = ("他", "男子", "少年", "先生", "父亲", "哥哥", "老者", "老")

CHAPTER_PATTERN = STANDARD_CHAPTER_PATTERN
SPEECH_VERB = r"(?:说道|问道|答道|喝道|喊道|叫道|笑道|冷声道|沉声道|低声道|高声道|道|说|问|答)"
QUOTE_PATTERN = re.compile(
    r"“(?P<double>[^”]+)”|「(?P<corner>[^」]+)」|『(?P<hollow>[^』]+)』|\"(?P<ascii>[^\"\n]+)\"",
    re.DOTALL,
)
ATTRIBUTION_PATTERN = re.compile(
    rf'(?:^|[\n。！？!?；;，,”"』」])(?P<clause>[\u4e00-\u9fff]{{2,14}}?)(?P<verb>{SPEECH_VERB})(?P<introduces>[：:]?[“"『「]?)',
    re.MULTILINE,
)
ROLE_TERMS = (
    "少年",
    "少女",
    "老人",
    "老者",
    "男子",
    "女子",
    "众人",
    "弟子",
    "族人",
    "宗主",
    "族长",
    "长老",
    "导师",
    "老师",
    "先生",
    "小姐",
    "少爷",
    "父亲",
    "母亲",
    "爷爷",
    "哥哥",
    "姐姐",
    "大人",
    "前辈",
    "掌柜",
)
LEADING_PHRASES = ("这时", "只见", "忽然", "突然", "此时", "随后")
NON_NAME_PREFIXES = (
    "谁知",
    "想要",
    "前途",
    "怎么",
    "什么",
    "这个",
    "那个",
    "如果",
    "但是",
    "不过",
    "因此",
    "于是",
    "然后",
    "因为",
    "所以",
    "只是",
    "自己",
    "没有",
    "可以",
    "已经",
    "还是",
    "看来",
    "原来",
    "难怪",
    "虽然",
    "当然",
    "现在",
    "略微",
    "笑着",
    "闻言",
    "任何",
    "连忙",
    "平静",
    "仰头",
    "方才",
    "应该",
    "带着",
    "满嘴",
)
NON_NAME_ENDINGS = tuple("的地得着了是在有没不也都可会能要知想让将而与和却再才嘴")
NON_ENTITY_SPEECH_TERMS = frozenset(
    {
        "干笑",
        "冷笑",
        "苦笑",
        "讪笑",
        "狞笑",
        "嗤笑",
        "轻笑",
        "微笑",
        "大笑",
        "薄怒",
        "愠怒",
        "沉声",
        "低声",
        "高声",
        "厉喝",
        "怒喝",
        "喝声",
        "笑声",
        "哭声",
    }
)
NON_ENTITY_SPEECH_ENDINGS = ("笑", "怒", "声", "喝")
ATTRIBUTION_MANNER_SUFFIXES = ("干", "冷", "苦", "讪", "狞", "嗤", "轻", "微", "大", "怒", "厉", "柔", "缓")
COMMON_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯卢莫房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉龚程邢滑裴陆荣翁荀羊惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全班仰秋仲伊宫宁仇栾甘厉戎祖武符刘景詹束龙叶司郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍桑桂濮牛寿通边扈燕冀浦尚农温庄晏柴瞿阎连茹习艾鱼容向古易廖步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁敖融冷訾辛阚毋乜鞠丰关蒯相查后荆红游竺权逯盖益桓公纳"
)
COMPOUND_SURNAMES = ("欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙", "慕容", "纳兰")
NAME_SUFFIXES = ("儿", "老", "翁", "仙", "妃")
ALIAS_TRANSLATION = str.maketrans({"薰": "熏"})
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_REFERENCE_AUDIO_BYTES = 64 * 1024 * 1024
REVISION_ARTIFACTS = (
    "analysis_settings.json",
    "analysis_audit.json",
    "character_voice_bible.json",
    "reference_plan.json",
    "emotion_plan.json",
    "director_doc.json",
    "analysis_activity.json",
    "character_analysis_checkpoint.json",
    "director_analysis_checkpoint.json",
)
MALE_NARRATOR_PROMPT = (
    "角色辨识核心：成年男性长篇旁白基线，保持沉稳可信与适度亲和，不绑定具体角色性格；"
    "基础声学画像：中低音区，声线重量适中、明暗均衡，质感清晰温润，以胸腔与混合共鸣为主；"
    "吐字清楚但不刻意，气息平稳充足，语速从容稳定，停连服从句意，动态克制自然；"
    "叙事表现：场景交代清楚，信息层级分明，对话转述仅做轻度区分，不抢占角色表演；"
    "长篇一致性：跨章节保持音高、共鸣位置、响度与节奏稳定，疲劳段不塌气、不含混；"
    "中性参考约束：避免播音腔、广告腔、过度低沉、夸张情绪和明显年龄化表演。"
)
FEMALE_NARRATOR_PROMPT = (
    "角色辨识核心：成年女性长篇旁白基线，保持清醒可信与适度温度，不绑定具体角色性格；"
    "基础声学画像：中音区，声线重量适中、明暗均衡，质感清澈温润，以口腔与混合共鸣为主；"
    "吐字清楚但不尖锐，气息平稳充足，语速从容稳定，停连服从句意，动态克制自然；"
    "叙事表现：场景交代清楚，信息层级分明，对话转述仅做轻度区分，不抢占角色表演；"
    "长篇一致性：跨章节保持音高、共鸣位置、响度与节奏稳定，疲劳段不虚浮、不发飘；"
    "中性参考约束：避免播音腔、客服腔、过度甜美、夸张情绪和明显年龄化表演。"
)


class PreparationProblem(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class PreparationCancelled(PreparationProblem):
    def __init__(self) -> None:
        super().__init__(409, "准备任务已终止，已完成的检查点已保留，可从失败处继续")


class SourceSummary(BaseModel):
    project_id: str
    file_name: str
    display_name: str
    size_bytes: int
    encoding: Literal["utf-8", "gb18030"]
    status: PreparationStatus


class ProjectManifest(BaseModel):
    schema_version: int = 1
    project_id: str
    display_name: str
    source_file: str
    source_path: str
    created_at: datetime
    updated_at: datetime


class ProjectRevision(BaseModel):
    revision_id: str
    display_name: str
    created_at: datetime
    updated_at: datetime
    status: ProjectRevisionStatus
    last_action: PreparationAction
    error: str | None = None


class ProjectRevisionWorkspace(BaseModel):
    schema_version: int = 1
    project_id: str
    active_revision_id: str | None = None
    revisions: list[ProjectRevision] = Field(default_factory=list)


class AnalysisStructure(BaseModel):
    chapter_count: int
    character_count: int
    nonempty_line_count: int
    estimated_segment_count: int
    dialogue_count: int


class CharacterCandidate(BaseModel):
    candidate_id: str
    display_name: str
    decision: CandidateDecision
    confidence: float = Field(ge=0, le=1)
    mention_count: int
    dialogue_count: int
    peak_batch_mentions: int = 0
    peak_batch_dialogue_count: int = 0
    batch_presence_count: int = 1
    local_importance: float = Field(default=0.0, ge=0, le=1)
    first_batch_mentions: int = 0
    first_batch_dialogue_count: int = 0
    entity_confidence: float = Field(default=0.0, ge=0, le=1)
    production_priority: float = Field(default=0.0, ge=0, le=1)
    batch_ids: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    reason: str
    screening_route: CandidateScreeningRoute | None = None
    screening_action: CandidateScreeningAction | None = None
    canonical_candidate_id: str | None = None
    screening_confidence: float | None = Field(default=None, ge=0, le=1)
    screening_rationale: str | None = None


class AnalysisAudit(BaseModel):
    schema_version: int = 4
    project_id: str
    source_file: str
    engine: Literal["rule_based_preview"] = "rule_based_preview"
    structure: AnalysisStructure
    candidates: list[CharacterCandidate]
    long_form_plan: LongFormPlan | None = None
    warnings: list[str] = Field(default_factory=list)
    candidate_screening_backend: AnalyzerMode | None = None
    candidate_screening_model: str | None = None
    candidate_screening_input_count: int = 0
    candidate_deterministic_kept_count: int = 0
    candidate_deterministic_rejected_count: int = 0
    candidate_screening_kept_count: int = 0
    candidate_screening_rejected_count: int = 0
    candidate_screening_merged_count: int = 0
    candidate_screening_completed_at: datetime | None = None


class ReferenceAudioVersion(BaseModel):
    version_id: str
    audio_url: str
    source: ReferenceAudioSource
    decision: ReferenceAudioDecision = "accepted"
    created_at: datetime


class ReferenceTextVersion(BaseModel):
    version_id: str
    text: str
    source: ReferenceTextSource
    created_at: datetime


class ReferencePlanItem(BaseModel):
    reference_id: str
    source_character_id: str
    display_name: str
    gender: Literal["male", "female", "unknown"]
    importance: float = Field(ge=0, le=1)
    selection_mode: ReferenceSelectionMode
    selected: bool
    locked: bool
    voice_prompt_locked: bool = False
    custom_voice_attributes: str = ""
    reference_text: str
    active_reference_text_version_id: str | None = None
    reference_text_versions: list[ReferenceTextVersion] = Field(default_factory=list)
    voice_prompt: str
    reuse_reference_id: str | None = None
    job_id: str | None = None
    audio_url: str | None = None
    audio_source: ReferenceAudioSource | None = None
    active_audio_version_id: str | None = None
    audio_versions: list[ReferenceAudioVersion] = Field(default_factory=list)
    status: ReferenceGenerationStatus = "not_generated"
    error: str | None = None


class ReferencePlan(BaseModel):
    schema_version: int = REFERENCE_PLAN_SCHEMA_VERSION
    project_id: str
    generation_backend: Literal["voxcpm2"] = "voxcpm2"
    automatic_threshold: float = REFERENCE_GENERATION_THRESHOLD
    automatic_items_locked: bool = True
    items: list[ReferencePlanItem]


class VoiceResourceMatch(BaseModel):
    source_project_id: str
    source_project_name: str
    source_reference_id: str
    source_version_id: str
    display_name: str
    gender: Literal["male", "female", "unknown"]
    voice_prompt: str
    audio_url: str
    audio_source: ReferenceAudioSource
    created_at: datetime
    similarity: float = Field(ge=0, le=1)


class VoiceResourceReuseRequest(BaseModel):
    source_project_id: str = Field(min_length=1, max_length=120)
    source_reference_id: str = Field(min_length=1, max_length=120)
    source_version_id: str = Field(min_length=1, max_length=120)


class EmotionPlanItem(BaseModel):
    variant_id: str
    parent_reference_id: str
    source_character_id: str
    display_name: str
    emotion_name: str
    description: str
    intensity: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    selection_mode: EmotionSelectionMode
    selected: bool
    locked: bool
    reference_text: str
    voice_prompt: str
    job_id: str | None = None
    audio_url: str | None = None
    status: EmotionGenerationStatus = "not_generated"
    error: str | None = None


class EmotionPlan(BaseModel):
    schema_version: int = EMOTION_PLAN_SCHEMA_VERSION
    project_id: str
    generation_backend: Literal["voxcpm2"] = "voxcpm2"
    skipped: bool = False
    automatic_threshold: float = EMOTION_GENERATION_THRESHOLD
    automatic_items_locked: bool = True
    items: list[EmotionPlanItem]


class PreparationPreview(BaseModel):
    project_id: str
    status: PreparationStatus
    source: SourceSummary
    analysis_settings: LongFormAnalysisSettings
    analysis_audit: AnalysisAudit | None = None
    character_voice_bible: CharacterVoiceBible | None = None
    reference_plan: ReferencePlan | None = None
    emotion_plan: EmotionPlan | None = None
    director_doc: DirectorDocument | None = None


class AnalysisActivityView(BaseModel):
    schema_version: int = 2
    project_id: str
    action: PreparationAction | None = None
    state: Literal["idle", "running", "complete", "failed", "cancelled"] = "idle"
    cancellable: bool = False
    percent: int = Field(default=0, ge=0, le=100)
    message: str = "尚未开始分析"
    backend: str = "-"
    model: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_seconds: float = Field(default=0.0, ge=0)
    current_batch: int | None = Field(default=None, ge=1)
    total_batches: int | None = Field(default=None, ge=1)
    input_events: list[CloudAnalysisEvent] = Field(default_factory=list)
    output_events: list[CloudAnalysisEvent] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PreparationActionRequest(BaseModel):
    action: PreparationAction
    revision_id: str | None = Field(default=None, min_length=1, max_length=120)
    resume: bool = False


class CharacterAnalysisCheckpoint(BaseModel):
    project_id: str
    profiles: dict[str, CharacterVoiceProfile] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DirectorAnalysisCheckpoint(BaseModel):
    project_id: str
    batch_size: int | None = Field(default=None, ge=1)
    completed_batches: list[str] = Field(default_factory=list)
    decisions: dict[str, DirectorPassageDecision] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    analysis_backend: Literal["local", "hybrid", "cloud", "rules"] = "rules"
    analysis_model: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReferenceUpdateRequest(BaseModel):
    selected: bool | None = None
    voice_prompt: str | None = Field(default=None, min_length=1, max_length=1_000)
    voice_prompt_locked: bool | None = None
    custom_voice_attributes: str | None = Field(default=None, max_length=500)
    reference_text: str | None = Field(default=None, min_length=1, max_length=180)

    @model_validator(mode="after")
    def require_change(self) -> "ReferenceUpdateRequest":
        if (
            self.selected is None
            and self.voice_prompt is None
            and self.voice_prompt_locked is None
            and self.custom_voice_attributes is None
            and self.reference_text is None
        ):
            raise ValueError("至少需要提交一个参考项修改")
        return self


class ReferenceSettingsRequest(BaseModel):
    automatic_threshold: float | None = Field(default=None, ge=0.01, le=1)
    automatic_items_locked: bool | None = None

    @model_validator(mode="after")
    def require_setting(self) -> "ReferenceSettingsRequest":
        if self.automatic_threshold is None and self.automatic_items_locked is None:
            raise ValueError("至少需要提交一个参考计划设置")
        return self


class ReferenceAudioReviewRequest(BaseModel):
    decision: Literal["accepted", "rejected"]


class VoiceProfileRegenerationRequest(BaseModel):
    character_id: str | None = Field(default=None, min_length=1, max_length=120)
    reference_id: str | None = Field(default=None, min_length=1, max_length=120)
    custom_attributes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_single_target(self) -> "VoiceProfileRegenerationRequest":
        if self.character_id is not None and self.reference_id is not None:
            raise ValueError("角色与参考项只能指定一个")
        if self.custom_attributes is not None and self.character_id is None and self.reference_id is None:
            raise ValueError("自定义属性只能用于指定角色或参考项")
        return self


class DirectorSegmentVoiceRequest(BaseModel):
    voice_reference_id: str | None = None

    @model_validator(mode="after")
    def require_explicit_value(self) -> "DirectorSegmentVoiceRequest":
        if "voice_reference_id" not in self.model_fields_set:
            raise ValueError("必须提交 voice_reference_id；设为 null 可恢复自动匹配")
        return self


@dataclass(frozen=True)
class _PreparedDirectorPassage:
    chapter_id: str
    text: str
    segment_type: Literal["narration", "dialogue"]
    evidence: DirectorPassageEvidence | None = None
    analysis_batch_id: str | None = None


@dataclass(frozen=True)
class _CharacterAnalysisTask:
    candidate_index: int
    candidate: CharacterCandidate
    aliases: list[str]
    character_id: str
    importance: float
    tier: CharacterTier
    evidence_pack: CharacterEvidencePack


class EmotionSettingsRequest(BaseModel):
    skipped: bool | None = None
    automatic_threshold: float | None = Field(default=None, ge=0.01, le=1)
    automatic_items_locked: bool | None = None

    @model_validator(mode="after")
    def require_setting(self) -> "EmotionSettingsRequest":
        if self.skipped is None and self.automatic_threshold is None and self.automatic_items_locked is None:
            raise ValueError("至少需要提交一个情绪计划设置")
        return self


class EmotionUpdateRequest(BaseModel):
    selected: bool


class EmotionCreateRequest(BaseModel):
    parent_reference_id: str = Field(min_length=1, max_length=120)
    emotion_name: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=1_000)
    intensity: float = Field(ge=0.05, le=1)


class PreparationService:
    """Owns the source-to-director preparation workflow and its persisted artifacts."""

    def __init__(
        self,
        workspace_root: Path,
        voice_analyzer: VoiceAnalyzer | None = None,
        runtime_logger: logging.Logger | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.source_root = self.workspace_root / "input"
        self.project_root = self.workspace_root / "outputs" / "projects"
        self.reference_audio_root = self.workspace_root / "outputs" / "audio" / "references"
        self.voice_analyzer = voice_analyzer or RuleBasedVoiceAnalyzer(self.workspace_root)
        self.runtime_logger = runtime_logger or logging.getLogger("zw_voice_factory")
        self._reference_lock = threading.RLock()
        self._artifact_lock = threading.RLock()
        self._revision_lock = threading.RLock()
        self._analysis_activity_lock = threading.RLock()
        self._manifest_lock = threading.RLock()
        self._preparation_cancel_lock = threading.RLock()
        self._preparation_cancel_events: dict[str, threading.Event] = {}
        set_event_handler = getattr(self.voice_analyzer, "set_analysis_event_handler", None)
        if callable(set_event_handler):
            set_event_handler(self.record_cloud_analysis_event)
        self.source_root.mkdir(parents=True, exist_ok=True)
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.reference_audio_root.mkdir(parents=True, exist_ok=True)
        self._recover_interrupted_analysis_activities()

    def _recover_interrupted_analysis_activities(self) -> None:
        for activity_path in self.project_root.glob("*/analysis_activity.json"):
            try:
                activity = AnalysisActivityView.model_validate_json(activity_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if activity.state != "running":
                continue
            checkpoint_at = activity.updated_at
            activity.state = "failed"
            activity.cancellable = False
            activity.completed_at = checkpoint_at
            if activity.started_at is not None:
                activity.elapsed_seconds = max(
                    activity.elapsed_seconds,
                    (checkpoint_at - activity.started_at).total_seconds(),
                )
            activity.message = f"任务在服务关闭时中断，可从 {activity.percent}% 的检查点继续"
            self._write_model(activity.project_id, "analysis_activity.json", activity)
            workspace = self._read_model(activity.project_id, "revisions.json", ProjectRevisionWorkspace)
            if workspace is None or workspace.active_revision_id is None:
                continue
            revision = next(
                (item for item in workspace.revisions if item.revision_id == workspace.active_revision_id),
                None,
            )
            if revision is None or revision.status != "running":
                continue
            revision.status = "failed"
            revision.error = activity.message
            revision.updated_at = checkpoint_at
            self._write_revision_workspace(workspace)

    def voice_analysis_status(self) -> VoiceAnalysisStatus:
        return self.voice_analyzer.status()

    def voice_analysis_configuration(self) -> VoiceAnalysisConfigurationView:
        configuration = getattr(self.voice_analyzer, "configuration", None)
        if not callable(configuration):
            status = self.voice_analyzer.status()
            return VoiceAnalysisConfigurationView(
                backend=status.backend,
                provider="custom",
                base_url="",
                model=status.model or "",
                api_protocol="chat_completions",
                api_key_configured=False,
            )
        return configuration()

    def update_voice_analysis_configuration(
        self,
        update: VoiceAnalysisConfigurationUpdate,
    ) -> VoiceAnalysisConfigurationView:
        update_configuration = getattr(self.voice_analyzer, "update_configuration", None)
        if not callable(update_configuration):
            raise PreparationProblem(409, "当前分析器不支持运行时切换")
        try:
            return update_configuration(update)
        except VoiceAnalysisError as error:
            raise PreparationProblem(422, str(error)) from error

    def test_voice_analysis_configuration(self) -> VoiceAnalysisStatus:
        test_configuration = getattr(self.voice_analyzer, "test_configuration", None)
        if not callable(test_configuration):
            return self.voice_analyzer.status()
        try:
            return test_configuration()
        except VoiceAnalysisError as error:
            raise PreparationProblem(503, str(error)) from error

    def test_voice_analysis_profile(self, profile_id: str) -> VoiceAnalysisConfigurationView:
        test_profile = getattr(self.voice_analyzer, "test_profile", None)
        if not callable(test_profile):
            raise PreparationProblem(409, "当前分析器不支持单端点测试")
        try:
            return test_profile(profile_id)
        except VoiceAnalysisError as error:
            raise PreparationProblem(503, str(error)) from error

    def list_voice_analysis_models(
        self,
        request: VoiceAnalysisModelCatalogRequest,
    ) -> VoiceAnalysisModelCatalog:
        list_models = getattr(self.voice_analyzer, "list_models", None)
        if not callable(list_models):
            raise PreparationProblem(409, "当前分析器不支持读取云端模型")
        try:
            return list_models(request)
        except VoiceAnalysisError as error:
            raise PreparationProblem(503, str(error)) from error

    def preview_voice_profile(self, evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        return self.voice_analyzer.analyze(evidence_pack)

    def list_sources(self) -> list[SourceSummary]:
        sources: list[SourceSummary] = []
        managed_project_ids: set[str] = set()
        manifests = sorted(
            self.project_root.glob("*/project.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for manifest_path in manifests:
            try:
                with self._manifest_lock:
                    manifest = ProjectManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
                path = self._manifest_source_path(manifest)
                _, encoding = self._read_text(path)
            except (OSError, UnicodeDecodeError, ValueError, PreparationProblem):
                continue
            managed_project_ids.add(manifest.project_id)
            sources.append(self._source_summary(path, encoding, manifest))
        for path in sorted(self.source_root.glob("*.txt"), key=lambda item: item.name.casefold()):
            try:
                _, encoding = self._read_text(path)
            except UnicodeDecodeError:
                continue
            source = self._source_summary(path, encoding)
            if source.project_id not in managed_project_ids:
                sources.append(source)
        return sources

    def import_source(self, file_name: str, content: bytes, project_name: str | None = None) -> SourceSummary:
        if not file_name or file_name != Path(file_name).name or any(mark in file_name for mark in ("/", "\\", ":", "\0")):
            raise PreparationProblem(400, "文件名不安全，请选择本地 TXT 文件")
        if Path(file_name).suffix.casefold() != ".txt":
            raise PreparationProblem(415, "仅支持 TXT 小说文件")
        if not content:
            raise PreparationProblem(400, "TXT 文件为空")
        if len(content) > MAX_UPLOAD_BYTES:
            raise PreparationProblem(413, "TXT 文件超过 32 MB 限制")
        _, encoding = self._decode(content)
        if project_name is not None:
            display_name = re.sub(r"\s+", " ", project_name).strip()
            if not display_name:
                raise PreparationProblem(400, "请先填写项目名称")
            if len(display_name) > 120 or any(mark in display_name for mark in ("/", "\\", ":", "\0")):
                raise PreparationProblem(400, "项目名称不安全或超过 120 个字符")
            if any(source.display_name.casefold() == display_name.casefold() for source in self.list_sources()):
                raise PreparationProblem(409, "同名项目已存在，请使用新的项目名称")
            project_id = f"project-{uuid.uuid4().hex[:16]}"
            project_dir = self.project_root / project_id
            source_dir = project_dir / "source"
            source_dir.mkdir(parents=True, exist_ok=False)
            target = source_dir / file_name
            target.write_bytes(content)
            now = datetime.now(timezone.utc)
            manifest = ProjectManifest(
                project_id=project_id,
                display_name=display_name,
                source_file=file_name,
                source_path=target.relative_to(project_dir).as_posix(),
                created_at=now,
                updated_at=now,
            )
            self._write_project_manifest(manifest)
            return self._source_summary(target, encoding, manifest)
        target = self.source_root / file_name
        if target.exists():
            raise PreparationProblem(409, "同名 TXT 已存在，请先在列表中选择")
        target.write_bytes(content)
        return self._source_summary(target, encoding)

    def revision_workspace(self, project_id: str) -> ProjectRevisionWorkspace:
        self._find_source(project_id)
        with self._revision_lock:
            return self._ensure_revision_workspace(project_id)

    def activate_revision(self, project_id: str, revision_id: str) -> PreparationPreview:
        self._find_source(project_id)
        with self._revision_lock:
            workspace = self._ensure_revision_workspace(project_id)
            if not any(item.revision_id == revision_id for item in workspace.revisions):
                raise PreparationProblem(404, "分析分支不存在")
            if workspace.active_revision_id == revision_id:
                return self.preview(project_id)
            if workspace.active_revision_id is not None:
                self._snapshot_revision(project_id, workspace.active_revision_id)
            self._restore_revision(project_id, revision_id)
            workspace.active_revision_id = revision_id
            self._write_revision_workspace(workspace)
        return self.preview(project_id)

    def delete_revision(self, project_id: str, revision_id: str) -> ProjectRevisionWorkspace:
        self._find_source(project_id)
        with self._revision_lock:
            workspace = self._ensure_revision_workspace(project_id)
            revision = next((item for item in workspace.revisions if item.revision_id == revision_id), None)
            if revision is None:
                raise PreparationProblem(404, "分析分支不存在")
            revision_dir = self._revision_dir(project_id, revision_id)
            if revision_dir.is_dir():
                shutil.rmtree(revision_dir)
            workspace.revisions = [item for item in workspace.revisions if item.revision_id != revision_id]
            if workspace.active_revision_id == revision_id:
                replacement = workspace.revisions[0].revision_id if workspace.revisions else None
                self._clear_root_revision_artifacts(project_id, preserve_settings=replacement is None)
                workspace.active_revision_id = replacement
                if replacement is not None:
                    self._restore_revision(project_id, replacement)
            self._write_revision_workspace(workspace)
            return workspace

    def _ensure_revision_workspace(self, project_id: str) -> ProjectRevisionWorkspace:
        path = self._revision_workspace_path(project_id)
        if path.is_file():
            return ProjectRevisionWorkspace.model_validate_json(path.read_text(encoding="utf-8"))
        workspace = ProjectRevisionWorkspace(project_id=project_id)
        if any(
            self._artifact_path(project_id, name).is_file()
            for name in ("analysis_audit.json", "character_voice_bible.json", "director_doc.json", "analysis_activity.json")
        ):
            now = datetime.now(timezone.utc)
            activity = self._read_model(project_id, "analysis_activity.json", AnalysisActivityView)
            revision_id = f"revision-legacy-{uuid.uuid4().hex[:8]}"
            workspace.active_revision_id = revision_id
            workspace.revisions.append(
                ProjectRevision(
                    revision_id=revision_id,
                    display_name="已有缓存",
                    created_at=now,
                    updated_at=now,
                    status=self._revision_status(project_id),
                    last_action=activity.action if activity is not None and activity.action is not None else "analyze",
                    error=(
                        activity.message
                        if activity is not None and activity.state in {"failed", "cancelled"}
                        else None
                    ),
                )
            )
            self._write_revision_workspace(workspace)
            self._snapshot_revision(project_id, revision_id)
        else:
            self._write_revision_workspace(workspace)
        return workspace

    def _prepare_revision_action(
        self,
        project_id: str,
        action: PreparationAction,
        revision_id: str | None,
        resume: bool,
    ) -> str | None:
        with self._revision_lock:
            workspace = self._ensure_revision_workspace(project_id)
            if action == "analyze" and not resume:
                if workspace.active_revision_id is not None:
                    self._snapshot_revision(project_id, workspace.active_revision_id)
                now = datetime.now(timezone.utc)
                next_number = len(workspace.revisions) + 1
                new_revision_id = f"revision-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
                workspace.revisions.insert(
                    0,
                    ProjectRevision(
                        revision_id=new_revision_id,
                        display_name=f"分析分支 {next_number:02d}",
                        created_at=now,
                        updated_at=now,
                        status="running",
                        last_action=action,
                    ),
                )
                workspace.active_revision_id = new_revision_id
                self._write_revision_workspace(workspace)
                self._clear_root_revision_artifacts(project_id, preserve_settings=True)
                return new_revision_id
            target_revision_id = revision_id or workspace.active_revision_id
            if target_revision_id is None:
                raise PreparationProblem(409, "请先分析文档并创建分析分支")
            if workspace.active_revision_id != target_revision_id:
                if workspace.active_revision_id is not None:
                    self._snapshot_revision(project_id, workspace.active_revision_id)
                self._restore_revision(project_id, target_revision_id)
                workspace.active_revision_id = target_revision_id
            target = next((item for item in workspace.revisions if item.revision_id == target_revision_id), None)
            if target is None:
                raise PreparationProblem(404, "选择的分析分支不存在")
            target.status = "running"
            target.last_action = action
            target.error = None
            target.updated_at = datetime.now(timezone.utc)
            self._write_revision_workspace(workspace)
            return target_revision_id

    def _finish_revision_action(
        self,
        project_id: str,
        revision_id: str | None,
        action: PreparationAction,
        error: str | None = None,
    ) -> None:
        if revision_id is None:
            return
        with self._revision_lock:
            workspace = self._ensure_revision_workspace(project_id)
            revision = next((item for item in workspace.revisions if item.revision_id == revision_id), None)
            if revision is None:
                return
            revision.status = "failed" if error is not None else {
                "analyze": "analyzed",
                "extract_characters": "characters_ready",
                "generate_director": "director_ready",
            }[action]
            revision.last_action = action
            revision.error = error[:500] if error is not None else None
            revision.updated_at = datetime.now(timezone.utc)
            self._snapshot_revision(project_id, revision_id)
            self._write_revision_workspace(workspace)

    def _revision_workspace_path(self, project_id: str) -> Path:
        return self.project_root / project_id / "revisions.json"

    def _revision_dir(self, project_id: str, revision_id: str) -> Path:
        root = (self.project_root / project_id / "revisions").resolve()
        candidate = (root / revision_id).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise PreparationProblem(400, "分析分支路径不安全") from error
        return candidate

    def _write_revision_workspace(self, workspace: ProjectRevisionWorkspace) -> None:
        path = self._revision_workspace_path(workspace.project_id)
        self._write_json_file(path, workspace.model_dump(mode="json"))

    @staticmethod
    def _write_json_file(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            PreparationService._replace_with_retry(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _replace_with_retry(temporary: Path, target: Path) -> None:
        for attempt in range(6):
            try:
                temporary.replace(target)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))

    def _snapshot_revision(self, project_id: str, revision_id: str) -> None:
        target_dir = self._revision_dir(project_id, revision_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in REVISION_ARTIFACTS:
            source = self._artifact_path(project_id, name)
            target = target_dir / name
            if source.is_file():
                shutil.copyfile(source, target)
            else:
                target.unlink(missing_ok=True)

    def _restore_revision(self, project_id: str, revision_id: str) -> None:
        source_dir = self._revision_dir(project_id, revision_id)
        if not source_dir.is_dir():
            raise PreparationProblem(404, "分析分支缓存目录不存在")
        self._clear_root_revision_artifacts(project_id, preserve_settings=False)
        for name in REVISION_ARTIFACTS:
            source = source_dir / name
            if source.is_file():
                shutil.copyfile(source, self._artifact_path(project_id, name))

    def _clear_root_revision_artifacts(self, project_id: str, preserve_settings: bool) -> None:
        for name in REVISION_ARTIFACTS:
            if preserve_settings and name == "analysis_settings.json":
                continue
            self._artifact_path(project_id, name).unlink(missing_ok=True)

    def _revision_status(self, project_id: str) -> ProjectRevisionStatus:
        activity = self._read_model(project_id, "analysis_activity.json", AnalysisActivityView)
        if activity is not None and activity.state == "failed":
            return "failed"
        status = self._status(project_id)
        return "director_ready" if status == "director_ready" else "characters_ready" if status == "characters_ready" else "analyzed"

    def preview(self, project_id: str) -> PreparationPreview:
        source_path = self._find_source(project_id)
        text, encoding = self._read_text(source_path)
        manifest = self._read_project_manifest(project_id)
        analysis_settings = self._read_analysis_settings(project_id)
        audit = self._read_model(project_id, "analysis_audit.json", AnalysisAudit)
        if audit is not None and audit.long_form_plan is None:
            candidates = find_heading_candidates(text)
            inferred = TextStructureDraft(
                heading_ids=heuristic_heading_ids(candidates),
                confidence=0.55,
                rationale="兼容旧项目时使用本地标题规则重建长篇计划",
                backend="rules",
            )
            audit.long_form_plan = build_long_form_plan(
                text,
                analysis_settings,
                inferred_candidates=candidates,
                inferred_structure=inferred,
            )
            audit.schema_version = 2
            self._write_model(project_id, "analysis_audit.json", audit)
        bible = self._read_model(project_id, "character_voice_bible.json", CharacterVoiceBible)
        reference_plan = self._read_model(project_id, "reference_plan.json", ReferencePlan)
        if bible is not None and reference_plan is None:
            with self._reference_lock:
                reference_plan = self._read_model(project_id, "reference_plan.json", ReferencePlan)
                if reference_plan is None:
                    reference_plan = self._build_reference_plan(project_id, bible)
                    self._write_model(project_id, "reference_plan.json", reference_plan)
        elif reference_plan is not None and reference_plan.schema_version < REFERENCE_PLAN_SCHEMA_VERSION:
            with self._reference_lock:
                previous_version = reference_plan.schema_version
                if previous_version < 2:
                    self._apply_reference_threshold(reference_plan, REFERENCE_GENERATION_THRESHOLD)
                if previous_version < 3:
                    self._apply_reference_lock(reference_plan, True)
                if previous_version < 4:
                    self._migrate_reference_audio_versions(reference_plan)
                    for item in reference_plan.items:
                        if item.selection_mode == "narrator_default":
                            item.voice_prompt_locked = True
                if previous_version < 5:
                    self._migrate_reference_text_versions(reference_plan)
                if previous_version < 6:
                    for item in reference_plan.items:
                        if item.selection_mode != "narrator_default" or len(item.voice_prompt) >= 120:
                            continue
                        item.voice_prompt = FEMALE_NARRATOR_PROMPT if item.gender == "female" else MALE_NARRATOR_PROMPT
                reference_plan.schema_version = REFERENCE_PLAN_SCHEMA_VERSION
                self._write_model(project_id, "reference_plan.json", reference_plan)
        emotion_plan = self._read_model(project_id, "emotion_plan.json", EmotionPlan)
        if reference_plan is not None:
            with self._reference_lock:
                emotion_plan = self._sync_emotion_plan(project_id, reference_plan, emotion_plan)
                self._write_model(project_id, "emotion_plan.json", emotion_plan)
        director = self._read_model(project_id, "director_doc.json", DirectorDocument)
        if director is not None and director.schema_version < DIRECTOR_SCHEMA_VERSION:
            character_by_id = {
                character.character_id: character
                for character in bible.characters
            } if bible is not None else {}
            for segment in director.segments:
                if segment.segment_type == "narration":
                    segment.speaker_kind = "narration"
                    continue
                character = character_by_id.get(segment.character_id)
                if character is not None and segment.character_id != "narrator":
                    segment.speaker_kind = "named"
                    if character.gender in {"male", "female"}:
                        segment.speaker_gender = character.gender
            director.schema_version = DIRECTOR_SCHEMA_VERSION
            self._write_model(project_id, "director_doc.json", director)
        return PreparationPreview(
            project_id=project_id,
            status=self._status(project_id),
            source=self._source_summary(source_path, encoding, manifest),
            analysis_settings=analysis_settings,
            analysis_audit=audit,
            character_voice_bible=bible,
            reference_plan=reference_plan,
            emotion_plan=emotion_plan,
            director_doc=director,
        )

    def run(
        self,
        project_id: str,
        action: PreparationAction,
        revision_id: str | None = None,
        resume: bool = False,
    ) -> PreparationPreview:
        cancel_event = self._begin_preparation_action(project_id)
        try:
            active_revision_id = self._prepare_revision_action(project_id, action, revision_id, resume)
            if not resume:
                if action == "extract_characters":
                    self._remove_artifact(project_id, "character_analysis_checkpoint.json")
                elif action == "generate_director":
                    self._remove_artifact(project_id, "director_analysis_checkpoint.json")
            self._start_analysis_activity(project_id, action)
            try:
                if action == "analyze":
                    self._analyze(project_id)
                elif action == "extract_characters":
                    self._extract_characters(project_id)
                else:
                    self._generate_director(project_id)
                self._check_preparation_cancelled(project_id)
            except PreparationCancelled as error:
                self._cancel_analysis_activity(project_id)
                self._finish_revision_action(project_id, active_revision_id, action, str(error))
                raise
            except PreparationProblem as error:
                self._fail_analysis_activity(project_id, str(error))
                self._finish_revision_action(project_id, active_revision_id, action, str(error))
                raise
            except Exception as error:
                self._fail_analysis_activity(project_id, str(error))
                self._finish_revision_action(project_id, active_revision_id, action, str(error))
                raise PreparationProblem(500, f"准备任务 {action} 执行异常：{error}") from error
            self._complete_analysis_activity(project_id, action)
            self._finish_revision_action(project_id, active_revision_id, action)
            return self.preview(project_id)
        finally:
            self._end_preparation_action(project_id, cancel_event)

    def cancel_preparation(self, project_id: str) -> AnalysisActivityView:
        self._find_source(project_id)
        with self._preparation_cancel_lock:
            cancel_event = self._preparation_cancel_events.get(project_id)
            if cancel_event is None:
                raise PreparationProblem(409, "当前项目没有正在运行的准备任务")
            cancel_event.set()
        with self._analysis_activity_lock:
            activity = self._read_analysis_activity(project_id)
            if activity.state == "running":
                activity.message = "已请求终止；当前模型调用完成后停止，并保留已完成检查点"
                activity.updated_at = datetime.now(timezone.utc)
                self._write_model(project_id, "analysis_activity.json", activity)
            return activity

    def prepare_analysis_window(self, project_id: str, ready_batch_limit: int) -> PreparationPreview:
        if ready_batch_limit < 1:
            raise PreparationProblem(422, "文本分析窗口必须至少包含一个切片")
        audit = self._read_model(project_id, "analysis_audit.json", AnalysisAudit)
        if audit is None:
            workspace = self._ensure_revision_workspace(project_id)
            if workspace.active_revision_id is None:
                self._prepare_revision_action(project_id, "analyze", None, False)
        self._start_analysis_activity(project_id, "analyze")
        try:
            complete = self._analyze(project_id, max_analyzed_batches=ready_batch_limit)
        except PreparationProblem as error:
            self._fail_analysis_activity(project_id, str(error))
            raise
        except Exception as error:
            self._fail_analysis_activity(project_id, str(error))
            raise PreparationProblem(500, f"文本切片分析异常：{error}") from error
        if complete:
            self._complete_analysis_activity(project_id, "analyze")
        else:
            self._complete_analysis_window_activity(project_id)
        return self.preview(project_id)

    def _begin_preparation_action(self, project_id: str) -> threading.Event:
        with self._preparation_cancel_lock:
            if project_id in self._preparation_cancel_events:
                raise PreparationProblem(409, "当前项目已有准备任务正在运行")
            cancel_event = threading.Event()
            self._preparation_cancel_events[project_id] = cancel_event
            return cancel_event

    def _end_preparation_action(self, project_id: str, cancel_event: threading.Event) -> None:
        with self._preparation_cancel_lock:
            if self._preparation_cancel_events.get(project_id) is cancel_event:
                del self._preparation_cancel_events[project_id]

    def _check_preparation_cancelled(self, project_id: str) -> None:
        with self._preparation_cancel_lock:
            cancel_event = self._preparation_cancel_events.get(project_id)
        if cancel_event is not None and cancel_event.is_set():
            raise PreparationCancelled()

    def _preparation_is_cancellable(self, project_id: str) -> bool:
        with self._preparation_cancel_lock:
            return project_id in self._preparation_cancel_events

    def prepare_director_window(self, project_id: str, ready_batch_limit: int) -> PreparationPreview:
        if ready_batch_limit < 1:
            raise PreparationProblem(422, "导演预取窗口必须至少包含一个切片")
        workspace = self._ensure_revision_workspace(project_id)
        revision_id = workspace.active_revision_id
        if revision_id is None:
            raise PreparationProblem(409, "请先分析文档并创建分析分支")
        self._start_analysis_activity(project_id, "generate_director")
        try:
            complete = self._generate_director(project_id, max_ready_batches=ready_batch_limit)
        except PreparationProblem as error:
            self._fail_analysis_activity(project_id, str(error))
            self._finish_revision_action(project_id, revision_id, "generate_director", str(error))
            raise
        except Exception as error:
            self._fail_analysis_activity(project_id, str(error))
            self._finish_revision_action(project_id, revision_id, "generate_director", str(error))
            raise PreparationProblem(500, f"导演窗口生成异常：{error}") from error
        if complete:
            self._complete_analysis_activity(project_id, "generate_director")
            self._finish_revision_action(project_id, revision_id, "generate_director")
        else:
            self._complete_director_window_activity(project_id)
        return self.preview(project_id)

    def prepare_character_window(self, project_id: str, ready_batch_limit: int) -> PreparationPreview:
        if ready_batch_limit < 1:
            raise PreparationProblem(422, "角色预取窗口必须至少包含一个切片")
        source_path = self._find_source(project_id)
        text, _ = self._read_text(source_path)
        audit = self._require_model(project_id, "analysis_audit.json", AnalysisAudit, "请先分析文档")
        if audit.long_form_plan is None or not audit.long_form_plan.is_long_form:
            bible = self._read_model(project_id, "character_voice_bible.json", CharacterVoiceBible)
            if bible is None:
                self._extract_characters(project_id)
            return self.preview(project_id)
        batches = audit.long_form_plan.batches[:ready_batch_limit]
        bible = self._read_model(project_id, "character_voice_bible.json", CharacterVoiceBible)
        if bible is None or any(batch.state == "analyzed" for batch in batches):
            self._start_analysis_activity(project_id, "extract_characters")
            self._extract_characters(project_id)
            self._complete_analysis_activity(project_id, "extract_characters")
            audit = self._require_model(project_id, "analysis_audit.json", AnalysisAudit, "请先分析文档")
            bible = self._require_model(
                project_id,
                "character_voice_bible.json",
                CharacterVoiceBible,
                "请先提取角色",
            )
        self._require_model(project_id, "reference_plan.json", ReferencePlan, "请先生成参考计划")
        windows = windows_from_plan(text, audit.long_form_plan)[:ready_batch_limit]
        window_text = "\n".join(window.text for window in windows)
        pending = [
            character
            for character in bible.characters
            if character.character_id != "narrator"
            and character.archetype_id is not None
            and not character.voice_profile_rationale.startswith("音色画像重试")
            and any(name and name in window_text for name in [character.display_name, *character.aliases])
        ][:LONG_FORM_BACKGROUND_PROFILE_LIMIT]
        if not pending:
            return self.preview(project_id)

        profile_updates: dict[str, CharacterVoiceProfile] = {}
        failed_updates: dict[str, str] = {}
        for character in pending:
            names = [character.display_name, *character.aliases]
            evidence = self._window_character_evidence(window_text, names)
            evidence.extend(item.text for item in character.evidence if item.text not in evidence)
            evidence = evidence[:8]
            evidence_pack = CharacterEvidencePack(
                project_id=project_id,
                character_id=character.character_id,
                display_name=character.display_name,
                aliases=character.aliases,
                mention_count=sum(window_text.count(name) for name in names if name),
                dialogue_count=sum(
                    len(re.findall(rf"{re.escape(name)}[\u4e00-\u9fff]{{0,4}}?{SPEECH_VERB}", window_text))
                    for name in names
                    if name
                ),
                gender_hint=character.gender if character.gender in {"male", "female"} else "unknown",
                evidence=evidence or [f"{character.display_name} 在当前切片中出现。"],
            )
            try:
                profile_updates[character.character_id] = self._analyze_character_profile_with_retries(evidence_pack)
            except VoiceAnalysisError as error:
                failed_updates[character.character_id] = (
                    f"{character.display_name} 音色画像重试 {CHARACTER_PROFILE_MAX_ATTEMPTS} 次仍失败，"
                    f"已跳过并继续后台切片：{error}"
                )

        with self._reference_lock:
            audit = self._require_model(project_id, "analysis_audit.json", AnalysisAudit, "请先分析文档")
            bible = self._require_model(
                project_id,
                "character_voice_bible.json",
                CharacterVoiceBible,
                "请先提取角色",
            )
            plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "请先生成参考计划",
            )
            character_by_id = {character.character_id: character for character in bible.characters}
            reference_by_character = {
                item.source_character_id: item
                for item in plan.items
                if item.selection_mode != "narrator_default"
            }
            for character_id, profile in profile_updates.items():
                character = character_by_id.get(character_id)
                if character is None or character.archetype_id is None:
                    continue
                character.gender = profile.gender
                character.age_range = profile.age_range
                character.personality_tags = profile.personality_tags
                character.timbre_tags = profile.timbre_tags
                character.delivery_tags = profile.delivery_tags
                character.voice_constraints = profile.voice_constraints
                character.voice_prompt = profile.voice_prompt
                character.voice_profile_confidence = profile.confidence
                character.voice_profile_rationale = profile.rationale
                character.archetype_id = None
                bible.analysis_backend = profile.backend
                bible.analysis_model = profile.model
                reference = reference_by_character.get(character_id)
                if reference is not None:
                    reference.gender = profile.gender
                    reference.voice_prompt = profile.voice_prompt
                    if character.importance >= REFERENCE_GENERATION_THRESHOLD:
                        reference.selection_mode = "automatic"
                        reference.selected = True
                        reference.locked = True
            for character_id, detail in failed_updates.items():
                character = character_by_id.get(character_id)
                if character is None or character.archetype_id is None:
                    continue
                character.voice_profile_rationale = detail
                character.voice_constraints = ["画像重试失败，暂用原型声线", "中性参考不携带场景情绪"]
                audit.warnings.append(detail)
            audit.warnings = list(dict.fromkeys(audit.warnings))
            self._write_model(project_id, "analysis_audit.json", audit)
            self._write_model(project_id, "character_voice_bible.json", bible)
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    def analysis_activity(self, project_id: str) -> AnalysisActivityView:
        self._find_source(project_id)
        cancellable = self._preparation_is_cancellable(project_id)
        with self._analysis_activity_lock:
            activity = self._read_analysis_activity(project_id)
            activity.cancellable = cancellable and activity.state == "running"
            if activity.state == "running" and activity.started_at is not None:
                activity.elapsed_seconds = max(
                    activity.elapsed_seconds,
                    (datetime.now(timezone.utc) - activity.started_at).total_seconds(),
                )
            return activity

    def update_analysis_settings(
        self,
        project_id: str,
        update: LongFormAnalysisSettingsUpdate,
    ) -> PreparationPreview:
        self._find_source(project_id)
        current = self._read_analysis_settings(project_id)
        values = current.model_dump()
        values.update(update.model_dump(exclude_none=True, exclude_unset=True))
        settings = LongFormAnalysisSettings.model_validate(values)
        self._write_model(project_id, "analysis_settings.json", settings)
        for artifact in (
            "analysis_audit.json",
            "character_voice_bible.json",
            "reference_plan.json",
            "emotion_plan.json",
            "director_doc.json",
            "analysis_activity.json",
        ):
            self._remove_artifact(project_id, artifact)
        return self.preview(project_id)

    def _read_analysis_settings(self, project_id: str) -> LongFormAnalysisSettings:
        settings = self._read_model(project_id, "analysis_settings.json", LongFormAnalysisSettings)
        return settings or LongFormAnalysisSettings()

    def record_cloud_analysis_event(self, event: CloudAnalysisEvent) -> None:
        with self._analysis_activity_lock:
            activity = self._read_analysis_activity(event.project_id)
            target = activity.input_events if event.direction == "INPUT" else activity.output_events
            target.append(event)
            del target[:-12]
            activity.updated_at = datetime.now(timezone.utc)
            self._write_model(event.project_id, "analysis_activity.json", activity)

    def _start_analysis_activity(self, project_id: str, action: PreparationAction) -> None:
        labels = {
            "analyze": "文本结构分析开始",
            "extract_characters": "角色与音色分析开始",
            "generate_director": "导演脚本分析开始",
        }
        started_at = datetime.now(timezone.utc)
        with self._analysis_activity_lock:
            self._write_model(
                project_id,
                "analysis_activity.json",
                AnalysisActivityView(
                    project_id=project_id,
                    action=action,
                    state="running",
                    cancellable=self._preparation_is_cancellable(project_id),
                    percent=0,
                    message=labels[action],
                    started_at=started_at,
                    elapsed_seconds=0,
                ),
            )

    def _complete_analysis_activity(self, project_id: str, action: PreparationAction) -> None:
        labels = {
            "analyze": "文本结构分析完成",
            "extract_characters": "角色与音色分析完成",
            "generate_director": "导演脚本分析完成",
        }
        with self._analysis_activity_lock:
            activity = self._read_analysis_activity(project_id)
            completed_at = datetime.now(timezone.utc)
            was_complete = activity.percent >= 100
            activity.state = "complete"
            activity.cancellable = False
            activity.percent = 100
            if not was_complete or activity.message.endswith("开始"):
                activity.message = labels[action]
            activity.completed_at = completed_at
            if activity.started_at is not None:
                activity.elapsed_seconds = max(
                    activity.elapsed_seconds,
                    (completed_at - activity.started_at).total_seconds(),
                )
            activity.updated_at = completed_at
            self._write_model(project_id, "analysis_activity.json", activity)

    def _complete_director_window_activity(self, project_id: str) -> None:
        audit = self._read_model(project_id, "analysis_audit.json", AnalysisAudit)
        batches = audit.long_form_plan.batches if audit is not None and audit.long_form_plan is not None else []
        ready = sum(batch.state == "ready" for batch in batches)
        total = max(len(batches), 1)
        with self._analysis_activity_lock:
            activity = self._read_analysis_activity(project_id)
            completed_at = datetime.now(timezone.utc)
            activity.state = "complete"
            activity.cancellable = False
            activity.percent = round(ready * 100 / total)
            activity.message = f"导演预取窗口已就绪：{ready}/{total} 个切片"
            activity.current_batch = ready or None
            activity.total_batches = total
            activity.completed_at = completed_at
            if activity.started_at is not None:
                activity.elapsed_seconds = max(
                    activity.elapsed_seconds,
                    (completed_at - activity.started_at).total_seconds(),
                )
            activity.updated_at = completed_at
            self._write_model(project_id, "analysis_activity.json", activity)

    def _complete_analysis_window_activity(self, project_id: str) -> None:
        audit = self._read_model(project_id, "analysis_audit.json", AnalysisAudit)
        batches = audit.long_form_plan.batches if audit is not None and audit.long_form_plan is not None else []
        analyzed = sum(batch.state != "pending" for batch in batches)
        total = max(len(batches), 1)
        with self._analysis_activity_lock:
            activity = self._read_analysis_activity(project_id)
            completed_at = datetime.now(timezone.utc)
            activity.state = "complete"
            activity.cancellable = False
            activity.percent = round(analyzed * 100 / total)
            activity.message = f"文本切片已扫描：{analyzed}/{total}，首片优先继续"
            activity.current_batch = analyzed or None
            activity.total_batches = total
            activity.completed_at = completed_at
            if activity.started_at is not None:
                activity.elapsed_seconds = max(
                    activity.elapsed_seconds,
                    (completed_at - activity.started_at).total_seconds(),
                )
            activity.updated_at = completed_at
            self._write_model(project_id, "analysis_activity.json", activity)

    def _fail_analysis_activity(self, project_id: str, detail: str) -> None:
        with self._analysis_activity_lock:
            activity = self._read_analysis_activity(project_id)
            completed_at = datetime.now(timezone.utc)
            activity.state = "failed"
            activity.cancellable = False
            activity.message = f"分析失败：{detail[:240]}"
            activity.completed_at = completed_at
            if activity.started_at is not None:
                activity.elapsed_seconds = max(
                    activity.elapsed_seconds,
                    (completed_at - activity.started_at).total_seconds(),
                )
            activity.updated_at = completed_at
            self._write_model(project_id, "analysis_activity.json", activity)

    def _cancel_analysis_activity(self, project_id: str) -> None:
        with self._analysis_activity_lock:
            activity = self._read_analysis_activity(project_id)
            completed_at = datetime.now(timezone.utc)
            activity.state = "cancelled"
            activity.cancellable = False
            activity.message = "任务已终止；已完成的检查点已保留，可从失败处继续"
            activity.completed_at = completed_at
            if activity.started_at is not None:
                activity.elapsed_seconds = max(
                    activity.elapsed_seconds,
                    (completed_at - activity.started_at).total_seconds(),
                )
            activity.updated_at = completed_at
            self._write_model(project_id, "analysis_activity.json", activity)

    def _read_analysis_activity(self, project_id: str) -> AnalysisActivityView:
        activity = self._read_model(project_id, "analysis_activity.json", AnalysisActivityView)
        return activity or AnalysisActivityView(project_id=project_id)

    def _analyze(self, project_id: str, max_analyzed_batches: int | None = None) -> bool:
        self._check_preparation_cancelled(project_id)
        source_path = self._find_source(project_id)
        text, _ = self._read_text(source_path)
        settings = self._read_analysis_settings(project_id)
        analysis_started_at = time.perf_counter()
        analysis_status = self.voice_analyzer.status()
        audit = self._read_model(project_id, "analysis_audit.json", AnalysisAudit)
        if audit is None:
            heading_candidates = find_heading_candidates(text)
            inferred_structure = TextStructureDraft(
                heading_ids=heuristic_heading_ids(heading_candidates),
                confidence=0.55,
                rationale="使用标题格式与排版边界预筛选章节",
                backend="rules",
            )
            warnings: list[str] = []
            should_confirm_structure = (
                len(text) >= settings.long_text_threshold
                and settings.mode != "characters"
                and not CHAPTER_PATTERN.search(text)
                and len(heading_candidates) >= 2
            )
            if should_confirm_structure:
                self._log_analysis_progress(
                    project_id,
                    2,
                    f"正在确认 {len(heading_candidates)} 个疑似章节标题",
                    analysis_status.backend,
                    analysis_status.model,
                    elapsed_seconds=time.perf_counter() - analysis_started_at,
                    workers=self._project_analysis_parallelism(project_id, analysis_status),
                )
                analyzer = getattr(self.voice_analyzer, "analyze_text_structure", None)
                if callable(analyzer):
                    try:
                        self._check_preparation_cancelled(project_id)
                        inferred_structure = analyzer(project_id, heading_candidates, len(text))
                        self._check_preparation_cancelled(project_id)
                    except VoiceAnalysisError as error:
                        warnings.append(f"章节标题模型确认失败，已使用规则候选：{error}")
            plan = build_long_form_plan(
                text,
                settings,
                inferred_candidates=heading_candidates,
                inferred_structure=inferred_structure,
            )
            if plan.warning:
                warnings.append(plan.warning)
            if plan.strategy == "characters" and plan.is_long_form:
                warnings.append("长文本未使用章节切分；每批均在完整句边界收束")
            if not CHAPTER_PATTERN.search(text) and plan.strategy != "inferred_chapters":
                warnings.append("未识别到可用章节标题，导演文件将按文本批次标记章节")
            audit = AnalysisAudit(
                project_id=project_id,
                source_file=source_path.name,
                structure=AnalysisStructure(
                    chapter_count=max(len(CHAPTER_PATTERN.findall(text)), plan.total_chapters),
                    character_count=len(text),
                    nonempty_line_count=sum(bool(line.strip()) for line in text.splitlines()),
                    estimated_segment_count=0,
                    dialogue_count=0,
                ),
                candidates=[],
                long_form_plan=plan,
                warnings=warnings,
            )
            self._write_model(project_id, "analysis_audit.json", audit)
            self._remove_artifact(project_id, "character_voice_bible.json")
            self._remove_artifact(project_id, "reference_plan.json")
            self._remove_artifact(project_id, "emotion_plan.json")
            self._remove_artifact(project_id, "director_doc.json")
            self._remove_artifact(project_id, "character_analysis_checkpoint.json")
            self._remove_artifact(project_id, "director_analysis_checkpoint.json")
        if audit.long_form_plan is None:
            raise PreparationProblem(500, "长篇切片计划不存在")

        plan = audit.long_form_plan
        windows = windows_from_plan(text, plan)
        target_count = len(windows) if max_analyzed_batches is None else min(len(windows), max_analyzed_batches)
        merged = {self._alias_key(candidate.display_name): candidate for candidate in audit.candidates}
        for window in windows[:target_count]:
            if window.batch.state != "pending":
                continue
            self._check_preparation_cancelled(project_id)
            window_candidates = self._scan_candidates(window.text)
            audit.structure.estimated_segment_count += len(self._split_sentences(window.text))
            audit.structure.dialogue_count += len(re.findall(r"[“\"『「].+?[”\"』」]", window.text, re.DOTALL))
            window.batch.candidate_ids = [candidate.candidate_id for candidate in window_candidates]
            for candidate in window_candidates:
                candidate.peak_batch_mentions = candidate.mention_count
                candidate.peak_batch_dialogue_count = candidate.dialogue_count
                candidate.batch_presence_count = 1
                candidate.batch_ids = [window.batch.batch_id]
                if window.batch.index == 1:
                    candidate.first_batch_mentions = candidate.mention_count
                    candidate.first_batch_dialogue_count = candidate.dialogue_count
                key = self._alias_key(candidate.display_name)
                existing = merged.get(key)
                if existing is None:
                    merged[key] = candidate
                    continue
                previous_mentions = existing.mention_count
                previous_dialogue = existing.dialogue_count
                existing.mention_count += candidate.mention_count
                existing.dialogue_count += candidate.dialogue_count
                existing.peak_batch_mentions = max(existing.peak_batch_mentions, candidate.peak_batch_mentions)
                existing.peak_batch_dialogue_count = max(
                    existing.peak_batch_dialogue_count,
                    candidate.peak_batch_dialogue_count,
                )
                existing.batch_presence_count += 1
                existing.batch_ids = list(dict.fromkeys([*existing.batch_ids, window.batch.batch_id]))
                existing.confidence = max(existing.confidence, candidate.confidence)
                if window.batch.index == 1:
                    existing.first_batch_mentions += candidate.mention_count
                    existing.first_batch_dialogue_count += candidate.dialogue_count
                if candidate.decision == "pending":
                    existing.decision = "pending"
                if (
                    existing.screening_action == "reject"
                    and not self._is_clear_non_entity(existing.display_name)
                    and (
                        existing.dialogue_count > previous_dialogue
                        or existing.mention_count >= max(3, previous_mentions * 2)
                    )
                ):
                    existing.decision = "pending"
                    existing.screening_route = None
                    existing.screening_action = None
                    existing.canonical_candidate_id = None
                    existing.screening_confidence = None
                    existing.screening_rationale = None
                    existing.reason = "后续切片补充了新的角色证据，等待重新粗筛"
                existing.evidence = list(dict.fromkeys([*existing.evidence, *candidate.evidence]))[:8]
            window.batch.state = "analyzed"
            audit.candidates = list(merged.values())
            self._score_candidates(audit.candidates, max(1, sum(batch.state != "pending" for batch in plan.batches)))
            self._log_analysis_progress(
                project_id,
                max(3, round(sum(batch.state != "pending" for batch in plan.batches) * 95 / max(len(windows), 1))),
                f"已完成文本批次 {window.batch.index}/{len(windows)}：{window.batch.title}",
                plan.detection_backend,
                plan.detection_model,
                elapsed_seconds=time.perf_counter() - analysis_started_at,
                batches=len(windows),
                workers=self._project_analysis_parallelism(project_id, analysis_status),
                current_batch=window.batch.index,
                total_batches=len(windows),
            )
            self._write_model(project_id, "analysis_audit.json", audit)
            self._check_preparation_cancelled(project_id)
        complete = all(batch.state != "pending" for batch in plan.batches)
        if complete and not any(candidate.decision == "pending" for candidate in audit.candidates):
            audit.warnings.append("未识别到具名说话人，请在角色审核阶段人工补充")
        audit.warnings = list(dict.fromkeys(audit.warnings))
        self._check_preparation_cancelled(project_id)
        self._write_model(project_id, "analysis_audit.json", audit)
        return complete

    def _extract_characters(self, project_id: str) -> None:
        self._check_preparation_cancelled(project_id)
        analysis_started_at = time.perf_counter()
        source_path = self._find_source(project_id)
        text, _ = self._read_text(source_path)
        audit = self._require_model(project_id, "analysis_audit.json", AnalysisAudit, "请先分析文档")
        existing_bible = self._read_model(project_id, "character_voice_bible.json", CharacterVoiceBible)
        existing_reference_plan = self._read_model(project_id, "reference_plan.json", ReferencePlan)
        existing_character_by_id = {
            character.character_id: character
            for character in existing_bible.characters
        } if existing_bible is not None else {}
        analysis_status = self.voice_analyzer.status()
        if analysis_status.backend == "hybrid":
            self._invalidate_unrelated_candidate_merges(audit)
            audit = self._screen_character_candidates(project_id, audit, analysis_status)
        self._check_preparation_cancelled(project_id)
        accepted = [candidate for candidate in audit.candidates if candidate.decision != "rejected"]
        for candidate in accepted:
            candidate.decision = "accepted"
        self._write_model(project_id, "analysis_audit.json", audit)

        canonical_candidates: list[CharacterCandidate] = []
        aliases: dict[str, list[str]] = {}
        accepted_by_id = {candidate.candidate_id: candidate for candidate in accepted}
        merged_aliases: dict[str, list[str]] = {}
        for candidate in audit.candidates:
            if candidate.screening_action == "merge" and candidate.canonical_candidate_id in accepted_by_id:
                merged_aliases.setdefault(candidate.canonical_candidate_id, []).append(candidate.display_name)
        for candidate in sorted(accepted, key=lambda item: -len(item.display_name)):
            canonical = next(
                (
                    existing
                    for existing in canonical_candidates
                    if self._alias_key(existing.display_name).endswith(self._alias_key(candidate.display_name))
                ),
                None,
            )
            if canonical is not None:
                aliases.setdefault(canonical.candidate_id, []).append(candidate.display_name)
            else:
                canonical_candidates.append(candidate)
        for canonical_id, names in merged_aliases.items():
            aliases.setdefault(canonical_id, []).extend(names)
        aliases = {key: list(dict.fromkeys(values)) for key, values in aliases.items()}
        canonical_candidates.sort(key=lambda item: audit.candidates.index(item))
        canonical_character_ids = {
            candidate.candidate_id: self._stable_id("character", candidate.display_name)
            for candidate in canonical_candidates
        }
        existing_character_ids = set(existing_character_by_id)
        if audit.long_form_plan is not None:
            for batch in audit.long_form_plan.batches:
                batch_character_ids = {
                    canonical_character_ids[candidate.candidate_id]
                    for candidate in canonical_candidates
                    if candidate.candidate_id in canonical_character_ids
                    and batch.batch_id in candidate.batch_ids
                }
                batch.reused_character_count = len(batch_character_ids & existing_character_ids)
                batch.new_character_count = len(batch_character_ids - existing_character_ids)
        immediate_candidate_ids = self._immediate_profile_candidate_ids(audit, canonical_candidates)
        deferred_candidates = [
            candidate for candidate in canonical_candidates if candidate.candidate_id not in immediate_candidate_ids
        ]
        immediate_candidates = [
            candidate for candidate in canonical_candidates if candidate.candidate_id in immediate_candidate_ids
        ]
        canonical_candidates = [*immediate_candidates, *deferred_candidates]

        peak_mentions = max((candidate.mention_count for candidate in canonical_candidates), default=1)
        analysis_label = {
            "cloud": "云端角色音色分析",
            "hybrid": "本地初筛与云端角色精推",
            "local": "本地角色音色分析",
            "rules": "规则角色音色分析",
        }[analysis_status.backend]
        analysis_parallelism = self._project_analysis_parallelism(project_id, analysis_status)
        profile_progress_start = 18 if analysis_status.backend == "hybrid" else 0
        profile_progress_span = 72 if analysis_status.backend == "hybrid" else 90
        total_candidates = len(canonical_candidates)
        checkpoint = self._read_model(
            project_id,
            "character_analysis_checkpoint.json",
            CharacterAnalysisCheckpoint,
        ) or CharacterAnalysisCheckpoint(project_id=project_id)
        self._log_analysis_progress(
            project_id,
            profile_progress_start,
            f"{analysis_label}开始",
            analysis_status.backend,
            analysis_status.model,
            characters=total_candidates,
            workers=analysis_parallelism,
        )

        default_narrator = CharacterVoice(
                character_id="narrator",
                display_name="旁白",
                confidence=1,
                importance=1,
                tier=CharacterTier.core,
                personality_tags=["叙事"],
                timbre_tags=["中音区", "适中", "均衡", "干净", "混合共鸣"],
                delivery_tags=["吐字清晰", "气息平稳", "语速平稳", "停连分明", "动态自然"],
                voice_constraints=["避免播音腔", "中性参考不携带场景情绪"],
                voice_prompt=(
                    "长篇旁白通用基线：成年中性叙述声线，中音区、重量适中、明暗均衡、混合共鸣；"
                    "吐字清楚，气息平稳，语速从容，停连服从句意，动态克制；场景、信息层级与对话转述清晰，"
                    "跨章节保持音高、响度、共鸣位置和节奏一致；避免播音腔、广告腔和夸张角色表演。"
                ),
                voice_profile_confidence=1,
                voice_profile_rationale="旁白使用项目稳定叙述基线。",
            )
        characters = [
            existing_character_by_id.get("narrator", default_narrator).model_copy(deep=True)
        ]
        analysis_backend: Literal["local", "hybrid", "cloud", "rules"] = (
            existing_bible.analysis_backend if existing_bible is not None else analysis_status.backend
        )
        analysis_model: str | None = (
            existing_bible.analysis_model if existing_bible is not None else analysis_status.model
        )
        tasks: list[_CharacterAnalysisTask] = []
        deferred_tasks: list[_CharacterAnalysisTask] = []
        for candidate_index, candidate in enumerate(canonical_candidates, start=1):
            importance = candidate.production_priority or candidate.local_importance or min(
                0.95,
                0.25 + 0.7 * candidate.mention_count / peak_mentions,
            )
            tier = CharacterTier.core if importance >= 0.75 else CharacterTier.supporting
            character_id = self._stable_id("character", candidate.display_name)
            gender_hint = self._infer_gender(candidate.display_name, candidate.evidence)
            candidate_aliases = aliases.get(candidate.candidate_id, [])
            existing_character = existing_character_by_id.get(character_id)
            if existing_character is not None:
                reused = existing_character.model_copy(deep=True)
                reused.aliases = list(dict.fromkeys([*reused.aliases, *candidate_aliases]))
                reused.confidence = max(reused.confidence, candidate.confidence)
                reused.importance = round(max(reused.importance, importance), 3)
                if reused.importance >= 0.75:
                    reused.tier = CharacterTier.core
                new_evidence = [
                    CharacterEvidence(
                        chapter_id=candidate.batch_ids[min(index, len(candidate.batch_ids) - 1)],
                        segment_id=f"evidence-{index + 1:03d}",
                        text=evidence,
                        evidence_type="dialogue",
                    )
                    for index, evidence in enumerate(candidate.evidence[:5])
                ] if candidate.batch_ids else []
                evidence_by_text = {item.text: item for item in [*reused.evidence, *new_evidence]}
                reused.evidence = list(evidence_by_text.values())[:8]
                characters.append(reused)
                continue
            task = _CharacterAnalysisTask(
                    candidate_index=candidate_index,
                    candidate=candidate,
                    aliases=candidate_aliases,
                    character_id=character_id,
                    importance=importance,
                    tier=tier,
                    evidence_pack=CharacterEvidencePack(
                        project_id=project_id,
                        character_id=character_id,
                        display_name=candidate.display_name,
                        aliases=candidate_aliases,
                        mention_count=candidate.mention_count,
                        dialogue_count=candidate.dialogue_count,
                        gender_hint=gender_hint,
                        evidence=candidate.evidence[:8],
                        local_screening=(
                            "本地候选粗筛已确认这是应进入云端精推的规范角色；"
                            f"置信度={candidate.screening_confidence or candidate.confidence:.2f}；"
                            f"依据={candidate.screening_rationale or candidate.reason}。"
                            if analysis_status.backend == "hybrid" and candidate.screening_action == "keep"
                            else None
                        ),
                    ),
                )
            if candidate.candidate_id in immediate_candidate_ids:
                tasks.append(task)
            else:
                deferred_tasks.append(task)
        total_profile_tasks = len(tasks)

        def analyze_task(task: _CharacterAnalysisTask) -> tuple[CharacterVoiceProfile, float]:
            self._check_preparation_cancelled(project_id)
            candidate_started_at = time.perf_counter()
            if analysis_parallelism == 1:
                progress_before = profile_progress_start + round(
                    (task.candidate_index - 1) * profile_progress_span / max(total_profile_tasks, 1)
                )
                self._log_analysis_progress(
                    project_id,
                    progress_before,
                    f"正在分析 {task.candidate_index}/{total_profile_tasks}：{task.candidate.display_name}",
                    analysis_status.backend,
                    analysis_status.model,
                    elapsed_seconds=time.perf_counter() - analysis_started_at,
                    workers=analysis_parallelism,
                )
            profile = self._analyze_character_profile_with_retries(task.evidence_pack)
            return profile, time.perf_counter() - candidate_started_at

        analyzed: dict[int, tuple[_CharacterAnalysisTask, CharacterVoiceProfile, float]] = {}
        pending_tasks: list[_CharacterAnalysisTask] = []
        for task in tasks:
            cached_profile = checkpoint.profiles.get(task.character_id)
            if cached_profile is None:
                pending_tasks.append(task)
            else:
                analyzed[task.candidate_index] = (task, cached_profile, 0.0)
        tasks = pending_tasks
        completed_count = len(analyzed)

        def cache_profile(task: _CharacterAnalysisTask, profile: CharacterVoiceProfile) -> None:
            checkpoint.profiles[task.character_id] = profile
            checkpoint.updated_at = datetime.now(timezone.utc)
            self._write_model(project_id, "character_analysis_checkpoint.json", checkpoint)

        failed_profile_errors: dict[str, str] = {}

        def defer_failed_profile(task: _CharacterAnalysisTask, error: VoiceAnalysisError) -> None:
            nonlocal completed_count
            completed_count += 1
            detail = (
                f"{task.candidate.display_name} 音色画像重试 {CHARACTER_PROFILE_MAX_ATTEMPTS} 次仍失败，"
                f"已跳过并暂用原型声线：{error}"
            )
            failed_profile_errors[task.character_id] = str(error)
            deferred_tasks.append(task)
            audit.warnings.append(detail)
            self._log_analysis_progress(
                project_id,
                profile_progress_start + round(
                    completed_count * profile_progress_span / max(total_profile_tasks, 1)
                ),
                f"已跳过 {completed_count}/{total_profile_tasks}：{task.candidate.display_name}",
                analysis_status.backend,
                analysis_status.model,
                elapsed_seconds=time.perf_counter() - analysis_started_at,
                workers=analysis_parallelism,
            )

        if analysis_parallelism > 1 and len(tasks) > 1:
            first_task = tasks[0]
            self._log_analysis_progress(
                project_id,
                profile_progress_start,
                f"正在分析 1/{total_profile_tasks}：{first_task.candidate.display_name}（并发队列已启动）",
                analysis_status.backend,
                analysis_status.model,
                elapsed_seconds=time.perf_counter() - analysis_started_at,
                workers=analysis_parallelism,
            )
            with ThreadPoolExecutor(
                max_workers=min(analysis_parallelism, len(tasks)),
                thread_name_prefix="zw-cloud-character",
            ) as executor:
                futures: dict[Future[tuple[CharacterVoiceProfile, float]], _CharacterAnalysisTask] = {
                    executor.submit(analyze_task, task): task
                    for task in tasks
                }
                try:
                    for future in as_completed(futures):
                        task = futures[future]
                        try:
                            profile, role_seconds = future.result()
                        except VoiceAnalysisError as error:
                            defer_failed_profile(task, error)
                            self._check_preparation_cancelled(project_id)
                            continue
                        completed_count += 1
                        analyzed[task.candidate_index] = (task, profile, role_seconds)
                        cache_profile(task, profile)
                        self._log_analysis_progress(
                            project_id,
                            profile_progress_start + round(
                                completed_count * profile_progress_span / max(total_profile_tasks, 1)
                            ),
                            f"已完成 {completed_count}/{total_profile_tasks}：{task.candidate.display_name}",
                            profile.backend,
                            profile.model,
                            elapsed_seconds=time.perf_counter() - analysis_started_at,
                            role_seconds=role_seconds,
                            workers=analysis_parallelism,
                        )
                        self._check_preparation_cancelled(project_id)
                except PreparationCancelled:
                    for pending in futures:
                        pending.cancel()
                    raise
        else:
            for task in tasks:
                self._check_preparation_cancelled(project_id)
                try:
                    profile, role_seconds = analyze_task(task)
                except VoiceAnalysisError as error:
                    defer_failed_profile(task, error)
                    self._check_preparation_cancelled(project_id)
                    continue
                completed_count += 1
                analyzed[task.candidate_index] = (task, profile, role_seconds)
                cache_profile(task, profile)
                self._log_analysis_progress(
                    project_id,
                    profile_progress_start + round(
                        completed_count * profile_progress_span / max(total_profile_tasks, 1)
                    ),
                    f"已完成 {completed_count}/{total_profile_tasks}：{task.candidate.display_name}",
                    profile.backend,
                    profile.model,
                    elapsed_seconds=time.perf_counter() - analysis_started_at,
                    role_seconds=role_seconds,
                    workers=analysis_parallelism,
                )
                self._check_preparation_cancelled(project_id)

        self._check_preparation_cancelled(project_id)
        for candidate_index in sorted(analyzed):
            task, profile, _ = analyzed[candidate_index]
            candidate = task.candidate
            analysis_backend = profile.backend
            analysis_model = profile.model
            characters.append(
                CharacterVoice(
                    character_id=task.character_id,
                    display_name=candidate.display_name,
                    aliases=task.aliases,
                    confidence=candidate.confidence,
                    importance=round(task.importance, 3),
                    tier=task.tier,
                    gender=profile.gender,
                    age_range=profile.age_range,
                    personality_tags=profile.personality_tags,
                    timbre_tags=profile.timbre_tags,
                    delivery_tags=profile.delivery_tags,
                    voice_constraints=profile.voice_constraints,
                    voice_prompt=profile.voice_prompt,
                    voice_profile_confidence=profile.confidence,
                    voice_profile_rationale=profile.rationale,
                    evidence=[
                        CharacterEvidence(
                            chapter_id="chapter-unknown",
                            segment_id=f"evidence-{index + 1:03d}",
                            text=evidence,
                            evidence_type="dialogue",
                        )
                        for index, evidence in enumerate(candidate.evidence[:5])
                    ],
                )
            )
        for task in deferred_tasks:
            candidate = task.candidate
            gender = task.evidence_pack.gender_hint
            failed_error = failed_profile_errors.get(task.character_id)
            characters.append(
                CharacterVoice(
                    character_id=task.character_id,
                    display_name=candidate.display_name,
                    aliases=task.aliases,
                    confidence=candidate.confidence,
                    importance=round(task.importance, 3),
                    tier=CharacterTier.minor,
                    gender=gender,
                    age_range="adult",
                    personality_tags=["画像失败待人工复核"] if failed_error else ["待后台精推"],
                    timbre_tags=["中音区", "适中", "均衡", "干净", "混合共鸣"],
                    delivery_tags=["自然口语咬字", "气息平稳", "语速平稳", "动态自然"],
                    voice_constraints=(
                        ["画像重试失败，暂用原型声线", "中性参考不携带场景情绪"]
                        if failed_error
                        else ["暂用原型声线", "中性参考不携带场景情绪"]
                    ),
                    voice_prompt=self._voice_prompt(gender),
                    voice_profile_confidence=max(0.25, min(candidate.entity_confidence, 0.55)),
                    voice_profile_rationale=(
                        f"音色画像重试 {CHARACTER_PROFILE_MAX_ATTEMPTS} 次失败，已跳过自动精推：{failed_error}"
                        if failed_error
                        else "长篇首切片优先模式：角色身份已保留，完整音色画像等待后台提升。"
                    ),
                    archetype_id=f"archetype-{gender}-adult",
                    evidence=[
                        CharacterEvidence(
                            chapter_id="chapter-unknown",
                            segment_id=f"evidence-{index + 1:03d}",
                            text=evidence,
                            evidence_type="dialogue",
                        )
                        for index, evidence in enumerate(candidate.evidence[:5])
                    ],
                )
            )
        bible = CharacterVoiceBible(
            project_id=project_id,
            source_text=f"input/{source_path.name}",
            analysis_backend=analysis_backend,
            analysis_model=analysis_model,
            characters=characters,
        )
        audit.warnings = list(dict.fromkeys(audit.warnings))
        self._check_preparation_cancelled(project_id)
        self._write_model(project_id, "character_voice_bible.json", bible)
        generated_reference_plan = self._build_reference_plan(project_id, bible)
        self._write_model(
            project_id,
            "reference_plan.json",
            self._merge_reference_plan(existing_reference_plan, generated_reference_plan),
        )
        if audit.long_form_plan is not None:
            for batch in audit.long_form_plan.batches:
                if batch.state == "analyzed":
                    batch.state = "characters_ready"
            self._write_model(project_id, "analysis_audit.json", audit)
        if existing_bible is None:
            self._remove_artifact(project_id, "emotion_plan.json")
            self._remove_artifact(project_id, "director_doc.json")
        self._remove_artifact(project_id, "character_analysis_checkpoint.json")
        self._log_analysis_progress(
            project_id,
            100,
            f"{analysis_label}完成",
            analysis_backend,
            analysis_model,
            elapsed_seconds=time.perf_counter() - analysis_started_at,
            characters=total_candidates,
            workers=analysis_parallelism,
        )

    @staticmethod
    def _immediate_profile_candidate_ids(
        audit: AnalysisAudit,
        candidates: list[CharacterCandidate],
    ) -> set[str]:
        plan = audit.long_form_plan
        if plan is None or not plan.is_long_form:
            return {candidate.candidate_id for candidate in candidates}
        first_window_candidates = [
            candidate
            for candidate in candidates
            if candidate.first_batch_dialogue_count > 0 or candidate.first_batch_mentions > 0
        ]
        ranked = sorted(
            first_window_candidates or candidates,
            key=lambda candidate: (
                candidate.first_batch_dialogue_count > 0,
                candidate.production_priority,
                candidate.dialogue_count,
                candidate.batch_presence_count,
                candidate.entity_confidence,
            ),
            reverse=True,
        )
        return {candidate.candidate_id for candidate in ranked[:LONG_FORM_IMMEDIATE_PROFILE_LIMIT]}

    def _analyze_character_profile_with_retries(
        self,
        evidence_pack: CharacterEvidencePack,
    ) -> CharacterVoiceProfile:
        last_error: VoiceAnalysisError | None = None
        for attempt in range(1, CHARACTER_PROFILE_MAX_ATTEMPTS + 1):
            if evidence_pack.project_id:
                self._check_preparation_cancelled(evidence_pack.project_id)
            try:
                return self.voice_analyzer.analyze(evidence_pack)
            except VoiceAnalysisError as error:
                last_error = error
                self.runtime_logger.info(
                    "[ANALYSIS %s] %s 音色画像第 %d/%d 次失败：%s",
                    evidence_pack.project_id or "-",
                    evidence_pack.display_name,
                    attempt,
                    CHARACTER_PROFILE_MAX_ATTEMPTS,
                    error,
                )
                if attempt < CHARACTER_PROFILE_MAX_ATTEMPTS:
                    if evidence_pack.project_id:
                        self._check_preparation_cancelled(evidence_pack.project_id)
                    time.sleep(0.1 * attempt)
        raise last_error or VoiceAnalysisError("音色画像分析失败")

    @staticmethod
    def _window_character_evidence(text: str, names: list[str], limit: int = 8) -> list[str]:
        evidence: list[str] = []
        for name in names:
            if not name:
                continue
            for match in re.finditer(re.escape(name), text):
                start = max(0, match.start() - 100)
                end = min(len(text), match.end() + 180)
                excerpt = text[start:end].strip()
                if excerpt and excerpt not in evidence:
                    evidence.append(excerpt)
                if len(evidence) >= limit:
                    return evidence
        return evidence

    def _screen_character_candidates(
        self,
        project_id: str,
        audit: AnalysisAudit,
        analysis_status: VoiceAnalysisStatus,
    ) -> AnalysisAudit:
        self._apply_deterministic_candidate_routes(audit.candidates)
        self._refresh_candidate_screening_summary(audit, analysis_status)
        pending = sorted(
            [
            candidate
            for candidate in audit.candidates
            if candidate.decision != "rejected"
            and candidate.screening_route == "model"
            and candidate.screening_action is None
            ],
            key=lambda candidate: (
                candidate.production_priority,
                candidate.dialogue_count,
                candidate.mention_count,
                candidate.batch_presence_count,
                candidate.confidence,
            ),
            reverse=True,
        )
        if not pending:
            self._refresh_candidate_screening_summary(audit, analysis_status)
            self._write_model(project_id, "analysis_audit.json", audit)
            return audit
        screen = getattr(self.voice_analyzer, "screen_character_candidates", None)
        if not callable(screen):
            raise PreparationProblem(503, "当前混合分析器不支持本地角色候选粗筛")
        original_count = len(pending)
        completed_count = 0
        self._log_analysis_progress(
            project_id,
            0,
            (
                f"角色候选分流完成：模型复核 {len(pending)}，"
                f"规则保留 {audit.candidate_deterministic_kept_count}，"
                f"规则排除 {audit.candidate_deterministic_rejected_count}"
            ),
            "local",
            analysis_status.model,
            characters=original_count,
            workers=1,
        )
        candidate_by_id = {candidate.candidate_id: candidate for candidate in audit.candidates}
        for start in range(0, len(pending), CHARACTER_SCREENING_BATCH_SIZE):
            self._check_preparation_cancelled(project_id)
            batch = pending[start : start + CHARACTER_SCREENING_BATCH_SIZE]
            anchors = self._candidate_screening_anchors(audit.candidates, batch)
            draft = screen(
                project_id,
                [self._candidate_screening_input(candidate) for candidate in batch],
                [self._candidate_screening_input(candidate) for candidate in anchors],
            )
            self._apply_candidate_screening_draft(candidate_by_id, batch, draft)
            completed_count += len(batch)
            self._refresh_candidate_screening_summary(audit, analysis_status, draft)
            self._write_model(project_id, "analysis_audit.json", audit)
            self._log_analysis_progress(
                project_id,
                min(18, round(completed_count * 18 / max(original_count, 1))),
                (
                    f"本地候选粗筛 {completed_count}/{original_count}："
                    f"保留 {audit.candidate_screening_kept_count}，"
                    f"合并 {audit.candidate_screening_merged_count}，"
                    f"排除 {audit.candidate_screening_rejected_count}"
                ),
                "local",
                draft.model or analysis_status.model,
                characters=original_count,
                workers=1,
            )
            self._check_preparation_cancelled(project_id)
        audit.candidate_screening_completed_at = datetime.now(timezone.utc)
        self._refresh_candidate_screening_summary(audit, analysis_status)
        self._write_model(project_id, "analysis_audit.json", audit)
        return audit

    @classmethod
    def _apply_deterministic_candidate_routes(cls, candidates: list[CharacterCandidate]) -> None:
        for candidate in candidates:
            if candidate.screening_action is not None:
                continue
            candidate.entity_confidence = max(
                candidate.entity_confidence,
                cls._candidate_entity_confidence(
                    candidate.display_name,
                    candidate.mention_count,
                    candidate.dialogue_count,
                    bool(candidate.dialogue_count),
                ),
            )
            if candidate.decision == "rejected" or cls._is_clear_non_entity(candidate.display_name):
                candidate.decision = "rejected"
                candidate.screening_route = "deterministic_reject"
                candidate.screening_action = "reject"
                candidate.screening_confidence = max(candidate.entity_confidence, 0.98)
                candidate.screening_rationale = "规则确认该候选是动作、情绪、通用身份词或无效名称片段"
                candidate.reason = f"确定性粗筛：{candidate.screening_rationale}"
                continue
            if cls._is_clear_character_candidate(candidate):
                candidate.decision = "accepted"
                candidate.screening_route = "deterministic_keep"
                candidate.screening_action = "keep"
                candidate.screening_confidence = max(candidate.entity_confidence, candidate.confidence)
                candidate.screening_rationale = "稳定姓名形态与多条直接说话证据相互印证"
                candidate.reason = f"确定性粗筛：{candidate.screening_rationale}"
                continue
            candidate.screening_route = "model"

    @classmethod
    def _is_clear_character_candidate(cls, candidate: CharacterCandidate) -> bool:
        name = candidate.display_name
        stable_name_shape = name[0] in COMMON_SURNAMES or name.startswith(COMPOUND_SURNAMES)
        return (
            stable_name_shape
            and candidate.dialogue_count >= 2
            and (
                candidate.batch_presence_count >= 2
                or candidate.mention_count >= 3
                or candidate.peak_batch_dialogue_count >= 2
            )
            and candidate.entity_confidence >= 0.72
        )

    @staticmethod
    def _candidate_screening_input(candidate: CharacterCandidate) -> CharacterCandidateScreeningInput:
        return CharacterCandidateScreeningInput(
            candidate_id=candidate.candidate_id,
            display_name=candidate.display_name,
            mention_count=candidate.mention_count,
            dialogue_count=candidate.dialogue_count,
            peak_batch_mentions=candidate.peak_batch_mentions,
            peak_batch_dialogue_count=candidate.peak_batch_dialogue_count,
            batch_presence_count=candidate.batch_presence_count,
            confidence=candidate.confidence,
            entity_confidence=candidate.entity_confidence,
            production_priority=candidate.production_priority,
            evidence=candidate.evidence[:4],
        )

    @staticmethod
    def _candidate_screening_anchors(
        candidates: list[CharacterCandidate],
        batch: list[CharacterCandidate],
    ) -> list[CharacterCandidate]:
        batch_ids = {candidate.candidate_id for candidate in batch}
        screened_kept = [
            candidate
            for candidate in candidates
            if candidate.candidate_id not in batch_ids
            and candidate.decision != "rejected"
            and candidate.screening_action == "keep"
        ]
        ranked = sorted(
            screened_kept,
            key=lambda candidate: (
                candidate.screening_action == "keep",
                candidate.dialogue_count,
                candidate.mention_count,
                candidate.batch_presence_count,
            ),
            reverse=True,
        )
        unique: dict[str, CharacterCandidate] = {}
        for candidate in ranked:
            unique.setdefault(candidate.candidate_id, candidate)
        return list(unique.values())[:16]

    @classmethod
    def _apply_candidate_screening_draft(
        cls,
        candidate_by_id: dict[str, CharacterCandidate],
        batch: list[CharacterCandidate],
        draft: CharacterCandidateScreeningDraft,
    ) -> None:
        decision_by_id = {decision.candidate_id: decision for decision in draft.decisions}
        if set(decision_by_id) != {candidate.candidate_id for candidate in batch}:
            raise PreparationProblem(503, "本地角色候选粗筛结果不完整")
        for candidate in batch:
            decision = decision_by_id[candidate.candidate_id]
            action = decision.action
            canonical_candidate_id = decision.canonical_candidate_id
            rationale = decision.rationale
            if action == "merge":
                target = candidate_by_id.get(canonical_candidate_id or "")
                if target is None or not cls._is_alias_merge(candidate.display_name, target.display_name):
                    action = "keep" if cls._looks_like_name(candidate.display_name) else "reject"
                    canonical_candidate_id = None
                    rationale = (
                        "候选名称与合并目标不存在可信别名关系，保守保留为独立角色"
                        if action == "keep"
                        else "候选名称与合并目标不存在可信别名关系，且名称形态不成立"
                    )
            candidate.screening_action = action
            candidate.screening_route = "model"
            candidate.canonical_candidate_id = canonical_candidate_id
            candidate.screening_confidence = decision.confidence
            candidate.screening_rationale = rationale
            candidate.reason = f"本地粗筛：{rationale}"
            if action in {"reject", "merge"}:
                candidate.decision = "rejected"
            else:
                candidate.decision = "accepted"

    @classmethod
    def _invalidate_unrelated_candidate_merges(cls, audit: AnalysisAudit) -> None:
        candidate_by_id = {candidate.candidate_id: candidate for candidate in audit.candidates}
        for candidate in audit.candidates:
            if candidate.screening_action != "merge":
                continue
            target = candidate_by_id.get(candidate.canonical_candidate_id or "")
            if target is not None and cls._is_alias_merge(candidate.display_name, target.display_name):
                continue
            candidate.canonical_candidate_id = None
            if cls._looks_like_name(candidate.display_name):
                candidate.decision = "pending"
                candidate.screening_route = "model"
                candidate.screening_action = None
                candidate.screening_confidence = None
                candidate.screening_rationale = None
                candidate.reason = "旧合并关系缺少可信名称依据，等待本地模型重新粗筛"
            else:
                candidate.decision = "rejected"
                candidate.screening_action = "reject"
                candidate.screening_confidence = 1.0
                candidate.screening_rationale = "旧合并关系无效，且候选名称形态不成立"
                candidate.reason = f"本地粗筛：{candidate.screening_rationale}"

    @classmethod
    def _is_alias_merge(cls, candidate_name: str, canonical_name: str) -> bool:
        candidate_key = cls._alias_key(candidate_name).strip()
        canonical_key = cls._alias_key(canonical_name).strip()
        if not candidate_key or not canonical_key:
            return False
        if candidate_key == canonical_key:
            return True
        if not cls._looks_like_name(candidate_name):
            return False
        return candidate_key in canonical_key or canonical_key in candidate_key

    @staticmethod
    def _refresh_candidate_screening_summary(
        audit: AnalysisAudit,
        analysis_status: VoiceAnalysisStatus,
        draft: CharacterCandidateScreeningDraft | None = None,
    ) -> None:
        model_screened = [
            candidate
            for candidate in audit.candidates
            if candidate.screening_route == "model" and candidate.screening_action is not None
        ]
        deterministic_kept = [
            candidate for candidate in audit.candidates if candidate.screening_route == "deterministic_keep"
        ]
        deterministic_rejected = [
            candidate for candidate in audit.candidates if candidate.screening_route == "deterministic_reject"
        ]
        screened = [candidate for candidate in audit.candidates if candidate.screening_action is not None]
        audit.schema_version = max(audit.schema_version, 4)
        audit.candidate_screening_backend = draft.backend if draft is not None else "local"
        audit.candidate_screening_model = draft.model if draft is not None else audit.candidate_screening_model or analysis_status.model
        audit.candidate_screening_input_count = len(model_screened)
        audit.candidate_deterministic_kept_count = len(deterministic_kept)
        audit.candidate_deterministic_rejected_count = len(deterministic_rejected)
        audit.candidate_screening_kept_count = sum(candidate.screening_action == "keep" for candidate in screened)
        audit.candidate_screening_rejected_count = sum(candidate.screening_action == "reject" for candidate in screened)
        audit.candidate_screening_merged_count = sum(candidate.screening_action == "merge" for candidate in screened)

    def _log_analysis_progress(
        self,
        project_id: str,
        percent: int,
        message: str,
        backend: str,
        model: str | None,
        *,
        elapsed_seconds: float | None = None,
        role_seconds: float | None = None,
        batch_seconds: float | None = None,
        characters: int | None = None,
        passages: int | None = None,
        batches: int | None = None,
        workers: int | None = None,
        batch_size: int | None = None,
        current_batch: int | None = None,
        total_batches: int | None = None,
    ) -> None:
        bounded_percent = max(0, min(100, percent))
        bar_width = 28
        filled = int(bar_width * bounded_percent / 100)
        progress_bar = "#" * filled + "-" * (bar_width - filled)
        details = [f"backend={backend}", f"model={model or '-'}"]
        if characters is not None:
            details.append(f"characters={characters}")
        if passages is not None:
            details.append(f"passages={passages}")
        if batches is not None:
            details.append(f"batches={batches}")
        if workers is not None:
            details.append(f"workers={workers}")
        if batch_size is not None:
            details.append(f"batch_size={batch_size}")
        if role_seconds is not None:
            details.append(f"role_elapsed={role_seconds:.1f}s")
        if batch_seconds is not None:
            details.append(f"batch_elapsed={batch_seconds:.1f}s")
        if elapsed_seconds is not None:
            details.append(f"elapsed={elapsed_seconds:.1f}s")
        with self._analysis_activity_lock:
            activity = self._read_analysis_activity(project_id)
            activity.state = "complete" if bounded_percent >= 100 else "running"
            activity.percent = bounded_percent
            activity.message = message
            activity.backend = backend
            activity.model = model
            if elapsed_seconds is not None:
                activity.elapsed_seconds = max(activity.elapsed_seconds, elapsed_seconds)
            if current_batch is not None:
                activity.current_batch = current_batch
            if total_batches is not None:
                activity.total_batches = total_batches
            activity.updated_at = datetime.now(timezone.utc)
            self._write_model(project_id, "analysis_activity.json", activity)
        self.runtime_logger.info(
            "[ANALYSIS %s] [%s] %3d%% %s | %s",
            project_id,
            progress_bar,
            bounded_percent,
            message,
            " ".join(details),
        )

    def _cloud_analysis_parallelism(self, status: VoiceAnalysisStatus) -> int:
        if status.backend not in {"cloud", "hybrid"}:
            return 1
        configured = getattr(self.voice_analyzer, "cloud_analysis_parallelism", None)
        if callable(configured):
            try:
                return max(1, min(8, int(configured())))
            except (TypeError, ValueError):
                pass
        return DEFAULT_CLOUD_PARALLELISM

    def _project_analysis_parallelism(self, project_id: str, status: VoiceAnalysisStatus) -> int:
        if status.backend not in {"cloud", "hybrid"}:
            return 1
        settings = self._read_analysis_settings(project_id)
        return max(1, min(8, settings.parallelism or DEFAULT_ANALYSIS_PARALLELISM))

    def _cloud_director_batch_size(self, status: VoiceAnalysisStatus) -> int:
        if status.backend not in {"cloud", "hybrid"}:
            return DIRECTOR_ANALYSIS_BATCH_SIZE
        configured = getattr(self.voice_analyzer, "cloud_director_batch_size", None)
        if callable(configured):
            try:
                return max(1, min(96, int(configured())))
            except (TypeError, ValueError):
                pass
        return DIRECTOR_CLOUD_ANALYSIS_BATCH_SIZE

    def update_reference(
        self,
        project_id: str,
        reference_id: str,
        selected: bool | None,
        voice_prompt: str | None,
        voice_prompt_locked: bool | None,
        custom_voice_attributes: str | None,
        reference_text: str | None,
    ) -> PreparationPreview:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "请先提取并审核角色",
            )
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            if selected is not None:
                if item.locked and item.selected != selected:
                    raise PreparationProblem(409, "自动生成项不能取消选择")
                item.selected = selected
            if voice_prompt is not None:
                prompt = voice_prompt.strip()
                if not prompt:
                    raise PreparationProblem(422, "声线描述不能为空")
                item.voice_prompt = prompt
                if item.selection_mode != "narrator_default":
                    bible = self._require_model(
                        project_id,
                        "character_voice_bible.json",
                        CharacterVoiceBible,
                        "角色圣经不存在",
                    )
                    character = next(
                        (candidate for candidate in bible.characters if candidate.character_id == item.source_character_id),
                        None,
                    )
                    if character is not None:
                        character.voice_prompt = prompt
                        self._write_model(project_id, "character_voice_bible.json", bible)
            if voice_prompt_locked is not None:
                if item.selection_mode != "narrator_default":
                    raise PreparationProblem(409, "只有默认男、女旁白需要单独解锁描述")
                item.voice_prompt_locked = voice_prompt_locked
            if custom_voice_attributes is not None:
                item.custom_voice_attributes = custom_voice_attributes.strip()
            if reference_text is not None:
                text = reference_text.strip()
                if not text:
                    raise PreparationProblem(422, "标准参考文本不能为空")
                self._append_reference_text_version(
                    item,
                    f"edited-{uuid.uuid4().hex[:12]}",
                    text,
                    "edited",
                )
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    def update_director_segment_voice(
        self,
        project_id: str,
        segment_id: str,
        voice_reference_id: str | None,
    ) -> PreparationPreview:
        with self._reference_lock:
            director = self._require_model(
                project_id,
                "director_doc.json",
                DirectorDocument,
                "请先生成导演文件",
            )
            segment = next((candidate for candidate in director.segments if candidate.segment_id == segment_id), None)
            if segment is None:
                raise PreparationProblem(404, "导演句子不存在")
            if voice_reference_id is not None:
                plan = self._require_model(
                    project_id,
                    "reference_plan.json",
                    ReferencePlan,
                    "参考计划不存在",
                )
                if not any(item.reference_id == voice_reference_id for item in plan.items):
                    raise PreparationProblem(404, "所选角色声线不存在")
            segment.voice_reference_id = voice_reference_id
            director.schema_version = DIRECTOR_SCHEMA_VERSION
            self._write_model(project_id, "director_doc.json", director)
        return self.preview(project_id)

    def delete_director_cache(self, project_id: str) -> PreparationPreview:
        self._find_source(project_id)
        self._remove_artifact(project_id, "director_doc.json")
        return self.preview(project_id)

    def regenerate_voice_profiles(
        self,
        project_id: str,
        character_id: str | None,
        reference_id: str | None,
        custom_attributes: str | None = None,
    ) -> PreparationPreview:
        with self._reference_lock:
            audit = self._require_model(
                project_id,
                "analysis_audit.json",
                AnalysisAudit,
                "请先分析文档",
            )
            bible = self._require_model(
                project_id,
                "character_voice_bible.json",
                CharacterVoiceBible,
                "请先提取并审核角色",
            )
            reference_plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "参考计划不存在",
            )
            reference_by_id = {item.reference_id: item for item in reference_plan.items}
            narrator_targets: list[ReferencePlanItem] = []
            if reference_id is not None:
                requested_reference = reference_by_id.get(reference_id)
                if requested_reference is None:
                    raise PreparationProblem(404, "参考计划项不存在")
                if requested_reference.selection_mode == "narrator_default":
                    if requested_reference.voice_prompt_locked:
                        raise PreparationProblem(409, "请先解锁该旁白的声线描述")
                    narrator_targets = [requested_reference]
                else:
                    character_id = requested_reference.source_character_id
                if custom_attributes is not None:
                    requested_reference.custom_voice_attributes = custom_attributes.strip()

            eligible = [character for character in bible.characters if character.character_id != "narrator"]
            if narrator_targets:
                targets: list[CharacterVoice] = []
            elif character_id is not None:
                targets = [character for character in eligible if character.character_id == character_id]
                if not targets:
                    if character_id == "narrator":
                        raise PreparationProblem(409, "默认旁白使用固定基准描述，不参与角色音色重分析")
                    raise PreparationProblem(404, "角色不存在")
            else:
                targets = eligible
                narrator_targets = [
                    item
                    for item in reference_plan.items
                    if item.selection_mode == "narrator_default" and not item.voice_prompt_locked
                ]
            reference_by_character = {
                item.source_character_id: item
                for item in reference_plan.items
                if item.selection_mode != "narrator_default"
            }
            if character_id is not None and custom_attributes is not None:
                target_reference = reference_by_character.get(character_id)
                if target_reference is not None:
                    target_reference.custom_voice_attributes = custom_attributes.strip()
            if not targets and not narrator_targets:
                raise PreparationProblem(409, "当前项目没有可重新分析的角色")

            generated: list[tuple[CharacterVoice, CharacterVoiceProfile]] = []
            for character in targets:
                candidate = next(
                    (item for item in audit.candidates if item.display_name == character.display_name),
                    None,
                )
                evidence = (
                    candidate.evidence[:8]
                    if candidate is not None and candidate.evidence
                    else [item.text for item in character.evidence[:8]]
                )
                if not evidence:
                    raise PreparationProblem(409, f"{character.display_name} 缺少可用于重分析的证据")
                try:
                    profile = self.voice_analyzer.analyze(
                        CharacterEvidencePack(
                            character_id=character.character_id,
                            display_name=character.display_name,
                            aliases=character.aliases,
                            mention_count=candidate.mention_count if candidate is not None else len(evidence),
                            dialogue_count=candidate.dialogue_count if candidate is not None else len(evidence),
                            gender_hint=character.gender,
                            evidence=evidence,
                            user_attributes=reference_by_character.get(character.character_id).custom_voice_attributes
                            if reference_by_character.get(character.character_id) is not None
                            else None,
                        )
                    )
                except VoiceAnalysisError as error:
                    raise PreparationProblem(503, f"{character.display_name} 的音色描述重新生成失败：{error}") from error
                generated.append((character, profile))

            generated_narrators: list[tuple[ReferencePlanItem, CharacterVoiceProfile]] = []
            for reference in narrator_targets:
                gender_label = "男性" if reference.gender == "male" else "女性"
                try:
                    profile = self.voice_analyzer.analyze(
                        CharacterEvidencePack(
                            character_id=reference.reference_id,
                            display_name=reference.display_name,
                            mention_count=1,
                            dialogue_count=0,
                            gender_hint=reference.gender,
                            user_attributes=reference.custom_voice_attributes or None,
                            evidence=[
                                f"这是长篇有声书的默认{gender_label}旁白，不属于故事角色。",
                                f"当前基础方向：{reference.voice_prompt}",
                                "稳定承担叙述、转场和信息组织，保持中性清晰，不绑定单一场景情绪。",
                                f"标准参考文本：{reference.reference_text}",
                            ],
                        )
                    )
                except VoiceAnalysisError as error:
                    raise PreparationProblem(503, f"{reference.display_name} 的声线描述重新生成失败：{error}") from error
                generated_narrators.append((reference, profile))

            narrator_by_gender = {
                item.gender: item.reference_id
                for item in reference_plan.items
                if item.selection_mode == "narrator_default"
            }
            changed_reference_ids: set[str] = set()
            for character, profile in generated:
                character.gender = profile.gender
                character.age_range = profile.age_range
                character.personality_tags = profile.personality_tags
                character.timbre_tags = profile.timbre_tags
                character.delivery_tags = profile.delivery_tags
                character.voice_constraints = profile.voice_constraints
                character.voice_prompt = profile.voice_prompt
                character.voice_profile_confidence = profile.confidence
                character.voice_profile_rationale = profile.rationale
                reference = reference_by_character.get(character.character_id)
                if reference is not None:
                    reference.gender = profile.gender
                    reference.voice_prompt = profile.voice_prompt
                    reference.reuse_reference_id = narrator_by_gender.get(
                        "female" if profile.gender == "female" else "male"
                    )
                    changed_reference_ids.add(reference.reference_id)
                bible.analysis_backend = profile.backend
                bible.analysis_model = profile.model

            for reference, profile in generated_narrators:
                reference.voice_prompt = profile.voice_prompt
                changed_reference_ids.add(reference.reference_id)
                bible.analysis_backend = profile.backend
                bible.analysis_model = profile.model

            emotion_plan = self._read_model(project_id, "emotion_plan.json", EmotionPlan)
            if emotion_plan is not None and changed_reference_ids:
                references = {item.reference_id: item for item in reference_plan.items}
                for item in emotion_plan.items:
                    if item.parent_reference_id not in changed_reference_ids:
                        continue
                    parent = references.get(item.parent_reference_id)
                    if parent is None:
                        continue
                    item.voice_prompt = parent.voice_prompt if item.selection_mode == "base" else (
                        f"{parent.voice_prompt}；情绪表现：{item.description}；强度 {item.intensity:.2f}"
                    )

            self._write_model(project_id, "character_voice_bible.json", bible)
            self._write_model(project_id, "reference_plan.json", reference_plan)
            if emotion_plan is not None:
                self._write_model(project_id, "emotion_plan.json", emotion_plan)
        return self.preview(project_id)

    @staticmethod
    def _activate_reference_text_version(item: ReferencePlanItem, version_id: str) -> None:
        version = next((candidate for candidate in item.reference_text_versions if candidate.version_id == version_id), None)
        if version is None:
            raise PreparationProblem(404, "标准参考文本版本不存在")
        item.active_reference_text_version_id = version.version_id
        item.reference_text = version.text

    @classmethod
    def _append_reference_text_version(
        cls,
        item: ReferencePlanItem,
        version_id: str,
        text: str,
        source: ReferenceTextSource,
    ) -> None:
        item.reference_text_versions.append(
            ReferenceTextVersion(
                version_id=version_id,
                text=text,
                source=source,
                created_at=datetime.now(timezone.utc),
            )
        )
        cls._activate_reference_text_version(item, version_id)

    @classmethod
    def _migrate_reference_text_versions(cls, plan: ReferencePlan) -> None:
        for item in plan.items:
            if item.reference_text and not item.reference_text_versions:
                legacy_id = f"initial-{hashlib.sha1(item.reference_text.encode('utf-8')).hexdigest()[:12]}"
                cls._append_reference_text_version(item, legacy_id, item.reference_text, "initial")
            elif item.reference_text_versions:
                active_id = item.active_reference_text_version_id
                if active_id is None or not any(
                    version.version_id == active_id for version in item.reference_text_versions
                ):
                    active_id = item.reference_text_versions[-1].version_id
                cls._activate_reference_text_version(item, active_id)

    def generate_reference_text(self, project_id: str, reference_id: str) -> PreparationPreview:
        with self._reference_lock:
            audit = self._require_model(project_id, "analysis_audit.json", AnalysisAudit, "请先分析文档")
            bible = self._require_model(
                project_id,
                "character_voice_bible.json",
                CharacterVoiceBible,
                "请先提取并审核角色",
            )
            plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "参考计划不存在",
            )
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            character = next(
                (candidate for candidate in bible.characters if candidate.character_id == item.source_character_id),
                None,
            )
            audit_candidate = next(
                (candidate for candidate in audit.candidates if candidate.display_name == item.display_name),
                None,
            )
            evidence = audit_candidate.evidence[:8] if audit_candidate is not None else []
            if not evidence and character is not None:
                evidence = [entry.text for entry in character.evidence[:8]]
            if not evidence:
                evidence = [
                    f"{item.display_name}承担长篇有声书的稳定叙述或角色表达。",
                    f"当前声线方向：{item.voice_prompt}",
                ]
            try:
                draft = self.voice_analyzer.generate_reference_text(
                    CharacterEvidencePack(
                        character_id=item.source_character_id,
                        display_name=item.display_name,
                        aliases=character.aliases if character is not None else [],
                        mention_count=audit_candidate.mention_count if audit_candidate is not None else len(evidence),
                        dialogue_count=audit_candidate.dialogue_count if audit_candidate is not None else 0,
                        gender_hint=item.gender,
                        evidence=evidence,
                        user_attributes=item.custom_voice_attributes or None,
                    ),
                    item.voice_prompt,
                )
            except VoiceAnalysisError as error:
                raise PreparationProblem(503, f"{item.display_name} 的标准参考文本生成失败：{error}") from error
            self._append_reference_text_version(
                item,
                f"generated-{uuid.uuid4().hex[:12]}",
                draft.text.strip(),
                "generated",
            )
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    def activate_reference_text_version(
        self,
        project_id: str,
        reference_id: str,
        version_id: str,
    ) -> PreparationPreview:
        with self._reference_lock:
            plan = self._require_model(project_id, "reference_plan.json", ReferencePlan, "参考计划不存在")
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            self._activate_reference_text_version(item, version_id)
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    def delete_reference_text_version(
        self,
        project_id: str,
        reference_id: str,
        version_id: str,
    ) -> PreparationPreview:
        with self._reference_lock:
            plan = self._require_model(project_id, "reference_plan.json", ReferencePlan, "参考计划不存在")
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            version_index = next(
                (index for index, version in enumerate(item.reference_text_versions) if version.version_id == version_id),
                None,
            )
            if version_index is None:
                raise PreparationProblem(404, "标准参考文本版本不存在")
            item.reference_text_versions.pop(version_index)
            if item.reference_text_versions:
                replacement = item.reference_text_versions[min(version_index, len(item.reference_text_versions) - 1)]
                self._activate_reference_text_version(item, replacement.version_id)
            else:
                item.active_reference_text_version_id = None
                item.reference_text = ""
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    @staticmethod
    def _activate_reference_audio_version(item: ReferencePlanItem, version_id: str) -> None:
        version = next((candidate for candidate in item.audio_versions if candidate.version_id == version_id), None)
        if version is None:
            raise PreparationProblem(404, "参考音频版本不存在")
        item.active_audio_version_id = version.version_id
        item.audio_url = version.audio_url
        item.audio_source = version.source
        item.status = "generated"
        item.error = None

    @classmethod
    def _append_reference_audio_version(
        cls,
        item: ReferencePlanItem,
        version_id: str,
        audio_url: str,
        source: ReferenceAudioSource,
        decision: ReferenceAudioDecision = "accepted",
    ) -> None:
        existing = next((version for version in item.audio_versions if version.version_id == version_id), None)
        if decision == "accepted":
            for version in item.audio_versions:
                if version.version_id != version_id and version.decision == "accepted":
                    version.decision = "superseded"
        if existing is None:
            item.audio_versions.append(
                ReferenceAudioVersion(
                    version_id=version_id,
                    audio_url=audio_url,
                    source=source,
                    decision=decision,
                    created_at=datetime.now(timezone.utc),
                )
            )
        else:
            existing.audio_url = audio_url
            existing.source = source
            existing.decision = decision
        cls._activate_reference_audio_version(item, version_id)

    @classmethod
    def _migrate_reference_audio_versions(cls, plan: ReferencePlan) -> None:
        for item in plan.items:
            if item.audio_url and not item.audio_versions:
                legacy_id = f"legacy-{hashlib.sha1(item.audio_url.encode('utf-8')).hexdigest()[:12]}"
                cls._append_reference_audio_version(
                    item,
                    legacy_id,
                    item.audio_url,
                    item.audio_source or "generated",
                    "accepted",
                )
            elif item.audio_versions:
                active_id = item.active_audio_version_id
                if active_id is None or not any(version.version_id == active_id for version in item.audio_versions):
                    active_id = item.audio_versions[-1].version_id
                cls._activate_reference_audio_version(item, active_id)

    def activate_reference_audio_version(
        self,
        project_id: str,
        reference_id: str,
        version_id: str,
    ) -> PreparationPreview:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "请先提取并审核角色",
            )
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            self._activate_reference_audio_version(item, version_id)
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    def review_reference_audio_version(
        self,
        project_id: str,
        reference_id: str,
        version_id: str,
        decision: Literal["accepted", "rejected"],
    ) -> PreparationPreview:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "请先提取并审核角色",
            )
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            version = next((candidate for candidate in item.audio_versions if candidate.version_id == version_id), None)
            if version is None:
                raise PreparationProblem(404, "参考音频版本不存在")
            if decision == "accepted":
                for candidate in item.audio_versions:
                    if candidate.version_id != version_id and candidate.decision == "accepted":
                        candidate.decision = "superseded"
                version.decision = "accepted"
                self._activate_reference_audio_version(item, version_id)
            else:
                version.decision = "rejected"
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    def delete_reference_audio_version(
        self,
        project_id: str,
        reference_id: str,
        version_id: str,
    ) -> PreparationPreview:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "请先提取并审核角色",
            )
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            version_index = next(
                (index for index, version in enumerate(item.audio_versions) if version.version_id == version_id),
                None,
            )
            if version_index is None:
                raise PreparationProblem(404, "参考音频版本不存在")
            version = item.audio_versions.pop(version_index)
            audio_path = self._reference_audio_path(version.audio_url)
            if audio_path is not None:
                audio_path.unlink(missing_ok=True)
            if item.audio_versions:
                replacement = item.audio_versions[min(version_index, len(item.audio_versions) - 1)]
                self._activate_reference_audio_version(item, replacement.version_id)
            else:
                item.active_audio_version_id = None
                item.audio_url = None
                item.audio_source = None
                item.status = "not_generated"
                item.job_id = None
                item.error = None
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    def clear_reference_audio_cache(
        self,
        project_id: str,
        reference_id: str,
    ) -> PreparationPreview:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "请先提取并审核角色",
            )
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            for version in item.audio_versions:
                audio_path = self._reference_audio_path(version.audio_url)
                if audio_path is not None:
                    audio_path.unlink(missing_ok=True)
            item.audio_versions = []
            item.active_audio_version_id = None
            item.audio_url = None
            item.audio_source = None
            item.status = "not_generated"
            item.job_id = None
            item.error = None
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    def _reference_audio_path(self, audio_url: str) -> Path | None:
        roots = (
            ("/media/outputs/audio/", (self.workspace_root / "outputs" / "audio").resolve()),
            ("/media/outputs/projects/", self.project_root.resolve()),
        )
        for prefix, root in roots:
            if not audio_url.startswith(prefix):
                continue
            candidate = (root / audio_url.removeprefix(prefix)).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                return None
            return candidate
        return None

    def _reference_audio_destination(
        self,
        project_id: str,
        reference_id: str,
        version_id: str,
    ) -> tuple[Path, str]:
        reference_key = hashlib.sha1(reference_id.encode("utf-8")).hexdigest()[:12]
        if self._read_project_manifest(project_id) is not None:
            target = self.project_root / project_id / "assets" / "references" / reference_key / f"{version_id}.wav"
            return target, f"/media/outputs/projects/{project_id}/assets/references/{reference_key}/{version_id}.wav"
        project_key = hashlib.sha1(project_id.encode("utf-8")).hexdigest()[:12]
        target = self.reference_audio_root / project_key / reference_key / f"{version_id}.wav"
        return target, f"/media/outputs/audio/references/{project_key}/{reference_key}/{version_id}.wav"

    def _copy_reference_audio(
        self,
        project_id: str,
        reference_id: str,
        version_id: str,
        source_path: Path,
    ) -> str:
        target, audio_url = self._reference_audio_destination(project_id, reference_id, version_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".wav.tmp")
        shutil.copyfile(source_path, temporary)
        temporary.replace(target)
        return audio_url

    def _materialize_generated_reference(
        self,
        project_id: str,
        reference_id: str,
        version_id: str,
        audio_url: str,
    ) -> str:
        if self._read_project_manifest(project_id) is None:
            return audio_url
        source_path = self._reference_audio_path(audio_url)
        if source_path is None or not source_path.is_file():
            return audio_url
        return self._copy_reference_audio(project_id, reference_id, version_id, source_path)

    def upload_reference_audio(
        self,
        project_id: str,
        reference_id: str,
        source: ReferenceAudioSource,
        content: bytes,
    ) -> PreparationPreview:
        self._find_source(project_id)
        if len(content) > MAX_REFERENCE_AUDIO_BYTES:
            raise PreparationProblem(413, "参考音频不能超过 64 MB")
        try:
            with wave.open(io.BytesIO(content), "rb") as audio:
                if audio.getnchannels() not in {1, 2} or audio.getsampwidth() not in {1, 2, 3, 4}:
                    raise wave.Error("unsupported PCM layout")
                if audio.getframerate() < 8_000 or audio.getnframes() <= 0:
                    raise wave.Error("empty or invalid sample rate")
        except (EOFError, wave.Error) as exc:
            raise PreparationProblem(415, "参考音频必须是有效的 PCM WAV 文件") from exc

        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "请先提取并审核角色",
            )
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            version_id = f"{source}-{uuid.uuid4().hex[:12]}"
            target, audio_url = self._reference_audio_destination(project_id, reference_id, version_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".wav.tmp")
            temporary.write_bytes(content)
            temporary.replace(target)
            self._append_reference_audio_version(item, version_id, audio_url, source, "accepted")
            item.job_id = None
            item.selected = True
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    def list_voice_resource_matches(
        self,
        project_id: str,
        reference_id: str,
        limit: int = 6,
    ) -> list[VoiceResourceMatch]:
        self._find_source(project_id)
        plan = self._require_model(project_id, "reference_plan.json", ReferencePlan, "请先提取并审核角色")
        target = next((item for item in plan.items if item.reference_id == reference_id), None)
        if target is None:
            raise PreparationProblem(404, "参考计划项不存在")
        target_bible = self._read_model(project_id, "character_voice_bible.json", CharacterVoiceBible)
        target_features = self._voice_resource_features(target, target_bible)
        matches: list[VoiceResourceMatch] = []
        for source in self.list_sources():
            if source.project_id == project_id:
                continue
            source_plan = self._read_model(source.project_id, "reference_plan.json", ReferencePlan)
            if source_plan is None:
                continue
            source_bible = self._read_model(source.project_id, "character_voice_bible.json", CharacterVoiceBible)
            for candidate in source_plan.items:
                if target.gender != "unknown" and candidate.gender != "unknown" and candidate.gender != target.gender:
                    continue
                active_version = next(
                    (version for version in candidate.audio_versions if version.version_id == candidate.active_audio_version_id),
                    candidate.audio_versions[-1] if candidate.audio_versions else None,
                )
                if active_version is None:
                    continue
                if active_version.decision != "accepted":
                    continue
                audio_path = self._reference_audio_path(active_version.audio_url)
                if audio_path is None or not audio_path.is_file():
                    continue
                similarity = self._voice_resource_similarity(
                    target_features,
                    self._voice_resource_features(candidate, source_bible),
                )
                if similarity < 0.08:
                    continue
                matches.append(
                    VoiceResourceMatch(
                        source_project_id=source.project_id,
                        source_project_name=source.display_name,
                        source_reference_id=candidate.reference_id,
                        source_version_id=active_version.version_id,
                        display_name=candidate.display_name,
                        gender=candidate.gender,
                        voice_prompt=candidate.voice_prompt,
                        audio_url=active_version.audio_url,
                        audio_source=active_version.source,
                        created_at=active_version.created_at,
                        similarity=similarity,
                    )
                )
        matches.sort(key=lambda item: (item.similarity, item.created_at), reverse=True)
        return matches[: max(1, min(limit, 20))]

    def reuse_voice_resource(
        self,
        project_id: str,
        reference_id: str,
        request: VoiceResourceReuseRequest,
    ) -> PreparationPreview:
        self._find_source(project_id)
        if request.source_project_id == project_id:
            raise PreparationProblem(409, "当前项目内请直接切换历史版本")
        with self._reference_lock:
            source_plan = self._require_model(
                request.source_project_id,
                "reference_plan.json",
                ReferencePlan,
                "来源项目没有可复用的参考计划",
            )
            source_item = next(
                (item for item in source_plan.items if item.reference_id == request.source_reference_id),
                None,
            )
            if source_item is None:
                raise PreparationProblem(404, "来源声线不存在")
            source_version = next(
                (version for version in source_item.audio_versions if version.version_id == request.source_version_id),
                None,
            )
            if source_version is None:
                raise PreparationProblem(404, "来源音频版本不存在")
            if source_version.decision != "accepted":
                raise PreparationProblem(409, "只能复用已经接受的标准参考音频")
            source_path = self._reference_audio_path(source_version.audio_url)
            if source_path is None or not source_path.is_file():
                raise PreparationProblem(404, "来源音频缓存已失效")

            plan = self._require_model(project_id, "reference_plan.json", ReferencePlan, "请先提取并审核角色")
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            version_id = f"reused-{uuid.uuid4().hex[:12]}"
            audio_url = self._copy_reference_audio(project_id, reference_id, version_id, source_path)
            self._append_reference_audio_version(item, version_id, audio_url, "reused", "accepted")
            item.job_id = None
            item.selected = True
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    @staticmethod
    def _voice_resource_features(
        item: ReferencePlanItem,
        bible: CharacterVoiceBible | None,
    ) -> str:
        character = next(
            (candidate for candidate in bible.characters if candidate.character_id == item.source_character_id),
            None,
        ) if bible is not None else None
        values = [item.voice_prompt, item.custom_voice_attributes]
        if character is not None:
            values.extend(character.personality_tags)
            values.extend(character.timbre_tags)
            values.extend(character.delivery_tags)
            values.extend(character.voice_constraints)
        return "；".join(value.strip() for value in values if value and value.strip())

    @staticmethod
    def _voice_resource_similarity(left: str, right: str) -> float:
        def tokens(value: str) -> set[str]:
            normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value.casefold())
            if not normalized:
                return set()
            grams = {normalized[index : index + 2] for index in range(max(1, len(normalized) - 1))}
            phrases = {
                phrase.strip().casefold()
                for phrase in re.split(r"[，。；、,:：/|\s]+", value)
                if len(phrase.strip()) >= 2
            }
            return grams | phrases

        left_tokens = tokens(left)
        right_tokens = tokens(right)
        if not left_tokens or not right_tokens:
            return 0.0
        return round(len(left_tokens & right_tokens) / len(left_tokens | right_tokens), 4)

    def update_reference_settings(
        self,
        project_id: str,
        automatic_threshold: float | None,
        automatic_items_locked: bool | None,
    ) -> PreparationPreview:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "请先提取并审核角色",
            )
            if automatic_threshold is not None:
                self._apply_reference_threshold(plan, automatic_threshold)
            if automatic_items_locked is not None:
                self._apply_reference_lock(plan, automatic_items_locked)
            self._write_model(project_id, "reference_plan.json", plan)
        return self.preview(project_id)

    @staticmethod
    def _apply_reference_threshold(plan: ReferencePlan, automatic_threshold: float) -> None:
        plan.automatic_threshold = automatic_threshold
        for item in plan.items:
            if item.selection_mode == "narrator_default":
                item.selected = True
                item.locked = True
                continue
            manually_selected = item.selection_mode == "optional" and item.selected
            automatic = item.importance >= automatic_threshold
            item.selection_mode = "automatic" if automatic else "optional"
            item.selected = automatic or manually_selected
            item.locked = automatic and plan.automatic_items_locked

    @staticmethod
    def _apply_reference_lock(plan: ReferencePlan, automatic_items_locked: bool) -> None:
        plan.automatic_items_locked = automatic_items_locked
        for item in plan.items:
            if item.selection_mode == "narrator_default":
                item.locked = True
            elif item.selection_mode == "automatic":
                item.locked = automatic_items_locked
            else:
                item.locked = False

    def record_reference_job(
        self,
        project_id: str,
        reference_id: str,
        job_id: str,
        status: Literal["queued", "running", "complete", "failed"],
        audio_url: str | None,
        error: str | None,
    ) -> None:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "参考计划不存在",
            )
            item = next((candidate for candidate in plan.items if candidate.reference_id == reference_id), None)
            if item is None:
                raise PreparationProblem(404, "参考计划项不存在")
            if status == "queued" and not item.selected:
                raise PreparationProblem(409, "请先将该角色加入参考生成")
            item.job_id = job_id
            item.status = "generated" if status == "complete" else status
            if status == "complete" and audio_url is not None:
                stored_url = self._materialize_generated_reference(project_id, reference_id, job_id, audio_url)
                self._append_reference_audio_version(item, job_id, stored_url, "generated", "provisional")
            item.error = error
            self._write_model(project_id, "reference_plan.json", plan)

    def update_emotion_settings(
        self,
        project_id: str,
        skipped: bool | None,
        automatic_threshold: float | None,
        automatic_items_locked: bool | None,
    ) -> PreparationPreview:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "emotion_plan.json",
                EmotionPlan,
                "请先完成角色参考计划",
            )
            if skipped is not None:
                plan.skipped = skipped
            if automatic_threshold is not None:
                self._apply_emotion_threshold(plan, automatic_threshold)
            if automatic_items_locked is not None:
                self._apply_emotion_lock(plan, automatic_items_locked)
            self._write_model(project_id, "emotion_plan.json", plan)
        return self.preview(project_id)

    def update_emotion_variant(
        self,
        project_id: str,
        variant_id: str,
        selected: bool,
    ) -> PreparationPreview:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "emotion_plan.json",
                EmotionPlan,
                "情绪生产计划不存在",
            )
            item = next((candidate for candidate in plan.items if candidate.variant_id == variant_id), None)
            if item is None:
                raise PreparationProblem(404, "情绪派生项不存在")
            if item.locked and item.selected != selected:
                raise PreparationProblem(409, "锁定的自动情绪项不能取消选择")
            item.selected = selected
            self._write_model(project_id, "emotion_plan.json", plan)
        return self.preview(project_id)

    def create_emotion_variant(
        self,
        project_id: str,
        parent_reference_id: str,
        emotion_name: str,
        description: str,
        intensity: float,
    ) -> PreparationPreview:
        with self._reference_lock:
            reference_plan = self._require_model(
                project_id,
                "reference_plan.json",
                ReferencePlan,
                "参考计划不存在",
            )
            parent = next(
                (item for item in reference_plan.items if item.reference_id == parent_reference_id and item.selected),
                None,
            )
            if parent is None:
                raise PreparationProblem(404, "父参考不存在或未加入生产")
            plan = self._require_model(
                project_id,
                "emotion_plan.json",
                EmotionPlan,
                "情绪生产计划不存在",
            )
            clean_name = emotion_name.strip()
            clean_description = description.strip()
            if not clean_name or not clean_description:
                raise PreparationProblem(422, "情绪名称和描述不能为空")
            duplicate = next(
                (
                    item for item in plan.items
                    if item.parent_reference_id == parent_reference_id and item.emotion_name == clean_name
                ),
                None,
            )
            if duplicate is not None:
                raise PreparationProblem(409, "该角色已存在同名情绪派生")
            plan.items.append(
                EmotionPlanItem(
                    variant_id=self._stable_id(
                        "emotion",
                        f"{project_id}:{parent_reference_id}:custom:{clean_name}",
                    ),
                    parent_reference_id=parent_reference_id,
                    source_character_id=parent.source_character_id,
                    display_name=parent.display_name,
                    emotion_name=clean_name,
                    description=clean_description,
                    intensity=intensity,
                    importance=parent.importance,
                    selection_mode="custom",
                    selected=True,
                    locked=False,
                    reference_text=REFERENCE_TEXT,
                    voice_prompt=f"{parent.voice_prompt}；情绪表现：{clean_description}；强度 {intensity:.2f}",
                )
            )
            self._write_model(project_id, "emotion_plan.json", plan)
        return self.preview(project_id)

    def delete_emotion_variant(self, project_id: str, variant_id: str) -> PreparationPreview:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "emotion_plan.json",
                EmotionPlan,
                "情绪生产计划不存在",
            )
            item = next((candidate for candidate in plan.items if candidate.variant_id == variant_id), None)
            if item is None:
                raise PreparationProblem(404, "情绪派生项不存在")
            if item.selection_mode != "custom":
                raise PreparationProblem(409, "只有自定义情绪派生可以删除")
            plan.items = [candidate for candidate in plan.items if candidate.variant_id != variant_id]
            self._write_model(project_id, "emotion_plan.json", plan)
        return self.preview(project_id)

    def record_emotion_job(
        self,
        project_id: str,
        variant_id: str,
        job_id: str,
        status: Literal["queued", "running", "complete", "failed"],
        audio_url: str | None,
        error: str | None,
    ) -> None:
        with self._reference_lock:
            plan = self._require_model(
                project_id,
                "emotion_plan.json",
                EmotionPlan,
                "情绪生产计划不存在",
            )
            item = next((candidate for candidate in plan.items if candidate.variant_id == variant_id), None)
            if item is None:
                raise PreparationProblem(404, "情绪派生项不存在")
            if item.selection_mode == "base":
                raise PreparationProblem(409, "自然基准直接复用父参考，无需生成")
            if status == "queued" and (plan.skipped or not item.selected):
                raise PreparationProblem(409, "请先启用并选择该情绪派生")
            item.job_id = job_id
            item.status = "generated" if status == "complete" else status
            item.audio_url = audio_url or item.audio_url
            item.error = error
            self._write_model(project_id, "emotion_plan.json", plan)

    @staticmethod
    def _apply_emotion_threshold(plan: EmotionPlan, automatic_threshold: float) -> None:
        plan.automatic_threshold = automatic_threshold
        for item in plan.items:
            if item.selection_mode in {"base", "custom"}:
                continue
            automatic = item.importance >= automatic_threshold
            item.selection_mode = "automatic" if automatic else "optional"
            item.selected = automatic
            item.locked = automatic and plan.automatic_items_locked

    @staticmethod
    def _apply_emotion_lock(plan: EmotionPlan, automatic_items_locked: bool) -> None:
        plan.automatic_items_locked = automatic_items_locked
        for item in plan.items:
            if item.selection_mode == "base":
                item.locked = True
            elif item.selection_mode == "automatic":
                item.locked = automatic_items_locked
            else:
                item.locked = False

    def _sync_emotion_plan(
        self,
        project_id: str,
        reference_plan: ReferencePlan,
        existing_plan: EmotionPlan | None,
    ) -> EmotionPlan:
        plan = existing_plan or EmotionPlan(project_id=project_id, items=[])
        existing_items = {item.variant_id: item for item in plan.items}
        valid_parent_ids = {item.reference_id for item in reference_plan.items if item.selected}
        synced: list[EmotionPlanItem] = []
        for parent in reference_plan.items:
            if not parent.selected:
                continue
            base_id = self._stable_id("emotion", f"{project_id}:{parent.reference_id}:natural")
            base = existing_items.get(base_id) or EmotionPlanItem(
                variant_id=base_id,
                parent_reference_id=parent.reference_id,
                source_character_id=parent.source_character_id,
                display_name=parent.display_name,
                emotion_name="自然",
                description="直接复用已确认的中性父参考",
                intensity=0,
                importance=parent.importance,
                selection_mode="base",
                selected=True,
                locked=True,
                reference_text=parent.reference_text,
                voice_prompt=parent.voice_prompt,
            )
            base.display_name = parent.display_name
            base.importance = parent.importance
            base.reference_text = parent.reference_text
            base.voice_prompt = parent.voice_prompt
            base.audio_url = parent.audio_url
            base.status = "generated" if parent.audio_url else "not_generated"
            base.error = parent.error
            synced.append(base)
            for emotion_name, description, intensity in DEFAULT_EMOTIONS:
                variant_id = self._stable_id(
                    "emotion",
                    f"{project_id}:{parent.reference_id}:{emotion_name}",
                )
                automatic = parent.importance >= plan.automatic_threshold
                item = existing_items.get(variant_id) or EmotionPlanItem(
                    variant_id=variant_id,
                    parent_reference_id=parent.reference_id,
                    source_character_id=parent.source_character_id,
                    display_name=parent.display_name,
                    emotion_name=emotion_name,
                    description=description,
                    intensity=intensity,
                    importance=parent.importance,
                    selection_mode="automatic" if automatic else "optional",
                    selected=automatic,
                    locked=automatic and plan.automatic_items_locked,
                    reference_text=REFERENCE_TEXT,
                    voice_prompt=f"{parent.voice_prompt}；情绪表现：{description}；强度 {intensity:.2f}",
                )
                item.display_name = parent.display_name
                item.importance = parent.importance
                item.voice_prompt = f"{parent.voice_prompt}；情绪表现：{item.description}；强度 {item.intensity:.2f}"
                synced.append(item)
        synced.extend(
            item
            for item in plan.items
            if item.selection_mode == "custom" and item.parent_reference_id in valid_parent_ids
        )
        plan.items = synced
        plan.schema_version = EMOTION_PLAN_SCHEMA_VERSION
        return plan

    def _build_reference_plan(self, project_id: str, bible: CharacterVoiceBible) -> ReferencePlan:
        male_reference_id = self._stable_id("reference", f"{project_id}:narrator:male")
        female_reference_id = self._stable_id("reference", f"{project_id}:narrator:female")
        items = [
            ReferencePlanItem(
                reference_id=male_reference_id,
                source_character_id="narrator",
                display_name="男旁白",
                gender="male",
                importance=1,
                selection_mode="narrator_default",
                selected=True,
                locked=True,
                voice_prompt_locked=True,
                reference_text=REFERENCE_TEXT,
                voice_prompt=MALE_NARRATOR_PROMPT,
            ),
            ReferencePlanItem(
                reference_id=female_reference_id,
                source_character_id="narrator",
                display_name="女旁白",
                gender="female",
                importance=1,
                selection_mode="narrator_default",
                selected=True,
                locked=True,
                voice_prompt_locked=True,
                reference_text=REFERENCE_TEXT,
                voice_prompt=FEMALE_NARRATOR_PROMPT,
            ),
        ]
        for character in bible.characters:
            if character.character_id == "narrator":
                continue
            automatic = (
                character.importance >= REFERENCE_GENERATION_THRESHOLD
                and character.archetype_id is None
            )
            gender = character.gender if character.gender in {"male", "female"} else "unknown"
            items.append(
                ReferencePlanItem(
                    reference_id=self._stable_id("reference", f"{project_id}:{character.character_id}:neutral"),
                    source_character_id=character.character_id,
                    display_name=character.display_name,
                    gender=gender,
                    importance=character.importance,
                    selection_mode="automatic" if automatic else "optional",
                    selected=automatic,
                    locked=automatic,
                    reference_text=REFERENCE_TEXT,
                    voice_prompt=character.voice_prompt,
                    reuse_reference_id=female_reference_id if gender == "female" else male_reference_id,
                )
            )
        plan = ReferencePlan(project_id=project_id, items=items)
        self._migrate_reference_text_versions(plan)
        return plan

    @staticmethod
    def _merge_reference_plan(existing: ReferencePlan | None, generated: ReferencePlan) -> ReferencePlan:
        if existing is None:
            return generated
        generated_by_id = {item.reference_id: item for item in generated.items}
        merged: list[ReferencePlanItem] = []
        seen: set[str] = set()
        for item in existing.items:
            current = generated_by_id.get(item.reference_id)
            if current is None:
                merged.append(item)
                seen.add(item.reference_id)
                continue
            item.display_name = current.display_name
            item.gender = current.gender
            item.importance = max(item.importance, current.importance)
            if not item.voice_prompt:
                item.voice_prompt = current.voice_prompt
            merged.append(item)
            seen.add(item.reference_id)
        merged.extend(item for item in generated.items if item.reference_id not in seen)
        existing.items = merged
        existing.schema_version = generated.schema_version
        return existing

    @staticmethod
    def _infer_gender(display_name: str, evidence: list[str]) -> Literal["male", "female", "unknown"]:
        context = " ".join([display_name, *evidence])
        female_score = sum(context.count(clue) for clue in FEMALE_CLUES)
        male_score = sum(context.count(clue) for clue in MALE_CLUES)
        if female_score > male_score:
            return "female"
        if male_score > female_score:
            return "male"
        return "unknown"

    @staticmethod
    def _voice_prompt(gender: Literal["male", "female", "unknown"]) -> str:
        if gender == "female":
            return "自然、清晰、有辨识度的女性角色声线，保持中性情绪"
        if gender == "male":
            return "自然、清晰、有辨识度的男性角色声线，保持中性情绪"
        return "自然、清晰、有辨识度的角色声线，保持中性情绪"

    def _generate_director(self, project_id: str, max_ready_batches: int | None = None) -> bool:
        self._check_preparation_cancelled(project_id)
        source_path = self._find_source(project_id)
        text, _ = self._read_text(source_path)
        audit = self._require_model(
            project_id,
            "analysis_audit.json",
            AnalysisAudit,
            "请先分析文档",
        )
        bible = self._require_model(
            project_id,
            "character_voice_bible.json",
            CharacterVoiceBible,
            "请先提取并审核角色",
        )
        canonical_ids = {
            character.display_name: character.character_id
            for character in bible.characters
            if character.character_id != "narrator"
        }
        aliases = {
            name: character.display_name
            for character in bible.characters
            if character.character_id != "narrator"
            for name in [character.display_name, *character.aliases]
        }
        analyzer_characters = [
            DirectorCharacter(
                display_name=character.display_name,
                aliases=character.aliases,
                gender=character.gender if character.gender in {"male", "female"} else "unknown",
            )
            for character in bible.characters
            if character.character_id != "narrator"
        ]
        checkpoint = self._read_model(
            project_id,
            "director_analysis_checkpoint.json",
            DirectorAnalysisCheckpoint,
        ) or DirectorAnalysisCheckpoint(project_id=project_id)
        warnings: list[str] = list(checkpoint.warnings)
        if audit.long_form_plan is None:
            settings = self._read_analysis_settings(project_id)
            audit.long_form_plan = build_long_form_plan(text, settings)
        windows = windows_from_plan(text, audit.long_form_plan)
        total_long_batches = max(len(windows), 1)
        target_windows = windows if max_ready_batches is None else windows[:max_ready_batches]
        analysis_backend: Literal["local", "hybrid", "cloud", "rules"] = checkpoint.analysis_backend
        analysis_model: str | None = checkpoint.analysis_model
        analyzer = getattr(self.voice_analyzer, "analyze_director", None)
        fallback = RuleBasedVoiceAnalyzer(self.workspace_root)
        analysis_status = self.voice_analyzer.status()
        analysis_label = {
            "cloud": "云端导演分析",
            "hybrid": "本地初筛与云端导演精推",
            "local": "本地导演分析",
            "rules": "规则导演分析",
        }[analysis_status.backend]
        batch_size = self._cloud_director_batch_size(analysis_status)
        if checkpoint.batch_size != batch_size:
            checkpoint.completed_batches = []
            checkpoint.batch_size = batch_size
        analysis_parallelism = self._project_analysis_parallelism(project_id, analysis_status)
        analysis_started_at = time.perf_counter()
        self._log_analysis_progress(
            project_id,
            0,
            f"{analysis_label}开始",
            analysis_status.backend,
            analysis_status.model,
            passages=audit.structure.dialogue_count,
            batches=total_long_batches,
            workers=analysis_parallelism,
            batch_size=batch_size,
            current_batch=1,
            total_batches=total_long_batches,
        )
        segments: list[DirectorSegment] = []
        unresolved = 0
        passage_offset = 0
        for window in target_windows:
            self._check_preparation_cancelled(project_id)
            window.batch.state = "director_running"
            self._write_model(project_id, "analysis_audit.json", audit)
            initial_chapter_number = (
                max((window.batch.chapter_start or 1) - 1, 0)
                if audit.long_form_plan.strategy in {"standard_chapters", "inferred_chapters"}
                else 0 if audit.long_form_plan.strategy == "short" else window.batch.index
            )
            passages = self._build_director_passages(
                window.text,
                aliases,
                project_id,
                initial_chapter_number=initial_chapter_number,
                passage_offset=passage_offset,
                analysis_batch_id=window.batch.batch_id,
            )
            window.batch.director_total_passages = len(passages)
            window.batch.director_completed_passages = 0
            self._write_model(project_id, "analysis_audit.json", audit)
            passage_offset += len(passages)
            explicit_gender = {
                character.display_name: character.gender
                for character in analyzer_characters
            }
            deterministic_decisions = {
                passage.evidence.passage_id: DirectorPassageDecision(
                    passage_id=passage.evidence.passage_id,
                    speaker=passage.evidence.explicit_speaker,
                    speaker_gender=explicit_gender.get(passage.evidence.explicit_speaker, "unknown"),
                    speaker_kind="named",
                    emotion="natural",
                    emotion_intensity=0.5,
                    tone="natural",
                    confidence=0.99,
                    rationale="文本包含明确说话归属，已由规则直接裁决",
                )
                for passage in passages
                if passage.evidence is not None
                and passage.evidence.explicit_speaker in canonical_ids
                and self._has_unambiguous_director_attribution(passage.evidence)
            }
            cached_decision_ids = set(checkpoint.decisions)
            dialogue_evidence = [
                passage.evidence
                for passage in passages
                if passage.evidence is not None
                and passage.evidence.passage_id not in deterministic_decisions
                and passage.evidence.passage_id not in cached_decision_ids
            ]
            micro_batch_count = max(1, (len(dialogue_evidence) + batch_size - 1) // batch_size)
            tasks: list[tuple[int, list[DirectorPassageEvidence], list[DirectorCharacter]]] = []
            for offset in range(0, len(dialogue_evidence), batch_size):
                evidence_batch = dialogue_evidence[offset : offset + batch_size]
                tasks.append(
                    (
                        len(tasks) + 1,
                        evidence_batch,
                        self._director_characters_for_batch(evidence_batch, analyzer_characters),
                    )
                )
            all_tasks = tasks

            def analyze_batch(
                task: tuple[int, list[DirectorPassageEvidence], list[DirectorCharacter]],
            ) -> tuple[int, DirectorAnalysisDraft, str | None, float]:
                self._check_preparation_cancelled(project_id)
                micro_index, evidence_batch, batch_characters = task
                batch_started_at = time.perf_counter()
                warning: str | None = None
                try:
                    draft: DirectorAnalysisDraft = (
                        analyzer(evidence_batch, batch_characters)
                        if callable(analyzer)
                        else fallback.analyze_director(evidence_batch, batch_characters)
                    )
                except (VoiceAnalysisError, httpx.HTTPError, ConnectionError, TimeoutError) as error:
                    draft = fallback.analyze_director(evidence_batch, batch_characters)
                    warning = (
                        f"模型导演分析失败，长篇批次 {window.batch.index} 的子批次 {micro_index} "
                        f"已使用规则回退：{error}"
                    )
                return micro_index, draft, warning, time.perf_counter() - batch_started_at

            passage_ids = {
                passage.evidence.passage_id
                for passage in passages
                if passage.evidence is not None
            }
            decisions: dict[str, DirectorPassageDecision] = {
                passage_id: decision
                for passage_id, decision in checkpoint.decisions.items()
                if passage_id in passage_ids
            }
            decisions.update(deterministic_decisions)
            checkpoint.decisions.update(deterministic_decisions)
            completed_micro_batches = 0

            def materialize_segments(source_passages: list[_PreparedDirectorPassage]) -> list[DirectorSegment]:
                materialized: list[DirectorSegment] = []
                for passage in source_passages:
                    decision = decisions.get(passage.evidence.passage_id) if passage.evidence is not None else None
                    speaker = decision.speaker if decision is not None and decision.speaker in canonical_ids else None
                    materialized.append(
                        DirectorSegment(
                            segment_id=f"seg-{len(segments) + len(materialized) + 1:06d}",
                            chapter_id=passage.chapter_id,
                            character_id=canonical_ids.get(speaker, "narrator"),
                            speaker_gender=(
                                decision.speaker_gender
                                if passage.segment_type == "dialogue" and decision is not None
                                else "unknown"
                            ),
                            speaker_kind=(
                                decision.speaker_kind
                                if passage.segment_type == "dialogue" and decision is not None
                                else "narration" if passage.segment_type == "narration" else "unknown"
                            ),
                            analysis_batch_id=passage.analysis_batch_id,
                            text=passage.text,
                            segment_type=passage.segment_type,
                            direction=PerformanceDirection(
                                emotion=decision.emotion if decision is not None else "neutral",
                                emotion_intensity=decision.emotion_intensity if decision is not None else 0.5,
                                tone=decision.tone if decision is not None else "natural",
                                pause_after_ms=220 if passage.segment_type == "dialogue" else 180,
                            ),
                        )
                    )
                return materialized

            def publish_partial_window() -> None:
                ready_passages: list[_PreparedDirectorPassage] = []
                for passage in passages:
                    if (
                        passage.segment_type == "dialogue"
                        and passage.evidence is not None
                        and passage.evidence.passage_id not in decisions
                    ):
                        break
                    ready_passages.append(passage)
                if not ready_passages:
                    return
                if dialogue_evidence and not any(passage.segment_type == "dialogue" for passage in ready_passages):
                    return
                if len(ready_passages) <= window.batch.director_completed_passages:
                    return
                window.batch.director_completed_passages = len(ready_passages)
                self._write_model(project_id, "analysis_audit.json", audit)
                self._write_model(
                    project_id,
                    "director_doc.json",
                    DirectorDocument(
                        schema_version=DIRECTOR_SCHEMA_VERSION,
                        project_id=project_id,
                        character_bible_id=f"{project_id}:character_voice_bible:v1",
                        analysis_backend=analysis_backend,
                        analysis_model=analysis_model,
                        warnings=[
                            *warnings,
                            (
                                f"片内导演处理中：{window.batch.title} 已释放 "
                                f"{len(ready_passages)}/{len(passages)} 个段落"
                            ),
                        ],
                        segments=[*segments, *materialize_segments(ready_passages)],
                    ),
                )
            if tasks:
                self._log_analysis_progress(
                    project_id,
                    round((window.batch.index - 1) * 95 / total_long_batches),
                    (
                        f"正在分析批次 1/{micro_batch_count}（长篇批次 "
                        f"{window.batch.index}/{total_long_batches}，并发队列已启动）"
                    ),
                    analysis_status.backend,
                    analysis_status.model,
                    elapsed_seconds=time.perf_counter() - analysis_started_at,
                    passages=len(dialogue_evidence),
                    batches=total_long_batches,
                    workers=analysis_parallelism,
                    batch_size=batch_size,
                    current_batch=window.batch.index,
                    total_batches=total_long_batches,
                )

            def record_result(
                micro_index: int,
                draft: DirectorAnalysisDraft,
                warning: str | None,
                batch_seconds: float,
            ) -> None:
                nonlocal analysis_backend, analysis_model, completed_micro_batches
                completed_micro_batches += 1
                if warning:
                    warnings.append(warning)
                decisions.update((decision.passage_id, decision) for decision in draft.decisions)
                if draft.backend in {"local", "hybrid", "cloud"}:
                    analysis_backend = draft.backend
                    analysis_model = draft.model
                batch_id = f"{window.batch.batch_id}:{micro_index}"
                if batch_id not in checkpoint.completed_batches:
                    checkpoint.completed_batches.append(batch_id)
                checkpoint.decisions.update((decision.passage_id, decision) for decision in draft.decisions)
                checkpoint.warnings = list(dict.fromkeys(warnings))
                checkpoint.analysis_backend = analysis_backend
                checkpoint.analysis_model = analysis_model
                checkpoint.updated_at = datetime.now(timezone.utc)
                self._write_model(project_id, "director_analysis_checkpoint.json", checkpoint)
                publish_partial_window()
                fraction = (
                    window.batch.index - 1 + completed_micro_batches / micro_batch_count
                ) / total_long_batches
                self._log_analysis_progress(
                    project_id,
                    round(fraction * 95),
                    (
                        f"已完成批次 {completed_micro_batches}/{micro_batch_count}（长篇批次 "
                        f"{window.batch.index}/{total_long_batches}，子批次 {micro_index}）"
                    ),
                    draft.backend,
                    draft.model,
                    elapsed_seconds=time.perf_counter() - analysis_started_at,
                    batch_seconds=batch_seconds,
                    passages=len(dialogue_evidence),
                    batches=total_long_batches,
                    workers=analysis_parallelism,
                    batch_size=batch_size,
                    current_batch=window.batch.index,
                    total_batches=total_long_batches,
                )

            if analysis_parallelism > 1 and len(tasks) > 1:
                with ThreadPoolExecutor(
                    max_workers=min(analysis_parallelism, len(tasks)),
                    thread_name_prefix="zw-cloud-director",
                ) as executor:
                    futures = [executor.submit(analyze_batch, task) for task in tasks]
                    try:
                        for future in as_completed(futures):
                            record_result(*future.result())
                            self._check_preparation_cancelled(project_id)
                    except Exception:
                        for pending in futures:
                            pending.cancel()
                        window.batch.state = "failed"
                        self._write_model(project_id, "analysis_audit.json", audit)
                        raise
            else:
                for task in tasks:
                    record_result(*analyze_batch(task))
                    self._check_preparation_cancelled(project_id)

            unresolved += sum(
                passage.segment_type == "dialogue"
                and (
                    passage.evidence is None
                    or decisions.get(passage.evidence.passage_id) is None
                    or decisions[passage.evidence.passage_id].speaker not in canonical_ids
                )
                for passage in passages
            )
            segments.extend(materialize_segments(passages))
            window.batch.director_completed_passages = len(passages)
            window.batch.state = "ready"
            self._write_model(project_id, "analysis_audit.json", audit)
            partial_warning = (
                [f"长篇导演处理中：已完成 {window.batch.index}/{total_long_batches} 批"]
                if window.batch.index < total_long_batches
                else []
            )
            self._write_model(
                project_id,
                "director_doc.json",
                DirectorDocument(
                    schema_version=DIRECTOR_SCHEMA_VERSION,
                    project_id=project_id,
                    character_bible_id=f"{project_id}:character_voice_bible:v1",
                    analysis_backend=analysis_backend,
                    analysis_model=analysis_model,
                    warnings=[*warnings, *partial_warning],
                    segments=segments,
                ),
            )
            self._check_preparation_cancelled(project_id)

        self._check_preparation_cancelled(project_id)
        all_batches_ready = all(batch.state == "ready" for batch in audit.long_form_plan.batches)
        release_model = getattr(self.voice_analyzer, "release_model", None)
        if callable(release_model):
            release_model()
        if unresolved:
            warnings.append(f"{unresolved} 条对白缺少可靠说话人，已暂用旁白并保留人工校正入口")
        partial_warning = (
            []
            if all_batches_ready
            else [
                f"滚动导演预取：已准备 {sum(batch.state == 'ready' for batch in audit.long_form_plan.batches)}/"
                f"{len(audit.long_form_plan.batches)} 个切片"
            ]
        )
        self._write_model(
            project_id,
            "director_doc.json",
            DirectorDocument(
                schema_version=DIRECTOR_SCHEMA_VERSION,
                project_id=project_id,
                character_bible_id=f"{project_id}:character_voice_bible:v1",
                analysis_backend=analysis_backend,
                analysis_model=analysis_model,
                warnings=[*warnings, *partial_warning],
                segments=segments,
            ),
        )
        if all_batches_ready:
            self._check_preparation_cancelled(project_id)
            self._remove_artifact(project_id, "director_analysis_checkpoint.json")
        ready_batches = sum(batch.state == "ready" for batch in audit.long_form_plan.batches)
        self._log_analysis_progress(
            project_id,
            100 if all_batches_ready else round(ready_batches * 100 / total_long_batches),
            f"{analysis_label}完成" if all_batches_ready else f"导演预取窗口已准备 {ready_batches}/{total_long_batches} 批",
            analysis_backend,
            analysis_model,
            elapsed_seconds=time.perf_counter() - analysis_started_at,
            passages=audit.structure.dialogue_count,
            batches=total_long_batches,
            workers=analysis_parallelism,
            batch_size=batch_size,
            current_batch=ready_batches,
            total_batches=total_long_batches,
        )
        return all_batches_ready

    def _build_director_passages(
        self,
        text: str,
        aliases: dict[str, str],
        project_id: str | None = None,
        *,
        initial_chapter_number: int = 0,
        passage_offset: int = 0,
        analysis_batch_id: str | None = None,
    ) -> list[_PreparedDirectorPassage]:
        source_lines: list[tuple[str, str]] = []
        chapter_number = initial_chapter_number
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if CHAPTER_PATTERN.fullmatch(stripped):
                chapter_number += 1
                continue
            source_lines.append((f"chapter-{max(chapter_number, 1):04d}", stripped))

        passages: list[_PreparedDirectorPassage] = []
        for line_index, (chapter_id, line) in enumerate(source_lines):
            previous_line = (
                source_lines[line_index - 1][1]
                if line_index > 0 and source_lines[line_index - 1][0] == chapter_id
                else ""
            )
            next_line = (
                source_lines[line_index + 1][1]
                if line_index + 1 < len(source_lines) and source_lines[line_index + 1][0] == chapter_id
                else ""
            )
            matches = list(QUOTE_PATTERN.finditer(line))
            if not matches:
                passages.extend(
                    _PreparedDirectorPassage(
                        chapter_id,
                        sentence,
                        "narration",
                        analysis_batch_id=analysis_batch_id,
                    )
                    for sentence in self._split_sentences(line)
                )
                continue

            cursor = 0
            for match in matches:
                narration = line[cursor : match.start()].strip()
                passages.extend(
                    _PreparedDirectorPassage(
                        chapter_id,
                        sentence,
                        "narration",
                        analysis_batch_id=analysis_batch_id,
                    )
                    for sentence in self._split_sentences(narration)
                )
                spoken_text = next(value for value in match.groupdict().values() if value is not None)
                current_context = line[max(0, match.start() - 500) : min(len(line), match.end() + 500)]
                context = "\n".join(
                    part
                    for part in (previous_line[-220:], current_context, next_line[:220])
                    if part
                )[-1_000:]
                explicit_speaker = self._explicit_speaker_for_dialogue(
                    line[: match.start()],
                    line[match.end() :],
                    previous_line,
                    aliases,
                )
                for sentence in self._split_sentences(spoken_text):
                    passage_id = f"passage-{passage_offset + len(passages) + 1:06d}"
                    evidence = DirectorPassageEvidence(
                        project_id=project_id,
                        passage_id=passage_id,
                        text=sentence,
                        context=context,
                        explicit_speaker=explicit_speaker,
                    )
                    passages.append(
                        _PreparedDirectorPassage(
                            chapter_id,
                            sentence,
                            "dialogue",
                            evidence,
                            analysis_batch_id,
                        )
                    )
                cursor = match.end()
            narration = line[cursor:].strip()
            passages.extend(
                _PreparedDirectorPassage(
                    chapter_id,
                    sentence,
                    "narration",
                    analysis_batch_id=analysis_batch_id,
                )
                for sentence in self._split_sentences(narration)
            )
        return passages

    @staticmethod
    def _director_characters_for_batch(
        passages: list[DirectorPassageEvidence],
        characters: list[DirectorCharacter],
    ) -> list[DirectorCharacter]:
        if len(characters) <= 24:
            return characters
        context = "\n".join(passage.context for passage in passages)
        matched = [
            character
            for character in characters
            if any(
                name and name in context
                for name in [character.display_name, *character.aliases]
            )
            or any(passage.explicit_speaker == character.display_name for passage in passages)
        ]
        return matched[:24] or characters[:24]

    @staticmethod
    def _has_unambiguous_director_attribution(passage: DirectorPassageEvidence) -> bool:
        speaker = passage.explicit_speaker
        if not speaker:
            return False
        return re.search(
            rf'(?:^|[\n。！？!?；;，,”"』」])\s*{re.escape(speaker)}'
            rf'[\u4e00-\u9fff]{{0,4}}?{SPEECH_VERB}(?=[：:，,。！？!?“"『「]|$)',
            passage.context,
            re.MULTILINE,
        ) is not None

    @classmethod
    def _explicit_speaker_for_dialogue(
        cls,
        before_quote: str,
        after_quote: str,
        previous_line: str,
        aliases: dict[str, str],
    ) -> str | None:
        speaker = cls._named_speaker_in_attribution(before_quote[-240:], aliases, prefer_last=True)
        if speaker is not None:
            return speaker
        if not before_quote.strip() and previous_line and not QUOTE_PATTERN.search(previous_line):
            speaker = cls._named_speaker_in_attribution(previous_line[-240:], aliases, prefer_last=True)
            if speaker is not None:
                return speaker
        return cls._named_speaker_in_attribution(after_quote[:160], aliases, prefer_last=False)

    @staticmethod
    def _named_speaker_in_attribution(
        text: str,
        aliases: dict[str, str],
        prefer_last: bool,
    ) -> str | None:
        candidates: list[tuple[int, str]] = []
        for name, canonical_name in sorted(aliases.items(), key=lambda item: -len(item[0])):
            for match in re.finditer(rf"{re.escape(name)}[\u4e00-\u9fff]{{0,10}}?{SPEECH_VERB}", text):
                candidates.append((match.end() if prefer_last else -match.start(), canonical_name))
        return max(candidates)[1] if candidates else None

    def _scan_candidates(self, text: str) -> list[CharacterCandidate]:
        matches = list(ATTRIBUTION_PATTERN.finditer(text))
        clauses = [match.group("clause") for match in matches]
        name_options = {
            candidate
            for clause in clauses
            for candidate in self._candidate_name_options(clause)
        }
        name_options.update(role for role in ROLE_TERMS if role in text)
        mention_counts = self._count_candidate_mentions(text, name_options)
        speaker_evidence: dict[str, dict[str, object]] = {}
        for match in matches:
            clause = match.group("clause")
            name = self._select_candidate_name(clause, mention_counts)
            if name is None:
                continue
            start = max(0, match.start() - 24)
            end = min(len(text), match.end() + 48)
            excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
            entry = speaker_evidence.setdefault(name, {"evidence": [], "explicit": False})
            evidence = entry["evidence"]
            if isinstance(evidence, list):
                evidence.append(excerpt)
            if match.group("introduces"):
                entry["explicit"] = True

        candidates: list[CharacterCandidate] = []
        for name, entry in speaker_evidence.items():
            evidence = entry["evidence"] if isinstance(entry["evidence"], list) else []
            explicit = bool(entry["explicit"])
            mentions = mention_counts.get(name, 0)
            rejected = self._is_clear_non_entity(name) or (not explicit and mentions < 2)
            entity_confidence = self._candidate_entity_confidence(
                name,
                mentions,
                len(evidence),
                explicit,
            )
            candidates.append(
                CharacterCandidate(
                    candidate_id=self._stable_id("candidate", name),
                    display_name=name,
                    decision="rejected" if rejected else "pending",
                    confidence=0.35 if rejected else min(0.98, 0.72 + len(evidence) * 0.04 + (0.08 if explicit else 0)),
                    mention_count=mentions,
                    dialogue_count=len(evidence),
                    entity_confidence=entity_confidence,
                    evidence=evidence[:5],
                    reason="叙述短语或证据不足，不建立独立音色" if rejected else "检测到标点锚定的说话归属和直接对话证据",
                )
            )
        known_names = {candidate.display_name for candidate in candidates}
        for role in ROLE_TERMS:
            if role in text and role not in known_names:
                candidates.append(
                    CharacterCandidate(
                        candidate_id=self._stable_id("candidate", role),
                        display_name=role,
                        decision="rejected",
                        confidence=0.3,
                        mention_count=mention_counts.get(role, 1),
                        dialogue_count=0,
                        entity_confidence=0.02,
                        evidence=[self._excerpt(text, role)],
                        reason="通用身份词，缺少具名角色证据",
                    )
                )
        accepted_names = [candidate.display_name for candidate in candidates if candidate.decision == "pending"]
        for candidate in candidates:
            if (
                candidate.decision == "rejected"
                and candidate.mention_count >= 2
                and any(
                    name != candidate.display_name
                    and self._alias_key(name).endswith(self._alias_key(candidate.display_name))
                    for name in accepted_names
                )
            ):
                candidate.decision = "pending"
                candidate.reason = "检测到已接纳全名的高频短称，将作为别名合并"
        return candidates

    @classmethod
    def _candidate_name_options(cls, clause: str) -> list[str]:
        for prefix in LEADING_PHRASES:
            if clause.startswith(prefix) and len(clause) > len(prefix) + 1:
                clause = clause[len(prefix) :]
                break
        while len(clause) > 2 and clause.endswith(ATTRIBUTION_MANNER_SUFFIXES):
            clause = clause[:-1]
        candidates = [clause[:length] for length in range(2, min(4, len(clause)) + 1)]
        return [candidate for candidate in candidates if cls._looks_like_name(candidate)]

    @classmethod
    def _select_candidate_name(cls, clause: str, mention_counts: dict[str, int]) -> str | None:
        candidates = cls._candidate_name_options(clause)
        if not candidates:
            return None
        counts = {candidate: mention_counts.get(candidate, 0) for candidate in candidates}
        if max(counts.values(), default=0) <= 1:
            return max(candidates, key=len)
        shortest = min(candidates, key=len)
        baseline = counts[shortest]
        plausible = [
            candidate
            for candidate in candidates
            if counts[candidate] >= 2 and counts[candidate] >= baseline * 0.35
        ]
        return max(plausible or [shortest], key=len)

    @staticmethod
    def _count_candidate_mentions(text: str, names: set[str]) -> dict[str, int]:
        if not names:
            return {}
        terminal = ""
        trie: dict[str, dict] = {}
        max_length = 0
        for name in names:
            node = trie
            max_length = max(max_length, len(name))
            for character in name:
                node = node.setdefault(character, {})
            node[terminal] = name
        counts: Counter[str] = Counter()
        for start, character in enumerate(text):
            node = trie.get(character)
            if node is None:
                continue
            matched = node.get(terminal)
            if matched is not None:
                counts[matched] += 1
            for position in range(start + 1, min(len(text), start + max_length)):
                child = node.get(text[position])
                if child is None:
                    break
                node = child
                matched = node.get(terminal)
                if matched is not None:
                    counts[matched] += 1
        return dict(counts)

    @classmethod
    def _is_clear_non_entity(cls, value: str) -> bool:
        return (
            value in ROLE_TERMS
            or value in NON_ENTITY_SPEECH_TERMS
            or not cls._looks_like_name(value)
            or (
                value.endswith(NON_ENTITY_SPEECH_ENDINGS)
                and not value.endswith(NAME_SUFFIXES)
                and len(value) <= 3
            )
        )

    @classmethod
    def _candidate_entity_confidence(
        cls,
        name: str,
        mention_count: int,
        dialogue_count: int,
        explicit: bool,
    ) -> float:
        if cls._is_clear_non_entity(name):
            return 0.02
        stable_name_shape = name[0] in COMMON_SURNAMES or name.startswith(COMPOUND_SURNAMES)
        score = 0.42 + (0.16 if stable_name_shape else 0.0) + (0.08 if explicit else 0.0)
        score += min(0.18, dialogue_count * 0.06)
        score += min(0.1, max(mention_count - 1, 0) * 0.025)
        return round(min(score, 0.98), 3)

    @classmethod
    def _score_candidates(cls, candidates: list[CharacterCandidate], batch_count: int) -> None:
        max_first_dialogue = max((candidate.first_batch_dialogue_count for candidate in candidates), default=1) or 1
        max_peak_dialogue = max((candidate.peak_batch_dialogue_count for candidate in candidates), default=1) or 1
        max_dialogue = max((candidate.dialogue_count for candidate in candidates), default=1) or 1
        max_mentions = max((candidate.mention_count for candidate in candidates), default=1) or 1
        max_peak_mentions = max((candidate.peak_batch_mentions for candidate in candidates), default=1) or 1
        for candidate in candidates:
            candidate.entity_confidence = max(
                candidate.entity_confidence,
                cls._candidate_entity_confidence(
                    candidate.display_name,
                    candidate.mention_count,
                    candidate.dialogue_count,
                    bool(candidate.dialogue_count),
                ),
            )
            first_slice = candidate.first_batch_dialogue_count / max_first_dialogue
            peak_slice = candidate.peak_batch_dialogue_count / max_peak_dialogue
            total_dialogue = candidate.dialogue_count / max_dialogue
            presence = min(candidate.batch_presence_count / max(min(batch_count, 4), 1), 1.0)
            mentions = candidate.mention_count / max_mentions
            candidate.production_priority = round(
                min(
                    0.98,
                    0.4 * first_slice
                    + 0.2 * peak_slice
                    + 0.17 * total_dialogue
                    + 0.13 * presence
                    + 0.05 * mentions
                    + 0.05 * candidate.entity_confidence,
                ),
                3,
            )
            candidate.local_importance = round(
                min(0.95, 0.25 + 0.7 * candidate.peak_batch_mentions / max_peak_mentions),
                3,
            )

    @staticmethod
    def _looks_like_name(value: str) -> bool:
        if any(value.startswith(role) for role in ROLE_TERMS) or value.startswith(NON_NAME_PREFIXES):
            return False
        if value.endswith(NON_NAME_ENDINGS):
            return False
        return (
            value[0] in COMMON_SURNAMES
            or value.startswith(COMPOUND_SURNAMES)
            or value.endswith(NAME_SUFFIXES)
            or (len(value) >= 3 and value.startswith("小"))
        )

    @staticmethod
    def _alias_key(value: str) -> str:
        return value.translate(ALIAS_TRANSLATION)

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        return [
            match.strip()
            for match in re.findall(r".+?(?:[。！？!?]+[”\"』」]?|$)", text, re.DOTALL)
            if match.strip()
        ]

    @staticmethod
    def _speaker_for_sentence(sentence: str, character_ids: dict[str, str]) -> str | None:
        for name in character_ids:
            if name != "旁白" and re.search(rf"{re.escape(name)}[\u4e00-\u9fff]{{0,8}}?{SPEECH_VERB}", sentence):
                return name
        return None

    @staticmethod
    def _excerpt(text: str, term: str) -> str:
        position = text.find(term)
        return re.sub(r"\s+", " ", text[max(0, position - 24) : position + len(term) + 48]).strip()

    def _find_source(self, project_id: str) -> Path:
        manifest = self._read_project_manifest(project_id)
        if manifest is not None:
            return self._manifest_source_path(manifest)
        for path in self.source_root.glob("*.txt"):
            if self._project_id(path.name) == project_id:
                return path
        raise PreparationProblem(404, "未找到对应的小说源文件")

    def _source_summary(
        self,
        path: Path,
        encoding: Literal["utf-8", "gb18030"],
        manifest: ProjectManifest | None = None,
    ) -> SourceSummary:
        project_id = manifest.project_id if manifest is not None else self._project_id(path.name)
        return SourceSummary(
            project_id=project_id,
            file_name=manifest.source_file if manifest is not None else path.name,
            display_name=manifest.display_name if manifest is not None else path.stem,
            size_bytes=path.stat().st_size,
            encoding=encoding,
            status=self._status(project_id),
        )

    def _read_project_manifest(self, project_id: str) -> ProjectManifest | None:
        with self._manifest_lock:
            path = self.project_root / project_id / "project.json"
            if not path.is_file():
                return None
            try:
                manifest = ProjectManifest.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise PreparationProblem(500, f"项目清单损坏：{project_id}") from error
            if manifest.project_id != project_id:
                raise PreparationProblem(500, f"项目清单 ID 不一致：{project_id}")
            return manifest

    def _write_project_manifest(self, manifest: ProjectManifest) -> None:
        with self._manifest_lock:
            path = self.project_root / manifest.project_id / "project.json"
            self._write_json_file(path, manifest.model_dump(mode="json"))

    def _touch_project_manifest(self, project_id: str) -> None:
        with self._manifest_lock:
            manifest = self._read_project_manifest(project_id)
            if manifest is None:
                return
            manifest.updated_at = datetime.now(timezone.utc)
            self._write_project_manifest(manifest)

    def _manifest_source_path(self, manifest: ProjectManifest) -> Path:
        project_dir = (self.project_root / manifest.project_id).resolve()
        source_path = (project_dir / manifest.source_path).resolve()
        try:
            source_path.relative_to(project_dir)
        except ValueError as error:
            raise PreparationProblem(500, f"项目源文件路径越界：{manifest.project_id}") from error
        if not source_path.is_file():
            raise PreparationProblem(404, f"项目源文件不存在：{manifest.source_file}")
        return source_path

    def _status(self, project_id: str) -> PreparationStatus:
        root = self.project_root / project_id
        if (root / "director_doc.json").is_file():
            return "director_ready"
        if (root / "character_voice_bible.json").is_file():
            return "characters_ready"
        if (root / "analysis_audit.json").is_file():
            return "analyzed"
        return "imported"

    def _read_text(self, path: Path) -> tuple[str, Literal["utf-8", "gb18030"]]:
        return self._decode(path.read_bytes())

    @staticmethod
    def _decode(content: bytes) -> tuple[str, Literal["utf-8", "gb18030"]]:
        try:
            return content.decode("utf-8-sig"), "utf-8"
        except UnicodeDecodeError:
            return content.decode("gb18030"), "gb18030"

    @staticmethod
    def _project_id(file_name: str) -> str:
        return PreparationService._stable_id("project", file_name.casefold())

    @staticmethod
    def _stable_id(prefix: str, value: str) -> str:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
        return f"{prefix}-{digest}"

    def _artifact_path(self, project_id: str, file_name: str) -> Path:
        return self.project_root / project_id / file_name

    def _remove_artifact(self, project_id: str, file_name: str) -> None:
        with self._artifact_lock:
            self._artifact_path(project_id, file_name).unlink(missing_ok=True)
            self._mirror_revision_artifact(project_id, file_name, removed=True)

    def _write_model(self, project_id: str, file_name: str, model: BaseModel) -> None:
        with self._artifact_lock:
            path = self._artifact_path(project_id, file_name)
            self._write_json_file(path, model.model_dump(mode="json"))
            self._mirror_revision_artifact(project_id, file_name)
            if file_name != "project.json":
                self._touch_project_manifest(project_id)

    def _mirror_revision_artifact(self, project_id: str, file_name: str, removed: bool = False) -> None:
        if file_name not in REVISION_ARTIFACTS:
            return
        workspace_path = self._revision_workspace_path(project_id)
        if not workspace_path.is_file():
            return
        try:
            workspace = ProjectRevisionWorkspace.model_validate_json(workspace_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if workspace.active_revision_id is None:
            return
        target = self._revision_dir(project_id, workspace.active_revision_id) / file_name
        if removed:
            target.unlink(missing_ok=True)
            return
        source = self._artifact_path(project_id, file_name)
        if not source.is_file():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    def _read_model(self, project_id: str, file_name: str, model_type: type[BaseModel]) -> BaseModel | None:
        with self._artifact_lock:
            path = self._artifact_path(project_id, file_name)
            if not path.is_file():
                return None
            return model_type.model_validate_json(path.read_text(encoding="utf-8"))

    def _require_model(
        self,
        project_id: str,
        file_name: str,
        model_type: type[BaseModel],
        detail: str,
    ) -> BaseModel:
        model = self._read_model(project_id, file_name, model_type)
        if model is None:
            raise PreparationProblem(409, detail)
        return model


def create_preparation_router(service: PreparationService) -> APIRouter:
    router = APIRouter()

    def handle(problem: PreparationProblem) -> HTTPException:
        return HTTPException(status_code=problem.status_code, detail=problem.detail)

    @router.get("/api/sources", response_model=list[SourceSummary])
    def list_sources() -> list[SourceSummary]:
        return service.list_sources()

    @router.get("/api/voice-analysis/status", response_model=VoiceAnalysisStatus)
    def voice_analysis_status() -> VoiceAnalysisStatus:
        return service.voice_analysis_status()

    @router.get("/api/voice-analysis/config", response_model=VoiceAnalysisConfigurationView)
    def voice_analysis_configuration() -> VoiceAnalysisConfigurationView:
        return service.voice_analysis_configuration()

    @router.patch("/api/voice-analysis/config", response_model=VoiceAnalysisConfigurationView)
    def update_voice_analysis_configuration(
        update: VoiceAnalysisConfigurationUpdate,
    ) -> VoiceAnalysisConfigurationView:
        try:
            return service.update_voice_analysis_configuration(update)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post("/api/voice-analysis/test", response_model=VoiceAnalysisStatus)
    def test_voice_analysis_configuration() -> VoiceAnalysisStatus:
        try:
            return service.test_voice_analysis_configuration()
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post(
        "/api/voice-analysis/profiles/{profile_id}/test",
        response_model=VoiceAnalysisConfigurationView,
    )
    def test_voice_analysis_profile(profile_id: str) -> VoiceAnalysisConfigurationView:
        try:
            return service.test_voice_analysis_profile(profile_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post("/api/voice-analysis/models", response_model=VoiceAnalysisModelCatalog)
    def list_voice_analysis_models(
        request: VoiceAnalysisModelCatalogRequest,
    ) -> VoiceAnalysisModelCatalog:
        try:
            return service.list_voice_analysis_models(request)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post("/api/voice-analysis/preview", response_model=CharacterVoiceProfile)
    def preview_voice_profile(evidence_pack: CharacterEvidencePack) -> CharacterVoiceProfile:
        try:
            return service.preview_voice_profile(evidence_pack)
        except VoiceAnalysisError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.post("/api/sources", response_model=SourceSummary, status_code=201)
    async def import_source(
        file: UploadFile = File(...),
        project_name: str | None = Form(None),
    ) -> SourceSummary:
        try:
            return service.import_source(file.filename or "", await file.read(), project_name)
        except (PreparationProblem, UnicodeDecodeError) as problem:
            if isinstance(problem, UnicodeDecodeError):
                raise HTTPException(status_code=415, detail="TXT 编码必须是 UTF-8 或 GB18030") from problem
            raise handle(problem) from problem

    @router.get("/api/projects/{project_id}/preparation/preview", response_model=PreparationPreview)
    def preview(project_id: str) -> PreparationPreview:
        try:
            return service.preview(project_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.get(
        "/api/projects/{project_id}/revisions",
        response_model=ProjectRevisionWorkspace,
    )
    def revision_workspace(project_id: str) -> ProjectRevisionWorkspace:
        try:
            return service.revision_workspace(project_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post(
        "/api/projects/{project_id}/revisions/{revision_id}/activate",
        response_model=PreparationPreview,
    )
    def activate_revision(project_id: str, revision_id: str) -> PreparationPreview:
        try:
            return service.activate_revision(project_id, revision_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.delete(
        "/api/projects/{project_id}/revisions/{revision_id}",
        response_model=ProjectRevisionWorkspace,
    )
    def delete_revision(project_id: str, revision_id: str) -> ProjectRevisionWorkspace:
        try:
            return service.delete_revision(project_id, revision_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.get("/api/projects/{project_id}/analysis-activity", response_model=AnalysisActivityView)
    def analysis_activity(project_id: str) -> AnalysisActivityView:
        try:
            return service.analysis_activity(project_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.patch("/api/projects/{project_id}/analysis-settings", response_model=PreparationPreview)
    def update_analysis_settings(
        project_id: str,
        request: LongFormAnalysisSettingsUpdate,
    ) -> PreparationPreview:
        try:
            return service.update_analysis_settings(project_id, request)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post("/api/projects/{project_id}/preparation", response_model=PreparationPreview)
    def run(project_id: str, request: PreparationActionRequest) -> PreparationPreview:
        try:
            return service.run(project_id, request.action, request.revision_id, request.resume)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post("/api/projects/{project_id}/preparation/cancel", response_model=AnalysisActivityView)
    def cancel_preparation(project_id: str) -> AnalysisActivityView:
        try:
            return service.cancel_preparation(project_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.patch("/api/projects/{project_id}/reference-settings", response_model=PreparationPreview)
    def update_reference_settings(
        project_id: str,
        request: ReferenceSettingsRequest,
    ) -> PreparationPreview:
        try:
            return service.update_reference_settings(
                project_id,
                request.automatic_threshold,
                request.automatic_items_locked,
            )
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post("/api/projects/{project_id}/voice-profiles/regenerate", response_model=PreparationPreview)
    def regenerate_voice_profiles(
        project_id: str,
        request: VoiceProfileRegenerationRequest,
    ) -> PreparationPreview:
        try:
            return service.regenerate_voice_profiles(
                project_id,
                request.character_id,
                request.reference_id,
                request.custom_attributes,
            )
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.patch(
        "/api/projects/{project_id}/references/{reference_id}",
        response_model=PreparationPreview,
    )
    def update_reference(
        project_id: str,
        reference_id: str,
        request: ReferenceUpdateRequest,
    ) -> PreparationPreview:
        try:
            return service.update_reference(
                project_id,
                reference_id,
                request.selected,
                request.voice_prompt,
                request.voice_prompt_locked,
                request.custom_voice_attributes,
                request.reference_text,
            )
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.patch(
        "/api/projects/{project_id}/director/{segment_id}/voice",
        response_model=PreparationPreview,
    )
    def update_director_segment_voice(
        project_id: str,
        segment_id: str,
        request: DirectorSegmentVoiceRequest,
    ) -> PreparationPreview:
        try:
            return service.update_director_segment_voice(project_id, segment_id, request.voice_reference_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.delete("/api/projects/{project_id}/director", response_model=PreparationPreview)
    def delete_director_cache(project_id: str) -> PreparationPreview:
        try:
            return service.delete_director_cache(project_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post(
        "/api/projects/{project_id}/references/{reference_id}/text/generate",
        response_model=PreparationPreview,
    )
    def generate_reference_text(project_id: str, reference_id: str) -> PreparationPreview:
        try:
            return service.generate_reference_text(project_id, reference_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.patch(
        "/api/projects/{project_id}/references/{reference_id}/text/{version_id}",
        response_model=PreparationPreview,
    )
    def activate_reference_text_version(
        project_id: str,
        reference_id: str,
        version_id: str,
    ) -> PreparationPreview:
        try:
            return service.activate_reference_text_version(project_id, reference_id, version_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.delete(
        "/api/projects/{project_id}/references/{reference_id}/text/{version_id}",
        response_model=PreparationPreview,
    )
    def delete_reference_text_version(
        project_id: str,
        reference_id: str,
        version_id: str,
    ) -> PreparationPreview:
        try:
            return service.delete_reference_text_version(project_id, reference_id, version_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.patch(
        "/api/projects/{project_id}/references/{reference_id}/audio/{version_id}",
        response_model=PreparationPreview,
    )
    def activate_reference_audio_version(
        project_id: str,
        reference_id: str,
        version_id: str,
    ) -> PreparationPreview:
        try:
            return service.activate_reference_audio_version(project_id, reference_id, version_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post(
        "/api/projects/{project_id}/references/{reference_id}/audio/{version_id}/review",
        response_model=PreparationPreview,
    )
    def review_reference_audio_version(
        project_id: str,
        reference_id: str,
        version_id: str,
        request: ReferenceAudioReviewRequest,
    ) -> PreparationPreview:
        try:
            return service.review_reference_audio_version(
                project_id,
                reference_id,
                version_id,
                request.decision,
            )
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.delete(
        "/api/projects/{project_id}/references/{reference_id}/audio/{version_id}",
        response_model=PreparationPreview,
    )
    def delete_reference_audio_version(
        project_id: str,
        reference_id: str,
        version_id: str,
    ) -> PreparationPreview:
        try:
            return service.delete_reference_audio_version(project_id, reference_id, version_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.delete(
        "/api/projects/{project_id}/references/{reference_id}/audio",
        response_model=PreparationPreview,
    )
    def clear_reference_audio_cache(project_id: str, reference_id: str) -> PreparationPreview:
        try:
            return service.clear_reference_audio_cache(project_id, reference_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.get(
        "/api/projects/{project_id}/references/{reference_id}/matches",
        response_model=list[VoiceResourceMatch],
    )
    def list_voice_resource_matches(
        project_id: str,
        reference_id: str,
        limit: int = 6,
    ) -> list[VoiceResourceMatch]:
        try:
            return service.list_voice_resource_matches(project_id, reference_id, limit)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post(
        "/api/projects/{project_id}/references/{reference_id}/reuse",
        response_model=PreparationPreview,
    )
    def reuse_voice_resource(
        project_id: str,
        reference_id: str,
        request: VoiceResourceReuseRequest,
    ) -> PreparationPreview:
        try:
            return service.reuse_voice_resource(project_id, reference_id, request)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post(
        "/api/projects/{project_id}/references/{reference_id}/audio",
        response_model=PreparationPreview,
    )
    async def upload_reference_audio(
        project_id: str,
        reference_id: str,
        file: UploadFile = File(...),
        source: ReferenceAudioSource = Form("uploaded"),
    ) -> PreparationPreview:
        try:
            if Path(file.filename or "").suffix.casefold() != ".wav":
                raise PreparationProblem(415, "参考音频上传前必须转换为 WAV")
            content = await file.read(MAX_REFERENCE_AUDIO_BYTES + 1)
            return service.upload_reference_audio(project_id, reference_id, source, content)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.patch("/api/projects/{project_id}/emotion-settings", response_model=PreparationPreview)
    def update_emotion_settings(
        project_id: str,
        request: EmotionSettingsRequest,
    ) -> PreparationPreview:
        try:
            return service.update_emotion_settings(
                project_id,
                request.skipped,
                request.automatic_threshold,
                request.automatic_items_locked,
            )
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.patch(
        "/api/projects/{project_id}/emotions/{variant_id}",
        response_model=PreparationPreview,
    )
    def update_emotion_variant(
        project_id: str,
        variant_id: str,
        request: EmotionUpdateRequest,
    ) -> PreparationPreview:
        try:
            return service.update_emotion_variant(project_id, variant_id, request.selected)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.post("/api/projects/{project_id}/emotions", response_model=PreparationPreview, status_code=201)
    def create_emotion_variant(
        project_id: str,
        request: EmotionCreateRequest,
    ) -> PreparationPreview:
        try:
            return service.create_emotion_variant(
                project_id,
                request.parent_reference_id,
                request.emotion_name,
                request.description,
                request.intensity,
            )
        except PreparationProblem as problem:
            raise handle(problem) from problem

    @router.delete(
        "/api/projects/{project_id}/emotions/{variant_id}",
        response_model=PreparationPreview,
    )
    def delete_emotion_variant(project_id: str, variant_id: str) -> PreparationPreview:
        try:
            return service.delete_emotion_variant(project_id, variant_id)
        except PreparationProblem as problem:
            raise handle(problem) from problem

    return router
