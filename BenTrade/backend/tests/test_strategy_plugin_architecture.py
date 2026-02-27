"""Regression tests for strategy plugin architecture refactoring.

Covers:
  1. StrategyPlugin ABC: POP source constants, TRANSIENT_FIELDS, hook methods
  2. Plugin inheritance: all 6 plugins inherit from StrategyPlugin
  3. Per-strategy Pydantic models: invariant validation
  4. Enrichment counter delegation: plugin.compute_enrichment_counters()
  5. Near-miss delegation: plugin.build_near_miss_entry()
  6. POP attribution invariant: validate_pop_attribution()
  7. Transient field coverage: plugin.TRANSIENT_FIELDS ⊇ base fields
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

import pytest

from app.models.strategy_trades import (
    BaseTrade,
    CreditSpreadTrade,
    DebitSpreadTrade,
    EnrichedLeg,
    IronCondorTrade,
)
from app.services.strategies.base import (
    ALL_POP_SOURCES,
    POP_SOURCE_BREAKEVEN_LOGNORMAL,
    POP_SOURCE_DELTA_ADJUSTED,
    POP_SOURCE_DELTA_APPROX,
    POP_SOURCE_NONE,
    POP_SOURCE_NORMAL_CDF,
    StrategyPlugin,
)
from app.services.strategies.butterflies import ButterfliesStrategyPlugin
from app.services.strategies.calendars import CalendarsStrategyPlugin
from app.services.strategies.credit_spread import CreditSpreadStrategyPlugin
from app.services.strategies.debit_spreads import DebitSpreadsStrategyPlugin
from app.services.strategies.income import IncomeStrategyPlugin
from app.services.strategies.iron_condor import IronCondorStrategyPlugin


# ── Helpers ────────────────────────────────────────────────────────────────

@dataclass
class FakeContract:
    """Minimal option contract stub for unit tests."""
    strike: float
    bid: float | None
    ask: float | None
    option_type: str = "put"
    delta: float | None = -0.30
    iv: float | None = 0.22
    open_interest: int | None = 500
    volume: int | None = 80
    symbol: str = "SPY250725P00590000"
    theta: float | None = -0.05
    vega: float | None = 0.10


_FUTURE_EXP = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()


# ===========================================================================
# Test: Plugin inheritance
# ===========================================================================

class TestPluginInheritance:
    """All 6 plugins must inherit from StrategyPlugin ABC."""

    @pytest.mark.parametrize("cls", [
        CreditSpreadStrategyPlugin,
        DebitSpreadsStrategyPlugin,
        IronCondorStrategyPlugin,
        ButterfliesStrategyPlugin,
        CalendarsStrategyPlugin,
        IncomeStrategyPlugin,
    ])
    def test_inherits_from_strategy_plugin(self, cls):
        assert issubclass(cls, StrategyPlugin)

    @pytest.mark.parametrize("cls", [
        CreditSpreadStrategyPlugin,
        DebitSpreadsStrategyPlugin,
        IronCondorStrategyPlugin,
        ButterfliesStrategyPlugin,
        CalendarsStrategyPlugin,
        IncomeStrategyPlugin,
    ])
    def test_has_transient_fields(self, cls):
        """Every plugin must have TRANSIENT_FIELDS as a frozenset."""
        assert isinstance(cls.TRANSIENT_FIELDS, frozenset)
        # Must include ALL base transient fields
        assert cls.TRANSIENT_FIELDS >= StrategyPlugin.TRANSIENT_FIELDS

    @pytest.mark.parametrize("cls", [
        CreditSpreadStrategyPlugin,
        DebitSpreadsStrategyPlugin,
        IronCondorStrategyPlugin,
    ])
    def test_has_plugin_id(self, cls):
        """Core plugins must have a non-empty id."""
        plugin = cls()
        assert plugin.id and isinstance(plugin.id, str)


# ===========================================================================
# Test: POP source constants
# ===========================================================================

class TestPOPSourceConstants:
    """POP source labels are stable enum-like values."""

    def test_all_pop_sources_is_frozenset(self):
        assert isinstance(ALL_POP_SOURCES, frozenset)

    def test_known_sources_in_all(self):
        for src in (
            POP_SOURCE_NONE, POP_SOURCE_NORMAL_CDF,
            POP_SOURCE_DELTA_APPROX, POP_SOURCE_DELTA_ADJUSTED,
            POP_SOURCE_BREAKEVEN_LOGNORMAL,
        ):
            assert src in ALL_POP_SOURCES

    def test_none_is_uppercase(self):
        assert POP_SOURCE_NONE == "NONE"


# ===========================================================================
# Test: POP attribution invariant
# ===========================================================================

class TestPOPAttributionInvariant:
    """validate_pop_attribution() must detect p_win_used without model."""

    def test_valid_attribution(self):
        plugin = CreditSpreadStrategyPlugin()
        trade = {"p_win_used": 0.72, "pop_model_used": POP_SOURCE_NORMAL_CDF}
        assert plugin.validate_pop_attribution(trade) is None

    def test_missing_pop_is_ok(self):
        plugin = CreditSpreadStrategyPlugin()
        trade = {"p_win_used": None, "pop_model_used": POP_SOURCE_NONE}
        assert plugin.validate_pop_attribution(trade) is None

    def test_violation_pop_without_model(self):
        plugin = CreditSpreadStrategyPlugin()
        trade = {"p_win_used": 0.65, "pop_model_used": POP_SOURCE_NONE}
        err = plugin.validate_pop_attribution(trade)
        assert err is not None
        assert "INVARIANT_VIOLATION" in err

    def test_violation_pop_with_none_model(self):
        plugin = DebitSpreadsStrategyPlugin()
        trade = {"p_win_used": 0.55, "pop_model_used": None}
        err = plugin.validate_pop_attribution(trade)
        assert err is not None
        assert "INVARIANT_VIOLATION" in err


# ===========================================================================
# Test: Enrichment counter delegation
# ===========================================================================

class TestEnrichmentCounterDelegation:
    """plugin.compute_enrichment_counters() derives counters from legs[]."""

    def _make_enriched_with_legs(self, n: int = 5) -> list[dict]:
        """Create n enriched rows with valid 2-leg canonical legs[]."""
        rows = []
        for i in range(n):
            rows.append({
                "legs": [
                    {"name": "short_put", "bid": 1.50, "ask": 1.60, "delta": -0.30, "side": "sell"},
                    {"name": "long_put", "bid": 0.40, "ask": 0.50, "delta": -0.10, "side": "buy"},
                ],
                "spread_bid": 1.00,
                "spread_ask": 1.20,
                "net_credit": 1.10,
            })
        return rows

    def test_all_quotes_present(self):
        plugin = CreditSpreadStrategyPlugin()
        enriched = self._make_enriched_with_legs(3)
        counters = plugin.compute_enrichment_counters(enriched)

        assert counters["total_enriched"] == 3
        assert counters["leg_quote_lookup_success"] == 3
        assert counters["leg_quote_lookup_failed"] == 0
        assert counters["spread_quote_derived_success"] == 3

    def test_missing_one_bid(self):
        plugin = CreditSpreadStrategyPlugin()
        enriched = self._make_enriched_with_legs(2)
        # Remove bid from short leg of first row
        enriched[0]["legs"][0]["bid"] = None
        counters = plugin.compute_enrichment_counters(enriched)

        assert counters["leg_quote_lookup_success"] == 1  # only second row
        assert counters["leg_quote_lookup_failed"] == 1

    def test_four_leg_ic(self):
        plugin = IronCondorStrategyPlugin()
        row = {
            "legs": [
                {"name": "long_put", "bid": 0.10, "ask": 0.20, "delta": -0.05, "side": "buy"},
                {"name": "short_put", "bid": 0.50, "ask": 0.60, "delta": -0.15, "side": "sell"},
                {"name": "short_call", "bid": 0.50, "ask": 0.60, "delta": 0.15, "side": "sell"},
                {"name": "long_call", "bid": 0.10, "ask": 0.20, "delta": 0.05, "side": "buy"},
            ],
            "spread_bid": 0.60,
            "spread_ask": 0.80,
            "net_credit": 0.70,
        }
        counters = plugin.compute_enrichment_counters([row])
        assert counters["leg_quote_lookup_success"] == 1
        assert counters["spread_quote_derived_success"] == 1

    def test_empty_enriched(self):
        plugin = CreditSpreadStrategyPlugin()
        counters = plugin.compute_enrichment_counters([])
        assert counters["total_enriched"] == 0
        assert counters["leg_quote_lookup_success"] == 0


# ===========================================================================
# Test: Near-miss delegation (IC)
# ===========================================================================

class TestNearMissDelegation:
    """IC plugin's build_near_miss_entry adds strategy-specific fields."""

    def test_ic_near_miss_adds_sigma_fields(self):
        plugin = IronCondorStrategyPlugin()
        row = {
            "spread_type": "iron_condor",
            "short_put_strike": 580.0,
            "long_put_strike": 575.0,
            "short_call_strike": 620.0,
            "long_call_strike": 625.0,
            "put_wing_width": 5.0,
            "call_wing_width": 5.0,
            "readiness": True,
            "short_put_mid": 1.50,
            "long_put_mid": 0.40,
            "short_call_mid": 1.20,
            "long_call_mid": 0.30,
            "_short_put_bid": 1.40,
            "_short_put_ask": 1.60,
            "_long_put_bid": 0.35,
            "_long_put_ask": 0.45,
            "_short_call_bid": 1.10,
            "_short_call_ask": 1.30,
            "_long_call_bid": 0.25,
            "_long_call_ask": 0.35,
            "spread_bid": 1.80,
            "spread_ask": 2.20,
            "sigma_put": 12.5,
            "sigma_call": 13.0,
            "min_sigma_dist": 1.25,
            "put_short_sigma_dist": 1.30,
            "call_short_sigma_dist": 1.25,
        }
        base_entry = {"symbol": "SPY", "nearness_score": -2.5}
        result = plugin.build_near_miss_entry(row, ["distance_below_min_sigma"], base_entry)

        # IC-specific fields added
        assert result["short_put_strike"] == 580.0
        assert result["long_call_strike"] == 625.0
        assert result["sigma_put"] == 12.5
        assert result["min_sigma_dist"] == 1.25
        assert result["readiness"] is True
        # Base entry fields preserved
        assert result["symbol"] == "SPY"
        assert result["nearness_score"] == -2.5

    def test_credit_spread_near_miss_is_noop(self):
        """Default build_near_miss_entry returns base_entry unchanged."""
        plugin = CreditSpreadStrategyPlugin()
        base_entry = {"symbol": "SPY", "net_credit": 1.20}
        result = plugin.build_near_miss_entry({}, [], base_entry)
        assert result is base_entry  # same object, unchanged


# ===========================================================================
# Test: Transient field isolation
# ===========================================================================

class TestTransientFieldIsolation:
    """IC plugin has IC-specific transient fields; credit doesn't."""

    def test_ic_transient_includes_per_leg(self):
        assert "_short_put_bid" in IronCondorStrategyPlugin.TRANSIENT_FIELDS
        assert "_long_call_ask" in IronCondorStrategyPlugin.TRANSIENT_FIELDS
        assert "_penny_wing" in IronCondorStrategyPlugin.TRANSIENT_FIELDS

    def test_credit_transient_does_not_include_ic_fields(self):
        assert "_short_put_bid" not in CreditSpreadStrategyPlugin.TRANSIENT_FIELDS
        assert "_penny_wing" not in CreditSpreadStrategyPlugin.TRANSIENT_FIELDS

    def test_debit_transient_includes_debit_specific(self):
        assert "_dq_flags" in DebitSpreadsStrategyPlugin.TRANSIENT_FIELDS
        assert "_pop_gate_eval" in DebitSpreadsStrategyPlugin.TRANSIENT_FIELDS


# ===========================================================================
# Test: Pydantic trade models
# ===========================================================================

class TestEnrichedLeg:
    def test_valid_leg(self):
        leg = EnrichedLeg(name="short_put", right="put", side="sell",
                          strike=590.0, bid=1.50, ask=1.60, mid=1.55)
        assert leg.name == "short_put"
        assert leg.mid == 1.55

    def test_mid_without_bid_warns(self, caplog):
        """Mid without bid/ask should warn but not fail."""
        with caplog.at_level("WARNING"):
            leg = EnrichedLeg(name="short_put", right="put", side="sell",
                              strike=590.0, mid=1.55, bid=None, ask=1.60)
        assert any("invariant violation" in r.message for r in caplog.records)


class TestCreditSpreadTrade:
    def test_valid_credit_trade(self):
        trade = CreditSpreadTrade(
            strategy="put_credit_spread",
            spread_type="put_credit_spread",
            net_credit=1.10,
            width=5.0,
            short_strike=590.0,
            long_strike=585.0,
            p_win_used=0.72,
            pop_model_used="normal_cdf",
        )
        assert trade.net_credit == 1.10

    def test_negative_credit_warns(self, caplog):
        with caplog.at_level("WARNING"):
            CreditSpreadTrade(
                net_credit=-0.50,
                width=5.0,
            )
        assert any("net_credit" in r.message for r in caplog.records)

    def test_credit_exceeds_width_warns(self, caplog):
        with caplog.at_level("WARNING"):
            CreditSpreadTrade(
                net_credit=6.0,
                width=5.0,
            )
        assert any("net_credit" in r.message for r in caplog.records)

    def test_pop_attribution_violation_warns(self, caplog):
        """p_win_used set but pop_model_used is NONE → warning."""
        with caplog.at_level("WARNING"):
            CreditSpreadTrade(
                p_win_used=0.72,
                pop_model_used="NONE",
            )
        assert any("POP attribution" in r.message for r in caplog.records)


class TestDebitSpreadTrade:
    def test_valid_debit_trade(self):
        trade = DebitSpreadTrade(
            strategy="call_debit",
            net_debit=2.00,
            width=5.0,
            p_win_used=0.55,
            pop_model_used="BREAKEVEN_LOGNORMAL",
        )
        assert trade.net_debit == 2.00

    def test_debit_exceeds_width_warns(self, caplog):
        with caplog.at_level("WARNING"):
            DebitSpreadTrade(net_debit=6.0, width=5.0)
        assert any("net_debit" in r.message for r in caplog.records)


class TestIronCondorTrade:
    def test_valid_ic_trade(self):
        legs = [
            {"name": "long_put", "right": "put", "side": "buy", "strike": 575.0},
            {"name": "short_put", "right": "put", "side": "sell", "strike": 580.0},
            {"name": "short_call", "right": "call", "side": "sell", "strike": 620.0},
            {"name": "long_call", "right": "call", "side": "buy", "strike": 625.0},
        ]
        trade = IronCondorTrade(
            strategy="iron_condor",
            legs=legs,
            net_credit=0.70,
            width=5.0,
            readiness=True,
            p_win_used=0.68,
            pop_model_used="normal_cdf",
        )
        assert len(trade.legs) == 4

    def test_wrong_leg_count_warns(self, caplog):
        with caplog.at_level("WARNING"):
            IronCondorTrade(
                legs=[
                    {"name": "short_put"},
                    {"name": "short_call"},
                ],
            )
        assert any("expected 4 legs" in r.message for r in caplog.records)


# ===========================================================================
# Test: Credit spread plugin POP constant usage
# ===========================================================================

class TestCreditSpreadPOPLabeling:
    """Credit spread enrich() must use POP_SOURCE_* constants."""

    def test_pop_model_uses_constant(self):
        plugin = CreditSpreadStrategyPlugin()
        short = FakeContract(strike=590.0, bid=1.50, ask=1.60, delta=-0.30)
        long = FakeContract(strike=585.0, bid=0.40, ask=0.50, delta=-0.10)
        candidates = [{
            "short_leg": short,
            "long_leg": long,
            "strategy": "put_credit_spread",
            "width": 5.0,
            "snapshot": {
                "symbol": "SPY",
                "expiration": _FUTURE_EXP,
                "underlying_price": 600.0,
                "vix": 18.0,
            },
        }]
        inputs = {
            "symbol": "SPY",
            "expiration": _FUTURE_EXP,
            "underlying_price": 600.0,
            "vix": 18.0,
            "prices_history": [600 + i * 0.1 for i in range(30)],
            "request": {},
        }
        enriched = plugin.enrich(candidates, inputs)
        assert len(enriched) >= 1
        trade = enriched[0]
        pm = trade.get("pop_model_used")
        # Must be a known POP source constant
        assert pm in ALL_POP_SOURCES, f"pop_model_used={pm!r} not in ALL_POP_SOURCES"
        # If p_win_used is set, model must not be NONE
        if trade.get("p_win_used") is not None:
            assert pm != POP_SOURCE_NONE
