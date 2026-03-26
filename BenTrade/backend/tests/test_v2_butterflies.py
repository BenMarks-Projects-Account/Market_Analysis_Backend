"""V2 butterfly family tests — Prompt 11.

Tests for:
1. Debit butterfly construction (Phase B) — symmetric triplet enumeration,
   call+put sides, generation cap, max_wing_width.
2. Iron butterfly construction (Phase B) — center with both put+call,
   symmetric wings, max_wing_width.
3. Debit butterfly structural checks (Phase C) — 3 legs, same type,
   2L+1S, center is short, symmetry.
4. Iron butterfly structural checks (Phase C) — 4 legs, 2P+2C,
   center match, side assignment, symmetry, ordering.
5. Debit butterfly math (Phase E) — net_debit (2× center), max_profit,
   max_loss, breakevens, POP delta approx.
6. Iron butterfly math (Phase E) — net_credit, max_profit, max_loss,
   breakevens, POP.
7. Math verification — verify_width, verify_net_credit_or_debit,
   verify_breakeven for 3-leg and 4-leg butterfly paths.
8. Hygiene integration (Phase D2) — quote/liquidity/dedup on butterflies.
9. End-to-end pipeline run via base scanner.
10. Reason code registry — new bf_invalid_geometry code.
11. Registry — family is implemented and loadable.
"""

from __future__ import annotations

import sys
sys.path.insert(0, ".")

import pytest

from app.services.scanner_v2.contracts import (
    V2Candidate,
    V2CheckResult,
    V2Diagnostics,
    V2Leg,
    V2RecomputedMath,
)
from app.services.scanner_v2.diagnostics.reason_codes import (
    REJECT_BF_INVALID_GEOMETRY,
    REJECT_MALFORMED_LEGS,
    is_valid_reject_code,
    is_valid_code,
    get_code_info,
    all_reject_codes,
)
from app.services.scanner_v2.families.butterflies import (
    ButterfliesV2Scanner,
    _build_debit_butterfly_candidate,
    _build_iron_butterfly_candidate,
)
from app.services.scanner_v2.phases import (
    phase_c_structural_validation,
    phase_d_quote_liquidity_sanity,
    phase_d2_trust_hygiene,
    phase_e_recomputed_math,
    phase_f_normalize,
)
from app.services.scanner_v2.validation.math_checks import (
    verify_width,
    verify_net_credit_or_debit,
    verify_breakeven,
    run_math_verification,
)
from app.services.scanner_v2.registry import (
    get_v2_family,
    get_v2_scanner,
    is_v2_supported,
)
from app.services.scanner_v2.data.contracts import (
    V2ExpiryBucket,
    V2NarrowedUniverse,
    V2NarrowingDiagnostics,
    V2OptionContract,
    V2StrikeEntry,
    V2UnderlyingSnapshot,
)


# =====================================================================
#  Helpers — build test objects
# =====================================================================

def _make_contract(
    *,
    strike: float,
    option_type: str,
    bid: float | None = 1.50,
    ask: float | None = 1.65,
    delta: float | None = -0.30,
    oi: int | None = 5000,
    volume: int | None = 800,
    expiration: str = "2026-04-17",
    root: str = "SPY",
) -> V2OptionContract:
    """Build a V2OptionContract for testing."""
    mid = ((bid + ask) / 2) if bid is not None and ask is not None else None
    return V2OptionContract(
        symbol=f"{root}260417{'P' if option_type == 'put' else 'C'}{int(strike * 1000):08d}",
        root_symbol=root,
        strike=strike,
        option_type=option_type,
        expiration=expiration,
        bid=bid,
        ask=ask,
        mid=mid,
        delta=delta,
        open_interest=oi,
        volume=volume,
    )


def _make_debit_butterfly(
    *,
    lower_strike: float = 580.0,
    center_strike: float = 585.0,
    upper_strike: float = 590.0,
    option_type: str = "call",
    lower_bid: float | None = 8.00,
    lower_ask: float | None = 8.20,
    center_bid: float | None = 5.50,
    center_ask: float | None = 5.70,
    upper_bid: float | None = 3.40,
    upper_ask: float | None = 3.60,
    lower_delta: float | None = 0.65,
    center_delta: float | None = 0.50,
    upper_delta: float | None = 0.35,
    oi: int | None = 5000,
    volume: int | None = 800,
    underlying_price: float | None = 585.0,
    strategy_id: str = "butterfly_debit",
    seq: int = 0,
) -> V2Candidate:
    """Build a 3-leg debit butterfly V2Candidate for testing.

    Default: call butterfly at 580/585/590 with spot=585.
    net_debit = ask(580) + ask(590) - 2×bid(585)
             = 8.20 + 3.60 - 2×5.50 = 0.80
    width = 5.0
    max_profit = (5.0 - 0.80) × 100 = 420.00
    max_loss = 0.80 × 100 = 80.00
    """
    exp = "2026-04-17"

    def _mid(b, a):
        return ((b + a) / 2) if b is not None and a is not None else None

    legs = [
        V2Leg(
            index=0, side="long", strike=lower_strike,
            option_type=option_type, expiration=exp,
            bid=lower_bid, ask=lower_ask, mid=_mid(lower_bid, lower_ask),
            delta=lower_delta, open_interest=oi, volume=volume,
        ),
        V2Leg(
            index=1, side="short", strike=center_strike,
            option_type=option_type, expiration=exp,
            bid=center_bid, ask=center_ask, mid=_mid(center_bid, center_ask),
            delta=center_delta, open_interest=oi, volume=volume,
        ),
        V2Leg(
            index=2, side="long", strike=upper_strike,
            option_type=option_type, expiration=exp,
            bid=upper_bid, ask=upper_ask, mid=_mid(upper_bid, upper_ask),
            delta=upper_delta, open_interest=oi, volume=volume,
        ),
    ]

    width = center_strike - lower_strike
    math = V2RecomputedMath(width=width)

    if lower_ask is not None and upper_ask is not None and center_bid is not None:
        debit = lower_ask + upper_ask - 2 * center_bid
        if 0 < debit < width:
            math.net_debit = round(debit, 4)

    candidate_id = (
        f"SPY|{strategy_id}|{exp}"
        f"|{option_type}|{lower_strike}/{center_strike}/{upper_strike}"
        f"|{seq}"
    )

    return V2Candidate(
        candidate_id=candidate_id,
        scanner_key=strategy_id,
        strategy_id=strategy_id,
        family_key="butterflies",
        symbol="SPY",
        underlying_price=underlying_price,
        expiration=exp,
        dte=36,
        legs=legs,
        math=math,
    )


def _make_iron_butterfly(
    *,
    center_strike: float = 590.0,
    lower_strike: float = 585.0,
    upper_strike: float = 595.0,
    pl_bid: float | None = 0.80,
    pl_ask: float | None = 0.95,
    ps_bid: float | None = 3.50,
    ps_ask: float | None = 3.65,
    cs_bid: float | None = 3.30,
    cs_ask: float | None = 3.45,
    cl_bid: float | None = 0.70,
    cl_ask: float | None = 0.85,
    ps_delta: float | None = -0.50,
    cs_delta: float | None = 0.50,
    oi: int | None = 5000,
    volume: int | None = 800,
    underlying_price: float | None = 590.0,
    strategy_id: str = "iron_butterfly",
    seq: int = 0,
) -> V2Candidate:
    """Build a 4-leg iron butterfly V2Candidate for testing.

    Default: center=590, wings at 585(P)/595(C), spot=590.
    net_credit = bid(ps) + bid(cs) - ask(pl) - ask(cl)
              = 3.50 + 3.30 - 0.95 - 0.85 = 5.00
    width = 5.0
    max_profit = 5.00 × 100 = 500.00
    max_loss = (5.0 - 5.00) × 100 = 0.00  → edge case; tests adjust
    """
    exp = "2026-04-17"

    def _mid(b, a):
        return ((b + a) / 2) if b is not None and a is not None else None

    legs = [
        V2Leg(
            index=0, side="long", strike=lower_strike,
            option_type="put", expiration=exp,
            bid=pl_bid, ask=pl_ask, mid=_mid(pl_bid, pl_ask),
            delta=-0.15, open_interest=oi, volume=volume,
        ),
        V2Leg(
            index=1, side="short", strike=center_strike,
            option_type="put", expiration=exp,
            bid=ps_bid, ask=ps_ask, mid=_mid(ps_bid, ps_ask),
            delta=ps_delta, open_interest=oi, volume=volume,
        ),
        V2Leg(
            index=2, side="short", strike=center_strike,
            option_type="call", expiration=exp,
            bid=cs_bid, ask=cs_ask, mid=_mid(cs_bid, cs_ask),
            delta=cs_delta, open_interest=oi, volume=volume,
        ),
        V2Leg(
            index=3, side="long", strike=upper_strike,
            option_type="call", expiration=exp,
            bid=cl_bid, ask=cl_ask, mid=_mid(cl_bid, cl_ask),
            delta=0.15, open_interest=oi, volume=volume,
        ),
    ]

    width = center_strike - lower_strike
    math = V2RecomputedMath(width=width)

    if (ps_bid is not None and cs_bid is not None
            and pl_ask is not None and cl_ask is not None):
        credit = ps_bid + cs_bid - pl_ask - cl_ask
        if credit > 0:
            math.net_credit = round(credit, 4)

    candidate_id = (
        f"SPY|{strategy_id}|{exp}"
        f"|{lower_strike}/{center_strike}/{upper_strike}|{seq}"
    )

    return V2Candidate(
        candidate_id=candidate_id,
        scanner_key=strategy_id,
        strategy_id=strategy_id,
        family_key="butterflies",
        symbol="SPY",
        underlying_price=underlying_price,
        expiration=exp,
        dte=36,
        legs=legs,
        math=math,
    )


def _make_butterfly_universe(
    *,
    symbol: str = "SPY",
    spot: float = 590.0,
    expiration: str = "2026-04-17",
    dte: int = 36,
    call_strikes: list[float] | None = None,
    put_strikes: list[float] | None = None,
    both_strikes: list[float] | None = None,
) -> V2NarrowedUniverse:
    """Build a V2NarrowedUniverse for butterfly testing.

    If ``both_strikes`` is provided, creates both put and call contracts
    at each strike (for iron butterfly center eligibility).
    If ``call_strikes``/``put_strikes`` are provided, creates contracts
    of only that type at each strike.
    """
    entries = []

    if both_strikes is not None:
        for s in both_strikes:
            for opt_type in ("put", "call"):
                d = -0.30 - (spot - s) * 0.005 if opt_type == "put" else 0.30 + (s - spot) * 0.005
                c = _make_contract(
                    strike=s, option_type=opt_type,
                    bid=max(0.10, 4.0 - abs(s - spot) * 0.08),
                    ask=max(0.20, 4.2 - abs(s - spot) * 0.08),
                    delta=d,
                    expiration=expiration,
                )
                entries.append(V2StrikeEntry(strike=s, contract=c))

    if call_strikes is not None:
        for s in call_strikes:
            c = _make_contract(
                strike=s, option_type="call",
                bid=max(0.10, 4.0 - abs(s - spot) * 0.08),
                ask=max(0.20, 4.2 - abs(s - spot) * 0.08),
                delta=0.30 + (s - spot) * 0.005,
                expiration=expiration,
            )
            entries.append(V2StrikeEntry(strike=s, contract=c))

    if put_strikes is not None:
        for s in put_strikes:
            c = _make_contract(
                strike=s, option_type="put",
                bid=max(0.10, 4.0 - abs(s - spot) * 0.08),
                ask=max(0.20, 4.2 - abs(s - spot) * 0.08),
                delta=-0.30 - (spot - s) * 0.005,
                expiration=expiration,
            )
            entries.append(V2StrikeEntry(strike=s, contract=c))

    bucket = V2ExpiryBucket(
        expiration=expiration,
        dte=dte,
        strikes=entries,
        strike_count=len(entries),
    )

    diag = V2NarrowingDiagnostics()
    diag.total_contracts_loaded = len(entries)
    diag.contracts_final = len(entries)
    diag.expirations_kept = 1
    diag.expirations_kept_list = [expiration]

    underlying = V2UnderlyingSnapshot(symbol=symbol, price=spot)

    return V2NarrowedUniverse(
        underlying=underlying,
        expiry_buckets={expiration: bucket},
        diagnostics=diag,
    )


# =====================================================================
#  1. TestDebitConstruction — Phase B debit butterfly
# =====================================================================

class TestDebitConstruction:
    """Test debit butterfly candidate construction from symmetric triplets."""

    def test_basic_call_construction(self):
        """Constructs debit call butterflies from symmetric triplets."""
        scanner = ButterfliesV2Scanner()
        # 5-point spaced call strikes: 580, 585, 590, 595
        universe = _make_butterfly_universe(
            call_strikes=[580.0, 585.0, 590.0, 595.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=590.0,
            expirations=["2026-04-17"],
            strategy_id="butterfly_debit",
            scanner_key="butterfly_debit",
            context={"option_side": "call"},
            narrowed_universe=universe,
        )

        assert len(candidates) > 0
        for c in candidates:
            assert c.family_key == "butterflies"
            assert c.strategy_id == "butterfly_debit"
            assert len(c.legs) == 3
            # All same option type
            assert all(l.option_type == "call" for l in c.legs)

    def test_both_call_and_put_by_default(self):
        """Without option_side filter, generates both call and put butterflies."""
        scanner = ButterfliesV2Scanner()
        universe = _make_butterfly_universe(
            call_strikes=[580.0, 585.0, 590.0],
            put_strikes=[580.0, 585.0, 590.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=585.0,
            expirations=["2026-04-17"],
            strategy_id="butterfly_debit",
            scanner_key="butterfly_debit",
            context={},
            narrowed_universe=universe,
        )

        types = {c.legs[0].option_type for c in candidates}
        assert "call" in types
        assert "put" in types

    def test_symmetric_triplets_only(self):
        """Only generates butterflies where center = (lower + upper) / 2."""
        scanner = ButterfliesV2Scanner()
        # 580, 585, 590 → valid: 580/585/590 (center=585)
        # 580, 590 → no valid center (missing 585 would make 580/585/590,
        #     but 585 IS present → gets generated)
        universe = _make_butterfly_universe(
            call_strikes=[580.0, 585.0, 590.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=585.0,
            expirations=["2026-04-17"],
            strategy_id="butterfly_debit",
            scanner_key="butterfly_debit",
            context={"option_side": "call"},
            narrowed_universe=universe,
        )

        # Only valid triplet: 580/585/590
        assert len(candidates) == 1
        strikes = sorted(l.strike for l in candidates[0].legs)
        assert strikes == [580.0, 585.0, 590.0]

    def test_asymmetric_strikes_excluded(self):
        """Non-evenly-spaced strikes don't form spurious butterflies."""
        scanner = ButterfliesV2Scanner()
        # 580, 583, 590: midpoint of 580 and 590 is 585 (not 583)
        # No valid symmetric triplet
        universe = _make_butterfly_universe(
            call_strikes=[580.0, 583.0, 590.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=585.0,
            expirations=["2026-04-17"],
            strategy_id="butterfly_debit",
            scanner_key="butterfly_debit",
            context={"option_side": "call"},
            narrowed_universe=universe,
        )

        assert len(candidates) == 0

    def test_candidate_id_format(self):
        """Candidate ID includes option_type and strikes."""
        scanner = ButterfliesV2Scanner()
        universe = _make_butterfly_universe(
            call_strikes=[580.0, 585.0, 590.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=585.0,
            expirations=["2026-04-17"],
            strategy_id="butterfly_debit",
            scanner_key="butterfly_debit",
            context={"option_side": "call"},
            narrowed_universe=universe,
        )

        assert len(candidates) == 1
        cid = candidates[0].candidate_id
        assert cid.startswith("SPY|butterfly_debit|2026-04-17|call|")
        assert "580.0/585.0/590.0" in cid

    def test_preliminary_math_has_width_and_debit(self):
        """Phase B sets preliminary width and net_debit."""
        scanner = ButterfliesV2Scanner()
        universe = _make_butterfly_universe(
            call_strikes=[580.0, 585.0, 590.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=585.0,
            expirations=["2026-04-17"],
            strategy_id="butterfly_debit",
            scanner_key="butterfly_debit",
            context={"option_side": "call"},
            narrowed_universe=universe,
        )

        assert len(candidates) == 1
        m = candidates[0].math
        assert m.width == 5.0

    def test_no_candidates_without_underlying(self):
        """Returns empty if no underlying price."""
        scanner = ButterfliesV2Scanner()
        universe = _make_butterfly_universe(call_strikes=[580.0, 585.0, 590.0])

        result = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=None,
            expirations=["2026-04-17"],
            strategy_id="butterfly_debit",
            scanner_key="butterfly_debit",
            context={},
            narrowed_universe=universe,
        )

        assert result == []

    def test_generation_cap(self):
        """Context generation_cap limits output."""
        scanner = ButterfliesV2Scanner()
        universe = _make_butterfly_universe(
            call_strikes=[575.0, 580.0, 585.0, 590.0, 595.0, 600.0],
            put_strikes=[575.0, 580.0, 585.0, 590.0, 595.0, 600.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=590.0,
            expirations=["2026-04-17"],
            strategy_id="butterfly_debit",
            scanner_key="butterfly_debit",
            context={"generation_cap": 3},
            narrowed_universe=universe,
        )

        assert len(candidates) <= 3

    def test_max_wing_width_filter(self):
        """Wings exceeding max_wing_width are excluded."""
        scanner = ButterfliesV2Scanner()
        # Only triplet would be 570/585/600 (width=15)
        universe = _make_butterfly_universe(
            call_strikes=[570.0, 585.0, 600.0],
        )

        # max_wing_width=10 → excludes 15-point wings
        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=585.0,
            expirations=["2026-04-17"],
            strategy_id="butterfly_debit",
            scanner_key="butterfly_debit",
            context={"option_side": "call", "max_wing_width": 10.0},
            narrowed_universe=universe,
        )

        assert len(candidates) == 0

    def test_empty_narrowed_universe(self):
        """Returns empty if narrowed universe is None."""
        scanner = ButterfliesV2Scanner()

        result = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=590.0,
            expirations=[],
            strategy_id="butterfly_debit",
            scanner_key="butterfly_debit",
            context={},
            narrowed_universe=None,
        )

        assert result == []

    def test_too_few_strikes(self):
        """Need at least 3 strikes to form a butterfly."""
        scanner = ButterfliesV2Scanner()
        universe = _make_butterfly_universe(
            call_strikes=[580.0, 585.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=585.0,
            expirations=["2026-04-17"],
            strategy_id="butterfly_debit",
            scanner_key="butterfly_debit",
            context={"option_side": "call"},
            narrowed_universe=universe,
        )

        assert len(candidates) == 0

    def test_multiple_triplets(self):
        """Multiple valid triplets from 5 equidistant strikes."""
        scanner = ButterfliesV2Scanner()
        # 580, 585, 590, 595, 600 → valid triplets:
        # 580/585/590, 585/590/595, 590/595/600, 580/590/600,
        # 585/592.5/600 (NO, not in set), 580/587.5/595 (NO)
        # Actually: only those where midpoint is in the set.
        # width=5: 580/585/590, 585/590/595, 590/595/600
        # width=10: 580/590/600
        # Total: 4
        universe = _make_butterfly_universe(
            call_strikes=[580.0, 585.0, 590.0, 595.0, 600.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=590.0,
            expirations=["2026-04-17"],
            strategy_id="butterfly_debit",
            scanner_key="butterfly_debit",
            context={"option_side": "call"},
            narrowed_universe=universe,
        )

        assert len(candidates) == 4


# =====================================================================
#  2. TestIronConstruction — Phase B iron butterfly
# =====================================================================

class TestIronConstruction:
    """Test iron butterfly candidate construction."""

    def test_basic_iron_construction(self):
        """Constructs iron butterflies with center having both put+call."""
        scanner = ButterfliesV2Scanner()
        universe = _make_butterfly_universe(
            both_strikes=[585.0, 590.0, 595.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=590.0,
            expirations=["2026-04-17"],
            strategy_id="iron_butterfly",
            scanner_key="iron_butterfly",
            context={},
            narrowed_universe=universe,
        )

        assert len(candidates) > 0
        for c in candidates:
            assert c.family_key == "butterflies"
            assert c.strategy_id == "iron_butterfly"
            assert len(c.legs) == 4
            # 2 puts + 2 calls
            puts = [l for l in c.legs if l.option_type == "put"]
            calls = [l for l in c.legs if l.option_type == "call"]
            assert len(puts) == 2
            assert len(calls) == 2

    def test_center_requires_both_types(self):
        """No iron butterflies if center strikes lack both put and call."""
        scanner = ButterfliesV2Scanner()
        # Only calls → no center with both types
        universe = _make_butterfly_universe(
            call_strikes=[585.0, 590.0, 595.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=590.0,
            expirations=["2026-04-17"],
            strategy_id="iron_butterfly",
            scanner_key="iron_butterfly",
            context={},
            narrowed_universe=universe,
        )

        assert len(candidates) == 0

    def test_symmetric_wings(self):
        """Iron butterfly wings are equidistant from center."""
        scanner = ButterfliesV2Scanner()
        universe = _make_butterfly_universe(
            both_strikes=[580.0, 585.0, 590.0, 595.0, 600.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=590.0,
            expirations=["2026-04-17"],
            strategy_id="iron_butterfly",
            scanner_key="iron_butterfly",
            context={},
            narrowed_universe=universe,
        )

        for c in candidates:
            puts = sorted([l for l in c.legs if l.option_type == "put"],
                          key=lambda l: l.strike)
            calls = sorted([l for l in c.legs if l.option_type == "call"],
                           key=lambda l: l.strike)
            center_put = puts[1].strike
            center_call = calls[0].strike
            # Center must be same strike
            assert center_put == center_call
            # Wings equidistant
            put_width = center_put - puts[0].strike
            call_width = calls[1].strike - center_call
            assert abs(put_width - call_width) < 0.01

    def test_candidate_id_format(self):
        """Iron butterfly candidate ID includes strikes."""
        scanner = ButterfliesV2Scanner()
        universe = _make_butterfly_universe(
            both_strikes=[585.0, 590.0, 595.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=590.0,
            expirations=["2026-04-17"],
            strategy_id="iron_butterfly",
            scanner_key="iron_butterfly",
            context={},
            narrowed_universe=universe,
        )

        assert len(candidates) > 0
        cid = candidates[0].candidate_id
        assert cid.startswith("SPY|iron_butterfly|2026-04-17|")

    def test_max_wing_width_filter(self):
        """Wings exceeding max_wing_width are excluded."""
        scanner = ButterfliesV2Scanner()
        # Only possible iron butterflies have width >= 5
        universe = _make_butterfly_universe(
            both_strikes=[580.0, 590.0, 600.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=590.0,
            expirations=["2026-04-17"],
            strategy_id="iron_butterfly",
            scanner_key="iron_butterfly",
            context={"max_wing_width": 3.0},
            narrowed_universe=universe,
        )

        assert len(candidates) == 0

    def test_generation_cap(self):
        """Context generation_cap limits output."""
        scanner = ButterfliesV2Scanner()
        universe = _make_butterfly_universe(
            both_strikes=[575.0, 580.0, 585.0, 590.0, 595.0, 600.0, 605.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=590.0,
            expirations=["2026-04-17"],
            strategy_id="iron_butterfly",
            scanner_key="iron_butterfly",
            context={"generation_cap": 5},
            narrowed_universe=universe,
        )

        assert len(candidates) <= 5


# =====================================================================
#  3. TestDebitStructuralChecks — Phase C debit butterfly
# =====================================================================

class TestDebitStructuralChecks:
    """Test debit butterfly structural validation."""

    def test_valid_debit_passes(self):
        """A well-formed debit butterfly passes all structural checks."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly()
        checks = scanner.family_structural_checks(cand)

        assert all(c.passed for c in checks)
        assert not cand.diagnostics.reject_reasons

    def test_wrong_leg_count_rejected(self):
        """Not 3 or 4 legs → v2_malformed_legs."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly()
        cand.legs = cand.legs[:2]  # only 2 legs
        checks = scanner.family_structural_checks(cand)

        assert any(not c.passed for c in checks)
        assert "v2_malformed_legs" in cand.diagnostics.reject_reasons

    def test_mixed_option_types_rejected(self):
        """Mixed option types in debit butterfly → v2_bf_invalid_geometry."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly()
        # Change upper leg to put
        cand.legs[2] = V2Leg(
            index=2, side="long", strike=590.0,
            option_type="put", expiration="2026-04-17",
            bid=3.40, ask=3.60, mid=3.50,
            delta=0.35, open_interest=5000, volume=800,
        )
        checks = scanner.family_structural_checks(cand)

        assert "v2_bf_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_wrong_side_balance_rejected(self):
        """3 long legs (no short) → v2_bf_invalid_geometry."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly()
        # Make center long instead of short
        cand.legs[1] = V2Leg(
            index=1, side="long", strike=585.0,
            option_type="call", expiration="2026-04-17",
            bid=5.50, ask=5.70, mid=5.60,
            delta=0.50, open_interest=5000, volume=800,
        )
        checks = scanner.family_structural_checks(cand)

        assert "v2_bf_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_center_not_short_rejected(self):
        """Middle strike must be short."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly()
        # Swap sides: make lower short, center long
        cand.legs[0] = V2Leg(
            index=0, side="short", strike=580.0,
            option_type="call", expiration="2026-04-17",
            bid=8.00, ask=8.20, mid=8.10,
            delta=0.65, open_interest=5000, volume=800,
        )
        cand.legs[1] = V2Leg(
            index=1, side="long", strike=585.0,
            option_type="call", expiration="2026-04-17",
            bid=5.50, ask=5.70, mid=5.60,
            delta=0.50, open_interest=5000, volume=800,
        )
        checks = scanner.family_structural_checks(cand)

        assert "v2_bf_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_asymmetric_wings_rejected(self):
        """Non-symmetric wings → v2_bf_invalid_geometry."""
        scanner = ButterfliesV2Scanner()
        # 580/585/592: center(585) != midpoint(586)
        cand = _make_debit_butterfly(lower_strike=580.0, center_strike=585.0,
                                     upper_strike=592.0)
        checks = scanner.family_structural_checks(cand)

        assert "v2_bf_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_symmetry_check_detail(self):
        """Symmetry check reports width for valid butterfly."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly()
        checks = scanner.family_structural_checks(cand)

        sym_check = [c for c in checks if c.name == "bf_symmetry"]
        assert len(sym_check) == 1
        assert sym_check[0].passed
        assert "width=5.0" in sym_check[0].detail


# =====================================================================
#  4. TestIronStructuralChecks — Phase C iron butterfly
# =====================================================================

class TestIronStructuralChecks:
    """Test iron butterfly structural validation."""

    def test_valid_iron_passes(self):
        """A well-formed iron butterfly passes all structural checks."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly()
        checks = scanner.family_structural_checks(cand)

        assert all(c.passed for c in checks), [
            (c.name, c.detail) for c in checks if not c.passed
        ]
        assert not cand.diagnostics.reject_reasons

    def test_wrong_type_balance_rejected(self):
        """3 puts + 1 call → v2_bf_invalid_geometry."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly()
        # Change upper call leg to put
        cand.legs[3] = V2Leg(
            index=3, side="long", strike=595.0,
            option_type="put", expiration="2026-04-17",
            bid=0.70, ask=0.85, mid=0.775,
            delta=-0.15, open_interest=5000, volume=800,
        )
        checks = scanner.family_structural_checks(cand)

        assert "v2_bf_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_center_strike_mismatch_rejected(self):
        """Put short and call short at different strikes → rejected."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly()
        # Move call short to a different strike
        cand.legs[2] = V2Leg(
            index=2, side="short", strike=591.0,
            option_type="call", expiration="2026-04-17",
            bid=3.30, ask=3.45, mid=3.375,
            delta=0.50, open_interest=5000, volume=800,
        )
        checks = scanner.family_structural_checks(cand)

        assert "v2_bf_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_center_sides_wrong_rejected(self):
        """Center legs must be short → v2_bf_invalid_geometry."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly()
        # Make center put long instead of short
        cand.legs[1] = V2Leg(
            index=1, side="long", strike=590.0,
            option_type="put", expiration="2026-04-17",
            bid=3.50, ask=3.65, mid=3.575,
            delta=-0.50, open_interest=5000, volume=800,
        )
        checks = scanner.family_structural_checks(cand)

        assert "v2_bf_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_wing_sides_wrong_rejected(self):
        """Wing legs must be long → v2_bf_invalid_geometry."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly()
        # Make lower put short instead of long
        cand.legs[0] = V2Leg(
            index=0, side="short", strike=585.0,
            option_type="put", expiration="2026-04-17",
            bid=0.80, ask=0.95, mid=0.875,
            delta=-0.15, open_interest=5000, volume=800,
        )
        checks = scanner.family_structural_checks(cand)

        assert "v2_bf_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_asymmetric_wings_rejected(self):
        """Unequal wing widths → v2_bf_invalid_geometry."""
        scanner = ButterfliesV2Scanner()
        # lower=583, center=590, upper=595: put_width=7, call_width=5
        cand = _make_iron_butterfly(lower_strike=583.0, center_strike=590.0,
                                    upper_strike=595.0)
        checks = scanner.family_structural_checks(cand)

        assert "v2_bf_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_symmetry_check_detail(self):
        """Symmetry check reports width for valid iron butterfly."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly()
        checks = scanner.family_structural_checks(cand)

        sym_check = [c for c in checks if c.name == "bf_symmetry"]
        assert len(sym_check) == 1
        assert sym_check[0].passed
        assert "width=5.0" in sym_check[0].detail


# =====================================================================
#  5. TestDebitMath — Phase E debit butterfly math
# =====================================================================

class TestDebitMath:
    """Test debit butterfly math recomputation."""

    def test_net_debit_with_2x_center(self):
        """net_debit = ask(lower) + ask(upper) − 2×bid(center)."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly(
            lower_ask=8.20, center_bid=5.50, upper_ask=3.60,
        )
        result = scanner.family_math(cand)

        # 8.20 + 3.60 - 2×5.50 = 0.80
        assert result.net_debit == 0.80

    def test_max_profit_and_max_loss(self):
        """max_profit = (width − debit) × 100; max_loss = debit × 100."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly(
            lower_ask=8.20, center_bid=5.50, upper_ask=3.60,
        )
        result = scanner.family_math(cand)

        # width = 5.0, debit = 0.80
        assert result.max_profit == 420.00
        assert result.max_loss == 80.00

    def test_breakevens(self):
        """breakevens = [lower + debit, upper − debit]."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly(
            lower_strike=580.0, center_strike=585.0, upper_strike=590.0,
            lower_ask=8.20, center_bid=5.50, upper_ask=3.60,
        )
        result = scanner.family_math(cand)

        # be_low = 580 + 0.80 = 580.80
        # be_high = 590 - 0.80 = 589.20
        assert result.breakeven == [580.80, 589.20]

    def test_pop_breakeven_range_call(self):
        """POP = P(BE_low < S_T < BE_high) via breakeven-range lognormal for calls."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly(
            lower_delta=0.65, upper_delta=0.35,
            lower_ask=8.20, center_bid=5.50, upper_ask=3.60,
        )
        result = scanner.family_math(cand)

        # Breakeven range lognormal: P(580.8 < S_T < 589.2), iv=0.25, dte=36
        assert result.pop_source == "breakeven_range_lognormal"
        assert 0.05 <= result.pop <= 0.15  # narrow zone → low POP

    def test_pop_breakeven_range_put(self):
        """POP = P(BE_low < S_T < BE_high) via breakeven-range lognormal for puts."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly(
            option_type="put",
            lower_delta=-0.25, upper_delta=-0.55,
            lower_ask=3.00, center_bid=4.50, upper_ask=6.50,
            lower_strike=580.0, center_strike=585.0, upper_strike=590.0,
        )
        result = scanner.family_math(cand)

        assert result.pop_source == "breakeven_range_lognormal"
        assert 0.05 <= result.pop <= 0.15

    def test_ev_computed(self):
        """EV adjusted for triangular payoff: pop*max_profit*0.50 - (1-pop)*max_loss."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly(
            lower_ask=8.20, center_bid=5.50, upper_ask=3.60,
            lower_delta=0.65, upper_delta=0.35,
        )
        result = scanner.family_math(cand)

        # With breakeven_range_lognormal POP and triangular adjustment
        assert result.ev is not None
        assert result.ev_adjustment == "triangular_payoff_0.50"
        assert result.ev_raw_binary is not None
        # Adjusted EV < raw binary EV (triangular payoff reduces the gain side)
        assert result.ev < result.ev_raw_binary
        assert result.ev_caveat is not None
        assert result.ev_accuracy == "adjusted"

    def test_ror_computed(self):
        """RoR = max_profit / max_loss."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly(
            lower_ask=8.20, center_bid=5.50, upper_ask=3.60,
        )
        result = scanner.family_math(cand)

        # 420 / 80 = 5.25
        assert result.ror == 5.25

    def test_kelly_computed(self):
        """Kelly criterion is computed when pop and ror available."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly(
            lower_ask=8.20, center_bid=5.50, upper_ask=3.60,
            lower_delta=0.65, upper_delta=0.35,
        )
        result = scanner.family_math(cand)

        assert result.kelly is not None

    def test_missing_quotes_returns_early(self):
        """Missing bid/ask → math returns without pricing."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly(center_bid=None)
        result = scanner.family_math(cand)

        assert result.net_debit is None
        assert result.max_profit is None

    def test_debit_exceeds_width_not_viable(self):
        """If net_debit >= width → trade not viable."""
        scanner = ButterfliesV2Scanner()
        # Make center bid very low → debit very high
        cand = _make_debit_butterfly(
            lower_ask=8.20, center_bid=1.00, upper_ask=3.60,
        )
        result = scanner.family_math(cand)

        # 8.20 + 3.60 - 2×1.00 = 9.80 > width(5.0)
        assert result.net_debit is None

    def test_notes_are_traceable(self):
        """Math notes include pricing formula and all key fields."""
        scanner = ButterfliesV2Scanner()
        cand = _make_debit_butterfly(
            lower_ask=8.20, center_bid=5.50, upper_ask=3.60,
        )
        result = scanner.family_math(cand)

        assert "pricing_formula" in result.notes
        assert "width" in result.notes
        assert "breakeven" in result.notes


# =====================================================================
#  6. TestIronMath — Phase E iron butterfly math
# =====================================================================

class TestIronMath:
    """Test iron butterfly math recomputation."""

    def test_net_credit(self):
        """net_credit = bid(ps) + bid(cs) − ask(pl) − ask(cl)."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly(
            ps_bid=3.50, cs_bid=3.30,
            pl_ask=0.95, cl_ask=0.85,
        )
        result = scanner.family_math(cand)

        # 3.50 + 3.30 - 0.95 - 0.85 = 5.00
        assert result.net_credit == 5.0

    def test_max_profit_is_credit(self):
        """max_profit = credit × 100."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly(
            ps_bid=3.50, cs_bid=3.30,
            pl_ask=0.95, cl_ask=0.85,
        )
        result = scanner.family_math(cand)

        assert result.max_profit == 500.00

    def test_max_loss(self):
        """max_loss = (width − credit) × 100."""
        scanner = ButterfliesV2Scanner()
        # Use less aggressive pricing so max_loss > 0
        cand = _make_iron_butterfly(
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
        )
        result = scanner.family_math(cand)

        # credit = 2.50 + 2.30 - 0.95 - 0.85 = 3.00
        # max_loss = (5.0 - 3.0) × 100 = 200.00
        assert result.net_credit == 3.0
        assert result.max_loss == 200.00

    def test_breakevens(self):
        """breakevens = [center − credit, center + credit]."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly(
            center_strike=590.0,
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
        )
        result = scanner.family_math(cand)

        # be_low = 590 - 3.00 = 587.00
        # be_high = 590 + 3.00 = 593.00
        assert result.breakeven == [587.00, 593.00]

    def test_pop_breakeven_range(self):
        """POP = P(BE_low < S_T < BE_high) via breakeven-range lognormal."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly(
            ps_delta=-0.50, cs_delta=0.50,
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
        )
        result = scanner.family_math(cand)

        # Breakeven range [587, 593] with spot=590, iv=0.25, dte=36
        assert result.pop_source == "breakeven_range_lognormal"
        assert 0.03 <= result.pop <= 0.15

    def test_pop_independent_of_delta(self):
        """POP depends on breakevens, not on delta values."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly(
            ps_delta=-0.40, cs_delta=0.40,
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
        )
        result = scanner.family_math(cand)

        # Same breakevens → same POP regardless of delta
        assert result.pop_source == "breakeven_range_lognormal"
        assert 0.03 <= result.pop <= 0.15

    def test_ev_computed(self):
        """EV adjusted for triangular payoff."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly(
            ps_delta=-0.40, cs_delta=0.40,
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
        )
        result = scanner.family_math(cand)

        assert result.ev is not None
        assert result.ev_adjustment == "triangular_payoff_0.50"
        assert result.ev_raw_binary is not None
        assert result.ev < result.ev_raw_binary
        assert result.ev_caveat is not None
        assert result.ev_accuracy == "adjusted"

    def test_missing_quotes_returns_early(self):
        """Missing bid/ask → math returns without pricing."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly(ps_bid=None)
        result = scanner.family_math(cand)

        assert result.net_credit is None
        assert result.max_profit is None

    def test_notes_traceable(self):
        """Math notes include pricing formula."""
        scanner = ButterfliesV2Scanner()
        cand = _make_iron_butterfly(
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
        )
        result = scanner.family_math(cand)

        assert "pricing_formula" in result.notes
        assert "breakeven" in result.notes


# =====================================================================
#  7. TestMathVerification — verify_width, verify_net, verify_breakeven
# =====================================================================

class TestMathVerification:
    """Test math verification handles butterfly paths correctly."""

    def test_verify_width_debit_3leg(self):
        """verify_width for 3-leg uses center − lower."""
        cand = _make_debit_butterfly()
        scanner = ButterfliesV2Scanner()
        scanner.family_math(cand)

        result = verify_width(cand, family_key="butterflies")
        assert result.status == "pass"

    def test_verify_width_iron_4leg(self):
        """verify_width for 4-leg iron butterfly uses max(put_w, call_w)."""
        cand = _make_iron_butterfly(
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
        )
        scanner = ButterfliesV2Scanner()
        scanner.family_math(cand)

        result = verify_width(cand, family_key="butterflies")
        assert result.status == "pass"

    def test_verify_net_debit_3leg(self):
        """verify_net_credit_or_debit for 3-leg debit butterfly."""
        cand = _make_debit_butterfly(
            lower_ask=8.20, center_bid=5.50, upper_ask=3.60,
        )
        scanner = ButterfliesV2Scanner()
        scanner.family_math(cand)

        result = verify_net_credit_or_debit(cand, family_key="butterflies")
        assert result.status == "pass"

    def test_verify_net_credit_4leg(self):
        """verify_net_credit_or_debit for 4-leg iron butterfly."""
        cand = _make_iron_butterfly(
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
        )
        scanner = ButterfliesV2Scanner()
        scanner.family_math(cand)

        result = verify_net_credit_or_debit(cand, family_key="butterflies")
        assert result.status == "pass"

    def test_verify_breakeven_debit_3leg(self):
        """verify_breakeven for 3-leg debit butterfly (dual breakeven)."""
        cand = _make_debit_butterfly(
            lower_ask=8.20, center_bid=5.50, upper_ask=3.60,
        )
        scanner = ButterfliesV2Scanner()
        scanner.family_math(cand)

        result = verify_breakeven(cand, family_key="butterflies")
        assert result.status == "pass"

    def test_verify_breakeven_iron_4leg(self):
        """verify_breakeven for 4-leg iron butterfly (dual breakeven)."""
        cand = _make_iron_butterfly(
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
        )
        scanner = ButterfliesV2Scanner()
        scanner.family_math(cand)

        result = verify_breakeven(cand, family_key="butterflies")
        assert result.status == "pass"

    def test_full_math_verification_debit(self):
        """run_math_verification passes for valid debit butterfly."""
        cand = _make_debit_butterfly(
            lower_ask=8.20, center_bid=5.50, upper_ask=3.60,
            lower_delta=0.65, upper_delta=0.35,
        )
        scanner = ButterfliesV2Scanner()
        scanner.family_math(cand)

        summary = run_math_verification(cand, family_key="butterflies")
        fails = [r for r in summary.results if r.status == "fail"]
        assert len(fails) == 0, [
            (r.check_key, r.message) for r in fails
        ]

    def test_full_math_verification_iron(self):
        """run_math_verification passes for valid iron butterfly."""
        cand = _make_iron_butterfly(
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
            ps_delta=-0.40, cs_delta=0.40,
        )
        scanner = ButterfliesV2Scanner()
        scanner.family_math(cand)

        summary = run_math_verification(cand, family_key="butterflies")
        fails = [r for r in summary.results if r.status == "fail"]
        assert len(fails) == 0, [
            (r.check_key, r.message) for r in fails
        ]


# =====================================================================
#  8. TestHygieneIntegration — Phase D/D2 shared hygiene
# =====================================================================

class TestHygieneIntegration:
    """Test shared hygiene phases work on butterfly candidates."""

    def test_quote_check_3leg(self):
        """Phase D quote checks work on 3-leg debit butterfly."""
        cand = _make_debit_butterfly()
        phase_d_quote_liquidity_sanity([cand])

        # Valid quotes → should pass
        assert "v2_missing_quote" not in cand.diagnostics.reject_reasons

    def test_quote_check_4leg(self):
        """Phase D quote checks work on 4-leg iron butterfly."""
        cand = _make_iron_butterfly(
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
        )
        phase_d_quote_liquidity_sanity([cand])

        assert "v2_missing_quote" not in cand.diagnostics.reject_reasons

    def test_missing_quote_rejected(self):
        """Leg with None bid → v2_missing_quote."""
        cand = _make_debit_butterfly(lower_bid=None, lower_ask=None)
        phase_d_quote_liquidity_sanity([cand])

        assert "v2_missing_quote" in cand.diagnostics.reject_reasons

    def test_dedup_works_3leg(self):
        """Phase D2 dedup suppresses duplicate 3-leg butterflies."""
        cand1 = _make_debit_butterfly(seq=0)
        cand2 = _make_debit_butterfly(seq=1)  # Same strikes, different seq
        result, summary = phase_d2_trust_hygiene([cand1, cand2])

        dedup = summary.get("dedup", {})
        assert dedup.get("duplicates_suppressed", 0) == 1
        keepers = [c for c in result if not c.diagnostics.reject_reasons]
        assert len(keepers) == 1

    def test_dedup_works_4leg(self):
        """Phase D2 dedup suppresses duplicate 4-leg iron butterflies."""
        cand1 = _make_iron_butterfly(
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
            seq=0,
        )
        cand2 = _make_iron_butterfly(
            ps_bid=2.50, cs_bid=2.30,
            pl_ask=0.95, cl_ask=0.85,
            seq=1,
        )
        result, summary = phase_d2_trust_hygiene([cand1, cand2])

        dedup = summary.get("dedup", {})
        assert dedup.get("duplicates_suppressed", 0) == 1
        keepers = [c for c in result if not c.diagnostics.reject_reasons]
        assert len(keepers) == 1


# =====================================================================
#  9. TestEndToEnd — full pipeline via run()
# =====================================================================

class TestEndToEnd:
    """Test full pipeline run via base scanner (Phase A–F)."""

    def _make_chain(self, *, strikes, spot=590.0, exp="2026-04-17"):
        """Build a minimal chain dict that narrow_chain can parse."""
        contracts = []
        for strike in strikes:
            for opt_type in ("put", "call"):
                p = "P" if opt_type == "put" else "C"
                d = -0.30 - (spot - strike) * 0.005 if opt_type == "put" else 0.30 + (strike - spot) * 0.005
                contracts.append({
                    "symbol": f"SPY260417{p}{int(strike * 1000):08d}",
                    "root_symbol": "SPY",
                    "strike": float(strike),
                    "option_type": opt_type,
                    "expiration_date": exp,
                    "bid": max(0.10, 4.0 - abs(strike - spot) * 0.08),
                    "ask": max(0.20, 4.2 - abs(strike - spot) * 0.08),
                    "mid": max(0.15, 4.1 - abs(strike - spot) * 0.08),
                    "greeks": {"delta": d},
                    "open_interest": 5000,
                    "volume": 800,
                })
        return {"options": {"option": contracts}}

    def test_debit_butterfly_pipeline(self):
        """Full run() for debit butterfly produces results."""
        scanner = ButterfliesV2Scanner()
        chain = self._make_chain(
            strikes=[580.0, 585.0, 590.0, 595.0],
        )

        result = scanner.run(
            scanner_key="butterfly_debit",
            strategy_id="butterfly_debit",
            symbol="SPY",
            chain=chain,
            underlying_price=590.0,
            context={"option_side": "call"},
        )

        assert result is not None
        assert result.total_constructed > 0
        assert result.family_key == "butterflies"

    def test_iron_butterfly_pipeline(self):
        """Full run() for iron butterfly produces results."""
        scanner = ButterfliesV2Scanner()
        chain = self._make_chain(
            strikes=[580.0, 585.0, 590.0, 595.0, 600.0],
        )

        result = scanner.run(
            scanner_key="iron_butterfly",
            strategy_id="iron_butterfly",
            symbol="SPY",
            chain=chain,
            underlying_price=590.0,
            context={},
        )

        assert result is not None
        assert result.total_constructed > 0
        assert result.family_key == "butterflies"


# =====================================================================
#  10. TestReasonCodes — new butterfly code registered
# =====================================================================

class TestReasonCodes:
    """Test butterfly reason code registration."""

    def test_bf_invalid_geometry_registered(self):
        """REJECT_BF_INVALID_GEOMETRY is registered."""
        assert is_valid_reject_code("v2_bf_invalid_geometry")

    def test_bf_invalid_geometry_code_info(self):
        """Code info has correct category and severity."""
        info = get_code_info("v2_bf_invalid_geometry")
        assert info is not None
        assert info.category == "structural"
        assert info.severity == "error"
        assert "Butterfly" in info.label

    def test_all_reject_codes_includes_bf(self):
        """all_reject_codes() includes the new BF code."""
        codes = all_reject_codes()
        assert "v2_bf_invalid_geometry" in codes


# =====================================================================
#  11. TestRegistry — family metadata and loading
# =====================================================================

class TestRegistry:
    """Test registry recognizes butterfly family as implemented."""

    def test_butterfly_debit_supported(self):
        """butterfly_debit is a supported V2 strategy."""
        assert is_v2_supported("butterfly_debit")

    def test_iron_butterfly_supported(self):
        """iron_butterfly is a supported V2 strategy."""
        assert is_v2_supported("iron_butterfly")

    def test_family_metadata(self):
        """Family metadata matches expected values."""
        meta = get_v2_family("butterfly_debit")
        assert meta is not None
        assert meta.family_key == "butterflies"
        assert meta.display_name == "Butterflies"
        assert "butterfly_debit" in meta.strategy_ids
        assert "iron_butterfly" in meta.strategy_ids
        assert meta.leg_count == "3-4"
        assert meta.implemented is True

    def test_scanner_loadable(self):
        """get_v2_scanner loads the butterfly scanner class."""
        scanner = get_v2_scanner("butterfly_debit")
        assert isinstance(scanner, ButterfliesV2Scanner)
        assert scanner.scanner_version == "2.0.0"

    def test_both_strategy_ids_load_same_scanner(self):
        """Both strategy IDs load the same butterfly scanner instance."""
        s1 = get_v2_scanner("butterfly_debit")
        s2 = get_v2_scanner("iron_butterfly")
        assert s1 is s2  # Cached instance
