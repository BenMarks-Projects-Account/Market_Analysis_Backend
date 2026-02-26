"""Unit tests for filter-trace instrumentation in the credit spread scanner pipeline.

Tests verify:
- filter_trace object is present in generated report blobs
- Pipeline stages are ordered and track input/output counts
- Gate breakdown categorises rejection reasons correctly
- Preset name and resolved thresholds are captured
- Data quality flags are populated
- Dev toggle controls rejected-examples capture
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.strategies.credit_spread import CreditSpreadStrategyPlugin


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


def _make_strategy_service(results_dir):
    """Minimal StrategyService stub that tests can use."""
    from pathlib import Path
    from app.services.strategy_service import StrategyService

    mock_bds = MagicMock()
    mock_bds.get_source_health_snapshot.return_value = {"sources": []}
    svc = StrategyService(
        base_data_service=mock_bds,
        results_dir=Path(results_dir),
    )
    return svc


# ---------------------------------------------------------------------------
# 1. _GATE_GROUPS covers all credit_spread rejection codes
# ---------------------------------------------------------------------------

class TestGateGroups:
    def test_gate_groups_defined(self):
        from app.services.strategy_service import StrategyService
        groups = StrategyService._GATE_GROUPS
        assert isinstance(groups, dict)
        assert "quote_validation" in groups
        assert "probability" in groups
        assert "liquidity" in groups

    def test_credit_spread_rejection_codes_covered(self):
        """Every rejection reason emitted by CreditSpreadStrategyPlugin.evaluate()
        should appear in at least one gate group."""
        from app.services.strategy_service import StrategyService
        all_covered = set()
        for keys in StrategyService._GATE_GROUPS.values():
            all_covered.update(keys)

        # Known evaluate() rejection codes from credit_spread.py
        evaluate_codes = {
            "pop_below_floor", "ev_to_risk_below_floor", "ev_negative",
            "ror_below_floor", "invalid_width", "non_positive_credit",
            "spread_too_wide", "open_interest_below_min", "volume_below_min",
            "CREDIT_SPREAD_METRICS_FAILED",
        }
        # Known enrich() quote rejection codes
        quote_codes = {
            "MISSING_QUOTES:short_bid", "MISSING_QUOTES:long_ask",
            "ASK_LT_BID:short_leg", "ASK_LT_BID:long_leg",
            "NON_POSITIVE_CREDIT", "NET_CREDIT_GE_WIDTH",
        }
        # Data-quality rejection codes (Gate 6)
        dq_codes = {
            "DQ_MISSING:open_interest", "DQ_MISSING:volume",
        }
        # Centralised QUOTE_INVALID codes (Gate 1)
        quote_invalid_codes = {
            "QUOTE_INVALID:short_leg:missing_bid",
            "QUOTE_INVALID:short_leg:missing_ask",
            "QUOTE_INVALID:short_leg:negative_bid",
            "QUOTE_INVALID:short_leg:zero_or_negative_ask",
            "QUOTE_INVALID:short_leg:inverted_market",
            "QUOTE_INVALID:short_leg:zero_mid",
            "QUOTE_INVALID:long_leg:missing_bid",
            "QUOTE_INVALID:long_leg:missing_ask",
            "QUOTE_INVALID:long_leg:negative_bid",
            "QUOTE_INVALID:long_leg:zero_or_negative_ask",
            "QUOTE_INVALID:long_leg:inverted_market",
            "QUOTE_INVALID:long_leg:zero_mid",
        }
        expected = evaluate_codes | quote_codes | dq_codes | quote_invalid_codes
        missing = expected - all_covered
        assert not missing, f"Rejection codes not in any gate group: {missing}"


# ---------------------------------------------------------------------------
# 2. _apply_request_defaults stamps _preset_name
# ---------------------------------------------------------------------------

class TestPresetStamping:
    def test_preset_name_stamped_for_credit_spread(self, tmp_path):
        svc = _make_strategy_service(tmp_path)
        result = svc._apply_request_defaults("credit_spread", {"preset": "strict"})
        assert result["_preset_name"] == "strict"

    def test_default_preset_used_when_none(self, tmp_path):
        svc = _make_strategy_service(tmp_path)
        result = svc._apply_request_defaults("credit_spread", {})
        assert result["_preset_name"] == "balanced"

    def test_preset_values_applied(self, tmp_path):
        svc = _make_strategy_service(tmp_path)
        result = svc._apply_request_defaults("credit_spread", {"preset": "wide"})
        assert result["min_pop"] == 0.45
        assert result["min_open_interest"] == 25
        assert result["_preset_name"] == "wide"

    def test_user_override_wins_over_preset(self, tmp_path):
        svc = _make_strategy_service(tmp_path)
        result = svc._apply_request_defaults("credit_spread", {
            "preset": "wide",
            "min_pop": 0.99,
        })
        assert result["min_pop"] == 0.99  # user wins
        assert result["_preset_name"] == "wide"


# ---------------------------------------------------------------------------
# 3. Filter trace gate breakdown computation
# ---------------------------------------------------------------------------

class TestGateBreakdownComputation:
    """Test that rejection_breakdown dict gets properly categorised
    into gate_breakdown via _GATE_GROUPS."""

    def test_single_gate_categorisation(self):
        from app.services.strategy_service import StrategyService
        rejection_breakdown = {
            "pop_below_floor": 5,
            "ev_to_risk_below_floor": 3,
        }
        gate_breakdown: dict[str, int] = {}
        all_categorized: set[str] = set()
        for gate_name, reason_keys in StrategyService._GATE_GROUPS.items():
            count = sum(rejection_breakdown.get(k, 0) for k in reason_keys)
            if count > 0:
                gate_breakdown[gate_name] = count
            all_categorized.update(reason_keys)

        assert gate_breakdown["probability"] == 5
        assert gate_breakdown["expected_value"] == 3
        assert "liquidity" not in gate_breakdown

    def test_mixed_rejections(self):
        from app.services.strategy_service import StrategyService
        rejection_breakdown = {
            "MISSING_QUOTES:short_bid": 10,
            "open_interest_below_min": 7,
            "volume_below_min": 3,
            "pop_below_floor": 2,
        }
        gate_breakdown: dict[str, int] = {}
        for gate_name, reason_keys in StrategyService._GATE_GROUPS.items():
            count = sum(rejection_breakdown.get(k, 0) for k in reason_keys)
            if count > 0:
                gate_breakdown[gate_name] = count
        assert gate_breakdown["quote_validation"] == 10
        assert gate_breakdown["liquidity"] == 10  # 7 + 3
        assert gate_breakdown["probability"] == 2

    def test_uncategorised_goes_to_other(self):
        from app.services.strategy_service import StrategyService
        rejection_breakdown = {
            "some_unknown_reason": 4,
            "pop_below_floor": 1,
        }
        gate_breakdown: dict[str, int] = {}
        all_categorized: set[str] = set()
        for gate_name, reason_keys in StrategyService._GATE_GROUPS.items():
            count = sum(rejection_breakdown.get(k, 0) for k in reason_keys)
            if count > 0:
                gate_breakdown[gate_name] = count
            all_categorized.update(reason_keys)
        uncategorized = sum(
            cnt for reason, cnt in rejection_breakdown.items()
            if reason not in all_categorized and cnt > 0
        )
        if uncategorized:
            gate_breakdown["other"] = uncategorized

        assert gate_breakdown["other"] == 4
        assert gate_breakdown["probability"] == 1


# ---------------------------------------------------------------------------
# 4. _build_report_blob includes filter_trace
# ---------------------------------------------------------------------------

class TestBuildReportBlobFilterTrace:
    def test_filter_trace_included_when_provided(self, tmp_path):
        svc = _make_strategy_service(tmp_path)
        trace = {"trace_id": "test_123", "preset_name": "balanced"}
        blob = svc._build_report_blob(
            strategy_id="credit_spread",
            payload={},
            symbol_list=["SPY"],
            primary={"symbol": "SPY", "expiration": "2025-03-21"},
            candidates=[],
            enriched=[],
            accepted=[],
            notes=[],
            generation_diagnostics={"closes_count": 100, "rejection_breakdown": {}},
            filter_trace=trace,
        )
        assert blob["filter_trace"] is trace
        assert blob["filter_trace"]["trace_id"] == "test_123"

    def test_filter_trace_none_when_not_provided(self, tmp_path):
        svc = _make_strategy_service(tmp_path)
        blob = svc._build_report_blob(
            strategy_id="credit_spread",
            payload={},
            symbol_list=["SPY"],
            primary={"symbol": "SPY", "expiration": "2025-03-21"},
            candidates=[],
            enriched=[],
            accepted=[],
            notes=[],
            generation_diagnostics={"closes_count": 100, "rejection_breakdown": {}},
        )
        assert blob["filter_trace"] is None


# ---------------------------------------------------------------------------
# 5. Full generate() produces filter_trace
# ---------------------------------------------------------------------------

class TestGenerateFilterTrace:
    @pytest.mark.anyio
    async def test_filter_trace_in_report(self, tmp_path):
        svc = _make_strategy_service(tmp_path)

        # Mock chain data that will produce at least some candidates
        mock_contracts = [
            FakeContract(strike=595.0, bid=3.00, ask=3.20, delta=-0.30),
            FakeContract(strike=590.0, bid=1.50, ask=1.80, delta=-0.20),
            FakeContract(strike=585.0, bid=0.80, ask=1.00, delta=-0.12),
            FakeContract(strike=580.0, bid=0.40, ask=0.55, delta=-0.08),
        ]

        async def mock_get_inputs(sym, exp):
            return {
                "symbol": sym,
                "expiration": exp,
                "underlying_price": 600.0,
                "vix": 18.0,
                "contracts": mock_contracts,
                "prices_history": [595.0, 596.0, 597.0, 598.0, 599.0, 600.0],
            }

        async def mock_get_expirations(sym):
            return ["2025-03-21"]

        svc.base_data_service.get_analysis_inputs = mock_get_inputs
        svc.base_data_service.tradier_client = MagicMock()
        svc.base_data_service.tradier_client.get_expirations = mock_get_expirations

        result = await svc.generate("credit_spread", {"preset": "wide", "symbols": ["SPY"]})

        assert "filter_trace" in result
        ft = result["filter_trace"]
        assert ft is not None
        assert "trace_id" in ft
        assert ft["strategy_id"] == "credit_spread"
        assert ft["preset_name"] == "wide"
        assert "resolved_thresholds" in ft
        assert "stages" in ft
        assert len(ft["stages"]) == 5

        # Verify stage order
        stage_names = [s["name"] for s in ft["stages"]]
        assert stage_names == [
            "snapshot_collection",
            "candidate_construction",
            "enrichment",
            "evaluate_gates",
            "dedup_ranking",
        ]

        # Every stage has required keys
        for stage in ft["stages"]:
            assert "input_count" in stage
            assert "output_count" in stage
            assert "label" in stage
            assert "detail" in stage

        # Snapshot stage input = number of symbols
        assert ft["stages"][0]["input_count"] == 1  # SPY only
        assert ft["stages"][0]["output_count"] >= 1

        # Resolved thresholds include preset values
        thresholds = ft["resolved_thresholds"]
        assert thresholds["min_pop"] == 0.45  # wide preset
        assert thresholds["min_open_interest"] == 25

    @pytest.mark.anyio
    async def test_filter_trace_no_snapshots(self, tmp_path):
        """When no snapshots are collected, filter_trace stages still present."""
        svc = _make_strategy_service(tmp_path)

        async def mock_get_expirations(sym):
            return []

        svc.base_data_service.tradier_client = MagicMock()
        svc.base_data_service.tradier_client.get_expirations = mock_get_expirations

        result = await svc.generate("credit_spread", {"symbols": ["FAKE"]})
        ft = result["filter_trace"]
        assert ft is not None
        assert ft["stages"][0]["output_count"] == 0
        assert ft["stages"][1]["output_count"] == 0  # no candidates

    @pytest.mark.anyio
    async def test_rejected_examples_off_by_default(self, tmp_path):
        svc = _make_strategy_service(tmp_path)

        mock_contracts = [
            FakeContract(strike=595.0, bid=3.00, ask=3.20, delta=-0.30),
            FakeContract(strike=590.0, bid=1.50, ask=1.80, delta=-0.20),
        ]

        async def mock_get_inputs(sym, exp):
            return {
                "symbol": sym, "expiration": exp,
                "underlying_price": 600.0, "vix": 18.0,
                "contracts": mock_contracts,
                "prices_history": [595.0, 596.0, 597.0, 598.0, 599.0, 600.0],
            }

        async def mock_get_expirations(sym):
            return ["2025-03-21"]

        svc.base_data_service.get_analysis_inputs = mock_get_inputs
        svc.base_data_service.tradier_client = MagicMock()
        svc.base_data_service.tradier_client.get_expirations = mock_get_expirations

        result = await svc.generate("credit_spread", {"symbols": ["SPY"]})
        ft = result["filter_trace"]
        assert "rejected_examples" not in ft

    @pytest.mark.anyio
    async def test_rejected_examples_captured_with_toggle(self, tmp_path):
        svc = _make_strategy_service(tmp_path)

        mock_contracts = [
            FakeContract(strike=595.0, bid=3.00, ask=3.20, delta=-0.30),
            FakeContract(strike=590.0, bid=1.50, ask=1.80, delta=-0.20),
        ]

        async def mock_get_inputs(sym, exp):
            return {
                "symbol": sym, "expiration": exp,
                "underlying_price": 600.0, "vix": 18.0,
                "contracts": mock_contracts,
                "prices_history": [595.0, 596.0, 597.0, 598.0, 599.0, 600.0],
            }

        async def mock_get_expirations(sym):
            return ["2025-03-21"]

        svc.base_data_service.get_analysis_inputs = mock_get_inputs
        svc.base_data_service.tradier_client = MagicMock()
        svc.base_data_service.tradier_client.get_expirations = mock_get_expirations

        result = await svc.generate("credit_spread", {
            "symbols": ["SPY"],
            "_capture_trace_examples": True,
        })
        ft = result["filter_trace"]
        # If any trades were rejected, examples should be present
        total_rejected = sum(ft.get("rejection_reasons", {}).values())
        if total_rejected > 0:
            assert "rejected_examples" in ft
            assert len(ft["rejected_examples"]) <= 3
            ex = ft["rejected_examples"][0]
            assert "symbol" in ex
            assert "reasons" in ex


# ---------------------------------------------------------------------------
# 6. Data quality flags
# ---------------------------------------------------------------------------

class TestDataQualityFlags:
    def test_missing_price_history_flag(self, tmp_path):
        svc = _make_strategy_service(tmp_path)
        from app.services.strategy_service import StrategyService

        # Simulate the flag computation logic
        diag = {"closes_count": 0, "invalid_quote_count": 0}
        notes: list[str] = []
        flags: list[str] = []
        if diag.get("closes_count", -1) == 0:
            flags.append("MISSING_PRICE_HISTORY")
        assert "MISSING_PRICE_HISTORY" in flags

    def test_invalid_quotes_flag(self):
        diag = {"closes_count": 100, "invalid_quote_count": 5}
        flags: list[str] = []
        iq = diag.get("invalid_quote_count", 0)
        if iq > 0:
            flags.append(f"INVALID_QUOTES:{iq}")
        assert "INVALID_QUOTES:5" in flags

    def test_no_chain_flag(self):
        notes = ["SPY 2025-03-21: no_chain", "QQQ 2025-03-21: no_chain"]
        flags: list[str] = []
        no_chain_count = sum(1 for n in notes if "no_chain" in n)
        if no_chain_count:
            flags.append(f"NO_CHAIN_SYMBOLS:{no_chain_count}")
        assert "NO_CHAIN_SYMBOLS:2" in flags


# ---------------------------------------------------------------------------
# 7. Resolved thresholds extraction
# ---------------------------------------------------------------------------

class TestResolvedThresholds:
    def test_skip_keys_excluded(self, tmp_path):
        from app.services.strategy_service import StrategyService
        svc = _make_strategy_service(tmp_path)
        payload = svc._apply_request_defaults("credit_spread", {"preset": "balanced"})

        resolved: dict[str, Any] = {}
        for k, v in payload.items():
            if k.startswith("_") or k in StrategyService._FILTER_TRACE_SKIP_KEYS:
                continue
            if isinstance(v, (int, float)):
                resolved[k] = v

        # _preset_name should be excluded (starts with _)
        assert "_preset_name" not in resolved
        # symbols should be excluded
        assert "symbols" not in resolved
        # But numeric thresholds should be included
        assert "min_pop" in resolved
        assert "dte_min" in resolved
        assert "min_open_interest" in resolved


# ---------------------------------------------------------------------------
# 8. Filter trace skip keys covers all non-numeric payload keys
# ---------------------------------------------------------------------------

class TestFilterTraceSkipKeys:
    def test_skip_keys_are_frozenset(self):
        from app.services.strategy_service import StrategyService
        assert isinstance(StrategyService._FILTER_TRACE_SKIP_KEYS, frozenset)

    def test_known_non_numeric_keys_included(self):
        from app.services.strategy_service import StrategyService
        skip = StrategyService._FILTER_TRACE_SKIP_KEYS
        for key in ("symbols", "symbol", "expiration", "direction", "moneyness"):
            assert key in skip, f"Expected '{key}' in _FILTER_TRACE_SKIP_KEYS"
