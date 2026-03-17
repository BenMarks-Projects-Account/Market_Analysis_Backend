"""Market-state artifact contract — the concrete JSON shape.

This module defines the canonical shape of ``market_state.json``, the
single source-of-truth artifact produced by the Market Intelligence
Producer workflow and consumed by all downstream workflows.

Greenfield design — does NOT reference archived pipeline code.

Sections
--------
1. Contract version & constants
2. Publication status vocabulary
3. Freshness assessment helpers
4. Top-level contract schema (MarketStateContract)
5. Section-level sub-schemas
6. Validation helpers
7. Consumer usage rules (documented in comments)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.workflows.architecture import (
    FRESHNESS_DEGRADE_THRESHOLD_SECONDS,
    FRESHNESS_WARN_THRESHOLD_SECONDS,
    FreshnessPolicy,
)

# ═══════════════════════════════════════════════════════════════════════
# 1. CONTRACT VERSION & CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

MARKET_STATE_CONTRACT_VERSION = "1.0"

# Six canonical market engines — order is stable.
ENGINE_KEYS: tuple[str, ...] = (
    "breadth_participation",
    "volatility_options",
    "cross_asset_macro",
    "flows_positioning",
    "liquidity_financial_conditions",
    "news_sentiment",
)

# Macro metrics captured in the market snapshot section.
MACRO_METRIC_KEYS: tuple[str, ...] = (
    "vix",
    "ten_year_yield",
    "two_year_yield",
    "fed_funds_rate",
    "oil_wti",
    "usd_index",
    "yield_curve_spread",
    "cpi_yoy",
)


# ═══════════════════════════════════════════════════════════════════════
# 2. PUBLICATION STATUS VOCABULARY
# ═══════════════════════════════════════════════════════════════════════
#
# Every published market_state.json carries a ``publication.status``
# field that tells consumers about the overall health of the artifact.
#
# Status lifecycle:
#   writing → (atomic rename) → valid | degraded | failed
#
# ``stale`` is NOT a publication status — it is a consumer-side
# assessment based on artifact age.  An artifact that was ``valid``
# at publish time becomes ``stale`` only when a consumer checks it
# later and finds it too old.
#
# ``incomplete`` means the producer aborted mid-run but still wrote
# a partial artifact (e.g. only 3 of 6 engines succeeded).  It is
# a subcategory of ``degraded``.


class PublicationStatus(str, Enum):
    """Allowed publication states for a market-state artifact."""

    VALID = "valid"
    # All engines ran, all required sections present, composite built,
    # model interpretation present.  The artifact is fully usable.

    DEGRADED = "degraded"
    # The artifact was published but with reduced quality.
    # At least one data source failed, returned stale data, or an
    # engine produced a fallback result.  The ``quality`` section
    # describes what is degraded.  Consumers may proceed but must
    # annotate their own output with degradation flags.

    INCOMPLETE = "incomplete"
    # The producer aborted before finishing all stages.  Some
    # sections may be missing or empty.  This is a severe form of
    # degradation.  Consumers should prefer a prior valid artifact
    # over an incomplete one when possible.

    FAILED = "failed"
    # The producer run failed entirely.  The artifact is written
    # only for diagnostic purposes.  Consumers MUST NOT use a
    # failed artifact and should fall back to the most recent
    # valid or degraded artifact.

    UNPUBLISHED = "unpublished"
    # The artifact exists on disk but was never promoted to the
    # latest-valid pointer.  This can happen if validation checks
    # fail after the producer writes the file.  Consumers should
    # never see this status via normal discovery paths.


# Statuses that a consumer is allowed to use for trading decisions.
CONSUMABLE_STATUSES: frozenset[PublicationStatus] = frozenset({
    PublicationStatus.VALID,
    PublicationStatus.DEGRADED,
})

# Statuses where the artifact exists but should not be consumed.
UNUSABLE_STATUSES: frozenset[PublicationStatus] = frozenset({
    PublicationStatus.INCOMPLETE,
    PublicationStatus.FAILED,
    PublicationStatus.UNPUBLISHED,
})


# ═══════════════════════════════════════════════════════════════════════
# 3. FRESHNESS ASSESSMENT
# ═══════════════════════════════════════════════════════════════════════
#
# Freshness is a *consumer-side* concept.  The producer records
# ``generated_at`` and per-source freshness.  The consumer compares
# ``generated_at`` against its own wall clock to determine staleness.
#
# Freshness tiers:
#   fresh    — age < warn threshold (600s default)
#   warning  — warn threshold <= age < degrade threshold
#   stale    — age >= degrade threshold
#
# These tiers are orthogonal to publication status.  A ``valid``
# artifact can become ``stale`` with time.  A ``degraded`` artifact
# can still be ``fresh`` if just published.


class FreshnessTier(str, Enum):
    """Consumer-assessed freshness of an artifact."""

    FRESH = "fresh"
    WARNING = "warning"
    STALE = "stale"
    UNKNOWN = "unknown"    # cannot determine (missing generated_at)


def assess_freshness(
    generated_at_iso: str | None,
    now: datetime | None = None,
    policy: FreshnessPolicy | None = None,
) -> FreshnessTier:
    """Determine the freshness tier of an artifact.

    Parameters
    ----------
    generated_at_iso : str | None
        ISO 8601 timestamp from the artifact's ``generated_at`` field.
    now : datetime | None
        Reference time.  Defaults to ``datetime.now(timezone.utc)``.
    policy : FreshnessPolicy | None
        Staleness thresholds.  Defaults to architecture defaults.

    Returns
    -------
    FreshnessTier
    """
    if not generated_at_iso:
        return FreshnessTier.UNKNOWN

    if policy is None:
        policy = FreshnessPolicy()
    if now is None:
        now = datetime.now(timezone.utc)

    try:
        generated_at = datetime.fromisoformat(generated_at_iso)
        # Ensure timezone-aware comparison
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return FreshnessTier.UNKNOWN

    age_seconds = (now - generated_at).total_seconds()

    if age_seconds < 0:
        # Future timestamp — treat as fresh (clock skew tolerance)
        return FreshnessTier.FRESH
    if age_seconds < policy.warn_after_seconds:
        return FreshnessTier.FRESH
    if age_seconds < policy.degrade_after_seconds:
        return FreshnessTier.WARNING
    return FreshnessTier.STALE


def is_consumable(
    status: PublicationStatus | str,
    freshness: FreshnessTier | str,
    allow_stale: bool = True,
) -> bool:
    """Determine whether a market-state artifact is safe to consume.

    Parameters
    ----------
    status : PublicationStatus | str
        The artifact's publication status.
    freshness : FreshnessTier | str
        Consumer-assessed freshness tier.
    allow_stale : bool
        If False, stale artifacts are treated as unusable even if
        publication status is valid/degraded.

    Returns
    -------
    bool
        True if the artifact may be used for downstream decisions.
    """
    # Normalize to enum values
    if isinstance(status, str):
        try:
            status = PublicationStatus(status)
        except ValueError:
            return False
    if isinstance(freshness, str):
        try:
            freshness = FreshnessTier(freshness)
        except ValueError:
            return False

    if status not in CONSUMABLE_STATUSES:
        return False
    if not allow_stale and freshness == FreshnessTier.STALE:
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════
# 4. TOP-LEVEL CONTRACT SCHEMA
# ═══════════════════════════════════════════════════════════════════════
#
# The canonical market_state.json has these top-level sections:
#
#   {
#     "contract_version":  "1.0",
#     "artifact_id":       unique run identifier,
#     "workflow_id":       "market_intelligence",
#     "generated_at":      ISO 8601 UTC timestamp,
#     "publication":       { status, published_at, prior_artifact_id },
#     "freshness":         { per-source freshness metadata },
#     "quality":           { source health, degradation summary },
#     "market_snapshot":   { macro metric envelopes },
#     "engines":           { engine_key → normalized engine output },
#     "composite":         { 3-state composite + evidence },
#     "conflicts":         { conflict detector report },
#     "model_interpretation": { LLM-driven market analysis },
#     "consumer_summary":  { compact downstream-ready digest },
#     "lineage":           { provenance, source references },
#     "warnings":          [ aggregated warnings ]
#   }

REQUIRED_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "contract_version",
    "artifact_id",
    "workflow_id",
    "generated_at",
    "publication",
    "freshness",
    "quality",
    "market_snapshot",
    "engines",
    "composite",
    "conflicts",
    "model_interpretation",
    "consumer_summary",
    "lineage",
    "warnings",
)

# Sections that may be None/empty in degraded artifacts but must still
# exist as keys.
NULLABLE_SECTIONS: frozenset[str] = frozenset({
    "conflicts",
    "model_interpretation",
})


# ── Section sub-schemas (declarative) ─────────────────────────────────

@dataclass(frozen=True)
class SectionSchema:
    """Declares expected keys within a top-level section."""
    section_name: str
    required_keys: tuple[str, ...]
    description: str


SECTION_SCHEMAS: dict[str, SectionSchema] = {
    "publication": SectionSchema(
        section_name="publication",
        required_keys=("status", "published_at"),
        description=(
            "Publication metadata: status (valid/degraded/incomplete/"
            "failed/unpublished), publication timestamp, optional "
            "reference to the prior artifact for lineage chaining."
        ),
    ),
    "freshness": SectionSchema(
        section_name="freshness",
        required_keys=("overall", "per_source"),
        description=(
            "Source-level freshness: overall freshness assessment, "
            "and per data-source breakdown mapping source name to "
            "freshness tier and age metadata."
        ),
    ),
    "quality": SectionSchema(
        section_name="quality",
        required_keys=(
            "sources_total",
            "sources_available",
            "sources_degraded",
            "sources_failed",
            "engines_total",
            "engines_succeeded",
            "engines_degraded",
            "engines_failed",
            "overall_quality",
        ),
        description=(
            "Source health and quality summary: counts of total, "
            "available, degraded, and failed providers and engines. "
            "overall_quality is one of: good, acceptable, degraded, "
            "poor, unavailable."
        ),
    ),
    "market_snapshot": SectionSchema(
        section_name="market_snapshot",
        required_keys=("metrics", "snapshot_at"),
        description=(
            "Normalized macro market metrics in metric-envelope format. "
            "Each entry in ``metrics`` is keyed by metric name (vix, "
            "ten_year_yield, etc.) and contains value, source, "
            "freshness, is_intraday, observation_date, fetched_at."
        ),
    ),
    "engines": SectionSchema(
        section_name="engines",
        required_keys=(),  # keys are dynamic (engine_key names)
        description=(
            "Engine outputs keyed by engine_key. Each value is the "
            "normalized 23-field engine output from engine_output_contract. "
            "All 6 engines should be present in valid artifacts; "
            "degraded artifacts may have fewer."
        ),
    ),
    "composite": SectionSchema(
        section_name="composite",
        required_keys=(
            "market_state",
            "support_state",
            "stability_state",
            "confidence",
            "summary",
        ),
        description=(
            "3-dimensional market composite: market_state (risk_on / "
            "neutral / risk_off), support_state (supportive / mixed / "
            "fragile), stability_state (orderly / noisy / unstable), "
            "confidence score, and human-readable summary. Evidence "
            "and adjustment details may be included."
        ),
    ),
    "conflicts": SectionSchema(
        section_name="conflicts",
        required_keys=("status", "conflict_count", "conflict_severity"),
        description=(
            "Conflict detector report. May be None if conflict detection "
            "was skipped. When present, includes conflict items grouped "
            "by category (market, candidate, model, horizon, quality)."
        ),
    ),
    "model_interpretation": SectionSchema(
        section_name="model_interpretation",
        required_keys=("status",),
        description=(
            "LLM-driven market interpretation. May be None if model "
            "interpretation was skipped or failed. When present, "
            "includes the model's structured analysis of market conditions. "
            "At minimum carries a ``status`` field (ok / skipped / failed)."
        ),
    ),
    "consumer_summary": SectionSchema(
        section_name="consumer_summary",
        required_keys=(
            "market_state",
            "support_state",
            "stability_state",
            "confidence",
            "vix",
            "regime_tags",
            "is_degraded",
            "summary_text",
        ),
        description=(
            "Compact digest intended for downstream Stock and Options "
            "workflows. Contains the essential market intelligence "
            "without full engine detail. Enough to make basic "
            "threshold/gating decisions without parsing full engines."
        ),
    ),
    "lineage": SectionSchema(
        section_name="lineage",
        required_keys=("workflow_id", "workflow_version", "run_id"),
        description=(
            "Provenance and auditability metadata: which workflow "
            "version produced this artifact, the unique run_id, "
            "optional references to input data hashes."
        ),
    ),
}


# ── Quality vocabulary ────────────────────────────────────────────────

class OverallQuality(str, Enum):
    """Quality assessment vocabulary (aligned with market_composite)."""
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    DEGRADED = "degraded"
    POOR = "poor"
    UNAVAILABLE = "unavailable"


# ── Composite state vocabularies (mirrors market_composite.py) ────────

MARKET_STATES: frozenset[str] = frozenset({"risk_on", "neutral", "risk_off"})
SUPPORT_STATES: frozenset[str] = frozenset({"supportive", "mixed", "fragile"})
STABILITY_STATES: frozenset[str] = frozenset({"orderly", "noisy", "unstable"})


# ═══════════════════════════════════════════════════════════════════════
# 5. VALIDATION HELPERS
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ValidationResult:
    """Result of validating a market-state artifact dict."""

    is_valid: bool
    missing_keys: list[str] = field(default_factory=list)
    invalid_sections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_market_state(artifact: dict[str, Any]) -> ValidationResult:
    """Validate a market-state artifact dict against the contract.

    This is a *structural* check — it verifies that required keys and
    sections exist.  It does not validate the semantic content of each
    engine output or composite value.

    Parameters
    ----------
    artifact : dict
        The parsed JSON artifact.

    Returns
    -------
    ValidationResult
    """
    result = ValidationResult(is_valid=True)

    # Check top-level keys
    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in artifact:
            result.missing_keys.append(key)
            result.is_valid = False

    # Check contract version
    cv = artifact.get("contract_version")
    if cv is not None and cv != MARKET_STATE_CONTRACT_VERSION:
        result.warnings.append(
            f"contract_version mismatch: expected {MARKET_STATE_CONTRACT_VERSION!r}, "
            f"got {cv!r}"
        )

    # Check section sub-schemas
    for section_name, schema in SECTION_SCHEMAS.items():
        section_data = artifact.get(section_name)

        # Nullable sections can be None
        if section_data is None:
            if section_name not in NULLABLE_SECTIONS:
                result.invalid_sections.append(section_name)
                result.is_valid = False
            continue

        if not isinstance(section_data, dict):
            result.invalid_sections.append(section_name)
            result.is_valid = False
            continue

        for req_key in schema.required_keys:
            if req_key not in section_data:
                result.invalid_sections.append(
                    f"{section_name}.{req_key}"
                )
                result.is_valid = False

    # Check publication status
    pub = artifact.get("publication")
    if isinstance(pub, dict):
        status_val = pub.get("status")
        valid_statuses = {s.value for s in PublicationStatus}
        if status_val not in valid_statuses:
            result.warnings.append(
                f"Unknown publication status: {status_val!r}"
            )

    # Check engine keys (warning-level, not validity-breaking)
    engines = artifact.get("engines")
    if isinstance(engines, dict):
        present = set(engines.keys())
        expected = set(ENGINE_KEYS)
        missing_engines = expected - present
        if missing_engines:
            result.warnings.append(
                f"Missing engines: {sorted(missing_engines)}"
            )

    return result


# ═══════════════════════════════════════════════════════════════════════
# 6. CONSUMER USAGE RULES
# ═══════════════════════════════════════════════════════════════════════
#
# Downstream Stock and Options workflows MUST follow these rules when
# consuming market_state.json:
#
# Rule 1: USE DISCOVERY — Never hard-code an artifact path.
#     Use ``load_latest_valid()`` (from market_state_discovery)
#     to locate the current consumable artifact.
#
# Rule 2: CHECK STATUS — Before using any artifact data, verify
#     ``publication.status`` is in CONSUMABLE_STATUSES.
#     If not, fall back or abort.
#
# Rule 3: CHECK FRESHNESS — Compare ``generated_at`` against the
#     current wall clock using ``assess_freshness()``.
#     If STALE and policy forbids stale usage, fall back or abort.
#     If WARNING, log a warning and annotate downstream output.
#
# Rule 4: PROPAGATE LINEAGE — Copy ``artifact_id`` into the
#     downstream artifact's ``market_state_ref`` field so the
#     provenance chain is traceable.
#
# Rule 5: SURFACE DEGRADATION — If the market-state artifact is
#     ``degraded`` or freshness is ``warning``/``stale``, annotate
#     the downstream artifact with that context.  Never hide
#     upstream quality issues from the final consumer.
#
# Rule 6: USE CONSUMER SUMMARY — For quick gating decisions
#     (e.g., "is market risk_off?"), use the ``consumer_summary``
#     section rather than re-parsing full engine outputs.
#     For deep analysis, use the ``engines`` section.
#
# Rule 7: DO NOT CALL PROVIDERS — Stock and Options workflows
#     must not call market-data providers (Tradier, FRED, Finnhub)
#     directly.  All market data comes from the published artifact.
#
# Rule 8: NEVER FABRICATE — If a needed field is None in the
#     artifact, propagate None downstream.  Do not fill in
#     default values or guesses.
# ═══════════════════════════════════════════════════════════════════════
