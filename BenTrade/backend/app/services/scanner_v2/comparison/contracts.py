"""Comparison harness — data contracts.

All data shapes for the comparison harness live here.  No business logic.

Contracts
---------
ComparisonSnapshot      Input fixture for side-by-side testing.
MetricDelta             Numeric difference between legacy and V2 for a metric.
DiagnosticsDiff         Structural diff of diagnostics between legacy and V2.
CandidateMatch          One candidate compared across both systems.
ComparisonReport        Full report from a family comparison run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


COMPARISON_CONTRACT_VERSION: str = "1.0.0"
"""Bump when the comparison report shape changes."""


# ── Snapshot: frozen scanner input ──────────────────────────────────

@dataclass(slots=True)
class ComparisonSnapshot:
    """Frozen market-data input that both legacy and V2 see identically.

    Fields
    ------
    snapshot_id     Unique label (``"spy_put_credit_2026-03-20_balanced"``).
    symbol          Underlying ticker (``"SPY"``).
    underlying_price  Spot price at snapshot time.
    chain           Tradier-shaped option chain dict.
    expirations     Available expiration dates (ISO strings).
    captured_at     ISO 8601 timestamp of when the snapshot was captured.
    description     Human-readable note about what the snapshot represents.
    tags            Categorical labels (e.g. ``["golden", "wide_spread"]``).
    metadata        Extra context (DTE range, scenario type, etc.).
    """

    snapshot_id: str
    symbol: str
    underlying_price: float
    chain: dict[str, Any]
    expirations: list[str] = field(default_factory=list)
    captured_at: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ComparisonSnapshot:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Metric delta ────────────────────────────────────────────────────

@dataclass(slots=True)
class MetricDelta:
    """Numeric difference for a single metric between legacy and V2.

    Uses ``None`` for values that are unavailable on one side.
    """

    metric: str                       # e.g. "net_credit", "pop", "max_loss"
    legacy_value: float | None = None
    v2_value: float | None = None
    abs_diff: float | None = None     # abs(v2 - legacy) if both present
    pct_diff: float | None = None     # abs_diff / abs(legacy) if legacy != 0

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


# ── Diagnostics diff ────────────────────────────────────────────────

@dataclass(slots=True)
class DiagnosticsDiff:
    """Structural comparison of diagnostics between legacy and V2."""

    legacy_rejection_codes: list[str] = field(default_factory=list)
    v2_rejection_codes: list[str] = field(default_factory=list)
    legacy_only_rejections: list[str] = field(default_factory=list)
    v2_only_rejections: list[str] = field(default_factory=list)
    shared_rejections: list[str] = field(default_factory=list)

    legacy_passed: bool = True
    v2_passed: bool = True

    # V2-specific diagnostics richness
    v2_structural_checks: int = 0
    v2_quote_checks: int = 0
    v2_liquidity_checks: int = 0
    v2_math_checks: int = 0
    v2_warnings: list[str] = field(default_factory=list)
    v2_pass_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


# ── Candidate match ─────────────────────────────────────────────────

@dataclass(slots=True)
class CandidateMatch:
    """Single candidate compared across legacy and V2.

    Match status
    ------------
    ``match_type`` indicates where the candidate appeared:
    - ``"matched"`` — found in both legacy and V2.
    - ``"legacy_only"`` — found only in legacy output.
    - ``"v2_only"`` — found only in V2 output.

    The ``comparison_key`` is the structural identity used for matching
    (see ``equivalence.build_comparison_key``).
    """

    comparison_key: str               # structural identity
    match_type: str                   # "matched" | "legacy_only" | "v2_only"

    # Raw candidate data (dicts for portability)
    legacy_candidate: dict[str, Any] | None = None
    v2_candidate: dict[str, Any] | None = None

    # Diffs (only for matched candidates)
    metric_deltas: list[MetricDelta] = field(default_factory=list)
    diagnostics_diff: DiagnosticsDiff | None = None

    # Trust signals
    v2_structurally_improved: bool | None = None
    """True if V2 caught structural issues legacy missed."""

    v2_math_recomputed: bool | None = None
    """True if V2 recomputed math from raw quotes rather than trusting upstream."""

    v2_diagnostics_richer: bool | None = None
    """True if V2 provides more diagnostic detail than legacy."""

    notes: list[str] = field(default_factory=list)
    """Human-readable observations about this candidate comparison."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "comparison_key": self.comparison_key,
            "match_type": self.match_type,
            "legacy_candidate": self.legacy_candidate,
            "v2_candidate": self.v2_candidate,
            "metric_deltas": [m.to_dict() for m in self.metric_deltas],
            "diagnostics_diff": self.diagnostics_diff.to_dict()
                if self.diagnostics_diff else None,
            "v2_structurally_improved": self.v2_structurally_improved,
            "v2_math_recomputed": self.v2_math_recomputed,
            "v2_diagnostics_richer": self.v2_diagnostics_richer,
            "notes": self.notes,
        }
        return d


# ── Comparison report ───────────────────────────────────────────────

@dataclass(slots=True)
class ComparisonReport:
    """Full report from a legacy-vs-V2 side-by-side comparison.

    This is the primary output of ``compare_scanner_family()``.
    Every field is inspectable for debugging and can be serialized
    for artifact storage.
    """

    # ── Identity ────────────────────────────────────────────────
    comparison_id: str = ""
    """Unique comparison run ID."""

    comparison_version: str = COMPARISON_CONTRACT_VERSION
    scanner_family: str = ""
    """Family being compared (e.g. ``"vertical_spreads"``)."""

    scanner_key: str = ""
    """Specific scanner key (e.g. ``"put_credit_spread"``)."""

    # ── Snapshot metadata ───────────────────────────────────────
    snapshot_id: str = ""
    symbol: str = ""
    underlying_price: float | None = None
    snapshot_metadata: dict[str, Any] = field(default_factory=dict)

    # ── Counts ──────────────────────────────────────────────────
    legacy_total_constructed: int = 0
    legacy_total_passed: int = 0
    legacy_total_rejected: int = 0

    v2_total_constructed: int = 0
    v2_total_passed: int = 0
    v2_total_rejected: int = 0

    overlap_count: int = 0
    """Candidates found in both systems (structurally equivalent)."""

    legacy_only_count: int = 0
    """Candidates only in legacy output."""

    v2_only_count: int = 0
    """Candidates only in V2 output."""

    # ── Matched candidates ──────────────────────────────────────
    matches: list[CandidateMatch] = field(default_factory=list)
    """All candidate comparisons (matched, legacy_only, v2_only)."""

    # ── Rejection analysis ──────────────────────────────────────
    legacy_rejection_counts: dict[str, int] = field(default_factory=dict)
    v2_rejection_counts: dict[str, int] = field(default_factory=dict)

    # ── Phase trace diff ────────────────────────────────────────
    legacy_stage_counts: list[dict[str, Any]] = field(default_factory=list)
    v2_phase_counts: list[dict[str, Any]] = field(default_factory=list)

    # ── Trust summary ───────────────────────────────────────────
    v2_caught_broken: int = 0
    """Count of candidates V2 rejected that legacy accepted
    due to structural or data-quality issues."""

    v2_new_valid: int = 0
    """Count of candidates V2 accepted that legacy rejected
    (scanner-time over-filtering removed)."""

    v2_diagnostics_richer_count: int = 0
    """Count of matched candidates where V2 diagnostics are richer."""

    # ── Metric summary ──────────────────────────────────────────
    metric_summary: dict[str, dict[str, float | None]] = field(
        default_factory=dict,
    )
    """Per-metric aggregated deltas across matched candidates.

    Shape: ``{metric_name: {"mean_abs_diff": ..., "max_abs_diff": ...,
    "mean_pct_diff": ...}}``.
    """

    # ── Anomalies / conclusions ─────────────────────────────────
    anomalies: list[str] = field(default_factory=list)
    """Detected anomalies or unexpected situations."""

    conclusions: list[str] = field(default_factory=list)
    """Human-readable summary conclusions."""

    # ── Timing ──────────────────────────────────────────────────
    legacy_elapsed_ms: float = 0.0
    v2_elapsed_ms: float = 0.0
    comparison_elapsed_ms: float = 0.0
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)

    @property
    def total_compared(self) -> int:
        return self.overlap_count + self.legacy_only_count + self.v2_only_count
