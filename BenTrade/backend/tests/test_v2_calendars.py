"""V2 calendar / diagonal family tests — Prompt 12.

Tests for:
1. Calendar call/put construction (Phase B) — same-strike pairing,
   multi-expiry, shared-strike enumeration, min DTE spread.
2. Diagonal call/put construction (Phase B) — different-strike pairing,
   max_strike_shift, generation cap.
3. Calendar structural checks (Phase C) — 2 legs, same type,
   short+long, different expirations, temporal ordering, same strike.
4. Diagonal structural checks (Phase C) — same as calendar but
   enforces different strikes.
5. Calendar math (Phase E) — net_debit (trustworthy), max_loss
   (approximate), informational fields (max_profit, breakeven,
   POP, EV, RoR all None with notes).
6. Diagonal math (Phase E) — same as calendar plus width = strike shift.
7. Math verification — existing 2-leg paths handle calendar/diagonal
   net_debit and max_loss; None fields are auto-skipped.
8. Hygiene integration (Phase D2) — quote/liquidity/dedup on calendars.
9. End-to-end pipeline run via base scanner with multi-expiry support.
10. Reason code registry — new cal_invalid_geometry code.
11. Registry — family is implemented and loadable.
12. Dedup key — includes expiration_back for multi-expiry uniqueness.
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
    REJECT_CAL_INVALID_GEOMETRY,
    REJECT_MALFORMED_LEGS,
    is_valid_reject_code,
    is_valid_code,
    get_code_info,
    all_reject_codes,
)
from app.services.scanner_v2.families.calendars import (
    CalendarsV2Scanner,
    _build_calendar_candidate,
    _contracts_by_type,
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
    verify_max_loss,
    verify_ror,
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
    bid: float | None = 3.00,
    ask: float | None = 3.20,
    delta: float | None = -0.30,
    oi: int | None = 5000,
    volume: int | None = 800,
    expiration: str = "2026-04-17",
    root: str = "SPY",
) -> V2OptionContract:
    """Build a V2OptionContract for testing."""
    mid = ((bid + ask) / 2) if bid is not None and ask is not None else None
    return V2OptionContract(
        symbol=f"{root}{'P' if option_type == 'put' else 'C'}{int(strike * 1000):08d}",
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


def _make_calendar(
    *,
    strike: float = 580.0,
    option_type: str = "call",
    near_exp: str = "2026-03-20",
    far_exp: str = "2026-04-17",
    near_bid: float | None = 3.00,
    near_ask: float | None = 3.20,
    far_bid: float | None = 5.50,
    far_ask: float | None = 5.80,
    near_delta: float | None = -0.35,
    far_delta: float | None = -0.40,
    oi: int | None = 5000,
    volume: int | None = 800,
    strategy_id: str = "calendar_call_spread",
) -> V2Candidate:
    """Build a valid calendar spread candidate for testing."""
    short_near = V2Leg(
        index=0, side="short", strike=strike, option_type=option_type,
        expiration=near_exp, bid=near_bid, ask=near_ask,
        mid=((near_bid + near_ask) / 2) if near_bid and near_ask else None,
        delta=near_delta, open_interest=oi, volume=volume,
    )
    long_far = V2Leg(
        index=1, side="long", strike=strike, option_type=option_type,
        expiration=far_exp, bid=far_bid, ask=far_ask,
        mid=((far_bid + far_ask) / 2) if far_bid and far_ask else None,
        delta=far_delta, open_interest=oi, volume=volume,
    )
    net_debit = round(far_ask - near_bid, 4) if far_ask and near_bid else None
    return V2Candidate(
        candidate_id=f"SPY|{strategy_id}|{near_exp}:{far_exp}|{strike}|0",
        scanner_key=strategy_id,
        strategy_id=strategy_id,
        family_key="calendars",
        symbol="SPY",
        underlying_price=585.0,
        expiration=near_exp,
        expiration_back=far_exp,
        dte=8,
        dte_back=36,
        legs=[short_near, long_far],
        math=V2RecomputedMath(net_debit=net_debit),
    )


def _make_diagonal(
    *,
    near_strike: float = 580.0,
    far_strike: float = 585.0,
    option_type: str = "call",
    near_exp: str = "2026-03-20",
    far_exp: str = "2026-04-17",
    near_bid: float | None = 3.00,
    near_ask: float | None = 3.20,
    far_bid: float | None = 4.50,
    far_ask: float | None = 4.80,
    oi: int | None = 5000,
    volume: int | None = 800,
    strategy_id: str = "diagonal_call_spread",
) -> V2Candidate:
    """Build a valid diagonal spread candidate for testing."""
    short_near = V2Leg(
        index=0, side="short", strike=near_strike, option_type=option_type,
        expiration=near_exp, bid=near_bid, ask=near_ask,
        mid=((near_bid + near_ask) / 2) if near_bid and near_ask else None,
        delta=-0.35, open_interest=oi, volume=volume,
    )
    long_far = V2Leg(
        index=1, side="long", strike=far_strike, option_type=option_type,
        expiration=far_exp, bid=far_bid, ask=far_ask,
        mid=((far_bid + far_ask) / 2) if far_bid and far_ask else None,
        delta=-0.30, open_interest=oi, volume=volume,
    )
    net_debit = round(far_ask - near_bid, 4) if far_ask and near_bid else None
    width = abs(far_strike - near_strike)
    return V2Candidate(
        candidate_id=f"SPY|{strategy_id}|{near_exp}:{far_exp}|{near_strike}-{far_strike}|0",
        scanner_key=strategy_id,
        strategy_id=strategy_id,
        family_key="calendars",
        symbol="SPY",
        underlying_price=585.0,
        expiration=near_exp,
        expiration_back=far_exp,
        dte=8,
        dte_back=36,
        legs=[short_near, long_far],
        math=V2RecomputedMath(net_debit=net_debit, width=width),
    )


def _make_bucket(
    expiration: str,
    dte: int,
    contracts: list[V2OptionContract],
) -> V2ExpiryBucket:
    """Build a V2ExpiryBucket from contracts."""
    entries = [V2StrikeEntry(strike=c.strike, contract=c) for c in contracts]
    return V2ExpiryBucket(
        expiration=expiration,
        dte=dte,
        strikes=entries,
        strike_count=len(entries),
    )


def _make_universe(
    buckets: dict[str, V2ExpiryBucket],
    symbol: str = "SPY",
    price: float = 585.0,
) -> V2NarrowedUniverse:
    """Build a V2NarrowedUniverse for testing."""
    return V2NarrowedUniverse(
        underlying=V2UnderlyingSnapshot(symbol=symbol, price=price),
        expiry_buckets=buckets,
        diagnostics=V2NarrowingDiagnostics(),
    )


def _make_chain(
    strikes: list[float],
    option_type: str = "call",
    expirations: list[str] | None = None,
    bid: float = 3.00,
    ask: float = 3.20,
    oi: int = 5000,
    volume: int = 800,
) -> dict:
    """Build a raw Tradier-shaped chain for end-to-end tests."""
    if expirations is None:
        expirations = ["2026-03-20", "2026-04-17"]
    options = []
    for exp in expirations:
        for strike in strikes:
            options.append({
                "symbol": f"SPY{exp.replace('-', '')}{'P' if option_type == 'put' else 'C'}{int(strike * 1000):08d}",
                "root_symbol": "SPY",
                "strike": strike,
                "option_type": option_type,
                "expiration_date": exp,
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2,
                "greeks": {"delta": -0.30, "gamma": 0.02, "theta": -0.05, "vega": 0.15},
                "open_interest": oi,
                "volume": volume,
            })
    return {"options": {"option": options}}


# =====================================================================
#  Calendar Construction (Phase B)
# =====================================================================

class TestCalendarConstruction:
    """Calendar spread construction tests."""

    def test_calendar_call_same_strike_pairing(self):
        """Calendar finds shared strikes across near and far buckets."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=580, option_type="call", expiration="2026-03-20"),
            _make_contract(strike=585, option_type="call", expiration="2026-03-20"),
            _make_contract(strike=590, option_type="call", expiration="2026-03-20"),
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=580, option_type="call", expiration="2026-04-17"),
            _make_contract(strike=585, option_type="call", expiration="2026-04-17"),
            # 590 missing in far → no calendar at 590
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="calendar_call_spread",
            scanner_key="calendar_call_spread", context={},
            narrowed_universe=universe,
        )
        assert len(cands) == 2  # 580 and 585
        # All legs should be call
        for c in cands:
            assert all(l.option_type == "call" for l in c.legs)

    def test_calendar_put_construction(self):
        """Calendar put spread constructs with put contracts only."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=575, option_type="put", expiration="2026-03-20"),
            _make_contract(strike=575, option_type="call", expiration="2026-03-20"),
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=575, option_type="put", expiration="2026-04-17"),
            _make_contract(strike=575, option_type="call", expiration="2026-04-17"),
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="calendar_put_spread",
            scanner_key="calendar_put_spread", context={},
            narrowed_universe=universe,
        )
        assert len(cands) == 1
        assert all(l.option_type == "put" for l in cands[0].legs)

    def test_calendar_leg_ordering(self):
        """Leg 0 is short near, leg 1 is long far."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=580, option_type="call", expiration="2026-03-20"),
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=580, option_type="call", expiration="2026-04-17"),
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="calendar_call_spread",
            scanner_key="calendar_call_spread", context={},
            narrowed_universe=universe,
        )
        assert len(cands) == 1
        c = cands[0]
        assert c.legs[0].side == "short"
        assert c.legs[0].expiration == "2026-03-20"
        assert c.legs[1].side == "long"
        assert c.legs[1].expiration == "2026-04-17"

    def test_calendar_multi_expiry_pairing(self):
        """Three expirations produce all valid (near, far) pairs."""
        scanner = CalendarsV2Scanner()
        buckets = {}
        for exp, dte in [("2026-03-20", 8), ("2026-04-17", 36), ("2026-05-15", 64)]:
            buckets[exp] = _make_bucket(exp, dte, [
                _make_contract(strike=580, option_type="call", expiration=exp),
            ])
        universe = _make_universe(buckets)
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="calendar_call_spread",
            scanner_key="calendar_call_spread", context={},
            narrowed_universe=universe,
        )
        # 3 choose 2 = 3 pairs: (03/20, 04/17), (03/20, 05/15), (04/17, 05/15)
        assert len(cands) == 3

    def test_calendar_min_dte_spread(self):
        """Pairs with DTE spread < min_dte_spread are skipped."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=580, option_type="call", expiration="2026-03-20"),
        ])
        # Only 3 days later — too close
        far = _make_bucket("2026-03-23", 11, [
            _make_contract(strike=580, option_type="call", expiration="2026-03-23"),
        ])
        universe = _make_universe({"2026-03-20": near, "2026-03-23": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="calendar_call_spread",
            scanner_key="calendar_call_spread",
            context={"min_dte_spread": 7},
            narrowed_universe=universe,
        )
        assert len(cands) == 0

    def test_calendar_expiration_back_set(self):
        """Calendar candidates have expiration_back and dte_back set."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=580, option_type="call", expiration="2026-03-20"),
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=580, option_type="call", expiration="2026-04-17"),
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="calendar_call_spread",
            scanner_key="calendar_call_spread", context={},
            narrowed_universe=universe,
        )
        c = cands[0]
        assert c.expiration == "2026-03-20"
        assert c.expiration_back == "2026-04-17"
        assert c.dte == 8
        assert c.dte_back == 36

    def test_calendar_empty_universe(self):
        """Empty universe returns no candidates."""
        scanner = CalendarsV2Scanner()
        universe = _make_universe({})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="calendar_call_spread",
            scanner_key="calendar_call_spread", context={},
            narrowed_universe=universe,
        )
        assert cands == []

    def test_calendar_generation_cap(self):
        """Generation cap prevents combinatorial explosion."""
        scanner = CalendarsV2Scanner()
        # 10 strikes × 2 expirations → 10 calendars per pair
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=s, option_type="call", expiration="2026-03-20")
            for s in range(570, 600, 3)
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=s, option_type="call", expiration="2026-04-17")
            for s in range(570, 600, 3)
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="calendar_call_spread",
            scanner_key="calendar_call_spread",
            context={"generation_cap": 5},
            narrowed_universe=universe,
        )
        assert len(cands) == 5

    def test_calendar_generation_cap_string_context(self):
        """generation_cap from context may be a string — int() cast required."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=s, option_type="call", expiration="2026-03-20")
            for s in range(570, 600, 3)
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=s, option_type="call", expiration="2026-04-17")
            for s in range(570, 600, 3)
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="calendar_call_spread",
            scanner_key="calendar_call_spread",
            context={"generation_cap": "5"},
            narrowed_universe=universe,
        )
        assert len(cands) == 5

    def test_calendar_no_shared_strikes(self):
        """No candidates when near and far have no shared strikes."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=570, option_type="call", expiration="2026-03-20"),
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=590, option_type="call", expiration="2026-04-17"),
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="calendar_call_spread",
            scanner_key="calendar_call_spread", context={},
            narrowed_universe=universe,
        )
        assert len(cands) == 0


# =====================================================================
#  Diagonal Construction (Phase B)
# =====================================================================

class TestDiagonalConstruction:
    """Diagonal spread construction tests."""

    def test_diagonal_call_different_strikes(self):
        """Diagonal pairs different strikes across expirations."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=580, option_type="call", expiration="2026-03-20"),
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=585, option_type="call", expiration="2026-04-17"),
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="diagonal_call_spread",
            scanner_key="diagonal_call_spread", context={},
            narrowed_universe=universe,
        )
        assert len(cands) == 1
        c = cands[0]
        assert c.legs[0].strike == 580  # short near
        assert c.legs[1].strike == 585  # long far

    def test_diagonal_excludes_same_strike(self):
        """Diagonal skips same-strike pairs (those are calendars)."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=580, option_type="call", expiration="2026-03-20"),
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=580, option_type="call", expiration="2026-04-17"),
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="diagonal_call_spread",
            scanner_key="diagonal_call_spread", context={},
            narrowed_universe=universe,
        )
        assert len(cands) == 0

    def test_diagonal_max_strike_shift(self):
        """Pairs beyond max_strike_shift are excluded."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=570, option_type="call", expiration="2026-03-20"),
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=585, option_type="call", expiration="2026-04-17"),
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        # shift = 15, cap = 10
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="diagonal_call_spread",
            scanner_key="diagonal_call_spread",
            context={"max_strike_shift": 10.0},
            narrowed_universe=universe,
        )
        assert len(cands) == 0

    def test_diagonal_put_construction(self):
        """Diagonal put spread uses put contracts only."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=575, option_type="put", expiration="2026-03-20"),
            _make_contract(strike=575, option_type="call", expiration="2026-03-20"),
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=580, option_type="put", expiration="2026-04-17"),
            _make_contract(strike=580, option_type="call", expiration="2026-04-17"),
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="diagonal_put_spread",
            scanner_key="diagonal_put_spread", context={},
            narrowed_universe=universe,
        )
        assert len(cands) == 1
        assert all(l.option_type == "put" for l in cands[0].legs)

    def test_diagonal_generation_cap(self):
        """Diagonal respects generation cap."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=s, option_type="call", expiration="2026-03-20")
            for s in range(570, 600, 5)
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=s, option_type="call", expiration="2026-04-17")
            for s in range(570, 600, 5)
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="diagonal_call_spread",
            scanner_key="diagonal_call_spread",
            context={"generation_cap": 3},
            narrowed_universe=universe,
        )
        assert len(cands) == 3

    def test_diagonal_candidate_id_format(self):
        """Diagonal candidate_id includes both strikes."""
        scanner = CalendarsV2Scanner()
        near = _make_bucket("2026-03-20", 8, [
            _make_contract(strike=580, option_type="call", expiration="2026-03-20"),
        ])
        far = _make_bucket("2026-04-17", 36, [
            _make_contract(strike=585, option_type="call", expiration="2026-04-17"),
        ])
        universe = _make_universe({"2026-03-20": near, "2026-04-17": far})
        cands = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=585.0,
            expirations=[], strategy_id="diagonal_call_spread",
            scanner_key="diagonal_call_spread", context={},
            narrowed_universe=universe,
        )
        c = cands[0]
        # Strikes may format as int or float depending on contract
        assert "580" in c.candidate_id and "585" in c.candidate_id
        assert "2026-03-20:2026-04-17" in c.candidate_id


# =====================================================================
#  Calendar Structural Checks (Phase C)
# =====================================================================

class TestCalendarStructuralChecks:
    """Calendar-specific structural validation."""

    def test_valid_calendar(self):
        """Valid calendar passes all structural checks."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        checks = scanner.family_structural_checks(cand)
        assert all(c.passed for c in checks)
        assert not cand.diagnostics.reject_reasons

    def test_wrong_leg_count(self):
        """Non-2-leg candidate is rejected."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        cand.legs.append(V2Leg(
            index=2, side="long", strike=580, option_type="call",
            expiration="2026-05-15", bid=7.0, ask=7.20,
        ))
        checks = scanner.family_structural_checks(cand)
        assert any(not c.passed for c in checks)
        assert "v2_cal_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_different_option_types_rejected(self):
        """Mixed option types are rejected."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        cand.legs[1].option_type = "put"
        checks = scanner.family_structural_checks(cand)
        assert "v2_cal_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_same_expiry_rejected(self):
        """Same expiration on both legs is rejected."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        cand.legs[1].expiration = cand.legs[0].expiration
        checks = scanner.family_structural_checks(cand)
        assert "v2_cal_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_temporal_inversion_rejected(self):
        """Short leg must expire before long leg."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        # Swap expirations but keep sides
        cand.legs[0].expiration = "2026-04-17"  # short now has later exp
        cand.legs[1].expiration = "2026-03-20"  # long now has earlier exp
        checks = scanner.family_structural_checks(cand)
        assert "v2_cal_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_both_short_rejected(self):
        """Both legs short is rejected."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        cand.legs[1].side = "short"
        checks = scanner.family_structural_checks(cand)
        assert "v2_cal_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_calendar_different_strikes_rejected(self):
        """Calendar (not diagonal) rejects different strikes."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar(strategy_id="calendar_call_spread")
        cand.legs[1].strike = 585.0
        checks = scanner.family_structural_checks(cand)
        assert "v2_cal_invalid_geometry" in cand.diagnostics.reject_reasons


# =====================================================================
#  Diagonal Structural Checks (Phase C)
# =====================================================================

class TestDiagonalStructuralChecks:
    """Diagonal-specific structural validation."""

    def test_valid_diagonal(self):
        """Valid diagonal passes all structural checks."""
        scanner = CalendarsV2Scanner()
        cand = _make_diagonal()
        checks = scanner.family_structural_checks(cand)
        assert all(c.passed for c in checks)
        assert not cand.diagnostics.reject_reasons

    def test_diagonal_same_strike_rejected(self):
        """Diagonal rejects same-strike legs."""
        scanner = CalendarsV2Scanner()
        cand = _make_diagonal(strategy_id="diagonal_call_spread")
        cand.legs[1].strike = cand.legs[0].strike  # same strike
        checks = scanner.family_structural_checks(cand)
        assert "v2_cal_invalid_geometry" in cand.diagnostics.reject_reasons

    def test_diagonal_temporal_ordering(self):
        """Diagonal enforces short=near, long=far temporal ordering."""
        scanner = CalendarsV2Scanner()
        cand = _make_diagonal()
        # Valid: short is near, long is far
        checks = scanner.family_structural_checks(cand)
        assert all(c.passed for c in checks)


# =====================================================================
#  Calendar Math (Phase E)
# =====================================================================

class TestCalendarMath:
    """Calendar-specific math recomputation."""

    def test_net_debit_computation(self):
        """net_debit = far_leg.ask - near_leg.bid."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar(near_bid=3.00, far_ask=5.80)
        math = scanner.family_math(cand)
        # far_ask(5.80) - near_bid(3.00) = 2.80
        assert math.net_debit == pytest.approx(2.80, abs=0.01)

    def test_max_loss_is_debit(self):
        """max_loss ≈ net_debit × 100."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar(near_bid=3.00, far_ask=5.80)
        math = scanner.family_math(cand)
        assert math.max_loss == pytest.approx(280.0, abs=0.50)

    def test_max_profit_is_none(self):
        """max_profit is deferred (None) with informational note."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        math = scanner.family_math(cand)
        assert math.max_profit is None
        assert "DEFERRED" in math.notes.get("max_profit", "")

    def test_breakeven_is_empty(self):
        """Breakeven is empty list with informational note."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        math = scanner.family_math(cand)
        assert math.breakeven == []
        assert "DEFERRED" in math.notes.get("breakeven", "")

    def test_pop_is_none(self):
        """POP is None with informational note."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        math = scanner.family_math(cand)
        assert math.pop is None
        assert "DEFERRED" in math.notes.get("pop", "")

    def test_ev_is_none(self):
        """EV is None with informational note."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        math = scanner.family_math(cand)
        assert math.ev is None
        assert "DEFERRED" in math.notes.get("ev", "")

    def test_ror_is_none(self):
        """RoR is None with informational note."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        math = scanner.family_math(cand)
        assert math.ror is None
        assert "DEFERRED" in math.notes.get("ror", "")

    def test_calendar_width_is_none(self):
        """Calendar (same-strike) has width = None."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar(strike=580.0)
        math = scanner.family_math(cand)
        assert math.width is None

    def test_missing_bid_ask(self):
        """Missing bid/ask produces None net_debit with note."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar(near_bid=None, far_ask=None)
        math = scanner.family_math(cand)
        assert math.net_debit is None
        assert "missing" in math.notes.get("net_debit", "")

    def test_zero_or_negative_debit(self):
        """Zero/negative debit produces None max_loss."""
        scanner = CalendarsV2Scanner()
        # far cheaper than near → negative debit (credit)
        cand = _make_calendar(near_bid=6.00, far_ask=3.00)
        math = scanner.family_math(cand)
        assert math.net_debit == pytest.approx(-3.0, abs=0.01)
        assert math.max_loss is None  # not standard calendar


# =====================================================================
#  Diagonal Math (Phase E)
# =====================================================================

class TestDiagonalMath:
    """Diagonal-specific math recomputation."""

    def test_diagonal_width_is_strike_shift(self):
        """Diagonal width = |far_strike - near_strike|."""
        scanner = CalendarsV2Scanner()
        cand = _make_diagonal(near_strike=580.0, far_strike=585.0)
        math = scanner.family_math(cand)
        assert math.width == pytest.approx(5.0, abs=0.01)

    def test_diagonal_net_debit(self):
        """Diagonal net_debit = far_ask - near_bid."""
        scanner = CalendarsV2Scanner()
        cand = _make_diagonal(near_bid=3.00, far_ask=4.80)
        math = scanner.family_math(cand)
        assert math.net_debit == pytest.approx(1.80, abs=0.01)

    def test_diagonal_informational_fields(self):
        """Diagonal has same informational-only fields as calendar."""
        scanner = CalendarsV2Scanner()
        cand = _make_diagonal()
        math = scanner.family_math(cand)
        assert math.max_profit is None
        assert math.pop is None
        assert math.ev is None
        assert math.breakeven == []


# =====================================================================
#  Math Verification
# =====================================================================

class TestMathVerification:
    """Verify math verification functions handle calendars correctly."""

    def test_verify_net_debit_2leg_path(self):
        """Standard 2-leg debit path handles calendar net_debit."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar(near_bid=3.00, far_ask=5.80)
        cand.math = scanner.family_math(cand)
        result = verify_net_credit_or_debit(cand, family_key="calendars")
        # Should pass — 2-leg debit: long.ask - short.bid = 5.80 - 3.00 = 2.80
        assert result.passed or result.status == "skipped"

    def test_verify_max_loss_debit_path(self):
        """Standard debit path handles calendar max_loss."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar(near_bid=3.00, far_ask=5.80)
        cand.math = scanner.family_math(cand)
        result = verify_max_loss(cand, family_key="calendars")
        # max_loss = net_debit × 100 = 2.80 × 100 = 280
        assert result.passed or result.status == "skipped"

    def test_verify_width_skipped_for_calendar(self):
        """Width is None for calendars → verify_width is skipped."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        cand.math = scanner.family_math(cand)
        result = verify_width(cand, family_key="calendars")
        assert result.status == "skipped"

    def test_verify_breakeven_skipped(self):
        """Empty breakeven → verify_breakeven is skipped."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        cand.math = scanner.family_math(cand)
        result = verify_breakeven(cand, family_key="calendars")
        assert result.status == "skipped"

    def test_verify_ror_skipped(self):
        """None RoR → verify_ror is skipped."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        cand.math = scanner.family_math(cand)
        result = verify_ror(cand, family_key="calendars")
        assert result.status == "skipped"

    def test_full_math_verification_no_failures(self):
        """Full run_math_verification passes for valid calendar."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar(near_bid=3.00, far_ask=5.80)
        cand.math = scanner.family_math(cand)
        summary = run_math_verification(cand, family_key="calendars")
        assert not summary.has_failures

    def test_diagonal_width_verification(self):
        """Diagonal width verified against strike distance."""
        scanner = CalendarsV2Scanner()
        cand = _make_diagonal(near_strike=580.0, far_strike=585.0)
        cand.math = scanner.family_math(cand)
        result = verify_width(cand, family_key="calendars")
        # 2-leg path: |short.strike - long.strike| = |580 - 585| = 5
        assert result.passed


# =====================================================================
#  Hygiene Integration (Phase D2)
# =====================================================================

class TestHygieneIntegration:
    """Quote/liquidity/dedup integration with calendars."""

    def test_phase_d_passes_valid_calendar(self):
        """Phase D passes a properly-quoted calendar."""
        cand = _make_calendar()
        result = phase_d_quote_liquidity_sanity([cand])
        assert not result[0].diagnostics.reject_reasons

    def test_phase_d_rejects_missing_quotes(self):
        """Phase D rejects calendar with missing bid/ask."""
        cand = _make_calendar(near_bid=None)
        result = phase_d_quote_liquidity_sanity([cand])
        assert result[0].diagnostics.reject_reasons

    def test_phase_d2_dedup_with_back_expiry(self):
        """Dedup includes back expiration — different back exps are unique."""
        scanner = CalendarsV2Scanner()
        cand1 = _make_calendar(far_exp="2026-04-17")
        cand2 = _make_calendar(far_exp="2026-05-15")
        key1 = scanner.family_dedup_key(cand1)
        key2 = scanner.family_dedup_key(cand2)
        assert key1 != key2

    def test_phase_d2_dedup_same_trade(self):
        """Identical calendars produce the same dedup key."""
        scanner = CalendarsV2Scanner()
        cand1 = _make_calendar()
        cand2 = _make_calendar()
        key1 = scanner.family_dedup_key(cand1)
        key2 = scanner.family_dedup_key(cand2)
        assert key1 == key2


# =====================================================================
#  End-to-End Pipeline (run())
# =====================================================================

class TestEndToEnd:
    """Full pipeline run via CalendarsV2Scanner.run()."""

    def test_calendar_call_e2e(self):
        """End-to-end calendar call scan produces valid results."""
        scanner = CalendarsV2Scanner()
        chain = _make_chain(
            strikes=[575.0, 580.0, 585.0, 590.0],
            option_type="call",
            expirations=["2026-03-20", "2026-04-17"],
        )
        result = scanner.run(
            scanner_key="calendar_call_spread",
            strategy_id="calendar_call_spread",
            symbol="SPY",
            chain=chain,
            underlying_price=585.0,
        )
        assert result.total_constructed > 0
        assert result.family_key == "calendars"
        assert result.scanner_version == "2.0.0"

    def test_diagonal_call_e2e(self):
        """End-to-end diagonal call scan produces results."""
        scanner = CalendarsV2Scanner()
        chain = _make_chain(
            strikes=[575.0, 580.0, 585.0],
            option_type="call",
            expirations=["2026-03-20", "2026-04-17"],
        )
        result = scanner.run(
            scanner_key="diagonal_call_spread",
            strategy_id="diagonal_call_spread",
            symbol="SPY",
            chain=chain,
            underlying_price=585.0,
        )
        assert result.total_constructed > 0

    def test_e2e_multi_expiry_not_rejected(self):
        """Calendar candidates are NOT rejected for multi-expiry.

        Verifies that require_same_expiry=False works correctly
        in the pipeline.
        """
        scanner = CalendarsV2Scanner()
        chain = _make_chain(
            strikes=[580.0],
            option_type="call",
            expirations=["2026-03-20", "2026-04-17"],
        )
        result = scanner.run(
            scanner_key="calendar_call_spread",
            strategy_id="calendar_call_spread",
            symbol="SPY",
            chain=chain,
            underlying_price=585.0,
        )
        # Should not be rejected for mismatched expiry
        expiry_rejects = result.reject_reason_counts.get("v2_mismatched_expiry", 0)
        assert expiry_rejects == 0


# =====================================================================
#  Reason Code Registry
# =====================================================================

class TestReasonCodes:
    """Calendar/diagonal reason code integration."""

    def test_cal_invalid_geometry_registered(self):
        """v2_cal_invalid_geometry is a registered reject code."""
        assert is_valid_reject_code("v2_cal_invalid_geometry")

    def test_cal_invalid_geometry_constant(self):
        """REJECT_CAL_INVALID_GEOMETRY constant has correct value."""
        assert REJECT_CAL_INVALID_GEOMETRY == "v2_cal_invalid_geometry"

    def test_cal_code_has_info(self):
        """Calendar reason code has metadata."""
        info = get_code_info("v2_cal_invalid_geometry")
        assert info is not None
        assert info.category == "structural"

    def test_all_reject_codes_count(self):
        """Total reject codes: 28 (27 prior + 1 CAL geometry)."""
        assert len(all_reject_codes()) == 28


# =====================================================================
#  Registry Integration
# =====================================================================

class TestRegistry:
    """Registry integration for calendars family."""

    def test_calendar_family_registered(self):
        """Calendar family exists in registry."""
        meta = get_v2_family("calendar_call_spread")
        assert meta is not None
        assert meta.family_key == "calendars"

    def test_calendar_is_implemented(self):
        """Calendar family is marked as implemented."""
        assert is_v2_supported("calendar_call_spread")
        assert is_v2_supported("calendar_put_spread")

    def test_diagonal_is_implemented(self):
        """Diagonal strategy IDs are supported."""
        assert is_v2_supported("diagonal_call_spread")
        assert is_v2_supported("diagonal_put_spread")

    def test_scanner_loadable(self):
        """Scanner can be instantiated from registry."""
        scanner = get_v2_scanner("calendar_call_spread")
        assert scanner.family_key == "calendars"
        assert scanner.scanner_version == "2.0.0"

    def test_strategy_ids(self):
        """All 4 strategy IDs are registered."""
        meta = get_v2_family("calendar_call_spread")
        assert "calendar_call_spread" in meta.strategy_ids
        assert "calendar_put_spread" in meta.strategy_ids
        assert "diagonal_call_spread" in meta.strategy_ids
        assert "diagonal_put_spread" in meta.strategy_ids

    def test_require_same_expiry_is_false(self):
        """Calendar scanner has require_same_expiry = False."""
        scanner = CalendarsV2Scanner()
        assert scanner.require_same_expiry is False


# =====================================================================
#  Dedup Key
# =====================================================================

class TestDedupKey:
    """Calendar dedup key includes both expirations."""

    def test_includes_expiration_back(self):
        """Dedup key includes expiration_back."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        key = scanner.family_dedup_key(cand)
        # key is (symbol, strategy_id, expiration, expiration_back, leg_tuples)
        assert cand.expiration in key
        assert cand.expiration_back in key

    def test_different_back_expirations_are_unique(self):
        """Different back expirations → different dedup keys."""
        scanner = CalendarsV2Scanner()
        cand1 = _make_calendar(far_exp="2026-04-17")
        cand2 = _make_calendar(far_exp="2026-05-15")
        assert scanner.family_dedup_key(cand1) != scanner.family_dedup_key(cand2)

    def test_leg_expiration_in_key(self):
        """Leg-level expiration is included via leg tuples."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        key = scanner.family_dedup_key(cand)
        # The frozenset of leg tuples includes expiration
        leg_tuples = key[4]  # 5th element
        assert isinstance(leg_tuples, frozenset)
        for side, strike, otype, exp in leg_tuples:
            assert exp in ("2026-03-20", "2026-04-17")


# =====================================================================
#  Informational Notes
# =====================================================================

class TestInformationalNotes:
    """Verify that informational/deferred metrics are properly documented."""

    def test_all_deferred_fields_have_notes(self):
        """Every deferred field has an explanatory note."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        math = scanner.family_math(cand)
        for field in ("max_profit", "breakeven", "pop", "ev", "ror"):
            assert field in math.notes, f"Missing note for {field}"
            assert "DEFERRED" in math.notes[field], f"Note for {field} should say DEFERRED"

    def test_trustworthy_fields_have_notes(self):
        """Trustworthy fields have computation trace notes."""
        scanner = CalendarsV2Scanner()
        cand = _make_calendar()
        math = scanner.family_math(cand)
        assert "net_debit" in math.notes
        assert "max_loss" in math.notes
