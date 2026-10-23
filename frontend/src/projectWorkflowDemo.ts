import type { WorkflowDefinition, WorkflowEdgeDefinition, WorkflowNodeDefinition } from "./types";

export const UI_DEMO_PROJECT_NAME = "demo · 工作流界面对比";
export const UI_DEMO_TEXT = "旁白：清晨，录音室的灯亮了。\n林舟：今天我们试试新的配音流程。\n苏晴：先确认角色，再听听声音吧。\n旁白：两人检查完台词，开始录制。";

export function projectUiDemoDefinition(): Pick<WorkflowDefinition, "name" | "version" | "description" | "inputs" | "nodes" | "edges" | "outputs" | "allow_pause" | "allow_retry" | "allow_recovery"> {
  const node = (id: string, type: string, title: string, x: number, inputs = {}, config = {}): WorkflowNodeDefinition => ({ node_id: id, node_type: type, title, version: "1.0", description: "", position: { x, y: 180 }, inputs, config, depends_on: [] });
  const edge = (source: string, port: string, target: string, input: string): WorkflowEdgeDefinition => ({ edge_id: `${source}-${target}-${input}`, source_node: source, source_port: port, target_node: target, target_port: input, kind: "data" });
  return {
    name: "UI 对比 · 可审核的多角色配音", version: "1.0", description: "两套项目界面共用此流程。导入文本、校正角色、试听声线、真实生成与导出。", allow_pause: true, allow_retry: true, allow_recovery: true,
    inputs: [
      { name: "text", label: "配音文本", type: "text", required: true, multiple: false, description: "可直接编辑，或用下方 TXT 文件替换。", default: UI_DEMO_TEXT, widget: "textarea" },
    ],
    nodes: [
      node("source", "text.import", "导入文本", 30, { text: "$input.text", project_id: "$input.project_id" }),
      node("analysis", "text.analysis", "识别角色与对白", 320, {}, { speaker_names: ["林舟", "苏晴"] }),
      node("speakers", "human.speakers", "校正说话人", 610),
      node("cast", "voice.assign_voices", "分配角色声线", 900, {}, { voice_map: { "旁白": "gunian", "林舟": "bazong", "苏晴": "suyingxue" } }),
      node("voices", "human.voices", "试听并确认声线", 1190),
      node("render", "voice.batch_render", "生成多角色配音", 1480, { project_id: "$input.project_id", render_settings: { route: "fast", quality_model: "gpt_sovits_v2", fast_speed: 1 } }),
      node("merge", "audio.merge", "合并并导出", 1770, { project_id: "$input.project_id", route: "fast" }),
    ],
    edges: [edge("source", "text", "analysis", "text"), edge("analysis", "segments", "speakers", "segments"), edge("speakers", "segments", "cast", "segments"), edge("cast", "segments", "voices", "segments"), edge("voices", "segments", "render", "segments"), edge("render", "job_ids", "merge", "job_ids")],
    outputs: [
      { name: "audio_url", label: "多角色配音", type: "string", display: "audio", source_node: "merge", source_port: "audio_url" },
      { name: "speakers", label: "已确认的角色", type: "json", display: "json", source_node: "voices", source_port: "speakers" },
    ],
  };
}
