"""Workflow Architecture — Source-of-truth boundaries and design rules.

This module defines the rules that govern how workflows communicate,
how state is shared, and how artifacts flow through the system.

Greenfield design — does NOT reference archived pipeline code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

# ═══════════════════════════════════════════════════════════════════════
# 1. SOURCE-OF-TRUTH CONCEPT
# ═══════════════════════════════════════════════════════════════════════
#
# Market Intelligence is *shared infrastructure*.
# It produces ONE canonical market-state artifact that all downstream
# workflows consume.  This means:
#
#   - There is exactly one authoritative representation of the current
#     market picture at any point in time.
#   - Downstream workflows (Stock, Options) never call market-data
#     providers directly.  They read from the published artifact.
#   - The artifact includes full freshness, source-health, and
#     degradation metadata so consumers can decide what is trustworthy.
#   - If market state is stale beyond a configurable threshold,
#     downstream workflows must degrade gracefully (warn, reduce
#     confidence) rather than silently proceed with old data.
#
# Discovery:
#   Downstream workflows locate the latest valid market state via a
#   single function call (defined at implementation time).  There is
#   no event bus, no pub/sub — just a file-backed artifact with a
#   deterministic location.  The artifact lives under data/snapshots/
#   and is overwritten each run (historical snapshots are retained
#   with timestamped filenames).
# ═══════════════════════════════════════════════════════════════════════


class ArtifactKind(str, Enum):
    """Identifies the type of artifact produced by a workflow stage."""

    MARKET_STATE = "market_state"
    STOCK_CANDIDATES = "stock_candidates"
    OPTIONS_CANDIDATES = "options_candidates"
    FILTER_TRACE = "filter_trace"


@dataclass(frozen=True)
class ArtifactSpec:
    """Declares shape expectations for a workflow artifact."""

    kind: ArtifactKind
    produced_by_workflow: str   # WorkflowID value
    produced_by_stage: str      # stage key
    description: str
    required_top_level_keys: tuple[str, ...]


# ── Artifact catalog ──────────────────────────────────────────────────
#
# These specs are declarative contracts.  They describe what each
# artifact MUST contain at a minimum.  Implementation code validates
# against these specs at write-time.

ARTIFACT_SPECS: dict[ArtifactKind, ArtifactSpec] = {
    ArtifactKind.MARKET_STATE: ArtifactSpec(
        kind=ArtifactKind.MARKET_STATE,
        produced_by_workflow="market_intelligence",
        produced_by_stage="publish",
        description=(
            "Complete market picture: all 6 engine results (normalized "
            "via engine_output_contract), market composite (risk_state, "
            "support_state, stability_state), macro context with metric "
            "envelopes, model interpretation, conflict annotations, and "
            "quality metadata."
        ),
        required_top_level_keys=(
            "version",
            "generated_at",
            "freshness",
            "engines",
            "composite",
            "macro_context",
            "quality",
        ),
    ),
    ArtifactKind.STOCK_CANDIDATES: ArtifactSpec(
        kind=ArtifactKind.STOCK_CANDIDATES,
        produced_by_workflow="stock_opportunity",
        produced_by_stage="select_package",
        description=(
            "Final stock opportunity output: ranked candidates in "
            "canonical format with market context attached and "
            "quality/ranking scores."
        ),
        required_top_level_keys=(
            "version",
            "generated_at",
            "market_state_ref",
            "candidates",
            "quality",
        ),
    ),
    ArtifactKind.OPTIONS_CANDIDATES: ArtifactSpec(
        kind=ArtifactKind.OPTIONS_CANDIDATES,
        produced_by_workflow="options_opportunity",
        produced_by_stage="select_package",
        description=(
            "Final options opportunity output: ranked candidates with "
            "per-contract metrics, full filter trace, trust-hygiene "
            "scores, and recomputed EV/PoP/Greeks."
        ),
        required_top_level_keys=(
            "version",
            "generated_at",
            "market_state_ref",
            "candidates",
            "filter_trace",
            "quality",
        ),
    ),
    ArtifactKind.FILTER_TRACE: ArtifactSpec(
        kind=ArtifactKind.FILTER_TRACE,
        produced_by_workflow="options_opportunity",
        produced_by_stage="enrich_evaluate",
        description=(
            "Scanner contract required filter trace: preset name, "
            "resolved thresholds, ordered stage_counts, rejection "
            "reason counts (stable taxonomy), data-quality counts."
        ),
        required_top_level_keys=(
            "preset_name",
            "resolved_thresholds",
            "stage_counts",
            "rejection_reasons",
            "data_quality_counts",
        ),
    ),
}


# ═══════════════════════════════════════════════════════════════════════
# 2. ARTIFACT BOUNDARY PHILOSOPHY
# ═══════════════════════════════════════════════════════════════════════
#
# Every workflow stage that sets ``produces_artifact=True`` writes its
# output as a JSON file.  This is a deliberate design choice:
#
#   a) INSPECTABLE    — Any artifact can be opened and read by a human
#                       or a diagnostic tool without running any code.
#   b) REPLAY-SAFE    — Re-running a downstream workflow with the same
#                       input artifact produces identical output (given
#                       the same scanner version and config).
#   c) COMPACT        — Artifacts store only what the next consumer
#                       needs, plus quality metadata.  No raw API
#                       payloads, no debug logs.
#   d) SELF-DESCRIBING — Each artifact carries ``version``, source
#                       reference (e.g. ``market_state_ref``), and
#                       ``generated_at`` timestamp.
#   e) FILE-BACKED    — No in-memory-only state.  If a process crashes
#                       midway, the last successfully published artifact
#                       is still valid and discoverable.
#
# Anti-patterns to avoid:
#   - Shared mutable caches that bypass artifact boundaries.
#   - Artifacts that grow unboundedly (e.g. appending forever).
#   - Binary formats that require special readers.
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# 3. FRESHNESS & DEGRADATION RULES
# ═══════════════════════════════════════════════════════════════════════
#
# Market Intelligence publishes freshness metadata in every artifact:
#   - ``generated_at``: ISO timestamp of when the artifact was created.
#   - ``freshness``: per-source breakdown (source → is_intraday, age).
#   - ``quality.sources_available``: count of responding providers.
#   - ``quality.sources_degraded``: count of providers that returned
#     stale or partial data.
#
# Downstream consumers compare ``generated_at`` against their own wall
# clock.  If the age exceeds a configurable threshold, they must:
#   1. Log a warning (never silently use stale data).
#   2. Annotate their output artifact with a degradation flag.
#   3. Optionally reduce confidence or skip certain gates.
#   4. Never refuse to run entirely (partial information > no information).
# ═══════════════════════════════════════════════════════════════════════

# Staleness thresholds (seconds).  These are defaults; overridable via
# runtime config.

FRESHNESS_WARN_THRESHOLD_SECONDS: int = 600       # 10 minutes
FRESHNESS_DEGRADE_THRESHOLD_SECONDS: int = 1800    # 30 minutes


@dataclass(frozen=True)
class FreshnessPolicy:
    """Configurable staleness behavior for a downstream workflow."""

    warn_after_seconds: int = FRESHNESS_WARN_THRESHOLD_SECONDS
    degrade_after_seconds: int = FRESHNESS_DEGRADE_THRESHOLD_SECONDS
    allow_stale: bool = True  # if False, fail fast instead of degrading


DEFAULT_FRESHNESS_POLICY = FreshnessPolicy()


# ═══════════════════════════════════════════════════════════════════════
# 4. TRADE MANAGEMENT CENTER (TMC) INTEGRATION DIRECTION
# ═══════════════════════════════════════════════════════════════════════
#
# The TMC is the *future* single entry point for users to trigger and
# observe workflows.  It replaces the old scanner-review / build-run
# interface that was removed in the pipeline quarantine (Prompt 0).
#
# Design principles for TMC integration:
#
#   a) TMC triggers workflows, it does not orchestrate them.
#      A TMC "run" button calls a workflow endpoint; the workflow
#      runs its own stages sequentially and writes artifacts.
#
#   b) TMC reads artifacts, it does not consume in-memory state.
#      The frontend reads the latest published artifact to render
#      results.  There is no long-lived websocket streaming state.
#
#   c) TMC can request a fresh market state before running scanners.
#      This is a convenience: TMC calls Market Intelligence first,
#      waits for the new artifact, then triggers the scanner workflow.
#
#   d) TMC handles execution decisions (accept/reject/modify a trade)
#      which live OUTSIDE the scope of Stock and Options workflows.
#      Those workflows produce opportunity lists; TMC owns the decision.
#
#   e) Active Trade management stays in the Active Trade workflow.
#      TMC can render active-trade dashboards, but the monitoring
#      logic is not part of the opportunity workflows.
#
# Implementation status: TMC does not exist yet.  These principles
# are recorded here so that future prompts build toward them rather
# than re-inventing old pipeline patterns.
# ═══════════════════════════════════════════════════════════════════════


# ── Cross-workflow boundary rules (programmatic) ──────────────────────

@dataclass(frozen=True)
class BoundaryRule:
    """Encodes a cross-workflow communication rule."""

    name: str
    description: str
    from_workflow: str   # WorkflowID value or "tmc"
    to_workflow: str     # WorkflowID value or "tmc"
    mechanism: str       # "artifact_read" | "api_call" | "none"


BOUNDARY_RULES: tuple[BoundaryRule, ...] = (
    BoundaryRule(
        name="market_state_consumption",
        description=(
            "Stock and Options workflows read the latest market_state "
            "artifact.  They never call market-data providers directly."
        ),
        from_workflow="market_intelligence",
        to_workflow="stock_opportunity",
        mechanism="artifact_read",
    ),
    BoundaryRule(
        name="market_state_consumption_options",
        description=(
            "Options workflow reads the same market_state artifact."
        ),
        from_workflow="market_intelligence",
        to_workflow="options_opportunity",
        mechanism="artifact_read",
    ),
    BoundaryRule(
        name="tmc_triggers_market_refresh",
        description=(
            "TMC can request a fresh Market Intelligence run before "
            "triggering downstream workflows."
        ),
        from_workflow="tmc",
        to_workflow="market_intelligence",
        mechanism="api_call",
    ),
    BoundaryRule(
        name="tmc_triggers_stock_scan",
        description=(
            "TMC triggers Stock Opportunity workflow via API call."
        ),
        from_workflow="tmc",
        to_workflow="stock_opportunity",
        mechanism="api_call",
    ),
    BoundaryRule(
        name="tmc_triggers_options_scan",
        description=(
            "TMC triggers Options Opportunity workflow via API call."
        ),
        from_workflow="tmc",
        to_workflow="options_opportunity",
        mechanism="api_call",
    ),
    BoundaryRule(
        name="tmc_reads_candidates",
        description=(
            "TMC reads published candidate artifacts to display results."
        ),
        from_workflow="stock_opportunity",
        to_workflow="tmc",
        mechanism="artifact_read",
    ),
    BoundaryRule(
        name="no_cross_scanner_dependency",
        description=(
            "Stock and Options workflows are fully independent.  "
            "Neither reads the other's artifacts."
        ),
        from_workflow="stock_opportunity",
        to_workflow="options_opportunity",
        mechanism="none",
    ),
)


# ── Convenience helpers ───────────────────────────────────────────────

def get_artifact_spec(kind: ArtifactKind) -> ArtifactSpec:
    """Return the spec for a given artifact kind."""
    return ARTIFACT_SPECS[kind]


def get_boundary_rules_for(workflow: str) -> tuple[BoundaryRule, ...]:
    """Return all boundary rules involving a given workflow."""
    return tuple(
        r for r in BOUNDARY_RULES
        if r.from_workflow == workflow or r.to_workflow == workflow
    )
