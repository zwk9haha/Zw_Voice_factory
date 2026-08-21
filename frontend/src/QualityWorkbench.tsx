import { Check, ChevronLeft, ChevronRight, CircleAlert, CircleStop, Download, FileAudio, Gauge, Layers3, ListMusic, LoaderCircle, Mic, Pause, Play, RefreshCw, Save, Settings2, Sparkles, Trash2, Upload, Users, WandSparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { AdvancedSettingsPanel, DEFAULT_PROGRAM_LOUDNESS_POLICY, DEFAULT_QUALITY_RENDER_OPTIONS } from "./AdvancedSettingsPanel";
import { AudioPlayer } from "./AudioPlayer";
import { cancelQualityQueue, createAudioJob, deleteQualityCache, fetchAudioJob, fetchAudioJobs, fetchContinuousProduction, fetchPreparationPreview, fetchProductionSettings, fetchRvcWorkspace, mergeQualityAudio, openQualityAudioStream, reprocessQualityLoudness, reprocessQualityRvc, updateDirectorSegmentVoice, updateProductionPreferences, updateReferenceVoicePrompt, uploadReferenceAudio } from "./api";
import { normalizeAudioToWav } from "./audioFile";
import { consumeQualityPcmStream, QualityPcmPlayer } from "./qualityAudioStream";
import { collectReusableQualityJobs, resolveQualityVoice } from "./qualityRouting";
import type { AudioJob, AudioJobRequest, ContinuousProductionRun, LoudnessMetrics, MergedAudio, PreparationPreview, PreparedDirectorSegment, ProductionSettings, ProductionStageId, ProgramLoudnessPolicy, QualityModelId, QualityRenderOptions, ReferencePlanItem, RvcWorkspace, SourceSummary } from "./types";
import { Waveform } from "./Waveform";

interface QualityWorkbenchProps {
  qualityModel: QualityModelId;
  qualityModelLabel: string;
  sources: SourceSummary[];
  selectedProjectId: string | null;
  selectedSliceId: string | null;
  onSelectedSliceChange: (sliceId: string) => void;
  onProjectChange: (projectId: string | null) => void;
  onStageChange: (stage: ProductionStageId) => void;
}

type CastColor = "teal" | "violet" | "gold";

interface WorkbenchReference extends ReferencePlanItem {
  color: CastColor;
  tier: "core" | "supporting" | "minor" | "uncertain";
}

interface StreamQueue {
  sessionId: number;
  targets: PreparedDirectorSegment[];
  startIndex: number;
  playIndex: number;
  submitIndex: number;
  jobIds: Map<string, string>;
  skipped: Set<string>;
  playedJobIds: string[];
  paused: boolean;
  generationComplete: boolean;
  lastScheduledIndex: number;
  lastFinishedIndex: number;
}

interface StreamPlaybackView {
  sessionId: number;
  currentIndex: number;
  currentSegmentId: string;
  remaining: number;
  status: "preparing" | "buffering" | "playing" | "paused";
}

interface SegmentLocation {
  segmentId: string;
  target: "script" | "result";
}

interface ChapterGroup {
  chapterId: string;
  label: string;
  startIndex: number;
  segments: PreparedDirectorSegment[];
}

const COLORS: CastColor[] = ["teal", "violet", "gold"];
const STREAM_LOOKAHEAD_SENTENCES = 3;
const qualityRvcStatusLabel: Record<ContinuousProductionRun["rvc_tasks"][number]["status"], string> = {
  waiting_reference: "等待参考",
  reused: "已复用",
  queued: "等待空闲",
  building_material: "构建素材",
  training: "训练中",
  benchmarking: "基准中",
  awaiting_review: "待审核",
  approved: "已启用",
  deferred: "延期",
  skipped: "跳过",
  rejected: "拒绝",
  failed: "回退",
};

function isPending(job: AudioJob | undefined): boolean {
  return job?.status === "queued" || job?.status === "running";
}

function jobLabel(job: AudioJob | undefined, fallback: string): string {
  if (!job) return fallback;
  if (job.status === "cancelled") return "已取消";
  if (job.status === "failed") return `失败 · ${job.error ?? job.message}`;
  if (job.status === "complete") return job.output_url ? "已生成" : job.message;
  return `${job.progress}% · ${job.message}`;
}

function loudnessLabel(metrics: LoudnessMetrics | null | undefined): string | null {
  if (!metrics || metrics.status !== "corrected" || metrics.output_lufs === null || metrics.output_true_peak_dbtp === null) return null;
  const gain = metrics.applied_gain_db >= 0 ? `+${metrics.applied_gain_db.toFixed(1)}` : metrics.applied_gain_db.toFixed(1);
  return `${metrics.output_lufs.toFixed(1)} LUFS · ${metrics.output_true_peak_dbtp.toFixed(1)} dBTP · ${gain} dB`;
}

function chapterLabel(chapterId: string, fallbackIndex: number): string {
  const match = chapterId.match(/chapter-(\d+)/i);
  return match ? `第 ${Number(match[1])} 章` : `第 ${fallbackIndex + 1} 章`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请检查后端服务";
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function latestJobsBy<T extends string>(jobs: AudioJob[], key: (job: AudioJob) => T | null): Map<T, AudioJob> {
  const result = new Map<T, AudioJob>();
  [...jobs]
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
    .forEach((job) => {
      const value = key(job);
      if (value && !result.has(value)) result.set(value, job);
    });
  return result;
}

export function QualityWorkbench({ qualityModel, qualityModelLabel, sources, selectedProjectId, selectedSliceId, onSelectedSliceChange, onProjectChange, onStageChange }: QualityWorkbenchProps) {
  const [preview, setPreview] = useState<PreparationPreview | null>(null);
  const [activeReferenceId, setActiveReferenceId] = useState<string | null>(null);
  const [voicePromptDraft, setVoicePromptDraft] = useState("");
  const [jobs, setJobs] = useState<Record<string, AudioJob>>({});
  const [selectedSegmentIds, setSelectedSegmentIds] = useState<Set<string>>(new Set());
  const [chapterIndex, setChapterIndex] = useState(0);
  const [playing, setPlaying] = useState<string | null>(null);
  const [operationFeedback, setOperationFeedback] = useState("选择已完成导演脚本的项目");
  const [loadingProject, setLoadingProject] = useState(true);
  const [submittingBatch, setSubmittingBatch] = useState(false);
  const [referenceBusy, setReferenceBusy] = useState(false);
  const [mergedAudio, setMergedAudio] = useState<MergedAudio | null>(null);
  const [renderOptions, setRenderOptions] = useState<QualityRenderOptions>(DEFAULT_QUALITY_RENDER_OPTIONS);
  const selectionManuallyEditedRef = useRef(false);
  const [loudnessPolicy, setLoudnessPolicy] = useState<ProgramLoudnessPolicy>(DEFAULT_PROGRAM_LOUDNESS_POLICY);
  const [productionSettings, setProductionSettings] = useState<ProductionSettings | null>(null);
  const [selectedCacheJobIds, setSelectedCacheJobIds] = useState<Set<string>>(new Set());
  const [cacheBusy, setCacheBusy] = useState(false);
  const [segmentVoiceBusy, setSegmentVoiceBusy] = useState<string | null>(null);
  const [showAdvancedSettings, setShowAdvancedSettings] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [streamView, setStreamView] = useState<StreamPlaybackView | null>(null);
  const [segmentLocation, setSegmentLocation] = useState<SegmentLocation | null>(null);
  const [continuousRun, setContinuousRun] = useState<ContinuousProductionRun | null>(null);
  const [rvcWorkspace, setRvcWorkspace] = useState<RvcWorkspace | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const jobsRef = useRef<Record<string, AudioJob>>({});
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recordingStreamRef = useRef<MediaStream | null>(null);
  const recordingTimerRef = useRef<number | null>(null);
  const recordingChunksRef = useRef<Blob[]>([]);
  const batchGenerationRef = useRef(0);
  const batchSubmissionRef = useRef<Promise<void> | null>(null);
  const streamSessionRef = useRef(0);
  const streamQueueRef = useRef<StreamQueue | null>(null);
  const streamSubmissionRef = useRef<Promise<AudioJob> | null>(null);
  const streamPumpingRef = useRef(false);
  const streamPumpRequestedRef = useRef<number | null>(null);
  const streamPlayingSegmentRef = useRef<string | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  const pcmPlayerRef = useRef<QualityPcmPlayer | null>(null);
  const locationTimerRef = useRef<number | null>(null);
  const segmentClickTimerRef = useRef<number | null>(null);
  const autoPlayedRunRef = useRef<string | null>(null);
  const v3V4Streaming = (qualityModel === "gpt_sovits_v3" || qualityModel === "gpt_sovits_v4")
    && !rvcWorkspace?.settings.quality_stability_enabled;

  function pcmPlayer(): QualityPcmPlayer {
    pcmPlayerRef.current ??= new QualityPcmPlayer();
    return pcmPlayerRef.current;
  }

  const allJobs = useMemo(() => Object.values(jobs), [jobs]);
  const segmentJobs = useMemo(() => latestJobsBy(
    allJobs.filter((job) => job.kind === "quality_render" && job.status !== "cancelled"),
    (job) => job.segment_id && job.reference_id && job.reference_audio_url && job.quality_model
      ? `${job.segment_id}:${job.reference_id}:${job.reference_audio_url}:${job.quality_model}:${job.rvc_model_id ?? "none"}:${job.rvc_profile_fingerprint ?? "none"}`
      : null,
  ), [allJobs]);
  const referenceJobs = useMemo(() => latestJobsBy(allJobs.filter((job) => job.kind === "voxcpm_reference"), (job) => job.reference_id), [allJobs]);
  const bibleById = useMemo(() => new Map(preview?.character_voice_bible?.characters.map((character) => [character.character_id, character]) ?? []), [preview]);
  const references = useMemo<WorkbenchReference[]>(() => (preview?.reference_plan?.items ?? []).map((reference, index) => ({
    ...reference,
    color: COLORS[index % COLORS.length],
    tier: bibleById.get(reference.source_character_id)?.tier ?? "core",
  })), [bibleById, preview?.reference_plan?.items]);
  const referencesById = useMemo(() => new Map(references.map((reference) => [reference.reference_id, reference])), [references]);
  const availableVoiceReferences = useMemo(
    () => references.filter((reference) => reference.selection_mode === "narrator_default" || reference.selected),
    [references],
  );
  const allSegments = preview?.director_doc?.segments ?? [];
  const segments = selectedSliceId
    ? allSegments.filter((segment) => segment.analysis_batch_id === selectedSliceId)
    : allSegments;
  const segmentIndexById = useMemo(() => new Map(segments.map((segment, index) => [segment.segment_id, index])), [segments]);
  const chapters = useMemo<ChapterGroup[]>(() => {
    const groups: ChapterGroup[] = [];
    segments.forEach((segment, index) => {
      const chapterId = segment.chapter_id || "chapter-unknown";
      const current = groups.at(-1);
      if (current && current.chapterId === chapterId) {
        current.segments.push(segment);
        return;
      }
      groups.push({ chapterId, label: chapterLabel(chapterId, groups.length), startIndex: index, segments: [segment] });
    });
    return groups;
  }, [segments]);
  const activeChapter = chapters[Math.min(chapterIndex, Math.max(0, chapters.length - 1))] ?? null;
  const visibleSegments = activeChapter?.segments ?? [];
  const activeReference = references.find((reference) => reference.reference_id === activeReferenceId) ?? references[0] ?? null;
  const selectedSource = sources.find((source) => source.project_id === selectedProjectId) ?? null;
  const selectedModelRenderer = qualityModel === "indextts2" ? "indextts2" : "gpt_sovits";
  const pendingJobIds = useMemo(() => allJobs.filter(isPending).map((job) => job.job_id).sort().join(","), [allJobs]);

  useEffect(() => {
    setChapterIndex(0);
  }, [selectedProjectId, selectedSliceId]);

  function selectChapterForSegment(segmentIndex: number) {
    const nextChapterIndex = chapters.findIndex((chapter) => (
      segmentIndex >= chapter.startIndex && segmentIndex < chapter.startIndex + chapter.segments.length
    ));
    if (nextChapterIndex >= 0) setChapterIndex(nextChapterIndex);
  }

  function ownReferenceAudioUrl(reference: ReferencePlanItem | null): string | null {
    if (!reference) return null;
    if (reference.audio_url) return reference.audio_url;
    const referenceJob = referenceJobs.get(reference.reference_id);
    if (referenceJob?.status === "complete" && referenceJob.output_url) return referenceJob.output_url;
    return null;
  }

  function referenceAudioSource(reference: WorkbenchReference | null, visited = new Set<string>()): WorkbenchReference | null {
    if (!reference || visited.has(reference.reference_id)) return null;
    if (ownReferenceAudioUrl(reference)) return reference;
    if (!reference.reuse_reference_id) return null;
    visited.add(reference.reference_id);
    return referenceAudioSource(referencesById.get(reference.reuse_reference_id) ?? null, visited);
  }

  function referenceAudioUrl(reference: WorkbenchReference | null): string | null {
    return ownReferenceAudioUrl(referenceAudioSource(reference));
  }

  function voiceDecisionForSegment(segment: PreparedDirectorSegment) {
    const segmentIndex = segmentIndexById.get(segment.segment_id) ?? -1;
    const previousSegment = segmentIndex > 0 ? segments[segmentIndex - 1] : null;
    return resolveQualityVoice({
      segment,
      references,
      characters: preview?.character_voice_bible?.characters ?? [],
      narratorGender: productionSettings?.narrator_gender ?? "male",
      contextText: previousSegment?.segment_type === "narration" ? `${previousSegment.text}\n${segment.text}` : segment.text,
      isPlayableReference: (reference) => Boolean(referenceAudioUrl(reference)),
      hasDistinctAudio: (reference) => Boolean(ownReferenceAudioUrl(reference)),
    });
  }

  function referenceForSegment(segment: PreparedDirectorSegment): WorkbenchReference | null {
    const decision = voiceDecisionForSegment(segment);
    return decision.referenceId ? referencesById.get(decision.referenceId) ?? null : null;
  }

  function rvcModelIdForSegment(segment: PreparedDirectorSegment, reference = referenceForSegment(segment)): string | null {
    if (!rvcWorkspace?.settings.quality_stability_enabled || !reference) return null;
    let characterId = segment.character_id;
    if (characterId === "narrator") {
      characterId = reference.source_character_id === "narrator"
        ? `narrator-${reference.gender}`
        : reference.source_character_id;
    }
    const binding = rvcWorkspace.characters.find((item) => item.character_id === characterId);
    return binding?.stability_enabled && binding.quality_approved ? binding.selected_model_id : null;
  }

  function rvcProfileFingerprintForSegment(segment: PreparedDirectorSegment, reference = referenceForSegment(segment)): string | null {
    const modelId = rvcModelIdForSegment(segment, reference);
    if (!modelId) return null;
    return rvcWorkspace?.models.find((model) => model.model_id === modelId)?.profile_fingerprints.quality ?? null;
  }

  function jobForSegment(segment: PreparedDirectorSegment): AudioJob | undefined {
    const reference = referenceForSegment(segment);
    if (!reference) return undefined;
    const referenceUrl = referenceAudioUrl(reference);
    if (!referenceUrl) return undefined;
    return segmentJobs.get(`${segment.segment_id}:${reference.reference_id}:${referenceUrl}:${qualityModel}:${rvcModelIdForSegment(segment, reference) ?? "none"}:${rvcProfileFingerprintForSegment(segment, reference) ?? "none"}`);
  }

  function speakerForSegment(segment: PreparedDirectorSegment, reference: WorkbenchReference | null): string {
    const decision = voiceDecisionForSegment(segment);
    if (decision.reason === "extra_random") return reference ? `随机 · ${reference.display_name}` : "随机声线";
    if (decision.reason !== "character") return reference?.display_name ?? "旁白";
    return bibleById.get(segment.character_id)?.display_name ?? reference?.display_name ?? "旁白";
  }

  function automaticVoiceLabel(segment: PreparedDirectorSegment, reference: WorkbenchReference | null): string {
    if (segment.segment_type === "narration") {
      return `全局 · ${productionSettings?.narrator_gender === "female" ? "女旁白" : "男旁白"}`;
    }
    const decision = voiceDecisionForSegment(segment);
    return decision.reason === "extra_random"
      ? `随机 · ${reference?.display_name ?? "角色声线"}`
      : `自动 · ${reference?.display_name ?? bibleById.get(segment.character_id)?.display_name ?? "旁白"}`;
  }

  const completedSegments = segments.filter((segment) => {
    const job = jobForSegment(segment);
    return job?.status === "complete" && Boolean(job.output_url);
  });
  const completedJobIds = completedSegments.map((segment) => jobForSegment(segment)?.job_id).filter((jobId): jobId is string => Boolean(jobId));
  const completedCacheJobs = allJobs.filter((job) => job.kind === "quality_render" && job.status === "complete" && Boolean(job.output_url));
  const pendingCount = allJobs.filter(isPending).length;
  const failedCount = allJobs.filter((job) => job.status === "failed").length;

  function recordJobs(nextJobs: AudioJob[]) {
    const incoming = Object.fromEntries(nextJobs.map((job) => [job.job_id, job]));
    jobsRef.current = { ...jobsRef.current, ...incoming };
    setJobs((current) => ({ ...current, ...incoming }));
  }

  useEffect(() => {
    let active = true;
    fetchProductionSettings().then((settings) => {
      if (!active) return;
      setRenderOptions(settings.render_options);
      setLoudnessPolicy(settings.loudness_policy);
      setProductionSettings(settings);
    }).catch((error: unknown) => {
      if (active) {
        setLoadingProject(false);
        setOperationFeedback(errorMessage(error));
      }
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const readySources = sources.filter((source) => source.status === "director_ready");
    if (selectedProjectId) return;
    const next = readySources[0] ?? null;
    if (next) {
      onProjectChange(next.project_id);
    } else {
      setLoadingProject(false);
      setOperationFeedback("请先完成导演脚本阶段");
    }
  }, [onProjectChange, selectedProjectId, sources]);

  useEffect(() => {
    if (!selectedProjectId) return;
    streamSessionRef.current += 1;
    streamQueueRef.current = null;
    streamPlayingSegmentRef.current = null;
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    pcmPlayerRef.current?.stop();
    setStreamView(null);
    audioRef.current?.pause();
    setPlaying(null);
    let active = true;
    setLoadingProject(true);
    localStorage.setItem("zw-quality-project", selectedProjectId);
    Promise.all([
      fetchPreparationPreview(selectedProjectId),
      fetchAudioJobs({ projectId: selectedProjectId, limit: 5_000 }),
      fetchRvcWorkspace(selectedProjectId),
    ]).then(([nextPreview, persistedJobs, nextRvcWorkspace]) => {
      if (!active) return;
      setPreview(nextPreview);
      const restoredJobs = Object.fromEntries(persistedJobs.map((job) => [job.job_id, job]));
      jobsRef.current = restoredJobs;
      setJobs(restoredJobs);
      setRvcWorkspace(nextRvcWorkspace);
      setSelectedCacheJobIds(new Set());
      selectionManuallyEditedRef.current = false;
      setSelectedSegmentIds(new Set(nextPreview.director_doc?.segments.map((segment) => segment.segment_id) ?? []));
      setChapterIndex(0);
      setMergedAudio(null);
      setOperationFeedback(`${nextPreview.director_doc?.segments.length ?? 0} 句导演脚本已载入`);
    }).catch((error: unknown) => {
      if (active) setOperationFeedback(errorMessage(error));
    }).finally(() => {
      if (active) setLoadingProject(false);
    });
    return () => { active = false; };
  }, [selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId) {
      setContinuousRun(null);
      return;
    }
    let active = true;
    let unavailable = false;
    const poll = async () => {
      if (unavailable) return;
      try {
        const run = await fetchContinuousProduction(selectedProjectId);
        if (!active) return;
        setContinuousRun(run);
        const nextPreview = await fetchPreparationPreview(selectedProjectId);
        if (!active) return;
        setPreview((current) => {
          const currentCount = current?.director_doc?.segments.length ?? 0;
          const nextCount = nextPreview.director_doc?.segments.length ?? 0;
          return nextCount >= currentCount ? nextPreview : current;
        });
        setSelectedSegmentIds((current) => {
          if (selectionManuallyEditedRef.current) return current;
          const next = new Set(current);
          nextPreview.director_doc?.segments.forEach((segment) => next.add(segment.segment_id));
          return next;
        });
      } catch {
        unavailable = true;
        if (active) setContinuousRun(null);
      }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 1_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId) {
      setRvcWorkspace(null);
      return;
    }
    let active = true;
    const refresh = () => {
      void fetchRvcWorkspace(selectedProjectId).then((next) => {
        if (active) setRvcWorkspace(next);
      }).catch(() => undefined);
    };
    const timer = window.setInterval(refresh, 5_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [selectedProjectId]);

  useEffect(() => {
    setActiveReferenceId((current) => current && references.some((reference) => reference.reference_id === current) ? current : references[0]?.reference_id ?? null);
  }, [references.map((reference) => reference.reference_id).join(",")]);

  useEffect(() => {
    setVoicePromptDraft(activeReference?.voice_prompt ?? "");
  }, [activeReference?.reference_id, activeReference?.voice_prompt]);

  useEffect(() => {
    if (!pendingJobIds) return;
    const ids = pendingJobIds.split(",");
    let disposed = false;
    const poll = async () => {
      try {
        const refreshed = await Promise.all(ids.map(fetchAudioJob));
        if (disposed) return;
        recordJobs(refreshed);
        const latest = refreshed.find((job) => job.status === "failed") ?? refreshed.at(-1);
        if (latest) setOperationFeedback(jobLabel(latest, latest.message));
        if (selectedProjectId && refreshed.some((job) => job.kind === "voxcpm_reference" && job.status === "complete")) {
          setPreview(await fetchPreparationPreview(selectedProjectId));
        }
      } catch (error) {
        if (!disposed) setOperationFeedback(errorMessage(error));
      }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 1_000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [pendingJobIds, selectedProjectId]);

  useEffect(() => {
    const queue = streamQueueRef.current;
    if (queue) void pumpStreamQueue(queue.sessionId);
  }, [jobs]);

  useEffect(() => {
    if (!streamView) return;
    const frame = window.requestAnimationFrame(() => {
      document.getElementById(`quality-segment-${streamView.currentSegmentId}`)?.scrollIntoView({ block: "center" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [streamView?.currentSegmentId]);

  useEffect(() => {
    if (!segmentLocation) return;
    const frame = window.requestAnimationFrame(() => {
      const prefix = segmentLocation.target === "script" ? "quality-segment" : "quality-result";
      document.getElementById(`${prefix}-${segmentLocation.segmentId}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [segmentLocation, chapterIndex]);

  useEffect(() => {
    setChapterIndex((current) => Math.min(current, Math.max(0, chapters.length - 1)));
  }, [chapters.length]);

  useEffect(() => {
    if (!continuousRun?.settings.auto_play || !segments.length || autoPlayedRunRef.current === continuousRun.run_id) return;
    const firstReadySlice = continuousRun.slices.find((slice) => ["render_ready", "rendering", "playing", "complete"].includes(slice.state));
    const firstIndex = firstReadySlice ? segments.findIndex((segment) => segment.analysis_batch_id === firstReadySlice.slice_id) : -1;
    if (firstIndex < 0) return;
    autoPlayedRunRef.current = continuousRun.run_id;
    void startStreamingFrom(firstIndex, "reuse");
  }, [continuousRun?.run_id, continuousRun?.settings.auto_play, continuousRun?.slices, segments.length]);

  useEffect(() => () => {
    streamSessionRef.current += 1;
    streamQueueRef.current = null;
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    void pcmPlayerRef.current?.dispose();
    audioRef.current?.pause();
    recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
    if (recordingTimerRef.current !== null) window.clearInterval(recordingTimerRef.current);
    if (locationTimerRef.current !== null) window.clearTimeout(locationTimerRef.current);
    if (segmentClickTimerRef.current !== null) window.clearTimeout(segmentClickTimerRef.current);
  }, []);

  useEffect(() => {
    if (recordingSeconds >= 120 && recorderRef.current?.state === "recording") recorderRef.current.stop();
  }, [recordingSeconds]);

  function toggleAudio(audioId: string, audioUrl: string | null) {
    const audio = audioRef.current;
    if (!audio || !audioUrl) {
      setOperationFeedback("暂无可播放音频");
      return;
    }
    if (streamQueueRef.current) {
      streamSessionRef.current += 1;
      streamQueueRef.current = null;
      streamPlayingSegmentRef.current = null;
      streamAbortRef.current?.abort();
      streamAbortRef.current = null;
      pcmPlayerRef.current?.stop();
      setStreamView(null);
    }
    if (playing === audioId && !audio.paused) {
      audio.pause();
      setPlaying(null);
      return;
    }
    audio.pause();
    audio.src = audioUrl;
    audio.currentTime = 0;
    setPlaying(audioId);
    audio.play().catch((error: unknown) => {
      setPlaying(null);
      setOperationFeedback(errorMessage(error));
    });
  }

  function updateStreamView(queue: StreamQueue, status: StreamPlaybackView["status"]) {
    const current = queue.targets[queue.playIndex];
    if (!current) return;
    setStreamView({
      sessionId: queue.sessionId,
      currentIndex: queue.startIndex + queue.playIndex,
      currentSegmentId: current.segment_id,
      remaining: queue.targets.length - queue.playIndex,
      status,
    });
  }

  function finishPcmStreamQueue(queue: StreamQueue) {
    if (streamQueueRef.current?.sessionId !== queue.sessionId) return;
    streamQueueRef.current = null;
    streamPlayingSegmentRef.current = null;
    setStreamView(null);
    setPlaying(null);
    setOperationFeedback("GPT-SoVITS V3/V4 流式队列已播放完成");
  }

  function schedulePcmSegmentStart(
    player: QualityPcmPlayer,
    queue: StreamQueue,
    segment: PreparedDirectorSegment,
    segmentIndex: number,
    startTime: number,
  ) {
    player.scheduleBoundary(startTime, () => {
      if (streamQueueRef.current?.sessionId !== queue.sessionId || streamSessionRef.current !== queue.sessionId) return;
      queue.playIndex = segmentIndex;
      streamPlayingSegmentRef.current = segment.segment_id;
      selectChapterForSegment(queue.startIndex + segmentIndex);
      setPlaying(`stream:${segment.segment_id}`);
      updateStreamView(queue, queue.paused ? "paused" : "playing");
      setOperationFeedback(`正在流播第 ${queue.startIndex + segmentIndex + 1} 句，后台继续加载后续句`);
      void pumpStreamQueue(queue.sessionId);
    });
  }

  function schedulePcmSegmentEnd(
    player: QualityPcmPlayer,
    queue: StreamQueue,
    segmentIndex: number,
    jobId: string,
    endTime: number,
  ) {
    player.scheduleBoundary(endTime, () => {
      if (streamQueueRef.current?.sessionId !== queue.sessionId || streamSessionRef.current !== queue.sessionId) return;
      queue.lastFinishedIndex = Math.max(queue.lastFinishedIndex, segmentIndex);
      queue.playedJobIds.push(jobId);
      autoDeletePlayedCache(queue);
      if (queue.generationComplete && queue.lastFinishedIndex >= queue.lastScheduledIndex) {
        finishPcmStreamQueue(queue);
      }
    });
  }

  async function pumpGptSovitsStreamQueue(sessionId: number): Promise<void> {
    const player = pcmPlayer();
    while (true) {
      const queue = streamQueueRef.current;
      if (!queue || queue.sessionId !== sessionId || streamSessionRef.current !== sessionId) return;
      if (queue.paused) {
        updateStreamView(queue, "paused");
        return;
      }
      if (queue.submitIndex >= queue.targets.length) {
        queue.generationComplete = true;
        player.finishStream();
        if (queue.lastScheduledIndex < 0 || queue.lastFinishedIndex >= queue.lastScheduledIndex) {
          finishPcmStreamQueue(queue);
        }
        return;
      }
      const bufferEnd = Math.min(
        queue.targets.length,
        queue.playIndex + STREAM_LOOKAHEAD_SENTENCES + 1,
      );
      if (queue.submitIndex >= bufferEnd) return;

      const segmentIndex = queue.submitIndex;
      const current = queue.targets[segmentIndex];
      const cachedJobId = queue.jobIds.get(current.segment_id);
      const cachedJob = cachedJobId ? jobsRef.current[cachedJobId] : undefined;
      if (cachedJob?.status === "failed" || cachedJob?.status === "cancelled") {
        queue.skipped.add(current.segment_id);
        queue.submitIndex += 1;
        if (segmentIndex === queue.playIndex) queue.playIndex += 1;
        continue;
      }
      if (cachedJob && cachedJob.status !== "complete") {
        if (!streamPlayingSegmentRef.current) updateStreamView(queue, "buffering");
        setOperationFeedback(`正在等待第 ${queue.startIndex + segmentIndex + 1} 句现有任务，播放队列继续运行`);
        return;
      }
      if (cachedJob?.output_url) {
        try {
          const response = await fetch(cachedJob.output_url, { cache: "no-store" });
          if (!response.ok) throw new Error(`第 ${queue.startIndex + segmentIndex + 1} 句缓存读取失败`);
          const startTime = await player.appendWav(await response.arrayBuffer());
          if (startTime === null) throw new Error("PCM 播放器未就绪");
          schedulePcmSegmentStart(player, queue, current, segmentIndex, startTime);
          queue.lastScheduledIndex = segmentIndex;
          schedulePcmSegmentEnd(player, queue, segmentIndex, cachedJob.job_id, player.queuedEndTime);
          queue.submitIndex += 1;
          continue;
        } catch (error) {
          player.stop();
          streamQueueRef.current = null;
          streamPlayingSegmentRef.current = null;
          setPlaying(null);
          setStreamView(null);
          setOperationFeedback(`第 ${queue.startIndex + segmentIndex + 1} 句缓存加载失败：${errorMessage(error)}`);
          return;
        }
      }

      const controller = new AbortController();
      streamAbortRef.current = controller;
      if (!streamPlayingSegmentRef.current) updateStreamView(queue, "buffering");
      setOperationFeedback(`正在加载第 ${queue.startIndex + segmentIndex + 1} 句，已生成音频继续播放`);
      try {
        const opened = await openQualityAudioStream(qualityRequestForSegment(current), controller.signal);
        queue.jobIds.set(current.segment_id, opened.jobId);
        void fetchAudioJob(opened.jobId).then((job) => recordJobs([job])).catch(() => undefined);
        let startTime: number | null = null;
        const metadata = await consumeQualityPcmStream(opened.response, controller.signal, {
          onMetadata: (streamMetadata) => {
            if (streamMetadata.job_id !== opened.jobId) throw new Error("流式任务标识不一致");
          },
          onAudio: (streamMetadata, audio) => {
            if (streamSessionRef.current !== sessionId) {
              controller.abort();
              return;
            }
            const chunkStartTime = player.append(streamMetadata, audio);
            if (startTime === null && chunkStartTime !== null) {
              startTime = chunkStartTime;
              schedulePcmSegmentStart(player, queue, current, segmentIndex, startTime);
            }
          },
        });
        if (metadata.job_id !== opened.jobId) throw new Error("流式响应任务标识不一致");
        if (startTime === null) throw new Error("流式任务没有返回可播放音频");
        const completed = await fetchAudioJob(opened.jobId);
        recordJobs([completed]);
        if (completed.status !== "complete" || !completed.output_url) {
          throw new Error(completed.error ?? "流式任务未生成可复用缓存");
        }
        if (streamSessionRef.current !== sessionId || streamQueueRef.current?.sessionId !== sessionId) return;
        queue.lastScheduledIndex = segmentIndex;
        schedulePcmSegmentEnd(player, queue, segmentIndex, opened.jobId, player.queuedEndTime);
        queue.submitIndex += 1;
      } catch (error) {
        controller.abort();
        player.stop();
        if (isAbortError(error) || streamSessionRef.current !== sessionId) return;
        const failedJobId = queue.jobIds.get(current.segment_id);
        if (failedJobId) {
          void fetchAudioJob(failedJobId).then((job) => recordJobs([job])).catch(() => undefined);
        }
        streamQueueRef.current = null;
        streamPlayingSegmentRef.current = null;
        setPlaying(null);
        setStreamView(null);
        setOperationFeedback(`第 ${queue.startIndex + segmentIndex + 1} 句流式生成失败：${errorMessage(error)}`);
        return;
      } finally {
        if (streamAbortRef.current === controller) streamAbortRef.current = null;
      }
    }
  }

  async function pumpStreamQueue(sessionId: number) {
    if (streamPumpingRef.current) {
      streamPumpRequestedRef.current = sessionId;
      return;
    }
    streamPumpingRef.current = true;
    try {
      if (v3V4Streaming) {
        await pumpGptSovitsStreamQueue(sessionId);
        return;
      }
      while (true) {
        const queue = streamQueueRef.current;
        if (!queue || queue.sessionId !== sessionId || streamSessionRef.current !== sessionId) return;

        const bufferEnd = Math.min(
          queue.targets.length,
          queue.playIndex + STREAM_LOOKAHEAD_SENTENCES + 1,
        );
        while (queue.submitIndex < bufferEnd) {
          const segment = queue.targets[queue.submitIndex];
          queue.submitIndex += 1;
          if (queue.jobIds.has(segment.segment_id)) continue;
          if (!streamPlayingSegmentRef.current) updateStreamView(queue, "preparing");
          const submission = submitSegment(segment);
          streamSubmissionRef.current = submission;
          try {
            const job = await submission;
            if (streamSessionRef.current !== sessionId || streamQueueRef.current?.sessionId !== sessionId) return;
            queue.jobIds.set(segment.segment_id, job.job_id);
          } catch (error) {
            queue.skipped.add(segment.segment_id);
            setOperationFeedback(`第 ${queue.startIndex + queue.submitIndex} 句提交失败：${errorMessage(error)}`);
          } finally {
            if (streamSubmissionRef.current === submission) streamSubmissionRef.current = null;
          }
        }

        const current = queue.targets[queue.playIndex];
        if (!current) {
          streamQueueRef.current = null;
          streamPlayingSegmentRef.current = null;
          setStreamView(null);
          setPlaying(null);
          setOperationFeedback("流式队列已播放完成");
          return;
        }
        if (streamPlayingSegmentRef.current === current.segment_id) return;
        if (queue.skipped.has(current.segment_id)) {
          queue.playIndex += 1;
          continue;
        }

        if (queue.paused) {
          updateStreamView(queue, "paused");
          return;
        }

        const jobId = queue.jobIds.get(current.segment_id);
        const job = jobId ? jobsRef.current[jobId] : undefined;
        if (job?.status === "failed" || job?.status === "cancelled") {
          queue.skipped.add(current.segment_id);
          queue.playIndex += 1;
          setOperationFeedback(`第 ${queue.startIndex + queue.playIndex} 句未生成，继续下一句`);
          continue;
        }
        if (job?.status !== "complete" || !job.output_url) {
          updateStreamView(queue, "buffering");
          setOperationFeedback(`流式缓冲中 · 等待第 ${queue.startIndex + queue.playIndex + 1} 句生成`);
          return;
        }

        const audio = audioRef.current;
        if (!audio) return;
        const audioId = `stream:${current.segment_id}`;
        streamPlayingSegmentRef.current = current.segment_id;
        selectChapterForSegment(queue.startIndex + queue.playIndex);
        audio.pause();
        audio.src = job.output_url;
        audio.currentTime = 0;
        setPlaying(audioId);
        updateStreamView(queue, "playing");
        setOperationFeedback(`流式播放第 ${queue.startIndex + queue.playIndex + 1} 句 · 剩余 ${queue.targets.length - queue.playIndex - 1} 句`);
        try {
          await audio.play();
        } catch (error) {
          queue.paused = true;
          setPlaying(null);
          updateStreamView(queue, "paused");
          setOperationFeedback(`浏览器等待播放授权：${errorMessage(error)}`);
        }
        return;
      }
    } finally {
      streamPumpingRef.current = false;
      const requestedSession = streamPumpRequestedRef.current;
      streamPumpRequestedRef.current = null;
      if (requestedSession !== null && streamQueueRef.current?.sessionId === requestedSession) {
        void pumpStreamQueue(requestedSession);
      }
    }
  }

  async function startStreamingFrom(startIndex: number, mode: "reuse" | "restart") {
    if (!selectedProjectId || startIndex < 0 || startIndex >= segments.length) return;
    batchGenerationRef.current += 1;
    const sessionId = streamSessionRef.current + 1;
    streamSessionRef.current = sessionId;
    streamQueueRef.current = null;
    streamPlayingSegmentRef.current = null;
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    pcmPlayerRef.current?.stop();
    audioRef.current?.pause();
    setPlaying(null);
    setSubmittingBatch(false);
    setStreamView({
      sessionId,
      currentIndex: startIndex,
      currentSegmentId: segments[startIndex].segment_id,
      remaining: segments.length - startIndex,
      status: "preparing",
    });
    setOperationFeedback(mode === "restart" ? "正在停止旧批次并取消原质量渲染队列" : "正在检查现有缓存并续接流播队列");

    try {
      await batchSubmissionRef.current?.catch(() => undefined);
      await streamSubmissionRef.current?.catch(() => undefined);
      if (streamSessionRef.current !== sessionId) return;
      let cancelledCount = 0;
      if (mode === "restart") {
        const cancellation = await cancelQualityQueue(selectedProjectId);
        cancelledCount = cancellation.cancelled_count;
        recordJobs(cancellation.cancelled_jobs);
        if (streamSessionRef.current !== sessionId) return;
      }
      const targets = segments.slice(startIndex);
      const reusableJobs = mode === "reuse" ? collectReusableQualityJobs({
        segments: targets,
        jobs: Object.values(jobsRef.current),
        qualityModel,
        referenceIdForSegment: (segment) => voiceDecisionForSegment(segment).referenceId,
        referenceAudioUrlForSegment: (segment) => referenceAudioUrl(referenceForSegment(segment)),
        rvcModelIdForSegment: (segment) => rvcModelIdForSegment(segment),
        rvcProfileFingerprintForSegment: (segment) => rvcProfileFingerprintForSegment(segment),
      }) : new Map<string, string>();
      const queue: StreamQueue = {
        sessionId,
        targets,
        startIndex,
        playIndex: 0,
        submitIndex: 0,
        jobIds: reusableJobs,
        skipped: new Set(),
        playedJobIds: [],
        paused: false,
        generationComplete: false,
        lastScheduledIndex: -1,
        lastFinishedIndex: -1,
      };
      if (v3V4Streaming) await pcmPlayer().begin();
      streamQueueRef.current = queue;
      updateStreamView(queue, "preparing");
      setOperationFeedback(mode === "restart"
        ? `已取消 ${cancelledCount} 个旧任务，从第 ${startIndex + 1} 句重建流播队列`
        : `从第 ${startIndex + 1} 句继续流播，复用 ${reusableJobs.size} 条现有缓存或任务`);
      void pumpStreamQueue(sessionId);
    } catch (error) {
      if (streamSessionRef.current !== sessionId) return;
      streamQueueRef.current = null;
      setStreamView(null);
      setOperationFeedback(errorMessage(error));
    }
  }

  async function stopStreamQueue() {
    const projectId = selectedProjectId;
    streamSessionRef.current += 1;
    streamQueueRef.current = null;
    streamPlayingSegmentRef.current = null;
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    pcmPlayerRef.current?.stop();
    audioRef.current?.pause();
    setPlaying(null);
    setStreamView(null);
    if (!projectId) return;
    await streamSubmissionRef.current?.catch(() => undefined);
    try {
      const cancellation = await cancelQualityQueue(projectId);
      recordJobs(cancellation.cancelled_jobs);
      setOperationFeedback(`流式播放已停止，取消 ${cancellation.cancelled_count} 个待处理任务`);
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    }
  }

  async function toggleStreamPlayback() {
    const queue = streamQueueRef.current;
    const segmentId = streamPlayingSegmentRef.current;
    const audio = audioRef.current;
    if (!queue || !audio) return;
    const pcm = pcmPlayerRef.current;
    if (v3V4Streaming && pcm?.isActive && segmentId) {
      if (!queue.paused) {
        queue.paused = true;
        await pcm.pause();
        setPlaying(null);
        updateStreamView(queue, "paused");
        setOperationFeedback("GPT-SoVITS 流式播放已暂停");
      } else {
        queue.paused = false;
        await pcm.resume();
        setPlaying(`stream:${segmentId}`);
        updateStreamView(queue, "playing");
        setOperationFeedback(`继续 GPT-SoVITS 流式播放第 ${queue.startIndex + queue.playIndex + 1} 句`);
      }
      return;
    }
    if (!queue.paused && segmentId && !audio.paused) {
      queue.paused = true;
      audio.pause();
      setPlaying(null);
      updateStreamView(queue, "paused");
      setOperationFeedback("流式播放已暂停");
      return;
    }
    queue.paused = false;
    if (!segmentId) {
      updateStreamView(queue, "buffering");
      setOperationFeedback(`继续缓冲第 ${queue.startIndex + queue.playIndex + 1} 句`);
      void pumpStreamQueue(queue.sessionId);
      return;
    }
    try {
      await audio.play();
      setPlaying(`stream:${segmentId}`);
      updateStreamView(queue, "playing");
      setOperationFeedback(`继续流式播放第 ${queue.startIndex + queue.playIndex + 1} 句`);
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    }
  }

  function pauseStreamForSinglePreview() {
    const queue = streamQueueRef.current;
    const segmentId = streamPlayingSegmentRef.current;
    const audio = audioRef.current;
    if (!queue || !audio) return;
    queue.paused = true;
    if (v3V4Streaming && pcmPlayerRef.current?.isActive) void pcmPlayerRef.current.pause();
    if (segmentId && !audio.paused) audio.pause();
    setPlaying(null);
    updateStreamView(queue, "paused");
    setOperationFeedback("流播已暂停，右侧仅试听当前单句");
  }

  function locateSegment(segmentId: string, target: SegmentLocation["target"]) {
    const index = segments.findIndex((segment) => segment.segment_id === segmentId);
    if (index < 0) return;
    selectChapterForSegment(index);
    setSegmentLocation({ segmentId, target });
    if (locationTimerRef.current !== null) window.clearTimeout(locationTimerRef.current);
    locationTimerRef.current = window.setTimeout(() => {
      setSegmentLocation((current) => current?.segmentId === segmentId && current.target === target ? null : current);
      locationTimerRef.current = null;
    }, 1_500);
  }

  function locateResultSegment(segmentId: string) {
    locateSegment(segmentId, "result");
  }

  function locateScriptSegment(segmentId: string) {
    locateSegment(segmentId, "script");
  }

  function locateProductionSlice(sliceId: string) {
    onSelectedSliceChange(sliceId);
    const segment = allSegments.find((item) => item.analysis_batch_id === sliceId);
    if (segment) {
      window.setTimeout(() => locateScriptSegment(segment.segment_id), 0);
    }
  }

  async function removeCacheJobs(jobIds: string[], automatic = false) {
    if (!selectedProjectId || !jobIds.length) return;
    if (!automatic) setCacheBusy(true);
    try {
      const deleted = await deleteQualityCache(selectedProjectId, jobIds);
      recordJobs(deleted.deleted_jobs);
      setSelectedCacheJobIds((current) => {
        const next = new Set(current);
        deleted.deleted_jobs.forEach((job) => next.delete(job.job_id));
        return next;
      });
      setMergedAudio(null);
      if (!automatic) {
        const megabytes = deleted.deleted_bytes / 1024 / 1024;
        setOperationFeedback(`已删除 ${deleted.deleted_count} 条音频缓存 · ${megabytes.toFixed(1)} MB`);
      }
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    } finally {
      if (!automatic) setCacheBusy(false);
    }
  }

  async function reprocessCacheLoudness(jobIds: string[]) {
    if (!selectedProjectId || !jobIds.length) return;
    setCacheBusy(true);
    try {
      const updated = await reprocessQualityLoudness(selectedProjectId, jobIds);
      recordJobs(updated);
      setMergedAudio(null);
      setOperationFeedback(`已重新统一 ${updated.length} 条缓存的节目响度`);
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    } finally {
      setCacheBusy(false);
    }
  }

  async function reprocessCacheRvc(jobIds: string[]) {
    if (!selectedProjectId || !jobIds.length) return;
    setCacheBusy(true);
    try {
      const updated = await reprocessQualityRvc(selectedProjectId, jobIds);
      recordJobs(updated);
      setMergedAudio(null);
      const applied = updated.filter((job) => job.rvc_status === "applied").length;
      const fallback = updated.filter((job) => job.rvc_status === "fallback").length;
      setOperationFeedback(`已从基础渲染处理 ${updated.length} 条缓存 · RVC 生效 ${applied}${fallback ? ` · 回退 ${fallback}` : ""}`);
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    } finally {
      setCacheBusy(false);
    }
  }

  function autoDeletePlayedCache(queue: StreamQueue) {
    if (!productionSettings?.auto_delete_played_cache) return;
    const overflow = queue.playedJobIds.length - productionSettings.cache_keep_sentences;
    if (overflow <= 0) return;
    const expiredJobIds = queue.playedJobIds.splice(0, overflow);
    void removeCacheJobs(expiredJobIds, true);
  }

  function handleAudioEnded() {
    const queue = streamQueueRef.current;
    const segmentId = streamPlayingSegmentRef.current;
    if (!queue || !segmentId || queue.targets[queue.playIndex]?.segment_id !== segmentId) {
      setPlaying(null);
      return;
    }
    const completedJobId = queue.jobIds.get(segmentId);
    if (completedJobId) queue.playedJobIds.push(completedJobId);
    streamPlayingSegmentRef.current = null;
    setPlaying(null);
    queue.playIndex += 1;
    void pumpStreamQueue(queue.sessionId);
    autoDeletePlayedCache(queue);
  }

  function handleAudioError() {
    const queue = streamQueueRef.current;
    const segmentId = streamPlayingSegmentRef.current;
    if (!queue || !segmentId || queue.targets[queue.playIndex]?.segment_id !== segmentId) {
      setPlaying(null);
      setOperationFeedback("音频加载失败");
      return;
    }
    queue.skipped.add(segmentId);
    queue.playIndex += 1;
    streamPlayingSegmentRef.current = null;
    setPlaying(null);
    setOperationFeedback("当前句音频加载失败，继续下一句");
    void pumpStreamQueue(queue.sessionId);
  }

  function qualityRequestForSegment(segment: PreparedDirectorSegment): AudioJobRequest {
    if (!selectedProjectId) throw new Error("未选择项目");
    const reference = referenceForSegment(segment);
    const audioSource = referenceAudioSource(reference);
    const referenceUrl = ownReferenceAudioUrl(audioSource);
    if (!referenceUrl) throw new Error(`${speakerForSegment(segment, reference)} 尚无参考音频`);
    return {
      kind: "quality_render",
      project_id: selectedProjectId,
      reference_id: reference?.reference_id,
      segment_id: segment.segment_id,
      character_id: reference?.source_character_id ?? segment.character_id,
      text: segment.text,
      reference_audio_url: referenceUrl,
      reference_text: v3V4Streaming ? audioSource?.reference_text : undefined,
      quality_model: qualityModel,
      emotion_description: `${segment.direction.emotion}，${segment.direction.tone}，强度 ${segment.direction.emotion_intensity.toFixed(2)}`,
      render_options: renderOptions,
    };
  }

  async function submitSegment(segment: PreparedDirectorSegment): Promise<AudioJob> {
    const job = await createAudioJob(qualityRequestForSegment(segment));
    recordJobs([job]);
    setMergedAudio(null);
    return job;
  }

  async function regenerateSegment(segment: PreparedDirectorSegment) {
    setOperationFeedback(`正在提交 ${segment.segment_id}`);
    try {
      const job = await submitSegment(segment);
      setOperationFeedback(job.message);
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    }
  }

  async function renderSelected() {
    if (streamQueueRef.current) await stopStreamQueue();
    const selected = segments.filter((segment) => selectedSegmentIds.has(segment.segment_id));
    if (!selected.length) {
      setOperationFeedback("请至少选择一句导演脚本");
      return;
    }
    const generation = batchGenerationRef.current + 1;
    batchGenerationRef.current = generation;
    const submission = (async () => {
      setSubmittingBatch(true);
      let submitted = 0;
      let failed = 0;
      for (const segment of selected) {
        if (batchGenerationRef.current !== generation) return;
        try {
          await submitSegment(segment);
          submitted += 1;
        } catch {
          failed += 1;
        }
        if (batchGenerationRef.current !== generation) return;
        setOperationFeedback(`正在提交 ${submitted + failed} / ${selected.length}`);
      }
      setOperationFeedback(`${submitted} 个任务已进入串行 GPU 队列${failed ? `，${failed} 个缺少参考音频` : ""}`);
    })();
    batchSubmissionRef.current = submission;
    try {
      await submission;
    } finally {
      if (batchSubmissionRef.current === submission) batchSubmissionRef.current = null;
      if (batchGenerationRef.current === generation) setSubmittingBatch(false);
    }
  }

  async function regenerateReference() {
    if (!selectedProjectId || !activeReference) return;
    setReferenceBusy(true);
    try {
      const job = await createAudioJob({
        kind: "voxcpm_reference",
        project_id: selectedProjectId,
        reference_id: activeReference.reference_id,
        character_id: activeReference.source_character_id,
        text: activeReference.reference_text,
        voice_prompt: voicePromptDraft.trim() || activeReference.voice_prompt,
      });
      recordJobs([job]);
      setOperationFeedback(job.message);
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    } finally {
      setReferenceBusy(false);
    }
  }

  async function saveVoicePrompt() {
    if (!selectedProjectId || !activeReference || !voicePromptDraft.trim()) return;
    setReferenceBusy(true);
    try {
      setPreview(await updateReferenceVoicePrompt(selectedProjectId, activeReference.reference_id, voicePromptDraft.trim()));
      setOperationFeedback("声线描述已保存");
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    } finally {
      setReferenceBusy(false);
    }
  }

  async function saveReferenceAudio(source: Blob, fileName: string, origin: "uploaded" | "recorded", referenceId: string) {
    if (!selectedProjectId) return;
    setReferenceBusy(true);
    try {
      setOperationFeedback(origin === "recorded" ? "正在处理录音" : "正在处理参考音频");
      const wav = await normalizeAudioToWav(source, fileName);
      setPreview(await uploadReferenceAudio(selectedProjectId, referenceId, wav, origin));
      setOperationFeedback(origin === "recorded" ? "录音已设为角色参考" : "参考音频已上传");
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    } finally {
      setReferenceBusy(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function startRecording() {
    if (!activeReference || !navigator.mediaDevices?.getUserMedia) {
      setOperationFeedback("当前浏览器不支持录音");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      const referenceId = activeReference.reference_id;
      recordingStreamRef.current = stream;
      recorderRef.current = recorder;
      recordingChunksRef.current = [];
      recorder.ondataavailable = (event) => { if (event.data.size) recordingChunksRef.current.push(event.data); };
      recorder.onstop = () => {
        const blob = new Blob(recordingChunksRef.current, { type: recorder.mimeType || "audio/webm" });
        stream.getTracks().forEach((track) => track.stop());
        recordingStreamRef.current = null;
        setRecording(false);
        if (recordingTimerRef.current !== null) window.clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
        void saveReferenceAudio(blob, `${referenceId}-recording.webm`, "recorded", referenceId);
      };
      recorder.start(250);
      setRecording(true);
      setRecordingSeconds(0);
      recordingTimerRef.current = window.setInterval(() => setRecordingSeconds((current) => current + 1), 1_000);
      setOperationFeedback("正在录制参考音频");
    } catch (error) {
      setOperationFeedback(error instanceof DOMException && error.name === "NotAllowedError" ? "麦克风权限未授权" : errorMessage(error));
    }
  }

  function stopRecording() {
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  }

  async function saveProductionPreference(settings: {
    narrator_gender?: "male" | "female";
    auto_delete_played_cache?: boolean;
    cache_keep_sentences?: number;
  }) {
    try {
      const updated = await updateProductionPreferences(settings);
      setProductionSettings(updated);
      setMergedAudio(null);
      setOperationFeedback("全局旁白与缓存策略已保存");
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    }
  }

  async function saveSegmentVoice(segment: PreparedDirectorSegment, voiceReferenceId: string | null) {
    if (!selectedProjectId) return;
    setSegmentVoiceBusy(segment.segment_id);
    try {
      setPreview(await updateDirectorSegmentVoice(selectedProjectId, segment.segment_id, voiceReferenceId));
      setMergedAudio(null);
      setOperationFeedback(voiceReferenceId ? "当前句已改用指定角色声线" : "当前句已恢复自动匹配角色声线");
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    } finally {
      setSegmentVoiceBusy(null);
    }
  }

  async function saveAdvancedSettings() {
    setSavingSettings(true);
    try {
      const settings = await updateProductionPreferences({ render_options: renderOptions, loudness_policy: loudnessPolicy });
      setRenderOptions(settings.render_options);
      setLoudnessPolicy(settings.loudness_policy);
      setProductionSettings(settings);
      setShowAdvancedSettings(false);
      setOperationFeedback("高级渲染设置已保存");
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    } finally {
      setSavingSettings(false);
    }
  }

  async function mergeCompleted(playAfterMerge = false) {
    if (!selectedProjectId || !completedJobIds.length) return;
    setOperationFeedback("正在按导演脚本顺序合并音频");
    try {
      const merged = await mergeQualityAudio(selectedProjectId, completedJobIds);
      setMergedAudio(merged);
      setOperationFeedback(`已合并 ${merged.segment_count} 句，时长 ${merged.duration_seconds.toFixed(1)} 秒`);
      if (playAfterMerge) toggleAudio("merged", merged.output_url);
    } catch (error) {
      setOperationFeedback(errorMessage(error));
    }
  }

  const activeReferenceUrl = referenceAudioUrl(activeReference);
  const activeReferenceJob = activeReference ? referenceJobs.get(activeReference.reference_id) : undefined;
  const activeEmotionItems = (preview?.emotion_plan?.items ?? []).filter((item) => item.parent_reference_id === activeReference?.reference_id && item.selected);
  const allSegmentsSelected = segments.length > 0 && segments.every((segment) => selectedSegmentIds.has(segment.segment_id));
  const allCacheSelected = completedCacheJobs.length > 0 && completedCacheJobs.every((job) => selectedCacheJobIds.has(job.job_id));

  return (
    <section className="workspace-grid quality-workbench">
      <audio ref={audioRef} className="audio-preview" aria-hidden="true" preload="metadata" onEnded={handleAudioEnded} onError={handleAudioError} />
      <aside className="cast-pane">
        <div className="pane-heading">
          <div><span className="eyebrow">VOICE CAST</span><h2>角色声线</h2></div>
          <span className="list-count">{references.length}</span>
        </div>
        <div className="cast-list quality-cast-list">
          {references.map((reference) => (
            <button key={reference.reference_id} className={`cast-item ${activeReference?.reference_id === reference.reference_id ? "selected" : ""}`} onClick={() => setActiveReferenceId(reference.reference_id)}>
              <span className={`cast-dot cast-dot--${reference.color}`} />
              <span className="cast-copy"><strong>{reference.display_name}</strong><small>{reference.tier === "core" ? "核心角色" : "配角"} · 权重 {Math.round(reference.importance * 100)}</small></span>
              <span className={`status status--${referenceAudioUrl(reference) ? "accepted" : "pending"}`}>{referenceAudioUrl(reference) ? "可用" : reference.selected ? "待参考" : "未启用"}</span>
            </button>
          ))}
          {!references.length && <p className="empty-state">当前项目尚无角色参考计划</p>}
        </div>

        {activeReference && (
          <section className="voice-editor quality-voice-editor">
            <div className="section-title"><WandSparkles size={16} /><h3>{activeReference.display_name} · 声线设计</h3></div>
            <textarea value={voicePromptDraft} disabled={referenceBusy} aria-label="声线描述" onChange={(event) => setVoicePromptDraft(event.target.value)} />
            <div className="voice-prompt-actions">
              <small>{voicePromptDraft.length} / 1000</small>
              <button className="text-button" disabled={referenceBusy || !voicePromptDraft.trim() || voicePromptDraft.trim() === activeReference.voice_prompt} onClick={() => void saveVoicePrompt()}><Save size={13} />保存描述</button>
            </div>
            <div className="reference-toolbar"><span>参考来源 · {activeReference.audio_source === "recorded" ? "用户录音" : activeReference.audio_source === "uploaded" ? "用户上传" : activeReference.audio_source === "reused" ? "历史复用" : activeReferenceJob ? "VoxCPM2" : activeReference.reuse_reference_id && !activeReference.audio_url ? "复用参考" : "VoxCPM2"}</span><button className="text-button" disabled={referenceBusy || isPending(activeReferenceJob)} onClick={() => void regenerateReference()}>{isPending(activeReferenceJob) ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}生成参考</button></div>
            <div className="reference-capture-actions">
              <input ref={fileInputRef} className="hidden-file-input" type="file" accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.webm" aria-hidden="true" tabIndex={-1} onChange={(event) => { const file = event.target.files?.[0]; if (file && activeReference) void saveReferenceAudio(file, file.name, "uploaded", activeReference.reference_id); }} />
              <button className="secondary-button" disabled={referenceBusy || recording} onClick={() => fileInputRef.current?.click()}><Upload size={14} />上传音频</button>
              <button className={`secondary-button record-button ${recording ? "recording" : ""}`} disabled={referenceBusy} onClick={() => recording ? stopRecording() : void startRecording()}>{recording ? <CircleStop size={14} /> : <Mic size={14} />}{recording ? `停止 ${Math.floor(recordingSeconds / 60)}:${String(recordingSeconds % 60).padStart(2, "0")}` : "录制参考"}</button>
            </div>
            {activeReferenceJob && <div className={`job-progress job-progress--${activeReferenceJob.status}`}><span style={{ width: `${activeReferenceJob.progress}%` }} /><small>{jobLabel(activeReferenceJob, "参考任务")}</small></div>}
            {activeReferenceUrl && <Waveform src={activeReferenceUrl} color={activeReference.color} />}
            {activeReferenceUrl ? <AudioPlayer src={activeReferenceUrl} label={`${activeReference.display_name}参考音频试听`} /> : <p className="reference-empty-audio">请生成、上传或录制一段参考音频。</p>}
            <div className="emotion-header"><span>情绪声线</span><strong>{activeEmotionItems.length}</strong></div>
            <div className="emotion-list">
              {activeEmotionItems.map((emotion) => <button key={emotion.variant_id} disabled={!emotion.audio_url} onClick={() => toggleAudio(`emotion:${emotion.variant_id}`, emotion.audio_url)}>{playing === `emotion:${emotion.variant_id}` ? <Pause size={12} /> : <Play size={12} />}<span>{emotion.emotion_name}</span></button>)}
              {!activeEmotionItems.length && <span className="empty-inline">尚未生成</span>}
            </div>
          </section>
        )}
      </aside>

      <section className="script-pane">
        <div className="pane-heading script-heading quality-script-heading">
          <div><span className="eyebrow">DIRECTOR SCORE</span><h2>导演脚本</h2></div>
          <select aria-label="质量渲染项目" value={selectedProjectId ?? ""} onChange={(event) => onProjectChange(event.target.value || null)}>
            {sources.filter((source) => source.status === "director_ready" || source.project_id === selectedProjectId).map((source) => <option key={source.project_id} value={source.project_id}>{source.display_name}</option>)}
          </select>
          <div className="script-tools">
            <span>{segments.length.toLocaleString()} 句</span>
            <button className="secondary-button" onClick={() => onStageChange("casting")}><Users size={15} />角色审核</button>
            <button className="primary-button" disabled={Boolean(streamView) || submittingBatch || loadingProject || !selectedSegmentIds.size} onClick={() => void renderSelected()}>{submittingBatch ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}生成已选 {selectedSegmentIds.size}</button>
          </div>
        </div>
        <div className="quality-production-controls">
          <label><span>全局旁白</span><select value={productionSettings?.narrator_gender ?? "male"} disabled={!productionSettings || Boolean(streamView)} onChange={(event) => void saveProductionPreference({ narrator_gender: event.target.value as "male" | "female" })}><option value="male">男旁白</option><option value="female">女旁白</option></select></label>
          <label className="cache-policy-toggle"><input type="checkbox" checked={productionSettings?.auto_delete_played_cache ?? false} disabled={!productionSettings} onChange={(event) => void saveProductionPreference({ auto_delete_played_cache: event.target.checked })} /><span>流播自动清理</span></label>
          <label className="cache-keep-control"><span>播放后保留</span><input type="number" min="1" max="1000" value={productionSettings?.cache_keep_sentences ?? 20} disabled={!productionSettings?.auto_delete_played_cache} onChange={(event) => setProductionSettings((current) => current ? { ...current, cache_keep_sentences: Math.max(1, Math.min(1000, Number(event.target.value) || 1)) } : current)} onBlur={(event) => void saveProductionPreference({ cache_keep_sentences: Number(event.target.value) })} /><strong>句</strong></label>
        </div>
        {continuousRun && (
          <div className="quality-continuous-queues">
            <div className="quality-slice-queue quality-slice-queue--local" aria-label="连续生产切片队列">
              <span><Layers3 size={13} /><strong>切片队列</strong><small>{continuousRun.message}</small></span>
              <div>
                {continuousRun.slices.map((slice) => (
                  <button key={slice.slice_id} className={`state--${slice.state}`} title={`${slice.title} · ${slice.message}`} disabled={!slice.segment_count} onClick={() => locateProductionSlice(slice.slice_id)}>
                    {slice.state === "failed" || slice.state === "blocked" ? <CircleAlert size={11} /> : <b>{slice.index}</b>}
                    <span>{slice.title}</span>
                    <small>{slice.completed_segment_count}/{slice.segment_count || "-"}</small>
                  </button>
                ))}
              </div>
            </div>
            {continuousRun.settings.rvc_stability_policy === "prepare_candidates" && (
              <div className="quality-rvc-queue">
                <span><WandSparkles size={13} /><strong>RVC 稳定层</strong><small>{continuousRun.rvc_progress}%</small></span>
                <div className="continuous-progress-track"><span style={{ width: `${continuousRun.rvc_progress}%` }} /></div>
                <div>{continuousRun.rvc_tasks.map((task) => <span key={task.character_id} className={`state--${task.status}`} title={task.error ?? task.message}><b>{task.display_name}</b><small>{qualityRvcStatusLabel[task.status]}</small></span>)}</div>
              </div>
            )}
          </div>
        )}
        <div className="script-pagination">
          <span className="stream-hint" title="双击句子后从该句开始流式播放" aria-label="双击句子流式播放"><ListMusic size={13} />流式播放</span>
          <button className="icon-button" title="上一章" disabled={chapterIndex === 0} onClick={() => setChapterIndex((current) => Math.max(0, current - 1))}><ChevronLeft size={15} /></button>
          <span>第 {chapters.length ? chapterIndex + 1 : 0} / {chapters.length} 章 · {activeChapter?.label ?? "未分章"} · {visibleSegments.length} 句</span>
          <button className="icon-button" title="下一章" disabled={chapterIndex + 1 >= chapters.length} onClick={() => setChapterIndex((current) => Math.min(Math.max(0, chapters.length - 1), current + 1))}><ChevronRight size={15} /></button>
        </div>
        <div className="script-table-head quality-script-grid">
          <label><input type="checkbox" checked={allSegmentsSelected} onChange={(event) => { selectionManuallyEditedRef.current = true; setSelectedSegmentIds((current) => { const next = new Set(current); segments.forEach((segment) => event.target.checked ? next.add(segment.segment_id) : next.delete(segment.segment_id)); return next; }); }} /><span>选择</span></label>
          <span>角色 / 表演</span><span>文本</span><span>状态</span>
        </div>
        <div className="script-list">
          {visibleSegments.map((segment, index) => {
            const reference = referenceForSegment(segment);
            const job = jobForSegment(segment);
            const resultUrl = job?.status === "complete" ? job.output_url : null;
            const speaker = speakerForSegment(segment, reference);
            const absoluteIndex = (activeChapter?.startIndex ?? 0) + index;
            const isStreamCurrent = streamView?.currentSegmentId === segment.segment_id;
            const isStreamPlaying = isStreamCurrent && streamView?.status === "playing";
            return (
              <article
                id={`quality-segment-${segment.segment_id}`}
                className={`script-row quality-script-grid ${isStreamCurrent ? "stream-current" : ""} ${segmentLocation?.segmentId === segment.segment_id ? "segment-located" : ""}`}
                key={segment.segment_id}
                title="单击句子定位右侧结果；播放键续接流播；双击取消旧队列并从此句重建"
                onClick={(event) => {
                  if ((event.target as HTMLElement).closest("button, input, select, a")) return;
                  if (segmentClickTimerRef.current !== null) window.clearTimeout(segmentClickTimerRef.current);
                  segmentClickTimerRef.current = window.setTimeout(() => {
                    locateResultSegment(segment.segment_id);
                    segmentClickTimerRef.current = null;
                  }, 180);
                }}
                onDoubleClick={(event) => {
                  if ((event.target as HTMLElement).closest("button, input, select, a")) return;
                  if (segmentClickTimerRef.current !== null) {
                    window.clearTimeout(segmentClickTimerRef.current);
                    segmentClickTimerRef.current = null;
                  }
                  void startStreamingFrom(absoluteIndex, "restart");
                }}
              >
                <input type="checkbox" aria-label={`选择第 ${absoluteIndex + 1} 句`} checked={selectedSegmentIds.has(segment.segment_id)} onChange={(event) => { selectionManuallyEditedRef.current = true; setSelectedSegmentIds((current) => { const next = new Set(current); event.target.checked ? next.add(segment.segment_id) : next.delete(segment.segment_id); return next; }); }} />
                <div className="segment-meta quality-segment-meta"><span className={`cast-dot cast-dot--${reference?.color ?? "teal"}`} /><select aria-label={`第 ${absoluteIndex + 1} 句角色声线`} value={segment.voice_reference_id ?? ""} disabled={Boolean(streamView) || isPending(job) || segmentVoiceBusy === segment.segment_id} title={speaker} onChange={(event) => void saveSegmentVoice(segment, event.target.value || null)}><option value="">{automaticVoiceLabel(segment, reference)}</option>{availableVoiceReferences.map((item) => <option key={item.reference_id} value={item.reference_id}>{item.display_name}</option>)}</select><span className="emotion-chip">{segment.direction.emotion}</span></div>
                <div className="segment-text"><span className="line-number">{String(absoluteIndex + 1).padStart(3, "0")}</span><p>{segment.text}</p></div>
                <button
                  className="icon-button"
                  disabled={(isStreamCurrent && (streamView?.status === "preparing" || streamView?.status === "buffering")) || (!resultUrl && !isPending(job) && !referenceAudioUrl(reference))}
                  title={isStreamCurrent ? (isStreamPlaying ? "暂停连续播放" : "继续连续播放") : resultUrl ? "从本句续接连续播放并复用缓存" : isPending(job) ? "从本句等待生成并连续播放" : "从本句开始流式生成并连续播放"}
                  onClick={() => isStreamCurrent
                    ? void toggleStreamPlayback()
                    : void startStreamingFrom(absoluteIndex, "reuse")}
                >
                  {isPending(job) && !isStreamCurrent ? <LoaderCircle className="spin" size={15} /> : isStreamPlaying ? <Pause size={15} /> : <Play size={15} />}
                </button>
              </article>
            );
          })}
          {!visibleSegments.length && <p className="empty-state">{loadingProject ? "正在载入真实项目" : "请先完成导演脚本"}</p>}
        </div>
        <footer className="timeline"><div><ListMusic size={16} /><span>{streamView ? `流播第 ${streamView.currentIndex + 1} 句` : "GPU 串行队列"}</span><strong>{operationFeedback}</strong></div><div className="timeline-track"><span style={{ width: `${segments.length ? (completedSegments.length / segments.length) * 100 : 0}%` }} /></div>{streamView ? <div className="stream-controls"><button className="secondary-button" onClick={() => void stopStreamQueue()}><CircleStop size={14} />停止</button><button className="primary-button" disabled={streamView.status === "preparing" || streamView.status === "buffering"} onClick={() => void toggleStreamPlayback()}>{streamView.status === "paused" ? <Play size={15} /> : streamView.status === "playing" ? <Pause size={15} /> : <LoaderCircle className="spin" size={15} />}{streamView.status === "paused" ? "继续流播" : streamView.status === "playing" ? "暂停流播" : "流式缓冲"}</button></div> : <button className="primary-button" disabled={!completedJobIds.length} onClick={() => mergedAudio ? toggleAudio("merged", mergedAudio.output_url) : void mergeCompleted(true)}>{playing === "merged" ? <Pause size={15} /> : <Play size={15} />}播放已完成</button>}</footer>
      </section>

      <aside className="result-pane">
        <div className="pane-heading quality-result-heading"><div><span className="eyebrow">QUALITY MODEL</span><h2>{qualityModelLabel}</h2></div><button className="secondary-button" onClick={() => setShowAdvancedSettings(true)}><Settings2 size={14} />高级设置</button><span className={`queue-state ${failedCount ? "queue-state--failed" : ""}`}><span />{streamView ? `流播剩余 ${streamView.remaining} 句` : pendingCount ? `${pendingCount} 个任务进行中` : failedCount ? `${failedCount} 个任务失败` : "队列空闲"}</span></div>
        <div className="quality-cache-toolbar">
          <label><input type="checkbox" checked={allCacheSelected} disabled={!completedCacheJobs.length || cacheBusy} onChange={(event) => setSelectedCacheJobIds(event.target.checked ? new Set(completedCacheJobs.map((job) => job.job_id)) : new Set())} /><span>全选缓存</span></label>
          <span>{selectedCacheJobIds.size} / {completedCacheJobs.length}</span>
          <div className="quality-cache-actions">
            <button className="secondary-button" disabled={cacheBusy || Boolean(streamView) || !selectedCacheJobIds.size || !rvcWorkspace?.settings.quality_stability_enabled} onClick={() => void reprocessCacheRvc([...selectedCacheJobIds])}>{cacheBusy ? <LoaderCircle className="spin" size={14} /> : <WandSparkles size={14} />}应用 RVC</button>
            <button className="secondary-button" disabled={cacheBusy || Boolean(streamView) || !selectedCacheJobIds.size} onClick={() => void reprocessCacheLoudness([...selectedCacheJobIds])}>{cacheBusy ? <LoaderCircle className="spin" size={14} /> : <Gauge size={14} />}统一响度</button>
            <button className="secondary-button danger-button" disabled={cacheBusy || Boolean(streamView) || !selectedCacheJobIds.size} onClick={() => void removeCacheJobs([...selectedCacheJobIds])}>{cacheBusy ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />}批量删除</button>
          </div>
        </div>
        <div className="result-list">
          {visibleSegments.map((segment) => {
            const reference = referenceForSegment(segment);
            const job = jobForSegment(segment);
            const resultUrl = job?.status === "complete" ? job.output_url : null;
            const speaker = speakerForSegment(segment, reference);
            return (
              <article
                id={`quality-result-${segment.segment_id}`}
                className={`result-card result-card--locatable ${segmentLocation?.segmentId === segment.segment_id ? "segment-located" : ""}`}
                key={segment.segment_id}
                title="单击定位到中间对应句；音频控件仅试听本句"
                onClick={(event) => {
                  if ((event.target as HTMLElement).closest("button, input, select, a, .audio-player")) return;
                  locateScriptSegment(segment.segment_id);
                }}
              >
                <header><div>{resultUrl && job && <input type="checkbox" aria-label={`选择 ${segment.segment_id} 音频缓存`} checked={selectedCacheJobIds.has(job.job_id)} onChange={(event) => setSelectedCacheJobIds((current) => { const next = new Set(current); event.target.checked ? next.add(job.job_id) : next.delete(job.job_id); return next; })} />}<span className={`cast-dot cast-dot--${reference?.color ?? "teal"}`} /><strong>{speaker}</strong><span>{segment.direction.emotion}</span></div><span className="render-time">{jobLabel(job, "待生成")}</span></header>
                <p>{segment.text}</p>
                {job && <div className={`job-progress job-progress--${job.status}`}><span style={{ width: `${job.progress}%` }} /><small>{job.message}</small></div>}
                {job && job.rvc_status !== "not_requested" && <div className={`rvc-render-state rvc-render-state--${job.rvc_status}`} title={job.rvc_error ?? undefined}><span>RVC</span><strong>{job.rvc_status === "applied" ? `已应用 · ${job.rvc_model_id ?? "当前模型"}` : job.rvc_status === "fallback" ? "处理失败，已回退基础渲染" : "当前角色未应用"}</strong></div>}
                {loudnessLabel(job?.loudness_metrics) && <div className="loudness-metrics"><span>LUFS</span><strong>{loudnessLabel(job?.loudness_metrics)}</strong></div>}
                {resultUrl && <Waveform src={resultUrl} color={reference?.color ?? "teal"} />}
                {resultUrl && <AudioPlayer src={resultUrl} label={`${speaker}渲染结果试听`} className="result-audio-player" onPlay={pauseStreamForSinglePreview} />}
                <footer><button className="text-button" disabled={Boolean(streamView) || isPending(job) || !referenceAudioUrl(reference)} onClick={() => void regenerateSegment(segment)}>{isPending(job) ? <LoaderCircle className="spin" size={13} /> : <RefreshCw size={13} />}重新生成</button><button className="text-button" disabled={Boolean(streamView) || !referenceAudioUrl(reference)} onClick={() => toggleAudio(`reference:${segment.segment_id}`, referenceAudioUrl(reference))}><Sparkles size={13} />参考声线</button><span className="spacer" />{resultUrl && job && <button className="icon-button cache-delete-button" title="删除本句缓存" disabled={cacheBusy || Boolean(streamView)} onClick={() => void removeCacheJobs([job.job_id])}><Trash2 size={15} /></button>}{resultUrl ? <a className="icon-button" title="下载音频" href={resultUrl} download={`${segment.segment_id}.wav`}><Download size={15} /></a> : <button className="icon-button" disabled title="尚无音频"><Download size={15} /></button>}</footer>
              </article>
            );
          })}
        </div>
        <footer className="export-bar"><div><FileAudio size={16} /><span>已完成 {completedSegments.length} / {segments.length}</span>{mergedAudio && <strong>{mergedAudio.duration_seconds.toFixed(1)} 秒</strong>}{mergedAudio && loudnessLabel(mergedAudio.loudness_metrics) && <small>{loudnessLabel(mergedAudio.loudness_metrics)}</small>}</div><div>{mergedAudio && <a className="icon-button" title="下载合并音频" href={mergedAudio.output_url} download={`${selectedSource?.display_name ?? "project"}-merged.wav`}><Download size={15} /></a>}<button className="secondary-button" disabled={!completedJobIds.length} onClick={() => void mergeCompleted()}>{mergedAudio ? "重新合并" : "合并音频"}</button></div></footer>
      </aside>

      {showAdvancedSettings && <AdvancedSettingsPanel options={renderOptions} loudness={loudnessPolicy} renderer={selectedModelRenderer} saving={savingSettings} onChange={setRenderOptions} onLoudnessChange={setLoudnessPolicy} onSave={() => void saveAdvancedSettings()} onClose={() => setShowAdvancedSettings(false)} />}
    </section>
  );
}
