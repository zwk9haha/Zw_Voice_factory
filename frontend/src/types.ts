export type RouteMode = "fast" | "quality";
export type VoiceInferenceMode = "cloud" | "hybrid" | "local";
export type ProductionStageId = "template" | "source" | "casting" | "references" | "emotions" | "director" | "quality_render";
export type ProductionStageStatus = "complete" | "current" | "ready" | "blocked";
export type StabilityPolicy = "disabled" | "benchmark_gated" | "enabled";
export type QualityModelId = "gpt_sovits_v1" | "gpt_sovits_v2" | "gpt_sovits_v2_pro" | "gpt_sovits_v2_pro_plus" | "gpt_sovits_v3" | "gpt_sovits_v4" | "indextts2";

export interface QualityRouteConfiguration {
  reference_backend: "voxcpm2" | "indextts2";
  render_backend: "gpt_sovits";
  stability_backend: "rvc" | null;
  stability_policy: StabilityPolicy;
}

export interface InferenceTemplate {
  template_id: string;
  display_name: string;
  inference_mode: VoiceInferenceMode;
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

export interface WorkspacePayload {
  project: { id: string; name: string; route: RouteMode };
  summary: { characters: number; accepted_references: number; segments: number; generated: number };
  workflow: ProductionStage[];
  available_templates: InferenceTemplate[];
  active_template: InferenceTemplate;
}

export type AudioJobKind = "voxcpm_reference" | "emotion_variant" | "quality_render" | "fast_render";
export type AudioJobStatus = "queued" | "running" | "complete" | "failed" | "cancelled";

export interface ProgramLoudnessPolicy {
  schema_version: number;
  enabled: boolean;
  target_lufs: number;
  true_peak_dbtp: number;
  target_lra: number;
  max_segment_gain_db: number;
}

export interface LoudnessMetrics {
  processor: string;
  status: string;
  input_lufs: number | null;
  output_lufs: number | null;
  input_true_peak_dbtp: number | null;
  output_true_peak_dbtp: number | null;
  loudness_range_lu: number | null;
  applied_gain_db: number;
  constrained_by_peak: boolean;
  final_program_pass: boolean;
  detail: string | null;
}

export interface AudioJobRequest {
  kind: AudioJobKind;
  text: string;
  project_id?: string;
  reference_id?: string;
  variant_id?: string;
  character_id?: string;
  segment_id?: string;
  voice_prompt?: string;
  reference_audio_url?: string;
  reference_text?: string;
  quality_model?: QualityModelId;
  emotion_description?: string;
  render_options?: QualityRenderOptions;
  fast_voice_id?: string;
  fast_speed?: number;
  fast_rvc_enabled?: boolean;
}

export interface AudioJob {
  job_id: string;
  kind: AudioJobKind;
  status: AudioJobStatus;
  progress: number;
  message: string;
  project_id: string | null;
  reference_id: string | null;
  variant_id: string | null;
  character_id: string | null;
  segment_id: string | null;
  reference_audio_url: string | null;
  quality_model: QualityModelId | null;
  render_options: QualityRenderOptions | null;
  fast_voice_id: string | null;
  fast_speed: number | null;
  fast_rvc_enabled: boolean | null;
  streaming: boolean;
  loudness_policy: ProgramLoudnessPolicy;
  loudness_metrics: LoudnessMetrics | null;
  base_output_url: string | null;
  rvc_output_url: string | null;
  rvc_status: "not_requested" | "bypassed" | "applied" | "fallback";
  rvc_model_id: string | null;
  rvc_profile_fingerprint: string | null;
  rvc_error: string | null;
  raw_output_url: string | null;
  output_url: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface CancelledQualityJobs {
  project_id: string;
  cancelled_count: number;
  cancelled_jobs: AudioJob[];
}

export interface DeletedQualityCache {
  project_id: string;
  deleted_count: number;
  deleted_bytes: number;
  deleted_jobs: AudioJob[];
}

export type PreparationStatus = "imported" | "analyzed" | "characters_ready" | "director_ready";
export type PreparationAction = "analyze" | "extract_characters" | "generate_director";
export type ProjectRevisionStatus = "running" | "analyzed" | "characters_ready" | "director_ready" | "failed";

export interface ProjectRevision {
  revision_id: string;
  display_name: string;
  created_at: string;
  updated_at: string;
  status: ProjectRevisionStatus;
  last_action: PreparationAction;
  error: string | null;
}

export interface ProjectRevisionWorkspace {
  schema_version: number;
  project_id: string;
  active_revision_id: string | null;
  revisions: ProjectRevision[];
}

export interface SourceSummary {
  project_id: string;
  file_name: string;
  display_name: string;
  size_bytes: number;
  encoding: "utf-8" | "gb18030";
  status: PreparationStatus;
}

export type LongFormMode = "auto" | "chapters" | "characters";
export type LongFormStrategy = "short" | "standard_chapters" | "inferred_chapters" | "characters";

export interface LongFormAnalysisSettings {
  schema_version: number;
  mode: LongFormMode;
  long_text_threshold: number;
  chapters_per_batch: number;
  characters_per_batch: number;
  parallelism: number;
}

export type LongFormAnalysisSettingsUpdate = Partial<Omit<LongFormAnalysisSettings, "schema_version">>;

export interface LongFormBatch {
  batch_id: string;
  index: number;
  title: string;
  start_char: number;
  end_char: number;
  character_count: number;
  chapter_start: number | null;
  chapter_end: number | null;
  state: "pending" | "analyzed" | "characters_ready" | "director_running" | "ready" | "failed";
  candidate_ids: string[];
  new_character_count: number;
  reused_character_count: number;
  director_completed_passages: number;
  director_total_passages: number;
}

export interface LongFormPlan {
  schema_version: number;
  plan_id: string;
  is_long_form: boolean;
  requested_mode: LongFormMode;
  strategy: LongFormStrategy;
  detection_backend: "local" | "hybrid" | "cloud" | "rules";
  detection_model: string | null;
  total_characters: number;
  total_chapters: number;
  batches: LongFormBatch[];
  warning: string | null;
}

export type EmotionPolicy = "skip" | "background" | "required_before_render";
export type RvcStabilityPolicy = "skip" | "prepare_candidates";
export type RvcPreparationStatus = "waiting_reference" | "reused" | "queued" | "building_material" | "training" | "benchmarking" | "awaiting_review" | "approved" | "deferred" | "skipped" | "rejected" | "failed";
export type ContinuousProductionRunState = "starting" | "running" | "pausing" | "paused" | "render_ready" | "complete" | "failed" | "cancelled";
export type ProductionSliceStatus = "pending" | "analyzing" | "casting" | "references" | "emotions" | "directing" | "render_ready" | "rendering" | "playing" | "complete" | "blocked" | "failed" | "skipped";
export type ContinuousProductionStage = "analysis" | "casting" | "references" | "emotions" | "director" | "quality_render";

export interface ContinuousProductionSettings {
  emotion_policy: EmotionPolicy;
  rvc_stability_policy: RvcStabilityPolicy;
  prefetch_slices: number;
  auto_play: boolean;
}

export interface ProductionFallback {
  fallback_id: string;
  slice_id: string;
  stage: ContinuousProductionStage;
  target_asset_id: string;
  actual_asset_id: string | null;
  reason: string;
  rerender_required: boolean;
  created_at: string;
}

export interface ProductionSliceState {
  slice_id: string;
  slice_revision_id: string;
  index: number;
  title: string;
  start_char: number;
  end_char: number;
  character_count: number;
  chapter_start: number | null;
  chapter_end: number | null;
  candidate_count: number;
  new_character_count: number;
  reused_character_count: number;
  director_completed_passages: number;
  director_total_passages: number;
  content_fingerprint: string;
  state: ProductionSliceStatus;
  current_stage: ContinuousProductionStage;
  progress: number;
  message: string;
  segment_count: number;
  completed_segment_count: number;
  provisional_reference_ids: string[];
  fallbacks: ProductionFallback[];
  error: string | null;
  render_ready_at: string | null;
  updated_at: string;
}

export interface ContinuousProductionEvent {
  event_id: string;
  kind: "run_state_changed" | "slice_state_changed" | "stage_progress" | "artifact_reused" | "fallback_applied" | "slice_render_ready" | "run_attention_required" | "rvc_state_changed";
  message: string;
  slice_id: string | null;
  stage: ContinuousProductionStage | null;
  progress: number | null;
  created_at: string;
}

export interface RvcPreparationTask {
  character_id: string;
  reference_id: string;
  display_name: string;
  priority: number;
  status: RvcPreparationStatus;
  progress: number;
  message: string;
  canonical_audio_version_id: string | null;
  training_job_id: string | null;
  model_id: string | null;
  benchmark_id: string | null;
  retry_count: number;
  benchmark_retry_count: number;
  eligible_after: string;
  error: string | null;
  updated_at: string;
}

export interface ContinuousProductionRun {
  schema_version: number;
  run_id: string;
  project_id: string;
  source_fingerprint: string;
  state: ContinuousProductionRunState;
  resume_state: ContinuousProductionRunState | null;
  settings: ContinuousProductionSettings;
  slices: ProductionSliceState[];
  current_slice_id: string | null;
  current_stage: ContinuousProductionStage;
  progress: number;
  message: string;
  failed_count: number;
  rvc_tasks: RvcPreparationTask[];
  rvc_progress: number;
  events: ContinuousProductionEvent[];
  started_at: string;
  updated_at: string;
  completed_at: string | null;
  elapsed_seconds: number;
}

export interface CharacterCandidate {
  candidate_id: string;
  display_name: string;
  decision: "pending" | "accepted" | "rejected";
  confidence: number;
  mention_count: number;
  dialogue_count: number;
  peak_batch_mentions: number;
  peak_batch_dialogue_count: number;
  batch_presence_count: number;
  local_importance: number;
  batch_ids: string[];
  evidence: string[];
  reason: string;
  screening_action: "keep" | "reject" | "merge" | null;
  canonical_candidate_id: string | null;
  screening_confidence: number | null;
  screening_rationale: string | null;
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
  long_form_plan: LongFormPlan | null;
  warnings: string[];
  candidate_screening_backend: "local" | "hybrid" | "cloud" | "rules" | null;
  candidate_screening_model: string | null;
  candidate_screening_input_count: number;
  candidate_screening_kept_count: number;
  candidate_screening_rejected_count: number;
  candidate_screening_merged_count: number;
  candidate_screening_completed_at: string | null;
}

export interface PreparedCharacter {
  character_id: string;
  display_name: string;
  aliases: string[];
  confidence: number;
  importance: number;
  tier: "core" | "supporting" | "minor" | "uncertain";
  gender: "male" | "female" | "unknown";
  age_range: string;
  personality_tags: string[];
  timbre_tags: string[];
  delivery_tags: string[];
  voice_constraints: string[];
  voice_prompt: string;
  voice_profile_confidence: number;
  voice_profile_rationale: string;
  evidence: Array<{ chapter_id: string; segment_id: string; text: string; evidence_type: string }>;
}

export type ReferenceSelectionMode = "automatic" | "optional" | "narrator_default";
export type ReferenceGenerationStatus = "not_generated" | "queued" | "running" | "generated" | "failed";
export type EmotionSelectionMode = "base" | "automatic" | "optional" | "custom";

export interface ReferenceAudioVersion {
  version_id: string;
  audio_url: string;
  source: "generated" | "uploaded" | "recorded" | "reused";
  decision: "provisional" | "accepted" | "rejected" | "superseded";
  created_at: string;
}

export interface VoiceResourceMatch {
  source_project_id: string;
  source_project_name: string;
  source_reference_id: string;
  source_version_id: string;
  display_name: string;
  gender: "male" | "female" | "unknown";
  voice_prompt: string;
  audio_url: string;
  audio_source: ReferenceAudioVersion["source"];
  created_at: string;
  similarity: number;
}

export interface ReferenceTextVersion {
  version_id: string;
  text: string;
  source: "initial" | "generated" | "edited";
  created_at: string;
}

export interface ReferencePlanItem {
  reference_id: string;
  source_character_id: string;
  display_name: string;
  gender: "male" | "female" | "unknown";
  importance: number;
  selection_mode: ReferenceSelectionMode;
  selected: boolean;
  locked: boolean;
  voice_prompt_locked: boolean;
  custom_voice_attributes: string;
  reference_text: string;
  active_reference_text_version_id: string | null;
  reference_text_versions: ReferenceTextVersion[];
  voice_prompt: string;
  reuse_reference_id: string | null;
  job_id: string | null;
  audio_url: string | null;
  audio_source: "generated" | "uploaded" | "recorded" | "reused" | null;
  active_audio_version_id: string | null;
  audio_versions: ReferenceAudioVersion[];
  status: ReferenceGenerationStatus;
  error: string | null;
}

export interface EmotionPlanItem {
  variant_id: string;
  parent_reference_id: string;
  source_character_id: string;
  display_name: string;
  emotion_name: string;
  description: string;
  intensity: number;
  importance: number;
  selection_mode: EmotionSelectionMode;
  selected: boolean;
  locked: boolean;
  reference_text: string;
  voice_prompt: string;
  job_id: string | null;
  audio_url: string | null;
  status: ReferenceGenerationStatus;
  error: string | null;
}

export interface PreparedDirectorSegment {
  segment_id: string;
  chapter_id: string;
  character_id: string;
  voice_reference_id: string | null;
  speaker_gender: "male" | "female" | "unknown";
  speaker_kind: "narration" | "named" | "extra" | "unknown";
  analysis_batch_id: string | null;
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

export interface CloudAnalysisEvent {
  project_id: string;
  call_id: string;
  direction: "INPUT" | "OUTPUT" | "ERROR";
  operation: string;
  provider: "custom" | "qwen" | "kimi" | "doubao" | "gemini";
  protocol: "chat_completions" | "responses";
  model: string;
  attempt: number;
  structured_mode: string;
  total_chars: number;
  preview: string;
  status_code: number | null;
  elapsed_seconds: number | null;
  created_at: string;
}

export interface AnalysisActivity {
  schema_version: number;
  project_id: string;
  action: PreparationAction | null;
  state: "idle" | "running" | "complete" | "failed" | "cancelled";
  cancellable: boolean;
  percent: number;
  message: string;
  backend: string;
  model: string | null;
  started_at: string | null;
  completed_at: string | null;
  elapsed_seconds: number;
  current_batch: number | null;
  total_batches: number | null;
  input_events: CloudAnalysisEvent[];
  output_events: CloudAnalysisEvent[];
  updated_at: string;
}

export interface PreparationPreview {
  project_id: string;
  status: PreparationStatus;
  source: SourceSummary;
  analysis_settings: LongFormAnalysisSettings;
  analysis_audit: AnalysisAudit | null;
  character_voice_bible: {
    schema_version: number;
    project_id: string;
    source_text: string;
    analysis_backend: "local" | "hybrid" | "cloud" | "rules";
    analysis_model: string | null;
    characters: PreparedCharacter[];
  } | null;
  reference_plan: {
    schema_version: number;
    project_id: string;
    generation_backend: "voxcpm2";
    automatic_threshold: number;
    automatic_items_locked: boolean;
    items: ReferencePlanItem[];
  } | null;
  emotion_plan: {
    schema_version: number;
    project_id: string;
    generation_backend: "voxcpm2";
    skipped: boolean;
    automatic_threshold: number;
    automatic_items_locked: boolean;
    items: EmotionPlanItem[];
  } | null;
  director_doc: {
    schema_version: number;
    project_id: string;
    character_bible_id: string;
    analysis_backend: "local" | "hybrid" | "cloud" | "rules";
    analysis_model: string | null;
    warnings: string[];
    segments: PreparedDirectorSegment[];
  } | null;
}

export interface VoiceAnalysisStatus {
  backend: "local" | "hybrid" | "cloud" | "rules";
  available: boolean;
  model: string | null;
  detail: string;
  taxonomy_version: number;
  model_store: string | null;
}

export type VoiceAnalysisProvider = "custom" | "qwen" | "kimi" | "doubao" | "gemini";
export type VoiceAnalysisApiProtocol = "chat_completions" | "responses";
export type VoiceAnalysisProfileHealth = "unknown" | "healthy" | "failed" | "cooldown";

export interface VoiceAnalysisCloudProfile {
  profile_id: string;
  name: string;
  provider: VoiceAnalysisProvider;
  base_url: string;
  model: string;
  api_protocol: VoiceAnalysisApiProtocol;
  api_key_configured: boolean;
  enabled: boolean;
  priority: number;
  health: VoiceAnalysisProfileHealth;
  last_error: string | null;
}

export interface VoiceAnalysisConfiguration {
  backend: "local" | "hybrid" | "cloud" | "rules";
  provider: VoiceAnalysisProvider;
  base_url: string;
  model: string;
  api_protocol: VoiceAnalysisApiProtocol;
  api_key_configured: boolean;
  failover_enabled: boolean;
  cloud_parallelism: number;
  cloud_director_batch_size: number;
  profiles: VoiceAnalysisCloudProfile[];
}

export interface VoiceAnalysisCloudProfileUpdate {
  profile_id: string | null;
  name: string;
  provider: VoiceAnalysisProvider;
  base_url: string;
  model: string;
  api_protocol: VoiceAnalysisApiProtocol;
  api_key: string | null;
  clear_api_key?: boolean;
  enabled: boolean;
}

export interface VoiceAnalysisConfigurationUpdate {
  backend: VoiceInferenceMode;
  provider?: VoiceAnalysisProvider;
  base_url?: string;
  model?: string;
  api_protocol?: VoiceAnalysisApiProtocol;
  api_key?: string | null;
  clear_api_key?: boolean;
  failover_enabled?: boolean;
  cloud_parallelism?: number;
  cloud_director_batch_size?: number;
  profiles?: VoiceAnalysisCloudProfileUpdate[];
}

export interface VoiceAnalysisModelCatalogRequest {
  profile_id?: string | null;
  provider: VoiceAnalysisProvider;
  base_url: string;
  api_key: string | null;
}

export interface VoiceAnalysisModelOption {
  id: string;
  owned_by: string | null;
  supported_endpoint_types: string[];
}

export interface VoiceAnalysisModelCatalog {
  provider: VoiceAnalysisProvider;
  base_url: string;
  models: VoiceAnalysisModelOption[];
}

export interface QualityModelOption {
  model_id: QualityModelId;
  label: string;
  effect: string;
  renderer: "gpt_sovits" | "indextts2";
  available: boolean;
  unavailable_reason: string | null;
}

export interface ProductionSettings {
  selected_quality_model: QualityModelId;
  render_options: QualityRenderOptions;
  loudness_policy: ProgramLoudnessPolicy;
  narrator_gender: "male" | "female";
  auto_delete_played_cache: boolean;
  cache_keep_sentences: number;
  quality_models: QualityModelOption[];
}

export interface RvcTrainingOptions {
  version: "v2";
  sample_rate: "40k";
  pitch_method: "rmvpe_gpu" | "rmvpe";
  pitch_guidance: true;
  epochs: number;
  save_every_epochs: number;
  batch_size: number;
  process_count: number;
  gpu_ids: string;
  cache_gpu: boolean;
}

export type RvcRoute = "quality" | "fast";
export type RvcTrainingPurpose = "quality_stability" | "fast_identity" | "both";
export type RvcModelStatus = "unverified" | "candidate" | "approved" | "rejected" | "retired" | "stale";

export interface RvcInferenceProfile {
  schema_version: number;
  preset: "conservative" | "balanced" | "strong" | "custom";
  f0_method: "rmvpe";
  f0_up_key: number;
  index_rate: number;
  filter_radius: number;
  resample_sr: number;
  rms_mix_rate: number;
  protect: number;
}

export interface RvcCharacterSettings {
  character_id: string;
  train_enabled: boolean;
  stability_enabled: boolean;
  fast_route_enabled: boolean;
  selected_model_id: string | null;
}

export interface RvcProjectSettings {
  quality_stability_enabled: boolean;
  fast_route_enabled: boolean;
  training_options: RvcTrainingOptions;
  characters: RvcCharacterSettings[];
}

export interface RvcModelAsset {
  schema_version: number;
  model_id: string;
  label: string;
  weight_path: string;
  index_path: string | null;
  source: "existing" | "trained";
  character_id: string | null;
  training_set_revision_id: string | null;
  status: RvcModelStatus;
  approved_routes: RvcRoute[];
  inference_profiles: Record<RvcRoute, RvcInferenceProfile>;
  profile_fingerprints: Record<RvcRoute, string>;
  benchmark_ids: string[];
  manifest_path: string | null;
  size_mb: number;
  updated_at: string;
}

export interface RvcCharacterView {
  character_id: string;
  reference_id: string;
  display_name: string;
  gender: "male" | "female" | "unknown";
  material_count: number;
  material_duration_seconds: number;
  training_ready: boolean;
  minimum_training_seconds: number;
  sample_audio_url: string | null;
  train_enabled: boolean;
  stability_enabled: boolean;
  fast_route_enabled: boolean;
  selected_model_id: string | null;
  selected_model_status: RvcModelStatus | null;
  quality_approved: boolean;
  fast_approved: boolean;
  active_job_id: string | null;
}

export interface RvcTrainingSetClip {
  clip_id: string;
  source: "canonical" | "reference_conditioned";
  text: string | null;
  path: string;
  sha256: string;
  duration_seconds: number;
  peak_dbfs: number | null;
  clipping_ratio: number;
  silence_ratio: number;
  accepted: boolean;
  rejection_reason: string | null;
}

export interface RvcTrainingSetRevision {
  schema_version: number;
  revision_id: string;
  project_id: string;
  character_id: string;
  reference_id: string;
  canonical_audio_version_id: string | null;
  canonical_audio_url: string;
  canonical_sha256: string;
  purpose: RvcTrainingPurpose;
  status: "building" | "ready" | "failed";
  minimum_duration_seconds: number;
  total_duration_seconds: number;
  clips: RvcTrainingSetClip[];
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface RvcBenchmarkSample {
  sample_id: string;
  text: string;
  base_audio_url: string;
  rvc_audio_url: string;
  duration_ratio: number;
  base_peak_dbfs: number | null;
  rvc_peak_dbfs: number | null;
  rvc_clipping_ratio: number;
  rvc_silence_ratio: number;
  automatic_pass: boolean;
}

export interface RvcBenchmarkReport {
  schema_version: number;
  benchmark_id: string;
  project_id: string;
  character_id: string;
  model_id: string;
  route: RvcRoute;
  canonical_audio_version_id: string | null;
  canonical_sha256: string | null;
  inference_profile: RvcInferenceProfile | null;
  profile_fingerprint: string | null;
  status: "queued" | "running" | "complete" | "failed";
  progress: number;
  message: string;
  automatic_pass: boolean;
  decision: "pending" | "approved" | "rejected";
  preference_percent: number | null;
  identity_improved: boolean | null;
  intelligibility_preserved: boolean | null;
  expression_preserved: boolean | null;
  reviewer_notes: string;
  samples: RvcBenchmarkSample[];
  limitations: string[];
  error: string | null;
  created_at: string;
  updated_at: string;
}

export type RvcTrainingJobStatus = "queued" | "running" | "complete" | "failed" | "cancelled";
export type RvcTrainingStage =
  | "queued"
  | "preparing_material"
  | "preprocessing"
  | "extracting_pitch"
  | "extracting_features"
  | "starting_training"
  | "training"
  | "building_index"
  | "finalizing"
  | "complete"
  | "failed"
  | "cancelled";

export interface RvcTrainingJob {
  job_id: string;
  project_id: string;
  character_id: string;
  display_name: string;
  status: RvcTrainingJobStatus;
  progress: number;
  message: string;
  stage: RvcTrainingStage;
  current_epoch: number | null;
  total_epochs: number | null;
  started_at: string | null;
  completed_at: string | null;
  elapsed_seconds: number;
  last_log: string | null;
  options: RvcTrainingOptions;
  material_count: number;
  material_duration_seconds: number;
  purpose: RvcTrainingPurpose;
  training_set_revision_id: string | null;
  model_id: string | null;
  log_id: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface RvcWorkspace {
  project_id: string;
  tool_available: boolean;
  training_runtime_available: boolean;
  runtime_detail: string;
  settings: RvcProjectSettings;
  characters: RvcCharacterView[];
  models: RvcModelAsset[];
  training_sets: RvcTrainingSetRevision[];
  benchmarks: RvcBenchmarkReport[];
  jobs: RvcTrainingJob[];
}

export type RvcPreviewSource = "voxcpm2" | "gpt_sovits_v2_pro_plus";

export interface RvcPreviewResult {
  preview_id: string;
  project_id: string;
  character_id: string;
  source: RvcPreviewSource;
  source_label: string;
  text: string;
  base_audio_url: string;
  rvc_audio_url: string;
  rvc_model_id: string;
  created_at: string;
}

export interface RuntimeLogEntry {
  log_id: string;
  category: string;
  name: string;
  size_bytes: number;
  updated_at: string;
  download_url: string;
}

export interface RvcSettingsUpdate {
  quality_stability_enabled?: boolean;
  fast_route_enabled?: boolean;
  character_id?: string;
  train_enabled?: boolean;
  stability_enabled?: boolean;
  fast_character_enabled?: boolean;
  selected_model_id?: string | null;
  training_options?: RvcTrainingOptions;
}

export interface QualityRenderOptions {
  chunk_length: number;
  top_k: number;
  top_p: number;
  temperature: number;
  repetition_penalty: number;
  speed_factor: number;
  fragment_interval: number;
  batch_size: number;
  split_bucket: boolean;
  seed: number;
  emotion_strength: number;
}

export interface MergedAudio {
  project_id: string;
  output_url: string;
  segment_count: number;
  duration_seconds: number;
  loudness_metrics: LoudnessMetrics | null;
}

export interface FastVoiceOption {
  voice_id: string;
  label: string;
  gender: "male" | "female" | "unknown";
  effect: string;
}

export interface FastVoiceAssignment {
  character_id: string;
  voice_id: string;
}

export interface FastRouteWorkspace {
  project_id: string;
  engine: string;
  voices: FastVoiceOption[];
  settings: {
    default_male_voice_id: string;
    default_female_voice_id: string;
    default_unknown_voice_id: string;
    assignments: FastVoiceAssignment[];
  };
}

export interface SystemResources {
  cpu: { percent: number };
  memory: { percent: number; used_gb: number; total_gb: number };
  gpu: {
    available: boolean;
    name: string | null;
    percent: number | null;
    memory_percent: number | null;
    used_mb: number | null;
    total_mb: number | null;
  };
  timestamp: string;
}
