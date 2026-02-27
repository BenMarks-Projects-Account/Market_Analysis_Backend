"""Per-strategy Pydantic trade contracts.

Provides validated models for enriched trade output from each scanner.
These are used for invariant checking — **not** for serializing/deserializing
from disk.  Plugins call ``Model.model_validate(row)`` at the end of
``enrich()`` to catch data-integrity issues before they reach evaluate().

Hierarchy:
    EnrichedLeg         — single option leg (bid/ask/delta/iv/OI/volume)
    BaseTrade           — fields shared by ALL strategies
    CreditSpreadTrade   — put_credit_spread specifics
    DebitSpreadTrade    — call_debit / put_debit specifics
    IronCondorTrade     — iron_condor specifics (4 legs, per-side sigma)

Invariants enforced (via model_validator):
    • credit: 0 < net_credit < width
    • credit: max_loss == (width − net_credit) × 100
    • debit:  0 < net_debit < width
    • IC:     exactly 4 legs present
    • common: if p_win_used is set → pop_model_used must not be "NONE"
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

_log = logging.getLogger("bentrade.strategy_trades")


# ── Shared leg schema ──────────────────────────────────────────────────────

class EnrichedLeg(BaseModel):
    """Single option leg with full market-data snapshot.

    Fields map 1:1 to the ``legs[]`` dicts produced by all plugins.
    """
    model_config = ConfigDict(extra="forbid")

    name: str                               # e.g. "short_put", "long_call"
    right: Literal["put", "call"]
    side: Literal["buy", "sell"]
    strike: float | None = None
    qty: int = 1
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    delta: float | None = None
    iv: float | None = None
    open_interest: int | None = None
    volume: int | None = None
    occ_symbol: str | None = None

    @model_validator(mode="after")
    def _mid_requires_bid_ask(self) -> "EnrichedLeg":
        """If mid is set, bid and ask must also be set."""
        if self.mid is not None and (self.bid is None or self.ask is None):
            _log.warning(
                "EnrichedLeg %s: mid=%s but bid=%s ask=%s — invariant violation",
                self.name, self.mid, self.bid, self.ask,
            )
        return self


# ── Base trade (common fields) ─────────────────────────────────────────────

class BaseTrade(BaseModel):
    """Fields shared by all strategy trade outputs.

    Uses ``extra="allow"`` so strategy-specific or transient fields survive
    validation without being stripped.
    """
    model_config = ConfigDict(extra="allow")

    # Identity
    strategy: str | None = None
    spread_type: str | None = None
    underlying: str | None = None
    underlying_symbol: str | None = None
    symbol: str | None = None
    expiration: str | None = None
    dte: int | None = None

    # Canonical legs + spread-level quotes
    legs: list[dict[str, Any]] | None = None
    spread_bid: float | None = None
    spread_ask: float | None = None
    spread_mid: float | None = None

    # Trade metrics
    underlying_price: float | None = None
    width: float | None = None
    p_win_used: float | None = None
    pop_model_used: str | None = None
    ev_per_share: float | None = None
    ev_to_risk: float | None = None
    return_on_risk: float | None = None
    kelly_fraction: float | None = None
    rank_score: float | None = None
    trade_quality_score: float | None = None

    # Liquidity
    open_interest: int | float | None = None
    volume: int | float | None = None
    bid_ask_spread_pct: float | None = None

    @model_validator(mode="after")
    def _pop_attribution_check(self) -> "BaseTrade":
        """Invariant: if p_win_used is set, pop_model_used must be an explicit source."""
        if self.p_win_used is not None:
            if self.pop_model_used is None or self.pop_model_used == "NONE":
                _log.warning(
                    "POP attribution violation: p_win_used=%s but pop_model_used=%r "
                    "— must be an explicit source (strategy=%s)",
                    self.p_win_used, self.pop_model_used, self.strategy,
                )
        return self


# ── Credit spread trade ───────────────────────────────────────────────────

class CreditSpreadTrade(BaseTrade):
    """Enriched trade output from credit_spread scanner.

    Invariants:
        • 0 < net_credit < width  (when both are set)
        • max_loss_per_share == (width − net_credit) (when both are set)
        • spread_type == "put_credit_spread"
    """
    net_credit: float | None = None
    short_strike: float | None = None
    long_strike: float | None = None
    max_loss_per_share: float | None = None
    max_profit_per_share: float | None = None

    @model_validator(mode="after")
    def _credit_invariants(self) -> "CreditSpreadTrade":
        nc = self.net_credit
        w = self.width
        if nc is not None and w is not None and w > 0:
            if nc <= 0:
                _log.warning(
                    "CreditSpreadTrade: net_credit=%s ≤ 0 — should be positive",
                    nc,
                )
            elif nc >= w:
                _log.warning(
                    "CreditSpreadTrade: net_credit=%s ≥ width=%s — exceeds spread width",
                    nc, w,
                )
        return self


# ── Debit spread trade ────────────────────────────────────────────────────

class DebitSpreadTrade(BaseTrade):
    """Enriched trade output from debit_spreads scanner.

    Invariants:
        • 0 < net_debit < width  (when both are set)
        • spread_type in ("call_debit", "put_debit")
    """
    net_debit: float | None = None
    short_strike: float | None = None
    long_strike: float | None = None
    max_profit: float | None = None
    max_loss: float | None = None
    break_even: float | None = None
    debit_as_pct_of_width: float | None = None
    pop_delta_approx: float | None = None
    pop_refined: float | None = None

    @model_validator(mode="after")
    def _debit_invariants(self) -> "DebitSpreadTrade":
        nd = self.net_debit
        w = self.width
        if nd is not None and w is not None and w > 0:
            if nd <= 0:
                _log.warning(
                    "DebitSpreadTrade: net_debit=%s ≤ 0 — should be positive",
                    nd,
                )
            elif nd >= w:
                _log.warning(
                    "DebitSpreadTrade: net_debit=%s ≥ width=%s — exceeds spread width",
                    nd, w,
                )
        return self


# ── Iron condor trade ─────────────────────────────────────────────────────

class IronCondorTrade(BaseTrade):
    """Enriched trade output from iron_condor scanner.

    Invariants:
        • legs must contain exactly 4 entries
        • spread_type == "iron_condor"
        • All 4 leg-strike fields present when legs[] present
    """
    net_credit: float | None = None
    total_credit: float | None = None
    max_loss: float | None = None

    # Explicit strike fields (convenience, legs[] is authoritative)
    short_put_strike: float | None = None
    long_put_strike: float | None = None
    short_call_strike: float | None = None
    long_call_strike: float | None = None
    put_wing_width: float | None = None
    call_wing_width: float | None = None

    # Sigma diagnostics
    min_sigma_dist: float | None = None
    put_short_sigma_dist: float | None = None
    call_short_sigma_dist: float | None = None

    # Readiness flag (set by IC enrich)
    readiness: bool | None = None

    @model_validator(mode="after")
    def _ic_leg_count(self) -> "IronCondorTrade":
        if self.legs is not None:
            if len(self.legs) != 4:
                _log.warning(
                    "IronCondorTrade: expected 4 legs, got %d", len(self.legs),
                )
        return self

    @model_validator(mode="after")
    def _ic_credit_invariants(self) -> "IronCondorTrade":
        nc = self.net_credit
        w = self.width
        if nc is not None and w is not None and w > 0:
            if nc <= 0:
                _log.warning(
                    "IronCondorTrade: net_credit=%s ≤ 0", nc,
                )
            elif nc >= w:
                _log.warning(
                    "IronCondorTrade: net_credit=%s ≥ width=%s", nc, w,
                )
        return self
