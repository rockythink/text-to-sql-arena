import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Copy, Download, Gauge, RotateCcw, Scale, ShieldCheck } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../api/client";
import { PageHeader, Scoreboard } from "../components/AppShell";
import { HeatmapChart, RadarChart, RankingChart } from "../components/ReportCharts";
import { ModelIdentity } from "../components/ModelIdentity";
import { displayModelName } from "../lib/modelIdentity";

const metricNumber = (value: number | null | undefined) => value == null ? "—" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(value);
const metricUsd = (value: number | null | undefined) => value == null ? "不可估算" : `$${value.toFixed(value < 0.01 ? 6 : 4)}`;
const metricTime = (value: number | null | undefined) => value == null ? "—" : value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${value.toFixed(0)}ms`;

export function ReportPage() {
  const runId = Number(useParams().id);
  const report = useQuery({ queryKey: ["report", runId], queryFn: () => api.report(runId) });
  if (!report.data) return <div className="loading-screen"><Gauge className="spin"/>正在生成运行报告…</div>;
  const data = report.data;
  const rerun = async () => { const next = await api.rerun(runId); window.location.href = `/runs/${next.id}/live`; };
  const copy = async () => { await navigator.clipboard.writeText(window.location.href); toast.success("报告链接已复制"); };
  const download = () => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `llm-text-to-sql-run-${runId}-evidence.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  const fairness = data.fairness;
  const fairnessTitle = fairness.comparison_mode === "pure_model" ? "纯模型对比" : fairness.comparison_mode === "access_path" ? "接入路径对比" : "单模型评测";

  return <div className="page report-page">
    <PageHeader eyebrow={`运行 #${runId}`} title="运行报告" description="查看综合得分、资源消耗、逐题结果和运行快照。" actions={<><Link className="button ghost" to={`/runs/${runId}/live`}><ArrowLeft/>运行过程</Link><button className="button ghost" onClick={copy}><Copy/>复制链接</button><button className="button ghost" onClick={download}><Download/>导出 JSON</button><button className="button primary" onClick={rerun}><RotateCcw/>按快照重跑</button></>}/>
    <><Scoreboard suiteHash={data.suite_content_hash} models={[...data.models].sort((a, b) => (b.official_score ?? 0) - (a.official_score ?? 0)).map((model) => ({ id: model.id, name: model.name, modelId: model.resolved_model_id ?? model.requested_model_id, adapterKind: model.adapter_kind, score: model.official_score, status: model.status }))}/><section className="efficiency-board"><header><small>QUALITY-ADJUSTED EFFICIENCY</small><h2>资源效率</h2><p>准确率独立保留；Token、费用和时长按“正确等价题”归一化，数值越低越好。费用只使用运行创建时冻结的价格快照估算。</p></header><div className="efficiency-models">{data.models.map((model) => { const metrics = model.efficiency; const adjusted = metrics?.per_correct_case_equivalent; return <article key={model.id}><div className="efficiency-model-head"><h3><ModelIdentity name={model.name} modelId={model.resolved_model_id ?? model.requested_model_id} adapterKind={model.adapter_kind}/></h3><strong>{model.official_score?.toFixed(2) ?? "—"}<small>/100</small></strong></div><dl><div><dt>Token / 正确等价题</dt><dd>{metricNumber(adjusted?.tokens)}</dd></div><div><dt>费用 / 正确等价题</dt><dd>{metricUsd(adjusted?.estimated_cost_usd)}</dd></div><div><dt>生成耗时 P95</dt><dd>{metricTime(metrics?.generation_ms.p95)}</dd></div><div><dt>累计估算费用</dt><dd>{metricUsd(metrics?.estimated_cost_usd)}</dd></div></dl><p>覆盖：Token {metrics?.coverage.tokens.measured ?? 0}/{metrics?.coverage.tokens.total ?? model.cases.length} · 时长 {metrics?.coverage.generation_time.measured ?? 0}/{metrics?.coverage.generation_time.total ?? model.cases.length}</p></article>; })}</div></section></>
    <section className={`fairness-verdict mode-${fairness.comparison_mode}`}><Scale/><div><small>比较条件</small><h2>{fairnessTitle}</h2><p>{fairness.differences.length ? `接入控制项存在差异：${fairness.differences.join("、")}。分数代表模型与接入路径的整体表现。` : "接入方式、响应模式和参数一致；模型是唯一主动变量。"}</p></div><dl><div><dt>题量</dt><dd>{data.protocol.case_count}</dd></div><div><dt>尝试</dt><dd>{data.protocol.attempts}</dd></div><div><dt>输出合同</dt><dd>{data.protocol.output_contract}</dd></div><div><dt>重跑</dt><dd>exact snapshot</dd></div></dl></section>
    <section className="verdict-strip"><div className="verdict-seal"><CheckCircle2/><span>OBJECTIVE</span></div><div><small>结果摘要</small><h2>{data.conclusion?.champions?.length ? `最高分：${data.conclusion.champions.map(displayModelName).join("、")}` : "运行已完成"}</h2><p>依据执行结果、列行比对、顺序语义和 AST 规则计算。</p></div><div className="runtime-proof"><span>APP {data.app_version}</span><span>SCORER {data.scorer_version}</span><span>DUCKDB {data.duckdb_version}</span><span>SQLGLOT {data.sqlglot_version}</span></div></section>
    <div className="report-grid"><article className="chart-card"><header><div><small>01 / RANKING</small><h2>综合得分对比</h2></div><span>0—100 · 失败题按 0 分计</span></header><RankingChart report={data}/></article><article className="chart-card"><header><div><small>02 / ABILITY</small><h2>能力维度</h2></div><span>每维固定 3 题</span></header><RadarChart report={data}/></article><article className="chart-card full"><header><div><small>03 / CASE MATRIX</small><h2>逐题结果</h2></div><span>每格可追溯到规划、SQL 与结果</span></header><HeatmapChart report={data}/></article></div>
    <section className="model-receipts">{data.models.map((model) => <article key={model.id}><div><small>模型运行信息</small><h3><ModelIdentity name={model.name} modelId={model.resolved_model_id ?? model.requested_model_id} adapterKind={model.adapter_kind}/></h3><code>{model.resolved_model_id ?? model.requested_model_id}</code></div><dl><div><dt>Transport</dt><dd>{model.adapter_kind}</dd></div><div><dt>Harness</dt><dd>{model.cli_version ?? model.response_mode}</dd></div><div><dt>失败题数</dt><dd>{model.failure_count ?? 0}</dd></div><div><dt>官方分</dt><dd>{model.official_score?.toFixed(2) ?? "—"}</dd></div></dl></article>)}</section>
    <section className="repro-proof"><ShieldCheck/><div><b>复现锚点</b><code>{data.suite_content_hash}</code></div><span>scorer {data.protocol.scorer_version} · source run {data.source_run_id ?? "original"}</span></section>
  </div>;
}
