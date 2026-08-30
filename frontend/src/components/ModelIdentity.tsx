import { displayModelName } from "../lib/modelIdentity";
type ModelIdentityProps = {
  name: string;
  modelId?: string | null;
  adapterKind?: string | null;
  compact?: boolean;
};


function modelBrand({ name, modelId, adapterKind }: ModelIdentityProps): "openai" | "anthropic" | "gemini" | null {
  const value = [name, modelId, adapterKind].filter(Boolean).join(" ").toLowerCase();
  if (/claude|anthropic/.test(value)) return "anthropic";
  if (/gemini|google/.test(value)) return "gemini";
  if (/gpt|openai|codex|luna|sol/.test(value)) return "openai";
  return null;
}

export function ModelLogo(props: ModelIdentityProps) {
  const brand = modelBrand(props);
  const name = displayModelName(props.name);
  return <span className={`model-logo ${brand ? `brand-${brand}` : "brand-generic"}`} aria-hidden="true">
    {brand ? <img src={`/brands/${brand}.svg`} alt=""/> : <b>{name.slice(0, 1).toUpperCase()}</b>}
  </span>;
}

export function ModelIdentity(props: ModelIdentityProps) {
  return <span className={`model-identity ${props.compact ? "compact" : ""}`}>
    <ModelLogo {...props}/><span>{displayModelName(props.name)}</span>
  </span>;
}
