"""
Proof script for Final Decision Response Contract v1.

Exercises representative scenarios and prints contract outputs
so that the schema, decision semantics, and degraded-state handling
can be visually inspected and externally reviewed.

Scenarios:
  1. Full Approve — all factors aligned, no warnings
  2. Cautious Approve with warnings — partial status, caution styling cues
  3. Reject with blocking factors — multiple reasons_against, warnings
  4. Insufficient Data — degraded state, unknown fields
  5. Placeholder for dev/testing
  6. Normalisation of broken input
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "BenTrade", "backend"))

from app.services.decision_response_contract import (
    build_decision_response,
    build_placeholder_response,
    validate_decision_response,
    normalize_decision_response,
)

PASS = 0
FAIL = 0


def check(label, response, expected_decision, expected_status):
    global PASS, FAIL
    ok, errors = validate_decision_response(response)
    passed = (
        ok
        and response["decision"] == expected_decision
        and response["status"] == expected_status
    )
    if passed:
        PASS += 1
        tag = "PASS"
    else:
        FAIL += 1
        tag = "FAIL"

    print(f"\n{'='*70}")
    print(f"  [{tag}] {label}")
    print(f"{'='*70}")
    print(f"  decision:         {response['decision']}")
    print(f"  decision_label:   {response['decision_label']}")
    print(f"  status:           {response['status']}")
    print(f"  conviction:       {response['conviction']}")
    print(f"  market_alignment: {response['market_alignment']}")
    print(f"  portfolio_fit:    {response['portfolio_fit']}")
    print(f"  policy_alignment: {response['policy_alignment']}")
    print(f"  event_risk:       {response['event_risk']}")
    print(f"  size_guidance:    {response['size_guidance']}")
    print(f"  time_horizon:     {response['time_horizon']}")
    print(f"  summary:          {response['summary'][:80]}...")
    print(f"  reasons_for:      {len(response['reasons_for'])} items")
    print(f"  reasons_against:  {len(response['reasons_against'])} items")
    print(f"  key_risks:        {len(response['key_risks'])} items")
    print(f"  warning_flags:    {response['warning_flags']}")
    print(f"  source:           {response['metadata'].get('source', '?')}")
    print(f"  valid:            {ok}")
    if errors:
        print(f"  errors:           {errors}")
    return passed


# ── Scenario 1: Full Approve ────────────────────────────────────────────

r1 = build_decision_response(
    decision="approve",
    conviction="high",
    market_alignment="aligned",
    portfolio_fit="good",
    policy_alignment="clear",
    event_risk="low",
    time_horizon="1-5 DTE",
    summary="SPY 560/555 put credit spread — all factors aligned. High probability setup with favorable risk/reward at current IV levels. Proceed at full size.",
    reasons_for=[
        "Market regime aligned — bullish trend, moderate volatility",
        "Portfolio has capacity for additional short delta",
        "IV rank at 0.62 favors premium selling",
        "Short strike below 1-sigma expected move",
        "Clean policy evaluation",
    ],
    reasons_against=["DTE is short (3 days)"],
    key_risks=["Overnight gap risk", "Gamma risk near expiration"],
    size_guidance="normal",
    invalidation_notes=["Close if SPY breaks below 558"],
    monitoring_notes=["Monitor VIX for spike above 22"],
    warning_flags=[],
    evidence={"symbol": "SPY", "strategy": "put_credit_spread", "iv_rank": 0.62},
    source="model",
)
check("Scenario 1: Full Approve", r1, "approve", "complete")


# ── Scenario 2: Cautious Approve with Warnings ─────────────────────────

r2 = build_decision_response(
    decision="cautious_approve",
    conviction="moderate",
    market_alignment="neutral",
    portfolio_fit="acceptable",
    policy_alignment="conditional",
    event_risk="moderate",
    time_horizon="7-14 DTE",
    summary="QQQ 480/475 put credit spread — mixed signals require reduced size. FOMC meeting within 3 days adds event risk.",
    reasons_for=["IV rank elevated at 0.55", "Short strike below support"],
    reasons_against=["FOMC within window", "Mixed trend/momentum", "Tech sector concentrated"],
    key_risks=["FOMC rate decision could trigger 2%+ move", "Sector concentration"],
    size_guidance="reduced",
    invalidation_notes=["Close pre-FOMC if down 50%"],
    monitoring_notes=["Review after FOMC", "Monitor sector correlation"],
    warning_flags=["event_risk_within_window", "portfolio_sector_concentration"],
    evidence={"symbol": "QQQ", "strategy": "put_credit_spread"},
    source="model",
)
check("Scenario 2: Cautious Approve", r2, "cautious_approve", "partial")


# ── Scenario 3: Reject ─────────────────────────────────────────────────

r3 = build_decision_response(
    decision="reject",
    conviction="high",
    market_alignment="misaligned",
    portfolio_fit="poor",
    policy_alignment="blocked",
    event_risk="high",
    time_horizon="1-5 DTE",
    summary="DIA 400/395 rejected — bearish regime conflicts with bullish strategy, portfolio overweight, CPI tomorrow.",
    reasons_for=[],
    reasons_against=[
        "Bearish regime conflicts with short put",
        "Dow-correlated positions overweight",
        "CPI release tomorrow",
        "Policy block triggered",
    ],
    key_risks=["3%+ drawdown possible on CPI surprise", "Portfolio max-loss exceeds budget"],
    size_guidance="none",
    invalidation_notes=["Do not re-enter until regime shifts"],
    monitoring_notes=[],
    warning_flags=["market_conflict", "policy_block", "event_risk_critical"],
    evidence={"symbol": "DIA", "strategy": "put_credit_spread"},
    source="model",
)
check("Scenario 3: Reject", r3, "reject", "partial")


# ── Scenario 4: Insufficient Data ──────────────────────────────────────

r4 = build_decision_response(
    decision="insufficient_data",
    summary="Cannot render a decision — candidate and market data missing.",
    warning_flags=["candidate_not_provided", "market_unavailable"],
)
check("Scenario 4: Insufficient Data", r4, "insufficient_data", "insufficient_data")


# ── Scenario 5: Placeholder ────────────────────────────────────────────

r5 = build_placeholder_response(symbol="SPY", strategy="put_credit_spread")
check("Scenario 5: Placeholder", r5, "watchlist", "partial")


# ── Scenario 6: Normalisation of broken input ──────────────────────────

broken = {
    "decision": 999,
    "conviction": None,
    "reasons_for": "not a list",
    "evidence": "not a dict",
    "extra_garbage": True,
}
r6 = normalize_decision_response(broken)
check("Scenario 6: Normalised Broken Input", r6, "insufficient_data", "insufficient_data")


# ── Final Summary ──────────────────────────────────────────────────────

print(f"\n{'='*70}")
print(f"  PROOF SUMMARY: {PASS} passed, {FAIL} failed out of 6 scenarios")
print(f"{'='*70}")

if FAIL > 0:
    print("\n  *** FAILURES DETECTED ***")
    sys.exit(1)
else:
    print("\n  ALL SCENARIOS PASSED")
    sys.exit(0)
