import Editor from "@monaco-editor/react";
import { Background, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Braces, CheckCircle2, CopyPlus, Database, Eye, FileCode2, GitFork, Save } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../api/client";
import { PageHeader, StatusPill } from "../components/AppShell";

const tabs = [["schema", "Schema", "sql"], ["seed", "Seed", "sql"], ["semantic", "语义层", "json"], ["prompt", "Prompt", "markdown"], ["cases", "用例", "json"]] as const;

export function BenchmarkEditPage() {
  const versionId = Number(useParams().id);
  const suites = useQuery({ queryKey: ["suites"], queryFn: api.suites });
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const located = useMemo(() => suites.data?.flatMap((suite) => suite.versions.map((version) => ({ suite, version }))).find((item) => item.version.id === versionId), [suites.data, versionId]);
  const [tab, setTab] = useState<(typeof tabs)[number][0]>("schema");
  const [values, setValues] = useState({ schema: "", seed: "", semantic: "{}", prompt: "", cases: "[]" });
  const [previewCaseId, setPreviewCaseId] = useState<number | null>(null);

  useEffect(() => {
    if (!located) return;
    const cases = located.version.cases.map(({ id, ...item }) => { void id; return item; });
    setValues({ schema: located.version.schema_sql, seed: located.version.seed_sql, semantic: JSON.stringify(located.version.semantic, null, 2), prompt: located.version.prompt_template, cases: JSON.stringify(cases, null, 2) });
    setPreviewCaseId((current) => located.version.cases.some((item) => item.id === current) ? current : (located.version.cases[0]?.id ?? null));
  }, [located]);

  const promptPreview = useQuery({
    queryKey: ["prompt-preview", versionId, previewCaseId],
    queryFn: () => api.promptPreview(versionId, previewCaseId!),
    enabled: Boolean(previewCaseId && located?.version.structure.tables?.length),
  });
  const editable = located?.version.status === "draft";
  const save = useMutation({ mutationFn: () => api.patchSuite(versionId, { schema_sql: values.schema, seed_sql: values.seed, semantic: JSON.parse(values.semantic), prompt_template: values.prompt, cases: JSON.parse(values.cases).map((item: Record<string, unknown>) => { const { id, ...rest } = item; void id; return rest; }) }), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["suites"] }); queryClient.invalidateQueries({ queryKey: ["prompt-preview"] }); toast.success("Draft 已保存"); }, onError: (error: Error) => toast.error(error.message) });
  const publish = useMutation({ mutationFn: () => api.publishSuite(versionId), onSuccess: (result) => { queryClient.invalidateQueries({ queryKey: ["suites"] }); queryClient.invalidateQueries({ queryKey: ["prompt-preview"] }); toast.success(`发布成功 · ${result.content_hash.slice(0, 12)}`); }, onError: (error: Error) => toast.error(error.message) });
  const clone = useMutation({ mutationFn: () => api.cloneSuite(located!.suite.id, versionId), onSuccess: (result) => navigate(`/benchmarks/${result.suite_version_id}/edit`) });

  if (!located) return <div className="loading-screen"><Database/>加载基准版本…</div>;
  const current = tabs.find(([key]) => key === tab)!;
  const source = values[tab];
  const setSource = (value = "") => setValues((state) => ({ ...state, [tab]: value }));
  const tables = located.version.structure.tables ?? [];
  const relationships = tables.flatMap((table) =>
    (table.foreign_keys ?? []).map((foreignKey, index) => ({ table, foreignKey, index })),
  );
  const tableNodes: Node[] = tables.map((table, index) => ({
    id: table.name,
    position: { x: (index % 2) * 225, y: Math.floor(index / 2) * 105 },
    data: { label: <div className="er-node"><b>{table.name}</b><span>{(table.columns ?? []).slice(0, 3).map((column) => column.name).join(", ")}</span><span>{table.columns?.length ?? 0} columns · {table.foreign_keys?.length ?? 0} FK</span></div> },
    style: { background: "#171A18", border: "1px solid #ffffff22", borderRadius: 12, color: "#F4F1E8", width: 185 },
  }));
  const edges: Edge[] = relationships.map(({ table, foreignKey, index }) => ({
    id: "fk-" + table.name + "-" + index,
    source: table.name,
    target: foreignKey.referenced_table,
    markerEnd: { type: MarkerType.ArrowClosed },
    style: { stroke: "#6fd8cf", strokeWidth: 1.5 },
  }));

  return <div className="page edit-page">
    <PageHeader eyebrow={`题库版本 V${located.version.version}`} title={located.suite.name} description="编辑题库源文件，并检查结构快照与模型实际输入。" actions={<><StatusPill status={located.version.status}/>{editable ? <><button className="button ghost" onClick={() => save.mutate()} disabled={save.isPending}><Save/>保存草稿</button><button className="button primary" onClick={() => publish.mutate()} disabled={publish.isPending}><CheckCircle2/>校验并发布</button></> : <button className="button primary" onClick={() => clone.mutate()}><CopyPlus/>复制为草稿</button>}</>}/>
    <div className="editor-tabs">{tabs.map(([key, label]) => <button className={tab === key ? "active" : ""} key={key} onClick={() => setTab(key)}>{key === "semantic" || key === "cases" ? <Braces/> : <FileCode2/>}{label}</button>)}</div>
    <div className="editor-layout">
      <section className="source-editor">
        <header><div><small>{current[1]}</small><b>{editable ? "可编辑草稿" : "已发布快照（只读）"}</b></div><span>{current[2]}</span></header>
        <Editor value={source} onChange={setSource} language={current[2]} theme="vs-dark" options={{ readOnly: !editable, minimap: { enabled: false }, fontFamily: "Maple Mono CN", fontSize: 14, wordWrap: "on", padding: { top: 16 }, automaticLayout: true }}/>
      </section>
      <aside className="preview-rail">
        <section className="structure-preview">
          <header><GitFork/><div><small>结构快照</small><b>外键关系</b></div></header>
          <div className="er-flow">{tableNodes.length ? <ReactFlow nodes={tableNodes} edges={edges} fitView fitViewOptions={{ padding: 0.12, maxZoom: 1.05 }} minZoom={0.45} nodesDraggable={false} nodesConnectable={false}><Background gap={18}/></ReactFlow> : <div className="result-empty">发布后生成结构快照</div>}</div>
          <div className="er-relationships">{relationships.map(({ table, foreignKey, index }) => <code key={table.name + "-" + index}>{table.name}.{foreignKey.columns.join(",")} → {foreignKey.referenced_table}.{foreignKey.referenced_columns.join(",")}</code>)}</div>
        </section>
        <section className="prompt-preview">
          <header><Eye/><div><small>运行时输入</small><b>模型实际 Prompt</b></div></header>
          <label className="preview-case-select">题目<select value={previewCaseId ?? ""} onChange={(event) => setPreviewCaseId(Number(event.target.value))}>{located.version.cases.map((item) => <option value={item.id} key={item.id}>{String(item.sort_order).padStart(2, "0")} · {item.title}</option>)}</select></label>
          <pre>{promptPreview.isLoading ? "正在渲染实际 Prompt…" : promptPreview.data?.prompt ?? (promptPreview.error instanceof Error ? promptPreview.error.message : "当前版本没有可用结构快照")}</pre>
        </section>
      </aside>
    </div>
  </div>;
}
