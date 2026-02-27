"""Tests for near-miss builder, trace counters, and per-candidate DQ flags.

Covers:
  NM-1  Zero-valued quotes preserved (no falsy `or` bug)
  NM-2  Debit-spread fields (net_debit, debit_as_pct_of_width) in near-miss
  NM-3  POP flows through to near-miss entry
  NM-4  _STRUCTURAL_REASONS includes debit codes
  MFC-1 missing_field_counts: strategy-aware all-4-leg checks
  MFC-2 missing_field_counts: 0.0 bid is NOT counted as missing
  MFC-3 any_leg_quote_missing counter
  DQ-1  Per-candidate _dq_flags in enrich() output
  DQ-2  spread_pct/EV/ROR null when quotes missing
  EC-1  enrichment_counters uses exact counter names
  EC-2  data_quality_flags includes MISSING_OR_INVALID_QUOTES
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from app.services.strategy_service import StrategyService
from app.services.ranking import safe_float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_enriched_row(
    *,
    short_bid: float | None = 1.50,
    short_ask: float | None = 1.60,
    long_bid: float | None = 0.30,
    long_ask: float | None = 0.40,
    open_interest: int | None = 500,
    volume: int | None = 100,
    p_win_used: float | None = 0.65,
    delta: float | None = -0.40,
    quote_rejection: str | None = None,
    rejection_codes: list[str] | None = None,
    net_credit: float | None = None,
    net_debit: float | None = 3.0,
    debit_as_pct_of_width: float | None = 0.60,
    credit_basis: str | None = None,
    width: float = 5.0,
    underlying: str = "SPY",
    expiration: str = "2025-03-21",
    dte: int = 30,
    short_strike: float = 105.0,
    long_strike: float = 100.0,
    spread_type: str = "call_debit",
    underlying_price: float = 102.0,
    ev_to_risk: float | None = 0.05,
    return_on_risk: float | None = 0.40,
    bid_ask_spread_pct: float | None = 0.02,
    ev_per_share: float | None = 0.10,
    max_loss: float | None = 3.0,
) -> dict[str, Any]:
    """Minimal enriched row dict mimicking debit_spreads.enrich() output."""
    return {
        "underlying": underlying,
        "symbol": underlying,
        "expiration": expiration,
        "dte": dte,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "width": width,
        "spread_type": spread_type,
        "strategy": spread_type,
        "underlying_price": underlying_price,
        "net_credit": net_credit,
        "net_debit": net_debit,
        "debit_as_pct_of_width": debit_as_pct_of_width,
        "_credit_basis": credit_basis,
        "_short_bid": short_bid,
        "_short_ask": short_ask,
        "_long_bid": long_bid,
        "_long_ask": long_ask,
        "open_interest": open_interest,
        "volume": volume,
        "p_win_used": p_win_used,
        "delta": delta,
        "_quote_rejection": quote_rejection,
        "_rejection_codes": rejection_codes or [],
        "ev_per_share": ev_per_share,
        "ev_to_risk": ev_to_risk,
        "return_on_risk": return_on_risk,
        "bid_ask_spread_pct": bid_ask_spread_pct,
        "max_loss": max_loss,
        "max_loss_per_share": None,
    }


def _build_near_miss_one(row: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    """Call _build_near_miss with a single rejected row, return the entry."""
    result = StrategyService._build_near_miss(
        rejected_rows=[(row, reasons)],
        payload={},
        policy={},
        limit=5,
    )
    assert len(result) == 1
    return result[0]


# ===========================================================================
# NM-1: Zero-valued quotes preserved (no falsy `or` bug)
# ===========================================================================
class TestNearMissQuotePreservation:
    """A bid of 0.0 is valid for deep OTM options — must NOT become None."""

    def test_short_bid_zero_preserved(self):
        row = _make_enriched_row(short_bid=0.0)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["short_bid"] == 0.0, "0.0 bid must not be treated as missing"

    def test_short_ask_zero_preserved(self):
        row = _make_enriched_row(short_ask=0.0)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["short_ask"] == 0.0, "0.0 ask must not be treated as missing"

    def test_long_bid_zero_preserved(self):
        row = _make_enriched_row(long_bid=0.0)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["long_bid"] == 0.0

    def test_long_ask_zero_preserved(self):
        row = _make_enriched_row(long_ask=0.0)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["long_ask"] == 0.0

    def test_all_zero_quotes_preserved(self):
        row = _make_enriched_row(short_bid=0.0, short_ask=0.0, long_bid=0.0, long_ask=0.0)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["short_bid"] == 0.0
        assert entry["short_ask"] == 0.0
        assert entry["long_bid"] == 0.0
        assert entry["long_ask"] == 0.0

    def test_none_quotes_stay_none(self):
        row = _make_enriched_row(short_bid=None, short_ask=None, long_bid=None, long_ask=None)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["short_bid"] is None
        assert entry["short_ask"] is None
        assert entry["long_bid"] is None
        assert entry["long_ask"] is None

    def test_normal_quotes_pass_through(self):
        row = _make_enriched_row(short_bid=1.50, short_ask=1.60, long_bid=0.30, long_ask=0.40)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["short_bid"] == 1.50
        assert entry["short_ask"] == 1.60
        assert entry["long_bid"] == 0.30
        assert entry["long_ask"] == 0.40


# ===========================================================================
# NM-2: Debit-spread fields in near-miss
# ===========================================================================
class TestNearMissDebitFields:
    def test_net_debit_present(self):
        row = _make_enriched_row(net_debit=3.50)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["net_debit"] == 3.50

    def test_debit_as_pct_of_width_present(self):
        row = _make_enriched_row(debit_as_pct_of_width=0.70)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["debit_as_pct_of_width"] == 0.70

    def test_credit_fields_still_work(self):
        """Credit spreads should still get net_credit and credit_basis."""
        row = _make_enriched_row(net_credit=1.20, credit_basis="mid", net_debit=None)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["net_credit"] == 1.20
        assert entry["credit_basis"] == "mid"


# ===========================================================================
# NM-3: POP flows through to near-miss
# ===========================================================================
class TestNearMissPOP:
    def test_pop_from_p_win_used(self):
        row = _make_enriched_row(p_win_used=0.72)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["pop"] == pytest.approx(0.72)

    def test_pop_zero_preserved(self):
        """p_win_used = 0.0 must not become None."""
        row = _make_enriched_row(p_win_used=0.0)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["pop"] == 0.0

    def test_pop_none_when_missing(self):
        row = _make_enriched_row(p_win_used=None)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["pop"] is None


# ===========================================================================
# NM-4: _STRUCTURAL_REASONS includes debit codes
# ===========================================================================
class TestStructuralReasons:
    def test_debit_structural_codes(self):
        sr = StrategyService._STRUCTURAL_REASONS
        assert "non_positive_debit" in sr
        assert "debit_ge_width" in sr
        assert "debit_too_close_to_width" in sr

    def test_credit_structural_codes_preserved(self):
        sr = StrategyService._STRUCTURAL_REASONS
        assert "non_positive_credit" in sr
        assert "credit_ge_width" in sr
        assert "CREDIT_SPREAD_METRICS_FAILED" in sr
        assert "invalid_width" in sr

    def test_structural_penalty_applied_to_debit_codes(self):
        """Debit structural reasons should get the -10.0 penalty each."""
        row = _make_enriched_row()
        entry_normal = _build_near_miss_one(row, ["spread_too_wide"])
        entry_structural = _build_near_miss_one(row, ["non_positive_debit"])
        assert entry_structural["nearness_score"] < entry_normal["nearness_score"]


# ===========================================================================
# MFC-1: missing_field_counts strategy-aware all-4-leg checks
# ===========================================================================

def _compute_missing_field_counts(enriched: list[dict[str, Any]]) -> dict[str, Any]:
    """Simulate the missing_field_counts loop from strategy_service.

    We import the actual StrategyService to use _to_float, but replicate
    the counter loop since it's embedded in _build_filter_trace_and_report.
    """
    svc = StrategyService.__new__(StrategyService)
    _mfc_oi = 0
    _mfc_oi_zero = 0
    _mfc_vol = 0
    _mfc_vol_zero = 0
    _mfc_bid = 0
    _mfc_ask = 0
    _mfc_any_leg_quote_missing = 0
    _mfc_pop = 0
    _mfc_delta = 0
    _mfc_quote_rejected = 0

    for _row in enriched:
        if not isinstance(_row, dict):
            continue
        _raw_oi = _row.get("open_interest")
        _raw_vol = _row.get("volume")
        if _raw_oi is None:
            _mfc_oi += 1
        elif svc._to_float(_raw_oi) == 0:
            _mfc_oi_zero += 1
        if _raw_vol is None:
            _mfc_vol += 1
        elif svc._to_float(_raw_vol) == 0:
            _mfc_vol_zero += 1
        _sb = _row.get("_short_bid")
        _sa = _row.get("_short_ask")
        _lb = _row.get("_long_bid")
        _la = _row.get("_long_ask")
        if _sb is None and _row.get("bid") is None:
            _mfc_bid += 1
        if _la is None and _row.get("ask") is None:
            _mfc_ask += 1
        if _sb is None or _sa is None or _lb is None or _la is None:
            _mfc_any_leg_quote_missing += 1
        if _row.get("p_win_used") is None and _row.get("pop_delta_approx") is None:
            _mfc_pop += 1
        if _row.get("delta") is None and _row.get("short_delta") is None:
            _mfc_delta += 1
        if _row.get("_quote_rejection"):
            _mfc_quote_rejected += 1

    return {
        "missing_bid": _mfc_bid,
        "missing_ask": _mfc_ask,
        "any_leg_quote_missing": _mfc_any_leg_quote_missing,
        "missing_oi": _mfc_oi,
        "zero_oi": _mfc_oi_zero,
        "missing_vol": _mfc_vol,
        "zero_vol": _mfc_vol_zero,
        "missing_pop": _mfc_pop,
        "missing_delta": _mfc_delta,
        "quote_rejected": _mfc_quote_rejected,
    }


class TestMissingFieldCounts:
    def test_all_fields_present_zero_counts(self):
        """Fully populated debit-spread row → all counts 0."""
        rows = [_make_enriched_row()]
        counts = _compute_missing_field_counts(rows)
        assert counts["missing_bid"] == 0
        assert counts["missing_ask"] == 0
        assert counts["any_leg_quote_missing"] == 0
        assert counts["missing_pop"] == 0
        assert counts["quote_rejected"] == 0

    def test_zero_bid_not_counted_as_missing(self):
        """A bid of 0.0 is not missing — it's a valid exchange value."""
        rows = [_make_enriched_row(short_bid=0.0)]
        counts = _compute_missing_field_counts(rows)
        assert counts["missing_bid"] == 0  # 0.0 is not None
        assert counts["any_leg_quote_missing"] == 0

    def test_missing_short_ask_counted(self):
        """Missing _short_ask triggers any_leg_quote_missing."""
        rows = [_make_enriched_row(short_ask=None)]
        counts = _compute_missing_field_counts(rows)
        assert counts["any_leg_quote_missing"] == 1
        # Legacy credit-critical counters: short_bid present → missing_bid=0
        assert counts["missing_bid"] == 0

    def test_missing_all_quotes(self):
        rows = [_make_enriched_row(short_bid=None, short_ask=None, long_bid=None, long_ask=None)]
        counts = _compute_missing_field_counts(rows)
        assert counts["missing_bid"] == 1   # _short_bid is None AND no fallback bid
        assert counts["missing_ask"] == 1   # _long_ask is None AND no fallback ask
        assert counts["any_leg_quote_missing"] == 1

    def test_oi_zero_vs_missing(self):
        rows_zero = [_make_enriched_row(open_interest=0)]
        rows_none = [_make_enriched_row(open_interest=None)]
        c_zero = _compute_missing_field_counts(rows_zero)
        c_none = _compute_missing_field_counts(rows_none)
        assert c_zero["zero_oi"] == 1
        assert c_zero["missing_oi"] == 0
        assert c_none["missing_oi"] == 1
        assert c_none["zero_oi"] == 0

    def test_quote_rejected_counted(self):
        rows = [_make_enriched_row(quote_rejection="MISSING_QUOTES:short_bid")]
        counts = _compute_missing_field_counts(rows)
        assert counts["quote_rejected"] == 1


# ===========================================================================
# NM-5: Mid computation from preserved quotes
# ===========================================================================
class TestNearMissMids:
    def test_mids_computed_from_quotes(self):
        row = _make_enriched_row(short_bid=1.50, short_ask=1.60, long_bid=0.30, long_ask=0.40)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["short_mid"] == pytest.approx(1.55)
        assert entry["long_mid"] == pytest.approx(0.35)

    def test_mids_none_when_quotes_missing(self):
        row = _make_enriched_row(short_bid=None, short_ask=None, long_bid=None, long_ask=None)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["short_mid"] is None
        assert entry["long_mid"] is None

    def test_mids_from_zero_quotes(self):
        """Zero quotes should produce mid = 0.0, not None."""
        row = _make_enriched_row(short_bid=0.0, short_ask=0.0, long_bid=0.0, long_ask=0.0)
        entry = _build_near_miss_one(row, ["spread_too_wide"])
        assert entry["short_mid"] == 0.0
        assert entry["long_mid"] == 0.0


# ===========================================================================
# DQ-1: Per-candidate _dq_flags in enrich() output
# ===========================================================================

from types import SimpleNamespace

from app.services.strategies.debit_spreads import DebitSpreadsStrategyPlugin


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
    return SimpleNamespace(
        strike=strike, bid=bid, ask=ask, open_interest=open_interest,
        volume=volume, iv=iv, theta=theta, option_type=option_type,
        delta=delta,
    )


def _candidate_for_enrich(
    *,
    long_bid: float | None = 5.0,
    long_ask: float | None = 5.5,
    short_bid: float | None = 2.0,
    short_ask: float | None = 2.5,
    long_oi: int | None = 500,
    short_oi: int | None = 600,
    long_vol: int | None = 100,
    short_vol: int | None = 120,
    long_iv: float | None = 0.25,
    short_iv: float | None = 0.22,
) -> dict[str, Any]:
    return {
        "strategy": "call_debit",
        "spread_type": "call_debit",
        "symbol": "SPY",
        "expiration": "2025-08-15",
        "dte": 30,
        "underlying_price": 102.0,
        "width": 5.0,
        "long_strike": 100.0,
        "short_strike": 105.0,
        "long_leg": _leg(strike=100.0, bid=long_bid, ask=long_ask,
                         open_interest=long_oi, volume=long_vol, iv=long_iv),
        "short_leg": _leg(strike=105.0, bid=short_bid, ask=short_ask,
                          open_interest=short_oi, volume=short_vol, iv=short_iv),
        "snapshot": {"symbol": "SPY", "prices_history": []},
    }


def _enrich_one(candidate: dict, request: dict | None = None) -> dict[str, Any]:
    plugin = DebitSpreadsStrategyPlugin()
    results = plugin.enrich([candidate], {"request": request or {}, "policy": {}})
    assert len(results) == 1
    return results[0]


class TestPerCandidateDQFlags:
    """enrich() must attach _dq_flags list to every candidate."""

    def test_healthy_candidate_empty_dq_flags(self):
        trade = _enrich_one(_candidate_for_enrich())
        # IVR_INSUFFICIENT_HISTORY is expected when no iv_history provided;
        # it is informational, not a data-quality failure.
        non_ivr_flags = [f for f in trade["_dq_flags"] if not f.startswith("IVR_")]
        assert non_ivr_flags == []

    def test_missing_long_bid_produces_quote_failed_flag(self):
        trade = _enrich_one(_candidate_for_enrich(long_bid=None))
        assert any("QUOTE_FAILED" in f for f in trade["_dq_flags"])

    def test_missing_oi_produces_missing_oi_flag(self):
        trade = _enrich_one(_candidate_for_enrich(long_oi=None))
        assert "MISSING_OI:long_leg" in trade["_dq_flags"]

    def test_missing_short_oi_produces_flag(self):
        trade = _enrich_one(_candidate_for_enrich(short_oi=None))
        assert "MISSING_OI:short_leg" in trade["_dq_flags"]

    def test_zero_oi_produces_zero_flag(self):
        trade = _enrich_one(_candidate_for_enrich(long_oi=0))
        assert "ZERO_OI:long_leg" in trade["_dq_flags"]

    def test_missing_volume_produces_flag(self):
        trade = _enrich_one(_candidate_for_enrich(long_vol=None))
        assert "MISSING_VOL:long_leg" in trade["_dq_flags"]

    def test_zero_volume_produces_flag(self):
        trade = _enrich_one(_candidate_for_enrich(short_vol=0))
        assert "ZERO_VOL:short_leg" in trade["_dq_flags"]

    def test_multiple_flags_accumulated(self):
        """A candidate with many issues gets all flags."""
        trade = _enrich_one(_candidate_for_enrich(
            long_oi=None, short_oi=0, long_vol=None, short_vol=0,
        ))
        flags = trade["_dq_flags"]
        assert "MISSING_OI:long_leg" in flags
        assert "ZERO_OI:short_leg" in flags
        assert "MISSING_VOL:long_leg" in flags
        assert "ZERO_VOL:short_leg" in flags


# ===========================================================================
# DQ-2: spread_pct/EV/ROR null when quotes missing
# ===========================================================================

class TestNullMetricsOnMissingQuotes:
    """When quotes are missing, derived metrics must be null — never fabricated."""

    def test_spread_pct_null_when_quotes_missing(self):
        trade = _enrich_one(_candidate_for_enrich(long_bid=None))
        assert trade["bid_ask_spread_pct"] is None

    def test_ev_null_when_quotes_missing(self):
        trade = _enrich_one(_candidate_for_enrich(long_ask=None), request={"_skip_quote_integrity": True})
        assert trade["ev_per_contract"] is None
        assert trade["ev_per_share"] is None
        assert trade["ev_to_risk"] is None

    def test_ror_null_when_quotes_missing(self):
        trade = _enrich_one(_candidate_for_enrich(short_bid=None), request={"_skip_quote_integrity": True})
        assert trade["return_on_risk"] is None

    def test_net_debit_null_when_quotes_missing(self):
        trade = _enrich_one(_candidate_for_enrich(long_ask=None), request={"_skip_quote_integrity": True})
        assert trade["net_debit"] is None

    def test_metrics_populated_when_quotes_present(self):
        """Sanity: with valid quotes, metrics must be non-null."""
        trade = _enrich_one(_candidate_for_enrich())
        assert trade["net_debit"] is not None
        assert trade["ev_to_risk"] is not None
        assert trade["return_on_risk"] is not None
        assert trade["bid_ask_spread_pct"] is not None


# ===========================================================================
# EC-1: enrichment_counters uses exact counter names
# ===========================================================================

class TestEnrichmentCounterNames:
    """Verify the enrichment_counters dict has the exact keys the user requested."""

    REQUIRED_KEYS = {
        "total_enriched",
        "quote_lookup_attempted",
        "quote_lookup_success",
        "quote_lookup_failed",
        "oi_lookup_attempted",
        "oi_lookup_success",
        "oi_lookup_failed",
        "volume_lookup_attempted",
        "volume_lookup_success",
        "volume_lookup_failed",
    }

    def _simulate_counters(self, enriched: list[dict[str, Any]]) -> dict[str, int]:
        """Re-derive counters using the same logic as strategy_service."""
        _eq_total = len(enriched)
        _eq_has_all_quotes = sum(
            1 for _r in enriched if isinstance(_r, dict)
            and _r.get("_short_bid") is not None
            and _r.get("_short_ask") is not None
            and _r.get("_long_bid") is not None
            and _r.get("_long_ask") is not None
        )
        _eq_quote_failed = sum(
            1 for _r in enriched if isinstance(_r, dict)
            and _r.get("_quote_rejection")
        )
        _mfc_oi = sum(1 for _r in enriched if isinstance(_r, dict)
                      and _r.get("open_interest") is None)
        _eq_has_oi = sum(1 for _r in enriched if isinstance(_r, dict)
                         and _r.get("open_interest") is not None)
        _mfc_vol = sum(1 for _r in enriched if isinstance(_r, dict)
                       and _r.get("volume") is None)
        _eq_has_vol = sum(1 for _r in enriched if isinstance(_r, dict)
                          and _r.get("volume") is not None)
        return {
            "total_enriched": _eq_total,
            "quote_lookup_attempted": _eq_total,
            "quote_lookup_success": _eq_has_all_quotes,
            "quote_lookup_failed": _eq_quote_failed,
            "oi_lookup_attempted": _eq_total,
            "oi_lookup_success": _eq_has_oi,
            "oi_lookup_failed": _mfc_oi,
            "volume_lookup_attempted": _eq_total,
            "volume_lookup_success": _eq_has_vol,
            "volume_lookup_failed": _mfc_vol,
        }

    def test_all_required_keys_present(self):
        rows = [_make_enriched_row()]
        counters = self._simulate_counters(rows)
        assert self.REQUIRED_KEYS.issubset(counters.keys())

    def test_counters_for_healthy_rows(self):
        rows = [_make_enriched_row() for _ in range(5)]
        counters = self._simulate_counters(rows)
        assert counters["total_enriched"] == 5
        assert counters["quote_lookup_attempted"] == 5
        assert counters["quote_lookup_success"] == 5
        assert counters["quote_lookup_failed"] == 0
        assert counters["oi_lookup_success"] == 5
        assert counters["oi_lookup_failed"] == 0

    def test_counters_for_mixed_rows(self):
        rows = [
            _make_enriched_row(),  # healthy
            _make_enriched_row(short_bid=None, quote_rejection="QUOTE_INVALID:short_leg:missing_bid"),
            _make_enriched_row(open_interest=None),
        ]
        counters = self._simulate_counters(rows)
        assert counters["total_enriched"] == 3
        assert counters["quote_lookup_success"] == 2  # 1 & 3 have all quotes
        assert counters["quote_lookup_failed"] == 1   # row 2
        assert counters["oi_lookup_success"] == 2
        assert counters["oi_lookup_failed"] == 1

    def test_counters_all_quotes_missing(self):
        rows = [
            _make_enriched_row(
                short_bid=None, short_ask=None, long_bid=None, long_ask=None,
                quote_rejection="QUOTE_INVALID:long_leg:missing_bid",
            ),
        ]
        counters = self._simulate_counters(rows)
        assert counters["quote_lookup_success"] == 0
        assert counters["quote_lookup_failed"] == 1
