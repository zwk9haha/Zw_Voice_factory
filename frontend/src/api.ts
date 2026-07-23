import type { AudioJob, AudioJobRequest, PreparationAction, PreparationPreview, SourceSummary, WorkspacePayload } from "./types";

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

async function responseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new ApiError(response.status, payload?.detail ?? `请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchWorkspace(): Promise<WorkspacePayload> {
  const response = await fetch("/api/workspace");
  return responseJson<WorkspacePayload>(response);
}

export async function fetchSources(): Promise<SourceSummary[]> {
  return responseJson<SourceSummary[]>(await fetch("/api/sources"));
}

export async function importTxtSource(file: File): Promise<SourceSummary> {
  const body = new FormData();
  body.append("file", file);
  return responseJson<SourceSummary>(await fetch("/api/sources", { method: "POST", body }));
}

export async function fetchPreparationPreview(projectId: string): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(await fetch(`/api/projects/${encodeURIComponent(projectId)}/preparation/preview`));
}

export async function runPreparationAction(projectId: string, action: PreparationAction): Promise<PreparationPreview> {
  return responseJson<PreparationPreview>(
    await fetch(`/api/projects/${encodeURIComponent(projectId)}/preparation`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    }),
  );
}

export async function createAudioJob(request: AudioJobRequest): Promise<AudioJob> {
  return responseJson<AudioJob>(
    await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    }),
  );
}

export async function fetchAudioJob(jobId: string): Promise<AudioJob> {
  return responseJson<AudioJob>(await fetch(`/api/jobs/${encodeURIComponent(jobId)}`));
}
