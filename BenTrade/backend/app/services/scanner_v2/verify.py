"""Scanner V2 — manual verification hooks (Prompt 13).

Provides practical utilities for manually validating V2 scanner
families before legacy retirement.  These hooks are designed for
developer/operator use during the migration period.

Usage
─────
    from app.services.scanner_v2.verify import (
        verify_v2_family,
        get_v2_routing_report,
        get_family_verification_summary,
    )

    # Run a targeted V2 family check with synthetic data
    result = verify_v2_family("iron_condor", symbol="SPY", chain=chain, price=585.0)

    # Get comprehensive routing report
    report = get_v2_routing_report()

    # Get summary of what's ready for legacy deletion
    summary = get_family_verification_summary()

RETIREMENT NOTE
───────────────
This module is useful during migration.  Once legacy code is deleted
(Prompt 15), the verification hooks are no longer needed — standard
V2 scanner tests and pipeline artifacts provide sufficient visibility.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger("bentrade.scanner_v2.verify")


def verify_v2_family(
    scanner_key: str,
    *,
    symbol: str = "SPY",
    chain: dict[str, Any] | None = None,
    underlying_price: float | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a V2 scanner for a single scanner_key and return rich diagnostics.

    This is for manual verification — produces a structured report
    showing construction, structural checks, math, and diagnostics.

    Parameters
    ----------
    scanner_key : str
        The strategy_id / scanner_key to test.
    symbol : str
        Target symbol (default SPY).
    chain : dict | None
        Raw options chain.  If None, returns an error dict.
    underlying_price : float | None
        Underlying price.  If None, returns an error dict.
    context : dict | None
        Optional scanner context overrides.

    Returns
    -------
    dict with:
        - scanner_key, family_key, strategy_id
        - routing: "v2" or "legacy"
        - v2_implemented: bool
        - scan_result: full V2ScanResult.to_dict() or None
        - phase_counts: list of phase → remaining counts
        - reject_reason_counts: dict
        - candidate_count, passed_count
        - sample_candidates: first 5 candidates (for inspection)
        - diagnostics_summary: aggregated diagnostics info
        - error: str or None
    """
    from app.services.scanner_v2.migration import get_scanner_version
    from app.services.scanner_v2.registry import (
        get_v2_family,
        get_v2_scanner,
        is_v2_supported,
    )

    report: dict[str, Any] = {
        "scanner_key": scanner_key,
        "symbol": symbol,
        "v2_implemented": is_v2_supported(scanner_key),
        "routing": get_scanner_version(scanner_key),
        "family_key": None,
        "strategy_id": scanner_key,
        "scan_result": None,
        "phase_counts": [],
        "reject_reason_counts": {},
        "candidate_count": 0,
        "passed_count": 0,
        "sample_candidates": [],
        "diagnostics_summary": {},
        "error": None,
    }

    if not is_v2_supported(scanner_key):
        report["error"] = (
            f"scanner_key={scanner_key!r} has no V2 implementation"
        )
        return report

    meta = get_v2_family(scanner_key)
    report["family_key"] = meta.family_key if meta else None

    if chain is None or underlying_price is None:
        report["error"] = "chain and underlying_price are required"
        return report

    try:
        scanner = get_v2_scanner(scanner_key)
        result = scanner.run(
            scanner_key=scanner_key,
            strategy_id=scanner_key,
            symbol=symbol,
            chain=chain,
            underlying_price=underlying_price,
            context=context,
        )

        report["scan_result"] = result.to_dict()
        report["phase_counts"] = result.phase_counts
        report["reject_reason_counts"] = result.reject_reason_counts
        report["candidate_count"] = result.total_constructed
        report["passed_count"] = result.total_passed

        # Sample candidates for manual inspection
        sample = result.candidates[:5]
        report["sample_candidates"] = [c.to_dict() for c in sample]

        # Diagnostics summary across all candidates
        all_rejects: dict[str, int] = {}
        all_warnings: dict[str, int] = {}
        total_passed_checks = 0
        total_failed_checks = 0

        for c in result.candidates + result.rejected:
            for r in c.diagnostics.reject_reasons:
                all_rejects[r] = all_rejects.get(r, 0) + 1
            for w in c.diagnostics.warnings:
                all_warnings[w] = all_warnings.get(w, 0) + 1
            total_passed_checks += len(c.diagnostics.passed)
            total_failed_checks += len(c.diagnostics.reject_reasons)

        report["diagnostics_summary"] = {
            "total_candidates_examined": (
                result.total_constructed
            ),
            "total_passed": result.total_passed,
            "total_rejected": len(result.rejected),
            "reject_reasons": all_rejects,
            "warning_counts": all_warnings,
            "total_passed_checks": total_passed_checks,
            "total_failed_checks": total_failed_checks,
        }

    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        _log.warning(
            "verify_v2_family failed for %s: %s",
            scanner_key, exc, exc_info=True,
        )

    return report


def get_v2_routing_report() -> dict[str, Any]:
    """Return a comprehensive V2-forward routing report.

    Delegates to ``migration.get_routing_report()`` but adds
    pipeline-level context: which scanner_keys are in the pipeline
    registry and their execution path.
    """
    from app.services.scanner_v2.migration import get_routing_report

    report = get_routing_report()

    # Add pipeline registry visibility
    from app.services.pipeline_scanner_stage import get_default_scanner_registry

    pipeline_registry = get_default_scanner_registry()
    pipeline_options_keys = [
        k for k, v in pipeline_registry.items()
        if v.get("scanner_family") == "options"
    ]
    pipeline_stock_keys = [
        k for k, v in pipeline_registry.items()
        if v.get("scanner_family") == "stock"
    ]

    report["pipeline_registry"] = {
        "options_scanners": pipeline_options_keys,
        "stock_scanners": pipeline_stock_keys,
        "total": len(pipeline_registry),
    }

    return report


def get_family_verification_summary() -> dict[str, Any]:
    """Return a summary of which families are verified and ready.

    For each V2 family, reports:
    - implemented: bool
    - all_keys_routing_v2: bool (all strategy_ids resolve to V2)
    - in_pipeline_registry: bool (all strategy_ids in stage registry)
    - ready_for_legacy_deletion: all above are True
    """
    from app.services.scanner_v2.migration import get_scanner_version
    from app.services.scanner_v2.registry import list_v2_families
    from app.services.pipeline_scanner_stage import get_default_scanner_registry

    families = list_v2_families()
    pipeline_keys = set(get_default_scanner_registry().keys())

    result: dict[str, Any] = {}
    for fm in families:
        sids = fm["strategy_ids"]
        all_v2 = all(get_scanner_version(sid) == "v2" for sid in sids)
        all_in_pipeline = all(sid in pipeline_keys for sid in sids)

        result[fm["family_key"]] = {
            "display_name": fm["display_name"],
            "strategy_ids": sids,
            "implemented": fm["implemented"],
            "all_keys_routing_v2": all_v2,
            "all_keys_in_pipeline": all_in_pipeline,
            "ready_for_legacy_deletion": (
                fm["implemented"] and all_v2 and all_in_pipeline
            ),
        }

    return result
