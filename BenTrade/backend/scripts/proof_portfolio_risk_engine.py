"""
Proof script for Portfolio Risk / Exposure Engine v1
=====================================================

Demonstrates the engine with two realistic scenarios:
1. Concentrated portfolio — heavy SPY/index exposure, all bullish, near-term
2. Diversified portfolio — mixed symbols, directions, wide DTE spread

Run:  python -m scripts.proof_portfolio_risk_engine
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.portfolio_risk_engine import build_portfolio_exposure


def _print_section(title: str, data, indent: int = 2):
    print(f"\n{'-' * 60}")
    print(f"  {title}")
    print(f"{'-' * 60}")
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=indent, default=str))
    else:
        print(data)


# ══════════════════════════════════════════════════════════════
#  SCENARIO 1: Concentrated Portfolio
# ══════════════════════════════════════════════════════════════

def scenario_concentrated():
    print("\n" + "=" * 60)
    print("  SCENARIO 1: Concentrated Portfolio")
    print("=" * 60)
    print("  5 positions, all SPY put credit spreads, all 0-7 DTE")
    print("  Expected: heavy concentration flags, bullish lean")

    positions = [
        {
            "trade_key": f"SPY_20250710_PUT_{420 - i * 5}/{415 - i * 5}",
            "symbol": "SPY",
            "strategy": "put_credit_spread",
            "expiration": "2025-07-10",
            "dte": 5,
            "quantity": -1,
            "risk": 350.0,
            "delta": -0.25,
            "gamma": 0.01,
            "theta": -0.05,
            "vega": 0.10,
        }
        for i in range(5)
    ]

    result = build_portfolio_exposure(positions, account_equity=25000)

    _print_section("Status & Counts", {
        "status": result["status"],
        "position_count": result["position_count"],
        "underlying_count": result["underlying_count"],
    })
    _print_section("Portfolio Summary", result["portfolio_summary"])
    _print_section("Directional Exposure", result["directional_exposure"])
    _print_section("Underlying Concentration", result["underlying_concentration"])
    _print_section("Strategy Concentration", result["strategy_concentration"])
    _print_section("Expiration Concentration", result["expiration_concentration"])
    _print_section("Capital at Risk", result["capital_at_risk"])
    _print_section("Greeks Exposure", result["greeks_exposure"])
    _print_section("Correlation Exposure", result["correlation_exposure"])
    _print_section("Risk Flags", result["risk_flags"])
    _print_section("Warning Flags", result["warning_flags"])

    # ── Verify expected flags ──
    assert result["status"] in ("ok", "partial"), f"Expected ok/partial, got {result['status']}"
    assert "underlying_concentrated" in result["risk_flags"], "Expected underlying_concentrated flag"
    assert "strategy_concentrated" in result["risk_flags"], "Expected strategy_concentrated flag"
    assert "heavy_bullish_lean" in result["risk_flags"], "Expected heavy_bullish_lean flag"
    assert result["directional_exposure"]["bias"] == "bullish", "Expected bullish bias"
    assert result["capital_at_risk"]["utilization_pct"] is not None, "Expected utilization_pct"
    print("\n  ✓ All scenario 1 assertions passed")


# ══════════════════════════════════════════════════════════════
#  SCENARIO 2: Diversified Portfolio
# ══════════════════════════════════════════════════════════════

def scenario_diversified():
    print("\n" + "=" * 60)
    print("  SCENARIO 2: Diversified Portfolio")
    print("=" * 60)
    print("  6 positions across 5 symbols, mixed strategies & DTEs")
    print("  Expected: no concentration flags, mixed/neutral bias")

    positions = [
        {
            "symbol": "SPY", "strategy": "put_credit_spread",
            "expiration": "2025-07-10", "dte": 5,
            "risk": 300.0, "delta": -0.20,
            "gamma": 0.01, "theta": -0.04, "vega": 0.08,
        },
        {
            "symbol": "QQQ", "strategy": "call_credit_spread",
            "expiration": "2025-07-25", "dte": 20,
            "risk": 250.0, "delta": 0.15,
            "gamma": 0.01, "theta": -0.03, "vega": 0.07,
        },
        {
            "symbol": "IWM", "strategy": "iron_condor",
            "expiration": "2025-08-15", "dte": 41,
            "risk": 400.0, "delta": 0.0,
            "gamma": 0.02, "theta": -0.06, "vega": 0.12,
        },
        {
            "symbol": "DIA", "strategy": "put_credit_spread",
            "expiration": "2025-09-19", "dte": 76,
            "risk": 350.0, "delta": -0.18,
            "gamma": 0.01, "theta": -0.03, "vega": 0.09,
        },
        {
            "symbol": "AAPL", "strategy": "call_debit",
            "expiration": "2025-10-17", "dte": 104,
            "risk": 200.0, "delta": 0.60,
            "gamma": 0.03, "theta": -0.02, "vega": 0.15,
        },
        {
            "symbol": "MSFT", "strategy": "put_credit_spread",
            "expiration": "2025-12-19", "dte": 167,
            "risk": 300.0, "delta": -0.15,
            "gamma": 0.01, "theta": -0.02, "vega": 0.11,
        },
    ]

    result = build_portfolio_exposure(positions, account_equity=100000)

    _print_section("Status & Counts", {
        "status": result["status"],
        "position_count": result["position_count"],
        "underlying_count": result["underlying_count"],
    })
    _print_section("Portfolio Summary", result["portfolio_summary"])
    _print_section("Directional Exposure", result["directional_exposure"])
    _print_section("Underlying Concentration", result["underlying_concentration"])
    _print_section("Expiration Concentration", result["expiration_concentration"])
    _print_section("Correlation Exposure", result["correlation_exposure"])
    _print_section("Risk Flags", result["risk_flags"])
    _print_section("Warning Flags", result["warning_flags"])
    _print_section("Capital at Risk", result["capital_at_risk"])

    # ── Verify expected ──
    assert result["underlying_concentration"]["concentrated"] is False, "Should not be concentrated"
    assert "underlying_concentrated" not in result["risk_flags"], "No underlying flag expected"
    assert result["capital_at_risk"]["utilization_pct"] is not None
    assert result["capital_at_risk"]["utilization_pct"] < 0.10, "Low utilization expected"
    print("\n  ✓ All scenario 2 assertions passed")


# ══════════════════════════════════════════════════════════════
#  SCENARIO 3: Empty Portfolio
# ══════════════════════════════════════════════════════════════

def scenario_empty():
    print("\n" + "=" * 60)
    print("  SCENARIO 3: Empty Portfolio")
    print("=" * 60)

    result = build_portfolio_exposure([])

    _print_section("Full Output", result)

    assert result["status"] == "empty"
    assert result["position_count"] == 0
    assert result["risk_flags"] == []
    assert result["warning_flags"] == []
    print("\n  ✓ All scenario 3 assertions passed")


# ══════════════════════════════════════════════════════════════
#  SCENARIO 4: Sparse / Low-Quality Data
# ══════════════════════════════════════════════════════════════

def scenario_sparse():
    print("\n" + "=" * 60)
    print("  SCENARIO 4: Sparse Data Quality")
    print("=" * 60)
    print("  3 positions with minimal fields — only symbol")
    print("  Expected: partial status, many warnings, no fabrication")

    positions = [
        {"symbol": "SPY"},
        {"symbol": "QQQ"},
        {"symbol": "IWM"},
    ]

    result = build_portfolio_exposure(positions)

    _print_section("Status", result["status"])
    _print_section("Warning Flags", result["warning_flags"])
    _print_section("Greeks Exposure", result["greeks_exposure"])
    _print_section("Capital at Risk", result["capital_at_risk"])

    assert result["status"] == "partial", "Should be partial with missing data"
    assert result["greeks_exposure"]["coverage"] == "none"
    assert result["capital_at_risk"]["total_risk"] == 0.0
    assert result["capital_at_risk"]["utilization_pct"] is None
    assert "greeks_unavailable" in result["warning_flags"]
    assert "sector_data_unavailable" in result["warning_flags"]
    assert "risk_data_unavailable" in result["warning_flags"]
    print("\n  ✓ All scenario 4 assertions passed")


if __name__ == "__main__":
    scenario_concentrated()
    scenario_diversified()
    scenario_empty()
    scenario_sparse()
    print("\n" + "=" * 60)
    print("  ALL SCENARIOS PASSED ✓")
    print("=" * 60)
