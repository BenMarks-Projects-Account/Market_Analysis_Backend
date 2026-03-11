"""Proof script for Final Decision Prompt Payload Builder v1.

Three scenarios:
  1. Complete payload — full decision packet in, complete model-ready payload out
  2. Partial payload — partial packet with fallback portfolio recovery
  3. Insufficient payload — no candidate

Run: python scripts/proof_decision_prompt_payload.py
"""

from __future__ import annotations

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.decision_prompt_payload import build_prompt_payload

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _trim(obj, max_depth=2):
    def _t(v, d):
        if d <= 0:
            if isinstance(v, dict): return f"<dict {len(v)} keys>"
            if isinstance(v, list): return f"<list {len(v)} items>"
            return v
        if isinstance(v, dict): return {k: _t(val, d - 1) for k, val in v.items()}
        if isinstance(v, list): return [_t(i, d - 1) for i in v[:3]] + (["..."] if len(v) > 3 else [])
        return v
    return _t(obj, max_depth)

def _sep(title):
    print(f"\n{'='*70}\n  {title}\n{'='*70}")

def _report(checks, payload):
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    print(f"\n  Payload size: {len(json.dumps(payload, default=str))} bytes")
    print(f"\n  Trimmed output:")
    print(json.dumps(_trim(payload, 2), indent=2, default=str))


# ═══════════════════════════════════════════════════════════════════════════
# Shared fixtures
# ═══════════════════════════════════════════════════════════════════════════

CANDIDATE = {
    "candidate_id": "SPY_bull_put_001", "scanner_key": "credit_spreads",
    "scanner_name": "Credit Spread Scanner", "strategy_family": "credit_spread",
    "setup_type": "bull_put_spread", "asset_class": "equity_option",
    "symbol": "SPY", "underlying": {"symbol": "SPY", "price": 510.25},
    "direction": "bullish", "thesis_summary": "Sell OTM put spread with 78% POP.",
    "entry_context": {"short_strike": 500, "long_strike": 495, "width": 5},
    "time_horizon": {"label": "short_term", "dte": 14},
    "setup_quality": "good", "confidence": 0.72,
    "risk_definition": {"type": "defined", "max_loss_per_contract": 500},
    "reward_profile": {"type": "credit", "max_gain_per_contract": 85},
    "supporting_signals": ["trend_up", "iv_rank_moderate"],
    "risk_flags": [], "invalidation_signals": [],
    "market_context_tags": ["bullish_momentum"],
    "position_sizing_notes": "Standard 1-lot.",
    "data_quality": {"source": "tradier", "source_confidence": "high", "missing_fields": []},
    "source_status": "live",
    "pricing_snapshot": {"bid": 0.85, "ask": 0.90, "mid": 0.875},
    "strategy_structure": {"legs": 2},
    "candidate_metrics": {"ev_per_contract": 28.5, "pop": 0.78},
    "detail_sections": {}, "generated_at": "2026-03-10T14:00:00Z",
}

MARKET = {
    "composite_version": "1.0", "computed_at": "2026-03-10T14:00:00Z",
    "status": "ok", "market_state": "bullish_leaning",
    "support_state": "moderate", "stability_state": "stable",
    "confidence": 0.68,
    "evidence": {"market_state": {}, "support_state": {}, "stability_state": {}},
    "adjustments": {"conflict_adjustment": None, "quality_adjustment": None, "horizon_adjustment": None},
    "summary": "Market is bullish-leaning with moderate support.",
    "metadata": {"composite_version": "1.0", "engines_used": 5,
                  "conflict_count": 0, "conflict_severity": "none",
                  "overall_quality": "good", "overall_freshness": "fresh",
                  "horizon_span": "short_term"},
}

POLICY = {
    "policy_version": "1.0", "evaluated_at": "2026-03-10T14:00:00Z",
    "status": "evaluated", "policy_decision": "allow",
    "decision_severity": "none",
    "summary": "Trade passes all policy checks.",
    "triggered_checks": [], "blocking_checks": [],
    "caution_checks": [], "restrictive_checks": [],
    "size_guidance": "normal",
    "eligibility_flags": ["clean_evaluation", "eligible"],
    "warning_flags": [],
    "evidence": {"candidate_symbol": "SPY", "candidate_strategy": "credit_spread",
                  "market_status": "ok", "market_state": "bullish_leaning",
                  "conflict_severity": "none", "portfolio_status": "ok",
                  "checks_triggered": 0, "blocking_count": 0,
                  "restrictive_count": 0, "caution_count": 0},
    "metadata": {"policy_version": "1.0", "candidate_provided": True,
                  "market_provided": True, "conflicts_provided": True,
                  "portfolio_provided": True, "checks_evaluated": 12},
}

CONFLICTS = {
    "status": "clean", "detected_at": "2026-03-10T14:00:00Z",
    "conflict_count": 0, "conflict_severity": "none",
    "conflict_summary": "No conflicts detected.", "conflict_flags": [],
    "market_conflicts": [], "candidate_conflicts": [],
    "model_conflicts": [], "time_horizon_conflicts": [],
    "quality_conflicts": [],
    "metadata": {"detector_version": "1.0", "engines_inspected": 5,
                  "candidates_inspected": 1, "models_inspected": 0, "degraded_inputs": 0},
}

PORTFOLIO = {
    "portfolio_version": "1.0", "generated_at": "2026-03-10T14:00:00Z",
    "status": "ok", "position_count": 3, "underlying_count": 2,
    "portfolio_summary": {"description": "Moderate options portfolio.", "risk_level": "moderate"},
    "directional_exposure": {"net_delta": 0.15},
    "underlying_concentration": {}, "sector_concentration": {},
    "strategy_concentration": {}, "expiration_concentration": {},
    "capital_at_risk": {"total": 1500},
    "greeks_exposure": {}, "event_exposure": {}, "correlation_exposure": {},
    "risk_flags": [], "warning_flags": [],
    "evidence": {"position_count": 3, "underlying_count": 2, "symbols": ["SPY", "QQQ"], "has_account_equity": True},
    "metadata": {"portfolio_version": "1.0", "position_count": 3, "underlying_count": 2,
                  "account_equity_provided": True, "greeks_coverage": "full",
                  "sector_coverage": "full", "event_coverage": "none"},
}

EVENTS = {
    "event_context_version": "1.0", "generated_at": "2026-03-10T14:00:00Z",
    "status": "ok", "summary": "No significant events nearby.",
    "event_risk_state": "quiet",
    "upcoming_macro_events": [], "upcoming_company_events": [],
    "candidate_event_overlap": {"candidate_symbol": "SPY", "overlapping_events": [], "overlap_count": 0},
    "portfolio_event_overlap": {"positions_with_overlap": 0, "symbols_with_overlap": [],
                                 "overlapping_events": [], "event_cluster_count": 0},
    "event_windows": {"within_24h": [], "within_3d": [], "within_7d": [], "beyond_7d": []},
    "risk_flags": [], "warning_flags": [],
    "evidence": {"macro_event_count": 0, "company_event_count": 0, "high_importance_count": 0,
                  "within_24h_count": 0, "within_3d_count": 0,
                  "candidate_overlap_count": 0, "portfolio_overlap_count": 0},
    "metadata": {"event_context_version": "1.0", "macro_coverage": "empty",
                  "company_event_coverage": "empty", "candidate_provided": True,
                  "positions_provided": False, "reference_time": "2026-03-10T14:00:00Z",
                  "total_events_processed": 0},
}

MODEL = {
    "status": "success", "analysis_type": "technical",
    "analysis_name": "Technical Analysis", "category": "technical",
    "model_source": "openai", "summary": "Bullish technical outlook for SPY.",
    "key_points": ["Trend up", "Support holding at 505"],
    "risks": ["Resistance at 515"], "confidence": 0.75,
    "warnings": [], "response_format": "json",
    "raw_content": "x" * 500,
    "normalized_text": "Bullish trend confirmed with momentum.",
    "structured_payload": {"trend": "bullish", "momentum": "strong"},
    "metadata": {},
}


def _full_packet():
    return {
        "decision_packet_version": "1.0",
        "generated_at": "2026-03-10T14:00:00Z",
        "status": "complete",
        "summary": "Decision packet is complete. Candidate: SPY (credit_spread). Market state: bullish_leaning. Policy decision: allow. Event risk: quiet.",
        "candidate": CANDIDATE, "market": MARKET, "portfolio": PORTFOLIO,
        "policy": POLICY, "events": EVENTS, "conflicts": CONFLICTS,
        "model_context": MODEL,
        "quality_overview": {
            "packet_status": "complete", "decision_ready": True,
            "readiness_note": "All required subsystems present and healthy.",
            "subsystems_present": sorted(["candidate", "market", "policy", "conflicts", "portfolio", "events", "model_context", "assembled"]),
            "subsystems_missing": [], "subsystems_degraded": [],
            "present_count": 8, "total_subsystems": 8, "coverage_ratio": 1.0, "warning_count": 0,
        },
        "warning_flags": [],
        "evidence": {
            "candidate_symbol": "SPY", "candidate_strategy": "credit_spread",
            "market_state": "bullish_leaning", "policy_decision": "allow",
            "event_risk_state": "quiet", "sections_present": 8, "sections_total": 8,
        },
        "metadata": {
            "decision_packet_version": "1.0", "generated_at": "2026-03-10T14:00:00Z",
            "candidate_provided": True, "market_provided": True,
            "conflicts_provided": True, "portfolio_provided": True,
            "policy_provided": True, "events_provided": True,
            "model_context_provided": True, "assembled_provided": True,
            "upstream_versions": {"market": "1.0", "portfolio": "1.0", "policy": "1.0", "events": "1.0"},
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# Scenarios
# ═══════════════════════════════════════════════════════════════════════════

def scenario_complete():
    _sep("SCENARIO 1: Complete Prompt Payload")
    pkt = _full_packet()
    pkt_size = len(json.dumps(pkt, default=str))
    pl = build_prompt_payload(decision_packet=pkt)
    pl_size = len(json.dumps(pl, default=str))

    checks = {
        "status == complete": pl["status"] == "complete",
        "candidate_block present": pl["candidate_block"] is not None,
        "candidate_block.symbol == SPY": pl["candidate_block"]["symbol"] == "SPY",
        "market_block present": pl["market_block"] is not None,
        "policy_block.decision == allow": pl["policy_block"]["policy_decision"] == "allow",
        "event_block present": pl["event_block"] is not None,
        "conflict_block present": pl["conflict_block"] is not None,
        "model_context_block present": pl["model_context_block"] is not None,
        "quality_block.ready == True": pl["quality_block"]["decision_ready"] is True,
        "instruction_block present": pl["instruction_block"] is not None,
        "warning_flags empty": pl["warning_flags"] == [],
        f"payload ({pl_size}B) < packet ({pkt_size}B)": pl_size < pkt_size,
        "raw_content excluded": "x" * 100 not in json.dumps(pl, default=str),
    }
    _report(checks, pl)
    return all(checks.values())


def scenario_partial_with_fallback():
    _sep("SCENARIO 2: Partial Payload + Fallback Recovery")
    pkt = _full_packet()
    pkt["status"] = "partial"
    pkt["portfolio"] = None
    pkt["events"] = None
    pkt["model_context"] = None
    pkt["warning_flags"] = ["portfolio_not_provided", "events_not_provided", "model_context_not_provided"]
    pkt["quality_overview"]["decision_ready"] = False
    pkt["quality_overview"]["subsystems_missing"] = ["events", "model_context", "portfolio"]

    pl = build_prompt_payload(
        decision_packet=pkt,
        portfolio=PORTFOLIO,  # fallback fills this
    )

    checks = {
        "status == partial": pl["status"] == "partial",
        "candidate_block present": pl["candidate_block"] is not None,
        "portfolio_block recovered": pl["portfolio_block"] is not None,
        "portfolio_from_fallback flagged": "portfolio_from_fallback" in pl["warning_flags"],
        "event_block still None": pl["event_block"] is None,
        "model_context_block still None": pl["model_context_block"] is None,
        "instruction_block present": pl["instruction_block"] is not None,
        "warnings > 0": len(pl["warning_flags"]) > 0,
    }
    _report(checks, pl)
    return all(checks.values())


def scenario_insufficient():
    _sep("SCENARIO 3: Insufficient Data (no candidate)")
    pkt = _full_packet()
    pkt["status"] = "insufficient_data"
    pkt["candidate"] = None
    pkt["summary"] = "Insufficient data to build decision packet."
    pkt["warning_flags"] = ["candidate_not_provided"]

    pl = build_prompt_payload(decision_packet=pkt)

    checks = {
        "status == insufficient_data": pl["status"] == "insufficient_data",
        "candidate_block is None": pl["candidate_block"] is None,
        "summary mentions insufficient": "insufficient" in pl["summary_block"].lower(),
        "instruction_block still present": pl["instruction_block"] is not None,
        "market_block still present": pl["market_block"] is not None,
    }
    _report(checks, pl)
    return all(checks.values())


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    results = [
        ("Scenario 1 (Complete)", scenario_complete()),
        ("Scenario 2 (Partial + Fallback)", scenario_partial_with_fallback()),
        ("Scenario 3 (Insufficient)", scenario_insufficient()),
    ]
    print(f"\n{'='*70}\n  FINAL RESULTS\n{'='*70}")
    all_pass = True
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok: all_pass = False
    print(f"\n  {'ALL SCENARIOS PASSED' if all_pass else 'SOME SCENARIOS FAILED'}")
    if not all_pass: sys.exit(1)
