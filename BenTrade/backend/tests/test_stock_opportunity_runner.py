"""Focused tests for market-state consumer seam and Stock Opportunity
workflow runner (Prompt 5).

Coverage:
    Consumer seam:
        - successful load returns usable result
        - missing pointer returns unusable result with error
        - stale artifact when policy forbids stale
        - lineage reference (artifact_id → market_state_ref)

    Stock runner:
        - happy-path run: creates output, summary, manifest, pointer
        - output conforms to OUTPUT_ARTIFACT_REQUIRED_KEYS
        - missing market state → run fails at stage 1
        - scan failure → run fails at stage 2
        - zero candidates → degraded quality
        - all 5 stages recorded in result
        - stage artifacts written to disk
        - RunResult structure
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.workflows.architecture import FreshnessPolicy
from app.workflows.artifact_strategy import (
    OUTPUT_ARTIFACT_REQUIRED_KEYS,
    get_manifest_path,
    get_output_path,
    get_pointer_path,
    get_run_dir,
    get_stage_artifact_path,
    get_summary_path,
    make_stage_filename,
)
from app.workflows.market_state_consumer import (
    MarketStateConsumerResult,
    load_market_state_for_consumer,
)
from app.workflows.market_state_contract import (
    MARKET_STATE_CONTRACT_VERSION,
)
from app.workflows.market_state_discovery import (
    POINTER_FILENAME,
    get_market_state_dir,
    make_artifact_filename,
)
from app.workflows.stock_opportunity_runner import (
    DEFAULT_TOP_N,
    STAGE_KEYS,
    WORKFLOW_ID,
    RunnerConfig,
    RunResult,
    StageOutcome,
    StockOpportunityDeps,
    run_stock_opportunity,
)


# ══════════════════════════════════════════════════════════════════════
# HELPERS — Market-state fixture writer
# ══════════════════════════════════════════════════════════════════════

_STUB_TS = "2026-03-16T14:30:00+00:00"
_STUB_ID = "mi_run_20260316_143000_abcd"


def _make_minimal_market_state(
    *,
    artifact_id: str = _STUB_ID,
    generated_at: str = _STUB_TS,
) -> dict[str, Any]:
    """Build a minimal market-state artifact that passes validation."""
    return {
        "contract_version": MARKET_STATE_CONTRACT_VERSION,
        "artifact_id": artifact_id,
        "workflow_id": "market_intelligence",
        "generated_at": generated_at,
        "publication": {"status": "valid"},
        "freshness": {
            "generated_at": generated_at,
            "freshness_tier": "fresh",
        },
        "quality": {"overall": "good"},
        "market_snapshot": {"metrics": {}, "snapshot_at": generated_at},
        "engines": {},
        "composite": {
            "risk_stance": "neutral",
            "support_state": "stable",
        },
        "conflicts": [],
        "model_interpretation": None,
        "consumer_summary": {
            "market_state": "neutral",
            "stability_state": "stable",
            "quick_take": "Test stub",
        },
        "lineage": {"run_id": "run_test"},
        "warnings": [],
    }


def _write_market_state_fixture(
    data_dir: Path,
    *,
    artifact_id: str = _STUB_ID,
    status: str = "valid",
    generated_at: str = _STUB_TS,
) -> Path:
    """Write a market-state artifact + pointer to disk in ``data_dir``."""
    ms_dir = get_market_state_dir(data_dir)
    ms_dir.mkdir(parents=True, exist_ok=True)

    artifact = _make_minimal_market_state(
        artifact_id=artifact_id,
        generated_at=generated_at,
    )
    ts_dt = datetime.fromisoformat(generated_at)
    filename = make_artifact_filename(ts_dt)
    artifact_path = ms_dir / filename
    artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    pointer = {
        "artifact_filename": filename,
        "artifact_id": artifact_id,
        "published_at": generated_at,
        "status": status,
        "contract_version": MARKET_STATE_CONTRACT_VERSION,
    }
    pointer_path = ms_dir / POINTER_FILENAME
    pointer_path.write_text(json.dumps(pointer, indent=2), encoding="utf-8")

    return artifact_path


# ══════════════════════════════════════════════════════════════════════
# HELPERS — Stock engine service stub
# ══════════════════════════════════════════════════════════════════════


def _make_stock_candidate(
    symbol: str,
    scanner_key: str,
    composite_score: float = 72.0,
) -> dict[str, Any]:
    """Build a minimal raw stock candidate dict (as returned by scanners)."""
    return {
        "symbol": symbol,
        "strategy_id": scanner_key,
        "price": 155.0,
        "composite_score": composite_score,
        "trade_key": f"{symbol}|{scanner_key}",
        "confidence": 0.8,
        "metrics": {
            "atr_pct": 0.025,
            "avg_dollar_volume": 5_000_000,
        },
        "score_breakdown": {
            "trend": 75,
            "momentum": 68,
            "volume": 70,
        },
        "thesis": [
            f"{symbol} shows strong momentum",
            "Volume confirming breakout",
        ],
        "data_source": {
            "provider": "tradier",
            "confidence": 0.85,
        },
        "risk_notes": [],
        "pullback_state": "confirmed",
        "breakout_state": "confirmed",
        "reversion_state": "confirmed",
        "expansion_state": "confirmed",
    }


class _StubStockEngineService:
    """Stub for StockEngineService.scan()."""

    def __init__(
        self,
        *,
        candidates: list[dict[str, Any]] | None = None,
        fail: bool = False,
    ) -> None:
        self._candidates = candidates
        self._fail = fail

    async def scan(self, top_n: int | None = None) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("StockEngineService unavailable")
        cands = self._candidates if self._candidates is not None else [
            _make_stock_candidate("AAPL", "stock_momentum_breakout", 82.0),
            _make_stock_candidate("MSFT", "stock_pullback_swing", 75.0),
            _make_stock_candidate("GOOGL", "stock_mean_reversion", 68.5),
        ]
        return {
            "engine": "stock_engine",
            "status": "ok",
            "as_of": _STUB_TS,
            "total_candidates": len(cands),
            "candidates": cands,
            "scanners": [
                {"strategy_id": "stock_pullback_swing", "status": "ok", "candidates_count": 1},
                {"strategy_id": "stock_momentum_breakout", "status": "ok", "candidates_count": 1},
                {"strategy_id": "stock_mean_reversion", "status": "ok", "candidates_count": 1},
                {"strategy_id": "stock_volatility_expansion", "status": "ok", "candidates_count": 0},
            ],
            "warnings": [],
            "scan_time_seconds": 1.23,
        }


def _make_deps(
    *,
    candidates: list[dict[str, Any]] | None = None,
    scan_fail: bool = False,
) -> StockOpportunityDeps:
    return StockOpportunityDeps(
        stock_engine_service=_StubStockEngineService(
            candidates=candidates,
            fail=scan_fail,
        ),
    )


# ══════════════════════════════════════════════════════════════════════
# CONSUMER SEAM TESTS
# ══════════════════════════════════════════════════════════════════════


class TestMarketStateConsumer:
    """Tests for ``load_market_state_for_consumer``."""

    def test_successful_load(self, tmp_path: Path):
        _write_market_state_fixture(tmp_path)
        result = load_market_state_for_consumer(tmp_path)
        assert result.loaded is True
        assert result.market_state_ref == _STUB_ID
        assert result.publication_status == "valid"
        assert result.freshness_tier is not None
        assert result.artifact is not None
        assert result.consumer_summary is not None
        assert result.error is None

    def test_missing_pointer_returns_unusable(self, tmp_path: Path):
        result = load_market_state_for_consumer(tmp_path)
        assert result.loaded is False
        assert result.error is not None
        assert "not found" in result.error.lower() or "not usable" in result.error.lower()

    def test_lineage_reference_propagated(self, tmp_path: Path):
        custom_id = "custom_artifact_id_xyz"
        _write_market_state_fixture(tmp_path, artifact_id=custom_id)
        result = load_market_state_for_consumer(tmp_path)
        assert result.loaded is True
        assert result.market_state_ref == custom_id

    def test_stale_with_allow_stale_true(self, tmp_path: Path):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_market_state_fixture(tmp_path, generated_at=old_ts)
        policy = FreshnessPolicy(allow_stale=True)
        result = load_market_state_for_consumer(tmp_path, freshness_policy=policy)
        # Should still be loaded because allow_stale=True.
        assert result.loaded is True
        assert result.freshness_tier in ("stale", "warning", "fresh")

    def test_stale_with_allow_stale_false(self, tmp_path: Path):
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_market_state_fixture(tmp_path, generated_at=old_ts)
        policy = FreshnessPolicy(allow_stale=False, degrade_after_seconds=60)
        result = load_market_state_for_consumer(tmp_path, freshness_policy=policy)
        assert result.loaded is False

    def test_consumer_result_to_dict(self, tmp_path: Path):
        _write_market_state_fixture(tmp_path)
        result = load_market_state_for_consumer(tmp_path)
        d = result.to_dict()
        assert d["loaded"] is True
        assert d["market_state_ref"] == _STUB_ID
        assert "artifact" not in d  # should not be serialised

    def test_composite_extracted(self, tmp_path: Path):
        _write_market_state_fixture(tmp_path)
        result = load_market_state_for_consumer(tmp_path)
        assert result.composite is not None
        assert result.composite.get("risk_stance") == "neutral"


# ══════════════════════════════════════════════════════════════════════
# STOCK OPPORTUNITY RUNNER TESTS
# ══════════════════════════════════════════════════════════════════════


class TestStockOpportunityRunner:
    """Tests for ``run_stock_opportunity``."""

    @pytest.mark.asyncio
    async def test_happy_path(self, tmp_path: Path):
        """Full run: market state + scan + normalize + enrich + package."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)

        assert result.status == "completed"
        assert result.workflow_id == WORKFLOW_ID
        assert result.run_id.startswith("run_")
        assert result.error is None
        assert len(result.stages) == len(STAGE_KEYS)

    @pytest.mark.asyncio
    async def test_output_conforms_to_contract(self, tmp_path: Path):
        """output.json must contain all OUTPUT_ARTIFACT_REQUIRED_KEYS."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)
        assert result.status == "completed"
        assert result.artifact_path is not None

        output_path = Path(result.artifact_path)
        assert output_path.is_file()
        output_data = json.loads(output_path.read_text(encoding="utf-8"))

        for key in OUTPUT_ARTIFACT_REQUIRED_KEYS:
            assert key in output_data, f"Missing required key: {key}"

    @pytest.mark.asyncio
    async def test_candidates_in_output(self, tmp_path: Path):
        """Output should contain normalized+enriched candidates."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)
        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        candidates = output_data["candidates"]
        assert len(candidates) == 3
        # Each candidate should have the canonical contract fields.
        for cand in candidates:
            assert "symbol" in cand
            assert "setup_quality" in cand
            assert "scanner_key" in cand

    @pytest.mark.asyncio
    async def test_lineage_in_output(self, tmp_path: Path):
        """Output must carry market_state_ref for upstream tracing."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)
        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        assert output_data["market_state_ref"] == _STUB_ID

    @pytest.mark.asyncio
    async def test_ranking_order(self, tmp_path: Path):
        """Candidates ranked by setup_quality descending."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)
        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        candidates = output_data["candidates"]
        scores = [c.get("setup_quality", 0) for c in candidates]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_missing_market_state_degrades_stage1(self, tmp_path: Path):
        """No market state → stage 1 degrades, workflow continues."""
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)
        assert result.status == "completed"
        assert result.stages[0]["stage_key"] == "load_market_state"
        assert result.stages[0]["status"] == "degraded"
        # All 5 stages should still run.
        assert len(result.stages) == len(STAGE_KEYS)
        assert any("proceeding without market context" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_scan_failure_fails_stage2(self, tmp_path: Path):
        """Scanner failure → run fails at scan."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps(scan_fail=True)

        result = await run_stock_opportunity(config, deps)
        assert result.status == "failed"
        assert "scan" in (result.error or "")
        assert len(result.stages) == 2

    @pytest.mark.asyncio
    async def test_zero_candidates_quality_degraded(self, tmp_path: Path):
        """No candidates → quality = 'no_candidates'."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps(candidates=[])

        result = await run_stock_opportunity(config, deps)
        assert result.status == "completed"

        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        assert output_data["quality"]["level"] == "no_candidates"
        assert output_data["candidates"] == []

    @pytest.mark.asyncio
    async def test_all_five_stages_recorded(self, tmp_path: Path):
        """Result must list all 5 stages."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)
        stage_keys = [s["stage_key"] for s in result.stages]
        assert tuple(stage_keys) == STAGE_KEYS

    @pytest.mark.asyncio
    async def test_stage_artifacts_on_disk(self, tmp_path: Path):
        """All stage artifacts, manifest, summary, pointer written."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)

        for key in STAGE_KEYS:
            artifact_path = get_stage_artifact_path(
                tmp_path, WORKFLOW_ID, result.run_id, key,
            )
            assert artifact_path.is_file(), f"Missing stage artifact: {key}"

        assert get_output_path(tmp_path, WORKFLOW_ID, result.run_id).is_file()
        assert get_summary_path(tmp_path, WORKFLOW_ID, result.run_id).is_file()
        assert get_manifest_path(tmp_path, WORKFLOW_ID, result.run_id).is_file()
        assert get_pointer_path(tmp_path, WORKFLOW_ID).is_file()

    @pytest.mark.asyncio
    async def test_manifest_structure(self, tmp_path: Path):
        """Manifest should list all stages and reference output.json."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)
        manifest_path = get_manifest_path(tmp_path, WORKFLOW_ID, result.run_id)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert manifest["workflow_id"] == WORKFLOW_ID
        assert manifest["run_id"] == result.run_id
        assert manifest["status"] == "completed"
        assert manifest["output_filename"] == "output.json"
        assert len(manifest["stages"]) == len(STAGE_KEYS)

    @pytest.mark.asyncio
    async def test_pointer_updated(self, tmp_path: Path):
        """latest.json should point to the completed run."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)
        pointer_path = get_pointer_path(tmp_path, WORKFLOW_ID)
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))

        assert pointer["run_id"] == result.run_id
        assert pointer["workflow_id"] == WORKFLOW_ID
        assert pointer["status"] == "valid"

    @pytest.mark.asyncio
    async def test_run_result_to_dict(self, tmp_path: Path):
        """RunResult.to_dict() returns complete structure."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)
        d = result.to_dict()

        assert d["run_id"] == result.run_id
        assert d["workflow_id"] == WORKFLOW_ID
        assert d["status"] == "completed"
        assert isinstance(d["stages"], list)
        assert isinstance(d["warnings"], list)

    @pytest.mark.asyncio
    async def test_top_n_cap_applied(self, tmp_path: Path):
        """top_n config should cap selected candidates."""
        _write_market_state_fixture(tmp_path)
        # Provide more candidates than the cap.
        many_candidates = [
            _make_stock_candidate(f"SYM{i}", "stock_momentum_breakout", 90 - i)
            for i in range(30)
        ]
        config = RunnerConfig(data_dir=tmp_path, top_n=5)
        deps = _make_deps(candidates=many_candidates)

        result = await run_stock_opportunity(config, deps)
        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        assert len(output_data["candidates"]) == 5
        assert output_data["quality"]["top_n_cap"] == 5
        assert output_data["quality"]["total_candidates_found"] == 30

    @pytest.mark.asyncio
    async def test_enrichment_adds_market_context(self, tmp_path: Path):
        """Each candidate should carry market_state_ref and regime."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_stock_opportunity(config, deps)
        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        for cand in output_data["candidates"]:
            assert cand["market_state_ref"] == _STUB_ID
            assert "market_regime" in cand
            assert "rank" in cand
