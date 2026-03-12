"""Options Scanner V2 — migration routing seam.

Controls whether a given scanner_key runs legacy (V1) or V2.

During migration, both code paths coexist.  This module provides the
routing decision and the hooks that ``pipeline_scanner_stage.py`` will
call to dispatch to the correct executor.

Routing model
─────────────
- Default: all scanners run legacy (``"v1"``).
- Per-family cutover: flip entries in ``_SCANNER_VERSION_MAP``.
- Side-by-side mode: run both and compare (built in Prompt 2).

Usage
─────
    from app.services.scanner_v2.migration import (
        get_scanner_version,
        should_run_v2,
        execute_v2_scanner,
    )

    version = get_scanner_version("put_credit_spread")  # "v1" or "v2"

    if should_run_v2("put_credit_spread"):
        result = execute_v2_scanner("put_credit_spread", ...)
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.scanner_v2.registry import (
    get_v2_family,
    get_v2_scanner,
    is_v2_supported,
)

_log = logging.getLogger("bentrade.scanner_v2.migration")


# ── Version map ─────────────────────────────────────────────────────
#
# Change entries from "v1" to "v2" to cut over individual scanner_keys.
# Only scanner_keys with a registered + implemented V2 family can be
# set to "v2".  Invalid entries are ignored (default to "v1").
#
# This map is the ONLY place where the cutover decision lives.

_SCANNER_VERSION_MAP: dict[str, str] = {
    # Vertical spreads
    "put_credit_spread":   "v1",
    "call_credit_spread":  "v1",
    "put_debit":           "v1",
    "call_debit":          "v1",
    # Iron condors
    "iron_condor":         "v1",
    # Butterflies
    "butterfly_debit":     "v1",
    "iron_butterfly":      "v1",
    # Calendars
    "calendar_spread":     "v1",
    "calendar_call_spread": "v1",
    "calendar_put_spread": "v1",
}


# ── Side-by-side comparison mode ────────────────────────────────────
#
# When True, BOTH v1 and v2 run for scanner_keys that have V2
# implementations.  The comparison harness (Prompt 2) collects both
# outputs and produces a diff report.

_SIDE_BY_SIDE_ENABLED: bool = False

# Scanner keys opted into side-by-side comparison.
# Empty set = all V2-capable keys are compared when side-by-side is on.
_SIDE_BY_SIDE_KEYS: set[str] = set()


# ── Public API ──────────────────────────────────────────────────────

def get_scanner_version(scanner_key: str) -> str:
    """Return ``"v1"`` or ``"v2"`` for a scanner_key.

    Returns ``"v1"`` if:
    - The key is not in the version map.
    - The key is mapped to ``"v2"`` but the V2 family is not implemented.
    """
    version = _SCANNER_VERSION_MAP.get(scanner_key, "v1")
    if version == "v2" and not is_v2_supported(scanner_key):
        _log.warning(
            "scanner_key=%r mapped to v2 but V2 family not implemented — "
            "falling back to v1",
            scanner_key,
        )
        return "v1"
    return version


def should_run_v2(scanner_key: str) -> bool:
    """True if the scanner_key should run the V2 path."""
    return get_scanner_version(scanner_key) == "v2"


def should_run_side_by_side(scanner_key: str) -> bool:
    """True if side-by-side comparison should run for this scanner_key.

    Requires:
    1. Side-by-side mode is enabled.
    2. The scanner_key has a V2 implementation.
    3. Either ``_SIDE_BY_SIDE_KEYS`` is empty (all-in) or the key is
       in the set.
    """
    if not _SIDE_BY_SIDE_ENABLED:
        return False
    if not is_v2_supported(scanner_key):
        return False
    if _SIDE_BY_SIDE_KEYS and scanner_key not in _SIDE_BY_SIDE_KEYS:
        return False
    return True


def execute_v2_scanner(
    scanner_key: str,
    *,
    symbol: str,
    chain: dict[str, Any],
    underlying_price: float | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the V2 scanner for a scanner_key and return a dict result.

    This is the entry point that ``pipeline_scanner_stage.py`` calls
    when the routing decision is ``"v2"``.

    Returns a dict compatible with the pipeline's scanner output shape
    so downstream normalization can handle it.

    Raises
    ------
    ValueError
        If scanner_key has no V2 family.
    NotImplementedError
        If V2 family not yet implemented.
    """
    meta = get_v2_family(scanner_key)
    if meta is None:
        raise ValueError(f"No V2 family for scanner_key={scanner_key!r}")

    scanner = get_v2_scanner(scanner_key)
    strategy_id = scanner_key  # scanner_key == canonical strategy_id

    result = scanner.run(
        scanner_key=scanner_key,
        strategy_id=strategy_id,
        symbol=symbol,
        chain=chain,
        underlying_price=underlying_price,
        context=context,
    )

    # Convert V2ScanResult → legacy-compatible dict shape so the
    # pipeline_scanner_stage normalization layer can consume it.
    return _v2_result_to_legacy_shape(result)


def _v2_result_to_legacy_shape(result: Any) -> dict[str, Any]:
    """Convert a V2ScanResult to the dict shape pipeline expects.

    The pipeline's ``normalize_scanner_candidates()`` expects:
    - ``accepted_trades``: list of trade dicts
    - ``candidate_count``: int
    - ``accepted_count``: int
    - ``filter_trace``: dict
    - ``timestamp``: str

    V2 candidates are serialized via ``to_dict()`` and placed in
    ``accepted_trades``.  The full V2ScanResult is also attached
    under ``_v2_scan_result`` for the comparison harness.
    """
    accepted = [c.to_dict() for c in result.candidates]

    # Build a filter_trace compatible with scanner-contract.md
    filter_trace = {
        "preset_name": "v2_wide_scan",
        "resolved_thresholds": {},  # V2 has no preset thresholds
        "stage_counts": [
            {"stage": pc["phase"], "remaining": pc["remaining"]}
            for pc in result.phase_counts
        ],
        "rejection_reason_counts": result.reject_reason_counts,
        "data_quality_counts": _extract_dq_counts(result.reject_reason_counts),
    }

    return {
        "accepted_trades": accepted,
        "candidate_count": result.total_constructed,
        "accepted_count": result.total_passed,
        "filter_trace": filter_trace,
        "timestamp": result.generated_at,
        "_v2_scan_result": result.to_dict(),
    }


def _extract_dq_counts(reject_counts: dict[str, int]) -> dict[str, int]:
    """Pull data-quality codes from reject counts."""
    dq_codes = {
        "v2_missing_quote", "v2_inverted_quote", "v2_zero_mid",
        "v2_missing_oi", "v2_missing_volume",
    }
    dq = {code: count for code, count in reject_counts.items() if code in dq_codes}
    dq["total_invalid"] = sum(dq.values())
    return dq


# ── Configuration helpers (for tests / admin) ──────────────────────

def set_scanner_version(scanner_key: str, version: str) -> None:
    """Override the version for a scanner_key.  For testing/admin."""
    if version not in ("v1", "v2"):
        raise ValueError(f"version must be 'v1' or 'v2', got {version!r}")
    _SCANNER_VERSION_MAP[scanner_key] = version


def enable_side_by_side(
    enabled: bool = True,
    keys: set[str] | None = None,
) -> None:
    """Toggle side-by-side comparison mode.  For testing/admin."""
    global _SIDE_BY_SIDE_ENABLED
    _SIDE_BY_SIDE_ENABLED = enabled
    if keys is not None:
        _SIDE_BY_SIDE_KEYS.clear()
        _SIDE_BY_SIDE_KEYS.update(keys)


def get_migration_status() -> dict[str, Any]:
    """Return current migration status for diagnostics."""
    return {
        "scanner_versions": dict(_SCANNER_VERSION_MAP),
        "side_by_side_enabled": _SIDE_BY_SIDE_ENABLED,
        "side_by_side_keys": sorted(_SIDE_BY_SIDE_KEYS) if _SIDE_BY_SIDE_KEYS else "all",
        "v2_families_implemented": [
            fm_key for fm_key, fm in __import__(
                "app.services.scanner_v2.registry", fromlist=["V2_FAMILIES"],
            ).V2_FAMILIES.items()
            if fm.implemented
        ],
    }
