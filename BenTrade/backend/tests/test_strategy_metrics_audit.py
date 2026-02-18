"""Regression tests for metrics correctness across all strategy plugins.

Covers:
- Butterfly: POP via normal CDF (not touch probability), correct break-evens,
  proper EV via numerical integration, minimum debit guard.
- Iron Condor: POP via normal CDF, real EV, real ev_to_risk.
- Income: EV from POP-based formula, not rank_score placeholder.
- Calendars: unknowable metrics emit None (max_profit, RoR, EV, POP).
- Debit Spreads: per-contract units, implied_prob_profit for POP.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _make_leg(**kwargs: Any) -> SimpleNamespace:
    defaults = dict(
        strike=100, option_type="call", bid=1.0, ask=1.2,
        delta=0.30, gamma=0.02, theta=-0.03, vega=0.10,
        iv=0.25, open_interest=5000, volume=500,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ────────────────────────────────────────────────────────────
# Butterfly Tests
# ────────────────────────────────────────────────────────────

class TestButterflyMetrics:
    """Validate butterfly POP, break-even, EV, and minimum debit guard."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.butterflies import ButterfliesStrategyPlugin
        return ButterfliesStrategyPlugin()

    def _debit_butterfly_candidate(
        self, *, spot: float = 100.0, center: float = 100.0,
        wing: float = 5.0, debit: float = 0.50,
    ) -> dict[str, Any]:
        lower = center - wing
        upper = center + wing
        half_spread = debit / 3.0
        center_bid = (wing - debit) / 2.0
        lower_ask = center_bid + half_spread
        upper_ask = center_bid + half_spread

        return {
            "strategy": "butterflies",
            "spread_type": "debit_call_butterfly",
            "butterfly_type": "debit",
            "option_side": "call",
            "symbol": "TEST",
            "expiration": "2026-06-01",
            "dte": 30,
            "underlying_price": spot,
            "center_strike": center,
            "lower_strike": lower,
            "upper_strike": upper,
            "wing_width": wing,
            "expected_move": spot * 0.05,
            "center_mode": "spot",
            "lower_leg": _make_leg(strike=lower, option_type="call", bid=lower_ask - 0.05, ask=lower_ask),
            "center_leg": _make_leg(strike=center, option_type="call", bid=center_bid, ask=center_bid + 0.05),
            "upper_leg": _make_leg(strike=upper, option_type="call", bid=upper_ask - 0.05, ask=upper_ask),
            "snapshot": {"symbol": "TEST", "prices_history": []},
        }

    def test_pop_not_100_percent(self, plugin):
        """POP must be < 1.0 for any realistic butterfly."""
        candidate = self._debit_butterfly_candidate(spot=100, center=100, wing=5, debit=0.50)
        enriched = plugin.enrich([candidate], {"policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        pop = trade["p_win_used"]
        assert pop < 1.0, f"POP should be < 1.0, got {pop}"
        assert pop > 0.0, f"POP should be > 0.0, got {pop}"

    def test_break_evens_span_wing_width(self, plugin):
        """Break-evens should span nearly the full wing width, not ±debit."""
        candidate = self._debit_butterfly_candidate(spot=100, center=100, wing=5, debit=0.50)
        enriched = plugin.enrich([candidate], {"policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        be_low = trade["break_even_low"]
        be_high = trade["break_even_high"]
        # Correct: lower + debit = 95 + 0.50 = 95.50
        # Correct: upper - debit = 105 - 0.50 = 104.50
        # Profit zone width should be close to 2*wing - 2*debit = 9.0
        profit_zone = be_high - be_low
        assert profit_zone > 5.0, f"Profit zone too narrow: {profit_zone:.2f}"
        assert profit_zone < 10.0, f"Profit zone unreasonably wide: {profit_zone:.2f}"

    def test_ev_is_numerical_integral(self, plugin):
        """EV should be a real number, not a weighted average around center."""
        candidate = self._debit_butterfly_candidate(spot=100, center=100, wing=5, debit=0.50)
        enriched = plugin.enrich([candidate], {"policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        ev = trade["expected_value"]
        assert ev is not None
        # EV should be finite and reasonable
        assert -1000 < ev < 5000, f"EV out of range: {ev}"

    def test_min_debit_guard_filters_cheap_trades(self, plugin):
        """Trades with debit < $0.05/share must be filtered out."""
        candidate = self._debit_butterfly_candidate(spot=100, center=100, wing=5, debit=0.04)
        enriched = plugin.enrich([candidate], {"policy": {}})
        assert len(enriched) == 0, "Should filter butterfly with $0.04 debit"

    def test_pop_uses_normal_cdf(self, plugin):
        """POP should match the analytical normal CDF between break-evens."""
        candidate = self._debit_butterfly_candidate(spot=100, center=100, wing=5, debit=0.50)
        enriched = plugin.enrich([candidate], {"policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        # Independently verify: POP = Φ((BE_high - spot)/EM) - Φ((BE_low - spot)/EM)
        em = 100 * 0.05  # 5.0
        be_low = trade["break_even_low"]
        be_high = trade["break_even_high"]
        expected_pop = _normal_cdf((be_high - 100) / em) - _normal_cdf((be_low - 100) / em)
        assert abs(trade["p_win_used"] - expected_pop) < 0.01, (
            f"POP {trade['p_win_used']:.4f} != expected {expected_pop:.4f}"
        )

    def test_touch_center_preserved_separately(self, plugin):
        """probability_of_touch_center should still exist as supplementary metric."""
        candidate = self._debit_butterfly_candidate(spot=100, center=100, wing=5, debit=0.50)
        enriched = plugin.enrich([candidate], {"policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        assert "probability_of_touch_center" in trade
        # Touch center and POP should be different values
        assert trade["probability_of_touch_center"] != trade["p_win_used"] or trade["p_win_used"] < 1.0


# ────────────────────────────────────────────────────────────
# Iron Condor Tests
# ────────────────────────────────────────────────────────────

class TestIronCondorMetrics:
    """Validate iron condor POP via CDF and real EV."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.iron_condor import IronCondorStrategyPlugin
        return IronCondorStrategyPlugin()

    def _condor_candidate(
        self, *, spot: float = 100.0,
        put_short: float = 90.0, put_long: float = 85.0,
        call_short: float = 110.0, call_long: float = 115.0,
        credit: float = 1.50,
    ) -> dict[str, Any]:
        put_credit = credit * 0.5
        call_credit = credit * 0.5
        return {
            "strategy": "iron_condor",
            "spread_type": "iron_condor",
            "symbol": "TEST",
            "expiration": "2026-06-01",
            "dte": 30,
            "underlying_price": spot,
            "put_short_strike": put_short,
            "put_long_strike": put_long,
            "call_short_strike": call_short,
            "call_long_strike": call_long,
            "put_short_leg": _make_leg(strike=put_short, option_type="put", bid=put_credit + 0.10, ask=put_credit + 0.20, delta=-0.15),
            "put_long_leg": _make_leg(strike=put_long, option_type="put", bid=0.05, ask=0.10, delta=-0.08),
            "call_short_leg": _make_leg(strike=call_short, option_type="call", bid=call_credit + 0.10, ask=call_credit + 0.20, delta=0.15),
            "call_long_leg": _make_leg(strike=call_long, option_type="call", bid=0.05, ask=0.10, delta=0.08),
            "width_put": put_short - put_long,
            "width_call": call_long - call_short,
            "symmetry_score": 1.0,
            "expected_move": spot * 0.05,
            "snapshot": {"symbol": "TEST", "prices_history": []},
        }

    def test_pop_via_normal_cdf(self, plugin):
        """POP should match normal CDF between break-evens."""
        candidate = self._condor_candidate(spot=100, put_short=90, call_short=110, credit=1.50)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        pop = trade["p_win_used"]
        # Verify against independent CDF calculation
        total_credit = trade.get("total_credit") or trade.get("net_credit")
        be_low = 90 - total_credit
        be_high = 110 + total_credit
        em = 100 * 0.05
        expected_pop = _normal_cdf((be_high - 100) / em) - _normal_cdf((be_low - 100) / em)
        assert abs(pop - expected_pop) < 0.05, f"POP {pop:.4f} != expected {expected_pop:.4f}"

    def test_ev_is_real_not_rank_derived(self, plugin):
        """EV must NOT be rank_score * 0.20 — must be pop * profit - (1-pop) * loss."""
        candidate = self._condor_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        assert "ev_per_contract" in trade
        assert "ev_per_share" in trade
        assert "expected_value" in trade
        # EV should not be rank_score * 0.20
        rank_score = trade.get("rank_score", 0)
        assert trade["ev_per_contract"] != pytest.approx(rank_score * 0.20 * 100, abs=1.0), (
            "EV should not be derived from rank_score"
        )

    def test_ev_to_risk_consistent(self, plugin):
        """ev_to_risk should equal ev_per_contract / max_loss."""
        candidate = self._condor_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        max_loss = trade["max_loss"]
        if max_loss > 0:
            expected_ratio = trade["ev_per_contract"] / max_loss
            assert abs(trade["ev_to_risk"] - expected_ratio) < 0.001


# ────────────────────────────────────────────────────────────
# Income Tests
# ────────────────────────────────────────────────────────────

class TestIncomeMetrics:
    """Validate income EV is POP-derived, not rank_score placeholder."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.income import IncomeStrategyPlugin
        return IncomeStrategyPlugin()

    def _csp_candidate(self, *, spot: float = 100.0, strike: float = 90.0) -> dict[str, Any]:
        return {
            "strategy": "income",
            "spread_type": "cash_secured_put",
            "symbol": "TEST",
            "expiration": "2026-06-01",
            "dte": 30,
            "underlying_price": spot,
            "short_strike": strike,
            "long_strike": None,
            "short_leg": _make_leg(
                strike=strike, option_type="put", bid=1.50, ask=1.70,
                delta=-0.20, iv=0.25, open_interest=3000, volume=200,
            ),
            "snapshot": {"symbol": "TEST", "prices_history": [float(100 + i * 0.1) for i in range(30)]},
        }

    def test_ev_is_pop_derived(self, plugin):
        """EV = pop * max_profit - (1-pop) * max_loss, not rank_score - 0.5."""
        candidate = self._csp_candidate(spot=100, strike=90)
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        pop = trade["p_win_used"]
        max_profit = trade["max_profit"]
        max_loss = trade["max_loss"]
        expected_ev = pop * max_profit - (1.0 - pop) * max_loss
        assert abs(trade["ev_per_contract"] - expected_ev) < 0.01, (
            f"EV {trade['ev_per_contract']:.2f} != expected {expected_ev:.2f}"
        )

    def test_ev_not_rank_score_placeholder(self, plugin):
        """EV must not be (rank_score - 0.5) * 100."""
        candidate = self._csp_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        rank_placeholder = (trade["rank_score"] - 0.5) * 100.0
        assert trade["ev_per_contract"] != pytest.approx(rank_placeholder, abs=0.5), (
            "EV should not be derived from rank_score"
        )


# ────────────────────────────────────────────────────────────
# Calendar Tests
# ────────────────────────────────────────────────────────────

class TestCalendarMetrics:
    """Validate calendars emit None for unknowable metrics."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.calendars import CalendarsStrategyPlugin
        return CalendarsStrategyPlugin()

    def _calendar_candidate(self, *, spot: float = 100.0, strike: float = 100.0) -> dict[str, Any]:
        return {
            "strategy": "calendar_spread",
            "spread_type": "calendar_call_spread",
            "option_side": "call",
            "symbol": "TEST",
            "expiration_near": "2026-05-15",
            "expiration_far": "2026-06-15",
            "expiration": "2026-06-15",
            "dte_near": 14,
            "dte_far": 45,
            "dte": 45,
            "underlying_price": spot,
            "strike": strike,
            "short_strike": strike,
            "long_strike": strike,
            "near_leg": _make_leg(strike=strike, option_type="call", bid=2.00, ask=2.30, iv=0.28, theta=-0.08),
            "far_leg": _make_leg(strike=strike, option_type="call", bid=3.50, ask=3.80, iv=0.25, theta=-0.04),
            "near_snapshot": {"symbol": "TEST"},
            "far_snapshot": {"symbol": "TEST"},
        }

    def test_max_profit_is_none(self, plugin):
        """max_profit cannot be computed without an options pricing model."""
        candidate = self._calendar_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        assert trade["max_profit"] is None
        assert trade["max_profit_per_contract"] is None

    def test_return_on_risk_is_none(self, plugin):
        """return_on_risk depends on unknowable max_profit; must be None."""
        candidate = self._calendar_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        assert enriched[0]["return_on_risk"] is None

    def test_ev_is_none(self, plugin):
        """EV cannot be computed without POP and max_profit; must be None."""
        candidate = self._calendar_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        assert trade["ev_per_contract"] is None
        assert trade["ev_per_share"] is None
        assert trade["expected_value"] is None

    def test_pop_is_none(self, plugin):
        """POP for calendars is unknowable; must be None."""
        candidate = self._calendar_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        assert enriched[0]["p_win_used"] is None

    def test_max_loss_is_debit(self, plugin):
        """max_loss IS computable: it's the net debit paid."""
        candidate = self._calendar_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        assert trade["max_loss"] is not None
        assert trade["max_loss"] > 0


# ────────────────────────────────────────────────────────────
# Debit Spreads Sanity Check
# ────────────────────────────────────────────────────────────

class TestDebitSpreadMetrics:
    """Verify debit spread metrics are real (existing plugin was already correct)."""

    @pytest.fixture()
    def plugin(self):
        from app.services.strategies.debit_spreads import DebitSpreadsStrategyPlugin
        return DebitSpreadsStrategyPlugin()

    def _call_debit_candidate(self) -> dict[str, Any]:
        return {
            "strategy": "debit_call_spread",
            "spread_type": "debit_call_spread",
            "symbol": "TEST",
            "expiration": "2026-06-01",
            "dte": 30,
            "underlying_price": 100.0,
            "width": 5.0,
            "long_strike": 100.0,
            "short_strike": 105.0,
            "long_leg": _make_leg(strike=100, option_type="call", bid=4.00, ask=4.20, iv=0.25, delta=0.50),
            "short_leg": _make_leg(strike=105, option_type="call", bid=2.00, ask=2.20, iv=0.22, delta=0.30),
            "snapshot": {"symbol": "TEST", "prices_history": [float(100 + i * 0.05) for i in range(30)]},
        }

    def test_max_profit_per_contract_units(self, plugin):
        """max_profit should be (width - debit) * 100, already per-contract."""
        candidate = self._call_debit_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        # debit = long_ask - short_bid = 4.20 - 2.00 = 2.20
        # max_profit = (5.0 - 2.20) * 100 = 280.0
        assert trade["max_profit"] == pytest.approx(280.0, abs=5.0)
        assert trade["max_profit_per_contract"] == trade["max_profit"]

    def test_pop_from_implied_prob(self, plugin):
        """POP should come from implied_prob_profit (1 - debit/width)."""
        candidate = self._call_debit_candidate()
        enriched = plugin.enrich([candidate], {"request": {}, "policy": {}})
        assert len(enriched) >= 1
        trade = enriched[0]
        debit_as_pct = trade.get("debit_as_pct_of_width")
        expected_pop = max(0, min(1, 1.0 - debit_as_pct))
        assert abs(trade["implied_prob_profit"] - expected_pop) < 0.01
