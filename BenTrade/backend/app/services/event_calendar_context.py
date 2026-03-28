"""
Event / Macro Calendar Context v1.1
======================================

Reusable event-context layer that summarizes upcoming scheduled macro
and company-event risk so policy logic and higher-order trade decisions
can account for catalyst timing explicitly.

This module makes **deterministic, auditable** assessments — no LLM
calls, no opaque scoring.  It answers:

    "What known catalysts are close enough to matter, and how do they
     overlap with a candidate trade or the current portfolio?"

Inputs (all optional, partial-data-safe)
----------------------------------------
- ``macro_events``   – list of upcoming macro-event dicts
- ``company_events`` – list of upcoming company/earnings-event dicts
- ``candidate``      – normalized candidate dict (for overlap detection)
- ``positions``      – list of current portfolio position dicts (for
                       portfolio-wide event clustering)
- ``reference_time`` – datetime anchor for time-to-event computation
                       (defaults to UTC now)

Output
------
``build_event_context(...)`` returns::

    {
        "event_context_version":  "1.1",
        "generated_at":           ISO-8601,
        "status":                 "ok" | "partial" | "no_data",
        "summary":                str,
        "event_risk_state":       "quiet" | "elevated" | "crowded" | "unknown",

        "upcoming_macro_events":  list[EventItem],
        "upcoming_company_events": list[EventItem],

        "candidate_event_overlap": {
            "candidate_symbol": str | None,
            "overlapping_events": list[EventItem],
            "overlap_count": int,
        },
        "portfolio_event_overlap": {
            "positions_with_overlap": int,
            "symbols_with_overlap": list[str],
            "overlapping_events": list[EventItem],
            "event_cluster_count": int,
        },

        "event_windows": {
            "within_24h": list[EventItem],
            "within_3d":  list[EventItem],
            "within_7d":  list[EventItem],
            "beyond_7d":  list[EventItem],
        },

        "risk_flags":    list[str],
        "warning_flags": list[str],
        "evidence":      dict,
        "metadata":      dict,
    }

Each ``EventItem`` has::

    {
        "event_type":      str,   # "macro" | "earnings" | "dividend" |
                                  # "fed_speak" | "company" | "other"
        "event_name":      str,   # e.g. "CPI Release", "AAPL Q2 Earnings"
        "event_category":  str,   # "inflation" | "employment" | "growth" |
                                  # "monetary_policy" | "earnings" | "other"
        "event_time":      str | None,  # ISO-8601 if known
        "time_to_event":   dict | None, # {"hours": float, "trading_days": float}
        "importance":      str,   # "high" | "medium" | "low" | "unknown"
        "scope":           str,   # "market_wide" | "sector" | "single_stock"
        "related_symbols": list[str],
        "risk_window":     str | None,  # "within_24h" | "within_3d" |
                                        # "within_7d" | "beyond_7d"
        "event_source":    str,   # "caller_provided" (v1.1+)
        "is_elapsed":      bool,  # True when event_time < reference_time (v1.1+)
        "notes":           str | None,
    }

``time_to_event`` (v1.1+) includes::

    {
        "hours": float,
        "trading_days": float,   # APPROXIMATE: calendar_heuristic (hours/24 * 5/7)
        "timing_method": "calendar_heuristic",
    }
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

# ── Constants ────────────────────────────────────────────────────────

_EVENT_CONTEXT_VERSION = "1.1"

# Importance classification for known macro event types.
# Derived field: importance = _MACRO_IMPORTANCE.get(event_name_normalised, "unknown")
_MACRO_IMPORTANCE: dict[str, str] = {
    # High importance — moves markets, not ignorable
    "fomc":          "high",
    "fomc_decision": "high",
    "fomc_minutes":  "high",
    "cpi":           "high",
    "core_cpi":      "high",
    "nfp":           "high",
    "non_farm_payrolls": "high",
    "jobs_report":   "high",
    "gdp":           "high",
    "gdp_advance":   "high",
    "pce":           "high",
    "core_pce":      "high",
    "fed_rate_decision": "high",
    # Medium importance
    "ppi":           "medium",
    "core_ppi":      "medium",
    "ism_manufacturing": "medium",
    "ism_services":  "medium",
    "retail_sales":  "medium",
    "durable_goods": "medium",
    "housing_starts": "medium",
    "consumer_confidence": "medium",
    "michigan_sentiment":  "medium",
    "initial_claims": "medium",
    "jolts":         "medium",
    "fed_speak":     "medium",
    "fed_speaker":   "medium",
    "treasury_auction": "medium",
    # Low importance
    "existing_home_sales": "low",
    "new_home_sales":      "low",
    "trade_balance":       "low",
    "factory_orders":      "low",
    "business_inventories": "low",
}

# Category classification for known macro event types.
# Derived field: event_category = _MACRO_CATEGORY.get(normalised, "other")
_MACRO_CATEGORY: dict[str, str] = {
    "fomc": "monetary_policy", "fomc_decision": "monetary_policy",
    "fomc_minutes": "monetary_policy", "fed_rate_decision": "monetary_policy",
    "fed_speak": "monetary_policy", "fed_speaker": "monetary_policy",
    "cpi": "inflation", "core_cpi": "inflation",
    "ppi": "inflation", "core_ppi": "inflation",
    "pce": "inflation", "core_pce": "inflation",
    "nfp": "employment", "non_farm_payrolls": "employment",
    "jobs_report": "employment", "initial_claims": "employment",
    "jolts": "employment",
    "gdp": "growth", "gdp_advance": "growth",
    "retail_sales": "growth", "durable_goods": "growth",
    "ism_manufacturing": "growth", "ism_services": "growth",
    "factory_orders": "growth", "business_inventories": "growth",
    "consumer_confidence": "growth", "michigan_sentiment": "growth",
    "housing_starts": "growth", "existing_home_sales": "growth",
    "new_home_sales": "growth", "trade_balance": "growth",
    "treasury_auction": "monetary_policy",
}

# Symbols considered market-wide for overlap purposes
_MARKET_WIDE_SYMBOLS = frozenset({
    "SPY", "SPX", "QQQ", "NDX", "IWM", "RUT", "DIA",
    "XSP", "ES", "NQ", "RTY", "YM",
})

# Window thresholds (hours)
_WINDOW_24H = 24.0
_WINDOW_3D = 72.0
_WINDOW_7D = 168.0

# ── Public API ───────────────────────────────────────────────────────


def build_event_context(
    *,
    macro_events: list[dict[str, Any]] | None = None,
    company_events: list[dict[str, Any]] | None = None,
    candidate: dict[str, Any] | None = None,
    positions: list[dict[str, Any]] | None = None,
    reference_time: _dt.datetime | None = None,
) -> dict[str, Any]:
    """Build a normalized event-context output.

    Parameters
    ----------
    macro_events : list[dict] | None
        Upcoming macro calendar events. Each dict should have at least
        ``event_name`` and ideally ``event_time`` (ISO-8601 str or
        datetime) and ``importance`` (high/medium/low).
    company_events : list[dict] | None
        Upcoming company / earnings events. Each dict should have at
        least ``event_name`` and ``related_symbols``.
    candidate : dict | None
        Normalized candidate dict for overlap detection.
    positions : list[dict] | None
        Current portfolio position dicts for cluster detection.
    reference_time : datetime | None
        Anchor for time calculations (defaults to UTC now).

    Returns
    -------
    dict – structured event-context output.
    """
    ref = reference_time or _dt.datetime.now(_dt.timezone.utc)
    warning_flags: list[str] = []
    risk_flags: list[str] = []

    macro_raw = macro_events or []
    company_raw = company_events or []

    has_macro = len(macro_raw) > 0
    has_company = len(company_raw) > 0

    # Distinguish "provided but empty" from "not provided at all"
    macro_provided = macro_events is not None
    company_provided = company_events is not None

    # Track what coverage is available
    if macro_events is None:
        warning_flags.append("macro_events_not_provided")
    if company_events is None:
        warning_flags.append("company_events_not_provided")
    if candidate is None:
        warning_flags.append("candidate_not_provided")
    if positions is None:
        warning_flags.append("positions_not_provided")

    # ── Normalize event items ────────────────────────────────────
    macro_items = [_normalize_event(e, "macro", ref) for e in macro_raw]
    company_items = [_normalize_event(e, "company", ref) for e in company_raw]
    all_items = macro_items + company_items

    # ── Assign risk windows ──────────────────────────────────────
    windows = _assign_windows(all_items)

    # ── Candidate overlap ────────────────────────────────────────
    cand_overlap = _compute_candidate_overlap(candidate, all_items)

    # ── Portfolio overlap ────────────────────────────────────────
    port_overlap = _compute_portfolio_overlap(positions, all_items)

    # ── Risk flags ───────────────────────────────────────────────
    risk_flags.extend(_compute_risk_flags(all_items, windows, cand_overlap, port_overlap))

    # ── Event risk state ─────────────────────────────────────────
    event_risk_state = _derive_event_risk_state(
        all_items, windows, has_macro, has_company, warning_flags,
        macro_provided=macro_provided, company_provided=company_provided,
    )

    # ── Status ───────────────────────────────────────────────────
    if not has_macro and not has_company:
        if macro_events is None and company_events is None:
            status = "no_data"
        else:
            # Caller provided empty lists → quiet but valid
            status = "ok"
    elif has_macro and has_company:
        status = "ok"
    else:
        status = "partial"

    summary = _build_summary(
        status, event_risk_state, macro_items, company_items,
        windows, cand_overlap, port_overlap, warning_flags,
    )

    return {
        "event_context_version": _EVENT_CONTEXT_VERSION,
        "generated_at": ref.isoformat(),
        "status": status,
        "summary": summary,
        "event_risk_state": event_risk_state,
        "upcoming_macro_events": macro_items,
        "upcoming_company_events": company_items,
        "candidate_event_overlap": cand_overlap,
        "portfolio_event_overlap": port_overlap,
        "event_windows": windows,
        "risk_flags": sorted(set(risk_flags)),
        "warning_flags": sorted(set(warning_flags)),
        "evidence": {
            "macro_event_count": len(macro_items),
            "company_event_count": len(company_items),
            "high_importance_count": sum(
                1 for e in all_items if e["importance"] == "high"
            ),
            "elapsed_event_count": sum(
                1 for e in all_items if e.get("is_elapsed")
            ),
            "within_24h_count": len(windows["within_24h"]),
            "within_3d_count": len(windows["within_3d"]),
            "candidate_overlap_count": cand_overlap["overlap_count"],
            "portfolio_overlap_count": port_overlap["event_cluster_count"],
        },
        "metadata": {
            "event_context_version": _EVENT_CONTEXT_VERSION,
            "macro_coverage": "available" if has_macro else ("none" if macro_events is None else "empty"),
            "company_event_coverage": "available" if has_company else ("none" if company_events is None else "empty"),
            "candidate_provided": candidate is not None,
            "positions_provided": positions is not None,
            "reference_time": ref.isoformat(),
            "total_events_processed": len(all_items),
            "elapsed_event_count": sum(
                1 for e in all_items if e.get("is_elapsed")
            ),
            "event_sources_used": sorted({
                e.get("event_source", "caller_provided") for e in all_items
            }) if all_items else [],
            "timing_method": "calendar_heuristic",
            "timing_note": (
                "trading_days uses hours/24 * 5/7 approximation; "
                "does not account for market holidays or weekends"
            ),
        },
    }


def classify_candidate_event_risk(
    event_context: dict[str, Any],
    *,
    window_end: str | None = None,
    window_days: int | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Classify event risk for a single candidate's time window.

    Parameters
    ----------
    event_context : dict
        Output of ``build_event_context()``.
    window_end : str | None
        ISO date (e.g. expiration) marking the end of the candidate's
        risk window.  Used for options candidates.
    window_days : int | None
        Fixed look-forward in calendar days.  Used for stock candidates
        (no expiration).
    symbol : str | None
        Candidate's ticker symbol.  When provided, company events are
        filtered to only those whose ``related_symbols`` contain this
        symbol.  Macro / market-wide events are always included.

    Returns
    -------
    dict with ``event_risk`` ("high" | "elevated" | "quiet" | "unknown")
    and ``event_details`` (list of matching events).
    """
    _unknown: dict[str, Any] = {"event_risk": "unknown", "event_details": []}

    if not event_context or event_context.get("status") == "no_data":
        return _unknown

    today = _dt.date.today()

    # Determine the window end date
    if window_end is not None:
        try:
            end_d = _dt.date.fromisoformat(str(window_end)[:10])
        except (ValueError, TypeError):
            return _unknown
    elif window_days is not None:
        end_d = today + _dt.timedelta(days=window_days)
    else:
        return _unknown

    # Macro events always apply to every candidate.
    macro_events = event_context.get("upcoming_macro_events") or []

    # Company events are filtered by symbol when provided.
    company_events = event_context.get("upcoming_company_events") or []
    if symbol:
        sym_upper = symbol.upper()
        company_events = [
            evt for evt in company_events
            if sym_upper in {s.upper() for s in (evt.get("related_symbols") or [])}
            or (evt.get("scope") or "") == "market_wide"
        ]

    all_events = macro_events + company_events

    events_in_window: list[dict[str, str]] = []
    for evt in all_events:
        if evt.get("is_elapsed"):
            continue
        importance = evt.get("importance", "low")
        if importance not in ("high", "medium"):
            continue
        evt_time = evt.get("event_time")
        if not evt_time:
            continue
        try:
            evt_d = _dt.date.fromisoformat(str(evt_time)[:10])
        except (ValueError, TypeError):
            continue
        if today <= evt_d <= end_d:
            events_in_window.append({
                "event": evt.get("event_name", "Unknown"),
                "date": str(evt_time)[:10],
                "importance": importance,
                "category": evt.get("event_category", "other"),
            })

    if any(e["importance"] == "high" for e in events_in_window):
        risk = "high"
    elif events_in_window:
        risk = "elevated"
    else:
        risk = "quiet"

    return {"event_risk": risk, "event_details": events_in_window}


# ── Event normalization ──────────────────────────────────────────────


def _normalize_event(
    raw: dict[str, Any],
    default_type: str,
    ref: _dt.datetime,
) -> dict[str, Any]:
    """Normalize a raw event dict into the EventItem schema.

    Input fields used:
      event_name (required), event_type, event_category, event_time,
      importance, scope, related_symbols, notes
    Derived fields:
      time_to_event (computed from event_time - ref; includes timing_method),
      risk_window (assigned from time_to_event; None for elapsed events),
      importance (classified from event_name if not provided),
      event_category (classified from event_name if not provided),
      event_source (always "caller_provided" in v1.1),
      is_elapsed (True when event_time < reference_time)
    """
    event_name = str(raw.get("event_name") or raw.get("name") or "unknown_event")
    event_type = str(raw.get("event_type") or default_type).lower()

    # Resolve importance from explicit field or heuristic lookup
    importance = str(raw.get("importance") or "").lower()
    if importance not in ("high", "medium", "low"):
        importance = _classify_importance(event_name, event_type)

    # Resolve category from explicit field or heuristic lookup
    category = str(raw.get("event_category") or raw.get("category") or "").lower()
    if not category or category == "other":
        category = _classify_category(event_name, event_type)

    # Parse event time
    event_time_str, event_dt = _parse_event_time(raw)

    # Compute time-to-event
    time_to_event = None
    risk_window = None
    is_elapsed = False
    if event_dt is not None:
        hours_until = (event_dt - ref).total_seconds() / 3600.0
        is_elapsed = hours_until < 0
        # trading_days: approximate calendar heuristic (hours/24 * 5/7).
        # Does NOT account for market holidays or weekends.
        trading_days = max(0.0, hours_until / 24.0 * (5.0 / 7.0))
        time_to_event = {
            "hours": round(hours_until, 1),
            "trading_days": round(trading_days, 1),
            "timing_method": "calendar_heuristic",
        }
        risk_window = _hours_to_window(hours_until) if not is_elapsed else None

    # Scope
    scope = str(raw.get("scope") or "").lower()
    if not scope:
        scope = "market_wide" if event_type in ("macro", "fed_speak") else "single_stock"

    # Related symbols
    related_symbols = raw.get("related_symbols") or raw.get("symbols") or []
    if isinstance(related_symbols, str):
        related_symbols = [related_symbols]
    related_symbols = [str(s).upper() for s in related_symbols if s]

    return {
        "event_type": event_type,
        "event_name": event_name,
        "event_category": category,
        "event_time": event_time_str,
        "time_to_event": time_to_event,
        "importance": importance,
        "scope": scope,
        "related_symbols": related_symbols,
        "risk_window": risk_window,
        "event_source": "caller_provided",
        "is_elapsed": is_elapsed,
        "notes": raw.get("notes"),
    }


# ── Classification helpers ───────────────────────────────────────────


def _classify_importance(event_name: str, event_type: str) -> str:
    """Classify event importance from event name or type.

    Uses _MACRO_IMPORTANCE lookup on normalised event name.
    Falls back to "unknown" if no match.
    """
    key = _normalise_event_key(event_name)
    imp = _MACRO_IMPORTANCE.get(key)
    if imp:
        return imp
    # Earnings are always at least medium importance
    if event_type in ("earnings", "company") or "earnings" in key:
        return "medium"
    if event_type == "fed_speak":
        return "medium"
    return "unknown"


def _classify_category(event_name: str, event_type: str) -> str:
    """Classify event category from event name or type."""
    key = _normalise_event_key(event_name)
    cat = _MACRO_CATEGORY.get(key)
    if cat:
        return cat
    if event_type == "earnings" or "earnings" in key:
        return "earnings"
    if event_type == "fed_speak" or "fed" in key:
        return "monetary_policy"
    return "other"


def _normalise_event_key(name: str) -> str:
    """Normalise event name for lookup.

    "CPI Release" → "cpi", "FOMC Decision" → "fomc_decision"
    """
    key = name.lower().strip()
    # Remove common suffixes
    for suffix in (" release", " report", " print", " data", " announcement"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
            break
    return key.replace(" ", "_").replace("-", "_")


# ── Time helpers ─────────────────────────────────────────────────────


def _parse_event_time(raw: dict) -> tuple[str | None, _dt.datetime | None]:
    """Extract event time from raw dict. Returns (iso_str, datetime)."""
    val = raw.get("event_time") or raw.get("event_date") or raw.get("date")
    if val is None:
        return None, None
    if isinstance(val, _dt.datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=_dt.timezone.utc)
        return val.isoformat(), val
    if isinstance(val, _dt.date):
        dt = _dt.datetime.combine(val, _dt.time(0, 0), tzinfo=_dt.timezone.utc)
        return dt.isoformat(), dt
    # Try ISO parse
    s = str(val).strip()
    if not s:
        return None, None
    try:
        dt = _dt.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.isoformat(), dt
    except (ValueError, TypeError):
        pass
    # Try date-only
    try:
        d = _dt.date.fromisoformat(s[:10])
        dt = _dt.datetime.combine(d, _dt.time(0, 0), tzinfo=_dt.timezone.utc)
        return dt.isoformat(), dt
    except (ValueError, TypeError):
        return s, None  # preserve the string, but can't parse


def _hours_to_window(hours: float) -> str:
    """Map hours-until-event to a risk window bucket."""
    if hours <= _WINDOW_24H:
        return "within_24h"
    if hours <= _WINDOW_3D:
        return "within_3d"
    if hours <= _WINDOW_7D:
        return "within_7d"
    return "beyond_7d"


# ── Window assignment ────────────────────────────────────────────────


def _assign_windows(items: list[dict]) -> dict[str, list[dict]]:
    """Bucket event items by risk window."""
    windows: dict[str, list[dict]] = {
        "within_24h": [],
        "within_3d": [],
        "within_7d": [],
        "beyond_7d": [],
    }
    for item in items:
        if item.get("is_elapsed"):
            continue  # past events excluded from window bucketing
        w = item.get("risk_window")
        if w and w in windows:
            windows[w].append(item)
        elif w is None:
            # No timing data — cannot bucket, skip
            pass
    return windows


# ── Candidate overlap ───────────────────────────────────────────────


def _compute_candidate_overlap(
    candidate: dict | None,
    all_items: list[dict],
) -> dict[str, Any]:
    """Detect events that overlap with a candidate trade's window."""
    result: dict[str, Any] = {
        "candidate_symbol": None,
        "overlapping_events": [],
        "overlap_count": 0,
    }
    if candidate is None:
        return result

    cand_sym = str(candidate.get("symbol") or "").upper()
    result["candidate_symbol"] = cand_sym or None

    if not cand_sym:
        return result

    # Candidate DTE for decision horizon overlap
    dte = candidate.get("entry_context", {}).get("dte")
    cand_dte_hours = dte * 24.0 if dte is not None else None

    overlapping: list[dict] = []
    for item in all_items:
        if item.get("is_elapsed"):
            continue  # past events excluded from candidate overlap

        # Check symbol match
        symbols = item.get("related_symbols", [])
        symbol_match = cand_sym in symbols

        # Market-wide macro events overlap with index ETFs
        scope = item.get("scope", "")
        macro_overlap = (
            scope == "market_wide"
            and cand_sym in _MARKET_WIDE_SYMBOLS
        )

        if not symbol_match and not macro_overlap:
            continue

        # Check timing overlap: event within candidate's decision horizon
        tte = item.get("time_to_event")
        if tte is None:
            # No timing — still flag as potential overlap by symbol
            overlapping.append(item)
            continue

        hours = tte.get("hours", float("inf"))

        # Within candidate DTE window?  Or within 7d default?
        horizon = cand_dte_hours if cand_dte_hours is not None else _WINDOW_7D
        if hours <= horizon:
            overlapping.append(item)

    result["overlapping_events"] = overlapping
    result["overlap_count"] = len(overlapping)
    return result


# ── Portfolio overlap ────────────────────────────────────────────────


def _compute_portfolio_overlap(
    positions: list[dict] | None,
    all_items: list[dict],
) -> dict[str, Any]:
    """Detect event clustering across portfolio positions."""
    result: dict[str, Any] = {
        "positions_with_overlap": 0,
        "symbols_with_overlap": [],
        "overlapping_events": [],
        "event_cluster_count": 0,
    }
    if not positions:
        return result

    # Collect unique symbols from positions
    port_symbols: set[str] = set()
    for pos in positions:
        sym = str(pos.get("symbol") or pos.get("underlying") or "").upper()
        if sym:
            port_symbols.add(sym)

    if not port_symbols:
        return result

    # Check which events overlap portfolio symbols
    overlapping_events: list[dict] = []
    symbols_hit: set[str] = set()

    for item in all_items:
        if item.get("is_elapsed"):
            continue  # past events excluded from portfolio overlap
        tte = item.get("time_to_event")
        # Only consider upcoming events (within 7d or no timing)
        if tte is not None and tte.get("hours", 0) > _WINDOW_7D:
            continue

        symbols = set(item.get("related_symbols", []))
        scope = item.get("scope", "")

        # Market-wide events overlap portfolio symbols that are index ETFs
        if scope == "market_wide":
            matched = port_symbols & _MARKET_WIDE_SYMBOLS
        else:
            matched = port_symbols & symbols

        if matched:
            overlapping_events.append(item)
            symbols_hit.update(matched)

    result["overlapping_events"] = overlapping_events
    result["symbols_with_overlap"] = sorted(symbols_hit)
    result["positions_with_overlap"] = len(symbols_hit)
    result["event_cluster_count"] = len(overlapping_events)
    return result


# ── Risk flags ───────────────────────────────────────────────────────


def _compute_risk_flags(
    all_items: list[dict],
    windows: dict[str, list[dict]],
    cand_overlap: dict,
    port_overlap: dict,
) -> list[str]:
    """Compute event-related risk flags."""
    flags: list[str] = []

    high_24h = [
        e for e in windows.get("within_24h", [])
        if e.get("importance") == "high"
    ]
    high_3d = [
        e for e in windows.get("within_3d", [])
        if e.get("importance") == "high"
    ]

    if high_24h:
        flags.append("high_importance_event_within_24h")
    if high_3d:
        flags.append("high_importance_event_within_3d")
    if len(windows.get("within_24h", [])) >= 2:
        flags.append("multiple_events_within_24h")
    if len(windows.get("within_3d", [])) >= 3:
        flags.append("multiple_events_within_3d")

    if cand_overlap.get("overlap_count", 0) > 0:
        flags.append("candidate_overlaps_event")
    if cand_overlap.get("overlap_count", 0) >= 2:
        flags.append("candidate_overlaps_multiple_events")

    if port_overlap.get("event_cluster_count", 0) >= 2:
        flags.append("portfolio_event_clustering")
    if port_overlap.get("positions_with_overlap", 0) >= 3:
        flags.append("portfolio_many_positions_near_event")

    return flags


# ── Event risk state ─────────────────────────────────────────────────


def _derive_event_risk_state(
    all_items: list[dict],
    windows: dict[str, list[dict]],
    has_macro: bool,
    has_company: bool,
    warning_flags: list[str],
    *,
    macro_provided: bool = False,
    company_provided: bool = False,
) -> str:
    """Derive overall event risk state.

    Heuristic (v1.1):
    - "crowded":  >=2 high-importance events within 3d, or
                  >=3 total events within 3d
    - "elevated": any high-importance event within 3d, or
                  >=2 events within 24h
    - "quiet":    no important events near-term
    - "unknown":  no event data provided, or all events lack timing

    Elapsed events are excluded — only future events affect state.
    """
    # Filter to non-elapsed items for state derivation
    future_items = [e for e in all_items if not e.get("is_elapsed")]

    if not future_items:
        # Caller explicitly provided empty list(s) → genuinely quiet
        if macro_provided or company_provided:
            return "quiet"
        return "unknown"

    # Count items with usable timing (future only)
    timed = [e for e in future_items if e.get("time_to_event") is not None]
    if not timed:
        return "unknown"

    near_3d = windows.get("within_24h", []) + windows.get("within_3d", [])
    high_near = [e for e in near_3d if e.get("importance") == "high"]
    near_24h = windows.get("within_24h", [])

    if len(high_near) >= 2 or len(near_3d) >= 3:
        return "crowded"
    if high_near or len(near_24h) >= 2:
        return "elevated"
    return "quiet"


# ── Summary builder ──────────────────────────────────────────────────


def _build_summary(
    status: str,
    risk_state: str,
    macro_items: list[dict],
    company_items: list[dict],
    windows: dict[str, list[dict]],
    cand_overlap: dict,
    port_overlap: dict,
    warning_flags: list[str],
) -> str:
    """Build a human-readable summary."""
    parts: list[str] = []

    total = len(macro_items) + len(company_items)
    if total == 0:
        if "macro_events_not_provided" in warning_flags and \
           "company_events_not_provided" in warning_flags:
            return "No event data provided. Event risk assessment unavailable."
        return "No upcoming events in the provided calendar. Event risk is quiet."

    parts.append(f"{total} upcoming event(s) tracked")
    if macro_items:
        parts.append(f"({len(macro_items)} macro")
    if company_items:
        if macro_items:
            parts.append(f", {len(company_items)} company)")
        else:
            parts.append(f"({len(company_items)} company)")
    elif macro_items:
        parts.append(")")

    w24 = len(windows.get("within_24h", []))
    w3d = len(windows.get("within_3d", []))
    if w24:
        parts.append(f". {w24} event(s) within 24h")
    if w3d:
        parts.append(f", {w3d} within 3 trading days")

    parts.append(f". Event risk state: {risk_state}.")

    if cand_overlap.get("overlap_count", 0) > 0:
        sym = cand_overlap.get("candidate_symbol", "?")
        parts.append(
            f" Candidate {sym} overlaps {cand_overlap['overlap_count']} event(s)."
        )

    if port_overlap.get("event_cluster_count", 0) > 0:
        parts.append(
            f" Portfolio has {port_overlap['event_cluster_count']} event overlap(s) "
            f"across {port_overlap['positions_with_overlap']} position(s)."
        )

    return "".join(parts)
