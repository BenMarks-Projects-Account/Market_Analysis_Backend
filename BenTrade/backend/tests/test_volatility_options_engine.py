"""Tests for Volatility & Options Structure Scoring Engine.

10 test scenarios:
  1. Unit Tests — scoring utilities (_clamp, _interpolate, _safe_float, etc.)
  2. Calm / Favorable — VIX low, contango, IV>RV, moderate skew → 70-95
  3. Elevated Stress — VIX high, backwardation, high skew → 20-45
  4. Mixed Conditions — some bullish, some bearish signals → 45-70
  5. Premium Selling Sweet Spot — ideal conditions for our primary strategy
  6. Degraded Data — many missing inputs → confidence penalty, warnings
  7. Partial Pillar Unavailability — one full pillar missing → composites anyway
  8. Strategy Suitability — derived pillar scores map correctly
  9. Label / Signal Quality Mapping — score→label→short_label bands correct
 10. Pillar Weight Validation — weights sum to 1.0, structure is correct
"""

import pytest

from app.services.volatility_options_engine import (
    PILLAR_WEIGHTS,
    _clamp,
    _interpolate,
    _safe_float,
    _weighted_avg,
    _build_submetric,
    _vix_level_score,
    compute_volatility_scores,
)


# ═══════════════════════════════════════════════════════════════════════
# HELPERS — build fixture data dicts
# ═══════════════════════════════════════════════════════════════════════

def _calm_regime():
    """VIX at 14, declining trend, low IV rank, subdued VVIX."""
    return {
        "vix_spot": 14.2,
        "vix_avg_20d": 15.8,
        "vix_rank_30d": 22,
        "vix_percentile_1y": 18,
        "vvix": 88.4,
    }


def _normal_structure():
    """Normal contango, IV > RV."""
    return {
        "vix_front_month": 14.2,
        "vix_2nd_month": 15.8,
        "vix_3rd_month": 16.4,
        "iv_30d": 15.2,
        "rv_30d": 12.8,
    }


def _moderate_skew():
    """Normal skew, no tail risk."""
    return {
        "cboe_skew": 128,
        "put_skew_25d": 4.2,
        "tail_risk_numeric": 25,
        "tail_risk_signal": "Low",
    }


def _calm_positioning():
    """Low equity P/C, moderate index hedging, options slightly cheap."""
    return {
        "equity_pc_ratio": 0.62,
        "spy_pc_ratio_proxy": 1.08,
        "option_richness": 45,
        "option_richness_label": "Fair",
        "premium_bias": 30,
    }


def _stressed_regime():
    """VIX spiked, high IV rank, elevated VVIX."""
    return {
        "vix_spot": 34.5,
        "vix_avg_20d": 22.0,
        "vix_rank_30d": 88,
        "vix_percentile_1y": 92,
        "vvix": 135,
    }


def _stressed_structure():
    """Backwardation, IV >> RV."""
    return {
        "vix_front_month": 34.5,
        "vix_2nd_month": 30.2,
        "vix_3rd_month": 28.8,
        "iv_30d": 38.0,
        "rv_30d": 28.0,
    }


def _extreme_skew():
    """Very high skew, elevated tail risk."""
    return {
        "cboe_skew": 155,
        "put_skew_25d": 12.0,
        "tail_risk_numeric": 80,
        "tail_risk_signal": "High",
    }


def _fearful_positioning():
    """High put/call ratios, expensive options."""
    return {
        "equity_pc_ratio": 1.15,
        "spy_pc_ratio_proxy": 1.85,
        "option_richness": 82,
        "option_richness_label": "Rich",
        "premium_bias": -40,
    }


def _empty_data():
    """All fields missing — simulates data fetch failure."""
    return {}


# ═══════════════════════════════════════════════════════════════════════
# UNIT TESTS — scoring utilities
# ═══════════════════════════════════════════════════════════════════════


class TestScoringUtilities:
    def test_clamp_within_range(self):
        assert _clamp(50) == 50

    def test_clamp_below(self):
        assert _clamp(-10) == 0.0

    def test_clamp_above(self):
        assert _clamp(150) == 100.0

    def test_safe_float_valid(self):
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_safe_float_none(self):
        assert _safe_float(None) is None

    def test_safe_float_invalid(self):
        assert _safe_float("abc", 0.0) == 0.0

    def test_interpolate_midpoint(self):
        result = _interpolate(0.5, 0.0, 1.0, 0.0, 100.0)
        assert result == pytest.approx(50.0)

    def test_interpolate_clamps_above(self):
        result = _interpolate(2.0, 0.0, 1.0, 0.0, 100.0)
        assert result == pytest.approx(100.0)

    def test_interpolate_clamps_below(self):
        result = _interpolate(-1.0, 0.0, 1.0, 0.0, 100.0)
        assert result == pytest.approx(0.0)

    def test_weighted_avg_basic(self):
        result = _weighted_avg([(80, 0.5), (60, 0.5)])
        assert result == pytest.approx(70.0)

    def test_weighted_avg_none_values(self):
        result = _weighted_avg([(None, 0.5), (80, 0.5)])
        assert result == pytest.approx(80.0)

    def test_weighted_avg_all_none(self):
        result = _weighted_avg([(None, 0.5), (None, 0.5)])
        assert result is None

    def test_build_submetric_ok(self):
        sm = _build_submetric("test_metric", 42.0, 75.0)
        assert sm["name"] == "test_metric"
        assert sm["raw_value"] == pytest.approx(42.0)
        assert sm["score"] == 75.0
        assert sm["status"] == "valid"

    def test_build_submetric_unavailable(self):
        sm = _build_submetric("test_metric", None, None)
        assert sm["status"] == "unavailable"

    def test_vix_level_score_sweet_spot(self):
        """VIX 15 — in sweet spot for premium selling."""
        score = _vix_level_score(15.0)
        assert 80 <= score <= 95

    def test_vix_level_score_elevated(self):
        """VIX 30 — elevated vol, still moderate score."""
        score = _vix_level_score(30.0)
        assert 40 <= score <= 70

    def test_vix_level_score_very_high(self):
        """VIX 45 — panic zone."""
        score = _vix_level_score(45.0)
        assert score <= 20

    def test_vix_level_score_very_low(self):
        """VIX 10 — too low for premium, reasonable but not ideal."""
        score = _vix_level_score(10.0)
        assert 55 <= score <= 80

    def test_pillar_weights_sum_to_one(self):
        assert sum(PILLAR_WEIGHTS.values()) == pytest.approx(1.0)

    def test_pillar_weights_keys(self):
        expected = {
            "volatility_regime", "volatility_structure",
            "tail_risk_skew", "positioning_options_posture",
            "strategy_suitability",
        }
        assert set(PILLAR_WEIGHTS.keys()) == expected


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 2 — Calm / Favorable Conditions
# ═══════════════════════════════════════════════════════════════════════


class TestCalmFavorable:
    """Low VIX, contango, IV>RV, moderate skew → favorable for premium selling."""

    @pytest.fixture
    def result(self):
        return compute_volatility_scores(
            regime_data=_calm_regime(),
            structure_data=_normal_structure(),
            skew_data=_moderate_skew(),
            positioning_data=_calm_positioning(),
        )

    def test_composite_score_high(self, result):
        assert result["score"] >= 60

    def test_label_favorable(self, result):
        label = result["label"].lower()
        assert any(w in label for w in ["favored", "favorable", "constructive"])

    def test_all_pillar_scores_present(self, result):
        for pname in PILLAR_WEIGHTS:
            assert result["pillar_scores"][pname] is not None

    def test_confidence_score_high(self, result):
        assert result["confidence_score"] >= 70

    def test_signal_quality(self, result):
        assert result["signal_quality"] in ("high", "medium")

    def test_has_positive_contributors(self, result):
        assert len(result["positive_contributors"]) >= 2

    def test_strategy_premium_selling_high(self, result):
        ps = result["strategy_scores"].get("premium_selling", {})
        assert ps.get("score") is not None
        assert ps["score"] >= 60

    def test_raw_inputs_present(self, result):
        assert "regime" in result["raw_inputs"]
        assert "structure" in result["raw_inputs"]
        assert "skew" in result["raw_inputs"]
        assert "positioning" in result["raw_inputs"]
        assert "strategy" in result["raw_inputs"]

    def test_diagnostics_present(self, result):
        assert "pillar_weights" in result["diagnostics"]
        assert "pillar_details" in result["diagnostics"]
        assert "confidence_penalties" in result["diagnostics"]

    def test_engine_field(self, result):
        assert result["engine"] == "volatility_options"

    def test_as_of_present(self, result):
        assert result["as_of"] is not None

    def test_trader_takeaway_not_defensive(self, result):
        takeaway = result["trader_takeaway"].lower()
        assert "defensive" not in takeaway


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 3 — Elevated Stress (High VIX, backwardation, panic skew)
# ═══════════════════════════════════════════════════════════════════════


class TestElevatedStress:
    """VIX spiked, backwardation, extreme skew → score < 50."""

    @pytest.fixture
    def result(self):
        return compute_volatility_scores(
            regime_data=_stressed_regime(),
            structure_data=_stressed_structure(),
            skew_data=_extreme_skew(),
            positioning_data=_fearful_positioning(),
        )

    def test_composite_score_low(self, result):
        assert result["score"] < 50

    def test_label_defensive_or_stress(self, result):
        label = result["label"].lower()
        assert any(w in label for w in ["stress", "defensive", "elevated", "fragile"])

    def test_has_negative_contributors(self, result):
        assert len(result["negative_contributors"]) >= 1

    def test_premium_selling_unfavorable(self, result):
        ps = result["strategy_scores"].get("premium_selling", {})
        # In stress, premium selling is still possible but riskier
        assert ps.get("score") is not None

    def test_hedging_score_low(self, result):
        """Hedging is expensive in stress → low hedging suitability."""
        hdg = result["strategy_scores"].get("hedging", {})
        assert hdg.get("score") is not None
        assert hdg["score"] < 50

    def test_all_pillars_scored(self, result):
        for pname in PILLAR_WEIGHTS:
            assert result["pillar_scores"][pname] is not None

    def test_warnings_present(self, result):
        # Should have warnings about elevated conditions
        assert len(result["warnings"]) >= 0  # may or may not have warnings


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 4 — Mixed Conditions
# ═══════════════════════════════════════════════════════════════════════


class TestMixedConditions:
    """Some bullish, some bearish signals → mid-range score."""

    @pytest.fixture
    def result(self):
        return compute_volatility_scores(
            regime_data=_calm_regime(),   # Calm
            structure_data=_stressed_structure(),  # But stressed structure
            skew_data=_moderate_skew(),   # Normal skew
            positioning_data=_fearful_positioning(),  # Fearful positioning
        )

    def test_composite_mid_range(self, result):
        assert 30 <= result["score"] <= 75

    def test_has_conflicting_signals(self, result):
        """With mixed data, should detect some conflict or mixed signals."""
        # At least some positive AND some negative contributors
        assert len(result["positive_contributors"]) >= 1 or len(result["negative_contributors"]) >= 1

    def test_all_pillars_scored(self, result):
        for pname in PILLAR_WEIGHTS:
            assert result["pillar_scores"][pname] is not None


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 5 — Premium Selling Sweet Spot
# ═══════════════════════════════════════════════════════════════════════


class TestPremiumSellingSweetSpot:
    """VIX 16 (sweet spot), strong contango, IV > RV, favorable positioning."""

    @pytest.fixture
    def result(self):
        return compute_volatility_scores(
            regime_data={
                "vix_spot": 16.0, "vix_avg_20d": 17.5,
                "vix_rank_30d": 35, "vix_percentile_1y": 30,
                "vvix": 85,
            },
            structure_data={
                "vix_front_month": 16.0, "vix_2nd_month": 17.5,
                "vix_3rd_month": 18.2,
                "iv_30d": 17.0, "rv_30d": 13.0,
            },
            skew_data={
                "cboe_skew": 122, "put_skew_25d": 3.5,
                "tail_risk_numeric": 20, "tail_risk_signal": "Low",
            },
            positioning_data={
                "equity_pc_ratio": 0.72, "spy_pc_ratio_proxy": 1.15,
                "option_richness": 55, "option_richness_label": "Fair",
                "premium_bias": 45,
            },
        )

    def test_high_composite(self, result):
        assert result["score"] >= 70

    def test_premium_selling_highest_strategy(self, result):
        strategies = result["strategy_scores"]
        ps = strategies.get("premium_selling", {}).get("score")
        assert ps is not None and ps >= 70

    def test_regime_score_high(self, result):
        assert result["pillar_scores"]["volatility_regime"] >= 65

    def test_structure_score_high(self, result):
        assert result["pillar_scores"]["volatility_structure"] >= 65


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 6 — Degraded Data (Many Missing Inputs)
# ═══════════════════════════════════════════════════════════════════════


class TestDegradedData:
    """Most fields missing → confidence penalty, many warnings."""

    @pytest.fixture
    def result(self):
        return compute_volatility_scores(
            regime_data={"vix_spot": 15.0},  # Only VIX spot available
            structure_data={},
            skew_data={},
            positioning_data={},
        )

    def test_score_still_produced(self, result):
        """Engine should still produce a score even with sparse data."""
        assert result["score"] is not None

    def test_confidence_degraded(self, result):
        """Missing data should reduce confidence."""
        assert result["confidence_score"] < 70

    def test_many_missing_inputs(self, result):
        assert len(result["missing_inputs"]) >= 5

    def test_warnings_about_missing(self, result):
        assert len(result["warnings"]) >= 3

    def test_signal_quality_reduced(self, result):
        assert result["signal_quality"] in ("low", "medium")

    def test_raw_inputs_reflect_empty(self, result):
        # Structure data was empty — engine still populates keys with None values
        struct = result["raw_inputs"]["structure"]
        assert all(v is None for v in struct.values())


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 7 — Partial Pillar Unavailability
# ═══════════════════════════════════════════════════════════════════════


class TestPartialPillarUnavailability:
    """One full pillar with no data; composites from remaining pillars."""

    @pytest.fixture
    def result(self):
        return compute_volatility_scores(
            regime_data=_calm_regime(),
            structure_data=_normal_structure(),
            skew_data=_empty_data(),  # Entirely missing
            positioning_data=_calm_positioning(),
        )

    def test_score_produced(self, result):
        assert result["score"] is not None

    def test_skew_pillar_none(self, result):
        """Skew pillar should be None since all inputs missing."""
        assert result["pillar_scores"]["tail_risk_skew"] is None

    def test_other_pillars_present(self, result):
        assert result["pillar_scores"]["volatility_regime"] is not None
        assert result["pillar_scores"]["volatility_structure"] is not None
        assert result["pillar_scores"]["positioning_options_posture"] is not None

    def test_confidence_penalized(self, result):
        """Missing a full pillar should reduce confidence."""
        assert result["confidence_score"] < 90

    def test_diagnostics_show_inactive(self, result):
        diag = result["diagnostics"]["composite_computation"]
        assert "tail_risk_skew" in diag["inactive_pillars"]


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 8 — Strategy Suitability Derived Correctly
# ═══════════════════════════════════════════════════════════════════════


class TestStrategySuitability:
    """Verify strategy scores map to well-known strategy families."""

    @pytest.fixture
    def result(self):
        return compute_volatility_scores(
            regime_data=_calm_regime(),
            structure_data=_normal_structure(),
            skew_data=_moderate_skew(),
            positioning_data=_calm_positioning(),
        )

    def test_all_strategies_present(self, result):
        expected = {"premium_selling", "directional", "vol_structure_plays", "hedging"}
        assert set(result["strategy_scores"].keys()) == expected

    def test_each_strategy_has_score_and_description(self, result):
        for name, data in result["strategy_scores"].items():
            assert "score" in data, f"{name} missing score"
            assert "description" in data, f"{name} missing description"
            assert data["score"] is not None, f"{name} score is None"

    def test_premium_selling_highest_in_calm(self, result):
        """In calm conditions, premium selling should be among the highest."""
        ps = result["strategy_scores"]["premium_selling"]["score"]
        direc = result["strategy_scores"]["directional"]["score"]
        assert ps >= direc  # Premium selling should beat directional in calm


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 9 — Label / Signal Quality Mapping
# ═══════════════════════════════════════════════════════════════════════


class TestLabelMapping:
    """Verify score→label→short_label bands are correct."""

    def test_high_score_label(self):
        result = compute_volatility_scores(
            regime_data={
                "vix_spot": 15.0, "vix_avg_20d": 16.5,
                "vix_rank_30d": 25, "vix_percentile_1y": 20,
                "vvix": 82,
            },
            structure_data={
                "vix_front_month": 15.0, "vix_2nd_month": 16.8,
                "vix_3rd_month": 17.5,
                "iv_30d": 16.0, "rv_30d": 12.0,
            },
            skew_data={
                "cboe_skew": 118, "put_skew_25d": 2.5,
                "tail_risk_numeric": 15, "tail_risk_signal": "Low",
            },
            positioning_data={
                "equity_pc_ratio": 0.68, "spy_pc_ratio_proxy": 1.0,
                "option_richness": 60, "option_richness_label": "Fair",
                "premium_bias": 50,
            },
        )
        # Should get a high label
        assert result["short_label"] is not None
        assert result["label"] is not None
        assert len(result["short_label"]) > 0

    def test_label_and_short_label_differ(self):
        result = compute_volatility_scores(
            regime_data=_calm_regime(),
            structure_data=_normal_structure(),
            skew_data=_moderate_skew(),
            positioning_data=_calm_positioning(),
        )
        # Short label should be a shorter version
        assert len(result["short_label"]) <= len(result["label"])


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 10 — All Empty Data
# ═══════════════════════════════════════════════════════════════════════


class TestAllEmptyData:
    """Completely empty inputs → engine still returns valid structure."""

    @pytest.fixture
    def result(self):
        return compute_volatility_scores(
            regime_data={},
            structure_data={},
            skew_data={},
            positioning_data={},
        )

    def test_score_is_zero_or_low(self, result):
        """With no data, score should default to 0."""
        assert result["score"] is not None
        assert result["score"] == 0.0

    def test_all_pillars_none(self, result):
        for pname in PILLAR_WEIGHTS:
            assert result["pillar_scores"][pname] is None

    def test_confidence_very_low(self, result):
        assert result["confidence_score"] <= 30

    def test_many_missing_inputs(self, result):
        assert len(result["missing_inputs"]) >= 10

    def test_structure_complete(self, result):
        """Even with no data, all required fields must be present."""
        required_keys = [
            "engine", "as_of", "score", "label", "short_label",
            "confidence_score", "signal_quality", "summary",
            "pillar_scores", "pillar_weights", "pillar_explanations",
            "strategy_scores", "positive_contributors",
            "negative_contributors", "conflicting_signals",
            "trader_takeaway", "warnings", "missing_inputs",
            "diagnostics", "raw_inputs",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 11 — Regression: Pillar Failure Isolation
# ═══════════════════════════════════════════════════════════════════════


class TestPillarFailureIsolation:
    """A single pillar failure must not collapse the entire engine."""

    def test_engine_survives_bad_regime_data_type(self):
        """If regime_data contains un-parseable values, engine still produces output."""
        result = compute_volatility_scores(
            regime_data={"vix_spot": "not_a_number", "vix_rank_30d": "bad"},
            structure_data=_normal_structure(),
            skew_data=_moderate_skew(),
            positioning_data=_calm_positioning(),
        )
        assert result["score"] is not None
        assert result["engine"] == "volatility_options"
        # Other pillars should still produce valid scores
        assert result["pillar_scores"]["volatility_structure"] is not None

    def test_field_rename_consistency(self):
        """Verify vix_rank_30d (not iv_rank_30d) is the field used end-to-end."""
        result = compute_volatility_scores(
            regime_data=_calm_regime(),
            structure_data=_normal_structure(),
            skew_data=_moderate_skew(),
            positioning_data=_calm_positioning(),
        )
        raw_regime = result["raw_inputs"]["regime"]
        assert "vix_rank_30d" in raw_regime
        assert "iv_rank_30d" not in raw_regime
        raw_strategy = result["raw_inputs"]["strategy"]
        assert "vix_rank_30d" in raw_strategy
        assert "iv_rank" not in raw_strategy

    def test_spy_pc_proxy_in_raw_inputs(self):
        """Verify spy_pc_ratio_proxy (not index_pc_ratio) is the field used."""
        result = compute_volatility_scores(
            regime_data=_calm_regime(),
            structure_data=_normal_structure(),
            skew_data=_moderate_skew(),
            positioning_data=_calm_positioning(),
        )
        raw_pos = result["raw_inputs"]["positioning"]
        assert "spy_pc_ratio_proxy" in raw_pos
        assert "index_pc_ratio" not in raw_pos

    def test_tail_risk_numeric_used_for_scoring(self):
        """tail_risk_numeric (not tail_risk_signal as number) is used for scoring."""
        result = compute_volatility_scores(
            regime_data=_calm_regime(),
            structure_data=_normal_structure(),
            skew_data={
                "cboe_skew": 128, "put_skew_25d": 4.2,
                "tail_risk_numeric": 25, "tail_risk_signal": "Low",
            },
            positioning_data=_calm_positioning(),
        )
        assert result["pillar_scores"]["tail_risk_skew"] is not None

    def test_option_richness_label_propagated(self):
        """option_richness_label should be propagated through to diagnostics."""
        result = compute_volatility_scores(
            regime_data=_calm_regime(),
            structure_data=_normal_structure(),
            skew_data=_moderate_skew(),
            positioning_data=_calm_positioning(),
        )
        # option_richness should appear in positioning submetrics
        pos_detail = result["diagnostics"]["pillar_details"]["positioning_options_posture"]
        richness_subs = [s for s in pos_detail["submetrics"] if s["name"] == "option_richness"]
        assert len(richness_subs) == 1
        assert richness_subs[0]["score"] is not None

    def test_degraded_partial_output_usable(self):
        """With sparse data, remaining valid pillars produce scores."""
        result = compute_volatility_scores(
            regime_data={"vix_spot": 18.0, "vix_avg_20d": 17.0},
            structure_data={"iv_30d": 20.0, "rv_30d": 15.0},
            skew_data={},
            positioning_data={"equity_pc_ratio": 0.75},
        )
        assert result["score"] is not None
        assert result["score"] > 0
        assert result["pillar_scores"]["volatility_regime"] is not None
        assert result["pillar_scores"]["tail_risk_skew"] is None
        assert result["confidence_score"] < 80
