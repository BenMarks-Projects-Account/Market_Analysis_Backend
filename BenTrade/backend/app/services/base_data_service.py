import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.clients.finnhub_client import FinnhubClient
from app.clients.fred_client import FredClient
from app.clients.tradier_client import TradierClient
from app.clients.yahoo_client import YahooClient
from app.models.schemas import OptionContract
from app.utils.http import UpstreamError

logger = logging.getLogger(__name__)


class BaseDataService:
    def __init__(
        self,
        tradier_client: TradierClient,
        finnhub_client: FinnhubClient,
        yahoo_client: YahooClient,
        fred_client: FredClient,
    ) -> None:
        self.tradier_client = tradier_client
        self.finnhub_client = finnhub_client
        self.yahoo_client = yahoo_client
        self.fred_client = fred_client
        self._source_health: dict[str, dict[str, Any]] = {
            "finnhub": self._new_source_state(),
            "yahoo": self._new_source_state(),
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
        if key == "yahoo":
            return True
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

        await asyncio.gather(
            _probe("tradier", self.tradier_client.health),
            _probe("finnhub", self.finnhub_client.health),
            _probe("yahoo", self.yahoo_client.health),
            _probe("fred", self.fred_client.health),
            return_exceptions=True,
        )

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
                message = "not configured"
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
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_iv(value: float | None) -> float | None:
        if value is None:
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
            expiration = str(row.get("expiration_date") or row.get("expiration") or "")
            if strike is None or not expiration:
                continue

            normalized.append(
                OptionContract(
                    option_type=raw_type,
                    strike=strike,
                    expiration=expiration,
                    bid=self._to_float(row.get("bid")),
                    ask=self._to_float(row.get("ask")),
                    open_interest=self._to_int(row.get("open_interest")),
                    volume=self._to_int(row.get("volume")),
                    delta=self._to_float(greeks.get("delta")) if isinstance(greeks, dict) else None,
                    iv=self._normalize_iv(iv),
                    symbol=row.get("symbol"),
                )
            )
        return normalized

    async def get_underlying_price(self, symbol: str) -> float | None:
        try:
            quote = await self.tradier_client.get_quote(symbol)
            self._mark_success("tradier", http_status=200, message="quote ok")
        except Exception as exc:
            self._mark_failure("tradier", exc)
            quote = {}

        for field in ("last", "close", "mark", "bid", "ask"):
            value = self._to_float(quote.get(field))
            if value is not None:
                return value

        logger.info("event=underlying_fallback symbol=%s source=finnhub", symbol.upper())
        try:
            fb = await self.finnhub_client.get_quote(symbol)
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
        underlying_task = asyncio.create_task(self.get_underlying_price(symbol))
        vix_task = asyncio.create_task(self.fred_client.get_latest_series_value())
        candles_task = asyncio.create_task(self.get_prices_history(symbol, lookback_days=365))

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

        return {
            "symbol": symbol.upper(),
            "underlying_price": underlying_price,
            "vix": vix,
            "prices_history": closes[-160:],
        }

    async def get_prices_history(self, symbol: str, lookback_days: int = 365) -> list[float]:
        try:
            closes = await self.yahoo_client.get_daily_closes(symbol, period="1y")
            if closes:
                self._mark_success("yahoo", http_status=200, message="history ok")
                return [float(x) for x in closes if x is not None]
            self._mark_failure("yahoo", UpstreamError("Yahoo returned empty history", details={"status_code": 429}))
            logger.warning("event=prices_history_empty symbol=%s source=yahoo", symbol.upper())
        except Exception as exc:
            self._mark_failure("yahoo", exc)
            logger.warning(
                "event=prices_history_unavailable symbol=%s source=yahoo error=%s",
                symbol.upper(),
                str(exc),
            )

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=lookback_days)
        try:
            fallback = await self.tradier_client.get_daily_closes(
                symbol,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
            self._mark_success("tradier", http_status=200, message="history fallback ok")
            return [float(x) for x in fallback if x is not None]
        except Exception as exc:
            self._mark_failure("tradier", exc)
            logger.warning(
                "event=prices_history_unavailable symbol=%s source=tradier error=%s",
                symbol.upper(),
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
        quote_task = asyncio.create_task(self.get_underlying_price(symbol))
        chain_task = asyncio.create_task(self._get_chain_with_health(symbol, expiration=expiration, greeks=True))
        vix_task = asyncio.create_task(self._get_vix_with_health())
        candles_task = asyncio.create_task(self.get_prices_history(symbol, lookback_days=365)) if include_prices_history else None

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
                symbol.upper(),
                expiration,
                str(candles_raw),
            )
            candles_raw = []
        if isinstance(vix, Exception):
            logger.warning(
                "event=analysis_vix_unavailable symbol=%s expiration=%s error=%s",
                symbol.upper(),
                expiration,
                str(vix),
            )
            vix = None

        closes = [float(x) for x in (candles_raw or []) if x is not None]
        contracts = self.normalize_chain(chain_raw)

        logger.info(
            "event=analysis_inputs_loaded symbol=%s expiration=%s contracts=%d closes=%d",
            symbol.upper(),
            expiration,
            len(contracts),
            len(closes),
        )

        return {
            "underlying_price": underlying_price,
            "contracts": contracts,
            "prices_history": closes,
            "vix": vix,
        }
