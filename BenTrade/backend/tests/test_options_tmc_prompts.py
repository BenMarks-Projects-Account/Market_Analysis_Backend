"""Tests for the options TMC final decision prompt module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from common.options_tmc_prompts import (
    OPTIONS_TMC_FINAL_DECISION_SYSTEM_PROMPT,
    OPTIONS_TMC_TEMPERATURE,
    build_options_tmc_user_prompt,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _sample_candidate() -> dict:
    return {
        "symbol": "SPY",
        "strategy_id": "put_credit_spread",
        "scanner_key": "put_credit_spread",
        "family_key": "vertical_spreads",
        "expiration": "2026-04-17",
        "dte": 25,
        "underlying_price": 560.50,
        "regime_alignment": "aligned",
        "regime_warning": None,
        "event_risk": "low",
        "event_details": ["FOMC 2026-04-29 (outside DTE window)"],
        "rank": 3,
        "rank_score": 78.5,
        "math": {
            "net_credit": 0.45,
            "net_debit": None,
            "max_profit": 45.0,
            "max_loss": -455.0,
            "width": 5.0,
            "pop": 0.72,
            "pop_source": "delta_approx",
            "ev": 12.5,
            "ev_per_day": 0.5,
            "ror": 0.099,
            "kelly": 0.12,
            "breakeven": [555.55],
        },
        "legs": [
            {
                "index": 0,
                "side": "short",
                "strike": 556.0,
                "option_type": "put",
                "expiration": "2026-04-17",
                "bid": 2.10,
                "ask": 2.15,
                "mid": 2.125,
                "delta": -0.28,
                "gamma": 0.008,
                "theta": -0.05,
                "vega": 0.15,
                "iv": 0.18,
                "open_interest": 5400,
                "volume": 320,
            },
            {
                "index": 1,
                "side": "long",
                "strike": 551.0,
                "option_type": "put",
                "expiration": "2026-04-17",
                "bid": 1.65,
                "ask": 1.70,
                "mid": 1.675,
                "delta": -0.22,
                "gamma": 0.007,
                "theta": -0.04,
                "vega": 0.13,
                "iv": 0.19,
                "open_interest": 3200,
                "volume": 180,
            },
        ],
    }


def _sample_market_context() -> dict:
    return {
        "market_state": "RISK_ON",
        "regime_score": 72,
        "vix": 14.5,
    }


# ---------------------------------------------------------------------------
# System Prompt Tests
# ---------------------------------------------------------------------------

class TestOptionsSystemPrompt:
    def test_anti_injection_preamble_present(self):
        assert OPTIONS_TMC_FINAL_DECISION_SYSTEM_PROMPT.startswith("SECURITY:")

    def test_contains_execute_pass_instructions(self):
        assert '"EXECUTE"' in OPTIONS_TMC_FINAL_DECISION_SYSTEM_PROMPT
        assert '"PASS"' in OPTIONS_TMC_FINAL_DECISION_SYSTEM_PROMPT

    def test_contains_pop_threshold(self):
        assert "0.65" in OPTIONS_TMC_FINAL_DECISION_SYSTEM_PROMPT

    def test_contains_credit_to_width_threshold(self):
        assert "0.15" in OPTIONS_TMC_FINAL_DECISION_SYSTEM_PROMPT
        assert "15%" in OPTIONS_TMC_FINAL_DECISION_SYSTEM_PROMPT

    def test_contains_conviction_rule(self):
        assert "conviction below 60" in OPTIONS_TMC_FINAL_DECISION_SYSTEM_PROMPT.lower()

    def test_contains_json_formatting_rules(self):
        assert "raw JSON" in OPTIONS_TMC_FINAL_DECISION_SYSTEM_PROMPT
        assert "No markdown fences" in OPTIONS_TMC_FINAL_DECISION_SYSTEM_PROMPT

    def test_output_schema_has_required_fields(self):
        prompt = OPTIONS_TMC_FINAL_DECISION_SYSTEM_PROMPT
        for field in [
            "recommendation",
            "conviction",
            "score",
            "headline",
            "narrative",
            "structure_analysis",
            "probability_assessment",
            "greeks_assessment",
            "market_alignment",
            "caution_points",
            "key_factors",
            "suggested_adjustment",
        ]:
            assert field in prompt, f"Missing field: {field}"

    def test_temperature_is_zero(self):
        assert OPTIONS_TMC_TEMPERATURE == 0.0


# ---------------------------------------------------------------------------
# User Prompt Builder Tests
# ---------------------------------------------------------------------------

class TestBuildOptionsUserPrompt:
    def test_returns_valid_json(self):
        result = build_options_tmc_user_prompt(_sample_candidate(), _sample_market_context())
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_top_level_keys(self):
        result = build_options_tmc_user_prompt(_sample_candidate(), _sample_market_context())
        parsed = json.loads(result)
        expected_keys = {
            "trade_structure",
            "pricing",
            "probability",
            "legs",
            "market_context",
            "risk_assessment",
            "ranking",
            "decision_prompt",
        }
        assert expected_keys == set(parsed.keys())

    def test_trade_structure_fields(self):
        result = build_options_tmc_user_prompt(_sample_candidate())
        parsed = json.loads(result)
        ts = parsed["trade_structure"]
        assert ts["symbol"] == "SPY"
        assert ts["strategy"] == "put_credit_spread"
        assert ts["dte"] == 25
        assert ts["width"] == 5.0
        assert "Put Credit Spread" in ts["strategy_description"]

    def test_pricing_fields(self):
        result = build_options_tmc_user_prompt(_sample_candidate())
        parsed = json.loads(result)
        p = parsed["pricing"]
        assert p["net_credit"] == 0.45
        assert p["max_profit"] == 45.0
        assert p["max_loss"] == -455.0
        assert p["breakeven"] == [555.55]

    def test_probability_fields(self):
        result = build_options_tmc_user_prompt(_sample_candidate())
        parsed = json.loads(result)
        prob = parsed["probability"]
        assert prob["pop"] == 0.72
        assert prob["ev"] == 12.5
        assert prob["ev_per_day"] == 0.5
        assert prob["ror"] == 0.099
        assert prob["kelly"] == 0.12

    def test_legs_serialized(self):
        result = build_options_tmc_user_prompt(_sample_candidate())
        parsed = json.loads(result)
        legs = parsed["legs"]
        assert len(legs) == 2
        short_leg = legs[0]
        assert short_leg["side"] == "short"
        assert short_leg["strike"] == 556.0
        assert short_leg["delta"] == -0.28
        assert short_leg["iv"] == 0.18
        long_leg = legs[1]
        assert long_leg["side"] == "long"
        assert long_leg["strike"] == 551.0

    def test_market_context_populated(self):
        result = build_options_tmc_user_prompt(
            _sample_candidate(), _sample_market_context()
        )
        parsed = json.loads(result)
        mc = parsed["market_context"]
        assert mc["regime"] == "RISK_ON"
        assert mc["vix"] == 14.5
        assert mc["underlying_price"] == 560.50

    def test_market_context_missing(self):
        """When no market_context is passed, all fields should be null."""
        result = build_options_tmc_user_prompt(_sample_candidate())
        parsed = json.loads(result)
        mc = parsed["market_context"]
        assert mc["regime"] is None
        assert mc["vix"] is None
        assert mc["underlying_price"] == 560.50  # comes from candidate

    def test_risk_assessment(self):
        result = build_options_tmc_user_prompt(_sample_candidate())
        parsed = json.loads(result)
        ra = parsed["risk_assessment"]
        assert ra["regime_alignment"] == "aligned"
        assert ra["event_risk"] == "low"

    def test_ranking(self):
        result = build_options_tmc_user_prompt(_sample_candidate())
        parsed = json.loads(result)
        assert parsed["ranking"]["rank"] == 3
        assert parsed["ranking"]["rank_score"] == 78.5

    def test_empty_candidate_produces_valid_json(self):
        """Degenerate input should still produce valid JSON, not crash."""
        result = build_options_tmc_user_prompt({})
        parsed = json.loads(result)
        assert parsed["trade_structure"]["symbol"] is None
        assert parsed["legs"] == []

    def test_iron_condor_four_legs(self):
        """Iron condor with 4 legs should serialize correctly."""
        cand = _sample_candidate()
        cand["strategy_id"] = "iron_condor"
        cand["legs"] = [
            {"index": 0, "side": "short", "strike": 545.0, "option_type": "put"},
            {"index": 1, "side": "long", "strike": 540.0, "option_type": "put"},
            {"index": 2, "side": "short", "strike": 575.0, "option_type": "call"},
            {"index": 3, "side": "long", "strike": 580.0, "option_type": "call"},
        ]
        result = build_options_tmc_user_prompt(cand)
        parsed = json.loads(result)
        assert len(parsed["legs"]) == 4
        assert "Iron Condor" in parsed["trade_structure"]["strategy_description"]

    def test_calendar_strategy_description(self):
        cand = _sample_candidate()
        cand["strategy_id"] = "calendar_call_spread"
        result = build_options_tmc_user_prompt(cand)
        parsed = json.loads(result)
        assert "Calendar Call" in parsed["trade_structure"]["strategy_description"]

    def test_unknown_strategy_fallback(self):
        cand = _sample_candidate()
        cand["strategy_id"] = "some_new_strategy"
        result = build_options_tmc_user_prompt(cand)
        parsed = json.loads(result)
        assert "some new strategy" in parsed["trade_structure"]["strategy_description"]
