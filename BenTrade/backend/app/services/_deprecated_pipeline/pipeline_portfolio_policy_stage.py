"""Pipeline Portfolio Context + Decision Policy Stage — Step 11.

Attaches candidate-relevant portfolio/risk context and applies
deterministic policy checks to each enriched candidate.  Produces
per-candidate policy artifacts plus a stage summary.

Public API
──────────
    portfolio_policy_handler(run, artifact_store, stage_key, **kwargs)
        Stage handler compatible with the Step 3 orchestrator.
    default_portfolio_provider(lookup_input)
        Default portfolio lookup provider (no live sources).
    evaluate_policy(enriched_data, event_ctx, portfolio_ctx, run_id)
        Run all policy checks and produce structured policy output.

Role boundary
─────────────
This module:
- Retrieves per-candidate enriched packets from Step 9.
- Retrieves per-candidate event context from Step 10 (opportunistic).
- Calls an injectable portfolio provider for portfolio/risk context.
- Applies deterministic policy checks per candidate.
- Writes per-candidate policy artifacts (keyed policy_{candidate_id}).
- Writes a policy_stage_summary artifact.
- Emits structured events via event_callback.

This module does NOT:
- Re-run any earlier stage.
- Mutate Step 9 or Step 10 artifacts in place.
- Make final recommendation decisions.
- Invoke model-generated judgment or prompt calls.
- Mix deterministic policy checks with model reasoning.
- Deep-copy giant candidate packets into policy artifacts.

This stage is the deterministic gating layer; later reasoning
stages must respect its outputs.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.pipeline_artifact_store import (
    build_artifact_record,
    get_artifact_by_key,
    put_artifact,
)
from app.services.pipeline_run_contract import (
    build_log_event,
    build_run_error,
)

logger = logging.getLogger("bentrade.pipeline_portfolio_policy_stage")

# ── Module identity ─────────────────────────────────────────────
_MODULE_ROLE = "stage_handler"
_STAGE_KEY = "policy"
_POLICY_VERSION = "1.0"
_PORTFOLIO_CONTEXT_VERSION = "1.0"


# =====================================================================
#  Policy thresholds — centralized and named
# =====================================================================

MAX_ACTIVE_SAME_SYMBOL = 2
"""Maximum active positions allowed in the same symbol."""

MAX_ACTIVE_SAME_STRATEGY = 5
"""Maximum active positions in the same strategy family."""

MAX_TOTAL_POSITIONS = 10
"""Maximum total active positions across portfolio."""

MAX_CAPITAL_UTILIZATION_PCT = 80.0
"""Maximum percentage of capital that may be committed."""

EARNINGS_BLOCK_DAYS = 3
"""Block premium-selling strategies if earnings are within this many days."""

EARNINGS_CAUTION_DAYS = 7
"""Issue caution if earnings are within this many days."""

MACRO_CAUTION_DAYS = 3
"""Issue caution if a macro event is within this many days."""

# Strategies that are sensitive to imminent earnings events
PREMIUM_SELLING_STRATEGIES = frozenset({
    "put_credit_spread",
    "call_credit_spread",
    "iron_condor",
    "iron_butterfly",
    "short_put",
    "short_call",
    "short_strangle",
    "short_straddle",
})
"""Strategy types that involve premium-selling and are at higher
risk from imminent earnings / volatility events."""


# =====================================================================
#  Outcome vocabulary
# =====================================================================

OUTCOME_ELIGIBLE = "eligible"
OUTCOME_ELIGIBLE_WITH_CAUTIONS = "eligible_with_cautions"
OUTCOME_RESTRICTED = "restricted"
OUTCOME_BLOCKED = "blocked"
OUTCOME_FAILED = "failed"

VALID_OUTCOMES = frozenset({
    OUTCOME_ELIGIBLE,
    OUTCOME_ELIGIBLE_WITH_CAUTIONS,
    OUTCOME_RESTRICTED,
    OUTCOME_BLOCKED,
    OUTCOME_FAILED,
})


# =====================================================================
#  Check status vocabulary
# =====================================================================

CHECK_PASS = "pass"
CHECK_CAUTION = "caution"
CHECK_RESTRICT = "restrict"
CHECK_BLOCK = "block"
CHECK_UNKNOWN = "unknown"

VALID_CHECK_STATUSES = frozenset({
    CHECK_PASS,
    CHECK_CAUTION,
    CHECK_RESTRICT,
    CHECK_BLOCK,
    CHECK_UNKNOWN,
})


# =====================================================================
#  Policy evaluation status vocabulary
# =====================================================================

POLICY_STATUS_EVALUATED = "evaluated"
POLICY_STATUS_EVALUATED_DEGRADED = "evaluated_degraded"
POLICY_STATUS_SKIPPED_INVALID = "skipped_invalid_candidate"
POLICY_STATUS_FAILED = "failed"


# =====================================================================
#  Portfolio provider type
# =====================================================================

PortfolioProvider = Callable[[dict[str, Any]], dict[str, Any]]
"""Callable that takes a lookup_input dict and returns portfolio data.

lookup_input keys:
    symbol: str
    strategy_type: str | None
    scanner_family: str | None
    direction: str | None
    candidate_id: str | None

Return shape:
    provider_status: "available" | "no_live_sources" | "degraded" | "failed"
    trade_capability: dict
        enabled: bool
        status: str  ("enabled" | "disabled" | "unknown")
        restrictions: list[str]
    active_positions: list[dict]
        Each: symbol, strategy_type, direction, quantity, capital_committed
    capital_summary: dict
        total_capital: float | None
        capital_in_use: float | None
        utilization_pct: float | None
    restrictions: list[str]
    degraded_reasons: list[str]
"""


# =====================================================================
#  Default portfolio provider
# =====================================================================

def default_portfolio_provider(lookup_input: dict[str, Any]) -> dict[str, Any]:
    """Default portfolio provider — no live data sources.

    Returns empty results with honest ``no_live_sources`` status.
    Replace with broker-account-backed providers without changing
    stage logic.

    Parameters
    ----------
    lookup_input : dict
        Portfolio lookup request.

    Returns
    -------
    dict
        Provider result with empty portfolio data.
    """
    return {
        "provider_status": "no_live_sources",
        "trade_capability": {
            "enabled": True,
            "status": "unknown",
            "restrictions": [],
        },
        "active_positions": [],
        "capital_summary": {
            "total_capital": None,
            "capital_in_use": None,
            "utilization_pct": None,
        },
        "restrictions": [],
        "degraded_reasons": ["no live portfolio data sources configured"],
    }


# =====================================================================
#  Portfolio context builder
# =====================================================================

def build_portfolio_context(
    enriched_data: dict[str, Any],
    provider_result: dict[str, Any],
) -> dict[str, Any]:
    """Build a per-candidate portfolio context from provider data.

    Parameters
    ----------
    enriched_data : dict
        Enriched candidate packet from Step 9.
    provider_result : dict
        Output from the portfolio provider.

    Returns
    -------
    dict
        Structured portfolio context for this candidate.
    """
    symbol = enriched_data.get("symbol")
    strategy_type = enriched_data.get("strategy_type")
    scanner_family = enriched_data.get("scanner_family")

    prov_status = provider_result.get("provider_status", "unknown")
    trade_cap = provider_result.get("trade_capability", {})
    active_positions = provider_result.get("active_positions", [])
    cap_summary = provider_result.get("capital_summary", {})

    # Count overlaps
    active_same_symbol = sum(
        1 for p in active_positions
        if p.get("symbol") == symbol
    )
    active_same_strategy = sum(
        1 for p in active_positions
        if p.get("strategy_type") == strategy_type
    )

    # Concentration context
    strategy_families_seen = {
        p.get("strategy_type") for p in active_positions
        if p.get("strategy_type")
    }
    symbols_seen = {
        p.get("symbol") for p in active_positions
        if p.get("symbol")
    }

    # Restriction flags from provider
    restriction_flags = list(provider_result.get("restrictions", []))

    # Degraded reasons
    degraded_reasons = list(provider_result.get("degraded_reasons", []))

    # Snapshot status
    if prov_status == "failed":
        snapshot_status = "failed"
    elif prov_status == "no_live_sources":
        snapshot_status = "no_data"
    elif prov_status == "degraded":
        snapshot_status = "degraded"
    else:
        snapshot_status = "available"

    return {
        "portfolio_context_version": _PORTFOLIO_CONTEXT_VERSION,
        "candidate_id": enriched_data.get("candidate_id"),
        "symbol": symbol,
        "portfolio_snapshot_status": snapshot_status,
        "trade_capability_status": trade_cap.get("status", "unknown"),
        "active_symbol_positions": active_same_symbol,
        "active_strategy_positions": active_same_strategy,
        "total_active_positions": len(active_positions),
        "estimated_capital_utilization_pct": cap_summary.get(
            "utilization_pct",
        ),
        "concentration_context": {
            "strategy_family_count": len(strategy_families_seen),
            "symbol_exposure_count": len(symbols_seen),
            "scanner_family": scanner_family,
        },
        "correlation_context": {
            "cluster_overlap": False,
            "cluster_count": 0,
        },
        "restriction_flags": restriction_flags,
        "degraded_reasons": degraded_reasons,
        "source_refs": {
            "provider_status": prov_status,
        },
    }


# =====================================================================
#  Policy check functions
#
#  Each returns: {check_name, check_status, reason, details}
# =====================================================================

def _make_check(
    name: str,
    status: str,
    reason: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    """Build a structured policy check result.

    Parameters
    ----------
    name : str
        Check name identifier.
    status : str
        One of CHECK_PASS / CHECK_CAUTION / CHECK_RESTRICT /
        CHECK_BLOCK / CHECK_UNKNOWN.
    reason : str | None
        Human-readable reason if not passing.
    **details
        Additional detail fields.
    """
    return {
        "check_name": name,
        "check_status": status,
        "reason": reason,
        "details": details,
    }


def check_required_fields(
    enriched_data: dict[str, Any],
) -> dict[str, Any]:
    """Verify required fields are present in the enriched packet.

    Required: candidate_id, symbol, strategy_type.
    """
    missing = []
    for field in ("candidate_id", "symbol", "strategy_type"):
        if not enriched_data.get(field):
            missing.append(field)

    if missing:
        return _make_check(
            "required_fields",
            CHECK_BLOCK,
            f"Missing required fields: {', '.join(missing)}",
            missing_fields=missing,
        )
    return _make_check("required_fields", CHECK_PASS)


def check_trade_capability(
    portfolio_ctx: dict[str, Any],
) -> dict[str, Any]:
    """Check that trading is enabled for this account/capability."""
    cap_status = portfolio_ctx.get("trade_capability_status", "unknown")

    if cap_status == "disabled":
        return _make_check(
            "trade_capability",
            CHECK_BLOCK,
            "Trading capability is disabled",
            capability_status=cap_status,
        )
    if cap_status == "unknown":
        return _make_check(
            "trade_capability",
            CHECK_UNKNOWN,
            "Trading capability status unknown",
            capability_status=cap_status,
        )
    return _make_check(
        "trade_capability",
        CHECK_PASS,
        capability_status=cap_status,
    )


def check_strategy_allowed(
    enriched_data: dict[str, Any],
    portfolio_ctx: dict[str, Any],
) -> dict[str, Any]:
    """Check that the strategy type is not restricted.

    Uses restriction_flags from portfolio context.
    """
    strategy = enriched_data.get("strategy_type", "")
    restrictions = portfolio_ctx.get("restriction_flags", [])

    if f"strategy_blocked:{strategy}" in restrictions:
        return _make_check(
            "strategy_allowed",
            CHECK_BLOCK,
            f"Strategy '{strategy}' is blocked by portfolio restrictions",
            strategy_type=strategy,
        )
    if f"strategy_restricted:{strategy}" in restrictions:
        return _make_check(
            "strategy_allowed",
            CHECK_RESTRICT,
            f"Strategy '{strategy}' is restricted",
            strategy_type=strategy,
        )
    return _make_check(
        "strategy_allowed",
        CHECK_PASS,
        strategy_type=strategy,
    )


def check_event_risk_window(
    enriched_data: dict[str, Any],
    event_ctx: dict[str, Any] | None,
) -> dict[str, Any]:
    """Check event proximity risk using Step 10 event context.

    Rules
    ─────
    Input fields: event_risk_flags from event context,
                  strategy_type from enriched data

    - earnings_nearby + premium-selling strategy + nearest ≤ EARNINGS_BLOCK_DAYS → block
    - earnings_nearby + any strategy + nearest ≤ EARNINGS_CAUTION_DAYS → caution
    - macro_event_nearby + nearest ≤ MACRO_CAUTION_DAYS → caution
    - event_window_overlap → caution
    - no_event_coverage → caution (not penalizing, just flagging)
    - event_lookup_degraded → caution

    Parameters
    ----------
    enriched_data : dict
        Enriched candidate packet.
    event_ctx : dict | None
        Event context from Step 10 (None if unavailable).
    """
    if event_ctx is None:
        return _make_check(
            "event_risk_window",
            CHECK_CAUTION,
            "Event context not available; event risk unknown",
            event_data_available=False,
        )

    risk_flags = event_ctx.get("event_risk_flags", [])
    nearest = event_ctx.get("nearest_relevant_event")
    strategy = enriched_data.get("strategy_type", "")
    nearest_days = nearest.get("days_until") if nearest else None

    # Block: premium-selling + earnings within EARNINGS_BLOCK_DAYS
    if ("earnings_nearby" in risk_flags
            and strategy in PREMIUM_SELLING_STRATEGIES
            and nearest_days is not None
            and 0 <= nearest_days <= EARNINGS_BLOCK_DAYS
            and nearest.get("event_type") == "earnings"):
        return _make_check(
            "event_risk_window",
            CHECK_BLOCK,
            (f"Earnings in {nearest_days}d; "
             f"premium-selling strategy '{strategy}' blocked"),
            nearest_event_type="earnings",
            nearest_days=nearest_days,
            strategy_type=strategy,
            risk_flags=risk_flags,
        )

    # Restrict: premium-selling + earnings within EARNINGS_CAUTION_DAYS
    if ("earnings_nearby" in risk_flags
            and strategy in PREMIUM_SELLING_STRATEGIES
            and nearest_days is not None
            and 0 <= nearest_days <= EARNINGS_CAUTION_DAYS
            and nearest.get("event_type") == "earnings"):
        return _make_check(
            "event_risk_window",
            CHECK_RESTRICT,
            (f"Earnings in {nearest_days}d; "
             f"premium-selling strategy '{strategy}' restricted"),
            nearest_event_type="earnings",
            nearest_days=nearest_days,
            strategy_type=strategy,
            risk_flags=risk_flags,
        )

    # Caution: earnings nearby (non-premium-selling)
    caution_reasons: list[str] = []

    if "earnings_nearby" in risk_flags:
        caution_reasons.append(
            f"Earnings nearby ({nearest_days}d)"
            if nearest_days is not None else "Earnings nearby"
        )

    if "macro_event_nearby" in risk_flags:
        caution_reasons.append("Macro event nearby")

    if "event_window_overlap" in risk_flags:
        caution_reasons.append("Multiple high-relevance events overlap")

    if "no_event_coverage" in risk_flags:
        caution_reasons.append("No event data coverage")

    if "event_lookup_degraded" in risk_flags:
        caution_reasons.append("Event lookup degraded")

    if caution_reasons:
        return _make_check(
            "event_risk_window",
            CHECK_CAUTION,
            "; ".join(caution_reasons),
            risk_flags=risk_flags,
            caution_reasons=caution_reasons,
        )

    return _make_check(
        "event_risk_window",
        CHECK_PASS,
        risk_flags=risk_flags,
    )


def check_same_symbol_overlap(
    enriched_data: dict[str, Any],
    portfolio_ctx: dict[str, Any],
) -> dict[str, Any]:
    """Check for same-symbol active position overlap.

    Rules
    ─────
    active_symbol_positions >= MAX_ACTIVE_SAME_SYMBOL → restrict
    active_symbol_positions > 0 → caution
    """
    active = portfolio_ctx.get("active_symbol_positions", 0)
    symbol = enriched_data.get("symbol")

    if active >= MAX_ACTIVE_SAME_SYMBOL:
        return _make_check(
            "same_symbol_overlap",
            CHECK_RESTRICT,
            (f"Symbol '{symbol}' has {active} active positions "
             f"(limit {MAX_ACTIVE_SAME_SYMBOL})"),
            symbol=symbol,
            active_count=active,
            limit=MAX_ACTIVE_SAME_SYMBOL,
        )
    if active > 0:
        return _make_check(
            "same_symbol_overlap",
            CHECK_CAUTION,
            f"Symbol '{symbol}' has {active} active position(s)",
            symbol=symbol,
            active_count=active,
            limit=MAX_ACTIVE_SAME_SYMBOL,
        )
    return _make_check(
        "same_symbol_overlap",
        CHECK_PASS,
        symbol=symbol,
        active_count=active,
        limit=MAX_ACTIVE_SAME_SYMBOL,
    )


def check_position_count(
    portfolio_ctx: dict[str, Any],
) -> dict[str, Any]:
    """Check total position count against limit.

    Rules
    ─────
    total_active_positions >= MAX_TOTAL_POSITIONS → restrict
    total_active_positions >= MAX_TOTAL_POSITIONS - 2 → caution
    """
    total = portfolio_ctx.get("total_active_positions", 0)

    if total >= MAX_TOTAL_POSITIONS:
        return _make_check(
            "position_count",
            CHECK_RESTRICT,
            f"Portfolio has {total} active positions (limit {MAX_TOTAL_POSITIONS})",
            total_active=total,
            limit=MAX_TOTAL_POSITIONS,
        )
    if total >= MAX_TOTAL_POSITIONS - 2:
        return _make_check(
            "position_count",
            CHECK_CAUTION,
            f"Portfolio has {total} active positions (near limit {MAX_TOTAL_POSITIONS})",
            total_active=total,
            limit=MAX_TOTAL_POSITIONS,
        )
    return _make_check(
        "position_count",
        CHECK_PASS,
        total_active=total,
        limit=MAX_TOTAL_POSITIONS,
    )


def check_capital_limit(
    portfolio_ctx: dict[str, Any],
) -> dict[str, Any]:
    """Check capital utilization against limit.

    Rules
    ─────
    utilization >= MAX_CAPITAL_UTILIZATION_PCT → restrict
    utilization >= MAX_CAPITAL_UTILIZATION_PCT - 10 → caution
    None utilization → unknown (no data)
    """
    util_pct = portfolio_ctx.get("estimated_capital_utilization_pct")

    if util_pct is None:
        return _make_check(
            "capital_limit",
            CHECK_UNKNOWN,
            "Capital utilization data not available",
            utilization_pct=None,
            limit_pct=MAX_CAPITAL_UTILIZATION_PCT,
        )
    if util_pct >= MAX_CAPITAL_UTILIZATION_PCT:
        return _make_check(
            "capital_limit",
            CHECK_RESTRICT,
            (f"Capital utilization {util_pct:.1f}% "
             f"exceeds limit {MAX_CAPITAL_UTILIZATION_PCT:.0f}%"),
            utilization_pct=util_pct,
            limit_pct=MAX_CAPITAL_UTILIZATION_PCT,
        )
    if util_pct >= MAX_CAPITAL_UTILIZATION_PCT - 10:
        return _make_check(
            "capital_limit",
            CHECK_CAUTION,
            (f"Capital utilization {util_pct:.1f}% "
             f"near limit {MAX_CAPITAL_UTILIZATION_PCT:.0f}%"),
            utilization_pct=util_pct,
            limit_pct=MAX_CAPITAL_UTILIZATION_PCT,
        )
    return _make_check(
        "capital_limit",
        CHECK_PASS,
        utilization_pct=util_pct,
        limit_pct=MAX_CAPITAL_UTILIZATION_PCT,
    )


def check_concentration(
    enriched_data: dict[str, Any],
    portfolio_ctx: dict[str, Any],
) -> dict[str, Any]:
    """Check strategy family concentration.

    Rules
    ─────
    active_strategy_positions >= MAX_ACTIVE_SAME_STRATEGY → restrict
    active_strategy_positions >= MAX_ACTIVE_SAME_STRATEGY - 1 → caution
    """
    active_strat = portfolio_ctx.get("active_strategy_positions", 0)
    strategy = enriched_data.get("strategy_type")

    if active_strat >= MAX_ACTIVE_SAME_STRATEGY:
        return _make_check(
            "concentration",
            CHECK_RESTRICT,
            (f"Strategy '{strategy}' has {active_strat} active positions "
             f"(limit {MAX_ACTIVE_SAME_STRATEGY})"),
            strategy_type=strategy,
            active_count=active_strat,
            limit=MAX_ACTIVE_SAME_STRATEGY,
        )
    if active_strat >= MAX_ACTIVE_SAME_STRATEGY - 1:
        return _make_check(
            "concentration",
            CHECK_CAUTION,
            (f"Strategy '{strategy}' has {active_strat} active positions "
             f"(near limit {MAX_ACTIVE_SAME_STRATEGY})"),
            strategy_type=strategy,
            active_count=active_strat,
            limit=MAX_ACTIVE_SAME_STRATEGY,
        )
    return _make_check(
        "concentration",
        CHECK_PASS,
        strategy_type=strategy,
        active_count=active_strat,
        limit=MAX_ACTIVE_SAME_STRATEGY,
    )


def check_event_coverage(
    event_ctx: dict[str, Any] | None,
) -> dict[str, Any]:
    """Check event data coverage quality.

    Rules
    ─────
    event_ctx is None → caution (data not yet available)
    event_status == "failed" → caution
    downstream_usable == False → caution
    Otherwise → pass
    """
    if event_ctx is None:
        return _make_check(
            "event_coverage",
            CHECK_CAUTION,
            "Event context not available for this candidate",
            event_data_available=False,
        )

    event_status = event_ctx.get("event_status")
    downstream = event_ctx.get("downstream_usable", False)

    if event_status == "failed":
        return _make_check(
            "event_coverage",
            CHECK_CAUTION,
            "Event context lookup failed for this candidate",
            event_status=event_status,
        )
    if not downstream:
        return _make_check(
            "event_coverage",
            CHECK_CAUTION,
            "Event context not downstream usable",
            event_status=event_status,
            downstream_usable=downstream,
        )
    return _make_check(
        "event_coverage",
        CHECK_PASS,
        event_status=event_status,
        downstream_usable=downstream,
    )


# =====================================================================
#  Outcome derivation
# =====================================================================

def derive_overall_outcome(checks: list[dict[str, Any]]) -> str:
    """Derive the overall policy outcome from check results.

    Deterministic derivation
    ────────────────────────
    1. If any check_status == "block" → blocked
    2. If any check_status == "restrict" → restricted
    3. If any check_status in ("caution", "unknown") → eligible_with_cautions
    4. Otherwise → eligible

    Parameters
    ----------
    checks : list[dict]
        List of policy check results.

    Returns
    -------
    str
        One of OUTCOME_ELIGIBLE / OUTCOME_ELIGIBLE_WITH_CAUTIONS /
        OUTCOME_RESTRICTED / OUTCOME_BLOCKED.
    """
    statuses = [c.get("check_status") for c in checks]

    if CHECK_BLOCK in statuses:
        return OUTCOME_BLOCKED
    if CHECK_RESTRICT in statuses:
        return OUTCOME_RESTRICTED
    if CHECK_CAUTION in statuses or CHECK_UNKNOWN in statuses:
        return OUTCOME_ELIGIBLE_WITH_CAUTIONS
    return OUTCOME_ELIGIBLE


def _collect_reasons(
    checks: list[dict[str, Any]],
    status_filter: str,
) -> list[str]:
    """Collect reason strings from checks matching a status."""
    return [
        c["reason"] for c in checks
        if c.get("check_status") == status_filter and c.get("reason")
    ]


# =====================================================================
#  Policy evaluation — core function
# =====================================================================

def evaluate_policy(
    enriched_data: dict[str, Any],
    event_ctx: dict[str, Any] | None,
    portfolio_ctx: dict[str, Any],
    run_id: str,
    enriched_artifact_ref: str | None = None,
    event_artifact_ref: str | None = None,
) -> dict[str, Any]:
    """Run all policy checks and produce structured policy output.

    Parameters
    ----------
    enriched_data : dict
        Per-candidate enriched packet from Step 9.
    event_ctx : dict | None
        Per-candidate event context from Step 10 (None if unavailable).
    portfolio_ctx : dict
        Per-candidate portfolio context from portfolio provider.
    run_id : str
        Pipeline run ID.
    enriched_artifact_ref : str | None
        Artifact ID of the enriched packet.
    event_artifact_ref : str | None
        Artifact ID of the event context.

    Returns
    -------
    dict
        Structured policy evaluation with all contract fields.
    """
    candidate_id = enriched_data.get("candidate_id")
    symbol = enriched_data.get("symbol")

    # Run all checks
    checks = [
        check_required_fields(enriched_data),
        check_trade_capability(portfolio_ctx),
        check_strategy_allowed(enriched_data, portfolio_ctx),
        check_event_risk_window(enriched_data, event_ctx),
        check_same_symbol_overlap(enriched_data, portfolio_ctx),
        check_position_count(portfolio_ctx),
        check_capital_limit(portfolio_ctx),
        check_concentration(enriched_data, portfolio_ctx),
        check_event_coverage(event_ctx),
    ]

    overall_outcome = derive_overall_outcome(checks)
    blocking_reasons = _collect_reasons(checks, CHECK_BLOCK)
    caution_reasons = _collect_reasons(checks, CHECK_CAUTION)
    restriction_reasons = _collect_reasons(checks, CHECK_RESTRICT)
    unknown_reasons = _collect_reasons(checks, CHECK_UNKNOWN)

    # Eligibility flags — compact boolean summary
    check_by_name = {c["check_name"]: c for c in checks}
    eligibility_flags = {
        "trade_capable": check_by_name.get(
            "trade_capability", {},
        ).get("check_status") in (CHECK_PASS, CHECK_UNKNOWN),
        "strategy_allowed": check_by_name.get(
            "strategy_allowed", {},
        ).get("check_status") == CHECK_PASS,
        "within_capital_limits": check_by_name.get(
            "capital_limit", {},
        ).get("check_status") in (CHECK_PASS, CHECK_UNKNOWN),
        "within_position_limits": check_by_name.get(
            "position_count", {},
        ).get("check_status") == CHECK_PASS,
        "no_symbol_overlap": check_by_name.get(
            "same_symbol_overlap", {},
        ).get("check_status") == CHECK_PASS,
        "event_risk_acceptable": check_by_name.get(
            "event_risk_window", {},
        ).get("check_status") in (CHECK_PASS, CHECK_CAUTION),
    }

    # Portfolio context summary — compact for downstream
    portfolio_summary = {
        "snapshot_status": portfolio_ctx.get("portfolio_snapshot_status"),
        "trade_capability_status": portfolio_ctx.get(
            "trade_capability_status",
        ),
        "active_symbol_positions": portfolio_ctx.get(
            "active_symbol_positions",
        ),
        "total_active_positions": portfolio_ctx.get(
            "total_active_positions",
        ),
        "capital_utilization_pct": portfolio_ctx.get(
            "estimated_capital_utilization_pct",
        ),
        "restriction_count": len(
            portfolio_ctx.get("restriction_flags", []),
        ),
    }

    # Event risk summary — compact for downstream
    event_risk_summary: dict[str, Any] = {}
    if event_ctx is not None:
        event_summary = event_ctx.get("event_summary", {})
        event_risk_summary = {
            "event_data_available": True,
            "event_status": event_ctx.get("event_status"),
            "total_events": event_summary.get("total_events", 0),
            "risk_flag_count": event_summary.get("risk_flag_count", 0),
            "nearest_event_type": event_summary.get("nearest_event_type"),
            "nearest_days_until": event_summary.get("nearest_days_until"),
            "risk_flags": event_ctx.get("event_risk_flags", []),
        }
    else:
        event_risk_summary = {
            "event_data_available": False,
            "event_status": None,
            "total_events": 0,
            "risk_flag_count": 0,
            "nearest_event_type": None,
            "nearest_days_until": None,
            "risk_flags": [],
        }

    # Degraded reasons — from portfolio or event coverage
    degraded_reasons = list(portfolio_ctx.get("degraded_reasons", []))
    if event_ctx is None:
        degraded_reasons.append("event context not available")

    # Determine policy status
    portfolio_snapshot = portfolio_ctx.get("portfolio_snapshot_status")
    if portfolio_snapshot in ("degraded", "no_data"):
        policy_status = POLICY_STATUS_EVALUATED_DEGRADED
    elif event_ctx is None:
        policy_status = POLICY_STATUS_EVALUATED_DEGRADED
    else:
        policy_status = POLICY_STATUS_EVALUATED

    blocking_count = len(blocking_reasons)
    caution_count = len(caution_reasons) + len(unknown_reasons)
    restriction_count = len(restriction_reasons)

    downstream_usable = overall_outcome != OUTCOME_FAILED

    return {
        "policy_version": _POLICY_VERSION,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "symbol": symbol,
        "source_enriched_candidate_ref": enriched_artifact_ref,
        "source_event_context_ref": event_artifact_ref,
        "policy_status": policy_status,
        "overall_outcome": overall_outcome,
        "checks": checks,
        "blocking_reasons": blocking_reasons,
        "caution_reasons": caution_reasons,
        "restriction_reasons": restriction_reasons,
        "eligibility_flags": eligibility_flags,
        "portfolio_context_summary": portfolio_summary,
        "event_risk_summary": event_risk_summary,
        "downstream_usable": downstream_usable,
        "degraded_reasons": degraded_reasons,
        "policy_metadata": {
            "thresholds_used": {
                "max_active_same_symbol": MAX_ACTIVE_SAME_SYMBOL,
                "max_active_same_strategy": MAX_ACTIVE_SAME_STRATEGY,
                "max_total_positions": MAX_TOTAL_POSITIONS,
                "max_capital_utilization_pct": MAX_CAPITAL_UTILIZATION_PCT,
                "earnings_block_days": EARNINGS_BLOCK_DAYS,
                "earnings_caution_days": EARNINGS_CAUTION_DAYS,
                "macro_caution_days": MACRO_CAUTION_DAYS,
            },
            "check_count": len(checks),
            "blocking_count": blocking_count,
            "caution_count": caution_count,
            "restriction_count": restriction_count,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
#  Event emission helper
# =====================================================================

def _make_event_emitter(
    run: dict[str, Any],
    event_callback: Callable[..., None] | None,
) -> Callable[..., None] | None:
    """Build an event emitter closure for policy stage events."""
    if event_callback is None:
        return None

    run_id = run["run_id"]

    def _emit(
        event_type: str,
        level: str = "info",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        merged_meta: dict[str, Any] = {"stage_key": _STAGE_KEY}
        if metadata:
            merged_meta.update(metadata)

        event = build_log_event(
            run_id=run_id,
            stage_key=_STAGE_KEY,
            event_type=event_type,
            level=level,
            message=message,
            metadata=merged_meta,
        )

        counts = run.get("log_event_counts", {})
        counts["total"] = counts.get("total", 0) + 1
        by_level = counts.get("by_level", {})
        by_level[level] = by_level.get(level, 0) + 1

        try:
            event_callback(event)
        except Exception:
            logger.warning(
                "Event callback raised during policy event '%s'",
                event_type, exc_info=True,
            )

    return _emit


# =====================================================================
#  Upstream artifact retrieval
# =====================================================================

def _retrieve_enrichment_summary(
    artifact_store: dict[str, Any],
) -> dict[str, Any] | None:
    """Retrieve candidate_enrichment_summary from Step 9."""
    art = get_artifact_by_key(
        artifact_store, "candidate_enrichment",
        "candidate_enrichment_summary",
    )
    if art is None:
        return None
    return art.get("data") or {}


def _retrieve_enriched_candidate(
    artifact_store: dict[str, Any],
    candidate_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Retrieve a per-candidate enriched packet from Step 9.

    Returns ``(enriched_data, artifact_id)`` or ``(None, None)``.
    """
    art = get_artifact_by_key(
        artifact_store, "candidate_enrichment",
        f"enriched_{candidate_id}",
    )
    if art is None:
        return None, None
    return art.get("data") or {}, art.get("artifact_id")


def _retrieve_event_context(
    artifact_store: dict[str, Any],
    candidate_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Retrieve per-candidate event context from Step 10.

    Returns ``(event_data, artifact_id)`` or ``(None, None)``.
    """
    art = get_artifact_by_key(
        artifact_store, "events", f"event_{candidate_id}",
    )
    if art is None:
        return None, None
    return art.get("data") or {}, art.get("artifact_id")


# =====================================================================
#  Per-candidate execution record builder
# =====================================================================

def _build_execution_record(
    candidate_id: str | None,
    symbol: str | None,
    policy_status: str,
    overall_outcome: str,
    source_enriched_candidate_ref: str | None,
    source_event_context_ref: str | None,
    policy_output: dict[str, Any] | None,
    output_artifact_ref: str | None,
    elapsed_ms: int,
    error_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a per-candidate execution record for the stage summary."""
    meta = (policy_output or {}).get("policy_metadata", {})
    return {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "policy_status": policy_status,
        "overall_outcome": overall_outcome,
        "check_count": meta.get("check_count", 0),
        "blocking_count": meta.get("blocking_count", 0),
        "caution_count": meta.get("caution_count", 0),
        "restriction_count": meta.get("restriction_count", 0),
        "source_enriched_candidate_ref": source_enriched_candidate_ref,
        "source_event_context_ref": source_event_context_ref,
        "output_artifact_ref": output_artifact_ref,
        "downstream_usable": (
            policy_output or {}
        ).get("downstream_usable", False),
        "degraded_reasons": (
            policy_output or {}
        ).get("degraded_reasons", []),
        "elapsed_ms": elapsed_ms,
        "error": error_info,
    }


# =====================================================================
#  Stage summary builder
# =====================================================================

def _build_stage_summary(
    *,
    stage_status: str,
    total_candidates_in: int,
    execution_records: list[dict[str, Any]],
    output_artifact_refs: dict[str, str],
    outcome_counts: dict[str, int],
    blocking_reason_counts: dict[str, int],
    caution_reason_counts: dict[str, int],
    elapsed_ms: int,
    total_evaluated: int = 0,
    total_eligible: int = 0,
    total_eligible_with_cautions: int = 0,
    total_restricted: int = 0,
    total_blocked: int = 0,
    total_failed: int = 0,
) -> dict[str, Any]:
    """Build the policy stage summary dict."""
    return {
        "stage_key": _STAGE_KEY,
        "stage_status": stage_status,
        "total_candidates_in": total_candidates_in,
        "total_evaluated": total_evaluated,
        "total_eligible": total_eligible,
        "total_eligible_with_cautions": total_eligible_with_cautions,
        "total_restricted": total_restricted,
        "total_blocked": total_blocked,
        "total_failed": total_failed,
        "candidate_ids_processed": [
            r.get("candidate_id") for r in execution_records
        ],
        "output_artifact_refs": output_artifact_refs,
        "outcome_counts": outcome_counts,
        "blocking_reason_counts": blocking_reason_counts,
        "caution_reason_counts": caution_reason_counts,
        "execution_records": execution_records,
        "degraded_reasons": [
            reason
            for r in execution_records
            for reason in r.get("degraded_reasons", [])
        ],
        "summary_artifact_ref": None,  # filled after write
        "elapsed_ms": elapsed_ms,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# =====================================================================
#  Artifact writers
# =====================================================================

def _write_policy_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    candidate_id: str | None,
    policy_output: dict[str, Any],
) -> str:
    """Write one policy_output artifact.  Returns artifact_id."""
    artifact_key = (
        f"policy_{candidate_id}" if candidate_id else "policy_unknown"
    )

    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key=artifact_key,
        artifact_type="policy_output",
        data=policy_output,
        candidate_id=candidate_id,
        summary={
            "candidate_id": candidate_id,
            "symbol": policy_output.get("symbol"),
            "overall_outcome": policy_output.get("overall_outcome"),
            "blocking_count": policy_output.get(
                "policy_metadata", {},
            ).get("blocking_count", 0),
            "downstream_usable": policy_output.get("downstream_usable"),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


def _write_policy_summary_artifact(
    artifact_store: dict[str, Any],
    run_id: str,
    summary: dict[str, Any],
) -> str:
    """Write the policy_stage_summary artifact.  Returns artifact_id."""
    art = build_artifact_record(
        run_id=run_id,
        stage_key=_STAGE_KEY,
        artifact_key="policy_stage_summary",
        artifact_type="policy_stage_summary",
        data=summary,
        summary={
            "stage_status": summary.get("stage_status"),
            "total_evaluated": summary.get("total_evaluated"),
            "total_eligible": summary.get("total_eligible"),
            "total_blocked": summary.get("total_blocked"),
            "total_failed": summary.get("total_failed"),
        },
        metadata={"stage_key": _STAGE_KEY},
    )
    put_artifact(artifact_store, art, overwrite=True)
    return art["artifact_id"]


# =====================================================================
#  Stage handler — public entry point
# =====================================================================

def portfolio_policy_handler(
    run: dict[str, Any],
    artifact_store: dict[str, Any],
    stage_key: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Portfolio context + decision policy stage handler (Step 11).

    Retrieves enriched candidate packets (Step 9) and event context
    (Step 10), gathers portfolio context via injectable provider,
    applies deterministic policy checks, and writes per-candidate
    policy artifacts plus stage summary.

    Parameters
    ----------
    run : dict
        The pipeline run dict (passed by orchestrator).
    artifact_store : dict
        The artifact store (passed by orchestrator).
    stage_key : str
        Expected to be "policy".
    **kwargs
        event_callback : callable | None
            Optional event callback for structured events.
        portfolio_provider : PortfolioProvider | None
            Injectable portfolio/risk provider.  Defaults to
            default_portfolio_provider.

    Returns
    -------
    dict[str, Any]
        Handler result: { outcome, summary_counts, artifacts,
        metadata, error }
    """
    t0 = time.monotonic()
    run_id = run["run_id"]

    # ── 1. Resolve parameters ───────────────────────────────────
    event_callback = kwargs.get("event_callback")
    emit = _make_event_emitter(run, event_callback)
    provider: PortfolioProvider = (
        kwargs.get("portfolio_provider") or default_portfolio_provider
    )

    # ── 2. Emit policy_evaluation_started ───────────────────────
    if emit:
        emit(
            "policy_evaluation_started",
            message="Policy evaluation stage started",
        )

    # ── 3. Retrieve enrichment summary ──────────────────────────
    try:
        enrichment_summary = _retrieve_enrichment_summary(artifact_store)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.error(
            "Policy stage failed during upstream retrieval: %s",
            exc, exc_info=True,
        )
        if emit:
            emit(
                "policy_evaluation_failed",
                level="error",
                message=f"Upstream retrieval failed: {exc}",
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="POLICY_UPSTREAM_ERROR",
                message=f"Failed to retrieve upstream artifacts: {exc}",
                source=_STAGE_KEY,
            ),
        }

    # ── 4. Extract candidate IDs ────────────────────────────────
    if enrichment_summary is None:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("No candidate_enrichment_summary found")
        if emit:
            emit(
                "policy_evaluation_failed",
                level="error",
                message="No candidate enrichment summary found",
            )
        return {
            "outcome": "failed",
            "summary_counts": _empty_summary_counts(),
            "artifacts": [],
            "metadata": {"elapsed_ms": elapsed_ms},
            "error": build_run_error(
                code="NO_CANDIDATE_SOURCE",
                message="candidate_enrichment_summary not found",
                source=_STAGE_KEY,
            ),
        }

    enrichment_records = enrichment_summary.get("enrichment_records", [])
    candidate_ids = [
        r.get("candidate_id") for r in enrichment_records
        if r.get("candidate_id")
    ]

    if not candidate_ids:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return _vacuous_completion(
            artifact_store, run_id, emit, elapsed_ms,
            note="Zero enriched candidates",
        )

    # ── 5. Process each candidate ───────────────────────────────
    execution_records: list[dict[str, Any]] = []
    output_artifact_refs: dict[str, str] = {}
    outcome_counts: dict[str, int] = {}
    blocking_reason_counts: dict[str, int] = {}
    caution_reason_counts: dict[str, int] = {}
    total_eligible = 0
    total_eligible_with_cautions = 0
    total_restricted = 0
    total_blocked = 0
    total_failed = 0

    for cand_id in candidate_ids:
        cand_t0 = time.monotonic()

        # Retrieve enriched packet (Step 9)
        enriched_data, enriched_art_id = _retrieve_enriched_candidate(
            artifact_store, cand_id,
        )

        if enriched_data is None:
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            total_failed += 1
            execution_records.append(_build_execution_record(
                candidate_id=cand_id,
                symbol=None,
                policy_status=POLICY_STATUS_FAILED,
                overall_outcome=OUTCOME_FAILED,
                source_enriched_candidate_ref=None,
                source_event_context_ref=None,
                policy_output=None,
                output_artifact_ref=None,
                elapsed_ms=cand_elapsed,
                error_info={
                    "code": "ENRICHED_PACKET_MISSING",
                    "message": f"No enriched packet for {cand_id}",
                },
            ))
            continue

        symbol = enriched_data.get("symbol")

        # Retrieve event context (Step 10) — optional/opportunistic
        event_data, event_art_id = _retrieve_event_context(
            artifact_store, cand_id,
        )

        # Call portfolio provider
        try:
            lookup_input = {
                "symbol": symbol,
                "strategy_type": enriched_data.get("strategy_type"),
                "scanner_family": enriched_data.get("scanner_family"),
                "direction": enriched_data.get("direction"),
                "candidate_id": cand_id,
            }
            provider_result = provider(lookup_input)
        except Exception as exc:
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            total_failed += 1
            logger.error(
                "Portfolio provider failed for candidate %s: %s",
                cand_id, exc, exc_info=True,
            )
            execution_records.append(_build_execution_record(
                candidate_id=cand_id,
                symbol=symbol,
                policy_status=POLICY_STATUS_FAILED,
                overall_outcome=OUTCOME_FAILED,
                source_enriched_candidate_ref=enriched_art_id,
                source_event_context_ref=event_art_id,
                policy_output=None,
                output_artifact_ref=None,
                elapsed_ms=int((time.monotonic() - cand_t0) * 1000),
                error_info={
                    "code": "PORTFOLIO_PROVIDER_ERROR",
                    "message": str(exc),
                },
            ))
            continue

        # Build portfolio context + evaluate policy + write artifact
        try:
            portfolio_ctx = build_portfolio_context(
                enriched_data, provider_result,
            )

            policy_output = evaluate_policy(
                enriched_data=enriched_data,
                event_ctx=event_data,
                portfolio_ctx=portfolio_ctx,
                run_id=run_id,
                enriched_artifact_ref=enriched_art_id,
                event_artifact_ref=event_art_id,
            )

            art_id = _write_policy_artifact(
                artifact_store, run_id, cand_id, policy_output,
            )
            output_artifact_refs[cand_id] = art_id
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)

            outcome = policy_output["overall_outcome"]
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

            if outcome == OUTCOME_ELIGIBLE:
                total_eligible += 1
            elif outcome == OUTCOME_ELIGIBLE_WITH_CAUTIONS:
                total_eligible_with_cautions += 1
            elif outcome == OUTCOME_RESTRICTED:
                total_restricted += 1
            elif outcome == OUTCOME_BLOCKED:
                total_blocked += 1
            else:
                total_failed += 1

            # Accumulate reason counts
            for reason in policy_output.get("blocking_reasons", []):
                blocking_reason_counts[reason] = (
                    blocking_reason_counts.get(reason, 0) + 1
                )
            for reason in policy_output.get("caution_reasons", []):
                caution_reason_counts[reason] = (
                    caution_reason_counts.get(reason, 0) + 1
                )

            execution_records.append(_build_execution_record(
                candidate_id=cand_id,
                symbol=symbol,
                policy_status=policy_output["policy_status"],
                overall_outcome=outcome,
                source_enriched_candidate_ref=enriched_art_id,
                source_event_context_ref=event_art_id,
                policy_output=policy_output,
                output_artifact_ref=art_id,
                elapsed_ms=cand_elapsed,
            ))

        except Exception as exc:
            cand_elapsed = int((time.monotonic() - cand_t0) * 1000)
            total_failed += 1
            logger.error(
                "Policy evaluation failed for candidate %s: %s",
                cand_id, exc, exc_info=True,
            )
            execution_records.append(_build_execution_record(
                candidate_id=cand_id,
                symbol=symbol,
                policy_status=POLICY_STATUS_FAILED,
                overall_outcome=OUTCOME_FAILED,
                source_enriched_candidate_ref=enriched_art_id,
                source_event_context_ref=event_art_id,
                policy_output=None,
                output_artifact_ref=None,
                elapsed_ms=cand_elapsed,
                error_info={
                    "code": "POLICY_EVALUATION_ERROR",
                    "message": str(exc),
                },
            ))

    # ── 6. Compute stage status ─────────────────────────────────
    total_evaluated = (
        total_eligible + total_eligible_with_cautions
        + total_restricted + total_blocked
    )

    if total_failed > 0 and total_evaluated == 0:
        stage_status = "failed"
    elif total_failed > 0:
        stage_status = "degraded"
    else:
        stage_status = "success"

    # ── 7. Build and write stage summary ────────────────────────
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    summary = _build_stage_summary(
        stage_status=stage_status,
        total_candidates_in=len(candidate_ids),
        execution_records=execution_records,
        output_artifact_refs=output_artifact_refs,
        outcome_counts=outcome_counts,
        blocking_reason_counts=blocking_reason_counts,
        caution_reason_counts=caution_reason_counts,
        elapsed_ms=elapsed_ms,
        total_evaluated=total_evaluated,
        total_eligible=total_eligible,
        total_eligible_with_cautions=total_eligible_with_cautions,
        total_restricted=total_restricted,
        total_blocked=total_blocked,
        total_failed=total_failed,
    )
    summary_art_id = _write_policy_summary_artifact(
        artifact_store, run_id, summary,
    )
    summary["summary_artifact_ref"] = summary_art_id

    # ── 8. Determine outcome ────────────────────────────────────
    if stage_status == "failed":
        if emit:
            emit(
                "policy_evaluation_failed",
                level="error",
                message=(
                    f"Policy evaluation failed: "
                    f"{total_failed}/{len(candidate_ids)} candidates failed"
                ),
                metadata={
                    "total_failed": total_failed,
                    "total_evaluated": total_evaluated,
                },
            )
        return {
            "outcome": "failed",
            "summary_counts": {
                "total_evaluated": total_evaluated,
                "total_eligible": total_eligible,
                "total_eligible_with_cautions": total_eligible_with_cautions,
                "total_restricted": total_restricted,
                "total_blocked": total_blocked,
                "total_failed": total_failed,
            },
            "artifacts": [],
            "metadata": {
                "stage_status": stage_status,
                "stage_summary": summary,
                "elapsed_ms": elapsed_ms,
            },
            "error": build_run_error(
                code="POLICY_ALL_FAILED",
                message=(
                    f"All {total_failed} candidates failed "
                    f"policy evaluation"
                ),
                source=_STAGE_KEY,
            ),
        }

    # ── 9. Emit success / degraded ──────────────────────────────
    if emit:
        emit(
            "policy_evaluation_completed",
            message=(
                f"Policy evaluation completed: "
                f"{total_evaluated}/{len(candidate_ids)} evaluated"
                + (f" ({total_blocked} blocked)" if total_blocked else "")
                + (f" ({total_restricted} restricted)"
                   if total_restricted else "")
                + (f" ({total_eligible_with_cautions} cautions)"
                   if total_eligible_with_cautions else "")
            ),
            metadata={
                "total_evaluated": total_evaluated,
                "total_eligible": total_eligible,
                "total_eligible_with_cautions": total_eligible_with_cautions,
                "total_restricted": total_restricted,
                "total_blocked": total_blocked,
                "total_failed": total_failed,
                "outcome_counts": outcome_counts,
            },
        )

    return {
        "outcome": "completed",
        "summary_counts": {
            "total_evaluated": total_evaluated,
            "total_eligible": total_eligible,
            "total_eligible_with_cautions": total_eligible_with_cautions,
            "total_restricted": total_restricted,
            "total_blocked": total_blocked,
            "total_failed": total_failed,
        },
        "artifacts": [],
        "metadata": {
            "stage_status": stage_status,
            "stage_summary": summary,
            "summary_artifact_id": summary_art_id,
            "output_artifact_refs": output_artifact_refs,
            "outcome_counts": outcome_counts,
            "blocking_reason_counts": blocking_reason_counts,
            "caution_reason_counts": caution_reason_counts,
            "elapsed_ms": elapsed_ms,
        },
        "error": None,
    }


# =====================================================================
#  Internal helpers
# =====================================================================

def _empty_summary_counts() -> dict[str, int]:
    """Return zeroed summary_counts dict."""
    return {
        "total_evaluated": 0,
        "total_eligible": 0,
        "total_eligible_with_cautions": 0,
        "total_restricted": 0,
        "total_blocked": 0,
        "total_failed": 0,
    }


def _vacuous_completion(
    artifact_store: dict[str, Any],
    run_id: str,
    emit: Callable[..., None] | None,
    elapsed_ms: int,
    note: str = "",
) -> dict[str, Any]:
    """Handle vacuous-completion path (no candidates to process)."""
    if emit:
        emit(
            "policy_evaluation_completed",
            message=f"Policy evaluation vacuous completion: {note}",
            metadata={"total_evaluated": 0},
        )

    summary = _build_stage_summary(
        stage_status="no_candidates_to_process",
        total_candidates_in=0,
        execution_records=[],
        output_artifact_refs={},
        outcome_counts={},
        blocking_reason_counts={},
        caution_reason_counts={},
        elapsed_ms=elapsed_ms,
    )
    summary_art_id = _write_policy_summary_artifact(
        artifact_store, run_id, summary,
    )
    summary["summary_artifact_ref"] = summary_art_id

    return {
        "outcome": "completed",
        "summary_counts": _empty_summary_counts(),
        "artifacts": [],
        "metadata": {
            "stage_status": "no_candidates_to_process",
            "stage_summary": summary,
            "elapsed_ms": elapsed_ms,
        },
        "error": None,
    }
