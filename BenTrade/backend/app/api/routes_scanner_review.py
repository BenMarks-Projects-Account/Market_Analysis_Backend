"""Scanner Review API routes — Prompt 14.

Provides review-surface endpoints for the Scanner Review dashboard.
Exposes V2 routing status, family verification, and per-run scanner
diagnostics without requiring the frontend to reconstruct raw artifacts.

Prefix: ``/api/scanner-review``
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

# NOTE: pipeline_run_store removed — deprecated as part of workflow pivot (Prompt 0).
# Per-run scanner diagnostic endpoints now return 410 Gone until the new workflow
# provides a replacement artifact source.

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scanner-review", tags=["scanner-review"])


# ---------------------------------------------------------------------------
# V2 routing / family verification (static — not per-run)
# ---------------------------------------------------------------------------


@router.get("/routing")
async def get_routing_overview(request: Request) -> dict[str, Any]:
    """Return V2-forward routing overview and family verification summary.

    Combines routing report + family verification summary + pipeline
    registry visibility into a single response for the dashboard.
    """
    from app.services.scanner_v2.verify import (
        get_family_verification_summary,
        get_v2_routing_report,
    )

    report = get_v2_routing_report()
    family_summary = get_family_verification_summary()

    return {
        "routing_model": report.get("routing_model"),
        "v2_families": report.get("v2_families", []),
        "scanner_key_routing": report.get("scanner_key_routing", {}),
        "overrides_active": report.get("overrides_active", {}),
        "legacy_forced_keys": report.get("legacy_forced_keys", []),
        "retirement_readiness": report.get("retirement_readiness", {}),
        "pipeline_registry": report.get("pipeline_registry", {}),
        "family_verification": family_summary,
    }


# ---------------------------------------------------------------------------
# Per-run scanner diagnostics
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/scanner-summary")
async def get_run_scanner_summary(
    run_id: str,
    request: Request,
) -> dict[str, Any]:
    """Return the scanner stage summary for a specific pipeline run.

    Extracts the ``scanner_stage_summary`` artifact and restructures it
    for the Scanner Review dashboard with family-grouped views.

    DEPRECATED: Pipeline run store has been removed (workflow pivot).
    This endpoint returns 410 Gone until the new workflow provides
    a replacement artifact source.
    """
    raise HTTPException(
        status_code=410,
        detail={"message": "Pipeline run store removed — workflow pivot in progress"},
    )


@router.get("/runs/{run_id}/candidates")
async def get_run_scanner_candidates(
    run_id: str,
    request: Request,
    scanner_key: str | None = Query(
        default=None,
        description="Filter by scanner_key",
    ),
    family: str | None = Query(
        default=None,
        description="Filter by family (options, stock)",
    ),
) -> dict[str, Any]:
    """Return scanner candidates for a pipeline run.

    Collects candidates from normalized_candidate artifacts and
    supports filtering by scanner_key or family.

    DEPRECATED: Pipeline run store has been removed (workflow pivot).
    This endpoint returns 410 Gone until the new workflow provides
    a replacement artifact source.
    """
    raise HTTPException(
        status_code=410,
        detail={"message": "Pipeline run store removed — workflow pivot in progress"},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_scanner_summary_artifact(
    art_store: dict[str, Any],
) -> dict[str, Any] | None:
    """Find the scanner_stage_summary artifact in the artifact store."""
    artifacts = art_store.get("artifacts", {})
    type_index = art_store.get("type_index", {})

    # Use type index if available
    summary_ids = type_index.get("scanner_stage_summary", [])
    if summary_ids:
        return artifacts.get(summary_ids[-1])  # latest

    # Fallback: linear scan
    for art in artifacts.values():
        if art.get("artifact_type") == "scanner_stage_summary":
            return art
    return None


# ── Family key lookup (Prompt 14) ──────────────────────────────
# Maps scanner_key → V2 family_key for grouping.

_SCANNER_KEY_TO_FAMILY: dict[str, str] = {
    "put_credit_spread": "vertical_spreads",
    "call_credit_spread": "vertical_spreads",
    "put_debit": "vertical_spreads",
    "call_debit": "vertical_spreads",
    "iron_condor": "iron_condors",
    "butterfly_debit": "butterflies",
    "iron_butterfly": "butterflies",
    "calendar_call_spread": "calendars",
    "calendar_put_spread": "calendars",
    "diagonal_call_spread": "calendars",
    "diagonal_put_spread": "calendars",
}


def _group_by_family(
    scanner_summaries: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Group scanner summaries by V2 family for dashboard cards."""
    families: dict[str, dict[str, Any]] = {}

    for key, s in scanner_summaries.items():
        family_key = _SCANNER_KEY_TO_FAMILY.get(key)
        if family_key is None:
            sf = s.get("scanner_family", "")
            family_key = "stock" if sf == "stock" else "other"

        if family_key not in families:
            families[family_key] = {
                "family_key": family_key,
                "scanners": [],
                "total_candidates": 0,
                "total_usable": 0,
                "execution_paths": set(),
                "statuses": [],
            }

        fam = families[family_key]
        fam["scanners"].append(key)
        fam["total_candidates"] += s.get("candidate_count", 0)
        fam["total_usable"] += s.get("usable_candidate_count", 0)
        fam["execution_paths"].add(s.get("execution_path", "unknown"))
        fam["statuses"].append(s.get("status", "unknown"))

    # Convert sets to lists for JSON serialization
    for fam in families.values():
        fam["execution_paths"] = sorted(fam["execution_paths"])

    return families


def _collect_candidates(
    art_store: dict[str, Any],
    scanner_key: str | None,
    family: str | None,
) -> list[dict[str, Any]]:
    """Collect candidates from normalized_candidate artifacts."""
    artifacts = art_store.get("artifacts", {})
    type_index = art_store.get("type_index", {})
    candidates: list[dict[str, Any]] = []

    # Find normalized_candidate artifacts
    cand_ids = type_index.get("normalized_candidate", [])
    if not cand_ids:
        # Fallback: scan all artifacts
        cand_ids = [
            aid for aid, art in artifacts.items()
            if art.get("artifact_type") == "normalized_candidate"
        ]

    for aid in cand_ids:
        art = artifacts.get(aid)
        if art is None:
            continue

        data = art.get("data", {})
        cand_list = data if isinstance(data, list) else data.get("candidates", [])
        if not isinstance(cand_list, list):
            continue

        for c in cand_list:
            if not isinstance(c, dict):
                continue
            if scanner_key and c.get("scanner_key") != scanner_key:
                continue
            if family:
                c_family = _SCANNER_KEY_TO_FAMILY.get(
                    c.get("scanner_key", ""),
                )
                c_sf = c.get("scanner_family", "")
                if family == "stock" and c_sf != "stock":
                    continue
                if family != "stock" and c_family != family:
                    continue
            candidates.append(c)

    return candidates
