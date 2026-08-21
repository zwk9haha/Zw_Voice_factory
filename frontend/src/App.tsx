import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowRight, BellRing, ChevronDown, Cpu, Expand, Eye, EyeOff, FileText, HardDrive, Library, MemoryStick, Moon, Settings2, Sun, Volume2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchContinuousProduction, fetchProductionSettings, fetchRvcWorkspace, fetchSources, fetchSystemResources, fetchWorkspace, updateQualityModel, updateRvcSettings } from "./api";
import { ContinuousSliceNavigator } from "./ContinuousSliceNavigator";
import { FastWorkbench } from "./FastWorkbench";
import { PreparationWorkspace } from "./PreparationWorkspace";
import { QualityWorkbench } from "./QualityWorkbench";
import { RvcWorkbench } from "./RvcWorkbench";
import { RuntimeLogPanel } from "./RuntimeLogPanel";
import type { ProductionStageId, QualityModelId, RouteMode, RvcWorkspace, SourceSummary } from "./types";
import { WorkflowNav } from "./WorkflowNav";

const EMPTY_SOURCES: SourceSummary[] = [];

function App() {
  const queryClient = useQueryClient();
  const rvcButtonRef = useRef<HTMLButtonElement>(null);
  const { data: workspace, isError, isFetching } = useQuery({
    queryKey: ["workspace"],
    queryFn: fetchWorkspace,
    retry: false,
  });
  const [activeStage, setActiveStage] = useState<ProductionStageId>("source");
  const [routeMode, setRouteMode] = useState<RouteMode>(() => localStorage.getItem("zw-route-mode") === "fast" ? "fast" : "quality");
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(() => localStorage.getItem("zw-active-project") ?? localStorage.getItem("zw-quality-project"));
  const [rvcOpen, setRvcOpen] = useState(false);
  const [rvcOrigin, setRvcOrigin] = useState({ x: 0, y: 0 });
  const [rvcError, setRvcError] = useState("");
  const [qualityModel, setQualityModel] = useState<QualityModelId>("gpt_sovits_v2");
  const [qualityModelError, setQualityModelError] = useState("");
  const [theme, setTheme] = useState<"dark" | "light">(() => localStorage.getItem("zw-theme") === "light" ? "light" : "dark");
  const [showResources, setShowResources] = useState(() => localStorage.getItem("zw-resources-visible") !== "false");
  const [showRuntimeLogs, setShowRuntimeLogs] = useState(false);
  const [selectedSliceId, setSelectedSliceId] = useState<string | null>(null);
  const [dismissedRvcReviewKey, setDismissedRvcReviewKey] = useState("");
  const { data: resources } = useQuery({
    queryKey: ["system-resources"],
    queryFn: fetchSystemResources,
    enabled: showResources,
    refetchInterval: showResources ? 5_000 : false,
    refetchIntervalInBackground: false,
    retry: false,
  });
  const { data: productionSettings } = useQuery({
    queryKey: ["production-settings"],
    queryFn: fetchProductionSettings,
    retry: false,
  });
  const { data: sourceData, isFetched: sourcesFetched } = useQuery({
    queryKey: ["sources"],
    queryFn: fetchSources,
    retry: false,
  });
  const sources = sourceData ?? EMPTY_SOURCES;
  const { data: rvcWorkspace } = useQuery({
    queryKey: ["rvc-workspace", activeProjectId],
    queryFn: () => fetchRvcWorkspace(activeProjectId!),
    enabled: Boolean(activeProjectId),
    retry: false,
  });
  const { data: continuousRun } = useQuery({
    queryKey: ["continuous-production", activeProjectId],
    queryFn: () => fetchContinuousProduction(activeProjectId!),
    enabled: Boolean(activeProjectId),
    retry: false,
    refetchInterval: activeProjectId ? 1_000 : false,
    refetchIntervalInBackground: false,
  });
  const selectedTemplate = workspace?.available_templates.find((template) => template.template_id === selectedTemplateId) ?? workspace?.active_template;

  useEffect(() => {
    if (workspace) setSelectedTemplateId(workspace.active_template.template_id);
  }, [workspace]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("zw-theme", theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem("zw-resources-visible", String(showResources));
  }, [showResources]);

  useEffect(() => {
    localStorage.setItem("zw-route-mode", routeMode);
  }, [routeMode]);

  useEffect(() => {
    if (productionSettings) setQualityModel(productionSettings.selected_quality_model);
  }, [productionSettings]);

  useEffect(() => {
    if (!sources.length) {
      if (sourcesFetched) setActiveProjectId(null);
      return;
    }
    if (activeProjectId && sources.some((source) => source.project_id === activeProjectId)) return;
    const remembered = localStorage.getItem("zw-active-project") ?? localStorage.getItem("zw-quality-project");
    const selected = sources.find((source) => source.project_id === remembered)
      ?? sources.find((source) => source.status === "director_ready")
      ?? sources[0];
    setActiveProjectId(selected.project_id);
  }, [activeProjectId, sources, sourcesFetched]);

  useEffect(() => {
    if (!activeProjectId) return;
    localStorage.setItem("zw-active-project", activeProjectId);
    localStorage.setItem("zw-quality-project", activeProjectId);
  }, [activeProjectId]);

  useEffect(() => {
    if (!continuousRun?.slices.length) {
      setSelectedSliceId(null);
      return;
    }
    setSelectedSliceId((current) => (
      current && continuousRun.slices.some((slice) => slice.slice_id === current)
        ? current
        : continuousRun.current_slice_id ?? continuousRun.slices[0].slice_id
    ));
  }, [activeProjectId, continuousRun?.current_slice_id, continuousRun?.run_id, continuousRun?.slices.length]);

  const selectActiveProject = useCallback((projectId: string | null) => {
    setActiveProjectId(projectId);
  }, []);

  const updateSources = useCallback((nextSources: SourceSummary[]) => {
    queryClient.setQueryData<SourceSummary[]>(["sources"], nextSources);
  }, [queryClient]);

  async function selectQualityModel(nextModel: QualityModelId) {
    const previousModel = qualityModel;
    setQualityModel(nextModel);
    setQualityModelError("");
    try {
      await updateQualityModel(nextModel);
    } catch (error) {
      setQualityModel(previousModel);
      setQualityModelError(error instanceof Error ? error.message : "质量模型切换失败");
    }
  }

  async function toggleRvcStability(enabled: boolean) {
    if (!activeProjectId) return;
    setRvcError("");
    try {
      const next = await updateRvcSettings(activeProjectId, routeMode === "fast" ? { fast_route_enabled: enabled } : { quality_stability_enabled: enabled });
      queryClient.setQueryData<RvcWorkspace>(["rvc-workspace", activeProjectId], next);
    } catch (error) {
      setRvcError(error instanceof Error ? error.message : "RVC 路线切换失败");
    }
  }

  function openRvcWorkbench() {
    const bounds = rvcButtonRef.current?.getBoundingClientRect();
    setRvcOrigin(bounds
      ? { x: bounds.left + bounds.width / 2, y: bounds.top + bounds.height / 2 }
      : { x: window.innerWidth / 2, y: window.innerHeight / 2 });
    setRvcOpen(true);
  }

  const cpuPercent = resources?.cpu.percent ?? 0;
  const memoryPercent = resources?.memory.percent ?? 0;
  const gpuPercent = resources?.gpu.percent ?? 0;
  const selectedQualityOption = productionSettings?.quality_models.find((item) => item.model_id === qualityModel);
  const rvcEnabled = routeMode === "fast"
    ? rvcWorkspace?.settings.fast_route_enabled ?? false
    : rvcWorkspace?.settings.quality_stability_enabled ?? false;
  const hasRvcBinding = rvcWorkspace?.characters.some((item) => (routeMode === "fast" ? item.fast_route_enabled : item.stability_enabled) && item.selected_model_id) ?? false;
  const workflow = workspace?.workflow.map((stage) => stage.stage_id === "quality_render" ? { ...stage, label: routeMode === "fast" ? "极速渲染" : "质量渲染" } : stage) ?? [];
  const rvcReviewTasks = continuousRun?.rvc_tasks.filter((task) => task.status === "awaiting_review") ?? [];
  const rvcReviewKey = continuousRun && rvcReviewTasks.length
    ? continuousRun.run_id + ":" + rvcReviewTasks.map((task) => task.character_id).sort().join(",")
    : "";

  if (!workspace || !selectedTemplate) {
    return <main className="app-bootstrap"><Activity size={18} /><strong>{isError ? "无法连接后端服务" : "正在读取工作区"}</strong><span>{isError ? "请通过启动器重新启动后再试" : "正在加载项目与推理模板"}</span></main>;
  }

  return (
    <main className={"app-shell" + (continuousRun?.slices.length ? " app-shell--with-slices" : "")}>
      <section className={`resource-strip ${showResources ? "" : "resource-strip--collapsed"}`} aria-label="系统资源监控">
        {showResources && (
          <div className="resource-metrics">
            <div className="resource-metric"><Cpu size={12} /><span>CPU</span><b>{Math.round(cpuPercent)}%</b><i><span style={{ width: `${cpuPercent}%` }} /></i></div>
            <div className="resource-metric"><MemoryStick size={12} /><span>内存</span><b>{resources ? `${resources.memory.used_gb}/${resources.memory.total_gb} GB` : "--"}</b><i><span style={{ width: `${memoryPercent}%` }} /></i></div>
            <div className="resource-metric"><HardDrive size={12} /><span>GPU</span><b>{resources?.gpu.available ? `${Math.round(gpuPercent)}%` : "不可用"}</b><i><span style={{ width: `${gpuPercent}%` }} /></i></div>
            {resources?.gpu.available && <small title={resources.gpu.name ?? undefined}>显存 {Math.round(resources.gpu.memory_percent ?? 0)}%</small>}
          </div>
        )}
        <button className="resource-toggle" title={showResources ? "收起资源监控" : "显示资源监控"} aria-pressed={showResources} onClick={() => setShowResources((current) => !current)}>{showResources ? <EyeOff size={13} /> : <Eye size={13} />}<span>资源监控</span></button>
      </section>
      <header className="topbar">
        <div className="brand"><div className="brand-mark"><Volume2 size={18} /></div><strong>Zw Voice Factory</strong><span>2.0</span></div>
        <label className="project-switch"><Library size={15} /><select aria-label="当前项目" value={activeProjectId ?? ""} disabled={!sources.length} onChange={(event) => selectActiveProject(event.target.value || null)}>{sources.length ? sources.map((source) => <option key={source.project_id} value={source.project_id}>{source.display_name}</option>) : <option value="">{workspace.project.name}</option>}</select><ChevronDown size={14} /></label>
        <div className="route-switch" aria-label="朗读路线">
          <button className={routeMode === "fast" ? "active" : ""} onClick={() => setRouteMode("fast")}>极速</button>
          <button className={routeMode === "quality" ? "active" : ""} onClick={() => setRouteMode("quality")}>质量</button>
        </div>
        <div className="top-actions"><span className={`health ${isError ? "health--error" : ""}`}><Activity size={14} />{isError ? "后端离线" : isFetching ? "同步中" : "后端在线"}</span><button className="icon-button runtime-log-trigger" title="查看运行日志" onClick={() => setShowRuntimeLogs(true)}><FileText size={16} /></button><button className="icon-button" title={theme === "dark" ? "切换到明亮主题" : "切换到暗色主题"} onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}>{theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}</button></div>
      </header>

      <WorkflowNav stages={workflow} activeStage={activeStage} onStageChange={setActiveStage} />

      <section className="route-context">
        <button className="template-context" onClick={() => setActiveStage("template")}><Settings2 size={14} /><span>模板</span><strong>{selectedTemplate.display_name}</strong></button>
        {routeMode === "quality" ? <div className="route-pipeline" aria-label="质量路线模型链路">
          <span><small>参考生产</small><strong>VoxCPM2</strong></span><ArrowRight size={14} />
          <span className="active quality-model-step"><small>在线渲染</small><select aria-label="质量渲染模型" value={qualityModel} onChange={(event) => { void selectQualityModel(event.target.value as QualityModelId); }}>{productionSettings?.quality_models.map((option) => <option key={option.model_id} value={option.model_id} disabled={!option.available}>{option.label}{option.available ? "" : " · 未安装"}</option>) ?? <option value="gpt_sovits_v2">GPT-SoVITS V2</option>}</select><b title={qualityModelError || selectedQualityOption?.effect}>{qualityModelError || selectedQualityOption?.effect || "稳定均衡"}</b></span><ArrowRight size={14} />
          <span className={rvcEnabled ? "active" : "optional"}><small>可选稳定层</small><strong>RVC</strong></span>
        </div> : <div className="route-pipeline fast-route-pipeline" aria-label="极速路线模型链路">
          <span className="active"><small>轻量发音</small><strong>Sherpa ONNX</strong><b>本地 CPU</b></span><ArrowRight size={14} />
          <span className={rvcEnabled ? "active" : "optional"}><small>角色身份</small><strong>RVC</strong><b>{rvcEnabled ? "按角色接入" : "可选"}</b></span><ArrowRight size={14} />
          <span><small>播放策略</small><strong>句级预取</strong><b>N+1 / N+2</b></span>
        </div>}
        <div className="stability-actions">
          <label className="stability-control" title={rvcError}>
            <input type="checkbox" checked={rvcEnabled} disabled={!rvcWorkspace || (!rvcEnabled && !hasRvcBinding)} onChange={(event) => { void toggleRvcStability(event.target.checked); }} />
            <span className="toggle-track"><span /></span>
            <span>{routeMode === "fast" ? "RVC 角色层" : "RVC 稳定层"}</span>
            <b>{rvcError || (rvcEnabled ? "启用" : hasRvcBinding ? "待测评" : "未绑定")}</b>
          </label>
          <button ref={rvcButtonRef} className="rvc-open-button" title="打开 RVC 速度路线工作台" disabled={!activeProjectId} onClick={openRvcWorkbench}><Expand size={14} /></button>
        </div>
      </section>

      {continuousRun?.slices.length ? (
        <ContinuousSliceNavigator run={continuousRun} selectedSliceId={selectedSliceId} onSelect={setSelectedSliceId} />
      ) : null}

      {activeStage === "quality_render" ? (
        routeMode === "fast"
          ? <FastWorkbench sources={sources} selectedProjectId={activeProjectId} selectedSliceId={selectedSliceId} onProjectChange={selectActiveProject} onStageChange={setActiveStage} />
          : <QualityWorkbench qualityModel={qualityModel} qualityModelLabel={selectedQualityOption?.label ?? "GPT-SoVITS V2"} sources={sources} selectedProjectId={activeProjectId} selectedSliceId={selectedSliceId} onSelectedSliceChange={setSelectedSliceId} onProjectChange={selectActiveProject} onStageChange={setActiveStage} />
      ) : (
        <PreparationWorkspace
          activeStage={activeStage}
          routeMode={routeMode}
          sources={sources}
          selectedProjectId={activeProjectId}
          selectedTemplate={selectedTemplate}
          workspace={workspace}
          onProjectChange={selectActiveProject}
          onSourcesChange={updateSources}
          onTemplateChange={setSelectedTemplateId}
          onStageChange={setActiveStage}
          selectedSliceId={selectedSliceId}
        />
      )}
      {activeProjectId && <RvcWorkbench open={rvcOpen} origin={rvcOrigin} projectId={activeProjectId} onClose={() => setRvcOpen(false)} />}
      {rvcReviewKey && rvcReviewKey !== dismissedRvcReviewKey && (
        <aside className="rvc-review-notice" role="status">
          <BellRing size={18} />
          <span><strong>RVC 稳定层已完成训练与基准</strong><small>{rvcReviewTasks.map((task) => task.display_name).join("、")} 等待选择是否启用</small></span>
          <button className="secondary-button" onClick={openRvcWorkbench}>前往审核</button>
          <button className="icon-button" title="稍后处理" onClick={() => setDismissedRvcReviewKey(rvcReviewKey)}><X size={14} /></button>
        </aside>
      )}
      <RuntimeLogPanel open={showRuntimeLogs} onClose={() => setShowRuntimeLogs(false)} />
    </main>
  );
}

export default App;
