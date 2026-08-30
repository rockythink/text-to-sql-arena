export type HealthStatus = "unknown" | "checking" | "healthy" | "unavailable" | "incompatible" | "error";

export interface TokenPricing {
  currency: "USD";
  input_usd_per_million: number | null;
  cached_input_usd_per_million: number | null;
  cache_write_input_usd_per_million: number | null;
  output_usd_per_million: number | null;
  source: string;
  effective_at: string;
}

export interface EfficiencyMetrics {
  metric_schema_version: "efficiency-v1";
  attempted_cases: number;
  correct_case_equivalents: number;
  coverage: Record<string, { measured: number; total: number }>;
  tokens: { input: number; cached_input: number; cache_write_input: number; output: number; reasoning_output: number; total: number } | null;
  estimated_cost_usd: number | null;
  generation_ms: { total: number | null; mean: number | null; p50: number | null; p95: number | null };
  execution_ms: { total: number | null; mean: number | null };
  per_correct_case_equivalent: { tokens: number | null; estimated_cost_usd: number | null; generation_ms: number | null };
  pricing: TokenPricing | null;
  cost_basis: "estimated_token_price" | "unavailable";
}

export interface ModelProfile {
  id: number;
  name: string;
  adapter_kind: "openai_compatible" | "codex_cli" | "claude_cli" | "gemini_cli";
  model_id: string;
  base_url: string | null;
  response_mode: "json_schema" | "json_object" | "text";
  parameters: Record<string, unknown>;
  pricing: TokenPricing | null;
  enabled: boolean;
  has_secret: boolean;
  secret_backend: "keyring" | "environment" | "none";
  health_status: HealthStatus;
  health_details: Record<string, unknown>;
  last_checked_at: string | null;
  health_expires_at: string | null;
}

export interface BenchmarkCase {
  id: number;
  stable_key: string;
  title: string;
  category: string;
  radar_dimension: string;
  difficulty: string;
  question: string;
  required_ast: unknown[];
  comparison: Record<string, unknown>;
  weight: number;
  sort_order: number;
  reference_sql?: string;
}

export interface StructureForeignKey {
  columns: string[];
  referenced_table: string;
  referenced_columns: string[];
}

export interface SuiteVersion {
  id: number;
  version: number;
  status: "draft" | "published";
  dialect: "duckdb";
  content_hash: string | null;
  published_at: string | null;
  schema_sql: string;
  seed_sql: string;
  semantic: Record<string, unknown>;
  prompt_template: string;
  structure: {
    tables?: Array<{
      name: string;
      columns?: Array<{ name: string; type?: string; data_type?: string }>;
      foreign_keys?: StructureForeignKey[];
    }>;
    semantic_relationships?: Array<{ from_entity: string; to_entity: string; sql_on: string }>;
  };
  cases: BenchmarkCase[];
}

export interface Suite {
  id: number;
  name: string;
  description: string;
  versions: SuiteVersion[];
}

export interface QueryPlan {
  grain: string;
  sources: string[];
  joins: string[];
  filters: string[];
  metrics: string[];
  steps: string[];
  risks: string[];
}

export interface ResultPreview {
  columns?: Array<{ name: string; type: string }>;
  rows?: unknown[][];
  row_count?: number;
  missing?: number[];
  extra?: number[];
  digest?: string;
}

export interface ScoreBreakdown {
  total?: number;
  protocol?: number;
  guard?: number;
  execution?: number;
  columns?: number;
  rows?: number;
  ordering?: number;
  ast?: number;
  [key: string]: unknown;
}

export interface CaseRun {
  id: number;
  case_id: number;
  stable_key: string;
  title: string;
  category: string;
  radar_dimension: string;
  attempt: number;
  status: string;
  visible_summary: string | null;
  formatted_sql: string | null;
  generation_ms: number | null;
  execution_ms: number | null;
  provider_request_id: string | null;
  token_usage: Record<string, number> | null;
  efficiency?: { tokens: EfficiencyMetrics["tokens"]; estimated_cost_usd: number | null; generation_ms: number | null; execution_ms: number | null };
  score: ScoreBreakdown | null;
  error_code: string | null;
  error_message: string | null;
}

export interface ModelRun {
  id: number;
  name: string;
  status: string;
  official_score: number | null;
  requested_model_id: string;
  resolved_model_id: string | null;
  adapter_kind: string;
  response_mode: string;
  parameters: Record<string, unknown>;
  cli_version: string | null;
  isolation: Record<string, unknown>;
  cases: CaseRun[];
  efficiency?: EfficiencyMetrics;
  categories?: Record<string, number>;
  failure_count?: number;
}

export interface FairnessContract {
  comparison_mode: "single_model" | "pure_model" | "access_path";
  pure_model_comparison: boolean;
  controlled_fields: string[];
  differences: string[];
  model_variable: string[];
  exact_rerun_default: boolean;
}

export interface RunSnapshot {
  id: number;
  source_run_id: number | null;
  suite_version_id: number;
  suite_content_hash: string;
  selected_case_keys: string[];
  status: string;
  attempts: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  protocol: { output_contract: string; app_version: string; scorer_version: string; duckdb_version: string; sqlglot_version: string; case_count: number; attempts: number };
  fairness: FairnessContract;
  models: ModelRun[];
  app_version?: string;
  scorer_version?: string;
  duckdb_version?: string;
  sqlglot_version?: string;
  conclusion?: { status: string; champions: string[]; models: Array<Record<string, unknown>> };
}

export interface RunEvent {
  seq: number;
  event_type: string;
  level: string;
  created_at: string;
  model_run_id: number | null;
  case_run_id: number | null;
  message: string;
  payload: Record<string, unknown>;
}

export interface CaseRunDetail {
  id: number;
  run_id: number;
  model_run_id: number;
  stable_key: string;
  title: string;
  question: string;
  category: string;
  radar_dimension: string;
  difficulty: string;
  status: string;
  attempt: number;
  prompt: string;
  raw_output: string | null;
  plan: QueryPlan | null;
  assumptions: string[] | null;
  visible_summary: string | null;
  generated_sql: string | null;
  formatted_sql: string | null;
  generation_ms: number | null;
  execution_ms: number | null;
  provider_request_id: string | null;
  token_usage: Record<string, number> | null;
  efficiency?: { tokens: EfficiencyMetrics["tokens"]; estimated_cost_usd: number | null; generation_ms: number | null; execution_ms: number | null };
  expected_digest: string | null;
  actual_digest: string | null;
  result_preview: ResultPreview | null;
  expected_result_preview?: ResultPreview;
  score: ScoreBreakdown | null;
  error_code: string | null;
  error_message: string | null;
  reference_sql?: string;
  required_ast: unknown[];
  comparison: Record<string, unknown>;
  suite_content_hash: string;
}

export interface RunHistoryItem {
  id: number;
  source_run_id: number | null;
  suite_version_id: number;
  suite_content_hash: string;
  status: string;
  attempts: number;
  case_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  models: Array<{ id: number; name: string; requested_model_id: string; status: string; official_score: number | null }>;
}
