"""Prompt 11 — compact stock output, review summary, top-metrics, card mapping.

Focused tests covering:
    1. _extract_compact_stock_candidate output shape
    2. select_top_metrics stable field selection
    3. build_review_summary deterministic output
    4. Market-context enrichment uses correct consumer_summary keys
    5. Compact candidates in output.json (no raw 27-field blobs)
    6. No run_id leak to card-level candidates

Test-only — run via: python -m pytest tests/test_prompt11_stock_card.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.workflows.stock_opportunity_runner import (
    RunnerConfig,
    StockOpportunityDeps,
    _extract_compact_stock_candidate,
    build_review_summary,
    select_top_metrics,
    run_stock_opportunity,
)


# ═══════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════

def _make_normalized_candidate(**overrides: Any) -> dict[str, Any]:
    """Build a normalized 27-field candidate for testing."""
    base: dict[str, Any] = {
        "candidate_id": "AAPL|stock_pullback_swing",
        "scanner_key": "stock_pullback_swing",
        "scanner_name": "Pullback Swing",
        "strategy_family": "stock",
        "setup_type": "pullback_swing",
        "asset_class": "equity",
        "symbol": "AAPL",
        "underlying": "AAPL",
        "direction": "long",
        "thesis_summary": ["Strong uptrend with healthy pullback to 20 EMA"],
        "entry_context": {
            "price": 185.50,
            "entry_reference": 183.0,
            "state": "uptrend_pullback",
        },
        "time_horizon": "swing",
        "setup_quality": 72.5,
        "confidence": 0.85,
        "risk_definition": {"type": "stop_loss_based", "notes": []},
        "reward_profile": {"type": "price_target_based", "composite_score": 72.5},
        "supporting_signals": ["RSI oversold bounce", "MACD crossover"],
        "risk_flags": ["High ATR%"],
        "invalidation_signals": [],
        "market_context_tags": ["stock_pullback_swing", "uptrend_pullback"],
        "position_sizing_notes": None,
        "data_quality": {"source": "tradier", "source_confidence": 0.85, "missing_fields": []},
        "source_status": {"history": "tradier", "confidence": 0.85},
        "pricing_snapshot": {"price": 185.50, "underlying_price": None},
        "strategy_structure": None,
        "candidate_metrics": {
            "composite_score": 72.5,
            "rsi": 42.3,
            "atr_pct": 0.018,
            "volume_ratio": 1.5,
            "macd_hist": 0.3,
            "score_breakdown": {"trend": 80, "value": 65},
        },
        "detail_sections": {},
        "generated_at": "2026-03-20T10:00:00+00:00",
        # Enrichment fields (added by Stage 4)
        "market_state_ref": "mi_run_abc",
        "market_regime": "bullish",
        "risk_environment": "stable",
        "rank": 1,
    }
    base.update(overrides)
    return base


def _make_stub_scan_result(n: int = 3) -> dict[str, Any]:
    """Create a stub scan result dict for StockEngineService.scan()."""
    candidates = []
    for i in range(n):
        candidates.append({
            "symbol": f"SYM{i}",
            "strategy_id": "stock_pullback_swing",
            "composite_score": 70 - i * 5,
            "price": 100 + i,
            "confidence": 0.8,
            "metrics": {"rsi": 40 + i, "atr_pct": 0.02},
            "score_breakdown": {"trend": 75, "value": 65},
            "thesis": [f"Thesis point for SYM{i}"],
            "data_source": {"history": "tradier", "confidence": 0.8},
        })
    return {
        "candidates": candidates,
        "scanners": [
            {"scanner_key": "stock_pullback_swing", "status": "ok"},
            {"scanner_key": "stock_momentum_breakout", "status": "ok"},
            {"scanner_key": "stock_mean_reversion", "status": "ok"},
            {"scanner_key": "stock_volatility_expansion", "status": "ok"},
        ],
        "warnings": [],
    }


class _StubEngine:
    async def scan(self) -> dict[str, Any]:
        return _make_stub_scan_result()


# ═══════════════════════════════════════════════════════════════════════
# 1. _extract_compact_stock_candidate
# ═══════════════════════════════════════════════════════════════════════

class TestExtractCompactStockCandidate:
    """Compact candidate shape matches card-friendly contract."""

    def test_required_fields_present(self):
        cand = _make_normalized_candidate()
        compact = _extract_compact_stock_candidate(cand)

        required = {
            "symbol", "scanner_key", "scanner_name", "setup_type",
            "direction", "setup_quality", "confidence", "rank",
            "thesis_summary", "supporting_signals", "risk_flags",
            "entry_context", "market_regime", "risk_environment",
            "market_state_ref", "top_metrics", "review_summary",
        }
        assert required.issubset(compact.keys()), (
            f"Missing: {required - compact.keys()}"
        )

    def test_no_raw_27_field_blobs(self):
        """Compact candidate must NOT carry internal normalized fields."""
        cand = _make_normalized_candidate()
        compact = _extract_compact_stock_candidate(cand)

        excluded = {
            "candidate_id", "strategy_family", "asset_class",
            "underlying", "time_horizon", "risk_definition",
            "reward_profile", "invalidation_signals",
            "market_context_tags", "position_sizing_notes",
            "data_quality", "source_status", "pricing_snapshot",
            "strategy_structure", "candidate_metrics", "detail_sections",
            "generated_at",
        }
        leaked = excluded & compact.keys()
        assert leaked == set(), f"Internal fields leaked: {leaked}"

    def test_no_run_id_in_candidate(self):
        """run_id must not appear in individual candidates."""
        cand = _make_normalized_candidate()
        compact = _extract_compact_stock_candidate(cand)
        assert "run_id" not in compact

    def test_values_propagated(self):
        cand = _make_normalized_candidate(
            symbol="MSFT", setup_quality=88.0, confidence=0.92, rank=2,
            direction="long", scanner_name="Momentum Breakout",
        )
        compact = _extract_compact_stock_candidate(cand)
        assert compact["symbol"] == "MSFT"
        assert compact["setup_quality"] == 88.0
        assert compact["confidence"] == 0.92
        assert compact["rank"] == 2
        assert compact["direction"] == "long"
        assert compact["scanner_name"] == "Momentum Breakout"

    def test_thesis_and_signals_are_lists(self):
        cand = _make_normalized_candidate(
            thesis_summary=["bullet1", "bullet2"],
            supporting_signals=["sig1"],
            risk_flags=["risk1", "risk2"],
        )
        compact = _extract_compact_stock_candidate(cand)
        assert compact["thesis_summary"] == ["bullet1", "bullet2"]
        assert compact["supporting_signals"] == ["sig1"]
        assert compact["risk_flags"] == ["risk1", "risk2"]

    def test_null_lists_default_to_empty(self):
        cand = _make_normalized_candidate(
            thesis_summary=None, supporting_signals=None, risk_flags=None,
        )
        compact = _extract_compact_stock_candidate(cand)
        assert compact["thesis_summary"] == []
        assert compact["supporting_signals"] == []
        assert compact["risk_flags"] == []


# ═══════════════════════════════════════════════════════════════════════
# 2. select_top_metrics
# ═══════════════════════════════════════════════════════════════════════

class TestSelectTopMetrics:

    def test_includes_price_and_trend(self):
        cand = _make_normalized_candidate()
        top = select_top_metrics(cand)
        assert top["price"] == 185.50
        assert top["trend_state"] == "uptrend_pullback"

    def test_includes_known_metric_keys(self):
        cand = _make_normalized_candidate()
        top = select_top_metrics(cand)
        assert "composite_score" in top
        assert "rsi" in top
        assert "atr_pct" in top

    def test_missing_metrics_omitted(self):
        """Keys not present in candidate_metrics should not appear."""
        cand = _make_normalized_candidate(candidate_metrics={})
        top = select_top_metrics(cand)
        assert "composite_score" not in top
        assert "rsi" not in top
        # price/trend_state still come from entry_context
        assert "price" in top

    def test_stable_subset(self):
        """Output should only contain expected keys (no extras)."""
        cand = _make_normalized_candidate()
        top = select_top_metrics(cand)
        allowed = {"price", "trend_state", "composite_score", "rsi",
                    "atr_pct", "volume_ratio", "macd_hist"}
        assert set(top.keys()).issubset(allowed)


# ═══════════════════════════════════════════════════════════════════════
# 3. build_review_summary
# ═══════════════════════════════════════════════════════════════════════

class TestBuildReviewSummary:

    def test_includes_scanner_and_symbol(self):
        cand = _make_normalized_candidate(
            scanner_name="Pullback Swing", symbol="AAPL",
        )
        summary = build_review_summary(cand)
        assert "Pullback Swing" in summary
        assert "AAPL" in summary

    def test_includes_quality_descriptor(self):
        cand = _make_normalized_candidate(setup_quality=80)
        summary = build_review_summary(cand)
        assert "strong" in summary

    def test_moderate_quality(self):
        cand = _make_normalized_candidate(setup_quality=55)
        summary = build_review_summary(cand)
        assert "moderate" in summary

    def test_speculative_quality(self):
        cand = _make_normalized_candidate(setup_quality=30)
        summary = build_review_summary(cand)
        assert "speculative" in summary

    def test_includes_trend_state(self):
        cand = _make_normalized_candidate()
        summary = build_review_summary(cand)
        assert "uptrend_pullback" in summary

    def test_includes_market_regime(self):
        cand = _make_normalized_candidate(market_regime="bullish")
        summary = build_review_summary(cand)
        assert "bullish" in summary

    def test_includes_first_thesis_bullet(self):
        cand = _make_normalized_candidate(
            thesis_summary=["Strong uptrend with pullback", "Volume confirming"],
        )
        summary = build_review_summary(cand)
        assert "Strong uptrend with pullback" in summary

    def test_deterministic(self):
        """Same input produces same output."""
        cand = _make_normalized_candidate()
        s1 = build_review_summary(cand)
        s2 = build_review_summary(cand)
        assert s1 == s2

    def test_returns_string(self):
        cand = _make_normalized_candidate()
        assert isinstance(build_review_summary(cand), str)


# ═══════════════════════════════════════════════════════════════════════
# 4. Market-context enrichment keys
# ═══════════════════════════════════════════════════════════════════════

class TestMarketContextEnrichment:

    @pytest.mark.asyncio
    async def test_market_regime_from_consumer_summary(self, tmp_path: Path):
        """market_regime should come from consumer_summary.market_state."""
        from datetime import datetime, timezone
        from app.workflows.market_state_contract import MARKET_STATE_CONTRACT_VERSION
        from app.workflows.market_state_discovery import (
            POINTER_FILENAME, get_market_state_dir, make_artifact_filename,
        )

        ts_dt = datetime(2026, 3, 20, 10, 0, 0, tzinfo=timezone.utc)
        ts = ts_dt.isoformat()
        artifact_id = "mi_run_test_123"
        artifact = {
            "contract_version": MARKET_STATE_CONTRACT_VERSION,
            "artifact_id": artifact_id,
            "workflow_id": "market_intelligence",
            "generated_at": ts,
            "publication": {"status": "valid"},
            "freshness": {"generated_at": ts, "freshness_tier": "fresh"},
            "quality": {"overall": "good"},
            "market_snapshot": {"metrics": {}, "snapshot_at": ts},
            "engines": {},
            "composite": {
                "market_state": "risk_on",
                "support_state": "strong",
                "stability_state": "calm",
                "confidence": 0.8,
            },
            "conflicts": [],
            "model_interpretation": None,
            "consumer_summary": {
                "market_state": "risk_on",
                "support_state": "strong",
                "stability_state": "calm",
                "confidence": 0.8,
                "vix": 15.0,
                "regime_tags": ["bullish"],
                "is_degraded": False,
                "summary_text": "Markets are risk-on.",
            },
            "lineage": {"run_id": "run_test"},
            "warnings": [],
        }

        ms_dir = get_market_state_dir(tmp_path)
        ms_dir.mkdir(parents=True, exist_ok=True)
        fname = make_artifact_filename(ts_dt)
        (ms_dir / fname).write_text(json.dumps(artifact))
        (ms_dir / POINTER_FILENAME).write_text(json.dumps({
            "artifact_filename": fname,
            "artifact_id": artifact_id,
            "published_at": ts,
            "status": "valid",
            "contract_version": MARKET_STATE_CONTRACT_VERSION,
        }))

        config = RunnerConfig(data_dir=tmp_path)
        deps = StockOpportunityDeps(stock_engine_service=_StubEngine())
        result = await run_stock_opportunity(config, deps)

        assert result.status == "completed"
        output = json.loads(Path(result.artifact_path).read_text())

        for cand in output["candidates"]:
            # market_regime from consumer_summary.market_state
            assert cand["market_regime"] == "risk_on"
            # risk_environment from consumer_summary.stability_state
            assert cand["risk_environment"] == "calm"

    @pytest.mark.asyncio
    async def test_degraded_market_state_still_produces_candidates(self, tmp_path: Path):
        """When market state is unavailable, candidates still appear with null context."""
        config = RunnerConfig(data_dir=tmp_path)
        deps = StockOpportunityDeps(stock_engine_service=_StubEngine())
        result = await run_stock_opportunity(config, deps)

        assert result.status == "completed"
        output = json.loads(Path(result.artifact_path).read_text())

        for cand in output["candidates"]:
            assert cand["market_regime"] is None
            assert cand["risk_environment"] is None
            assert cand["market_state_ref"] is None


# ═══════════════════════════════════════════════════════════════════════
# 5. Output.json shape — compact candidates
# ═══════════════════════════════════════════════════════════════════════

class TestOutputCompactShape:

    @pytest.mark.asyncio
    async def test_output_candidates_are_compact(self, tmp_path: Path):
        """output.json candidates should be compact, not full 27-field."""
        config = RunnerConfig(data_dir=tmp_path)
        deps = StockOpportunityDeps(stock_engine_service=_StubEngine())
        result = await run_stock_opportunity(config, deps)

        output = json.loads(Path(result.artifact_path).read_text())
        for cand in output["candidates"]:
            # Must have card-friendly fields
            assert "symbol" in cand
            assert "scanner_key" in cand
            assert "scanner_name" in cand
            assert "setup_quality" in cand
            assert "review_summary" in cand
            assert "top_metrics" in cand
            assert "thesis_summary" in cand
            assert "supporting_signals" in cand
            assert "risk_flags" in cand
            # Must NOT have internal normalized fields
            assert "candidate_id" not in cand
            assert "strategy_family" not in cand
            assert "data_quality" not in cand
            assert "source_status" not in cand
            assert "pricing_snapshot" not in cand
            assert "candidate_metrics" not in cand

    @pytest.mark.asyncio
    async def test_no_run_id_in_candidates(self, tmp_path: Path):
        """run_id lives at output level, not in individual candidates."""
        config = RunnerConfig(data_dir=tmp_path)
        deps = StockOpportunityDeps(stock_engine_service=_StubEngine())
        result = await run_stock_opportunity(config, deps)

        output = json.loads(Path(result.artifact_path).read_text())
        assert "run_id" in output  # run_id at envelope level
        for cand in output["candidates"]:
            assert "run_id" not in cand


# ═══════════════════════════════════════════════════════════════════════
# 6. TMC read model passes compact candidates through
# ═══════════════════════════════════════════════════════════════════════

class TestTMCReadModelPassthrough:

    @pytest.mark.asyncio
    async def test_tmc_serves_compact_candidates(self, tmp_path: Path):
        """TMC read model should serve the same compact shape from output.json."""
        from app.workflows.tmc_service import load_latest_stock_output

        config = RunnerConfig(data_dir=tmp_path)
        deps = StockOpportunityDeps(stock_engine_service=_StubEngine())
        result = await run_stock_opportunity(config, deps)

        read_model = load_latest_stock_output(tmp_path)
        assert read_model is not None
        assert len(read_model.candidates) > 0

        for cand in read_model.candidates:
            # Card-friendly fields present
            assert "symbol" in cand
            assert "review_summary" in cand
            assert "top_metrics" in cand
            # No internal fields
            assert "candidate_id" not in cand
            assert "candidate_metrics" not in cand
