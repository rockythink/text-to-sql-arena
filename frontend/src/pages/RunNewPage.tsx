import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, Check, CircleAlert, Play, RefreshCw, Save, ShieldCheck, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../api/client";
import { preflightRun } from "../api/workflows";
import { EmptyState, PageHeader, StatusPill } from "../components/AppShell";
import { ModelIdentity, ModelLogo } from "../components/ModelIdentity";
import { displayModelName } from "../lib/modelIdentity";

type MatchPreset = { name: string; suiteId: number; suiteHash: string; models: number[]; cases: number[] | null; attempts: number };
const PRESET_KEY = "arena-match-presets-v1";
const ids = (value: unknown): value is number[] => Array.isArray(value) && value.every((item) => Number.isInteger(item) && item > 0);
function loadPresets(): MatchPreset[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(PRESET_KEY) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is MatchPreset => item != null && typeof item === "object" && typeof item.name === "string" && typeof item.suiteHash === "string" && Number.isInteger(item.suiteId) && ids(item.models) && item.models.length > 0 && item.models.length <= 6 && (item.cases === null || ids(item.cases)) && item.attempts === 1).slice(0, 20);
  } catch { return []; }
}

export function RunNewPage() {
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: api.profiles, select: (items) => items.map((profile) => profile.adapter_kind === "pi" ? profile : { ...profile, enabled: false }).sort((a, b) => Number(b.adapter_kind === "pi") - Number(a.adapter_kind === "pi")) });
  const suites = useQuery({ queryKey: ["suites"], queryFn: api.suites });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const published = useMemo(() => suites.data?.flatMap((suite) => suite.versions.filter((version) => version.status === "published").map((version) => ({ suite, version }))) ?? [], [suites.data]);
  const [suiteId, setSuiteId] = useState<number | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [caseIds, setCaseIds] = useState<number[] | null>(null);
  const [attempts, setAttempts] = useState(1);
  const [presets, setPresets] = useState(loadPresets);
  const [presetName, setPresetName] = useState("");
  const [presetIndex, setPresetIndex] = useState("");
  const [search, setSearch] = useState("");
  const [historyStatus, setHistoryStatus] = useState("all");
  const suite = published.find(({ version }) => version.id === (suiteId ?? published.at(-1)?.version.id));
  const cases = suite?.version.cases ?? [];
  const chosenCases = caseIds === null ? cases : cases.filter((item) => caseIds.includes(item.id));
  const selectedProfiles = profiles.data?.filter((profile) => selected.includes(profile.id)) ?? [];
  const payload = { suite_version_id: suite?.version.id ?? 0, model_profile_ids: selected, case_ids: caseIds, attempts };
  const readyToCheck = Boolean(suite && selected.length && chosenCases.length);
  const preflight = useQuery({ queryKey: ["preflight", payload], queryFn: () => preflightRun(payload), enabled: readyToCheck, staleTime: 0, retry: false, refetchInterval: 30_000 });
  const start = useMutation({
    mutationFn: async () => {
      const checked = await preflightRun(payload);
      if (!checked.ready) throw new Error(checked.issues.filter((issue) => issue.severity === "error").map((issue) => issue.message).join("；") || "开赛条件已变化，请重新预检");
      return api.createRun(payload);
    },
    onSuccess: (run) => window.location.assign(`/runs/${run.id}/live`),
    onError: (error: Error) => { toast.error(error.message); void preflight.refetch(); },
  });
  const toggle = (id: number) => setSelected((values) => values.includes(id) ? values.filter((value) => value !== id) : values.length < 6 ? [...values, id] : values);
  const totalCalls = chosenCases.length * selected.length * attempts;
  const persist = (next: MatchPreset[]) => {
    try { localStorage.setItem(PRESET_KEY, JSON.stringify(next)); setPresets(next); return true; }
    catch { toast.error("浏览器未允许保存方案，请检查本地存储权限"); return false; }
  };
  const savePreset = () => {
    if (!suite || !selected.length || !chosenCases.length || !presetName.trim()) return;
    const name = presetName.trim();
    if (presets.some((preset) => preset.name === name)) { toast.error("方案名称已存在，请换一个名称或先删除旧方案"); return; }
    if (presets.length >= 20) { toast.error("最多保存 20 个方案，请先删除不再使用的方案"); return; }
    if (persist([...presets, { name, suiteId: suite.version.id, suiteHash: suite.version.content_hash!, models: selected, cases: caseIds, attempts }])) { setPresetName(""); toast.success("方案已保存在此浏览器；不包含密钥"); }
  };
  const applyPreset = (index: string) => {
    setPresetIndex(index);
    if (index === "") return;
    const preset = presets[Number(index)];
    const version = published.find((item) => item.version.id === preset.suiteId)?.version;
    if (!version || version.content_hash !== preset.suiteHash) { toast.error("方案题库版本不可用或指纹不一致，未替换当前配置"); return; }
    if (preset.cases?.some((id) => !version.cases.some((item) => item.id === id))) { toast.error("方案中存在不可用题目，未替换当前配置"); return; }
    if (preset.models.some((id) => !profiles.data?.some((item) => item.id === id && item.enabled && item.adapter_kind === "pi"))) { toast.error("方案中存在历史、已删除或禁用的模型，未替换当前配置"); return; }
    setSuiteId(preset.suiteId); setSelected(preset.models); setCaseIds(preset.cases); setAttempts(1);
    toast.success("已载入方案；使用模型当前配置，开赛前重新检查");
  };
  const history = runs.data?.runs.filter((run) => (historyStatus === "all" || (historyStatus === "completed" ? run.status === "completed" : run.status !== "completed")) && `${run.id} ${run.models.map((model) => model.name).join(" ")}`.toLowerCase().includes(search.toLowerCase())) ?? [];
  const estimate = preflight.data?.estimate;
  const error = profiles.error ?? suites.error;
  if (error) return <div className="page"><EmptyState icon={<CircleAlert/>} title="工作台暂时无法读取数据" body={(error as Error).message} action={<button className="button" onClick={() => { void profiles.refetch(); void suites.refetch(); }}>重新读取</button>}/></div>;
  if (profiles.isPending || suites.isPending) return <div className="loading-screen"><RefreshCw className="spin"/>正在准备赛场…</div>;

  return <div className="page run-setup-page">
    <PageHeader eyebrow="SQL 擂台 / 本地评测" title="让模型，拿结果说话。" description="从真实业务问题出发。同一道题、同一份数据，看看谁能把账算明白。"/>
    <div className="setup-intro"><div><h2>认真出题，<span>也认真看一场比赛。</span></h2><p>新建一场较量，或从下方往期报告开始看。所有评分都有原始证据。</p></div><div className="setup-marker" aria-hidden="true">A / B</div></div>
    {!published.length ? <EmptyState title="还没有锁定的赛题" body="请先在赛题实验室建立并发布题库版本。发布会校验参考答案并冻结内容。" action={<Link className="button primary" to="/benchmarks">进入赛题实验室<ArrowRight/></Link>}/> : <>
      <div className="preset-bar"><label htmlFor="match-preset" className="sr-only">常用评测方案</label><select id="match-preset" value={presetIndex} onChange={(event) => applyPreset(event.target.value)}><option value="">载入常用方案 · 当前浏览器</option>{presets.map((preset, index) => <option key={preset.name} value={index}>{preset.name}</option>)}</select><button className="button ghost" disabled={presetIndex === ""} onClick={() => { if (persist(presets.filter((_, index) => index !== Number(presetIndex)))) setPresetIndex(""); }}><Trash2/>删除方案</button></div>
      <div className="setup-layout"><div className="setup-main">
        <section className="setup-section"><div className="section-title"><div><span className="step-no">01</span><h2>今天，比什么？</h2></div><Link className="button ghost" to="/benchmarks">查看题库</Link></div>
          <label className="suite-selector">选择已锁定版本<select value={suite?.version.id ?? ""} onChange={(event) => { setSuiteId(Number(event.target.value)); setCaseIds(null); setPresetIndex(""); }}>{published.map(({ suite: item, version }) => <option key={version.id} value={version.id}>{item.name} · v{version.version}</option>)}</select></label>
          <p className="suite-proof"><span>{chosenCases.length} / {cases.length} 题入选</span><span>DuckDB · 固定数据</span><code title={suite?.version.content_hash ?? ""}>{suite?.version.content_hash?.slice(0, 12)}</code></p>
          <details className="case-picker"><summary>看看题目，或只选几个关键回合</summary><div className="case-picker-toolbar"><small>所有参赛模型回答相同题目</small><div className="button-row"><button className="button ghost" onClick={() => setCaseIds(null)}>全选</button><button className="button ghost" onClick={() => setCaseIds([])}>清空</button></div></div><div className="case-options">{cases.map((item, index) => <label className="case-option" key={item.id}><input type="checkbox" checked={caseIds === null || caseIds.includes(item.id)} onChange={() => setCaseIds((previous) => { const current = previous ?? cases.map((entry) => entry.id); return current.includes(item.id) ? current.filter((id) => id !== item.id) : [...current, item.id]; })}/><div><b>{String(index + 1).padStart(2, "0")} · {item.title}</b><p>{item.question}</p><small>{item.radar_dimension} · {item.difficulty === "easy" ? "基础" : item.difficulty === "hard" ? "挑战" : "进阶"}</small></div></label>)}</div></details>
        </section>
        <section className="setup-section"><div className="section-title"><div><span className="step-no">02</span><h2>谁来应战？</h2></div><small>已选 {selected.length} / 6</small></div>
          {!profiles.data?.length ? <EmptyState title="等待第一位参赛者" body="先添加模型，再检查接入是否可用。" action={<Link className="button primary" to="/models">配置模型</Link>}/> : <div className="model-grid">{profiles.data.map((profile) => { const active = selected.includes(profile.id); return <button key={profile.id} className={`model-card ${active ? "selected" : ""}`} disabled={!profile.enabled || (!active && selected.length >= 6)} aria-pressed={active} onClick={() => toggle(profile.id)}><ModelLogo name={profile.name} modelId={profile.model_id} adapterKind={profile.adapter_kind}/><div><h3>{displayModelName(profile.name)}</h3><code>{profile.model_id}</code></div><span className="model-select-box" aria-hidden="true">{active && <Check/>}</span><div className="model-card-foot"><StatusPill status={profile.health_status}/><span>{profile.adapter_kind === "pi" ? "Pi 统一调用" : "历史配置 · 不可参赛"}</span></div></button>; })}</div>}
          <p className="fairness-note">新评测只允许 Pi 统一调用配置，且固定单次作答。历史适配器仍显示但不能参赛；Provider、认证和有效控制项会在报告中披露。<Link to="/models"> 管理模型与健康检查 →</Link></p>
        </section>
        <section className="setup-section"><div className="section-title"><div><span className="step-no">03</span><h2>下次，一键再来</h2></div></div><p className="page-description">方案只保存选择项；正式运行仍冻结当时的配置。要复现旧配置，请从历史报告按快照重跑。</p><div className="preset-form"><label className="sr-only" htmlFor="preset-name">方案名称</label><input id="preset-name" value={presetName} maxLength={60} onChange={(event) => setPresetName(event.target.value)} placeholder="例如：零售题库 · Pi 双模型"/><button className="button" disabled={!presetName.trim() || !readyToCheck} onClick={savePreset}><Save/>保存方案</button></div></section>
      </div>
      <aside className="setup-aside" aria-label="开赛预检"><h2>开赛前，核对一下</h2><dl><div><dt>参赛者</dt><dd>{selectedProfiles.length} 位</dd></div><div><dt>题目</dt><dd>{chosenCases.length} 道</dd></div><div><dt>总调用量</dt><dd>{totalCalls} 次</dd></div></dl><p>每题单次作答</p><div className="segments" aria-label="重复次数"><button aria-pressed="true" className="active" disabled>1 次</button></div><p>统一调用固定单次尝试；运行会保留完整输出和控制快照。</p>
        {readyToCheck ? <div className="preflight-result" aria-live="polite">{preflight.isFetching && <p>正在核对本地配置…</p>}{preflight.error && <><p className="error">{(preflight.error as Error).message}</p><button className="button" onClick={() => void preflight.refetch()}>重新检查</button></>}{preflight.data && <><h3><ShieldCheck size={16}/> {preflight.data.ready ? "条件已满足" : "还不能开赛"}</h3>{preflight.data.issues.length > 0 && <ul>{preflight.data.issues.map((issue, index) => <li className={issue.severity} key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul>}<p>预计用时：{estimate?.estimated_duration_seconds != null ? `约 ${Math.ceil(estimate.estimated_duration_seconds / 60)} 分钟` : "没有足够历史样本"}<br/>预计费用：{estimate?.estimated_cost_usd != null ? `$${estimate.estimated_cost_usd.toFixed(4)}` : "暂不可估算"}</p><small>{estimate?.sample_calls ? `依据 ${estimate.sample_calls} 次历史调用，仅供参考。` : "不使用固定常量假装实时估算。"}</small></>}</div> : <p>选好至少一道题和一位参赛者后，自动检查开赛条件。</p>}
        <button className="button launch" disabled={!readyToCheck || !preflight.data?.ready || preflight.isFetching || preflight.isError || start.isPending} onClick={() => start.mutate()}>{start.isPending ? <RefreshCw className="spin"/> : <Play/>}{start.isPending ? "正在创建比赛…" : "开始评测"}</button><p>点击后会真实调用模型，可能产生费用。不会自动发布结果。</p>
      </aside></div>
    </>}
    <section className="run-history"><header><div><p className="eyebrow">已经发生的较量</p><h2>往期赛场</h2></div><div className="history-tools"><input aria-label="搜索历史运行" placeholder="运行编号或模型名称" value={search} onChange={(event) => setSearch(event.target.value)}/><select aria-label="历史状态筛选" value={historyStatus} onChange={(event) => setHistoryStatus(event.target.value)}><option value="all">全部状态</option><option value="completed">完整结束</option><option value="other">有失败 / 其他状态</option></select></div></header>
      {runs.error ? <p className="notice error">历史记录读取失败：{(runs.error as Error).message}</p> : !history.length ? <p className="notice">{runs.isPending ? "正在读取往期比赛…" : "暂无匹配的运行记录。"}</p> : <><p><small>最近 {runs.data?.runs.length} 场中的 {history.length} 场 · 失败与中断同样保留</small></p>{history.map((run) => <Link className="history-row" to={`/runs/${run.id}/${["completed", "completed_with_errors", "failed", "cancelled", "interrupted"].includes(run.status) ? "report" : "live"}`} key={run.id}><b>#{run.id}</b><span className="run-models">{run.models.map((model) => <ModelIdentity compact key={model.id} name={model.name} modelId={model.requested_model_id}/>)}</span><small>{run.case_count} 题 × {run.attempts} 次{run.source_run_id && ` · 来自 #${run.source_run_id}`}</small><StatusPill status={run.status}/></Link>)}</>}
    </section>
  </div>;
}
