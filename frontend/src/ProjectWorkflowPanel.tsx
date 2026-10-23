import { GitBranch, LoaderCircle, Play, RefreshCw, Send, Volume2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { bindProjectWorkflow, createProjectWorkflowRun, fetchProjectWorkflow, fetchWorkflows } from "./api";
import type { PlatformProject, ProjectWorkflowPayload, WorkflowDefinition, WorkflowInputDefinition, WorkflowRun } from "./types";
import { WorkflowRunDetails } from "./WorkflowRunDetails";
import { workflowMediaUrl, workflowStatusLabels } from "./workflowTrace";

interface ProjectWorkflowPanelProps {
  project: PlatformProject;
  onOpenEditor: (projectId: string) => void;
}

function initialInputs(workflow: WorkflowDefinition | null, projectId: string): Record<string, unknown> {
  const values: Record<string, unknown> = { project_id: projectId };
  workflow?.inputs.forEach((input) => {
    if (input.default !== null && input.default !== undefined) values[input.name] = input.default;
  });
  return values;
}

function inputLabel(input: WorkflowInputDefinition): string {
  return input.label || input.name;
}

function inputValueText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "";
  return JSON.stringify(value, null, 2);
}

export function ProjectWorkflowPanel({ project, onOpenEditor }: ProjectWorkflowPanelProps) {
  const [payload, setPayload] = useState<ProjectWorkflowPayload | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState("");
  const [inputs, setInputs] = useState<Record<string, unknown>>({ project_id: project.project_id });
  const [busy, setBusy] = useState<"load" | "bind" | "run" | null>("load");
  const [message, setMessage] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [showRunDetails, setShowRunDetails] = useState(false);

  const publishedWorkflows = useMemo(() => workflows.filter((workflow) => workflow.lifecycle === "published"), [workflows]);
  const selectedRun: WorkflowRun | null = payload?.runs.find((run) => run.run_id === selectedRunId) ?? payload?.runs[0] ?? null;

  async function load(): Promise<void> {
    setBusy("load");
    try {
      const [nextPayload, nextWorkflows] = await Promise.all([fetchProjectWorkflow(project.project_id), fetchWorkflows()]);
      setPayload(nextPayload);
      setWorkflows(nextWorkflows);
      setSelectedWorkflowId(nextPayload.workflow?.workflow_id ?? nextPayload.binding?.workflow_id ?? nextWorkflows.find((item) => item.lifecycle === "published")?.workflow_id ?? "");
      setInputs(initialInputs(nextPayload.workflow, project.project_id));
      setSelectedRunId(nextPayload.runs[0]?.run_id ?? null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "项目工作流读取失败");
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    void load();
  }, [project.project_id]);

  useEffect(() => {
    const run = selectedRun;
    if (!run || !["queued", "running", "paused", "awaiting_review"].includes(run.status)) return;
    let cancelled = false;
    let inFlight = false;
    const timer = window.setInterval(() => {
      if (inFlight) return;
      inFlight = true;
      void fetchProjectWorkflow(project.project_id).then((next) => {
        if (cancelled) return;
        setPayload(next);
        setSelectedRunId((current) => current ?? next.runs[0]?.run_id ?? null);
      }).catch(() => undefined).finally(() => { inFlight = false; });
    }, 900);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [project.project_id, selectedRun?.run_id, selectedRun?.status]);

  function changeInput(name: string, value: unknown): void {
    setInputs((current) => ({ ...current, [name]: value }));
  }

  async function readTxtFile(input: WorkflowInputDefinition, file: File): Promise<void> {
    try {
      const bytes = await file.arrayBuffer();
      let text: string;
      try {
        text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      } catch {
        text = new TextDecoder("gb18030", { fatal: true }).decode(bytes);
      }
      changeInput(input.name, text.replace(/^\uFEFF/, ""));
      changeInput(`${input.name}_name`, file.name);
      setMessage(`已载入 ${file.name}，可启动项目工作流`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "TXT 文件读取失败");
    }
  }

  async function bind(): Promise<void> {
    if (!selectedWorkflowId) return;
    setBusy("bind");
    setMessage("");
    try {
      const next = await bindProjectWorkflow(project.project_id, selectedWorkflowId);
      setPayload(next);
      setInputs(initialInputs(next.workflow, project.project_id));
      setMessage(`已绑定 ${next.workflow?.name ?? "工作流"} · v${next.workflow?.version ?? ""}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "绑定工作流失败");
    } finally {
      setBusy(null);
    }
  }

  async function run(): Promise<void> {
    if (!payload?.workflow) {
      setMessage("请先绑定一个已发布工作流");
      return;
    }
    setBusy("run");
    setMessage("");
    try {
      const next = await createProjectWorkflowRun(project.project_id, { ...inputs, project_id: project.project_id });
      setPayload((current) => current ? { ...current, runs: [next, ...current.runs.filter((item) => item.run_id !== next.run_id)] } : current);
      setSelectedRunId(next.run_id);
      setMessage("工作流已启动，项目运行会固定使用当前发布版本");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "启动工作流失败");
    } finally {
      setBusy(null);
    }
  }

  function renderInput(input: WorkflowInputDefinition): JSX.Element {
    const value = inputs[input.name];
    const isProjectId = input.name === "project_id";
    const isText = input.type === "text" || input.name === "text" || input.name === "file_path" || input.widget === "textarea";
    const isFile = input.widget === "file";
    return <label className="project-workflow-field" key={input.name} title={input.description}>
      <span>{inputLabel(input)}{input.required && <b>*</b>}</span>
      {input.type === "boolean" ? <input type="checkbox" checked={Boolean(value)} disabled={isProjectId} onChange={(event) => changeInput(input.name, event.target.checked)} />
        : input.type === "number" ? <input type="number" value={typeof value === "number" ? value : ""} disabled={isProjectId} onChange={(event) => changeInput(input.name, event.target.value === "" ? null : event.target.valueAsNumber)} />
          : isFile ? <><input type="file" accept=".txt,text/plain" disabled={isProjectId} onChange={(event) => { const file = event.target.files?.[0]; if (file) void readTxtFile(input, file); event.target.value = ""; }} /><small>{typeof value === "string" && value.trim() ? `${value.trim().length.toLocaleString()} 字已载入` : "支持 UTF-8 和 GB18030 编码的 TXT 文件"}</small></>
            : isText ? <textarea rows={3} value={inputValueText(value)} disabled={isProjectId} placeholder={input.description || "输入文本"} onChange={(event) => changeInput(input.name, event.target.value)} />
              : input.widget === "select" ? <select value={inputValueText(value)} disabled={isProjectId} onChange={(event) => changeInput(input.name, event.target.value)}>{(input.options ?? []).map((option) => <option value={option} key={option}>{option}</option>)}</select>
                : input.type === "json" || input.type === "any" ? <textarea rows={3} value={inputValueText(value)} disabled={isProjectId} placeholder={input.description || "JSON"} onChange={(event) => { try { changeInput(input.name, JSON.parse(event.target.value)); } catch { changeInput(input.name, event.target.value); } }} />
                  : <input value={inputValueText(value)} disabled={isProjectId} onChange={(event) => changeInput(input.name, event.target.value)} />}
      {input.description && <small>{input.description}</small>}
    </label>;
  }

  const workflow = payload?.workflow;
  return <section className="project-workflow-panel">
    <header className="project-workflow-header">
      <div><span className="eyebrow">PROJECT WORKFLOW</span><h3><GitBranch size={16} />{project.name}</h3><p>项目运行只使用已发布版本；编辑草稿不会影响正在运行的任务。</p></div>
      <div className="project-workflow-actions"><button className="icon-button" title="刷新项目工作流" onClick={() => void load()} disabled={busy === "load"}><RefreshCw size={14} className={busy === "load" ? "spin" : ""} /></button><button className="secondary-button" onClick={() => onOpenEditor(project.project_id)}><GitBranch size={13} />编辑工作流</button></div>
    </header>
    <div className="project-workflow-binding">
      <label><span>当前发布工作流</span><select value={selectedWorkflowId} onChange={(event) => setSelectedWorkflowId(event.target.value)}><option value="">选择已发布工作流</option>{publishedWorkflows.map((item) => <option value={item.workflow_id} key={item.workflow_id}>{item.name} · v{item.version}</option>)}</select></label>
      <button className="secondary-button" onClick={() => void bind()} disabled={!selectedWorkflowId || busy !== null}><Send size={13} />{payload?.binding ? "切换发布版本" : "绑定到项目"}</button>
      {payload?.binding && <span className="project-workflow-version">当前：v{payload.binding.workflow_version}</span>}
    </div>
    {workflow ? <>
      <div className="project-workflow-form"><div className="project-workflow-form-heading"><strong>运行输入</strong><span>{workflow.inputs.length ? "由工作流输入声明生成" : "此工作流没有额外输入"}</span></div>{workflow.inputs.map(renderInput)}<button className="primary-button" onClick={() => void run()} disabled={busy !== null}><Play size={13} />{busy === "run" ? "启动中" : "运行当前版本"}</button></div>
      {selectedRun && <div className="project-workflow-run">
        <div className="project-workflow-run-heading"><strong>运行记录</strong><select aria-label="项目运行记录" value={selectedRun.run_id} onChange={(event) => setSelectedRunId(event.target.value)}>{payload.runs.map((item) => <option key={item.run_id} value={item.run_id}>{item.run_id.slice(-8)} · {workflowStatusLabels[item.status]} · {item.progress}%</option>)}</select></div>
        <p>{selectedRun.message}</p><div className="project-workflow-progress"><span style={{ width: `${selectedRun.progress}%` }} /></div>
        {selectedRun.status === "completed" && workflowMediaUrl(selectedRun.outputs.audio_url) && <div className="project-workflow-audio"><Volume2 size={15} /><audio controls preload="none" src={workflowMediaUrl(selectedRun.outputs.audio_url)!} /><a href={workflowMediaUrl(selectedRun.outputs.audio_url)!} download>下载音频</a></div>}
        {selectedRun.status === "failed" && <p className="error">{selectedRun.error}</p>}
        <details className="project-workflow-trace" open={showRunDetails} onToggle={(event) => setShowRunDetails(event.currentTarget.open)}><summary>节点执行详情与输入输出</summary>{showRunDetails && <WorkflowRunDetails run={selectedRun} />}</details>
      </div>}
    </> : <div className="project-workflow-empty"><GitBranch size={20} /><span>绑定已发布工作流后，项目页会显示输入、运行状态与音频输出。</span></div>}
    {message && <p className="project-workflow-message">{busy === "load" && <LoaderCircle size={13} className="spin" />}{message}</p>}
  </section>;
}
