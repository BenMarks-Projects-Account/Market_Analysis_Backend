"""
Portfolio Risk / Exposure Engine v1.1
======================================

Reusable portfolio-context module that inspects current positions and
produces a normalized exposure summary for downstream policy and
decision layers.

Input sources
-------------
The engine accepts a flat list of **position dicts**.  Each position
should carry a subset of these fields (all optional except symbol):

.. code-block:: python

    {
        "symbol":          str,        # underlying ticker (required)
        "strategy":        str | None, # canonical strategy_id or legacy alias
        "direction":       str | None, # "long" | "short" | inferred from quantity
        "quantity":        int | None, # signed or unsigned (neg=short)
        "expiration":      str | None, # "YYYY-MM-DD"
        "dte":             int | None, # days to expiration
        "risk":            float|None, # max loss ($) for this position
        "max_profit":      float|None, # max profit ($) where available
        "delta":           float|None,
        "gamma":           float|None,
        "theta":           float|None,
        "vega":            float|None,
        "sector":          str | None, # GICS / custom sector label
        "event_tag":       str | None, # event label if applicable
        "underlying_price":float|None,
        "mark_price":      float|None,
        "avg_open_price":  float|None,
        "unrealized_pnl":  float|None,
        "trade_key":       str | None, # stable identifier
    }

Positions may come from:
- ``routes_active_trades._build_active_trades()`` → active-trade dicts
- ``routes_portfolio_risk._normalize_from_active()`` → normalized risk rows
- ``routes_portfolio_risk._normalize_from_report()`` → report-based rows
- Any other list of position-like dicts with at least ``symbol``

Output contract
---------------
``build_portfolio_exposure(positions, account_equity=None)`` returns::

    {
        "portfolio_version":        "1.0",
        "generated_at":             ISO-8601,
        "status":                   "ok" | "partial" | "empty",
        "position_count":           int,
        "underlying_count":         int,

        "portfolio_summary":        {...},
        "directional_exposure":     {...},
        "underlying_concentration": {...},
        "sector_concentration":     {...},
        "strategy_concentration":   {...},
        "expiration_concentration": {...},
        "capital_at_risk":          {...},
        "greeks_exposure":          {...},
        "event_exposure":           {...},
        "correlation_exposure":     {...},

        "risk_flags":               list[str],
        "warning_flags":            list[str],

        "evidence":                 {...},
        "metadata":                 {...},
    }
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter
from typing import Any

from app.utils.strategy_constants import (
    CORRELATION_CLUSTERS,
    SYMBOL_TO_CLUSTER,
)

# ── Constants ────────────────────────────────────────────────────────

_ENGINE_VERSION = "1.1"

# Backward-compatible aliases — canonical source is strategy_constants.
_CORRELATION_CLUSTERS = CORRELATION_CLUSTERS
_SYMBOL_TO_CLUSTER = SYMBOL_TO_CLUSTER

# DTE bucket boundaries (same as routes_portfolio_risk)
_DTE_BUCKETS = [
    ("0-7D",   0,   7),
    ("8-21D",  8,  21),
    ("22-45D", 22, 45),
    ("46-90D", 46, 90),
    ("90D+",   91, 99999),
]

# Concentration thresholds for v1 heuristics
_UNDERLYING_CONCENTRATION_THRESHOLD = 0.40  # 40% of total risk in one symbol
_STRATEGY_CONCENTRATION_THRESHOLD = 0.60    # 60% of positions same strategy
_EXPIRATION_CLUSTER_THRESHOLD = 0.50        # 50% risk in one DTE bucket
_CORRELATION_CLUSTER_THRESHOLD = 0.50       # 50% of risk in one correlated cluster
_SECTOR_CONCENTRATION_THRESHOLD = 0.50      # 50% of positions in one sector

# Strategy family classification
_CREDIT_STRATEGIES = frozenset({
    "put_credit_spread", "call_credit_spread", "iron_condor",
    "csp", "covered_call", "income", "credit_put", "credit_call",
    "credit_put_spread", "credit_call_spread", "cash_secured_put",
})
_DEBIT_STRATEGIES = frozenset({
    "put_debit", "call_debit", "butterfly_debit",
    "debit_put", "debit_call", "put_debit_spread",
    "call_debit_spread", "debit_butterfly", "debit_put_spread",
    "debit_call_spread", "calendar_spread", "calendar_call_spread",
    "calendar_put_spread",
})
_STOCK_STRATEGIES = frozenset({
    "stock_pullback_swing", "stock_momentum_breakout",
    "stock_mean_reversion", "stock_volatility_expansion",
    "stock_long", "stock_short", "equity",
})

# Directional biases
_BULLISH_STRATEGIES = frozenset({
    "put_credit_spread", "credit_put", "credit_put_spread",
    "call_debit", "call_debit_spread", "debit_call",
    "debit_call_spread", "csp", "cash_secured_put",
    "covered_call", "stock_long",
    "stock_pullback_swing", "stock_momentum_breakout",
    "stock_mean_reversion", "stock_volatility_expansion",
})
_BEARISH_STRATEGIES = frozenset({
    "call_credit_spread", "credit_call", "credit_call_spread",
    "put_debit", "put_debit_spread", "debit_put",
    "debit_put_spread", "stock_short",
})
_NEUTRAL_STRATEGIES = frozenset({
    "iron_condor", "butterfly_debit", "debit_butterfly",
    "calendar_spread", "calendar_call_spread", "calendar_put_spread",
    "income",
})


# ── Public API ───────────────────────────────────────────────────────


def build_portfolio_exposure(
    positions: list[dict[str, Any]],
    account_equity: float | None = None,
) -> dict[str, Any]:
    """Build a normalized portfolio exposure summary.

    Parameters
    ----------
    positions : list[dict]
        Flat list of position dicts.  Each should carry at least
        ``symbol``.  All other fields are optional.
    account_equity : float | None
        Total account equity/NLV for utilization calculations.
        When *None*, utilization metrics are omitted.

    Returns
    -------
    dict – portfolio exposure report conforming to the output contract.
    """
    # Sanitize input
    clean = _sanitize_positions(positions)

    if not clean:
        return _empty_output()

    position_count = len(clean)
    symbols = {p["symbol"] for p in clean}
    underlying_count = len(symbols)

    # ── Build each exposure dimension ────────────────────────────
    directional = _build_directional_exposure(clean)
    underlying_conc = _build_underlying_concentration(clean)
    sector_conc = _build_sector_concentration(clean)
    strategy_conc = _build_strategy_concentration(clean)
    expiration_conc = _build_expiration_concentration(clean)
    capital = _build_capital_at_risk(clean, account_equity)
    greeks = _build_greeks_exposure(clean)
    event_exp = _build_event_exposure(clean)
    correlation = _build_correlation_exposure(clean)

    # ── Derive risk flags and warnings ───────────────────────────
    risk_flags = _derive_risk_flags(
        underlying_conc, strategy_conc, expiration_conc,
        capital, greeks, correlation, directional, sector_conc,
    )
    warning_flags = _derive_warning_flags(clean, greeks, sector_conc, event_exp)

    # ── Status ───────────────────────────────────────────────────
    status = _determine_status(clean, greeks, sector_conc)

    # ── Dimension coverage ───────────────────────────────────────
    dim_coverage = _build_dimension_coverage(
        directional, underlying_conc, sector_conc, strategy_conc,
        expiration_conc, capital, greeks, event_exp, correlation,
    )

    # ── Portfolio summary ────────────────────────────────────────
    summary = _build_portfolio_summary(
        directional, underlying_conc, capital, greeks,
        position_count, underlying_count, risk_flags,
    )

    return {
        "portfolio_version": _ENGINE_VERSION,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "status": status,
        "position_count": position_count,
        "underlying_count": underlying_count,
        "portfolio_summary": summary,
        "directional_exposure": directional,
        "underlying_concentration": underlying_conc,
        "sector_concentration": sector_conc,
        "strategy_concentration": strategy_conc,
        "expiration_concentration": expiration_conc,
        "capital_at_risk": capital,
        "greeks_exposure": greeks,
        "event_exposure": event_exp,
        "correlation_exposure": correlation,
        "dimension_coverage": dim_coverage,
        "risk_flags": risk_flags,
        "warning_flags": warning_flags,
        "evidence": {
            "position_count": position_count,
            "underlying_count": underlying_count,
            "symbols": sorted(symbols),
            "has_account_equity": account_equity is not None,
        },
        "metadata": {
            "portfolio_version": _ENGINE_VERSION,
            "position_count": position_count,
            "underlying_count": underlying_count,
            "account_equity_provided": account_equity is not None,
            "greeks_coverage": greeks.get("coverage", "none"),
            "sector_coverage": sector_conc.get("coverage", "none"),
            "event_coverage": event_exp.get("coverage", "none"),
            "correlation_method": correlation.get("method", "static_cluster"),
        },
    }


# ── Empty output ─────────────────────────────────────────────────────

def _empty_output() -> dict[str, Any]:
    """Contract shape for zero-position portfolio."""
    return {
        "portfolio_version": _ENGINE_VERSION,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "status": "empty",
        "position_count": 0,
        "underlying_count": 0,
        "portfolio_summary": {
            "description": "No positions in portfolio.",
            "directional_bias": "neutral",
            "risk_level": "none",
            "flags_count": 0,
        },
        "directional_exposure": {
            "bias": "neutral",
            "bullish_count": 0, "bearish_count": 0,
            "neutral_count": 0, "unknown_count": 0,
        },
        "underlying_concentration": {
            "top_symbols": [],
            "concentrated": False,
            "hhi": 0.0,
        },
        "sector_concentration": {
            "coverage": "none", "sectors": {},
            "positions_with_sector": 0, "positions_without_sector": 0,
            "concentrated": False, "concentration_reliable": False,
        },
        "strategy_concentration": {
            "top_strategies": [],
            "concentrated": False,
            "families": {},
        },
        "expiration_concentration": {
            "buckets": {},
            "concentrated": False,
            "nearest_expiration": None,
        },
        "capital_at_risk": {
            "coverage": "none",
            "total_risk": 0.0,
            "positions_with_risk": 0,
            "positions_without_risk": 0,
            "utilization_pct": None,
        },
        "greeks_exposure": {
            "coverage": "none",
            "delta": 0.0, "gamma": 0.0,
            "theta": 0.0, "vega": 0.0,
            "positions_with_greeks": 0,
            "positions_without_greeks": 0,
        },
        "event_exposure": {"coverage": "none", "events": []},
        "correlation_exposure": {
            "clusters": {}, "concentrated": False,
            "method": "static_cluster",
            "method_note": "Predefined ETF clusters; no live correlation data.",
        },
        "dimension_coverage": {
            "directional_exposure":     "unavailable",
            "underlying_concentration": "unavailable",
            "sector_concentration":     "unavailable",
            "strategy_concentration":   "unavailable",
            "expiration_concentration": "unavailable",
            "capital_at_risk":          "unavailable",
            "greeks_exposure":          "unavailable",
            "event_exposure":           "unavailable",
            "correlation_exposure":     "unavailable",
        },
        "risk_flags": [],
        "warning_flags": [],
        "evidence": {
            "position_count": 0, "underlying_count": 0,
            "symbols": [], "has_account_equity": False,
        },
        "metadata": {
            "portfolio_version": _ENGINE_VERSION,
            "position_count": 0,
            "underlying_count": 0,
            "account_equity_provided": False,
            "greeks_coverage": "none",
            "sector_coverage": "none",
            "event_coverage": "none",
            "correlation_method": "static_cluster",
        },
    }


# ── Sanitization ─────────────────────────────────────────────────────

def _sanitize_positions(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clean and normalise raw position dicts.

    Skips entries missing a symbol.  Normalises symbol casing and
    applies safe defaults for missing numeric fields.
    """
    if not isinstance(positions, list):
        return []
    result: list[dict[str, Any]] = []
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        sym = str(pos.get("symbol") or pos.get("underlying") or "").strip().upper()
        if not sym:
            continue
        result.append({**pos, "symbol": sym})
    return result


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ── Directional exposure ─────────────────────────────────────────────

def _infer_direction(pos: dict[str, Any]) -> str:
    """Infer directional bias: bullish / bearish / neutral / unknown.

    Priority:
    1. Explicit ``direction`` field ("long"→bullish, "short"→bearish)
    2. Strategy-based inference
    3. Signed quantity (positive→bullish, negative→bearish)
    4. "unknown"
    """
    direction = str(pos.get("direction") or "").lower()
    if direction == "long":
        return "bullish"
    if direction == "short":
        return "bearish"

    strategy = str(pos.get("strategy") or pos.get("strategy_id") or "").lower()
    if strategy in _BULLISH_STRATEGIES:
        return "bullish"
    if strategy in _BEARISH_STRATEGIES:
        return "bearish"
    if strategy in _NEUTRAL_STRATEGIES:
        return "neutral"

    qty = _safe_int(pos.get("quantity"))
    if qty is not None:
        if qty > 0:
            return "bullish"
        if qty < 0:
            return "bearish"

    return "unknown"


def _build_directional_exposure(
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate directional exposure across portfolio.

    Bias rules:
    - Majority bullish → bullish
    - Majority bearish → bearish
    - Both present (neither majority) → mixed
    - All neutral/unknown → neutral
    """
    counts: dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0, "unknown": 0}
    for pos in positions:
        d = _infer_direction(pos)
        counts[d] = counts.get(d, 0) + 1

    total = counts["bullish"] + counts["bearish"] + counts["neutral"]
    if total == 0:
        bias = "neutral"
    elif counts["bullish"] > counts["bearish"] and counts["bullish"] > counts["neutral"]:
        bias = "bullish"
    elif counts["bearish"] > counts["bullish"] and counts["bearish"] > counts["neutral"]:
        bias = "bearish"
    elif counts["bullish"] > 0 and counts["bearish"] > 0:
        bias = "mixed"
    else:
        bias = "neutral"

    return {
        "bias": bias,
        "bullish_count": counts["bullish"],
        "bearish_count": counts["bearish"],
        "neutral_count": counts["neutral"],
        "unknown_count": counts["unknown"],
    }


# ── Underlying concentration ─────────────────────────────────────────

def _build_underlying_concentration(
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Measure underlying symbol concentration.

    Uses risk-weighted concentration when risk data is available,
    otherwise falls back to position counts.

    HHI (Herfindahl-Hirschman Index) is computed as sum of squared
    share fractions.  1.0 = perfectly concentrated in one symbol.
    """
    # Risk-weighted if available
    risk_by_sym: dict[str, float] = {}
    count_by_sym: Counter[str] = Counter()
    has_risk = False

    for pos in positions:
        sym = pos["symbol"]
        count_by_sym[sym] += 1
        risk = _safe_float(pos.get("risk"))
        if risk is not None and risk > 0:
            risk_by_sym[sym] = risk_by_sym.get(sym, 0.0) + risk
            has_risk = True

    if has_risk:
        total = sum(risk_by_sym.values())
        shares = {sym: r / total for sym, r in risk_by_sym.items()} if total > 0 else {}
        top = sorted(shares.items(), key=lambda x: -x[1])[:5]
        hhi = sum(s ** 2 for s in shares.values()) if shares else 0.0
        concentrated = any(s >= _UNDERLYING_CONCENTRATION_THRESHOLD for _, s in top)
        top_symbols = [
            {"symbol": sym, "share": round(share, 4), "risk": round(risk_by_sym[sym], 2)}
            for sym, share in top
        ]
    else:
        total_count = sum(count_by_sym.values())
        shares = {sym: c / total_count for sym, c in count_by_sym.items()} if total_count > 0 else {}
        top = sorted(shares.items(), key=lambda x: -x[1])[:5]
        hhi = sum(s ** 2 for s in shares.values()) if shares else 0.0
        concentrated = any(s >= _UNDERLYING_CONCENTRATION_THRESHOLD for _, s in top)
        top_symbols = [
            {"symbol": sym, "share": round(share, 4), "count": count_by_sym[sym]}
            for sym, share in top
        ]

    return {
        "top_symbols": top_symbols,
        "concentrated": concentrated,
        "hhi": round(hhi, 4),
        "method": "risk_weighted" if has_risk else "count_weighted",
        "total_symbols": len(count_by_sym),
    }


# ── Sector concentration ─────────────────────────────────────────────

def _build_sector_concentration(
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate sector exposure where sector labels are available.

    Coverage semantics:
    - none: zero positions have sector data
    - partial: some positions have sector data
    - full: all positions have sector data

    concentration_reliable: True only when coverage == "full".
    Partial sector data produces sector shares and a concentrated flag,
    but marks concentration_reliable=False so downstream consumers know
    the concentration claim is based on incomplete data.

    Sector shares are computed two ways:
    - share: count / positions_with_sector (sector-relative)
    - total_share: count / all_positions (portfolio-relative, honest denominator)
    """
    sector_counts: Counter[str] = Counter()
    with_sector = 0
    without_sector = 0

    for pos in positions:
        sector = pos.get("sector")
        if sector and isinstance(sector, str) and sector.strip():
            sector_counts[sector.strip()] += 1
            with_sector += 1
        else:
            without_sector += 1

    if with_sector == 0:
        return {
            "coverage": "none", "sectors": {},
            "positions_with_sector": 0,
            "positions_without_sector": without_sector,
            "concentrated": False,
            "concentration_reliable": False,
        }

    total_positions = with_sector + without_sector
    total_with = with_sector
    sectors = {
        s: {
            "count": c,
            "share": round(c / total_with, 4),
            "total_share": round(c / total_positions, 4),
        }
        for s, c in sector_counts.most_common()
    }

    coverage = "full" if without_sector == 0 else "partial"

    # Concentration: any sector ≥ threshold of known-sector positions
    concentrated = any(
        c / total_with >= _SECTOR_CONCENTRATION_THRESHOLD
        for c in sector_counts.values()
    ) if total_with > 1 else False

    return {
        "coverage": coverage,
        "sectors": sectors,
        "positions_with_sector": with_sector,
        "positions_without_sector": without_sector,
        "concentrated": concentrated,
        "concentration_reliable": coverage == "full",
    }


# ── Strategy concentration ───────────────────────────────────────────

def _classify_strategy_family(strategy: str) -> str:
    """Classify strategy into family: credit / debit / stock / other."""
    s = strategy.lower()
    if s in _CREDIT_STRATEGIES:
        return "credit"
    if s in _DEBIT_STRATEGIES:
        return "debit"
    if s in _STOCK_STRATEGIES:
        return "stock"
    return "other"


def _build_strategy_concentration(
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate strategy types across portfolio."""
    strat_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()

    for pos in positions:
        s = str(pos.get("strategy") or pos.get("strategy_id") or "unknown").lower()
        strat_counts[s] += 1
        family_counts[_classify_strategy_family(s)] += 1

    total = sum(strat_counts.values())
    top = strat_counts.most_common(5)
    concentrated = any(c / total >= _STRATEGY_CONCENTRATION_THRESHOLD for _, c in top) if total > 0 else False

    top_strategies = [
        {"strategy": s, "count": c, "share": round(c / total, 4)}
        for s, c in top
    ] if total > 0 else []

    return {
        "top_strategies": top_strategies,
        "concentrated": concentrated,
        "families": dict(family_counts),
        "total_strategies": len(strat_counts),
    }


# ── Expiration concentration ─────────────────────────────────────────

def _dte_bucket(dte: int | None) -> str:
    """Map DTE value to bucket label."""
    if dte is None:
        return "unknown"
    for label, lo, hi in _DTE_BUCKETS:
        if lo <= dte <= hi:
            return label
    return "90D+"


def _build_expiration_concentration(
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Measure expiration clustering by DTE buckets and risk."""
    bucket_risk: dict[str, float] = {}
    bucket_count: Counter[str] = Counter()
    expirations: list[str] = []
    no_expiration = 0

    for pos in positions:
        exp = pos.get("expiration")
        dte = _safe_int(pos.get("dte"))
        risk = _safe_float(pos.get("risk")) or 0.0

        if exp:
            expirations.append(str(exp))

        if dte is not None or exp:
            bucket = _dte_bucket(dte)
            bucket_count[bucket] += 1
            bucket_risk[bucket] = bucket_risk.get(bucket, 0.0) + risk
        else:
            no_expiration += 1

    total_risk = sum(bucket_risk.values())
    buckets_out: dict[str, Any] = {}
    for label, _, _ in _DTE_BUCKETS:
        r = bucket_risk.get(label, 0.0)
        c = bucket_count.get(label, 0)
        buckets_out[label] = {
            "count": c,
            "risk": round(r, 2),
            "share": round(r / total_risk, 4) if total_risk > 0 else 0.0,
        }

    # Add unknown bucket if present
    if "unknown" in bucket_count:
        buckets_out["unknown"] = {
            "count": bucket_count["unknown"],
            "risk": round(bucket_risk.get("unknown", 0.0), 2),
            "share": 0.0,
        }

    concentrated = any(
        v["share"] >= _EXPIRATION_CLUSTER_THRESHOLD
        for k, v in buckets_out.items()
        if k != "unknown" and v["count"] > 1
    )

    nearest = min(expirations) if expirations else None

    return {
        "buckets": buckets_out,
        "concentrated": concentrated,
        "nearest_expiration": nearest,
        "no_expiration_count": no_expiration,
    }


# ── Capital at risk ──────────────────────────────────────────────────

def _build_capital_at_risk(
    positions: list[dict[str, Any]],
    account_equity: float | None,
) -> dict[str, Any]:
    """Aggregate capital at risk from position max-loss fields."""
    total_risk = 0.0
    total_max_profit = 0.0
    with_risk = 0
    without_risk = 0
    with_profit = 0

    for pos in positions:
        risk = _safe_float(pos.get("risk"))
        if risk is not None and risk > 0:
            total_risk += risk
            with_risk += 1
        else:
            without_risk += 1

        profit = _safe_float(pos.get("max_profit"))
        if profit is not None and profit > 0:
            total_max_profit += profit
            with_profit += 1

    utilization = None
    if account_equity is not None and account_equity > 0:
        utilization = round(total_risk / account_equity, 4)

    # Coverage: how many positions have risk data
    total = with_risk + without_risk
    if with_risk == 0:
        coverage = "none"
    elif without_risk == 0:
        coverage = "full"
    else:
        coverage = "partial"

    return {
        "coverage": coverage,
        "total_risk": round(total_risk, 2),
        "total_max_profit": round(total_max_profit, 2) if with_profit > 0 else None,
        "positions_with_risk": with_risk,
        "positions_without_risk": without_risk,
        "utilization_pct": utilization,
        "account_equity_provided": account_equity is not None,
    }


# ── Greeks exposure ──────────────────────────────────────────────────

def _build_greeks_exposure(
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate portfolio-level Greeks."""
    totals = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    with_greeks = 0
    without_greeks = 0

    for pos in positions:
        has_any = False
        for greek in ("delta", "gamma", "theta", "vega"):
            val = _safe_float(pos.get(greek))
            if val is not None:
                totals[greek] += val
                has_any = True
        if has_any:
            with_greeks += 1
        else:
            without_greeks += 1

    total = with_greeks + without_greeks
    if with_greeks == 0:
        coverage = "none"
    elif without_greeks == 0:
        coverage = "full"
    else:
        coverage = "partial"

    return {
        "coverage": coverage,
        "delta": round(totals["delta"], 4),
        "gamma": round(totals["gamma"], 4),
        "theta": round(totals["theta"], 4),
        "vega": round(totals["vega"], 4),
        "positions_with_greeks": with_greeks,
        "positions_without_greeks": without_greeks,
    }


# ── Event exposure ───────────────────────────────────────────────────

def _build_event_exposure(
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate event-tagged positions."""
    event_counts: Counter[str] = Counter()
    for pos in positions:
        tag = pos.get("event_tag")
        if tag and isinstance(tag, str) and tag.strip():
            event_counts[tag.strip()] += 1

    if not event_counts:
        return {"coverage": "none", "events": []}

    events = [
        {"event": e, "position_count": c}
        for e, c in event_counts.most_common()
    ]

    return {
        "coverage": "partial",  # event tags are opt-in
        "events": events,
    }


# ── Correlation exposure ─────────────────────────────────────────────

def _build_correlation_exposure(
    positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Group positions by correlated-asset clusters.

    Uses predefined static clusters (SPY/SPX/XSP → sp500, QQQ/NDX → nasdaq,
    etc.) to detect overlapping exposure across related underlyings.

    Method: ``static_cluster`` — heuristic grouping based on known ETF
    families.  Not derived from measured covariance or live market data.
    """
    cluster_risk: dict[str, float] = {}
    cluster_count: Counter[str] = Counter()
    cluster_symbols: dict[str, set[str]] = {}

    for pos in positions:
        sym = pos["symbol"]
        cluster = _SYMBOL_TO_CLUSTER.get(sym)
        if cluster:
            risk = _safe_float(pos.get("risk")) or 0.0
            cluster_risk[cluster] = cluster_risk.get(cluster, 0.0) + risk
            cluster_count[cluster] += 1
            cluster_symbols.setdefault(cluster, set()).add(sym)

    total_risk = sum(cluster_risk.values())
    clusters_out: dict[str, Any] = {}
    for cluster in sorted(cluster_risk.keys()):
        r = cluster_risk[cluster]
        clusters_out[cluster] = {
            "count": cluster_count[cluster],
            "risk": round(r, 2),
            "share": round(r / total_risk, 4) if total_risk > 0 else 0.0,
            "symbols": sorted(cluster_symbols.get(cluster, set())),
        }

    concentrated = any(
        v["share"] >= _CORRELATION_CLUSTER_THRESHOLD and v["count"] > 1
        for v in clusters_out.values()
    )

    return {
        "clusters": clusters_out,
        "concentrated": concentrated,
        "method": "static_cluster",
        "method_note": "Predefined ETF clusters; no live correlation data.",
    }


# ── Dimension coverage ──────────────────────────────────────────────

def _build_dimension_coverage(
    directional: dict,
    underlying_conc: dict,
    sector_conc: dict,
    strategy_conc: dict,
    expiration_conc: dict,
    capital: dict,
    greeks: dict,
    event_exp: dict,
    correlation: dict,
) -> dict[str, str]:
    """Build per-dimension evaluation quality summary.

    Possible values per dimension:
    - fully_evaluated:      dimension has full input data, strong inference
    - partially_evaluated:  dimension computed but with incomplete inputs
    - heuristic:            dimension computed using heuristic/static logic
    - unavailable:          dimension could not be evaluated (no data)

    This lets downstream consumers distinguish "we processed this"
    from "we know this well."
    """
    def _coverage_to_eval(coverage: str) -> str:
        if coverage == "full":
            return "fully_evaluated"
        if coverage == "partial":
            return "partially_evaluated"
        return "unavailable"

    # Directional: always fully evaluated when positions exist
    dir_eval = "fully_evaluated"

    # Underlying: always fully evaluated (only needs symbol)
    und_eval = "fully_evaluated"

    # Sector: depends on coverage tier
    sec_eval = _coverage_to_eval(sector_conc.get("coverage", "none"))

    # Strategy: always fully evaluated (falls back to "unknown" strategy)
    strat_eval = "fully_evaluated"

    # Expiration: check no_expiration_count
    no_exp = expiration_conc.get("no_expiration_count", 0)
    total_buckets = sum(
        v.get("count", 0) for v in expiration_conc.get("buckets", {}).values()
    )
    if total_buckets == 0 and no_exp > 0:
        exp_eval = "unavailable"
    elif no_exp > 0:
        exp_eval = "partially_evaluated"
    else:
        exp_eval = "fully_evaluated"

    # Capital at risk: depends on coverage
    cap_eval = _coverage_to_eval(capital.get("coverage", "none"))

    # Greeks: depends on coverage
    greek_eval = _coverage_to_eval(greeks.get("coverage", "none"))

    # Events: opt-in, so partial is the best possible
    ev_cov = event_exp.get("coverage", "none")
    if ev_cov == "none":
        ev_eval = "unavailable"
    else:
        ev_eval = "partially_evaluated"  # event tags are opt-in, never "full"

    # Correlation: always heuristic (static clusters, not measured)
    corr_eval = "heuristic"

    return {
        "directional_exposure":     dir_eval,
        "underlying_concentration": und_eval,
        "sector_concentration":     sec_eval,
        "strategy_concentration":   strat_eval,
        "expiration_concentration": exp_eval,
        "capital_at_risk":          cap_eval,
        "greeks_exposure":          greek_eval,
        "event_exposure":           ev_eval,
        "correlation_exposure":     corr_eval,
    }


# ── Risk flags ───────────────────────────────────────────────────────

def _derive_risk_flags(
    underlying_conc: dict,
    strategy_conc: dict,
    expiration_conc: dict,
    capital: dict,
    greeks: dict,
    correlation: dict,
    directional: dict,
    sector_conc: dict,
) -> list[str]:
    """Derive machine-readable risk flags from exposure dimensions."""
    flags: list[str] = []

    if underlying_conc.get("concentrated"):
        flags.append("underlying_concentrated")

    if strategy_conc.get("concentrated"):
        flags.append("strategy_concentrated")

    if expiration_conc.get("concentrated"):
        flags.append("expiration_clustered")

    if correlation.get("concentrated"):
        flags.append("correlated_cluster_concentrated")

    # Sector concentration (only flagged when concentration is reliable)
    if sector_conc.get("concentrated") and sector_conc.get("concentration_reliable"):
        flags.append("sector_concentrated")

    # Heavy directional lean
    bull = directional.get("bullish_count", 0)
    bear = directional.get("bearish_count", 0)
    total_directional = bull + bear + directional.get("neutral_count", 0)
    if total_directional > 0:
        if bull / total_directional >= 0.80:
            flags.append("heavy_bullish_lean")
        elif bear / total_directional >= 0.80:
            flags.append("heavy_bearish_lean")

    # High utilization
    util = capital.get("utilization_pct")
    if util is not None and util > 0.50:
        flags.append("high_utilization")

    # Large negative delta
    if abs(greeks.get("delta", 0.0)) > 5.0:
        flags.append("large_aggregate_delta")

    return sorted(flags)


# ── Warning flags ────────────────────────────────────────────────────

def _derive_warning_flags(
    positions: list[dict],
    greeks: dict,
    sector_conc: dict,
    event_exp: dict,
) -> list[str]:
    """Derive advisory warning flags (data-quality caveats)."""
    warnings: list[str] = []

    if greeks.get("coverage") == "partial":
        warnings.append("greeks_partial_coverage")
    elif greeks.get("coverage") == "none":
        warnings.append("greeks_unavailable")

    if sector_conc.get("coverage") == "none":
        warnings.append("sector_data_unavailable")
    elif sector_conc.get("coverage") == "partial":
        warnings.append("sector_data_partial")

    if event_exp.get("coverage") == "none":
        warnings.append("event_data_unavailable")

    # Check for positions missing risk data
    no_risk = sum(1 for p in positions if _safe_float(p.get("risk")) is None)
    if no_risk > 0 and no_risk < len(positions):
        warnings.append("risk_data_partial")
    elif no_risk == len(positions):
        warnings.append("risk_data_unavailable")

    return sorted(warnings)


# ── Status determination ─────────────────────────────────────────────

def _determine_status(
    positions: list[dict],
    greeks: dict,
    sector_conc: dict,
) -> str:
    """Determine output status: ok / partial / empty.

    - ok: positions present with reasonable coverage
    - partial: positions present but significant data gaps
    - empty: no positions
    """
    if not positions:
        return "empty"

    # Count data gaps
    gaps = 0
    if greeks.get("coverage") in ("none", "partial"):
        gaps += 1
    if sector_conc.get("coverage") == "none":
        gaps += 1

    no_risk = sum(1 for p in positions if _safe_float(p.get("risk")) is None)
    if no_risk > len(positions) / 2:
        gaps += 1

    return "partial" if gaps >= 2 else "ok"


# ── Portfolio summary ────────────────────────────────────────────────

def _build_portfolio_summary(
    directional: dict,
    underlying_conc: dict,
    capital: dict,
    greeks: dict,
    position_count: int,
    underlying_count: int,
    risk_flags: list[str],
) -> dict[str, Any]:
    """Build human-readable portfolio summary block."""
    bias = directional.get("bias", "neutral")
    total_risk = capital.get("total_risk", 0.0)

    # Risk level heuristic
    flag_count = len(risk_flags)
    if flag_count >= 3:
        risk_level = "elevated"
    elif flag_count >= 1:
        risk_level = "moderate"
    else:
        risk_level = "low"

    bias_label = {
        "bullish": "Bullish",
        "bearish": "Bearish",
        "neutral": "Neutral",
        "mixed": "Mixed",
    }.get(bias, bias)

    parts = [f"{position_count} positions across {underlying_count} underlyings."]
    parts.append(f"Directional bias: {bias_label}.")
    if total_risk > 0:
        parts.append(f"Total capital at risk: ${total_risk:,.0f}.")
    if risk_flags:
        parts.append(f"Risk flags: {', '.join(risk_flags)}.")

    return {
        "description": " ".join(parts),
        "directional_bias": bias,
        "risk_level": risk_level,
        "flags_count": flag_count,
    }
