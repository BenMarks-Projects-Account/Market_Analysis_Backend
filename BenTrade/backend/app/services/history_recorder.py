"""BenTrade decision history recorder — fail-safe write helpers.

Single service used by MI publish + stock/options runners to persist
``market_state_snapshots`` and ``decisions`` rows. Nothing in this module
raises to the caller: a null ``session_maker`` is a silent no-op (history
DB unavailable per Step 2 failure policy), and any write exception is
logged with ``event=history_record_failed`` and swallowed.

Public API
----------
* :func:`log_market_snapshot` — one row per MI artifact publish
* :func:`log_decision`        — one row per LLM-evaluated candidate
* :func:`make_decision_id`    — stable, human-readable decision id

Private helpers
---------------
Kept module-private (underscore prefix) and covered by the unit test in
``tests/test_history_recorder.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Decision, MarketStateSnapshot
from app.db.tracking_config import STOCK_TRACKING_WINDOWS

_LOG = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────

LLM_REASONING_MAX_CHARS: int = 500

# NYSE holidays 2024-2030. Source: NYSE published schedule. Extend as
# years pass; ``numpy.busday_offset`` will raise if asked about a date
# beyond the known range so we fail loud rather than silently wrong.
# Format: YYYY-MM-DD strings consumable directly by numpy.
US_MARKET_HOLIDAYS: tuple[str, ...] = (
    # 2024
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29",
    "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02",
    "2024-11-28", "2024-12-25",
    # 2025
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17",
    "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
    "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
    "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
    # 2028
    "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29",
    "2028-06-19", "2028-07-04", "2028-09-04", "2028-11-23",
    "2028-12-25",
    # 2029
    "2029-01-01", "2029-01-15", "2029-02-19", "2029-03-30",
    "2029-05-28", "2029-06-19", "2029-07-04", "2029-09-03",
    "2029-11-22", "2029-12-25",
    # 2030
    "2030-01-01", "2030-01-21", "2030-02-18", "2030-04-19",
    "2030-05-27", "2030-06-19", "2030-07-04", "2030-09-02",
    "2030-11-28", "2030-12-25",
)

# Pre-build the numpy BusinessDayCalendar once at import (cheap).
_NYSE_BUSDAYCAL: np.busdaycalendar = np.busdaycalendar(
    weekmask="1111100",  # Mon-Fri
    holidays=US_MARKET_HOLIDAYS,
)

# ─── Utilities ────────────────────────────────────────────────────────


def _utc_iso_now() -> str:
    """Timezone-aware ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


_ID_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def _slugify(s: str) -> str:
    """Filesystem/id-safe lowercase slug."""
    return _ID_SLUG_RE.sub("_", s).strip("_").lower()


def make_decision_id(
    decision_type: str,
    timestamp_utc: str,
    candidate_key: str,
) -> str:
    """Build a stable, human-readable decision id.

    Shape: ``{type}_{YYYYMMDDHHMMSS}_{slug}_{hash8}``

    ``hash8`` is the first 8 hex chars of a SHA-256 over the three inputs,
    giving collision resistance even if two candidates share the same key
    within the same second.
    """
    # Compact the ISO timestamp: 2026-04-16T18:00:00.123+00:00 → 20260416180000
    try:
        ts = datetime.fromisoformat(timestamp_utc)
        compact_ts = ts.strftime("%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        compact_ts = _slugify(timestamp_utc)[:14]

    slug = _slugify(candidate_key)[:40] or "unknown"
    digest = hashlib.sha256(
        f"{decision_type}|{timestamp_utc}|{candidate_key}".encode("utf-8")
    ).hexdigest()[:8]
    return f"{_slugify(decision_type)}_{compact_ts}_{slug}_{digest}"


def _truncate_reasoning(text: Optional[str]) -> Optional[str]:
    """Collapse whitespace and clip LLM reasoning to the capture limit."""
    if text is None:
        return None
    s = " ".join(str(text).split())
    if len(s) <= LLM_REASONING_MAX_CHARS:
        return s
    return s[: LLM_REASONING_MAX_CHARS - 1] + "…"


# ─── Tracking window math ─────────────────────────────────────────────


def _parse_expiration(candidate: Mapping[str, Any]) -> Optional[date]:
    """Extract the options expiration date as a ``date`` object.

    Candidates carry expiration in one of: ``expiration``, ``exp``,
    ``expiration_date`` as ISO ``YYYY-MM-DD`` text.
    """
    for key in ("expiration", "exp", "expiration_date", "expiry"):
        raw = candidate.get(key)
        if not raw:
            continue
        if isinstance(raw, date):
            return raw
        try:
            return datetime.fromisoformat(str(raw)[:10]).date()
        except (ValueError, TypeError):
            continue
    return None


def _business_days_between(start: date, end: date) -> int:
    """Count NYSE business days strictly between ``start`` and ``end``.

    Returns 0 if ``end <= start`` (same-day or expired candidate). Uses
    numpy's busday machinery with the NYSE holiday calendar.
    """
    if end <= start:
        return 0
    # numpy.busday_count: half-open [start, end); we want exclusive of
    # start, inclusive of end's trading day → same half-open interval
    # shifted by +1 day on both sides works, but simpler: count[start, end)
    # gives the number of business days reachable, which is what we store.
    count = int(
        np.busday_count(
            start.isoformat(),
            end.isoformat(),
            busdaycal=_NYSE_BUSDAYCAL,
        )
    )
    return max(count, 0)


def _compute_tracking_window_days(
    candidate: Mapping[str, Any],
    decision_type: str,
) -> int:
    """Strategy-aware tracking window length in trading days.

    * ``options``: business days between today and expiration. If the
      expiration cannot be parsed, fall back to 10 with a warning.
    * ``stock``: ``STOCK_TRACKING_WINDOWS[scanner_key]`` with log-and-
      fallback when the key is missing or unknown.
    * anything else: 10 (conservative default).
    """
    if decision_type == "options":
        exp = _parse_expiration(candidate)
        if exp is None:
            _LOG.warning(
                "event=tracking_window_unknown_exp symbol=%s candidate_keys=%s",
                candidate.get("symbol"),
                sorted(candidate.keys()),
            )
            return 10
        days = _business_days_between(date.today(), exp)
        if days <= 0:
            _LOG.warning(
                "event=tracking_window_expired symbol=%s exp=%s",
                candidate.get("symbol"),
                exp.isoformat(),
            )
            return 1
        return days

    if decision_type == "stock":
        key = (
            candidate.get("scanner_key")
            or candidate.get("strategy")
            or candidate.get("strategy_id")
        )
        if key in STOCK_TRACKING_WINDOWS:
            return STOCK_TRACKING_WINDOWS[key]
        _LOG.warning(
            "event=tracking_window_unknown_strategy key=%s symbol=%s",
            key,
            candidate.get("symbol"),
        )
        return 10

    return 10


def _compute_tracking_ends_utc(
    start_utc: str,
    window_days: int,
) -> str:
    """Add ``window_days`` NYSE business days to ``start_utc``.

    Returns an ISO-8601 UTC timestamp. The wall-clock time component of
    ``start_utc`` is preserved (we only shift the date).
    """
    try:
        start_dt = datetime.fromisoformat(start_utc)
    except (ValueError, TypeError):
        start_dt = datetime.now(timezone.utc)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)

    # numpy shifts a date. We want the same HH:MM:SS back after shifting.
    shifted = np.busday_offset(
        start_dt.date().isoformat(),
        window_days,
        roll="forward",
        busdaycal=_NYSE_BUSDAYCAL,
    )
    end_date = datetime.strptime(str(shifted), "%Y-%m-%d").date()
    end_dt = datetime(
        end_date.year,
        end_date.month,
        end_date.day,
        start_dt.hour,
        start_dt.minute,
        start_dt.second,
        tzinfo=start_dt.tzinfo,
    )
    return end_dt.isoformat()


# ─── Market snapshot field extraction ─────────────────────────────────


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None  # filter NaN
    except (TypeError, ValueError):
        return None


def _get(obj: Any, *path: str, default: Any = None) -> Any:
    """Walk a dict path safely; return default on any missing key."""
    cur = obj
    for part in path:
        if not isinstance(cur, Mapping):
            return default
        cur = cur.get(part)
        if cur is None:
            return default
    return cur if cur is not None else default


def _build_flows_pillar_json(flows_engine: Any) -> Optional[str]:
    """Extract the flows pillar breakdown per Step 0.5 locked shape.

    Stored as JSON text: ``{"scores": {...}, "full": {...}, "status": ...}``.

    * ``scores`` — pillar_scores dict (already the right shape in the
      engine output contract).
    * ``full``   — the complete detail_sections / supporting_metrics
      slice for later forensic queries.
    * ``status`` — engine source_status (ok / degraded / missing / unknown).
    """
    if not isinstance(flows_engine, Mapping):
        return None
    scores = flows_engine.get("pillar_scores") or flows_engine.get("scores")
    full = {
        "detail_sections": flows_engine.get("detail_sections"),
        "supporting_metrics": flows_engine.get("supporting_metrics"),
        "contradiction_flags": flows_engine.get("contradiction_flags"),
    }
    status = (
        flows_engine.get("source_status")
        or flows_engine.get("engine_status")
        or "unknown"
    )
    payload = {"scores": scores, "full": full, "status": status}
    try:
        return json.dumps(payload, default=str, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        _LOG.warning("event=flows_pillar_json_failed error=%r", exc)
        return None


def _extract_snapshot_fields(market_state: Mapping[str, Any]) -> dict[str, Any]:
    """Pull the columnar fields out of the canonical market_state artifact.

    Shape reference: ``app/workflows/market_state_contract.py`` §4.

    Key notes
    ---------
    * ``composite.market_state`` → regime (lowercase_underscore)
    * ``composite.confidence`` is 0–1; we store as 0–100
    * engines carry ``confidence`` (0–100) and ``score`` (0–100) directly
    * macro metrics live under ``market_snapshot.metrics.<key>.value``
    """
    engines = market_state.get("engines") if isinstance(market_state, Mapping) else None
    engines = engines if isinstance(engines, Mapping) else {}

    composite_conf = _safe_float(_get(market_state, "composite", "confidence"))
    if composite_conf is not None and composite_conf <= 1.0:
        composite_conf = composite_conf * 100.0

    def _engine(key: str) -> Mapping[str, Any]:
        e = engines.get(key)
        return e if isinstance(e, Mapping) else {}

    breadth = _engine("breadth_participation")
    vol = _engine("volatility_options")
    cross = _engine("cross_asset_macro")
    flows = _engine("flows_positioning")
    liquidity = _engine("liquidity_financial_conditions")
    news = _engine("news_sentiment")

    fields: dict[str, Any] = {
        "regime": _get(market_state, "composite", "market_state"),
        "regime_confidence": composite_conf,
        "breadth_score": _safe_float(breadth.get("score")),
        "breadth_confidence": _safe_float(breadth.get("confidence")),
        "volatility_score": _safe_float(vol.get("score")),
        "volatility_confidence": _safe_float(vol.get("confidence")),
        "cross_asset_score": _safe_float(cross.get("score")),
        "cross_asset_confidence": _safe_float(cross.get("confidence")),
        "flows_score": _safe_float(flows.get("score")),
        "flows_confidence": _safe_float(flows.get("confidence")),
        "liquidity_score": _safe_float(liquidity.get("score")),
        "liquidity_confidence": _safe_float(liquidity.get("confidence")),
        "news_sentiment_score": _safe_float(news.get("score")),
        "news_sentiment_confidence": _safe_float(news.get("confidence")),
        "vix": _safe_float(_get(market_state, "market_snapshot", "metrics", "vix", "value")),
        "us10y": _safe_float(
            _get(market_state, "market_snapshot", "metrics", "ten_year_yield", "value")
        ),
        "spy_close": _safe_float(
            _get(market_state, "market_snapshot", "metrics", "spy_close", "value")
        ),
        "flows_pillar_json": _build_flows_pillar_json(flows),
    }
    return fields


# ─── Hypothetical P&L extraction ──────────────────────────────────────

_CONTRACT_MULTIPLIER: float = 100.0  # standard US equity options


def _extract_hypothetical_options(
    candidate: Mapping[str, Any],
) -> dict[str, Optional[float]]:
    """Entry / max-profit / max-loss for an options candidate.

    Strategy-aware mapping:

    * credit spreads (``put_credit_spread``, ``call_credit_spread``,
      ``iron_condor``, ``iron_butterfly``): entry = net credit, max_profit
      = credit × 100, max_loss = (width - credit) × 100.
    * debit spreads (``put_debit``, ``call_debit``, ``butterfly_debit``,
      ``calendar_*``, ``diagonal_*``): entry = net debit, max_profit = (width
      - debit) × 100 or candidate-provided, max_loss = debit × 100.

    Always prefer explicit candidate fields (``max_profit``,
    ``max_loss``) when present — callers may have computed them from
    chain data with better precision than our reconstruction.
    """
    strategy = str(candidate.get("strategy_id") or candidate.get("strategy") or "").lower()

    # Pull per-share / per-contract fields defensively
    credit = _safe_float(candidate.get("net_credit"))
    if credit is None:
        credit = _safe_float(candidate.get("credit"))
    debit = _safe_float(candidate.get("net_debit"))
    if debit is None:
        debit = _safe_float(candidate.get("debit"))
    width = _safe_float(candidate.get("width"))

    # Prefer explicit dollar totals if the candidate has them
    explicit_mp = _safe_float(candidate.get("max_profit"))
    explicit_ml = _safe_float(candidate.get("max_loss"))

    mp_per_share = _safe_float(candidate.get("max_profit_per_share"))
    ml_per_share = _safe_float(candidate.get("max_loss_per_share"))

    is_credit_strategy = any(
        tag in strategy
        for tag in ("credit_spread", "iron_condor", "iron_butterfly")
    )
    is_debit_strategy = any(
        tag in strategy
        for tag in ("_debit", "calendar_", "diagonal_", "butterfly_debit")
    )

    entry: Optional[float]
    if is_credit_strategy:
        entry = credit
        mp = explicit_mp
        if mp is None and mp_per_share is not None:
            mp = mp_per_share * _CONTRACT_MULTIPLIER
        if mp is None and credit is not None:
            mp = credit * _CONTRACT_MULTIPLIER

        ml = explicit_ml
        if ml is None and ml_per_share is not None:
            ml = ml_per_share * _CONTRACT_MULTIPLIER
        if ml is None and width is not None and credit is not None:
            ml = max(width - credit, 0.0) * _CONTRACT_MULTIPLIER
    elif is_debit_strategy:
        entry = debit
        mp = explicit_mp
        if mp is None and mp_per_share is not None:
            mp = mp_per_share * _CONTRACT_MULTIPLIER
        if mp is None and width is not None and debit is not None:
            mp = max(width - debit, 0.0) * _CONTRACT_MULTIPLIER

        ml = explicit_ml
        if ml is None and ml_per_share is not None:
            ml = ml_per_share * _CONTRACT_MULTIPLIER
        if ml is None and debit is not None:
            ml = debit * _CONTRACT_MULTIPLIER
    else:
        # Unknown strategy — use whatever explicit fields we have, no guess
        entry = credit if credit is not None else debit
        mp = explicit_mp
        if mp is None and mp_per_share is not None:
            mp = mp_per_share * _CONTRACT_MULTIPLIER
        ml = explicit_ml
        if ml is None and ml_per_share is not None:
            ml = ml_per_share * _CONTRACT_MULTIPLIER

    return {
        "hypothetical_entry_price": entry,
        "hypothetical_max_profit": mp,
        "hypothetical_max_loss": ml,
    }


def _extract_hypothetical_stock(
    candidate: Mapping[str, Any],
    market_state: Optional[Mapping[str, Any]] = None,
) -> dict[str, Optional[float]]:
    """Entry + bounded downside for a stock candidate.

    * entry = candidate close / current / price — or fall back to the
      SPY close in market_state (never ideal; we log a warning).
    * max_profit = None  (stocks are open-ended)
    * max_loss  = candidate stop_loss distance × share count when the
      candidate declares an explicit stop, else None.
    """
    entry = _safe_float(
        candidate.get("current_price")
        or candidate.get("close")
        or candidate.get("price")
        or candidate.get("entry_price")
    )
    if entry is None and market_state is not None:
        entry = _safe_float(
            _get(market_state, "market_snapshot", "metrics", "spy_close", "value")
        )
        if entry is not None:
            _LOG.warning(
                "event=stock_entry_price_fallback symbol=%s using=spy_close",
                candidate.get("symbol"),
            )

    stop = _safe_float(candidate.get("stop_loss") or candidate.get("stop_price"))
    max_loss: Optional[float] = None
    if stop is not None and entry is not None and stop < entry:
        max_loss = entry - stop  # per-share dollar loss

    return {
        "hypothetical_entry_price": entry,
        "hypothetical_max_profit": None,
        "hypothetical_max_loss": max_loss,
    }


# ─── Public API ───────────────────────────────────────────────────────


async def log_market_snapshot(
    *,
    session_maker: Optional[async_sessionmaker[AsyncSession]],
    snapshot_id: str,
    market_state: Mapping[str, Any],
    captured_at_utc: str,
    artifact_filename: Optional[str] = None,
) -> None:
    """Persist one ``market_state_snapshots`` row.

    Fail-safe: returns silently if ``session_maker`` is None (history DB
    disabled/unavailable) or if any write error occurs.
    """
    if session_maker is None:
        return
    try:
        fields = _extract_snapshot_fields(market_state)
        row = MarketStateSnapshot(
            snapshot_id=snapshot_id,
            captured_at_utc=captured_at_utc,
            artifact_filename=artifact_filename,
            created_at_utc=_utc_iso_now(),
            **fields,
        )
        async with session_maker() as session:
            session.add(row)
            await session.commit()
        _LOG.info(
            "event=history_snapshot_recorded snapshot_id=%s regime=%s",
            snapshot_id,
            fields.get("regime"),
        )
    except Exception as exc:  # noqa: BLE001 — fail-safe
        _LOG.error(
            "event=history_record_failed type=snapshot snapshot_id=%s error=%r",
            snapshot_id,
            exc,
        )


async def log_decision(
    *,
    session_maker: Optional[async_sessionmaker[AsyncSession]],
    decision_type: str,
    candidate: Mapping[str, Any],
    recommendation: str,
    run_id: str,
    workflow_id: str,
    snapshot_id: Optional[str] = None,
    market_state: Optional[Mapping[str, Any]] = None,
    model_score: Optional[float] = None,
    deterministic_rank: Optional[int] = None,
    rank: Optional[int] = None,
    llm_reasoning: Optional[str] = None,
    timestamp_utc: Optional[str] = None,
) -> Optional[str]:
    """Persist one ``decisions`` row.

    Returns the generated ``decision_id`` on success, or ``None`` when
    the history DB is disabled / unreachable (silent no-op per the
    fail-safe policy).

    ``decision_type`` must be ``"options"`` or ``"stock"``. Anything else
    is accepted but hypothetical P&L will not be computed.
    """
    if session_maker is None:
        return None
    try:
        now_utc = timestamp_utc or _utc_iso_now()
        symbol = str(candidate.get("symbol") or "UNKNOWN")
        strategy_id = candidate.get("strategy_id") or candidate.get("strategy")
        scanner_key = candidate.get("scanner_key")
        expiration = candidate.get("expiration") or candidate.get("exp")

        # Build a stable candidate key for id hashing
        candidate_key_parts = [symbol, str(strategy_id or ""), str(expiration or "")]
        if decision_type == "options":
            short = candidate.get("short_strike") or candidate.get("short")
            long_ = candidate.get("long_strike") or candidate.get("long")
            candidate_key_parts.extend([str(short or ""), str(long_ or "")])
        candidate_key = ":".join(p for p in candidate_key_parts if p)

        decision_id = make_decision_id(decision_type, now_utc, candidate_key)

        # Hypothetical P&L
        if decision_type == "options":
            hypo = _extract_hypothetical_options(candidate)
        elif decision_type == "stock":
            hypo = _extract_hypothetical_stock(candidate, market_state=market_state)
        else:
            hypo = {
                "hypothetical_entry_price": None,
                "hypothetical_max_profit": None,
                "hypothetical_max_loss": None,
            }

        # Tracking window
        window_days = _compute_tracking_window_days(candidate, decision_type)
        tracking_ends_utc = _compute_tracking_ends_utc(now_utc, window_days)

        # Candidate JSON — keep it bounded
        try:
            candidate_json = json.dumps(dict(candidate), default=str, separators=(",", ":"))
        except (TypeError, ValueError):
            candidate_json = None

        row = Decision(
            decision_id=decision_id,
            run_id=run_id,
            workflow_id=workflow_id,
            snapshot_id=snapshot_id,
            timestamp_utc=now_utc,
            symbol=symbol,
            strategy_id=strategy_id,
            scanner_key=scanner_key,
            recommendation=recommendation,
            model_score=_safe_float(model_score),
            deterministic_rank=deterministic_rank,
            rank=rank,
            expiration=str(expiration) if expiration else None,
            candidate_json=candidate_json,
            llm_reasoning=_truncate_reasoning(llm_reasoning),
            hypothetical_entry_price=hypo["hypothetical_entry_price"],
            hypothetical_max_profit=hypo["hypothetical_max_profit"],
            hypothetical_max_loss=hypo["hypothetical_max_loss"],
            tracking_window_days=window_days,
            tracking_ends_utc=tracking_ends_utc,
            tracking_status="active",
            created_at_utc=_utc_iso_now(),
        )
        async with session_maker() as session:
            session.add(row)
            await session.commit()
        _LOG.info(
            "event=history_decision_recorded decision_id=%s type=%s symbol=%s "
            "recommendation=%s window_days=%s",
            decision_id,
            decision_type,
            symbol,
            recommendation,
            window_days,
        )
        return decision_id
    except Exception as exc:  # noqa: BLE001 — fail-safe
        _LOG.error(
            "event=history_record_failed type=decision symbol=%s error=%r",
            candidate.get("symbol") if isinstance(candidate, Mapping) else None,
            exc,
        )
        return None


__all__ = [
    "LLM_REASONING_MAX_CHARS",
    "US_MARKET_HOLIDAYS",
    "log_decision",
    "log_market_snapshot",
    "make_decision_id",
]
