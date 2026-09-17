# 数据、API、事件与证据合同

## 1. 版本标识

| 合同 | 当前值 | 变更原则 |
| --- | --- | --- |
| 应用 | `0.4.0` | 功能和持久化行为变化 |
| 评分器 | `2.0.0` | 全精度数值、整数列精确比较、别名无关结构检查；旧分不重算 |
| 模型输出 | `query-plan-v1` | 必填字段或语义不兼容变化必须新版本 |
| 运行报告 | `run-report-v4` | `result-quality-v2` 与实际调用证据；旧 scorer 1.x 保留 v3/v1 |
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
  decimal_scale: 0..12 # 保留题库结构字段；scorer 2.x 不以它舍入判等值
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
- `attempts`（新建运行固定为 `1`；历史快照可保留旧值）
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
- `adapter_kind_snapshot`（新运行固定 `pi`）
- `base_url_snapshot`（仅 API Key 模式可用）
- `response_mode_snapshot`（新运行固定 `text`）
- `requested_model_id`
- `parameters_snapshot_json`：`provider`、`auth_mode`、固定 `timeout_seconds=180`，以及可选 `temperature`/`reasoning_effort`；`max_tokens` 仅 API Key Provider 可选，`openai-codex` 明确拒绝并由 Provider 管理输出上限
- `pricing_snapshot_json`（USD/百万 Token 的可选价格快照）
- `api_key_ref_snapshot`（仅引用，不是密钥；OAuth/API Key 明文都不进入报告）
- `cli_version_snapshot`（Pi bridge/harness 版本；字段名为历史兼容保留）
- `isolation_snapshot_json`（harness/policy、无工具、单次生成、上下文、Prompt 摘要、Provider/认证、模型身份来源与有效参数）

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

### 效率派生合同 `efficiency-v2`

模型报告包含：

- `correct_cases = count(result_correct is true)`；
- 标准化 Token：`input`、`cached_input`、`cache_write_input`、`output`、`reasoning_output`、`total`；
- `estimated_cost_usd`，仅在所有已用 Token 类型都有冻结单价时产生；
- 模型生成耗时 `total/mean/p50/p95`；
- SQL 执行耗时 `total/mean`；
- `per_correct_case.tokens/estimated_cost_usd/generation_ms`；正确题数为零或该指标覆盖不完整则为 null；
- 每类指标的 `coverage.measured/total`。

`reasoning_output` 只作披露，不在 `total` 中重复计数。Pi input 已排除 cacheRead，不重复相减。费用是冻结价格下的估算，订阅无价则 null。历史 scorer 1.x 保留 `efficiency-v1`、`correct_case_equivalents=sum(score/100)` 与 `per_correct_case_equivalent`；这些是得分折算，不是正确题，不能混排。

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
| POST | `/model-profiles/{id}/check` | 只检查本地 catalog、凭据、参数和 Pi bridge 就绪状态；不生成内容、不证明 Provider 可用 |

新建 profile 合同：`adapter_kind="pi"`、`response_mode="text"`。`parameters.provider` 为非空字符串，`parameters.auth_mode` 为 `oauth|api_key`，`parameters.timeout_seconds=180`。`openai-codex` 只允许 OAuth 且拒绝 Base URL/API Key/`max_tokens`，输出上限由 Provider 管理；API Key 模式可使用 OpenAI、Anthropic、Google 或自定义 Provider 标识，`api_key` 与 `api_key_env` 互斥。旧适配器记录仍可由 GET/DELETE 访问；前端不提供编辑或检查，并禁止其进入 preflight 和新运行。OAuth 本地检查只从支持的外部 Pi CLI 或既有 Codex 登录凭据文件导入系统钥匙串，不生成内容，也没有独立 auth API。

### 题库

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `/suites` | 返回题库、版本、结构和案例；这是作者/本地管理接口，不是模型 Prompt |
| POST | `/suites` | 创建题库和草稿 |
| POST | `/suites/{id}/clone?source_version_id=` | 克隆到新草稿 |
| PATCH | `/suite-versions/{id}` | 只允许修改 draft |
| POST | `/suite-versions/{id}/publish` | 确定性构建、校验、哈希和发布 |
| GET | `/suite-versions/{id}/prompt-preview?case_id=` | 返回实际 Prompt 和输出 Schema，不返回参考 SQL |
| POST | `/suite-versions/{id}/challenge-check` | 在临时 DuckDB 数据变体上检验候选 SQL 能否区分正确/错误答案；只读且不修改题库 |

### 运行

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `/runs?limit=` | 最近运行摘要，limit 1..100 |
| POST | `/runs/preflight` | 只读预检 suite/Pi model/case、固定 `attempts=1`、本地就绪检查有效期和价格；不调用模型、不创建运行 |
| POST | `/runs` | 仅接受 Pi profile 与 `attempts=1`，冻结配置并异步启动；返回运行 ID |
| POST | `/runs/{id}/cancel` | 幂等取消请求 |
| POST | `/runs/{id}/rerun?mode=exact\|current&scope=all\|failed` | 精确快照或当前配置复跑；failed 为各模型失败、未完成或未满分案例的有序并集，所有模型使用同一子集 |
| GET | `/runs/{id}` | 运行快照和案例摘要 |
| GET | `/runs/{id}/report` | run-report-v3 动态生成报告，保留历史官方分数 |
| GET | `/runs/{id}/publication-preview` | 终态运行的脱敏发布预览、清单摘要、警告和确认摘要；只读 |
| POST | `/runs/{id}/publication-export` | 请求体确认 preview_digest 后导出该运行及所属题库的临时 ZIP；不替换 evidence/，也不部署 |
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

## 7. 报告合同 `run-report-v4`

顶层至少包含：

- `report_schema_version` 与 `quality_schema_version=result-quality-v2`；
- `id`、`source_run_id`、状态和时间；
- `suite_version_id`、`suite_content_hash`、案例选择、attempts；
- `protocol`：输出/app/scorer/DuckDB/SQLGlot 版本和案例数；
- `fairness`：新 Pi 多模型报告为 `controlled_harness`，表示共享 Pi/文本/单轮/无工具/无重试/Prompt 控制；Provider、认证、模型和显式参数仍披露为控制或差异。历史 `pure_model`/`access_path`/`single_model` 原样保留；
- `models`；
- `conclusion`。

每个模型至少包含：

- profile 名称快照；
- 请求/解析模型 ID；
- 适配器、响应模式、参数、价格、CLI 和隔离快照；
- 状态、official score、失败数；
- categories、attempt statistics 与 `efficiency-v2`；非零分率仍叫 `nonzero_score_rate`，不冒充成功率；
- `quality`：业务结果、执行、JSON、格式分别计数；分母 total 为全部计划尝试，evaluated 披露已执行结果证据覆盖率。列名/AST 不决定业务正确。未执行 result_correct=null；failure_counts 区分政策拒绝、协议、执行、Provider、基础设施、取消、业务结果及格式错误；
- `endpoint_fingerprint`：冻结 endpoint 的 SHA-256 或 null，用于对照检查，不输出原始 endpoint；
- 全部案例的题目、权重、结果、评分、效率、质量及 `invocation`；后者仅从实际调用事件提取，不复制预检快照。包含实际参数、wire_generation、SDK/bridge/lock/policy/系统 Prompt 摘要、请求身份来源及完成状态；无事件时 null，不作推断；

历史官方分数和 efficiency-v1 不重算。scorer 1.x 动态报告仍为 run-report-v3/result-quality-v1；所有既有公开证据保持原字节、原字段、原摘要。精确复跑若 app/scorer/DuckDB/SQLGlot 已变化，返回 exact_environment_changed，不把当前裁判冒充原环境。

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
