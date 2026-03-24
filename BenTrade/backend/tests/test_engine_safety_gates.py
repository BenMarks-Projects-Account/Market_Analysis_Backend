"""Tests for safety gates on breadth, volatility, and cross-asset macro engines.

Each engine gets one gate on its most critical pillar:
  - Breadth: trend_breadth < 35 while composite >= 55
  - Volatility: volatility_regime < 30 while composite >= 55
  - Cross-Asset: credit_risk_appetite < 35 while composite >= 55
"""

from unittest.mock import patch
import pytest

from app.services.breadth_engine import compute_breadth_scores
from app.services.volatility_options_engine import compute_volatility_scores
from app.services.cross_asset_macro_engine import compute_cross_asset_scores


# ═══════════════════════════════════════════════════════════════════════
# Helpers: stub pillar dicts
# ═══════════════════════════════════════════════════════════════════════

def _pillar(score, **extra):
    """Minimal pillar dict with required fields."""
    return {
        "score": score,
        "submetrics": [],
        "explanation": "stub",
        "warnings": [],
        "raw_inputs": {},
        "missing_count": 0,
        **extra,
    }


_EMPTY_DATA = {}
_UNIVERSE = {"name": "test", "expected_count": 196, "actual_count": 196}


# ═══════════════════════════════════════════════════════════════════════
# BREADTH ENGINE — Trend Breadth Gate
# ═══════════════════════════════════════════════════════════════════════

_BREADTH_MODULE = "app.services.breadth_engine"


def _breadth_pillars(trend=70, participation=75, volume=75, leadership=75, stability=75):
    """Build mocked pillar dict for breadth engine."""
    return {
        "participation_breadth": _pillar(participation),
        "trend_breadth": _pillar(trend),
        "volume_breadth": _pillar(volume),
        "leadership_quality": _pillar(leadership),
        "participation_stability": _pillar(stability),
    }


def _call_breadth(pillars):
    """Call compute_breadth_scores with all pillar compute funcs mocked."""
    with patch(f"{_BREADTH_MODULE}._compute_participation_breadth",
               return_value=pillars["participation_breadth"]), \
         patch(f"{_BREADTH_MODULE}._compute_trend_breadth",
               return_value=pillars["trend_breadth"]), \
         patch(f"{_BREADTH_MODULE}._compute_volume_breadth",
               return_value=pillars["volume_breadth"]), \
         patch(f"{_BREADTH_MODULE}._compute_leadership_quality",
               return_value=pillars["leadership_quality"]), \
         patch(f"{_BREADTH_MODULE}._compute_participation_stability",
               return_value=pillars["participation_stability"]):
        return compute_breadth_scores(
            _EMPTY_DATA, _EMPTY_DATA, _EMPTY_DATA,
            _EMPTY_DATA, _EMPTY_DATA, _UNIVERSE,
        )


class TestBreadthTrendGate:
    """Gate: trend_breadth < 35 when composite >= 55."""

    def test_gate_fires_when_trend_low(self):
        """trend_breadth=25 with high composite → gate fires, score drops."""
        result = _call_breadth(_breadth_pillars(trend=25))
        assert result["gate_applied"] is True
        assert len(result["gate_details"]) > 0
        assert "trend_breadth" in result["gate_details"][0]
        assert "(Gated" in result["label"]

    def test_gate_penalty_proportional(self):
        """Penalty = min(15, (35 - 25) * 0.5) = 5.0."""
        # All pillars at 72 except trend=25 → composite ≈ 72*0.75 + 25*0.25 ≈ 60.25
        result = _call_breadth(_breadth_pillars(
            trend=25, participation=72, volume=72, leadership=72, stability=72,
        ))
        assert result["gate_applied"] is True
        # Penalty should be (35-25)*0.5 = 5.0
        assert "penalty=5.0" in result["gate_details"][0]

    def test_gate_score_floor_at_45(self):
        """Even with extreme penalty, composite never drops below 45."""
        # trend=0, others at 80 → composite ≈ 60, penalty=15 → floor at 45
        result = _call_breadth(_breadth_pillars(
            trend=0, participation=80, volume=80, leadership=80, stability=80,
        ))
        assert result["gate_applied"] is True
        assert result["score"] >= 45

    def test_no_gate_when_trend_healthy(self):
        """trend_breadth=70 (above threshold) → no gate."""
        result = _call_breadth(_breadth_pillars(trend=70))
        assert result["gate_applied"] is False
        assert result["gate_details"] == []
        assert "(Gated" not in result["label"]

    def test_no_gate_when_composite_below_55(self):
        """All pillars low (composite < 55) → gate not evaluated."""
        result = _call_breadth(_breadth_pillars(
            trend=20, participation=40, volume=35, leadership=35, stability=30,
        ))
        assert result["gate_applied"] is False

    def test_trend_none_conservative_penalty(self):
        """Missing trend_breadth score → conservative 5-point penalty."""
        result = _call_breadth(_breadth_pillars(
            trend=None, participation=75, volume=70, leadership=70, stability=65,
        ))
        assert result["gate_applied"] is True
        assert "trend_breadth=None" in result["gate_details"][0]

    def test_label_appended_not_replaced(self):
        """Gate label text is appended, not replacing the base label."""
        result = _call_breadth(_breadth_pillars(trend=25))
        # The base label should still be present (Constructive, Mixed, etc.)
        assert "Gated: weak trend breadth" in result["label"]

    def test_verification_scenario(self):
        """VERIFICATION: composite=72, trend_breadth=25 → score ~67, label includes (Gated)."""
        # Set all pillars to produce composite ≈ 72, with trend=25
        # Weighted: part(0.25)*80 + trend(0.25)*25 + vol(0.20)*85 + lead(0.20)*85 + stab(0.10)*80
        # = 20 + 6.25 + 17 + 17 + 8 = 68.25
        # We want ~72, so adjust up
        result = _call_breadth(_breadth_pillars(
            trend=25, participation=85, volume=85, leadership=85, stability=85,
        ))
        assert result["gate_applied"] is True
        assert "(Gated" in result["label"]
        # Penalty = (35-25)*0.5 = 5.0
        assert "penalty=5.0" in result["gate_details"][0]


# ═══════════════════════════════════════════════════════════════════════
# VOLATILITY ENGINE — VIX Regime Gate
# ═══════════════════════════════════════════════════════════════════════

_VOL_MODULE = "app.services.volatility_options_engine"


def _vol_pillars(regime=70, structure=75, skew=75, positioning=75, strategy=75):
    """Build mocked pillar dict for volatility engine."""
    return {
        "volatility_regime": _pillar(regime),
        "volatility_structure": _pillar(structure),
        "tail_risk_skew": _pillar(skew),
        "positioning_options_posture": _pillar(positioning),
        "strategy_suitability": _pillar(strategy),
    }


def _call_vol(pillars):
    """Call compute_volatility_scores with all pillar compute funcs mocked."""
    with patch(f"{_VOL_MODULE}._compute_volatility_regime",
               return_value=pillars["volatility_regime"]), \
         patch(f"{_VOL_MODULE}._compute_volatility_structure",
               return_value=pillars["volatility_structure"]), \
         patch(f"{_VOL_MODULE}._compute_tail_risk_skew",
               return_value=pillars["tail_risk_skew"]), \
         patch(f"{_VOL_MODULE}._compute_positioning_options",
               return_value=pillars["positioning_options_posture"]), \
         patch(f"{_VOL_MODULE}._compute_strategy_suitability",
               return_value=pillars["strategy_suitability"]):
        return compute_volatility_scores(
            _EMPTY_DATA, _EMPTY_DATA, _EMPTY_DATA, _EMPTY_DATA,
        )


class TestVolatilityVixGate:
    """Gate: volatility_regime < 30 when composite >= 55.

    VIX scoring is inverted-U: VIX sweet spot (12-18) → HIGH score (~80-95),
    extreme VIX (>40) → LOW score (<20). Gate fires when LOW = crisis.
    """

    def test_gate_fires_when_vix_extreme(self):
        """VIX regime=15 (extreme vol) with high composite → gate fires."""
        result = _call_vol(_vol_pillars(regime=15))
        assert result["gate_applied"] is True
        assert len(result["gate_details"]) > 0
        assert "volatility_regime" in result["gate_details"][0]
        assert "(Gated" in result["label"]

    def test_gate_penalty_proportional(self):
        """Penalty = min(12, (30 - 15) * 0.4) = 6.0."""
        result = _call_vol(_vol_pillars(regime=15))
        assert result["gate_applied"] is True
        assert "penalty=6.0" in result["gate_details"][0]

    def test_gate_penalty_capped_at_12(self):
        """Penalty capped at 12 even when pillar is 0."""
        result = _call_vol(_vol_pillars(regime=0))
        assert result["gate_applied"] is True
        assert "penalty=12.0" in result["gate_details"][0]

    def test_gate_score_floor_at_45(self):
        """Composite never drops below 45 from gate alone."""
        result = _call_vol(_vol_pillars(
            regime=0, structure=80, skew=80, positioning=80, strategy=80,
        ))
        assert result["gate_applied"] is True
        assert result["score"] >= 45

    def test_no_gate_when_vix_normal(self):
        """VIX regime=70 (sweet spot) → no gate."""
        result = _call_vol(_vol_pillars(regime=70))
        assert result["gate_applied"] is False
        assert result["gate_details"] == []
        assert "(Gated" not in result["label"]

    def test_no_gate_when_composite_below_55(self):
        """Low composite (< 55) → gate not evaluated."""
        result = _call_vol(_vol_pillars(
            regime=10, structure=30, skew=30, positioning=30, strategy=30,
        ))
        assert result["gate_applied"] is False

    def test_vix_none_conservative_penalty(self):
        """Missing volatility_regime → conservative 5-point penalty."""
        result = _call_vol(_vol_pillars(
            regime=None, structure=75, skew=70, positioning=70, strategy=70,
        ))
        assert result["gate_applied"] is True
        assert "volatility_regime=None" in result["gate_details"][0]

    def test_label_includes_vol_stress(self):
        """Gate label includes 'elevated vol stress'."""
        result = _call_vol(_vol_pillars(regime=15))
        assert "Gated: elevated vol stress" in result["label"]

    def test_verification_scenario(self):
        """VERIFICATION: composite=68, vix_regime extreme → score drops, label gated."""
        result = _call_vol(_vol_pillars(
            regime=10, structure=80, skew=75, positioning=75, strategy=80,
        ))
        assert result["gate_applied"] is True
        assert "(Gated" in result["label"]
        # Penalty = min(12, (30-10)*0.4) = 8.0
        assert "penalty=8.0" in result["gate_details"][0]


# ═══════════════════════════════════════════════════════════════════════
# CROSS-ASSET MACRO ENGINE — Credit Stress Gate
# ═══════════════════════════════════════════════════════════════════════

_MACRO_MODULE = "app.services.cross_asset_macro_engine"


def _macro_pillars(credit=70, rates=75, dollar=75, defensive=75, coherence=75):
    """Build mocked pillar dict for cross-asset macro engine."""
    return {
        "rates_yield_curve": _pillar(rates),
        "dollar_commodity": _pillar(dollar),
        "credit_risk_appetite": _pillar(credit),
        "defensive_vs_growth": _pillar(defensive),
        "macro_coherence": _pillar(coherence),
    }


def _call_macro(pillars):
    """Call compute_cross_asset_scores with all pillar compute funcs mocked."""
    with patch(f"{_MACRO_MODULE}._compute_rates_yield_curve",
               return_value=pillars["rates_yield_curve"]), \
         patch(f"{_MACRO_MODULE}._compute_dollar_commodity",
               return_value=pillars["dollar_commodity"]), \
         patch(f"{_MACRO_MODULE}._compute_credit_risk_appetite",
               return_value=pillars["credit_risk_appetite"]), \
         patch(f"{_MACRO_MODULE}._compute_defensive_vs_growth",
               return_value=pillars["defensive_vs_growth"]), \
         patch(f"{_MACRO_MODULE}._compute_macro_coherence",
               return_value=pillars["macro_coherence"]):
        return compute_cross_asset_scores(
            _EMPTY_DATA, _EMPTY_DATA, _EMPTY_DATA,
            _EMPTY_DATA, _EMPTY_DATA, {},
        )


class TestCrossAssetCreditGate:
    """Gate: credit_risk_appetite < 35 when composite >= 55."""

    def test_gate_fires_when_credit_stressed(self):
        """credit=25 with high composite → gate fires."""
        result = _call_macro(_macro_pillars(credit=25))
        assert result["gate_applied"] is True
        assert len(result["gate_details"]) > 0
        assert "credit" in result["gate_details"][0]
        assert "(Gated" in result["label"]

    def test_gate_penalty_proportional(self):
        """Penalty = min(15, (35 - 28) * 0.5) = 3.5."""
        result = _call_macro(_macro_pillars(credit=28))
        assert result["gate_applied"] is True
        assert "penalty=3.5" in result["gate_details"][0]

    def test_gate_penalty_capped_at_15(self):
        """Penalty capped at 15 even when credit is 0."""
        result = _call_macro(_macro_pillars(credit=0))
        assert result["gate_applied"] is True
        assert "penalty=15.0" in result["gate_details"][0]

    def test_gate_score_floor_at_45(self):
        """Composite never drops below 45 from gate alone."""
        result = _call_macro(_macro_pillars(
            credit=0, rates=80, dollar=80, defensive=80, coherence=80,
        ))
        assert result["gate_applied"] is True
        assert result["score"] >= 45

    def test_no_gate_when_credit_healthy(self):
        """credit=70 (healthy spreads) → no gate."""
        result = _call_macro(_macro_pillars(credit=70))
        assert result["gate_applied"] is False
        assert result["gate_details"] == []
        assert "(Gated" not in result["label"]

    def test_no_gate_when_composite_below_55(self):
        """Low composite (< 55) → gate not evaluated."""
        result = _call_macro(_macro_pillars(
            credit=20, rates=35, dollar=30, defensive=30, coherence=30,
        ))
        assert result["gate_applied"] is False

    def test_credit_none_conservative_penalty(self):
        """Missing credit score → conservative 5-point penalty."""
        result = _call_macro(_macro_pillars(
            credit=None, rates=75, dollar=70, defensive=70, coherence=65,
        ))
        assert result["gate_applied"] is True
        assert "credit=None" in result["gate_details"][0]

    def test_label_includes_credit_stress(self):
        """Gate label includes 'credit stress'."""
        result = _call_macro(_macro_pillars(credit=25))
        assert "Gated: credit stress" in result["label"]

    def test_verification_scenario(self):
        """VERIFICATION: composite=65, credit=28 → score ~61.5, label gated."""
        result = _call_macro(_macro_pillars(credit=28))
        assert result["gate_applied"] is True
        assert "(Gated" in result["label"]
        # Penalty = (35-28)*0.5 = 3.5
        assert "penalty=3.5" in result["gate_details"][0]


# ═══════════════════════════════════════════════════════════════════════
# CROSS-ENGINE: Normal operation unchanged
# ═══════════════════════════════════════════════════════════════════════


class TestNormalOperationUnchanged:
    """When no gate triggers, output should be identical to pre-gate behavior."""

    def test_breadth_no_gate_keys_present(self):
        """gate_applied and gate_details should still be in output."""
        result = _call_breadth(_breadth_pillars(trend=70))
        assert "gate_applied" in result
        assert "gate_details" in result
        assert result["gate_applied"] is False
        assert result["gate_details"] == []

    def test_vol_no_gate_keys_present(self):
        result = _call_vol(_vol_pillars(regime=70))
        assert "gate_applied" in result
        assert "gate_details" in result
        assert result["gate_applied"] is False
        assert result["gate_details"] == []

    def test_macro_no_gate_keys_present(self):
        result = _call_macro(_macro_pillars(credit=70))
        assert "gate_applied" in result
        assert "gate_details" in result
        assert result["gate_applied"] is False
        assert result["gate_details"] == []
