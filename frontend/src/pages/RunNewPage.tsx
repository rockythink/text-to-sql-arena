import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, ChevronRight, CircleAlert, Cpu, Play, RefreshCw, Scale, ShieldCheck } from "lucide-react";
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
    <PageHeader eyebrow="新建评测" title="配置评测运行" description="选择题库、参评模型和重复次数，然后开始运行。"/>
    {!published.length ? <EmptyState icon={<CircleAlert/>} title="没有可用的已发布题库" body="请先复制或发布一个题库版本。" action={<button className="button primary" onClick={() => navigate("/benchmarks")}>管理题库<ChevronRight/></button>}/> : <>
      <section className="setup-band"><div><span className="step-no">1</span><div><label>题库版本</label><select value={suite?.version.id ?? ""} onChange={(event) => setSuiteId(Number(event.target.value))}>{published.map(({ suite: item, version }) => <option key={version.id} value={version.id}>{item.name} · v{version.version}</option>)}</select></div></div><div className="suite-proof"><small>运行范围</small><b>{caseCount} 题</b><code>{suite?.version.content_hash?.slice(0, 12)}</code></div></section>
      <section className="section-block"><div className="section-title"><div><span className="step-no">2</span><h2>参评模型</h2></div><small>已选择 {selected.length}/6 · 至少选择 1 个</small></div>
        {!healthy.length ? <EmptyState icon={<Cpu/>} title="没有可用模型" body="模型需要完成配置并通过健康检查。" action={<button className="button primary" onClick={() => navigate("/models")}>配置模型<ChevronRight/></button>}/> : <div className="model-grid">{healthy.map((profile) => { const active = selected.includes(profile.id); return <button key={profile.id} className={`model-card ${active ? "selected" : ""}`} aria-pressed={active} onClick={() => toggle(profile.id)}><span className="model-select-box">{active && <Check/>}</span><div className="model-card-copy"><h3>{profile.name}</h3><code>{profile.model_id}</code></div><div className="model-card-foot"><StatusPill status={profile.health_status}/><span>{profile.adapter_kind}</span></div></button>; })}</div>}
      </section>
      <section className="fairness-contract"><header><Scale/><div><small>运行约束</small><h2>{fairnessMode}</h2></div></header><div className="fairness-grid"><article><small>控制变量</small><b>{differences.length ? "存在差异" : "配置一致"}</b><span>{differences.length ? differences.join("、") : "接入、响应和参数一致"}</span></article><article><small>输出合同</small><b>query-plan-v1</b><span>规划与 SQL 单次调用</span></article><article><small>预计调用</small><b>{totalCalls} 次</b><span>约 {estimatedMinutesPerModel} 分钟/模型</span></article><article><small>预计输入</small><b>{estimatedInputTokens}K tokens</b><span>按近期运行数据估算</span></article></div><p><ShieldCheck/>运行报告会保留比较模式、配置快照和失败记录。</p></section>
      <section className="launch-deck"><div><span className="step-no">3</span><div><h2>重复次数</h2><p>报告保留全部尝试，并显示最新结果。</p></div><div className="segments" aria-label="重复次数">{[1, 2, 3].map((value) => <button className={attempts === value ? "active" : ""} key={value} onClick={() => setAttempts(value)}>{value}</button>)}</div></div><div className="launch-action">{selected.length === 0 && <small>请至少选择 1 个模型</small>}<button className="button launch" disabled={!suite || selected.length === 0 || start.isPending} onClick={() => suite && start.mutate({ suite_version_id: suite.version.id, model_profile_ids: selected, case_ids: null, attempts })}>{start.isPending ? <RefreshCw className="spin"/> : <Play/>}{start.isPending ? "正在创建运行…" : "开始评测"}</button></div></section>
      <section className="run-history"><header><div><small>最近运行</small><h2>运行记录</h2></div><span>共 {runs.data?.runs.length ?? 0} 条</span></header><div>{runs.data?.runs.slice(0, 5).map((run) => <Link to={`/runs/${run.id}/${["completed", "completed_with_errors"].includes(run.status) ? "report" : "live"}`} key={run.id}><b>#{run.id}</b><span>{run.models.map((model) => model.name).join(" / ") || "等待模型"}</span><small>{run.case_count} 题 · {run.attempts} 次</small><StatusPill status={run.status}/></Link>)}</div></section>
    </>}
  </div>;
}
