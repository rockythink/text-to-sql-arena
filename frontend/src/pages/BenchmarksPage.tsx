import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, CheckCircle2, CopyPlus, Database, FileCode2, Hash, PencilLine } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../api/client";
import { EmptyState, PageHeader, StatusPill } from "../components/AppShell";

export function BenchmarksPage() {
  const suites = useQuery({ queryKey: ["suites"], queryFn: api.suites });
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const clone = useMutation({ mutationFn: ({ suiteId, versionId }: { suiteId: number; versionId: number }) => api.cloneSuite(suiteId, versionId), onSuccess: (result) => { queryClient.invalidateQueries({ queryKey: ["suites"] }); navigate(`/benchmarks/${result.suite_version_id}/edit`); }, onError: (error: Error) => toast.error(error.message) });
  return <div className="page benchmarks-page"><PageHeader eyebrow="BENCHMARKS / FIXTURES" title="基准赛题库" description="发布后内容哈希锁定。复制出 draft，修改、校验，再发布新版本。"/>
    {!suites.data?.length ? <EmptyState icon={<Boxes/>} title="没有基准赛题" body="首个基准包应包含 Schema、Seed、语义层、Prompt 与客观用例。"/> : <div className="suite-list">{suites.data.map((suite) => <article className="suite-card" key={suite.id}><header><div className="suite-icon"><Database/></div><div><small>TEXT-TO-SQL SUITE</small><h2>{suite.name}</h2><p>{suite.description}</p></div><b>{suite.versions.length} VERSION{suite.versions.length > 1 ? "S" : ""}</b></header><div className="version-list">{[...suite.versions].reverse().map((version) => <div className="version-row" key={version.id}><div className="version-tag">v{version.version}</div><div className="version-main"><div><StatusPill status={version.status}/><span>{version.cases.length} 题</span><span>{version.structure.tables?.length ?? 0} 张表</span></div><code><Hash/>{version.content_hash?.slice(0,16) ?? "draft / not locked"}</code></div><div className="version-proof"><span><FileCode2/> DuckDB</span>{version.published_at && <span><CheckCircle2/> {new Date(version.published_at).toLocaleDateString("zh-CN")}</span>}</div><div className="row-actions"><Link className="button ghost" to={`/benchmarks/${version.id}/edit`}><PencilLine/>{version.status === "draft" ? "继续编辑" : "查看"}</Link><button className="button ghost" disabled={clone.isPending} onClick={() => clone.mutate({ suiteId: suite.id, versionId: version.id })}><CopyPlus/>复制 Draft</button></div></div>)}</div></article>)}</div>}
  </div>;
}
