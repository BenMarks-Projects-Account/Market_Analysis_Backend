"""StrategyPlugin ABC — single contract for all strategy scanner plugins.

Every strategy plugin must implement the four core phases:
    build_candidates  → candidate construction from raw chains
    enrich            → per-candidate enrichment (quotes, metrics, legs[])
    evaluate          → quality-gate filtering (returns pass/fail + reasons)
    score             → ranking score + tie-break dict

Additionally, plugins CAN override two trace-support hooks so that
strategy_service.py never needs strategy-specific field knowledge:
    build_near_miss_entry  → extract near-miss diagnostics from a rejected row
    compute_enrichment_counters → derive quote/spread/POP counters from enriched rows
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


# ── POP source labels ───────────────────────────────────────────────────────
# Stable enum of all valid pop_model_used values.
# Plugins MUST use one of these when setting pop_model_used.
# If POP could not be computed, use POP_SOURCE_NONE.
# If POP *is* set but pop_model_used is NONE, that's an invariant violation.
POP_SOURCE_NONE = "NONE"
POP_SOURCE_NORMAL_CDF = "normal_cdf"
POP_SOURCE_DELTA_APPROX = "DELTA_APPROX"
POP_SOURCE_DELTA_ADJUSTED = "DELTA_ADJUSTED"
POP_SOURCE_BREAKEVEN_LOGNORMAL = "BREAKEVEN_LOGNORMAL"
POP_SOURCE_MODEL = "MODEL"
POP_SOURCE_FALLBACK = "FALLBACK"

ALL_POP_SOURCES = frozenset({
    POP_SOURCE_NONE,
    POP_SOURCE_NORMAL_CDF,
    POP_SOURCE_DELTA_APPROX,
    POP_SOURCE_DELTA_ADJUSTED,
    POP_SOURCE_BREAKEVEN_LOGNORMAL,
    POP_SOURCE_MODEL,
    POP_SOURCE_FALLBACK,
})


class StrategyPlugin(ABC):
    """Abstract base class for all strategy scanner plugins.

    Core phases (REQUIRED — abstract):
        build_candidates → enrich → evaluate → score

    Trace hooks (OPTIONAL — default implementations provided):
        build_near_miss_entry  — strategy-specific near-miss fields
        compute_enrichment_counters — quote/spread/POP derivation counters
    """

    id: str = "base"
    display_name: str = "Base Strategy"

    # ── Core phases ─────────────────────────────────────────────────────────

    @abstractmethod
    def build_candidates(self, inputs: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def enrich(self, candidates: list[dict[str, Any]], inputs: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def evaluate(self, trade: dict[str, Any]) -> tuple[bool, list[str]]:
        raise NotImplementedError

    @abstractmethod
    def score(self, trade: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        raise NotImplementedError

    # ── Trace hooks (overridable) ───────────────────────────────────────────

    def build_near_miss_entry(
        self,
        row: dict[str, Any],
        reasons: list[str],
        base_entry: dict[str, Any],
    ) -> dict[str, Any]:
        """Append strategy-specific diagnostic fields to a near-miss entry.

        Called by strategy_service for each rejected candidate that makes
        into the near-miss top-N.  *base_entry* already contains the universal
        fields (symbol, expiration, dte, short_strike, long_strike, width,
        net_credit, ev, pop, ror, etc.).  The plugin should add any strategy-
        specific fields (IC leg diagnostics, sigma distances, etc.).

        Default: return base_entry unchanged.
        """
        return base_entry

    def compute_enrichment_counters(
        self,
        enriched: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Derive enrichment counters from enriched rows.

        Returns a dict of counter_name → int.  At minimum should include:
          leg_quote_lookup_success, spread_quote_derived_success,
          quote_lookup_partial, quote_lookup_missing.

        Default implementation uses canonical legs[] array.
        """
        total = len(enriched)
        has_all_quotes = 0
        quote_partial = 0
        spread_derived = 0

        for row in enriched:
            if not isinstance(row, dict):
                continue
            legs = row.get("legs")
            if isinstance(legs, list) and len(legs) >= 2:
                bid_ok = [isinstance(lg, dict) and lg.get("bid") is not None for lg in legs]
                ask_ok = [isinstance(lg, dict) and lg.get("ask") is not None for lg in legs]
                if all(bid_ok) and all(ask_ok):
                    has_all_quotes += 1
                    # Spread derived: spread_bid+spread_ask present
                    # OR net_credit/net_debit present
                    if (
                        (row.get("spread_bid") is not None and row.get("spread_ask") is not None)
                        or row.get("net_credit") is not None
                        or row.get("net_debit") is not None
                    ):
                        spread_derived += 1
                elif any(b and a for b, a in zip(bid_ok, ask_ok)):
                    quote_partial += 1
            else:
                # Legacy 2-leg path: use transient fields
                fields = [
                    row.get("_short_bid"), row.get("_short_ask"),
                    row.get("_long_bid"), row.get("_long_ask"),
                ]
                present = sum(1 for f in fields if f is not None)
                if present == 4:
                    has_all_quotes += 1
                    if (row.get("spread_bid") is not None
                            and row.get("spread_ask") is not None):
                        spread_derived += 1
                elif present > 0:
                    quote_partial += 1

        quote_failed = total - has_all_quotes
        quote_missing = quote_failed - quote_partial

        return {
            "total_enriched": total,
            "leg_quote_lookup_attempted": total,
            "leg_quote_lookup_success": has_all_quotes,
            "leg_quote_lookup_failed": quote_failed,
            "quote_lookup_partial": quote_partial,
            "quote_lookup_missing": quote_missing,
            "spread_quote_derived_attempted": total,
            "spread_quote_derived_success": spread_derived,
            "spread_quote_derived_failed": total - spread_derived,
        }

    def validate_pop_attribution(self, trade: dict[str, Any]) -> str | None:
        """Check POP / pop_model_used consistency.

        Returns an error string if invariant violated, else None.
        Invariant: if p_win_used is not None, pop_model_used must not be NONE.
        """
        p_win = trade.get("p_win_used")
        pop_model = trade.get("pop_model_used")
        if p_win is not None and (pop_model is None or pop_model == POP_SOURCE_NONE):
            return (
                f"INVARIANT_VIOLATION: p_win_used={p_win} but "
                f"pop_model_used={pop_model!r} — must be an explicit source"
            )
        return None

    # ── Transient field cleanup ────────────────────────────────────────────

    #: Fields to strip from accepted trades before persisting.
    #: Plugins can override to add strategy-specific transient fields.
    TRANSIENT_FIELDS: frozenset[str] = frozenset({
        "_quote_rejection", "_rejection_codes",
        "_short_bid", "_short_ask", "_long_bid", "_long_ask",
        "_short_oi", "_short_vol", "_long_oi", "_long_vol",
        "_credit_basis",
        "_policy", "_request",
    })
