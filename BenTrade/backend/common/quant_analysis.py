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
from typing import Literal, Optional, Dict, Any, List
import json
import sys
import os
from datetime import datetime

from app.services.validation_events import emit_validation_event

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


def normalize_prob(p: Optional[float]) -> Optional[float]:
    """
    Normalize probability to [0,1]. If passed as percent (e.g., 90.2), convert to 0.902.
    """
    if p is None:
        return None
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None
    if p > 1.0:
        p = p / 100.0
    return max(0.0, min(1.0, p))


def normalize_vol(x: Optional[float], *, trading_days: int = 252) -> Optional[float]:
    """
    Normalize volatility to *annualized decimal* (e.g., 0.18 = 18%).

    Heuristics:
      - If x > 1.0, assume percent (18 -> 0.18).
      - If 0 < x < 0.03, assume DAILY vol (0.012 -> annualize).
    """
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if x <= 0:
        return None

    # percent -> decimal
    if x > 1.0:
        x = x / 100.0

    # likely daily vol -> annualize
    if x < 0.03:
        x = x * sqrt(trading_days)

    return x


def history_matches_spot(prices: Optional[List[float]], spot: float, tol: float = 0.15) -> bool:
    """
    Returns True if the last price in history is within ±tol of spot (default 15%).
    Prevents using mismatched scales (e.g., XSP-like series vs SPY spot) for SMA/RSI/RV.
    """
    if not prices or spot <= 0:
        return False
    try:
        last = float(prices[-1])
    except (TypeError, ValueError):
        return False
    if last <= 0:
        return False
    return abs(last - spot) / spot <= tol


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

        p_win_n = normalize_prob(p_win)
        if p_win_n is None:
            raise ValueError("p_win must be numeric")
        p_win = p_win_n

        p_loss = 1.0 - p_win
        return (p_win * self.max_profit_per_share) - (p_loss * self.max_loss_per_share)

    def ev_to_risk(self, p_win: Optional[float] = None) -> float:
        """Normalized EV per $ at risk (per share)."""
        ev = self.expected_value_per_share(p_win=p_win)
        return ev / self.max_loss_per_share

    def annualized_ror(self) -> float | None:
        """Annualized return on risk (ROR) assuming max profit over dte (optimistic upper-bound)."""
        self.validate()
        if self.dte < 10:
            return None
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

        p_win_n = normalize_prob(p_win)
        if p_win_n is None:
            raise ValueError("p_win must be numeric")
        p_win = p_win_n

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

        p_win_n = normalize_prob(p_win)
        if p_win_n is None:
            raise ValueError("p_win must be numeric")
        p_win = p_win_n

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
            use_p_win_n = normalize_prob(use_p_win)
            out["p_win_used"] = use_p_win_n
            if use_p_win_n is not None:
                out["ev_per_share"] = self.expected_value_per_share(p_win=use_p_win_n)
                out["ev_to_risk"] = self.ev_to_risk(p_win=use_p_win_n)
                out["kelly_fraction"] = self.kelly_fraction(p_win=use_p_win_n)
                out["trade_quality_score"] = self.trade_quality_score(p_win=use_p_win_n, iv_rank_value=iv_rank_value)

        annualized = self.annualized_ror()
        out["annualized_ror_upper_bound"] = annualized
        if annualized is None and self.dte < 10:
            out["validation_warnings"] = ["ANNUALIZE_SHORT_DTE"]
            emit_validation_event(
                severity="warn",
                code="ANNUALIZE_SHORT_DTE",
                message="Skipped annualized_ror_upper_bound for short DTE (<10)",
                context={
                    "spread_type": self.spread_type,
                    "underlying_price": self.underlying_price,
                    "short_strike": self.short_strike,
                    "long_strike": self.long_strike,
                    "dte": self.dte,
                },
            )
        return out


# (CLI/example runner removed — this module exposes `CreditSpread` for import.)


# -----------------------------
# Enrichment helpers (from quant_expanded)
# -----------------------------
def safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def bid_ask_spread_pct(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    if bid is None or ask is None:
        return None
    try:
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
    except TypeError:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid


def log_returns(prices: List[float]) -> List[float]:
    rets: List[float] = []
    for i in range(1, len(prices)):
        p0, p1 = prices[i - 1], prices[i]
        if p0 <= 0 or p1 <= 0:
            continue
        rets.append(log(p1 / p0))
    return rets


def realized_vol_annualized(prices: List[float], trading_days: int = 252) -> Optional[float]:
    rets = log_returns(prices)
    n = len(rets)
    if n < 2:
        return None
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return sqrt(var) * sqrt(trading_days)


def _history_rescale_to_spot(
    prices: List[float],
    spot: float,
    *,
    rel_tol: float = 0.15,
    max_scale: float = 100.0,
) -> Dict[str, Any]:
    """
    Try to ensure prices_history is on the same price scale as the trade's underlying_price.

    Returns:
      {
        "prices": <possibly rescaled list>,
        "scaled": bool,
        "scale_factor": float|None,
        "reason": str|None
      }

    Heuristic:
    - If last price is within rel_tol of spot, keep as-is.
    - Else try multiplicative rescale by spot/last if that factor looks reasonable.
      This catches common issues like history being for a different unit/scale (e.g., $68.13 vs $681.3).
    """
    out: Dict[str, Any] = {"prices": prices, "scaled": False, "scale_factor": None, "reason": None}
    if not prices or spot <= 0:
        return out

    last = prices[-1]
    if last <= 0:
        out["reason"] = "invalid last price in history"
        return out

    # Already matches (within tolerance)
    if abs(last - spot) / spot <= rel_tol:
        return out

    scale = spot / last

    # If scale is extreme, don't apply it.
    if scale <= 0 or scale > max_scale:
        out["reason"] = f"scale factor {scale:.4g} outside max_scale"
        return out

    # Only apply if it meaningfully improves the match.
    new_last = last * scale
    if abs(new_last - spot) / spot <= rel_tol:
        out["prices"] = [p * scale for p in prices]
        out["scaled"] = True
        out["scale_factor"] = scale
        out["reason"] = "rescaled history to match underlying_price"
        return out

    # Otherwise, don't touch it.
    out["reason"] = "history mismatch; rescale did not converge"
    return out


def _history_matches_spot(prices: List[float], spot: float, rel_tol: float = 0.15) -> bool:
    if not prices or spot <= 0:
        return False
    last = prices[-1]
    if last <= 0:
        return False
    return abs(last - spot) / spot <= rel_tol


def simple_moving_average(prices: List[float], window: int) -> Optional[float]:
    if window <= 0 or len(prices) < window:
        return None
    return sum(prices[-window:]) / window


def rsi(prices: List[float], period: int = 14) -> Optional[float]:
    if period <= 0 or len(prices) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def trend_features(prices: List[float]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {
        "sma20": None,
        "sma50": None,
        "price_vs_sma20_pct": None,
        "sma20_vs_sma50_pct": None,
    }
    if not prices:
        return out
    last = prices[-1]
    sma20 = simple_moving_average(prices, 20)
    sma50 = simple_moving_average(prices, 50)
    out["sma20"] = sma20
    out["sma50"] = sma50
    if sma20 and sma20 != 0:
        out["price_vs_sma20_pct"] = (last - sma20) / sma20
    if sma20 and sma50 and sma50 != 0:
        out["sma20_vs_sma50_pct"] = (sma20 - sma50) / sma50
    return out


def classify_market_regime(
    prices: List[float],
    iv: Optional[float] = None,
    vix: Optional[float] = None,
) -> Dict[str, Any]:
    feats = trend_features(prices)
    last = prices[-1] if prices else None
    trend = "unknown"
    if last is not None and feats["sma20"] is not None and feats["sma50"] is not None:
        sma20 = feats["sma20"]
        sma50 = feats["sma50"]
        if sma20 > sma50 and last > sma20:
            trend = "bullish"
        elif sma20 < sma50 and last < sma20:
            trend = "bearish"
        else:
            trend = "sideways"

    vol_level = "unknown"
    vol_source = None
    vol_value = None

    if vix is not None:
        vol_source = "VIX"
        vol_value = vix
        if vix < 15:
            vol_level = "low"
        elif vix < 25:
            vol_level = "moderate"
        else:
            vol_level = "high"
    elif iv is not None:
        vol_source = "IV"
        vol_value = iv
        if iv < 0.16:
            vol_level = "low"
        elif iv < 0.28:
            vol_level = "moderate"
        else:
            vol_level = "high"
    else:
        rv = realized_vol_annualized(prices) if prices else None
        if rv is not None:
            vol_source = "RV"
            vol_value = rv
            if rv < 0.16:
                vol_level = "low"
            elif rv < 0.28:
                vol_level = "moderate"
            else:
                vol_level = "high"

    label = f"{trend} trend, {vol_level} volatility" if trend != "unknown" and vol_level != "unknown" else "unknown"

    return {
        "market_trend": trend,
        "vol_level": vol_level,
        "vol_source": vol_source,
        "vol_value": vol_value,
        "market_regime": label,
        **feats,
        "rsi14": rsi(prices, 14) if prices else None,
        "realized_vol_20d": realized_vol_annualized(prices[-21:]) if prices and len(prices) >= 21 else (realized_vol_annualized(prices) if prices else None),
    }


# Canonical spread-type aliases accepted by enrich_trade / CreditSpread
_CREDIT_SPREAD_TYPE_MAP: Dict[str, SpreadType] = {
    "put_credit": "put_credit",
    "call_credit": "call_credit",
    "put_credit_spread": "put_credit",
    "call_credit_spread": "call_credit",
    "credit_put_spread": "put_credit",
    "credit_call_spread": "call_credit",
}


def _resolve_credit_spread_type(raw: Optional[str]) -> Optional[SpreadType]:
    """Map any accepted credit-spread alias to the internal SpreadType literal."""
    if raw is None:
        return None
    return _CREDIT_SPREAD_TYPE_MAP.get(str(raw).strip().lower())


def strike_distance_stddev(
    spread_type: str,
    price: float,
    short_strike: float,
    em_1sigma: float,
) -> Optional[float]:
    if em_1sigma is None or em_1sigma <= 0:
        return None
    resolved = _resolve_credit_spread_type(spread_type) or spread_type
    if resolved == "put_credit":
        return (price - short_strike) / em_1sigma
    elif resolved == "call_credit":
        return (short_strike - price) / em_1sigma
    return None


import logging as _logging

_enrich_log = _logging.getLogger("bentrade.enrich_trade")


def enrich_trade(
    trade: Dict[str, Any],
    *,
    prices_history: Optional[List[float]] = None,
    vix: Optional[float] = None,
    iv_low: Optional[float] = None,
    iv_high: Optional[float] = None,
) -> Dict[str, Any]:
    t = dict(trade)
    raw_spread = t.get("spread_type") or t.get("type") or t.get("strategy")
    spread_type: Optional[SpreadType] = _resolve_credit_spread_type(raw_spread)
    if spread_type is None:
        raise ValueError(
            f"trade must include a recognized credit-spread spread_type "
            f"(got {raw_spread!r}). Accepted: {sorted(_CREDIT_SPREAD_TYPE_MAP)}"
        )
    # Store resolved internal type for CreditSpread usage while keeping
    # the caller's canonical name in the output dict.
    t["spread_type"] = raw_spread  # preserve original canonical label

    # Preserve ticker/underlying symbol if provided (e.g., 'SPY') so UI can show it
    symbol = t.get('underlying') or t.get('symbol') or t.get('ticker')
    if symbol:
        t['underlying'] = symbol
        t['underlying_symbol'] = symbol

    price = t.get("price", t.get("underlying_price"))
    if price is None:
        raise ValueError("trade must include price (or underlying_price)")
    price = float(price)

    short_strike = float(t["short_strike"])
    dte = int(t["dte"])

    iv = t.get("iv", t.get("implied_vol"))
    iv = float(iv) if iv is not None else None
    iv = normalize_vol(iv)
    # Prefer per-trade embedded history (trade['prices_history']) if present; else use batch-provided history.
    local_history = t.get('prices_history') or prices_history
    history_scaled_info = None
    if local_history and price is not None:
        # If history is on the wrong scale (common in mocked data), attempt a multiplicative rescale.
        if not _history_matches_spot(local_history, price, rel_tol=0.15):
            info = _history_rescale_to_spot(local_history, price, rel_tol=0.15)
            history_scaled_info = info
            if info.get('scaled'):
                local_history = info['prices']

    # keep both keys consistent
    if iv is not None:
        t["iv"] = iv
        t["implied_vol"] = iv

    rv = t.get("realized_vol")
    if rv is None and local_history and _history_matches_spot(local_history, price, rel_tol=0.15):
        rv = realized_vol_annualized(local_history)
    rv = float(rv) if rv is not None else None
    rv = normalize_vol(rv)

    t["realized_vol"] = rv
    t["iv_rv_ratio"] = safe_div(iv, rv) if (iv is not None and rv is not None) else None

    em = expected_move(price, iv, dte) if iv is not None else None
    t["expected_move_1sigma"] = em
    # keep backwards compatibility with older reports / frontend that expect `expected_move`
    t["expected_move"] = em

    t["short_strike_z"] = strike_distance_stddev(spread_type, price, short_strike, em) if em is not None else None

    if iv is not None and iv_low is not None and iv_high is not None:
        t["iv_rank"] = iv_rank(iv, float(iv_low), float(iv_high))
    else:
        t["iv_rank"] = t.get("iv_rank")

    t["bid_ask_spread_pct"] = bid_ask_spread_pct(t.get("bid"), t.get("ask"))

    if local_history and _history_matches_spot(local_history, price, rel_tol=0.15):
        regime = classify_market_regime(local_history, iv=iv, vix=vix)
        t.update(regime)
    else:
        if local_history and not _history_matches_spot(local_history, price, rel_tol=0.15):
            t["data_warning"] = "prices_history scale mismatch vs underlying_price; skipped regime features"
        if history_scaled_info and history_scaled_info.get("scaled"):
            t["data_warning"] = f"prices_history rescaled by {history_scaled_info.get('scale_factor'):.6g} to match underlying_price"
        if vix is not None:
            t["vix"] = vix

    t["strike_distance_pct"] = abs(short_strike - price) / price if price != 0 else None

    # ── Core CreditSpread metric computation ──────────────────────────
    # This was previously missing: enrich_trade only added market-context
    # features but never computed the CreditSpread-derived metrics that the
    # UI displays (max_profit, max_loss, break_even, POP, EV, RoR, kelly).
    long_strike_f = None
    try:
        long_strike_f = float(t["long_strike"])
    except (KeyError, TypeError, ValueError):
        pass

    net_credit = None
    try:
        net_credit = float(t["net_credit"])
    except (KeyError, TypeError, ValueError):
        pass

    if long_strike_f is not None and net_credit is not None and net_credit > 0:
        try:
            cs = CreditSpread(
                spread_type=spread_type,
                underlying_price=price,
                short_strike=short_strike,
                long_strike=long_strike_f,
                net_credit=net_credit,
                dte=dte,
                short_delta_abs=float(t["short_delta_abs"]) if t.get("short_delta_abs") is not None else None,
                implied_vol=iv,
                realized_vol=rv,
            )
            summary = cs.summary(iv_rank_value=t.get("iv_rank"))
            # Merge computed metrics into the enriched dict.  Never
            # overwrite values the caller already provided.
            for key, value in summary.items():
                if key in t and t[key] is not None:
                    continue
                t[key] = value

            # Promote per-share values to per-contract (* multiplier) so
            # downstream code finds them under the expected keys.
            multiplier = float(t.get("contractsMultiplier") or t.get("contracts_multiplier") or 100)
            if t.get("max_profit_per_contract") is None and t.get("max_profit_per_share") is not None:
                t["max_profit_per_contract"] = t["max_profit_per_share"] * multiplier
            if t.get("max_loss_per_contract") is None and t.get("max_loss_per_share") is not None:
                t["max_loss_per_contract"] = t["max_loss_per_share"] * multiplier
            if t.get("ev_per_contract") is None and t.get("ev_per_share") is not None:
                t["ev_per_contract"] = t["ev_per_share"] * multiplier

            _enrich_log.debug(
                "CreditSpread metrics computed  trade_key=%s  max_profit=%.4f  max_loss=%.4f  "
                "pop=%.4f  ev_per_share=%.4f  ror=%.4f  kelly=%.4f  break_even=%.2f",
                t.get("trade_key", "?"),
                t.get("max_profit_per_share") or 0,
                t.get("max_loss_per_share") or 0,
                t.get("p_win_used") or t.get("pop_delta_approx") or 0,
                t.get("ev_per_share") or 0,
                t.get("return_on_risk") or 0,
                t.get("kelly_fraction") or 0,
                t.get("break_even") or 0,
            )
        except Exception as exc:
            _enrich_log.warning("CreditSpread metric computation failed: %s", exc)
            t.setdefault("data_warning", f"CreditSpread metrics unavailable: {exc}")
    else:
        missing = []
        if long_strike_f is None:
            missing.append("long_strike")
        if net_credit is None or (net_credit is not None and net_credit <= 0):
            missing.append("net_credit>0")
        _enrich_log.debug("Skipping CreditSpread metrics – missing: %s", ", ".join(missing))

    return t


def enrich_trades_batch(
    trades: List[Dict[str, Any]],
    *,
    prices_history: Optional[List[float]] = None,
    vix: Optional[float] = None,
    iv_low: Optional[float] = None,
    iv_high: Optional[float] = None,
) -> List[Dict[str, Any]]:
    return [
        enrich_trade(
            tr,
            prices_history=prices_history,
            vix=vix,
            iv_low=iv_low,
            iv_high=iv_high,
        )
        for tr in trades
    ]
