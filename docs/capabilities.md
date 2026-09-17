# 能力清单

本文描述 `0.4.0` 的可观察能力。实现入口和验证方式同时列出，避免把计划、UI 文案或测试替身误写成已实现能力。

## 1. 模型配置

### 已实现

- 创建、修改、软删除和列出模型配置。新建配置只能使用 `adapter_kind=pi` 与 `response_mode=text`；旧 `openai_compatible`/CLI 配置保持可见和可删除，但不能执行本地就绪检查或进入新运行。
- 必填参数：`provider`、`auth_mode`（`oauth` 或 `api_key`）和固定 `timeout_seconds=180`；可选 `temperature`、`reasoning_effort`，API Key Provider 还可选 `max_tokens`。
- GPT 订阅固定 `provider=openai-codex`、`auth_mode=oauth`，本地 catalog 包含 `gpt-5.6-luna` / `gpt-5.6-sol`，不允许 Base URL/API Key/`max_tokens`；输出上限由 Provider 管理。凭据可由支持的外部 Pi CLI 登录或既有 Codex 登录文件导入系统钥匙串。
- API Key 模式可选 OpenAI、Anthropic、Google 或自由填写 Provider 标识，Base URL 可选；该列表是接入入口，不是完整模型目录承诺。API Key 只保存到系统钥匙串，或保存显式环境变量引用；HTTP/API 输出不返回明文。
- 可选配置 USD/百万 Token 的输入、缓存输入、缓存写入和输出价格及来源/生效日期；运行创建时冻结价格快照。
- “检查本地配置”只验证本地 catalog、凭据、参数、Pi harness/bridge/policy 与隔离详情；不调用模型、不消耗生成，也不证明 Provider 可用。
- 运行创建前要求所选配置启用、属于 Pi 且本地就绪检查仍有效。

### Pi 调用行为

| 层 | 当前合同 |
| --- | --- |
| Node bridge | `runtime/pi` 固定 `@earendil-works/pi-ai` 0.85.1；stdin/stdout 传输单次请求和结果 |
| Prompt | 使用评测引擎生成的固定 Prompt；单轮，不加载外部配置或会话 |
| 工具与重试 | 工具关闭、工具数 0、生成尝试上限 1；本地就绪检查不生成内容（实际计数 0），运行调用保存实际计数 |
| 输出 | Pi 返回文本后进入现有 `query-plan-v1` 严格解析和 SQL 评测链路 |
| OAuth | 仅 `openai-codex`；凭据文件是导入来源，运行使用系统钥匙串引用 |
| API Key | OpenAI/Anthropic/Google/自定义 Provider；只有当前案例 Prompt 和调用参数离开进程 |

每次真实调用在 `provider.requested` wire payload 与 `provider.completed` 证据中保存请求/解析模型身份、Provider request ID（若返回）、Token（若返回）、生成耗时和不含凭据的有效控制快照。已完成一次受控订阅 smoke：Pi 0.85.1、`openai-codex/gpt-5.6-luna`、既有 OAuth 凭据导入 keyring、单次请求、无工具，严格 JSON 中的 SQL `SELECT 1` 执行得到 `[(1,)]`；生成 2839 ms，usage 为 381 input / 66 output。该 smoke 没有创建 benchmark run 或历史记录，也不证明其他远端 Provider 已测试。

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
6. 发布前回归验收另用完整评分器验证内置 v4 的 18 个参考答案全部 100 分；发布构建本身验证参考 SQL 可执行性并生成金标。
7. 对规范化源内容计算 SHA-256 `content_hash`。
8. 写入内容寻址目录并把版本设为 `published`。
- 题库挑战检查可在最多 10 个临时数据变体上执行候选 SQL，验证“应正确/应错误”候选是否能被确定性比较器区分；不修改草稿或发布版本。
发布版本不可原地修改。更新必须克隆成新版本。

## 3. 内置 Retail Analytics 题库

### v4（当前，scorer 2.0.0）

- 18 个案例。
- 6 个雷达维度，每个维度 3 题：
  - 基础查询
  - 连接与粒度
  - 聚合与指标
  - 时间与窗口
  - 复杂查询
  - 数据开发
- 难度：3 easy、10 medium、5 hard。
- 固定数据包括 123 位客户、39 个商品、5 个渠道、617 个订单；覆盖空月/零收入、原始均值精度、金额分档端点、重复退货、pending 支付、无销量品类及无明细/非完成对账异常。v2 的 120/36/5/604 原样保留，不重建历史金标。
- 案例覆盖筛选、连接、聚合、反连接、窗口、CTE、条件聚合、多事实表预聚合和数据质量检查。

### v1（历史）

- 12 个案例。
- 仍可由数据库中的历史源完整重建。
- 源、金标和历史运行均在 `evidence/` 中发布。

## 4. 运行编排

- 选择 1–6 个已启用且健康的 Pi 模型；历史适配器不可选择。
- 选择全部案例或案例子集。
- 新运行每个案例固定执行 1 次；API 也拒绝其他 attempts 值。
- 模型按运行快照执行，运行中修改配置不改变已创建运行。
- 每个模型内部按案例顺序执行；模型之间可并发。
- 取消请求传播到 Pi bridge 和后续案例。
- 应用启动时把意外遗留的运行恢复为 `interrupted`，避免永久停在 `running`。
- `exact` 复跑复制原题库哈希、模型名、适配器、模型 ID、参数、价格和隔离快照；历史运行仍按原快照解释，不被迁移为 Pi。
- `current` 复跑重新读取当前模型配置；只有满足当前 Pi 合同的配置才能创建新运行。
- 新建运行前执行只读预检：验证题库、Pi profile、案例、固定单次尝试、本地就绪检查有效期和价格完整性；不调用模型、不创建记录。
- 预检历史估算只使用同题库、所选案例且模型/adapter/Provider/认证/参数/响应合同/bridge 版本一致的样本；缺证据不编造数字。
- `failed` 补跑取所有参赛模型失败、未完成或未满分案例的有序并集，再让同组模型公平比较同一子集；空子集返回 409，进行中运行禁止补跑。
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

- 数值转 Decimal 保留全精度，不以 decimal_scale 先舍入；12=12.0，但12.4不等于12。
- 金标整数列精确比较；非整数列支持绝对和相对容差，取两者较大值。
- 日期按 ISO，时间戳转 UTC 微秒，字符串做 Unicode NFC。
- 保留 NULL、布尔类型和重复行语义。
- 列名去引用符、大小写折叠；同名集合可按名称重排。
- 名称不足以对齐时，先精确指纹、再类型/容差二分图唯一匹配；歧义失败，不猜测。
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

规则只用于辅助评分，不进入模型 Prompt。窗口按作用域来源识别，预聚合按事实来源/度量/分组和消费关系识别，不强制表别名或 CTE 名；来源不同及事实放大仍拒绝。

## 9. 评分与报告

- 每案例固定 100 分公式，见 [methodology.md](methodology.md)。
- 多次尝试保存 mean、nonzero_score_rate 和总体标准差；非零得分率不等于结果正确率。
- 模型总分按案例 `weight` 加权平均。
- 分类/雷达维度分数按同一权重规则聚合。
- 报告包含独立的结果正确、执行成功、协议通过、覆盖率、关键回合与逐题解释；所有计划尝试进入分母。
- 新 Pi 多模型运行标为 `controlled_harness`：统一的是 Pi harness、文本响应、单轮/无工具/无重试和 Prompt 合同；Provider、认证、模型身份和显式生成参数仍逐项披露。这不表示同一端点或同一模型。
- 新 Pi 单模型运行仍标为 `single_model`。
- 历史报告保留原 `pure_model`、`access_path` 或 `single_model` 标签及原始字段，不回写为新分类。

## 10. 实时事件与查询工作区

- 19 种有类型事件，先持久化再推送。
- 每个运行的 `seq` 单调递增。
- SSE 支持 `after_seq` 断线续传，并用持久化历史补齐订阅水位线前后的竞态。
- 历史接口支持模型、案例、级别、事件类型、搜索词、offset 和 limit 筛选。
- Provider delta 以 250 ms 缓冲，避免逐 token 写库。
- 实时页支持自动跟随、筛选和大日志虚拟化。
- 案例工作区展示规划、SQL、执行结果、差异和评分；参考 SQL/金标必须显式请求后才返回。

## 11. 报告与证据导出

- 新 scorer 2.x 动态报告为 run-report-v4/result-quality-v2：业务结果与格式/政策/执行错误分开，资源按实际正确题归一；逐题实际调用证据与预检配置分开。历史 scorer 1.x 保留 v3/v1，已发布旧报告不改写。
- Web UI 下载当前运行报告 JSON；单场完整证据使用预览/确认导出，CLI 仍支持全量导出。
- `text-to-sql-evidence-v1`：公开证据目录合同。
- 所有已发布题库和所有持久化运行全量导出。
- 逐文件 SHA-256 与目录 `bundle_sha256`。
- 校验器发现缺文件、新增未登记文件或摘要变化即失败。
- 导出器脱敏常见 Provider 密钥、Authorization、项目根目录、用户主目录和应用临时路径。
- 不导出原始 SQLite、WAL/SHM、Keychain 值、CLI Home 或二进制 DuckDB；DuckDB 可由公开 Schema/Seed 确定性重建。
- 终态运行可先只读预览脱敏报告、清单摘要和警告，再以预览摘要显式确认导出仅该运行和所属题库版本的 ZIP。
- “确认并导出发布包”只生成临时下载文件：不替换 `evidence/`，不复制 SQLite，也不代表内容已经上线或完成公网部署。
## 12. Web UI

- 模型配置、价格快照和健康状态。
- 题库列表、Schema/Seed/Semantic/Cases 编辑器、实体关系图、Prompt 预览和发布。
- 新建运行支持浏览器本地方案、题目子集与只读预检；本地就绪检查失效时不能开赛。
- 实时页展示真实业务题意、全部作答与持久化日志；终态停止事件订阅，不持续重连。
- 报告分“看比赛 / 看门道 / 查证据”；关键回合按全部计划尝试和题目权重计算，历史回放明确标识为回放。
- 匿名竞猜揭晓前不展示身份、排名或分数；预测不计分。公开站关键题竞猜仅保存在浏览器，不生成票数。
- 对照先核验题库、案例、attempts、协议、工具版本、endpoint 指纹和隔离控制；不一致时不输出进退结论。
- 复测支持精确/当前配置、全部/失败未满分子集；导出发布包不代表部署。
- 统一中性深色、实体分隔、青橙选手色、少字号档位；主要正文 16px、辅助信息 14px，保留焦点可见与 reduced-motion。
- 录屏模式只隐藏导航，保存在当前 tab 的 sessionStorage；方案保存在 localStorage，不包含密钥。

## 13. 明确不支持

- 多用户账号、RBAC、团队隔离或公网部署安全模型。
- 云端队列、分布式 Worker 或多机并发。
- 除 DuckDB 外的执行方言。
- 自动化浏览器 E2E 测试；当前 UI 有 Vitest/Testing Library 测试和真实浏览器 smoke 验证。
- 基于单次运行的统计显著性声明。
- 防止公开题库被训练数据污染。
- Provider 实际账单、CLI 包月成本分摊、能耗、端到端网络延迟或吞吐排名。
