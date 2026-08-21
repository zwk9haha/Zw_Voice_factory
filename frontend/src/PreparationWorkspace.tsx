import { ArrowRight, Check, Gauge, Mic2, RefreshCw, SlidersHorizontal, Sparkles } from "lucide-react";
import { useState } from "react";
import type { InferenceTemplate, ProductionStageId, RouteMode, SourceSummary, WorkspacePayload } from "./types";
import { ProjectPreparationWorkspace } from "./ProjectPreparationWorkspace";
import { VoiceAnalysisSettings } from "./VoiceAnalysisSettings";

type PreparationStageId = Exclude<ProductionStageId, "quality_render">;

interface PreparationWorkspaceProps {
  activeStage: PreparationStageId;
  sources: SourceSummary[];
  selectedProjectId: string | null;
  selectedTemplate: InferenceTemplate;
  workspace: WorkspacePayload;
  routeMode: RouteMode;
  onProjectChange: (projectId: string | null) => void;
  onSourcesChange: (sources: SourceSummary[]) => void;
  onTemplateChange: (templateId: string) => void;
  onStageChange: (stage: ProductionStageId) => void;
  selectedSliceId: string | null;
}

function TemplateWorkspace({ selectedTemplate, workspace, onTemplateChange, onStageChange }: Omit<PreparationWorkspaceProps, "activeStage">) {
  const [applyRequest, setApplyRequest] = useState(0);
  const [applyMessage, setApplyMessage] = useState("");
  const inferenceDescriptions = {
    cloud: "章节、角色、声线和导演脚本全部交给云端 API，精度优先。",
    hybrid: "本地模型先筛选章节、角色与说话人线索，云端 API 再做最终裁决。",
    local: "所有文本与角色证据留在本机，由项目内 Ollama 模型完成推理。",
  };
  const pipelineRows = {
    cloud: [["文本初筛", "云端 API", "章节 / 候选角色"], ["角色与声线", "云端 API", "画像 / 参考文本"], ["导演裁决", "云端 API", "说话人 / 情绪"]],
    hybrid: [["文本初筛", "本地 Ollama", "章节 / 候选角色"], ["角色与声线", "本地 -> 云端", "初筛后精推"], ["导演裁决", "本地 -> 云端", "线索后最终裁决"]],
    local: [["文本初筛", "本地 Ollama", "章节 / 候选角色"], ["角色与声线", "本地 Ollama", "画像 / 参考文本"], ["导演裁决", "本地 Ollama", "说话人 / 情绪"]],
  } as const;
  const routeSummary = {
    cloud: ["云端 API", "云端 API", "项目文本发送到已配置 API"],
    hybrid: ["本地模型 -> 云端 API", "本地模型 -> 云端 API", "云端仅接收原文片段与本地初筛线索"],
    local: ["本地 Ollama", "本地 Ollama", "项目文本不离开本机"],
  } as const;
  return (
    <section className="prep-workspace">
      <aside className="prep-list-pane">
        <div className="pane-heading"><div><span className="eyebrow">INFERENCE TEMPLATES</span><h2>推理模板</h2></div><span className="list-count">{workspace.available_templates.length}</span></div>
        <div className="prep-list">
          {workspace.available_templates.map((template) => (
            <button key={template.template_id} className={template.template_id === selectedTemplate.template_id ? "selected" : ""} onClick={() => onTemplateChange(template.template_id)}>
              <Gauge size={16} />
              <span><strong>{template.display_name}</strong><small>{inferenceDescriptions[template.inference_mode]}</small></span>
              {template.template_id === selectedTemplate.template_id && <Check size={14} />}
            </button>
          ))}
        </div>
      </aside>

      <section className="prep-main-pane">
        <div className="prep-titlebar"><div><span className="eyebrow">PROJECT PRESET</span><h2>{selectedTemplate.display_name}</h2></div><button className="secondary-button" onClick={() => onTemplateChange(workspace.active_template.template_id)}><RefreshCw size={14} />恢复默认</button></div>
        <div className="config-grid">
          <div><span>角色与声线推理</span><strong>{routeSummary[selectedTemplate.inference_mode][0]}</strong></div>
          <div><span>说话人与导演推理</span><strong>{routeSummary[selectedTemplate.inference_mode][1]}</strong></div>
          <div><span>数据去向</span><strong>{routeSummary[selectedTemplate.inference_mode][2]}</strong></div>
        </div>
        <div className="model-role-table">
          <div className="model-role-head"><span>职责</span><span>模型</span><span>执行阶段</span></div>
          {pipelineRows[selectedTemplate.inference_mode].map(([role, model, stage]) => <div key={role}><span>{role}</span><strong>{model}</strong><small>{stage}</small></div>)}
        </div>
        <VoiceAnalysisSettings inferenceMode={selectedTemplate.inference_mode} applyRequest={applyRequest} onApplied={() => { setApplyMessage("模板已生效"); onStageChange("source"); }} onError={setApplyMessage} />
      </section>

      <aside className="prep-check-pane">
        <div className="pane-heading"><div><span className="eyebrow">MODEL READINESS</span><h2>资源检查</h2></div></div>
        <div className="readiness-list">
          <div><Mic2 size={15} /><span><strong>VoxCPM2</strong><small>参考生产</small></span><b>可用</b></div>
          <div><Sparkles size={15} /><span><strong>GPT-SoVITS</strong><small>质量渲染</small></span><b>可用</b></div>
          <div><SlidersHorizontal size={15} /><span><strong>RVC</strong><small>稳定层</small></span><b>待测评</b></div>
        </div>
        <div className="stage-action">{applyMessage && <small>{applyMessage}</small>}<button className="primary-button" onClick={() => { setApplyMessage(""); setApplyRequest((current) => current + 1); }}>应用模板<ArrowRight size={15} /></button></div>
      </aside>
    </section>
  );
}

export function PreparationWorkspace(props: PreparationWorkspaceProps) {
  const activeStage = props.activeStage;
  if (activeStage === "template") {
    return <TemplateWorkspace {...props} />;
  }
  return <ProjectPreparationWorkspace activeStage={activeStage} routeMode={props.routeMode} sources={props.sources} selectedProjectId={props.selectedProjectId} selectedSliceId={props.selectedSliceId} onProjectChange={props.onProjectChange} onSourcesChange={props.onSourcesChange} onStageChange={props.onStageChange} />;
}
