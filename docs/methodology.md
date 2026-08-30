# 评测方法

## 1. 可复核单位

最小可复核单位不是“模型总分”，而是一个 CaseRun：

```text
题库内容哈希
+ 运行与模型快照
+ 实际 Prompt
+ 模型原始输出
+ 解析后的 plan/SQL
+ SQL 守卫和执行结果
+ 固定金标
+ 结果差异
+ 评分明细
+ 有序事件
```

总分只是这些案例证据按公开公式聚合后的派生值。

## 2. 题库版本与确定性

每个发布版本由以下源内容决定：

- `schema.sql`
- `seed.sql`
- `semantic.json`
- `prompt.md`
- `cases.yaml`

发布过程对规范化源计算 SHA-256。内容变化必须产生新哈希和新版本。

构建环境固定：

- DuckDB 1.5.5；
- `TimeZone='UTC'`；
- `threads=1`；
- `enable_external_access=false`；
- Seed 完全由 SQL 表达，不依赖当前时间、随机数或外部文件。

每个参考 SQL 在发布时执行，结果按第 6 节规则归一化并写入金标 JSON。发布还会让参考 SQL走完整评分器；内置 v2 的 18 个案例全部必须得到 100 分。

## 3. 当前案例矩阵

v2 共 18 题，每个雷达维度 3 题：

| 维度 | 主要能力 |
| --- | --- |
| 基础查询 | 过滤、空值/布尔/日期语义、基础投影 |
| 连接与粒度 | 多表连接、外连接、连接基数和结果粒度 |
| 聚合与指标 | 业务口径、分组、条件聚合、重复事实处理 |
| 时间与窗口 | 时间边界、分区排序、ROW_NUMBER/SUM/LAG |
| 复杂查询 | 相关子查询、NOT EXISTS、深层查询、CTE |
| 数据开发 | 数据质量、孤立维度、对账、多事实表预聚合 |

难度分布：3 easy、10 medium、5 hard。默认每题权重为题库中公开的 `weight`；模型总分按权重聚合，不按维度二次平均。

v1 的 12 题定义仍在公开证据中，用于解释历史运行，不能和 v2 分数直接横向比较。

## 4. 盲测 Prompt 合同

模型收到：

- DuckDB 方言说明；
- 发布时结构快照；
- 语义层实体、关系、指标、维度和业务规则；
- 当前自然语言问题；
- `query-plan-v1` JSON Schema。

模型不收到：

- `reference_sql`；
- 金标结果；
- `required_ast`；
- 评分权重；
- 题库目录、数据库路径或 Git 仓库路径。

每个实际 Prompt 原样保存。公平性审查应优先检查 Prompt 证据，而不是相信模板源码。

## 5. 输出协议

严格输出必须是单个 JSON 对象：

```json
{
  "plan": {
    "grain": "结果粒度",
    "sources": ["来源"],
    "joins": ["连接"],
    "filters": ["过滤"],
    "metrics": ["指标"],
    "steps": ["步骤"],
    "risks": ["风险"]
  },
  "sql": "SELECT ...",
  "summary": "简短说明",
  "assumptions": []
}
```

- 直接满足 JSON Schema：`protocol_strict=true`，协议项 5 分。
- 只允许恢复单个 ` ```json ` fence，且 fence 前后不能有其他文本；恢复后协议项 0 分。
- 缺字段、额外字段、类型错误、多 fence 或解释性前后缀：`output_contract_error`。

协议分只衡量输出契约，不表示 SQL 正确。

## 6. SQL 守卫

模型 SQL 必须：

- 能按 DuckDB 方言解析；
- 只有一条语句；
- 根节点是只读 Query；
- 不含写入、DDL、事务、附加数据库或危险 AST；
- 不调用外部读取/扫描函数；
- 只引用 `main` 中的发布表或查询自己的 CTE。

通过静态守卫后才会进入独立 DuckDB Worker。Worker 以只读模式打开固定仓库，关闭外部访问，限制 512 MB、单线程、UTC、5 秒和案例最大行数。

## 7. 结果归一化与比较

### 单元格

- `NULL` 保持独立类型。
- 整数、浮点和 Decimal 都转为 Decimal。
- 按案例 `decimal_scale` 使用 ROUND_HALF_UP 定标。
- 数值相等条件：

$$
|e-a| \le \max(\text{abs\_tolerance},\; \text{rel\_tolerance}\times\max(|e|,|a|))
$$

- 日期转 ISO 日期。
- 时间戳转 UTC，固定微秒格式。
- 字符串做 Unicode NFC；不会擅自 trim 内容。
- NaN 和 Infinity 拒绝。

### 列

1. 列数先比较。
2. 列名去 SQL 引用符并 casefold。
3. 名称集合一致时按金标列顺序重排实际结果。
4. 名称无法唯一对齐时尝试列值指纹唯一映射。
5. 指纹仍有多个解时返回 `column_alignment_ambiguous`，不猜测。

列被成功重排仍可能失去“列名完全相同”的 5 分，但行内容可以继续客观比较。

### 行

- `duplicate_policy` 固定为 `multiset`；重复行按次数计数。
- 无容差的完全相同多重集走快速路径。
- 有容差时构造行相等二分图，以 Hopcroft–Karp 风格最大匹配计算匹配数。
- `precision = matched / actual_count`。
- `recall = matched / expected_count`。
- `F1 = 2PR/(P+R)`。
- 若 `row_order_significant=false`，F1=1 即顺序项通过。
- 若 `row_order_significant=true`，必须逐行相等。

## 8. 单案例评分

| 项 | 满分 | 判定 |
| --- | ---: | --- |
| 严格输出协议 | 5 | `protocol_strict=true` |
| 只读 AST 守卫 | 5 | SQL 通过静态守卫 |
| 可执行 | 10 | Worker 成功返回结果 |
| 列数 | 5 | 与金标列数一致 |
| 列名 | 5 | 归一化列名及原顺序一致 |
| 行结果 | 45 | `45 × F1` |
| 顺序 | 10 | 按案例顺序合同通过 |
| SQL 能力 | 15 | 无规则时全部给分；有规则时按通过比例 |

$$
S_{case}=S_{protocol}+S_{guard}+S_{exec}+S_{column\ count}+S_{column\ names}+45F_1+S_{order}+S_{AST}
$$

总分四舍五入到两位。重要边界：

- SQL 解析/守卫失败时，仍可能保留严格协议 5 分，其余与 SQL 相关项为 0。
- SQL 通过守卫但执行失败时，可保留协议、守卫和已计算的 AST 能力分。
- Provider/输出合同失败且没有可评分 SQL 时，案例总分记 0。
- `SQL 能力` 不是代码风格分；只检查案例公开定义的结构能力规则。

## 9. 聚合

### 多次尝试

同一案例的尝试保存：

- mean；
- success rate：得分大于 0 的尝试比例；
- population stddev：分母为尝试数 $N$。

### 模型总分

先得到每个案例的尝试均值，再按案例权重计算：

$$
S_{model}=\frac{\sum_i w_i\bar S_i}{\sum_i w_i}
$$

分类和雷达维度使用同一加权方式。失败和取消案例不会从分母消失；其已落库分数（通常为 0）参与聚合，防止“只统计成功案例”。

### Token、费用与时间效率

正确性、Token、费用和时间分别报告，不合成一个难以解释的总分。为避免低分模型因“少做事”显得便宜，资源指标按“正确等价题”归一化：

$$
C_{eq}=\sum_i S_i/100
$$

$$
T_{\text{per correct}}=T_{\text{total}}/C_{eq},\quad \text{Cost}_{\text{per correct}}=\text{Cost}_{\text{total}}/C_{eq}
$$

固定比较指标：

- 官方正确性分：越高越好；
- Token / 正确等价题：越低越好；
- 估算费用 / 正确等价题：越低越好；
- 模型生成耗时 P95：越低越好；SQL 本地执行耗时单独披露，不混入模型响应时长。

Token 按未缓存输入、缓存输入、缓存写入、输出和推理输出分别保存；推理 Token 是输出的解释性子集，不重复计入总量。费用只根据模型配置中手工确认并在运行创建时冻结的 USD/百万 Token 价格快照估算。CLI 包月订阅不会伪装成单次实际账单；没有完整 Token 或单价时费用保持 `null`，同时报告覆盖题数。

## 10. 公平性分类

报告冻结并比较以下字段：

- `adapter_kind`
- `response_mode`
- `parameters`

分类：

- `single_model`：只有一个模型。
- `pure_model`：两个模型除请求模型身份外，上述路径字段一致。
- `access_path`：上述任一字段不同。

`pure_model` 仍不等于实验室意义上的完全控制：不同模型可能由 Provider 侧采用不同基础设施、系统 Prompt、采样实现或版本路由。报告只声明本应用能观测和冻结的控制变量。

## 11. 身份和版本证据

新运行保存：

- profile 显示名；
- 请求模型 ID；
- Provider/CLI 返回的解析模型 ID；
- Provider request ID；
- CLI 版本；
- 适配器参数和隔离配置；
- 生成耗时和 SQL 执行耗时；
- app/scorer/DuckDB/SQLGlot/output contract 版本。

0.2.0 之前的历史运行没有持久化的 Provider request ID 和生成耗时，公开报告保留 `null`。应用/评分器/DuckDB/SQLGlot 值按当时已知部署基线回填；显示名由迁移时仍关联的 profile 回填。这些字段是“可恢复历史元数据”，不应被描述为当时原生保存的证明。

## 12. 如何解释结果

可以据此回答：

- 在指定题库版本和调用路径下，哪个模型本次得分更高？
- 差异来自哪些案例、结果行、AST 能力或失败类型？
- 模型是否稳定遵守输出合同和只读边界？
- 同一快照精确复跑后是否得到相近结果？

不能据此单独回答：

- 哪个模型“普遍更聪明”？
- 差值是否具有统计显著性？
- 模型在其他语言、方言、行业数据和 Schema 规模下如何？
- 公开题库是否已进入模型训练数据？
- 没有完整 Token、价格或耗时覆盖时，哪个方案成本或延迟更优？

建议公开比较至少报告：题库哈希、运行 ID、日期、attempts、请求/解析模型 ID、适配器、参数、公平性分类、总分、失败数、Token/正确等价题、估算费用/正确等价题、生成耗时 P95、各指标覆盖率和证据链接。只贴排行榜截图不构成可复核报告。
