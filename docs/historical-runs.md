# 历史运行总览

导出时间：2026-08-29 UTC。权威索引为 [`evidence/index.json`](../evidence/index.json)。本页只做导航，不替代逐案例证据。

## 题库版本

| DB 版本 ID | 版本 | 案例数 | 内容哈希 | 证据 |
| ---: | ---: | ---: | --- | --- |
| 1 | v1 | 12 | `0a4a18b4374f510f5eff18b06272c30c3375e1f082ae405adc8ead7dd9c81556` | [suite](../evidence/suites/0a4a18b4374f510f5eff18b06272c30c3375e1f082ae405adc8ead7dd9c81556/) |
| 2 | v2 | 18 | `5b5d98876ea35114f18ce6dfa48cc9800d88b6baba80d311b2f52552a38b31af` | [suite](../evidence/suites/5b5d98876ea35114f18ce6dfa48cc9800d88b6baba80d311b2f52552a38b31af/) |

## 18 次运行

| Run | 题库 | 运行状态 | 模型结果 | 报告 | 日志 |
| ---: | --- | --- | --- | --- | --- |
| 1 | v1 | failed | Sol 0.00（failed） | [report](../evidence/runs/run-0001/report.json) | [events](../evidence/runs/run-0001/events.jsonl) |
| 2 | v1 | failed | Gemini 0.00（failed） | [report](../evidence/runs/run-0002/report.json) | [events](../evidence/runs/run-0002/events.jsonl) |
| 3 | v1 | failed | Sol 0.00（failed） | [report](../evidence/runs/run-0003/report.json) | [events](../evidence/runs/run-0003/events.jsonl) |
| 4 | v1 | failed | Gemini 0.00（failed） | [report](../evidence/runs/run-0004/report.json) | [events](../evidence/runs/run-0004/events.jsonl) |
| 5 | v1 | failed | Sol 0.00（failed） | [report](../evidence/runs/run-0005/report.json) | [events](../evidence/runs/run-0005/events.jsonl) |
| 6 | v1 | failed | Sol 0.00（failed） | [report](../evidence/runs/run-0006/report.json) | [events](../evidence/runs/run-0006/events.jsonl) |
| 7 | v1 | failed | Sol 0.00（failed） | [report](../evidence/runs/run-0007/report.json) | [events](../evidence/runs/run-0007/events.jsonl) |
| 8 | v1 | failed | Sol 0.00（failed） | [report](../evidence/runs/run-0008/report.json) | [events](../evidence/runs/run-0008/events.jsonl) |
| 9 | v1 | completed | Sol 100.00 | [report](../evidence/runs/run-0009/report.json) | [events](../evidence/runs/run-0009/events.jsonl) |
| 10 | v1 | completed | Sol 100.00 | [report](../evidence/runs/run-0010/report.json) | [events](../evidence/runs/run-0010/events.jsonl) |
| 11 | v1 | completed_with_errors | Sol 89.79；Gemini 0.00（failed） | [report](../evidence/runs/run-0011/report.json) | [events](../evidence/runs/run-0011/events.jsonl) |
| 12 | v1 | completed_with_errors | Sol 98.12；Gemini 0.00（failed） | [report](../evidence/runs/run-0012/report.json) | [events](../evidence/runs/run-0012/events.jsonl) |
| 13 | v1 | cancelled | Sol 50.00（cancelled）；Gemini 0.00（failed） | [report](../evidence/runs/run-0013/report.json) | [events](../evidence/runs/run-0013/events.jsonl) |
| 14 | v1 | completed | Sol 98.12 | [report](../evidence/runs/run-0014/report.json) | [events](../evidence/runs/run-0014/events.jsonl) |
| 15 | v1 | cancelled | Sol 8.33（cancelled） | [report](../evidence/runs/run-0015/report.json) | [events](../evidence/runs/run-0015/events.jsonl) |
| 16 | v2 | completed_with_errors | GPT 当前会话桥接（流程验收）87.31 | [report](../evidence/runs/run-0016/report.json) | [events](../evidence/runs/run-0016/events.jsonl) |
| 17 | v2 | completed | Luna 100.00 | [report](../evidence/runs/run-0017/report.json) | [events](../evidence/runs/run-0017/events.jsonl) |
| 18 | v2 | completed_with_errors | Luna 95.09；Sol 92.04 | [report](../evidence/runs/run-0018/report.json) | [events](../evidence/runs/run-0018/events.jsonl) |

每个 Run 目录还包含：

- `cases/`：全部 CaseRun JSON；
- `bundle-manifest.json`：逐文件摘要和整包摘要。

## Run 18：当前双模型比较

- 题库：v2，18 题；
- attempts：1；
- 公平性：`pure_model=true`，`differences=[]`；
- 适配器：两者均为 `codex_cli`；
- CLI：两者均记录 `codex-cli 0.147.0`；
- Luna 请求/解析模型：`gpt-5.6-luna`；
- Sol 请求/解析模型：`gpt-5.6-sol`；
- Luna：95.09，模型状态 completed；
- Sol：92.04，模型状态 completed_with_errors；
- 顶层运行状态：completed_with_errors；
- 报告结论冠军：Luna。

这仍是一次尝试的观察值，不是统计显著性结论。逐案例得分、错误、Prompt、原始输出、SQL 和结果差异以 [Run 18 报告](../evidence/runs/run-0018/report.json) 与 `cases/` 为准。

## 历史证据应如何解释

### 失败运行不删除

Run 1–8 的失败、Run 13/15 的取消和其他 `completed_with_errors` 都属于系统演进证据。保留它们可以复核：

- Provider/CLI 协议失败；
- 沙箱或认证问题；
- 输出合同问题；
- SQL 守卫/执行问题；
- 取消与状态恢复行为。

只公开成功运行会造成幸存者偏差。

### Run 16 不是通用模型排名

名称已经明确标注“当前会话桥接（流程验收）”，适配器是 `openai_compatible`，请求模型 ID 为 `gpt-session-bridge`。它用于验证流程，不应和正式 Provider 模型身份等同。

### v1 与 v2 不直接比较

两版案例数、雷达维度和案例内容不同。跨版本只能分析共同案例或系统演进，不能直接把 v1 总分和 v2 总分作为同一排行榜。

### 0.2.0 前元数据限制

这些运行在 0.1.0 时创建。0.2.0 增加了不可变应用/评分器/依赖/profile 名称快照和 CaseRun Provider 元数据：

- app/scorer/DuckDB/SQLGlot/output contract 按当时已知部署基线回填；
- profile 名称由迁移时仍关联的配置回填；
- 当时没有保存的 `provider_request_id` 和 `generation_ms` 保持 `null`。

报告不会为了“看起来完整”而伪造不可恢复字段。

## 验真

```bash
uv run python -m backend.app.cli verify-evidence --input evidence
```

预期：2 个 suite bundle、18 个 run bundle 全部摘要一致。任何报告、事件或案例文件发生一个字节变化都会导致校验失败。
