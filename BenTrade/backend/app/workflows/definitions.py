"""Workflow definitions — IDs, ownership boundaries, and stage maps.

This module is the single reference for what each workflow owns, its
ordered stages, and the boundaries between workflows.

Greenfield design — does NOT reference archived pipeline code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

# ── Workflow identifiers ──────────────────────────────────────────────

WORKFLOW_VERSION = "1.0"


class WorkflowID(str, Enum):
    """Canonical workflow identifiers."""

    MARKET_INTELLIGENCE = "market_intelligence"
    STOCK_OPPORTUNITY = "stock_opportunity"
    OPTIONS_OPPORTUNITY = "options_opportunity"
    ACTIVE_TRADE = "active_trade"


# ── Stage definitions ─────────────────────────────────────────────────
# Each stage is a named step that produces one inspectable JSON artifact.
# Stages run sequentially within a workflow (no hidden parallelism).


@dataclass(frozen=True)
class StageSpec:
    """Immutable specification for a single workflow stage."""

    key: str
    label: str
    description: str
    produces_artifact: bool = True


# ── Workflow A — Market Intelligence Producer ─────────────────────────
#
# Runs on a 5-minute schedule.  Produces a single market_state package
# that downstream workflows consume.  This is the *only* workflow that
# talks to market-data providers and market engines.
#
# Ownership:
#   - Market data collection (Tradier quotes, FRED macro, Finnhub)
#   - Six market engines (breadth, volatility, macro, flows, liquidity, sentiment)
#   - Model-driven market interpretation
#   - Freshness / source-health / degradation metadata
#   - Publication of latest valid market-state artifact
#
# Does NOT own:
#   - Scanner execution
#   - Trade candidate generation
#   - Portfolio policy evaluation
#   - Trade recommendation or decisioning

MARKET_INTELLIGENCE_STAGES: tuple[StageSpec, ...] = (
    StageSpec(
        key="collect",
        label="Data Collection",
        description=(
            "Fetch current market data from all providers "
            "(Tradier, FRED, Finnhub, Polygon).  Record source "
            "freshness and availability per provider."
        ),
    ),
    StageSpec(
        key="engine_run",
        label="Engine Execution",
        description=(
            "Run six market engines: breadth, volatility_options, "
            "cross_asset_macro, flows_positioning, liquidity_conditions, "
            "news_sentiment.  Each engine produces a normalized envelope "
            "via engine_output_contract."
        ),
    ),
    StageSpec(
        key="model_interpret",
        label="Market Model Interpretation",
        description=(
            "Run LLM-driven market interpretation against the assembled "
            "engine results.  Produces structured model analysis payload."
        ),
    ),
    StageSpec(
        key="composite",
        label="Composite & Conflict Detection",
        description=(
            "Build market composite (risk_on / neutral / risk_off, "
            "support_state, stability_state).  Detect engine-to-engine "
            "contradictions via conflict_detector."
        ),
    ),
    StageSpec(
        key="publish",
        label="Package & Publish",
        description=(
            "Assemble the final market_state package with all engine "
            "results, composite, model output, and quality metadata.  "
            "Write to the source-of-truth location as the latest valid "
            "market state."
        ),
    ),
)


# ── Workflow B — Stock Opportunity Workflow ────────────────────────────
#
# Runs on-demand (via Trade Management Center or scheduled trigger).
# Consumes the latest valid market-state artifact.
#
# Ownership:
#   - Stock scanner execution (momentum, pullback, mean reversion, vol expansion)
#   - Stock candidate normalization
#   - Stock-specific enrichment and evaluation
#   - Stock opportunity output packaging
#
# Does NOT own:
#   - Market data fetching (uses market_state artifact)
#   - Options chain analysis
#   - Trade execution

STOCK_OPPORTUNITY_STAGES: tuple[StageSpec, ...] = (
    StageSpec(
        key="load_market_state",
        label="Load Market State",
        description=(
            "Read the latest valid market_state artifact published by "
            "Market Intelligence.  Verify freshness; abort or degrade "
            "if stale beyond threshold."
        ),
    ),
    StageSpec(
        key="scan",
        label="Stock Scanner Execution",
        description=(
            "Run stock scanners (momentum_breakout, pullback_swing, "
            "mean_reversion, volatility_expansion) against configured "
            "symbol universe."
        ),
    ),
    StageSpec(
        key="normalize",
        label="Candidate Normalization",
        description=(
            "Normalize scanner output into canonical candidate format "
            "via scanner_candidate_contract."
        ),
    ),
    StageSpec(
        key="enrich_evaluate",
        label="Enrichment & Evaluation",
        description=(
            "Attach market context from loaded market_state, compute "
            "per-candidate quality scores, apply ranking."
        ),
    ),
    StageSpec(
        key="select_package",
        label="Selection & Packaging",
        description=(
            "Apply selection caps, write final stock opportunity output "
            "as inspectable JSON artifact."
        ),
    ),
)


# ── Workflow C — Options Opportunity Workflow ──────────────────────────
#
# Runs on-demand (via Trade Management Center or scheduled trigger).
# Consumes the latest valid market-state artifact.
#
# Ownership:
#   - Options scanner execution (V2 families: credit spreads, debit spreads,
#     iron condors, butterflies, calendars/diagonals)
#   - Full quote validation and trust hygiene
#   - EV / PoP / Greeks / risk-width recomputed math
#   - Per-contract normalization via scanner_candidate_contract
#   - Options opportunity output packaging
#
# Does NOT own:
#   - Market data fetching (uses market_state artifact)
#   - Stock scanning
#   - Trade execution

OPTIONS_OPPORTUNITY_STAGES: tuple[StageSpec, ...] = (
    StageSpec(
        key="load_market_state",
        label="Load Market State",
        description=(
            "Read the latest valid market_state artifact published by "
            "Market Intelligence.  Verify freshness; abort or degrade "
            "if stale beyond threshold."
        ),
    ),
    StageSpec(
        key="scan",
        label="Options Scanner Execution",
        description=(
            "Run V2 options scanners across configured symbol universe.  "
            "Each scanner family produces candidates with full chain data, "
            "quote validation, and trust-hygiene annotation."
        ),
    ),
    StageSpec(
        key="validate_math",
        label="Validation & Recomputed Math",
        description=(
            "Structural validation (legs, expirations, DTE).  "
            "Recomputed math: credit/debit, max_loss, max_gain, EV, PoP, "
            "break-even, IV/RV ratio, expected fill.  "
            "Trust-hygiene scoring per candidate."
        ),
    ),
    StageSpec(
        key="enrich_evaluate",
        label="Enrichment & Evaluation",
        description=(
            "Attach market context.  Apply EV gates, PoP thresholds, "
            "liquidity/OI gates.  Compute per-candidate quality/ranking "
            "scores.  Full filter trace with rejection reason codes."
        ),
    ),
    StageSpec(
        key="select_package",
        label="Selection & Packaging",
        description=(
            "Apply selection caps, deduplication.  Write final options "
            "opportunity output as inspectable JSON artifact with "
            "per-contract metrics and filter trace."
        ),
    ),
)


# ── Workflow D — Active Trade (out of scope) ──────────────────────────
#
# The Active Trade workflow is UNCHANGED and OUT OF SCOPE for the
# current rebuild phase.  It continues to use:
#   - active_trade_pipeline.py
#   - active_trade_monitor_service.py
#   - routes_active_trade_pipeline.py / routes_active_trades.py
#
# Future prompts may align it with the new architecture, but it is
# explicitly excluded from this workflow pivot.

ACTIVE_TRADE_STAGES: tuple[StageSpec, ...] = ()  # intentionally empty — not redefined


# ── Lookup helpers ────────────────────────────────────────────────────

WORKFLOW_STAGES: dict[WorkflowID, tuple[StageSpec, ...]] = {
    WorkflowID.MARKET_INTELLIGENCE: MARKET_INTELLIGENCE_STAGES,
    WorkflowID.STOCK_OPPORTUNITY: STOCK_OPPORTUNITY_STAGES,
    WorkflowID.OPTIONS_OPPORTUNITY: OPTIONS_OPPORTUNITY_STAGES,
    WorkflowID.ACTIVE_TRADE: ACTIVE_TRADE_STAGES,
}


def get_stage_keys(workflow_id: WorkflowID) -> tuple[str, ...]:
    """Return ordered stage keys for a workflow."""
    return tuple(s.key for s in WORKFLOW_STAGES[workflow_id])


def get_stage_spec(workflow_id: WorkflowID, stage_key: str) -> StageSpec | None:
    """Look up a single stage spec by workflow + key."""
    for s in WORKFLOW_STAGES[workflow_id]:
        if s.key == stage_key:
            return s
    return None


# ── Ownership summary (for programmatic checks) ──────────────────────

@dataclass(frozen=True)
class WorkflowOwnership:
    """Declares what a workflow owns and what it does NOT own."""

    workflow_id: WorkflowID
    owns: tuple[str, ...]
    does_not_own: tuple[str, ...]


OWNERSHIP: dict[WorkflowID, WorkflowOwnership] = {
    WorkflowID.MARKET_INTELLIGENCE: WorkflowOwnership(
        workflow_id=WorkflowID.MARKET_INTELLIGENCE,
        owns=(
            "market_data_collection",
            "market_engine_execution",
            "model_market_interpretation",
            "freshness_and_source_health",
            "market_state_publication",
        ),
        does_not_own=(
            "scanner_execution",
            "trade_candidate_generation",
            "portfolio_policy",
            "trade_recommendation",
            "trade_execution",
        ),
    ),
    WorkflowID.STOCK_OPPORTUNITY: WorkflowOwnership(
        workflow_id=WorkflowID.STOCK_OPPORTUNITY,
        owns=(
            "stock_scanner_execution",
            "stock_candidate_normalization",
            "stock_enrichment_evaluation",
            "stock_opportunity_output",
        ),
        does_not_own=(
            "market_data_fetching",
            "options_chain_analysis",
            "trade_execution",
        ),
    ),
    WorkflowID.OPTIONS_OPPORTUNITY: WorkflowOwnership(
        workflow_id=WorkflowID.OPTIONS_OPPORTUNITY,
        owns=(
            "options_scanner_execution",
            "quote_validation_trust_hygiene",
            "ev_pop_greeks_recomputed_math",
            "options_candidate_normalization",
            "options_opportunity_output",
        ),
        does_not_own=(
            "market_data_fetching",
            "stock_scanning",
            "trade_execution",
        ),
    ),
    WorkflowID.ACTIVE_TRADE: WorkflowOwnership(
        workflow_id=WorkflowID.ACTIVE_TRADE,
        owns=(
            "active_position_monitoring",
            "health_scoring",
            "hold_reduce_close_recommendation",
        ),
        does_not_own=(
            "new_trade_generation",
            "scanner_execution",
        ),
    ),
}
