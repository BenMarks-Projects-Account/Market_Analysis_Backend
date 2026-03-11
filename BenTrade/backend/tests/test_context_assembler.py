"""Tests for context_assembler — Context Assembler v1.

Test categories:
  1. Contract shape — assemble_context returns all required top-level fields
  2. Assembly status — complete / partial / degraded / empty
  3. Market context — normalized priority, fallback, degraded module tracking
  4. Candidate context — stock and options candidates, fallback
  5. Model context — normalized model analysis, fallback
  6. Quality summary — rollup across modules
  7. Freshness summary — rollup across modules
  8. Input priority — normalized preferred over legacy
  9. Degraded assembly — missing module, stale data, error payloads
 10. Fallback proof — legacy-only payloads assembled correctly
 11. Integration: complete assembly — realistic multi-module
 12. Integration: degraded assembly — partial/missing modules
 13. Edge cases — empty inputs, None inputs, unknown keys
"""

import pytest

from app.services.context_assembler import (
    CONTEXT_VERSION,
    MARKET_MODULES,
    ASSEMBLY_STATUSES,
    MODULE_KEY_ALIASES,
    MODULE_SOURCES,
    assemble_context,
    _assemble_market_context,
    _assemble_candidate_context,
    _assemble_model_context,
    _build_quality_summary,
    _build_freshness_summary,
    _compute_assembly_status,
    _extract_market_module,
    _resolve_module_key,
    _build_fallback_normalized,
    _build_fallback_candidate,
    _build_fallback_model,
    _infer_family,
)


# ════════════════════════════════════════════════════════════════════════
# Constants
# ════════════════════════════════════════════════════════════════════════

REQUIRED_TOP_LEVEL_KEYS = {
    "context_version",
    "assembled_at",
    "assembly_status",
    "assembly_warnings",
    "included_modules",
    "missing_modules",
    "degraded_modules",
    "failed_modules",
    "market_context",
    "candidate_context",
    "model_context",
    "quality_summary",
    "freshness_summary",
    "horizon_summary",
    "metadata",
}

REQUIRED_METADATA_KEYS = {
    "context_version",
    "assembled_at",
    "market_module_count",
    "candidate_count",
    "model_count",
    "assembly_status",
    "module_sources",
}

REQUIRED_CANDIDATE_CTX_KEYS = {"candidates", "count", "scanners", "families"}
REQUIRED_MODEL_CTX_KEYS = {"analyses", "count"}
REQUIRED_QUALITY_SUMMARY_KEYS = {"overall_quality", "average_confidence", "module_count", "degraded_count", "modules"}
REQUIRED_FRESHNESS_SUMMARY_KEYS = {"overall_freshness", "module_count", "modules"}


# ════════════════════════════════════════════════════════════════════════
# Fixtures — realistic payloads
# ════════════════════════════════════════════════════════════════════════


def _normalized_engine(engine_key, score=72.5, label="Healthy", confidence=85,
                       signal_quality="high"):
    """Build a realistic normalized engine dict (23 keys)."""
    return {
        "engine_key": engine_key,
        "engine_name": engine_key.replace("_", " ").title(),
        "as_of": "2026-03-10T14:00:00Z",
        "score": score,
        "label": label,
        "short_label": label[:10],
        "confidence": confidence,
        "signal_quality": signal_quality,
        "time_horizon": "short_term",
        "freshness": {"compute_duration_s": 0.5, "data_age_s": 10},
        "summary": f"{engine_key} summary",
        "trader_takeaway": f"{engine_key} takeaway",
        "bull_factors": ["factor1"],
        "bear_factors": ["factor2"],
        "risks": [],
        "regime_tags": ["neutral"],
        "supporting_metrics": [],
        "contradiction_flags": [],
        "data_quality": {
            "confidence_score": confidence,
            "signal_quality": signal_quality,
            "missing_inputs_count": 0,
            "warning_count": 0,
        },
        "warnings": [],
        "source_status": [],
        "pillar_scores": [],
        "detail_sections": [],
    }


def _dashboard_metadata(engine_key, quality_status="good", freshness="live",
                        coverage="full"):
    """Build a realistic dashboard_metadata dict (18 keys)."""
    return {
        "data_quality_status": quality_status,
        "coverage_level": coverage,
        "freshness_status": freshness,
        "proxy_reliance_level": "none",
        "confidence_impact": {
            "confidence_score": 85,
            "signal_quality": "high",
            "degradation_factors": [],
            "proxy_reliance_level": "none",
            "is_actionable": True,
        },
        "missing_fields": [],
        "stale_fields": [],
        "proxy_fields": [],
        "failed_sources": [],
        "insufficient_history_fields": [],
        "unimplemented_fields": [],
        "partial_fields": [],
        "field_status_map": {"score": "ok", "label": "ok"},
        "source_status": [],
        "warnings": [],
        "notes": [],
        "last_successful_update": "2026-03-10T14:00:00Z",
        "evaluation_metadata": {
            "evaluated_at": "2026-03-10T14:00:00Z",
            "engine_key": engine_key,
            "engine_version": "1.0",
            "compute_duration_s": 0.5,
        },
    }


def _full_market_payload(engine_key, **kwargs):
    """Build a complete service payload with both normalized + dashboard_metadata."""
    return {
        "engine_result": {
            "engine": engine_key,
            "score": kwargs.get("score", 72.5),
            "label": kwargs.get("label", "Healthy"),
            "confidence_score": kwargs.get("confidence", 85),
            "signal_quality": kwargs.get("signal_quality", "high"),
            "as_of": "2026-03-10T14:00:00Z",
            "warnings": [],
            "missing_inputs": [],
            "diagnostics": {},
        },
        "data_quality": {
            "signal_quality": kwargs.get("signal_quality", "high"),
            "confidence_score": kwargs.get("confidence", 85),
            "missing_inputs_count": 0,
            "warning_count": 0,
        },
        "compute_duration_s": 0.5,
        "as_of": "2026-03-10T14:00:00Z",
        "normalized": _normalized_engine(engine_key, **kwargs),
        "dashboard_metadata": _dashboard_metadata(engine_key),
    }


def _legacy_market_payload(engine_key, score=60, label="Mixed"):
    """Build a legacy payload WITHOUT normalized or dashboard_metadata."""
    return {
        "engine_result": {
            "engine": engine_key,
            "score": score,
            "label": label,
            "confidence_score": 55,
            "signal_quality": "moderate",
            "as_of": "2026-03-10T12:00:00Z",
            "summary": f"{engine_key} legacy summary",
            "trader_takeaway": f"{engine_key} legacy takeaway",
            "warnings": ["Using cached data"],
            "missing_inputs": ["some_input"],
            "diagnostics": {},
        },
        "data_quality": {
            "signal_quality": "moderate",
            "confidence_score": 55,
            "missing_inputs_count": 1,
            "warning_count": 1,
        },
        "compute_duration_s": 0.3,
        "as_of": "2026-03-10T12:00:00Z",
    }


def _normalized_stock_candidate(symbol="AAPL", score=78.5):
    """Build a candidate dict with normalized contract attached."""
    return {
        "symbol": symbol,
        "price": 185.50,
        "composite_score": score,
        "normalized": {
            "candidate_id": f"{symbol}_pullback_20260310",
            "scanner_key": "stock_pullback_swing",
            "scanner_name": "Stock Pullback Swing",
            "strategy_family": "stock",
            "setup_type": "pullback",
            "asset_class": "equity",
            "symbol": symbol,
            "underlying": symbol,
            "direction": "long",
            "thesis_summary": ["Strong RSI divergence"],
            "entry_context": {"price": 185.50},
            "time_horizon": "swing",
            "setup_quality": score,
            "confidence": 0.8,
            "risk_definition": {"type": "stop_loss_based"},
            "reward_profile": {"type": "price_target_based"},
            "supporting_signals": [],
            "risk_flags": [],
            "invalidation_signals": [],
            "market_context_tags": [],
            "position_sizing_notes": None,
            "data_quality": {},
            "source_status": [],
            "pricing_snapshot": {"price": 185.50},
            "strategy_structure": None,
            "candidate_metrics": {},
            "detail_sections": [],
            "generated_at": "2026-03-10T14:00:00Z",
        },
    }


def _normalized_options_candidate(symbol="SPY", strategy="put_credit_spread"):
    """Build an options candidate dict with normalized contract attached."""
    return {
        "symbol": symbol,
        "strategy_id": strategy,
        "short_strike": 500,
        "long_strike": 495,
        "normalized": {
            "candidate_id": f"{symbol}_{strategy}_20260310",
            "scanner_key": strategy,
            "scanner_name": strategy.replace("_", " ").title(),
            "strategy_family": "options",
            "setup_type": strategy,
            "asset_class": "option",
            "symbol": symbol,
            "underlying": symbol,
            "direction": "short",
            "thesis_summary": ["High POP credit spread"],
            "entry_context": {"spread_mid": 0.35},
            "time_horizon": "days_to_expiry",
            "setup_quality": 82.0,
            "confidence": 0.75,
            "risk_definition": {"type": "defined_risk_spread", "max_loss": 465},
            "reward_profile": {"type": "defined_reward_spread", "max_profit": 35},
            "supporting_signals": [],
            "risk_flags": [],
            "invalidation_signals": [],
            "market_context_tags": [],
            "position_sizing_notes": None,
            "data_quality": {},
            "source_status": [],
            "pricing_snapshot": {"spread_mid": 0.35},
            "strategy_structure": {"legs": [{"strike": 500}, {"strike": 495}]},
            "candidate_metrics": {"pop": 0.82, "ev": 12.5},
            "detail_sections": [],
            "generated_at": "2026-03-10T14:00:00Z",
        },
    }


def _legacy_candidate(symbol="TSLA"):
    """Build a legacy candidate WITHOUT normalized contract."""
    return {
        "symbol": symbol,
        "price": 250.00,
        "composite_score": 65.0,
        "trend_state": "pullback",
        "strategy_id": "stock_pullback_swing",
        "trade_key": f"{symbol}_pullback_legacy",
    }


def _normalized_model_response(analysis_type="breadth_participation"):
    """Build a model-analysis response with normalized contract attached."""
    return {
        "model_analysis": {"score": 72, "label": "Healthy"},
        "normalized": {
            "status": "success",
            "analysis_type": analysis_type,
            "analysis_name": analysis_type.replace("_", " ").title(),
            "category": "market_picture",
            "model_source": "openai",
            "requested_at": "2026-03-10T14:00:00Z",
            "completed_at": "2026-03-10T14:00:05Z",
            "duration_ms": 5000,
            "raw_content": "{}",
            "normalized_text": "Model analysis text",
            "structured_payload": {"score": 72, "label": "Healthy"},
            "summary": "Breadth is healthy",
            "key_points": ["Broad participation"],
            "risks": [],
            "actions": ["Monitor breadth"],
            "confidence": 0.85,
            "warnings": [],
            "error_type": None,
            "error_message": None,
            "parse_strategy": "json",
            "response_format": "json",
            "metadata": {},
        },
    }


def _legacy_model_response(analysis_type="volatility_options"):
    """Build a legacy model response WITHOUT normalized."""
    return {
        "model_analysis": {"score": 55, "label": "Elevated"},
        "summary": "Volatility elevated",
        "confidence": 0.6,
        "warnings": ["Partial data"],
    }


# ════════════════════════════════════════════════════════════════════════
# 1. CONTRACT SHAPE
# ════════════════════════════════════════════════════════════════════════

class TestContractShape:
    """assemble_context returns the full expected shape."""

    def test_empty_call_has_all_required_keys(self):
        result = assemble_context()
        assert set(result.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_metadata_has_all_required_keys(self):
        result = assemble_context()
        assert set(result["metadata"].keys()) == REQUIRED_METADATA_KEYS

    def test_candidate_context_has_required_keys(self):
        result = assemble_context()
        assert set(result["candidate_context"].keys()) == REQUIRED_CANDIDATE_CTX_KEYS

    def test_model_context_has_required_keys(self):
        result = assemble_context()
        assert set(result["model_context"].keys()) == REQUIRED_MODEL_CTX_KEYS

    def test_quality_summary_has_required_keys(self):
        result = assemble_context(market_payloads={
            "breadth_participation": _full_market_payload("breadth_participation"),
        })
        assert set(result["quality_summary"].keys()) == REQUIRED_QUALITY_SUMMARY_KEYS

    def test_freshness_summary_has_required_keys(self):
        result = assemble_context(market_payloads={
            "breadth_participation": _full_market_payload("breadth_participation"),
        })
        assert set(result["freshness_summary"].keys()) == REQUIRED_FRESHNESS_SUMMARY_KEYS

    def test_context_version_is_1_1(self):
        result = assemble_context()
        assert result["context_version"] == "1.1"
        assert result["metadata"]["context_version"] == "1.1"

    def test_assembled_at_is_iso_string(self):
        result = assemble_context()
        assert "T" in result["assembled_at"]
        assert "Z" in result["assembled_at"] or "+" in result["assembled_at"]

    def test_list_fields_are_lists(self):
        result = assemble_context()
        for key in ("assembly_warnings", "included_modules", "missing_modules",
                     "degraded_modules", "failed_modules"):
            assert isinstance(result[key], list), f"{key} should be a list"


# ════════════════════════════════════════════════════════════════════════
# 2. ASSEMBLY STATUS
# ════════════════════════════════════════════════════════════════════════

class TestAssemblyStatus:
    """Assembly status computation."""

    def test_empty_returns_empty(self):
        assert _compute_assembly_status(
            included_count=0, missing_count=0, degraded_count=0, failed_count=0,
            candidate_count=0, any_market_provided=False,
        ) == "empty"

    def test_all_included_returns_complete(self):
        assert _compute_assembly_status(
            included_count=6, missing_count=0, degraded_count=0, failed_count=0,
            candidate_count=5, any_market_provided=True,
        ) == "complete"

    def test_some_missing_returns_partial(self):
        assert _compute_assembly_status(
            included_count=4, missing_count=2, degraded_count=0, failed_count=0,
            candidate_count=0, any_market_provided=True,
        ) == "partial"

    def test_some_degraded_returns_partial(self):
        assert _compute_assembly_status(
            included_count=5, missing_count=0, degraded_count=1, failed_count=0,
            candidate_count=0, any_market_provided=True,
        ) == "partial"

    def test_mostly_missing_returns_degraded(self):
        assert _compute_assembly_status(
            included_count=1, missing_count=5, degraded_count=0, failed_count=0,
            candidate_count=0, any_market_provided=True,
        ) == "degraded"

    def test_candidates_only_returns_partial(self):
        assert _compute_assembly_status(
            included_count=0, missing_count=0, degraded_count=0, failed_count=0,
            candidate_count=5, any_market_provided=False,
        ) == "partial"

    def test_some_failed_returns_partial(self):
        assert _compute_assembly_status(
            included_count=4, missing_count=0, degraded_count=0, failed_count=2,
            candidate_count=0, any_market_provided=True,
        ) == "partial"

    def test_mostly_failed_returns_degraded(self):
        assert _compute_assembly_status(
            included_count=1, missing_count=0, degraded_count=0, failed_count=5,
            candidate_count=0, any_market_provided=True,
        ) == "degraded"

    def test_status_vocabulary_valid(self):
        for s in ASSEMBLY_STATUSES:
            assert s in {"complete", "partial", "degraded", "empty"}


# ════════════════════════════════════════════════════════════════════════
# 3. MARKET CONTEXT
# ════════════════════════════════════════════════════════════════════════

class TestMarketContext:
    """Market context assembly."""

    def test_normalized_module_included(self):
        payloads = {"breadth_participation": _full_market_payload("breadth_participation")}
        ctx, included, missing, degraded, failed, warnings = _assemble_market_context(payloads)
        assert "breadth_participation" in ctx
        assert "breadth_participation" in included
        assert ctx["breadth_participation"]["source"] == "normalized"

    def test_normalized_module_has_both_layers(self):
        payloads = {"breadth_participation": _full_market_payload("breadth_participation")}
        ctx, _, _, _, _, _ = _assemble_market_context(payloads)
        mod = ctx["breadth_participation"]
        assert mod["normalized"] is not None
        assert mod["dashboard_metadata"] is not None

    def test_legacy_module_marked_degraded(self):
        payloads = {"volatility_options": _legacy_market_payload("volatility_options")}
        ctx, included, missing, degraded, failed, warnings = _assemble_market_context(payloads)
        assert "volatility_options" in degraded
        assert "volatility_options" not in included
        assert ctx["volatility_options"]["source"] == "fallback"

    def test_missing_module_tracked(self):
        payloads = {"breadth_participation": _full_market_payload("breadth_participation")}
        ctx, included, missing, degraded, failed, warnings = _assemble_market_context(payloads)
        assert "volatility_options" in missing
        assert "flows_positioning" in missing

    def test_all_six_modules_complete(self):
        payloads = {k: _full_market_payload(k) for k in MARKET_MODULES}
        ctx, included, missing, degraded, failed, warnings = _assemble_market_context(payloads)
        assert len(included) == 6
        assert len(missing) == 0
        assert len(degraded) == 0
        assert len(failed) == 0

    def test_empty_payload_dict_means_error(self):
        payloads = {"breadth_participation": {}}
        ctx, included, missing, degraded, failed, warnings = _assemble_market_context(payloads)
        assert "breadth_participation" in failed
        assert any("no usable data" in w for w in warnings)


# ════════════════════════════════════════════════════════════════════════
# 4. CANDIDATE CONTEXT
# ════════════════════════════════════════════════════════════════════════

class TestCandidateContext:
    """Candidate context assembly."""

    def test_stock_candidate_assembled(self):
        candidates = [_normalized_stock_candidate("AAPL")]
        ctx, warnings = _assemble_candidate_context(candidates)
        assert ctx["count"] == 1
        assert ctx["candidates"][0]["symbol"] == "AAPL"
        assert ctx["candidates"][0]["strategy_family"] == "stock"
        assert "stock" in ctx["families"]

    def test_options_candidate_assembled(self):
        candidates = [_normalized_options_candidate("SPY")]
        ctx, warnings = _assemble_candidate_context(candidates)
        assert ctx["count"] == 1
        assert ctx["candidates"][0]["symbol"] == "SPY"
        assert ctx["candidates"][0]["strategy_family"] == "options"
        assert "options" in ctx["families"]

    def test_mixed_candidates(self):
        candidates = [
            _normalized_stock_candidate("AAPL"),
            _normalized_options_candidate("SPY"),
        ]
        ctx, warnings = _assemble_candidate_context(candidates)
        assert ctx["count"] == 2
        assert "stock" in ctx["families"]
        assert "options" in ctx["families"]
        assert len(ctx["scanners"]) == 2

    def test_legacy_candidate_fallback(self):
        candidates = [_legacy_candidate("TSLA")]
        ctx, warnings = _assemble_candidate_context(candidates)
        assert ctx["count"] == 1
        assert ctx["candidates"][0].get("_fallback") is True
        assert ctx["candidates"][0]["symbol"] == "TSLA"
        assert len(warnings) == 1
        assert "legacy fallback" in warnings[0]

    def test_empty_candidates(self):
        ctx, warnings = _assemble_candidate_context([])
        assert ctx["count"] == 0
        assert ctx["candidates"] == []
        assert len(warnings) == 0


# ════════════════════════════════════════════════════════════════════════
# 5. MODEL CONTEXT
# ════════════════════════════════════════════════════════════════════════

class TestModelContext:
    """Model context assembly."""

    def test_normalized_model_included(self):
        payloads = {"breadth_participation": _normalized_model_response("breadth_participation")}
        ctx, warnings = _assemble_model_context(payloads)
        assert ctx["count"] == 1
        assert "breadth_participation" in ctx["analyses"]
        assert ctx["analyses"]["breadth_participation"]["source"] == "normalized"

    def test_legacy_model_fallback(self):
        payloads = {"volatility_options": _legacy_model_response("volatility_options")}
        ctx, warnings = _assemble_model_context(payloads)
        assert ctx["count"] == 1
        assert ctx["analyses"]["volatility_options"]["source"] == "fallback"
        assert ctx["analyses"]["volatility_options"]["normalized"].get("_fallback") is True
        assert len(warnings) == 1

    def test_empty_model_payloads(self):
        ctx, warnings = _assemble_model_context({})
        assert ctx["count"] == 0
        assert ctx["analyses"] == {}

    def test_model_missing_does_not_fail_assembly(self):
        result = assemble_context(model_payloads=None)
        assert result["model_context"]["count"] == 0
        assert set(result.keys()) == REQUIRED_TOP_LEVEL_KEYS


# ════════════════════════════════════════════════════════════════════════
# 6. QUALITY SUMMARY
# ════════════════════════════════════════════════════════════════════════

class TestQualitySummary:
    """Quality rollup across modules."""

    def test_all_good_modules(self):
        market_ctx = {
            "breadth_participation": {
                "normalized": _normalized_engine("breadth_participation"),
                "dashboard_metadata": _dashboard_metadata("breadth_participation"),
                "source": "normalized",
            },
            "volatility_options": {
                "normalized": _normalized_engine("volatility_options"),
                "dashboard_metadata": _dashboard_metadata("volatility_options"),
                "source": "normalized",
            },
        }
        qs = _build_quality_summary(market_ctx, ["breadth_participation", "volatility_options"], [])
        assert qs["overall_quality"] == "good"
        assert qs["average_confidence"] == 85.0
        assert qs["module_count"] == 2
        assert qs["degraded_count"] == 0

    def test_worst_quality_propagates(self):
        market_ctx = {
            "breadth_participation": {
                "normalized": _normalized_engine("breadth_participation"),
                "dashboard_metadata": _dashboard_metadata("breadth_participation", quality_status="good"),
                "source": "normalized",
            },
            "volatility_options": {
                "normalized": _normalized_engine("volatility_options", confidence=40),
                "dashboard_metadata": _dashboard_metadata("volatility_options", quality_status="poor"),
                "source": "normalized",
            },
        }
        qs = _build_quality_summary(market_ctx, ["breadth_participation", "volatility_options"], [])
        assert qs["overall_quality"] == "poor"

    def test_no_modules_returns_unknown(self):
        qs = _build_quality_summary({}, [], [])
        assert qs["overall_quality"] == "unknown"


# ════════════════════════════════════════════════════════════════════════
# 7. FRESHNESS SUMMARY
# ════════════════════════════════════════════════════════════════════════

class TestFreshnessSummary:
    """Freshness rollup across modules."""

    def test_all_live(self):
        market_ctx = {
            "breadth_participation": {
                "normalized": _normalized_engine("breadth_participation"),
                "dashboard_metadata": _dashboard_metadata("breadth_participation", freshness="live"),
                "source": "normalized",
            },
        }
        fs = _build_freshness_summary(market_ctx, ["breadth_participation"])
        assert fs["overall_freshness"] == "live"

    def test_worst_freshness_propagates(self):
        market_ctx = {
            "breadth_participation": {
                "normalized": _normalized_engine("breadth_participation"),
                "dashboard_metadata": _dashboard_metadata("breadth_participation", freshness="live"),
                "source": "normalized",
            },
            "volatility_options": {
                "normalized": _normalized_engine("volatility_options"),
                "dashboard_metadata": _dashboard_metadata("volatility_options", freshness="stale"),
                "source": "normalized",
            },
        }
        fs = _build_freshness_summary(market_ctx, ["breadth_participation", "volatility_options"])
        assert fs["overall_freshness"] == "stale"

    def test_no_modules_returns_unknown(self):
        fs = _build_freshness_summary({}, [])
        assert fs["overall_freshness"] == "unknown"


# ════════════════════════════════════════════════════════════════════════
# 8. INPUT PRIORITY
# ════════════════════════════════════════════════════════════════════════

class TestInputPriority:
    """Assembler prefers normalized contracts over legacy."""

    def test_normalized_preferred_over_legacy(self):
        payload = _full_market_payload("breadth_participation")
        mod, source, warnings = _extract_market_module("breadth_participation", payload)
        assert source == "normalized"
        assert mod["source"] == "normalized"
        assert len(warnings) == 0

    def test_legacy_only_gets_fallback_source(self):
        payload = _legacy_market_payload("breadth_participation")
        mod, source, warnings = _extract_market_module("breadth_participation", payload)
        assert source == "fallback"
        assert mod["source"] == "fallback"
        assert any("legacy fallback" in w for w in warnings)

    def test_normalized_in_full_assembly_no_warnings(self):
        payloads = {
            "breadth_participation": _full_market_payload("breadth_participation"),
        }
        result = assemble_context(market_payloads=payloads)
        fallback_warnings = [w for w in result["assembly_warnings"] if "legacy" in w]
        assert len(fallback_warnings) == 0

    def test_legacy_in_full_assembly_has_warnings(self):
        payloads = {
            "breadth_participation": _legacy_market_payload("breadth_participation"),
        }
        result = assemble_context(market_payloads=payloads)
        fallback_warnings = [w for w in result["assembly_warnings"] if "legacy" in w]
        assert len(fallback_warnings) > 0

    def test_fallback_normalized_has_marker(self):
        payload = _legacy_market_payload("breadth_participation")
        mod, source, _ = _extract_market_module("breadth_participation", payload)
        sd = mod["normalized"].get("status_detail", {})
        assert sd.get("is_fallback") is True or sd.get("is_legacy") is True

    def test_real_normalized_has_no_fallback_marker(self):
        payload = _full_market_payload("breadth_participation")
        mod, source, _ = _extract_market_module("breadth_participation", payload)
        sd = mod["normalized"].get("status_detail", {})
        assert sd.get("is_fallback") is not True


# ════════════════════════════════════════════════════════════════════════
# 9. DEGRADED ASSEMBLY
# ════════════════════════════════════════════════════════════════════════

class TestDegradedAssembly:
    """Missing/degraded modules handled gracefully."""

    def test_missing_module_does_not_fail(self):
        result = assemble_context(market_payloads={
            "breadth_participation": _full_market_payload("breadth_participation"),
        })
        assert "volatility_options" in result["missing_modules"]
        assert result["assembly_status"] in ("partial", "degraded")
        assert set(result.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_all_modules_missing_returns_empty(self):
        result = assemble_context()
        assert result["assembly_status"] == "empty"
        assert len(result["included_modules"]) == 0

    def test_degraded_module_captured(self):
        result = assemble_context(market_payloads={
            "breadth_participation": _full_market_payload("breadth_participation"),
            "volatility_options": _legacy_market_payload("volatility_options"),
        })
        assert "volatility_options" in result["degraded_modules"]
        assert "breadth_participation" in result["included_modules"]

    def test_error_payload_module_tracked(self):
        error_payload = {
            "engine_result": {
                "engine": "cross_asset_macro",
                "score": None,
                "label": "Unavailable",
                "confidence_score": 0,
                "signal_quality": "low",
                "as_of": "2026-03-10T14:00:00Z",
                "warnings": ["Engine error"],
                "missing_inputs": [],
            },
            "data_quality": {"signal_quality": "low", "confidence_score": 0},
            "error": "Engine failed",
        }
        result = assemble_context(market_payloads={
            "cross_asset_macro": error_payload,
        })
        # Without normalized key, it's a legacy fallback → degraded
        assert "cross_asset_macro" in result["degraded_modules"]
        assert "cross_asset_macro" not in result["failed_modules"]

    def test_stale_dashboard_metadata_influences_quality(self):
        payload = _full_market_payload("breadth_participation")
        payload["dashboard_metadata"]["freshness_status"] = "very_stale"
        payload["dashboard_metadata"]["data_quality_status"] = "degraded"
        result = assemble_context(market_payloads={"breadth_participation": payload})
        qs = result["quality_summary"]
        assert qs["modules"]["breadth_participation"]["data_quality_status"] == "degraded"
        fs = result["freshness_summary"]
        assert fs["modules"]["breadth_participation"]["freshness_status"] == "very_stale"

    def test_mixed_normalized_and_legacy_assembly(self):
        payloads = {
            "breadth_participation": _full_market_payload("breadth_participation"),
            "volatility_options": _full_market_payload("volatility_options"),
            "cross_asset_macro": _legacy_market_payload("cross_asset_macro"),
            "flows_positioning": _legacy_market_payload("flows_positioning"),
        }
        result = assemble_context(market_payloads=payloads)
        assert len(result["included_modules"]) == 2
        assert len(result["degraded_modules"]) == 2
        assert len(result["missing_modules"]) == 2
        assert result["assembly_status"] == "partial"


# ════════════════════════════════════════════════════════════════════════
# 10. FALLBACK PROOF
# ════════════════════════════════════════════════════════════════════════

class TestFallbackProof:
    """Legacy / cached payloads without normalized → safe fallback."""

    def test_legacy_only_produces_valid_assembled_shape(self):
        """Payload missing normalized due to older cache → still assembles."""
        payloads = {
            "breadth_participation": _legacy_market_payload("breadth_participation"),
            "volatility_options": _legacy_market_payload("volatility_options"),
        }
        result = assemble_context(
            market_payloads=payloads,
            candidates=[_legacy_candidate("TSLA")],
        )
        assert set(result.keys()) == REQUIRED_TOP_LEVEL_KEYS
        assert result["assembly_status"] in ("partial", "degraded")
        assert len(result["assembly_warnings"]) >= 3  # 2 market + 1 candidate

    def test_legacy_fallback_preserves_score_and_label(self):
        payload = _legacy_market_payload("breadth_participation", score=60, label="Mixed")
        mod, source, _ = _extract_market_module("breadth_participation", payload)
        norm = mod["normalized"]
        assert norm["score"] == 60
        assert norm["label"] == "Mixed"
        sd = norm.get("status_detail", {})
        assert sd.get("is_fallback") is True or sd.get("is_legacy") is True

    def test_legacy_candidate_fallback_infers_family(self):
        cand = _legacy_candidate("TSLA")
        fb = _build_fallback_candidate(cand, 0)
        assert fb["strategy_family"] == "stock"  # has trend_state
        assert fb["symbol"] == "TSLA"

    def test_options_candidate_fallback_infers_family(self):
        cand = {"symbol": "SPY", "short_strike": 500, "long_strike": 495}
        fb = _build_fallback_candidate(cand, 0)
        assert fb["strategy_family"] == "options"

    def test_unknown_candidate_fallback_family(self):
        cand = {"symbol": "XYZ"}
        fb = _build_fallback_candidate(cand, 0)
        assert fb["strategy_family"] == "unknown"

    def test_fallback_model_produces_degraded_status(self):
        response = {"summary": "Some text", "confidence": 0.5}
        fb = _build_fallback_model("test_analysis", response)
        assert fb["status"] == "degraded"
        assert fb["_fallback"] is True

    def test_fallback_model_empty_produces_error_status(self):
        fb = _build_fallback_model("test_analysis", {})
        assert fb["status"] == "error"
        assert fb["_fallback"] is True


# ════════════════════════════════════════════════════════════════════════
# 11. INTEGRATION: COMPLETE ASSEMBLY
# ════════════════════════════════════════════════════════════════════════

class TestIntegrationComplete:
    """Full integration with realistic multi-module assembly."""

    def test_full_assembly_shape_and_status(self):
        payloads = {k: _full_market_payload(k) for k in MARKET_MODULES}
        candidates = [
            _normalized_stock_candidate("AAPL"),
            _normalized_options_candidate("SPY"),
        ]
        model = {"breadth_participation": _normalized_model_response("breadth_participation")}

        result = assemble_context(
            market_payloads=payloads,
            candidates=candidates,
            model_payloads=model,
        )

        assert set(result.keys()) == REQUIRED_TOP_LEVEL_KEYS
        assert result["context_version"] == "1.1"
        assert result["assembly_status"] == "complete"
        assert len(result["included_modules"]) == 6
        assert len(result["missing_modules"]) == 0
        assert len(result["degraded_modules"]) == 0
        assert len(result["failed_modules"]) == 0
        assert result["candidate_context"]["count"] == 2
        assert result["model_context"]["count"] == 1
        assert result["quality_summary"]["overall_quality"] == "good"
        assert result["freshness_summary"]["overall_freshness"] == "live"
        assert result["metadata"]["market_module_count"] == 6
        assert result["metadata"]["candidate_count"] == 2
        assert result["metadata"]["model_count"] == 1

    def test_full_assembly_market_modules_accessible(self):
        payloads = {k: _full_market_payload(k) for k in MARKET_MODULES}
        result = assemble_context(market_payloads=payloads)
        for key in MARKET_MODULES:
            assert key in result["market_context"]
            mod = result["market_context"][key]
            assert mod["source"] == "normalized"
            assert mod["normalized"]["engine_key"] == key
            assert mod["normalized"]["score"] is not None

    def test_full_assembly_candidates_accessible(self):
        candidates = [
            _normalized_stock_candidate("AAPL"),
            _normalized_stock_candidate("MSFT", score=82.0),
            _normalized_options_candidate("SPY"),
        ]
        result = assemble_context(candidates=candidates)
        assert result["candidate_context"]["count"] == 3
        symbols = [c["symbol"] for c in result["candidate_context"]["candidates"]]
        assert "AAPL" in symbols
        assert "MSFT" in symbols
        assert "SPY" in symbols

    def test_full_assembly_no_warnings(self):
        payloads = {k: _full_market_payload(k) for k in MARKET_MODULES}
        candidates = [_normalized_stock_candidate("AAPL")]
        model = {"breadth_participation": _normalized_model_response("breadth_participation")}
        result = assemble_context(
            market_payloads=payloads, candidates=candidates, model_payloads=model,
        )
        # No fallback warnings expected
        fallback_warnings = [w for w in result["assembly_warnings"] if "fallback" in w.lower()]
        assert len(fallback_warnings) == 0


# ════════════════════════════════════════════════════════════════════════
# 12. INTEGRATION: DEGRADED ASSEMBLY
# ════════════════════════════════════════════════════════════════════════

class TestIntegrationDegraded:
    """Integration with partial/missing/degraded modules."""

    def test_degraded_assembly_shape_and_status(self):
        payloads = {
            "breadth_participation": _full_market_payload("breadth_participation"),
            "volatility_options": _legacy_market_payload("volatility_options"),
            # Other 4 modules missing
        }
        candidates = [
            _normalized_stock_candidate("AAPL"),
            _legacy_candidate("TSLA"),
        ]
        model = {
            "breadth_participation": _normalized_model_response("breadth_participation"),
            "volatility_options": _legacy_model_response("volatility_options"),
        }

        result = assemble_context(
            market_payloads=payloads,
            candidates=candidates,
            model_payloads=model,
        )

        assert set(result.keys()) == REQUIRED_TOP_LEVEL_KEYS
        assert result["assembly_status"] in ("partial", "degraded")
        assert "breadth_participation" in result["included_modules"]
        assert "volatility_options" in result["degraded_modules"]
        assert len(result["missing_modules"]) == 4
        assert result["candidate_context"]["count"] == 2
        assert result["model_context"]["count"] == 2

        # Warnings should mention legacy fallbacks
        assert len(result["assembly_warnings"]) >= 3

    def test_degraded_assembly_quality_reflects_mixed_state(self):
        payloads = {
            "breadth_participation": _full_market_payload("breadth_participation"),
            "volatility_options": _legacy_market_payload("volatility_options"),
        }
        result = assemble_context(market_payloads=payloads)
        qs = result["quality_summary"]
        # Good module + unknown (fallback has no dashboard_metadata) → mixed
        assert qs["module_count"] == 2
        assert qs["degraded_count"] == 1

    def test_degraded_candidate_still_accessible(self):
        candidates = [_legacy_candidate("TSLA")]
        result = assemble_context(candidates=candidates)
        cand = result["candidate_context"]["candidates"][0]
        assert cand["symbol"] == "TSLA"
        assert cand.get("_fallback") is True

    def test_degraded_model_still_accessible(self):
        model = {"volatility_options": _legacy_model_response("volatility_options")}
        result = assemble_context(model_payloads=model)
        analysis = result["model_context"]["analyses"]["volatility_options"]
        assert analysis["source"] == "fallback"
        assert analysis["normalized"].get("_fallback") is True


# ════════════════════════════════════════════════════════════════════════
# 13. EDGE CASES
# ════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases: None/empty inputs, unknown keys."""

    def test_all_none_inputs(self):
        result = assemble_context(
            market_payloads=None,
            candidates=None,
            model_payloads=None,
            options=None,
        )
        assert set(result.keys()) == REQUIRED_TOP_LEVEL_KEYS
        assert result["assembly_status"] == "empty"

    def test_empty_dict_market_payloads(self):
        result = assemble_context(market_payloads={})
        assert len(result["included_modules"]) == 0
        assert len(result["missing_modules"]) == 6

    def test_unknown_engine_key_ignored(self):
        payloads = {"made_up_engine": _full_market_payload("made_up_engine")}
        result = assemble_context(market_payloads=payloads)
        # Unknown key is not in MARKET_MODULES, so it's ignored
        assert "made_up_engine" not in result["included_modules"]
        assert "made_up_engine" not in result["market_context"]

    def test_payload_with_none_engine_result(self):
        payload = {"engine_result": None, "data_quality": {}}
        result = assemble_context(market_payloads={"breadth_participation": payload})
        # Should degrade but not crash
        assert set(result.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_empty_candidate_list(self):
        result = assemble_context(candidates=[])
        assert result["candidate_context"]["count"] == 0

    def test_metadata_counts_consistent(self):
        payloads = {
            "breadth_participation": _full_market_payload("breadth_participation"),
            "volatility_options": _full_market_payload("volatility_options"),
        }
        candidates = [_normalized_stock_candidate("AAPL")]
        model = {"breadth_participation": _normalized_model_response("breadth_participation")}
        result = assemble_context(
            market_payloads=payloads, candidates=candidates, model_payloads=model,
        )
        assert result["metadata"]["market_module_count"] == len(result["included_modules"])
        assert result["metadata"]["candidate_count"] == result["candidate_context"]["count"]
        assert result["metadata"]["model_count"] == result["model_context"]["count"]

    def test_infer_family_stock(self):
        assert _infer_family({"price": 100, "trend_state": "up"}) == "stock"

    def test_infer_family_options(self):
        assert _infer_family({"legs": [{}]}) == "options"
        assert _infer_family({"short_strike": 500}) == "options"

    def test_infer_family_unknown(self):
        assert _infer_family({"symbol": "XYZ"}) == "unknown"

    def test_market_modules_frozen_set_has_six(self):
        assert len(MARKET_MODULES) == 6

    def test_failed_modules_list_present(self):
        result = assemble_context()
        assert "failed_modules" in result
        assert isinstance(result["failed_modules"], list)

    def test_module_sources_in_metadata(self):
        result = assemble_context()
        assert "module_sources" in result["metadata"]
        assert isinstance(result["metadata"]["module_sources"], dict)


# ════════════════════════════════════════════════════════════════════════
# 14. ALIAS RESOLUTION
# ════════════════════════════════════════════════════════════════════════

class TestAliasResolution:
    """Module key alias resolution."""

    def test_canonical_key_unchanged(self):
        assert _resolve_module_key("breadth_participation") == "breadth_participation"

    def test_alias_resolved(self):
        assert _resolve_module_key("liquidity_conditions") == "liquidity_financial_conditions"

    def test_unknown_key_passthrough(self):
        assert _resolve_module_key("made_up_key") == "made_up_key"

    def test_alias_payload_assembled_under_canonical(self):
        payload = _full_market_payload("liquidity_financial_conditions")
        result = assemble_context(market_payloads={
            "liquidity_conditions": payload,
        })
        assert "liquidity_financial_conditions" in result["included_modules"]
        assert "liquidity_financial_conditions" in result["market_context"]
        assert any("resolved to canonical" in w for w in result["assembly_warnings"])

    def test_alias_resolution_warning_produced(self):
        payload = _full_market_payload("liquidity_financial_conditions")
        ctx, included, missing, degraded, failed, warnings = _assemble_market_context({
            "liquidity_conditions": payload,
        })
        assert "liquidity_financial_conditions" in included
        assert any("liquidity_conditions" in w and "canonical" in w for w in warnings)

    def test_alias_map_entries_valid(self):
        for alias, canonical in MODULE_KEY_ALIASES.items():
            assert canonical in MARKET_MODULES, f"Alias target '{canonical}' not in MARKET_MODULES"
            assert alias not in MARKET_MODULES, f"Alias '{alias}' should not also be a canonical key"


# ════════════════════════════════════════════════════════════════════════
# 15. MODULE SOURCE TRACKING
# ════════════════════════════════════════════════════════════════════════

class TestModuleSourceTracking:
    """Per-module source attribution in metadata."""

    def test_all_normalized_sources(self):
        payloads = {k: _full_market_payload(k) for k in MARKET_MODULES}
        result = assemble_context(market_payloads=payloads)
        sources = result["metadata"]["module_sources"]
        for key in MARKET_MODULES:
            assert sources[key] == "normalized"

    def test_mixed_sources(self):
        payloads = {
            "breadth_participation": _full_market_payload("breadth_participation"),
            "volatility_options": _legacy_market_payload("volatility_options"),
        }
        result = assemble_context(market_payloads=payloads)
        sources = result["metadata"]["module_sources"]
        assert sources["breadth_participation"] == "normalized"
        assert sources["volatility_options"] == "fallback"

    def test_error_source_tracked(self):
        result = assemble_context(market_payloads={
            "breadth_participation": {},
        })
        sources = result["metadata"]["module_sources"]
        assert sources["breadth_participation"] == "error"

    def test_missing_modules_not_in_sources(self):
        payloads = {"breadth_participation": _full_market_payload("breadth_participation")}
        result = assemble_context(market_payloads=payloads)
        sources = result["metadata"]["module_sources"]
        assert "volatility_options" not in sources

    def test_source_vocabulary_valid(self):
        for source in MODULE_SOURCES:
            assert source in {"normalized", "fallback", "error"}


# ════════════════════════════════════════════════════════════════════════
# 16. FAILED VS DEGRADED DISTINCTION
# ════════════════════════════════════════════════════════════════════════

class TestFailedVsDegraded:
    """Failed modules (no data) vs degraded modules (fallback data)."""

    def test_legacy_payload_is_degraded_not_failed(self):
        payloads = {"volatility_options": _legacy_market_payload("volatility_options")}
        result = assemble_context(market_payloads=payloads)
        assert "volatility_options" in result["degraded_modules"]
        assert "volatility_options" not in result["failed_modules"]

    def test_empty_payload_is_failed_not_degraded(self):
        payloads = {"breadth_participation": {}}
        result = assemble_context(market_payloads=payloads)
        assert "breadth_participation" in result["failed_modules"]
        assert "breadth_participation" not in result["degraded_modules"]

    def test_failed_and_degraded_coexist(self):
        payloads = {
            "breadth_participation": _full_market_payload("breadth_participation"),
            "volatility_options": _legacy_market_payload("volatility_options"),
            "cross_asset_macro": {},
        }
        result = assemble_context(market_payloads=payloads)
        assert "breadth_participation" in result["included_modules"]
        assert "volatility_options" in result["degraded_modules"]
        assert "cross_asset_macro" in result["failed_modules"]

    def test_failed_module_not_in_market_context(self):
        payloads = {"breadth_participation": {}}
        result = assemble_context(market_payloads=payloads)
        assert "breadth_participation" not in result["market_context"]

    def test_failed_modules_affect_assembly_status(self):
        # 1 included + 5 failed → usable < 50% → degraded
        payloads = {
            "breadth_participation": _full_market_payload("breadth_participation"),
        }
        for key in ["volatility_options", "cross_asset_macro", "flows_positioning",
                     "liquidity_financial_conditions", "news_sentiment"]:
            payloads[key] = {}
        result = assemble_context(market_payloads=payloads)
        assert result["assembly_status"] == "degraded"

    def test_no_overlap_between_lists(self):
        """included, degraded, failed, missing must be mutually exclusive."""
        payloads = {
            "breadth_participation": _full_market_payload("breadth_participation"),
            "volatility_options": _legacy_market_payload("volatility_options"),
            "cross_asset_macro": {},
        }
        result = assemble_context(market_payloads=payloads)
        included = set(result["included_modules"])
        degraded = set(result["degraded_modules"])
        failed = set(result["failed_modules"])
        missing = set(result["missing_modules"])
        all_sets = [included, degraded, failed, missing]
        for i, s1 in enumerate(all_sets):
            for j, s2 in enumerate(all_sets):
                if i != j:
                    assert s1.isdisjoint(s2), f"Overlap in module lists at indices {i},{j}"
