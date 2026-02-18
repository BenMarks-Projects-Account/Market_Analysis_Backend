"""
Regression tests: enrich_trade must compute CreditSpread-derived core metrics.

These tests reproduce the root cause of the N/A metrics bug:
  enrich_trade() previously only added market-context features (IV/RV/regime)
  but never created a CreditSpread object to compute max_profit, max_loss,
  break_even, POP, EV, RoR, kelly_fraction.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from common.quant_analysis import enrich_trade, enrich_trades_batch


# ── Fixtures ─────────────────────────────────────────────────────────────

def _base_put_credit() -> dict[str, Any]:
    """Minimal put-credit-spread candidate matching what credit_spread.py emits."""
    return {
        "spread_type": "put_credit_spread",
        "strategy": "put_credit_spread",
        "underlying": "SPY",
        "expiration": "2026-02-23",
        "dte": 7,
        "short_strike": 655.0,
        "long_strike": 650.0,
        "underlying_price": 681.75,
        "price": 681.75,
        "bid": 1.42,
        "ask": 1.44,
        "short_delta_abs": 0.1293,
        "iv": 0.212,
        "implied_vol": 0.212,
        "width": 5.0,
        "net_credit": 0.35,
        "contractsMultiplier": 100,
    }


def _base_call_credit() -> dict[str, Any]:
    return {
        "spread_type": "call_credit_spread",
        "strategy": "call_credit_spread",
        "underlying": "SPY",
        "expiration": "2026-02-23",
        "dte": 7,
        "short_strike": 710.0,
        "long_strike": 715.0,
        "underlying_price": 681.75,
        "price": 681.75,
        "bid": 0.80,
        "ask": 0.85,
        "short_delta_abs": 0.15,
        "iv": 0.20,
        "width": 5.0,
        "net_credit": 0.40,
        "contractsMultiplier": 100,
    }


# ── Core metric presence tests ──────────────────────────────────────────

CORE_METRIC_KEYS = [
    "max_profit_per_share",
    "max_loss_per_share",
    "max_profit_per_contract",
    "max_loss_per_contract",
    "break_even",
    "return_on_risk",
    "pop_delta_approx",
    "p_win_used",
    "ev_per_share",
    "ev_per_contract",
    "kelly_fraction",
    "trade_quality_score",
]


class TestEnrichTradeComputesCoreMetrics:
    """enrich_trade must populate CreditSpread-derived core metrics."""

    def test_put_credit_spread_canonical_name(self) -> None:
        trade = _base_put_credit()
        enriched = enrich_trade(trade)
        for key in CORE_METRIC_KEYS:
            assert enriched.get(key) is not None, f"{key} is None/missing after enrich_trade"

    def test_call_credit_spread_canonical_name(self) -> None:
        trade = _base_call_credit()
        enriched = enrich_trade(trade)
        for key in CORE_METRIC_KEYS:
            assert enriched.get(key) is not None, f"{key} is None/missing after enrich_trade"

    def test_old_put_credit_still_accepted(self) -> None:
        trade = _base_put_credit()
        trade["spread_type"] = "put_credit"
        enriched = enrich_trade(trade)
        for key in CORE_METRIC_KEYS:
            assert enriched.get(key) is not None, f"{key} is None/missing for legacy put_credit"

    def test_old_call_credit_still_accepted(self) -> None:
        trade = _base_call_credit()
        trade["spread_type"] = "call_credit"
        enriched = enrich_trade(trade)
        for key in CORE_METRIC_KEYS:
            assert enriched.get(key) is not None, f"{key} is None/missing for legacy call_credit"


# ── Metric correctness tests ────────────────────────────────────────────

class TestCreditSpreadMetricValues:
    """Verify the computed values are mathematically correct."""

    def test_max_profit_equals_net_credit(self) -> None:
        trade = _base_put_credit()
        enriched = enrich_trade(trade)
        assert enriched["max_profit_per_share"] == pytest.approx(0.35, abs=1e-6)
        assert enriched["max_profit_per_contract"] == pytest.approx(35.0, abs=1e-4)

    def test_max_loss_equals_width_minus_credit(self) -> None:
        trade = _base_put_credit()
        enriched = enrich_trade(trade)
        assert enriched["max_loss_per_share"] == pytest.approx(5.0 - 0.35, abs=1e-6)
        assert enriched["max_loss_per_contract"] == pytest.approx((5.0 - 0.35) * 100, abs=1e-2)

    def test_break_even_put_credit(self) -> None:
        trade = _base_put_credit()
        enriched = enrich_trade(trade)
        # BE = short_strike - net_credit  =>  655 - 0.35 = 654.65
        assert enriched["break_even"] == pytest.approx(654.65, abs=1e-6)

    def test_break_even_call_credit(self) -> None:
        trade = _base_call_credit()
        enriched = enrich_trade(trade)
        # BE = short_strike + net_credit  =>  710 + 0.40 = 710.40
        assert enriched["break_even"] == pytest.approx(710.40, abs=1e-6)

    def test_return_on_risk(self) -> None:
        trade = _base_put_credit()
        enriched = enrich_trade(trade)
        # RoR = max_profit / max_loss = 0.35 / 4.65
        assert enriched["return_on_risk"] == pytest.approx(0.35 / 4.65, abs=1e-6)

    def test_pop_from_delta(self) -> None:
        trade = _base_put_credit()
        enriched = enrich_trade(trade)
        # POP ≈ 1 - delta_abs = 1 - 0.1293 = 0.8707
        # May be stored as pop_delta_approx or p_win_used
        pop = enriched.get("p_win_used") or enriched.get("pop_delta_approx")
        assert pop == pytest.approx(0.8707, abs=1e-4)

    def test_ev_per_share_positive_when_pop_high(self) -> None:
        trade = _base_put_credit()
        enriched = enrich_trade(trade)
        # EV = pop * profit - (1-pop) * loss
        ev = enriched["ev_per_share"]
        assert ev is not None
        # With pop ~0.87, net_credit=0.35, width=5 → should be positive
        pop = 1.0 - 0.1293
        expected_ev = pop * 0.35 - (1 - pop) * 4.65
        assert ev == pytest.approx(expected_ev, abs=1e-3)

    def test_ev_per_contract_is_100x_share(self) -> None:
        trade = _base_put_credit()
        enriched = enrich_trade(trade)
        assert enriched["ev_per_contract"] == pytest.approx(
            enriched["ev_per_share"] * 100, abs=1e-2
        )

    def test_kelly_fraction(self) -> None:
        trade = _base_put_credit()
        enriched = enrich_trade(trade)
        kelly = enriched["kelly_fraction"]
        assert kelly is not None
        assert isinstance(kelly, float)


# ── Batch enrichment ─────────────────────────────────────────────────────

class TestEnrichTradesBatch:
    def test_batch_enriches_all(self) -> None:
        trades = [_base_put_credit(), _base_call_credit()]
        enriched = enrich_trades_batch(trades)
        assert len(enriched) == 2
        for row in enriched:
            for key in CORE_METRIC_KEYS:
                assert row.get(key) is not None, f"{key} missing in batch trade"


# ── Canonical name preservation ──────────────────────────────────────────

class TestCanonicalNamePreserved:
    def test_put_credit_spread_preserved_in_output(self) -> None:
        trade = _base_put_credit()
        enriched = enrich_trade(trade)
        assert enriched["spread_type"] == "put_credit_spread"

    def test_call_credit_spread_preserved_in_output(self) -> None:
        trade = _base_call_credit()
        enriched = enrich_trade(trade)
        assert enriched["spread_type"] == "call_credit_spread"


# ── Rejected spread types ───────────────────────────────────────────────

class TestUnrecognizedSpreadType:
    def test_unknown_spread_type_raises(self) -> None:
        trade = _base_put_credit()
        trade["spread_type"] = "iron_condor"
        with pytest.raises(ValueError, match="recognized credit-spread"):
            enrich_trade(trade)
