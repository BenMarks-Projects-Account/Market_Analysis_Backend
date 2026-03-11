"""
Shared Time-Horizon Vocabulary & Helpers
=========================================

Provides the canonical time-horizon vocabulary used across all Market
Picture engines, scanner candidates, model-analysis responses, and the
Context Assembler.

Vocabulary
----------
intraday        – same session / same-day signals
short_term      – next few trading days (1-5 days)
swing           – multi-day to multi-week trade horizon (roughly 2-4 weeks)
medium_term     – several weeks to months
long_term       – months+
event_driven    – dominated by a discrete catalyst / event window
days_to_expiry  – options-contract horizon tied primarily to DTE structure
unknown         – not yet classifiable confidently

Design intent
-------------
This vocabulary is a semantic foundation for future contradiction
detection, market/candidate alignment checks, policy rules, and
portfolio-aware review.  All consumers should import from here rather
than hardcoding strings.
"""

from __future__ import annotations

# ── Canonical vocabulary ─────────────────────────────────────────────

ALLOWED_HORIZONS: frozenset[str] = frozenset({
    "intraday",
    "short_term",
    "swing",
    "medium_term",
    "long_term",
    "event_driven",
    "days_to_expiry",
    "unknown",
})

# ── Horizon categories ───────────────────────────────────────────────
# Horizons fall into three categories that affect how rank-based
# comparisons should be interpreted:
#
#   duration      – pure calendar-time bucket (intraday → long_term).
#                   Rank comparisons between these are reliable.
#   variable      – duration is data-dependent, not a fixed bucket.
#                     event_driven  – catalyst/event window, could be
#                                     1 day before earnings or 3 months
#                                     before an election.
#                     days_to_expiry – options-contract horizon that
#                                     depends entirely on the DTE of
#                                     the specific position.
#                   Comparing variable vs duration by rank is approximate.
#   unclassified  – no horizon information available (unknown).

DURATION_HORIZONS: frozenset[str] = frozenset({
    "intraday", "short_term", "swing", "medium_term", "long_term",
})

VARIABLE_HORIZONS: frozenset[str] = frozenset({
    "event_driven", "days_to_expiry",
})

HORIZON_CATEGORIES: dict[str, str] = {
    **{h: "duration" for h in DURATION_HORIZONS},
    **{h: "variable" for h in VARIABLE_HORIZONS},
    "unknown": "unclassified",
}

# Semantic ordering from shortest to longest effective horizon.
# Used by the Context Assembler to compute horizon span summaries.
#
# IMPORTANT — partial order for variable horizons:
#   event_driven and days_to_expiry are placed between swing and
#   medium_term as a *reasonable default position*, NOT because they
#   are genuinely equivalent to that duration.  Consumers doing
#   rank-based gap arithmetic should check ``horizons_comparable()``
#   when precision matters.
#
# Do NOT reorder without auditing all consumers (conflict_detector,
# decision_policy, market_composite, context_assembler).
HORIZON_ORDER: tuple[str, ...] = (
    "intraday",       # 0  duration
    "short_term",     # 1  duration
    "swing",          # 2  duration
    "event_driven",   # 3  variable (catalyst-dependent)
    "days_to_expiry", # 4  variable (DTE-dependent)
    "medium_term",    # 5  duration
    "long_term",      # 6  duration
    "unknown",        # 7  unclassified — always last
)

_HORIZON_RANK: dict[str, int] = {h: i for i, h in enumerate(HORIZON_ORDER)}


# ── Engine-level mapping ─────────────────────────────────────────────
# Maps engine_key → canonical time_horizon.
# Rationale for each is documented inline.

ENGINE_HORIZON_MAP: dict[str, str] = {
    # News crawl + live headlines — refreshes frequently, same-day cadence.
    "news_sentiment": "intraday",
    # Daily aggregate breadth/participation — measures 1-5 day market health.
    "breadth_participation": "short_term",
    # Daily IV regime, term structure snapshot — reflects current-day structure.
    "volatility_options": "short_term",
    # Daily cross-asset correlation/divergence — overnight-to-daily window.
    "cross_asset_macro": "short_term",
    # Daily positioning/flow signals — 1-day data windows.
    "flows_positioning": "short_term",
    # Weekly FRED data, credit spreads, funding rates — longer-term themes.
    "liquidity_financial_conditions": "medium_term",
}

# ── Scanner-level mapping ────────────────────────────────────────────
# Maps scanner_key → canonical time_horizon.

SCANNER_HORIZON_MAP: dict[str, str] = {
    # Stock scanners — multi-day to multi-week hold periods.
    "stock_pullback_swing": "swing",
    "stock_momentum_breakout": "swing",
    "stock_mean_reversion": "swing",
    "stock_volatility_expansion": "swing",
    # Options scanners — horizon determined by DTE structure.
    "put_credit_spread": "days_to_expiry",
    "call_credit_spread": "days_to_expiry",
    "put_debit": "days_to_expiry",
    "call_debit": "days_to_expiry",
    "iron_condor": "days_to_expiry",
    "butterfly_debit": "days_to_expiry",
    "calendar_spread": "days_to_expiry",
    "calendar_call_spread": "days_to_expiry",
    "calendar_put_spread": "days_to_expiry",
    "csp": "days_to_expiry",
    "covered_call": "days_to_expiry",
    "income": "days_to_expiry",
}

# ── Model-analysis horizon mapping ──────────────────────────────────
# Maps the legacy model "1D"/"1W"/"1M" codes to canonical horizons.

MODEL_HORIZON_MAP: dict[str, str] = {
    "1D": "intraday",
    "1W": "short_term",
    "1M": "medium_term",
}

# ── Family-level defaults ────────────────────────────────────────────
# Fallback when a specific scanner_key is not in SCANNER_HORIZON_MAP.

FAMILY_HORIZON_DEFAULTS: dict[str, str] = {
    "stock": "swing",
    "options": "days_to_expiry",
}


# ── Public helpers ───────────────────────────────────────────────────

def validate_horizon(value: str | None) -> str:
    """Return *value* if it is a valid horizon, otherwise ``"unknown"``.

    Accepts ``None`` gracefully.
    """
    if value and value in ALLOWED_HORIZONS:
        return value
    return "unknown"


def resolve_engine_horizon(engine_key: str) -> str:
    """Return the canonical horizon for *engine_key*.

    Falls back to ``"unknown"`` for unrecognised engines.
    """
    return ENGINE_HORIZON_MAP.get(engine_key, "unknown")


def resolve_scanner_horizon(
    scanner_key: str | None = None,
    strategy_family: str | None = None,
) -> str:
    """Return the canonical horizon for a scanner candidate.

    Resolution order:
      1. Explicit ``scanner_key`` lookup in ``SCANNER_HORIZON_MAP``
      2. ``strategy_family`` default (``FAMILY_HORIZON_DEFAULTS``)
      3. ``"unknown"``
    """
    if scanner_key and scanner_key in SCANNER_HORIZON_MAP:
        return SCANNER_HORIZON_MAP[scanner_key]
    if strategy_family and strategy_family in FAMILY_HORIZON_DEFAULTS:
        return FAMILY_HORIZON_DEFAULTS[strategy_family]
    return "unknown"


def resolve_model_horizon(
    raw_horizon: str | None = None,
    analysis_type: str | None = None,
) -> str:
    """Return the canonical horizon for a model-analysis response.

    Resolution order:
      1. Explicit ``raw_horizon`` mapped via ``MODEL_HORIZON_MAP``
      2. ``raw_horizon`` validated directly against ``ALLOWED_HORIZONS``
      3. ``analysis_type`` mapped via ``ENGINE_HORIZON_MAP`` (market-picture
         analysis types share engine keys)
      4. ``"unknown"``
    """
    if raw_horizon:
        mapped = MODEL_HORIZON_MAP.get(raw_horizon.strip().upper())
        if mapped:
            return mapped
        validated = validate_horizon(raw_horizon)
        if validated != "unknown":
            return validated
    if analysis_type:
        return ENGINE_HORIZON_MAP.get(analysis_type, "unknown")
    return "unknown"


def horizon_rank(value: str) -> int:
    """Return the ordinal rank of a horizon value (lower = shorter).

    ``"unknown"`` is ranked last.

    Note: for ``event_driven`` and ``days_to_expiry`` the rank is a
    default placement, not a genuine duration.  Use
    ``horizons_comparable()`` to check whether a rank-based gap between
    two horizons is semantically reliable.
    """
    return _HORIZON_RANK.get(value, len(HORIZON_ORDER))


def horizon_category(value: str) -> str:
    """Return the category of a horizon: ``"duration"``, ``"variable"``, or ``"unclassified"``.

    Duration horizons (intraday → long_term) represent fixed calendar-time
    buckets.  Variable horizons (event_driven, days_to_expiry) have
    data-dependent duration.  ``"unknown"`` is unclassified.
    """
    return HORIZON_CATEGORIES.get(value, "unclassified")


def horizons_comparable(a: str, b: str) -> bool:
    """Return True if rank-based comparison between *a* and *b* is reliable.

    Both horizons must be duration-based for the comparison to be
    semantically trustworthy.  Comparisons involving variable or
    unclassified horizons should be treated as approximate.
    """
    return (
        horizon_category(a) == "duration"
        and horizon_category(b) == "duration"
    )
