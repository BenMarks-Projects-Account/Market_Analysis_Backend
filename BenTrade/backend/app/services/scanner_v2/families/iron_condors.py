"""Options Scanner V2 — Iron Condors family.

Handles:
- iron_condor

Builds iron condors as a composition of two credit spread sides:
- Put credit spread:  short put (higher, closer to ATM) + long put (lower)
- Call credit spread: short call (lower, closer to ATM) + long call (higher)

Geometry: put_long < put_short < underlying < call_short < call_long

Construction logic (Phase B)
----------------------------
For each eligible expiry bucket:
    1. Separate OTM puts (strike < underlying) and OTM calls (strike > underlying)
    2. Generate all valid put credit spread sides (short > long, both puts)
    3. Generate all valid call credit spread sides (short < long, both calls)
    4. Cross-product into iron condor candidates (sides never overlap
       because puts < underlying < calls)
    5. Set preliminary pricing from raw leg quotes

Family-specific structural checks (Phase C hook):
- Exactly 4 legs (2 puts, 2 calls).
- Strike ordering: put_long < put_short < call_short < call_long.
- Per-side positive width.

Family math override (Phase E hook):
- put_side_credit  = put_short.bid  - put_long.ask
- call_side_credit = call_short.bid - call_long.ask
- net_credit = put_side_credit + call_side_credit
- max_profit = net_credit × 100
- width = max(put_width, call_width)   [effective risk width]
- max_loss = (width - net_credit) × 100
- breakeven_low  = put_short.strike  - net_credit
- breakeven_high = call_short.strike + net_credit
- POP ≈ 1 - |delta_put_short| - |delta_call_short|    [delta approximation]
- EV  = POP × max_profit - (1-POP) × max_loss
- RoR = max_profit / max_loss

Reuse from V2 shared infrastructure:
- Phase A:  narrow_chain() with same DTE/strike narrowing
- Phase C:  run_shared_structural_checks() for generic leg/expiry/width checks
- Phase D:  phase_d_quote_liquidity_sanity() for quote/OI/volume presence
- Phase D2: run_quote_sanity(), run_liquidity_sanity(), run_dedup()
- Phase E:  run_math_verification() for tolerance-based verification
- Phase F:  phase_f_normalize() for status/timestamps
"""

from __future__ import annotations

import logging
import math
from typing import Any

from app.services.scanner_v2.base_scanner import BaseV2Scanner
from app.services.scanner_v2.contracts import (
    V2Candidate,
    V2CheckResult,
    V2Leg,
    V2RecomputedMath,
)
from app.services.scanner_v2.data import V2NarrowedUniverse

_log = logging.getLogger("bentrade.scanner_v2.families.iron_condors")

# Construction safety cap — prevent combinatorial explosion.
_DEFAULT_GENERATION_CAP = 50_000

# Maximum wing width in dollars (structural bound, not desirability).
_DEFAULT_MAX_WING_WIDTH = 50.0

# Minimum wing width in dollars.
# Skips narrow wings (e.g. $1-wide SPY) that yield marginal credit.
_DEFAULT_MIN_WIDTH = 2.0

# Short-leg delta range — only generate sides whose short strike
# has abs(delta) within this band.
_DEFAULT_SHORT_DELTA_MIN = 0.05
_DEFAULT_SHORT_DELTA_MAX = 0.40

# Max ratio between put/call short deltas — prevents heavily skewed condors.
_DEFAULT_MAX_DELTA_RATIO = 2.5


class IronCondorsV2Scanner(BaseV2Scanner):
    """V2 scanner for iron condors.

    Builds condors from two credit spread sides using the same
    expiry bucket and trusted V2 narrowing infrastructure.  Construction
    reuses the spread-side pairing concept from the vertical spreads
    family rather than reimplementing bespoke candidate generation.
    """

    family_key = "iron_condors"
    scanner_version = "2.0.0"
    dte_min = 7
    dte_max = 60

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
        """Phase B — construct iron condor candidates from spread sides.

        Algorithm
        ---------
        For each expiry bucket in the narrowed universe:
        1. Separate OTM puts and OTM calls
        2. Build all valid put credit spread sides (short > long)
        3. Build all valid call credit spread sides (short < long)
        4. Cross-product into 4-leg condor candidates
        5. Set preliminary math from raw quotes

        The non-overlap constraint (put_short < call_short) is
        automatically satisfied because OTM puts < underlying < OTM calls.

        Parameters
        ----------
        context
            May contain ``generation_cap`` (int) and ``max_wing_width``
            (float) to override defaults.
        """
        if narrowed_universe is None or not narrowed_universe.expiry_buckets:
            return []

        spot = underlying_price or 0.0
        if spot <= 0:
            _log.warning("Iron condor %s: no underlying price — skipping", symbol)
            return []

        generation_cap = int(context.get("generation_cap", _DEFAULT_GENERATION_CAP))
        max_wing = float(context.get("max_wing_width", _DEFAULT_MAX_WING_WIDTH))
        min_width = float(context.get("min_width", _DEFAULT_MIN_WIDTH))
        short_delta_min = float(context.get("short_delta_min", _DEFAULT_SHORT_DELTA_MIN))
        short_delta_max = float(context.get("short_delta_max", _DEFAULT_SHORT_DELTA_MAX))
        max_delta_ratio = float(context.get("max_delta_ratio", _DEFAULT_MAX_DELTA_RATIO))

        # === TEMPORARY DIAGNOSTIC LOGGING (remove after debugging) ===
        import json as _diag_json
        from pathlib import Path as _DiagPath
        from datetime import datetime as _DiagDT
        _diag: dict[str, Any] = {
            "timestamp": _DiagDT.now().isoformat(),
            "scanner_key": scanner_key,
            "strategy_id": strategy_id,
            "symbol": symbol,
            "phase_a": {},
            "phase_b": {
                "per_expiry": [],
                "total_constructed": 0,
                "put_delta_none_skipped": 0,
                "put_delta_filter_skipped": 0,
                "call_delta_none_skipped": 0,
                "call_delta_filter_skipped": 0,
                "put_width_skipped": 0,
                "call_width_skipped": 0,
                "delta_balance_skipped": 0,
                "cap_hit": False,
            },
            "config": {
                "generation_cap": generation_cap,
                "max_wing": max_wing,
                "min_width": min_width,
                "short_delta_min": short_delta_min,
                "short_delta_max": short_delta_max,
                "max_delta_ratio": max_delta_ratio,
                "spot": spot,
            },
        }
        # === END DIAG INIT ===

        candidates: list[V2Candidate] = []
        seq = 0
        capped = False

        # Per-expiration side cap — ensures all expirations get fair representation.
        # With 10 exps: √(50000/10) = √5000 ≈ 70 sides per exp.
        num_expirations = len(narrowed_universe.expiry_buckets)
        per_exp_side_cap = max(30, int(math.isqrt(generation_cap // max(num_expirations, 1))))

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
            "per_exp_side_cap": per_exp_side_cap,
        }

        for exp in sorted(narrowed_universe.expiry_buckets.keys()):
            bucket = narrowed_universe.expiry_buckets[exp]
            if capped:
                break

            # === DIAG: per-expiry entry ===
            _diag_exp: dict[str, Any] = {
                "expiry": exp,
                "dte": getattr(bucket, 'dte', None),
                "put_delta_values": [],
                "call_delta_values": [],
                "put_delta_none_skipped": 0,
                "put_delta_filter_skipped": 0,
                "call_delta_none_skipped": 0,
                "call_delta_filter_skipped": 0,
                "put_width_skipped": 0,
                "call_width_skipped": 0,
                "delta_balance_skipped": 0,
            }

            # ── 1. Separate OTM puts and calls ─────────────────
            puts: list[tuple[float, Any]] = []
            calls: list[tuple[float, Any]] = []
            for entry in bucket.strikes:
                c = entry.contract
                if c.option_type == "put" and entry.strike < spot:
                    puts.append((entry.strike, c))
                elif c.option_type == "call" and entry.strike > spot:
                    calls.append((entry.strike, c))

            puts.sort(key=lambda x: x[0])    # ascending
            calls.sort(key=lambda x: x[0])   # ascending

            _diag_exp["otm_put_count"] = len(puts)
            _diag_exp["otm_call_count"] = len(calls)

            # Sample delta values from puts and calls
            for _idx, (_s, _c) in enumerate(puts[:10]):
                _dv = getattr(_c, 'delta', None)
                if _dv is None and hasattr(_c, 'greeks'):
                    _dv = (_c.greeks or {}).get('delta') if isinstance(getattr(_c, 'greeks', None), dict) else None
                _diag_exp["put_delta_values"].append({"strike": _s, "delta": _dv, "bid": getattr(_c, 'bid', None), "ask": getattr(_c, 'ask', None)})
            for _idx, (_s, _c) in enumerate(calls[:10]):
                _dv = getattr(_c, 'delta', None)
                if _dv is None and hasattr(_c, 'greeks'):
                    _dv = (_c.greeks or {}).get('delta') if isinstance(getattr(_c, 'greeks', None), dict) else None
                _diag_exp["call_delta_values"].append({"strike": _s, "delta": _dv, "bid": getattr(_c, 'bid', None), "ask": getattr(_c, 'ask', None)})

            if len(puts) < 2 or len(calls) < 2:
                _diag_exp["skipped_reason"] = f"insufficient_contracts (puts={len(puts)}, calls={len(calls)})"
                _diag["phase_b"]["per_expiry"].append(_diag_exp)
                continue

            # ── 2. Build put credit spread sides ───────────────
            # short_put > long_put  (credit: short closer to ATM)
            # Short put is the HIGHER strike (j), so delta-filter on j.
            put_sides: list[tuple[float, Any, float, Any]] = []
            for i in range(len(puts)):
                if len(put_sides) >= per_exp_side_cap:
                    break
                for j in range(i + 1, len(puts)):
                    lp_s, lp_c = puts[i]   # lower strike  = long put
                    sp_s, sp_c = puts[j]   # higher strike = short put
                    # Delta filter on the short put
                    if sp_c.delta is None:
                        _diag_exp["put_delta_none_skipped"] += 1
                        _diag["phase_b"]["put_delta_none_skipped"] += 1
                        continue
                    if not (short_delta_min <= abs(sp_c.delta) <= short_delta_max):
                        _diag_exp["put_delta_filter_skipped"] += 1
                        _diag["phase_b"]["put_delta_filter_skipped"] += 1
                        continue
                    if sp_s - lp_s < min_width:
                        _diag_exp["put_width_skipped"] += 1
                        _diag["phase_b"]["put_width_skipped"] += 1
                        continue
                    if sp_s - lp_s > max_wing:
                        continue
                    put_sides.append((sp_s, sp_c, lp_s, lp_c))
                    if len(put_sides) >= per_exp_side_cap:
                        break

            # ── 3. Build call credit spread sides ──────────────
            # short_call < long_call  (credit: short closer to ATM)
            # Short call is the LOWER strike (i), so delta-filter on outer loop.
            call_sides: list[tuple[float, Any, float, Any]] = []
            for i in range(len(calls)):
                if len(call_sides) >= per_exp_side_cap:
                    break
                sc_s, sc_c = calls[i]   # lower strike  = short call
                # Delta filter on the short call
                if sc_c.delta is None:
                    _diag_exp["call_delta_none_skipped"] += 1
                    _diag["phase_b"]["call_delta_none_skipped"] += 1
                    continue
                if not (short_delta_min <= abs(sc_c.delta) <= short_delta_max):
                    _diag_exp["call_delta_filter_skipped"] += 1
                    _diag["phase_b"]["call_delta_filter_skipped"] += 1
                    continue
                for j in range(i + 1, len(calls)):
                    lc_s, lc_c = calls[j]   # higher strike = long call
                    if lc_s - sc_s < min_width:
                        _diag_exp["call_width_skipped"] += 1
                        _diag["phase_b"]["call_width_skipped"] += 1
                        continue
                    if lc_s - sc_s > max_wing:
                        continue
                    call_sides.append((sc_s, sc_c, lc_s, lc_c))
                    if len(call_sides) >= per_exp_side_cap:
                        break

            _diag_exp["put_sides_constructed"] = len(put_sides)
            _diag_exp["call_sides_constructed"] = len(call_sides)
            _diag_exp["cross_product_possible"] = len(put_sides) * len(call_sides)

            # ── 4. Cross-product into condors ──────────────────
            _exp_constructed = 0
            for ps_s, ps_c, pl_s, pl_c in put_sides:
                if capped:
                    break
                put_short_delta = abs(ps_c.delta or 0)
                for cs_s, cs_c, cl_s, cl_c in call_sides:
                    # Delta balance check — skip heavily skewed condors
                    call_short_delta = abs(cs_c.delta or 0)
                    if put_short_delta < 0.01 or call_short_delta < 0.01:
                        _diag_exp["delta_balance_skipped"] += 1
                        _diag["phase_b"]["delta_balance_skipped"] += 1
                        continue
                    delta_ratio = (max(put_short_delta, call_short_delta)
                                   / min(put_short_delta, call_short_delta))
                    if delta_ratio > max_delta_ratio:
                        _diag_exp["delta_balance_skipped"] += 1
                        _diag["phase_b"]["delta_balance_skipped"] += 1
                        continue

                    cand = _build_condor_candidate(
                        symbol=symbol,
                        strategy_id=strategy_id,
                        scanner_key=scanner_key,
                        family_key=self.family_key,
                        underlying_price=underlying_price,
                        expiration=exp,
                        dte=bucket.dte,
                        short_put_strike=ps_s, short_put_contract=ps_c,
                        long_put_strike=pl_s, long_put_contract=pl_c,
                        short_call_strike=cs_s, short_call_contract=cs_c,
                        long_call_strike=cl_s, long_call_contract=cl_c,
                        seq=seq,
                    )
                    candidates.append(cand)
                    seq += 1
                    _exp_constructed += 1
                    _diag["phase_b"]["total_constructed"] += 1

                    if seq >= generation_cap:
                        capped = True
                        _diag["phase_b"]["cap_hit"] = True
                        _log.warning(
                            "Iron condor %s: hit generation cap (%d)",
                            symbol, generation_cap,
                        )
                        break

            _diag_exp["candidates_constructed"] = _exp_constructed
            _diag["phase_b"]["per_expiry"].append(_diag_exp)

        _log.info(
            "Iron condor %s: constructed %d candidates from %d expirations",
            symbol, len(candidates),
            len(narrowed_universe.expiry_buckets),
        )

        # === WRITE DIAGNOSTIC REPORT (TEMPORARY — remove after debugging) ===
        import os as _diag_os
        if not _diag_os.environ.get("PYTEST_CURRENT_TEST"):
            try:
                _diag_dir = _DiagPath("results/diagnostics")
                _diag_dir.mkdir(parents=True, exist_ok=True)
                _diag_file = _diag_dir / f"options_diag_iron_condor_{symbol}_{_DiagDT.now().strftime('%Y%m%d_%H%M%S')}.json"
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
        """Iron-condor-specific structural checks.

        Validates condor geometry beyond shared Phase C checks:
        1. Exactly 4 legs (2 puts + 2 calls)
        2. Strike ordering: put_long < put_short < call_short < call_long
        3. Per-side positive width

        Uses ``v2_ic_invalid_geometry`` for condor-specific geometry
        failures and ``v2_malformed_legs`` for leg count/type issues.
        """
        checks: list[V2CheckResult] = []

        # 1. Exactly 4 legs
        if len(candidate.legs) != 4:
            checks.append(V2CheckResult(
                "ic_leg_count", False,
                f"expected 4 legs, got {len(candidate.legs)}",
            ))
            if "v2_malformed_legs" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
            return checks
        checks.append(V2CheckResult("ic_leg_count", True, "4 legs"))

        # 2. 2 puts + 2 calls
        puts = sorted(
            [l for l in candidate.legs if l.option_type == "put"],
            key=lambda l: l.strike,
        )
        calls = sorted(
            [l for l in candidate.legs if l.option_type == "call"],
            key=lambda l: l.strike,
        )

        if len(puts) != 2 or len(calls) != 2:
            checks.append(V2CheckResult(
                "ic_put_call_balance", False,
                f"expected 2P+2C, got {len(puts)}P+{len(calls)}C",
            ))
            if "v2_malformed_legs" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
            return checks
        checks.append(V2CheckResult("ic_put_call_balance", True, "2P+2C"))

        # 3. Strike ordering: put_long < put_short < call_short < call_long
        pl, ps = puts[0], puts[1]    # put_long (lower), put_short (higher)
        cs, cl = calls[0], calls[1]  # call_short (lower), call_long (higher)

        ordering_ok = pl.strike < ps.strike < cs.strike < cl.strike
        if not ordering_ok:
            detail = (
                f"expected pl({pl.strike}) < ps({ps.strike}) "
                f"< cs({cs.strike}) < cl({cl.strike})"
            )
            checks.append(V2CheckResult("ic_strike_ordering", False, detail))
            if "v2_ic_invalid_geometry" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_ic_invalid_geometry")
        else:
            checks.append(V2CheckResult("ic_strike_ordering", True, ""))

        # 4. Per-side positive width
        put_width = ps.strike - pl.strike
        call_width = cl.strike - cs.strike
        if put_width <= 0 or call_width <= 0:
            checks.append(V2CheckResult(
                "ic_side_widths", False,
                f"put_width={put_width}, call_width={call_width}",
            ))
            if "v2_ic_invalid_geometry" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_ic_invalid_geometry")
        else:
            checks.append(V2CheckResult(
                "ic_side_widths", True,
                f"put_width={put_width}, call_width={call_width}",
            ))

        return checks

    # ── Phase E hook: family math ───────────────────────────────

    def family_math(
        self, candidate: V2Candidate,
    ) -> V2RecomputedMath:
        """Iron condor math recomputation.

        Computes all pricing from the 4 legs:
        - net_credit = put_side_credit + call_side_credit
        - max_profit = net_credit × 100
        - width = max(put_width, call_width)  [effective risk width]
        - max_loss = (width - net_credit) × 100
        - breakevens = [put_short - net_credit, call_short + net_credit]
        - POP via delta approximation
        - EV, RoR, Kelly derived from above
        """
        m = candidate.math
        notes: dict[str, str] = {}

        # ── Identify legs by type ──────────────────────────────
        puts = sorted(
            [l for l in candidate.legs if l.option_type == "put"],
            key=lambda l: l.strike,
        )
        calls = sorted(
            [l for l in candidate.legs if l.option_type == "call"],
            key=lambda l: l.strike,
        )

        if len(puts) != 2 or len(calls) != 2:
            notes["skipped"] = f"expected 2P+2C, got {len(puts)}P+{len(calls)}C"
            m.notes = notes
            return m

        put_long, put_short = puts[0], puts[1]
        call_short, call_long = calls[0], calls[1]

        # ── Per-side widths ────────────────────────────────────
        put_width = put_short.strike - put_long.strike
        call_width = call_long.strike - call_short.strike
        m.width = max(put_width, call_width)
        notes["width"] = (
            f"max(put_width={put_width}, call_width={call_width}) = {m.width}"
        )
        notes["put_width"] = str(put_width)
        notes["call_width"] = str(call_width)

        if m.width <= 0:
            m.notes = notes
            return m

        # ── Net credit ─────────────────────────────────────────
        # put_side_credit  = put_short.bid  - put_long.ask
        # call_side_credit = call_short.bid - call_long.ask
        if (put_short.bid is not None and put_long.ask is not None
                and call_short.bid is not None and call_long.ask is not None):
            put_side_credit = put_short.bid - put_long.ask
            call_side_credit = call_short.bid - call_long.ask
            net_credit = put_side_credit + call_side_credit

            notes["put_side_credit"] = (
                f"put_short.bid({put_short.bid}) - "
                f"put_long.ask({put_long.ask}) = {round(put_side_credit, 4)}"
            )
            notes["call_side_credit"] = (
                f"call_short.bid({call_short.bid}) - "
                f"call_long.ask({call_long.ask}) = {round(call_side_credit, 4)}"
            )

            if net_credit > 0:
                m.net_credit = round(net_credit, 4)
                m.max_profit = round(net_credit * 100, 2)
                m.max_loss = round((m.width - net_credit) * 100, 2)
                notes["net_credit"] = (
                    f"put_side({round(put_side_credit, 4)}) + "
                    f"call_side({round(call_side_credit, 4)}) = {m.net_credit}"
                )
            else:
                notes["pricing"] = (
                    f"net_credit={round(net_credit, 4)} <= 0 — not viable"
                )
                m.notes = notes
                return m
        else:
            notes["pricing"] = "missing bid/ask on one or more legs"
            m.notes = notes
            return m

        # ── Breakevens ─────────────────────────────────────────
        # breakeven_low  = put_short.strike  - net_credit
        # breakeven_high = call_short.strike + net_credit
        be_low = round(put_short.strike - m.net_credit, 2)
        be_high = round(call_short.strike + m.net_credit, 2)
        m.breakeven = [be_low, be_high]
        notes["breakeven"] = f"[{be_low}, {be_high}]"

        # ── POP — delta approximation ──────────────────────────
        # POP ≈ 1 - |delta_put_short| - |delta_call_short|
        # = P(put_short < S_T < call_short) approximately
        if put_short.delta is not None and call_short.delta is not None:
            pop = 1.0 - abs(put_short.delta) - abs(call_short.delta)
            m.pop = round(max(0.0, min(1.0, pop)), 4)
            m.pop_source = "delta_approx"
            notes["pop"] = (
                f"1 - |put_short.delta({put_short.delta})| - "
                f"|call_short.delta({call_short.delta})| = {m.pop}"
            )

        # ── EV ─────────────────────────────────────────────────
        if m.pop is not None and m.max_profit is not None and m.max_loss is not None:
            m.ev = round(
                m.pop * m.max_profit - (1.0 - m.pop) * m.max_loss, 2,
            )
            notes["ev"] = f"pop*max_profit - (1-pop)*max_loss = {m.ev}"
            if candidate.dte and candidate.dte > 0:
                m.ev_per_day = round(m.ev / candidate.dte, 4)

        # ── RoR ────────────────────────────────────────────────
        if m.max_profit is not None and m.max_loss is not None and m.max_loss > 0:
            m.ror = round(m.max_profit / m.max_loss, 4)
            notes["ror"] = f"max_profit / max_loss = {m.ror}"

        # ── Kelly ──────────────────────────────────────────────
        if m.pop is not None and m.ror is not None and m.ror > 0:
            q = 1.0 - m.pop
            kelly = m.pop - q / m.ror
            m.kelly = round(kelly, 4)

        m.notes = notes
        return m


# ═══════════════════════════════════════════════════════════════════
#  Construction helper
# ═══════════════════════════════════════════════════════════════════

def _build_condor_candidate(
    *,
    symbol: str,
    strategy_id: str,
    scanner_key: str,
    family_key: str,
    underlying_price: float | None,
    expiration: str,
    dte: int,
    short_put_strike: float,
    short_put_contract: Any,
    long_put_strike: float,
    long_put_contract: Any,
    short_call_strike: float,
    short_call_contract: Any,
    long_call_strike: float,
    long_call_contract: Any,
    seq: int,
) -> V2Candidate:
    """Build a single iron condor V2Candidate from 4 leg contracts.

    Leg ordering convention (stable — do not reorder):
    - legs[0]: short put  (index=0, side="short", option_type="put")
    - legs[1]: long put   (index=1, side="long",  option_type="put")
    - legs[2]: short call (index=2, side="short", option_type="call")
    - legs[3]: long call  (index=3, side="long",  option_type="call")

    Preliminary math
    ----------------
    Sets width and net_credit from raw quotes for Phase B traceability.
    Phase E (family_math) recomputes all fields from leg data.
    """
    def _leg(index: int, side: str, strike: float, contract: Any,
             option_type: str) -> V2Leg:
        return V2Leg(
            index=index,
            side=side,
            strike=strike,
            option_type=option_type,
            expiration=expiration,
            bid=contract.bid,
            ask=contract.ask,
            mid=contract.mid,
            delta=contract.delta,
            gamma=contract.gamma,
            theta=contract.theta,
            vega=contract.vega,
            iv=contract.iv,
            open_interest=contract.open_interest,
            volume=contract.volume,
        )

    legs = [
        _leg(0, "short", short_put_strike, short_put_contract, "put"),
        _leg(1, "long", long_put_strike, long_put_contract, "put"),
        _leg(2, "short", short_call_strike, short_call_contract, "call"),
        _leg(3, "long", long_call_strike, long_call_contract, "call"),
    ]

    # Preliminary math — Phase E recomputes everything.
    put_width = short_put_strike - long_put_strike
    call_width = long_call_strike - short_call_strike
    width = max(put_width, call_width)

    math = V2RecomputedMath(width=width)

    if (short_put_contract.bid is not None and long_put_contract.ask is not None
            and short_call_contract.bid is not None
            and long_call_contract.ask is not None):
        put_side_cr = short_put_contract.bid - long_put_contract.ask
        call_side_cr = short_call_contract.bid - long_call_contract.ask
        credit = put_side_cr + call_side_cr
        if credit > 0:
            math.net_credit = round(credit, 4)

    candidate_id = (
        f"{symbol}|{strategy_id}|{expiration}"
        f"|P{short_put_strike}/{long_put_strike}"
        f"|C{short_call_strike}/{long_call_strike}|{seq}"
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
        legs=legs,
        math=math,
    )
