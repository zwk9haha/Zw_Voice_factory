import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../src/workflowConnections.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  fileName: "workflowConnections.ts",
}).outputText;
const { checkWorkflowConnection, isBranchOutput } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

const port = (name, type, multiple = false) => ({ name, type, multiple, label: name });
const manifest = (type, inputs, outputs, mode = "sync") => ({ type, inputs, outputs, execution: { mode } });
const node = (id, type = id, overrides = {}) => ({ node_id: id, node_type: type, inputs: {}, depends_on: [], ...overrides });
const edge = (from, fromPort, to, toPort, kind = "data") => ({ source_node: from, source_port: fromPort, target_node: to, target_port: toPort, kind });
const endpoint = (nodeId, port) => ({ nodeId, port });
const manifests = new Map([
  ["source", manifest("source", [port("input", "text")], [port("text", "text"), port("length", "number"), port("jobs", "job_ids")])],
  ["target", manifest("target", [port("text", "text"), port("jobs", "job_ids", true), port("value", "any")], [port("text", "text")])],
  ["condition", manifest("condition", [port("value", "any")], [port("true", "boolean"), port("value", "any")], "control")],
]);
const nodes = [node("source"), node("second", "source"), node("target"), node("condition")];
const check = (from, fromPort, to, toPort, edges = [], graph = nodes) => checkWorkflowConnection(graph, edges, manifests, endpoint(from, fromPort), endpoint(to, toPort));
const rejected = (result, reason) => {
  assert.equal(result.allowed, false);
  assert.match(result.reason, reason);
};

assert.deepEqual(check("source", "text", "target", "text"), { allowed: true, kind: "data" });
rejected(check("source", "length", "target", "text"), /类型不兼容/);
rejected(check("source", "text", "target", "jobs"), /类型不兼容/);
rejected(check("missing", "text", "target", "text"), /节点不存在/);
rejected(check("source", "text", "source", "input"), /自身/);
rejected(check("source", "input", "target", "text"), /输出端口/);
rejected(check("source", "text", "target", "missing"), /输入端口/);
rejected(checkWorkflowConnection(nodes, [], new Map(), endpoint("source", "text"), endpoint("target", "text")), /定义尚未加载/);

const boundText = [edge("source", "text", "target", "text")];
rejected(check("second", "text", "target", "text", boundText), /只能接收一条/);
rejected(check("source", "text", "target", "text", boundText), /已存在/);
const boundJobs = [edge("source", "jobs", "target", "jobs")];
assert.equal(check("second", "jobs", "target", "jobs", boundJobs).allowed, true);
rejected(check("source", "jobs", "target", "jobs", boundJobs), /已存在/);

assert.equal(isBranchOutput(manifests.get("condition"), "true"), true);
assert.equal(isBranchOutput(manifests.get("condition"), "value"), false);
assert.deepEqual(check("condition", "true", "target", "__route__"), { allowed: true, kind: "branch" });
rejected(check("condition", "value", "target", "__route__"), /路由入口只能接收/);
rejected(check("source", "text", "target", "__route__"), /路由入口只能接收/);
// A boolean wired to a data input stays a data binding; it must not silently route the target.
assert.deepEqual(check("condition", "true", "target", "value"), { allowed: true, kind: "data" });
assert.equal(check("condition", "true", "second", "__route__", [edge("condition", "true", "target", "__route__", "branch")]).allowed, true);

rejected(check("source", "text", "target", "text", [edge("target", "text", "source", "input")]), /环路/);
rejected(check("condition", "true", "target", "__route__", [edge("target", "text", "condition", "value")]), /环路/);
for (const dependency of [
  { depends_on: ["target"] },
  { inputs: { input: "$target.text" } },
  { inputs: { input: { from_node: "target", port: "text" } } },
  { inputs: { input: { node_id: "target", port: "text" } } },
  { inputs: { input: [{ nested: { $from: "target.text" } }] } },
]) {
  rejected(check("source", "text", "target", "text", [], [node("source", "source", dependency), node("target")]), /环路/);
}
assert.equal(check("source", "text", "target", "text", [], [node("source", "source", { inputs: { input: "$input.text" } }), node("target")]).allowed, true);

// Full wire-type matrix: agree with the server, keeping valid demo and JSON bindings.
const valueTypes = ["string", "number", "boolean", "json", "text", "text_segment", "text_segments", "voice_profile", "voice_matches", "voice_snapshot", "audio_asset", "audio_assets", "job_ids", "any"];
for (const sourceType of valueTypes) {
  for (const targetType of valueTypes) {
    const pair = new Map([
      ["out", manifest("out", [], [port("out", sourceType)])],
      ["in", manifest("in", [port("in", targetType)], [])],
    ]);
    const result = checkWorkflowConnection([node("out"), node("in")], [], pair, endpoint("out", "out"), endpoint("in", "in"));
    const expected = sourceType === targetType || [sourceType, targetType].some((type) => type === "any" || type === "json")
      || [sourceType, targetType].every((type) => type === "text" || type === "string");
    assert.equal(result.allowed, expected, `${sourceType} → ${targetType}`);
  }
}
console.log("Workflow port types, occupied inputs, branches and dependency cycles verified");
