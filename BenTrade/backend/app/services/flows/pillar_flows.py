"""Pillar 2 — Flows (sector relative strength + NAV creation overlay).

Phase 1 sub-signals (5):
  1. `rs_risk_on_rotation_20d`: mean(offensive sector RS) - mean(defensive sector RS)
  2. `rs_cyclicals_vs_staples_20d`: XLY RS - XLP RS
  3. `rs_tech_leadership_20d`: XLK RS vs SPY
  4. `rs_credit_flow_hyg_tlt_20d`: HYG return - TLT return (credit risk appetite)
  5. `nav_sector_creation_overlay`: WoW change in offensive vs defensive
     sector ETF shares-outstanding (derived = AUM / NAV). Deferred with
     reason_code `NAV_HISTORY_BUILDING` until >= 8 weekly snapshots exist.

RS definition: `ret_symbol_20d - ret_spy_20d` using adjusted closes (FMP
`get_historical_price_eod` returns `close` which is adj-close at the
`/historical-price-eod/full` endpoint). 20-trading-day lookback.

Sign convention (risk-on-positive axis):
  All 4 RS sub-signals use `score = +(clipped_z / 3.0)`. Positive RS
  means offense / cyclicals / tech / credit is OUTPERFORMING =
  risk-on. No inversion needed.

Pillar-level history gate:
  rs_history.jsonl accumulates daily snapshots (deduped by date). Until
  it has >= 60 observations, the pillar returns an `INSUFFICIENT_HISTORY`
  PillarResult regardless of today's data, because z-scoring against
  <60 samples is not meaningful. Current raw RS values are still
  computed and appended so the history builds forward. Sub-signals in
  that mode carry the same reason code and include their raw_value.

NAV overlay sub-signal:
  shares_outstanding.jsonl accumulates weekly snapshots (deduped by
  ISO week key) of derived shares-outstanding per sector ETF. Until
  >= 8 distinct weekly snapshots exist, the overlay sub-signal emits
  score=None with reason_code `NAV_HISTORY_BUILDING`. Pillar-level
  history gate (60 daily RS obs) can still pass the other 4 RS
  sub-signals even when NAV overlay is still building.

History file paths (created lazily on first run):
  BenTrade/backend/data/flows/rs_history.jsonl
  BenTrade/backend/data/flows/shares_outstanding.jsonl

TODO:
  * Consider adding a breadth-adjacent sub-signal (advance/decline
    line or sector participation) once Phase 1 stabilises.
  * shares_outstanding field sharesOutstanding is not directly provided
    by FMP /etf/info; we derive as AUM / NAV. Confirmed in Step 0
    discovery: /etf/info does not include sharesOutstanding directly.
"""
from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.clients.fmp_client import FMPClient
from app.services.flows.contracts import PillarResult, SubSignal

logger = logging.getLogger(__name__)


# ── Tunables ────────────────────────────────────────────────────────────

RS_LOOKBACK_DAYS: int = 20
RS_HISTORY_MIN_OBS: int = 60
RS_ZSCORE_WINDOW: int = 60
RS_ZSCORE_CLIP: float = 3.0

NAV_HISTORY_MIN_WEEKS: int = 8
NAV_WOW_WINDOW_WEEKS: int = 8

BARS_LOOKBACK_DAYS: int = 90  # plenty for 20d returns

# Sector ETF universe (SPDR Select Sector).
OFFENSIVE_SECTORS: list[str] = ["XLK", "XLY", "XLC", "XLF", "XLI"]
DEFENSIVE_SECTORS: list[str] = ["XLP", "XLU", "XLV"]
BENCHMARK: str = "SPY"
CREDIT_HY: str = "HYG"
CREDIT_TSY: str = "TLT"

ALL_RS_SYMBOLS: list[str] = [
    BENCHMARK, CREDIT_HY, CREDIT_TSY, *OFFENSIVE_SECTORS, *DEFENSIVE_SECTORS
]

NAV_SYMBOLS: list[str] = OFFENSIVE_SECTORS + DEFENSIVE_SECTORS

# History paths (relative to backend/ data dir).
_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "flows"
RS_HISTORY_PATH: Path = _DATA_DIR / "rs_history.jsonl"
SHARES_OUTSTANDING_PATH: Path = _DATA_DIR / "shares_outstanding.jsonl"

EXPECTED_FLOWS_SUBSIGNALS: int = 5
MIN_FLOWS_SUBSIGNALS: int = 3

# Bootstrap tunables — see `bootstrap_rs_history` docstring.
BOOTSTRAP_TARGET_OBSERVATIONS: int = 252
BOOTSTRAP_LOOKBACK_BUFFER: int = RS_LOOKBACK_DAYS  # 20 trading days
# ~252 trading days ≈ 365 calendar days. Add the 20-day lookback buffer
# (≈ 30 cal) plus a 30-day safety margin for holidays/weekends.
BOOTSTRAP_FETCH_CALENDAR_DAYS: int = 365 + 30 + 30
BOOTSTRAP_SLOW_WARN_SECONDS: float = 60.0


# ── Return helpers ─────────────────────────────────────────────────────

def _pct_return(bars: list[dict[str, Any]], lookback: int) -> float | None:
    """Simple price return from `lookback` bars ago to latest."""
    if not bars or len(bars) <= lookback:
        return None
    try:
        latest = float(bars[-1]["close"])
        prior = float(bars[-1 - lookback]["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if prior <= 0:
        return None
    return latest / prior - 1.0


def _compute_rs_snapshot(bars_by_symbol: dict[str, list[dict[str, Any]]]) -> dict[str, float | None]:
    """Compute the 4 RS quantities from today's bars.

    Returns a dict with keys matching rs_history.jsonl row schema.
    Values may be None if bars are missing.
    """
    spy_ret = _pct_return(bars_by_symbol.get(BENCHMARK, []), RS_LOOKBACK_DAYS)

    def rs_vs_spy(sym: str) -> float | None:
        r = _pct_return(bars_by_symbol.get(sym, []), RS_LOOKBACK_DAYS)
        if r is None or spy_ret is None:
            return None
        return r - spy_ret

    offense_rs = [rs_vs_spy(s) for s in OFFENSIVE_SECTORS]
    defense_rs = [rs_vs_spy(s) for s in DEFENSIVE_SECTORS]
    offense_rs_clean = [x for x in offense_rs if x is not None]
    defense_rs_clean = [x for x in defense_rs if x is not None]

    risk_on_rotation = None
    if offense_rs_clean and defense_rs_clean:
        risk_on_rotation = statistics.fmean(offense_rs_clean) - statistics.fmean(defense_rs_clean)

    cyc_vs_stap = None
    xly_rs, xlp_rs = rs_vs_spy("XLY"), rs_vs_spy("XLP")
    if xly_rs is not None and xlp_rs is not None:
        cyc_vs_stap = xly_rs - xlp_rs

    tech_lead = rs_vs_spy("XLK")

    hyg_ret = _pct_return(bars_by_symbol.get(CREDIT_HY, []), RS_LOOKBACK_DAYS)
    tlt_ret = _pct_return(bars_by_symbol.get(CREDIT_TSY, []), RS_LOOKBACK_DAYS)
    credit_flow = None
    if hyg_ret is not None and tlt_ret is not None:
        credit_flow = hyg_ret - tlt_ret

    return {
        "risk_on_rotation_20d": risk_on_rotation,
        "cyclicals_vs_staples_20d": cyc_vs_stap,
        "tech_leadership_20d": tech_lead,
        "credit_flow_hyg_tlt_20d": credit_flow,
    }


def _compute_rs_at(
    closes_by_symbol: dict[str, dict[str, float]],
    eval_date: str,
    lookback_date: str,
) -> dict[str, float | None]:
    """Point-in-time variant of `_compute_rs_snapshot`.

    Uses closes at `eval_date` (T) and `lookback_date` (T-20 trading
    days). Formula matches the live pipeline exactly — no future-info
    leakage, bootstrap rows are indistinguishable from forward-built
    rows by design.
    """
    def ret(sym: str) -> float | None:
        closes = closes_by_symbol.get(sym, {})
        latest = closes.get(eval_date)
        prior = closes.get(lookback_date)
        if not isinstance(latest, (int, float)) or not isinstance(prior, (int, float)):
            return None
        if prior <= 0:
            return None
        return latest / prior - 1.0

    spy_ret = ret(BENCHMARK)

    def rs_vs_spy(sym: str) -> float | None:
        r = ret(sym)
        if r is None or spy_ret is None:
            return None
        return r - spy_ret

    offense = [rs_vs_spy(s) for s in OFFENSIVE_SECTORS]
    defense = [rs_vs_spy(s) for s in DEFENSIVE_SECTORS]
    off_c = [x for x in offense if x is not None]
    def_c = [x for x in defense if x is not None]
    risk_on_rotation = (
        statistics.fmean(off_c) - statistics.fmean(def_c)
        if off_c and def_c else None
    )

    xly_rs, xlp_rs = rs_vs_spy("XLY"), rs_vs_spy("XLP")
    cyc_vs_stap = (xly_rs - xlp_rs) if (xly_rs is not None and xlp_rs is not None) else None

    tech_lead = rs_vs_spy("XLK")

    hyg_ret, tlt_ret = ret(CREDIT_HY), ret(CREDIT_TSY)
    credit_flow = (
        hyg_ret - tlt_ret
        if (hyg_ret is not None and tlt_ret is not None) else None
    )

    return {
        "risk_on_rotation_20d": risk_on_rotation,
        "cyclicals_vs_staples_20d": cyc_vs_stap,
        "tech_leadership_20d": tech_lead,
        "credit_flow_hyg_tlt_20d": credit_flow,
    }


# ── History I/O (JSONL, deduped append) ────────────────────────────────

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def _append_rs_snapshot_if_new(
    snapshot: dict[str, float | None],
    *,
    today: date,
) -> tuple[list[dict[str, Any]], bool]:
    """Append today's RS snapshot to history if not already present.
    Returns (full_history_including_today, appended_flag).
    """
    history = _read_jsonl(RS_HISTORY_PATH)
    iso = today.isoformat()
    existing_dates = {r.get("date") for r in history}
    appended = False
    if iso not in existing_dates:
        row = {"date": iso, **{k: v for k, v in snapshot.items()}}
        _append_jsonl(RS_HISTORY_PATH, row)
        history.append(row)
        appended = True
    history.sort(key=lambda r: r.get("date", ""))
    return history, appended


def _iso_week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _append_shares_snapshot_if_new(
    shares_by_symbol: dict[str, float | None],
    *,
    today: date,
) -> tuple[list[dict[str, Any]], bool]:
    """Append shares-outstanding snapshot keyed by ISO week."""
    history = _read_jsonl(SHARES_OUTSTANDING_PATH)
    wk = _iso_week_key(today)
    existing_weeks = {r.get("iso_week") for r in history}
    appended = False
    if wk not in existing_weeks:
        row = {
            "iso_week": wk,
            "date": today.isoformat(),
            "shares": {k: v for k, v in shares_by_symbol.items()},
        }
        _append_jsonl(SHARES_OUTSTANDING_PATH, row)
        history.append(row)
        appended = True
    history.sort(key=lambda r: r.get("iso_week", ""))
    return history, appended


# ── Z-score helper ──────────────────────────────────────────────────────

def _zscore_against_history(values: list[float]) -> float | None:
    """Z-score of the last value against the preceding window."""
    if len(values) < RS_HISTORY_MIN_OBS:
        return None
    latest = values[-1]
    window = values[-RS_ZSCORE_WINDOW:-1] if len(values) > RS_ZSCORE_WINDOW else values[:-1]
    if len(window) < RS_HISTORY_MIN_OBS - 1:
        return None
    mean_w = statistics.fmean(window)
    try:
        stdev_w = statistics.pstdev(window)
    except statistics.StatisticsError:
        return None
    if stdev_w == 0 or not math.isfinite(stdev_w):
        return None
    return (latest - mean_w) / stdev_w


# ── NAV overlay ─────────────────────────────────────────────────────────

async def _fetch_etf_shares_outstanding(
    fmp: FMPClient, symbol: str
) -> float | None:
    """shares_outstanding = AUM / NAV (FMP /etf/info does not expose
    sharesOutstanding directly; see module TODO)."""
    info = await fmp._fetch("/etf/info", params={"symbol": symbol}, ttl=6 * 3600)
    if not info:
        return None
    rec = info[0] if isinstance(info, list) else info
    if not isinstance(rec, dict):
        return None
    aum = rec.get("assetsUnderManagement")
    nav = rec.get("nav")
    if not isinstance(aum, (int, float)) or not isinstance(nav, (int, float)):
        return None
    if nav <= 0:
        return None
    return float(aum) / float(nav)


async def _fetch_all_shares(fmp: FMPClient) -> dict[str, float | None]:
    import asyncio
    tasks = [_fetch_etf_shares_outstanding(fmp, s) for s in NAV_SYMBOLS]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: dict[str, float | None] = {}
    for sym, res in zip(NAV_SYMBOLS, results):
        out[sym] = None if isinstance(res, BaseException) else res
    return out


def _build_nav_overlay_subsignal(
    shares_history: list[dict[str, Any]],
) -> SubSignal:
    """Build NAV overlay sub-signal. Returns None with
    NAV_HISTORY_BUILDING until >= 8 weekly snapshots exist.
    """
    name = "nav_sector_creation_overlay"
    if len(shares_history) < NAV_HISTORY_MIN_WEEKS:
        return SubSignal(
            name=name, score=None, raw_value=None,
            reason_code="NAV_HISTORY_BUILDING",
            detail=f"Have {len(shares_history)} weekly snapshots; need {NAV_HISTORY_MIN_WEEKS}.",
        )
    # Compute WoW % change per symbol across last N weeks, then aggregate
    recent = shares_history[-NAV_WOW_WINDOW_WEEKS:]
    if len(recent) < 2:
        return SubSignal(
            name=name, score=None, raw_value=None,
            reason_code="NAV_HISTORY_BUILDING",
            detail="Insufficient consecutive-week snapshots for WoW change.",
        )
    latest_shares = recent[-1].get("shares", {}) or {}
    prior_shares = recent[-2].get("shares", {}) or {}

    def pct_change(sym: str) -> float | None:
        cur = latest_shares.get(sym)
        prv = prior_shares.get(sym)
        if not isinstance(cur, (int, float)) or not isinstance(prv, (int, float)) or prv <= 0:
            return None
        return cur / prv - 1.0

    offense_changes = [pct_change(s) for s in OFFENSIVE_SECTORS]
    defense_changes = [pct_change(s) for s in DEFENSIVE_SECTORS]
    off_clean = [x for x in offense_changes if x is not None]
    def_clean = [x for x in defense_changes if x is not None]
    if not off_clean or not def_clean:
        return SubSignal(
            name=name, score=None, raw_value=None,
            reason_code="NAV_SHARES_MISSING",
            detail="Could not compute WoW share changes for offensive/defensive groups.",
        )
    off_mean = statistics.fmean(off_clean)
    def_mean = statistics.fmean(def_clean)
    creation_delta = off_mean - def_mean  # positive = offense getting created faster

    # Clip raw to roughly +/- 2% WoW spread and map linearly to [-1, 1]
    CLIP = 0.02
    clipped = max(-CLIP, min(CLIP, creation_delta))
    score = clipped / CLIP  # positive = risk-on
    return SubSignal(
        name=name,
        score=score,
        raw_value=creation_delta,
        detail=(
            f"Offense WoW SO change {off_mean:+.3%} vs defense {def_mean:+.3%} "
            f"(delta {creation_delta:+.3%})."
        ),
    )


# ── Public entry point ─────────────────────────────────────────────────

async def bootstrap_rs_history(fmp_client: FMPClient) -> int:
    """Populate `rs_history.jsonl` with ~252 trading days of computed RS rows.

    Idempotent: if the file already has >= RS_HISTORY_MIN_OBS (60) rows,
    this is a no-op. If the file is missing or below threshold, we fetch
    ~272 days of bars for all 11 RS symbols, align on common trading
    dates across symbols, drop the earliest 20 dates (no 20d lookback
    available), and compute one RS row per remaining date using
    `_compute_rs_at` (same formula as the live path).

    Rules:
      * Dates missing from any symbol's bars are skipped — no forward-fill.
      * Bootstrap does NOT touch `shares_outstanding.jsonl`.
      * Writes atomically via a tmp file then replace. If the destination
        already has >= 60 rows when we finish fetching (concurrent run),
        we skip the write and log a notice.
      * Returns the final count of rows written.
    """
    import asyncio
    import time

    history = _read_jsonl(RS_HISTORY_PATH)
    if len(history) >= RS_HISTORY_MIN_OBS:
        return len(history)

    t0 = time.monotonic()
    logger.info(
        "event=rs_history_bootstrap_started existing_rows=%d target=%d",
        len(history), BOOTSTRAP_TARGET_OBSERVATIONS,
    )

    today = datetime.now(timezone.utc).date()
    from_date = (today - timedelta(days=BOOTSTRAP_FETCH_CALENDAR_DAYS)).isoformat()
    to_date = today.isoformat()

    tasks = [
        fmp_client.get_historical_price_eod(s, from_date=from_date, to_date=to_date)
        for s in ALL_RS_SYMBOLS
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    closes_by_symbol: dict[str, dict[str, float]] = {}
    for sym, res in zip(ALL_RS_SYMBOLS, results):
        if isinstance(res, BaseException) or not res:
            logger.warning("bootstrap: %s bars missing (%s)", sym, res if isinstance(res, BaseException) else "empty")
            closes_by_symbol[sym] = {}
            continue
        closes_by_symbol[sym] = {
            b["date"]: float(b["close"]) for b in res
            if isinstance(b.get("close"), (int, float))
        }

    # Dates present for ALL symbols (no forward-fill).
    per_sym_date_sets = [set(c.keys()) for c in closes_by_symbol.values() if c]
    if not per_sym_date_sets:
        logger.warning("event=rs_history_bootstrap_aborted reason=no_data")
        return len(history)
    common_dates = sorted(set.intersection(*per_sym_date_sets))
    if len(common_dates) <= BOOTSTRAP_LOOKBACK_BUFFER:
        logger.warning(
            "event=rs_history_bootstrap_aborted reason=insufficient_common_dates count=%d",
            len(common_dates),
        )
        return len(history)

    # For each date from index LOOKBACK_BUFFER onward, the prior date is
    # at `common_dates[i - LOOKBACK_BUFFER]`. Take the most recent
    # BOOTSTRAP_TARGET_OBSERVATIONS of those.
    usable = common_dates[BOOTSTRAP_LOOKBACK_BUFFER:]
    bootstrap_dates = usable[-BOOTSTRAP_TARGET_OBSERVATIONS:]

    rows: list[dict[str, Any]] = []
    for d in bootstrap_dates:
        idx = common_dates.index(d)
        prior = common_dates[idx - BOOTSTRAP_LOOKBACK_BUFFER]
        values = _compute_rs_at(closes_by_symbol, d, prior)
        # Skip rows where ALL four values are None (unusable).
        if all(v is None for v in values.values()):
            continue
        rows.append({"date": d, **values})

    # Re-read once more in case a concurrent run raced us.
    current = _read_jsonl(RS_HISTORY_PATH)
    if len(current) >= RS_HISTORY_MIN_OBS:
        elapsed = time.monotonic() - t0
        logger.info(
            "event=rs_history_bootstrap_skipped reason=raced existing=%d elapsed=%.2fs",
            len(current), elapsed,
        )
        return len(current)

    # Atomic write.
    RS_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RS_HISTORY_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    tmp.replace(RS_HISTORY_PATH)

    elapsed = time.monotonic() - t0
    if elapsed > BOOTSTRAP_SLOW_WARN_SECONDS:
        logger.warning(
            "event=rs_history_bootstrap_slow elapsed=%.2fs observations=%d",
            elapsed, len(rows),
        )
    logger.info(
        "event=rs_history_bootstrap_complete observations=%d elapsed=%.2fs",
        len(rows), elapsed,
    )
    return len(rows)


async def build_flows_pillar(
    fmp_client: FMPClient,
    *,
    today: date | None = None,
) -> PillarResult:
    import asyncio
    today = today or datetime.now(timezone.utc).date()

    # Auto-bootstrap on first run (idempotent — no-op if already populated).
    await bootstrap_rs_history(fmp_client)

    # 1. Fetch bars for all RS symbols (parallel)
    bar_tasks = [fmp_client.get_historical_price_eod(s) for s in ALL_RS_SYMBOLS]
    bar_results = await asyncio.gather(*bar_tasks, return_exceptions=True)
    bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for sym, res in zip(ALL_RS_SYMBOLS, bar_results):
        if isinstance(res, BaseException) or not res:
            bars_by_symbol[sym] = []
        else:
            bars_by_symbol[sym] = res

    # 2. Compute today's RS snapshot
    snapshot = _compute_rs_snapshot(bars_by_symbol)
    rs_history, rs_appended = _append_rs_snapshot_if_new(snapshot, today=today)
    logger.info(
        "event=flows_rs_snapshot appended=%s history_size=%d values=%s",
        rs_appended, len(rs_history),
        {k: round(v, 5) if isinstance(v, float) else v for k, v in snapshot.items()},
    )

    # 3. Fetch + append shares-outstanding snapshot (weekly dedupe)
    shares_by_symbol = await _fetch_all_shares(fmp_client)
    shares_history, shares_appended = _append_shares_snapshot_if_new(
        shares_by_symbol, today=today
    )
    logger.info(
        "event=flows_shares_snapshot appended=%s weekly_snapshots=%d",
        shares_appended, len(shares_history),
    )

    # 4. Pillar-level history gate
    if len(rs_history) < RS_HISTORY_MIN_OBS:
        # Return a pillar-level INSUFFICIENT_HISTORY result. Still include
        # current raw values in sub_signals for transparency.
        sub_signals = [
            SubSignal(
                name=f"rs_{k}",
                score=None,
                raw_value=v,
                reason_code="INSUFFICIENT_HISTORY",
                detail=f"{k} current raw={v} — waiting for {RS_HISTORY_MIN_OBS} daily obs.",
            )
            for k, v in snapshot.items()
        ]
        sub_signals.append(_build_nav_overlay_subsignal(shares_history))
        return PillarResult(
            name="flows",
            score=None,
            confidence=0.0,
            status="unavailable",
            sub_signals=tuple(sub_signals),
            available_count=0,
            expected_count=EXPECTED_FLOWS_SUBSIGNALS,
            explanation=(
                f"RS history building: have {len(rs_history)} daily snapshots, "
                f"need {RS_HISTORY_MIN_OBS}. Raw RS values are captured live."
            ),
            reason_code="INSUFFICIENT_HISTORY",
        )

    # 5. Z-score each RS series
    sub_signals: list[SubSignal] = []
    for key in ["risk_on_rotation_20d", "cyclicals_vs_staples_20d",
                "tech_leadership_20d", "credit_flow_hyg_tlt_20d"]:
        series = [float(r[key]) for r in rs_history if isinstance(r.get(key), (int, float))]
        latest = snapshot.get(key)
        name = f"rs_{key}"
        if latest is None or len(series) < RS_HISTORY_MIN_OBS:
            sub_signals.append(SubSignal(
                name=name, score=None, raw_value=latest,
                reason_code="RS_COMPUTE_FAILED" if latest is None else "INSUFFICIENT_HISTORY",
                detail=f"{key}: raw={latest}, series_len={len(series)}",
            ))
            continue
        z = _zscore_against_history(series)
        if z is None:
            sub_signals.append(SubSignal(
                name=name, score=None, raw_value=latest,
                reason_code="ZSCORE_FAILED",
                detail=f"{key}: z-score computation failed.",
            ))
            continue
        clipped = max(-RS_ZSCORE_CLIP, min(RS_ZSCORE_CLIP, z))
        # Positive RS → risk-on, no sign inversion
        score = +(clipped / RS_ZSCORE_CLIP)
        sub_signals.append(SubSignal(
            name=name, score=score, raw_value=z,
            detail=f"{key}: z={z:+.2f} (latest raw={latest:+.4f}, window={min(len(series)-1, RS_ZSCORE_WINDOW)}d)",
        ))

    # 6. NAV overlay sub-signal
    sub_signals.append(_build_nav_overlay_subsignal(shares_history))

    explanation = (
        f"Sector RS vs SPY (offense/defense rotation, cyclicals/staples, tech, "
        f"credit HYG/TLT) z-scored over {RS_HISTORY_MIN_OBS}d history "
        f"({len(rs_history)} obs). NAV creation overlay: {len(shares_history)} "
        f"weekly snapshots (need {NAV_HISTORY_MIN_WEEKS})."
    )
    pillar = PillarResult.from_subsignals(
        name="flows",
        sub_signals=sub_signals,
        expected_count=EXPECTED_FLOWS_SUBSIGNALS,
        min_subsignals=MIN_FLOWS_SUBSIGNALS,
        explanation=explanation,
    )
    logger.info(
        "event=flows_pillar_built available=%d/%d score=%s confidence=%.3f status=%s",
        pillar.available_count, pillar.expected_count,
        f"{pillar.score:+.3f}" if pillar.score is not None else "None",
        pillar.confidence, pillar.status,
    )
    return pillar


__all__ = [
    "build_flows_pillar",
    "bootstrap_rs_history",
    "EXPECTED_FLOWS_SUBSIGNALS",
    "MIN_FLOWS_SUBSIGNALS",
    "RS_HISTORY_MIN_OBS",
    "NAV_HISTORY_MIN_WEEKS",
    "RS_HISTORY_PATH",
    "SHARES_OUTSTANDING_PATH",
    "OFFENSIVE_SECTORS",
    "DEFENSIVE_SECTORS",
]
