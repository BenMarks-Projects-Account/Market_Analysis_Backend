"""V2 trust hygiene layer tests — Prompt 9.

Tests for:
1. Quote sanity — negative bid/ask, spread pricing impossible, wide leg spread.
2. Liquidity sanity — dead leg, low OI, low volume, wide composite spread.
3. Duplicate suppression — exact duplicates, keeper selection, diagnostics.
4. Phase D2 integration — end-to-end pipeline with hygiene layer.
5. Reason code registry — all new codes registered.
"""

from __future__ import annotations

import sys
sys.path.insert(0, ".")

import pytest
from copy import deepcopy

from app.services.scanner_v2.contracts import (
    V2Candidate,
    V2CheckResult,
    V2Diagnostics,
    V2Leg,
    V2RecomputedMath,
)
from app.services.scanner_v2.diagnostics.reason_codes import (
    # New hygiene reject codes
    REJECT_NEGATIVE_BID,
    REJECT_NEGATIVE_ASK,
    REJECT_SPREAD_PRICING_IMPOSSIBLE,
    REJECT_DEAD_LEG,
    REJECT_EXACT_DUPLICATE,
    # New hygiene warning codes
    WARN_WIDE_LEG_SPREAD,
    WARN_LOW_OI,
    WARN_LOW_VOLUME,
    WARN_WIDE_COMPOSITE_SPREAD,
    WARN_NEAR_DUPLICATE_SUPPRESSED,
    # New hygiene pass codes
    PASS_QUOTE_SANITY_CLEAN,
    PASS_LIQUIDITY_SANITY_OK,
    PASS_DEDUP_UNIQUE,
    # Registry helpers
    is_valid_reject_code,
    is_valid_warn_code,
    is_valid_pass_code,
    get_code_info,
)
from app.services.scanner_v2.hygiene.quote_sanity import run_quote_sanity
from app.services.scanner_v2.hygiene.liquidity_sanity import run_liquidity_sanity
from app.services.scanner_v2.hygiene.dedup import (
    run_dedup,
    candidate_dedup_key,
    DedupResult,
)
from app.services.scanner_v2.phases import phase_d2_trust_hygiene
from app.services.scanner_v2.migration import execute_v2_scanner
from app.services.scanner_v2.comparison.fixtures import (
    fixture_spy_golden_put_spread,
    fixture_spy_golden_put_debit,
    fixture_spy_bad_liquidity,
    fixture_spy_wide_spreads,
)


# =====================================================================
#  Helpers — build test candidates directly
# =====================================================================

def _make_vertical(
    *,
    short_strike: float = 590.0,
    long_strike: float = 585.0,
    short_bid: float | None = 1.50,
    short_ask: float | None = 1.65,
    long_bid: float | None = 0.65,
    long_ask: float | None = 0.80,
    short_oi: int | None = 5000,
    short_volume: int | None = 800,
    long_oi: int | None = 3000,
    long_volume: int | None = 450,
    strategy_id: str = "put_credit_spread",
    candidate_id: str = "SPY|put_credit_spread|2026-03-20|590/585|0",
    credit: float | None = 0.70,
    debit: float | None = None,
    seq: int = 0,
) -> V2Candidate:
    """Build a minimal V2Candidate for hygiene testing."""
    short_leg = V2Leg(
        index=0, side="short", strike=short_strike,
        option_type="put", expiration="2026-03-20",
        bid=short_bid, ask=short_ask,
        mid=((short_bid + short_ask) / 2) if short_bid is not None and short_ask is not None else None,
        delta=-0.30, open_interest=short_oi, volume=short_volume,
    )
    long_leg = V2Leg(
        index=1, side="long", strike=long_strike,
        option_type="put", expiration="2026-03-20",
        bid=long_bid, ask=long_ask,
        mid=((long_bid + long_ask) / 2) if long_bid is not None and long_ask is not None else None,
        delta=-0.18, open_interest=long_oi, volume=long_volume,
    )
    math = V2RecomputedMath(
        width=abs(short_strike - long_strike),
    )
    if credit is not None:
        math.net_credit = credit
    if debit is not None:
        math.net_debit = debit

    return V2Candidate(
        candidate_id=candidate_id,
        scanner_key=strategy_id,
        strategy_id=strategy_id,
        family_key="vertical_spreads",
        symbol="SPY",
        underlying_price=595.50,
        expiration="2026-03-20",
        dte=9,
        legs=[short_leg, long_leg],
        math=math,
    )


# =====================================================================
#  Section 1 — Quote Sanity
# =====================================================================

class TestQuoteSanity:
    """Quote sanity checks beyond Phase D presence checks."""

    def test_clean_candidate_passes(self):
        """Candidate with clean quotes should pass without issues."""
        cand = _make_vertical()
        result = run_quote_sanity([cand])
        assert not result[0].diagnostics.reject_reasons

    def test_negative_bid_rejected(self):
        """Leg with negative bid should be rejected."""
        cand = _make_vertical(short_bid=-0.50)
        result = run_quote_sanity([cand])
        assert REJECT_NEGATIVE_BID in result[0].diagnostics.reject_reasons

    def test_negative_ask_rejected(self):
        """Leg with negative ask should be rejected."""
        cand = _make_vertical(long_ask=-0.10)
        result = run_quote_sanity([cand])
        assert REJECT_NEGATIVE_ASK in result[0].diagnostics.reject_reasons

    def test_both_negative_bid_and_ask(self):
        """Both negative bid and ask produce both reject codes."""
        cand = _make_vertical(short_bid=-0.50, long_ask=-0.10)
        result = run_quote_sanity([cand])
        reasons = result[0].diagnostics.reject_reasons
        assert REJECT_NEGATIVE_BID in reasons
        assert REJECT_NEGATIVE_ASK in reasons

    def test_spread_pricing_impossible_credit(self):
        """Credit spread where short.bid < long.ask → impossible."""
        # short.bid=0.50, long.ask=0.80 → credit = -0.30 (impossible)
        # But we also set math.net_credit to signal it's a credit spread
        cand = _make_vertical(short_bid=0.50, long_ask=0.80, credit=0.70)
        result = run_quote_sanity([cand])
        assert REJECT_SPREAD_PRICING_IMPOSSIBLE in result[0].diagnostics.reject_reasons

    def test_spread_pricing_impossible_debit(self):
        """Debit spread where long.ask < short.bid → impossible."""
        cand = _make_vertical(
            short_bid=2.00, long_ask=1.50,
            credit=None, debit=0.50,
            strategy_id="put_debit",
            candidate_id="SPY|put_debit|2026-03-20|590/585|0",
        )
        result = run_quote_sanity([cand])
        assert REJECT_SPREAD_PRICING_IMPOSSIBLE in result[0].diagnostics.reject_reasons

    def test_wide_leg_spread_warning(self):
        """Leg with excessively wide bid-ask → warning, not rejection."""
        # short: bid=1.00, ask=4.00 → spread_ratio = 3.0/2.5 = 1.20 > 1.0
        # long:  bid=0.65, ask=0.80 (normal)
        # Still credit-viable: short.bid(1.00) - long.ask(0.80) = 0.20 > 0
        cand = _make_vertical(short_bid=1.00, short_ask=4.00, credit=0.20)
        result = run_quote_sanity([cand])
        # Should warn, not reject (credit spread still viable)
        assert not result[0].diagnostics.reject_reasons
        assert any(
            WARN_WIDE_LEG_SPREAD in item.code
            for item in result[0].diagnostics.items
            if item.kind == "warning"
        )

    def test_wide_leg_spread_custom_threshold(self):
        """Custom wide spread threshold is respected."""
        # bid=1.00, ask=1.80 → ratio = 0.8/1.4 ≈ 0.571
        cand = _make_vertical(short_bid=1.00, short_ask=1.80)
        # Default threshold (1.0) → should pass
        result = run_quote_sanity([cand])
        assert not result[0].diagnostics.reject_reasons

        # Stricter threshold (0.5) → should warn
        cand2 = _make_vertical(short_bid=1.00, short_ask=1.80)
        result2 = run_quote_sanity([cand2], wide_leg_spread_ratio=0.5)
        assert any(
            WARN_WIDE_LEG_SPREAD in item.code
            for item in result2[0].diagnostics.items
            if item.kind == "warning"
        )

    def test_already_rejected_skipped(self):
        """Candidates already rejected by prior phases are skipped."""
        cand = _make_vertical(short_bid=-0.50)
        cand.diagnostics.reject_reasons.append("v2_prior_rejection")
        result = run_quote_sanity([cand])
        # Should not add any new reason codes
        assert result[0].diagnostics.reject_reasons == ["v2_prior_rejection"]

    def test_quote_sanity_diagnostics_items(self):
        """Rejection produces structured diagnostic items."""
        cand = _make_vertical(short_bid=-0.50)
        result = run_quote_sanity([cand])
        reject_items = [
            i for i in result[0].diagnostics.items
            if i.kind == "reject"
        ]
        assert len(reject_items) > 0
        assert reject_items[0].source_phase == "D2"
        assert reject_items[0].code == REJECT_NEGATIVE_BID


# =====================================================================
#  Section 2 — Liquidity Sanity
# =====================================================================

class TestLiquiditySanity:
    """Liquidity sanity checks beyond Phase D presence checks."""

    def test_clean_candidate_passes(self):
        cand = _make_vertical()
        result = run_liquidity_sanity([cand])
        assert not result[0].diagnostics.reject_reasons

    def test_dead_leg_rejected(self):
        """Leg with OI=0 AND volume=0 → rejected."""
        cand = _make_vertical(short_oi=0, short_volume=0)
        result = run_liquidity_sanity([cand])
        assert REJECT_DEAD_LEG in result[0].diagnostics.reject_reasons

    def test_zero_oi_nonzero_volume_not_dead(self):
        """OI=0 but volume>0 → not dead, but low OI warning."""
        cand = _make_vertical(short_oi=0, short_volume=100)
        result = run_liquidity_sanity([cand])
        assert REJECT_DEAD_LEG not in result[0].diagnostics.reject_reasons
        # Should get low OI warning
        assert any(
            WARN_LOW_OI in item.code
            for item in result[0].diagnostics.items
            if item.kind == "warning"
        )

    def test_nonzero_oi_zero_volume_not_dead(self):
        """OI>0 but volume=0 → not dead, but low volume warning."""
        cand = _make_vertical(short_oi=100, short_volume=0)
        result = run_liquidity_sanity([cand])
        assert REJECT_DEAD_LEG not in result[0].diagnostics.reject_reasons
        assert any(
            WARN_LOW_VOLUME in item.code
            for item in result[0].diagnostics.items
            if item.kind == "warning"
        )

    def test_low_oi_warning(self):
        """OI below threshold → warning (not rejection)."""
        cand = _make_vertical(short_oi=5)
        result = run_liquidity_sanity([cand])
        assert not result[0].diagnostics.reject_reasons
        assert any(
            WARN_LOW_OI in item.code
            for item in result[0].diagnostics.items
            if item.kind == "warning"
        )

    def test_low_volume_warning(self):
        """Volume below threshold → warning (not rejection)."""
        cand = _make_vertical(short_volume=2)
        result = run_liquidity_sanity([cand])
        assert not result[0].diagnostics.reject_reasons
        assert any(
            WARN_LOW_VOLUME in item.code
            for item in result[0].diagnostics.items
            if item.kind == "warning"
        )

    def test_wide_composite_spread_warning(self):
        """Excessively wide composite bid-ask spread → warning."""
        # short: bid=0.10, ask=1.00 → spread=0.90, mid=0.55
        # long:  bid=0.10, ask=0.80 → spread=0.70, mid=0.45
        # composite = (0.90+0.70) / (0.55+0.45) = 1.60 / 1.00 = 1.60
        cand = _make_vertical(
            short_bid=0.10, short_ask=1.00,
            long_bid=0.10, long_ask=0.80,
        )
        result = run_liquidity_sanity([cand])
        assert any(
            WARN_WIDE_COMPOSITE_SPREAD in item.code
            for item in result[0].diagnostics.items
            if item.kind == "warning"
        )

    def test_custom_thresholds(self):
        """Custom thresholds are respected."""
        cand = _make_vertical(short_oi=50, short_volume=20)
        # Default thresholds (10/5) → should pass
        result = run_liquidity_sanity([cand])
        assert not any(
            WARN_LOW_OI in item.code
            for item in result[0].diagnostics.items
            if item.kind == "warning"
        )

        # Stricter thresholds (100/50) → should warn
        cand2 = _make_vertical(short_oi=50, short_volume=20)
        result2 = run_liquidity_sanity([cand2], low_oi_warn=100, low_volume_warn=50)
        warn_codes = [i.code for i in result2[0].diagnostics.items if i.kind == "warning"]
        assert WARN_LOW_OI in warn_codes
        assert WARN_LOW_VOLUME in warn_codes

    def test_already_rejected_skipped(self):
        cand = _make_vertical(short_oi=0, short_volume=0)
        cand.diagnostics.reject_reasons.append("v2_prior_rejection")
        result = run_liquidity_sanity([cand])
        assert result[0].diagnostics.reject_reasons == ["v2_prior_rejection"]

    def test_dead_leg_diagnostics_items(self):
        """Dead leg rejection produces structured diagnostic items."""
        cand = _make_vertical(short_oi=0, short_volume=0)
        result = run_liquidity_sanity([cand])
        reject_items = [
            i for i in result[0].diagnostics.items
            if i.kind == "reject"
        ]
        assert len(reject_items) > 0
        assert reject_items[0].source_phase == "D2"
        assert reject_items[0].code == REJECT_DEAD_LEG


# =====================================================================
#  Section 3 — Duplicate Suppression
# =====================================================================

class TestDedupKey:
    """Test the dedup key function."""

    def test_same_candidate_same_key(self):
        c1 = _make_vertical()
        c2 = _make_vertical()
        assert candidate_dedup_key(c1) == candidate_dedup_key(c2)

    def test_different_strikes_different_key(self):
        c1 = _make_vertical(short_strike=590.0, long_strike=585.0)
        c2 = _make_vertical(short_strike=585.0, long_strike=580.0)
        assert candidate_dedup_key(c1) != candidate_dedup_key(c2)

    def test_different_strategy_different_key(self):
        c1 = _make_vertical(strategy_id="put_credit_spread")
        c2 = _make_vertical(strategy_id="put_debit")
        assert candidate_dedup_key(c1) != candidate_dedup_key(c2)

    def test_key_is_hashable(self):
        cand = _make_vertical()
        key = candidate_dedup_key(cand)
        # Must be hashable for use as dict key
        assert isinstance(hash(key), int)


class TestDedupRunner:
    """Test the duplicate suppression runner."""

    def test_no_duplicates(self):
        c1 = _make_vertical(
            short_strike=590.0, long_strike=585.0,
            candidate_id="SPY|pcs|2026-03-20|590/585|0",
        )
        c2 = _make_vertical(
            short_strike=585.0, long_strike=580.0,
            candidate_id="SPY|pcs|2026-03-20|585/580|1",
        )
        result_list, dedup_result = run_dedup([c1, c2])
        assert dedup_result.total_before == 2
        assert dedup_result.total_after == 2
        assert dedup_result.duplicates_suppressed == 0

    def test_exact_duplicate_suppressed(self):
        """Two identical candidates → one suppressed."""
        c1 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|0")
        c2 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|1")
        result_list, dedup_result = run_dedup([c1, c2])
        assert dedup_result.total_before == 2
        assert dedup_result.total_after == 1
        assert dedup_result.duplicates_suppressed == 1

    def test_keeper_has_pass_diagnostic(self):
        """Keeper gets PASS_DEDUP_UNIQUE diagnostic."""
        c1 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|0")
        c2 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|1")
        result_list, _ = run_dedup([c1, c2])
        keepers = [c for c in result_list if not c.diagnostics.reject_reasons]
        assert len(keepers) == 1
        pass_items = [
            i for i in keepers[0].diagnostics.items
            if i.kind == "pass" and PASS_DEDUP_UNIQUE in i.code
        ]
        assert len(pass_items) == 1

    def test_suppressed_has_reject_diagnostic(self):
        """Suppressed duplicate gets REJECT_EXACT_DUPLICATE."""
        c1 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|0")
        c2 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|1")
        result_list, _ = run_dedup([c1, c2])
        suppressed = [
            c for c in result_list
            if REJECT_EXACT_DUPLICATE in c.diagnostics.reject_reasons
        ]
        assert len(suppressed) == 1

    def test_suppressed_diagnostic_mentions_keeper(self):
        """Suppressed diagnostic metadata includes keeper_id."""
        c1 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|0")
        c2 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|1")
        result_list, _ = run_dedup([c1, c2])
        suppressed = [
            c for c in result_list
            if REJECT_EXACT_DUPLICATE in c.diagnostics.reject_reasons
        ]
        assert len(suppressed) == 1
        reject_item = next(
            i for i in suppressed[0].diagnostics.items
            if i.code == REJECT_EXACT_DUPLICATE
        )
        assert "keeper_id" in reject_item.metadata

    def test_deterministic_keeper_selection(self):
        """Keeper selection is deterministic — always picks same one."""
        c1 = _make_vertical(
            candidate_id="SPY|pcs|2026-03-20|590/585|0",
            short_oi=5000,
        )
        c2 = _make_vertical(
            candidate_id="SPY|pcs|2026-03-20|590/585|1",
            short_oi=100,
        )
        _, r1 = run_dedup([c1, c2])
        _, r2 = run_dedup([c2, c1])  # Reversed input order
        assert r1.keeper_ids == r2.keeper_ids

    def test_keeper_prefers_better_liquidity(self):
        """Keeper selection prefers candidate with better liquidity."""
        c1 = _make_vertical(
            candidate_id="SPY|pcs|2026-03-20|590/585|0",
            short_oi=50, short_volume=10,
            long_oi=30, long_volume=5,
        )
        c2 = _make_vertical(
            candidate_id="SPY|pcs|2026-03-20|590/585|1",
            short_oi=5000, short_volume=800,
            long_oi=3000, long_volume=450,
        )
        _, result = run_dedup([c1, c2])
        # c2 has much better liquidity → should be keeper
        assert c2.candidate_id in result.keeper_ids

    def test_already_rejected_pass_through(self):
        """Already-rejected candidates pass through untouched."""
        c1 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|0")
        c1.diagnostics.reject_reasons.append("v2_prior_rejection")
        c2 = _make_vertical(
            short_strike=585.0, long_strike=580.0,
            candidate_id="SPY|pcs|2026-03-20|585/580|1",
        )
        result_list, dedup_result = run_dedup([c1, c2])
        assert dedup_result.total_before == 1  # Only c2 was live
        assert dedup_result.total_after == 1
        # c1 still has its prior rejection
        prior = next(c for c in result_list if c.candidate_id == c1.candidate_id)
        assert "v2_prior_rejection" in prior.diagnostics.reject_reasons

    def test_dedup_result_serializable(self):
        """DedupResult.to_dict() is JSON-serializable."""
        c1 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|0")
        c2 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|1")
        _, result = run_dedup([c1, c2])
        d = result.to_dict()
        assert isinstance(d, dict)
        assert d["duplicates_suppressed"] == 1
        assert d["total_after"] == 1

    def test_three_duplicates_one_keeper(self):
        """Three identical candidates → two suppressed, one keeper."""
        cands = [
            _make_vertical(candidate_id=f"SPY|pcs|2026-03-20|590/585|{i}")
            for i in range(3)
        ]
        result_list, dedup_result = run_dedup(cands)
        assert dedup_result.total_after == 1
        assert dedup_result.duplicates_suppressed == 2


# =====================================================================
#  Section 4 — Phase D2 Integration
# =====================================================================

class TestPhaseD2Integration:
    """Test the combined phase_d2_trust_hygiene function."""

    def test_clean_candidates_pass(self):
        """Clean candidates pass Phase D2 unscathed."""
        c1 = _make_vertical(
            short_strike=590.0, long_strike=585.0,
            candidate_id="SPY|pcs|2026-03-20|590/585|0",
        )
        c2 = _make_vertical(
            short_strike=585.0, long_strike=580.0,
            candidate_id="SPY|pcs|2026-03-20|585/580|1",
        )
        result, summary = phase_d2_trust_hygiene([c1, c2])
        live = [c for c in result if not c.diagnostics.reject_reasons]
        assert len(live) == 2
        assert summary["dedup"]["duplicates_suppressed"] == 0

    def test_bad_quote_rejected_in_d2(self):
        """Negative bid is caught by Phase D2."""
        cand = _make_vertical(short_bid=-1.00)
        result, _ = phase_d2_trust_hygiene([cand])
        assert REJECT_NEGATIVE_BID in result[0].diagnostics.reject_reasons

    def test_dead_leg_rejected_in_d2(self):
        """Dead leg (OI=0, volume=0) is caught by Phase D2."""
        cand = _make_vertical(short_oi=0, short_volume=0)
        result, _ = phase_d2_trust_hygiene([cand])
        assert REJECT_DEAD_LEG in result[0].diagnostics.reject_reasons

    def test_duplicates_suppressed_in_d2(self):
        """Duplicates are suppressed in Phase D2."""
        c1 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|0")
        c2 = _make_vertical(candidate_id="SPY|pcs|2026-03-20|590/585|1")
        result, summary = phase_d2_trust_hygiene([c1, c2])
        live = [c for c in result if not c.diagnostics.reject_reasons]
        assert len(live) == 1
        assert summary["dedup"]["duplicates_suppressed"] == 1

    def test_d2_returns_hygiene_summary(self):
        """Phase D2 returns a hygiene summary dict."""
        cand = _make_vertical()
        _, summary = phase_d2_trust_hygiene([cand])
        assert "dedup" in summary
        assert "total_before" in summary["dedup"]

    def test_d2_with_custom_dedup_key(self):
        """Phase D2 accepts custom dedup key function."""
        # Custom key that ignores strikes: all candidates are "duplicates"
        def always_same_key(cand):
            return ("SPY", "put_credit_spread")

        c1 = _make_vertical(
            short_strike=590.0, long_strike=585.0,
            candidate_id="SPY|pcs|2026-03-20|590/585|0",
        )
        c2 = _make_vertical(
            short_strike=585.0, long_strike=580.0,
            candidate_id="SPY|pcs|2026-03-20|585/580|1",
        )
        result, summary = phase_d2_trust_hygiene(
            [c1, c2], dedup_key_fn=always_same_key,
        )
        assert summary["dedup"]["duplicates_suppressed"] == 1


# =====================================================================
#  Section 5 — End-to-End Pipeline Integration
# =====================================================================

class TestPipelineIntegration:
    """Verify Phase D2 works in the full V2 scanner pipeline."""

    def test_golden_put_credit_still_passes(self):
        """Golden fixture should still produce candidates after D2."""
        snapshot = fixture_spy_golden_put_spread()
        result = execute_v2_scanner(
            "put_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        assert result["accepted_count"] > 0
        # Phase trace should include trust_hygiene
        phases = [p["phase"] for p in result["_v2_scan_result"]["phase_counts"]]
        assert "trust_hygiene" in phases

    def test_golden_put_debit_still_passes(self):
        """Debit spread golden fixture still produces candidates."""
        snapshot = fixture_spy_golden_put_debit()
        result = execute_v2_scanner(
            "put_debit",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        assert result["accepted_count"] > 0
        phases = [p["phase"] for p in result["_v2_scan_result"]["phase_counts"]]
        assert "trust_hygiene" in phases

    def test_phase_d2_appears_in_trace(self):
        """Phase D2 (trust_hygiene) appears in the phase count trace."""
        snapshot = fixture_spy_golden_put_spread()
        result = execute_v2_scanner(
            "put_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        phases = [p["phase"] for p in result["_v2_scan_result"]["phase_counts"]]
        expected_order = [
            "constructed",
            "structural_validation",
            "quote_liquidity_sanity",
            "trust_hygiene",
            "recomputed_math",
            "normalized",
        ]
        assert phases == expected_order

    def test_bad_liquidity_fixture_catches_issues(self):
        """Bad liquidity fixture should have Phase D rejections."""
        snapshot = fixture_spy_bad_liquidity()
        result = execute_v2_scanner(
            "put_credit_spread",
            symbol=snapshot.symbol,
            chain=snapshot.chain,
            underlying_price=snapshot.underlying_price,
        )
        # This fixture has missing OI and zero volume → Phase D catches it
        # (before Phase D2 even runs)
        v2 = result["_v2_scan_result"]
        reject_counts = v2["reject_reason_counts"]
        # Should have data quality rejections
        assert len(reject_counts) > 0


# =====================================================================
#  Section 6 — Reason Code Registry
# =====================================================================

class TestReasonCodeRegistry:
    """Verify all new hygiene codes are properly registered."""

    @pytest.mark.parametrize("code", [
        REJECT_NEGATIVE_BID,
        REJECT_NEGATIVE_ASK,
        REJECT_SPREAD_PRICING_IMPOSSIBLE,
        REJECT_DEAD_LEG,
        REJECT_EXACT_DUPLICATE,
    ])
    def test_reject_codes_registered(self, code):
        assert is_valid_reject_code(code), f"{code} not registered"
        info = get_code_info(code)
        assert info is not None
        assert info.severity == "error"

    @pytest.mark.parametrize("code", [
        WARN_WIDE_LEG_SPREAD,
        WARN_LOW_OI,
        WARN_LOW_VOLUME,
        WARN_WIDE_COMPOSITE_SPREAD,
        WARN_NEAR_DUPLICATE_SUPPRESSED,
    ])
    def test_warn_codes_registered(self, code):
        assert is_valid_warn_code(code), f"{code} not registered"
        info = get_code_info(code)
        assert info is not None
        assert info.severity == "warning"

    @pytest.mark.parametrize("code", [
        PASS_QUOTE_SANITY_CLEAN,
        PASS_LIQUIDITY_SANITY_OK,
        PASS_DEDUP_UNIQUE,
    ])
    def test_pass_codes_registered(self, code):
        assert is_valid_pass_code(code), f"{code} not registered"
        info = get_code_info(code)
        assert info is not None
        assert info.severity == "info"

    def test_new_codes_have_categories(self):
        """All new codes have a non-empty category."""
        new_codes = [
            REJECT_NEGATIVE_BID, REJECT_NEGATIVE_ASK,
            REJECT_SPREAD_PRICING_IMPOSSIBLE, REJECT_DEAD_LEG,
            REJECT_EXACT_DUPLICATE,
            WARN_WIDE_LEG_SPREAD, WARN_LOW_OI, WARN_LOW_VOLUME,
            WARN_WIDE_COMPOSITE_SPREAD,
            PASS_QUOTE_SANITY_CLEAN, PASS_LIQUIDITY_SANITY_OK,
            PASS_DEDUP_UNIQUE,
        ]
        for code in new_codes:
            info = get_code_info(code)
            assert info is not None, f"{code} missing from registry"
            assert info.category, f"{code} has no category"
            assert info.label, f"{code} has no label"
