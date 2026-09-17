import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Check, ChevronLeft, ChevronRight, CircleHelp, Copy, Download, Eye, EyeOff, FileCheck2, Gauge, Pause, Play, RefreshCw, RotateCcw, Search, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../api/client";
import { exportPublicationPackage, getPublicationPreview, rerunRun, type PublicationPreview } from "../api/workflows";
import { PageHeader } from "../components/AppShell";
import { SqlWorkspace } from "../components/SqlWorkspace";
import { anonymousName, actualControlVariants, buildCaseRounds, compareResultCorrect, comparisonControls, keyRounds, terminalRunStatuses, type CaseRound, type ReportModel, type ReportSnapshot } from "../lib/reportAnalysis";
import "./report.css";

type Layer = "watch" | "inspect" | "verify";
const percent = (value: number | null | undefined) => value == null ? "待评估" : `${(value * 100).toFixed(1)}%`;
const score = (value: number | null | undefined) => value == null ? "—" : value.toFixed(2);
const compact = (value: number | null | undefined) => value == null ? "—" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1, notation: "compact" }).format(value);

const failureLabels: Record<string, string> = { policy_rejected: "政策拒绝（未证明业务错误）", protocol_error: "JSON 协议失败", execution_error: "SQL 解析或执行失败", provider_error: "Provider 调用失败", infrastructure_error: "本地基础设施错误", cancelled: "已取消", result_mismatch: "业务结果不匹配", format_mismatch: "输出格式不匹配" };

export function ReportPage() {
  const runId = Number(useParams().id);
  const report = useQuery({ queryKey: ["report", runId], queryFn: () => api.report(runId), enabled: Number.isFinite(runId), refetchInterval: (query) => terminalRunStatuses[query.state.data?.status ?? ""] ? false : 1500 });
  if (report.isError) return <main className="report-state"><AlertTriangle/><h1>报告读取失败</h1><p>{report.error instanceof Error ? report.error.message : "无法连接本地评测服务。"}</p><button className="button primary" onClick={() => report.refetch()}>重新读取</button></main>;
  if (!report.data) return <div className="loading-screen"><Gauge className="spin"/>正在整理比赛证据…</div>;
  return <ReportExperience report={report.data as ReportSnapshot}/>;
}

function ReportExperience({ report }: { report: ReportSnapshot }) {
  const [layer, setLayer] = useState<Layer>("watch");
  const [blind, setBlind] = useState(false);
  const [revealed, setRevealed] = useState(true);
  const [prediction, setPrediction] = useState<number | null>(null);
  const [roundIndex, setRoundIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [workspaceCase, setWorkspaceCase] = useState<string | null>(null);
  const [rerunChoice, setRerunChoice] = useState<null | { scope: "all" | "failed"; mode: "exact" | "current" }>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [publication, setPublication] = useState<PublicationPreview | null>(null);
  const [exportArmed, setExportArmed] = useState(false);
  const rounds = useMemo(() => buildCaseRounds(report), [report]);
  const highlights = useMemo(() => keyRounds(rounds), [rounds]);
  const ranked = useMemo(() => [...report.models].sort((a, b) => report.quality_schema_version === "result-quality-v2" ? (b.quality?.correct_rate ?? -1) - (a.quality?.correct_rate ?? -1) : (b.official_score ?? -Infinity) - (a.official_score ?? -Infinity)), [report.models, report.quality_schema_version]);
  const showIdentity = !blind || revealed;
  const completed = Boolean(terminalRunStatuses[report.status]);
  const expectedAttempts = report.protocol.case_count * Math.max(1, report.attempts);
  const nameFor = (model: ReportModel) => showIdentity ? model.name : anonymousName(report.models.indexOf(model));

  useEffect(() => {
    if (!playing || rounds.length < 2) return;
    const timer = window.setInterval(() => setRoundIndex((index) => index >= rounds.length - 1 ? 0 : index + 1), 1600);
    return () => window.clearInterval(timer);
  }, [playing, rounds.length]);

  const beginBlind = () => {
    setBlind(true);
    setRevealed(false);
    setPrediction(null);
    setWorkspaceCase(null);
    setLayer("watch");
    setRerunChoice(null);
    setPlaying(false);
  };
  const reveal = () => {
    setBlind(false);
    setRevealed(true);
    setPlaying(false);
  };
  const downloadJson = () => {
    if (!showIdentity) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `run-${report.id}-evidence.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  const confirmRerun = async () => {
    if (!rerunChoice) return;
    setActionBusy(true);
    try {
      const created = await rerunRun(report.id, rerunChoice);
      window.location.assign(`/runs/${created.id}/live`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "复测启动失败");
      setActionBusy(false);
    }
  };
  const previewPublication = async () => {
    setActionBusy(true);
    try {
      setPublication(await getPublicationPreview(report.id));
      setExportArmed(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "发布预检失败");
    } finally {
      setActionBusy(false);
    }
  };
  const exportPublication = async () => {
    if (!publication || !exportArmed) return;
    setActionBusy(true);
    try {
      const exported = await exportPublicationPackage(report.id, publication.summary_digest);
      const url = URL.createObjectURL(exported.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = exported.filename;
      anchor.click();
      URL.revokeObjectURL(url);
      setExportArmed(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "证据包导出失败");
    } finally {
      setActionBusy(false);
    }
  };

  return <div className="page report-page">
    <PageHeader eyebrow={`运行 #${report.id} · ${completed ? "已结束" : "运行中"}`} title="比赛复盘" description="同一份冻结证据，先看赛况，再拆解门道，最后核对合同。" actions={<>
      <Link className="button ghost" to={`/runs/${report.id}/live`}><ArrowLeft/>运行过程</Link>
      <button className="button ghost" onClick={() => navigator.clipboard.writeText(window.location.href).then(() => toast.success("报告链接已复制"))}><Copy/>复制链接</button>
      {showIdentity ? <button className="button ghost" onClick={beginBlind}><EyeOff/>进入匿名竞猜</button> : <button className="button ghost" onClick={reveal}><Eye/>退出盲测并揭晓</button>}
      <button className="button primary" disabled={!completed || !showIdentity || report.models.some((model) => model.adapter_kind !== "pi")} onClick={() => setRerunChoice({ scope: "failed", mode: "exact" })}><RotateCcw/>复测</button>
    </>}/>

    {showIdentity && report.models.some((model) => model.adapter_kind !== "pi") && <p className="notice">此运行使用历史接入，原始证据保持不变；如需新测，请在开赛页选择 Pi 配置。</p>}
    {!completed && <div className="report-running"><RefreshCw className="spin"/><b>比赛仍在进行</b><span>以下为当前持久化证据，官方总分与关键回合可能继续变化。</span></div>}
    {!report.models.length && <section className="report-empty"><CircleHelp/><h2>还没有参赛结果</h2><p>运行已建立，但尚未收到任何模型案例。可回到运行过程查看状态。</p></section>}

    {blind && !revealed && <section className="blind-banner" aria-label="匿名竞猜模式"><div><b>匿名竞猜进行中</b><p>本页只生成临时 A/B/更多参赛者代号；真实名称、模型 ID、SQL 证据和导出入口不会写入当前 DOM。预测只保留在此标签页，不上传也不改变评分。</p></div><button className="button ghost" onClick={reveal}>退出盲测并揭晓</button></section>}

    <nav className="report-layers" aria-label="复盘层级">
      {([['watch','看比赛','总分与关键回合'],['inspect','看门道','质量指标与逐题证据'],['verify','查证据','合同、对照与导出']] as const).map(([value, title, note], index) => <button key={value} disabled={!showIdentity && value !== "watch"} aria-pressed={layer === value} className={layer === value ? "active" : ""} onClick={() => setLayer(value)}><span>0{index + 1}</span><b>{title}</b><small>{note}</small></button>)}
    </nav>

    {layer === "watch" && <WatchLayer report={report} ranked={ranked} rounds={rounds} highlights={highlights} nameFor={nameFor} prediction={prediction} setPrediction={setPrediction} showIdentity={showIdentity} reveal={reveal} roundIndex={roundIndex} setRoundIndex={setRoundIndex} playing={playing} setPlaying={setPlaying} expectedAttempts={expectedAttempts}/>}
    {layer === "inspect" && <InspectLayer report={report} rounds={rounds} nameFor={nameFor} showIdentity={showIdentity} openCase={setWorkspaceCase}/>}
    {layer === "verify" && <VerifyLayer report={report} nameFor={nameFor} showIdentity={showIdentity} downloadJson={downloadJson} previewPublication={previewPublication} publication={publication} exportArmed={exportArmed} setExportArmed={setExportArmed} exportPublication={exportPublication} actionBusy={actionBusy}/>}

    {rerunChoice && <section className="action-confirm" role="dialog" aria-label="确认复测"><div><b>确认启动新的模型调用</b><p>这会创建新运行，不会修改当前证据。请选择冻结快照或当前配置，以及复测范围。</p></div><label>配置<select value={rerunChoice.mode} onChange={(event) => setRerunChoice({ ...rerunChoice, mode: event.target.value as "exact" | "current" })}><option value="exact">原运行冻结快照</option><option value="current">当前配置</option></select></label><label>范围<select value={rerunChoice.scope} onChange={(event) => setRerunChoice({ ...rerunChoice, scope: event.target.value as "all" | "failed" })}><option value="failed">仅失败题</option><option value="all">全部题</option></select></label><button className="button ghost" onClick={() => setRerunChoice(null)}>取消</button><button className="button primary" disabled={actionBusy} onClick={confirmRerun}>{actionBusy ? "正在创建…" : "确认并启动调用"}</button></section>}
    {showIdentity && <SqlWorkspace open={workspaceCase != null} onOpenChange={(open) => !open && setWorkspaceCase(null)} models={report.models} selectedCase={workspaceCase}/>}
  </div>;
}

function WatchLayer({ report, ranked, rounds, highlights, nameFor, prediction, setPrediction, showIdentity, reveal, roundIndex, setRoundIndex, playing, setPlaying, expectedAttempts }: {
  report: ReportSnapshot; ranked: ReportModel[]; rounds: CaseRound[]; highlights: CaseRound[]; nameFor: (model: ReportModel) => string; prediction: number | null; setPrediction: (id: number) => void; showIdentity: boolean; reveal: () => void; roundIndex: number; setRoundIndex: (index: number) => void; playing: boolean; setPlaying: (value: boolean) => void; expectedAttempts: number;
}) {
  const round = rounds[Math.min(roundIndex, Math.max(rounds.length - 1, 0))];
  const maxScore = Math.max(100, ...ranked.map((model) => model.official_score ?? 0));
  const leader = ranked[0];
  const runnerUp = ranked[1];
  const gap = leader?.official_score != null && runnerUp?.official_score != null ? leader.official_score - runnerUp.official_score : null;
  const businessFirst = report.quality_schema_version === "result-quality-v2";
  if (!showIdentity) return <section className="match-scoreboard"><header><div><span className="section-kicker">先预测，再揭晓</span><h2>同一道题，谁更稳？</h2></div><p>结果暂时隐藏。预测只在本标签页暂存，不上传、不计票、不影响评分。</p></header><p>{round?.question ?? "参赛者将回答同一份固定题库。"}</p><div className="blind-contenders">{report.models.map((model) => <button key={model.id} className={prediction === model.id ? "button primary" : "button"} aria-pressed={prediction === model.id} onClick={() => setPrediction(model.id)}>{prediction === model.id && <Check/>}{nameFor(model)} · 预测它胜出</button>)}</div><p>{prediction == null ? "选择一位参赛者，再打开真实成绩。" : "预测已在本标签页暂存。"}</p><button className="button primary" onClick={reveal}>揭晓身份</button></section>;
  return <div className="report-layer-content"><section className="match-summary"><p className="section-kicker">本场看点</p><h2>{businessFirst ? "先看业务结果，再看辅助得分" : gap == null ? "一份成绩，多种证据" : gap === 0 ? "本场综合得分并列" : `${nameFor(leader)} 本场高 ${score(gap)} 分`}</h2><p>综合得分不是正确率。{report.protocol.case_count} 道题、每题 {report.attempts} 次尝试，只代表本题库与本次配置，不构成稳定领先或统计显著性的证明。</p></section>
    {businessFirst && <section className="quality-board"><header><h2>本轮业务结果正确率</h2><p>同一冻结题库，分母包含全部计划尝试。单轮不代表稳定领先。</p></header><div className="quality-models">{ranked.map(model => <article key={model.id}><h3>{nameFor(model)}</h3><div className="quality-rate"><strong>{percent(model.quality?.correct_rate)}</strong><span>{model.quality?.result_correct ?? "—"}/{model.quality?.total ?? expectedAttempts} 题</span></div></article>)}</div></section>}
    <section className="match-scoreboard"><header><div><span className="section-kicker">官方记分牌</span><h2>{ranked.length > 1 ? businessFirst ? "辅助综合得分" : "总分赛况" : "单模型成绩单"}</h2></div><p>{report.protocol.case_count} 题 × {report.attempts} 次尝试；每个模型应有 {expectedAttempts} 次案例尝试。失败与未知状态保留边界，不补写结论。</p></header><div className="score-lines">{ranked.map((model, index) => <div className="score-line" key={model.id}><span className="rank">{businessFirst ? ranked.findIndex(other => other.quality?.correct_rate === model.quality?.correct_rate) + 1 : index + 1}</span><b>{nameFor(model)}</b><div className="score-track"><i style={{ width: `${Math.max(0, Math.min(100, ((model.official_score ?? 0) / maxScore) * 100))}%` }}/></div><strong>{score(model.official_score)}</strong><small>{model.cases.length}/{expectedAttempts} 尝试</small>{!showIdentity && <button className={prediction === model.id ? "prediction selected" : "prediction"} onClick={() => setPrediction(model.id)}>{prediction === model.id ? <Check/> : null}预测它胜出</button>}</div>)}</div>{!showIdentity && <div className="prediction-note"><span>{prediction == null ? "先选出你的预测，再揭晓参赛者。" : "预测已在本标签页暂存。"}</span><button className="button primary" onClick={reveal}>揭晓身份</button></div>}</section>

    <section className="round-highlights"><header><div><span className="section-kicker">关键回合</span><h2>分差来自哪里</h2></div><p>按全部计划尝试均分和题目权重，折算到 100 分总分中的贡献；失败不从分母消失。</p></header>{highlights.length ? <div className="highlight-list">{highlights.map((item, index) => <article key={item.key}><span>#{index + 1}</span><div><b>{item.title}</b><small>{item.category} · 权重 {item.weight}</small></div><strong>对总分的最大影响 {score(item.spread)}</strong><div>{item.models.map((row) => <span key={row.model.id}>{nameFor(row.model)} 均值 {score(row.score)} · 总分贡献 {score(row.contribution)}</span>)}</div></article>)}</div> : <p className="empty-inline">单模型、并列或缺少分项得分时无法计算跨模型分差。</p>}</section>

    <section className="replay-board"><header><div><span className="section-kicker">历史回放</span><h2>按题聚合回看</h2></div><p>这是已保存证据的逐题切换，不是实时过程。每一回合聚合同题的全部 attempt。</p></header>{round ? <><div className="replay-controls"><button aria-label="上一回合" disabled={roundIndex === 0} onClick={() => setRoundIndex(Math.max(0, roundIndex - 1))}><ChevronLeft/></button><button aria-label={playing ? "暂停历史回放" : "播放历史回放"} onClick={() => setPlaying(!playing)}>{playing ? <Pause/> : <Play/>}{playing ? "暂停" : "播放"}</button><span>第 {roundIndex + 1} / {rounds.length} 回合</span><button aria-label="下一回合" disabled={roundIndex >= rounds.length - 1} onClick={() => setRoundIndex(Math.min(rounds.length - 1, roundIndex + 1))}><ChevronRight/></button></div><article className="replay-frame"><div><small>{round.key}</small><h3>{round.title}</h3><p>{round.question ?? "题意未写入此报告版本。"}</p></div><div className="replay-results">{round.models.map((row) => <div key={row.model.id}><b>{nameFor(row.model)}</b><strong>{score(row.score)}</strong><span>{row.attempts} 次尝试 · 结果正确 {percent(row.resultCorrect)}</span></div>)}</div></article></> : <p className="empty-inline">没有可回放的案例证据。</p>}</section>
  </div>;
}

function InspectLayer({ report, rounds, nameFor, showIdentity, openCase }: { report: ReportSnapshot; rounds: CaseRound[]; nameFor: (model: ReportModel) => string; showIdentity: boolean; openCase: (key: string) => void }) {
  return <div className="report-layer-content">
    <section className="quality-board"><header><div><span className="section-kicker">{report.quality_schema_version === "result-quality-v2" ? "质量与失败分类" : "质量三指标"}</span><h2>结果、执行与协议分别看</h2></div><p>分母为全部计划尝试；未执行不等于业务结果错误。{report.quality_schema_version === "result-quality-v2" ? "新合同按结果值与排序判定业务正确，列名和 JSON 格式另报。" : "历史合同包含列名要求，原口径保留。"}</p></header>
      <div className="quality-models">{report.models.map(model => <article key={model.id}><h3>{nameFor(model)}</h3>{model.quality ? <>
        <div className="quality-rate"><span>结果正确</span><strong>{percent(model.quality.correct_rate)}</strong><small>{model.quality.result_correct}/{model.quality.total}</small></div>
        <div className="quality-rate"><span>执行成功</span><strong>{percent(model.quality.execution_rate)}</strong><small>{model.quality.execution_ok}/{model.quality.total}</small></div>
        <div className="quality-rate"><span>JSON 协议</span><strong>{percent(model.quality.protocol_rate)}</strong><small>{model.quality.protocol_ok}/{model.quality.total}</small></div>
        {model.quality.format_rate !== undefined && <div className="quality-rate"><span>输出格式</span><strong>{percent(model.quality.format_rate)}</strong><small>{model.quality.format_ok}/{model.quality.total}</small></div>}
        <p>结果证据覆盖 {model.quality.evaluated}/{model.quality.total}</p>
        {Object.entries(model.quality.failure_counts ?? {}).map(([kind, count]) => <p key={kind}>{failureLabels[kind] ?? kind}：{count}</p>)}
      </> : <p>质量证据不足</p>}</article>)}</div>
    </section>

    <section className="case-ledger"><header><div><span className="section-kicker">逐题账本</span><h2>题意、分项与对比</h2></div><p>{showIdentity ? "打开证据工作台可核对 SQL、执行结果与摘要。" : "匿名阶段隐藏 SQL 工作台和身份字段；揭晓后可查原始证据。"}</p></header><div className="case-table-wrap"><table><thead><tr><th>案例</th>{report.models.map((model) => <th key={model.id}>{nameFor(model)}</th>)}<th>证据</th></tr></thead><tbody>{rounds.map((round) => <tr key={round.key}><th><b>{round.title}</b><span>{round.question ?? "题意缺失"}</span><small>{round.category} · 权重 {round.weight}</small></th>{round.models.map((row) => <td key={row.model.id}><strong>{score(row.score)}</strong><span>结果 {row.resultCorrect == null ? "未知" : percent(row.resultCorrect)}</span><span>执行 {row.executionOk == null ? "未知" : percent(row.executionOk)}</span><span>协议 {row.protocolOk == null ? "未知" : percent(row.protocolOk)}</span><small>{row.reason ?? "无质量原因"}</small></td>)}<td>{showIdentity ? <button className="evidence-link" onClick={() => openCase(round.key)}><Search/>打开真实证据</button> : <span className="blind-lock">揭晓后可查</span>}</td></tr>)}</tbody></table></div>{!rounds.length && <p className="empty-inline">报告没有逐题记录。</p>}</section>

    <details className="efficiency-fold"><summary><span><b>资源效率</b><small>新合同按实际正确题数归一；旧合同保留得分折算，不能混排</small></span><ChevronRight/></summary><div>{report.models.map(model => {
      const modern = model.efficiency?.metric_schema_version === "efficiency-v2";
      const adjusted = modern ? model.efficiency?.per_correct_case : model.efficiency?.per_correct_case_equivalent;
      const unit = modern ? "正确题" : "得分折算题";
      return <article key={model.id}><h3>{nameFor(model)}</h3>{adjusted ? <dl><div><dt>{unit}数</dt><dd>{score(modern ? model.efficiency?.correct_cases : model.efficiency?.correct_case_equivalents)}</dd></div><div><dt>Token / {unit}</dt><dd>{compact(adjusted.tokens)}</dd></div><div><dt>生成时长 / {unit}</dt><dd>{compact(adjusted.generation_ms)} ms</dd></div><div><dt>估算费用 / {unit}</dt><dd>{adjusted.estimated_cost_usd == null ? "不可估算" : String(adjusted.estimated_cost_usd) + " USD"}</dd></div><div><dt>Token 记录覆盖</dt><dd>{model.efficiency?.coverage.tokens.measured}/{model.efficiency?.coverage.tokens.total}</dd></div></dl> : <p>缺少效率证据</p>}</article>;
    })}</div></details>
  </div>;
}

const comparisonModeLabel: Record<string, string> = {
  single_model: "单模型运行",
  pure_model: "历史：相同接入控制的模型比较",
  access_path: "历史：接入路径比较",
  controlled_harness: "Pi 统一受控调用",
};

const effectiveControlKeys = ["provider", "auth_mode", "timeout_seconds", "temperature", "max_tokens", "reasoning_effort", "harness", "harness_version", "policy_version", "tools_enabled", "tool_count", "generation_attempts", "generation_attempt_limit", "context_isolated", "system_prompt_sha256", "model_identity_source", "effective_parameters"] as const;
const controlValue = (value: unknown) => {
  if (value == null) return "默认";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return Object.entries(value).filter(([key]) => ["provider", "auth_mode", "timeout_seconds", "temperature", "max_tokens", "reasoning_effort"].includes(key)).map(([key, nested]) => `${key}=${String(nested)}`).join(" · ") || "已冻结";
  return String(value);
};

function VerifyLayer({ report, nameFor, showIdentity, downloadJson, previewPublication, publication, exportArmed, setExportArmed, exportPublication, actionBusy }: { report: ReportSnapshot; nameFor: (model: ReportModel) => string; showIdentity: boolean; downloadJson: () => void; previewPublication: () => void; publication: PublicationPreview | null; exportArmed: boolean; setExportArmed: (value: boolean) => void; exportPublication: () => void; actionBusy: boolean }) {
  return <div className="report-layer-content">
    <section className="contract-board"><header><div><span className="section-kicker">冻结快照</span><h2>评分合同与复现锚点</h2></div><ShieldCheck/></header><dl><div><dt>报告 schema</dt><dd>{report.report_schema_version ?? "旧版未声明"}</dd></div><div><dt>质量 schema</dt><dd>{report.quality_schema_version ?? "旧版未声明"}</dd></div><div><dt>Suite hash</dt><dd><code>{report.suite_content_hash}</code></dd></div><div><dt>案例集合</dt><dd>{report.selected_case_keys.length} 项</dd></div><div><dt>Attempts</dt><dd>{report.attempts}</dd></div><div><dt>评分器</dt><dd>{report.protocol.scorer_version}</dd></div><div><dt>输出合同</dt><dd>{report.protocol.output_contract}</dd></div></dl><div className="adapter-contracts">{report.models.map((model) => <article key={model.id}><b>{nameFor(model)}</b>{showIdentity && <code>{model.resolved_model_id ?? model.requested_model_id}</code>}<span>{showIdentity ? `${model.adapter_kind} · ${model.response_mode}` : "身份与适配器在匿名阶段隐藏"}</span></article>)}</div></section>
    <section className="contract-board"><header><div><span className="section-kicker">配置与预检快照</span><h2>{comparisonModeLabel[report.fairness.comparison_mode] ?? report.fairness.comparison_mode}</h2></div></header><p>{report.fairness.comparison_mode === "controlled_harness" ? "Pi 统一本地调用规则，不等于同端点、同预算或同模型。下方为配置/预检值，实际请求另列。" : "这是按原始历史快照保留的比较口径，不会改写为当前 Pi 调用合同。"}</p><div className="adapter-contracts">{report.models.map((model) => <article key={model.id}><b>{nameFor(model)}</b><dl>{effectiveControlKeys.map((key) => { const value = model.parameters[key] ?? model.isolation[key]; return value === undefined ? null : <div key={key}><dt>{key}</dt><dd>{controlValue(value)}</dd></div>; })}<div><dt>adapter</dt><dd>{model.adapter_kind}</dd></div><div><dt>response</dt><dd>{model.response_mode}</dd></div></dl></article>)}</div>{report.fairness.differences.length > 0 && <p>已披露差异：{report.fairness.differences.join("；")}</p>}</section>
    {showIdentity && report.models.some(model => model.adapter_kind === "pi") && <section className="contract-board"><header><div><span className="section-kicker">逐题请求证据</span><h2>实际请求与配置快照分开核对</h2></div></header><p>以下来自调用事件，而不是本地预检。记录的是请求载荷参数，不证明 Provider 内部计算预算或服务端模型版本。未完成的请求不能证明成功完成。</p><div className="adapter-contracts">{report.models.map(model => <article key={model.id}><b>{nameFor(model)}</b><p>已记录请求 {model.cases.filter(c => c.invocation).length}/{model.cases.length}；完成返回 {model.cases.filter(c => c.invocation?.status === "completed").length}/{model.cases.length}</p>{actualControlVariants(model).map((controls, index) => <details key={controls}><summary>实际控制组合 {index + 1}</summary><dl>{Object.entries(JSON.parse(controls) as Record<string, unknown>).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></div>)}</dl></details>)}</article>)}</div></section>}
    <RunComparison current={report} showIdentity={showIdentity}/>
    <section className="evidence-actions"><header><div><span className="section-kicker">证据出口</span><h2>预检后再导出</h2></div><p>JSON 是当前报告原文；发布包必须先生成预览摘要，再二次确认导出。导出不会自动上线。</p></header>{showIdentity ? <div className="evidence-action-row"><button className="button ghost" onClick={downloadJson}><Download/>下载报告 JSON</button><button className="button ghost" disabled={actionBusy} onClick={previewPublication}><FileCheck2/>生成发布预览</button></div> : <p className="blind-lock">匿名竞猜阶段禁用原始证据下载；退出盲测即可恢复。</p>}{publication && showIdentity && <article className="publication-preview"><div><b>{publication.eligible ? "预检通过" : "暂不可导出"}</b><span>状态 {publication.status} · 摘要 {publication.summary_digest}</span><pre>{JSON.stringify(publication.preview, null, 2)}</pre>{publication.warnings.length > 0 && <ul>{publication.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}</div><dl><div><dt>事件</dt><dd>{publication.manifest_summary.event_count}</dd></div><div><dt>案例运行</dt><dd>{publication.manifest_summary.case_run_count}</dd></div><div><dt>文件</dt><dd>{publication.manifest_summary.file_count}</dd></div></dl>{publication.eligible && (!exportArmed ? <button className="button primary" onClick={() => setExportArmed(true)}>准备导出证据包</button> : <div className="export-confirm"><span>确认摘要未变化？这只会下载文件，不会上线。</span><button className="button ghost" onClick={() => setExportArmed(false)}>取消</button><button className="button primary" disabled={actionBusy} onClick={exportPublication}>确认导出</button></div>)}</article>}</section>
  </div>;
}

function RunComparison({ current, showIdentity }: { current: ReportSnapshot; showIdentity: boolean }) {
  const history = useQuery({ queryKey: ["runs-for-comparison"], queryFn: api.runs, enabled: showIdentity });
  const candidates = history.data?.runs ?? [];
  const [leftRunId, setLeftRunId] = useState(current.source_run_id ?? current.id);
  const [rightRunId, setRightRunId] = useState(current.id);
  const [leftModelId, setLeftModelId] = useState<number | null>(null);
  const [rightModelId, setRightModelId] = useState<number | null>(null);

  const leftReport = useQuery({ queryKey: ["report", leftRunId], queryFn: () => api.report(leftRunId), enabled: showIdentity && leftRunId !== current.id });
  const rightReport = useQuery({ queryKey: ["report", rightRunId], queryFn: () => api.report(rightRunId), enabled: showIdentity && rightRunId !== current.id });
  const left = (leftRunId === current.id ? current : leftReport.data) as ReportSnapshot | undefined;
  const right = (rightRunId === current.id ? current : rightReport.data) as ReportSnapshot | undefined;
  const leftModel = left?.models.find((model) => model.id === leftModelId) ?? left?.models[0];
  const rightModel = right?.models.find((model) => model.id === rightModelId) ?? right?.models[0];
  const controls = left && right && leftModel && rightModel ? comparisonControls(left, right, leftModel, rightModel) : [];
  const comparable = controls.length > 0 && controls.every((control) => control.same);
  const changes = comparable && left && right && leftModel && rightModel ? compareResultCorrect(left, right, leftModel, rightModel) : [];
  if (!showIdentity) return <section className="comparison-board"><h2>跨运行比较</h2><p className="blind-lock">匿名阶段不加载运行历史，避免通过选择项或网络响应泄露身份。</p></section>;
  return <section className="comparison-board"><header><div><span className="section-kicker">跨运行比较</span><h2>先校验控制项，再谈进退</h2></div><p>只有 suite、案例、attempts、评分器、合同和接入控制一致，才按跨 attempt 的 result_correct 列出进步、退步与持平。</p></header><div className="comparison-pickers"><label>左侧基准<select value={leftRunId} onChange={(event) => { setLeftRunId(Number(event.target.value)); setLeftModelId(null); }}>{candidates.map((item) => <option key={item.id} value={item.id}>运行 #{item.id}</option>)}{!candidates.some((item) => item.id === current.id) && <option value={current.id}>运行 #{current.id}</option>}</select></label><label>左侧模型<select value={leftModel?.id ?? ""} onChange={(event) => setLeftModelId(Number(event.target.value))}>{left?.models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label><span>对照</span><label>右侧运行<select value={rightRunId} onChange={(event) => { setRightRunId(Number(event.target.value)); setRightModelId(null); }}>{candidates.map((item) => <option key={item.id} value={item.id}>运行 #{item.id}</option>)}{!candidates.some((item) => item.id === current.id) && <option value={current.id}>运行 #{current.id}</option>}</select></label><label>右侧模型<select value={rightModel?.id ?? ""} onChange={(event) => setRightModelId(Number(event.target.value))}>{right?.models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label></div>{leftReport.isError || rightReport.isError ? <p className="comparison-error">对照报告读取失败，只保留选择项，不生成优劣结论。</p> : !left || !right ? <p className="empty-inline">正在读取对照报告…</p> : <><div className="control-checks">{controls.map((control) => <div className={control.same ? "same" : "different"} key={control.label}><span>{control.same ? <Check/> : <AlertTriangle/>}{control.label}</span><code>{control.left}</code><code>{control.right}</code></div>)}</div>{comparable ? <div className="change-list">{changes.map((change) => <div className={`change ${change.state}`} key={change.key}><b>{change.title}</b><span>{change.left == null ? "未知" : percent(change.left)} → {change.right == null ? "未知" : percent(change.right)}</span><strong>{change.state}</strong></div>)}</div> : <div className="not-comparable"><AlertTriangle/><div><b>控制项不一致，只能并排查阅</b><p>{controls.filter((control) => !control.same).map((control) => control.label).join("、") || "缺少控制项"} 不一致；本页不会排列谁更好。</p></div></div>}</>}</section>;
}
