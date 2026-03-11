"""Tests for Final Decision Prompt Payload Builder v1.

Covers:
- Contract shape
- Complete / partial / insufficient-data status
- Block compression (candidate, market, portfolio, policy, event, conflict, model)
- Instruction block stability
- Quality block
- Warning flags
- Summary block
- Metadata & fallback tracking
- Degraded / edge-case inputs
- Compression effectiveness
- Integration scenarios
"""

from __future__ import annotations

import json
import pytest

from app.services.decision_prompt_payload import build_prompt_payload

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures — realistic upstream subsystem outputs inside a decision packet
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
            "composite_version": "1.0", "engines_used": 5,
            "conflict_count": 0, "conflict_severity": "none",
            "overall_quality": "good", "overall_freshness": "fresh",
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
        "market_conflicts": [], "candidate_conflicts": [],
        "model_conflicts": [], "time_horizon_conflicts": [],
        "quality_conflicts": [],
        "metadata": {"detector_version": "1.0", "engines_inspected": 5,
                      "candidates_inspected": 1, "models_inspected": 0, "degraded_inputs": 0},
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
        "metadata": {"portfolio_version": "1.0", "position_count": 3, "underlying_count": 2,
                      "account_equity_provided": True, "greeks_coverage": "full",
                      "sector_coverage": "full", "event_coverage": "none"},
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
            "candidate_symbol": "SPY", "candidate_strategy": "credit_spread",
            "market_status": "ok", "market_state": "bullish_leaning",
            "conflict_severity": "none", "portfolio_status": "ok",
            "checks_triggered": 0, "blocking_count": 0,
            "restrictive_count": 0, "caution_count": 0,
        },
        "metadata": {"policy_version": "1.0", "candidate_provided": True,
                      "market_provided": True, "conflicts_provided": True,
                      "portfolio_provided": True, "checks_evaluated": 12},
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
        "evidence": {"macro_event_count": 0, "company_event_count": 0, "high_importance_count": 0,
                      "within_24h_count": 0, "within_3d_count": 0,
                      "candidate_overlap_count": 0, "portfolio_overlap_count": 0},
        "metadata": {"event_context_version": "1.0", "macro_coverage": "empty",
                      "company_event_coverage": "empty", "candidate_provided": True,
                      "positions_provided": False, "reference_time": "2026-03-10T12:00:00Z",
                      "total_events_processed": 0},
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
        "raw_content": "..." * 100,
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


def _make_packet(**overrides: object) -> dict:
    """Build a full decision packet."""
    base = {
        "decision_packet_version": "1.0",
        "generated_at": "2026-03-10T12:00:00Z",
        "status": "complete",
        "summary": "Decision packet is complete. Candidate: SPY (credit_spread). Market state: bullish_leaning. Policy decision: allow. Event risk: quiet.",
        "candidate": _make_candidate(),
        "market": _make_market(),
        "portfolio": _make_portfolio(),
        "policy": _make_policy(),
        "events": _make_events(),
        "conflicts": _make_conflicts(),
        "model_context": _make_model_analysis(),
        "quality_overview": {
            "packet_status": "complete",
            "decision_ready": True,
            "readiness_note": "All required subsystems present and healthy.",
            "subsystems_present": sorted(["candidate", "market", "policy", "conflicts", "portfolio", "events", "model_context", "assembled"]),
            "subsystems_missing": [],
            "subsystems_degraded": [],
            "present_count": 8,
            "total_subsystems": 8,
            "coverage_ratio": 1.0,
            "warning_count": 0,
        },
        "warning_flags": [],
        "evidence": {
            "candidate_symbol": "SPY",
            "candidate_strategy": "credit_spread",
            "candidate_direction": "bullish",
            "candidate_confidence": 0.72,
            "market_status": "ok",
            "market_state": "bullish_leaning",
            "market_confidence": 0.68,
            "policy_decision": "allow",
            "policy_severity": "none",
            "policy_size_guidance": "normal",
            "event_risk_state": "quiet",
            "event_status": "ok",
            "conflict_severity": "none",
            "conflict_count": 0,
            "portfolio_status": "ok",
            "portfolio_position_count": 3,
            "sections_present": 8,
            "sections_total": 8,
        },
        "metadata": {
            "decision_packet_version": "1.0",
            "generated_at": "2026-03-10T12:00:00Z",
            "candidate_provided": True,
            "market_provided": True,
            "conflicts_provided": True,
            "portfolio_provided": True,
            "policy_provided": True,
            "events_provided": True,
            "model_context_provided": True,
            "assembled_provided": True,
            "upstream_versions": {"market": "1.0", "portfolio": "1.0", "policy": "1.0", "events": "1.0"},
        },
    }
    base.update(overrides)
    return base


def _make_partial_packet() -> dict:
    """Packet with candidate + market + policy only."""
    pkt = _make_packet(
        status="partial",
        summary="Decision packet is partial.",
        portfolio=None,
        events=None,
        conflicts=None,
        model_context=None,
        warning_flags=["portfolio_not_provided", "events_not_provided",
                        "conflicts_not_provided", "model_context_not_provided"],
    )
    pkt["quality_overview"]["decision_ready"] = False
    pkt["quality_overview"]["subsystems_missing"] = ["conflicts", "events", "model_context", "portfolio"]
    pkt["quality_overview"]["coverage_ratio"] = 0.5
    return pkt


def _make_insufficient_packet() -> dict:
    """Packet with no candidate."""
    return _make_packet(
        status="insufficient_data",
        summary="Insufficient data to build decision packet.",
        candidate=None,
        market=_make_market(),
        policy=_make_policy(),
        portfolio=None,
        events=None,
        conflicts=None,
        model_context=None,
        warning_flags=["candidate_not_provided"],
    )


# ═══════════════════════════════════════════════════════════════════════════
# 1. Contract shape tests
# ═══════════════════════════════════════════════════════════════════════════

class TestContractShape:
    EXPECTED_KEYS = {
        "payload_version",
        "generated_at",
        "status",
        "summary_block",
        "candidate_block",
        "market_block",
        "portfolio_block",
        "policy_block",
        "event_block",
        "conflict_block",
        "model_context_block",
        "quality_block",
        "instruction_block",
        "warning_flags",
        "metadata",
    }

    def test_full_packet_shape(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert set(pl.keys()) == self.EXPECTED_KEYS

    def test_empty_input_shape(self):
        pl = build_prompt_payload()
        assert set(pl.keys()) == self.EXPECTED_KEYS

    def test_version_is_string(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert pl["payload_version"] == "1.0"

    def test_generated_at_is_iso(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert isinstance(pl["generated_at"], str)
        assert "T" in pl["generated_at"]

    def test_status_valid(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert pl["status"] in ("complete", "partial", "insufficient_data")

    def test_warning_flags_is_list(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert isinstance(pl["warning_flags"], list)

    def test_metadata_is_dict(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert isinstance(pl["metadata"], dict)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Status tests
# ═══════════════════════════════════════════════════════════════════════════

class TestStatus:
    def test_complete_packet_complete_payload(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert pl["status"] == "complete"

    def test_partial_packet_partial_payload(self):
        pl = build_prompt_payload(decision_packet=_make_partial_packet())
        assert pl["status"] == "partial"

    def test_insufficient_packet_insufficient_payload(self):
        pl = build_prompt_payload(decision_packet=_make_insufficient_packet())
        assert pl["status"] == "insufficient_data"

    def test_no_packet_no_fallback_insufficient(self):
        pl = build_prompt_payload()
        assert pl["status"] == "insufficient_data"

    def test_no_packet_with_candidate_fallback_partial(self):
        pl = build_prompt_payload(candidate=_make_candidate())
        assert pl["status"] == "partial"

    def test_no_packet_full_fallback_complete(self):
        pl = build_prompt_payload(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
        )
        assert pl["status"] == "complete"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Candidate block tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCandidateBlock:
    def test_present_when_packet_has_candidate(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        cb = pl["candidate_block"]
        assert cb is not None
        assert cb["symbol"] == "SPY"
        assert cb["strategy_family"] == "credit_spread"
        assert cb["direction"] == "bullish"

    def test_preserves_key_fields(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        cb = pl["candidate_block"]
        assert cb["time_horizon"] == {"label": "short_term", "dte": 14}
        assert cb["confidence"] == 0.72
        assert cb["setup_quality"] == "good"
        assert cb["thesis_summary"] is not None
        assert cb["risk_definition"] is not None
        assert cb["reward_profile"] is not None
        assert cb["key_metrics"] is not None

    def test_none_when_missing(self):
        pl = build_prompt_payload(decision_packet=_make_insufficient_packet())
        assert pl["candidate_block"] is None

    def test_excludes_noisy_fields(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        cb = pl["candidate_block"]
        assert "candidate_id" not in cb
        assert "scanner_key" not in cb
        assert "pricing_snapshot" not in cb
        assert "detail_sections" not in cb
        assert "generated_at" not in cb


# ═══════════════════════════════════════════════════════════════════════════
# 4. Market block tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMarketBlock:
    def test_present_when_provided(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        mb = pl["market_block"]
        assert mb is not None
        assert mb["market_state"] == "bullish_leaning"
        assert mb["confidence"] == 0.68

    def test_preserves_key_fields(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        mb = pl["market_block"]
        assert mb["support_state"] == "moderate"
        assert mb["stability_state"] == "stable"
        assert mb["summary"] is not None

    def test_excludes_evidence_adjustments(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        mb = pl["market_block"]
        assert "evidence" not in mb
        assert "adjustments" not in mb
        assert "metadata" not in mb

    def test_none_when_missing(self):
        pkt = _make_packet(market=None)
        pl = build_prompt_payload(decision_packet=pkt)
        assert pl["market_block"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 5. Portfolio block tests
# ═══════════════════════════════════════════════════════════════════════════

class TestPortfolioBlock:
    def test_present_when_provided(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        pb = pl["portfolio_block"]
        assert pb is not None
        assert pb["position_count"] == 3

    def test_preserves_key_fields(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        pb = pl["portfolio_block"]
        assert pb["directional_exposure"] is not None
        assert pb["capital_at_risk"] is not None
        assert pb["summary"] == "Moderate portfolio."

    def test_excludes_noisy_fields(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        pb = pl["portfolio_block"]
        assert "sector_concentration" not in pb
        assert "greeks_exposure" not in pb
        assert "metadata" not in pb

    def test_none_when_missing(self):
        pl = build_prompt_payload(decision_packet=_make_partial_packet())
        assert pl["portfolio_block"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 6. Policy block tests
# ═══════════════════════════════════════════════════════════════════════════

class TestPolicyBlock:
    def test_present_when_provided(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        pb = pl["policy_block"]
        assert pb is not None
        assert pb["policy_decision"] == "allow"
        assert pb["size_guidance"] == "normal"

    def test_includes_check_counts(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        pb = pl["policy_block"]
        assert pb["blocking_count"] == 0
        assert pb["caution_count"] == 0
        assert pb["restrictive_count"] == 0

    def test_top_checks_limited(self):
        checks = [
            {"check_code": f"CHK_{i}", "severity": "low", "title": f"Check {i}",
             "category": "x", "description": "...", "entities": [],
             "evidence": {}, "recommended_effect": "caution", "confidence_impact": "minor"}
            for i in range(10)
        ]
        pkt = _make_packet(policy=_make_policy(triggered_checks=checks))
        pl = build_prompt_payload(decision_packet=pkt)
        assert len(pl["policy_block"]["top_checks"]) <= 5

    def test_none_when_missing(self):
        pkt = _make_packet(policy=None)
        pl = build_prompt_payload(decision_packet=pkt)
        assert pl["policy_block"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 7. Event block tests
# ═══════════════════════════════════════════════════════════════════════════

class TestEventBlock:
    def test_present_when_provided(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        eb = pl["event_block"]
        assert eb is not None
        assert eb["event_risk_state"] == "quiet"

    def test_nearest_events_present(self):
        events = _make_events(event_windows={
            "within_24h": [
                {"event_name": "FOMC", "event_type": "macro",
                 "importance": "high", "risk_window": "within_24h"},
            ],
            "within_3d": [],
            "within_7d": [],
            "beyond_7d": [],
        })
        pkt = _make_packet(events=events)
        pl = build_prompt_payload(decision_packet=pkt)
        eb = pl["event_block"]
        assert len(eb["nearest_events"]) == 1
        assert eb["nearest_events"][0]["event_name"] == "FOMC"

    def test_nearest_events_capped(self):
        many = [
            {"event_name": f"EVT_{i}", "event_type": "macro",
             "importance": "medium", "risk_window": "within_24h"}
            for i in range(10)
        ]
        events = _make_events(event_windows={
            "within_24h": many,
            "within_3d": [],
            "within_7d": [],
            "beyond_7d": [],
        })
        pkt = _make_packet(events=events)
        pl = build_prompt_payload(decision_packet=pkt)
        assert len(pl["event_block"]["nearest_events"]) <= 5

    def test_none_when_missing(self):
        pl = build_prompt_payload(decision_packet=_make_partial_packet())
        assert pl["event_block"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 8. Conflict block tests
# ═══════════════════════════════════════════════════════════════════════════

class TestConflictBlock:
    def test_present_when_provided(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        cb = pl["conflict_block"]
        assert cb is not None
        assert cb["conflict_severity"] == "none"
        assert cb["conflict_count"] == 0

    def test_preserves_flags(self):
        conf = _make_conflicts(
            conflict_flags=["tone_disagreement", "horizon_mismatch"],
            conflict_count=2,
            conflict_severity="moderate",
        )
        pkt = _make_packet(conflicts=conf)
        pl = build_prompt_payload(decision_packet=pkt)
        cb = pl["conflict_block"]
        assert cb["conflict_flags"] == ["tone_disagreement", "horizon_mismatch"]

    def test_none_when_missing(self):
        pl = build_prompt_payload(decision_packet=_make_partial_packet())
        assert pl["conflict_block"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 9. Model context block tests
# ═══════════════════════════════════════════════════════════════════════════

class TestModelContextBlock:
    def test_single_model_compressed(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        mc = pl["model_context_block"]
        assert mc is not None
        assert isinstance(mc, list)
        assert len(mc) == 1
        assert mc[0]["analysis_type"] == "technical"
        assert mc[0]["summary"] is not None

    def test_excludes_raw_content(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        mc = pl["model_context_block"]
        assert "raw_content" not in mc[0]
        assert "normalized_text" not in mc[0]
        assert "structured_payload" not in mc[0]
        assert "metadata" not in mc[0]

    def test_multi_model_list(self):
        models = [
            _make_model_analysis(analysis_type="technical"),
            _make_model_analysis(analysis_type="sentiment", summary="Neutral sentiment."),
        ]
        pkt = _make_packet(model_context=models)
        pl = build_prompt_payload(decision_packet=pkt)
        mc = pl["model_context_block"]
        assert len(mc) == 2

    def test_none_when_missing(self):
        pl = build_prompt_payload(decision_packet=_make_partial_packet())
        assert pl["model_context_block"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 10. Instruction block tests
# ═══════════════════════════════════════════════════════════════════════════

class TestInstructionBlock:
    def test_always_present(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        ib = pl["instruction_block"]
        assert ib is not None
        assert isinstance(ib, dict)

    def test_has_role(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert pl["instruction_block"]["role"] == "decision_reviewer"

    def test_has_guidance(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        guidance = pl["instruction_block"]["guidance"]
        assert isinstance(guidance, list)
        assert len(guidance) >= 5

    def test_stable_across_calls(self):
        pl1 = build_prompt_payload(decision_packet=_make_packet())
        pl2 = build_prompt_payload(decision_packet=_make_partial_packet())
        assert pl1["instruction_block"]["guidance"] == pl2["instruction_block"]["guidance"]

    def test_no_decision_in_guidance(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        guidance_text = " ".join(pl["instruction_block"]["guidance"]).lower()
        assert "approve" not in guidance_text
        assert "reject this" not in guidance_text
        assert "allow the trade" not in guidance_text
        assert "deny the trade" not in guidance_text

    def test_mentions_missing_inputs_caution(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        guidance_text = " ".join(pl["instruction_block"]["guidance"]).lower()
        assert "missing" in guidance_text or "degraded" in guidance_text

    def test_has_version(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert pl["instruction_block"]["version"] == "1.0"


# ═══════════════════════════════════════════════════════════════════════════
# 11. Quality block tests
# ═══════════════════════════════════════════════════════════════════════════

class TestQualityBlock:
    EXPECTED_KEYS = {
        "decision_ready",
        "readiness_note",
        "coverage_ratio",
        "subsystems_present",
        "subsystems_missing",
        "subsystems_degraded",
    }

    def test_shape(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert set(pl["quality_block"].keys()) == self.EXPECTED_KEYS

    def test_complete_ready(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        qb = pl["quality_block"]
        assert qb["decision_ready"] is True
        assert qb["coverage_ratio"] == 1.0

    def test_partial_not_ready(self):
        pl = build_prompt_payload(decision_packet=_make_partial_packet())
        qb = pl["quality_block"]
        assert qb["decision_ready"] is False

    def test_from_fallback_derives_quality(self):
        """No packet — quality derived from resolved sections."""
        pl = build_prompt_payload(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
        )
        qb = pl["quality_block"]
        assert qb["decision_ready"] is True
        assert "candidate" in qb["subsystems_present"]


# ═══════════════════════════════════════════════════════════════════════════
# 12. Warning flags tests
# ═══════════════════════════════════════════════════════════════════════════

class TestWarningFlags:
    def test_no_warnings_complete(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert pl["warning_flags"] == []

    def test_propagates_packet_flags(self):
        pkt = _make_packet(warning_flags=["policy_blocks_trade"])
        pl = build_prompt_payload(decision_packet=pkt)
        assert "policy_blocks_trade" in pl["warning_flags"]

    def test_adds_missing_section_flags(self):
        pl = build_prompt_payload(decision_packet=_make_partial_packet())
        wf = pl["warning_flags"]
        assert any("not_provided" in f or "not_available" in f for f in wf)

    def test_fallback_flag_present(self):
        pkt = _make_packet(portfolio=None)
        pl = build_prompt_payload(
            decision_packet=pkt,
            portfolio=_make_portfolio(),
        )
        assert "portfolio_from_fallback" in pl["warning_flags"]

    def test_no_duplicates(self):
        pl = build_prompt_payload(decision_packet=_make_partial_packet())
        wf = pl["warning_flags"]
        assert len(wf) == len(set(wf))


# ═══════════════════════════════════════════════════════════════════════════
# 13. Summary block tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSummaryBlock:
    def test_is_string(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert isinstance(pl["summary_block"], str)
        assert len(pl["summary_block"]) > 0

    def test_complete_uses_packet_summary(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert "SPY" in pl["summary_block"]

    def test_insufficient_mentions_insufficient(self):
        pl = build_prompt_payload(decision_packet=_make_insufficient_packet())
        assert "insufficient" in pl["summary_block"].lower()

    def test_fallback_builds_from_blocks(self):
        pl = build_prompt_payload(
            candidate=_make_candidate(),
            market=_make_market(),
            policy=_make_policy(),
        )
        assert "SPY" in pl["summary_block"]


# ═══════════════════════════════════════════════════════════════════════════
# 14. Metadata tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMetadata:
    def test_shape(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        md = pl["metadata"]
        assert md["payload_version"] == "1.0"
        assert isinstance(md["generated_at"], str)
        assert isinstance(md["sections_included"], list)
        assert isinstance(md["sections_missing"], list)
        assert isinstance(md["fallbacks_used"], list)

    def test_source_packet_version(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        assert pl["metadata"]["source_packet_version"] == "1.0"
        assert pl["metadata"]["source_packet_status"] == "complete"

    def test_fallbacks_tracked(self):
        pkt = _make_packet(portfolio=None)
        pl = build_prompt_payload(decision_packet=pkt, portfolio=_make_portfolio())
        assert "portfolio" in pl["metadata"]["fallbacks_used"]
        assert "portfolio" in pl["metadata"]["sections_included"]

    def test_no_packet_no_version(self):
        pl = build_prompt_payload(candidate=_make_candidate())
        assert pl["metadata"]["source_packet_version"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 15. Compression tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCompression:
    def test_payload_smaller_than_packet(self):
        pkt = _make_packet()
        pl = build_prompt_payload(decision_packet=pkt)
        pkt_size = len(json.dumps(pkt, default=str))
        pl_size = len(json.dumps(pl, default=str))
        assert pl_size < pkt_size, f"Payload ({pl_size}) should be smaller than packet ({pkt_size})"

    def test_raw_content_excluded(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        pl_str = json.dumps(pl, default=str)
        assert "..." * 50 not in pl_str

    def test_model_block_excludes_raw(self):
        pl = build_prompt_payload(decision_packet=_make_packet())
        mc = pl["model_context_block"]
        for item in mc:
            assert "raw_content" not in item
            assert "normalized_text" not in item


# ═══════════════════════════════════════════════════════════════════════════
# 16. Fallback / degraded tests
# ═══════════════════════════════════════════════════════════════════════════

class TestFallbackDegraded:
    def test_fallback_fills_missing_section(self):
        pkt = _make_packet(events=None)
        pl = build_prompt_payload(decision_packet=pkt, events=_make_events())
        assert pl["event_block"] is not None
        assert "events_from_fallback" in pl["warning_flags"]

    def test_packet_section_preferred_over_fallback(self):
        pl = build_prompt_payload(
            decision_packet=_make_packet(),
            market=_make_market(market_state="bearish"),
        )
        # packet market should win
        assert pl["market_block"]["market_state"] == "bullish_leaning"

    def test_empty_dict_section_triggers_fallback(self):
        pkt = _make_packet(portfolio={})
        pl = build_prompt_payload(
            decision_packet=pkt,
            portfolio=_make_portfolio(),
        )
        assert pl["portfolio_block"] is not None
        assert "portfolio_from_fallback" in pl["warning_flags"]

    def test_degraded_model_still_compressed(self):
        model = _make_model_analysis(status="degraded")
        pkt = _make_packet(model_context=model)
        pl = build_prompt_payload(decision_packet=pkt)
        mc = pl["model_context_block"]
        assert mc is not None
        assert mc[0]["status"] == "degraded"

    def test_no_crash_on_garbage_packet(self):
        pl = build_prompt_payload(decision_packet={"random": "data"})
        assert pl["status"] == "insufficient_data"
        assert pl["candidate_block"] is None

    def test_no_crash_on_none_packet(self):
        pl = build_prompt_payload(decision_packet=None)
        assert pl["status"] == "insufficient_data"


# ═══════════════════════════════════════════════════════════════════════════
# 17. Integration scenarios
# ═══════════════════════════════════════════════════════════════════════════

class TestIntegrationScenarios:
    def test_scenario_complete_payload(self):
        """Full packet → complete model-ready payload."""
        pkt = _make_packet()
        pl = build_prompt_payload(decision_packet=pkt)
        assert pl["status"] == "complete"
        assert pl["candidate_block"]["symbol"] == "SPY"
        assert pl["market_block"]["market_state"] == "bullish_leaning"
        assert pl["policy_block"]["policy_decision"] == "allow"
        assert pl["event_block"]["event_risk_state"] == "quiet"
        assert pl["conflict_block"]["conflict_count"] == 0
        assert pl["portfolio_block"]["position_count"] == 3
        assert pl["model_context_block"] is not None
        assert pl["quality_block"]["decision_ready"] is True
        assert pl["instruction_block"]["role"] == "decision_reviewer"
        assert pl["warning_flags"] == []

    def test_scenario_partial_payload(self):
        """Partial packet → partial payload with honest warnings."""
        pkt = _make_partial_packet()
        pl = build_prompt_payload(decision_packet=pkt)
        assert pl["status"] == "partial"
        assert pl["candidate_block"]["symbol"] == "SPY"
        assert pl["market_block"] is not None
        assert pl["portfolio_block"] is None
        assert pl["event_block"] is None
        assert pl["conflict_block"] is None
        assert pl["model_context_block"] is None
        assert pl["quality_block"]["decision_ready"] is False
        assert len(pl["warning_flags"]) > 0
        assert pl["instruction_block"]["role"] == "decision_reviewer"

    def test_scenario_insufficient_payload(self):
        """No candidate → insufficient payload."""
        pkt = _make_insufficient_packet()
        pl = build_prompt_payload(decision_packet=pkt)
        assert pl["status"] == "insufficient_data"
        assert pl["candidate_block"] is None
        assert "insufficient" in pl["summary_block"].lower()

    def test_scenario_fallback_recovery(self):
        """Partial packet + fallback portfolio → portfolio block restored."""
        pkt = _make_partial_packet()
        pl = build_prompt_payload(
            decision_packet=pkt,
            portfolio=_make_portfolio(),
        )
        assert pl["portfolio_block"] is not None
        assert pl["portfolio_block"]["position_count"] == 3
        assert "portfolio_from_fallback" in pl["warning_flags"]

    def test_scenario_degraded_market_blocking_policy(self):
        """Degraded market + blocking policy → all captured honestly."""
        pkt = _make_packet(
            market=_make_market(status="degraded", market_state="uncertain"),
            policy=_make_policy(policy_decision="block", decision_severity="critical"),
            warning_flags=["market_composite_degraded", "policy_blocks_trade"],
        )
        pl = build_prompt_payload(decision_packet=pkt)
        assert pl["market_block"]["market_state"] == "uncertain"
        assert pl["policy_block"]["policy_decision"] == "block"
        assert "market_composite_degraded" in pl["warning_flags"]
        assert "policy_blocks_trade" in pl["warning_flags"]
