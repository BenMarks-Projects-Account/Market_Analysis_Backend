from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from app.config import Settings
from app.services.base_data_service import BaseDataService
from app.storage.repository import InMemoryTradingRepository
from app.trading.broker_base import BrokerBase
from app.trading.models import (
    BrokerResult,
    OrderLeg,
    OrderPreviewResponse,
    OrderSubmitResponse,
    OrderTicket,
    PreviewLeg,
    ProfitLossEstimate,
    TradingPreviewRequest,
    TradingSubmitRequest,
)
from app.trading.risk import evaluate_preview_risk, evaluate_submit_freshness
from app.trading.tradier_credentials import (
    log_execution_context,
    resolve_tradier_credentials,
)
from app.services.trading.order_builder import (
    build_occ_symbol,
    build_tradier_multileg_order,
)
from app.trading.execution_validator import validate_trade_for_execution

logger = logging.getLogger(__name__)


class TradingService:
    def __init__(
        self,
        *,
        settings: Settings,
        base_data_service: BaseDataService,
        repository: InMemoryTradingRepository,
        paper_broker: BrokerBase,
        live_broker: BrokerBase,
        risk_policy_service: Any | None = None,
    ) -> None:
        self.settings = settings
        self.base_data_service = base_data_service
        self.repository = repository
        self.paper_broker = paper_broker
        self.live_broker = live_broker
        self.risk_policy_service = risk_policy_service

    def _secret(self) -> bytes:
        secret = self.settings.TRADING_CONFIRMATION_SECRET or "unsafe-dev-secret"
        return secret.encode("utf-8")

    @staticmethod
    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")

    @staticmethod
    def _b64d(data: str) -> bytes:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded.encode("utf-8"))

    def _ticket_hash(self, ticket: OrderTicket) -> str:
        serialized = json.dumps(ticket.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _make_confirmation_token(self, ticket: OrderTicket, expires_at: datetime) -> str:
        payload = {
            "ticket_id": ticket.id,
            "ticket_hash": self._ticket_hash(ticket),
            "exp": int(expires_at.timestamp()),
            "mode": ticket.mode,
        }
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        sig = hmac.new(self._secret(), payload_bytes, hashlib.sha256).digest()
        return f"{self._b64(payload_bytes)}.{self._b64(sig)}"

    def _validate_confirmation_token(self, token: str, ticket: OrderTicket) -> None:
        try:
            payload_b64, sig_b64 = token.split(".", 1)
            payload_raw = self._b64d(payload_b64)
            expected_sig = hmac.new(self._secret(), payload_raw, hashlib.sha256).digest()
            provided_sig = self._b64d(sig_b64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid confirmation token format") from exc

        if not hmac.compare_digest(expected_sig, provided_sig):
            raise HTTPException(status_code=400, detail="Invalid confirmation token signature")

        payload = json.loads(payload_raw.decode("utf-8"))
        if payload.get("ticket_id") != ticket.id:
            raise HTTPException(status_code=400, detail="Confirmation token ticket mismatch")
        if payload.get("ticket_hash") != self._ticket_hash(ticket):
            raise HTTPException(status_code=400, detail="Confirmation token hash mismatch")
        exp = int(payload.get("exp", 0))
        if datetime.now(timezone.utc).timestamp() > exp:
            raise HTTPException(status_code=400, detail="Confirmation token expired")

    @staticmethod
    def _mid(bid: float | None, ask: float | None) -> float | None:
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    @staticmethod
    def _normalize_side(raw: str) -> str:
        """Normalize any side variant to uppercase BUY_TO_OPEN / SELL_TO_OPEN."""
        s = raw.strip().upper().replace(" ", "_")
        if s in ("BUY_TO_OPEN", "BUY"):
            return "BUY_TO_OPEN"
        if s in ("SELL_TO_OPEN", "SELL"):
            return "SELL_TO_OPEN"
        return s

    def _build_legs_from_preview(
        self,
        *,
        req: TradingPreviewRequest,
        contract_map: dict[str, Any],
    ) -> tuple[list[OrderLeg], str]:
        """Build OrderLeg list from req.legs (multi-leg strategies).

        Input fields per leg: strike, side, option_type, quantity
        Each leg is matched to a contract in the chain for bid/ask/mid.
        Returns (legs, price_effect).
        """
        # Determine price_effect from strategy
        if req.strategy in ("iron_condor", "put_credit", "call_credit"):
            price_effect = "CREDIT"
        else:
            price_effect = "DEBIT"

        order_legs: list[OrderLeg] = []
        for i, pleg in enumerate(req.legs):
            side = self._normalize_side(pleg.side)
            # Lookup contract in chain (keyed by option_type + strike)
            key = f"{pleg.option_type}:{pleg.strike:.8f}"
            contract = contract_map.get(key)
            if not contract:
                available = sorted(contract_map.keys())[:10]
                raise HTTPException(
                    status_code=404,
                    detail={
                        "message": f"Leg {i} not found in option chain",
                        "strike": pleg.strike,
                        "option_type": pleg.option_type,
                        "available_sample": available,
                    },
                )
            # OCC symbol priority: request > chain > reconstructed
            occ = (
                pleg.option_symbol
                or getattr(contract, "symbol", None)
                or build_occ_symbol(
                    req.symbol.upper(), req.expiration, pleg.strike, pleg.option_type
                )
            )
            order_legs.append(OrderLeg(
                option_type=pleg.option_type,
                expiration=req.expiration,
                strike=pleg.strike,
                side=side,
                quantity=pleg.quantity * req.quantity,
                occ_symbol=occ,
                bid=contract.bid,
                ask=contract.ask,
                mid=self._mid(contract.bid, contract.ask),
            ))
        return order_legs, price_effect

    def _build_legs(
        self,
        *,
        req: TradingPreviewRequest,
        short_contract,
        long_contract,
    ) -> tuple[OrderLeg, OrderLeg, str]:
        qty = req.quantity
        if req.strategy == "put_credit":
            short_side, long_side, price_effect = "SELL_TO_OPEN", "BUY_TO_OPEN", "CREDIT"
            option_type = "put"
        elif req.strategy == "call_credit":
            short_side, long_side, price_effect = "SELL_TO_OPEN", "BUY_TO_OPEN", "CREDIT"
            option_type = "call"
        elif req.strategy == "put_debit":
            short_side, long_side, price_effect = "SELL_TO_OPEN", "BUY_TO_OPEN", "DEBIT"
            option_type = "put"
        else:
            short_side, long_side, price_effect = "SELL_TO_OPEN", "BUY_TO_OPEN", "DEBIT"
            option_type = "call"

        # OCC symbol: prefer exact symbol from chain, fall back to reconstructed
        short_occ = (
            getattr(short_contract, "symbol", None)
            or build_occ_symbol(req.symbol.upper(), req.expiration, req.short_strike, option_type)
        )
        long_occ = (
            getattr(long_contract, "symbol", None)
            or build_occ_symbol(req.symbol.upper(), req.expiration, req.long_strike, option_type)
        )

        short_leg = OrderLeg(
            option_type=option_type,
            expiration=req.expiration,
            strike=req.short_strike,
            side=short_side,
            quantity=qty,
            occ_symbol=short_occ,
            bid=short_contract.bid,
            ask=short_contract.ask,
            mid=self._mid(short_contract.bid, short_contract.ask),
        )
        long_leg = OrderLeg(
            option_type=option_type,
            expiration=req.expiration,
            strike=req.long_strike,
            side=long_side,
            quantity=qty,
            occ_symbol=long_occ,
            bid=long_contract.bid,
            ask=long_contract.ask,
            mid=self._mid(long_contract.bid, long_contract.ask),
        )
        return short_leg, long_leg, price_effect

    def _estimate_max_pnl(
        self,
        *,
        width: float,
        limit_price: float,
        quantity: int,
        price_effect: str,
    ) -> tuple[ProfitLossEstimate, ProfitLossEstimate]:
        multiplier = self.settings.TRADING_CONTRACT_MULTIPLIER
        if price_effect == "CREDIT":
            max_profit_per = max(0.0, limit_price)
            max_loss_per = max(0.0, width - limit_price)
        else:
            max_profit_per = max(0.0, width - limit_price)
            max_loss_per = max(0.0, limit_price)

        max_profit = ProfitLossEstimate(
            per_spread=max_profit_per,
            total=max_profit_per * quantity * multiplier,
        )
        max_loss = ProfitLossEstimate(
            per_spread=max_loss_per,
            total=max_loss_per * quantity * multiplier,
        )
        return max_profit, max_loss

    async def preview(self, req: TradingPreviewRequest) -> OrderPreviewResponse:
        trace_id = req.trace_id or f"prev-{uuid.uuid4().hex[:12]}"
        symbol = req.symbol.upper()
        has_legs = bool(req.legs and len(req.legs) >= 2)

        # Validate: legs array OR short/long strikes must be provided
        if not has_legs and (req.short_strike is None or req.long_strike is None):
            raise HTTPException(
                status_code=422,
                detail="Preview requires a legs array (>= 2) or short_strike + long_strike.",
            )

        # ── Tradier DATA calls (always LIVE creds for market data) ──
        logger.info(
            "event=preview_tradier_quote trace_id=%s symbol=%s",
            trace_id, symbol,
        )
        try:
            quote = await self.base_data_service.tradier_client.get_quote(symbol)
        except Exception as exc:
            logger.error(
                "event=preview_quote_failed trace_id=%s symbol=%s error=%s",
                trace_id, symbol, exc,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Tradier quote fetch failed for {symbol}: {exc}",
            ) from exc
        quote_ts = datetime.now(timezone.utc)

        logger.info(
            "event=preview_tradier_chain trace_id=%s symbol=%s expiration=%s",
            trace_id, symbol, req.expiration,
        )
        try:
            raw_chain = await self.base_data_service.tradier_client.get_chain(symbol, req.expiration, greeks=True)
        except Exception as exc:
            logger.error(
                "event=preview_chain_failed trace_id=%s symbol=%s expiration=%s error=%s",
                trace_id, symbol, req.expiration, exc,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Tradier chain fetch failed for {symbol} exp={req.expiration}: {exc}",
            ) from exc
        chain_ts = datetime.now(timezone.utc)

        contracts = self.base_data_service.normalize_chain(raw_chain)

        if has_legs:
            # ── Legs-based path (all strategies with legs array) ─────
            # Index ALL contracts by option_type + strike for mixed put/call lookup
            filtered = [c for c in contracts if c.expiration == req.expiration]
            contract_map = {f"{c.option_type}:{c.strike:.8f}": c for c in filtered}

            logger.info(
                "event=preview_chain_normalized trace_id=%s total_contracts=%d "
                "filtered=%d strategy=%s legs=%d expiration=%s",
                trace_id, len(contracts), len(filtered), req.strategy,
                len(req.legs), req.expiration,
            )

            order_legs, price_effect = self._build_legs_from_preview(
                req=req, contract_map=contract_map,
            )

            # Width: for multi-type legs use the narrower wing;
            # for same-type legs use min-to-max distance
            strikes_by_type: dict[str, list[float]] = {}
            for leg in req.legs:
                strikes_by_type.setdefault(leg.option_type, []).append(leg.strike)
            widths = [abs(max(s) - min(s)) for s in strikes_by_type.values() if len(s) >= 2]
            width = min(widths) if widths else abs(req.limit_price)

            # Spread mid: sum of sell mids minus sum of buy mids
            sell_mid = sum(ol.mid or 0.0 for ol in order_legs if ol.side == "SELL_TO_OPEN")
            buy_mid = sum(ol.mid or 0.0 for ol in order_legs if ol.side == "BUY_TO_OPEN")
            spread_mid = sell_mid - buy_mid if price_effect == "CREDIT" else buy_mid - sell_mid

            # For risk checks, use first sell leg as short_leg, first buy leg as long_leg
            short_leg = next((ol for ol in order_legs if ol.side == "SELL_TO_OPEN"), order_legs[0])
            long_leg = next((ol for ol in order_legs if ol.side == "BUY_TO_OPEN"), order_legs[-1])
        else:
            # ── Fallback 2-leg path (short_strike/long_strike only) ──
            option_type = "put" if "put" in req.strategy else "call"
            filtered = [c for c in contracts if c.option_type == option_type and c.expiration == req.expiration]
            contract_map_2leg = {f"{c.strike:.8f}": c for c in filtered}

            logger.info(
                "event=preview_chain_normalized trace_id=%s total_contracts=%d "
                "filtered=%d option_type=%s expiration=%s",
                trace_id, len(contracts), len(filtered), option_type, req.expiration,
            )

            short_contract = contract_map_2leg.get(f"{req.short_strike:.8f}")
            long_contract = contract_map_2leg.get(f"{req.long_strike:.8f}")
            if not short_contract or not long_contract:
                available_strikes = sorted([c.strike for c in filtered])[:10]
                logger.warning(
                    "event=preview_legs_not_found trace_id=%s "
                    "short_strike=%s long_strike=%s available_strikes_sample=%s",
                    trace_id, req.short_strike, req.long_strike, available_strikes,
                )
                raise HTTPException(
                    status_code=404,
                    detail={
                        "message": "One or both spread legs not found in option chain",
                        "short_strike": req.short_strike,
                        "long_strike": req.long_strike,
                        "short_found": short_contract is not None,
                        "long_found": long_contract is not None,
                        "available_strikes_sample": available_strikes,
                        "chain_size": len(filtered),
                    },
                )

            short_leg, long_leg, price_effect = self._build_legs(
                req=req,
                short_contract=short_contract,
                long_contract=long_contract,
            )
            order_legs = [short_leg, long_leg]

            width = abs(req.short_strike - req.long_strike)
            spread_mid = 0.0
            if short_leg.mid is not None and long_leg.mid is not None:
                if price_effect == "CREDIT":
                    spread_mid = short_leg.mid - long_leg.mid
                else:
                    spread_mid = long_leg.mid - short_leg.mid

        max_profit, max_loss = self._estimate_max_pnl(
            width=width,
            limit_price=req.limit_price,
            quantity=req.quantity,
            price_effect=price_effect,
        )

        risk = evaluate_preview_risk(
            settings=self.settings,
            strategy=req.strategy,
            width=width,
            max_loss_per_spread=max_loss.per_spread * self.settings.TRADING_CONTRACT_MULTIPLIER,
            net_credit_or_debit=max(0.0, spread_mid),
            short_leg=short_leg,
            long_leg=long_leg,
            limit_price=req.limit_price,
        )

        checks = dict(risk.checks)
        checks.update(
            {
                "legs_found": True,
                "spread_mid": round(spread_mid, 6),
                "underlying_price": quote.get("last") or quote.get("close") or quote.get("mark"),
            }
        )

        hard_reject_keys = ["width_ok", "max_loss_ok", "credit_floor_ok", "legs_have_bid_ask"]
        hard_failures = [k for k in hard_reject_keys if checks.get(k) is False]
        if hard_failures:
            logger.warning(
                "event=preview_hard_check_fail trace_id=%s failed=%s checks=%s",
                trace_id, hard_failures, checks,
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Preview failed risk hard checks",
                    "failed_checks": hard_failures,
                    "checks": checks,
                    "warnings": risk.warnings,
                },
            )

        ticket = OrderTicket(
            id=str(uuid.uuid4()),
            mode=req.mode,
            strategy=req.strategy,
            underlying=symbol,
            expiration=req.expiration,
            quantity=req.quantity,
            limit_price=req.limit_price,
            price_effect=price_effect,
            time_in_force=req.time_in_force,
            legs=order_legs,
            estimated_max_profit=max_profit,
            estimated_max_loss=max_loss,
            created_at=datetime.now(timezone.utc),
            asof_quote_ts=quote_ts,
            asof_chain_ts=chain_ts,
        )
        self.repository.save_ticket(ticket.model_dump(mode="json"))

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.settings.TRADING_CONFIRMATION_TTL_SECONDS)
        token = self._make_confirmation_token(ticket, expires_at)
        logger.info(
            "event=preview_complete trace_id=%s ticket_id=%s underlying=%s strategy=%s legs=%d",
            trace_id, ticket.id, symbol, req.strategy, len(ticket.legs),
        )

        # ── Tradier preview call (POST /orders with preview=true) ──
        # After local risk checks pass, call Tradier's orders endpoint with
        # preview=true to get buying power effect and validation.
        # to get buying power effect and validate the order format.
        tradier_preview_data: dict | None = None
        tradier_preview_error: str | None = None
        tradier_payload_sent: dict | None = None

        try:
            # Resolve credentials for preview (uses PAPER for paper mode)
            preview_creds = resolve_tradier_credentials(
                purpose="EXECUTION",
                account_mode=req.mode,
                live_api_key=self.settings.TRADIER_API_KEY_LIVE,
                live_account_id=self.settings.TRADIER_ACCOUNT_ID_LIVE,
                live_env=self.settings.TRADIER_ENV_LIVE,
                paper_api_key=self.settings.TRADIER_API_KEY_PAPER,
                paper_account_id=self.settings.TRADIER_ACCOUNT_ID_PAPER,
                paper_env=self.settings.TRADIER_ENV_PAPER,
            )
            has_creds = bool(preview_creds and preview_creds.api_key and preview_creds.account_id)

            if has_creds:
                print(f"=== SERVICE.PREVIEW: has_creds=True, mode={req.mode} ===")  # DIAGNOSTIC
                # Build Tradier payload from the ticket's constructed OrderLeg objects
                tradier_payload_sent = build_tradier_multileg_order(
                    {
                        "symbol": symbol,
                        "limit_price": req.limit_price,
                        "time_in_force": req.time_in_force,
                        "legs": [
                            {
                                "occ_symbol": ol.occ_symbol,
                                "side": ol.side,
                                "qty": ol.quantity,
                            }
                            for ol in order_legs
                        ],
                    },
                    preview=True,
                )
                logger.info(
                    "event=tradier_preview_call trace_id=%s mode=%s "
                    "base_url=%s acct_last4=%s payload=%s",
                    trace_id, req.mode, preview_creds.base_url,
                    (preview_creds.account_id or "")[-4:],
                    tradier_payload_sent,
                )

                tradier_preview_data = await self.live_broker.preview_raw_payload(
                    tradier_payload_sent, creds=preview_creds, trace_id=trace_id,
                )
                logger.info(
                    "event=tradier_preview_ok trace_id=%s response=%s",
                    trace_id, tradier_preview_data,
                )
            else:
                tradier_preview_error = "No Tradier credentials configured for preview"
                logger.warning(
                    "event=tradier_preview_skip trace_id=%s reason=no_credentials mode=%s",
                    trace_id, req.mode,
                )
        except Exception as prev_exc:
            # Non-fatal: Tradier preview failure should NOT block our local preview.
            # Surface the error to the UI for debugging.
            tradier_preview_error = str(prev_exc)
            if hasattr(prev_exc, "details") and prev_exc.details:
                # Preserve structured error — includes status_code and full body
                tradier_preview_error = str(prev_exc)
                tradier_preview_data = {
                    "error": True,
                    "message": str(prev_exc),
                    "upstream_status": prev_exc.details.get("status_code"),
                    "upstream_body": prev_exc.details.get("body", ""),
                    "upstream_url": prev_exc.details.get("url", ""),
                }
            logger.warning(
                "event=tradier_preview_failed trace_id=%s error=%s details=%s",
                trace_id, prev_exc,
                prev_exc.details if hasattr(prev_exc, "details") else "N/A",
                exc_info=True,
            )

        # ── Risk policy check (Phase 1 — warnings only) ─────────
        policy_warnings: list[dict[str, str]] = []
        policy_status = "clear"
        if self.risk_policy_service is not None:
            try:
                snapshot = await self.risk_policy_service.build_snapshot(None)
                warn_groups = (snapshot.get("exposure") or {}).get("warnings") or {}
                for msg in warn_groups.get("hard_limits", []):
                    policy_warnings.append({"severity": "hard", "message": str(msg)})
                for msg in warn_groups.get("soft_gates", []):
                    policy_warnings.append({"severity": "soft", "message": str(msg)})
                if policy_warnings:
                    policy_status = "warning"
            except Exception as pol_exc:
                logger.warning("event=risk_policy_check_failed trace_id=%s error=%s", trace_id, pol_exc)

        return OrderPreviewResponse(
            ticket=ticket,
            checks=checks,
            warnings=risk.warnings,
            confirmation_token=token,
            expires_at=expires_at,
            trace_id=trace_id,
            tradier_preview=tradier_preview_data,
            tradier_preview_error=tradier_preview_error,
            payload_sent=tradier_payload_sent,
            policy_warnings=policy_warnings,
            policy_status=policy_status,
        )

    async def submit(self, req: TradingSubmitRequest) -> OrderSubmitResponse:
        trace_id = req.trace_id or f"sub-{uuid.uuid4().hex[:12]}"
        ticket_raw = self.repository.get_ticket(req.ticket_id)
        if not ticket_raw:
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket = OrderTicket.model_validate(ticket_raw)
        # In dev mode, live→paper override happens after this check,
        # so only reject mismatches when NOT in dev mode.
        if ticket.mode != req.mode:
            if not (self.settings.ENVIRONMENT == "development" and req.mode == "live"):
                raise HTTPException(status_code=400, detail="Submit mode does not match preview mode")

        self._validate_confirmation_token(req.confirmation_token, ticket)

        cached = self.repository.get_idempotent(req.ticket_id, req.idempotency_key)
        if cached:
            return OrderSubmitResponse.model_validate(cached)

        # ── Server-side validation safety net ────────────────────
        validation = validate_trade_for_execution(ticket_raw)
        if not validation["valid"]:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "server_side_validation_failed",
                    "blocking_errors": validation["blocking_errors"],
                    "warnings": validation["warnings"],
                },
            )

        # ── Development safety: force PAPER mode ─────────────────
        is_dev = self.settings.ENVIRONMENT == "development"
        dev_mode_forced_paper = False
        account_mode = req.mode  # "paper" | "live"

        if is_dev and account_mode == "live":
            logger.warning(
                "event=dev_mode_force_paper trace_id=%s "
                "reason=BENTRADE_ENVIRONMENT=development, live orders blocked",
                trace_id,
            )
            account_mode = "paper"
            dev_mode_forced_paper = True

        # ── Single execution gate ────────────────────────────────
        # TRADIER_EXECUTION_ENABLED is the ONE flag that controls
        # whether orders are sent to Tradier or dry-run logged.
        tradier_execution_enabled = self.settings.TRADIER_EXECUTION_ENABLED

        # Always resolve credentials — both paper and live need them for Tradier
        try:
            creds = resolve_tradier_credentials(
                purpose="EXECUTION",
                account_mode=account_mode,
                live_api_key=self.settings.TRADIER_API_KEY_LIVE,
                live_account_id=self.settings.TRADIER_ACCOUNT_ID_LIVE,
                live_env=self.settings.TRADIER_ENV_LIVE,
                paper_api_key=self.settings.TRADIER_API_KEY_PAPER,
                paper_account_id=self.settings.TRADIER_ACCOUNT_ID_PAPER,
                paper_env=self.settings.TRADIER_ENV_PAPER,
            )
        except ValueError as exc:
            if account_mode == "live":
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            # Paper mode without creds will be handled below
            creds = None

        if creds:
            log_execution_context(
                creds,
                tradier_execution_enabled=tradier_execution_enabled,
            )

        logger.info(
            "event=order_submit_start trace_id=%s mode=%s ticket_id=%s "
            "underlying=%s strategy=%s has_creds=%s execution_enabled=%s",
            trace_id, account_mode, ticket.id, ticket.underlying,
            ticket.strategy, creds is not None, tradier_execution_enabled,
        )

        # ── Effective dry_run: single flag is the source of truth ──
        # TRADIER_EXECUTION_ENABLED=True  → dry_run=False (submit to Tradier)
        # TRADIER_EXECUTION_ENABLED=False → dry_run=True  (log only)
        effective_dry_run = not tradier_execution_enabled

        # Use account_mode (post-dev-override), not req.mode (raw frontend value)
        if account_mode == "live":
            if not tradier_execution_enabled:
                raise HTTPException(status_code=403, detail="Live trading is disabled (TRADIER_EXECUTION_ENABLED=false)")

            freshness = evaluate_submit_freshness(ticket, max_age_seconds=self.settings.LIVE_DATA_MAX_AGE_SECONDS)
            if not freshness["data_fresh"]:
                raise HTTPException(status_code=400, detail=f"Live submit rejected: stale market data ({freshness})")

            result = await self.live_broker.place_order(
                ticket, creds=creds, trace_id=trace_id, dry_run=False,
            )
        else:
            # Paper mode — use TradierBroker with sandbox creds when available,
            # fall back to local PaperBroker simulator only when creds are missing.
            paper_has_creds = bool(
                creds and creds.api_key and creds.account_id
            )
            if paper_has_creds:
                # Route through TradierBroker with sandbox credentials.
                # dry_run is controlled by the trade capability toggle:
                #   toggle ON  → dry_run=False → real Tradier sandbox order
                #   toggle OFF → dry_run=True  → payload logged, not submitted
                logger.info(
                    "event=paper_tradier_submit trace_id=%s base_url=%s "
                    "acct_last4=%s dry_run=%s",
                    trace_id, creds.base_url,
                    (creds.account_id or "")[-4:], effective_dry_run,
                )
                result = await self.live_broker.place_order(
                    ticket, creds=creds, trace_id=trace_id,
                    dry_run=effective_dry_run,
                )
            else:
                logger.warning(
                    "event=paper_simulator_fallback trace_id=%s "
                    "reason=no_paper_credentials_configured",
                    trace_id,
                )
                result = await self.paper_broker.place_order(ticket)

        # ── Reconciliation: poll Tradier for real order status ────
        tradier_raw_status = result.raw.get("order", {}).get("status") if result.raw else None
        if (
            result.broker == "tradier"
            and result.broker_order_id
            and not result.broker_order_id.startswith("dryrun-")
            and creds
        ):
            reconciled = await self._reconcile_order(
                result.broker_order_id, creds=creds, trace_id=trace_id,
            )
            if reconciled:
                tradier_raw_status = reconciled.get("status")
                # Update result status based on Tradier's actual status
                _RECON_MAP = {
                    "pending": "ACCEPTED",
                    "open": "WORKING",
                    "partially_filled": "WORKING",
                    "filled": "FILLED",
                    "expired": "REJECTED",
                    "canceled": "REJECTED",
                    "rejected": "REJECTED",
                }
                mapped = _RECON_MAP.get(
                    str(tradier_raw_status or "").lower(),
                )
                if mapped:
                    result = BrokerResult(
                        broker=result.broker,
                        status=mapped,
                        broker_order_id=result.broker_order_id,
                        message=f"Tradier order {mapped} (reconciled: {tradier_raw_status})",
                        raw=result.raw,
                    )

        # ── Destination metadata ─────────────────────────────────
        destination = account_mode  # "paper" or "live" after dev-mode override
        if destination == "paper":
            destination_label = "Tradier PAPER (sandbox)"
        else:
            destination_label = "Tradier LIVE"

        # Append dev-mode note when auto-forced
        note_suffix = ""
        if dev_mode_forced_paper:
            note_suffix = " — Development mode forces PAPER routing"

        response = OrderSubmitResponse(
            broker=result.broker,
            status=result.status,
            broker_order_id=result.broker_order_id,
            message=result.message + note_suffix,
            created_at=datetime.now(timezone.utc),
            account_mode_used=account_mode,
            trace_id=trace_id,
            tradier_raw_status=tradier_raw_status,
            dry_run=result.status == "DRY_RUN",
            destination=destination,
            destination_label=destination_label,
            dev_mode_forced_paper=dev_mode_forced_paper,
        )

        order_record = {
            "id": response.broker_order_id,
            "ticket_id": ticket.id,
            "idempotency_key": req.idempotency_key,
            "request_mode": req.mode,
            "trace_id": trace_id,
            "ticket": ticket.model_dump(mode="json"),
            "result": response.model_dump(mode="json"),
            "raw": result.raw,
        }
        self.repository.save_order(order_record)
        self.repository.save_idempotent(req.ticket_id, req.idempotency_key, response.model_dump(mode="json"))

        logger.info(
            "event=order_submit_complete trace_id=%s broker=%s status=%s "
            "broker_order_id=%s tradier_raw=%s",
            trace_id, result.broker, result.status,
            result.broker_order_id, tradier_raw_status,
        )

        return response

    async def _reconcile_order(
        self,
        broker_order_id: str,
        *,
        creds: "TradierCredentials",
        trace_id: str,
    ) -> dict | None:
        """Poll Tradier for order status after submission.

        Retries 3 times with backoff (0.5s, 1s, 2s) to give Tradier time
        to process the order. Returns the order dict or None on failure.
        """
        delays = [0.5, 1.0, 2.0]
        for attempt, delay in enumerate(delays, 1):
            await asyncio.sleep(delay)
            try:
                result = await self.live_broker.get_order_status(
                    broker_order_id, creds=creds,
                )
                order_data = result.get("order", result)
                status = str(order_data.get("status") or "").lower()
                logger.info(
                    "event=reconcile_poll trace_id=%s attempt=%d/%d "
                    "order_id=%s status=%s",
                    trace_id, attempt, len(delays), broker_order_id, status,
                )
                # If we got a definitive status, return immediately
                if status in ("filled", "rejected", "canceled", "expired"):
                    return order_data
                # If pending/open, keep polling
            except Exception as exc:
                logger.warning(
                    "event=reconcile_poll_error trace_id=%s attempt=%d/%d err=%s",
                    trace_id, attempt, len(delays), exc,
                )
        # Return last known state even if not definitive
        try:
            result = await self.live_broker.get_order_status(
                broker_order_id, creds=creds,
            )
            return result.get("order", result)
        except Exception:
            return None
