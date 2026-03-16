"""
Tests for Active Trade Pipeline v1 — deterministic engine, packet builder,
normalizer, model layer, and pipeline runner.

Validates:
  1. build_reassessment_packet — output shape, degraded tracking, missing data
  2. run_analysis_engine — component scores, risk flags, thresholds, edge cases
  3. normalize_recommendation — model preferred, engine fallback, default fallback
  4. run_model_analysis — with stub executor, degraded mode
  5. _build_engine_rationale — text from engine output
  6. run_active_trade_pipeline — async, stub executor, empty trades, mixed results
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# ── ensure importable ───────────────────────────────────────────────
_backend = Path(__file__).resolve().parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from app.services.active_trade_pipeline import (
    RECOMMENDATION_HOLD,
    RECOMMENDATION_REDUCE,
    RECOMMENDATION_CLOSE,
    RECOMMENDATION_URGENT_REVIEW,
    VALID_RECOMMENDATIONS,
    ENGINE_WEIGHTS,
    ENGINE_THRESHOLDS,
    ATP_STAGES,
    ATP_DEPENDENCY_MAP,
    _check_dependencies,
    build_reassessment_packet,
    run_analysis_engine,
    run_model_analysis,
    normalize_recommendation,
    _build_engine_rationale,
    _to_float,
    _to_int,
)


# ═════════════════════════════════════════════════════════════════════
#  Fixtures / helpers
# ═════════════════════════════════════════════════════════════════════

def _base_trade(**overrides):
    """Minimal active trade dict."""
    trade = {
        "symbol": "SPY",
        "strategy": "credit_put_spread",
        "strategy_id": "credit_put_spread",
        "trade_key": "spy-cps-20260601",
        "trade_id": "t-001",
        "dte": 30,
        "short_strike": 400.0,
        "long_strike": 395.0,
        "expiration": "2026-06-01",
        "quantity": 1,
        "legs": [],
        "status": "OPEN",
        "avg_open_price": 2.50,
        "mark_price": 1.80,
        "unrealized_pnl": 70.0,
        "unrealized_pnl_pct": 0.04,
        "cost_basis_total": 250.0,
        "market_value": 180.0,
        "spread_type": "vertical",
    }
    trade.update(overrides)
    return trade


def _base_market(**overrides):
    d = {"regime_label": "RISK_ON", "regime_score": 75, "vix": 15.0}
    d.update(overrides)
    return d


def _base_monitor(**overrides):
    d = {
        "symbol": "SPY",
        "status": "HEALTHY",
        "score_0_100": 72,
        "breakdown": {},
        "triggers": [],
        "recommended_action": "MONITOR",
    }
    d.update(overrides)
    return d


def _base_indicators(**overrides):
    d = {"sma20": 420.0, "sma50": 415.0, "rsi14": 55.0}
    d.update(overrides)
    return d


def _full_packet(**trade_overrides):
    """Build a packet with all defaults."""
    return build_reassessment_packet(
        _base_trade(**trade_overrides),
        _base_market(),
        _base_monitor(),
        _base_indicators(),
    )


def _stub_model_executor(result_dict=None):
    """Return a model executor stub that returns a fixed result."""
    def executor(payload, rendered_text):
        if result_dict is not None:
            return {
                "status": "success",
                "raw_response": result_dict,
                "provider": "test",
                "model_name": "stub-model",
                "latency_ms": 42,
                "error": None,
                "metadata": {},
            }
        return {
            "status": "error",
            "raw_response": {},
            "provider": "test",
            "model_name": "stub-model",
            "latency_ms": 10,
            "error": "stub_unavailable",
            "metadata": {},
        }
    return executor


# ═════════════════════════════════════════════════════════════════════
#  1. build_reassessment_packet
# ═════════════════════════════════════════════════════════════════════

class TestBuildReassessmentPacket:
    """Reassessment packet: shape, field mapping, degraded tracking."""

    def test_full_packet_shape(self):
        pkt = _full_packet()
        assert pkt["packet_version"] == "1.0"
        assert pkt["symbol"] == "SPY"
        for section in ("identity", "position", "market", "monitor", "indicators", "data_quality"):
            assert section in pkt, f"missing section: {section}"

    def test_identity_fields(self):
        pkt = _full_packet()
        ident = pkt["identity"]
        assert ident["symbol"] == "SPY"
        assert ident["strategy"] == "credit_put_spread"
        assert ident["dte"] == 30
        assert ident["short_strike"] == 400.0
        assert ident["long_strike"] == 395.0
        assert ident["trade_status"] == "OPEN"

    def test_position_fields(self):
        pkt = _full_packet()
        pos = pkt["position"]
        assert pos["avg_open_price"] == 2.50
        assert pos["mark_price"] == 1.80
        assert pos["unrealized_pnl"] == 70.0
        assert pos["unrealized_pnl_pct"] == 0.04

    def test_market_fields(self):
        pkt = _full_packet()
        mkt = pkt["market"]
        assert mkt["regime_label"] == "RISK_ON"
        assert mkt["regime_score"] == 75.0

    def test_not_degraded_when_full(self):
        pkt = _full_packet()
        dq = pkt["data_quality"]
        assert dq["is_degraded"] is False
        assert dq["degraded_count"] == 0
        assert dq["degraded_fields"] == []

    def test_degraded_when_missing_mark(self):
        pkt = build_reassessment_packet(
            _base_trade(mark_price=None),
            _base_market(),
            _base_monitor(),
            _base_indicators(),
        )
        dq = pkt["data_quality"]
        assert dq["is_degraded"] is True
        assert "mark_price" in dq["degraded_fields"]

    def test_degraded_when_no_monitor(self):
        pkt = build_reassessment_packet(
            _base_trade(),
            _base_market(),
            None,
            _base_indicators(),
        )
        assert "monitor_result" in pkt["data_quality"]["degraded_fields"]

    def test_degraded_when_no_market(self):
        pkt = build_reassessment_packet(
            _base_trade(),
            {},
            _base_monitor(),
            _base_indicators(),
        )
        dq = pkt["data_quality"]
        assert "market_context" in dq["degraded_fields"]

    def test_degraded_when_no_indicators(self):
        pkt = build_reassessment_packet(
            _base_trade(),
            _base_market(),
            _base_monitor(),
            None,
        )
        assert "indicators" in pkt["data_quality"]["degraded_fields"]

    def test_derives_pnl_pct_when_missing(self):
        """If unrealized_pnl_pct is absent, derive from unrealized_pnl / |cost_basis|."""
        pkt = build_reassessment_packet(
            _base_trade(unrealized_pnl=50.0, unrealized_pnl_pct=None, cost_basis_total=200.0),
            _base_market(),
            _base_monitor(),
            _base_indicators(),
        )
        assert pkt["position"]["unrealized_pnl_pct"] == pytest.approx(0.25)

    def test_symbol_uppercased(self):
        pkt = build_reassessment_packet(
            _base_trade(symbol="spy"),
            _base_market(),
            _base_monitor(),
            _base_indicators(),
        )
        assert pkt["symbol"] == "SPY"
        assert pkt["identity"]["symbol"] == "SPY"


# ═════════════════════════════════════════════════════════════════════
#  2. run_analysis_engine
# ═════════════════════════════════════════════════════════════════════

class TestRunAnalysisEngine:
    """Deterministic engine: component scoring, flags, thresholds."""

    def test_healthy_trade_is_hold(self):
        pkt = _full_packet()
        out = run_analysis_engine(pkt)
        assert out["engine_recommendation"] == RECOMMENDATION_HOLD
        assert out["trade_health_score"] >= 70

    def test_significant_loss_flag(self):
        pkt = _full_packet(unrealized_pnl_pct=-0.12)
        out = run_analysis_engine(pkt)
        assert "SIGNIFICANT_LOSS" in out["risk_flags"]

    def test_severe_loss_flag(self):
        pkt = _full_packet(unrealized_pnl_pct=-0.25)
        out = run_analysis_engine(pkt)
        assert "SEVERE_LOSS" in out["risk_flags"]
        assert "SIGNIFICANT_LOSS" in out["risk_flags"]

    def test_large_gain_flag(self):
        pkt = _full_packet(unrealized_pnl_pct=0.55)
        out = run_analysis_engine(pkt)
        assert "LARGE_UNREALIZED_GAIN" in out["risk_flags"]

    def test_expiry_imminent(self):
        pkt = _full_packet(dte=2)
        out = run_analysis_engine(pkt)
        assert "EXPIRY_IMMINENT" in out["risk_flags"]

    def test_expiry_near(self):
        pkt = _full_packet(dte=5)
        out = run_analysis_engine(pkt)
        assert "EXPIRY_NEAR" in out["risk_flags"]

    def test_regime_adverse_for_credit_put_in_risk_off(self):
        pkt = build_reassessment_packet(
            _base_trade(),
            _base_market(regime_label="RISK_OFF"),
            _base_monitor(),
            _base_indicators(),
        )
        out = run_analysis_engine(pkt)
        assert "REGIME_ADVERSE" in out["risk_flags"]

    def test_pnl_zero_gives_70(self):
        pkt = _full_packet(unrealized_pnl_pct=0.0)
        out = run_analysis_engine(pkt)
        assert out["component_scores"]["pnl_health"] == 70.0

    def test_pnl_10pct_gives_95(self):
        pkt = _full_packet(unrealized_pnl_pct=0.10)
        out = run_analysis_engine(pkt)
        assert out["component_scores"]["pnl_health"] == 95.0

    def test_pnl_minus_20pct_gives_0(self):
        pkt = _full_packet(unrealized_pnl_pct=-0.20)
        out = run_analysis_engine(pkt)
        assert out["component_scores"]["pnl_health"] == 0.0

    def test_dte_45_high_score(self):
        pkt = _full_packet(dte=45)
        out = run_analysis_engine(pkt)
        assert out["component_scores"]["time_pressure"] == 90.0

    def test_dte_0_zero_score(self):
        pkt = _full_packet(dte=0)
        out = run_analysis_engine(pkt)
        assert out["component_scores"]["time_pressure"] == 0.0

    def test_monitors_carry_forward_critical_triggers(self):
        pkt = build_reassessment_packet(
            _base_trade(),
            _base_market(),
            _base_monitor(triggers=[
                {"id": "drawdown_pct", "hit": True, "level": "CRITICAL"},
            ]),
            _base_indicators(),
        )
        out = run_analysis_engine(pkt)
        assert "MONITOR_CRITICAL_DRAWDOWN_PCT" in out["risk_flags"]

    def test_all_components_present_in_full_packet(self):
        pkt = _full_packet()
        out = run_analysis_engine(pkt)
        for key in ENGINE_WEIGHTS:
            assert key in out["component_scores"]

    def test_missing_pnl_degrades(self):
        pkt = _full_packet(unrealized_pnl_pct=None, unrealized_pnl=None)
        out = run_analysis_engine(pkt)
        assert out["component_scores"]["pnl_health"] is None
        assert "pnl_health_missing" in out["degraded_flags"]

    def test_urgency_levels(self):
        """Urgency maps to recommendation severity."""
        pkt_hold = _full_packet()
        out_hold = run_analysis_engine(pkt_hold)
        assert out_hold["urgency"] == 1  # HOLD → low urgency

    def test_critical_override(self):
        """Two critical risk flags force URGENT_REVIEW."""
        pkt = _full_packet(unrealized_pnl_pct=-0.25, dte=1)
        out = run_analysis_engine(pkt)
        critical_flags = [f for f in out["risk_flags"] if "SEVERE" in f or "IMMINENT" in f]
        assert len(critical_flags) >= 2
        assert out["engine_recommendation"] == RECOMMENDATION_URGENT_REVIEW

    def test_output_shape(self):
        """Engine output has all required keys."""
        pkt = _full_packet()
        out = run_analysis_engine(pkt)
        required = {
            "engine_version", "trade_health_score", "component_scores",
            "risk_flags", "engine_recommendation", "urgency", "degraded_flags",
        }
        assert required.issubset(set(out.keys()))

    def test_structure_health_credit_spread_mark_below_entry(self):
        """Credit spread w/ mark < avg_open → structure bonus."""
        pkt = _full_packet(avg_open_price=3.0, mark_price=2.0)
        out = run_analysis_engine(pkt)
        assert out["component_scores"]["structure_health"] == 100.0  # 80 + 10(width) + 10(mark)

    def test_structure_health_credit_spread_mark_above_entry(self):
        """Credit spread w/ mark > avg_open → structure penalty."""
        pkt = _full_packet(avg_open_price=2.0, mark_price=3.0)
        out = run_analysis_engine(pkt)
        assert out["component_scores"]["structure_health"] == 80.0  # 80 + 10(width) - 10(mark)


# ═════════════════════════════════════════════════════════════════════
#  3. normalize_recommendation
# ═════════════════════════════════════════════════════════════════════

class TestNormalizeRecommendation:
    """Recommendation normalization: model preferred, engine fallback."""

    def test_model_recommendation_preferred(self):
        pkt = _full_packet()
        engine = run_analysis_engine(pkt)
        model = {
            "model_available": True,
            "recommendation": "CLOSE",
            "conviction": 0.85,
            "rationale_summary": "Model says close",
            "key_supporting_points": ["point1"],
            "key_risks": ["risk1"],
            "market_alignment": "adverse",
            "portfolio_fit": "poor",
            "event_sensitivity": "high",
            "suggested_next_move": "Close at market open",
            "provider": "test",
            "model_name": "stub",
            "latency_ms": 50,
            "degraded_reasons": [],
        }
        rec = normalize_recommendation(_base_trade(), engine, model, pkt)
        assert rec["recommendation"] == "CLOSE"
        assert rec["recommendation_source"] == "model"
        assert rec["conviction"] == 0.85

    def test_engine_fallback_when_model_unavailable(self):
        pkt = _full_packet()
        engine = run_analysis_engine(pkt)
        model = {
            "model_available": False,
            "recommendation": None,
            "conviction": None,
            "rationale_summary": None,
            "key_supporting_points": [],
            "key_risks": [],
            "market_alignment": None,
            "portfolio_fit": None,
            "event_sensitivity": None,
            "suggested_next_move": None,
            "provider": None,
            "model_name": None,
            "latency_ms": None,
            "degraded_reasons": ["model_skipped"],
        }
        rec = normalize_recommendation(_base_trade(), engine, model, pkt)
        assert rec["recommendation_source"] == "engine"
        assert rec["recommendation"] == engine["engine_recommendation"]

    def test_default_fallback_when_both_missing(self):
        pkt = build_reassessment_packet(
            _base_trade(unrealized_pnl_pct=None, unrealized_pnl=None, dte=None),
            {},
            None,
            None,
        )
        engine = run_analysis_engine(pkt)
        model = {
            "model_available": False,
            "recommendation": None,
            "conviction": None,
            "rationale_summary": None,
            "key_supporting_points": [],
            "key_risks": [],
            "degraded_reasons": ["model_unavailable"],
        }
        # Engine might also have None recommendation if all scores are None
        rec = normalize_recommendation(_base_trade(), engine, model, pkt)
        assert rec["recommendation"] in VALID_RECOMMENDATIONS
        assert rec["recommendation_source"] in ("engine", "default")

    def test_output_has_all_required_fields(self):
        pkt = _full_packet()
        engine = run_analysis_engine(pkt)
        model = {
            "model_available": False,
            "recommendation": None,
            "conviction": None,
            "rationale_summary": None,
            "key_supporting_points": [],
            "key_risks": [],
            "degraded_reasons": ["model_skipped"],
        }
        rec = normalize_recommendation(_base_trade(), engine, model, pkt)
        required = {
            "active_trade_recommendation_version", "symbol", "strategy",
            "recommendation", "recommendation_source", "conviction",
            "urgency", "rationale_summary",
            "internal_engine_summary", "internal_engine_metrics",
            "model_summary", "position_snapshot",
            "degraded_reasons", "is_degraded",
        }
        assert required.issubset(set(rec.keys()))

    def test_conviction_mapped_from_engine_when_model_absent(self):
        pkt = _full_packet()
        engine = run_analysis_engine(pkt)
        model = {
            "model_available": False,
            "recommendation": None,
            "conviction": None,
            "rationale_summary": None,
            "key_supporting_points": [],
            "key_risks": [],
            "degraded_reasons": [],
        }
        rec = normalize_recommendation(_base_trade(), engine, model, pkt)
        # Conviction = engine_health_score / 100
        expected = engine["trade_health_score"] / 100.0
        assert rec["conviction"] == pytest.approx(expected, abs=0.01)

    def test_degraded_reasons_deduplicated(self):
        pkt = _full_packet()
        engine_out = run_analysis_engine(pkt)
        engine_out["degraded_flags"] = ["missing_field", "dup"]
        model = {
            "model_available": False,
            "recommendation": None,
            "conviction": None,
            "rationale_summary": None,
            "key_supporting_points": [],
            "key_risks": [],
            "degraded_reasons": ["dup", "model_skipped"],
        }
        rec = normalize_recommendation(_base_trade(), engine_out, model, pkt)
        # "dup" should appear only once
        assert rec["degraded_reasons"].count("dup") == 1


# ═════════════════════════════════════════════════════════════════════
#  4. run_model_analysis (with stub executors)
# ═════════════════════════════════════════════════════════════════════

class TestRunModelAnalysis:
    """Model layer: stub executor, degraded modes."""

    def test_successful_model_output(self):
        pkt = _full_packet()
        engine = run_analysis_engine(pkt)
        model_result = {
            "recommendation": "HOLD",
            "conviction": 0.75,
            "rationale_summary": "Trade looks healthy",
            "key_supporting_points": ["Favorable regime", "Low DTE risk"],
            "key_risks": ["VIX spike risk"],
            "market_alignment": "aligned",
            "portfolio_fit": "good",
            "event_sensitivity": "low",
            "suggested_next_move": "Continue monitoring",
        }
        executor = _stub_model_executor(model_result)
        out = run_model_analysis(pkt, engine, model_executor=executor)
        assert out["model_available"] is True
        assert out["recommendation"] == "HOLD"
        assert out["conviction"] == 0.75
        assert out["rationale_summary"] == "Trade looks healthy"
        assert len(out["key_supporting_points"]) == 2
        assert out["degraded_reasons"] == []

    def test_model_unavailable_degrades(self):
        pkt = _full_packet()
        engine = run_analysis_engine(pkt)
        executor = _stub_model_executor(None)  # will return error
        out = run_model_analysis(pkt, engine, model_executor=executor)
        assert out["model_available"] is False
        assert len(out["degraded_reasons"]) > 0

    def test_invalid_recommendation_set_to_none(self):
        pkt = _full_packet()
        engine = run_analysis_engine(pkt)
        model_result = {
            "recommendation": "INVALID_VALUE",
            "conviction": 0.5,
            "rationale_summary": "test",
        }
        executor = _stub_model_executor(model_result)
        out = run_model_analysis(pkt, engine, model_executor=executor)
        assert out["model_available"] is True
        assert out["recommendation"] is None  # invalid → None

    def test_conviction_clamped(self):
        pkt = _full_packet()
        engine = run_analysis_engine(pkt)
        model_result = {
            "recommendation": "HOLD",
            "conviction": 5.0,  # out of range
            "rationale_summary": "test",
        }
        executor = _stub_model_executor(model_result)
        out = run_model_analysis(pkt, engine, model_executor=executor)
        assert out["conviction"] == 1.0  # clamped to max

    def test_executor_exception_degrades(self):
        pkt = _full_packet()
        engine = run_analysis_engine(pkt)

        def failing_executor(payload, text):
            raise RuntimeError("boom")

        out = run_model_analysis(pkt, engine, model_executor=failing_executor)
        assert out["model_available"] is False
        assert "boom" in out["degraded_reasons"][0]


# ═════════════════════════════════════════════════════════════════════
#  5. _build_engine_rationale
# ═════════════════════════════════════════════════════════════════════

class TestBuildEngineRationale:
    """Engine rationale generation for model-less runs."""

    def test_basic_rationale(self):
        pkt = _full_packet()
        engine = run_analysis_engine(pkt)
        text = _build_engine_rationale(engine, pkt)
        assert "SPY" in text
        assert "HOLD" in text or "health" in text.lower()

    def test_rationale_includes_risk_flags(self):
        pkt = _full_packet(unrealized_pnl_pct=-0.15)
        engine = run_analysis_engine(pkt)
        text = _build_engine_rationale(engine, pkt)
        assert "SIGNIFICANT_LOSS" in text

    def test_rationale_includes_weak_areas(self):
        pkt = _full_packet(unrealized_pnl_pct=-0.18, dte=2)
        engine = run_analysis_engine(pkt)
        text = _build_engine_rationale(engine, pkt)
        assert "Weak areas" in text


# ═════════════════════════════════════════════════════════════════════
#  6. run_active_trade_pipeline (async)
# ═════════════════════════════════════════════════════════════════════

class _StubRegimeService:
    async def get_regime(self):
        return {"label": "NEUTRAL", "score": 50}


class _StubMonitorService:
    async def evaluate_batch(self, trades):
        return [
            {"symbol": t.get("symbol", "???"), "status": "HEALTHY",
             "score_0_100": 60, "triggers": [], "recommended_action": "MONITOR"}
            for t in trades
        ]


class _StubDataService:
    async def get_prices_history(self, symbol, lookback_days=120):
        # Return 60 fake prices (ascending)
        return [400.0 + i * 0.5 for i in range(60)]


class TestRunActiveTradePipeline:
    """Pipeline runner: async, stub executor, empty input, mixed results."""

    def test_empty_trades_returns_completed(self):
        from app.services.active_trade_pipeline import run_active_trade_pipeline
        result = asyncio.run(
            run_active_trade_pipeline(
                [],
                _StubMonitorService(),
                _StubRegimeService(),
                _StubDataService(),
            )
        )
        assert result["status"] == "completed"
        assert result["trade_count"] == 0
        assert result["recommendations"] == []
        assert result["summary"]["total_trades"] == 0

        # Stages must be present and honest
        stages = result["stages"]
        assert stages["load_positions"]["status"] == "completed"
        assert stages["market_context"]["status"] == "completed"
        assert stages["build_packets"]["status"] == "skipped"
        assert stages["engine_analysis"]["status"] == "skipped"
        assert stages["model_analysis"]["status"] == "skipped"
        assert stages["normalize"]["status"] == "skipped"
        assert stages["complete"]["status"] == "completed"
        # Market context still fetched even with 0 trades
        assert "regime_label" in stages["market_context"].get("metadata", {})

    def test_single_trade_engine_only(self):
        from app.services.active_trade_pipeline import run_active_trade_pipeline
        result = asyncio.run(
            run_active_trade_pipeline(
                [_base_trade()],
                _StubMonitorService(),
                _StubRegimeService(),
                _StubDataService(),
                skip_model=True,
            )
        )
        assert result["status"] == "completed"
        assert result["trade_count"] == 1
        assert len(result["recommendations"]) == 1

        rec = result["recommendations"][0]
        assert rec["recommendation"] in VALID_RECOMMENDATIONS
        assert rec["recommendation_source"] == "engine"
        assert rec["symbol"] == "SPY"

    def test_single_trade_with_model(self):
        from app.services.active_trade_pipeline import run_active_trade_pipeline
        model_data = {
            "recommendation": "REDUCE",
            "conviction": 0.65,
            "rationale_summary": "Consider trimming",
            "key_supporting_points": ["Time decay slowing"],
            "key_risks": ["Regime shift ahead"],
            "market_alignment": "neutral",
            "portfolio_fit": "acceptable",
            "event_sensitivity": "moderate",
            "suggested_next_move": "Close half",
        }
        executor = _stub_model_executor(model_data)

        result = asyncio.run(
            run_active_trade_pipeline(
                [_base_trade()],
                _StubMonitorService(),
                _StubRegimeService(),
                _StubDataService(),
                model_executor=executor,
            )
        )
        assert result["trade_count"] == 1
        rec = result["recommendations"][0]
        assert rec["recommendation"] == "REDUCE"
        assert rec["recommendation_source"] == "model"
        assert rec["conviction"] == 0.65

    def test_multiple_trades(self):
        from app.services.active_trade_pipeline import run_active_trade_pipeline
        trades = [
            _base_trade(symbol="SPY"),
            _base_trade(symbol="QQQ", unrealized_pnl_pct=-0.15),
        ]
        result = asyncio.run(
            run_active_trade_pipeline(
                trades,
                _StubMonitorService(),
                _StubRegimeService(),
                _StubDataService(),
                skip_model=True,
            )
        )
        assert result["trade_count"] == 2
        assert len(result["recommendations"]) == 2
        symbols = {r["symbol"] for r in result["recommendations"]}
        assert symbols == {"SPY", "QQQ"}

    def test_result_shape(self):
        from app.services.active_trade_pipeline import run_active_trade_pipeline
        result = asyncio.run(
            run_active_trade_pipeline(
                [_base_trade()],
                _StubMonitorService(),
                _StubRegimeService(),
                _StubDataService(),
                skip_model=True,
            )
        )
        required = {
            "run_id", "pipeline_version", "started_at", "ended_at",
            "duration_ms", "status", "trade_count", "recommendation_counts",
            "recommendations", "market_context_snapshot", "summary",
            "degraded_reasons", "stages", "stage_order", "dependency_graph",
        }
        assert required.issubset(set(result.keys()))

    def test_stages_present_with_trades(self):
        from app.services.active_trade_pipeline import run_active_trade_pipeline
        result = asyncio.run(
            run_active_trade_pipeline(
                [_base_trade()],
                _StubMonitorService(),
                _StubRegimeService(),
                _StubDataService(),
                skip_model=True,
            )
        )
        stages = result["stages"]
        expected_keys = {
            "load_positions", "market_context", "build_packets",
            "engine_analysis", "model_analysis", "normalize", "complete",
        }
        assert expected_keys == set(stages.keys())

        # All non-skipped stages have timing
        for key in ("load_positions", "market_context", "build_packets",
                     "engine_analysis", "normalize", "complete"):
            assert stages[key]["status"] == "completed"
            assert isinstance(stages[key]["duration_ms"], int)

        # model_analysis should be skipped when skip_model=True
        assert stages["model_analysis"]["status"] == "skipped"

        # Metadata should contain real data
        assert stages["load_positions"]["metadata"]["positions_loaded"] == 1
        assert stages["build_packets"]["metadata"]["packets_built"] == 1
        assert stages["engine_analysis"]["metadata"]["trades_analyzed"] == 1


# ═════════════════════════════════════════════════════════════════════
#  7. Utility functions
# ═════════════════════════════════════════════════════════════════════

class TestUtilities:
    """_to_float, _to_int edge cases."""

    def test_to_float_none(self):
        assert _to_float(None) is None

    def test_to_float_str(self):
        assert _to_float("3.14") == pytest.approx(3.14)

    def test_to_float_nan(self):
        assert _to_float(float("nan")) is None

    def test_to_float_invalid(self):
        assert _to_float("not_a_number") is None

    def test_to_int_none(self):
        assert _to_int(None) is None

    def test_to_int_from_float(self):
        assert _to_int(3.7) == 3

    def test_to_int_from_string(self):
        assert _to_int("42") == 42


# ═════════════════════════════════════════════════════════════════════
#  8. Dependency graph & enforcement
# ═════════════════════════════════════════════════════════════════════

class TestDependencyGraph:
    """ATP_STAGES ordering, ATP_DEPENDENCY_MAP consistency, and
    _check_dependencies enforcement."""

    def test_stages_tuple_matches_dependency_map_keys(self):
        """Every stage in the ordered tuple has a dependency-map entry."""
        assert set(ATP_STAGES) == set(ATP_DEPENDENCY_MAP.keys())

    def test_dependency_map_references_are_valid(self):
        """All dependency references point to stages that exist."""
        for stage, deps in ATP_DEPENDENCY_MAP.items():
            for dep in deps:
                assert dep in ATP_STAGES, (
                    f"Stage '{stage}' depends on '{dep}' which is not in ATP_STAGES"
                )

    def test_no_circular_dependencies(self):
        """Dependency graph must be a DAG — no cycles."""
        visited: set[str] = set()
        path: set[str] = set()

        def _dfs(node: str) -> None:
            if node in path:
                raise AssertionError(f"Circular dependency detected at '{node}'")
            if node in visited:
                return
            path.add(node)
            for dep in ATP_DEPENDENCY_MAP.get(node, set()):
                _dfs(dep)
            path.discard(node)
            visited.add(node)

        for stage in ATP_STAGES:
            _dfs(stage)

    def test_dependencies_come_before_dependents(self):
        """In ATP_STAGES ordering, every dependency appears before its dependent."""
        idx = {stage: i for i, stage in enumerate(ATP_STAGES)}
        for stage, deps in ATP_DEPENDENCY_MAP.items():
            for dep in deps:
                assert idx[dep] < idx[stage], (
                    f"Stage '{dep}' (index {idx[dep]}) must appear before "
                    f"'{stage}' (index {idx[stage]}) in ATP_STAGES"
                )

    def test_check_dependencies_passes_when_satisfied(self):
        stages = {
            "load_positions": {"status": "completed"},
            "market_context": {"status": "completed"},
        }
        assert _check_dependencies("build_packets", stages) == []

    def test_check_dependencies_passes_with_skipped(self):
        stages = {
            "load_positions": {"status": "completed"},
            "market_context": {"status": "skipped"},
        }
        assert _check_dependencies("build_packets", stages) == []

    def test_check_dependencies_fails_when_missing(self):
        stages = {"load_positions": {"status": "completed"}}
        unsatisfied = _check_dependencies("build_packets", stages)
        assert "market_context" in unsatisfied

    def test_check_dependencies_fails_when_running(self):
        stages = {
            "load_positions": {"status": "completed"},
            "market_context": {"status": "running"},
        }
        unsatisfied = _check_dependencies("build_packets", stages)
        assert "market_context" in unsatisfied

    def test_root_stages_have_no_dependencies(self):
        assert _check_dependencies("load_positions", {}) == []
        assert _check_dependencies("market_context", {}) == []

    def test_normalize_requires_both_engine_and_model(self):
        deps = ATP_DEPENDENCY_MAP["normalize"]
        assert "engine_analysis" in deps
        assert "model_analysis" in deps


class TestDependencyEnforcement:
    """Verify the pipeline runner actually enforces dependencies —
    stage timing proves sequential execution and dependency satisfaction."""

    def test_stages_execute_in_dependency_order(self):
        """For a single-trade run, every stage's started_at must be
        >= the ended_at of each of its dependencies."""
        from app.services.active_trade_pipeline import run_active_trade_pipeline
        result = asyncio.run(
            run_active_trade_pipeline(
                [_base_trade()],
                _StubMonitorService(),
                _StubRegimeService(),
                _StubDataService(),
                skip_model=True,
            )
        )
        stages = result["stages"]
        for stage_key in ATP_STAGES:
            entry = stages[stage_key]
            if entry["status"] == "skipped":
                continue
            for dep in ATP_DEPENDENCY_MAP.get(stage_key, set()):
                dep_entry = stages[dep]
                dep_status = dep_entry["status"]
                assert dep_status in ("completed", "skipped"), (
                    f"Stage '{stage_key}' ran but dependency '{dep}' has "
                    f"status='{dep_status}'"
                )
                if dep_status == "completed" and "ended_at" in dep_entry:
                    assert entry["started_at"] >= dep_entry["ended_at"], (
                        f"Stage '{stage_key}' started at {entry['started_at']} "
                        f"before dependency '{dep}' ended at {dep_entry['ended_at']}"
                    )

    def test_dependency_metadata_present(self):
        """Each stage entry must include its dependency list."""
        from app.services.active_trade_pipeline import run_active_trade_pipeline
        result = asyncio.run(
            run_active_trade_pipeline(
                [_base_trade()],
                _StubMonitorService(),
                _StubRegimeService(),
                _StubDataService(),
                skip_model=True,
            )
        )
        stages = result["stages"]
        for stage_key in ATP_STAGES:
            entry = stages[stage_key]
            assert "dependencies" in entry, (
                f"Stage '{stage_key}' missing 'dependencies' metadata"
            )
            expected_deps = sorted(ATP_DEPENDENCY_MAP[stage_key])
            assert entry["dependencies"] == expected_deps

    def test_dependency_satisfied_at_present(self):
        """Non-skipped stages must record when dependencies were verified."""
        from app.services.active_trade_pipeline import run_active_trade_pipeline
        result = asyncio.run(
            run_active_trade_pipeline(
                [_base_trade()],
                _StubMonitorService(),
                _StubRegimeService(),
                _StubDataService(),
                skip_model=True,
            )
        )
        stages = result["stages"]
        for stage_key in ATP_STAGES:
            entry = stages[stage_key]
            if entry["status"] == "skipped":
                continue
            assert "dependency_satisfied_at" in entry, (
                f"Stage '{stage_key}' missing 'dependency_satisfied_at'"
            )

    def test_dependency_graph_in_result(self):
        """Pipeline result must include the dependency graph for auditability."""
        from app.services.active_trade_pipeline import run_active_trade_pipeline
        result = asyncio.run(
            run_active_trade_pipeline(
                [_base_trade()],
                _StubMonitorService(),
                _StubRegimeService(),
                _StubDataService(),
                skip_model=True,
            )
        )
        assert "dependency_graph" in result
        assert "stage_order" in result
        assert result["stage_order"] == list(ATP_STAGES)
        # Verify dependency_graph matches the canonical map
        for stage_key, deps in ATP_DEPENDENCY_MAP.items():
            assert result["dependency_graph"][stage_key] == sorted(deps)

    def test_zero_trades_skipped_stages_have_dependency_metadata(self):
        """Even skipped stages must record their dependencies."""
        from app.services.active_trade_pipeline import run_active_trade_pipeline
        result = asyncio.run(
            run_active_trade_pipeline(
                [],
                _StubMonitorService(),
                _StubRegimeService(),
                _StubDataService(),
            )
        )
        stages = result["stages"]
        for skip_key in ("build_packets", "engine_analysis",
                         "model_analysis", "normalize"):
            assert stages[skip_key]["status"] == "skipped"
            assert "dependencies" in stages[skip_key]

    def test_start_stage_rejects_unsatisfied_dependencies(self):
        """_start_stage must raise RuntimeError when dependencies are not met."""
        from app.services.active_trade_pipeline import _start_stage
        stages = {"load_positions": {"status": "completed"}}
        # build_packets needs both load_positions AND market_context
        with pytest.raises(RuntimeError, match="unsatisfied dependencies"):
            _start_stage(stages, "build_packets")

    def test_start_stage_accepts_satisfied_dependencies(self):
        """_start_stage must succeed when all dependencies are completed."""
        from app.services.active_trade_pipeline import _start_stage
        stages = {
            "load_positions": {"status": "completed"},
            "market_context": {"status": "completed"},
        }
        t = _start_stage(stages, "build_packets")
        assert isinstance(t, float)
        assert stages["build_packets"]["status"] == "running"

    def test_model_analysis_with_model(self):
        """With model enabled, model_analysis should complete (not skip)
        and still satisfy dependency ordering."""
        from app.services.active_trade_pipeline import run_active_trade_pipeline
        model_data = {
            "recommendation": "HOLD",
            "conviction": 0.8,
            "rationale_summary": "All good",
            "key_supporting_points": [],
            "key_risks": [],
            "market_alignment": "bullish",
            "portfolio_fit": "good",
            "event_sensitivity": "low",
            "suggested_next_move": "Monitor",
        }
        executor = _stub_model_executor(model_data)

        result = asyncio.run(
            run_active_trade_pipeline(
                [_base_trade()],
                _StubMonitorService(),
                _StubRegimeService(),
                _StubDataService(),
                model_executor=executor,
            )
        )
        stages = result["stages"]
        assert stages["model_analysis"]["status"] == "completed"
        assert stages["normalize"]["status"] == "completed"
        # normalize depends on both engine_analysis and model_analysis
        assert stages["normalize"]["started_at"] >= stages["engine_analysis"]["ended_at"]
        assert stages["normalize"]["started_at"] >= stages["model_analysis"]["ended_at"]
