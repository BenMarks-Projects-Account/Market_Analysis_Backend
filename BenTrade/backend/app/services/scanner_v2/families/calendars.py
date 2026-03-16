"""Options Scanner V2 — Calendar / Diagonal Spreads family.

Handles:
- calendar_call_spread  (same strike, call, multi-expiry)
- calendar_put_spread   (same strike, put, multi-expiry)
- diagonal_call_spread  (different strike, call, multi-expiry)
- diagonal_put_spread   (different strike, put, multi-expiry)

These are fundamentally multi-expiry strategies: the front (near) leg
is sold, and the back (far) leg is bought.  This is the first V2
family to span multiple expirations.

Construction logic (Phase B)
----------------------------
1. Get all expiry buckets from narrowed universe (standard narrowing,
   DTE 7–90).
2. For each valid (near_exp, far_exp) pair where near < far and
   DTE spread ≥ min_dte_spread (default 7):
   a. Calendar: find strikes present in BOTH expirations → one
      candidate per shared strike.
   b. Diagonal: cross-product of near and far strikes with
      |near_strike − far_strike| ≤ max_strike_shift (default $10)
      → one candidate per valid pair.
3. Leg ordering: [short_near, long_far]

Net debit = far_leg.ask − near_leg.bid (paying for time value).

Scanner-time trustworthy vs informational metrics
──────────────────────────────────────────────────
TRUSTWORTHY (computed from leg quotes):
- net_debit: directly from ask/bid of the two legs
- max_loss: ≈ net_debit × 100 (debit paid is approximate max loss)

INFORMATIONAL / DEFERRED (set to None):
- max_profit: path-dependent; depends on far-leg residual value at
  near-leg expiration and underlying price at that point.
- breakeven: depends on IV term structure; no closed-form.
- POP: delta approximation does not work for time spreads.
- EV: requires max_profit, which is unknown.
- RoR: requires max_profit, which is unknown.

These fields are set to None with explanatory notes.  This is honest:
trustworthy limited output > fake exactness.

Family-specific structural checks (Phase C hook):
- Exactly 2 legs
- Same option_type on both legs
- One short (near) + one long (far)
- Different expirations (near < far)
- Calendar: same strike / Diagonal: different strikes

Reuse from V2 shared infrastructure:
- Phase A:  narrow_chain() with standard DTE/strike narrowing
- Phase C:  run_shared_structural_checks() with require_same_expiry=False
- Phase D:  phase_d_quote_liquidity_sanity() for quote/OI/volume presence
- Phase D2: run_quote_sanity(), run_liquidity_sanity(), run_dedup()
- Phase E:  run_math_verification() — existing 2-leg paths handle
            calendar net_debit and max_loss verification; None fields
            are automatically skipped.
- Phase F:  phase_f_normalize() for status/timestamps
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.scanner_v2.base_scanner import BaseV2Scanner
from app.services.scanner_v2.contracts import (
    V2Candidate,
    V2CheckResult,
    V2Leg,
    V2RecomputedMath,
)
from app.services.scanner_v2.data import V2NarrowedUniverse
from app.services.scanner_v2.data.contracts import V2ExpiryBucket, V2OptionContract

_log = logging.getLogger("bentrade.scanner_v2.families.calendars")

# Construction safety cap — prevent combinatorial explosion.
_DEFAULT_GENERATION_CAP = 50_000

# Maximum strike shift for diagonals (in dollars).
_DEFAULT_MAX_STRIKE_SHIFT = 10.0

# Minimum DTE spread between near and far legs (days).
_DEFAULT_MIN_DTE_SPREAD = 7


# ── Strategy config ─────────────────────────────────────────────

_STRATEGY_CONFIG: dict[str, dict[str, object]] = {
    "calendar_call_spread": {"option_type": "call", "is_diagonal": False},
    "calendar_put_spread":  {"option_type": "put",  "is_diagonal": False},
    "diagonal_call_spread": {"option_type": "call", "is_diagonal": True},
    "diagonal_put_spread":  {"option_type": "put",  "is_diagonal": True},
}


class CalendarsV2Scanner(BaseV2Scanner):
    """V2 scanner for calendar and diagonal spread families.

    Multi-expiry: sells near-term, buys far-term at same (calendar)
    or shifted (diagonal) strikes.
    """

    family_key = "calendars"
    scanner_version = "2.0.0"
    dte_min = 7
    dte_max = 90
    require_same_expiry = False  # Multi-expiry family

    # ── Phase B: construct_candidates ───────────────────────────

    def construct_candidates(
        self,
        *,
        chain: dict[str, Any],
        symbol: str,
        underlying_price: float | None,
        expirations: list[str],
        strategy_id: str,
        scanner_key: str,
        context: dict[str, Any],
        narrowed_universe: V2NarrowedUniverse | None = None,
    ) -> list[V2Candidate]:
        """Phase B — construct calendar/diagonal spread candidates.

        Pairs expirations (near < far) and strikes per strategy_id
        config.  Uses standard narrowing buckets — all DTE 7-90
        expirations are available for pairing.

        Calendar: same-strike across two expirations.
        Diagonal: different strikes with |shift| ≤ max_strike_shift.
        """
        if narrowed_universe is None or not narrowed_universe.expiry_buckets:
            return []

        config = _STRATEGY_CONFIG.get(strategy_id)
        if config is None:
            _log.warning("Unknown strategy_id for calendars: %s", strategy_id)
            return []

        option_type: str = config["option_type"]  # type: ignore[assignment]
        is_diagonal: bool = config["is_diagonal"]  # type: ignore[assignment]

        generation_cap = int(context.get("generation_cap", _DEFAULT_GENERATION_CAP))
        min_dte_spread = context.get("min_dte_spread", _DEFAULT_MIN_DTE_SPREAD)
        max_strike_shift = context.get("max_strike_shift", _DEFAULT_MAX_STRIKE_SHIFT)

        buckets = narrowed_universe.expiry_buckets
        sorted_exps = sorted(buckets.keys())
        candidates: list[V2Candidate] = []
        seq = 0
        capped = False

        for i, near_exp in enumerate(sorted_exps):
            if capped:
                break
            near_bucket = buckets[near_exp]
            near_map = _contracts_by_type(near_bucket, option_type)
            if not near_map:
                continue

            for far_exp in sorted_exps[i + 1:]:
                if capped:
                    break
                far_bucket = buckets[far_exp]

                # Enforce minimum DTE spread between legs
                if far_bucket.dte - near_bucket.dte < min_dte_spread:
                    continue

                far_map = _contracts_by_type(far_bucket, option_type)
                if not far_map:
                    continue

                if is_diagonal:
                    # Diagonal: cross-product of different strikes
                    for near_strike in sorted(near_map):
                        if capped:
                            break
                        for far_strike in sorted(far_map):
                            if near_strike == far_strike:
                                continue  # same strike = calendar
                            if abs(far_strike - near_strike) > max_strike_shift:
                                continue
                            cand = _build_calendar_candidate(
                                near_contract=near_map[near_strike],
                                far_contract=far_map[far_strike],
                                near_bucket=near_bucket,
                                far_bucket=far_bucket,
                                symbol=symbol,
                                underlying_price=underlying_price,
                                strategy_id=strategy_id,
                                scanner_key=scanner_key,
                                seq=seq,
                            )
                            candidates.append(cand)
                            seq += 1
                            if seq >= generation_cap:
                                capped = True
                                _log.warning(
                                    "Generation cap %d hit for %s %s",
                                    generation_cap, scanner_key, symbol,
                                )
                                break
                else:
                    # Calendar: shared strikes only
                    shared_strikes = sorted(set(near_map) & set(far_map))
                    for strike in shared_strikes:
                        cand = _build_calendar_candidate(
                            near_contract=near_map[strike],
                            far_contract=far_map[strike],
                            near_bucket=near_bucket,
                            far_bucket=far_bucket,
                            symbol=symbol,
                            underlying_price=underlying_price,
                            strategy_id=strategy_id,
                            scanner_key=scanner_key,
                            seq=seq,
                        )
                        candidates.append(cand)
                        seq += 1
                        if seq >= generation_cap:
                            capped = True
                            _log.warning(
                                "Generation cap %d hit for %s %s",
                                generation_cap, scanner_key, symbol,
                            )
                            break

        _log.info(
            "V2 calendars %s %s: constructed %d %s candidates%s",
            scanner_key, symbol, len(candidates),
            "diagonal" if is_diagonal else "calendar",
            " (CAPPED)" if capped else "",
        )
        return candidates

    # ── Phase C hook: family structural checks ──────────────────

    def family_structural_checks(
        self, candidate: V2Candidate,
    ) -> list[V2CheckResult]:
        """Calendar/diagonal-specific structural checks.

        Validates:
        - Exactly 2 legs
        - Same option_type on both legs
        - One short (near) + one long (far)
        - Different expirations (near < far)
        - Calendar: same strike / Diagonal: different strikes
        """
        checks: list[V2CheckResult] = []
        reject = "v2_cal_invalid_geometry"

        # ── 2 legs ──────────────────────────────────────────
        if len(candidate.legs) != 2:
            checks.append(V2CheckResult(
                "cal_leg_count", False,
                f"expected 2 legs, got {len(candidate.legs)}",
            ))
            candidate.diagnostics.reject_reasons.append(reject)
            return checks
        checks.append(V2CheckResult("cal_leg_count", True, "2 legs"))

        leg_a, leg_b = candidate.legs

        # ── Same option type ────────────────────────────────
        if leg_a.option_type != leg_b.option_type:
            checks.append(V2CheckResult(
                "cal_same_type", False,
                f"types differ: {leg_a.option_type} vs {leg_b.option_type}",
            ))
            candidate.diagnostics.reject_reasons.append(reject)
        else:
            checks.append(V2CheckResult(
                "cal_same_type", True, f"both {leg_a.option_type}",
            ))

        # ── Short + long ────────────────────────────────────
        sides = {leg_a.side, leg_b.side}
        if sides != {"short", "long"}:
            checks.append(V2CheckResult(
                "cal_short_long", False,
                f"expected short+long, got {sorted(sides)}",
            ))
            candidate.diagnostics.reject_reasons.append(reject)
        else:
            checks.append(V2CheckResult("cal_short_long", True, "short+long"))

        # ── Different expirations ───────────────────────────
        if leg_a.expiration == leg_b.expiration:
            checks.append(V2CheckResult(
                "cal_different_expiry", False,
                "both legs have same expiration",
            ))
            candidate.diagnostics.reject_reasons.append(reject)
        else:
            checks.append(V2CheckResult(
                "cal_different_expiry", True,
                f"{leg_a.expiration} vs {leg_b.expiration}",
            ))

        # ── Temporal ordering: short=near, long=far ─────────
        short_leg = leg_a if leg_a.side == "short" else leg_b
        long_leg = leg_b if leg_a.side == "short" else leg_a
        if (short_leg.side == "short" and long_leg.side == "long"
                and short_leg.expiration >= long_leg.expiration):
            checks.append(V2CheckResult(
                "cal_temporal_order", False,
                f"short exp {short_leg.expiration} >= "
                f"long exp {long_leg.expiration}",
            ))
            candidate.diagnostics.reject_reasons.append(reject)
        else:
            checks.append(V2CheckResult(
                "cal_temporal_order", True,
                f"near={short_leg.expiration} < far={long_leg.expiration}",
            ))

        # ── Strike relationship ─────────────────────────────
        is_diagonal = "diagonal" in (candidate.strategy_id or "")
        if is_diagonal:
            if leg_a.strike == leg_b.strike:
                checks.append(V2CheckResult(
                    "cal_strike_shift", False,
                    "diagonal expects different strikes, got same",
                ))
                candidate.diagnostics.reject_reasons.append(reject)
            else:
                shift = abs(leg_a.strike - leg_b.strike)
                checks.append(V2CheckResult(
                    "cal_strike_shift", True, f"strike shift={shift}",
                ))
        else:
            if leg_a.strike != leg_b.strike:
                checks.append(V2CheckResult(
                    "cal_same_strike", False,
                    f"calendar expects same strike: "
                    f"{leg_a.strike} vs {leg_b.strike}",
                ))
                candidate.diagnostics.reject_reasons.append(reject)
            else:
                checks.append(V2CheckResult(
                    "cal_same_strike", True, f"both at {leg_a.strike}",
                ))

        return checks

    # ── Phase E hook: family math ───────────────────────────────

    def family_math(
        self, candidate: V2Candidate,
    ) -> V2RecomputedMath:
        """Calendar/diagonal-specific math recomputation.

        TRUSTWORTHY at scanner time:
        - net_debit: far_leg.ask − near_leg.bid
        - max_loss: ≈ net_debit × 100 (debit paid)

        INFORMATIONAL ONLY (set to None with explanatory notes):
        - max_profit: path-dependent
        - breakeven: IV-dependent
        - POP: no simple delta approximation for time spreads
        - EV, RoR: require max_profit
        """
        legs = candidate.legs
        if len(legs) != 2:
            return V2RecomputedMath(notes={"error": "expected 2 legs"})

        # Identify near (short) and far (long) legs
        short_leg = next((l for l in legs if l.side == "short"), None)
        long_leg = next((l for l in legs if l.side == "long"), None)
        if short_leg is None or long_leg is None:
            return V2RecomputedMath(
                notes={"error": "missing short or long leg"},
            )

        notes: dict[str, str] = {}
        is_diagonal = short_leg.strike != long_leg.strike

        # ── Net debit (trustworthy) ─────────────────────────
        # Formula: net_debit = far_leg.ask − near_leg.bid
        net_debit: float | None = None
        if long_leg.ask is not None and short_leg.bid is not None:
            net_debit = round(long_leg.ask - short_leg.bid, 4)
            notes["net_debit"] = (
                f"far_ask({long_leg.ask}) - near_bid({short_leg.bid})"
                f" = {net_debit}"
            )
        else:
            notes["net_debit"] = "missing bid/ask on legs"

        # ── Max loss (approximate — debit paid) ─────────────
        # Formula: max_loss ≈ net_debit × 100
        max_loss: float | None = None
        if net_debit is not None and net_debit > 0:
            max_loss = round(net_debit * 100, 2)
            notes["max_loss"] = (
                f"net_debit × 100 = {max_loss} "
                "(approximate: actual max loss is the debit paid)"
            )
        elif net_debit is not None:
            notes["max_loss"] = (
                f"net_debit={net_debit} <= 0 — "
                "not a standard calendar debit"
            )
        else:
            notes["max_loss"] = "cannot compute (missing net_debit)"

        # ── Width ───────────────────────────────────────────
        width: float | None = None
        if is_diagonal:
            # Diagonal: width = strike difference (meaningful for risk)
            width = abs(long_leg.strike - short_leg.strike)
            notes["width"] = f"diagonal strike shift = {width}"
        else:
            # Calendar: width is N/A for same-strike time spreads
            notes["width"] = (
                "N/A for same-strike calendar "
                "(width concept not applicable)"
            )

        # ── Max profit (informational — NOT computable) ─────
        notes["max_profit"] = (
            "DEFERRED: max profit is path-dependent. Depends on "
            "far-leg residual value at near-leg expiration and "
            "underlying price at that point. "
            "Cannot compute at scanner time."
        )

        # ── Breakeven (informational — NOT computable) ──────
        notes["breakeven"] = (
            "DEFERRED: breakeven depends on IV term structure "
            "and is not a simple closed-form for time spreads."
        )

        # ── POP (informational — NOT computable) ────────────
        notes["pop"] = (
            "DEFERRED: delta approximation does not work for "
            "multi-expiry time spreads. POP requires modeling "
            "the near-leg expiration scenario."
        )

        # ── EV, RoR (informational — require max_profit) ───
        notes["ev"] = (
            "DEFERRED: requires max_profit (unknown at scanner time)"
        )
        notes["ror"] = (
            "DEFERRED: requires max_profit (unknown at scanner time)"
        )

        return V2RecomputedMath(
            net_debit=net_debit,
            max_loss=max_loss,
            width=width,
            # Explicitly None — not computable at scanner time
            max_profit=None,
            pop=None,
            pop_source=None,
            ev=None,
            ev_per_day=None,
            ror=None,
            kelly=None,
            breakeven=[],
            notes=notes,
        )

    # ── Phase D2 hook: dedup key ────────────────────────────────

    def family_dedup_key(self, candidate: V2Candidate) -> tuple:
        """Calendar dedup key includes both expirations.

        Default dedup key only uses primary expiration.  Calendar
        candidates with different back expirations are structurally
        different trades.
        """
        leg_tuples = frozenset(
            (leg.side, leg.strike, leg.option_type, leg.expiration)
            for leg in candidate.legs
        )
        return (
            candidate.symbol,
            candidate.strategy_id,
            candidate.expiration,
            candidate.expiration_back,
            leg_tuples,
        )


# ═══════════════════════════════════════════════════════════════════
#  Module-level helpers
# ═══════════════════════════════════════════════════════════════════

def _contracts_by_type(
    bucket: V2ExpiryBucket,
    option_type: str,
) -> dict[float, V2OptionContract]:
    """Return strike → contract map filtered to one option_type."""
    return {
        s.contract.strike: s.contract
        for s in bucket.strikes
        if s.contract.option_type == option_type
    }


def _build_calendar_candidate(
    *,
    near_contract: V2OptionContract,
    far_contract: V2OptionContract,
    near_bucket: V2ExpiryBucket,
    far_bucket: V2ExpiryBucket,
    symbol: str,
    underlying_price: float | None,
    strategy_id: str,
    scanner_key: str,
    seq: int,
) -> V2Candidate:
    """Build a single calendar/diagonal candidate from near + far contracts.

    Legs: [short_near (index 0), long_far (index 1)]
    """
    near_exp = near_contract.expiration
    far_exp = far_contract.expiration

    # Strikes label for candidate_id
    if near_contract.strike == far_contract.strike:
        strikes_label = f"{near_contract.strike}"
    else:
        strikes_label = f"{near_contract.strike}-{far_contract.strike}"

    candidate_id = (
        f"{symbol}|{strategy_id}|{near_exp}:{far_exp}|{strikes_label}|{seq}"
    )

    # ── Leg 0: short near (front month — sold) ─────────────
    short_near = V2Leg(
        index=0,
        side="short",
        strike=near_contract.strike,
        option_type=near_contract.option_type,
        expiration=near_contract.expiration,
        bid=near_contract.bid,
        ask=near_contract.ask,
        mid=near_contract.mid,
        delta=near_contract.delta,
        gamma=near_contract.gamma,
        theta=near_contract.theta,
        vega=near_contract.vega,
        iv=near_contract.iv,
        open_interest=near_contract.open_interest,
        volume=near_contract.volume,
    )

    # ── Leg 1: long far (back month — bought) ──────────────
    long_far = V2Leg(
        index=1,
        side="long",
        strike=far_contract.strike,
        option_type=far_contract.option_type,
        expiration=far_contract.expiration,
        bid=far_contract.bid,
        ask=far_contract.ask,
        mid=far_contract.mid,
        delta=far_contract.delta,
        gamma=far_contract.gamma,
        theta=far_contract.theta,
        vega=far_contract.vega,
        iv=far_contract.iv,
        open_interest=far_contract.open_interest,
        volume=far_contract.volume,
    )

    # ── Preliminary math ────────────────────────────────────
    # net_debit = far_leg.ask − near_leg.bid
    net_debit: float | None = None
    if far_contract.ask is not None and near_contract.bid is not None:
        net_debit = round(far_contract.ask - near_contract.bid, 4)

    # width = strike difference for diagonals, None for calendars
    width: float | None = None
    if near_contract.strike != far_contract.strike:
        width = abs(far_contract.strike - near_contract.strike)

    math = V2RecomputedMath(
        net_debit=net_debit,
        width=width,
    )

    return V2Candidate(
        candidate_id=candidate_id,
        scanner_key=scanner_key,
        strategy_id=strategy_id,
        family_key="calendars",
        symbol=symbol,
        underlying_price=underlying_price,
        expiration=near_exp,
        expiration_back=far_exp,
        dte=near_bucket.dte,
        dte_back=far_bucket.dte,
        legs=[short_near, long_far],
        math=math,
    )
