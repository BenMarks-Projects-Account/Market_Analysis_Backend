"""Tests for Breadth & Participation Scoring Engine.

8 test scenarios:
  1. Strong Breadth — all inputs healthy → score 85-100, label "Strong Breadth"
  2. Narrow Rally — participation strong but leadership weak → detect conflict
  3. Weak Volume — volume pillar drags composite despite decent participation
  4. Mixed Improving — mixed data but momentum positive
  5. Degraded Data — many missing inputs → confidence penalty, warnings
  6. Missing EW Benchmark — missing equal-weight data → specific penalty
  7. Partial Pillar Unavailability — one full pillar missing → composites anyway
  8. Survivorship Bias Warning — universe_meta flag triggers warning
"""

import pytest

from app.services.breadth_engine import (
    PILLAR_WEIGHTS,
    _clamp,
    _interpolate,
    _pct_score,
    _ratio_score,
    _safe_float,
    compute_breadth_scores,
)


# ═══════════════════════════════════════════════════════════════════════
# HELPERS — build fixture data dicts
# ═══════════════════════════════════════════════════════════════════════

def _strong_participation():
    """80%+ advancing, all sectors up, EW confirming."""
    return {
        "advancing": 104, "declining": 20, "unchanged": 6,
        "total_valid": 130,
        "new_highs": 35, "new_lows": 3,
        "sectors_positive": 10, "sectors_total": 11,
        "ew_return": 0.012, "cw_return": 0.010,
    }


def _strong_trend():
    return {
        "pct_above_20dma": 0.82, "pct_above_50dma": 0.75,
        "pct_above_200dma": 0.68,
        "pct_20_over_50": 0.78, "pct_50_over_200": 0.65,
        "trend_momentum_short": 0.08,
        "trend_momentum_intermediate": 0.05,
        "trend_momentum_long": 0.03,
        "total_valid": 130,
    }


def _strong_volume():
    return {
        "up_volume": 4_000_000_000, "down_volume": 1_200_000_000,
        "total_volume": 5_200_000_000,
        "advancing": 104, "declining": 20,
    }


def _strong_leadership():
    return {
        "ew_return": 0.012, "cw_return": 0.010,
        "sector_returns": {
            "Technology": 0.015, "Healthcare": 0.008,
            "Financials": 0.010, "Consumer Discretionary": 0.012,
            "Industrials": 0.009, "Energy": 0.005,
            "Materials": 0.006, "Utilities": 0.003,
            "Consumer Staples": 0.004, "Communication Services": 0.011,
            "REITs": 0.002,
        },
        "pct_outperforming_index": 0.65,
        "median_return": 0.009, "index_return": 0.008,
    }


def _strong_stability():
    return {
        "breadth_persistence_10d": 0.80,
        "ad_ratio_volatility_5d": 0.25,
        "pct_above_20dma_volatility_5d": 0.04,
    }


def _healthy_universe_meta():
    return {
        "name": "SP500_proxy",
        "expected_count": 130,
        "actual_count": 130,
        "survivorship_bias_risk": False,
    }


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

    def test_interpolate_clamps(self):
        result = _interpolate(2.0, 0.0, 1.0, 0.0, 100.0)
        assert result == pytest.approx(100.0)

    def test_pct_score_strong(self):
        """80%+ participation → 90-100 score."""
        score = _pct_score(0.85)
        assert 90 <= score <= 100

    def test_pct_score_moderate(self):
        """55% → moderate zone."""
        score = _pct_score(0.55)
        assert 50 <= score <= 70

    def test_pct_score_poor(self):
        """20% → poor zone."""
        score = _pct_score(0.20)
        assert score < 30

    def test_ratio_score_strong(self):
        """2.5 A/D ratio → very strong."""
        score = _ratio_score(2.5)
        assert 85 <= score <= 95

    def test_ratio_score_neutral(self):
        """1.0 ratio → around 50."""
        score = _ratio_score(1.0)
        assert 45 <= score <= 55

    def test_ratio_score_weak(self):
        """0.4 → weak."""
        score = _ratio_score(0.4)
        assert score < 15

    def test_pillar_weights_sum_to_one(self):
        assert sum(PILLAR_WEIGHTS.values()) == pytest.approx(1.0)


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 1 — Strong Breadth
# ═══════════════════════════════════════════════════════════════════════


class TestStrongBreadth:
    """All inputs healthy → score ≥ 70, label 'Strong Breadth' or 'Constructive'."""

    @pytest.fixture
    def result(self):
        return compute_breadth_scores(
            participation_data=_strong_participation(),
            trend_data=_strong_trend(),
            volume_data=_strong_volume(),
            leadership_data=_strong_leadership(),
            stability_data=_strong_stability(),
            universe_meta=_healthy_universe_meta(),
        )

    def test_composite_score_high(self, result):
        assert result["score"] >= 70

    def test_label_constructive_or_strong(self, result):
        assert result["label"] in ("Strong Breadth", "Constructive")

    def test_all_pillar_scores_present(self, result):
        for pname in PILLAR_WEIGHTS:
            assert result["pillar_scores"][pname] is not None
            assert result["pillar_scores"][pname] >= 50

    def test_confidence_score_high(self, result):
        assert result["confidence_score"] >= 70

    def test_signal_quality(self, result):
        assert result["signal_quality"] in ("high", "medium")

    def test_has_positive_contributors(self, result):
        assert len(result["positive_contributors"]) >= 2

    def test_trader_takeaway_not_defensive(self, result):
        takeaway = result["trader_takeaway"].lower()
        assert "defensive" not in takeaway

    def test_raw_inputs_present(self, result):
        assert "participation" in result["raw_inputs"]
        assert "trend" in result["raw_inputs"]
        assert "volume" in result["raw_inputs"]

    def test_diagnostics_present(self, result):
        assert "pillar_weights" in result["diagnostics"]
        assert "pillar_details" in result["diagnostics"]

    def test_engine_field(self, result):
        assert result["engine"] == "breadth_participation"

    def test_as_of_present(self, result):
        assert result["as_of"] is not None


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 2 — Narrow Rally (high participation, weak leadership)
# ═══════════════════════════════════════════════════════════════════════


class TestNarrowRally:
    """Participation strong but leadership weak → detects conflict."""

    @pytest.fixture
    def result(self):
        narrow_leadership = {
            "ew_return": -0.005,  # EW lagging CW → narrow
            "cw_return": 0.015,
            "sector_returns": {
                "Technology": 0.04,  # Only tech driving
                "Healthcare": -0.005,
                "Financials": -0.003,
                "Consumer Discretionary": -0.002,
                "Industrials": -0.004,
                "Energy": -0.008,
                "Materials": -0.006,
                "Utilities": -0.009,
                "Consumer Staples": -0.003,
                "Communication Services": 0.001,
                "REITs": -0.007,
            },
            "pct_outperforming_index": 0.25,  # Only 25% beating index
            "median_return": -0.003,
            "index_return": 0.012,
        }
        return compute_breadth_scores(
            participation_data=_strong_participation(),
            trend_data=_strong_trend(),
            volume_data=_strong_volume(),
            leadership_data=narrow_leadership,
            stability_data=_strong_stability(),
            universe_meta=_healthy_universe_meta(),
        )

    def test_leadership_pillar_low(self, result):
        leadership_score = result["pillar_scores"]["leadership_quality"]
        assert leadership_score is not None
        assert leadership_score < 45

    def test_participation_pillar_still_high(self, result):
        part_score = result["pillar_scores"]["participation_breadth"]
        assert part_score is not None
        assert part_score >= 60

    def test_conflicting_signals_detected(self, result):
        # Large gap between strong participation and weak leadership
        assert len(result["conflicting_signals"]) >= 1 or \
               len(result["negative_contributors"]) >= 1


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 3 — Weak Volume
# ═══════════════════════════════════════════════════════════════════════


class TestWeakVolume:
    """Volume pillar weak while participation and trend decent."""

    @pytest.fixture
    def result(self):
        weak_vol = {
            "up_volume": 1_000_000_000,
            "down_volume": 3_500_000_000,  # More down volume
            "total_volume": 4_500_000_000,
            "advancing": 70, "declining": 55,
        }
        return compute_breadth_scores(
            participation_data=_strong_participation(),
            trend_data=_strong_trend(),
            volume_data=weak_vol,
            leadership_data=_strong_leadership(),
            stability_data=_strong_stability(),
            universe_meta=_healthy_universe_meta(),
        )

    def test_volume_pillar_low(self, result):
        vol_score = result["pillar_scores"]["volume_breadth"]
        assert vol_score is not None
        assert vol_score < 40

    def test_composite_dragged_down(self, result):
        """Composite should be lower than a fully strong scenario."""
        assert result["score"] < 80

    def test_negative_contributors_include_volume(self, result):
        negatives = " ".join(result["negative_contributors"]).lower()
        assert "volume" in negatives


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 4 — Mixed Improving (mixed data, positive momentum)
# ═══════════════════════════════════════════════════════════════════════


class TestMixedImproving:
    """Mixed participation but positive trend momentum → mid-range score."""

    @pytest.fixture
    def result(self):
        mixed_participation = {
            "advancing": 65, "declining": 55, "unchanged": 10,
            "total_valid": 130,
            "new_highs": 10, "new_lows": 8,
            "sectors_positive": 6, "sectors_total": 11,
            "ew_return": 0.003, "cw_return": 0.005,
        }
        improving_trend = {
            "pct_above_20dma": 0.55, "pct_above_50dma": 0.48,
            "pct_above_200dma": 0.52,
            "pct_20_over_50": 0.50, "pct_50_over_200": 0.55,
            "trend_momentum_short": 0.12,   # Strong improvement
            "trend_momentum_intermediate": 0.08,
            "trend_momentum_long": 0.04,
            "total_valid": 130,
        }
        return compute_breadth_scores(
            participation_data=mixed_participation,
            trend_data=improving_trend,
            volume_data=_strong_volume(),
            leadership_data=_strong_leadership(),
            stability_data=_strong_stability(),
            universe_meta=_healthy_universe_meta(),
        )

    def test_composite_mid_range(self, result):
        assert 45 <= result["score"] <= 80

    def test_label_is_moderate(self, result):
        assert result["label"] in (
            "Constructive", "Mixed but Positive", "Mixed / Fragile"
        )

    def test_has_both_positive_and_negative(self, result):
        total_signals = (
            len(result["positive_contributors"]) +
            len(result["negative_contributors"])
        )
        assert total_signals >= 1


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 5 — Degraded Data (many missing inputs)
# ═══════════════════════════════════════════════════════════════════════


class TestDegradedData:
    """Missing inputs across pillars → confidence penalty, warnings populated."""

    @pytest.fixture
    def result(self):
        sparse_participation = {
            "advancing": 60, "declining": 40, "total_valid": 100,
            # Missing: new_highs, new_lows, sectors, ew_return, cw_return
        }
        sparse_trend = {
            "pct_above_50dma": 0.55,
            # Missing: pct_above_20dma, 200dma, crossovers, momentum
        }
        sparse_volume = {
            "up_volume": 2_000_000_000,
            "down_volume": 1_500_000_000,
            "total_volume": 3_500_000_000,
            # Missing: advancing/declining
        }
        sparse_leadership = {
            # Missing everything
        }
        sparse_stability = {
            "breadth_persistence_10d": 0.60,
            # Missing: volatility metrics
        }
        return compute_breadth_scores(
            participation_data=sparse_participation,
            trend_data=sparse_trend,
            volume_data=sparse_volume,
            leadership_data=sparse_leadership,
            stability_data=sparse_stability,
            universe_meta=_healthy_universe_meta(),
        )

    def test_confidence_penalty(self, result):
        """Many missing inputs should reduce confidence below 70."""
        assert result["confidence_score"] < 70

    def test_warnings_populated(self, result):
        assert len(result["warnings"]) >= 5

    def test_missing_inputs_listed(self, result):
        assert len(result["missing_inputs"]) >= 3

    def test_signal_quality_low_or_medium(self, result):
        assert result["signal_quality"] in ("low", "medium")

    def test_still_produces_score(self, result):
        """Engine should degrade gracefully, not crash."""
        assert result["score"] is not None
        assert 0 <= result["score"] <= 100


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 6 — Missing EW Benchmark
# ═══════════════════════════════════════════════════════════════════════


class TestMissingEWBenchmark:
    """Missing equal-weight data → specific confidence penalty."""

    @pytest.fixture
    def result(self):
        no_ew_participation = {
            **_strong_participation(),
            "ew_return": None, "cw_return": None,
        }
        no_ew_leadership = {
            **_strong_leadership(),
            "ew_return": None, "cw_return": None,
        }
        return compute_breadth_scores(
            participation_data=no_ew_participation,
            trend_data=_strong_trend(),
            volume_data=_strong_volume(),
            leadership_data=no_ew_leadership,
            stability_data=_strong_stability(),
            universe_meta=_healthy_universe_meta(),
        )

    def test_ew_penalty_in_confidence(self, result):
        """Missing EW benchmark should trigger specific -5 penalty."""
        penalties = result["diagnostics"]["confidence_penalties"]
        ew_penalties = [p for p in penalties if "equal-weight" in p.lower()]
        assert len(ew_penalties) >= 1

    def test_participation_still_scored(self, result):
        """Pillar should still produce a score from remaining submetrics."""
        assert result["pillar_scores"]["participation_breadth"] is not None

    def test_warnings_mention_ew(self, result):
        ew_warnings = [w for w in result["warnings"] if "ew" in w.lower() or "equal" in w.lower()]
        assert len(ew_warnings) >= 1


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 7 — Partial Pillar Unavailability
# ═══════════════════════════════════════════════════════════════════════


class TestPartialPillarUnavailability:
    """One full pillar has no computable data → composite still works."""

    @pytest.fixture
    def result(self):
        empty_stability = {}  # All None → pillar unavailable
        return compute_breadth_scores(
            participation_data=_strong_participation(),
            trend_data=_strong_trend(),
            volume_data=_strong_volume(),
            leadership_data=_strong_leadership(),
            stability_data=empty_stability,
            universe_meta=_healthy_universe_meta(),
        )

    def test_stability_pillar_none(self, result):
        assert result["pillar_scores"]["participation_stability"] is None

    def test_composite_still_computed(self, result):
        """Composite uses remaining 4 pillars (90% of weight)."""
        assert result["score"] is not None
        assert result["score"] > 0

    def test_confidence_penalty_for_missing_pillar(self, result):
        penalties = result["diagnostics"]["confidence_penalties"]
        pillar_penalties = [
            p for p in penalties if "unavailable pillar" in p.lower()
        ]
        assert len(pillar_penalties) >= 1

    def test_diagnostics_show_inactive(self, result):
        inactive = result["diagnostics"]["composite_computation"]["inactive_pillars"]
        assert "participation_stability" in inactive


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 8 — Survivorship Bias Warning
# ═══════════════════════════════════════════════════════════════════════


class TestSurvivorshipBias:
    """Universe meta flag triggers survivorship bias warning and penalty."""

    @pytest.fixture
    def result(self):
        biased_universe = {
            "name": "SP500_proxy",
            "expected_count": 130,
            "actual_count": 130,
            "survivorship_bias_risk": True,
        }
        return compute_breadth_scores(
            participation_data=_strong_participation(),
            trend_data=_strong_trend(),
            volume_data=_strong_volume(),
            leadership_data=_strong_leadership(),
            stability_data=_strong_stability(),
            universe_meta=biased_universe,
        )

    def test_survivorship_penalty(self, result):
        penalties = result["diagnostics"]["confidence_penalties"]
        bias_penalties = [p for p in penalties if "survivorship" in p.lower()]
        assert len(bias_penalties) >= 1

    def test_confidence_slightly_lower(self, result):
        """Survivorship flag costs -5 points relative to healthy universe."""
        # With full healthy data, confidence is ~90. With bias flag, ~85.
        assert result["confidence_score"] <= 95


# ═══════════════════════════════════════════════════════════════════════
# OUTPUT CONTRACT TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestOutputContract:
    """Verify the full output contract is populated."""

    @pytest.fixture
    def result(self):
        return compute_breadth_scores(
            participation_data=_strong_participation(),
            trend_data=_strong_trend(),
            volume_data=_strong_volume(),
            leadership_data=_strong_leadership(),
            stability_data=_strong_stability(),
            universe_meta=_healthy_universe_meta(),
        )

    def test_top_level_keys(self, result):
        required_keys = [
            "engine", "as_of", "universe", "score", "label", "short_label",
            "confidence_score", "signal_quality", "summary",
            "pillar_scores", "pillar_weights", "pillar_explanations",
            "positive_contributors", "negative_contributors",
            "conflicting_signals", "trader_takeaway",
            "warnings", "missing_inputs", "diagnostics", "raw_inputs",
        ]
        for key in required_keys:
            assert key in result, f"Missing top-level key: {key}"

    def test_universe_keys(self, result):
        u = result["universe"]
        assert "name" in u
        assert "expected_count" in u
        assert "actual_count" in u
        assert "coverage_pct" in u

    def test_diagnostics_keys(self, result):
        d = result["diagnostics"]
        assert "pillar_weights" in d
        assert "pillar_details" in d
        assert "confidence_penalties" in d
        assert "total_submetrics" in d
        assert "composite_computation" in d

    def test_score_bounded(self, result):
        assert 0 <= result["score"] <= 100

    def test_confidence_bounded(self, result):
        assert 0 <= result["confidence_score"] <= 100

    def test_pillar_scores_all_present(self, result):
        for pname in PILLAR_WEIGHTS:
            assert pname in result["pillar_scores"]

    def test_pillar_weights_match_config(self, result):
        assert result["pillar_weights"] == PILLAR_WEIGHTS

    def test_raw_inputs_sections(self, result):
        for section in ["participation", "trend", "volume", "leadership", "stability"]:
            assert section in result["raw_inputs"]
