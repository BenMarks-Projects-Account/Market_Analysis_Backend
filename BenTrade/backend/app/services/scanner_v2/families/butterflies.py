"""Options Scanner V2 — Butterflies family.

Handles:
- butterfly_debit  (3-leg: all calls or all puts)
- iron_butterfly   (4-leg: 2 puts + 2 calls, center straddle)

Supported variants
──────────────────
**Debit butterfly (3 legs)** — strategy_id ``butterfly_debit``
    Buy 1× lower wing, sell 2× body, buy 1× upper wing.
    All same option_type (call or put).
    Generates both call and put variants (context ``option_side`` filters).

    Geometry: lower < center < upper, center = (lower + upper) / 2.
    Width = center − lower = upper − center (symmetric).

    Pricing: net_debit = ask(lower) + ask(upper) − 2×bid(center)
    max_profit = (width − net_debit) × 100
    max_loss = net_debit × 100
    breakevens = [lower + debit, upper − debit]

**Iron butterfly (4 legs)** — strategy_id ``iron_butterfly``
    Short 1× put at body, short 1× call at body (center straddle),
    long 1× put at lower wing, long 1× call at upper wing.
    Symmetric: center − lower = upper − center.

    Pricing: net_credit = bid(ps) + bid(cs) − ask(pl) − ask(cl)
    max_profit = net_credit × 100
    max_loss = (width − net_credit) × 100
    breakevens = [center − credit, center + credit]

Construction logic (Phase B)
────────────────────────────
Debit butterfly:
    For each expiry bucket, for each option_type (call/put):
        1. Collect all strikes of that type
        2. Enumerate symmetric triplets: for each pair (lower, upper),
           check if midpoint center exists as a strike
        3. Filter by max_wing_width
        4. Build 3-leg candidate with preliminary math

Iron butterfly:
    For each expiry bucket:
        1. Build put_map and call_map from strikes
        2. Center candidates = strikes with both put AND call
        3. For each center, enumerate equidistant wings:
           lower = center − width (must exist in put_map)
           upper = center + width (must exist in call_map)
        4. Build 4-leg candidate with preliminary math

Reuse from V2 shared infrastructure:
- Phase A:  narrow_chain() with same DTE/strike narrowing
- Phase C:  run_shared_structural_checks() for generic leg/expiry/width checks
- Phase D:  phase_d_quote_liquidity_sanity() for quote/OI/volume presence
- Phase D2: run_quote_sanity(), run_liquidity_sanity(), run_dedup()
- Phase E:  run_math_verification() for tolerance-based verification
- Phase F:  phase_f_normalize() for status/timestamps

No hidden threshold multipliers — V2 uses shared hygiene (Phase D/D2)
without any butterfly-specific relaxation of OI/volume thresholds.
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

_log = logging.getLogger("bentrade.scanner_v2.families.butterflies")

# Construction safety cap — prevent combinatorial explosion.
_DEFAULT_GENERATION_CAP = 50_000

# Maximum wing width in dollars (structural bound, not desirability).
_DEFAULT_MAX_WING_WIDTH = 50.0


class ButterfliesV2Scanner(BaseV2Scanner):
    """V2 scanner for butterfly families.

    Handles both debit butterflies (3-leg, same option_type) and
    iron butterflies (4-leg, center straddle with wings).

    Dispatches on ``strategy_id`` for construction, and on leg count
    (3 vs 4) for structural checks and math.
    """

    family_key = "butterflies"
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
        """Phase B — construct butterfly candidates.

        Dispatches to debit or iron construction based on strategy_id.
        """
        if narrowed_universe is None or not narrowed_universe.expiry_buckets:
            return []

        spot = underlying_price or 0.0
        if spot <= 0:
            _log.warning("Butterfly %s: no underlying price — skipping", symbol)
            return []

        generation_cap = int(context.get("generation_cap", _DEFAULT_GENERATION_CAP))
        max_wing = float(context.get("max_wing_width", _DEFAULT_MAX_WING_WIDTH))

        if strategy_id == "iron_butterfly":
            return self._construct_iron_butterflies(
                symbol=symbol, strategy_id=strategy_id,
                scanner_key=scanner_key, spot=spot,
                narrowed_universe=narrowed_universe,
                generation_cap=generation_cap, max_wing=max_wing,
            )
        else:
            option_side = context.get("option_side")
            return self._construct_debit_butterflies(
                symbol=symbol, strategy_id=strategy_id,
                scanner_key=scanner_key, spot=spot,
                narrowed_universe=narrowed_universe,
                generation_cap=generation_cap, max_wing=max_wing,
                option_side=option_side,
            )

    # ── Debit butterfly construction ───────────────────────────

    def _construct_debit_butterflies(
        self,
        *,
        symbol: str,
        strategy_id: str,
        scanner_key: str,
        spot: float,
        narrowed_universe: V2NarrowedUniverse,
        generation_cap: int,
        max_wing: float,
        option_side: str | None,
    ) -> list[V2Candidate]:
        """Build debit butterfly candidates (3-leg, same option_type).

        For each expiry bucket, enumerates symmetric triplets where
        center = (lower + upper) / 2 and all three strikes have contracts.
        """
        option_sides = ["call", "put"]
        if option_side in ("call", "put"):
            option_sides = [option_side]

        candidates: list[V2Candidate] = []
        seq = 0
        capped = False

        for exp, bucket in narrowed_universe.expiry_buckets.items():
            if capped:
                break

            for opt_type in option_sides:
                if capped:
                    break

                # Build strike → contract map for this option type
                strike_map: dict[float, Any] = {}
                for entry in bucket.strikes:
                    if entry.contract.option_type == opt_type:
                        strike_map[entry.strike] = entry.contract

                strikes = sorted(strike_map.keys())
                strike_set = set(strikes)

                if len(strikes) < 3:
                    continue

                # Enumerate symmetric triplets: (lower, center, upper)
                for i in range(len(strikes)):
                    if capped:
                        break
                    for k in range(i + 2, len(strikes)):
                        center_needed = (strikes[i] + strikes[k]) / 2
                        if center_needed not in strike_set:
                            continue

                        width = center_needed - strikes[i]
                        if width > max_wing:
                            continue

                        cand = _build_debit_butterfly_candidate(
                            symbol=symbol,
                            strategy_id=strategy_id,
                            scanner_key=scanner_key,
                            family_key=self.family_key,
                            underlying_price=spot,
                            expiration=exp,
                            dte=bucket.dte,
                            option_type=opt_type,
                            lower_strike=strikes[i],
                            lower_contract=strike_map[strikes[i]],
                            center_strike=center_needed,
                            center_contract=strike_map[center_needed],
                            upper_strike=strikes[k],
                            upper_contract=strike_map[strikes[k]],
                            seq=seq,
                        )
                        candidates.append(cand)
                        seq += 1

                        if seq >= generation_cap:
                            capped = True
                            _log.warning(
                                "Debit butterfly %s: hit generation cap (%d)",
                                symbol, generation_cap,
                            )
                            break

        _log.info(
            "Debit butterfly %s: constructed %d candidates",
            symbol, len(candidates),
        )
        return candidates

    # ── Iron butterfly construction ────────────────────────────

    def _construct_iron_butterflies(
        self,
        *,
        symbol: str,
        strategy_id: str,
        scanner_key: str,
        spot: float,
        narrowed_universe: V2NarrowedUniverse,
        generation_cap: int,
        max_wing: float,
    ) -> list[V2Candidate]:
        """Build iron butterfly candidates (4-leg, center straddle).

        Center strike must have both put and call contracts.
        Wings are equidistant: lower put at center−width, upper call
        at center+width.
        """
        candidates: list[V2Candidate] = []
        seq = 0
        capped = False

        for exp, bucket in narrowed_universe.expiry_buckets.items():
            if capped:
                break

            # Separate put and call strike maps
            put_map: dict[float, Any] = {}
            call_map: dict[float, Any] = {}
            for entry in bucket.strikes:
                c = entry.contract
                if c.option_type == "put":
                    put_map[entry.strike] = c
                elif c.option_type == "call":
                    call_map[entry.strike] = c

            # Center must have both put and call
            center_strikes = sorted(
                set(put_map.keys()) & set(call_map.keys()),
            )

            for center in center_strikes:
                if capped:
                    break

                # Enumerate equidistant wings from available strikes
                lower_puts = sorted(
                    s for s in put_map if s < center
                )

                for lp_strike in lower_puts:
                    width = center - lp_strike
                    upper_needed = center + width

                    if width > max_wing:
                        continue
                    if upper_needed not in call_map:
                        continue

                    cand = _build_iron_butterfly_candidate(
                        symbol=symbol,
                        strategy_id=strategy_id,
                        scanner_key=scanner_key,
                        family_key=self.family_key,
                        underlying_price=spot,
                        expiration=exp,
                        dte=bucket.dte,
                        center_strike=center,
                        lower_strike=lp_strike,
                        upper_strike=upper_needed,
                        center_put_contract=put_map[center],
                        center_call_contract=call_map[center],
                        lower_put_contract=put_map[lp_strike],
                        upper_call_contract=call_map[upper_needed],
                        seq=seq,
                    )
                    candidates.append(cand)
                    seq += 1

                    if seq >= generation_cap:
                        capped = True
                        _log.warning(
                            "Iron butterfly %s: hit generation cap (%d)",
                            symbol, generation_cap,
                        )
                        break

        _log.info(
            "Iron butterfly %s: constructed %d candidates",
            symbol, len(candidates),
        )
        return candidates

    # ── Phase C hook: family structural checks ──────────────────

    def family_structural_checks(
        self, candidate: V2Candidate,
    ) -> list[V2CheckResult]:
        """Butterfly-specific structural checks.

        Dispatches to debit (3-leg) or iron (4-leg) checks.
        """
        checks: list[V2CheckResult] = []

        n = len(candidate.legs)
        if n not in (3, 4):
            checks.append(V2CheckResult(
                "bf_leg_count", False,
                f"expected 3 or 4 legs, got {n}",
            ))
            if "v2_malformed_legs" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_malformed_legs")
            return checks
        checks.append(V2CheckResult("bf_leg_count", True, f"{n} legs"))

        if n == 3:
            checks.extend(self._debit_bf_checks(candidate))
        else:
            checks.extend(self._iron_bf_checks(candidate))

        return checks

    # ── Debit butterfly structural checks ──────────────────────

    def _debit_bf_checks(
        self, candidate: V2Candidate,
    ) -> list[V2CheckResult]:
        """Structural checks for 3-leg debit butterfly."""
        checks: list[V2CheckResult] = []

        # All same option_type
        types = {l.option_type for l in candidate.legs}
        if len(types) > 1:
            checks.append(V2CheckResult(
                "bf_option_type", False,
                f"mixed types: {types}",
            ))
            if "v2_bf_invalid_geometry" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_bf_invalid_geometry")
            return checks
        checks.append(V2CheckResult(
            "bf_option_type", True, f"all {next(iter(types))}",
        ))

        # 2 long + 1 short (wings long, center short)
        longs = [l for l in candidate.legs if l.side == "long"]
        shorts = [l for l in candidate.legs if l.side == "short"]
        if len(longs) != 2 or len(shorts) != 1:
            checks.append(V2CheckResult(
                "bf_side_balance", False,
                f"expected 2L+1S, got {len(longs)}L+{len(shorts)}S",
            ))
            if "v2_bf_invalid_geometry" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_bf_invalid_geometry")
            return checks
        checks.append(V2CheckResult("bf_side_balance", True, "2L+1S"))

        # Center must be the short leg (middle strike)
        legs_sorted = sorted(candidate.legs, key=lambda l: l.strike)
        lower, center, upper = legs_sorted

        if center.side != "short":
            checks.append(V2CheckResult(
                "bf_center_is_short", False,
                f"center at {center.strike} is {center.side}, expected short",
            ))
            if "v2_bf_invalid_geometry" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_bf_invalid_geometry")
        else:
            checks.append(V2CheckResult(
                "bf_center_is_short", True,
                f"center={center.strike}",
            ))

        # Symmetric wings: center = (lower + upper) / 2
        expected_center = (lower.strike + upper.strike) / 2
        delta = abs(expected_center - center.strike)
        if delta > 0.01:
            checks.append(V2CheckResult(
                "bf_symmetry", False,
                f"center {center.strike} != midpoint {expected_center}",
            ))
            if "v2_bf_invalid_geometry" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_bf_invalid_geometry")
        else:
            width = center.strike - lower.strike
            checks.append(V2CheckResult(
                "bf_symmetry", True,
                f"width={width}",
            ))

        return checks

    # ── Iron butterfly structural checks ───────────────────────

    def _iron_bf_checks(
        self, candidate: V2Candidate,
    ) -> list[V2CheckResult]:
        """Structural checks for 4-leg iron butterfly."""
        checks: list[V2CheckResult] = []

        # 2 puts + 2 calls
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
                "bf_type_balance", False,
                f"expected 2P+2C, got {len(puts)}P+{len(calls)}C",
            ))
            if "v2_bf_invalid_geometry" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_bf_invalid_geometry")
            return checks
        checks.append(V2CheckResult("bf_type_balance", True, "2P+2C"))

        put_long, put_short = puts[0], puts[1]
        call_short, call_long = calls[0], calls[1]

        # Center: short put and short call at same strike
        if put_short.side != "short" or call_short.side != "short":
            checks.append(V2CheckResult(
                "bf_center_sides", False,
                f"center legs must be short (ps={put_short.side}, cs={call_short.side})",
            ))
            if "v2_bf_invalid_geometry" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_bf_invalid_geometry")
            return checks

        if put_long.side != "long" or call_long.side != "long":
            checks.append(V2CheckResult(
                "bf_wing_sides", False,
                f"wing legs must be long (pl={put_long.side}, cl={call_long.side})",
            ))
            if "v2_bf_invalid_geometry" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_bf_invalid_geometry")
            return checks

        checks.append(V2CheckResult(
            "bf_sides", True, "correct short/long assignment",
        ))

        # Center match: put_short.strike == call_short.strike
        if abs(put_short.strike - call_short.strike) > 0.01:
            checks.append(V2CheckResult(
                "bf_center_match", False,
                f"put_short({put_short.strike}) != call_short({call_short.strike})",
            ))
            if "v2_bf_invalid_geometry" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_bf_invalid_geometry")
        else:
            checks.append(V2CheckResult(
                "bf_center_match", True,
                f"center={put_short.strike}",
            ))

        # Symmetric wings
        center = put_short.strike
        put_width = center - put_long.strike
        call_width = call_long.strike - center
        if abs(put_width - call_width) > 0.01:
            checks.append(V2CheckResult(
                "bf_symmetry", False,
                f"put_width={put_width} != call_width={call_width}",
            ))
            if "v2_bf_invalid_geometry" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_bf_invalid_geometry")
        else:
            checks.append(V2CheckResult(
                "bf_symmetry", True,
                f"width={put_width}",
            ))

        # Strike ordering: put_long < put_short <= call_short < call_long
        if not (put_long.strike < put_short.strike
                and call_short.strike < call_long.strike):
            checks.append(V2CheckResult(
                "bf_strike_ordering", False,
                f"{put_long.strike} < {put_short.strike} ≤ "
                f"{call_short.strike} < {call_long.strike}",
            ))
            if "v2_bf_invalid_geometry" not in candidate.diagnostics.reject_reasons:
                candidate.diagnostics.reject_reasons.append("v2_bf_invalid_geometry")
        else:
            checks.append(V2CheckResult("bf_strike_ordering", True, ""))

        return checks

    # ── Phase E hook: family math ───────────────────────────────

    def family_math(
        self, candidate: V2Candidate,
    ) -> V2RecomputedMath:
        """Butterfly-specific math, dispatched by leg count."""
        if len(candidate.legs) == 3:
            return self._debit_butterfly_math(candidate)
        elif len(candidate.legs) == 4:
            return self._iron_butterfly_math(candidate)
        else:
            m = candidate.math
            m.notes = {"skipped": f"unexpected leg count {len(candidate.legs)}"}
            return m

    # ── Debit butterfly math ───────────────────────────────────

    def _debit_butterfly_math(
        self, candidate: V2Candidate,
    ) -> V2RecomputedMath:
        """Debit butterfly math (3-leg, same option_type).

        Pricing: net_debit = ask(lower) + ask(upper) − 2×bid(center)
        The center leg is 2× short in a debit butterfly.

        max_profit = (width − net_debit) × 100
        max_loss   = net_debit × 100
        breakevens = [lower + debit, upper − debit]
        POP        ≈ |Δ_lower| − |Δ_upper| (delta approximation)
        """
        m = candidate.math
        notes: dict[str, str] = {}

        legs_sorted = sorted(candidate.legs, key=lambda l: l.strike)
        if len(legs_sorted) != 3:
            notes["skipped"] = f"expected 3 legs, got {len(legs_sorted)}"
            m.notes = notes
            return m

        lower, center, upper = legs_sorted

        # Width
        width = center.strike - lower.strike
        m.width = width
        notes["width"] = (
            f"center({center.strike}) - lower({lower.strike}) = {width}"
        )

        if width <= 0:
            m.notes = notes
            return m

        # Net debit = ask(lower) + ask(upper) - 2 × bid(center)
        # (buying 1× lower + 1× upper, selling 2× center body)
        if (lower.ask is not None and upper.ask is not None
                and center.bid is not None):
            net_debit = lower.ask + upper.ask - 2 * center.bid

            notes["pricing_formula"] = (
                f"ask(lower={lower.ask}) + ask(upper={upper.ask})"
                f" - 2×bid(center={center.bid}) = {round(net_debit, 4)}"
            )

            if net_debit > 0 and net_debit < width:
                m.net_debit = round(net_debit, 4)
                m.max_loss = round(net_debit * 100, 2)
                m.max_profit = round((width - net_debit) * 100, 2)
                notes["net_debit"] = str(m.net_debit)
                notes["max_profit"] = (
                    f"(width({width}) - debit({m.net_debit})) × 100"
                    f" = {m.max_profit}"
                )
                notes["max_loss"] = (
                    f"debit({m.net_debit}) × 100 = {m.max_loss}"
                )
            else:
                notes["pricing"] = (
                    f"net_debit={round(net_debit, 4)} — not viable"
                    f" (must be 0 < debit < width={width})"
                )
                m.notes = notes
                return m
        else:
            notes["pricing"] = "missing bid/ask on one or more legs"
            m.notes = notes
            return m

        # Breakevens
        be_low = round(lower.strike + m.net_debit, 2)
        be_high = round(upper.strike - m.net_debit, 2)
        m.breakeven = [be_low, be_high]
        notes["breakeven"] = f"[{be_low}, {be_high}]"

        # POP — delta approximation
        # P(lower < S_T < upper) ≈ |Δ_lower| − |Δ_upper| (calls)
        #                        ≈ |Δ_upper| − |Δ_lower| (puts)
        # Overestimates: covers full strike range, not just profit zone.
        if lower.delta is not None and upper.delta is not None:
            if lower.option_type == "call":
                pop = abs(lower.delta) - abs(upper.delta)
            else:
                pop = abs(upper.delta) - abs(lower.delta)
            m.pop = round(max(0.0, min(1.0, pop)), 4)
            m.pop_source = "delta_approx"
            notes["pop"] = (
                f"|Δ_lower({lower.delta})| − |Δ_upper({upper.delta})|"
                f" = {m.pop} (full strike range, overestimates profit zone)"
            )

        # EV
        if (m.pop is not None and m.max_profit is not None
                and m.max_loss is not None):
            m.ev = round(
                m.pop * m.max_profit - (1.0 - m.pop) * m.max_loss, 2,
            )
            notes["ev"] = f"pop*max_profit - (1-pop)*max_loss = {m.ev}"
            if candidate.dte and candidate.dte > 0:
                m.ev_per_day = round(m.ev / candidate.dte, 4)

        # RoR
        if (m.max_profit is not None and m.max_loss is not None
                and m.max_loss > 0):
            m.ror = round(m.max_profit / m.max_loss, 4)
            notes["ror"] = f"max_profit / max_loss = {m.ror}"

        # Kelly
        if m.pop is not None and m.ror is not None and m.ror > 0:
            q = 1.0 - m.pop
            m.kelly = round(m.pop - q / m.ror, 4)

        m.notes = notes
        return m

    # ── Iron butterfly math ────────────────────────────────────

    def _iron_butterfly_math(
        self, candidate: V2Candidate,
    ) -> V2RecomputedMath:
        """Iron butterfly math (4-leg, center straddle).

        Pricing: net_credit = bid(ps) + bid(cs) − ask(pl) − ask(cl)

        max_profit = net_credit × 100
        max_loss   = (width − net_credit) × 100
        breakevens = [center − credit, center + credit]
        POP        ≈ 1 − |Δ_ps| − |Δ_cs| (delta approximation)
        """
        m = candidate.math
        notes: dict[str, str] = {}

        puts = sorted(
            [l for l in candidate.legs if l.option_type == "put"],
            key=lambda l: l.strike,
        )
        calls = sorted(
            [l for l in candidate.legs if l.option_type == "call"],
            key=lambda l: l.strike,
        )

        if len(puts) != 2 or len(calls) != 2:
            notes["skipped"] = (
                f"expected 2P+2C, got {len(puts)}P+{len(calls)}C"
            )
            m.notes = notes
            return m

        put_long, put_short = puts[0], puts[1]
        call_short, call_long = calls[0], calls[1]

        # Width (symmetric: put_width should equal call_width)
        put_width = put_short.strike - put_long.strike
        call_width = call_long.strike - call_short.strike
        m.width = max(put_width, call_width)
        notes["width"] = (
            f"max(put_width={put_width}, call_width={call_width})"
            f" = {m.width}"
        )

        if m.width <= 0:
            m.notes = notes
            return m

        # Net credit = bid(ps) + bid(cs) - ask(pl) - ask(cl)
        if (put_short.bid is not None and call_short.bid is not None
                and put_long.ask is not None and call_long.ask is not None):
            net_credit = (
                put_short.bid + call_short.bid
                - put_long.ask - call_long.ask
            )
            notes["pricing_formula"] = (
                f"bid(ps={put_short.bid}) + bid(cs={call_short.bid})"
                f" - ask(pl={put_long.ask}) - ask(cl={call_long.ask})"
                f" = {round(net_credit, 4)}"
            )

            if net_credit > 0:
                m.net_credit = round(net_credit, 4)
                m.max_profit = round(net_credit * 100, 2)
                m.max_loss = round((m.width - net_credit) * 100, 2)
                notes["net_credit"] = str(m.net_credit)
                notes["max_profit"] = (
                    f"credit × 100 = {m.max_profit}"
                )
                notes["max_loss"] = (
                    f"(width({m.width}) - credit({m.net_credit})) × 100"
                    f" = {m.max_loss}"
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

        # Breakevens: center ± credit
        center = put_short.strike
        be_low = round(center - m.net_credit, 2)
        be_high = round(center + m.net_credit, 2)
        m.breakeven = [be_low, be_high]
        notes["breakeven"] = (
            f"center({center}) ± credit({m.net_credit})"
            f" = [{be_low}, {be_high}]"
        )

        # POP — delta approximation (same formula as iron condor)
        # POP ≈ 1 − |Δ_short_put| − |Δ_short_call|
        if put_short.delta is not None and call_short.delta is not None:
            pop = 1.0 - abs(put_short.delta) - abs(call_short.delta)
            m.pop = round(max(0.0, min(1.0, pop)), 4)
            m.pop_source = "delta_approx"
            notes["pop"] = (
                f"1 - |Δ_ps({put_short.delta})| - |Δ_cs({call_short.delta})|"
                f" = {m.pop}"
            )

        # EV
        if (m.pop is not None and m.max_profit is not None
                and m.max_loss is not None):
            m.ev = round(
                m.pop * m.max_profit - (1.0 - m.pop) * m.max_loss, 2,
            )
            notes["ev"] = f"pop*max_profit - (1-pop)*max_loss = {m.ev}"
            if candidate.dte and candidate.dte > 0:
                m.ev_per_day = round(m.ev / candidate.dte, 4)

        # RoR
        if (m.max_profit is not None and m.max_loss is not None
                and m.max_loss > 0):
            m.ror = round(m.max_profit / m.max_loss, 4)
            notes["ror"] = f"max_profit / max_loss = {m.ror}"

        # Kelly
        if m.pop is not None and m.ror is not None and m.ror > 0:
            q = 1.0 - m.pop
            m.kelly = round(m.pop - q / m.ror, 4)

        m.notes = notes
        return m


# ═══════════════════════════════════════════════════════════════════
#  Construction helpers
# ═══════════════════════════════════════════════════════════════════

def _build_debit_butterfly_candidate(
    *,
    symbol: str,
    strategy_id: str,
    scanner_key: str,
    family_key: str,
    underlying_price: float,
    expiration: str,
    dte: int,
    option_type: str,
    lower_strike: float,
    lower_contract: Any,
    center_strike: float,
    center_contract: Any,
    upper_strike: float,
    upper_contract: Any,
    seq: int,
) -> V2Candidate:
    """Build a 3-leg debit butterfly V2Candidate.

    Leg ordering (stable — do not reorder):
    - legs[0]: long lower wing  (index=0, side="long")
    - legs[1]: short center     (index=1, side="short", qty=2 implicit)
    - legs[2]: long upper wing  (index=2, side="long")

    All three legs share the same option_type.
    """
    def _leg(index: int, side: str, strike: float, contract: Any) -> V2Leg:
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
            gamma=getattr(contract, "gamma", None),
            theta=getattr(contract, "theta", None),
            vega=getattr(contract, "vega", None),
            iv=getattr(contract, "iv", None),
            open_interest=contract.open_interest,
            volume=contract.volume,
        )

    legs = [
        _leg(0, "long", lower_strike, lower_contract),
        _leg(1, "short", center_strike, center_contract),
        _leg(2, "long", upper_strike, upper_contract),
    ]

    # Preliminary math
    width = center_strike - lower_strike
    math = V2RecomputedMath(width=width)

    if (lower_contract.ask is not None and upper_contract.ask is not None
            and center_contract.bid is not None):
        debit = (lower_contract.ask + upper_contract.ask
                 - 2 * center_contract.bid)
        if 0 < debit < width:
            math.net_debit = round(debit, 4)

    candidate_id = (
        f"{symbol}|{strategy_id}|{expiration}"
        f"|{option_type}|{lower_strike}/{center_strike}/{upper_strike}"
        f"|{seq}"
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


def _build_iron_butterfly_candidate(
    *,
    symbol: str,
    strategy_id: str,
    scanner_key: str,
    family_key: str,
    underlying_price: float,
    expiration: str,
    dte: int,
    center_strike: float,
    lower_strike: float,
    upper_strike: float,
    center_put_contract: Any,
    center_call_contract: Any,
    lower_put_contract: Any,
    upper_call_contract: Any,
    seq: int,
) -> V2Candidate:
    """Build a 4-leg iron butterfly V2Candidate.

    Leg ordering (stable — do not reorder):
    - legs[0]: long put  lower wing  (index=0, side="long",  type="put")
    - legs[1]: short put center      (index=1, side="short", type="put")
    - legs[2]: short call center     (index=2, side="short", type="call")
    - legs[3]: long call upper wing  (index=3, side="long",  type="call")
    """
    def _leg(index: int, side: str, strike: float, contract: Any,
             opt_type: str) -> V2Leg:
        return V2Leg(
            index=index,
            side=side,
            strike=strike,
            option_type=opt_type,
            expiration=expiration,
            bid=contract.bid,
            ask=contract.ask,
            mid=contract.mid,
            delta=contract.delta,
            gamma=getattr(contract, "gamma", None),
            theta=getattr(contract, "theta", None),
            vega=getattr(contract, "vega", None),
            iv=getattr(contract, "iv", None),
            open_interest=contract.open_interest,
            volume=contract.volume,
        )

    legs = [
        _leg(0, "long", lower_strike, lower_put_contract, "put"),
        _leg(1, "short", center_strike, center_put_contract, "put"),
        _leg(2, "short", center_strike, center_call_contract, "call"),
        _leg(3, "long", upper_strike, upper_call_contract, "call"),
    ]

    # Preliminary math
    width = center_strike - lower_strike
    math = V2RecomputedMath(width=width)

    if (center_put_contract.bid is not None
            and center_call_contract.bid is not None
            and lower_put_contract.ask is not None
            and upper_call_contract.ask is not None):
        credit = (center_put_contract.bid + center_call_contract.bid
                  - lower_put_contract.ask - upper_call_contract.ask)
        if credit > 0:
            math.net_credit = round(credit, 4)

    candidate_id = (
        f"{symbol}|{strategy_id}|{expiration}"
        f"|{lower_strike}/{center_strike}/{upper_strike}|{seq}"
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
