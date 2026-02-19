"""Unit tests for credit spread pricing math: net_credit, spread_width,
quote validation, and rejection codes.

Uses a small synthetic option chain with known values.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.services.strategies.credit_spread import CreditSpreadStrategyPlugin
from common.quant_analysis import CreditSpread


# ---------------------------------------------------------------------------
# Helpers: synthetic option contract objects
# ---------------------------------------------------------------------------

@dataclass
class FakeContract:
    strike: float
    bid: float | None
    ask: float | None
    option_type: str = "put"
    delta: float | None = None
    iv: float | None = None
    open_interest: int = 1000
    volume: int = 100


# ---------------------------------------------------------------------------
# 1. CreditSpread model: correct net_credit and spread_width
# ---------------------------------------------------------------------------

class TestCreditSpreadModel:
    def test_valid_put_credit_spread(self) -> None:
        cs = CreditSpread(
            spread_type="put_credit",
            underlying_price=600.0,
            short_strike=595.0,
            long_strike=590.0,
            net_credit=1.20,
            dte=30,
            short_delta_abs=0.25,
        )
        cs.validate()
        assert cs.width == 5.0
        assert cs.max_profit_per_share == 1.20
        assert cs.max_loss_per_share == pytest.approx(3.80)
        assert cs.break_even == pytest.approx(593.80)

    def test_net_credit_exceeds_width_raises(self) -> None:
        cs = CreditSpread(
            spread_type="put_credit",
            underlying_price=600.0,
            short_strike=595.0,
            long_strike=593.0,
            net_credit=2.50,
            dte=30,
        )
        with pytest.raises(ValueError, match="net_credit must be < spread width"):
            cs.validate()

    def test_net_credit_within_epsilon_of_width_raises(self) -> None:
        """net_credit within 0.01 of width should still be rejected."""
        cs = CreditSpread(
            spread_type="put_credit",
            underlying_price=600.0,
            short_strike=595.0,
            long_strike=593.0,
            net_credit=1.995,   # width=2.0, 1.995 > 2.0 - 0.01
            dte=30,
        )
        with pytest.raises(ValueError, match="net_credit must be < spread width"):
            cs.validate()

    def test_net_credit_safely_below_width_passes(self) -> None:
        cs = CreditSpread(
            spread_type="put_credit",
            underlying_price=600.0,
            short_strike=595.0,
            long_strike=593.0,
            net_credit=1.50,
            dte=30,
        )
        cs.validate()
        assert cs.width == 2.0
        assert cs.max_profit_per_share == 1.50

    def test_units_are_per_share_dollars(self) -> None:
        """width and net_credit are both in per-share dollars (not per-contract)."""
        cs = CreditSpread(
            spread_type="put_credit",
            underlying_price=600.0,
            short_strike=598.0,
            long_strike=595.0,
            net_credit=0.80,
            dte=30,
        )
        cs.validate()
        assert cs.width == 3.0    # dollars per share
        assert cs.max_profit_per_share == 0.80   # dollars per share


# ---------------------------------------------------------------------------
# 2. CreditSpreadStrategyPlugin.enrich — net_credit using bid/ask
# ---------------------------------------------------------------------------

class TestPluginEnrich:
    plugin = CreditSpreadStrategyPlugin()

    def _make_candidate(
        self,
        short: FakeContract,
        long: FakeContract,
        underlying_price: float = 600.0,
    ) -> dict[str, Any]:
        return {
            "short_leg": short,
            "long_leg": long,
            "strategy": "put_credit_spread",
            "width": abs(short.strike - long.strike),
            "snapshot": {
                "symbol": "SPY",
                "underlying_price": underlying_price,
                "vix": 18.0,
                "expiration": "2026-03-20",
            },
        }

    def _make_inputs(self, underlying_price: float = 600.0) -> dict[str, Any]:
        return {
            "symbol": "SPY",
            "expiration": "2026-03-20",
            "underlying_price": underlying_price,
            "vix": 18.0,
            "prices_history": [598.0 + i * 0.1 for i in range(100)],
        }

    def test_correct_net_credit_from_bid_ask(self) -> None:
        """net_credit = short_bid - long_ask (conservative fill)."""
        short = FakeContract(strike=595.0, bid=1.50, ask=1.80, delta=-0.25)
        long = FakeContract(strike=590.0, bid=0.40, ask=0.60, delta=-0.12)
        candidates = [self._make_candidate(short, long)]
        enriched = self.plugin.enrich(candidates, self._make_inputs())
        assert len(enriched) == 1
        assert enriched[0]["net_credit"] == pytest.approx(0.90)
        assert enriched[0]["width"] == 5.0

    def test_ask_lt_bid_on_short_leg_rejected(self) -> None:
        """Short leg with inverted quotes gets _quote_rejection."""
        short = FakeContract(strike=595.0, bid=1.80, ask=1.50)  # inverted
        long = FakeContract(strike=590.0, bid=0.40, ask=0.60)
        candidates = [self._make_candidate(short, long)]
        enriched = self.plugin.enrich(candidates, self._make_inputs())
        assert len(enriched) == 1
        assert enriched[0]["_quote_rejection"] == "ASK_LT_BID:short_leg"

    def test_ask_lt_bid_on_long_leg_rejected(self) -> None:
        """Long leg with inverted quotes gets _quote_rejection."""
        short = FakeContract(strike=595.0, bid=1.50, ask=1.80)
        long = FakeContract(strike=590.0, bid=0.80, ask=0.30)  # inverted
        candidates = [self._make_candidate(short, long)]
        enriched = self.plugin.enrich(candidates, self._make_inputs())
        assert len(enriched) == 1
        assert enriched[0]["_quote_rejection"] == "ASK_LT_BID:long_leg"

    def test_missing_short_bid_rejected(self) -> None:
        short = FakeContract(strike=595.0, bid=None, ask=1.80)
        long = FakeContract(strike=590.0, bid=0.40, ask=0.60)
        candidates = [self._make_candidate(short, long)]
        enriched = self.plugin.enrich(candidates, self._make_inputs())
        assert enriched[0]["_quote_rejection"] == "MISSING_QUOTES:short_bid"

    def test_zero_short_bid_rejected(self) -> None:
        short = FakeContract(strike=595.0, bid=0.0, ask=0.10)
        long = FakeContract(strike=590.0, bid=0.02, ask=0.05)
        candidates = [self._make_candidate(short, long)]
        enriched = self.plugin.enrich(candidates, self._make_inputs())
        assert enriched[0]["_quote_rejection"] == "MISSING_QUOTES:short_bid"

    def test_net_credit_ge_width_rejected(self) -> None:
        """Credit exceeding width is rejected before CreditSpread model."""
        short = FakeContract(strike=595.0, bid=3.50, ask=3.80, delta=-0.40)
        long = FakeContract(strike=593.0, bid=0.30, ask=0.50, delta=-0.15)
        # width = 2.0, credit = 3.50 - 0.50 = 3.00 > 2.0 - 0.01
        candidates = [self._make_candidate(short, long)]
        enriched = self.plugin.enrich(candidates, self._make_inputs())
        assert enriched[0]["_quote_rejection"] == "NET_CREDIT_GE_WIDTH"

    def test_missing_delta_does_not_default_to_zero(self) -> None:
        """When delta is None, short_delta_abs should be None (not 0.0)."""
        short = FakeContract(strike=595.0, bid=1.50, ask=1.80, delta=None)
        long = FakeContract(strike=590.0, bid=0.40, ask=0.60, delta=None)
        candidates = [self._make_candidate(short, long)]
        enriched = self.plugin.enrich(candidates, self._make_inputs())
        assert enriched[0]["short_delta_abs"] is None


# ---------------------------------------------------------------------------
# 3. Evaluate: quote-rejected candidates produce clear reason codes
# ---------------------------------------------------------------------------

class TestPluginEvaluate:
    plugin = CreditSpreadStrategyPlugin()

    def test_quote_rejected_candidate_fails_evaluate(self) -> None:
        """A candidate flagged during enrich gets fast-rejected in evaluate."""
        trade = {"_quote_rejection": "ASK_LT_BID:short_leg", "net_credit": None}
        ok, reasons = self.plugin.evaluate(trade)
        assert not ok
        assert reasons == ["ASK_LT_BID:short_leg"]

    def test_metrics_failed_candidate_includes_reason(self) -> None:
        trade = {
            "data_warning": "CreditSpread metrics unavailable: net_credit must be < spread width",
            "net_credit": 3.0,
            "width": 2.0,
            "p_win_used": None,
            "ev_per_share": None,
            "ev_to_risk": None,
            "return_on_risk": None,
            "bid_ask_spread_pct": 0.05,
            "open_interest": 1000,
            "volume": 200,
        }
        ok, reasons = self.plugin.evaluate(trade)
        assert not ok
        assert "CREDIT_SPREAD_METRICS_FAILED" in reasons


# ---------------------------------------------------------------------------
# 4. build_candidates: multi-snapshot and distance_min/distance_max
# ---------------------------------------------------------------------------

class TestBuildCandidates:
    plugin = CreditSpreadStrategyPlugin()

    @staticmethod
    def _make_chain(underlying_price: float, strikes: list[float]) -> list[FakeContract]:
        return [
            FakeContract(
                strike=s,
                bid=max(underlying_price - s, 0.10),
                ask=max(underlying_price - s, 0.10) + 0.10,
                delta=-(s / underlying_price) * 0.5 if s < underlying_price else -0.01,
            )
            for s in strikes
        ]

    def test_multiple_snapshots_produce_candidates_from_each(self) -> None:
        """build_candidates should iterate all snapshots, not just the first."""
        snap1 = {
            "symbol": "SPY",
            "expiration": "2026-03-06",
            "underlying_price": 600.0,
            "contracts": self._make_chain(600.0, [590.0, 587.0, 585.0, 580.0, 575.0]),
        }
        snap2 = {
            "symbol": "QQQ",
            "expiration": "2026-03-06",
            "underlying_price": 500.0,
            "contracts": self._make_chain(500.0, [490.0, 487.0, 485.0, 480.0, 475.0]),
        }
        inputs = {
            "snapshots": [snap1, snap2],
            "request": {"width_min": 3.0, "width_max": 5.0, "distance_min": 0.01, "distance_max": 0.10},
        }
        candidates = self.plugin.build_candidates(inputs)
        symbols = {c["snapshot"].get("symbol") for c in candidates}
        assert "SPY" in symbols, "Should produce candidates from SPY snapshot"
        assert "QQQ" in symbols, "Should produce candidates from QQQ snapshot"

    def test_distance_min_max_from_payload(self) -> None:
        """distance_min / distance_max from request payload are respected."""
        # underlying=600, strikes at 582 (3% OTM) and 540 (10% OTM)
        snap = {
            "symbol": "SPY",
            "expiration": "2026-03-06",
            "underlying_price": 600.0,
            "contracts": self._make_chain(600.0, [582.0, 579.0, 540.0, 537.0]),
        }
        inputs = {
            "snapshots": [snap],
            "request": {"width_min": 3.0, "width_max": 3.0, "distance_min": 0.05, "distance_max": 0.12},
        }
        candidates = self.plugin.build_candidates(inputs)
        # 582 is 3% OTM → excluded (below distance_min=5%)
        # 540 is 10% OTM → included
        short_strikes = {c.get("short_leg").strike for c in candidates}
        assert 582.0 not in short_strikes, "3% OTM should be excluded by distance_min=5%"
        assert 540.0 in short_strikes, "10% OTM should be included"
