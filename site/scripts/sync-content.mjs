import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const siteRoot = resolve(scriptDir, "..");
const repoRoot = resolve(siteRoot, "..");
const docsTarget = resolve(siteRoot, "src/content/docs/docs");

const docs = [
  ["capabilities.md", "能力清单", "从题库、模型接入到可观测性与证据导出的完整能力边界。", 1],
  ["architecture.md", "架构与信任边界", "本地应用的数据流、隔离策略、持久化边界与威胁模型。", 2],
  ["methodology.md", "评测方法", "固定题库、确定性执行、评分公式与结果解释边界。", 3],
  ["contracts.md", "数据、API、事件与证据合同", "运行快照、HTTP/SSE 接口、事件版本和公开证据格式。", 4],
  ["testing.md", "测试策略与证据等级", "自动化测试、真实浏览器验收与不能推出的结论。", 5],
  ["historical-runs.md", "历史运行总览", "18 次公开运行的状态、模型、版本和结果导航。", 6],
  ["evidence.md", "证据导出与验真", "公开证据目录、脱敏规则、摘要算法和离线复核步骤。", 7],
  ["open-source-audit.md", "完整开源审计", "源码、依赖、隐私、历史数据和公开发布的逐项审计。", 8]
];

await rm(resolve(siteRoot, "src/content/docs"), { recursive: true, force: true });
await mkdir(docsTarget, { recursive: true });

const overview = `---
title: 项目总览
description: SQL 擂台是一套本地优先、结果可复核的 Text-to-SQL 模型评测系统。
sidebar:
  order: 0
---

SQL 擂台把题库、确定性 DuckDB 数据、模型调用、SQL 安全执行、结果比较、评分和静态证据放在同一条可审计链路中。

- 浏览 [能力清单](./capabilities/)，确认系统实际实现和明确不做的事情。
- 阅读 [评测方法](./methodology/)，理解 100 分公式、控制变量和统计边界。
- 查看 [历史运行](../runs/)，进入每次运行的模型、逐题得分和原始证据。
- 使用 [证据验真](./evidence/) 在本地复核 2 套题库和 18 次运行。

> 这不是一个公网竞赛服务。它是作者在本机运行、对外公开方法与证据的研究工具。
`;
await writeFile(resolve(docsTarget, "index.md"), overview);

for (const [file, title, description, order] of docs) {
  const source = await readFile(resolve(repoRoot, "docs", file), "utf8");
  let body = source.replace(/^# .+?\n+/, "");
  body = body.replace(/\]\(\.\.\/evidence\/([^)]+)\)/g, (_, target) => {
    const view = target.endsWith("/") ? "tree" : "blob";
    return `](https://github.com/rockythink/text-to-sql-arena/${view}/main/evidence/${target})`;
  });
  body = body.replace(/\]\((?:\.\/)?([a-z0-9-]+)\.md(#[^)]*)?\)/gi, (_, slug, anchor = "") => `](../${slug}/${anchor})`);
  const frontmatter = `---\ntitle: ${title}\ndescription: ${description}\nsidebar:\n  order: ${order}\n---\n\n`;
  await writeFile(resolve(docsTarget, file), frontmatter + body);
}
