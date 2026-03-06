"""
Tests for Active Trade Monitor — deterministic scoring engine, triggers, overrides.

Validates:
  1. Individual scoring components with known inputs
  2. Status mapping from composite scores
  3. Trigger evaluation (drawdown, SMA breaks, regime flip)
  4. Trigger override logic (CRITICAL caps / multiple CRITICALs → CLOSE)
  5. Full evaluate_position_monitor() with synthetic data
  6. MonitorResult.to_dict() serialisation
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# ── ensure importable ───────────────────────────────────────────────
_backend = Path(__file__).resolve().parent.parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from app.services.active_trade_monitor_service import (
    SCORE_WEIGHTS,
    STATUS_THRESHOLDS,
    TriggerResult,
    MonitorResult,
    _score_regime_alignment,
    _score_trend_strength,
    _score_drawdown_risk,
    _score_volatility_risk,
    _score_time_in_trade,
    _status_from_score,
    _apply_trigger_overrides,
    evaluate_triggers,
    evaluate_position_monitor,
)


# ═════════════════════════════════════════════════════════════════════
#  1. Scoring Components
# ═════════════════════════════════════════════════════════════════════

class TestScoreRegimeAlignment:
    """Regime alignment scoring: 0–25 points."""

    def test_long_risk_on_full(self):
        """Long + RISK_ON with score 100 → full 25 points."""
        score = _score_regime_alignment("long", "RISK_ON", 100)
        assert score == 25.0

    def test_long_risk_on_partial(self):
        """Long + RISK_ON with score 60 → 15 points."""
        score = _score_regime_alignment("long", "RISK_ON", 60)
        assert score == pytest.approx(15.0)

    def test_long_risk_off_low(self):
        """Long + RISK_OFF → max 5 (25 * 0.2)."""
        score = _score_regime_alignment("long", "RISK_OFF", 80)
        assert score == pytest.approx(5.0)

    def test_long_neutral(self):
        """Long + NEUTRAL with score 80 → 25 * 0.6 * 0.8 = 12.0."""
        score = _score_regime_alignment("long", "NEUTRAL", 80)
        assert score == pytest.approx(12.0)

    def test_short_risk_off_full(self):
        """Short + RISK_OFF with score 0 → 25 * (100-0)/100 = 25."""
        score = _score_regime_alignment("short", "RISK_OFF", 0)
        assert score == pytest.approx(25.0)

    def test_short_risk_on(self):
        """Short + RISK_ON → 25 * 0.2 = 5."""
        score = _score_regime_alignment("short", "RISK_ON", 80)
        assert score == pytest.approx(5.0)

    def test_missing_regime(self):
        """No regime data → half credit (12.5)."""
        score = _score_regime_alignment("long", None, None)
        assert score == pytest.approx(12.5)


class TestScoreTrendStrength:
    """Trend strength scoring: 0–25 points."""

    def test_long_all_bullish(self):
        """Long: price > SMA20 > SMA50, RSI 60 → 8+7+5+RSI bonus."""
        rsi_bonus = 5 * max(0, min(1, (60 - 30) / 40))  # = 5 * 0.75 = 3.75
        expected = 8 + 7 + 5 + rsi_bonus  # = 23.75
        score = _score_trend_strength("long", 150.0, 145.0, 140.0, 60.0)
        assert score == pytest.approx(expected)

    def test_long_price_below_both_sma(self):
        """Long: price below both SMAs → only RSI bonus from sub-components."""
        rsi_bonus = 5 * max(0, min(1, (55 - 30) / 40))  # = 5 * 0.625 = 3.125
        score = _score_trend_strength("long", 130.0, 140.0, 145.0, 55.0)
        assert score == pytest.approx(rsi_bonus)

    def test_short_bearish_trend(self):
        """Short: price < SMA20 < SMA50, RSI 35 → full trend alignment."""
        rsi_bonus = 5 * max(0, min(1, (70 - 35) / 40))  # = 5 * 0.875 = 4.375
        expected = 8 + 7 + 5 + rsi_bonus  # = 24.375
        score = _score_trend_strength("short", 130.0, 140.0, 145.0, 35.0)
        assert score == pytest.approx(expected)

    def test_missing_all_indicators(self):
        """No indicators → half credit (12.5)."""
        score = _score_trend_strength("long", None, None, None, None)
        assert score == pytest.approx(12.5)

    def test_capped_at_max(self):
        """Score cannot exceed 25 even with extreme values."""
        score = _score_trend_strength("long", 200.0, 100.0, 50.0, 70.0)
        assert score <= 25.0


class TestScoreDrawdownRisk:
    """Drawdown risk scoring: 0–25 points (inverse)."""

    def test_big_profit(self):
        """P&L +15% → max 25 points."""
        assert _score_drawdown_risk(0.15) == 25.0

    def test_breakeven(self):
        """P&L 0% → 18 points."""
        assert _score_drawdown_risk(0.0) == pytest.approx(18.0)

    def test_small_loss(self):
        """P&L -5% → 10 points."""
        assert _score_drawdown_risk(-0.05) == pytest.approx(10.0)

    def test_moderate_loss(self):
        """P&L -10% → 5 points."""
        assert _score_drawdown_risk(-0.10) == pytest.approx(5.0)

    def test_severe_loss(self):
        """P&L -20% → 0 points."""
        assert _score_drawdown_risk(-0.20) == pytest.approx(0.0)

    def test_deep_loss(self):
        """P&L -30% → clamped to 0."""
        assert _score_drawdown_risk(-0.30) == 0.0

    def test_interpolation_midpoints(self):
        """P&L -2.5% should interpolate between -5%→10 and 0%→18."""
        expected = 10 + ((-0.025 + 0.05) / 0.05) * (18 - 10)  # 10 + 0.5*8 = 14
        assert _score_drawdown_risk(-0.025) == pytest.approx(expected)

    def test_missing_pl(self):
        """Missing P&L → half credit (12.5)."""
        assert _score_drawdown_risk(None) == pytest.approx(12.5)


class TestScoreVolatilityRisk:
    """Volatility risk scoring: 0–15 points."""

    def test_stable_rsi(self):
        """RSI 50 (stable) → full 15."""
        assert _score_volatility_risk(50.0) == 15.0

    def test_moderate_rsi(self):
        """RSI 65 (in 30-70 but outside 40-60) → 15 * 0.67."""
        assert _score_volatility_risk(65.0) == pytest.approx(15 * 0.67)

    def test_extreme_rsi(self):
        """RSI 80 (extreme) → 15 * 0.33."""
        assert _score_volatility_risk(80.0) == pytest.approx(15 * 0.33)

    def test_missing_rsi(self):
        """No RSI → half credit (7.5)."""
        assert _score_volatility_risk(None) == pytest.approx(7.5)


class TestScoreTimeInTrade:
    """Time in trade placeholder: always 5 (half of max 10)."""

    def test_placeholder(self):
        assert _score_time_in_trade() == pytest.approx(5.0)


# ═════════════════════════════════════════════════════════════════════
#  2. Status Mapping
# ═════════════════════════════════════════════════════════════════════

class TestStatusMapping:

    def test_hold(self):
        assert _status_from_score(80) == "HOLD"
        assert _status_from_score(65) == "HOLD"

    def test_watch(self):
        assert _status_from_score(60) == "WATCH"
        assert _status_from_score(45) == "WATCH"

    def test_reduce(self):
        assert _status_from_score(40) == "REDUCE"
        assert _status_from_score(25) == "REDUCE"

    def test_close(self):
        assert _status_from_score(24) == "CLOSE"
        assert _status_from_score(0) == "CLOSE"


# ═════════════════════════════════════════════════════════════════════
#  3. Trigger Evaluation
# ═════════════════════════════════════════════════════════════════════

class TestTriggerEvaluation:

    def test_drawdown_critical(self):
        """P&L -12% → CRITICAL drawdown trigger."""
        triggers = evaluate_triggers("long", -0.12, 100.0, 110.0, 120.0, "RISK_ON")
        dd = next(t for t in triggers if t.id == "max_drawdown")
        assert dd.hit is True
        assert dd.level == "CRITICAL"

    def test_drawdown_warn(self):
        """P&L -6% → WARN drawdown trigger."""
        triggers = evaluate_triggers("long", -0.06, 100.0, 90.0, 80.0, "RISK_ON")
        dd = next(t for t in triggers if t.id == "max_drawdown")
        assert dd.hit is True
        assert dd.level == "WARN"

    def test_drawdown_ok(self):
        """P&L -2% → INFO (within tolerance)."""
        triggers = evaluate_triggers("long", -0.02, 100.0, 90.0, 80.0, "RISK_ON")
        dd = next(t for t in triggers if t.id == "max_drawdown")
        assert dd.hit is False
        assert dd.level == "INFO"

    def test_trend_break_sma20_long(self):
        """Long price below SMA20 → WARN trend break."""
        triggers = evaluate_triggers("long", 0.0, 95.0, 100.0, 90.0, "RISK_ON")
        sma20 = next(t for t in triggers if t.id == "trend_break_sma20")
        assert sma20.hit is True
        assert sma20.level == "WARN"

    def test_trend_break_sma50_long(self):
        """Long price below SMA50 → CRITICAL trend break."""
        triggers = evaluate_triggers("long", 0.0, 85.0, 100.0, 90.0, "RISK_ON")
        sma50 = next(t for t in triggers if t.id == "trend_break_sma50")
        assert sma50.hit is True
        assert sma50.level == "CRITICAL"

    def test_regime_flip_long_risk_off(self):
        """Long + RISK_OFF → CRITICAL regime flip."""
        triggers = evaluate_triggers("long", 0.0, 100.0, 100.0, 100.0, "RISK_OFF")
        rf = next(t for t in triggers if t.id == "regime_flip")
        assert rf.hit is True
        assert rf.level == "CRITICAL"

    def test_regime_flip_short_risk_on(self):
        """Short + RISK_ON → CRITICAL regime flip."""
        triggers = evaluate_triggers("short", 0.0, 100.0, 100.0, 100.0, "RISK_ON")
        rf = next(t for t in triggers if t.id == "regime_flip")
        assert rf.hit is True
        assert rf.level == "CRITICAL"

    def test_no_triggers_hit(self):
        """Healthy long: no triggers should fire."""
        triggers = evaluate_triggers("long", 0.02, 110.0, 105.0, 100.0, "RISK_ON")
        hit_triggers = [t for t in triggers if t.hit]
        assert len(hit_triggers) == 0

    def test_always_produces_four_triggers(self):
        """Every call should produce exactly 4 trigger results."""
        triggers = evaluate_triggers("long", 0.0, 100.0, 100.0, 100.0, "NEUTRAL")
        assert len(triggers) == 4
        ids = {t.id for t in triggers}
        assert ids == {"max_drawdown", "trend_break_sma20", "trend_break_sma50", "regime_flip"}


# ═════════════════════════════════════════════════════════════════════
#  4. Trigger Override Logic
# ═════════════════════════════════════════════════════════════════════

class TestTriggerOverrides:

    def _trigger(self, level: str, hit: bool) -> TriggerResult:
        return TriggerResult(id="test", level=level, message="test", hit=hit)

    def test_one_critical_caps_at_reduce(self):
        """Single CRITICAL hit → HOLD becomes REDUCE."""
        triggers = [self._trigger("CRITICAL", True)]
        assert _apply_trigger_overrides("HOLD", triggers) == "REDUCE"
        assert _apply_trigger_overrides("WATCH", triggers) == "REDUCE"

    def test_one_critical_does_not_upgrade(self):
        """Single CRITICAL doesn't upgrade REDUCE or CLOSE."""
        triggers = [self._trigger("CRITICAL", True)]
        assert _apply_trigger_overrides("REDUCE", triggers) == "REDUCE"
        assert _apply_trigger_overrides("CLOSE", triggers) == "CLOSE"

    def test_two_criticals_force_close(self):
        """Two+ CRITICALs → always CLOSE."""
        triggers = [
            self._trigger("CRITICAL", True),
            self._trigger("CRITICAL", True),
        ]
        assert _apply_trigger_overrides("HOLD", triggers) == "CLOSE"
        assert _apply_trigger_overrides("WATCH", triggers) == "CLOSE"
        assert _apply_trigger_overrides("REDUCE", triggers) == "CLOSE"

    def test_unhit_criticals_ignored(self):
        """CRITICAL triggers that didn't fire (hit=False) are not counted."""
        triggers = [self._trigger("CRITICAL", False)]
        assert _apply_trigger_overrides("HOLD", triggers) == "HOLD"

    def test_warn_only_no_override(self):
        """WARN triggers don't override status."""
        triggers = [self._trigger("WARN", True), self._trigger("WARN", True)]
        assert _apply_trigger_overrides("HOLD", triggers) == "HOLD"


# ═════════════════════════════════════════════════════════════════════
#  5. Full evaluate_position_monitor()
# ═════════════════════════════════════════════════════════════════════

class TestEvaluatePositionMonitor:

    def _make_position(self, **overrides):
        pos = {
            "symbol": "SPY",
            "quantity": 10,
            "avg_open_price": 450.0,
            "mark_price": 460.0,
            "cost_basis_total": 4500.0,
            "market_value": 4600.0,
            "unrealized_pnl": 100.0,
            "unrealized_pnl_pct": 2.22,  # +2.22%
        }
        pos.update(overrides)
        return pos

    def test_healthy_long(self):
        """Healthy long position → HOLD status, score ≥ 60."""
        result = evaluate_position_monitor(
            position=self._make_position(),
            market_context={"regime_label": "RISK_ON", "regime_score": 75},
            indicators={"sma20": 455.0, "sma50": 445.0, "rsi14": 55},
        )
        assert result.status == "HOLD"
        assert result.score_0_100 >= 60
        assert result.symbol == "SPY"
        assert result.breakdown["regime_alignment"] > 0
        assert result.breakdown["trend_strength"] > 0
        assert result.recommended_action["action"] == "HOLD"

    def test_distressed_long(self):
        """Distressed long: big loss + RISK_OFF → CLOSE or REDUCE."""
        result = evaluate_position_monitor(
            position=self._make_position(
                mark_price=400.0,
                unrealized_pnl=-500.0,
                unrealized_pnl_pct=-11.1,
            ),
            market_context={"regime_label": "RISK_OFF", "regime_score": 20},
            indicators={"sma20": 440.0, "sma50": 450.0, "rsi14": 28},
        )
        assert result.status in ("CLOSE", "REDUCE")
        assert result.score_0_100 < 45

    def test_short_position_in_risk_off(self):
        """Short position in RISK_OFF → should score well."""
        result = evaluate_position_monitor(
            position=self._make_position(quantity=-10, unrealized_pnl_pct=5.0),
            market_context={"regime_label": "RISK_OFF", "regime_score": 20},
            indicators={"sma20": 465.0, "sma50": 470.0, "rsi14": 35},
        )
        # Short in RISK_OFF should score higher on regime alignment
        assert result.breakdown["regime_alignment"] > 12

    def test_missing_indicators(self):
        """Missing all indicators → still produces a valid result with half-credits."""
        result = evaluate_position_monitor(
            position=self._make_position(),
            market_context=None,
            indicators=None,
        )
        assert result.symbol == "SPY"
        assert result.status in ("HOLD", "WATCH", "REDUCE", "CLOSE")
        assert 0 <= result.score_0_100 <= 100
        # With all half-credits: 12.5 + 12.5 + drawdown_score + 7.5 + 5
        assert result.breakdown["regime_alignment"] == pytest.approx(12.5)
        assert result.breakdown["trend_strength"] == pytest.approx(12.5)
        assert result.breakdown["volatility_risk"] == pytest.approx(7.5)
        assert result.breakdown["time_in_trade"] == pytest.approx(5.0)

    def test_trigger_override_forces_close(self):
        """Long + RISK_OFF + below SMA50 → 2 CRITICALs → CLOSE override."""
        result = evaluate_position_monitor(
            position=self._make_position(
                mark_price=420.0,
                unrealized_pnl_pct=-6.67,
            ),
            market_context={"regime_label": "RISK_OFF", "regime_score": 20},
            indicators={"sma20": 450.0, "sma50": 455.0, "rsi14": 32},
        )
        # Two CRITICALs: regime_flip + trend_break_sma50 → CLOSE
        critical_hits = [t for t in result.triggers if t.get("hit") and t.get("level") == "CRITICAL"]
        assert len(critical_hits) >= 2
        assert result.status == "CLOSE"


# ═════════════════════════════════════════════════════════════════════
#  6. MonitorResult serialisation
# ═════════════════════════════════════════════════════════════════════

class TestMonitorResultSerialisation:

    def test_to_dict_keys(self):
        """to_dict() produces all expected keys."""
        result = evaluate_position_monitor(
            position={"symbol": "QQQ", "quantity": 5, "mark_price": 380.0, "unrealized_pnl_pct": 1.5},
            market_context={"regime_label": "RISK_ON", "regime_score": 70},
            indicators={"sma20": 375.0, "sma50": 370.0, "rsi14": 55},
        )
        d = result.to_dict()

        required_keys = {"symbol", "status", "score_0_100", "breakdown", "triggers",
                         "recommended_action", "last_evaluated_ts"}
        assert required_keys.issubset(d.keys())

    def test_to_dict_types(self):
        """to_dict() values have correct types."""
        result = evaluate_position_monitor(
            position={"symbol": "IWM", "quantity": 3, "mark_price": 200.0, "unrealized_pnl_pct": -2.0},
        )
        d = result.to_dict()

        assert isinstance(d["symbol"], str)
        assert isinstance(d["status"], str)
        assert isinstance(d["score_0_100"], int)
        assert isinstance(d["breakdown"], dict)
        assert isinstance(d["triggers"], list)
        assert isinstance(d["recommended_action"], dict)
        assert isinstance(d["last_evaluated_ts"], float)

    def test_breakdown_has_all_factors(self):
        """Breakdown dict contains all 5 scoring factors."""
        result = evaluate_position_monitor(
            position={"symbol": "DIA", "quantity": 1, "mark_price": 350.0},
        )
        expected_factors = {"regime_alignment", "trend_strength", "drawdown_risk",
                           "volatility_risk", "time_in_trade"}
        assert expected_factors == set(result.breakdown.keys())
