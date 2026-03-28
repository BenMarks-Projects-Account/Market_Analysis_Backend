"""Options Scanner V2 — normalized contracts.

All V2 data shapes live here.  No business logic — pure data definitions.

Contracts
---------
V2Leg               Per-leg quote/greek data.
V2RecomputedMath    Derived pricing fields recomputed from V2Leg quotes.
V2CheckResult       Single pass/fail check with detail.
V2Diagnostics       Full diagnostics record for a candidate.
V2Candidate         The normalized candidate output.
V2ScanResult        Top-level result from a V2 scanner run.

Design rules
------------
1. All monetary values are **per-contract** (×100 of per-share).
   Exception: ``net_credit`` / ``net_debit`` stay per-share (option quote
   convention) and are clearly labeled.
2. ``None`` means "not available / could not compute".  Never use 0 as a
   sentinel for missing data.
3. Reason codes in ``reject_reasons`` come from the V2 taxonomy defined
   in ``docs/scanners/options/v2-architecture.md §2``.
4. ``contract_version`` is bumped whenever the shape changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.scanner_v2.diagnostics.diagnostic_item import (
        V2DiagnosticItem,
    )


# ── Version ─────────────────────────────────────────────────────────

SCANNER_V2_CONTRACT_VERSION: str = "2.0.0"
"""Bump when the candidate/diagnostics shape changes materially."""


# ── V2Leg ───────────────────────────────────────────────────────────

@dataclass(slots=True)
class V2Leg:
    """Per-leg structured data.

    Fields
    ------
    index       Positional index within the candidate (0-based).
    side        ``"long"`` or ``"short"``.
    strike      Strike price.
    option_type ``"put"`` or ``"call"``.
    expiration  ISO date string (``"2026-03-20"``).
    bid         Best bid (per-share).  None if missing.
    ask         Best ask (per-share).  None if missing.
    mid         (bid + ask) / 2.  Derived — None if either is missing.
    delta       Option delta.  None if unavailable.
    gamma       Option gamma.  None if unavailable.
    theta       Option theta.  None if unavailable.
    vega        Option vega.   None if unavailable.
    iv          Implied volatility (annualized decimal).  None if unavailable.
    open_interest  Open interest.  None if unavailable.
    volume      Day volume.  None if unavailable.
    """

    index: int
    side: str                        # "long" | "short"
    strike: float
    option_type: str                 # "put" | "call"
    expiration: str                  # ISO date

    # Quote data — None means missing (NOT zero)
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None

    # Greeks — all optional
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    iv: float | None = None

    # Liquidity — None means missing
    open_interest: int | None = None
    volume: int | None = None


# ── V2RecomputedMath ────────────────────────────────────────────────

@dataclass(slots=True)
class V2RecomputedMath:
    """Derived pricing fields recomputed from leg quotes.

    Every field is computed inside Phase E from the candidate's V2Leg
    values.  Nothing is copied from upstream data blobs.

    Monetary convention
    -------------------
    - ``net_credit`` / ``net_debit``: **per-share** (option quote units).
    - ``max_profit`` / ``max_loss``: **per-contract** (×100).
    - ``width``: **per-share** (strike distance).

    Computation notes
    -----------------
    ``notes`` is a dict of ``{field_name: str}`` explaining how each
    value was derived or why it is None.

    Formula reference (vertical spread)
    ------------------------------------------
    Credit spread:
      net_credit  = short_leg.bid − long_leg.ask
      max_profit  = net_credit × 100
      max_loss    = (width − net_credit) × 100
      pop         = 1 − abs(short_leg.delta)   [P(short expires OTM)]

    Debit spread:
      net_debit   = long_leg.ask − short_leg.bid
      max_profit  = (width − net_debit) × 100
      max_loss    = net_debit × 100
      pop         = abs(long_leg.delta)         [P(long finishes ITM)]

    Common:
      width       = abs(short_leg.strike − long_leg.strike)
      ev          = (pop × max_profit) − ((1 − pop) × max_loss)
      ror         = max_profit / max_loss       [if max_loss > 0]
    """

    net_credit: float | None = None      # per-share
    net_debit: float | None = None       # per-share
    max_profit: float | None = None      # per-contract
    max_loss: float | None = None        # per-contract
    width: float | None = None           # per-share (strike distance)

    pop: float | None = None             # probability of profit [0, 1]
    pop_source: str | None = None        # "delta_approx" | "normal_cdf" | "model" | None
    ev: float | None = None              # expected value per-contract
    ev_per_day: float | None = None      # EV / DTE
    ev_raw_binary: float | None = None   # pre-adjustment binary EV (butterflies)
    ev_adjustment: str | None = None     # adjustment label (e.g. "triangular_payoff_0.50")
    ev_caveat: str | None = None         # human-readable caveat about EV accuracy
    ev_accuracy: str | None = None       # "standard" | "adjusted"
    ror: float | None = None             # return on risk (max_profit / max_loss)
    expected_ror: float | None = None    # expected RoR (ev / |max_loss|)
    kelly: float | None = None           # Kelly fraction

    breakeven: list[float] = field(default_factory=list)
    """Breakeven price(s).  Some strategies have multiple."""

    # ── Managed EV (three-outcome model) ────────────────────────
    ev_managed: float | None = None          # managed EV per-contract ($)
    ev_managed_per_day: float | None = None  # ev_managed / DTE
    managed_profit_target: float | None = None  # $ amount of profit target
    managed_stop_loss: float | None = None   # $ amount of stop loss (positive = loss)
    p_profit_target: float | None = None     # probability of hitting profit target
    p_stop_loss: float | None = None         # probability of hitting stop loss
    p_expiration: float | None = None        # probability of reaching expiration
    management_policy_used: dict | None = None  # the policy that was applied
    ev_model: str | None = None              # "three_outcome_v1"
    managed_expected_ror: float | None = None  # ev_managed / max_loss

    notes: dict[str, str] = field(default_factory=dict)
    """Per-field computation notes / flags.

    Example: ``{"pop": "delta_approx from short leg delta=-0.30"}``
    """


# ── V2CheckResult ──────────────────────────────────────────────────

@dataclass(slots=True)
class V2CheckResult:
    """Single validation check outcome.

    Parameters
    ----------
    name    Machine-readable check name (e.g. ``"valid_leg_count"``).
    passed  True if the check passed.
    detail  Human-readable explanation or diagnostic value.
    """

    name: str
    passed: bool
    detail: str = ""


# ── V2Diagnostics ──────────────────────────────────────────────────

@dataclass(slots=True)
class V2Diagnostics:
    """Full diagnostics record for a V2 candidate.

    Every candidate (pass or reject) gets one of these.  It makes the
    scanner's reasoning fully transparent.

    Sections map to the V2 architecture phases:
    - ``structural_checks``  ← Phase C
    - ``quote_checks``       ← Phase D (quote sanity)
    - ``liquidity_checks``   ← Phase D (liquidity sanity)
    - ``math_checks``        ← Phase E
    """

    # Phase C — structural validation results
    structural_checks: list[V2CheckResult] = field(default_factory=list)

    # Phase D — quote & liquidity sanity results
    quote_checks: list[V2CheckResult] = field(default_factory=list)
    liquidity_checks: list[V2CheckResult] = field(default_factory=list)

    # Phase E — recomputed math validation results
    math_checks: list[V2CheckResult] = field(default_factory=list)

    # Outcome
    reject_reasons: list[str] = field(default_factory=list)
    """Reason codes from V2 taxonomy (e.g. ``"v2_inverted_quote"``).
    Empty list = candidate passed all checks."""

    warnings: list[str] = field(default_factory=list)
    """Non-fatal warnings (e.g. ``"POP could not be computed — missing delta"``).
    These do NOT cause rejection."""

    pass_reasons: list[str] = field(default_factory=list)
    """Why the candidate is valid (e.g. ``"all structural checks passed"``,
    ``"quotes valid on all legs"``).  Useful for downstream trust."""

    # ── Structured diagnostic items (Prompt 5) ──────────────────
    items: list[V2DiagnosticItem] = field(default_factory=list)
    """Rich structured diagnostic events.  Every reject, pass, and
    warning is captured here with code, category, severity, phase,
    and metadata.  The flat lists above remain for backward compat."""


# ── V2Candidate ─────────────────────────────────────────────────────

@dataclass(slots=True)
class V2Candidate:
    """Normalized output for every V2 options scanner candidate.

    One of these is produced for every candidate constructed in Phase B,
    regardless of whether the candidate passes or is rejected.  The
    ``diagnostics`` field explains the outcome.

    Identity fields
    ---------------
    ``candidate_id`` — unique across all candidates in one scan run.
        Format: ``"{symbol}|{strategy_id}|{expiration}|{strikes}|{seq}"``
    ``scanner_key``  — the scanner_key as registered in the pipeline
        (e.g. ``"put_credit_spread"``).
    ``strategy_id``  — canonical strategy ID from canonical-contract.md
        (e.g. ``"put_credit_spread"``).
    ``family_key``   — family grouping key (e.g. ``"vertical_spreads"``).
    """

    # ── Identity ────────────────────────────────────────────────
    candidate_id: str
    scanner_key: str
    strategy_id: str
    family_key: str

    # ── Symbol / underlying ─────────────────────────────────────
    symbol: str
    underlying_price: float | None = None

    # ── Expiry ──────────────────────────────────────────────────
    expiration: str = ""                 # ISO date (primary / front leg)
    expiration_back: str | None = None   # back leg for calendars/diagonals
    dte: int | None = None               # days to expiration (primary)
    dte_back: int | None = None          # DTE for back leg

    # ── Legs ────────────────────────────────────────────────────
    legs: list[V2Leg] = field(default_factory=list)

    # ── Recomputed math ─────────────────────────────────────────
    math: V2RecomputedMath = field(default_factory=V2RecomputedMath)

    # ── Diagnostics ─────────────────────────────────────────────
    diagnostics: V2Diagnostics = field(default_factory=V2Diagnostics)

    # ── Status ──────────────────────────────────────────────────
    passed: bool = False
    """True if the candidate passed all scanner-time checks."""

    downstream_usable: bool = False
    """True if the candidate should be forwarded to downstream stages.
    Typically same as ``passed``, but a scanner could mark a candidate
    as passed-with-warnings and still set downstream_usable=True."""

    # ── Lineage ─────────────────────────────────────────────────
    contract_version: str = SCANNER_V2_CONTRACT_VERSION
    scanner_version: str = ""
    """Version string of the family implementation that built this."""

    generated_at: str = ""
    """ISO 8601 timestamp of when this candidate was produced."""

    # ── Raw reference (debug only) ──────────────────────────────
    _raw_construction: dict[str, Any] = field(
        default_factory=dict, repr=False,
    )
    """Original construction data from Phase B.  Stripped before
    serialization — kept only for in-process debugging."""

    # ── Serialization ───────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON / artifact storage.

        Strips ``_raw_construction`` (debug-only field).
        """
        from dataclasses import asdict

        d = asdict(self)
        d.pop("_raw_construction", None)
        return d


# ── V2ScanResult ────────────────────────────────────────────────────

@dataclass(slots=True)
class V2ScanResult:
    """Top-level result from a single V2 scanner run.

    Contains all candidates (pass + reject) and run-level diagnostics.
    """

    scanner_key: str
    strategy_id: str
    family_key: str
    symbol: str

    candidates: list[V2Candidate] = field(default_factory=list)
    """All candidates that PASSED scanner-time checks."""

    rejected: list[V2Candidate] = field(default_factory=list)
    """All candidates that FAILED scanner-time checks.
    Retained for diagnostics / comparison harness."""

    # ── Run-level counts ────────────────────────────────────────
    total_constructed: int = 0
    total_passed: int = 0
    total_rejected: int = 0

    # ── Run-level diagnostics ───────────────────────────────────
    reject_reason_counts: dict[str, int] = field(default_factory=dict)
    """Aggregated counts of each reject reason code across all rejected
    candidates.  Sum of values == ``total_rejected``."""

    warning_counts: dict[str, int] = field(default_factory=dict)
    """Aggregated warning counts across ALL candidates (pass + reject)."""

    phase_counts: list[dict[str, Any]] = field(default_factory=list)
    """Ordered list of ``{"phase": str, "remaining": int}`` showing
    how many candidates survived each phase.

    Example::

        [
            {"phase": "constructed", "remaining": 420},
            {"phase": "structural_validation", "remaining": 418},
            {"phase": "quote_liquidity_sanity", "remaining": 390},
            {"phase": "recomputed_math", "remaining": 385},
            {"phase": "normalized", "remaining": 385},
        ]
    """

    narrowing_diagnostics: dict[str, Any] = field(default_factory=dict)
    """Narrowing diagnostics from Phase A (V2NarrowingDiagnostics.to_dict()).
    Shows what was loaded, kept, dropped, and why at the chain level."""

    # ── Metadata ────────────────────────────────────────────────
    scanner_version: str = ""
    contract_version: str = SCANNER_V2_CONTRACT_VERSION
    elapsed_ms: float = 0.0
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON / artifact storage."""
        from dataclasses import asdict

        d = asdict(self)
        # Strip _raw_construction from nested candidates
        for c in d.get("candidates", []):
            c.pop("_raw_construction", None)
        for c in d.get("rejected", []):
            c.pop("_raw_construction", None)
        return d
