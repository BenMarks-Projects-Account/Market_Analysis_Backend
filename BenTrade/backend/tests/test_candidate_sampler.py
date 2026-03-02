"""Tests for app.utils.candidate_sampler — heap-based soft-cap selection.

Covers:
  - extract_leg_contracts: all candidate shapes (IC / credit / debit / calendar / butterfly)
  - compute_pre_score: scoring components, penny penalty, missing quotes
  - select_top_n: heap selection, determinism, cap_summary correctness
  - Edge cases: empty list, n=0, all same score, single candidate
"""

from __future__ import annotations

import math
import sys
import types
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.utils.candidate_sampler import (
    _safe_float,
    compute_pre_score,
    extract_leg_contracts,
    select_top_n,
)


# ---------------------------------------------------------------------------
# Helpers — mock contract objects
# ---------------------------------------------------------------------------

def _make_contract(
    bid: float | None = 1.0,
    ask: float | None = 2.0,
    open_interest: int | None = 500,
    volume: int | None = 50,
    strike: float = 100.0,
    delta: float | None = -0.30,
) -> types.SimpleNamespace:
    """Build a lightweight contract-like object."""
    return types.SimpleNamespace(
        bid=bid,
        ask=ask,
        open_interest=open_interest,
        volume=volume,
        strike=strike,
        delta=delta,
    )


def _credit_spread_candidate(
    short_bid: float = 2.00,
    short_ask: float = 2.20,
    long_bid: float = 0.50,
    long_ask: float = 0.70,
    short_oi: int = 1000,
    long_oi: int = 800,
    short_vol: int = 100,
    long_vol: int = 60,
) -> dict[str, Any]:
    """Build a credit-spread candidate dict (short_leg + long_leg shape)."""
    return {
        "strategy": "put_credit_spread",
        "short_leg": _make_contract(bid=short_bid, ask=short_ask, open_interest=short_oi, volume=short_vol),
        "long_leg": _make_contract(bid=long_bid, ask=long_ask, open_interest=long_oi, volume=long_vol),
        "width": 5.0,
        "snapshot": {},
    }


def _ic_candidate(
    bids: tuple[float, ...] = (0.50, 1.80, 1.90, 0.40),
    asks: tuple[float, ...] = (0.70, 2.00, 2.10, 0.60),
    ois: tuple[int, ...] = (500, 1000, 1000, 500),
    vols: tuple[int, ...] = (30, 80, 80, 30),
) -> dict[str, Any]:
    """Build an iron-condor candidate dict (legs[] with _contract shape)."""
    names = ["long_put", "short_put", "short_call", "long_call"]
    legs = []
    for i, name in enumerate(names):
        legs.append({
            "name": name,
            "right": "put" if i < 2 else "call",
            "side": "buy" if i in (0, 3) else "sell",
            "strike": 100.0 + i * 5,
            "qty": 1,
            "_contract": _make_contract(
                bid=bids[i], ask=asks[i],
                open_interest=ois[i], volume=vols[i],
            ),
        })
    return {
        "strategy": "iron_condor",
        "legs": legs,
        "underlying_price": 110.0,
        "snapshot": {},
    }


def _calendar_candidate(
    near_bid: float = 1.00,
    near_ask: float = 1.30,
    far_bid: float = 2.50,
    far_ask: float = 2.90,
    near_oi: int = 600,
    far_oi: int = 400,
) -> dict[str, Any]:
    """Build a calendar spread candidate dict (near_leg + far_leg shape)."""
    return {
        "strategy": "calendar_spread",
        "near_leg": _make_contract(bid=near_bid, ask=near_ask, open_interest=near_oi, volume=50),
        "far_leg": _make_contract(bid=far_bid, ask=far_ask, open_interest=far_oi, volume=30),
        "strike": 100.0,
        "underlying_price": 100.0,
    }


# ===========================================================================
# _safe_float
# ===========================================================================

class TestSafeFloat:
    def test_none(self):
        assert _safe_float(None) is None

    def test_normal_float(self):
        assert _safe_float(3.14) == 3.14

    def test_int(self):
        assert _safe_float(42) == 42.0

    def test_string_number(self):
        assert _safe_float("1.5") == 1.5

    def test_bad_string(self):
        assert _safe_float("abc") is None

    def test_nan(self):
        assert _safe_float(float("nan")) is None

    def test_inf(self):
        assert _safe_float(float("inf")) is None


# ===========================================================================
# extract_leg_contracts
# ===========================================================================

class TestExtractLegContracts:
    def test_ic_legs(self):
        cand = _ic_candidate()
        contracts = extract_leg_contracts(cand)
        assert len(contracts) == 4
        # All should be SimpleNamespace with bid attribute
        for c in contracts:
            assert hasattr(c, "bid")

    def test_credit_spread_legs(self):
        cand = _credit_spread_candidate()
        contracts = extract_leg_contracts(cand)
        assert len(contracts) == 2
        assert contracts[0].bid == 2.00  # short_leg
        assert contracts[1].bid == 0.50  # long_leg

    def test_calendar_legs(self):
        cand = _calendar_candidate()
        contracts = extract_leg_contracts(cand)
        assert len(contracts) == 2
        assert contracts[0].bid == 1.00  # near_leg
        assert contracts[1].bid == 2.50  # far_leg

    def test_empty_candidate(self):
        contracts = extract_leg_contracts({})
        assert contracts == []

    def test_legs_preferred_over_named_fields(self):
        """When both legs[] and short_leg/long_leg exist, legs[] wins."""
        cand = _ic_candidate()
        cand["short_leg"] = _make_contract(bid=99.0)
        contracts = extract_leg_contracts(cand)
        assert len(contracts) == 4  # from legs[], not short_leg


# ===========================================================================
# compute_pre_score
# ===========================================================================

class TestComputePreScore:
    def test_good_candidate_positive_score(self):
        cand = _credit_spread_candidate(
            short_bid=2.00, short_ask=2.20,
            long_bid=0.50, long_ask=0.70,
            short_oi=1000, long_oi=800,
            short_vol=100, long_vol=60,
        )
        score = compute_pre_score(cand)
        assert score > 0, "Good candidate should have positive pre_score"

    def test_penny_wing_penalized(self):
        """A candidate with penny-priced legs should score lower."""
        good = _credit_spread_candidate(
            short_bid=2.00, short_ask=2.20,
            long_bid=0.50, long_ask=0.70,
        )
        penny = _credit_spread_candidate(
            short_bid=0.01, short_ask=0.03,
            long_bid=0.01, long_ask=0.02,
        )
        assert compute_pre_score(good) > compute_pre_score(penny)

    def test_missing_quotes_penalized(self):
        """Legs with None bid/ask should lower the score."""
        good = _credit_spread_candidate()
        missing = _credit_spread_candidate()
        missing["short_leg"] = _make_contract(bid=None, ask=None)
        assert compute_pre_score(good) > compute_pre_score(missing)

    def test_higher_liquidity_scores_better(self):
        """More OI/volume should improve the score."""
        low_liq = _credit_spread_candidate(short_oi=10, long_oi=10, short_vol=1, long_vol=1)
        high_liq = _credit_spread_candidate(short_oi=5000, long_oi=5000, short_vol=500, long_vol=500)
        assert compute_pre_score(high_liq) > compute_pre_score(low_liq)

    def test_tight_spread_scores_better(self):
        """Tighter bid-ask spread → higher score."""
        tight = _credit_spread_candidate(short_bid=2.00, short_ask=2.05, long_bid=0.50, long_ask=0.55)
        wide = _credit_spread_candidate(short_bid=1.00, short_ask=3.00, long_bid=0.10, long_ask=1.50)
        assert compute_pre_score(tight) > compute_pre_score(wide)

    def test_empty_candidate_zero(self):
        assert compute_pre_score({}) == 0.0

    def test_ic_candidate_scores(self):
        """Iron condor candidates should produce valid scores."""
        cand = _ic_candidate()
        score = compute_pre_score(cand)
        assert isinstance(score, float)
        assert score > 0

    def test_calendar_candidate_scores(self):
        """Calendar spread candidates should produce valid scores."""
        cand = _calendar_candidate()
        score = compute_pre_score(cand)
        assert isinstance(score, float)
        assert score > 0

    def test_all_zero_bid_heavily_penalized(self):
        """All legs with bid=0 should be heavily penalized (penny wings)."""
        cand = _credit_spread_candidate(short_bid=0.0, long_bid=0.0)
        score = compute_pre_score(cand)
        # With 2 penny legs at 0.5 penalty each, should be negative
        assert score < 0

    def test_pre_score_stored_on_candidate(self):
        """select_top_n stores _pre_score on each candidate."""
        cand = _credit_spread_candidate()
        compute_pre_score(cand)
        # compute_pre_score itself doesn't store it; select_top_n does
        # Just verify the score is deterministic
        s1 = compute_pre_score(cand)
        s2 = compute_pre_score(cand)
        assert s1 == s2


# ===========================================================================
# select_top_n — heap selection
# ===========================================================================

class TestSelectTopN:
    def test_no_cap_needed(self):
        """When total <= n, all candidates are returned."""
        cands = [_credit_spread_candidate() for _ in range(5)]
        selected, summary = select_top_n(cands, 10)
        assert len(selected) == 5
        assert summary["cap_reached_enrichment"] is False
        assert summary["discarded_due_to_enrichment_cap"] == 0

    def test_cap_applied(self):
        """When total > n, only top n are kept."""
        cands = [
            _credit_spread_candidate(short_oi=i * 100, long_oi=i * 100)
            for i in range(1, 21)  # 20 candidates
        ]
        selected, summary = select_top_n(cands, 5)
        assert len(selected) == 5
        assert summary["cap_reached_enrichment"] is True
        assert summary["discarded_due_to_enrichment_cap"] == 15
        assert summary["generated_total"] == 20
        assert summary["kept_for_enrichment"] == 5

    def test_best_candidates_kept(self):
        """The cap should keep the highest-scoring candidates."""
        # Make one clearly best candidate
        good = _credit_spread_candidate(short_oi=10000, long_oi=10000, short_vol=1000, long_vol=1000)
        bad_list = [
            _credit_spread_candidate(short_bid=0.01, short_ask=0.03, long_bid=0.01, long_ask=0.02, short_oi=1, long_oi=1, short_vol=0, long_vol=0)
            for _ in range(10)
        ]
        selected, _ = select_top_n([good] + bad_list, 1)
        assert len(selected) == 1
        # The good candidate should be the one kept
        assert selected[0]["short_leg"].open_interest == 10000

    def test_empty_list(self):
        selected, summary = select_top_n([], 10)
        assert selected == []
        assert summary["cap_reached_enrichment"] is False
        assert summary["generated_total"] == 0

    def test_n_equals_total(self):
        """When n == total, no cap should be applied."""
        cands = [_credit_spread_candidate() for _ in range(5)]
        selected, summary = select_top_n(cands, 5)
        assert len(selected) == 5
        assert summary["cap_reached_enrichment"] is False

    def test_cap_summary_scores(self):
        """Summary should contain min/max/median pre_scores."""
        cands = [
            _credit_spread_candidate(short_oi=i * 100, long_oi=i * 100)
            for i in range(1, 11)
        ]
        _, summary = select_top_n(cands, 5)
        assert summary["pre_score_min"] is not None
        assert summary["pre_score_max"] is not None
        assert summary["pre_score_median"] is not None
        assert summary["pre_score_min"] <= summary["pre_score_median"] <= summary["pre_score_max"]
        assert summary["pre_score_cutoff"] is not None

    def test_pre_score_attached(self):
        """Each candidate should have _pre_score after selection."""
        cands = [_credit_spread_candidate() for _ in range(3)]
        selected, _ = select_top_n(cands, 10)
        for s in selected:
            assert "_pre_score" in s
            assert isinstance(s["_pre_score"], float)

    def test_determinism(self):
        """Same input should produce same output (stable ordering)."""
        cands = [
            _credit_spread_candidate(short_oi=i * 100, long_oi=i * 100)
            for i in range(1, 11)
        ]
        s1, sum1 = select_top_n(cands, 5)
        # Reset _pre_score to ensure fresh computation
        for c in cands:
            c.pop("_pre_score", None)
        s2, sum2 = select_top_n(cands, 5)
        scores1 = [c["_pre_score"] for c in s1]
        scores2 = [c["_pre_score"] for c in s2]
        assert scores1 == scores2

    def test_cap_summary_penny_count(self):
        """Summary should report penny-priced legs."""
        # 3 candidates with penny wings
        penny_cands = [
            _credit_spread_candidate(short_bid=0.01, short_ask=0.03, long_bid=0.01, long_ask=0.02)
            for _ in range(3)
        ]
        # 2 candidates with good pricing
        good_cands = [_credit_spread_candidate() for _ in range(2)]
        _, summary = select_top_n(penny_cands + good_cands, 10)
        assert summary["penny_count"] > 0

    def test_single_candidate(self):
        """Edge case: exactly one candidate."""
        cands = [_credit_spread_candidate()]
        selected, summary = select_top_n(cands, 1)
        assert len(selected) == 1
        assert summary["cap_reached_enrichment"] is False

    def test_all_same_score(self):
        """When all candidates score identically, cap still works."""
        # Use identical candidates so scores match
        cands = [_credit_spread_candidate() for _ in range(10)]
        selected, summary = select_top_n(cands, 3)
        assert len(selected) == 3
        assert summary["cap_reached_enrichment"] is True

    def test_ic_candidates_selectable(self):
        """Iron condor candidates work with select_top_n."""
        cands = [_ic_candidate() for _ in range(5)]
        selected, summary = select_top_n(cands, 3)
        assert len(selected) == 3
        assert summary["cap_reached_enrichment"] is True

    def test_calendar_candidates_selectable(self):
        """Calendar candidates work with select_top_n."""
        cands = [_calendar_candidate() for _ in range(5)]
        selected, summary = select_top_n(cands, 3)
        assert len(selected) == 3
        assert summary["cap_reached_enrichment"] is True

    def test_mixed_strategy_candidates(self):
        """Heterogeneous candidate shapes should all be scorable."""
        cands = [
            _credit_spread_candidate(),
            _ic_candidate(),
            _calendar_candidate(),
        ]
        selected, summary = select_top_n(cands, 10)
        assert len(selected) == 3
        for c in selected:
            assert "_pre_score" in c

    def test_cap_summary_no_cutoff_when_no_cap(self):
        """When cap is not applied, cutoff should be None."""
        cands = [_credit_spread_candidate() for _ in range(2)]
        _, summary = select_top_n(cands, 10)
        assert summary["pre_score_cutoff"] is None

    def test_missing_quote_count_in_summary(self):
        """Summary should count legs with missing bid/ask."""
        cand_missing = _credit_spread_candidate()
        cand_missing["short_leg"] = _make_contract(bid=None, ask=None)
        cands = [cand_missing, _credit_spread_candidate()]
        _, summary = select_top_n(cands, 10)
        assert summary["missing_quote_count"] >= 1


# ===========================================================================
# Dual-cap architecture tests (generation_cap + enrichment_cap)
# ===========================================================================

class TestDualCapArchitecture:
    """Verify the two-cap schema: generation_cap (safety ceiling) vs
    enrichment_cap (preset max_candidates) are tracked independently."""

    def test_cap_summary_contains_both_caps(self):
        """Cap summary must report both generation_cap and enrichment_cap."""
        cands = [_credit_spread_candidate() for _ in range(5)]
        _, summary = select_top_n(cands, 3, generation_cap=20_000)
        assert "generation_cap" in summary
        assert "enrichment_cap" in summary
        assert summary["generation_cap"] == 20_000
        assert summary["enrichment_cap"] == 3

    def test_enrichment_cap_math(self):
        """discarded_due_to_enrichment_cap == generated_total - kept_for_enrichment."""
        cands = [
            _credit_spread_candidate(short_oi=i * 100, long_oi=i * 100)
            for i in range(1, 21)  # 20 candidates
        ]
        _, summary = select_top_n(cands, 5, generation_cap=20_000)
        assert summary["generated_total"] == 20
        assert summary["kept_for_enrichment"] == 5
        assert summary["discarded_due_to_enrichment_cap"] == 15
        assert (
            summary["discarded_due_to_enrichment_cap"]
            == summary["generated_total"] - summary["kept_for_enrichment"]
        )

    def test_generation_cap_reached_flag(self):
        """cap_reached_generation is True when generated_total >= generation_cap."""
        # Provide exactly 10 candidates with generation_cap=10
        cands = [_credit_spread_candidate() for _ in range(10)]
        _, summary = select_top_n(cands, 20, generation_cap=10)
        assert summary["cap_reached_generation"] is True
        assert summary["cap_reached_enrichment"] is False

    def test_generation_cap_not_reached(self):
        """cap_reached_generation is False when below the ceiling."""
        cands = [_credit_spread_candidate() for _ in range(5)]
        _, summary = select_top_n(cands, 10, generation_cap=20_000)
        assert summary["cap_reached_generation"] is False

    def test_enrichment_cap_not_reached(self):
        """cap_reached_enrichment is False when candidates fit within cap."""
        cands = [_credit_spread_candidate() for _ in range(5)]
        _, summary = select_top_n(cands, 10, generation_cap=20_000)
        assert summary["cap_reached_enrichment"] is False
        assert summary["discarded_due_to_enrichment_cap"] == 0

    def test_both_caps_reached(self):
        """Both caps can be reached simultaneously."""
        # generation_cap=5 (already truncated), enrichment_cap=3
        cands = [_credit_spread_candidate() for _ in range(5)]
        _, summary = select_top_n(cands, 3, generation_cap=5)
        assert summary["cap_reached_generation"] is True
        assert summary["cap_reached_enrichment"] is True
        assert summary["kept_for_enrichment"] == 3
        assert summary["discarded_due_to_enrichment_cap"] == 2

    def test_default_generation_cap(self):
        """Default generation_cap when not specified is 20_000."""
        cands = [_credit_spread_candidate() for _ in range(3)]
        _, summary = select_top_n(cands, 10)
        assert summary["generation_cap"] == 20_000

    def test_generated_after_generation_cap_field(self):
        """generated_after_generation_cap tracks count after safety ceiling."""
        cands = [_credit_spread_candidate() for _ in range(8)]
        _, summary = select_top_n(cands, 5, generation_cap=20_000)
        assert summary["generated_after_generation_cap"] == 8

    def test_enrichment_selects_by_pre_score_not_order(self):
        """Regression: when generated_total > enrichment_cap, the kept
        candidates must be the top-N by pre_score, NOT by generation order.

        This is the core reason for the dual-cap rework: per-plugin hard caps
        previously kept the first-N-generated (arbitrary order), discarding
        potentially better candidates generated later.
        """
        # Build 20 candidates with deliberately varying quality.
        # Put the BEST candidates at the END of the list to prove that
        # selection is by score, not by list position.
        bad_cands = [
            _credit_spread_candidate(
                short_bid=0.01, short_ask=0.03,
                long_bid=0.01, long_ask=0.02,
                short_oi=1, long_oi=1,
                short_vol=0, long_vol=0,
            )
            for _ in range(15)
        ]
        good_cands = [
            _credit_spread_candidate(
                short_bid=3.00, short_ask=3.10,
                long_bid=1.00, long_ask=1.05,
                short_oi=5000, long_oi=5000,
                short_vol=500, long_vol=500,
            )
            for _ in range(5)
        ]
        # Bad candidates first, good candidates at the tail
        all_cands = bad_cands + good_cands

        selected, summary = select_top_n(all_cands, 5, generation_cap=20_000)

        assert summary["cap_reached_enrichment"] is True
        assert summary["kept_for_enrichment"] == 5

        # All 5 selected must be the good ones (high OI = 5000)
        for s in selected:
            assert s["short_leg"].open_interest == 5000, (
                "Enrichment cap must select by pre_score, not generation order"
            )

    def test_cap_summary_schema_completeness(self):
        """All required cap_summary fields must be present."""
        required_fields = [
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
        cands = [_credit_spread_candidate() for _ in range(3)]
        _, summary = select_top_n(cands, 10, generation_cap=20_000)
        for field in required_fields:
            assert field in summary, f"Missing cap_summary field: {field}"

    def test_old_field_names_absent(self):
        """Old cap_summary field names must NOT appear (prevent regressions)."""
        old_fields = [
            "cap_applied",
            "dropped_count",
            "total_before_cap",
            "total_after_cap",
            "max_candidates",
        ]
        cands = [_credit_spread_candidate() for _ in range(5)]
        _, summary = select_top_n(cands, 3, generation_cap=20_000)
        for field in old_fields:
            assert field not in summary, (
                f"Old field '{field}' found in cap_summary — should be removed"
            )
