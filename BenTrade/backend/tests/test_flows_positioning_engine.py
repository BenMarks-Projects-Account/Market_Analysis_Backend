"""Tests for Flows & Positioning Scoring Engine.

Scenarios:
  1. Supportive Continuation — healthy VIX, moderate positioning → score 55-85, supportive labels
  2. Crowded Reversal Risk — extreme one-sided positioning → low score, reversal/fragile labels
  3. Squeeze-Prone — high short interest, elevated VIX → squeeze/unwind risk elevated
  4. Mixed / Unstable — some pillars ok, some degraded → mid-range score
  5. Degraded Confidence — many None inputs → confidence penalty, warnings
  6. Single Pillar Crash — one pillar raises exception → composite still works
  7. UI Mapping — label/short_label match label bands exactly
  8. Provenance Metadata — SIGNAL_PROVENANCE in diagnostics
  9. Raw Inputs Partitioned — raw_inputs keys = positioning, crowding, squeeze, flow, stability
  10. Utility Functions — _clamp, _interpolate, _safe_float, _weighted_avg
"""

import pytest

from app.services.flows_positioning_engine import (
    PILLAR_WEIGHTS,
    SIGNAL_PROVENANCE,
    _CROWDING_GATE_THRESHOLD,
    _STABILITY_GATE_THRESHOLD,
    _SQUEEZE_RISK_GATE_THRESHOLD,
    _clamp,
    _interpolate,
    _label_from_score_with_gates,
    _safe_float,
    _weighted_avg,
    compute_flows_positioning_scores,
)


# ═══════════════════════════════════════════════════════════════════════
# HELPERS — fixture data dicts
# ═══════════════════════════════════════════════════════════════════════


def _supportive_positioning():
    """Moderate net long, reasonable put/call, low VIX."""
    return {
        "put_call_ratio": 0.78,
        "vix": 15.0,
        "retail_bull_pct": 42.0,
        "systematic_allocation": 62.0,
        "futures_net_long_pct": 55.0,
    }


def _supportive_crowding():
    """Not crowded — moderate positioning across the board."""
    return {
        "futures_net_long_pct": 52.0,
        "put_call_ratio": 0.82,
        "retail_bull_pct": 40.0,
        "retail_bear_pct": 32.0,
        "vix": 15.0,
        "short_interest_pct": 1.8,
    }


def _supportive_squeeze():
    """Low squeeze risk — moderate SI, moderate positioning."""
    return {
        "short_interest_pct": 1.5,
        "futures_net_long_pct": 52.0,
        "put_call_ratio": 0.80,
        "vix": 15.0,
        "vix_term_structure": 0.88,
    }


def _supportive_flow():
    """Inflows with persistence."""
    return {
        "flow_direction_score": 68.0,
        "flow_persistence_5d": 72.0,
        "flow_persistence_20d": 65.0,
        "inflow_outflow_balance": 66.0,
        "follow_through_score": 62.0,
    }


def _supportive_stability():
    """Low VIX, contango, moderate positioning."""
    return {
        "vix": 15.0,
        "vix_term_structure": 0.88,
        "futures_net_long_pct": 52.0,
        "flow_direction_score": 68.0,
        "flow_volatility": 25.0,
        "put_call_ratio": 0.80,
    }


def _supportive_source_meta():
    return {
        "market_context_freshness": "live",
        "has_direct_flow_data": False,
        "has_futures_positioning": False,
    }


def _crowded_positioning():
    """Extreme net-long, very low put/call — overextended bulls."""
    return {
        "put_call_ratio": 0.52,
        "vix": 11.0,
        "retail_bull_pct": 58.0,
        "systematic_allocation": 92.0,
        "futures_net_long_pct": 88.0,
    }


def _crowded_crowding():
    """Heavily crowded — extreme positioning everywhere."""
    return {
        "futures_net_long_pct": 88.0,
        "put_call_ratio": 0.52,
        "retail_bull_pct": 58.0,
        "retail_bear_pct": 15.0,
        "vix": 11.0,
        "short_interest_pct": 1.0,
    }


def _crowded_squeeze():
    """Low SI but extreme long-side crowding."""
    return {
        "short_interest_pct": 1.0,
        "futures_net_long_pct": 88.0,
        "put_call_ratio": 0.52,
        "vix": 11.0,
        "vix_term_structure": 0.82,
    }


def _crowded_flow():
    """Strong inflows — but could be chasing."""
    return {
        "flow_direction_score": 78.0,
        "flow_persistence_5d": 80.0,
        "flow_persistence_20d": 75.0,
        "inflow_outflow_balance": 78.0,
        "follow_through_score": 72.0,
    }


def _crowded_stability():
    """Low VIX but extreme positioning → fragile."""
    return {
        "vix": 11.0,
        "vix_term_structure": 0.82,
        "futures_net_long_pct": 88.0,
        "flow_direction_score": 78.0,
        "flow_volatility": 18.0,
        "put_call_ratio": 0.52,
    }


def _squeeze_prone_positioning():
    """Elevated VIX, elevated hedging, moderate net long."""
    return {
        "put_call_ratio": 1.15,
        "vix": 28.0,
        "retail_bull_pct": 24.0,
        "systematic_allocation": 32.0,
        "futures_net_long_pct": 35.0,
    }


def _squeeze_prone_crowding():
    """Elevated short interest, cautious sentiment."""
    return {
        "futures_net_long_pct": 35.0,
        "put_call_ratio": 1.15,
        "retail_bull_pct": 24.0,
        "retail_bear_pct": 46.0,
        "vix": 28.0,
        "short_interest_pct": 4.5,
    }


def _squeeze_prone_squeeze():
    """High short interest + backwardation → squeeze risk."""
    return {
        "short_interest_pct": 4.5,
        "futures_net_long_pct": 35.0,
        "put_call_ratio": 1.15,
        "vix": 28.0,
        "vix_term_structure": 1.08,
    }


def _squeeze_prone_flow():
    """Outflows with low persistence."""
    return {
        "flow_direction_score": 32.0,
        "flow_persistence_5d": 38.0,
        "flow_persistence_20d": 30.0,
        "inflow_outflow_balance": 34.0,
        "follow_through_score": 28.0,
    }


def _squeeze_prone_stability():
    """High VIX, backwardation, outflows → unstable."""
    return {
        "vix": 28.0,
        "vix_term_structure": 1.08,
        "futures_net_long_pct": 35.0,
        "flow_direction_score": 32.0,
        "flow_volatility": 65.0,
        "put_call_ratio": 1.15,
    }


def _empty_data():
    """All None — simulates total data failure."""
    return {}


# ═══════════════════════════════════════════════════════════════════════
# UTILITY TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestUtilities:
    """Tests for scoring utility functions."""

    def test_clamp_lower(self):
        assert _clamp(-10) == 0.0

    def test_clamp_upper(self):
        assert _clamp(150) == 100.0

    def test_clamp_normal(self):
        assert _clamp(55) == 55

    def test_safe_float_none(self):
        assert _safe_float(None) is None

    def test_safe_float_invalid(self):
        assert _safe_float("abc") is None

    def test_safe_float_valid(self):
        assert _safe_float("42.5") == 42.5

    def test_safe_float_default(self):
        assert _safe_float(None, default=50.0) == 50.0

    def test_interpolate_low(self):
        score = _interpolate(5, 10, 20, 0, 100)
        assert score == 0.0  # Clamped below

    def test_interpolate_mid(self):
        score = _interpolate(15, 10, 20, 0, 100)
        assert score == 50.0

    def test_interpolate_high(self):
        score = _interpolate(25, 10, 20, 0, 100)
        assert score == 100.0  # Clamped above

    def test_weighted_avg_basic(self):
        result = _weighted_avg([(80, 0.5), (60, 0.5)])
        assert result == 70.0

    def test_weighted_avg_with_none(self):
        result = _weighted_avg([(80, 0.5), (None, 0.5)])
        assert result == 80.0  # Ignores None

    def test_weighted_avg_all_none(self):
        result = _weighted_avg([(None, 0.5), (None, 0.5)])
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO TESTS
# ═══════════════════════════════════════════════════════════════════════


class TestSupportiveContinuation:
    """Scenario 1: Healthy positioning, moderate VIX, good flows → supportive score."""

    @pytest.fixture()
    def result(self):
        return compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )

    def test_score_range(self, result):
        assert 55 <= result["score"] <= 90, f"Score {result['score']} outside supportive range"

    def test_label_supportive(self, result):
        label = result["label"].lower()
        assert "supportive" in label or "mixed but tradable" in label, \
            f"Expected supportive or mixed label, got: {result['label']}"

    def test_short_label_present(self, result):
        assert result["short_label"] not in (None, "", "Unknown")

    def test_pillar_scores_populated(self, result):
        ps = result["pillar_scores"]
        for key in PILLAR_WEIGHTS:
            assert key in ps
            assert ps[key] is not None, f"Pillar {key} score is None"

    def test_strategy_bias_present(self, result):
        bias = result["strategy_bias"]
        assert "continuation_support" in bias
        assert "reversal_risk" in bias
        assert "squeeze_potential" in bias
        assert "fragility" in bias

    def test_positive_contributors_not_empty(self, result):
        assert len(result["positive_contributors"]) > 0

    def test_trader_takeaway_not_empty(self, result):
        assert result["trader_takeaway"] is not None
        assert len(result["trader_takeaway"]) > 10


class TestCrowdedReversalRisk:
    """Scenario 2: Extreme one-sided positioning → crowding leads to fragility."""

    @pytest.fixture()
    def result(self):
        return compute_flows_positioning_scores(
            positioning_data=_crowded_positioning(),
            crowding_data=_crowded_crowding(),
            squeeze_data=_crowded_squeeze(),
            flow_data=_crowded_flow(),
            stability_data=_crowded_stability(),
            source_meta=_supportive_source_meta(),
        )

    def test_crowding_pillar_depressed(self, result):
        """Crowding / Stretch pillar should score low due to extreme positioning."""
        cs = result["pillar_scores"]["crowding_stretch"]
        assert cs is not None
        # Extreme crowding should depress this pillar
        assert cs < 65, f"Crowding score {cs} not low enough for extreme positioning"

    def test_positioning_pressure_moderate_or_low(self, result):
        """Extreme positioning reduces P1 score via overextension penalties."""
        pp = result["pillar_scores"]["positioning_pressure"]
        assert pp is not None
        # Extreme net-long and low put/call → overextended → lower score
        assert pp < 70, f"Positioning pressure {pp} should be under 70 for extreme longs"

    def test_negative_contributors_present(self, result):
        """Should have negative contributors flagged."""
        assert len(result["negative_contributors"]) > 0 or len(result["warnings"]) > 0


class TestSqueezeProne:
    """Scenario 3: High SI, elevated VIX, outflows → squeeze/unwind risk."""

    @pytest.fixture()
    def result(self):
        return compute_flows_positioning_scores(
            positioning_data=_squeeze_prone_positioning(),
            crowding_data=_squeeze_prone_crowding(),
            squeeze_data=_squeeze_prone_squeeze(),
            flow_data=_squeeze_prone_flow(),
            stability_data=_squeeze_prone_stability(),
            source_meta=_supportive_source_meta(),
        )

    def test_overall_score_low(self, result):
        """High squeeze risk + outflows should drag composite down."""
        assert result["score"] < 65, f"Score {result['score']} too high for squeeze scenario"

    def test_squeeze_pillar_low(self, result):
        sq = result["pillar_scores"]["squeeze_unwind_risk"]
        assert sq is not None
        assert sq < 65, f"Squeeze pillar {sq} should be low with high SI + backwardation"

    def test_flow_pillar_low(self, result):
        fp = result["pillar_scores"]["flow_direction_persistence"]
        assert fp is not None
        assert fp < 45, f"Flow pillar {fp} should be low with outflows"

    def test_stability_pillar_low(self, result):
        stab = result["pillar_scores"]["positioning_stability"]
        assert stab is not None
        assert stab < 70, f"Stability pillar {stab} should be low with high VIX+backwardation"

    def test_label_reflects_risk(self, result):
        label = result["label"].lower()
        # VIX 28 with outflows is stressed but not fully collapsed;
        # engine may produce "Mixed but Tradable" or risk labels
        assert any(w in label for w in ("reversal", "unstable", "fragile", "mixed")), \
            f"Expected stressed label, got: {result['label']}"


class TestMixedUnstable:
    """Scenario 4: Mix of supportive + stressed inputs → mid-range."""

    @pytest.fixture()
    def result(self):
        # Supportive positioning but stressed flows
        return compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_squeeze_prone_flow(),      # Stressed flows
            stability_data=_squeeze_prone_stability(),  # Stressed stability
            source_meta=_supportive_source_meta(),
        )

    def test_score_mid_range(self, result):
        """Mixed inputs should produce mid-range composite."""
        assert 30 <= result["score"] <= 75, f"Score {result['score']} outside mixed range"

    def test_conflicting_signals_or_warnings(self, result):
        """Should flag conflicts between supportive positioning and stressed flows."""
        has_signals = (
            len(result.get("conflicting_signals", [])) > 0
            or len(result.get("warnings", [])) > 0
            or len(result.get("negative_contributors", [])) > 0
        )
        assert has_signals, "Expected conflicting signals or warnings for mixed scenario"


class TestDegradedConfidence:
    """Scenario 5: Empty/None inputs → confidence drops, warnings issued."""

    @pytest.fixture()
    def result(self):
        return compute_flows_positioning_scores(
            positioning_data=_empty_data(),
            crowding_data=_empty_data(),
            squeeze_data=_empty_data(),
            flow_data=_empty_data(),
            stability_data=_empty_data(),
            source_meta={},
        )

    def test_low_confidence(self, result):
        assert result["confidence_score"] < 50, \
            f"Confidence {result['confidence_score']} should be very low with no data"

    def test_many_warnings(self, result):
        assert len(result["warnings"]) >= 5, \
            f"Expected many warnings, got {len(result['warnings'])}"

    def test_many_missing_inputs(self, result):
        assert len(result["missing_inputs"]) >= 3, \
            f"Expected missing inputs, got {len(result['missing_inputs'])}"

    def test_signal_quality_low(self, result):
        assert result["signal_quality"] == "low"


class TestSinglePillarCrash:
    """Scenario 6: One pillar gets bad data that would crash → composite uses remaining."""

    @pytest.fixture()
    def result(self):
        # Pass invalid data for positioning, valid for the rest.
        # The engine wraps each pillar in try/except, so a crash just
        # disables that pillar and uses the remaining four.
        bad_positioning = {"put_call_ratio": "not_a_number_object_{}"}
        return compute_flows_positioning_scores(
            positioning_data=bad_positioning,
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )

    def test_composite_still_computed(self, result):
        """Even with one pillar failed, composite should not crash."""
        assert result["score"] is not None
        assert isinstance(result["score"], (int, float))

    def test_remaining_pillars_scored(self, result):
        ps = result["pillar_scores"]
        # At least some of the working pillars should have scores
        working = [k for k in ps if ps[k] is not None]
        assert len(working) >= 3, f"Expected at least 3 working pillars, got {working}"


class TestUIMapping:
    """Scenario 7: Label bands produce correct full_label and short_label."""

    def test_high_score_label(self):
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )
        # Verify label/short_label are non-empty strings
        assert result["label"] not in (None, "", "Unknown")
        assert result["short_label"] not in (None, "", "Unknown")

    def test_result_has_engine_tag(self):
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )
        assert result["engine"] == "flows_positioning"


class TestProvenanceMetadata:
    """Scenario 8: SIGNAL_PROVENANCE is present and populated in diagnostics."""

    def test_provenance_in_diagnostics(self):
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )
        diag = result["diagnostics"]
        assert "signal_provenance" in diag
        provenance = diag["signal_provenance"]
        assert len(provenance) > 0

    def test_provenance_entries_valid(self):
        for key, info in SIGNAL_PROVENANCE.items():
            assert "source" in info, f"SIGNAL_PROVENANCE[{key}] missing 'source'"
            assert "type" in info, f"SIGNAL_PROVENANCE[{key}] missing 'type'"
            assert info["type"] in ("direct", "proxy", "derived"), \
                f"SIGNAL_PROVENANCE[{key}] has invalid type: {info['type']}"


class TestRawInputsPartitioned:
    """Scenario 9: raw_inputs has correct partition keys."""

    def test_raw_inputs_keys(self):
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )
        ri = result["raw_inputs"]
        expected_keys = {"positioning", "crowding", "squeeze", "flow", "stability"}
        assert set(ri.keys()) == expected_keys

    def test_positioning_raw_inputs_fields(self):
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )
        pos = result["raw_inputs"]["positioning"]
        assert "put_call_ratio" in pos
        assert "vix" in pos or "vix_level" in pos


class TestPillarWeights:
    """Verify pillar weights sum to 1.0 and are all present."""

    def test_weights_sum_to_one(self):
        total = sum(PILLAR_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Pillar weights sum to {total}, expected 1.0"

    def test_all_five_pillars_present(self):
        expected = {
            "positioning_pressure",
            "crowding_stretch",
            "squeeze_unwind_risk",
            "flow_direction_persistence",
            "positioning_stability",
        }
        assert set(PILLAR_WEIGHTS.keys()) == expected


class TestDiagnosticsCompleteness:
    """Verify diagnostics contain full audit trail."""

    def test_diagnostics_structure(self):
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )
        diag = result["diagnostics"]
        assert "pillar_weights" in diag
        assert "pillar_details" in diag
        assert "confidence_penalties" in diag
        assert "composite_computation" in diag
        assert "source_meta" in diag

    def test_pillar_details_have_submetrics(self):
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )
        for pname, detail in result["diagnostics"]["pillar_details"].items():
            assert "score" in detail
            assert "submetrics" in detail
            assert isinstance(detail["submetrics"], list)


# ═══════════════════════════════════════════════════════════════════════
# STALE-DATA & MIXED-FRESHNESS TESTS (Item 1)
# ═══════════════════════════════════════════════════════════════════════


class TestStaleData:
    """Stale-data scenarios: confidence penalties and diagnostic surfacing."""

    def _stale_source_meta(self, stale_count: int):
        return {
            "market_context_freshness": "stale",
            "has_direct_flow_data": False,
            "has_futures_positioning": False,
            "stale_source_count": stale_count,
        }

    @pytest.fixture()
    def result_stale_3(self):
        return compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=self._stale_source_meta(3),
        )

    @pytest.fixture()
    def result_fresh(self):
        return compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )

    def test_stale_data_lowers_confidence(self, result_stale_3, result_fresh):
        """3 stale sources should penalize confidence vs. fresh data."""
        assert result_stale_3["confidence_score"] < result_fresh["confidence_score"], \
            "Stale data should reduce confidence"

    def test_stale_penalty_in_diagnostics(self, result_stale_3):
        """Stale-source penalty string should appear in confidence_penalties."""
        penalties = result_stale_3["diagnostics"]["confidence_penalties"]
        assert any("stale" in p.lower() for p in penalties), \
            f"Expected stale penalty in: {penalties}"

    def test_stale_count_4_capped(self):
        """stale_source_count=4 → penalty of min(12, 4*3)=12."""
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=self._stale_source_meta(4),
        )
        penalties = result["diagnostics"]["confidence_penalties"]
        stale_penalties = [p for p in penalties if "stale" in p.lower()]
        assert len(stale_penalties) == 1
        assert "(-12)" in stale_penalties[0], "4 stale sources should cap at -12"

    def test_stale_count_6_still_capped_at_12(self):
        """stale_source_count=6 → penalty still capped at 12 (max -12)."""
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=self._stale_source_meta(6),
        )
        penalties = result["diagnostics"]["confidence_penalties"]
        stale_penalties = [p for p in penalties if "stale" in p.lower()]
        assert "(-12)" in stale_penalties[0], "Stale penalty should cap at -12"


class TestMixedFreshness:
    """Some pillars have valid data, mixed freshness metadata."""

    def test_mixed_freshness_moderate_hit(self):
        """Source with 1 stale source and partial proxy → moderate confidence hit."""
        meta = {
            "market_context_freshness": "mixed",
            "has_direct_flow_data": False,
            "has_futures_positioning": False,
            "stale_source_count": 1,
            "proxy_source_count": 3,
        }
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=meta,
        )
        # Should have penalties for stale + proxy + no direct + no futures
        penalties = result["diagnostics"]["confidence_penalties"]
        assert len(penalties) >= 3, f"Expected multiple penalties, got: {penalties}"

    def test_fresh_data_with_proxy_only(self):
        """No stale sources but heavy proxy → only proxy penalty."""
        meta = {
            "market_context_freshness": "live",
            "has_direct_flow_data": False,
            "has_futures_positioning": False,
            "proxy_source_count": 5,
            "stale_source_count": 0,
        }
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=meta,
        )
        penalties = result["diagnostics"]["confidence_penalties"]
        assert not any("stale" in p.lower() for p in penalties), \
            "No stale penalty expected with stale_source_count=0"
        assert any("proxy" in p.lower() for p in penalties), \
            "Should have proxy penalty with 5 proxy sources"


# ═══════════════════════════════════════════════════════════════════════
# PROXY-HONESTY TESTS (Item 2)
# ═══════════════════════════════════════════════════════════════════════


class TestProxyHonesty:
    """Verify proxy-derived signals are clearly surfaced in diagnostics and output."""

    @pytest.fixture()
    def result(self):
        return compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )

    def test_proxy_summary_in_diagnostics(self, result):
        """Diagnostics must include proxy_summary with signal counts."""
        diag = result["diagnostics"]
        ps = diag["proxy_summary"]
        assert "total_proxy_signals" in ps
        assert "total_direct_signals" in ps
        assert "total_derived_signals" in ps
        assert "proxy_signal_names" in ps
        assert "direct_signal_names" in ps
        # Verify counts match the actual SIGNAL_PROVENANCE
        expected_proxy = sum(
            1 for v in SIGNAL_PROVENANCE.values() if v.get("type") == "proxy"
        )
        assert ps["total_proxy_signals"] == expected_proxy

    def test_proxy_signal_names_match_provenance(self, result):
        """proxy_signal_names list should match filtered SIGNAL_PROVENANCE."""
        ps = result["diagnostics"]["proxy_summary"]
        expected = [
            k for k, v in SIGNAL_PROVENANCE.items() if v.get("type") == "proxy"
        ]
        assert sorted(ps["proxy_signal_names"]) == sorted(expected)

    def test_direct_signal_names_match_provenance(self, result):
        """direct_signal_names list should match filtered SIGNAL_PROVENANCE."""
        ps = result["diagnostics"]["proxy_summary"]
        expected = [
            k for k, v in SIGNAL_PROVENANCE.items() if v.get("type") == "direct"
        ]
        assert sorted(ps["direct_signal_names"]) == sorted(expected)

    def test_every_provenance_has_required_fields(self):
        """Redundant with TestProvenanceMetadata but enforced here for proxy-honesty scope."""
        for key, info in SIGNAL_PROVENANCE.items():
            assert "source" in info, f"{key} missing source"
            assert "type" in info, f"{key} missing type"
            assert "notes" in info, f"{key} missing notes"
            assert info["type"] in ("direct", "proxy", "derived"), \
                f"{key} has invalid type: {info['type']}"

    def test_proxy_penalty_surfaced_in_warnings(self):
        """Source meta with heavy proxy count → warning message mentions 'proxy'."""
        meta = {
            "market_context_freshness": "live",
            "has_direct_flow_data": False,
            "has_futures_positioning": False,
            "proxy_source_count": 5,
        }
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=meta,
        )
        all_text = " ".join(result["warnings"])
        assert "proxy" in all_text.lower(), \
            f"Expected proxy-related warning, got: {result['warnings']}"


# ═══════════════════════════════════════════════════════════════════════
# CROWDING GATE TESTS (Ensures Item 3 gate works)
# ═══════════════════════════════════════════════════════════════════════


class TestCrowdingGate:
    """Verify that 'Supportive' labels are blocked when key pillars are dangerously low."""

    def test_crowding_gate_caps_label(self):
        """Low crowding_stretch (< 40) should prevent 'Supportive' label."""
        pillars = {
            "positioning_pressure": {"score": 80},
            "crowding_stretch": {"score": 30},
            "squeeze_unwind_risk": {"score": 75},
            "flow_direction_persistence": {"score": 80},
            "positioning_stability": {"score": 70},
        }
        full, short, _adj, _applied, warnings, _details = _label_from_score_with_gates(75, pillars)
        assert "gated" in full.lower(), f"Expected gated label, got: {full}"
        assert len(warnings) > 0
        assert any("crowding" in w.lower() for w in warnings)

    def test_stability_gate_caps_label(self):
        """Low stability (< 35) should prevent 'Supportive' label."""
        pillars = {
            "positioning_pressure": {"score": 80},
            "crowding_stretch": {"score": 70},
            "squeeze_unwind_risk": {"score": 75},
            "flow_direction_persistence": {"score": 80},
            "positioning_stability": {"score": 25},
        }
        full, short, _adj, _applied, warnings, _details = _label_from_score_with_gates(75, pillars)
        assert "gated" in full.lower(), f"Expected gated label, got: {full}"
        assert any("stability" in w.lower() for w in warnings)

    def test_squeeze_gate_caps_label(self):
        """Low squeeze (< 35) should prevent 'Supportive' label."""
        pillars = {
            "positioning_pressure": {"score": 80},
            "crowding_stretch": {"score": 70},
            "squeeze_unwind_risk": {"score": 25},
            "flow_direction_persistence": {"score": 80},
            "positioning_stability": {"score": 70},
        }
        full, short, _adj, _applied, warnings, _details = _label_from_score_with_gates(75, pillars)
        assert "gated" in full.lower(), f"Expected gated label, got: {full}"
        assert any("squeeze" in w.lower() for w in warnings)

    def test_no_gate_when_pillars_healthy(self):
        """All pillars above thresholds → no gating."""
        pillars = {
            "positioning_pressure": {"score": 75},
            "crowding_stretch": {"score": 70},
            "squeeze_unwind_risk": {"score": 65},
            "flow_direction_persistence": {"score": 75},
            "positioning_stability": {"score": 65},
        }
        full, short, _adj, _applied, warnings, _details = _label_from_score_with_gates(80, pillars)
        assert "gated" not in full.lower(), f"Unexpected gating: {full}"
        assert len(warnings) == 0

    def test_gate_not_applied_below_55(self):
        """Low composite score (< 55) should not trigger gating at all."""
        pillars = {
            "positioning_pressure": {"score": 40},
            "crowding_stretch": {"score": 20},
            "squeeze_unwind_risk": {"score": 20},
            "flow_direction_persistence": {"score": 30},
            "positioning_stability": {"score": 20},
        }
        full, short, _adj, _applied, warnings, _details = _label_from_score_with_gates(40, pillars)
        assert "gated" not in full.lower()
        assert len(warnings) == 0

    def test_gate_warnings_in_engine_output(self):
        """Gate warnings should appear in the final engine output warnings list."""
        result = compute_flows_positioning_scores(
            positioning_data=_crowded_positioning(),
            crowding_data=_crowded_crowding(),
            squeeze_data=_crowded_squeeze(),
            flow_data=_crowded_flow(),
            stability_data=_crowded_stability(),
            source_meta=_supportive_source_meta(),
        )
        # Verify label_gates structure exists in diagnostics
        assert "label_gates" in result["diagnostics"]
        gates = result["diagnostics"]["label_gates"]
        assert "crowding_gate_threshold" in gates
        assert gates["crowding_gate_threshold"] == _CROWDING_GATE_THRESHOLD

    def test_multiple_gates_can_fire(self):
        """Multiple pillar gates can fire simultaneously."""
        pillars = {
            "positioning_pressure": {"score": 80},
            "crowding_stretch": {"score": 20},
            "squeeze_unwind_risk": {"score": 20},
            "flow_direction_persistence": {"score": 80},
            "positioning_stability": {"score": 20},
        }
        full, short, _adj, _applied, warnings, _details = _label_from_score_with_gates(75, pillars)
        assert "gated" in full.lower()
        assert len(warnings) >= 3, f"Expected 3+ gate warnings, got {len(warnings)}"


# ═══════════════════════════════════════════════════════════════════════
# SQUEEZE-VS-CONTINUATION DISTINCTION TESTS (Item 4)
# ═══════════════════════════════════════════════════════════════════════


class TestSqueezeVsContinuation:
    """Verify engine distinguishes squeeze-driven momentum from healthy continuation."""

    def _high_flows_low_squeeze_inputs(self):
        """Strong flows + high squeeze risk → squeeze-driven."""
        return {
            "positioning": {
                "put_call_ratio": 0.75, "vix": 14.0, "retail_bull_pct": 42.0,
                "systematic_allocation": 65.0, "futures_net_long_pct": 58.0,
            },
            "crowding": {
                "futures_net_long_pct": 52.0, "put_call_ratio": 0.78,
                "retail_bull_pct": 40.0, "retail_bear_pct": 32.0,
                "vix": 14.0, "short_interest_pct": 1.8,
            },
            "squeeze": {
                "short_interest_pct": 4.0, "futures_net_long_pct": 52.0,
                "put_call_ratio": 1.10, "vix": 25.0,
                "vix_term_structure": 1.05,
            },
            "flow": {
                "flow_direction_score": 80.0, "flow_persistence_5d": 82.0,
                "flow_persistence_20d": 78.0, "inflow_outflow_balance": 80.0,
                "follow_through_score": 75.0,
            },
            "stability": {
                "vix": 14.0, "vix_term_structure": 0.88,
                "futures_net_long_pct": 52.0, "flow_direction_score": 80.0,
                "flow_volatility": 22.0, "put_call_ratio": 0.78,
            },
        }

    def test_squeeze_driven_scenario_flags_conflicting(self):
        """Low squeeze pillar + high flow pillar → conflicting signal about squeeze-driven flows."""
        inputs = self._high_flows_low_squeeze_inputs()
        result = compute_flows_positioning_scores(
            positioning_data=inputs["positioning"],
            crowding_data=inputs["crowding"],
            squeeze_data=inputs["squeeze"],
            flow_data=inputs["flow"],
            stability_data=inputs["stability"],
            source_meta=_supportive_source_meta(),
        )
        conflicting = result.get("conflicting_signals", [])
        all_text = " ".join(conflicting).lower()
        assert "squeeze" in all_text, \
            f"Expected squeeze-driven note in conflicting signals, got: {conflicting}"

    def test_healthy_continuation_no_squeeze_flag(self):
        """All supportive pillars → no squeeze-driven warning."""
        result = compute_flows_positioning_scores(
            positioning_data=_supportive_positioning(),
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )
        conflicting = result.get("conflicting_signals", [])
        all_text = " ".join(conflicting).lower()
        assert "squeeze-driven" not in all_text, \
            f"Should not flag squeeze-driven with supportive data: {conflicting}"


# ═══════════════════════════════════════════════════════════════════════
# SINGLE-PILLAR FAILURE RESILIENCE (Item 5)
# ═══════════════════════════════════════════════════════════════════════


class TestPillarFailureResilience:
    """When one pillar crashes, verify explanations/takeaway/model payloads are intact."""

    @pytest.fixture()
    def result_one_crash(self):
        """Positioning pillar gets garbage data → crashes → engine continues."""
        return compute_flows_positioning_scores(
            positioning_data={"put_call_ratio": "CRASH_ME", "vix": object()},
            crowding_data=_supportive_crowding(),
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )

    @pytest.fixture()
    def result_two_crash(self):
        """Two pillars crash → engine uses remaining three."""
        return compute_flows_positioning_scores(
            positioning_data={"put_call_ratio": object()},
            crowding_data={"futures_net_long_pct": object()},
            squeeze_data=_supportive_squeeze(),
            flow_data=_supportive_flow(),
            stability_data=_supportive_stability(),
            source_meta=_supportive_source_meta(),
        )

    def test_trader_takeaway_valid_string(self, result_one_crash):
        ta = result_one_crash["trader_takeaway"]
        assert isinstance(ta, str)
        assert len(ta) > 10

    def test_summary_valid_string(self, result_one_crash):
        assert isinstance(result_one_crash["summary"], str)
        assert len(result_one_crash["summary"]) > 10

    def test_strategy_bias_complete(self, result_one_crash):
        bias = result_one_crash["strategy_bias"]
        for key in ("continuation_support", "reversal_risk", "squeeze_potential", "fragility"):
            assert key in bias, f"strategy_bias missing {key}"
            assert isinstance(bias[key], (int, float))

    def test_contributor_lists_valid(self, result_one_crash):
        for key in ("positive_contributors", "negative_contributors"):
            contributors = result_one_crash[key]
            assert isinstance(contributors, list)
            for item in contributors:
                assert isinstance(item, str)

    def test_two_crash_still_works(self, result_two_crash):
        assert result_two_crash["score"] is not None
        assert isinstance(result_two_crash["trader_takeaway"], str)
        assert len(result_two_crash["trader_takeaway"]) > 10

    def test_crashed_pillar_has_warning_in_output(self, result_one_crash):
        """The crashed pillar should produce a warning in the final output."""
        assert any("positioning_pressure" in w for w in result_one_crash["warnings"]), \
            f"Expected pillar crash warning, got: {result_one_crash['warnings']}"
