import { ArrowRight, Check, CircleAlert, FileText, LoaderCircle, Play, RefreshCw, SlidersHorizontal, Upload, Users } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { fetchPreparationPreview, fetchSources, importTxtSource, runPreparationAction } from "./api";
import type {
  PreparationAction,
  PreparationPreview,
  ProductionStageId,
  SourceSummary,
} from "./types";

type ProjectPreparationStage = "source" | "casting" | "director";

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
  const [busy, setBusy] = useState<PreparationAction | "upload" | "refresh" | null>(null);
  const [feedback, setFeedback] = useState("选择 TXT 后开始准备流程");
  const [showPreview, setShowPreview] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const selectedSource = sources.find((source) => source.project_id === selectedProjectId) ?? null;
  const candidates = preview?.analysis_audit?.candidates ?? [];
  const segments = preview?.director_doc?.segments ?? [];
  const selectedCandidate = candidates[selectedIndex] ?? null;
  const selectedSegment = segments[selectedIndex] ?? null;

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

  const stageTitle = activeStage === "source" ? "小说导入" : activeStage === "casting" ? "角色候选审核" : "逐句导演";
  const stageEyebrow = activeStage === "source" ? "SOURCE TEXT" : activeStage === "casting" ? "CAST AUDIT" : "DIRECTOR DOCUMENT";
  const StageIcon = activeStage === "source" ? FileText : activeStage === "casting" ? Users : SlidersHorizontal;
  const acceptedCount = candidates.filter((candidate) => candidate.decision === "accepted").length;
  const rejectedCount = candidates.filter((candidate) => candidate.decision === "rejected").length;
  const aliasNames = new Set(preview?.character_voice_bible?.characters.flatMap((character) => character.aliases) ?? []);
  const acceptedIdentityCount = preview?.character_voice_bible?.characters.filter((character) => character.character_id !== "narrator").length ?? 0;
  const characterNames = new Map(preview?.character_voice_bible?.characters.map((character) => [character.character_id, character.display_name]));
  const isBusy = busy !== null;

  const sourceFields = selectedSource ? [
    { label: "文件大小", value: formatBytes(selectedSource.size_bytes) },
    { label: "文本编码", value: selectedSource.encoding.toUpperCase() },
    { label: "当前状态", value: statusLabel[selectedSource.status] },
    { label: "正文字符", value: preview?.analysis_audit?.structure.character_count.toLocaleString() ?? "分析后生成" },
    { label: "章节识别", value: preview?.analysis_audit ? `${preview.analysis_audit.structure.chapter_count} 章` : "分析后生成" },
    { label: "预计片段", value: preview?.analysis_audit ? `${preview.analysis_audit.structure.estimated_segment_count} 句` : "分析后生成" },
  ] : [];

  const castingFields = selectedCandidate ? [
    { label: "候选角色", value: selectedCandidate.display_name },
    { label: "审核决定", value: aliasNames.has(selectedCandidate.display_name) ? "已作为别名合并" : selectedCandidate.decision === "accepted" ? "已接纳" : selectedCandidate.decision === "rejected" ? "已排除" : "待提取" },
    { label: "识别置信度", value: `${Math.round(selectedCandidate.confidence * 100)}%` },
    { label: "提及 / 对话", value: `${selectedCandidate.mention_count} / ${selectedCandidate.dialogue_count}` },
    { label: "判断依据", value: selectedCandidate.reason },
  ] : [];

  const directorFields = selectedSegment ? [
    { label: "片段编号", value: selectedSegment.segment_id },
    { label: "稳定角色 ID", value: selectedSegment.character_id },
    { label: "角色", value: characterNames.get(selectedSegment.character_id) ?? selectedSegment.character_id },
    { label: "类型 / 情绪", value: `${selectedSegment.segment_type} / ${selectedSegment.direction.emotion}` },
    { label: "句后停顿", value: `${selectedSegment.direction.pause_after_ms} ms` },
  ] : [];

  const fields = activeStage === "source" ? sourceFields : activeStage === "casting" ? castingFields : directorFields;
  const canExtract = preview?.analysis_audit !== null && preview?.analysis_audit !== undefined;
  const canGenerateDirector = preview?.character_voice_bible !== null && preview?.character_voice_bible !== undefined;
  const canAdvanceSource = canGenerateDirector;

  return (
    <section className="prep-workspace project-preparation">
      <aside className="prep-list-pane">
        <div className="pane-heading"><div><span className="eyebrow">{stageEyebrow}</span><h2>{stageTitle}</h2></div><span className="list-count">{activeStage === "source" ? sources.length : activeStage === "casting" ? candidates.length : segments.length}</span></div>
        <div className="prep-list">
          {activeStage === "source" && sources.map((source) => (
            <button key={source.project_id} className={source.project_id === selectedProjectId ? "selected" : ""} onClick={() => setSelectedProjectId(source.project_id)}>
              <FileText size={16} /><span><strong>{source.file_name}</strong><small>{formatBytes(source.size_bytes)} · {source.encoding.toUpperCase()}</small></span><em>{statusLabel[source.status]}</em>
            </button>
          ))}
          {activeStage === "casting" && candidates.map((candidate, index) => (
            <button key={candidate.candidate_id} className={index === selectedIndex ? "selected" : ""} onClick={() => setSelectedIndex(index)}>
              <Users size={16} /><span><strong>{candidate.display_name}</strong><small>提及 {candidate.mention_count} · 对话 {candidate.dialogue_count}</small></span><em>{aliasNames.has(candidate.display_name) ? "已合并" : candidate.decision === "accepted" ? "已接纳" : candidate.decision === "rejected" ? "已排除" : "待提取"}</em>
            </button>
          ))}
          {activeStage === "director" && segments.slice(0, 500).map((segment, index) => (
            <button key={segment.segment_id} className={index === selectedIndex ? "selected" : ""} onClick={() => setSelectedIndex(index)}>
              <SlidersHorizontal size={16} /><span><strong>{segment.segment_id} · {characterNames.get(segment.character_id) ?? segment.character_id}</strong><small>{segment.text}</small></span><em>{segment.direction.emotion}</em>
            </button>
          ))}
          {!isBusy && ((activeStage === "source" && !sources.length) || (activeStage === "casting" && !candidates.length) || (activeStage === "director" && !segments.length)) && <p className="empty-state">当前阶段还没有可显示的数据。</p>}
          {isBusy && activeStage !== "source" && <p className="empty-state"><LoaderCircle className="spin" size={15} />正在读取产物</p>}
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
          <div><span className="eyebrow">CURRENT PROJECT</span><h2>{activeStage === "source" ? selectedSource?.file_name ?? "等待导入" : activeStage === "casting" ? selectedCandidate?.display_name ?? "等待角色提取" : selectedSegment?.segment_id ?? "等待导演文件"}</h2></div>
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
        <div className="operation-feedback" role="status">{isBusy && <LoaderCircle className="spin" size={14} />}{feedback}</div>
        {showPreview && preview ? <PreviewPanel preview={preview} /> : (
          <>
            <div className="field-table">
              {fields.map((field) => <div key={field.label}><span>{field.label}</span><strong>{field.value}</strong></div>)}
              {!fields.length && <p className="empty-state">完成前置操作后，这里会显示真实产物字段。</p>}
            </div>
            {activeStage === "casting" && selectedCandidate && <div className="evidence-block"><span>证据摘录</span>{selectedCandidate.evidence.length ? selectedCandidate.evidence.map((evidence, index) => <p key={`${selectedCandidate.candidate_id}:${index}`}>{evidence}</p>) : <p>没有直接对话证据。</p>}</div>}
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
              <div><Check size={15} /><span>候选总数</span><strong>{candidates.length}</strong></div>
              <div><Check size={15} /><span>角色身份</span><strong>{acceptedIdentityCount}</strong></div>
              <div><Check size={15} /><span>别名合并</span><strong>{acceptedCount - acceptedIdentityCount}</strong></div>
              <div className={rejectedCount ? "attention" : ""}>{rejectedCount ? <CircleAlert size={15} /> : <Check size={15} />}<span>误判排除</span><strong>{rejectedCount}</strong></div>
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
        <div className="stage-action">
          {activeStage === "source" && <button className="primary-button" disabled={!canAdvanceSource || isBusy} title={!canAdvanceSource ? "请先提取角色" : "进入角色审核"} onClick={() => onStageChange("casting")}>下一步<ArrowRight size={15} /></button>}
          {activeStage === "casting" && <button className="primary-button" disabled={!canGenerateDirector || isBusy} onClick={() => onStageChange("references")}>进入标准参考<ArrowRight size={15} /></button>}
          {activeStage === "director" && <button className="primary-button" disabled={!preview?.director_doc || isBusy} onClick={() => onStageChange("quality_render")}>进入质量渲染<ArrowRight size={15} /></button>}
        </div>
      </aside>
    </section>
  );
}
