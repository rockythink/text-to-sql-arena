import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, KeyRound, Plus, RefreshCw, Terminal, Trash2 } from "lucide-react";
import { FormEvent, useState } from "react";
import { toast } from "sonner";
import { api } from "../api/client";
import { EmptyState, PageHeader, StatusPill } from "../components/AppShell";

const adapters = [
  ["codex_cli", "Codex CLI", "本机订阅 / Seatbelt"],
  ["gemini_cli", "Gemini CLI", "本机订阅 / stdin"],
  ["claude_cli", "Claude Code", "未安装时明确门禁"],
  ["openai_compatible", "OpenAI Compatible", "API Key / 环境变量"],
] as const;

export function ModelsPage() {
  const queryClient = useQueryClient();
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const [showForm, setShowForm] = useState(false);
  const create = useMutation({ mutationFn: api.createProfile, onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["profiles"] }); setShowForm(false); toast.success("模型选手已登记"); }, onError: (error: Error) => toast.error(error.message) });
  const check = useMutation({ mutationFn: api.checkProfile, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profiles"] }), onError: (error: Error) => toast.error(error.message) });
  const remove = useMutation({ mutationFn: api.deleteProfile, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profiles"] }) });
  const submit = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const data = new FormData(event.currentTarget); const kind = String(data.get("adapter_kind")); create.mutate({ name: data.get("name"), adapter_kind: kind, model_id: data.get("model_id"), base_url: data.get("base_url") || null, response_mode: kind.endsWith("_cli") ? "text" : "json_schema", api_key_env: data.get("api_key_env") || null, parameters: {} }); };
  return <div className="page models-page"><PageHeader eyebrow="ROSTER / MODELS" title="模型选手席" description="先验身份、版本和调用链，再谈谁赢。" actions={<button className="button primary" onClick={() => setShowForm(!showForm)}><Plus/>登记选手</button>}/>
    {showForm && <form className="profile-form" onSubmit={submit}><div className="form-grid"><label>显示名称<input name="name" required placeholder="例如 Codex · GPT-5.6"/></label><label>接入方式<select name="adapter_kind">{adapters.map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>Model ID<input name="model_id" required placeholder="gpt-5.6-sol"/></label><label>Base URL（API 可选）<input name="base_url" placeholder="https://…/v1"/></label><label>Key 环境变量<input name="api_key_env" placeholder="OPENAI_API_KEY"/></label></div><div className="form-actions"><button type="button" className="button ghost" onClick={() => setShowForm(false)}>取消</button><button className="button primary" disabled={create.isPending}>保存并待检</button></div></form>}
    {!profiles.data?.length ? <EmptyState icon={<Terminal/>} title="选手席还是空的" body="登记本机 CLI 或 OpenAI-compatible 模型，然后执行健康检查。"/> : <div className="profile-list">{profiles.data.map((profile) => <article className="profile-row" key={profile.id}><div className="profile-icon"><Terminal/></div><div className="profile-main"><div><h3>{profile.name}</h3><StatusPill status={profile.health_status}/></div><code>{profile.model_id}</code><p>{profile.adapter_kind} · {profile.response_mode}</p></div><div className="health-detail"><small>INSTALL / TRANSPORT</small><b>{String(profile.health_details.version ?? profile.health_details.command ?? "待检查")}</b><span><KeyRound/> {profile.secret_backend === "none" ? "无需密钥" : profile.secret_backend}</span></div><div className="row-actions"><button className="button ghost" disabled={check.isPending} onClick={() => check.mutate(profile.id)}>{check.isPending && check.variables === profile.id ? <RefreshCw className="spin"/> : <Activity/>}健康检查</button><button className="icon-only danger" aria-label="删除模型" onClick={() => remove.mutate(profile.id)}><Trash2/></button></div></article>)}</div>}
  </div>;
}
