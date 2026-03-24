"""Shared data-quality utilities for all data providers."""

from __future__ import annotations

import logging
from datetime import date

from app.utils.market_hours import market_status

_log = logging.getLogger(__name__)

# Unified freshness vocabulary — matches confidence_framework.py FRESHNESS_PENALTIES.
# "delayed" is an intermediate tier added here for observation_date-based sources.
FRESHNESS_PENALTIES: dict[str, float] = {
    "live":       0.00,   # Real-time data during market hours
    "recent":     0.00,   # Within 1 trading day
    "delayed":    0.03,   # 2-3 calendar days (monthly series grace)
    "stale":      0.10,   # 4-7 calendar days
    "very_stale": 0.25,   # 8+ calendar days
    "unknown":    0.05,   # Can't determine freshness
}


def days_stale(observation_date_str: str | None) -> int | None:
    """Compute calendar-day staleness from an observation_date string.

    Returns the number of calendar days since the observation date,
    or None if the date string is missing/unparseable.
    """
    if not observation_date_str:
        return None
    try:
        obs = date.fromisoformat(str(observation_date_str)[:10])
        return (date.today() - obs).days
    except (ValueError, TypeError):
        return None


def staleness_tier(age_days: int | None) -> str:
    """Classify staleness into a tier for logging and confidence adjustment.

    Returns one of: "current", "acceptable", "stale", "very_stale", "unknown"
    """
    if age_days is None:
        return "unknown"
    if age_days <= 1:
        return "current"
    if age_days <= 3:
        return "acceptable"
    if age_days <= 7:
        return "stale"
    return "very_stale"


def staleness_confidence_penalty(age_days: int | None) -> float:
    """Return a confidence penalty (0.0 to 0.25) based on data staleness.

    Penalty table (calendar days):
      0-1 days:  0.00 (normal FRED lag)
      2-3 days:  0.03
      4-7 days:  0.08
      8-14 days: 0.15
      15+ days:  0.25
    """
    if age_days is None:
        return 0.05  # Unknown age gets a small penalty
    if age_days <= 1:
        return 0.0
    if age_days <= 3:
        return 0.03
    if age_days <= 7:
        return 0.08
    if age_days <= 14:
        return 0.15
    return 0.25


def compute_data_currency(
    *,
    observation_date: str | None = None,
    source_type: str = "unknown",
    is_market_open: bool | None = None,
) -> dict:
    """Compute unified data freshness tier and confidence penalty.

    Produces the confidence framework's vocabulary so all callers
    (MI Runner freshness section, Market Context Service metric envelopes,
    engine confidence) share one classification.

    Args:
        observation_date: ISO date string (YYYY-MM-DD) of market observation.
        source_type: "tradier", "fred", "fred_monthly", "finnhub", "polygon",
                     "derived", or "unknown".
        is_market_open: Override for market status.  ``None`` = auto-detect
                        via ``market_hours.market_status()``.

    Returns:
        ``{"tier": str, "penalty": float, "age_days": int|None,
          "source_type": str}``
    """
    if is_market_open is None:
        is_market_open = market_status() == "open"

    # Tradier intraday data: freshness depends on market hours
    if source_type == "tradier":
        if is_market_open:
            return {"tier": "live", "penalty": 0.00, "age_days": 0, "source_type": source_type}
        # Off-hours: Tradier data is from the last session
        return {"tier": "recent", "penalty": 0.00, "age_days": 0, "source_type": source_type}

    # FRED and other observation_date-based sources
    if observation_date:
        age = days_stale(observation_date)
        if age is None:
            return {"tier": "unknown", "penalty": 0.05, "age_days": None, "source_type": source_type}

        # Monthly series (like copper) tolerate more staleness
        is_monthly = source_type == "fred_monthly"

        if age <= 1:
            tier = "live" if is_market_open else "recent"
        elif age <= 3:
            tier = "recent"
        elif age <= 7:
            tier = "delayed" if is_monthly else "stale"
        elif age <= 14:
            tier = "stale"
        else:
            tier = "very_stale"

        return {
            "tier": tier,
            "penalty": FRESHNESS_PENALTIES.get(tier, 0.05),
            "age_days": age,
            "source_type": source_type,
        }

    # No observation_date available
    return {"tier": "unknown", "penalty": 0.05, "age_days": None, "source_type": source_type}


def extract_value(metric: dict | float | int | None) -> float | None:
    """Extract the numeric value from a metric envelope.

    Handles dict envelopes (``{"value": 25.1, "source": "tradier", ...}``),
    bare scalars, and ``None``.
    """
    if metric is None:
        return None
    if isinstance(metric, (int, float)):
        return float(metric)
    if isinstance(metric, dict):
        return metric.get("value")
    return None


def extract_quality(metric: dict | None, *, is_market_open: bool | None = None) -> dict:
    """Extract data-quality metadata from a metric envelope.

    Works alongside ``extract_value()`` — call both on the same metric.

    Returns:
        ``{"source": str, "tier": str, "penalty": float, "age_days": int|None,
          "is_proxy": bool, "observation_date": str|None}``
    """
    if not isinstance(metric, dict):
        return {
            "source": "unknown",
            "tier": "unknown",
            "penalty": 0.05,
            "age_days": None,
            "is_proxy": False,
            "observation_date": None,
        }

    source = metric.get("source", "unknown")
    obs_date = metric.get("observation_date")

    source_type = (
        "tradier" if source == "tradier"
        else "fred" if source and "fred" in source.lower()
        else "unknown"
    )
    currency = compute_data_currency(
        observation_date=obs_date,
        source_type=source_type,
        is_market_open=is_market_open,
    )

    return {
        "source": source,
        "tier": currency["tier"],
        "penalty": currency["penalty"],
        "age_days": currency["age_days"],
        "is_proxy": False,
        "observation_date": obs_date,
    }


def build_data_quality_summary(metrics: dict) -> dict:
    """Build a per-metric quality dict with an aggregate ``_summary``.

    *metrics* is the raw metric-envelope dict from MarketContextService
    (keys → envelope dicts).  Returns a dict of the same keys → quality
    dicts, plus a ``_summary`` key with aggregate stats.
    """
    quality: dict = {}
    for key, metric in metrics.items():
        quality[key] = extract_quality(metric)

    metric_count = len(quality)
    age_list = [q["age_days"] for q in quality.values() if q["age_days"] is not None]
    max_age = max(age_list) if age_list else None
    total_penalty = sum(q["penalty"] for q in quality.values())

    quality["_summary"] = {
        "max_age_days": max_age,
        "total_freshness_penalty": round(total_penalty, 3),
        "stale_count": sum(
            1 for q in quality.values()
            if isinstance(q, dict) and q.get("tier") in ("stale", "very_stale")
        ),
        "metric_count": metric_count,
    }
    return quality
