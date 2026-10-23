import { useEffect, useMemo, useState } from "react";
import { ArrowRight, CircleAlert, Clock3, GitBranch, ListTree, LoaderCircle, Search } from "lucide-react";
import { fetchAudioJob } from "./api";
import type { AudioJob, WorkflowNodeRun, WorkflowRun } from "./types";
import { WorkflowRecordInspector } from "./WorkflowRecordInspector";
import { buildWorkflowTrace, formatWorkflowDuration, workflowElapsed, workflowMediaUrl, workflowSkipLabels, workflowStatusLabels } from "./workflowTrace";
import type { WorkflowTraceJob } from "./workflowTrace";
import "./workflowTrace.css";

const nodeLabels: Record<string, string> = {
  "human.speakers": "校正说话人", "human.voices": "试听并确认声线",
  "text.import": "导入文本", "text.input": "输入文本", "text.analysis": "文本分析", "control.condition": "条件分支",
  "voice.character_analysis": "角色识别与画像", "voice.profile": "声线画像", "voice.match_or_generate": "匹配或生成声音",
  "voice.reference_audio": "生成参考音频", "voice.assign_voices": "角色声线分配", "voice.render_settings": "推理模型与参数",
  "voice.batch_render": "批量语音生成", "voice.multi_role_fast_render": "多角色极速渲染", "voice.quality_render": "质量渲染",
  "voice.fast_render": "极速渲染", "audio.rvc": "RVC 音色处理", "audio.loudness": "节目响度", "audio.merge": "合并导出",
};

export function formatWorkflowTime(value: string | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "—";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function MediaResult({ value, label }: { value: unknown; label: string }) {
  const url = workflowMediaUrl(value);
  return url ? <div className="workflow-trace-audio"><strong>{label}</strong><audio controls preload="none" src={url} /><a href={url} download>下载音频</a></div> : null;
}

function JobRecord({ job }: { job: WorkflowTraceJob }) {
  const [record, setRecord] = useState<AudioJob | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setRecord(null);
    setError("");
    void fetchAudioJob(job.jobId).then((next) => { if (!cancelled) setRecord(next); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "任务记录读取失败"); });
    return () => { cancelled = true; };
  }, [job.jobId, refresh]);
  return <div className="workflow-trace-job-record">
    <header><strong>任务 {job.jobId}</strong><button type="button" className="workflow-trace-text-button" onClick={() => setRefresh(refresh + 1)}>刷新任务</button></header>
    {job.text && <p className="workflow-trace-quote">{job.text}</p>}
    {job.cacheHit !== undefined && <p>{job.cacheHit ? "本次运行复用了已有音频" : "本次运行生成的音频"}</p>}
    {error ? <p role="alert">{error}。历史任务可能已清理，节点输出记录仍可查看。</p> : !record ? <p><LoaderCircle size={13} className="spin" />正在读取任务记录…</p> : <>
      <p>{workflowStatusLabels[record.status] ?? record.status} · {record.message}</p>
      <p>模型 / 声线：{record.quality_model || record.fast_voice_id || "未记录"} · RVC：{record.rvc_status}</p>
      <small>以下为任务当前记录；任务可能已经过后续响度或 RVC 处理。</small>
      {record.error && <p role="alert">{record.error}</p>}
      <MediaResult value={record.output_url} label="任务音频" />
      <WorkflowRecordInspector key={record.updated_at} label="任务完整记录" value={record} />
    </>}
  </div>;
}

function NodeJobs({ jobs }: { jobs: WorkflowTraceJob[] }) {
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(30);
  const [selected, setSelected] = useState<string | null>(null);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return query ? jobs.filter((job) => `${job.jobId} ${job.speaker ?? ""} ${job.text ?? ""}`.toLowerCase().includes(query)) : jobs;
  }, [jobs, search]);
  const selectedJob = jobs.find((job) => job.jobId === selected);
  if (!jobs.length) return <p className="workflow-trace-note">该节点没有记录音频任务 ID。</p>;
  return <section className="workflow-trace-jobs">
    <label className="workflow-trace-search"><Search size={14} /><input aria-label="搜索音频任务" placeholder="搜索角色、台词或任务 ID" value={search} onChange={(event) => { setSearch(event.target.value); setLimit(30); }} /></label>
    <p>共 {jobs.length} 个关联任务{search && `，匹配 ${filtered.length} 个`}；点击任务检查参数和音频。</p>
    <div className="workflow-trace-job-list">{filtered.slice(0, limit).map((job) => <button type="button" key={job.jobId} aria-pressed={selected === job.jobId} onClick={() => setSelected(job.jobId)}><strong>{job.speaker || job.jobId}</strong><span>{job.text || job.jobId}</span><small>{job.cacheHit ? "复用音频" : job.jobId}</small></button>)}</div>
    {filtered.length > limit && <button type="button" className="workflow-trace-text-button" onClick={() => setLimit(limit + 30)}>再显示 30 个任务（剩余 {filtered.length - limit}）</button>}
    {selectedJob && <JobRecord key={selectedJob.jobId} job={selectedJob} />}
  </section>;
}

function nodeDuration(node: WorkflowNodeRun, run: WorkflowRun): number | null {
  if (!node.started_at) return null;
  return node.status === "running" ? workflowElapsed(node.started_at, run.updated_at) : node.elapsed_seconds;
}

interface WorkflowRunDetailsProps {
  run: WorkflowRun | null;
  initialNodeId?: string | null;
  onLocateNode?: (nodeId: string) => void;
}

export function WorkflowRunDetails(props: WorkflowRunDetailsProps) {
  return props.run ? <RunDetails key={props.run.run_id} {...props} run={props.run} /> : <div className="workflow-trace-empty"><ListTree size={30} /><strong>暂无运行记录</strong><p>发布并启动工作流后，这里会展示实际执行过程。</p></div>;
}

function RunDetails({ run, initialNodeId, onLocateNode }: WorkflowRunDetailsProps & { run: WorkflowRun }) {
  const trace = useMemo(() => buildWorkflowTrace(run), [run]);
  const [nodeId, setNodeId] = useState<string | null>(initialNodeId ?? null);
  const [section, setSection] = useState<"input" | "output" | "events" | "jobs">("output");
  const [scope, setScope] = useState<"node" | "run">("node");
  const [errorsOnly, setErrorsOnly] = useState(false);
  useEffect(() => { if (initialNodeId) setNodeId(initialNodeId); }, [initialNodeId]);
  const selected = trace.steps.find((step) => step.node.node_id === nodeId)
    ?? trace.steps.find((step) => step.node.status === "failed" || step.node.status === "running" || step.node.status === "awaiting_review") ?? trace.steps[0];
  const events = (scope === "run" ? run.events : selected?.events ?? []).filter((event) => !errorsOnly || event.level !== "info");
  return <div className="workflow-run-details">
    <header className="workflow-trace-summary">
      <div><small>运行 {run.run_id}</small><strong>{workflowStatusLabels[run.status] ?? run.status} · {run.progress}%</strong><span>{run.message}</span><small>发布版本：{run.workflow_version ? `v${run.workflow_version}` : "历史记录未保存版本号"}{run.project_id ? ` · 项目 ${run.project_id}` : ""}</small></div>
      <dl><div><dt>已完成节点</dt><dd>{trace.completed} / {trace.steps.length}</dd></div><div><dt>总历时</dt><dd>{formatWorkflowDuration(trace.elapsed)}</dd></div><div><dt>关联音频任务</dt><dd>{trace.jobCount}</dd></div><div><dt>节点缓存 / 重试</dt><dd>{trace.cached} / {run.retry_count}</dd></div></dl>
    </header>
    {run.error && <p className="workflow-trace-error" role="alert"><CircleAlert size={15} />{run.error}</p>}
    <div className="workflow-trace-body">
      <nav className="workflow-trace-steps" aria-label="实际执行节点">
        <p>按最近一次开始时间排列；未执行节点列在末尾。</p>
        {trace.steps.map(({ node }, index) => <button type="button" key={node.node_id} className={`workflow-trace-step run-${node.status}`} aria-current={selected?.node.node_id === node.node_id ? "step" : undefined} onClick={() => setNodeId(node.node_id)}>
          <span className="workflow-trace-step-number">{index + 1}</span><strong>{nodeLabels[node.node_type] || node.node_type}</strong><small>{node.node_id}</small><span>{node.skip_reason ? workflowSkipLabels[node.skip_reason] : workflowStatusLabels[node.status]}</span><small><Clock3 size={10} />{formatWorkflowDuration(nodeDuration(node, run))} · 尝试 {node.attempt} 次</small>
        </button>)}
      </nav>
      <div className="workflow-trace-detail-pane">
        {selected && <>
          <header className="workflow-trace-node-heading"><div><strong>{nodeLabels[selected.node.node_type] || selected.node.node_type}</strong><small>{selected.node.node_id} · {selected.node.node_type} · v{selected.node.version}</small><p>{selected.node.message}</p></div>{onLocateNode && <button type="button" className="secondary-button" onClick={() => onLocateNode(selected.node.node_id)}><GitBranch size={12} />定位画布节点</button>}</header>
          <div className="workflow-trace-flow" aria-label="节点执行详情">
            <button type="button" aria-pressed={section === "input"} onClick={() => setSection("input")}><small>INPUT</small><strong>已解析输入</strong><span>{Object.keys(selected.node.inputs).length} 个字段</span></button><ArrowRight size={18} aria-hidden="true" />
            <button type="button" aria-pressed={section === "events"} onClick={() => setSection("events")}><small>EXECUTION</small><strong>{workflowStatusLabels[selected.node.status]}</strong><span>{formatWorkflowDuration(nodeDuration(selected.node, run))} · {selected.events.length} 条事件</span></button><ArrowRight size={18} aria-hidden="true" />
            <button type="button" aria-pressed={section === "output"} onClick={() => setSection("output")}><small>OUTPUT</small><strong>实际输出</strong><span>{Object.keys(selected.node.outputs).length} 个字段</span></button>
          </div>
          <div className="workflow-trace-node-meta"><span>开始：{formatWorkflowTime(selected.node.started_at)}</span><span>结束：{formatWorkflowTime(selected.node.finished_at)}</span>{selected.node.skip_reason && <span>{workflowSkipLabels[selected.node.skip_reason]}</span>}</div>
          {selected.node.error && <p className="workflow-trace-error" role="alert">{selected.node.error}</p>}
          <div className="workflow-trace-tabs" role="group" aria-label="节点记录分类">{([ ["input", "输入"], ["output", "输出"], ["events", "执行事件"], ["jobs", `音频任务（${selected.jobs.length}）`] ] as const).map(([value, label]) => <button type="button" key={value} aria-pressed={section === value} onClick={() => setSection(value)}>{label}</button>)}</div>
          {section === "input" && <WorkflowRecordInspector key={`${selected.node.node_id}-input`} label="已解析输入" value={selected.node.inputs} />}
          {section === "output" && <><MediaResult value={selected.node.outputs.audio_url} label="节点音频" /><WorkflowRecordInspector key={`${selected.node.node_id}-output`} label="实际输出" value={selected.node.outputs} />{selected.node.review && <WorkflowRecordInspector key={`${selected.node.node_id}-review`} label="人工审核记录" value={selected.node.review} />}</>}
          {section === "jobs" && <NodeJobs key={selected.node.node_id} jobs={selected.jobs} />}
        </>}
        {(section === "events" || !selected) && <section className="workflow-trace-events">
          <header><select aria-label="事件范围" value={scope} onChange={(event) => setScope(event.target.value as "node" | "run")}><option value="node">当前节点事件</option><option value="run">整次运行事件</option></select><label><input type="checkbox" checked={errorsOnly} onChange={(event) => setErrorsOnly(event.target.checked)} />只看警告和错误</label></header>
          <p className="workflow-trace-note">事件最多保留最近 256 条；节点详情记录最近一次尝试，历史尝试可从保留事件中查看。</p>
          <ol>{events.map((event) => <li key={event.event_id} data-level={event.level}><time>{formatWorkflowTime(event.created_at)}</time><small>{event.node_id || "运行"} · {event.level}</small><span>{event.message}</span></li>)}</ol>{!events.length && <p>没有符合条件的事件。</p>}
        </section>}
        <details className="workflow-trace-run-records"><summary>整次运行的输入与最终输出</summary><WorkflowRecordInspector label="运行输入" value={run.input} /><MediaResult value={run.outputs.audio_url} label="最终音频" /><WorkflowRecordInspector label="最终输出" value={run.outputs} /></details>
        <p className="workflow-trace-note">此视图展示已保存的节点和音频任务记录；模型原始请求、提示词与 Token 用量尚未采集。</p>
      </div>
    </div>
  </div>;
}
