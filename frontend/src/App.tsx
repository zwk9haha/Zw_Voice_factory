import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowRight, ChevronDown, CircleStop, Cpu, Library, Save, Settings2, Volume2 } from "lucide-react";
import { useState } from "react";
import { fetchWorkspace } from "./api";
import { demoWorkspace } from "./demo";
import { PreparationWorkspace } from "./PreparationWorkspace";
import { QualityWorkbench } from "./QualityWorkbench";
import type { ProductionStageId } from "./types";
import { WorkflowNav } from "./WorkflowNav";

function App() {
  const { data: workspace = demoWorkspace, isError, isFetching } = useQuery({
    queryKey: ["workspace"],
    queryFn: fetchWorkspace,
    initialData: demoWorkspace,
    retry: false,
  });
  const [activeStage, setActiveStage] = useState<ProductionStageId>("quality_render");
  const [selectedTemplateId, setSelectedTemplateId] = useState(demoWorkspace.active_template.template_id);
  const [rvcEnabled, setRvcEnabled] = useState(false);
  const selectedTemplate = workspace.available_templates.find((template) => template.template_id === selectedTemplateId) ?? workspace.active_template;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><div className="brand-mark"><Volume2 size={18} /></div><strong>Zw Voice Factory</strong><span>2.0</span></div>
        <button className="project-switch"><Library size={15} /><span>{workspace.project.name}</span><ChevronDown size={14} /></button>
        <div className="route-switch" aria-label="朗读路线">
          <button disabled title="极速路线将在质量链路完成后接入">极速</button>
          <button className="active">质量</button>
        </div>
        <div className="top-actions"><span className={`health ${isError ? "health--error" : ""}`}><Activity size={14} />{isError ? "离线数据" : isFetching ? "同步中" : "后端在线"}</span><button className="icon-button" title="保存项目"><Save size={17} /></button><button className="stop-button"><CircleStop size={15} />停止</button></div>
      </header>

      <WorkflowNav stages={workspace.workflow} activeStage={activeStage} onStageChange={setActiveStage} />

      <section className="route-context">
        <button className="template-context" onClick={() => setActiveStage("template")}><Settings2 size={14} /><span>模板</span><strong>{selectedTemplate.display_name}</strong></button>
        <div className="route-pipeline" aria-label="质量路线模型链路">
          <span><small>参考生产</small><strong>VoxCPM2</strong></span><ArrowRight size={14} />
          <span className="active"><small>在线渲染</small><strong>GPT-SoVITS</strong></span><ArrowRight size={14} />
          <span className={rvcEnabled ? "active" : "optional"}><small>可选稳定层</small><strong>RVC</strong></span>
        </div>
        <label className="stability-control">
          <input type="checkbox" checked={rvcEnabled} onChange={(event) => setRvcEnabled(event.target.checked)} />
          <span className="toggle-track"><span /></span>
          <span>RVC 稳定层</span>
          <b>{rvcEnabled ? "启用" : "测评后启用"}</b>
        </label>
        <span className="gpu-state"><Cpu size={14} />GPU 38%</span>
      </section>

      {activeStage === "quality_render" ? (
        <QualityWorkbench workspace={workspace} />
      ) : (
        <PreparationWorkspace
          activeStage={activeStage}
          selectedTemplate={selectedTemplate}
          workspace={workspace}
          onTemplateChange={setSelectedTemplateId}
          onStageChange={setActiveStage}
        />
      )}
    </main>
  );
}

export default App;
