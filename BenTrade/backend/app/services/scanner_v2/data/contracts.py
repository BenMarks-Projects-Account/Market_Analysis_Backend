"""V2 data-narrowing framework — contracts and data shapes.

All data types for the narrowing pipeline live here.  No business logic.

Contracts
---------
V2NarrowingRequest      Parameters for a narrowing run.
V2OptionContract        Normalized single option contract.
V2StrikeEntry           One strike within an expiry bucket.
V2ExpiryBucket          All contracts for one expiration date.
V2UnderlyingSnapshot    Normalized underlying price / context.
V2NarrowedUniverse      Complete narrowing output for family builders.
V2NarrowingDiagnostics  What was loaded, kept, dropped, and why.

Design rules
────────────
1. ``None`` means "not available".  Never use 0 as a sentinel.
2. All monetary values use the same convention as V2 contracts:
   per-share for quote fields, per-contract where labeled.
3. These types are consumed by family ``construct_candidates()``
   implementations — keep them clean and stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Narrowing request ───────────────────────────────────────────────

@dataclass(slots=True)
class V2NarrowingRequest:
    """Parameters that control narrowing behavior.

    Family implementations create a request with their preferred
    windows, then call ``narrow_chain()`` to get a V2NarrowedUniverse.

    DTE window
    ----------
    ``dte_min`` / ``dte_max`` — structural DTE range (default 1–90).

    Option types
    ────────────
    ``option_types`` — list of ``"put"`` / ``"call"`` to keep.
    Empty list = keep both.

    Strike window
    ─────────────
    ``distance_min_pct`` / ``distance_max_pct`` — min/max distance
    from underlying as percentage (0.0–1.0).  None = no constraint.

    ``moneyness`` — ``"otm"`` | ``"itm"`` | ``"atm"`` | ``None``.
    Filter strikes by moneyness relative to underlying.

    Multi-expiry
    ────────────
    ``multi_expiry`` — if True, allow different expirations in the
    narrowed universe (calendars/diagonals).  Default False =
    same-expiry strategies.

    ``near_dte_min`` / ``near_dte_max`` — near-leg DTE window
    (only used when ``multi_expiry=True``).

    ``far_dte_min`` / ``far_dte_max`` — far-leg DTE window
    (only used when ``multi_expiry=True``).
    """

    # ── DTE window ──────────────────────────────────────────────
    dte_min: int = 1
    dte_max: int = 90

    # ── Option type filter ──────────────────────────────────────
    option_types: list[str] = field(default_factory=list)
    """Empty = keep both puts and calls."""

    # ── Strike distance window (% from underlying) ─────────────
    distance_min_pct: float | None = None
    """Min distance from spot as decimal (e.g. 0.01 = 1%)."""

    distance_max_pct: float | None = None
    """Max distance from spot as decimal (e.g. 0.12 = 12%)."""

    # ── Moneyness filter ────────────────────────────────────────
    moneyness: str | None = None
    """``"otm"`` | ``"itm"`` | ``"atm"`` | None (no filter)."""

    # ── Multi-expiry (calendars/diagonals) ──────────────────────
    multi_expiry: bool = False

    near_dte_min: int | None = None
    near_dte_max: int | None = None
    far_dte_min: int | None = None
    far_dte_max: int | None = None

    # ── Extra context (family can attach hints) ─────────────────
    extra: dict[str, Any] = field(default_factory=dict)


# ── Normalized option contract ──────────────────────────────────────

@dataclass(slots=True)
class V2OptionContract:
    """Normalized single option contract from the chain.

    Produced by ``chain.normalize_chain()`` from raw Tradier data.
    Fields mirror V2Leg but represent the raw chain data before
    any candidate pairing.
    """

    symbol: str                        # OCC symbol (SPY260320P00590000)
    root_symbol: str                   # Underlying (SPY)
    strike: float
    option_type: str                   # "put" | "call"
    expiration: str                    # ISO date

    bid: float | None = None
    ask: float | None = None
    mid: float | None = None           # Derived: (bid + ask) / 2

    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    iv: float | None = None

    open_interest: int | None = None
    volume: int | None = None

    # ── Data quality flags ──────────────────────────────────────
    quote_valid: bool = True
    """False if bid/ask are missing or inverted."""

    def distance_pct(self, underlying_price: float) -> float | None:
        """Distance from underlying as a positive fraction."""
        if underlying_price <= 0:
            return None
        return abs(self.strike - underlying_price) / underlying_price

    def is_otm(self, underlying_price: float) -> bool:
        """True if this contract is out-of-the-money."""
        if self.option_type == "put":
            return self.strike < underlying_price
        return self.strike > underlying_price

    def is_itm(self, underlying_price: float) -> bool:
        return not self.is_otm(underlying_price)


# ── Strike entry (deduplicated per expiry) ──────────────────────────

@dataclass(slots=True)
class V2StrikeEntry:
    """One strike within an expiry bucket.

    If multiple contracts exist for the same strike (rare but possible
    with different OCC symbols), the one with highest open interest
    is kept and duplicates are tracked in diagnostics.
    """

    strike: float
    contract: V2OptionContract
    duplicates_dropped: int = 0


# ── Expiry bucket ──────────────────────────────────────────────────

@dataclass(slots=True)
class V2ExpiryBucket:
    """All narrowed contracts for one expiration date.

    Provides strike-indexed access for family builders.
    """

    expiration: str                    # ISO date
    dte: int
    option_type: str | None = None     # "put" | "call" | None (mixed)

    # Strike-indexed contracts (sorted by strike)
    strikes: list[V2StrikeEntry] = field(default_factory=list)

    # Convenience
    strike_count: int = 0

    # IV sampling
    median_iv: float | None = None
    """Median IV across contracts in this bucket (for sigma distance)."""

    def get_strike_map(self) -> dict[float, V2OptionContract]:
        """Return strike → contract map for quick lookups."""
        return {s.strike: s.contract for s in self.strikes}

    def get_strikes_list(self) -> list[float]:
        """Return sorted list of available strikes."""
        return [s.strike for s in self.strikes]

    def find_nearest_strike(
        self,
        target: float,
        exclude: set[float] | None = None,
    ) -> V2StrikeEntry | None:
        """Find the strike entry nearest to target.

        Excludes strikes in ``exclude`` set if provided.
        """
        best: V2StrikeEntry | None = None
        best_dist = float("inf")
        for entry in self.strikes:
            if exclude and entry.strike in exclude:
                continue
            dist = abs(entry.strike - target)
            if dist < best_dist:
                best_dist = dist
                best = entry
        return best


# ── Underlying snapshot ─────────────────────────────────────────────

@dataclass(slots=True)
class V2UnderlyingSnapshot:
    """Normalized underlying price and context.

    Family builders should use ``price`` as the canonical underlying
    value for all distance/moneyness calculations.

    ``price_source`` indicates where the price came from:
    - ``"provided"`` — explicitly passed by caller.
    - ``"chain_derived"`` — derived from option chain mid-points.
    - ``"unknown"`` — source not determined.
    """

    symbol: str
    price: float
    price_source: str = "provided"
    as_of: str = ""                    # ISO 8601 timestamp

    # Optional volatility context
    iv_rank: float | None = None
    iv_percentile: float | None = None
    hv_20: float | None = None         # 20-day historical vol

    # Quality flags
    is_stale: bool = False
    """True if the price may be stale (e.g. after-hours)."""

    warnings: list[str] = field(default_factory=list)


# ── Narrowing diagnostics ──────────────────────────────────────────

@dataclass(slots=True)
class V2NarrowingDiagnostics:
    """What was loaded, kept, dropped, and why.

    Every narrowing run produces one of these.  It makes the
    narrowing pipeline fully transparent.
    """

    # ── Expiry counts ───────────────────────────────────────────
    total_expirations_loaded: int = 0
    expirations_kept: int = 0
    expirations_dropped: int = 0

    expiry_drop_reasons: dict[str, int] = field(default_factory=dict)
    """Counts by reason: ``{"dte_below_min": 3, "dte_above_max": 5}``."""

    expirations_kept_list: list[str] = field(default_factory=list)
    """ISO date strings of kept expirations."""

    expirations_dropped_list: list[str] = field(default_factory=list)
    """ISO date strings of dropped expirations."""

    # ── Contract counts ─────────────────────────────────────────
    total_contracts_loaded: int = 0
    contracts_after_type_filter: int = 0
    contracts_after_expiry_filter: int = 0
    contracts_after_strike_filter: int = 0
    contracts_final: int = 0

    # ── Strike counts ───────────────────────────────────────────
    total_unique_strikes: int = 0
    strikes_kept: int = 0
    strikes_dropped: int = 0

    strike_drop_reasons: dict[str, int] = field(default_factory=dict)
    """Counts by reason: ``{"distance_below_min": 5, "wrong_moneyness": 12}``."""

    # ── Duplicate handling ──────────────────────────────────────
    duplicate_contracts_dropped: int = 0

    # ── Data quality ────────────────────────────────────────────
    contracts_missing_bid: int = 0
    contracts_missing_ask: int = 0
    contracts_inverted_quote: int = 0
    contracts_missing_delta: int = 0
    contracts_missing_iv: int = 0
    contracts_missing_oi: int = 0
    contracts_missing_volume: int = 0

    # ── Chain completeness ───────────────────────────────────────
    chain_completeness_warning: bool = False
    chain_contract_count: int = 0
    chain_expected_min: int = 0

    # ── Warnings ────────────────────────────────────────────────
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


# ── Narrowed universe ──────────────────────────────────────────────

@dataclass(slots=True)
class V2NarrowedUniverse:
    """Complete narrowing output for family builders.

    This is what ``narrow_chain()`` returns and what family
    ``construct_candidates()`` consumes.

    Structure
    ---------
    - ``underlying`` — normalized underlying snapshot.
    - ``expiry_buckets`` — dict of ``expiration → V2ExpiryBucket``,
      each containing deduplicated, strike-sorted contracts.
    - ``diagnostics`` — full narrowing trace.
    - ``request`` — the narrowing request that produced this universe.

    For multi-expiry families (calendars), there may be multiple
    expiry buckets.  For same-expiry families (verticals, condors),
    each bucket can be processed independently.
    """

    underlying: V2UnderlyingSnapshot
    expiry_buckets: dict[str, V2ExpiryBucket] = field(default_factory=dict)
    """Keyed by ISO expiration date."""

    diagnostics: V2NarrowingDiagnostics = field(
        default_factory=V2NarrowingDiagnostics,
    )
    request: V2NarrowingRequest = field(
        default_factory=V2NarrowingRequest,
    )

    @property
    def total_strikes(self) -> int:
        return sum(b.strike_count for b in self.expiry_buckets.values())

    @property
    def total_contracts(self) -> int:
        return self.diagnostics.contracts_final

    @property
    def is_empty(self) -> bool:
        return not self.expiry_buckets

    def get_single_expiry_bucket(self) -> V2ExpiryBucket | None:
        """Convenience for same-expiry families: return the sole bucket.

        Returns None if there are 0 or >1 buckets.
        """
        if len(self.expiry_buckets) == 1:
            return next(iter(self.expiry_buckets.values()))
        return None
