"""Prompt 12 — Stock Opportunity Workflow Rebuild tests.

Focused on the 7-stage runner redesign:
    Stage contract (7 stages, correct keys)
    Scanner coverage diagnostics
    Aggregate/normalize with dedup
    Richer market-context enrichment
    Deterministic filter/rank/select with stage counts
    Model analysis (with mock) + degraded fallback
    Compact output shape with model review fields
    Stage artifact creation for all 7 stages

STRICT: Only Prompt 12 tests.  No prior tests.  No broad regression.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.workflows.stock_opportunity_runner import (
    DEFAULT_TOP_N,
    MIN_SETUP_QUALITY,
    STAGE_KEYS,
    WORKFLOW_ID,
    RunnerConfig,
    RunResult,
    StockOpportunityDeps,
    _extract_compact_stock_candidate,
    _stage_aggregate_normalize,
    _stage_enrich_with_market_context,
    _stage_filter_rank_select,
    build_review_summary,
    run_stock_opportunity,
    select_top_metrics,
)


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def _make_raw_candidate(
    symbol: str = "AAPL",
    strategy_id: str = "stock_pullback_swing",
    composite_score: float = 72.0,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal raw scanner candidate dict."""
    base = {
        "symbol": symbol,
        "strategy_id": strategy_id,
        "composite_score": composite_score,
        "thesis": [f"{symbol} shows pullback setup"],
        "metrics": {
            "rsi": 42.0,
            "atr_pct": 2.5,
            "volume_ratio": 1.3,
        },
        "entry_price": 150.0,
        "trend_state": "uptrend",
        "stop_loss": 145.0,
        "target_price": 165.0,
        "signals": ["RSI oversold bounce", "Volume surge"],
        "risk_flags": ["Earnings next week"],
    }
    base.update(overrides)
    return base


def _make_scan_result(candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a mock StockEngineService.scan() return value."""
    if candidates is None:
        candidates = [
            _make_raw_candidate("AAPL", "stock_pullback_swing", 72.0),
            _make_raw_candidate("MSFT", "stock_momentum_breakout", 68.0),
            _make_raw_candidate("GOOG", "stock_mean_reversion", 55.0),
            _make_raw_candidate("TSLA", "stock_volatility_expansion", 40.0),
        ]
    return {
        "engine": "stock_engine",
        "status": "ok",
        "as_of": "2026-03-20T15:00:00+00:00",
        "top_n": 9,
        "total_candidates": len(candidates),
        "candidates": candidates,
        "scanners": [
            {"strategy_id": "stock_pullback_swing", "status": "ok", "candidates_count": 1, "max_composite_score": 72.0},
            {"strategy_id": "stock_momentum_breakout", "status": "ok", "candidates_count": 1, "max_composite_score": 68.0},
            {"strategy_id": "stock_mean_reversion", "status": "ok", "candidates_count": 1, "max_composite_score": 55.0},
            {"strategy_id": "stock_volatility_expansion", "status": "ok", "candidates_count": 1, "max_composite_score": 40.0},
        ],
        "warnings": [],
        "scan_time_seconds": 12.5,
    }


def _make_market_state_artifact(data_dir: Path) -> None:
    """Write a minimal market-state artifact so stage 1 loads successfully."""
    from app.workflows.definitions import WORKFLOW_VERSION
    from app.workflows.market_state_contract import MARKET_STATE_CONTRACT_VERSION

    ms_dir = data_dir / "workflows" / "market_state"
    ms_dir.mkdir(parents=True, exist_ok=True)

    artifact_id = "mi_run_20260320_150000_test"
    artifact = {
        "contract_version": MARKET_STATE_CONTRACT_VERSION,
        "artifact_id": artifact_id,
        "generated_at": "2026-03-20T15:00:00+00:00",
        "status": "valid",
        "composite": {
            "market_state": "bullish",
            "support_state": "strong",
            "stability_state": "stable",
            "confidence": 0.85,
        },
        "consumer_summary": {
            "market_state": "bullish",
            "support_state": "strong",
            "stability_state": "stable",
            "confidence": 0.85,
            "vix": 15.2,
            "regime_tags": ["low_vol", "risk_on"],
            "is_degraded": False,
            "summary_text": "Market conditions are favorable for long entries.",
        },
        "sections": {},
    }

    fname = f"market_state_{artifact_id}.json"
    (ms_dir / fname).write_text(json.dumps(artifact), encoding="utf-8")

    pointer = {
        "artifact_id": artifact_id,
        "artifact_filename": fname,
        "generated_at": "2026-03-20T15:00:00+00:00",
        "status": "valid",
    }
    (ms_dir / "latest.json").write_text(json.dumps(pointer), encoding="utf-8")


def _make_deps(
    scan_result: dict[str, Any] | None = None,
    model_request_fn: Any = None,
) -> StockOpportunityDeps:
    """Build StockOpportunityDeps with a mock engine service."""
    engine = AsyncMock()
    engine.scan = AsyncMock(return_value=scan_result or _make_scan_result())
    return StockOpportunityDeps(
        stock_engine_service=engine,
        model_request_fn=model_request_fn,
    )


# ══════════════════════════════════════════════════════════════════════
# STAGE CONTRACT TESTS
# ══════════════════════════════════════════════════════════════════════


class TestStageContract:
    """Verify the 7-stage contract is correct."""

    def test_stage_keys_are_seven(self):
        assert len(STAGE_KEYS) == 7

    def test_stage_key_names(self):
        expected = (
            "load_market_state",
            "run_stock_scanner_suite",
            "aggregate_normalize",
            "enrich_with_market_context",
            "filter_rank_select",
            "run_final_model_analysis",
            "package_publish_output",
        )
        assert STAGE_KEYS == expected

    def test_workflow_id(self):
        assert WORKFLOW_ID == "stock_opportunity"


# ══════════════════════════════════════════════════════════════════════
# SCANNER COVERAGE DIAGNOSTICS TESTS
# ══════════════════════════════════════════════════════════════════════


class TestScannerCoverage:
    """Verify scanner coverage diagnostics in stage 2."""

    @pytest.mark.asyncio
    async def test_scanner_coverage_in_stage_data(self, tmp_path):
        """Stage 2 should populate scanner_coverage diagnostics."""
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        _make_market_state_artifact(tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)

        assert result.status == "completed"
        # Find stage 2 in result stages
        stage2 = next(s for s in result.stages if s["stage_key"] == "run_stock_scanner_suite")
        assert stage2["status"] == "completed"

    @pytest.mark.asyncio
    async def test_scanner_coverage_artifact_written(self, tmp_path):
        """Stage artifact for run_stock_scanner_suite should contain coverage."""
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        _make_market_state_artifact(tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)

        # Read stage artifact
        from app.workflows.artifact_strategy import get_stage_artifact_path
        artifact_path = get_stage_artifact_path(
            tmp_path, WORKFLOW_ID, result.run_id, "run_stock_scanner_suite"
        )
        artifact = json.loads(artifact_path.read_text())
        assert "scanner_coverage" in artifact
        cov = artifact["scanner_coverage"]
        assert cov["scanners_attempted"] == 4
        assert cov["scanners_succeeded"] == 4
        assert cov["total_raw_candidates"] == 4

    @pytest.mark.asyncio
    async def test_partial_scanner_failure_coverage(self, tmp_path):
        """When a scanner fails, coverage diagnostics should reflect it."""
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        _make_market_state_artifact(tmp_path)

        scan_result = _make_scan_result()
        # Simulate one scanner failing
        scan_result["scanners"][2]["status"] = "error"
        scan_result["warnings"] = ["stock_mean_reversion: timeout"]
        deps = _make_deps(scan_result=scan_result)

        result = await run_stock_opportunity(config, deps)

        from app.workflows.artifact_strategy import get_stage_artifact_path
        artifact_path = get_stage_artifact_path(
            tmp_path, WORKFLOW_ID, result.run_id, "run_stock_scanner_suite"
        )
        artifact = json.loads(artifact_path.read_text())
        cov = artifact["scanner_coverage"]
        assert cov["scanners_succeeded"] == 3
        assert cov["scanners_failed"] == 1


# ══════════════════════════════════════════════════════════════════════
# AGGREGATE / NORMALIZE TESTS
# ══════════════════════════════════════════════════════════════════════


class TestAggregateNormalize:
    """Verify stage 3: aggregation, normalization, and dedup."""

    def test_dedup_keeps_highest_quality(self):
        """When same symbol appears twice, keep the higher setup_quality."""
        stage_data = {
            "raw_candidates": [
                _make_raw_candidate("AAPL", "stock_pullback_swing", 72.0),
                _make_raw_candidate("AAPL", "stock_momentum_breakout", 85.0),
                _make_raw_candidate("MSFT", "stock_mean_reversion", 60.0),
            ],
        }
        warnings: list[str] = []
        outcome = _stage_aggregate_normalize(stage_data, warnings)

        assert outcome.status == "completed"
        normalized = stage_data["normalized_candidates"]
        # Should have 2 symbols (AAPL deduped, MSFT kept)
        assert len(normalized) == 2
        symbols = {c["symbol"] for c in normalized}
        assert symbols == {"AAPL", "MSFT"}

        # AAPL should have the higher quality
        aapl = next(c for c in normalized if c["symbol"] == "AAPL")
        assert aapl["setup_quality"] >= 80  # from composite_score 85

    def test_aggregation_counts_tracked(self):
        """aggregation_counts should be populated in stage_data."""
        stage_data = {
            "raw_candidates": [
                _make_raw_candidate("AAPL", "stock_pullback_swing", 72.0),
                _make_raw_candidate("AAPL", "stock_momentum_breakout", 85.0),
                _make_raw_candidate("MSFT", "stock_mean_reversion", 60.0),
            ],
        }
        warnings: list[str] = []
        _stage_aggregate_normalize(stage_data, warnings)

        counts = stage_data["aggregation_counts"]
        assert counts["raw_input"] == 3
        assert counts["normalized"] == 3
        assert counts["dedup_removed"] == 1
        assert counts["after_dedup"] == 2

    def test_raw_candidates_by_key_preserved(self):
        """raw_candidates_by_key should be populated for model analysis."""
        stage_data = {
            "raw_candidates": [
                _make_raw_candidate("AAPL", "stock_pullback_swing", 72.0),
            ],
        }
        warnings: list[str] = []
        _stage_aggregate_normalize(stage_data, warnings)

        raw_by_key = stage_data["raw_candidates_by_key"]
        assert "AAPL:stock_pullback_swing" in raw_by_key

    def test_missing_strategy_id_skipped(self):
        """Candidates without strategy_id should be skipped."""
        stage_data = {
            "raw_candidates": [
                {"symbol": "AAPL"},  # no strategy_id
                _make_raw_candidate("MSFT", "stock_mean_reversion", 60.0),
            ],
        }
        warnings: list[str] = []
        outcome = _stage_aggregate_normalize(stage_data, warnings)

        assert outcome.status == "completed"
        assert len(stage_data["normalized_candidates"]) == 1
        assert stage_data["aggregation_counts"]["skipped"] == 1


# ══════════════════════════════════════════════════════════════════════
# MARKET CONTEXT ENRICHMENT TESTS
# ══════════════════════════════════════════════════════════════════════


class TestMarketContextEnrichment:
    """Verify stage 4: richer market-context enrichment."""

    def test_all_market_fields_attached(self):
        """Enrichment should attach VIX, regime_tags, support_state, etc."""
        stage_data = {
            "normalized_candidates": [
                {"symbol": "AAPL", "setup_quality": 72},
            ],
            "market_state_ref": "mi_run_test",
            "consumer_summary": {
                "market_state": "bullish",
                "stability_state": "stable",
                "vix": 15.2,
                "regime_tags": ["low_vol", "risk_on"],
                "support_state": "strong",
                "summary_text": "Favorable conditions.",
                "confidence": 0.85,
                "is_degraded": False,
            },
        }
        warnings: list[str] = []
        outcome = _stage_enrich_with_market_context(stage_data, warnings)

        assert outcome.status == "completed"
        enriched = stage_data["enriched_candidates"]
        assert len(enriched) == 1
        c = enriched[0]
        assert c["market_state_ref"] == "mi_run_test"
        assert c["market_regime"] == "bullish"
        assert c["risk_environment"] == "stable"
        assert c["vix"] == 15.2
        assert c["regime_tags"] == ["low_vol", "risk_on"]
        assert c["support_state"] == "strong"
        assert c["market_summary_text"] == "Favorable conditions."
        assert c["market_confidence"] == 0.85

    def test_degraded_market_state_warning(self):
        """When market state is degraded, a warning should be emitted."""
        stage_data = {
            "normalized_candidates": [{"symbol": "AAPL", "setup_quality": 72}],
            "market_state_ref": None,
            "consumer_summary": {"is_degraded": True},
        }
        warnings: list[str] = []
        _stage_enrich_with_market_context(stage_data, warnings)

        assert any("degraded" in w.lower() for w in warnings)


# ══════════════════════════════════════════════════════════════════════
# FILTER / RANK / SELECT TESTS
# ══════════════════════════════════════════════════════════════════════


class TestFilterRankSelect:
    """Verify stage 5: deterministic filtering, ranking, selection."""

    def test_below_quality_threshold_rejected(self):
        """Candidates below MIN_SETUP_QUALITY should be filtered out."""
        config = RunnerConfig(data_dir="/tmp", top_n=20)
        stage_data = {
            "enriched_candidates": [
                {"symbol": "AAPL", "setup_quality": 72},
                {"symbol": "MSFT", "setup_quality": 25},  # below threshold
                {"symbol": "GOOG", "setup_quality": 50},
            ],
        }
        warnings: list[str] = []
        outcome = _stage_filter_rank_select(config, stage_data, warnings)

        assert outcome.status == "completed"
        selected = stage_data["selected_candidates"]
        assert len(selected) == 2
        symbols = {c["symbol"] for c in selected}
        assert "MSFT" not in symbols

        counts = stage_data["filter_counts"]
        assert counts["rejected"] == 1
        assert counts["rejected_reasons"]["below_quality_threshold"] == 1

    def test_ranking_by_setup_quality_desc(self):
        """Selected candidates should be ranked by setup_quality DESC."""
        config = RunnerConfig(data_dir="/tmp", top_n=20)
        stage_data = {
            "enriched_candidates": [
                {"symbol": "GOOG", "setup_quality": 50},
                {"symbol": "AAPL", "setup_quality": 72},
                {"symbol": "TSLA", "setup_quality": 65},
            ],
        }
        warnings: list[str] = []
        _stage_filter_rank_select(config, stage_data, warnings)

        selected = stage_data["selected_candidates"]
        assert selected[0]["symbol"] == "AAPL"
        assert selected[0]["rank"] == 1
        assert selected[1]["symbol"] == "TSLA"
        assert selected[1]["rank"] == 2
        assert selected[2]["symbol"] == "GOOG"
        assert selected[2]["rank"] == 3

    def test_top_n_cap_applied(self):
        """Selection should respect the top_n cap."""
        config = RunnerConfig(data_dir="/tmp", top_n=2)
        stage_data = {
            "enriched_candidates": [
                {"symbol": "AAPL", "setup_quality": 72},
                {"symbol": "MSFT", "setup_quality": 68},
                {"symbol": "GOOG", "setup_quality": 50},
            ],
        }
        warnings: list[str] = []
        _stage_filter_rank_select(config, stage_data, warnings)

        assert len(stage_data["selected_candidates"]) == 2
        assert stage_data["filter_counts"]["selected"] == 2
        assert stage_data["filter_counts"]["top_n_cap"] == 2

    def test_filter_counts_structure(self):
        """filter_counts should have all expected keys."""
        config = RunnerConfig(data_dir="/tmp", top_n=20)
        stage_data = {
            "enriched_candidates": [
                {"symbol": "AAPL", "setup_quality": 72},
            ],
        }
        warnings: list[str] = []
        _stage_filter_rank_select(config, stage_data, warnings)

        counts = stage_data["filter_counts"]
        assert "enriched_input" in counts
        assert "rejected" in counts
        assert "rejected_reasons" in counts
        assert "passed" in counts
        assert "selected" in counts
        assert "top_n_cap" in counts


# ══════════════════════════════════════════════════════════════════════
# MODEL ANALYSIS TESTS
# ══════════════════════════════════════════════════════════════════════


class TestModelAnalysis:
    """Verify stage 6: model analysis execution and degradation."""

    @pytest.mark.asyncio
    async def test_model_analysis_degraded_when_no_fn(self):
        """When model_request_fn is None, stage should degrade gracefully."""
        deps = StockOpportunityDeps(stock_engine_service=AsyncMock(), model_request_fn=None)
        stage_data = {
            "selected_candidates": [
                {"symbol": "AAPL", "scanner_key": "stock_pullback_swing", "setup_quality": 72},
            ],
            "raw_candidates_by_key": {},
        }
        warnings: list[str] = []

        from app.workflows.stock_opportunity_runner import _stage_run_final_model_analysis
        outcome = await _stage_run_final_model_analysis(deps, stage_data, warnings)

        assert outcome.status == "degraded"
        assert stage_data["model_analysis_counts"]["attempted"] == 0
        assert stage_data["selected_candidates"][0]["model_review"] is None

    @pytest.mark.asyncio
    async def test_model_analysis_degraded_when_no_fn_full_run(self, tmp_path):
        """Full run without model_request_fn should complete with degraded model stage."""
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        _make_market_state_artifact(tmp_path)
        deps = _make_deps(model_request_fn=None)

        result = await run_stock_opportunity(config, deps)

        assert result.status == "completed"
        model_stage = next(s for s in result.stages if s["stage_key"] == "run_final_model_analysis")
        assert model_stage["status"] == "degraded"


# ══════════════════════════════════════════════════════════════════════
# COMPACT OUTPUT SHAPE TESTS
# ══════════════════════════════════════════════════════════════════════


class TestCompactOutputShape:
    """Verify compact candidate shape includes model review fields."""

    def test_compact_includes_model_review_fields(self):
        """Compact shape should include model_recommendation, model_confidence, etc."""
        cand = {
            "symbol": "AAPL",
            "scanner_key": "stock_pullback_swing",
            "scanner_name": "Pullback Swing",
            "setup_type": "pullback_swing",
            "direction": "long",
            "setup_quality": 72,
            "confidence": 0.8,
            "rank": 1,
            "thesis_summary": ["Pullback in uptrend"],
            "supporting_signals": ["RSI bounce"],
            "risk_flags": ["Earnings"],
            "entry_context": {"price": 150.0, "state": "uptrend"},
            "candidate_metrics": {"composite_score": 72, "rsi": 42},
            "market_state_ref": "test_ref",
            "market_regime": "bullish",
            "risk_environment": "stable",
            "vix": 15.2,
            "regime_tags": ["low_vol"],
            "support_state": "strong",
            # Model review
            "model_recommendation": "BUY",
            "model_confidence": 85,
            "model_score": 78,
            "model_review_summary": "Strong pullback setup.",
            "model_key_factors": [{"factor": "RSI", "impact": "positive", "evidence": "oversold"}],
            "model_caution_notes": ["Earnings risk"],
        }

        compact = _extract_compact_stock_candidate(cand)

        # Deterministic fields
        assert compact["symbol"] == "AAPL"
        assert compact["setup_quality"] == 72
        assert compact["market_regime"] == "bullish"
        assert compact["vix"] == 15.2
        assert compact["regime_tags"] == ["low_vol"]
        assert compact["support_state"] == "strong"

        # Model review fields
        assert compact["model_recommendation"] == "BUY"
        assert compact["model_confidence"] == 85
        assert compact["model_score"] == 78
        assert compact["model_review_summary"] == "Strong pullback setup."
        assert compact["model_key_factors"] == [{"factor": "RSI", "impact": "positive", "evidence": "oversold"}]
        assert compact["model_caution_notes"] == ["Earnings risk"]

        # Derived
        assert "top_metrics" in compact
        assert "review_summary" in compact

    def test_compact_model_fields_none_when_absent(self):
        """When model analysis was skipped, model fields should be None."""
        cand = {
            "symbol": "AAPL",
            "scanner_key": "stock_pullback_swing",
            "scanner_name": "Pullback Swing",
            "setup_type": "pullback_swing",
            "direction": "long",
            "setup_quality": 72,
            "confidence": 0.8,
            "rank": 1,
            "entry_context": {"price": 150.0, "state": "uptrend"},
            "candidate_metrics": {},
        }

        compact = _extract_compact_stock_candidate(cand)

        assert compact["model_recommendation"] is None
        assert compact["model_confidence"] is None
        assert compact["model_score"] is None
        assert compact["model_review_summary"] is None


# ══════════════════════════════════════════════════════════════════════
# FULL RUN INTEGRATION TESTS
# ══════════════════════════════════════════════════════════════════════


class TestFullRun:
    """Integration tests for the complete 7-stage run."""

    @pytest.mark.asyncio
    async def test_happy_path_7_stages(self, tmp_path):
        """Full run should produce 7 stages, all completed (model degraded OK)."""
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        _make_market_state_artifact(tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)

        assert result.status == "completed"
        assert len(result.stages) == 7
        stage_keys = [s["stage_key"] for s in result.stages]
        assert stage_keys == list(STAGE_KEYS)

    @pytest.mark.asyncio
    async def test_output_json_has_coverage_and_filter_counts(self, tmp_path):
        """output.json should contain scanner_coverage and filter_counts."""
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        _make_market_state_artifact(tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)

        from app.workflows.artifact_strategy import get_output_path
        output_path = get_output_path(tmp_path, WORKFLOW_ID, result.run_id)
        output = json.loads(output_path.read_text())

        assert "scanner_coverage" in output
        assert "filter_counts" in output
        assert "model_analysis_counts" in output
        assert output["scanner_coverage"]["scanners_attempted"] == 4

    @pytest.mark.asyncio
    async def test_output_candidates_compact_shape(self, tmp_path):
        """Output candidates should have the extended compact shape."""
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        _make_market_state_artifact(tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)

        from app.workflows.artifact_strategy import get_output_path
        output_path = get_output_path(tmp_path, WORKFLOW_ID, result.run_id)
        output = json.loads(output_path.read_text())

        candidates = output["candidates"]
        assert len(candidates) > 0

        c = candidates[0]
        # Deterministic fields
        assert "symbol" in c
        assert "setup_quality" in c
        assert "rank" in c
        assert "top_metrics" in c
        assert "review_summary" in c
        assert "market_regime" in c
        assert "vix" in c
        assert "regime_tags" in c
        # Model fields (None since no model_request_fn)
        assert "model_recommendation" in c
        assert "model_confidence" in c

    @pytest.mark.asyncio
    async def test_all_7_stage_artifacts_written(self, tmp_path):
        """All 7 stage artifacts should be written to disk."""
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        _make_market_state_artifact(tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)

        from app.workflows.artifact_strategy import get_stage_artifact_path
        for stage_key in STAGE_KEYS:
            path = get_stage_artifact_path(tmp_path, WORKFLOW_ID, result.run_id, stage_key)
            assert path.exists(), f"Missing stage artifact: {stage_key}"
            data = json.loads(path.read_text())
            assert data["stage_key"] == stage_key

    @pytest.mark.asyncio
    async def test_summary_json_has_diagnostics(self, tmp_path):
        """summary.json should include scanner_coverage and filter_counts."""
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        _make_market_state_artifact(tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)

        from app.workflows.artifact_strategy import get_summary_path
        summary_path = get_summary_path(tmp_path, WORKFLOW_ID, result.run_id)
        summary = json.loads(summary_path.read_text())

        assert "scanner_coverage" in summary
        assert "filter_counts" in summary
        assert "model_analysis_counts" in summary
        assert len(summary["stages"]) == 7

    @pytest.mark.asyncio
    async def test_low_quality_candidates_filtered(self, tmp_path):
        """Candidates below MIN_SETUP_QUALITY should not appear in output."""
        low_score_candidates = [
            _make_raw_candidate("AAPL", "stock_pullback_swing", 72.0),
            _make_raw_candidate("WEAK", "stock_mean_reversion", 10.0),  # very low
        ]
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        _make_market_state_artifact(tmp_path)
        deps = _make_deps(scan_result=_make_scan_result(low_score_candidates))

        result = await run_stock_opportunity(config, deps)

        from app.workflows.artifact_strategy import get_output_path
        output_path = get_output_path(tmp_path, WORKFLOW_ID, result.run_id)
        output = json.loads(output_path.read_text())

        symbols = [c["symbol"] for c in output["candidates"]]
        assert "WEAK" not in symbols

    @pytest.mark.asyncio
    async def test_scan_failure_aborts_run(self, tmp_path):
        """If scan stage fails, the run should fail."""
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        _make_market_state_artifact(tmp_path)

        engine = AsyncMock()
        engine.scan = AsyncMock(side_effect=RuntimeError("Tradier API down"))
        deps = StockOpportunityDeps(stock_engine_service=engine)

        result = await run_stock_opportunity(config, deps)

        assert result.status == "failed"
        assert "run_stock_scanner_suite" in result.error

    @pytest.mark.asyncio
    async def test_deps_model_request_fn_default_none(self):
        """StockOpportunityDeps should default model_request_fn to None."""
        deps = StockOpportunityDeps(stock_engine_service=AsyncMock())
        assert deps.model_request_fn is None
