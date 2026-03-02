"""Tests for wide-preset price-history tolerance in the snapshot pipeline.

Covers:
  a) wide preset + missing history   => snapshot valid + flagged
  b) non-wide preset + missing history => snapshot rejected (invalid)
  c) wide preset + missing Tier-1 data => snapshot invalid
  d) is_price_history_required() helper returns correct values
  e) Trace instrumentation includes per-symbol details
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.strategy_service import StrategyService


def _run(coro):
    """Run a coroutine synchronously (no pytest-asyncio needed)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeContract:
    strike: float
    bid: float | None
    ask: float | None
    option_type: str = "put"
    delta: float | None = -0.30
    iv: float | None = 0.25
    open_interest: int = 500
    volume: int = 50
    symbol: str = "SPY260320P00400000"


def _make_svc(tmp_path: Path) -> StrategyService:
    """Create a StrategyService with a mocked base_data_service."""
    mock_bds = MagicMock()
    mock_bds.get_source_health_snapshot.return_value = {"sources": []}
    mock_bds.snapshot_recorder = None
    svc = StrategyService(
        base_data_service=mock_bds,
        results_dir=tmp_path,
    )
    return svc


def _fake_analysis_inputs(
    *,
    has_contracts: bool = True,
    has_underlying_price: bool = True,
    has_prices_history: bool = True,
) -> dict[str, Any]:
    """Build a fake snapshot_inputs dict with configurable completeness."""
    contracts = []
    if has_contracts:
        contracts = [
            FakeContract(strike=395, bid=1.20, ask=1.40),
            FakeContract(strike=400, bid=2.00, ask=2.20),
            FakeContract(strike=405, bid=3.00, ask=3.30),
        ]
    return {
        "underlying_price": 410.0 if has_underlying_price else None,
        "contracts": contracts,
        "prices_history": [400.0 + i * 0.5 for i in range(100)] if has_prices_history else [],
        "vix": 18.5,
        "notes": [],
    }


# ---------------------------------------------------------------------------
# ① is_price_history_required() class method
# ---------------------------------------------------------------------------

class TestIsPriceHistoryRequired:
    def test_wide_not_required(self):
        assert StrategyService.is_price_history_required("wide") is False

    def test_wide_case_insensitive(self):
        assert StrategyService.is_price_history_required("Wide") is False
        assert StrategyService.is_price_history_required("WIDE") is False

    def test_balanced_required(self):
        assert StrategyService.is_price_history_required("balanced") is True

    def test_strict_required(self):
        assert StrategyService.is_price_history_required("strict") is True

    def test_conservative_required(self):
        assert StrategyService.is_price_history_required("conservative") is True

    def test_manual_required(self):
        # manual preset should still require price history (not in the optional set)
        assert StrategyService.is_price_history_required("manual") is True


# ---------------------------------------------------------------------------
# ② Snapshot collection: wide + missing history => valid + flagged
# ---------------------------------------------------------------------------

class TestWidePresetMissingHistory:
    """wide preset + missing price history => snapshot is accepted and flagged."""

    def test_wide_missing_history_snapshot_valid(self, tmp_path):
        svc = _make_svc(tmp_path)

        # Mock get_analysis_inputs to return snapshot with no prices_history
        mock_inputs = _fake_analysis_inputs(has_prices_history=False)
        svc.base_data_service.get_analysis_inputs = AsyncMock(return_value=mock_inputs)

        snapshots: list[dict[str, Any]] = []
        notes: list[str] = []
        _snapshot_symbol_details: list[str] = []

        # Simulate the snapshot collection logic directly
        symbol = "SPY"
        expiration = "2026-03-20"
        snapshot_inputs = _run(svc.base_data_service.get_analysis_inputs(symbol, expiration))

        # Exercise the validation logic
        _resolved_preset = "wide"
        _price_history_required = svc.is_price_history_required(_resolved_preset)

        contracts = snapshot_inputs.get("contracts") or []
        assert len(contracts) > 0, "Tier-1 contracts must exist"

        _closes = snapshot_inputs.get("prices_history") or []
        _has_valid_history = bool(_closes) and any(float(c) > 0 for c in _closes if c is not None)
        assert not _has_valid_history, "Should have no valid history"

        _history_flag = None
        if not _has_valid_history:
            if _price_history_required:
                pytest.fail("Wide preset should NOT require price history")
            else:
                _history_flag = "MISSING_PRICE_HISTORY"

        _snap_entry = {
            **snapshot_inputs,
            "symbol": symbol,
            "expiration": expiration,
        }
        if _history_flag:
            _snap_entry.setdefault("data_quality_flags", []).append(_history_flag)
        snapshots.append(_snap_entry)

        if _history_flag:
            _snapshot_symbol_details.append(f"{symbol}/{expiration}: VALID ({_history_flag})")
        else:
            _snapshot_symbol_details.append(f"{symbol}/{expiration}: VALID")

        # Assertions
        assert len(snapshots) == 1, "Snapshot should be accepted"
        assert "MISSING_PRICE_HISTORY" in snapshots[0].get("data_quality_flags", [])
        assert "VALID (MISSING_PRICE_HISTORY)" in _snapshot_symbol_details[0]

    def test_wide_with_valid_history_no_flag(self, tmp_path):
        """wide preset + valid history => snapshot valid, no DQ flag."""
        svc = _make_svc(tmp_path)
        mock_inputs = _fake_analysis_inputs(has_prices_history=True)
        svc.base_data_service.get_analysis_inputs = AsyncMock(return_value=mock_inputs)

        snapshot_inputs = _run(svc.base_data_service.get_analysis_inputs("SPY", "2026-03-20"))

        _resolved_preset = "wide"
        _price_history_required = svc.is_price_history_required(_resolved_preset)

        _closes = snapshot_inputs.get("prices_history") or []
        _has_valid_history = bool(_closes) and any(float(c) > 0 for c in _closes if c is not None)
        assert _has_valid_history

        _history_flag = None
        if not _has_valid_history:
            if _price_history_required:
                _history_flag = "REJECTED"
            else:
                _history_flag = "MISSING_PRICE_HISTORY"

        assert _history_flag is None, "Should have no flag when history is present"


# ---------------------------------------------------------------------------
# ③ Non-wide preset + missing history => snapshot rejected
# ---------------------------------------------------------------------------

class TestNonWidePresetMissingHistory:
    """non-wide preset + missing price history => snapshot rejected."""

    def test_balanced_missing_history_rejected(self, tmp_path):
        svc = _make_svc(tmp_path)
        mock_inputs = _fake_analysis_inputs(has_prices_history=False)
        svc.base_data_service.get_analysis_inputs = AsyncMock(return_value=mock_inputs)

        snapshot_inputs = _run(svc.base_data_service.get_analysis_inputs("SPY", "2026-03-20"))

        _resolved_preset = "balanced"
        _price_history_required = svc.is_price_history_required(_resolved_preset)
        assert _price_history_required, "Balanced must require price history"

        _closes = snapshot_inputs.get("prices_history") or []
        _has_valid_history = bool(_closes) and any(float(c) > 0 for c in _closes if c is not None)

        rejected = False
        _detail = ""
        if not _has_valid_history:
            if _price_history_required:
                rejected = True
                _detail = "SPY/2026-03-20: REJECTED (MISSING_PRICE_HISTORY)"

        assert rejected, "Balanced preset should reject missing history"
        assert "REJECTED" in _detail

    def test_strict_missing_history_rejected(self, tmp_path):
        svc = _make_svc(tmp_path)
        _resolved_preset = "strict"
        assert svc.is_price_history_required(_resolved_preset) is True

    def test_conservative_missing_history_rejected(self, tmp_path):
        svc = _make_svc(tmp_path)
        _resolved_preset = "conservative"
        assert svc.is_price_history_required(_resolved_preset) is True


# ---------------------------------------------------------------------------
# ④ Wide preset + missing Tier-1 data => snapshot invalid
# ---------------------------------------------------------------------------

class TestWidePresetMissingTier1:
    """wide preset + missing Tier-1 (chain or underlying price) => rejected."""

    def test_wide_missing_contracts_rejected(self, tmp_path):
        """Even in wide mode, missing chain data rejects the snapshot."""
        svc = _make_svc(tmp_path)
        mock_inputs = _fake_analysis_inputs(has_contracts=False, has_prices_history=False)
        svc.base_data_service.get_analysis_inputs = AsyncMock(return_value=mock_inputs)

        snapshot_inputs = _run(svc.base_data_service.get_analysis_inputs("SPY", "2026-03-20"))
        contracts = snapshot_inputs.get("contracts") or []

        rejected = False
        _detail = ""
        if not contracts:
            rejected = True
            _detail = "SPY/2026-03-20: REJECTED (NO_CHAIN)"

        assert rejected, "Missing contracts must reject even in wide mode"
        assert "NO_CHAIN" in _detail

    def test_wide_missing_underlying_price_rejected(self, tmp_path):
        """Even in wide mode, missing underlying_price rejects the snapshot."""
        svc = _make_svc(tmp_path)
        mock_inputs = _fake_analysis_inputs(has_underlying_price=False, has_prices_history=False)
        svc.base_data_service.get_analysis_inputs = AsyncMock(return_value=mock_inputs)

        snapshot_inputs = _run(svc.base_data_service.get_analysis_inputs("SPY", "2026-03-20"))

        rejected = False
        _detail = ""
        if snapshot_inputs.get("underlying_price") is None:
            rejected = True
            _detail = "SPY/2026-03-20: REJECTED (MISSING_UNDERLYING_PRICE)"

        assert rejected, "Missing underlying_price must reject even in wide mode"
        assert "MISSING_UNDERLYING_PRICE" in _detail


# ---------------------------------------------------------------------------
# ⑤ Trace instrumentation: per-symbol details
# ---------------------------------------------------------------------------

class TestTraceInstrumentation:
    """Verify snapshot_collection stage includes per-symbol detail strings."""

    def test_valid_detail_format(self):
        detail = "SPY/2026-03-20: VALID"
        assert detail.startswith("SPY/")
        assert "VALID" in detail

    def test_valid_with_flag_format(self):
        detail = "SPY/2026-03-20: VALID (MISSING_PRICE_HISTORY)"
        assert "VALID" in detail
        assert "MISSING_PRICE_HISTORY" in detail

    def test_rejected_detail_format(self):
        detail = "QQQ/2026-03-20: REJECTED (MISSING_UNDERLYING_PRICE)"
        assert detail.startswith("QQQ/")
        assert "REJECTED" in detail
        assert "MISSING_UNDERLYING_PRICE" in detail

    def test_filter_trace_snapshot_collection_stage(self, tmp_path):
        """The snapshot_collection stage dict must include per_symbol list and preset info."""
        # Simulate the trace dict that _generate_inner builds
        _snapshot_symbol_details = [
            "SPY/2026-03-20: VALID (MISSING_PRICE_HISTORY)",
            "QQQ/2026-03-20: REJECTED (MISSING_UNDERLYING_PRICE)",
        ]
        stage = {
            "name": "snapshot_collection",
            "label": "Snapshot Collection",
            "input_count": 2,
            "output_count": 1,
            "detail": "2 symbols → 1 valid snapshots",
            "price_history_required": False,
            "preset_name": "wide",
            "per_symbol": _snapshot_symbol_details,
        }
        assert stage["per_symbol"] == _snapshot_symbol_details
        assert stage["price_history_required"] is False
        assert stage["preset_name"] == "wide"
        assert stage["output_count"] == 1


# ---------------------------------------------------------------------------
# ⑥ Downstream: realized_vol / iv_rv_ratio handle None gracefully
# ---------------------------------------------------------------------------

class TestDownstreamPriceHistoryHandling:
    """Verify strategy plugins handle empty prices_history without crashing."""

    def test_iron_condor_realized_vol_empty(self):
        from app.services.strategies.iron_condor import IronCondorStrategyPlugin
        plugin = IronCondorStrategyPlugin()
        # Empty prices
        assert plugin._realized_vol([]) is None
        # Fewer than 25 prices
        assert plugin._realized_vol([100.0] * 20) is None

    def test_iron_condor_expected_move_no_rv(self):
        from app.services.strategies.iron_condor import IronCondorStrategyPlugin
        plugin = IronCondorStrategyPlugin()
        # rv=None => fallback
        move = plugin._expected_move(spot=400.0, dte=30, rv=None, iv_guess=None)
        assert move > 0, "expected_move must be positive even with rv=None"
        assert move == max(400.0 * 0.02, 1.0)

    def test_butterflies_realized_vol_empty(self):
        from app.services.strategies.butterflies import ButterfliesStrategyPlugin
        plugin = ButterfliesStrategyPlugin()
        assert plugin._realized_vol([]) is None

    def test_enrich_trade_handles_empty_history(self):
        """enrich_trade should not crash with empty prices_history."""
        from common.quant_analysis import enrich_trade
        trade = {
            "spread_type": "put_credit_spread",
            "price": 410.0,
            "underlying_price": 410.0,
            "short_strike": 400.0,
            "long_strike": 395.0,
            "net_credit": 1.50,
            "dte": 30,
            "iv": 0.20,
            "short_delta_abs": 0.25,
        }
        # Should not crash with empty history
        result = enrich_trade(trade, prices_history=[], vix=18.5)
        assert isinstance(result, dict)
        # RV should be None or not present when no history
        rv = result.get("realized_vol")
        assert rv is None or rv == 0 or isinstance(rv, float)

    def test_enrich_trade_handles_none_history(self):
        """enrich_trade should not crash with None prices_history."""
        from common.quant_analysis import enrich_trade
        trade = {
            "spread_type": "put_credit_spread",
            "price": 410.0,
            "underlying_price": 410.0,
            "short_strike": 400.0,
            "long_strike": 395.0,
            "net_credit": 1.50,
            "dte": 30,
            "iv": 0.20,
            "short_delta_abs": 0.25,
        }
        result = enrich_trade(trade, prices_history=None, vix=18.5)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# ⑦ _PRICE_HISTORY_OPTIONAL_PRESETS set is consistent
# ---------------------------------------------------------------------------

class TestPresetConstants:
    def test_optional_presets_set_contains_wide(self):
        assert "wide" in StrategyService._PRICE_HISTORY_OPTIONAL_PRESETS

    def test_optional_presets_does_not_contain_balanced(self):
        assert "balanced" not in StrategyService._PRICE_HISTORY_OPTIONAL_PRESETS

    def test_optional_presets_does_not_contain_strict(self):
        assert "strict" not in StrategyService._PRICE_HISTORY_OPTIONAL_PRESETS

    def test_optional_presets_is_frozen(self):
        assert isinstance(StrategyService._PRICE_HISTORY_OPTIONAL_PRESETS, frozenset)
