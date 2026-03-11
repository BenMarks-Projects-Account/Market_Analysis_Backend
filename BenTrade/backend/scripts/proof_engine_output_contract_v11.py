"""Proof script for Market Engine Output Contract v1.1 — Second Pass.

Demonstrates four scenarios with concrete sample output:
  1. Success  — fully normalized payload
  2. Degraded — partial data, low signal quality
  3. Error    — engine failure
  4. Legacy   — old cached payload without normalized key

Run from backend/:
  python -m scripts.proof_engine_output_contract_v11
"""

import json
from datetime import datetime, timezone, timedelta

from app.services.engine_output_contract import (
    REQUIRED_FIELDS,
    normalize_engine_output,
    build_error_output,
    build_degraded_output,
    detect_legacy_payload,
    normalize_legacy_payload,
    validate_normalized_output,
)


def scenario_success():
    """Scenario 1: Fully normalized success payload."""
    payload = {
        "engine_result": {
            "engine": "breadth_participation",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "score": 72.5,
            "label": "Healthy Breadth",
            "short_label": "Healthy",
            "confidence_score": 85.0,
            "signal_quality": "high",
            "universe": {"name": "S&P 500", "expected_count": 503,
                          "actual_count": 498, "coverage_pct": 99.0},
            "pillar_scores": {
                "participation_breadth": 78.0,
                "trend_breadth": 65.0,
                "volume_breadth": 70.0,
            },
            "pillar_weights": {
                "participation_breadth": 0.30,
                "trend_breadth": 0.20,
                "volume_breadth": 0.15,
            },
            "pillar_explanations": {
                "participation_breadth": "Broad participation.",
                "trend_breadth": "Positive but not accelerating.",
                "volume_breadth": "Volume confirming price action.",
            },
            "diagnostics": {"pillar_details": {}},
            "summary": "Breadth is healthy with broad participation.",
            "trader_takeaway": "Conditions support directional strategies.",
            "positive_contributors": ["Strong A/D breadth", "Tech leadership"],
            "negative_contributors": ["Volume lagging"],
            "conflicting_signals": [],
            "warnings": [],
            "missing_inputs": [],
        },
        "data_quality": {
            "signal_quality": "high",
            "confidence_score": 85.0,
            "missing_inputs_count": 0,
            "warning_count": 0,
        },
        "compute_duration_s": 0.45,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
    return normalize_engine_output("breadth_participation", payload)


def scenario_degraded():
    """Scenario 2: Degraded payload with partial data."""
    payload = {
        "engine_result": {
            "engine": "volatility_options",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "score": 55.0,
            "label": "Uncertain",
            "short_label": "Uncertain",
            "confidence_score": 40.0,
            "signal_quality": "low",
            "pillar_scores": {"volatility_regime": 55.0},
            "pillar_weights": {"volatility_regime": 0.25},
            "pillar_explanations": {"volatility_regime": "Mixed signals."},
            "diagnostics": {"pillar_details": {}},
            "summary": "Mixed signals in volatility environment.",
            "trader_takeaway": "Reduce position size.",
            "positive_contributors": [],
            "negative_contributors": ["Skew elevated"],
            "conflicting_signals": ["VIX vs realized"],
            "warnings": ["VIX source delayed", "Skew data stale",
                          "Structure incomplete"],
            "missing_inputs": ["vix_futures_curve", "realized_vol_10d"],
        },
        "data_quality": {
            "signal_quality": "low",
            "confidence_score": 40.0,
            "missing_inputs_count": 2,
            "warning_count": 3,
        },
        "compute_duration_s": 0.3,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
    return normalize_engine_output("volatility_options", payload)


def scenario_error():
    """Scenario 3: Engine failure — error payload."""
    return build_error_output(
        "cross_asset_macro",
        "FRED API connection timeout after 30s",
        exception_type="ConnectionError",
    )


def scenario_legacy_cache():
    """Scenario 4: Legacy cached payload without normalized key."""
    legacy_payload = {
        "engine_result": {
            "score": 60.0,
            "label": "Supportive Conditions",
            "short_label": "Supportive",
            "confidence_score": 50.0,
            "signal_quality": "medium",
            "summary": "From legacy cache.",
            "trader_takeaway": "Conditions support risk-on.",
            "warnings": ["stale data warning"],
            "missing_inputs": [],
        },
        "data_quality": {
            "signal_quality": "medium",
            "confidence_score": 50.0,
            "missing_inputs_count": 0,
            "warning_count": 1,
        },
        "compute_duration_s": 0.2,
        "as_of": "2026-01-01T12:00:00Z",
    }

    is_legacy, reasons = detect_legacy_payload(legacy_payload)
    print(f"  Legacy detection: is_legacy={is_legacy}, reasons={reasons}")

    return normalize_legacy_payload(
        "liquidity_financial_conditions", legacy_payload
    )


def _print_scenario(name, output):
    """Print scenario output with validation and summary."""
    print(f"\n{'─' * 60}")
    print(f"  {name}")
    print(f"{'─' * 60}\n")
    print(json.dumps(output, indent=2, default=str))

    ok, errors = validate_normalized_output(output)
    print(f"\n  Validation:      {'PASS' if ok else 'FAIL'}")
    if errors:
        print(f"  Errors:          {errors}")
    print(f"  engine_status:   {output['engine_status']}")
    print(f"  score:           {output['score']}")
    print(f"  label:           {output['label']}")
    print(f"  signal_quality:  {output['signal_quality']}")
    print(f"  status_detail:   {json.dumps(output['status_detail'], indent=4)}")

    missing_fields = REQUIRED_FIELDS - set(output.keys())
    if missing_fields:
        print(f"  MISSING FIELDS:  {missing_fields}")
    else:
        print(f"  All {len(REQUIRED_FIELDS)} required fields present")


def main():
    print("=" * 60)
    print("  PROOF: Market Engine Output Contract v1.1")
    print("=" * 60)

    scenarios = [
        ("Scenario 1: SUCCESS", scenario_success()),
        ("Scenario 2: DEGRADED", scenario_degraded()),
        ("Scenario 3: ERROR", scenario_error()),
        ("Scenario 4: LEGACY CACHE FALLBACK", scenario_legacy_cache()),
    ]

    all_ok = True
    for name, output in scenarios:
        _print_scenario(name, output)
        ok, _ = validate_normalized_output(output)
        if not ok:
            all_ok = False

    print(f"\n{'=' * 60}")
    assert all_ok, "Some scenarios failed validation!"
    print("  ALL 4 SCENARIOS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
