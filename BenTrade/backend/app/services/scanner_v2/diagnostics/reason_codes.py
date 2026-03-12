"""V2 Diagnostics — reason code registry.

Central registry of all V2 reason codes (reject, pass, warning).
Every code emitted by any phase must be registered here.

Design rules
------------
1. All codes use ``v2_`` prefix to distinguish from canonical taxonomy.
2. Codes are **stable identifiers** — never rename, only deprecate.
3. Each code has a category, default severity, and human label.
4. Helper functions validate codes and retrieve metadata.

Category vocabulary
-------------------
STRUCTURAL   Leg count, side/type, width, expiry consistency.
QUOTE        Bid/ask presence, inversion, mid validity.
LIQUIDITY    OI, volume presence.
MATH         Derived field integrity (max_loss, max_profit, breakevens, etc.).
THRESHOLD    Strategy-quality gates (EV, POP, RoR, credit floors).
              Not emitted by V2 base — reserved for downstream/family.

Mapping to canonical taxonomy
-----------------------------
``to_canonical()`` maps V2 codes to the unprefixed codes in
``docs/standards/rejection-taxonomy.md``.
"""

from __future__ import annotations

from typing import Any, NamedTuple


# ── Category constants ──────────────────────────────────────────────

CAT_STRUCTURAL = "structural"
CAT_QUOTE = "quote"
CAT_LIQUIDITY = "liquidity"
CAT_MATH = "math"
CAT_THRESHOLD = "threshold"

ALL_CATEGORIES = frozenset({
    CAT_STRUCTURAL, CAT_QUOTE, CAT_LIQUIDITY, CAT_MATH, CAT_THRESHOLD,
})

# ── Severity constants ─────────────────────────────────────────────

SEV_ERROR = "error"
SEV_WARNING = "warning"
SEV_INFO = "info"

# ── Kind constants ──────────────────────────────────────────────────

KIND_REJECT = "reject"
KIND_PASS = "pass"
KIND_WARNING = "warning"


# ── Code metadata ──────────────────────────────────────────────────

class CodeInfo(NamedTuple):
    """Metadata for a reason code."""
    code: str
    category: str
    severity: str
    label: str


# =====================================================================
#  REJECT CODES — emitted by phases C, D, E
# =====================================================================

# Phase C — structural
REJECT_MALFORMED_LEGS = "v2_malformed_legs"
REJECT_INVALID_WIDTH = "v2_invalid_width"
REJECT_NON_POSITIVE_CREDIT = "v2_non_positive_credit"
REJECT_IMPOSSIBLE_PRICING = "v2_impossible_pricing"
REJECT_MISMATCHED_EXPIRY = "v2_mismatched_expiry"

# Phase D — quote
REJECT_MISSING_QUOTE = "v2_missing_quote"
REJECT_INVERTED_QUOTE = "v2_inverted_quote"
REJECT_ZERO_MID = "v2_zero_mid"

# Phase D — liquidity
REJECT_MISSING_OI = "v2_missing_oi"
REJECT_MISSING_VOLUME = "v2_missing_volume"

# Phase E — math
REJECT_IMPOSSIBLE_MAX_LOSS = "v2_impossible_max_loss"
REJECT_IMPOSSIBLE_MAX_PROFIT = "v2_impossible_max_profit"
REJECT_NON_FINITE_MATH = "v2_non_finite_math"
REJECT_WIDTH_MISMATCH = "v2_width_mismatch"
REJECT_CREDIT_MISMATCH = "v2_credit_mismatch"
REJECT_DEBIT_MISMATCH = "v2_debit_mismatch"
REJECT_MAX_PROFIT_MISMATCH = "v2_max_profit_mismatch"
REJECT_MAX_LOSS_MISMATCH = "v2_max_loss_mismatch"
REJECT_BREAKEVEN_MISMATCH = "v2_breakeven_mismatch"
REJECT_ROR_MISMATCH = "v2_ror_mismatch"


# =====================================================================
#  WARNING CODES — emitted by phase E (math tolerance warnings)
# =====================================================================

WARN_WIDTH_MISMATCH = "v2_warn_width_mismatch"
WARN_CREDIT_MISMATCH = "v2_warn_credit_mismatch"
WARN_DEBIT_MISMATCH = "v2_warn_debit_mismatch"
WARN_MAX_PROFIT_MISMATCH = "v2_warn_max_profit_mismatch"
WARN_MAX_LOSS_MISMATCH = "v2_warn_max_loss_mismatch"
WARN_BREAKEVEN_MISMATCH = "v2_warn_breakeven_mismatch"
WARN_ROR_MISMATCH = "v2_warn_ror_mismatch"
WARN_POP_MISSING = "v2_warn_pop_missing"
WARN_EV_MISSING = "v2_warn_ev_missing"


# =====================================================================
#  PASS CODES — emitted by phase F for passing candidates
# =====================================================================

PASS_STRUCTURAL_VALID = "v2_pass_structural_valid"
PASS_QUOTES_CLEAN = "v2_pass_quotes_clean"
PASS_LIQUIDITY_PRESENT = "v2_pass_liquidity_present"
PASS_MATH_CONSISTENT = "v2_pass_math_consistent"
PASS_ALL_PHASES = "v2_pass_all_phases"


# =====================================================================
#  Registry — code → metadata lookup
# =====================================================================

_REJECT_REGISTRY: dict[str, CodeInfo] = {
    # Structural
    REJECT_MALFORMED_LEGS:      CodeInfo(REJECT_MALFORMED_LEGS, CAT_STRUCTURAL, SEV_ERROR, "Malformed legs"),
    REJECT_INVALID_WIDTH:       CodeInfo(REJECT_INVALID_WIDTH, CAT_STRUCTURAL, SEV_ERROR, "Invalid width"),
    REJECT_NON_POSITIVE_CREDIT: CodeInfo(REJECT_NON_POSITIVE_CREDIT, CAT_STRUCTURAL, SEV_ERROR, "Non-positive credit"),
    REJECT_IMPOSSIBLE_PRICING:  CodeInfo(REJECT_IMPOSSIBLE_PRICING, CAT_STRUCTURAL, SEV_ERROR, "Impossible pricing"),
    REJECT_MISMATCHED_EXPIRY:   CodeInfo(REJECT_MISMATCHED_EXPIRY, CAT_STRUCTURAL, SEV_ERROR, "Mismatched expiry"),
    # Quote
    REJECT_MISSING_QUOTE:       CodeInfo(REJECT_MISSING_QUOTE, CAT_QUOTE, SEV_ERROR, "Missing quote"),
    REJECT_INVERTED_QUOTE:      CodeInfo(REJECT_INVERTED_QUOTE, CAT_QUOTE, SEV_ERROR, "Inverted quote"),
    REJECT_ZERO_MID:            CodeInfo(REJECT_ZERO_MID, CAT_QUOTE, SEV_ERROR, "Zero mid"),
    # Liquidity
    REJECT_MISSING_OI:          CodeInfo(REJECT_MISSING_OI, CAT_LIQUIDITY, SEV_ERROR, "Missing open interest"),
    REJECT_MISSING_VOLUME:      CodeInfo(REJECT_MISSING_VOLUME, CAT_LIQUIDITY, SEV_ERROR, "Missing volume"),
    # Math
    REJECT_IMPOSSIBLE_MAX_LOSS:   CodeInfo(REJECT_IMPOSSIBLE_MAX_LOSS, CAT_MATH, SEV_ERROR, "Impossible max loss"),
    REJECT_IMPOSSIBLE_MAX_PROFIT: CodeInfo(REJECT_IMPOSSIBLE_MAX_PROFIT, CAT_MATH, SEV_ERROR, "Impossible max profit"),
    REJECT_NON_FINITE_MATH:       CodeInfo(REJECT_NON_FINITE_MATH, CAT_MATH, SEV_ERROR, "Non-finite math"),
    REJECT_WIDTH_MISMATCH:        CodeInfo(REJECT_WIDTH_MISMATCH, CAT_MATH, SEV_ERROR, "Width mismatch"),
    REJECT_CREDIT_MISMATCH:       CodeInfo(REJECT_CREDIT_MISMATCH, CAT_MATH, SEV_ERROR, "Credit mismatch"),
    REJECT_DEBIT_MISMATCH:        CodeInfo(REJECT_DEBIT_MISMATCH, CAT_MATH, SEV_ERROR, "Debit mismatch"),
    REJECT_MAX_PROFIT_MISMATCH:   CodeInfo(REJECT_MAX_PROFIT_MISMATCH, CAT_MATH, SEV_ERROR, "Max profit mismatch"),
    REJECT_MAX_LOSS_MISMATCH:     CodeInfo(REJECT_MAX_LOSS_MISMATCH, CAT_MATH, SEV_ERROR, "Max loss mismatch"),
    REJECT_BREAKEVEN_MISMATCH:    CodeInfo(REJECT_BREAKEVEN_MISMATCH, CAT_MATH, SEV_ERROR, "Breakeven mismatch"),
    REJECT_ROR_MISMATCH:          CodeInfo(REJECT_ROR_MISMATCH, CAT_MATH, SEV_ERROR, "RoR mismatch"),
}

_WARN_REGISTRY: dict[str, CodeInfo] = {
    WARN_WIDTH_MISMATCH:      CodeInfo(WARN_WIDTH_MISMATCH, CAT_MATH, SEV_WARNING, "Width near tolerance"),
    WARN_CREDIT_MISMATCH:     CodeInfo(WARN_CREDIT_MISMATCH, CAT_MATH, SEV_WARNING, "Credit near tolerance"),
    WARN_DEBIT_MISMATCH:      CodeInfo(WARN_DEBIT_MISMATCH, CAT_MATH, SEV_WARNING, "Debit near tolerance"),
    WARN_MAX_PROFIT_MISMATCH: CodeInfo(WARN_MAX_PROFIT_MISMATCH, CAT_MATH, SEV_WARNING, "Max profit near tolerance"),
    WARN_MAX_LOSS_MISMATCH:   CodeInfo(WARN_MAX_LOSS_MISMATCH, CAT_MATH, SEV_WARNING, "Max loss near tolerance"),
    WARN_BREAKEVEN_MISMATCH:  CodeInfo(WARN_BREAKEVEN_MISMATCH, CAT_MATH, SEV_WARNING, "Breakeven near tolerance"),
    WARN_ROR_MISMATCH:        CodeInfo(WARN_ROR_MISMATCH, CAT_MATH, SEV_WARNING, "RoR near tolerance"),
    WARN_POP_MISSING:         CodeInfo(WARN_POP_MISSING, CAT_MATH, SEV_WARNING, "POP could not be computed"),
    WARN_EV_MISSING:          CodeInfo(WARN_EV_MISSING, CAT_MATH, SEV_WARNING, "EV could not be computed"),
}

_PASS_REGISTRY: dict[str, CodeInfo] = {
    PASS_STRUCTURAL_VALID:  CodeInfo(PASS_STRUCTURAL_VALID, CAT_STRUCTURAL, SEV_INFO, "Structural checks passed"),
    PASS_QUOTES_CLEAN:      CodeInfo(PASS_QUOTES_CLEAN, CAT_QUOTE, SEV_INFO, "Quotes valid on all legs"),
    PASS_LIQUIDITY_PRESENT: CodeInfo(PASS_LIQUIDITY_PRESENT, CAT_LIQUIDITY, SEV_INFO, "Liquidity data present"),
    PASS_MATH_CONSISTENT:   CodeInfo(PASS_MATH_CONSISTENT, CAT_MATH, SEV_INFO, "Math verification passed"),
    PASS_ALL_PHASES:        CodeInfo(PASS_ALL_PHASES, CAT_STRUCTURAL, SEV_INFO, "All phases passed"),
}


# =====================================================================
#  Public helpers
# =====================================================================

def is_valid_reject_code(code: str) -> bool:
    """True if code is a registered reject reason."""
    return code in _REJECT_REGISTRY


def is_valid_warn_code(code: str) -> bool:
    """True if code is a registered warning code."""
    return code in _WARN_REGISTRY


def is_valid_pass_code(code: str) -> bool:
    """True if code is a registered pass reason."""
    return code in _PASS_REGISTRY


def is_valid_code(code: str) -> bool:
    """True if code is registered in any registry."""
    return code in _REJECT_REGISTRY or code in _WARN_REGISTRY or code in _PASS_REGISTRY


def get_code_info(code: str) -> CodeInfo | None:
    """Look up metadata for any registered code."""
    return (
        _REJECT_REGISTRY.get(code)
        or _WARN_REGISTRY.get(code)
        or _PASS_REGISTRY.get(code)
    )


def get_category(code: str) -> str | None:
    """Return the category for a registered code, or None."""
    info = get_code_info(code)
    return info.category if info else None


def get_severity(code: str) -> str | None:
    """Return the default severity for a registered code, or None."""
    info = get_code_info(code)
    return info.severity if info else None


def get_label(code: str) -> str:
    """Return the human label for a code, or the code itself."""
    info = get_code_info(code)
    return info.label if info else code


def all_reject_codes() -> frozenset[str]:
    """All registered reject codes."""
    return frozenset(_REJECT_REGISTRY)


def all_warn_codes() -> frozenset[str]:
    """All registered warning codes."""
    return frozenset(_WARN_REGISTRY)


def all_pass_codes() -> frozenset[str]:
    """All registered pass codes."""
    return frozenset(_PASS_REGISTRY)


# =====================================================================
#  Canonical taxonomy mapping
# =====================================================================

# Maps V2 internal codes → canonical taxonomy codes from
# docs/standards/rejection-taxonomy.md
_V2_TO_CANONICAL: dict[str, str] = {
    # Structural → threshold category in taxonomy
    REJECT_INVALID_WIDTH:       "invalid_width",
    REJECT_NON_POSITIVE_CREDIT: "non_positive_credit",
    REJECT_IMPOSSIBLE_PRICING:  "credit_ge_width",
    # Quote → data_quality category in taxonomy
    REJECT_MISSING_QUOTE:       "missing_quote",
    REJECT_INVERTED_QUOTE:      "inverted_market",
    REJECT_ZERO_MID:            "zero_mid",
    # Liquidity → data_quality category in taxonomy
    REJECT_MISSING_OI:          "missing_open_interest",
    REJECT_MISSING_VOLUME:      "missing_volume",
}

_CANONICAL_TO_V2: dict[str, str] = {v: k for k, v in _V2_TO_CANONICAL.items()}


def to_canonical(v2_code: str) -> str | None:
    """Map a V2 reject code to its canonical taxonomy equivalent.

    Returns None if no canonical mapping exists (e.g. V2-only codes
    like ``v2_malformed_legs`` or math mismatch codes).
    """
    return _V2_TO_CANONICAL.get(v2_code)


def from_canonical(canonical_code: str) -> str | None:
    """Map a canonical taxonomy code to its V2 equivalent."""
    return _CANONICAL_TO_V2.get(canonical_code)
