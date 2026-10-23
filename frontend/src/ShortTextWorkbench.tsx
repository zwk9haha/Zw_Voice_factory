import { ArrowLeft, CheckCircle2, FileText, Library, LoaderCircle, Sparkles, WandSparkles } from "lucide-react";
import { useState } from "react";
import { createPlatformProject, createQuickVoicePreview } from "./api";
import type { PlatformProject, VoiceTask } from "./types";

interface ShortTextWorkbenchProps {
  onBack: () => void;
  onOpenCatalog: () => void;
  onProjectCreated: (project: PlatformProject) => void;
}

export function ShortTextWorkbench({ onBack, onOpenCatalog, onProjectCreated }: ShortTextWorkbenchProps) {
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [analyzeAfterCreate, setAnalyzeAfterCreate] = useState(true);
  const [createdProject, setCreatedProject] = useState<PlatformProject | null>(null);
  const [task, setTask] = useState<VoiceTask | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function createProject(): Promise<void> {
    if (!name.trim() || !text.trim()) {
      setMessage("请填写项目名称和短文本内容");
      return;
    }
    setBusy(true);
    setMessage("");
    setTask(null);
    try {
      const project = await createPlatformProject(name.trim(), "short_text", text.trim());
      onProjectCreated(project);
      setCreatedProject(project);
      setName("");
      setText("");
      if (analyzeAfterCreate) {
        setMessage("项目已创建，正在分析说话人和情绪…");
        setTask(await createQuickVoicePreview(project.project_id));
        setMessage("分析完成，可以检查说话人分配并继续生成");
      } else {
        setMessage("项目已创建，可从平台首页进入分析");
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "短文本项目创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="short-text-workbench">
      <header className="short-text-header">
        <div className="short-text-title">
          <button className="icon-button" title="返回平台首页" onClick={onBack}><ArrowLeft size={17} /></button>
          <div><span className="eyebrow">SHORT TEXT CONFIGURATION</span><h1>短文本配置</h1><p>为几句旁白或对白快速建立音声项目，先分析说话人，再复用或生成合适的声音。</p></div>
        </div>
        <button className="secondary-button" onClick={onOpenCatalog}><Library size={14} />浏览基础音色库</button>
      </header>

      <div className="short-text-layout">
        <section className="short-text-config-card">
          <div className="short-text-card-heading"><div><span className="eyebrow">VOICE PROJECT</span><h2>创建短文本项目</h2></div><FileText size={18} /></div>
          <div className="short-text-type-badge"><WandSparkles size={15} /><span><strong>短文本</strong><small>即时分析 · 适合试听和单段配音</small></span></div>
          <label><span>项目名称</span><input value={name} maxLength={120} placeholder="例如：片头旁白试听" onChange={(event) => setName(event.target.value)} /></label>
          <label className="short-text-content-label"><span>输入文本 <small>{text.length.toLocaleString()} 字</small></span><textarea value={text} rows={12} maxLength={200_000} placeholder="输入旁白、单人对白或多角色短对白…" onChange={(event) => setText(event.target.value)} /></label>
          <label className="short-text-checkbox"><input type="checkbox" checked={analyzeAfterCreate} onChange={(event) => setAnalyzeAfterCreate(event.target.checked)} /><span><strong>创建后立即分析</strong><small>自动识别旁白、说话人和情绪，不会直接生成音频</small></span></label>
          <footer><button className="secondary-button" onClick={onBack}>取消</button><button className="primary-button" onClick={() => void createProject()} disabled={busy}>{busy ? <LoaderCircle size={14} className="spin" /> : <Sparkles size={14} />}{analyzeAfterCreate ? "创建并分析" : "创建项目"}</button></footer>
          {message && <p className="short-text-message">{message}</p>}
        </section>

        <aside className="short-text-guide-card">
          <span className="eyebrow">QUICK PATH</span>
          <h2>短文本工作路径</h2>
          <ol>
            <li><CheckCircle2 size={16} /><span><strong>输入内容</strong><small>保留完整对白和必要的上下文。</small></span></li>
            <li><CheckCircle2 size={16} /><span><strong>分析说话人</strong><small>识别旁白、角色、情绪和置信度。</small></span></li>
            <li><CheckCircle2 size={16} /><span><strong>匹配声音</strong><small>优先使用项目或基础音色库中的资产。</small></span></li>
            <li><CheckCircle2 size={16} /><span><strong>试听生产</strong><small>确认分配后再进入质量或极速路线。</small></span></li>
          </ol>
          <button className="text-button short-text-catalog-link" onClick={onOpenCatalog}><Library size={13} />先去挑选一个基础音色</button>
        </aside>
      </div>

      {createdProject && task && <section className="short-text-result-card"><header><div><span className="eyebrow">ANALYSIS RESULT</span><h2>{createdProject.name}</h2></div><span>{task.segment_count} 个片段 · {task.message}</span></header><div className="platform-segment-list">{task.segments.map((segment) => <article key={segment.segment_id}><div><strong>{segment.speaker.speaker_name}</strong><span>{segment.speaker.speaker_kind === "narration" ? "旁白" : segment.speaker.speaker_kind === "named" ? "角色" : "待确认"} · {segment.emotion}</span></div><p>{segment.text}</p><small>{segment.speaker.voice_status === "unmatched" ? "暂无匹配声音" : "已有声音候选"}</small></article>)}</div></section>}
    </section>
  );
}
