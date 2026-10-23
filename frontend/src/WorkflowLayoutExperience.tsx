import { useState, type ComponentProps, type CSSProperties } from "react";
import { ArrowDown, ArrowUp, GripVertical, LayoutTemplate, Save, Undo2 } from "lucide-react";
import { WorkflowProjectExperience, type ExperiencePanel, type ExperiencePanelId } from "./WorkflowProjectExperience";
import { defaultProjectLayout, moveProjectPanel, normalizeProjectLayout, projectLayoutStorageKey, type ProjectUiLayout } from "./workflowProjectLayout";
import "./workflowLayoutExperience.css";

type Props = Omit<ComponentProps<typeof WorkflowProjectExperience>, "variant" | "renderPanels">;
export function WorkflowLayoutExperience(props: Props) {
  return <WorkflowProjectExperience {...props} variant="layout" renderPanels={(panels, projectId, workflowId) => <ProjectLayoutEditor key={`${projectId}:${workflowId}`} panels={panels} projectId={projectId} workflowId={workflowId} />} />;
}
function ProjectLayoutEditor({ panels, projectId, workflowId }: { panels: ExperiencePanel[]; projectId: string; workflowId: string }) {
  const storageKey = projectLayoutStorageKey(projectId, workflowId);
  const [layout, setLayout] = useState<ProjectUiLayout>(() => {
    try { const saved = localStorage.getItem(storageKey); return saved ? normalizeProjectLayout(JSON.parse(saved)) : defaultProjectLayout(); }
    catch { return defaultProjectLayout(); }
  });
  const [editing, setEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState("");
  const [dragged, setDragged] = useState<ExperiencePanelId | null>(null);
  const [dropTarget, setDropTarget] = useState<ExperiencePanelId | null>(null);
  function change(next: ProjectUiLayout) { setLayout(next); setDirty(true); setMessage(""); }
  function save() {
    try { localStorage.setItem(storageKey, JSON.stringify(layout)); setDirty(false); setMessage("布局已保存在当前浏览器，再次打开此项目会恢复。"); }
    catch { setMessage("浏览器无法保存布局，当前修改仍可使用。"); }
  }
  function patch(id: ExperiencePanelId, fields: { wide?: boolean; visible?: boolean }) { change({ ...layout, panels: layout.panels.map((panel) => panel.id === id ? { ...panel, ...fields } : panel) }); }
  const styles = { "--experience-columns": layout.columns } as CSSProperties;
  return <div className="project-layout-editor">
    <div className="project-layout-toolbar"><div><strong><LayoutTemplate size={15} />页面布局</strong><small>{dirty ? "有未保存的布局修改" : "按项目与发布工作流分别保存"}</small></div><div className="experience-actions"><button className="secondary-button" aria-pressed={editing} onClick={() => setEditing(!editing)}>{editing ? "结束布局编辑" : "编辑布局"}</button>{editing && <><label>分栏<select aria-label="页面分栏" value={layout.columns} onChange={(event) => change({ ...layout, columns: Number(event.target.value) as 1 | 2 | 3 })}><option value="1">单栏</option><option value="2">双栏</option><option value="3">三栏</option></select></label><button className="secondary-button" onClick={() => change(defaultProjectLayout())}><Undo2 size={13} />恢复默认</button></>}<button className="primary-button" disabled={!dirty || !projectId} onClick={save}><Save size={13} />保存布局</button></div></div>
    {message && <p className="experience-feedback" role="status">{message}</p>}
    {editing && <aside className="project-layout-palette" aria-label="页面组件"><strong>页面组件</strong><p>拖动卡片手柄调整顺序，也可以用上移、下移按钮。导入与审核始终可访问。</p><div>{layout.panels.map((panel) => <label key={panel.id}><input type="checkbox" checked={panel.visible} disabled={panel.id === "inputs" || panel.id === "review"} onChange={(event) => patch(panel.id, { visible: event.target.checked })} />{panels.find((item) => item.id === panel.id)?.title}</label>)}</div></aside>}
    <div className={`project-layout-grid ${editing ? "project-layout-grid--editing" : ""}`} style={styles}>
      {layout.panels.filter((panel) => panel.visible).map((item) => {
        const panel = panels.find((candidate) => candidate.id === item.id)!;
        const index = layout.panels.findIndex((candidate) => candidate.id === item.id);
        return <section key={item.id} className={`experience-card project-layout-card ${item.wide ? "project-layout-card--wide" : ""} ${dropTarget === item.id ? "project-layout-card--drop" : ""}`} data-panel={item.id}
          onDragOver={(event) => { if (!editing || !dragged || dragged === item.id) return; event.preventDefault(); event.dataTransfer.dropEffect = "move"; setDropTarget(item.id); }}
          onDragLeave={(event) => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDropTarget(null); }}
          onDrop={(event) => { event.preventDefault(); if (dragged && editing) change(moveProjectPanel(layout, dragged, item.id)); setDragged(null); setDropTarget(null); }}>
          {editing && <div className="project-layout-card-controls"><button className="icon-button project-layout-grip" aria-label={`拖动 ${panel.title}`} draggable onDragStart={(event) => { setDragged(item.id); event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", item.id); }} onDragEnd={() => { setDragged(null); setDropTarget(null); }}><GripVertical size={16} /></button><span>组件布局</span><button className="icon-button" aria-label={`上移 ${panel.title}`} disabled={index === 0} onClick={() => change(moveProjectPanel(layout, item.id, layout.panels[index - 1].id))}><ArrowUp size={13} /></button><button className="icon-button" aria-label={`下移 ${panel.title}`} disabled={index === layout.panels.length - 1} onClick={() => change(moveProjectPanel(layout, item.id, layout.panels[index + 1].id))}><ArrowDown size={13} /></button><label><input type="checkbox" checked={item.wide} onChange={(event) => patch(item.id, { wide: event.target.checked })} />占满一行</label></div>}
          <header><h2>{panel.title}</h2><small>{panel.hint}</small></header>{panel.content}
        </section>;
      })}
    </div>
  </div>;
}
