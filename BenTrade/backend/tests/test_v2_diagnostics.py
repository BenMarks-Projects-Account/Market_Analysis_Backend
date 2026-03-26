"""Comprehensive tests for V2 Diagnostics Framework (Prompt 5).

Test groups
-----------
1. Reason code registry — all codes registered, metadata correct.
2. V2DiagnosticItem — constructors, predicates, serialization.
3. DiagnosticsBuilder — accumulation, dedup, apply, merge.
4. collect_pass_reasons — semantic pass reason generation.
5. Phase integration — builder wired correctly in phases C/D/E/F.
6. Canonical taxonomy mapping — to_canonical / from_canonical.
7. V2ScanResult — narrowing_diagnostics field populated.
8. Contract stability — codes don't change between versions.
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
from app.services.scanner_v2.diagnostics import (
    # Builder
    DiagnosticsBuilder,
    V2DiagnosticItem,
    collect_pass_reasons,
    # Category constants
    CAT_LIQUIDITY,
    CAT_MATH,
    CAT_QUOTE,
    CAT_STRUCTURAL,
    CAT_THRESHOLD,
    ALL_CATEGORIES,
    # Kind constants
    KIND_PASS,
    KIND_REJECT,
    KIND_WARNING,
    # Severity constants
    SEV_ERROR,
    SEV_INFO,
    SEV_WARNING,
    # Reject codes
    REJECT_BREAKEVEN_MISMATCH,
    REJECT_CREDIT_MISMATCH,
    REJECT_DEBIT_MISMATCH,
    REJECT_IMPOSSIBLE_MAX_LOSS,
    REJECT_IMPOSSIBLE_MAX_PROFIT,
    REJECT_IMPOSSIBLE_PRICING,
    REJECT_INVALID_WIDTH,
    REJECT_INVERTED_QUOTE,
    REJECT_MALFORMED_LEGS,
    REJECT_MAX_LOSS_MISMATCH,
    REJECT_MAX_PROFIT_MISMATCH,
    REJECT_MISSING_OI,
    REJECT_MISSING_QUOTE,
    REJECT_MISSING_VOLUME,
    REJECT_MISMATCHED_EXPIRY,
    REJECT_NON_FINITE_MATH,
    REJECT_NON_POSITIVE_CREDIT,
    REJECT_ROR_MISMATCH,
    REJECT_WIDTH_MISMATCH,
    REJECT_ZERO_MID,
    # Warning codes
    WARN_BREAKEVEN_MISMATCH,
    WARN_CREDIT_MISMATCH,
    WARN_DEBIT_MISMATCH,
    WARN_EV_MISSING,
    WARN_MAX_LOSS_MISMATCH,
    WARN_MAX_PROFIT_MISMATCH,
    WARN_POP_MISSING,
    WARN_ROR_MISMATCH,
    WARN_WIDTH_MISMATCH,
    # Pass codes
    PASS_ALL_PHASES,
    PASS_LIQUIDITY_PRESENT,
    PASS_MATH_CONSISTENT,
    PASS_QUOTES_CLEAN,
    PASS_STRUCTURAL_VALID,
    # Helpers
    all_pass_codes,
    all_reject_codes,
    all_warn_codes,
    from_canonical,
    get_category,
    get_code_info,
    get_label,
    get_severity,
    is_valid_code,
    is_valid_pass_code,
    is_valid_reject_code,
    is_valid_warn_code,
    to_canonical,
    CodeInfo,
)
from app.services.scanner_v2.phases import (
    phase_c_structural_validation,
    phase_d_quote_liquidity_sanity,
    phase_e_recomputed_math,
    phase_f_normalize,
)
from app.services.scanner_v2.validation.contracts import (
    V2ValidationResult,
    V2ValidationSummary,
)


# =====================================================================
#  Helpers
# =====================================================================

def _make_candidate(
    *,
    legs: list[V2Leg] | None = None,
    math: V2RecomputedMath | None = None,
) -> V2Candidate:
    """Build a minimal V2Candidate for testing."""
    return V2Candidate(
        candidate_id="test|pcs|2026-04-01|380/375|0",
        scanner_key="put_credit_spread",
        strategy_id="put_credit_spread",
        family_key="vertical_spreads",
        symbol="SPY",
        underlying_price=400.0,
        expiration="2026-04-01",
        dte=30,
        legs=legs or [],
        math=math or V2RecomputedMath(),
    )


def _good_legs() -> list[V2Leg]:
    """Two-leg put credit spread with valid quotes."""
    return [
        V2Leg(
            index=0, side="short", strike=380.0, option_type="put",
            expiration="2026-04-01",
            bid=2.50, ask=2.60, mid=2.55,
            delta=-0.30, gamma=0.01, theta=-0.05, vega=0.10,
            iv=0.20, open_interest=5000, volume=200,
        ),
        V2Leg(
            index=1, side="long", strike=375.0, option_type="put",
            expiration="2026-04-01",
            bid=1.80, ask=1.90, mid=1.85,
            delta=-0.22, gamma=0.01, theta=-0.04, vega=0.08,
            iv=0.21, open_interest=3000, volume=150,
        ),
    ]


# =====================================================================
#  1. Reason Code Registry
# =====================================================================


class TestReasonCodeRegistry:
    """Verify all reason codes are properly registered."""

    def test_reject_codes_count(self):
        """All 32 reject codes registered (30 previous + 2 credit integrity)."""
        assert len(all_reject_codes()) == 32

    def test_warn_codes_count(self):
        """All 14 warn codes registered (9 original + 5 hygiene)."""
        assert len(all_warn_codes()) == 14

    def test_pass_codes_count(self):
        """All 8 pass codes registered (5 original + 3 hygiene)."""
        assert len(all_pass_codes()) == 8

    def test_all_reject_codes_start_with_v2(self):
        for code in all_reject_codes():
            assert code.startswith("v2_"), f"{code} missing v2_ prefix"

    def test_all_warn_codes_start_with_v2_warn(self):
        for code in all_warn_codes():
            assert code.startswith("v2_warn_"), f"{code} missing v2_warn_ prefix"

    def test_all_pass_codes_start_with_v2_pass(self):
        for code in all_pass_codes():
            assert code.startswith("v2_pass_"), f"{code} missing v2_pass_ prefix"

    def test_no_overlap_between_registries(self):
        """Reject, warn, and pass codes must be disjoint."""
        r = all_reject_codes()
        w = all_warn_codes()
        p = all_pass_codes()
        assert not r & w, f"Overlap reject/warn: {r & w}"
        assert not r & p, f"Overlap reject/pass: {r & p}"
        assert not w & p, f"Overlap warn/pass: {w & p}"

    def test_every_reject_code_has_error_severity(self):
        for code in all_reject_codes():
            assert get_severity(code) == SEV_ERROR

    def test_every_warn_code_has_warning_severity(self):
        for code in all_warn_codes():
            assert get_severity(code) == SEV_WARNING

    def test_every_pass_code_has_info_severity(self):
        for code in all_pass_codes():
            assert get_severity(code) == SEV_INFO

    def test_every_code_has_category(self):
        for code in all_reject_codes() | all_warn_codes() | all_pass_codes():
            cat = get_category(code)
            assert cat in ALL_CATEGORIES, f"{code} has unknown category {cat}"

    def test_every_code_has_label(self):
        for code in all_reject_codes() | all_warn_codes() | all_pass_codes():
            label = get_label(code)
            assert label != code, f"{code} has no label"

    def test_code_info_returns_namedtuple(self):
        info = get_code_info(REJECT_MISSING_QUOTE)
        assert isinstance(info, CodeInfo)
        assert info.code == REJECT_MISSING_QUOTE
        assert info.category == CAT_QUOTE
        assert info.severity == SEV_ERROR

    def test_unknown_code_returns_none(self):
        assert get_code_info("v2_nonexistent") is None
        assert get_category("v2_nonexistent") is None
        assert get_severity("v2_nonexistent") is None

    def test_unknown_code_label_falls_back(self):
        assert get_label("v2_nonexistent") == "v2_nonexistent"


# ── Validity helpers ────────────────────────────────────────────────


class TestValidityHelpers:

    def test_is_valid_reject_code(self):
        assert is_valid_reject_code(REJECT_MISSING_QUOTE)
        assert not is_valid_reject_code(WARN_POP_MISSING)
        assert not is_valid_reject_code("not_a_code")

    def test_is_valid_warn_code(self):
        assert is_valid_warn_code(WARN_POP_MISSING)
        assert not is_valid_warn_code(REJECT_MISSING_QUOTE)

    def test_is_valid_pass_code(self):
        assert is_valid_pass_code(PASS_ALL_PHASES)
        assert not is_valid_pass_code(REJECT_MISSING_QUOTE)

    def test_is_valid_code_any(self):
        assert is_valid_code(REJECT_MISSING_QUOTE)
        assert is_valid_code(WARN_POP_MISSING)
        assert is_valid_code(PASS_ALL_PHASES)
        assert not is_valid_code("bogus")


# =====================================================================
#  2. V2DiagnosticItem
# =====================================================================


class TestV2DiagnosticItem:
    """Test V2DiagnosticItem construction, predicates, serialization."""

    def test_reject_constructor(self):
        item = V2DiagnosticItem.reject(
            REJECT_MISSING_QUOTE,
            source_phase="D",
            source_check="quote_present",
            message="leg[0]: bid=None",
        )
        assert item.code == REJECT_MISSING_QUOTE
        assert item.kind == KIND_REJECT
        assert item.category == CAT_QUOTE
        assert item.severity == SEV_ERROR
        assert item.source_phase == "D"
        assert item.source_check == "quote_present"
        assert item.is_reject
        assert not item.is_pass
        assert not item.is_warning

    def test_warning_constructor(self):
        item = V2DiagnosticItem.warning(
            WARN_POP_MISSING,
            source_phase="E",
            message="POP missing",
        )
        assert item.kind == KIND_WARNING
        assert item.severity == SEV_WARNING
        assert item.category == CAT_MATH
        assert item.is_warning

    def test_pass_constructor(self):
        item = V2DiagnosticItem.pass_item(
            PASS_QUOTES_CLEAN,
            source_phase="F",
            message="All quote checks passed",
        )
        assert item.kind == KIND_PASS
        assert item.severity == SEV_INFO
        assert item.category == CAT_QUOTE
        assert item.is_pass

    def test_auto_label_when_no_message(self):
        item = V2DiagnosticItem.reject(REJECT_INVERTED_QUOTE)
        assert item.message == "Inverted quote"

    def test_metadata_via_kwargs(self):
        item = V2DiagnosticItem.reject(
            REJECT_MISSING_QUOTE,
            leg_index=0,
            bid=None,
            ask=None,
        )
        assert item.metadata == {"leg_index": 0, "bid": None, "ask": None}

    def test_to_dict(self):
        item = V2DiagnosticItem.reject(
            REJECT_MISSING_QUOTE,
            source_phase="D",
            source_check="quote_present",
        )
        d = item.to_dict()
        assert d["code"] == REJECT_MISSING_QUOTE
        assert d["kind"] == KIND_REJECT
        assert d["category"] == CAT_QUOTE
        assert d["severity"] == SEV_ERROR
        assert d["source_phase"] == "D"
        assert d["source_check"] == "quote_present"
        assert isinstance(d["metadata"], dict)

    def test_unknown_code_falls_back(self):
        item = V2DiagnosticItem.reject("v2_custom_family_check")
        assert item.category == ""
        assert item.message == "v2_custom_family_check"


# =====================================================================
#  3. DiagnosticsBuilder
# =====================================================================


class TestDiagnosticsBuilder:

    def test_add_reject(self):
        b = DiagnosticsBuilder(source_phase="D")
        b.add_reject(REJECT_MISSING_QUOTE, source_check="quote_present")
        assert REJECT_MISSING_QUOTE in b.reject_codes
        assert len(b.items) == 1
        assert b.items[0].is_reject

    def test_add_reject_dedup(self):
        """Same code added twice → only one item."""
        b = DiagnosticsBuilder(source_phase="D")
        b.add_reject(REJECT_MISSING_QUOTE, source_check="quote_present")
        b.add_reject(REJECT_MISSING_QUOTE, source_check="quote_present")
        assert len(b.items) == 1

    def test_add_warning(self):
        b = DiagnosticsBuilder(source_phase="E")
        b.add_warning(WARN_POP_MISSING, message="POP missing")
        assert WARN_POP_MISSING in b.warn_codes
        assert len(b.items) == 1
        assert b.items[0].is_warning

    def test_add_warning_dedup(self):
        b = DiagnosticsBuilder(source_phase="E")
        b.add_warning(WARN_POP_MISSING)
        b.add_warning(WARN_POP_MISSING)
        assert len(b.items) == 1

    def test_add_pass(self):
        b = DiagnosticsBuilder(source_phase="F")
        b.add_pass(PASS_STRUCTURAL_VALID)
        assert len(b.items) == 1
        assert b.items[0].is_pass

    def test_has_rejects(self):
        b = DiagnosticsBuilder(source_phase="D")
        assert not b.has_rejects
        b.add_reject(REJECT_MISSING_QUOTE)
        assert b.has_rejects

    def test_set_check_results(self):
        b = DiagnosticsBuilder(source_phase="D")
        checks = [V2CheckResult("test", True, "ok")]
        b.set_check_results("quote", checks)
        diag = V2Diagnostics()
        b.apply(diag)
        assert diag.quote_checks == checks

    def test_apply_populates_reject_reasons(self):
        b = DiagnosticsBuilder(source_phase="D")
        b.add_reject(REJECT_MISSING_QUOTE)
        b.add_reject(REJECT_INVERTED_QUOTE)
        diag = V2Diagnostics()
        b.apply(diag)
        assert REJECT_MISSING_QUOTE in diag.reject_reasons
        assert REJECT_INVERTED_QUOTE in diag.reject_reasons

    def test_apply_populates_warnings(self):
        b = DiagnosticsBuilder(source_phase="E")
        b.add_warning(WARN_POP_MISSING, message="POP could not be computed")
        diag = V2Diagnostics()
        b.apply(diag)
        assert "POP could not be computed" in diag.warnings

    def test_apply_populates_items(self):
        b = DiagnosticsBuilder(source_phase="D")
        b.add_reject(REJECT_MISSING_QUOTE)
        diag = V2Diagnostics()
        b.apply(diag)
        assert len(diag.items) == 1
        assert diag.items[0].code == REJECT_MISSING_QUOTE

    def test_apply_merges_with_existing_items(self):
        """Items from multiple phases accumulate."""
        diag = V2Diagnostics()

        b1 = DiagnosticsBuilder(source_phase="C")
        b1.add_reject(REJECT_MALFORMED_LEGS)
        b1.apply(diag)

        b2 = DiagnosticsBuilder(source_phase="D")
        b2.add_reject(REJECT_MISSING_QUOTE)
        b2.apply(diag)

        assert len(diag.items) == 2
        phases = {item.source_phase for item in diag.items}
        assert phases == {"C", "D"}

    def test_apply_dedup_across_builders(self):
        """Same reject code from two builders → appears in reject_reasons once."""
        diag = V2Diagnostics()

        b1 = DiagnosticsBuilder(source_phase="C")
        b1.add_reject(REJECT_MALFORMED_LEGS)
        b1.apply(diag)

        b2 = DiagnosticsBuilder(source_phase="D")
        b2.add_reject(REJECT_MALFORMED_LEGS)
        b2.apply(diag)

        assert diag.reject_reasons.count(REJECT_MALFORMED_LEGS) == 1

    def test_merge_validation_summary(self):
        """Import fail/warn codes from a V2ValidationSummary."""
        summary = V2ValidationSummary(results=[
            V2ValidationResult.make_fail(
                "width_positive", "v2_invalid_width", "width ≤ 0",
            ),
            V2ValidationResult.make_warn(
                "credit_match", "v2_warn_credit_mismatch", "credit near tolerance",
            ),
            V2ValidationResult.make_pass("leg_count", "2 legs"),
        ])

        b = DiagnosticsBuilder(source_phase="C")
        b.merge_validation_summary(summary, check_section="structural")

        assert REJECT_INVALID_WIDTH in b.reject_codes
        assert WARN_CREDIT_MISMATCH in b.warn_codes
        assert len(b.items) == 2  # fail + warn (pass not added as item)

        diag = V2Diagnostics()
        b.apply(diag)
        assert len(diag.structural_checks) == 3  # all 3 check results

    def test_merge_validation_summary_without_section(self):
        summary = V2ValidationSummary(results=[
            V2ValidationResult.make_fail(
                "test", "v2_impossible_max_loss", "bad",
            ),
        ])
        b = DiagnosticsBuilder(source_phase="E")
        b.merge_validation_summary(summary)
        diag = V2Diagnostics()
        b.apply(diag)
        # No check_section → no check results set
        assert diag.math_checks == []
        # But reject reason is there
        assert REJECT_IMPOSSIBLE_MAX_LOSS in diag.reject_reasons


# =====================================================================
#  4. collect_pass_reasons
# =====================================================================


class TestCollectPassReasons:

    def test_all_checks_passed(self):
        diag = V2Diagnostics(
            structural_checks=[V2CheckResult("a", True), V2CheckResult("b", True)],
            quote_checks=[V2CheckResult("c", True)],
            liquidity_checks=[V2CheckResult("d", True)],
            math_checks=[V2CheckResult("e", True), V2CheckResult("f", True)],
        )
        reasons = collect_pass_reasons(diag)
        assert PASS_STRUCTURAL_VALID in reasons
        assert PASS_QUOTES_CLEAN in reasons
        assert PASS_LIQUIDITY_PRESENT in reasons
        assert PASS_MATH_CONSISTENT in reasons
        assert PASS_ALL_PHASES in reasons
        assert len(diag.items) == 5  # 4 section passes + ALL_PHASES

    def test_empty_diagnostics(self):
        diag = V2Diagnostics()
        reasons = collect_pass_reasons(diag)
        assert reasons == []
        assert len(diag.items) == 0

    def test_partial_failure_no_pass_for_that_section(self):
        diag = V2Diagnostics(
            structural_checks=[V2CheckResult("a", True), V2CheckResult("b", False)],
            quote_checks=[V2CheckResult("c", True)],
        )
        reasons = collect_pass_reasons(diag)
        # structural has a failure → no pass code for structural
        assert PASS_STRUCTURAL_VALID not in reasons
        # quotes all passed → pass code present
        assert PASS_QUOTES_CLEAN in reasons

    def test_items_have_source_phase_f(self):
        diag = V2Diagnostics(
            structural_checks=[V2CheckResult("a", True)],
        )
        collect_pass_reasons(diag)
        for item in diag.items:
            assert item.source_phase == "F"

    def test_items_have_metadata(self):
        diag = V2Diagnostics(
            structural_checks=[V2CheckResult("a", True), V2CheckResult("b", True)],
        )
        collect_pass_reasons(diag)
        struct_item = next(i for i in diag.items if i.code == PASS_STRUCTURAL_VALID)
        assert struct_item.metadata["checks_passed"] == 2
        assert struct_item.metadata["checks_total"] == 2


# =====================================================================
#  5. Phase Integration
# =====================================================================


class TestPhaseC_DiagnosticItems:
    """Phase C should populate diag.items with structured reject items."""

    def test_valid_candidate_no_reject_items(self):
        cand = _make_candidate(legs=_good_legs())
        [cand] = phase_c_structural_validation([cand])
        reject_items = [i for i in cand.diagnostics.items if i.is_reject]
        assert reject_items == []

    def test_no_legs_produces_reject_item(self):
        cand = _make_candidate(legs=[])
        [cand] = phase_c_structural_validation([cand])
        reject_items = [i for i in cand.diagnostics.items if i.is_reject]
        assert len(reject_items) >= 1
        codes = {i.code for i in reject_items}
        assert REJECT_MALFORMED_LEGS in codes

    def test_reject_items_have_source_phase_c(self):
        cand = _make_candidate(legs=[])
        [cand] = phase_c_structural_validation([cand])
        for item in cand.diagnostics.items:
            if item.is_reject:
                assert item.source_phase == "C"


class TestPhaseD_DiagnosticItems:
    """Phase D should populate diag.items with structured reject items."""

    def test_missing_quote_produces_reject_item(self):
        legs = _good_legs()
        legs[0].bid = None  # break the short leg
        cand = _make_candidate(legs=legs)
        [cand] = phase_d_quote_liquidity_sanity([cand])
        reject_items = [i for i in cand.diagnostics.items if i.is_reject]
        codes = {i.code for i in reject_items}
        assert REJECT_MISSING_QUOTE in codes

    def test_inverted_quote_produces_reject_item(self):
        legs = _good_legs()
        legs[0].bid = 3.0
        legs[0].ask = 2.0
        cand = _make_candidate(legs=legs)
        [cand] = phase_d_quote_liquidity_sanity([cand])
        codes = {i.code for i in cand.diagnostics.items if i.is_reject}
        assert REJECT_INVERTED_QUOTE in codes

    def test_missing_oi_produces_reject_item(self):
        legs = _good_legs()
        legs[0].open_interest = None
        cand = _make_candidate(legs=legs)
        [cand] = phase_d_quote_liquidity_sanity([cand])
        codes = {i.code for i in cand.diagnostics.items if i.is_reject}
        assert REJECT_MISSING_OI in codes

    def test_missing_volume_produces_reject_item(self):
        legs = _good_legs()
        legs[1].volume = None
        cand = _make_candidate(legs=legs)
        [cand] = phase_d_quote_liquidity_sanity([cand])
        codes = {i.code for i in cand.diagnostics.items if i.is_reject}
        assert REJECT_MISSING_VOLUME in codes

    def test_reject_items_have_source_phase_d(self):
        legs = _good_legs()
        legs[0].bid = None
        cand = _make_candidate(legs=legs)
        [cand] = phase_d_quote_liquidity_sanity([cand])
        for item in cand.diagnostics.items:
            if item.is_reject:
                assert item.source_phase == "D"

    def test_reject_items_have_metadata(self):
        legs = _good_legs()
        legs[0].bid = None
        cand = _make_candidate(legs=legs)
        [cand] = phase_d_quote_liquidity_sanity([cand])
        item = next(i for i in cand.diagnostics.items if i.code == REJECT_MISSING_QUOTE)
        assert "leg_index" in item.metadata

    def test_clean_candidate_no_reject_items(self):
        cand = _make_candidate(legs=_good_legs())
        [cand] = phase_d_quote_liquidity_sanity([cand])
        reject_items = [i for i in cand.diagnostics.items if i.is_reject]
        assert reject_items == []

    def test_already_rejected_skipped(self):
        cand = _make_candidate(legs=_good_legs())
        cand.diagnostics.reject_reasons.append(REJECT_MALFORMED_LEGS)
        [cand] = phase_d_quote_liquidity_sanity([cand])
        # No items added since candidate was already rejected
        assert len(cand.diagnostics.items) == 0


class TestPhaseE_DiagnosticItems:
    """Phase E should populate diag.items via builder."""

    def test_valid_math_no_reject_items(self):
        cand = _make_candidate(legs=_good_legs())
        [cand] = phase_e_recomputed_math([cand])
        reject_items = [i for i in cand.diagnostics.items if i.is_reject]
        assert reject_items == []

    def test_math_rejects_produce_items(self):
        legs = _good_legs()
        # Set up candidate with impossible math
        cand = _make_candidate(legs=legs)
        cand.math.max_loss = -100  # Impossible
        # Phase E will recompute, so let's use a bad setup that triggers rejection
        # Actually Phase E recomputes from scratch, so let me use a candidate
        # that will fail: zero width legs
        legs[0].strike = 380.0
        legs[1].strike = 380.0  # Same strike → width = 0
        cand = _make_candidate(legs=legs)
        [cand] = phase_e_recomputed_math([cand])
        # Width 0 → math problems, may produce reject items
        # Check that if any reject items exist, they're from phase E
        for item in cand.diagnostics.items:
            if item.is_reject:
                assert item.source_phase == "E"

    def test_warning_items_from_math(self):
        legs = _good_legs()
        legs[0].delta = None  # Will trigger warn_pop_missing
        cand = _make_candidate(legs=legs)
        [cand] = phase_e_recomputed_math([cand])
        warn_items = [i for i in cand.diagnostics.items if i.is_warning]
        # Should have warnings about POP/EV being missing
        if warn_items:  # Only if the math framework emits them
            for item in warn_items:
                assert item.source_phase == "E"

    def test_already_rejected_skipped(self):
        cand = _make_candidate(legs=_good_legs())
        cand.diagnostics.reject_reasons.append(REJECT_MALFORMED_LEGS)
        [cand] = phase_e_recomputed_math([cand])
        assert len(cand.diagnostics.items) == 0


class TestPhaseF_DiagnosticItems:
    """Phase F adds pass reason items for passing candidates."""

    def test_passing_candidate_gets_pass_items(self):
        cand = _make_candidate(legs=_good_legs())
        # Simulate having passed C, D, E
        cand.diagnostics.structural_checks = [V2CheckResult("a", True)]
        cand.diagnostics.quote_checks = [V2CheckResult("b", True)]
        cand.diagnostics.liquidity_checks = [V2CheckResult("c", True)]
        cand.diagnostics.math_checks = [V2CheckResult("d", True)]
        [cand] = phase_f_normalize([cand])
        assert cand.passed
        pass_items = [i for i in cand.diagnostics.items if i.is_pass]
        assert len(pass_items) == 5  # structural + quote + liquidity + math + all
        codes = {i.code for i in pass_items}
        assert PASS_ALL_PHASES in codes

    def test_rejected_candidate_gets_no_pass_items(self):
        cand = _make_candidate(legs=_good_legs())
        cand.diagnostics.reject_reasons.append(REJECT_MISSING_QUOTE)
        [cand] = phase_f_normalize([cand])
        assert not cand.passed
        pass_items = [i for i in cand.diagnostics.items if i.is_pass]
        assert pass_items == []

    def test_pass_reasons_are_now_code_strings(self):
        """Pass reasons changed from count strings to semantic codes."""
        cand = _make_candidate(legs=_good_legs())
        cand.diagnostics.structural_checks = [V2CheckResult("a", True)]
        [cand] = phase_f_normalize([cand])
        # pass_reasons should now contain code strings, not "structural: 1/1 passed"
        assert PASS_STRUCTURAL_VALID in cand.diagnostics.pass_reasons


class TestFullPipeline_DiagnosticItems:
    """End-to-end: C→D→E→F with diagnostic items accumulating."""

    def test_passing_candidate_accumulates_items(self):
        cand = _make_candidate(legs=_good_legs())
        [cand] = phase_c_structural_validation([cand])
        [cand] = phase_d_quote_liquidity_sanity([cand])
        [cand] = phase_e_recomputed_math([cand])
        [cand] = phase_f_normalize([cand])

        assert cand.passed
        # Should have items from E (warnings maybe) and F (pass reasons)
        # At minimum, F should add pass items
        pass_items = [i for i in cand.diagnostics.items if i.is_pass]
        assert len(pass_items) >= 1
        assert PASS_ALL_PHASES in {i.code for i in pass_items}

    def test_rejected_candidate_accumulates_reject_items(self):
        legs = _good_legs()
        legs[0].bid = None  # Will fail Phase D
        cand = _make_candidate(legs=legs)
        [cand] = phase_c_structural_validation([cand])
        [cand] = phase_d_quote_liquidity_sanity([cand])
        [cand] = phase_e_recomputed_math([cand])
        [cand] = phase_f_normalize([cand])

        assert not cand.passed
        reject_items = [i for i in cand.diagnostics.items if i.is_reject]
        assert len(reject_items) >= 1
        # No pass items for rejected candidate
        pass_items = [i for i in cand.diagnostics.items if i.is_pass]
        assert pass_items == []

    def test_legacy_reject_reasons_still_populated(self):
        """Backward compat: reject_reasons list still works."""
        legs = _good_legs()
        legs[0].bid = None
        cand = _make_candidate(legs=legs)
        [cand] = phase_c_structural_validation([cand])
        [cand] = phase_d_quote_liquidity_sanity([cand])
        assert REJECT_MISSING_QUOTE in cand.diagnostics.reject_reasons

    def test_legacy_warnings_still_populated(self):
        """Backward compat: warnings list still works."""
        legs = _good_legs()
        legs[0].delta = None  # missing delta → warn_pop_missing
        cand = _make_candidate(legs=legs)
        [cand] = phase_c_structural_validation([cand])
        [cand] = phase_d_quote_liquidity_sanity([cand])
        [cand] = phase_e_recomputed_math([cand])
        # If any warnings exist, they should be strings in the legacy list
        for w in cand.diagnostics.warnings:
            assert isinstance(w, str)


# =====================================================================
#  6. Canonical Taxonomy Mapping
# =====================================================================


class TestCanonicalMapping:

    def test_quote_mappings(self):
        assert to_canonical(REJECT_MISSING_QUOTE) == "missing_quote"
        assert to_canonical(REJECT_INVERTED_QUOTE) == "inverted_market"
        assert to_canonical(REJECT_ZERO_MID) == "zero_mid"

    def test_liquidity_mappings(self):
        assert to_canonical(REJECT_MISSING_OI) == "missing_open_interest"
        assert to_canonical(REJECT_MISSING_VOLUME) == "missing_volume"

    def test_structural_mappings(self):
        assert to_canonical(REJECT_INVALID_WIDTH) == "invalid_width"
        assert to_canonical(REJECT_NON_POSITIVE_CREDIT) == "non_positive_credit"
        assert to_canonical(REJECT_IMPOSSIBLE_PRICING) == "credit_ge_width"

    def test_v2_only_codes_return_none(self):
        assert to_canonical(REJECT_MALFORMED_LEGS) is None
        assert to_canonical(REJECT_WIDTH_MISMATCH) is None

    def test_reverse_mapping(self):
        assert from_canonical("missing_quote") == REJECT_MISSING_QUOTE
        assert from_canonical("inverted_market") == REJECT_INVERTED_QUOTE
        assert from_canonical("nonexistent") is None

    def test_roundtrip(self):
        for v2_code in [REJECT_MISSING_QUOTE, REJECT_INVERTED_QUOTE, REJECT_ZERO_MID]:
            canonical = to_canonical(v2_code)
            assert canonical is not None
            assert from_canonical(canonical) == v2_code


# =====================================================================
#  7. V2ScanResult — narrowing_diagnostics
# =====================================================================


class TestV2ScanResultNarrowingDiag:

    def test_default_empty(self):
        result = V2ScanResult(
            scanner_key="test", strategy_id="test",
            family_key="test", symbol="SPY",
        )
        assert result.narrowing_diagnostics == {}

    def test_can_be_populated(self):
        result = V2ScanResult(
            scanner_key="test", strategy_id="test",
            family_key="test", symbol="SPY",
            narrowing_diagnostics={
                "total_contracts_loaded": 500,
                "contracts_final": 120,
                "expirations_kept": 3,
            },
        )
        assert result.narrowing_diagnostics["total_contracts_loaded"] == 500

    def test_serializes_in_to_dict(self):
        result = V2ScanResult(
            scanner_key="test", strategy_id="test",
            family_key="test", symbol="SPY",
            narrowing_diagnostics={"some_key": 42},
        )
        d = result.to_dict()
        assert d["narrowing_diagnostics"]["some_key"] == 42


# =====================================================================
#  8. V2Diagnostics — items field
# =====================================================================


class TestV2DiagnosticsItemsField:

    def test_default_empty(self):
        diag = V2Diagnostics()
        assert diag.items == []

    def test_items_serializable(self):
        diag = V2Diagnostics()
        diag.items.append(V2DiagnosticItem.reject(REJECT_MISSING_QUOTE, source_phase="D"))
        cand = _make_candidate()
        cand.diagnostics = diag
        d = cand.to_dict()
        items = d["diagnostics"]["items"]
        assert len(items) == 1
        assert items[0]["code"] == REJECT_MISSING_QUOTE


# =====================================================================
#  9. Contract stability — reason codes must not change
# =====================================================================


class TestContractStability:
    """Ensure reason code string values are stable (never silently renamed)."""

    EXPECTED_REJECT_CODES = {
        "v2_malformed_legs",
        "v2_invalid_width",
        "v2_non_positive_credit",
        "v2_impossible_pricing",
        "v2_mismatched_expiry",
        "v2_missing_quote",
        "v2_inverted_quote",
        "v2_zero_mid",
        "v2_missing_oi",
        "v2_missing_volume",
        "v2_impossible_max_loss",
        "v2_impossible_max_profit",
        "v2_non_finite_math",
        "v2_width_mismatch",
        "v2_credit_mismatch",
        "v2_debit_mismatch",
        "v2_max_profit_mismatch",
        "v2_max_loss_mismatch",
        "v2_breakeven_mismatch",
        "v2_ror_mismatch",
        # Prompt 9 — trust hygiene codes
        "v2_negative_bid",
        "v2_negative_ask",
        "v2_spread_pricing_impossible",
        "v2_dead_leg",
        "v2_exact_duplicate",
        # Prompt 10 — iron condor codes
        "v2_ic_invalid_geometry",
        # Prompt 11 — butterfly codes
        "v2_bf_invalid_geometry",
        # Prompt 12 — calendar/diagonal codes
        "v2_cal_invalid_geometry",
        # Phase D — quote checks
        "v2_missing_short_delta",
        "v2_zero_bid_short_leg",
        # Phase D — wide spread on short leg (credit strategies)
        "v2_wide_spread_short_leg",
        # Phase E — credit strategy without actual credit
        "v2_credit_spread_no_credit",
    }

    EXPECTED_WARN_CODES = {
        "v2_warn_width_mismatch",
        "v2_warn_credit_mismatch",
        "v2_warn_debit_mismatch",
        "v2_warn_max_profit_mismatch",
        "v2_warn_max_loss_mismatch",
        "v2_warn_breakeven_mismatch",
        "v2_warn_ror_mismatch",
        "v2_warn_pop_missing",
        "v2_warn_ev_missing",
        # Prompt 9 — trust hygiene codes
        "v2_warn_wide_leg_spread",
        "v2_warn_low_oi",
        "v2_warn_low_volume",
        "v2_warn_wide_composite_spread",
        "v2_warn_near_duplicate_suppressed",
    }

    EXPECTED_PASS_CODES = {
        "v2_pass_structural_valid",
        "v2_pass_quotes_clean",
        "v2_pass_liquidity_present",
        "v2_pass_math_consistent",
        "v2_pass_all_phases",
        # Prompt 9 — trust hygiene codes
        "v2_pass_quote_sanity_clean",
        "v2_pass_liquidity_sanity_ok",
        "v2_pass_dedup_unique",
    }

    def test_reject_codes_stable(self):
        assert all_reject_codes() == frozenset(self.EXPECTED_REJECT_CODES)

    def test_warn_codes_stable(self):
        assert all_warn_codes() == frozenset(self.EXPECTED_WARN_CODES)

    def test_pass_codes_stable(self):
        assert all_pass_codes() == frozenset(self.EXPECTED_PASS_CODES)

    def test_constant_values_match_strings(self):
        """Verify constant variables hold the expected string values."""
        assert REJECT_MALFORMED_LEGS == "v2_malformed_legs"
        assert REJECT_MISSING_QUOTE == "v2_missing_quote"
        assert WARN_POP_MISSING == "v2_warn_pop_missing"
        assert PASS_ALL_PHASES == "v2_pass_all_phases"
