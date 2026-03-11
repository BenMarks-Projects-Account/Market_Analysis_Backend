"""Tests for engine_output_contract — normalized Market Engine output.

Test categories:
  1. Contract shape verification — all required fields present, correct types
  2. Pillar engine normalization — breadth, vol, cross-asset, flows, liquidity
  3. News engine normalization — structurally different input
  4. Edge cases — missing data, None engine_result, empty payloads
  5. Helper functions — regime tags, drivers, risks, etc.
"""

import pytest

from app.services.engine_output_contract import (
    ENGINE_METADATA,
    normalize_engine_output,
    _build_freshness,
    _build_risks,
    _build_source_status,
    _derive_regime_tags,
    _extract_detail_sections,
    _extract_drivers,
    _extract_pillar_scores,
    _extract_supporting_metrics,
    _normalize_news,
    _normalize_pillar_engine,
)

# ═══════════════════════════════════════════════════════════════════════
# REQUIRED FIELDS — the canonical contract shape
# ═══════════════════════════════════════════════════════════════════════

REQUIRED_FIELDS = {
    "engine_key",
    "engine_name",
    "as_of",
    "score",
    "label",
    "short_label",
    "confidence",
    "signal_quality",
    "time_horizon",
    "freshness",
    "summary",
    "trader_takeaway",
    "bull_factors",
    "bear_factors",
    "risks",
    "regime_tags",
    "supporting_metrics",
    "contradiction_flags",
    "data_quality",
    "warnings",
    "source_status",
    "pillar_scores",
    "detail_sections",
}


# ═══════════════════════════════════════════════════════════════════════
# FIXTURES — realistic engine payloads
# ═══════════════════════════════════════════════════════════════════════

def _breadth_payload():
    """Realistic breadth service payload."""
    return {
        "engine_result": {
            "engine": "breadth_participation",
            "as_of": "2026-03-15T14:00:00Z",
            "score": 72.5,
            "label": "Healthy Breadth",
            "short_label": "Healthy",
            "confidence_score": 85.0,
            "signal_quality": "high",
            "universe": {
                "name": "S&P 500",
                "expected_count": 503,
                "actual_count": 498,
                "coverage_pct": 99.0,
            },
            "pillar_scores": {
                "participation_breadth": 78.0,
                "trend_breadth": 65.0,
                "volume_breadth": 70.0,
                "leadership_quality": 80.0,
                "participation_stability": 68.0,
            },
            "pillar_weights": {
                "participation_breadth": 0.30,
                "trend_breadth": 0.20,
                "volume_breadth": 0.15,
                "leadership_quality": 0.20,
                "participation_stability": 0.15,
            },
            "pillar_explanations": {
                "participation_breadth": "Broad participation across sectors.",
                "trend_breadth": "Positive but not accelerating.",
                "volume_breadth": "Volume confirming price action.",
                "leadership_quality": "Strong leadership from large-caps.",
                "participation_stability": "Steady participation over past 5 days.",
            },
            "diagnostics": {
                "pillar_details": {
                    "participation_breadth": {
                        "score": 78.0,
                        "submetrics": [
                            {"name": "advance_decline_ratio", "raw_value": 2.5, "score": 80.0},
                            {"name": "pct_above_200ma", "raw_value": 0.65, "score": 72.0},
                        ],
                    },
                },
                "quality_scores": {"data_quality_score": 90.0},
            },
            "summary": "Breadth is healthy with broad participation.",
            "trader_takeaway": "Conditions support directional strategies.",
            "positive_contributors": ["Strong A/D breadth", "Leadership from tech"],
            "negative_contributors": ["Volume breadth lagging"],
            "conflicting_signals": ["Small-caps underperforming"],
            "warnings": ["EW benchmark stale by 2h"],
            "missing_inputs": ["sector_rotation_score"],
            "raw_inputs": {},
        },
        "data_quality": {
            "universe_coverage_pct": 99.0,
            "signal_quality": "high",
            "confidence_score": 85.0,
            "data_quality_score": 90.0,
            "missing_inputs_count": 1,
            "warning_count": 1,
        },
        "compute_duration_s": 0.45,
        "as_of": "2026-03-15T14:00:00Z",
    }


def _volatility_payload():
    """Realistic volatility service payload."""
    return {
        "engine_result": {
            "engine": "volatility_options",
            "as_of": "2026-03-15T14:00:00Z",
            "score": 68.0,
            "label": "Premium Selling Favorable",
            "short_label": "Favorable",
            "confidence_score": 78.0,
            "signal_quality": "medium",
            "pillar_scores": {
                "volatility_regime": 72.0,
                "volatility_structure": 65.0,
                "tail_risk_skew": 60.0,
                "positioning_options_posture": 70.0,
                "strategy_suitability": 68.0,
            },
            "pillar_weights": {
                "volatility_regime": 0.25,
                "volatility_structure": 0.25,
                "tail_risk_skew": 0.20,
                "positioning_options_posture": 0.15,
                "strategy_suitability": 0.15,
            },
            "pillar_explanations": {
                "volatility_regime": "VIX in normal range.",
                "volatility_structure": "Term structure in contango.",
                "tail_risk_skew": "Moderate skew.",
                "positioning_options_posture": "Neutral positioning.",
                "strategy_suitability": "Premium selling favorable.",
            },
            "strategy_scores": {
                "premium_selling": {"score": 75.0, "description": "Iron condors favorable"},
                "directional": {"score": 50.0, "description": "Moderate opportunity"},
                "vol_structure_plays": {"score": 60.0, "description": "Calendar spreads OK"},
                "hedging": {"score": 40.0, "description": "Low hedging urgency"},
            },
            "diagnostics": {
                "pillar_details": {
                    "volatility_regime": {
                        "score": 72.0,
                        "submetrics": [
                            {"name": "vix_level", "raw_value": 16.5, "score": 75.0},
                            {"name": "vix_rank_30d", "raw_value": 0.35, "score": 65.0},
                        ],
                    },
                },
            },
            "summary": "Volatility environment is favorable for premium selling.",
            "trader_takeaway": "Consider iron condors and credit spreads.",
            "positive_contributors": ["Low VIX", "Normal contango"],
            "negative_contributors": ["Moderate skew pressure"],
            "conflicting_signals": [],
            "warnings": [],
            "missing_inputs": [],
            "raw_inputs": {},
        },
        "data_quality": {
            "signal_quality": "medium",
            "confidence_score": 78.0,
            "missing_inputs_count": 0,
            "warning_count": 0,
        },
        "compute_duration_s": 0.38,
        "as_of": "2026-03-15T14:00:00Z",
    }


def _cross_asset_payload():
    """Realistic cross-asset service payload."""
    return {
        "engine_result": {
            "engine": "cross_asset_macro",
            "as_of": "2026-03-15T14:00:00Z",
            "score": 62.0,
            "label": "Partial Confirmation",
            "short_label": "Partial",
            "confidence_score": 70.0,
            "signal_quality": "medium",
            "pillar_scores": {
                "rates_yield_curve": 55.0,
                "dollar_commodity": 65.0,
                "credit_risk_appetite": 70.0,
                "defensive_vs_growth": 58.0,
                "macro_coherence": 60.0,
            },
            "pillar_weights": {
                "rates_yield_curve": 0.25,
                "dollar_commodity": 0.20,
                "credit_risk_appetite": 0.25,
                "defensive_vs_growth": 0.15,
                "macro_coherence": 0.15,
            },
            "pillar_explanations": {
                "rates_yield_curve": "Yield curve mildly positive.",
                "dollar_commodity": "Dollar neutral.",
                "credit_risk_appetite": "Credit spreads tight.",
                "defensive_vs_growth": "Slight growth tilt.",
                "macro_coherence": "Moderate agreement.",
            },
            "diagnostics": {
                "pillar_details": {},
                "signal_provenance": {
                    "ten_year_yield": {"source": "FRED", "type": "direct"},
                    "vix_level": {"source": "derived", "type": "proxy"},
                },
            },
            # Cross-asset uses confirming/contradicting/mixed
            "confirming_signals": ["Credit spreads tight", "Oil stable"],
            "contradicting_signals": ["Yield curve flat"],
            "mixed_signals": ["Dollar neutral"],
            "summary": "Macro environment partially confirms equity trend.",
            "trader_takeaway": "Proceed with reduced position size.",
            "warnings": [],
            "missing_inputs": [],
            "raw_inputs": {},
        },
        "data_quality": {
            "signal_quality": "medium",
            "confidence_score": 70.0,
            "missing_inputs_count": 0,
            "warning_count": 0,
            "source_errors": {"FRED_DGS10": "timeout"},
        },
        "cache_info": {
            "cache_hit": False,
            "engine_run_at": "2026-03-15T14:00:00Z",
            "cache_ttl_s": 120,
        },
        "compute_duration_s": 1.2,
        "as_of": "2026-03-15T14:00:00Z",
    }


def _flows_payload():
    """Realistic flows & positioning service payload."""
    return {
        "engine_result": {
            "engine": "flows_positioning",
            "as_of": "2026-03-15T14:00:00Z",
            "score": 55.0,
            "label": "Mixed but Tradable",
            "short_label": "Mixed",
            "confidence_score": 60.0,
            "signal_quality": "low",
            "pillar_scores": {
                "positioning_pressure": 58.0,
                "crowding_stretch": 50.0,
                "squeeze_unwind_risk": 55.0,
                "flow_direction_persistence": 52.0,
                "positioning_stability": 60.0,
            },
            "pillar_weights": {
                "positioning_pressure": 0.25,
                "crowding_stretch": 0.20,
                "squeeze_unwind_risk": 0.20,
                "flow_direction_persistence": 0.20,
                "positioning_stability": 0.15,
            },
            "pillar_explanations": {
                "positioning_pressure": "Neutral positioning.",
                "crowding_stretch": "No crowding detected.",
                "squeeze_unwind_risk": "Low squeeze risk.",
                "flow_direction_persistence": "Flows neutral.",
                "positioning_stability": "Stable positioning.",
            },
            "diagnostics": {
                "pillar_details": {},
                "signal_provenance": {
                    "put_call_ratio": {"source": "derived", "type": "proxy"},
                    "etf_flow_proxy": {"source": "derived", "type": "proxy"},
                    "dealer_gamma_proxy": {"source": "derived", "type": "proxy"},
                },
            },
            "strategy_bias": {
                "description": "No strong directional bias",
                "calls_favored": None,
                "puts_favored": None,
                "mean_reversion_edge": "low",
                "trend_continuation_edge": "low",
                "spread_width_context": "normal",
            },
            "summary": "Flows are mixed with no strong directional signal.",
            "trader_takeaway": "Market-neutral strategies preferred.",
            "positive_contributors": ["Stable positioning"],
            "negative_contributors": ["Weak flow momentum"],
            "conflicting_signals": ["P/C ratio vs flow direction"],
            "warnings": ["All inputs are proxy-based (Phase 1)"],
            "missing_inputs": [],
            "raw_inputs": {},
        },
        "data_quality": {
            "signal_quality": "low",
            "confidence_score": 60.0,
            "missing_inputs_count": 0,
            "warning_count": 1,
            "source_errors": {},
        },
        "cache_info": {
            "cache_hit": False,
            "engine_run_at": "2026-03-15T14:00:00Z",
            "cache_ttl_s": 90,
        },
        "compute_duration_s": 0.55,
        "as_of": "2026-03-15T14:00:00Z",
    }


def _liquidity_payload():
    """Realistic liquidity & conditions service payload."""
    return {
        "engine_result": {
            "engine": "liquidity_financial_conditions",
            "as_of": "2026-03-15T14:00:00Z",
            "score": 65.0,
            "label": "Supportive Conditions",
            "short_label": "Supportive",
            "confidence_score": 72.0,
            "signal_quality": "medium",
            "pillar_scores": {
                "rates_policy_pressure": 60.0,
                "financial_conditions_tightness": 68.0,
                "credit_funding_stress": 70.0,
                "dollar_global_liquidity": 62.0,
                "liquidity_stability_fragility": 65.0,
            },
            "pillar_weights": {
                "rates_policy_pressure": 0.25,
                "financial_conditions_tightness": 0.25,
                "credit_funding_stress": 0.20,
                "dollar_global_liquidity": 0.15,
                "liquidity_stability_fragility": 0.15,
            },
            "pillar_explanations": {
                "rates_policy_pressure": "Rates accommodative.",
                "financial_conditions_tightness": "Conditions loose.",
                "credit_funding_stress": "Credit spreads tight.",
                "dollar_global_liquidity": "Dollar mildly strong.",
                "liquidity_stability_fragility": "Stable conditions.",
            },
            "diagnostics": {"pillar_details": {}},
            "support_vs_stress": {
                "liquidity_support_factors": ["Low credit spreads", "Accommodative rates"],
                "liquidity_stress_factors": ["Dollar strength"],
                "net_assessment": "supportive",
                "implication_for_risk_assets": "Favorable for risk-on positioning",
            },
            "summary": "Liquidity conditions are supportive.",
            "trader_takeaway": "Environment supports risk-on strategies.",
            "positive_contributors": ["Low credit spreads"],
            "negative_contributors": ["Dollar headwind"],
            "conflicting_signals": [],
            "warnings": [],
            "missing_inputs": ["nfci_index"],
            "raw_inputs": {},
        },
        "data_quality": {
            "signal_quality": "medium",
            "confidence_score": 72.0,
            "missing_inputs_count": 1,
            "warning_count": 0,
            "source_errors": {},
        },
        "cache_info": {
            "cache_hit": False,
            "engine_run_at": "2026-03-15T14:00:00Z",
            "cache_ttl_s": 90,
        },
        "compute_duration_s": 0.62,
        "as_of": "2026-03-15T14:00:00Z",
    }


def _news_payload():
    """Realistic news & sentiment service payload."""
    return {
        "internal_engine": {
            "score": 58.0,
            "regime_label": "Mixed",
            "components": {
                "headline_sentiment": {"score": 62.0, "signal": "mildly positive", "inputs": {}},
                "negative_pressure": {"score": 45.0, "signal": "moderate pressure", "inputs": {}},
                "narrative_severity": {"score": 55.0, "signal": "normal", "inputs": {}},
                "source_agreement": {"score": 70.0, "signal": "agreeing", "inputs": {}},
                "macro_stress": {"score": 50.0, "signal": "neutral", "inputs": {}},
                "recency_pressure": {"score": 60.0, "signal": "recent positive", "inputs": {}},
            },
            "weights": {
                "headline_sentiment": 0.30,
                "negative_pressure": 0.20,
                "narrative_severity": 0.15,
                "source_agreement": 0.15,
                "macro_stress": 0.10,
                "recency_pressure": 0.10,
            },
            "explanation": {
                "summary": "News sentiment is mixed with mild positive bias.",
                "signal_quality": "medium",
                "trader_takeaway": "No strong news-driven thesis.",
            },
            "as_of": "2026-03-15T14:00:00Z",
        },
        "items": [
            {"headline": "Markets rally on earnings", "sentiment_score": 0.7},
            {"headline": "Fed signals caution", "sentiment_score": -0.3},
        ],
        "macro_context": {"vix": 16.5, "us_10y_yield": 4.2},
        "source_freshness": [
            {"source": "finnhub", "status": "ok", "last_fetched": "2026-03-15T13:55:00Z", "item_count": 15, "error": None},
            {"source": "polygon", "status": "error", "last_fetched": None, "item_count": 0, "error": "API key expired"},
        ],
        "as_of": "2026-03-15T14:00:00Z",
        "item_count": 2,
    }


# ═══════════════════════════════════════════════════════════════════════
# 1. CONTRACT SHAPE TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestContractShape:
    """Every normalized output must contain all required fields."""

    @pytest.mark.parametrize("engine_key,builder", [
        ("breadth_participation", _breadth_payload),
        ("volatility_options", _volatility_payload),
        ("cross_asset_macro", _cross_asset_payload),
        ("flows_positioning", _flows_payload),
        ("liquidity_financial_conditions", _liquidity_payload),
        ("news_sentiment", _news_payload),
    ])
    def test_all_required_fields_present(self, engine_key, builder):
        result = normalize_engine_output(engine_key, builder())
        missing = REQUIRED_FIELDS - set(result.keys())
        assert not missing, f"Missing fields for {engine_key}: {missing}"

    @pytest.mark.parametrize("engine_key,builder", [
        ("breadth_participation", _breadth_payload),
        ("volatility_options", _volatility_payload),
        ("cross_asset_macro", _cross_asset_payload),
        ("flows_positioning", _flows_payload),
        ("liquidity_financial_conditions", _liquidity_payload),
        ("news_sentiment", _news_payload),
    ])
    def test_field_types(self, engine_key, builder):
        r = normalize_engine_output(engine_key, builder())
        assert isinstance(r["engine_key"], str)
        assert isinstance(r["engine_name"], str)
        assert isinstance(r["label"], str)
        assert isinstance(r["short_label"], str)
        assert isinstance(r["signal_quality"], str)
        assert isinstance(r["time_horizon"], str)
        assert isinstance(r["freshness"], dict)
        assert isinstance(r["summary"], str)
        assert isinstance(r["trader_takeaway"], str)
        assert isinstance(r["bull_factors"], list)
        assert isinstance(r["bear_factors"], list)
        assert isinstance(r["risks"], list)
        assert isinstance(r["regime_tags"], list)
        assert isinstance(r["supporting_metrics"], list)
        assert isinstance(r["contradiction_flags"], list)
        assert isinstance(r["data_quality"], dict)
        assert isinstance(r["warnings"], list)
        assert isinstance(r["source_status"], dict)
        assert isinstance(r["pillar_scores"], list)
        assert isinstance(r["detail_sections"], dict)
        # score can be None (error state) or float
        assert r["score"] is None or isinstance(r["score"], (int, float))
        assert isinstance(r["confidence"], (int, float))


# ═══════════════════════════════════════════════════════════════════════
# 2. PILLAR ENGINE NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════

class TestPillarEngines:
    """Pillar-based engines produce correct normalized output."""

    def test_breadth_score_passthrough(self):
        r = normalize_engine_output("breadth_participation", _breadth_payload())
        assert r["score"] == 72.5
        assert r["label"] == "Healthy Breadth"
        assert r["short_label"] == "Healthy"
        assert r["confidence"] == 85.0

    def test_breadth_pillar_scores_ordered_by_weight(self):
        r = normalize_engine_output("breadth_participation", _breadth_payload())
        pillars = r["pillar_scores"]
        assert len(pillars) == 5
        # Sorted descending by weight
        weights = [p["weight"] for p in pillars]
        assert weights == sorted(weights, reverse=True)
        # Check content
        names = {p["name"] for p in pillars}
        assert "participation_breadth" in names
        assert "trend_breadth" in names

    def test_breadth_detail_sections_has_universe(self):
        r = normalize_engine_output("breadth_participation", _breadth_payload())
        assert "universe" in r["detail_sections"]
        assert r["detail_sections"]["universe"]["name"] == "S&P 500"

    def test_breadth_supporting_metrics_extracted(self):
        r = normalize_engine_output("breadth_participation", _breadth_payload())
        metrics = r["supporting_metrics"]
        assert len(metrics) > 0
        # Each metric has required keys
        for m in metrics:
            assert "name" in m
            assert "pillar" in m

    def test_volatility_strategy_scores_in_detail(self):
        r = normalize_engine_output("volatility_options", _volatility_payload())
        assert "strategy_scores" in r["detail_sections"]
        assert "premium_selling" in r["detail_sections"]["strategy_scores"]

    def test_cross_asset_uses_confirming_contradicting(self):
        """Cross-asset maps confirming→bull, contradicting→bear, mixed→contradictions."""
        r = normalize_engine_output("cross_asset_macro", _cross_asset_payload())
        assert "Credit spreads tight" in r["bull_factors"]
        assert "Yield curve flat" in r["bear_factors"]
        assert "Dollar neutral" in r["contradiction_flags"]

    def test_cross_asset_signal_provenance_in_detail(self):
        r = normalize_engine_output("cross_asset_macro", _cross_asset_payload())
        assert "signal_provenance" in r["detail_sections"]

    def test_cross_asset_source_errors(self):
        r = normalize_engine_output("cross_asset_macro", _cross_asset_payload())
        assert "FRED_DGS10" in r["source_status"]["errors"]

    def test_cross_asset_proxy_direct_counts(self):
        r = normalize_engine_output("cross_asset_macro", _cross_asset_payload())
        assert r["source_status"]["proxy_count"] == 1
        assert r["source_status"]["direct_count"] == 1

    def test_flows_strategy_bias_in_detail(self):
        r = normalize_engine_output("flows_positioning", _flows_payload())
        assert "strategy_bias" in r["detail_sections"]
        assert r["detail_sections"]["strategy_bias"]["mean_reversion_edge"] == "low"

    def test_flows_all_proxy(self):
        r = normalize_engine_output("flows_positioning", _flows_payload())
        assert r["source_status"]["proxy_count"] == 3
        assert r["source_status"]["direct_count"] == 0

    def test_liquidity_support_vs_stress_in_detail(self):
        r = normalize_engine_output("liquidity_financial_conditions", _liquidity_payload())
        assert "support_vs_stress" in r["detail_sections"]
        assert r["detail_sections"]["support_vs_stress"]["net_assessment"] == "supportive"

    def test_liquidity_risks_include_missing_inputs(self):
        r = normalize_engine_output("liquidity_financial_conditions", _liquidity_payload())
        risk_text = " ".join(r["risks"])
        assert "nfci_index" in risk_text

    def test_cache_info_in_freshness(self):
        r = normalize_engine_output("cross_asset_macro", _cross_asset_payload())
        assert r["freshness"]["cache_hit"] is False
        assert r["freshness"]["compute_duration_s"] == 1.2


# ═══════════════════════════════════════════════════════════════════════
# 3. NEWS ENGINE NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════

class TestNewsEngine:
    """News engine uses different structure — verify mapping is correct."""

    def test_news_score_and_label(self):
        r = normalize_engine_output("news_sentiment", _news_payload())
        assert r["score"] == 58.0
        assert r["label"] == "Mixed"
        # News has no separate short_label — uses label
        assert r["short_label"] == "Mixed"

    def test_news_engine_metadata(self):
        r = normalize_engine_output("news_sentiment", _news_payload())
        assert r["engine_key"] == "news_sentiment"
        assert r["engine_name"] == "News & Sentiment"
        assert r["time_horizon"] == "intraday"

    def test_news_pillar_scores_from_components(self):
        r = normalize_engine_output("news_sentiment", _news_payload())
        pillars = r["pillar_scores"]
        assert len(pillars) == 6
        names = {p["name"] for p in pillars}
        assert "headline_sentiment" in names
        assert "negative_pressure" in names

    def test_news_bull_bear_from_components(self):
        r = normalize_engine_output("news_sentiment", _news_payload())
        # Components with score >= 60 → bull
        bull_names = [b.split(":")[0] for b in r["bull_factors"]]
        assert "headline_sentiment" in bull_names  # score 62
        assert "source_agreement" in bull_names    # score 70
        # Components with score < 40 → bear
        # negative_pressure has score 45, so not bear
        # No component is < 40 in our fixture

    def test_news_items_in_detail_sections(self):
        r = normalize_engine_output("news_sentiment", _news_payload())
        assert "items" in r["detail_sections"]
        assert len(r["detail_sections"]["items"]) == 2

    def test_news_macro_context_in_detail_sections(self):
        r = normalize_engine_output("news_sentiment", _news_payload())
        assert "macro_context" in r["detail_sections"]
        assert r["detail_sections"]["macro_context"]["vix"] == 16.5

    def test_news_source_freshness_in_detail_sections(self):
        r = normalize_engine_output("news_sentiment", _news_payload())
        assert "source_freshness" in r["detail_sections"]

    def test_news_source_status_errors(self):
        r = normalize_engine_output("news_sentiment", _news_payload())
        assert "polygon" in r["source_status"]["errors"]
        assert r["source_status"]["direct_count"] == 1  # finnhub OK
        assert r["source_status"]["proxy_count"] == 0

    def test_news_summary_from_explanation(self):
        r = normalize_engine_output("news_sentiment", _news_payload())
        assert "mixed" in r["summary"].lower()

    def test_news_trader_takeaway(self):
        r = normalize_engine_output("news_sentiment", _news_payload())
        assert r["trader_takeaway"] != ""


# ═══════════════════════════════════════════════════════════════════════
# 4. EDGE CASES
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Handle degraded, missing, or error states gracefully."""

    def test_empty_engine_result(self):
        """Payload has engine_result: {} — should not crash."""
        payload = {
            "engine_result": {},
            "data_quality": {},
            "compute_duration_s": 0,
            "as_of": "2026-03-15T14:00:00Z",
        }
        r = normalize_engine_output("breadth_participation", payload)
        assert r["score"] is None
        assert r["label"] == "Unknown"
        assert r["pillar_scores"] == []

    def test_none_engine_result(self):
        """Payload has engine_result: None — should not crash."""
        payload = {
            "engine_result": None,
            "data_quality": {},
            "compute_duration_s": 0,
            "as_of": "2026-03-15T14:00:00Z",
        }
        r = normalize_engine_output("breadth_participation", payload)
        assert r["score"] is None
        assert isinstance(r["bull_factors"], list)

    def test_news_none_internal_engine(self):
        """News payload with internal_engine=None (engine failed)."""
        payload = {
            "internal_engine": None,
            "items": [],
            "macro_context": {},
            "source_freshness": [],
            "as_of": "2026-03-15T14:00:00Z",
            "item_count": 0,
        }
        r = normalize_engine_output("news_sentiment", payload)
        assert r["score"] is None
        assert r["label"] == "Unknown"
        assert r["pillar_scores"] == []
        assert "No news items available" in r["warnings"]

    def test_unknown_engine_key(self):
        """Unknown engine key should still produce valid output."""
        payload = {
            "engine_result": {"score": 50.0, "label": "Test"},
            "data_quality": {},
            "compute_duration_s": 0,
            "as_of": "2026-03-15T14:00:00Z",
        }
        r = normalize_engine_output("unknown_engine", payload)
        assert r["engine_key"] == "unknown_engine"
        assert r["engine_name"] == "unknown_engine"  # Falls back to key

    def test_missing_data_quality(self):
        """No data_quality key in payload — should not crash."""
        payload = {
            "engine_result": {"score": 60.0, "label": "OK"},
            "compute_duration_s": 0.1,
            "as_of": "2026-03-15T14:00:00Z",
        }
        r = normalize_engine_output("breadth_participation", payload)
        assert r["data_quality"]["signal_quality"] == "low"


# ═══════════════════════════════════════════════════════════════════════
# 5. HELPER FUNCTION TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestHelpers:
    """Unit tests for private helper functions."""

    def test_derive_regime_tags_normal(self):
        tags = _derive_regime_tags("Premium Selling Strongly Favored")
        assert tags == ["premium_selling_strongly_favored"]

    def test_derive_regime_tags_empty(self):
        assert _derive_regime_tags("") == []
        assert _derive_regime_tags("Unknown") == []

    def test_derive_regime_tags_special_chars(self):
        tags = _derive_regime_tags("Risk-On / Bullish")
        assert len(tags) == 1
        assert "risk" in tags[0]

    def test_build_risks_dedup(self):
        risks = _build_risks(
            ["Warning A", "Warning B", "Warning A"],
            ["input_x", "input_y"],
        )
        assert len(risks) == 4  # A, B, Missing input: x, Missing input: y
        assert risks[0] == "Warning A"
        assert risks[1] == "Warning B"

    def test_extract_drivers_standard(self):
        er = {
            "positive_contributors": ["A", "B"],
            "negative_contributors": ["C"],
            "conflicting_signals": ["D"],
        }
        bull, bear, contra = _extract_drivers("breadth_participation", er)
        assert bull == ["A", "B"]
        assert bear == ["C"]
        assert contra == ["D"]

    def test_extract_drivers_cross_asset(self):
        er = {
            "confirming_signals": ["X"],
            "contradicting_signals": ["Y"],
            "mixed_signals": ["Z"],
        }
        bull, bear, contra = _extract_drivers("cross_asset_macro", er)
        assert bull == ["X"]
        assert bear == ["Y"]
        assert contra == ["Z"]

    def test_extract_pillar_scores_empty(self):
        assert _extract_pillar_scores({}) == []

    def test_extract_pillar_scores_ordered(self):
        er = {
            "pillar_scores": {"a": 80, "b": 60},
            "pillar_weights": {"a": 0.3, "b": 0.7},
            "pillar_explanations": {"a": "Atext", "b": "Btext"},
        }
        pillars = _extract_pillar_scores(er)
        assert pillars[0]["name"] == "b"  # Higher weight first
        assert pillars[0]["weight"] == 0.7
        assert pillars[1]["name"] == "a"

    def test_build_freshness_with_cache(self):
        f = _build_freshness(
            {"compute_duration_s": 0.5},
            {"cache_hit": True},
        )
        assert f["compute_duration_s"] == 0.5
        assert f["cache_hit"] is True

    def test_build_freshness_no_cache(self):
        f = _build_freshness({"compute_duration_s": 0.3}, None)
        assert f["cache_hit"] is None

    def test_extract_detail_sections_volatility(self):
        er = {"strategy_scores": {"premium_selling": {"score": 75}}}
        sections = _extract_detail_sections("volatility_options", er)
        assert "strategy_scores" in sections

    def test_extract_detail_sections_unknown(self):
        sections = _extract_detail_sections("unknown", {})
        assert sections == {}

    def test_engine_metadata_coverage(self):
        """All 6 engines have metadata entries."""
        expected = {
            "breadth_participation",
            "volatility_options",
            "cross_asset_macro",
            "flows_positioning",
            "liquidity_financial_conditions",
            "news_sentiment",
        }
        assert set(ENGINE_METADATA.keys()) == expected
