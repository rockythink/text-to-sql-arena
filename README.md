# SQL 擂台

本地优先、可复核的 Text-to-SQL 模型评测应用。它把题库、确定性 DuckDB 数据、模型调用、SQL 安全执行、结果比较、评分、实时事件和静态证据包放在同一条可审计链路中。

当前版本：`0.3.0`。

## 它解决什么问题

普通 Text-to-SQL 演示往往只展示一条“看起来能跑”的 SQL。SQL 擂台要求每个结论都能回到以下证据：

- 模型实际收到的完整 Prompt；
- 模型原始输出与结构化查询规划；
- 解析、只读守卫、执行与结果比较事件；
- 固定金标结果和逐行差异；
- 不可变的题库内容哈希、运行配置和工具版本快照；
- 可离线校验 SHA-256 的报告、日志和案例证据包。

## 核心能力

| 能力 | 当前实现 |
| --- | --- |
| 题库编写 | 在 Web UI 创建草稿、克隆版本、编辑 Schema/Seed/Semantic/Prompt/Cases、预览 Prompt、发布不可变版本 |
| 确定性数据 | DuckDB 1.5.5、UTC、固定 SQL 种子、单线程金标构建、内容寻址产物 |
| 模型接入 | OpenAI-compatible HTTP、Codex CLI、Claude CLI、Gemini CLI |
| 公平性 | 运行前健康检查；冻结适配器、模型 ID、参数、价格、CLI 版本、隔离配置；区分纯模型比较和接入路径比较 |
| 结构化输出 | `query-plan-v1`：`plan`、`sql`、`summary`、`assumptions` 四个必填字段 |
| SQL 安全 | SQLGlot 单语句解析、只读 AST、表白名单、外部访问函数拒绝；独立进程只读执行、超时、行数和内存上限 |
| 结果比较 | 列名归一化/重排、Decimal 定标、UTC 时间、Unicode NFC、NULL、重复行多重集、容差匹配、顺序语义 |
| 评分 | 100 分固定公式：协议 5、只读 5、执行 10、列 10、行 F1 45、顺序 10、SQL 能力 15 |
| 资源效率 | `efficiency-v1`：Token、价格快照估算费用、生成/执行时长、正确等价题归一化及覆盖率 |
| 运行控制 | 双模型或单模型、1–3 次尝试、取消、启动恢复、精确复跑、按当前配置复跑 |
| 可观测性 | 持久化事件序列、SSE 断线续传、服务端筛选、虚拟列表、案例工作区、最终报告 |
| 证据发布 | 导出全部已发布题库和全部历史运行；脱敏；逐文件和整包 SHA-256；离线验真 |
| 本地安全 | 默认仅绑定 loopback；Host/Origin/CSRF 校验；Keychain/环境变量密钥引用；事件和公开证据脱敏 |

完整清单见 [docs/capabilities.md](docs/capabilities.md)。

## 快速开始

### 环境

- macOS 或 Linux；Codex CLI 适配器当前要求 macOS `sandbox-exec`；
- Python `>=3.12,<3.13`；
- [uv](https://docs.astral.sh/uv/)；
- Node.js 22+；
- pnpm 10；
- 现代桌面浏览器；工作台已验证到 768px 宽，390px 手机布局不在支持范围；
- 按需安装并登录 `codex`、`claude` 或 `gemini` CLI。

### 安装与构建

```bash
uv sync --frozen
cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..
```

`pnpm build` 将生产前端写入 `backend/app/static/`，FastAPI 直接提供这套静态资源。

### 启动

```bash
uv run python -m backend.app.cli serve
```

浏览器打开 <http://127.0.0.1:8000>。

可选环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_TEST_HOST` | `127.0.0.1` | 非 loopback 地址还必须显式设置 `LLM_TEST_ALLOW_LAN=1` |
| `LLM_TEST_PORT` | `8000` | HTTP 端口 |
| `LLM_TEST_VAR_DIR` | `./var` | SQLite、DuckDB 产物和临时运行数据目录 |
| `LLM_TEST_DATABASE_URL` | `sqlite+aiosqlite:///.../var/app.db` | SQLAlchemy 数据库 URL |
| `LLM_TEST_ALLOW_LAN` | 未设置 | 设为 `1` 才允许非 loopback 绑定；应用仍不是多用户服务 |

## 运行一次评测

1. 在“模型”页创建配置，按需填写 USD/百万 Token 价格，并执行健康检查。
2. 在“题库”页选择已发布版本，或克隆为草稿后修改并重新发布。
3. 在“新建对局”页选择一个或两个模型、题目和尝试次数。
4. 在实时页观察 Prompt、Provider、SQL、比较和评分事件。
5. 在报告页查看正确性、Token/正确等价题、估算费用/正确等价题、生成时长 P95、六维能力和逐题证据。
6. 对需要复核的运行执行“精确复跑”；它复用原运行快照，不读取已变化的模型配置。

## 文档与证据站点

Cloudflare Pages：<https://arena.ss-data.cc/>。

站点从仓库内现有材料静态生成：

- `docs/` 是方法、合同和审计文档的唯一正文源；
- `evidence/` 是题库、运行报告和摘要的唯一数据源；
- `site/` 只负责索引、可视化和导航，不复制或改写证据结论。

本地预览：

```bash
cd site
pnpm install --frozen-lockfile
pnpm dev
```

执行 `pnpm check && pnpm build && pnpm verify:build` 会先同步正文、从证据索引生成全部静态页面并检查内部链接。`.github/workflows/docs.yml` 只负责持续验证；公开站点由 Cloudflare Pages 托管。

## 公开证据

仓库内 `evidence/` 包含：

- 2 个已发布题库版本；
- 18 次历史运行；
- 264 个文本证据文件；
- 每次运行的报告、完整事件 JSONL、逐案例 Prompt/原始输出/SQL/结果/评分；
- 每个题库的源文件、金标结果和构建清单；
- 每个目录的 `bundle-manifest.json`。

校验：

```bash
uv run python -m backend.app.cli verify-evidence --input evidence
```

重新从本地数据库导出全部证据：

```bash
uv run python -m backend.app.cli export-evidence --output evidence
```

导出是全量替换；输出会去除已知密钥模式、项目绝对路径、用户主目录和临时目录。原始 `var/app.db`、CLI Home 和二进制 DuckDB 仓库不会进入公开仓库。

历史总览见 [docs/historical-runs.md](docs/historical-runs.md)，证据格式见 [docs/evidence.md](docs/evidence.md)。

## 验证

```bash
./script/verify.sh
```

也可以分开执行：

```bash
uv run ruff check .
uv run mypy backend tests
uv run pytest -q

cd frontend
pnpm lint
pnpm typecheck
pnpm test --run
pnpm build
```

测试覆盖边界和不能推出的结论见 [docs/testing.md](docs/testing.md)。

## 文档

- [能力清单](docs/capabilities.md)
- [架构与信任边界](docs/architecture.md)
- [评测方法](docs/methodology.md)
- [数据、API、事件与证据合同](docs/contracts.md)
- [测试策略与证据等级](docs/testing.md)
- [历史运行总览](docs/historical-runs.md)
- [证据导出与验真](docs/evidence.md)
- [开源审计](docs/open-source-audit.md)
- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)
- [第三方声明](THIRD_PARTY_NOTICES.md)

## 仓库结构

```text
backend/app/
  adapters/       模型接入和 CLI 隔离
  api/            FastAPI HTTP/SSE 接口
  data/           内置 retail-analytics 题库源
  services/       题库构建、运行引擎、比较、评分、报告、证据导出
  static/         可复现构建的生产前端
frontend/         React + TypeScript UI
alembic/          SQLite 迁移
tests/            后端合同与集成测试
evidence/         脱敏、哈希锁定的公开历史证据
docs/             方法、合同、审计和能力文档
site/             Astro + Starlight 静态文档与证据展示层
```

## 结果解释警告

- 分数只描述指定题库、Prompt、适配器、模型版本和运行时间下的行为，不代表通用 SQL 能力。
- 默认一次尝试的运行是观察值，不是统计显著性结论；稳定性比较应使用 2–3 次尝试并查看均值、成功率和标准差。
- 题库源码和金标公开，便于复核，但也意味着长期公开榜单可能受到训练污染或记忆影响。
- 不同适配器、响应模式或参数时，报告会标记为“接入路径比较”，不能把差异全部归因于模型。
- 自动化测试中的 FixtureAdapter 使用参考 SQL验证状态机和评分链路，不是模型质量证据。

## 许可证

除文件另有声明外，本仓库原创代码、测试、文档、题库、历史报告、运行日志与证据均按 [Mozilla Public License 2.0](LICENSE) 授权。第三方依赖和字体保留各自许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
