import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, eventStream } from "../api/client";
import { SqlWorkspace } from "../components/SqlWorkspace";
import { RunLivePage } from "../pages/RunLivePage";
import { RunNewPage } from "../pages/RunNewPage";
import { useArenaStore } from "../store";
import type { CaseRunDetail, ModelProfile, ModelRun, RunEvent, RunSnapshot, Suite } from "../types";

vi.mock("@monaco-editor/react", () => ({ DiffEditor: ({ modified }: { modified: string }) => <pre data-testid="diff-modified">{modified}</pre> }));

const healthy = (id: number, name: string): ModelProfile => ({ id, name, adapter_kind: "codex_cli", model_id: name.toLowerCase(), base_url: null, response_mode: "text", parameters: {}, enabled: true, has_secret: false, secret_backend: "none", health_status: "healthy", health_details: {}, last_checked_at: null, health_expires_at: null });
const suite: Suite = { id: 1, name: "retail", description: "", versions: [{ id: 10, version: 1, status: "published", dialect: "duckdb", content_hash: "abcdef1234567890", published_at: null, schema_sql: "", seed_sql: "", semantic: {}, prompt_template: "", structure: {}, cases: [{ id: 11, stable_key: "case-1", title: "题一", category: "filter", radar_dimension: "基础查询", difficulty: "easy", question: "q", required_ast: [], comparison: {}, weight: 1, sort_order: 1 }] }] };

function Wrapper({ children }: PropsWithChildren) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  return <QueryClientProvider client={client}><MemoryRouter>{children}</MemoryRouter></QueryClientProvider>;
}

describe("新建对局门禁与公平性合同", () => {
  it("未选模型时禁用并随选手显示公平性和成本", async () => {
    vi.spyOn(api, "profiles").mockResolvedValue([healthy(1, "Alpha"), healthy(2, "Beta")]);
    vi.spyOn(api, "suites").mockResolvedValue([suite]);
    vi.spyOn(api, "runs").mockResolvedValue({ runs: [] });
    render(<RunNewPage/>, { wrapper: Wrapper });
    const launch = await screen.findByRole("button", { name: "开始单测" });
    expect(launch).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Alpha/ }));
    expect(screen.getByRole("button", { name: "开始单测" })).toBeEnabled();
    expect(screen.getByText("单模型评测")).toBeInTheDocument();
    expect(screen.getByText("1 次")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Beta/ }));
    expect(screen.getByRole("button", { name: "开始对比" })).toBeEnabled();
    expect(screen.getByText("纯模型对比")).toBeInTheDocument();
  });
});

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  listeners = new Map<string, EventListener>();
  closed = false;
  constructor(public url: string) { FakeEventSource.instances.push(this); }
  addEventListener(type: string, listener: EventListener) { this.listeners.set(type, listener); }
  close() { this.closed = true; }
  emit(type: string, item: RunEvent) { this.listeners.get(type)?.({ data: JSON.stringify(item) } as unknown as Event); }
}

const event = (seq: number): RunEvent => ({ seq, event_type: "score.completed", level: "info", created_at: "2026-08-29T00:00:00Z", model_run_id: 1, case_run_id: 1, message: "scored", payload: {} });

describe("SSE 断线续传", () => {
  beforeEach(() => { vi.useFakeTimers(); FakeEventSource.instances = []; vi.stubGlobal("EventSource", FakeEventSource); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
  it("以最后事件序号重连且不重复消费", () => {
    const received: number[] = [];
    const stop = eventStream(9, 3, (item) => received.push(item.seq), () => undefined);
    FakeEventSource.instances[0].emit("score.completed", event(7));
    FakeEventSource.instances[0].onerror?.();
    vi.advanceTimersByTime(800);
    expect(received).toEqual([7]);
    expect(FakeEventSource.instances[1].url).toContain("after_seq=7");
    stop();
  });
});

it("演示模式仅持久化在当前 tab sessionStorage", () => {
  useArenaStore.getState().setDemoMode(true);
  expect(useArenaStore.getState().demoMode).toBe(true);
  expect(sessionStorage.getItem("arena-demo")).toBe("1");
  useArenaStore.getState().setDemoMode(false);
});

const detail = (reference?: string): CaseRunDetail => ({
  id: 101, run_id: 3, model_run_id: 1, stable_key: "case-1", title: "题一", question: "统计常量", category: "filter", radar_dimension: "基础查询", difficulty: "easy", status: "completed", attempt: 1, prompt: "actual prompt", raw_output: "raw json", plan: { grain: "单行", sources: ["items"], joins: [], filters: [], metrics: ["value"], steps: ["选择常量"], risks: [] }, assumptions: [], visible_summary: null, generated_sql: "SELECT 1", formatted_sql: "SELECT 1", generation_ms: 2, execution_ms: 1, provider_request_id: "fixture-101", token_usage: null, expected_digest: "gold", actual_digest: "actual", result_preview: { columns: [{ name: "value", type: "BIGINT" }], rows: [[1]], row_count: 1 }, score: { total: 100, protocol: 5 }, error_code: null, error_message: null, required_ast: [], comparison: {}, suite_content_hash: "hash", ...(reference ? { reference_sql: reference, expected_result_preview: { columns: [{ name: "value", type: "BIGINT" }], rows: [[999]], row_count: 1, digest: "gold" } } : {})
});
const model = (id: number, name: string, caseId: number): ModelRun => ({ id, name, status: "completed", official_score: 100, requested_model_id: name, resolved_model_id: name, adapter_kind: "codex_cli", response_mode: "text", parameters: {}, cli_version: "fixture", isolation: {}, cases: [{ id: caseId, case_id: 11, stable_key: "case-1", title: "题一", category: "filter", radar_dimension: "基础查询", attempt: 1, status: "completed", visible_summary: null, formatted_sql: "SELECT 1", generation_ms: 2, execution_ms: 1, provider_request_id: "fixture-case", token_usage: null, score: { total: 100 }, error_code: null, error_message: null }] });
const snapshot = (id: number): RunSnapshot => ({ id, source_run_id: null, suite_version_id: 10, suite_content_hash: "hash", selected_case_keys: ["case-1"], status: "running", attempts: 1, created_at: "2026-08-29T00:00:00Z", started_at: null, finished_at: null, protocol: { output_contract: "query-plan-v1", app_version: "0.2.0", scorer_version: "1.0.0", duckdb_version: "1.5.5", sqlglot_version: "30.17.0", case_count: 1, attempts: 1 }, fairness: { comparison_mode: "single_model", pure_model_comparison: false, controlled_fields: ["adapter_kind"], differences: [], model_variable: ["Alpha"], exact_rerun_default: true }, models: [model(1, "Alpha", 101)] });

it("工作区按规划、SQL、固定金标和实际结果映射证据", async () => {
  vi.spyOn(api, "caseRun").mockImplementation(async (_id, reference) => detail(reference ? "SELECT reference" : undefined));
  const { container } = render(<SqlWorkspace open onOpenChange={() => undefined} models={[model(1, "A", 101), model(2, "B", 102)]} selectedCase="case-1"/>);
  await screen.findByText("揭晓 Reference");
  expect(api.caseRun).toHaveBeenCalledWith(101, false);
  expect(screen.getByText("模型显式查询规划")).toBeInTheDocument();
  expect(screen.getByText("选择常量")).toBeInTheDocument();
  expect(screen.getByTestId("diff-modified")).not.toHaveTextContent("SELECT reference");
  expect(screen.queryByText("999")).not.toBeInTheDocument();
  expect(container.ownerDocument.querySelector(".legend.expected")).toBeInTheDocument();
  expect(container.ownerDocument.querySelector(".legend.actual")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "揭晓 Reference" }));
  await waitFor(() => expect(screen.getByTestId("diff-modified")).toHaveTextContent("SELECT reference"));
  expect(await screen.findByText("999")).toBeInTheDocument();
  expect(api.caseRun).toHaveBeenCalledWith(101, true);
});

it("运行中的取消按钮调用后端且刷新状态", async () => {
  vi.spyOn(api, "run").mockResolvedValue(snapshot(3));
  vi.spyOn(api, "history").mockResolvedValue({ events: [], total: 0 });
  const cancel = vi.spyOn(api, "cancelRun").mockResolvedValue({ status: "cancelling" });
  vi.stubGlobal("EventSource", FakeEventSource);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={["/runs/3/live"]}><Routes><Route path="/runs/:id/live" element={<RunLivePage/>}/></Routes></MemoryRouter></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: "取消运行" }));
  await waitFor(() => expect(cancel).toHaveBeenCalledWith(3));
  vi.unstubAllGlobals();
});

it("持久化事件缺少 payload 时实时页仍可渲染", async () => {
  vi.spyOn(api, "run").mockResolvedValue(snapshot(4));
  vi.spyOn(api, "history").mockResolvedValue({ events: [{ ...event(1), payload: undefined } as unknown as RunEvent], total: 1 });
  vi.stubGlobal("EventSource", FakeEventSource);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={["/runs/4/live"]}><Routes><Route path="/runs/:id/live" element={<RunLivePage/>}/></Routes></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("事件直播台")).toBeInTheDocument();
  vi.unstubAllGlobals();
});
