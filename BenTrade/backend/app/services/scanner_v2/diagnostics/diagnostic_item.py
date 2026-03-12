"""V2 Diagnostics — structured diagnostic item.

``V2DiagnosticItem`` is the rich, structured replacement for the flat
string entries in ``reject_reasons`` / ``warnings`` / ``pass_reasons``.

Every diagnostic event (reject, pass, warning) is captured as one of
these items, making the scanner fully inspectable at both machine and
human levels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.scanner_v2.diagnostics.reason_codes import (
    KIND_PASS,
    KIND_REJECT,
    KIND_WARNING,
    SEV_ERROR,
    SEV_INFO,
    SEV_WARNING,
    get_code_info,
)


@dataclass(slots=True)
class V2DiagnosticItem:
    """Structured diagnostic event for a V2 candidate.

    Parameters
    ----------
    code
        Machine-readable reason code from the V2 vocabulary
        (e.g. ``"v2_missing_quote"``, ``"v2_pass_quotes_clean"``).
    kind
        ``"reject"`` | ``"pass"`` | ``"warning"``.
    category
        ``"structural"`` | ``"quote"`` | ``"liquidity"`` | ``"math"``
        | ``"threshold"``.
    severity
        ``"error"`` | ``"warning"`` | ``"info"``.
    message
        Human-readable explanation.
    source_phase
        Which pipeline phase produced this item (``"C"`` / ``"D"`` /
        ``"E"`` / ``"F"``).
    source_check
        The specific check key that produced this item
        (e.g. ``"valid_leg_count"``, ``"quote_present"``).
    metadata
        Additional context: actual values, thresholds, leg indices, etc.
    """

    code: str
    kind: str                            # "reject" | "pass" | "warning"
    category: str                        # "structural" | "quote" | ...
    severity: str                        # "error" | "warning" | "info"
    message: str = ""
    source_phase: str = ""               # "C" | "D" | "E" | "F"
    source_check: str = ""               # check key that triggered this
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Convenience constructors ────────────────────────────────

    @staticmethod
    def reject(
        code: str,
        *,
        message: str = "",
        source_phase: str = "",
        source_check: str = "",
        category: str | None = None,
        **metadata: Any,
    ) -> V2DiagnosticItem:
        """Create a reject diagnostic item.

        Auto-fills category from the reason code registry if not
        explicitly provided.
        """
        info = get_code_info(code)
        return V2DiagnosticItem(
            code=code,
            kind=KIND_REJECT,
            category=category or (info.category if info else ""),
            severity=SEV_ERROR,
            message=message or (info.label if info else code),
            source_phase=source_phase,
            source_check=source_check,
            metadata=metadata if metadata else {},
        )

    @staticmethod
    def warning(
        code: str,
        *,
        message: str = "",
        source_phase: str = "",
        source_check: str = "",
        category: str | None = None,
        **metadata: Any,
    ) -> V2DiagnosticItem:
        """Create a warning diagnostic item."""
        info = get_code_info(code)
        return V2DiagnosticItem(
            code=code,
            kind=KIND_WARNING,
            category=category or (info.category if info else ""),
            severity=SEV_WARNING,
            message=message or (info.label if info else code),
            source_phase=source_phase,
            source_check=source_check,
            metadata=metadata if metadata else {},
        )

    @staticmethod
    def pass_item(
        code: str,
        *,
        message: str = "",
        source_phase: str = "",
        source_check: str = "",
        category: str | None = None,
        **metadata: Any,
    ) -> V2DiagnosticItem:
        """Create a pass diagnostic item."""
        info = get_code_info(code)
        return V2DiagnosticItem(
            code=code,
            kind=KIND_PASS,
            category=category or (info.category if info else ""),
            severity=SEV_INFO,
            message=message or (info.label if info else code),
            source_phase=source_phase,
            source_check=source_check,
            metadata=metadata if metadata else {},
        )

    # ── Predicates ──────────────────────────────────────────────

    @property
    def is_reject(self) -> bool:
        return self.kind == KIND_REJECT

    @property
    def is_pass(self) -> bool:
        return self.kind == KIND_PASS

    @property
    def is_warning(self) -> bool:
        return self.kind == KIND_WARNING

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict."""
        return {
            "code": self.code,
            "kind": self.kind,
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "source_phase": self.source_phase,
            "source_check": self.source_check,
            "metadata": self.metadata,
        }
