import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Load the actual read-only UI without a browser; CSS belongs to the production build.
const moduleUrls = new Map();
function loadModule(name) {
  if (moduleUrls.has(name)) return moduleUrls.get(name);
  let source = ts.transpileModule(readFileSync(new URL(`../src/${name}`, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX }, fileName: name,
  }).outputText;
  source = source.replace(/import "\.\/[^"\n]+\.css";?/g, "");
  source = source.replace(/from "([^"]+)"/g, (_, specifier) => {
    const url = specifier === "./api"
      ? "data:text/javascript,export function fetchAudioJob(){throw new Error('Jobs must load only after selection')}"
      : specifier.startsWith("./") ? loadModule(`${specifier.slice(2)}${specifier.includes("Inspector") ? ".tsx" : ".ts"}`) : import.meta.resolve(specifier);
    return `from ${JSON.stringify(url)}`;
  });
  const url = `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
  moduleUrls.set(name, url);
  return url;
}
const { buildWorkflowTrace, workflowNodeJobs, workflowElapsed, formatWorkflowDuration, workflowMediaUrl } = await import(loadModule("workflowTrace.ts"));
const { WorkflowRunDetails } = await import(loadModule("WorkflowRunDetails.tsx"));
const { WorkflowRecordInspector } = await import(loadModule("WorkflowRecordInspector.tsx"));
const node = (id, changes = {}) => ({ node_id: id, node_type: "text.import", version: "1.0", status: "pending", skip_reason: null, inputs: {}, outputs: {}, error: null, review: null, attempt: 0, elapsed_seconds: 0, started_at: null, finished_at: null, message: "", ...changes });
const run = (nodes, changes = {}) => ({ run_id: "run-fixture", workflow_id: "published-flow", status: "completed", progress: 100, message: "实际运行记录", input: {}, outputs: {}, node_runs: nodes, events: [], error: null, retry_count: 0, created_at: "2026-10-01T00:00:00Z", started_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:08Z", finished_at: "2026-10-01T00:00:08Z", ...changes });
const fixture = run([
  node("later", { status: "failed", attempt: 2, started_at: "2026-10-01T00:00:05Z", error: "缺少参考音频" }),
  node("not-started"),
  node("first", { status: "completed", started_at: "2026-10-01T00:00:01Z", outputs: { job_ids: ["job-a", "job-b"] } }),
  node("cached", { status: "skipped", skip_reason: "cache_hit", outputs: { job_ids: ["job-a"] } }),
], { events: [{ event_id: "event-1", level: "error", message: "缺少参考音频", node_id: "later", created_at: "2026-10-01T00:00:06Z" }] });
const trace = buildWorkflowTrace(fixture);
assert.deepEqual(trace.steps.map((step) => step.node.node_id), ["first", "later", "not-started", "cached"]);
assert.equal(trace.completed, 2);
assert.equal(trace.cached, 1);
assert.equal(trace.failed, 1);
assert.equal(trace.jobCount, 2);
assert.equal(trace.elapsed, 8);
assert.equal(trace.steps[1].events[0].event_id, "event-1");
assert.equal(fixture.node_runs[0].node_id, "later", "Projection must not mutate saved node order");
assert.equal(workflowElapsed(null, fixture.updated_at), null);
assert.equal(workflowElapsed("invalid", fixture.updated_at), null);
assert.equal(workflowElapsed(fixture.updated_at, fixture.started_at), 0);
assert.equal(formatWorkflowDuration(null), "—");
assert.equal(formatWorkflowDuration(0.012), "12 毫秒");
assert.equal(workflowMediaUrl("/media/output.wav"), "/media/output.wav");
for (const unsafe of ["https://remote/audio.wav", "javascript:alert(1)", "//remote/media/a.wav", "/media/\\remote", "/media/a\n", null]) assert.equal(workflowMediaUrl(unsafe), null);
const segments = Array.from({ length: 887 }, (_, index) => ({ job_id: `audio-${index}`, text: `台词 ${index}`, speaker_name: index % 2 ? "萧炎" : "旁白", cache_hit: index === 0 }));
const renderNode = node("render", { node_type: "voice.batch_render", status: "completed", outputs: { job_ids: segments.map((item) => item.job_id), segments }, inputs: { job_ids: ["audio-0"], text: "not-a-job" } });
const jobs = workflowNodeJobs(renderNode);
assert.equal(jobs.length, 887);
assert.equal(jobs[1].speaker, "萧炎");
assert.equal(jobs[0].cacheHit, true);
assert.equal(jobs[886].text, "台词 886");
const html = renderToStaticMarkup(React.createElement(WorkflowRunDetails, { run: fixture }));
assert.match(html, /缺少参考音频/);
assert.match(html, /历史记录未保存版本号/);
assert.match(html, /模型原始请求、提示词与 Token 用量尚未采集/);
assert.ok(html.indexOf("first</small>") < html.indexOf("later</small>"));
assert.match(html, /未执行节点列在末尾/);
const cachedHtml = renderToStaticMarkup(React.createElement(WorkflowRunDetails, { run: fixture, initialNodeId: "cached" }));
assert.match(cachedHtml, /命中节点缓存/);
assert.match(cachedHtml, /开始：—/);
const largeHtml = renderToStaticMarkup(React.createElement(WorkflowRunDetails, { run: run([renderNode]), initialNodeId: "render" }));
assert.match(largeHtml, /音频任务（887）/);
assert.doesNotMatch(largeHtml, /台词 886/, "Segments stay collapsed until the user expands them");
const treeHtml = renderToStaticMarkup(React.createElement(WorkflowRecordInspector, { label: "分页", value: Array.from({ length: 887 }, (_, index) => `value-${index}`) }));
assert.match(treeHtml, /value-29/);
assert.doesNotMatch(treeHtml, /value-30/);
assert.match(treeHtml, /剩余 857/);
assert.match(renderToStaticMarkup(React.createElement(WorkflowRunDetails, { run: null })), /暂无运行记录/);

// The canvas has regressed twice when a new row squeezed its viewport to zero.
const studio = ts.createSourceFile("WorkflowStudio.tsx", readFileSync(new URL("../src/WorkflowStudio.tsx", import.meta.url), "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let canvasShell;
function visit(ast) {
  if (ts.isJsxElement(ast) && ast.openingElement.attributes.properties.some((property) => ts.isJsxAttribute(property) && property.name.getText(studio) === "className" && property.initializer?.getText(studio) === '"workflow-canvas-shell"')) canvasShell = ast;
  ts.forEachChild(ast, visit);
}
visit(studio);
assert.ok(canvasShell);
const shellChildren = canvasShell.children.filter(ts.isJsxElement);
assert.equal(shellChildren.length, 2, "Keep toolbar + canvas; trace view belongs outside the canvas shell");
assert.match(shellChildren[0].openingElement.getText(studio), /workflow-canvas-toolbar/);
assert.match(shellChildren[1].openingElement.getText(studio), /workflow-canvas/);
console.log("Workflow execution trace: ordering, retries, cache, 887 jobs, lazy records, legacy data and canvas structure verified");
