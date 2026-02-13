"""
credit_spread_analysis.py

A compact, practical Python “engine” for analyzing SPY (or any index/ETF) credit spreads.
Works for:
- Put Credit Spread (bull put): sell put, buy lower put
- Call Credit Spread (bear call): sell call, buy higher call

No external deps required (math + dataclasses only).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from math import sqrt, log, exp, erf
from typing import Literal, Optional, Dict, Any
import json
import sys
import os
from datetime import datetime

SpreadType = Literal["put_credit", "call_credit"]


# -----------------------------
# Helpers
# -----------------------------
def _norm_cdf(x: float) -> float:
    """Standard normal CDF (approx via erf)."""
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def expected_move(price: float, iv: float, dte: int) -> float:
    """
    1-sigma expected move (approx):
      EM = S * IV * sqrt(DTE/365)
    iv is annualized (e.g., 0.18 for 18%).
    """
    if price <= 0:
        raise ValueError("price must be > 0")
    if iv < 0:
        raise ValueError("iv must be >= 0")
    if dte <= 0:
        raise ValueError("dte must be > 0")
    return price * iv * sqrt(dte / 365.0)


def iv_rank(iv_current: float, iv_low: float, iv_high: float) -> float:
    """
    IV Rank approximation:
      (IV - IV_low) / (IV_high - IV_low)
    Returns clipped [0,1] when bounds are valid.
    """
    if iv_high <= iv_low:
        return 0.0
    x = (iv_current - iv_low) / (iv_high - iv_low)
    return max(0.0, min(1.0, x))


def annualized_return(r: float, dte: int) -> float:
    """
    Annualize a simple return r over dte days:
      (1+r)^(365/dte) - 1
    """
    if dte <= 0:
        raise ValueError("dte must be > 0")
    return (1.0 + r) ** (365.0 / dte) - 1.0


# -----------------------------
# Trade model
# -----------------------------
@dataclass(frozen=True)
class CreditSpread:
    spread_type: SpreadType
    underlying_price: float          # current underlying price (S)
    short_strike: float              # short option strike
    long_strike: float               # long option strike
    net_credit: float                # credit per share (e.g., 1.20 = $120/spread)
    dte: int                         # days to expiration
    short_delta_abs: Optional[float] = None  # abs(delta) of short leg, e.g., 0.25
    implied_vol: Optional[float] = None      # annualized IV, e.g., 0.18
    realized_vol: Optional[float] = None     # annualized RV, e.g., 0.14

    def validate(self) -> None:
        if self.underlying_price <= 0:
            raise ValueError("underlying_price must be > 0")
        if self.net_credit <= 0:
            raise ValueError("net_credit must be > 0 (credit spread)")
        if self.dte <= 0:
            raise ValueError("dte must be > 0")

        # Strike ordering rules:
        if self.spread_type == "put_credit":
            # long put strike < short put strike
            if not (self.long_strike < self.short_strike):
                raise ValueError("For put_credit, long_strike must be < short_strike")
        elif self.spread_type == "call_credit":
            # long call strike > short call strike
            if not (self.long_strike > self.short_strike):
                raise ValueError("For call_credit, long_strike must be > short_strike")
        else:
            raise ValueError("spread_type must be 'put_credit' or 'call_credit'")

        width = abs(self.long_strike - self.short_strike)
        if self.net_credit >= width:
            raise ValueError("net_credit must be < spread width (else invalid quotes/arbitrage)")

        if self.short_delta_abs is not None and not (0.0 < self.short_delta_abs < 1.0):
            raise ValueError("short_delta_abs must be between 0 and 1")
        if self.implied_vol is not None and self.implied_vol < 0:
            raise ValueError("implied_vol must be >= 0")
        if self.realized_vol is not None and self.realized_vol < 0:
            raise ValueError("realized_vol must be >= 0")

    @property
    def width(self) -> float:
        return abs(self.long_strike - self.short_strike)

    @property
    def max_profit_per_share(self) -> float:
        return self.net_credit

    @property
    def max_loss_per_share(self) -> float:
        return self.width - self.net_credit

    @property
    def break_even(self) -> float:
        # Put credit: BE = short - credit
        # Call credit: BE = short + credit
        if self.spread_type == "put_credit":
            return self.short_strike - self.net_credit
        return self.short_strike + self.net_credit

    @property
    def return_on_risk(self) -> float:
        return self.max_profit_per_share / self.max_loss_per_share

    @property
    def risk_reward(self) -> float:
        # Risk per unit of reward
        return self.max_loss_per_share / self.max_profit_per_share

    def pop_delta_approx(self) -> Optional[float]:
        """
        POP approximation using delta:
          POP ≈ 1 - abs(delta_short)
        """
        if self.short_delta_abs is None:
            return None
        return 1.0 - self.short_delta_abs

    def iv_rv_ratio(self) -> Optional[float]:
        if self.implied_vol is None or self.realized_vol is None or self.realized_vol == 0:
            return None
        return self.implied_vol / self.realized_vol

    def expected_move(self) -> Optional[float]:
        if self.implied_vol is None:
            return None
        return expected_move(self.underlying_price, self.implied_vol, self.dte)

    def strike_distance_pct(self) -> float:
        """
        Distance from spot to short strike as % of spot.
        (Safety buffer; interpret with sign based on spread type if desired.)
        """
        return abs(self.short_strike - self.underlying_price) / self.underlying_price

    # --- Expected value ---
    def expected_value_per_share(self, p_win: Optional[float] = None) -> float:
        """
        EV per share:
          EV = p_win * max_profit - (1-p_win) * max_loss

        If p_win not supplied, uses delta approximation if available.
        """
        self.validate()

        if p_win is None:
            pop = self.pop_delta_approx()
            if pop is None:
                raise ValueError("Provide p_win or short_delta_abs to compute EV.")
            p_win = pop

        if not (0.0 <= p_win <= 1.0):
            raise ValueError("p_win must be within [0,1]")

        p_loss = 1.0 - p_win
        return (p_win * self.max_profit_per_share) - (p_loss * self.max_loss_per_share)

    def ev_to_risk(self, p_win: Optional[float] = None) -> float:
        """Normalized EV per $ at risk (per share)."""
        ev = self.expected_value_per_share(p_win=p_win)
        return ev / self.max_loss_per_share

    def annualized_ror(self) -> float:
        """Annualized return on risk (ROR) assuming max profit over dte (optimistic upper-bound)."""
        self.validate()
        return annualized_return(self.return_on_risk, self.dte)

    # --- Kelly sizing ---
    def kelly_fraction(self, p_win: Optional[float] = None) -> float:
        """
        Kelly fraction f*:
          f* = (b*p - q) / b
        where:
          b = max_profit / max_loss
          p = p_win
          q = 1-p
        """
        self.validate()
        if p_win is None:
            pop = self.pop_delta_approx()
            if pop is None:
                raise ValueError("Provide p_win or short_delta_abs to compute Kelly.")
            p_win = pop

        b = self.max_profit_per_share / self.max_loss_per_share
        q = 1.0 - p_win
        f = (b * p_win - q) / b
        return f  # can be negative; you can clamp at 0 if you only take +EV trades

    # --- Simple composite score (tweakable) ---
    def trade_quality_score(
        self,
        p_win: Optional[float] = None,
        iv_rank_value: Optional[float] = None,
        w_pop: float = 0.4,
        w_ror: float = 0.3,
        w_ivrank: float = 0.3,
        ror_cap: float = 0.5,
    ) -> float:
        """
        A simple bounded score in ~[0,1+] (not guaranteed), intended for ranking:
        - POP in [0,1]
        - ROR normalized by cap (defaults cap=0.5 => 50% ROR maps to 1.0)
        - IV Rank in [0,1] (optional)

        If p_win not provided, uses delta approximation if available.
        """
        self.validate()

        if p_win is None:
            pop = self.pop_delta_approx()
            if pop is None:
                raise ValueError("Provide p_win or short_delta_abs for scoring.")
            p_win = pop

        pop = max(0.0, min(1.0, p_win))
        ror_norm = max(0.0, min(1.0, self.return_on_risk / max(1e-9, ror_cap)))

        if iv_rank_value is None:
            iv_rank_value = 0.5  # neutral default if you don't supply it
        ivr = max(0.0, min(1.0, iv_rank_value))

        return (w_pop * pop) + (w_ror * ror_norm) + (w_ivrank * ivr)

    # --- Output ---
    def summary(self, p_win: Optional[float] = None, iv_rank_value: Optional[float] = None) -> Dict[str, Any]:
        """Convenient dict output for UI / logging."""
        self.validate()

        # pick p_win if we can
        pop = self.pop_delta_approx()
        use_p_win = p_win if p_win is not None else pop

        out: Dict[str, Any] = {
            **asdict(self),
            "width": self.width,
            "max_profit_per_share": self.max_profit_per_share,
            "max_loss_per_share": self.max_loss_per_share,
            "break_even": self.break_even,
            "return_on_risk": self.return_on_risk,
            "risk_reward": self.risk_reward,
            "pop_delta_approx": pop,
            "expected_move": self.expected_move(),
            "iv_rv_ratio": self.iv_rv_ratio(),
            "strike_distance_pct": self.strike_distance_pct(),
        }

        if use_p_win is not None:
            out["p_win_used"] = use_p_win
            out["ev_per_share"] = self.expected_value_per_share(p_win=use_p_win)
            out["ev_to_risk"] = self.ev_to_risk(p_win=use_p_win)
            out["kelly_fraction"] = self.kelly_fraction(p_win=use_p_win)
            out["trade_quality_score"] = self.trade_quality_score(p_win=use_p_win, iv_rank_value=iv_rank_value)

        out["annualized_ror_upper_bound"] = self.annualized_ror()
        return out


# (CLI/example runner removed — this module exposes `CreditSpread` for import.)