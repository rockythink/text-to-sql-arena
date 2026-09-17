import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, KeyRound, Plus, Terminal, Trash2 } from "lucide-react";
import { FormEvent, useState } from "react";
import { toast } from "sonner";
import { api } from "../api/client";
import { EmptyState, PageHeader, StatusPill } from "../components/AppShell";
import { ModelLogo } from "../components/ModelIdentity";
import { displayModelName } from "../lib/modelIdentity";
import type { ModelProfile } from "../types";

const apiProviders = [
  ["openai", "OpenAI"],
  ["anthropic", "Anthropic"],
  ["google", "Google"],
  ["custom", "自定义 Provider"],
] as const;

const parameterText = (profile: ModelProfile, key: string) => {
  const value = profile.parameters[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
};

export function ModelsPage() {
  const queryClient = useQueryClient();
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const [showForm, setShowForm] = useState(false);
  const [providerChoice, setProviderChoice] = useState("openai-codex");
  const isOAuth = providerChoice === "openai-codex";
  const create = useMutation({ mutationFn: api.createProfile, onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["profiles"] }); setShowForm(false); toast.success("模型配置已添加"); }, onError: (error: Error) => toast.error(error.message) });
  const check = useMutation({ mutationFn: api.checkProfile, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profiles"] }), onError: (error: Error) => toast.error(error.message) });
  const remove = useMutation({ mutationFn: api.deleteProfile, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profiles"] }) });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const provider = providerChoice === "custom" ? String(data.get("custom_provider") ?? "").trim() : providerChoice;
    const rate = (name: string) => { const value = String(data.get(name) ?? "").trim(); return value ? Number(value) : null; };
    const apiKey = String(data.get("api_key") ?? "").trim();
    const apiKeyEnv = String(data.get("api_key_env") ?? "").trim();
    if (!isOAuth && !apiKey && !apiKeyEnv) { toast.error("API 接入需要 API Key 或环境变量引用"); return; }
    if (!isOAuth && apiKey && apiKeyEnv) { toast.error("API Key 与环境变量引用只能填写一项"); return; }
    const optionalNumber = (name: string) => { const value = String(data.get(name) ?? "").trim(); return value ? Number(value) : undefined; };
    const rates = {
      input_usd_per_million: rate("input_price"),
      cached_input_usd_per_million: rate("cached_input_price"),
      cache_write_input_usd_per_million: rate("cache_write_price"),
      output_usd_per_million: rate("output_price"),
    };
    const hasPricing = Object.values(rates).some((value) => value !== null);
    const parameters: Record<string, unknown> = { provider, auth_mode: isOAuth ? "oauth" : "api_key", timeout_seconds: 180 };
    const temperature = optionalNumber("temperature");
    const maxTokens = optionalNumber("max_tokens");
    const reasoningEffort = String(data.get("reasoning_effort") ?? "").trim();
    if (temperature !== undefined) parameters.temperature = temperature;
    if (maxTokens !== undefined) parameters.max_tokens = maxTokens;
    if (reasoningEffort) parameters.reasoning_effort = reasoningEffort;
    create.mutate({
      name: data.get("name"), adapter_kind: "pi", model_id: data.get("model_id"),
      base_url: isOAuth ? null : data.get("base_url") || null,
      response_mode: "text",
      api_key: isOAuth ? null : apiKey || null,
      api_key_env: isOAuth ? null : apiKeyEnv || null,
      parameters,
      pricing: hasPricing ? { currency: "USD", ...rates, source: data.get("price_source") || "manual", effective_at: data.get("price_date") || new Date().toISOString().slice(0, 10) } : null,
    });
  };

  return <div className="page models-page"><PageHeader eyebrow="模型配置" title="模型与统一调用配置" description="新配置统一通过 Pi 单轮文本调用；历史接入只保留查看与删除。" actions={<button className="button primary" onClick={() => setShowForm(!showForm)}><Plus/>添加模型</button>}/>
    {showForm && <form className="profile-form" onSubmit={submit}>
      <div className="form-grid">
        <label>显示名称<input name="name" required placeholder="例如 GPT · 订阅账号"/></label>
        <label>Provider<select name="provider" value={providerChoice} onChange={(event) => setProviderChoice(event.target.value)}><option value="openai-codex">OpenAI Codex（订阅 OAuth）</option>{apiProviders.map(([value, label]) => <option key={value} value={value}>{label}（API Key）</option>)}</select></label>
        {providerChoice === "custom" && <label>自定义 Provider 标识<input name="custom_provider" required placeholder="provider-id"/></label>}
        <label>Model ID<input name="model_id" list={isOAuth ? "codex-models" : undefined} required placeholder={isOAuth ? "gpt-5.6-sol" : "Provider 支持的模型 ID"}/>{isOAuth && <datalist id="codex-models"><option value="gpt-5.6-luna"/><option value="gpt-5.6-sol"/></datalist>}</label>
        <label>认证方式<input value={isOAuth ? "OAuth（订阅凭据）" : "API Key"} readOnly/></label>
        {!isOAuth && <><label>Base URL（可选）<input name="base_url" placeholder="协议对应的 API 根地址；默认可留空"/></label><label>API Key（安全写入系统钥匙串）<input name="api_key" type="password" autoComplete="off" placeholder="与环境变量二选一"/></label><label>Key 环境变量<input name="api_key_env" placeholder="与 API Key 二选一"/></label></>}
        <label>Temperature（可选）<input name="temperature" type="number" step="any"/></label>
        {isOAuth ? <label>输出上限<input value="由 OpenAI Codex Provider 管理" readOnly/></label> : <label>Max tokens（可选）<input name="max_tokens" type="number" min="1" step="1"/></label>}
        <label>Reasoning effort（可选）<select name="reasoning_effort"><option value="">使用 Provider 默认值</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></label>
        <label>调用超时<input value="180 秒（固定）" readOnly/></label>
        {!isOAuth && <><label>输入价 USD / 百万 Token<input name="input_price" type="number" min="0" step="any" placeholder="不填则不估算"/></label>
        <label>缓存输入价 USD / 百万 Token<input name="cached_input_price" type="number" min="0" step="any" placeholder="可选"/></label>
        <label>缓存写入价 USD / 百万 Token<input name="cache_write_price" type="number" min="0" step="any" placeholder="可选"/></label>
        <label>输出价 USD / 百万 Token<input name="output_price" type="number" min="0" step="any" placeholder="不填则不估算"/></label>
        <label>价格来源<input name="price_source" placeholder="manual / 官方价格页"/></label>
        <label>价格生效日期<input name="price_date" type="date"/></label></>}
      </div>
      {isOAuth ? <p className="form-note">“检查本地配置”只确认本地 catalog 与凭据就绪，不调用模型、也不证明 Provider 可用。凭据仅从支持的外部 Pi CLI 登录或既有 Codex 登录文件导入系统钥匙串；不读取配置、工具或会话。</p> : <p className="form-note">“检查本地配置”不发起生成。内置选择仅表示当前支持的 API 接入类别，不承诺覆盖 Provider 的全部模型目录；密钥与环境变量引用二选一。</p>}
      <div className="form-actions"><button className="button primary" disabled={create.isPending}>保存配置</button><button className="button ghost" type="button" onClick={() => setShowForm(false)}>取消</button></div>
    </form>}
    {!profiles.data?.length ? <EmptyState icon={<Terminal/>} title="没有模型配置" body="添加 Pi 模型配置；真实运行前先检查本地 catalog、凭据与参数是否就绪。"/> : <div className="profile-list">{[...profiles.data].sort((a, b) => Number(b.adapter_kind === "pi") - Number(a.adapter_kind === "pi")).map((profile) => {
      const current = profile.adapter_kind === "pi";
      const provider = parameterText(profile, "provider");
      const authMode = parameterText(profile, "auth_mode");
      return <article className={`profile-row ${current ? "" : "historical-profile"}`} key={profile.id}><div className="profile-icon"><ModelLogo name={profile.name} modelId={profile.model_id} adapterKind={profile.adapter_kind}/></div><div className="profile-main"><div><h3>{displayModelName(profile.name)}</h3><StatusPill status={profile.health_status}/>{!current && <span className="historical-badge">历史配置 · 仅查看</span>}</div><code>{profile.model_id}</code><p>{current ? "Pi · " : ""}{profile.response_mode}{provider ? " · " : ""}{provider}{authMode ? " · " : ""}{authMode}</p></div><div className="health-detail"><small>{current ? "PI / LOCAL READINESS" : "HISTORICAL ADAPTER"}</small><b>{String(profile.health_details.version ?? profile.health_details.command ?? (current ? "待检查" : profile.adapter_kind))}</b><span><KeyRound/> {profile.has_secret ? "凭据已安全引用" : authMode === "oauth" ? (profile.health_status === "healthy" ? "OAuth 已存系统钥匙串" : "需检查 OAuth") : "未配置密钥"}</span>{current && profile.health_status === "unavailable" && authMode === "oauth" && <span>请先用支持的外部 Pi CLI 或既有 Codex 登录完成 OAuth，再检查本地配置。</span>}</div><div className="profile-actions">{current && <button className="button" disabled={check.isPending} onClick={() => check.mutate(profile.id)}><Activity/>检查本地配置</button>}<button className="icon-button danger" aria-label={`删除 ${profile.name}`} onClick={() => remove.mutate(profile.id)}><Trash2/></button></div></article>;
    })}</div>}
  </div>;
}
