"""V2 validation framework — contracts and data shapes.

All types for the structural and math validation pipeline live here.
No business logic — pure data definitions.

Contracts
---------
V2ValidationResult      Rich validation check outcome.
V2ToleranceSpec         Tolerance bounds for one numeric metric.
V2ValidationSummary     Aggregated results from a validation run.

Status vocabulary
-────────────────
PASS     Check succeeded.
WARN     Check borderline (within warning tolerance but not ideal).
FAIL     Check failed — candidate should be rejected.
SKIPPED  Check could not run (missing input data).

Severity vocabulary
───────────────────
INFO     Informational — never causes rejection.
WARNING  May indicate a problem — does not cause rejection alone.
ERROR    Hard failure — causes rejection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.scanner_v2.contracts import V2CheckResult

# ── Status constants ────────────────────────────────────────────────

STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_SKIPPED = "skipped"

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"


# ── V2ValidationResult ─────────────────────────────────────────────

@dataclass(slots=True)
class V2ValidationResult:
    """Rich validation check outcome.

    Extends the information available beyond ``V2CheckResult`` to
    support expected/actual comparison, tolerance tracking, severity
    classification, and structured fail codes.

    Parameters
    ----------
    check_key
        Machine-readable check identifier (e.g. ``"width_positive"``).
    status
        One of ``"pass"`` / ``"warn"`` / ``"fail"`` / ``"skipped"``.
    severity
        One of ``"info"`` / ``"warning"`` / ``"error"``.
    message
        Human-readable explanation.
    expected
        Expected value (for comparison checks).
    actual
        Actual value found.
    delta
        Numeric difference between expected and actual.
    fail_code
        V2 taxonomy reject code (e.g. ``"v2_invalid_width"``).
        Only set when ``status == "fail"``.
    warn_code
        Warning code (e.g. ``"v2_warn_width_mismatch"``).
        Only set when ``status == "warn"``.
    metadata
        Additional context for debugging / downstream.
    """

    check_key: str
    status: str = STATUS_PASS          # pass | warn | fail | skipped
    severity: str = SEVERITY_INFO      # info | warning | error
    message: str = ""
    expected: Any = None
    actual: Any = None
    delta: float | None = None
    fail_code: str = ""
    warn_code: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """True if status is pass or skipped (non-failure)."""
        return self.status in (STATUS_PASS, STATUS_SKIPPED)

    @property
    def is_failure(self) -> bool:
        return self.status == STATUS_FAIL

    @property
    def is_warning(self) -> bool:
        return self.status == STATUS_WARN

    def to_check_result(self) -> V2CheckResult:
        """Convert to V2CheckResult for V2Diagnostics storage.

        Maps the rich result into the simpler (name, passed, detail)
        format used by V2Diagnostics.structural_checks, etc.
        """
        detail = self.message
        if self.expected is not None and self.actual is not None:
            detail = f"{self.message} (expected={self.expected}, actual={self.actual}"
            if self.delta is not None:
                detail += f", delta={self.delta}"
            detail += ")"
        return V2CheckResult(
            name=self.check_key,
            passed=self.passed,
            detail=detail,
        )

    @staticmethod
    def make_pass(
        check_key: str, message: str = "", **kwargs: Any,
    ) -> V2ValidationResult:
        """Convenience: create a passing result."""
        return V2ValidationResult(
            check_key=check_key,
            status=STATUS_PASS,
            severity=SEVERITY_INFO,
            message=message,
            **kwargs,
        )

    @staticmethod
    def make_fail(
        check_key: str,
        fail_code: str,
        message: str = "",
        **kwargs: Any,
    ) -> V2ValidationResult:
        """Convenience: create a failing result."""
        return V2ValidationResult(
            check_key=check_key,
            status=STATUS_FAIL,
            severity=SEVERITY_ERROR,
            message=message,
            fail_code=fail_code,
            **kwargs,
        )

    @staticmethod
    def make_warn(
        check_key: str,
        warn_code: str,
        message: str = "",
        **kwargs: Any,
    ) -> V2ValidationResult:
        """Convenience: create a warning result."""
        return V2ValidationResult(
            check_key=check_key,
            status=STATUS_WARN,
            severity=SEVERITY_WARNING,
            message=message,
            warn_code=warn_code,
            **kwargs,
        )

    @staticmethod
    def make_skipped(
        check_key: str, message: str = "",
    ) -> V2ValidationResult:
        """Convenience: create a skipped result."""
        return V2ValidationResult(
            check_key=check_key,
            status=STATUS_SKIPPED,
            severity=SEVERITY_INFO,
            message=message,
        )


# ── V2ToleranceSpec ─────────────────────────────────────────────────

@dataclass(slots=True)
class V2ToleranceSpec:
    """Tolerance bounds for one numeric metric.

    Classification logic
    --------------------
    1. Compute ``delta = |expected - actual|``.
    2. If ``delta <= abs_pass`` → PASS.
    3. If ``delta <= abs_warn`` → WARN.
    4. If ``rel_warn`` is set and ``delta / |expected| <= rel_warn`` → WARN.
    5. Otherwise → FAIL.

    Parameters
    ----------
    abs_pass
        Maximum absolute delta for a clean pass.
    abs_warn
        Maximum absolute delta before failure.
        Values between ``abs_pass`` and ``abs_warn`` produce a warning.
    rel_warn
        Optional relative tolerance (as fraction of expected).
        If set, provides an alternative warn threshold for large values.
    """

    abs_pass: float = 0.01
    abs_warn: float = 0.05
    rel_warn: float | None = None

    def classify(
        self,
        expected: float | None,
        actual: float | None,
    ) -> tuple[str, float | None]:
        """Classify a comparison as pass/warn/fail.

        Returns ``(status, delta)`` where status is one of
        STATUS_PASS, STATUS_WARN, STATUS_FAIL, STATUS_SKIPPED.
        """
        if expected is None or actual is None:
            return STATUS_SKIPPED, None

        delta = abs(expected - actual)

        if delta <= self.abs_pass:
            return STATUS_PASS, delta

        if delta <= self.abs_warn:
            return STATUS_WARN, delta

        # Relative tolerance check
        if self.rel_warn is not None and expected != 0:
            if delta / abs(expected) <= self.rel_warn:
                return STATUS_WARN, delta

        return STATUS_FAIL, delta


# ── V2ValidationSummary ────────────────────────────────────────────

@dataclass(slots=True)
class V2ValidationSummary:
    """Aggregated results from a validation run.

    Collects all individual V2ValidationResult objects and provides
    quick access to pass/warn/fail counts and reject codes.
    """

    results: list[V2ValidationResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        """True if no result is a failure."""
        return not any(r.is_failure for r in self.results)

    @property
    def has_warnings(self) -> bool:
        return any(r.is_warning for r in self.results)

    @property
    def has_failures(self) -> bool:
        return any(r.is_failure for r in self.results)

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.results if r.status == STATUS_PASS)

    @property
    def warn_count(self) -> int:
        return sum(1 for r in self.results if r.status == STATUS_WARN)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if r.status == STATUS_FAIL)

    @property
    def skip_count(self) -> int:
        return sum(1 for r in self.results if r.status == STATUS_SKIPPED)

    @property
    def fail_codes(self) -> list[str]:
        """All unique fail_code values from failed results."""
        return list(dict.fromkeys(
            r.fail_code for r in self.results if r.fail_code
        ))

    @property
    def warn_codes(self) -> list[str]:
        """All unique warn_code values from warned results."""
        return list(dict.fromkeys(
            r.warn_code for r in self.results if r.warn_code
        ))

    def to_check_results(self) -> list[V2CheckResult]:
        """Convert all results to V2CheckResult for diagnostics storage."""
        return [r.to_check_result() for r in self.results]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain dict."""
        from dataclasses import asdict
        return asdict(self)
