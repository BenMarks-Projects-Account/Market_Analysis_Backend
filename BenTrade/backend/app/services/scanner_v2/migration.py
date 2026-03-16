"""Options Scanner V2 — migration routing seam.

Controls whether a given scanner_key runs legacy (V1) or V2.

As of Prompt 13, V2 is the **primary** scanner architecture.  All four
implemented families (vertical spreads, iron condors, butterflies,
calendars/diagonals) route through V2 by default.  Legacy fallback
only applies to scanner_keys that have NO V2 implementation.

Routing model
─────────────
- Default: V2 if implemented, legacy otherwise.
- Per-key override: ``_SCANNER_VERSION_OVERRIDES`` can force a key
  back to v1 for emergency rollback.
- Side-by-side mode: run both and compare (built in Prompt 2).

Retirement note
───────────────
This module is a TEMPORARY migration seam.  Once all families are
validated and legacy code is deleted (target: Prompt 15), this module
can be removed entirely.  Callers will import V2 scanners directly.

Usage
─────
    from app.services.scanner_v2.migration import (
        get_scanner_version,
        should_run_v2,
        execute_v2_scanner,
        get_routing_report,
    )
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.scanner_v2.registry import (
    get_v2_family,
    get_v2_scanner,
    is_v2_supported,
    list_v2_families,
)

_log = logging.getLogger("bentrade.scanner_v2.migration")


# ── Version overrides ───────────────────────────────────────────────
#
# As of Prompt 13, V2 is the default for ALL implemented families.
# This map is only used for emergency rollback — to force a specific
# scanner_key back to legacy ("v1").
#
# If a key is NOT in this map, the routing decision is:
#   V2 if is_v2_supported(key) else v1.
#
# To roll back a family: add an entry here with value "v1".
# To explicitly confirm V2: add an entry with value "v2" (optional).
#
# RETIREMENT TARGET: delete this map and get_scanner_version() logic
# once legacy is fully retired (Prompt 15).

_SCANNER_VERSION_OVERRIDES: dict[str, str] = {
    # No overrides — all implemented V2 families run V2 by default.
    # To emergency-rollback a family, add e.g.:
    #   "iron_condor": "v1",
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

    Routing logic (V2-forward, Prompt 13):
    1. If the key has an explicit override in ``_SCANNER_VERSION_OVERRIDES``,
       use that (unless the override says v2 but V2 is not implemented →
       fall back to v1).
    2. Otherwise: v2 if ``is_v2_supported(key)`` else v1.

    This means V2 is the **default** for all implemented families.
    Legacy is only used for keys with no V2 implementation.
    """
    override = _SCANNER_VERSION_OVERRIDES.get(scanner_key)
    if override is not None:
        if override == "v2" and not is_v2_supported(scanner_key):
            _log.warning(
                "scanner_key=%r overridden to v2 but V2 family not "
                "implemented — falling back to v1",
                scanner_key,
            )
            return "v1"
        if override == "v1":
            _log.info(
                "scanner_key=%r forced to v1 via override (emergency rollback)",
                scanner_key,
            )
        return override

    # V2-forward default: use V2 if implemented, otherwise v1.
    if is_v2_supported(scanner_key):
        return "v2"
    return "v1"


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
        "candidates": accepted,           # pipeline_scanner_stage reads this
        "accepted_trades": accepted,       # legacy alias
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
    """Override the version for a scanner_key.  For testing/admin.

    Sets an entry in ``_SCANNER_VERSION_OVERRIDES``.  To remove an
    override and return to V2-forward default, use
    ``clear_scanner_version_override()``.
    """
    if version not in ("v1", "v2"):
        raise ValueError(f"version must be 'v1' or 'v2', got {version!r}")
    _SCANNER_VERSION_OVERRIDES[scanner_key] = version


def clear_scanner_version_override(scanner_key: str) -> None:
    """Remove a version override, returning to V2-forward default."""
    _SCANNER_VERSION_OVERRIDES.pop(scanner_key, None)


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
    """Return current migration status for diagnostics.

    .. deprecated:: Prompt 13
       Prefer ``get_routing_report()`` for richer visibility.
    """
    return {
        "version_overrides": dict(_SCANNER_VERSION_OVERRIDES),
        "side_by_side_enabled": _SIDE_BY_SIDE_ENABLED,
        "side_by_side_keys": sorted(_SIDE_BY_SIDE_KEYS) if _SIDE_BY_SIDE_KEYS else "all",
        "v2_families_implemented": [
            fm["family_key"] for fm in list_v2_families() if fm["implemented"]
        ],
    }


def get_routing_report() -> dict[str, Any]:
    """Return a comprehensive V2-forward routing report.

    Designed for manual verification (Prompt 13).  Reports:
    - Which families are V2 and which strategy_ids they serve.
    - Which scanner_keys have overrides.
    - Which scanner_keys would fall to legacy (no V2 implementation).
    - Retirement readiness per family.
    """
    families = list_v2_families()

    # Collect all known scanner_keys from V2 families
    v2_keys: dict[str, dict[str, Any]] = {}
    for fm in families:
        for sid in fm["strategy_ids"]:
            v2_keys[sid] = {
                "family_key": fm["family_key"],
                "display_name": fm["display_name"],
                "implemented": fm["implemented"],
                "routing": get_scanner_version(sid),
                "override": _SCANNER_VERSION_OVERRIDES.get(sid),
            }

    # Legacy-only keys: anything in overrides set to v1
    legacy_forced = {
        k: v for k, v in _SCANNER_VERSION_OVERRIDES.items() if v == "v1"
    }

    return {
        "routing_model": "v2_forward",
        "v2_families": families,
        "scanner_key_routing": v2_keys,
        "overrides_active": dict(_SCANNER_VERSION_OVERRIDES),
        "legacy_forced_keys": list(legacy_forced.keys()),
        "side_by_side_enabled": _SIDE_BY_SIDE_ENABLED,
        "retirement_readiness": {
            fm["family_key"]: {
                "implemented": fm["implemented"],
                "strategy_ids": fm["strategy_ids"],
                "all_routing_v2": all(
                    get_scanner_version(sid) == "v2"
                    for sid in fm["strategy_ids"]
                ),
                "ready_for_legacy_deletion": (
                    fm["implemented"]
                    and all(
                        get_scanner_version(sid) == "v2"
                        for sid in fm["strategy_ids"]
                    )
                ),
            }
            for fm in families
        },
    }
