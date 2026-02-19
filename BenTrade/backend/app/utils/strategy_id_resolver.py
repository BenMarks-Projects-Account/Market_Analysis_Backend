"""Single-entry strategy resolver — the ONE place strategy strings are validated.

Every inbound boundary (scanner output, workbench lookup, report normalization,
lifecycle events) calls ``resolve_strategy_id()`` to convert any raw strategy
/ spread_type / alias string into a canonical ``strategy_id``.

Rules
-----
1. Canonical IDs pass through unchanged (no event).
2. Known aliases resolve to canonical but emit ``STRATEGY_ALIAS_USED`` warn.
3. Unknown / empty strings raise ``StrategyResolutionError`` (→ HTTP 400).
"""

from __future__ import annotations

import logging
from typing import Any

from app.utils.trade_key import CANONICAL_STRATEGY_IDS

logger = logging.getLogger(__name__)

# ── alias table ──────────────────────────────────────────────────────
# Maps every known legacy / shorthand / variant string to its canonical
# strategy_id.  Canonical IDs intentionally included so the lookup is a
# single dict hit — the ``was_alias`` flag distinguishes them.

_STRATEGY_ALIASES: dict[str, str] = {
    # credit spreads
    "put_credit_spread": "put_credit_spread",
    "put_credit": "put_credit_spread",
    "credit_put_spread": "put_credit_spread",
    "call_credit_spread": "call_credit_spread",
    "call_credit": "call_credit_spread",
    "credit_call_spread": "call_credit_spread",
    # debit spreads
    "put_debit": "put_debit",
    "debit_put_spread": "put_debit",
    "call_debit": "call_debit",
    "debit_call_spread": "call_debit",
    # butterflies
    "butterfly_debit": "butterfly_debit",
    "debit_call_butterfly": "butterfly_debit",
    "debit_put_butterfly": "butterfly_debit",
    "debit_butterfly": "butterfly_debit",
    "butterflies": "butterfly_debit",
    "iron_butterfly": "iron_butterfly",
    # multi-leg
    "iron_condor": "iron_condor",
    # calendars
    "calendar_spread": "calendar_spread",
    "calendar_call_spread": "calendar_call_spread",
    "calendar_put_spread": "calendar_put_spread",
    # income / single-leg
    "csp": "csp",
    "cash_secured_put": "csp",
    "covered_call": "covered_call",
    "income": "income",
    "single": "single",
    "long_call": "long_call",
    "long_put": "long_put",
}

# Verify at import time that every alias target is in fact canonical.
for _alias, _target in _STRATEGY_ALIASES.items():
    assert _target in CANONICAL_STRATEGY_IDS, (
        f"BUG: alias '{_alias}' maps to '{_target}' which is not in CANONICAL_STRATEGY_IDS"
    )


# ── error type ───────────────────────────────────────────────────────


class StrategyResolutionError(ValueError):
    """Raised when a strategy string cannot be resolved to a canonical ID."""

    def __init__(self, provided: str) -> None:
        self.provided = provided
        super().__init__(
            f"Unknown strategy '{provided}'. "
            f"Valid strategy IDs: {', '.join(sorted(CANONICAL_STRATEGY_IDS))}"
        )


# ── resolver ─────────────────────────────────────────────────────────


def resolve_strategy_id(
    value: Any,
    *,
    emit_event: bool = True,
) -> str:
    """Resolve *value* to a canonical ``strategy_id``.

    Parameters
    ----------
    value:
        Raw string (from ``spread_type``, ``strategy``, ``strategy_id``,
        URL params, etc.).
    emit_event:
        When ``True`` (default) and *value* is a known **alias** (not
        already canonical), a ``STRATEGY_ALIAS_USED`` validation event is
        emitted.

    Returns
    -------
    str
        A value guaranteed to be in ``CANONICAL_STRATEGY_IDS``.

    Raises
    ------
    StrategyResolutionError
        When *value* is empty or not a known alias/canonical ID.
    """
    raw = str(value or "").strip().lower()
    if not raw:
        raise StrategyResolutionError("")

    target = _STRATEGY_ALIASES.get(raw)
    if target is None:
        raise StrategyResolutionError(raw)

    was_alias = raw != target
    if was_alias and emit_event:
        _emit_alias_event(raw, target)

    return target


def resolve_strategy_id_or_none(value: Any) -> str | None:
    """Like ``resolve_strategy_id`` but returns ``None`` for blanks/unknowns
    instead of raising.  Still emits the alias event for known aliases."""
    try:
        return resolve_strategy_id(value)
    except StrategyResolutionError:
        return None


# ── event emitter (lazy import to avoid circular deps) ───────────────


def _emit_alias_event(provided: str, canonical: str) -> None:
    """Emit a STRATEGY_ALIAS_USED validation event."""
    try:
        from app.services.validation_events import emit_validation_event

        emit_validation_event(
            severity="warn",
            code="STRATEGY_ALIAS_USED",
            message=f"Legacy alias '{provided}' resolved to canonical '{canonical}'",
            context={
                "provided": provided,
                "canonical": canonical,
            },
        )
    except Exception:
        pass
    logger.warning(
        "STRATEGY_ALIAS_USED: '%s' -> '%s'",
        provided,
        canonical,
    )
