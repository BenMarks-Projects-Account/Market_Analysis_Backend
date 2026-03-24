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
# Filters out impractically wide spreads at construction time.
_DEFAULT_MAX_WIDTH = 50.0

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
    dte_min = 1
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
        # === TEMPORARY DIAGNOSTIC LOGGING (remove after debugging) ===
        import json as _diag_json
        from pathlib import Path as _DiagPath
        from datetime import datetime as _DiagDT
        _diag = {
            "timestamp": _DiagDT.now().isoformat(),
            "scanner_key": scanner_key,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "phase_a": {},
            "phase_b": {
                "per_expiry": [],
                "total_constructed": 0,
                "delta_filter_skipped": 0,
                "delta_none_skipped": 0,
                "width_too_narrow_skipped": 0,
                "width_too_wide_skipped": 0,
                "cap_hit": False,
            },
            "config": {},
        }
        # === END DIAG INIT ===

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
        max_width = float(context.get("max_width", _DEFAULT_MAX_WIDTH))
        min_width = float(context.get("min_width", _DEFAULT_MIN_WIDTH))
        short_delta_min = float(context.get("short_delta_min", _DEFAULT_SHORT_DELTA_MIN))
        short_delta_max = float(context.get("short_delta_max", _DEFAULT_SHORT_DELTA_MAX))

        # === DIAG: capture resolved config ===
        _diag["config"] = {
            "target_type": target_type,
            "short_is_higher": short_is_higher,
            "generation_cap": generation_cap,
            "max_width": max_width,
            "min_width": min_width,
            "short_delta_min": short_delta_min,
            "short_delta_max": short_delta_max,
        }

        candidates: list[V2Candidate] = []
        total_seq = 0
        capped = False

        # Per-expiration budget — ensures all expirations get fair representation
        num_expirations = len(narrowed_universe.expiry_buckets)
        per_exp_cap = max(200, generation_cap // max(num_expirations, 1))

        # === DIAG: Phase A summary ===
        _diag["phase_a"] = {
            "total_expirations": num_expirations,
            "expiration_dates": sorted(narrowed_universe.expiry_buckets.keys()),
            "contracts_per_expiry": {
                exp: len(bucket.strikes)
                for exp, bucket in narrowed_universe.expiry_buckets.items()
            },
            "total_contracts": sum(
                len(bucket.strikes)
                for bucket in narrowed_universe.expiry_buckets.values()
            ),
            "underlying_price": underlying_price,
            "per_exp_cap": per_exp_cap,
        }

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

            # === DIAG: per-expiry entry ===
            _diag_exp: dict[str, Any] = {
                "expiry": exp,
                "dte": getattr(bucket, 'dte', None),
                "all_strikes_count": len(bucket.strikes),
                "typed_contract_count": len(typed_contracts),
                "delta_values": [],
                "delta_none_skipped": 0,
                "delta_filter_skipped": 0,
                "width_too_narrow_skipped": 0,
                "width_too_wide_skipped": 0,
                "candidates_constructed": 0,
            }
            # Sample first 10 contracts' delta/quote data
            for _idx, (_s, _c) in enumerate(typed_contracts[:10]):
                _dv = getattr(_c, 'delta', None)
                if _dv is None and hasattr(_c, 'greeks'):
                    _dv = (_c.greeks or {}).get('delta') if isinstance(getattr(_c, 'greeks', None), dict) else None
                _diag_exp["delta_values"].append({
                    "strike": _s,
                    "delta": _dv,
                    "bid": getattr(_c, 'bid', None),
                    "ask": getattr(_c, 'ask', None),
                    "oi": getattr(_c, 'open_interest', None),
                })

            if len(typed_contracts) < 2:
                _diag["phase_b"]["per_expiry"].append(_diag_exp)
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
                        _diag_exp["delta_none_skipped"] += 1
                        _diag["phase_b"]["delta_none_skipped"] += 1
                        continue
                    if not (short_delta_min <= abs(c_low.delta) <= short_delta_max):
                        _diag_exp["delta_filter_skipped"] += 1
                        _diag["phase_b"]["delta_filter_skipped"] += 1
                        continue

                for j in range(i + 1, len(typed_contracts)):
                    s_high, c_high = typed_contracts[j]

                    # Skip narrow spreads (e.g. $1-wide SPY)
                    if s_high - s_low < min_width:
                        _diag_exp["width_too_narrow_skipped"] += 1
                        _diag["phase_b"]["width_too_narrow_skipped"] += 1
                        continue  # try wider pairs
                    # Skip impossibly wide spreads
                    if s_high - s_low > max_width:
                        _diag_exp["width_too_wide_skipped"] += 1
                        _diag["phase_b"]["width_too_wide_skipped"] += 1
                        break  # remaining j values only wider

                    # When short leg is the HIGH strike, filter on inner loop
                    if short_is_higher:
                        if c_high.delta is None:
                            _diag_exp["delta_none_skipped"] += 1
                            _diag["phase_b"]["delta_none_skipped"] += 1
                            continue
                        if not (short_delta_min <= abs(c_high.delta) <= short_delta_max):
                            _diag_exp["delta_filter_skipped"] += 1
                            _diag["phase_b"]["delta_filter_skipped"] += 1
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
                    _diag_exp["candidates_constructed"] += 1
                    _diag["phase_b"]["total_constructed"] += 1

                    if exp_seq >= per_exp_cap:
                        break
                    if total_seq >= generation_cap:
                        capped = True
                        _diag["phase_b"]["cap_hit"] = True
                        _log.warning(
                            "Vertical %s %s: hit generation cap (%d)",
                            strategy_id, symbol, generation_cap,
                        )
                        break

            _diag["phase_b"]["per_expiry"].append(_diag_exp)

        _log.info(
            "Vertical %s %s: constructed %d candidates from %d expirations%s",
            strategy_id, symbol, len(candidates),
            len(narrowed_universe.expiry_buckets),
            " (CAPPED)" if capped else "",
        )

        # === WRITE DIAGNOSTIC REPORT (TEMPORARY — remove after debugging) ===
        import os as _diag_os
        if not _diag_os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                _diag_dir = _DiagPath("results/diagnostics")
                _diag_dir.mkdir(parents=True, exist_ok=True)
                _diag_file = _diag_dir / f"options_diag_{scanner_key}_{_DiagDT.now().strftime('%Y%m%d_%H%M%S')}.json"
                with open(_diag_file, "w") as _f:
                    _diag_json.dump(_diag, _f, indent=2, default=str)
                _log.info("DIAG: wrote %s", _diag_file)
            except Exception as _diag_exc:
                _log.warning("event=diag_write_failed error=%s", _diag_exc)
        # === END DIAGNOSTIC LOGGING ===

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
