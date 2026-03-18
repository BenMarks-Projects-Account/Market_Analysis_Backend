from __future__ import annotations

import json as _json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query, Request

from app.utils.http import UpstreamError, request_json
from app.utils.trade_key import trade_key
from common.json_repair import extract_and_repair_json

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
    # Tradier returns {"positions":"null"} (string) when no positions exist.
    # Guard against non-dict values from the API.
    positions_wrapper = (payload or {}).get("positions")
    if not isinstance(positions_wrapper, dict):
        return []
    positions = positions_wrapper.get("position")
    return _as_list(positions)


def _extract_orders(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # Tradier returns {"orders":"null"} (string) when no orders exist.
    orders_wrapper = (payload or {}).get("orders")
    if not isinstance(orders_wrapper, dict):
        return []
    orders = orders_wrapper.get("order")
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
    """Build a structured error payload from an exception.

    STOP MASKING ERRORS: propagate the actual Tradier HTTP status and
    error body so the frontend can display the real reason (not just
    a generic "service unavailable").
    """
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
            error["message"] = "Timeout connecting to Tradier"
        elif error["upstream_status"] in (401, 403):
            error["message"] = f"Tradier {error['upstream_status']}: API key invalid or unauthorized"
        elif error["upstream_status"] == 429:
            error["message"] = f"Tradier 429: rate limited"
            retry_after = _extract_retry_after(details)
            if retry_after is not None:
                error["retry_after"] = retry_after
        elif error["upstream_status"] == 404:
            error["message"] = f"Tradier 404: account or endpoint not found"
        elif error["upstream_status"] and error["upstream_status"] >= 500:
            error["message"] = f"Tradier {error['upstream_status']}: server error — {body[:200] or 'no details'}"
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

        # --- Per-share avg entry price ---
        # Tradier fields: cost_basis = TOTAL cost (qty * price_per_share).
        # Prefer explicit per-share fields first; fall back to cost_basis / qty.
        avg_open_price = _to_float(
            row.get("average_open_price")     # Tradier standard option field
            or row.get("avg_open_price")      # alias
            or row.get("average_price")       # Tradier equity per-share field
            or row.get("avg_cost")            # common broker alias
            or row.get("price")               # fill price fallback
        )
        cost_basis_total = _to_float(row.get("cost_basis"))
        if avg_open_price is None and cost_basis_total is not None and quantity not in (None, 0):
            # Derive per-share from total: avg_entry = cost_basis_total / qty
            avg_open_price = round(cost_basis_total / abs(quantity), 4)

        # --- Per-share mark/current price ---
        # Do NOT use market_value here — that is a total, not per-share.
        mark_price = _to_float(
            row.get("last")
            or row.get("mark")
        )
        unrealized = _to_float(
            row.get("unrealized_pl")
            or row.get("unrealized_pnl")
            or row.get("gain_loss")
        )

        quote = quote_map.get(underlying) if isinstance(quote_map, dict) else None
        quote_mark = _to_float((quote or {}).get("last") or (quote or {}).get("mark") or (quote or {}).get("close"))

        # --- Day-change fields from Tradier quote ---
        # Tradier quotes include "change" (dollar) and "change_percentage" (percent).
        day_change = _to_float((quote or {}).get("change"))
        day_change_pct = _to_float((quote or {}).get("change_percentage"))

        if mark_price is None:
            mark_price = quote_mark

        # --- Compute cost_basis_total if not provided by Tradier ---
        if cost_basis_total is None and avg_open_price is not None and quantity not in (None, 0):
            cost_basis_total = round(avg_open_price * abs(quantity), 2)

        # --- Market value (total) ---
        market_value = _to_float(row.get("market_value"))
        if market_value is None and mark_price is not None and quantity not in (None, 0):
            market_value = round(mark_price * abs(quantity), 2)

        # --- Unrealized P&L (total) ---
        # Formula: (current_price - avg_entry_price) * qty
        if unrealized is None and mark_price is not None and avg_open_price is not None and quantity is not None:
            unrealized = round((mark_price - avg_open_price) * quantity, 2)

        # --- P&L % ---
        # Formula: unrealized_pl / cost_basis_total * 100  (returned as decimal)
        unrealized_pct = None
        if unrealized is not None and cost_basis_total not in (None, 0):
            unrealized_pct = unrealized / abs(cost_basis_total)

        out.append(
            {
                "position_key": f"{underlying}|{symbol}|{parsed_occ.get('expiration') if parsed_occ else ''}|{parsed_occ.get('strike') if parsed_occ else ''}",
                "symbol": symbol,
                "underlying": underlying,
                "quantity": quantity,
                "avg_open_price": avg_open_price,
                "mark_price": mark_price,
                "cost_basis_total": cost_basis_total,
                "market_value": market_value,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": unrealized_pct,
                "expiration": parsed_occ.get("expiration") if parsed_occ else None,
                "option_type": parsed_occ.get("option_type") if parsed_occ else None,
                "strike": parsed_occ.get("strike") if parsed_occ else None,
                "day_change": day_change,
                "day_change_pct": day_change_pct,
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
                "day_change": short_leg.get("day_change"),
                "day_change_pct": short_leg.get("day_change_pct"),
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
        avg_open_price = pos.get("avg_open_price")  # already per-share from _normalize_positions
        cost_basis_total = pos.get("cost_basis_total")
        market_value = pos.get("market_value")
        unrealized = pos.get("unrealized_pnl")

        # Recompute P&L only if Tradier didn't provide it
        if unrealized is None and mark_price is not None and avg_open_price is not None:
            multiplier = 100 if pos.get("option_type") else 1
            unrealized = round((float(mark_price) - float(avg_open_price)) * quantity * multiplier, 2)

        # P&L % = unrealized / cost_basis_total
        unrealized_pct = pos.get("unrealized_pnl_pct")
        if unrealized_pct is None and unrealized is not None and cost_basis_total not in (None, 0):
            unrealized_pct = float(unrealized) / abs(float(cost_basis_total))

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
                "cost_basis_total": cost_basis_total,
                "market_value": market_value,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": unrealized_pct,
                "day_change": pos.get("day_change"),
                "day_change_pct": pos.get("day_change_pct"),
                "dte": dte,
                "status": "CLOSING" if order_hit else "OPEN",
                "notes": None,
            }
        )

    return active


def _resolve_creds(settings, account_mode: str):
    """Resolve Tradier credentials — delegates to the single shared resolver.

    Inputs: settings (app.config.Settings or SimpleNamespace), account_mode ("live" | "paper").
    Output: TradierCredentials dataclass.

    This is a thin wrapper around get_tradier_context() from tradier_credentials.py,
    which is the SINGLE SOURCE OF TRUTH for credential resolution.
    Both Active Trades fetch AND trade execution use the same resolver.

    Raises ValueError when the requested mode's credentials are missing.
    """
    from app.trading.tradier_credentials import get_tradier_context

    return get_tradier_context(settings, account_type=account_mode)


async def _build_active_payload(
    request: Request,
    account_mode: str = "live",
) -> dict[str, Any]:
    settings = request.app.state.trading_service.settings
    http_client = request.app.state.http_client
    mode = account_mode.lower().strip() if account_mode else "live"
    if mode not in ("live", "paper"):
        mode = "live"

    # ── Resolve credentials for the requested account ──────────
    try:
        creds = _resolve_creds(settings, mode)
    except Exception as exc:
        logger.warning("trading.creds_resolve_failed mode=%s exc=%s", mode, exc)
        return {
            "ok": False,
            "as_of": _utc_iso_now(),
            "source": "tradier",
            "account_mode": mode,
            "positions": [],
            "orders": [],
            "active_trades": [],
            "source_health": request.app.state.base_data_service.get_source_health_snapshot(),
            "error": {
                "message": f"Tradier credentials not configured for {mode.upper()}",
                "type": "ConfigurationError",
                "upstream_status": None,
                "upstream_body_snippet": None,
            },
        }

    if not creds.api_key or not creds.account_id:
        return {
            "ok": False,
            "as_of": _utc_iso_now(),
            "source": "tradier",
            "account_mode": mode,
            "positions": [],
            "orders": [],
            "active_trades": [],
            "source_health": request.app.state.base_data_service.get_source_health_snapshot(),
            "error": {
                "message": f"Tradier credentials not configured for {mode.upper()}",
                "type": "ConfigurationError",
                "upstream_status": None,
                "upstream_body_snippet": None,
            },
        }

    headers = {
        "Authorization": f"Bearer {creds.api_key}",
        "Accept": "application/json",
    }
    base = creds.base_url
    acct = creds.account_id

    # ── Per-request debug logging (import shared logger) ──────
    from app.trading.tradier_credentials import log_tradier_request

    # ── Fetch positions + orders from the resolved account ─────
    try:
        positions_url = f"{base}/accounts/{acct}/positions"
        orders_url = f"{base}/accounts/{acct}/orders"

        log_tradier_request(creds=creds, method="GET", path=f"/accounts/{acct}/positions")
        raw_positions_payload = await request_json(http_client, "GET", positions_url, headers=headers)
        log_tradier_request(creds=creds, method="GET", path=f"/accounts/{acct}/positions", status=200)

        log_tradier_request(creds=creds, method="GET", path=f"/accounts/{acct}/orders")
        raw_orders_payload = await request_json(
            http_client, "GET", orders_url, headers=headers, params={"status": "open"},
        )
        log_tradier_request(creds=creds, method="GET", path=f"/accounts/{acct}/orders", status=200)
    except Exception as exc:
        # Log the failed request with upstream status if available
        upstream_status = None
        body_snippet = None
        if isinstance(exc, UpstreamError) and exc.details:
            upstream_status = exc.details.get("status_code")
            body_snippet = str(exc.details.get("body") or "")[:500]
        log_tradier_request(
            creds=creds, method="GET", path=f"/accounts/{acct}/...",
            status=upstream_status, error=str(exc)[:200],
        )
        logger.warning(
            "[active-trades] Tradier fetch FAILED mode=%s status=%s body=%s",
            mode, upstream_status, body_snippet,
        )
        error = _error_payload_from_exception(exc, fallback_message="Failed to load active trades from Tradier")
        logger.exception("trading.active_fetch_failed mode=%s error=%s", mode, error)
        return {
            "ok": False,
            "as_of": _utc_iso_now(),
            "source": "tradier",
            "account_mode": mode,
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
            # Quotes always from LIVE (production market data)
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
            "account_mode": mode,
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
        "account_mode": mode,
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
async def get_active_trades(
    request: Request,
    account_mode: str = Query("live", pattern="^(live|paper)$"),
) -> dict[str, Any]:
    return await _build_active_payload(request, account_mode=account_mode)


@router.post("/active/refresh")
async def refresh_active_trades(
    request: Request,
    account_mode: str = Query("live", pattern="^(live|paper)$"),
) -> dict[str, Any]:
    return await _build_active_payload(request, account_mode=account_mode)


# ═══════════════════════════════════════════════════════════════
# Monitor — evaluate all active positions and return scores + triggers
# GET /api/trading/monitor?account_mode=live|paper
# ═══════════════════════════════════════════════════════════════

@router.get("/monitor")
async def get_monitor_results(
    request: Request,
    account_mode: str = Query("live", pattern="^(live|paper)$"),
) -> dict[str, Any]:
    """Evaluate all positions through the Active Trade Monitor.

    Returns a dict keyed by symbol with monitor_result for each position.
    Uses caching internally — safe to call on every refresh cycle.
    """
    monitor_service = getattr(request.app.state, "active_trade_monitor_service", None)
    if monitor_service is None:
        return {"ok": False, "error": {"message": "Monitor service not available"}}

    payload = await _build_active_payload(request, account_mode=account_mode)
    if not payload.get("ok"):
        return {"ok": False, "error": payload.get("error")}

    trades = payload.get("active_trades") or []
    results = await monitor_service.evaluate_batch(trades)

    # Index results by symbol for easy frontend lookup
    by_symbol: dict[str, Any] = {}
    for r in results:
        by_symbol[r.get("symbol", "???")] = r

    return {
        "ok": True,
        "as_of": payload.get("as_of"),
        "account_mode": payload.get("account_mode"),
        "monitor_results": by_symbol,
    }


# ═══════════════════════════════════════════════════════════════
# Monitor Narrative — LLM-powered analysis for a single position
# POST /api/trading/monitor/narrative
# ═══════════════════════════════════════════════════════════════

@router.post("/monitor/narrative")
async def get_monitor_narrative(
    request: Request,
) -> dict[str, Any]:
    """Generate a structured LLM memo for a single position's monitor result.

    Called on-demand when user clicks "Run Monitor Analysis".
    Returns a structured JSON memo with label, summary, thesis_check,
    key_risks, action, and confidence — never raw reasoning text.

    Request body:
      { "symbol": "AAPL", "position": {...}, "monitor_result": {...} }
    """
    body = await request.json()
    symbol = str(body.get("symbol") or "").upper()
    position = body.get("position") or {}
    monitor_result = body.get("monitor_result") or {}

    if not symbol:
        return {"ok": False, "error": {"message": "symbol is required"}}

    from common.model_sanitize import sanitize_model_text, had_think_tags, classify_model_error, user_facing_error_message

    # ── Build structured prompt ──────────────────────────────────
    status = monitor_result.get("status", "UNKNOWN")
    score = monitor_result.get("score_0_100", "?")
    breakdown = monitor_result.get("breakdown", {})
    triggers = monitor_result.get("triggers", [])
    hit_triggers = [t for t in triggers if t.get("hit")]
    action = monitor_result.get("recommended_action", {})

    avg_entry = position.get("avg_open_price") or "N/A"
    current = position.get("mark_price") or "N/A"
    pnl = position.get("unrealized_pnl") or "N/A"
    pnl_pct = position.get("unrealized_pnl_pct")
    pnl_pct_str = f"{pnl_pct:.1%}" if pnl_pct is not None else "N/A"
    qty = position.get("quantity") or "N/A"

    system_msg = (
        "You are a senior portfolio analyst for BenTrade.\n"
        "Review the supplied position-monitor data and return ONLY the final "
        "assessment as a single valid JSON object.\n\n"
        "RULES — follow these exactly:\n"
        "1. Do NOT include chain-of-thought, reasoning traces, or <think> tags.\n"
        "2. Do NOT include markdown fences, explanation, or any text outside the JSON.\n"
        "3. Do NOT repeat the raw input data verbatim.\n"
        "4. Return ONLY the JSON object — nothing before or after it.\n"
        "5. Keep it concise, specific, and trader-usable.\n"
        "6. Reference actual price levels, P&L numbers, and indicator values.\n\n"
        "Required JSON schema:\n"
        "{\n"
        '  "label": "HOLD | WATCH | REDUCE | EXIT | ADD",\n'
        '  "summary": "2-3 sentence executive memo",\n'
        '  "thesis_check": "1-2 sentence assessment of whether original thesis is intact",\n'
        '  "key_risks": ["risk 1", "risk 2", "risk 3"],\n'
        '  "action": "clear action recommendation",\n'
        '  "confidence": 0-100\n'
        "}\n\n"
        "label must be exactly one of: HOLD, WATCH, REDUCE, EXIT, ADD\n"
        "confidence must be an integer 0-100\n"
        "key_risks must have 1-5 items"
    )

    user_msg = (
        f"POSITION SNAPSHOT:\n"
        f"  Symbol: {symbol}\n"
        f"  Quantity: {qty}\n"
        f"  Avg Entry: ${avg_entry}\n"
        f"  Current Price: ${current}\n"
        f"  Unrealized P&L: ${pnl} ({pnl_pct_str})\n\n"
        f"MONITOR ASSESSMENT:\n"
        f"  Status: {status}\n"
        f"  Score: {score}/100\n"
        f"  Breakdown:\n"
        f"{_fmt_breakdown(breakdown)}\n\n"
        f"ACTIVE TRIGGERS:\n"
        f"{_fmt_triggers(hit_triggers) if hit_triggers else '  None'}\n\n"
        f"RECOMMENDED ACTION: {action.get('action', 'N/A')} — {action.get('reason_short', '')}\n\n"
        f"Return your assessment as the required JSON object only."
    )

    logger.info(
        "[monitor-narrative] request symbol=%s status=%s score=%s",
        symbol, status, score,
    )

    # ── LLM call (routed with legacy fallback) ──────────────────
    try:
        import asyncio as _asyncio
        from app.services.model_routing_integration import execute_routed_model, RoutingDisabledError

        raw_content = None

        # Routed execution — participates in gate + provider rotation
        try:
            _loop = _asyncio.get_running_loop()
            _routed_result, _routed_trace = await _loop.run_in_executor(
                None,
                lambda: execute_routed_model(
                    task_type="monitor_narrative",
                    messages=[{"role": "user", "content": user_msg}],
                    system_prompt=system_msg,
                    timeout=90.0,
                    max_tokens=600,
                    temperature=0.2,
                    metadata={"symbol": symbol},
                ),
            )
            if _routed_result["status"] == "success":
                raw_content = _routed_result.get("content", "")
                logger.info(
                    "[monitor-narrative] routed OK provider=%s symbol=%s",
                    _routed_trace.selected_provider, symbol,
                )
            else:
                logger.warning(
                    "[monitor-narrative] routed call failed: %s — trying legacy symbol=%s",
                    _routed_result.get("error"), symbol,
                )
        except RoutingDisabledError:
            logger.info("[monitor-narrative] routing disabled — legacy fallback symbol=%s", symbol)
        except Exception as _routing_exc:
            logger.warning("[monitor-narrative] routing error (%s) — legacy fallback symbol=%s", _routing_exc, symbol)

        # Legacy fallback if routed execution didn't produce content
        if raw_content is None:
            from app.services.model_router import get_model_endpoint
            llm_response = await request.app.state.http_client.post(
                get_model_endpoint(),
                json={
                    "model": "local-model",
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 600,
                    "stream": False,
                },
                timeout=90.0,
            )
            if llm_response.status_code != 200:
                msg = f"LLM returned HTTP {llm_response.status_code}"
                logger.warning("[monitor-narrative] %s symbol=%s", msg, symbol)
                return {"ok": False, "symbol": symbol, "error": {"message": msg}}
            data = llm_response.json()
            raw_content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")

        logger.info(
            "[monitor-narrative] response len=%d think_detected=%s symbol=%s",
            len(raw_content), had_think_tags(raw_content), symbol,
        )

        # ── Sanitize + parse ──────────────────────────────────────
        cleaned = sanitize_model_text(raw_content)

        if not cleaned:
            logger.warning("[monitor-narrative] empty after sanitization symbol=%s", symbol)
            return {
                "ok": True,
                "symbol": symbol,
                "narrative": None,
                "structured": _build_fallback_narrative(status, score),
                "monitor_status": status,
                "monitor_score": score,
                "error": {"message": "Model returned empty content after sanitization."},
            }

        # Try JSON parse
        from common.json_repair import extract_and_repair_json
        parsed, method = extract_and_repair_json(cleaned)

        logger.info(
            "[monitor-narrative] parse method=%s success=%s symbol=%s",
            method, parsed is not None, symbol,
        )

        structured = _coerce_narrative_memo(parsed) if parsed else None

        if structured:
            return {
                "ok": True,
                "symbol": symbol,
                "narrative": structured.get("summary", ""),
                "structured": structured,
                "monitor_status": status,
                "monitor_score": score,
            }

        # Fallback: treat sanitized text as plain narrative
        logger.warning(
            "[monitor-narrative] JSON parse failed, using plain text fallback symbol=%s first200=%s",
            symbol, cleaned[:200],
        )
        return {
            "ok": True,
            "symbol": symbol,
            "narrative": cleaned[:1000],
            "structured": None,
            "monitor_status": status,
            "monitor_score": score,
        }

    except Exception as exc:
        error_kind = classify_model_error(exc)
        msg = user_facing_error_message(error_kind)
        logger.error(
            "[monitor-narrative] %s symbol=%s error_kind=%s exc=%s",
            msg, symbol, error_kind, exc,
        )
        return {"ok": False, "symbol": symbol, "narrative": "",
                "error": {"message": msg, "kind": error_kind}}


def _fmt_breakdown(bd: dict) -> str:
    lines = []
    for k, v in bd.items():
        lines.append(f"    {k}: {v}")
    return "\n".join(lines) if lines else "    (none)"


def _fmt_triggers(triggers: list) -> str:
    lines = []
    for t in triggers:
        lines.append(f"  [{t.get('level','?')}] {t.get('message','')}")
    return "\n".join(lines) if lines else "  None"


def _coerce_narrative_memo(parsed: Any) -> dict[str, Any] | None:
    """Normalize LLM response into the monitor narrative memo contract."""
    if not isinstance(parsed, dict):
        return None

    label = str(parsed.get("label") or "WATCH").strip().upper()
    if label not in {"HOLD", "WATCH", "REDUCE", "EXIT", "ADD"}:
        label = "WATCH"

    summary = str(parsed.get("summary") or "").strip()
    thesis_check = str(parsed.get("thesis_check") or "").strip()
    action = str(parsed.get("action") or "").strip()

    risks_raw = parsed.get("key_risks") or []
    if isinstance(risks_raw, list):
        key_risks = [str(r).strip() for r in risks_raw if str(r).strip()][:5]
    else:
        key_risks = [str(risks_raw).strip()] if str(risks_raw).strip() else []

    conf = parsed.get("confidence")
    try:
        confidence = max(0, min(100, int(float(conf))))
    except (TypeError, ValueError):
        confidence = 50

    if not summary:
        return None

    return {
        "label": label,
        "summary": summary,
        "thesis_check": thesis_check,
        "key_risks": key_risks,
        "action": action,
        "confidence": confidence,
    }


def _build_fallback_narrative(status: str, score: Any) -> dict[str, Any]:
    """Build a minimal fallback memo when the model returns empty/unparseable."""
    return {
        "label": "WATCH",
        "summary": f"Monitor status is {status} with score {score}/100. Model analysis unavailable.",
        "thesis_check": "Unable to assess — model did not return a valid response.",
        "key_risks": ["Model analysis unavailable"],
        "action": "Review position manually.",
        "confidence": 0,
    }


# ═══════════════════════════════════════════════════════════════
# Active Trade Model Analysis — purpose-built LLM analysis
# POST /api/trading/active/model-analysis
#
# Sends ONLY raw position + raw market context to the LLM.
# Does NOT include any monitor scores, triggers, or recommended actions.
# The model must form its own independent opinion.
# ═══════════════════════════════════════════════════════════════

# JSON schema instruction block for the model analysis prompt.
# Defined as a module constant so the system message stays readable.
_MODEL_ANALYSIS_SCHEMA = """{
  "headline": "<short attention-grabbing headline>",
  "stance": "HOLD|REDUCE|EXIT|ADD|WATCH",
  "confidence": 0-100,
  "thesis_status": "INTACT|WEAKENING|BROKEN",
  "summary": "<2-3 sentence executive summary>",
  "key_risks": ["<risk 1>", "<risk 2>"],
  "key_supports": ["<support 1>", "<support 2>"],
  "technical_state": {
    "price_vs_sma20": "ABOVE|BELOW|NEAR",
    "price_vs_sma50": "ABOVE|BELOW|NEAR",
    "trend_assessment": "<1 sentence>",
    "drawdown_assessment": "<1 sentence>"
  },
  "action_plan": {
    "primary_action": "<what to do>",
    "urgency": "LOW|MEDIUM|HIGH",
    "next_step": "<concrete next step>",
    "risk_trigger": "<what would force an exit>",
    "upside_trigger": "<what would justify adding>"
  },
  "memo": {
    "thesis_check": "<is original thesis intact?>",
    "what_changed": "<what moved since entry?>",
    "decision": "<clear recommendation>"
  }
}"""

_MODEL_ANALYSIS_SYSTEM_MSG = (
    "You are a senior portfolio and risk analyst writing a structured "
    "active-position review for a trading desk UI.\n\n"
    "RULES — follow these exactly:\n"
    "1. Use ONLY the provided position and market context data.\n"
    "2. Do NOT invent catalysts, fundamentals, or news.\n"
    "3. Do NOT output chain-of-thought, reasoning tags, <think> tags, "
    "markdown fences, or any text outside the JSON.\n"
    "4. Return a single valid JSON object using the schema below.\n"
    "5. Be concise, specific, and decision-oriented. "
    "Reference actual price levels, P&L numbers, and indicator values.\n"
    "6. Avoid filler, generic advice, and educational explanations.\n"
    "7. Keep risk/action language specific to the provided data.\n"
    "8. confidence is an integer 0-100, NOT a float.\n"
    "9. stance must be exactly one of: HOLD, REDUCE, EXIT, ADD, WATCH\n"
    "10. thesis_status must be exactly: INTACT, WEAKENING, or BROKEN\n"
    "11. urgency must be exactly: LOW, MEDIUM, or HIGH\n\n"
    "Required JSON schema:\n" + _MODEL_ANALYSIS_SCHEMA
)


def _sanitize_model_analysis(raw: dict) -> dict:
    """Validate and coerce a parsed model analysis dict into the required schema.

    Input:  raw dict from LLM JSON parse
    Output: cleaned dict conforming to the position review schema
    """
    # ── stance ──
    valid_stances = {"HOLD", "REDUCE", "EXIT", "ADD", "WATCH"}
    stance = str(raw.get("stance") or raw.get("suggested_action") or "HOLD").upper()
    # Map old schema values to new
    if stance == "CLOSE":
        stance = "EXIT"
    if stance not in valid_stances:
        stance = "HOLD"

    # ── confidence (0-100 int) ──
    conf = raw.get("confidence")
    try:
        conf = int(float(conf))
        if conf < 0:
            conf = 0
        elif conf > 100:
            conf = 100
    except (TypeError, ValueError):
        conf = 0

    # ── thesis_status ──
    valid_thesis = {"INTACT", "WEAKENING", "BROKEN"}
    thesis = str(raw.get("thesis_status") or "INTACT").upper()
    if thesis not in valid_thesis:
        thesis = "INTACT"

    # ── string fields ──
    headline = str(raw.get("headline") or raw.get("one_sentence_summary") or "")
    summary = str(raw.get("summary") or raw.get("one_sentence_summary") or "")

    # ── array fields ──
    key_risks = raw.get("key_risks") or raw.get("risk_flags") or []
    if not isinstance(key_risks, list):
        key_risks = []
    key_risks = [str(r) for r in key_risks if r]

    key_supports = raw.get("key_supports") or []
    if not isinstance(key_supports, list):
        key_supports = []
    key_supports = [str(s) for s in key_supports if s]

    # ── technical_state ──
    ts_raw = raw.get("technical_state") or {}
    if not isinstance(ts_raw, dict):
        ts_raw = {}
    valid_price_pos = {"ABOVE", "BELOW", "NEAR"}

    def _price_pos(val: Any) -> str:
        v = str(val or "").upper()
        return v if v in valid_price_pos else "NEAR"

    technical_state = {
        "price_vs_sma20": _price_pos(ts_raw.get("price_vs_sma20")),
        "price_vs_sma50": _price_pos(ts_raw.get("price_vs_sma50")),
        "trend_assessment": str(ts_raw.get("trend_assessment") or ""),
        "drawdown_assessment": str(ts_raw.get("drawdown_assessment") or ""),
    }

    # ── action_plan ──
    ap_raw = raw.get("action_plan") or {}
    if not isinstance(ap_raw, dict):
        ap_raw = {}
    valid_urgency = {"LOW", "MEDIUM", "HIGH"}
    urgency = str(ap_raw.get("urgency") or "LOW").upper()
    if urgency not in valid_urgency:
        urgency = "LOW"

    action_plan = {
        "primary_action": str(ap_raw.get("primary_action") or ""),
        "urgency": urgency,
        "next_step": str(ap_raw.get("next_step") or raw.get("next_check") or ""),
        "risk_trigger": str(ap_raw.get("risk_trigger") or ""),
        "upside_trigger": str(ap_raw.get("upside_trigger") or ""),
    }

    # ── memo ──
    memo_raw = raw.get("memo") or {}
    if not isinstance(memo_raw, dict):
        memo_raw = {}
    memo = {
        "thesis_check": str(memo_raw.get("thesis_check") or ""),
        "what_changed": str(memo_raw.get("what_changed") or ""),
        "decision": str(memo_raw.get("decision") or ""),
    }

    return {
        "headline": headline,
        "stance": stance,
        "confidence": conf,
        "thesis_status": thesis,
        "summary": summary,
        "key_risks": key_risks,
        "key_supports": key_supports,
        "technical_state": technical_state,
        "action_plan": action_plan,
        "memo": memo,
    }


def _build_fallback_analysis(reason: str) -> dict:
    """Return a safe fallback analysis dict when parsing fails completely."""
    return {
        "headline": "Analysis unavailable",
        "stance": "WATCH",
        "confidence": 0,
        "thesis_status": "INTACT",
        "summary": reason,
        "key_risks": ["Model response could not be parsed"],
        "key_supports": [],
        "technical_state": {
            "price_vs_sma20": "NEAR",
            "price_vs_sma50": "NEAR",
            "trend_assessment": "",
            "drawdown_assessment": "",
        },
        "action_plan": {
            "primary_action": "Retry analysis",
            "urgency": "LOW",
            "next_step": "Re-run model analysis",
            "risk_trigger": "",
            "upside_trigger": "",
        },
        "memo": {
            "thesis_check": "",
            "what_changed": "",
            "decision": "Retry analysis for actionable recommendation.",
        },
    }


@router.post("/active/model-analysis")
async def active_trade_model_analysis(
    request: Request,
) -> dict[str, Any]:
    """Run purpose-built LLM analysis on an active trade position.

    The prompt sends ONLY raw data:
      - Position snapshot (symbol, qty, direction, entry, cost, market value, P&L)
      - Raw market context (regime label/score, SMA20, SMA50, RSI14)

    NO monitor scores, triggers, or recommendations are included.
    The LLM must form its own independent opinion.

    Returns a structured position-review memo (see _MODEL_ANALYSIS_SCHEMA).
    Chain-of-thought / ``<think>`` blocks are stripped server-side.
    """
    body = await request.json()
    symbol = str(body.get("symbol") or "").upper()
    position = body.get("position") or {}

    if not symbol:
        return {"ok": False, "error": {"message": "symbol is required"}}

    # ── Fetch raw market context (regime + indicators) ───────
    monitor_svc = getattr(request.app.state, "active_trade_monitor_service", None)
    regime_ctx = {"regime_label": None, "regime_score": None}
    indicators = {"sma20": None, "sma50": None, "rsi14": None}

    if monitor_svc:
        try:
            regime_ctx = await monitor_svc._fetch_regime()
        except Exception as exc:
            logger.warning("[model-analysis] regime fetch failed: %s", exc)
        try:
            indicators = await monitor_svc._fetch_indicators(symbol)
        except Exception as exc:
            logger.warning("[model-analysis] indicator fetch failed symbol=%s: %s", symbol, exc)

    # ── Extract position fields ──────────────────────────────
    qty = position.get("quantity", "N/A")
    direction = "Short" if (isinstance(qty, (int, float)) and qty < 0) else "Long"
    avg_entry = position.get("avg_open_price") or "N/A"
    current = position.get("mark_price") or "N/A"
    cost_basis = position.get("cost_basis_total") or "N/A"
    market_value = position.get("market_value") or "N/A"
    pnl = position.get("unrealized_pnl") or "N/A"
    pnl_pct = position.get("unrealized_pnl_pct")
    pnl_pct_str = f"{pnl_pct:.1%}" if isinstance(pnl_pct, (int, float)) else "N/A"
    strategy = position.get("strategy") or "single"
    day_change = position.get("day_change")
    day_change_str = f"${day_change:+.2f}" if isinstance(day_change, (int, float)) else "N/A"

    # ── Format indicator values ──────────────────────────────
    sma20_str = f"${indicators['sma20']:.2f}" if indicators.get("sma20") is not None else "N/A"
    sma50_str = f"${indicators['sma50']:.2f}" if indicators.get("sma50") is not None else "N/A"
    rsi14_str = f"{indicators['rsi14']:.1f}" if indicators.get("rsi14") is not None else "N/A"
    regime_label = regime_ctx.get("regime_label") or "UNKNOWN"
    regime_score = regime_ctx.get("regime_score")
    regime_score_str = f"{regime_score:.0f}/100" if isinstance(regime_score, (int, float)) else "N/A"

    # ── Compute derived price-vs-SMA comparisons for prompt context ──
    _cur = None
    try:
        _cur = float(current) if current != "N/A" else None
    except (TypeError, ValueError):
        pass

    def _price_rel(price, sma_val):
        if price is None or sma_val is None:
            return "N/A"
        pct = (price - sma_val) / sma_val * 100
        if pct > 1.5:
            return f"ABOVE (+{pct:.1f}%)"
        if pct < -1.5:
            return f"BELOW ({pct:.1f}%)"
        return f"NEAR ({pct:+.1f}%)"

    sma20_rel = _price_rel(_cur, indicators.get("sma20"))
    sma50_rel = _price_rel(_cur, indicators.get("sma50"))

    # ── Build user prompt with raw data only ─────────────────
    user_msg = f"""Evaluate this active position using ONLY the data below.

POSITION SNAPSHOT
  Symbol: {symbol}
  Strategy: {strategy}
  Direction: {direction}
  Quantity: {qty}
  Avg Entry Price: ${avg_entry}
  Current Price: ${current}
  Cost Basis (total): ${cost_basis}
  Market Value: ${market_value}
  Unrealized P&L: ${pnl} ({pnl_pct_str})
  Day Change: {day_change_str}

MARKET CONTEXT
  Regime: {regime_label} (score: {regime_score_str})
  SMA 20-day: {sma20_str}  — Price vs SMA20: {sma20_rel}
  SMA 50-day: {sma50_str}  — Price vs SMA50: {sma50_rel}
  RSI 14-day: {rsi14_str}

Produce a concise position review memo as JSON. Be decisive."""

    llm_payload = {
        "model": "local-model",
        "messages": [
            {"role": "system", "content": _MODEL_ANALYSIS_SYSTEM_MSG},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
        "max_tokens": 900,
    }

    # ── LLM call with retry-once on JSON parse failure ───────
    MAX_ATTEMPTS = 2
    analysis = None

    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            import asyncio as _asyncio
            from app.services.model_routing_integration import execute_routed_model, RoutingDisabledError

            raw_content = None

            # Routed execution — participates in gate + provider rotation
            try:
                _loop = _asyncio.get_running_loop()
                _routed_result, _routed_trace = await _loop.run_in_executor(
                    None,
                    lambda attempt=attempt: execute_routed_model(
                        task_type="active_trade_model_analysis",
                        messages=[{"role": "user", "content": user_msg}],
                        system_prompt=_MODEL_ANALYSIS_SYSTEM_MSG,
                        timeout=90.0,
                        max_tokens=900,
                        temperature=0.2,
                        metadata={"symbol": symbol, "attempt": attempt},
                    ),
                )
                if _routed_result["status"] == "success":
                    raw_content = _routed_result.get("content", "")
                    logger.info(
                        "[model-analysis] routed OK provider=%s (attempt %d) symbol=%s",
                        _routed_trace.selected_provider, attempt, symbol,
                    )
                else:
                    logger.warning(
                        "[model-analysis] routed call failed: %s — trying legacy (attempt %d) symbol=%s",
                        _routed_result.get("error"), attempt, symbol,
                    )
            except RoutingDisabledError:
                logger.info("[model-analysis] routing disabled — legacy fallback (attempt %d) symbol=%s", attempt, symbol)
            except Exception as _routing_exc:
                logger.warning("[model-analysis] routing error (%s) — legacy fallback (attempt %d) symbol=%s", _routing_exc, attempt, symbol)

            # Legacy fallback if routed execution didn't produce content
            if raw_content is None:
                from app.services.model_router import get_model_endpoint
                llm_response = await request.app.state.http_client.post(
                    get_model_endpoint(),
                    json={**llm_payload, "stream": False},
                    timeout=90.0,
                )
                if llm_response.status_code != 200:
                    msg = f"LLM returned HTTP {llm_response.status_code}"
                    logger.warning("[model-analysis] %s (attempt %d)", msg, attempt)
                    if attempt < MAX_ATTEMPTS:
                        continue
                    return {"ok": False, "symbol": symbol, "error": {"message": msg}}
                data = llm_response.json()
                raw_content = (
                    (data.get("choices") or [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )

            # Log raw content for debugging (never exposed to UI)
            logger.debug(
                "[model-analysis] raw LLM output (attempt %d) symbol=%s: %s",
                attempt, symbol, raw_content[:500],
            )

            # ── Sanitize: strip <think> blocks and reasoning traces ──
            from common.model_sanitize import sanitize_model_text, had_think_tags
            if had_think_tags(raw_content):
                logger.info("[model-analysis] <think> content detected and stripped (attempt %d) symbol=%s", attempt, symbol)
            cleaned = sanitize_model_text(raw_content)

            # ── Parse via json_repair pipeline ──
            parsed, method = extract_and_repair_json(cleaned)

            if parsed is not None and isinstance(parsed, dict):
                logger.info(
                    "[model-analysis] JSON parsed via %s (attempt %d) symbol=%s",
                    method, attempt, symbol,
                )
                analysis = _sanitize_model_analysis(parsed)
                break

            logger.warning(
                "[model-analysis] JSON parse failed (attempt %d) symbol=%s first200=%s",
                attempt, symbol, cleaned[:200],
            )

        if analysis is None:
            analysis = _build_fallback_analysis("Model response could not be parsed.")

    except Exception as exc:
        from common.model_sanitize import classify_model_error, user_facing_error_message
        error_kind = classify_model_error(exc)
        msg = user_facing_error_message(error_kind)
        logger.error(
            "[model-analysis] %s symbol=%s error_kind=%s exc=%s",
            msg, symbol, error_kind, exc,
        )
        return {
            "ok": False,
            "symbol": symbol,
            "error": {"message": msg, "kind": error_kind},
        }

    # ── Inject position_context from raw data (not from model) ──
    # Input fields: avg_entry, current, pnl, pnl_pct — from the position payload
    # Formula: these are pass-through values, not model-generated
    position_context = {}
    try:
        position_context["entry_price"] = float(avg_entry) if avg_entry != "N/A" else None
    except (TypeError, ValueError):
        position_context["entry_price"] = None
    try:
        position_context["current_price"] = float(current) if current != "N/A" else None
    except (TypeError, ValueError):
        position_context["current_price"] = None
    try:
        position_context["pnl_dollar"] = float(pnl) if pnl != "N/A" else None
    except (TypeError, ValueError):
        position_context["pnl_dollar"] = None
    try:
        position_context["pnl_percent"] = round(float(pnl_pct) * 100, 2) if isinstance(pnl_pct, (int, float)) else None
    except (TypeError, ValueError):
        position_context["pnl_percent"] = None
    position_context["score"] = None  # populated by monitor, not model

    analysis["position_context"] = position_context

    return {
        "ok": True,
        "symbol": symbol,
        "analysis": analysis,
        "context_used": {
            "regime": regime_label,
            "regime_score": regime_score,
            "sma20": indicators.get("sma20"),
            "sma50": indicators.get("sma50"),
            "rsi14": indicators.get("rsi14"),
        },
    }
# POST /api/trading/close-position
# ═══════════════════════════════════════════════════════════════

@router.post("/close-position")
async def close_position(
    request: Request,
) -> dict[str, Any]:
    """Close an equity position by submitting a market order in the opposite direction.

    Request body:
      { "symbol": "WMT", "quantity": 10, "side": "buy"|"sell", "account_mode": "live"|"paper" }

    The side should be the CURRENT position side — the endpoint will submit
    the opposite (sell to close a long, buy to close a short).
    """
    from app.trading.tradier_credentials import get_tradier_context, log_tradier_request

    body = await request.json()
    symbol = str(body.get("symbol") or "").upper()
    qty = abs(int(body.get("quantity") or 0))
    current_side = str(body.get("side") or "buy").lower()
    mode = str(body.get("account_mode") or "paper").lower()

    if not symbol or qty <= 0:
        return {"ok": False, "error": {"message": "symbol and quantity are required"}}
    if mode not in ("live", "paper"):
        mode = "paper"

    # Opposite side to close
    close_side = "sell" if current_side == "buy" else "buy"

    settings = request.app.state.trading_service.settings
    http_client = request.app.state.http_client

    try:
        creds = get_tradier_context(settings, account_type=mode)
    except Exception as exc:
        return {"ok": False, "error": {"message": f"Credential error: {exc}"}}

    headers = {
        "Authorization": f"Bearer {creds.api_key}",
        "Accept": "application/json",
    }
    order_url = f"{creds.base_url}/accounts/{creds.account_id}/orders"
    order_data = {
        "class": "equity",
        "symbol": symbol,
        "side": close_side,
        "quantity": str(qty),
        "type": "market",
        "duration": "day",
    }

    log_tradier_request(creds=creds, method="POST", path=f"/accounts/{creds.account_id}/orders")
    logger.info("[close-position] mode=%s symbol=%s qty=%d close_side=%s", mode, symbol, qty, close_side)

    try:
        response = await http_client.request(
            "POST", order_url, headers=headers, data=order_data,
        )
        status_code = response.status_code
        try:
            resp_body = response.json()
        except Exception:
            resp_body = response.text[:500]

        log_tradier_request(
            creds=creds, method="POST",
            path=f"/accounts/{creds.account_id}/orders",
            status=status_code,
        )

        if status_code not in (200, 201):
            logger.warning("[close-position] FAILED status=%d body=%s", status_code, resp_body)
            return {
                "ok": False,
                "error": {
                    "message": f"Tradier returned HTTP {status_code}",
                    "upstream_status": status_code,
                    "upstream_body": resp_body,
                },
            }

        order_info = resp_body.get("order", resp_body) if isinstance(resp_body, dict) else resp_body
        logger.info("[close-position] SUCCESS mode=%s symbol=%s order=%s", mode, symbol, order_info)

        return {
            "ok": True,
            "symbol": symbol,
            "quantity": qty,
            "close_side": close_side,
            "account_mode": mode,
            "order": order_info,
        }

    except Exception as exc:
        logger.exception("[close-position] NETWORK_ERROR symbol=%s exc=%s", symbol, exc)
        return {"ok": False, "error": {"message": f"Network error: {exc}"}}


@router.get("/positions")
async def get_tradier_positions(
    request: Request,
    account_mode: str = Query("live", pattern="^(live|paper)$"),
) -> dict[str, Any]:
    payload = await _build_active_payload(request, account_mode=account_mode)
    return {
        "ok": bool(payload.get("ok", False)),
        "as_of": payload.get("as_of"),
        "source": payload.get("source", "tradier"),
        "account_mode": payload.get("account_mode", account_mode),
        "positions": payload.get("positions") or [],
        "source_health": payload.get("source_health") or {},
        "error": payload.get("error") if not payload.get("ok", False) else None,
    }


@router.get("/orders/open")
async def get_tradier_open_orders(
    request: Request,
    account_mode: str = Query("live", pattern="^(live|paper)$"),
) -> dict[str, Any]:
    payload = await _build_active_payload(request, account_mode=account_mode)
    return {
        "ok": bool(payload.get("ok", False)),
        "as_of": payload.get("as_of"),
        "source": payload.get("source", "tradier"),
        "account_mode": payload.get("account_mode", account_mode),
        "orders": payload.get("orders") or [],
        "source_health": payload.get("source_health") or {},
        "error": payload.get("error") if not payload.get("ok", False) else None,
    }


@router.get("/account")
async def get_tradier_account(
    request: Request,
    account_mode: str = Query("live", pattern="^(live|paper)$"),
) -> dict[str, Any]:
    settings = request.app.state.trading_service.settings
    http_client = request.app.state.http_client
    mode = account_mode.lower().strip() if account_mode else "live"
    if mode not in ("live", "paper"):
        mode = "live"

    try:
        creds = _resolve_creds(settings, mode)
    except Exception:
        return {
            "ok": False,
            "as_of": _utc_iso_now(),
            "source": "tradier",
            "account_mode": mode,
            "account": {},
            "error": {
                "message": f"Tradier credentials not configured for {mode.upper()}",
                "type": "ConfigurationError",
                "upstream_status": None,
                "upstream_body_snippet": None,
            },
        }

    if not creds.api_key or not creds.account_id:
        return {
            "ok": False,
            "as_of": _utc_iso_now(),
            "source": "tradier",
            "account_mode": mode,
            "account": {},
            "error": {
                "message": f"Tradier credentials not configured for {mode.upper()}",
                "type": "ConfigurationError",
                "upstream_status": None,
                "upstream_body_snippet": None,
            },
        }

    headers = {
        "Authorization": f"Bearer {creds.api_key}",
        "Accept": "application/json",
    }

    from app.trading.tradier_credentials import log_tradier_request

    try:
        acct_path = f"/accounts/{creds.account_id}/balances"
        url = f"{creds.base_url}{acct_path}"
        log_tradier_request(creds=creds, method="GET", path=acct_path)
        account_payload = await request_json(http_client, "GET", url, headers=headers)
        log_tradier_request(creds=creds, method="GET", path=acct_path, status=200)
    except Exception as exc:
        error = _error_payload_from_exception(exc, fallback_message="Failed to load account balances from Tradier")
        logger.exception("trading.account_fetch_failed mode=%s error=%s", mode, error)
        return {
            "ok": False,
            "as_of": _utc_iso_now(),
            "source": "tradier",
            "account_mode": mode,
            "account": {},
            "source_health": request.app.state.base_data_service.get_source_health_snapshot(),
            "error": error,
        }

    return {
        "ok": True,
        "as_of": _utc_iso_now(),
        "source": "tradier",
        "account_mode": mode,
        "account": _json_safe(account_payload),
        "source_health": request.app.state.base_data_service.get_source_health_snapshot(),
        "error": None,
    }


# ═══════════════════════════════════════════════════════════════
# Debug Probe — raw Tradier call with full diagnostics
# GET /api/trading/debug/positions?accountType=live|paper
# ═══════════════════════════════════════════════════════════════

@router.get("/debug/positions")
async def tradier_debug_positions(
    request: Request,
    accountType: str = Query("paper", pattern="^(live|paper)$"),
) -> dict[str, Any]:
    """DEV-ONLY debug probe.  Makes a raw Tradier positions call and returns
    complete diagnostics so we can see exactly what URL / token / account ID
    is used and what Tradier responds.

    This uses the SAME get_tradier_context() resolver that _build_active_payload
    uses, so proving this works proves the production path works.
    """
    import hashlib
    from app.trading.tradier_credentials import get_tradier_context

    settings = request.app.state.trading_service.settings
    http_client = request.app.state.http_client
    mode = accountType.lower().strip()

    # ── Step 1: Resolve credentials ───────────────────────────
    try:
        creds = get_tradier_context(settings, account_type=mode)
    except ValueError as exc:
        result = {
            "ok": False,
            "accountType": mode,
            "baseUrl": None,
            "accountId": None,
            "tokenPresent": False,
            "tokenPrefix": None,
            "tokenHash": None,
            "status": None,
            "requestUrl": None,
            "authHeaderPresent": False,
            "authHeaderFormat": None,
            "tradierBody": None,
            "error": {"message": str(exc), "details": "Credential resolution failed"},
        }
        logger.error("[tradier-probe] CREDENTIAL_RESOLUTION_FAILED type=%s error=%s", mode.upper(), exc)
        return result

    token_prefix = creds.api_key[:6] if len(creds.api_key) >= 6 else "(empty)"
    token_hash = hashlib.sha256(creds.api_key.encode()).hexdigest()[:12] if creds.api_key else "(empty)"

    # ── Step 2: Build request exactly as _build_active_payload does ──
    auth_header = f"Bearer {creds.api_key}"
    headers = {
        "Authorization": auth_header,
        "Accept": "application/json",
    }
    request_url = f"{creds.base_url}/accounts/{creds.account_id}/positions"

    logger.info(
        "[tradier-probe] type=%s base=%s acct=%s tokenPresent=%s tokenPrefix=%s "
        "url=%s authHeader=true",
        mode.upper(), creds.base_url, creds.account_id,
        bool(creds.api_key), token_prefix, request_url,
    )

    # ── Step 3: Make the raw HTTP call ────────────────────────
    try:
        response = await http_client.request("GET", request_url, headers=headers)
        status_code = response.status_code
        body_text = response.text
    except Exception as exc:
        logger.error(
            "[tradier-probe] type=%s base=%s acct=%s tokenPresent=%s tokenPrefix=%s "
            "NETWORK_ERROR=%s",
            mode.upper(), creds.base_url, creds.account_id,
            bool(creds.api_key), token_prefix, str(exc)[:200],
        )
        return {
            "ok": False,
            "accountType": mode,
            "baseUrl": creds.base_url,
            "accountId": creds.account_id,
            "tokenPresent": bool(creds.api_key),
            "tokenPrefix": token_prefix,
            "tokenHash": token_hash,
            "status": None,
            "requestUrl": request_url,
            "authHeaderPresent": True,
            "authHeaderFormat": f"Bearer {token_prefix}...",
            "tradierBody": None,
            "error": {"message": f"Network error: {exc}", "details": str(exc)[:500]},
        }

    logger.info(
        "[tradier-probe] type=%s base=%s acct=%s tokenPresent=%s tokenPrefix=%s "
        "status=%d bodyLen=%d",
        mode.upper(), creds.base_url, creds.account_id,
        bool(creds.api_key), token_prefix, status_code, len(body_text),
    )

    # ── Step 4: Parse response ────────────────────────────────
    tradier_body = None
    try:
        tradier_body = response.json()
    except Exception:
        tradier_body = body_text[:500]

    return {
        "ok": status_code == 200,
        "accountType": mode,
        "baseUrl": creds.base_url,
        "accountId": creds.account_id,
        "tokenPresent": bool(creds.api_key),
        "tokenPrefix": token_prefix,
        "tokenHash": token_hash,
        "status": status_code,
        "requestUrl": request_url,
        "authHeaderPresent": True,
        "authHeaderFormat": f"Bearer {token_prefix}...",
        "tradierBody": tradier_body if status_code == 200 else body_text[:500],
        "error": None if status_code == 200 else {
            "message": f"Tradier returned HTTP {status_code}",
            "details": body_text[:500],
        },
    }
