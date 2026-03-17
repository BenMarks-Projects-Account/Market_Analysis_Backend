"""Prompt 12B — Full Market Picture Enrichment tests.

Focused on the 8-stage runner redesign:
    Stage contract (8 stages, correct keys)
    Explicit scanner suite resolution (stage 2)
    Aggregate/dedup with multi-scanner provenance (source_scanners)
    Merged enrich+filter+rank+select (stage 5)
    Full Market Picture enrichment — 6 engine modules (stage 6)
    Model analysis with Market Picture in prompt (stage 7)
    Compact output proves workflow depth (source_scanners, market_picture_summary)
    Stage artifacts for all 8 stages

STRICT: Only Prompt 12B tests.  No prior tests.  No broad regression.
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
    MARKET_PICTURE_ENGINE_KEYS,
    MIN_SETUP_QUALITY,
    STAGE_KEYS,
    STOCK_SCANNER_KEYS,
    WORKFLOW_ID,
    RunnerConfig,
    RunResult,
    StockOpportunityDeps,
    _build_market_picture_context,
    _build_market_picture_summary,
    _extract_compact_stock_candidate,
    _stage_aggregate_dedup_candidates,
    _stage_append_market_picture_context,
    _stage_enrich_filter_rank_select,
    _stage_resolve_stock_scanner_suite,
    build_review_summary,
    run_stock_opportunity,
    select_top_metrics,
)


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════


def _make_engine(
    engine_key: str,
    score: float = 65.0,
    label: str = "Moderate",
    summary: str = "Engine summary text.",
) -> dict[str, Any]:
    """Build a minimal MI engine dict matching the 25-field contract."""
    return {
        "engine_key": engine_key,
        "engine_name": engine_key.replace("_", " ").title(),
        "score": score,
        "label": label,
        "short_label": label[:3],
        "confidence": 0.75,
        "summary": summary,
        "trader_takeaway": f"Takeaway for {engine_key}",
        "bull_factors": [f"{engine_key} bull factor"],
        "bear_factors": [f"{engine_key} bear factor"],
        "risks": [f"{engine_key} risk"],
        "regime_tags": ["neutral"],
        "supporting_metrics": {},
        "pillar_scores": {},
        "detail_sections": [],
        "engine_status": "ok",
    }


def _make_engines_dict() -> dict[str, Any]:
    """Build a full 6-engine MI engines dict."""
    return {k: _make_engine(k) for k in MARKET_PICTURE_ENGINE_KEYS}


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
        "thesis": [f"{symbol} shows {strategy_id.replace('stock_', '')} setup"],
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


def _make_market_state_artifact(data_dir: Path, engines: dict[str, Any] | None = None) -> None:
    """Write a market-state artifact with full engines for Market Picture tests."""
    from app.workflows.definitions import WORKFLOW_VERSION
    from app.workflows.market_state_contract import MARKET_STATE_CONTRACT_VERSION

    ms_dir = data_dir / "market_state"
    ms_dir.mkdir(parents=True, exist_ok=True)

    artifact_id = "mi_run_20260320_150000_test"
    if engines is None:
        engines = _make_engines_dict()

    artifact = {
        "contract_version": MARKET_STATE_CONTRACT_VERSION,
        "artifact_id": artifact_id,
        "workflow_id": "market_state",
        "generated_at": "2026-03-20T15:00:00+00:00",
        "status": "valid",
        "publication": {"status": "valid"},
        "freshness": {"generated_at": "2026-03-20T15:00:00+00:00"},
        "quality": {"level": "good"},
        "market_snapshot": {},
        "composite": {
            "market_state": "bullish",
            "support_state": "strong",
            "stability_state": "stable",
            "confidence": 0.85,
        },
        "conflicts": None,
        "model_interpretation": None,
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
        "engines": engines,
        "lineage": {},
        "warnings": [],
        "sections": {},
    }

    fname = f"market_state_{artifact_id}.json"
    (ms_dir / fname).write_text(json.dumps(artifact), encoding="utf-8")

    pointer = {
        "artifact_id": artifact_id,
        "artifact_filename": fname,
        "published_at": "2026-03-20T15:00:00+00:00",
        "status": "valid",
        "contract_version": MARKET_STATE_CONTRACT_VERSION,
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


class TestStageContract12B:
    """Verify the 8-stage contract is correct."""

    def test_stage_keys_are_eight(self):
        assert len(STAGE_KEYS) == 8

    def test_stage_key_names(self):
        expected = (
            "load_market_state",
            "resolve_stock_scanner_suite",
            "run_stock_scanner_suite",
            "aggregate_dedup_candidates",
            "enrich_filter_rank_select",
            "append_market_picture_context",
            "run_final_model_analysis",
            "package_publish_output",
        )
        assert STAGE_KEYS == expected

    def test_workflow_id(self):
        assert WORKFLOW_ID == "stock_opportunity"

    def test_market_picture_engine_keys(self):
        expected = (
            "breadth_participation",
            "volatility_options",
            "cross_asset_macro",
            "flows_positioning",
            "liquidity_financial_conditions",
            "news_sentiment",
        )
        assert MARKET_PICTURE_ENGINE_KEYS == expected

    def test_stock_scanner_keys(self):
        assert len(STOCK_SCANNER_KEYS) == 4


# ══════════════════════════════════════════════════════════════════════
# SCANNER SUITE RESOLUTION TESTS
# ══════════════════════════════════════════════════════════════════════


class TestScannerSuiteResolution:
    """Verify stage 2: resolve_stock_scanner_suite."""

    def test_scanner_suite_resolves_all_four(self):
        stage_data: dict[str, Any] = {}
        warnings: list[str] = []
        outcome = _stage_resolve_stock_scanner_suite(stage_data, warnings)

        assert outcome.status == "completed"
        assert outcome.stage_key == "resolve_stock_scanner_suite"

        suite = stage_data["scanner_suite"]
        assert suite["scanner_count"] == 4
        assert len(suite["configured"]) == 4
        assert len(suite["available"]) == 4
        assert len(suite["unavailable"]) == 0

    def test_scanner_suite_contains_all_keys(self):
        stage_data: dict[str, Any] = {}
        warnings: list[str] = []
        _stage_resolve_stock_scanner_suite(stage_data, warnings)

        suite = stage_data["scanner_suite"]
        for key in STOCK_SCANNER_KEYS:
            assert key in suite["available"]


# ══════════════════════════════════════════════════════════════════════
# AGGREGATE DEDUP WITH MULTI-SCANNER PROVENANCE TESTS
# ══════════════════════════════════════════════════════════════════════


class TestAggregateDedupProvenance:
    """Verify stage 4: aggregate_dedup_candidates with source_scanners."""

    def test_source_scanners_single_scanner(self):
        """Each symbol from one scanner should have source_scanners = [that_scanner]."""
        stage_data: dict[str, Any] = {
            "raw_candidates": [
                _make_raw_candidate("AAPL", "stock_pullback_swing", 72.0),
                _make_raw_candidate("MSFT", "stock_momentum_breakout", 68.0),
            ],
        }
        warnings: list[str] = []
        outcome = _stage_aggregate_dedup_candidates(stage_data, warnings)

        assert outcome.status == "completed"
        candidates = stage_data["normalized_candidates"]
        assert len(candidates) == 2

        for cand in candidates:
            assert "source_scanners" in cand
            assert len(cand["source_scanners"]) == 1

    def test_source_scanners_multi_scanner_provenance(self):
        """Same symbol from 2 scanners should merge into source_scanners."""
        stage_data: dict[str, Any] = {
            "raw_candidates": [
                _make_raw_candidate("AAPL", "stock_pullback_swing", 65.0),
                _make_raw_candidate("AAPL", "stock_momentum_breakout", 72.0),
                _make_raw_candidate("MSFT", "stock_mean_reversion", 55.0),
            ],
        }
        warnings: list[str] = []
        outcome = _stage_aggregate_dedup_candidates(stage_data, warnings)

        assert outcome.status == "completed"
        candidates = stage_data["normalized_candidates"]
        assert len(candidates) == 2  # AAPL deduped

        aapl = next(c for c in candidates if c["symbol"] == "AAPL")
        assert len(aapl["source_scanners"]) == 2
        assert "stock_pullback_swing" in aapl["source_scanners"]
        assert "stock_momentum_breakout" in aapl["source_scanners"]
        # Kept the higher quality one.
        assert aapl["scanner_key"] == "stock_momentum_breakout"

    def test_multi_scanner_count_in_aggregation(self):
        """Aggregation counts should track multi_scanner_symbols."""
        stage_data: dict[str, Any] = {
            "raw_candidates": [
                _make_raw_candidate("AAPL", "stock_pullback_swing", 65.0),
                _make_raw_candidate("AAPL", "stock_momentum_breakout", 72.0),
            ],
        }
        warnings: list[str] = []
        _stage_aggregate_dedup_candidates(stage_data, warnings)

        counts = stage_data["aggregation_counts"]
        assert counts["multi_scanner_symbols"] == 1
        assert counts["dedup_removed"] == 1

    def test_dedup_keeps_highest_quality(self):
        """Dedup should keep the candidate with highest setup_quality."""
        stage_data: dict[str, Any] = {
            "raw_candidates": [
                _make_raw_candidate("AAPL", "stock_pullback_swing", 60.0),
                _make_raw_candidate("AAPL", "stock_momentum_breakout", 80.0),
                _make_raw_candidate("AAPL", "stock_mean_reversion", 70.0),
            ],
        }
        warnings: list[str] = []
        _stage_aggregate_dedup_candidates(stage_data, warnings)

        candidates = stage_data["normalized_candidates"]
        assert len(candidates) == 1
        assert candidates[0]["scanner_key"] == "stock_momentum_breakout"
        assert len(candidates[0]["source_scanners"]) == 3


# ══════════════════════════════════════════════════════════════════════
# MARKET PICTURE HELPERS TESTS
# ══════════════════════════════════════════════════════════════════════


class TestMarketPictureHelpers:
    """Verify _build_market_picture_context and _build_market_picture_summary."""

    def test_build_context_all_six_engines(self):
        """Context should have all 6 engines when all are available."""
        engines = _make_engines_dict()
        ctx = _build_market_picture_context(engines)
        assert len(ctx) == 6
        for key in MARKET_PICTURE_ENGINE_KEYS:
            assert key in ctx

    def test_build_context_engine_fields(self):
        """Each engine in context should have the expected compact fields."""
        engines = _make_engines_dict()
        ctx = _build_market_picture_context(engines)
        for key in MARKET_PICTURE_ENGINE_KEYS:
            eng = ctx[key]
            assert "score" in eng
            assert "label" in eng
            assert "confidence" in eng
            assert "summary" in eng
            assert "trader_takeaway" in eng
            assert "bull_factors" in eng
            assert "bear_factors" in eng
            assert "risks" in eng
            assert "engine_status" in eng

    def test_build_context_partial_engines(self):
        """Context should handle missing engines gracefully."""
        engines = {
            "breadth_participation": _make_engine("breadth_participation"),
            "news_sentiment": _make_engine("news_sentiment"),
        }
        ctx = _build_market_picture_context(engines)
        assert len(ctx) == 2
        assert "breadth_participation" in ctx
        assert "news_sentiment" in ctx

    def test_build_context_empty_engines(self):
        """Context should be empty dict when no engines available."""
        ctx = _build_market_picture_context({})
        assert ctx == {}

    def test_build_summary_all_engines(self):
        """Summary should report engines_available and engine_summaries."""
        engines = _make_engines_dict()
        ctx = _build_market_picture_context(engines)
        summary = _build_market_picture_summary(ctx)

        assert summary["engines_available"] == 6
        assert summary["engines_total"] == 6
        assert len(summary["engine_summaries"]) == 6

        for key in MARKET_PICTURE_ENGINE_KEYS:
            eng_sum = summary["engine_summaries"][key]
            assert "score" in eng_sum
            assert "label" in eng_sum
            assert "summary" in eng_sum

    def test_build_summary_partial(self):
        """Summary should accurately reflect partial engine availability."""
        engines = {"breadth_participation": _make_engine("breadth_participation")}
        ctx = _build_market_picture_context(engines)
        summary = _build_market_picture_summary(ctx)
        assert summary["engines_available"] == 1
        assert summary["engines_total"] == 6


# ══════════════════════════════════════════════════════════════════════
# MARKET PICTURE STAGE TESTS
# ══════════════════════════════════════════════════════════════════════


class TestMarketPictureStage:
    """Verify stage 6: append_market_picture_context."""

    def test_appends_context_to_candidates(self):
        """Selected candidates should get market_picture_context and summary."""
        stage_data: dict[str, Any] = {
            "selected_candidates": [
                {"symbol": "AAPL", "scanner_key": "stock_pullback_swing"},
                {"symbol": "MSFT", "scanner_key": "stock_momentum_breakout"},
            ],
            "market_engines": _make_engines_dict(),
        }
        warnings: list[str] = []
        outcome = _stage_append_market_picture_context(stage_data, warnings)

        assert outcome.status == "completed"
        assert outcome.stage_key == "append_market_picture_context"

        for cand in stage_data["selected_candidates"]:
            assert "market_picture_context" in cand
            assert len(cand["market_picture_context"]) == 6
            assert "market_picture_summary" in cand
            assert cand["market_picture_summary"]["engines_available"] == 6

    def test_degrades_when_no_engines(self):
        """Stage should degrade when no MI engines are available."""
        stage_data: dict[str, Any] = {
            "selected_candidates": [{"symbol": "AAPL"}],
            "market_engines": {},
        }
        warnings: list[str] = []
        outcome = _stage_append_market_picture_context(stage_data, warnings)

        assert outcome.status == "degraded"
        assert any("No MI engines" in w for w in warnings)

    def test_stores_summary_in_stage_data(self):
        """market_picture_summary should be stored in stage_data for package stage."""
        stage_data: dict[str, Any] = {
            "selected_candidates": [{"symbol": "AAPL"}],
            "market_engines": _make_engines_dict(),
        }
        warnings: list[str] = []
        _stage_append_market_picture_context(stage_data, warnings)

        assert "market_picture_summary" in stage_data
        assert stage_data["market_picture_summary"]["engines_available"] == 6


# ══════════════════════════════════════════════════════════════════════
# COMPACT OUTPUT TESTS
# ══════════════════════════════════════════════════════════════════════


class TestCompactOutput12B:
    """Verify compact output includes source_scanners and market_picture_summary."""

    def test_compact_has_source_scanners(self):
        """Compact output must include source_scanners field."""
        cand = {
            "symbol": "AAPL",
            "scanner_key": "stock_pullback_swing",
            "source_scanners": ["stock_pullback_swing", "stock_momentum_breakout"],
            "setup_quality": 72.0,
            "entry_context": {"price": 150.0, "state": "uptrend"},
        }
        compact = _extract_compact_stock_candidate(cand)
        assert compact["source_scanners"] == ["stock_pullback_swing", "stock_momentum_breakout"]

    def test_compact_source_scanners_fallback(self):
        """When source_scanners is missing, fallback to [scanner_key]."""
        cand = {
            "symbol": "AAPL",
            "scanner_key": "stock_pullback_swing",
            "setup_quality": 72.0,
            "entry_context": {"price": 150.0, "state": "uptrend"},
        }
        compact = _extract_compact_stock_candidate(cand)
        assert compact["source_scanners"] == ["stock_pullback_swing"]

    def test_compact_has_market_picture_summary(self):
        """Compact output must include market_picture_summary."""
        engines = _make_engines_dict()
        ctx = _build_market_picture_context(engines)
        summary = _build_market_picture_summary(ctx)

        cand = {
            "symbol": "AAPL",
            "scanner_key": "stock_pullback_swing",
            "source_scanners": ["stock_pullback_swing"],
            "market_picture_summary": summary,
            "setup_quality": 72.0,
            "entry_context": {"price": 150.0, "state": "uptrend"},
        }
        compact = _extract_compact_stock_candidate(cand)
        assert compact["market_picture_summary"] is not None
        assert compact["market_picture_summary"]["engines_available"] == 6


# ══════════════════════════════════════════════════════════════════════
# PROMPT AUGMENTATION TESTS
# ══════════════════════════════════════════════════════════════════════


class TestPromptAugmentation:
    """Verify Market Picture context is injected into model prompts."""

    def test_prompt_includes_market_picture(self):
        """When candidate has market_picture_context, prompt should include it."""
        from common.stock_strategy_prompts import build_stock_strategy_user_prompt

        candidate = _make_raw_candidate("AAPL", "stock_pullback_swing", 72.0)
        engines = _make_engines_dict()
        candidate["market_picture_context"] = _build_market_picture_context(engines)

        prompt_str = build_stock_strategy_user_prompt("stock_pullback_swing", candidate)
        prompt_dict = json.loads(prompt_str)

        assert "market_picture" in prompt_dict
        assert "engines" in prompt_dict["market_picture"]
        assert len(prompt_dict["market_picture"]["engines"]) == 6

    def test_prompt_without_market_picture(self):
        """Without market_picture_context, prompt should not have market_picture."""
        from common.stock_strategy_prompts import build_stock_strategy_user_prompt

        candidate = _make_raw_candidate("AAPL", "stock_pullback_swing", 72.0)
        prompt_str = build_stock_strategy_user_prompt("stock_pullback_swing", candidate)
        prompt_dict = json.loads(prompt_str)
        assert "market_picture" not in prompt_dict

    def test_prompt_augmented_for_all_strategies(self):
        """Market picture augmentation should work for all 4 strategy types."""
        from common.stock_strategy_prompts import build_stock_strategy_user_prompt

        engines = _make_engines_dict()
        mpc = _build_market_picture_context(engines)

        for strategy_id in STOCK_SCANNER_KEYS:
            candidate = _make_raw_candidate("AAPL", strategy_id, 72.0)
            candidate["market_picture_context"] = mpc
            prompt_str = build_stock_strategy_user_prompt(strategy_id, candidate)
            prompt_dict = json.loads(prompt_str)
            assert "market_picture" in prompt_dict, f"Missing for {strategy_id}"


# ══════════════════════════════════════════════════════════════════════
# ENRICH + FILTER + RANK + SELECT TESTS
# ══════════════════════════════════════════════════════════════════════


class TestEnrichFilterRankSelect:
    """Verify merged stage 5: enrich_filter_rank_select."""

    def test_enriches_and_filters(self):
        """Stage 5 should enrich with market context AND filter/rank/select."""
        stage_data: dict[str, Any] = {
            "normalized_candidates": [
                {"symbol": "AAPL", "setup_quality": 72.0, "scanner_key": "stock_pullback_swing"},
                {"symbol": "TSLA", "setup_quality": 20.0, "scanner_key": "stock_volatility_expansion"},
            ],
            "market_state_ref": "mi_run_test",
            "consumer_summary": {
                "market_state": "bullish",
                "stability_state": "stable",
                "vix": 15.0,
                "regime_tags": ["risk_on"],
                "support_state": "strong",
                "summary_text": "Favorable conditions.",
                "confidence": 0.85,
                "is_degraded": False,
            },
        }
        warnings: list[str] = []
        config = RunnerConfig(data_dir="/tmp", top_n=10)
        outcome = _stage_enrich_filter_rank_select(config, stage_data, warnings)

        assert outcome.status == "completed"
        assert outcome.stage_key == "enrich_filter_rank_select"

        # TSLA rejected (setup_quality 20 < 30 threshold).
        selected = stage_data["selected_candidates"]
        assert len(selected) == 1
        assert selected[0]["symbol"] == "AAPL"

        # Enrichment fields present.
        assert selected[0]["market_regime"] == "bullish"
        assert selected[0]["vix"] == 15.0
        assert selected[0]["regime_tags"] == ["risk_on"]

    def test_filter_counts_in_stage_data(self):
        """Filter counts should track enriched_input, rejected, passed, selected."""
        stage_data: dict[str, Any] = {
            "normalized_candidates": [
                {"symbol": "AAPL", "setup_quality": 72.0},
                {"symbol": "TSLA", "setup_quality": 20.0},
            ],
            "market_state_ref": None,
            "consumer_summary": {},
        }
        warnings: list[str] = []
        config = RunnerConfig(data_dir="/tmp", top_n=10)
        _stage_enrich_filter_rank_select(config, stage_data, warnings)

        fc = stage_data["filter_counts"]
        assert fc["enriched_input"] == 2
        assert fc["rejected"] == 1
        assert fc["passed"] == 1
        assert fc["selected"] == 1


# ══════════════════════════════════════════════════════════════════════
# FULL PIPELINE E2E TESTS
# ══════════════════════════════════════════════════════════════════════


class TestFullPipeline12B:
    """End-to-end pipeline tests for the 8-stage runner."""

    @pytest.mark.asyncio
    async def test_full_run_produces_8_stages(self, tmp_path):
        """Complete run should produce exactly 8 stage outcomes."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        assert result.status == "completed"
        assert len(result.stages) == 8

    @pytest.mark.asyncio
    async def test_stage_keys_in_order(self, tmp_path):
        """Stage outcomes should match STAGE_KEYS ordering."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        stage_keys = [s["stage_key"] for s in result.stages]
        assert tuple(stage_keys) == STAGE_KEYS

    @pytest.mark.asyncio
    async def test_output_has_scanner_suite(self, tmp_path):
        """output.json should include scanner_suite from stage 2."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        output_path = Path(result.artifact_path)
        assert output_path.exists()
        output = json.loads(output_path.read_text())
        assert "scanner_suite" in output
        assert output["scanner_suite"]["scanner_count"] == 4

    @pytest.mark.asyncio
    async def test_output_has_market_picture_summary(self, tmp_path):
        """output.json should include market_picture_summary."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        output_path = Path(result.artifact_path)
        output = json.loads(output_path.read_text())
        assert "market_picture_summary" in output
        mps = output["market_picture_summary"]
        assert mps["engines_available"] == 6
        assert mps["engines_total"] == 6

    @pytest.mark.asyncio
    async def test_candidates_have_source_scanners(self, tmp_path):
        """Each candidate in output should have source_scanners list."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        output_path = Path(result.artifact_path)
        output = json.loads(output_path.read_text())
        for cand in output["candidates"]:
            assert "source_scanners" in cand
            assert isinstance(cand["source_scanners"], list)
            assert len(cand["source_scanners"]) >= 1

    @pytest.mark.asyncio
    async def test_candidates_have_market_picture_summary(self, tmp_path):
        """Each candidate in output should have market_picture_summary."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        output_path = Path(result.artifact_path)
        output = json.loads(output_path.read_text())
        for cand in output["candidates"]:
            assert "market_picture_summary" in cand
            if cand["market_picture_summary"] is not None:
                assert cand["market_picture_summary"]["engines_available"] == 6

    @pytest.mark.asyncio
    async def test_stage_artifacts_written_for_all_8(self, tmp_path):
        """All 8 stage artifacts should be written to disk."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        for stage_key in STAGE_KEYS:
            artifact_path = run_dir / f"stage_{stage_key}.json"
            assert artifact_path.exists(), f"Missing stage artifact: {stage_key}"

    @pytest.mark.asyncio
    async def test_resolve_stage_artifact_has_suite(self, tmp_path):
        """stage_resolve_stock_scanner_suite.json should contain suite details."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        artifact = json.loads((run_dir / "stage_resolve_stock_scanner_suite.json").read_text())
        assert artifact["stage_key"] == "resolve_stock_scanner_suite"
        assert "scanner_suite" in artifact
        assert artifact["scanner_suite"]["scanner_count"] == 4

    @pytest.mark.asyncio
    async def test_market_picture_stage_artifact(self, tmp_path):
        """stage_append_market_picture_context.json should contain summary."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        artifact = json.loads((run_dir / "stage_append_market_picture_context.json").read_text())
        assert artifact["stage_key"] == "append_market_picture_context"
        assert "market_picture_summary" in artifact

    @pytest.mark.asyncio
    async def test_load_market_state_extracts_engines(self, tmp_path):
        """Stage 1 artifact should record market engines availability."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        artifact = json.loads((run_dir / "stage_load_market_state.json").read_text())
        assert artifact["market_engines_available"] == 6
        assert len(artifact["market_engine_keys"]) == 6

    @pytest.mark.asyncio
    async def test_model_analysis_degraded_without_fn(self, tmp_path):
        """Model analysis stage should degrade when no model_request_fn configured."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps(model_request_fn=None)
        result = await run_stock_opportunity(config, deps)

        assert result.status == "completed"
        model_stage = next(s for s in result.stages if s["stage_key"] == "run_final_model_analysis")
        assert model_stage["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_degraded_market_state_still_completes(self, tmp_path):
        """Pipeline should complete even without market state (degraded)."""
        # Don't write market state artifact — stage 1 will degrade.
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        assert result.status == "completed"
        assert len(result.stages) == 8
        # Market picture should degrade too.
        mp_stage = next(s for s in result.stages if s["stage_key"] == "append_market_picture_context")
        assert mp_stage["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_summary_has_market_picture(self, tmp_path):
        """summary.json should include market_picture_summary."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        summary = json.loads((run_dir / "summary.json").read_text())
        assert "market_picture_summary" in summary
        assert "scanner_suite" in summary
