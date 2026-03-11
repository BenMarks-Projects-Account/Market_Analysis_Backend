"""Proof script for Scanner Candidate Contract — Second Pass (Step 2).

Demonstrates normalized outputs for:
  1. Stock candidate  (pullback swing)
  2. Options candidate — put credit spread (full data)
  3. Options candidate — iron condor (degraded / incomplete data)
  4. Options candidate — calendar spread (all fields populated)

Also shows confidence derivation differences between scenarios.

Run from backend/:
  python -m scripts.proof_scanner_candidate_contract_v2
"""

import json

from app.services.scanner_candidate_contract import (
    REQUIRED_FIELDS,
    normalize_candidate_output,
)


def _stock_candidate() -> dict:
    return {
        "symbol": "MSFT",
        "strategy_id": "stock_pullback_swing",
        "trade_type": "stock_long",
        "trade_key": "MSFT|STOCK|stock_pullback_swing|NA|NA|NA",
        "price": 420.50,
        "underlying_price": 420.50,
        "entry_reference": 420.50,
        "composite_score": 78.2,
        "score_breakdown": {"trend": 28.0, "pullback": 22.0, "reset": 16.0, "liquidity": 12.2},
        "metrics": {"sma20": 415.0, "sma50": 405.0, "atr_pct": 0.018, "rsi14": 38.5},
        "thesis": ["Strong pullback to 20-SMA", "RSI oversold reset", "Volume confirmed"],
        "risk_notes": [],
        "as_of": "2026-03-10T14:00:00+00:00",
        "data_source": {"history": "tradier", "confidence": 0.95},
        "trend_state": "uptrend",
        "trend_score": 28.0,
        "pullback_score": 22.0,
        "reset_score": 16.0,
        "liquidity_score": 12.2,
    }


def _options_full() -> dict:
    return {
        "symbol": "SPY",
        "underlying": "SPY",
        "strategy_id": "put_credit_spread",
        "trade_key": "SPY|put_credit_spread|2026-04-17|540|535|33",
        "trade_id": "SPY|put_credit_spread|2026-04-17|540|535|33",
        "expiration": "2026-04-17",
        "dte": 33,
        "short_strike": 540.0,
        "long_strike": 535.0,
        "price": 590.0,
        "underlying_price": 590.0,
        "composite_score": 82.7,
        "rank_score": 82.7,
        "computed": {
            "max_profit": 130.0, "max_loss": -370.0, "pop": 0.74,
            "return_on_risk": 0.351, "expected_value": 28.50,
            "kelly_fraction": 0.15, "iv_rank": 52.0, "bid_ask_pct": 0.06,
            "ev_to_risk": 0.077,
        },
        "details": {
            "break_even": 538.70, "dte": 33.0, "market_regime": "neutral",
            "trade_quality_score": 80.0,
        },
        "pills": {"strategy_label": "Put Credit Spread", "dte": 33, "pop": 0.74},
        "pricing": {"spread_mid": 1.30, "spread_natural": 1.15, "spread_mark": 1.225},
        "computed_metrics": {"max_profit": 130.0, "max_loss": -370.0, "pop": 0.74},
        "metrics_status": {"ready": True, "missing_fields": []},
        "validation_warnings": [],
        "engine_gate_status": {"passed": True, "failed_reasons": []},
        "legs": [
            {"name": "short_put", "right": "put", "side": "sell", "strike": 540.0,
             "bid": 3.30, "ask": 3.60, "mid": 3.45, "delta": -0.26, "iv": 0.21},
            {"name": "long_put", "right": "put", "side": "buy", "strike": 535.0,
             "bid": 2.10, "ask": 2.40, "mid": 2.25, "delta": -0.20, "iv": 0.22},
        ],
        "tie_breaks": {"ev_to_risk": 0.077, "pop": 0.74},
        "as_of": "2026-03-10T14:30:00+00:00",
    }


def _options_degraded() -> dict:
    """Iron condor with missing data to show degraded confidence."""
    return {
        "symbol": "IWM",
        "underlying": "IWM",
        "strategy_id": "iron_condor",
        "trade_key": "IWM|iron_condor|2026-04-17|220|215|200|195|33",
        "expiration": "2026-04-17",
        "dte": 33,
        "short_strike": 220.0,
        "long_strike": 215.0,
        "price": 210.0,
        "underlying_price": 210.0,
        "composite_score": 55.0,
        "computed": {
            "max_profit": 80.0,
            "max_loss": -420.0,
            "pop": 0.60,
            # missing: return_on_risk, expected_value, iv_rank, bid_ask_pct
        },
        "details": {"dte": 33.0},
        "pills": {"strategy_label": "Iron Condor"},
        "pricing": {},  # no pricing
        "metrics_status": {"ready": False, "missing_fields": ["iv_rank", "bid_ask_pct"]},
        "validation_warnings": ["STALE:iv_data", "LOW_OI:short_call"],
        "legs": [],  # no legs
        "as_of": "2026-03-10T14:30:00+00:00",
    }


def _options_calendar() -> dict:
    return {
        "symbol": "QQQ",
        "underlying": "QQQ",
        "strategy_id": "calendar_spread",
        "trade_key": "QQQ|calendar_spread|2026-05-15|480|480|60",
        "expiration": "2026-05-15",
        "dte": 60,
        "short_strike": 480.0,
        "long_strike": 480.0,
        "price": 485.0,
        "underlying_price": 485.0,
        "composite_score": 70.3,
        "rank_score": 70.3,
        "computed": {
            "max_profit": 150.0, "max_loss": -250.0, "pop": 0.65,
            "return_on_risk": 0.60, "expected_value": 35.0,
            "kelly_fraction": 0.10, "iv_rank": 38.0, "bid_ask_pct": 0.09,
            "ev_to_risk": 0.14,
        },
        "details": {"break_even": 481.50, "dte": 60.0, "market_regime": "bullish"},
        "pills": {"strategy_label": "Calendar Spread", "dte": 60, "pop": 0.65},
        "pricing": {"spread_mid": 2.50, "spread_natural": 2.30, "spread_mark": 2.40},
        "computed_metrics": {"max_profit": 150.0, "max_loss": -250.0, "pop": 0.65},
        "metrics_status": {"ready": True, "missing_fields": []},
        "validation_warnings": [],
        "engine_gate_status": {"passed": True, "failed_reasons": []},
        "legs": [
            {"name": "short_call", "right": "call", "side": "sell", "strike": 480.0,
             "bid": 5.0, "ask": 5.50, "mid": 5.25, "delta": 0.50, "iv": 0.20},
            {"name": "long_call", "right": "call", "side": "buy", "strike": 480.0,
             "bid": 7.20, "ask": 7.80, "mid": 7.50, "delta": 0.52, "iv": 0.22},
        ],
        "as_of": "2026-03-10T14:30:00+00:00",
    }


def _print_scenario(name: str, output: dict):
    print(f"\n{'─' * 60}")
    print(f"  {name}")
    print(f"{'─' * 60}\n")
    print(json.dumps(output, indent=2, default=str))

    missing = REQUIRED_FIELDS - set(output.keys())
    print(f"\n  setup_quality:  {output['setup_quality']}")
    print(f"  confidence:     {output['confidence']}")
    print(f"  direction:      {output['direction']}")
    print(f"  time_horizon:   {output['time_horizon']}")
    if missing:
        print(f"  MISSING FIELDS: {missing}")
    else:
        print(f"  All {len(REQUIRED_FIELDS)} required fields present")


def main():
    print("=" * 60)
    print("  PROOF: Scanner Candidate Contract — Second Pass")
    print("=" * 60)

    scenarios = [
        ("1. STOCK — Pullback Swing (MSFT)",
         normalize_candidate_output("stock_pullback_swing", _stock_candidate())),
        ("2. OPTIONS — Put Credit Spread (SPY, full data)",
         normalize_candidate_output("put_credit_spread", _options_full())),
        ("3. OPTIONS — Iron Condor (IWM, degraded data)",
         normalize_candidate_output("iron_condor", _options_degraded())),
        ("4. OPTIONS — Calendar Spread (QQQ, complete)",
         normalize_candidate_output("calendar_spread", _options_calendar())),
    ]

    all_ok = True
    for name, output in scenarios:
        _print_scenario(name, output)
        missing = REQUIRED_FIELDS - set(output.keys())
        if missing:
            all_ok = False

    # Confidence comparison
    print(f"\n{'─' * 60}")
    print("  CONFIDENCE COMPARISON")
    print(f"{'─' * 60}")
    for name, output in scenarios:
        label = name.split("—")[1].strip() if "—" in name else name
        print(f"  {label:45s}  confidence={output['confidence']:.2f}  "
              f"setup_quality={output['setup_quality']}")

    print(f"\n{'=' * 60}")
    assert all_ok, "Some scenarios have missing fields!"
    print("  ALL 4 SCENARIOS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
