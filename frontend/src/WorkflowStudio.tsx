import { Check, CircleAlert, FileUp, GitBranch, Hand, LoaderCircle, Pause, Play, Plus, RotateCcw, Save, Send, Square, Trash2, WandSparkles, X } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { bindProjectWorkflow, createWorkflow, createWorkflowRun, fetchProjectWorkflow, fetchWorkflowNodeTypes, fetchWorkflowRun, fetchWorkflowRuns, fetchWorkflows, publishWorkflow, reviewWorkflowNode, updateWorkflow, validateWorkflow, workflowRunAction } from "./api";
import type { WorkflowDefinition, WorkflowEdgeDefinition, WorkflowGraphValidation, WorkflowInputDefinition, WorkflowNodeDefinition, WorkflowNodeManifest, WorkflowOutputBinding, WorkflowRun } from "./types";
import { checkWorkflowConnection, isBranchOutput } from "./workflowConnections";
import type { WorkflowConnectionCheck } from "./workflowConnections";
import { WorkflowRunDetails, formatWorkflowTime } from "./WorkflowRunDetails";
import { workflowMediaUrl, workflowStatusLabels } from "./workflowTrace";
import { WorkflowResizeHandle } from "./WorkflowResizeHandle";
import "./workflowLayout.css";

interface WorkflowStudioProps {
  onBack: () => void;
  projectId?: string;
}

type DraftNode = WorkflowNodeDefinition & { position: { x: number; y: number } };
type ConditionOperator = "equals" | "not_equals" | "contains" | "not_contains" | "greater_than" | "greater_or_equal" | "less_than" | "less_or_equal" | "is_empty" | "is_not_empty" | "is_true" | "is_false";
type ConditionRuleDraft = { path: string; operator: ConditionOperator; compare_value: unknown };

const conditionOperatorOptions: Array<{ value: ConditionOperator; label: string }> = [
  { value: "equals", label: "等于" },
  { value: "not_equals", label: "不等于" },
  { value: "contains", label: "包含" },
  { value: "not_contains", label: "不包含" },
  { value: "greater_than", label: "大于" },
  { value: "greater_or_equal", label: "大于等于" },
  { value: "less_than", label: "小于" },
  { value: "less_or_equal", label: "小于等于" },
  { value: "is_empty", label: "为空" },
  { value: "is_not_empty", label: "不为空" },
  { value: "is_true", label: "为真" },
  { value: "is_false", label: "为假" },
];

const unaryConditionOperators = new Set<ConditionOperator>(["is_empty", "is_not_empty", "is_true", "is_false"]);

const categoryLabels: Record<string, string> = {
  input: "输入",
  analysis: "分析",
  control: "控制流",
  voice: "声音",
  render: "渲染",
  audio: "音频",
  output: "输出",
};

const DEFAULT_PAYLOAD = {
  name: "未命名音声工作流",
  version: "1.0",
  description: "",
  inputs: [],
  outputs: [],
  allow_pause: true,
  allow_retry: true,
  allow_recovery: true,
};

const DEFAULT_PANEL_SIZES = { library: 168, inspector: 216, console: 44 };
const PANEL_SIZES_KEY = "zw-workflow-panel-sizes";
function savedPanelSizes(): typeof DEFAULT_PANEL_SIZES {
  try {
    const saved = JSON.parse(localStorage.getItem(PANEL_SIZES_KEY) ?? "null");
    const size = (key: keyof typeof DEFAULT_PANEL_SIZES, min: number, max: number) => typeof saved?.[key] === "number" && Number.isFinite(saved[key]) ? Math.max(min, Math.min(max, saved[key])) : DEFAULT_PANEL_SIZES[key];
    return { library: size("library", 140, 360), inspector: size("inspector", 180, 460), console: size("console", 44, 480) };
  } catch { return DEFAULT_PANEL_SIZES; }
}

function parseConditionValue(raw: string): unknown {
  if (!raw.trim()) return null;
  try { return JSON.parse(raw); } catch { return raw; }
}

function conditionRulesFrom(config: Record<string, unknown>): ConditionRuleDraft[] {
  if (Array.isArray(config.conditions) && config.conditions.length > 0) {
    return config.conditions.map((item) => {
      const rule = item && typeof item === "object" ? item as Record<string, unknown> : {};
      const operator = conditionOperatorOptions.some((option) => option.value === rule.operator) ? rule.operator as ConditionOperator : "equals";
      return { path: typeof rule.path === "string" && rule.path.trim() ? rule.path : "$", operator, compare_value: rule.compare_value ?? null };
    });
  }
  const operator = conditionOperatorOptions.some((option) => option.value === config.operator) ? config.operator as ConditionOperator : "equals";
  return [{ path: "$", operator, compare_value: config.compare_value ?? null }];
}

function ConditionGroupEditor({ config, onChange }: { config: Record<string, unknown>; onChange: (config: Record<string, unknown>) => void }) {
  const rules = conditionRulesFrom(config);
  const rulesKey = JSON.stringify(rules);
  const [valueTexts, setValueTexts] = useState<string[]>(() => rules.map((rule) => JSON.stringify(rule.compare_value)));

  useEffect(() => {
    setValueTexts(rules.map((rule) => JSON.stringify(rule.compare_value)));
  }, [rulesKey]);

  function commit(nextRules: ConditionRuleDraft[], logic = config.logic === "any" ? "any" : "all"): void {
    const nextConfig: Record<string, unknown> = { ...config, logic, conditions: nextRules };
    delete nextConfig.operator;
    delete nextConfig.compare_value;
    onChange(nextConfig);
  }

  function updateRule(index: number, changes: Partial<ConditionRuleDraft>): void {
    commit(rules.map((rule, ruleIndex) => ruleIndex === index ? { ...rule, ...changes } : rule));
  }

  return (
    <div className="workflow-condition-editor">
      <label className="workflow-field">
        <span>条件组合</span>
        <select value={config.logic === "any" ? "any" : "all"} onChange={(event) => commit(rules, event.target.value as "all" | "any")}>
          <option value="all">全部满足（AND）</option>
          <option value="any">任一满足（OR）</option>
        </select>
      </label>
      <div className="workflow-condition-rules">
        {rules.map((rule, index) => (
          <div className="workflow-condition-rule" key={index}>
            <header>
              <strong>规则 {index + 1}</strong>
              <button type="button" className="icon-button" onClick={() => commit(rules.filter((_, ruleIndex) => ruleIndex !== index))} disabled={rules.length === 1} title="删除规则">
                <X size={12} />
              </button>
            </header>
            <label className="workflow-field">
              <span>字段路径</span>
              <input value={rule.path} onChange={(event) => updateRule(index, { path: event.target.value })} onBlur={() => { if (!rule.path.trim()) updateRule(index, { path: "$" }); }} placeholder="例如 $.profile.score" />
            </label>
            <label className="workflow-field">
              <span>运算符</span>
              <select value={rule.operator} onChange={(event) => updateRule(index, { operator: event.target.value as ConditionOperator })}>
                {conditionOperatorOptions.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
              </select>
            </label>
            {!unaryConditionOperators.has(rule.operator) && (
              <label className="workflow-field">
                <span>比较值</span>
                <input
                  value={valueTexts[index] ?? JSON.stringify(rule.compare_value)}
                  onChange={(event) => {
                    const raw = event.target.value;
                    setValueTexts((current) => current.map((value, valueIndex) => valueIndex === index ? raw : value));
                    try { updateRule(index, { compare_value: JSON.parse(raw) }); } catch { /* preserve partial JSON while typing */ }
                  }}
                  onBlur={() => updateRule(index, { compare_value: parseConditionValue(valueTexts[index] ?? "") })}
                />
              </label>
            )}
          </div>
        ))}
      </div>
      <button type="button" className="secondary-button workflow-add-condition" onClick={() => commit([...rules, { path: "$", operator: "equals", compare_value: null }])} disabled={rules.length >= 32}>
        <Plus size={12} />添加条件
      </button>
    </div>
  );
}

const configLabels: Record<string, string> = {
  route: "生成路线", quality_model: "质量推理模型", fast_speed: "极速语速", enabled: "启用 RVC 音色处理",
  fast_rvc_enabled: "生成时启用 RVC", profile_mode: "画像分析方式", reuse_run_id: "复用已完成运行（可选）",
  speaker_names: "角色名提示",
};

function NodeConfigFields({ manifest, config, onChange }: { manifest: WorkflowNodeManifest; config: Record<string, unknown>; onChange: (config: Record<string, unknown>) => void }) {
  return <div className="workflow-config-fields">{Object.entries(manifest.config_schema).map(([name, raw]) => {
    if (!raw || typeof raw !== "object") return null;
    const schema = raw as Record<string, unknown>;
    if (manifest.type === "text.import" && name === "text") return null;
    const stringArray = schema.type === "array" && schema.items != null && typeof schema.items === "object" && (schema.items as Record<string, unknown>).type === "string";
    if (!stringArray && !["string", "boolean", "number", "integer"].includes(String(schema.type))) return null;
    if (manifest.type === "voice.render_settings" && ((name === "quality_model" && (config.route ?? "fast") === "fast") || (name === "fast_speed" && config.route === "quality"))) return null;
    const value = config[name] ?? schema.default ?? "";
    const labels = schema.enum_labels && typeof schema.enum_labels === "object" ? schema.enum_labels as Record<string, string> : {};
    return <label className="workflow-field" key={name} title={String(schema.description ?? "")}>
      <span>{configLabels[name] ?? name}</span>
      {stringArray ? <textarea key={JSON.stringify(value)} rows={3} defaultValue={Array.isArray(value) ? value.join("，") : ""} placeholder="多个名字以逗号或换行分隔" onBlur={(event) => onChange({ ...config, [name]: event.target.value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean) })} /> : Array.isArray(schema.enum) ? <select value={String(value)} onChange={(event) => onChange({ ...config, [name]: event.target.value })}>
        {schema.enum.map((choice) => <option key={String(choice)} value={String(choice)}>{labels[String(choice)] ?? String(choice)}</option>)}
      </select> : schema.type === "boolean" ? <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange({ ...config, [name]: event.target.checked })} /> : <input
        type={schema.type === "string" ? "text" : "number"} value={String(value)}
        min={typeof schema.minimum === "number" ? schema.minimum : undefined} max={typeof schema.maximum === "number" ? schema.maximum : undefined}
        step={schema.type === "integer" ? 1 : 0.1}
        onChange={(event) => {
          if (schema.type === "string") onChange({ ...config, [name]: event.target.value });
          else if (event.target.value !== "" && Number.isFinite(event.target.valueAsNumber)) onChange({ ...config, [name]: event.target.valueAsNumber });
        }} />}
      {schema.description != null && <small>{String(schema.description)}</small>}
    </label>;
  })}</div>;
}

const fastVoiceChoices = [
  ["suyingxue", "苏映雪"], ["gunian", "顾念"], ["bazong", "沉稳男声"], ["bingjiao", "高张力女声"], ["fushiyu", "傅诗语"],
];

function VoiceAssignmentEditor({ config, manifest, onChange }: { config: Record<string, unknown>; manifest: WorkflowNodeManifest; onChange: (config: Record<string, unknown>) => void }) {
  const [newName, setNewName] = useState("");
  const schema = manifest.config_schema.voice_map as { voice_options?: { voice_id: string; label: string }[] } | undefined;
  const choices = schema?.voice_options?.map((voice) => [voice.voice_id, voice.label]) ?? fastVoiceChoices;
  const mapping = config.voice_map && typeof config.voice_map === "object" && !Array.isArray(config.voice_map) ? config.voice_map as Record<string, string> : {};
  function commit(next: Record<string, string>): void { onChange({ ...config, voice_map: next }); }
  return <div className="workflow-voice-editor">
    <strong>角色与极速声线</strong>
    {Object.entries(mapping).map(([name, voice]) => <div className="workflow-voice-row" key={name}>
      <span title={name}>{name}</span>
      <select aria-label={`${name}的声线`} value={voice} onChange={(event) => commit({ ...mapping, [name]: event.target.value })}>
        {choices.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select>
      <button className="icon-button" title={`删除${name}的声线配置`} onClick={() => { const next = { ...mapping }; delete next[name]; commit(next); }}><X size={12} /></button>
    </div>)}
    <div className="workflow-voice-row"><input placeholder="角色名（如萧炎）" value={newName} onChange={(event) => setNewName(event.target.value)} /><button className="secondary-button" disabled={!newName.trim() || Object.prototype.hasOwnProperty.call(mapping, newName.trim())} onClick={() => { commit({ ...mapping, [newName.trim()]: "suyingxue" }); setNewName(""); }}>添加</button></div>
    <small>未配置的角色使用默认声线。质量路线的参考音频可在高级配置 reference_map 中按角色指定。</small>
  </div>;
}

function makeNode(manifest: WorkflowNodeManifest, index: number): DraftNode {
  return {
    node_id: `${manifest.type.replace(/[^A-Za-z0-9]+/g, "-")}-${index + 1}`,
    node_type: manifest.type,
    version: manifest.version,
    title: manifest.label,
    description: "",
    position: { x: 70 + (index % 3) * 270, y: 70 + Math.floor(index / 3) * 210 },
    inputs: {},
    config: manifest.type === "control.condition" ? { logic: "all", conditions: [{ path: "$", operator: "equals", compare_value: null }] } : {},
    depends_on: [],
  };
}

function payloadFrom(name: string, nodes: DraftNode[], edges: WorkflowEdgeDefinition[], existing?: WorkflowDefinition | null, workflowOutputs?: WorkflowOutputBinding[], workflowInputs?: WorkflowInputDefinition[]) {
  return {
    name: name.trim() || DEFAULT_PAYLOAD.name,
    version: existing?.version ?? DEFAULT_PAYLOAD.version,
    description: existing?.description ?? DEFAULT_PAYLOAD.description,
    nodes,
    inputs: workflowInputs ?? existing?.inputs ?? DEFAULT_PAYLOAD.inputs,
    edges,
    outputs: workflowOutputs ?? existing?.outputs ?? DEFAULT_PAYLOAD.outputs,
    allow_pause: existing?.allow_pause ?? true,
    allow_retry: existing?.allow_retry ?? true,
    allow_recovery: existing?.allow_recovery ?? true,
  };
}

function nodeExecutionKey(nodes: WorkflowNodeDefinition[]): string {
  return JSON.stringify(nodes.map((node) => {
    const config = { ...node.config };
    delete config.port_language;
    return { node_id: node.node_id, node_type: node.node_type, version: node.version, inputs: node.inputs, config, depends_on: node.depends_on };
  }));
}

function workflowInputsKey(inputs: WorkflowInputDefinition[]): string {
  return JSON.stringify(inputs);
}

export function WorkflowStudio({ onBack, projectId }: WorkflowStudioProps) {
  const [manifests, setManifests] = useState<WorkflowNodeManifest[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [active, setActive] = useState<WorkflowDefinition | null>(null);
  const [name, setName] = useState(DEFAULT_PAYLOAD.name);
  const [workflowInputs, setWorkflowInputs] = useState<WorkflowInputDefinition[]>([]);
  const [nodes, setNodes] = useState<DraftNode[]>([]);
  const [edges, setEdges] = useState<WorkflowEdgeDefinition[]>([]);
  const [outputs, setOutputs] = useState<WorkflowOutputBinding[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [connectFrom, setConnectFrom] = useState<{ nodeId: string; port: string } | null>(null);
  const [connectPointer, setConnectPointer] = useState<{ x: number; y: number } | null>(null);
  const [validation, setValidation] = useState<WorkflowGraphValidation | null>(null);
  const [configText, setConfigText] = useState("{}");
  const [busy, setBusy] = useState<"load" | "save" | "validate" | "publish" | null>("load");
  const [run, setRun] = useState<WorkflowRun | null>(null);
  const [view, setView] = useState<"canvas" | "trace">("canvas");
  const [panelSizes, setPanelSizes] = useState(savedPanelSizes);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const runPanelRef = useRef<HTMLElement | null>(null);
  const [workspaceSize, setWorkspaceSize] = useState({ width: 1200, height: 524 });
  useLayoutEffect(() => {
    const element = workspaceRef.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      const { width, height: canvasHeight } = element.getBoundingClientRect();
      // Measure the shared space so resizing the console does not change its own limit.
      const height = canvasHeight + (runPanelRef.current?.getBoundingClientRect().height ?? 44);
      if (width > 0 && height > 0) setWorkspaceSize((current) => current.width === width && current.height === height ? current : { width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    try { localStorage.setItem(PANEL_SIZES_KEY, JSON.stringify(panelSizes)); } catch { /* resizing still works when storage is unavailable */ }
  }, [panelSizes]);
  // Keep space for the canvas even when a saved layout is opened in a smaller window.
  const inspectorWidth = Math.min(panelSizes.inspector, Math.max(180, workspaceSize.width - 140 - 320 - 16));
  const libraryMax = Math.max(140, Math.min(360, workspaceSize.width - inspectorWidth - 320 - 16));
  const libraryWidth = Math.min(panelSizes.library, libraryMax);
  const inspectorMax = Math.max(180, Math.min(460, workspaceSize.width - libraryWidth - 320 - 16));
  const consoleMax = Math.max(44, Math.min(480, workspaceSize.height - 240));
  const consoleHeight = Math.min(panelSizes.console, consoleMax);
  const consoleExpanded = consoleHeight > 64;
  function resizePanel(panel: keyof typeof DEFAULT_PANEL_SIZES, value: number): void {
    setPanelSizes((current) => ({ ...current, [panel]: value }));
  }
  const panelStyle = { "--workflow-library-width": `${libraryWidth}px`, "--workflow-inspector-width": `${inspectorWidth}px`, "--workflow-console-height": `${consoleHeight}px` } as CSSProperties;
  const [traceNodeId, setTraceNodeId] = useState<string | null>(null);
  const [runHistory, setRunHistory] = useState<WorkflowRun[]>([]);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historyRefresh, setHistoryRefresh] = useState(0);
  const activeWorkflowId = useRef<string | null>(null);
  activeWorkflowId.current = active?.workflow_id ?? null;
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const locateAfterSwitch = useRef<string | null>(null);
  const focusedWorkflow = useRef<string | null>(null);
  const [runInputText, setRunInputText] = useState('{"text":""}');
  const [runBusy, setRunBusy] = useState<"start" | "pause" | "resume" | "retry" | "cancel" | "approve" | "reject" | null>(null);
  const [reviewComment, setReviewComment] = useState("");
  const [message, setMessage] = useState("");
  const drag = useRef<{ nodeId: string; dx: number; dy: number } | null>(null);
  const importFileRef = useRef<HTMLInputElement | null>(null);
  const portElements = useRef(new Map<string, HTMLElement>());
  const [portOffsets, setPortOffsets] = useState<Record<string, { x: number; y: number }>>({});

  useLayoutEffect(() => {
    if (view !== "canvas") return;
    const offsets: Record<string, { x: number; y: number }> = {};
    portElements.current.forEach((element, key) => {
      const card = element.closest(".workflow-node-card");
      if (!card) return;
      const bounds = element.getBoundingClientRect();
      const cardBounds = card.getBoundingClientRect();
      offsets[key] = { x: bounds.left + bounds.width / 2 - cardBounds.left, y: bounds.top + bounds.height / 2 - cardBounds.top };
    });
    setPortOffsets(offsets);
  }, [nodes, edges, manifests, connectFrom?.nodeId, connectFrom?.port, view]);

  useEffect(() => {
    if (view !== "canvas" || !locateAfterSwitch.current) return;
    const nodeId = locateAfterSwitch.current;
    locateAfterSwitch.current = null;
    const card = Array.from(canvasRef.current?.querySelectorAll<HTMLElement>("[data-node-id]") ?? []).find((element) => element.dataset.nodeId === nodeId);
    card?.scrollIntoView({ block: "center", inline: "center" });
  }, [view, selectedNodeId]);

  useEffect(() => {
    const workflowId = active?.workflow_id ?? "unsaved";
    if (view !== "canvas" || !nodes.length || focusedWorkflow.current === workflowId) return;
    focusedWorkflow.current = workflowId;
    if (selectedNodeId) return;
    const entry = nodes.find((node) => node.node_type === "text.import" || node.node_type === "text.input") ?? nodes[0];
    canvasRef.current?.scrollTo({ left: Math.max(0, entry.position.x - 30), top: Math.max(0, entry.position.y - 30) });
  }, [active?.workflow_id, nodes, view, selectedNodeId]);

  useEffect(() => {
    const workflowId = active?.workflow_id;
    setRun((current) => current?.workflow_id === workflowId ? current : null);
    setRunHistory((current) => current.filter((item) => item.workflow_id === workflowId));
    setTraceNodeId(null);
    setHistoryError("");
    if (!workflowId) { setHistoryBusy(false); return; }
    let cancelled = false;
    setHistoryBusy(true);
    void fetchWorkflowRuns(workflowId).then((history) => {
      if (cancelled) return;
      setRunHistory((current) => [...history, ...current.filter((item) => item.workflow_id === workflowId && !history.some((saved) => saved.run_id === item.run_id))].sort((a, b) => b.created_at.localeCompare(a.created_at)));
      setRun((current) => current?.workflow_id === workflowId ? history.find((item) => item.run_id === current.run_id) ?? current : history[0] ?? null);
    }).catch((error) => {
      if (!cancelled) setHistoryError(error instanceof Error ? error.message : "运行历史读取失败");
    }).finally(() => { if (!cancelled) setHistoryBusy(false); });
    return () => { cancelled = true; };
  }, [active?.workflow_id, historyRefresh]);

  useEffect(() => {
    if (run) setRunHistory((current) => [run, ...current.filter((item) => item.run_id !== run.run_id)].sort((a, b) => b.created_at.localeCompare(a.created_at)));
  }, [run]);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([fetchWorkflowNodeTypes(), fetchWorkflows(), projectId ? fetchProjectWorkflow(projectId) : Promise.resolve(null)]).then(([nextManifests, nextWorkflows, projectWorkflow]) => {
      if (cancelled) return;
      setManifests(nextManifests);
      setWorkflows(nextWorkflows);
      const first = projectWorkflow?.workflow ?? nextWorkflows[0];
      if (first) {
        setActive(first);
        setName(first.name);
        setWorkflowInputs(first.inputs ?? []);
        setNodes(first.nodes.map((node) => ({ ...node, position: node.position ?? { x: 70, y: 70 } })));
        setEdges(first.edges ?? []);
        setOutputs(first.outputs ?? []);
      }
    }).catch((error) => {
      if (!cancelled) setMessage(error instanceof Error ? error.message : "工作流元数据读取失败");
    }).finally(() => {
      if (!cancelled) setBusy(null);
    });
    return () => { cancelled = true; };
  }, [projectId]);

  useEffect(() => {
    if (!run || !["queued", "running", "paused", "awaiting_review"].includes(run.status)) return;
    let cancelled = false;
    let inFlight = false;
    const timer = window.setInterval(() => {
      if (inFlight) return;
      inFlight = true;
      void fetchWorkflowRun(run.run_id).then((next) => {
        if (!cancelled) setRun((current) => current?.run_id === next.run_id ? next : current);
      }).catch(() => undefined).finally(() => { inFlight = false; });
    }, 800);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [run?.run_id, run?.status]);

  const manifestByType = useMemo(() => new Map(manifests.map((manifest) => [manifest.type, manifest])), [manifests]);
  const connectionTargets = useMemo(() => {
    const checks = new Map<string, WorkflowConnectionCheck>();
    if (!connectFrom) return checks;
    for (const node of nodes) {
      const ports = [...(manifestByType.get(node.node_type)?.inputs.map((port) => port.name) ?? []), "__route__"];
      for (const port of ports) {
        checks.set(`${node.node_id}:${port}`, checkWorkflowConnection(nodes, edges, manifestByType, connectFrom, { nodeId: node.node_id, port }));
      }
    }
    return checks;
  }, [nodes, edges, manifestByType, connectFrom]);
  const selectedNode = nodes.find((node) => node.node_id === selectedNodeId) ?? null;
  const selectedManifest = selectedNode ? manifestByType.get(selectedNode.node_type) : null;
  const selectedEdge = edges.find((edge) => edge.edge_id === selectedEdgeId) ?? null;
  const pendingReview = run?.node_runs.find((node) => node.status === "awaiting_review" && node.review) ?? null;
  useEffect(() => {
    setConfigText(selectedNode ? JSON.stringify(selectedNode.config, null, 2) : "{}");
  }, [selectedNodeId]);
  const groupedManifests = useMemo(() => manifests.reduce<Record<string, WorkflowNodeManifest[]>>((groups, manifest) => {
    (groups[manifest.category] ??= []).push(manifest);
    return groups;
  }, {}), [manifests]);

  function newWorkflow(): void {
    setView("canvas");
    focusedWorkflow.current = null;
    setActive(null);
    setName(DEFAULT_PAYLOAD.name);
    setWorkflowInputs([]);
    setNodes([]);
    setEdges([]);
    setOutputs([]);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setConfigText("{}");
    setValidation(null);
    setRunInputText('{"text":""}');
    setMessage("已创建空白草稿");
  }

  function loadWorkflow(workflow: WorkflowDefinition): void {
    focusedWorkflow.current = null;
    setActive(workflow);
    setName(workflow.name);
    setWorkflowInputs(workflow.inputs ?? []);
    setNodes(workflow.nodes.map((node) => ({ ...node, position: node.position ?? { x: 70, y: 70 } })));
    setEdges(workflow.edges ?? []);
    setOutputs(workflow.outputs ?? []);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setConfigText("{}");
    setValidation(null);
    setRunInputText(JSON.stringify(Object.fromEntries(workflow.inputs.filter((input) => input.default != null).map((input) => [input.name, input.default])), null, 2));
    setMessage(`已载入 ${workflow.name}`);
  }

  function addNode(manifest: WorkflowNodeManifest): void {
    let next = makeNode(manifest, nodes.length);
    let suffix = nodes.length + 1;
    while (nodes.some((node) => node.node_id === next.node_id)) {
      next = makeNode(manifest, suffix++);
    }
    setNodes((current) => [...current, next]);
    setSelectedNodeId(next.node_id);
    setMessage(`已添加「${manifest.label}」，点击输出端口再点击目标输入端口即可连线`);
  }

  function loadMultiRoleDemo(): void {
    const requiredTypes = ["text.import", "text.analysis", "voice.assign_voices", "voice.render_settings", "voice.batch_render", "audio.rvc", "audio.loudness", "audio.merge"];
    if (requiredTypes.some((type) => !manifestByType.has(type))) {
      setMessage("多角色 Demo 所需节点尚未加载，请稍后再试");
      return;
    }
    setView("canvas");
    focusedWorkflow.current = null;
    const create = (type: string, nodeId: string, title: string, position: { x: number; y: number }, inputs: Record<string, unknown>, config: Record<string, unknown> = {}, dependsOn: string[] = []): DraftNode => {
      const manifest = manifestByType.get(type);
      if (!manifest) throw new Error(`工作流节点未注册：${type}`);
      return { ...makeNode(manifest, 0), node_id: nodeId, title, position, inputs, config: { port_language: "zh", ...config }, depends_on: dependsOn };
    };
    setActive(null);
    setName("多角色配音 Demo");
    setWorkflowInputs([{ name: "project_id", label: "项目 ID", type: "string", required: true, multiple: false, description: "项目运行上下文", default: "demo", widget: "auto", options: [] }]);
    setNodes([
      create("text.import", "source", "导入文本", { x: 30, y: 250 }, { project_id: "$input.project_id" }),
      create("text.analysis", "analysis", "识别角色与分段", { x: 320, y: 250 }, {}, { speaker_names: ["林默", "苏晚"] }),
      create("voice.assign_voices", "cast", "角色声线分配", { x: 610, y: 250 }, {}, { voice_map: { "旁白": "gunian", "林默": "bazong", "苏晚": "suyingxue", "未指定说话人": "fushiyu" } }),
      create("voice.render_settings", "settings", "选择推理模型", { x: 900, y: 20 }, {}, { route: "fast", quality_model: "gpt_sovits_v2", fast_speed: 1.0 }),
      create("voice.batch_render", "render", "批量语音生成", { x: 900, y: 300 }, {}),
      create("audio.rvc", "rvc", "RVC 音色处理", { x: 1190, y: 250 }, {}, { enabled: false }),
      create("audio.loudness", "loudness", "统一节目响度", { x: 1480, y: 250 }, {}),
      create("audio.merge", "merge", "合并导出", { x: 1770, y: 250 }, {}),
    ]);
    setEdges([
      { edge_id: "source-analysis", source_node: "source", source_port: "text", target_node: "analysis", target_port: "text", kind: "data" },
      { edge_id: "analysis-cast", source_node: "analysis", source_port: "segments", target_node: "cast", target_port: "segments", kind: "data" },
      { edge_id: "cast-render", source_node: "cast", source_port: "segments", target_node: "render", target_port: "segments", kind: "data" },
      { edge_id: "settings-render", source_node: "settings", source_port: "render_settings", target_node: "render", target_port: "render_settings", kind: "data" },
      { edge_id: "render-rvc", source_node: "render", source_port: "job_ids", target_node: "rvc", target_port: "job_ids", kind: "data" },
      { edge_id: "settings-rvc", source_node: "settings", source_port: "route", target_node: "rvc", target_port: "route", kind: "data" },
      { edge_id: "rvc-loudness", source_node: "rvc", source_port: "job_ids", target_node: "loudness", target_port: "job_ids", kind: "data" },
      { edge_id: "loudness-merge", source_node: "loudness", source_port: "job_ids", target_node: "merge", target_port: "job_ids", kind: "data" },
      { edge_id: "settings-merge", source_node: "settings", source_port: "route", target_node: "merge", target_port: "route", kind: "data" },
      ...["render", "rvc", "loudness", "merge"].map((target) => ({ edge_id: `source-${target}-project`, source_node: "source", source_port: "project_id", target_node: target, target_port: "project_id", kind: "data" as const })),
    ]);
    setOutputs([
      { name: "audio_url", type: "string", source_node: "merge", source_port: "audio_url" },
      { name: "job_ids", type: "job_ids", source_node: "render", source_port: "job_ids" },
      { name: "source_name", type: "string", source_node: "source", source_port: "source_name" },
    ]);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setConfigText("{}");
    setValidation(null);
    setRun(null);
    setRunInputText(JSON.stringify({ project_id: "demo", file_name: "demo-script.txt", text: "雨声落在窗边。林默说道：‘我们走。’苏晚问道：‘现在吗？’" }, null, 2));
    setMessage("已加载分步多角色 Demo：导入 → 识别 → 声线分配 → 生成 → RVC → 响度 → 合并；推理设置可单独编辑");
  }

  async function importWorkflowText(file: File, nodeId?: string): Promise<void> {
    if (!file.name.toLowerCase().endsWith(".txt")) {
      setMessage("工作流导入目前只支持 TXT 文件");
      return;
    }
    try {
      const bytes = await file.arrayBuffer();
      let text: string;
      try {
        text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      } catch {
        text = new TextDecoder("gb18030", { fatal: true }).decode(bytes);
      }
      text = text.replace(/^\uFEFF/, "");
      if (!text.trim()) {
        setMessage("导入的 TXT 文件为空");
        return;
      }
      const sourceNodes = nodes.filter((node) => node.node_type === "text.import");
      const targetId = nodeId ?? (sourceNodes.length === 1 ? sourceNodes[0].node_id : undefined);
      if (targetId) {
        setNodes((current) => current.map((node) => {
          if (node.node_id !== targetId) return node;
          const inputs = { ...node.inputs };
          const config = { ...node.config, text, file_name: file.name };
          delete inputs.text;
          delete inputs.file_path;
          delete inputs.file_name;
          delete (config as Record<string, unknown>).file_path;
          return { ...node, inputs, config };
        }));
      }
      let input: Record<string, unknown> = {};
      try {
        const parsed = JSON.parse(runInputText || "{}");
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) input = parsed as Record<string, unknown>;
      } catch { /* replace invalid run JSON with a valid import payload */ }
      const savedProjectId = active?.inputs.find((item) => item.name === "project_id")?.default;
      input.project_id = typeof input.project_id === "string" && input.project_id.trim() ? input.project_id : typeof savedProjectId === "string" && savedProjectId.trim() ? savedProjectId : "demo";
      if (!targetId) {
        input.file_name = file.name;
        input.text = text;
      } else {
        delete input.text;
        delete input.file_name;
      }
      delete input.file_path;
      setRunInputText(JSON.stringify(input, null, 2));
      setMessage(`已导入 ${file.name}（${text.trim().length} 字）${targetId ? "，请保存草稿并发布以保留节点文件" : "，已填入运行输入"}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "TXT 文件读取失败");
    } finally {
      if (importFileRef.current) importFileRef.current.value = "";
    }
  }

  function startDrag(event: React.PointerEvent, node: DraftNode): void {
    const canvasElement = event.currentTarget.closest(".workflow-canvas");
    if (!canvasElement) return;
    const canvas = canvasElement.getBoundingClientRect();
    drag.current = { nodeId: node.node_id, dx: event.clientX - canvas.left + canvasElement.scrollLeft - node.position.x, dy: event.clientY - canvas.top + canvasElement.scrollTop - node.position.y };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }

  function canvasPoint(event: React.PointerEvent): { x: number; y: number } | null {
    const canvas = event.currentTarget.closest(".workflow-canvas") as HTMLElement | null;
    if (!canvas) return null;
    const bounds = canvas.getBoundingClientRect();
    return {
      x: event.clientX - bounds.left + canvas.scrollLeft,
      y: event.clientY - bounds.top + canvas.scrollTop,
    };
  }

  function moveDrag(event: React.PointerEvent): void {
    const point = canvasPoint(event);
    if (connectFrom && point) setConnectPointer(point);
    if (!drag.current) return;
    const canvas = event.currentTarget.closest(".workflow-canvas")?.getBoundingClientRect();
    if (!canvas) return;
    const current = drag.current;
    const canvasElement = event.currentTarget.closest(".workflow-canvas") as HTMLElement;
    const x = Math.max(12, event.clientX - canvas.left + canvasElement.scrollLeft - current.dx);
    const y = Math.max(12, event.clientY - canvas.top + canvasElement.scrollTop - current.dy);
    setNodes((items) => items.map((node) => node.node_id === current.nodeId ? { ...node, position: { x, y } } : node));
  }

  function stopDrag(): void { drag.current = null; }

  function stopCanvasPointer(event: React.PointerEvent<HTMLDivElement>): void {
    stopDrag();
    const target = event.target as HTMLElement;
    if (connectFrom && !target.closest(".workflow-port")) {
      setConnectFrom(null);
      setConnectPointer(null);
    }
  }

  function leaveCanvas(event: React.PointerEvent<HTMLDivElement>): void {
    stopDrag();
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setConnectFrom(null);
      setConnectPointer(null);
    }
  }

  function connectionAnchor(nodeId: string, port: string, direction: "input" | "output" = "output"): { x: number; y: number } | null {
    const source = nodes.find((node) => node.node_id === nodeId);
    const offset = portOffsets[`${nodeId}:${direction}:${port}`] ?? { x: direction === "output" ? 238 : 0, y: 92 };
    return source ? { x: source.position.x + offset.x, y: source.position.y + offset.y } : null;
  }

  function connectOutput(nodeId: string, port: string): void {
    setConnectFrom({ nodeId, port });
    setConnectPointer(connectionAnchor(nodeId, port));
    const manifest = manifestByType.get(nodes.find((node) => node.node_id === nodeId)?.node_type ?? "");
    const isBranch = isBranchOutput(manifest, port);
    setMessage(isBranch ? "已选择条件输出，请点击目标路由入口或兼容的数据输入端口" : "已选择输出端口，请点击另一个节点的输入端口完成连线");
  }

  function beginConnect(event: React.PointerEvent<HTMLButtonElement>, nodeId: string, port: string): void {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    setConnectFrom({ nodeId, port });
    setConnectPointer(canvasPoint(event) ?? connectionAnchor(nodeId, port));
    const manifest = manifestByType.get(nodes.find((node) => node.node_id === nodeId)?.node_type ?? "");
    const isBranch = isBranchOutput(manifest, port);
    setMessage(isBranch ? "拖动到路由入口创建条件分支，或连接兼容的数据输入端口" : "拖动到目标节点的输入端口以创建数据连线");
  }

  function connectInput(nodeId: string, port: string): void {
    if (!connectFrom) return;
    const check = checkWorkflowConnection(nodes, edges, manifestByType, connectFrom, { nodeId, port });
    if (!check.allowed) {
      setMessage(`连线已拒绝：${check.reason}`);
      setConnectFrom(null);
      setConnectPointer(null);
      return;
    }

    const edge: WorkflowEdgeDefinition = {
      edge_id: `edge-${connectFrom.nodeId}-${connectFrom.port}-${nodeId}-${port}`,
      source_node: connectFrom.nodeId,
      source_port: connectFrom.port,
      target_node: nodeId,
      target_port: port,
      kind: check.kind,
    };
    setEdges((current) => current.some((item) => item.source_node === edge.source_node && item.source_port === edge.source_port && item.target_node === edge.target_node && item.target_port === edge.target_port) ? current : [...current, edge]);
    setConnectFrom(null);
    setConnectPointer(null);
    setSelectedEdgeId(edge.edge_id);
    setMessage(check.kind === "branch" ? "条件分支连线已添加" : "数据连线已添加");
  }

  function inputConnectionProps(nodeId: string, port: string, description: string) {
    const check = connectionTargets.get(`${nodeId}:${port}`);
    return {
      "data-connection-state": check ? (check.allowed ? "allowed" : "rejected") : undefined,
      "aria-disabled": check ? !check.allowed : undefined,
      title: check ? `${description}\n${check.allowed ? "可连接" : `不可连接：${check.reason}`}` : description,
    };
  }

  function removeSelected(): void {
    if (!selectedNodeId) return;
    setNodes((current) => current.filter((node) => node.node_id !== selectedNodeId));
    setEdges((current) => current.filter((edge) => edge.source_node !== selectedNodeId && edge.target_node !== selectedNodeId));
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
  }

  function removeSelectedEdge(): void {
    if (!selectedEdgeId) return;
    setEdges((current) => current.filter((edge) => edge.edge_id !== selectedEdgeId));
    setSelectedEdgeId(null);
    setMessage("连线已删除");
  }

  function updateSelected(changes: Partial<DraftNode>): void {
    if (!selectedNodeId) return;
    setNodes((current) => current.map((node) => node.node_id === selectedNodeId ? { ...node, ...changes } : node));
  }

  async function validateDraft(): Promise<WorkflowGraphValidation | null> {
    setBusy("validate");
    try {
      const result = await validateWorkflow(payloadFrom(name, nodes, edges, active, outputs, workflowInputs));
      setValidation(result);
      setMessage(result.valid ? "工作流校验通过" : "工作流校验未通过，请修正画布中的问题");
      return result;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "工作流校验失败");
      return null;
    } finally { setBusy(null); }
  }

  async function saveDraft(): Promise<void> {
    setBusy("save");
    try {
      const isPublished = active?.lifecycle === "published";
      const draftName = isPublished && !name.includes("草稿") ? `${name}（草稿）` : name;
      const payload = payloadFrom(draftName, nodes, edges, active, outputs, workflowInputs);
      const next = active && !isPublished ? await updateWorkflow(active.workflow_id, payload) : await createWorkflow(payload);
      setActive(next);
      setWorkflows((current) => [next, ...current.filter((item) => item.workflow_id !== next.workflow_id)]);
      setName(next.name);
      setWorkflowInputs(next.inputs ?? []);
      setNodes(next.nodes.map((node) => ({ ...node, position: node.position ?? { x: 70, y: 70 } })));
      setEdges(next.edges ?? []);
      setOutputs(next.outputs ?? []);
      setMessage(isPublished ? "已从已发布版本复制为新草稿" : "草稿已保存");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "工作流保存失败");
    } finally { setBusy(null); }
  }

  async function publish(): Promise<void> {
    const result = await validateDraft();
    if (!result?.valid || !active) {
      if (!active) setMessage("请先保存草稿，再发布工作流");
      return;
    }
    setBusy("publish");
    try {
      if (active.lifecycle === "published" && (JSON.stringify(nodes) !== JSON.stringify(active.nodes) || JSON.stringify(edges) !== JSON.stringify(active.edges) || workflowInputsKey(workflowInputs) !== workflowInputsKey(active.inputs))) {
        setMessage("已发布版本有更改，请先保存为新草稿，再发布");
        return;
      }
      if (active.lifecycle !== "published") await updateWorkflow(active.workflow_id, payloadFrom(name, nodes, edges, active, outputs, workflowInputs));
      const next = await publishWorkflow(active.workflow_id);
      setActive(next);
      setWorkflows((current) => current.map((item) => item.workflow_id === next.workflow_id ? next : item));
      if (projectId) {
        await bindProjectWorkflow(projectId, next.workflow_id);
        setMessage("工作流已发布，并已绑定到当前项目");
      } else {
        setMessage("工作流已发布，可供运行实例固定引用");
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "工作流发布失败");
    } finally { setBusy(null); }
  }

  async function startRun(): Promise<void> {
    if (!active || active.lifecycle !== "published") {
      setMessage("请先保存并发布工作流，再启动运行");
      return;
    }
    if (nodeExecutionKey(nodes) !== nodeExecutionKey(active.nodes) || JSON.stringify(edges) !== JSON.stringify(active.edges) || JSON.stringify(outputs) !== JSON.stringify(active.outputs) || workflowInputsKey(workflowInputs) !== workflowInputsKey(active.inputs)) {
      setMessage("节点或连线有未发布的更改，请先保存草稿并发布再运行");
      return;
    }
    setRunBusy("start");
    try {
      const parsed = JSON.parse(runInputText || "{}");
      const next = await createWorkflowRun(active.workflow_id, parsed);
      if (activeWorkflowId.current !== next.workflow_id) return;
      setRun(next);
      setTraceNodeId(null);
      setView("trace");
      setReviewComment("");
      setMessage("工作流运行已启动");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "运行输入必须是合法 JSON");
    } finally { setRunBusy(null); }
  }

  async function controlRun(action: "pause" | "resume" | "retry" | "cancel"): Promise<void> {
    if (!run) return;
    setRunBusy(action);
    try {
      const next = await workflowRunAction(run.run_id, action);
      setRun((current) => current?.run_id === next.run_id ? next : current);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "工作流运行控制失败");
    } finally { setRunBusy(null); }
  }

  async function reviewNode(decision: "approved" | "rejected"): Promise<void> {
    if (!run || !pendingReview) return;
    setRunBusy(decision === "approved" ? "approve" : "reject");
    try {
      const next = await reviewWorkflowNode(run.run_id, pendingReview.node_id, decision, reviewComment);
      setRun((current) => current?.run_id === next.run_id ? next : current);
      setReviewComment("");
      setMessage(decision === "approved" ? "审核已批准，工作流继续执行" : "审核已拒绝，工作流已终止");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "人工审核提交失败");
    } finally { setRunBusy(null); }
  }

  function runNodeStatus(nodeId: string): string {
    return run?.node_runs.find((node) => node.node_id === nodeId)?.status ?? "pending";
  }

  function showNodeTrace(nodeId: string): void {
    setTraceNodeId(nodeId);
    setConnectFrom(null);
    setConnectPointer(null);
    setView("trace");
  }

  function locateNode(nodeId: string): void {
    const recorded = run?.node_runs.find((node) => node.node_id === nodeId);
    if (!nodes.some((node) => node.node_id === nodeId && node.node_type === recorded?.node_type)) {
      setMessage("当前草稿中没有这个运行节点，可继续查看历史记录");
      return;
    }
    locateAfterSwitch.current = nodeId;
    setSelectedNodeId(nodeId);
    setSelectedEdgeId(null);
    setView("canvas");
  }

  function edgePath(edge: WorkflowEdgeDefinition): string | null {
    const source = connectionAnchor(edge.source_node, edge.source_port);
    const target = connectionAnchor(edge.target_node, edge.target_port, "input");
    if (!source || !target) return null;
    const sx = source.x;
    const sy = source.y;
    const tx = target.x;
    const ty = target.y;
    const curve = Math.max(40, Math.abs(tx - sx) * 0.45);
    return `M ${sx} ${sy} C ${sx + curve} ${sy}, ${tx - curve} ${ty}, ${tx} ${ty}`;
  }

  function connectingPath(): string | null {
    if (!connectFrom || !connectPointer) return null;
    const start = connectionAnchor(connectFrom.nodeId, connectFrom.port);
    if (!start) return null;
    const curve = Math.max(40, Math.abs(connectPointer.x - start.x) * 0.45);
    return `M ${start.x} ${start.y} C ${start.x + curve} ${start.y}, ${connectPointer.x - curve} ${connectPointer.y}, ${connectPointer.x} ${connectPointer.y}`;
  }

  const draftPath = connectingPath();

  return (
    <section className={`workflow-studio workflow-studio--dual-view workflow-studio--resizable workflow-studio--${view}`} style={panelStyle}>
      <header className="workflow-studio-header">
        <div className="workflow-studio-heading"><h1 title="拖入能力节点，用端口连线，保存草稿后发布可运行版本。">音声工作流</h1><div className="workflow-studio-view-switch" role="group" aria-label="工作流视图"><button type="button" aria-pressed={view === "canvas"} onClick={() => setView("canvas")}>编排画布</button><button type="button" aria-pressed={view === "trace"} onClick={() => { setConnectFrom(null); setConnectPointer(null); setView("trace"); }}>运行详情{run ? ` · ${workflowStatusLabels[run.status]}` : ""}</button></div></div>
        <div className="workflow-studio-actions"><button className="secondary-button" onClick={onBack}><X size={14} />返回平台</button><button className="secondary-button" onClick={newWorkflow}><Plus size={14} />新建</button><button className="secondary-button" onClick={loadMultiRoleDemo}><WandSparkles size={14} />多角色 Demo</button><button className="secondary-button" onClick={() => void validateDraft()} disabled={busy !== null || runBusy !== null}><Check size={14} />校验</button><button className="secondary-button" onClick={() => void saveDraft()} disabled={busy !== null || runBusy !== null}><Save size={14} />保存草稿</button><button className="primary-button" onClick={() => void publish()} disabled={busy !== null || runBusy !== null || !active}><Send size={14} />发布版本</button><button className="run-button" onClick={() => void startRun()} disabled={runBusy !== null || busy !== null || active?.lifecycle !== "published"}><Play size={14} />运行</button></div>
      </header>
      <div className="workflow-workspace" ref={workspaceRef}>
      <div className="workflow-studio-layout" hidden={view !== "canvas"}>
        <aside id="workflow-node-library" className="workflow-node-palette"><div className="workflow-panel-title"><div><span className="eyebrow">NODE LIBRARY</span><h2>能力节点</h2></div><GitBranch size={17} /></div>{Object.entries(groupedManifests).map(([category, items]) => <div className="workflow-node-group" key={category}><span>{categoryLabels[category] ?? category}</span>{items.map((manifest) => <button key={manifest.type} className="workflow-palette-node" onClick={() => addNode(manifest)} title={manifest.description}><Plus size={13} /><span>{manifest.label}</span><small>{manifest.type}</small></button>)}</div>)}</aside>
        <WorkflowResizeHandle label="调整能力节点面板宽度" orientation="vertical" value={libraryWidth} min={140} max={libraryMax} controls="workflow-node-library" onChange={(value) => resizePanel("library", value)} onReset={() => resizePanel("library", DEFAULT_PANEL_SIZES.library)} />
        <div className="workflow-canvas-shell">
          <div className="workflow-canvas-toolbar">
            <label className="workflow-existing-select">已有工作流
              <select aria-label="已有工作流" value={active?.workflow_id ?? ""} onChange={(event) => { const workflow = workflows.find((item) => item.workflow_id === event.target.value); if (workflow) loadWorkflow(workflow); }}>
                <option value="">未选择</option>
                {workflows.map((workflow) => <option key={workflow.workflow_id} value={workflow.workflow_id}>{workflow.name}</option>)}
              </select>
            </label>
            <label>工作流名称<input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} /></label>
            <span>{active ? `${active.lifecycle === "published" ? "已发布" : "草稿"} · v${active.version}` : "未保存草稿"}</span>
          </div>
          <div ref={canvasRef} className="workflow-canvas" onPointerMove={moveDrag} onPointerUp={stopCanvasPointer} onPointerLeave={leaveCanvas} onClick={() => { setSelectedNodeId(null); setSelectedEdgeId(null); }}>
            <svg className="workflow-edges" width={Math.max(800, ...nodes.map((node) => node.position.x + 270))} height={Math.max(500, ...nodes.map((node) => node.position.y + 350))}>
              {edges.map((edge) => {
                const path = edgePath(edge);
                if (!path) return null;
                const edgeClass = `${edge.kind === "branch" ? `workflow-edge--branch workflow-edge--${edge.source_port}` : ""} ${selectedEdgeId === edge.edge_id ? "selected" : ""}`;
                return <g key={edge.edge_id} onClick={(event) => { event.stopPropagation(); setSelectedNodeId(null); setSelectedEdgeId(edge.edge_id); }}><path d={path} className={`workflow-edge-hit ${edgeClass}`} /><path d={path} className={`workflow-edge ${edgeClass}`} /></g>;
              })}
              {draftPath && connectPointer && <><path d={draftPath} className="workflow-edge workflow-edge--draft" /><circle cx={connectPointer.x} cy={connectPointer.y} r="4" className="workflow-edge-draft-point" /></>}
            </svg>
            {nodes.map((node) => {
              const manifest = manifestByType.get(node.node_type);
              const selected = selectedNodeId === node.node_id;
              const status = runNodeStatus(node.node_id);
              const execution = run?.node_runs.find((item) => item.node_id === node.node_id);
              const chinesePorts = node.config.port_language !== "en";
              const fileName = String(node.config.file_name || node.config.file_path || (typeof node.inputs.file_path === "string" && !node.inputs.file_path.startsWith("$") ? node.inputs.file_path : ""));
              const portRef = (direction: "input" | "output", port: string) => (element: HTMLElement | null) => {
                const key = `${node.node_id}:${direction}:${port}`;
                if (element) portElements.current.set(key, element);
                else portElements.current.delete(key);
              };
              const linked = edges.some((edge) => edge.source_node === node.node_id || edge.target_node === node.node_id);
              const routed = edges.some((edge) => edge.kind === "branch" && edge.target_node === node.node_id);
              return <article key={node.node_id} data-node-id={node.node_id} className={`workflow-node-card ${linked ? "workflow-node-card--linked" : ""} ${selected ? "selected" : ""} ${connectFrom?.nodeId === node.node_id ? "connecting" : ""} run-${status}`} style={{ left: node.position.x, top: node.position.y }} onClick={(event) => { event.stopPropagation(); setSelectedNodeId(node.node_id); setSelectedEdgeId(null); }}>
                <header onPointerDown={(event) => startDrag(event, node)}>
                  <span className="workflow-node-kind">{manifest?.category ?? "custom"}</span>
                  <button type="button" className="workflow-port-language" aria-label={`${node.title || manifest?.label}：切换为${chinesePorts ? "英文" : "中文"}端口名称`} title={`当前为${chinesePorts ? "中文" : "英文"}，点击切换端口名称`} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => {
                    event.stopPropagation();
                    setNodes((current) => current.map((item) => item.node_id === node.node_id ? { ...item, config: { ...item.config, port_language: chinesePorts ? "en" : "zh" } } : item));
                  }}>{chinesePorts ? "中 / EN" : "EN / 中"}</button>
                  <strong>{node.title || manifest?.label || node.node_type}</strong><small>{node.node_type}{run ? ` · ${status}` : ""}</small>
                </header>
                <div className="workflow-node-ports">
                  <div>
                    {(routed || Boolean(connectFrom && isBranchOutput(manifestByType.get(nodes.find((item) => item.node_id === connectFrom.nodeId)?.node_type ?? ""), connectFrom.port))) && <button className={`workflow-port workflow-port-input workflow-port-route ${routed ? "workflow-port--connected" : ""}`} onPointerUp={(event) => { event.stopPropagation(); connectInput(node.node_id, "__route__"); }} onClick={(event) => { event.stopPropagation(); connectInput(node.node_id, "__route__"); }} {...inputConnectionProps(node.node_id, "__route__", "路由入口：只能接收控制节点的 boolean 分支输出。")}><i ref={portRef("input", "__route__")} />{chinesePorts ? "路由入口" : "route"}</button>}
                    {manifest?.inputs.map((port) => <button key={port.name}
                      className={`workflow-port workflow-port-input ${edges.some((edge) => edge.target_node === node.node_id && edge.target_port === port.name) ? "workflow-port--connected" : ""}`}
                      onPointerUp={(event) => { event.stopPropagation(); connectInput(node.node_id, port.name); }} onClick={(event) => { event.stopPropagation(); connectInput(node.node_id, port.name); }}
                      {...inputConnectionProps(node.node_id, port.name, `${port.label || port.name} (${port.name})\n输入${port.required ? " · 必填" : ""}：${port.description}`)}><i ref={portRef("input", port.name)} />{chinesePorts ? port.label || port.name : port.name}{port.required && <b>*</b>}</button>)}
                  </div>
                  <div>{manifest?.outputs.map((port) => <button key={port.name}
                    className={`workflow-port workflow-port-output ${edges.some((edge) => edge.source_node === node.node_id && edge.source_port === port.name) ? "workflow-port--connected" : ""} ${connectFrom?.nodeId === node.node_id && connectFrom.port === port.name ? "connecting" : ""}`}
                    onPointerDown={(event) => beginConnect(event, node.node_id, port.name)} onClick={(event) => { event.stopPropagation(); connectOutput(node.node_id, port.name); }}
                    title={`${port.label || port.name} (${port.name})\n输出：${port.description}`}>{chinesePorts ? port.label || port.name : port.name}<i ref={portRef("output", port.name)} /></button>)}</div>
                </div>
                {node.node_type === "text.import" && <div className="workflow-node-file" onPointerDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
                  <label className="workflow-file-picker"><FileUp size={13} />选择 TXT 文件<input type="file" accept=".txt,text/plain" aria-label={`${node.title || "导入文本"}：选择 TXT 文件`} onChange={(event) => { const file = event.target.files?.[0]; if (file) void importWorkflowText(file, node.node_id); event.target.value = ""; }} /></label>
                  <span title={fileName || "支持 UTF-8 和 GB18030 编码的 TXT 文件"}>{fileName || "未选择文件"}</span>
                  {typeof node.config.text === "string" && <small>{node.config.text.trim().length.toLocaleString()} 字 · 随工作流保存</small>}
                </div>}
                {execution?.status === "running" && <div className="workflow-node-progress" title={execution.message}><span>{execution.message}</span><progress max={100} value={execution.progress} /></div>}
                {manifest?.execution.requires_human_review && <footer><Hand size={12} />需要人工审核</footer>}
              </article>;
            })}
            {!nodes.length && <div className="workflow-canvas-empty"><GitBranch size={32} /><strong>从左侧添加第一个节点</strong><span>推荐顺序：输入文本 → 文本分析 → 声线画像 → 匹配或生成 → 渲染 → 导出</span></div>}
          </div>
        </div>
        <WorkflowResizeHandle label="调整节点配置面板宽度" orientation="vertical" reverse value={inspectorWidth} min={180} max={inspectorMax} controls="workflow-node-inspector" onChange={(value) => resizePanel("inspector", value)} onReset={() => resizePanel("inspector", DEFAULT_PANEL_SIZES.inspector)} />
        <aside id="workflow-node-inspector" className="workflow-inspector">
          <div className="workflow-panel-title">
            <div><span className="eyebrow">INSPECTOR</span><h2>{selectedEdge ? "连线配置" : "节点配置"}</h2></div>
            <Hand size={17} />
          </div>
          {selectedEdge ? (
            <>
              <div className="workflow-inspector-section">
                <strong>{selectedEdge.kind === "branch" ? "条件分支" : "数据绑定"}</strong>
                <span>{selectedEdge.source_node}.{selectedEdge.source_port}</span>
                <span>→ {selectedEdge.kind === "branch" ? "路由入口" : `${selectedEdge.target_node}.${selectedEdge.target_port}`}</span>
                <span>{selectedEdge.kind === "branch" ? "仅在该条件输出为 true 时允许目标节点运行" : "把源节点输出绑定到目标输入"}</span>
              </div>
              <button className="danger-button" onClick={removeSelectedEdge}><Trash2 size={14} />删除连线</button>
            </>
          ) : selectedNode && selectedManifest ? (
            <>
              <label className="workflow-field">
                <span>节点标题</span>
                <input value={selectedNode.title ?? ""} onChange={(event) => updateSelected({ title: event.target.value })} />
              </label>
              <label className="workflow-field">
                <span>节点类型</span>
                <input value={`${selectedManifest.label} · ${selectedNode.node_type}`} readOnly />
              </label>
              {selectedNode.node_type === "control.condition" && (
                <ConditionGroupEditor config={selectedNode.config} onChange={(config) => updateSelected({ config })} />
              )}
              {selectedNode.node_type === "text.import" && typeof selectedNode.config.text === "string" && <div className="workflow-inspector-section"><strong>已导入文本</strong><span>{String(selectedNode.config.file_name || "TXT 文件")} · {selectedNode.config.text.trim().length.toLocaleString()} 字</span><span>{selectedNode.config.text.slice(0, 140)}{selectedNode.config.text.length > 140 ? "…" : ""}</span></div>}
              {selectedNode.node_type !== "control.condition" && <NodeConfigFields manifest={selectedManifest} config={selectedNode.config} onChange={(config) => { updateSelected({ config }); setConfigText(JSON.stringify(config, null, 2)); }} />}
              {["voice.assign_voices", "voice.multi_role_fast_render"].includes(selectedNode.node_type) && <VoiceAssignmentEditor key={selectedNode.node_id} config={selectedNode.config} manifest={selectedManifest} onChange={(config) => { updateSelected({ config }); setConfigText(JSON.stringify(config, null, 2)); }} />}
              {selectedNode.node_type !== "control.condition" && <details className="workflow-advanced-config"><summary>高级配置（JSON）</summary>
                <label className="workflow-field">
                  <span>配置（JSON）</span>
                  <textarea
                    rows={8}
                    value={configText}
                    onFocus={() => setConfigText(JSON.stringify(selectedNode.config, null, 2))}
                    onChange={(event) => {
                      const raw = event.target.value;
                      setConfigText(raw);
                      try { updateSelected({ config: JSON.parse(raw) }); } catch { /* keep editing invalid JSON until it becomes valid */ }
                    }}
                  />
                </label>
              </details>}
              <div className="workflow-inspector-section">
                <strong>执行策略</strong>
                <span>{selectedManifest.execution.mode} · {selectedManifest.execution.resource_group}</span>
                <span>{selectedManifest.execution.cacheable ? "支持缓存" : "不缓存"}{selectedManifest.execution.requires_human_review ? " · 人工审核" : ""}</span>
              </div>
              <button className="danger-button" onClick={removeSelected}><Trash2 size={14} />删除节点</button>
              {run?.node_runs.some((node) => node.node_id === selectedNode.node_id) && <button type="button" className="secondary-button" onClick={() => showNodeTrace(selectedNode.node_id)}>查看节点运行详情</button>}
            </>
          ) : (
            <div className="workflow-inspector-empty"><CircleAlert size={20} /><span>选择画布中的节点或连线查看配置。</span></div>
          )}
        </aside>
      </div>
      <div className="workflow-trace-workspace" hidden={view !== "trace"}>
        <div className="workflow-trace-toolbar"><label>运行历史<select aria-label="选择运行记录" value={run?.run_id ?? ""} disabled={!runHistory.length || runBusy !== null} onChange={(event) => { setRun(runHistory.find((item) => item.run_id === event.target.value) ?? null); setTraceNodeId(null); setReviewComment(""); }}><option value="" disabled>暂无运行记录</option>{runHistory.map((item) => <option key={item.run_id} value={item.run_id}>{formatWorkflowTime(item.created_at)} · {workflowStatusLabels[item.status]} · {item.run_id.slice(-8)}</option>)}</select></label><button type="button" className="secondary-button" disabled={historyBusy} onClick={() => setHistoryRefresh((current) => current + 1)}>{historyBusy ? "读取中…" : "刷新记录"}</button>{historyError && <span role="alert">{historyError}</span>}</div>
        {view === "trace" && <WorkflowRunDetails run={run} initialNodeId={traceNodeId} onLocateNode={locateNode} />}
      </div>
      </div>
      <WorkflowResizeHandle label="调整运行面板高度" orientation="horizontal" reverse value={consoleHeight} min={44} max={consoleMax} controls="workflow-run-panel" onChange={(value) => resizePanel("console", value)} onReset={() => resizePanel("console", DEFAULT_PANEL_SIZES.console)} />
      <section ref={runPanelRef} id="workflow-run-panel" className={`workflow-run-console${consoleExpanded ? " is-expanded" : ""}`}>
        <header className="workflow-run-panel-heading"><strong>运行与输入</strong><span>{run ? `${workflowStatusLabels[run.status]} · ${run.progress}% · ${run.message}` : "发布工作流后可启动运行"}</span><button type="button" className="workflow-trace-text-button" onClick={() => setPanelSizes(DEFAULT_PANEL_SIZES)} title="恢复默认面板尺寸">重置布局</button><button type="button" className="secondary-button" aria-expanded={consoleExpanded} aria-controls="workflow-run-panel-content" onClick={() => resizePanel("console", consoleExpanded ? 44 : Math.min(240, consoleMax))}>{consoleExpanded ? "收起" : "展开"}</button></header>
        <div id="workflow-run-panel-content" className="workflow-run-panel-content" hidden={!consoleExpanded}>
        <div className="workflow-run-input"><label><span>运行输入（JSON）</span><textarea rows={2} value={runInputText} onChange={(event) => setRunInputText(event.target.value)} /></label><div className="workflow-run-actions"><input ref={importFileRef} type="file" accept=".txt,text/plain" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) void importWorkflowText(file); }} /><button className="secondary-button" onClick={() => importFileRef.current?.click()} title="选择 TXT 剧本"><FileUp size={13} />导入 TXT</button><button className="run-button" onClick={() => void startRun()} disabled={runBusy !== null || busy !== null || active?.lifecycle !== "published"}><Play size={13} />启动运行</button>{run?.status === "running" && <button className="secondary-button" onClick={() => void controlRun("pause")} disabled={runBusy !== null}><Pause size={13} />暂停</button>}{run?.status === "paused" && <button className="secondary-button" onClick={() => void controlRun("resume")} disabled={runBusy !== null}><Play size={13} />恢复</button>}{run?.status === "failed" && <button className="secondary-button" onClick={() => void controlRun("retry")} disabled={runBusy !== null}><RotateCcw size={13} />重试</button>}{run && ["queued", "running", "paused", "awaiting_review"].includes(run.status) && <button className="danger-button" onClick={() => void controlRun("cancel")} disabled={runBusy !== null}><Square size={12} />取消</button>}</div></div>
        {run && pendingReview?.review && <div className="workflow-review-card"><header><div><span className="eyebrow">HUMAN CHECKPOINT</span><strong>{nodes.find((node) => node.node_id === pendingReview.node_id)?.title || pendingReview.node_id}</strong><small>{pendingReview.message}</small></div><Hand size={20} /></header><pre>{JSON.stringify(pendingReview.review.payload, null, 2)}</pre><label><span>审核意见（可选）</span><textarea rows={2} maxLength={1000} value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} placeholder="记录批准或拒绝的原因" /></label><div className="workflow-review-actions"><button className="primary-button" onClick={() => void reviewNode("approved")} disabled={runBusy !== null}><Check size={13} />批准并继续</button><button className="danger-button" onClick={() => void reviewNode("rejected")} disabled={runBusy !== null}><X size={13} />拒绝并终止</button></div></div>}
        {view === "canvas" && run && <div className="workflow-run-summary"><div><span className="eyebrow">RUN {run.run_id.slice(-8)}</span><strong>{workflowStatusLabels[run.status]} · {run.progress}%</strong><small>{run.message}</small><button type="button" className="workflow-trace-text-button" onClick={() => setView("trace")}>查看完整运行详情</button></div><div className="workflow-run-nodes">{run.node_runs.map((node) => <button type="button" key={node.node_id} className={`run-pill run-pill--${node.status}`} title={node.message} onClick={() => showNodeTrace(node.node_id)}>{node.node_id}<b>{workflowStatusLabels[node.status]}</b></button>)}</div></div>}
        {view === "canvas" && run?.status === "failed" && run.error && <div className="workflow-run-error" role="alert"><CircleAlert size={15} /><span>{run.error}</span></div>}
        {view === "canvas" && run?.status === "completed" && workflowMediaUrl(run.outputs.audio_url) && <div className="workflow-result-audio"><strong>多角色音频</strong><audio controls preload="none" src={workflowMediaUrl(run.outputs.audio_url)!} /><a href={workflowMediaUrl(run.outputs.audio_url)!} download>下载合并音频</a></div>}
        </div>
      </section>
      {(busy || runBusy || message || validation) && <footer className="workflow-studio-status">{(busy || runBusy) && <LoaderCircle size={14} className="spin" />}{message && <span>{message}</span>}{validation?.errors.map((error) => <span className="error" key={error}>{error}</span>)}{validation?.warnings.map((warning) => <span className="warning" key={warning}>{warning}</span>)}</footer>}
    </section>
  );
}
