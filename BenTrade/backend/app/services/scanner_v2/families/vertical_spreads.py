"""Options Scanner V2 — Vertical Spreads family.

Handles:
- put_credit_spread
- call_credit_spread
- put_debit
- call_debit

One shared engine — all four variants are parameterized via
``_VARIANT_CONFIG``, not four separate implementations.

Construction logic (Phase B)
----------------------------
For each eligible expiration in the narrowed universe:
    Filter strikes to the target option_type.
    For each pair of strikes (S_low, S_high) where S_low < S_high:
        Assign short/long legs per variant config:
        - put_credit_spread:  short=S_high, long=S_low  (credit)
        - call_credit_spread: short=S_low,  long=S_high (credit)
        - put_debit:          short=S_low,  long=S_high (debit)
        - call_debit:         short=S_high, long=S_low  (debit)

Width = |short_strike - long_strike|

Family-specific structural checks:
- Exactly 2 legs (one short, one long).
- Both legs same option_type (put or call).
- Both legs same expiration.

Family math:
- Uses default vertical math (credit or debit).  No override needed.
  Phase E ``_recompute_vertical_math`` handles all four variants.
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

_log = logging.getLogger("bentrade.scanner_v2.families.vertical_spreads")

# Construction safety cap — prevent combinatorial explosion.
# Vertical spreads are O(n²) per expiry per symbol; without this, a
# symbol with 100 strikes × 20 expirations can generate millions.
_DEFAULT_GENERATION_CAP = 50_000

# Maximum allowable width between strikes in dollars.
# Used as absolute ceiling; dynamic cap based on underlying price
# is computed at construction time (see MAX_WIDTH_PCT).
_DEFAULT_MAX_WIDTH = 30.0

# Max spread width as percentage of underlying price.
# SPY at $560: 3% = $16.80 max width.  Applied in construct_candidates
# alongside the absolute ceiling above.
_DEFAULT_MAX_WIDTH_PCT = 0.03

# Absolute floor — never cap narrower than this.
_DEFAULT_MIN_WIDTH_ABS = 1.0

# Minimum spread width in dollars.
# Skips narrow spreads (e.g. $1-wide SPY) that yield marginal credit.
_DEFAULT_MIN_WIDTH = 2.0

# Short-leg delta range — only generate candidates whose short strike
# has abs(delta) within this band.  Eliminates penny-delta far-OTM and
# near-ATM strikes that income traders wouldn't use.
_DEFAULT_SHORT_DELTA_MIN = 0.05
_DEFAULT_SHORT_DELTA_MAX = 0.40


# ═══════════════════════════════════════════════════════════════════
#  Variant configuration
# ═══════════════════════════════════════════════════════════════════
# For each pair (S_low, S_high) with S_low < S_high:
#   short_is_higher=True  → short=S_high, long=S_low
#   short_is_higher=False → short=S_low,  long=S_high
#
# Credit spreads: short leg is closer to ATM → collects more premium.
# Debit spreads:  long  leg is closer to ATM → costs more premium.

_VARIANT_CONFIG: dict[str, dict[str, Any]] = {
    "put_credit_spread": {
        "option_type": "put",
        "short_is_higher": True,    # short closer to ATM for puts
    },
    "call_credit_spread": {
        "option_type": "call",
        "short_is_higher": False,   # short closer to ATM for calls (lower strike)
    },
    "put_debit": {
        "option_type": "put",
        "short_is_higher": False,   # long closer to ATM for puts
    },
    "call_debit": {
        "option_type": "call",
        "short_is_higher": True,    # long closer to ATM for calls (lower strike)
    },
}


class VerticalSpreadsV2Scanner(BaseV2Scanner):
    """V2 scanner for vertical spread families.

    Supports put_credit_spread, call_credit_spread, put_debit, call_debit.
    All four variants share one construction engine parameterized by
    ``_VARIANT_CONFIG``.
    """

    family_key = "vertical_spreads"
    scanner_version = "2.0.0"
    dte_min = 5
    dte_max = 90

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
        """Phase B — construct all vertical spread candidates.

        Iterates each expiry bucket in the narrowed universe.
        For each bucket:
        1. Filter strikes to the target option_type.
        2. Generate all valid (short, long) pairs per variant config.
        3. Build a V2Candidate for each pair with initial math.

        Uses ``_VARIANT_CONFIG`` to determine short/long assignment.
        """
        config = _VARIANT_CONFIG.get(strategy_id)
        if config is None:
            _log.warning(
                "Unknown strategy_id=%r for vertical spreads", strategy_id,
            )
            return []

        if narrowed_universe is None or not narrowed_universe.expiry_buckets:
            return []

        target_type: str = config["option_type"]
        short_is_higher: bool = config["short_is_higher"]

        generation_cap = int(context.get("generation_cap", _DEFAULT_GENERATION_CAP))
        max_width_abs = float(context.get("max_width", _DEFAULT_MAX_WIDTH))
        max_width_pct = float(context.get("max_width_pct", _DEFAULT_MAX_WIDTH_PCT))
        min_width = float(context.get("min_width", _DEFAULT_MIN_WIDTH))

        # Dynamic width cap: percentage of underlying, clamped to absolute bounds.
        # Derived field: max_width = clamp(underlying × pct, MIN_WIDTH_ABS, max_width_abs)
        if underlying_price and underlying_price > 0:
            max_width = min(underlying_price * max_width_pct, max_width_abs)
            max_width = max(max_width, _DEFAULT_MIN_WIDTH_ABS)
        else:
            max_width = max_width_abs
        short_delta_min = float(context.get("short_delta_min", _DEFAULT_SHORT_DELTA_MIN))
        short_delta_max = float(context.get("short_delta_max", _DEFAULT_SHORT_DELTA_MAX))

        candidates: list[V2Candidate] = []
        total_seq = 0
        capped = False

        # Per-expiration budget — ensures all expirations get fair representation
        num_expirations = len(narrowed_universe.expiry_buckets)
        per_exp_cap = max(200, generation_cap // max(num_expirations, 1))

        for exp in sorted(narrowed_universe.expiry_buckets.keys()):
            bucket = narrowed_universe.expiry_buckets[exp]
            if capped:
                break
            exp_seq = 0
            # Filter strikes to target option type
            typed_contracts: list[tuple[float, Any]] = []
            for entry in bucket.strikes:
                if entry.contract.option_type == target_type:
                    typed_contracts.append((entry.strike, entry.contract))

            if len(typed_contracts) < 2:
                continue

            # Sort ascending by strike
            typed_contracts.sort(key=lambda x: x[0])

            # Generate all valid (S_low, S_high) pairs
            for i in range(len(typed_contracts)):
                if capped or exp_seq >= per_exp_cap:
                    break
                s_low, c_low = typed_contracts[i]

                # When short leg is the LOW strike, filter on outer loop
                if not short_is_higher:
                    if c_low.delta is None:
                        continue
                    if not (short_delta_min <= abs(c_low.delta) <= short_delta_max):
                        continue

                for j in range(i + 1, len(typed_contracts)):
                    s_high, c_high = typed_contracts[j]

                    # Skip narrow spreads (e.g. $1-wide SPY)
                    if s_high - s_low < min_width:
                        continue  # try wider pairs
                    # Skip impossibly wide spreads
                    if s_high - s_low > max_width:
                        break  # remaining j values only wider

                    # When short leg is the HIGH strike, filter on inner loop
                    if short_is_higher:
                        if c_high.delta is None:
                            continue
                        if not (short_delta_min <= abs(c_high.delta) <= short_delta_max):
                            continue

                    if short_is_higher:
                        short_strike, short_c = s_high, c_high
                        long_strike, long_c = s_low, c_low
                    else:
                        short_strike, short_c = s_low, c_low
                        long_strike, long_c = s_high, c_high

                    cand = _build_candidate(
                        symbol=symbol,
                        strategy_id=strategy_id,
                        scanner_key=scanner_key,
                        family_key=self.family_key,
                        underlying_price=underlying_price,
                        expiration=exp,
                        dte=bucket.dte,
                        short_strike=short_strike,
                        short_contract=short_c,
                        long_strike=long_strike,
                        long_contract=long_c,
                        option_type=target_type,
                        seq=total_seq,
                    )
                    candidates.append(cand)
                    exp_seq += 1
                    total_seq += 1

                    if exp_seq >= per_exp_cap:
                        break
                    if total_seq >= generation_cap:
                        capped = True
                        _log.warning(
                            "Vertical %s %s: hit generation cap (%d)",
                            strategy_id, symbol, generation_cap,
                        )
                        break

        _log.info(
            "Vertical %s %s: constructed %d candidates from %d expirations%s",
            strategy_id, symbol, len(candidates),
            len(narrowed_universe.expiry_buckets),
            " (CAPPED)" if capped else "",
        )

        return candidates

    # ── Phase C hook: family structural checks ──────────────────

    def family_structural_checks(
        self, candidate: V2Candidate,
    ) -> list[V2CheckResult]:
        """Vertical-spread-specific structural checks.

        Checks beyond what shared Phase C already validates:
        - One short + one long leg (has_short_and_long).
        - Same option_type on both legs.

        On failure, appends ``v2_malformed_legs`` to reject_reasons.
        """
        checks: list[V2CheckResult] = []

        # Both sides present
        sides = {leg.side for leg in candidate.legs}
        if sides != {"long", "short"}:
            checks.append(V2CheckResult(
                "vertical_has_short_and_long", False,
                f"expected one long + one short, got {sides}",
            ))
            if "v2_malformed_legs" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
        else:
            checks.append(V2CheckResult(
                "vertical_has_short_and_long", True, "",
            ))

        # Same option type
        types = {leg.option_type for leg in candidate.legs}
        if len(types) > 1:
            checks.append(V2CheckResult(
                "vertical_same_option_type", False,
                f"mixed option types: {types}",
            ))
            if "v2_malformed_legs" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
        else:
            checks.append(V2CheckResult(
                "vertical_same_option_type", True,
                f"all {next(iter(types))}" if types else "",
            ))

        return checks


# ═══════════════════════════════════════════════════════════════════
#  Construction helper
# ═══════════════════════════════════════════════════════════════════

def _build_candidate(
    *,
    symbol: str,
    strategy_id: str,
    scanner_key: str,
    family_key: str,
    underlying_price: float | None,
    expiration: str,
    dte: int,
    short_strike: float,
    short_contract: Any,
    long_strike: float,
    long_contract: Any,
    option_type: str,
    seq: int,
) -> V2Candidate:
    """Build a single V2Candidate from a short/long strike pair.

    Sets identity fields, legs with full quote/greek data, and
    preliminary math (width + initial credit/debit from raw quotes).
    Phase E will recompute all math fields from the leg data.
    """
    short_leg = V2Leg(
        index=0,
        side="short",
        strike=short_strike,
        option_type=option_type,
        expiration=expiration,
        bid=short_contract.bid,
        ask=short_contract.ask,
        mid=short_contract.mid,
        delta=short_contract.delta,
        gamma=short_contract.gamma,
        theta=short_contract.theta,
        vega=short_contract.vega,
        iv=short_contract.iv,
        open_interest=short_contract.open_interest,
        volume=short_contract.volume,
    )
    long_leg = V2Leg(
        index=1,
        side="long",
        strike=long_strike,
        option_type=option_type,
        expiration=expiration,
        bid=long_contract.bid,
        ask=long_contract.ask,
        mid=long_contract.mid,
        delta=long_contract.delta,
        gamma=long_contract.gamma,
        theta=long_contract.theta,
        vega=long_contract.vega,
        iv=long_contract.iv,
        open_interest=long_contract.open_interest,
        volume=long_contract.volume,
    )

    width = abs(short_strike - long_strike)

    # Preliminary credit/debit from raw quotes.
    # Phase E recomputes — this is for Phase B traceability.
    math = V2RecomputedMath(width=width)
    if short_contract.bid is not None and long_contract.ask is not None:
        credit = short_contract.bid - long_contract.ask
        if credit > 0:
            math.net_credit = round(credit, 4)
        elif credit < 0:
            math.net_debit = round(-credit, 4)

    candidate_id = (
        f"{symbol}|{strategy_id}|{expiration}"
        f"|{short_strike}/{long_strike}|{seq}"
    )

    return V2Candidate(
        candidate_id=candidate_id,
        scanner_key=scanner_key,
        strategy_id=strategy_id,
        family_key=family_key,
        symbol=symbol,
        underlying_price=underlying_price,
        expiration=expiration,
        dte=dte,
        legs=[short_leg, long_leg],
        math=math,
    )
