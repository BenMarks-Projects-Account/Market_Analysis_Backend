#!/usr/bin/env python
"""
Proof script – Event / Macro Calendar Context v1
==================================================

Demonstrates three scenarios:
  1. Macro-heavy elevated/crowded risk (FOMC + CPI + NFP, candidate overlap)
  2. Quiet / partial-data case (distant low-importance events)
  3. Candidate + portfolio overlap proof (AAPL earnings overlap)

Run:
    cd BenTrade/backend
    python scripts/proof_event_calendar_context.py
"""

import json
import datetime as dt
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.event_calendar_context import build_event_context

REF = dt.datetime(2026, 3, 10, 14, 0, 0, tzinfo=dt.timezone.utc)


def _print(label: str, result: dict) -> None:
    print(f"\n{'='*65}")
    print(f"  SCENARIO: {label}")
    print(f"{'='*65}")
    print(f"  status:           {result['status']}")
    print(f"  event_risk_state: {result['event_risk_state']}")
    print(f"  macro events:     {result['evidence']['macro_event_count']}")
    print(f"  company events:   {result['evidence']['company_event_count']}")
    print(f"  high importance:  {result['evidence']['high_importance_count']}")
    print(f"  within 24h:       {result['evidence']['within_24h_count']}")
    print(f"  within 3d:        {result['evidence']['within_3d_count']}")
    print(f"  cand overlap:     {result['candidate_event_overlap']['overlap_count']}")
    print(f"  port overlap:     {result['portfolio_event_overlap']['event_cluster_count']}")
    print(f"  risk_flags:       {result['risk_flags']}")
    print(f"  warning_flags:    {result['warning_flags']}")
    print(f"  summary:          {result['summary']}")

    if result['upcoming_macro_events']:
        print(f"\n  Macro events:")
        for e in result['upcoming_macro_events']:
            tte = e['time_to_event']
            hrs = f"{tte['hours']}h" if tte else "?"
            print(f"    [{e['importance']:>7}] {e['event_name']:<25} {hrs:<8} "
                  f"window={e['risk_window'] or '?'}")

    if result['upcoming_company_events']:
        print(f"\n  Company events:")
        for e in result['upcoming_company_events']:
            tte = e['time_to_event']
            hrs = f"{tte['hours']}h" if tte else "?"
            print(f"    [{e['importance']:>7}] {e['event_name']:<25} {hrs:<8} "
                  f"syms={e['related_symbols']}")

    co = result['candidate_event_overlap']
    if co['overlap_count'] > 0:
        print(f"\n  Candidate overlap ({co['candidate_symbol']}):")
        for e in co['overlapping_events']:
            print(f"    -> {e['event_name']}")

    po = result['portfolio_event_overlap']
    if po['event_cluster_count'] > 0:
        print(f"\n  Portfolio overlap ({po['symbols_with_overlap']}):")
        for e in po['overlapping_events']:
            print(f"    -> {e['event_name']}")
    print()


# ── Scenario 1: Macro-heavy elevated/crowded ────────────────────
r1 = build_event_context(
    macro_events=[
        {
            "event_name": "FOMC Decision",
            "event_type": "macro",
            "event_time": (REF + dt.timedelta(hours=18)).isoformat(),
        },
        {
            "event_name": "CPI Release",
            "event_type": "macro",
            "event_time": (REF + dt.timedelta(hours=50)).isoformat(),
        },
        {
            "event_name": "Non Farm Payrolls",
            "event_type": "macro",
            "event_time": (REF + dt.timedelta(hours=90)).isoformat(),
        },
    ],
    company_events=[
        {
            "event_name": "AAPL Q1 Earnings",
            "event_type": "earnings",
            "related_symbols": ["AAPL"],
            "event_time": (REF + dt.timedelta(hours=72)).isoformat(),
        },
    ],
    candidate={"symbol": "SPY", "entry_context": {"dte": 7}},
    positions=[
        {"symbol": "SPY"},
        {"symbol": "QQQ"},
        {"symbol": "AAPL"},
    ],
    reference_time=REF,
)
_print("Macro-heavy elevated/crowded", r1)
assert r1["event_risk_state"] in ("elevated", "crowded"), \
    f"Expected elevated/crowded, got {r1['event_risk_state']}"
assert r1["candidate_event_overlap"]["overlap_count"] >= 1
assert r1["portfolio_event_overlap"]["event_cluster_count"] >= 1
assert len(r1["risk_flags"]) >= 1
print("  OK Scenario 1 PASSED")

# ── Scenario 2: Quiet / partial data ────────────────────────────
r2 = build_event_context(
    macro_events=[
        {
            "event_name": "Factory Orders",
            "event_type": "macro",
            "importance": "low",
            "event_time": (REF + dt.timedelta(hours=200)).isoformat(),
        },
        {
            "event_name": "Trade Balance",
            "event_type": "macro",
            "importance": "low",
            "event_time": (REF + dt.timedelta(hours=250)).isoformat(),
        },
    ],
    # No company events provided (None)
    reference_time=REF,
)
_print("Quiet / partial data", r2)
assert r2["status"] == "partial"
assert r2["event_risk_state"] == "quiet"
assert r2["risk_flags"] == []
assert "company_events_not_provided" in r2["warning_flags"]
assert r2["metadata"]["company_event_coverage"] == "none"
print("  OK Scenario 2 PASSED")

# ── Scenario 3: Candidate + portfolio earnings overlap ──────────
r3 = build_event_context(
    company_events=[
        {
            "event_name": "AAPL Q1 Earnings",
            "event_type": "earnings",
            "related_symbols": ["AAPL"],
            "event_time": (REF + dt.timedelta(hours=48)).isoformat(),
        },
    ],
    candidate={"symbol": "AAPL", "entry_context": {"dte": 30}},
    positions=[
        {"symbol": "AAPL"},
        {"symbol": "MSFT"},
        {"symbol": "GOOGL"},
    ],
    reference_time=REF,
)
_print("Candidate + portfolio earnings overlap", r3)
co = r3["candidate_event_overlap"]
assert co["candidate_symbol"] == "AAPL"
assert co["overlap_count"] == 1, f"Expected 1, got {co['overlap_count']}"
assert "candidate_overlaps_event" in r3["risk_flags"]
po = r3["portfolio_event_overlap"]
assert "AAPL" in po["symbols_with_overlap"]
assert po["event_cluster_count"] == 1
print("  OK Scenario 3 PASSED")

print(f"\n{'='*65}")
print("  ALL 3 SCENARIOS PASSED")
print(f"{'='*65}")
