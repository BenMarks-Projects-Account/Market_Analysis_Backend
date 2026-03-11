"""Context Assembler v1 — bridge-layer assembly of market + candidate + model context.

Gathers normalized market-picture outputs, scanner candidate outputs,
model-analysis responses, and dashboard metadata into a single reusable
context payload for future decision workflows.

Assembly philosophy
-------------------
1. Prefer ``payload["normalized"]`` (from engine_output_contract).
2. Fall back to ``payload["dashboard_metadata"]`` for quality / freshness.
3. Fall back to legacy ``payload["engine_result"]`` / ``payload["data_quality"]``
   only when normalized layers are absent (older cached payloads).
4. Never fail the entire assembly because one module is unavailable.

Assembled context shape (v1)
----------------------------
{
    "context_version":   "1.0",
    "assembled_at":      ISO timestamp,
    "assembly_status":   "complete" | "partial" | "degraded" | "empty",
    "assembly_warnings": list[str],
    "included_modules":  list[str],
    "missing_modules":   list[str],
    "degraded_modules":  list[str],
    "failed_modules":    list[str],
    "market_context":    {engine_key: MarketModuleContext, ...},
    "candidate_context": {
        "candidates": list[NormalizedCandidate],
        "count":      int,
        "scanners":   list[str],
        "families":   list[str],
    },
    "model_context": {
        "analyses": {analysis_type: NormalizedModelAnalysis, ...},
        "count":    int,
    },
    "quality_summary": {...},
    "freshness_summary": {...},
    "horizon_summary": {...},
    "metadata": {...},
}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.utils.time_horizon import (
    resolve_engine_horizon,
    resolve_model_horizon,
    resolve_scanner_horizon,
    validate_horizon,
    horizon_rank,
    HORIZON_ORDER,
)
from app.services.engine_output_contract import normalize_legacy_payload

logger = logging.getLogger("bentrade.context_assembler")

CONTEXT_VERSION = "1.1"

# The 6 Market Picture engine modules (canonical keys)
MARKET_MODULES = frozenset({
    "breadth_participation",
    "volatility_options",
    "cross_asset_macro",
    "flows_positioning",
    "liquidity_financial_conditions",
    "news_sentiment",
})

# Known alias → canonical key mappings.
# Model-analysis uses "liquidity_conditions" while the engine layer uses
# "liquidity_financial_conditions".  The assembler resolves these on
# ingestion so callers don't need to know which spelling is canonical.
MODULE_KEY_ALIASES: dict[str, str] = {
    "liquidity_conditions": "liquidity_financial_conditions",
}

# Assembly status vocabulary
# Semantics (each status is **exclusive** — first matching rule wins):
#   empty    – nothing provided at all: no market payloads and no candidates
#   degraded – market payloads were provided but more than half of the
#              provided modules are either failed or on fallback
#   partial  – some modules are missing, failed, or on fallback, but the
#              majority of provided modules are usable with normalized data
#   complete – every provided market module has normalized data and no
#              fallback or failure markers
#
# "partial" means the context IS usable but has gaps.
# "degraded" means the context is MOSTLY gaps — consumers should treat
#  it with suspicion.
ASSEMBLY_STATUSES = frozenset({
    "complete",
    "partial",
    "degraded",
    "empty",
})

# Per-module source vocabulary (how each module's data was obtained)
MODULE_SOURCES = frozenset({
    "normalized",   # primary: payload["normalized"] present and used
    "fallback",     # legacy: normalized absent, built from engine_result
    "error",        # failed: no usable data in the payload at all
})


# ═══════════════════════════════════════════════════════════════════════
# Alias resolution
# ═══════════════════════════════════════════════════════════════════════


def _resolve_module_key(key: str) -> str:
    """Return the canonical engine key, resolving known aliases."""
    return MODULE_KEY_ALIASES.get(key, key)


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════


def assemble_context(
    *,
    market_payloads: dict[str, dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    model_payloads: dict[str, dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an assembled context payload from normalized subcontracts.

    Parameters
    ----------
    market_payloads : dict[engine_key, service_payload]
        Service payloads for each Market Picture module (the full dict
        returned by e.g. ``BreadthService.get_breadth_analysis()``).
        Each should contain ``["normalized"]`` and/or ``["dashboard_metadata"]``.
    candidates : list[candidate_dict]
        Scanner candidate dicts. Each should have ``["normalized"]``
        (from ``normalize_candidate_output``).
    model_payloads : dict[analysis_type, model_response]
        Model-analysis response dicts. Each should have ``["normalized"]``
        (from ``wrap_service_model_response``).
    options : dict
        Assembly options (reserved for future use).

    Returns
    -------
    dict — The assembled context payload with all top-level fields.
    """
    now = datetime.now(timezone.utc).isoformat()
    warnings: list[str] = []

    # ── Market context ────────────────────────────────────────────
    market_ctx, mkt_included, mkt_missing, mkt_degraded, mkt_failed, mkt_warnings = (
        _assemble_market_context(market_payloads or {})
    )
    warnings.extend(mkt_warnings)

    # ── Candidate context ─────────────────────────────────────────
    cand_ctx, cand_warnings = _assemble_candidate_context(candidates or [])
    warnings.extend(cand_warnings)

    # ── Model context ─────────────────────────────────────────────
    model_ctx, model_warnings = _assemble_model_context(model_payloads or {})
    warnings.extend(model_warnings)

    # ── Aggregate module tracking ─────────────────────────────────
    included = sorted(mkt_included)
    missing = sorted(mkt_missing)
    degraded = sorted(mkt_degraded)
    failed = sorted(mkt_failed)

    # ── Quality and freshness summaries ───────────────────────────
    quality_summary = _build_quality_summary(market_ctx, mkt_included, mkt_degraded)
    freshness_summary = _build_freshness_summary(market_ctx, mkt_included)
    horizon_summary = _build_horizon_summary(market_ctx, cand_ctx, model_ctx)

    # ── Assembly status ───────────────────────────────────────────
    status = _compute_assembly_status(
        included_count=len(included),
        missing_count=len(missing),
        degraded_count=len(degraded),
        failed_count=len(failed),
        candidate_count=cand_ctx["count"],
        any_market_provided=bool(market_payloads),
    )

    # ── Per-module source attribution ─────────────────────────────
    module_sources: dict[str, str] = {}
    for key in included:
        module_sources[key] = "normalized"
    for key in degraded:
        module_sources[key] = "fallback"
    for key in failed:
        module_sources[key] = "error"

    return {
        "context_version": CONTEXT_VERSION,
        "assembled_at": now,
        "assembly_status": status,
        "assembly_warnings": warnings,
        "included_modules": included,
        "missing_modules": missing,
        "degraded_modules": degraded,
        "failed_modules": failed,
        "market_context": market_ctx,
        "candidate_context": cand_ctx,
        "model_context": model_ctx,
        "quality_summary": quality_summary,
        "freshness_summary": freshness_summary,
        "horizon_summary": horizon_summary,
        "metadata": {
            "context_version": CONTEXT_VERSION,
            "assembled_at": now,
            "market_module_count": len(included),
            "candidate_count": cand_ctx["count"],
            "model_count": model_ctx["count"],
            "assembly_status": status,
            "module_sources": module_sources,
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# Market context assembly
# ═══════════════════════════════════════════════════════════════════════


def _assemble_market_context(
    payloads: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str], list[str], list[str], list[str], list[str]]:
    """Build market_context from service payloads.

    Returns (market_ctx, included, missing, degraded, failed, warnings).
    - included:  modules with normalized data (source="normalized")
    - degraded:  modules on legacy fallback (source="fallback")
    - failed:    modules with no usable data (source="error")
    """
    market_ctx: dict[str, Any] = {}
    included: list[str] = []
    degraded: list[str] = []
    failed: list[str] = []
    warnings: list[str] = []

    # Resolve aliases so callers can pass e.g. "liquidity_conditions"
    resolved_payloads: dict[str, dict[str, Any]] = {}
    for key, payload in payloads.items():
        canonical = _resolve_module_key(key)
        if canonical != key:
            warnings.append(
                f"Module key '{key}' resolved to canonical '{canonical}'"
            )
        resolved_payloads[canonical] = payload

    for engine_key in MARKET_MODULES:
        payload = resolved_payloads.get(engine_key)
        if payload is None:
            continue

        module_data, source, module_warnings = _extract_market_module(
            engine_key, payload
        )
        warnings.extend(module_warnings)

        if module_data is not None:
            market_ctx[engine_key] = module_data
            if source == "fallback":
                degraded.append(engine_key)
            else:
                included.append(engine_key)
        else:
            failed.append(engine_key)

    # Modules not present at all
    missing = sorted(MARKET_MODULES - set(included) - set(degraded) - set(failed))

    return market_ctx, included, missing, degraded, failed, warnings


def _extract_market_module(
    engine_key: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, str, list[str]]:
    """Extract a single market module from its service payload.

    Priority:
      1. payload["normalized"] (engine_output_contract)
      2. payload["dashboard_metadata"] + legacy fields as fallback
      3. legacy engine_result extraction

    Returns (module_data, source_type, warnings).
    source_type: "normalized" | "fallback" | "error"
    """
    warnings: list[str] = []

    # ── Priority 1: Normalized contract ───────────────────────────
    normalized = payload.get("normalized")
    if normalized and isinstance(normalized, dict):
        dm = payload.get("dashboard_metadata")
        return {
            "normalized": normalized,
            "dashboard_metadata": dm,
            "source": "normalized",
        }, "normalized", warnings

    # ── Priority 2/3: Fallback to legacy shape ────────────────────
    warnings.append(
        f"Module '{engine_key}': normalized contract missing, using legacy fallback"
    )

    # Try dashboard_metadata for quality info
    dm = payload.get("dashboard_metadata")

    # Build a minimal normalized-like dict from legacy fields
    er = payload.get("engine_result") or payload.get("internal_engine")
    if er is None and not dm:
        warnings.append(f"Module '{engine_key}': no usable data in payload")
        return None, "error", warnings

    # Delegate to unified legacy normalizer in engine_output_contract
    fallback_normalized = normalize_legacy_payload(engine_key, payload)

    return {
        "normalized": fallback_normalized,
        "dashboard_metadata": dm,
        "source": "fallback",
    }, "fallback", warnings


def _build_fallback_normalized(
    engine_key: str,
    engine_result: dict[str, Any] | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Build a minimal normalized dict from legacy engine_result fields.

    .. deprecated:: 1.1
        Use ``normalize_legacy_payload()`` from engine_output_contract instead.
        Kept for any direct callers during transition.
    """
    return normalize_legacy_payload(engine_key, payload)


# ═══════════════════════════════════════════════════════════════════════
# Candidate context assembly
# ═══════════════════════════════════════════════════════════════════════


def _assemble_candidate_context(
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """Build candidate_context from scanner candidate dicts.

    Returns (candidate_ctx, warnings).
    """
    warnings: list[str] = []
    assembled: list[dict[str, Any]] = []
    scanners_seen: set[str] = set()
    families_seen: set[str] = set()

    for i, cand in enumerate(candidates):
        normalized = cand.get("normalized")
        if normalized and isinstance(normalized, dict):
            assembled.append(normalized)
            scanners_seen.add(normalized.get("scanner_key", "unknown"))
            families_seen.add(normalized.get("strategy_family", "unknown"))
        else:
            # Fallback: include raw candidate with marker
            fb = _build_fallback_candidate(cand, i)
            assembled.append(fb)
            scanners_seen.add(fb.get("scanner_key", "unknown"))
            families_seen.add(fb.get("strategy_family", "unknown"))
            warnings.append(
                f"Candidate #{i}: normalized contract missing, using legacy fallback"
            )

    return {
        "candidates": assembled,
        "count": len(assembled),
        "scanners": sorted(scanners_seen),
        "families": sorted(families_seen),
    }, warnings


def _build_fallback_candidate(
    candidate: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    """Build a minimal candidate dict when normalized is absent."""
    scanner_key = candidate.get("strategy_id") or "unknown"
    family = _infer_family(candidate)
    return {
        "candidate_id": candidate.get("trade_key") or f"unknown_{index}",
        "scanner_key": scanner_key,
        "scanner_name": candidate.get("strategy_id", "Unknown"),
        "strategy_family": family,
        "symbol": candidate.get("symbol", "UNKNOWN"),
        "underlying": candidate.get("underlying") or candidate.get("symbol", "UNKNOWN"),
        "direction": candidate.get("direction", "unknown"),
        "setup_quality": candidate.get("composite_score") or candidate.get("rank_score"),
        "confidence": None,
        "data_quality": {},
        "time_horizon": resolve_scanner_horizon(scanner_key, family),
        "_fallback": True,
    }


def _infer_family(candidate: dict[str, Any]) -> str:
    """Infer strategy_family from candidate shape."""
    if "legs" in candidate or "short_strike" in candidate:
        return "options"
    if "price" in candidate and "trend_state" in candidate:
        return "stock"
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════
# Model context assembly
# ═══════════════════════════════════════════════════════════════════════


def _assemble_model_context(
    payloads: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """Build model_context from model-analysis response dicts.

    Returns (model_ctx, warnings).
    """
    warnings: list[str] = []
    analyses: dict[str, Any] = {}

    for analysis_type, response in payloads.items():
        normalized = response.get("normalized")
        if normalized and isinstance(normalized, dict):
            analyses[analysis_type] = {
                "normalized": normalized,
                "source": "normalized",
            }
        else:
            # Fallback: extract what we can
            fb = _build_fallback_model(analysis_type, response)
            analyses[analysis_type] = {
                "normalized": fb,
                "source": "fallback",
            }
            warnings.append(
                f"Model '{analysis_type}': normalized contract missing, using legacy fallback"
            )

    return {
        "analyses": analyses,
        "count": len(analyses),
    }, warnings


def _build_fallback_model(
    analysis_type: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    """Build a minimal model analysis dict when normalized is absent."""
    raw_horizon = response.get("time_horizon") if response else None
    return {
        "status": "degraded" if response else "error",
        "analysis_type": analysis_type,
        "summary": response.get("summary") or response.get("text", ""),
        "confidence": response.get("confidence"),
        "warnings": list(response.get("warnings", [])) if response else [],
        "time_horizon": resolve_model_horizon(raw_horizon, analysis_type),
        "_fallback": True,
    }


# ═══════════════════════════════════════════════════════════════════════
# Quality and freshness summaries
# ═══════════════════════════════════════════════════════════════════════


def _build_quality_summary(
    market_ctx: dict[str, Any],
    included: list[str],
    degraded: list[str],
) -> dict[str, Any]:
    """Roll up quality across all market modules.

    Collects data_quality_status from dashboard_metadata where available,
    and confidence/signal_quality from normalized contracts.
    """
    module_quality: dict[str, dict[str, Any]] = {}

    for key, mod in market_ctx.items():
        dm = mod.get("dashboard_metadata")
        norm = mod.get("normalized", {})

        module_quality[key] = {
            "data_quality_status": dm.get("data_quality_status", "unknown") if dm else "unknown",
            "coverage_level": dm.get("coverage_level", "unknown") if dm else "unknown",
            "confidence": norm.get("confidence", 0),
            "signal_quality": norm.get("signal_quality", "low"),
            "source": mod.get("source", "unknown"),
        }

    # Aggregate
    statuses = [mq["data_quality_status"] for mq in module_quality.values()]
    status_priority = {"unavailable": 0, "poor": 1, "degraded": 2, "acceptable": 3, "good": 4, "unknown": -1}
    known_statuses = [s for s in statuses if s != "unknown"]

    if not known_statuses:
        overall = "unknown"
    else:
        worst = min(known_statuses, key=lambda s: status_priority.get(s, -1))
        overall = worst

    confidences = [mq["confidence"] for mq in module_quality.values() if mq["confidence"]]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0

    return {
        "overall_quality": overall,
        "average_confidence": round(avg_confidence, 1),
        "module_count": len(module_quality),
        "degraded_count": len(degraded),
        "modules": module_quality,
    }


def _build_freshness_summary(
    market_ctx: dict[str, Any],
    included: list[str],
) -> dict[str, Any]:
    """Roll up freshness across market modules.

    Uses dashboard_metadata.freshness_status and
    evaluation_metadata.compute_duration_s where available.
    """
    module_freshness: dict[str, dict[str, Any]] = {}

    for key, mod in market_ctx.items():
        dm = mod.get("dashboard_metadata")
        norm = mod.get("normalized", {})

        freshness_status = "unknown"
        last_update = None
        compute_duration = None

        if dm:
            freshness_status = dm.get("freshness_status", "unknown")
            last_update = dm.get("last_successful_update")
            eval_meta = dm.get("evaluation_metadata", {})
            compute_duration = eval_meta.get("compute_duration_s")
        elif norm:
            last_update = norm.get("as_of")
            if norm.get("freshness"):
                fr = norm["freshness"]
                if isinstance(fr, dict):
                    compute_duration = fr.get("compute_duration_s")

        module_freshness[key] = {
            "freshness_status": freshness_status,
            "last_update": last_update,
            "compute_duration_s": compute_duration,
        }

    # Aggregate
    statuses = [mf["freshness_status"] for mf in module_freshness.values()]
    freshness_priority = {"very_stale": 0, "stale": 1, "unknown": 2, "recent": 3, "live": 4}
    known = [s for s in statuses if s in freshness_priority]

    if not known:
        overall = "unknown"
    else:
        worst = min(known, key=lambda s: freshness_priority.get(s, -1))
        overall = worst

    return {
        "overall_freshness": overall,
        "module_count": len(module_freshness),
        "modules": module_freshness,
    }


# ═══════════════════════════════════════════════════════════════════════
# Horizon summary
# ═══════════════════════════════════════════════════════════════════════


def _build_horizon_summary(
    market_ctx: dict[str, Any],
    candidate_ctx: dict[str, Any],
    model_ctx: dict[str, Any],
) -> dict[str, Any]:
    """Roll up time-horizon tags across all assembled sub-contexts.

    Returns a dict with:
      - market_horizons: {engine_key: horizon, ...}
      - candidate_horizons: list[horizon] (one per candidate)
      - model_horizons: {analysis_type: horizon, ...}
      - distinct_horizons: sorted list of unique horizons present
      - shortest / longest: boundary horizon values (ignoring 'unknown')
    """
    # Market module horizons
    market_horizons: dict[str, str] = {}
    for key, mod in market_ctx.items():
        norm = mod.get("normalized") or {}
        market_horizons[key] = validate_horizon(norm.get("time_horizon"))

    # Candidate horizons
    candidate_horizons: list[str] = []
    for cand in candidate_ctx.get("candidates", []):
        candidate_horizons.append(validate_horizon(cand.get("time_horizon")))

    # Model horizons
    model_horizons: dict[str, str] = {}
    for atype, analysis in model_ctx.get("analyses", {}).items():
        norm = analysis.get("normalized") or {}
        model_horizons[atype] = validate_horizon(norm.get("time_horizon"))

    # Distinct horizons (ordered by semantic rank)
    all_horizons = (
        list(market_horizons.values())
        + candidate_horizons
        + list(model_horizons.values())
    )
    distinct = sorted(set(all_horizons), key=lambda h: horizon_rank(h))

    # Shortest / longest (excluding "unknown")
    ranked = [h for h in all_horizons if h != "unknown"]
    shortest = min(ranked, key=lambda h: horizon_rank(h)) if ranked else "unknown"
    longest = max(ranked, key=lambda h: horizon_rank(h)) if ranked else "unknown"

    return {
        "market_horizons": market_horizons,
        "candidate_horizons": candidate_horizons,
        "model_horizons": model_horizons,
        "distinct_horizons": distinct,
        "shortest": shortest,
        "longest": longest,
    }


# ═══════════════════════════════════════════════════════════════════════
# Assembly status computation
# ═══════════════════════════════════════════════════════════════════════


def _compute_assembly_status(
    *,
    included_count: int,
    missing_count: int,
    degraded_count: int,
    failed_count: int = 0,
    candidate_count: int,
    any_market_provided: bool = False,
) -> str:
    """Compute overall assembly status.

    Formula:
      - empty:    nothing provided at all (no market payloads and no candidates)
      - degraded: less than half of provided market modules are usable
                  (usable = normalized + fallback; unusable = missing + failed)
      - partial:  some modules missing, on fallback, or failed, but the
                  majority are still usable
      - complete: every provided market module has normalized data; no
                  fallback, missing, or failed modules
    """
    total = included_count + missing_count + degraded_count + failed_count
    if not any_market_provided and candidate_count == 0:
        return "empty"
    usable = included_count + degraded_count
    if total > 0 and any_market_provided:
        usable_ratio = usable / total
        if usable_ratio < 0.5:
            return "degraded"
        if missing_count > 0 or degraded_count > 0 or failed_count > 0:
            return "partial"
    elif candidate_count > 0:
        return "partial"
    return "complete"
