import * as Dialog from "@radix-ui/react-dialog";
import { DiffEditor } from "@monaco-editor/react";
import { ChevronDown, Eye, EyeOff, ListTree, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { api } from "../api/client";
import type { CaseRun, CaseRunDetail, ModelRun, QueryPlan, ResultPreview, ScoreBreakdown } from "../types";
import { displayModelName } from "../lib/modelIdentity";

type EligibleRun = { model: ModelRun; run: CaseRun };

function formatCell(value: unknown) {
  if (value === null) return "NULL";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function ResultTable({ preview, label, legend, score, error }: { preview?: ResultPreview | null; label: string; legend: "expected" | "actual"; score?: ScoreBreakdown | null; error?: string | null }) {
  const rows = preview?.rows ?? [];
  const columns = preview?.columns ?? rows[0]?.map((_, index) => ({ name: "列 " + (index + 1), type: "" })) ?? [];
  const extra = new Set(preview?.extra ?? []);
  const missing = preview?.missing ?? [];
  const total = preview?.row_count ?? rows.length;
  const positionMismatch = typeof score?.ordering === "number" && score.ordering < 10;
  return <article className={`result-table-card ${positionMismatch ? "position-mismatch" : ""}`}>
    <header><div><span className={`legend ${legend}`}/><b>{label}</b></div><strong>{legend === "actual" ? (score?.total ?? "—") : "GOLD"}</strong></header>
    {rows.length > 0 ? <div className="result-table-scroll"><table><thead><tr><th>#</th>{columns.map((column, index) => <th key={column.name + "-" + index}><span>{column.name}</span><small>{column.type}</small></th>)}</tr></thead><tbody>{rows.slice(0, 200).map((row, rowIndex) => <tr key={rowIndex} className={extra.has(rowIndex) ? "extra-row" : ""}><td>{rowIndex + 1}</td>{columns.map((_, columnIndex) => <td key={columnIndex}>{formatCell(row[columnIndex])}</td>)}</tr>)}{missing.slice(0, 20).map((index) => <tr className="missing-row" key={"missing-" + index}><td>—</td><td colSpan={Math.max(columns.length, 1)}>缺失期望行 #{index + 1}</td></tr>)}</tbody></table></div> : <div className="result-empty">{error ?? (legend === "expected" ? "揭晓 Reference 后展示固定金标结果" : "暂无可预览结果")}</div>}
    <footer><span>显示 {Math.min(rows.length, 200)} / {total} 行</span><span>{legend === "actual" ? `${missing.length} 缺失 · ${extra.size} 多余${positionMismatch ? " · 顺序不一致" : ""}` : preview?.digest?.slice(0, 12) ?? "固定快照"}</span></footer>
  </article>;
}

function PlanEvidence({ plan, assumptions }: { plan?: QueryPlan | null; assumptions?: string[] | null }) {
  if (!plan) return <div className="result-empty">本次输出未形成有效查询规划</div>;
  const groups = [["数据源", plan.sources], ["连接", plan.joins], ["过滤", plan.filters], ["指标", plan.metrics], ["风险", plan.risks]] as const;
  return <div className="plan-evidence">
    <div className="plan-grain"><small>目标粒度</small><b>{plan.grain}</b></div>
    <ol>{plan.steps.map((step, index) => <li key={index}><span>{String(index + 1).padStart(2, "0")}</span>{step}</li>)}</ol>
    <div className="plan-groups">{groups.map(([label, values]) => <div key={label}><small>{label}</small>{values.length ? values.map((value) => <span key={value}>{value}</span>) : <span>无</span>}</div>)}</div>
    <div className="assumption-list"><small>显式假设</small>{assumptions?.length ? assumptions.map((value) => <span key={value}>{value}</span>) : <span>无额外假设</span>}</div>
  </div>;
}

export function SqlWorkspace({ open, onOpenChange, models, selectedCase }: { open: boolean; onOpenChange: (value: boolean) => void; models: ModelRun[]; selectedCase: string | null }) {
  const allRuns = useMemo(() => models.flatMap((model) => model.cases.filter((run) => run.stable_key === selectedCase).map((run) => ({ model, run }))), [models, selectedCase]);
  const availableAttempts = useMemo(() => [...new Set(allRuns.map(({ run }) => run.attempt))].sort((a, b) => a - b), [allRuns]);
  const [attempt, setAttempt] = useState<number | null>(null);
  const eligible = useMemo(() => models.map((model) => ({ model, run: [...model.cases].reverse().find((item) => item.stable_key === selectedCase && (attempt === null || item.attempt === attempt)) })).filter((item): item is EligibleRun => Boolean(item.run)), [attempt, models, selectedCase]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [detail, setDetail] = useState<CaseRunDetail | null>(null);
  const [showReference, setShowReference] = useState(false);

  useEffect(() => {
    if (!open || availableAttempts.length === 0) return;
    const completed = allRuns.filter(({ run }) => run.status === "completed").map(({ run }) => run.attempt);
    const preferred = Math.max(...(completed.length ? completed : availableAttempts));
    setAttempt((value) => availableAttempts.includes(value ?? -1) ? value : preferred);
  }, [allRuns, availableAttempts, open]);

  useEffect(() => {
    if (!open || eligible.length === 0) return;
    const nextId = eligible.some(({ run }) => run.id === selectedRunId) ? selectedRunId! : eligible[0].run.id;
    setSelectedRunId(nextId);
    setShowReference(false);
    let active = true;
    api.caseRun(nextId, false).then((value) => { if (active) setDetail(value); }).catch((error: Error) => toast.error(error.message));
    return () => { active = false; };
  }, [eligible, open, selectedRunId]);

  const selected = eligible.find(({ run }) => run.id === selectedRunId);
  const revealReference = async () => {
    if (!selectedRunId || !detail) return;
    if (!showReference && !detail.reference_sql) {
      try { setDetail(await api.caseRun(selectedRunId, true)); } catch (error) { toast.error((error as Error).message); return; }
    }
    setShowReference(!showReference);
  };

  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="sheet-overlay"/><Dialog.Content className="sql-sheet">
    <div className="sheet-head"><div><Dialog.Title>查询规划 / SQL / 结果证据</Dialog.Title><Dialog.Description>{detail?.title ?? selectedCase ?? "请选择题目"} · {detail?.question ?? "全过程证据随运行持久化"}</Dialog.Description></div><Dialog.Close className="icon-only"><X/></Dialog.Close></div>
    <div className="workspace-toolbar">
      <label>模型作答<select value={selectedRunId ?? ""} onChange={(event) => setSelectedRunId(Number(event.target.value))}>{eligible.map(({ model, run }) => <option key={run.id} value={run.id}>{displayModelName(model.name)} · A{run.attempt}</option>)}</select><ChevronDown/></label>
      {availableAttempts.length > 1 && <div className="attempt-switch" aria-label="选择作答轮次">{availableAttempts.map((value) => <button className={attempt === value ? "active" : ""} key={value} onClick={() => setAttempt(value)}>A{value}</button>)}</div>}
      <span className="evidence-status"><ListTree/>{selected?.run.status ?? "queued"}</span>
      <button className="button ghost" disabled={!detail || detail.status !== "completed"} onClick={revealReference}>{showReference ? <EyeOff/> : <Eye/>}{showReference ? "隐藏 Reference" : "揭晓 Reference"}</button>
    </div>
    <section className="process-evidence"><header><small>01 / PLAN</small><h3>模型显式查询规划</h3></header><PlanEvidence plan={detail?.plan} assumptions={detail?.assumptions}/><details><summary>查看模型实际收到的 Prompt</summary><pre>{detail?.prompt ?? "等待 Prompt 构建"}</pre></details><details><summary>查看模型原始结构化输出</summary><pre>{detail?.raw_output ?? "暂无原始输出"}</pre></details></section>
    <section className="sql-evidence"><header><small>02 / SQL</small><h3>{displayModelName(selected?.model.name ?? "模型")} SQL {showReference ? "对比 Reference" : "（Reference 未揭晓）"}</h3></header><div className="diff-frame"><DiffEditor original={detail?.formatted_sql ?? "-- 等待模型作答"} modified={showReference ? detail?.reference_sql ?? "-- Reference 不可用" : "-- Reference 默认隐藏"} language="sql" theme="vs-dark" options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false }, fontFamily: "Maple Mono CN", fontSize: 14, lineNumbersMinChars: 3, padding: { top: 16 } }}/></div></section>
    <section className="result-evidence"><header><small>03 / RESULT</small><h3>固定金标与实际执行结果</h3></header><div className="result-tables"><ResultTable preview={showReference ? detail?.expected_result_preview : null} label="固定金标结果" legend="expected"/><ResultTable preview={detail?.result_preview} label={displayModelName(selected?.model.name ?? "模型") + " 实际结果"} legend="actual" score={detail?.score} error={detail?.error_message}/></div><div className="breakdown">{Object.entries(detail?.score ?? {}).filter(([key, value]) => key !== "total" && typeof value === "number").slice(0, 8).map(([key, value]) => <span key={key}>{key}<b>{String(value)}</b></span>)}</div></section>
  </Dialog.Content></Dialog.Portal></Dialog.Root>;
}
