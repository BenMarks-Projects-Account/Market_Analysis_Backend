"""Tests for the V2 comparison harness, equivalence, and snapshot framework.

Covers:
1. Candidate equivalence key building
2. Candidate matching (matched / legacy-only / v2-only)
3. Metric delta computation
4. Diagnostics diff computation
5. Comparison report shape and stability
6. Snapshot fixture loading and building
7. Full compare_from_results end-to-end
8. Trust signal detection
9. Edge cases (empty, zero candidates, all-rejected)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.services.scanner_v2.comparison.contracts import (
    COMPARISON_CONTRACT_VERSION,
    CandidateMatch,
    ComparisonReport,
    ComparisonSnapshot,
    DiagnosticsDiff,
    MetricDelta,
)
from app.services.scanner_v2.comparison.equivalence import (
    build_comparison_key,
    match_candidates,
)
from app.services.scanner_v2.comparison.harness import (
    compare_from_results,
    compare_scanner_family,
)
from app.services.scanner_v2.comparison.snapshots import (
    build_snapshot,
    build_synthetic_chain,
    load_snapshot,
    save_snapshot,
)
from app.services.scanner_v2.comparison.fixtures import (
    fixture_spy_golden_put_spread,
    fixture_spy_bad_liquidity,
    fixture_spy_wide_spreads,
    fixture_spy_empty_chain,
    fixture_spy_golden_iron_condor,
)


# ════════════════════════════════════════════════════════════════════
#  § 1  Candidate equivalence key — build_comparison_key
# ════════════════════════════════════════════════════════════════════

class TestBuildComparisonKey:
    """Verify that comparison keys are stable and correctly derived."""

    def test_v2_candidate_with_legs(self):
        cand = {
            "symbol": "SPY",
            "strategy_id": "put_credit_spread",
            "expiration": "2026-03-20",
            "legs": [
                {"strike": 590.0, "side": "short"},
                {"strike": 585.0, "side": "long"},
            ],
        }
        key = build_comparison_key(cand)
        assert key == "SPY|put_credit_spread|2026-03-20|585/590"

    def test_legacy_candidate_flat_strikes(self):
        """Legacy candidates may have short_strike + long_strike."""
        cand = {
            "symbol": "SPY",
            "strategy_id": "put_credit_spread",
            "expiration": "2026-03-20",
            "short_strike": 590.0,
            "long_strike": 585.0,
        }
        key = build_comparison_key(cand)
        assert key == "SPY|put_credit_spread|2026-03-20|585/590"

    def test_key_sorts_strikes_ascending(self):
        """Strikes in the key are always sorted low→high."""
        cand = {
            "symbol": "QQQ",
            "strategy_id": "iron_condor",
            "expiration": "2026-04-17",
            "legs": [
                {"strike": 510.0},
                {"strike": 500.0},
                {"strike": 530.0},
                {"strike": 540.0},
            ],
        }
        key = build_comparison_key(cand)
        assert "500/510/530/540" in key

    def test_legacy_and_v2_produce_same_key(self):
        """Same structural candidate from both systems → same key."""
        legacy = {
            "symbol": "SPY",
            "strategy_id": "put_credit_spread",
            "expiration": "2026-03-20",
            "legs": [
                {"strike": 590.0, "side": "short", "bid": 1.50},
                {"strike": 585.0, "side": "long", "bid": 0.65},
            ],
        }
        v2 = {
            "symbol": "SPY",
            "strategy_id": "put_credit_spread",
            "expiration": "2026-03-20",
            "legs": [
                {"index": 0, "side": "short", "strike": 590.0,
                 "option_type": "put", "bid": 1.50, "ask": 1.65},
                {"index": 1, "side": "long", "strike": 585.0,
                 "option_type": "put", "bid": 0.65, "ask": 0.80},
            ],
        }
        assert build_comparison_key(legacy) == build_comparison_key(v2)

    def test_missing_symbol_handled(self):
        cand = {"strategy_id": "put_credit_spread", "expiration": "2026-03-20"}
        key = build_comparison_key(cand)
        assert key.startswith("|")  # empty symbol

    def test_no_legs_no_strikes(self):
        cand = {
            "symbol": "SPY",
            "strategy_id": "put_credit_spread",
            "expiration": "2026-03-20",
        }
        key = build_comparison_key(cand)
        assert key == "SPY|put_credit_spread|2026-03-20|"


# ════════════════════════════════════════════════════════════════════
#  § 2  Candidate matching — match_candidates
# ════════════════════════════════════════════════════════════════════

class TestMatchCandidates:
    """Verify candidate matching across legacy and V2 outputs."""

    def _make_candidate(self, strike_short, strike_long, **extra):
        return {
            "symbol": "SPY",
            "strategy_id": "put_credit_spread",
            "expiration": "2026-03-20",
            "legs": [
                {"strike": strike_short, "side": "short"},
                {"strike": strike_long, "side": "long"},
            ],
            **extra,
        }

    def test_perfect_overlap(self):
        legacy = [self._make_candidate(590, 585)]
        v2 = [self._make_candidate(590, 585)]
        matches = match_candidates(legacy, v2)

        assert len(matches) == 1
        assert matches[0].match_type == "matched"
        assert matches[0].legacy_candidate is not None
        assert matches[0].v2_candidate is not None

    def test_legacy_only(self):
        legacy = [self._make_candidate(590, 585)]
        v2: list = []
        matches = match_candidates(legacy, v2)

        assert len(matches) == 1
        assert matches[0].match_type == "legacy_only"

    def test_v2_only(self):
        legacy: list = []
        v2 = [self._make_candidate(590, 585)]
        matches = match_candidates(legacy, v2)

        assert len(matches) == 1
        assert matches[0].match_type == "v2_only"

    def test_mixed_overlap(self):
        """One matched, one legacy-only, one v2-only."""
        legacy = [
            self._make_candidate(590, 585),
            self._make_candidate(585, 580),
        ]
        v2 = [
            self._make_candidate(590, 585),
            self._make_candidate(580, 575),
        ]
        matches = match_candidates(legacy, v2)

        assert len(matches) == 3
        types = {m.match_type for m in matches}
        assert types == {"matched", "legacy_only", "v2_only"}

    def test_empty_inputs(self):
        matches = match_candidates([], [])
        assert matches == []

    def test_matched_has_metric_deltas(self):
        legacy = [self._make_candidate(
            590, 585,
            net_credit=0.85,
            p_win_used=0.68,
            max_profit=85.0,
            max_loss=415.0,
        )]
        v2 = [self._make_candidate(
            590, 585,
            math={
                "net_credit": 0.80,
                "pop": 0.70,
                "max_profit": 80.0,
                "max_loss": 420.0,
            },
        )]
        matches = match_candidates(legacy, v2)

        assert len(matches) == 1
        m = matches[0]
        assert m.match_type == "matched"
        assert len(m.metric_deltas) > 0

        # Find net_credit delta
        credit_delta = next(
            d for d in m.metric_deltas if d.metric == "net_credit"
        )
        assert credit_delta.legacy_value == 0.85
        assert credit_delta.v2_value == 0.80
        assert credit_delta.abs_diff == pytest.approx(0.05, abs=0.001)

    def test_matched_has_diagnostics_diff(self):
        legacy = [self._make_candidate(590, 585, rejection_codes=[])]
        v2 = [self._make_candidate(
            590, 585,
            passed=True,
            diagnostics={
                "structural_checks": [{"name": "valid_leg_count", "passed": True}],
                "quote_checks": [],
                "liquidity_checks": [],
                "math_checks": [],
                "reject_reasons": [],
                "warnings": [],
                "pass_reasons": ["all structural checks passed"],
            },
        )]
        matches = match_candidates(legacy, v2)
        m = matches[0]

        assert m.diagnostics_diff is not None
        assert m.diagnostics_diff.legacy_passed is True
        assert m.diagnostics_diff.v2_passed is True
        assert m.diagnostics_diff.v2_structural_checks == 1


# ════════════════════════════════════════════════════════════════════
#  § 3  Metric delta computation
# ════════════════════════════════════════════════════════════════════

class TestMetricDelta:

    def test_serializable(self):
        delta = MetricDelta(
            metric="net_credit",
            legacy_value=0.85,
            v2_value=0.80,
            abs_diff=0.05,
            pct_diff=0.0588,
        )
        d = delta.to_dict()
        assert d["metric"] == "net_credit"
        assert d["abs_diff"] == 0.05

    def test_none_values(self):
        delta = MetricDelta(
            metric="pop",
            legacy_value=0.68,
            v2_value=None,
        )
        d = delta.to_dict()
        assert d["v2_value"] is None
        assert d["abs_diff"] is None


# ════════════════════════════════════════════════════════════════════
#  § 4  Diagnostics diff
# ════════════════════════════════════════════════════════════════════

class TestDiagnosticsDiff:

    def test_serializable(self):
        diff = DiagnosticsDiff(
            legacy_rejection_codes=["pop_below_floor"],
            v2_rejection_codes=["v2_inverted_quote"],
            legacy_only_rejections=["pop_below_floor"],
            v2_only_rejections=["v2_inverted_quote"],
            shared_rejections=[],
            legacy_passed=False,
            v2_passed=False,
        )
        d = diff.to_dict()
        assert d["legacy_only_rejections"] == ["pop_below_floor"]
        assert d["v2_only_rejections"] == ["v2_inverted_quote"]


# ════════════════════════════════════════════════════════════════════
#  § 5  Snapshot framework
# ════════════════════════════════════════════════════════════════════

class TestSnapshotFramework:

    def test_build_snapshot(self):
        chain = build_synthetic_chain(
            symbol="SPY",
            underlying_price=595.50,
            expiration="2026-03-20",
            put_strikes=[{"strike": 590.0, "bid": 1.50, "ask": 1.65}],
        )
        snap = build_snapshot(
            snapshot_id="test_snap",
            symbol="SPY",
            underlying_price=595.50,
            chain=chain,
        )
        assert snap.snapshot_id == "test_snap"
        assert snap.symbol == "SPY"
        assert snap.underlying_price == 595.50
        assert len(snap.expirations) == 1
        assert snap.expirations[0] == "2026-03-20"
        assert snap.captured_at  # non-empty

    def test_snapshot_serialization_roundtrip(self):
        snap = fixture_spy_golden_put_spread()
        d = snap.to_dict()
        restored = ComparisonSnapshot.from_dict(d)
        assert restored.snapshot_id == snap.snapshot_id
        assert restored.symbol == snap.symbol
        assert restored.underlying_price == snap.underlying_price

    def test_save_and_load_snapshot(self, tmp_path):
        snap = fixture_spy_golden_put_spread()
        path = tmp_path / "test_snap.json"
        save_snapshot(snap, path)
        assert path.exists()

        loaded = load_snapshot(path)
        assert loaded.snapshot_id == snap.snapshot_id
        assert loaded.symbol == snap.symbol
        assert len(loaded.chain["options"]["option"]) == len(
            snap.chain["options"]["option"]
        )

    def test_load_nonexistent_raises(self):
        with pytest.raises(FileNotFoundError):
            load_snapshot("/tmp/does_not_exist_abcxyz.json")

    def test_synthetic_chain_structure(self):
        chain = build_synthetic_chain(
            symbol="SPY",
            underlying_price=595.50,
            expiration="2026-03-20",
            put_strikes=[
                {"strike": 590.0, "bid": 1.50, "ask": 1.65, "delta": -0.30},
            ],
            call_strikes=[
                {"strike": 600.0, "bid": 1.20, "ask": 1.35, "delta": 0.28},
            ],
        )
        options = chain["options"]["option"]
        assert len(options) == 2
        put = [o for o in options if o["option_type"] == "put"][0]
        call = [o for o in options if o["option_type"] == "call"][0]

        assert put["strike"] == 590.0
        assert put["bid"] == 1.50
        assert put["greeks"]["delta"] == -0.30
        assert call["strike"] == 600.0
        assert call["greeks"]["delta"] == 0.28


# ════════════════════════════════════════════════════════════════════
#  § 6  Fixtures load correctly
# ════════════════════════════════════════════════════════════════════

class TestFixtures:

    def test_golden_put_spread_fixture(self):
        snap = fixture_spy_golden_put_spread()
        assert snap.symbol == "SPY"
        assert "golden" in snap.tags
        options = snap.chain["options"]["option"]
        assert len(options) == 4  # 4 puts

    def test_bad_liquidity_fixture(self):
        snap = fixture_spy_bad_liquidity()
        options = snap.chain["options"]["option"]
        # One with missing OI
        missing_oi = [o for o in options if o.get("open_interest") is None]
        assert len(missing_oi) == 1

    def test_wide_spreads_fixture(self):
        snap = fixture_spy_wide_spreads()
        options = snap.chain["options"]["option"]
        # One inverted quote
        inverted = [o for o in options if (o.get("bid") or 0) > (o.get("ask") or 999)]
        assert len(inverted) == 1

    def test_empty_chain_fixture(self):
        snap = fixture_spy_empty_chain()
        options = snap.chain["options"]["option"]
        assert len(options) == 0

    def test_iron_condor_fixture(self):
        snap = fixture_spy_golden_iron_condor()
        options = snap.chain["options"]["option"]
        puts = [o for o in options if o["option_type"] == "put"]
        calls = [o for o in options if o["option_type"] == "call"]
        assert len(puts) == 2
        assert len(calls) == 2


# ════════════════════════════════════════════════════════════════════
#  § 7  compare_from_results end-to-end
# ════════════════════════════════════════════════════════════════════

class TestCompareFromResults:

    def _make_v2_candidate(self, strike_short, strike_long, **overrides):
        """Build a V2-shape candidate dict."""
        base = {
            "candidate_id": f"SPY|put_credit_spread|2026-03-20|{strike_long}/{strike_short}|0",
            "scanner_key": "put_credit_spread",
            "strategy_id": "put_credit_spread",
            "family_key": "vertical_spreads",
            "symbol": "SPY",
            "underlying_price": 595.50,
            "expiration": "2026-03-20",
            "dte": 9,
            "legs": [
                {
                    "index": 0, "side": "short", "strike": strike_short,
                    "option_type": "put", "expiration": "2026-03-20",
                    "bid": 1.50, "ask": 1.65, "mid": 1.575,
                    "delta": -0.30, "iv": 0.22,
                    "open_interest": 5000, "volume": 800,
                },
                {
                    "index": 1, "side": "long", "strike": strike_long,
                    "option_type": "put", "expiration": "2026-03-20",
                    "bid": 0.65, "ask": 0.80, "mid": 0.725,
                    "delta": -0.18, "iv": 0.21,
                    "open_interest": 3000, "volume": 450,
                },
            ],
            "math": {
                "net_credit": 0.70,
                "max_profit": 70.0,
                "max_loss": 430.0,
                "width": 5.0,
                "pop": 0.70,
                "ev": -80.0,
                "ror": 0.163,
                "notes": {"net_credit": "short.bid - long.ask"},
            },
            "diagnostics": {
                "structural_checks": [
                    {"name": "valid_leg_count", "passed": True, "detail": "2 legs"},
                ],
                "quote_checks": [
                    {"name": "quote_present_leg_0", "passed": True, "detail": ""},
                ],
                "liquidity_checks": [],
                "math_checks": [
                    {"name": "max_loss_positive", "passed": True, "detail": "430.0"},
                ],
                "reject_reasons": [],
                "warnings": [],
                "pass_reasons": ["all checks passed"],
            },
            "passed": True,
            "downstream_usable": True,
        }
        base.update(overrides)
        return base

    def _make_legacy_candidate(self, strike_short, strike_long, **overrides):
        """Build a legacy-shape candidate dict."""
        base = {
            "symbol": "SPY",
            "strategy_id": "put_credit_spread",
            "expiration": "2026-03-20",
            "dte": 9,
            "underlying_price": 595.50,
            "short_strike": strike_short,
            "long_strike": strike_long,
            "width": 5.0,
            "net_credit": 0.85,
            "max_profit": 85.0,
            "max_loss": 415.0,
            "p_win_used": 0.68,
            "ev_per_share": 0.42,
            "return_on_risk": 0.205,
            "legs": [
                {"strike": strike_short, "side": "short", "option_type": "put"},
                {"strike": strike_long, "side": "long", "option_type": "put"},
            ],
            "rejection_codes": [],
        }
        base.update(overrides)
        return base

    def test_perfect_overlap_report(self):
        snapshot = fixture_spy_golden_put_spread()

        legacy_result = {
            "accepted_trades": [self._make_legacy_candidate(590, 585)],
            "rejected_trades": [],
            "candidate_count": 1,
            "accepted_count": 1,
            "filter_trace": {
                "preset_name": "wide",
                "resolved_thresholds": {},
                "stage_counts": [{"stage": "constructed", "remaining": 1}],
                "rejection_reason_counts": {},
                "data_quality_counts": {},
            },
        }
        v2_result = {
            "candidates": [self._make_v2_candidate(590, 585)],
            "rejected": [],
            "total_constructed": 1,
            "total_passed": 1,
            "total_rejected": 0,
            "reject_reason_counts": {},
            "phase_counts": [{"phase": "constructed", "remaining": 1}],
            "family_key": "vertical_spreads",
        }

        report = compare_from_results(
            scanner_key="put_credit_spread",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        assert isinstance(report, ComparisonReport)
        assert report.comparison_version == COMPARISON_CONTRACT_VERSION
        assert report.scanner_key == "put_credit_spread"
        assert report.scanner_family == "vertical_spreads"
        assert report.symbol == "SPY"

        assert report.overlap_count == 1
        assert report.legacy_only_count == 0
        assert report.v2_only_count == 0

        assert report.legacy_total_passed == 1
        assert report.v2_total_passed == 1

        # Metric deltas should show net_credit difference
        matched = [m for m in report.matches if m.match_type == "matched"]
        assert len(matched) == 1
        credit_delta = next(
            d for d in matched[0].metric_deltas if d.metric == "net_credit"
        )
        assert credit_delta.legacy_value == 0.85
        assert credit_delta.v2_value == 0.70

    def test_mixed_candidates_report(self):
        """One matched, one legacy-only, one v2-only."""
        snapshot = fixture_spy_golden_put_spread()

        legacy_result = {
            "accepted_trades": [
                self._make_legacy_candidate(590, 585),
                self._make_legacy_candidate(585, 580),
            ],
            "rejected_trades": [],
            "candidate_count": 2,
            "accepted_count": 2,
            "filter_trace": {
                "preset_name": "wide",
                "resolved_thresholds": {},
                "stage_counts": [],
                "rejection_reason_counts": {},
                "data_quality_counts": {},
            },
        }
        v2_result = {
            "candidates": [
                self._make_v2_candidate(590, 585),
                self._make_v2_candidate(580, 575),
            ],
            "rejected": [],
            "total_constructed": 2,
            "total_passed": 2,
            "total_rejected": 0,
            "reject_reason_counts": {},
            "phase_counts": [],
            "family_key": "vertical_spreads",
        }

        report = compare_from_results(
            scanner_key="put_credit_spread",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        assert report.overlap_count == 1
        assert report.legacy_only_count == 1
        assert report.v2_only_count == 1
        assert report.total_compared == 3

    def test_empty_results(self):
        snapshot = fixture_spy_empty_chain()

        legacy_result = {
            "accepted_trades": [],
            "rejected_trades": [],
            "candidate_count": 0,
            "accepted_count": 0,
            "filter_trace": {
                "preset_name": "wide",
                "resolved_thresholds": {},
                "stage_counts": [],
                "rejection_reason_counts": {},
                "data_quality_counts": {},
            },
        }
        v2_result = {
            "candidates": [],
            "rejected": [],
            "total_constructed": 0,
            "total_passed": 0,
            "total_rejected": 0,
            "reject_reason_counts": {},
            "phase_counts": [],
            "family_key": "vertical_spreads",
        }

        report = compare_from_results(
            scanner_key="put_credit_spread",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        assert report.overlap_count == 0
        assert report.legacy_only_count == 0
        assert report.v2_only_count == 0
        assert "No candidates" in report.conclusions[0]

    def test_report_serializable(self):
        """Report can be serialized to JSON without errors."""
        snapshot = fixture_spy_golden_put_spread()

        legacy_result = {
            "accepted_trades": [self._make_legacy_candidate(590, 585)],
            "rejected_trades": [],
            "candidate_count": 1,
            "accepted_count": 1,
            "filter_trace": {
                "preset_name": "wide",
                "resolved_thresholds": {},
                "stage_counts": [],
                "rejection_reason_counts": {},
                "data_quality_counts": {},
            },
        }
        v2_result = {
            "candidates": [self._make_v2_candidate(590, 585)],
            "rejected": [],
            "total_constructed": 1,
            "total_passed": 1,
            "total_rejected": 0,
            "reject_reason_counts": {},
            "phase_counts": [],
            "family_key": "vertical_spreads",
        }

        report = compare_from_results(
            scanner_key="put_credit_spread",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        d = report.to_dict()
        json_str = json.dumps(d, default=str)
        assert len(json_str) > 100
        parsed = json.loads(json_str)
        assert parsed["comparison_version"] == COMPARISON_CONTRACT_VERSION
        assert parsed["scanner_key"] == "put_credit_spread"

    def test_v2_caught_broken_trust_signal(self):
        """V2 rejects a structurally broken candidate legacy accepted."""
        snapshot = fixture_spy_golden_put_spread()

        # Legacy: accepted a candidate
        legacy_result = {
            "accepted_trades": [self._make_legacy_candidate(590, 585)],
            "rejected_trades": [],
            "candidate_count": 1,
            "accepted_count": 1,
            "filter_trace": {
                "preset_name": "wide",
                "resolved_thresholds": {},
                "stage_counts": [],
                "rejection_reason_counts": {},
                "data_quality_counts": {},
            },
        }

        # V2: rejected same candidate due to structural issue
        v2_cand = self._make_v2_candidate(590, 585)
        v2_cand["passed"] = False
        v2_cand["diagnostics"]["reject_reasons"] = ["v2_impossible_pricing"]
        v2_cand["diagnostics"]["structural_checks"].append(
            {"name": "non_degenerate_pricing", "passed": False,
             "detail": "credit >= width"}
        )

        v2_result = {
            "candidates": [],
            "rejected": [v2_cand],
            "total_constructed": 1,
            "total_passed": 0,
            "total_rejected": 1,
            "reject_reason_counts": {"v2_impossible_pricing": 1},
            "phase_counts": [],
            "family_key": "vertical_spreads",
        }

        report = compare_from_results(
            scanner_key="put_credit_spread",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        assert report.v2_caught_broken == 1
        assert any("caught" in c.lower() for c in report.conclusions)

    def test_v2_new_valid_trust_signal(self):
        """V2 surfaces a candidate legacy over-filtered."""
        snapshot = fixture_spy_golden_put_spread()

        # Legacy: rejected the candidate
        legacy_result = {
            "accepted_trades": [],
            "rejected_trades": [],
            "candidate_count": 0,
            "accepted_count": 0,
            "filter_trace": {
                "preset_name": "wide",
                "resolved_thresholds": {},
                "stage_counts": [],
                "rejection_reason_counts": {},
                "data_quality_counts": {},
            },
        }

        # V2: accepted it
        v2_result = {
            "candidates": [self._make_v2_candidate(590, 585)],
            "rejected": [],
            "total_constructed": 1,
            "total_passed": 1,
            "total_rejected": 0,
            "reject_reason_counts": {},
            "phase_counts": [],
            "family_key": "vertical_spreads",
        }

        report = compare_from_results(
            scanner_key="put_credit_spread",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        assert report.v2_new_valid == 1
        assert report.v2_only_count == 1

    def test_rejection_analysis_populated(self):
        """Rejection counts from both systems appear in the report."""
        snapshot = fixture_spy_golden_put_spread()

        legacy_result = {
            "accepted_trades": [],
            "rejected_trades": [],
            "candidate_count": 2,
            "accepted_count": 0,
            "filter_trace": {
                "preset_name": "wide",
                "resolved_thresholds": {},
                "stage_counts": [],
                "rejection_reason_counts": {
                    "pop_below_floor": 1,
                    "ev_negative": 1,
                },
                "data_quality_counts": {},
            },
        }
        v2_result = {
            "candidates": [],
            "rejected": [],
            "total_constructed": 2,
            "total_passed": 0,
            "total_rejected": 2,
            "reject_reason_counts": {
                "v2_missing_quote": 1,
                "v2_inverted_quote": 1,
            },
            "phase_counts": [],
            "family_key": "vertical_spreads",
        }

        report = compare_from_results(
            scanner_key="put_credit_spread",
            snapshot=snapshot,
            legacy_result=legacy_result,
            v2_result=v2_result,
        )

        assert report.legacy_rejection_counts == {
            "pop_below_floor": 1,
            "ev_negative": 1,
        }
        assert report.v2_rejection_counts == {
            "v2_missing_quote": 1,
            "v2_inverted_quote": 1,
        }


# ════════════════════════════════════════════════════════════════════
#  § 8  compare_scanner_family with injected runners
# ════════════════════════════════════════════════════════════════════

class TestCompareScannerFamily:

    def test_with_injected_runners(self):
        """Full harness with injected legacy + V2 runners."""
        snapshot = fixture_spy_golden_put_spread()

        def legacy_runner(key, snap, preset):
            return {
                "accepted_trades": [{
                    "symbol": "SPY",
                    "strategy_id": "put_credit_spread",
                    "expiration": "2026-03-20",
                    "legs": [
                        {"strike": 590.0, "side": "short"},
                        {"strike": 585.0, "side": "long"},
                    ],
                    "net_credit": 0.85,
                    "max_profit": 85.0,
                    "max_loss": 415.0,
                    "p_win_used": 0.68,
                    "rejection_codes": [],
                }],
                "rejected_trades": [],
                "candidate_count": 1,
                "accepted_count": 1,
                "filter_trace": {
                    "preset_name": preset,
                    "resolved_thresholds": {},
                    "stage_counts": [],
                    "rejection_reason_counts": {},
                    "data_quality_counts": {},
                },
            }

        def v2_runner(key, snap):
            return {
                "candidates": [{
                    "candidate_id": "SPY|put_credit_spread|2026-03-20|585/590|0",
                    "scanner_key": "put_credit_spread",
                    "strategy_id": "put_credit_spread",
                    "family_key": "vertical_spreads",
                    "symbol": "SPY",
                    "expiration": "2026-03-20",
                    "legs": [
                        {"index": 0, "side": "short", "strike": 590.0,
                         "option_type": "put"},
                        {"index": 1, "side": "long", "strike": 585.0,
                         "option_type": "put"},
                    ],
                    "math": {"net_credit": 0.70, "pop": 0.70,
                             "max_profit": 70.0, "max_loss": 430.0,
                             "notes": {}},
                    "diagnostics": {
                        "structural_checks": [],
                        "quote_checks": [],
                        "liquidity_checks": [],
                        "math_checks": [],
                        "reject_reasons": [],
                        "warnings": [],
                        "pass_reasons": [],
                    },
                    "passed": True,
                }],
                "rejected": [],
                "total_constructed": 1,
                "total_passed": 1,
                "total_rejected": 0,
                "reject_reason_counts": {},
                "phase_counts": [],
                "family_key": "vertical_spreads",
            }

        report = compare_scanner_family(
            scanner_key="put_credit_spread",
            snapshot=snapshot,
            legacy_runner=legacy_runner,
            v2_runner=v2_runner,
        )

        assert report.overlap_count == 1
        assert report.legacy_elapsed_ms >= 0
        assert report.v2_elapsed_ms >= 0
        assert report.comparison_elapsed_ms >= 0
        assert report.generated_at


# ════════════════════════════════════════════════════════════════════
#  § 9  Report shape stability
# ════════════════════════════════════════════════════════════════════

class TestReportShapeStability:
    """Verify that ComparisonReport has all required fields."""

    def test_report_fields_present(self):
        report = ComparisonReport()
        required_fields = [
            "comparison_id", "comparison_version", "scanner_family",
            "scanner_key", "snapshot_id", "symbol", "underlying_price",
            "legacy_total_constructed", "legacy_total_passed",
            "legacy_total_rejected",
            "v2_total_constructed", "v2_total_passed", "v2_total_rejected",
            "overlap_count", "legacy_only_count", "v2_only_count",
            "matches", "legacy_rejection_counts", "v2_rejection_counts",
            "legacy_stage_counts", "v2_phase_counts",
            "v2_caught_broken", "v2_new_valid",
            "v2_diagnostics_richer_count",
            "metric_summary", "anomalies", "conclusions",
            "legacy_elapsed_ms", "v2_elapsed_ms", "comparison_elapsed_ms",
            "generated_at",
        ]
        d = report.to_dict()
        for f in required_fields:
            assert f in d, f"Missing field: {f}"

    def test_version_constant(self):
        assert COMPARISON_CONTRACT_VERSION == "1.0.0"
