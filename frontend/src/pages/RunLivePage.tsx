import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Ban, CircleDot, ExternalLink, Filter, Radio, RotateCcw, Search, TerminalSquare } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { api, eventStream, type EventHistoryQuery } from "../api/client";
import { PageHeader, Scoreboard, StatusPill } from "../components/AppShell";
import { SqlWorkspace } from "../components/SqlWorkspace";
import { useArenaStore } from "../store";
import type { RunEvent } from "../types";

const terminal = new Set(["completed", "completed_with_errors", "cancelled", "failed", "interrupted"]);
const statusTitles: Record<string, string> = {
  cancelling: "正在安全收场",
  completed: "本场对局已结束",
  completed_with_errors: "本场对局已结束",
  cancelled: "本场已取消",
  failed: "本场无人完赛",
  interrupted: "本场被意外中断",
};

function toggleValue<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

export function RunLivePage() {
  const runId = Number(useParams().id);
  const queryClient = useQueryClient();
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api.run(runId), refetchInterval: (query) => terminal.has(query.state.data?.status ?? "") ? false : 1200 });
  const events = useArenaStore((state) => state.events);
  const hydrateEvents = useArenaStore((state) => state.hydrateEvents);
  const appendEvent = useArenaStore((state) => state.appendEvent);
  const reconnecting = useArenaStore((state) => state.reconnecting);
  const setReconnecting = useArenaStore((state) => state.setReconnecting);
  const [selectedCase, setSelectedCase] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState(false);
  const [search, setSearch] = useState("");
  const [levels, setLevels] = useState<string[]>([]);
  const [modelFilters, setModelFilters] = useState<number[]>([]);
  const [caseFilter, setCaseFilter] = useState("all");
  const [eventType, setEventType] = useState("all");
  const [follow, setFollow] = useState(true);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [serverResult, setServerResult] = useState<{ events: RunEvent[]; total: number } | null>(null);
  const [serverLoading, setServerLoading] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let closeStream: () => void = () => undefined;
    let active = true;
    api.history(runId).then((result) => {
      if (!active) return;
      hydrateEvents(result.events);
      setHistoryTotal(result.total);
      const last = result.events.at(-1)?.seq ?? 0;
      closeStream = eventStream(runId, last, (event) => {
        appendEvent(event);
        setHistoryTotal((total) => Math.max(total + 1, event.seq));
        setReconnecting(false);
        if (event.event_type.startsWith("run.")) queryClient.invalidateQueries({ queryKey: ["run", runId] });
      }, () => setReconnecting(true));
    }).catch((error: Error) => toast.error(error.message));
    return () => { active = false; closeStream(); };
  }, [appendEvent, hydrateEvents, queryClient, runId, setReconnecting]);

  useEffect(() => {
    if (!selectedCase && run.data?.selected_case_keys[0]) setSelectedCase(run.data.selected_case_keys[0]);
  }, [run.data, selectedCase]);

  const caseRunIds = useMemo(() => caseFilter === "all" ? [] : (run.data?.models.flatMap((model) => model.cases.filter((item) => item.stable_key === caseFilter).map((item) => item.id)) ?? []), [caseFilter, run.data]);
  const availableEventTypes = useMemo(() => [...new Set(events.map((event) => event.event_type))].sort(), [events]);
  const queryFor = useCallback((offset = 0): EventHistoryQuery => ({
    modelRunIds: modelFilters,
    caseRunIds,
    levels,
    eventTypes: eventType === "all" ? [] : [eventType],
    search: search.trim() || undefined,
    offset,
    limit: 5000,
  }), [caseRunIds, eventType, levels, modelFilters, search]);

  useEffect(() => {
    if (historyTotal <= 5000) {
      setServerResult(null);
      return;
    }
    let active = true;
    const timer = window.setTimeout(() => {
      setServerLoading(true);
      api.history(runId, queryFor()).then((result) => {
        if (active) setServerResult(result);
      }).catch((error: Error) => toast.error(error.message)).finally(() => {
        if (active) setServerLoading(false);
      });
    }, 200);
    return () => { active = false; window.clearTimeout(timer); };
  }, [historyTotal, queryFor, runId]);

  const filtered = useMemo(() => {
    const caseIds = new Set(caseRunIds);
    const match = (event: RunEvent) =>
      (!modelFilters.length || (event.model_run_id != null && modelFilters.includes(event.model_run_id))) &&
      (!caseIds.size || !event.case_run_id || caseIds.has(event.case_run_id)) &&
      (!levels.length || levels.includes(event.level)) &&
      (eventType === "all" || event.event_type === eventType) &&
      (!search || `${event.message} ${event.event_type}`.toLowerCase().includes(search.toLowerCase()));
    const source = serverResult
      ? [...new Map([...serverResult.events, ...events.filter((event) => event.seq > (serverResult.events.at(-1)?.seq ?? 0))].map((event) => [event.seq, event])).values()]
      : events;
    return source.filter(match);
  }, [caseRunIds, eventType, events, levels, modelFilters, search, serverResult]);

  const virtualizer = useVirtualizer({ count: filtered.length, getScrollElement: () => logRef.current, estimateSize: () => 30, overscan: 12 });
  useEffect(() => { if (follow && filtered.length) virtualizer.scrollToIndex(filtered.length - 1, { align: "end" }); }, [filtered.length, follow, virtualizer]);

  const snapshot = run.data;
  if (!snapshot) return <div className="loading-screen"><Radio className="spin"/>正在读取擂台…</div>;
  const cases = snapshot.selected_case_keys;
  const cancel = async () => { try { await api.cancelRun(runId); await queryClient.invalidateQueries({ queryKey: ["run", runId] }); } catch (error) { toast.error((error as Error).message); } };
  const clearFilters = () => { setSearch(""); setLevels([]); setModelFilters([]); setCaseFilter("all"); setEventType("all"); };
  const loadMore = async () => {
    if (!serverResult) return;
    setServerLoading(true);
    try {
      const next = await api.history(runId, queryFor(serverResult.events.length));
      setServerResult({ events: [...serverResult.events, ...next.events], total: next.total });
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setServerLoading(false);
    }
  };

  return <div className="page live-page">
    <PageHeader eyebrow={`MATCH #${runId} / ${terminal.has(snapshot.status) ? "FINAL" : "LIVE"}`} title={statusTitles[snapshot.status] ?? "数据擂台正在开打"} actions={<>{terminal.has(snapshot.status) && <Link className="button primary" to={`/runs/${runId}/report`}>查看报告<ExternalLink/></Link>}<button className="button danger" disabled={terminal.has(snapshot.status)} onClick={cancel}><Ban/>取消运行</button></>}/>
    <Scoreboard suiteHash={snapshot.suite_content_hash} models={snapshot.models.map((model) => ({ id: model.id, name: model.name, score: model.official_score, status: model.status }))}/>
    <div className="live-workspace">
      <section className={`model-lanes lanes-${Math.min(snapshot.models.length, 4)}`}>{snapshot.models.map((model) => <article className="model-lane" key={model.id}><header><div><span className="lane-mark">{model.adapter_kind.slice(0,2).toUpperCase()}</span><div><h3>{model.name}</h3><code>{model.resolved_model_id ?? model.requested_model_id}</code></div></div><StatusPill status={model.status}/></header><div className="case-grid">{cases.map((key, index) => { const attempts = model.cases.filter((item) => item.stable_key === key); const result = attempts.at(-1); return <button key={key} className={`case-tile ${selectedCase === key ? "active" : ""} status-${result?.status ?? "queued"}`} onClick={() => { setSelectedCase(key); if (result) setWorkspace(true); }}><span>{String(index + 1).padStart(2,"0")}</span><div><b>{result?.title ?? key}</b><small>{result ? `A${result.attempt} · ${result.status}` : "候场"}</small></div><em>{result?.score?.total == null ? "—" : Number(result.score.total).toFixed(0)}</em></button>; })}</div><footer><span><CircleDot/> {model.cli_version ?? model.response_mode}</span><b>{model.cases.filter((item) => item.status === "completed").length}/{cases.length * snapshot.attempts}</b></footer></article>)}</section>
      <section className="log-panel">
        <header><div className="log-title"><TerminalSquare/><div><b>事件直播台</b><small>{historyTotal} 条持久化事件 · {filtered.length} 条匹配 {reconnecting && "· 正在重连"}</small></div></div>
          <div className="log-filters"><Filter/>
            <details className="multi-filter"><summary>{modelFilters.length ? `模型 ${modelFilters.length}` : "全部模型"}</summary><div>{snapshot.models.map((model) => <label key={model.id}><input type="checkbox" checked={modelFilters.includes(model.id)} onChange={() => setModelFilters((values) => toggleValue(values, model.id))}/>{model.name}</label>)}</div></details>
            <select aria-label="Case 筛选" value={caseFilter} onChange={(event) => setCaseFilter(event.target.value)}><option value="all">全部 Case</option>{cases.map((key, index) => <option value={key} key={key}>{String(index + 1).padStart(2,"0")} · {key}</option>)}</select>
            <details className="multi-filter"><summary>{levels.length ? `级别 ${levels.length}` : "全部级别"}</summary><div>{["info","warning","error"].map((value) => <label key={value}><input type="checkbox" checked={levels.includes(value)} onChange={() => setLevels((items) => toggleValue(items, value))}/>{value}</label>)}</div></details>
            <select aria-label="事件类型筛选" value={eventType} onChange={(event) => setEventType(event.target.value)}><option value="all">全部事件</option>{availableEventTypes.map((value) => <option value={value} key={value}>{value}</option>)}</select>
            <label><Search/><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索日志"/></label>
            <button onClick={clearFilters}><RotateCcw/>清空</button>
          </div>
        </header>
        <div className="virtual-log" ref={logRef} onScroll={() => { const node = logRef.current; if (node) setFollow(node.scrollHeight - node.scrollTop - node.clientHeight < 32); }}>
          {filtered.length === 0 && <div className="log-empty">{serverLoading ? "正在加载筛选结果…" : "暂无匹配日志"}</div>}
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>{virtualizer.getVirtualItems().map((row) => { const event = filtered[row.index]; const model = snapshot.models.find((item) => item.id === event.model_run_id); return <div className={`log-row level-${event.level}`} key={event.seq} style={{ position: "absolute", transform: `translateY(${row.start}px)`, width: "100%" }}><time>{new Date(event.created_at).toLocaleTimeString("zh-CN", { hour12: false })}</time><span>{event.event_type}</span><b>{model?.name ?? "SYSTEM"}</b><p>{event.message}</p></div>; })}</div>
        </div>
        {serverResult && serverResult.events.length < serverResult.total && <button className="load-more-events" disabled={serverLoading} onClick={loadMore}>{serverLoading ? "加载中…" : `加载更多（${serverResult.events.length}/${serverResult.total}）`}</button>}
        {!follow && <button className="back-latest" onClick={() => setFollow(true)}>回到最新 ↓</button>}
      </section>
    </div>
    <SqlWorkspace open={workspace} onOpenChange={setWorkspace} models={snapshot.models} selectedCase={selectedCase}/>
  </div>;
}
