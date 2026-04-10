"""
Position Sizing Engine
======================

Computes recommended position size for a trade based on risk policy,
account state, and current portfolio exposure.

Input fields (trade):
    max_loss        — per-contract max loss in dollars
    symbol          — underlying symbol
    scanner_key     — strategy identifier for direction classification
    strategy        — fallback strategy name

Input fields (account):
    equity          — total account equity
    buying_power    — option buying power

Input fields (position):
    symbol/underlying — underlying symbol
    max_loss          — per-contract max loss
    quantity          — number of contracts
    cost_basis        — deployed capital (margin/collateral)
    scanner_key       — for direction classification

Output: see compute_size() docstring.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class PositionSizingEngine:
    """Computes recommended position size given risk policy, account, and portfolio."""

    def __init__(self, risk_policy: dict[str, Any]) -> None:
        self.policy = risk_policy

    def compute_size(
        self,
        trade: dict[str, Any],
        account: dict[str, Any],
        open_positions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Compute recommended position size.

        Returns dict with:
            max_contracts, suggested_contracts, risk_per_contract,
            total_risk, risk_pct_of_equity, binding_constraint,
            constraints (per_trade, per_underlying, total_portfolio,
            account_reserve, directional), warnings, blocked, block_reason
        """
        equity = _safe_float(account.get("equity") or account.get("total_equity"), 0.0)
        if equity <= 0:
            return self._blocked("Cannot size: account equity is $0 or unavailable")

        max_loss_per_contract = abs(_safe_float(trade.get("max_loss"), 0.0))
        if max_loss_per_contract <= 0:
            return self._blocked("Cannot size: trade has no defined max loss")

        underlying = (
            trade.get("symbol")
            or trade.get("underlying")
            or ""
        ).upper()

        # ── Policy limits ──────────────────────────────────────
        max_risk_per_trade = _safe_float(
            self.policy.get("max_risk_per_trade"),
            equity * _safe_float(self.policy.get("max_trade_risk_pct"), 0.01),
        )
        max_risk_per_underlying = _safe_float(
            self.policy.get("max_risk_per_underlying"),
            equity * _safe_float(self.policy.get("max_symbol_risk_pct"), 0.02),
        )
        max_risk_total = _safe_float(
            self.policy.get("max_risk_total"),
            equity * _safe_float(self.policy.get("max_total_risk_pct"), 0.06),
        )
        min_cash_reserve_pct = _safe_float(self.policy.get("min_cash_reserve_pct"), 20.0)
        # Stored as 20.0 for compat; normalize to 0.20 if > 1
        if min_cash_reserve_pct > 1:
            min_cash_reserve_pct = min_cash_reserve_pct / 100.0
        max_directional_pct = _safe_float(
            self.policy.get("max_directional_concentration_pct"), 0.06
        )
        sizing_buffer = _safe_float(self.policy.get("sizing_buffer_pct"), 0.85)

        constraints: dict[str, Any] = {}
        warnings: list[str] = []

        # ── 1. Per-trade limit ─────────────────────────────────
        per_trade_allows = int(max_risk_per_trade / max_loss_per_contract) if max_loss_per_contract > 0 else 0
        constraints["per_trade"] = {
            "limit": round(max_risk_per_trade, 2),
            "allows": per_trade_allows,
        }

        # ── 2. Per-underlying limit ───────────────────────────
        underlying_exposure = self._get_underlying_exposure(underlying, open_positions)
        remaining_underlying = max(0.0, max_risk_per_underlying - underlying_exposure)
        per_underlying_allows = int(remaining_underlying / max_loss_per_contract) if max_loss_per_contract > 0 else 0
        constraints["per_underlying"] = {
            "limit": round(max_risk_per_underlying, 2),
            "current_exposure": round(underlying_exposure, 2),
            "remaining": round(remaining_underlying, 2),
            "allows": per_underlying_allows,
        }
        if underlying_exposure > 0:
            warnings.append(f"You already have ${underlying_exposure:,.0f} at risk on {underlying}")

        # ── 3. Total portfolio risk limit ──────────────────────
        total_exposure = self._get_total_exposure(open_positions)
        remaining_total = max(0.0, max_risk_total - total_exposure)
        total_allows = int(remaining_total / max_loss_per_contract) if max_loss_per_contract > 0 else 0
        constraints["total_portfolio"] = {
            "limit": round(max_risk_total, 2),
            "current_exposure": round(total_exposure, 2),
            "remaining": round(remaining_total, 2),
            "allows": total_allows,
        }

        # ── 4. Account reserve limit ──────────────────────────
        deployed_capital = self._get_deployed_capital(open_positions)
        deployable = equity * (1.0 - min_cash_reserve_pct) - deployed_capital
        reserve_allows = int(max(0.0, deployable) / max_loss_per_contract) if max_loss_per_contract > 0 else 0
        constraints["account_reserve"] = {
            "reserve_pct": round(min_cash_reserve_pct * 100, 1),
            "deployed": round(deployed_capital, 2),
            "deployable_remaining": round(max(0.0, deployable), 2),
            "allows": reserve_allows,
        }

        # ── 5. Directional concentration ──────────────────────
        direction = self._get_direction(trade)
        directional_exposure = self._get_directional_exposure(direction, open_positions)
        directional_limit = equity * max_directional_pct
        remaining_directional = max(0.0, directional_limit - directional_exposure)
        directional_allows = int(remaining_directional / max_loss_per_contract) if max_loss_per_contract > 0 else 0
        constraints["directional"] = {
            "direction": direction,
            "limit": round(directional_limit, 2),
            "current_exposure": round(directional_exposure, 2),
            "remaining": round(remaining_directional, 2),
            "allows": directional_allows,
        }

        # ── Find binding constraint (lowest allows) ───────────
        all_allows = {
            "per_trade": per_trade_allows,
            "per_underlying": per_underlying_allows,
            "total_portfolio": total_allows,
            "account_reserve": reserve_allows,
            "directional": directional_allows,
        }

        binding = min(all_allows, key=all_allows.get)
        max_contracts = max(0, all_allows[binding])

        # Apply buffer
        if max_contracts == 0:
            suggested = 0
        else:
            suggested = max(1, int(max_contracts * sizing_buffer))

        total_risk = suggested * max_loss_per_contract
        risk_pct = (total_risk / equity * 100) if equity > 0 else 0.0

        # Warnings for tight constraints
        if 0 < max_contracts <= 2:
            warnings.append("Position size is near minimum — risk limits are tight")
        if total_exposure > max_risk_total * 0.8:
            pct_used = total_exposure / max_risk_total * 100 if max_risk_total > 0 else 100
            warnings.append(f"Portfolio risk at {pct_used:.0f}% of limit")

        return {
            "max_contracts": max_contracts,
            "suggested_contracts": suggested,
            "risk_per_contract": round(max_loss_per_contract, 2),
            "total_risk": round(total_risk, 2),
            "risk_pct_of_equity": round(risk_pct, 2),
            "binding_constraint": binding,
            "constraints": constraints,
            "warnings": warnings,
            "blocked": max_contracts == 0,
            "block_reason": f"All risk capacity used ({binding})" if max_contracts == 0 else None,
        }

    # ── Portfolio aggregation helpers ──────────────────────────

    @staticmethod
    def _get_underlying_exposure(symbol: str, positions: list[dict[str, Any]]) -> float:
        """Sum of max_loss × quantity for positions on the same underlying."""
        total = 0.0
        for p in positions:
            p_sym = (p.get("symbol") or p.get("underlying") or "").upper()
            if p_sym == symbol:
                ml = abs(_safe_float(p.get("max_loss"), 0.0))
                qty = abs(_safe_float(p.get("quantity"), 1.0))
                total += ml * qty
        return total

    @staticmethod
    def _get_total_exposure(positions: list[dict[str, Any]]) -> float:
        """Sum of max_loss × quantity across all positions."""
        total = 0.0
        for p in positions:
            ml = abs(_safe_float(p.get("max_loss"), 0.0))
            qty = abs(_safe_float(p.get("quantity"), 1.0))
            total += ml * qty
        return total

    @staticmethod
    def _get_deployed_capital(positions: list[dict[str, Any]]) -> float:
        """Total capital deployed (margin/collateral in use)."""
        total = 0.0
        for p in positions:
            cb = _safe_float(p.get("cost_basis"), None)
            if cb is not None:
                total += abs(cb)
            else:
                ml = abs(_safe_float(p.get("max_loss"), 0.0))
                qty = abs(_safe_float(p.get("quantity"), 1.0))
                total += ml * qty
        return total

    @staticmethod
    def _get_direction(trade: dict[str, Any]) -> str:
        """Classify trade as bullish, bearish, or neutral."""
        key = (
            trade.get("scanner_key")
            or trade.get("strategy_id")
            or trade.get("strategy")
            or ""
        ).lower()
        if "put_credit" in key or "call_debit" in key:
            return "bullish"
        if "call_credit" in key or "put_debit" in key:
            return "bearish"
        return "neutral"

    @staticmethod
    def _get_directional_exposure(direction: str, positions: list[dict[str, Any]]) -> float:
        """Sum of max_loss × quantity for positions in the same direction."""
        total = 0.0
        for p in positions:
            p_dir = PositionSizingEngine._get_direction(p)
            if p_dir == direction:
                ml = abs(_safe_float(p.get("max_loss"), 0.0))
                qty = abs(_safe_float(p.get("quantity"), 1.0))
                total += ml * qty
        return total

    @staticmethod
    def _blocked(reason: str) -> dict[str, Any]:
        return {
            "max_contracts": 0,
            "suggested_contracts": 0,
            "risk_per_contract": 0,
            "total_risk": 0,
            "risk_pct_of_equity": 0,
            "binding_constraint": None,
            "constraints": {},
            "warnings": [reason],
            "blocked": True,
            "block_reason": reason,
        }


# ── Portfolio exposure snapshot builder ────────────────────────

def build_portfolio_exposure(
    positions: list[dict[str, Any]],
    *,
    equity: float,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """
    Build aggregate portfolio exposure from open positions.

    Output:
        total_risk, total_risk_pct, total_limit, utilization_pct,
        by_underlying, by_direction, position_count, deployed_capital
    """
    engine = PositionSizingEngine(policy)

    total_exposure = engine._get_total_exposure(positions)
    deployed_capital = engine._get_deployed_capital(positions)
    max_risk_total = _safe_float(
        policy.get("max_risk_total"),
        equity * _safe_float(policy.get("max_total_risk_pct"), 0.06),
    )

    # By underlying
    by_underlying: dict[str, dict[str, Any]] = {}
    for p in positions:
        sym = (p.get("symbol") or p.get("underlying") or "UNKNOWN").upper()
        if sym not in by_underlying:
            by_underlying[sym] = {"risk": 0.0, "positions": 0}
        ml = abs(_safe_float(p.get("max_loss"), 0.0))
        qty = abs(_safe_float(p.get("quantity"), 1.0))
        by_underlying[sym]["risk"] = round(by_underlying[sym]["risk"] + ml * qty, 2)
        by_underlying[sym]["positions"] += 1

    # By direction
    by_direction: dict[str, float] = {"bullish": 0.0, "bearish": 0.0, "neutral": 0.0}
    for p in positions:
        d = engine._get_direction(p)
        ml = abs(_safe_float(p.get("max_loss"), 0.0))
        qty = abs(_safe_float(p.get("quantity"), 1.0))
        by_direction[d] = round(by_direction[d] + ml * qty, 2)

    utilization = (total_exposure / max_risk_total * 100) if max_risk_total > 0 else 0.0

    # Capacity
    remaining_total = max(0.0, max_risk_total - total_exposure)
    max_risk_per_trade = _safe_float(
        policy.get("max_risk_per_trade"),
        equity * _safe_float(policy.get("max_trade_risk_pct"), 0.01),
    )

    return {
        "total_risk": round(total_exposure, 2),
        "total_risk_pct": round(total_exposure / equity * 100, 2) if equity > 0 else 0,
        "total_limit": round(max_risk_total, 2),
        "utilization_pct": round(utilization, 2),
        "by_underlying": by_underlying,
        "by_direction": by_direction,
        "position_count": len(positions),
        "deployed_capital": round(deployed_capital, 2),
        "capacity": {
            "remaining_total": round(remaining_total, 2),
            "remaining_per_trade": round(max_risk_per_trade, 2),
            "can_open_new": remaining_total > 0,
        },
    }


# ── Helpers ────────────────────────────────────────────────────

def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert value to float, returning default on failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
