import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, ChevronRight, CircleAlert, Cpu, Play, RefreshCw, Scale, ShieldCheck, Trophy } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../api/client";
import { EmptyState, PageHeader, StatusPill } from "../components/AppShell";

const SECONDS_PER_CASE = 19;
const INPUT_TOKENS_PER_CASE = 19_143;

export function RunNewPage() {
  const navigate = useNavigate();
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const suites = useQuery({ queryKey: ["suites"], queryFn: api.suites });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const published = useMemo(() => suites.data?.flatMap((suite) => suite.versions.filter((version) => version.status === "published").map((version) => ({ suite, version }))) ?? [], [suites.data]);
  const [suiteId, setSuiteId] = useState<number | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [attempts, setAttempts] = useState(1);
  const suite = published.find(({ version }) => version.id === (suiteId ?? published.at(-1)?.version.id));
  const healthy = profiles.data?.filter((profile) => profile.enabled && profile.health_status === "healthy") ?? [];
  const selectedProfiles = healthy.filter((profile) => selected.includes(profile.id));
  const start = useMutation({ mutationFn: api.createRun, onSuccess: (run) => window.location.assign("/runs/" + run.id + "/live"), onError: (error: Error) => toast.error(error.message) });
  const toggle = (id: number) => setSelected((values) => values.includes(id) ? values.filter((value) => value !== id) : values.length < 6 ? [...values, id] : values);

  const controls = {
    adapter_kind: new Set(selectedProfiles.map((profile) => profile.adapter_kind)),
    base_url: new Set(selectedProfiles.map((profile) => profile.base_url ?? "")),
    response_mode: new Set(selectedProfiles.map((profile) => profile.response_mode)),
    parameters: new Set(selectedProfiles.map((profile) => JSON.stringify(profile.parameters, Object.keys(profile.parameters).sort()))),
  };
  const differences = Object.entries(controls).filter(([, values]) => values.size > 1).map(([field]) => field);
  const fairnessMode = selectedProfiles.length < 2 ? "单模型评测" : differences.length ? "接入路径对比" : "纯模型对比";
  const caseCount = suite?.version.cases.length ?? 0;
  const totalCalls = caseCount * selectedProfiles.length * attempts;
  const estimatedMinutesPerModel = Math.ceil(caseCount * attempts * SECONDS_PER_CASE / 60);
  const estimatedInputTokens = Math.round(totalCalls * INPUT_TOKENS_PER_CASE / 1000);

  return <div className="page run-setup-page">
    <PageHeader eyebrow="MATCH SETUP / 01" title="今天让谁来写 SQL？" description="18 题、六维能力、单次结构化规划与 SQL 输出；先声明公平性，再开跑。"/>
    {!published.length ? <EmptyState icon={<CircleAlert/>} title="还没有已发布的赛题" body="先去基准赛题复制或发布一个版本。" action={<button className="button primary" onClick={() => navigate("/benchmarks")}>去准备赛题<ChevronRight/></button>}/> : <>
      <section className="setup-band"><div><span className="step-no">01</span><div><label>选择基准赛题</label><select value={suite?.version.id ?? ""} onChange={(event) => setSuiteId(Number(event.target.value))}>{published.map(({ suite: item, version }) => <option key={version.id} value={version.id}>{item.name} · v{version.version}</option>)}</select></div></div><div className="suite-proof"><small>固定题量</small><b>{caseCount} 题</b><code>{suite?.version.content_hash?.slice(0, 12)}</code></div></section>
      <section className="section-block"><div className="section-title"><div><span className="step-no">02</span><h2>挑选模型选手</h2></div><small>{selected.length}/6 已选择</small></div>
        {!healthy.length ? <EmptyState icon={<Cpu/>} title="没有通过健康检查的模型" body="模型必须安装、可调用且健康状态未过期，才允许上场。" action={<button className="button primary" onClick={() => navigate("/models")}>配置模型<ChevronRight/></button>}/> : <div className="model-grid">{healthy.map((profile, index) => { const active = selected.includes(profile.id); return <button key={profile.id} className={`model-card ${active ? "selected" : ""}`} onClick={() => toggle(profile.id)}><span className="model-index">M{String(index + 1).padStart(2, "0")}</span><div className="model-card-head"><span className="provider-mark">{profile.adapter_kind.slice(0, 2).toUpperCase()}</span>{active && <span className="selected-check"><Check/></span>}</div><h3>{profile.name}</h3><code>{profile.model_id}</code><div className="model-card-foot"><StatusPill status={profile.health_status}/><span>{profile.response_mode}</span></div></button>; })}</div>}
      </section>
      <section className="fairness-contract"><header><Scale/><div><small>03 / FAIRNESS CONTRACT</small><h2>{fairnessMode}</h2></div></header><div className="fairness-grid"><article><small>控制变量</small><b>{differences.length ? "存在差异" : "接入配置一致"}</b><span>{differences.length ? differences.join("、") : "adapter · base_url · response · parameters"}</span></article><article><small>协议</small><b>query-plan-v1</b><span>规划 + SQL 单次调用，失败同样留证</span></article><article><small>预计调用</small><b>{totalCalls} 次</b><span>约 {estimatedMinutesPerModel} 分钟/模型</span></article><article><small>输入量级</small><b>约 {estimatedInputTokens}K tokens</b><span>按最近实测 19,143 tokens/题估算</span></article></div><p><ShieldCheck/>报告会标记纯模型或接入路径对比；“整场重跑”默认复制本场全部快照，不读取后来修改的配置。</p></section>
      <section className="launch-deck"><div><span className="step-no">04</span><div><h2>重复作答</h2><p>取最新结果展示，报告保留全部尝试统计。</p></div><div className="segments" aria-label="重复次数">{[1, 2, 3].map((value) => <button className={attempts === value ? "active" : ""} key={value} onClick={() => setAttempts(value)}>{value}</button>)}</div></div><button className="button launch" disabled={!suite || selected.length === 0 || start.isPending} onClick={() => suite && start.mutate({ suite_version_id: suite.version.id, model_profile_ids: selected, case_ids: null, attempts })}>{start.isPending ? <RefreshCw className="spin"/> : selected.length > 1 ? <Trophy/> : <Play/>}{start.isPending ? "正在入场…" : selected.length > 1 ? "开始对比" : "开始单测"}</button></section>
      <section className="run-history"><header><div><small>RECENT RUNS</small><h2>运行历史</h2></div><span>最近 {runs.data?.runs.length ?? 0} 场</span></header><div>{runs.data?.runs.slice(0, 8).map((run) => <Link to={`/runs/${run.id}/${["completed", "completed_with_errors"].includes(run.status) ? "report" : "live"}`} key={run.id}><b>#{run.id}</b><span>{run.models.map((model) => model.name).join(" vs ") || "等待模型"}</span><small>{run.case_count} 题 · {run.attempts} 次</small><StatusPill status={run.status}/></Link>)}</div></section>
    </>}
  </div>;
}
