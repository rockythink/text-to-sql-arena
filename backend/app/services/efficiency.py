from __future__ import annotations

import math
from typing import Any

_TOKEN_KEYS = {
    "input": ("input_tokens", "prompt_tokens"),
    "cached_input": ("cached_input_tokens", "cache_read_input_tokens", "cache_read_tokens"),
    "cache_write_input": (
        "cache_write_input_tokens",
        "cache_creation_input_tokens",
        "cache_creation_tokens",
    ),
    "output": ("output_tokens", "completion_tokens"),
    "reasoning_output": ("reasoning_output_tokens", "reasoning_tokens"),
}


def _first_nonnegative_int(source: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = source.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


def normalize_token_usage(
    usage: dict[str, Any] | None,
    adapter_kind: str,
) -> dict[str, int] | None:
    """Normalize provider counters without double-counting cached or reasoning tokens."""
    if not usage:
        return None
    values = {name: _first_nonnegative_int(usage, keys) for name, keys in _TOKEN_KEYS.items()}
    if not any(values.values()):
        return None

    reported_input = values["input"]
    cached_input = values["cached_input"]
    cache_write_input = values["cache_write_input"]
    if adapter_kind == "claude_cli":
        uncached_input = reported_input
    else:
        uncached_input = max(reported_input - cached_input, 0)

    output = values["output"]
    return {
        "input": uncached_input,
        "cached_input": cached_input,
        "cache_write_input": cache_write_input,
        "output": output,
        "reasoning_output": values["reasoning_output"],
        "total": uncached_input + cached_input + cache_write_input + output,
    }


def estimate_cost_usd(
    tokens: dict[str, int] | None,
    pricing: dict[str, Any] | None,
) -> float | None:
    """Estimate token cost from an immutable USD-per-million pricing snapshot."""
    if tokens is None or not pricing:
        return None
    rates = {
        "input": pricing.get("input_usd_per_million"),
        "cached_input": pricing.get("cached_input_usd_per_million"),
        "cache_write_input": pricing.get("cache_write_input_usd_per_million"),
        "output": pricing.get("output_usd_per_million"),
    }
    total = 0.0
    for name, rate in rates.items():
        count = tokens[name]
        if count == 0:
            continue
        if not isinstance(rate, (int, float)) or isinstance(rate, bool) or rate < 0:
            return None
        total += count * float(rate) / 1_000_000
    return round(total, 8)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 2)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 2)


def case_efficiency(
    case: dict[str, Any],
    adapter_kind: str,
    pricing: dict[str, Any] | None,
) -> dict[str, Any]:
    tokens = normalize_token_usage(case.get("token_usage"), adapter_kind)
    generation_ms = case.get("generation_ms")
    execution_ms = case.get("execution_ms")
    return {
        "tokens": tokens,
        "estimated_cost_usd": estimate_cost_usd(tokens, pricing),
        "generation_ms": round(float(generation_ms), 2)
        if isinstance(generation_ms, (int, float))
        else None,
        "execution_ms": round(float(execution_ms), 2)
        if isinstance(execution_ms, (int, float))
        else None,
    }


def aggregate_efficiency(
    cases: list[dict[str, Any]],
    adapter_kind: str,
    pricing: dict[str, Any] | None,
) -> dict[str, Any]:
    token_totals = {
        "input": 0,
        "cached_input": 0,
        "cache_write_input": 0,
        "output": 0,
        "reasoning_output": 0,
        "total": 0,
    }
    generation_values: list[float] = []
    execution_values: list[float] = []
    measured_token_cases = 0
    measured_cost_cases = 0
    estimated_cost = 0.0
    correct_case_equivalents = 0.0

    for case in cases:
        score = (case.get("score") or {}).get("total", 0)
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            correct_case_equivalents += max(float(score), 0) / 100
        metric = case_efficiency(case, adapter_kind, pricing)
        tokens = metric["tokens"]
        if tokens is not None:
            measured_token_cases += 1
            for key in token_totals:
                token_totals[key] += tokens[key]
        cost = metric["estimated_cost_usd"]
        if cost is not None:
            measured_cost_cases += 1
            estimated_cost += cost
        if metric["generation_ms"] is not None:
            generation_values.append(metric["generation_ms"])
        if metric["execution_ms"] is not None:
            execution_values.append(metric["execution_ms"])

    attempted_cases = len(cases)
    total_tokens = token_totals["total"] if measured_token_cases else None
    complete_cost = measured_cost_cases == attempted_cases and attempted_cases > 0
    total_cost = round(estimated_cost, 8) if complete_cost else None
    total_generation_ms = round(sum(generation_values), 2) if generation_values else None
    equivalent = round(correct_case_equivalents, 4)

    return {
        "metric_schema_version": "efficiency-v1",
        "attempted_cases": attempted_cases,
        "correct_case_equivalents": equivalent,
        "coverage": {
            "tokens": {"measured": measured_token_cases, "total": attempted_cases},
            "cost": {"measured": measured_cost_cases, "total": attempted_cases},
            "generation_time": {"measured": len(generation_values), "total": attempted_cases},
            "execution_time": {"measured": len(execution_values), "total": attempted_cases},
        },
        "tokens": token_totals if measured_token_cases else None,
        "estimated_cost_usd": total_cost,
        "generation_ms": {
            "total": total_generation_ms,
            "mean": round(sum(generation_values) / len(generation_values), 2)
            if generation_values
            else None,
            "p50": _percentile(generation_values, 0.5),
            "p95": _percentile(generation_values, 0.95),
        },
        "execution_ms": {
            "total": round(sum(execution_values), 2) if execution_values else None,
            "mean": round(sum(execution_values) / len(execution_values), 2)
            if execution_values
            else None,
        },
        "per_correct_case_equivalent": {
            "tokens": round(total_tokens / equivalent, 2)
            if total_tokens is not None and equivalent > 0
            else None,
            "estimated_cost_usd": round(total_cost / equivalent, 8)
            if total_cost is not None and equivalent > 0
            else None,
            "generation_ms": round(total_generation_ms / equivalent, 2)
            if total_generation_ms is not None and equivalent > 0
            else None,
        },
        "pricing": pricing,
        "cost_basis": "estimated_token_price" if pricing else "unavailable",
    }
