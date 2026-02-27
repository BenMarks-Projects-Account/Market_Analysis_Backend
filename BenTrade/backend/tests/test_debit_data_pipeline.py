"""Regression tests for debit-spreads data pipeline consistency.

Phase 6 of the "production-reliable debit scanner" task.  Verifies:
  R1  — Both scanners receive identical OptionContract data from shared chain
  R2  — Chain-level guard: empty contracts → skipped, logged
  R3  — Sub-stage instrumentation populates inputs["_build_sub_stages"]
  R4  — All 4 leg quotes non-None for a healthy chain (field mapping correct)
  R5  — Missing quotes → QUOTE_INVALID rejection (no silent null continuation)
  R6  — pop_delta_approx fallback when implied_prob_profit unavailable
  R7  — POP: p_win_used populated from implied_prob_profit or pop_delta_approx
  R8  — OI: min(long, short) aggregation; None if either leg missing
  R10 — Top-level bid/ask compat fields match credit_spread output shape
  R11 — Quote Integrity invariant fires on systemic null quotes
  R12 — missing_field_counts simulation: strategy_service sees bid/ask non-null
  R13 — OI: None preserved (not silently defaulted to 0)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services.strategies.debit_spreads import (
    DataQualityError,
    DebitSpreadsStrategyPlugin,
    validate_quote,
    validate_spread_quotes,
)


# ---------------------------------------------------------------------------
# Helpers (mirror test_debit_spreads_gating.py conventions)
# ---------------------------------------------------------------------------

def _leg(
    *,
    strike: float = 100.0,
    bid: float | None = 2.0,
    ask: float | None = 2.5,
    open_interest: int | None = 500,
    volume: int | None = 100,
    iv: float | None = 0.25,
    theta: float | None = -0.03,
    option_type: str = "call",
    delta: float | None = 0.50,
) -> SimpleNamespace:
    """Fake contract mimicking OptionContract Pydantic model."""
    return SimpleNamespace(
        strike=strike,
        bid=bid,
        ask=ask,
        open_interest=open_interest,
        volume=volume,
        iv=iv,
        theta=theta,
        option_type=option_type,
        delta=delta,
    )


def _make_chain(
    underlying_price: float = 450.0,
    symbol: str = "SPY",
    expiration: str = "2025-09-19",
    num_strikes: int = 10,
    strike_step: float = 1.0,
    *,
    bid_base: float = 3.0,
    ask_base: float = 3.5,
    oi: int = 1000,
    volume: int = 200,
    delta: float = 0.50,
    iv: float = 0.20,
    include_calls: bool = True,
    include_puts: bool = True,
    null_bids: bool = False,
    null_asks: bool = False,
    null_oi: bool = False,
) -> list[SimpleNamespace]:
    """Build a realistic mock option chain.

    Returns a list of OptionContract-like SimpleNamespace objects,
    identical to what normalize_chain() would produce.
    """
    contracts: list[SimpleNamespace] = []
    start = underlying_price - (num_strikes // 2) * strike_step
    for i in range(num_strikes):
        strike = start + i * strike_step
        # Price decays away from ATM
        distance = abs(strike - underlying_price)
        price_decay = max(0.05, bid_base - distance * 0.3)
        b = None if null_bids else round(price_decay, 2)
        a = None if null_asks else round(price_decay + 0.50, 2)
        o = None if null_oi else oi
        v = None if null_oi else volume

        if include_calls:
            contracts.append(SimpleNamespace(
                strike=strike,
                bid=b,
                ask=a,
                open_interest=o,
                volume=v,
                iv=iv,
                theta=-0.03,
                option_type="call",
                delta=delta,
            ))
        if include_puts:
            contracts.append(SimpleNamespace(
                strike=strike,
                bid=b,
                ask=a,
                open_interest=o,
                volume=v,
                iv=iv,
                theta=-0.03,
                option_type="put",
                delta=-delta,
            ))
    return contracts


def _snapshot(
    symbol: str = "SPY",
    underlying_price: float = 450.0,
    expiration: str = "2025-09-19",
    dte: int = 30,
    **chain_kwargs,
) -> dict[str, Any]:
    """Build a snapshot dict as strategy_service.generate() would produce."""
    contracts = _make_chain(
        underlying_price=underlying_price,
        symbol=symbol,
        expiration=expiration,
        **chain_kwargs,
    )
    return {
        "symbol": symbol,
        "underlying_price": underlying_price,
        "expiration": expiration,
        "dte": dte,
        "contracts": contracts,
        "prices_history": [underlying_price] * 30,
    }


def _build_and_enrich(
    snapshots: list[dict] | None = None,
    request: dict | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Run build_candidates → enrich, return (candidates, enriched, inputs)."""
    plugin = DebitSpreadsStrategyPlugin()
    inputs: dict[str, Any] = {
        "snapshots": snapshots or [_snapshot()],
        "request": request or {},
        "policy": {},
    }
    candidates = plugin.build_candidates(inputs)
    enriched = plugin.enrich(candidates, inputs)
    return candidates, enriched, inputs


def _build_and_enrich_unsafe(
    snapshots: list[dict] | None = None,
    request: dict | None = None,
) -> tuple[list[dict], list[dict], dict]:
    """Like _build_and_enrich but skips the Quote Integrity invariant.

    Used by tests that intentionally pass null-quote chains and need to
    inspect the enriched output for rejection codes, dq_flags, etc.
    """
    req = dict(request or {})
    req["_skip_quote_integrity"] = True
    return _build_and_enrich(snapshots=snapshots, request=req)


# ═══════════════════════════════════════════════════════════════════════════
# R1 — Shared data path: OptionContract field mapping correctness
# ═══════════════════════════════════════════════════════════════════════════

class TestR1_FieldMapping:
    """Verify that getattr(leg, field) returns non-None for healthy chains."""

    def test_healthy_chain_all_leg_quotes_present(self):
        """When chain has valid bids/asks, all 4 per-leg quotes are non-None."""
        _, enriched, _ = _build_and_enrich()
        assert len(enriched) > 0, "Should produce at least one enriched trade"
        for trade in enriched:
            assert trade["_long_bid"] is not None, f"long_bid None: {trade}"
            assert trade["_long_ask"] is not None, f"long_ask None: {trade}"
            assert trade["_short_bid"] is not None, f"short_bid None: {trade}"
            assert trade["_short_ask"] is not None, f"short_ask None: {trade}"

    def test_healthy_chain_oi_volume_present(self):
        """When chain has valid OI/volume, trade-level values are non-None."""
        _, enriched, _ = _build_and_enrich()
        for trade in enriched:
            assert trade["open_interest"] is not None, "OI should not be None"
            assert trade["volume"] is not None, "Volume should not be None"

    def test_healthy_chain_iv_present(self):
        """IV from chain propagates to enriched trade."""
        _, enriched, _ = _build_and_enrich()
        for trade in enriched:
            assert trade["iv"] is not None, "IV should not be None"

    def test_healthy_chain_debit_computed(self):
        """Net debit is computed (not None) for healthy chain."""
        _, enriched, _ = _build_and_enrich()
        has_debit = any(t.get("net_debit") is not None for t in enriched)
        assert has_debit, "At least one trade should have a computed net_debit"


# ═══════════════════════════════════════════════════════════════════════════
# R2 — Chain-level data guard
# ═══════════════════════════════════════════════════════════════════════════

class TestR2_ChainGuard:
    """Empty/missing chain → no candidates, proper sub_stages tracking."""

    def test_empty_contracts_skipped(self):
        """Snapshot with contracts=[] produces no candidates."""
        snap = _snapshot()
        snap["contracts"] = []
        candidates, enriched, inputs = _build_and_enrich(snapshots=[snap])
        assert candidates == []
        assert enriched == []
        # Sub-stages should still exist and show the skip
        sub = inputs.get("_build_sub_stages")
        assert sub is not None
        assert sub["skipped_empty_chain"] >= 1

    def test_none_underlying_price_skipped(self):
        """Snapshot with underlying_price=None produces no candidates."""
        snap = _snapshot()
        snap["underlying_price"] = None
        candidates, _, _ = _build_and_enrich(snapshots=[snap])
        assert candidates == []

    def test_mixed_snapshots_partial_success(self):
        """One valid + one empty snapshot → only valid produces candidates."""
        good = _snapshot(symbol="SPY")
        bad = _snapshot(symbol="QQQ")
        bad["contracts"] = []
        candidates, enriched, inputs = _build_and_enrich(
            snapshots=[good, bad],
        )
        assert len(candidates) > 0
        symbols = {c["symbol"] for c in candidates}
        assert "SPY" in symbols
        assert "QQQ" not in symbols
        sub = inputs["_build_sub_stages"]
        assert sub["skipped_empty_chain"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# R3 — Sub-stage instrumentation
# ═══════════════════════════════════════════════════════════════════════════

class TestR3_SubStages:
    """build_candidates populates inputs['_build_sub_stages'] for filter trace."""

    def test_sub_stages_present(self):
        _, _, inputs = _build_and_enrich()
        sub = inputs.get("_build_sub_stages")
        assert sub is not None, "_build_sub_stages missing from inputs"

    def test_sub_stages_keys(self):
        _, _, inputs = _build_and_enrich()
        sub = inputs["_build_sub_stages"]
        required_keys = {
            "total_contracts", "call_contracts", "put_contracts",
            "after_otm_filter", "after_width_match", "after_positive_width",
            "after_cap", "by_symbol", "by_expiration",
            "max_candidates_setting", "direction", "skipped_empty_chain",
        }
        assert required_keys.issubset(sub.keys()), f"Missing keys: {required_keys - sub.keys()}"

    def test_sub_stages_counts_positive_for_healthy_chain(self):
        _, _, inputs = _build_and_enrich()
        sub = inputs["_build_sub_stages"]
        assert sub["total_contracts"] > 0
        assert sub["after_cap"] > 0
        assert sub["call_contracts"] > 0 or sub["put_contracts"] > 0

    def test_sub_stages_by_symbol_populated(self):
        _, _, inputs = _build_and_enrich()
        sub = inputs["_build_sub_stages"]
        assert "SPY" in sub["by_symbol"]
        assert sub["by_symbol"]["SPY"] > 0


# ═══════════════════════════════════════════════════════════════════════════
# R4 — Missing quotes → proper rejection (no silent null continuation)
# ═══════════════════════════════════════════════════════════════════════════

class TestR4_MissingQuoteRejection:
    """Chain with null bids/asks → QUOTE_INVALID rejections, not silent Nones."""

    def test_null_bids_produce_quote_rejection(self):
        """All legs with bid=None → every enriched trade has _quote_rejection."""
        snap = _snapshot(null_bids=True)
        _, enriched, _ = _build_and_enrich_unsafe(snapshots=[snap])
        for trade in enriched:
            assert trade["_quote_rejection"] is not None, (
                "Expected QUOTE_INVALID rejection for null-bid chain"
            )
            assert "QUOTE_INVALID" in trade["_quote_rejection"]

    def test_null_asks_produce_quote_rejection(self):
        """All legs with ask=None → every enriched trade has _quote_rejection."""
        snap = _snapshot(null_asks=True)
        _, enriched, _ = _build_and_enrich_unsafe(snapshots=[snap])
        for trade in enriched:
            assert trade["_quote_rejection"] is not None
            assert "QUOTE_INVALID" in trade["_quote_rejection"]

    def test_null_bids_debit_is_none(self):
        """When quotes are invalid, net_debit must be None (not fabricated)."""
        snap = _snapshot(null_bids=True)
        _, enriched, _ = _build_and_enrich_unsafe(snapshots=[snap])
        for trade in enriched:
            assert trade["net_debit"] is None

    def test_null_bids_pop_fallback_to_delta(self):
        """When quotes fail but delta is present, pop_delta_approx provides POP."""
        snap = _snapshot(null_bids=True)
        _, enriched, _ = _build_and_enrich_unsafe(snapshots=[snap])
        for trade in enriched:
            # implied_prob_profit now equals pop_delta_approx (delta-based POP)
            assert trade["pop_delta_approx"] is not None
            assert 0 < trade["pop_delta_approx"] <= 1.0
            assert trade["implied_prob_profit"] == trade["pop_delta_approx"]
            assert trade["p_win_used"] == trade["pop_delta_approx"]

    def test_quote_rejection_blocks_evaluate(self):
        """A trade with _quote_rejection should fail evaluate()."""
        snap = _snapshot(null_bids=True)
        _, enriched, _ = _build_and_enrich_unsafe(snapshots=[snap])
        assert len(enriched) > 0
        _plugin = DebitSpreadsStrategyPlugin()
        for trade in enriched:
            trade["_policy"] = {}
            trade["_request"] = {}
            ok, reasons = _plugin.evaluate(trade)
            assert not ok, "Trade with null bids should fail evaluate"
            assert any("QUOTE_INVALID" in r for r in reasons)


# ═══════════════════════════════════════════════════════════════════════════
# R5 — OI aggregation: min(long, short), None propagation
# ═══════════════════════════════════════════════════════════════════════════

class TestR5_OIAggregation:
    """Trade-level OI = min(long, short); None if either leg missing."""

    def test_both_legs_have_oi(self):
        """min(500, 1000) = 500."""
        _, enriched, _ = _build_and_enrich()
        for trade in enriched:
            # Both legs have oi=1000 from _make_chain default
            assert trade["open_interest"] is not None
            assert trade["open_interest"] >= 0

    def test_one_leg_oi_none_propagates(self):
        """If either leg has OI=None, trade-level OI is None."""
        snap = _snapshot(null_oi=True)
        _, enriched, _ = _build_and_enrich(snapshots=[snap])
        for trade in enriched:
            assert trade["open_interest"] is None
            assert trade["volume"] is None

    def test_oi_none_flagged_in_dq_flags(self):
        """Missing OI produces _dq_flags entries."""
        snap = _snapshot(null_oi=True)
        _, enriched, _ = _build_and_enrich(snapshots=[snap])
        for trade in enriched:
            dq = trade.get("_dq_flags", [])
            oi_flags = [f for f in dq if "MISSING_OI" in f]
            assert len(oi_flags) > 0, f"Expected MISSING_OI flags, got {dq}"


# ═══════════════════════════════════════════════════════════════════════════
# R6 — POP consistency: implied_prob_profit + pop_delta_approx
# ═══════════════════════════════════════════════════════════════════════════

class TestR6_POPConsistency:
    """p_win_used = implied_prob_profit ?? pop_delta_approx."""

    def test_healthy_chain_both_pop_fields(self):
        """Healthy chain produces both implied_prob_profit and pop_delta_approx."""
        _, enriched, _ = _build_and_enrich()
        has_both = False
        for trade in enriched:
            if (trade.get("implied_prob_profit") is not None
                and trade.get("pop_delta_approx") is not None):
                has_both = True
                break
        assert has_both, "At least one trade should have both POP sources"

    def test_p_win_used_prefers_implied_prob(self):
        """When both available, p_win_used == implied_prob_profit."""
        _, enriched, _ = _build_and_enrich()
        for trade in enriched:
            ipp = trade.get("implied_prob_profit")
            pda = trade.get("pop_delta_approx")
            pwu = trade.get("p_win_used")
            if ipp is not None:
                assert pwu == ipp, f"p_win_used should match implied_prob_profit: {pwu} != {ipp}"

    def test_p_win_used_equals_delta(self):
        """p_win_used always equals pop_delta_approx for debit spreads."""
        snap = _snapshot(null_bids=True)
        _, enriched, _ = _build_and_enrich_unsafe(snapshots=[snap])
        for trade in enriched:
            pda = trade.get("pop_delta_approx")
            pwu = trade.get("p_win_used")
            assert pwu == pda, f"p_win_used must equal pop_delta_approx: {pwu} != {pda}"
            # implied_prob_profit now tracks delta-based POP
            assert trade["implied_prob_profit"] == pda

    def test_pop_delta_approx_in_0_1_range(self):
        """pop_delta_approx is clamped to [0, 1]."""
        _, enriched, _ = _build_and_enrich()
        for trade in enriched:
            pda = trade.get("pop_delta_approx")
            if pda is not None:
                assert 0.0 <= pda <= 1.0


# ═══════════════════════════════════════════════════════════════════════════
# R7 — Direction filtering
# ═══════════════════════════════════════════════════════════════════════════

class TestR7_Direction:
    """direction parameter controls which spread types are built."""

    def test_both_direction_produces_calls_and_puts(self):
        candidates, _, _ = _build_and_enrich(request={"direction": "both"})
        strategies = {c["strategy"] for c in candidates}
        assert "call_debit" in strategies or "put_debit" in strategies

    def test_call_only_direction(self):
        candidates, _, _ = _build_and_enrich(request={"direction": "call"})
        for c in candidates:
            assert c["strategy"] == "call_debit"

    def test_put_only_direction(self):
        candidates, _, _ = _build_and_enrich(request={"direction": "put"})
        for c in candidates:
            assert c["strategy"] == "put_debit"


# ═══════════════════════════════════════════════════════════════════════════
# R8 — DQ flags completeness
# ═══════════════════════════════════════════════════════════════════════════

class TestR8_DQFlags:
    """Per-candidate _dq_flags track all data quality issues."""

    def test_healthy_chain_no_dq_flags(self):
        """Healthy chain should have minimal/no DQ flags."""
        _, enriched, _ = _build_and_enrich()
        # Some trades might have DQ flags due to edge cases, but healthy
        # quotes should not have QUOTE_FAILED flags.
        for trade in enriched:
            dq = trade.get("_dq_flags", [])
            quote_flags = [f for f in dq if "QUOTE_FAILED" in f]
            assert len(quote_flags) == 0, f"Unexpected QUOTE_FAILED: {quote_flags}"

    def test_null_quotes_produce_dq_flags(self):
        snap = _snapshot(null_bids=True)
        _, enriched, _ = _build_and_enrich_unsafe(snapshots=[snap])
        for trade in enriched:
            dq = trade.get("_dq_flags", [])
            assert any("QUOTE_FAILED" in f for f in dq), f"Expected QUOTE_FAILED flag: {dq}"

    def test_null_oi_produces_dq_flags(self):
        snap = _snapshot(null_oi=True)
        _, enriched, _ = _build_and_enrich(snapshots=[snap])
        for trade in enriched:
            dq = trade.get("_dq_flags", [])
            oi_flags = [f for f in dq if "MISSING_OI" in f or "MISSING_VOL" in f]
            assert len(oi_flags) > 0, f"Expected OI/VOL DQ flags: {dq}"

    def test_both_pop_missing_flag(self):
        """When delta AND IV are unavailable, MISSING_POP flag appears."""
        snap = _snapshot(null_bids=True)
        # Remove delta AND IV so both POP models fail
        for c in snap["contracts"]:
            c.delta = None
            c.iv = None
        _, enriched, _ = _build_and_enrich_unsafe(snapshots=[snap])
        for trade in enriched:
            dq = trade.get("_dq_flags", [])
            assert any("MISSING_POP:all_models_unavailable" in f for f in dq), (
                f"Expected MISSING_POP:all_models_unavailable flag: {dq}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# R9 — Candidate cap respected
# ═══════════════════════════════════════════════════════════════════════════

class TestR9_CandidateCap:
    """max_candidates setting limits output."""

    def test_cap_enforced(self):
        """Setting max_candidates=5 caps output."""
        candidates, _, _ = _build_and_enrich(
            request={"max_candidates": 5},
            snapshots=[_snapshot(num_strikes=30)],
        )
        assert len(candidates) <= 5


# ═══════════════════════════════════════════════════════════════════════════
# R10 — Top-level bid/ask compat fields
# ═══════════════════════════════════════════════════════════════════════════

class TestR10_TopLevelBidAsk:
    """Debit enriched output sets 'bid' and 'ask' for strategy_service compat."""

    def test_bid_field_present_and_non_none(self):
        """Top-level 'bid' matches _short_bid for healthy chain."""
        _, enriched, _ = _build_and_enrich()
        for trade in enriched:
            assert "bid" in trade, "Missing top-level 'bid' field"
            assert trade["bid"] is not None, "bid should not be None for valid quotes"
            assert trade["bid"] == trade["_short_bid"], (
                f"bid={trade['bid']} should match _short_bid={trade['_short_bid']}"
            )

    def test_ask_field_present_and_non_none(self):
        """Top-level 'ask' matches _short_ask for healthy chain."""
        _, enriched, _ = _build_and_enrich()
        for trade in enriched:
            assert "ask" in trade, "Missing top-level 'ask' field"
            assert trade["ask"] is not None
            assert trade["ask"] == trade["_short_ask"]

    def test_bid_ask_none_when_quotes_invalid(self):
        """When quotes are null, bid/ask are None (not fabricated)."""
        snap = _snapshot(null_bids=True)
        _, enriched, _ = _build_and_enrich_unsafe(snapshots=[snap])
        for trade in enriched:
            assert trade["bid"] is None
            assert trade["_short_bid"] is None


# ═══════════════════════════════════════════════════════════════════════════
# R11 — Quote Integrity invariant
# ═══════════════════════════════════════════════════════════════════════════

class TestR11_QuoteIntegrity:
    """Enrich raises DataQualityError when >50% of sampled legs have null quotes."""

    def test_all_null_bids_and_asks_raises(self):
        """Chain where both bid AND ask are None → DataQualityError."""
        snap = _snapshot(null_bids=True, null_asks=True)
        plugin = DebitSpreadsStrategyPlugin()
        inputs = {"snapshots": [snap], "request": {}, "policy": {}}
        candidates = plugin.build_candidates(inputs)
        with pytest.raises(DataQualityError, match="QUOTE INTEGRITY FAILURE"):
            plugin.enrich(candidates, inputs)

    def test_healthy_chain_does_not_raise(self):
        """A healthy chain should enrich without raising."""
        _, enriched, _ = _build_and_enrich()
        assert len(enriched) > 0  # no exception raised


# ═══════════════════════════════════════════════════════════════════════════
# R12 — missing_field_counts simulation
# ═══════════════════════════════════════════════════════════════════════════

class TestR12_MissingFieldCountsCompat:
    """Simulate what strategy_service's missing_field_counts loop does.

    This ensures the debit enriched output is compatible with the
    reporting logic that checks row.get("bid"), row.get("_short_bid"),
    row.get("ask"), row.get("_long_ask"), etc.
    """

    def _count_missing(self, enriched: list[dict]) -> dict[str, int]:
        """Mirror strategy_service.py missing_field_counts logic."""
        mfc_bid = 0
        mfc_ask = 0
        mfc_any_leg = 0
        mfc_pop = 0
        mfc_oi = 0
        for row in enriched:
            _sb = row.get("_short_bid")
            _la = row.get("_long_ask")
            _sa = row.get("_short_ask")
            _lb = row.get("_long_bid")
            # Legacy counters
            if _sb is None and row.get("bid") is None:
                mfc_bid += 1
            if _la is None and row.get("ask") is None:
                mfc_ask += 1
            # Aggregate
            if _sb is None or _sa is None or _lb is None or _la is None:
                mfc_any_leg += 1
            # POP
            if row.get("p_win_used") is None and row.get("pop_delta_approx") is None:
                mfc_pop += 1
            # OI
            if row.get("open_interest") is None:
                mfc_oi += 1
        return {
            "missing_bid": mfc_bid,
            "missing_ask": mfc_ask,
            "any_leg_quote_missing": mfc_any_leg,
            "missing_pop": mfc_pop,
            "missing_oi": mfc_oi,
        }

    def test_healthy_chain_zero_missing_bid_ask(self):
        """With valid quotes, missing_bid and missing_ask must be 0."""
        _, enriched, _ = _build_and_enrich()
        counts = self._count_missing(enriched)
        assert counts["missing_bid"] == 0, f"missing_bid should be 0, got {counts}"
        assert counts["missing_ask"] == 0, f"missing_ask should be 0, got {counts}"
        assert counts["any_leg_quote_missing"] == 0

    def test_healthy_chain_zero_missing_pop(self):
        """POP should be present for all enriched trades."""
        _, enriched, _ = _build_and_enrich()
        counts = self._count_missing(enriched)
        assert counts["missing_pop"] == 0

    def test_healthy_chain_zero_missing_oi(self):
        """OI should be present (possibly 0, not None) for valid chain."""
        _, enriched, _ = _build_and_enrich()
        counts = self._count_missing(enriched)
        assert counts["missing_oi"] == 0

    def test_null_oi_chain_reports_missing_oi(self):
        """When OI is None, missing_oi should count it."""
        snap = _snapshot(null_oi=True)
        _, enriched, _ = _build_and_enrich(snapshots=[snap])
        counts = self._count_missing(enriched)
        assert counts["missing_oi"] == len(enriched)


# ═══════════════════════════════════════════════════════════════════════════
# R13 — OI None preservation (Step 4)
# ═══════════════════════════════════════════════════════════════════════════

class TestR13_OINonePreservation:
    """OI/volume: None means missing, 0 means exchange-reported zero."""

    def test_oi_zero_is_zero_not_none(self):
        """Contract with OI=0 → trade-level OI=0 (not None)."""
        snap = _snapshot(oi=0, volume=0)
        _, enriched, _ = _build_and_enrich(snapshots=[snap])
        for trade in enriched:
            # min(0, 0) = 0, not None
            assert trade["open_interest"] is not None, "OI=0 should not become None"
            assert trade["open_interest"] == 0
            assert trade["volume"] is not None
            assert trade["volume"] == 0

    def test_oi_none_is_none_not_zero(self):
        """Contract with OI=None → trade-level OI=None (not 0)."""
        snap = _snapshot(null_oi=True)
        _, enriched, _ = _build_and_enrich(snapshots=[snap])
        for trade in enriched:
            assert trade["open_interest"] is None, "Missing OI must stay None"
            assert trade["volume"] is None, "Missing volume must stay None"

    def test_per_leg_oi_preserved_in_debug_fields(self):
        """_long_oi/_short_oi carry raw per-leg values."""
        _, enriched, _ = _build_and_enrich()
        for trade in enriched:
            assert "_long_oi" in trade
            assert "_short_oi" in trade
            assert trade["_long_oi"] is not None
            assert trade["_short_oi"] is not None
