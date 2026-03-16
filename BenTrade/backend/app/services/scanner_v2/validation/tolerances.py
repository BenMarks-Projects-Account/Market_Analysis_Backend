"""Tolerance policy — centralized default tolerances for math verification.

All tolerance thresholds live here.  No magic numbers in validation
modules.  Family-specific overrides are supported via the
``get_tolerances()`` function.

Design
──────
Each metric gets a ``V2ToleranceSpec(abs_pass, abs_warn, rel_warn)``:

- ``abs_pass``: Absolute delta ≤ this → PASS.
- ``abs_warn``: Absolute delta between ``abs_pass`` and ``abs_warn`` → WARN.
- ``abs_warn`` < delta → FAIL (unless rescued by ``rel_warn``).
- ``rel_warn``: Relative tolerance (fraction of expected).
  Provides an alternative WARN threshold for large values.

Monetary values
───────────────
- ``net_credit`` / ``net_debit`` are per-share (small numbers: 0.01–10.0).
  Tight absolute tolerance.
- ``max_profit`` / ``max_loss`` are per-contract (larger: 1–5000).
  Slightly wider absolute tolerance, with relative fallback.
- ``width`` is per-share (strike distance: 1.0–50.0).
  Should be exact from strike arithmetic.
- ``breakeven`` is a price level (300–600+).
  Absolute tolerance is looser; relative is tighter.
- ``ror`` is a ratio (0.0–2.0).
  Small absolute tolerance.
"""

from __future__ import annotations

from app.services.scanner_v2.validation.contracts import V2ToleranceSpec


# ═══════════════════════════════════════════════════════════════════
#  Default tolerance table
# ═══════════════════════════════════════════════════════════════════

# Per-share pricing (small numbers)
_TOL_NET_CREDIT = V2ToleranceSpec(abs_pass=0.005, abs_warn=0.02)
_TOL_NET_DEBIT = V2ToleranceSpec(abs_pass=0.005, abs_warn=0.02)

# Per-contract pricing (larger numbers)
_TOL_MAX_PROFIT = V2ToleranceSpec(abs_pass=0.50, abs_warn=2.00, rel_warn=0.01)
_TOL_MAX_LOSS = V2ToleranceSpec(abs_pass=0.50, abs_warn=2.00, rel_warn=0.01)

# Strike distance (should be exact)
_TOL_WIDTH = V2ToleranceSpec(abs_pass=0.001, abs_warn=0.01)

# Price level (larger)
_TOL_BREAKEVEN = V2ToleranceSpec(abs_pass=0.01, abs_warn=0.05)

# Ratio
_TOL_ROR = V2ToleranceSpec(abs_pass=0.001, abs_warn=0.01)

# EV (per-contract)
_TOL_EV = V2ToleranceSpec(abs_pass=1.00, abs_warn=5.00, rel_warn=0.02)


# ═══════════════════════════════════════════════════════════════════
#  Default tolerance map
# ═══════════════════════════════════════════════════════════════════

DEFAULT_TOLERANCES: dict[str, V2ToleranceSpec] = {
    "net_credit": _TOL_NET_CREDIT,
    "net_debit": _TOL_NET_DEBIT,
    "max_profit": _TOL_MAX_PROFIT,
    "max_loss": _TOL_MAX_LOSS,
    "width": _TOL_WIDTH,
    "breakeven": _TOL_BREAKEVEN,
    "ror": _TOL_ROR,
    "ev": _TOL_EV,
}
"""Keyed by metric name.  Used by ``math_checks.run_math_verification()``."""


# ═══════════════════════════════════════════════════════════════════
#  Family-specific overrides
# ═══════════════════════════════════════════════════════════════════

_FAMILY_OVERRIDES: dict[str, dict[str, V2ToleranceSpec]] = {
    # Iron condors have wider pricing tolerances
    # (net credit is sum of two sides, accumulated rounding)
    "iron_condors": {
        "net_credit": V2ToleranceSpec(abs_pass=0.01, abs_warn=0.04),
        "max_profit": V2ToleranceSpec(abs_pass=1.00, abs_warn=4.00, rel_warn=0.02),
        "max_loss": V2ToleranceSpec(abs_pass=1.00, abs_warn=4.00, rel_warn=0.02),
    },
    # Butterflies have wider tolerances due to 3–4 leg accumulation
    "butterflies": {
        "net_credit": V2ToleranceSpec(abs_pass=0.01, abs_warn=0.04),
        "net_debit": V2ToleranceSpec(abs_pass=0.01, abs_warn=0.04),
        "max_profit": V2ToleranceSpec(abs_pass=1.00, abs_warn=5.00, rel_warn=0.02),
        "max_loss": V2ToleranceSpec(abs_pass=1.00, abs_warn=5.00, rel_warn=0.02),
    },
    # Calendars/diagonals: net_debit is straightforward (2-leg),
    # max_loss is approximate, many fields are deferred (None)
    "calendars": {
        "net_debit": V2ToleranceSpec(abs_pass=0.005, abs_warn=0.02),
        "max_loss": V2ToleranceSpec(abs_pass=0.50, abs_warn=2.00, rel_warn=0.01),
    },
}


def get_tolerances(
    family_key: str | None = None,
) -> dict[str, V2ToleranceSpec]:
    """Return tolerance map for a specific family or defaults.

    If ``family_key`` has registered overrides, those are merged on
    top of the defaults (overrides win per-metric).

    Parameters
    ----------
    family_key
        Family key (e.g. ``"vertical_spreads"``).  None = defaults only.

    Returns
    -------
    dict[str, V2ToleranceSpec]
        Tolerance map keyed by metric name.
    """
    base = dict(DEFAULT_TOLERANCES)

    if family_key and family_key in _FAMILY_OVERRIDES:
        base.update(_FAMILY_OVERRIDES[family_key])

    return base


def get_tolerance(
    metric: str,
    family_key: str | None = None,
) -> V2ToleranceSpec:
    """Return the tolerance spec for one metric.

    Falls back to a permissive default if the metric is not in the map.
    """
    tol_map = get_tolerances(family_key)
    return tol_map.get(metric, V2ToleranceSpec(abs_pass=0.01, abs_warn=0.10))
