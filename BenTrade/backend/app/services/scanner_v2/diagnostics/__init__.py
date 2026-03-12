"""V2 Diagnostics Framework — public API surface.

Modules
-------
reason_codes      Central registry of all V2 reason codes (reject, pass, warning).
diagnostic_item   V2DiagnosticItem structured type.
builder           DiagnosticsBuilder accumulator for phases.

Quick start
-----------
::
    from app.services.scanner_v2.diagnostics import (
        DiagnosticsBuilder,
        V2DiagnosticItem,
        collect_pass_reasons,
        # Reason codes
        REJECT_MISSING_QUOTE,
        PASS_QUOTES_CLEAN,
        WARN_POP_MISSING,
        # Helpers
        is_valid_reject_code,
        get_code_info,
        to_canonical,
    )

    builder = DiagnosticsBuilder(source_phase="D")
    builder.add_reject(REJECT_MISSING_QUOTE, source_check="quote_present")
    builder.apply(candidate.diagnostics)
"""

from app.services.scanner_v2.diagnostics.builder import (  # noqa: F401
    DiagnosticsBuilder,
    collect_pass_reasons,
)
from app.services.scanner_v2.diagnostics.diagnostic_item import (  # noqa: F401
    V2DiagnosticItem,
)
from app.services.scanner_v2.diagnostics.reason_codes import (  # noqa: F401
    # Category constants
    ALL_CATEGORIES,
    CAT_LIQUIDITY,
    CAT_MATH,
    CAT_QUOTE,
    CAT_STRUCTURAL,
    CAT_THRESHOLD,
    # Kind constants
    KIND_PASS,
    KIND_REJECT,
    KIND_WARNING,
    # Severity constants
    SEV_ERROR,
    SEV_INFO,
    SEV_WARNING,
    # CodeInfo type
    CodeInfo,
    # Reject codes
    REJECT_BREAKEVEN_MISMATCH,
    REJECT_CREDIT_MISMATCH,
    REJECT_DEBIT_MISMATCH,
    REJECT_IMPOSSIBLE_MAX_LOSS,
    REJECT_IMPOSSIBLE_MAX_PROFIT,
    REJECT_IMPOSSIBLE_PRICING,
    REJECT_INVALID_WIDTH,
    REJECT_INVERTED_QUOTE,
    REJECT_MALFORMED_LEGS,
    REJECT_MAX_LOSS_MISMATCH,
    REJECT_MAX_PROFIT_MISMATCH,
    REJECT_MISSING_OI,
    REJECT_MISSING_QUOTE,
    REJECT_MISSING_VOLUME,
    REJECT_MISMATCHED_EXPIRY,
    REJECT_NON_FINITE_MATH,
    REJECT_NON_POSITIVE_CREDIT,
    REJECT_ROR_MISMATCH,
    REJECT_WIDTH_MISMATCH,
    REJECT_ZERO_MID,
    # Warning codes
    WARN_BREAKEVEN_MISMATCH,
    WARN_CREDIT_MISMATCH,
    WARN_DEBIT_MISMATCH,
    WARN_EV_MISSING,
    WARN_MAX_LOSS_MISMATCH,
    WARN_MAX_PROFIT_MISMATCH,
    WARN_POP_MISSING,
    WARN_ROR_MISMATCH,
    WARN_WIDTH_MISMATCH,
    # Pass codes
    PASS_ALL_PHASES,
    PASS_LIQUIDITY_PRESENT,
    PASS_MATH_CONSISTENT,
    PASS_QUOTES_CLEAN,
    PASS_STRUCTURAL_VALID,
    # Helpers
    all_pass_codes,
    all_reject_codes,
    all_warn_codes,
    from_canonical,
    get_category,
    get_code_info,
    get_label,
    get_severity,
    is_valid_code,
    is_valid_pass_code,
    is_valid_reject_code,
    is_valid_warn_code,
    to_canonical,
)
