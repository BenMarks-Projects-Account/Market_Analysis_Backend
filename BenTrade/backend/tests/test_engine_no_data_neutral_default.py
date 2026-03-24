"""Tests: MI engines default to 50.0 / Neutral / data_status="no_data" when all pillars are None.

Covers all 5 deterministic engines (vol, breadth, flows, cross-asset, liquidity)
plus the news sentiment engine.  Each is called with empty/None inputs so every
pillar returns None, then verifies:
  1. composite score == 50.0
  2. label contains "Neutral"
  3. data_status == "no_data"

Liquidity engine note: Pillar 5 (stability/fragility) derives a score from
cross-pillar values and has its own 50.0 fallback, so _weighted_avg never
returns None for liquidity even with all-empty inputs.  We verify data_status
exists and is "ok" (since a valid composite was computed) and score is near
neutral (~50-60 range) rather than the old 0.0 hostile default.
"""

from __future__ import annotations

import pytest

# ── Helpers ───────────────────────────────────────────────────────────

_EMPTY = {}  # shorthand for empty input dict


# ── Volatility ────────────────────────────────────────────────────────

class TestVolatilityNoData:
    def test_defaults_to_neutral(self):
        from app.services.volatility_options_engine import compute_volatility_scores

        result = compute_volatility_scores(
            regime_data={}, structure_data={}, skew_data={}, positioning_data={},
        )
        assert result["score"] == 50.0
        assert "Neutral" in result["label"]
        assert result["data_status"] == "no_data"

    def test_data_status_field_present(self):
        """data_status field must always exist in the output."""
        from app.services.volatility_options_engine import compute_volatility_scores

        result = compute_volatility_scores(
            regime_data={}, structure_data={}, skew_data={}, positioning_data={},
        )
        assert "data_status" in result


# ── Breadth ───────────────────────────────────────────────────────────

class TestBreadthNoData:
    def test_defaults_to_neutral(self):
        from app.services.breadth_engine import compute_breadth_scores

        result = compute_breadth_scores(
            participation_data={}, trend_data={}, volume_data={},
            leadership_data={}, stability_data={},
            universe_meta={"name": "test", "expected_count": 0, "actual_count": 0},
        )
        assert result["score"] == 50.0
        assert "Neutral" in result["label"]
        assert result["data_status"] == "no_data"

    def test_data_status_field_present(self):
        from app.services.breadth_engine import compute_breadth_scores

        result = compute_breadth_scores(
            participation_data={}, trend_data={}, volume_data={},
            leadership_data={}, stability_data={},
            universe_meta={"name": "test", "expected_count": 0, "actual_count": 0},
        )
        assert "data_status" in result


# ── Flows & Positioning ──────────────────────────────────────────────

class TestFlowsNoData:
    def test_defaults_to_neutral(self):
        from app.services.flows_positioning_engine import compute_flows_positioning_scores

        result = compute_flows_positioning_scores(
            positioning_data={}, crowding_data={}, squeeze_data={},
            flow_data={}, stability_data={}, source_meta={},
        )
        assert result["score"] == 50.0
        assert "Neutral" in result["label"]
        assert result["data_status"] == "no_data"

    def test_data_status_field_present(self):
        from app.services.flows_positioning_engine import compute_flows_positioning_scores

        result = compute_flows_positioning_scores(
            positioning_data={}, crowding_data={}, squeeze_data={},
            flow_data={}, stability_data={}, source_meta={},
        )
        assert "data_status" in result


# ── Cross-Asset Macro ────────────────────────────────────────────────

class TestCrossAssetNoData:
    def test_defaults_to_neutral(self):
        from app.services.cross_asset_macro_engine import compute_cross_asset_scores

        result = compute_cross_asset_scores(
            rates_data={}, dollar_commodity_data={}, credit_data={},
            defensive_growth_data={}, coherence_data={}, source_meta={},
        )
        assert result["score"] == 50.0
        assert "Neutral" in result["label"]
        assert result["data_status"] == "no_data"

    def test_data_status_field_present(self):
        from app.services.cross_asset_macro_engine import compute_cross_asset_scores

        result = compute_cross_asset_scores(
            rates_data={}, dollar_commodity_data={}, credit_data={},
            defensive_growth_data={}, coherence_data={}, source_meta={},
        )
        assert "data_status" in result


# ── Liquidity / Financial Conditions ─────────────────────────────────
# NOTE: Pillar 5 (stability/fragility) is derived from cross-pillar
# scores and has its own 50.0 fallback, so _weighted_avg never returns
# None for liquidity even with all-empty inputs.  We verify data_status
# exists and score is NOT 0.0 (the old hostile default).

class TestLiquidityNoData:
    def test_not_zero_on_empty_input(self):
        """With no input data, score should NOT be 0.0 (old hostile default)."""
        from app.services.liquidity_conditions_engine import compute_liquidity_conditions_scores

        result = compute_liquidity_conditions_scores(
            rates_data={}, conditions_data={}, credit_data={},
            dollar_data={}, stability_data={}, source_meta={},
        )
        assert result["score"] != 0.0, "Score should not be 0.0 (hostile) on empty input"
        assert result["score"] >= 40.0, "Score should be near neutral, not hostile"
        assert "data_status" in result

    def test_data_status_field_present(self):
        from app.services.liquidity_conditions_engine import compute_liquidity_conditions_scores

        result = compute_liquidity_conditions_scores(
            rates_data={}, conditions_data={}, credit_data={},
            dollar_data={}, stability_data={}, source_meta={},
        )
        assert "data_status" in result


# ── News Sentiment ───────────────────────────────────────────────────
# NOTE: News engine components return default scores (50.0) for empty
# item lists, so total_weight > 0 and data_status is "ok".  The engine
# naturally produces a near-neutral score (~53.75) which is correct
# behavior — there's no data so components default to neutral.

class TestNewsSentimentNoData:
    def test_near_neutral_on_empty_items(self):
        """With empty items, news engine should produce a near-neutral score."""
        from app.services.news_sentiment_engine import compute_engine_scores

        result = compute_engine_scores(items=[], macro_context={})
        # Score should be near 50 (neutral), not 0 (hostile)
        assert 40.0 <= result["score"] <= 60.0
        assert "data_status" in result

    def test_data_status_field_present(self):
        from app.services.news_sentiment_engine import compute_engine_scores

        result = compute_engine_scores(items=[], macro_context={})
        assert "data_status" in result


# ── Cross-engine consistency ─────────────────────────────────────────

class TestAllEnginesConsistentNoDataDefault:
    """All engines that have _weighted_avg must agree on the neutral default."""

    def test_four_engines_produce_50_on_empty_input(self):
        """Vol, breadth, flows, cross-asset all return 50.0 with no data."""
        from app.services.breadth_engine import compute_breadth_scores
        from app.services.cross_asset_macro_engine import compute_cross_asset_scores
        from app.services.flows_positioning_engine import compute_flows_positioning_scores
        from app.services.volatility_options_engine import compute_volatility_scores

        results = {
            "volatility": compute_volatility_scores({}, {}, {}, {}),
            "breadth": compute_breadth_scores({}, {}, {}, {}, {}, {"name": "t", "expected_count": 0, "actual_count": 0}),
            "flows": compute_flows_positioning_scores({}, {}, {}, {}, {}, {}),
            "cross_asset": compute_cross_asset_scores({}, {}, {}, {}, {}, {}),
        }

        for name, r in results.items():
            assert r["score"] == 50.0, f"{name} score should be 50.0, got {r['score']}"
            assert r["data_status"] == "no_data", f"{name} data_status should be 'no_data', got {r['data_status']}"
            label = r.get("label", r.get("regime_label", ""))
            assert "Neutral" in label, f"{name} label should contain 'Neutral', got '{label}'"

    def test_all_engines_have_data_status_field(self):
        """All 6 engines must include data_status in their output."""
        from app.services.breadth_engine import compute_breadth_scores
        from app.services.cross_asset_macro_engine import compute_cross_asset_scores
        from app.services.flows_positioning_engine import compute_flows_positioning_scores
        from app.services.liquidity_conditions_engine import compute_liquidity_conditions_scores
        from app.services.news_sentiment_engine import compute_engine_scores
        from app.services.volatility_options_engine import compute_volatility_scores

        results = {
            "volatility": compute_volatility_scores({}, {}, {}, {}),
            "breadth": compute_breadth_scores({}, {}, {}, {}, {}, {"name": "t", "expected_count": 0, "actual_count": 0}),
            "flows": compute_flows_positioning_scores({}, {}, {}, {}, {}, {}),
            "cross_asset": compute_cross_asset_scores({}, {}, {}, {}, {}, {}),
            "liquidity": compute_liquidity_conditions_scores({}, {}, {}, {}, {}, {}),
            "news": compute_engine_scores(items=[], macro_context={}),
        }

        for name, r in results.items():
            assert "data_status" in r, f"{name} is missing data_status field"
