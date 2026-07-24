import { ArrowRight, Check, CircleAlert, FileText, LoaderCircle, Lock, Mic2, Play, Plus, RefreshCw, RotateCcw, Save, SlidersHorizontal, Unlock, Upload, Users } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createAudioJob, fetchAudioJob, fetchPreparationPreview, fetchSources, importTxtSource, runPreparationAction, updateAutomaticReferenceLock, updateReferenceSelection, updateReferenceThreshold, updateReferenceVoicePrompt } from "./api";
import type {
  AudioJob,
  PreparationAction,
  PreparationPreview,
  ProductionStageId,
  ReferencePlanItem,
  SourceSummary,
} from "./types";

type ProjectPreparationStage = "source" | "casting" | "references" | "director";

interface ProjectPreparationWorkspaceProps {
  activeStage: ProjectPreparationStage;
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

const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败，请检查后端服务";
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

export function ProjectPreparationWorkspace({ activeStage, onStageChange }: ProjectPreparationWorkspaceProps) {
  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreparationPreview | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [referenceJobs, setReferenceJobs] = useState<Record<string, AudioJob>>({});
  const [thresholdPercent, setThresholdPercent] = useState(10);
  const [voicePromptDraft, setVoicePromptDraft] = useState("");
  const [feedback, setFeedback] = useState("选择 TXT 后开始准备流程");
  const [showPreview, setShowPreview] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const selectedSource = sources.find((source) => source.project_id === selectedProjectId) ?? null;
  const candidates = preview?.analysis_audit?.candidates ?? [];
  const segments = preview?.director_doc?.segments ?? [];
  const referenceItems = preview?.reference_plan?.items ?? [];
  const selectedReferences = referenceItems.filter((item) => item.selected);
  const selectedSegment = segments[selectedIndex] ?? null;
  const selectedReference = (activeStage === "references" ? selectedReferences : referenceItems)[selectedIndex] ?? null;

  useEffect(() => {
    let active = true;
    setBusy("refresh");
    fetchSources().then((items) => {
      if (!active) return;
      setSources(items);
      setSelectedProjectId((current) => current && items.some((item) => item.project_id === current) ? current : items[0]?.project_id ?? null);
      setFeedback(items.length ? `已载入 ${items.length} 个 TXT` : "尚无 TXT，请先导入小说");
    }).catch((error: unknown) => {
      if (active) setFeedback(errorMessage(error));
    }).finally(() => {
      if (active) setBusy(null);
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!selectedProjectId) {
      setPreview(null);
      return;
    }
    let active = true;
    fetchPreparationPreview(selectedProjectId).then((next) => {
      if (active) setPreview(next);
    }).catch((error: unknown) => {
      if (active) setFeedback(errorMessage(error));
    });
    return () => { active = false; };
  }, [selectedProjectId]);

  useEffect(() => {
    setSelectedIndex(0);
    setShowPreview(false);
  }, [activeStage, selectedProjectId]);

  useEffect(() => {
    if (preview?.reference_plan) {
      setThresholdPercent(Math.round(preview.reference_plan.automatic_threshold * 100));
    }
  }, [preview?.reference_plan?.automatic_threshold]);

  useEffect(() => {
    setVoicePromptDraft(selectedReference?.voice_prompt ?? "");
  }, [selectedReference?.reference_id, selectedReference?.voice_prompt]);

  async function refresh(): Promise<void> {
    setBusy("refresh");
    try {
      const items = await fetchSources();
      setSources(items);
      const projectId = selectedProjectId && items.some((item) => item.project_id === selectedProjectId)
        ? selectedProjectId
        : items[0]?.project_id ?? null;
      setSelectedProjectId(projectId);
      if (projectId) setPreview(await fetchPreparationPreview(projectId));
      setFeedback("源文件与产物状态已刷新");
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
    }
  }

  async function upload(file: File): Promise<void> {
    setBusy("upload");
    try {
      const source = await importTxtSource(file);
      const items = await fetchSources();
      setSources(items);
      setSelectedProjectId(source.project_id);
      setPreview(await fetchPreparationPreview(source.project_id));
      setFeedback(`${source.file_name} 已导入，编码 ${source.encoding.toUpperCase()}`);
    } catch (error) {
      setFeedback(errorMessage(error));
    } finally {
      setBusy(null);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function runAction(action: PreparationAction): Promise<void> {
    if (!selectedProjectId) {
      setFeedback("请先选择或导入 TXT");
      return;
    }
    setBusy(action);
    setFeedback(`${actionLabel[action]}进行中`);
    try {
      const next = await runPreparationAction(selectedProjectId, action);
      setPreview(next);
      setSources((current) => current.map((source) => source.project_id === next.project_id ? next.source : source));
      setFeedback(`${actionLabel[action]}完成，产物已写入项目目录`);
      if (action === "generate_director") setShowPreview(true);
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

  const stageTitle = activeStage === "source" ? "小说导入" : activeStage === "casting" ? "角色候选审核" : activeStage === "references" ? "中性标准参考" : "逐句导演";
  const stageEyebrow = activeStage === "source" ? "SOURCE TEXT" : activeStage === "casting" ? "CAST AUDIT" : activeStage === "references" ? "CANONICAL REFERENCES" : "DIRECTOR DOCUMENT";
  const characterNames = new Map(preview?.character_voice_bible?.characters.map((character) => [character.character_id, character.display_name]));
  const selectedPreparedCharacter = preview?.character_voice_bible?.characters.find((character) => character.character_id === selectedReference?.source_character_id) ?? null;
  const selectedReferenceCandidate = candidates.find((candidate) => candidate.display_name === selectedReference?.display_name) ?? null;
  const isBusy = busy !== null;

  const sourceFields = selectedSource ? [
    { label: "文件大小", value: formatBytes(selectedSource.size_bytes) },
    { label: "文本编码", value: selectedSource.encoding.toUpperCase() },
    { label: "当前状态", value: statusLabel[selectedSource.status] },
    { label: "正文字符", value: preview?.analysis_audit?.structure.character_count.toLocaleString() ?? "分析后生成" },
    { label: "章节识别", value: preview?.analysis_audit ? `${preview.analysis_audit.structure.chapter_count} 章` : "分析后生成" },
    { label: "预计片段", value: preview?.analysis_audit ? `${preview.analysis_audit.structure.estimated_segment_count} 句` : "分析后生成" },
  ] : [];

  const fallbackReference = referenceItems.find((item) => item.reference_id === selectedReference?.reuse_reference_id) ?? null;
  const castingFields = selectedReference ? [
    { label: "角色", value: selectedReference.display_name },
    { label: "识别性别", value: genderLabel[selectedReference.gender] },
    { label: "角色权重", value: `${Math.round(selectedReference.importance * 100)}%` },
    { label: "生成规则", value: selectedReference.selection_mode === "narrator_default" ? "默认生成并锁定" : selectedReference.selection_mode === "automatic" ? `达到 ${Math.round((preview?.reference_plan?.automatic_threshold ?? 0.75) * 100)}% 阈值，自动生成` : selectedReference.selected ? "用户已加入生成" : "低于阈值，复用旁白" },
    { label: "未生成时复用", value: fallbackReference?.display_name ?? "独立参考" },
    { label: "声线描述", value: selectedReference.voice_prompt },
  ] : [];

  const currentReferenceJob = selectedReference ? referenceJobs[selectedReference.reference_id] : null;
  const referenceFields = selectedReference ? [
    { label: "生成后端", value: "VoxCPM2" },
    { label: "当前状态", value: currentReferenceJob?.message ?? referenceStatusLabel[selectedReference.status] },
    { label: "任务进度", value: currentReferenceJob ? `${currentReferenceJob.progress}%` : selectedReference.status === "generated" ? "100%" : "尚未提交" },
    { label: "参考用途", value: selectedReference.selection_mode === "narrator_default" ? "默认旁白与低权重角色复用" : "角色中性身份锚点" },
  ] : [];

  const directorFields = selectedSegment ? [
    { label: "片段编号", value: selectedSegment.segment_id },
    { label: "稳定角色 ID", value: selectedSegment.character_id },
    { label: "角色", value: characterNames.get(selectedSegment.character_id) ?? selectedSegment.character_id },
    { label: "类型 / 情绪", value: `${selectedSegment.segment_type} / ${selectedSegment.direction.emotion}` },
    { label: "句后停顿", value: `${selectedSegment.direction.pause_after_ms} ms` },
  ] : [];

  const fields = activeStage === "source" ? sourceFields : activeStage === "casting" ? castingFields : activeStage === "references" ? referenceFields : directorFields;
  const canExtract = preview?.analysis_audit !== null && preview?.analysis_audit !== undefined;
  const canGenerateDirector = preview?.character_voice_bible !== null && preview?.character_voice_bible !== undefined;
  const canAdvanceSource = canGenerateDirector && preview?.reference_plan !== null;
  const generatedReferenceCount = selectedReferences.filter((item) => item.status === "generated").length;
  const allReferencesGenerated = selectedReferences.length > 0 && generatedReferenceCount === selectedReferences.length;

  return (
    <section className="prep-workspace project-preparation">
      <aside className="prep-list-pane">
        <div className="pane-heading"><div><span className="eyebrow">{stageEyebrow}</span><h2>{stageTitle}</h2></div><span className="list-count">{activeStage === "source" ? sources.length : activeStage === "casting" ? referenceItems.length : activeStage === "references" ? selectedReferences.length : segments.length}</span></div>
        <div className="prep-list">
          {activeStage === "source" && sources.map((source) => (
            <button key={source.project_id} className={source.project_id === selectedProjectId ? "selected" : ""} onClick={() => setSelectedProjectId(source.project_id)}>
              <FileText size={16} /><span><strong>{source.file_name}</strong><small>{formatBytes(source.size_bytes)} · {source.encoding.toUpperCase()}</small></span><em>{statusLabel[source.status]}</em>
            </button>
          ))}
          {activeStage === "casting" && referenceItems.map((item, index) => (
            <div key={item.reference_id} className={`reference-cast-row ${index === selectedIndex ? "selected" : ""}`}>
              <button className="reference-character-select" onClick={() => setSelectedIndex(index)}>
                <Users size={16} /><span><strong>{item.display_name}</strong><small>{genderLabel[item.gender]} · 权重 {Math.round(item.importance * 100)}%</small></span><em>{item.selection_mode === "automatic" ? item.selected ? "自动已选" : "自动未选" : item.selection_mode === "narrator_default" ? "默认" : item.selected ? "已选择" : "复用旁白"}</em>
              </button>
              <button className={`reference-select-toggle ${item.selected ? "active" : ""}`} title={item.selection_mode === "narrator_default" ? "默认旁白始终生成" : item.locked ? "权重达标角色当前已锁定" : item.selected ? "取消独立参考，改用旁白" : "加入 VoxCPM2 参考生成"} disabled={isBusy || item.locked} onClick={() => void toggleReference(item)}>
                {item.locked ? <Lock size={13} /> : item.selected ? <Check size={14} /> : <Plus size={14} />}
              </button>
            </div>
          ))}
          {activeStage === "references" && selectedReferences.map((item, index) => (
            <button key={item.reference_id} className={index === selectedIndex ? "selected" : ""} onClick={() => setSelectedIndex(index)}>
              <Mic2 size={16} /><span><strong>{item.display_name}</strong><small>{genderLabel[item.gender]} · VoxCPM2 中性参考</small></span><em>{referenceStatusLabel[item.status]}</em>
            </button>
          ))}
          {activeStage === "director" && segments.slice(0, 500).map((segment, index) => (
            <button key={segment.segment_id} className={index === selectedIndex ? "selected" : ""} onClick={() => setSelectedIndex(index)}>
              <SlidersHorizontal size={16} /><span><strong>{segment.segment_id} · {characterNames.get(segment.character_id) ?? segment.character_id}</strong><small>{segment.text}</small></span><em>{segment.direction.emotion}</em>
            </button>
          ))}
          {!isBusy && ((activeStage === "source" && !sources.length) || (activeStage === "casting" && !referenceItems.length) || (activeStage === "references" && !selectedReferences.length) || (activeStage === "director" && !segments.length)) && <p className="empty-state">当前阶段还没有可显示的数据。</p>}
          {busy === "refresh" && activeStage !== "source" && <p className="empty-state"><LoaderCircle className="spin" size={15} />正在读取产物</p>}
        </div>
        {activeStage === "source" && (
          <>
            <input ref={inputRef} className="hidden-file-input" type="file" accept=".txt,text/plain" aria-hidden="true" tabIndex={-1} onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); }} />
            <button className="import-button" disabled={isBusy} onClick={() => inputRef.current?.click()}><Upload size={15} />添加 TXT</button>
          </>
        )}
      </aside>

      <section className="prep-main-pane">
        <div className="prep-titlebar">
          <div><span className="eyebrow">CURRENT PROJECT</span><h2>{activeStage === "source" ? selectedSource?.file_name ?? "等待导入" : activeStage === "casting" || activeStage === "references" ? selectedReference?.display_name ?? "等待角色提取" : selectedSegment?.segment_id ?? "等待导演文件"}</h2></div>
          <button className="icon-button" title="刷新源文件与产物" disabled={isBusy} onClick={() => void refresh()}>{busy === "refresh" ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}</button>
        </div>
        {activeStage === "source" && (
          <div className="preparation-actions" aria-label="小说准备操作">
            <button className="secondary-button" disabled={isBusy || !selectedSource} onClick={() => void runAction("analyze")}><FileText size={14} />分析文档</button>
            <button className="secondary-button" disabled={isBusy || !canExtract} onClick={() => void runAction("extract_characters")}><Users size={14} />提取角色</button>
            <button className="secondary-button" disabled={isBusy || !canGenerateDirector} onClick={() => void runAction("generate_director")}><SlidersHorizontal size={14} />生成导演文件</button>
            <button className="secondary-button" disabled={isBusy || !selectedSource} onClick={() => void openPreview()}><Play size={14} />预览</button>
          </div>
        )}
        {activeStage === "casting" && preview?.reference_plan && (
          <div className="casting-threshold-control">
            <div><span>自动生成权重阈值</span><strong>{thresholdPercent}% 及以上</strong></div>
            <input type="range" min="1" max="100" step="1" value={thresholdPercent} aria-label="自动生成权重阈值" disabled={isBusy} onChange={(event) => setThresholdPercent(Number(event.target.value))} onPointerUp={() => void saveReferenceThreshold()} onKeyUp={() => void saveReferenceThreshold()} />
            <button className="icon-button" title="应用权重阈值" disabled={isBusy || Math.abs(thresholdPercent / 100 - preview.reference_plan.automatic_threshold) < 0.0001} onClick={() => void saveReferenceThreshold()}><Save size={14} /></button>
            <button className={`secondary-button threshold-lock-button ${preview.reference_plan.automatic_items_locked ? "" : "active"}`} disabled={isBusy} onClick={() => void toggleAutomaticReferenceLock()}>{preview.reference_plan.automatic_items_locked ? <Unlock size={14} /> : <Lock size={14} />}{preview.reference_plan.automatic_items_locked ? "解锁选择" : "锁定选择"}</button>
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
            {activeStage === "references" && selectedReference && (
              <section className="reference-preview">
                <div className="reference-copy"><span>标准参考文本</span><p>{selectedReference.reference_text}</p></div>
                <div className="reference-voice-editor">
                  <label htmlFor="reference-voice-prompt">声线描述</label>
                  <textarea id="reference-voice-prompt" maxLength={1000} value={voicePromptDraft} disabled={isBusy} onChange={(event) => setVoicePromptDraft(event.target.value)} />
                  <div><small>{voicePromptDraft.length} / 1000</small><button className="secondary-button" disabled={isBusy || !voicePromptDraft.trim() || voicePromptDraft.trim() === selectedReference.voice_prompt} onClick={() => void saveVoicePrompt()}><Save size={14} />保存声线描述</button></div>
                </div>
                {currentReferenceJob && currentReferenceJob.status !== "complete" && (
                  <div className={`job-progress ${currentReferenceJob.status === "failed" ? "job-progress--failed" : ""}`}><span style={{ width: `${currentReferenceJob.progress}%` }} /><small>{currentReferenceJob.message}</small></div>
                )}
                {selectedReference.audio_url ? <audio controls preload="metadata" src={selectedReference.audio_url} aria-label={`${selectedReference.display_name}参考音频试听`} /> : <p className="reference-empty-audio">生成完成后可在这里试听。</p>}
                {selectedReference.error && <p className="reference-error">{selectedReference.error}</p>}
                <button className="secondary-button" disabled={isBusy} onClick={() => void generateReference(selectedReference)}>{selectedReference.status === "generated" ? <RotateCcw size={14} /> : <Mic2 size={14} />}{selectedReference.status === "generated" ? "重新生成" : "生成预览"}</button>
              </section>
            )}
            {activeStage === "director" && selectedSegment && <div className="evidence-block"><span>朗读文本</span><p>{selectedSegment.text}</p></div>}
          </>
        )}
      </section>

      <aside className="prep-check-pane">
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
          {activeStage === "director" && (
            <>
              <div><Check size={15} /><span>导演片段</span><strong>{segments.length}</strong></div>
              <div><Check size={15} /><span>角色绑定</span><strong>{segments.filter((segment) => !!characterNames.get(segment.character_id)).length} / {segments.length}</strong></div>
              <div><Check size={15} /><span>稳定 ID</span><strong>已启用</strong></div>
            </>
          )}
        </div>
        <div className={`stage-action ${activeStage === "references" ? "reference-stage-actions" : ""}`}>
          {activeStage === "source" && <button className="primary-button" disabled={!canAdvanceSource || isBusy} title={!canAdvanceSource ? "请先提取角色" : "进入角色审核"} onClick={() => onStageChange("casting")}>下一步<ArrowRight size={15} /></button>}
          {activeStage === "casting" && <button className="primary-button" disabled={!preview?.reference_plan || isBusy} onClick={() => onStageChange("references")}>进入标准参考<ArrowRight size={15} /></button>}
          {activeStage === "references" && <><button className="secondary-button" disabled={isBusy || allReferencesGenerated} onClick={() => void generateSelectedReferences()}><Mic2 size={14} />批量生成</button><button className="primary-button" disabled={isBusy || !allReferencesGenerated} onClick={() => onStageChange("emotions")}>进入情绪派生<ArrowRight size={15} /></button></>}
          {activeStage === "director" && <button className="primary-button" disabled={!preview?.director_doc || isBusy} onClick={() => onStageChange("quality_render")}>进入质量渲染<ArrowRight size={15} /></button>}
        </div>
      </aside>
    </section>
  );
}
