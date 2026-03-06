"""Tests for select_top_n bypass mode and high-water safety guard.

Note: The pipeline no longer calls select_top_n (soft cap removed).
These tests verify the candidate_sampler module's internal logic.

Covers:
  - select_top_n bypass mode: all candidates pass, cap_summary reports bypass metadata
  - High-water safety guard: clamps at BYPASS_HIGH_WATER_MARK (20k)
  - Cap summary schema with bypassed/bypass_reason/original/effective fields
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.utils.candidate_sampler import (
    BYPASS_HIGH_WATER_MARK,
    compute_pre_score,
    select_top_n,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_contract(
    bid: float = 1.0,
    ask: float = 2.0,
    open_interest: int = 500,
    volume: int = 50,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        bid=bid, ask=ask, open_interest=open_interest, volume=volume,
        strike=100.0, delta=-0.30,
    )


def _credit_spread_candidate(
    short_bid: float = 2.00,
    short_ask: float = 2.20,
    long_bid: float = 0.50,
    long_ask: float = 0.70,
    short_oi: int = 1000,
    long_oi: int = 800,
    short_vol: int = 100,
    long_vol: int = 80,
) -> dict[str, Any]:
    return {
        "short_leg": _make_contract(short_bid, short_ask, short_oi, short_vol),
        "long_leg": _make_contract(long_bid, long_ask, long_oi, long_vol),
        "underlying": "SPY",
        "expiration": "2026-04-01",
        "short_strike": 500,
        "long_strike": 495,
    }


# ===========================================================================
# Tests: select_top_n bypass mode
# ===========================================================================


class TestSelectTopNBypass:
    """Verify bypass_enrichment_cap=True skips the enrichment cap."""

    def test_bypass_keeps_all_candidates(self):
        """When bypass=True, all candidates are kept regardless of n."""
        cands = [_credit_spread_candidate() for _ in range(50)]
        selected, summary = select_top_n(
            cands, n=10, generation_cap=20_000, bypass_enrichment_cap=True,
            bypass_reason="credit_spread_wide_experiment",
        )
        assert len(selected) == 50, (
            f"Expected all 50 candidates, got {len(selected)}"
        )
        assert summary["bypassed"] is True
        assert summary["bypass_enabled"] is True
        assert summary["bypass_reason"] == "credit_spread_wide_experiment"
        assert summary["original_enrichment_cap"] == 10
        assert summary["effective_enrichment_cap"] == 50
        assert summary["kept_for_enrichment"] == 50
        assert summary["discarded_due_to_enrichment_cap"] == 0

    def test_bypass_false_trims_normally(self):
        """When bypass=False (default), normal trimming applies."""
        cands = [_credit_spread_candidate() for _ in range(50)]
        selected, summary = select_top_n(
            cands, n=10, generation_cap=20_000, bypass_enrichment_cap=False,
        )
        assert len(selected) == 10
        assert summary["bypassed"] is False
        assert "bypass_enabled" not in summary
        assert summary["kept_for_enrichment"] == 10
        assert summary["discarded_due_to_enrichment_cap"] == 40

    def test_bypass_default_is_false(self):
        """Default bypass_enrichment_cap is False."""
        cands = [_credit_spread_candidate() for _ in range(20)]
        selected, summary = select_top_n(cands, n=5, generation_cap=20_000)
        assert len(selected) == 5
        assert summary["bypassed"] is False

    def test_bypass_preserves_pre_score_sorting(self):
        """Even in bypass mode, candidates are sorted by pre_score descending."""
        cands = []
        for i in range(10):
            c = _credit_spread_candidate(
                short_bid=float(1 + i * 0.5),
                short_ask=float(1.5 + i * 0.5),
            )
            cands.append(c)
        selected, _ = select_top_n(
            cands, n=5, generation_cap=20_000, bypass_enrichment_cap=True,
        )
        scores = [c["_pre_score"] for c in selected]
        assert scores == sorted(scores, reverse=True), (
            "Candidates must be sorted by pre_score descending"
        )

    def test_bypass_empty_list(self):
        """Bypass with empty candidate list returns empty + bypass metadata."""
        selected, summary = select_top_n(
            [], n=100, generation_cap=20_000, bypass_enrichment_cap=True,
            bypass_reason="credit_spread_wide_experiment",
        )
        assert selected == []
        assert summary["bypassed"] is True
        assert summary["bypass_reason"] == "credit_spread_wide_experiment"
        assert summary["original_enrichment_cap"] == 100
        assert summary["effective_enrichment_cap"] == 0
        assert summary["high_water_clamped"] is False

    def test_bypass_when_under_cap(self):
        """Bypass when candidate count < n still sets bypassed=True."""
        cands = [_credit_spread_candidate() for _ in range(3)]
        selected, summary = select_top_n(
            cands, n=100, generation_cap=20_000, bypass_enrichment_cap=True,
        )
        assert len(selected) == 3
        assert summary["bypassed"] is True
        assert summary["original_enrichment_cap"] == 100
        assert summary["effective_enrichment_cap"] == 3

    def test_bypass_reason_defaults_to_experiment(self):
        """When bypass_reason is not provided, it defaults to 'experiment'."""
        cands = [_credit_spread_candidate() for _ in range(5)]
        _, summary = select_top_n(
            cands, n=2, generation_cap=20_000, bypass_enrichment_cap=True,
        )
        assert summary["bypass_reason"] == "experiment"

    def test_bypass_cap_summary_has_all_standard_fields(self):
        """Bypass mode cap_summary still includes all standard fields."""
        standard_fields = [
            "generation_cap",
            "enrichment_cap",
            "generated_total",
            "generated_after_generation_cap",
            "kept_for_enrichment",
            "cap_reached_generation",
            "cap_reached_enrichment",
            "discarded_due_to_generation_cap",
            "discarded_due_to_enrichment_cap",
            "pre_score_min",
            "pre_score_max",
            "pre_score_median",
            "pre_score_cutoff",
            "penny_count",
            "missing_quote_count",
        ]
        bypass_fields = [
            "bypassed",
            "bypass_enabled",
            "bypass_reason",
            "original_enrichment_cap",
            "effective_enrichment_cap",
            "high_water_clamped",
        ]
        cands = [_credit_spread_candidate() for _ in range(5)]
        _, summary = select_top_n(
            cands, n=2, generation_cap=20_000, bypass_enrichment_cap=True,
        )
        for f in standard_fields + bypass_fields:
            assert f in summary, f"Missing field in bypass cap_summary: {f}"

    def test_non_bypass_has_bypassed_false(self):
        """Non-bypass always has bypassed=False in cap_summary."""
        cands = [_credit_spread_candidate() for _ in range(5)]
        _, summary = select_top_n(cands, n=10, generation_cap=20_000)
        assert summary["bypassed"] is False
        assert "bypass_reason" not in summary


# ===========================================================================
# Tests: High-water safety guard
# ===========================================================================


class TestBypassHighWaterGuard:
    """Verify the BYPASS_HIGH_WATER_MARK clamps candidates."""

    def test_high_water_constant_is_20k(self):
        """BYPASS_HIGH_WATER_MARK should be 20_000."""
        assert BYPASS_HIGH_WATER_MARK == 20_000

    def test_clamp_above_high_water(self):
        """When candidate count > high-water mark, clamp to it."""
        with patch("app.utils.candidate_sampler.BYPASS_HIGH_WATER_MARK", 10):
            cands = [_credit_spread_candidate() for _ in range(15)]
            selected, summary = select_top_n(
                cands, n=5, generation_cap=20_000, bypass_enrichment_cap=True,
            )
            assert len(selected) == 10, (
                f"Expected clamped to 10 (patched high-water), got {len(selected)}"
            )
            assert summary["high_water_clamped"] is True
            assert summary["bypassed"] is True
            assert summary["effective_enrichment_cap"] == 10
            assert summary["original_enrichment_cap"] == 5

    def test_no_clamp_under_high_water(self):
        """When candidate count < high-water mark, no clamping."""
        cands = [_credit_spread_candidate() for _ in range(20)]
        selected, summary = select_top_n(
            cands, n=5, generation_cap=20_000, bypass_enrichment_cap=True,
        )
        assert len(selected) == 20
        assert summary["high_water_clamped"] is False



