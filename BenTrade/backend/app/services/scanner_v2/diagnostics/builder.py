"""V2 Diagnostics — builder for accumulating diagnostic items.

``DiagnosticsBuilder`` replaces the manual ``if code not in
cand.diagnostics.reject_reasons: append(code)`` pattern used in
phases.py.  It provides:

- Automatic deduplication of reason codes.
- Phase-aware source tracking.
- Conversion from V2ValidationSummary → diagnostic items.
- Generation of semantic pass reasons for passing candidates.
- Single ``build()`` call that populates both the legacy flat lists
  AND the new structured ``items`` list on ``V2Diagnostics``.

Usage in a phase function::

    builder = DiagnosticsBuilder(source_phase="D")
    builder.add_reject("v2_missing_quote", source_check="quote_present",
                       message="leg[0]: bid=None ask=None")
    builder.set_check_results("quote", q_checks)
    builder.set_check_results("liquidity", l_checks)
    builder.apply(cand.diagnostics)
"""

from __future__ import annotations

from typing import Any

from app.services.scanner_v2.diagnostics.diagnostic_item import (
    V2DiagnosticItem,
)
from app.services.scanner_v2.diagnostics.reason_codes import (
    KIND_PASS,
    KIND_REJECT,
    KIND_WARNING,
    PASS_ALL_PHASES,
    PASS_LIQUIDITY_PRESENT,
    PASS_MATH_CONSISTENT,
    PASS_QUOTES_CLEAN,
    PASS_STRUCTURAL_VALID,
    get_code_info,
    get_label,
)

# Import V2CheckResult for type hints only — avoid circular imports
# by using string annotations where needed.
from app.services.scanner_v2.contracts import V2CheckResult, V2Diagnostics


class DiagnosticsBuilder:
    """Accumulator for diagnostic items within a single phase.

    Parameters
    ----------
    source_phase
        Phase identifier (``"C"`` / ``"D"`` / ``"E"`` / ``"F"``).
    """

    __slots__ = (
        "_source_phase",
        "_items",
        "_reject_codes",
        "_warn_codes",
        "_check_results",
    )

    def __init__(self, source_phase: str = "") -> None:
        self._source_phase = source_phase
        self._items: list[V2DiagnosticItem] = []
        self._reject_codes: set[str] = set()
        self._warn_codes: set[str] = set()
        # phase_section → list[V2CheckResult]
        self._check_results: dict[str, list[V2CheckResult]] = {}

    # ── Add individual items ────────────────────────────────────

    def add_reject(
        self,
        code: str,
        *,
        message: str = "",
        source_check: str = "",
        **metadata: Any,
    ) -> None:
        """Record a reject reason (deduplicated by code)."""
        if code in self._reject_codes:
            return
        self._reject_codes.add(code)
        self._items.append(V2DiagnosticItem.reject(
            code,
            message=message,
            source_phase=self._source_phase,
            source_check=source_check,
            **metadata,
        ))

    def add_warning(
        self,
        code: str,
        *,
        message: str = "",
        source_check: str = "",
        **metadata: Any,
    ) -> None:
        """Record a warning (deduplicated by code)."""
        if code in self._warn_codes:
            return
        self._warn_codes.add(code)
        self._items.append(V2DiagnosticItem.warning(
            code,
            message=message,
            source_phase=self._source_phase,
            source_check=source_check,
            **metadata,
        ))

    def add_pass(
        self,
        code: str,
        *,
        message: str = "",
        source_check: str = "",
        **metadata: Any,
    ) -> None:
        """Record a pass reason."""
        self._items.append(V2DiagnosticItem.pass_item(
            code,
            message=message,
            source_phase=self._source_phase,
            source_check=source_check,
            **metadata,
        ))

    # ── Check result storage ────────────────────────────────────

    def set_check_results(
        self,
        section: str,
        checks: list[V2CheckResult],
    ) -> None:
        """Store V2CheckResult list for a diagnostics section.

        ``section`` is one of ``"structural"`` / ``"quote"`` /
        ``"liquidity"`` / ``"math"``.
        """
        self._check_results[section] = checks

    # ── Bulk import from V2ValidationSummary ────────────────────

    def merge_validation_summary(
        self,
        summary: Any,  # V2ValidationSummary — Any to avoid circular import
        *,
        check_section: str = "",
    ) -> None:
        """Import results from a V2ValidationSummary.

        Converts fail codes → reject items, warn codes → warning items.
        Stores the ``to_check_results()`` output under ``check_section``
        if provided.
        """
        for code in summary.fail_codes:
            self.add_reject(code, source_check=code)

        for code in summary.warn_codes:
            # Find the message from the original result
            msg = next(
                (r.message for r in summary.results if r.warn_code == code),
                "",
            )
            self.add_warning(code, message=msg, source_check=code)

        if check_section:
            self.set_check_results(check_section, summary.to_check_results())

    # ── Apply to V2Diagnostics ──────────────────────────────────

    def apply(self, diag: V2Diagnostics) -> None:
        """Write accumulated results into a V2Diagnostics instance.

        Populates both the legacy flat lists AND the new ``items``
        list.  Merges with any items already present from prior phases.
        """
        # Legacy flat lists — append new codes
        for code in self._reject_codes:
            if code not in diag.reject_reasons:
                diag.reject_reasons.append(code)

        for item in self._items:
            if item.kind == KIND_WARNING:
                # Legacy: warnings stored as message strings
                if item.message and item.message not in diag.warnings:
                    diag.warnings.append(item.message)

        # Check results → legacy sections
        if "structural" in self._check_results:
            diag.structural_checks = self._check_results["structural"]
        if "quote" in self._check_results:
            diag.quote_checks = self._check_results["quote"]
        if "liquidity" in self._check_results:
            diag.liquidity_checks = self._check_results["liquidity"]
        if "math" in self._check_results:
            diag.math_checks = self._check_results["math"]

        # New structured items — merge into diag.items
        diag.items.extend(self._items)

    # ── Introspection ───────────────────────────────────────────

    @property
    def reject_codes(self) -> frozenset[str]:
        return frozenset(self._reject_codes)

    @property
    def warn_codes(self) -> frozenset[str]:
        return frozenset(self._warn_codes)

    @property
    def items(self) -> list[V2DiagnosticItem]:
        return list(self._items)

    @property
    def has_rejects(self) -> bool:
        return bool(self._reject_codes)


# =====================================================================
#  Pass-reason generation (called from Phase F)
# =====================================================================

def collect_pass_reasons(diag: V2Diagnostics) -> list[str]:
    """Generate semantic pass reasons for a passing candidate.

    Called in Phase F for candidates with no reject_reasons.
    Returns code strings AND populates ``diag.items`` with
    structured pass items.
    """
    reasons: list[str] = []

    s_pass = sum(1 for c in diag.structural_checks if c.passed)
    s_total = len(diag.structural_checks)
    if s_total > 0 and s_pass == s_total:
        reasons.append(PASS_STRUCTURAL_VALID)
        diag.items.append(V2DiagnosticItem.pass_item(
            PASS_STRUCTURAL_VALID,
            message=f"All {s_total} structural checks passed",
            source_phase="F",
            checks_passed=s_pass,
            checks_total=s_total,
        ))

    q_pass = sum(1 for c in diag.quote_checks if c.passed)
    q_total = len(diag.quote_checks)
    if q_total > 0 and q_pass == q_total:
        reasons.append(PASS_QUOTES_CLEAN)
        diag.items.append(V2DiagnosticItem.pass_item(
            PASS_QUOTES_CLEAN,
            message=f"All {q_total} quote checks passed",
            source_phase="F",
            checks_passed=q_pass,
            checks_total=q_total,
        ))

    l_pass = sum(1 for c in diag.liquidity_checks if c.passed)
    l_total = len(diag.liquidity_checks)
    if l_total > 0 and l_pass == l_total:
        reasons.append(PASS_LIQUIDITY_PRESENT)
        diag.items.append(V2DiagnosticItem.pass_item(
            PASS_LIQUIDITY_PRESENT,
            message=f"All {l_total} liquidity checks passed",
            source_phase="F",
            checks_passed=l_pass,
            checks_total=l_total,
        ))

    m_pass = sum(1 for c in diag.math_checks if c.passed)
    m_total = len(diag.math_checks)
    if m_total > 0 and m_pass == m_total:
        reasons.append(PASS_MATH_CONSISTENT)
        diag.items.append(V2DiagnosticItem.pass_item(
            PASS_MATH_CONSISTENT,
            message=f"All {m_total} math checks passed",
            source_phase="F",
            checks_passed=m_pass,
            checks_total=m_total,
        ))

    if reasons:
        reasons.append(PASS_ALL_PHASES)
        diag.items.append(V2DiagnosticItem.pass_item(
            PASS_ALL_PHASES,
            message="Candidate passed all scanner-time phases",
            source_phase="F",
        ))

    return reasons
