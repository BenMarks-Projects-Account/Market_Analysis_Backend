from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Request

from app.utils.http import UpstreamError
from app.utils.trade_key import trade_key

router = APIRouter(prefix="/api/trading", tags=["trading"])
logger = logging.getLogger(__name__)


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _extract_positions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    positions = (((payload or {}).get("positions") or {}).get("position"))
    return _as_list(positions)


def _extract_orders(payload: dict[str, Any]) -> list[dict[str, Any]]:
    orders = (((payload or {}).get("orders") or {}).get("order"))
    return _as_list(orders)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    return str(value)


def _extract_retry_after(details: dict[str, Any] | None) -> int | None:
    payload = details or {}
    body = str(payload.get("body") or "")
    for marker in ("retry_after", "retry-after"):
        idx = body.lower().find(marker)
        if idx >= 0:
            segment = body[idx:idx + 64]
            digits = "".join(ch for ch in segment if ch.isdigit())
            if digits:
                try:
                    return int(digits)
                except ValueError:
                    return None
    return None


def _error_payload_from_exception(exc: Exception, *, fallback_message: str) -> dict[str, Any]:
    error = {
        "message": fallback_message,
        "type": type(exc).__name__,
        "upstream_status": None,
        "upstream_body_snippet": None,
    }

    if isinstance(exc, UpstreamError):
        details = exc.details or {}
        status_code = details.get("status_code")
        error["upstream_status"] = int(status_code) if isinstance(status_code, int) else None
        body = str(details.get("body") or "")
        error["upstream_body_snippet"] = body[:400] if body else None

        message_lower = str(exc).lower()
        exception_text = str(details.get("exception") or "").lower()
        if "timeout" in message_lower or "timeout" in exception_text or "timed out" in exception_text:
            error["message"] = "Timeout"
        elif error["upstream_status"] in (401, 403):
            error["message"] = "Tradier API key invalid or unauthorized"
        elif error["upstream_status"] == 429:
            error["message"] = "Tradier rate limited or unavailable"
            retry_after = _extract_retry_after(details)
            if retry_after is not None:
                error["retry_after"] = retry_after
        elif error["upstream_status"] and error["upstream_status"] >= 500:
            error["message"] = "Tradier service unavailable"
        else:
            error["message"] = str(exc)
    else:
        text = str(exc).lower()
        if "timeout" in text or "timed out" in text:
            error["message"] = "Timeout"
        else:
            error["message"] = str(exc)

    return error


def _parse_occ_symbol(option_symbol: str) -> dict[str, Any] | None:
    s = str(option_symbol or "").strip().upper()
    if len(s) < 15:
        return None

    idx = None
    for i, ch in enumerate(s):
        if ch.isdigit():
            idx = i
            break
    if idx is None or idx + 15 > len(s):
        return None

    root = s[:idx].strip()
    yymmdd = s[idx:idx + 6]
    opt_type = s[idx + 6:idx + 7]
    strike_raw = s[idx + 7:idx + 15]

    try:
        yy = int(yymmdd[0:2])
        mm = int(yymmdd[2:4])
        dd = int(yymmdd[4:6])
        year = 2000 + yy
        expiration = f"{year:04d}-{mm:02d}-{dd:02d}"
        strike = int(strike_raw) / 1000.0
    except Exception:
        return None

    if opt_type not in ("C", "P"):
        return None

    return {
        "underlying": root,
        "expiration": expiration,
        "option_type": "call" if opt_type == "C" else "put",
        "strike": strike,
    }


def _signed_qty(side: str | None, quantity: int | None) -> int:
    q = int(quantity or 0)
    sign = -1 if str(side or "").lower().startswith("sell") else 1
    return sign * q


def _normalize_positions(raw_positions: list[dict[str, Any]], quote_map: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in raw_positions:
        symbol = str(row.get("symbol") or row.get("underlying") or "").upper()
        parsed_occ = _parse_occ_symbol(symbol)

        underlying = (parsed_occ or {}).get("underlying") or symbol
        quantity = _to_int(row.get("quantity") or row.get("qty") or row.get("open_quantity"))
        avg_open_price = _to_float(
            row.get("cost_basis")
            or row.get("average_open_price")
            or row.get("avg_open_price")
            or row.get("price")
        )
        mark_price = _to_float(
            row.get("last")
            or row.get("mark")
            or row.get("market_value")
        )
        unrealized = _to_float(
            row.get("unrealized_pl")
            or row.get("unrealized_pnl")
            or row.get("gain_loss")
        )

        quote = quote_map.get(underlying) if isinstance(quote_map, dict) else None
        quote_mark = _to_float((quote or {}).get("last") or (quote or {}).get("mark") or (quote or {}).get("close"))

        if mark_price is None:
            mark_price = quote_mark

        if unrealized is None and mark_price is not None and avg_open_price is not None and quantity is not None:
            unrealized = (mark_price - avg_open_price) * quantity

        unrealized_pct = None
        if unrealized is not None and avg_open_price not in (None, 0) and quantity not in (None, 0):
            basis = avg_open_price * abs(quantity)
            if basis > 0:
                unrealized_pct = unrealized / basis

        out.append(
            {
                "position_key": f"{underlying}|{symbol}|{parsed_occ.get('expiration') if parsed_occ else ''}|{parsed_occ.get('strike') if parsed_occ else ''}",
                "symbol": symbol,
                "underlying": underlying,
                "quantity": quantity,
                "avg_open_price": avg_open_price,
                "mark_price": mark_price,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": unrealized_pct,
                "expiration": parsed_occ.get("expiration") if parsed_occ else None,
                "option_type": parsed_occ.get("option_type") if parsed_occ else None,
                "strike": parsed_occ.get("strike") if parsed_occ else None,
                "raw": row,
            }
        )

    return out


def _normalize_orders(raw_orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in raw_orders:
        out.append(
            {
                "id": str(row.get("id") or row.get("order_id") or row.get("tag") or ""),
                "symbol": str(row.get("symbol") or row.get("underlying") or "").upper(),
                "status": str(row.get("status") or "UNKNOWN").upper(),
                "side": str(row.get("side") or row.get("transaction") or "").lower(),
                "quantity": _to_int(row.get("quantity") or row.get("qty")),
                "price": _to_float(row.get("price") or row.get("avg_fill_price") or row.get("limit_price")),
                "created_at": row.get("create_date") or row.get("created_at") or row.get("transaction_date"),
                "raw": row,
            }
        )
    return out


def _compute_dte(expiration: str | None) -> int | None:
    if not expiration:
        return None
    try:
        exp = datetime.strptime(expiration, "%Y-%m-%d").date()
    except Exception:
        return None
    return (exp - datetime.now(timezone.utc).date()).days


def _build_active_trades(positions: list[dict[str, Any]], orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for pos in positions:
        if not pos.get("option_type"):
            continue
        key = (str(pos.get("underlying") or ""), str(pos.get("expiration") or ""), str(pos.get("option_type") or ""))
        grouped.setdefault(key, []).append(pos)

    active: list[dict[str, Any]] = []
    used_keys: set[str] = set()

    for (underlying, expiration, option_type), legs in grouped.items():
        sells = [leg for leg in legs if (leg.get("quantity") or 0) < 0]
        buys = [leg for leg in legs if (leg.get("quantity") or 0) > 0]
        if not sells or not buys:
            continue

        short_leg = sells[0]
        long_leg = buys[0]
        quantity = min(abs(int(short_leg.get("quantity") or 0)), abs(int(long_leg.get("quantity") or 0)))
        if quantity <= 0:
            continue

        avg_open_price = None
        if short_leg.get("avg_open_price") is not None and long_leg.get("avg_open_price") is not None:
            avg_open_price = float(short_leg.get("avg_open_price") or 0) - float(long_leg.get("avg_open_price") or 0)

        mark_price = None
        if short_leg.get("mark_price") is not None and long_leg.get("mark_price") is not None:
            mark_price = float(short_leg.get("mark_price") or 0) - float(long_leg.get("mark_price") or 0)

        unrealized = None
        parts = [short_leg.get("unrealized_pnl"), long_leg.get("unrealized_pnl")]
        if all(p is not None for p in parts):
            unrealized = float(parts[0]) + float(parts[1])

        if unrealized is None and mark_price is not None and avg_open_price is not None:
            unrealized = (avg_open_price - mark_price) * quantity * 100

        unrealized_pct = None
        if unrealized is not None and avg_open_price not in (None, 0):
            basis = abs(float(avg_open_price)) * quantity * 100
            if basis > 0:
                unrealized_pct = unrealized / basis

        strategy = "put_credit_spread" if option_type == "put" else "call_credit_spread"
        dte = _compute_dte(expiration)
        stable_key = trade_key(
            underlying=underlying,
            expiration=expiration,
            spread_type=strategy,
            short_strike=short_leg.get("strike"),
            long_strike=long_leg.get("strike"),
            dte=dte,
        )

        order_hit = any(
            str(order.get("symbol") or "").upper() in {str(short_leg.get("symbol") or "").upper(), str(long_leg.get("symbol") or "").upper(), underlying.upper()}
            and str(order.get("status") or "").upper() in {"OPEN", "PENDING", "WORKING", "PARTIALLY_FILLED"}
            for order in orders
        )

        active.append(
            {
                "trade_key": stable_key,
                "trade_id": stable_key,
                "symbol": underlying,
                "strategy": strategy,
                "strategy_id": strategy,
                "spread_type": strategy,
                "short_strike": short_leg.get("strike"),
                "long_strike": long_leg.get("strike"),
                "expiration": expiration,
                "legs": [
                    {
                        "symbol": short_leg.get("symbol"),
                        "side": "sell",
                        "qty": quantity,
                        "price": short_leg.get("mark_price") or short_leg.get("avg_open_price"),
                    },
                    {
                        "symbol": long_leg.get("symbol"),
                        "side": "buy",
                        "qty": quantity,
                        "price": long_leg.get("mark_price") or long_leg.get("avg_open_price"),
                    },
                ],
                "quantity": quantity,
                "avg_open_price": avg_open_price,
                "mark_price": mark_price,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": unrealized_pct,
                "dte": dte,
                "status": "CLOSING" if order_hit else "OPEN",
                "notes": None,
            }
        )

        used_keys.add(str(short_leg.get("position_key") or ""))
        used_keys.add(str(long_leg.get("position_key") or ""))

    for pos in positions:
        position_key = str(pos.get("position_key") or "")
        if position_key in used_keys:
            continue

        underlying = str(pos.get("underlying") or pos.get("symbol") or "").upper()
        quantity = abs(int(pos.get("quantity") or 0))
        if quantity <= 0:
            continue

        mark_price = pos.get("mark_price")
        avg_open_price = pos.get("avg_open_price")
        unrealized = pos.get("unrealized_pnl")

        if unrealized is None and mark_price is not None and avg_open_price is not None:
            if pos.get("option_type"):
                unrealized = (float(mark_price) - float(avg_open_price)) * quantity * 100
            else:
                unrealized = (float(mark_price) - float(avg_open_price)) * quantity

        unrealized_pct = pos.get("unrealized_pnl_pct")
        if unrealized_pct is None and unrealized is not None and avg_open_price not in (None, 0):
            basis = abs(float(avg_open_price)) * quantity * (100 if pos.get("option_type") else 1)
            if basis > 0:
                unrealized_pct = float(unrealized) / basis

        dte = _compute_dte(pos.get("expiration"))
        stable_key = trade_key(
            underlying=underlying,
            expiration=pos.get("expiration"),
            spread_type="single",
            short_strike=pos.get("strike"),
            long_strike=None,
            dte=dte,
        )
        order_hit = any(
            str(order.get("symbol") or "").upper() in {str(pos.get("symbol") or "").upper(), underlying.upper()}
            and str(order.get("status") or "").upper() in {"OPEN", "PENDING", "WORKING", "PARTIALLY_FILLED"}
            for order in orders
        )

        side = "buy"
        if (pos.get("quantity") or 0) < 0:
            side = "sell"

        active.append(
            {
                "trade_key": stable_key,
                "trade_id": stable_key,
                "symbol": underlying,
                "strategy": "single",
                "strategy_id": "single",
                "spread_type": "single",
                "short_strike": pos.get("strike"),
                "long_strike": None,
                "expiration": pos.get("expiration"),
                "legs": [
                    {
                        "symbol": pos.get("symbol"),
                        "side": side,
                        "qty": quantity,
                        "price": mark_price or avg_open_price,
                    }
                ],
                "quantity": quantity,
                "avg_open_price": avg_open_price,
                "mark_price": mark_price,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": unrealized_pct,
                "dte": dte,
                "status": "CLOSING" if order_hit else "OPEN",
                "notes": None,
            }
        )

    return active


async def _build_active_payload(request: Request) -> dict[str, Any]:
    settings = request.app.state.trading_service.settings
    if not settings.TRADIER_TOKEN or not settings.TRADIER_ACCOUNT_ID:
        return {
            "ok": False,
            "as_of": _utc_iso_now(),
            "source": "tradier",
            "positions": [],
            "orders": [],
            "active_trades": [],
            "source_health": request.app.state.base_data_service.get_source_health_snapshot(),
            "error": {
                "message": "Tradier credentials not configured",
                "type": "ConfigurationError",
                "upstream_status": None,
                "upstream_body_snippet": None,
            },
        }

    try:
        raw_positions_payload = await request.app.state.tradier_client.get_positions()
        raw_orders_payload = await request.app.state.tradier_client.get_orders(status="open")
    except Exception as exc:
        error = _error_payload_from_exception(exc, fallback_message="Failed to load active trades from Tradier")
        logger.exception("trading.active_fetch_failed error=%s", error)
        return {
            "ok": False,
            "as_of": _utc_iso_now(),
            "source": "tradier",
            "positions": [],
            "orders": [],
            "active_trades": [],
            "source_health": request.app.state.base_data_service.get_source_health_snapshot(),
            "error": error,
        }

    raw_positions = _extract_positions(raw_positions_payload)
    raw_orders = _extract_orders(raw_orders_payload)

    symbols = sorted(
        {
            str(item.get("underlying") or item.get("symbol") or "").upper()
            for item in raw_positions
            if str(item.get("underlying") or item.get("symbol") or "").strip()
        }
    )

    quote_map = {}
    if symbols:
        try:
            quote_map = await request.app.state.tradier_client.get_quotes(symbols)
        except Exception as exc:
            logger.warning("trading.quote_fetch_failed exc=%s", exc)
            quote_map = {}

    try:
        positions = _normalize_positions(raw_positions, quote_map)
        orders = _normalize_orders(raw_orders)
        active_trades = _build_active_trades(positions, orders)
    except Exception as exc:
        logger.exception("trading.serialization_failed exc=%s", exc)
        return {
            "ok": False,
            "as_of": _utc_iso_now(),
            "source": "tradier",
            "positions": [],
            "orders": [],
            "active_trades": [],
            "source_health": request.app.state.base_data_service.get_source_health_snapshot(),
            "error": {
                "message": "Failed to serialize broker payload",
                "type": type(exc).__name__,
                "upstream_status": None,
                "upstream_body_snippet": None,
            },
        }

    return {
        "ok": True,
        "as_of": _utc_iso_now(),
        "source": "tradier",
        "positions": _json_safe(positions),
        "orders": _json_safe(orders),
        "active_trades": _json_safe(active_trades),
        "source_health": request.app.state.base_data_service.get_source_health_snapshot(),
        "raw": {
            "positions": _json_safe(raw_positions_payload),
            "orders": _json_safe(raw_orders_payload),
        },
    }


@router.get("/active")
async def get_active_trades(request: Request) -> dict[str, Any]:
    return await _build_active_payload(request)


@router.post("/active/refresh")
async def refresh_active_trades(request: Request) -> dict[str, Any]:
    return await _build_active_payload(request)


@router.get("/positions")
async def get_tradier_positions(request: Request) -> dict[str, Any]:
    payload = await _build_active_payload(request)
    return {
        "ok": bool(payload.get("ok", False)),
        "as_of": payload.get("as_of"),
        "source": payload.get("source", "tradier"),
        "positions": payload.get("positions") or [],
        "source_health": payload.get("source_health") or {},
        "error": payload.get("error") if not payload.get("ok", False) else None,
    }


@router.get("/orders/open")
async def get_tradier_open_orders(request: Request) -> dict[str, Any]:
    payload = await _build_active_payload(request)
    return {
        "ok": bool(payload.get("ok", False)),
        "as_of": payload.get("as_of"),
        "source": payload.get("source", "tradier"),
        "orders": payload.get("orders") or [],
        "source_health": payload.get("source_health") or {},
        "error": payload.get("error") if not payload.get("ok", False) else None,
    }


@router.get("/account")
async def get_tradier_account(request: Request) -> dict[str, Any]:
    settings = request.app.state.trading_service.settings
    if not settings.TRADIER_TOKEN or not settings.TRADIER_ACCOUNT_ID:
        return {
            "ok": False,
            "as_of": _utc_iso_now(),
            "source": "tradier",
            "account": {},
            "error": {
                "message": "Tradier credentials not configured",
                "type": "ConfigurationError",
                "upstream_status": None,
                "upstream_body_snippet": None,
            },
        }

    try:
        account_payload = await request.app.state.tradier_client.get_balances()
    except Exception as exc:
        error = _error_payload_from_exception(exc, fallback_message="Failed to load account balances from Tradier")
        logger.exception("trading.account_fetch_failed error=%s", error)
        return {
            "ok": False,
            "as_of": _utc_iso_now(),
            "source": "tradier",
            "account": {},
            "source_health": request.app.state.base_data_service.get_source_health_snapshot(),
            "error": error,
        }

    return {
        "ok": True,
        "as_of": _utc_iso_now(),
        "source": "tradier",
        "account": _json_safe(account_payload),
        "source_health": request.app.state.base_data_service.get_source_health_snapshot(),
        "error": None,
    }
