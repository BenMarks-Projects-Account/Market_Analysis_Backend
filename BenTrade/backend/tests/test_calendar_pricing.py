"""Tests for calendar spread pricing, gating, and strategy completeness.

Covers:
- spread_mid / spread_natural / spread_mark computation
- net_debit populated and consistent with max_loss
- max_profit / pop / expected_value / return_on_risk intentionally None
- execution_invalid when quotes missing
- rank_score = 0 when execution_invalid
- evaluate() rejects with strategy_not_ready (POP/EV not implemented)
- evaluate() rejects with execution_invalid when pricing missing
- engine_gate_status.passed = false for rejected trades
- sanity metrics (debit_as_pct_of_underlying, etc.)
- computed_metrics correctly reports metrics_status.ready = false for calendars
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.strategies.calendars import CalendarsStrategyPlugin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_leg(bid: float | None, ask: float | None, **kwargs: Any) -> SimpleNamespace:
    """Create a minimal option leg stub with bid/ask and optional greeks."""
    defaults = {
        "strike": kwargs.pop("strike", 686.0),
        "option_type": kwargs.pop("option_type", "call"),
        "open_interest": kwargs.pop("open_interest", 1000),
        "volume": kwargs.pop("volume", 100),
        "delta": kwargs.pop("delta", 0.50),
        "gamma": kwargs.pop("gamma", 0.02),
        "theta": kwargs.pop("theta", -0.05),
        "vega": kwargs.pop("vega", 0.15),
        "iv": kwargs.pop("iv", 0.18),
        "symbol": kwargs.pop("symbol", "SPY260313C00686000"),
    }
    defaults.update(kwargs)
    return SimpleNamespace(bid=bid, ask=ask, **defaults)


def _build_calendar_candidate(
    near_leg: SimpleNamespace,
    far_leg: SimpleNamespace,
    *,
    spot: float = 686.0,
    strike: float = 686.0,
    dte_near: int = 14,
    dte_far: int = 32,
    side: str = "call",
) -> dict[str, Any]:
    """Build a single calendar spread candidate dict."""
    return {
        "strategy": "calendar_spread",
        "spread_type": f"calendar_{side}_spread",
        "option_side": side,
        "symbol": "SPY",
        "expiration_near": "2026-03-13",
        "expiration_far": "2026-03-31",
        "expiration": "2026-03-31",
        "dte_near": dte_near,
        "dte_far": dte_far,
        "dte": dte_far,
        "underlying_price": spot,
        "strike": strike,
        "short_strike": strike,
        "long_strike": strike,
        "near_leg": near_leg,
        "far_leg": far_leg,
        "near_snapshot": {"symbol": "SPY", "expiration": "2026-03-13"},
        "far_snapshot": {"symbol": "SPY", "expiration": "2026-03-31"},
    }


def _enrich_one(candidate: dict[str, Any]) -> dict[str, Any] | None:
    """Run enrich on a single candidate and return the enriched row."""
    plugin = CalendarsStrategyPlugin()
    enriched = plugin.enrich([candidate], {"policy": {}})
    return enriched[0] if enriched else None


# ---------------------------------------------------------------------------
# Test: spread pricing computation
# ---------------------------------------------------------------------------

class TestCalendarPricing:
    """SPY 2026-03-13 → 2026-03-31 K686 (14/32 DTE) call calendar."""

    @pytest.fixture()
    def example_legs(self):
        # Near expiration (sell, 14 DTE): bid=5.20, ask=5.40
        near = _make_leg(bid=5.20, ask=5.40, strike=686, theta=-0.08)
        # Far expiration (buy, 32 DTE):  bid=8.50, ask=8.90
        far = _make_leg(bid=8.50, ask=8.90, strike=686, theta=-0.04)
        return near, far

    @pytest.fixture()
    def enriched(self, example_legs):
        near, far = example_legs
        cand = _build_calendar_candidate(near, far)
        result = _enrich_one(cand)
        assert result is not None, "enrich dropped the candidate unexpectedly"
        return result

    def test_spread_mid(self, enriched):
        # spread_mid = mid(far) - mid(near)
        # mid(near) = (5.20 + 5.40) / 2 = 5.30
        # mid(far) = (8.50 + 8.90) / 2 = 8.70
        # spread_mid = 8.70 - 5.30 = 3.40
        assert enriched["spread_mid"] == pytest.approx(3.40, abs=0.01)

    def test_spread_natural(self, enriched):
        # spread_natural = ask(far) - bid(near)  (worst fill)
        # = 8.90 - 5.20 = 3.70
        assert enriched["spread_natural"] == pytest.approx(3.70, abs=0.01)

    def test_spread_mark(self, enriched):
        # spread_mark = spread_mid initially
        assert enriched["spread_mark"] == enriched["spread_mid"]

    def test_net_debit_equals_spread_mid(self, enriched):
        # net_debit = spread_mid (primary)
        assert enriched["net_debit"] is not None
        assert enriched["net_debit"] == enriched["spread_mid"]

    def test_net_credit_null(self, enriched):
        # Debit strategy → net_credit = None
        assert enriched["net_credit"] is None

    def test_max_loss_equals_debit(self, enriched):
        # max_loss = net_debit × 100
        debit = enriched["net_debit"]
        assert enriched["max_loss"] == pytest.approx(debit * 100.0, abs=0.01)

    def test_per_leg_quotes_stored(self, enriched):
        """Per-leg bid/ask/mid must be traceable."""
        assert enriched["near_bid"] == pytest.approx(5.20)
        assert enriched["near_ask"] == pytest.approx(5.40)
        assert enriched["near_mid"] == pytest.approx(5.30)
        assert enriched["far_bid"] == pytest.approx(8.50)
        assert enriched["far_ask"] == pytest.approx(8.90)
        assert enriched["far_mid"] == pytest.approx(8.70)


# ---------------------------------------------------------------------------
# Test: strategy metrics intentionally None for calendars
# ---------------------------------------------------------------------------

class TestStrategyMetricsIntentionallyNull:
    """Calendar max_profit / pop / EV are unknowable without pricing model."""

    @pytest.fixture()
    def enriched(self):
        near = _make_leg(bid=5.20, ask=5.40, strike=686)
        far = _make_leg(bid=8.50, ask=8.90, strike=686)
        cand = _build_calendar_candidate(near, far)
        return _enrich_one(cand)

    def test_max_profit_none(self, enriched):
        assert enriched["max_profit"] is None

    def test_max_profit_per_contract_none(self, enriched):
        assert enriched["max_profit_per_contract"] is None

    def test_return_on_risk_none(self, enriched):
        assert enriched["return_on_risk"] is None

    def test_expected_value_none(self, enriched):
        assert enriched["expected_value"] is None

    def test_ev_per_contract_none(self, enriched):
        assert enriched["ev_per_contract"] is None

    def test_p_win_used_none(self, enriched):
        assert enriched["p_win_used"] is None

    def test_pop_model_used_is_none_source(self, enriched):
        assert enriched["pop_model_used"] == "NONE"


# ---------------------------------------------------------------------------
# Test: execution_invalid when pricing missing
# ---------------------------------------------------------------------------

class TestExecutionInvalid:

    def test_all_quotes_missing(self):
        """All leg quotes = None → execution_invalid = True."""
        near = _make_leg(bid=None, ask=None, strike=686)
        far = _make_leg(bid=None, ask=None, strike=686)
        cand = _build_calendar_candidate(near, far)
        result = _enrich_one(cand)
        assert result is not None
        assert result["execution_invalid"] is True
        assert result["execution_invalid_reason"] == "leg_quote_missing"
        assert result["spread_mid"] is None
        assert result["net_debit"] is None

    def test_near_bid_missing(self):
        """Missing near bid → spread_natural unavailable, mid unavailable."""
        near = _make_leg(bid=None, ask=5.40, strike=686)
        far = _make_leg(bid=8.50, ask=8.90, strike=686)
        cand = _build_calendar_candidate(near, far)
        result = _enrich_one(cand)
        assert result is not None
        assert result["execution_invalid"] is True

    def test_far_ask_missing(self):
        """Missing far ask → spread_natural unavailable, mid unavailable."""
        near = _make_leg(bid=5.20, ask=5.40, strike=686)
        far = _make_leg(bid=8.50, ask=None, strike=686)
        cand = _build_calendar_candidate(near, far)
        result = _enrich_one(cand)
        assert result is not None
        assert result["execution_invalid"] is True

    def test_rank_score_zero_when_invalid(self):
        """rank_score must be 0 for execution-invalid trades."""
        near = _make_leg(bid=None, ask=None, strike=686)
        far = _make_leg(bid=None, ask=None, strike=686)
        cand = _build_calendar_candidate(near, far)
        result = _enrich_one(cand)
        assert result is not None
        assert result["rank_score"] == 0.0

    def test_readiness_false_when_invalid(self):
        """readiness must be False for execution-invalid trades."""
        near = _make_leg(bid=None, ask=None, strike=686)
        far = _make_leg(bid=None, ask=None, strike=686)
        cand = _build_calendar_candidate(near, far)
        result = _enrich_one(cand)
        assert result is not None
        assert result["readiness"] is False


# ---------------------------------------------------------------------------
# Test: evaluate() gate behavior
# ---------------------------------------------------------------------------

class TestEvaluateGates:

    def test_execution_invalid_rejected(self):
        """evaluate() rejects execution_invalid trades."""
        near = _make_leg(bid=None, ask=None, strike=686)
        far = _make_leg(bid=None, ask=None, strike=686)
        cand = _build_calendar_candidate(near, far)
        result = _enrich_one(cand)
        assert result is not None

        plugin = CalendarsStrategyPlugin()
        result["_policy"] = {}
        result["_request"] = {}
        passed, reasons = plugin.evaluate(result)
        assert not passed
        assert any("execution_invalid" in r for r in reasons), f"Expected execution_invalid, got: {reasons}"

    def test_strategy_not_ready_rejection(self):
        """evaluate() rejects valid-priced calendars because POP/EV/max_profit are None."""
        near = _make_leg(bid=5.20, ask=5.40, strike=686)
        far = _make_leg(bid=8.50, ask=8.90, strike=686)
        cand = _build_calendar_candidate(near, far)
        result = _enrich_one(cand)
        assert result is not None
        assert result["execution_invalid"] is False

        plugin = CalendarsStrategyPlugin()
        result["_policy"] = {}
        result["_request"] = {}
        passed, reasons = plugin.evaluate(result)
        assert not passed
        assert "METRICS_NOT_IMPLEMENTED" in reasons, f"Expected METRICS_NOT_IMPLEMENTED, got: {reasons}"

    def test_liquidity_gate_still_fires(self):
        """Low OI/volume should produce calendar_liquidity_low reason."""
        near = _make_leg(bid=5.20, ask=5.40, strike=686, open_interest=1, volume=0)
        far = _make_leg(bid=8.50, ask=8.90, strike=686, open_interest=1, volume=0)
        cand = _build_calendar_candidate(near, far)
        result = _enrich_one(cand)
        assert result is not None

        plugin = CalendarsStrategyPlugin()
        result["_policy"] = {}
        result["_request"] = {}
        passed, reasons = plugin.evaluate(result)
        assert not passed
        assert "calendar_liquidity_low" in reasons or "calendar_liquidity_score_low" in reasons


# ---------------------------------------------------------------------------
# Test: sanity / diagnostic metrics
# ---------------------------------------------------------------------------

class TestSanityMetrics:

    @pytest.fixture()
    def enriched(self):
        near = _make_leg(bid=5.20, ask=5.40, strike=686)
        far = _make_leg(bid=8.50, ask=8.90, strike=686)
        cand = _build_calendar_candidate(near, far, spot=686.0)
        return _enrich_one(cand)

    def test_debit_as_pct_of_underlying(self, enriched):
        # debit / underlying_price
        debit = enriched["net_debit"]
        expected = debit / 686.0
        assert enriched["debit_as_pct_of_underlying"] == pytest.approx(expected, abs=0.0001)

    def test_debit_as_pct_of_expected_move(self, enriched):
        # debit / expected_move_near
        assert enriched["debit_as_pct_of_expected_move"] is not None
        assert enriched["debit_as_pct_of_expected_move"] > 0

    def test_term_structure_ok(self, enriched):
        assert isinstance(enriched["term_structure_ok"], bool)


# ---------------------------------------------------------------------------
# Test: computed_metrics reports ready=false for calendars
# ---------------------------------------------------------------------------

class TestComputedMetricsForCalendars:

    def test_metrics_status_ready_false(self):
        """computed_metrics must report ready=false for calendar trades."""
        from app.utils.computed_metrics import build_computed_metrics, build_metrics_status

        near = _make_leg(bid=5.20, ask=5.40, strike=686)
        far = _make_leg(bid=8.50, ask=8.90, strike=686)
        cand = _build_calendar_candidate(near, far)
        enriched = _enrich_one(cand)
        assert enriched is not None

        metrics = build_computed_metrics(enriched)
        status = build_metrics_status(metrics)
        assert status["ready"] is False
        # Missing at least: max_profit, pop, expected_value, ev_to_risk, return_on_risk
        assert "max_profit" in status["missing_fields"]
        assert "pop" in status["missing_fields"]
        assert "expected_value" in status["missing_fields"]
        assert "return_on_risk" in status["missing_fields"]

    def test_net_debit_present_in_computed(self):
        """computed_metrics must resolve net_debit from enriched trade."""
        from app.utils.computed_metrics import build_computed_metrics

        near = _make_leg(bid=5.20, ask=5.40, strike=686)
        far = _make_leg(bid=8.50, ask=8.90, strike=686)
        cand = _build_calendar_candidate(near, far)
        enriched = _enrich_one(cand)
        assert enriched is not None

        metrics = build_computed_metrics(enriched)
        assert metrics["net_debit"] is not None
        assert metrics["net_debit"] == enriched["net_debit"]

    def test_max_loss_present_in_computed(self):
        """computed_metrics must resolve max_loss."""
        from app.utils.computed_metrics import build_computed_metrics

        near = _make_leg(bid=5.20, ask=5.40, strike=686)
        far = _make_leg(bid=8.50, ask=8.90, strike=686)
        cand = _build_calendar_candidate(near, far)
        enriched = _enrich_one(cand)
        assert enriched is not None

        metrics = build_computed_metrics(enriched)
        assert metrics["max_loss"] is not None
        assert metrics["max_loss"] == enriched["max_loss"]
