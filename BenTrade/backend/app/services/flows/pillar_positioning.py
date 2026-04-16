"""Pillar 1 — Positioning (COT-only in Phase 1).

Scores non-commercial (speculator) net positioning across 5 CME/CBOE
futures:  ES, NQ, VX, ZN, ZB.  Each symbol produces one sub-signal; the
pillar is the mean of sub-signals that resolved.

Methodology (per sub-signal):
  1. Fetch ~14 months of weekly COT reports (56 weeks observed live).
  2. Compute non-commercial net = noncommLong - noncommShort for every
     week (CFTC field names `noncommPositionsLongAll` /
     `noncommPositionsShortAll`).
  3. Compute a 52-week rolling z-score of the latest week's net:
         z = (latest_net - mean(history)) / stdev(history)
     Requires >= 26 weeks of history; otherwise the sub-signal is
     skipped with reason_code INSUFFICIENT_HISTORY.
  4. Clip z to +/- 3.0 and divide by 3.0 to land in [-1, 1].
  5. Apply sign convention (see below), then emit as SubSignal.

Sign convention (risk-on-positive axis):
  All five sub-signals use the same formula `score = -(clipped_z / 3.0)`.
  For ES / NQ / ZN / ZB this is the contrarian framing:
      crowded non-commercial long = contrarian bearish = risk-off
      (bonds: flight-to-safety long build-up also = risk-off).
  For VX this is the direct framing:
      crowded non-commercial long on VIX futures = speculators
      positioning for rising vol = risk-off.
  The contrarian framing (equity/bond longs) and the direct framing
  (VIX longs) coincide in sign on a risk-on-positive axis, so no
  per-symbol sign flip is required. A positive sub-signal score
  always means "risk-on bias from this symbol."

Put/call and AAII sentiment are DEFERRED in Phase 1 — see docstring
TODO below.

Public API:
    async def build_positioning_pillar(fmp_client) -> PillarResult

Phase 1 pillar composition invariants:
    expected_count = 5  (ES, NQ, VX, ZN, ZB)
    min_subsignals = 3   (see contracts.PillarResult.from_subsignals)

TODO — sub-signals deferred in Phase 1:
    * Equity put/call ratio: Step-0 probes confirmed Finnhub 403,
      FMP /put-call-ratio 404, CBOE CSVs 403 (Incapsula). When an
      alternate data source is acquired (Alpha Vantage, Barchart,
      paid CBOE feed, etc.), add a new sub-signal via a helper
      analogous to `_cot_subsignal`. The pillar composition code
      (`PillarResult.from_subsignals`) already accepts new sub-signals
      without further change as long as `expected_count` is bumped.
    * AAII bull/bear spread: attempted scrape blocked by Incapsula
      at www.aaii.com. Defer until an alternate feed is identified.
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import date, datetime, timezone
from typing import Any

from app.clients.fmp_client import FMPClient
from app.services.flows.contracts import PillarResult, SubSignal
from app.services.flows.cot_fetch import (
    COT_SYMBOL_META,
    COT_SYMBOLS,
    cot_is_stale,
    fetch_all_cot_reports,
)

logger = logging.getLogger(__name__)


# ── Tunables ────────────────────────────────────────────────────────────

# Rolling window length (weeks) for the z-score.
ZSCORE_WINDOW_WEEKS: int = 52

# Minimum history below which z-score is not computed.
MIN_HISTORY_WEEKS: int = 26

# Clip z-scores to this absolute value before mapping to [-1, 1].
ZSCORE_CLIP: float = 3.0

# Phase-1 COT-only sub-signal count (one per symbol).
EXPECTED_COT_SUBSIGNALS: int = len(COT_SYMBOLS)

# Minimum sub-signals required for the pillar to produce a score.
MIN_COT_SUBSIGNALS: int = 3


# ── Sub-signal construction ─────────────────────────────────────────────

def _zscore(values: list[float]) -> float | None:
    """Z-score of the last value against the preceding window.

    Requires at least MIN_HISTORY_WEEKS observations (including the
    latest). Uses population stdev of the preceding window.
    """
    if len(values) < MIN_HISTORY_WEEKS:
        return None
    latest = values[-1]
    hist = values[-ZSCORE_WINDOW_WEEKS:-1] if len(values) > ZSCORE_WINDOW_WEEKS else values[:-1]
    if len(hist) < MIN_HISTORY_WEEKS - 1:
        return None
    mean_h = statistics.fmean(hist)
    try:
        stdev_h = statistics.pstdev(hist)
    except statistics.StatisticsError:
        return None
    if stdev_h == 0 or not math.isfinite(stdev_h):
        return None
    return (latest - mean_h) / stdev_h


def _cot_subsignal(symbol: str, rows: list[dict[str, Any]]) -> SubSignal:
    """Build a SubSignal for a single COT symbol.

    rows: normalized oldest-first list from `normalize_cot_records`.
    """
    name = f"cot_{symbol.lower()}_noncomm_net"
    meta = COT_SYMBOL_META.get(symbol, {})
    display = meta.get("name", symbol)

    if not rows:
        return SubSignal(
            name=name, score=None, raw_value=None,
            reason_code="COT_FETCH_EMPTY",
            detail=f"No COT rows returned for {display}.",
        )
    if cot_is_stale(rows):
        newest = rows[-1]["report_date"]
        return SubSignal(
            name=name, score=None, raw_value=None,
            reason_code="COT_STALE",
            detail=f"{display}: newest report {newest.isoformat()} exceeds 14-day freshness window.",
        )
    values = [float(r["net"]) for r in rows]
    if len(values) < MIN_HISTORY_WEEKS:
        return SubSignal(
            name=name, score=None, raw_value=None,
            reason_code="INSUFFICIENT_HISTORY",
            detail=f"{display}: {len(values)} weeks of history (need {MIN_HISTORY_WEEKS}).",
        )
    z = _zscore(values)
    if z is None:
        return SubSignal(
            name=name, score=None, raw_value=None,
            reason_code="ZSCORE_FAILED",
            detail=f"{display}: z-score could not be computed (zero stdev or invalid data).",
        )
    clipped = max(-ZSCORE_CLIP, min(ZSCORE_CLIP, z))
    # Unified formula across all 5 symbols on a risk-on-positive axis.
    # See module docstring for the contrarian (ES/NQ/ZN/ZB) vs
    # direct (VX) framing — both resolve to the same sign here.
    score = -(clipped / ZSCORE_CLIP)
    return SubSignal(
        name=name,
        score=score,
        raw_value=z,
        detail=f"{display}: z={z:+.2f} (latest net={int(values[-1]):,}, window={min(len(values)-1, ZSCORE_WINDOW_WEEKS)}w)",
    )


# ── Public entry point ─────────────────────────────────────────────────

async def build_positioning_pillar(
    fmp_client: FMPClient,
    *,
    today: date | None = None,
) -> PillarResult:
    """Fetch COT reports for all Phase-1 symbols and build the pillar."""
    today = today or datetime.now(timezone.utc).date()
    reports = await fetch_all_cot_reports(fmp_client, today=today)

    sub_signals: list[SubSignal] = []
    for sym in COT_SYMBOLS:
        rows = reports.get(sym, [])
        sub_signals.append(_cot_subsignal(sym, rows))

    available = sum(1 for s in sub_signals if s.score is not None)
    explanation = (
        f"COT non-commercial net positioning across "
        f"{', '.join(COT_SYMBOLS)} ({available} of {EXPECTED_COT_SUBSIGNALS} available). "
        f"Put/call and AAII sentiment deferred — see module TODO."
    )

    pillar = PillarResult.from_subsignals(
        name="positioning",
        sub_signals=sub_signals,
        expected_count=EXPECTED_COT_SUBSIGNALS,
        min_subsignals=MIN_COT_SUBSIGNALS,
        explanation=explanation,
    )
    logger.info(
        "event=positioning_pillar_built available=%d/%d score=%s confidence=%.3f status=%s",
        available, EXPECTED_COT_SUBSIGNALS,
        f"{pillar.score:+.3f}" if pillar.score is not None else "None",
        pillar.confidence, pillar.status,
    )
    return pillar


__all__ = [
    "build_positioning_pillar",
    "EXPECTED_COT_SUBSIGNALS",
    "MIN_COT_SUBSIGNALS",
    "ZSCORE_WINDOW_WEEKS",
    "MIN_HISTORY_WEEKS",
    "ZSCORE_CLIP",
]
