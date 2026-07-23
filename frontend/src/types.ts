export type RouteMode = "fast" | "quality";
export type ProductionStageId = "template" | "source" | "casting" | "references" | "emotions" | "director" | "quality_render";
export type ProductionStageStatus = "complete" | "current" | "ready" | "blocked";
export type StabilityPolicy = "disabled" | "benchmark_gated" | "enabled";

export interface QualityRouteConfiguration {
  reference_backend: "voxcpm2" | "indextts2";
  render_backend: "gpt_sovits";
  stability_backend: "rvc" | null;
  stability_policy: StabilityPolicy;
}

export interface InferenceTemplate {
  template_id: string;
  display_name: string;
  analysis_profile: "balanced" | "character_recall" | "precision_first";
  segmentation_profile: "audiobook" | "dialogue_dense" | "long_form";
  reference_text_profile: "phoneme_coverage" | "emotion_contrast";
  quality_route: QualityRouteConfiguration;
}

export interface ProductionStage {
  stage_id: ProductionStageId;
  label: string;
  status: ProductionStageStatus;
}

export interface CharacterSummary {
  character_id: string;
  display_name: string;
  tier: "core" | "supporting" | "minor" | "uncertain";
  importance: number;
  voice_prompt: string;
  reference_status: "pending" | "accepted" | "rejected";
  reference_backend: "voxcpm2" | "indextts2" | "uploaded";
  preview_audio_url: string | null;
  emotion_variants: string[];
  color: "teal" | "violet" | "gold";
}

export interface DirectorSegment {
  segment_id: string;
  character_id: string;
  speaker: string;
  emotion: string;
  text: string;
}

export interface WorkspacePayload {
  project: { id: string; name: string; route: RouteMode };
  summary: { characters: number; accepted_references: number; segments: number; generated: number };
  workflow: ProductionStage[];
  available_templates: InferenceTemplate[];
  active_template: InferenceTemplate;
  characters: CharacterSummary[];
  segments: DirectorSegment[];
}

export type PreparationStatus = "imported" | "analyzed" | "characters_ready" | "director_ready";
export type PreparationAction = "analyze" | "extract_characters" | "generate_director";

export interface SourceSummary {
  project_id: string;
  file_name: string;
  display_name: string;
  size_bytes: number;
  encoding: "utf-8" | "gb18030";
  status: PreparationStatus;
}

export interface CharacterCandidate {
  candidate_id: string;
  display_name: string;
  decision: "pending" | "accepted" | "rejected";
  confidence: number;
  mention_count: number;
  dialogue_count: number;
  evidence: string[];
  reason: string;
}

export interface AnalysisAudit {
  schema_version: number;
  project_id: string;
  source_file: string;
  engine: "rule_based_preview";
  structure: {
    chapter_count: number;
    character_count: number;
    nonempty_line_count: number;
    estimated_segment_count: number;
    dialogue_count: number;
  };
  candidates: CharacterCandidate[];
  warnings: string[];
}

export interface PreparedCharacter {
  character_id: string;
  display_name: string;
  aliases: string[];
  confidence: number;
  importance: number;
  tier: "core" | "supporting" | "minor" | "uncertain";
  voice_prompt: string;
  evidence: Array<{ chapter_id: string; segment_id: string; text: string; evidence_type: string }>;
}

export interface PreparedDirectorSegment {
  segment_id: string;
  chapter_id: string;
  character_id: string;
  text: string;
  segment_type: "narration" | "dialogue";
  direction: {
    emotion: string;
    emotion_intensity: number;
    tone: string;
    pause_before_ms: number;
    pause_after_ms: number;
    speed: number;
    pitch: number;
    energy: number;
  };
}

export interface PreparationPreview {
  project_id: string;
  status: PreparationStatus;
  source: SourceSummary;
  analysis_audit: AnalysisAudit | null;
  character_voice_bible: {
    schema_version: number;
    project_id: string;
    source_text: string;
    characters: PreparedCharacter[];
  } | null;
  director_doc: {
    schema_version: number;
    project_id: string;
    character_bible_id: string;
    segments: PreparedDirectorSegment[];
  } | null;
}
