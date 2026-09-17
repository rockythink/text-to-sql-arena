import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { builtinModels } from "@earendil-works/pi-ai/providers/all";
import { createProvider, getSupportedThinkingLevels } from "@earendil-works/pi-ai";
import * as completions from "@earendil-works/pi-ai/api/openai-completions";

const root = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));
const installed = JSON.parse(readFileSync(resolve(dirname(fileURLToPath(import.meta.resolve("@earendil-works/pi-ai"))), "../package.json"), "utf8"));
const policy = JSON.parse(readFileSync(resolve(root, "policy.json"), "utf8"));
const hash = (text) => createHash("sha256").update(text).digest("hex");
const send = (value) => process.stdout.write(`${JSON.stringify(value)}\n`);
const reject = (code, message) => { throw Object.assign(new Error(message), { code }); };
const supportedApis = new Set(["openai-completions", "openai-responses", "openai-codex-responses", "anthropic-messages", "google-generative-ai"]);
let secretValues = [];
let evidence = {};
function safe(value) {
  if (typeof value === "string") {
    for (const secret of secretValues) value = value.replaceAll(secret, "[REDACTED]");
    return value;
  }
  if (Array.isArray(value)) return value.map(safe);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, /^(authorization|api_key|access|refresh|token|secret)$/i.test(k) ? "[REDACTED]" : safe(v)]));
  return value;
}

async function main() {
  if (installed.version !== pkg.dependencies["@earendil-works/pi-ai"]) reject("profile_incompatible", "Pi 依赖版本与锁定版本不一致");
  if (process.argv.includes("--version")) {
    send({ type: "result", version: installed.version, policy_version: policy.version });
    return;
  }
  let input = "";
  for await (const chunk of process.stdin) {
    input += chunk;
    if (Buffer.byteLength(input) > 2 * 1024 * 1024) reject("provider_protocol_error", "Pi 输入过大");
  }
  const req = JSON.parse(input);
  const p = req.parameters;
  let credential = req.credential;
  secretValues = [credential?.key, credential?.access, credential?.refresh].filter(v => typeof v === "string" && v);
  if (!p || !req.model_id || !["check", "auth", "generate"].includes(req.operation)) reject("profile_incompatible", "Pi 请求缺少模型或操作");
  const models = builtinModels({
    authContext: { env: async () => undefined, fileExists: async () => false },
    credentials: {
      read: async (id) => id === p.provider ? credential : undefined,
      list: async () => [{ providerId: p.provider, type: credential.type }],
      modify: async (id, fn) => {
        if (id !== p.provider) reject("provider_auth_error", "OAuth provider 不匹配");
        credential = await fn(credential) ?? credential;
        return credential;
      },
      delete: async () => reject("provider_auth_error", "评测运行不能删除认证信息"),
    },
  });
  let provider = models.getProvider(p.provider);
  let model = models.getModel(p.provider, req.model_id);
  let modelSource = "requested_catalog";
  if (!model && req.base_url && p.auth_mode === "api_key") {
    // An explicitly configured OpenAI-compatible endpoint, not a guessed provider model.
    model = {
      id: req.model_id, name: req.model_id, provider: p.provider, api: "openai-completions",
      baseUrl: req.base_url, reasoning: false, input: ["text"],
      contextWindow: 128000, maxTokens: p.max_tokens ?? 8192,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      compat: { supportsStore: false, supportsDeveloperRole: false, supportsReasoningEffort: false },
    };
    provider = createProvider({ id: p.provider, models: [model], api: completions,
      auth: { apiKey: { resolve: async () => ({ apiKey: credential.key }) } } });
    modelSource = "requested_custom_endpoint";
  }
  if (!provider || !model) reject("profile_incompatible", "锁定 Pi 目录未包含该 provider/model；API 自定义模型需明确配置兼容端点 Base URL");
  if (!supportedApis.has(model.api)) reject("profile_incompatible", `该 Pi 协议尚不满足单次无工具审计合同：${model.api}`);
  if (p.auth_mode === "oauth" && (!provider.auth.oauth || req.base_url)) reject("profile_incompatible", "该 provider 不支持订阅 OAuth，或尝试覆盖订阅端点");
  if (p.auth_mode === "api_key" && credential?.type !== "api_key") reject("provider_auth_error", "缺少 API Key 凭证");
  if (p.auth_mode === "oauth" && credential?.type !== "oauth") reject("provider_auth_error", "缺少 OAuth 凭证");
  if (model.api === "openai-codex-responses" && p.max_tokens !== undefined) reject("profile_incompatible", "Codex 订阅通道不发送 max_tokens");
  if (p.reasoning_effort && (!model.reasoning || !getSupportedThinkingLevels(model).includes(p.reasoning_effort))) reject("profile_incompatible", "该模型不支持请求的 reasoning_effort，禁止静默降档");
  if (req.base_url) model = { ...model, baseUrl: req.base_url };
  evidence = {
    harness: "pi-ai", harness_version: installed.version, policy_version: policy.version,
    bridge_sha256: hash(readFileSync(fileURLToPath(import.meta.url))),
    dependency_lock_sha256: hash(readFileSync(resolve(root, "pnpm-lock.yaml"))),
    system_prompt_sha256: hash(policy.system_prompt), system_prompt: policy.system_prompt,
    tools_enabled: false, tool_count: 0, tool_calls_observed: 0,
    context_isolated: true, context_files_loaded: false, extensions_loaded: false,
    generation_attempt_limit: 1, retry_limit: 0, generation_attempts: 0,
    provider: p.provider, auth_mode: p.auth_mode, api: model.api,
    model_identity_source: modelSource, requested_model_id: req.model_id,
    effective_parameters: { ...p, max_tokens: model.api === "openai-codex-responses" ? "provider_managed" : p.max_tokens ?? Math.min(model.maxTokens, 8192) },
    parameter_notes: model.api === "openai-codex-responses" ? ["输出上限由订阅端点控制，未发送 max_tokens"] : [],
    billing_basis: p.auth_mode === "oauth" ? "subscription_or_provider_extra_usage" : "api",
  };
  if (req.operation === "check") {
    send({ type: "result", evidence });
    return;
  }
  if (req.operation === "auth") {
    if (p.auth_mode !== "oauth") reject("provider_auth_error", "仅 OAuth 使用刷新操作");
    await models.getAuth(p.provider, { signal: AbortSignal.timeout(55000) });
    // Private pipe to Python, which persists this to keyring before generation. Never emit to run events.
    send({ type: "result", credential });
    return;
  }
  const started = performance.now();
  const controller = new AbortController();
  const context = {
    systemPrompt: policy.system_prompt,
    messages: [{ role: "user", content: `${req.prompt}\n\nOutput JSON schema:\n${JSON.stringify(req.output_schema)}`, timestamp: 0 }],
    tools: [],
  };
  let auth = { apiKey: credential.key };
  if (p.auth_mode === "oauth") {
    if (credential.expires <= Date.now()) reject("provider_auth_error", "OAuth 已过期，必须先安全刷新");
    auth = await provider.auth.oauth.toAuth(credential);
    if (auth.baseUrl) model = { ...model, baseUrl: auth.baseUrl };
  }
  const nativeFetch = globalThis.fetch;
  let wirePayload;
  let requestId = null;
  const countedFetch = async (url, init) => {
    if (evidence.generation_attempts !== 0) reject("adapter_policy_violation", "Pi 尝试第二次网络请求，已阻断自动重试");
    if (!wirePayload) reject("adapter_policy_violation", "Pi 未提供可审计的请求载荷");
    evidence.generation_attempts = 1;
    send({ type: "requested", evidence: safe({ ...evidence, wire_payload: wirePayload, wire_payload_sha256: hash(JSON.stringify(wirePayload)) }) });
    const response = await nativeFetch(url, { ...init, redirect: "error" });
    requestId = response.headers.get("x-request-id") ?? response.headers.get("request-id");
    return response;
  };
  // Pi's Google SDK currently ignores options.fetch; the fresh process has no other network tasks.
  // This also prevents hidden SDK retry/fallback paths from issuing a second request.
  globalThis.fetch = countedFetch;
  const options = {
    ...auth, signal: controller.signal, maxRetries: 0, transport: "sse", cacheRetention: "none",
    timeoutMs: p.timeout_seconds * 1000, toolChoice: "none", fetch: countedFetch,
    ...(p.temperature === undefined ? {} : { temperature: p.temperature }),
    ...(model.api === "openai-codex-responses" ? {} : { maxTokens: p.max_tokens ?? Math.min(model.maxTokens, 8192) }),
    ...(p.reasoning_effort ? { reasoning: p.reasoning_effort } : {}),
    onPayload: (body) => {
      if ((body.tools?.length ?? 0) || (body.config?.tools?.length ?? 0)) reject("adapter_policy_violation", "SDK 尝试添加工具声明");
      wirePayload = JSON.parse(JSON.stringify(body));
      evidence.effective_parameters.wire_generation = Object.fromEntries(
        ["temperature", "max_tokens", "max_completion_tokens", "max_output_tokens", "reasoning", "thinking", "generationConfig"]
          .filter(key => body[key] !== undefined).map(key => [key, body[key]]),
      );
      if (body.config) evidence.effective_parameters.wire_generation.config = {
        temperature: body.config.temperature, maxOutputTokens: body.config.maxOutputTokens,
        thinkingConfig: body.config.thinkingConfig,
      };
    },
  };
  const stream = provider.streamSimple(model, context, options);
  let textBytes = 0;
  try {
    for await (const event of stream) {
      if (event.type.startsWith("toolcall_")) {
        evidence.tool_calls_observed += 1;
        controller.abort();
        reject("adapter_policy_violation", "模型尝试调用工具；未执行，未继续对话");
      }
      if (event.type === "text_delta") {
        textBytes += Buffer.byteLength(event.delta);
        if (textBytes > 512 * 1024) {
          controller.abort();
          reject("provider_output_too_large", "模型文本超过 512 KiB");
        }
        send({ type: "delta", text: safe(event.delta) });
      }
    }
    const result = await stream.result();
    if (result.content.some(block => block.type === "toolCall")) reject("adapter_policy_violation", "模型返回工具调用；未执行");
    if (result.stopReason !== "stop") reject(result.stopReason === "length" ? "provider_output_truncated" : "provider_error", result.errorMessage || `模型未正常完成：${result.stopReason}`);
    if (evidence.generation_attempts !== 1) reject("adapter_policy_violation", "不能证明恰好一次模型请求");
    send({ type: "result", raw_output: safe(result.content.filter(b => b.type === "text").map(b => b.text).join("")),
      token_usage: { input_tokens: result.usage.input, output_tokens: result.usage.output,
        cache_read_tokens: result.usage.cacheRead, cache_write_tokens: result.usage.cacheWrite },
      provider_request_id: requestId, latency_ms: performance.now() - started, evidence: safe(evidence) });
  } finally {
    globalThis.fetch = nativeFetch;
    controller.abort();
  }
}

main().catch(error => {
  send({ type: "error", error: { code: error.code || "provider_runtime_error", message: safe(String(error.message || error)) }, evidence: safe(evidence) });
  process.exitCode = 1;
});
