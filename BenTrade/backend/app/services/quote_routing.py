"""Quote routing: Tradier primary (batch) + FMP fallback (sequential).

Provides a single ``get_batch_quotes()`` async function used by the breadth
engine and pre-market movers endpoint.  The caller does not need to know
which upstream supplied the data — quotes from either source use the same
normalized shape and populate the same cache.

Routing logic:
  1. Check Tradier health.  If known-red for >60 s, skip to FMP.
  2. Attempt Tradier batch (50-symbol chunks).  One attempt per chunk.
  3. On any Tradier failure, fall back to FMP sequential for the
     missing symbols (rate-limited via ``FMPClient._rate_limiter``).
  4. Partial success: Tradier supplies some, FMP fills gaps → ``mixed``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Maximum symbols per Tradier /markets/quotes call.
_TRADIER_BATCH_SIZE = 50

# If Tradier health was red this many seconds ago, skip directly to FMP.
_HEALTH_RED_GRACE_SECONDS = 60

# Concurrency cap for FMP sequential fallback.
_FMP_CONCURRENCY = 20


async def get_batch_quotes(
    tradier_client: Any,
    fmp_client: Any,
    symbols: list[str],
    *,
    _tradier_health_cache: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch quotes for *symbols*.  Tradier primary, FMP fallback.

    Returns ``{SYMBOL: {price, last, close, volume, change, …}, …}``.
    Symbols for which neither source returns data are omitted.

    *_tradier_health_cache* is a mutable dict shared across calls to
    track Tradier's last-known health state between invocations.  The
    caller may pass ``{}``; keys ``healthy`` (bool) and ``checked_at``
    (float, monotonic) are managed internally.
    """
    if _tradier_health_cache is None:
        _tradier_health_cache = {}

    all_quotes: dict[str, dict[str, Any]] = {}
    source = "tradier"
    tradier_failure_reason: str | None = None

    # ── 1. Decide whether to try Tradier ──────────────────────
    skip_tradier = False
    if tradier_client is None:
        skip_tradier = True
        tradier_failure_reason = "tradier_client_not_configured"
    else:
        last_healthy = _tradier_health_cache.get("healthy")
        last_checked = _tradier_health_cache.get("checked_at", 0.0)
        if last_healthy is False and (time.monotonic() - last_checked) < _HEALTH_RED_GRACE_SECONDS:
            skip_tradier = True
            tradier_failure_reason = "tradier_health_red"
            logger.info(
                "event=quote_routing_skip_tradier reason=health_red "
                "red_for_s=%.0f",
                time.monotonic() - last_checked,
            )

    # ── 2. Try Tradier batch ──────────────────────────────────
    if not skip_tradier:
        try:
            for i in range(0, len(symbols), _TRADIER_BATCH_SIZE):
                chunk = symbols[i : i + _TRADIER_BATCH_SIZE]
                batch_result = await tradier_client.get_quotes(chunk)
                if isinstance(batch_result, dict):
                    all_quotes.update(batch_result)
            # Mark Tradier healthy
            _tradier_health_cache["healthy"] = True
            _tradier_health_cache["checked_at"] = time.monotonic()
        except Exception as exc:
            tradier_failure_reason = f"tradier_error:{type(exc).__name__}:{exc}"
            logger.warning(
                "event=quote_routing_tradier_failed reason=%s",
                tradier_failure_reason,
            )
            # Mark Tradier unhealthy
            _tradier_health_cache["healthy"] = False
            _tradier_health_cache["checked_at"] = time.monotonic()

    # ── 3. Check for missing symbols → FMP fallback ───────────
    missing = [s for s in symbols if s not in all_quotes]

    if missing and fmp_client is not None and fmp_client.is_available():
        if tradier_failure_reason and not all_quotes:
            # Total Tradier failure → all symbols via FMP
            source = "fmp"
        elif tradier_failure_reason or missing:
            source = "mixed" if all_quotes else "fmp"

        sem = asyncio.Semaphore(_FMP_CONCURRENCY)

        async def _fmp_fetch(sym: str) -> tuple[str, dict[str, Any] | None]:
            async with sem:
                try:
                    return sym, await fmp_client.get_quote(sym)
                except Exception as exc:
                    logger.debug(
                        "event=quote_routing_fmp_fetch_failed symbol=%s error=%s",
                        sym, exc,
                    )
                    return sym, None

        fmp_results = await asyncio.gather(
            *[_fmp_fetch(s) for s in missing],
            return_exceptions=True,
        )
        fmp_count = 0
        for result in fmp_results:
            if isinstance(result, Exception):
                continue
            sym, quote = result
            if quote is not None:
                all_quotes[sym] = quote
                fmp_count += 1

        if fmp_count > 0 and source == "tradier":
            source = "mixed"

        logger.info(
            "event=quote_routing_fmp_fallback symbols_requested=%d "
            "symbols_filled=%d reason=%s",
            len(missing), fmp_count, tradier_failure_reason or "partial_miss",
        )

    # ── 4. Log routing summary ────────────────────────────────
    logger.info(
        "event=quote_routing_complete source=%s total_requested=%d "
        "total_returned=%d",
        source, len(symbols), len(all_quotes),
    )

    return all_quotes
