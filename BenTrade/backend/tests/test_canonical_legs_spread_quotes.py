"""Unit tests for canonical legs[] array , spread-level quote derivation,
and pop_model_used labeling across credit_spread and debit_spread strategies.

Tests cover:
  1. Credit spread: legs[], spread_bid/ask/mid derivation from leg quotes
  2. Debit spread: legs[], spread_bid/ask/mid derivation
  3. Invalid/crossed markets → _quote_rejection, spread_* = None
  4. pop_model_used labeling
  5. Enrichment counter compatibility (legs[] drives multi-leg path)
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services.strategies.credit_spread import CreditSpreadStrategyPlugin
from app.services.strategies.debit_spreads import DebitSpreadsStrategyPlugin


# ---------------------------------------------------------------------------
# Helpers: synthetic option contract objects
# ---------------------------------------------------------------------------

@dataclass
class FakeContract:
    """Minimal option contract stub matching OptionContract attrs."""
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


# A future expiration date (30 days out) so dte_ceil() returns > 0.
_FUTURE_EXP = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()


def _make_credit_inputs(
    short: FakeContract,
    long: FakeContract,
    underlying_price: float = 600.0,
    symbol: str = "SPY",
    expiration: str | None = None,
) -> tuple[list[dict], dict]:
    """Build (candidates, inputs) for CreditSpreadStrategyPlugin.enrich()."""
    if expiration is None:
        expiration = _FUTURE_EXP
    candidates = [
        {
            "short_leg": short,
            "long_leg": long,
            "strategy": "put_credit_spread",
            "width": abs(short.strike - long.strike),
            "snapshot": {
                "symbol": symbol,
                "expiration": expiration,
                "underlying_price": underlying_price,
                "vix": 18.0,
            },
        }
    ]
    inputs = {
        "symbol": symbol,
        "expiration": expiration,
        "underlying_price": underlying_price,
        "vix": 18.0,
        "prices_history": [600 + i * 0.1 for i in range(30)],
        "request": {},
    }
    return candidates, inputs


def _make_debit_inputs(
    long_leg: FakeContract,
    short_leg: FakeContract,
    strategy: str = "call_debit",
    underlying_price: float = 600.0,
    symbol: str = "SPY",
    expiration: str | None = None,
) -> tuple[list[dict], dict]:
    """Build (candidates, inputs) for DebitSpreadsStrategyPlugin.enrich()."""
    if expiration is None:
        expiration = _FUTURE_EXP
    dte = 30
    width = abs(long_leg.strike - short_leg.strike)
    candidates = [
        {
            "long_leg": long_leg,
            "short_leg": short_leg,
            "strategy": strategy,
            "symbol": symbol,
            "expiration": expiration,
            "dte": dte,
            "width": width,
            "underlying_price": underlying_price,
            "long_strike": long_leg.strike,
            "short_strike": short_leg.strike,
            "snapshot": {
                "symbol": symbol,
                "expiration": expiration,
                "underlying_price": underlying_price,
                "vix": 18.0,
                "prices_history": [600 + i * 0.1 for i in range(30)],
            },
        }
    ]
    inputs = {
        "symbol": symbol,
        "expiration": expiration,
        "underlying_price": underlying_price,
        "vix": 18.0,
        "request": {"_skip_quote_integrity": True},
    }
    return candidates, inputs


# ═══════════════════════════════════════════════════════════════════════════
# 1. Credit spread: legs[] + spread quotes
# ═══════════════════════════════════════════════════════════════════════════

class TestCreditSpreadCanonicalLegs:
    """Credit spread enriched trades must include a canonical legs[] array
    and derived spread_bid / spread_ask / spread_mid fields."""

    @pytest.fixture()
    def plugin(self) -> CreditSpreadStrategyPlugin:
        return CreditSpreadStrategyPlugin()

    @pytest.fixture()
    def valid_legs(self) -> tuple[FakeContract, FakeContract]:
        short = FakeContract(
            strike=595.0, bid=2.10, ask=2.30, option_type="put",
            delta=-0.28, iv=0.22, open_interest=800, volume=120,
            symbol="SPY250725P00595000",
        )
        long = FakeContract(
            strike=590.0, bid=0.80, ask=1.00, option_type="put",
            delta=-0.15, iv=0.24, open_interest=600, volume=90,
            symbol="SPY250725P00590000",
        )
        return short, long

    def test_legs_array_present_and_correct(
        self, plugin: CreditSpreadStrategyPlugin, valid_legs: tuple
    ) -> None:
        short, long = valid_legs
        candidates, inputs = _make_credit_inputs(short, long)
        enriched = plugin.enrich(candidates, inputs)

        assert len(enriched) >= 1
        trade = enriched[0]
        legs = trade.get("legs")
        assert isinstance(legs, list)
        assert len(legs) == 2

        # Short leg
        sl = legs[0]
        assert sl["name"] == "short_put"
        assert sl["right"] == "put"
        assert sl["side"] == "sell"
        assert sl["strike"] == 595.0
        assert sl["qty"] == 1
        assert sl["bid"] == 2.10
        assert sl["ask"] == 2.30
        assert sl["mid"] == pytest.approx(2.20)
        assert sl["delta"] == pytest.approx(-0.28)
        assert sl["iv"] == pytest.approx(0.22)
        assert sl["open_interest"] == 800
        assert sl["volume"] == 120
        assert sl["occ_symbol"] == "SPY250725P00595000"

        # Long leg
        ll = legs[1]
        assert ll["name"] == "long_put"
        assert ll["right"] == "put"
        assert ll["side"] == "buy"
        assert ll["strike"] == 590.0
        assert ll["qty"] == 1
        assert ll["bid"] == 0.80
        assert ll["ask"] == 1.00

    def test_spread_bid_ask_mid_derived(
        self, plugin: CreditSpreadStrategyPlugin, valid_legs: tuple
    ) -> None:
        """spread_bid = short_bid − long_ask (conservative natural credit)
        spread_ask = short_ask − long_bid (best-case credit)
        spread_mid = average of bid and ask."""
        short, long = valid_legs
        candidates, inputs = _make_credit_inputs(short, long)
        enriched = plugin.enrich(candidates, inputs)
        trade = enriched[0]

        # spread_bid = 2.10 - 1.00 = 1.10
        assert trade["spread_bid"] == pytest.approx(1.10, abs=1e-4)
        # spread_ask = 2.30 - 0.80 = 1.50
        assert trade["spread_ask"] == pytest.approx(1.50, abs=1e-4)
        # spread_mid = (1.10 + 1.50) / 2 = 1.30
        assert trade["spread_mid"] == pytest.approx(1.30, abs=1e-4)

    def test_pop_model_used_normal_cdf(
        self, plugin: CreditSpreadStrategyPlugin, valid_legs: tuple
    ) -> None:
        """Credit spread POP model should be labeled 'normal_cdf'."""
        short, long = valid_legs
        candidates, inputs = _make_credit_inputs(short, long)
        enriched = plugin.enrich(candidates, inputs)
        trade = enriched[0]
        # When p_win_used is present, pop_model_used = "normal_cdf"
        if trade.get("p_win_used") is not None:
            assert trade["pop_model_used"] == "normal_cdf"
        else:
            assert trade["pop_model_used"] == "NONE"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Credit spread: invalid quotes → spread_* = None
# ═══════════════════════════════════════════════════════════════════════════

class TestCreditSpreadInvalidQuotes:
    """When leg quotes are invalid/missing, spread quotes must be None
    and _quote_rejection must be set."""

    @pytest.fixture()
    def plugin(self) -> CreditSpreadStrategyPlugin:
        return CreditSpreadStrategyPlugin()

    def test_missing_short_bid_returns_none_spread(
        self, plugin: CreditSpreadStrategyPlugin
    ) -> None:
        short = FakeContract(
            strike=595.0, bid=None, ask=2.30, option_type="put",
            symbol="SPY250725P00595000",
        )
        long = FakeContract(
            strike=590.0, bid=0.80, ask=1.00, option_type="put",
            symbol="SPY250725P00590000",
        )
        candidates, inputs = _make_credit_inputs(short, long)
        enriched = plugin.enrich(candidates, inputs)
        assert len(enriched) >= 1
        trade = enriched[0]
        assert trade["spread_bid"] is None
        assert trade["spread_ask"] is None
        assert trade["spread_mid"] is None
        assert trade.get("_quote_rejection") is not None

    def test_crossed_market_returns_none_spread(
        self, plugin: CreditSpreadStrategyPlugin
    ) -> None:
        """An inverted market (bid > ask) should produce rejection."""
        short = FakeContract(
            strike=595.0, bid=2.50, ask=2.10, option_type="put",
            symbol="SPY250725P00595000",
        )
        long = FakeContract(
            strike=590.0, bid=0.80, ask=1.00, option_type="put",
            symbol="SPY250725P00590000",
        )
        candidates, inputs = _make_credit_inputs(short, long)
        enriched = plugin.enrich(candidates, inputs)
        assert len(enriched) >= 1
        trade = enriched[0]
        assert trade["spread_bid"] is None
        assert trade["spread_ask"] is None
        assert trade["spread_mid"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. Debit spread: legs[] + spread quotes
# ═══════════════════════════════════════════════════════════════════════════

class TestDebitSpreadCanonicalLegs:
    """Debit spread enriched trades must include a canonical legs[] array."""

    @pytest.fixture()
    def plugin(self) -> DebitSpreadsStrategyPlugin:
        return DebitSpreadsStrategyPlugin()

    @pytest.fixture()
    def call_debit_legs(self) -> tuple[FakeContract, FakeContract]:
        # Call debit: long = lower strike (ATM), short = higher strike (OTM)
        long_leg = FakeContract(
            strike=600.0, bid=5.00, ask=5.40, option_type="call",
            delta=0.55, iv=0.20, open_interest=700, volume=150,
            symbol="SPY250725C00600000",
        )
        short_leg = FakeContract(
            strike=605.0, bid=3.00, ask=3.30, option_type="call",
            delta=0.35, iv=0.21, open_interest=500, volume=100,
            symbol="SPY250725C00605000",
        )
        return long_leg, short_leg

    def test_legs_array_present_call_debit(
        self, plugin: DebitSpreadsStrategyPlugin, call_debit_legs: tuple
    ) -> None:
        long_leg, short_leg = call_debit_legs
        candidates, inputs = _make_debit_inputs(long_leg, short_leg, strategy="call_debit")
        enriched = plugin.enrich(candidates, inputs)

        assert len(enriched) >= 1
        trade = enriched[0]
        legs = trade.get("legs")
        assert isinstance(legs, list)
        assert len(legs) == 2

        # Long leg (buy side)
        ll = legs[0]
        assert ll["name"] == "long_call"
        assert ll["right"] == "call"
        assert ll["side"] == "buy"
        assert ll["strike"] == 600.0
        assert ll["qty"] == 1
        assert ll["bid"] == 5.00
        assert ll["ask"] == 5.40

        # Short leg (sell side)
        sl = legs[1]
        assert sl["name"] == "short_call"
        assert sl["right"] == "call"
        assert sl["side"] == "sell"
        assert sl["strike"] == 605.0

    def test_legs_array_present_put_debit(
        self, plugin: DebitSpreadsStrategyPlugin
    ) -> None:
        """Put debit spread legs should use put right and correct names."""
        long_leg = FakeContract(
            strike=600.0, bid=5.00, ask=5.40, option_type="put",
            delta=-0.55, iv=0.20, open_interest=700, volume=150,
            symbol="SPY250725P00600000",
        )
        short_leg = FakeContract(
            strike=595.0, bid=3.00, ask=3.30, option_type="put",
            delta=-0.35, iv=0.21, open_interest=500, volume=100,
            symbol="SPY250725P00595000",
        )
        candidates, inputs = _make_debit_inputs(
            long_leg, short_leg, strategy="put_debit",
        )
        enriched = plugin.enrich(candidates, inputs)

        assert len(enriched) >= 1
        trade = enriched[0]
        legs = trade.get("legs")
        assert isinstance(legs, list)
        assert len(legs) == 2
        assert legs[0]["name"] == "long_put"
        assert legs[0]["right"] == "put"
        assert legs[1]["name"] == "short_put"
        assert legs[1]["right"] == "put"

    def test_spread_bid_ask_mid_for_debit(
        self, plugin: DebitSpreadsStrategyPlugin, call_debit_legs: tuple
    ) -> None:
        """Debit spread quotes:
        spread_bid = long_bid − short_ask  (what we'd receive exiting)
        spread_ask = long_ask − short_bid  (what we'd pay entering)
        spread_mid = average."""
        long_leg, short_leg = call_debit_legs
        candidates, inputs = _make_debit_inputs(long_leg, short_leg)
        enriched = plugin.enrich(candidates, inputs)
        trade = enriched[0]

        # spread_bid = 5.00 - 3.30 = 1.70
        assert trade["spread_bid"] == pytest.approx(1.70, abs=1e-4)
        # spread_ask = 5.40 - 3.00 = 2.40
        assert trade["spread_ask"] == pytest.approx(2.40, abs=1e-4)
        # spread_mid = (1.70 + 2.40) / 2 = 2.05
        assert trade["spread_mid"] == pytest.approx(2.05, abs=1e-4)

    def test_pop_model_used_present(
        self, plugin: DebitSpreadsStrategyPlugin, call_debit_legs: tuple
    ) -> None:
        """Debit spread should have pop_model_used set."""
        long_leg, short_leg = call_debit_legs
        candidates, inputs = _make_debit_inputs(long_leg, short_leg)
        enriched = plugin.enrich(candidates, inputs)
        trade = enriched[0]
        assert "pop_model_used" in trade
        # Should be one of the known model labels or None
        assert trade["pop_model_used"] in (
            "BREAKEVEN_LOGNORMAL", "DELTA_ADJUSTED", "DELTA_APPROX", None,
        )


# ═══════════════════════════════════════════════════════════════════════════
# 4. Debit spread: invalid quotes
# ═══════════════════════════════════════════════════════════════════════════

class TestDebitSpreadInvalidQuotes:
    @pytest.fixture()
    def plugin(self) -> DebitSpreadsStrategyPlugin:
        return DebitSpreadsStrategyPlugin()

    def test_missing_long_ask_returns_none_spread(
        self, plugin: DebitSpreadsStrategyPlugin
    ) -> None:
        long_leg = FakeContract(
            strike=600.0, bid=5.00, ask=None, option_type="call",
            symbol="SPY250725C00600000",
        )
        short_leg = FakeContract(
            strike=605.0, bid=3.00, ask=3.30, option_type="call",
            symbol="SPY250725C00605000",
        )
        candidates, inputs = _make_debit_inputs(long_leg, short_leg)
        enriched = plugin.enrich(candidates, inputs)
        assert len(enriched) >= 1
        trade = enriched[0]
        assert trade["spread_bid"] is None
        assert trade["spread_ask"] is None
        assert trade["spread_mid"] is None
        assert trade.get("_quote_rejection") is not None


# ═══════════════════════════════════════════════════════════════════════════
# 5. Enrichment counter compatibility with legs[]
# ═══════════════════════════════════════════════════════════════════════════

class TestEnrichmentCounterCompat:
    """Once legs[] is present, the strategy_service counter logic should
    use the multi-leg path and correctly derive spread_quote_derived."""

    def test_credit_spread_has_legs_for_counter(self) -> None:
        """A valid credit spread enriched trade should have legs[] with
        bid/ask populated, enabling multi-leg counter path."""
        plugin = CreditSpreadStrategyPlugin()
        short = FakeContract(
            strike=595.0, bid=2.10, ask=2.30, option_type="put",
            symbol="SPY250725P00595000",
        )
        long = FakeContract(
            strike=590.0, bid=0.80, ask=1.00, option_type="put",
            symbol="SPY250725P00590000",
        )
        candidates, inputs = _make_credit_inputs(short, long)
        enriched = plugin.enrich(candidates, inputs)
        trade = enriched[0]

        legs = trade["legs"]
        assert len(legs) >= 2
        # All legs have bid + ask → counter says all_quotes=True
        assert all(lg["bid"] is not None for lg in legs)
        assert all(lg["ask"] is not None for lg in legs)
        # spread_bid/ask present → spread_derived counts
        assert trade["spread_bid"] is not None
        assert trade["spread_ask"] is not None

    def test_debit_spread_has_legs_for_counter(self) -> None:
        """A valid debit spread enriched trade should have legs[] with
        bid/ask populated, enabling multi-leg counter path."""
        plugin = DebitSpreadsStrategyPlugin()
        long_leg = FakeContract(
            strike=600.0, bid=5.00, ask=5.40, option_type="call",
            delta=0.55, iv=0.20, symbol="SPY250725C00600000",
        )
        short_leg = FakeContract(
            strike=605.0, bid=3.00, ask=3.30, option_type="call",
            delta=0.35, iv=0.21, symbol="SPY250725C00605000",
        )
        candidates, inputs = _make_debit_inputs(long_leg, short_leg)
        enriched = plugin.enrich(candidates, inputs)
        trade = enriched[0]

        legs = trade["legs"]
        assert len(legs) == 2
        assert all(lg["bid"] is not None for lg in legs)
        assert all(lg["ask"] is not None for lg in legs)
        assert trade["spread_bid"] is not None
        assert trade["spread_ask"] is not None
