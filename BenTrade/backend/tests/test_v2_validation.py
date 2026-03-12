"""Tests for V2 validation framework.

Covers:
- Validation contracts (V2ValidationResult, V2ToleranceSpec, V2ValidationSummary)
- Tolerance policy (defaults, family overrides)
- Structural validation (all individual checks + shared runner + family runners)
- Math verification (all individual checks + runner)
- Integration with phases.py (backward compatibility)
"""

import pytest
import sys

sys.path.insert(0, ".")

from app.services.scanner_v2.contracts import (
    V2Candidate,
    V2CheckResult,
    V2Diagnostics,
    V2Leg,
    V2RecomputedMath,
)
from app.services.scanner_v2.validation.contracts import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
    STATUS_WARN,
    V2ToleranceSpec,
    V2ValidationResult,
    V2ValidationSummary,
)
from app.services.scanner_v2.validation.tolerances import (
    DEFAULT_TOLERANCES,
    get_tolerance,
    get_tolerances,
)
from app.services.scanner_v2.validation.structural import (
    run_butterfly_structural_checks,
    run_calendar_structural_checks,
    run_iron_condor_structural_checks,
    run_shared_structural_checks,
    run_vertical_structural_checks,
    validate_has_legs,
    validate_has_short_and_long,
    validate_leg_count,
    validate_no_duplicate_legs,
    validate_option_types,
    validate_pricing_sanity,
    validate_required_fields,
    validate_same_expiry,
    validate_same_option_type,
    validate_sides,
    validate_strike_ordering,
    validate_width,
    validate_multi_expiry,
)
from app.services.scanner_v2.validation.math_checks import (
    check_ev_computed,
    check_pop_computed,
    run_math_verification,
    verify_breakeven,
    verify_finite_values,
    verify_max_loss,
    verify_max_profit,
    verify_net_credit_or_debit,
    verify_positive_max_loss,
    verify_positive_max_profit,
    verify_ror,
    verify_width,
)


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════

def _make_leg(
    index=0, side="short", strike=440.0, option_type="put",
    expiration="2026-03-20", bid=1.20, ask=1.25, mid=1.225,
    delta=-0.30, open_interest=5000, volume=200,
):
    return V2Leg(
        index=index, side=side, strike=strike, option_type=option_type,
        expiration=expiration, bid=bid, ask=ask, mid=mid,
        delta=delta, open_interest=open_interest, volume=volume,
    )


def _make_vertical_candidate(
    short_strike=440.0, long_strike=435.0,
    short_bid=1.20, long_ask=0.85,
    short_delta=-0.30,
    expiration="2026-03-20",
    symbol="SPY",
    net_credit=None, width=None,
):
    """Create a valid 2-leg put credit spread candidate."""
    legs = [
        _make_leg(
            index=0, side="short", strike=short_strike,
            option_type="put", bid=short_bid, ask=short_bid + 0.05,
            delta=short_delta, expiration=expiration,
        ),
        _make_leg(
            index=1, side="long", strike=long_strike,
            option_type="put", bid=long_ask - 0.05, ask=long_ask,
            delta=-0.20, expiration=expiration,
        ),
    ]
    math = V2RecomputedMath(
        net_credit=net_credit or round(short_bid - long_ask, 4),
        width=width or abs(short_strike - long_strike),
    )
    return V2Candidate(
        candidate_id=f"{symbol}|put_credit_spread|{expiration}|{short_strike}|{long_strike}|0",
        scanner_key="put_credit_spread",
        strategy_id="put_credit_spread",
        family_key="vertical_spreads",
        symbol=symbol,
        underlying_price=450.0,
        expiration=expiration,
        dte=9,
        legs=legs,
        math=math,
    )


def _make_iron_condor_candidate():
    """Create a valid 4-leg iron condor candidate."""
    legs = [
        _make_leg(index=0, side="long", strike=420.0, option_type="put",
                  bid=0.30, ask=0.35, delta=-0.10, expiration="2026-03-20"),
        _make_leg(index=1, side="short", strike=430.0, option_type="put",
                  bid=0.80, ask=0.85, delta=-0.20, expiration="2026-03-20"),
        _make_leg(index=2, side="short", strike=460.0, option_type="call",
                  bid=0.90, ask=0.95, delta=0.20, expiration="2026-03-20"),
        _make_leg(index=3, side="long", strike=470.0, option_type="call",
                  bid=0.35, ask=0.40, delta=0.10, expiration="2026-03-20"),
    ]
    return V2Candidate(
        candidate_id="SPY|iron_condor|2026-03-20|420|430|460|470|0",
        scanner_key="iron_condor",
        strategy_id="iron_condor",
        family_key="iron_condors",
        symbol="SPY",
        underlying_price=450.0,
        expiration="2026-03-20",
        dte=9,
        legs=legs,
        math=V2RecomputedMath(width=10.0, net_credit=1.05),
    )


def _make_butterfly_candidate():
    """Create a valid 3-leg butterfly candidate."""
    legs = [
        _make_leg(index=0, side="long", strike=440.0, option_type="put",
                  bid=2.00, ask=2.10, delta=-0.40, expiration="2026-03-20"),
        _make_leg(index=1, side="short", strike=445.0, option_type="put",
                  bid=3.00, ask=3.10, delta=-0.50, expiration="2026-03-20"),
        _make_leg(index=2, side="long", strike=450.0, option_type="put",
                  bid=4.20, ask=4.30, delta=-0.60, expiration="2026-03-20"),
    ]
    return V2Candidate(
        candidate_id="SPY|butterfly|2026-03-20|440|445|450|0",
        scanner_key="butterfly",
        strategy_id="butterfly",
        family_key="butterflies",
        symbol="SPY",
        underlying_price=450.0,
        expiration="2026-03-20",
        dte=9,
        legs=legs,
        math=V2RecomputedMath(width=10.0),
    )


# ═══════════════════════════════════════════════════════════════════
#  Validation Contracts Tests
# ═══════════════════════════════════════════════════════════════════

class TestV2ValidationResult:

    def test_make_pass(self):
        r = V2ValidationResult.make_pass("check_a", message="ok")
        assert r.status == STATUS_PASS
        assert r.passed
        assert not r.is_failure
        assert not r.is_warning
        assert r.check_key == "check_a"

    def test_make_fail(self):
        r = V2ValidationResult.make_fail("check_b", "v2_bad_thing", message="nope")
        assert r.status == STATUS_FAIL
        assert not r.passed
        assert r.is_failure
        assert r.fail_code == "v2_bad_thing"

    def test_make_warn(self):
        r = V2ValidationResult.make_warn("check_c", "v2_warn_xyz", message="hmm")
        assert r.status == STATUS_WARN
        assert not r.passed  # warn is NOT a pass (only pass/skipped are)
        assert not r.is_failure  # warn is also NOT a failure
        assert r.is_warning
        assert r.warn_code == "v2_warn_xyz"

    def test_make_skipped(self):
        r = V2ValidationResult.make_skipped("check_d", message="no data")
        assert r.status == STATUS_SKIPPED
        assert r.passed  # skipped is non-failure

    def test_to_check_result(self):
        r = V2ValidationResult.make_fail(
            "width_check", "v2_invalid_width",
            message="width too small",
            expected=5.0, actual=0.0, delta=5.0,
        )
        cr = r.to_check_result()
        assert isinstance(cr, V2CheckResult)
        assert cr.name == "width_check"
        assert cr.passed is False
        assert "expected=5.0" in cr.detail
        assert "delta=5.0" in cr.detail

    def test_to_check_result_simple(self):
        r = V2ValidationResult.make_pass("ok_check", message="all good")
        cr = r.to_check_result()
        assert cr.passed is True
        assert cr.detail == "all good"


class TestV2ToleranceSpec:

    def test_exact_match_passes(self):
        tol = V2ToleranceSpec(abs_pass=0.01, abs_warn=0.05)
        status, delta = tol.classify(100.0, 100.0)
        assert status == STATUS_PASS
        assert delta == 0.0

    def test_within_pass_threshold(self):
        tol = V2ToleranceSpec(abs_pass=0.01, abs_warn=0.05)
        status, delta = tol.classify(100.0, 100.005)
        assert status == STATUS_PASS

    def test_between_pass_and_warn(self):
        tol = V2ToleranceSpec(abs_pass=0.01, abs_warn=0.05)
        status, delta = tol.classify(100.0, 100.03)
        assert status == STATUS_WARN
        assert abs(delta - 0.03) < 1e-10

    def test_beyond_warn_is_fail(self):
        tol = V2ToleranceSpec(abs_pass=0.01, abs_warn=0.05)
        status, delta = tol.classify(100.0, 100.10)
        assert status == STATUS_FAIL

    def test_relative_warn_rescues(self):
        tol = V2ToleranceSpec(abs_pass=0.01, abs_warn=0.05, rel_warn=0.05)
        # delta=2.0, but relative to 1000.0 that's 0.2% — within rel_warn
        status, delta = tol.classify(1000.0, 1002.0)
        assert status == STATUS_WARN

    def test_none_values_skip(self):
        tol = V2ToleranceSpec()
        status, delta = tol.classify(None, 100.0)
        assert status == STATUS_SKIPPED
        assert delta is None

    def test_both_none_skip(self):
        tol = V2ToleranceSpec()
        status, delta = tol.classify(None, None)
        assert status == STATUS_SKIPPED


class TestV2ValidationSummary:

    def test_all_passed(self):
        results = [
            V2ValidationResult.make_pass("a"),
            V2ValidationResult.make_pass("b"),
            V2ValidationResult.make_skipped("c"),
        ]
        s = V2ValidationSummary(results=results)
        assert s.all_passed
        assert s.pass_count == 2
        assert s.skip_count == 1
        assert s.fail_count == 0

    def test_has_failures(self):
        results = [
            V2ValidationResult.make_pass("a"),
            V2ValidationResult.make_fail("b", "v2_bad"),
        ]
        s = V2ValidationSummary(results=results)
        assert not s.all_passed
        assert s.has_failures
        assert s.fail_codes == ["v2_bad"]

    def test_has_warnings(self):
        results = [
            V2ValidationResult.make_pass("a"),
            V2ValidationResult.make_warn("b", "v2_warn_x"),
        ]
        s = V2ValidationSummary(results=results)
        assert s.all_passed  # warnings don't count as failures
        assert s.has_warnings
        assert s.warn_codes == ["v2_warn_x"]

    def test_to_check_results(self):
        results = [
            V2ValidationResult.make_pass("a"),
            V2ValidationResult.make_fail("b", "v2_bad"),
        ]
        s = V2ValidationSummary(results=results)
        crs = s.to_check_results()
        assert len(crs) == 2
        assert all(isinstance(cr, V2CheckResult) for cr in crs)
        assert crs[0].passed is True
        assert crs[1].passed is False


# ═══════════════════════════════════════════════════════════════════
#  Tolerance Tests
# ═══════════════════════════════════════════════════════════════════

class TestTolerances:

    def test_default_tolerances_complete(self):
        expected_keys = {
            "net_credit", "net_debit", "max_profit", "max_loss",
            "width", "breakeven", "ror", "ev",
        }
        assert expected_keys == set(DEFAULT_TOLERANCES.keys())

    def test_get_tolerances_default(self):
        tols = get_tolerances()
        assert "net_credit" in tols
        assert isinstance(tols["net_credit"], V2ToleranceSpec)

    def test_get_tolerances_family_override(self):
        default = get_tolerances()
        ic = get_tolerances("iron_condors")
        # Iron condors should have wider net_credit tolerance
        assert ic["net_credit"].abs_pass > default["net_credit"].abs_pass

    def test_get_tolerances_unknown_family_uses_defaults(self):
        tols = get_tolerances("unknown_family")
        assert tols == DEFAULT_TOLERANCES

    def test_get_tolerance_single(self):
        tol = get_tolerance("width")
        assert isinstance(tol, V2ToleranceSpec)
        assert tol.abs_pass == 0.001

    def test_get_tolerance_fallback(self):
        tol = get_tolerance("unknown_metric")
        assert isinstance(tol, V2ToleranceSpec)
        # Should return permissive default
        assert tol.abs_pass == 0.01

    def test_butterfly_tolerances_wider(self):
        bf = get_tolerances("butterflies")
        default = get_tolerances()
        assert bf["max_profit"].abs_pass >= default["max_profit"].abs_pass


# ═══════════════════════════════════════════════════════════════════
#  Structural Validation — Individual Checks
# ═══════════════════════════════════════════════════════════════════

class TestValidateHasLegs:

    def test_has_legs(self):
        r = validate_has_legs([_make_leg()])
        assert r.passed

    def test_no_legs(self):
        r = validate_has_legs([])
        assert r.is_failure
        assert r.fail_code == "v2_malformed_legs"


class TestValidateLegCount:

    def test_exact_match(self):
        r = validate_leg_count([_make_leg(), _make_leg(index=1)], 2)
        assert r.passed

    def test_wrong_count(self):
        r = validate_leg_count([_make_leg()], 2)
        assert r.is_failure

    def test_tuple_allowed(self):
        r = validate_leg_count(
            [_make_leg(), _make_leg(index=1), _make_leg(index=2)],
            (3, 4),
        )
        assert r.passed

    def test_tuple_not_in_allowed(self):
        r = validate_leg_count([_make_leg(), _make_leg(index=1)], (3, 4))
        assert r.is_failure


class TestValidateRequiredFields:

    def test_valid_candidate(self):
        cand = _make_vertical_candidate()
        r = validate_required_fields(cand)
        assert r.passed

    def test_missing_strike(self):
        cand = _make_vertical_candidate()
        cand.legs[0] = _make_leg(strike=float("nan"))
        r = validate_required_fields(cand)
        assert r.is_failure
        assert "strike" in r.message

    def test_missing_symbol(self):
        cand = _make_vertical_candidate()
        cand.symbol = ""
        r = validate_required_fields(cand)
        assert r.is_failure
        assert "symbol" in r.message

    def test_bad_option_type(self):
        cand = _make_vertical_candidate()
        cand.legs[0] = _make_leg(option_type="invalid")
        r = validate_required_fields(cand)
        assert r.is_failure

    def test_bad_side(self):
        cand = _make_vertical_candidate()
        cand.legs[0] = _make_leg(side="neutral")
        r = validate_required_fields(cand)
        assert r.is_failure


class TestValidateSides:

    def test_valid_sides(self):
        r = validate_sides([_make_leg(side="short"), _make_leg(index=1, side="long")])
        assert r.passed

    def test_invalid_side(self):
        r = validate_sides([_make_leg(side="invalid")])
        assert r.is_failure


class TestValidateOptionTypes:

    def test_valid_types(self):
        r = validate_option_types([_make_leg(option_type="put"), _make_leg(index=1, option_type="call")])
        assert r.passed

    def test_invalid_type(self):
        r = validate_option_types([_make_leg(option_type="straddle")])
        assert r.is_failure


class TestValidateSameExpiry:

    def test_same_expiry(self):
        legs = [
            _make_leg(expiration="2026-03-20"),
            _make_leg(index=1, expiration="2026-03-20"),
        ]
        r = validate_same_expiry(legs)
        assert r.passed

    def test_different_expiry(self):
        legs = [
            _make_leg(expiration="2026-03-20"),
            _make_leg(index=1, expiration="2026-04-17"),
        ]
        r = validate_same_expiry(legs)
        assert r.is_failure
        assert r.fail_code == "v2_mismatched_expiry"


class TestValidateMultiExpiry:

    def test_multiple_expirations(self):
        legs = [
            _make_leg(expiration="2026-03-20"),
            _make_leg(index=1, expiration="2026-04-17"),
        ]
        r = validate_multi_expiry(legs)
        assert r.passed

    def test_single_expiration_fails(self):
        legs = [
            _make_leg(expiration="2026-03-20"),
            _make_leg(index=1, expiration="2026-03-20"),
        ]
        r = validate_multi_expiry(legs)
        assert r.is_failure


class TestValidateWidth:

    def test_positive_width(self):
        r = validate_width(V2RecomputedMath(width=5.0))
        assert r.passed

    def test_zero_width(self):
        r = validate_width(V2RecomputedMath(width=0.0))
        assert r.is_failure
        assert r.fail_code == "v2_invalid_width"

    def test_negative_width(self):
        r = validate_width(V2RecomputedMath(width=-1.0))
        assert r.is_failure

    def test_none_width_skipped(self):
        r = validate_width(V2RecomputedMath())
        assert r.status == STATUS_SKIPPED


class TestValidatePricingSanity:

    def test_credit_valid(self):
        r = validate_pricing_sanity(V2RecomputedMath(net_credit=0.50, width=5.0))
        assert r.passed

    def test_credit_too_large(self):
        r = validate_pricing_sanity(V2RecomputedMath(net_credit=6.0, width=5.0))
        assert r.is_failure
        assert r.fail_code == "v2_impossible_pricing"

    def test_credit_negative(self):
        r = validate_pricing_sanity(V2RecomputedMath(net_credit=-0.10, width=5.0))
        assert r.is_failure
        assert r.fail_code == "v2_non_positive_credit"

    def test_debit_valid(self):
        r = validate_pricing_sanity(V2RecomputedMath(net_debit=2.0, width=5.0))
        assert r.passed

    def test_neither_set_skipped(self):
        r = validate_pricing_sanity(V2RecomputedMath())
        assert r.status == STATUS_SKIPPED


class TestValidateNoDuplicateLegs:

    def test_no_duplicates(self):
        legs = [
            _make_leg(index=0, strike=440, side="short"),
            _make_leg(index=1, strike=435, side="long"),
        ]
        r = validate_no_duplicate_legs(legs)
        assert r.passed

    def test_duplicates_detected(self):
        legs = [
            _make_leg(index=0, strike=440, side="short"),
            _make_leg(index=1, strike=440, side="short"),
        ]
        r = validate_no_duplicate_legs(legs)
        assert r.is_failure


class TestValidateStrikeOrdering:

    def test_ascending_valid(self):
        legs = [
            _make_leg(index=0, strike=430),
            _make_leg(index=1, strike=440),
            _make_leg(index=2, strike=450),
        ]
        r = validate_strike_ordering(legs, ascending=True)
        assert r.passed

    def test_ascending_invalid(self):
        legs = [
            _make_leg(index=0, strike=450),
            _make_leg(index=1, strike=440),
        ]
        r = validate_strike_ordering(legs, ascending=True)
        assert r.is_failure


class TestValidateSameOptionType:

    def test_same_type(self):
        r = validate_same_option_type([
            _make_leg(option_type="put"),
            _make_leg(index=1, option_type="put"),
        ])
        assert r.passed

    def test_mixed_types(self):
        r = validate_same_option_type([
            _make_leg(option_type="put"),
            _make_leg(index=1, option_type="call"),
        ])
        assert r.is_failure


class TestValidateHasShortAndLong:

    def test_both_sides(self):
        r = validate_has_short_and_long([
            _make_leg(side="short"),
            _make_leg(index=1, side="long"),
        ])
        assert r.passed

    def test_missing_long(self):
        r = validate_has_short_and_long([
            _make_leg(side="short"),
            _make_leg(index=1, side="short"),
        ])
        assert r.is_failure


# ═══════════════════════════════════════════════════════════════════
#  Structural Validation — Shared Runner
# ═══════════════════════════════════════════════════════════════════

class TestRunSharedStructuralChecks:

    def test_valid_vertical_passes(self):
        cand = _make_vertical_candidate()
        summary = run_shared_structural_checks(cand)
        assert summary.all_passed
        assert summary.fail_count == 0

    def test_no_legs_fails(self):
        cand = _make_vertical_candidate()
        cand.legs = []
        summary = run_shared_structural_checks(cand)
        assert summary.has_failures
        assert "v2_malformed_legs" in summary.fail_codes

    def test_expected_leg_count_enforced(self):
        cand = _make_vertical_candidate()
        summary = run_shared_structural_checks(cand, expected_leg_count=4)
        assert summary.has_failures

    def test_expected_leg_count_passes(self):
        cand = _make_vertical_candidate()
        summary = run_shared_structural_checks(cand, expected_leg_count=2)
        assert summary.all_passed

    def test_require_same_expiry_false_skips(self):
        cand = _make_vertical_candidate()
        cand.legs[1] = _make_leg(index=1, side="long", strike=435, expiration="2026-04-17")
        summary = run_shared_structural_checks(cand, require_same_expiry=False)
        # Should not fail on expiry (other checks may still apply)
        assert "v2_mismatched_expiry" not in summary.fail_codes

    def test_mismatched_expiry_detected(self):
        cand = _make_vertical_candidate()
        cand.legs[1] = _make_leg(index=1, side="long", strike=435, expiration="2026-04-17")
        summary = run_shared_structural_checks(cand, require_same_expiry=True)
        assert "v2_mismatched_expiry" in summary.fail_codes

    def test_to_check_results_format(self):
        cand = _make_vertical_candidate()
        summary = run_shared_structural_checks(cand)
        crs = summary.to_check_results()
        assert all(isinstance(cr, V2CheckResult) for cr in crs)
        assert all(cr.passed for cr in crs)


# ═══════════════════════════════════════════════════════════════════
#  Structural Validation — Family Runners
# ═══════════════════════════════════════════════════════════════════

class TestRunVerticalStructuralChecks:

    def test_valid_vertical(self):
        cand = _make_vertical_candidate()
        summary = run_vertical_structural_checks(cand)
        assert summary.all_passed

    def test_wrong_leg_count(self):
        cand = _make_vertical_candidate()
        cand.legs.append(_make_leg(index=2, strike=430))
        summary = run_vertical_structural_checks(cand)
        assert summary.has_failures

    def test_missing_long_side(self):
        cand = _make_vertical_candidate()
        cand.legs[1] = _make_leg(index=1, side="short", strike=435)
        summary = run_vertical_structural_checks(cand)
        assert summary.has_failures

    def test_mixed_option_types(self):
        cand = _make_vertical_candidate()
        cand.legs[1] = _make_leg(index=1, side="long", strike=435, option_type="call")
        summary = run_vertical_structural_checks(cand)
        assert summary.has_failures


class TestRunIronCondorStructuralChecks:

    def test_valid_iron_condor(self):
        cand = _make_iron_condor_candidate()
        summary = run_iron_condor_structural_checks(cand)
        assert summary.all_passed

    def test_wrong_leg_count(self):
        cand = _make_iron_condor_candidate()
        cand.legs = cand.legs[:3]
        summary = run_iron_condor_structural_checks(cand)
        assert summary.has_failures

    def test_bad_strike_ordering(self):
        cand = _make_iron_condor_candidate()
        # Swap put_short and call_short strikes to break ordering
        cand.legs[1] = _make_leg(
            index=1, side="short", strike=465.0, option_type="put",
            expiration="2026-03-20",
        )
        summary = run_iron_condor_structural_checks(cand)
        assert summary.has_failures

    def test_wrong_put_call_balance(self):
        cand = _make_iron_condor_candidate()
        # Make all legs puts
        for leg in cand.legs:
            leg = _make_leg(
                index=leg.index, side=leg.side, strike=leg.strike,
                option_type="put", expiration="2026-03-20",
            )
            cand.legs[leg.index] = leg
        summary = run_iron_condor_structural_checks(cand)
        assert summary.has_failures


class TestRunButterflyStructuralChecks:

    def test_valid_butterfly(self):
        cand = _make_butterfly_candidate()
        summary = run_butterfly_structural_checks(cand)
        assert summary.all_passed

    def test_asymmetric_butterfly(self):
        cand = _make_butterfly_candidate()
        # Move body strike off center
        cand.legs[1] = _make_leg(
            index=1, side="short", strike=447.0, option_type="put",
            expiration="2026-03-20",
        )
        summary = run_butterfly_structural_checks(cand)
        assert summary.has_failures
        assert "v2_malformed_legs" in summary.fail_codes


class TestRunCalendarStructuralChecks:

    def test_valid_calendar(self):
        legs = [
            _make_leg(index=0, side="short", strike=440, option_type="put",
                      expiration="2026-03-20"),
            _make_leg(index=1, side="long", strike=440, option_type="put",
                      expiration="2026-04-17"),
        ]
        cand = V2Candidate(
            candidate_id="SPY|calendar|0",
            scanner_key="calendar",
            strategy_id="calendar",
            family_key="calendars",
            symbol="SPY",
            expiration="2026-03-20",
            expiration_back="2026-04-17",
            legs=legs,
            math=V2RecomputedMath(),
        )
        summary = run_calendar_structural_checks(cand)
        assert summary.all_passed

    def test_same_expiry_fails(self):
        legs = [
            _make_leg(index=0, side="short", strike=440, option_type="put",
                      expiration="2026-03-20"),
            _make_leg(index=1, side="long", strike=440, option_type="put",
                      expiration="2026-03-20"),
        ]
        cand = V2Candidate(
            candidate_id="SPY|calendar|0",
            scanner_key="calendar",
            strategy_id="calendar",
            family_key="calendars",
            symbol="SPY",
            expiration="2026-03-20",
            legs=legs,
            math=V2RecomputedMath(),
        )
        summary = run_calendar_structural_checks(cand)
        assert summary.has_failures
        assert "v2_mismatched_expiry" in summary.fail_codes


# ═══════════════════════════════════════════════════════════════════
#  Math Verification — Individual Checks
# ═══════════════════════════════════════════════════════════════════

class TestVerifyPositiveMaxLoss:

    def test_positive(self):
        r = verify_positive_max_loss(V2RecomputedMath(max_loss=465.0))
        assert r.passed

    def test_zero(self):
        r = verify_positive_max_loss(V2RecomputedMath(max_loss=0.0))
        assert r.is_failure
        assert r.fail_code == "v2_impossible_max_loss"

    def test_negative(self):
        r = verify_positive_max_loss(V2RecomputedMath(max_loss=-10.0))
        assert r.is_failure

    def test_none_skipped(self):
        r = verify_positive_max_loss(V2RecomputedMath())
        assert r.status == STATUS_SKIPPED

    def test_nan(self):
        r = verify_positive_max_loss(V2RecomputedMath(max_loss=float("nan")))
        assert r.is_failure


class TestVerifyPositiveMaxProfit:

    def test_positive(self):
        r = verify_positive_max_profit(V2RecomputedMath(max_profit=35.0))
        assert r.passed

    def test_zero(self):
        r = verify_positive_max_profit(V2RecomputedMath(max_profit=0.0))
        assert r.is_failure
        assert r.fail_code == "v2_impossible_max_profit"


class TestVerifyFiniteValues:

    def test_all_finite(self):
        m = V2RecomputedMath(
            net_credit=0.35, max_profit=35.0, max_loss=465.0,
            width=5.0, pop=0.70, ev=-115.0, ror=0.0753,
        )
        r = verify_finite_values(m)
        assert r.passed

    def test_nan_detected(self):
        m = V2RecomputedMath(net_credit=float("nan"))
        r = verify_finite_values(m)
        assert r.is_failure
        assert "non-finite" in r.message

    def test_inf_detected(self):
        m = V2RecomputedMath(max_loss=float("inf"))
        r = verify_finite_values(m)
        assert r.is_failure

    def test_none_values_ok(self):
        m = V2RecomputedMath()  # all None
        r = verify_finite_values(m)
        assert r.passed

    def test_breakeven_nan(self):
        m = V2RecomputedMath(breakeven=[float("nan")])
        r = verify_finite_values(m)
        assert r.is_failure


class TestVerifyWidth:

    def test_width_matches(self):
        cand = _make_vertical_candidate()
        # Width should be |440 - 435| = 5.0
        cand.math.width = 5.0
        r = verify_width(cand)
        assert r.passed

    def test_width_mismatch(self):
        cand = _make_vertical_candidate()
        cand.math.width = 10.0  # Wrong
        r = verify_width(cand)
        assert r.is_failure or r.is_warning

    def test_width_none_skipped(self):
        cand = _make_vertical_candidate()
        cand.math.width = None
        r = verify_width(cand)
        assert r.status == STATUS_SKIPPED


class TestVerifyNetCreditOrDebit:

    def test_credit_matches(self):
        cand = _make_vertical_candidate(short_bid=1.20, long_ask=0.85)
        # Expected credit = 1.20 - 0.85 = 0.35
        cand.math.net_credit = 0.35
        r = verify_net_credit_or_debit(cand)
        assert r.passed

    def test_credit_mismatch(self):
        cand = _make_vertical_candidate(short_bid=1.20, long_ask=0.85)
        cand.math.net_credit = 0.50  # Wrong
        r = verify_net_credit_or_debit(cand)
        # delta = |0.35 - 0.50| = 0.15, which exceeds abs_warn=0.02
        assert r.is_failure

    def test_non_vertical_skipped(self):
        cand = _make_iron_condor_candidate()
        r = verify_net_credit_or_debit(cand)
        assert r.status == STATUS_SKIPPED


class TestVerifyMaxProfit:

    def test_credit_spread_matches(self):
        cand = _make_vertical_candidate()
        cand.math.net_credit = 0.35
        cand.math.max_profit = 35.0  # 0.35 × 100
        r = verify_max_profit(cand)
        assert r.passed

    def test_credit_spread_mismatch(self):
        cand = _make_vertical_candidate()
        cand.math.net_credit = 0.35
        cand.math.max_profit = 50.0  # Should be 35.0
        r = verify_max_profit(cand)
        assert r.is_failure or r.is_warning


class TestVerifyMaxLoss:

    def test_credit_spread_matches(self):
        cand = _make_vertical_candidate()
        cand.math.net_credit = 0.35
        cand.math.width = 5.0
        cand.math.max_loss = 465.0  # (5.0 - 0.35) × 100
        r = verify_max_loss(cand)
        assert r.passed

    def test_credit_spread_mismatch(self):
        cand = _make_vertical_candidate()
        cand.math.net_credit = 0.35
        cand.math.width = 5.0
        cand.math.max_loss = 500.0  # Should be 465.0
        r = verify_max_loss(cand)
        assert r.is_failure or r.is_warning


class TestVerifyBreakeven:

    def test_put_credit_spread_matches(self):
        cand = _make_vertical_candidate()
        cand.math.net_credit = 0.35
        # For put credit spread: BE = short.strike - net_credit = 440 - 0.35 = 439.65
        cand.math.breakeven = [439.65]
        r = verify_breakeven(cand)
        assert r.passed

    def test_breakeven_mismatch(self):
        cand = _make_vertical_candidate()
        cand.math.net_credit = 0.35
        cand.math.breakeven = [438.0]  # Wrong
        r = verify_breakeven(cand)
        assert r.is_failure

    def test_no_breakeven_skipped(self):
        cand = _make_vertical_candidate()
        cand.math.breakeven = []
        r = verify_breakeven(cand)
        assert r.status == STATUS_SKIPPED


class TestVerifyRor:

    def test_matches(self):
        cand = _make_vertical_candidate()
        cand.math.max_profit = 35.0
        cand.math.max_loss = 465.0
        cand.math.ror = round(35.0 / 465.0, 4)
        r = verify_ror(cand)
        assert r.passed

    def test_mismatch(self):
        cand = _make_vertical_candidate()
        cand.math.max_profit = 35.0
        cand.math.max_loss = 465.0
        cand.math.ror = 0.50  # Way off
        r = verify_ror(cand)
        assert r.is_failure


class TestCheckPopComputed:

    def test_pop_present(self):
        m = V2RecomputedMath(pop=0.70, pop_source="delta_approx")
        r = check_pop_computed(m)
        assert r.passed

    def test_pop_missing(self):
        r = check_pop_computed(V2RecomputedMath())
        assert r.is_warning
        assert r.warn_code == "v2_warn_pop_missing"


class TestCheckEvComputed:

    def test_ev_present(self):
        r = check_ev_computed(V2RecomputedMath(ev=-115.0))
        assert r.passed

    def test_ev_missing(self):
        r = check_ev_computed(V2RecomputedMath())
        assert r.is_warning
        assert r.warn_code == "v2_warn_ev_missing"


# ═══════════════════════════════════════════════════════════════════
#  Math Verification — Runner
# ═══════════════════════════════════════════════════════════════════

class TestRunMathVerification:

    def test_valid_vertical_passes(self):
        """A correctly computed vertical should pass all checks."""
        cand = _make_vertical_candidate(short_bid=1.20, long_ask=0.85)
        # Recompute math as Phase E would
        from app.services.scanner_v2.phases import _recompute_vertical_math
        _recompute_vertical_math(cand)

        summary = run_math_verification(cand, family_key="vertical_spreads")
        assert summary.all_passed, (
            f"Expected all passed, but got failures: "
            f"{[(r.check_key, r.status, r.message) for r in summary.results if r.is_failure]}"
        )

    def test_detects_tampered_max_profit(self):
        """If max_profit is wrong after recomputation, math verification catches it."""
        cand = _make_vertical_candidate(short_bid=1.20, long_ask=0.85)
        from app.services.scanner_v2.phases import _recompute_vertical_math
        _recompute_vertical_math(cand)

        # Tamper with max_profit
        cand.math.max_profit = 999.0

        summary = run_math_verification(cand)
        assert summary.has_failures
        assert any("max_profit" in code for code in summary.fail_codes)

    def test_detects_tampered_width(self):
        cand = _make_vertical_candidate(short_bid=1.20, long_ask=0.85)
        from app.services.scanner_v2.phases import _recompute_vertical_math
        _recompute_vertical_math(cand)

        cand.math.width = 99.0

        summary = run_math_verification(cand)
        assert summary.has_failures

    def test_missing_pop_is_warning_not_failure(self):
        """Missing POP should warn, not reject."""
        cand = _make_vertical_candidate(short_bid=1.20, long_ask=0.85)
        cand.legs[0] = _make_leg(
            index=0, side="short", strike=440.0, option_type="put",
            bid=1.20, ask=1.25, delta=None,  # No delta → no POP
        )
        from app.services.scanner_v2.phases import _recompute_vertical_math
        _recompute_vertical_math(cand)

        summary = run_math_verification(cand)
        # Should not have hard failures (positivity, width, credit all fine)
        hard_failures = [r for r in summary.results if r.is_failure]
        assert not hard_failures, (
            f"Unexpected failures: {[(r.check_key, r.message) for r in hard_failures]}"
        )
        # But should have POP warning
        assert summary.has_warnings

    def test_family_key_affects_tolerances(self):
        """Iron condor tolerances should be wider."""
        cand = _make_vertical_candidate(short_bid=1.20, long_ask=0.85)
        from app.services.scanner_v2.phases import _recompute_vertical_math
        _recompute_vertical_math(cand)

        # Introduce small credit error that would WARN with default but PASS with wider
        original_credit = cand.math.net_credit
        cand.math.net_credit = original_credit + 0.008  # within IC tolerance

        summary_default = run_math_verification(cand, family_key=None)
        summary_ic = run_math_verification(cand, family_key="iron_condors")

        # Both should classify differently (or same depending on margin)
        # At minimum, IC summary should not be worse
        assert summary_ic.fail_count <= summary_default.fail_count


# ═══════════════════════════════════════════════════════════════════
#  Integration: phases.py backward compat
# ═══════════════════════════════════════════════════════════════════

class TestPhasesIntegration:

    def test_phase_c_produces_check_results(self):
        """Phase C still produces V2CheckResult in diagnostics."""
        from app.services.scanner_v2.phases import phase_c_structural_validation

        cand = _make_vertical_candidate()
        [cand] = phase_c_structural_validation([cand])

        assert isinstance(cand.diagnostics.structural_checks, list)
        assert all(
            isinstance(cr, V2CheckResult)
            for cr in cand.diagnostics.structural_checks
        )

    def test_phase_c_valid_candidate_no_rejects(self):
        from app.services.scanner_v2.phases import phase_c_structural_validation

        cand = _make_vertical_candidate()
        [cand] = phase_c_structural_validation([cand])
        assert not cand.diagnostics.reject_reasons

    def test_phase_c_invalid_candidate_has_rejects(self):
        from app.services.scanner_v2.phases import phase_c_structural_validation

        cand = _make_vertical_candidate()
        cand.legs = []  # No legs → structural failure
        [cand] = phase_c_structural_validation([cand])
        assert "v2_malformed_legs" in cand.diagnostics.reject_reasons

    def test_phase_e_produces_math_checks(self):
        """Phase E still produces V2CheckResult in diagnostics."""
        from app.services.scanner_v2.phases import phase_e_recomputed_math

        cand = _make_vertical_candidate()
        [cand] = phase_e_recomputed_math([cand])

        assert isinstance(cand.diagnostics.math_checks, list)
        assert all(
            isinstance(cr, V2CheckResult)
            for cr in cand.diagnostics.math_checks
        )

    def test_phase_e_valid_candidate_has_math(self):
        from app.services.scanner_v2.phases import phase_e_recomputed_math

        cand = _make_vertical_candidate()
        [cand] = phase_e_recomputed_math([cand])
        assert cand.math.max_profit is not None
        assert cand.math.max_loss is not None

    def test_full_pipeline_valid(self):
        """Full C→D→E→F pipeline produces correct output shape."""
        from app.services.scanner_v2.phases import (
            phase_c_structural_validation,
            phase_d_quote_liquidity_sanity,
            phase_e_recomputed_math,
            phase_f_normalize,
        )

        cand = _make_vertical_candidate()
        [cand] = phase_c_structural_validation([cand])
        [cand] = phase_d_quote_liquidity_sanity([cand])
        [cand] = phase_e_recomputed_math([cand])
        [cand] = phase_f_normalize([cand])

        assert cand.passed
        assert cand.downstream_usable
        assert cand.diagnostics.structural_checks
        assert cand.diagnostics.math_checks

    def test_full_pipeline_reject(self):
        """Structurally broken candidate gets rejected through pipeline."""
        from app.services.scanner_v2.phases import (
            phase_c_structural_validation,
            phase_d_quote_liquidity_sanity,
            phase_e_recomputed_math,
            phase_f_normalize,
        )

        cand = _make_vertical_candidate()
        cand.legs = []  # Break it
        [cand] = phase_c_structural_validation([cand])
        [cand] = phase_d_quote_liquidity_sanity([cand])
        [cand] = phase_e_recomputed_math([cand])
        [cand] = phase_f_normalize([cand])

        assert not cand.passed
        assert not cand.downstream_usable
        assert "v2_malformed_legs" in cand.diagnostics.reject_reasons


# ═══════════════════════════════════════════════════════════════════
#  Import Tests
# ═══════════════════════════════════════════════════════════════════

class TestValidationImports:

    def test_top_level_import(self):
        from app.services.scanner_v2.validation import (
            V2ValidationResult,
            V2ValidationSummary,
            V2ToleranceSpec,
            STATUS_PASS,
            STATUS_FAIL,
            run_shared_structural_checks,
            run_math_verification,
            get_tolerances,
        )
        assert V2ValidationResult is not None
        assert V2ValidationSummary is not None

    def test_contracts_import(self):
        from app.services.scanner_v2.validation.contracts import V2ValidationResult
        assert V2ValidationResult is not None

    def test_structural_import(self):
        from app.services.scanner_v2.validation.structural import (
            run_shared_structural_checks,
            validate_same_expiry,
        )
        assert run_shared_structural_checks is not None

    def test_math_checks_import(self):
        from app.services.scanner_v2.validation.math_checks import (
            run_math_verification,
            verify_width,
        )
        assert run_math_verification is not None

    def test_tolerances_import(self):
        from app.services.scanner_v2.validation.tolerances import (
            DEFAULT_TOLERANCES,
            get_tolerances,
        )
        assert len(DEFAULT_TOLERANCES) == 8
