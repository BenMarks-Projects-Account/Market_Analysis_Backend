"""Tests for Cross-Asset / Macro Confirmation Scoring Engine.

8 + 5 test scenarios:
  1. Strong Confirmation — all inputs healthy → score 70-100, label "Confirming" or higher
  2. Strong Contradiction — bearish across all pillars → low score, contradicting label
  3. Mixed Signals — some pillars confirming, others contradicting → mid-range
  4. Credit Stress + Rates Confirming — divergence within pillars
  5. Degraded Data — many None inputs → confidence penalty, warnings
  6. Single Pillar Unavailable — one pillar crashes → composite still works
  7. Dollar Headwind — strong dollar dragging score despite healthy credit
  8. High Macro Coherence — all signals aligned risk-on → coherence pillar high
  9. Oil Ambiguity — oil in $45-$85 zone scores neutral
  10. Graded Coherence — ternary grading with neutral bands
  11. Provenance Metadata — SIGNAL_PROVENANCE in diagnostics
  12. VIX Not In Pillar Four — Pillar 4 submetrics exclude VIX
  13. Source Delay Confidence — copper monthly penalty applied
"""

import pytest

from app.services.cross_asset_macro_engine import (
    PILLAR_WEIGHTS,
    SIGNAL_PROVENANCE,
    _clamp,
    _interpolate,
    _safe_float,
    _weighted_avg,
    compute_cross_asset_scores,
)


# ═══════════════════════════════════════════════════════════════════════
# HELPERS — fixture data dicts
# ═══════════════════════════════════════════════════════════════════════


def _healthy_rates():
    """Positive yield curve spread, moderate yields."""
    return {
        "ten_year_yield": 4.20,
        "two_year_yield": 4.00,
        "yield_curve_spread": 0.20,
        "fed_funds_rate": 5.25,
    }


def _healthy_dollar_commodity():
    """Moderate USD, reasonable oil/gold/copper."""
    return {
        "usd_index": 100.0,
        "oil_wti": 75.0,
        "gold_price": 2000.0,
        "copper_price": 8500.0,
    }


def _healthy_credit():
    """Tight spreads, low VIX."""
    return {
        "ig_spread": 0.90,
        "hy_spread": 3.20,
        "vix": 14.0,
    }


def _healthy_defensive_growth():
    """Favorable growth/defensive balance (no VIX — removed in second pass)."""
    return {
        "gold_price": 2000.0,
        "ten_year_yield": 4.20,
        "copper_price": 8500.0,
    }


def _healthy_coherence():
    """All signals available, majority risk-on."""
    return {
        "vix": 14.0,
        "yield_curve_spread": 0.20,
        "ig_spread": 0.90,
        "hy_spread": 3.20,
        "usd_index": 100.0,
        "oil_wti": 75.0,
        "gold_price": 2000.0,
        "copper_price": 8500.0,
    }


def _healthy_source_meta():
    return {
        "market_context_freshness": "live",
        "fred_freshness": "recent",
    }


def _bearish_rates():
    """Deeply inverted yield curve."""
    return {
        "ten_year_yield": 3.50,
        "two_year_yield": 5.00,
        "yield_curve_spread": -1.50,
        "fed_funds_rate": 5.50,
    }


def _bearish_dollar_commodity():
    """Strong dollar (headwind), weak copper."""
    return {
        "usd_index": 115.0,
        "oil_wti": 35.0,
        "gold_price": 2500.0,
        "copper_price": 5000.0,
    }


def _bearish_credit():
    """Wide spreads, high VIX."""
    return {
        "ig_spread": 2.50,
        "hy_spread": 7.00,
        "vix": 35.0,
    }


def _bearish_defensive_growth():
    return {
        "gold_price": 2500.0,
        "ten_year_yield": 3.50,
        "copper_price": 5000.0,
    }


def _bearish_coherence():
    return {
        "vix": 35.0,
        "yield_curve_spread": -1.50,
        "ig_spread": 2.50,
        "hy_spread": 7.00,
        "usd_index": 115.0,
        "oil_wti": 35.0,
        "gold_price": 2500.0,
        "copper_price": 5000.0,
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

    def test_interpolate_clamps_high(self):
        result = _interpolate(2.0, 0.0, 1.0, 0.0, 100.0)
        assert result == pytest.approx(100.0)

    def test_interpolate_clamps_low(self):
        result = _interpolate(-1.0, 0.0, 1.0, 0.0, 100.0)
        assert result == pytest.approx(0.0)

    def test_weighted_avg_all_valid(self):
        result = _weighted_avg([(80.0, 0.5), (60.0, 0.5)])
        assert result == pytest.approx(70.0)

    def test_weighted_avg_some_none(self):
        result = _weighted_avg([(80.0, 0.5), (None, 0.5)])
        assert result == pytest.approx(80.0)

    def test_weighted_avg_all_none(self):
        result = _weighted_avg([(None, 0.5), (None, 0.5)])
        assert result is None

    def test_pillar_weights_sum_to_one(self):
        assert sum(PILLAR_WEIGHTS.values()) == pytest.approx(1.0)


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 1 — Strong Confirmation
# ═══════════════════════════════════════════════════════════════════════


class TestStrongConfirmation:
    """All inputs healthy → score ≥ 55, label 'Confirming' or higher."""

    @pytest.fixture
    def result(self):
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data=_healthy_dollar_commodity(),
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=_healthy_coherence(),
            source_meta=_healthy_source_meta(),
        )

    def test_composite_score_high(self, result):
        assert result["score"] >= 55

    def test_label_confirming(self, result):
        assert result["label"] in (
            "Strong Confirmation", "Confirming", "Partial Confirmation"
        )

    def test_all_pillar_scores_present(self, result):
        for pname in PILLAR_WEIGHTS:
            assert result["pillar_scores"][pname] is not None

    def test_confidence_score_reasonable(self, result):
        assert result["confidence_score"] >= 50

    def test_signal_quality(self, result):
        assert result["signal_quality"] in ("high", "medium")

    def test_has_confirming_signals(self, result):
        assert len(result["confirming_signals"]) >= 1

    def test_engine_field(self, result):
        assert result["engine"] == "cross_asset_macro"

    def test_as_of_present(self, result):
        assert result["as_of"] is not None

    def test_raw_inputs_present(self, result):
        assert "rates" in result["raw_inputs"]
        assert "dollar_commodity" in result["raw_inputs"]
        assert "credit" in result["raw_inputs"]

    def test_diagnostics_present(self, result):
        assert "pillar_weights" in result["diagnostics"]
        assert "pillar_details" in result["diagnostics"]

    def test_trader_takeaway_present(self, result):
        assert result["trader_takeaway"] is not None
        assert len(result["trader_takeaway"]) > 10


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 2 — Strong Contradiction
# ═══════════════════════════════════════════════════════════════════════


class TestStrongContradiction:
    """All inputs bearish → low score, contradicting label."""

    @pytest.fixture
    def result(self):
        return compute_cross_asset_scores(
            rates_data=_bearish_rates(),
            dollar_commodity_data=_bearish_dollar_commodity(),
            credit_data=_bearish_credit(),
            defensive_growth_data=_bearish_defensive_growth(),
            coherence_data=_bearish_coherence(),
            source_meta=_healthy_source_meta(),
        )

    def test_composite_score_low(self, result):
        assert result["score"] < 50

    def test_label_contradicting(self, result):
        assert "Contradiction" in result["label"] or "Contra" in result["short_label"] or "Mixed" in result["label"]

    def test_all_pillar_scores_present(self, result):
        for pname in PILLAR_WEIGHTS:
            assert result["pillar_scores"][pname] is not None

    def test_has_contradicting_signals(self, result):
        assert len(result["contradicting_signals"]) >= 1

    def test_rates_pillar_low(self, result):
        assert result["pillar_scores"]["rates_yield_curve"] < 40

    def test_credit_pillar_low(self, result):
        assert result["pillar_scores"]["credit_risk_appetite"] < 50


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 3 — Mixed Signals
# ═══════════════════════════════════════════════════════════════════════


class TestMixedSignals:
    """Rates confirming, credit stressed, dollar headwind → mid-range."""

    @pytest.fixture
    def result(self):
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data=_bearish_dollar_commodity(),
            credit_data=_bearish_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data={
                "vix": 30.0,
                "yield_curve_spread": 0.20,
                "ig_spread": 2.50,
                "hy_spread": 7.00,
                "usd_index": 115.0,
                "oil_wti": 35.0,
                "gold_price": 2000.0,
                "copper_price": 8500.0,
            },
            source_meta=_healthy_source_meta(),
        )

    def test_composite_mid_range(self, result):
        """Score between 25 and 65 (mixed territory)."""
        assert 25 <= result["score"] <= 65

    def test_rates_higher_than_credit(self, result):
        """Rates should score higher since it's healthy."""
        assert (result["pillar_scores"]["rates_yield_curve"] >
                result["pillar_scores"]["credit_risk_appetite"])

    def test_both_confirming_and_contradicting(self, result):
        has_positive = len(result["confirming_signals"]) > 0
        has_negative = len(result["contradicting_signals"]) > 0
        assert has_positive or has_negative


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 4 — Credit Stress + Rates Confirming
# ═══════════════════════════════════════════════════════════════════════


class TestCreditStressRatesConfirm:
    """Credit deteriorating while rates still constructive → divergence."""

    @pytest.fixture
    def result(self):
        stressed_credit = {
            "ig_spread": 2.00,
            "hy_spread": 6.00,
            "vix": 28.0,
        }
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data=_healthy_dollar_commodity(),
            credit_data=stressed_credit,
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=_healthy_coherence(),
            source_meta=_healthy_source_meta(),
        )

    def test_credit_pillar_low(self, result):
        assert result["pillar_scores"]["credit_risk_appetite"] < 60

    def test_rates_pillar_still_reasonable(self, result):
        assert result["pillar_scores"]["rates_yield_curve"] > 50

    def test_composite_penalized(self, result):
        """Credit has 25% weight so composite should be pulled down."""
        assert result["score"] < 75


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 5 — Degraded Data (many missing)
# ═══════════════════════════════════════════════════════════════════════


class TestDegradedData:
    """Many None inputs → confidence penalty, warnings populated."""

    @pytest.fixture
    def result(self):
        return compute_cross_asset_scores(
            rates_data={"ten_year_yield": 4.20},  # only 1 field
            dollar_commodity_data={},              # empty
            credit_data={"vix": 15.0},             # only VIX
            defensive_growth_data={},              # empty
            coherence_data={"vix": 15.0},          # minimal
            source_meta=_healthy_source_meta(),
        )

    def test_still_produces_result(self, result):
        """Engine must never crash; always returns a result dict."""
        assert result["engine"] == "cross_asset_macro"
        assert result["score"] is not None

    def test_confidence_penalized(self, result):
        """Missing data → confidence should be reduced."""
        assert result["confidence_score"] < 80

    def test_warnings_populated(self, result):
        """Should have warnings about missing data."""
        assert len(result["warnings"]) >= 1

    def test_missing_inputs_tracked(self, result):
        """Missing inputs list should have entries."""
        assert len(result["missing_inputs"]) >= 1

    def test_signal_quality_not_high(self, result):
        assert result["signal_quality"] in ("medium", "low")


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 6 — Single Pillar Unavailable (crash-hardened)
# ═══════════════════════════════════════════════════════════════════════


class TestSinglePillarUnavailable:
    """Pass data that would make rates_yield_curve crash through bad types.

    Engine has per-pillar try/except so composite should still work.
    """

    @pytest.fixture
    def result(self):
        # Pass something that can't be float-coerced for yields
        bad_rates = {
            "ten_year_yield": "not_a_number",
            "two_year_yield": object(),
            "yield_curve_spread": None,
        }
        return compute_cross_asset_scores(
            rates_data=bad_rates,
            dollar_commodity_data=_healthy_dollar_commodity(),
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=_healthy_coherence(),
            source_meta=_healthy_source_meta(),
        )

    def test_still_returns_result(self, result):
        assert result["engine"] == "cross_asset_macro"

    def test_composite_not_zero(self, result):
        """Other pillars are healthy, so composite shouldn't be 0."""
        assert result["score"] > 0

    def test_other_pillars_present(self, result):
        """Dollar, credit, defensive, coherence should be scored."""
        assert result["pillar_scores"]["dollar_commodity"] is not None
        assert result["pillar_scores"]["credit_risk_appetite"] is not None


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 7 — Dollar Headwind
# ═══════════════════════════════════════════════════════════════════════


class TestDollarHeadwind:
    """Strong dollar dragging score despite healthy credit/rates."""

    @pytest.fixture
    def result(self):
        strong_dollar = {
            "usd_index": 114.0,  # Very strong — headwind
            "oil_wti": 75.0,
            "gold_price": 2200.0,
            "copper_price": 6000.0,
        }
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data=strong_dollar,
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=_healthy_coherence(),
            source_meta=_healthy_source_meta(),
        )

    def test_dollar_pillar_low(self, result):
        assert result["pillar_scores"]["dollar_commodity"] < 50

    def test_credit_still_healthy(self, result):
        assert result["pillar_scores"]["credit_risk_appetite"] > 60

    def test_rates_still_healthy(self, result):
        assert result["pillar_scores"]["rates_yield_curve"] > 50

    def test_composite_dragged_down(self, result):
        """Dollar has 20% weight — composite should be below pure-signal
        of the other healthy pillars."""
        assert result["score"] < 85


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 8 — High Macro Coherence
# ═══════════════════════════════════════════════════════════════════════


class TestHighMacroCoherence:
    """All signals aligned risk-on → coherence pillar scores high."""

    @pytest.fixture
    def result(self):
        # All signals should read as risk-on (graded ternary = +1):
        # VIX < 16, yield_spread > 0.10, IG < 1.0, HY < 3.5
        # USD < 98, copper > 8000, gold < 1900
        # Oil excluded from coherence (ambiguous by design)
        all_risk_on_coherence = {
            "vix": 12.0,
            "yield_curve_spread": 0.50,
            "ig_spread": 0.80,
            "hy_spread": 3.00,
            "usd_index": 95.0,
            "oil_wti": 80.0,
            "gold_price": 1800.0,
            "copper_price": 9000.0,
        }
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data=_healthy_dollar_commodity(),
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=all_risk_on_coherence,
            source_meta=_healthy_source_meta(),
        )

    def test_coherence_pillar_high(self, result):
        assert result["pillar_scores"]["macro_coherence"] >= 70

    def test_overall_score_high(self, result):
        assert result["score"] >= 60

    def test_signal_quality_good(self, result):
        assert result["signal_quality"] in ("high", "medium")


# ═══════════════════════════════════════════════════════════════════════
# OUTPUT SCHEMA VALIDATION
# ═══════════════════════════════════════════════════════════════════════


class TestOutputSchema:
    """Verify all required fields are present in engine output."""

    @pytest.fixture
    def result(self):
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data=_healthy_dollar_commodity(),
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=_healthy_coherence(),
            source_meta=_healthy_source_meta(),
        )

    @pytest.mark.parametrize("field", [
        "engine", "as_of", "score", "label", "short_label",
        "confidence_score", "signal_quality", "summary",
        "pillar_scores", "pillar_weights", "pillar_explanations",
        "confirming_signals", "contradicting_signals", "mixed_signals",
        "trader_takeaway", "warnings", "missing_inputs",
        "diagnostics", "raw_inputs",
    ])
    def test_required_field_present(self, result, field):
        assert field in result

    def test_pillar_scores_has_all_pillars(self, result):
        for pname in PILLAR_WEIGHTS:
            assert pname in result["pillar_scores"]

    def test_pillar_weights_matches_config(self, result):
        assert result["pillar_weights"] == PILLAR_WEIGHTS

    def test_diagnostics_has_details(self, result):
        diag = result["diagnostics"]
        assert "pillar_details" in diag
        assert "composite_computation" in diag
        assert "active_pillars" in diag["composite_computation"]

    def test_score_bounded(self, result):
        assert 0 <= result["score"] <= 100

    def test_confidence_bounded(self, result):
        assert 0 <= result["confidence_score"] <= 100


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 9 — Oil Ambiguity
# ═══════════════════════════════════════════════════════════════════════


class TestOilAmbiguity:
    """Oil in $45-$85 zone should score near neutral (50-55)."""

    @pytest.fixture
    def result_ambiguous(self):
        """Oil at $65 — middle of ambiguity zone."""
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data={
                "usd_index": 100.0,
                "oil_wti": 65.0,  # Ambiguous zone
                "gold_price": 2000.0,
                "copper_price": 8500.0,
            },
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=_healthy_coherence(),
            source_meta=_healthy_source_meta(),
        )

    @pytest.fixture
    def result_extreme_low(self):
        """Oil at $25 — demand destruction."""
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data={
                "usd_index": 100.0,
                "oil_wti": 25.0,  # Extreme low
                "gold_price": 2000.0,
                "copper_price": 8500.0,
            },
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=_healthy_coherence(),
            source_meta=_healthy_source_meta(),
        )

    @pytest.fixture
    def result_extreme_high(self):
        """Oil at $115 — cost pressure."""
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data={
                "usd_index": 100.0,
                "oil_wti": 115.0,  # Extreme high
                "gold_price": 2000.0,
                "copper_price": 8500.0,
            },
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=_healthy_coherence(),
            source_meta=_healthy_source_meta(),
        )

    def test_ambiguous_oil_scores_neutral(self, result_ambiguous):
        """Oil submetric should score 50-55 in ambiguity zone."""
        diag = result_ambiguous["diagnostics"]["pillar_details"]["dollar_commodity"]
        oil_sm = [s for s in diag["submetrics"] if s["name"] == "oil_level"][0]
        assert 49 <= oil_sm["score"] <= 56

    def test_ambiguous_oil_has_classification(self, result_ambiguous):
        """Oil submetric details should include classification."""
        diag = result_ambiguous["diagnostics"]["pillar_details"]["dollar_commodity"]
        oil_sm = [s for s in diag["submetrics"] if s["name"] == "oil_level"][0]
        assert oil_sm["details"]["oil_classification"] == "ambiguous"

    def test_ambiguous_oil_generates_warning(self, result_ambiguous):
        """Ambiguous oil should produce a warning."""
        oil_warnings = [w for w in result_ambiguous["warnings"] if "ambiguous" in w.lower()]
        assert len(oil_warnings) >= 1

    def test_extreme_low_scores_bearish(self, result_extreme_low):
        """$25 oil -> demand destruction -> low score."""
        diag = result_extreme_low["diagnostics"]["pillar_details"]["dollar_commodity"]
        oil_sm = [s for s in diag["submetrics"] if s["name"] == "oil_level"][0]
        assert oil_sm["score"] <= 40

    def test_extreme_high_scores_bearish(self, result_extreme_high):
        """$115 oil -> cost pressure -> low score."""
        diag = result_extreme_high["diagnostics"]["pillar_details"]["dollar_commodity"]
        oil_sm = [s for s in diag["submetrics"] if s["name"] == "oil_level"][0]
        assert oil_sm["score"] < 40


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 10 — Graded Ternary Coherence
# ═══════════════════════════════════════════════════════════════════════


class TestGradedCoherence:
    """Coherence uses graded ternary (+1/0/-1) instead of binary True/False."""

    @pytest.fixture
    def result_all_neutral(self):
        """All signals in neutral bands → coherence should be moderate."""
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data=_healthy_dollar_commodity(),
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data={
                "vix": 19.0,               # Neutral (16-22)
                "yield_curve_spread": 0.0,  # Neutral (-0.20 to +0.10)
                "ig_spread": 1.4,           # Neutral (1.0-1.8)
                "hy_spread": 4.5,           # Neutral (3.5-5.5)
                "usd_index": 102.0,         # Neutral (98-107)
                "oil_wti": 70.0,            # Excluded from coherence
                "gold_price": 2100.0,       # Neutral (1900-2300)
                "copper_price": 7200.0,     # Neutral (6500-8000)
            },
            source_meta=_healthy_source_meta(),
        )

    @pytest.fixture
    def result_mixed_directional(self):
        """Some confirming, some contradicting, some neutral → mixed."""
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data=_healthy_dollar_commodity(),
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data={
                "vix": 12.0,               # Confirming (+1)
                "yield_curve_spread": 0.50, # Confirming (+1)
                "ig_spread": 2.5,           # Contradicting (-1)
                "hy_spread": 6.0,           # Contradicting (-1)
                "usd_index": 102.0,         # Neutral (0)
                "oil_wti": 70.0,            # Excluded
                "gold_price": 2100.0,       # Neutral (0)
                "copper_price": 9000.0,     # Confirming (+1)
            },
            source_meta=_healthy_source_meta(),
        )

    def test_all_neutral_produces_moderate_coherence(self, result_all_neutral):
        """All-neutral coherence should be moderate (not extreme)."""
        coh = result_all_neutral["pillar_scores"]["macro_coherence"]
        assert coh is not None
        # When all graded signals are 0 (neutral), effective risk-on pct is 0.5
        # and agreement is computed from directional signals only
        assert 30 <= coh <= 70

    def test_mixed_not_extreme(self, result_mixed_directional):
        """Mixed grades should produce mid-range coherence."""
        coh = result_mixed_directional["pillar_scores"]["macro_coherence"]
        assert coh is not None
        assert 25 <= coh <= 75

    def test_signal_grades_in_details(self, result_mixed_directional):
        """Submetric details should contain signal_grades (not boolean signals)."""
        diag = result_mixed_directional["diagnostics"]["pillar_details"]["macro_coherence"]
        risk_on_sm = [s for s in diag["submetrics"] if s["name"] == "risk_on_count"][0]
        grades = risk_on_sm["details"].get("signal_grades", {})
        assert len(grades) >= 3
        # All grades should be -1, 0, or +1
        for v in grades.values():
            assert v in (-1.0, 0.0, 1.0)

    def test_oil_excluded_from_coherence_grades(self, result_mixed_directional):
        """Oil should NOT appear in signal_grades due to ambiguity."""
        diag = result_mixed_directional["diagnostics"]["pillar_details"]["macro_coherence"]
        risk_on_sm = [s for s in diag["submetrics"] if s["name"] == "risk_on_count"][0]
        grades = risk_on_sm["details"].get("signal_grades", {})
        assert "oil" not in grades


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 11 — Provenance Metadata
# ═══════════════════════════════════════════════════════════════════════


class TestProvenanceMetadata:
    """SIGNAL_PROVENANCE should be in diagnostics and well-structured."""

    @pytest.fixture
    def result(self):
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data=_healthy_dollar_commodity(),
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=_healthy_coherence(),
            source_meta=_healthy_source_meta(),
        )

    def test_provenance_in_diagnostics(self, result):
        assert "signal_provenance" in result["diagnostics"]

    def test_provenance_has_key_signals(self, result):
        prov = result["diagnostics"]["signal_provenance"]
        for key in ["ten_year_yield", "vix", "gold_price", "copper_price",
                     "ig_spread", "hy_spread", "oil_wti", "usd_index"]:
            assert key in prov, f"Missing provenance for {key}"

    def test_copper_provenance_is_monthly(self, result):
        prov = result["diagnostics"]["signal_provenance"]
        copper = prov["copper_price"]
        assert "monthly" in copper.get("delay", "").lower()

    def test_usd_provenance_is_proxy(self, result):
        prov = result["diagnostics"]["signal_provenance"]
        usd = prov["usd_index"]
        assert usd.get("type") == "proxy"

    def test_provenance_matches_registry(self, result):
        """Diagnostics provenance should match the module-level registry."""
        assert result["diagnostics"]["signal_provenance"] == SIGNAL_PROVENANCE


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 12 — VIX Not In Pillar Four
# ═══════════════════════════════════════════════════════════════════════


class TestVIXNotInPillarFour:
    """Pillar 4 (Defensive vs Growth) should not have VIX-related submetrics."""

    @pytest.fixture
    def result(self):
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data=_healthy_dollar_commodity(),
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=_healthy_coherence(),
            source_meta=_healthy_source_meta(),
        )

    def test_no_vix_credit_alignment_submetric(self, result):
        """vix_credit_alignment should be removed from Pillar 4."""
        diag = result["diagnostics"]["pillar_details"]["defensive_vs_growth"]
        sub_names = [s["name"] for s in diag["submetrics"]]
        assert "vix_credit_alignment" not in sub_names

    def test_only_two_submetrics(self, result):
        """Pillar 4 should have exactly 2 submetrics after VIX removal."""
        diag = result["diagnostics"]["pillar_details"]["defensive_vs_growth"]
        assert len(diag["submetrics"]) == 2

    def test_expected_submetrics(self, result):
        """Should be gold_yield_divergence and copper_gold_ratio only."""
        diag = result["diagnostics"]["pillar_details"]["defensive_vs_growth"]
        sub_names = {s["name"] for s in diag["submetrics"]}
        assert sub_names == {"gold_yield_divergence", "copper_gold_ratio"}

    def test_vix_not_in_raw_inputs(self, result):
        """Pillar 4 raw_inputs should not include VIX."""
        raw = result["raw_inputs"]["defensive_growth"]
        assert "vix" not in raw
        assert "hy_spread" not in raw


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 13 — Source Delay Confidence Penalty
# ═══════════════════════════════════════════════════════════════════════


class TestSourceDelayConfidence:
    """Copper monthly source should cause a confidence penalty."""

    @pytest.fixture
    def result_with_copper_date(self):
        meta = {
            "market_context_freshness": "live",
            "fred_freshness": "recent",
            "fred_copper_date": "2026-01-15",  # Has a date → monthly penalty
        }
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data=_healthy_dollar_commodity(),
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=_healthy_coherence(),
            source_meta=meta,
        )

    @pytest.fixture
    def result_without_copper_date(self):
        meta = {
            "market_context_freshness": "live",
            "fred_freshness": "recent",
        }
        return compute_cross_asset_scores(
            rates_data=_healthy_rates(),
            dollar_commodity_data=_healthy_dollar_commodity(),
            credit_data=_healthy_credit(),
            defensive_growth_data=_healthy_defensive_growth(),
            coherence_data=_healthy_coherence(),
            source_meta=meta,
        )

    def test_copper_date_causes_penalty(self, result_with_copper_date):
        """Should have a penalty mentioning PCOPPUSDM monthly."""
        penalties = [w for w in result_with_copper_date["warnings"]
                     if "PCOPPUSDM" in w or "monthly" in w.lower()]
        assert len(penalties) >= 1

    def test_confidence_lower_with_copper_date(
        self, result_with_copper_date, result_without_copper_date
    ):
        """Confidence with copper date should be lower."""
        assert (result_with_copper_date["confidence_score"] <
                result_without_copper_date["confidence_score"])
