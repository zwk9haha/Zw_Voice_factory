import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, Check, GitBranch, LoaderCircle, Play, RefreshCw, X } from "lucide-react";
import { bindProjectWorkflow, createAudioJob, createPlatformProject, createProjectWorkflowRun, createWorkflow, fetchAudioJob, fetchProjectWorkflow, fetchWorkflowNodeTypes, fetchWorkflows, publishWorkflow, reviewWorkflowNode, workflowRunAction } from "./api";
import type { AudioJob, FastVoiceOption, PlatformProject, ProjectWorkflowPayload, WorkflowDefinition, WorkflowInputDefinition, WorkflowNodeRun, WorkflowRun } from "./types";
import { UI_DEMO_PROJECT_NAME, UI_DEMO_TEXT, projectUiDemoDefinition } from "./projectWorkflowDemo";
import { WorkflowRecordInspector } from "./WorkflowRecordInspector";
import { WorkflowRunDetails } from "./WorkflowRunDetails";
import { workflowMediaUrl, workflowStatusLabels } from "./workflowTrace";
import "./workflowExperience.css";

export type ExperienceVariant = "standard" | "layout";
export type ExperiencePanelId = "inputs" | "progress" | "review" | "results" | "history";
export interface ExperiencePanel { id: ExperiencePanelId; title: string; hint: string; content: ReactNode }
interface Props {
  projects: PlatformProject[];
  initialProjectId: string | null;
  onProjectCreated: (project: PlatformProject) => void;
  onProjectChange: (projectId: string) => void;
  onBack: () => void;
  onOpenEditor: (projectId: string) => void;
  variant?: ExperienceVariant;
  renderPanels?: (panels: ExperiencePanel[], projectId: string, workflowId: string) => ReactNode;
}
const activeStatuses = ["queued", "running", "paused", "awaiting_review"];
function defaults(workflow: WorkflowDefinition | null): Record<string, unknown> {
  return Object.fromEntries((workflow?.inputs ?? []).filter((input) => input.name !== "project_id" && input.default !== null).map((input) => [input.name, input.default]));
}
function text(value: unknown): string { return typeof value === "string" ? value : value === undefined || value === null ? "" : JSON.stringify(value, null, 2); }

function InputField({ input, value, change, disabled }: { input: WorkflowInputDefinition; value: unknown; change: (value: unknown) => void; disabled: boolean }) {
  const [jsonDraft, setJsonDraft] = useState(text(value));
  const [error, setError] = useState("");
  useEffect(() => { setJsonDraft(text(value)); }, [value]);
  async function readFile(file: File) {
    try {
      const bytes = await file.arrayBuffer();
      let content: string;
      try { content = new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
      catch { content = new TextDecoder("gb18030", { fatal: true }).decode(bytes); }
      change(content.replace(/^\uFEFF/, "")); setError("");
    } catch { setError("无法读取文件，请选择 UTF-8 或 GB18030 编码的 TXT。"); }
  }
  const json = input.type === "json" || input.type === "any";
  return <div className="experience-field">
    <label><span>{input.label || input.name}{input.required ? " *" : ""}</span>
      {input.type === "boolean" ? <input type="checkbox" disabled={disabled} checked={Boolean(value)} onChange={(event) => change(event.target.checked)} />
        : input.widget === "select" ? <select disabled={disabled} value={text(value)} onChange={(event) => change(event.target.value)}><option value="">请选择</option>{input.options?.map((option) => <option key={option}>{option}</option>)}</select>
          : input.type === "number" ? <input type="number" disabled={disabled} value={typeof value === "number" ? value : ""} onChange={(event) => change(event.target.value === "" ? null : event.target.valueAsNumber)} />
            : json ? <textarea disabled={disabled} rows={5} value={jsonDraft} onChange={(event) => { setJsonDraft(event.target.value); try { change(JSON.parse(event.target.value)); setError(""); } catch { change(event.target.value); setError("请输入有效的 JSON"); } }} />
              : input.widget === "file" ? <input disabled={disabled} type="file" accept=".txt,text/plain" onChange={(event) => { const file = event.target.files?.[0]; if (file) void readFile(file); event.target.value = ""; }} />
                : input.type === "text" || input.widget === "textarea" ? <textarea disabled={disabled} rows={7} value={text(value)} onChange={(event) => change(event.target.value)} />
                  : <input disabled={disabled} value={text(value)} onChange={(event) => change(event.target.value)} />}
    </label>
    {input.type === "text" && input.widget !== "file" && <label className="experience-file"><span>从 TXT 文件载入</span><input disabled={disabled} type="file" accept=".txt,text/plain" onChange={(event) => { const file = event.target.files?.[0]; if (file) void readFile(file); event.target.value = ""; }} /></label>}
    {input.description && <small>{input.description}</small>}{error && <small role="alert">{error}</small>}
  </div>;
}

interface ReviewSegment { segment_id: string; text: string; speaker_name: string; fast_voice_id?: string }
function ReviewPanel({ run, node, voices, busy, onReview }: { run: WorkflowRun; node: WorkflowNodeRun; voices: FastVoiceOption[]; busy: boolean; onReview: (decision: "approved" | "rejected", comment: string, edits?: Record<string, unknown>) => Promise<void> }) {
  const [changes, setChanges] = useState<Record<string, string>>({});
  const [comment, setComment] = useState("");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(30);
  const segments = (node.review?.payload.segments ?? []) as ReviewSegment[];
  const kind = node.node_type === "human.speakers" ? "speakers" : node.node_type === "human.voices" ? "voices" : null;
  const rows = kind === "voices" ? [...new Map(segments.map((segment) => [segment.speaker_name, segment])).values()] : segments;
  const filtered = rows.filter((segment) => `${segment.text} ${segment.speaker_name}`.includes(query));
  const edits = kind ? { [kind]: changes } : undefined;
  return <div className="experience-review">
    <p className="experience-callout">{kind === "speakers" ? "检查每句台词属于谁。修改后点击确认，后续节点会使用校正结果。" : kind === "voices" ? "选择并试听每个角色的声音，确认后才开始整段生成。" : "工作流在此等待你的审核决定。"}</p>
    {kind ? <><label className="experience-field"><span>搜索角色或台词</span><input value={query} onChange={(event) => { setQuery(event.target.value); setLimit(30); }} /></label>
      <div className="experience-review-list">{filtered.slice(0, limit).map((segment) => {
        const id = kind === "voices" ? segment.speaker_name : segment.segment_id;
        return <div className="experience-review-row" key={id}><p>{kind === "voices" ? <strong>{segment.speaker_name}</strong> : segment.text}</p>
          {kind === "speakers" ? <label><span>说话人</span><input disabled={busy} maxLength={120} aria-label={`说话人 ${segment.segment_id}`} value={changes[id] ?? segment.speaker_name} onChange={(event) => setChanges({ ...changes, [id]: event.target.value })} /></label>
            : <><label><span>角色声线</span><select disabled={busy} aria-label={`${segment.speaker_name}的声线`} value={changes[id] ?? segment.fast_voice_id} onChange={(event) => setChanges({ ...changes, [id]: event.target.value })}>{voices.map((voice) => <option key={voice.voice_id} value={voice.voice_id}>{voice.label} · {voice.effect}</option>)}</select></label><VoicePreview key={`${id}-${changes[id] ?? segment.fast_voice_id}`} voiceId={changes[id] ?? segment.fast_voice_id!} sample={segment.text} projectId={run.project_id!} /></>}
        </div>;
      })}</div>{filtered.length > limit && <button className="secondary-button" onClick={() => setLimit(limit + 30)}>再显示 30 项</button>}</> : <WorkflowRecordInspector label="待审核候选" value={node.review?.payload} />}
    <label className="experience-field"><span>审核备注（可选）</span><textarea disabled={busy} rows={2} maxLength={1000} value={comment} onChange={(event) => setComment(event.target.value)} /></label>
    <div className="experience-actions"><button className="primary-button" disabled={busy || (kind === "speakers" && Object.values(changes).some((name) => !name.trim()))} onClick={() => void onReview("approved", comment, edits)}><Check size={14} />确认并继续</button><button className="secondary-button" disabled={busy} onClick={() => void onReview("rejected", comment)}><X size={14} />拒绝并结束</button></div>
  </div>;
}

function VoicePreview({ voiceId, sample, projectId }: { voiceId: string; sample: string; projectId: string }) {
  const [job, setJob] = useState<AudioJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    let cancelled = false; let inFlight = false;
    const timer = window.setInterval(() => {
      if (inFlight) return; inFlight = true;
      void fetchAudioJob(job.job_id).then((next) => { if (!cancelled) setJob(next); }).catch((reason) => { if (!cancelled) setError(String(reason)); }).finally(() => { inFlight = false; });
    }, 900);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [job?.job_id, job?.status]);
  async function preview() {
    setBusy(true); setError("");
    try { const next = await createAudioJob({ kind: "fast_render", text: sample.slice(0, 80), project_id: projectId, fast_voice_id: voiceId, fast_speed: 1, fast_rvc_enabled: false }); if (mounted.current) setJob(next); }
    catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : "试听失败"); }
    finally { if (mounted.current) setBusy(false); }
  }
  const url = workflowMediaUrl(job?.output_url);
  return <div className="experience-preview"><button className="secondary-button" disabled={busy || Boolean(job && ["queued", "running"].includes(job.status))} onClick={() => void preview()}><Play size={12} />{job && ["queued", "running"].includes(job.status) ? "生成试听中…" : "生成试听"}</button>{url && <audio controls preload="none" src={url} />}{(error || job?.error) && <small role="alert">{error || job?.error}</small>}</div>;
}

export function WorkflowProjectExperience({ projects, initialProjectId, onProjectCreated, onProjectChange, onBack, onOpenEditor, variant = "standard", renderPanels }: Props) {
  const [projectId, setProjectId] = useState(initialProjectId ?? projects.find((project) => project.name === UI_DEMO_PROJECT_NAME)?.project_id ?? projects[0]?.project_id ?? "");
  const [createdProject, setCreatedProject] = useState<PlatformProject | null>(null);
  const [payload, setPayload] = useState<ProjectWorkflowPayload | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [voices, setVoices] = useState<FastVoiceOption[]>([]);
  const [bindingId, setBindingId] = useState("");
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [runId, setRunId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  const requestGeneration = useRef(0);
  const allProjects = createdProject && !projects.some((project) => project.project_id === createdProject.project_id) ? [createdProject, ...projects] : projects;
  const workflow = payload?.workflow ?? null;
  const run = payload?.runs.find((item) => item.run_id === runId) ?? payload?.runs[0] ?? null;
  const runDefinition = workflows.find((item) => item.workflow_id === run?.workflow_id) ?? workflow;
  const pending = run?.node_runs.find((node) => node.status === "awaiting_review");
  const projectActive = payload?.runs.some((item) => activeStatuses.includes(item.status)) ?? false;

  useEffect(() => {
    let cancelled = false;
    void Promise.all([fetchWorkflows(), fetchWorkflowNodeTypes()]).then(([definitions, manifests]) => {
      if (cancelled) return;
      setWorkflows(definitions);
      const options = manifests.find((manifest) => manifest.type === "voice.assign_voices")?.config_schema.voice_map as { voice_options?: FastVoiceOption[] } | undefined;
      setVoices(options?.voice_options ?? []);
    }).catch((reason) => { if (!cancelled) setError(String(reason)); });
    return () => { cancelled = true; };
  }, [refresh]);
  useEffect(() => {
    const generation = ++requestGeneration.current;
    setPayload(null); setRunId(null); setError(""); setNotice(""); setValues({});
    if (!projectId) return;
    setBusy("load");
    void fetchProjectWorkflow(projectId).then((next) => {
      if (generation !== requestGeneration.current) return;
      setPayload(next); setBindingId(next.binding?.workflow_id ?? ""); setValues(defaults(next.workflow));
    }).catch((reason) => { if (generation === requestGeneration.current) setError(reason instanceof Error ? reason.message : "读取项目失败"); })
      .finally(() => { if (generation === requestGeneration.current) setBusy(null); });
    return () => { requestGeneration.current += 1; };
  }, [projectId, refresh]);
  useEffect(() => {
    if (!projectId || !payload?.runs.some((item) => activeStatuses.includes(item.status))) return;
    let cancelled = false; let inFlight = false;
    const timer = window.setInterval(() => {
      if (inFlight) return; inFlight = true;
      void fetchProjectWorkflow(projectId).then((next) => { if (!cancelled) setPayload(next); })
        .catch((reason) => { if (!cancelled) setError(`运行状态刷新失败：${String(reason)}`); }).finally(() => { inFlight = false; });
    }, 1000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [projectId, payload?.runs.some((item) => activeStatuses.includes(item.status))]);

  async function perform(name: string, action: () => Promise<void>) {
    if (busy) return;
    setBusy(name); setError(""); setNotice("");
    try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败"); } finally { setBusy(null); }
  }
  function receiveRun(next: WorkflowRun) {
    setPayload((current) => current ? { ...current, runs: [next, ...current.runs.filter((item) => item.run_id !== next.run_id)].sort((a, b) => b.created_at.localeCompare(a.created_at)) } : current);
    setRunId(next.run_id);
  }
  async function setupDemo() {
    await perform("demo", async () => {
      let project = allProjects.find((item) => item.name === UI_DEMO_PROJECT_NAME);
      if (!project) { project = await createPlatformProject(UI_DEMO_PROJECT_NAME, "script", UI_DEMO_TEXT); setCreatedProject(project); onProjectCreated(project); }
      let definition = workflows.find((item) => item.name === projectUiDemoDefinition().name && item.lifecycle === "published");
      if (!definition) { const draft = await createWorkflow(projectUiDemoDefinition()); definition = await publishWorkflow(draft.workflow_id); setWorkflows((current) => [definition!, ...current]); }
      const next = await bindProjectWorkflow(project.project_id, definition.workflow_id);
      setProjectId(project.project_id); onProjectChange(project.project_id); setPayload(next); setBindingId(definition.workflow_id); setValues(defaults(definition));
      setNotice("演示项目已就绪：运行后会依次等待角色校正和声线确认。");
    });
  }
  async function bind() {
    await perform("bind", async () => { const next = await bindProjectWorkflow(projectId, bindingId); setPayload(next); setValues(defaults(next.workflow)); setNotice("已切换项目发布版本"); });
  }
  const displayedInputs = workflow?.inputs.filter((input) => input.name !== "project_id") ?? [];
  const invalidInput = displayedInputs.some((input) => {
    const value = values[input.name];
    return (input.required && (value === undefined || value === null || value === "")) || ((input.type === "json" || input.type === "any") && typeof value === "string");
  });
  const panels: ExperiencePanel[] = [
    { id: "inputs", title: "1 · 导入与运行", hint: "输入由项目绑定的发布版本决定", content: workflow ? <>
      {displayedInputs.map((input) => <InputField key={`${workflow.workflow_id}-${input.name}`} input={input} value={values[input.name]} change={(value) => setValues((current) => ({ ...current, [input.name]: value }))} disabled={Boolean(busy)} />)}
      {!displayedInputs.length && <p>此流程使用节点内的固定输入；可在工作流画布中编辑。</p>}
      <button className="primary-button" disabled={Boolean(busy) || projectActive || invalidInput} onClick={() => void perform("run", async () => { receiveRun(await createProjectWorkflowRun(projectId, values)); setNotice("运行已启动"); })}><Play size={14} />运行当前工作流</button>
      {projectActive && <small>项目有正在进行的运行，请先完成审核或结束运行。</small>}
    </> : <p>选择已发布工作流并绑定，或点击“准备对比 Demo”。</p> },
    { id: "progress", title: "2 · 执行进度", hint: run ? `${workflowStatusLabels[run.status]} · ${run.progress}%` : "启动后显示真实节点状态", content: run ? <>
      <p>{run.message}</p><progress max={100} value={run.progress} aria-label="工作流执行进度" />
      <ol className="experience-steps">{run.node_runs.map((node) => <li key={node.node_id} data-status={node.status}><span>{runDefinition?.nodes.find((item) => item.node_id === node.node_id)?.title || node.node_type}</span><small>{workflowStatusLabels[node.status]}</small></li>)}</ol>
      {run.error && <p role="alert">{run.error}</p>}
      <div className="experience-actions">{(["pause", "resume", "retry", "cancel"] as const).filter((action) => action === "pause" ? ["queued", "running"].includes(run.status) : action === "resume" ? run.status === "paused" : action === "retry" ? run.status === "failed" : !["completed", "cancelled"].includes(run.status)).map((action) => <button key={action} className="secondary-button" disabled={Boolean(busy)} onClick={() => void perform(action, async () => receiveRun(await workflowRunAction(run.run_id, action)))}>{{ pause: "暂停", resume: "继续", retry: "重试", cancel: "取消运行" }[action]}</button>)}</div>
    </> : <p>还没有运行记录。</p> },
    { id: "review", title: "3 · 人工审核", hint: pending ? "需要你的决定" : "工作流中的人工检查点", content: run && pending ? <ReviewPanel key={`${run.run_id}-${pending.node_id}`} run={run} node={pending} voices={voices} busy={Boolean(busy)} onReview={(decision, comment, edits) => perform("review", async () => receiveRun(await reviewWorkflowNode(run.run_id, pending.node_id, decision, comment, edits)))} /> : <p>{run?.status === "completed" ? "本次运行已完成，可在历史详情查看审核记录。" : "执行到审核节点时，这里会显示角色校正或声线试听。"}</p> },
    { id: "results", title: "4 · 音频与输出", hint: "依据本次运行的输出声明展示", content: run?.status === "completed" ? <>{(runDefinition?.outputs ?? []).map((output) => {
      const value = run.outputs[output.name]; const url = workflowMediaUrl(value);
      return <div className="experience-output" key={output.name}><strong>{output.label || output.name}</strong>{url ? <><audio controls preload="none" src={url} /><a className="secondary-button" href={url} download>下载音频</a></> : typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? <p>{String(value)}</p> : <WorkflowRecordInspector label={output.label || output.name} value={value} />}</div>;
    })}{!runDefinition?.outputs.length && <p>此工作流没有声明公开输出，可展开运行详情查看节点结果。</p>}</> : <p>完成生成后，这里会显示播放器、下载入口和公开结果。</p> },
    { id: "history", title: "运行记录", hint: "A、B 两种界面访问相同项目记录", content: <>
      <label className="experience-field"><span>选择运行</span><select value={run?.run_id ?? ""} onChange={(event) => setRunId(event.target.value)}><option value="">暂无记录</option>{payload?.runs.map((item) => <option key={item.run_id} value={item.run_id}>{new Date(item.created_at).toLocaleString("zh-CN")} · {workflowStatusLabels[item.status]} · {item.run_id.slice(-6)}</option>)}</select></label>
      {run && <details><summary>节点、审核与音频任务详情</summary><WorkflowRunDetails key={run.run_id} run={run} /></details>}
    </> },
  ];
  return <section className={`workflow-experience workflow-experience--${variant}`}>
    <header className="experience-header"><div><span className="eyebrow">{variant === "standard" ? "A · STANDARD WORKSPACE" : "B · LAYOUT STUDIO"}</span><h1>{variant === "standard" ? "统一项目工作台" : "自定义项目工作台"}</h1><p>{variant === "standard" ? "按制作顺序操作，工作流决定输入、审核与交付。" : "组合相同制作组件，按习惯安排项目界面。"}</p></div><div className="experience-actions"><button className="secondary-button" onClick={onBack}><ArrowLeft size={14} />返回平台</button><button className="secondary-button" disabled={Boolean(busy)} onClick={() => setRefresh((current) => current + 1)}><RefreshCw size={14} />刷新</button><button className="primary-button" disabled={Boolean(busy)} onClick={() => void setupDemo()}>{busy === "demo" ? <LoaderCircle className="spin" size={14} /> : <Play size={14} />}准备对比 Demo</button></div></header>
    <div className="experience-project-bar"><label><span>当前项目</span><select aria-label="对比项目" value={projectId} disabled={Boolean(busy)} onChange={(event) => { setProjectId(event.target.value); onProjectChange(event.target.value); }}><option value="">选择项目</option>{allProjects.map((project) => <option key={project.project_id} value={project.project_id}>{project.name}</option>)}</select></label><label><span>发布工作流</span><select aria-label="项目发布工作流" disabled={Boolean(busy)} value={bindingId} onChange={(event) => setBindingId(event.target.value)}><option value="">选择已发布工作流</option>{workflows.filter((item) => item.lifecycle === "published").map((item) => <option key={item.workflow_id} value={item.workflow_id}>{item.name} · v{item.version}</option>)}</select></label><button className="secondary-button" disabled={!projectId || !bindingId || Boolean(busy) || projectActive || bindingId === workflow?.workflow_id} onClick={() => void bind()}>绑定版本</button><button className="secondary-button" disabled={!projectId} onClick={() => onOpenEditor(projectId)}><GitBranch size={14} />编辑工作流</button></div>
    {workflow && <p className="experience-binding">当前绑定：{workflow.name} · v{workflow.version} {run?.workflow_id !== workflow.workflow_id && run ? "· 当前查看的是历史版本运行" : ""}</p>}
    {error && <p className="experience-feedback" role="alert">{error}</p>}{notice && <p className="experience-feedback" role="status">{notice}</p>}
    {busy === "load" ? <p><LoaderCircle size={16} className="spin" />正在读取项目工作流…</p> : renderPanels ? renderPanels(panels, projectId, workflow?.workflow_id ?? "unbound") : <div className="experience-standard-grid">{panels.map((panel) => <section className={`experience-card experience-card--${panel.id}`} key={panel.id}><header><h2>{panel.title}</h2><small>{panel.hint}</small></header>{panel.content}</section>)}</div>}
  </section>;
}
