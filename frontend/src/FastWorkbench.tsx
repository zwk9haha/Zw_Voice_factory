import { Check, ChevronLeft, ChevronRight, CircleStop, Download, Gauge, Layers3, ListMusic, LoaderCircle, Pause, Play, RefreshCw, Sparkles, Trash2, Users, Volume2, Zap } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { AudioPlayer } from "./AudioPlayer";
import { cancelFastQueue, createAudioJob, deleteFastCache, fetchAudioJobs, fetchFastRouteWorkspace, fetchPreparationPreview, fetchProductionSettings, fetchRvcWorkspace, mergeFastAudio, updateFastVoice } from "./api";
import type { AudioJob, FastRouteWorkspace, FastVoiceOption, MergedAudio, PreparationPreview, PreparedDirectorSegment, ProductionSettings, ProductionStageId, ReferencePlanItem, RvcCharacterView, RvcWorkspace, SourceSummary } from "./types";
import { Waveform } from "./Waveform";

interface FastWorkbenchProps {
  sources: SourceSummary[];
  selectedProjectId: string | null;
  selectedSliceId: string | null;
  onProjectChange: (projectId: string | null) => void;
  onStageChange: (stage: ProductionStageId) => void;
}

interface StreamQueue {
  sessionId: number;
  targets: PreparedDirectorSegment[];
  playIndex: number;
  submitIndex: number;
  jobIds: Map<string, string>;
}

interface StreamView {
  currentSegmentId: string;
  currentIndex: number;
  remaining: number;
  status: "buffering" | "playing" | "paused";
}

const PAGE_SIZE = 100;
const STREAM_BUFFER_SIZE = 3;

function isPending(job: AudioJob | undefined): boolean {
  return job?.status === "queued" || job?.status === "running";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "极速路线请求失败";
}

function latestJobsBySegment(jobs: AudioJob[]): Map<string, AudioJob> {
  const result = new Map<string, AudioJob>();
  [...jobs]
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
    .forEach((job) => {
      if (job.segment_id && !result.has(job.segment_id) && job.status !== "cancelled") result.set(job.segment_id, job);
    });
  return result;
}

export function FastWorkbench({ sources, selectedProjectId, selectedSliceId, onProjectChange, onStageChange }: FastWorkbenchProps) {
  const [preview, setPreview] = useState<PreparationPreview | null>(null);
  const [fastWorkspace, setFastWorkspace] = useState<FastRouteWorkspace | null>(null);
  const [rvcWorkspace, setRvcWorkspace] = useState<RvcWorkspace | null>(null);
  const [productionSettings, setProductionSettings] = useState<ProductionSettings | null>(null);
  const [jobs, setJobs] = useState<Record<string, AudioJob>>({});
  const [selectedCharacterId, setSelectedCharacterId] = useState<string | null>(null);
  const [selectedSegmentIds, setSelectedSegmentIds] = useState<Set<string>>(new Set());
  const [selectedCacheJobIds, setSelectedCacheJobIds] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [feedback, setFeedback] = useState("选择已完成导演脚本的项目");
  const [playing, setPlaying] = useState<string | null>(null);
  const [streamView, setStreamView] = useState<StreamView | null>(null);
  const [mergedAudio, setMergedAudio] = useState<MergedAudio | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const jobsRef = useRef<Record<string, AudioJob>>({});
  const streamSessionRef = useRef(0);
  const streamQueueRef = useRef<StreamQueue | null>(null);
  const streamPumpBusyRef = useRef(false);

  const allJobs = useMemo(() => Object.values(jobs), [jobs]);
  const segmentJobs = useMemo(() => latestJobsBySegment(allJobs.filter((job) => job.kind === "fast_render")), [allJobs]);
  const characters = preview?.character_voice_bible?.characters ?? [];
  const references = preview?.reference_plan?.items ?? [];
  const allSegments = preview?.director_doc?.segments ?? [];
  const segments = selectedSliceId
    ? allSegments.filter((segment) => segment.analysis_batch_id === selectedSliceId)
    : allSegments;
  const assignments = useMemo(() => new Map(fastWorkspace?.settings.assignments.map((item) => [item.character_id, item.voice_id]) ?? []), [fastWorkspace]);
  const voices = useMemo(() => new Map(fastWorkspace?.voices.map((voice) => [voice.voice_id, voice]) ?? []), [fastWorkspace]);
  const referencesById = useMemo(() => new Map(references.map((reference) => [reference.reference_id, reference])), [references]);
  const charactersById = useMemo(() => new Map(characters.map((character) => [character.character_id, character])), [characters]);
  const rvcCharacters = useMemo(() => new Map(rvcWorkspace?.characters.map((character) => [character.character_id, character]) ?? []), [rvcWorkspace]);
  const selectedCharacter = characters.find((character) => character.character_id === selectedCharacterId) ?? characters[0] ?? null;
  const selectedVoice = selectedCharacter ? voices.get(assignments.get(selectedCharacter.character_id) ?? "") ?? null : null;
  const pageCount = Math.max(1, Math.ceil(segments.length / PAGE_SIZE));
  const visibleSegments = segments.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const selectedSource = sources.find((source) => source.project_id === selectedProjectId) ?? null;

  useEffect(() => {
    setPage(0);
    setSelectedSegmentIds(new Set(segments.map((segment) => segment.segment_id)));
  }, [selectedProjectId, selectedSliceId, preview?.director_doc?.segments]);

  function recordJobs(nextJobs: AudioJob[]) {
    const incoming = Object.fromEntries(nextJobs.map((job) => [job.job_id, job]));
    jobsRef.current = { ...jobsRef.current, ...incoming };
    setJobs((current) => ({ ...current, ...incoming }));
  }

  function referenceForSegment(segment: PreparedDirectorSegment): ReferencePlanItem | null {
    if (segment.voice_reference_id) return referencesById.get(segment.voice_reference_id) ?? null;
    if (segment.segment_type === "narration" || segment.character_id === "narrator") {
      return references.find((reference) => reference.source_character_id === "narrator" && reference.gender === (productionSettings?.narrator_gender ?? "male"))
        ?? references.find((reference) => reference.source_character_id === "narrator")
        ?? null;
    }
    return references.find((reference) => reference.source_character_id === segment.character_id && reference.selected) ?? null;
  }

  function voiceForSegment(segment: PreparedDirectorSegment): FastVoiceOption | null {
    return voices.get(assignments.get(segment.character_id) ?? "") ?? fastWorkspace?.voices[0] ?? null;
  }

  function rvcForSegment(segment: PreparedDirectorSegment): RvcCharacterView | null {
    const reference = referenceForSegment(segment);
    const bindingId = segment.character_id === "narrator" && reference?.gender
      ? `narrator-${reference.gender}`
      : segment.character_id;
    return rvcCharacters.get(bindingId) ?? null;
  }

  function usesRvc(segment: PreparedDirectorSegment): boolean {
    const binding = rvcForSegment(segment);
    return Boolean(rvcWorkspace?.settings.fast_route_enabled && binding?.fast_route_enabled && binding.selected_model_id);
  }

  function jobForSegment(segment: PreparedDirectorSegment): AudioJob | undefined {
    const job = segmentJobs.get(segment.segment_id);
    const voice = voiceForSegment(segment);
    if (job?.fast_voice_id && voice && job.fast_voice_id !== voice.voice_id) return undefined;
    if (job && Boolean(job.fast_rvc_enabled) !== usesRvc(segment)) return undefined;
    return job;
  }

  function speakerForSegment(segment: PreparedDirectorSegment): string {
    return charactersById.get(segment.character_id)?.display_name ?? (segment.segment_type === "narration" ? "旁白" : "未分配角色");
  }

  const completedSegments = segments.filter((segment) => {
    const job = jobForSegment(segment);
    return job?.status === "complete" && Boolean(job.output_url);
  });
  const completedJobIds = completedSegments.map((segment) => jobForSegment(segment)?.job_id).filter((jobId): jobId is string => Boolean(jobId));
  const completedCacheJobs = allJobs.filter((job) => job.kind === "fast_render" && job.status === "complete" && Boolean(job.output_url));
  const pendingCount = allJobs.filter((job) => job.kind === "fast_render" && isPending(job)).length;
  const failedCount = allJobs.filter((job) => job.kind === "fast_render" && job.status === "failed").length;

  useEffect(() => {
    const readySources = sources.filter((source) => source.status === "director_ready");
    if (selectedProjectId && readySources.some((source) => source.project_id === selectedProjectId)) return;
    const nextProjectId = readySources[0]?.project_id ?? null;
    if (nextProjectId !== selectedProjectId) onProjectChange(nextProjectId);
  }, [onProjectChange, selectedProjectId, sources]);

  useEffect(() => {
    if (!selectedProjectId) {
      setLoading(false);
      return;
    }
    streamSessionRef.current += 1;
    streamQueueRef.current = null;
    setStreamView(null);
    audioRef.current?.pause();
    setPlaying(null);
    setLoading(true);
    let active = true;
    Promise.all([
      fetchPreparationPreview(selectedProjectId),
      fetchFastRouteWorkspace(selectedProjectId),
      fetchRvcWorkspace(selectedProjectId),
      fetchProductionSettings(),
      fetchAudioJobs({ projectId: selectedProjectId, kind: "fast_render", limit: 5_000 }),
    ]).then(([nextPreview, nextFastWorkspace, nextRvcWorkspace, nextProductionSettings, persistedJobs]) => {
      if (!active) return;
      setPreview(nextPreview);
      setFastWorkspace(nextFastWorkspace);
      setRvcWorkspace(nextRvcWorkspace);
      setProductionSettings(nextProductionSettings);
      setSelectedCharacterId(nextPreview.character_voice_bible?.characters[0]?.character_id ?? null);
      setSelectedSegmentIds(new Set(nextPreview.director_doc?.segments.map((segment) => segment.segment_id) ?? []));
      const restored = Object.fromEntries(persistedJobs.map((job) => [job.job_id, job]));
      jobsRef.current = restored;
      setJobs(restored);
      setSelectedCacheJobIds(new Set());
      setMergedAudio(null);
      setPage(0);
      setFeedback(`${nextPreview.director_doc?.segments.length ?? 0} 句导演脚本已载入极速路线`);
    }).catch((error: unknown) => {
      if (active) setFeedback(errorMessage(error));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId || !pendingCount) return;
    const timer = window.setInterval(() => {
      fetchAudioJobs({ projectId: selectedProjectId, kind: "fast_render", limit: 5_000 })
        .then(recordJobs)
        .catch(() => undefined);
    }, 900);
    return () => window.clearInterval(timer);
  }, [pendingCount, selectedProjectId]);

  useEffect(() => {
    const queue = streamQueueRef.current;
    if (queue) void pumpStream(queue.sessionId);
  }, [jobs]);

  useEffect(() => () => {
    streamSessionRef.current += 1;
    streamQueueRef.current = null;
    audioRef.current?.pause();
  }, []);

  async function saveVoice(characterId: string, voiceId: string) {
    if (!selectedProjectId) return;
    setBusy(`voice:${characterId}`);
    try {
      setFastWorkspace(await updateFastVoice(selectedProjectId, characterId, voiceId));
      setFeedback("轻量声线映射已保存，旧缓存将保留但不再作为当前结果");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy("");
    }
  }

  async function submitSegment(segment: PreparedDirectorSegment): Promise<AudioJob> {
    if (!selectedProjectId) throw new Error("未选择项目");
    const voice = voiceForSegment(segment);
    if (!voice) throw new Error(`${speakerForSegment(segment)} 尚未分配轻量声线`);
    const reference = referenceForSegment(segment);
    const job = await createAudioJob({
      kind: "fast_render",
      project_id: selectedProjectId,
      segment_id: segment.segment_id,
      character_id: segment.character_id,
      reference_id: reference?.reference_id,
      text: segment.text,
      fast_voice_id: voice.voice_id,
      fast_speed: Math.max(0.5, Math.min(2, segment.direction.speed)),
      fast_rvc_enabled: usesRvc(segment),
    });
    recordJobs([job]);
    return job;
  }

  async function renderSelected() {
    const targets = segments.filter((segment) => selectedSegmentIds.has(segment.segment_id));
    if (!targets.length) return;
    if (streamQueueRef.current) await stopStream();
    setBusy("batch");
    setFeedback(`正在提交 ${targets.length} 句极速渲染任务`);
    try {
      for (const segment of targets) await submitSegment(segment);
      setFeedback(`${targets.length} 句已进入轻量 TTS 串行队列`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy("");
    }
  }

  async function regenerate(segment: PreparedDirectorSegment) {
    setBusy(`segment:${segment.segment_id}`);
    try {
      await submitSegment(segment);
      setFeedback(`${speakerForSegment(segment)} · ${segment.segment_id} 已重新提交`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy("");
    }
  }

  async function startStreamingFrom(index: number) {
    if (!selectedProjectId || index >= segments.length) return;
    const sessionId = streamSessionRef.current + 1;
    streamSessionRef.current = sessionId;
    streamQueueRef.current = null;
    audioRef.current?.pause();
    setPlaying(null);
    try {
      const cancelled = await cancelFastQueue(selectedProjectId);
      recordJobs(cancelled.cancelled_jobs);
      if (streamSessionRef.current !== sessionId) return;
      const targets = segments.slice(index);
      streamQueueRef.current = { sessionId, targets, playIndex: 0, submitIndex: 0, jobIds: new Map() };
      setStreamView({ currentSegmentId: targets[0].segment_id, currentIndex: index, remaining: targets.length, status: "buffering" });
      setFeedback(`从第 ${index + 1} 句开始流式预取`);
      await pumpStream(sessionId);
    } catch (error) {
      setFeedback(errorMessage(error));
      setStreamView(null);
    }
  }

  async function pumpStream(sessionId: number) {
    if (streamPumpBusyRef.current) return;
    streamPumpBusyRef.current = true;
    try {
      const queue = streamQueueRef.current;
      if (!queue || queue.sessionId !== sessionId || streamSessionRef.current !== sessionId) return;
      const submitLimit = Math.min(queue.targets.length, queue.playIndex + STREAM_BUFFER_SIZE);
      while (queue.submitIndex < submitLimit) {
        const segment = queue.targets[queue.submitIndex];
        const existing = jobForSegment(segment);
        if (existing?.status === "complete" && existing.output_url) {
          queue.jobIds.set(segment.segment_id, existing.job_id);
        } else {
          const job = await submitSegment(segment);
          if (streamSessionRef.current !== sessionId) return;
          queue.jobIds.set(segment.segment_id, job.job_id);
        }
        queue.submitIndex += 1;
      }
      const current = queue.targets[queue.playIndex];
      if (!current) {
        streamQueueRef.current = null;
        setStreamView(null);
        setPlaying(null);
        setFeedback("极速流式播放完成");
        return;
      }
      const jobId = queue.jobIds.get(current.segment_id);
      const job = jobId ? jobsRef.current[jobId] : jobForSegment(current);
      if (job?.status === "failed" || job?.status === "cancelled") {
        queue.playIndex += 1;
        const next = queue.targets[queue.playIndex];
        if (next) {
          setStreamView((current) => current ? { ...current, currentSegmentId: next.segment_id, currentIndex: current.currentIndex + 1, remaining: queue.targets.length - queue.playIndex, status: "buffering" } : current);
          window.setTimeout(() => void pumpStream(sessionId), 0);
        } else {
          streamQueueRef.current = null;
          setStreamView(null);
          setFeedback("极速流式播放完成，已跳过失败任务");
        }
        return;
      }
      if (job?.status !== "complete" || !job.output_url || !audioRef.current) return;
      const audioId = `stream:${current.segment_id}`;
      if (playing === audioId && !audioRef.current.paused) return;
      audioRef.current.src = job.output_url;
      await audioRef.current.play();
      setPlaying(audioId);
      const absoluteIndex = segments.findIndex((segment) => segment.segment_id === current.segment_id);
      setStreamView({ currentSegmentId: current.segment_id, currentIndex: absoluteIndex, remaining: queue.targets.length - queue.playIndex, status: "playing" });
    } catch (error) {
      if (streamSessionRef.current === sessionId) setFeedback(errorMessage(error));
    } finally {
      streamPumpBusyRef.current = false;
    }
  }

  async function stopStream() {
    streamSessionRef.current += 1;
    streamQueueRef.current = null;
    audioRef.current?.pause();
    setPlaying(null);
    setStreamView(null);
    if (!selectedProjectId) return;
    try {
      const cancelled = await cancelFastQueue(selectedProjectId);
      recordJobs(cancelled.cancelled_jobs);
      setFeedback(`流式播放已停止，取消 ${cancelled.cancelled_count} 个任务`);
    } catch (error) {
      setFeedback(errorMessage(error));
    }
  }

  async function toggleStreamPlayback() {
    const audio = audioRef.current;
    if (!audio || !streamView) return;
    if (audio.paused) {
      await audio.play();
      setStreamView((current) => current ? { ...current, status: "playing" } : current);
    } else {
      audio.pause();
      setStreamView((current) => current ? { ...current, status: "paused" } : current);
    }
  }

  function handleAudioEnded() {
    const queue = streamQueueRef.current;
    if (!queue || !playing?.startsWith("stream:")) {
      setPlaying(null);
      return;
    }
    queue.playIndex += 1;
    setPlaying(null);
    const next = queue.targets[queue.playIndex];
    if (next) {
      setStreamView((current) => current ? { ...current, currentSegmentId: next.segment_id, currentIndex: current.currentIndex + 1, remaining: queue.targets.length - queue.playIndex, status: "buffering" } : current);
      void pumpStream(queue.sessionId);
    } else {
      streamQueueRef.current = null;
      setStreamView(null);
      setFeedback("极速流式播放完成");
    }
  }

  function toggleAudio(id: string, url: string | null) {
    if (!url || !audioRef.current) return;
    if (streamQueueRef.current) void stopStream();
    if (playing === id && !audioRef.current.paused) {
      audioRef.current.pause();
      setPlaying(null);
      return;
    }
    audioRef.current.src = url;
    audioRef.current.play().then(() => setPlaying(id)).catch((error: unknown) => setFeedback(errorMessage(error)));
  }

  async function removeCache(jobIds: string[]) {
    if (!selectedProjectId || !jobIds.length) return;
    setBusy("cache");
    try {
      const result = await deleteFastCache(selectedProjectId, jobIds);
      recordJobs(result.deleted_jobs);
      setSelectedCacheJobIds((current) => new Set([...current].filter((jobId) => !jobIds.includes(jobId))));
      setMergedAudio(null);
      setFeedback(`已删除 ${result.deleted_count} 条极速缓存`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy("");
    }
  }

  async function mergeCompleted(playAfterMerge = false) {
    if (!selectedProjectId || !completedJobIds.length) return;
    setBusy("merge");
    try {
      const result = await mergeFastAudio(selectedProjectId, completedJobIds);
      setMergedAudio(result);
      setFeedback(`已按导演顺序合并 ${result.segment_count} 句`);
      if (playAfterMerge) toggleAudio("merged-fast", result.output_url);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy("");
    }
  }

  const visibleSelectedCount = visibleSegments.filter((segment) => selectedSegmentIds.has(segment.segment_id)).length;
  const allVisibleSelected = visibleSegments.length > 0 && visibleSelectedCount === visibleSegments.length;
  const allCacheSelected = completedCacheJobs.length > 0 && completedCacheJobs.every((job) => selectedCacheJobIds.has(job.job_id));
  const selectedRvc = selectedCharacter ? rvcCharacters.get(selectedCharacter.character_id) ?? null : null;

  return (
    <section className="workspace-grid fast-workbench">
      <audio ref={audioRef} className="audio-preview" preload="metadata" onEnded={handleAudioEnded} onError={() => setFeedback("音频播放失败，请重新生成当前句")} />
      <aside className="cast-pane fast-cast-pane">
        <div className="pane-heading"><div><span className="eyebrow">FAST CAST</span><h2>轻量声线</h2></div><span className="list-count">{characters.length}</span></div>
        <div className="cast-list">
          {characters.map((character) => {
            const voice = voices.get(assignments.get(character.character_id) ?? "");
            const binding = rvcCharacters.get(character.character_id);
            const rvcActive = Boolean(rvcWorkspace?.settings.fast_route_enabled && binding?.fast_route_enabled && binding.selected_model_id);
            return <button key={character.character_id} className={`cast-item ${selectedCharacter?.character_id === character.character_id ? "selected" : ""}`} onClick={() => setSelectedCharacterId(character.character_id)}><span className={`cast-dot cast-dot--${character.gender === "female" ? "violet" : character.gender === "male" ? "teal" : "gold"}`} /><span className="cast-copy"><strong>{character.display_name}</strong><small>{voice?.label ?? "未分配"} · 权重 {Math.round(character.importance * 100)}</small></span><span className={`status status--${rvcActive ? "accepted" : "pending"}`}>{rvcActive ? "RVC" : "原声"}</span></button>;
          })}
          {!characters.length && <p className="empty-state">请先生成角色印象文件</p>}
        </div>
        {selectedCharacter && (
          <section className="fast-voice-editor">
            <div className="section-title"><Volume2 size={16} /><h3>{selectedCharacter.display_name} · 极速基线</h3></div>
            <label><span>轻量 TTS 声线</span><select value={selectedVoice?.voice_id ?? ""} disabled={busy === `voice:${selectedCharacter.character_id}`} onChange={(event) => void saveVoice(selectedCharacter.character_id, event.target.value)}>{fastWorkspace?.voices.map((voice) => <option key={voice.voice_id} value={voice.voice_id}>{voice.label} · {voice.effect}</option>)}</select></label>
            <div className={`fast-rvc-state ${selectedRvc?.fast_route_enabled && rvcWorkspace?.settings.fast_route_enabled ? "active" : ""}`}><Layers3 size={15} /><span><strong>RVC 角色身份层</strong><small>{selectedRvc?.fast_route_enabled && selectedRvc.selected_model_id && rvcWorkspace?.settings.fast_route_enabled ? "当前角色已接入" : selectedRvc?.selected_model_id ? "模型已绑定，前往 RVC 工作台启用" : "未绑定时保留轻量原声"}</small></span></div>
            <div className="fast-profile"><span>角色印象</span><p>{selectedCharacter.voice_prompt}</p></div>
            <div className="fast-profile-tags">{[...selectedCharacter.timbre_tags.slice(0, 3), ...selectedCharacter.delivery_tags.slice(0, 2)].map((tag) => <span key={tag}>{tag}</span>)}</div>
          </section>
        )}
      </aside>

      <section className="script-pane">
        <div className="pane-heading script-heading quality-script-heading"><div><span className="eyebrow">DIRECTOR SCORE</span><h2>导演脚本</h2></div><select aria-label="极速渲染项目" value={selectedProjectId ?? ""} onChange={(event) => onProjectChange(event.target.value || null)}>{sources.filter((source) => source.status === "director_ready").map((source) => <option key={source.project_id} value={source.project_id}>{source.display_name}</option>)}</select><div className="script-tools"><span>{segments.length.toLocaleString()} 句</span><button className="secondary-button" onClick={() => onStageChange("casting")}><Users size={15} />角色审核</button><button className="primary-button" disabled={Boolean(streamView) || busy === "batch" || loading || !selectedSegmentIds.size} onClick={() => void renderSelected()}>{busy === "batch" ? <LoaderCircle className="spin" size={15} /> : <Zap size={15} />}一键生成 {selectedSegmentIds.size}</button></div></div>
        <div className="fast-production-strip"><span><Gauge size={14} />Sherpa ONNX · 本地 CPU 轻量推理</span><strong><Layers3 size={13} />{rvcWorkspace?.settings.fast_route_enabled ? "RVC 按角色接入" : "RVC 未启用"}</strong><span><ListMusic size={13} />双击任一句开始预取播放</span></div>
        <div className="script-pagination"><button className="icon-button" title="上一页" disabled={page === 0} onClick={() => setPage((current) => Math.max(0, current - 1))}><ChevronLeft size={15} /></button><span>第 {page + 1} / {pageCount} 页 · {segments.length ? `${page * PAGE_SIZE + 1}-${Math.min((page + 1) * PAGE_SIZE, segments.length)}` : "0"}</span><button className="icon-button" title="下一页" disabled={page + 1 >= pageCount} onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}><ChevronRight size={15} /></button></div>
        <div className="script-table-head quality-script-grid"><label><input type="checkbox" checked={allVisibleSelected} onChange={(event) => setSelectedSegmentIds((current) => { const next = new Set(current); visibleSegments.forEach((segment) => event.target.checked ? next.add(segment.segment_id) : next.delete(segment.segment_id)); return next; })} /><span>选择</span></label><span>角色 / 路线</span><span>文本</span><span>状态</span></div>
        <div className="script-list">
          {visibleSegments.map((segment, index) => {
            const job = jobForSegment(segment);
            const resultUrl = job?.status === "complete" ? job.output_url : null;
            const voice = voiceForSegment(segment);
            const absoluteIndex = page * PAGE_SIZE + index;
            return <article id={`fast-segment-${segment.segment_id}`} className={`script-row quality-script-grid ${streamView?.currentSegmentId === segment.segment_id ? "stream-current" : ""}`} key={segment.segment_id} title="双击从此句开始极速流式播放" onDoubleClick={(event) => { if ((event.target as HTMLElement).closest("button, input, select, a")) return; void startStreamingFrom(absoluteIndex); }}><input type="checkbox" aria-label={`选择第 ${absoluteIndex + 1} 句`} checked={selectedSegmentIds.has(segment.segment_id)} onChange={(event) => setSelectedSegmentIds((current) => { const next = new Set(current); event.target.checked ? next.add(segment.segment_id) : next.delete(segment.segment_id); return next; })} /><div className="segment-meta quality-segment-meta"><span className="cast-dot cast-dot--teal" /><strong>{speakerForSegment(segment)}</strong><span className="emotion-chip">{segment.direction.emotion}</span><small className="fast-route-chip">{voice?.label ?? "未分配"}{usesRvc(segment) ? " → RVC" : ""}</small></div><div className="segment-text"><span className="line-number">{String(absoluteIndex + 1).padStart(3, "0")}</span><p>{segment.text}</p></div><button className="icon-button" disabled={Boolean(streamView) || isPending(job) || busy === `segment:${segment.segment_id}`} title={resultUrl ? "播放本句" : job?.message ?? "生成本句"} onClick={() => resultUrl ? toggleAudio(`fast:${segment.segment_id}`, resultUrl) : void regenerate(segment)}>{isPending(job) || busy === `segment:${segment.segment_id}` ? <LoaderCircle className="spin" size={15} /> : resultUrl ? (playing === `fast:${segment.segment_id}` ? <Pause size={15} /> : <Play size={15} />) : <Sparkles size={15} />}</button></article>;
          })}
          {!visibleSegments.length && <p className="empty-state">{loading ? "正在载入导演文件" : "请先完成导演脚本"}</p>}
        </div>
        <footer className="timeline"><div><ListMusic size={16} /><span>{streamView ? `流播第 ${streamView.currentIndex + 1} 句` : "轻量 TTS 队列"}</span><strong>{feedback}</strong></div><div className="timeline-track"><span style={{ width: `${segments.length ? completedSegments.length / segments.length * 100 : 0}%` }} /></div>{streamView ? <div className="stream-controls"><button className="secondary-button" onClick={() => void stopStream()}><CircleStop size={14} />停止</button><button className="primary-button" disabled={streamView.status === "buffering"} onClick={() => void toggleStreamPlayback()}>{streamView.status === "paused" ? <Play size={15} /> : streamView.status === "playing" ? <Pause size={15} /> : <LoaderCircle className="spin" size={15} />}{streamView.status === "paused" ? "继续" : streamView.status === "playing" ? "暂停" : "缓冲"}</button></div> : <button className="primary-button" disabled={!completedJobIds.length || busy === "merge"} onClick={() => mergedAudio ? toggleAudio("merged-fast", mergedAudio.output_url) : void mergeCompleted(true)}>{playing === "merged-fast" ? <Pause size={15} /> : <Play size={15} />}播放已完成</button>}</footer>
      </section>

      <aside className="result-pane">
        <div className="pane-heading quality-result-heading"><div><span className="eyebrow">FAST OUTPUT</span><h2>极速缓存</h2></div><span className={`queue-state ${failedCount ? "queue-state--failed" : ""}`}><span />{streamView ? `预取剩余 ${streamView.remaining} 句` : pendingCount ? `${pendingCount} 个任务进行中` : failedCount ? `${failedCount} 个任务失败` : "队列空闲"}</span></div>
        <div className="quality-cache-toolbar"><label><input type="checkbox" checked={allCacheSelected} disabled={!completedCacheJobs.length || busy === "cache"} onChange={(event) => setSelectedCacheJobIds(event.target.checked ? new Set(completedCacheJobs.map((job) => job.job_id)) : new Set())} /><span>全选缓存</span></label><span>{selectedCacheJobIds.size} / {completedCacheJobs.length}</span><button className="secondary-button danger-button" disabled={busy === "cache" || Boolean(streamView) || !selectedCacheJobIds.size} onClick={() => void removeCache([...selectedCacheJobIds])}>{busy === "cache" ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />}批量删除</button></div>
        <div className="result-list">
          {visibleSegments.map((segment) => {
            const job = jobForSegment(segment);
            const resultUrl = job?.status === "complete" ? job.output_url : null;
            return <article className="result-card" key={segment.segment_id}><header><div>{resultUrl && job && <input type="checkbox" aria-label={`选择 ${segment.segment_id} 极速缓存`} checked={selectedCacheJobIds.has(job.job_id)} onChange={(event) => setSelectedCacheJobIds((current) => { const next = new Set(current); event.target.checked ? next.add(job.job_id) : next.delete(job.job_id); return next; })} />}<span className="cast-dot cast-dot--teal" /><strong>{speakerForSegment(segment)}</strong>{usesRvc(segment) && <span><Check size={11} />RVC</span>}</div><span className="render-time">{job?.status === "failed" ? job.error ?? "生成失败" : isPending(job) ? `${job?.progress ?? 0}% · ${job?.message}` : resultUrl ? "已缓存" : "待生成"}</span></header><p>{segment.text}</p>{job && <div className={`job-progress job-progress--${job.status}`}><span style={{ width: `${job.progress}%` }} /><small>{job.message}</small></div>}{resultUrl && <Waveform src={resultUrl} color={usesRvc(segment) ? "violet" : "teal"} />}{resultUrl && <AudioPlayer src={resultUrl} label={`${speakerForSegment(segment)} 极速结果试听`} className="result-audio-player" />}<footer><button className="text-button" disabled={Boolean(streamView) || isPending(job)} onClick={() => void regenerate(segment)}><RefreshCw size={13} />重新生成</button><span className="spacer" />{resultUrl && job && <button className="icon-button cache-delete-button" title="删除本句缓存" disabled={busy === "cache" || Boolean(streamView)} onClick={() => void removeCache([job.job_id])}><Trash2 size={15} /></button>}{resultUrl ? <a className="icon-button" title="下载音频" href={resultUrl} download={`${segment.segment_id}-fast.wav`}><Download size={15} /></a> : <button className="icon-button" disabled title="尚无音频"><Download size={15} /></button>}</footer></article>;
          })}
        </div>
        <footer className="export-bar"><div><Zap size={16} /><span>已完成 {completedSegments.length} / {segments.length}</span>{mergedAudio && <strong>{mergedAudio.duration_seconds.toFixed(1)} 秒</strong>}</div><div>{mergedAudio && <a className="icon-button" title="下载合并音频" href={mergedAudio.output_url} download={`${selectedSource?.display_name ?? "project"}-fast.wav`}><Download size={15} /></a>}<button className="secondary-button" disabled={!completedJobIds.length || busy === "merge"} onClick={() => void mergeCompleted()}>{busy === "merge" ? <LoaderCircle className="spin" size={14} /> : null}{mergedAudio ? "重新合并" : "合并音频"}</button></div></footer>
      </aside>
    </section>
  );
}
