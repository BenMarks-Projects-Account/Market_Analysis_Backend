"""Market-state consumer loading seam — Prompt 5.

Reusable module for downstream workflows (Stock Opportunity, Options
Opportunity) to load the latest valid market-state artifact.

This is a thin adapter over ``market_state_discovery.load_latest_valid``
that normalises the discovery result into a consumer-oriented shape
suitable for use as the first stage of any consuming workflow.

Design rules
-------------
1. ALL market data enters consuming workflows through this seam.
2. Never call market-data providers (Tradier, FRED, etc.) directly.
3. Propagate ``artifact_id`` as ``market_state_ref`` for lineage.
4. Surface degradation/freshness metadata so the consuming runner can
   decide whether to continue, degrade, or abort.
5. Return a frozen, serialisable result — no live service references.

Greenfield design — does NOT reference archived pipeline code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.workflows.architecture import FreshnessPolicy
from app.workflows.market_state_contract import (
    FreshnessTier,
    PublicationStatus,
)
from app.workflows.market_state_discovery import (
    DiscoveryResult,
    load_latest_valid,
)


# ═══════════════════════════════════════════════════════════════════════
# CONSUMER RESULT
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MarketStateConsumerResult:
    """Outcome of loading market state for a consuming workflow.

    Fields
    ------
    loaded : bool
        True if a usable market-state artifact was found and loaded.
    market_state_ref : str | None
        The ``artifact_id`` from the loaded artifact — used as the
        upstream lineage reference in every downstream artifact.
    publication_status : str | None
        "valid" | "degraded" — only populated when ``loaded`` is True.
    freshness_tier : str | None
        "fresh" | "warning" | "stale" | "unknown".
    artifact : dict | None
        The full market-state dict.  None when loading fails.
    consumer_summary : dict | None
        The ``consumer_summary`` section from the artifact — gives the
        consumer a quick regime / risk handle without parsing engines.
    composite : dict | None
        The ``composite`` section (risk_on / risk_off / neutral).
    warnings : list[str]
        Consumer-relevant warnings (freshness, degradation, etc.).
    error : str | None
        Human-readable error when loading fails.
    """

    loaded: bool = False
    market_state_ref: str | None = None
    publication_status: str | None = None
    freshness_tier: str | None = None
    artifact: dict[str, Any] | None = None
    consumer_summary: dict[str, Any] | None = None
    composite: dict[str, Any] | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "loaded": self.loaded,
            "market_state_ref": self.market_state_ref,
            "publication_status": self.publication_status,
            "freshness_tier": self.freshness_tier,
            "consumer_summary": self.consumer_summary,
            "composite": self.composite,
            "warnings": list(self.warnings),
            "error": self.error,
            # NOTE: artifact is intentionally excluded from serialisation
            # to keep stage artifacts compact (lineage ref is sufficient).
        }


# ═══════════════════════════════════════════════════════════════════════
# LOADING FUNCTION
# ═══════════════════════════════════════════════════════════════════════


def load_market_state_for_consumer(
    data_dir: str | Path,
    freshness_policy: FreshnessPolicy | None = None,
    now: datetime | None = None,
) -> MarketStateConsumerResult:
    """Load the latest valid market-state artifact for a consuming workflow.

    This is the SINGLE entry point for Stock and Options workflows to
    obtain market context.  It wraps ``load_latest_valid()`` and
    extracts the fields consumers need.

    Parameters
    ----------
    data_dir : str | Path
        Backend data directory (``BenTrade/backend/data/``).
    freshness_policy : FreshnessPolicy | None
        Staleness policy.  Defaults to architecture defaults.
    now : datetime | None
        Reference time for freshness.  Defaults to now (UTC).

    Returns
    -------
    MarketStateConsumerResult
        Always returned — never raises.
    """
    discovery: DiscoveryResult = load_latest_valid(
        data_dir=data_dir,
        policy=freshness_policy,
        now=now,
    )

    # ── Not found / not usable ────────────────────────────────────
    if not discovery.is_usable or discovery.artifact is None:
        warn_list: list[str] = list(discovery.warnings)
        error_msg = discovery.error or "Market state not usable"
        if discovery.publication_status is not None:
            error_msg += f" (status={discovery.publication_status.value})"
        if discovery.freshness_tier is not None:
            error_msg += f" (freshness={discovery.freshness_tier.value})"
        return MarketStateConsumerResult(
            loaded=False,
            publication_status=(
                discovery.publication_status.value
                if discovery.publication_status
                else None
            ),
            freshness_tier=(
                discovery.freshness_tier.value
                if discovery.freshness_tier
                else None
            ),
            warnings=tuple(warn_list),
            error=error_msg,
        )

    # ── Usable — extract consumer fields ──────────────────────────
    artifact = discovery.artifact
    artifact_id = artifact.get("artifact_id")
    consumer_summary = artifact.get("consumer_summary")
    composite = artifact.get("composite")

    pub_status = (
        discovery.publication_status.value
        if discovery.publication_status
        else None
    )
    fresh_tier = (
        discovery.freshness_tier.value
        if discovery.freshness_tier
        else None
    )

    return MarketStateConsumerResult(
        loaded=True,
        market_state_ref=artifact_id,
        publication_status=pub_status,
        freshness_tier=fresh_tier,
        artifact=artifact,
        consumer_summary=consumer_summary,
        composite=composite,
        warnings=tuple(discovery.warnings),
    )
