"""Tests for Model-vs-Engine Disagreement Tracking v1.1.

Coverage targets:
─── Contract-level tests
    - top-level report shape / required keys
    - empty / sparse / populated status
    - version string
    - override_patterns section present
─── Single-record disagreement detection (build_disagreement_record)
    - model_vs_policy: model approve + policy block → detected
    - model_vs_policy: model reject + policy allow → detected
    - aligned: model approve + policy allow → no disagreement
    - size_guidance: model normal + policy none → detected
    - size_guidance: same size → no disagreement
    - direction: approve + risk_off market → detected
    - model_vs_market_composite: approve + fragile support → detected
    - model_vs_market_composite: approve + unstable stability → detected
    - caution_level: high conviction + high conflict → detected
    - risk_acceptance: approve + elevated event risk → detected
    - confidence_uncertainty: high conviction + low confidence → detected
    - model_vs_portfolio_fit: approve + poor portfolio fit → detected (v1.1)
    - fully aligned: no disagreement records
─── Outcome classification (v1.1)
    - pnl > 0 → win
    - pnl == 0 → breakeven
    - pnl < 0 → loss
    - no pnl → None
─── Feedback-record batch (build_tracking_report)
    - disagreement_summary by category
    - disagreement_rates computation
    - disagreement_by_regime grouping
    - disagreement_by_strategy grouping
    - disagreement_by_policy_state grouping
    - outcome stats (win/loss/breakeven/unknown)
    - breakeven_count and decided_count in outcome stats
    - confidence_state in outcome stats
─── Override patterns (v1.1)
    - repeated overrides produce pattern entry
    - single override does not produce pattern
    - outcome stats attached to patterns
─── Weighting diagnostics
    - low-sample → preliminary warning only
    - model_vs_policy override pattern with outcomes
    - no disagreement → general diagnostic
    - language is observational not prescriptive
─── Sparse-data tests
    - empty records list
    - None input
    - non-dict entries filtered
    - no outcome data
    - partial snapshots
─── Warning flags
    - no_data_available
    - sparse_data
    - no_outcome_data
    - persistent_policy_override
─── Validation tests
    - valid report passes
    - missing key detected
    - wrong version detected (incompatible)
    - compatible versions accepted
    - invalid status detected
─── Report summary (v1.1)
    - compact digest output
    - module_role field
─── Aligned-case tests
    - fully aligned record → no disagreements
    - aligned batch → low disagreement rate
─── Integration tests
    - realistic multi-record scenario
"""

import pytest

from app.services.disagreement_tracking import (
    VALID_CATEGORIES,
    VALID_OUTCOME_CLASSIFICATIONS,
    VALID_SEVERITIES,
    _COMPATIBLE_VERSIONS,
    _REQUIRED_REPORT_KEYS,
    _TRACKING_VERSION,
    build_disagreement_record,
    build_tracking_report,
    report_summary,
    validate_tracking_report,
)


# ── Fixtures ──────────────────────────────────────────────────────────

def _response(**overrides):
    """Build a decision-response dict."""
    base = {
        "decision": "approve",
        "conviction": "high",
        "market_alignment": "aligned",
        "portfolio_fit": "good",
        "policy_alignment": "clear",
        "event_risk": "low",
        "size_guidance": "normal",
        "summary": "Test trade looks good",
        "warning_flags": [],
    }
    base.update(overrides)
    return base


def _policy(**overrides):
    """Build a policy-output dict."""
    base = {
        "policy_decision": "allow",
        "severity": "none",
        "size_guidance": "normal",
        "failed_check_names": [],
        "blocking_checks": [],
    }
    base.update(overrides)
    return base


def _composite(**overrides):
    """Build a market-composite dict."""
    base = {
        "market_state": "neutral",
        "support_state": "supportive",
        "stability_state": "orderly",
        "overall_bias": "neutral",
        "confidence": 0.75,
    }
    base.update(overrides)
    return base


def _conflict(**overrides):
    """Build a conflict-report dict."""
    base = {
        "has_conflicts": False,
        "conflict_count": 0,
        "max_severity": "none",
        "conflict_severity": "none",
    }
    base.update(overrides)
    return base


def _confidence(**overrides):
    """Build a confidence-assessment dict."""
    base = {
        "confidence_label": "high",
        "uncertainty_level": "low",
        "base_score": 0.85,
    }
    base.update(overrides)
    return base


def _feedback_record(
    *,
    decision="approve",
    conviction="high",
    policy_decision="allow",
    severity="none",
    market_alignment="aligned",
    overall_bias="neutral",
    regime_label="neutral",
    strategy="iron_condor",
    spread_type="put_credit_spread",
    event_risk="low",
    event_risk_state="low",
    max_severity="none",
    confidence=0.75,
    realized_pnl=None,
    status="closed",
    portfolio_fit="good",
):
    """Build a minimal feedback record for batch testing."""
    rec = {
        "feedback_version": "1.0",
        "feedback_id": "test-id",
        "recorded_at": "2025-01-01T00:00:00Z",
        "status": status,
        "trade_action": "taken",
        "response_snapshot": {
            "decision": decision,
            "conviction": conviction,
            "market_alignment": market_alignment,
            "portfolio_fit": portfolio_fit,
            "policy_alignment": "clear",
            "event_risk": event_risk,
            "size_guidance": "normal",
        },
        "policy_snapshot": {
            "policy_decision": policy_decision,
            "severity": severity,
            "failed_check_names": [],
        },
        "market_snapshot": {
            "overall_bias": overall_bias,
            "regime_label": regime_label,
            "confidence": confidence,
        },
        "candidate_snapshot": {
            "strategy": strategy,
            "spread_type": spread_type,
        },
        "event_snapshot": {
            "event_risk_state": event_risk_state,
        },
        "conflict_snapshot": {
            "max_severity": max_severity,
        },
        "outcome_snapshot": {},
    }
    if realized_pnl is not None:
        rec["outcome_snapshot"]["realized_pnl"] = realized_pnl
    return rec


# =====================================================================
#  Contract-level tests
# =====================================================================

class TestReportContract:

    def test_required_keys_present(self):
        report = build_tracking_report([])
        for key in _REQUIRED_REPORT_KEYS:
            assert key in report, f"missing: {key}"

    def test_version_string(self):
        report = build_tracking_report([])
        assert report["tracking_version"] == _TRACKING_VERSION
        assert report["tracking_version"] == "1.1"

    def test_generated_at_present(self):
        report = build_tracking_report([])
        assert isinstance(report["generated_at"], str)

    def test_empty_status_insufficient(self):
        report = build_tracking_report([])
        assert report["status"] == "insufficient"

    def test_sparse_status(self):
        records = [_feedback_record(realized_pnl=50.0)] * 3
        report = build_tracking_report(records)
        assert report["status"] == "sparse"

    def test_sufficient_status(self):
        records = [_feedback_record(realized_pnl=50.0)] * 6
        report = build_tracking_report(records)
        assert report["status"] == "sufficient"

    def test_lists_are_lists(self):
        report = build_tracking_report([])
        for k in ("disagreement_records", "disagreement_by_regime",
                   "disagreement_by_strategy", "disagreement_by_policy_state",
                   "override_patterns", "weighting_diagnostics",
                   "warning_flags"):
            assert isinstance(report[k], list), f"{k} must be list"

    def test_dicts_are_dicts(self):
        report = build_tracking_report([])
        for k in ("disagreement_summary", "disagreement_rates",
                   "evidence", "metadata"):
            assert isinstance(report[k], dict), f"{k} must be dict"


# =====================================================================
#  Single-record disagreement detection
# =====================================================================

class TestBuildDisagreementRecord:

    def test_model_approve_policy_block(self):
        """Model approve + policy block → model_vs_policy disagreement."""
        dis = build_disagreement_record(
            response=_response(decision="approve"),
            policy=_policy(policy_decision="block"),
        )
        assert len(dis) >= 1
        cats = [d["category"] for d in dis]
        assert "model_vs_policy" in cats

    def test_model_approve_policy_restrict(self):
        dis = build_disagreement_record(
            response=_response(decision="approve"),
            policy=_policy(policy_decision="restrict"),
        )
        cats = [d["category"] for d in dis]
        assert "model_vs_policy" in cats

    def test_model_reject_policy_allow(self):
        """Model rejects but policy allows → disagreement (lower severity)."""
        dis = build_disagreement_record(
            response=_response(decision="reject"),
            policy=_policy(policy_decision="allow"),
        )
        cats = [d["category"] for d in dis]
        assert "model_vs_policy" in cats

    def test_aligned_no_disagreement(self):
        """Model approve + policy allow → no model_vs_policy."""
        dis = build_disagreement_record(
            response=_response(decision="approve"),
            policy=_policy(policy_decision="allow"),
            composite=_composite(),
        )
        cats = [d["category"] for d in dis]
        assert "model_vs_policy" not in cats

    def test_size_guidance_disagreement(self):
        """Model normal + policy none → size_guidance disagreement."""
        dis = build_disagreement_record(
            response=_response(size_guidance="normal"),
            policy=_policy(size_guidance="none"),
        )
        cats = [d["category"] for d in dis]
        assert "size_guidance" in cats

    def test_size_guidance_same_no_disagreement(self):
        """Same size guidance → no disagreement."""
        dis = build_disagreement_record(
            response=_response(size_guidance="reduced"),
            policy=_policy(size_guidance="reduced"),
            composite=_composite(),
        )
        cats = [d["category"] for d in dis]
        assert "size_guidance" not in cats

    def test_direction_approve_risk_off(self):
        """Approve + risk_off market → direction disagreement."""
        dis = build_disagreement_record(
            response=_response(decision="approve"),
            policy=_policy(),
            composite=_composite(market_state="risk_off"),
        )
        cats = [d["category"] for d in dis]
        assert "direction" in cats

    def test_composite_fragile_support(self):
        """Approve + fragile support → market_composite disagreement."""
        dis = build_disagreement_record(
            response=_response(decision="approve"),
            policy=_policy(),
            composite=_composite(support_state="fragile"),
        )
        cats = [d["category"] for d in dis]
        assert "model_vs_market_composite" in cats

    def test_composite_unstable(self):
        """Approve + unstable stability → market_composite disagreement."""
        dis = build_disagreement_record(
            response=_response(decision="approve"),
            policy=_policy(),
            composite=_composite(stability_state="unstable"),
        )
        cats = [d["category"] for d in dis]
        assert "model_vs_market_composite" in cats

    def test_caution_level_high_conviction_high_conflict(self):
        """High conviction + high conflict severity → caution_level."""
        dis = build_disagreement_record(
            response=_response(conviction="high"),
            policy=_policy(),
            conflict_report=_conflict(max_severity="high"),
        )
        cats = [d["category"] for d in dis]
        assert "caution_level" in cats

    def test_caution_level_low_conviction_no_disagreement(self):
        """Low conviction + high conflict → no caution_level (model is cautious)."""
        dis = build_disagreement_record(
            response=_response(conviction="low"),
            policy=_policy(),
            conflict_report=_conflict(max_severity="high"),
        )
        cats = [d["category"] for d in dis]
        assert "caution_level" not in cats

    def test_risk_acceptance_approve_elevated_event(self):
        """Approve + elevated event_risk → risk_acceptance."""
        dis = build_disagreement_record(
            response=_response(decision="approve", event_risk="elevated"),
            policy=_policy(),
        )
        cats = [d["category"] for d in dis]
        assert "risk_acceptance" in cats

    def test_risk_acceptance_approve_high_event(self):
        dis = build_disagreement_record(
            response=_response(decision="approve", event_risk="high"),
            policy=_policy(),
        )
        cats = [d["category"] for d in dis]
        assert "risk_acceptance" in cats

    def test_confidence_disagreement(self):
        """High conviction + low confidence → confidence_uncertainty."""
        dis = build_disagreement_record(
            response=_response(conviction="high"),
            policy=_policy(),
            confidence=_confidence(confidence_label="low"),
        )
        cats = [d["category"] for d in dis]
        assert "confidence_uncertainty" in cats

    def test_confidence_aligned_no_disagreement(self):
        """High conviction + high confidence → no disagreement."""
        dis = build_disagreement_record(
            response=_response(conviction="high"),
            policy=_policy(),
            confidence=_confidence(confidence_label="high"),
        )
        cats = [d["category"] for d in dis]
        assert "confidence_uncertainty" not in cats

    def test_portfolio_fit_approve_poor(self):
        """Approve + poor portfolio_fit → model_vs_portfolio_fit (v1.1)."""
        dis = build_disagreement_record(
            response=_response(decision="approve", portfolio_fit="poor"),
            policy=_policy(),
        )
        cats = [d["category"] for d in dis]
        assert "model_vs_portfolio_fit" in cats

    def test_portfolio_fit_approve_good_no_disagreement(self):
        """Approve + good portfolio_fit → no model_vs_portfolio_fit."""
        dis = build_disagreement_record(
            response=_response(decision="approve", portfolio_fit="good"),
            policy=_policy(),
        )
        cats = [d["category"] for d in dis]
        assert "model_vs_portfolio_fit" not in cats

    def test_portfolio_fit_reject_poor_no_disagreement(self):
        """Reject + poor portfolio_fit → no model_vs_portfolio_fit (model is cautious)."""
        dis = build_disagreement_record(
            response=_response(decision="reject", portfolio_fit="poor"),
            policy=_policy(),
        )
        cats = [d["category"] for d in dis]
        assert "model_vs_portfolio_fit" not in cats

    def test_portfolio_fit_missing_no_crash(self):
        """Missing portfolio_fit → no model_vs_portfolio_fit, no crash."""
        resp = _response(decision="approve")
        del resp["portfolio_fit"]
        dis = build_disagreement_record(
            response=resp,
            policy=_policy(),
        )
        cats = [d["category"] for d in dis]
        assert "model_vs_portfolio_fit" not in cats

    def test_fully_aligned_no_disagreements(self):
        """Fully aligned case → empty list."""
        dis = build_disagreement_record(
            response=_response(
                decision="approve", conviction="moderate",
                event_risk="low", size_guidance="normal",
            ),
            policy=_policy(
                policy_decision="allow", size_guidance="normal",
                severity="none",
            ),
            composite=_composite(
                market_state="neutral", support_state="supportive",
                stability_state="orderly",
            ),
            conflict_report=_conflict(max_severity="none"),
            confidence=_confidence(confidence_label="high"),
        )
        assert dis == []

    def test_record_has_required_fields(self):
        """Each disagreement record has the expected shape."""
        dis = build_disagreement_record(
            response=_response(decision="approve"),
            policy=_policy(policy_decision="block"),
        )
        assert len(dis) >= 1
        rec = dis[0]
        for key in ("record_id", "category", "severity",
                     "model_position", "outcome", "notes"):
            assert key in rec, f"missing: {key}"

    def test_severity_in_valid_set(self):
        dis = build_disagreement_record(
            response=_response(decision="approve"),
            policy=_policy(policy_decision="block"),
        )
        for d in dis:
            assert d["severity"] in VALID_SEVERITIES

    def test_none_inputs_no_crash(self):
        """None for all inputs → empty list, no crash."""
        dis = build_disagreement_record()
        assert dis == []

    def test_feedback_record_mode(self):
        """Pass feedback_record instead of direct outputs."""
        fb = _feedback_record(decision="approve", policy_decision="block")
        dis = build_disagreement_record(feedback_record=fb)
        cats = [d["category"] for d in dis]
        assert "model_vs_policy" in cats

    def test_feedback_record_outcome_attached(self):
        """Outcome from feedback record is attached to disagreement."""
        fb = _feedback_record(
            decision="approve", policy_decision="block",
            realized_pnl=-100.0,
        )
        dis = build_disagreement_record(feedback_record=fb)
        assert any(d["outcome"] == "loss" for d in dis)

    def test_feedback_record_outcome_breakeven(self):
        """pnl == 0 → outcome 'breakeven' (v1.1)."""
        fb = _feedback_record(
            decision="approve", policy_decision="block",
            realized_pnl=0.0,
        )
        dis = build_disagreement_record(feedback_record=fb)
        assert any(d["outcome"] == "breakeven" for d in dis)

    def test_feedback_record_outcome_win(self):
        """pnl > 0 → outcome 'win'."""
        fb = _feedback_record(
            decision="approve", policy_decision="block",
            realized_pnl=50.0,
        )
        dis = build_disagreement_record(feedback_record=fb)
        assert any(d["outcome"] == "win" for d in dis)


# =====================================================================
#  Batch tracking report
# =====================================================================

class TestBatchTracking:

    def test_summary_by_category(self):
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             realized_pnl=50.0),
            _feedback_record(decision="approve", policy_decision="restrict",
                             realized_pnl=-30.0),
        ]
        report = build_tracking_report(records)
        ds = report["disagreement_summary"]
        assert "model_vs_policy" in ds

    def test_rates_computation(self):
        records = [
            _feedback_record(decision="approve", policy_decision="block"),
            _feedback_record(decision="approve", policy_decision="allow"),
        ]
        report = build_tracking_report(records)
        rates = report["disagreement_rates"]
        assert rates["total_records"] == 2
        assert rates["records_with_disagreement"] >= 1

    def test_by_regime_grouping(self):
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             regime_label="bearish"),
            _feedback_record(decision="approve", policy_decision="block",
                             regime_label="neutral"),
        ]
        report = build_tracking_report(records)
        regimes = {g.get("regime_label") for g in report["disagreement_by_regime"]}
        assert "bearish" in regimes or "neutral" in regimes

    def test_by_strategy_grouping(self):
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             strategy="iron_condor"),
            _feedback_record(decision="approve", policy_decision="block",
                             strategy="butterfly"),
        ]
        report = build_tracking_report(records)
        strats = {g.get("strategy") for g in report["disagreement_by_strategy"]}
        assert "iron_condor" in strats or "butterfly" in strats

    def test_by_policy_state_grouping(self):
        records = [
            _feedback_record(decision="approve", policy_decision="block"),
            _feedback_record(decision="approve", policy_decision="restrict"),
        ]
        report = build_tracking_report(records)
        assert len(report["disagreement_by_policy_state"]) >= 1

    def test_outcome_stats_in_summary(self):
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             realized_pnl=50.0),
            _feedback_record(decision="approve", policy_decision="block",
                             realized_pnl=-50.0),
        ]
        report = build_tracking_report(records)
        mvp = report["disagreement_summary"].get("model_vs_policy", {})
        os = mvp.get("outcome_stats", {})
        assert os.get("win_count", 0) >= 1
        assert os.get("loss_count", 0) >= 1

    def test_outcome_stats_breakeven_count(self):
        """Breakeven pnl tracked separately in v1.1."""
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             realized_pnl=0.0),
            _feedback_record(decision="approve", policy_decision="block",
                             realized_pnl=50.0),
        ]
        report = build_tracking_report(records)
        mvp = report["disagreement_summary"].get("model_vs_policy", {})
        os = mvp.get("outcome_stats", {})
        assert os.get("breakeven_count", -1) >= 1
        assert "decided_count" in os
        assert "confidence_state" in os

    def test_sample_size_with_decided(self):
        """Sample size includes with_decided field (v1.1)."""
        records = [
            _feedback_record(realized_pnl=50.0),
            _feedback_record(realized_pnl=0.0),
            _feedback_record(realized_pnl=-30.0),
        ]
        report = build_tracking_report(records)
        ss = report["sample_size"]
        assert "with_decided" in ss
        # with_decided counts pnl != 0 (50.0 and -30.0), not breakeven (0.0)
        assert ss["with_decided"] == 2
        assert ss["with_outcome"] == 3


# =====================================================================
#  Weighting diagnostics
# =====================================================================

class TestWeightingDiagnostics:

    def test_low_sample_preliminary(self):
        records = [_feedback_record(decision="approve", policy_decision="block")]
        report = build_tracking_report(records)
        diags = report["weighting_diagnostics"]
        assert len(diags) >= 1
        assert any("preliminary" in d.get("observation", "").lower()
                    or "sample_size" in d.get("category", "")
                    for d in diags)

    def test_sufficient_override_pattern(self):
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             realized_pnl=-50.0)
            for _ in range(6)
        ]
        report = build_tracking_report(records)
        diags = report["weighting_diagnostics"]
        mvp_diags = [d for d in diags if d.get("category") == "model_vs_policy"]
        assert len(mvp_diags) >= 1

    def test_no_disagreement_general_diagnostic(self):
        records = [_feedback_record() for _ in range(6)]
        report = build_tracking_report(records)
        diags = report["weighting_diagnostics"]
        assert len(diags) >= 1
        assert any(d.get("category") == "general" for d in diags)


# =====================================================================
#  Sparse-data / edge-case tests
# =====================================================================

class TestSparseData:

    def test_empty_list(self):
        report = build_tracking_report([])
        assert report["status"] == "insufficient"
        assert report["sample_size"]["total_records"] == 0

    def test_none_input(self):
        report = build_tracking_report(None)
        assert report["status"] == "insufficient"

    def test_non_dict_entries_filtered(self):
        records = [_feedback_record(), "bad", 123, None]
        report = build_tracking_report(records)
        assert report["sample_size"]["total_records"] == 1

    def test_no_outcome_data(self):
        records = [_feedback_record(realized_pnl=None) for _ in range(3)]
        report = build_tracking_report(records)
        assert report["sample_size"]["with_outcome"] == 0

    def test_partial_snapshots(self):
        """Record with missing snapshots → no crash."""
        rec = {
            "feedback_version": "1.0",
            "status": "closed",
            "trade_action": "taken",
            "response_snapshot": {"decision": "approve"},
            # missing policy, market, event, conflict, candidate, outcome
        }
        report = build_tracking_report([rec])
        ok, _ = validate_tracking_report(report)
        assert ok

    def test_empty_snapshots(self):
        rec = {
            "feedback_version": "1.0",
            "status": "closed",
            "trade_action": "taken",
            "response_snapshot": {},
            "policy_snapshot": {},
            "market_snapshot": {},
        }
        report = build_tracking_report([rec])
        ok, _ = validate_tracking_report(report)
        assert ok


# =====================================================================
#  Warning flags
# =====================================================================

class TestWarningFlags:

    def test_no_data_flag(self):
        report = build_tracking_report([])
        assert "no_data_available" in report["warning_flags"]

    def test_sparse_data_flag(self):
        records = [_feedback_record() for _ in range(3)]
        report = build_tracking_report(records)
        assert "sparse_data" in report["warning_flags"]

    def test_no_outcome_flag(self):
        records = [_feedback_record(realized_pnl=None) for _ in range(6)]
        report = build_tracking_report(records)
        assert "no_outcome_data" in report["warning_flags"]

    def test_persistent_policy_override_flag(self):
        records = [
            _feedback_record(decision="approve", policy_decision="block")
            for _ in range(6)
        ]
        report = build_tracking_report(records)
        matching = [f for f in report["warning_flags"]
                    if f.startswith("persistent_policy_override")]
        assert len(matching) >= 1


# =====================================================================
#  Validation
# =====================================================================

class TestValidation:

    def test_valid_report_passes(self):
        report = build_tracking_report([_feedback_record()])
        ok, errors = validate_tracking_report(report)
        assert ok, f"Errors: {errors}"

    def test_empty_report_passes(self):
        report = build_tracking_report([])
        ok, errors = validate_tracking_report(report)
        assert ok, f"Errors: {errors}"

    def test_non_dict_fails(self):
        ok, errors = validate_tracking_report("not_a_dict")
        assert not ok

    def test_missing_key(self):
        report = build_tracking_report([])
        del report["status"]
        ok, errors = validate_tracking_report(report)
        assert not ok
        assert any("status" in e for e in errors)

    def test_wrong_version(self):
        report = build_tracking_report([])
        report["tracking_version"] = "999.0"
        ok, errors = validate_tracking_report(report)
        assert not ok

    def test_invalid_status(self):
        report = build_tracking_report([])
        report["status"] = "bad"
        ok, errors = validate_tracking_report(report)
        assert not ok

    def test_non_list_section(self):
        report = build_tracking_report([])
        report["disagreement_records"] = "wrong"
        ok, errors = validate_tracking_report(report)
        assert not ok

    def test_round_trip_all_cases(self):
        """Every build output must pass validate."""
        for records in [
            [],
            [_feedback_record()],
            [_feedback_record(decision="approve", policy_decision="block",
                              realized_pnl=50.0)],
            [_feedback_record()] * 6,
        ]:
            report = build_tracking_report(records)
            ok, errors = validate_tracking_report(report)
            assert ok, f"Failed with {len(records)} records: {errors}"

    def test_compatible_version_1_0_accepted(self):
        """A report with version '1.0' still passes validation."""
        report = build_tracking_report([])
        report["tracking_version"] = "1.0"
        ok, errors = validate_tracking_report(report)
        assert ok, f"Errors: {errors}"

    def test_compatible_version_1_1_accepted(self):
        """A report with version '1.1' passes validation."""
        report = build_tracking_report([])
        report["tracking_version"] = "1.1"
        ok, errors = validate_tracking_report(report)
        assert ok, f"Errors: {errors}"

    def test_incompatible_version_rejected(self):
        """A version not in _COMPATIBLE_VERSIONS is rejected."""
        report = build_tracking_report([])
        report["tracking_version"] = "99.9"
        ok, errors = validate_tracking_report(report)
        assert not ok
        assert any("99.9" in e for e in errors)

    def test_compatible_versions_constant(self):
        """_COMPATIBLE_VERSIONS includes both 1.0 and 1.1."""
        assert "1.0" in _COMPATIBLE_VERSIONS
        assert "1.1" in _COMPATIBLE_VERSIONS


# =====================================================================
#  Override patterns (v1.1)
# =====================================================================

class TestOverridePatterns:

    def test_repeated_overrides_produce_pattern(self):
        """>=2 model_vs_policy overrides in same regime×strategy → pattern entry."""
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             regime_label="bearish", strategy="iron_condor",
                             realized_pnl=-50.0),
            _feedback_record(decision="approve", policy_decision="block",
                             regime_label="bearish", strategy="iron_condor",
                             realized_pnl=30.0),
        ]
        report = build_tracking_report(records)
        patterns = report["override_patterns"]
        assert len(patterns) >= 1
        p = patterns[0]
        assert p["regime_label"] == "bearish"
        assert p["strategy"] == "iron_condor"
        assert p["override_count"] >= 2
        assert "outcome_stats" in p
        assert "confidence_state" in p

    def test_single_override_no_pattern(self):
        """Single override does not produce a pattern."""
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             regime_label="bearish", strategy="iron_condor"),
            _feedback_record(decision="approve", policy_decision="allow",
                             regime_label="bearish", strategy="iron_condor"),
        ]
        report = build_tracking_report(records)
        patterns = report["override_patterns"]
        # At most 1 override in this regime×strategy, so no pattern
        assert len(patterns) == 0

    def test_pattern_outcome_stats_present(self):
        """Pattern includes outcome stats with breakeven/decided fields."""
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             regime_label="neutral", strategy="butterfly",
                             realized_pnl=0.0),
            _feedback_record(decision="approve", policy_decision="block",
                             regime_label="neutral", strategy="butterfly",
                             realized_pnl=50.0),
            _feedback_record(decision="approve", policy_decision="restrict",
                             regime_label="neutral", strategy="butterfly",
                             realized_pnl=-20.0),
        ]
        report = build_tracking_report(records)
        patterns = report["override_patterns"]
        assert len(patterns) >= 1
        os = patterns[0]["outcome_stats"]
        assert "breakeven_count" in os
        assert "decided_count" in os
        assert "confidence_state" in os

    def test_pattern_has_low_sample_warning(self):
        """Pattern with few overrides should flag low_sample_warning."""
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             regime_label="bearish", strategy="iron_condor"),
            _feedback_record(decision="approve", policy_decision="block",
                             regime_label="bearish", strategy="iron_condor"),
        ]
        report = build_tracking_report(records)
        patterns = report["override_patterns"]
        assert len(patterns) >= 1
        assert patterns[0]["low_sample_warning"] is True

    def test_empty_records_no_patterns(self):
        """Empty records → no override patterns."""
        report = build_tracking_report([])
        assert report["override_patterns"] == []


# =====================================================================
#  Report summary (v1.1)
# =====================================================================

class TestReportSummary:

    def test_summary_keys(self):
        """report_summary produces expected keys."""
        report = build_tracking_report([_feedback_record()])
        s = report_summary(report)
        expected = {
            "tracking_version", "status", "total_records",
            "records_with_disagreement", "disagreement_rate",
            "categories_detected", "override_pattern_count",
            "warning_count", "module_role",
        }
        assert expected.issubset(s.keys())

    def test_module_role_is_diagnostic(self):
        """module_role must always be 'diagnostic'."""
        report = build_tracking_report([])
        s = report_summary(report)
        assert s["module_role"] == "diagnostic"

    def test_summary_reflects_data(self):
        """Summary values reflect actual report data."""
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             realized_pnl=-50.0),
            _feedback_record(realized_pnl=30.0),
        ]
        report = build_tracking_report(records)
        s = report_summary(report)
        assert s["total_records"] == 2
        assert s["records_with_disagreement"] >= 1
        assert s["tracking_version"] == "1.1"
        assert isinstance(s["categories_detected"], list)

    def test_summary_empty_report(self):
        """Summary on empty report → default values."""
        report = build_tracking_report([])
        s = report_summary(report)
        assert s["total_records"] == 0
        assert s["override_pattern_count"] == 0
        assert s["status"] == "insufficient"


# =====================================================================
#  Outcome classification constants (v1.1)
# =====================================================================

class TestOutcomeConstants:

    def test_valid_outcome_classifications(self):
        """VALID_OUTCOME_CLASSIFICATIONS contains expected values."""
        assert "win" in VALID_OUTCOME_CLASSIFICATIONS
        assert "loss" in VALID_OUTCOME_CLASSIFICATIONS
        assert "breakeven" in VALID_OUTCOME_CLASSIFICATIONS
        assert "unknown" in VALID_OUTCOME_CLASSIFICATIONS

    def test_valid_categories_includes_portfolio_fit(self):
        """VALID_CATEGORIES includes model_vs_portfolio_fit (v1.1)."""
        assert "model_vs_portfolio_fit" in VALID_CATEGORIES


# =====================================================================
#  Observational language (v1.1 — diagnostic-only guardrail)
# =====================================================================

class TestObservationalLanguage:

    def test_weighting_diagnostics_no_prescriptive_language(self):
        """Weighting diagnostics should use observational language only."""
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             realized_pnl=-50.0)
            for _ in range(6)
        ]
        report = build_tracking_report(records)
        diags = report["weighting_diagnostics"]
        prescriptive_phrases = [
            "consider higher", "consider lower", "consider loosening",
            "strongly recommend", "you should", "must adjust",
        ]
        for d in diags:
            obs = d.get("observation", "").lower()
            rec = d.get("recommendation", "").lower()
            full = obs + " " + rec
            for phrase in prescriptive_phrases:
                assert phrase not in full, (
                    f"Prescriptive language found: '{phrase}' in diagnostic"
                )


# =====================================================================
#  Aligned-case tests
# =====================================================================

class TestAlignedCases:

    def test_fully_aligned_single(self):
        """Aligned record → no disagreements."""
        dis = build_disagreement_record(
            response=_response(
                decision="approve", conviction="moderate",
                event_risk="low", size_guidance="normal",
            ),
            policy=_policy(
                policy_decision="allow", size_guidance="normal",
                severity="none",
            ),
            composite=_composite(
                market_state="neutral", support_state="supportive",
                stability_state="orderly",
            ),
            conflict_report=_conflict(max_severity="none"),
            confidence=_confidence(confidence_label="high"),
        )
        assert dis == []

    def test_aligned_batch_low_disagreement_rate(self):
        """Batch of aligned records → low/zero disagreement rate."""
        records = [_feedback_record() for _ in range(10)]
        report = build_tracking_report(records)
        rate = report["disagreement_rates"]["disagreement_rate"]
        assert rate is not None
        assert rate <= 0.1  # 0% expected for aligned records

    def test_aligned_no_false_positives(self):
        """Aligned records should not produce any disagreement records."""
        records = [_feedback_record() for _ in range(6)]
        report = build_tracking_report(records)
        assert report["sample_size"]["total_disagreements"] == 0
        assert report["disagreement_records"] == []

    def test_aligned_general_diagnostic(self):
        """Aligned records → general 'no notable patterns' diagnostic."""
        records = [_feedback_record() for _ in range(6)]
        report = build_tracking_report(records)
        diags = report["weighting_diagnostics"]
        assert any(d.get("category") == "general" for d in diags)


# =====================================================================
#  Integration
# =====================================================================

class TestIntegration:

    def test_populated_diverse_report(self):
        """Mix of disagreements and aligned records with outcomes."""
        records = [
            # Model overrides policy — loss
            _feedback_record(decision="approve", policy_decision="block",
                             realized_pnl=-100.0, regime_label="bearish",
                             strategy="iron_condor"),
            # Model overrides policy — win
            _feedback_record(decision="approve", policy_decision="restrict",
                             realized_pnl=80.0, regime_label="neutral",
                             strategy="put_credit_spread"),
            # Aligned — win
            _feedback_record(realized_pnl=60.0, regime_label="neutral",
                             strategy="iron_condor"),
            # Aligned — loss
            _feedback_record(realized_pnl=-20.0, regime_label="bearish",
                             strategy="iron_condor"),
            # Model overrides — win
            _feedback_record(decision="cautious_approve", policy_decision="block",
                             realized_pnl=40.0, regime_label="neutral"),
            # Aligned — win
            _feedback_record(realized_pnl=90.0),
        ]
        report = build_tracking_report(records)
        ok, errors = validate_tracking_report(report)
        assert ok, f"Errors: {errors}"
        assert report["status"] == "sufficient"
        assert report["sample_size"]["total_records"] == 6
        assert report["sample_size"]["records_with_disagreement"] >= 2
        assert "with_decided" in report["sample_size"]
        assert len(report["disagreement_records"]) >= 2
        assert isinstance(report["override_patterns"], list)

    def test_sparse_diverse_report(self):
        """Few records with disagreement."""
        records = [
            _feedback_record(decision="approve", policy_decision="block",
                             realized_pnl=-50.0),
            _feedback_record(realized_pnl=30.0),
        ]
        report = build_tracking_report(records)
        ok, errors = validate_tracking_report(report)
        assert ok, f"Errors: {errors}"
        assert report["status"] == "sparse"
