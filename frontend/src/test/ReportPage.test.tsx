import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { ReportPage } from "../pages/ReportPage";
import type { RunSnapshot } from "../types";
import { buildCaseRounds, compareResultCorrect, comparisonControls, type ReportSnapshot } from "../lib/reportAnalysis";

vi.mock("../api/client", () => ({ api: { report: vi.fn(), runs: vi.fn() } }));
vi.mock("../api/workflows", () => ({ getPublicationPreview: vi.fn(), exportPublicationPackage: vi.fn(), rerunRun: vi.fn() }));
vi.mock("../components/SqlWorkspace", () => ({ SqlWorkspace: ({ open }: { open: boolean }) => open ? <div>SQL evidence workspace</div> : null }));

function model(id: number, name: string, caseOneCorrect: boolean, caseTwoCorrect: boolean) {
  return {
    id,
    name,
    status: "completed",
    official_score: caseOneCorrect && caseTwoCorrect ? 92 : 61,
    requested_model_id: `${name}-secret-id`,
    resolved_model_id: `${name}-resolved-secret-id`,
    adapter_kind: "codex_cli",
    response_mode: "json",
    parameters: { temperature: 0 },
    cli_version: "1",
    isolation: { network: false },
    endpoint_fingerprint: null,
    quality: { total: 2, evaluated: 2, result_correct: Number(caseOneCorrect) + Number(caseTwoCorrect), execution_ok: 2, protocol_ok: 2, correct_rate: (Number(caseOneCorrect) + Number(caseTwoCorrect)) / 2, execution_rate: 1, protocol_rate: 1 },
    cases: [
      { id: id * 10 + 1, case_id: 1, stable_key: "orders", title: "订单汇总", question: "统计每个地区订单", weight: 1, category: "聚合", radar_dimension: "aggregation", attempt: 1, status: "completed", visible_summary: null, formatted_sql: "select secret from orders", generation_ms: 12, execution_ms: 2, provider_request_id: null, token_usage: null, score: { total: caseOneCorrect ? 90 : 30, rows: caseOneCorrect ? 20 : 0 }, quality: { result_correct: caseOneCorrect, execution_ok: true, protocol_ok: true, reason: caseOneCorrect ? "digest_match" : "row_mismatch" }, error_code: null, error_message: null },
      { id: id * 10 + 2, case_id: 2, stable_key: "customers", title: "客户筛选", question: "筛选活跃客户", weight: 2, category: "筛选", radar_dimension: "filter", attempt: 1, status: "completed", visible_summary: null, formatted_sql: "select private from customers", generation_ms: 14, execution_ms: 2, provider_request_id: null, token_usage: null, score: { total: caseTwoCorrect ? 94 : 40 }, quality: { result_correct: caseTwoCorrect, execution_ok: true, protocol_ok: true, reason: caseTwoCorrect ? "digest_match" : "row_mismatch" }, error_code: null, error_message: null },
    ],
  };
}

function report(id = 7): ReportSnapshot {
  return {
    id,
    report_schema_version: "run-report-v3",
    quality_schema_version: "result-quality-v1",
    source_run_id: null,
    suite_version_id: 3,
    suite_content_hash: "suite-abc",
    selected_case_keys: ["orders", "customers"],
    status: "completed",
    attempts: 1,
    created_at: "2026-09-17T00:00:00Z",
    started_at: "2026-09-17T00:00:01Z",
    finished_at: "2026-09-17T00:00:10Z",
    protocol: { output_contract: "json-v1", app_version: "3", scorer_version: "3", duckdb_version: "1", sqlglot_version: "2", case_count: 2, attempts: 1 },
    fairness: { comparison_mode: "pure_model", pure_model_comparison: true, controlled_fields: [], differences: [], model_variable: [], exact_rerun_default: true },
    models: [model(1, "Alpha Real", true, false), model(2, "Beta Real", false, true)],
  } as RunSnapshot as ReportSnapshot;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/runs/7/report"]}><Routes><Route path="/runs/:id/report" element={<ReportPage/>}/></Routes></MemoryRouter></QueryClientProvider>);
}

beforeEach(() => {
  vi.mocked(api.report).mockResolvedValue(report());
  vi.mocked(api.runs).mockResolvedValue({ runs: [] });
});

describe("report analysis", () => {
  it("uses every planned attempt and normalizes contributions onto the total score", () => {
    const data = report();
    data.attempts = 2;
    for (const entry of data.models) entry.cases.push(...entry.cases.map((item) => ({ ...item, id: item.id + 100, attempt: 2 })));
    data.models[0].cases[2] = { ...data.models[0].cases[2], score: { total: 50 }, quality: { result_correct: false, execution_ok: true, protocol_ok: true, reason: "row_mismatch" } };
    const order = buildCaseRounds(data).find((round) => round.key === "orders");
    expect(order?.models[0].score).toBe(70);
    expect(order?.models[0].resultCorrect).toBe(0.5);
    expect(order?.spread).toBeCloseTo(40 / 3);
    data.models[0].cases[2] = { ...data.models[0].cases[2], status: "failed", score: null, quality: undefined };
    const failed = buildCaseRounds(data).find((round) => round.key === "orders");
    expect(failed?.models[0].score).toBe(45);
    expect(failed?.models[0].resultCorrect).toBeNull();
  });

  it("refuses ranked change labels when a control differs", () => {
    const left = report(7);
    const right = report(8);
    right.protocol.output_contract = "text-v2";
    const controls = comparisonControls(left, right, left.models[0], right.models[0]);
    expect(controls.find((control) => control.label === "输出合同")?.same).toBe(false);
    expect(compareResultCorrect(left, right, left.models[0], right.models[0]).map((change) => change.state)).toEqual(["无法判断", "无法判断"]);
  });
  it("requires completed actual calls and refuses matching configurations with different wire budgets", () => {
    const left = report(7);
    const right = report(8);
    left.models[0].adapter_kind = right.models[0].adapter_kind = "pi";
    const states = () => compareResultCorrect(left, right, left.models[0], right.models[0]).map(change => change.state);
    expect(states()).toEqual(["无法判断", "无法判断"]);
    for (const data of [left, right]) for (const item of data.models[0].cases) item.invocation = { status: "completed", harness: "pi-ai", effective_parameters: { wire_generation: { max_tokens: 8192 } } };
    expect(states()).toEqual(["持平", "持平"]);
    right.models[0].cases[0].invocation = { status: "completed", harness: "pi-ai", effective_parameters: { wire_generation: { max_tokens: 16384 } } };
    expect(states()).toEqual(["无法判断", "无法判断"]);
  });
});

describe("ReportPage", () => {
  it("shows error-completed runs as ended and hides scores until prediction reveal", async () => {
    vi.mocked(api.report).mockResolvedValue({ ...report(), status: "completed_with_errors" });
    renderPage();
    expect((await screen.findAllByText("Alpha Real"))[0]).toBeVisible();
    expect(screen.queryByText("比赛仍在进行")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复测" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /进入匿名竞猜/ }));
    expect(document.body.textContent).not.toContain("61.00");
    expect(screen.getByRole("button", { name: /看门道/ })).toBeDisabled();
    expect(screen.queryByText("Alpha Real")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("secret-id");
    expect(document.body.textContent).not.toContain("select secret");
    fireEvent.click(screen.getAllByRole("button", { name: /预测它胜出/ })[0]);
    expect(screen.getByText("预测已在本标签页暂存。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "揭晓身份" }));
    expect(screen.getAllByText("Alpha Real")[0]).toBeVisible();
  });

  it("shows quality boundaries and opens real evidence only after navigating to inspection", async () => {
    renderPage();
    await screen.findByText("官方记分牌");
    fireEvent.click(screen.getByRole("button", { name: /看门道/ }));
    expect(screen.getByText("质量三指标")).toBeInTheDocument();
    expect(screen.getAllByText("结果正确")).toHaveLength(2);
    fireEvent.click(screen.getAllByRole("button", { name: "打开真实证据" })[0]);
    await waitFor(() => expect(screen.getByText("SQL evidence workspace")).toBeInTheDocument());
  });
  it("hides secret controls in configuration and invocation evidence", async () => {
    const controlled = report();
    controlled.fairness = { ...controlled.fairness, comparison_mode: "controlled_harness", pure_model_comparison: false, differences: ["provider"] };
    controlled.models = controlled.models.map(item => ({ ...item, adapter_kind: "pi", response_mode: "text", parameters: { provider: item.id === 1 ? "openai-codex" : "anthropic", auth_mode: "oauth", timeout_seconds: 180, api_key_ref: "must-not-render" }, isolation: { harness: "pi", secret_ref: "also-hidden" }, cases: item.cases.map(c => ({ ...c, invocation: { status: "completed", harness: "pi-ai", effective_parameters: { wire_generation: { max_tokens: 16384, api_key: "nested-wire-secret" } } } })) }));
    vi.mocked(api.report).mockResolvedValue(controlled);
    const view = renderPage();
    const page = within(view.container);
    await page.findByText("官方记分牌");
    fireEvent.click(page.getByRole("button", { name: /查证据/ }));
    expect(document.body.textContent).not.toContain("must-not-render");
    expect(document.body.textContent).not.toContain("also-hidden");
    expect(document.body.textContent).not.toContain("nested-wire-secret");
  });
});
