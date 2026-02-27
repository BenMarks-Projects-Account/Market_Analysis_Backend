"""Tests for preset resolution across all four filter levels (strict / conservative / balanced / wide).

Covers:
- Preset ordering: strict is tighter than balanced in ≥3 evaluate dimensions
- Wide is looser than balanced in ≥3 evaluate dimensions
- min_ror varies by preset (was hardcoded at 0.01)
- evaluate() uses payload thresholds, not hardcoded fallbacks
- resolve_thresholds() classmethod is the single source of truth
- Unknown preset falls back to balanced with a warning
- _preset_name is correctly stamped for all entry points
- Filter trace resolved_thresholds differs per preset
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.strategies.credit_spread import CreditSpreadStrategyPlugin
from app.services.strategy_service import StrategyService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeContract:
    strike: float
    bid: float | None
    ask: float | None
    option_type: str = "put"
    delta: float | None = None
    iv: float | None = None
    open_interest: int = 1000
    volume: int = 100


def _make_svc(tmp_path) -> StrategyService:
    mock_bds = MagicMock()
    mock_bds.get_source_health_snapshot.return_value = {"sources": []}
    return StrategyService(
        base_data_service=mock_bds,
        results_dir=Path(tmp_path),
    )


def _make_trade(**overrides) -> dict[str, Any]:
    """Synthetic enriched trade row that passes balanced filters by default."""
    base = {
        "p_win_used": 0.65,
        "ev_per_share": 0.50,
        "ev_to_risk": 0.025,
        "return_on_risk": 0.015,
        "width": 3.0,
        "net_credit": 0.80,
        "bid_ask_spread_pct": 0.008,   # 0.8% → passes max_bid_ask_spread_pct ≤ 1.5
        "open_interest": 400,
        "volume": 30,
        "_request": {},
        "_policy": {},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# ① Preset ordering: strict > balanced > wide in ≥3 evaluate dimensions
# ---------------------------------------------------------------------------

class TestPresetOrdering:
    """Verify that strict thresholds are materially tighter than balanced,
    and wide is materially looser."""

    TIGHTNESS_KEYS = {
        # key: direction — higher means tighter for 'higher_is_tighter',
        #                    lower means tighter for 'lower_is_tighter'
        "min_pop":               "higher_is_tighter",
        "min_ev_to_risk":        "higher_is_tighter",
        "min_ror":               "higher_is_tighter",
        "min_open_interest":     "higher_is_tighter",
        "min_volume":            "higher_is_tighter",
        "max_bid_ask_spread_pct": "lower_is_tighter",
    }

    def _check_tighter(self, tighter: dict, looser: dict, min_dims: int):
        """Assert tighter preset is strictly tighter on ≥ min_dims evaluate keys."""
        tighter_count = 0
        for key, direction in self.TIGHTNESS_KEYS.items():
            tv = tighter.get(key)
            lv = looser.get(key)
            if tv is None or lv is None:
                continue
            if direction == "higher_is_tighter":
                if tv > lv:
                    tighter_count += 1
            else:
                if tv < lv:
                    tighter_count += 1
        assert tighter_count >= min_dims, (
            f"Expected ≥{min_dims} tighter dimensions, got {tighter_count}.  "
            f"tighter={tighter}, looser={looser}"
        )

    def test_strict_tighter_than_balanced_in_at_least_3_dims(self):
        strict = StrategyService.resolve_thresholds("credit_spread", "strict")
        balanced = StrategyService.resolve_thresholds("credit_spread", "balanced")
        self._check_tighter(strict, balanced, 3)

    def test_strict_tighter_than_conservative(self):
        strict = StrategyService.resolve_thresholds("credit_spread", "strict")
        conservative = StrategyService.resolve_thresholds("credit_spread", "conservative")
        self._check_tighter(strict, conservative, 3)

    def test_conservative_tighter_than_balanced(self):
        conservative = StrategyService.resolve_thresholds("credit_spread", "conservative")
        balanced = StrategyService.resolve_thresholds("credit_spread", "balanced")
        self._check_tighter(conservative, balanced, 2)

    def test_balanced_tighter_than_wide_in_at_least_3_dims(self):
        balanced = StrategyService.resolve_thresholds("credit_spread", "balanced")
        wide = StrategyService.resolve_thresholds("credit_spread", "wide")
        self._check_tighter(balanced, wide, 3)

    def test_full_ordering_strict_gt_conservative_gt_balanced_gt_wide(self):
        """Full chain: strict ≥ conservative ≥ balanced ≥ wide on min_pop."""
        s = StrategyService.resolve_thresholds("credit_spread", "strict")
        c = StrategyService.resolve_thresholds("credit_spread", "conservative")
        b = StrategyService.resolve_thresholds("credit_spread", "balanced")
        w = StrategyService.resolve_thresholds("credit_spread", "wide")
        assert s["min_pop"] >= c["min_pop"] >= b["min_pop"] >= w["min_pop"]
        assert s["min_ror"] >= c["min_ror"] >= b["min_ror"] >= w["min_ror"]
        assert s["min_open_interest"] >= c["min_open_interest"] >= b["min_open_interest"] >= w["min_open_interest"]


# ---------------------------------------------------------------------------
# ② min_ror varies by preset (no longer hardcoded)
# ---------------------------------------------------------------------------

class TestMinRorPreset:
    def test_strict_min_ror_is_higher_than_balanced(self):
        strict = StrategyService.resolve_thresholds("credit_spread", "strict")
        balanced = StrategyService.resolve_thresholds("credit_spread", "balanced")
        assert strict["min_ror"] > balanced["min_ror"]

    def test_wide_min_ror_is_lower_than_balanced(self):
        balanced = StrategyService.resolve_thresholds("credit_spread", "balanced")
        wide = StrategyService.resolve_thresholds("credit_spread", "wide")
        assert wide["min_ror"] < balanced["min_ror"]

    def test_all_presets_have_min_ror(self):
        for level in ("strict", "conservative", "balanced", "wide"):
            vals = StrategyService.resolve_thresholds("credit_spread", level)
            assert "min_ror" in vals, f"{level} preset missing min_ror"
            assert isinstance(vals["min_ror"], (int, float))
            assert vals["min_ror"] > 0


# ---------------------------------------------------------------------------
# ③ evaluate() reads thresholds from payload (not hardcoded fallbacks)
# ---------------------------------------------------------------------------

class TestEvaluateUsesPayloadThresholds:
    """Confirm evaluate() honours the thresholds set by preset resolution."""

    plugin = CreditSpreadStrategyPlugin()

    def test_strict_pop_rejects_balanced_trade(self):
        """A trade with POP 0.62 passes balanced (0.60) but fails strict (0.70)."""
        strict = StrategyService.resolve_thresholds("credit_spread", "strict")
        balanced = StrategyService.resolve_thresholds("credit_spread", "balanced")

        trade = _make_trade(
            p_win_used=0.62,
            return_on_risk=0.05,
            open_interest=2000,
            volume=200,
        )

        trade["_request"] = balanced
        ok_bal, reasons_bal = self.plugin.evaluate(trade)

        trade["_request"] = strict
        ok_str, reasons_str = self.plugin.evaluate(trade)

        assert ok_bal, f"Trade should pass balanced; reasons={reasons_bal}"
        assert not ok_str, "Trade should fail strict POP gate"
        assert "pop_below_floor" in reasons_str

    def test_strict_ror_rejects_low_ror_trade(self):
        """A trade with ROR 0.015 passes balanced (min_ror=0.01) but fails strict (min_ror=0.03)."""
        strict = StrategyService.resolve_thresholds("credit_spread", "strict")
        balanced = StrategyService.resolve_thresholds("credit_spread", "balanced")

        trade = _make_trade(
            p_win_used=0.80,
            return_on_risk=0.015,
            open_interest=2000,
            volume=200,
        )

        trade["_request"] = balanced
        ok_bal, reasons_bal = self.plugin.evaluate(trade)

        trade["_request"] = strict
        ok_str, reasons_str = self.plugin.evaluate(trade)

        assert ok_bal, f"Trade should pass balanced; reasons={reasons_bal}"
        assert not ok_str, "Trade should fail strict ROR gate"
        assert "ror_below_floor" in reasons_str

    def test_wide_oi_accepts_low_oi_trade(self):
        """A trade with OI=50 fails balanced (100) but passes wide (25)."""
        wide = StrategyService.resolve_thresholds("credit_spread", "wide")
        balanced = StrategyService.resolve_thresholds("credit_spread", "balanced")

        trade = _make_trade(
            p_win_used=0.70,
            return_on_risk=0.05,
            open_interest=50,
            volume=30,
        )

        trade["_request"] = balanced
        ok_bal, reasons_bal = self.plugin.evaluate(trade)
        assert not ok_bal, "Trade should fail balanced OI gate"
        assert "open_interest_below_min" in reasons_bal

        trade["_request"] = wide
        ok_wide, reasons_wide = self.plugin.evaluate(trade)
        assert ok_wide, f"Trade should pass wide; reasons={reasons_wide}"

    def test_evaluate_fallback_without_payload(self):
        """If payload is empty, evaluate() uses balanced-level safety fallbacks."""
        trade = _make_trade(
            p_win_used=0.62,    # > fallback 0.60
            return_on_risk=0.02,  # > fallback 0.01
            open_interest=350,    # > fallback 300
            volume=25,            # > fallback 20
            _request={},
        )
        ok, reasons = self.plugin.evaluate(trade)
        assert ok, f"Should pass with balanced-level fallbacks; reasons={reasons}"


# ---------------------------------------------------------------------------
# ④ resolve_thresholds() classmethod
# ---------------------------------------------------------------------------

class TestResolveThresholds:
    def test_returns_dict_for_known_preset(self):
        result = StrategyService.resolve_thresholds("credit_spread", "strict")
        assert isinstance(result, dict)
        assert "min_pop" in result
        assert "min_ror" in result

    def test_returns_balanced_for_none_preset(self):
        result = StrategyService.resolve_thresholds("credit_spread", None)
        balanced = StrategyService.resolve_thresholds("credit_spread", "balanced")
        assert result["min_pop"] == balanced["min_pop"]
        assert result["min_ror"] == balanced["min_ror"]

    def test_unknown_preset_falls_back_to_balanced(self):
        result = StrategyService.resolve_thresholds("credit_spread", "turbo_yolo")
        balanced = StrategyService.resolve_thresholds("credit_spread", "balanced")
        assert result["min_pop"] == balanced["min_pop"]

    def test_overrides_win(self):
        result = StrategyService.resolve_thresholds(
            "credit_spread", "strict", overrides={"min_pop": 0.99}
        )
        assert result["min_pop"] == 0.99

    def test_none_overrides_are_ignored(self):
        result = StrategyService.resolve_thresholds(
            "credit_spread", "strict", overrides={"min_pop": None}
        )
        # Should keep strict's min_pop, not set to None
        assert result["min_pop"] == 0.70

    def test_unknown_strategy_returns_empty(self):
        result = StrategyService.resolve_thresholds("magic_beans", "strict")
        assert result == {}

    def test_all_four_levels_are_distinct(self):
        """Each level should produce a unique set of threshold values."""
        results = {}
        for level in ("strict", "conservative", "balanced", "wide"):
            vals = StrategyService.resolve_thresholds("credit_spread", level)
            key = tuple(sorted(vals.items()))
            assert key not in results.values(), f"{level} produces duplicate of another level"
            results[level] = key


# ---------------------------------------------------------------------------
# ⑤ _apply_request_defaults integration
# ---------------------------------------------------------------------------

class TestApplyRequestDefaults:
    def test_preset_strict_stamps_name(self, tmp_path):
        svc = _make_svc(tmp_path)
        result = svc._apply_request_defaults("credit_spread", {"preset": "strict"})
        assert result["_preset_name"] == "strict"
        assert result["min_pop"] == 0.70
        assert result["min_ror"] == 0.03

    def test_preset_wide_stamps_name(self, tmp_path):
        svc = _make_svc(tmp_path)
        result = svc._apply_request_defaults("credit_spread", {"preset": "wide"})
        assert result["_preset_name"] == "wide"
        assert result["min_pop"] == 0.45
        assert result["min_ror"] == 0.002

    def test_no_preset_defaults_to_balanced(self, tmp_path):
        svc = _make_svc(tmp_path)
        result = svc._apply_request_defaults("credit_spread", {})
        assert result["_preset_name"] == "balanced"
        assert result["min_pop"] == 0.55
        assert result["min_ror"] == 0.005

    def test_unknown_preset_falls_back_to_balanced(self, tmp_path):
        svc = _make_svc(tmp_path)
        result = svc._apply_request_defaults("credit_spread", {"preset": "turbo"})
        assert result["_preset_name"] == "balanced"
        assert result["min_pop"] == 0.55

    def test_user_override_preserved(self, tmp_path):
        svc = _make_svc(tmp_path)
        result = svc._apply_request_defaults("credit_spread", {
            "preset": "strict",
            "min_pop": 0.55,
        })
        assert result["min_pop"] == 0.55  # user override wins
        assert result["min_ror"] == 0.03  # preset value fills in

    def test_orchestrator_payload_keeps_profile_values(self, tmp_path):
        """Simulate what the orchestrator sends: profile values + spread_type, no preset key.
        After fix, orchestrator now also sends preset=level."""
        svc = _make_svc(tmp_path)
        # Simulating strict profile params + preset key (post-fix)
        payload = {
            "preset": "strict",
            "spread_type": "put_credit_spread",
            "dte_min": 14,
            "dte_max": 30,
            "min_pop": 0.70,
            "min_ev_to_risk": 0.03,
            "min_ror": 0.03,
            "min_open_interest": 1000,
            "min_volume": 100,
        }
        result = svc._apply_request_defaults("credit_spread", payload)
        assert result["_preset_name"] == "strict"
        assert result["min_pop"] == 0.70
        assert result["min_ror"] == 0.03


# ---------------------------------------------------------------------------
# ⑥ Different presets produce different resolved_thresholds in filter trace
# ---------------------------------------------------------------------------

class TestFilterTraceThresholdsDiffer:
    """The resolved_thresholds dict in the filter trace should differ
    when different presets are used."""

    def _extract_numeric_thresholds(self, payload: dict) -> dict:
        skip = StrategyService._FILTER_TRACE_SKIP_KEYS
        return {
            k: v for k, v in payload.items()
            if not k.startswith("_") and k not in skip and isinstance(v, (int, float))
        }

    def test_strict_vs_balanced_thresholds_differ(self, tmp_path):
        svc = _make_svc(tmp_path)
        strict_payload = svc._apply_request_defaults("credit_spread", {"preset": "strict"})
        balanced_payload = svc._apply_request_defaults("credit_spread", {"preset": "balanced"})

        strict_t = self._extract_numeric_thresholds(strict_payload)
        balanced_t = self._extract_numeric_thresholds(balanced_payload)

        diff_keys = {k for k in strict_t if strict_t.get(k) != balanced_t.get(k)}
        assert len(diff_keys) >= 3, (
            f"Expected ≥3 differing threshold keys between strict and balanced, "
            f"got {len(diff_keys)}: {diff_keys}"
        )

    def test_balanced_vs_wide_thresholds_differ(self, tmp_path):
        svc = _make_svc(tmp_path)
        balanced_payload = svc._apply_request_defaults("credit_spread", {"preset": "balanced"})
        wide_payload = svc._apply_request_defaults("credit_spread", {"preset": "wide"})

        balanced_t = self._extract_numeric_thresholds(balanced_payload)
        wide_t = self._extract_numeric_thresholds(wide_payload)

        diff_keys = {k for k in balanced_t if balanced_t.get(k) != wide_t.get(k)}
        assert len(diff_keys) >= 3, (
            f"Expected ≥3 differing threshold keys between balanced and wide, "
            f"got {len(diff_keys)}: {diff_keys}"
        )

    def test_all_four_presets_produce_unique_thresholds(self, tmp_path):
        svc = _make_svc(tmp_path)
        seen: dict[str, tuple] = {}
        for level in ("strict", "conservative", "balanced", "wide"):
            payload = svc._apply_request_defaults("credit_spread", {"preset": level})
            t = self._extract_numeric_thresholds(payload)
            sig = tuple(sorted(t.items()))
            assert sig not in seen.values(), f"{level} has same thresholds as another level"
            seen[level] = sig


# ---------------------------------------------------------------------------
# ⑦ R8 Calibration: exact threshold values after recalibration
# ---------------------------------------------------------------------------

class TestCalibrationValues:
    """Verify exact recalibrated threshold values for each preset.

    Strict is unchanged.  Conservative / Balanced / Wide were adjusted
    in R8 to achieve non-zero pass rates on SPY/QQQ while preserving
    strict > conservative > balanced > wide ordering.

    Values derived from filter-trace bottleneck analysis:
      ev_to_risk_below_floor 98%, volume_below_min 98%, spread_too_wide 60%
    """

    def test_strict_unchanged(self):
        s = StrategyService.resolve_thresholds("credit_spread", "strict")
        assert s["min_pop"] == 0.70
        assert s["min_ev_to_risk"] == 0.03
        assert s["min_ror"] == 0.03
        assert s["max_bid_ask_spread_pct"] == 1.0
        assert s["min_open_interest"] == 1000
        assert s["min_volume"] == 100
        assert s["data_quality_mode"] == "strict"

    def test_conservative_calibrated(self):
        c = StrategyService.resolve_thresholds("credit_spread", "conservative")
        assert c["min_pop"] == 0.60
        assert c["min_ev_to_risk"] == 0.012
        assert c["min_ror"] == 0.01
        assert c["max_bid_ask_spread_pct"] == 1.5
        assert c["min_open_interest"] == 200
        assert c["min_volume"] == 10
        assert c["data_quality_mode"] == "balanced"

    def test_balanced_calibrated(self):
        b = StrategyService.resolve_thresholds("credit_spread", "balanced")
        assert b["min_pop"] == 0.55
        assert b["min_ev_to_risk"] == 0.008
        assert b["min_ror"] == 0.005
        assert b["max_bid_ask_spread_pct"] == 2.0
        assert b["min_open_interest"] == 100
        assert b["min_volume"] == 5
        assert b["data_quality_mode"] == "balanced"

    def test_wide_calibrated(self):
        w = StrategyService.resolve_thresholds("credit_spread", "wide")
        assert w["min_pop"] == 0.45
        assert w["min_ev_to_risk"] == 0.005
        assert w["min_ror"] == 0.002
        assert w["max_bid_ask_spread_pct"] == 3.0
        assert w["min_open_interest"] == 25
        assert w["min_volume"] == 1
        assert w["data_quality_mode"] == "lenient"

    def test_full_ordering_all_quality_gate_params(self):
        """Every quality-gate threshold is monotonically ordered across all 4 levels."""
        s = StrategyService.resolve_thresholds("credit_spread", "strict")
        c = StrategyService.resolve_thresholds("credit_spread", "conservative")
        b = StrategyService.resolve_thresholds("credit_spread", "balanced")
        w = StrategyService.resolve_thresholds("credit_spread", "wide")

        # Higher-is-tighter: strict ≥ conservative ≥ balanced ≥ wide
        for key in ("min_pop", "min_ev_to_risk", "min_ror", "min_open_interest", "min_volume"):
            assert s[key] >= c[key] >= b[key] >= w[key], (
                f"Ordering violated for {key}: {s[key]} >= {c[key]} >= {b[key]} >= {w[key]}"
            )

        # Lower-is-tighter for max_bid_ask_spread_pct: strict ≤ conservative ≤ balanced ≤ wide
        assert s["max_bid_ask_spread_pct"] <= c["max_bid_ask_spread_pct"] <= b["max_bid_ask_spread_pct"] <= w["max_bid_ask_spread_pct"]


class TestBalancedAcceptance:
    """Verify that a trade with typical SPY metrics passes the new Balanced thresholds.

    Derived from trace data: median SPY credit spread has ~0.010 EV/risk,
    ~8 volume, ~1.8% spread, ~120 OI, ~0.8% ROR, ~58% POP.
    """

    plugin = CreditSpreadStrategyPlugin()

    def test_typical_spy_trade_passes_balanced(self):
        """A trade resembling typical SPY credit spread should pass Balanced."""
        balanced = StrategyService.resolve_thresholds("credit_spread", "balanced")
        trade = _make_trade(
            p_win_used=0.58,          # > 0.55 balanced min_pop
            ev_to_risk=0.010,         # > 0.008 balanced min_ev_to_risk
            return_on_risk=0.008,     # > 0.005 balanced min_ror
            bid_ask_spread_pct=0.018, # 1.8% < 2.0% balanced max
            open_interest=120,        # > 100 balanced min_oi
            volume=8,                 # > 5 balanced min_volume
        )
        trade["_request"] = balanced
        ok, reasons = self.plugin.evaluate(trade)
        assert ok, f"Typical SPY trade should pass Balanced; reasons={reasons}"

    def test_typical_spy_trade_fails_strict(self):
        """Same SPY trade should fail Strict on multiple gates."""
        strict = StrategyService.resolve_thresholds("credit_spread", "strict")
        trade = _make_trade(
            p_win_used=0.58,
            ev_to_risk=0.010,
            return_on_risk=0.008,
            bid_ask_spread_pct=0.018,
            open_interest=120,
            volume=8,
        )
        trade["_request"] = strict
        ok, reasons = self.plugin.evaluate(trade)
        assert not ok, "Typical SPY trade should fail Strict"
        # Should fail on POP (0.58 < 0.70) and EV/risk (0.010 < 0.03)
        assert len(reasons) >= 2, f"Expected ≥2 rejection reasons, got {reasons}"

    def test_marginal_trade_passes_wide_but_fails_balanced(self):
        """A marginal trade (low metrics) passes Wide but fails Balanced."""
        balanced = StrategyService.resolve_thresholds("credit_spread", "balanced")
        wide = StrategyService.resolve_thresholds("credit_spread", "wide")
        trade = _make_trade(
            p_win_used=0.48,          # fails balanced (0.55), passes wide (0.45)
            ev_to_risk=0.006,         # fails balanced (0.008), passes wide (0.005)
            return_on_risk=0.003,     # fails balanced (0.005), passes wide (0.002)
            bid_ask_spread_pct=0.025, # 2.5% fails balanced (2.0%), passes wide (3.0%)
            open_interest=30,         # fails balanced (100), passes wide (25)
            volume=2,                 # fails balanced (5), passes wide (1)
        )

        trade["_request"] = balanced
        ok_b, reasons_b = self.plugin.evaluate(trade)
        assert not ok_b, f"Marginal trade should fail Balanced; reasons={reasons_b}"

        trade["_request"] = wide
        ok_w, reasons_w = self.plugin.evaluate(trade)
        assert ok_w, f"Marginal trade should pass Wide; reasons={reasons_w}"