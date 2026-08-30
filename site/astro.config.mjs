// @ts-check
import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import sitemap from "@astrojs/sitemap";

const site = process.env.SITE_URL ?? "https://arena.ss-data.cc";
const base = process.env.SITE_BASE ?? "/";

export default defineConfig({
  site,
  base,
  trailingSlash: "always",
  integrations: [
    sitemap(),
    starlight({
      title: "SQL 擂台",
      description: "可复核的 Text-to-SQL 模型评测、方法与公开证据。",
      favicon: "/favicon.svg",
      customCss: ["./src/styles/starlight.css"],
      social: [
        { icon: "github", label: "GitHub", href: "https://github.com/rockythink/text-to-sql-arena" }
      ],
      editLink: {
        baseUrl: "https://github.com/rockythink/text-to-sql-arena/edit/main/docs/"
      },
      lastUpdated: true,
      sidebar: [
        { label: "项目总览", slug: "docs" },
        {
          label: "产品与架构",
          items: [
            { label: "能力清单", slug: "docs/capabilities" },
            { label: "架构与信任边界", slug: "docs/architecture" }
          ]
        },
        {
          label: "评测方法",
          items: [
            { label: "评测方法", slug: "docs/methodology" },
            { label: "数据与事件合同", slug: "docs/contracts" },
            { label: "测试策略", slug: "docs/testing" }
          ]
        },
        {
          label: "公开证据",
          items: [
            { label: "历史运行", slug: "docs/historical-runs" },
            { label: "证据格式", slug: "docs/evidence" },
            { label: "开源审计", slug: "docs/open-source-audit" }
          ]
        }
      ]
    })
  ]
});
