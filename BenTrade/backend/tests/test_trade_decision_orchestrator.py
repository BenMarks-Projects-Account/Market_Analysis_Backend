"""Tests for Trade Decision Orchestrator v1.1.

Covers:
- Contract shape
- Complete / partial / insufficient-data status
- Section preservation (including assembled)
- Quality overview
- Warning flags
- Evidence (events integration, model context details, assembled degradation)
- Metadata & upstream versions & component roles
- Summary generation
- Degraded / fallback scenarios
- Source semantics honesty (events, assembled, model)
- Integration-level proof paths
"""

from __future__ import annotations

import pytest

from app.services.trade_decision_orchestrator import build_decision_packet

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures — realistic upstream subsystem outputs
# ═══════════════════════════════════════════════════════════════════════════

def _make_candidate(**overrides: object) -> dict:
    base = {
        "candidate_id": "SPY_credit_spread_1",
        "scanner_key": "credit_spreads",
        "scanner_name": "Credit Spread Scanner",
        "strategy_family": "credit_spread",
        "setup_type": "bull_put_spread",
        "asset_class": "equity_option",
        "symbol": "SPY",
        "underlying": {"symbol": "SPY", "price": 510.25},
        "direction": "bullish",
        "thesis_summary": "Sell OTM put spread on SPY.",
        "entry_context": {"short_strike": 500, "long_strike": 495, "width": 5},
        "time_horizon": {"label": "short_term", "dte": 14},
        "setup_quality": "good",
        "confidence": 0.72,
        "risk_definition": {"type": "defined", "max_loss_per_contract": 500},
        "reward_profile": {"type": "credit", "max_gain_per_contract": 85},
        "supporting_signals": ["trend_up", "iv_rank_moderate"],
        "risk_flags": [],
        "invalidation_signals": [],
        "market_context_tags": ["bullish_momentum"],
        "position_sizing_notes": "Standard 1-lot.",
        "data_quality": {"source": "tradier", "source_confidence": "high", "missing_fields": []},
        "source_status": "live",
        "pricing_snapshot": {"bid": 0.85, "ask": 0.90, "mid": 0.875},
        "strategy_structure": {"legs": 2},
        "candidate_metrics": {"ev_per_contract": 28.5, "pop": 0.78},
        "detail_sections": {},
        "generated_at": "2026-03-10T12:00:00Z",
    }
    base.update(overrides)
    return base


def _make_market(**overrides: object) -> dict:
    base = {
        "composite_version": "1.0",
        "computed_at": "2026-03-10T12:00:00Z",
        "status": "ok",
        "market_state": "bullish_leaning",
        "support_state": "moderate",
        "stability_state": "stable",
        "confidence": 0.68,
        "evidence": {"market_state": {}, "support_state": {}, "stability_state": {}},
        "adjustments": {"conflict_adjustment": None, "quality_adjustment": None, "horizon_adjustment": None},
        "summary": "Market is bullish-leaning with moderate support.",
        "metadata": {
            "composite_version": "1.0",
            "engines_used": 5,
            "conflict_count": 0,
            "conflict_severity": "none",
            "overall_quality": "good",
            "overall_freshness": "fresh",
            "horizon_span": "short_term",
        },
    }
    base.update(overrides)
    return base


def _make_conflicts(**overrides: object) -> dict:
    base = {
        "status": "clean",
        "detected_at": "2026-03-10T12:00:00Z",
        "conflict_count": 0,
        "conflict_severity": "none",
        "conflict_summary": "No conflicts detected.",
        "conflict_flags": [],
        "market_conflicts": [],
        "candidate_conflicts": [],
        "model_conflicts": [],
        "time_horizon_conflicts": [],
        "quality_conflicts": [],
        "metadata": {
            "detector_version": "1.0",
            "engines_inspected": 5,
            "candidates_inspected": 1,
            "models_inspected": 0,
            "degraded_inputs": 0,
        },
    }
    base.update(overrides)
    return base


def _make_portfolio(**overrides: object) -> dict:
    base = {
        "portfolio_version": "1.0",
        "generated_at": "2026-03-10T12:00:00Z",
        "status": "ok",
        "position_count": 3,
        "underlying_count": 2,
        "portfolio_summary": {"description": "Moderate portfolio.", "risk_level": "moderate"},
        "directional_exposure": {"net_delta": 0.2},
        "underlying_concentration": {},
        "sector_concentration": {},
        "strategy_concentration": {},
        "expiration_concentration": {},
        "capital_at_risk": {"total": 1500},
        "greeks_exposure": {},
        "event_exposure": {},
        "correlation_exposure": {},
        "risk_flags": [],
        "warning_flags": [],
        "evidence": {"position_count": 3, "underlying_count": 2, "symbols": ["SPY", "QQQ"], "has_account_equity": True},
        "metadata": {"portfolio_version": "1.0", "position_count": 3, "underlying_count": 2, "account_equity_provided": True, "greeks_coverage": "full", "sector_coverage": "full", "event_coverage": "none"},
    }
    base.update(overrides)
    return base


def _make_policy(**overrides: object) -> dict:
    base = {
        "policy_version": "1.0",
        "evaluated_at": "2026-03-10T12:00:00Z",
        "status": "evaluated",
        "policy_decision": "allow",
        "decision_severity": "none",
        "summary": "Trade passes all policy checks.",
        "triggered_checks": [],
        "blocking_checks": [],
        "caution_checks": [],
        "restrictive_checks": [],
        "size_guidance": "normal",
        "eligibility_flags": ["clean_evaluation", "eligible"],
        "warning_flags": [],
        "evidence": {
            "candidate_symbol": "SPY",
            "candidate_strategy": "credit_spread",
            "market_status": "ok",
            "market_state": "bullish_leaning",
            "conflict_severity": "none",
            "portfolio_status": "ok",
            "checks_triggered": 0,
            "blocking_count": 0,
            "restrictive_count": 0,
            "caution_count": 0,
        },
        "metadata": {
            "policy_version": "1.0",
            "candidate_provided": True,
            "market_provided": True,
            "conflicts_provided": True,
            "portfolio_provided": True,
            "checks_evaluated": 12,
        },
    }
    base.update(overrides)
    return base


def _make_events(**overrides: object) -> dict:
    base = {
        "event_context_version": "1.0",
        "generated_at": "2026-03-10T12:00:00Z",
        "status": "ok",
        "summary": "No significant events nearby.",
        "event_risk_state": "quiet",
        "upcoming_macro_events": [],
        "upcoming_company_events": [],
        "candidate_event_overlap": {"candidate_symbol": "SPY", "overlapping_events": [], "overlap_count": 0},
        "portfolio_event_overlap": {"positions_with_overlap": 0, "symbols_with_overlap": [], "overlapping_events": [], "event_cluster_count": 0},
        "event_windows": {"within_24h": [], "within_3d": [], "within_7d": [], "beyond_7d": []},
        "risk_flags": [],
        "warning_flags": [],
        "evidence": {"macro_event_count": 0, "company_event_count": 0, "high_importance_count": 0, "within_24h_count": 0, "within_3d_count": 0, "candidate_overlap_count": 0, "portfolio_overlap_count": 0},
        "metadata": {"event_context_version": "1.0", "macro_coverage": "empty", "company_event_coverage": "empty", "candidate_provided": True, "positions_provided": False, "reference_time": "2026-03-10T12:00:00Z", "total_events_processed": 0},
    }
    base.update(overrides)
    return base


def _make_model_analysis(**overrides: object) -> dict:
    base = {
        "status": "success",
        "analysis_type": "technical",
        "analysis_name": "Technical Analysis",
        "category": "technical",
        "model_source": "openai",
        "requested_at": "2026-03-10T12:00:00Z",
        "completed_at": "2026-03-10T12:00:02Z",
        "duration_ms": 2000,
        "raw_content": "...",
        "normalized_text": "Bullish trend confirmed.",
        "structured_payload": {"trend": "bullish"},
        "summary": "Bullish technical outlook.",
        "key_points": ["Trend up", "Support holding"],
        "risks": ["Resistance at 515"],
        "actions": ["Hold position"],
        "confidence": 0.75,
        "warnings": [],
        "error_type": None,
        "error_message": None,
        "parse_strategy": "direct",
        "response_format": "json",
        "time_horizon": "short_term",
        "metadata": {"trace": {}, "label": None, "score": None},
    }
    base.update(overrides)
    return base


def _make_assembled(**overrides: object) -> dict:
    base = {
        "context_version": "1.0",
        "assembled_at": "2026-03-10T12:00:00Z",
        "assembly_status": "complete",
        "assembly_warnings": [],
        "included_modules": ["finnhub", "yahoo"],
        "missing_modules": [],
        "degraded_modules": [],
        "market_context": {},
        "candidate_context": {"candidates": [], "count": 0, "scanners": [], "families": []},
        "model_context": {"analyses": {}, "count": 0},
        "quality_summary": {"overall_quality": "good"},
        "freshness_summary": {"overall_freshness": "fresh"},
        "horizon_summary": {"shortest": "short_term", "longest": "short_term"},
        "metadata": {"context_version": "1.0", "assembled_at": "2026-03-10T12:00:00Z", "market_module_count": 2, "candidate_count": 0, "model_count": 0, "assembly_status": "complete"},
    }
    base.update(overrides)
    return base


def _full_inputs() -> dict:
    return {
        "candidate": _make_candidate(),
        "market": _make_market(),
        "conflicts": _make_conflicts(),
        "portfolio": _make_portfolio(),
        "policy": _make_policy(),
        "events": _make_events(),
        "model_context": _make_model_analysis(),
        "assembled": _make_assembled(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 1. Contract shape tests
# ═══════════════════════════════════════════════════════════════════════════

class TestContractShape:
    """Decision packet returns the expected top-level shape."""

    EXPECTED_KEYS = {
        "decision_packet_version",
        "generated_at",
        "status",
        "summary",
        "candidate",
        "market",
        "portfolio",
        "policy",
        "events",
        "conflicts",
        "model_context",
        "assembled",
        "quality_overview",
        "warning_flags",
        "evidence",
        "metadata",
    }

    def test_full_input_shape(self):
        pkt = build_decision_packet(**_full_inputs())
        assert set(pkt.keys()) == self.EXPECTED_KEYS

    def test_empty_input_shape(self):
        pkt = build_decision_packet()
        assert set(pkt.keys()) == self.EXPECTED_KEYS

    def test_version_is_string(self):
        pkt = build_decision_packet(**_full_inputs())
        assert pkt["decision_packet_version"] == "1.1"

    def test_generated_at_is_iso_string(self):
        pkt = build_decision_packet(**_full_inputs())
        assert isinstance(pkt["generated_at"], str)
        assert "T" in pkt["generated_at"]

    def test_status_is_string(self):
        pkt = build_decision_packet(**_full_inputs())
        assert isinstance(pkt["status"], str)
        assert pkt["status"] in ("complete", "partial", "insufficient_data")

    def test_summary_is_string(self):
        pkt = build_decision_packet(**_full_inputs())
        assert isinstance(pkt["summary"], str)
        assert len(pkt["summary"]) > 0

    def test_warning_flags_is_list(self):
        pkt = build_decision_packet(**_full_inputs())
        assert isinstance(pkt["warning_flags"], list)

    def test_evidence_is_dict(self):
        pkt = build_decision_packet(**_full_inputs())
        assert isinstance(pkt["evidence"], dict)

    def test_metadata_is_dict(self):
        pkt = build_decision_packet(**_full_inputs())
        assert isinstance(pkt["metadata"], dict)

    def test_quality_overview_is_dict(self):
        pkt = build_decision_packet(**_full_inputs())
        assert isinstance(pkt["quality_overview"], dict)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Assembly status tests
# ═══════════════════════════════════════════════════════════════════════════

class TestAssemblyStatus:
    """Complete, partial, and insufficient_data are derived correctly."""

    def test_full_input_is_complete(self):
        pkt = build_decision_packet(**_full_inputs())
        assert pkt["status"] == "complete"

    def test_missing_candidate_is_insufficient(self):
        inputs = _full_inputs()
        del inputs["candidate"]
        pkt = build_decision_packet(**inputs)
        assert pkt["status"] == "insufficient_data"

    def test_candidate_none_is_insufficient(self):
        inputs = _full_inputs()
        inputs["candidate"] = None
        pkt = build_decision_packet(**inputs)
        assert pkt["status"] == "insufficient_data"

    def test_empty_dict_candidate_is_insufficient(self):
        inputs = _full_inputs()
        inputs["candidate"] = {}
        pkt = build_decision_packet(**inputs)
        assert pkt["status"] == "insufficient_data"

    def test_missing_market_is_partial(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            policy=_make_policy(),
        )
        assert pkt["status"] == "partial"

    def test_missing_policy_is_partial(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
        )
        assert pkt["status"] == "partial"

    def test_candidate_market_policy_only_is_complete(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
        )
        assert pkt["status"] == "complete"

    def test_missing_portfolio_still_complete(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            events=_make_events(),
        )
        assert pkt["status"] == "complete"

    def test_missing_events_still_complete(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            portfolio=_make_portfolio(),
        )
        assert pkt["status"] == "complete"

    def test_missing_model_still_complete(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            conflicts=_make_conflicts(),
        )
        assert pkt["status"] == "complete"

    def test_candidate_only_is_partial(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        assert pkt["status"] == "partial"

    def test_no_inputs_is_insufficient(self):
        pkt = build_decision_packet()
        assert pkt["status"] == "insufficient_data"

    def test_market_error_status_makes_partial(self):
        """Market with insufficient_data upstream status → required errored → partial."""
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="insufficient_data"),
            policy=_make_policy(),
        )
        assert pkt["status"] == "partial"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Section preservation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSectionPreservation:
    """Sections preserve upstream structure without fabrication."""

    def test_candidate_section_preserved(self):
        cand = _make_candidate()
        pkt = build_decision_packet(candidate=cand)
        assert pkt["candidate"] is not None
        assert pkt["candidate"]["symbol"] == "SPY"
        assert pkt["candidate"]["strategy_family"] == "credit_spread"

    def test_market_section_preserved(self):
        mkt = _make_market()
        pkt = build_decision_packet(candidate=_make_candidate(), market=mkt)
        assert pkt["market"] is not None
        assert pkt["market"]["composite_version"] == "1.0"
        assert pkt["market"]["market_state"] == "bullish_leaning"

    def test_conflicts_section_preserved(self):
        conf = _make_conflicts()
        pkt = build_decision_packet(candidate=_make_candidate(), conflicts=conf)
        assert pkt["conflicts"] is not None
        assert pkt["conflicts"]["conflict_count"] == 0

    def test_portfolio_section_preserved(self):
        port = _make_portfolio()
        pkt = build_decision_packet(candidate=_make_candidate(), portfolio=port)
        assert pkt["portfolio"] is not None
        assert pkt["portfolio"]["position_count"] == 3

    def test_policy_section_preserved(self):
        pol = _make_policy()
        pkt = build_decision_packet(candidate=_make_candidate(), policy=pol)
        assert pkt["policy"] is not None
        assert pkt["policy"]["policy_decision"] == "allow"

    def test_events_section_preserved(self):
        evts = _make_events()
        pkt = build_decision_packet(candidate=_make_candidate(), events=evts)
        assert pkt["events"] is not None
        assert pkt["events"]["event_risk_state"] == "quiet"

    def test_model_dict_preserved(self):
        model = _make_model_analysis()
        pkt = build_decision_packet(candidate=_make_candidate(), model_context=model)
        assert pkt["model_context"] is not None
        assert pkt["model_context"]["status"] == "success"

    def test_model_list_preserved(self):
        models = [_make_model_analysis(), _make_model_analysis(analysis_type="sentiment")]
        pkt = build_decision_packet(candidate=_make_candidate(), model_context=models)
        assert isinstance(pkt["model_context"], list)
        assert len(pkt["model_context"]) == 2

    def test_missing_section_is_none(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        assert pkt["market"] is None
        assert pkt["conflicts"] is None
        assert pkt["portfolio"] is None
        assert pkt["events"] is None
        assert pkt["model_context"] is None
        assert pkt["assembled"] is None

    def test_sections_are_copies(self):
        """Sections should be shallow copies, not references to inputs."""
        cand = _make_candidate()
        pkt = build_decision_packet(candidate=cand)
        assert pkt["candidate"] is not cand

    def test_no_fabricated_sections(self):
        """Missing inputs must not produce fabricated sections."""
        pkt = build_decision_packet(candidate=_make_candidate())
        for key in ("market", "conflicts", "portfolio", "events", "model_context", "assembled"):
            assert pkt[key] is None, f"{key} should be None when not provided"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Quality overview tests
# ═══════════════════════════════════════════════════════════════════════════

class TestQualityOverview:
    """Quality overview tracks subsystem presence and readiness."""

    QO_KEYS = {
        "packet_status",
        "decision_ready",
        "readiness_note",
        "subsystems_present",
        "subsystems_missing",
        "subsystems_degraded",
        "present_count",
        "total_subsystems",
        "coverage_ratio",
        "warning_count",
        "confidence_assessment",
        "uncertainty_summary",
    }

    def test_quality_overview_shape(self):
        pkt = build_decision_packet(**_full_inputs())
        qo = pkt["quality_overview"]
        assert set(qo.keys()) == self.QO_KEYS

    def test_complete_packet_is_decision_ready(self):
        pkt = build_decision_packet(**_full_inputs())
        qo = pkt["quality_overview"]
        assert qo["decision_ready"] is True
        assert qo["packet_status"] == "complete"

    def test_partial_packet_not_decision_ready(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        qo = pkt["quality_overview"]
        assert qo["decision_ready"] is False
        assert qo["packet_status"] == "partial"

    def test_insufficient_packet_not_decision_ready(self):
        pkt = build_decision_packet()
        qo = pkt["quality_overview"]
        assert qo["decision_ready"] is False
        assert qo["packet_status"] == "insufficient_data"

    def test_all_subsystems_present_count(self):
        pkt = build_decision_packet(**_full_inputs())
        qo = pkt["quality_overview"]
        assert qo["present_count"] == 8
        assert qo["total_subsystems"] == 8
        assert qo["coverage_ratio"] == 1.0

    def test_partial_coverage_ratio(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
        )
        qo = pkt["quality_overview"]
        assert qo["present_count"] == 3
        assert qo["coverage_ratio"] == round(3 / 8, 2)

    def test_subsystems_missing_tracked(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
        )
        qo = pkt["quality_overview"]
        assert "portfolio" in qo["subsystems_missing"]
        assert "events" in qo["subsystems_missing"]

    def test_subsystems_present_tracked(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
        )
        qo = pkt["quality_overview"]
        assert "candidate" in qo["subsystems_present"]
        assert "market" in qo["subsystems_present"]
        assert "policy" in qo["subsystems_present"]

    def test_degraded_subsystem_tracked(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="degraded"),
            policy=_make_policy(),
        )
        qo = pkt["quality_overview"]
        assert "market" in qo["subsystems_degraded"]

    def test_readiness_note_mentions_degraded(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="degraded"),
            policy=_make_policy(),
        )
        qo = pkt["quality_overview"]
        assert "degraded" in qo["readiness_note"].lower()

    def test_readiness_note_mentions_missing(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        qo = pkt["quality_overview"]
        assert "missing" in qo["readiness_note"].lower() or "partial" in qo["readiness_note"].lower()


# ═══════════════════════════════════════════════════════════════════════════
# 5. Warning flags tests
# ═══════════════════════════════════════════════════════════════════════════

class TestWarningFlags:
    """Warning flags reflect missing/degraded inputs and high-value signals."""

    def test_no_warnings_when_all_healthy(self):
        pkt = build_decision_packet(**_full_inputs())
        assert pkt["warning_flags"] == []

    def test_missing_subsystem_flags(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        wf = pkt["warning_flags"]
        assert "market_not_provided" in wf
        assert "policy_not_provided" in wf
        assert "portfolio_not_provided" in wf
        assert "events_not_provided" in wf

    def test_degraded_market_flag(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="degraded"),
            policy=_make_policy(),
        )
        assert "market_composite_degraded" in pkt["warning_flags"]

    def test_insufficient_market_flag(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="insufficient_data"),
            policy=_make_policy(),
        )
        assert "market_composite_insufficient" in pkt["warning_flags"]

    def test_policy_block_flag(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(policy_decision="block"),
        )
        assert "policy_blocks_trade" in pkt["warning_flags"]

    def test_policy_restrict_flag(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(policy_decision="restrict"),
        )
        assert "policy_restricts_trade" in pkt["warning_flags"]

    def test_event_crowded_flag(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            events=_make_events(event_risk_state="crowded"),
        )
        assert "event_calendar_crowded" in pkt["warning_flags"]

    def test_event_elevated_flag(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            events=_make_events(event_risk_state="elevated"),
        )
        assert "event_calendar_elevated" in pkt["warning_flags"]

    def test_no_duplicate_flags(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="degraded"),
        )
        wf = pkt["warning_flags"]
        assert len(wf) == len(set(wf))

    def test_candidate_missing_warning(self):
        pkt = build_decision_packet()
        assert "candidate_not_provided" in pkt["warning_flags"]

    def test_model_degraded_flag(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            model_context=_make_model_analysis(status="degraded"),
        )
        assert "model_context_degraded" in pkt["warning_flags"]


# ═══════════════════════════════════════════════════════════════════════════
# 6. Evidence tests
# ═══════════════════════════════════════════════════════════════════════════

class TestEvidence:
    """Evidence provides quick-reference fields for downstream use."""

    EVIDENCE_KEYS = {
        "candidate_symbol",
        "candidate_strategy",
        "candidate_direction",
        "candidate_confidence",
        "market_status",
        "market_state",
        "market_confidence",
        "policy_decision",
        "policy_severity",
        "policy_size_guidance",
        "event_risk_state",
        "event_status",
        "events_integration_status",
        "conflict_severity",
        "conflict_count",
        "portfolio_status",
        "portfolio_position_count",
        "model_context_count",
        "model_context_types",
        "assembled_degraded_modules",
        "section_statuses",
        "sections_present",
        "sections_total",
    }

    def test_evidence_shape(self):
        pkt = build_decision_packet(**_full_inputs())
        assert set(pkt["evidence"].keys()) == self.EVIDENCE_KEYS

    def test_evidence_candidate_fields(self):
        pkt = build_decision_packet(**_full_inputs())
        ev = pkt["evidence"]
        assert ev["candidate_symbol"] == "SPY"
        assert ev["candidate_strategy"] == "credit_spread"
        assert ev["candidate_direction"] == "bullish"
        assert ev["candidate_confidence"] == 0.72

    def test_evidence_market_fields(self):
        pkt = build_decision_packet(**_full_inputs())
        ev = pkt["evidence"]
        assert ev["market_status"] == "ok"
        assert ev["market_state"] == "bullish_leaning"
        assert ev["market_confidence"] == 0.68

    def test_evidence_policy_fields(self):
        pkt = build_decision_packet(**_full_inputs())
        ev = pkt["evidence"]
        assert ev["policy_decision"] == "allow"
        assert ev["policy_severity"] == "none"
        assert ev["policy_size_guidance"] == "normal"

    def test_evidence_event_fields(self):
        pkt = build_decision_packet(**_full_inputs())
        ev = pkt["evidence"]
        assert ev["event_risk_state"] == "quiet"
        assert ev["event_status"] == "ok"

    def test_evidence_conflict_fields(self):
        pkt = build_decision_packet(**_full_inputs())
        ev = pkt["evidence"]
        assert ev["conflict_severity"] == "none"
        assert ev["conflict_count"] == 0

    def test_evidence_portfolio_fields(self):
        pkt = build_decision_packet(**_full_inputs())
        ev = pkt["evidence"]
        assert ev["portfolio_status"] == "ok"
        assert ev["portfolio_position_count"] == 3

    def test_evidence_section_counts(self):
        pkt = build_decision_packet(**_full_inputs())
        ev = pkt["evidence"]
        assert ev["sections_present"] == 8
        assert ev["sections_total"] == 8

    def test_evidence_none_when_missing(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        ev = pkt["evidence"]
        assert ev["market_status"] is None
        assert ev["policy_decision"] is None
        assert ev["event_risk_state"] is None
        assert ev["conflict_severity"] is None
        assert ev["portfolio_status"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 7. Metadata tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMetadata:
    """Metadata tracks what was provided and upstream versions."""

    def test_metadata_shape(self):
        pkt = build_decision_packet(**_full_inputs())
        md = pkt["metadata"]
        assert md["decision_packet_version"] == "1.1"
        assert isinstance(md["generated_at"], str)
        assert "upstream_versions" in md
        assert "component_roles" in md

    def test_metadata_provided_flags_all_true(self):
        pkt = build_decision_packet(**_full_inputs())
        md = pkt["metadata"]
        assert md["candidate_provided"] is True
        assert md["market_provided"] is True
        assert md["conflicts_provided"] is True
        assert md["portfolio_provided"] is True
        assert md["policy_provided"] is True
        assert md["events_provided"] is True
        assert md["model_context_provided"] is True
        assert md["assembled_provided"] is True

    def test_metadata_provided_flags_partial(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        md = pkt["metadata"]
        assert md["candidate_provided"] is True
        assert md["market_provided"] is False
        assert md["portfolio_provided"] is False

    def test_upstream_versions_collected(self):
        pkt = build_decision_packet(**_full_inputs())
        uv = pkt["metadata"]["upstream_versions"]
        assert uv.get("market") == "1.0"
        assert uv.get("portfolio") == "1.0"
        assert uv.get("policy") == "1.0"
        assert uv.get("events") == "1.0"
        assert uv.get("assembled") == "1.0"

    def test_upstream_versions_empty_when_missing(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        uv = pkt["metadata"]["upstream_versions"]
        assert uv == {}

    def test_conflict_version_from_metadata(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            conflicts=_make_conflicts(),
        )
        uv = pkt["metadata"]["upstream_versions"]
        assert uv.get("conflicts") == "1.0"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Summary tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSummary:
    """Summary is a readable text string with key decision context."""

    def test_complete_summary_mentions_complete(self):
        pkt = build_decision_packet(**_full_inputs())
        assert "complete" in pkt["summary"].lower()

    def test_partial_summary_mentions_partial(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        assert "partial" in pkt["summary"].lower()

    def test_insufficient_summary_mentions_insufficient(self):
        pkt = build_decision_packet()
        assert "insufficient" in pkt["summary"].lower()

    def test_summary_includes_candidate_symbol(self):
        pkt = build_decision_packet(**_full_inputs())
        assert "SPY" in pkt["summary"]

    def test_summary_includes_market_state(self):
        pkt = build_decision_packet(**_full_inputs())
        assert "bullish_leaning" in pkt["summary"]

    def test_summary_includes_policy_decision(self):
        pkt = build_decision_packet(**_full_inputs())
        assert "allow" in pkt["summary"]

    def test_summary_includes_event_risk(self):
        pkt = build_decision_packet(**_full_inputs())
        assert "quiet" in pkt["summary"]

    def test_summary_includes_warning_count(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        # Has warnings, so summary should mention count
        assert "warning" in pkt["summary"].lower()


# ═══════════════════════════════════════════════════════════════════════════
# 9. Degraded / fallback tests
# ═══════════════════════════════════════════════════════════════════════════

class TestDegradedFallback:
    """Degraded and edge-case inputs are handled safely."""

    def test_partial_portfolio_valid_packet(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            portfolio=_make_portfolio(status="partial"),
        )
        assert pkt["status"] == "complete"
        assert pkt["portfolio"] is not None
        assert "portfolio_partial" in pkt["warning_flags"]

    def test_empty_portfolio_valid_packet(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            portfolio=_make_portfolio(status="empty"),
        )
        assert pkt["status"] == "complete"
        assert "portfolio_error" in pkt["warning_flags"]

    def test_missing_event_context_no_crash(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
        )
        assert pkt["status"] == "complete"
        assert pkt["events"] is None

    def test_degraded_model_context_no_crash(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            model_context=_make_model_analysis(status="degraded"),
        )
        assert pkt["status"] == "complete"
        assert pkt["model_context"] is not None

    def test_error_model_context_warns(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            model_context=_make_model_analysis(status="error"),
        )
        assert "model_context_error" in pkt["warning_flags"]

    def test_model_list_mixed_statuses(self):
        models = [
            _make_model_analysis(status="success"),
            _make_model_analysis(status="error", analysis_type="sentiment"),
        ]
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            model_context=models,
        )
        assert pkt["model_context"] is not None
        assert len(pkt["model_context"]) == 2
        # Has degraded flag because of mixed success/error
        assert "model_context_degraded" in pkt["warning_flags"]

    def test_no_data_events_warns(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            events=_make_events(status="no_data"),
        )
        assert "events_error" in pkt["warning_flags"]

    def test_conflicts_detected_still_ok(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            conflicts=_make_conflicts(status="conflicts_detected", conflict_count=2, conflict_severity="moderate"),
        )
        assert pkt["status"] == "complete"
        assert pkt["conflicts"]["conflict_count"] == 2

    def test_empty_dict_portfolio_treated_as_missing(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            portfolio={},
        )
        assert pkt["portfolio"] is None
        assert "portfolio_not_provided" in pkt["warning_flags"]

    def test_empty_list_model_treated_as_missing(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            model_context=[],
        )
        assert pkt["model_context"] is None
        assert "model_context_not_provided" in pkt["warning_flags"]

    def test_assembled_fallback_included(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            assembled=_make_assembled(),
        )
        assert pkt["status"] == "complete"
        qo = pkt["quality_overview"]
        assert "assembled" in qo["subsystems_present"]


# ═══════════════════════════════════════════════════════════════════════════
# 10. Assembled section (v1.1)
# ═══════════════════════════════════════════════════════════════════════════

class TestAssembledSection:
    """v1.1: assembled context is now included in the output packet."""

    def test_assembled_section_present_when_provided(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            assembled=_make_assembled(),
        )
        assert pkt["assembled"] is not None
        assert pkt["assembled"]["context_version"] == "1.0"

    def test_assembled_section_none_when_missing(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        assert pkt["assembled"] is None

    def test_assembled_section_is_copy(self):
        asm = _make_assembled()
        pkt = build_decision_packet(candidate=_make_candidate(), assembled=asm)
        assert pkt["assembled"] is not asm

    def test_assembled_empty_dict_treated_as_missing(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            assembled={},
        )
        assert pkt["assembled"] is None

    def test_assembled_degraded_modules_surfaced(self):
        """Assembled with degraded modules shows in evidence."""
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            assembled=_make_assembled(
                assembly_status="degraded",
                degraded_modules=["finnhub"],
                failed_modules=["yahoo"],
            ),
        )
        ev = pkt["evidence"]
        assert sorted(ev["assembled_degraded_modules"]) == ["finnhub", "yahoo"]

    def test_assembled_no_degraded_modules(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            assembled=_make_assembled(),
        )
        assert pkt["evidence"]["assembled_degraded_modules"] == []


# ═══════════════════════════════════════════════════════════════════════════
# 11. Events integration honesty (v1.1)
# ═══════════════════════════════════════════════════════════════════════════

class TestEventsIntegration:
    """v1.1: events_integration_status clarifies event context role."""

    def test_events_integration_support_only(self):
        """Events present → events_integration_status = 'support_context_only'."""
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            events=_make_events(),
        )
        assert pkt["evidence"]["events_integration_status"] == "support_context_only"

    def test_events_integration_none_when_missing(self):
        """Events absent → events_integration_status = None."""
        pkt = build_decision_packet(candidate=_make_candidate())
        assert pkt["evidence"]["events_integration_status"] is None

    def test_events_not_policy_integrated(self):
        """Component role for events is 'support_context_only'."""
        pkt = build_decision_packet(**_full_inputs())
        roles = pkt["metadata"]["component_roles"]
        assert roles["events"] == "support_context_only"

    def test_policy_role_is_evaluated(self):
        """Component role for policy is 'evaluated'."""
        pkt = build_decision_packet(**_full_inputs())
        roles = pkt["metadata"]["component_roles"]
        assert roles["policy"] == "evaluated"

    def test_assembled_role_is_metadata_only(self):
        """Component role for assembled is 'metadata_only'."""
        pkt = build_decision_packet(**_full_inputs())
        roles = pkt["metadata"]["component_roles"]
        assert roles["assembled"] == "metadata_only"


# ═══════════════════════════════════════════════════════════════════════════
# 12. Model context details (v1.1)
# ═══════════════════════════════════════════════════════════════════════════

class TestModelContextDetails:
    """v1.1: evidence surfaces model context count and types."""

    def test_single_model_context(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            model_context=_make_model_analysis(),
        )
        ev = pkt["evidence"]
        assert ev["model_context_count"] == 1
        assert ev["model_context_types"] == ["technical"]

    def test_multiple_model_contexts(self):
        models = [
            _make_model_analysis(analysis_type="technical"),
            _make_model_analysis(analysis_type="sentiment"),
            _make_model_analysis(analysis_type="fundamental"),
        ]
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            model_context=models,
        )
        ev = pkt["evidence"]
        assert ev["model_context_count"] == 3
        assert sorted(ev["model_context_types"]) == ["fundamental", "sentiment", "technical"]

    def test_no_model_context(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        ev = pkt["evidence"]
        assert ev["model_context_count"] == 0
        assert ev["model_context_types"] == []

    def test_model_list_mixed_statuses_preserved(self):
        """Multiple models with mixed statuses are all preserved."""
        models = [
            _make_model_analysis(status="success", analysis_type="technical"),
            _make_model_analysis(status="error", analysis_type="sentiment"),
        ]
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            model_context=models,
        )
        assert len(pkt["model_context"]) == 2
        assert pkt["evidence"]["model_context_count"] == 2

    def test_model_context_role_is_support(self):
        """Model context role is support_context_only."""
        pkt = build_decision_packet(**_full_inputs())
        assert pkt["metadata"]["component_roles"]["model_context"] == "support_context_only"


# ═══════════════════════════════════════════════════════════════════════════
# 13. Section statuses in evidence (v1.1)
# ═══════════════════════════════════════════════════════════════════════════

class TestSectionStatuses:
    """v1.1: section_statuses exposed in evidence for inspectability."""

    def test_section_statuses_present(self):
        pkt = build_decision_packet(**_full_inputs())
        ss = pkt["evidence"]["section_statuses"]
        assert isinstance(ss, dict)
        assert "candidate" in ss
        assert "market" in ss
        assert "policy" in ss

    def test_healthy_sections_show_ok(self):
        pkt = build_decision_packet(**_full_inputs())
        ss = pkt["evidence"]["section_statuses"]
        # candidate has no 'status' field → maps to 'unknown'
        assert ss["candidate"] == "unknown"
        assert ss["market"] == "ok"
        assert ss["policy"] == "ok"

    def test_degraded_section_shown(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="degraded"),
            policy=_make_policy(),
        )
        ss = pkt["evidence"]["section_statuses"]
        assert ss["market"] == "degraded"

    def test_missing_section_shown(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        ss = pkt["evidence"]["section_statuses"]
        assert ss["market"] == "missing"
        assert ss["portfolio"] == "missing"

    def test_error_section_shown(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="insufficient_data"),
            policy=_make_policy(),
        )
        ss = pkt["evidence"]["section_statuses"]
        assert ss["market"] == "error"


# ═══════════════════════════════════════════════════════════════════════════
# 14. Component roles in metadata (v1.1)
# ═══════════════════════════════════════════════════════════════════════════

class TestComponentRoles:
    """v1.1: metadata.component_roles documents integration depth."""

    def test_component_roles_shape(self):
        pkt = build_decision_packet(**_full_inputs())
        roles = pkt["metadata"]["component_roles"]
        assert isinstance(roles, dict)
        assert len(roles) == 8

    def test_evaluated_components(self):
        pkt = build_decision_packet(**_full_inputs())
        roles = pkt["metadata"]["component_roles"]
        for k in ("candidate", "market", "policy", "conflicts", "portfolio"):
            assert roles[k] == "evaluated", f"{k} should be 'evaluated'"

    def test_support_context_components(self):
        pkt = build_decision_packet(**_full_inputs())
        roles = pkt["metadata"]["component_roles"]
        assert roles["events"] == "support_context_only"
        assert roles["model_context"] == "support_context_only"

    def test_metadata_only_components(self):
        pkt = build_decision_packet(**_full_inputs())
        roles = pkt["metadata"]["component_roles"]
        assert roles["assembled"] == "metadata_only"

    def test_roles_always_present(self):
        """Component roles are always present even with no inputs."""
        pkt = build_decision_packet()
        assert "component_roles" in pkt["metadata"]
        assert len(pkt["metadata"]["component_roles"]) == 8


# ═══════════════════════════════════════════════════════════════════════════
# 15. Mixed-quality integration scenarios (v1.1)
# ═══════════════════════════════════════════════════════════════════════════

class TestMixedQualityScenarios:
    """v1.1: representative mixed-quality packets survive messy reality."""

    def test_degraded_assembled_with_full_primaries(self):
        """Degraded assembled context doesn't affect packet status."""
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            assembled=_make_assembled(
                assembly_status="degraded",
                degraded_modules=["finnhub"],
            ),
        )
        assert pkt["status"] == "complete"
        assert "assembled_degraded" in pkt["warning_flags"]
        assert pkt["evidence"]["assembled_degraded_modules"] == ["finnhub"]

    def test_events_elevated_with_clean_policy(self):
        """Elevated events + clean policy → complete with event warning."""
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            events=_make_events(event_risk_state="elevated"),
        )
        assert pkt["status"] == "complete"
        assert pkt["evidence"]["event_risk_state"] == "elevated"
        assert pkt["evidence"]["events_integration_status"] == "support_context_only"
        assert "event_calendar_elevated" in pkt["warning_flags"]

    def test_multiple_models_with_partial_market(self):
        """Multiple model contexts + partial market → partial packet."""
        models = [
            _make_model_analysis(analysis_type="technical"),
            _make_model_analysis(analysis_type="sentiment"),
        ]
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="insufficient_data"),
            policy=_make_policy(),
            model_context=models,
        )
        assert pkt["status"] == "partial"
        assert pkt["evidence"]["model_context_count"] == 2
        assert pkt["evidence"]["section_statuses"]["market"] == "error"

    def test_everything_degraded_but_present(self):
        """All components present but degraded → complete with warnings."""
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="degraded"),
            policy=_make_policy(status="evaluated"),
            portfolio=_make_portfolio(status="partial"),
            events=_make_events(status="partial"),
            model_context=_make_model_analysis(status="degraded"),
            assembled=_make_assembled(
                assembly_status="degraded",
                degraded_modules=["finnhub", "yahoo"],
            ),
        )
        assert pkt["status"] == "complete"
        assert len(pkt["warning_flags"]) > 0
        ss = pkt["evidence"]["section_statuses"]
        assert ss["market"] == "degraded"

    def test_minimal_candidate_only_packet(self):
        """Bare minimum packet has all v1.1 evidence fields."""
        pkt = build_decision_packet(candidate=_make_candidate())
        ev = pkt["evidence"]
        assert ev["model_context_count"] == 0
        assert ev["model_context_types"] == []
        assert ev["assembled_degraded_modules"] == []
        assert ev["events_integration_status"] is None
        assert "section_statuses" in ev


# ═══════════════════════════════════════════════════════════════════════════
# 10. Decision readiness tests
# ═══════════════════════════════════════════════════════════════════════════

class TestDecisionReadiness:
    """Explicit decision-readiness semantics."""

    def test_complete_healthy_is_ready(self):
        pkt = build_decision_packet(**_full_inputs())
        assert pkt["quality_overview"]["decision_ready"] is True

    def test_required_only_is_ready(self):
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
        )
        assert pkt["quality_overview"]["decision_ready"] is True

    def test_missing_required_not_ready(self):
        pkt = build_decision_packet(candidate=_make_candidate())
        assert pkt["quality_overview"]["decision_ready"] is False

    def test_no_inputs_not_ready(self):
        pkt = build_decision_packet()
        assert pkt["quality_overview"]["decision_ready"] is False

    def test_degraded_required_still_ready(self):
        """Degraded market is present — still complete, still ready, with note."""
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="degraded"),
            policy=_make_policy(),
        )
        qo = pkt["quality_overview"]
        assert qo["decision_ready"] is True
        assert "degraded" in qo["readiness_note"].lower()

    def test_errored_required_not_ready(self):
        """Errored market → partial → not ready."""
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="insufficient_data"),
            policy=_make_policy(),
        )
        qo = pkt["quality_overview"]
        assert qo["decision_ready"] is False


# ═══════════════════════════════════════════════════════════════════════════
# 11. Integration scenarios
# ═══════════════════════════════════════════════════════════════════════════

class TestIntegrationScenarios:
    """End-to-end scenarios proving full, partial, and insufficient behavior."""

    def test_scenario_complete_review_packet(self):
        """Full candidate review: all subsystems present, all healthy."""
        pkt = build_decision_packet(**_full_inputs())
        assert pkt["status"] == "complete"
        assert pkt["quality_overview"]["decision_ready"] is True
        assert pkt["quality_overview"]["coverage_ratio"] == 1.0
        assert pkt["warning_flags"] == []
        assert pkt["candidate"]["symbol"] == "SPY"
        assert pkt["market"]["market_state"] == "bullish_leaning"
        assert pkt["policy"]["policy_decision"] == "allow"
        assert pkt["events"]["event_risk_state"] == "quiet"
        assert pkt["conflicts"]["conflict_count"] == 0
        assert pkt["portfolio"]["position_count"] == 3
        assert pkt["evidence"]["candidate_symbol"] == "SPY"
        assert pkt["evidence"]["sections_present"] == 8

    def test_scenario_partial_missing_portfolio_events_model(self):
        """Candidate + market + policy only — partial packet."""
        pkt = build_decision_packet(
            candidate=_make_candidate(symbol="QQQ"),
            market=_make_market(market_state="neutral"),
            policy=_make_policy(policy_decision="caution"),
        )
        assert pkt["status"] == "complete"  # all required present
        assert pkt["quality_overview"]["decision_ready"] is True
        assert pkt["portfolio"] is None
        assert pkt["events"] is None
        assert pkt["model_context"] is None
        assert "portfolio_not_provided" in pkt["warning_flags"]
        assert "events_not_provided" in pkt["warning_flags"]
        assert pkt["evidence"]["candidate_symbol"] == "QQQ"
        assert pkt["evidence"]["market_state"] == "neutral"

    def test_scenario_insufficient_no_candidate(self):
        """No candidate — insufficient data."""
        pkt = build_decision_packet(
            market=_make_market(),
            policy=_make_policy(),
            portfolio=_make_portfolio(),
            events=_make_events(),
        )
        assert pkt["status"] == "insufficient_data"
        assert pkt["quality_overview"]["decision_ready"] is False
        assert pkt["candidate"] is None
        assert "candidate_not_provided" in pkt["warning_flags"]
        assert "insufficient" in pkt["summary"].lower()

    def test_scenario_degraded_market_blocking_policy(self):
        """Degraded market + policy blocks trade."""
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(status="degraded"),
            policy=_make_policy(policy_decision="block", decision_severity="critical"),
            conflicts=_make_conflicts(
                status="conflicts_detected",
                conflict_count=3,
                conflict_severity="high",
            ),
            events=_make_events(event_risk_state="crowded"),
        )
        assert pkt["status"] == "complete"
        wf = pkt["warning_flags"]
        assert "policy_blocks_trade" in wf
        assert "event_calendar_crowded" in wf
        assert "market_composite_degraded" in wf
        ev = pkt["evidence"]
        assert ev["policy_decision"] == "block"
        assert ev["conflict_severity"] == "high"

    def test_scenario_partial_missing_market(self):
        """Missing market → partial, not ready."""
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            policy=_make_policy(),
            portfolio=_make_portfolio(),
        )
        assert pkt["status"] == "partial"
        assert pkt["quality_overview"]["decision_ready"] is False
        assert pkt["market"] is None
        assert "market_not_provided" in pkt["warning_flags"]

    def test_scenario_model_list_with_errors(self):
        """Multiple model analyses, some failing."""
        models = [
            _make_model_analysis(status="success", analysis_type="technical"),
            _make_model_analysis(status="error", analysis_type="sentiment"),
            _make_model_analysis(status="success", analysis_type="fundamental"),
        ]
        pkt = build_decision_packet(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
            model_context=models,
        )
        assert pkt["status"] == "complete"
        assert len(pkt["model_context"]) == 3
        assert "model_context_degraded" in pkt["warning_flags"]
