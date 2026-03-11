"""Comprehensive tests for the Contradiction / Conflict Detector v1.

Test Classes
------------
1. TestOutputContract          – top-level shape, no-conflict & conflict cases
2. TestConflictItemSchema      – individual conflict items conform to schema
3. TestMarketConflicts         – label split, bull/bear clusters
4. TestCandidateConflicts      – direction mismatch, premium-sell vs cautionary
5. TestTimeHorizonConflicts    – candidate/market gap, model/context gap
6. TestModelConflicts          – model vs market tone, model vs candidate
7. TestQualityConflicts        – degraded, stale, low-confidence, missing
8. TestNoConflictProof         – aligned contexts produce clean output
9. TestDegradedInputSafety     – fallback/legacy inputs handled safely
10. TestEdgeCases              – empty inputs, single module, None fields
11. TestIntegrationProofs      – full assembled context → detector flows
"""

import pytest
from app.services.conflict_detector import (
    detect_conflicts,
    _engine_tone,
    _candidate_tone,
    _classify_label,
    _classify_score,
    _majority_market_tone,
    _infer_model_tone,
    _is_fallback,
    _make_conflict,
)

# ═════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════

TOP_LEVEL_KEYS = {
    "status", "detected_at", "conflict_count", "conflict_severity",
    "conflict_summary", "conflict_flags",
    "market_conflicts", "candidate_conflicts", "model_conflicts",
    "time_horizon_conflicts", "quality_conflicts",
    "metadata",
}

CONFLICT_ITEM_KEYS = {
    "conflict_type", "severity", "title", "description",
    "entities", "time_horizon", "evidence",
    "confidence_impact", "resolution_note",
}

METADATA_KEYS = {
    "detector_version", "engines_inspected", "candidates_inspected",
    "models_inspected", "degraded_inputs",
}


def _bullish_engine(key: str, score: float = 75.0, horizon: str = "short_term") -> dict:
    """Build a market module payload with bullish characteristics."""
    return {
        "normalized": {
            "engine_key": key,
            "engine_name": key.replace("_", " ").title(),
            "score": score,
            "label": "Bullish",
            "short_label": "Bullish",
            "confidence": 85.0,
            "signal_quality": "high",
            "time_horizon": horizon,
            "summary": f"{key} is bullish",
            "trader_takeaway": "Conditions are supportive",
            "bull_factors": ["strong breadth", "positive momentum"],
            "bear_factors": [],
            "risks": [],
            "regime_tags": ["bullish"],
            "data_quality": {
                "confidence_score": 85.0,
                "signal_quality": "high",
                "missing_inputs_count": 0,
                "warning_count": 0,
            },
            "warnings": [],
            "source_status": {"errors": {}, "proxy_count": 0, "direct_count": 5},
        },
        "source": "normalized",
    }


def _bearish_engine(key: str, score: float = 25.0, horizon: str = "short_term") -> dict:
    """Build a market module payload with bearish characteristics."""
    return {
        "normalized": {
            "engine_key": key,
            "engine_name": key.replace("_", " ").title(),
            "score": score,
            "label": "Cautionary",
            "short_label": "Bearish",
            "confidence": 80.0,
            "signal_quality": "high",
            "time_horizon": horizon,
            "summary": f"{key} is cautionary / bearish",
            "trader_takeaway": "Risk-off conditions",
            "bull_factors": [],
            "bear_factors": ["weak internals", "declining momentum", "risk elevated"],
            "risks": ["potential downside"],
            "regime_tags": ["bearish"],
            "data_quality": {
                "confidence_score": 80.0,
                "signal_quality": "high",
                "missing_inputs_count": 0,
                "warning_count": 1,
            },
            "warnings": [],
            "source_status": {"errors": {}, "proxy_count": 0, "direct_count": 5},
        },
        "source": "normalized",
    }


def _neutral_engine(key: str, score: float = 50.0, horizon: str = "short_term") -> dict:
    """Build a market module payload with neutral characteristics."""
    return {
        "normalized": {
            "engine_key": key,
            "engine_name": key.replace("_", " ").title(),
            "score": score,
            "label": "Neutral",
            "short_label": "Neutral",
            "confidence": 70.0,
            "signal_quality": "medium",
            "time_horizon": horizon,
            "summary": f"{key} is neutral / mixed",
            "trader_takeaway": "No strong directional bias",
            "bull_factors": ["some support"],
            "bear_factors": ["some resistance"],
            "risks": [],
            "regime_tags": ["neutral"],
            "data_quality": {
                "confidence_score": 70.0,
                "signal_quality": "medium",
                "missing_inputs_count": 0,
                "warning_count": 0,
            },
            "warnings": [],
            "source_status": {"errors": {}, "proxy_count": 1, "direct_count": 3},
        },
        "source": "normalized",
    }


def _fallback_engine(key: str) -> dict:
    """Build a degraded/fallback market module payload."""
    return {
        "normalized": {
            "engine_key": key,
            "engine_name": key.replace("_", " ").title(),
            "score": 50,
            "label": "neutral",
            "short_label": "neutral",
            "confidence": 0,
            "signal_quality": "low",
            "time_horizon": "short_term",
            "summary": "Fallback data",
            "warnings": ["No normalized contract available"],
            "data_quality": {
                "confidence_score": 0,
                "signal_quality": "low",
                "missing_inputs_count": 5,
                "warning_count": 1,
            },
            "_fallback": True,
        },
        "source": "fallback",
    }


def _stock_candidate(symbol: str, direction: str = "long", horizon: str = "swing") -> dict:
    return {
        "normalized": {
            "candidate_id": f"cand_{symbol}",
            "symbol": symbol,
            "scanner_key": "stock_momentum_breakout",
            "scanner_name": "Stock Momentum Breakout",
            "strategy_family": "stock",
            "direction": direction,
            "setup_quality": 80.0,
            "confidence": 0.85,
            "time_horizon": horizon,
            "data_quality": {"source": "tradier", "missing_fields": []},
        },
    }


def _options_candidate(
    symbol: str, direction: str = "short", scanner: str = "put_credit_spread",
    horizon: str = "days_to_expiry",
) -> dict:
    return {
        "normalized": {
            "candidate_id": f"cand_{symbol}_{scanner}",
            "symbol": symbol,
            "scanner_key": scanner,
            "scanner_name": scanner.replace("_", " ").title(),
            "strategy_family": "options",
            "direction": direction,
            "setup_quality": 75.0,
            "confidence": 0.80,
            "time_horizon": horizon,
            "data_quality": {"metrics_ready": True, "missing_fields": [], "warning_count": 0},
            "candidate_metrics": {"pop": 0.72, "expected_value": 15.0},
        },
    }


def _model_analysis(
    analysis_type: str, tone: str = "neutral",
    confidence: float = 0.8, status: str = "success",
    horizon: str = "short_term",
) -> dict:
    summary_map = {
        "bullish": "Market outlook is bullish and supportive with positive signals.",
        "bearish": "Cautious outlook; bearish signals dominate with negative downside risk.",
        "neutral": "Mixed signals; no strong directional bias detected.",
    }
    score_map = {"bullish": 78.0, "bearish": 22.0, "neutral": 50.0}
    return {
        "normalized": {
            "status": status,
            "analysis_type": analysis_type,
            "summary": summary_map.get(tone, "No summary."),
            "confidence": confidence,
            "time_horizon": horizon,
            "actions": [f"Consider {tone} positioning"],
            "metadata": {
                "label": tone.capitalize() if tone != "neutral" else "Mixed",
                "score": score_map.get(tone, 50.0),
            },
            "warnings": [],
        },
        "source": "normalized",
    }


def _aligned_assembled() -> dict:
    """Build an assembled context where everything is aligned / bullish."""
    market = {
        "breadth_participation": _bullish_engine("breadth_participation"),
        "volatility_options": _bullish_engine("volatility_options"),
        "cross_asset_macro": _bullish_engine("cross_asset_macro"),
        "flows_positioning": _bullish_engine("flows_positioning"),
        "liquidity_financial_conditions": _bullish_engine(
            "liquidity_financial_conditions", horizon="medium_term"
        ),
        "news_sentiment": _bullish_engine("news_sentiment", horizon="intraday"),
    }
    cands = [_stock_candidate("AAPL", direction="long")]
    models = {"trade_decision": _model_analysis("trade_decision", tone="bullish")}
    return {
        "market_context": market,
        "candidate_context": {"candidates": cands, "count": 1},
        "model_context": {"analyses": models, "count": 1},
        "quality_summary": {
            "overall_quality": "good",
            "average_confidence": 85.0,
            "module_count": 6,
            "degraded_count": 0,
            "modules": {k: {"confidence": 85, "data_quality_status": "good",
                            "signal_quality": "high", "source": "normalized"}
                        for k in market},
        },
        "freshness_summary": {
            "overall_freshness": "live",
            "module_count": 6,
            "modules": {k: {"freshness_status": "live", "last_update": None}
                        for k in market},
        },
        "horizon_summary": {
            "market_horizons": {
                "breadth_participation": "short_term",
                "volatility_options": "short_term",
                "cross_asset_macro": "short_term",
                "flows_positioning": "short_term",
                "liquidity_financial_conditions": "medium_term",
                "news_sentiment": "intraday",
            },
            "candidate_horizons": ["swing"],
            "model_horizons": {"trade_decision": "short_term"},
            "distinct_horizons": ["intraday", "short_term", "swing", "medium_term"],
            "shortest": "intraday",
            "longest": "medium_term",
        },
        "assembly_status": "complete",
    }


def _mixed_assembled() -> dict:
    """Build an assembled context with multiple disagreement types."""
    market = {
        "breadth_participation": _bullish_engine("breadth_participation"),
        "volatility_options": _bearish_engine("volatility_options"),
        "cross_asset_macro": _bearish_engine("cross_asset_macro"),
        "flows_positioning": _fallback_engine("flows_positioning"),
        "liquidity_financial_conditions": _neutral_engine(
            "liquidity_financial_conditions", horizon="medium_term"
        ),
    }
    cands = [
        _stock_candidate("SPY", direction="long"),
        _options_candidate("QQQ", direction="short"),
    ]
    models = {"trade_decision": _model_analysis("trade_decision", tone="bullish")}
    return {
        "market_context": market,
        "candidate_context": {"candidates": cands, "count": 2},
        "model_context": {"analyses": models, "count": 1},
        "quality_summary": {
            "overall_quality": "degraded",
            "average_confidence": 55.0,
            "module_count": 5,
            "degraded_count": 1,
            "modules": {
                "breadth_participation": {"confidence": 85, "data_quality_status": "good",
                                          "signal_quality": "high", "source": "normalized"},
                "volatility_options": {"confidence": 80, "data_quality_status": "good",
                                       "signal_quality": "high", "source": "normalized"},
                "cross_asset_macro": {"confidence": 80, "data_quality_status": "good",
                                      "signal_quality": "high", "source": "normalized"},
                "flows_positioning": {"confidence": 0, "data_quality_status": "unknown",
                                      "signal_quality": "low", "source": "fallback"},
                "liquidity_financial_conditions": {"confidence": 70, "data_quality_status": "good",
                                                   "signal_quality": "medium", "source": "normalized"},
            },
        },
        "freshness_summary": {
            "overall_freshness": "recent",
            "module_count": 5,
            "modules": {
                "breadth_participation": {"freshness_status": "live", "last_update": None},
                "volatility_options": {"freshness_status": "recent", "last_update": None},
                "cross_asset_macro": {"freshness_status": "live", "last_update": None},
                "flows_positioning": {"freshness_status": "unknown", "last_update": None},
                "liquidity_financial_conditions": {"freshness_status": "stale", "last_update": None},
            },
        },
        "horizon_summary": {
            "market_horizons": {
                "breadth_participation": "short_term",
                "volatility_options": "short_term",
                "cross_asset_macro": "short_term",
                "flows_positioning": "short_term",
                "liquidity_financial_conditions": "medium_term",
            },
            "candidate_horizons": ["swing", "days_to_expiry"],
            "model_horizons": {"trade_decision": "short_term"},
            "distinct_horizons": ["short_term", "swing", "days_to_expiry", "medium_term"],
            "shortest": "short_term",
            "longest": "medium_term",
        },
        "assembly_status": "partial",
    }


# ═════════════════════════════════════════════════════════════════════
# 1. Output Contract
# ═════════════════════════════════════════════════════════════════════

class TestOutputContract:
    """Top-level output shape and invariants."""

    def test_all_top_level_keys_present(self):
        result = detect_conflicts(_aligned_assembled())
        assert TOP_LEVEL_KEYS == set(result.keys())

    def test_metadata_keys(self):
        result = detect_conflicts(_aligned_assembled())
        assert METADATA_KEYS == set(result["metadata"].keys())

    def test_clean_status_when_no_conflicts(self):
        result = detect_conflicts(_aligned_assembled())
        assert result["status"] == "clean"
        assert result["conflict_count"] == 0
        assert result["conflict_severity"] == "none"

    def test_conflicts_detected_status(self):
        result = detect_conflicts(_mixed_assembled())
        assert result["status"] == "conflicts_detected"
        assert result["conflict_count"] > 0
        assert result["conflict_severity"] in ("low", "moderate", "high")

    def test_insufficient_data_on_empty(self):
        result = detect_conflicts({})
        assert result["status"] == "insufficient_data"
        assert result["conflict_count"] == 0

    def test_detected_at_is_iso_string(self):
        result = detect_conflicts(_aligned_assembled())
        assert isinstance(result["detected_at"], str)
        assert "T" in result["detected_at"]

    def test_conflict_flags_are_stable_codes(self):
        result = detect_conflicts(_mixed_assembled())
        assert isinstance(result["conflict_flags"], list)
        for flag in result["conflict_flags"]:
            assert isinstance(flag, str)
            assert "_" in flag  # taxonomy codes use underscores

    def test_conflict_summary_is_human_readable(self):
        result = detect_conflicts(_mixed_assembled())
        assert isinstance(result["conflict_summary"], str)
        assert len(result["conflict_summary"]) > 10

    def test_all_family_lists_are_lists(self):
        result = detect_conflicts(_aligned_assembled())
        for family in ("market_conflicts", "candidate_conflicts",
                       "model_conflicts", "time_horizon_conflicts",
                       "quality_conflicts"):
            assert isinstance(result[family], list)

    def test_detector_version_in_metadata(self):
        result = detect_conflicts(_aligned_assembled())
        assert result["metadata"]["detector_version"] == "1.1"


# ═════════════════════════════════════════════════════════════════════
# 2. Conflict Item Schema
# ═════════════════════════════════════════════════════════════════════

class TestConflictItemSchema:
    """Every conflict item conforms to the ConflictItem schema."""

    def test_all_items_have_required_keys(self):
        result = detect_conflicts(_mixed_assembled())
        all_items = (
            result["market_conflicts"]
            + result["candidate_conflicts"]
            + result["model_conflicts"]
            + result["time_horizon_conflicts"]
            + result["quality_conflicts"]
        )
        assert len(all_items) > 0, "Mixed scenario should produce conflicts"
        for item in all_items:
            assert CONFLICT_ITEM_KEYS == set(item.keys()), f"Bad keys in {item}"

    def test_severity_values_are_valid(self):
        result = detect_conflicts(_mixed_assembled())
        for family in ("market_conflicts", "candidate_conflicts",
                       "model_conflicts", "time_horizon_conflicts",
                       "quality_conflicts"):
            for item in result[family]:
                assert item["severity"] in ("low", "moderate", "high")

    def test_confidence_impact_values_are_valid(self):
        result = detect_conflicts(_mixed_assembled())
        for family in ("market_conflicts", "candidate_conflicts",
                       "model_conflicts", "time_horizon_conflicts",
                       "quality_conflicts"):
            for item in result[family]:
                assert item["confidence_impact"] in ("none", "minor", "moderate", "major")

    def test_entities_is_list_of_strings(self):
        result = detect_conflicts(_mixed_assembled())
        for family in ("market_conflicts", "candidate_conflicts",
                       "model_conflicts", "time_horizon_conflicts",
                       "quality_conflicts"):
            for item in result[family]:
                assert isinstance(item["entities"], list)
                for e in item["entities"]:
                    assert isinstance(e, str)

    def test_evidence_is_dict(self):
        result = detect_conflicts(_mixed_assembled())
        for family in ("market_conflicts", "candidate_conflicts",
                       "model_conflicts", "time_horizon_conflicts",
                       "quality_conflicts"):
            for item in result[family]:
                assert isinstance(item["evidence"], dict)


# ═════════════════════════════════════════════════════════════════════
# 3. Market Conflicts
# ═════════════════════════════════════════════════════════════════════

class TestMarketConflicts:
    """Market module disagreement detection."""

    def test_opposing_labels_create_label_split(self):
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bullish_engine("volatility_options"),
            "cross_asset_macro": _bearish_engine("cross_asset_macro"),
        }
        assembled = {"market_context": market, "candidate_context": {},
                      "model_context": {}, "quality_summary": {},
                      "freshness_summary": {}, "horizon_summary": {}}
        result = detect_conflicts(assembled)
        mkt = result["market_conflicts"]
        types = [c["conflict_type"] for c in mkt]
        assert "market_label_split" in types

    def test_all_bullish_no_label_split(self):
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bullish_engine("volatility_options"),
            "cross_asset_macro": _bullish_engine("cross_asset_macro"),
        }
        assembled = {"market_context": market, "candidate_context": {},
                      "model_context": {}, "quality_summary": {},
                      "freshness_summary": {}, "horizon_summary": {}}
        result = detect_conflicts(assembled)
        types = [c["conflict_type"] for c in result["market_conflicts"]]
        assert "market_label_split" not in types

    def test_label_split_severity_scales_with_balance(self):
        # 2 bullish vs 2 bearish = high (ratio 0.5)
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bullish_engine("volatility_options"),
            "cross_asset_macro": _bearish_engine("cross_asset_macro"),
            "flows_positioning": _bearish_engine("flows_positioning"),
        }
        assembled = {"market_context": market, "candidate_context": {},
                      "model_context": {}, "quality_summary": {},
                      "freshness_summary": {}, "horizon_summary": {}}
        result = detect_conflicts(assembled)
        split = [c for c in result["market_conflicts"]
                 if c["conflict_type"] == "market_label_split"]
        assert split[0]["severity"] == "high"

    def test_bull_bear_cluster_detected(self):
        # Multiple bull and bear factors across modules
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bearish_engine("volatility_options"),
            "cross_asset_macro": _neutral_engine("cross_asset_macro"),
        }
        # Add extra factors to neutral engine
        market["cross_asset_macro"]["normalized"]["bull_factors"] = [
            "some support", "positive correlation",
        ]
        market["cross_asset_macro"]["normalized"]["bear_factors"] = [
            "some resistance", "negative divergence",
        ]
        assembled = {"market_context": market, "candidate_context": {},
                      "model_context": {}, "quality_summary": {},
                      "freshness_summary": {}, "horizon_summary": {}}
        result = detect_conflicts(assembled)
        types = [c["conflict_type"] for c in result["market_conflicts"]]
        assert "market_bull_bear_cluster" in types

    def test_single_module_no_market_conflict(self):
        market = {"breadth_participation": _bullish_engine("breadth_participation")}
        assembled = {"market_context": market, "candidate_context": {},
                      "model_context": {}, "quality_summary": {},
                      "freshness_summary": {}, "horizon_summary": {}}
        result = detect_conflicts(assembled)
        assert result["market_conflicts"] == []

    def test_label_split_evidence_includes_tone_map(self):
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "cross_asset_macro": _bearish_engine("cross_asset_macro"),
        }
        assembled = {"market_context": market, "candidate_context": {},
                      "model_context": {}, "quality_summary": {},
                      "freshness_summary": {}, "horizon_summary": {}}
        result = detect_conflicts(assembled)
        split = [c for c in result["market_conflicts"]
                 if c["conflict_type"] == "market_label_split"]
        assert len(split) == 1
        ev = split[0]["evidence"]
        assert "tone_map" in ev
        assert "bullish_engines" in ev
        assert "bearish_engines" in ev


# ═════════════════════════════════════════════════════════════════════
# 4. Candidate Conflicts
# ═════════════════════════════════════════════════════════════════════

class TestCandidateConflicts:
    """Candidate direction vs market state."""

    def test_bullish_stock_in_bearish_market(self):
        market = {
            "breadth_participation": _bearish_engine("breadth_participation"),
            "volatility_options": _bearish_engine("volatility_options"),
            "cross_asset_macro": _bearish_engine("cross_asset_macro"),
        }
        cands = [_stock_candidate("AAPL", direction="long")]
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        types = [c["conflict_type"] for c in result["candidate_conflicts"]]
        assert "candidate_vs_market_direction" in types
        match = [c for c in result["candidate_conflicts"]
                 if c["conflict_type"] == "candidate_vs_market_direction"][0]
        assert "AAPL" in match["entities"]

    def test_bearish_stock_in_bullish_market(self):
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bullish_engine("volatility_options"),
        }
        cands = [_stock_candidate("TSLA", direction="short")]
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        types = [c for c in result["candidate_conflicts"]
                 if c["conflict_type"] == "candidate_vs_market_direction"]
        assert len(types) >= 1

    def test_aligned_candidate_no_direction_conflict(self):
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bullish_engine("volatility_options"),
        }
        cands = [_stock_candidate("AAPL", direction="long")]
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        dir_conflicts = [c for c in result["candidate_conflicts"]
                         if c["conflict_type"] == "candidate_vs_market_direction"]
        assert len(dir_conflicts) == 0

    def test_options_short_premium_in_bearish_market(self):
        market = {
            "breadth_participation": _bearish_engine("breadth_participation"),
            "volatility_options": _bearish_engine("volatility_options"),
            "cross_asset_macro": _bearish_engine("cross_asset_macro"),
        }
        cands = [_options_candidate("SPY", direction="short")]
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        types = [c["conflict_type"] for c in result["candidate_conflicts"]]
        assert "candidate_vs_market_regime" in types

    def test_options_short_in_bullish_market_no_regime_conflict(self):
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bullish_engine("volatility_options"),
        }
        cands = [_options_candidate("SPY", direction="short")]
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        regime_conflicts = [c for c in result["candidate_conflicts"]
                            if c["conflict_type"] == "candidate_vs_market_regime"]
        assert len(regime_conflicts) == 0

    def test_candidate_conflict_includes_evidence(self):
        market = {
            "breadth_participation": _bearish_engine("breadth_participation"),
            "volatility_options": _bearish_engine("volatility_options"),
        }
        cands = [_stock_candidate("MSFT", direction="long")]
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        dir_conflicts = [c for c in result["candidate_conflicts"]
                         if c["conflict_type"] == "candidate_vs_market_direction"]
        assert len(dir_conflicts) == 1
        ev = dir_conflicts[0]["evidence"]
        assert ev["candidate_direction"] == "bullish"
        assert ev["market_majority_tone"] == "bearish"

    def test_no_candidates_no_candidate_conflicts(self):
        market = {"breadth_participation": _bearish_engine("breadth_participation")}
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        assert result["candidate_conflicts"] == []

    def test_neutral_candidate_no_directional_conflict(self):
        market = {
            "breadth_participation": _bearish_engine("breadth_participation"),
            "volatility_options": _bearish_engine("volatility_options"),
        }
        cands = [_options_candidate("SPY", direction="neutral", scanner="iron_condor")]
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        dir_conflicts = [c for c in result["candidate_conflicts"]
                         if c["conflict_type"] == "candidate_vs_market_direction"]
        assert len(dir_conflicts) == 0


# ═════════════════════════════════════════════════════════════════════
# 5. Time-Horizon Conflicts
# ═════════════════════════════════════════════════════════════════════

class TestTimeHorizonConflicts:
    """Time-horizon mismatch detection."""

    def test_long_term_candidate_vs_intraday_market(self):
        market = {
            "news_sentiment": _bullish_engine("news_sentiment", horizon="intraday"),
        }
        cands = [_stock_candidate("AAPL", horizon="long_term")]
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {
                "market_horizons": {"news_sentiment": "intraday"},
                "shortest": "intraday", "longest": "intraday",
            },
        }
        result = detect_conflicts(assembled)
        hz_conflicts = [c for c in result["time_horizon_conflicts"]
                        if c["conflict_type"] == "horizon_candidate_market_gap"]
        assert len(hz_conflicts) == 1
        assert hz_conflicts[0]["evidence"]["min_gap"] >= 2

    def test_adjacent_horizons_no_conflict(self):
        # short_term (rank 1) vs swing (rank 2) — gap is 1, below threshold
        market = {
            "breadth_participation": _bullish_engine("breadth_participation", horizon="short_term"),
        }
        cands = [_stock_candidate("AAPL", horizon="swing")]
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {
                "market_horizons": {"breadth_participation": "short_term"},
                "shortest": "short_term", "longest": "short_term",
            },
        }
        result = detect_conflicts(assembled)
        hz_conflicts = [c for c in result["time_horizon_conflicts"]
                        if c["conflict_type"] == "horizon_candidate_market_gap"]
        assert len(hz_conflicts) == 0

    def test_model_horizon_mismatch(self):
        market = {
            "news_sentiment": _bullish_engine("news_sentiment", horizon="intraday"),
        }
        cands = [_stock_candidate("AAPL", horizon="intraday")]
        models = {"outlook": _model_analysis("outlook", horizon="long_term")}
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {"analyses": models, "count": 1},
            "quality_summary": {}, "freshness_summary": {},
            "horizon_summary": {
                "market_horizons": {"news_sentiment": "intraday"},
                "shortest": "intraday", "longest": "intraday",
            },
        }
        result = detect_conflicts(assembled)
        model_hz = [c for c in result["time_horizon_conflicts"]
                    if c["conflict_type"] == "horizon_model_market_gap"]
        assert len(model_hz) == 1

    def test_unknown_horizon_ignored(self):
        market = {
            "breadth_participation": _bullish_engine("breadth_participation", horizon="short_term"),
        }
        cands = [_stock_candidate("AAPL", horizon="unknown")]
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {
                "market_horizons": {"breadth_participation": "short_term"},
            },
        }
        result = detect_conflicts(assembled)
        hz_conflicts = [c for c in result["time_horizon_conflicts"]
                        if c["conflict_type"] == "horizon_candidate_market_gap"]
        assert len(hz_conflicts) == 0

    def test_horizon_conflict_evidence_includes_gap(self):
        market = {
            "news_sentiment": _bullish_engine("news_sentiment", horizon="intraday"),
        }
        cands = [_stock_candidate("AAPL", horizon="medium_term")]
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {
                "market_horizons": {"news_sentiment": "intraday"},
            },
        }
        result = detect_conflicts(assembled)
        hz_conflicts = [c for c in result["time_horizon_conflicts"]
                        if c["conflict_type"] == "horizon_candidate_market_gap"]
        assert len(hz_conflicts) == 1
        ev = hz_conflicts[0]["evidence"]
        assert "min_gap" in ev
        assert "candidate_horizon" in ev
        assert "market_horizons" in ev


# ═════════════════════════════════════════════════════════════════════
# 6. Model Conflicts
# ═════════════════════════════════════════════════════════════════════

class TestModelConflicts:
    """Model vs structured data conflicts."""

    def test_bullish_model_vs_bearish_market(self):
        market = {
            "breadth_participation": _bearish_engine("breadth_participation"),
            "volatility_options": _bearish_engine("volatility_options"),
            "cross_asset_macro": _bearish_engine("cross_asset_macro"),
        }
        models = {"trade_decision": _model_analysis("trade_decision", tone="bullish")}
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {"analyses": models, "count": 1},
            "quality_summary": {}, "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        model_conflicts = [c for c in result["model_conflicts"]
                           if c["conflict_type"] == "model_vs_market_tone"]
        assert len(model_conflicts) == 1
        assert model_conflicts[0]["evidence"]["model_tone"] == "bullish"
        assert model_conflicts[0]["evidence"]["market_majority_tone"] == "bearish"

    def test_aligned_model_and_market_no_conflict(self):
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bullish_engine("volatility_options"),
        }
        models = {"trade_decision": _model_analysis("trade_decision", tone="bullish")}
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {"analyses": models, "count": 1},
            "quality_summary": {}, "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        model_tone_conflicts = [c for c in result["model_conflicts"]
                                if c["conflict_type"] == "model_vs_market_tone"]
        assert len(model_tone_conflicts) == 0

    def test_model_vs_candidate_direction(self):
        market = {}  # No market to avoid market tone conflict
        cands = [_stock_candidate("AAPL", direction="long")]
        models = {"trade_decision": _model_analysis("trade_decision", tone="bearish")}
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": cands, "count": 1},
            "model_context": {"analyses": models, "count": 1},
            "quality_summary": {}, "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        vs_cand = [c for c in result["model_conflicts"]
                   if c["conflict_type"] == "model_vs_candidate_tone"]
        assert len(vs_cand) == 1
        assert "AAPL" in vs_cand[0]["entities"]

    def test_error_model_skipped(self):
        market = {
            "breadth_participation": _bearish_engine("breadth_participation"),
        }
        models = {"trade_decision": _model_analysis("trade_decision",
                                                     tone="bullish", status="error")}
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {"analyses": models, "count": 1},
            "quality_summary": {}, "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        model_tone_conflicts = [c for c in result["model_conflicts"]
                                if c["conflict_type"] == "model_vs_market_tone"]
        assert len(model_tone_conflicts) == 0

    def test_neutral_model_no_tone_conflict(self):
        market = {
            "breadth_participation": _bearish_engine("breadth_participation"),
            "volatility_options": _bearish_engine("volatility_options"),
        }
        models = {"trade_decision": _model_analysis("trade_decision", tone="neutral")}
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {"analyses": models, "count": 1},
            "quality_summary": {}, "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        model_tone_conflicts = [c for c in result["model_conflicts"]
                                if c["conflict_type"] == "model_vs_market_tone"]
        assert len(model_tone_conflicts) == 0

    def test_low_confidence_model_reduces_severity(self):
        market = {
            "breadth_participation": _bearish_engine("breadth_participation"),
            "volatility_options": _bearish_engine("volatility_options"),
        }
        models = {"trade_decision": _model_analysis(
            "trade_decision", tone="bullish", confidence=0.3
        )}
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {"analyses": models, "count": 1},
            "quality_summary": {}, "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        model_tone_conflicts = [c for c in result["model_conflicts"]
                                if c["conflict_type"] == "model_vs_market_tone"]
        assert len(model_tone_conflicts) == 1
        assert model_tone_conflicts[0]["severity"] == "low"

    def test_no_models_no_model_conflicts(self):
        assembled = {
            "market_context": {"breadth_participation": _bullish_engine("breadth_participation")},
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {"analyses": {}, "count": 0},
            "quality_summary": {}, "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        assert result["model_conflicts"] == []


# ═════════════════════════════════════════════════════════════════════
# 7. Quality / Degradation Conflicts
# ═════════════════════════════════════════════════════════════════════

class TestQualityConflicts:
    """Quality and degradation conflict detection."""

    def test_degraded_consensus_detected(self):
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "flows_positioning": _fallback_engine("flows_positioning"),
            "cross_asset_macro": _fallback_engine("cross_asset_macro"),
        }
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        deg = [c for c in result["quality_conflicts"]
               if c["conflict_type"] == "quality_degraded_consensus"]
        assert len(deg) == 1

    def test_no_fallback_no_degraded_consensus(self):
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bullish_engine("volatility_options"),
        }
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {}, "quality_summary": {},
            "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        deg = [c for c in result["quality_conflicts"]
               if c["conflict_type"] == "quality_degraded_consensus"]
        assert len(deg) == 0

    def test_stale_module_detected(self):
        assembled = {
            "market_context": {"breadth_participation": _bullish_engine("breadth_participation")},
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {
                "overall_freshness": "stale",
                "module_count": 1,
                "modules": {
                    "breadth_participation": {"freshness_status": "stale", "last_update": None},
                },
            },
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        stale = [c for c in result["quality_conflicts"]
                 if c["conflict_type"] == "quality_stale_module"]
        assert len(stale) == 1
        assert stale[0]["entities"] == ["breadth_participation"]

    def test_very_stale_higher_severity(self):
        assembled = {
            "market_context": {"breadth_participation": _bullish_engine("breadth_participation")},
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {
                "modules": {
                    "breadth_participation": {"freshness_status": "very_stale", "last_update": None},
                },
            },
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        stale = [c for c in result["quality_conflicts"]
                 if c["conflict_type"] == "quality_stale_module"]
        assert len(stale) == 1
        assert stale[0]["severity"] == "moderate"

    def test_low_confidence_module_detected(self):
        assembled = {
            "market_context": {"breadth_participation": _bullish_engine("breadth_participation")},
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {},
            "quality_summary": {
                "modules": {
                    "breadth_participation": {
                        "confidence": 20, "data_quality_status": "poor",
                        "signal_quality": "low",
                    },
                },
            },
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        low_conf = [c for c in result["quality_conflicts"]
                    if c["conflict_type"] == "quality_low_confidence_module"]
        assert len(low_conf) == 1

    def test_missing_modules_detected(self):
        # Only 2 of 6 expected engines present
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bullish_engine("volatility_options"),
        }
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        missing = [c for c in result["quality_conflicts"]
                   if c["conflict_type"] == "quality_missing_modules"]
        assert len(missing) == 1
        assert len(missing[0]["evidence"]["missing_modules"]) == 4

    def test_all_modules_present_no_missing_conflict(self):
        result = detect_conflicts(_aligned_assembled())
        missing = [c for c in result["quality_conflicts"]
                   if c["conflict_type"] == "quality_missing_modules"]
        assert len(missing) == 0

    def test_missing_modules_not_fired_when_no_engines(self):
        """When market_context is empty, don't fire missing modules (that's "insufficient")."""
        assembled = {
            "market_context": {},
            "candidate_context": {"candidates": [_stock_candidate("AAPL")], "count": 1},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        missing = [c for c in result["quality_conflicts"]
                   if c["conflict_type"] == "quality_missing_modules"]
        assert len(missing) == 0


# ═════════════════════════════════════════════════════════════════════
# 8. No-Conflict Proof
# ═════════════════════════════════════════════════════════════════════

class TestNoConflictProof:
    """Aligned contexts produce clean / no-conflict output."""

    def test_fully_aligned_bullish_clean(self):
        result = detect_conflicts(_aligned_assembled())
        assert result["status"] == "clean"
        assert result["conflict_count"] == 0
        assert result["conflict_severity"] == "none"
        assert result["market_conflicts"] == []
        assert result["candidate_conflicts"] == []
        assert result["model_conflicts"] == []
        assert result["time_horizon_conflicts"] == []
        # May have quality conflicts if modules are missing; check
        # that the aligned fixture is truly clean
        assert result["quality_conflicts"] == []

    def test_clean_summary_message(self):
        result = detect_conflicts(_aligned_assembled())
        assert "no conflicts" in result["conflict_summary"].lower()

    def test_single_bullish_engine_single_long_candidate(self):
        """Minimal aligned scenario."""
        assembled = {
            "market_context": {
                "breadth_participation": _bullish_engine("breadth_participation"),
                "volatility_options": _bullish_engine("volatility_options"),
                "cross_asset_macro": _bullish_engine("cross_asset_macro"),
                "flows_positioning": _bullish_engine("flows_positioning"),
                "liquidity_financial_conditions": _bullish_engine(
                    "liquidity_financial_conditions", horizon="medium_term"),
                "news_sentiment": _bullish_engine("news_sentiment", horizon="intraday"),
            },
            "candidate_context": {
                "candidates": [_stock_candidate("SPY", direction="long")],
                "count": 1,
            },
            "model_context": {"analyses": {}, "count": 0},
            "quality_summary": {
                "modules": {k: {"confidence": 85, "data_quality_status": "good",
                                "signal_quality": "high", "source": "normalized"}
                            for k in ["breadth_participation", "volatility_options",
                                       "cross_asset_macro", "flows_positioning",
                                       "liquidity_financial_conditions", "news_sentiment"]},
            },
            "freshness_summary": {
                "modules": {k: {"freshness_status": "live", "last_update": None}
                            for k in ["breadth_participation", "volatility_options",
                                       "cross_asset_macro", "flows_positioning",
                                       "liquidity_financial_conditions", "news_sentiment"]},
            },
            "horizon_summary": {
                "market_horizons": {
                    "breadth_participation": "short_term",
                    "volatility_options": "short_term",
                    "cross_asset_macro": "short_term",
                    "flows_positioning": "short_term",
                    "liquidity_financial_conditions": "medium_term",
                    "news_sentiment": "intraday",
                },
            },
        }
        result = detect_conflicts(assembled)
        assert result["status"] == "clean"
        assert result["conflict_count"] == 0


# ═════════════════════════════════════════════════════════════════════
# 9. Degraded Input Safety
# ═════════════════════════════════════════════════════════════════════

class TestDegradedInputSafety:
    """Fallback/legacy/degraded inputs handled without crashes."""

    def test_all_fallback_engines_no_crash(self):
        market = {
            "breadth_participation": _fallback_engine("breadth_participation"),
            "volatility_options": _fallback_engine("volatility_options"),
            "cross_asset_macro": _fallback_engine("cross_asset_macro"),
        }
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        assert result["status"] in ("clean", "conflicts_detected")
        assert result["metadata"]["degraded_inputs"] == 3

    def test_legacy_candidate_no_crash(self):
        """Candidate without normalized wrapper."""
        cand = {"symbol": "QQQ", "score": 55, "direction": "long", "_fallback": True}
        assembled = {
            "market_context": {"breadth_participation": _bearish_engine("breadth_participation"),
                               "volatility_options": _bearish_engine("volatility_options")},
            "candidate_context": {"candidates": [cand], "count": 1},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        # Should detect direction conflict (long vs bearish market)
        assert result["status"] == "conflicts_detected"

    def test_degraded_model_no_crash(self):
        model = {
            "normalized": {
                "status": "degraded",
                "analysis_type": "outlook",
                "summary": "Insufficient data",
                "confidence": None,
                "time_horizon": "unknown",
                "_fallback": True,
            },
            "source": "fallback",
        }
        assembled = {
            "market_context": {"breadth_participation": _bullish_engine("breadth_participation")},
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {"analyses": {"outlook": model}, "count": 1},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        assert result["metadata"]["degraded_inputs"] == 1

    def test_mixed_normalized_and_fallback(self):
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _fallback_engine("volatility_options"),
        }
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        assert result["metadata"]["degraded_inputs"] == 1


# ═════════════════════════════════════════════════════════════════════
# 10. Edge Cases
# ═════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases: empty inputs, None fields, minimal data."""

    def test_empty_assembled_returns_insufficient(self):
        result = detect_conflicts({})
        assert result["status"] == "insufficient_data"
        assert result["conflict_count"] == 0
        assert result["conflict_severity"] == "none"

    def test_none_market_context(self):
        result = detect_conflicts({"market_context": None})
        assert result["status"] == "insufficient_data"

    def test_engine_with_none_label_and_score(self):
        """Engine that has no label or score should not crash."""
        eng = _bullish_engine("breadth_participation")
        eng["normalized"]["label"] = None
        eng["normalized"]["short_label"] = None
        eng["normalized"]["score"] = None
        assembled = {
            "market_context": {"breadth_participation": eng},
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        assert isinstance(result, dict)

    def test_candidate_with_none_direction(self):
        cand = _stock_candidate("AAPL")
        cand["normalized"]["direction"] = None
        assembled = {
            "market_context": {"breadth_participation": _bearish_engine("breadth_participation"),
                               "volatility_options": _bearish_engine("volatility_options")},
            "candidate_context": {"candidates": [cand], "count": 1},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        # No directional conflict since direction is None → neutral
        dir_conflicts = [c for c in result["candidate_conflicts"]
                         if c["conflict_type"] == "candidate_vs_market_direction"]
        assert len(dir_conflicts) == 0

    def test_only_candidates_no_market(self):
        assembled = {
            "market_context": {},
            "candidate_context": {
                "candidates": [_stock_candidate("AAPL")],
                "count": 1,
            },
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        assert result["status"] in ("clean", "conflicts_detected")
        # No market to conflict with
        assert result["candidate_conflicts"] == []

    def test_only_models_no_market(self):
        assembled = {
            "market_context": {},
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {
                "analyses": {"outlook": _model_analysis("outlook", tone="bullish")},
                "count": 1,
            },
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        assert result["status"] in ("clean", "conflicts_detected")


# ═════════════════════════════════════════════════════════════════════
# 11. Integration Proofs
# ═════════════════════════════════════════════════════════════════════

class TestIntegrationProofs:
    """Full assembled context → conflict detector integration tests."""

    def test_aligned_assembly_produces_clean(self):
        """Proof: fully aligned, high-quality context → clean report."""
        result = detect_conflicts(_aligned_assembled())
        assert result["status"] == "clean"
        assert result["conflict_count"] == 0
        assert result["conflict_severity"] == "none"
        assert result["metadata"]["engines_inspected"] == 6
        assert result["metadata"]["candidates_inspected"] == 1
        assert result["metadata"]["models_inspected"] == 1
        assert result["metadata"]["degraded_inputs"] == 0

    def test_mixed_assembly_produces_conflicts(self):
        """Proof: mixed/degraded context → meaningful conflict report."""
        result = detect_conflicts(_mixed_assembled())
        assert result["status"] == "conflicts_detected"
        assert result["conflict_count"] >= 3  # expect multiple conflict types
        assert result["conflict_severity"] in ("moderate", "high")

        # Should have at least market + quality conflicts
        assert len(result["market_conflicts"]) >= 1
        assert len(result["quality_conflicts"]) >= 1

        # All items should conform to schema
        all_items = (
            result["market_conflicts"]
            + result["candidate_conflicts"]
            + result["model_conflicts"]
            + result["time_horizon_conflicts"]
            + result["quality_conflicts"]
        )
        assert len(all_items) == result["conflict_count"]
        for item in all_items:
            assert CONFLICT_ITEM_KEYS == set(item.keys())

    def test_mixed_assembly_evidence_is_reviewable(self):
        """Every conflict in mixed scenario has non-empty evidence."""
        result = detect_conflicts(_mixed_assembled())
        all_items = (
            result["market_conflicts"]
            + result["candidate_conflicts"]
            + result["model_conflicts"]
            + result["time_horizon_conflicts"]
            + result["quality_conflicts"]
        )
        for item in all_items:
            assert item["evidence"], f"Empty evidence in {item['conflict_type']}"


# ═════════════════════════════════════════════════════════════════════
# 12. Helper Unit Tests
# ═════════════════════════════════════════════════════════════════════

class TestHelpers:
    """Unit tests for internal helper functions."""

    def test_classify_label_bullish(self):
        assert _classify_label("Bullish") == "bullish"
        assert _classify_label("Strongly_Favored") == "bullish"
        assert _classify_label("Premium Selling Strongly Favored") == "bullish"

    def test_classify_label_bearish(self):
        assert _classify_label("Cautionary") == "bearish"
        assert _classify_label("Bearish") == "bearish"
        assert _classify_label("Risk_Off") == "bearish"

    def test_classify_label_neutral(self):
        assert _classify_label("Neutral") == "neutral"
        assert _classify_label("Mixed") == "neutral"

    def test_classify_label_none(self):
        assert _classify_label(None) == "unknown"
        assert _classify_label("") == "unknown"

    def test_classify_score(self):
        assert _classify_score(80) == "bullish"
        assert _classify_score(20) == "bearish"
        assert _classify_score(50) == "neutral"
        assert _classify_score(None) == "unknown"

    def test_engine_tone(self):
        norm = {"short_label": "Bullish", "score": 75}
        assert _engine_tone(norm) == "bullish"
        norm = {"short_label": "Unknown_regime", "score": 80}
        assert _engine_tone(norm) == "bullish"  # score fallback
        norm = {"short_label": None, "score": None}
        assert _engine_tone(norm) == "unknown"

    def test_candidate_tone(self):
        assert _candidate_tone({"direction": "long"}) == "bullish"
        assert _candidate_tone({"direction": "short"}) == "bearish"
        assert _candidate_tone({"direction": "neutral"}) == "neutral"
        assert _candidate_tone({"direction": None}) == "neutral"

    def test_is_fallback(self):
        assert _is_fallback({"source": "fallback", "normalized": {}}) is True
        assert _is_fallback({"source": "normalized", "normalized": {}}) is False
        assert _is_fallback({"normalized": {"_fallback": True}}) is True

    def test_infer_model_tone_from_metadata(self):
        norm = {"metadata": {"label": "Bullish", "score": 78}, "summary": "", "actions": []}
        assert _infer_model_tone(norm) == "bullish"

    def test_infer_model_tone_from_text(self):
        norm = {
            "metadata": {},
            "summary": "The outlook is bullish and supportive with positive momentum",
            "actions": ["Consider bullish positioning"],
        }
        assert _infer_model_tone(norm) == "bullish"

    def test_infer_model_tone_unknown_when_ambiguous(self):
        norm = {"metadata": {}, "summary": "Conditions are moderate.", "actions": []}
        assert _infer_model_tone(norm) == "unknown"

    def test_majority_market_tone(self):
        market = {
            "a": _bullish_engine("a"),
            "b": _bullish_engine("b"),
            "c": _bearish_engine("c"),
        }
        tone, counts = _majority_market_tone(market)
        assert tone == "bullish"
        assert counts["bullish"] == 2
        assert counts["bearish"] == 1


# ═════════════════════════════════════════════════════════════════════
# 13. Improved Model-Tone Inference
# ═════════════════════════════════════════════════════════════════════

class TestModelToneInferenceV2:
    """Test the improved _infer_model_tone heuristic.

    Key improvements over v1:
    - Threshold lowered from >=2-with-0 to >=1-with-0
    - "cautious" removed from bear signals (too ambiguous in model text)
    - "risk" removed (too common in neutral commentary)
    - "mixed" return value when both bull + bear signals present
    - More specific keywords added (rally, strength, decline, weakness, etc.)
    """

    def test_single_bullish_signal_detected(self):
        """One bull keyword with zero bear -> bullish (was 'unknown' in v1)."""
        norm = {"metadata": {}, "summary": "The outlook is supportive.", "actions": []}
        assert _infer_model_tone(norm) == "bullish"

    def test_single_bearish_signal_detected(self):
        """One bear keyword with zero bull -> bearish (was 'unknown' in v1)."""
        norm = {"metadata": {}, "summary": "Unfavorable conditions detected.", "actions": []}
        assert _infer_model_tone(norm) == "bearish"

    def test_cautious_no_longer_bearish(self):
        """'cautious' in model text should NOT trigger bearish tone."""
        norm = {"metadata": {}, "summary": "Take a cautious approach.", "actions": []}
        assert _infer_model_tone(norm) == "unknown"

    def test_risk_alone_not_bearish(self):
        """Bare 'risk' should NOT trigger bearish (too common)."""
        norm = {"metadata": {}, "summary": "Consider the risk/reward.", "actions": []}
        assert _infer_model_tone(norm) == "unknown"

    def test_mixed_signals_return_mixed(self):
        """Both bull and bear signals -> 'mixed' (was 'unknown' in v1)."""
        norm = {
            "metadata": {},
            "summary": "Rally in tech but decline in industrials expected.",
            "actions": [],
        }
        assert _infer_model_tone(norm) == "mixed"

    def test_mixed_does_not_trigger_model_conflict(self):
        """'mixed' model tone should not fire model_vs_market_tone conflict."""
        market = {
            "breadth_participation": _bearish_engine("breadth_participation"),
            "volatility_options": _bearish_engine("volatility_options"),
        }
        model_data = {
            "normalized": {
                "status": "success",
                "analysis_type": "outlook",
                "summary": "Rally potential but weakness in breadth signals.",
                "confidence": 0.7,
                "time_horizon": "short_term",
                "actions": ["Watch for deteriorating conditions"],
                "metadata": {},
                "warnings": [],
            },
            "source": "normalized",
        }
        assembled = {
            "market_context": market,
            "candidate_context": {"candidates": [], "count": 0},
            "model_context": {"analyses": {"outlook": model_data}, "count": 1},
            "quality_summary": {}, "freshness_summary": {}, "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        model_tone_conflicts = [c for c in result["model_conflicts"]
                                if c["conflict_type"] == "model_vs_market_tone"]
        assert len(model_tone_conflicts) == 0

    def test_new_bull_keywords(self):
        """rally, strength, recovery should count as bullish signals."""
        for keyword in ("rally", "strength", "recovery"):
            norm = {"metadata": {}, "summary": f"Expected {keyword} ahead.", "actions": []}
            assert _infer_model_tone(norm) == "bullish", f"'{keyword}' should be bullish"

    def test_new_bear_keywords(self):
        """decline, recession, weakness, deteriorating should be bearish."""
        for keyword in ("decline", "recession", "weakness", "deteriorating"):
            norm = {"metadata": {}, "summary": f"Signs of {keyword} emerging.", "actions": []}
            assert _infer_model_tone(norm) == "bearish", f"'{keyword}' should be bearish"

    def test_metadata_still_takes_priority(self):
        """When metadata label is set, text heuristic is not reached."""
        norm = {
            "metadata": {"label": "Bearish", "score": 22.0},
            "summary": "The outlook is bullish and supportive.",
            "actions": [],
        }
        assert _infer_model_tone(norm) == "bearish"

    def test_empty_text_returns_unknown(self):
        norm = {"metadata": {}, "summary": "", "actions": []}
        assert _infer_model_tone(norm) == "unknown"

    def test_actions_contribute_to_signal_count(self):
        """Keywords in actions list should be counted."""
        norm = {
            "metadata": {},
            "summary": "Conditions are moderate.",
            "actions": ["Consider bullish positioning"],
        }
        assert _infer_model_tone(norm) == "bullish"


# ═════════════════════════════════════════════════════════════════════
# 14. Regime-Tag Disagreement
# ═════════════════════════════════════════════════════════════════════

class TestRegimeTagDisagreement:
    """Test market_regime_disagreement conflict detection.

    Heuristic: when engines have contradictory regime_tags (e.g. bullish
    vs bearish, risk_on vs risk_off) across the market_ctx, fire a
    market_regime_disagreement conflict.
    """

    def test_opposing_regime_tags_fire_conflict(self):
        """Engines with bullish vs bearish tags -> regime disagreement."""
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bearish_engine("volatility_options"),
        }
        assembled = {
            "market_context": market,
            "candidate_context": {},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        regime_conflicts = [c for c in result["market_conflicts"]
                            if c["conflict_type"] == "market_regime_disagreement"]
        assert len(regime_conflicts) >= 1
        # Check evidence structure
        rc = regime_conflicts[0]
        assert "tag_pair" in rc["evidence"]
        assert "all_engine_tags" in rc["evidence"]

    def test_aligned_regime_tags_no_conflict(self):
        """All engines bullish -> no regime disagreement."""
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bullish_engine("volatility_options"),
            "cross_asset_macro": _bullish_engine("cross_asset_macro"),
        }
        assembled = {
            "market_context": market,
            "candidate_context": {},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        regime_conflicts = [c for c in result["market_conflicts"]
                            if c["conflict_type"] == "market_regime_disagreement"]
        assert len(regime_conflicts) == 0

    def test_single_engine_no_regime_conflict(self):
        """Single engine can't have disagreement."""
        market = {"breadth_participation": _bullish_engine("breadth_participation")}
        assembled = {
            "market_context": market,
            "candidate_context": {},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        regime_conflicts = [c for c in result["market_conflicts"]
                            if c["conflict_type"] == "market_regime_disagreement"]
        assert len(regime_conflicts) == 0

    def test_regime_disagreement_severity_scales(self):
        """Balanced opposing tags -> high severity; skewed -> moderate."""
        # 2 vs 2 -> ratio 0.5 -> high
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bullish_engine("volatility_options"),
            "cross_asset_macro": _bearish_engine("cross_asset_macro"),
            "flows_positioning": _bearish_engine("flows_positioning"),
        }
        assembled = {
            "market_context": market,
            "candidate_context": {},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        regime_conflicts = [c for c in result["market_conflicts"]
                            if c["conflict_type"] == "market_regime_disagreement"]
        assert len(regime_conflicts) >= 1
        assert regime_conflicts[0]["severity"] == "high"

    def test_empty_regime_tags_no_crash(self):
        """Engines with empty/missing regime_tags -> no crash, no conflict."""
        eng1 = _bullish_engine("breadth_participation")
        eng1["normalized"]["regime_tags"] = []
        eng2 = _bearish_engine("volatility_options")
        eng2["normalized"]["regime_tags"] = []
        market = {
            "breadth_participation": eng1,
            "volatility_options": eng2,
        }
        assembled = {
            "market_context": market,
            "candidate_context": {},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        regime_conflicts = [c for c in result["market_conflicts"]
                            if c["conflict_type"] == "market_regime_disagreement"]
        assert len(regime_conflicts) == 0

    def test_regime_disagreement_in_conflict_flags(self):
        """market_regime_disagreement appears in conflict_flags."""
        market = {
            "breadth_participation": _bullish_engine("breadth_participation"),
            "volatility_options": _bearish_engine("volatility_options"),
        }
        assembled = {
            "market_context": market,
            "candidate_context": {},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        assert "market_regime_disagreement" in result["conflict_flags"]

    def test_non_contradictory_tags_no_conflict(self):
        """Tags that are not in the contradictory pairs -> no conflict."""
        eng1 = _bullish_engine("breadth_participation")
        eng1["normalized"]["regime_tags"] = ["broadening"]
        eng2 = _bearish_engine("volatility_options")
        eng2["normalized"]["regime_tags"] = ["stress"]
        market = {
            "breadth_participation": eng1,
            "volatility_options": eng2,
        }
        assembled = {
            "market_context": market,
            "candidate_context": {},
            "model_context": {},
            "quality_summary": {},
            "freshness_summary": {},
            "horizon_summary": {},
        }
        result = detect_conflicts(assembled)
        regime_conflicts = [c for c in result["market_conflicts"]
                            if c["conflict_type"] == "market_regime_disagreement"]
        # broadening vs stress is NOT in contradictory pairs
        assert len(regime_conflicts) == 0
