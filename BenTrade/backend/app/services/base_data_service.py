import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from app.clients.finnhub_client import FinnhubClient
from app.clients.fred_client import FredClient
from app.clients.polygon_client import PolygonClient
from app.clients.tradier_client import TradierClient
from app.models.schemas import OptionContract
from app.utils.http import UpstreamError
from app.utils.validation import clamp, parse_expiration, validate_bid_ask, validate_symbol

logger = logging.getLogger(__name__)


class BaseDataService:
    def __init__(
        self,
        tradier_client: TradierClient,
        finnhub_client: FinnhubClient,
        fred_client: FredClient,
        polygon_client: PolygonClient | None = None,
    ) -> None:
        self.tradier_client = tradier_client
        self.finnhub_client = finnhub_client
        self.fred_client = fred_client
        self.polygon_client = polygon_client
        self._source_health: dict[str, dict[str, Any]] = {
            "finnhub": self._new_source_state(),
            "polygon": self._new_source_state(),
            "tradier": self._new_source_state(),
            "fred": self._new_source_state(),
        }

    def _source_configured(self, source: str) -> bool:
        key = str(source or "").strip().lower()
        if key == "tradier":
            token = str(getattr(self.tradier_client.settings, "TRADIER_TOKEN", "") or "").strip()
            return bool(token)
        if key == "finnhub":
            api_key = str(getattr(self.finnhub_client.settings, "FINNHUB_KEY", "") or "").strip()
            return bool(api_key)
        if key == "fred":
            api_key = str(getattr(self.fred_client.settings, "FRED_KEY", "") or "").strip()
            return bool(api_key)
        if key == "polygon":
            if self.polygon_client is None:
                return False
            api_key = str(getattr(self.polygon_client.settings, "POLYGON_API_KEY", "") or "").strip()
            return bool(api_key)
        return False

    async def refresh_source_health_probe(self) -> None:
        async def _probe(source: str, fn):
            if not self._source_configured(source):
                self._source_health[source]["last_status"] = "error"
                self._source_health[source]["message"] = "not configured"
                return
            try:
                ok = await fn()
                if ok:
                    self._mark_success(source, http_status=200, message="healthy")
                else:
                    self._mark_failure(source, UpstreamError("health check failed", details={"status_code": 503}))
            except Exception as exc:
                self._mark_failure(source, exc)

        probes = [
            _probe("tradier", self.tradier_client.health),
            _probe("finnhub", self.finnhub_client.health),
            _probe("fred", self.fred_client.health),
        ]
        if self.polygon_client is not None:
            probes.append(_probe("polygon", self.polygon_client.health))
        await asyncio.gather(*probes, return_exceptions=True)

    @staticmethod
    def _new_source_state() -> dict[str, Any]:
        return {
            "last_http": None,
            "last_ok_ts": None,
            "last_error_ts": None,
            "last_status": "unknown",
            "last_error_kind": None,
            "message": "",
            "consecutive_5xx": 0,
            "failure_events": [],
        }

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(ts: datetime | None) -> str | None:
        if ts is None:
            return None
        return ts.isoformat()

    @staticmethod
    def _extract_http_status(error: Exception) -> int | None:
        if isinstance(error, UpstreamError):
            status = (error.details or {}).get("status_code")
            try:
                return int(status)
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _extract_error_kind(error: Exception) -> str:
        status = BaseDataService._extract_http_status(error)
        if status is not None:
            if status == 429:
                return "rate_limit"
            if status in (401, 403):
                return "auth"
            if status >= 500:
                return "http_5xx"
            return "http"

        text = str(error).lower()
        if "too many requests" in text or "rate limit" in text or "429" in text:
            return "rate_limit"
        if "timed out" in text or "timeout" in text:
            return "timeout"
        return "network"

    def _trim_failures(self, state: dict[str, Any], now: datetime) -> None:
        cutoff = now - timedelta(minutes=5)
        failures = state.get("failure_events") or []
        state["failure_events"] = [ts for ts in failures if isinstance(ts, datetime) and ts >= cutoff]

    def _mark_success(self, source: str, *, http_status: int | None = 200, message: str = "") -> None:
        state = self._source_health[source]
        now = self._utc_now()
        state["last_http"] = http_status
        state["last_ok_ts"] = now
        state["last_status"] = "ok"
        state["message"] = message
        state["last_error_kind"] = None
        state["consecutive_5xx"] = 0
        self._trim_failures(state, now)

    def _mark_failure(self, source: str, error: Exception) -> None:
        state = self._source_health[source]
        now = self._utc_now()
        status = self._extract_http_status(error)
        kind = self._extract_error_kind(error)

        state["last_http"] = status
        state["last_error_ts"] = now
        state["last_status"] = "error"
        state["last_error_kind"] = kind
        state["message"] = str(error)

        if status is not None and status >= 500:
            state["consecutive_5xx"] = int(state.get("consecutive_5xx") or 0) + 1
        else:
            state["consecutive_5xx"] = 0

        failures = state.get("failure_events") or []
        failures.append(now)
        state["failure_events"] = failures
        self._trim_failures(state, now)

    def get_source_health_snapshot(self) -> dict[str, dict[str, Any]]:
        now = self._utc_now()
        out: dict[str, dict[str, Any]] = {}
        for source, state in self._source_health.items():
            self._trim_failures(state, now)
            failures_recent = len(state.get("failure_events") or [])
            last_http = state.get("last_http")
            last_ok_ts = state.get("last_ok_ts")
            last_error_kind = state.get("last_error_kind")

            status = "red"
            message = state.get("message") or "unavailable"

            if not self._source_configured(source):
                status = "red"
                message = "misconfigured" if source == "polygon" else "not configured"
                out[source] = {
                    "status": status,
                    "last_http": last_http,
                    "last_ok_ts": self._iso(last_ok_ts),
                    "last_error_ts": self._iso(state.get("last_error_ts")),
                    "message": message,
                }
                continue

            if last_http in (401, 403):
                status = "red"
                message = message or "auth failure"
            elif int(state.get("consecutive_5xx") or 0) >= 3:
                status = "red"
                message = message or "repeated upstream 5xx"
            elif last_error_kind == "network" and (
                last_ok_ts is None or (now - last_ok_ts) >= timedelta(minutes=5)
            ):
                status = "red"
                message = message or "network unavailable"
            elif last_http == 429 or last_error_kind in ("timeout", "rate_limit"):
                status = "yellow"
                if not message:
                    message = "degraded"
            elif state.get("last_status") == "ok":
                if failures_recent > 0:
                    status = "yellow"
                    message = "intermittent errors"
                else:
                    status = "green"
                    message = "healthy"
            else:
                status = "red"
                message = message or "unavailable"

            out[source] = {
                "status": status,
                "last_http": last_http,
                "last_ok_ts": self._iso(last_ok_ts),
                "last_error_ts": self._iso(state.get("last_error_ts")),
                "message": message,
            }
        return out

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        return parsed

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        try:
            return int(parsed)
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _normalize_symbol(symbol: Any) -> str | None:
        return validate_symbol(symbol)

    @staticmethod
    def _parse_expiration(expiration: Any) -> tuple[str | None, int | None]:
        return parse_expiration(expiration)

    def _mark_validation_warning(self, source: str, message: str) -> None:
        logger.warning("event=ingress_validation_warning source=%s message=%s", source, message)
        self._mark_failure(
            source,
            UpstreamError(
                f"validation warning: {message}",
                details={"status_code": 429},
            ),
        )

    def _validate_quote_sanity(self, source: str, quote: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        if not isinstance(quote, dict):
            return {}, ["quote payload not an object"]

        cleaned = dict(quote)
        bid, ask, raw_warnings = validate_bid_ask(cleaned.get("bid"), cleaned.get("ask"))
        warnings = [f"quote {warning.lower()}" for warning in raw_warnings]
        cleaned["bid"] = bid
        cleaned["ask"] = ask

        return cleaned, warnings

    @staticmethod
    def _normalize_iv(value: float | None) -> float | None:
        if value is None:
            return None
        if not math.isfinite(value):
            return None
        if value > 1.0:
            return value / 100.0
        return value

    def normalize_chain(self, contracts: list[dict[str, Any]]) -> list[OptionContract]:
        normalized: list[OptionContract] = []
        for row in contracts:
            raw_type = (row.get("option_type") or row.get("type") or "").lower()
            if raw_type not in ("put", "call"):
                symbol = (row.get("symbol") or "").upper()
                if len(symbol) >= 9:
                    opt_char = symbol[-9:-8]
                    if opt_char == "P":
                        raw_type = "put"
                    elif opt_char == "C":
                        raw_type = "call"

            greeks = row.get("greeks") or {}
            iv = (
                self._to_float(row.get("iv"))
                or self._to_float(row.get("implied_vol"))
                or self._to_float(greeks.get("smv_vol"))
                or self._to_float(greeks.get("mid_iv"))
            )

            if raw_type not in ("put", "call"):
                continue

            strike = self._to_float(row.get("strike"))
            expiration_raw = str(row.get("expiration_date") or row.get("expiration") or "")
            expiration, _ = self._parse_expiration(expiration_raw)
            if strike is None or not expiration:
                self._mark_validation_warning("tradier", "chain row missing/invalid strike or expiration")
                continue

            bid, ask, quote_warnings = validate_bid_ask(row.get("bid"), row.get("ask"))
            for warning in quote_warnings:
                self._mark_validation_warning("tradier", f"chain row {warning.lower()}")
            if "ASK_LT_BID" in quote_warnings:
                continue

            delta = self._to_float(greeks.get("delta")) if isinstance(greeks, dict) else None
            delta, delta_warning = clamp(
                delta,
                minimum=-1.0,
                maximum=1.0,
                field="delta",
                warning_code="DELTA_CLAMP",
            )
            if delta_warning:
                self._mark_validation_warning("tradier", f"chain row {delta_warning}")

            open_interest = self._to_int(row.get("open_interest"))
            open_interest, oi_warning = clamp(
                open_interest,
                minimum=0,
                field="open_interest",
                warning_code="OI_CLAMP",
            )
            if oi_warning:
                self._mark_validation_warning("tradier", f"chain row {oi_warning}")
            open_interest = int(open_interest) if open_interest is not None else None

            volume = self._to_int(row.get("volume"))
            volume, volume_warning = clamp(
                volume,
                minimum=0,
                field="volume",
                warning_code="VOLUME_CLAMP",
            )
            if volume_warning:
                self._mark_validation_warning("tradier", f"chain row {volume_warning}")
            volume = int(volume) if volume is not None else None

            iv = self._normalize_iv(iv)
            if iv is None and row.get("iv") not in (None, ""):
                self._mark_validation_warning("tradier", "chain row iv non-finite")

            normalized.append(
                OptionContract(
                    option_type=raw_type,
                    strike=strike,
                    expiration=expiration,
                    bid=bid,
                    ask=ask,
                    open_interest=open_interest,
                    volume=volume,
                    delta=delta,
                    iv=iv,
                    symbol=row.get("symbol"),
                )
            )
        return normalized

    async def get_underlying_price(self, symbol: str) -> float | None:
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            self._mark_validation_warning("tradier", "invalid symbol")
            self._mark_validation_warning("finnhub", "invalid symbol")
            return None

        try:
            quote = await self.tradier_client.get_quote(normalized_symbol)
            self._mark_success("tradier", http_status=200, message="quote ok")
        except Exception as exc:
            self._mark_failure("tradier", exc)
            quote = {}

        quote, quote_warnings = self._validate_quote_sanity("tradier", quote)
        for warning in quote_warnings:
            self._mark_validation_warning("tradier", warning)

        for field in ("last", "close", "mark", "bid", "ask"):
            value = self._to_float(quote.get(field))
            if value is not None:
                return value

        logger.info("event=underlying_fallback symbol=%s source=finnhub", normalized_symbol)
        try:
            fb = await self.finnhub_client.get_quote(normalized_symbol)
            self._mark_success("finnhub", http_status=200, message="quote fallback ok")
        except Exception as exc:
            self._mark_failure("finnhub", exc)
            return None

        for field in ("c", "pc", "o", "h", "l"):
            value = self._to_float(fb.get(field))
            if value is not None:
                return value
        return None

    async def get_snapshot(self, symbol: str) -> dict[str, Any]:
        normalized_symbol = self._normalize_symbol(symbol)
        warnings: list[str] = []
        if not normalized_symbol:
            warnings.append("invalid symbol")
            return {
                "symbol": str(symbol or "").upper(),
                "underlying_price": None,
                "vix": None,
                "prices_history": [],
                "warnings": warnings,
            }

        underlying_task = asyncio.create_task(self.get_underlying_price(normalized_symbol))
        vix_task = asyncio.create_task(self.fred_client.get_latest_series_value())
        candles_task = asyncio.create_task(self.get_prices_history(normalized_symbol, lookback_days=365))

        underlying_price, vix, candles = await asyncio.gather(
            underlying_task, vix_task, candles_task, return_exceptions=True
        )

        if isinstance(underlying_price, Exception):
            underlying_price = None
        if isinstance(vix, Exception):
            vix = None
        if isinstance(candles, Exception):
            logger.warning("event=snapshot_candles_unavailable symbol=%s error=%s", symbol.upper(), str(candles))
            candles = []

        closes = [float(x) for x in (candles or []) if x is not None]

        if not closes:
            warnings.append("history_unavailable: no close prices returned")
            logger.warning("event=snapshot_no_closes symbol=%s", normalized_symbol)

        return {
            "symbol": normalized_symbol,
            "underlying_price": underlying_price,
            "vix": vix,
            "prices_history": closes[-160:],
            "warnings": warnings,
        }

    async def get_prices_history(self, symbol: str, lookback_days: int = 365) -> list[float]:
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            self._mark_validation_warning("polygon", "invalid symbol")
            self._mark_validation_warning("tradier", "invalid symbol")
            return []

        # Primary: Polygon aggregates
        if self.polygon_client is not None and self._source_configured("polygon"):
            try:
                closes = await self.polygon_client.get_daily_closes(
                    normalized_symbol, lookback_days=lookback_days
                )
                if closes:
                    self._mark_success("polygon", http_status=200, message="history ok")
                    return [float(x) for x in closes if x is not None]
                self._mark_failure(
                    "polygon",
                    UpstreamError("Polygon returned empty history", details={"status_code": 204}),
                )
                logger.warning("event=prices_history_empty symbol=%s source=polygon", normalized_symbol)
            except Exception as exc:
                self._mark_failure("polygon", exc)
                logger.warning(
                    "event=prices_history_unavailable symbol=%s source=polygon error=%s",
                    normalized_symbol,
                    str(exc),
                )

        # Fallback: Tradier daily history
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days)
        try:
            fallback = await self.tradier_client.get_daily_closes(
                normalized_symbol,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
            self._mark_success("tradier", http_status=200, message="history fallback ok")
            return [float(x) for x in fallback if x is not None]
        except Exception as exc:
            self._mark_failure("tradier", exc)
            logger.warning(
                "event=prices_history_unavailable symbol=%s source=tradier error=%s",
                normalized_symbol,
                str(exc),
            )
            return []

    async def _get_chain_with_health(self, symbol: str, expiration: str, greeks: bool = True) -> list[dict[str, Any]]:
        try:
            chain = await self.tradier_client.get_chain(symbol, expiration=expiration, greeks=greeks)
            self._mark_success("tradier", http_status=200, message="chain ok")
            return chain
        except Exception as exc:
            self._mark_failure("tradier", exc)
            raise

    async def _get_vix_with_health(self) -> float | None:
        try:
            value = await self.fred_client.get_latest_series_value()
            self._mark_success("fred", http_status=200, message="series ok")
            return value
        except Exception as exc:
            self._mark_failure("fred", exc)
            raise

    async def get_analysis_inputs(self, symbol: str, expiration: str, include_prices_history: bool = True) -> dict[str, Any]:
        normalized_symbol = self._normalize_symbol(symbol)
        if not normalized_symbol:
            self._mark_validation_warning("tradier", "invalid symbol")
            raise ValueError("invalid symbol")

        normalized_expiration, dte = self._parse_expiration(expiration)
        if not normalized_expiration or dte is None or dte < 0:
            self._mark_validation_warning("tradier", "invalid expiration")
            raise ValueError("invalid expiration")

        quote_task = asyncio.create_task(self.get_underlying_price(normalized_symbol))
        chain_task = asyncio.create_task(self._get_chain_with_health(normalized_symbol, expiration=normalized_expiration, greeks=True))
        vix_task = asyncio.create_task(self._get_vix_with_health())
        candles_task = asyncio.create_task(self.get_prices_history(normalized_symbol, lookback_days=365)) if include_prices_history else None

        if candles_task is not None:
            underlying_price, chain_raw, candles_raw, vix = await asyncio.gather(
                quote_task,
                chain_task,
                candles_task,
                vix_task,
                return_exceptions=True,
            )
        else:
            underlying_price, chain_raw, vix = await asyncio.gather(
                quote_task,
                chain_task,
                vix_task,
                return_exceptions=True,
            )
            candles_raw = []

        if isinstance(underlying_price, Exception):
            raise underlying_price
        if isinstance(chain_raw, Exception):
            raise chain_raw
        if isinstance(candles_raw, Exception):
            logger.warning(
                "event=analysis_candles_unavailable symbol=%s expiration=%s error=%s",
                normalized_symbol,
                normalized_expiration,
                str(candles_raw),
            )
            candles_raw = []
        if isinstance(vix, Exception):
            logger.warning(
                "event=analysis_vix_unavailable symbol=%s expiration=%s error=%s",
                normalized_symbol,
                normalized_expiration,
                str(vix),
            )
            vix = None

        closes = [float(x) for x in (candles_raw or []) if x is not None]
        contracts = self.normalize_chain(chain_raw)

        notes: list[str] = []
        if not closes:
            notes.append("history_unavailable: no close prices returned")
            logger.warning(
                "event=analysis_no_closes symbol=%s expiration=%s",
                normalized_symbol,
                normalized_expiration,
            )

        logger.info(
            "event=analysis_inputs_loaded symbol=%s expiration=%s contracts=%d closes=%d",
            normalized_symbol,
            normalized_expiration,
            len(contracts),
            len(closes),
        )

        return {
            "underlying_price": underlying_price,
            "contracts": contracts,
            "prices_history": closes,
            "vix": vix,
            "notes": notes,
        }
