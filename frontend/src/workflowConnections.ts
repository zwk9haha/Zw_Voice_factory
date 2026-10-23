import type { WorkflowEdgeDefinition, WorkflowNodeDefinition, WorkflowNodeManifest, WorkflowValueType } from "./types";

export interface WorkflowConnectionEndpoint {
  nodeId: string;
  port: string;
}

export type WorkflowConnectionCheck =
  | { allowed: true; kind: WorkflowEdgeDefinition["kind"] }
  | { allowed: false; reason: string };

export function isBranchOutput(manifest: WorkflowNodeManifest | undefined, portName: string): boolean {
  return manifest?.execution.mode === "control" && manifest.outputs.some((port) => port.name === portName && port.type === "boolean");
}

function portsCompatible(source: WorkflowValueType, target: WorkflowValueType): boolean {
  // Keep the same wire types as WorkflowService.validate_definition.
  return source === target || source === "any" || target === "any" || source === "json" || target === "json"
    || ((source === "text" || source === "string") && (target === "text" || target === "string"));
}

function referencedNodes(value: unknown, result: Set<string>): void {
  if (Array.isArray(value)) {
    value.forEach((item) => referencedNodes(item, result));
  } else if (value && typeof value === "object") {
    const binding = value as Record<string, unknown>;
    const nodeId = binding.from_node || binding.node_id;
    if (typeof nodeId === "string" && nodeId) result.add(nodeId);
    if (typeof binding.$from === "string" && binding.$from) result.add(binding.$from.split(".", 1)[0]);
    Object.values(binding).forEach((item) => referencedNodes(item, result));
  } else if (typeof value === "string" && value.startsWith("$")) {
    const nodeId = value.slice(1).split(".", 1)[0];
    if (nodeId && nodeId !== "input") result.add(nodeId);
  }
}

export function checkWorkflowConnection(
  nodes: WorkflowNodeDefinition[],
  edges: WorkflowEdgeDefinition[],
  manifests: ReadonlyMap<string, WorkflowNodeManifest>,
  source: WorkflowConnectionEndpoint,
  target: WorkflowConnectionEndpoint,
): WorkflowConnectionCheck {
  const sourceNode = nodes.find((node) => node.node_id === source.nodeId);
  const targetNode = nodes.find((node) => node.node_id === target.nodeId);
  if (!sourceNode || !targetNode) return { allowed: false, reason: "节点不存在" };
  if (source.nodeId === target.nodeId) return { allowed: false, reason: "不能把节点连接到自身" };
  const sourceManifest = manifests.get(sourceNode.node_type);
  const targetManifest = manifests.get(targetNode.node_type);
  if (!sourceManifest || !targetManifest) return { allowed: false, reason: "节点端口定义尚未加载，无法判断是否可连接" };
  const sourcePort = sourceManifest.outputs.find((port) => port.name === source.port);
  if (!sourcePort) return { allowed: false, reason: "源端口必须是输出端口" };

  const kind = target.port === "__route__" ? "branch" : "data";
  if (kind === "branch" && !isBranchOutput(sourceManifest, source.port)) {
    return { allowed: false, reason: "路由入口只能接收控制节点的 boolean 分支输出" };
  }
  const targetPort = targetManifest.inputs.find((port) => port.name === target.port);
  if (kind === "data") {
    if (!targetPort) return { allowed: false, reason: "目标端口必须是输入端口" };
    if (!portsCompatible(sourcePort.type, targetPort.type)) {
      return { allowed: false, reason: `端口类型不兼容（${sourcePort.type} → ${targetPort.type}）` };
    }
  }
  if (edges.some((edge) => edge.source_node === source.nodeId && edge.source_port === source.port
    && edge.target_node === target.nodeId && edge.target_port === target.port)) {
    return { allowed: false, reason: "连线已存在" };
  }
  if (kind === "data" && !targetPort?.multiple
    && edges.some((edge) => edge.kind === "data" && edge.target_node === target.nodeId && edge.target_port === target.port)) {
    return { allowed: false, reason: `${targetPort?.label || target.port}只能接收一条连线，请先删除已有连线` };
  }

  const successors = new Map<string, Set<string>>();
  function addSuccessor(from: string, to: string): void {
    if (!successors.has(from)) successors.set(from, new Set());
    successors.get(from)!.add(to);
  }
  edges.forEach((edge) => addSuccessor(edge.source_node, edge.target_node));
  nodes.forEach((node) => {
    const dependencies = new Set(node.depends_on);
    referencedNodes(node.inputs, dependencies);
    dependencies.forEach((dependency) => addSuccessor(dependency, node.node_id));
  });
  const visited = new Set<string>();
  const pending = [target.nodeId];
  while (pending.length) {
    const nodeId = pending.pop()!;
    if (nodeId === source.nodeId) return { allowed: false, reason: "会形成工作流环路" };
    if (visited.has(nodeId)) continue;
    visited.add(nodeId);
    pending.push(...(successors.get(nodeId) ?? []));
  }
  return { allowed: true, kind };
}
