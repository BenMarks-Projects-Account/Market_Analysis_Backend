"""BenTrade — Stock Engine Service

Aggregates results from ALL four stock strategy scanners into a single
ranked list and returns the top N candidates.

Ranking priority (descending):
  1. composite_score  (0–100, strategy-specific, primary sort key)
  2. model recommendation strength  (BUY=3 > HOLD=2 > PASS/SELL=1 > N/A=0)
  3. avg_dollar_volume  (liquidity proxy, descending)
  4. symbol + strategy_id  (deterministic tie-breaker)

The composite_score is the canonical "single score" produced by each
stock scanner service (PullbackSwing, MomentumBreakout, MeanReversion,
VolatilityExpansion).  Each is a 0–100 weighted composite of
strategy-specific sub-scores.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────
TOP_N = 9

# Model recommendation → numeric rank for sorting.
# BUY > HOLD > PASS/SELL > unknown/absent.
_REC_RANK = {
    "STRONG_BUY": 4,
    "BUY": 3,
    "HOLD": 2,
    "PASS": 1,
    "SELL": 1,
}


def _rec_sort_value(candidate: dict[str, Any]) -> int:
    """Extract a numeric recommendation rank from a candidate.

    Looks for model_evaluation.recommendation (string).
    Falls back to 0 if absent or unrecognized.
    """
    model = candidate.get("model_evaluation")
    if not model or not isinstance(model, dict):
        return 0
    rec = str(model.get("recommendation") or "").strip().upper()
    return _REC_RANK.get(rec, 0)


def get_scanner_score(candidate: dict[str, Any]) -> float:
    """Return the canonical scanner score (0-100) for ranking.

    This is the SAME field displayed as "Score XX%" on the trade card.
    Field: composite_score (numeric, 0-100).
    Handles: None, missing key, strings, 0.
    """
    raw = candidate.get("composite_score")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 0.0


def _sort_key(candidate: dict[str, Any]) -> tuple:
    """Build a deterministic sort key for ranking candidates.

    Order: composite_score DESC, rec_rank DESC, avg_dollar_volume DESC,
           symbol ASC, strategy_id ASC.
    Negate numeric fields for descending sort.
    """
    score = get_scanner_score(candidate)
    rec_rank = _rec_sort_value(candidate)
    avg_dol_vol = float(
        (candidate.get("metrics") or {}).get("avg_dollar_volume") or 0
    )
    symbol = str(candidate.get("symbol") or "").upper()
    strategy_id = str(candidate.get("strategy_id") or "")
    # Negate for descending; strings sort ascending for tie-break.
    return (-score, -rec_rank, -avg_dol_vol, symbol, strategy_id)


class StockEngineService:
    """Runs all stock scanners in parallel and returns the top N.

    The global TradierClient rate limiter (2 req/sec) prevents API overload
    regardless of concurrent callers; parallelism lets scanners overlap
    computation and share the TTLCache simultaneously.
    """

    def __init__(
        self,
        pullback_swing_service,
        momentum_breakout_service,
        mean_reversion_service,
        volatility_expansion_service,
    ):
        self._scanners = {
            "stock_pullback_swing": pullback_swing_service,
            "stock_momentum_breakout": momentum_breakout_service,
            "stock_mean_reversion": mean_reversion_service,
            "stock_volatility_expansion": volatility_expansion_service,
        }

    async def scan(self, top_n: int = TOP_N) -> dict[str, Any]:
        """Run all stock scanners, aggregate, rank, and return top N.

        Returns the same shape as individual scanner responses but with
        candidates aggregated across all scanners plus engine-level metadata.

        If one scanner fails, the others still contribute.  Partial failures
        are reported in ``warnings``.

        Scanners run in PARALLEL via asyncio.gather.  The global
        TradierClient rate limiter (2 req/sec, asyncio.Lock-gated)
        prevents API overload regardless of concurrent callers.
        All scanners share the same TTLCache (1800s bars), so cache
        hits improve when multiple scanners request the same symbols
        simultaneously.
        """
        t0 = time.monotonic()
        all_candidates: list[dict[str, Any]] = []
        scanner_meta: list[dict[str, Any]] = []
        warnings: list[str] = []

        # Build tasks for all initialised scanners
        active: list[tuple[str, Any]] = []
        for sid, svc in self._scanners.items():
            if svc is None:
                warnings.append(f"{sid}: service not initialised")
                scanner_meta.append({
                    "strategy_id": sid,
                    "status": "skipped",
                    "candidates_count": 0,
                })
            else:
                active.append((sid, svc))

        # Dispatch all scanners in parallel — rate limiter handles throttling
        tasks = [svc.scan() for _, svc in active]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for (sid, _svc), result in zip(active, results):
            if isinstance(result, BaseException):
                msg = f"{sid}: {str(result)[:200]}"
                logger.exception(
                    "event=stock_engine_scanner_error scanner=%s", sid,
                    exc_info=result,
                )
                warnings.append(msg)
                scanner_meta.append({
                    "strategy_id": sid,
                    "status": "error",
                    "error": str(result)[:200],
                    "candidates_count": 0,
                })
                continue

            if not isinstance(result, dict):
                warnings.append(
                    f"{sid}: unexpected result type {type(result).__name__}"
                )
                scanner_meta.append({
                    "strategy_id": sid,
                    "status": "error",
                    "candidates_count": 0,
                })
                continue

            status = result.get("status", "unknown")
            candidates = result.get("candidates") or []
            max_score = max(
                (get_scanner_score(c) for c in candidates),
                default=0.0,
            )
            scanner_meta.append({
                "strategy_id": sid,
                "status": status,
                "candidates_count": len(candidates),
                "max_composite_score": round(max_score, 1),
            })
            all_candidates.extend(candidates)
            logger.info(
                "event=stock_engine_scanner_done scanner=%s "
                "candidates=%d max_score=%.1f",
                sid, len(candidates), max_score,
            )

        # ── Sort & select top N ──
        all_candidates.sort(key=_sort_key)
        top = all_candidates[:top_n]

        elapsed = round(time.monotonic() - t0, 2)

        # ── Diagnostic logging ──
        scanner_summary = ", ".join(
            f"{m['strategy_id']}={m['candidates_count']}c"
            f"(max={m.get('max_composite_score', '?')})"
            for m in scanner_meta
        )
        top_preview = ", ".join(
            f"{c.get('symbol')}@{get_scanner_score(c):.1f}"
            f"({c.get('strategy_id', '?')})"
            for c in top[:15]
        )
        logger.info(
            "event=stock_engine_aggregation "
            "total_candidates=%d top_n=%d elapsed=%.2fs "
            "scanners=[%s] top=[%s]",
            len(all_candidates), top_n, elapsed,
            scanner_summary, top_preview,
        )

        return {
            "engine": "stock_engine",
            "status": "ok" if not warnings else "partial",
            "as_of": _iso_now(),
            "top_n": top_n,
            "total_candidates": len(all_candidates),
            "candidates": top,
            "scanners": scanner_meta,
            "warnings": warnings,
            "scan_time_seconds": elapsed,
        }


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
