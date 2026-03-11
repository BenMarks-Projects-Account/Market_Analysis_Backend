"""
Tests for Final Decision Response Contract v1.

Covers:
- contract shape and top-level schema
- all five decision states (approve, cautious_approve, watchlist, reject, insufficient_data)
- conviction, alignment, fit, risk enum handling
- validation — valid and invalid responses
- normalisation — partial, missing, invalid inputs
- placeholder/mock generation
- degraded/insufficient state handling
- warning flag propagation
- metadata and evidence preservation
- UI-integration readiness (card-renderable shapes)
"""

import copy
import pytest
from datetime import datetime, timezone

from app.services.decision_response_contract import (
    _RESPONSE_VERSION,
    VALID_DECISIONS,
    VALID_CONVICTION_LEVELS,
    VALID_ALIGNMENTS,
    VALID_FIT_LEVELS,
    VALID_POLICY_ALIGNMENTS,
    VALID_EVENT_RISK_LEVELS,
    VALID_SIZE_GUIDANCE,
    build_decision_response,
    build_placeholder_response,
    validate_decision_response,
    normalize_decision_response,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _full_approve(**overrides):
    """Build a complete approve response for testing."""
    defaults = dict(
        decision="approve",
        conviction="high",
        market_alignment="aligned",
        portfolio_fit="good",
        policy_alignment="clear",
        event_risk="low",
        time_horizon="1-5 DTE",
        summary="Strong setup across all factors.",
        reasons_for=["IV rank favourable", "Trend aligned"],
        reasons_against=["Short DTE"],
        key_risks=["Overnight gap risk"],
        size_guidance="normal",
        invalidation_notes=["Close if tested"],
        monitoring_notes=["Watch VIX"],
        warning_flags=[],
        evidence={"symbol": "SPY"},
        metadata={"upstream": "test"},
        source="model",
    )
    defaults.update(overrides)
    return build_decision_response(**defaults)


# =====================================================================
#  1. Contract Shape
# =====================================================================

class TestContractShape:
    """Top-level schema and required keys."""

    REQUIRED_KEYS = {
        "response_version", "generated_at", "status",
        "decision", "decision_label", "conviction",
        "market_alignment", "portfolio_fit", "policy_alignment",
        "event_risk", "time_horizon", "summary",
        "reasons_for", "reasons_against", "key_risks",
        "size_guidance", "invalidation_notes", "monitoring_notes",
        "warning_flags", "evidence", "metadata",
    }

    def test_all_required_keys_present(self):
        r = _full_approve()
        assert self.REQUIRED_KEYS <= set(r.keys())

    def test_version_locked(self):
        r = _full_approve()
        assert r["response_version"] == _RESPONSE_VERSION

    def test_generated_at_is_iso(self):
        r = _full_approve()
        dt = datetime.fromisoformat(r["generated_at"])
        assert dt.year >= 2025

    def test_list_fields_are_lists(self):
        r = _full_approve()
        for key in ("reasons_for", "reasons_against", "key_risks",
                     "invalidation_notes", "monitoring_notes", "warning_flags"):
            assert isinstance(r[key], list), f"{key} should be list"

    def test_dict_fields_are_dicts(self):
        r = _full_approve()
        assert isinstance(r["evidence"], dict)
        assert isinstance(r["metadata"], dict)

    def test_string_fields_are_strings(self):
        r = _full_approve()
        for key in ("summary", "decision_label", "time_horizon",
                     "generated_at", "status"):
            assert isinstance(r[key], str), f"{key} should be str"

    def test_metadata_includes_source(self):
        r = _full_approve(source="model")
        assert r["metadata"]["source"] == "model"


# =====================================================================
#  2. Decision States
# =====================================================================

class TestDecisionStates:
    """Each of the five decision values produces a valid response."""

    @pytest.mark.parametrize("decision", sorted(VALID_DECISIONS))
    def test_valid_decision_accepted(self, decision):
        r = build_decision_response(decision=decision)
        assert r["decision"] == decision

    @pytest.mark.parametrize("decision,label", [
        ("approve", "Approve"),
        ("cautious_approve", "Cautious Approve"),
        ("watchlist", "Watchlist"),
        ("reject", "Reject"),
        ("insufficient_data", "Insufficient Data"),
    ])
    def test_decision_labels(self, decision, label):
        r = build_decision_response(decision=decision)
        assert r["decision_label"] == label

    def test_invalid_decision_falls_to_insufficient(self):
        r = build_decision_response(decision="INVALID_VALUE")
        assert r["decision"] == "insufficient_data"

    def test_approve_status_complete(self):
        r = build_decision_response(decision="approve")
        assert r["status"] == "complete"

    def test_approve_with_warnings_status_partial(self):
        r = build_decision_response(
            decision="approve",
            warning_flags=["some_warning"],
        )
        assert r["status"] == "partial"

    def test_insufficient_data_status(self):
        r = build_decision_response(decision="insufficient_data")
        assert r["status"] == "insufficient_data"

    def test_reject_no_warnings_complete(self):
        r = build_decision_response(decision="reject")
        assert r["status"] == "complete"


# =====================================================================
#  3. Enum Validation
# =====================================================================

class TestEnumHandling:
    """Invalid enum values fall back safely."""

    def test_invalid_conviction_falls_to_none(self):
        r = build_decision_response(decision="approve", conviction="WRONG")
        assert r["conviction"] == "none"

    def test_invalid_alignment_falls_to_unknown(self):
        r = build_decision_response(decision="approve", market_alignment="WRONG")
        assert r["market_alignment"] == "unknown"

    def test_invalid_fit_falls_to_unknown(self):
        r = build_decision_response(decision="approve", portfolio_fit="WRONG")
        assert r["portfolio_fit"] == "unknown"

    def test_invalid_policy_falls_to_unknown(self):
        r = build_decision_response(decision="approve", policy_alignment="WRONG")
        assert r["policy_alignment"] == "unknown"

    def test_invalid_event_risk_falls_to_unknown(self):
        r = build_decision_response(decision="approve", event_risk="WRONG")
        assert r["event_risk"] == "unknown"

    def test_invalid_size_guidance_falls_to_normal(self):
        r = build_decision_response(decision="approve", size_guidance="WRONG")
        assert r["size_guidance"] == "normal"

    @pytest.mark.parametrize("conv", sorted(VALID_CONVICTION_LEVELS))
    def test_all_conviction_levels_accepted(self, conv):
        r = build_decision_response(decision="approve", conviction=conv)
        assert r["conviction"] == conv

    @pytest.mark.parametrize("align", sorted(VALID_ALIGNMENTS))
    def test_all_alignments_accepted(self, align):
        r = build_decision_response(decision="approve", market_alignment=align)
        assert r["market_alignment"] == align

    @pytest.mark.parametrize("fit", sorted(VALID_FIT_LEVELS))
    def test_all_fit_levels_accepted(self, fit):
        r = build_decision_response(decision="approve", portfolio_fit=fit)
        assert r["portfolio_fit"] == fit

    @pytest.mark.parametrize("policy", sorted(VALID_POLICY_ALIGNMENTS))
    def test_all_policy_alignments_accepted(self, policy):
        r = build_decision_response(decision="approve", policy_alignment=policy)
        assert r["policy_alignment"] == policy

    @pytest.mark.parametrize("risk", sorted(VALID_EVENT_RISK_LEVELS))
    def test_all_event_risk_levels_accepted(self, risk):
        r = build_decision_response(decision="approve", event_risk=risk)
        assert r["event_risk"] == risk

    @pytest.mark.parametrize("size", sorted(VALID_SIZE_GUIDANCE))
    def test_all_size_guidance_accepted(self, size):
        r = build_decision_response(decision="approve", size_guidance=size)
        assert r["size_guidance"] == size


# =====================================================================
#  4. Validation
# =====================================================================

class TestValidation:
    """validate_decision_response catches contract violations."""

    def test_valid_response_passes(self):
        r = _full_approve()
        ok, errors = validate_decision_response(r)
        assert ok is True
        assert errors == []

    def test_non_dict_fails(self):
        ok, errors = validate_decision_response("not a dict")
        assert ok is False
        assert "response is not a dict" in errors[0]

    def test_missing_keys_detected(self):
        r = {"decision": "approve"}
        ok, errors = validate_decision_response(r)
        assert ok is False
        assert any("missing required keys" in e for e in errors)

    def test_invalid_decision_detected(self):
        r = _full_approve()
        r["decision"] = "INVALID"
        ok, errors = validate_decision_response(r)
        assert ok is False
        assert any("invalid decision" in e for e in errors)

    def test_invalid_conviction_detected(self):
        r = _full_approve()
        r["conviction"] = "INVALID"
        ok, errors = validate_decision_response(r)
        assert ok is False
        assert any("invalid conviction" in e for e in errors)

    def test_invalid_version_detected(self):
        r = _full_approve()
        r["response_version"] = "99.0"
        ok, errors = validate_decision_response(r)
        assert ok is False
        assert any("response_version mismatch" in e for e in errors)

    def test_list_field_wrong_type_detected(self):
        r = _full_approve()
        r["reasons_for"] = "should be list"
        ok, errors = validate_decision_response(r)
        assert ok is False
        assert any("reasons_for must be a list" in e for e in errors)

    def test_dict_field_wrong_type_detected(self):
        r = _full_approve()
        r["evidence"] = "should be dict"
        ok, errors = validate_decision_response(r)
        assert ok is False
        assert any("evidence must be a dict" in e for e in errors)

    def test_all_five_decisions_validate(self):
        for d in sorted(VALID_DECISIONS):
            r = build_decision_response(decision=d)
            ok, errors = validate_decision_response(r)
            assert ok is True, f"{d} failed: {errors}"

    def test_placeholder_validates(self):
        r = build_placeholder_response()
        ok, errors = validate_decision_response(r)
        assert ok is True, f"Placeholder failed: {errors}"


# =====================================================================
#  5. Normalisation
# =====================================================================

class TestNormalisation:
    """normalize_decision_response fills missing fields safely."""

    def test_empty_dict_normalises(self):
        r = normalize_decision_response({})
        ok, errors = validate_decision_response(r)
        assert ok is True, f"Normalised empty dict invalid: {errors}"
        assert r["decision"] == "insufficient_data"
        assert r["conviction"] == "none"

    def test_none_input_normalises(self):
        r = normalize_decision_response(None)
        ok, errors = validate_decision_response(r)
        assert ok is True

    def test_non_dict_normalises(self):
        r = normalize_decision_response(42)
        ok, errors = validate_decision_response(r)
        assert ok is True

    def test_partial_dict_normalises(self):
        r = normalize_decision_response({"decision": "approve", "conviction": "high"})
        ok, errors = validate_decision_response(r)
        assert ok is True
        assert r["decision"] == "approve"

    def test_invalid_enums_corrected(self):
        r = normalize_decision_response({
            "decision": "BAD",
            "conviction": "BAD",
            "market_alignment": "BAD",
        })
        assert r["decision"] == "insufficient_data"
        assert r["conviction"] == "none"
        assert r["market_alignment"] == "unknown"

    def test_does_not_mutate_input(self):
        original = {"decision": "approve"}
        copy_of = copy.deepcopy(original)
        normalize_decision_response(original)
        assert original == copy_of

    def test_list_fields_coerced(self):
        r = normalize_decision_response({"reasons_for": "not a list"})
        assert isinstance(r["reasons_for"], list)

    def test_status_derived_correctly(self):
        r = normalize_decision_response({
            "decision": "approve",
            "warning_flags": ["degraded"],
        })
        assert r["status"] == "partial"

    def test_label_derived(self):
        r = normalize_decision_response({"decision": "cautious_approve"})
        assert r["decision_label"] == "Cautious Approve"


# =====================================================================
#  6. Placeholder / Mock
# =====================================================================

class TestPlaceholder:
    """build_placeholder_response for dev/testing workflows."""

    def test_placeholder_is_valid(self):
        r = build_placeholder_response()
        ok, errors = validate_decision_response(r)
        assert ok is True, f"Placeholder invalid: {errors}"

    def test_placeholder_has_warning_flag(self):
        r = build_placeholder_response()
        assert "placeholder_response" in r["warning_flags"]

    def test_placeholder_source_is_placeholder(self):
        r = build_placeholder_response()
        assert r["metadata"]["source"] == "placeholder"

    def test_placeholder_default_decision_is_watchlist(self):
        r = build_placeholder_response()
        assert r["decision"] == "watchlist"

    def test_placeholder_custom_decision(self):
        r = build_placeholder_response(decision="approve")
        assert r["decision"] == "approve"

    def test_placeholder_includes_symbol(self):
        r = build_placeholder_response(symbol="SPY", strategy="put_credit_spread")
        assert r["evidence"]["symbol"] == "SPY"
        assert r["evidence"]["strategy"] == "put_credit_spread"

    def test_placeholder_summary_mentions_symbol(self):
        r = build_placeholder_response(symbol="QQQ")
        assert "QQQ" in r["summary"]

    def test_placeholder_overrides_accepted(self):
        r = build_placeholder_response(
            decision="reject",
            conviction="high",
            summary="Custom summary",
        )
        assert r["decision"] == "reject"
        assert r["conviction"] == "high"
        assert r["summary"] == "Custom summary"


# =====================================================================
#  7. Warning Flags & Status
# =====================================================================

class TestWarningsAndStatus:
    """Warning flags affect status derivation."""

    def test_no_warnings_complete(self):
        r = build_decision_response(decision="approve", warning_flags=[])
        assert r["status"] == "complete"

    def test_warnings_make_partial(self):
        r = build_decision_response(
            decision="approve",
            warning_flags=["something"],
        )
        assert r["status"] == "partial"

    def test_insufficient_always_insufficient(self):
        r = build_decision_response(
            decision="insufficient_data",
            warning_flags=["something"],
        )
        assert r["status"] == "insufficient_data"

    def test_none_warning_flags_coerced(self):
        r = build_decision_response(decision="approve", warning_flags=None)
        assert r["warning_flags"] == []

    def test_multiple_warnings_preserved(self):
        flags = ["warn_a", "warn_b", "warn_c"]
        r = build_decision_response(decision="approve", warning_flags=flags)
        assert r["warning_flags"] == flags


# =====================================================================
#  8. Evidence & Metadata
# =====================================================================

class TestEvidenceMetadata:
    """Evidence and metadata blobs are preserved and extended correctly."""

    def test_evidence_preserved(self):
        ev = {"symbol": "SPY", "iv_rank": 0.55}
        r = build_decision_response(decision="approve", evidence=ev)
        assert r["evidence"]["symbol"] == "SPY"
        assert r["evidence"]["iv_rank"] == 0.55

    def test_none_evidence_defaults_empty(self):
        r = build_decision_response(decision="approve", evidence=None)
        assert r["evidence"] == {}

    def test_metadata_merged(self):
        r = build_decision_response(
            decision="approve",
            metadata={"custom_key": "custom_val"},
            source="model",
        )
        assert r["metadata"]["custom_key"] == "custom_val"
        assert r["metadata"]["source"] == "model"
        assert r["metadata"]["response_version"] == _RESPONSE_VERSION

    def test_none_metadata_defaults(self):
        r = build_decision_response(decision="approve", metadata=None)
        assert isinstance(r["metadata"], dict)
        assert "response_version" in r["metadata"]


# =====================================================================
#  9. Degraded / Insufficient Data Rendering
# =====================================================================

class TestDegradedState:
    """Degraded and insufficient states are handled safely."""

    def test_insufficient_all_unknowns(self):
        r = build_decision_response(decision="insufficient_data")
        assert r["market_alignment"] == "unknown"
        assert r["portfolio_fit"] == "unknown"
        assert r["conviction"] == "none"

    def test_empty_lists_safe(self):
        r = build_decision_response(decision="reject")
        assert r["reasons_for"] == []
        assert r["invalidation_notes"] == []
        assert r["monitoring_notes"] == []

    def test_partial_with_some_unknowns(self):
        r = build_decision_response(
            decision="cautious_approve",
            market_alignment="aligned",
            portfolio_fit="unknown",
            warning_flags=["portfolio_unavailable"],
        )
        assert r["status"] == "partial"
        assert r["market_alignment"] == "aligned"
        assert r["portfolio_fit"] == "unknown"

    def test_normalise_handles_deeply_broken_input(self):
        broken = {
            "decision": 123,
            "conviction": None,
            "reasons_for": "not list",
            "evidence": "not dict",
            "metadata": [1, 2, 3],
        }
        r = normalize_decision_response(broken)
        ok, errors = validate_decision_response(r)
        assert ok is True, f"Deeply broken input not normalised: {errors}"

    def test_all_optional_lists_empty_still_valid(self):
        r = build_decision_response(
            decision="approve",
            reasons_for=[],
            reasons_against=[],
            key_risks=[],
            invalidation_notes=[],
            monitoring_notes=[],
            warning_flags=[],
        )
        ok, errors = validate_decision_response(r)
        assert ok is True


# =====================================================================
#  10. Integration Scenarios
# =====================================================================

class TestIntegrationScenarios:
    """End-to-end scenarios proving contract + card readiness."""

    def test_full_approve_scenario(self):
        """Complete approve with all fields populated — card-ready."""
        r = _full_approve()
        ok, errors = validate_decision_response(r)
        assert ok is True
        assert r["decision"] == "approve"
        assert r["status"] == "complete"
        assert len(r["reasons_for"]) > 0
        assert len(r["key_risks"]) > 0

    def test_cautious_approve_scenario(self):
        """Cautious approve with warnings — card shows caution styling."""
        r = build_decision_response(
            decision="cautious_approve",
            conviction="moderate",
            market_alignment="neutral",
            portfolio_fit="acceptable",
            policy_alignment="conditional",
            event_risk="moderate",
            summary="Proceed with caution due to mixed signals.",
            reasons_for=["IV rank elevated"],
            reasons_against=["FOMC within window"],
            key_risks=["Event risk"],
            size_guidance="reduced",
            warning_flags=["event_risk_within_window"],
        )
        ok, errors = validate_decision_response(r)
        assert ok is True
        assert r["decision"] == "cautious_approve"
        assert r["status"] == "partial"
        assert r["size_guidance"] == "reduced"

    def test_watchlist_scenario(self):
        """Watchlist — interesting but not actionable."""
        r = build_decision_response(
            decision="watchlist",
            conviction="low",
            summary="IV too low for entry. Monitor.",
            size_guidance="none",
        )
        ok, errors = validate_decision_response(r)
        assert ok is True
        assert r["decision"] == "watchlist"

    def test_reject_scenario(self):
        """Reject with multiple blocking factors — card shows negative styling."""
        r = build_decision_response(
            decision="reject",
            conviction="high",
            market_alignment="misaligned",
            portfolio_fit="poor",
            policy_alignment="blocked",
            event_risk="high",
            reasons_against=["Market conflict", "Concentration", "Event risk"],
            key_risks=["3%+ drawdown possible"],
            size_guidance="none",
            warning_flags=["market_conflict", "policy_block"],
        )
        ok, errors = validate_decision_response(r)
        assert ok is True
        assert r["decision"] == "reject"
        assert r["status"] == "partial"  # has warnings

    def test_insufficient_data_scenario(self):
        """Insufficient data — card shows degraded styling."""
        r = build_decision_response(
            decision="insufficient_data",
            warning_flags=["candidate_missing", "market_unavailable"],
        )
        ok, errors = validate_decision_response(r)
        assert ok is True
        assert r["decision"] == "insufficient_data"
        assert r["status"] == "insufficient_data"

    def test_placeholder_drives_card(self):
        """Placeholder response is card-ready."""
        r = build_placeholder_response(symbol="SPY", strategy="put_credit_spread")
        ok, errors = validate_decision_response(r)
        assert ok is True
        assert r["decision"] == "watchlist"
        assert "placeholder_response" in r["warning_flags"]

    def test_normalised_broken_input_drives_card(self):
        """Even a deeply broken input normalises to a card-ready shape."""
        broken = {"foo": "bar", "decision": 999}
        r = normalize_decision_response(broken)
        ok, errors = validate_decision_response(r)
        assert ok is True

    def test_round_trip_build_validate(self):
        """Every decision value round-trips through build → validate."""
        for decision in sorted(VALID_DECISIONS):
            r = build_decision_response(decision=decision)
            ok, errors = validate_decision_response(r)
            assert ok is True, f"Round-trip failed for {decision}: {errors}"

    def test_round_trip_normalise_validate(self):
        """Normalised responses always pass validation."""
        inputs = [
            {},
            None,
            {"decision": "approve"},
            {"decision": "invalid", "conviction": "??"},
            {"reasons_for": 42, "evidence": "nope"},
        ]
        for inp in inputs:
            r = normalize_decision_response(inp)
            ok, errors = validate_decision_response(r)
            assert ok is True, f"Normalise→validate failed for {inp!r}: {errors}"

    def test_case_insensitive_enums(self):
        """Enum normalisation is case-insensitive."""
        r = build_decision_response(
            decision="APPROVE",
            conviction="HIGH",
            market_alignment="Aligned",
            portfolio_fit="GOOD",
            policy_alignment="Clear",
            event_risk="LOW",
            size_guidance="NORMAL",
        )
        assert r["decision"] == "approve"
        assert r["conviction"] == "high"
        assert r["market_alignment"] == "aligned"


# =====================================================================
#  11. UI Card Rendering Tests (contract → card compatibility)
# =====================================================================

class TestUICardCompatibility:
    """Verify that contract outputs contain all fields needed
    by the JS card renderer (BenTradeDecisionCard.render).
    The JS card reads these specific keys from the response."""

    CARD_REQUIRED_KEYS = {
        "decision", "decision_label", "conviction",
        "market_alignment", "portfolio_fit", "policy_alignment",
        "event_risk", "time_horizon", "summary",
        "reasons_for", "reasons_against", "key_risks",
        "size_guidance", "invalidation_notes", "monitoring_notes",
        "warning_flags", "response_version", "metadata",
    }

    @pytest.mark.parametrize("decision", sorted(VALID_DECISIONS))
    def test_card_keys_present_for_each_decision(self, decision):
        r = build_decision_response(decision=decision)
        missing = self.CARD_REQUIRED_KEYS - set(r.keys())
        assert missing == set(), f"Card-required keys missing for {decision}: {missing}"

    def test_card_keys_present_for_placeholder(self):
        r = build_placeholder_response()
        missing = self.CARD_REQUIRED_KEYS - set(r.keys())
        assert missing == set()

    def test_card_keys_present_for_normalised(self):
        r = normalize_decision_response({})
        missing = self.CARD_REQUIRED_KEYS - set(r.keys())
        assert missing == set()

    def test_metadata_has_source_for_footer(self):
        """Card footer reads metadata.source for the source tag."""
        r = _full_approve(source="model")
        assert "source" in r["metadata"]

    def test_generated_at_for_timestamp(self):
        """Card header reads generated_at for timestamp display."""
        r = _full_approve()
        assert r["generated_at"]
        dt = datetime.fromisoformat(r["generated_at"])
        assert dt.year >= 2025

    def test_decision_attribute_for_styling(self):
        """Card uses data-decision attribute from response.decision."""
        for decision in sorted(VALID_DECISIONS):
            r = build_decision_response(decision=decision)
            # Must be a clean string value usable as HTML attribute
            assert r["decision"] == decision
            assert " " not in r["decision"]
