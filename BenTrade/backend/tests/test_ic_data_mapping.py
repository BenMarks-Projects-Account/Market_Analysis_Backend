"""Task H — Iron Condor data-mapping / reporting-bug tests.

Validates:
- IC enriched legs[] carry full market data: bid, ask, mid, delta, iv, OI, volume, occ_symbol
- IC enriched output includes spread_bid / spread_ask
- IC enriched output includes _short_bid/_short_ask/_long_bid/_long_ask (2-leg compat)
- IC enriched output includes delta / short_delta / short_delta_abs
- IC enriched output includes _credit_basis='mid'
- missing_bid / missing_ask / missing_delta counters are IC-aware
- missing_bid / missing_ask never contradict any_leg_quote_missing
- spread_quote_derived_success reflects IC spread_bid / spread_ask
- Near-miss builder populates per-leg AND top-level short_bid/ask for IC
- Counter semantics: quote_lookup_partial, quote_lookup_missing
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


def _make_leg(**kwargs: Any) -> SimpleNamespace:
    defaults = dict(
        strike=100, option_type="call", bid=1.0, ask=1.2,
        delta=0.30, gamma=0.02, theta=-0.03, vega=0.10,
        iv=0.25, open_interest=5000, volume=500, symbol="TEST260601C100",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_ic_candidate(**leg_overrides: Any) -> dict[str, Any]:
    """Build a standard 4-leg IC candidate for enrich()."""
    sp_bid = leg_overrides.get("short_put_bid", 0.85)
    sp_ask = leg_overrides.get("short_put_ask", 0.95)
    sp_delta = leg_overrides.get("short_put_delta", -0.15)
    lp_bid = leg_overrides.get("long_put_bid", 0.05)
    lp_ask = leg_overrides.get("long_put_ask", 0.10)
    lp_delta = leg_overrides.get("long_put_delta", -0.05)
    sc_bid = leg_overrides.get("short_call_bid", 0.85)
    sc_ask = leg_overrides.get("short_call_ask", 0.95)
    sc_delta = leg_overrides.get("short_call_delta", 0.15)
    lc_bid = leg_overrides.get("long_call_bid", 0.05)
    lc_ask = leg_overrides.get("long_call_ask", 0.10)
    lc_delta = leg_overrides.get("long_call_delta", 0.05)

    ps_leg = _make_leg(strike=90, option_type="put", bid=sp_bid, ask=sp_ask,
                        delta=sp_delta, symbol="TEST260601P090")
    pl_leg = _make_leg(strike=85, option_type="put", bid=lp_bid, ask=lp_ask,
                        delta=lp_delta, symbol="TEST260601P085")
    cs_leg = _make_leg(strike=110, option_type="call", bid=sc_bid, ask=sc_ask,
                        delta=sc_delta, symbol="TEST260601C110")
    cl_leg = _make_leg(strike=115, option_type="call", bid=lc_bid, ask=lc_ask,
                        delta=lc_delta, symbol="TEST260601C115")

    return {
        "strategy": "iron_condor",
        "spread_type": "iron_condor",
        "symbol": "TEST",
        "expiration": "2026-06-01",
        "dte": 30,
        "underlying_price": 100.0,
        "put_short_strike": 90,
        "put_long_strike": 85,
        "call_short_strike": 110,
        "call_long_strike": 115,
        "legs": [
            {"name": "long_put",   "right": "put",  "side": "buy",  "strike": 85,  "qty": 1, "_contract": pl_leg},
            {"name": "short_put",  "right": "put",  "side": "sell", "strike": 90,  "qty": 1, "_contract": ps_leg},
            {"name": "short_call", "right": "call", "side": "sell", "strike": 110, "qty": 1, "_contract": cs_leg},
            {"name": "long_call",  "right": "call", "side": "buy",  "strike": 115, "qty": 1, "_contract": cl_leg},
        ],
        "width_put": 5.0,
        "width_call": 5.0,
        "symmetry_score": 1.0,
        "expected_move": 5.0,
        "snapshot": {"symbol": "TEST", "prices_history": []},
    }


@pytest.fixture()
def plugin():
    from app.services.strategies.iron_condor import IronCondorStrategyPlugin
    return IronCondorStrategyPlugin()


# ────────────────────────────────────────────────────────────────────
# A. Enriched legs[] full market data: bid/ask/mid/delta/iv/OI/vol/occ
# ────────────────────────────────────────────────────────────────────

class TestEnrichedLegQuoteFields:
    """IC enriched legs[] must carry all canonical per-leg market fields."""

    _REQUIRED_LEG_FIELDS = {
        "name", "right", "side", "strike", "qty",
        "bid", "ask", "mid", "delta", "iv",
        "open_interest", "volume", "occ_symbol",
    }

    def test_ready_trade_legs_have_all_fields(self, plugin):
        """When readiness=True, every leg has all canonical fields populated."""
        candidate = _make_ic_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        assert trade["readiness"] is True
        for leg in trade["legs"]:
            for field in self._REQUIRED_LEG_FIELDS:
                assert field in leg, f"Leg {leg['name']} missing field: {field}"
            assert leg["bid"] is not None, f"Leg {leg['name']} bid should not be None"
            assert leg["ask"] is not None, f"Leg {leg['name']} ask should not be None"
            assert leg["mid"] is not None, f"Leg {leg['name']} mid should not be None"
            assert leg["delta"] is not None, f"Leg {leg['name']} delta should not be None"
            assert leg["iv"] is not None, f"Leg {leg['name']} iv should not be None"
            assert leg["occ_symbol"] is not None, f"Leg {leg['name']} occ_symbol should not be None"

    def test_ready_trade_legs_mid_formula(self, plugin):
        """mid = (bid + ask) / 2 for each leg."""
        candidate = _make_ic_candidate(
            short_put_bid=0.80, short_put_ask=1.00,
            long_put_bid=0.10, long_put_ask=0.20,
        )
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        by_name = {leg["name"]: leg for leg in trade["legs"]}
        sp = by_name["short_put"]
        assert sp["mid"] == pytest.approx((0.80 + 1.00) / 2.0, abs=0.001)
        lp = by_name["long_put"]
        assert lp["mid"] == pytest.approx((0.10 + 0.20) / 2.0, abs=0.001)

    def test_unready_trade_legs_have_bid_ask_but_none_mid(self, plugin):
        """When a leg has None bid/ask → readiness=False, mid=None for all."""
        candidate = _make_ic_candidate(short_put_bid=None, short_put_ask=None)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        assert trade["readiness"] is False
        by_name = {leg["name"]: leg for leg in trade["legs"]}
        assert by_name["short_put"]["bid"] is None
        assert by_name["short_put"]["ask"] is None
        for leg in trade["legs"]:
            assert leg["mid"] is None, f"Leg {leg['name']} mid should be None when unready"

    def test_legs_occ_symbol_from_contract(self, plugin):
        """occ_symbol comes from the leg contract's symbol attr."""
        candidate = _make_ic_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        by_name = {leg["name"]: leg for leg in trade["legs"]}
        assert by_name["short_put"]["occ_symbol"] == "TEST260601P090"
        assert by_name["long_put"]["occ_symbol"] == "TEST260601P085"
        assert by_name["short_call"]["occ_symbol"] == "TEST260601C110"
        assert by_name["long_call"]["occ_symbol"] == "TEST260601C115"

    def test_legs_delta_from_contract(self, plugin):
        """delta should come from the leg contract's delta attr."""
        candidate = _make_ic_candidate(
            short_put_delta=-0.18,
            long_put_delta=-0.04,
            short_call_delta=0.12,
            long_call_delta=0.03,
        )
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        by_name = {leg["name"]: leg for leg in trade["legs"]}
        assert by_name["short_put"]["delta"] == pytest.approx(-0.18)
        assert by_name["long_put"]["delta"] == pytest.approx(-0.04)
        assert by_name["short_call"]["delta"] == pytest.approx(0.12)
        assert by_name["long_call"]["delta"] == pytest.approx(0.03)


# ────────────────────────────────────────────────────────────────────
# B. Enriched output includes spread_bid / spread_ask
# ────────────────────────────────────────────────────────────────────

class TestSpreadBidAsk:
    """IC enriched output must include spread_bid and spread_ask."""

    def test_ready_trade_has_spread_bid_ask(self, plugin):
        candidate = _make_ic_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["readiness"] is True
        assert trade["spread_bid"] is not None, "spread_bid must be set when ready"
        assert trade["spread_ask"] is not None, "spread_ask must be set when ready"

    def test_spread_bid_formula(self, plugin):
        """spread_bid = (sp_bid + sc_bid) - (lp_ask + lc_ask)."""
        candidate = _make_ic_candidate(
            short_put_bid=0.80, short_put_ask=1.00,
            long_put_bid=0.10, long_put_ask=0.20,
            short_call_bid=0.80, short_call_ask=1.00,
            long_call_bid=0.10, long_call_ask=0.20,
        )
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        # spread_bid = (0.80 + 0.80) - (0.20 + 0.20) = 1.60 - 0.40 = 1.20
        assert trade["spread_bid"] == pytest.approx(1.20, abs=0.001)
        # spread_ask = (1.00 + 1.00) - (0.10 + 0.10) = 2.00 - 0.20 = 1.80
        assert trade["spread_ask"] == pytest.approx(1.80, abs=0.001)

    def test_unready_trade_spread_bid_ask_none(self, plugin):
        candidate = _make_ic_candidate(short_put_bid=None, short_put_ask=None)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["readiness"] is False
        assert trade["spread_bid"] is None
        assert trade["spread_ask"] is None

    def test_spread_bid_with_zero_bid(self, plugin):
        """A zero bid is a valid market value, not missing data."""
        candidate = _make_ic_candidate(
            long_put_bid=0.0, long_call_bid=0.0,
        )
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["readiness"] is True
        assert trade["spread_bid"] is not None


# ────────────────────────────────────────────────────────────────────
# C. Per-leg transient fields + 2-leg compat + delta
# ────────────────────────────────────────────────────────────────────

class TestPerLegTransientFields:
    """IC enriched output sets per-leg bid/ask transients for counter compat."""

    def test_ready_trade_has_per_leg_bid_ask(self, plugin):
        candidate = _make_ic_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        for field in ("_short_put_bid", "_short_put_ask", "_long_put_bid",
                       "_long_put_ask", "_short_call_bid", "_short_call_ask",
                       "_long_call_bid", "_long_call_ask"):
            assert field in trade, f"Missing field: {field}"
            assert trade[field] is not None, f"{field} should not be None when ready"

    def test_bid_zero_preserved(self, plugin):
        """bid=0.0 must be kept as 0.0, not treated as missing."""
        candidate = _make_ic_candidate(long_put_bid=0.0)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["_long_put_bid"] == 0.0


class TestTwoLegCompatFields:
    """IC enriched output sets _short_bid/ask = short_put, _long_bid/ask = long_put."""

    def test_compat_fields_populated(self, plugin):
        candidate = _make_ic_candidate(
            short_put_bid=0.85, short_put_ask=0.95,
            long_put_bid=0.05, long_put_ask=0.10,
        )
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["_short_bid"] == pytest.approx(0.85)
        assert trade["_short_ask"] == pytest.approx(0.95)
        assert trade["_long_bid"] == pytest.approx(0.05)
        assert trade["_long_ask"] == pytest.approx(0.10)

    def test_compat_fields_none_when_leg_missing(self, plugin):
        candidate = _make_ic_candidate(short_put_bid=None, short_put_ask=None)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["_short_bid"] is None
        assert trade["_short_ask"] is None

    def test_bid_zero_preserved_in_compat(self, plugin):
        """bid=0.0 must be kept as 0.0 in _long_bid, not None."""
        candidate = _make_ic_candidate(long_put_bid=0.0)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["_long_bid"] == 0.0


class TestDeltaPersistence:
    """IC enriched output must include delta/short_delta/short_delta_abs."""

    def test_delta_fields_present(self, plugin):
        candidate = _make_ic_candidate(short_put_delta=-0.15)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["readiness"] is True
        assert trade["delta"] == pytest.approx(-0.15)
        assert trade["short_delta"] == pytest.approx(-0.15)
        assert trade["short_delta_abs"] == pytest.approx(0.15)

    def test_per_leg_delta_transient_fields(self, plugin):
        candidate = _make_ic_candidate(
            short_put_delta=-0.15, long_put_delta=-0.05,
            short_call_delta=0.12, long_call_delta=0.03,
        )
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["_short_put_delta"] == pytest.approx(-0.15)
        assert trade["_long_put_delta"] == pytest.approx(-0.05)
        assert trade["_short_call_delta"] == pytest.approx(0.12)
        assert trade["_long_call_delta"] == pytest.approx(0.03)

    def test_credit_basis_set(self, plugin):
        """Enriched IC should set _credit_basis='mid'."""
        candidate = _make_ic_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["_credit_basis"] == "mid"


# ────────────────────────────────────────────────────────────────────
# D. Counter consistency: missing_bid/missing_ask vs any_leg_quote_missing
# ────────────────────────────────────────────────────────────────────

class TestCounterConsistency:
    """missing_bid/missing_ask/missing_delta must never contradict any_leg_quote_missing for IC.

    The original bug: missing_bid=220, missing_ask=220, missing_delta=220
    but any_leg_quote_missing=0.  After fix: all must be 0 when readiness=True.
    """

    def _run_missing_field_counts(self, enriched: list[dict]) -> dict[str, int]:
        """Replicate the missing_field_counts logic from strategy_service.py.

        MUST mirror the production code exactly:
        - For rows with canonical legs[]: derive bid/ask/delta counters
          from legs[].bid / legs[].ask / legs[].delta directly.
        - For 2-leg rows: use transient _short_bid etc.
        """
        _mfc_bid = 0
        _mfc_ask = 0
        _mfc_any_leg_quote_missing = 0
        _mfc_delta = 0
        for _row in enriched:
            if not isinstance(_row, dict):
                continue
            _ic_legs = _row.get("legs")
            if isinstance(_ic_legs, list) and len(_ic_legs) >= 2:
                # Multi-leg (IC): read directly from legs[]
                _any_bid_missing = any(
                    isinstance(lg, dict) and lg.get("bid") is None
                    for lg in _ic_legs
                )
                _any_ask_missing = any(
                    isinstance(lg, dict) and lg.get("ask") is None
                    for lg in _ic_legs
                )
                if _any_bid_missing:
                    _mfc_bid += 1
                if _any_ask_missing:
                    _mfc_ask += 1
                if _any_bid_missing or _any_ask_missing:
                    _mfc_any_leg_quote_missing += 1
                _any_delta_missing = any(
                    isinstance(lg, dict) and lg.get("delta") is None
                    for lg in _ic_legs
                )
                if _any_delta_missing:
                    _mfc_delta += 1
            else:
                # 2-leg / legacy strategies
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
                _has_delta = (_row.get("delta") is not None
                              or _row.get("short_delta") is not None
                              or _row.get("short_delta_abs") is not None)
                if not _has_delta:
                    _mfc_delta += 1
        return {
            "missing_bid": _mfc_bid,
            "missing_ask": _mfc_ask,
            "any_leg_quote_missing": _mfc_any_leg_quote_missing,
            "missing_delta": _mfc_delta,
        }

    def test_ready_ic_zero_missing(self, plugin):
        """Readiness=True IC rows must produce 0 for all missing counters."""
        candidate = _make_ic_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        assert enriched[0]["readiness"] is True
        counts = self._run_missing_field_counts(enriched)
        assert counts["missing_bid"] == 0, f"missing_bid should be 0, got {counts['missing_bid']}"
        assert counts["missing_ask"] == 0, f"missing_ask should be 0, got {counts['missing_ask']}"
        assert counts["any_leg_quote_missing"] == 0
        assert counts["missing_delta"] == 0, f"missing_delta should be 0, got {counts['missing_delta']}"

    def test_unready_ic_consistent_counters(self, plugin):
        """Readiness=False IC rows increment bid/ask/any together."""
        candidate = _make_ic_candidate(short_put_bid=None, short_put_ask=None)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        assert enriched[0]["readiness"] is False
        counts = self._run_missing_field_counts(enriched)
        assert counts["missing_bid"] == 1
        assert counts["missing_ask"] == 1
        assert counts["any_leg_quote_missing"] == 1

    def test_no_contradiction(self, plugin):
        """INVARIANT: (missing_bid > 0 or missing_ask > 0) AND any_leg_quote_missing == 0 is impossible."""
        candidate = _make_ic_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        counts = self._run_missing_field_counts(enriched)
        if counts["any_leg_quote_missing"] == 0:
            assert counts["missing_bid"] == 0, (
                "CONTRADICTION: any_leg_quote_missing=0 but missing_bid>0"
            )
            assert counts["missing_ask"] == 0, (
                "CONTRADICTION: any_leg_quote_missing=0 but missing_ask>0"
            )

    def test_no_delta_contradiction(self, plugin):
        """When delta is present on the enriched row, missing_delta must be 0."""
        candidate = _make_ic_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert enriched[0].get("delta") is not None or enriched[0].get("short_delta") is not None
        counts = self._run_missing_field_counts(enriched)
        assert counts["missing_delta"] == 0, (
            f"CONTRADICTION: delta field present but missing_delta={counts['missing_delta']}"
        )


# ────────────────────────────────────────────────────────────────────
# E. spread_quote_derived counter for IC
# ────────────────────────────────────────────────────────────────────

class TestSpreadQuoteDerivedCounter:
    """IC with all legs having bid+ask AND net_credit must count as spread_quote_derived_success.

    Production logic reads from legs[] directly:
    - All 4 legs have bid is not None and ask is not None
    - AND net_credit is not None
    """

    def _count_spread_derived(self, enriched: list[dict]) -> int:
        """Mirror the production legs[]-based spread_quote_derived logic."""
        count = 0
        for r in enriched:
            if not isinstance(r, dict):
                continue
            _ic_legs = r.get("legs")
            if isinstance(_ic_legs, list) and len(_ic_legs) >= 2:
                _all_bid = all(
                    isinstance(lg, dict) and lg.get("bid") is not None
                    for lg in _ic_legs
                )
                _all_ask = all(
                    isinstance(lg, dict) and lg.get("ask") is not None
                    for lg in _ic_legs
                )
                if _all_bid and _all_ask and r.get("net_credit") is not None:
                    count += 1
            else:
                if (r.get("spread_bid") is not None
                        and r.get("spread_ask") is not None):
                    count += 1
        return count

    def test_ready_ic_counts_as_derived(self, plugin):
        candidate = _make_ic_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert self._count_spread_derived(enriched) == len(enriched)

    def test_unready_ic_not_counted(self, plugin):
        candidate = _make_ic_candidate(short_put_bid=None, short_put_ask=None)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert self._count_spread_derived(enriched) == 0

    def test_bid_zero_counts_as_derived(self, plugin):
        """Far OTM option with bid=0, ask>0 is valid — spread should count as derived."""
        candidate = _make_ic_candidate(long_put_bid=0.0, long_call_bid=0.0)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        # bid=0.0 is valid market data, readiness=True, net_credit exists
        assert enriched[0]["readiness"] is True
        assert enriched[0]["net_credit"] is not None
        assert self._count_spread_derived(enriched) == 1, (
            "bid=0.0 must not cause spread_quote_derived to fail"
        )

    def test_quote_lookup_success_matches_spread_derived(self, plugin):
        """When all legs have bid+ask, both quote_lookup_success and spread_derived should count."""
        candidate = _make_ic_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        # Both counters should agree for fully-quoted IC
        _ic_legs = enriched[0]["legs"]
        _all_bid = all(isinstance(lg, dict) and lg.get("bid") is not None for lg in _ic_legs)
        _all_ask = all(isinstance(lg, dict) and lg.get("ask") is not None for lg in _ic_legs)
        assert _all_bid and _all_ask, "All legs must have bid and ask"
        assert self._count_spread_derived(enriched) == 1


# ────────────────────────────────────────────────────────────────────
# F. Near-miss builder populates per-leg IC fields
# ────────────────────────────────────────────────────────────────────

class TestNearMissICFields:
    """Near-miss entries for IC must include per-leg bid/ask/mid, top-level short/long_bid/ask, and spread_bid/ask."""

    def _build_ic_row(self) -> dict[str, Any]:
        return {
            "underlying": "TEST",
            "symbol": "TEST",
            "expiration": "2026-06-01",
            "dte": 30,
            "width": 5.0,
            "spread_type": "iron_condor",
            "strategy": "iron_condor",
            "readiness": True,
            # Per-leg mids
            "short_put_mid": 0.90,
            "long_put_mid": 0.075,
            "short_call_mid": 0.90,
            "long_call_mid": 0.075,
            # 2-leg compat fields (IC now sets these)
            "_short_bid": 0.85,
            "_short_ask": 0.95,
            "_long_bid": 0.05,
            "_long_ask": 0.10,
            # IC per-leg bid/ask
            "_short_put_bid": 0.85,
            "_short_put_ask": 0.95,
            "_long_put_bid": 0.05,
            "_long_put_ask": 0.10,
            "_short_call_bid": 0.85,
            "_short_call_ask": 0.95,
            "_long_call_bid": 0.05,
            "_long_call_ask": 0.10,
            # Spread-level
            "spread_bid": 1.20,
            "spread_ask": 1.80,
            # IC strikes
            "short_put_strike": 90,
            "long_put_strike": 85,
            "short_call_strike": 110,
            "long_call_strike": 115,
            "put_wing_width": 5.0,
            "call_wing_width": 5.0,
            # Metrics (low to trigger rejection)
            "p_win_used": 0.30,
            "ev_to_risk": -0.05,
            "return_on_risk": 0.005,
            "net_credit": 0.10,
            "max_loss": 490.0,
            "open_interest": 100,
            "volume": 5,
        }

    def test_near_miss_top_level_short_long_bid_ask(self):
        """Top-level short_bid/short_ask/long_bid/long_ask must NOT be null for IC."""
        from app.services.strategy_service import StrategyService

        row = self._build_ic_row()
        rejected_rows = [(row, ["pop_below_threshold"])]
        result = StrategyService._build_near_miss(rejected_rows, {}, {}, limit=5)
        assert len(result) == 1
        nm = result[0]
        # These were the original null fields — must now be populated
        assert nm["short_bid"] is not None, "short_bid must not be null for IC"
        assert nm["short_ask"] is not None, "short_ask must not be null for IC"
        assert nm["long_bid"] is not None, "long_bid must not be null for IC"
        assert nm["long_ask"] is not None, "long_ask must not be null for IC"

    def test_near_miss_top_level_values_match_put_legs(self):
        """short_bid = short_put.bid, long_bid = long_put.bid (documented mapping)."""
        from app.services.strategy_service import StrategyService

        row = self._build_ic_row()
        rejected_rows = [(row, ["pop_below_threshold"])]
        result = StrategyService._build_near_miss(rejected_rows, {}, {}, limit=5)
        nm = result[0]
        assert nm["short_bid"] == pytest.approx(0.85)
        assert nm["short_ask"] == pytest.approx(0.95)
        assert nm["long_bid"] == pytest.approx(0.05)
        assert nm["long_ask"] == pytest.approx(0.10)

    def test_near_miss_ic_per_leg_bid_ask(self):
        """IC-specific per-leg bid/ask fields must be populated."""
        from app.services.strategy_service import StrategyService

        row = self._build_ic_row()
        rejected_rows = [(row, ["pop_below_threshold"])]
        result = StrategyService._build_near_miss(rejected_rows, {}, {}, limit=5)
        nm = result[0]
        assert nm["short_put_bid"] == pytest.approx(0.85)
        assert nm["short_call_bid"] == pytest.approx(0.85)
        assert nm["long_put_bid"] == pytest.approx(0.05)
        assert nm["long_call_bid"] == pytest.approx(0.05)

    def test_near_miss_spread_bid_ask(self):
        """Spread-level bid/ask must be present."""
        from app.services.strategy_service import StrategyService

        row = self._build_ic_row()
        rejected_rows = [(row, ["pop_below_threshold"])]
        result = StrategyService._build_near_miss(rejected_rows, {}, {}, limit=5)
        nm = result[0]
        assert nm["spread_bid"] == pytest.approx(1.20)
        assert nm["spread_ask"] == pytest.approx(1.80)

    def test_near_miss_short_mid_computed(self):
        """When short_bid + short_ask are present, short_mid should be derived."""
        from app.services.strategy_service import StrategyService

        row = self._build_ic_row()
        rejected_rows = [(row, ["pop_below_threshold"])]
        result = StrategyService._build_near_miss(rejected_rows, {}, {}, limit=5)
        nm = result[0]
        # short_mid = (short_bid + short_ask) / 2 = (0.85 + 0.95) / 2 = 0.90
        assert nm["short_mid"] == pytest.approx(0.90, abs=0.01)
        # long_mid = (long_bid + long_ask) / 2 = (0.05 + 0.10) / 2 = 0.075
        assert nm["long_mid"] == pytest.approx(0.075, abs=0.01)


# ────────────────────────────────────────────────────────────────────
# G. Bid=0 is valid market data
# ────────────────────────────────────────────────────────────────────

class TestBidZeroValid:
    """bid=0.0 is a valid market value, must not be treated as missing."""

    def test_bid_zero_preserved_in_legs(self, plugin):
        candidate = _make_ic_candidate(long_put_bid=0.0)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        by_name = {leg["name"]: leg for leg in trade["legs"]}
        assert by_name["long_put"]["bid"] == 0.0

    def test_bid_zero_in_compat_field(self, plugin):
        """_long_bid = long_put.bid = 0.0 must be preserved."""
        candidate = _make_ic_candidate(long_put_bid=0.0)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["_long_bid"] == 0.0

    def test_bid_zero_ready(self, plugin):
        """bid=0.0 should NOT cause readiness=False."""
        candidate = _make_ic_candidate(long_put_bid=0.0)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert enriched[0]["readiness"] is True


# ────────────────────────────────────────────────────────────────────
# H. pop_model_used field
# ────────────────────────────────────────────────────────────────────

class TestPopModelUsed:
    """IC enriched output must set pop_model_used for dq_summary."""

    def test_ready_trade_has_normal_cdf(self, plugin):
        """When readiness=True, pop_model_used should be 'normal_cdf'."""
        candidate = _make_ic_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["readiness"] is True
        assert trade["pop_model_used"] == "normal_cdf"

    def test_unready_trade_has_none_model(self, plugin):
        """When readiness=False, pop_model_used should be 'NONE'."""
        candidate = _make_ic_candidate(short_put_bid=None, short_put_ask=None)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        trade = enriched[0]
        assert trade["readiness"] is False
        assert trade["pop_model_used"] == "NONE"
