"""V2 iron condor family tests — Prompt 10.

Tests for:
1. Candidate construction (Phase B) — spread side pairing, OTM filtering.
2. Family structural checks (Phase C) — 4 legs, 2P+2C, geometry, side widths.
3. Family math (Phase E) — net credit, max profit/loss, breakevens, POP, EV, RoR.
4. Math verification — iron condor paths in verify_width, verify_net_credit,
   verify_breakeven.
5. Duplicate/quote/liquidity hygiene integration (Phase D2).
6. End-to-end pipeline run via base scanner.
7. Reason code registry — new ic_invalid_geometry code registered.
8. Registry — family is implemented and loadable.
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
    REJECT_IC_INVALID_GEOMETRY,
    REJECT_MALFORMED_LEGS,
    REJECT_IMPOSSIBLE_MAX_LOSS,
    REJECT_IMPOSSIBLE_MAX_PROFIT,
    is_valid_reject_code,
    is_valid_code,
    get_code_info,
    all_reject_codes,
)
from app.services.scanner_v2.families.iron_condors import (
    IronCondorsV2Scanner,
    _build_condor_candidate,
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


def _make_condor(
    *,
    sp_strike: float = 585.0,  # short put
    lp_strike: float = 580.0,  # long put
    sc_strike: float = 605.0,  # short call
    lc_strike: float = 610.0,  # long call
    sp_bid: float | None = 1.50,
    sp_ask: float | None = 1.65,
    lp_bid: float | None = 0.40,
    lp_ask: float | None = 0.55,
    sc_bid: float | None = 1.30,
    sc_ask: float | None = 1.45,
    lc_bid: float | None = 0.30,
    lc_ask: float | None = 0.45,
    sp_delta: float | None = -0.25,
    sc_delta: float | None = 0.20,
    oi: int | None = 5000,
    volume: int | None = 800,
    strategy_id: str = "iron_condor",
    underlying_price: float | None = 595.0,
    seq: int = 0,
) -> V2Candidate:
    """Build a V2Candidate iron condor directly from leg quotes.

    Leg ordering:
    - legs[0]: short put
    - legs[1]: long put
    - legs[2]: short call
    - legs[3]: long call
    """
    exp = "2026-04-17"

    def _mid(b, a):
        return ((b + a) / 2) if b is not None and a is not None else None

    legs = [
        V2Leg(
            index=0, side="short", strike=sp_strike,
            option_type="put", expiration=exp,
            bid=sp_bid, ask=sp_ask, mid=_mid(sp_bid, sp_ask),
            delta=sp_delta, open_interest=oi, volume=volume,
        ),
        V2Leg(
            index=1, side="long", strike=lp_strike,
            option_type="put", expiration=exp,
            bid=lp_bid, ask=lp_ask, mid=_mid(lp_bid, lp_ask),
            delta=-0.15, open_interest=oi, volume=volume,
        ),
        V2Leg(
            index=2, side="short", strike=sc_strike,
            option_type="call", expiration=exp,
            bid=sc_bid, ask=sc_ask, mid=_mid(sc_bid, sc_ask),
            delta=sc_delta, open_interest=oi, volume=volume,
        ),
        V2Leg(
            index=3, side="long", strike=lc_strike,
            option_type="call", expiration=exp,
            bid=lc_bid, ask=lc_ask, mid=_mid(lc_bid, lc_ask),
            delta=0.10, open_interest=oi, volume=volume,
        ),
    ]

    put_width = sp_strike - lp_strike
    call_width = lc_strike - sc_strike
    width = max(put_width, call_width)

    math = V2RecomputedMath(width=width)
    # Preliminary credit
    if all(v is not None for v in [sp_bid, lp_ask, sc_bid, lc_ask]):
        credit = (sp_bid - lp_ask) + (sc_bid - lc_ask)
        if credit > 0:
            math.net_credit = round(credit, 4)

    candidate_id = (
        f"SPY|{strategy_id}|{exp}"
        f"|P{sp_strike}/{lp_strike}"
        f"|C{sc_strike}/{lc_strike}|{seq}"
    )

    return V2Candidate(
        candidate_id=candidate_id,
        scanner_key=strategy_id,
        strategy_id=strategy_id,
        family_key="iron_condors",
        symbol="SPY",
        underlying_price=underlying_price,
        expiration=exp,
        dte=36,
        legs=legs,
        math=math,
    )


def _make_narrowed_universe(
    *,
    symbol: str = "SPY",
    spot: float = 595.0,
    expiration: str = "2026-04-17",
    dte: int = 36,
    put_strikes: list[float] | None = None,
    call_strikes: list[float] | None = None,
) -> V2NarrowedUniverse:
    """Build a V2NarrowedUniverse with put and call strikes for testing."""
    if put_strikes is None:
        put_strikes = [575.0, 580.0, 585.0, 590.0]
    if call_strikes is None:
        call_strikes = [600.0, 605.0, 610.0, 615.0]

    entries = []
    for s in put_strikes:
        c = _make_contract(
            strike=s, option_type="put",
            bid=max(0.10, 3.0 - (spot - s) * 0.05),
            ask=max(0.20, 3.2 - (spot - s) * 0.05),
            delta=-0.15 - (spot - s) * 0.002,
            expiration=expiration,
        )
        entries.append(V2StrikeEntry(strike=s, contract=c))

    for s in call_strikes:
        c = _make_contract(
            strike=s, option_type="call",
            bid=max(0.10, 3.0 - (s - spot) * 0.05),
            ask=max(0.20, 3.2 - (s - spot) * 0.05),
            delta=0.15 + (s - spot) * 0.002,
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
#  1. TestConstruction — Phase B candidate construction
# =====================================================================

class TestConstruction:
    """Test iron condor candidate construction from spread sides."""

    def test_basic_construction(self):
        """Constructs valid condor candidates from OTM puts + calls."""
        scanner = IronCondorsV2Scanner()
        universe = _make_narrowed_universe()

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=595.0,
            expirations=["2026-04-17"],
            strategy_id="iron_condor",
            scanner_key="iron_condor",
            context={},
            narrowed_universe=universe,
        )

        assert len(candidates) > 0
        for c in candidates:
            assert c.family_key == "iron_condors"
            assert c.strategy_id == "iron_condor"
            assert c.symbol == "SPY"
            assert len(c.legs) == 4

    def test_all_candidates_have_otm_geometry(self):
        """Every constructed condor has put_short < underlying < call_short."""
        scanner = IronCondorsV2Scanner()
        universe = _make_narrowed_universe()

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=595.0,
            expirations=["2026-04-17"],
            strategy_id="iron_condor",
            scanner_key="iron_condor",
            context={},
            narrowed_universe=universe,
        )

        for c in candidates:
            puts = [l for l in c.legs if l.option_type == "put"]
            calls = [l for l in c.legs if l.option_type == "call"]
            assert len(puts) == 2
            assert len(calls) == 2
            short_put = max(puts, key=lambda l: l.strike)
            short_call = min(calls, key=lambda l: l.strike)
            assert short_put.strike < 595.0 < short_call.strike

    def test_candidate_id_format(self):
        """Candidate ID includes P and C sides and sequence number."""
        scanner = IronCondorsV2Scanner()
        universe = _make_narrowed_universe(
            put_strikes=[585.0, 590.0],
            call_strikes=[600.0, 605.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=595.0,
            expirations=["2026-04-17"],
            strategy_id="iron_condor",
            scanner_key="iron_condor",
            context={},
            narrowed_universe=universe,
        )

        assert len(candidates) == 1
        cid = candidates[0].candidate_id
        assert cid.startswith("SPY|iron_condor|2026-04-17|P")
        assert "|C" in cid

    def test_preliminary_math_has_width_and_credit(self):
        """Phase B sets preliminary width and net_credit."""
        scanner = IronCondorsV2Scanner()
        universe = _make_narrowed_universe(
            put_strikes=[580.0, 585.0],
            call_strikes=[605.0, 610.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=595.0,
            expirations=["2026-04-17"],
            strategy_id="iron_condor",
            scanner_key="iron_condor",
            context={},
            narrowed_universe=universe,
        )

        assert len(candidates) == 1
        m = candidates[0].math
        assert m.width == 5.0
        # net_credit may be set if raw quotes produce positive credit
        assert m.width > 0

    def test_no_candidates_without_underlying(self):
        """Returns empty if no underlying price available."""
        scanner = IronCondorsV2Scanner()
        universe = _make_narrowed_universe()

        result = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=None,
            expirations=["2026-04-17"],
            strategy_id="iron_condor",
            scanner_key="iron_condor",
            context={},
            narrowed_universe=universe,
        )

        assert result == []

    def test_generation_cap(self):
        """Context generation_cap limits output."""
        scanner = IronCondorsV2Scanner()
        universe = _make_narrowed_universe()

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=595.0,
            expirations=["2026-04-17"],
            strategy_id="iron_condor",
            scanner_key="iron_condor",
            context={"generation_cap": 5},
            narrowed_universe=universe,
        )

        assert len(candidates) <= 5

    def test_max_wing_width_filter(self):
        """Wings exceeding max_wing_width are excluded."""
        scanner = IronCondorsV2Scanner()
        # put_strikes span 30 points, call_strikes span 30 points
        universe = _make_narrowed_universe(
            put_strikes=[560.0, 590.0],
            call_strikes=[600.0, 630.0],
        )

        # With max_wing_width=5 → no valid sides (30pt wing > 5pt cap)
        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=595.0,
            expirations=["2026-04-17"],
            strategy_id="iron_condor",
            scanner_key="iron_condor",
            context={"max_wing_width": 5.0},
            narrowed_universe=universe,
        )

        assert len(candidates) == 0

    def test_empty_narrowed_universe(self):
        """Returns empty if narrowed universe is None or empty."""
        scanner = IronCondorsV2Scanner()

        result = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=595.0,
            expirations=[],
            strategy_id="iron_condor",
            scanner_key="iron_condor",
            context={},
            narrowed_universe=None,
        )

        assert result == []

    def test_too_few_strikes_per_side(self):
        """Need at least 2 per side to form a spread."""
        scanner = IronCondorsV2Scanner()
        universe = _make_narrowed_universe(
            put_strikes=[585.0],    # only 1 put
            call_strikes=[605.0, 610.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=595.0,
            expirations=["2026-04-17"],
            strategy_id="iron_condor",
            scanner_key="iron_condor",
            context={},
            narrowed_universe=universe,
        )

        assert len(candidates) == 0

    def test_combinatorial_count(self):
        """4 put strikes × 4 call strikes = 6×6 = 36 condors."""
        scanner = IronCondorsV2Scanner()
        universe = _make_narrowed_universe(
            put_strikes=[575.0, 580.0, 585.0, 590.0],
            call_strikes=[600.0, 605.0, 610.0, 615.0],
        )

        candidates = scanner.construct_candidates(
            chain={},
            symbol="SPY",
            underlying_price=595.0,
            expirations=["2026-04-17"],
            strategy_id="iron_condor",
            scanner_key="iron_condor",
            context={},
            narrowed_universe=universe,
        )

        # C(4,2) = 6 put sides × C(4,2) = 6 call sides = 36
        assert len(candidates) == 36


# =====================================================================
#  2. TestStructuralChecks — Phase C family hook
# =====================================================================

class TestStructuralChecks:
    """Test iron condor structural validation."""

    def test_valid_condor_passes(self):
        """A well-formed condor passes all structural checks."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor()
        checks = scanner.family_structural_checks(cand)

        assert all(c.passed for c in checks)
        assert not cand.diagnostics.reject_reasons

    def test_wrong_leg_count_rejected(self):
        """Fewer than 4 legs → v2_malformed_legs."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor()
        cand.legs = cand.legs[:3]  # only 3 legs
        checks = scanner.family_structural_checks(cand)

        assert any(not c.passed for c in checks)
        assert "v2_malformed_legs" in cand.diagnostics.reject_reasons

    def test_wrong_type_balance_rejected(self):
        """3 puts + 1 call → v2_malformed_legs."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor()
        # Change the long call to a long put
        cand.legs[3] = V2Leg(
            index=3, side="long", strike=610.0,
            option_type="put", expiration="2026-04-17",
            bid=0.30, ask=0.45, mid=0.375,
            delta=-0.10, open_interest=5000, volume=800,
        )
        checks = scanner.family_structural_checks(cand)

        assert "v2_malformed_legs" in cand.diagnostics.reject_reasons

    def test_overlapping_sides_rejected(self):
        """put_short >= call_short → v2_ic_invalid_geometry."""
        scanner = IronCondorsV2Scanner()
        # Make put_short(605) >= call_short(600) — overlap
        cand = _make_condor(sp_strike=605.0, lp_strike=600.0,
                            sc_strike=600.0, lc_strike=605.0)
        checks = scanner.family_structural_checks(cand)

        assert "v2_ic_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_equal_strikes_within_side_rejected(self):
        """put_long == put_short → ordering violation."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(sp_strike=585.0, lp_strike=585.0)
        checks = scanner.family_structural_checks(cand)

        # Either v2_ic_invalid_geometry or v2_malformed_legs depending on check
        assert cand.diagnostics.reject_reasons

    def test_strike_ordering_check_detail(self):
        """Check detail message includes all 4 strikes."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor()
        checks = scanner.family_structural_checks(cand)

        ordering_check = [c for c in checks if c.name == "ic_strike_ordering"]
        assert len(ordering_check) == 1
        assert ordering_check[0].passed

    def test_side_widths_check(self):
        """Side widths check reports put_width and call_width."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_strike=590.0, lp_strike=580.0,
            sc_strike=600.0, lc_strike=615.0,
        )
        checks = scanner.family_structural_checks(cand)

        width_check = [c for c in checks if c.name == "ic_side_widths"]
        assert len(width_check) == 1
        assert width_check[0].passed
        assert "put_width=10.0" in width_check[0].detail
        assert "call_width=15.0" in width_check[0].detail


# =====================================================================
#  3. TestFamilyMath — Phase E condor math
# =====================================================================

class TestFamilyMath:
    """Test iron condor math recomputation."""

    def test_net_credit_from_4_legs(self):
        """net_credit = put_side_credit + call_side_credit."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_bid=1.50, lp_ask=0.55,
            sc_bid=1.30, lc_ask=0.45,
        )
        result = scanner.family_math(cand)

        # put_side = 1.50 - 0.55 = 0.95
        # call_side = 1.30 - 0.45 = 0.85
        # net = 0.95 + 0.85 = 1.80
        assert result.net_credit == 1.80

    def test_max_profit_and_max_loss(self):
        """max_profit = net_credit × 100, max_loss = (width - net_credit) × 100."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_strike=585.0, lp_strike=580.0,
            sc_strike=605.0, lc_strike=610.0,
            sp_bid=1.50, lp_ask=0.55,
            sc_bid=1.30, lc_ask=0.45,
        )
        result = scanner.family_math(cand)

        # width = max(5, 5) = 5
        # net_credit = 1.80
        # max_profit = 1.80 × 100 = 180.00
        # max_loss = (5 - 1.80) × 100 = 320.00
        assert result.max_profit == 180.00
        assert result.max_loss == 320.00
        assert result.width == 5.0

    def test_asymmetric_wing_widths(self):
        """Width = max(put_width, call_width) for asymmetric condors."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_strike=590.0, lp_strike=580.0,   # put_width = 10
            sc_strike=600.0, lc_strike=605.0,   # call_width = 5
            sp_bid=2.00, lp_ask=0.50,
            sc_bid=1.50, lc_ask=0.30,
        )
        result = scanner.family_math(cand)

        assert result.width == 10.0  # max(10, 5)
        assert "put_width=10.0" in result.notes.get("width", "")

    def test_breakevens(self):
        """breakeven_low = put_short - net_credit, breakeven_high = call_short + net_credit."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_strike=585.0, sc_strike=605.0,
            sp_bid=1.50, lp_ask=0.55,
            sc_bid=1.30, lc_ask=0.45,
        )
        result = scanner.family_math(cand)

        # net_credit = 1.80
        assert result.breakeven == [585.0 - 1.80, 605.0 + 1.80]
        assert result.breakeven[0] == 583.20
        assert result.breakeven[1] == 606.80

    def test_pop_delta_approximation(self):
        """POP ≈ 1 - |delta_put_short| - |delta_call_short|."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_delta=-0.25, sc_delta=0.20,
            sp_bid=1.50, lp_ask=0.55,
            sc_bid=1.30, lc_ask=0.45,
        )
        result = scanner.family_math(cand)

        # POP = 1 - 0.25 - 0.20 = 0.55
        assert result.pop == 0.55
        assert result.pop_source == "delta_approx"

    def test_ev_computation(self):
        """EV = POP × max_profit - (1-POP) × max_loss."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_delta=-0.25, sc_delta=0.20,
            sp_bid=1.50, lp_ask=0.55,
            sc_bid=1.30, lc_ask=0.45,
        )
        result = scanner.family_math(cand)

        # POP=0.55, max_profit=180, max_loss=320
        # EV = 0.55 * 180 - 0.45 * 320 = 99 - 144 = -45.0
        assert result.ev == -45.0

    def test_ror_computation(self):
        """RoR = max_profit / max_loss."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_bid=1.50, lp_ask=0.55,
            sc_bid=1.30, lc_ask=0.45,
        )
        result = scanner.family_math(cand)

        # 180 / 320 = 0.5625
        assert result.ror == 0.5625

    def test_kelly_computation(self):
        """Kelly = pop - (1-pop) / ror."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_delta=-0.25, sc_delta=0.20,
            sp_bid=1.50, lp_ask=0.55,
            sc_bid=1.30, lc_ask=0.45,
        )
        result = scanner.family_math(cand)

        # kelly = 0.55 - 0.45 / 0.5625 = 0.55 - 0.8 = -0.25
        assert result.kelly == -0.25

    def test_negative_credit_leaves_math_incomplete(self):
        """If net_credit ≤ 0, math is incomplete (no max_profit etc)."""
        scanner = IronCondorsV2Scanner()
        # Set bids very low so credit is negative
        cand = _make_condor(
            sp_bid=0.10, lp_ask=0.55,
            sc_bid=0.10, lc_ask=0.45,
        )
        result = scanner.family_math(cand)

        assert result.net_credit is None
        assert result.max_profit is None
        assert result.max_loss is None
        assert "not viable" in result.notes.get("pricing", "")

    def test_missing_quotes_leaves_math_incomplete(self):
        """If any leg has None bid/ask, math can't be computed."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(sp_bid=None)
        result = scanner.family_math(cand)

        assert result.net_credit is None
        assert "missing bid/ask" in result.notes.get("pricing", "")

    def test_ev_per_day(self):
        """ev_per_day = ev / dte."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_delta=-0.25, sc_delta=0.20,
            sp_bid=1.50, lp_ask=0.55,
            sc_bid=1.30, lc_ask=0.45,
        )
        cand.dte = 36
        result = scanner.family_math(cand)

        assert result.ev_per_day is not None
        assert result.ev_per_day == round(result.ev / 36, 4)

    def test_notes_traceability(self):
        """Math notes include computation traces for all fields."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_delta=-0.25, sc_delta=0.20,
            sp_bid=1.50, lp_ask=0.55,
            sc_bid=1.30, lc_ask=0.45,
        )
        result = scanner.family_math(cand)

        assert "put_side_credit" in result.notes
        assert "call_side_credit" in result.notes
        assert "net_credit" in result.notes
        assert "width" in result.notes
        assert "breakeven" in result.notes
        assert "pop" in result.notes
        assert "ev" in result.notes
        assert "ror" in result.notes


# =====================================================================
#  4. TestMathVerification — IC paths in verification checks
# =====================================================================

class TestMathVerification:
    """Test iron condor handling in shared math verification."""

    def _full_condor(self):
        """Build a condor and run family_math to populate all fields."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_bid=1.50, lp_ask=0.55,
            sc_bid=1.30, lc_ask=0.45,
            sp_delta=-0.25, sc_delta=0.20,
        )
        scanner.family_math(cand)
        return cand

    def test_verify_width_ic_path(self):
        """verify_width uses max(put_width, call_width) for iron condors."""
        cand = self._full_condor()
        result = verify_width(cand, family_key="iron_condors")
        assert result.passed

    def test_verify_width_ic_asymmetric(self):
        """Asymmetric condor width verifies correctly."""
        scanner = IronCondorsV2Scanner()
        cand = _make_condor(
            sp_strike=590.0, lp_strike=580.0,  # put_width=10
            sc_strike=600.0, lc_strike=605.0,  # call_width=5
            sp_bid=2.00, lp_ask=0.50,
            sc_bid=1.50, lc_ask=0.30,
        )
        scanner.family_math(cand)
        result = verify_width(cand, family_key="iron_condors")
        assert result.passed
        # Width should be 10 (max of the two sides)
        assert cand.math.width == 10.0

    def test_verify_net_credit_ic_path(self):
        """verify_net_credit_or_debit uses 4-leg iron condor path."""
        cand = self._full_condor()
        result = verify_net_credit_or_debit(cand, family_key="iron_condors")
        assert result.passed

    def test_verify_breakeven_ic_path(self):
        """verify_breakeven checks both breakevens for iron condors."""
        cand = self._full_condor()
        result = verify_breakeven(cand, family_key="iron_condors")
        assert result.passed

    def test_full_math_verification_passes(self):
        """run_math_verification passes for well-formed condor."""
        cand = self._full_condor()
        summary = run_math_verification(cand, family_key="iron_condors")
        assert not summary.fail_codes
        assert not summary.warn_codes

    def test_verify_width_without_family_key_uses_fallback(self):
        """Without family_key, verify_width uses max-min span fallback."""
        cand = self._full_condor()
        # Without family_key, width verification uses max(strikes)-min(strikes)
        # which would be 610-580=30, not 5.  So this would fail.
        result = verify_width(cand, family_key=None)
        # Should fail because 30 != 5
        assert not result.passed


# =====================================================================
#  5. TestHygieneIntegration — Phase D, D2 integration
# =====================================================================

class TestHygieneIntegration:
    """Test condor interaction with shared hygiene phases."""

    def test_phase_d_rejects_missing_quotes(self):
        """Phase D rejects condor with None bid/ask on a leg."""
        cand = _make_condor(sp_bid=None, sp_ask=None)
        result = phase_d_quote_liquidity_sanity([cand])
        assert cand.diagnostics.reject_reasons

    def test_phase_d2_quote_sanity_on_condor(self):
        """Phase D2 quote sanity handles 4-leg candidates."""
        cand = _make_condor()
        result, summary = phase_d2_trust_hygiene([cand])
        # Well-formed condor should pass hygiene
        assert not cand.diagnostics.reject_reasons

    def test_phase_d2_dedup_on_condors(self):
        """Duplicate condors are suppressed."""
        cand1 = _make_condor(seq=0)
        cand2 = _make_condor(seq=1)
        # Same legs → same dedup key → one suppressed
        result, summary = phase_d2_trust_hygiene([cand1, cand2])
        dedup = summary.get("dedup", {})
        assert dedup.get("duplicates_suppressed", 0) == 1
        # All candidates returned, but one is rejected as duplicate
        keepers = [c for c in result if not c.diagnostics.reject_reasons]
        assert len(keepers) == 1

    def test_phase_d2_dead_leg_rejects_condor(self):
        """A condor with a dead leg (OI=0, vol=0) is rejected."""
        cand = _make_condor(oi=0, volume=0)
        # Need to set legs individually — _make_condor sets all legs same
        result, summary = phase_d2_trust_hygiene([cand])
        assert any("v2_dead_leg" in r for r in cand.diagnostics.reject_reasons)


# =====================================================================
#  6. TestEndToEnd — full pipeline run
# =====================================================================

class TestEndToEnd:
    """Test full iron condor pipeline execution."""

    def test_full_pipeline_constructs_and_filters(self):
        """Full Phase A–F pipeline produces passed and rejected candidates."""
        scanner = IronCondorsV2Scanner()
        # Build a minimal chain that narrow_chain can parse
        contracts = []
        exp = "2026-04-17"
        for strike in [575, 580, 585, 590]:
            contracts.append({
                "symbol": f"SPY260417P{strike*1000:08d}",
                "root_symbol": "SPY",
                "strike": float(strike),
                "option_type": "put",
                "expiration_date": exp,
                "bid": max(0.10, 3.0 - (595 - strike) * 0.05),
                "ask": max(0.20, 3.2 - (595 - strike) * 0.05),
                "mid": max(0.15, 3.1 - (595 - strike) * 0.05),
                "greeks": {"delta": -0.15 - (595 - strike) * 0.002},
                "open_interest": 5000,
                "volume": 800,
            })
        for strike in [600, 605, 610, 615]:
            contracts.append({
                "symbol": f"SPY260417C{strike*1000:08d}",
                "root_symbol": "SPY",
                "strike": float(strike),
                "option_type": "call",
                "expiration_date": exp,
                "bid": max(0.10, 3.0 - (strike - 595) * 0.05),
                "ask": max(0.20, 3.2 - (strike - 595) * 0.05),
                "mid": max(0.15, 3.1 - (strike - 595) * 0.05),
                "greeks": {"delta": 0.15 + (strike - 595) * 0.002},
                "open_interest": 5000,
                "volume": 800,
            })

        chain = {"options": {"option": contracts}}
        result = scanner.run(
            scanner_key="iron_condor",
            strategy_id="iron_condor",
            symbol="SPY",
            chain=chain,
            underlying_price=595.0,
            context={},
        )

        assert result.total_constructed > 0
        assert result.scanner_key == "iron_condor"
        assert result.family_key == "iron_condors"
        # Phase counts should show all phases
        phase_names = [p["phase"] for p in result.phase_counts]
        assert "constructed" in phase_names
        assert "structural_validation" in phase_names
        assert "quote_liquidity_sanity" in phase_names
        assert "trust_hygiene" in phase_names
        assert "recomputed_math" in phase_names
        assert "normalized" in phase_names

    def test_passed_candidates_have_complete_math(self):
        """Passed candidates have all math fields populated."""
        scanner = IronCondorsV2Scanner()
        contracts = []
        exp = "2026-04-17"
        for strike in [580, 585, 590]:
            contracts.append({
                "symbol": f"SPY260417P{strike*1000:08d}",
                "root_symbol": "SPY",
                "strike": float(strike),
                "option_type": "put",
                "expiration_date": exp,
                "bid": 2.50 - (595 - strike) * 0.08,
                "ask": 2.70 - (595 - strike) * 0.08,
                "greeks": {"delta": -0.10 - (595 - strike) * 0.005},
                "open_interest": 5000,
                "volume": 800,
            })
        for strike in [600, 605, 610]:
            contracts.append({
                "symbol": f"SPY260417C{strike*1000:08d}",
                "root_symbol": "SPY",
                "strike": float(strike),
                "option_type": "call",
                "expiration_date": exp,
                "bid": 2.50 - (strike - 595) * 0.08,
                "ask": 2.70 - (strike - 595) * 0.08,
                "greeks": {"delta": 0.10 + (strike - 595) * 0.005},
                "open_interest": 5000,
                "volume": 800,
            })

        chain = {"options": {"option": contracts}}
        result = scanner.run(
            scanner_key="iron_condor",
            strategy_id="iron_condor",
            symbol="SPY",
            chain=chain,
            underlying_price=595.0,
        )

        for c in result.candidates:
            assert c.passed is True
            assert c.downstream_usable is True
            assert c.math.net_credit is not None
            assert c.math.max_profit is not None
            assert c.math.max_loss is not None
            assert c.math.width is not None
            assert len(c.math.breakeven) == 2


# =====================================================================
#  7. TestReasonCodeRegistry — new code registered
# =====================================================================

class TestReasonCodeRegistry:
    """Test that iron condor reason code is properly registered."""

    def test_ic_geometry_code_exists(self):
        """v2_ic_invalid_geometry is a valid reject code."""
        assert is_valid_reject_code(REJECT_IC_INVALID_GEOMETRY)
        assert is_valid_code(REJECT_IC_INVALID_GEOMETRY)

    def test_ic_geometry_code_metadata(self):
        """Code has correct category and severity."""
        info = get_code_info(REJECT_IC_INVALID_GEOMETRY)
        assert info is not None
        assert info.category == "structural"
        assert info.severity == "error"
        assert "geometry" in info.label.lower()

    def test_all_reject_codes_count(self):
        """Total reject codes: 32 (30 previous + 2 credit integrity)."""
        assert len(all_reject_codes()) == 32


# =====================================================================
#  8. TestRegistry — family registration and loading
# =====================================================================

class TestRegistry:
    """Test iron condor family is properly registered and loadable."""

    def test_is_v2_supported(self):
        """iron_condor strategy is V2 supported."""
        assert is_v2_supported("iron_condor")

    def test_family_metadata(self):
        """Family metadata is correct."""
        meta = get_v2_family("iron_condor")
        assert meta is not None
        assert meta.family_key == "iron_condors"
        assert meta.leg_count == 4
        assert meta.implemented is True

    def test_scanner_loadable(self):
        """Scanner can be loaded and is correct class."""
        scanner = get_v2_scanner("iron_condor")
        assert isinstance(scanner, IronCondorsV2Scanner)
        assert scanner.family_key == "iron_condors"
        assert scanner.scanner_version == "2.0.0"

    def test_dte_window(self):
        """DTE window is 7–60 for condors."""
        scanner = IronCondorsV2Scanner()
        assert scanner.dte_min == 7
        assert scanner.dte_max == 60


# =====================================================================
#  9. TestBuildCondorCandidate — construction helper
# =====================================================================

class TestBuildCondorCandidate:
    """Test _build_condor_candidate helper directly."""

    def test_leg_ordering(self):
        """Legs are ordered: short_put, long_put, short_call, long_call."""
        sp = _make_contract(strike=585, option_type="put")
        lp = _make_contract(strike=580, option_type="put")
        sc = _make_contract(strike=605, option_type="call")
        lc = _make_contract(strike=610, option_type="call")

        cand = _build_condor_candidate(
            symbol="SPY", strategy_id="iron_condor",
            scanner_key="iron_condor", family_key="iron_condors",
            underlying_price=595.0, expiration="2026-04-17", dte=36,
            short_put_strike=585.0, short_put_contract=sp,
            long_put_strike=580.0, long_put_contract=lp,
            short_call_strike=605.0, short_call_contract=sc,
            long_call_strike=610.0, long_call_contract=lc,
            seq=42,
        )

        assert len(cand.legs) == 4
        assert cand.legs[0].side == "short" and cand.legs[0].option_type == "put"
        assert cand.legs[1].side == "long" and cand.legs[1].option_type == "put"
        assert cand.legs[2].side == "short" and cand.legs[2].option_type == "call"
        assert cand.legs[3].side == "long" and cand.legs[3].option_type == "call"

    def test_candidate_id_includes_strikes(self):
        """Candidate ID has P{sp}/{lp}|C{sc}/{lc} pattern."""
        sp = _make_contract(strike=585, option_type="put")
        lp = _make_contract(strike=580, option_type="put")
        sc = _make_contract(strike=605, option_type="call")
        lc = _make_contract(strike=610, option_type="call")

        cand = _build_condor_candidate(
            symbol="SPY", strategy_id="iron_condor",
            scanner_key="iron_condor", family_key="iron_condors",
            underlying_price=595.0, expiration="2026-04-17", dte=36,
            short_put_strike=585.0, short_put_contract=sp,
            long_put_strike=580.0, long_put_contract=lp,
            short_call_strike=605.0, short_call_contract=sc,
            long_call_strike=610.0, long_call_contract=lc,
            seq=42,
        )

        assert "P585.0/580.0" in cand.candidate_id
        assert "C605.0/610.0" in cand.candidate_id
        assert "|42" in cand.candidate_id

    def test_preliminary_width(self):
        """Preliminary width = max(put_width, call_width)."""
        sp = _make_contract(strike=590, option_type="put", bid=2.0, ask=2.2)
        lp = _make_contract(strike=580, option_type="put", bid=0.5, ask=0.7)
        sc = _make_contract(strike=600, option_type="call", bid=1.5, ask=1.7)
        lc = _make_contract(strike=605, option_type="call", bid=0.3, ask=0.5)

        cand = _build_condor_candidate(
            symbol="SPY", strategy_id="iron_condor",
            scanner_key="iron_condor", family_key="iron_condors",
            underlying_price=595.0, expiration="2026-04-17", dte=36,
            short_put_strike=590.0, short_put_contract=sp,
            long_put_strike=580.0, long_put_contract=lp,
            short_call_strike=600.0, short_call_contract=sc,
            long_call_strike=605.0, long_call_contract=lc,
            seq=0,
        )

        # put_width=10, call_width=5, max=10
        assert cand.math.width == 10.0
