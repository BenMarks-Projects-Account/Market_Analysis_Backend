"""
Stock Execution Service
=======================

Orchestrates equity (stock_long) order execution via Tradier.

Flow:
  1. Validate StockExecutionRequest (schema + business rules)
  2. Check idempotency — return cached response if same client_request_id
  3. Resolve Tradier credentials (paper vs live)
  4. Optionally fetch last price for audit / guard
  5. Build Tradier equity order payload
  6. Submit to Tradier (or paper-simulate)
  7. Normalise response → StockExecutionResponse
  8. Persist execution record in trading repository
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.storage.repository import InMemoryTradingRepository
from app.trading.stock_models import (
    STOCK_MAX_QTY_DEFAULT,
    StockExecutionRequest,
    StockExecutionResponse,
)
from app.trading.tradier_credentials import (
    TradierCredentials,
    log_execution_context,
    resolve_tradier_credentials,
)
from app.utils.http import UpstreamError, request_json

logger = logging.getLogger("bentrade.stock_execution")

# ── Config knobs (env-overrideable) ───────────────────────────────
STOCK_MAX_QTY = int(os.getenv("STOCK_MAX_QTY", str(STOCK_MAX_QTY_DEFAULT)))


class StockExecutionService:
    """Self-contained equity execution service.

    Reuses the same credential resolver and safety gates as the options
    TradingService so paper / live mode behaves identically.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        http_client: httpx.AsyncClient,
        repository: InMemoryTradingRepository,
    ) -> None:
        self.settings = settings
        self.http_client = http_client
        self.repository = repository

    # ── Public entry-point ────────────────────────────────────────

    async def execute(self, req: StockExecutionRequest) -> StockExecutionResponse:
        """Validate → resolve creds → submit → persist → respond."""
        trace_id = uuid.uuid4().hex[:12]
        now_iso = datetime.now(timezone.utc).isoformat()
        warnings: list[str] = []

        logger.info(
            "[STOCK_EXEC] trace=%s symbol=%s strategy=%s qty=%d order_type=%s mode=%s",
            trace_id, req.symbol, req.strategy_id, req.qty, req.order_type, req.account_mode,
        )

        # ── 1. Business validation ────────────────────────────────
        self._validate_business_rules(req, warnings)

        # ── 2. Idempotency check ──────────────────────────────────
        if req.client_request_id:
            cached = self.repository.get_idempotent(req.trade_key, req.client_request_id)
            if cached:
                logger.info("[STOCK_EXEC] trace=%s idempotent hit", trace_id)
                return StockExecutionResponse(**cached)

        # ── 3. Resolve credentials ────────────────────────────────
        creds = self._resolve_creds(req.account_mode)
        log_execution_context(
            creds,
            tradier_execution_enabled=self.settings.TRADIER_EXECUTION_ENABLED,
        )

        # ── 4. Fetch last quote (best effort) ─────────────────────
        last_price = await self._fetch_last_price(req.symbol, creds)
        if last_price is not None and req.price_reference is not None:
            drift_pct = abs(last_price - req.price_reference) / req.price_reference * 100
            if drift_pct > 2.0:
                warnings.append(
                    f"Price moved {drift_pct:.1f}% since card was rendered "
                    f"(card ${req.price_reference:.2f} → live ${last_price:.2f})"
                )
        if last_price is None:
            warnings.append("Could not fetch live quote — price guard skipped")

        # ── 5. Build Tradier equity payload ───────────────────────
        payload = self._build_tradier_equity_payload(req, trace_id)

        # ── 6. Submit order ───────────────────────────────────────
        # Paper mode: submit to Tradier sandbox when credentials are
        # configured; fall back to local simulation ONLY if not.
        if req.account_mode == "paper" and not (creds.api_key and creds.account_id):
            logger.warning(
                "[STOCK_EXEC] trace=%s paper creds missing — using local simulator",
                trace_id,
            )
            broker_result = self._paper_fill_simulator(req, last_price, trace_id)
        else:
            broker_result = await self._tradier_submit(payload, creds, trace_id)

        # ── 7. Normalise response ─────────────────────────────────
        resp = StockExecutionResponse(
            status=broker_result["status"],
            broker=broker_result["broker"],
            account_mode=req.account_mode,
            order_id=broker_result.get("order_id"),
            symbol=req.symbol,
            qty=req.qty,
            order_type=req.order_type,
            limit_price=req.limit_price,
            submitted_at=now_iso,
            message=broker_result.get("message", ""),
            raw_broker_response=broker_result.get("raw"),
            warnings=warnings,
            trade_key=req.trade_key,
            client_request_id=req.client_request_id,
        )

        # ── 8. Persist execution record ───────────────────────────
        record = {
            "id": resp.order_id or trace_id,
            "trace_id": trace_id,
            "trade_key": req.trade_key,
            "request": req.model_dump(),
            "response": resp.model_dump(),
            "raw_broker": broker_result.get("raw"),
            "timestamp": now_iso,
        }
        self.repository.save_order(record)

        if req.client_request_id:
            self.repository.save_idempotent(
                req.trade_key, req.client_request_id, resp.model_dump(),
            )

        logger.info(
            "[STOCK_EXEC] trace=%s result=%s order_id=%s",
            trace_id, resp.status, resp.order_id,
        )

        return resp

    # ── Validation ────────────────────────────────────────────────

    def _validate_business_rules(
        self, req: StockExecutionRequest, warnings: list[str],
    ) -> None:
        """Apply business rules beyond Pydantic schema validation.

        Raises HTTPException-friendly ValueError on blocking failures.
        Appends non-blocking notes to *warnings*.
        """
        # Quantity cap
        if req.qty > STOCK_MAX_QTY:
            raise ValueError(
                f"Quantity {req.qty} exceeds maximum allowed ({STOCK_MAX_QTY})"
            )

        # Live safety gates
        if req.account_mode == "live":
            if not self.settings.TRADIER_EXECUTION_ENABLED:
                raise ValueError(
                    "Tradier execution is disabled (TRADIER_EXECUTION_ENABLED=false). "
                    "Enable via the Trade Ticket toggle or set TRADIER_EXECUTION_ENABLED=true."
                )
            if not req.confirm_live:
                raise ValueError(
                    "Live execution requires explicit confirmation. "
                    "Set confirm_live=true to proceed."
                )
            warnings.append("⚠ LIVE MODE — this order will be routed to your brokerage account")

        # Paper mode info
        if req.account_mode == "paper":
            if not self.settings.TRADIER_API_KEY_PAPER and not self.settings.TRADIER_ACCOUNT_ID_PAPER:
                warnings.append(
                    "Paper credentials not configured — using simulated fill"
                )

    # ── Credential resolution ─────────────────────────────────────

    def _resolve_creds(self, account_mode: str) -> TradierCredentials:
        return resolve_tradier_credentials(
            purpose="EXECUTION",
            account_mode=account_mode,  # type: ignore[arg-type]
            live_api_key=self.settings.TRADIER_API_KEY_LIVE,
            live_account_id=self.settings.TRADIER_ACCOUNT_ID_LIVE,
            live_env=self.settings.TRADIER_ENV_LIVE,
            paper_api_key=self.settings.TRADIER_API_KEY_PAPER,
            paper_account_id=self.settings.TRADIER_ACCOUNT_ID_PAPER,
            paper_env=self.settings.TRADIER_ENV_PAPER,
        )

    # ── Tradier equity payload builder ────────────────────────────

    @staticmethod
    def _build_tradier_equity_payload(
        req: StockExecutionRequest, trace_id: str,
    ) -> dict[str, str]:
        """Build Tradier form-data for an equity buy order.

        Tradier equity order API fields:
          class:    "equity"
          symbol:   e.g. "AAPL"
          side:     "buy"
          quantity: e.g. "10"
          type:     "market" | "limit"
          duration: "day" | "gtc"
          price:    required for limit orders (e.g. "150.25")
          tag:      free-text tag for tracking (max 255 chars)
        """
        payload: dict[str, str] = {
            "class": "equity",
            "symbol": req.symbol,
            "side": "buy",
            "quantity": str(req.qty),
            "type": req.order_type,
            "duration": req.time_in_force,
            # Tradier tag: alphanumeric + dashes only (max 255 chars)
            "tag": f"bentrade-{req.strategy_id.replace('_', '-')}-{trace_id}",
        }
        if req.order_type == "limit" and req.limit_price is not None:
            payload["price"] = f"{req.limit_price:.2f}"
        return payload

    # ── Fallback local simulator (no-creds only) ─────────────────

    @staticmethod
    def _paper_fill_simulator(
        req: StockExecutionRequest,
        last_price: float | None,
        trace_id: str,
    ) -> dict[str, Any]:
        """Local simulation used ONLY when paper credentials are missing.

        Returns status 'submitted' (never 'filled') to match real broker
        semantics.  The order_id is prefixed 'sim-' so the UI can tell.
        """
        ref_price = (
            req.limit_price if req.order_type == "limit" and req.limit_price
            else last_price if last_price
            else req.price_reference if req.price_reference
            else 0.0
        )
        return {
            "status": "submitted",
            "broker": "paper-simulator",
            "order_id": f"sim-eq-{uuid.uuid4().hex[:12]}",
            "message": (
                f"Simulated submit (no paper credentials configured). "
                f"Ref price ${ref_price:.2f}"
            ),
            "raw": {
                "simulated": True,
                "ref_price": ref_price,
                "symbol": req.symbol,
                "qty": req.qty,
            },
        }

    # ── Live Tradier submission ───────────────────────────────────

    async def _tradier_submit(
        self,
        payload: dict[str, str],
        creds: TradierCredentials,
        trace_id: str,
    ) -> dict[str, Any]:
        """POST equity order to Tradier accounts endpoint.

        Works for BOTH paper (sandbox) and live — the credentials/base_url
        are already resolved by the caller.
        """
        url = f"{creds.base_url}/accounts/{creds.account_id}/orders"
        headers = {
            "Authorization": f"Bearer {creds.api_key}",
            "Accept": "application/json",
        }

        # Dry-run gate — when execution is disabled, log payload only
        if creds.mode_label == "LIVE-EXEC" and not self.settings.TRADIER_EXECUTION_ENABLED:
            logger.warning(
                "[STOCK_EXEC] DRY_RUN trace=%s mode=%s payload=%s",
                trace_id, creds.mode_label, payload,
            )
            return {
                "status": "submitted",
                "broker": "tradier-dryrun",
                "order_id": f"dryrun-eq-{uuid.uuid4().hex[:10]}",
                "message": "Dry-run enabled — payload logged, no order submitted",
                "raw": {"dry_run": True, "payload": payload},
            }

        # ── Structured pre-submission logging (redacted) ──────────
        masked_key = creds.api_key[-4:] if len(creds.api_key) >= 4 else "????"
        masked_acct = creds.account_id[-4:] if len(creds.account_id) >= 4 else "????"
        logger.info(
            "[STOCK_EXEC] SUBMIT trace=%s mode=%s url=%s "
            "acct_last4=%s key_last4=%s payload=%s",
            trace_id, creds.mode_label, url,
            masked_acct, masked_key, payload,
        )

        try:
            # Tradier expects form-encoded body, NOT query params
            result = await request_json(
                self.http_client, "POST", url,
                data=payload, headers=headers,
            )
        except UpstreamError as exc:
            logger.error(
                "[STOCK_EXEC] FAIL trace=%s mode=%s error=%s details=%s",
                trace_id, creds.mode_label, exc, getattr(exc, 'details', {}),
            )
            return {
                "status": "error",
                "broker": "tradier",
                "order_id": None,
                "message": f"Tradier order failed: {exc}",
                "raw": exc.details,
            }

        # ── Post-submission logging ───────────────────────────────
        logger.info(
            "[STOCK_EXEC] RESPONSE trace=%s mode=%s raw=%s",
            trace_id, creds.mode_label, result,
        )

        order_obj = result.get("order") or {}
        broker_order_id = str(
            order_obj.get("id") or order_obj.get("order_id") or ""
        )
        if not broker_order_id:
            # Tradier didn't return an order id — treat as error
            logger.error(
                "[STOCK_EXEC] NO_ORDER_ID trace=%s raw=%s", trace_id, result,
            )
            return {
                "status": "error",
                "broker": "tradier",
                "order_id": None,
                "message": f"Tradier returned no order id. Response: {result}",
                "raw": result,
            }

        raw_status = str(order_obj.get("status") or "").upper()

        # Map Tradier statuses to our canonical set.
        # IMPORTANT: Only map 'FILLED' → 'filled' when Tradier actually
        # confirms a fill.  Everything else → 'submitted' (honest status).
        # Tradier returns status="ok" on new order acceptance.
        status_map = {
            "OK": "submitted",
            "FILLED": "filled",
            "PARTIALLY_FILLED": "submitted",
            "ACCEPTED": "submitted",
            "PENDING": "submitted",
            "OPEN": "submitted",
            "WORKING": "submitted",
            "REJECTED": "rejected",
            "CANCELED": "rejected",
            "EXPIRED": "rejected",
        }
        normalised = status_map.get(raw_status, "submitted")

        logger.info(
            "[STOCK_EXEC] OK trace=%s order_id=%s raw_status=%s mapped=%s",
            trace_id, broker_order_id, raw_status, normalised,
        )

        return {
            "status": normalised,
            "broker": "tradier",
            "order_id": broker_order_id,
            "message": f"Tradier equity order {raw_status or 'ACCEPTED'}",
            "raw": result,
        }

    # ── Quote helper ──────────────────────────────────────────────

    async def _fetch_last_price(
        self, symbol: str, creds: TradierCredentials,
    ) -> float | None:
        """Best-effort last-price fetch via Tradier quotes endpoint."""
        try:
            # Use DATA credentials (always live) for quote
            data_creds = resolve_tradier_credentials(
                purpose="DATA",
                live_api_key=self.settings.TRADIER_API_KEY_LIVE,
                live_account_id=self.settings.TRADIER_ACCOUNT_ID_LIVE,
                live_env=self.settings.TRADIER_ENV_LIVE,
                paper_api_key=self.settings.TRADIER_API_KEY_PAPER,
                paper_account_id=self.settings.TRADIER_ACCOUNT_ID_PAPER,
                paper_env=self.settings.TRADIER_ENV_PAPER,
            )
            url = f"{data_creds.base_url}/markets/quotes"
            headers = {
                "Authorization": f"Bearer {data_creds.api_key}",
                "Accept": "application/json",
            }
            result = await request_json(
                self.http_client, "GET", url,
                params={"symbols": symbol}, headers=headers,
            )
            quotes = result.get("quotes", {})
            quote = quotes.get("quote", {})
            if isinstance(quote, list):
                quote = quote[0] if quote else {}
            return float(quote.get("last") or quote.get("close") or 0) or None
        except Exception as exc:
            logger.warning("[STOCK_EXEC] quote fetch failed for %s: %s", symbol, exc)
            return None
