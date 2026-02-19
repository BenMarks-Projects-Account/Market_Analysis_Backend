from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from app.api.routes_active_trades import _build_active_payload
from app.utils.report_conformance import validate_report_file
from app.utils.trade_key import trade_key

router = APIRouter(prefix="/api/portfolio/risk", tags=["portfolio-risk"])


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bucket_from_dte(dte: int | None) -> str:
    if dte is None:
        return "90D+"
    if dte <= 7:
        return "0-7D"
    if dte <= 21:
        return "8-21D"
    if dte <= 45:
        return "22-45D"
    if dte <= 90:
        return "46-90D"
    return "90D+"


def _latest_report_trades(results_dir: Path) -> list[dict[str, Any]]:
    candidates = list(results_dir.glob("analysis_*.json"))
    try:
        outer = results_dir.parent.parent / "results"
        if outer.exists() and outer.is_dir():
            candidates.extend(list(outer.glob("analysis_*.json")))
    except Exception:
        pass

    candidates = sorted(set(candidates), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for path in candidates:
        payload = validate_report_file(path, auto_delete=True)
        if payload is None:
            continue

        trades = payload.get("trades") if isinstance(payload, dict) else payload
        if not isinstance(trades, list):
            continue

        out: list[dict[str, Any]] = []
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            rec = str((trade.get("model_evaluation") or {}).get("recommendation") or "").upper()
            if rec == "REJECT":
                continue
            out.append(trade)
        if out:
            return out
    return []


def _normalize_from_active(item: dict[str, Any]) -> dict[str, Any]:
    symbol = str(item.get("symbol") or item.get("underlying") or "").upper()
    strategy = str(item.get("strategy") or item.get("spread_type") or "")
    dte = _safe_int(item.get("dte"))
    qty = _safe_int(item.get("quantity")) or 1
    short_strike = _safe_float(item.get("short_strike"))
    long_strike = _safe_float(item.get("long_strike"))
    width = abs(short_strike - long_strike) if short_strike is not None and long_strike is not None else None
    credit = _safe_float(item.get("avg_open_price"))
    risk = None
    if width is not None:
        if credit is not None:
            risk = max(width - credit, 0.0) * abs(qty) * 100.0
        else:
            risk = width * abs(qty) * 100.0

    delta = _safe_float(item.get("delta"))
    if delta is None:
        approx_pop = _safe_float(item.get("p_win_used") or item.get("pop_delta_approx"))
        if "credit_put" in strategy:
            delta = (approx_pop - 1.0) if approx_pop is not None else 0.25
        elif "credit_call" in strategy:
            delta = (1.0 - approx_pop) if approx_pop is not None else -0.25
        elif strategy == "single":
            delta = 1.0

    gamma = _safe_float(item.get("gamma"))
    theta = _safe_float(item.get("theta"))
    vega = _safe_float(item.get("vega"))

    tkey = str(item.get("trade_key") or trade_key(
        underlying=symbol,
        expiration=item.get("expiration"),
        spread_type=strategy,
        short_strike=short_strike,
        long_strike=long_strike,
        dte=dte,
    ))

    return {
        "trade_key": tkey,
        "symbol": symbol,
        "strategy": strategy,
        "expiration": item.get("expiration"),
        "dte": dte,
        "quantity": qty,
        "risk": risk,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "reference_price": _safe_float(item.get("mark_price") or item.get("short_strike") or item.get("long_strike") or 100.0),
    }


def _normalize_from_report(item: dict[str, Any]) -> dict[str, Any]:
    symbol = str(item.get("underlying") or item.get("underlying_symbol") or item.get("symbol") or "").upper()
    strategy = str(item.get("strategy") or item.get("spread_type") or "")
    dte = _safe_int(item.get("dte"))
    qty = _safe_int(item.get("quantity") or item.get("contracts") or item.get("contracts_count")) or 1

    short_strike = _safe_float(item.get("short_strike"))
    long_strike = _safe_float(item.get("long_strike"))
    width = _safe_float(item.get("width"))
    if width is None and short_strike is not None and long_strike is not None:
        width = abs(short_strike - long_strike)

    credit = _safe_float(item.get("net_credit") or item.get("avg_open_price"))
    risk = _safe_float(item.get("max_loss"))
    if risk is None and width is not None:
        if credit is not None:
            risk = max(width - credit, 0.0) * abs(qty) * 100.0
        else:
            risk = width * abs(qty) * 100.0

    pop = _safe_float(item.get("p_win_used") or item.get("pop_delta_approx"))
    delta = _safe_float(item.get("delta"))
    if delta is None:
        if "credit_put" in strategy:
            delta = (pop - 1.0) if pop is not None else 0.25
        elif "credit_call" in strategy:
            delta = (1.0 - pop) if pop is not None else -0.25

    tkey = trade_key(
        underlying=symbol,
        expiration=item.get("expiration"),
        spread_type=strategy,
        short_strike=short_strike,
        long_strike=long_strike,
        dte=dte,
    )

    return {
        "trade_key": tkey,
        "symbol": symbol,
        "strategy": strategy,
        "expiration": item.get("expiration"),
        "dte": dte,
        "quantity": qty,
        "risk": risk,
        "delta": delta,
        "gamma": _safe_float(item.get("gamma")),
        "theta": _safe_float(item.get("theta")),
        "vega": _safe_float(item.get("vega")),
        "reference_price": _safe_float(item.get("underlying_price") or short_strike or long_strike or 100.0),
    }


def _aggregate_by_underlying(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").upper() or "UNKNOWN"
        slot = grouped.setdefault(symbol, {
            "symbol": symbol,
            "delta": 0.0,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "risk": 0.0,
            "trade_count": 0,
            "trades": [],
        })
        for greek in ("delta", "gamma", "theta", "vega"):
            value = _safe_float(row.get(greek))
            if value is not None:
                slot[greek] += value
        risk = _safe_float(row.get("risk"))
        if risk is not None:
            slot["risk"] += risk
        slot["trade_count"] += 1
        slot["trades"].append({
            "trade_key": row.get("trade_key"),
            "strategy": row.get("strategy"),
            "dte": row.get("dte"),
            "risk": row.get("risk"),
        })

    return sorted(grouped.values(), key=lambda item: float(item.get("risk") or 0.0), reverse=True)


def _aggregate_by_bucket(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = ["0-7D", "8-21D", "22-45D", "46-90D", "90D+"]
    grouped = {bucket: {"bucket": bucket, "risk": 0.0, "trade_count": 0} for bucket in buckets}

    for row in rows:
        bucket = _bucket_from_dte(_safe_int(row.get("dte")))
        slot = grouped[bucket]
        slot["trade_count"] += 1
        risk = _safe_float(row.get("risk"))
        if risk is not None:
            slot["risk"] += risk

    return [grouped[bucket] for bucket in buckets]


def _portfolio_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "net_liq": None}
    for row in rows:
        for greek in ("delta", "gamma", "theta", "vega"):
            value = _safe_float(row.get(greek))
            if value is not None:
                totals[greek] += value
    return totals


def _scenario_rows(rows: list[dict[str, Any]], by_symbol: list[dict[str, Any]]) -> list[dict[str, Any]]:
    top_symbol = by_symbol[0].get("symbol") if by_symbol else "SPY"
    pct_moves = [-0.05, -0.02, -0.01, 0.01, 0.02, 0.05]

    out: list[dict[str, Any]] = []
    for pct in pct_moves:
        pnl = 0.0
        known = False
        for row in rows:
            if str(row.get("symbol") or "").upper() != str(top_symbol).upper():
                continue
            delta = _safe_float(row.get("delta"))
            ref = _safe_float(row.get("reference_price")) or 100.0
            qty = abs(_safe_float(row.get("quantity")) or 1.0)
            if delta is None:
                continue
            pnl += delta * ref * pct * qty * 100.0
            known = True

        out.append(
            {
                "name": f"{top_symbol} {pct * 100:+.0f}%",
                "shock": {"symbol": top_symbol, "pct_move": pct, "iv_shift": 0.0},
                "pnl_estimate": pnl if known else None,
            }
        )

    vol_shift = 0.05
    pnl_vol = 0.0
    known_vol = False
    for row in rows:
        if str(row.get("symbol") or "").upper() != str(top_symbol).upper():
            continue
        vega = _safe_float(row.get("vega"))
        qty = abs(_safe_float(row.get("quantity")) or 1.0)
        if vega is None:
            continue
        pnl_vol += vega * vol_shift * qty * 100.0
        known_vol = True

    out.append(
        {
            "name": f"{top_symbol} IV +5%",
            "shock": {"symbol": top_symbol, "pct_move": 0.0, "iv_shift": vol_shift},
            "pnl_estimate": pnl_vol if known_vol else None,
        }
    )

    return out


def _build_warnings(rows: list[dict[str, Any]], by_symbol: list[dict[str, Any]], policy: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    warnings.append("Greeks may be approximate for some trades")
    warnings.append("Scenario P&L is approximate")

    total_risk = sum(_safe_float(row.get("risk")) or 0.0 for row in rows)
    max_risk_total = _safe_float(policy.get("max_risk_total"))
    if max_risk_total is not None and total_risk > max_risk_total:
        warnings.append("Total risk exceeds max_risk_total policy")

    max_risk_per_underlying = _safe_float(policy.get("max_risk_per_underlying"))
    if max_risk_per_underlying is not None:
        for item in by_symbol:
            risk = _safe_float(item.get("risk"))
            if risk is not None and risk > max_risk_per_underlying:
                warnings.append(f"{item.get('symbol')} risk exceeds max_risk_per_underlying")

    max_symbol_risk_pct = _safe_float(policy.get("max_symbol_risk_pct"))
    portfolio_size = _safe_float(policy.get("portfolio_size"))
    if max_symbol_risk_pct is not None and portfolio_size not in (None, 0):
        for item in by_symbol:
            risk = _safe_float(item.get("risk"))
            if risk is None:
                continue
            if risk > portfolio_size * max_symbol_risk_pct:
                warnings.append(f"{item.get('symbol')} exceeds max_symbol_risk_pct concentration")

    if not rows:
        warnings.append("No open trades found for risk matrix")

    deduped: list[str] = []
    for msg in warnings:
        if msg and msg not in deduped:
            deduped.append(msg)
    return deduped


@router.get("/matrix")
async def get_portfolio_risk_matrix(request: Request) -> dict[str, Any]:
    source = "none"
    rows: list[dict[str, Any]] = []

    try:
        active_payload = await _build_active_payload(request)
    except Exception:
        active_payload = {}

    active_trades = active_payload.get("active_trades") if isinstance(active_payload, dict) else []
    active_error = str((active_payload or {}).get("error") or "")
    if isinstance(active_trades, list) and active_trades and not active_error:
        rows = [_normalize_from_active(item) for item in active_trades if isinstance(item, dict)]
        source = "tradier"

    report_rows: list[dict[str, Any]] = []
    report_trades = _latest_report_trades(Path(request.app.state.results_dir))
    if report_trades:
        report_rows = [_normalize_from_report(item) for item in report_trades if isinstance(item, dict)]

    if rows and report_rows:
        source = "mixed"
    elif not rows and report_rows:
        rows = report_rows
        source = "report"

    portfolio = _portfolio_totals(rows)
    by_underlying = _aggregate_by_underlying(rows)
    by_expiration_bucket = _aggregate_by_bucket(rows)
    scenarios = _scenario_rows(rows, by_underlying)

    policy = {}
    try:
        policy = request.app.state.risk_policy_service.get_policy()
    except Exception:
        policy = {}

    warnings = _build_warnings(rows, by_underlying, policy)

    source_health = {}
    try:
        source_health = request.app.state.base_data_service.get_source_health_snapshot()
    except Exception:
        source_health = {}

    return {
        "as_of": _utc_now_iso(),
        "source": source,
        "portfolio": portfolio,
        "by_underlying": by_underlying,
        "by_expiration_bucket": by_expiration_bucket,
        "scenarios": scenarios,
        "warnings": warnings,
        "source_health": source_health,
        "trades": rows,
    }
