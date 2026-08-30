# 能力清单

本文描述 `0.3.0` 的可观察能力。实现入口和验证方式同时列出，避免把计划、UI 文案或测试替身误写成已实现能力。

## 1. 模型配置

### 已实现

- 创建、修改、软删除和列出模型配置。
- 适配器类型：
  - `openai_compatible`
  - `codex_cli`
  - `claude_cli`
  - `gemini_cli`
- 请求模型 ID、Base URL、响应模式和适配器参数配置。
- 可选配置 USD/百万 Token 的输入、缓存输入、缓存写入和输出价格及来源/生效日期；运行创建时冻结价格快照。
- API Key 只保存引用：macOS Keychain 或显式环境变量引用；HTTP/API 输出不返回明文。
- 健康检查记录状态、解析后的模型 ID、CLI/服务版本、隔离详情、检查时间和过期时间。
- 运行创建前要求所选配置启用且健康检查仍有效。

### 适配器行为

| 适配器 | 输入路径 | 输出解析 | 隔离与限制 |
| --- | --- | --- | --- |
| OpenAI-compatible | Chat Completions SSE/JSON | 严格 JSON Schema、JSON Object 或文本恢复 | 只发送当前案例 Prompt；loopback Base URL 不继承系统代理；远端 HTTPS 可继承代理 |
| Codex CLI | `codex exec` stdin | Codex JSONL 中的可见 `agent_message` | macOS Seatbelt；默认拒绝；用户主目录数据不可读，只有原生二进制和 `~/.codex/auth.json` 被精确放行；项目与 `~/.ssh` 显式拒绝；案例临时目录可读写 |
| Claude CLI | stdin | Claude stream JSON | 空工具集、临时工作目录、受控环境；不把题库路径传给 CLI |
| Gemini CLI | stdin | Gemini stream JSON | 临时 Home；只复制认证选择；skills 禁用；工具调用一旦出现即判策略违规 |

所有 CLI 调用：

- 独立进程组；
- 可取消；
- 有调用超时；
- 原始 stdout 上限 1 MiB；
- 只传递受控环境变量白名单；
- 保存请求模型、解析模型、CLI 版本、Provider request ID（新运行）和生成耗时（新运行）。

## 2. 题库生命周期

### 草稿

- 创建题库和首个草稿版本。
- 从任一版本克隆新草稿。
- 修改 SQL Schema、Seed SQL、语义层、Prompt 模板和案例集合。
- 雷达维度是题库版本数据，不是应用硬编码枚举；历史版本可保留旧维度。
- 预览单案例实际 Prompt 和输出 JSON Schema。

### 发布

发布执行完整构建：

1. Pydantic 严格校验源合同。
2. 执行 Schema 与固定 Seed SQL。
3. 固定 DuckDB 运行参数：UTC、单线程、关闭外部访问。
4. 提取表、字段、主键、外键和语义关系快照。
5. 对每个参考 SQL 执行与归一化，生成完整金标 JSON。
6. 执行全部参考 SQL 自检；当前内置 v2 的 18 个案例必须得到 100 分。
7. 对规范化源内容计算 SHA-256 `content_hash`。
8. 写入内容寻址目录并把版本设为 `published`。

发布版本不可原地修改。更新必须克隆成新版本。

## 3. 内置 Retail Analytics 题库

### v2（当前）

- 18 个案例。
- 6 个雷达维度，每个维度 3 题：
  - 基础查询
  - 连接与粒度
  - 聚合与指标
  - 时间与窗口
  - 复杂查询
  - 数据开发
- 难度：3 easy、10 medium、5 hard。
- 固定数据基数包括 120 位客户、36 个商品、5 个渠道、604 个订单，以及专门构造的孤立维度、重复支付、并列值和对账差异。
- 案例覆盖筛选、连接、聚合、反连接、窗口、CTE、条件聚合、多事实表预聚合和数据质量检查。

### v1（历史）

- 12 个案例。
- 仍可由数据库中的历史源完整重建。
- 源、金标和历史运行均在 `evidence/` 中发布。

## 4. 运行编排

- 选择一个或两个健康模型。
- 选择全部案例或案例子集。
- 每个案例执行 1–3 次尝试。
- 模型按运行快照执行，运行中修改配置不改变已创建运行。
- 每个模型内部按案例顺序执行；模型之间可并发。
- 取消请求传播到适配器进程和后续案例。
- 应用启动时把意外遗留的运行恢复为 `interrupted`，避免永久停在 `running`。
- `exact` 复跑复制原题库哈希、模型名、适配器、模型 ID、参数、价格、CLI/隔离快照和案例选择。
- `current` 复跑重新读取当前模型配置，报告中明确反映差异。

运行状态：

- ComparisonRun：`pending`、`running`、`cancelling`、`completed`、`completed_with_errors`、`cancelled`、`failed`、`interrupted`。
- ModelRun：`pending`、`running`、`completed`、`completed_with_errors`、`cancelled`、`failed`。
- CaseRun：`pending`、`running`、`completed`、`failed`、`cancelled`。

## 5. Prompt 与模型输出

- Prompt 由发布版本的模板、结构快照、语义层、问题和 JSON 输出合同构成。
- Prompt 不包含参考 SQL、金标结果或必需 AST 规则。
- 实际 Prompt 在运行前持久化，并随案例证据公开。
- `query-plan-v1` 要求模型输出：
  - `plan.grain`
  - `plan.sources`
  - `plan.joins`
  - `plan.filters`
  - `plan.metrics`
  - `plan.steps`
  - `plan.risks`
  - `sql`
  - `summary`
  - `assumptions`
- 严格 JSON 得到协议分；仅允许恢复“单层、无前后文本”的 `json` Markdown fence。其他文本判输出合同错误。

## 6. SQL 守卫和执行

### 静态守卫

- DuckDB 方言解析。
- 只能有一条语句。
- 根节点必须是 SQLGlot `Query`。
- 禁止写入、DDL、事务、附加数据库及其他危险 AST 节点。
- 禁止 `read_*`、`*_scan` 和已知外部访问函数。
- 禁止非 `main` schema/catalog。
- 只允许发布快照中的表或当前查询定义的 CTE。

### 运行时隔离

- 使用 `multiprocessing spawn` 独立进程。
- DuckDB 以 `read_only=True` 打开。
- `enable_external_access=false`。
- `threads=1`、`memory_limit=512MB`、`TimeZone=UTC`。
- 默认 5 秒执行超时。
- 每案例按合同限制最大结果行数；内置题库上限不超过 10,000。
- 超时先 terminate，仍存活再 kill。

## 7. 结果比较

- 数值统一转 `Decimal`，按案例 `decimal_scale` 使用 ROUND_HALF_UP。
- 支持绝对和相对容差，取两者较大值。
- 日期按 ISO，时间戳转 UTC 微秒，字符串做 Unicode NFC。
- 保留 NULL、布尔类型和重复行语义。
- 列名去引用符、大小写折叠；同名集合可按名称重排。
- 名称不足以对齐时，按列值指纹寻找唯一映射；歧义时失败，不猜测。
- 行比较是多重集最大匹配，不把重复行折叠为集合。
- 无顺序要求时，F1=1 即顺序项通过；有顺序要求时逐行比较。
- 保存 expected/actual digest、匹配数、precision、recall、F1、缺失和额外行预览。

## 8. SQL 能力规则

可发布在案例中的 AST 规则：

- 最少 Join/Case 节点数；
- LEFT JOIN 类型；
- 相关子查询；
- NOT EXISTS；
- 窗口函数名称、分区和排序；
- 查询深度；
- CTE 数量；
- SUM 条件聚合数量；
- 两个事实度量先分别预聚合再连接。

规则只用于评分，不进入模型 Prompt。

## 9. 评分与报告

- 每案例固定 100 分公式，见 [methodology.md](methodology.md)。
- 多次尝试保存 mean、success rate 和总体标准差。
- 模型总分按案例 `weight` 加权平均。
- 分类/雷达维度分数按同一权重规则聚合。
- 报告包含：冠军、评级、优势、短板、失败计数、热力图、雷达图、分类柱图和逐案例数据。
- 公平性合同自动比较适配器、响应模式和参数：
  - 所有路径一致：`pure_model`；
  - 路径不同：`access_path`；
  - 单模型：`single_model`。

## 10. 实时事件与查询工作区

- 19 种有类型事件，先持久化再推送。
- 每个运行的 `seq` 单调递增。
- SSE 支持 `after_seq` 断线续传，并用持久化历史补齐订阅水位线前后的竞态。
- 历史接口支持模型、案例、级别、事件类型、搜索词、offset 和 limit 筛选。
- Provider delta 以 250 ms 缓冲，避免逐 token 写库。
- 实时页支持自动跟随、筛选和大日志虚拟化。
- 案例工作区展示规划、SQL、执行结果、差异和评分；参考 SQL/金标必须显式请求后才返回。

## 11. 报告与证据导出

- `run-report-v2`：单次运行的静态报告合同，包含正确性、Token、估算费用、生成/执行时长及覆盖率。
- Web UI“导出报告 JSON”只下载当前运行的 `run-report-v2`；全量题库、日志和逐案例证据使用 CLI 导出。
- `text-to-sql-evidence-v1`：公开证据目录合同。
- 所有已发布题库和所有持久化运行全量导出。
- 逐文件 SHA-256 与目录 `bundle_sha256`。
- 校验器发现缺文件、新增未登记文件或摘要变化即失败。
- 导出器脱敏常见 Provider 密钥、Authorization、项目根目录、用户主目录和应用临时路径。
- 不导出原始 SQLite、WAL/SHM、Keychain 值、CLI Home 或二进制 DuckDB；DuckDB 可由公开 Schema/Seed 确定性重建。

## 12. Web UI

- 模型配置、价格快照和健康状态。
- 题库列表、Schema/Seed/Semantic/Cases 编辑器、实体关系图、Prompt 预览和发布。
- 新建运行的模型、题目、尝试次数与公平性预览。
- 运行状态、实时日志、取消和案例工作区。
- 历史运行列表、精确/当前配置复跑。
- 报告总分、Token/正确等价题、估算费用/正确等价题、生成耗时 P95、覆盖率、分类、雷达、案例热力图和结论。
- 报告支持复制当前链接、下载当前报告 JSON 和按冻结快照精确复跑。
- 演示模式仅保存在当前浏览器 tab 的 `sessionStorage`，不写后端。

## 13. 明确不支持

- 多用户账号、RBAC、团队隔离或公网部署安全模型。
- 云端队列、分布式 Worker 或多机并发。
- 除 DuckDB 外的执行方言。
- 自动化浏览器 E2E 测试；当前 UI 有 Vitest/Testing Library 测试和真实浏览器 smoke 验证。
- 基于单次运行的统计显著性声明。
- 防止公开题库被训练数据污染。
- Provider 实际账单、CLI 包月成本分摊、能耗、端到端网络延迟或吞吐排名。
