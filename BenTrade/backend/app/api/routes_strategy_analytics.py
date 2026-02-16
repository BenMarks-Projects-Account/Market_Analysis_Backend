from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api/analytics/strategy", tags=["strategy-analytics"])


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_range_days(value: str) -> int:
    key = str(value or "90d").strip().lower()
    mapping = {"30d": 30, "90d": 90, "180d": 180, "1y": 365}
    if key in mapping:
        return mapping[key]
    if key.endswith("d"):
        try:
            return max(1, int(key[:-1]))
        except ValueError:
            return 90
    return 90


def _date_only(iso_value: str | None) -> str | None:
    if not iso_value:
        return None
    try:
        return datetime.fromisoformat(str(iso_value).replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return None


@router.get("/summary")
async def get_strategy_analytics_summary(
    request: Request,
    range: str = Query("90d", description="30d|90d|180d|1y"),
) -> dict[str, Any]:
    days = _parse_range_days(range)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    lifecycle = request.app.state.trade_lifecycle_service
    events = lifecycle.list_events()

    filtered_events: list[dict[str, Any]] = []
    for event in events:
        ts = str(event.get("ts") or "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        if dt >= cutoff:
            filtered_events.append(event)

    rows = lifecycle.get_trades()
    keyed = {str(row.get("trade_key") or ""): row for row in rows}

    closed_rows = [row for row in rows if str(row.get("state") or "").upper() == "CLOSED"]

    pnl_by_day: dict[str, float] = defaultdict(float)
    for row in closed_rows:
        key = str(row.get("trade_key") or "")
        detail = lifecycle.get_trade_history(key)
        history = detail.get("history") if isinstance(detail, dict) else []
        if not isinstance(history, list):
            history = []

        close_event = None
        for event in reversed(history):
            if str(event.get("event") or "").upper() == "CLOSE":
                close_event = event
                break
        if not close_event:
            continue

        realized = _safe_float(((close_event.get("payload") or {}).get("realized_pnl")))
        if realized is None:
            realized = _safe_float(row.get("realized_pnl"))
        if realized is None:
            continue

        day = _date_only(str(close_event.get("ts") or ""))
        if not day:
            continue
        pnl_by_day[day] += realized

    sorted_days = sorted(pnl_by_day.keys())
    cum = 0.0
    equity_curve: list[dict[str, Any]] = []
    for day in sorted_days:
        pnl = pnl_by_day[day]
        cum += pnl
        equity_curve.append({"date": day, "pnl": pnl, "cum_pnl": cum})

    strategy_group: dict[str, dict[str, Any]] = {}
    underlying_group: dict[str, dict[str, Any]] = {}

    for row in rows:
        key = str(row.get("trade_key") or "")
        snapshot = row.get("latest_snapshot") if isinstance(row.get("latest_snapshot"), dict) else {}
        strategy = str(row.get("strategy") or snapshot.get("strategy") or snapshot.get("spread_type") or "UNKNOWN")
        symbol = str(row.get("symbol") or snapshot.get("symbol") or snapshot.get("underlying") or "UNKNOWN").upper()
        realized = _safe_float(row.get("realized_pnl"))

        strat_slot = strategy_group.setdefault(strategy, {
            "strategy": strategy,
            "trades": 0,
            "wins": 0,
            "known": 0,
            "total_pnl": 0.0,
            "avg_pnl": None,
            "win_rate": None,
        })
        strat_slot["trades"] += 1
        if realized is not None:
            strat_slot["known"] += 1
            strat_slot["total_pnl"] += realized
            if realized > 0:
                strat_slot["wins"] += 1

        und_slot = underlying_group.setdefault(symbol, {
            "symbol": symbol,
            "trades": 0,
            "known": 0,
            "total_pnl": 0.0,
            "avg_pnl": None,
        })
        und_slot["trades"] += 1
        if realized is not None:
            und_slot["known"] += 1
            und_slot["total_pnl"] += realized

    by_strategy: list[dict[str, Any]] = []
    for item in strategy_group.values():
        known = int(item.get("known") or 0)
        total_pnl = float(item.get("total_pnl") or 0.0)
        item["avg_pnl"] = (total_pnl / known) if known > 0 else None
        item["win_rate"] = (float(item.get("wins") or 0) / known) if known > 0 else None
        item.pop("wins", None)
        item.pop("known", None)
        by_strategy.append(item)
    by_strategy.sort(key=lambda row: float(row.get("total_pnl") or 0.0), reverse=True)

    by_underlying: list[dict[str, Any]] = []
    for item in underlying_group.values():
        known = int(item.get("known") or 0)
        total_pnl = float(item.get("total_pnl") or 0.0)
        item["avg_pnl"] = (total_pnl / known) if known > 0 else None
        item.pop("known", None)
        by_underlying.append(item)
    by_underlying.sort(key=lambda row: float(row.get("total_pnl") or 0.0), reverse=True)

    points: list[dict[str, Any]] = []
    ev_notes: list[str] = []
    for key, row in keyed.items():
        snapshot = row.get("latest_snapshot") if isinstance(row.get("latest_snapshot"), dict) else {}
        strategy = str(row.get("strategy") or snapshot.get("strategy") or snapshot.get("spread_type") or "UNKNOWN")
        symbol = str(row.get("symbol") or snapshot.get("symbol") or snapshot.get("underlying") or "UNKNOWN").upper()
        ev_to_risk = _safe_float(snapshot.get("ev_to_risk"))
        if ev_to_risk is None:
            ev = _safe_float(snapshot.get("ev_per_share") or snapshot.get("expected_value"))
            risk = _safe_float(snapshot.get("max_loss") or snapshot.get("estimated_risk"))
            if ev is not None and risk not in (None, 0):
                ev_to_risk = ev / risk

        points.append(
            {
                "trade_key": key,
                "strategy": strategy,
                "symbol": symbol,
                "ev_to_risk": ev_to_risk,
                "realized_pnl": _safe_float(row.get("realized_pnl")),
                "regime_label": str(snapshot.get("regime_label") or snapshot.get("market_regime") or "UNKNOWN"),
            }
        )

    if not filtered_events:
        ev_notes.append("No lifecycle events found in selected range")
    if not any(point.get("realized_pnl") is not None for point in points):
        ev_notes.append("Realized P&L is partial; close events can include payload.realized_pnl")

    notes = [
        "Lifecycle ledger is the canonical analytics source",
    ]
    if not equity_curve:
        notes.append("Equity curve is empty for selected range")

    return {
        "as_of": _utc_now_iso(),
        "range": str(range),
        "equity_curve": equity_curve,
        "by_strategy": by_strategy,
        "by_underlying": by_underlying,
        "ev_vs_realized": {
            "points": points,
            "notes": ev_notes,
        },
        "notes": notes,
    }
