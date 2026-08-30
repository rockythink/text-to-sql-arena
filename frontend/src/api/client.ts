import type { CaseRunDetail, ModelProfile, RunEvent, RunHistoryItem, RunSnapshot, Suite } from "../types";

let csrfToken = "";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: unknown) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ code: "http_error", message: response.statusText, details: {} }));
    throw new ApiError(response.status, error.code, error.message, error.details);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function bootstrap(): Promise<void> {
  const payload = await request<{ csrf_token: string }>("/api/bootstrap");
  csrfToken = payload.csrf_token;
}

export interface EventHistoryQuery {
  afterSeq?: number;
  modelRunIds?: number[];
  caseRunIds?: number[];
  levels?: string[];
  eventTypes?: string[];
  search?: string;
  offset?: number;
  limit?: number;
}

function eventHistoryPath(id: number, query: EventHistoryQuery): string {
  const params = new URLSearchParams({
    after_seq: String(query.afterSeq ?? 0),
    offset: String(query.offset ?? 0),
    limit: String(query.limit ?? 5000),
  });
  for (const value of query.modelRunIds ?? []) params.append("model_run_ids", String(value));
  for (const value of query.caseRunIds ?? []) params.append("case_run_ids", String(value));
  for (const value of query.levels ?? []) params.append("levels", value);
  for (const value of query.eventTypes ?? []) params.append("event_types", value);
  if (query.search) params.set("search", query.search);
  return `/api/runs/${id}/events/history?${params}`;
}

export const api = {
  profiles: () => request<ModelProfile[]>("/api/model-profiles"),
  createProfile: (payload: Record<string, unknown>) => request<ModelProfile>("/api/model-profiles", { method: "POST", body: JSON.stringify(payload) }),
  checkProfile: (id: number) => request<ModelProfile>(`/api/model-profiles/${id}/check`, { method: "POST" }),
  deleteProfile: (id: number) => request<{ status: string }>(`/api/model-profiles/${id}`, { method: "DELETE" }),
  suites: () => request<Suite[]>("/api/suites"),
  promptPreview: (versionId: number, caseId: number) => request<{ case_id: number; stable_key: string; prompt: string; output_schema: Record<string, unknown> }>(`/api/suite-versions/${versionId}/prompt-preview?case_id=${caseId}`),
  cloneSuite: (suiteId: number, versionId: number) => request<{ suite_version_id: number }>(`/api/suites/${suiteId}/clone?source_version_id=${versionId}`, { method: "POST" }),
  patchSuite: (versionId: number, payload: Record<string, unknown>) => request<{ status: string }>(`/api/suite-versions/${versionId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  publishSuite: (versionId: number) => request<{ content_hash: string }>(`/api/suite-versions/${versionId}/publish`, { method: "POST" }),
  createRun: (payload: { suite_version_id: number; model_profile_ids: number[]; case_ids: number[] | null; attempts: number }) => request<{ id: number; status: string }>("/api/runs", { method: "POST", body: JSON.stringify(payload) }),
  cancelRun: (id: number) => request<{ status: string }>(`/api/runs/${id}/cancel`, { method: "POST" }),
  rerun: (id: number) => request<{ id: number }>(`/api/runs/${id}/rerun`, { method: "POST" }),
  runs: () => request<{ runs: RunHistoryItem[] }>("/api/runs"),
  run: (id: number) => request<RunSnapshot>(`/api/runs/${id}`),
  report: (id: number) => request<RunSnapshot>(`/api/runs/${id}/report`),
  caseRun: (id: number, reference = false) => request<CaseRunDetail>(`/api/case-runs/${id}?include_reference=${reference}`),
  history: (id: number, query: EventHistoryQuery = {}) => request<{ events: RunEvent[]; total: number }>(eventHistoryPath(id, query)),
};

const EVENT_TYPES = ["run.created", "run.started", "model.started", "case.started", "prompt.built", "provider.requested", "provider.delta", "provider.completed", "plan.completed", "sql.parsed", "sql.rejected", "sql.executed", "result.compared", "score.completed", "case.failed", "model.completed", "run.completed", "run.cancelled", "run.interrupted"];

export function eventStream(runId: number, afterSeq: number, onEvent: (event: RunEvent) => void, onReconnect: () => void): () => void {
  let stopped = false;
  let source: EventSource | null = null;
  let retry = 800;
  const connect = () => {
    if (stopped) return;
    source = new EventSource(`/api/runs/${runId}/events?after_seq=${afterSeq}`);
    source.onopen = () => { retry = 800; };
    const receive = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as RunEvent;
      afterSeq = Math.max(afterSeq, event.seq);
      onEvent(event);
    };
    for (const eventType of EVENT_TYPES) source.addEventListener(eventType, receive as EventListener);
    source.onerror = () => {
      source?.close();
      if (!stopped) {
        onReconnect();
        window.setTimeout(connect, retry);
        retry = Math.min(retry * 1.8, 8000);
      }
    };
  };
  connect();
  return () => { stopped = true; source?.close(); };
}
