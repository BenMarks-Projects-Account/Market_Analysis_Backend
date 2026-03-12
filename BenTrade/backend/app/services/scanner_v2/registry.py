"""Options Scanner V2 — family registry.

Single source of truth for which V2 scanner families exist and which
strategy IDs they service.

Usage
-----
    from app.services.scanner_v2.registry import (
        get_v2_family,
        get_v2_scanner,
        is_v2_supported,
        V2_FAMILIES,
    )

    # Check if a strategy has a V2 implementation
    if is_v2_supported("put_credit_spread"):
        scanner = get_v2_scanner("put_credit_spread")
        result = scanner.run(...)

Extension
---------
When implementing a new family (e.g. ``vertical_spreads.py``), register
it by adding an entry to ``_FAMILY_REGISTRY`` and implementing the
lazy-load path in ``_load_family()``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger("bentrade.scanner_v2.registry")


# ── Family metadata ────────────────────────────────────────────────

@dataclass(slots=True)
class V2FamilyMeta:
    """Metadata for a V2 scanner family.

    Parameters
    ----------
    family_key
        Unique key for this family (e.g. ``"vertical_spreads"``).
    display_name
        Human-readable name.
    strategy_ids
        Canonical strategy IDs this family handles.
    leg_count
        Expected number of legs per candidate.
    module_path
        Dotted import path to the family module.
    class_name
        Class name inside the module.
    implemented
        True if the family is fully implemented and ready to run.
        False = skeleton only, not yet wired.
    """

    family_key: str
    display_name: str
    strategy_ids: list[str]
    leg_count: int | str            # int or "3-4" for butterflies
    module_path: str
    class_name: str
    implemented: bool = False


# ── Registry ────────────────────────────────────────────────────────

_FAMILY_REGISTRY: list[V2FamilyMeta] = [
    V2FamilyMeta(
        family_key="vertical_spreads",
        display_name="Vertical Spreads",
        strategy_ids=[
            "put_credit_spread",
            "call_credit_spread",
            "put_debit",
            "call_debit",
        ],
        leg_count=2,
        module_path="app.services.scanner_v2.families.vertical_spreads",
        class_name="VerticalSpreadsV2Scanner",
        implemented=True,
    ),
    V2FamilyMeta(
        family_key="iron_condors",
        display_name="Iron Condors",
        strategy_ids=["iron_condor"],
        leg_count=4,
        module_path="app.services.scanner_v2.families.iron_condors",
        class_name="IronCondorsV2Scanner",
        implemented=True,
    ),
    V2FamilyMeta(
        family_key="butterflies",
        display_name="Butterflies",
        strategy_ids=["butterfly_debit", "iron_butterfly"],
        leg_count="3-4",
        module_path="app.services.scanner_v2.families.butterflies",
        class_name="ButterfliesV2Scanner",
        implemented=True,
    ),
    V2FamilyMeta(
        family_key="calendars",
        display_name="Calendar Spreads",
        strategy_ids=[
            "calendar_spread",
            "calendar_call_spread",
            "calendar_put_spread",
        ],
        leg_count=2,
        module_path="app.services.scanner_v2.families.calendars",
        class_name="CalendarsV2Scanner",
        implemented=False,
    ),
]


# ── Derived lookup tables ───────────────────────────────────────────

V2_FAMILIES: dict[str, V2FamilyMeta] = {
    fm.family_key: fm for fm in _FAMILY_REGISTRY
}
"""Family key → metadata."""

_STRATEGY_TO_FAMILY: dict[str, str] = {}
for _fm in _FAMILY_REGISTRY:
    for _sid in _fm.strategy_ids:
        _STRATEGY_TO_FAMILY[_sid] = _fm.family_key


# ── Public API ──────────────────────────────────────────────────────

def is_v2_supported(strategy_id: str) -> bool:
    """True if the strategy_id has a V2 family registered AND implemented."""
    family_key = _STRATEGY_TO_FAMILY.get(strategy_id)
    if not family_key:
        return False
    meta = V2_FAMILIES.get(family_key)
    return meta is not None and meta.implemented


def get_v2_family(strategy_id: str) -> V2FamilyMeta | None:
    """Return the V2FamilyMeta for a strategy_id, or None."""
    family_key = _STRATEGY_TO_FAMILY.get(strategy_id)
    if not family_key:
        return None
    return V2_FAMILIES.get(family_key)


def get_v2_scanner(strategy_id: str) -> Any:
    """Lazy-load and return a V2 scanner instance for a strategy_id.

    Raises
    ------
    ValueError
        If the strategy_id has no V2 family registered.
    NotImplementedError
        If the family is registered but not yet implemented.
    """
    meta = get_v2_family(strategy_id)
    if meta is None:
        raise ValueError(
            f"No V2 scanner family registered for strategy_id={strategy_id!r}",
        )
    if not meta.implemented:
        raise NotImplementedError(
            f"V2 family {meta.family_key!r} is registered but not yet "
            f"implemented (strategy_id={strategy_id!r})",
        )
    return _load_family(meta)


# ── Lazy loading ────────────────────────────────────────────────────

_SCANNER_CACHE: dict[str, Any] = {}


def _load_family(meta: V2FamilyMeta) -> Any:
    """Import and instantiate a V2 scanner family class.

    Instances are cached by family_key.
    """
    if meta.family_key in _SCANNER_CACHE:
        return _SCANNER_CACHE[meta.family_key]

    import importlib

    mod = importlib.import_module(meta.module_path)
    cls = getattr(mod, meta.class_name)
    instance = cls()
    _SCANNER_CACHE[meta.family_key] = instance
    _log.info("V2 scanner loaded: %s → %s", meta.family_key, meta.class_name)
    return instance


def list_v2_families() -> list[dict[str, Any]]:
    """Return a summary of all registered V2 families for diagnostics."""
    return [
        {
            "family_key": fm.family_key,
            "display_name": fm.display_name,
            "strategy_ids": fm.strategy_ids,
            "leg_count": fm.leg_count,
            "implemented": fm.implemented,
        }
        for fm in _FAMILY_REGISTRY
    ]
