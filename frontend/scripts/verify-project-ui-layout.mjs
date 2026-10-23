import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

async function load(name) {
  const source = ts.transpileModule(readFileSync(new URL(`../src/${name}`, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 }, fileName: name,
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
}
const { normalizeProjectLayout, moveProjectPanel, defaultProjectLayout, projectLayoutStorageKey } = await load("workflowProjectLayout.ts");
const restored = normalizeProjectLayout({ version: 1, columns: 3, panels: [
  { id: "review", visible: false, wide: true }, { id: "review", visible: false }, { id: "unknown", visible: true }, { id: "inputs", visible: false },
] });
assert.equal(restored.columns, 3);
assert.equal(restored.panels.length, 5);
assert.equal(new Set(restored.panels.map((panel) => panel.id)).size, 5);
assert.equal(restored.panels.find((panel) => panel.id === "review").visible, true, "An old or corrupted layout must not block approval");
assert.equal(restored.panels.find((panel) => panel.id === "inputs").visible, true, "Run inputs must remain reachable");
assert.deepEqual(normalizeProjectLayout({ version: 999, panels: [] }), defaultProjectLayout());
const moved = moveProjectPanel(restored, "results", "review");
assert.equal(moved.panels[0].id, "results");
assert.equal(restored.panels[0].id, "review", "Editing must not mutate the saved layout");
assert.deepEqual(normalizeProjectLayout(JSON.parse(JSON.stringify(moved))), moved);
assert.notEqual(projectLayoutStorageKey("a:b", "c"), projectLayoutStorageKey("a", "b:c"));
assert.notEqual(projectLayoutStorageKey("project", "v1"), projectLayoutStorageKey("project", "v2"));
const { projectUiDemoDefinition } = await load("projectWorkflowDemo.ts");
const demo = projectUiDemoDefinition();
assert.equal(demo.nodes.filter((node) => node.node_type.startsWith("human.")).length, 2);
assert.equal(demo.outputs[0].display, "audio");
assert.equal(demo.inputs[0].required, true);
console.log("Project UI layout recovery, review access, isolation, and shared demo contract verified");
