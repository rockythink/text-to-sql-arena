import type { CaseRun, ModelRun, RunSnapshot } from "../types";

export type ReportModel = ModelRun;
export type ReportSnapshot = RunSnapshot;

export const terminalRunStatuses: Record<string, true> = { completed: true, completed_with_errors: true, failed: true, interrupted: true, cancelled: true };
const settledCaseStatuses: Record<string, true> = { completed: true, failed: true, cancelled: true };
const reasonLabels: Record<string, string> = {
  "quality evidence unavailable": "缺少质量证据，无法判断",
  "quality evidence incomplete": "分项证据不完整，无法判断",
  "result matches the reference contract": "结果满足列、行与排序合同",
  "query did not execute successfully": "查询未成功执行",
  "result differs from the reference contract": "结果与金标合同不一致",
};

export type CaseRound = {
  key: string;
  title: string;
  question: string | null;
  category: string;
  weight: number;
  models: Array<{
    model: ReportModel;
    score: number | null;
    contribution: number | null;
    attempts: number;
    resultCorrect: number | null;
    executionOk: number | null;
    protocolOk: number | null;
    reason: string | null;
  }>;
  spread: number | null;
};

export function anonymousName(index: number) {
  return `参赛者 ${index < 26 ? String.fromCharCode(65 + index) : index + 1}`;
}

export function buildCaseRounds(report: ReportSnapshot): CaseRound[] {
  const keys = report.selected_case_keys;
  const byModel = report.models.map((model) => {
    const grouped = new Map<string, CaseRun[]>();
    for (const attempt of model.cases) {
      const group = grouped.get(attempt.stable_key);
      if (group) group.push(attempt);
      else grouped.set(attempt.stable_key, [attempt]);
    }
    return grouped;
  });
  const metadata = keys.map((key) => byModel.map((group) => group.get(key)?.[0]).find(Boolean));
  const totalWeight = metadata.reduce((sum, item) => sum + (item?.weight ?? 1), 0);
  return keys.map((key, index) => {
    const first = metadata[index];
    const weight = first?.weight ?? 1;
    const modelRows = report.models.map((model, modelIndex) => {
      const attempts = byModel[modelIndex].get(key) ?? [];
      const complete = attempts.length === report.attempts && new Set(attempts.map((item) => item.attempt)).size === report.attempts && attempts.every((item) => settledCaseStatuses[item.status]);
      // A failed attempt with no score contributes zero; pending or absent evidence is not a finished round.
      const averageScore = complete ? attempts.reduce((sum, item) => sum + (item.score?.total ?? 0), 0) / report.attempts : null;
      const qualityRates = (["result_correct", "execution_ok", "protocol_ok"] as const).map((field) => complete && attempts.every((item) => typeof item.quality?.[field] === "boolean") ? attempts.filter((item) => item.quality?.[field] === true).length / report.attempts : null);
      const reasons = [...new Set(attempts.map((item) => item.quality?.reason).filter((item): item is string => Boolean(item)))];
      return {
        model, score: averageScore,
        contribution: averageScore == null || totalWeight === 0 ? null : averageScore * weight / totalWeight,
        attempts: attempts.length,
        resultCorrect: qualityRates[0], executionOk: qualityRates[1], protocolOk: qualityRates[2],
        reason: reasons.length ? reasons.map((reason) => reasonLabels[reason] ?? reason).join("；") : null,
      };
    });
    const contributions = modelRows.flatMap((row) => row.contribution == null ? [] : [row.contribution]);
    return {
      key, title: first?.title ?? key, question: first?.question ?? null,
      category: first?.radar_dimension ?? first?.category ?? "未分类", weight, models: modelRows,
      spread: contributions.length === report.models.length && contributions.length > 1 ? Math.max(...contributions) - Math.min(...contributions) : null,
    };
  });
}

export function keyRounds(rounds: CaseRound[], limit = 3) {
  return rounds.filter((round) => round.spread != null && round.spread > 0).sort((a, b) => (b.spread ?? 0) - (a.spread ?? 0)).slice(0, limit);
}

export type ControlCheck = { label: string; left: string; right: string; same: boolean };
const stableJson = (value: unknown) => JSON.stringify(value, (_key, nested) => {
  if (!nested || typeof nested !== "object" || Array.isArray(nested)) return nested;
  return Object.fromEntries(Object.entries(nested as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b)));
});

const disclosedParameterKeys = new Set(["provider", "auth_mode", "timeout_seconds", "temperature", "max_tokens", "reasoning_effort"]);
const disclosedIsolationKeys = new Set(["harness", "harness_version", "policy_version", "tools_enabled", "tool_count", "generation_attempts", "generation_attempt_limit", "context_isolated", "system_prompt_sha256", "model_identity_source", "effective_parameters"]);
const disclosed = (source: Record<string, unknown>, keys: Set<string>) => Object.fromEntries(Object.entries(source).filter(([key]) => keys.has(key)));
const disclosedIsolation = (source: Record<string, unknown>) => Object.fromEntries(Object.entries(disclosed(source, disclosedIsolationKeys)).map(([key, value]) => [key, key === "effective_parameters" && value && typeof value === "object" && !Array.isArray(value) ? disclosed(value as Record<string, unknown>, disclosedParameterKeys) : value]));

const actualControlKeys = new Set(["harness", "harness_version", "bridge_sha256", "dependency_lock_sha256", "policy_version", "system_prompt_sha256", "provider", "auth_mode", "api", "model_identity_source", "effective_parameters", "generation_attempts", "tools_enabled", "tool_calls_observed"]);
function safeControl(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(safeControl);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).filter(([key]) => !/secret|credential|api.?key|authorization|access_token|refresh_token/i.test(key)).map(([key, nested]) => [key, safeControl(nested)]));
  return value;
}
export function disclosedActualControls(source: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(source).filter(([key]) => actualControlKeys.has(key)).map(([key, value]) => [key, safeControl(value)]));
}
export function actualControlVariants(model: ReportModel): string[] {
  return [...new Set(model.cases.flatMap((item) => item.invocation ? [stableJson(disclosedActualControls(item.invocation))] : []))].sort();
}

export function comparisonControls(left: ReportSnapshot, right: ReportSnapshot, leftModel: ReportModel, rightModel: ReportModel): ControlCheck[] {
  const parameters = [leftModel, rightModel].map((model) => disclosed(model.parameters, disclosedParameterKeys));
  const controls = [leftModel, rightModel].map((model) => disclosedIsolation(model.isolation));
  const checks: Array<[string, unknown, unknown]> = [
    ["题库内容哈希", left.suite_content_hash, right.suite_content_hash],
    ["案例集合", [...left.selected_case_keys].sort(), [...right.selected_case_keys].sort()],
    ["尝试次数", left.attempts, right.attempts],
    ["评分器", left.protocol.scorer_version, right.protocol.scorer_version],
    ["结果判定合同", left.quality_schema_version, right.quality_schema_version],
    ["输出合同", left.protocol.output_contract, right.protocol.output_contract],
    ["应用版本", left.protocol.app_version, right.protocol.app_version],
    ["执行引擎", left.protocol.duckdb_version, right.protocol.duckdb_version],
    ["SQL 解析器", left.protocol.sqlglot_version, right.protocol.sqlglot_version],
    ["CLI 版本", leftModel.cli_version, rightModel.cli_version],
    ["接入适配器", leftModel.adapter_kind, rightModel.adapter_kind],
    ["接入地址指纹", leftModel.endpoint_fingerprint, rightModel.endpoint_fingerprint],
    ["响应模式", leftModel.response_mode, rightModel.response_mode],
    ["适配器参数", parameters[0], parameters[1]],
    ["隔离控制", controls[0], controls[1]],
  ];
  const actualChecks: ControlCheck[] = leftModel.adapter_kind === "pi" || rightModel.adapter_kind === "pi" ? [
    { label: "实际请求证据完整", left: `${leftModel.cases.filter(c => c.invocation?.status === "completed").length}/${leftModel.cases.length}`, right: `${rightModel.cases.filter(c => c.invocation?.status === "completed").length}/${rightModel.cases.length}`, same: [leftModel, rightModel].every(m => m.cases.length > 0 && m.cases.every(c => c.invocation?.status === "completed")) },
    { label: "实际调用控制项", left: stableJson(actualControlVariants(leftModel)), right: stableJson(actualControlVariants(rightModel)), same: actualControlVariants(leftModel).length > 0 && stableJson(actualControlVariants(leftModel)) === stableJson(actualControlVariants(rightModel)) },
  ] : [];
  return [{ label: "两侧运行均已结束", left: left.status, right: right.status, same: Boolean(terminalRunStatuses[left.status] && terminalRunStatuses[right.status]) }, ...actualChecks, ...checks.map(([label, a, b]) => ({ label, left: typeof a === "string" ? a : stableJson(a) ?? "未记录", right: typeof b === "string" ? b : stableJson(b) ?? "未记录", same: a !== undefined && b !== undefined && stableJson(a) === stableJson(b) }))];
}

export type CaseChange = { key: string; title: string; left: number | null; right: number | null; state: "进步" | "退步" | "持平" | "无法判断" };

export function compareResultCorrect(left: ReportSnapshot, right: ReportSnapshot, leftModel: ReportModel, rightModel: ReportModel): CaseChange[] {
  const comparable = comparisonControls(left, right, leftModel, rightModel).every((control) => control.same);
  const leftRounds = new Map(buildCaseRounds({ ...left, models: [leftModel] }).map((round) => [round.key, round]));
  const rightRounds = new Map(buildCaseRounds({ ...right, models: [rightModel] }).map((round) => [round.key, round]));
  const keys = [...new Set([...leftRounds.keys(), ...rightRounds.keys()])].sort();
  return keys.map((key) => {
    const l = leftRounds.get(key)?.models[0]?.resultCorrect ?? null;
    const r = rightRounds.get(key)?.models[0]?.resultCorrect ?? null;
    const state = !comparable || l == null || r == null ? "无法判断" : r > l ? "进步" : r < l ? "退步" : "持平";
    return { key, title: rightRounds.get(key)?.title ?? leftRounds.get(key)?.title ?? key, left: l, right: r, state };
  });
}
