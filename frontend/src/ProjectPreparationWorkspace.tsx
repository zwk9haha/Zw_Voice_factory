import { Activity, ArrowDownToLine, ArrowRight, ArrowUpFromLine, BookOpenText, BrainCircuit, Check, ChevronDown, ChevronRight, CircleAlert, CircleStop, Clock3, Database, Download, FileText, Folder, FolderPlus, GitBranch, Layers3, LoaderCircle, Lock, Mic2, Pause, Play, Plus, RefreshCw, RotateCcw, Save, SlidersHorizontal, Sparkles, Trash2, Unlock, Upload, Users, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AudioPlayer } from "./AudioPlayer";
import { activateProjectRevision, activateReferenceAudioVersion, activateReferenceTextVersion, cancelPreparationAction, clearReferenceAudioCache, commandContinuousProduction, createAudioJob, createEmotionVariant, deleteDirectorCache, deleteEmotionVariant, deleteProjectRevision, deleteReferenceAudioVersion, deleteReferenceTextVersion, fetchAnalysisActivity, fetchAudioJob, fetchContinuousProduction, fetchPreparationPreview, fetchProjectRevisions, fetchSources, fetchVoiceAnalysisStatus, fetchVoiceResourceMatches, generateReferenceText, importTxtSource, regenerateVoiceProfiles, reuseVoiceResource, reviewReferenceAudioVersion, runPreparationAction, startContinuousProduction, updateAutomaticReferenceLock, updateEmotionSelection, updateEmotionSettings, updateLongFormAnalysisSettings, updateReferencePromptLock, updateReferenceSelection, updateReferenceText, updateReferenceThreshold, updateReferenceVoicePrompt, uploadReferenceAudio } from "./api";
import { ReferenceAudioPanel } from "./ReferenceAudioPanel";
import { ReferenceTextPanel } from "./ReferenceTextPanel";
import { VoiceReusePanel } from "./VoiceReusePanel";
import type {
  AnalysisActivity,
  AudioJob,
  CloudAnalysisEvent,
  ContinuousProductionRun,
  ContinuousProductionSettings,
  EmotionPlanItem,
  LongFormAnalysisSettings,
  LongFormMode,
  PreparationAction,
  PreparationPreview,
  ProjectRevision,
  ProjectRevisionWorkspace,
  ProductionStageId,
  RouteMode,
  ReferencePlanItem,
  SourceSummary,
  VoiceAnalysisStatus,
  VoiceResourceMatch,
} from "./types";
import { Waveform } from "./Waveform";

type ProjectPreparationStage = "source" | "casting" | "references" | "emotions" | "director";
const REFERENCE_COLORS = ["teal", "violet", "gold"] as const;

interface ProjectPreparationWorkspaceProps {
  activeStage: ProjectPreparationStage;
  routeMode: RouteMode;
  sources: SourceSummary[];
  selectedProjectId: string | null;
  selectedSliceId: string | null;
  onProjectChange: (projectId: string | null) => void;
  onSourcesChange: (sources: SourceSummary[]) => void;
  onStageChange: (stage: ProductionStageId) => void;
}

const statusLabel: Record<SourceSummary["status"], string> = {
  imported: "待分析",
  analyzed: "已分析",
  characters_ready: "角色已提取",
  director_ready: "导演文件就绪",
};

const actionLabel: Record<PreparationAction, string> = {
  analyze: "分析文档",
  extract_characters: "提取角色",
  generate_director: "生成导演文件",
};

const genderLabel: Record<ReferencePlanItem["gender"], string> = {
  male: "男声",
  female: "女声",
  unknown: "性别待确认",
};

const referenceStatusLabel: Record<ReferencePlanItem["status"], string> = {
  not_generated: "待生成",
  queued: "排队中",
  running: "生成中",
  generated: "可试听",
  failed: "生成失败",
};

const revisionStatusLabel: Record<ProjectRevision["status"], string> = {
  running: "运行中",
  analyzed: "分析缓存",
  characters_ready: "角色缓存",
  director_ready: "导演缓存",
  failed: "可继续",
};

const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
const continuousQualityNavigationKey = (runId: string) => `zw-continuous-quality-opened:${runId}`;

function hasOpenedContinuousQualityRun(runId: string): boolean {
  try {
    return window.sessionStorage.getItem(continuousQualityNavigationKey(runId)) === "1";
  } catch {
    return false;
  }
}

function markContinuousQualityRunOpened(runId: string): void {
  try {
    window.sessionStorage.setItem(continuousQualityNavigationKey(runId), "1");
  } catch {
    // The navigation still works when browser storage is unavailable.
  }
}

function isContinuousRunRenderReady(run: ContinuousProductionRun): boolean {
  return ["render_ready", "complete"].includes(run.state)
    && run.slices.some((slice) => ["render_ready", "rendering", "playing", "complete"].includes(slice.state));
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainingSeconds = total % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请检查后端服务";
}

const analysisOperationLabel: Record<string, string> = {
  text_structure: "章节结构",
  candidate_screening: "角色候选粗筛",
  character_profile: "角色画像",
  director_analysis: "导演裁决",
  reference_text: "参考文本",
};

const longFormModeLabel: Record<LongFormMode, string> = {
  auto: "自动识别",
  chapters: "按章节",
  characters: "按字数",
};

const longFormStrategyLabel = {
  short: "短篇全文",
  standard_chapters: "标准章节",
  inferred_chapters: "模型章节",
  characters: "整句字数",
} as const;

const longFormBatchStateLabel = {
  pending: "待处理",
  analyzed: "已扫描",
  characters_ready: "角色就绪",
  director_running: "导演处理中",
  ready: "可使用",
  failed: "失败",
} as const;

const emotionPolicyLabel: Record<ContinuousProductionSettings["emotion_policy"], string> = {
  skip: "跳过",
  background: "后台生成",
  required_before_render: "先生成后渲染",
};

const rvcPreparationStatusLabel: Record<ContinuousProductionRun["rvc_tasks"][number]["status"], string> = {
  waiting_reference: "等待参考确认",
  reused: "已复用",
  queued: "等待空闲",
  building_material: "构建素材",
  training: "训练中",
  benchmarking: "基准测试",
  awaiting_review: "等待审核",
  approved: "已启用",
  deferred: "已延期",
  skipped: "已跳过",
  rejected: "已拒绝",
  failed: "失败回退",
};

const continuousStateLabel: Record<ContinuousProductionRun["state"], string> = {
  starting: "正在启动",
  running: "准备中",
  pausing: "正在暂停",
  paused: "已暂停",
  render_ready: "可渲染",
  complete: "已完成",
  failed: "需要处理",
  cancelled: "已取消",
};

const continuousStageLabel: Record<ContinuousProductionRun["current_stage"], string> = {
  analysis: "文本分析",
  casting: "角色审核",
  references: "标准参考",
  emotions: "情绪派生",
  director: "导演脚本",
  quality_render: "质量渲染",
};

const productionSliceStateLabel: Record<ContinuousProductionRun["slices"][number]["state"], string> = {
  pending: "等待",
  analyzing: "分析",
  casting: "角色",
  references: "参考",
  emotions: "情绪",
  directing: "导演",
  render_ready: "可渲染",
  rendering: "渲染",
  playing: "播放",
  complete: "完成",
  blocked: "阻塞",
  failed: "失败",
  skipped: "已跳过",
};

function AnalysisEventBox({ title, events, direction }: { title: string; events: CloudAnalysisEvent[]; direction: "input" | "output" }) {
  const latest = events.at(-1) ?? null;
  const Icon = direction === "input" ? ArrowUpFromLine : ArrowDownToLine;
  return (
    <section className={`analysis-event-box analysis-event-box--${direction}`}>
      <header><Icon size={13} /><strong>{title}</strong><span>{events.length ? `${events.length} 条` : "等待数据"}</span></header>
      {latest ? (
        <>
          <div className="analysis-event-meta"><b>{analysisOperationLabel[latest.operation] ?? latest.operation}</b><span>{latest.call_id} · {latest.model}</span></div>
          <pre>{latest.preview}</pre>
          <footer><span>{latest.total_chars.toLocaleString()} 字符</span><span>{latest.elapsed_seconds === null ? `第 ${latest.attempt} 次` : `${latest.elapsed_seconds.toFixed(2)} 秒`}</span></footer>
        </>
      ) : <p>当前项目尚无云端 API {title}。</p>}
    </section>
  );
}

function PreviewPanel({ preview }: { preview: PreparationPreview }) {
  const accepted = preview.analysis_audit?.candidates.filter((candidate) => candidate.decision === "accepted") ?? [];
  const rejected = preview.analysis_audit?.candidates.filter((candidate) => candidate.decision === "rejected") ?? [];
  const segments = preview.director_doc?.segments.slice(0, 6) ?? [];
  const characterNames = new Map(preview.character_voice_bible?.characters.map((character) => [character.character_id, character.display_name]));

  return (
    <section className="preparation-preview" aria-label="准备流程预览">
      <div className="preview-heading"><span>产物预览</span><strong>{statusLabel[preview.status]}</strong></div>
      {preview.analysis_audit ? (
        <div className="preview-summary">
          <span>规则预览</span>
          <strong>{preview.analysis_audit.structure.chapter_count} 章</strong>
          <strong>{preview.analysis_audit.structure.estimated_segment_count} 句</strong>
          <strong>{preview.analysis_audit.structure.character_count.toLocaleString()} 字符</strong>
        </div>
      ) : <p className="empty-state">尚未生成分析审计。</p>}
      {!!preview.analysis_audit && (
        <div className="preview-section">
          <span>角色候选</span>
          <p>已接纳 {accepted.length} · 已排除 {rejected.length}</p>
          {preview.analysis_audit.candidate_screening_completed_at && (
            <p>
              本地粗筛 {preview.analysis_audit.candidate_screening_input_count} 个 ·
              保留 {preview.analysis_audit.candidate_screening_kept_count} ·
              合并 {preview.analysis_audit.candidate_screening_merged_count} ·
              排除 {preview.analysis_audit.candidate_screening_rejected_count}
            </p>
          )}
          <div className="preview-tags">
            {accepted.slice(0, 8).map((candidate) => <em key={candidate.candidate_id}>{candidate.display_name}</em>)}
            {rejected.slice(0, 4).map((candidate) => <em className="rejected" key={candidate.candidate_id}>{candidate.display_name}</em>)}
          </div>
        </div>
      )}
      {!!segments.length && (
        <div className="preview-section preview-lines">
          <span>导演文件前 {segments.length} 句</span>
          {segments.map((segment) => (
            <p key={segment.segment_id}><b>{characterNames.get(segment.character_id) ?? segment.character_id}</b>{segment.text}</p>
          ))}
        </div>
      )}
    </section>
  );
}

export function ProjectPreparationWorkspace({ activeStage, routeMode, sources, selectedProjectId, selectedSliceId, onProjectChange, onSourcesChange, onStageChange }: ProjectPreparationWorkspaceProps) {
  const [preview, setPreview] = useState<PreparationPreview | null>(null);
  const [voiceAnalysis, setVoiceAnalysis] = useState<VoiceAnalysisStatus | null>(null);
  const [analysisActivity, setAnalysisActivity] = useState<AnalysisActivity | null>(null);
  const [analysisSettingsDraft, setAnalysisSettingsDraft] = useState<LongFormAnalysisSettings | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [cancellingPreparation, setCancellingPreparation] = useState(false);
  const [referenceJobs, setReferenceJobs] = useState<Record<string, AudioJob>>({});
  const [emotionJobs, setEmotionJobs] = useState<Record<string, AudioJob>>({});
  const [thresholdPercent, setThresholdPercent] = useState(10);
  const [emotionThresholdPercent, setEmotionThresholdPercent] = useState(10);
  const [selectedEmotionParentId, setSelectedEmotionParentId] = useState<string | null>(null);
  const [showCustomEmotion, setShowCustomEmotion] = useState(false);
  const [customEmotionName, setCustomEmotionName] = useState("");
  const [customEmotionDescription, setCustomEmotionDescription] = useState("");
  const [customEmotionIntensity, setCustomEmotionIntensity] = useState(65);
  const [voicePromptDraft, setVoicePromptDraft] = useState("");
  const [customVoiceAttributesDraft, setCustomVoiceAttributesDraft] = useState("");
  const [referenceAudioExpanded, setReferenceAudioExpanded] = useState(false);
  const [showProjectCreator, setShowProjectCreator] = useState(false);
  const [projectNameDraft, setProjectNameDraft] = useState("");
  const [pendingSourceFile, setPendingSourceFile] = useState<File | null>(null);
  const [voiceMatches, setVoiceMatches] = useState<VoiceResourceMatch[]>([]);
  const [voiceMatchesLoading, setVoiceMatchesLoading] = useState(false);
  const [revisionWorkspace, setRevisionWorkspace] = useState<ProjectRevisionWorkspace | null>(null);
  const [selectedRevisionId, setSelectedRevisionId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("选择 TXT 后开始准备流程");
  const [showPreview, setShowPreview] = useState(false);
  const [continuousRun, setContinuousRun] = useState<ContinuousProductionRun | null>(null);
  const [continuousSettings, setContinuousSettings] = useState<ContinuousProductionSettings>({ emotion_policy: "background", rvc_stability_policy: "skip", prefetch_slices: 1, auto_play: false });
  const [continuousBusy, setContinuousBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const selectedSource = sources.find((source) => source.project_id === selectedProjectId) ?? null;
  const selectedRevision = revisionWorkspace?.revisions.find((revision) => revision.revision_id === selectedRevisionId) ?? null;
  const allCandidates = preview?.analysis_audit?.candidates ?? [];
  const allSegments = preview?.director_doc?.segments ?? [];
  const selectedBatch = preview?.analysis_audit?.long_form_plan?.batches.find((batch) => batch.batch_id === selectedSliceId) ?? null;
  const candidates = selectedBatch && allCandidates.some((candidate) => candidate.batch_ids.length)
    ? allCandidates.filter((candidate) => candidate.batch_ids.includes(selectedBatch.batch_id))
    : allCandidates;
  const segments = selectedBatch
    ? allSegments.filter((segment) => segment.analysis_batch_id === selectedBatch.batch_id)
    : allSegments;
  const sliceCharacterIds = new Set(segments.map((segment) => segment.character_id));
  const sliceCandidateNames = new Set(candidates.map((candidate) => candidate.display_name));
  for (const character of preview?.character_voice_bible?.characters ?? []) {
    if ([character.display_name, ...character.aliases].some((name) => sliceCandidateNames.has(name))) {
      sliceCharacterIds.add(character.character_id);
    }
  }
  const allReferenceItems = preview?.reference_plan?.items ?? [];
  const referenceItems = selectedBatch
    ? allReferenceItems.filter((item) => item.source_character_id === "narrator" || sliceCharacterIds.has(item.source_character_id))
    : allReferenceItems;
  const selectedReferences = referenceItems.filter((item) => item.selected);
  const emotionItems = preview?.emotion_plan?.items ?? [];
  const selectedEmotionParent = selectedReferences.find((item) => item.reference_id === selectedEmotionParentId) ?? selectedReferences[0] ?? null;
  const visibleEmotionItems = emotionItems.filter((item) => item.parent_reference_id === selectedEmotionParent?.reference_id);
  const selectedEmotion = visibleEmotionItems[selectedIndex] ?? null;
  const selectedSegment = segments[selectedIndex] ?? null;
  const selectedReference = activeStage === "emotions" ? selectedEmotionParent : (activeStage === "references" ? selectedReferences : referenceItems)[selectedIndex] ?? null;
  const continuousCurrentSlice = continuousRun?.slices.find((slice) => slice.slice_id === selectedSliceId)
    ?? continuousRun?.slices.find((slice) => slice.slice_id === continuousRun.current_slice_id)
    ?? null;
  const continuousCompletedCount = continuousRun?.slices.filter((slice) => slice.state === "complete").length ?? 0;
  const continuousReadyCount = continuousRun?.slices.filter((slice) => ["render_ready", "rendering", "playing"].includes(slice.state)).length ?? 0;
  const continuousFailedCount = continuousRun?.slices.filter((slice) => ["failed", "blocked"].includes(slice.state)).length ?? 0;
  const continuousReusedCount = continuousRun?.events.filter((event) => event.kind === "artifact_reused").length ?? 0;
  const continuousRvcReviewCount = continuousRun?.rvc_tasks.filter((task) => task.status === "awaiting_review").length ?? 0;

  useEffect(() => {
    setFeedback(sources.length ? `已载入 ${sources.length} 个 TXT` : "尚无 TXT，请先导入小说");
  }, [sources.length]);

  useEffect(() => {
    let active = true;
    fetchVoiceAnalysisStatus().then((status) => {
      if (active) setVoiceAnalysis(status);
    }).catch(() => {
      if (active) setVoiceAnalysis(null);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!selectedProjectId) {
      setPreview(null);
      setRevisionWorkspace(null);
      setSelectedRevisionId(null);
      setContinuousRun(null);
      return;
    }
    setRevisionWorkspace(null);
    setSelectedRevisionId(null);
    let active = true;
    fetchPreparationPreview(selectedProjectId).then((next) => {
      if (active) setPreview(next);
    }).catch((error: unknown) => {
      if (active) setFeedback(errorMessage(error));
    });
    fetchProjectRevisions(selectedProjectId).then((workspace) => {
      if (!active) return;
      setRevisionWorkspace(workspace);
      setSelectedRevisionId(workspace.active_revision_id);
    }).catch(() => {
      if (active) setRevisionWorkspace(null);
    });
    fetchContinuousProduction(selectedProjectId).then((run) => {
      if (!active) return;
      setContinuousRun(run);
      setContinuousSettings(run.settings);
    }).catch(() => {
      if (active) setContinuousRun(null);
    });
    return () => { active = false; };
  }, [selectedProjectId]);

  useEffect(() => {
    if (activeStage !== "source" || !selectedProjectId || !continuousRun || ["complete", "cancelled"].includes(continuousRun.state)) return;
    let active = true;
    let openingQualityRun = false;
    const poll = async () => {
      if (openingQualityRun) return;
      try {
        const run = await fetchContinuousProduction(selectedProjectId);
        if (!active) return;
        setContinuousRun(run);
        setContinuousSettings(run.settings);
        if (isContinuousRunRenderReady(run) && !hasOpenedContinuousQualityRun(run.run_id)) {
          openingQualityRun = true;
          const nextPreview = await fetchPreparationPreview(selectedProjectId);
          if (!active || hasOpenedContinuousQualityRun(run.run_id)) return;
          markContinuousQualityRunOpened(run.run_id);
          setPreview(nextPreview);
          onSourcesChange(sources.map((source) => source.project_id === nextPreview.project_id ? nextPreview.source : source));
          onStageChange("quality_render");
        }
      } catch (error) {
        if (active) setFeedback(errorMessage(error));
      } finally {
        openingQualityRun = false;
      }
    };
    const timer = window.setInterval(() => { void poll(); }, 800);
    return () => { active = false; window.clearInterval(timer); };
  }, [activeStage, continuousRun?.run_id, continuousRun?.state, onStageChange, selectedProjectId]);

  useEffect(() => {
    if (activeStage !== "source" || !selectedProjectId) {
      setAnalysisActivity(null);
      return;
    }
    let active = true;
    const poll = async () => {
      try {
        const activity = await fetchAnalysisActivity(selectedProjectId);
        if (active) setAnalysisActivity(activity);
      } catch {
        if (active) setAnalysisActivity(null);
      }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 800);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [activeStage, selectedProjectId]);

  useEffect(() => {
    setSelectedIndex(0);
    setShowPreview(false);
  }, [activeStage, selectedProjectId, selectedSliceId]);

  useEffect(() => {
    if (preview?.analysis_settings) setAnalysisSettingsDraft(preview.analysis_settings);
  }, [preview?.analysis_settings]);

  useEffect(() => {
    if (preview?.reference_plan) {
      setThresholdPercent(Math.round(preview.reference_plan.automatic_threshold * 100));
    }
  }, [preview?.reference_plan?.automatic_threshold]);

  useEffect(() => {
    if (preview?.emotion_plan) {
      setEmotionThresholdPercent(Math.round(preview.emotion_plan.automatic_threshold * 100));
    }
  }, [preview?.emotion_plan?.automatic_threshold]);

  useEffect(() => {
    setSelectedEmotionParentId((current) => current && selectedReferences.some((item) => item.reference_id === current) ? current : selectedReferences[0]?.reference_id ?? null);
  }, [selectedReferences.map((item) => item.reference_id).join(",")]);

  useEffect(() => {
    setVoicePromptDraft(selectedReference?.voice_prompt ?? "");
    setCustomVoiceAttributesDraft(selectedReference?.custom_voice_attributes ?? "");
  }, [selectedReference?.reference_id, selectedReference?.voice_prompt, selectedReference?.custom_voice_attributes]);

  useEffect(() => {
    if (!selectedProjectId || !selectedReference || (activeStage !== "casting" && activeStage !== "references")) {
      setVoiceMatches([]);
      setVoiceMatchesLoading(false);
      return;
    }
    let active = true;
    setVoiceMatchesLoading(true);
    fetchVoiceResourceMatches(selectedProjectId, selectedReference.reference_id).then((matches) => {
      if (active) setVoiceMatches(matches);
    }).catch(() => {
      if (active) setVoiceMatches([]);
    }).finally(() => {
      if (active) setVoiceMatchesLoading(false);
    });
    return () => { active = false; };
  }, [activeStage, selectedProjectId, selectedReference?.reference_id, selectedReference?.voice_prompt]);

  async function refresh(): Promise<void> {
    setBusy("refresh");
    try {
      const items = await fetchSources();
      setVoiceAnalysis(await fetchVoiceAnalysisStatus());
      onSourcesChange(items);
      const projectId = selectedProjectId && items.some((item) => item.project_id === selectedProjectId)
        ? selectedProjectId
        : items[0]?.project_id ?? null;
      onProjectChange(projectId);
      if (projectId) {
        setPreview(await fetchPreparationPreview(projectId));
        const revisions = await fetchProjectRevisions(projectId);
        setRevisionWorkspace(revisions);
        setSelectedRevisionId(revisions.active_revision_id);
      }
      setFeedback("源文件与产物状态已刷新");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function cancelPreparation(): Promise<void> {
    if (!selectedProjectId || cancellingPreparation) return;
    setCancellingPreparation(true);
    setFeedback("正在请求终止；当前模型调用完成后停止");
    try {
      const activity = await cancelPreparationAction(selectedProjectId);
      setAnalysisActivity(activity);
      setFeedback("终止请求已提交，已完成的检查点会保留");
    } catch (error) {
      setCancellingPreparation(false);
      setFeedback(errorMessage(error));
    }
  }

  async function upload(file: File, projectName: string): Promise<void> {
    setBusy("upload");
    try {
      const source = await importTxtSource(file, projectName);
      const items = await fetchSources();
      onSourcesChange(items);
      onProjectChange(source.project_id);
      setPreview(await fetchPreparationPreview(source.project_id));
      const revisions = await fetchProjectRevisions(source.project_id);
      setRevisionWorkspace(revisions);
      setSelectedRevisionId(revisions.active_revision_id);
      setProjectNameDraft("");
      setPendingSourceFile(null);
      setShowProjectCreator(false);
      setFeedback(`项目“${source.display_name}”已创建，${source.file_name} 使用 ${source.encoding.toUpperCase()}`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function createProject(): Promise<void> {
    const projectName = projectNameDraft.trim();
    if (!projectName) {
      setFeedback("请先填写项目名称");
      return;
    }
    if (!pendingSourceFile) {
      setFeedback("请选择要导入的 TXT 小说");
      return;
    }
    await upload(pendingSourceFile, projectName);
  }

  async function runAction(action: PreparationAction, resume = false): Promise<void> {
    if (!selectedProjectId) {
      setFeedback("请先选择或导入 TXT");
      return;
    }
    setBusy(action);
    setFeedback(`${actionLabel[action]}进行中`);
    try {
      const next = await runPreparationAction(
        selectedProjectId,
        action,
        action === "analyze" && !resume ? null : selectedRevisionId,
        resume,
      );
      setPreview(next);
      const revisions = await fetchProjectRevisions(selectedProjectId);
      setRevisionWorkspace(revisions);
      setSelectedRevisionId(revisions.active_revision_id);
      onSourcesChange(sources.map((source) => source.project_id === next.project_id ? next.source : source));
      setFeedback(resume ? `${actionLabel[action]}已从失败处继续并完成` : `${actionLabel[action]}完成，产物已写入当前分析分支`);
      if (action === "generate_director") setShowPreview(true);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
      setCancellingPreparation(false);
    }
  }

  async function startOneClickProduction(): Promise<void> {
    if (!selectedProjectId) return;
    setContinuousBusy(true);
    setFeedback("正在启动一键质量生产");
    try {
      const run = await startContinuousProduction(selectedProjectId, continuousSettings);
      setContinuousRun(run);
      setContinuousSettings(run.settings);
      setFeedback(run.message);
      if (isContinuousRunRenderReady(run)) {
        const nextPreview = await fetchPreparationPreview(selectedProjectId);
        markContinuousQualityRunOpened(run.run_id);
        setPreview(nextPreview);
        onSourcesChange(sources.map((source) => source.project_id === nextPreview.project_id ? nextPreview.source : source));
        onStageChange("quality_render");
      }
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setContinuousBusy(false);
    }
  }

  async function runContinuousCommand(command: "pause" | "resume" | "retry" | "skip" | "cancel"): Promise<void> {
    if (!selectedProjectId) return;
    setContinuousBusy(true);
    try {
      const run = await commandContinuousProduction(selectedProjectId, command);
      setContinuousRun(run);
      setContinuousSettings(run.settings);
      setFeedback(run.message);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setContinuousBusy(false);
    }
  }

  async function selectRevision(revisionId: string): Promise<void> {
    if (!selectedProjectId || revisionId === selectedRevisionId) return;
    setBusy(`revision:${revisionId}`);
    try {
      setPreview(await activateProjectRevision(selectedProjectId, revisionId));
      const revisions = await fetchProjectRevisions(selectedProjectId);
      setRevisionWorkspace(revisions);
      setSelectedRevisionId(revisionId);
      setSelectedIndex(0);
      setFeedback("已切换分析分支，角色、参考与导演缓存均已恢复");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function removeRevision(revisionId: string): Promise<void> {
    if (!selectedProjectId) return;
    const revision = revisionWorkspace?.revisions.find((item) => item.revision_id === revisionId);
    if (!window.confirm(`删除“${revision?.display_name ?? revisionId}”及其中的阶段缓存？项目源文件和参考音频资产不会删除。`)) return;
    setBusy(`revision_delete:${revisionId}`);
    try {
      const revisions = await deleteProjectRevision(selectedProjectId, revisionId);
      setRevisionWorkspace(revisions);
      setSelectedRevisionId(revisions.active_revision_id);
      setPreview(await fetchPreparationPreview(selectedProjectId));
      setSelectedIndex(0);
      setFeedback("分析分支缓存已删除");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function saveLongFormSettings(): Promise<void> {
    if (!selectedProjectId || !analysisSettingsDraft) return;
    setBusy("analysis_settings");
    try {
      const next = await updateLongFormAnalysisSettings(selectedProjectId, {
        mode: analysisSettingsDraft.mode,
        long_text_threshold: analysisSettingsDraft.long_text_threshold,
        chapters_per_batch: analysisSettingsDraft.chapters_per_batch,
        characters_per_batch: analysisSettingsDraft.characters_per_batch,
        parallelism: analysisSettingsDraft.parallelism,
      });
      setPreview(next);
      onSourcesChange(sources.map((source) => source.project_id === next.project_id ? next.source : source));
      setFeedback("长篇分析设置已保存；请重新分析文档生成批次计划");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function openPreview(): Promise<void> {
    if (!selectedProjectId) {
      setFeedback("请先选择或导入 TXT");
      return;
    }
    setBusy("refresh");
    try {
      setPreview(await fetchPreparationPreview(selectedProjectId));
      setShowPreview(true);
      setFeedback("预览已更新");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  function downloadDirectorDocument(): void {
    if (!preview?.director_doc) return;
    const blob = new Blob([JSON.stringify(preview.director_doc, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const projectName = (selectedSource?.display_name ?? preview.project_id).replace(/[\\/:*?"<>|]/g, "_");
    anchor.href = url;
    anchor.download = `${projectName}-director.json`;
    anchor.click();
    URL.revokeObjectURL(url);
    setFeedback("导演脚本 JSON 已导出");
  }

  async function removeDirectorCache(): Promise<void> {
    if (!selectedProjectId || !preview?.director_doc) return;
    if (!window.confirm("删除当前导演脚本缓存？角色审核、标准参考和情绪计划不会被删除。")) return;
    setBusy("delete_director");
    setFeedback("正在删除导演脚本缓存");
    try {
      const next = await deleteDirectorCache(selectedProjectId);
      setPreview(next);
      onSourcesChange(sources.map((source) => source.project_id === next.project_id ? next.source : source));
      setSelectedIndex(0);
      setShowPreview(false);
      setFeedback("导演脚本缓存已删除，可重新调用本地模型生成");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function toggleReference(item: ReferencePlanItem): Promise<void> {
    if (!selectedProjectId || item.locked) return;
    setBusy(`selection:${item.reference_id}`);
    try {
      setPreview(await updateReferenceSelection(selectedProjectId, item.reference_id, !item.selected));
      setFeedback(item.selected ? `${item.display_name} 将复用${item.gender === "female" ? "女" : "男"}旁白` : `${item.display_name} 已加入参考生成`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function saveReferenceThreshold(): Promise<void> {
    if (!selectedProjectId || !preview?.reference_plan) return;
    const nextThreshold = thresholdPercent / 100;
    if (Math.abs(nextThreshold - preview.reference_plan.automatic_threshold) < 0.0001) return;
    setBusy("reference_threshold");
    try {
      const next = await updateReferenceThreshold(selectedProjectId, nextThreshold);
      setPreview(next);
      const automaticCount = next.reference_plan?.items.filter((item) => item.selection_mode !== "optional").length ?? 0;
      setFeedback(`自动生成阈值已设为 ${thresholdPercent}%，当前自动选择 ${automaticCount} 项`);
    } catch (error) {
      setFeedback(errorMessage(error));
      setThresholdPercent(Math.round(preview.reference_plan.automatic_threshold * 100));
    } finally {
      setBusy(null);
    }
  }

  async function toggleAutomaticReferenceLock(): Promise<void> {
    if (!selectedProjectId || !preview?.reference_plan) return;
    const nextLocked = !preview.reference_plan.automatic_items_locked;
    setBusy("reference_lock");
    try {
      setPreview(await updateAutomaticReferenceLock(selectedProjectId, nextLocked));
      setFeedback(nextLocked ? "权重达标角色已锁定当前选择" : "权重达标角色已解锁，可以单独切换");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function saveVoicePrompt(): Promise<void> {
    if (!selectedProjectId || !selectedReference) return;
    const prompt = voicePromptDraft.trim();
    if (!prompt) {
      setFeedback("声线描述不能为空");
      return;
    }
    setBusy(`voice_prompt:${selectedReference.reference_id}`);
    try {
      setPreview(await updateReferenceVoicePrompt(selectedProjectId, selectedReference.reference_id, prompt));
      setFeedback(`${selectedReference.display_name} 的声线描述已保存`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function toggleNarratorPromptLock(item: ReferencePlanItem): Promise<void> {
    if (!selectedProjectId || item.selection_mode !== "narrator_default") return;
    const nextLocked = !item.voice_prompt_locked;
    setBusy(`prompt_lock:${item.reference_id}`);
    try {
      setPreview(await updateReferencePromptLock(selectedProjectId, item.reference_id, nextLocked));
      setFeedback(nextLocked ? `${item.display_name} 的描述已锁定` : `${item.display_name} 已解锁，可以重新生成描述`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function regenerateSelectedVoiceProfile(): Promise<void> {
    if (!selectedProjectId || !selectedReference) return;
    if (selectedReference.selection_mode === "narrator_default" && selectedReference.voice_prompt_locked) {
      setFeedback(`请先解锁 ${selectedReference.display_name} 的声线描述`);
      return;
    }
    setBusy(`voice_profile:${selectedReference.source_character_id}`);
    setFeedback(`正在重新分析 ${selectedReference.display_name} 的角色证据`);
    try {
      const target = selectedReference.selection_mode === "narrator_default"
        ? { reference_id: selectedReference.reference_id, custom_attributes: customVoiceAttributesDraft }
        : { character_id: selectedReference.source_character_id, custom_attributes: customVoiceAttributesDraft };
      setPreview(await regenerateVoiceProfiles(selectedProjectId, target));
      setFeedback(`${selectedReference.display_name} 的声线描述已重新生成`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function regenerateAllVoiceProfiles(): Promise<void> {
    if (!selectedProjectId) return;
    const characterCount = referenceItems.filter((item) => item.selection_mode !== "narrator_default" || !item.voice_prompt_locked).length;
    if (!characterCount) {
      setFeedback("当前项目没有可重新分析的角色");
      return;
    }
    setBusy("voice_profiles:all");
    setFeedback(`正在重新分析全部 ${characterCount} 个角色的证据`);
    try {
      setPreview(await regenerateVoiceProfiles(selectedProjectId));
      setFeedback(`全部 ${characterCount} 个角色的声线描述已重新生成`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function saveReferenceAudio(referenceId: string, file: File, source: "uploaded" | "recorded"): Promise<void> {
    if (!selectedProjectId) return;
    setBusy(`reference_audio:${referenceId}`);
    setFeedback(source === "recorded" ? "正在保存用户录音" : "正在上传参考音频");
    try {
      setPreview(await uploadReferenceAudio(selectedProjectId, referenceId, file, source));
      setFeedback(source === "recorded" ? "录音已追加为新的参考版本" : "音频已追加为新的参考版本");
    } catch (error) {
      setFeedback(errorMessage(error));
      throw error;
    } finally {
      setBusy(null);
    }
  }

  async function activateReferenceVersion(referenceId: string, versionId: string): Promise<void> {
    if (!selectedProjectId) return;
    setBusy(`reference_audio:${referenceId}`);
    try {
      setPreview(await activateReferenceAudioVersion(selectedProjectId, referenceId, versionId));
      setFeedback("已切换标准参考音频");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function reviewReferenceVersion(referenceId: string, versionId: string, decision: "accepted" | "rejected"): Promise<void> {
    if (!selectedProjectId) return;
    setBusy(`reference_audio:${referenceId}`);
    try {
      setPreview(await reviewReferenceAudioVersion(selectedProjectId, referenceId, versionId, decision));
      setFeedback(decision === "accepted" ? "当前版本已接受为标准参考" : "当前参考候选已拒绝");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function deleteReferenceVersion(referenceId: string, versionId: string): Promise<void> {
    if (!selectedProjectId) return;
    setBusy(`reference_audio:${referenceId}`);
    try {
      setPreview(await deleteReferenceAudioVersion(selectedProjectId, referenceId, versionId));
      setFeedback("当前参考音频缓存已删除");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function clearReferenceCache(referenceId: string): Promise<void> {
    if (!selectedProjectId) return;
    if (!window.confirm("清空该角色的全部参考音频缓存？历史版本会从磁盘删除。")) return;
    setBusy(`reference_audio:${referenceId}`);
    try {
      setPreview(await clearReferenceAudioCache(selectedProjectId, referenceId));
      setFeedback("该角色的参考音频缓存已清空");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function refreshVoiceMatches(): Promise<void> {
    if (!selectedProjectId || !selectedReference) return;
    setVoiceMatchesLoading(true);
    try {
      setVoiceMatches(await fetchVoiceResourceMatches(selectedProjectId, selectedReference.reference_id));
    } catch (error) {
      setVoiceMatches([]);
      setFeedback(errorMessage(error));
    } finally {
      setVoiceMatchesLoading(false);
    }
  }

  async function reuseHistoricalVoice(match: VoiceResourceMatch): Promise<void> {
    if (!selectedProjectId || !selectedReference) return;
    setBusy(`voice_reuse:${selectedReference.reference_id}`);
    setFeedback(`正在复用 ${match.source_project_name} · ${match.display_name} 的参考音频`);
    try {
      setPreview(await reuseVoiceResource(selectedProjectId, selectedReference.reference_id, match));
      setFeedback("历史参考已复制到当前项目，并追加为新的音频版本");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function generateReferenceTextVersion(referenceId: string): Promise<void> {
    if (!selectedProjectId) return;
    setBusy(`reference_text_generate:${referenceId}`);
    setFeedback("本地模型正在设计标准参考句式");
    try {
      setPreview(await generateReferenceText(selectedProjectId, referenceId));
      setFeedback("标准参考文本已生成并追加为新版本");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function saveReferenceTextVersion(referenceId: string, text: string): Promise<void> {
    if (!selectedProjectId) return;
    setBusy(`reference_text:${referenceId}`);
    try {
      setPreview(await updateReferenceText(selectedProjectId, referenceId, text));
      setFeedback("编辑内容已保存为新的标准参考文本版本");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function activateReferenceText(referenceId: string, versionId: string): Promise<void> {
    if (!selectedProjectId) return;
    setBusy(`reference_text:${referenceId}`);
    try {
      setPreview(await activateReferenceTextVersion(selectedProjectId, referenceId, versionId));
      setFeedback("已切换标准参考文本版本");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function deleteReferenceText(referenceId: string, versionId: string): Promise<void> {
    if (!selectedProjectId) return;
    setBusy(`reference_text:${referenceId}`);
    try {
      setPreview(await deleteReferenceTextVersion(selectedProjectId, referenceId, versionId));
      setFeedback("当前标准参考文本记录已删除");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function pollReferenceJob(referenceId: string, jobId: string): Promise<AudioJob> {
    for (let attempt = 0; attempt < 900; attempt += 1) {
      const job = await fetchAudioJob(jobId);
      setReferenceJobs((current) => ({ ...current, [referenceId]: job }));
      if (job.status === "complete" || job.status === "failed") return job;
      await wait(800);
    }
    throw new Error("音频生成超时，请在任务队列中检查状态");
  }

  async function submitReference(item: ReferencePlanItem): Promise<AudioJob> {
    if (!selectedProjectId) throw new Error("请先选择项目");
    const job = await createAudioJob({
      kind: "voxcpm_reference",
      project_id: selectedProjectId,
      reference_id: item.reference_id,
      character_id: item.source_character_id,
      text: item.reference_text,
      voice_prompt: item.voice_prompt,
    });
    setReferenceJobs((current) => ({ ...current, [item.reference_id]: job }));
    return pollReferenceJob(item.reference_id, job.job_id);
  }

  async function generateReference(item: ReferencePlanItem): Promise<void> {
    if (!selectedProjectId) return;
    setBusy(`reference:${item.reference_id}`);
    setFeedback(`${item.display_name} 已提交 VoxCPM2`);
    try {
      const completed = await submitReference(item);
      if (completed.status === "failed") throw new Error(completed.error ?? "音频生成失败");
      setPreview(await fetchPreparationPreview(selectedProjectId));
      setFeedback(`${item.display_name} 参考音频已生成，可以试听`);
    } catch (error) {
      setFeedback(errorMessage(error));
      setPreview(await fetchPreparationPreview(selectedProjectId).catch(() => preview));
    } finally {
      setBusy(null);
    }
  }

  async function generateSelectedReferences(): Promise<void> {
    if (!selectedProjectId) return;
    const pending = selectedReferences.filter((item) => item.status !== "generated" && item.status !== "queued" && item.status !== "running");
    if (!pending.length) {
      setFeedback("所有已选参考均已生成");
      return;
    }
    setBusy("batch_references");
    setFeedback(`正在提交 ${pending.length} 条 VoxCPM2 参考任务`);
    try {
      const completed = await Promise.all(pending.map((item) => submitReference(item)));
      const failed = completed.filter((job) => job.status === "failed");
      setPreview(await fetchPreparationPreview(selectedProjectId));
      setFeedback(failed.length ? `${completed.length - failed.length} 条完成，${failed.length} 条失败` : `${completed.length} 条参考音频已生成`);
    } catch (error) {
      setFeedback(errorMessage(error));
      setPreview(await fetchPreparationPreview(selectedProjectId).catch(() => preview));
    } finally {
      setBusy(null);
    }
  }

  async function toggleEmotionVariant(item: EmotionPlanItem): Promise<void> {
    if (!selectedProjectId || item.locked) return;
    setBusy(`emotion_selection:${item.variant_id}`);
    try {
      setPreview(await updateEmotionSelection(selectedProjectId, item.variant_id, !item.selected));
      setFeedback(item.selected ? `${item.display_name} · ${item.emotion_name} 已取消生产` : `${item.display_name} · ${item.emotion_name} 已加入生产`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function toggleEmotionSkip(): Promise<void> {
    if (!selectedProjectId || !preview?.emotion_plan) return;
    const skipped = !preview.emotion_plan.skipped;
    setBusy("emotion_skip");
    try {
      setPreview(await updateEmotionSettings(selectedProjectId, { skipped }));
      setFeedback(skipped ? "情绪声线派生已跳过，导演阶段将回退到中性参考" : "情绪声线派生已启用");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function saveEmotionThreshold(): Promise<void> {
    if (!selectedProjectId || !preview?.emotion_plan) return;
    const automaticThreshold = emotionThresholdPercent / 100;
    if (Math.abs(automaticThreshold - preview.emotion_plan.automatic_threshold) < 0.0001) return;
    setBusy("emotion_threshold");
    try {
      const next = await updateEmotionSettings(selectedProjectId, { automatic_threshold: automaticThreshold });
      setPreview(next);
      const automaticCount = next.emotion_plan?.items.filter((item) => item.selection_mode === "automatic").length ?? 0;
      setFeedback(`情绪自动生产阈值已设为 ${emotionThresholdPercent}%，自动选择 ${automaticCount} 项`);
    } catch (error) {
      setFeedback(errorMessage(error));
      setEmotionThresholdPercent(Math.round(preview.emotion_plan.automatic_threshold * 100));
    } finally {
      setBusy(null);
    }
  }

  async function toggleAutomaticEmotionLock(): Promise<void> {
    if (!selectedProjectId || !preview?.emotion_plan) return;
    const automaticItemsLocked = !preview.emotion_plan.automatic_items_locked;
    setBusy("emotion_lock");
    try {
      setPreview(await updateEmotionSettings(selectedProjectId, { automatic_items_locked: automaticItemsLocked }));
      setFeedback(automaticItemsLocked ? "高权重情绪项已锁定" : "高权重情绪项已解锁，可以逐项切换");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function pollEmotionJob(variantId: string, jobId: string): Promise<AudioJob> {
    for (let attempt = 0; attempt < 900; attempt += 1) {
      const job = await fetchAudioJob(jobId);
      setEmotionJobs((current) => ({ ...current, [variantId]: job }));
      if (job.status === "complete" || job.status === "failed") return job;
      await wait(800);
    }
    throw new Error("情绪音频生成超时，请在任务队列中检查状态");
  }

  async function submitEmotionVariant(item: EmotionPlanItem): Promise<AudioJob> {
    if (!selectedProjectId) throw new Error("请先选择项目");
    const job = await createAudioJob({
      kind: "emotion_variant",
      project_id: selectedProjectId,
      variant_id: item.variant_id,
      character_id: item.source_character_id,
      text: item.reference_text,
      voice_prompt: item.voice_prompt,
    });
    setEmotionJobs((current) => ({ ...current, [item.variant_id]: job }));
    return pollEmotionJob(item.variant_id, job.job_id);
  }

  async function generateEmotionVariant(item: EmotionPlanItem): Promise<void> {
    if (!selectedProjectId || item.selection_mode === "base") return;
    setBusy(`emotion:${item.variant_id}`);
    setFeedback(`${item.display_name} · ${item.emotion_name} 已提交 VoxCPM2`);
    try {
      const completed = await submitEmotionVariant(item);
      if (completed.status === "failed") throw new Error(completed.error ?? "情绪音频生成失败");
      setPreview(await fetchPreparationPreview(selectedProjectId));
      setFeedback(`${item.display_name} · ${item.emotion_name} 已生成，可以试听`);
    } catch (error) {
      setFeedback(errorMessage(error));
      setPreview(await fetchPreparationPreview(selectedProjectId).catch(() => preview));
    } finally {
      setBusy(null);
    }
  }

  async function generateSelectedEmotions(): Promise<void> {
    if (!selectedProjectId || !preview?.emotion_plan || preview.emotion_plan.skipped) return;
    const pending = emotionItems.filter((item) => item.selected && item.selection_mode !== "base" && item.status !== "generated" && item.status !== "queued" && item.status !== "running");
    if (!pending.length) {
      setFeedback("所有已选情绪声线均已生成");
      return;
    }
    setBusy("batch_emotions");
    setFeedback(`正在提交 ${pending.length} 条情绪声线任务`);
    try {
      const completed = await Promise.all(pending.map((item) => submitEmotionVariant(item)));
      const failed = completed.filter((job) => job.status === "failed");
      setPreview(await fetchPreparationPreview(selectedProjectId));
      setFeedback(failed.length ? `${completed.length - failed.length} 条完成，${failed.length} 条失败` : `${completed.length} 条情绪声线已生成`);
    } catch (error) {
      setFeedback(errorMessage(error));
      setPreview(await fetchPreparationPreview(selectedProjectId).catch(() => preview));
    } finally {
      setBusy(null);
    }
  }

  async function addCustomEmotion(): Promise<void> {
    if (!selectedProjectId || !selectedEmotionParent) return;
    if (!customEmotionName.trim() || !customEmotionDescription.trim()) {
      setFeedback("请填写自定义情绪名称和声音描述");
      return;
    }
    setBusy("custom_emotion");
    try {
      setPreview(await createEmotionVariant(selectedProjectId, {
        parent_reference_id: selectedEmotionParent.reference_id,
        emotion_name: customEmotionName.trim(),
        description: customEmotionDescription.trim(),
        intensity: customEmotionIntensity / 100,
      }));
      setCustomEmotionName("");
      setCustomEmotionDescription("");
      setCustomEmotionIntensity(65);
      setShowCustomEmotion(false);
      setFeedback(`${selectedEmotionParent.display_name} 的自定义情绪已加入生产计划`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function removeCustomEmotion(item: EmotionPlanItem): Promise<void> {
    if (!selectedProjectId || item.selection_mode !== "custom") return;
    setBusy(`delete_emotion:${item.variant_id}`);
    try {
      setPreview(await deleteEmotionVariant(selectedProjectId, item.variant_id));
      setSelectedIndex(0);
      setFeedback(`${item.emotion_name} 已从情绪生产计划移除`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  const stageTitle = activeStage === "source" ? "小说导入" : activeStage === "casting" ? "角色候选审核" : activeStage === "references" ? "中性标准参考" : activeStage === "emotions" ? "情绪声线派生" : "逐句导演";
  const stageEyebrow = activeStage === "source" ? "SOURCE TEXT" : activeStage === "casting" ? "CAST AUDIT" : activeStage === "references" ? "CANONICAL REFERENCES" : activeStage === "emotions" ? "EMOTION VARIANTS" : "DIRECTOR DOCUMENT";
  const characterNames = new Map(preview?.character_voice_bible?.characters.map((character) => [character.character_id, character.display_name]));
  const selectedPreparedCharacter = preview?.character_voice_bible?.characters.find((character) => character.character_id === selectedReference?.source_character_id) ?? null;
  const selectedReferenceCandidate = candidates.find((candidate) => candidate.display_name === selectedReference?.display_name) ?? null;
  const localPreparationAction = busy === "analyze" || busy === "extract_characters" || busy === "generate_director"
    ? busy
    : null;
  const activePreparationAction = localPreparationAction
    ?? (analysisActivity?.state === "running" && analysisActivity.cancellable ? analysisActivity.action : null);
  const isBusy = busy !== null || activePreparationAction !== null;
  const longFormPlan = preview?.analysis_audit?.long_form_plan ?? null;
  const analysisSettingsChanged = !!analysisSettingsDraft && !!preview?.analysis_settings && (
    analysisSettingsDraft.mode !== preview.analysis_settings.mode
    || analysisSettingsDraft.long_text_threshold !== preview.analysis_settings.long_text_threshold
    || analysisSettingsDraft.chapters_per_batch !== preview.analysis_settings.chapters_per_batch
    || analysisSettingsDraft.characters_per_batch !== preview.analysis_settings.characters_per_batch
    || analysisSettingsDraft.parallelism !== preview.analysis_settings.parallelism
  );

  const sourceFields = selectedSource ? [
    { label: "文件大小", value: formatBytes(selectedSource.size_bytes) },
    { label: "文本编码", value: selectedSource.encoding.toUpperCase() },
    { label: "当前状态", value: statusLabel[selectedSource.status] },
    { label: "正文字符", value: preview?.analysis_audit?.structure.character_count.toLocaleString() ?? "分析后生成" },
    { label: "章节识别", value: preview?.analysis_audit ? `${preview.analysis_audit.structure.chapter_count} 章` : "分析后生成" },
    { label: "预计片段", value: preview?.analysis_audit ? `${preview.analysis_audit.structure.estimated_segment_count} 句` : "分析后生成" },
    { label: "长篇策略", value: longFormPlan ? `${longFormStrategyLabel[longFormPlan.strategy]} · ${longFormPlan.batches.length} 批` : "分析后生成" },
  ] : [];

  const fallbackReference = referenceItems.find((item) => item.reference_id === selectedReference?.reuse_reference_id) ?? null;
  const castingFields = selectedReference ? [
    { label: "角色", value: selectedReference.display_name },
    { label: "识别性别", value: genderLabel[selectedReference.gender] },
    { label: "角色权重", value: `${Math.round(selectedReference.importance * 100)}%` },
    { label: "生成规则", value: selectedReference.selection_mode === "narrator_default" ? `默认生成 · 描述${selectedReference.voice_prompt_locked ? "已锁定" : "已解锁"}` : selectedReference.selection_mode === "automatic" ? `达到 ${Math.round((preview?.reference_plan?.automatic_threshold ?? 0.75) * 100)}% 阈值，自动生成` : selectedReference.selected ? "用户已加入生成" : "低于阈值，复用旁白" },
    { label: "未生成时复用", value: fallbackReference?.display_name ?? "独立参考" },
    { label: "声线描述", value: selectedReference.voice_prompt },
  ] : [];

  const currentReferenceJob = selectedReference ? referenceJobs[selectedReference.reference_id] : null;
  const selectedReferenceColor = selectedReference?.selection_mode === "narrator_default"
    ? selectedReference.gender === "female" ? "violet" : "teal"
    : REFERENCE_COLORS[selectedIndex % REFERENCE_COLORS.length];
  const referenceFields = selectedReference ? [
    { label: "生成后端", value: "VoxCPM2" },
    { label: "当前状态", value: currentReferenceJob?.message ?? referenceStatusLabel[selectedReference.status] },
    { label: "任务进度", value: currentReferenceJob ? `${currentReferenceJob.progress}%` : selectedReference.status === "generated" ? "100%" : "尚未提交" },
    { label: "参考用途", value: selectedReference.selection_mode === "narrator_default" ? "默认旁白与低权重角色复用" : "角色中性身份锚点" },
  ] : [];

  const currentEmotionJob = selectedEmotion ? emotionJobs[selectedEmotion.variant_id] : null;
  const emotionFields = selectedEmotion ? [
    { label: "父参考", value: selectedEmotion.display_name },
    { label: "情绪类型", value: selectedEmotion.emotion_name },
    { label: "表现强度", value: `${Math.round(selectedEmotion.intensity * 100)}%` },
    { label: "生产规则", value: selectedEmotion.selection_mode === "base" ? "复用中性父参考" : selectedEmotion.selection_mode === "automatic" ? "高权重自动生产" : selectedEmotion.selection_mode === "custom" ? "用户自定义" : selectedEmotion.selected ? "用户已加入生产" : "可选未加入" },
    { label: "当前状态", value: currentEmotionJob?.message ?? referenceStatusLabel[selectedEmotion.status] },
  ] : [];

  const directorFields = selectedSegment ? [
    { label: "片段编号", value: selectedSegment.segment_id },
    { label: "稳定角色 ID", value: selectedSegment.character_id },
    { label: "角色", value: characterNames.get(selectedSegment.character_id) ?? selectedSegment.character_id },
    { label: "类型 / 情绪", value: `${selectedSegment.segment_type} / ${selectedSegment.direction.emotion}` },
    { label: "句后停顿", value: `${selectedSegment.direction.pause_after_ms} ms` },
    { label: "分析后端", value: preview?.director_doc?.analysis_backend === "local" ? preview.director_doc.analysis_model ?? "本地模型" : preview?.director_doc?.analysis_backend === "cloud" ? preview.director_doc.analysis_model ?? "云端模型" : "规则回退" },
  ] : [];

  const fields = activeStage === "source" ? sourceFields : activeStage === "casting" ? castingFields : activeStage === "references" ? referenceFields : activeStage === "emotions" ? emotionFields : directorFields;
  const canExtract = preview?.analysis_audit !== null && preview?.analysis_audit !== undefined;
  const canGenerateDirector = preview?.character_voice_bible !== null && preview?.character_voice_bible !== undefined;
  const canAdvanceSource = canGenerateDirector && preview?.reference_plan !== null;
  const generatedReferenceCount = selectedReferences.filter((item) => item.status === "generated").length;
  const allReferencesGenerated = selectedReferences.length > 0 && generatedReferenceCount === selectedReferences.length;
  const selectedEmotionItems = emotionItems.filter((item) => item.selected);
  const generatedEmotionCount = selectedEmotionItems.filter((item) => item.status === "generated").length;
  const allEmotionsReady = preview?.emotion_plan?.skipped === true || (selectedEmotionItems.length > 0 && generatedEmotionCount === selectedEmotionItems.length);

  return (
    <section className="prep-workspace project-preparation">
      <aside className={`prep-list-pane ${activeStage === "emotions" ? "emotion-list-pane" : ""}`}>
        <div className="pane-heading"><div><span className="eyebrow">{activeStage === "source" ? "WORKSPACE" : stageEyebrow}</span><h2>{activeStage === "source" ? "项目" : stageTitle}</h2></div><span className="list-count">{activeStage === "source" ? sources.length : activeStage === "casting" ? referenceItems.length : activeStage === "references" ? selectedReferences.length : activeStage === "emotions" ? visibleEmotionItems.length : segments.length}</span></div>
        {activeStage === "emotions" && (
          <label className="emotion-parent-picker"><span>角色参考</span><select value={selectedEmotionParent?.reference_id ?? ""} disabled={isBusy || !selectedReferences.length} onChange={(event) => { setSelectedEmotionParentId(event.target.value); setSelectedIndex(0); }}>{selectedReferences.map((item) => <option key={item.reference_id} value={item.reference_id}>{item.display_name} · 权重 {Math.round(item.importance * 100)}%</option>)}</select></label>
        )}
        <div className="prep-list">
          {activeStage === "source" && sources.map((source) => {
            const expanded = source.project_id === selectedProjectId;
            const referenceAssetCount = expanded
              ? referenceItems.reduce((count, item) => count + item.audio_versions.length, 0)
              : 0;
            return (
              <section key={source.project_id} className={`project-tree-project ${expanded ? "expanded" : ""}`}>
                <button className={`project-tree-root ${expanded ? "selected" : ""}`} onClick={() => onProjectChange(source.project_id)}>
                  {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <Folder size={16} />
                  <span><strong>{source.display_name}</strong><small>outputs/projects/{source.project_id}</small></span>
                  <em>{statusLabel[source.status]}</em>
                </button>
                {expanded && (
                  <div className="project-tree-children">
                    <div className="project-tree-resource" title={source.file_name}><FileText size={13} /><span><strong>源文件</strong><small>{source.file_name} · {formatBytes(source.size_bytes)}</small></span></div>
                    <div className="project-tree-group-label"><GitBranch size={12} /><span>分析分支</span><em>{revisionWorkspace?.revisions.length ?? 0}</em></div>
                    {revisionWorkspace?.revisions.map((revision) => (
                      <div key={revision.revision_id} className={`project-tree-revision ${revision.revision_id === selectedRevisionId ? "selected" : ""}`}>
                        <button title={revision.error ?? undefined} disabled={isBusy} onClick={() => void selectRevision(revision.revision_id)}>
                          <GitBranch size={13} /><span><strong>{revision.display_name}</strong><small>{new Date(revision.updated_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</small></span><em>{revisionStatusLabel[revision.status]}</em>
                        </button>
                        <button className="project-tree-delete" title="删除该分析分支缓存" disabled={isBusy} onClick={() => void removeRevision(revision.revision_id)}><Trash2 size={13} /></button>
                      </div>
                    ))}
                    {!revisionWorkspace?.revisions.length && <p className="project-tree-empty">点击“分析文档”创建第一个分支</p>}
                    <div className="project-tree-resource" title={`outputs/projects/${source.project_id}/assets`}><Database size={13} /><span><strong>项目资源</strong><small>参考音频 {referenceAssetCount} 个版本</small></span></div>
                  </div>
                )}
              </section>
            );
          })}
          {activeStage === "casting" && referenceItems.map((item, index) => (
            <div key={item.reference_id} className={`reference-cast-row ${index === selectedIndex ? "selected" : ""}`}>
              <button className="reference-character-select" onClick={() => setSelectedIndex(index)}>
                <Users size={16} /><span><strong>{item.display_name}</strong><small>{genderLabel[item.gender]} · 权重 {Math.round(item.importance * 100)}%</small></span><em>{item.selection_mode === "automatic" ? item.selected ? "自动已选" : "自动未选" : item.selection_mode === "narrator_default" ? "默认" : item.selected ? "已选择" : "复用旁白"}</em>
              </button>
              {item.selection_mode === "narrator_default" ? (
                <button className={`reference-select-toggle ${item.voice_prompt_locked ? "" : "active"}`} title={item.voice_prompt_locked ? `解锁 ${item.display_name} 的声线描述` : `锁定 ${item.display_name} 的声线描述`} disabled={isBusy} onClick={() => void toggleNarratorPromptLock(item)}>
                  {item.voice_prompt_locked ? <Unlock size={13} /> : <Lock size={13} />}
                </button>
              ) : (
                <button className={`reference-select-toggle ${item.selected ? "active" : ""}`} title={item.locked ? "权重达标角色当前已锁定" : item.selected ? "取消独立参考，改用旁白" : "加入 VoxCPM2 参考生成"} disabled={isBusy || item.locked} onClick={() => void toggleReference(item)}>
                  {item.locked ? <Lock size={13} /> : item.selected ? <Check size={14} /> : <Plus size={14} />}
                </button>
              )}
            </div>
          ))}
          {activeStage === "references" && selectedReferences.map((item, index) => (
            <button key={item.reference_id} className={index === selectedIndex ? "selected" : ""} onClick={() => setSelectedIndex(index)}>
              <Mic2 size={16} /><span><strong>{item.display_name}</strong><small>{genderLabel[item.gender]} · VoxCPM2 中性参考</small></span><em>{referenceStatusLabel[item.status]}</em>
            </button>
          ))}
          {activeStage === "emotions" && visibleEmotionItems.map((item, index) => (
            <div key={item.variant_id} className={`reference-cast-row emotion-variant-row ${index === selectedIndex ? "selected" : ""}`}>
              <button className="reference-character-select" onClick={() => setSelectedIndex(index)}>
                <Sparkles size={16} /><span><strong>{item.emotion_name}</strong><small>{item.selection_mode === "base" ? "中性父参考" : `强度 ${Math.round(item.intensity * 100)}%`}</small></span><em>{referenceStatusLabel[item.status]}</em>
              </button>
              <button className={`reference-select-toggle ${item.selected ? "active" : ""}`} title={item.locked ? "当前选择已锁定" : item.selected ? "取消生产" : "加入生产"} disabled={isBusy || item.locked || preview?.emotion_plan?.skipped} onClick={() => void toggleEmotionVariant(item)}>{item.locked ? <Lock size={13} /> : item.selected ? <Check size={14} /> : <Plus size={14} />}</button>
            </div>
          ))}
          {activeStage === "director" && segments.slice(0, 500).map((segment, index) => (
            <button key={segment.segment_id} className={index === selectedIndex ? "selected" : ""} onClick={() => setSelectedIndex(index)}>
              <SlidersHorizontal size={16} /><span><strong>{segment.segment_id} · {characterNames.get(segment.character_id) ?? segment.character_id}</strong><small>{segment.text}</small></span><em>{segment.direction.emotion}</em>
            </button>
          ))}
          {!isBusy && ((activeStage === "source" && !sources.length) || (activeStage === "casting" && !referenceItems.length) || (activeStage === "references" && !selectedReferences.length) || (activeStage === "emotions" && !visibleEmotionItems.length) || (activeStage === "director" && !segments.length)) && <p className="empty-state">当前阶段还没有可显示的数据。</p>}
          {busy === "refresh" && activeStage !== "source" && <p className="empty-state"><LoaderCircle className="spin" size={15} />正在读取产物</p>}
        </div>
        {activeStage === "source" && (
          <>
            <input ref={inputRef} className="hidden-file-input" type="file" accept=".txt,text/plain" aria-hidden="true" tabIndex={-1} onChange={(event) => setPendingSourceFile(event.target.files?.[0] ?? null)} />
            {showProjectCreator ? (
              <section className="project-create-panel" aria-label="创建小说项目">
                <div><FolderPlus size={14} /><strong>新建项目</strong><button className="icon-button" title="取消创建" disabled={isBusy} onClick={() => { setShowProjectCreator(false); setProjectNameDraft(""); setPendingSourceFile(null); if (inputRef.current) inputRef.current.value = ""; }}><X size={13} /></button></div>
                <label><span>项目名称</span><input autoFocus maxLength={120} value={projectNameDraft} disabled={isBusy} placeholder="例如：斗破苍穹 · 第一部" onChange={(event) => setProjectNameDraft(event.target.value)} /></label>
                <button className="project-file-picker" disabled={isBusy} onClick={() => inputRef.current?.click()}><Upload size={14} /><span>{pendingSourceFile?.name ?? "选择 TXT 小说"}</span></button>
                <button className="primary-button" disabled={isBusy || !projectNameDraft.trim() || !pendingSourceFile} onClick={() => void createProject()}>{busy === "upload" ? <LoaderCircle className="spin" size={14} /> : <FolderPlus size={14} />}创建并导入</button>
              </section>
            ) : <button className="import-button" disabled={isBusy} onClick={() => setShowProjectCreator(true)}><FolderPlus size={15} />创建项目</button>}
          </>
        )}
        {activeStage === "emotions" && <button className="import-button emotion-add-button" disabled={isBusy || !selectedEmotionParent || preview?.emotion_plan?.skipped} onClick={() => setShowCustomEmotion(true)}><Plus size={16} />自定义情绪声线</button>}
      </aside>

      <section className="prep-main-pane">
        <div className="prep-titlebar">
          <div><span className="eyebrow">CURRENT PROJECT</span><h2>{activeStage === "source" ? selectedSource?.display_name ?? "等待创建项目" : activeStage === "casting" || activeStage === "references" ? selectedReference?.display_name ?? "等待角色提取" : activeStage === "emotions" ? selectedEmotion ? `${selectedEmotion.display_name} · ${selectedEmotion.emotion_name}` : "等待情绪计划" : selectedSegment?.segment_id ?? "等待导演文件"}</h2></div>
          <div className="prep-title-actions">
            {activeStage === "casting" && voiceAnalysis && <div className={`voice-analysis-state ${voiceAnalysis.available ? "" : "unavailable"}`} title={`${voiceAnalysis.detail}${voiceAnalysis.model_store ? ` · ${voiceAnalysis.model_store}` : ""}`}><BrainCircuit size={14} /><span>{voiceAnalysis.backend === "local" ? "本地音色分析" : voiceAnalysis.backend === "cloud" ? "云端文本分析" : "规则兼容分析"}</span><strong>{voiceAnalysis.available ? voiceAnalysis.model ?? "已就绪" : "不可用"}</strong></div>}
            <button className="icon-button" title="刷新源文件与产物" disabled={isBusy} onClick={() => void refresh()}>{busy === "refresh" ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}</button>
          </div>
        </div>
        {activeStage === "source" && (
          <>
            <div className="preparation-actions" aria-label="小说准备操作">
              <button className="primary-button continuous-start-button" disabled={isBusy || continuousBusy || !selectedSource || continuousRun?.state === "running" || continuousRun?.state === "starting"} onClick={() => void startOneClickProduction()}>{continuousBusy ? <LoaderCircle className="spin" size={14} /> : <Play size={14} />}一键开始质量生产</button>
              <button className="secondary-button" title="创建新的分析分支，不覆盖已有缓存" disabled={isBusy || !selectedSource} onClick={() => void runAction("analyze")}><FileText size={14} />分析文档</button>
              <button className="secondary-button" disabled={isBusy || !canExtract} onClick={() => void runAction("extract_characters")}><Users size={14} />提取角色</button>
              <button className="secondary-button" disabled={isBusy || !canGenerateDirector} onClick={() => void runAction("generate_director")}><SlidersHorizontal size={14} />生成导演文件</button>
              {activePreparationAction && (
                <button className="secondary-button danger-button preparation-cancel-button" title={`终止${actionLabel[activePreparationAction]}并保留检查点`} disabled={cancellingPreparation} onClick={() => void cancelPreparation()}>{cancellingPreparation ? <LoaderCircle className="spin" size={14} /> : <CircleStop size={14} />}{cancellingPreparation ? "正在终止" : "终止"}</button>
              )}
              {analysisActivity && ["failed", "cancelled"].includes(analysisActivity.state) && analysisActivity.action && selectedRevisionId && (
                <button className="secondary-button retry-analysis-button" disabled={isBusy} onClick={() => void runAction(analysisActivity.action!, true)}><RotateCcw size={14} />从失败处继续</button>
              )}
              <button className="secondary-button" disabled={isBusy || !selectedSource} onClick={() => void openPreview()}><Play size={14} />预览</button>
            </div>
            <section className="continuous-production-control" aria-label="连续质量生产设置">
              <div className="continuous-policy" role="group" aria-label="情绪派生策略">
                {(Object.keys(emotionPolicyLabel) as ContinuousProductionSettings["emotion_policy"][]).map((policy) => <button key={policy} className={continuousSettings.emotion_policy === policy ? "active" : ""} disabled={continuousBusy || Boolean(continuousRun && ["starting", "running", "pausing"].includes(continuousRun.state))} onClick={() => setContinuousSettings((current) => ({ ...current, emotion_policy: policy }))}>{emotionPolicyLabel[policy]}</button>)}
              </div>
              <label><span>后台预取</span><select value={continuousSettings.prefetch_slices} disabled={continuousBusy} onChange={(event) => setContinuousSettings((current) => ({ ...current, prefetch_slices: Number(event.target.value) }))}><option value={1}>1 个切片</option><option value={2}>2 个切片</option></select></label>
              <button
                type="button"
                className={"continuous-rvc-option" + (continuousSettings.rvc_stability_policy === "prepare_candidates" ? " active" : "")}
                aria-pressed={continuousSettings.rvc_stability_policy === "prepare_candidates"}
                disabled={continuousBusy || Boolean(continuousRun && ["starting", "running", "pausing"].includes(continuousRun.state))}
                onClick={() => setContinuousSettings((current) => ({ ...current, rvc_stability_policy: current.rvc_stability_policy === "prepare_candidates" ? "skip" : "prepare_candidates" }))}
              >
                <Database size={14} />
                <span><strong>训练 RVC 稳定层</strong><small>{continuousSettings.rvc_stability_policy === "prepare_candidates" ? "后台同步训练" : "本次跳过"}</small></span>
                <Check size={13} />
              </button>
              <label className="continuous-auto-play"><input type="checkbox" checked={continuousSettings.auto_play} disabled={continuousBusy} onChange={(event) => setContinuousSettings((current) => ({ ...current, auto_play: event.target.checked }))} /><span>自动开始播放</span></label>
            </section>
            {continuousRun && (
              <section className={`continuous-run-summary state--${continuousRun.state}`}>
                <header className="continuous-run-header">
                  <div className="continuous-run-heading"><Layers3 size={14} /><span>连续生产</span><strong>{continuousStateLabel[continuousRun.state]}</strong><em>{continuousStageLabel[continuousRun.current_stage]}</em></div>
                  <div className="continuous-run-runtime"><span><Clock3 size={11} />{formatDuration(continuousRun.elapsed_seconds)}</span><strong>{continuousRun.progress}%</strong></div>
                </header>
                <div className="continuous-progress-track" aria-label={`总进度 ${continuousRun.progress}%`}><span style={{ width: `${continuousRun.progress}%` }} /></div>
                {continuousRun.settings.rvc_stability_policy === "prepare_candidates" && (
                  <div className="continuous-rvc-progress">
                    <header><span><Database size={12} />RVC 稳定层</span><strong>{continuousRun.rvc_progress}%</strong>{continuousRvcReviewCount > 0 && <em>{continuousRvcReviewCount} 个待审核</em>}</header>
                    <div className="continuous-progress-track"><span style={{ width: `${continuousRun.rvc_progress}%` }} /></div>
                    <div className="continuous-rvc-task-strip">
                      {continuousRun.rvc_tasks.map((task) => <span key={task.character_id} className={`state--${task.status}`} title={task.error ?? task.message}><strong>{task.display_name}</strong><small>{rvcPreparationStatusLabel[task.status]}</small></span>)}
                      {!continuousRun.rvc_tasks.length && <small>等待角色参考计划</small>}
                    </div>
                  </div>
                )}
                <div className="continuous-run-detail">
                  <p><strong>{continuousCurrentSlice ? `切片 ${continuousCurrentSlice.index} · ${continuousCurrentSlice.title}` : "等待切片"}</strong><span>{continuousRun.message}</span></p>
                  <div className="continuous-run-stats" aria-label="连续生产统计">
                    <span><b>{continuousCompletedCount}</b>/{continuousRun.slices.length} 已完成</span>
                    <span><b>{continuousReadyCount}</b> 可渲染</span>
                    <span><b>{continuousReusedCount}</b> 资源复用</span>
                    <span className={continuousFailedCount ? "attention" : ""}><b>{continuousFailedCount}</b> 失败</span>
                  </div>
                </div>
                <div className="continuous-slice-strip continuous-slice-strip--local" role="list" aria-label="长篇切片处理队列">
                  {continuousRun.slices.map((slice) => (
                    <span key={slice.slice_id} role="listitem" data-slice-id={slice.slice_id} className={`state--${slice.state}${slice.slice_id === continuousRun.current_slice_id ? " current" : ""}`} title={`${slice.title} · ${slice.message}${slice.error ? ` · ${slice.error}` : ""}`}>
                      <span><b>切片 {slice.index}</b><small>{slice.progress}%</small></span>
                      <i><span style={{ width: `${slice.progress}%` }} /></i>
                      <small>{productionSliceStateLabel[slice.state]} · {slice.completed_segment_count}/{slice.segment_count || 0} 句</small>
                    </span>
                  ))}
                </div>
                <footer className="continuous-run-footer">
                  <span>{continuousRun.slices.length ? `${continuousRun.slices.length} 个切片 · 后台预取 ${continuousRun.settings.prefetch_slices}` : "正在规划长篇切片"}</span>
                  <div className="continuous-run-actions">
                    {continuousRun.state === "paused" ? <button className="icon-button" title="继续连续生产" disabled={continuousBusy} onClick={() => void runContinuousCommand("resume")}><Play size={13} /></button> : ["starting", "running", "render_ready"].includes(continuousRun.state) && <button className="icon-button" title="暂停后台生产" disabled={continuousBusy} onClick={() => void runContinuousCommand("pause")}><Pause size={13} /></button>}
                    {continuousRun.state === "failed" && <button className="icon-button" title="重试失败阶段" disabled={continuousBusy} onClick={() => void runContinuousCommand("retry")}><RotateCcw size={13} /></button>}
                    {continuousRun.state === "failed" && <button className="icon-button" title="跳过当前问题切片" disabled={continuousBusy} onClick={() => void runContinuousCommand("skip")}><ArrowRight size={13} /></button>}
                    {!continuousRun.completed_at && <button className="icon-button danger-button" title="终止连续生产" disabled={continuousBusy} onClick={() => void runContinuousCommand("cancel")}><CircleStop size={13} /></button>}
                  </div>
                </footer>
              </section>
            )}
            <div className="revision-toolbar" aria-label="阶段缓存选择">
              <label><GitBranch size={13} /><span>阶段缓存</span><select value={selectedRevisionId ?? ""} disabled={isBusy || !revisionWorkspace?.revisions.length} onChange={(event) => void selectRevision(event.target.value)}>{revisionWorkspace?.revisions.length ? revisionWorkspace.revisions.map((revision) => <option key={revision.revision_id} value={revision.revision_id}>{revision.display_name} · {revisionStatusLabel[revision.status]}</option>) : <option value="">分析后创建分支</option>}</select></label>
              <span>{selectedRevision ? `${new Date(selectedRevision.updated_at).toLocaleString("zh-CN", { hour12: false })} · ${revisionStatusLabel[selectedRevision.status]}` : "当前没有阶段缓存"}</span>
              <button className="icon-button" title="删除当前分析分支缓存" disabled={isBusy || !selectedRevisionId} onClick={() => selectedRevisionId && void removeRevision(selectedRevisionId)}><Trash2 size={14} /></button>
            </div>
            {analysisSettingsDraft && (
              <section className="long-form-settings" aria-label="长篇分析设置">
                <header>
                  <div><BookOpenText size={15} /><span>长篇分批</span><strong>{longFormPlan ? longFormStrategyLabel[longFormPlan.strategy] : longFormModeLabel[analysisSettingsDraft.mode]}</strong></div>
                  <button className="icon-button" title="保存长篇分析设置" disabled={isBusy || !analysisSettingsChanged} onClick={() => void saveLongFormSettings()}><Save size={14} /></button>
                </header>
                <div className="long-form-mode" role="group" aria-label="长篇切分模式">
                  {(Object.keys(longFormModeLabel) as LongFormMode[]).map((mode) => (
                    <button key={mode} className={analysisSettingsDraft.mode === mode ? "active" : ""} disabled={isBusy} onClick={() => setAnalysisSettingsDraft((current) => current ? { ...current, mode } : current)}>{longFormModeLabel[mode]}</button>
                  ))}
                </div>
                <div className="long-form-sliders">
                  <label><span>长篇判定<strong>{analysisSettingsDraft.long_text_threshold.toLocaleString()} 字</strong></span><input type="range" min="20000" max="500000" step="10000" disabled={isBusy} value={analysisSettingsDraft.long_text_threshold} onChange={(event) => setAnalysisSettingsDraft((current) => current ? { ...current, long_text_threshold: Number(event.target.value) } : current)} /></label>
                  <label><span>每批章节<strong>{analysisSettingsDraft.chapters_per_batch} 章</strong></span><input type="range" min="5" max="200" step="5" disabled={isBusy || analysisSettingsDraft.mode === "characters"} value={analysisSettingsDraft.chapters_per_batch} onChange={(event) => setAnalysisSettingsDraft((current) => current ? { ...current, chapters_per_batch: Number(event.target.value) } : current)} /></label>
                  <label><span>每批字符<strong>{analysisSettingsDraft.characters_per_batch.toLocaleString()} 字</strong></span><input type="range" min="10000" max="200000" step="5000" disabled={isBusy || analysisSettingsDraft.mode === "chapters"} value={analysisSettingsDraft.characters_per_batch} onChange={(event) => setAnalysisSettingsDraft((current) => current ? { ...current, characters_per_batch: Number(event.target.value) } : current)} /></label>
                  <label><span>API 并发<strong>{analysisSettingsDraft.parallelism}</strong></span><input type="range" min="1" max="8" step="1" disabled={isBusy} value={analysisSettingsDraft.parallelism} onChange={(event) => setAnalysisSettingsDraft((current) => current ? { ...current, parallelism: Number(event.target.value) } : current)} /></label>
                </div>
                {longFormPlan && (
                  <div className="long-form-batches" aria-label="长篇批次状态">
                    <div><Layers3 size={13} /><span>{longFormPlan.batches.length} 批</span><strong>{longFormPlan.total_characters.toLocaleString()} 字</strong></div>
                    <div className="long-form-batch-strip">
                      {longFormPlan.batches.slice(0, 20).map((batch) => <span key={batch.batch_id} className={`state--${batch.state}`} title={`${batch.title} · ${batch.character_count.toLocaleString()} 字`}>{batch.index}<small>{longFormBatchStateLabel[batch.state]}</small></span>)}
                      {longFormPlan.batches.length > 20 && <em>+{longFormPlan.batches.length - 20}</em>}
                    </div>
                  </div>
                )}
              </section>
            )}
          </>
        )}
        {activeStage === "casting" && preview?.reference_plan && (
          <div className="casting-threshold-control">
            <div><span>自动生成权重阈值</span><strong>{thresholdPercent}% 及以上</strong></div>
            <input type="range" min="1" max="100" step="1" value={thresholdPercent} aria-label="自动生成权重阈值" disabled={isBusy} style={{ background: `linear-gradient(to right, var(--theme-accent) 0%, var(--theme-accent) ${thresholdPercent}%, var(--theme-control-track) ${thresholdPercent}%, var(--theme-control-track) 100%)` }} onChange={(event) => setThresholdPercent(Number(event.target.value))} onPointerUp={() => void saveReferenceThreshold()} onKeyUp={() => void saveReferenceThreshold()} />
            <button className="icon-button" title="应用权重阈值" disabled={isBusy || Math.abs(thresholdPercent / 100 - preview.reference_plan.automatic_threshold) < 0.0001} onClick={() => void saveReferenceThreshold()}><Save size={14} /></button>
            <button className={`secondary-button threshold-lock-button ${preview.reference_plan.automatic_items_locked ? "" : "active"}`} disabled={isBusy} onClick={() => void toggleAutomaticReferenceLock()}>{preview.reference_plan.automatic_items_locked ? <Unlock size={14} /> : <Lock size={14} />}{preview.reference_plan.automatic_items_locked ? "解锁选择" : "锁定选择"}</button>
          </div>
        )}
        {activeStage === "casting" && preview?.reference_plan && (
          <div className="preparation-actions casting-profile-actions" aria-label="角色声线描述重新生成">
            <label className="casting-attribute-editor">
              <span>自定义音色属性</span>
              <textarea
                maxLength={500}
                value={customVoiceAttributesDraft}
                disabled={isBusy || !selectedReference || (selectedReference.selection_mode === "narrator_default" && selectedReference.voice_prompt_locked)}
                placeholder="例如：年轻但不轻浮，句尾带一点压住锋芒的笑意"
                onChange={(event) => setCustomVoiceAttributesDraft(event.target.value)}
              />
              <small>{customVoiceAttributesDraft.length} / 500</small>
            </label>
            <button
              className="secondary-button"
              disabled={isBusy || !voiceAnalysis?.available || !selectedReference || (selectedReference.selection_mode === "narrator_default" && selectedReference.voice_prompt_locked)}
              title={selectedReference?.selection_mode === "narrator_default" && selectedReference.voice_prompt_locked ? "先点击男/女旁白旁边的解锁按钮" : "根据当前证据重新生成声线描述"}
              onClick={() => void regenerateSelectedVoiceProfile()}
            >
              {busy?.startsWith("voice_profile:") ? <LoaderCircle className="spin" size={14} /> : <RotateCcw size={14} />}
              重新生成当前描述
            </button>
            <button
              className="secondary-button"
              disabled={isBusy || !voiceAnalysis?.available || !referenceItems.some((item) => item.selection_mode !== "narrator_default" || !item.voice_prompt_locked)}
              title="根据全部角色证据重新生成声线描述"
              onClick={() => void regenerateAllVoiceProfiles()}
            >
              {busy === "voice_profiles:all" ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}
              全部重新生成描述
            </button>
          </div>
        )}
        {activeStage === "emotions" && preview?.emotion_plan && (
          <div className="emotion-settings-control">
            <button className={`secondary-button emotion-skip-button ${preview.emotion_plan.skipped ? "active" : ""}`} disabled={isBusy} onClick={() => void toggleEmotionSkip()}>{preview.emotion_plan.skipped ? <Play size={14} /> : <CircleAlert size={14} />}{preview.emotion_plan.skipped ? "启用情绪派生" : "跳过这一步"}</button>
            <div><span>自动生产权重阈值</span><strong>{emotionThresholdPercent}% 及以上</strong></div>
            <input type="range" min="1" max="100" step="1" value={emotionThresholdPercent} aria-label="情绪自动生产权重阈值" disabled={isBusy || preview.emotion_plan.skipped} style={{ background: `linear-gradient(to right, var(--theme-accent) 0%, var(--theme-accent) ${emotionThresholdPercent}%, var(--theme-control-track) ${emotionThresholdPercent}%, var(--theme-control-track) 100%)` }} onChange={(event) => setEmotionThresholdPercent(Number(event.target.value))} onPointerUp={() => void saveEmotionThreshold()} onKeyUp={() => void saveEmotionThreshold()} />
            <button className="icon-button" title="应用情绪权重阈值" disabled={isBusy || preview.emotion_plan.skipped || Math.abs(emotionThresholdPercent / 100 - preview.emotion_plan.automatic_threshold) < 0.0001} onClick={() => void saveEmotionThreshold()}><Save size={14} /></button>
            <button className={`secondary-button threshold-lock-button ${preview.emotion_plan.automatic_items_locked ? "" : "active"}`} disabled={isBusy || preview.emotion_plan.skipped} onClick={() => void toggleAutomaticEmotionLock()}>{preview.emotion_plan.automatic_items_locked ? <Unlock size={14} /> : <Lock size={14} />}{preview.emotion_plan.automatic_items_locked ? "解锁选择" : "锁定选择"}</button>
          </div>
        )}
        {activeStage === "director" && (
          <div className="preparation-actions director-script-actions" aria-label="导演脚本操作">
            <button className="secondary-button" disabled={isBusy || !canGenerateDirector} onClick={() => void runAction("generate_director")}>
              {busy === "generate_director" ? <LoaderCircle className="spin" size={14} /> : <BrainCircuit size={14} />}
              生成脚本文件
            </button>
            <button className="secondary-button" disabled={isBusy || !preview?.director_doc} onClick={downloadDirectorDocument}><Download size={14} />导出 JSON</button>
            <button className="secondary-button danger-text" disabled={isBusy || !preview?.director_doc} onClick={() => void removeDirectorCache()}><Trash2 size={14} />删除脚本缓存</button>
            {preview?.director_doc && <span className="director-analysis-summary"><BrainCircuit size={13} />{preview.director_doc.analysis_backend === "local" ? preview.director_doc.analysis_model ?? "本地模型" : preview.director_doc.analysis_backend === "cloud" ? preview.director_doc.analysis_model ?? "云端模型" : "规则回退"}{preview.director_doc.warnings.length ? ` · ${preview.director_doc.warnings.length} 条待复核` : " · 已完成"}</span>}
          </div>
        )}
        <div className="operation-feedback" role="status">{isBusy && <LoaderCircle className="spin" size={14} />}{feedback}</div>
        {showPreview && preview ? <PreviewPanel preview={preview} /> : (
          <>
            <div className="field-table">
              {fields.map((field) => <div key={field.label}><span>{field.label}</span><strong>{field.value}</strong></div>)}
              {!fields.length && <p className="empty-state">完成前置操作后，这里会显示真实产物字段。</p>}
            </div>
            {activeStage === "casting" && selectedReference && <div className="evidence-block"><span>证据摘录</span>{selectedReferenceCandidate?.evidence.length ? selectedReferenceCandidate.evidence.map((evidence, index) => <p key={`${selectedReference.reference_id}:${index}`}>{evidence}</p>) : selectedPreparedCharacter?.evidence.length ? selectedPreparedCharacter.evidence.map((evidence) => <p key={evidence.segment_id}>{evidence.text}</p>) : <p>默认旁白不依赖角色对话证据。</p>}</div>}
            {(activeStage === "casting" || activeStage === "references") && selectedReference && (
              <VoiceReusePanel
                matches={voiceMatches}
                disabled={isBusy}
                loading={voiceMatchesLoading}
                onRefresh={refreshVoiceMatches}
                onReuse={reuseHistoricalVoice}
              />
            )}
            {activeStage === "references" && selectedReference && (
              <section className="reference-preview">
                <ReferenceTextPanel
                  reference={selectedReference}
                  disabled={isBusy}
                  generating={busy === `reference_text_generate:${selectedReference.reference_id}`}
                  onGenerate={() => generateReferenceTextVersion(selectedReference.reference_id)}
                  onSave={(text) => saveReferenceTextVersion(selectedReference.reference_id, text)}
                  onActivate={(versionId) => activateReferenceText(selectedReference.reference_id, versionId)}
                  onDelete={(versionId) => deleteReferenceText(selectedReference.reference_id, versionId)}
                />
                <div className="reference-voice-editor">
                  <label htmlFor="reference-voice-prompt">声线描述</label>
                  <textarea id="reference-voice-prompt" maxLength={1000} value={voicePromptDraft} disabled={isBusy} onChange={(event) => setVoicePromptDraft(event.target.value)} />
                  <div><small>{voicePromptDraft.length} / 1000</small><button className="secondary-button" disabled={isBusy || !voicePromptDraft.trim() || voicePromptDraft.trim() === selectedReference.voice_prompt} onClick={() => void saveVoicePrompt()}><Save size={14} />保存声线描述</button></div>
                </div>
              </section>
            )}
            {activeStage === "emotions" && showCustomEmotion && selectedEmotionParent && (
              <section className="custom-emotion-editor">
                <div className="section-title"><Plus size={16} /><h3>{selectedEmotionParent.display_name} · 自定义情绪</h3></div>
                <label><span>情绪名称</span><input maxLength={40} value={customEmotionName} disabled={isBusy} placeholder="例如：克制期待" onChange={(event) => setCustomEmotionName(event.target.value)} /></label>
                <label><span>声音描述</span><textarea maxLength={1000} value={customEmotionDescription} disabled={isBusy} placeholder="描述语气、气息、节奏和能量变化" onChange={(event) => setCustomEmotionDescription(event.target.value)} /></label>
                <label className="custom-emotion-intensity"><span>强度</span><input type="range" min="5" max="100" value={customEmotionIntensity} disabled={isBusy} onChange={(event) => setCustomEmotionIntensity(Number(event.target.value))} /><strong>{customEmotionIntensity}%</strong></label>
                <div><button className="text-button" disabled={isBusy} onClick={() => setShowCustomEmotion(false)}>取消</button><button className="primary-button" disabled={isBusy || !customEmotionName.trim() || !customEmotionDescription.trim()} onClick={() => void addCustomEmotion()}><Plus size={14} />加入生产计划</button></div>
              </section>
            )}
            {activeStage === "emotions" && !showCustomEmotion && selectedEmotion && (
              <section className="emotion-preview">
                <div className="reference-copy"><span>情绪描述</span><p>{selectedEmotion.description}</p></div>
                <div className="reference-copy"><span>比较文本</span><p>{selectedEmotion.reference_text}</p></div>
                {currentEmotionJob && currentEmotionJob.status !== "complete" && <div className={`job-progress ${currentEmotionJob.status === "failed" ? "job-progress--failed" : ""}`}><span style={{ width: `${currentEmotionJob.progress}%` }} /><small>{currentEmotionJob.message}</small></div>}
                {selectedEmotion.audio_url && <Waveform src={selectedEmotion.audio_url} color="violet" />}
                {selectedEmotion.audio_url ? <AudioPlayer src={selectedEmotion.audio_url} label={`${selectedEmotion.display_name}${selectedEmotion.emotion_name}情绪音频试听`} /> : <p className="reference-empty-audio">生成完成后可在这里试听。</p>}
                {selectedEmotion.error && <p className="reference-error">{selectedEmotion.error}</p>}
                <div className="emotion-preview-actions">{selectedEmotion.selection_mode === "custom" && <button className="text-button danger-text" disabled={isBusy} onClick={() => void removeCustomEmotion(selectedEmotion)}><Trash2 size={14} />删除</button>}<button className="secondary-button" disabled={isBusy || preview?.emotion_plan?.skipped || !selectedEmotion.selected || selectedEmotion.selection_mode === "base"} onClick={() => void generateEmotionVariant(selectedEmotion)}>{selectedEmotion.status === "generated" ? <RotateCcw size={14} /> : <Sparkles size={14} />}{selectedEmotion.selection_mode === "base" ? "复用父参考" : selectedEmotion.status === "generated" ? "重新生成" : "生成试听"}</button></div>
              </section>
            )}
            {activeStage === "director" && selectedSegment && <div className="evidence-block"><span>朗读文本</span><p>{selectedSegment.text}</p></div>}
          </>
        )}
      </section>

      <aside className={`prep-check-pane ${activeStage === "source" ? "has-analysis-monitor" : ""} ${(activeStage === "casting" || activeStage === "references") && selectedReference ? "has-reference-dock" : ""}`}>
        {(activeStage === "casting" || activeStage === "references") && selectedReference && (
          <section className="reference-review-dock">
            <details className="reference-audio-disclosure reference-audio-disclosure--dock" open={referenceAudioExpanded} onToggle={(event) => setReferenceAudioExpanded(event.currentTarget.open)}>
              <summary><Mic2 size={14} /><span>{selectedReference.display_name} · 参考音频</span><strong>{selectedReference.audio_versions.length ? `${selectedReference.audio_versions.length} 个版本` : ""}</strong></summary>
              {currentReferenceJob && currentReferenceJob.status !== "complete" && (
                <div className={`job-progress ${currentReferenceJob.status === "failed" ? "job-progress--failed" : ""}`}><span style={{ width: `${currentReferenceJob.progress}%` }} /><small>{currentReferenceJob.message}</small></div>
              )}
              <ReferenceAudioPanel
                compact
                hideEmpty={activeStage === "casting"}
                waveformColor={selectedReferenceColor}
                reference={selectedReference}
                disabled={isBusy}
                onUpload={saveReferenceAudio}
                onActivate={(versionId) => activateReferenceVersion(selectedReference.reference_id, versionId)}
                onReview={(versionId, decision) => reviewReferenceVersion(selectedReference.reference_id, versionId, decision)}
                onDelete={(versionId) => deleteReferenceVersion(selectedReference.reference_id, versionId)}
                onClear={() => clearReferenceCache(selectedReference.reference_id)}
                onError={setFeedback}
              />
              {selectedReference.error && <p className="reference-error">{selectedReference.error}</p>}
              {activeStage === "references" && <button className="secondary-button reference-generate-button" disabled={isBusy || !selectedReference.reference_text.trim()} onClick={() => void generateReference(selectedReference)}>{selectedReference.status === "generated" ? <RotateCcw size={14} /> : <Mic2 size={14} />}{selectedReference.status === "generated" ? "重新生成" : "生成预览"}</button>}
            </details>
          </section>
        )}
        <div className="pane-heading"><div><span className="eyebrow">STAGE GATE</span><h2>阶段检查</h2></div></div>
        <div className="checkpoint-list">
          {activeStage === "source" && (
            <>
              <div>{selectedSource ? <Check size={15} /> : <CircleAlert size={15} />}<span>TXT 源文件</span><strong>{selectedSource ? "已选择" : "待导入"}</strong></div>
              <div className={!preview?.analysis_audit ? "attention" : ""}>{preview?.analysis_audit ? <Check size={15} /> : <CircleAlert size={15} />}<span>分析审计</span><strong>{preview?.analysis_audit ? "已落盘" : "待生成"}</strong></div>
              <div className={!preview?.character_voice_bible ? "attention" : ""}>{preview?.character_voice_bible ? <Check size={15} /> : <CircleAlert size={15} />}<span>角色圣经</span><strong>{preview?.character_voice_bible ? `${preview.character_voice_bible.characters.length} 个角色` : "待生成"}</strong></div>
              <div className={!preview?.director_doc ? "attention" : ""}>{preview?.director_doc ? <Check size={15} /> : <CircleAlert size={15} />}<span>导演文件</span><strong>{preview?.director_doc ? `${preview.director_doc.segments.length} 句` : "待生成"}</strong></div>
            </>
          )}
          {activeStage === "casting" && (
            <>
              <div><Check size={15} /><span>默认旁白</span><strong>男 / 女</strong></div>
              <div><Check size={15} /><span>自动生成</span><strong>{referenceItems.filter((item) => item.selection_mode !== "optional").length}</strong></div>
              <div><Check size={15} /><span>手动加入</span><strong>{referenceItems.filter((item) => item.selection_mode === "optional" && item.selected).length}</strong></div>
              <div className={referenceItems.some((item) => !item.selected) ? "attention" : ""}><CircleAlert size={15} /><span>复用旁白</span><strong>{referenceItems.filter((item) => !item.selected).length}</strong></div>
            </>
          )}
          {activeStage === "references" && (
            <>
              <div><Check size={15} /><span>已选参考</span><strong>{selectedReferences.length}</strong></div>
              <div><Check size={15} /><span>生成完成</span><strong>{generatedReferenceCount} / {selectedReferences.length}</strong></div>
              <div className={selectedReferences.some((item) => item.status === "failed") ? "attention" : ""}><CircleAlert size={15} /><span>失败任务</span><strong>{selectedReferences.filter((item) => item.status === "failed").length}</strong></div>
              <div><Check size={15} /><span>未选角色回退</span><strong>{referenceItems.filter((item) => !item.selected).length}</strong></div>
            </>
          )}
          {activeStage === "emotions" && (
            <>
              <div className={preview?.emotion_plan?.skipped ? "attention" : ""}>{preview?.emotion_plan?.skipped ? <CircleAlert size={15} /> : <Check size={15} />}<span>情绪派生</span><strong>{preview?.emotion_plan?.skipped ? "已跳过" : "已启用"}</strong></div>
              <div><Check size={15} /><span>已选生产</span><strong>{selectedEmotionItems.filter((item) => item.selection_mode !== "base").length}</strong></div>
              <div className={!allEmotionsReady ? "attention" : ""}>{allEmotionsReady ? <Check size={15} /> : <CircleAlert size={15} />}<span>生成完成</span><strong>{generatedEmotionCount} / {selectedEmotionItems.length}</strong></div>
              <div><Check size={15} /><span>自定义情绪</span><strong>{emotionItems.filter((item) => item.selection_mode === "custom").length}</strong></div>
            </>
          )}
          {activeStage === "director" && (
            <>
              <div><Check size={15} /><span>导演片段</span><strong>{segments.length}</strong></div>
              <div><Check size={15} /><span>角色绑定</span><strong>{segments.filter((segment) => !!characterNames.get(segment.character_id)).length} / {segments.length}</strong></div>
              <div><Check size={15} /><span>分析后端</span><strong>{preview?.director_doc?.analysis_backend === "local" ? "本地模型" : preview?.director_doc?.analysis_backend === "cloud" ? "云端 API" : "规则回退"}</strong></div>
              <div className={preview?.director_doc?.warnings.length ? "attention" : ""}>{preview?.director_doc?.warnings.length ? <CircleAlert size={15} /> : <Check size={15} />}<span>待复核</span><strong>{preview?.director_doc?.warnings.length ?? 0}</strong></div>
            </>
          )}
        </div>
        {activeStage === "source" && (
          <section className="analysis-monitor" aria-label="文本分析输入输出与处理进度">
            <div className={`analysis-progress-panel state--${analysisActivity?.state ?? "idle"}`}>
              <header><Activity size={14} /><strong>处理进度</strong><span><Clock3 size={11} />{formatDuration(analysisActivity?.elapsed_seconds ?? 0)} · {analysisActivity?.percent ?? 0}%</span></header>
              <div className="analysis-progress-track"><span style={{ width: `${analysisActivity?.percent ?? 0}%` }} /></div>
              <p>{analysisActivity?.message ?? "尚未开始分析"}</p>
              <footer><span>{analysisActivity?.current_batch && analysisActivity?.total_batches ? `批次 ${analysisActivity.current_batch}/${analysisActivity.total_batches}` : analysisActivity?.backend === "cloud" ? "云端 API" : analysisActivity?.backend === "local" ? "本地模型" : analysisActivity?.backend === "rules" ? "规则分析" : "等待任务"}</span><span>{analysisActivity?.model ?? "-"}</span></footer>
              {analysisActivity && ["failed", "cancelled"].includes(analysisActivity.state) && analysisActivity.action && selectedRevisionId && <button className="secondary-button analysis-resume-button" disabled={isBusy} onClick={() => void runAction(analysisActivity.action!, true)}><RotateCcw size={14} />继续生成未完成批次</button>}
            </div>
            <div className="analysis-io-grid">
              <AnalysisEventBox title="输入" direction="input" events={analysisActivity?.input_events ?? []} />
              <AnalysisEventBox title="输出" direction="output" events={analysisActivity?.output_events ?? []} />
            </div>
          </section>
        )}
        <div className={`stage-action ${activeStage === "references" || activeStage === "emotions" ? "reference-stage-actions" : ""}`}>
          {activeStage === "source" && <button className="primary-button" disabled={!canAdvanceSource || isBusy} title={!canAdvanceSource ? "请先提取角色" : "进入角色审核"} onClick={() => onStageChange("casting")}>下一步<ArrowRight size={15} /></button>}
          {activeStage === "casting" && <button className="primary-button" disabled={!preview?.reference_plan || isBusy} onClick={() => onStageChange("references")}>进入标准参考<ArrowRight size={15} /></button>}
          {activeStage === "references" && <><button className="secondary-button" disabled={isBusy || allReferencesGenerated} onClick={() => void generateSelectedReferences()}><Mic2 size={14} />批量生成</button><button className="primary-button" disabled={isBusy || !allReferencesGenerated} onClick={() => onStageChange("emotions")}>进入情绪派生<ArrowRight size={15} /></button></>}
          {activeStage === "emotions" && <><button className="secondary-button" disabled={isBusy || preview?.emotion_plan?.skipped || allEmotionsReady} onClick={() => void generateSelectedEmotions()}><Sparkles size={14} />一键生成</button><button className="primary-button" disabled={isBusy || !allEmotionsReady} onClick={() => onStageChange("director")}>进入导演脚本<ArrowRight size={15} /></button></>}
          {activeStage === "director" && <button className="primary-button" disabled={!preview?.director_doc || isBusy} onClick={() => onStageChange("quality_render")}>进入{routeMode === "fast" ? "极速" : "质量"}渲染<ArrowRight size={15} /></button>}
        </div>
      </aside>
    </section>
  );
}
