"""Focused tests for Options Opportunity workflow runner (Prompt 6).

Coverage:
    Market-state consumer integration:
        - successful load via consumer seam
        - missing market state → run fails at stage 1

    Options runner:
        - happy-path run: creates output, summary, manifest, pointer
        - stage artifact creation (5 stage files)
        - final output contains compact candidates with quant fields
        - validation/math/hygiene summaries preserved in artifacts
        - lineage propagation from consumed market state
        - honest handling of unsupported/None metric fields
        - zero candidates → degraded quality
        - scan failure → run fails at stage 2
        - missing/stale market state handling
        - no direct market-provider usage in workflow path
        - RunResult structure / to_dict
        - top_n cap
        - enrichment context propagation

This test file is ONLY for Prompt 6.
No prior prompt tests. No broad regression suite.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.workflows.architecture import FreshnessPolicy
from app.workflows.artifact_strategy import (
    get_manifest_path,
    get_output_path,
    get_pointer_path,
    get_run_dir,
    get_stage_artifact_path,
    get_summary_path,
    make_stage_filename,
)
from app.workflows.market_state_contract import (
    MARKET_STATE_CONTRACT_VERSION,
)
from app.workflows.market_state_discovery import (
    POINTER_FILENAME,
    get_market_state_dir,
    make_artifact_filename,
)
from app.workflows.options_opportunity_runner import (
    ALL_V2_SCANNER_KEYS,
    DEFAULT_TOP_N,
    STAGE_KEYS,
    WORKFLOW_ID,
    OptionsOpportunityDeps,
    RunnerConfig,
    RunResult,
    StageOutcome,
    _extract_compact_candidate,
    run_options_opportunity,
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
    """Write a market-state artifact + pointer to disk."""
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
# HELPERS — V2 options candidate / scanner service stubs
# ══════════════════════════════════════════════════════════════════════


def _make_v2_candidate_dict(
    symbol: str = "SPY",
    scanner_key: str = "put_credit_spread",
    strategy_id: str = "put_credit_spread",
    family_key: str = "vertical_spreads",
    *,
    ev: float | None = 12.50,
    max_profit: float | None = 65.0,
    max_loss: float | None = 435.0,
    pop: float | None = 0.72,
    ror: float | None = 0.1494,
    width: float | None = 5.0,
    net_credit: float | None = 0.65,
    breakeven: list[float] | None = None,
    passed: bool = True,
    downstream_usable: bool = True,
    reject_reasons: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build a V2Candidate.to_dict()-shaped dict for testing."""
    return {
        "candidate_id": f"{symbol}|{strategy_id}|2026-04-17|395/400|001",
        "scanner_key": scanner_key,
        "strategy_id": strategy_id,
        "family_key": family_key,
        "symbol": symbol,
        "underlying_price": 450.25,
        "expiration": "2026-04-17",
        "expiration_back": None,
        "dte": 32,
        "dte_back": None,
        "legs": [
            {
                "index": 0,
                "side": "short",
                "strike": 400.0,
                "option_type": "put",
                "expiration": "2026-04-17",
                "bid": 1.20,
                "ask": 1.35,
                "mid": 1.275,
                "delta": -0.28,
                "gamma": 0.008,
                "theta": -0.04,
                "vega": 0.12,
                "iv": 0.22,
                "open_interest": 5000,
                "volume": 1200,
            },
            {
                "index": 1,
                "side": "long",
                "strike": 395.0,
                "option_type": "put",
                "expiration": "2026-04-17",
                "bid": 0.55,
                "ask": 0.70,
                "mid": 0.625,
                "delta": -0.18,
                "gamma": 0.006,
                "theta": -0.03,
                "vega": 0.10,
                "iv": 0.23,
                "open_interest": 3000,
                "volume": 800,
            },
        ],
        "math": {
            "net_credit": net_credit,
            "net_debit": None,
            "max_profit": max_profit,
            "max_loss": max_loss,
            "width": width,
            "pop": pop,
            "pop_source": "delta_approx",
            "ev": ev,
            "ev_per_day": ev / 32 if ev is not None else None,
            "ror": ror,
            "kelly": None,
            "breakeven": breakeven or [399.35],
            "notes": {
                "net_credit": "short.bid - long.ask = 1.20 - 0.70 = 0.50",
                "pop": "delta_approx from short leg delta=-0.28",
            },
        },
        "diagnostics": {
            "structural_checks": [
                {"name": "valid_leg_count", "passed": True, "detail": "2 legs"},
                {"name": "valid_strike_order", "passed": True, "detail": "short > long for put spread"},
            ],
            "quote_checks": [
                {"name": "all_legs_quoted", "passed": True, "detail": "bid/ask present"},
                {"name": "no_inverted_quotes", "passed": True, "detail": "bid <= ask"},
            ],
            "liquidity_checks": [
                {"name": "min_open_interest", "passed": True, "detail": "OI >= 100"},
                {"name": "min_volume", "passed": True, "detail": "volume >= 10"},
            ],
            "math_checks": [
                {"name": "positive_max_profit", "passed": True, "detail": "max_profit > 0"},
                {"name": "positive_max_loss", "passed": True, "detail": "max_loss > 0"},
                {"name": "width_consistency", "passed": True, "detail": "width matches strikes"},
            ],
            "reject_reasons": reject_reasons or [],
            "warnings": warnings or [],
            "pass_reasons": ["all structural checks passed", "quotes valid on all legs"],
            "items": [],
        },
        "passed": passed,
        "downstream_usable": downstream_usable,
        "contract_version": "2.0.0",
        "scanner_version": "vertical_spreads_v2",
        "generated_at": _STUB_TS,
    }


def _make_scan_result(
    scanner_key: str = "put_credit_spread",
    family_key: str = "vertical_spreads",
    symbol: str = "SPY",
    candidates: list[dict[str, Any]] | None = None,
    rejected: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a V2ScanResult-like dict."""
    cands = candidates if candidates is not None else [
        _make_v2_candidate_dict(symbol=symbol, scanner_key=scanner_key, family_key=family_key),
    ]
    rej = rejected or []
    return {
        "scanner_key": scanner_key,
        "strategy_id": scanner_key,
        "family_key": family_key,
        "symbol": symbol,
        "candidates": cands,
        "rejected": rej,
        "total_constructed": len(cands) + len(rej),
        "total_passed": len(cands),
        "total_rejected": len(rej),
        "reject_reason_counts": {},
        "warning_counts": {},
        "phase_counts": [
            {"phase": "constructed", "remaining": len(cands) + len(rej)},
            {"phase": "normalized", "remaining": len(cands)},
        ],
        "elapsed_ms": 42.5,
    }


class _StubOptionsScannerService:
    """Stub for options_scanner_service.scan()."""

    def __init__(
        self,
        *,
        scan_results: list[dict[str, Any]] | None = None,
        fail: bool = False,
    ) -> None:
        self._scan_results = scan_results
        self._fail = fail
        self.last_call_args: dict[str, Any] | None = None

    async def scan(
        self,
        symbols: list[str],
        scanner_keys: list[str],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.last_call_args = {
            "symbols": symbols,
            "scanner_keys": scanner_keys,
            "context": context,
        }
        if self._fail:
            raise RuntimeError("Options scanner service unavailable")

        results = self._scan_results if self._scan_results is not None else [
            _make_scan_result("put_credit_spread", "vertical_spreads", "SPY"),
            _make_scan_result("iron_condor", "iron_condors", "SPY",
                              candidates=[
                                  _make_v2_candidate_dict(
                                      symbol="SPY",
                                      scanner_key="iron_condor",
                                      strategy_id="iron_condor",
                                      family_key="iron_condors",
                                      ev=8.0,
                                      max_profit=120.0,
                                      max_loss=380.0,
                                  ),
                              ]),
            _make_scan_result("put_credit_spread", "vertical_spreads", "QQQ",
                              candidates=[
                                  _make_v2_candidate_dict(
                                      symbol="QQQ",
                                      scanner_key="put_credit_spread",
                                      family_key="vertical_spreads",
                                      ev=15.0,
                                  ),
                              ]),
        ]

        ok_count = len(results)
        return {
            "scan_results": results,
            "warnings": [],
            "scanners_total": ok_count,
            "scanners_ok": ok_count,
            "scanners_failed": 0,
        }


def _make_deps(
    *,
    scan_results: list[dict[str, Any]] | None = None,
    scan_fail: bool = False,
) -> OptionsOpportunityDeps:
    return OptionsOpportunityDeps(
        options_scanner_service=_StubOptionsScannerService(
            scan_results=scan_results,
            fail=scan_fail,
        ),
    )


# ══════════════════════════════════════════════════════════════════════
# OPTIONS OPPORTUNITY RUNNER TESTS
# ══════════════════════════════════════════════════════════════════════


class TestOptionsOpportunityRunner:
    """Tests for ``run_options_opportunity``."""

    # ── Happy path ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_happy_path(self, tmp_path: Path):
        """Full run: market state → scan → validate → enrich → package."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)

        assert result.status == "completed"
        assert result.workflow_id == WORKFLOW_ID
        assert result.run_id.startswith("run_")
        assert result.error is None
        assert len(result.stages) == len(STAGE_KEYS)

    @pytest.mark.asyncio
    async def test_output_created(self, tmp_path: Path):
        """output.json must be created on disk."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)
        assert result.status == "completed"
        assert result.artifact_path is not None

        output_path = Path(result.artifact_path)
        assert output_path.is_file()
        output_data = json.loads(output_path.read_text(encoding="utf-8"))
        assert output_data["workflow_id"] == WORKFLOW_ID
        assert "candidates" in output_data

    @pytest.mark.asyncio
    async def test_output_contains_compact_candidates(self, tmp_path: Path):
        """Output candidates should have compact quant fields."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)
        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        candidates = output_data["candidates"]
        assert len(candidates) >= 1

        cand = candidates[0]
        # Identity fields
        assert "candidate_id" in cand
        assert "scanner_key" in cand
        assert "strategy_id" in cand
        assert "family_key" in cand
        assert "symbol" in cand

        # Math fields preserved
        assert "math" in cand
        math = cand["math"]
        assert "ev" in math
        assert "max_profit" in math
        assert "max_loss" in math
        assert "pop" in math
        assert "ror" in math
        assert "width" in math
        assert "breakeven" in math
        assert "net_credit" in math

    @pytest.mark.asyncio
    async def test_validation_summaries_in_output(self, tmp_path: Path):
        """Output candidates must include structural/math/hygiene summaries."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)
        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        cand = output_data["candidates"][0]

        # Structural validation
        sv = cand["structural_validation"]
        assert "passed" in sv
        assert "total_checks" in sv
        assert "failure_count" in sv

        # Math validation
        mv = cand["math_validation"]
        assert "passed" in mv
        assert "total_checks" in mv

        # Hygiene
        h = cand["hygiene"]
        assert "quote_sanity_ok" in h
        assert "liquidity_ok" in h

        # Diagnostics summary
        ds = cand["diagnostics_summary"]
        assert "reject_reasons" in ds
        assert "warnings" in ds

    # ── Lineage ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_lineage_propagated(self, tmp_path: Path):
        """market_state_ref must appear in output and candidates."""
        custom_id = "mi_custom_lineage_test"
        _write_market_state_fixture(tmp_path, artifact_id=custom_id)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)
        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        assert output_data["market_state_ref"] == custom_id

        for cand in output_data["candidates"]:
            assert cand["market_state_ref"] == custom_id

    # ── Stage artifacts ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_all_stage_artifacts_written(self, tmp_path: Path):
        """All 5 stage artifacts must exist on disk after a successful run."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)
        assert result.status == "completed"

        for stage_key in STAGE_KEYS:
            stage_path = get_stage_artifact_path(
                tmp_path, WORKFLOW_ID, result.run_id, stage_key
            )
            assert stage_path.is_file(), f"Stage artifact missing: {stage_key}"

            stage_data = json.loads(stage_path.read_text(encoding="utf-8"))
            assert stage_data["workflow_id"] == WORKFLOW_ID
            assert stage_data["run_id"] == result.run_id
            assert stage_data["stage_key"] == stage_key

    @pytest.mark.asyncio
    async def test_manifest_created(self, tmp_path: Path):
        """manifest.json must exist and list all stages."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)
        manifest_path = get_manifest_path(tmp_path, WORKFLOW_ID, result.run_id)
        assert manifest_path.is_file()

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["workflow_id"] == WORKFLOW_ID
        assert len(manifest["stages"]) == len(STAGE_KEYS)
        assert manifest["output_filename"] == "output.json"

    @pytest.mark.asyncio
    async def test_summary_created(self, tmp_path: Path):
        """summary.json must exist with run-level info."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)
        summary_path = get_summary_path(tmp_path, WORKFLOW_ID, result.run_id)
        assert summary_path.is_file()

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["workflow_id"] == WORKFLOW_ID
        assert summary["status"] == "completed"
        assert "market_state_ref" in summary
        assert "validation_summary" in summary

    @pytest.mark.asyncio
    async def test_pointer_updated(self, tmp_path: Path):
        """latest.json pointer must be written after successful run."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)
        pointer_path = get_pointer_path(tmp_path, WORKFLOW_ID)
        assert pointer_path.is_file()

        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        assert pointer["run_id"] == result.run_id
        assert pointer["workflow_id"] == WORKFLOW_ID

    # ── Missing / failed market state ────────────────────────────

    @pytest.mark.asyncio
    async def test_missing_market_state_degrades(self, tmp_path: Path):
        """Run degrades at stage 1 when no market state exists, continues."""
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)
        assert result.status == "completed"
        assert result.stages[0]["stage_key"] == "load_market_state"
        assert result.stages[0]["status"] == "degraded"
        # All 5 stages should still run.
        assert len(result.stages) == len(STAGE_KEYS)
        assert any("proceeding without market context" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_stale_market_state_with_forbid(self, tmp_path: Path):
        """Stale market state with allow_stale=False should degrade."""
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _write_market_state_fixture(tmp_path, generated_at=old_ts)
        config = RunnerConfig(
            data_dir=tmp_path,
            freshness_policy=FreshnessPolicy(allow_stale=False, degrade_after_seconds=60),
        )
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)
        assert result.status == "completed"
        assert result.stages[0]["status"] == "degraded"

    # ── Scan failure ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_scan_failure(self, tmp_path: Path):
        """Run should fail at stage 2 when scanner throws."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps(scan_fail=True)

        result = await run_options_opportunity(config, deps)
        assert result.status == "failed"
        assert "scan" in (result.error or "").lower()
        assert len(result.stages) == 2

    # ── Zero candidates ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_zero_candidates(self, tmp_path: Path):
        """Run with no scanner candidates → degraded quality."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps(scan_results=[
            _make_scan_result("put_credit_spread", "vertical_spreads", "SPY",
                              candidates=[]),
        ])

        result = await run_options_opportunity(config, deps)
        assert result.status == "completed"

        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        assert output_data["quality"]["level"] == "no_candidates"
        assert output_data["candidates"] == []

    # ── None metric fields ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_none_metrics_propagated(self, tmp_path: Path):
        """Candidates with None EV/POP/etc. must propagate None, not 0."""
        _write_market_state_fixture(tmp_path)

        # Calendar spread: max_profit and pop are path-dependent → None
        cand = _make_v2_candidate_dict(
            scanner_key="calendar_call_spread",
            strategy_id="calendar_call_spread",
            family_key="calendars",
            ev=None,
            pop=None,
            ror=None,
            max_profit=None,
            max_loss=250.0,
            width=None,
            breakeven=[],
        )

        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps(scan_results=[
            _make_scan_result(
                "calendar_call_spread", "calendars", "SPY",
                candidates=[cand],
            ),
        ])

        result = await run_options_opportunity(config, deps)
        assert result.status == "completed"

        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        out_cand = output_data["candidates"][0]
        math = out_cand["math"]
        assert math["ev"] is None
        assert math["pop"] is None
        assert math["ror"] is None
        assert math["max_profit"] is None
        assert math["width"] is None

    # ── Top-N cap ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_top_n_cap(self, tmp_path: Path):
        """Only top_n candidates should appear in output."""
        _write_market_state_fixture(tmp_path)

        cands = [
            _make_v2_candidate_dict(symbol=f"SYM{i}", ev=float(100 - i))
            for i in range(10)
        ]

        config = RunnerConfig(data_dir=tmp_path, top_n=3)
        deps = _make_deps(scan_results=[
            _make_scan_result("put_credit_spread", "vertical_spreads", "SPY",
                              candidates=cands),
        ])

        result = await run_options_opportunity(config, deps)
        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        assert len(output_data["candidates"]) == 3
        assert output_data["quality"]["top_n_cap"] == 3
        assert output_data["quality"]["total_candidates_found"] == 10

    # ── Ranking by EV ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_candidates_ranked_by_ev(self, tmp_path: Path):
        """Candidates should be ranked by EV descending."""
        _write_market_state_fixture(tmp_path)

        cands = [
            _make_v2_candidate_dict(symbol="LOW", ev=5.0),
            _make_v2_candidate_dict(symbol="HIGH", ev=25.0),
            _make_v2_candidate_dict(symbol="MID", ev=15.0),
        ]

        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps(scan_results=[
            _make_scan_result("put_credit_spread", "vertical_spreads", "SPY",
                              candidates=cands),
        ])

        result = await run_options_opportunity(config, deps)
        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        evs = [c["math"]["ev"] for c in output_data["candidates"]]
        assert evs == sorted(evs, reverse=True)

    # ── Enrichment context ───────────────────────────────────────

    @pytest.mark.asyncio
    async def test_enrichment_context_from_market_state(self, tmp_path: Path):
        """Market regime and risk stance should appear in stage artifact."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)

        # Read enrich_evaluate stage artifact.
        stage_path = get_stage_artifact_path(
            tmp_path, WORKFLOW_ID, result.run_id, "enrich_evaluate"
        )
        stage_data = json.loads(stage_path.read_text(encoding="utf-8"))
        enriched = stage_data.get("candidates", [])
        assert len(enriched) >= 1

        cand = enriched[0]
        assert cand["market_state_ref"] == _STUB_ID
        assert cand["market_regime"] == "neutral"
        assert cand["risk_environment"] == "stable"
        assert "rank" in cand

    # ── Validation stage preserves info ──────────────────────────

    @pytest.mark.asyncio
    async def test_validate_math_stage_artifact(self, tmp_path: Path):
        """validate_math stage artifact should have validation summary."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)

        stage_path = get_stage_artifact_path(
            tmp_path, WORKFLOW_ID, result.run_id, "validate_math"
        )
        stage_data = json.loads(stage_path.read_text(encoding="utf-8"))
        assert stage_data["status"] == "completed"
        assert "validated_count" in stage_data
        assert "validation_summary" in stage_data

        vs = stage_data["validation_summary"]
        assert "total_validated" in vs
        assert "structural_all_passed" in vs
        assert "math_all_passed" in vs
        assert "quote_sanity_ok" in vs
        assert "liquidity_sanity_ok" in vs

    # ── Non-usable candidates filtered with reason ───────────────

    @pytest.mark.asyncio
    async def test_non_usable_candidates_filtered(self, tmp_path: Path):
        """Candidates with downstream_usable=False should be filtered."""
        _write_market_state_fixture(tmp_path)

        usable = _make_v2_candidate_dict(symbol="PASS", ev=20.0)
        rejected = _make_v2_candidate_dict(
            symbol="FAIL",
            ev=30.0,
            passed=False,
            downstream_usable=False,
            reject_reasons=["v2_inverted_quote"],
        )

        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps(scan_results=[
            _make_scan_result("put_credit_spread", "vertical_spreads", "SPY",
                              candidates=[usable, rejected]),
        ])

        result = await run_options_opportunity(config, deps)
        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        # Only the usable candidate should survive.
        assert len(output_data["candidates"]) == 1
        assert output_data["candidates"][0]["symbol"] == "PASS"

    # ── RunResult structure ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_run_result_to_dict(self, tmp_path: Path):
        """RunResult.to_dict() should produce all expected fields."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)
        d = result.to_dict()

        assert d["run_id"] == result.run_id
        assert d["workflow_id"] == WORKFLOW_ID
        assert d["status"] == "completed"
        assert "stages" in d
        assert "warnings" in d
        assert d["error"] is None

    @pytest.mark.asyncio
    async def test_all_five_stages_recorded(self, tmp_path: Path):
        """All 5 stages should be in the result."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps()

        result = await run_options_opportunity(config, deps)
        assert len(result.stages) == 5
        recorded_keys = [s["stage_key"] for s in result.stages]
        assert recorded_keys == list(STAGE_KEYS)

    # ── Scanner service not called directly by runner ────────────

    @pytest.mark.asyncio
    async def test_scanner_receives_context_not_raw_market_data(self, tmp_path: Path):
        """Runner should pass context (ref + summary) to scanner, not raw data."""
        _write_market_state_fixture(tmp_path)
        config = RunnerConfig(data_dir=tmp_path)
        stub = _StubOptionsScannerService()
        deps = OptionsOpportunityDeps(options_scanner_service=stub)

        await run_options_opportunity(config, deps)

        assert stub.last_call_args is not None
        ctx = stub.last_call_args["context"]
        assert ctx["market_state_ref"] == _STUB_ID
        assert "consumer_summary" in ctx

    # ── Scan diagnostics in output ───────────────────────────────

    @pytest.mark.asyncio
    async def test_scan_diagnostics_in_output(self, tmp_path: Path):
        """Output should include scan-level diagnostic counts."""
        _write_market_state_fixture(tmp_path)

        rejected_cand = _make_v2_candidate_dict(
            symbol="REJ", passed=False, downstream_usable=False,
            reject_reasons=["v2_missing_quote"],
        )

        config = RunnerConfig(data_dir=tmp_path)
        deps = _make_deps(scan_results=[
            _make_scan_result(
                "put_credit_spread", "vertical_spreads", "SPY",
                candidates=[_make_v2_candidate_dict()],
                rejected=[rejected_cand],
            ),
        ])

        result = await run_options_opportunity(config, deps)
        output_data = json.loads(
            Path(result.artifact_path).read_text(encoding="utf-8")
        )
        diag = output_data.get("scan_diagnostics", {})
        assert diag["total_constructed"] >= 2
        assert diag["total_rejected"] >= 1


# ══════════════════════════════════════════════════════════════════════
# COMPACT CANDIDATE EXTRACTION UNIT TESTS
# ══════════════════════════════════════════════════════════════════════


class TestExtractCompactCandidate:
    """Unit tests for _extract_compact_candidate."""

    def test_preserves_identity(self):
        cand = _make_v2_candidate_dict()
        compact = _extract_compact_candidate(cand)
        assert compact["candidate_id"] == cand["candidate_id"]
        assert compact["scanner_key"] == "put_credit_spread"
        assert compact["strategy_id"] == "put_credit_spread"
        assert compact["family_key"] == "vertical_spreads"
        assert compact["symbol"] == "SPY"

    def test_preserves_math_fields(self):
        cand = _make_v2_candidate_dict(ev=12.5, pop=0.72, ror=0.15)
        compact = _extract_compact_candidate(cand)
        math = compact["math"]
        assert math["ev"] == 12.5
        assert math["pop"] == 0.72
        assert math["ror"] == 0.15
        assert math["breakeven"] == [399.35]

    def test_none_ev_stays_none(self):
        cand = _make_v2_candidate_dict(ev=None, pop=None, ror=None, max_profit=None)
        compact = _extract_compact_candidate(cand)
        assert compact["math"]["ev"] is None
        assert compact["math"]["pop"] is None
        assert compact["math"]["ror"] is None
        assert compact["math"]["max_profit"] is None

    def test_structural_validation_summary(self):
        cand = _make_v2_candidate_dict()
        compact = _extract_compact_candidate(cand)
        sv = compact["structural_validation"]
        assert sv["passed"] is True
        assert sv["total_checks"] == 2
        assert sv["pass_count"] == 2
        assert sv["failure_count"] == 0

    def test_hygiene_summary(self):
        cand = _make_v2_candidate_dict()
        compact = _extract_compact_candidate(cand)
        h = compact["hygiene"]
        assert h["quote_sanity_ok"] is True
        assert h["liquidity_ok"] is True

    def test_diagnostics_summary(self):
        cand = _make_v2_candidate_dict(
            reject_reasons=["v2_width_too_narrow"],
            warnings=["POP approximate"],
        )
        compact = _extract_compact_candidate(cand)
        ds = compact["diagnostics_summary"]
        assert "v2_width_too_narrow" in ds["reject_reasons"]
        assert "POP approximate" in ds["warnings"]

    def test_compact_legs(self):
        cand = _make_v2_candidate_dict()
        compact = _extract_compact_candidate(cand)
        assert compact["leg_count"] == 2
        assert len(compact["legs"]) == 2
        leg = compact["legs"][0]
        assert "strike" in leg
        assert "side" in leg
        assert "bid" in leg
        assert "delta" in leg
