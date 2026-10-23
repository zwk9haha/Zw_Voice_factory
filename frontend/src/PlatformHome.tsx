import { ArrowRight, BookOpenText, CheckCircle2, FileAudio, FileText, GitBranch, Library, LoaderCircle, RefreshCw, Sparkles, WandSparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { acceptProjectVoiceSnapshot, createPlatformProject, createQuickVoicePreview, fetchPlatformProjectWorkspace, matchOrGenerateVoice, startVoiceDecisionGeneration } from "./api";
import { ProjectWorkflowPanel } from "./ProjectWorkflowPanel";
import { WorkflowProjectExperience } from "./WorkflowProjectExperience";
import type { PlatformProject, PlatformProjectType, ProjectVoiceDecision, PlatformTextSegment, VoiceTask } from "./types";

interface PlatformHomeProps {
  projects: PlatformProject[];
  loading: boolean;
  onRefresh: () => void;
  onOpenNovel: (projectId: string) => void;
  onProjectCreated: (project: PlatformProject) => void;
  onOpenCatalog: () => void;
  onOpenShortText: () => void;
  onOpenWorkflowStudio: (projectId?: string) => void;
}

const typeLabels: Record<PlatformProjectType, string> = {
  short_text: "短文本",
  novel: "小说",
  script: "剧本/对白",
  subtitle: "字幕",
  document: "普通文档",
};

function readExperience(): { projectId: string | null } | null {
  const match = window.location.hash.match(/^#workflow-ui\/standard(?:\/([^/]*))?$/);
  if (!match) return null;
  try { return { projectId: match[1] ? decodeURIComponent(match[1]) : null }; } catch { return null; }
}

const typeIcons: Record<PlatformProjectType, typeof FileText> = {
  short_text: WandSparkles,
  novel: BookOpenText,
  script: FileAudio,
  subtitle: FileText,
  document: FileText,
};

function formatLength(length: number): string {
  return length >= 10_000 ? `${(length / 10_000).toFixed(1)} 万字` : `${length.toLocaleString()} 字`;
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function PlatformHome({ projects, loading, onRefresh, onOpenNovel, onProjectCreated, onOpenCatalog, onOpenShortText, onOpenWorkflowStudio }: PlatformHomeProps) {
  const [name, setName] = useState("");
  const [contentType, setContentType] = useState<PlatformProjectType>("short_text");
  const [text, setText] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [workflowProjectId, setWorkflowProjectId] = useState<string | null>(null);
  const [task, setTask] = useState<VoiceTask | null>(null);
  const [decisions, setDecisions] = useState<ProjectVoiceDecision[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [experience, setExperience] = useState(readExperience);
  useEffect(() => {
    const update = () => setExperience(readExperience());
    window.addEventListener("popstate", update);
    window.addEventListener("hashchange", update);
    return () => { window.removeEventListener("popstate", update); window.removeEventListener("hashchange", update); };
  }, []);
  function openExperience(projectId: string | null = null) {
    window.history.pushState(null, "", `#workflow-ui/standard/${projectId ? encodeURIComponent(projectId) : ""}`);
    setExperience({ projectId });
  }
  function closeExperience() {
    window.history.pushState(null, "", `${window.location.pathname}${window.location.search}`);
    setExperience(null);
  }

  useEffect(() => {
    setSelectedProjectId((current) => current && projects.some((project) => project.project_id === current) ? current : null);
    setWorkflowProjectId((current) => current && projects.some((project) => project.project_id === current) ? current : null);
  }, [projects]);

  async function createProject(): Promise<void> {
    if (!name.trim() || !text.trim()) {
      setMessage("请填写项目名称和文本内容");
      return;
    }
    setBusy("create");
    setMessage("");
    try {
      const project = await createPlatformProject(name.trim(), contentType, text.trim());
      onProjectCreated(project);
      setSelectedProjectId(project.project_id);
      setName("");
      setText("");
      setMessage("项目已创建，可以开始分析");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "项目创建失败");
    } finally {
      setBusy(null);
    }
  }

  async function analyze(project: PlatformProject): Promise<void> {
    if (project.content_type !== "short_text") return;
    setSelectedProjectId(project.project_id);
    setBusy(`analyze:${project.project_id}`);
    setMessage("");
    try {
      const workspace = await fetchPlatformProjectWorkspace(project.project_id);
      const latest = workspace.tasks[0];
      if (latest) {
        setTask(latest);
      } else {
        setTask(await createQuickVoicePreview(project.project_id));
      }
      setDecisions(workspace.voice_decisions ?? []);
      setMessage("文本分析完成，请检查说话人分配");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "文本分析失败");
    } finally {
      setBusy(null);
    }
  }

  function decisionFor(segment: PlatformTextSegment): ProjectVoiceDecision | undefined {
    return decisions.find((item) => item.speaker_key === segment.speaker.speaker_name);
  }

  async function decide(project: PlatformProject, segment: PlatformTextSegment): Promise<void> {
    const key = `decide:${segment.speaker.speaker_name}`;
    setSelectedProjectId(project.project_id);
    setBusy(key);
    setMessage("");
    try {
      const decision = await matchOrGenerateVoice(project.project_id, {
        speaker_key: segment.speaker.speaker_name,
        display_name: segment.speaker.speaker_name,
        minimum_score: 0.58,
      });
      setDecisions((current) => [decision, ...current.filter((item) => item.speaker_key !== decision.speaker_key)]);
      setMessage(decision.action === "catalog_match" ? "已找到声音库候选，请明确接受后绑定项目" : decision.action === "reuse_snapshot" ? "已复用项目声音快照" : "没有合适的已完成声音，请点击生成候选");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "声音决策失败");
    } finally {
      setBusy(null);
    }
  }

  async function generateCandidate(project: PlatformProject, decision: ProjectVoiceDecision): Promise<void> {
    setBusy(`generate:${decision.decision_id}`);
    try {
      await startVoiceDecisionGeneration(project.project_id, decision.decision_id);
      for (let attempt = 0; attempt < 30; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 300));
        const workspace = await fetchPlatformProjectWorkspace(project.project_id);
        setDecisions(workspace.voice_decisions ?? []);
        const updated = (workspace.voice_decisions ?? []).find((item) => item.decision_id === decision.decision_id);
        if (updated?.generation?.status === "complete" || updated?.generation?.status === "failed") break;
      }
      setMessage("候选声音已生成，请审核后接受为项目声音");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "候选声音生成失败");
    } finally {
      setBusy(null);
    }
  }

  async function acceptCatalogCandidate(project: PlatformProject, decision: ProjectVoiceDecision): Promise<void> {
    const selected = decision.selected_match;
    if (!selected?.version_id) return;
    setBusy(`accept:${decision.decision_id}`);
    try {
      await acceptProjectVoiceSnapshot(project.project_id, {
        speaker_key: decision.speaker_key,
        display_name: decision.display_name,
        source: "catalog",
        catalog_asset_id: selected.asset_id,
        catalog_version_id: selected.version_id,
        match_score: selected.score,
        match_reasons: selected.reasons,
      });
      const workspace = await fetchPlatformProjectWorkspace(project.project_id);
      setDecisions(workspace.voice_decisions ?? []);
      setMessage("声音已绑定到项目快照，后续目录更新不会静默替换它");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "绑定项目声音失败");
    } finally {
      setBusy(null);
    }
  }

  if (experience) return <WorkflowProjectExperience key={experience.projectId ?? "select"} projects={projects} initialProjectId={experience.projectId} onProjectCreated={onProjectCreated} onProjectChange={openExperience} onBack={closeExperience} onOpenEditor={onOpenWorkflowStudio} />;

  return (
    <section className="platform-home">
      <header className="platform-hero">
        <div><span className="eyebrow">VOICE PLATFORM</span><h1>音声生产平台</h1><p>从短文本即时试听，到长篇小说连续生产。先复用已有声音，缺少时再生成。</p></div>
        <div className="platform-hero-actions"><button className="secondary-button" onClick={() => onOpenWorkflowStudio()}><GitBranch size={14} />工作流编排</button><button className="secondary-button" onClick={onOpenCatalog}><Library size={14} />基础音色库</button><button className="secondary-button" onClick={onRefresh} disabled={loading}><RefreshCw size={14} className={loading ? "spin" : ""} />刷新项目</button></div>
      </header>

      <div className="platform-ui-experiments"><button className="platform-ui-entry" onClick={() => openExperience()}><strong>A · 统一项目工作台</strong><span>按制作顺序导入、审核、生成并导出；工作流决定所需操作。</span></button></div>
      <div className="platform-home-grid">
        <section className="platform-create-card">
          <div className="platform-card-heading"><div><span className="eyebrow">NEW VOICE PROJECT</span><h2>新建音声项目</h2></div><button className="text-button platform-short-text-link" onClick={onOpenShortText}><WandSparkles size={13} />短文本配置</button></div>
          <label><span>项目名称</span><input value={name} maxLength={120} placeholder="例如：第三章 · 对话试听" onChange={(event) => setName(event.target.value)} /></label>
          <label><span>内容类型</span><select value={contentType} onChange={(event) => setContentType(event.target.value as PlatformProjectType)}>{Object.entries(typeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="platform-textarea-label"><span>输入文本</span><textarea value={text} rows={8} placeholder="输入一段旁白或对白，平台会先分析说话人和表达要求" onChange={(event) => setText(event.target.value)} /></label>
          <footer><small>{text.length.toLocaleString()} 字</small><button className="primary-button" onClick={() => void createProject()} disabled={busy === "create"}>{busy === "create" ? <LoaderCircle size={14} className="spin" /> : <Sparkles size={14} />}创建项目</button></footer>
          {message && <p className="platform-feedback">{message}</p>}
        </section>

        <section className="platform-projects-card">
          <div className="platform-card-heading"><div><span className="eyebrow">PROJECTS</span><h2>项目工作台</h2></div><strong>{projects.length}</strong></div>
          {projects.length ? <div className="platform-project-list">{projects.map((project) => {
            const Icon = typeIcons[project.content_type];
            const selected = selectedProjectId === project.project_id;
            return <article className={`platform-project-card ${selected ? "selected" : ""}`} key={project.project_id}>
              <div className="platform-project-icon"><Icon size={17} /></div><div className="platform-project-copy"><strong>{project.name}</strong><span>{typeLabels[project.content_type]} · {formatLength(project.source_text_length)} · {formatTime(project.updated_at)}</span></div>
              <div className="platform-project-actions">{project.content_type === "novel" ? <button className="icon-button" title="进入小说工作台" onClick={() => onOpenNovel(project.project_id)}><ArrowRight size={15} /></button> : <button className="secondary-button platform-analyze-button" disabled={busy === `analyze:${project.project_id}`} onClick={() => void analyze(project)}>{busy === `analyze:${project.project_id}` ? <LoaderCircle size={14} className="spin" /> : <Sparkles size={14} />}分析</button>}<button className="secondary-button platform-workflow-button" onClick={() => setWorkflowProjectId((current) => current === project.project_id ? null : project.project_id)}><GitBranch size={13} />工作流</button></div>
            </article>;
          })}</div> : <div className="platform-empty"><WandSparkles size={24} /><strong>还没有音声项目</strong><span>从左侧输入一段文本，或导入小说进入长篇流程。</span></div>}
        </section>
      </div>

      {workflowProjectId && projects.find((project) => project.project_id === workflowProjectId) && <ProjectWorkflowPanel key={workflowProjectId} project={projects.find((project) => project.project_id === workflowProjectId)!} onOpenEditor={onOpenWorkflowStudio} />}

      {task && <section className="platform-task-card"><header><div><span className="eyebrow">QUICK TASK · READY FOR REVIEW</span><h2>分析结果</h2></div><span>{task.segment_count} 个文本片段</span></header><div className="platform-segment-list">{task.segments.map((segment) => <article key={segment.segment_id}><div><strong>{segment.speaker.speaker_name}</strong><span>{segment.speaker.speaker_kind === "narration" ? "旁白" : segment.speaker.speaker_kind === "named" ? "角色" : "待确认"} · {segment.emotion}</span></div><p>{segment.text}</p><small>{segment.speaker.voice_status === "unmatched" ? "暂无匹配声音，后续进入生成候选" : "已有通用声音候选"}</small></article>)}</div></section>}
      {task && selectedProjectId && <section className="platform-task-card platform-decision-card"><header><div><span className="eyebrow">MATCH OR GENERATE</span><h2>声音决策</h2></div><span>明确接受后才会绑定项目</span></header><div className="platform-decision-list">{Array.from(new Map(task.segments.map((segment) => [segment.speaker.speaker_name, segment])).values()).map((segment) => {
        const decision = decisionFor(segment);
        const decisionBusy = busy === `decide:${segment.speaker.speaker_name}`;
        const generationBusy = decision ? busy === `generate:${decision.decision_id}` : false;
        const acceptBusy = decision ? busy === `accept:${decision.decision_id}` : false;
        return <article key={segment.speaker.speaker_name} className="platform-decision-row"><div><strong>{segment.speaker.speaker_name}</strong><small>{decision ? decision.reason : "尚未执行匹配决策"}</small></div>{!decision && <button className="secondary-button" disabled={decisionBusy} onClick={() => void decide(projects.find((item) => item.project_id === selectedProjectId)!, segment)}>{decisionBusy ? <LoaderCircle size={14} className="spin" /> : <Sparkles size={14} />}匹配声音</button>}{decision?.action === "catalog_match" && decision.selected_match && <button className="secondary-button" disabled={acceptBusy} onClick={() => void acceptCatalogCandidate(projects.find((item) => item.project_id === selectedProjectId)!, decision)}>{acceptBusy ? <LoaderCircle size={14} className="spin" /> : <CheckCircle2 size={14} />}接受候选</button>}{decision?.action === "generate_candidate" && <button className="secondary-button" disabled={generationBusy} onClick={() => void generateCandidate(projects.find((item) => item.project_id === selectedProjectId)!, decision)}>{generationBusy ? <LoaderCircle size={14} className="spin" /> : <WandSparkles size={14} />}生成候选</button>}{decision?.action === "generation_started" && <span className="platform-decision-status">生成状态：{decision.generation?.status === "complete" ? "待审核" : decision.generation?.status === "failed" ? "失败" : "处理中"}</span>}{decision?.action === "reuse_snapshot" && <span className="platform-decision-status">已复用项目快照</span>}</article>;
      })}</div></section>}
    </section>
  );
}
