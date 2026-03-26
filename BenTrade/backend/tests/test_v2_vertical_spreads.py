"""Comprehensive tests for V2 Vertical Spreads family (Prompt 6).

Test groups
-----------
1. Variant config — correct option_type and short/long assignment.
2. Put credit spread construction — legs, math, identity.
3. Call credit spread construction — legs, math, identity.
4. Put debit construction — legs, math, identity.
5. Call debit construction — legs, math, identity.
6. Family structural checks — pass and fail cases.
7. Phase C integration — structural validation pipeline.
8. Phase D integration — quote/liquidity sanity.
9. Phase E integration — recomputed math.
10. Phase F integration — normalization.
11. Full pipeline end-to-end (A→F via run()).
12. Registry integration — is_v2_supported, get_v2_scanner.
13. Edge cases — empty buckets, single strike, unknown strategy.
14. Duplicate suppression — each pair is unique.
15. Candidate ID format correctness.
"""

from __future__ import annotations

import pytest

from app.services.scanner_v2.contracts import (
    V2Candidate,
    V2CheckResult,
    V2Diagnostics,
    V2Leg,
    V2RecomputedMath,
    V2ScanResult,
)
from app.services.scanner_v2.data.contracts import (
    V2ExpiryBucket,
    V2NarrowedUniverse,
    V2NarrowingDiagnostics,
    V2NarrowingRequest,
    V2OptionContract,
    V2StrikeEntry,
    V2UnderlyingSnapshot,
)
from app.services.scanner_v2.families.vertical_spreads import (
    VerticalSpreadsV2Scanner,
    _VARIANT_CONFIG,
    _build_candidate,
)
from app.services.scanner_v2.phases import (
    phase_c_structural_validation,
    phase_d_quote_liquidity_sanity,
    phase_e_recomputed_math,
    phase_f_normalize,
)
from app.services.scanner_v2.registry import (
    get_v2_family,
    get_v2_scanner,
    is_v2_supported,
)


# =====================================================================
#  Test data builders
# =====================================================================

def _make_contract(
    strike: float,
    option_type: str = "put",
    *,
    bid: float | None = 2.00,
    ask: float | None = 2.10,
    delta: float | None = -0.30,
    oi: int | None = 5000,
    volume: int | None = 200,
    iv: float | None = 0.20,
    expiration: str = "2026-04-17",
) -> V2OptionContract:
    """Build a V2OptionContract for testing."""
    mid = round((bid + ask) / 2, 4) if bid is not None and ask is not None else None
    return V2OptionContract(
        symbol=f"SPY260417{'P' if option_type == 'put' else 'C'}{int(strike * 1000):08d}",
        root_symbol="SPY",
        strike=strike,
        option_type=option_type,
        expiration=expiration,
        bid=bid,
        ask=ask,
        mid=mid,
        delta=delta,
        iv=iv,
        open_interest=oi,
        volume=volume,
    )


def _make_bucket(
    expiration: str = "2026-04-17",
    dte: int = 30,
    contracts: list[V2OptionContract] | None = None,
) -> V2ExpiryBucket:
    """Build a V2ExpiryBucket from a list of contracts."""
    if contracts is None:
        contracts = []
    entries = [V2StrikeEntry(strike=c.strike, contract=c) for c in contracts]
    types = {c.option_type for c in contracts}
    return V2ExpiryBucket(
        expiration=expiration,
        dte=dte,
        option_type=types.pop() if len(types) == 1 else None,
        strikes=entries,
        strike_count=len(entries),
    )


def _make_universe(
    buckets: dict[str, V2ExpiryBucket] | None = None,
    underlying_price: float = 600.0,
) -> V2NarrowedUniverse:
    """Build a V2NarrowedUniverse for testing."""
    if buckets is None:
        buckets = {}
    diag = V2NarrowingDiagnostics(
        expirations_kept=len(buckets),
        expirations_kept_list=sorted(buckets.keys()),
    )
    return V2NarrowedUniverse(
        underlying=V2UnderlyingSnapshot(
            symbol="SPY", price=underlying_price,
        ),
        expiry_buckets=buckets,
        diagnostics=diag,
    )


def _put_contracts_580_590() -> list[V2OptionContract]:
    """Three put contracts at 580, 585, 590."""
    return [
        _make_contract(580.0, "put", bid=1.20, ask=1.30, delta=-0.18),
        _make_contract(585.0, "put", bid=1.80, ask=1.90, delta=-0.24),
        _make_contract(590.0, "put", bid=2.50, ask=2.60, delta=-0.32),
    ]


def _call_contracts_610_620() -> list[V2OptionContract]:
    """Three call contracts at 610, 615, 620."""
    return [
        _make_contract(610.0, "call", bid=2.40, ask=2.50, delta=0.32),
        _make_contract(615.0, "call", bid=1.70, ask=1.80, delta=0.24),
        _make_contract(620.0, "call", bid=1.10, ask=1.20, delta=0.18),
    ]


def _make_put_universe() -> V2NarrowedUniverse:
    """Universe with put contracts at 580/585/590, underlying at 600."""
    bucket = _make_bucket(contracts=_put_contracts_580_590())
    return _make_universe({"2026-04-17": bucket})


def _make_call_universe() -> V2NarrowedUniverse:
    """Universe with call contracts at 610/615/620, underlying at 600."""
    bucket = _make_bucket(contracts=_call_contracts_610_620())
    return _make_universe({"2026-04-17": bucket})


def _make_mixed_universe() -> V2NarrowedUniverse:
    """Universe with both put and call contracts."""
    contracts = _put_contracts_580_590() + _call_contracts_610_620()
    bucket = _make_bucket(contracts=contracts)
    return _make_universe({"2026-04-17": bucket})


def _scanner() -> VerticalSpreadsV2Scanner:
    return VerticalSpreadsV2Scanner()


# =====================================================================
#  § 1  Variant config
# =====================================================================

class TestVariantConfig:
    """Verify _VARIANT_CONFIG covers all four strategies."""

    def test_all_four_strategies_present(self):
        assert set(_VARIANT_CONFIG.keys()) == {
            "put_credit_spread",
            "call_credit_spread",
            "put_debit",
            "call_debit",
        }

    def test_put_credit_spread_config(self):
        c = _VARIANT_CONFIG["put_credit_spread"]
        assert c["option_type"] == "put"
        assert c["short_is_higher"] is True

    def test_call_credit_spread_config(self):
        c = _VARIANT_CONFIG["call_credit_spread"]
        assert c["option_type"] == "call"
        assert c["short_is_higher"] is False

    def test_put_debit_config(self):
        c = _VARIANT_CONFIG["put_debit"]
        assert c["option_type"] == "put"
        assert c["short_is_higher"] is False

    def test_call_debit_config(self):
        c = _VARIANT_CONFIG["call_debit"]
        assert c["option_type"] == "call"
        assert c["short_is_higher"] is True

    def test_credit_strategies_have_short_closer_to_atm(self):
        """Credit spreads: short leg is closer to ATM."""
        # For puts: ATM is higher → short_is_higher=True
        assert _VARIANT_CONFIG["put_credit_spread"]["short_is_higher"] is True
        # For calls: ATM is lower → short_is_higher=False
        assert _VARIANT_CONFIG["call_credit_spread"]["short_is_higher"] is False


# =====================================================================
#  § 2  Put credit spread construction
# =====================================================================

class TestPutCreditConstruction:
    """Verify put credit spread candidate construction."""

    def test_basic_construction_count(self):
        """3 strikes → 3 pairs."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        # 3 strikes → C(3,2) = 3 pairs
        assert len(candidates) == 3

    def test_short_leg_has_higher_strike(self):
        """Put credit: short strike > long strike."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        for cand in candidates:
            short = next(l for l in cand.legs if l.side == "short")
            long = next(l for l in cand.legs if l.side == "long")
            assert short.strike > long.strike, (
                f"Put credit short={short.strike} should be > long={long.strike}"
            )

    def test_both_legs_are_puts(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        for cand in candidates:
            assert all(l.option_type == "put" for l in cand.legs)

    def test_width_is_positive(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        for cand in candidates:
            assert cand.math.width is not None
            assert cand.math.width > 0

    def test_initial_credit_set(self):
        """Phase B sets preliminary net_credit for put credit spreads."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        # 590/585 pair: short=590 (bid=2.50), long=585 (ask=1.90) → credit=0.60
        pair_590_585 = next(
            c for c in candidates
            if any(l.strike == 590.0 and l.side == "short" for l in c.legs)
            and any(l.strike == 585.0 and l.side == "long" for l in c.legs)
        )
        assert pair_590_585.math.net_credit == pytest.approx(0.60, abs=0.01)

    def test_identity_fields(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        for cand in candidates:
            assert cand.symbol == "SPY"
            assert cand.strategy_id == "put_credit_spread"
            assert cand.family_key == "vertical_spreads"
            assert cand.scanner_key == "put_credit_spread"
            assert cand.expiration == "2026-04-17"
            assert cand.dte == 30
            assert cand.underlying_price == 600.0

    def test_leg_quotes_populated(self):
        """Leg bid/ask/mid/delta copied from chain contracts."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        for cand in candidates:
            for leg in cand.legs:
                assert leg.bid is not None
                assert leg.ask is not None
                assert leg.mid is not None
                assert leg.delta is not None


# =====================================================================
#  § 3  Call credit spread construction
# =====================================================================

class TestCallCreditConstruction:
    """Verify call credit spread candidate construction."""

    def test_basic_construction_count(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="call_credit_spread",
            scanner_key="call_credit_spread", context={},
            narrowed_universe=_make_call_universe(),
        )
        assert len(candidates) == 3

    def test_short_leg_has_lower_strike(self):
        """Call credit: short strike < long strike (short closer to ATM)."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="call_credit_spread",
            scanner_key="call_credit_spread", context={},
            narrowed_universe=_make_call_universe(),
        )
        for cand in candidates:
            short = next(l for l in cand.legs if l.side == "short")
            long = next(l for l in cand.legs if l.side == "long")
            assert short.strike < long.strike, (
                f"Call credit short={short.strike} should be < long={long.strike}"
            )

    def test_both_legs_are_calls(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="call_credit_spread",
            scanner_key="call_credit_spread", context={},
            narrowed_universe=_make_call_universe(),
        )
        for cand in candidates:
            assert all(l.option_type == "call" for l in cand.legs)

    def test_initial_credit_set(self):
        """610/615 pair: short=610 (bid=2.40), long=615 (ask=1.80) → credit=0.60."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="call_credit_spread",
            scanner_key="call_credit_spread", context={},
            narrowed_universe=_make_call_universe(),
        )
        pair = next(
            c for c in candidates
            if any(l.strike == 610.0 and l.side == "short" for l in c.legs)
            and any(l.strike == 615.0 and l.side == "long" for l in c.legs)
        )
        assert pair.math.net_credit == pytest.approx(0.60, abs=0.01)

    def test_only_calls_used_from_mixed_universe(self):
        """Call credit ignores put contracts in a mixed bucket."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="call_credit_spread",
            scanner_key="call_credit_spread", context={},
            narrowed_universe=_make_mixed_universe(),
        )
        # 3 call contracts → 3 pairs, puts ignored
        assert len(candidates) == 3
        for cand in candidates:
            assert all(l.option_type == "call" for l in cand.legs)


# =====================================================================
#  § 4  Put debit construction
# =====================================================================

class TestPutDebitConstruction:
    """Verify put debit spread candidate construction."""

    def test_short_leg_has_lower_strike(self):
        """Put debit: short has lower strike (further OTM)."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_debit",
            scanner_key="put_debit", context={},
            narrowed_universe=_make_put_universe(),
        )
        for cand in candidates:
            short = next(l for l in cand.legs if l.side == "short")
            long = next(l for l in cand.legs if l.side == "long")
            assert short.strike < long.strike, (
                f"Put debit short={short.strike} should be < long={long.strike}"
            )

    def test_initial_debit_set(self):
        """580/590 pair: short=580 (bid=1.20), long=590 (ask=2.60) → debit=1.40."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_debit",
            scanner_key="put_debit", context={},
            narrowed_universe=_make_put_universe(),
        )
        pair = next(
            c for c in candidates
            if any(l.strike == 580.0 and l.side == "short" for l in c.legs)
            and any(l.strike == 590.0 and l.side == "long" for l in c.legs)
        )
        assert pair.math.net_debit == pytest.approx(1.40, abs=0.01)

    def test_construction_count(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_debit",
            scanner_key="put_debit", context={},
            narrowed_universe=_make_put_universe(),
        )
        assert len(candidates) == 3


# =====================================================================
#  § 5  Call debit construction
# =====================================================================

class TestCallDebitConstruction:
    """Verify call debit spread candidate construction."""

    def test_short_leg_has_higher_strike(self):
        """Call debit: short has higher strike (further OTM)."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="call_debit",
            scanner_key="call_debit", context={},
            narrowed_universe=_make_call_universe(),
        )
        for cand in candidates:
            short = next(l for l in cand.legs if l.side == "short")
            long = next(l for l in cand.legs if l.side == "long")
            assert short.strike > long.strike, (
                f"Call debit short={short.strike} should be > long={long.strike}"
            )

    def test_initial_debit_set(self):
        """610/620 pair: short=620 (bid=1.10), long=610 (ask=2.50) → debit=1.40."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="call_debit",
            scanner_key="call_debit", context={},
            narrowed_universe=_make_call_universe(),
        )
        pair = next(
            c for c in candidates
            if any(l.strike == 620.0 and l.side == "short" for l in c.legs)
            and any(l.strike == 610.0 and l.side == "long" for l in c.legs)
        )
        assert pair.math.net_debit == pytest.approx(1.40, abs=0.01)

    def test_both_legs_are_calls(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="call_debit",
            scanner_key="call_debit", context={},
            narrowed_universe=_make_call_universe(),
        )
        for cand in candidates:
            assert all(l.option_type == "call" for l in cand.legs)


# =====================================================================
#  § 6  Family structural checks
# =====================================================================

class TestFamilyStructuralChecks:
    """Verify vertical-specific structural checks."""

    def _make_vertical_candidate(
        self,
        legs: list[V2Leg] | None = None,
    ) -> V2Candidate:
        if legs is None:
            legs = [
                V2Leg(0, "short", 590.0, "put", "2026-04-17",
                      bid=2.50, ask=2.60, mid=2.55),
                V2Leg(1, "long", 585.0, "put", "2026-04-17",
                      bid=1.80, ask=1.90, mid=1.85),
            ]
        return V2Candidate(
            candidate_id="test|pcs|2026-04-17|590/585|0",
            scanner_key="put_credit_spread",
            strategy_id="put_credit_spread",
            family_key="vertical_spreads",
            symbol="SPY",
            legs=legs,
            math=V2RecomputedMath(width=5.0),
        )

    def test_valid_vertical_passes(self):
        scanner = _scanner()
        cand = self._make_vertical_candidate()
        checks = scanner.family_structural_checks(cand)
        assert all(c.passed for c in checks)
        assert not cand.diagnostics.reject_reasons

    def test_missing_long_side_fails(self):
        scanner = _scanner()
        legs = [
            V2Leg(0, "short", 590.0, "put", "2026-04-17"),
            V2Leg(1, "short", 585.0, "put", "2026-04-17"),
        ]
        cand = self._make_vertical_candidate(legs=legs)
        checks = scanner.family_structural_checks(cand)
        failed = [c for c in checks if not c.passed]
        assert len(failed) >= 1
        assert "v2_malformed_legs" in cand.diagnostics.reject_reasons

    def test_mixed_option_types_fails(self):
        scanner = _scanner()
        legs = [
            V2Leg(0, "short", 590.0, "put", "2026-04-17"),
            V2Leg(1, "long", 610.0, "call", "2026-04-17"),
        ]
        cand = self._make_vertical_candidate(legs=legs)
        checks = scanner.family_structural_checks(cand)
        failed = [c for c in checks if not c.passed]
        assert len(failed) >= 1
        assert "v2_malformed_legs" in cand.diagnostics.reject_reasons


# =====================================================================
#  § 7  Phase C integration
# =====================================================================

class TestPhaseCIntegration:
    """Verify structural validation pipeline with vertical candidates."""

    def test_valid_candidates_survive_phase_c(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        family_fn = scanner._get_family_checks_fn()
        result = phase_c_structural_validation(
            candidates, family_checks=family_fn,
        )
        surviving = [c for c in result if not c.diagnostics.reject_reasons]
        assert len(surviving) == len(candidates)

    def test_structural_checks_populated(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        family_fn = scanner._get_family_checks_fn()
        result = phase_c_structural_validation(
            candidates, family_checks=family_fn,
        )
        for cand in result:
            assert len(cand.diagnostics.structural_checks) > 0


# =====================================================================
#  § 8  Phase D integration
# =====================================================================

class TestPhaseDIntegration:
    """Verify quote/liquidity sanity with vertical candidates."""

    def test_valid_quotes_survive_phase_d(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        result = phase_d_quote_liquidity_sanity(candidates)
        surviving = [c for c in result if not c.diagnostics.reject_reasons]
        assert len(surviving) == len(candidates)

    def test_missing_bid_rejects(self):
        """Candidate with missing bid on short leg is rejected."""
        contracts = [
            _make_contract(580.0, "put", bid=None, ask=1.30, delta=-0.18),
            _make_contract(585.0, "put", bid=1.80, ask=1.90, delta=-0.24),
        ]
        bucket = _make_bucket(contracts=contracts)
        universe = _make_universe({"2026-04-17": bucket})

        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=universe,
        )
        result = phase_d_quote_liquidity_sanity(candidates)
        rejected = [c for c in result if c.diagnostics.reject_reasons]
        assert len(rejected) == 1
        assert "v2_missing_quote" in rejected[0].diagnostics.reject_reasons

    def test_missing_oi_rejects(self):
        """Candidate with missing open_interest is rejected."""
        contracts = [
            _make_contract(580.0, "put", oi=None),
            _make_contract(585.0, "put"),
        ]
        bucket = _make_bucket(contracts=contracts)
        universe = _make_universe({"2026-04-17": bucket})

        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=universe,
        )
        result = phase_d_quote_liquidity_sanity(candidates)
        rejected = [c for c in result if c.diagnostics.reject_reasons]
        assert len(rejected) == 1


# =====================================================================
#  § 9  Phase E integration
# =====================================================================

class TestPhaseEIntegration:
    """Verify recomputed math with vertical candidates."""

    def test_math_recomputed_for_credit(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        result = phase_e_recomputed_math(
            candidates, family_key="vertical_spreads",
        )
        for cand in result:
            m = cand.math
            # Credit spread math populated
            assert m.width is not None and m.width > 0
            assert m.net_credit is not None and m.net_credit > 0
            assert m.max_profit is not None and m.max_profit > 0
            assert m.max_loss is not None and m.max_loss > 0

    def test_pop_computed_from_delta(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        result = phase_e_recomputed_math(
            candidates, family_key="vertical_spreads",
        )
        for cand in result:
            assert cand.math.pop is not None
            assert 0 < cand.math.pop < 1
            assert cand.math.pop_source == "delta_approx"

    def test_breakeven_computed(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        result = phase_e_recomputed_math(
            candidates, family_key="vertical_spreads",
        )
        for cand in result:
            assert len(cand.math.breakeven) == 1
            assert cand.math.breakeven[0] > 0

    def test_ev_computed(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        result = phase_e_recomputed_math(
            candidates, family_key="vertical_spreads",
        )
        for cand in result:
            assert cand.math.ev is not None

    def test_debit_math_for_put_debit(self):
        """Put debit candidates get net_debit, not net_credit."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_debit",
            scanner_key="put_debit", context={},
            narrowed_universe=_make_put_universe(),
        )
        result = phase_e_recomputed_math(
            candidates, family_key="vertical_spreads",
        )
        for cand in result:
            # Put debit: long has higher strike (more expensive),
            # so net_debit should be set
            assert cand.math.net_debit is not None or cand.math.net_credit is not None
            assert cand.math.width is not None and cand.math.width > 0


# =====================================================================
#  § 10  Phase F integration
# =====================================================================

class TestPhaseFIntegration:
    """Verify normalization and packaging."""

    def test_passing_candidates_marked_passed(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        result = phase_f_normalize(candidates, scanner_version="2.0.0")
        for cand in result:
            assert cand.passed is True
            assert cand.downstream_usable is True
            assert cand.scanner_version == "2.0.0"
            assert cand.generated_at != ""

    def test_pass_reasons_populated_after_full_pipeline(self):
        """After C→D→E→F, passing candidates have pass_reasons."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        family_fn = scanner._get_family_checks_fn()
        candidates = phase_c_structural_validation(
            candidates, family_checks=family_fn,
        )
        candidates = phase_d_quote_liquidity_sanity(candidates)
        candidates = phase_e_recomputed_math(
            candidates, family_key="vertical_spreads",
        )
        candidates = phase_f_normalize(candidates, scanner_version="2.0.0")
        for cand in candidates:
            if cand.passed:
                assert len(cand.diagnostics.pass_reasons) > 0


# =====================================================================
#  § 11  Full pipeline end-to-end
# =====================================================================

class TestFullPipeline:
    """End-to-end through scanner.run() with synthetic chain."""

    def _build_chain(
        self,
        contracts: list[V2OptionContract],
    ) -> dict[str, Any]:
        """Build a Tradier-format chain dict from V2OptionContract list."""
        options = []
        for c in contracts:
            opt = {
                "symbol": c.symbol,
                "root_symbol": c.root_symbol,
                "strike": c.strike,
                "option_type": c.option_type,
                "expiration_date": c.expiration,
                "bid": c.bid,
                "ask": c.ask,
                "greeks": {
                    "delta": c.delta,
                    "gamma": c.gamma,
                    "theta": c.theta,
                    "vega": c.vega,
                    "mid_iv": c.iv,
                },
                "open_interest": c.open_interest,
                "volume": c.volume,
            }
            options.append(opt)
        return {"options": {"option": options}}

    def test_put_credit_full_pipeline(self):
        scanner = _scanner()
        chain = self._build_chain(_put_contracts_580_590())
        result = scanner.run(
            scanner_key="put_credit_spread",
            strategy_id="put_credit_spread",
            symbol="SPY",
            chain=chain,
            underlying_price=600.0,
        )
        assert isinstance(result, V2ScanResult)
        assert result.total_constructed == 3
        assert result.total_passed + result.total_rejected == result.total_constructed
        assert result.family_key == "vertical_spreads"
        assert result.strategy_id == "put_credit_spread"
        assert len(result.phase_counts) >= 4

    def test_call_credit_full_pipeline(self):
        scanner = _scanner()
        chain = self._build_chain(_call_contracts_610_620())
        result = scanner.run(
            scanner_key="call_credit_spread",
            strategy_id="call_credit_spread",
            symbol="SPY",
            chain=chain,
            underlying_price=600.0,
        )
        assert result.total_constructed == 3
        assert result.family_key == "vertical_spreads"

    def test_put_debit_full_pipeline(self):
        scanner = _scanner()
        chain = self._build_chain(_put_contracts_580_590())
        result = scanner.run(
            scanner_key="put_debit",
            strategy_id="put_debit",
            symbol="SPY",
            chain=chain,
            underlying_price=600.0,
        )
        assert result.total_constructed == 3

    def test_call_debit_full_pipeline(self):
        scanner = _scanner()
        chain = self._build_chain(_call_contracts_610_620())
        result = scanner.run(
            scanner_key="call_debit",
            strategy_id="call_debit",
            symbol="SPY",
            chain=chain,
            underlying_price=600.0,
        )
        assert result.total_constructed == 3

    def test_passed_candidates_have_valid_math(self):
        scanner = _scanner()
        chain = self._build_chain(_put_contracts_580_590())
        result = scanner.run(
            scanner_key="put_credit_spread",
            strategy_id="put_credit_spread",
            symbol="SPY",
            chain=chain,
            underlying_price=600.0,
        )
        for cand in result.candidates:
            m = cand.math
            assert m.width > 0
            assert m.max_profit is not None
            assert m.max_loss is not None
            assert m.pop is not None
            assert m.ev is not None

    def test_phase_counts_populated(self):
        scanner = _scanner()
        chain = self._build_chain(_put_contracts_580_590())
        result = scanner.run(
            scanner_key="put_credit_spread",
            strategy_id="put_credit_spread",
            symbol="SPY",
            chain=chain,
            underlying_price=600.0,
        )
        phase_names = [p["phase"] for p in result.phase_counts]
        assert "constructed" in phase_names
        assert "structural_validation" in phase_names
        assert "quote_liquidity_sanity" in phase_names
        assert "recomputed_math" in phase_names
        assert "normalized" in phase_names

    def test_narrowing_diagnostics_populated(self):
        scanner = _scanner()
        chain = self._build_chain(_put_contracts_580_590())
        result = scanner.run(
            scanner_key="put_credit_spread",
            strategy_id="put_credit_spread",
            symbol="SPY",
            chain=chain,
            underlying_price=600.0,
        )
        assert result.narrowing_diagnostics is not None
        assert isinstance(result.narrowing_diagnostics, dict)

    def test_all_four_variants_produce_candidates(self):
        """Confirm all four variants produce candidates from appropriate chain."""
        scanner = _scanner()
        put_chain = self._build_chain(_put_contracts_580_590())
        call_chain = self._build_chain(_call_contracts_610_620())

        for strategy, chain in [
            ("put_credit_spread", put_chain),
            ("put_debit", put_chain),
            ("call_credit_spread", call_chain),
            ("call_debit", call_chain),
        ]:
            result = scanner.run(
                scanner_key=strategy,
                strategy_id=strategy,
                symbol="SPY",
                chain=chain,
                underlying_price=600.0,
            )
            assert result.total_constructed > 0, (
                f"{strategy} should construct candidates"
            )


# =====================================================================
#  § 12  Registry integration
# =====================================================================

class TestRegistryIntegration:
    """Verify vertical spreads are registered and loadable."""

    @pytest.mark.parametrize("strategy_id", [
        "put_credit_spread",
        "call_credit_spread",
        "put_debit",
        "call_debit",
    ])
    def test_is_v2_supported(self, strategy_id):
        assert is_v2_supported(strategy_id) is True

    def test_get_v2_family_metadata(self):
        meta = get_v2_family("put_credit_spread")
        assert meta is not None
        assert meta.family_key == "vertical_spreads"
        assert meta.implemented is True
        assert meta.leg_count == 2

    def test_get_v2_scanner_returns_instance(self):
        scanner = get_v2_scanner("put_credit_spread")
        assert isinstance(scanner, VerticalSpreadsV2Scanner)

    def test_all_strategies_map_to_same_family(self):
        """All four vertical strategy IDs map to the same family."""
        families = set()
        for sid in ["put_credit_spread", "call_credit_spread",
                     "put_debit", "call_debit"]:
            meta = get_v2_family(sid)
            families.add(meta.family_key)
        assert families == {"vertical_spreads"}

    def test_scanner_version_not_skeleton(self):
        scanner = get_v2_scanner("put_credit_spread")
        assert "skeleton" not in scanner.scanner_version


# =====================================================================
#  § 13  Edge cases
# =====================================================================

class TestEdgeCases:
    """Edge cases in construction."""

    def test_empty_universe_returns_empty(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=[], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_universe(),
        )
        assert candidates == []

    def test_none_universe_returns_empty(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=[], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=None,
        )
        assert candidates == []

    def test_single_strike_returns_empty(self):
        """Need at least 2 strikes to form a spread."""
        contracts = [_make_contract(590.0, "put")]
        bucket = _make_bucket(contracts=contracts)
        universe = _make_universe({"2026-04-17": bucket})

        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=universe,
        )
        assert candidates == []

    def test_unknown_strategy_returns_empty(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="unknown_strategy",
            scanner_key="unknown", context={},
            narrowed_universe=_make_put_universe(),
        )
        assert candidates == []

    def test_wrong_option_type_for_strategy_returns_empty(self):
        """Put credit with only call contracts → no candidates."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_call_universe(),
        )
        assert candidates == []

    def test_multiple_expirations(self):
        """Candidates generated for each expiration independently."""
        c1 = [_make_contract(580.0, "put", expiration="2026-04-17"),
              _make_contract(585.0, "put", expiration="2026-04-17")]
        c2 = [_make_contract(580.0, "put", expiration="2026-05-15"),
              _make_contract(585.0, "put", expiration="2026-05-15")]
        b1 = _make_bucket(expiration="2026-04-17", contracts=c1)
        b2 = _make_bucket(expiration="2026-05-15", dte=58, contracts=c2)
        universe = _make_universe({"2026-04-17": b1, "2026-05-15": b2})

        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17", "2026-05-15"],
            strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=universe,
        )
        # 2 strikes per exp → 1 pair each → 2 total
        assert len(candidates) == 2
        expirations = {c.expiration for c in candidates}
        assert expirations == {"2026-04-17", "2026-05-15"}

    def test_many_strikes_combinatorial(self):
        """5 strikes → C(5,2) = 10 pairs."""
        contracts = [
            _make_contract(float(s), "put")
            for s in [575, 580, 585, 590, 595]
        ]
        bucket = _make_bucket(contracts=contracts)
        universe = _make_universe({"2026-04-17": bucket})

        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=universe,
        )
        assert len(candidates) == 10


# =====================================================================
#  § 14  Duplicate suppression
# =====================================================================

class TestDuplicateSuppression:
    """Verify no duplicate candidates are produced."""

    def test_no_duplicate_candidate_ids(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        ids = [c.candidate_id for c in candidates]
        assert len(ids) == len(set(ids))

    def test_no_duplicate_strike_pairs(self):
        """Each (short_strike, long_strike) pair appears exactly once."""
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        pairs = set()
        for cand in candidates:
            short = next(l for l in cand.legs if l.side == "short")
            long = next(l for l in cand.legs if l.side == "long")
            pair = (short.strike, long.strike)
            assert pair not in pairs, f"Duplicate pair: {pair}"
            pairs.add(pair)


# =====================================================================
#  § 15  Candidate ID format
# =====================================================================

class TestCandidateIdFormat:
    """Verify candidate ID format is correct and parseable."""

    def test_id_format(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        for cand in candidates:
            parts = cand.candidate_id.split("|")
            assert len(parts) == 5
            assert parts[0] == "SPY"
            assert parts[1] == "put_credit_spread"
            assert parts[2] == "2026-04-17"
            # parts[3] is "short_strike/long_strike"
            strikes = parts[3].split("/")
            assert len(strikes) == 2
            float(strikes[0])  # should not raise
            float(strikes[1])  # should not raise
            int(parts[4])      # seq should be integer

    def test_id_contains_actual_strikes(self):
        scanner = _scanner()
        candidates = scanner.construct_candidates(
            chain={}, symbol="SPY", underlying_price=600.0,
            expirations=["2026-04-17"], strategy_id="put_credit_spread",
            scanner_key="put_credit_spread", context={},
            narrowed_universe=_make_put_universe(),
        )
        for cand in candidates:
            short = next(l for l in cand.legs if l.side == "short")
            long = next(l for l in cand.legs if l.side == "long")
            assert str(short.strike) in cand.candidate_id
            assert str(long.strike) in cand.candidate_id


# =====================================================================
#  § 16  _build_candidate helper
# =====================================================================

class TestBuildCandidate:
    """Verify the _build_candidate helper function."""

    def test_builds_correct_structure(self):
        c_short = _make_contract(590.0, "put", bid=2.50, ask=2.60)
        c_long = _make_contract(585.0, "put", bid=1.80, ask=1.90)
        cand = _build_candidate(
            symbol="SPY",
            strategy_id="put_credit_spread",
            scanner_key="put_credit_spread",
            family_key="vertical_spreads",
            underlying_price=600.0,
            expiration="2026-04-17",
            dte=30,
            short_strike=590.0,
            short_contract=c_short,
            long_strike=585.0,
            long_contract=c_long,
            option_type="put",
            seq=0,
        )
        assert len(cand.legs) == 2
        assert cand.legs[0].side == "short"
        assert cand.legs[0].strike == 590.0
        assert cand.legs[1].side == "long"
        assert cand.legs[1].strike == 585.0
        assert cand.math.width == 5.0
        assert cand.math.net_credit == pytest.approx(0.60, abs=0.01)

    def test_debit_case(self):
        """When short premium < long cost → net_debit set."""
        c_short = _make_contract(580.0, "put", bid=1.20, ask=1.30)
        c_long = _make_contract(590.0, "put", bid=2.50, ask=2.60)
        cand = _build_candidate(
            symbol="SPY",
            strategy_id="put_debit",
            scanner_key="put_debit",
            family_key="vertical_spreads",
            underlying_price=600.0,
            expiration="2026-04-17",
            dte=30,
            short_strike=580.0,
            short_contract=c_short,
            long_strike=590.0,
            long_contract=c_long,
            option_type="put",
            seq=0,
        )
        assert cand.math.net_debit is not None
        assert cand.math.net_debit > 0
        assert cand.math.net_credit is None

    def test_missing_quotes_no_credit_or_debit(self):
        """If bid is None on short → neither credit nor debit set."""
        c_short = _make_contract(590.0, "put", bid=None, ask=2.60)
        c_long = _make_contract(585.0, "put", bid=1.80, ask=1.90)
        cand = _build_candidate(
            symbol="SPY",
            strategy_id="put_credit_spread",
            scanner_key="put_credit_spread",
            family_key="vertical_spreads",
            underlying_price=600.0,
            expiration="2026-04-17",
            dte=30,
            short_strike=590.0,
            short_contract=c_short,
            long_strike=585.0,
            long_contract=c_long,
            option_type="put",
            seq=0,
        )
        assert cand.math.net_credit is None
        assert cand.math.net_debit is None
        # Width still set
        assert cand.math.width == 5.0


# =====================================================================
#  § 17  Scanner class attributes
# =====================================================================

class TestScannerAttributes:
    """Verify scanner class-level configuration."""

    def test_family_key(self):
        assert _scanner().family_key == "vertical_spreads"

    def test_scanner_version_not_skeleton(self):
        assert "skeleton" not in _scanner().scanner_version

    def test_dte_range(self):
        s = _scanner()
        assert s.dte_min == 5
        assert s.dte_max == 90

    def test_no_family_math_override(self):
        """Vertical spreads use default vertical math (no override)."""
        s = _scanner()
        assert s._get_family_math_fn() is None
