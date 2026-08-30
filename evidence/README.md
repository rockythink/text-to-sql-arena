# Evidence

本目录是 SQL 擂台的公开、脱敏、哈希锁定证据，不是原始数据库备份。

- [`index.json`](index.json)：2 个已发布题库、18 次历史运行的根索引。
- [`suites/`](suites/)：题库源、结构、金标和构建清单。
- [`runs/`](runs/)：每次运行的完整报告、事件 JSONL 和逐案例证据。
- [`../docs/evidence.md`](../docs/evidence.md)：目录合同、脱敏规则、摘要算法和第三方复核步骤。
- [`../docs/historical-runs.md`](../docs/historical-runs.md)：人类可读的历史导航。

校验：

```bash
uv run python -m backend.app.cli verify-evidence --input evidence
```

预期：

```json
{"suite_count": 2, "run_count": 18}
```

注意：`README.md` 是说明文件，不属于任何 suite/run bundle，因此不会影响这些 bundle 的 `bundle_sha256`；根校验器只校验 `index.json` 登记的 bundle。
