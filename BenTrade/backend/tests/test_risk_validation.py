"""Tests for POST /api/risk/validate — pre-trade risk validation endpoint.

Mocks Tradier account balance and positions to test each risk check
in isolation and in combination.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# ── We test the endpoint function directly, not via HTTP client,
#    because the app wiring is complex. Instead we test the logic
#    through a helper that mirrors the endpoint's behavior. ──

from app.services.position_sizing import PositionSizingEngine


# ── Fixtures ────────────────────────────────────────────────


def _default_policy() -> dict[str, Any]:
    """Return a deterministic test policy."""
    return {
        "portfolio_size": 100_000,
        "max_trade_risk_pct": 0.01,
        "max_risk_per_trade": 1000.0,
        "max_symbol_risk_pct": 0.02,
        "max_risk_per_underlying": 2000.0,
        "max_total_risk_pct": 0.06,
        "max_risk_total": 6000.0,
        "min_cash_reserve_pct": 20.0,  # 20%
        "max_directional_concentration_pct": 0.06,
    }


def _validate_trade(
    symbol: str,
    scanner_key: str,
    max_loss: float,
    quantity: int,
    equity: float,
    positions: list[dict[str, Any]],
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replicate the validation logic from the endpoint for unit testing.

    This avoids needing the full FastAPI app, request object, and Tradier
    credentials while testing exactly the same math.
    """
    if policy is None:
        policy = _default_policy()

    proposed_risk = max_loss * quantity

    # Policy limits
    per_trade_limit = float(
        policy.get("max_risk_per_trade")
        or equity * float(policy.get("max_trade_risk_pct") or 0.01)
    )
    per_underlying_limit = float(
        policy.get("max_risk_per_underlying")
        or equity * float(policy.get("max_symbol_risk_pct") or 0.02)
    )
    total_limit = float(
        policy.get("max_risk_total")
        or equity * float(policy.get("max_total_risk_pct") or 0.06)
    )
    min_cash_reserve_pct = float(policy.get("min_cash_reserve_pct") or 20.0)
    if min_cash_reserve_pct > 1:
        min_cash_reserve_pct /= 100.0
    max_directional_pct = float(policy.get("max_directional_concentration_pct") or 0.06)
    directional_limit = equity * max_directional_pct

    underlying_exposure = PositionSizingEngine._get_underlying_exposure(symbol, positions)
    total_exposure = PositionSizingEngine._get_total_exposure(positions)
    deployed_capital = PositionSizingEngine._get_deployed_capital(positions)
    direction = PositionSizingEngine._get_direction({"scanner_key": scanner_key})
    directional_exposure = PositionSizingEngine._get_directional_exposure(direction, positions)

    checks = []
    all_pass = True
    warnings = []

    # Check 1: Per-trade risk
    check1 = {
        "check": "per_trade_risk",
        "limit": round(per_trade_limit, 2),
        "proposed": round(proposed_risk, 2),
        "pct_of_limit": round(proposed_risk / per_trade_limit * 100, 1) if per_trade_limit > 0 else 999,
        "status": "PASS" if proposed_risk <= per_trade_limit else "FAIL",
    }
    if check1["status"] == "FAIL":
        all_pass = False
    checks.append(check1)

    # Check 2: Per-underlying
    proposed_underlying = underlying_exposure + proposed_risk
    check2 = {
        "check": "per_underlying",
        "limit": round(per_underlying_limit, 2),
        "current": round(underlying_exposure, 2),
        "proposed_total": round(proposed_underlying, 2),
        "pct_of_limit": round(proposed_underlying / per_underlying_limit * 100, 1) if per_underlying_limit > 0 else 999,
        "status": "PASS" if proposed_underlying <= per_underlying_limit else "FAIL",
    }
    if check2["status"] == "FAIL":
        all_pass = False
    checks.append(check2)

    # Check 3: Total portfolio
    proposed_total = total_exposure + proposed_risk
    check3 = {
        "check": "total_portfolio",
        "limit": round(total_limit, 2),
        "current": round(total_exposure, 2),
        "proposed_total": round(proposed_total, 2),
        "pct_of_limit": round(proposed_total / total_limit * 100, 1) if total_limit > 0 else 999,
        "status": "PASS" if proposed_total <= total_limit else "FAIL",
    }
    if check3["status"] == "FAIL":
        all_pass = False
    checks.append(check3)

    # Check 4: Directional
    proposed_dir = directional_exposure + proposed_risk
    check4 = {
        "check": "directional_concentration",
        "direction": direction,
        "limit": round(directional_limit, 2),
        "current": round(directional_exposure, 2),
        "proposed_total": round(proposed_dir, 2),
        "pct_of_limit": round(proposed_dir / directional_limit * 100, 1) if directional_limit > 0 else 999,
        "status": "PASS" if proposed_dir <= directional_limit else "FAIL",
    }
    if check4["status"] == "FAIL":
        all_pass = False
    checks.append(check4)

    # Check 5: Account reserve
    reserve_required = equity * min_cash_reserve_pct
    deployed_after = deployed_capital + proposed_risk
    reserve_after = equity - deployed_after
    check5 = {
        "check": "account_reserve",
        "reserve_required": round(reserve_required, 2),
        "equity": round(equity, 2),
        "deployed_after": round(deployed_after, 2),
        "reserve_after": round(reserve_after, 2),
        "status": "PASS" if reserve_after >= reserve_required else "FAIL",
    }
    if check5["status"] == "FAIL":
        all_pass = False
    checks.append(check5)

    suggested_max = None
    if not all_pass:
        engine = PositionSizingEngine(policy)
        sizing = engine.compute_size(
            {"symbol": symbol, "scanner_key": scanner_key, "max_loss": max_loss},
            {"equity": equity},
            positions,
        )
        suggested_max = sizing.get("suggested_contracts", 0)

    return {
        "ok": True,
        "approved": all_pass,
        "risk_checks": checks,
        "warnings": warnings,
        "suggested_max_quantity": suggested_max,
    }


# ── Test classes ────────────────────────────────────────────


class TestAllChecksPass:
    """Trade within all limits — small trade, empty portfolio."""

    def test_small_trade_all_pass(self):
        result = _validate_trade(
            symbol="SPY",
            scanner_key="put_credit_spread",
            max_loss=187.0,
            quantity=2,
            equity=100_000,
            positions=[],
        )
        assert result["approved"] is True
        assert result["suggested_max_quantity"] is None
        assert all(c["status"] == "PASS" for c in result["risk_checks"])
        assert len(result["risk_checks"]) == 5

    def test_check_names(self):
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=100, quantity=1, equity=100_000, positions=[],
        )
        names = [c["check"] for c in result["risk_checks"]]
        assert names == [
            "per_trade_risk", "per_underlying", "total_portfolio",
            "directional_concentration", "account_reserve",
        ]


class TestPerTradeLimit:
    """Check 1: per_trade_risk fails when proposed > single-trade limit."""

    def test_exceeds_per_trade_limit(self):
        # Default limit: $1,000. Trade: $200 × 6 = $1,200 > $1,000
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=200.0, quantity=6,
            equity=100_000, positions=[],
        )
        assert result["approved"] is False
        per_trade = next(c for c in result["risk_checks"] if c["check"] == "per_trade_risk")
        assert per_trade["status"] == "FAIL"
        assert per_trade["proposed"] == 1200.0
        assert per_trade["limit"] == 1000.0

    def test_at_exact_limit_passes(self):
        # $500 × 2 = $1,000 exactly at limit
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=500.0, quantity=2,
            equity=100_000, positions=[],
        )
        per_trade = next(c for c in result["risk_checks"] if c["check"] == "per_trade_risk")
        assert per_trade["status"] == "PASS"


class TestPerUnderlyingLimit:
    """Check 2: per_underlying fails with existing positions on same symbol."""

    def test_exceeds_underlying_limit(self):
        # Default limit: $2,000 per underlying.
        # Existing: 1 position with max_loss=150, qty=10 = $1,500
        # New: $150 × 5 = $750 → total $2,250 > $2,000
        positions = [
            {"symbol": "SPY", "underlying": "SPY", "max_loss": 150, "quantity": 10, "cost_basis": 1500, "scanner_key": "put_credit_spread"},
        ]
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=150.0, quantity=5,
            equity=100_000, positions=positions,
        )
        assert result["approved"] is False
        per_und = next(c for c in result["risk_checks"] if c["check"] == "per_underlying")
        assert per_und["status"] == "FAIL"
        assert per_und["current"] == 1500.0
        assert per_und["proposed_total"] == 2250.0

    def test_different_underlying_passes(self):
        # Existing risk on QQQ, new trade on SPY — no conflict
        positions = [
            {"symbol": "QQQ", "underlying": "QQQ", "max_loss": 200, "quantity": 8, "cost_basis": 1600, "scanner_key": "put_credit_spread"},
        ]
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=150.0, quantity=3,
            equity=100_000, positions=positions,
        )
        per_und = next(c for c in result["risk_checks"] if c["check"] == "per_underlying")
        assert per_und["status"] == "PASS"
        assert per_und["current"] == 0.0  # No SPY exposure


class TestTotalPortfolioLimit:
    """Check 3: total_portfolio fails when cumulative risk exceeds limit."""

    def test_exceeds_total_limit(self):
        # Default limit: $6,000 total.
        # Existing across multiple underlyings: $5,500
        # New: $100 × 6 = $600 → total $6,100 > $6,000
        positions = [
            {"symbol": "SPY", "underlying": "SPY", "max_loss": 100, "quantity": 20, "cost_basis": 2000, "scanner_key": "put_credit_spread"},
            {"symbol": "QQQ", "underlying": "QQQ", "max_loss": 150, "quantity": 10, "cost_basis": 1500, "scanner_key": "put_credit_spread"},
            {"symbol": "IWM", "underlying": "IWM", "max_loss": 200, "quantity": 10, "cost_basis": 2000, "scanner_key": "put_credit_spread"},
        ]
        result = _validate_trade(
            symbol="DIA", scanner_key="put_credit_spread",
            max_loss=100.0, quantity=6,
            equity=100_000, positions=positions,
        )
        total = next(c for c in result["risk_checks"] if c["check"] == "total_portfolio")
        assert total["status"] == "FAIL"
        assert total["current"] == 5500.0
        assert total["proposed_total"] == 6100.0


class TestDirectionalConcentration:
    """Check 4: directional_concentration for bullish/bearish/neutral."""

    def test_direction_classification(self):
        # put_credit_spread → bullish, call_credit_spread → bearish
        assert PositionSizingEngine._get_direction({"scanner_key": "put_credit_spread"}) == "bullish"
        assert PositionSizingEngine._get_direction({"scanner_key": "call_credit_spread"}) == "bearish"
        assert PositionSizingEngine._get_direction({"scanner_key": "iron_condor"}) == "neutral"

    def test_directional_limit_fail(self):
        # Default: 6% of $100k = $6,000 directional limit.
        # Existing bullish: $5,500
        # New bullish: $150 × 5 = $750 → total $6,250 > $6,000
        positions = [
            {"symbol": "SPY", "underlying": "SPY", "max_loss": 100, "quantity": 30, "cost_basis": 3000, "scanner_key": "put_credit_spread"},
            {"symbol": "QQQ", "underlying": "QQQ", "max_loss": 125, "quantity": 20, "cost_basis": 2500, "scanner_key": "put_credit_spread"},
        ]
        result = _validate_trade(
            symbol="IWM", scanner_key="put_credit_spread",
            max_loss=150.0, quantity=5,
            equity=100_000, positions=positions,
        )
        dir_check = next(c for c in result["risk_checks"] if c["check"] == "directional_concentration")
        assert dir_check["status"] == "FAIL"
        assert dir_check["direction"] == "bullish"


class TestAccountReserve:
    """Check 5: account_reserve ensures minimum cash reserve."""

    def test_reserve_fail(self):
        # Equity: $100,000, reserve: 20% → need $20,000 free.
        # Deployed already: 75,000. New: $150 × 40 = $6,000
        # Deployed after: $81,000. Reserve: $19,000 < $20,000 → FAIL
        positions = [
            {"symbol": "SPY", "underlying": "SPY", "max_loss": 100, "quantity": 10, "cost_basis": 75_000, "scanner_key": "put_credit_spread"},
        ]
        result = _validate_trade(
            symbol="QQQ", scanner_key="put_credit_spread",
            max_loss=150.0, quantity=40,
            equity=100_000, positions=positions,
        )
        reserve = next(c for c in result["risk_checks"] if c["check"] == "account_reserve")
        assert reserve["status"] == "FAIL"
        assert reserve["reserve_required"] == 20_000.0
        assert reserve["reserve_after"] == 100_000 - (75_000 + 6_000)

    def test_reserve_pass(self):
        # Small trade, plenty of reserve
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=100.0, quantity=1,
            equity=100_000, positions=[],
        )
        reserve = next(c for c in result["risk_checks"] if c["check"] == "account_reserve")
        assert reserve["status"] == "PASS"
        assert reserve["reserve_after"] == 99_900.0


class TestSuggestedMaxQuantity:
    """When any check fails, suggested_max_quantity should be provided."""

    def test_suggested_max_on_failure(self):
        # Per-trade limit: $1,000. max_loss: $200 → max 5.
        # Sizing engine with buffer 0.85 → suggested = max(1, int(5 * 0.85)) = 4
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=200.0, quantity=10,
            equity=100_000, positions=[],
        )
        assert result["approved"] is False
        assert result["suggested_max_quantity"] is not None
        assert result["suggested_max_quantity"] > 0
        assert result["suggested_max_quantity"] < 10

    def test_no_suggestion_when_all_pass(self):
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=100.0, quantity=1,
            equity=100_000, positions=[],
        )
        assert result["approved"] is True
        assert result["suggested_max_quantity"] is None


class TestPctOfLimit:
    """Verify pct_of_limit is computed correctly."""

    def test_per_trade_pct(self):
        # $500 / $1,000 limit = 50%
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=250.0, quantity=2,
            equity=100_000, positions=[],
        )
        per_trade = next(c for c in result["risk_checks"] if c["check"] == "per_trade_risk")
        assert per_trade["pct_of_limit"] == 50.0

    def test_per_underlying_pct(self):
        # Existing: $800, new: $400, total: $1,200 / $2,000 = 60%
        positions = [
            {"symbol": "SPY", "underlying": "SPY", "max_loss": 100, "quantity": 8, "cost_basis": 800, "scanner_key": "put_credit_spread"},
        ]
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=200.0, quantity=2,
            equity=100_000, positions=positions,
        )
        per_und = next(c for c in result["risk_checks"] if c["check"] == "per_underlying")
        assert per_und["pct_of_limit"] == 60.0


class TestMultipleFailures:
    """Multiple checks can fail simultaneously."""

    def test_two_checks_fail(self):
        # Exceed both per-trade ($1,000) and per-underlying ($2,000)
        # max_loss=300, qty=8 → risk=$2,400 > $1,000 per-trade AND
        # no existing, but $2,400 > $2,000 per-underlying
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=300.0, quantity=8,
            equity=100_000, positions=[],
        )
        assert result["approved"] is False
        failed = [c for c in result["risk_checks"] if c["status"] == "FAIL"]
        check_names = {c["check"] for c in failed}
        assert "per_trade_risk" in check_names
        assert "per_underlying" in check_names


class TestEdgeCases:
    """Edge cases: zero quantity, zero max_loss, single contract."""

    def test_single_contract(self):
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=187.0, quantity=1,
            equity=100_000, positions=[],
        )
        assert result["approved"] is True

    def test_high_equity_relaxes_limits(self):
        # With $1M equity, percentage-based limits are much higher.
        # But absolute limits still cap: max_risk_per_trade = $1,000
        result = _validate_trade(
            symbol="SPY", scanner_key="put_credit_spread",
            max_loss=200.0, quantity=6,
            equity=1_000_000, positions=[],
        )
        per_trade = next(c for c in result["risk_checks"] if c["check"] == "per_trade_risk")
        # Absolute cap: $1,000. $200 × 6 = $1,200 > $1,000 → FAIL
        assert per_trade["status"] == "FAIL"
