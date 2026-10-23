import type { ExperiencePanelId } from "./WorkflowProjectExperience";

export interface ProjectPanelLayout { id: ExperiencePanelId; wide: boolean; visible: boolean }
export interface ProjectUiLayout { version: 1; columns: 1 | 2 | 3; panels: ProjectPanelLayout[] }
export const projectPanelIds: ExperiencePanelId[] = ["inputs", "progress", "review", "results", "history"];
export function defaultProjectLayout(): ProjectUiLayout {
  return { version: 1, columns: 2, panels: projectPanelIds.map((id) => ({ id, wide: id === "review" || id === "history", visible: true })) };
}
export function normalizeProjectLayout(value: unknown): ProjectUiLayout {
  const defaults = defaultProjectLayout();
  if (!value || typeof value !== "object" || Array.isArray(value)) return defaults;
  const raw = value as Record<string, unknown>;
  if (raw.version !== 1 || !Array.isArray(raw.panels)) return defaults;
  const seen = new Set<string>();
  const panels: ProjectPanelLayout[] = [];
  for (const entry of raw.panels) {
    if (!entry || typeof entry !== "object") continue;
    const panel = entry as Record<string, unknown>;
    if (!projectPanelIds.includes(panel.id as ExperiencePanelId) || seen.has(String(panel.id))) continue;
    seen.add(String(panel.id));
    panels.push({ id: panel.id as ExperiencePanelId, wide: panel.wide === true, visible: panel.visible !== false });
  }
  defaults.panels.forEach((panel) => { if (!seen.has(panel.id)) panels.push(panel); });
  // Review and input remain reachable even when all optional components are hidden.
  panels.forEach((panel) => { if (panel.id === "inputs" || panel.id === "review") panel.visible = true; });
  return { version: 1, columns: raw.columns === 1 || raw.columns === 3 ? raw.columns : 2, panels };
}
export function moveProjectPanel(layout: ProjectUiLayout, from: ExperiencePanelId, to: ExperiencePanelId): ProjectUiLayout {
  const panels = [...layout.panels];
  const fromIndex = panels.findIndex((panel) => panel.id === from);
  const toIndex = panels.findIndex((panel) => panel.id === to);
  if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return layout;
  const [panel] = panels.splice(fromIndex, 1); panels.splice(toIndex, 0, panel);
  return { ...layout, panels };
}
export function projectLayoutStorageKey(projectId: string, workflowId: string): string {
  return `zw-project-layout-v1:${encodeURIComponent(projectId)}:${encodeURIComponent(workflowId)}`;
}
