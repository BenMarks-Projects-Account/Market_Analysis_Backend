"""V2 Validation Framework — public API surface.

Modules
-------
contracts     V2ValidationResult, V2ToleranceSpec, V2ValidationSummary, status constants.
tolerances    Centralized tolerance policy and family overrides.
structural    Composable structural validation checks.
math_checks   Recomputed-math verification checks.

Quick start
-----------
::
    from app.services.scanner_v2.validation import (
        run_shared_structural_checks,
        run_math_verification,
        V2ValidationResult,
        V2ValidationSummary,
    )

    struct = run_shared_structural_checks(candidate, expected_leg_count=2)
    math   = run_math_verification(candidate, family_key="vertical_spreads")
"""

from app.services.scanner_v2.validation.contracts import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
    STATUS_WARN,
    V2ToleranceSpec,
    V2ValidationResult,
    V2ValidationSummary,
)
from app.services.scanner_v2.validation.math_checks import run_math_verification
from app.services.scanner_v2.validation.structural import (
    run_butterfly_structural_checks,
    run_calendar_structural_checks,
    run_iron_condor_structural_checks,
    run_shared_structural_checks,
    run_vertical_structural_checks,
)
from app.services.scanner_v2.validation.tolerances import (
    DEFAULT_TOLERANCES,
    get_tolerance,
    get_tolerances,
)

__all__ = [
    # Contracts
    "V2ValidationResult",
    "V2ValidationSummary",
    "V2ToleranceSpec",
    # Status / severity constants
    "STATUS_PASS",
    "STATUS_WARN",
    "STATUS_FAIL",
    "STATUS_SKIPPED",
    "SEVERITY_INFO",
    "SEVERITY_WARNING",
    "SEVERITY_ERROR",
    # Structural runners
    "run_shared_structural_checks",
    "run_vertical_structural_checks",
    "run_iron_condor_structural_checks",
    "run_butterfly_structural_checks",
    "run_calendar_structural_checks",
    # Math verification
    "run_math_verification",
    # Tolerances
    "DEFAULT_TOLERANCES",
    "get_tolerances",
    "get_tolerance",
]
