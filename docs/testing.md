# 测试策略与证据等级

## 1. 原则

测试只为可观察合同服务：

- 同样输入是否生成同样题库和金标？
- 危险 SQL 是否被拒绝？
- 结果比较是否正确处理类型、重复行、容差和顺序？
- Provider 记录格式是否按预期解析？
- 运行状态、取消、续传、复跑和报告是否端到端一致？
- 公开证据是否脱敏且能检测篡改？
- UI 的关键交互是否使用正确后端证据？

不测试纯实现细节、源文本存在性或无行为意义的 getter。

## 2. 一键验证

```bash
./script/verify.sh
```

脚本依次执行：

1. Ruff；
2. mypy strict；
3. Pytest；
4. 空 SQLite 的 Alembic `upgrade head`；
5. 已提交证据摘要校验；
6. ESLint；
7. TypeScript build typecheck；
8. Vitest；
9. Vite production build。

任何一步失败即退出非零。

## 3. 后端测试矩阵

### `tests/test_retail_suite.py`

保护：

- 相同源生成相同 content hash 和 lock；
- v2 恰好 18 题、六维均衡和难度分布；
- 雷达维度是可版本化字符串，历史维度可重建；
- 固定表基数和专用边界数据存在；
- 所有参考 SQL 与提交的 lock 完全一致；
- 18 个参考查询完整评分为 100。

未证明：模型能解这些题。

### `tests/test_suites.py`

保护：

- 发布构建可复现；
- Schema/Seed/semantic/cases 变化会改变哈希；
- 结构快照包含实际外键和语义关系；
- Prompt 包含问题、结构、语义和输出合同；
- Prompt 不含 reference SQL、金标和 AST 评分规则；
- 非确定性/危险 Seed、错误参考 SQL 和非法合同被拒绝。

未证明：公开题库不会被模型记忆。

### `tests/test_result_compare.py`

保护：

- Decimal 定标和容差；
- NULL、布尔、日期、UTC 时间戳和 Unicode NFC；
- 重复行按多重集处理；
- 列名大小写/引用符与列重排；
- 指纹映射唯一性和歧义失败；
- 有序/无序结果；
- 空结果 precision/recall/F1 边界；
- 金标和实际摘要稳定。

未证明：比较器支持任意数据库私有类型；执行方言仅 DuckDB。

### `tests/test_sql_evaluator.py`

保护：

- 单语句、只读 Query、表白名单和 CTE；
- 外部函数、未知 schema、写入和多语句拒绝；
- Worker 超时、行数上限和错误映射；
- 窗口、相关子查询、NOT EXISTS、CTE、条件聚合和预聚合 AST 规则；
- 100 分固定公式及部分失败得分；
- 案例权重、attempt mean/success/stddev 和结论生成。

未证明：SQLGlot 或 DuckDB 不存在未知漏洞；因此实现还保留运行时只读和 external access 双层防守。

### `tests/test_adapters.py`

保护：

- loopback HTTP 不继承环境代理；
- Codex JS launcher 只解析唯一原生二进制；
- Codex Seatbelt 默认拒绝、用户主目录边界、项目显式拒绝、认证/案例目录精确放行；
- 录制的 Codex、Claude、Gemini JSONL 和 OpenAI SSE 解析；
- 只恢复单一 JSON fence；
- Gemini 临时设置仅保留认证选择并禁用 skills；
- Gemini 工具调用判策略违规；
- 递归密钥脱敏；
- CLI 1 MiB 输出上限；
- CLI 超时会终止进程组。

此外，发布前执行真实 macOS `sandbox-exec` smoke：

- 案例临时文件可读；
- 仓库文件读取返回非零；
- 当前 Codex 原生二进制在同一策略下 `--version` 可启动。

未证明：录制夹具等于所有未来 CLI 版本；健康检查和历史 CLI 版本快照用于发现漂移。

### `tests/test_api_integration.py`

使用 FastAPI TestClient、真实 SQLite、真实题库构建、真实运行引擎和真实评分器。模型层换成 FixtureAdapter。

保护：

- 创建/健康检查模型配置；
- Prompt preview；
- 双模型完整状态机；
- 成功模型 18 题真实执行和 100 分链路；
- 失败模型、错误码和脱敏；
- `run.started` 与有 model ID 的 `model.started` 事件语义；
- 事件严格递增、history `after_seq` 和 SSE 续传；
- 默认隐藏和显式揭示 reference/gold；
- 生成耗时与 Provider request ID 落库；
- `run-report-v1` 协议版本和公平性；
- profile 改名后历史报告仍使用名称快照；
- exact rerun 复制原模型和题库快照；
- 取消到终态；
- Host、Origin 和 CSRF 拒绝；
- 全量证据导出、路径/密钥脱敏、摘要验真和篡改检测。

关键限制：FixtureAdapter 直接返回每题参考 SQL。它验证编排、执行、比较、评分、事件和报告链路，不是外部模型能力证明。真实模型能力只能由 `evidence/runs/` 中的实际 Provider/CLI 运行支持。

## 4. 前端测试矩阵

`frontend/src/test/arena.test.tsx` 使用 Vitest、jsdom、Testing Library 和 mock API。

保护：

- 新建运行的健康门禁和公平性提示；
- SSE 按最后 `seq` 重连且不重复消费；
- 演示模式只写当前 tab 的 `sessionStorage`；
- SQL 工作区把 plan、SQL、固定金标和实际结果放在正确位置；
- 运行中取消调用正确 API 并刷新状态；
- 旧事件缺失 payload 时页面仍可渲染。

未覆盖：

- 真实浏览器布局、字体加载和 SVG 图表；
- 浏览器与真实 FastAPI 的完整写操作；
- 多浏览器兼容；
- 触屏和辅助技术完整流程。

因此不能称为“自动化 E2E”。发布前需要真实浏览器 smoke。

## 5. 真实浏览器 smoke

至少验证：

1. `GET /api/health` 返回 0.2.0；
2. 首页/历史页加载，18 次公开前历史运行仍可打开；
3. Run 18 报告显示 Luna 95.09、Sol 92.04，并标为 `pure_model`；
4. 案例工作区默认隐藏 Reference，点击后能加载金标；
5. 实时日志筛选和历史事件加载正常；
6. 生产构建没有浏览器 console error；
7. 404 前端路径回退到 SPA。

Smoke 只确认被操作的路径，不替代自动化合同测试。

### 0.2.0 开源候选实测结果

- `/api/health`：HTTP 200，`{status: "ok", version: "0.2.0"}`。
- 1440px Chromium：`/runs/new`、`/benchmarks`、`/models`、`/runs/18/live`、`/runs/18/report` 均加载；没有 console warning/error、page error 或横向溢出。
- 新建对局页首次视觉检查发现卡片使用 `model-card-foot`、CSS 仍指向已删除的 `model-meta`；迁移样式并重建后，状态徽标与响应模式为 flex 布局、间距 10px，截图确认不再贴连。
- Run 18 报告：Luna 95.09、Sol 92.04、`pure_model`；总分、雷达和热力图实际渲染；APP 0.1.0 是该历史运行快照，不是当前服务版本。
- Live 工作区：293 条持久化事件可见；`case.failed` 服务端筛选得到 1 条；案例 08 默认隐藏 Reference，显式揭晓后返回参考 SQL、12 行金标和 digest。
- 未知前端路径 HTTP 200 返回 SPA，并由客户端重定向到 `/runs/new`。
- 1024px、768px 无横向溢出；390px 会横向滚动到 631px，因此手机布局明确不在 0.2.0 支持范围。
- 没有在 smoke 中创建新运行或点击 exact rerun：这会调用真实外部模型并改变历史状态；创建、取消和复跑合同由 API 集成测试覆盖，真实 Provider 行为由已公开的 Runs 1–18 支持。

## 6. 迁移验证

空库验证必须使用临时 `LLM_TEST_VAR_DIR` 和 `LLM_TEST_DATABASE_URL`，执行：

```bash
uv run alembic upgrade head
```

随后确认：

- `alembic_version = c4e8b9f7a102`；
- `comparison_runs` 有五个版本快照字段；
- `model_runs` 有 `profile_name_snapshot`；
- `case_runs` 有 `generation_ms` 和 `provider_request_id`。

现有早期数据库还要验证兼容路径：列已被 `ensure_schema()` 添加时，迁移可幂等跳过并正确盖章。

## 7. 证据验证

```bash
uv run python -m backend.app.cli verify-evidence --input evidence
```

当前公开基线应报告：

```json
{"suite_count": 2, "run_count": 18}
```

还应执行独立文本扫描，确保没有：

- `/Users/<name>/` 或 `/home/<name>/`；
- GitHub、OpenAI、Google 已知密钥前缀；
- 测试密钥 `private-token`；
- `.env`、SQLite、WAL/SHM 或 CLI 认证文件。

摘要验证保护“导出后未变化”，文本扫描保护“导出内容可公开”。两者不能互相替代。

## 8. 质量门禁

| 门禁 | 失败含义 |
| --- | --- |
| Ruff | Python 风格、导入、常见 bug/async 问题 |
| mypy strict | 后端和测试存在类型不确定性 |
| Pytest | 核心领域/API 合同回归 |
| Alembic empty upgrade | 新安装无法建库或迁移头不完整 |
| Evidence verifier | 历史证据缺失、增加或字节变化 |
| ESLint | React hooks 或前端静态规则回归 |
| TypeScript | API/UI 类型合同不一致 |
| Vitest | 关键前端交互回归 |
| Vite build | 生产前端无法生成或引用失败 |

## 9. 依赖和安全检查

发布前额外执行：

```bash
cd frontend
pnpm audit --prod
```

Python 依赖由 `uv.lock` 固定，前端由 `pnpm-lock.yaml` 固定。依赖漏洞扫描只能发现已登记 CVE，不证明应用安全；Host/Origin/CSRF、CLI 沙箱、SQL 双层守卫和脱敏仍需行为测试。

## 10. 证据等级

从强到弱：

1. 哈希锁定的真实运行逐案例证据；
2. 真实程序/浏览器/Seatbelt smoke 输出；
3. 使用真实核心服务的 API 集成测试；
4. 领域单元/属性边界测试；
5. 录制 Provider 协议夹具；
6. 前端 mock 组件测试；
7. 文档或源码静态声明。

对外结论必须标明它依赖哪一级。尤其不能用 FixtureAdapter 的 100 分替代真实模型结果。
