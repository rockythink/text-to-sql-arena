import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

export const repoRoot = resolve(process.cwd(), "..");
export const evidenceRoot = resolve(repoRoot, "evidence");
export const githubEvidenceRoot = "https://github.com/rockythink/text-to-sql-arena/blob/main/evidence";

export interface EvidenceIndexRun {
  bundle_sha256: string;
  path: string;
  run_id: number;
  status: string;
  suite_content_hash: string;
}

export interface EvidenceIndexSuite {
  bundle_sha256: string;
  content_hash: string;
  path: string;
  suite_version_id: number;
  version: number;
}

export interface EvidenceIndex {
  exported_at: string;
  run_count: number;
  runs: EvidenceIndexRun[];
  schema_version: string;
  scope: string;
  suite_count: number;
  suites: EvidenceIndexSuite[];
}

export interface CaseScore {
  total?: number;
  [key: string]: unknown;
}

export interface CaseReport {
  attempt: number;
  case_id: number;
  category: string;
  error_code: string | null;
  error_message: string | null;
  execution_ms: number | null;
  formatted_sql: string | null;
  generation_ms: number | null;
  id: number;
  provider_request_id: string | null;
  radar_dimension: string;
  score: CaseScore | null;
  stable_key: string;
  status: string;
  title: string;
  token_usage: Record<string, unknown> | null;
  visible_summary: string | null;
}

export interface ModelReport {
  adapter_kind: string;
  attempt_statistics: Record<string, { mean: number; stddev: number; success_rate: number }>;
  cases: CaseReport[];
  categories: Record<string, number>;
  cli_version: string | null;
  failure_count: number;
  id: number;
  name: string;
  official_score: number | null;
  requested_model_id: string;
  resolved_model_id: string | null;
  response_mode: string;
  status: string;
}

export interface RunReport {
  app_version: string;
  attempts: number;
  conclusion: { champions?: string[]; status?: string };
  created_at: string;
  fairness: { comparison_mode: string; pure_model_comparison: boolean; differences: string[] };
  finished_at: string | null;
  id: number;
  models: ModelReport[];
  protocol: Record<string, string | number | null>;
  report_schema_version: string;
  selected_case_keys: string[];
  source_run_id: number | null;
  started_at: string | null;
  status: string;
  suite_content_hash: string;
  suite_version_id: number;
}

export interface SuiteReport extends EvidenceIndexSuite {
  name: string;
  description: string;
  dialect: string;
  published_at: string | null;
  status: string;
  gold_count: number;
  row_count: number;
  duckdb_version: string;
  scorer_version: string;
  sqlglot_version: string;
}

async function readJson<T>(path: string): Promise<T> {
  return JSON.parse(await readFile(path, "utf8")) as T;
}

export async function getEvidenceIndex(): Promise<EvidenceIndex> {
  return readJson<EvidenceIndex>(resolve(evidenceRoot, "index.json"));
}

export async function getRuns(): Promise<Array<{ index: EvidenceIndexRun; report: RunReport }>> {
  const index = await getEvidenceIndex();
  const runs = await Promise.all(index.runs.map(async (entry) => ({
    index: entry,
    report: await readJson<RunReport>(resolve(evidenceRoot, entry.path, "report.json"))
  })));
  return runs.sort((a, b) => b.report.id - a.report.id);
}

export async function getSuites(): Promise<SuiteReport[]> {
  const index = await getEvidenceIndex();
  return Promise.all(index.suites.map(async (entry) => {
    const suite = await readJson<Record<string, unknown>>(resolve(evidenceRoot, entry.path, "suite.json"));
    const artifact = await readJson<{
      duckdb_version: string;
      scorer_version: string;
      sqlglot_version: string;
      gold: Record<string, { row_count: number }>;
    }>(resolve(evidenceRoot, entry.path, "artifact-manifest.json"));
    return {
      ...entry,
      name: String(suite.name),
      description: String(suite.description),
      dialect: String(suite.dialect),
      published_at: suite.published_at ? String(suite.published_at) : null,
      status: String(suite.status),
      gold_count: Object.keys(artifact.gold).length,
      row_count: Object.values(artifact.gold).reduce((sum, item) => sum + item.row_count, 0),
      duckdb_version: artifact.duckdb_version,
      scorer_version: artifact.scorer_version,
      sqlglot_version: artifact.sqlglot_version
    };
  }));
}

export function formatDate(value: string | null): string {
  if (!value) return "未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai"
  }).format(new Date(value));
}

export function statusLabel(status: string): string {
  return ({
    completed: "完成",
    completed_with_errors: "完成但有错误",
    failed: "失败",
    cancelled: "已取消",
    interrupted: "被中断",
    running: "运行中"
  } as Record<string, string>)[status] ?? status;
}

export function scoreText(score: number | null): string {
  return score == null ? "—" : score.toFixed(2);
}

export function runSlug(id: number): string {
  return `run-${String(id).padStart(4, "0")}`;
}
