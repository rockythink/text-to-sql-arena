# 数据、API、事件与证据合同

## 1. 版本标识

| 合同 | 当前值 | 变更原则 |
| --- | --- | --- |
| 应用 | `0.2.0` | 功能和持久化行为变化 |
| 评分器 | `1.0.0` | 评分公式、比较语义或聚合语义变化必须升级 |
| 模型输出 | `query-plan-v1` | 必填字段或语义不兼容变化必须新版本 |
| 运行报告 | `run-report-v1` | 静态报告结构不兼容变化必须新版本 |
| 公开证据 | `text-to-sql-evidence-v1` | 目录/清单/验真语义不兼容变化必须新版本 |
| DuckDB | `1.5.5` | 精确固定 |
| SQLGlot | 运行时解析版本 | 每次运行冻结实际包版本 |

## 2. 题库源合同

`SuiteSource` 是严格对象，未知字段拒绝。

```yaml
name: string
description: string
dialect: duckdb
schema_sql: string
seed_sql: string
semantic:
  entities: []
  relationships: []
  metrics: []
  dimensions: []
  business_rules: []
prompt_template: string
cases: []
```

### 案例

```yaml
stable_key: string          # 版本内唯一
title: string
category: string
radar_dimension: string     # 非空，最长 100；由题库版本定义
difficulty: string
question: string
reference_sql: string
required_ast: []
comparison:
  row_order_significant: boolean
  duplicate_policy: multiset
  decimal_scale: 0..12
  abs_tolerance: decimal string
  rel_tolerance: decimal string
  max_rows: 1..10000
weight: positive number
sort_order: positive integer # 版本内唯一
```

### 内容哈希

哈希输入是规范化后的完整源合同，而不是文件时间、数据库 ID 或产物路径。相同源必须产生相同 SHA-256；任何 Schema、Seed、语义、Prompt、案例、参考 SQL 或比较规则变化都必须改变哈希。

发布版本：

- `status=published`；
- `content_hash` 非空且唯一；
- `structure_snapshot_json` 非空；
- `published_at` 非空；
- 不允许 PATCH。

## 3. 模型输出合同 `query-plan-v1`

```json
{
  "plan": {
    "grain": "string",
    "sources": ["string"],
    "joins": ["string"],
    "filters": ["string"],
    "metrics": ["string"],
    "steps": ["string"],
    "risks": ["string"]
  },
  "sql": "string",
  "summary": "string",
  "assumptions": ["string"]
}
```

对象 `extra="forbid"`。所有字段必填。`summary` 是模型可见说明，不影响 SQL 结果分；`plan` 可视化并作为证据，但只有案例的 `required_ast` 用于 SQL 能力分。

## 4. 运行持久化合同

### ComparisonRun

不可变快照：

- `suite_version_id`
- `suite_content_hash`
- `selected_case_keys_json`
- `attempts`
- `app_version_snapshot`
- `scorer_version_snapshot`
- `duckdb_version_snapshot`
- `sqlglot_version_snapshot`
- `output_contract_snapshot`

状态字段：

- `status`
- `next_event_seq`
- `created_at`
- `started_at`
- `finished_at`
- `cancellation_requested_at`
- `source_run_id`

### ModelRun

不可变快照：

- `profile_name_snapshot`
- `adapter_kind_snapshot`
- `base_url_snapshot`
- `response_mode_snapshot`
- `requested_model_id`
- `parameters_snapshot_json`
- `api_key_ref_snapshot`（仅引用，不是密钥）
- `cli_version_snapshot`
- `isolation_snapshot_json`

运行结果：

- `resolved_model_id`
- `status`
- `official_score`
- `conclusion_json`

报告绝不通过 `model_profile_id` 回查当前名称或参数。profile 被改名/删除后，历史报告保持不变。

### CaseRun

输入和输出证据：

- `prompt_text`
- `raw_output`
- `plan_json`
- `assumptions_json`
- `visible_summary`
- `generated_sql`
- `formatted_sql`
- `token_usage_json`
- `provider_request_id`
- `generation_ms`
- `execution_ms`

比较和评分证据：

- `expected_digest`
- `actual_digest`
- `result_preview_json`
- `score_breakdown_json`
- `error_code`
- `error_message`

历史运行中无法恢复的 `provider_request_id` 和 `generation_ms` 为 `null`。

## 5. 事件合同

### 信封

```json
{
  "seq": 1,
  "event_type": "run.started",
  "level": "info",
  "created_at": "ISO-8601",
  "model_run_id": null,
  "case_run_id": null,
  "message": "",
  "payload": {}
}
```

不变量：

- `seq` 在单个运行内从 1 单调递增且唯一；
- 事件先写 SQLite，再发布到内存 Hub；
- message/payload 入库前递归脱敏；
- `model_run_id` 和 `case_run_id` 只在对应作用域事件中填写；
- SSE 的事件名等于 `event_type`；
- 客户端使用 `after_seq` 恢复，不能按时间戳去重。

### 事件类型

| 事件 | 作用域 | 含义 |
| --- | --- | --- |
| `run.created` | run | 运行与案例快照已创建 |
| `run.started` | run | ComparisonRun 首次进入 running |
| `model.started` | model | 一个 ModelRun 开始，必须含 model_run_id |
| `case.started` | case | 一个尝试开始 |
| `prompt.built` | case | 实际 Prompt 已冻结 |
| `provider.requested` | case | 适配器开始请求 |
| `provider.delta` | case | 250 ms 缓冲后的可见流增量 |
| `provider.completed` | case | Provider 返回完成 |
| `plan.completed` | case | 输出合同解析成功 |
| `sql.parsed` | case | SQL 守卫通过 |
| `sql.rejected` | case | SQL 解析/守卫拒绝 |
| `sql.executed` | case | SQL Worker 成功返回 |
| `result.compared` | case | 金标比较完成 |
| `score.completed` | case | 案例分数落库 |
| `case.failed` | case | 案例终止失败 |
| `model.completed` | model | 模型聚合结束 |
| `run.completed` | run | 正常或带错误结束 |
| `run.cancelled` | run | 取消完成 |
| `run.interrupted` | run | 启动恢复发现中断 |

> 版本边界：以上事件语义从应用 0.2.0 起生效。公开的 0.1.0 历史日志按原样保留；其中运行启动曾记录为缺少 `model_run_id` 的 `model.started`。这不是当前合同，也不会在导出时重写成未实际发生的事件。审计记录见 A-05。

## 6. HTTP API

所有路径以下均带 `/api` 前缀。写请求必须有 bootstrap session cookie 和 `X-CSRF-Token`。

### 系统

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `/health` | `{status, version}` |
| GET | `/bootstrap` | 设置 HttpOnly session cookie，返回 CSRF token、版本信息 |

### 模型配置

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `/model-profiles` | 列出未软删除配置；只返回 `has_secret` 和 secret backend |
| POST | `/model-profiles` | 创建配置；明文 API Key 只进入密钥存储 |
| PATCH | `/model-profiles/{id}` | 修改当前配置，不改历史快照 |
| DELETE | `/model-profiles/{id}` | 软删除；运行引用保留 |
| POST | `/model-profiles/{id}/check` | 执行适配器健康/兼容性检查 |

### 题库

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `/suites` | 返回题库、版本、结构和案例；这是作者/本地管理接口，不是模型 Prompt |
| POST | `/suites` | 创建题库和草稿 |
| POST | `/suites/{id}/clone?source_version_id=` | 克隆到新草稿 |
| PATCH | `/suite-versions/{id}` | 只允许修改 draft |
| POST | `/suite-versions/{id}/publish` | 确定性构建、校验、哈希和发布 |
| GET | `/suite-versions/{id}/prompt-preview?case_id=` | 返回实际 Prompt 和输出 Schema，不返回参考 SQL |

### 运行

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `/runs?limit=` | 最近运行摘要，limit 1..100 |
| POST | `/runs` | 冻结配置并异步启动；返回运行 ID |
| POST | `/runs/{id}/cancel` | 幂等取消请求 |
| POST | `/runs/{id}/rerun?mode=exact|current` | 精确快照或当前配置复跑 |
| GET | `/runs/{id}` | 运行快照和案例摘要 |
| GET | `/runs/{id}/report` | `run-report-v1` 静态报告 |
| GET | `/case-runs/{id}?include_reference=false` | 完整案例证据；默认不揭示参考 SQL/金标 |

### 事件

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `/runs/{id}/events?after_seq=` | SSE：历史补齐 + 实时订阅 + heartbeat |
| GET | `/runs/{id}/events/history` | JSON 历史；支持 after_seq、模型/案例/级别/类型/搜索/分页 |

### 错误信封

```json
{
  "code": "stable_error_code",
  "message": "可读信息",
  "details": {},
  "request_id": "UUID"
}
```

HTTP 验证错误同样使用此信封。异常 details 会脱敏。

## 7. 报告合同 `run-report-v1`

顶层至少包含：

- `report_schema_version`
- `id`、`source_run_id`、状态和时间；
- `suite_version_id`、`suite_content_hash`、案例选择、attempts；
- `protocol`：输出/app/scorer/DuckDB/SQLGlot 版本和案例数；
- `fairness`；
- `models`；
- `conclusion`。

每个模型至少包含：

- profile 名称快照；
- 请求/解析模型 ID；
- 适配器、响应模式、参数、CLI 和隔离快照；
- 状态、official score、失败数；
- categories、attempt statistics；
- 全部案例及其输入、输出、结果和评分字段。

报告中的 `conclusion.status` 描述是否能形成结论；顶层 `status` 描述运行状态，两者不是同一字段。例如某模型个别案例失败时，顶层可为 `completed_with_errors`，但仍可形成模型比较结论。

## 8. 公开证据合同 `text-to-sql-evidence-v1`

```text
evidence/
  index.json
  suites/<content-hash>/
    source/{schema.sql,seed.sql,semantic.json,prompt.md,cases.yaml}
    artifact-manifest.json
    gold/*.json
    suite.json
    bundle-manifest.json
  runs/run-NNNN/
    report.json
    events.jsonl
    cases/case-run-NNNNN.json
    bundle-manifest.json
```

`bundle-manifest.json`：

```json
{
  "schema_version": "text-to-sql-evidence-v1",
  "kind": "suite|run",
  "bundle_sha256": "sha256",
  "files": [
    {"path": "relative/path", "sha256": "sha256", "bytes": 123}
  ]
}
```

`bundle_sha256` 是按路径排序后的 `files` 记录规范 JSON 的 SHA-256。清单本身不包含在自己的 `files` 中。

校验失败条件：

- `index.json` 版本错误；
- 任一登记文件缺失；
- 文件字节数或摘要不同；
- 目录出现未登记文件；
- 整包摘要不同；
- index 中整包摘要与目录清单不同。

## 9. 兼容性规则

- 新增可选字段可以保持同一报告/证据主版本。
- 删除字段、改变含义、改变摘要算法或评分语义必须升主合同版本。
- 评分器版本必须随任何得分变化升级，即使 API 结构不变。
- 历史证据永远按运行自己的快照解释；不能用当前版本号覆盖。
- 旧题库维度和分类必须作为版本数据保留，应用枚举不得阻止重建。
