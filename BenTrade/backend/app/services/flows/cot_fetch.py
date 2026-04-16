"""Thin FMP COT fetcher for the Flows & Positioning engine (Phase 1).

FMP endpoint: `/stable/commitment-of-traders-report?symbol=<code>`.
Returns weekly CFTC records, newest-first, with full non-commercial /
commercial long/short breakdowns.

Data cadence: weekly (CFTC releases every Friday for the Tuesday cutoff).
TTL: 6 hours — new data appears at most once per week.

Symbols exercised in Phase 1: ES, NQ, VX, ZN, ZB. All confirmed live
returning 56 weeks of history (Step 0.5 probe).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.clients.fmp_client import FMPClient

logger = logging.getLogger(__name__)


# ── Symbol universe and display metadata ────────────────────────────────

COT_SYMBOLS: list[str] = ["ES", "NQ", "VX", "ZN", "ZB"]

COT_SYMBOL_META: dict[str, dict[str, str]] = {
    "ES": {"name": "S&P 500 E-Mini", "asset_class": "equity_index"},
    "NQ": {"name": "Nasdaq 100 E-Mini", "asset_class": "equity_index"},
    "VX": {"name": "CBOE VIX", "asset_class": "volatility"},
    "ZN": {"name": "10-Year T-Note", "asset_class": "rates"},
    "ZB": {"name": "30-Year T-Bond", "asset_class": "rates"},
}

# FMP COT TTL — 6 hours. Data only changes Friday afternoons; 6h gives
# ample intra-week cache reuse without missing the weekly refresh.
COT_CACHE_TTL_SECONDS: int = 6 * 60 * 60

# History depth — request ~14 months so a 52-week z-score window is
# always fully covered even after weekends / missing reports.
COT_LOOKBACK_DAYS: int = 420

# Staleness threshold — COT data older than this is treated as unusable
# (report is weekly; >14 days means we've missed two cycles).
COT_STALE_AFTER_DAYS: int = 14


# ── Record shape helpers ────────────────────────────────────────────────

def _parse_cot_date(raw: Any) -> date | None:
    """FMP COT dates arrive as 'YYYY-MM-DD HH:MM:SS' strings."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def extract_noncomm_net(record: dict[str, Any]) -> int | None:
    """Return non-commercial net positioning (long - short) for a row.

    Field source: FMP COT schema fields `noncommPositionsLongAll` and
    `noncommPositionsShortAll`. These are the canonical CFTC
    "non-commercial" (speculator) totals. Formula:
        net = noncommPositionsLongAll - noncommPositionsShortAll
    """
    long_ = record.get("noncommPositionsLongAll")
    short_ = record.get("noncommPositionsShortAll")
    if not isinstance(long_, (int, float)) or not isinstance(short_, (int, float)):
        return None
    return int(long_) - int(short_)


def normalize_cot_records(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize raw FMP rows, oldest-first, with derived `net` and
    parsed `report_date`. Drops rows with unparseable date or missing
    non-commercial fields.
    """
    if not raw:
        return []
    rows: list[dict[str, Any]] = []
    for row in raw:
        rd = _parse_cot_date(row.get("date"))
        net = extract_noncomm_net(row)
        if rd is None or net is None:
            continue
        rows.append({
            "report_date": rd,
            "net": net,
            "noncomm_long": int(row.get("noncommPositionsLongAll") or 0),
            "noncomm_short": int(row.get("noncommPositionsShortAll") or 0),
            "open_interest": row.get("openInterestAll"),
            "raw": row,
        })
    rows.sort(key=lambda r: r["report_date"])
    return rows


# ── Public fetcher ──────────────────────────────────────────────────────

async def fetch_cot_report(
    fmp: FMPClient,
    symbol: str,
    *,
    lookback_days: int = COT_LOOKBACK_DAYS,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Fetch CFTC COT report for a single symbol, normalized oldest-first.

    Uses `FMPClient._fetch` (which applies `FMPClient.cache` + rate
    limiting + 402 gating). A 6-hour TTL is passed via `ttl`.
    """
    if symbol not in COT_SYMBOL_META:
        logger.warning("cot_fetch: unknown COT symbol %s", symbol)
        return []
    today = today or datetime.now(timezone.utc).date()
    frm = (today - timedelta(days=lookback_days)).isoformat()
    to = today.isoformat()
    raw = await fmp._fetch(  # noqa: SLF001 — intentional, mirrors FMPClient pattern
        "/commitment-of-traders-report",
        params={"symbol": symbol, "from": frm, "to": to},
        ttl=COT_CACHE_TTL_SECONDS,
    )
    return normalize_cot_records(raw)


async def fetch_all_cot_reports(
    fmp: FMPClient,
    *,
    symbols: list[str] | None = None,
    today: date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch COT reports for all Phase-1 symbols in parallel.

    Returns `{symbol: normalized_rows}`. Symbols that failed return
    an empty list; callers distinguish this from "genuinely empty".
    """
    symbols = symbols or COT_SYMBOLS
    today = today or datetime.now(timezone.utc).date()
    tasks = [fetch_cot_report(fmp, s, today=today) for s in symbols]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: dict[str, list[dict[str, Any]]] = {}
    for sym, res in zip(symbols, results):
        if isinstance(res, BaseException):
            logger.warning("cot_fetch: %s failed: %s", sym, res)
            out[sym] = []
        else:
            out[sym] = res
    return out


def cot_is_stale(rows: list[dict[str, Any]], *, today: date | None = None) -> bool:
    """True if the most-recent report is older than COT_STALE_AFTER_DAYS."""
    if not rows:
        return True
    today = today or datetime.now(timezone.utc).date()
    newest: date = rows[-1]["report_date"]
    return (today - newest).days > COT_STALE_AFTER_DAYS


__all__ = [
    "COT_SYMBOLS",
    "COT_SYMBOL_META",
    "COT_CACHE_TTL_SECONDS",
    "COT_LOOKBACK_DAYS",
    "COT_STALE_AFTER_DAYS",
    "fetch_cot_report",
    "fetch_all_cot_reports",
    "normalize_cot_records",
    "extract_noncomm_net",
    "cot_is_stale",
]
