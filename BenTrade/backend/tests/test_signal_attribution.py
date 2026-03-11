"""Tests for Signal Attribution and Regime Calibration v1.1.

Coverage targets:
─── Contract-level tests
    - top-level report shape
    - required keys present
    - version string
    - status enum values
    - sample_size structure (incl. with_decided)
─── Outcome classification tests
    - positive pnl → win
    - zero pnl → breakeven
    - negative pnl → loss
    - missing outcome → unknown
    - missing pnl → unknown
    - non-numeric pnl → unknown
    - VALID_OUTCOME_CLASSIFICATIONS constant
─── Regime calibration tests
    - single regime group
    - multiple regime groups
    - unknown/missing regime labels → "unknown" default
    - sorting by sample_count desc
─── Signal attribution tests
    - individual signal groupings
    - multiple signal values
    - missing signals skipped
─── Strategy attribution tests
    - group by strategy + spread_type
    - unknown fallback
─── Policy attribution tests
    - group by decision + failed checks
    - no failed checks → "none"
    - multiple failed checks sorted & joined
─── Conflict attribution tests
    - group by has_conflicts + severity
    - missing conflict snapshot
─── Event attribution tests
    - group by event_risk_state
    - unknown fallback
─── Conviction attribution tests
    - group by conviction + decision
─── Alignment attribution tests (v1.1)
    - group by market_alignment + portfolio_fit
    - missing response_snapshot → unknown
─── Stats computation tests
    - win_rate calculation (excludes breakeven from denominator)
    - avg/median/total pnl
    - low_sample_warning threshold
    - all-unknown outcomes → win_rate=None
    - breakeven_count field
    - decided_count field
    - confidence_state field
─── Status derivation tests
    - 0 pnl records → insufficient
    - sparse pnl records → sparse
    - sufficient pnl records → sufficient
─── Warning flags tests
    - no_pnl_data flag
    - sparse_pnl_data flag
    - regime_distribution_skewed flag
    - all_signal_groups_low_sample flag
    - closed_records_without_pnl flag
─── Summary text tests
    - insufficient summary
    - sparse summary
    - sufficient summary
─── Edge case tests
    - empty records list
    - None records
    - non-dict records filtered
    - single record
    - all records have no outcome
    - mixed outcome/no-outcome records
─── Custom threshold tests
    - low_sample_threshold override
─── Validation tests
    - valid report passes
    - missing keys detected
    - wrong version detected
    - invalid status detected
    - compatible versions accepted
─── Report summary tests (v1.1)
    - compact digest output
    - module_role field
─── Integration tests
    - realistic multi-record scenario
"""

import copy
import pytest

from app.services.signal_attribution import (
    _CALIBRATION_VERSION,
    _COMPATIBLE_VERSIONS,
    _DEFAULT_LOW_SAMPLE_THRESHOLD,
    _REQUIRED_REPORT_KEYS,
    VALID_OUTCOME_CLASSIFICATIONS,
    build_calibration_report,
    classify_outcome,
    report_summary,
    validate_calibration_report,
)


# ── Fixtures ──────────────────────────────────────────────────────────

def _make_record(
    *,
    trade_action="taken",
    status="closed",
    realized_pnl=50.0,
    strategy="iron_condor",
    spread_type="put_credit_spread",
    regime_label="neutral",
    overall_bias="neutral",
    volatility_label="normal",
    trend_label="sideways",
    macro_label="stable",
    signal_quality="good",
    policy_decision="allow",
    failed_check_names=None,
    has_conflicts=False,
    max_severity="none",
    event_risk_state="low",
    conviction="moderate",
    decision="approve",
):
    """Build a minimal but realistic feedback record for testing."""
    rec = {
        "feedback_version": "1.0",
        "feedback_id": "test-id",
        "recorded_at": "2025-01-01T00:00:00Z",
        "status": status,
        "trade_action": trade_action,
        "candidate_snapshot": {
            "strategy": strategy,
            "spread_type": spread_type,
            "symbol": "SPY260320P00510000",
            "underlying": "SPY",
        },
        "market_snapshot": {
            "regime_label": regime_label,
            "overall_bias": overall_bias,
            "volatility_label": volatility_label,
            "trend_label": trend_label,
            "macro_label": macro_label,
            "signal_quality": signal_quality,
            "confidence": 0.8,
        },
        "policy_snapshot": {
            "policy_decision": policy_decision,
            "failed_check_names": failed_check_names or [],
        },
        "conflict_snapshot": {
            "has_conflicts": has_conflicts,
            "max_severity": max_severity,
        },
        "event_snapshot": {
            "event_risk_state": event_risk_state,
        },
        "response_snapshot": {
            "conviction": conviction,
            "decision": decision,
        },
        "outcome_snapshot": {},
        "review_notes": [],
        "warning_flags": [],
        "evidence": {},
        "metadata": {},
    }
    if realized_pnl is not None:
        rec["outcome_snapshot"]["realized_pnl"] = realized_pnl
    return rec


def _make_records_diverse():
    """Build a list of diverse feedback records for integration testing."""
    return [
        # Win in neutral regime
        _make_record(realized_pnl=80.0, regime_label="neutral", overall_bias="neutral",
                     strategy="iron_condor", conviction="high", decision="approve"),
        # Win in bullish regime
        _make_record(realized_pnl=120.0, regime_label="bullish", overall_bias="bullish",
                     strategy="put_credit_spread", volatility_label="elevated",
                     conviction="high", decision="approve"),
        # Loss in bearish regime
        _make_record(realized_pnl=-200.0, regime_label="bearish", overall_bias="bearish",
                     strategy="put_credit_spread", volatility_label="high",
                     event_risk_state="elevated", conviction="low", decision="cautious_approve"),
        # Win in neutral regime (same group as first)
        _make_record(realized_pnl=60.0, regime_label="neutral", overall_bias="neutral",
                     strategy="iron_condor", conviction="moderate", decision="approve"),
        # Loss in neutral regime
        _make_record(realized_pnl=-30.0, regime_label="neutral", overall_bias="neutral",
                     strategy="put_credit_spread", conviction="low", decision="approve"),
        # Unknown outcome (no pnl)
        _make_record(realized_pnl=None, regime_label="neutral", overall_bias="neutral",
                     strategy="iron_condor", status="recorded"),
        # Skipped trade with loss (hypothetical)
        _make_record(realized_pnl=-50.0, trade_action="skipped",
                     regime_label="bearish", overall_bias="bearish",
                     conviction="none", decision="reject"),
        # Win with conflicts
        _make_record(realized_pnl=40.0, has_conflicts=True, max_severity="warning",
                     event_risk_state="elevated"),
        # Win with policy failures
        _make_record(realized_pnl=90.0, policy_decision="warn",
                     failed_check_names=["max_positions", "sector_concentration"]),
        # Loss with policy failures
        _make_record(realized_pnl=-100.0, policy_decision="reject",
                     failed_check_names=["max_positions"]),
    ]


# =====================================================================
#  Contract-level tests
# =====================================================================

class TestReportContract:
    """Top-level report shape and required keys."""

    def test_required_keys_present(self):
        report = build_calibration_report([])
        for key in _REQUIRED_REPORT_KEYS:
            assert key in report, f"missing required key: {key}"

    def test_version_string(self):
        report = build_calibration_report([])
        assert report["calibration_version"] == _CALIBRATION_VERSION

    def test_generated_at_present(self):
        report = build_calibration_report([])
        assert isinstance(report["generated_at"], str)
        assert len(report["generated_at"]) > 0

    def test_empty_records_status_insufficient(self):
        report = build_calibration_report([])
        assert report["status"] == "insufficient"

    def test_attribution_sections_are_lists(self):
        report = build_calibration_report([])
        for section in (
            "regime_calibration", "signal_attribution", "strategy_attribution",
            "policy_attribution", "conflict_attribution", "event_attribution",
            "conviction_attribution", "alignment_attribution",
        ):
            assert isinstance(report[section], list), f"{section} must be a list"

    def test_warning_flags_is_list(self):
        report = build_calibration_report([])
        assert isinstance(report["warning_flags"], list)

    def test_metadata_is_dict(self):
        report = build_calibration_report([])
        assert isinstance(report["metadata"], dict)
        assert report["metadata"]["calibration_version"] == _CALIBRATION_VERSION

    def test_sample_size_structure(self):
        records = [_make_record()]
        report = build_calibration_report(records)
        ss = report["sample_size"]
        assert isinstance(ss, dict)
        for k in ("total_records", "closed_records", "with_outcome", "with_pnl", "with_decided"):
            assert k in ss
            assert isinstance(ss[k], int)


# =====================================================================
#  Outcome classification tests
# =====================================================================

class TestClassifyOutcome:
    """Test classify_outcome for various edge cases."""

    def test_positive_pnl_is_win(self):
        rec = _make_record(realized_pnl=100.0)
        assert classify_outcome(rec) == "win"

    def test_zero_pnl_is_breakeven(self):
        rec = _make_record(realized_pnl=0.0)
        assert classify_outcome(rec) == "breakeven"

    def test_negative_pnl_is_loss(self):
        rec = _make_record(realized_pnl=-50.0)
        assert classify_outcome(rec) == "loss"

    def test_small_positive_pnl_is_win(self):
        rec = _make_record(realized_pnl=0.01)
        assert classify_outcome(rec) == "win"

    def test_missing_outcome_snapshot_is_unknown(self):
        rec = _make_record()
        rec["outcome_snapshot"] = None
        assert classify_outcome(rec) == "unknown"

    def test_empty_outcome_snapshot_is_unknown(self):
        rec = _make_record()
        rec["outcome_snapshot"] = {}
        assert classify_outcome(rec) == "unknown"

    def test_missing_pnl_field_is_unknown(self):
        rec = _make_record(realized_pnl=None)
        assert classify_outcome(rec) == "unknown"

    def test_non_numeric_pnl_is_unknown(self):
        rec = _make_record()
        rec["outcome_snapshot"]["realized_pnl"] = "not_a_number"
        assert classify_outcome(rec) == "unknown"

    def test_integer_pnl_works(self):
        rec = _make_record()
        rec["outcome_snapshot"]["realized_pnl"] = 100
        assert classify_outcome(rec) == "win"

    def test_no_outcome_key_at_all(self):
        rec = _make_record()
        del rec["outcome_snapshot"]
        assert classify_outcome(rec) == "unknown"

    def test_valid_outcome_classifications_constant(self):
        """VALID_OUTCOME_CLASSIFICATIONS must match classify_outcome outputs."""
        assert VALID_OUTCOME_CLASSIFICATIONS == frozenset({"win", "loss", "breakeven", "unknown"})

    def test_integer_zero_is_breakeven(self):
        rec = _make_record()
        rec["outcome_snapshot"]["realized_pnl"] = 0
        assert classify_outcome(rec) == "breakeven"


# =====================================================================
#  Regime calibration tests
# =====================================================================

class TestRegimeCalibration:
    """Test regime_calibration grouping and stats."""

    def test_single_regime_group(self):
        records = [_make_record(realized_pnl=50.0)]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert len(rc) == 1
        assert rc[0]["regime_label"] == "neutral"
        assert rc[0]["overall_bias"] == "neutral"
        assert rc[0]["volatility_label"] == "normal"
        assert rc[0]["sample_count"] == 1
        assert rc[0]["win_count"] == 1
        assert rc[0]["loss_count"] == 0

    def test_multiple_regime_groups(self):
        records = [
            _make_record(realized_pnl=50.0, regime_label="bullish"),
            _make_record(realized_pnl=-20.0, regime_label="bearish"),
        ]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        labels = {r["regime_label"] for r in rc}
        assert "bullish" in labels
        assert "bearish" in labels

    def test_missing_regime_uses_unknown(self):
        rec = _make_record(realized_pnl=50.0)
        rec["market_snapshot"] = {}
        report = build_calibration_report([rec])
        rc = report["regime_calibration"]
        assert len(rc) == 1
        assert rc[0]["regime_label"] == "unknown"

    def test_sorted_by_sample_count_desc(self):
        records = [
            _make_record(realized_pnl=10.0, regime_label="neutral"),
            _make_record(realized_pnl=20.0, regime_label="neutral"),
            _make_record(realized_pnl=30.0, regime_label="neutral"),
            _make_record(realized_pnl=40.0, regime_label="bullish"),
        ]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["sample_count"] >= rc[-1]["sample_count"]

    def test_regime_win_rate_calculation(self):
        records = [
            _make_record(realized_pnl=50.0, regime_label="neutral"),
            _make_record(realized_pnl=-20.0, regime_label="neutral"),
            _make_record(realized_pnl=30.0, regime_label="neutral"),
        ]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert len(rc) == 1
        grp = rc[0]
        assert grp["win_count"] == 2
        assert grp["loss_count"] == 1
        # win_rate = 2/3 ≈ 0.6667
        assert abs(grp["win_rate"] - 0.6667) < 0.001

    def test_regime_pnl_stats(self):
        records = [
            _make_record(realized_pnl=100.0, regime_label="X"),
            _make_record(realized_pnl=-50.0, regime_label="X"),
        ]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        grp = rc[0]
        # avg_pnl = (100 + -50) / 2 = 25.0
        assert grp["avg_pnl"] == 25.0
        # total_pnl = 50.0
        assert grp["total_pnl"] == 50.0

    def test_no_market_snapshot_at_all(self):
        rec = _make_record(realized_pnl=50.0)
        rec["market_snapshot"] = None
        report = build_calibration_report([rec])
        rc = report["regime_calibration"]
        assert len(rc) == 1
        assert rc[0]["regime_label"] == "unknown"


# =====================================================================
#  Signal attribution tests
# =====================================================================

class TestSignalAttribution:
    """Test signal_attribution groupings."""

    def test_signal_groups_created(self):
        records = [_make_record(realized_pnl=50.0)]
        report = build_calibration_report(records)
        sa = report["signal_attribution"]
        # Should have groups for: overall_bias, trend_label, volatility_label,
        # macro_label, regime_label, signal_quality
        keys = {s["signal_key"] for s in sa}
        assert "overall_bias" in keys
        assert "trend_label" in keys
        assert "volatility_label" in keys
        assert "macro_label" in keys
        assert "regime_label" in keys
        assert "signal_quality" in keys

    def test_signal_values_correct(self):
        records = [_make_record(realized_pnl=50.0, overall_bias="bullish")]
        report = build_calibration_report(records)
        sa = report["signal_attribution"]
        bias_groups = [s for s in sa if s["signal_key"] == "overall_bias"]
        assert len(bias_groups) == 1
        assert bias_groups[0]["signal_value"] == "bullish"

    def test_multiple_signal_values(self):
        records = [
            _make_record(realized_pnl=50.0, overall_bias="bullish"),
            _make_record(realized_pnl=-20.0, overall_bias="bearish"),
        ]
        report = build_calibration_report(records)
        sa = report["signal_attribution"]
        bias_groups = [s for s in sa if s["signal_key"] == "overall_bias"]
        values = {b["signal_value"] for b in bias_groups}
        assert "bullish" in values
        assert "bearish" in values

    def test_missing_signal_skipped(self):
        rec = _make_record(realized_pnl=50.0)
        rec["market_snapshot"] = {"overall_bias": "neutral"}
        report = build_calibration_report([rec])
        sa = report["signal_attribution"]
        keys = {s["signal_key"] for s in sa}
        assert "overall_bias" in keys
        # Other signal fields not present → should not appear
        assert "trend_label" not in keys

    def test_sorted_by_sample_count(self):
        records = [
            _make_record(realized_pnl=50.0, overall_bias="bullish"),
            _make_record(realized_pnl=50.0, overall_bias="bullish"),
            _make_record(realized_pnl=50.0, overall_bias="bearish"),
        ]
        report = build_calibration_report(records)
        sa = report["signal_attribution"]
        counts = [s["sample_count"] for s in sa]
        assert counts == sorted(counts, reverse=True)


# =====================================================================
#  Strategy attribution tests
# =====================================================================

class TestStrategyAttribution:
    """Test strategy_attribution groupings."""

    def test_single_strategy_group(self):
        records = [_make_record(realized_pnl=50.0, strategy="iron_condor",
                                spread_type="put_credit_spread")]
        report = build_calibration_report(records)
        strat = report["strategy_attribution"]
        assert len(strat) == 1
        assert strat[0]["strategy"] == "iron_condor"
        assert strat[0]["spread_type"] == "put_credit_spread"

    def test_multiple_strategies(self):
        records = [
            _make_record(realized_pnl=50.0, strategy="iron_condor"),
            _make_record(realized_pnl=-20.0, strategy="butterfly"),
        ]
        report = build_calibration_report(records)
        strat = report["strategy_attribution"]
        strategies = {s["strategy"] for s in strat}
        assert "iron_condor" in strategies
        assert "butterfly" in strategies

    def test_unknown_strategy_fallback(self):
        rec = _make_record(realized_pnl=50.0)
        rec["candidate_snapshot"] = {}
        report = build_calibration_report([rec])
        strat = report["strategy_attribution"]
        assert strat[0]["strategy"] == "unknown"

    def test_no_candidate_snapshot(self):
        rec = _make_record(realized_pnl=50.0)
        rec["candidate_snapshot"] = None
        report = build_calibration_report([rec])
        strat = report["strategy_attribution"]
        assert strat[0]["strategy"] == "unknown"


# =====================================================================
#  Policy attribution tests
# =====================================================================

class TestPolicyAttribution:
    """Test policy_attribution groupings."""

    def test_allow_no_failures(self):
        records = [_make_record(realized_pnl=50.0, policy_decision="allow")]
        report = build_calibration_report(records)
        pol = report["policy_attribution"]
        assert len(pol) == 1
        assert pol[0]["policy_decision"] == "allow"
        assert pol[0]["failed_checks"] == []

    def test_with_failed_checks(self):
        records = [_make_record(
            realized_pnl=-30.0, policy_decision="warn",
            failed_check_names=["max_positions", "sector_concentration"],
        )]
        report = build_calibration_report(records)
        pol = report["policy_attribution"]
        assert len(pol) == 1
        assert "max_positions" in pol[0]["failed_checks"]
        assert "sector_concentration" in pol[0]["failed_checks"]

    def test_failed_checks_sorted(self):
        """Failed check names are sorted for stable grouping keys."""
        records = [
            _make_record(realized_pnl=50.0, policy_decision="warn",
                         failed_check_names=["b_check", "a_check"]),
            _make_record(realized_pnl=30.0, policy_decision="warn",
                         failed_check_names=["a_check", "b_check"]),
        ]
        report = build_calibration_report(records)
        pol = report["policy_attribution"]
        # Both should group together since sorted keys match
        warn_groups = [p for p in pol if p["policy_decision"] == "warn"]
        assert len(warn_groups) == 1
        assert warn_groups[0]["sample_count"] == 2

    def test_no_policy_snapshot(self):
        rec = _make_record(realized_pnl=50.0)
        rec["policy_snapshot"] = None
        report = build_calibration_report([rec])
        pol = report["policy_attribution"]
        assert pol[0]["policy_decision"] == "unknown"


# =====================================================================
#  Conflict attribution tests
# =====================================================================

class TestConflictAttribution:
    """Test conflict_attribution groupings."""

    def test_no_conflicts(self):
        records = [_make_record(realized_pnl=50.0, has_conflicts=False,
                                max_severity="none")]
        report = build_calibration_report(records)
        conf = report["conflict_attribution"]
        assert len(conf) == 1
        assert conf[0]["has_conflicts"] == "False"
        assert conf[0]["max_severity"] == "none"

    def test_with_conflicts(self):
        records = [_make_record(realized_pnl=-50.0, has_conflicts=True,
                                max_severity="critical")]
        report = build_calibration_report(records)
        conf = report["conflict_attribution"]
        assert len(conf) == 1
        assert conf[0]["has_conflicts"] == "True"
        assert conf[0]["max_severity"] == "critical"

    def test_missing_conflict_snapshot(self):
        rec = _make_record(realized_pnl=50.0)
        rec["conflict_snapshot"] = None
        report = build_calibration_report([rec])
        conf = report["conflict_attribution"]
        assert conf[0]["has_conflicts"] == "unknown"

    def test_conflict_groups(self):
        records = [
            _make_record(realized_pnl=50.0, has_conflicts=False),
            _make_record(realized_pnl=-30.0, has_conflicts=True, max_severity="warning"),
        ]
        report = build_calibration_report(records)
        conf = report["conflict_attribution"]
        assert len(conf) == 2


# =====================================================================
#  Event attribution tests
# =====================================================================

class TestEventAttribution:
    """Test event_attribution groupings."""

    def test_single_event_state(self):
        records = [_make_record(realized_pnl=50.0, event_risk_state="low")]
        report = build_calibration_report(records)
        evt = report["event_attribution"]
        assert len(evt) == 1
        assert evt[0]["event_risk_state"] == "low"

    def test_multiple_event_states(self):
        records = [
            _make_record(realized_pnl=50.0, event_risk_state="low"),
            _make_record(realized_pnl=-30.0, event_risk_state="elevated"),
        ]
        report = build_calibration_report(records)
        evt = report["event_attribution"]
        states = {e["event_risk_state"] for e in evt}
        assert "low" in states
        assert "elevated" in states

    def test_missing_event_snapshot(self):
        rec = _make_record(realized_pnl=50.0)
        rec["event_snapshot"] = None
        report = build_calibration_report([rec])
        evt = report["event_attribution"]
        assert evt[0]["event_risk_state"] == "unknown"


# =====================================================================
#  Conviction attribution tests
# =====================================================================

class TestConvictionAttribution:
    """Test conviction_attribution groupings."""

    def test_single_conviction(self):
        records = [_make_record(realized_pnl=50.0, conviction="high",
                                decision="approve")]
        report = build_calibration_report(records)
        conv = report["conviction_attribution"]
        assert len(conv) == 1
        assert conv[0]["conviction"] == "high"
        assert conv[0]["decision"] == "approve"

    def test_multiple_convictions(self):
        records = [
            _make_record(realized_pnl=50.0, conviction="high"),
            _make_record(realized_pnl=-20.0, conviction="low"),
        ]
        report = build_calibration_report(records)
        conv = report["conviction_attribution"]
        convictions = {c["conviction"] for c in conv}
        assert "high" in convictions
        assert "low" in convictions

    def test_missing_response_snapshot(self):
        rec = _make_record(realized_pnl=50.0)
        rec["response_snapshot"] = None
        report = build_calibration_report([rec])
        conv = report["conviction_attribution"]
        assert conv[0]["conviction"] == "unknown"


# =====================================================================
#  Alignment attribution tests (v1.1)
# =====================================================================

class TestAlignmentAttribution:
    """Test alignment_attribution grouping by market_alignment × portfolio_fit."""

    def test_single_alignment_group(self):
        rec = _make_record(realized_pnl=50.0)
        rec["response_snapshot"]["market_alignment"] = "aligned"
        rec["response_snapshot"]["portfolio_fit"] = "good"
        report = build_calibration_report([rec])
        al = report["alignment_attribution"]
        assert len(al) == 1
        assert al[0]["market_alignment"] == "aligned"
        assert al[0]["portfolio_fit"] == "good"

    def test_multiple_alignment_groups(self):
        r1 = _make_record(realized_pnl=50.0)
        r1["response_snapshot"]["market_alignment"] = "aligned"
        r1["response_snapshot"]["portfolio_fit"] = "good"
        r2 = _make_record(realized_pnl=-20.0)
        r2["response_snapshot"]["market_alignment"] = "misaligned"
        r2["response_snapshot"]["portfolio_fit"] = "poor"
        report = build_calibration_report([r1, r2])
        al = report["alignment_attribution"]
        assert len(al) == 2
        alignments = {a["market_alignment"] for a in al}
        assert "aligned" in alignments
        assert "misaligned" in alignments

    def test_missing_response_uses_unknown(self):
        rec = _make_record(realized_pnl=50.0)
        rec["response_snapshot"] = None
        report = build_calibration_report([rec])
        al = report["alignment_attribution"]
        assert al[0]["market_alignment"] == "unknown"
        assert al[0]["portfolio_fit"] == "unknown"

    def test_partial_fields_default_unknown(self):
        rec = _make_record(realized_pnl=50.0)
        rec["response_snapshot"]["market_alignment"] = "aligned"
        # portfolio_fit not set → should default to unknown
        report = build_calibration_report([rec])
        al = report["alignment_attribution"]
        assert al[0]["market_alignment"] == "aligned"
        assert al[0]["portfolio_fit"] == "unknown"

    def test_alignment_stats_present(self):
        rec = _make_record(realized_pnl=50.0)
        rec["response_snapshot"]["market_alignment"] = "aligned"
        rec["response_snapshot"]["portfolio_fit"] = "good"
        report = build_calibration_report([rec])
        al = report["alignment_attribution"][0]
        assert "sample_count" in al
        assert "win_count" in al
        assert "decided_count" in al
        assert "confidence_state" in al

    def test_alignment_in_required_keys(self):
        assert "alignment_attribution" in _REQUIRED_REPORT_KEYS

    def test_alignment_sorted_by_sample_count(self):
        recs = []
        for _ in range(3):
            r = _make_record(realized_pnl=50.0)
            r["response_snapshot"]["market_alignment"] = "aligned"
            r["response_snapshot"]["portfolio_fit"] = "good"
            recs.append(r)
        r = _make_record(realized_pnl=-10.0)
        r["response_snapshot"]["market_alignment"] = "neutral"
        r["response_snapshot"]["portfolio_fit"] = "fair"
        recs.append(r)
        report = build_calibration_report(recs)
        al = report["alignment_attribution"]
        assert al[0]["sample_count"] >= al[-1]["sample_count"]


# =====================================================================
#  Stats computation tests
# =====================================================================

class TestStatsComputation:
    """Test statistical calculations in attribution groups."""

    def test_win_rate_all_wins(self):
        records = [_make_record(realized_pnl=x) for x in [50, 100, 150]]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["win_rate"] == 1.0

    def test_win_rate_all_losses(self):
        records = [_make_record(realized_pnl=x) for x in [-50, -100, -150]]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["win_rate"] == 0.0

    def test_win_rate_mixed(self):
        records = [_make_record(realized_pnl=x) for x in [50, -50]]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["win_rate"] == 0.5

    def test_avg_pnl(self):
        records = [_make_record(realized_pnl=x) for x in [100, -50, 50]]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        # avg = (100 + -50 + 50) / 3 ≈ 33.33
        assert abs(rc[0]["avg_pnl"] - 33.3333) < 0.01

    def test_median_pnl(self):
        records = [_make_record(realized_pnl=x) for x in [100, -50, 50]]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["median_pnl"] == 50.0

    def test_total_pnl(self):
        records = [_make_record(realized_pnl=x) for x in [100, -50, 50]]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["total_pnl"] == 100.0

    def test_low_sample_warning_default(self):
        # Default threshold is 5
        records = [_make_record(realized_pnl=50.0)] * 3
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["low_sample_warning"] is True

    def test_low_sample_warning_sufficient(self):
        records = [_make_record(realized_pnl=50.0)] * 6
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["low_sample_warning"] is False

    def test_all_unknown_outcomes_no_win_rate(self):
        records = [_make_record(realized_pnl=None)] * 3
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["win_rate"] is None
        assert rc[0]["avg_pnl"] is None

    def test_notes_string_present(self):
        records = [_make_record(realized_pnl=50.0)]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert isinstance(rc[0]["notes"], str)
        assert len(rc[0]["notes"]) > 0

    def test_breakeven_count_tracked(self):
        records = [
            _make_record(realized_pnl=50.0),
            _make_record(realized_pnl=0.0),
            _make_record(realized_pnl=-10.0),
        ]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        grp = rc[0]
        assert grp["breakeven_count"] == 1
        assert grp["win_count"] == 1
        assert grp["loss_count"] == 1

    def test_decided_count_excludes_breakeven(self):
        records = [
            _make_record(realized_pnl=50.0),
            _make_record(realized_pnl=0.0),
            _make_record(realized_pnl=-10.0),
        ]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        grp = rc[0]
        # decided = wins + losses = 1 + 1 = 2 (breakeven excluded)
        assert grp["decided_count"] == 2

    def test_decided_count_excludes_unknown(self):
        records = [
            _make_record(realized_pnl=50.0),
            _make_record(realized_pnl=None),
        ]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        grp = rc[0]
        assert grp["decided_count"] == 1
        assert grp["unknown_count"] == 1

    def test_win_rate_excludes_breakeven_from_denominator(self):
        """Win rate = wins / (wins + losses), breakeven not in denominator."""
        records = [
            _make_record(realized_pnl=50.0),
            _make_record(realized_pnl=0.0),
        ]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        grp = rc[0]
        # win_rate = 1 / 1 = 1.0 (breakeven excluded from denominator)
        assert grp["win_rate"] == 1.0

    def test_confidence_state_insufficient(self):
        records = [_make_record(realized_pnl=None)] * 3
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["confidence_state"] == "insufficient"

    def test_confidence_state_low(self):
        records = [_make_record(realized_pnl=50.0)] * 3
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["confidence_state"] == "low"

    def test_confidence_state_adequate(self):
        records = [_make_record(realized_pnl=50.0)] * 6
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["confidence_state"] == "adequate"

    def test_confidence_state_breakeven_only_insufficient(self):
        """All-breakeven means decided_count=0 → insufficient."""
        records = [_make_record(realized_pnl=0.0)] * 3
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert rc[0]["confidence_state"] == "insufficient"

    def test_notes_include_breakeven(self):
        records = [_make_record(realized_pnl=0.0)]
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        assert "breakeven" in rc[0]["notes"].lower()


# =====================================================================
#  Status derivation tests
# =====================================================================

class TestStatusDerivation:
    """Test report-level status derivation."""

    def test_insufficient_no_records(self):
        report = build_calibration_report([])
        assert report["status"] == "insufficient"

    def test_insufficient_no_pnl(self):
        records = [_make_record(realized_pnl=None)] * 3
        report = build_calibration_report(records)
        assert report["status"] == "insufficient"

    def test_sparse_few_pnl(self):
        records = [_make_record(realized_pnl=50.0)] * 3
        report = build_calibration_report(records)
        assert report["status"] == "sparse"

    def test_sufficient_many_pnl(self):
        records = [_make_record(realized_pnl=50.0)] * 6
        report = build_calibration_report(records)
        assert report["status"] == "sufficient"

    def test_threshold_boundary_below(self):
        records = [_make_record(realized_pnl=50.0)] * 4
        report = build_calibration_report(records)
        assert report["status"] == "sparse"

    def test_threshold_boundary_at(self):
        records = [_make_record(realized_pnl=50.0)] * 5
        report = build_calibration_report(records)
        assert report["status"] == "sufficient"


# =====================================================================
#  Warning flags tests
# =====================================================================

class TestWarningFlags:
    """Test report-level warning flags."""

    def test_no_pnl_data_flag(self):
        report = build_calibration_report([])
        assert "no_pnl_data_available" in report["warning_flags"]

    def test_sparse_pnl_data_flag(self):
        records = [_make_record(realized_pnl=50.0)] * 3
        report = build_calibration_report(records)
        assert "sparse_pnl_data" in report["warning_flags"]

    def test_no_flag_when_sufficient(self):
        records = [_make_record(realized_pnl=50.0)] * 10
        report = build_calibration_report(records)
        flags = report["warning_flags"]
        assert "no_pnl_data_available" not in flags
        assert "sparse_pnl_data" not in flags

    def test_regime_distribution_skewed(self):
        # 9 in one regime, 1 in another → >80% skew
        records = (
            [_make_record(realized_pnl=50.0, regime_label="neutral")] * 9
            + [_make_record(realized_pnl=50.0, regime_label="bullish")]
        )
        report = build_calibration_report(records)
        assert "regime_distribution_skewed" in report["warning_flags"]

    def test_no_skew_flag_balanced(self):
        records = (
            [_make_record(realized_pnl=50.0, regime_label="neutral")] * 5
            + [_make_record(realized_pnl=50.0, regime_label="bullish")] * 5
        )
        report = build_calibration_report(records)
        assert "regime_distribution_skewed" not in report["warning_flags"]

    def test_all_signal_groups_low_sample(self):
        records = [_make_record(realized_pnl=50.0)]  # 1 record → all groups low
        report = build_calibration_report(records)
        assert "all_signal_groups_low_sample" in report["warning_flags"]

    def test_closed_without_pnl_flag(self):
        records = [
            _make_record(realized_pnl=50.0, status="closed"),
            _make_record(realized_pnl=None, status="closed"),
        ]
        report = build_calibration_report(records)
        matching = [f for f in report["warning_flags"]
                    if f.startswith("closed_records_without_pnl")]
        assert len(matching) == 1
        assert "1" in matching[0]


# =====================================================================
#  Summary text tests
# =====================================================================

class TestSummary:
    """Test human-readable summary generation."""

    def test_insufficient_summary(self):
        report = build_calibration_report([])
        assert "Insufficient" in report["summary"]

    def test_sparse_summary(self):
        records = [_make_record(realized_pnl=50.0)] * 3
        report = build_calibration_report(records)
        assert "Sparse" in report["summary"]

    def test_sufficient_summary(self):
        records = [_make_record(realized_pnl=50.0)] * 10
        report = build_calibration_report(records)
        assert "Calibration report" in report["summary"]

    def test_summary_is_string(self):
        report = build_calibration_report([])
        assert isinstance(report["summary"], str)


# =====================================================================
#  Edge case tests
# =====================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_list(self):
        report = build_calibration_report([])
        assert report["sample_size"]["total_records"] == 0

    def test_none_records(self):
        report = build_calibration_report(None)
        assert report["sample_size"]["total_records"] == 0

    def test_non_dict_records_filtered(self):
        records = [_make_record(realized_pnl=50.0), "not_a_dict", 123, None]
        report = build_calibration_report(records)
        assert report["sample_size"]["total_records"] == 1

    def test_single_record(self):
        records = [_make_record(realized_pnl=50.0)]
        report = build_calibration_report(records)
        assert report["sample_size"]["total_records"] == 1
        assert report["sample_size"]["with_pnl"] == 1

    def test_all_no_outcome(self):
        records = [_make_record(realized_pnl=None)] * 5
        report = build_calibration_report(records)
        assert report["sample_size"]["with_pnl"] == 0
        assert report["status"] == "insufficient"

    def test_mixed_outcome_no_outcome(self):
        records = [
            _make_record(realized_pnl=50.0),
            _make_record(realized_pnl=None),
            _make_record(realized_pnl=-20.0),
        ]
        report = build_calibration_report(records)
        assert report["sample_size"]["with_pnl"] == 2
        # All still contributed to grouping
        assert report["sample_size"]["total_records"] == 3

    def test_record_not_mutated(self):
        rec = _make_record(realized_pnl=50.0)
        original = copy.deepcopy(rec)
        build_calibration_report([rec])
        assert rec == original

    def test_large_pnl_handled(self):
        rec = _make_record(realized_pnl=1_000_000.0)
        report = build_calibration_report([rec])
        rc = report["regime_calibration"]
        assert rc[0]["total_pnl"] == 1_000_000.0

    def test_very_small_pnl(self):
        rec = _make_record(realized_pnl=0.001)
        assert classify_outcome(rec) == "win"


# =====================================================================
#  Custom threshold tests
# =====================================================================

class TestCustomThreshold:
    """Test low_sample_threshold parameter."""

    def test_custom_threshold_higher(self):
        records = [_make_record(realized_pnl=50.0)] * 8
        report = build_calibration_report(records, low_sample_threshold=10)
        rc = report["regime_calibration"]
        assert rc[0]["low_sample_warning"] is True

    def test_custom_threshold_lower(self):
        records = [_make_record(realized_pnl=50.0)] * 3
        report = build_calibration_report(records, low_sample_threshold=2)
        rc = report["regime_calibration"]
        assert rc[0]["low_sample_warning"] is False

    def test_custom_threshold_affects_status(self):
        records = [_make_record(realized_pnl=50.0)] * 3
        report = build_calibration_report(records, low_sample_threshold=2)
        assert report["status"] == "sufficient"

    def test_threshold_in_metadata(self):
        report = build_calibration_report([], low_sample_threshold=10)
        assert report["metadata"]["low_sample_threshold"] == 10


# =====================================================================
#  Validation tests
# =====================================================================

class TestValidation:
    """Test validate_calibration_report."""

    def test_valid_report_passes(self):
        report = build_calibration_report([_make_record(realized_pnl=50.0)])
        ok, errors = validate_calibration_report(report)
        assert ok is True
        assert errors == []

    def test_empty_report_passes(self):
        report = build_calibration_report([])
        ok, errors = validate_calibration_report(report)
        assert ok is True

    def test_non_dict_fails(self):
        ok, errors = validate_calibration_report("not_a_dict")
        assert ok is False
        assert "report must be a dict" in errors

    def test_missing_key_detected(self):
        report = build_calibration_report([])
        del report["status"]
        ok, errors = validate_calibration_report(report)
        assert ok is False
        assert any("status" in e for e in errors)

    def test_wrong_version_detected(self):
        report = build_calibration_report([])
        report["calibration_version"] = "999.0"
        ok, errors = validate_calibration_report(report)
        assert ok is False
        assert any("version" in e for e in errors)

    def test_invalid_status_detected(self):
        report = build_calibration_report([])
        report["status"] = "bad_status"
        ok, errors = validate_calibration_report(report)
        assert ok is False
        assert any("status" in e for e in errors)

    def test_non_list_section_detected(self):
        report = build_calibration_report([])
        report["regime_calibration"] = "not_a_list"
        ok, errors = validate_calibration_report(report)
        assert ok is False
        assert any("regime_calibration" in e for e in errors)

    def test_non_list_warning_flags(self):
        report = build_calibration_report([])
        report["warning_flags"] = "not_a_list"
        ok, errors = validate_calibration_report(report)
        assert ok is False

    def test_missing_sample_size_key(self):
        report = build_calibration_report([])
        del report["sample_size"]["with_pnl"]
        ok, errors = validate_calibration_report(report)
        assert ok is False
        assert any("with_pnl" in e for e in errors)

    def test_sample_size_not_dict(self):
        report = build_calibration_report([])
        report["sample_size"] = "not_a_dict"
        ok, errors = validate_calibration_report(report)
        assert ok is False

    def test_compatible_versions_accepted(self):
        """Both 1.0 and 1.1 version strings should be accepted."""
        report = build_calibration_report([])
        for v in _COMPATIBLE_VERSIONS:
            r = dict(report, calibration_version=v)
            r["metadata"] = dict(report["metadata"], calibration_version=v)
            ok, errors = validate_calibration_report(r)
            version_errors = [e for e in errors if "version" in e]
            assert not version_errors, f"Version {v} rejected: {version_errors}"

    def test_incompatible_version_rejected(self):
        report = build_calibration_report([])
        report["calibration_version"] = "99.9"
        ok, errors = validate_calibration_report(report)
        assert ok is False
        assert any("version" in e for e in errors)

    def test_alignment_attribution_validated(self):
        report = build_calibration_report([])
        report["alignment_attribution"] = "not_a_list"
        ok, errors = validate_calibration_report(report)
        assert ok is False
        assert any("alignment_attribution" in e for e in errors)


# =====================================================================
#  Sample size tests
# =====================================================================

class TestSampleSize:
    """Test sample_size computation."""

    def test_total_records(self):
        records = [_make_record()] * 5
        report = build_calibration_report(records)
        assert report["sample_size"]["total_records"] == 5

    def test_closed_records_count(self):
        records = [
            _make_record(status="closed"),
            _make_record(status="recorded"),
            _make_record(status="closed"),
        ]
        report = build_calibration_report(records)
        assert report["sample_size"]["closed_records"] == 2

    def test_with_outcome_count(self):
        records = [
            _make_record(realized_pnl=50.0),
            _make_record(realized_pnl=None),  # empty outcome snapshot
        ]
        report = build_calibration_report(records)
        # First has outcome_snapshot with realized_pnl, second has empty dict
        assert report["sample_size"]["with_outcome"] == 1

    def test_with_pnl_count(self):
        records = [
            _make_record(realized_pnl=50.0),
            _make_record(realized_pnl=-20.0),
            _make_record(realized_pnl=None),
        ]
        report = build_calibration_report(records)
        assert report["sample_size"]["with_pnl"] == 2

    def test_with_decided_count(self):
        records = [
            _make_record(realized_pnl=50.0),    # win → decided
            _make_record(realized_pnl=-20.0),   # loss → decided
            _make_record(realized_pnl=0.0),     # breakeven → NOT decided
            _make_record(realized_pnl=None),    # unknown → NOT decided
        ]
        report = build_calibration_report(records)
        assert report["sample_size"]["with_decided"] == 2

    def test_with_decided_zero_when_all_breakeven(self):
        records = [_make_record(realized_pnl=0.0)] * 3
        report = build_calibration_report(records)
        assert report["sample_size"]["with_decided"] == 0


# =====================================================================
#  Report summary tests (v1.1)
# =====================================================================

class TestReportSummary:
    """Test report_summary compact digest."""

    def test_summary_keys(self):
        report = build_calibration_report([_make_record(realized_pnl=50.0)])
        s = report_summary(report)
        expected_keys = {
            "calibration_version", "status", "total_records", "with_pnl",
            "with_decided", "regime_count", "warning_count", "top_regime",
            "module_role",
        }
        assert set(s.keys()) == expected_keys

    def test_summary_module_role(self):
        report = build_calibration_report([])
        s = report_summary(report)
        assert s["module_role"] == "summary"

    def test_summary_with_data(self):
        records = [_make_record(realized_pnl=50.0)] * 6
        report = build_calibration_report(records)
        s = report_summary(report)
        assert s["total_records"] == 6
        assert s["with_pnl"] == 6
        assert s["status"] == "sufficient"
        assert s["top_regime"] == "neutral"
        assert s["regime_count"] >= 1

    def test_summary_empty_report(self):
        report = build_calibration_report([])
        s = report_summary(report)
        assert s["total_records"] == 0
        assert s["top_regime"] is None
        assert s["status"] == "insufficient"

    def test_summary_version_matches_report(self):
        report = build_calibration_report([])
        s = report_summary(report)
        assert s["calibration_version"] == report["calibration_version"]

    def test_summary_warning_count(self):
        records = [_make_record(realized_pnl=50.0)]
        report = build_calibration_report(records)
        s = report_summary(report)
        assert s["warning_count"] == len(report["warning_flags"])


# =====================================================================
#  Integration tests
# =====================================================================

class TestIntegration:
    """Integration tests with realistic multi-record scenarios."""

    def test_diverse_records_full_report(self):
        records = _make_records_diverse()
        report = build_calibration_report(records)
        ok, errors = validate_calibration_report(report)
        assert ok is True, f"Validation errors: {errors}"
        assert report["sample_size"]["total_records"] == 10

    def test_diverse_records_regime_groups(self):
        records = _make_records_diverse()
        report = build_calibration_report(records)
        rc = report["regime_calibration"]
        # Should have at least neutral and bearish
        labels = {r["regime_label"] for r in rc}
        assert "neutral" in labels

    def test_diverse_records_strategy_groups(self):
        records = _make_records_diverse()
        report = build_calibration_report(records)
        strat = report["strategy_attribution"]
        strategies = {s["strategy"] for s in strat}
        assert "iron_condor" in strategies
        assert "put_credit_spread" in strategies

    def test_diverse_records_policy_groups(self):
        records = _make_records_diverse()
        report = build_calibration_report(records)
        pol = report["policy_attribution"]
        decisions = {p["policy_decision"] for p in pol}
        assert "allow" in decisions

    def test_diverse_records_has_warnings(self):
        records = _make_records_diverse()
        report = build_calibration_report(records)
        # With 10 records, should be sufficient
        assert report["status"] == "sufficient"

    def test_diverse_records_conviction_groups(self):
        records = _make_records_diverse()
        report = build_calibration_report(records)
        conv = report["conviction_attribution"]
        convictions = {c["conviction"] for c in conv}
        assert "high" in convictions
        assert "low" in convictions

    def test_diverse_records_event_groups(self):
        records = _make_records_diverse()
        report = build_calibration_report(records)
        evt = report["event_attribution"]
        states = {e["event_risk_state"] for e in evt}
        assert "low" in states
        assert "elevated" in states

    def test_diverse_records_conflict_groups(self):
        records = _make_records_diverse()
        report = build_calibration_report(records)
        conf = report["conflict_attribution"]
        assert len(conf) >= 2  # at least True/False groups

    def test_all_sections_non_empty_with_data(self):
        records = _make_records_diverse()
        report = build_calibration_report(records)
        assert len(report["regime_calibration"]) > 0
        assert len(report["signal_attribution"]) > 0
        assert len(report["strategy_attribution"]) > 0
        assert len(report["policy_attribution"]) > 0
        assert len(report["conflict_attribution"]) > 0
        assert len(report["event_attribution"]) > 0
        assert len(report["conviction_attribution"]) > 0
        assert len(report["alignment_attribution"]) > 0

    def test_pnl_sanity_all_groups(self):
        """Total pnl should be consistent across groups that partition all records."""
        records = [
            _make_record(realized_pnl=100.0, event_risk_state="low"),
            _make_record(realized_pnl=-50.0, event_risk_state="elevated"),
            _make_record(realized_pnl=30.0, event_risk_state="low"),
        ]
        report = build_calibration_report(records)
        evt = report["event_attribution"]
        # event groups partition all records
        total = sum(e.get("total_pnl", 0) or 0 for e in evt)
        assert abs(total - 80.0) < 0.01

    def test_round_trip_validate(self):
        """Every build_calibration_report output must pass validate."""
        for records in [
            [],
            [_make_record(realized_pnl=None)],
            [_make_record(realized_pnl=50.0)],
            _make_records_diverse(),
        ]:
            report = build_calibration_report(records)
            ok, errors = validate_calibration_report(report)
            assert ok is True, f"Failed with {len(records)} records: {errors}"
