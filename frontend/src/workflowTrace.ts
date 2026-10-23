import type { WorkflowEvent, WorkflowNodeRun, WorkflowRun } from "./types";

export const workflowStatusLabels: Record<string, string> = {
  draft: "未启动", pending: "等待依赖", queued: "排队中", running: "运行中", awaiting_review: "待人工审核",
  paused: "已暂停", completed: "已完成", complete: "已完成", failed: "失败", cancelled: "已取消", skipped: "已跳过",
};

export const workflowSkipLabels: Record<string, string> = {
  cache_hit: "命中节点缓存", dependency_failed: "依赖失败", branch_not_selected: "分支未选中", upstream_not_selected: "上游分支未产生结果",
};

export interface WorkflowTraceJob {
  jobId: string;
  text?: string;
  speaker?: string;
  audioUrl?: string;
  cacheHit?: boolean;
}

export function workflowMediaUrl(value: unknown): string | null {
  return typeof value === "string" && value.startsWith("/media/") && !/[\\\u0000-\u001f]/.test(value) ? value : null;
}

function timestamp(value: string | null | undefined): number | null {
  const parsed = value ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

export function workflowElapsed(start: string | null, end: string | null | undefined): number | null {
  const from = timestamp(start);
  const to = timestamp(end);
  return from === null || to === null ? null : Math.max(0, (to - from) / 1000);
}

export function formatWorkflowDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} 毫秒`;
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes} 分 ${Math.floor(seconds % 60)} 秒` : `${Math.floor(minutes / 60)} 时 ${minutes % 60} 分`;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

export function workflowNodeJobs(node: WorkflowNodeRun): WorkflowTraceJob[] {
  const jobs = new Map<string, WorkflowTraceJob>();
  function add(value: unknown): WorkflowTraceJob | undefined {
    if (typeof value !== "string" || !value) return;
    if (!jobs.has(value)) jobs.set(value, { jobId: value });
    return jobs.get(value);
  }
  // Only declared job fields count; arbitrary strings in text/JSON are not job references.
  const values = [node.outputs.job_ids, node.inputs.job_ids];
  values.forEach((value) => { if (Array.isArray(value)) value.flat().forEach(add); });
  add(node.outputs.job_id);
  const segments = node.outputs.segments;
  if (Array.isArray(segments)) segments.forEach((value) => {
    const segment = record(value);
    if (!segment) return;
    const job = add(segment.job_id);
    if (!job) return;
    if (typeof segment.text === "string") job.text = segment.text;
    if (typeof segment.speaker_name === "string") job.speaker = segment.speaker_name;
    job.audioUrl = workflowMediaUrl(segment.audio_url) ?? undefined;
    if (typeof segment.cache_hit === "boolean") job.cacheHit = segment.cache_hit;
  });
  return [...jobs.values()];
}

export function buildWorkflowTrace(run: WorkflowRun) {
  const events = new Map<string, WorkflowEvent[]>();
  run.events.forEach((event) => {
    if (event.node_id) events.set(event.node_id, [...(events.get(event.node_id) ?? []), event]);
  });
  const steps = run.node_runs.map((node, index) => ({ node, index, events: events.get(node.node_id) ?? [], jobs: workflowNodeJobs(node) }))
    .sort((a, b) => {
      const left = timestamp(a.node.started_at);
      const right = timestamp(b.node.started_at);
      if (left === null && right === null) return a.index - b.index;
      if (left === null) return 1;
      if (right === null) return -1;
      return left - right || a.index - b.index;
    });
  return {
    steps,
    elapsed: workflowElapsed(run.started_at, run.finished_at ?? run.updated_at),
    completed: run.node_runs.filter((node) => node.status === "completed" || node.skip_reason === "cache_hit").length,
    failed: run.node_runs.filter((node) => node.status === "failed").length,
    cached: run.node_runs.filter((node) => node.skip_reason === "cache_hit").length,
    jobCount: new Set(steps.flatMap((step) => step.jobs.map((job) => job.jobId))).size,
  };
}
