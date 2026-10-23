import type { AnalysisActivity, AudioJob, AudioJobKind, AudioJobRequest, CancelledQualityJobs, ContinuousProductionRun, ContinuousProductionSettings, DeletedQualityCache, FastRouteWorkspace, GenieCompareResult, GenieVoiceOption, LongFormAnalysisSettingsUpdate, MergedAudio, PlatformProject, PlatformProjectType, PlatformProjectWorkspace, PreparationAction, PreparationPreview, ProductionSettings, ProgramLoudnessPolicy, ProjectRevisionWorkspace, ProjectVoiceDecision, ProjectVoiceSnapshot, ProjectWorkflowPayload, QualityModelId, QualityRenderOptions, RvcBenchmarkReport, RvcInferenceProfile, RvcPreviewResult, RvcPreviewSource, RvcRoute, RvcSettingsUpdate, RvcTrainingJob, RvcTrainingOptions, RvcTrainingPurpose, RvcWorkspace, RuntimeLogEntry, SourceSummary, SystemResources, VoiceAnalysisConfiguration, VoiceAnalysisConfigurationUpdate, VoiceAnalysisModelCatalog, VoiceAnalysisModelCatalogRequest, VoiceAnalysisStatus, VoiceCatalogAsset, VoiceCatalogPayload, VoiceResourceMatch, VoiceTask, WorkflowDefinition, WorkflowGraphValidation, WorkflowNodeManifest, WorkflowRun, WorkspacePayload } from "./types";

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

async function responseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new ApiError(response.status, payload?.detail ?? `请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchWorkspace(): Promise<WorkspacePayload> {
  const response = await fetch("/api/workspace");
  return responseJson<WorkspacePayload>(response);
}

export async function fetchWorkflowNodeTypes(): Promise<WorkflowNodeManifest[]> {
  return responseJson<WorkflowNodeManifest[]>(await fetch("/api/workflows/node-types"));
}

type WorkflowDraftPayload = Pick<WorkflowDefinition, "name" | "version" | "description" | "nodes" | "inputs" | "edges" | "outputs" | "allow_pause" | "allow_retry" | "allow_recovery">;

export async function fetchWorkflows(): Promise<WorkflowDefinition[]> {
  return responseJson<WorkflowDefinition[]>(await fetch("/api/workflows"));
}

export async function createWorkflow(payload: WorkflowDraftPayload): Promise<WorkflowDefinition> {
  return responseJson<WorkflowDefinition>(await fetch("/api/workflows", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function updateWorkflow(workflowId: string, payload: WorkflowDraftPayload): Promise<WorkflowDefinition> {
  return responseJson<WorkflowDefinition>(await fetch(`/api/workflows/${encodeURIComponent(workflowId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function validateWorkflow(payload: WorkflowDraftPayload): Promise<WorkflowGraphValidation> {
  return responseJson<WorkflowGraphValidation>(await fetch("/api/workflows/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

export async function publishWorkflow(workflowId: string): Promise<WorkflowDefinition> {
  return responseJson<WorkflowDefinition>(await fetch(`/api/workflows/${encodeURIComponent(workflowId)}/publish`, { method: "POST" }));
}

export async function createWorkflowRun(workflowId: string, input: Record<string, unknown>): Promise<WorkflowRun> {
  return responseJson<WorkflowRun>(await fetch(`/api/workflows/${encodeURIComponent(workflowId)}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, auto_start: true }),
  }));
}

export async function fetchWorkflowRun(runId: string): Promise<WorkflowRun> {
  return responseJson<WorkflowRun>(await fetch(`/api/workflows/runs/${encodeURIComponent(runId)}`));
}

export async function fetchWorkflowRuns(workflowId: string): Promise<WorkflowRun[]> {
  return responseJson<WorkflowRun[]>(await fetch(`/api/workflows/${encodeURIComponent(workflowId)}/runs`));
}

export async function fetchProjectWorkflow(projectId: string): Promise<ProjectWorkflowPayload> {
  return responseJson<ProjectWorkflowPayload>(await fetch(`/api/workflows/projects/${encodeURIComponent(projectId)}`));
}

export async function bindProjectWorkflow(projectId: string, workflowId: string): Promise<ProjectWorkflowPayload> {
  return responseJson<ProjectWorkflowPayload>(await fetch(`/api/workflows/projects/${encodeURIComponent(projectId)}/binding`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workflow_id: workflowId }),
  }));
}

export async function createProjectWorkflowRun(projectId: string, input: Record<string, unknown>): Promise<WorkflowRun> {
  return responseJson<WorkflowRun>(await fetch(`/api/workflows/projects/${encodeURIComponent(projectId)}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, auto_start: true }),
  }));
}

export async function workflowRunAction(runId: string, action: "start" | "pause" | "resume" | "retry" | "cancel"): Promise<WorkflowRun> {
  return responseJson<WorkflowRun>(await fetch(`/api/workflows/runs/${encodeURIComponent(runId)}/${action}`, { method: "POST" }));
}

export async function reviewWorkflowNode(runId: string, nodeId: string, decision: "approved" | "rejected", comment?: string, edits?: Record<string, unknown>): Promise<WorkflowRun> {
  return responseJson<WorkflowRun>(await fetch(`/api/workflows/runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, comment: comment?.trim() || null, edits }),
  }));
}

export async function fetchPlatformProjects(): Promise<PlatformProject[]> {
  return responseJson<PlatformProject[]>(await fetch("/api/platform/projects"));
}

export async function fetchVoiceCatalog(): Promise<VoiceCatalogPayload> {
  return responseJson<VoiceCatalogPayload>(await fetch("/api/voice-catalog"));
}

export async function updateVoiceCatalogAsset(assetId: string, changes: Partial<Pick<VoiceCatalogAsset, "voice_prompt" | "timbre_tags" | "delivery_tags" | "voice_constraints" | "use_cases" | "reference_text" | "generation_preset_id">>): Promise<VoiceCatalogAsset> {
  return responseJson<VoiceCatalogAsset>(await fetch(`/api/voice-catalog/assets/${encodeURIComponent(assetId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  }));
}

export async function generateVoiceCatalogAsset(assetId: string, request: { reference_text?: string; voice_prompt?: string; preset_id?: string }): Promise<VoiceCatalogAsset> {
  return responseJson<VoiceCatalogAsset>(await fetch(`/api/voice-catalog/assets/${encodeURIComponent(assetId)}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  }));
}

export async function deriveVoiceCatalogAsset(assetId: string, request: { parent_version_id: string; variant_name: string; description: string; emotion?: string; intensity: number; reference_text?: string; preset_id?: string }): Promise<VoiceCatalogAsset> {
  return responseJson<VoiceCatalogAsset>(await fetch(`/api/voice-catalog/assets/${encodeURIComponent(assetId)}/derive`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  }));
}

export async function activateVoiceCatalogVersion(assetId: string, versionId: string): Promise<VoiceCatalogAsset> {
  return responseJson<VoiceCatalogAsset>(await fetch(`/api/voice-catalog/assets/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(versionId)}/activate`, { method: "POST" }));
}

export async function deleteVoiceCatalogVersion(assetId: string, versionId: string): Promise<VoiceCatalogAsset> {
  return responseJson<VoiceCatalogAsset>(await fetch(`/api/voice-catalog/assets/${encodeURIComponent(assetId)}/versions/${encodeURIComponent(versionId)}`, { method: "DELETE" }));
}

export async function createPlatformProject(
  name: string,
  contentType: PlatformProjectType,
  text: string,
): Promise<PlatformProject> {
  return responseJson<PlatformProject>(await fetch("/api/platform/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, content_type: contentType, text }),
  }));
}

export async function fetchPlatformProjectWorkspace(projectId: string): Promise<PlatformProjectWorkspace> {
  return responseJson<PlatformProjectWorkspace>(await fetch(`/api/platform/projects/${encodeURIComponent(projectId)}/workspace`));
}

export async function matchOrGenerateVoice(
  projectId: string,
  request: {
    speaker_key?: string;
    character_id?: string;
    display_name: string;
    profile?: Record<string, unknown>;
    gender?: string;
    age_range?: string;
    timbre_tags?: string[];
    delivery_tags?: string[];
    use_cases?: string[];
    voice_prompt?: string;
    reference_text?: string;
    limit?: number;
    minimum_score?: number;
  },
): Promise<ProjectVoiceDecision> {
  return responseJson<ProjectVoiceDecision>(await fetch(`/api/platform/projects/${encodeURIComponent(projectId)}/voice-decisions/match-or-generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  }));
}

export async function startVoiceDecisionGeneration(projectId: string, decisionId: string): Promise<ProjectVoiceDecision> {
  return responseJson<ProjectVoiceDecision>(await fetch(`/api/platform/projects/${encodeURIComponent(projectId)}/voice-decisions/${encodeURIComponent(decisionId)}/generate`, { method: "POST" }));
}

export async function acceptProjectVoiceSnapshot(
  projectId: string,
  request: {
    speaker_key: string;
    display_name: string;
    source: "catalog" | "generated";
    catalog_asset_id?: string;
    catalog_version_id?: string;
    job_id?: string;
    voice_prompt?: string;
    reference_text?: string;
    match_score?: number;
    match_reasons?: string[];
  },
): Promise<ProjectVoiceSnapshot> {
  return responseJson<ProjectVoiceSnapshot>(await fetch(`/api/platform/projects/${encodeURIComponent(projectId)}/voice-snapshots`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  }));
}

export async function createQuickVoicePreview(projectId: string): Promise<VoiceTask> {
  return responseJson<VoiceTask>(await fetch(`/api/platform/projects/${encodeURIComponent(projectId)}/tasks/quick-preview`, { method: "POST" }));
}

export async function fetchSources(): Promise<SourceSummary[]> {
  return responseJson<SourceSummary[]>(await fetch("/api/sources"));
}

export async function importTxtSource(file: File, projectName?: string): Promise<SourceSummary> {
  const body = new FormData();
  body.append("file", file);
  if (projectName) body.append("project_name", projectName);
  return responseJson<SourceSummary>(await fetch("/api/sources", { method: "POST", body }));
}

export async function fetchPreparationPreview(projectId: string): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(await fetch(`/api/projects/${encodeURIComponent(projectId)}/preparation/preview`));
}

export async function fetchAnalysisActivity(projectId: string): Promise<AnalysisActivity> {
  return responseJson<AnalysisActivity>(await fetch(`/api/projects/${encodeURIComponent(projectId)}/analysis-activity`));
}

export async function updateLongFormAnalysisSettings(
  projectId: string,
  update: LongFormAnalysisSettingsUpdate,
): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/analysis-settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }),
  );
}

export async function fetchVoiceAnalysisStatus(): Promise<VoiceAnalysisStatus> {
  return responseJson<VoiceAnalysisStatus>(await fetch("/api/voice-analysis/status"));
}

export async function fetchVoiceAnalysisConfig(): Promise<VoiceAnalysisConfiguration> {
  return responseJson<VoiceAnalysisConfiguration>(await fetch("/api/voice-analysis/config"));
}

export async function updateVoiceAnalysisConfig(
  configuration: VoiceAnalysisConfigurationUpdate,
): Promise<VoiceAnalysisConfiguration> {
  return responseJson<VoiceAnalysisConfiguration>(
    await fetch("/api/voice-analysis/config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(configuration),
    }),
  );
}

export async function testVoiceAnalysisConfig(): Promise<VoiceAnalysisStatus> {
  return responseJson<VoiceAnalysisStatus>(
    await fetch("/api/voice-analysis/test", { method: "POST" }),
  );
}

export async function testVoiceAnalysisProfile(profileId: string): Promise<VoiceAnalysisConfiguration> {
  return responseJson<VoiceAnalysisConfiguration>(
    await fetch(`/api/voice-analysis/profiles/${encodeURIComponent(profileId)}/test`, { method: "POST" }),
  );
}

export async function fetchVoiceAnalysisModels(
  request: VoiceAnalysisModelCatalogRequest,
): Promise<VoiceAnalysisModelCatalog> {
  return responseJson<VoiceAnalysisModelCatalog>(
    await fetch("/api/voice-analysis/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }),
  );
}

export async function runPreparationAction(
  projectId: string,
  action: PreparationAction,
  revisionId?: string | null,
  resume = false,
): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/preparation`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, revision_id: revisionId || undefined, resume }),
    }),
  );
}

export async function cancelPreparationAction(projectId: string): Promise<AnalysisActivity> {
  return responseJson<AnalysisActivity>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/preparation/cancel`, {
      method: "POST",
    }),
  );
}

export async function fetchContinuousProduction(projectId: string): Promise<ContinuousProductionRun> {
  return responseJson<ContinuousProductionRun>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/continuous-production`),
  );
}

export async function startContinuousProduction(
  projectId: string,
  settings: ContinuousProductionSettings,
): Promise<ContinuousProductionRun> {
  return responseJson<ContinuousProductionRun>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/continuous-production`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
  );
}

export async function commandContinuousProduction(
  projectId: string,
  command: "pause" | "resume" | "retry" | "skip" | "cancel",
): Promise<ContinuousProductionRun> {
  return responseJson<ContinuousProductionRun>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/continuous-production/${command}`, { method: "POST" }),
  );
}

export async function updateContinuousProductionSettings(
  projectId: string,
  settings: Partial<ContinuousProductionSettings>,
): Promise<ContinuousProductionRun> {
  return responseJson<ContinuousProductionRun>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/continuous-production/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
  );
}

export async function fetchProjectRevisions(projectId: string): Promise<ProjectRevisionWorkspace> {
  return responseJson<ProjectRevisionWorkspace>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/revisions`),
  );
}

export async function activateProjectRevision(projectId: string, revisionId: string): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/revisions/${encodeURIComponent(revisionId)}/activate`, {
      method: "POST",
    }),
  );
}

export async function deleteProjectRevision(projectId: string, revisionId: string): Promise<ProjectRevisionWorkspace> {
  return responseJson<ProjectRevisionWorkspace>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/revisions/${encodeURIComponent(revisionId)}`, {
      method: "DELETE",
    }),
  );
}

export async function updateReferenceSelection(projectId: string, referenceId: string, selected: boolean): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected }),
    }),
  );
}

export async function updateReferenceThreshold(projectId: string, automaticThreshold: number): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/reference-settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ automatic_threshold: automaticThreshold }),
    }),
  );
}

export async function updateAutomaticReferenceLock(projectId: string, automaticItemsLocked: boolean): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/reference-settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ automatic_items_locked: automaticItemsLocked }),
    }),
  );
}

export async function updateReferenceVoicePrompt(projectId: string, referenceId: string, voicePrompt: string): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice_prompt: voicePrompt }),
    }),
  );
}

export async function updateReferencePromptLock(projectId: string, referenceId: string, locked: boolean): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice_prompt_locked: locked }),
    }),
  );
}

export async function regenerateVoiceProfiles(
  projectId: string,
  target: { character_id?: string; reference_id?: string; custom_attributes?: string } = {},
): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/voice-profiles/regenerate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(target),
    }),
  );
}

export async function updateReferenceText(projectId: string, referenceId: string, referenceText: string): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reference_text: referenceText }),
    }),
  );
}

export async function generateReferenceText(projectId: string, referenceId: string): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}/text/generate`, {
      method: "POST",
    }),
  );
}

export async function activateReferenceTextVersion(projectId: string, referenceId: string, versionId: string): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}/text/${encodeURIComponent(versionId)}`, {
      method: "PATCH",
    }),
  );
}

export async function deleteReferenceTextVersion(projectId: string, referenceId: string, versionId: string): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}/text/${encodeURIComponent(versionId)}`, {
      method: "DELETE",
    }),
  );
}

export async function fetchVoiceResourceMatches(projectId: string, referenceId: string): Promise<VoiceResourceMatch[]> {
  return responseJson<VoiceResourceMatch[]>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}/matches`),
  );
}

export async function reuseVoiceResource(
  projectId: string,
  referenceId: string,
  source: Pick<VoiceResourceMatch, "source_project_id" | "source_reference_id" | "source_version_id">,
): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}/reuse`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(source),
    }),
  );
}

export async function clearReferenceAudioCache(projectId: string, referenceId: string): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}/audio`, {
      method: "DELETE",
    }),
  );
}

export async function updateDirectorSegmentVoice(
  projectId: string,
  segmentId: string,
  voiceReferenceId: string | null,
): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/director/${encodeURIComponent(segmentId)}/voice`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice_reference_id: voiceReferenceId }),
    }),
  );
}

export async function deleteDirectorCache(projectId: string): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/director`, { method: "DELETE" }),
  );
}

export async function updateEmotionSettings(
  projectId: string,
  settings: { skipped?: boolean; automatic_threshold?: number; automatic_items_locked?: boolean },
): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/emotion-settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
  );
}

export async function updateEmotionSelection(projectId: string, variantId: string, selected: boolean): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/emotions/${encodeURIComponent(variantId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected }),
    }),
  );
}

export async function createEmotionVariant(
  projectId: string,
  payload: { parent_reference_id: string; emotion_name: string; description: string; intensity: number },
): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/emotions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function deleteEmotionVariant(projectId: string, variantId: string): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/emotions/${encodeURIComponent(variantId)}`, { method: "DELETE" }),
  );
}

export async function fetchProductionSettings(): Promise<ProductionSettings> {
  return responseJson<ProductionSettings>(await fetch("/api/production/settings"));
}

export async function updateQualityModel(selectedQualityModel: QualityModelId): Promise<ProductionSettings> {
  return responseJson<ProductionSettings>(
    await fetch("/api/production/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected_quality_model: selectedQualityModel }),
    }),
  );
}

export async function updateQualityRenderOptions(renderOptions: QualityRenderOptions): Promise<ProductionSettings> {
  return responseJson<ProductionSettings>(
    await fetch("/api/production/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ render_options: renderOptions }),
    }),
  );
}

export async function updateProductionPreferences(settings: {
  render_options?: QualityRenderOptions;
  loudness_policy?: ProgramLoudnessPolicy;
  narrator_gender?: "male" | "female";
  auto_delete_played_cache?: boolean;
  cache_keep_sentences?: number;
}): Promise<ProductionSettings> {
  return responseJson<ProductionSettings>(
    await fetch("/api/production/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
  );
}

export async function fetchGenieVoices(): Promise<GenieVoiceOption[]> {
  return responseJson<GenieVoiceOption[]>(await fetch("/api/experiments/genie/voices"));
}

export async function compareGenieExperiment(request: {
  project_id: string;
  text: string;
  reference_audio_url: string;
  reference_text: string;
  voice_id: string;
  character_name?: string;
  language?: string;
  gsv_model: QualityModelId;
  render_options: QualityRenderOptions;
}): Promise<GenieCompareResult> {
  return responseJson<GenieCompareResult>(
    await fetch("/api/experiments/genie/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }),
  );
}

export async function fetchRvcWorkspace(projectId: string): Promise<RvcWorkspace> {
  return responseJson<RvcWorkspace>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/rvc/workspace`),
  );
}

export async function updateRvcSettings(projectId: string, settings: RvcSettingsUpdate): Promise<RvcWorkspace> {
  return responseJson<RvcWorkspace>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/rvc/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
  );
}

export async function updateRvcInferenceProfile(
  projectId: string,
  modelId: string,
  route: RvcRoute,
  profile: RvcInferenceProfile,
): Promise<RvcWorkspace> {
  return responseJson<RvcWorkspace>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/rvc/models/${encodeURIComponent(modelId)}/profiles/${route}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile }),
    }),
  );
}

export async function createRvcTrainingJob(
  projectId: string,
  characterId: string,
  options: RvcTrainingOptions,
  purpose: RvcTrainingPurpose = "quality_stability",
): Promise<RvcTrainingJob> {
  return responseJson<RvcTrainingJob>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/rvc/training`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ character_id: characterId, purpose, options }),
    }),
  );
}

export async function createRvcBenchmark(
  projectId: string,
  characterId: string,
  modelId: string,
  route: RvcRoute,
  fastVoiceId = "suyingxue",
): Promise<RvcBenchmarkReport> {
  return responseJson<RvcBenchmarkReport>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/rvc/benchmarks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ character_id: characterId, model_id: modelId, route, fast_voice_id: fastVoiceId }),
    }),
  );
}

export async function fetchRvcBenchmark(benchmarkId: string): Promise<RvcBenchmarkReport> {
  return responseJson<RvcBenchmarkReport>(
    await fetch(`/api/rvc/benchmarks/${encodeURIComponent(benchmarkId)}`),
  );
}

export async function reviewRvcBenchmark(
  benchmarkId: string,
  review: {
    approved: boolean;
    preference_percent: number;
    identity_improved: boolean;
    intelligibility_preserved: boolean;
    expression_preserved: boolean;
    notes: string;
  },
): Promise<RvcWorkspace> {
  return responseJson<RvcWorkspace>(
    await fetch(`/api/rvc/benchmarks/${encodeURIComponent(benchmarkId)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(review),
    }),
  );
}

export async function fetchRvcTrainingJob(jobId: string): Promise<RvcTrainingJob> {
  return responseJson<RvcTrainingJob>(await fetch(`/api/rvc/jobs/${encodeURIComponent(jobId)}`));
}

export async function createRvcPreview(
  projectId: string,
  characterId: string,
  text: string,
  source: RvcPreviewSource,
): Promise<RvcPreviewResult> {
  return responseJson<RvcPreviewResult>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/rvc/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ character_id: characterId, text, source }),
    }),
  );
}

export async function fetchRuntimeLogs(): Promise<RuntimeLogEntry[]> {
  return responseJson<RuntimeLogEntry[]>(await fetch("/api/logs?limit=200"));
}

export async function fetchRuntimeLog(logId: string): Promise<string> {
  const response = await fetch(`/api/logs/${logId.split("/").map(encodeURIComponent).join("/")}?tail=2000`);
  if (!response.ok) throw new ApiError(response.status, "日志读取失败");
  return response.text();
}

export async function cancelRvcTrainingJob(jobId: string): Promise<RvcTrainingJob> {
  return responseJson<RvcTrainingJob>(
    await fetch(`/api/rvc/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" }),
  );
}

export async function createAudioJob(request: AudioJobRequest): Promise<AudioJob> {
  return responseJson<AudioJob>(
    await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }),
  );
}

export async function openQualityAudioStream(
  request: AudioJobRequest,
  signal: AbortSignal,
): Promise<{ response: Response; jobId: string }> {
  const response = await fetch("/api/jobs/quality-stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new ApiError(response.status, payload?.detail ?? `流式请求失败：${response.status}`);
  }
  const jobId = response.headers.get("X-Zw-Stream-Job-Id");
  if (!response.body || !jobId || response.headers.get("X-Zw-Stream-Protocol") !== "1") {
    throw new Error("服务端没有返回有效的质量音频流");
  }
  return { response, jobId };
}

export async function fetchAudioJob(jobId: string): Promise<AudioJob> {
  return responseJson<AudioJob>(await fetch(`/api/jobs/${encodeURIComponent(jobId)}`));
}

export async function fetchAudioJobs(filters: { projectId?: string; kind?: AudioJobKind; limit?: number } = {}): Promise<AudioJob[]> {
  const parameters = new URLSearchParams();
  if (filters.projectId) parameters.set("project_id", filters.projectId);
  if (filters.kind) parameters.set("kind", filters.kind);
  parameters.set("limit", String(filters.limit ?? 500));
  return responseJson<AudioJob[]>(await fetch(`/api/jobs?${parameters.toString()}`));
}

export async function fetchFastRouteWorkspace(projectId: string): Promise<FastRouteWorkspace> {
  return responseJson<FastRouteWorkspace>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/fast/workspace`),
  );
}

export async function updateFastVoice(projectId: string, characterId: string, voiceId: string): Promise<FastRouteWorkspace> {
  return responseJson<FastRouteWorkspace>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/fast/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ character_id: characterId, voice_id: voiceId }),
    }),
  );
}

export async function cancelFastQueue(projectId: string): Promise<CancelledQualityJobs> {
  return responseJson<CancelledQualityJobs>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/fast/cancel`, { method: "POST" }),
  );
}

export async function deleteFastCache(projectId: string, jobIds: string[]): Promise<DeletedQualityCache> {
  return responseJson<DeletedQualityCache>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/fast/cache/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: jobIds }),
    }),
  );
}

export async function mergeFastAudio(projectId: string, jobIds: string[]): Promise<MergedAudio> {
  return responseJson<MergedAudio>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/fast/merge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: jobIds }),
    }),
  );
}

export async function cancelQualityQueue(projectId: string): Promise<CancelledQualityJobs> {
  return responseJson<CancelledQualityJobs>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/quality/cancel`, { method: "POST" }),
  );
}

export async function deleteQualityCache(projectId: string, jobIds: string[]): Promise<DeletedQualityCache> {
  return responseJson<DeletedQualityCache>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/quality/cache/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: jobIds }),
    }),
  );
}

export async function reprocessQualityLoudness(projectId: string, jobIds: string[]): Promise<AudioJob[]> {
  return responseJson<AudioJob[]>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/quality/loudness/reprocess`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: jobIds }),
    }),
  );
}

export async function reprocessQualityRvc(projectId: string, jobIds: string[]): Promise<AudioJob[]> {
  return responseJson<AudioJob[]>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/quality/rvc/reprocess`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: jobIds }),
    }),
  );
}

export async function uploadReferenceAudio(
  projectId: string,
  referenceId: string,
  file: File,
  source: "uploaded" | "recorded",
): Promise<PreparationPreview> {
  const body = new FormData();
  body.append("source", source);
  body.append("file", file);
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}/audio`, {
      method: "POST",
      body,
    }),
  );
}

export async function activateReferenceAudioVersion(
  projectId: string,
  referenceId: string,
  versionId: string,
): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}/audio/${encodeURIComponent(versionId)}`, {
      method: "PATCH",
    }),
  );
}

export async function reviewReferenceAudioVersion(
  projectId: string,
  referenceId: string,
  versionId: string,
  decision: "accepted" | "rejected",
): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}/audio/${encodeURIComponent(versionId)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    }),
  );
}

export async function deleteReferenceAudioVersion(
  projectId: string,
  referenceId: string,
  versionId: string,
): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(referenceId)}/audio/${encodeURIComponent(versionId)}`, {
      method: "DELETE",
    }),
  );
}

export async function mergeQualityAudio(projectId: string, jobIds: string[]): Promise<MergedAudio> {
  return responseJson<MergedAudio>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/quality/merge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: jobIds }),
    }),
  );
}

export async function fetchSystemResources(): Promise<SystemResources> {
  return responseJson<SystemResources>(await fetch("/api/system/resources"));
}
