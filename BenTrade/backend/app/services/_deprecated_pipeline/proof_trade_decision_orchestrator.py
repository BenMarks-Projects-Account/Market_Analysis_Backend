"""Proof script for Trade Decision Orchestrator v1.

Three scenarios:
  1. Complete decision packet — all subsystems present and healthy
  2. Partial packet — missing portfolio, events, model context
  3. Insufficient data — no candidate provided

Run: python scripts/proof_trade_decision_orchestrator.py
"""

from __future__ import annotations

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.trade_decision_orchestrator import build_decision_packet

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _trim(pkt: dict, max_depth: int = 2) -> dict:
    """Trim nested dicts for readability."""
    def _t(v, depth):
        if depth <= 0:
            if isinstance(v, dict):
                return f"<dict {len(v)} keys>"
            if isinstance(v, list):
                return f"<list {len(v)} items>"
            return v
        if isinstance(v, dict):
            return {k: _t(val, depth - 1) for k, val in v.items()}
        if isinstance(v, list):
            return [_t(i, depth - 1) for i in v[:3]] + (["..."] if len(v) > 3 else [])
        return v
    return _t(pkt, max_depth)


def _separator(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

CANDIDATE = {
    "candidate_id": "SPY_bull_put_001",
    "scanner_key": "credit_spreads",
    "scanner_name": "Credit Spread Scanner",
    "strategy_family": "credit_spread",
    "setup_type": "bull_put_spread",
    "asset_class": "equity_option",
    "symbol": "SPY",
    "underlying": {"symbol": "SPY", "price": 510.25},
    "direction": "bullish",
    "thesis_summary": "Sell OTM put spread with 78% POP.",
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
    "generated_at": "2026-03-10T14:00:00Z",
}

MARKET = {
    "composite_version": "1.0",
    "computed_at": "2026-03-10T14:00:00Z",
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

CONFLICTS = {
    "status": "clean",
    "detected_at": "2026-03-10T14:00:00Z",
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

PORTFOLIO = {
    "portfolio_version": "1.0",
    "generated_at": "2026-03-10T14:00:00Z",
    "status": "ok",
    "position_count": 3,
    "underlying_count": 2,
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

POLICY = {
    "policy_version": "1.0",
    "evaluated_at": "2026-03-10T14:00:00Z",
    "status": "evaluated",
    "policy_decision": "allow",
    "decision_severity": "none",
    "summary": "Trade passes all policy checks.",
    "triggered_checks": [], "blocking_checks": [],
    "caution_checks": [], "restrictive_checks": [],
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
    "metadata": {
        "policy_version": "1.0", "candidate_provided": True,
        "market_provided": True, "conflicts_provided": True,
        "portfolio_provided": True, "checks_evaluated": 12,
    },
}

EVENTS = {
    "event_context_version": "1.0",
    "generated_at": "2026-03-10T14:00:00Z",
    "status": "ok",
    "summary": "No significant events nearby.",
    "event_risk_state": "quiet",
    "upcoming_macro_events": [],
    "upcoming_company_events": [],
    "candidate_event_overlap": {"candidate_symbol": "SPY", "overlapping_events": [], "overlap_count": 0},
    "portfolio_event_overlap": {"positions_with_overlap": 0, "symbols_with_overlap": [], "overlapping_events": [], "event_cluster_count": 0},
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
    "status": "success",
    "analysis_type": "technical",
    "analysis_name": "Technical Analysis",
    "category": "technical",
    "model_source": "openai",
    "summary": "Bullish technical outlook for SPY.",
    "key_points": ["Trend up", "Support holding at 505"],
    "risks": ["Resistance at 515"],
    "confidence": 0.75,
    "warnings": [],
    "response_format": "json",
    "metadata": {},
}

ASSEMBLED = {
    "context_version": "1.0",
    "assembled_at": "2026-03-10T14:00:00Z",
    "assembly_status": "complete",
    "assembly_warnings": [],
    "included_modules": ["finnhub", "yahoo"],
    "missing_modules": [],
    "degraded_modules": [],
    "metadata": {"context_version": "1.0", "market_module_count": 2},
}


# ═══════════════════════════════════════════════════════════════════════════
# Scenarios
# ═══════════════════════════════════════════════════════════════════════════

def scenario_complete():
    _separator("SCENARIO 1: Complete Decision Packet")
    pkt = build_decision_packet(
        candidate=CANDIDATE,
        market=MARKET,
        conflicts=CONFLICTS,
        portfolio=PORTFOLIO,
        policy=POLICY,
        events=EVENTS,
        model_context=MODEL,
        assembled=ASSEMBLED,
    )
    checks = {
        "status == complete": pkt["status"] == "complete",
        "decision_ready == True": pkt["quality_overview"]["decision_ready"] is True,
        "coverage_ratio == 1.0": pkt["quality_overview"]["coverage_ratio"] == 1.0,
        "warning_flags empty": pkt["warning_flags"] == [],
        "candidate symbol == SPY": pkt["evidence"]["candidate_symbol"] == "SPY",
        "market_state == bullish_leaning": pkt["evidence"]["market_state"] == "bullish_leaning",
        "policy_decision == allow": pkt["evidence"]["policy_decision"] == "allow",
        "event_risk_state == quiet": pkt["evidence"]["event_risk_state"] == "quiet",
        "sections_present == 8": pkt["evidence"]["sections_present"] == 8,
    }
    _report(checks, pkt)
    return all(checks.values())


def scenario_partial():
    _separator("SCENARIO 2: Partial Packet (missing portfolio, events, model)")
    pkt = build_decision_packet(
        candidate=CANDIDATE,
        market=MARKET,
        policy=POLICY,
    )
    checks = {
        "status == complete": pkt["status"] == "complete",
        "decision_ready == True": pkt["quality_overview"]["decision_ready"] is True,
        "portfolio is None": pkt["portfolio"] is None,
        "events is None": pkt["events"] is None,
        "model_context is None": pkt["model_context"] is None,
        "portfolio_not_provided in warnings": "portfolio_not_provided" in pkt["warning_flags"],
        "events_not_provided in warnings": "events_not_provided" in pkt["warning_flags"],
        "present_count == 3": pkt["quality_overview"]["present_count"] == 3,
    }
    _report(checks, pkt)
    return all(checks.values())


def scenario_insufficient():
    _separator("SCENARIO 3: Insufficient Data (no candidate)")
    pkt = build_decision_packet(
        market=MARKET,
        policy=POLICY,
        portfolio=PORTFOLIO,
        events=EVENTS,
    )
    checks = {
        "status == insufficient_data": pkt["status"] == "insufficient_data",
        "decision_ready == False": pkt["quality_overview"]["decision_ready"] is False,
        "candidate is None": pkt["candidate"] is None,
        "candidate_not_provided in warnings": "candidate_not_provided" in pkt["warning_flags"],
        "summary mentions insufficient": "insufficient" in pkt["summary"].lower(),
        "market preserved despite no candidate": pkt["market"] is not None,
    }
    _report(checks, pkt)
    return all(checks.values())


def _report(checks: dict[str, bool], pkt: dict):
    for label, passed in checks.items():
        icon = "PASS" if passed else "FAIL"
        print(f"  [{icon}] {label}")

    print(f"\n  Trimmed output:")
    trimmed = _trim(pkt, max_depth=2)
    print(json.dumps(trimmed, indent=2, default=str))


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    results = [
        ("Scenario 1 (Complete)", scenario_complete()),
        ("Scenario 2 (Partial)", scenario_partial()),
        ("Scenario 3 (Insufficient)", scenario_insufficient()),
    ]

    print(f"\n{'='*70}")
    print("  FINAL RESULTS")
    print(f"{'='*70}")
    all_pass = True
    for name, ok in results:
        icon = "PASS" if ok else "FAIL"
        print(f"  [{icon}] {name}")
        if not ok:
            all_pass = False

    if all_pass:
        print("\n  ALL SCENARIOS PASSED")
    else:
        print("\n  SOME SCENARIOS FAILED")
        sys.exit(1)
