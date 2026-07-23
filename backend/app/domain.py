from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ReviewStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"


class CharacterTier(str, Enum):
    core = "core"
    supporting = "supporting"
    minor = "minor"
    uncertain = "uncertain"


class ProductionStageStatus(str, Enum):
    complete = "complete"
    current = "current"
    ready = "ready"
    blocked = "blocked"


class StabilityPolicy(str, Enum):
    disabled = "disabled"
    benchmark_gated = "benchmark_gated"
    enabled = "enabled"


class QualityRouteConfiguration(BaseModel):
    reference_backend: Literal["voxcpm2", "indextts2"]
    render_backend: Literal["gpt_sovits"] = "gpt_sovits"
    stability_backend: Literal["rvc"] | None = "rvc"
    stability_policy: StabilityPolicy = StabilityPolicy.benchmark_gated


class InferenceTemplate(BaseModel):
    template_id: str
    display_name: str
    analysis_profile: Literal["balanced", "character_recall", "precision_first"]
    segmentation_profile: Literal["audiobook", "dialogue_dense", "long_form"]
    reference_text_profile: Literal["phoneme_coverage", "emotion_contrast"]
    quality_route: QualityRouteConfiguration


class ProductionStage(BaseModel):
    stage_id: Literal[
        "template",
        "source",
        "casting",
        "references",
        "emotions",
        "director",
        "quality_render",
    ]
    label: str
    status: ProductionStageStatus


class ReferenceAudio(BaseModel):
    reference_id: str
    character_id: str
    parent_reference_id: str | None = None
    emotion: str = "neutral"
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)
    text: str
    audio_path: str
    generation_backend: Literal["voxcpm2", "indextts2", "uploaded"]
    seed: int | None = None
    review_status: ReviewStatus = ReviewStatus.pending
    created_at: datetime = Field(default_factory=datetime.now)


class CharacterEvidence(BaseModel):
    chapter_id: str
    segment_id: str
    text: str
    evidence_type: Literal["dialogue", "mention", "alias", "narrative"]


class CharacterVoice(BaseModel):
    character_id: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0)
    tier: CharacterTier
    gender: str = "unknown"
    age_range: str = "adult"
    personality_tags: list[str] = Field(default_factory=list)
    timbre_tags: list[str] = Field(default_factory=list)
    voice_prompt: str = ""
    archetype_id: str | None = None
    evidence: list[CharacterEvidence] = Field(default_factory=list)
    canonical_reference_id: str | None = None
    references: list[ReferenceAudio] = Field(default_factory=list)
    fast_route_rvc_model_id: str | None = None
    quality_route_stability_rvc_model_id: str | None = None


class CharacterVoiceBible(BaseModel):
    schema_version: int = 1
    project_id: str
    source_text: str
    characters: list[CharacterVoice]


class PerformanceDirection(BaseModel):
    emotion: str = "neutral"
    emotion_intensity: float = Field(default=0.5, ge=0.0, le=1.0)
    tone: str = "natural"
    pause_before_ms: int = Field(default=0, ge=0)
    pause_after_ms: int = Field(default=180, ge=0)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch: float = Field(default=1.0, ge=0.5, le=2.0)
    energy: float = Field(default=1.0, ge=0.0, le=2.0)


class DirectorSegment(BaseModel):
    segment_id: str
    chapter_id: str
    character_id: str
    text: str
    segment_type: Literal["narration", "dialogue"]
    direction: PerformanceDirection = Field(default_factory=PerformanceDirection)


class DirectorDocument(BaseModel):
    schema_version: int = 1
    project_id: str
    character_bible_id: str
    segments: list[DirectorSegment]
