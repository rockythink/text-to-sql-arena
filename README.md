# SQL 擂台

本地优先、可复核的 Text-to-SQL 模型评测应用。它把题库、确定性 DuckDB 数据、模型调用、SQL 安全执行、结果比较、评分、实时事件和静态证据包放在同一条可审计链路中。

当前版本：0.4.0；当前题库 v4 / scorer 2.0.0。v3 三轮诊断发现累计消费口径问题，v4 已澄清；不把 v3 成绩冒充 v4 实测。旧题库与已发布历史成绩保持不变。

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
| 模型接入 | 新配置统一使用固定 `@earendil-works/pi-ai` 0.85.1 低层调用；GPT 订阅走 `openai-codex` OAuth，API 接入可选 OpenAI/Anthropic/Google 或自定义 Provider |
| 公平性 | 新运行固定 Pi、文本响应、单轮、无工具、无重试和统一 Prompt；冻结并披露 Provider、认证、模型、参数与隔离控制，不把“同一框架”写成“同端点/同模型” |
| 结构化输出 | `query-plan-v1`：`plan`、`sql`、`summary`、`assumptions` 四个必填字段 |
| SQL 安全 | SQLGlot 单语句解析、只读 AST、表白名单、外部访问函数拒绝；独立进程只读执行、超时、行数和内存上限 |
| 结果比较 | 列名归一化/重排、全精度 Decimal、整数列精确比较、UTC 时间、Unicode NFC、NULL、多重集、唯一容差列对齐与顺序语义 |
| 评分 | 业务结果正确率为主，格式另报；政策拒绝与执行错误不冒充业务错解；保留综合分作辅助，分母包含全部计划尝试 |
| 资源效率 | 新 efficiency-v2 按实际正确题归一，Pi 缓存输入不重复扣减；历史 v1 得分折算仅按原口径展示，缺价/缺数据不估算 |
| 运行控制 | 1–6 个 Pi 模型、每题固定 1 次尝试、只读预检、浏览器评测方案、取消、精确/当前配置复跑、关联失败/未满分补跑；旧配置仅供历史查看和删除 |
| 观赛与复核 | 分层报告、关键回合、真实历史逐题回放、本机匿名竞猜；控制项一致后才做逐题结果回归比较 |
| 证据发布 | 单场脱敏预览与摘要确认后下载 ZIP；CLI 全量导出仍可用；导出不等于部署上线 |
| 本地安全 | 默认仅绑定 loopback；Host/Origin/CSRF 校验；Keychain/环境变量密钥引用；事件和公开证据脱敏 |

完整清单见 [docs/capabilities.md](docs/capabilities.md)。

## 快速开始

### 环境

- macOS 或 Linux；新运行通过仓库内 Pi Node bridge 调用，不依赖 coding-agent CLI；
- Python `>=3.12,<3.13`；
- [uv](https://docs.astral.sh/uv/)；
- Node.js `>=22.19`；
- pnpm 10；
- 现代浏览器；本地工作台已在 1440px、768px、390px 宽实际验证；复杂 SQL 编辑建议桌面；
- GPT 订阅 OAuth 需先通过支持的外部 Pi CLI 登录或既有 Codex 登录取得凭据；API Provider 使用 API Key 或环境变量引用。

### 安装与构建

```bash
uv sync --frozen
pnpm --dir runtime/pi install --frozen-lockfile --ignore-scripts
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
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

1. 在“参赛模型”创建新的 Pi 配置：GPT 订阅选择 `openai-codex` + OAuth（本地 catalog 提供 `gpt-5.6-luna` / `gpt-5.6-sol`）；API 模型选择 OpenAI、Anthropic、Google 或填写自定义 Provider。点击“检查本地配置”只核对 catalog、凭据和参数，不调用模型，也不证明 Provider 可用。OAuth 凭据可来自支持的外部 Pi CLI 登录或既有 Codex 登录文件；应用只导入凭据到系统钥匙串，不读取配置、工具或会话。
2. 在“赛题实验室”选择已发布版本，或克隆为草稿后修改并发布；可用固定数据变体检查典型错解。
3. 在“开赛与往期”选择 Pi 模型和题目；新运行固定每题单次调用，可保存本浏览器方案。旧适配器不会进入新运行。只读预检通过后才能开赛，不虚构耗时或费用。
4. 在实时页观察业务题意、Prompt、Provider、SQL、比较和评分事件；历史运行不冒充直播。
5. 报告分“看比赛 / 看门道 / 查证据”：看关键回合，再核对质量指标与 SQL 证据，最后比较冻结合同和导出材料。
6. 复测可选择精确快照或当前配置、全部题或失败/未满分子集。新运行关联原记录，不覆盖旧结果。
7. 对终态运行先预览脱敏材料，再确认下载单场发布包。上线仍是独立、明确授权的操作。

## 公开证据站点

Cloudflare Pages：<https://arena.ss-data.cc/>。

公开站点与本地 Web App 是两个独立产品。本地 Web App 用于实际评测操作和录屏；公开站点只用于发布经过明确选择的证据材料。新增本地运行不会自动同步或发布，只有明确指定某次报告后才更新公开内容。

对外内容严格限定为：

- 评测报告；
- 支持报告结论的原始证据与校验摘要；
- 报告实际使用的测试用例及不可变版本；
- 评测方法、评分公式和结果解释边界。

不对外发布本地工作台的操作手册、模型接入配置、系统架构、内部 API/事件合同、运维流程或实现清单。

站点内容来源：

- `docs/methodology.md` 与 `docs/evidence.md` 提供评测方法和证据验真正文；
- `evidence/` 提供被明确选中发布的测试用例、运行报告和报告证据；
- `site/` 只负责索引、可视化和导航，不复制或改写证据结论。

本地预览：
```bash
cd site
pnpm install --frozen-lockfile
pnpm dev
```

执行 `pnpm check && pnpm build && pnpm verify:build` 会生成公开边界内的正文与证据页面并检查内部链接。该命令只构建本地静态产物，不部署站点。

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
- 默认一次尝试是观察值，不是统计显著性结论；多次尝试查看全部作答、均值、标准差与结果正确率，非零得分率不等于正确率。
- 题库源码和金标公开，便于复核，但也意味着长期公开榜单可能受到训练污染或记忆影响。
- 不同适配器、响应模式或参数时，报告会标记为“接入路径比较”，不能把差异全部归因于模型。
- 自动化测试中的 FixtureAdapter 使用参考 SQL验证状态机和评分链路，不是模型质量证据。

## 许可证

除文件另有声明外，本仓库原创代码、测试、文档、题库、历史报告、运行日志与证据均按 [Mozilla Public License 2.0](LICENSE) 授权。第三方依赖和字体保留各自许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
