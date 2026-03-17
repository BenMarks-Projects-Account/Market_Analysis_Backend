"""Prompt 12C — Runtime Truth Audit tests.

Focused on:
    Truth-audit artifact creation from real stage_data
    Per-scanner raw candidate count recording
    Selected-candidate provenance recording
    Model-analysis input-preview artifact creation
    Market Picture field presence recording
    Honest single-scanner vs multi-scanner reporting
    Engine scan limit ensures multi-scanner candidates survive

STRICT: Only Prompt 12C tests.  No prior tests.  No broad regression.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.workflows.stock_opportunity_runner import (
    MARKET_PICTURE_ENGINE_KEYS,
    STAGE_KEYS,
    STOCK_SCANNER_KEYS,
    WORKFLOW_ID,
    _ENGINE_SCAN_LIMIT,
    RunnerConfig,
    StockOpportunityDeps,
    _build_model_input_preview,
    _build_truth_audit,
    run_stock_opportunity,
)


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════


def _make_engine(engine_key: str, score: float = 65.0) -> dict[str, Any]:
    return {
        "engine_key": engine_key,
        "score": score,
        "label": "Moderate",
        "short_label": "Mod",
        "confidence": 0.75,
        "summary": f"Summary for {engine_key}",
        "trader_takeaway": f"Takeaway for {engine_key}",
        "bull_factors": [f"{engine_key} bull"],
        "bear_factors": [f"{engine_key} bear"],
        "risks": [f"{engine_key} risk"],
        "regime_tags": ["neutral"],
        "supporting_metrics": {},
        "pillar_scores": {},
        "detail_sections": [],
        "engine_status": "ok",
    }


def _make_engines_dict() -> dict[str, Any]:
    return {k: _make_engine(k) for k in MARKET_PICTURE_ENGINE_KEYS}


def _make_raw_candidate(
    symbol: str,
    strategy_id: str,
    composite_score: float,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "strategy_id": strategy_id,
        "composite_score": composite_score,
        "thesis": [f"{symbol} {strategy_id} setup"],
        "metrics": {"rsi": 42.0, "atr_pct": 2.5, "volume_ratio": 1.3},
        "entry_price": 150.0,
        "trend_state": "uptrend",
        "stop_loss": 145.0,
        "target_price": 165.0,
        "signals": ["RSI bounce"],
        "risk_flags": [],
    }


def _make_multi_scanner_scan_result() -> dict[str, Any]:
    """Simulate what the engine returns when given a high top_n.

    4 scanners each produce candidates — the engine returns ALL of them
    (no premature trim) because runners passes _ENGINE_SCAN_LIMIT.
    """
    candidates = [
        _make_raw_candidate("AAPL", "stock_pullback_swing", 85.0),
        _make_raw_candidate("MSFT", "stock_pullback_swing", 80.0),
        _make_raw_candidate("GOOG", "stock_momentum_breakout", 78.0),
        _make_raw_candidate("AMZN", "stock_mean_reversion", 75.0),
        _make_raw_candidate("TSLA", "stock_volatility_expansion", 70.0),
        # AAPL also found by mean_reversion (cross-scanner overlap)
        _make_raw_candidate("AAPL", "stock_mean_reversion", 72.0),
    ]
    return {
        "engine": "stock_engine",
        "status": "ok",
        "as_of": "2026-03-20T15:00:00+00:00",
        "top_n": _ENGINE_SCAN_LIMIT,
        "total_candidates": 6,
        "candidates": candidates,
        "scanners": [
            {"strategy_id": "stock_pullback_swing", "status": "ok", "candidates_count": 2, "max_composite_score": 85.0},
            {"strategy_id": "stock_momentum_breakout", "status": "ok", "candidates_count": 1, "max_composite_score": 78.0},
            {"strategy_id": "stock_mean_reversion", "status": "ok", "candidates_count": 2, "max_composite_score": 75.0},
            {"strategy_id": "stock_volatility_expansion", "status": "ok", "candidates_count": 1, "max_composite_score": 70.0},
        ],
        "warnings": [],
        "scan_time_seconds": 10.0,
    }


def _make_single_scanner_scan_result() -> dict[str, Any]:
    """Simulate a run where only pullback_swing produces candidates."""
    candidates = [
        _make_raw_candidate("AAPL", "stock_pullback_swing", 90.0),
        _make_raw_candidate("MSFT", "stock_pullback_swing", 85.0),
    ]
    return {
        "engine": "stock_engine",
        "status": "ok",
        "as_of": "2026-03-20T15:00:00+00:00",
        "top_n": _ENGINE_SCAN_LIMIT,
        "total_candidates": 2,
        "candidates": candidates,
        "scanners": [
            {"strategy_id": "stock_pullback_swing", "status": "ok", "candidates_count": 2, "max_composite_score": 90.0},
            {"strategy_id": "stock_momentum_breakout", "status": "ok", "candidates_count": 0, "max_composite_score": 0},
            {"strategy_id": "stock_mean_reversion", "status": "ok", "candidates_count": 0, "max_composite_score": 0},
            {"strategy_id": "stock_volatility_expansion", "status": "ok", "candidates_count": 0, "max_composite_score": 0},
        ],
        "warnings": [],
        "scan_time_seconds": 8.0,
    }


def _make_market_state_artifact(data_dir: Path, engines: dict[str, Any] | None = None) -> None:
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
            "summary_text": "Market conditions are favorable.",
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


def _make_deps(scan_result: dict[str, Any] | None = None) -> StockOpportunityDeps:
    engine = AsyncMock()
    engine.scan = AsyncMock(return_value=scan_result or _make_multi_scanner_scan_result())
    return StockOpportunityDeps(stock_engine_service=engine, model_request_fn=None)


# ══════════════════════════════════════════════════════════════════════
# TRUTH-AUDIT ARTIFACT TESTS
# ══════════════════════════════════════════════════════════════════════


class TestTruthAuditArtifact:
    """Verify stock_workflow_truth_audit.json is written and accurate."""

    @pytest.mark.asyncio
    async def test_truth_audit_written_to_disk(self, tmp_path):
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        audit_path = run_dir / "stock_workflow_truth_audit.json"
        assert audit_path.exists(), "Truth audit artifact not written"

    @pytest.mark.asyncio
    async def test_truth_audit_has_required_fields(self, tmp_path):
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        audit = json.loads((run_dir / "stock_workflow_truth_audit.json").read_text())

        required_fields = [
            "configured_default_scanners",
            "runnable_scanners",
            "unavailable_scanners",
            "attempted_scanners",
            "per_scanner_status",
            "per_scanner_raw_candidate_counts",
            "total_raw_candidates",
            "post_dedup_candidate_count",
            "post_filter_candidate_count",
            "shortlisted_for_model_count",
            "final_selected_count",
            "selected_candidates_by_primary_scanner",
            "selected_candidates_by_source_scanners",
            "model_analysis_invoked_count",
            "market_picture_fields_appended",
            "effectively_multi_scanner",
            "multi_scanner_statement",
        ]
        for f in required_fields:
            assert f in audit, f"Missing truth audit field: {f}"

    @pytest.mark.asyncio
    async def test_truth_audit_per_scanner_counts(self, tmp_path):
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        audit = json.loads((run_dir / "stock_workflow_truth_audit.json").read_text())

        counts = audit["per_scanner_raw_candidate_counts"]
        assert counts["stock_pullback_swing"] == 2
        assert counts["stock_momentum_breakout"] == 1
        assert counts["stock_mean_reversion"] == 2
        assert counts["stock_volatility_expansion"] == 1


# ══════════════════════════════════════════════════════════════════════
# PROVENANCE & MULTI-SCANNER TESTS
# ══════════════════════════════════════════════════════════════════════


class TestProvenanceReporting:
    """Verify single vs multi-scanner detection is honest."""

    @pytest.mark.asyncio
    async def test_multi_scanner_run_detected(self, tmp_path):
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps(_make_multi_scanner_scan_result())
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        audit = json.loads((run_dir / "stock_workflow_truth_audit.json").read_text())

        assert audit["effectively_multi_scanner"] is True
        assert "MULTI-SCANNER" in audit["multi_scanner_statement"]
        # Selected candidates should come from more than 1 primary scanner.
        assert len(audit["selected_candidates_by_primary_scanner"]) > 1

    @pytest.mark.asyncio
    async def test_single_scanner_run_detected(self, tmp_path):
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps(_make_single_scanner_scan_result())
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        audit = json.loads((run_dir / "stock_workflow_truth_audit.json").read_text())

        assert audit["effectively_multi_scanner"] is False
        assert "SINGLE-SCANNER" in audit["multi_scanner_statement"]
        assert list(audit["selected_candidates_by_primary_scanner"].keys()) == ["stock_pullback_swing"]

    @pytest.mark.asyncio
    async def test_cross_scanner_symbol_has_multi_source(self, tmp_path):
        """AAPL appears in both pullback_swing and mean_reversion."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps(_make_multi_scanner_scan_result())
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        output = json.loads((run_dir / "output.json").read_text())

        aapl = [c for c in output["candidates"] if c["symbol"] == "AAPL"]
        assert len(aapl) == 1
        # AAPL was found by two scanners.
        assert len(aapl[0]["source_scanners"]) == 2
        assert "stock_pullback_swing" in aapl[0]["source_scanners"]
        assert "stock_mean_reversion" in aapl[0]["source_scanners"]


# ══════════════════════════════════════════════════════════════════════
# MODEL-INPUT PREVIEW TESTS
# ══════════════════════════════════════════════════════════════════════


class TestModelInputPreview:
    """Verify stock_model_analysis_input_preview.json is correct."""

    @pytest.mark.asyncio
    async def test_preview_artifact_written(self, tmp_path):
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        preview_path = run_dir / "stock_model_analysis_input_preview.json"
        assert preview_path.exists(), "Model input preview not written"

    @pytest.mark.asyncio
    async def test_preview_shows_market_picture_engines(self, tmp_path):
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        preview = json.loads((run_dir / "stock_model_analysis_input_preview.json").read_text())

        assert preview["total_selected"] > 0
        first = preview["candidate_previews"][0]
        assert first["market_picture_engine_count"] == 6
        for ek in MARKET_PICTURE_ENGINE_KEYS:
            assert ek in first["market_picture_engines_present"]
            assert first["market_picture_engine_summaries"][ek] is not None

    @pytest.mark.asyncio
    async def test_preview_shows_trade_fields(self, tmp_path):
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        preview = json.loads((run_dir / "stock_model_analysis_input_preview.json").read_text())

        first = preview["candidate_previews"][0]
        assert first["symbol"] is not None
        assert first["scanner_key"] is not None
        assert first["setup_quality"] is not None
        assert first["market_state_ref"] is not None
        assert first["market_regime"] is not None
        assert first["vix"] is not None


# ══════════════════════════════════════════════════════════════════════
# MARKET PICTURE FIELD RECORDING
# ══════════════════════════════════════════════════════════════════════


class TestMarketPictureFieldRecording:
    """Verify truth audit records exactly which MP fields are present."""

    @pytest.mark.asyncio
    async def test_all_mp_fields_recorded_when_present(self, tmp_path):
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        audit = json.loads((run_dir / "stock_workflow_truth_audit.json").read_text())

        mp_fields = audit["market_picture_fields_appended"]
        for ek in MARKET_PICTURE_ENGINE_KEYS:
            assert mp_fields[ek] is True, f"Engine {ek} not recorded as present"
        assert mp_fields["overall_market_summary"] is True
        assert mp_fields["regime"] is True
        assert mp_fields["stability"] is True
        assert mp_fields["confidence"] is True
        assert mp_fields["vix"] is True

    @pytest.mark.asyncio
    async def test_mp_fields_false_when_no_market_state(self, tmp_path):
        """No market state artifact → fields should be False."""
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        result = await run_stock_opportunity(config, deps)

        run_dir = tmp_path / "workflows" / "stock_opportunity" / result.run_id
        audit = json.loads((run_dir / "stock_workflow_truth_audit.json").read_text())

        mp_fields = audit["market_picture_fields_appended"]
        for ek in MARKET_PICTURE_ENGINE_KEYS:
            assert mp_fields[ek] is False


# ══════════════════════════════════════════════════════════════════════
# ENGINE SCAN LIMIT (BOTTLENECK FIX VERIFICATION)
# ══════════════════════════════════════════════════════════════════════


class TestEngineScanLimit:
    """Verify that the runner passes a large top_n to the engine."""

    def test_engine_scan_limit_is_large(self):
        assert _ENGINE_SCAN_LIMIT >= 100, "Engine scan limit too small for multi-scanner survival"

    @pytest.mark.asyncio
    async def test_scan_called_with_high_top_n(self, tmp_path):
        """Engine.scan() must be called with top_n=_ENGINE_SCAN_LIMIT."""
        _make_market_state_artifact(tmp_path)
        config = RunnerConfig(data_dir=str(tmp_path), top_n=10)
        deps = _make_deps()
        await run_stock_opportunity(config, deps)

        deps.stock_engine_service.scan.assert_called_once_with(top_n=_ENGINE_SCAN_LIMIT)
