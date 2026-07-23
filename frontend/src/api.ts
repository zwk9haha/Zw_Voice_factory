import type { WorkspacePayload } from "./types";

export async function fetchWorkspace(): Promise<WorkspacePayload> {
  const response = await fetch("/api/workspace");
  if (!response.ok) {
    throw new Error(`Workspace request failed: ${response.status}`);
  }
  return response.json() as Promise<WorkspacePayload>;
}
