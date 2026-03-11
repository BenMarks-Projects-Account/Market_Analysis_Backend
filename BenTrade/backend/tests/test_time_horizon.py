"""Tests for time-horizon tagging across engines, scanners, and Context Assembler.

Covers:
  1. Vocabulary & validation (allowed values, rejection, fallback)
  2. Engine horizon mapping (all 6 engines)
  3. Scanner horizon mapping (stock + options scanners)
  4. Model horizon mapping (1D/1W/1M + analysis_type fallback)
  5. Context Assembler horizon propagation (market, candidates, models)
  6. Horizon summary builder (distinct, shortest, longest)
  7. Legacy/fallback behavior (missing horizon → unknown)
  8. Edge cases
"""

from __future__ import annotations

import pytest

from app.utils.time_horizon import (
    ALLOWED_HORIZONS,
    DURATION_HORIZONS,
    ENGINE_HORIZON_MAP,
    FAMILY_HORIZON_DEFAULTS,
    HORIZON_CATEGORIES,
    HORIZON_ORDER,
    MODEL_HORIZON_MAP,
    SCANNER_HORIZON_MAP,
    VARIABLE_HORIZONS,
    horizon_category,
    horizon_rank,
    horizons_comparable,
    resolve_engine_horizon,
    resolve_model_horizon,
    resolve_scanner_horizon,
    validate_horizon,
)
from app.services.engine_output_contract import (
    ENGINE_METADATA,
    normalize_engine_output,
)
from app.services.scanner_candidate_contract import (
    SCANNER_METADATA,
    normalize_candidate_output,
)
from app.services.model_analysis_contract import (
    normalize_model_analysis_response,
)
from app.services.context_assembler import (
    assemble_context,
    _build_horizon_summary,
)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

def _pillar_payload(engine_key, score=70, label="Neutral"):
    """Minimal pillar-engine service payload."""
    return {
        "engine_result": {
            "score": score,
            "label": label,
            "short_label": label[:6],
            "summary": f"{engine_key} summary",
            "trader_takeaway": "hold",
            "confidence_score": 80,
            "signal_quality": "medium",
            "warnings": [],
            "missing_inputs": [],
        },
        "data_quality": {
            "confidence_score": 80,
            "signal_quality": "medium",
        },
        "as_of": "2026-03-10T14:00:00Z",
    }


def _news_payload():
    """Minimal news_sentiment service payload."""
    return {
        "internal_engine": {
            "score": 55,
            "label": "Mixed",
            "summary": "Headlines mixed",
            "source_freshness": [{"source": "news", "freshness": "live"}],
        },
        "as_of": "2026-03-10T14:00:00Z",
    }


def _stock_candidate(scanner_key="stock_pullback_swing"):
    """Minimal stock scanner candidate."""
    return {
        "symbol": "AAPL",
        "composite_score": 78.5,
        "rank_score": 78.5,
        "trend_state": "uptrend",
        "price": 185.0,
        "scanner_key": scanner_key,
    }


def _options_candidate(scanner_key="put_credit_spread"):
    """Minimal options scanner candidate."""
    return {
        "symbol": "SPY",
        "underlying": "SPY",
        "short_strike": 420,
        "long_strike": 415,
        "expiration": "2026-04-17",
        "dte": 38,
        "composite_score": 85.0,
        "scanner_key": scanner_key,
        "pricing": {"spread_mid": 1.25},
    }


def _full_market_payload(engine_key):
    """Full market payload with normalized + dashboard_metadata."""
    from app.utils.time_horizon import resolve_engine_horizon
    th = resolve_engine_horizon(engine_key)
    return {
        "engine_result": {"score": 72, "label": "bullish"},
        "normalized": {
            "engine_key": engine_key,
            "engine_name": engine_key.replace("_", " ").title(),
            "score": 72,
            "label": "bullish",
            "confidence": 85,
            "signal_quality": "high",
            "time_horizon": th,
            "data_quality_status": "good",
            "normalization_version": "1.0",
            "normalized_at": "2026-03-10T14:00:00Z",
        },
        "dashboard_metadata": {
            "engine_name": engine_key,
            "data_quality_status": "good",
            "freshness_status": "live",
            "confidence": 0.85,
        },
    }


def _normalized_stock_candidate():
    """Pre-normalized stock candidate."""
    return {
        "normalized": {
            "candidate_id": "AAPL_swing_01",
            "scanner_key": "stock_pullback_swing",
            "scanner_name": "Pullback Swing",
            "strategy_family": "stock",
            "symbol": "AAPL",
            "time_horizon": "swing",
            "setup_quality": 78.5,
            "normalization_version": "1.0",
        }
    }


def _normalized_options_candidate():
    """Pre-normalized options candidate."""
    return {
        "normalized": {
            "candidate_id": "SPY_pcs_01",
            "scanner_key": "put_credit_spread",
            "scanner_name": "Put Credit Spread",
            "strategy_family": "options",
            "symbol": "SPY",
            "time_horizon": "days_to_expiry",
            "setup_quality": 85.0,
            "normalization_version": "1.0",
        }
    }


def _normalized_model_response(analysis_type="breadth_participation"):
    """Pre-normalized model analysis response."""
    from app.utils.time_horizon import resolve_model_horizon
    return {
        "normalized": {
            "status": "success",
            "analysis_type": analysis_type,
            "analysis_name": "Breadth & Participation",
            "category": "market_picture",
            "response_format": "json",
            "time_horizon": resolve_model_horizon(None, analysis_type),
            "normalization_version": "1.0",
        }
    }


# ═══════════════════════════════════════════════════════════════════════
# 1. Vocabulary & validation
# ═══════════════════════════════════════════════════════════════════════


class TestVocabulary:
    """Shared time-horizon vocabulary validation."""

    def test_allowed_horizons_has_required_values(self):
        required = {
            "intraday", "short_term", "swing", "medium_term",
            "long_term", "event_driven", "days_to_expiry", "unknown",
        }
        assert required == ALLOWED_HORIZONS

    def test_allowed_horizons_is_frozenset(self):
        assert isinstance(ALLOWED_HORIZONS, frozenset)

    def test_validate_accepts_all_allowed(self):
        for h in ALLOWED_HORIZONS:
            assert validate_horizon(h) == h

    def test_validate_rejects_invalid(self):
        assert validate_horizon("daily") == "unknown"
        assert validate_horizon("weekly") == "unknown"
        assert validate_horizon("1D") == "unknown"
        assert validate_horizon("foo") == "unknown"

    def test_validate_handles_none(self):
        assert validate_horizon(None) == "unknown"

    def test_validate_handles_empty_string(self):
        assert validate_horizon("") == "unknown"

    def test_horizon_order_contains_all_allowed(self):
        assert set(HORIZON_ORDER) == ALLOWED_HORIZONS

    def test_horizon_rank_ordering(self):
        assert horizon_rank("intraday") < horizon_rank("short_term")
        assert horizon_rank("short_term") < horizon_rank("swing")
        assert horizon_rank("swing") < horizon_rank("medium_term")
        assert horizon_rank("medium_term") < horizon_rank("long_term")

    def test_unknown_is_ranked_last(self):
        for h in ALLOWED_HORIZONS - {"unknown"}:
            assert horizon_rank(h) < horizon_rank("unknown")


# ═══════════════════════════════════════════════════════════════════════
# 2. Engine horizon mapping
# ═══════════════════════════════════════════════════════════════════════


class TestEngineHorizon:
    """Engine-level time-horizon mappings."""

    def test_news_sentiment_is_intraday(self):
        assert resolve_engine_horizon("news_sentiment") == "intraday"

    def test_breadth_is_short_term(self):
        assert resolve_engine_horizon("breadth_participation") == "short_term"

    def test_volatility_is_short_term(self):
        assert resolve_engine_horizon("volatility_options") == "short_term"

    def test_cross_asset_macro_is_short_term(self):
        assert resolve_engine_horizon("cross_asset_macro") == "short_term"

    def test_flows_is_short_term(self):
        assert resolve_engine_horizon("flows_positioning") == "short_term"

    def test_liquidity_is_medium_term(self):
        assert resolve_engine_horizon("liquidity_financial_conditions") == "medium_term"

    def test_unknown_engine_returns_unknown(self):
        assert resolve_engine_horizon("nonexistent_engine") == "unknown"

    def test_all_engines_in_map(self):
        """Every engine in ENGINE_METADATA has a horizon mapping."""
        for key in ENGINE_METADATA:
            assert key in ENGINE_HORIZON_MAP, f"{key} missing from ENGINE_HORIZON_MAP"

    def test_engine_metadata_matches_horizon_map(self):
        """ENGINE_METADATA time_horizon values agree with ENGINE_HORIZON_MAP."""
        for key, meta in ENGINE_METADATA.items():
            assert meta["time_horizon"] == ENGINE_HORIZON_MAP[key], (
                f"{key}: metadata says '{meta['time_horizon']}', "
                f"map says '{ENGINE_HORIZON_MAP[key]}'"
            )

    def test_normalize_engine_output_propagates_horizon(self):
        """normalize_engine_output() includes the shared-vocabulary horizon."""
        for engine_key in ["breadth_participation", "volatility_options",
                           "cross_asset_macro", "flows_positioning",
                           "liquidity_financial_conditions"]:
            payload = _pillar_payload(engine_key)
            result = normalize_engine_output(engine_key, payload)
            expected = resolve_engine_horizon(engine_key)
            assert result["time_horizon"] == expected, (
                f"{engine_key}: got '{result['time_horizon']}', expected '{expected}'"
            )
            assert result["time_horizon"] in ALLOWED_HORIZONS

    def test_news_engine_output_has_intraday(self):
        payload = _news_payload()
        result = normalize_engine_output("news_sentiment", payload)
        assert result["time_horizon"] == "intraday"


# ═══════════════════════════════════════════════════════════════════════
# 3. Scanner horizon mapping
# ═══════════════════════════════════════════════════════════════════════


class TestScannerHorizon:
    """Scanner-level time-horizon mappings."""

    def test_stock_scanners_are_swing(self):
        for key in ["stock_pullback_swing", "stock_momentum_breakout",
                     "stock_mean_reversion", "stock_volatility_expansion"]:
            assert resolve_scanner_horizon(key) == "swing"

    def test_options_scanners_are_days_to_expiry(self):
        for key in ["put_credit_spread", "call_credit_spread", "put_debit",
                     "call_debit", "iron_condor", "butterfly_debit",
                     "calendar_spread", "csp", "covered_call", "income"]:
            assert resolve_scanner_horizon(key) == "days_to_expiry"

    def test_family_fallback_stock(self):
        assert resolve_scanner_horizon("unknown_stock_scanner", "stock") == "swing"

    def test_family_fallback_options(self):
        assert resolve_scanner_horizon("unknown_options_scanner", "options") == "days_to_expiry"

    def test_unknown_scanner_unknown_family(self):
        assert resolve_scanner_horizon("totally_unknown") == "unknown"

    def test_none_scanner_key(self):
        assert resolve_scanner_horizon(None, "stock") == "swing"

    def test_all_scanner_metadata_in_map(self):
        """Every scanner in SCANNER_METADATA has a horizon mapping."""
        for key in SCANNER_METADATA:
            assert key in SCANNER_HORIZON_MAP, f"{key} missing from SCANNER_HORIZON_MAP"

    def test_scanner_metadata_matches_horizon_map(self):
        """SCANNER_METADATA time_horizon values agree with SCANNER_HORIZON_MAP."""
        for key, meta in SCANNER_METADATA.items():
            assert meta["time_horizon"] == SCANNER_HORIZON_MAP[key], (
                f"{key}: metadata says '{meta['time_horizon']}', "
                f"map says '{SCANNER_HORIZON_MAP[key]}'"
            )

    def test_normalize_stock_candidate_has_horizon(self):
        """normalize_candidate_output stock path includes shared-vocabulary horizon."""
        cand = _stock_candidate("stock_pullback_swing")
        result = normalize_candidate_output("stock_pullback_swing", cand)
        assert result["time_horizon"] == "swing"
        assert result["time_horizon"] in ALLOWED_HORIZONS

    def test_normalize_options_candidate_has_horizon(self):
        """normalize_candidate_output options path includes shared-vocabulary horizon."""
        cand = _options_candidate("put_credit_spread")
        result = normalize_candidate_output("put_credit_spread", cand)
        assert result["time_horizon"] == "days_to_expiry"
        assert result["time_horizon"] in ALLOWED_HORIZONS

    def test_options_candidate_preserves_dte(self):
        """Raw DTE is preserved elsewhere while time_horizon is normalized."""
        cand = _options_candidate("put_credit_spread")
        result = normalize_candidate_output("put_credit_spread", cand)
        assert result["time_horizon"] == "days_to_expiry"
        assert result["entry_context"]["dte"] == 38


# ═══════════════════════════════════════════════════════════════════════
# 4. Model horizon mapping
# ═══════════════════════════════════════════════════════════════════════


class TestModelHorizon:
    """Model-analysis time-horizon mapping."""

    def test_1d_maps_to_intraday(self):
        assert resolve_model_horizon("1D") == "intraday"

    def test_1w_maps_to_short_term(self):
        assert resolve_model_horizon("1W") == "short_term"

    def test_1m_maps_to_medium_term(self):
        assert resolve_model_horizon("1M") == "medium_term"

    def test_case_insensitive(self):
        assert resolve_model_horizon("1d") == "intraday"
        assert resolve_model_horizon("1w") == "short_term"

    def test_direct_allowed_value_passthrough(self):
        assert resolve_model_horizon("swing") == "swing"
        assert resolve_model_horizon("long_term") == "long_term"

    def test_analysis_type_fallback(self):
        """When no raw_horizon, falls back to analysis_type via ENGINE_HORIZON_MAP."""
        assert resolve_model_horizon(None, "breadth_participation") == "short_term"
        assert resolve_model_horizon(None, "news_sentiment") == "intraday"
        assert resolve_model_horizon(None, "liquidity_conditions") == "unknown"

    def test_none_both(self):
        assert resolve_model_horizon(None, None) == "unknown"

    def test_invalid_raw_horizon(self):
        assert resolve_model_horizon("bogus") == "unknown"

    def test_normalize_model_includes_horizon(self):
        """normalize_model_analysis_response includes time_horizon."""
        result = normalize_model_analysis_response(
            "breadth_participation",
            model_result={"label": "BULLISH", "score": 72, "summary": "test"},
        )
        assert "time_horizon" in result
        assert result["time_horizon"] == "short_term"

    def test_model_with_explicit_horizon_in_result(self):
        """Model result with its own time_horizon uses that value."""
        result = normalize_model_analysis_response(
            "stock_idea",
            model_result={"label": "BUY", "score": 80, "summary": "test",
                          "time_horizon": "1M"},
        )
        assert result["time_horizon"] == "medium_term"


# ═══════════════════════════════════════════════════════════════════════
# 5. Context Assembler horizon propagation
# ═══════════════════════════════════════════════════════════════════════


ENGINES = [
    "breadth_participation", "volatility_options", "cross_asset_macro",
    "flows_positioning", "liquidity_financial_conditions", "news_sentiment",
]


class TestAssemblerHorizonPropagation:
    """Assembled context propagates time horizons from sub-contracts."""

    def test_assembled_has_horizon_summary(self):
        result = assemble_context()
        assert "horizon_summary" in result

    def test_market_module_horizons_propagated(self):
        payloads = {e: _full_market_payload(e) for e in ENGINES}
        result = assemble_context(market_payloads=payloads)
        hs = result["horizon_summary"]
        assert hs["market_horizons"]["news_sentiment"] == "intraday"
        assert hs["market_horizons"]["breadth_participation"] == "short_term"
        assert hs["market_horizons"]["liquidity_financial_conditions"] == "medium_term"

    def test_candidate_horizons_propagated(self):
        candidates = [_normalized_stock_candidate(), _normalized_options_candidate()]
        result = assemble_context(candidates=candidates)
        hs = result["horizon_summary"]
        assert "swing" in hs["candidate_horizons"]
        assert "days_to_expiry" in hs["candidate_horizons"]

    def test_model_horizons_propagated(self):
        models = {
            "breadth_participation": _normalized_model_response("breadth_participation"),
        }
        result = assemble_context(model_payloads=models)
        hs = result["horizon_summary"]
        assert "breadth_participation" in hs["model_horizons"]
        assert hs["model_horizons"]["breadth_participation"] == "short_term"

    def test_fallback_market_module_has_horizon(self):
        """Legacy payload without normalized → fallback still gets time_horizon."""
        payloads = {
            "breadth_participation": {
                "engine_result": {"score": 60, "label": "neutral"},
            },
        }
        result = assemble_context(market_payloads=payloads)
        mod = result["market_context"]["breadth_participation"]
        norm = mod["normalized"]
        assert norm["time_horizon"] == "short_term"
        sd = norm.get("status_detail", {})
        assert sd.get("is_fallback") is True or sd.get("is_legacy") is True

    def test_fallback_candidate_has_horizon(self):
        """Legacy candidate without normalized → fallback has time_horizon."""
        legacy = [{"symbol": "AAPL", "price": 185, "trend_state": "up",
                    "strategy_id": "stock_pullback_swing"}]
        result = assemble_context(candidates=legacy)
        fb = result["candidate_context"]["candidates"][0]
        assert fb["time_horizon"] == "swing"
        assert fb["_fallback"] is True

    def test_legacy_options_candidate_fallback_horizon(self):
        """Legacy options candidate gets days_to_expiry from family inference."""
        legacy = [{"symbol": "SPY", "short_strike": 420, "long_strike": 415}]
        result = assemble_context(candidates=legacy)
        fb = result["candidate_context"]["candidates"][0]
        assert fb["time_horizon"] == "days_to_expiry"


# ═══════════════════════════════════════════════════════════════════════
# 6. Horizon summary builder
# ═══════════════════════════════════════════════════════════════════════


class TestHorizonSummary:
    """_build_horizon_summary produces correct rollup."""

    def test_distinct_horizons_sorted_by_rank(self):
        payloads = {e: _full_market_payload(e) for e in ENGINES}
        candidates = [_normalized_stock_candidate(), _normalized_options_candidate()]
        result = assemble_context(market_payloads=payloads, candidates=candidates)
        hs = result["horizon_summary"]
        distinct = hs["distinct_horizons"]
        # Should be sorted by horizon_rank
        ranks = [horizon_rank(h) for h in distinct]
        assert ranks == sorted(ranks)

    def test_shortest_and_longest(self):
        payloads = {e: _full_market_payload(e) for e in ENGINES}
        candidates = [_normalized_stock_candidate()]
        result = assemble_context(market_payloads=payloads, candidates=candidates)
        hs = result["horizon_summary"]
        assert hs["shortest"] == "intraday"  # from news_sentiment
        assert hs["longest"] == "medium_term"  # from liquidity_conditions

    def test_empty_assembly_horizons(self):
        result = assemble_context()
        hs = result["horizon_summary"]
        assert hs["market_horizons"] == {}
        assert hs["candidate_horizons"] == []
        assert hs["model_horizons"] == {}
        assert hs["shortest"] == "unknown"
        assert hs["longest"] == "unknown"

    def test_single_horizon_set(self):
        """When only one horizon type is present."""
        payloads = {"breadth_participation": _full_market_payload("breadth_participation")}
        result = assemble_context(market_payloads=payloads)
        hs = result["horizon_summary"]
        assert hs["shortest"] == "short_term"
        assert hs["longest"] == "short_term"

    def test_distinct_contains_all_present(self):
        payloads = {e: _full_market_payload(e) for e in ENGINES}
        result = assemble_context(market_payloads=payloads)
        hs = result["horizon_summary"]
        assert "intraday" in hs["distinct_horizons"]
        assert "short_term" in hs["distinct_horizons"]
        assert "medium_term" in hs["distinct_horizons"]


# ═══════════════════════════════════════════════════════════════════════
# 7. Legacy/fallback behavior
# ═══════════════════════════════════════════════════════════════════════


class TestLegacyFallback:
    """Legacy payloads without horizon tags degrade safely."""

    def test_legacy_market_payload_gets_horizon(self):
        """Legacy engine_result (no normalized) → fallback includes time_horizon."""
        payloads = {}
        for eng in ENGINES:
            payloads[eng] = {
                "engine_result": {"score": 50, "label": "neutral"},
            }
        result = assemble_context(market_payloads=payloads)
        for eng in ENGINES:
            mod = result["market_context"][eng]
            assert mod["normalized"]["time_horizon"] in ALLOWED_HORIZONS

    def test_candidate_without_normalized_gets_unknown_or_family(self):
        """Unknown scanner → family inference → appropriate horizon or unknown."""
        legacy = [{"symbol": "XYZ", "composite_score": 50}]
        result = assemble_context(candidates=legacy)
        fb = result["candidate_context"]["candidates"][0]
        assert fb["time_horizon"] == "unknown"  # unknown family → unknown horizon

    def test_mixed_horizon_assembly_valid(self):
        """Mix of normalized + legacy payloads → assembly remains valid."""
        payloads = {
            "breadth_participation": _full_market_payload("breadth_participation"),
            "volatility_options": {
                "engine_result": {"score": 60, "label": "cautious"},
            },
        }
        candidates = [
            _normalized_stock_candidate(),
            {"symbol": "QQQ", "short_strike": 380, "long_strike": 375},
        ]
        result = assemble_context(market_payloads=payloads, candidates=candidates)
        hs = result["horizon_summary"]
        # Should have horizons from both normalized and fallback paths
        assert len(hs["market_horizons"]) == 2
        assert len(hs["candidate_horizons"]) == 2
        assert all(h in ALLOWED_HORIZONS for h in hs["candidate_horizons"])


# ═══════════════════════════════════════════════════════════════════════
# 8. Edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases for time-horizon tagging."""

    def test_none_in_normalized_falls_back(self):
        """normalized dict with time_horizon: None → validate to unknown."""
        cand = {"normalized": {"symbol": "TEST", "time_horizon": None,
                               "scanner_key": "x"}}
        result = assemble_context(candidates=[cand])
        hs = result["horizon_summary"]
        assert hs["candidate_horizons"] == ["unknown"]

    def test_invalid_horizon_in_normalized_falls_back(self):
        """normalized dict with invalid time_horizon → validate to unknown."""
        cand = {"normalized": {"symbol": "TEST", "time_horizon": "daily",
                               "scanner_key": "x"}}
        result = assemble_context(candidates=[cand])
        hs = result["horizon_summary"]
        assert hs["candidate_horizons"] == ["unknown"]

    def test_horizon_summary_keys(self):
        result = assemble_context()
        hs = result["horizon_summary"]
        expected_keys = {
            "market_horizons", "candidate_horizons", "model_horizons",
            "distinct_horizons", "shortest", "longest",
        }
        assert set(hs.keys()) == expected_keys

    def test_model_horizon_map_values_all_valid(self):
        for v in MODEL_HORIZON_MAP.values():
            assert v in ALLOWED_HORIZONS

    def test_engine_horizon_map_values_all_valid(self):
        for v in ENGINE_HORIZON_MAP.values():
            assert v in ALLOWED_HORIZONS

    def test_scanner_horizon_map_values_all_valid(self):
        for v in SCANNER_HORIZON_MAP.values():
            assert v in ALLOWED_HORIZONS

    def test_family_defaults_all_valid(self):
        for v in FAMILY_HORIZON_DEFAULTS.values():
            assert v in ALLOWED_HORIZONS


# ═══════════════════════════════════════════════════════════════════════
# 9. Integration proofs
# ═══════════════════════════════════════════════════════════════════════


class TestIntegrationProofs:
    """Full-path integration scenarios proving horizon propagation."""

    def test_complete_multi_module_assembly(self):
        """All 6 engines + 2 candidates + 1 model → horizons all present."""
        payloads = {e: _full_market_payload(e) for e in ENGINES}
        candidates = [_normalized_stock_candidate(), _normalized_options_candidate()]
        models = {"breadth_participation": _normalized_model_response("breadth_participation")}

        result = assemble_context(
            market_payloads=payloads,
            candidates=candidates,
            model_payloads=models,
        )

        hs = result["horizon_summary"]
        # All 6 engines have horizons
        assert len(hs["market_horizons"]) == 6
        assert all(h in ALLOWED_HORIZONS for h in hs["market_horizons"].values())
        # 2 candidates
        assert len(hs["candidate_horizons"]) == 2
        assert "swing" in hs["candidate_horizons"]
        assert "days_to_expiry" in hs["candidate_horizons"]
        # 1 model
        assert len(hs["model_horizons"]) == 1
        # Span
        assert hs["shortest"] == "intraday"
        assert hs["longest"] == "medium_term"
        # At least 3 distinct horizons
        assert len(hs["distinct_horizons"]) >= 3

    def test_degraded_legacy_fallback_assembly(self):
        """Mix of legacy + normalized → all horizons present via fallback."""
        payloads = {
            "breadth_participation": _full_market_payload("breadth_participation"),
            "volatility_options": {
                "engine_result": {"score": 55, "label": "cautious"},
            },
        }
        candidates = [
            _normalized_stock_candidate(),
            {"symbol": "QQQ", "strategy_id": "put_credit_spread",
             "short_strike": 380, "long_strike": 375},
        ]
        models = {
            "breadth_participation": _normalized_model_response("breadth_participation"),
            "stock_idea": {"response": "some text"},
        }

        result = assemble_context(
            market_payloads=payloads,
            candidates=candidates,
            model_payloads=models,
        )

        hs = result["horizon_summary"]
        # 2 market modules
        assert len(hs["market_horizons"]) == 2
        assert hs["market_horizons"]["breadth_participation"] == "short_term"
        assert hs["market_horizons"]["volatility_options"] == "short_term"
        # 2 candidates (one normalized, one fallback)
        assert len(hs["candidate_horizons"]) == 2
        # All horizons valid
        for h in hs["candidate_horizons"]:
            assert h in ALLOWED_HORIZONS
        # Models
        assert len(hs["model_horizons"]) == 2
        # Assembly doesn't fail
        assert result["assembly_status"] in {"partial", "degraded", "complete"}


# ═══════════════════════════════════════════════════════════════════════
# 10. Horizon categories & comparability
# ═══════════════════════════════════════════════════════════════════════


class TestHorizonCategories:
    """Horizon category classification and comparability helpers."""

    # ── Category classification ──────────────────────────────────────

    def test_duration_horizons_classified(self):
        for h in ("intraday", "short_term", "swing", "medium_term", "long_term"):
            assert horizon_category(h) == "duration", f"{h} should be duration"

    def test_variable_horizons_classified(self):
        assert horizon_category("event_driven") == "variable"
        assert horizon_category("days_to_expiry") == "variable"

    def test_unknown_is_unclassified(self):
        assert horizon_category("unknown") == "unclassified"

    def test_invalid_horizon_is_unclassified(self):
        assert horizon_category("bogus") == "unclassified"

    def test_categories_cover_all_allowed(self):
        for h in ALLOWED_HORIZONS:
            cat = horizon_category(h)
            assert cat in ("duration", "variable", "unclassified"), (
                f"{h} has unexpected category '{cat}'"
            )

    def test_duration_set_complete(self):
        assert DURATION_HORIZONS == frozenset({
            "intraday", "short_term", "swing", "medium_term", "long_term",
        })

    def test_variable_set_complete(self):
        assert VARIABLE_HORIZONS == frozenset({"event_driven", "days_to_expiry"})

    def test_categories_dict_covers_all_allowed(self):
        assert set(HORIZON_CATEGORIES.keys()) == ALLOWED_HORIZONS

    def test_duration_plus_variable_plus_unknown_equals_allowed(self):
        assert DURATION_HORIZONS | VARIABLE_HORIZONS | {"unknown"} == ALLOWED_HORIZONS

    # ── Comparability ────────────────────────────────────────────────

    def test_duration_vs_duration_comparable(self):
        assert horizons_comparable("intraday", "long_term") is True
        assert horizons_comparable("short_term", "swing") is True

    def test_duration_vs_variable_not_comparable(self):
        assert horizons_comparable("short_term", "event_driven") is False
        assert horizons_comparable("swing", "days_to_expiry") is False

    def test_variable_vs_variable_not_comparable(self):
        assert horizons_comparable("event_driven", "days_to_expiry") is False

    def test_unknown_not_comparable(self):
        assert horizons_comparable("unknown", "short_term") is False
        assert horizons_comparable("event_driven", "unknown") is False

    def test_same_duration_comparable(self):
        assert horizons_comparable("swing", "swing") is True

    def test_same_variable_not_comparable(self):
        # Even same variable type — "comparable" means reliable rank gap,
        # and rank is just a default position for variable horizons.
        assert horizons_comparable("event_driven", "event_driven") is False


# ═══════════════════════════════════════════════════════════════════════
# 11. Event-driven vs duration-based scenarios
# ═══════════════════════════════════════════════════════════════════════


class TestEventDrivenSemantics:
    """Event-driven horizon is catalyst-based, not just short-duration."""

    def test_event_driven_has_distinct_rank_from_short_term(self):
        """event_driven is not the same rank as short_term."""
        assert horizon_rank("event_driven") != horizon_rank("short_term")

    def test_event_driven_between_swing_and_medium_term(self):
        """Default placement: swing < event_driven < medium_term."""
        assert horizon_rank("swing") < horizon_rank("event_driven")
        assert horizon_rank("event_driven") < horizon_rank("medium_term")

    def test_event_driven_is_variable_not_duration(self):
        assert "event_driven" in VARIABLE_HORIZONS
        assert "event_driven" not in DURATION_HORIZONS

    def test_event_driven_gap_vs_short_term_is_approximate(self):
        """Gap between event_driven and short_term exists but is approximate."""
        gap = abs(horizon_rank("event_driven") - horizon_rank("short_term"))
        assert gap == 2  # rank 3 - rank 1
        # But this comparison is NOT reliable:
        assert horizons_comparable("event_driven", "short_term") is False

    def test_days_to_expiry_gap_vs_swing_is_approximate(self):
        """days_to_expiry at rank 4 vs swing at rank 2 — gap=2 but approximate."""
        gap = abs(horizon_rank("days_to_expiry") - horizon_rank("swing"))
        assert gap == 2
        assert horizons_comparable("days_to_expiry", "swing") is False

    def test_coexist_in_horizon_summary(self):
        """Assembly with both event_driven and days_to_expiry doesn't crash."""
        # Simulate pre-normalized candidates with these horizons
        event_cand = {"normalized": {
            "candidate_id": "AAPL_event_01",
            "scanner_key": "earnings_play",
            "scanner_name": "Earnings Play",
            "strategy_family": "options",
            "symbol": "AAPL",
            "time_horizon": "event_driven",
            "setup_quality": 75.0,
        }}
        dte_cand = {"normalized": {
            "candidate_id": "SPY_pcs_01",
            "scanner_key": "put_credit_spread",
            "scanner_name": "Put Credit Spread",
            "strategy_family": "options",
            "symbol": "SPY",
            "time_horizon": "days_to_expiry",
            "setup_quality": 82.0,
        }}
        result = assemble_context(candidates=[event_cand, dte_cand])
        hs = result["horizon_summary"]
        assert "event_driven" in hs["candidate_horizons"]
        assert "days_to_expiry" in hs["candidate_horizons"]
        assert "event_driven" in hs["distinct_horizons"]
        assert "days_to_expiry" in hs["distinct_horizons"]

    def test_close_duration_different_semantic_type(self):
        """swing (rank 2) and event_driven (rank 3) are close in rank but
        semantically different — one is calendar-based, the other catalyst-based."""
        gap = abs(horizon_rank("swing") - horizon_rank("event_driven"))
        assert gap == 1  # very close in rank
        assert horizons_comparable("swing", "event_driven") is False


# ═══════════════════════════════════════════════════════════════════════
# 12. DTE-derived classification
# ═══════════════════════════════════════════════════════════════════════


class TestDTEClassification:
    """days_to_expiry classification is honest about what it knows."""

    def test_all_options_scanners_get_days_to_expiry(self):
        """Every options scanner in the map resolves to days_to_expiry."""
        options_keys = [k for k, v in SCANNER_HORIZON_MAP.items()
                        if v == "days_to_expiry"]
        assert len(options_keys) >= 8  # at least 8 options scanner keys

    def test_options_family_default_is_days_to_expiry(self):
        assert FAMILY_HORIZON_DEFAULTS["options"] == "days_to_expiry"

    def test_dte_preserved_separately_from_horizon(self):
        """DTE is a numeric field in entry_context, not collapsed into horizon."""
        cand = _options_candidate("put_credit_spread")
        result = normalize_candidate_output("put_credit_spread", cand)
        assert result["time_horizon"] == "days_to_expiry"  # semantic label
        assert result["entry_context"]["dte"] == 38  # raw numeric value

    def test_days_to_expiry_is_variable_not_duration(self):
        assert "days_to_expiry" in VARIABLE_HORIZONS
        assert "days_to_expiry" not in DURATION_HORIZONS

    def test_days_to_expiry_default_rank_between_swing_and_medium(self):
        assert horizon_rank("swing") < horizon_rank("days_to_expiry")
        assert horizon_rank("days_to_expiry") < horizon_rank("medium_term")


# ═══════════════════════════════════════════════════════════════════════
# 13. Dashboard metadata horizon exposure
# ═══════════════════════════════════════════════════════════════════════


class TestDashboardMetadataHorizon:
    """Dashboard metadata now exposes time_horizon for each engine."""

    def test_dashboard_metadata_has_time_horizon(self):
        from app.services.dashboard_metadata_contract import build_dashboard_metadata
        result = build_dashboard_metadata("breadth_participation")
        assert "time_horizon" in result
        assert result["time_horizon"] == "short_term"

    def test_dashboard_metadata_horizons_match_engine_map(self):
        from app.services.dashboard_metadata_contract import build_dashboard_metadata
        for engine_key, expected_horizon in ENGINE_HORIZON_MAP.items():
            result = build_dashboard_metadata(engine_key)
            assert result["time_horizon"] == expected_horizon, (
                f"{engine_key}: dashboard_metadata says '{result['time_horizon']}', "
                f"expected '{expected_horizon}'"
            )

    def test_unknown_engine_dashboard_metadata_horizon_unknown(self):
        from app.services.dashboard_metadata_contract import build_dashboard_metadata
        result = build_dashboard_metadata("nonexistent_engine")
        assert result["time_horizon"] == "unknown"

    def test_dashboard_metadata_horizon_in_allowed(self):
        from app.services.dashboard_metadata_contract import build_dashboard_metadata
        for engine_key in ENGINE_HORIZON_MAP:
            result = build_dashboard_metadata(engine_key)
            assert result["time_horizon"] in ALLOWED_HORIZONS


# ═══════════════════════════════════════════════════════════════════════
# 14. Cross-family consistency
# ═══════════════════════════════════════════════════════════════════════


class TestCrossFamilyConsistency:
    """Engines, stock scanners, and options scanners use one horizon family."""

    def test_all_engine_horizons_in_allowed(self):
        for h in ENGINE_HORIZON_MAP.values():
            assert h in ALLOWED_HORIZONS

    def test_all_scanner_horizons_in_allowed(self):
        for h in SCANNER_HORIZON_MAP.values():
            assert h in ALLOWED_HORIZONS

    def test_all_model_horizons_in_allowed(self):
        for h in MODEL_HORIZON_MAP.values():
            assert h in ALLOWED_HORIZONS

    def test_all_family_defaults_in_allowed(self):
        for h in FAMILY_HORIZON_DEFAULTS.values():
            assert h in ALLOWED_HORIZONS

    def test_stock_scanners_are_duration_based(self):
        """Stock scanner horizons should be duration-based (swing)."""
        for key in ["stock_pullback_swing", "stock_momentum_breakout",
                     "stock_mean_reversion", "stock_volatility_expansion"]:
            h = resolve_scanner_horizon(key)
            assert horizon_category(h) == "duration"

    def test_options_scanners_are_variable(self):
        """Options scanner horizons should be variable (days_to_expiry)."""
        for key in ["put_credit_spread", "iron_condor", "butterfly_debit"]:
            h = resolve_scanner_horizon(key)
            assert horizon_category(h) == "variable"

    def test_engines_all_duration_based(self):
        """All market engines expose duration-based horizons."""
        for key, h in ENGINE_HORIZON_MAP.items():
            assert horizon_category(h) == "duration", (
                f"Engine '{key}' has non-duration horizon '{h}'"
            )
