from backend.app.services.efficiency import (
    aggregate_efficiency,
    estimate_cost_usd,
    normalize_token_usage,
)

PRICING = {
    "currency": "USD",
    "input_usd_per_million": 2.0,
    "cached_input_usd_per_million": 0.5,
    "cache_write_input_usd_per_million": 2.5,
    "output_usd_per_million": 8.0,
    "source": "fixture",
    "effective_at": "2026-08-30",
}


def test_pi_disjoint_cache_counters_preserve_total_and_cost() -> None:
    usage = normalize_token_usage(
        {
            "input_tokens": 1_000,
            "cache_read_tokens": 400,
            "cache_write_tokens": 100,
            "output_tokens": 200,
        },
        "pi",
    )
    assert usage is not None
    assert usage["input"] == 1_000
    assert usage["total"] == 1_700
    assert estimate_cost_usd(usage, PRICING) == 0.00405


def test_normalizes_claude_cache_counters_as_additional_input() -> None:
    usage = normalize_token_usage(
        {
            "input_tokens": 600,
            "cache_read_input_tokens": 300,
            "cache_creation_input_tokens": 100,
            "output_tokens": 200,
        },
        "claude_cli",
    )
    assert usage is not None
    assert usage["input"] == 600
    assert usage["total"] == 1_200
    assert estimate_cost_usd(usage, PRICING) == 0.0032


def test_aggregate_reports_coverage_and_quality_adjusted_cost() -> None:
    metrics = aggregate_efficiency(
        [
            {
                "score": {
                    "total": 100,
                    "protocol": 5,
                    "execution": 10,
                    "column_count": 5,
                    "column_names": 5,
                    "row_f1": 45,
                    "ordering": 10,
                },
                "token_usage": {"input_tokens": 1_000, "output_tokens": 200},
                "generation_ms": 1_000,
                "execution_ms": 20,
            },
            {
                "score": {
                    "total": 50,
                    "protocol": 5,
                    "execution": 10,
                    "column_count": 5,
                    "column_names": 5,
                    "row_f1": 0,
                    "ordering": 10,
                },
                "token_usage": {"input_tokens": 500, "output_tokens": 100},
                "generation_ms": 3_000,
                "execution_ms": 40,
            },
        ],
        "pi",
        PRICING,
    )
    assert metrics["correct_cases"] == 1
    assert metrics["tokens"]["total"] == 1_800
    assert metrics["estimated_cost_usd"] == 0.0054
    assert metrics["generation_ms"] == {
        "total": 4_000.0,
        "mean": 2_000.0,
        "p50": 2_000.0,
        "p95": 2_900.0,
    }
    assert metrics["per_correct_case"] == {
        "tokens": 1_800.0,
        "estimated_cost_usd": 0.0054,
        "generation_ms": 4_000.0,
    }
    assert metrics["coverage"]["cost"] == {"measured": 2, "total": 2}


def test_cost_is_unknown_when_required_price_or_usage_is_missing() -> None:
    tokens = normalize_token_usage({"input_tokens": 10, "output_tokens": 2}, "pi")
    assert estimate_cost_usd(tokens, {"input_usd_per_million": 1.0}) is None
    metrics = aggregate_efficiency(
        [{"score": {"total": 100}, "token_usage": None, "generation_ms": None}],
        "codex_cli",
        PRICING,
    )
    assert metrics["tokens"] is None
    assert metrics["estimated_cost_usd"] is None
    assert metrics["coverage"]["tokens"] == {"measured": 0, "total": 1}
