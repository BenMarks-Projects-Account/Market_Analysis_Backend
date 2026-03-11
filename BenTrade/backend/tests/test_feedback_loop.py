"""Tests for Post-Trade Feedback Loop v1.1.

Coverage targets:
─── Contract-level tests
    - top-level shape (19 keys including trade_key)
    - trade_action enum handling
    - status enum handling
    - feedback_id generation
─── Snapshot tests
    - decision/candidate/market/policy/event/conflict/response snapshots
    - missing optional sections
    - snapshot compression / key filtering
    - snapshot_from_decision_packet
─── Execution/outcome tests
    - taken trade with execution snapshot
    - skipped trade without execution/outcome
    - exited trade with outcome snapshot
    - partial execution/outcome data
    - execution provenance tagging (v1.1)
─── Status derivation tests
    - minimal → partial
    - candidate present → recorded
    - outcome + taken → closed
─── Update/close workflow tests
    - update_feedback_execution
    - update_feedback_outcome
    - close_feedback_record
─── Warning/metadata tests
    - warning flags for missing context
    - metadata captures source/versioning
─── Validation tests
    - validate_feedback_record
    - v1.0 backward compatibility
─── Lifecycle correlation tests (v1.1)
    - trade_key field
─── Serialization tests (v1.1)
    - record_to_serializable / record_from_serializable
─── Inspectability tests (v1.1)
    - record_summary / snapshot_coverage
─── Role boundary tests (v1.1)
    - module role constant
─── Integration tests
    - with decision_packet from orchestrator
    - with decision_response from response contract
    - end-to-end taken + close workflow
"""

import copy
import json
import pytest

from app.services.feedback_loop import (
    VALID_EXECUTION_SOURCES,
    VALID_FILL_QUALITIES,
    VALID_STATUSES,
    VALID_TRADE_ACTIONS,
    _COMPATIBLE_VERSIONS,
    _FEEDBACK_VERSION,
    _MODULE_ROLE,
    build_feedback_record,
    close_feedback_record,
    normalise_execution_snapshot,
    normalise_outcome_snapshot,
    record_from_serializable,
    record_summary,
    record_to_serializable,
    snapshot_coverage,
    snapshot_from_decision_packet,
    update_feedback_execution,
    update_feedback_outcome,
    validate_feedback_record,
)


# ── Fixtures ──────────────────────────────────────────────────────────

def _candidate():
    return {
        "symbol": "SPY260320P00510000",
        "underlying": "SPY",
        "spread_type": "put_credit_spread",
        "expiration": "2026-03-20",
        "dte": 10,
        "short_strike": 510,
        "long_strike": 505,
        "net_credit": 1.25,
        "width": 5.0,
        "max_profit_per_share": 1.25,
        "max_loss_per_share": 3.75,
        "return_on_risk": 0.333,
        "pop_delta_approx": 0.72,
        "expected_fill_price": 1.20,
        "iv": 0.22,
        "trade_quality_score": 82,
        "confidence": 0.85,
    }


def _market():
    return {
        "overall_bias": "neutral",
        "composite_score": 62,
        "regime_label": "range_bound",
        "trend_label": "sideways",
        "volatility_label": "moderate",
        "confidence": 0.78,
        "signal_quality": "medium",
        "warning_flags": [],
    }


def _policy():
    return {
        "policy_decision": "pass",
        "severity": "low",
        "checks_passed": 8,
        "checks_failed": 0,
        "total_checks": 8,
        "pass_rate": 1.0,
        "failed_check_names": [],
    }


def _events():
    return {
        "event_risk_state": "quiet",
        "total_events": 2,
        "high_impact_events": 0,
    }


def _conflicts():
    return {
        "has_conflicts": False,
        "conflict_count": 0,
        "max_severity": "none",
    }


def _decision_response():
    return {
        "decision": "approve",
        "decision_label": "Approve",
        "conviction": "high",
        "market_alignment": "aligned",
        "portfolio_fit": "good",
        "policy_alignment": "clear",
        "event_risk": "low",
        "size_guidance": "normal",
        "summary": "High-probability put credit spread on SPY.",
        "reasons_for": ["strong support level", "low IV rank"],
        "reasons_against": [],
        "key_risks": ["overnight gap risk"],
        "warning_flags": [],
        "status": "complete",
        "confidence_assessment": {"adjusted_score": 0.92, "confidence_label": "high"},
    }


def _execution():
    return {
        "broker_order_id": "ORD-12345",
        "broker": "tradier",
        "order_status": "FILLED",
        "fill_price": 1.18,
        "fill_quantity": 5,
        "fill_timestamp": "2026-03-10T14:30:00Z",
        "limit_price": 1.20,
        "estimated_max_profit": 590.0,
        "estimated_max_loss": 1910.0,
        "mode": "paper",
    }


def _outcome():
    return {
        "realized_pnl": 425.0,
        "exit_reason": "profit_target",
        "close_timestamp": "2026-03-18T15:55:00Z",
        "hold_duration_days": 8,
        "final_status": "closed_profitable",
    }


def _decision_packet():
    """Simulate a decision packet from trade_decision_orchestrator."""
    return {
        "decision_packet_version": "1.0",
        "generated_at": "2026-03-10T10:00:00Z",
        "status": "complete",
        "summary": "SPY put credit spread — all systems green.",
        "candidate": _candidate(),
        "market": _market(),
        "portfolio": {
            "total_positions": 3,
            "total_delta": -12.5,
            "greeks_coverage": "full",
            "risk_flags": [],
        },
        "policy": _policy(),
        "events": _events(),
        "conflicts": _conflicts(),
        "quality_overview": {
            "packet_status": "complete",
            "decision_ready": True,
            "coverage_ratio": 1.0,
            "confidence_assessment": {"adjusted_score": 0.88},
        },
        "warning_flags": [],
    }


# =====================================================================
#  Contract-level tests
# =====================================================================

class TestFeedbackRecordShape:
    """Verify top-level shape of a feedback record."""

    EXPECTED_KEYS = {
        "feedback_version", "feedback_id", "recorded_at", "status",
        "trade_action", "trade_key",
        "decision_snapshot", "candidate_snapshot", "market_snapshot",
        "portfolio_snapshot", "policy_snapshot", "event_snapshot",
        "conflict_snapshot", "response_snapshot",
        "execution_snapshot", "outcome_snapshot",
        "review_notes", "warning_flags", "evidence", "metadata",
    }

    def test_minimal_record_has_all_keys(self):
        r = build_feedback_record()
        assert self.EXPECTED_KEYS == set(r.keys())

    def test_version_is_current(self):
        r = build_feedback_record()
        assert r["feedback_version"] == _FEEDBACK_VERSION

    def test_feedback_id_is_string(self):
        r = build_feedback_record()
        assert isinstance(r["feedback_id"], str)
        assert r["feedback_id"].startswith("fb-")

    def test_recorded_at_is_iso(self):
        r = build_feedback_record()
        assert len(r["recorded_at"]) > 10

    def test_review_notes_default_empty_list(self):
        r = build_feedback_record()
        assert r["review_notes"] == []

    def test_evidence_default_empty_dict(self):
        r = build_feedback_record()
        assert r["evidence"] == {}

    def test_metadata_has_version(self):
        r = build_feedback_record()
        assert r["metadata"]["feedback_version"] == _FEEDBACK_VERSION

    def test_full_record_has_all_keys(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            market_snapshot=_market(),
            policy_snapshot=_policy(),
            event_snapshot=_events(),
            conflict_snapshot=_conflicts(),
            decision_response=_decision_response(),
            execution_snapshot=_execution(),
            outcome_snapshot=_outcome(),
            review_notes=["good trade"],
            evidence={"model": "v3"},
            metadata={"user": "ben"},
            source="scanner",
        )
        assert self.EXPECTED_KEYS == set(r.keys())


# =====================================================================
#  Trade action enum tests
# =====================================================================

class TestTradeAction:
    """Verify trade_action handling."""

    def test_taken(self):
        r = build_feedback_record(trade_action="taken")
        assert r["trade_action"] == "taken"

    def test_skipped(self):
        r = build_feedback_record(trade_action="skipped")
        assert r["trade_action"] == "skipped"

    def test_modified(self):
        r = build_feedback_record(trade_action="modified")
        assert r["trade_action"] == "modified"

    def test_exited(self):
        r = build_feedback_record(trade_action="exited")
        assert r["trade_action"] == "exited"

    def test_unknown_default(self):
        r = build_feedback_record()
        assert r["trade_action"] == "unknown"

    def test_invalid_action_becomes_unknown(self):
        r = build_feedback_record(trade_action="INVALID_ACTION")
        assert r["trade_action"] == "unknown"

    def test_none_action_becomes_unknown(self):
        r = build_feedback_record(trade_action=None)
        assert r["trade_action"] == "unknown"

    def test_all_valid_actions_accepted(self):
        for action in VALID_TRADE_ACTIONS:
            r = build_feedback_record(trade_action=action)
            assert r["trade_action"] == action


# =====================================================================
#  Status derivation tests
# =====================================================================

class TestStatusDerivation:
    """Verify status derivation rules."""

    def test_minimal_is_partial(self):
        r = build_feedback_record()
        assert r["status"] == "partial"

    def test_candidate_present_is_recorded(self):
        r = build_feedback_record(
            trade_action="skipped",
            candidate_snapshot=_candidate(),
        )
        assert r["status"] == "recorded"

    def test_response_present_is_recorded(self):
        r = build_feedback_record(
            trade_action="skipped",
            decision_response=_decision_response(),
        )
        assert r["status"] == "recorded"

    def test_taken_with_outcome_is_closed(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            outcome_snapshot=_outcome(),
        )
        assert r["status"] == "closed"

    def test_exited_with_outcome_is_closed(self):
        r = build_feedback_record(
            trade_action="exited",
            candidate_snapshot=_candidate(),
            outcome_snapshot=_outcome(),
        )
        assert r["status"] == "closed"

    def test_modified_with_outcome_is_closed(self):
        r = build_feedback_record(
            trade_action="modified",
            candidate_snapshot=_candidate(),
            outcome_snapshot=_outcome(),
        )
        assert r["status"] == "closed"

    def test_skipped_with_outcome_not_closed(self):
        """Skipped trade with outcome stays recorded (not closed)."""
        r = build_feedback_record(
            trade_action="skipped",
            candidate_snapshot=_candidate(),
            outcome_snapshot=_outcome(),
        )
        # Outcome on a skipped trade doesn't close it
        assert r["status"] == "recorded"

    def test_taken_without_outcome_is_recorded(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            execution_snapshot=_execution(),
        )
        assert r["status"] == "recorded"


# =====================================================================
#  Snapshot tests
# =====================================================================

class TestSnapshots:
    """Verify snapshot extraction and compression."""

    def test_candidate_snapshot_preserves_key_fields(self):
        r = build_feedback_record(candidate_snapshot=_candidate())
        snap = r["candidate_snapshot"]
        assert snap is not None
        assert snap["symbol"] == "SPY260320P00510000"
        assert snap["short_strike"] == 510
        assert snap["net_credit"] == 1.25

    def test_candidate_snapshot_filters_unknown_keys(self):
        cand = _candidate()
        cand["random_extra_field"] = "should_be_filtered"
        r = build_feedback_record(candidate_snapshot=cand)
        assert "random_extra_field" not in r["candidate_snapshot"]

    def test_market_snapshot_preserves_key_fields(self):
        r = build_feedback_record(market_snapshot=_market())
        snap = r["market_snapshot"]
        assert snap is not None
        assert snap["overall_bias"] == "neutral"

    def test_policy_snapshot_preserves_key_fields(self):
        r = build_feedback_record(policy_snapshot=_policy())
        snap = r["policy_snapshot"]
        assert snap is not None
        assert snap["policy_decision"] == "pass"

    def test_event_snapshot_preserves_key_fields(self):
        r = build_feedback_record(event_snapshot=_events())
        snap = r["event_snapshot"]
        assert snap is not None
        assert snap["event_risk_state"] == "quiet"

    def test_conflict_snapshot_preserves_key_fields(self):
        r = build_feedback_record(conflict_snapshot=_conflicts())
        snap = r["conflict_snapshot"]
        assert snap is not None
        assert snap["has_conflicts"] is False

    def test_response_snapshot_preserves_key_fields(self):
        r = build_feedback_record(decision_response=_decision_response())
        snap = r["response_snapshot"]
        assert snap is not None
        assert snap["decision"] == "approve"
        assert snap["conviction"] == "high"
        assert snap["confidence_assessment"]["adjusted_score"] == 0.92

    def test_missing_snapshot_is_none(self):
        r = build_feedback_record()
        assert r["candidate_snapshot"] is None
        assert r["market_snapshot"] is None
        assert r["portfolio_snapshot"] is None
        assert r["response_snapshot"] is None
        assert r["execution_snapshot"] is None
        assert r["outcome_snapshot"] is None

    def test_snapshot_is_deep_copy(self):
        """Verify mutable fields are frozen (not by-reference)."""
        cand = _candidate()
        r = build_feedback_record(candidate_snapshot=cand)
        cand["net_credit"] = 999.0
        assert r["candidate_snapshot"]["net_credit"] == 1.25

    def test_empty_dict_snapshot_is_none(self):
        r = build_feedback_record(candidate_snapshot={})
        assert r["candidate_snapshot"] is None


class TestSnapshotFromDecisionPacket:
    """Verify snapshot_from_decision_packet()."""

    def test_extracts_all_sections(self):
        pkt = _decision_packet()
        snaps = snapshot_from_decision_packet(pkt)
        assert snaps["candidate"] is not None
        assert snaps["market"] is not None
        assert snaps["portfolio"] is not None
        assert snaps["policy"] is not None
        assert snaps["events"] is not None
        assert snaps["conflicts"] is not None

    def test_none_packet(self):
        snaps = snapshot_from_decision_packet(None)
        for v in snaps.values():
            assert v is None

    def test_empty_packet(self):
        snaps = snapshot_from_decision_packet({})
        for v in snaps.values():
            assert v is None

    def test_partial_packet(self):
        pkt = {"candidate": _candidate()}
        snaps = snapshot_from_decision_packet(pkt)
        assert snaps["candidate"] is not None
        assert snaps["market"] is None


# =====================================================================
#  Execution/outcome tests
# =====================================================================

class TestExecutionSnapshot:
    """Verify execution snapshot handling."""

    def test_taken_with_execution(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            execution_snapshot=_execution(),
        )
        ex = r["execution_snapshot"]
        assert ex is not None
        assert ex["broker_order_id"] == "ORD-12345"
        assert ex["fill_price"] == 1.18
        assert ex["fill_quantity"] == 5

    def test_skipped_without_execution(self):
        r = build_feedback_record(
            trade_action="skipped",
            candidate_snapshot=_candidate(),
        )
        assert r["execution_snapshot"] is None

    def test_partial_execution_data(self):
        """Only some execution fields available."""
        partial = {"broker_order_id": "ORD-999", "order_status": "WORKING"}
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            execution_snapshot=partial,
        )
        ex = r["execution_snapshot"]
        assert ex is not None
        assert ex["broker_order_id"] == "ORD-999"
        assert "fill_price" not in ex

    def test_normalise_execution_none(self):
        assert normalise_execution_snapshot(None) is None

    def test_normalise_execution_empty(self):
        assert normalise_execution_snapshot({}) is None

    def test_normalise_execution_extra_keys_preserved(self):
        """Future-proof: unknown keys are kept."""
        ex = {"fill_price": 1.50, "custom_field": "hello"}
        result = normalise_execution_snapshot(ex)
        assert result["fill_price"] == 1.50
        assert result["custom_field"] == "hello"


class TestOutcomeSnapshot:
    """Verify outcome snapshot handling."""

    def test_exited_with_outcome(self):
        r = build_feedback_record(
            trade_action="exited",
            candidate_snapshot=_candidate(),
            outcome_snapshot=_outcome(),
        )
        out = r["outcome_snapshot"]
        assert out is not None
        assert out["realized_pnl"] == 425.0
        assert out["exit_reason"] == "profit_target"

    def test_taken_without_outcome(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            execution_snapshot=_execution(),
        )
        assert r["outcome_snapshot"] is None

    def test_partial_outcome_data(self):
        partial = {"realized_pnl": -150.0}
        r = build_feedback_record(
            trade_action="exited",
            candidate_snapshot=_candidate(),
            outcome_snapshot=partial,
        )
        out = r["outcome_snapshot"]
        assert out["realized_pnl"] == -150.0
        assert "exit_reason" not in out

    def test_normalise_outcome_none(self):
        assert normalise_outcome_snapshot(None) is None

    def test_normalise_outcome_empty(self):
        assert normalise_outcome_snapshot({}) is None


# =====================================================================
#  Decision snapshot tests
# =====================================================================

class TestDecisionSnapshot:
    """Verify decision_snapshot from packet."""

    def test_from_packet(self):
        r = build_feedback_record(
            decision_packet=_decision_packet(),
        )
        ds = r["decision_snapshot"]
        assert ds is not None
        assert ds["status"] == "complete"
        assert ds["summary"] == "SPY put credit spread — all systems green."
        assert ds["quality_overview"]["coverage_ratio"] == 1.0

    def test_warning_flags_in_decision_snapshot(self):
        pkt = _decision_packet()
        pkt["warning_flags"] = ["stale_data", "low_liquidity"]
        r = build_feedback_record(decision_packet=pkt)
        ds = r["decision_snapshot"]
        assert ds["warning_flags"] == ["stale_data", "low_liquidity"]

    def test_no_packet_no_decision_snapshot(self):
        r = build_feedback_record()
        assert r["decision_snapshot"] is None


# =====================================================================
#  Packet-derived snapshots
# =====================================================================

class TestPacketDerivedSnapshots:
    """Verify subsystem snapshots extracted from decision_packet."""

    def test_candidate_from_packet(self):
        r = build_feedback_record(decision_packet=_decision_packet())
        assert r["candidate_snapshot"] is not None
        assert r["candidate_snapshot"]["underlying"] == "SPY"

    def test_market_from_packet(self):
        r = build_feedback_record(decision_packet=_decision_packet())
        assert r["market_snapshot"] is not None
        assert r["market_snapshot"]["overall_bias"] == "neutral"

    def test_explicit_snapshot_overrides_packet(self):
        """Individual snapshots take priority over packet-derived."""
        custom_cand = {"symbol": "QQQ_CUSTOM", "underlying": "QQQ"}
        r = build_feedback_record(
            decision_packet=_decision_packet(),
            candidate_snapshot=custom_cand,
        )
        assert r["candidate_snapshot"]["underlying"] == "QQQ"


# =====================================================================
#  Update / close workflow tests
# =====================================================================

class TestUpdateExecution:
    """Verify update_feedback_execution()."""

    def test_adds_execution(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
        )
        assert r["execution_snapshot"] is None
        updated = update_feedback_execution(r, _execution())
        assert updated["execution_snapshot"] is not None
        assert updated["execution_snapshot"]["fill_price"] == 1.18
        assert "execution_updated_at" in updated["metadata"]

    def test_does_not_mutate_original(self):
        r = build_feedback_record(trade_action="taken", candidate_snapshot=_candidate())
        updated = update_feedback_execution(r, _execution())
        assert r["execution_snapshot"] is None
        assert updated["execution_snapshot"] is not None

    def test_raises_on_non_dict(self):
        with pytest.raises(ValueError):
            update_feedback_execution("not_a_dict", _execution())


class TestUpdateOutcome:
    """Verify update_feedback_outcome()."""

    def test_adds_outcome(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            execution_snapshot=_execution(),
        )
        updated = update_feedback_outcome(r, _outcome())
        assert updated["outcome_snapshot"]["realized_pnl"] == 425.0
        assert "outcome_updated_at" in updated["metadata"]

    def test_close_flag_sets_closed(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
        )
        updated = update_feedback_outcome(r, _outcome(), close=True)
        assert updated["status"] == "closed"

    def test_without_close_derives_status(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
        )
        updated = update_feedback_outcome(r, _outcome())
        assert updated["status"] == "closed"  # taken + outcome → closed

    def test_does_not_mutate_original(self):
        r = build_feedback_record(trade_action="taken", candidate_snapshot=_candidate())
        updated = update_feedback_outcome(r, _outcome())
        assert r["outcome_snapshot"] is None

    def test_raises_on_non_dict(self):
        with pytest.raises(ValueError):
            update_feedback_outcome("x", _outcome())


class TestCloseFeedbackRecord:
    """Verify close_feedback_record()."""

    def test_basic_close(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            execution_snapshot=_execution(),
        )
        closed = close_feedback_record(r)
        assert closed["status"] == "closed"
        assert "closed_at" in closed["metadata"]

    def test_close_with_outcome(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
        )
        closed = close_feedback_record(r, outcome_snapshot=_outcome())
        assert closed["status"] == "closed"
        assert closed["outcome_snapshot"]["realized_pnl"] == 425.0

    def test_close_with_review_notes(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            review_notes=["initial note"],
        )
        closed = close_feedback_record(r, review_notes=["final review: good exit"])
        assert "initial note" in closed["review_notes"]
        assert "final review: good exit" in closed["review_notes"]

    def test_does_not_mutate_original(self):
        r = build_feedback_record(trade_action="taken", candidate_snapshot=_candidate())
        closed = close_feedback_record(r)
        assert r["status"] != "closed"

    def test_raises_on_non_dict(self):
        with pytest.raises(ValueError):
            close_feedback_record(42)


# =====================================================================
#  Warning flags tests
# =====================================================================

class TestWarningFlags:
    """Verify warning flags for missing context."""

    def test_minimal_record_warns_about_missing(self):
        r = build_feedback_record()
        wf = r["warning_flags"]
        assert "missing_candidate_snapshot" in wf
        assert "missing_decision_context" in wf
        assert "missing_market_snapshot" in wf

    def test_taken_without_execution_warns(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
        )
        assert "taken_without_execution_data" in r["warning_flags"]

    def test_taken_without_outcome_warns(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            execution_snapshot=_execution(),
        )
        assert "no_outcome_data" in r["warning_flags"]

    def test_complete_record_no_missing_warnings(self):
        r = build_feedback_record(
            trade_action="taken",
            decision_packet=_decision_packet(),
            decision_response=_decision_response(),
            execution_snapshot=_execution(),
            outcome_snapshot=_outcome(),
        )
        wf = r["warning_flags"]
        assert "missing_candidate_snapshot" not in wf
        assert "missing_decision_context" not in wf
        assert "missing_market_snapshot" not in wf
        assert "taken_without_execution_data" not in wf

    def test_custom_warnings_preserved(self):
        r = build_feedback_record(
            warning_flags=["custom_flag"],
            candidate_snapshot=_candidate(),
            decision_response=_decision_response(),
        )
        assert "custom_flag" in r["warning_flags"]

    def test_skipped_no_execution_warning(self):
        """Skipped trade should NOT warn about missing execution."""
        r = build_feedback_record(
            trade_action="skipped",
            candidate_snapshot=_candidate(),
            decision_response=_decision_response(),
        )
        assert "taken_without_execution_data" not in r["warning_flags"]


# =====================================================================
#  Metadata tests
# =====================================================================

class TestMetadata:
    """Verify metadata handling."""

    def test_source_propagated(self):
        r = build_feedback_record(source="scanner")
        assert r["metadata"]["source"] == "scanner"

    def test_custom_metadata_merged(self):
        r = build_feedback_record(metadata={"user": "ben", "session": "abc"})
        assert r["metadata"]["user"] == "ben"
        assert r["metadata"]["session"] == "abc"

    def test_version_in_metadata(self):
        r = build_feedback_record()
        assert r["metadata"]["feedback_version"] == _FEEDBACK_VERSION


# =====================================================================
#  Validation tests
# =====================================================================

class TestValidation:
    """Verify validate_feedback_record()."""

    def test_valid_minimal(self):
        r = build_feedback_record()
        ok, errs = validate_feedback_record(r)
        assert ok is True, errs

    def test_valid_full(self):
        r = build_feedback_record(
            trade_action="taken",
            decision_packet=_decision_packet(),
            decision_response=_decision_response(),
            execution_snapshot=_execution(),
            outcome_snapshot=_outcome(),
            review_notes=["good"],
            source="test",
        )
        ok, errs = validate_feedback_record(r)
        assert ok is True, errs

    def test_non_dict_fails(self):
        ok, errs = validate_feedback_record("not_a_dict")
        assert ok is False
        assert "must be a dict" in errs[0]

    def test_missing_required_keys_fails(self):
        ok, errs = validate_feedback_record({})
        assert ok is False
        assert any("missing required" in e for e in errs)

    def test_invalid_trade_action_fails(self):
        r = build_feedback_record()
        r["trade_action"] = "BAD_ACTION"
        ok, errs = validate_feedback_record(r)
        assert ok is False

    def test_invalid_status_fails(self):
        r = build_feedback_record()
        r["status"] = "INVALID_STATUS"
        ok, errs = validate_feedback_record(r)
        assert ok is False

    def test_bad_version_fails(self):
        r = build_feedback_record()
        r["feedback_version"] = "99.0"
        ok, errs = validate_feedback_record(r)
        assert ok is False

    def test_snapshot_wrong_type_fails(self):
        r = build_feedback_record()
        r["candidate_snapshot"] = "not_a_dict"
        ok, errs = validate_feedback_record(r)
        assert ok is False
        assert any("candidate_snapshot" in e for e in errs)

    def test_list_field_wrong_type_fails(self):
        r = build_feedback_record()
        r["warning_flags"] = "not_a_list"
        ok, errs = validate_feedback_record(r)
        assert ok is False

    def test_bad_feedback_id_fails(self):
        r = build_feedback_record()
        r["feedback_id"] = ""
        ok, errs = validate_feedback_record(r)
        assert ok is False


# =====================================================================
#  Integration: with orchestrator + response contract
# =====================================================================

class TestIntegrationOrchestrator:
    """Integration with trade_decision_orchestrator."""

    def test_build_from_real_packet(self):
        from app.services.trade_decision_orchestrator import build_decision_packet
        pkt = build_decision_packet(
            candidate=_candidate(),
            market=_market(),
            conflicts=_conflicts(),
            portfolio={"total_positions": 2},
            policy=_policy(),
            events=_events(),
        )
        r = build_feedback_record(
            trade_action="taken",
            decision_packet=pkt,
            execution_snapshot=_execution(),
            source="integration_test",
        )
        ok, errs = validate_feedback_record(r)
        assert ok is True, errs
        assert r["candidate_snapshot"] is not None
        assert r["market_snapshot"] is not None
        assert r["decision_snapshot"] is not None
        assert r["decision_snapshot"]["quality_overview"]["decision_ready"] is not None


class TestIntegrationResponseContract:
    """Integration with decision_response_contract."""

    def test_build_from_real_response(self):
        from app.services.decision_response_contract import build_decision_response
        resp = build_decision_response(
            decision="approve",
            conviction="high",
            market_alignment="aligned",
            summary="Strong setup on SPY credit spread.",
        )
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            decision_response=resp,
            execution_snapshot=_execution(),
        )
        ok, errs = validate_feedback_record(r)
        assert ok is True, errs
        assert r["response_snapshot"]["decision"] == "approve"
        assert r["response_snapshot"]["conviction"] == "high"
        assert "confidence_assessment" in r["response_snapshot"]


class TestEndToEndWorkflow:
    """End-to-end: create → update execution → update outcome → close."""

    def test_full_lifecycle(self):
        # 1. Record the decision
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            decision_response=_decision_response(),
            source="workflow_test",
        )
        assert r["status"] == "recorded"
        assert r["execution_snapshot"] is None

        # 2. Update with execution data
        r2 = update_feedback_execution(r, _execution())
        assert r2["execution_snapshot"]["fill_price"] == 1.18
        assert r2["status"] == "recorded"  # no outcome yet

        # 3. Update with outcome
        r3 = update_feedback_outcome(r2, _outcome())
        assert r3["outcome_snapshot"]["realized_pnl"] == 425.0
        assert r3["status"] == "closed"  # taken + outcome → closed

        # 4. Close with final review
        r4 = close_feedback_record(r3, review_notes=["Final: solid entry and exit."])
        assert r4["status"] == "closed"
        assert "Final: solid entry and exit." in r4["review_notes"]

        # Validate final record
        ok, errs = validate_feedback_record(r4)
        assert ok is True, errs


# =====================================================================
#  Module role boundary tests (v1.1)
# =====================================================================

class TestModuleRole:
    """Verify the capture-only role boundary."""

    def test_module_role_is_capture(self):
        assert _MODULE_ROLE == "capture"

    def test_version_is_1_1(self):
        assert _FEEDBACK_VERSION == "1.1"

    def test_compatible_versions_include_1_0(self):
        assert "1.0" in _COMPATIBLE_VERSIONS
        assert "1.1" in _COMPATIBLE_VERSIONS


# =====================================================================
#  Execution provenance tagging tests (v1.1)
# =====================================================================

class TestExecutionProvenanceTagging:
    """Verify execution_source and fill_quality tagging."""

    def test_paper_sim_tagged(self):
        ex = normalise_execution_snapshot({
            "fill_price": 1.18,
            "execution_source": "paper_sim",
            "fill_quality": "estimated",
        })
        assert ex["execution_source"] == "paper_sim"
        assert ex["fill_quality"] == "estimated"

    def test_live_broker_tagged(self):
        ex = normalise_execution_snapshot({
            "fill_price": 1.18,
            "execution_source": "live_broker",
            "fill_quality": "confirmed",
        })
        assert ex["execution_source"] == "live_broker"
        assert ex["fill_quality"] == "confirmed"

    def test_manual_entry_tagged(self):
        ex = normalise_execution_snapshot({
            "fill_price": 1.18,
            "execution_source": "manual_entry",
            "fill_quality": "unverified",
        })
        assert ex["execution_source"] == "manual_entry"
        assert ex["fill_quality"] == "unverified"

    def test_missing_source_defaults_unknown(self):
        ex = normalise_execution_snapshot({"fill_price": 1.18})
        assert ex["execution_source"] == "unknown"
        assert ex["fill_quality"] == "unverified"

    def test_invalid_source_defaults_unknown(self):
        ex = normalise_execution_snapshot({
            "fill_price": 1.0,
            "execution_source": "INVALID",
            "fill_quality": "INVALID",
        })
        assert ex["execution_source"] == "unknown"
        assert ex["fill_quality"] == "unverified"

    def test_valid_execution_sources_enum(self):
        assert VALID_EXECUTION_SOURCES == {
            "live_broker", "paper_sim", "manual_entry", "unknown",
        }

    def test_valid_fill_qualities_enum(self):
        assert VALID_FILL_QUALITIES == {
            "confirmed", "estimated", "unverified",
        }

    def test_provenance_in_full_record(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            execution_snapshot={
                "fill_price": 1.18,
                "execution_source": "live_broker",
                "fill_quality": "confirmed",
            },
        )
        ex = r["execution_snapshot"]
        assert ex["execution_source"] == "live_broker"
        assert ex["fill_quality"] == "confirmed"

    def test_provenance_validates_in_record(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            execution_snapshot={"fill_price": 1.18},
        )
        ok, errs = validate_feedback_record(r)
        assert ok is True, errs


# =====================================================================
#  Trade key correlation tests (v1.1)
# =====================================================================

class TestTradeKeyCorrelation:
    """Verify trade_key lifecycle correlation field."""

    def test_trade_key_in_record(self):
        tk = "SPY|2026-03-20|put_credit_spread|510|505|10"
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            trade_key=tk,
        )
        assert r["trade_key"] == tk

    def test_trade_key_default_none(self):
        r = build_feedback_record()
        assert r["trade_key"] is None

    def test_trade_key_in_expected_keys(self):
        r = build_feedback_record()
        assert "trade_key" in r

    def test_trade_key_validated_type(self):
        r = build_feedback_record(trade_key="abc")
        r["trade_key"] = 123  # Invalid type
        ok, errs = validate_feedback_record(r)
        assert ok is False
        assert any("trade_key" in e for e in errs)

    def test_trade_key_none_validates(self):
        r = build_feedback_record()
        ok, errs = validate_feedback_record(r)
        assert ok is True, errs

    def test_trade_key_affects_feedback_id(self):
        """Different trade_keys produce different feedback IDs."""
        r1 = build_feedback_record(trade_key="KEY_A")
        r2 = build_feedback_record(trade_key="KEY_B")
        # IDs differ because different trade_keys + different timestamps
        assert r1["feedback_id"] != r2["feedback_id"]

    def test_trade_key_stripped(self):
        r = build_feedback_record(trade_key="  SPY|test  ")
        assert r["trade_key"] == "SPY|test"


# =====================================================================
#  Serialization readiness tests (v1.1)
# =====================================================================

class TestSerializationReadiness:
    """Verify record_to_serializable / record_from_serializable."""

    def test_to_serializable_returns_json_safe(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            execution_snapshot=_execution(),
            outcome_snapshot=_outcome(),
            trade_key="SPY|test",
        )
        s = record_to_serializable(r)
        # Must not raise
        json_str = json.dumps(s)
        assert isinstance(json_str, str)
        assert len(json_str) > 0

    def test_to_serializable_deep_copies(self):
        r = build_feedback_record(candidate_snapshot=_candidate())
        s = record_to_serializable(r)
        s["candidate_snapshot"]["symbol"] = "CHANGED"
        assert r["candidate_snapshot"]["symbol"] != "CHANGED"

    def test_to_serializable_rejects_non_dict(self):
        with pytest.raises(ValueError):
            record_to_serializable("not_a_dict")

    def test_from_serializable_round_trip(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            trade_key="SPY|round_trip",
        )
        s = record_to_serializable(r)
        r2 = record_from_serializable(s)
        assert r2["trade_action"] == "taken"
        assert r2["trade_key"] == "SPY|round_trip"
        assert r2["feedback_version"] == _FEEDBACK_VERSION

    def test_from_serializable_adds_trade_key_for_v1_0(self):
        """v1.0 records without trade_key get it added."""
        old_record = {
            "feedback_version": "1.0",
            "feedback_id": "fb-test",
            "recorded_at": "2026-01-01T00:00:00Z",
            "status": "recorded",
            "trade_action": "taken",
        }
        r = record_from_serializable(old_record)
        assert r["trade_key"] is None

    def test_from_serializable_rejects_unknown_version(self):
        with pytest.raises(ValueError, match="unsupported"):
            record_from_serializable({"feedback_version": "99.0"})

    def test_from_serializable_rejects_non_dict(self):
        with pytest.raises(ValueError):
            record_from_serializable("not_a_dict")


# =====================================================================
#  Snapshot coverage tests (v1.1)
# =====================================================================

class TestSnapshotCoverage:
    """Verify snapshot_coverage() inspectability."""

    def test_empty_record_all_false(self):
        r = build_feedback_record()
        cov = snapshot_coverage(r)
        assert all(v is False for v in cov.values())
        assert len(cov) == 10

    def test_full_record_counts(self):
        r = build_feedback_record(
            trade_action="taken",
            decision_packet=_decision_packet(),
            decision_response=_decision_response(),
            execution_snapshot=_execution(),
            outcome_snapshot=_outcome(),
        )
        cov = snapshot_coverage(r)
        present = sum(1 for v in cov.values() if v)
        assert present >= 8  # decision, candidate, market, portfolio, policy, events, conflicts, response, execution, outcome

    def test_partial_coverage(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            execution_snapshot=_execution(),
        )
        cov = snapshot_coverage(r)
        assert cov["candidate_snapshot"] is True
        assert cov["execution_snapshot"] is True
        assert cov["market_snapshot"] is False
        assert cov["outcome_snapshot"] is False

    def test_non_dict_returns_empty(self):
        assert snapshot_coverage("not_a_dict") == {}


# =====================================================================
#  Record summary tests (v1.1)
# =====================================================================

class TestRecordSummary:
    """Verify record_summary() inspectability."""

    def test_summary_has_expected_keys(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            trade_key="SPY|summary_test",
            source="scanner",
        )
        s = record_summary(r)
        assert s["feedback_version"] == _FEEDBACK_VERSION
        assert s["status"] == "recorded"
        assert s["trade_action"] == "taken"
        assert s["trade_key"] == "SPY|summary_test"
        assert s["symbol"] == "SPY260320P00510000"
        assert s["source"] == "scanner"
        assert isinstance(s["snapshots_present"], int)
        assert isinstance(s["snapshots_total"], int)
        assert isinstance(s["warning_count"], int)
        assert "timestamps" in s

    def test_summary_snapshot_counts(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
            market_snapshot=_market(),
        )
        s = record_summary(r)
        assert s["snapshots_present"] == 2
        assert s["snapshots_total"] == 10

    def test_summary_timestamps(self):
        r = build_feedback_record()
        s = record_summary(r)
        ts = s["timestamps"]
        assert ts["generated_at"] is not None
        assert ts["execution_updated_at"] is None
        assert ts["outcome_updated_at"] is None
        assert ts["closed_at"] is None

    def test_summary_after_lifecycle(self):
        r = build_feedback_record(
            trade_action="taken",
            candidate_snapshot=_candidate(),
        )
        r2 = update_feedback_execution(r, _execution())
        r3 = update_feedback_outcome(r2, _outcome())
        r4 = close_feedback_record(r3)
        s = record_summary(r4)
        ts = s["timestamps"]
        assert ts["execution_updated_at"] is not None
        assert ts["outcome_updated_at"] is not None
        assert ts["closed_at"] is not None

    def test_summary_non_dict(self):
        s = record_summary("not_a_dict")
        assert s["error"] == "not a dict"


# =====================================================================
#  Backward compatibility tests (v1.1)
# =====================================================================

class TestBackwardCompatV1:
    """Verify v1.0 records still validate and can be deserialized."""

    def test_v1_0_record_validates(self):
        """A record with version 1.0 should still pass validation."""
        r = build_feedback_record(candidate_snapshot=_candidate())
        r["feedback_version"] = "1.0"
        ok, errs = validate_feedback_record(r)
        assert ok is True, errs

    def test_v1_0_record_deserializes(self):
        old = {
            "feedback_version": "1.0",
            "feedback_id": "fb-oldrecord12345",
            "recorded_at": "2026-01-01T00:00:00Z",
            "status": "recorded",
            "trade_action": "taken",
            "candidate_snapshot": {"symbol": "SPY"},
        }
        r = record_from_serializable(old)
        assert r["feedback_version"] == "1.0"
        assert r["trade_key"] is None  # Added by migration

    def test_invalid_version_fails_validation(self):
        r = build_feedback_record()
        r["feedback_version"] = "99.0"
        ok, errs = validate_feedback_record(r)
        assert ok is False
        assert any("unexpected" in e for e in errs)

    def test_compatible_versions_constant(self):
        assert _COMPATIBLE_VERSIONS == frozenset({"1.0", "1.1"})


# =====================================================================
#  Not-taken proof (explicit requirement)
# =====================================================================

class TestNotTakenProof:
    """Prove that a skipped/not-taken trade is valid and reviewable."""

    def test_skipped_trade_valid_record(self):
        r = build_feedback_record(
            trade_action="skipped",
            candidate_snapshot=_candidate(),
            decision_response=_decision_response(),
            market_snapshot=_market(),
            review_notes=["Too close to earnings window."],
        )
        # No execution
        assert r["execution_snapshot"] is None
        # No outcome
        assert r["outcome_snapshot"] is None
        # No fake data
        assert r["trade_action"] == "skipped"
        # Still valid
        ok, errs = validate_feedback_record(r)
        assert ok is True, errs
        # Status is recorded, not closed
        assert r["status"] == "recorded"
        # Context is preserved
        assert r["candidate_snapshot"]["underlying"] == "SPY"
        assert r["response_snapshot"]["decision"] == "approve"
        assert "Too close to earnings window." in r["review_notes"]
        # No taken-without-execution warning
        assert "taken_without_execution_data" not in r["warning_flags"]

    def test_skipped_minimal_still_valid(self):
        """Even minimal skipped record is valid."""
        r = build_feedback_record(
            trade_action="skipped",
            candidate_snapshot={"symbol": "SPY", "underlying": "SPY"},
        )
        ok, errs = validate_feedback_record(r)
        assert ok is True, errs
        assert r["status"] == "recorded"
        assert r["execution_snapshot"] is None
        assert r["outcome_snapshot"] is None
