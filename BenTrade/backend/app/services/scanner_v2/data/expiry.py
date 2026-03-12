"""Expiry narrowing — filter expirations by DTE window.

Supports two modes:
1. **Single-expiry** (default) — one DTE window [dte_min, dte_max].
2. **Multi-expiry** (calendars/diagonals) — separate near/far windows.

Every expiration is classified as kept or dropped, with a reason
code for drops (required by scanner-contract.md).

Reason codes
────────────
- ``dte_below_min``  — DTE < dte_min
- ``dte_above_max``  — DTE > dte_max
- ``dte_invalid``    — expiration date could not be parsed
- ``dte_below_near_min`` — multi-expiry: near-leg too short
- ``dte_above_near_max`` — multi-expiry: near-leg too long
- ``dte_below_far_min``  — multi-expiry: far-leg too short
- ``dte_above_far_max``  — multi-expiry: far-leg too long
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from app.services.scanner_v2.data.contracts import (
    V2NarrowingDiagnostics,
    V2NarrowingRequest,
    V2OptionContract,
)


def _compute_dte(expiration: str, today: date) -> int | None:
    """Parse ISO expiration string and return DTE, or None on failure."""
    try:
        exp_date = date.fromisoformat(expiration)
    except (ValueError, TypeError):
        return None
    return (exp_date - today).days


def narrow_expirations(
    contracts: list[V2OptionContract],
    request: V2NarrowingRequest,
    diag: V2NarrowingDiagnostics | None = None,
    today: date | None = None,
) -> list[V2OptionContract]:
    """Filter contracts to those whose expiration falls within DTE window.

    Parameters
    ----------
    contracts
        Normalized contracts from ``chain.normalize_chain()``.
    request
        Narrowing parameters with DTE windows.
    diag
        Diagnostics to populate (optional).
    today
        Override for today's date (testing convenience).

    Returns
    -------
    list[V2OptionContract]
        Contracts with valid, in-window expirations.
    """
    if today is None:
        today = date.today()

    kept: list[V2OptionContract] = []
    seen_exps: dict[str, int | None] = {}  # expiration → DTE (cache)
    drop_reasons: dict[str, int] = defaultdict(int)
    kept_exps: set[str] = set()
    dropped_exps: set[str] = set()

    for c in contracts:
        exp = c.expiration

        # Compute DTE once per unique expiration
        if exp not in seen_exps:
            seen_exps[exp] = _compute_dte(exp, today)

        dte = seen_exps[exp]

        if dte is None:
            drop_reasons["dte_invalid"] += 1
            dropped_exps.add(exp)
            continue

        reason = _check_dte_window(dte, request)
        if reason is not None:
            drop_reasons[reason] += 1
            dropped_exps.add(exp)
            continue

        kept.append(c)
        kept_exps.add(exp)

    # Populate diagnostics
    if diag is not None:
        all_exps = set(seen_exps.keys())
        diag.total_expirations_loaded = len(all_exps)
        diag.expirations_kept = len(kept_exps)
        diag.expirations_dropped = len(dropped_exps - kept_exps)
        diag.expiry_drop_reasons = dict(drop_reasons)
        diag.expirations_kept_list = sorted(kept_exps)
        diag.expirations_dropped_list = sorted(dropped_exps - kept_exps)
        diag.contracts_after_expiry_filter = len(kept)

    return kept


def _check_dte_window(dte: int, request: V2NarrowingRequest) -> str | None:
    """Return drop reason if DTE is outside the requested window, else None.

    For multi-expiry mode, callers handle near/far classification in
    the orchestrator — here we only use the broad window.
    """
    if dte < request.dte_min:
        return "dte_below_min"
    if dte > request.dte_max:
        return "dte_above_max"
    return None


def narrow_expirations_multi(
    contracts: list[V2OptionContract],
    request: V2NarrowingRequest,
    diag: V2NarrowingDiagnostics | None = None,
    today: date | None = None,
) -> tuple[list[V2OptionContract], list[V2OptionContract]]:
    """Multi-expiry narrowing for calendars/diagonals.

    Returns ``(near_contracts, far_contracts)`` — two separate lists
    filtered by near/far DTE windows.

    Uses ``request.near_dte_min / near_dte_max`` and
    ``request.far_dte_min / far_dte_max``.  Falls back to the
    single-expiry window if near/far bounds are not set.
    """
    if today is None:
        today = date.today()

    near_min = request.near_dte_min if request.near_dte_min is not None else request.dte_min
    near_max = request.near_dte_max if request.near_dte_max is not None else request.dte_max
    far_min = request.far_dte_min if request.far_dte_min is not None else request.dte_min
    far_max = request.far_dte_max if request.far_dte_max is not None else request.dte_max

    near: list[V2OptionContract] = []
    far: list[V2OptionContract] = []
    seen_exps: dict[str, int | None] = {}
    drop_reasons: dict[str, int] = defaultdict(int)
    kept_exps: set[str] = set()
    dropped_exps: set[str] = set()

    for c in contracts:
        exp = c.expiration
        if exp not in seen_exps:
            seen_exps[exp] = _compute_dte(exp, today)

        dte = seen_exps[exp]
        if dte is None:
            drop_reasons["dte_invalid"] += 1
            dropped_exps.add(exp)
            continue

        in_near = near_min <= dte <= near_max
        in_far = far_min <= dte <= far_max

        if in_near:
            near.append(c)
            kept_exps.add(exp)
        elif in_far:
            far.append(c)
            kept_exps.add(exp)
        else:
            # Classify drop reason
            if dte < near_min:
                drop_reasons["dte_below_near_min"] += 1
            elif near_max < dte < far_min:
                drop_reasons["dte_between_windows"] += 1
            elif dte > far_max:
                drop_reasons["dte_above_far_max"] += 1
            dropped_exps.add(exp)

    if diag is not None:
        all_exps = set(seen_exps.keys())
        diag.total_expirations_loaded = len(all_exps)
        diag.expirations_kept = len(kept_exps)
        diag.expirations_dropped = len(dropped_exps - kept_exps)
        diag.expiry_drop_reasons = dict(drop_reasons)
        diag.expirations_kept_list = sorted(kept_exps)
        diag.expirations_dropped_list = sorted(dropped_exps - kept_exps)
        diag.contracts_after_expiry_filter = len(near) + len(far)

    return near, far
