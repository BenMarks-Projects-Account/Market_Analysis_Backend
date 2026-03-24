"""Tests for FN-5: composite rank score within scanner-key budgets.

Verifies:
  - _compute_candidate_rank() bridges candidate dicts to ranking.py properly
  - Capital efficiency (EV/max_loss) is the primary ranking factor
  - $5-wide with better EV/risk outranks $50-wide with higher raw EV
  - High-POP trades rank above low-POP when EV/risk is similar
  - rank_score is present in candidate output after sorting
"""

from __future__ import annotations

import pytest

from app.workflows.options_opportunity_runner import _compute_candidate_rank


def _make_candidate(
    ev: float = 5.0,
    max_loss: float = 400.0,
    ror: float = 0.0125,
    pop: float = 0.75,
    open_interest: int = 500,
    volume: int = 100,
    symbol: str = "SPY",
    scanner_key: str = "put_credit_spread",
) -> dict:
    return {
        "symbol": symbol,
        "scanner_key": scanner_key,
        "family_key": "verticals",
        "math": {
            "ev": ev,
            "max_loss": max_loss,
            "ror": ror,
            "pop": pop,
        },
        "legs": [
            {"open_interest": open_interest, "volume": volume, "strike": 100},
        ],
    }


class TestComputeCandidateRank:
    """Test _compute_candidate_rank bridge function."""

    def test_returns_float(self):
        cand = _make_candidate()
        score = _compute_candidate_rank(cand)
        assert isinstance(score, float)

    def test_score_range_0_to_100(self):
        cand = _make_candidate()
        score = _compute_candidate_rank(cand)
        assert 0.0 <= score <= 100.0

    def test_none_math_returns_zero(self):
        cand = {"math": None, "legs": []}
        score = _compute_candidate_rank(cand)
        assert score == 0.0

    def test_empty_candidate(self):
        score = _compute_candidate_rank({})
        assert score == 0.0


class TestCapitalEfficiencyRanking:
    """$5-wide with better EV/risk must outrank $50-wide with higher raw EV."""

    def test_narrow_spread_beats_wide_spread(self):
        # $5-wide: EV=$5, max_loss=$400 → EV/risk = 1.25%
        narrow = _make_candidate(ev=5.0, max_loss=400.0, ror=0.0125, pop=0.75)
        # $50-wide: EV=$8, max_loss=$4200 → EV/risk = 0.19%
        wide = _make_candidate(ev=8.0, max_loss=4200.0, ror=0.0019, pop=0.65)

        narrow_score = _compute_candidate_rank(narrow)
        wide_score = _compute_candidate_rank(wide)

        assert narrow_score > wide_score, (
            f"$5-wide (EV/risk=1.25%) should rank above $50-wide (EV/risk=0.19%): "
            f"narrow={narrow_score:.4f} vs wide={wide_score:.4f}"
        )

    def test_high_pop_beats_low_pop_same_ev_risk(self):
        # Same EV/risk, different POP
        high_pop = _make_candidate(ev=5.0, max_loss=400.0, pop=0.85)
        low_pop = _make_candidate(ev=5.0, max_loss=400.0, pop=0.60)

        high_score = _compute_candidate_rank(high_pop)
        low_score = _compute_candidate_rank(low_pop)

        assert high_score > low_score, (
            f"High POP (0.85) should rank above low POP (0.60): "
            f"high={high_score:.4f} vs low={low_score:.4f}"
        )

    def test_high_ror_beats_low_ror_same_pop(self):
        # Same POP, different RoR
        high_ror = _make_candidate(ev=10.0, max_loss=400.0, ror=0.25, pop=0.75)
        low_ror = _make_candidate(ev=3.0, max_loss=400.0, ror=0.075, pop=0.75)

        high_score = _compute_candidate_rank(high_ror)
        low_score = _compute_candidate_rank(low_ror)

        assert high_score > low_score

    def test_liquidity_matters(self):
        # Same math, different liquidity
        liquid = _make_candidate(open_interest=5000, volume=2000)
        illiquid = _make_candidate(open_interest=10, volume=2)

        liquid_score = _compute_candidate_rank(liquid)
        illiquid_score = _compute_candidate_rank(illiquid)

        assert liquid_score > illiquid_score


class TestRankScoreInOutput:
    """Verify rank_score is set on candidates during sorting."""

    def test_rank_score_assigned_by_sorting(self):
        """Simulate per-key bucket sort and confirm rank_score is injected."""
        candidates = [
            _make_candidate(ev=8.0, max_loss=4200.0, ror=0.0019, pop=0.65),
            _make_candidate(ev=5.0, max_loss=400.0, ror=0.0125, pop=0.80),
            _make_candidate(ev=3.0, max_loss=200.0, ror=0.015, pop=0.85),
        ]

        # Replicate the sorting logic from the runner
        for c in candidates:
            c["rank_score"] = round(_compute_candidate_rank(c), 4)
        candidates.sort(key=lambda c: (-c.get("rank_score", 0), c.get("symbol", "")))

        # All should have rank_score
        for c in candidates:
            assert "rank_score" in c
            assert isinstance(c["rank_score"], float)

        # Best capital efficiency should be first
        # $3 EV / $200 max_loss (1.5%) and high POP should be top
        assert candidates[0]["math"]["max_loss"] == 200.0
