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
