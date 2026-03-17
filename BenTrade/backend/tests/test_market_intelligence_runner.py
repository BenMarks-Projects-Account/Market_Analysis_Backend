"""Focused tests for the Market Intelligence workflow runner (Prompt 4).

Small, operational test set covering:
    - successful run creates valid artifact and pointer
    - artifact conforms to contract
    - degraded engines → degraded publication status
    - all engines fail → no valid pointer published
    - model interpretation skipped / failed handled honestly
    - collect_inputs failure aborts the run
    - all 6 stages recorded in result
    - RunResult has expected structure
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.workflows.market_intelligence_runner import (
    MI_STAGES,
    MarketIntelligenceDeps,
    RunResult,
    RunnerConfig,
    run_market_intelligence,
)
from app.workflows.market_state_contract import (
    REQUIRED_TOP_LEVEL_KEYS,
    validate_market_state,
)
from app.workflows.market_state_discovery import (
    POINTER_FILENAME,
    get_market_state_dir,
)


# ═══════════════════════════════════════════════════════════════════════
# TEST STUBS
# ═══════════════════════════════════════════════════════════════════════

_STUB_TS = "2026-03-16T14:30:00+00:00"


def _make_stub_normalized(engine_key: str, status: str = "ok") -> dict[str, Any]:
    """Produce a valid normalized engine output for testing."""
    return {
        "engine_key": engine_key,
        "engine_name": engine_key.replace("_", " ").title(),
        "as_of": _STUB_TS,
        "score": 65,
        "label": "Moderately Bullish",
        "short_label": "Bullish",
        "confidence": 75,
        "signal_quality": "medium",
        "time_horizon": "short_term",
        "freshness": {},
        "summary": f"Test summary for {engine_key}",
        "trader_takeaway": "Cautiously optimistic",
        "bull_factors": ["positive_breadth"],
        "bear_factors": [],
        "risks": [],
        "regime_tags": ["bullish"],
        "supporting_metrics": [],
        "contradiction_flags": [],
        "data_quality": {"confidence": 75, "signal_quality": "medium"},
        "warnings": [],
        "source_status": {},
        "pillar_scores": [],
        "detail_sections": {},
        "engine_status": status,
        "status_detail": {"normalization_source": "test_stub"},
    }


class _StubService:
    """Generic engine service stub that responds to any ``get_*`` call."""

    def __init__(
        self,
        engine_key: str,
        status: str = "ok",
        fail: bool = False,
    ) -> None:
        self._engine_key = engine_key
        self._status = status
        self._fail = fail

    def __getattr__(self, name: str):
        if name.startswith("get_"):
            return self._get_analysis
        raise AttributeError(name)

    async def _get_analysis(self, *, force: bool = False):
        if self._fail:
            raise RuntimeError(f"{self._engine_key} stub failure")
        return {"normalized": _make_stub_normalized(self._engine_key, self._status)}


class _StubMarketContextService:
    """Stub for MarketContextService."""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    async def get_market_context(self) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("MarketContextService unavailable")
        return {
            "vix": {
                "value": 18.5,
                "source": "tradier",
                "freshness": "intraday",
                "is_intraday": True,
                "observation_date": "2026-03-16",
                "fetched_at": _STUB_TS,
            },
            "ten_year_yield": {
                "value": 4.25,
                "source": "fred",
                "freshness": "eod",
                "is_intraday": False,
                "observation_date": "2026-03-15",
                "fetched_at": _STUB_TS,
            },
            "two_year_yield": {
                "value": 4.05,
                "source": "fred",
                "freshness": "eod",
                "is_intraday": False,
                "observation_date": "2026-03-15",
                "fetched_at": _STUB_TS,
            },
            "fed_funds_rate": {
                "value": 5.33,
                "source": "fred",
                "freshness": "eod",
                "is_intraday": False,
                "observation_date": "2026-03-15",
                "fetched_at": _STUB_TS,
            },
            "oil_wti": {
                "value": 72.5,
                "source": "fred",
                "freshness": "eod",
                "is_intraday": False,
                "observation_date": "2026-03-15",
                "fetched_at": _STUB_TS,
            },
            "usd_index": {
                "value": 103.2,
                "source": "fred",
                "freshness": "eod",
                "is_intraday": False,
                "observation_date": "2026-03-15",
                "fetched_at": _STUB_TS,
            },
            "yield_curve_spread": {
                "value": 0.20,
                "source": "derived",
                "freshness": "eod",
                "is_intraday": False,
                "observation_date": "2026-03-15",
                "fetched_at": _STUB_TS,
            },
            "cpi_yoy": {
                "value": 3.1,
                "source": "fred",
                "freshness": "eod",
                "is_intraday": False,
                "observation_date": "2026-02-28",
                "fetched_at": _STUB_TS,
            },
            "context_generated_at": _STUB_TS,
        }


def _make_deps(
    *,
    context_fail: bool = False,
    failing_engines: set[str] | None = None,
    degraded_engines: set[str] | None = None,
    model_available: bool = False,
    model_fail: bool = False,
) -> MarketIntelligenceDeps:
    """Build a ``MarketIntelligenceDeps`` with configurable behavior."""
    failing = failing_engines or set()
    degraded = degraded_engines or set()

    engine_keys = [
        "breadth_participation",
        "volatility_options",
        "cross_asset_macro",
        "flows_positioning",
        "liquidity_financial_conditions",
        "news_sentiment",
    ]

    def _svc(ek: str) -> _StubService:
        if ek in failing:
            return _StubService(ek, fail=True)
        if ek in degraded:
            return _StubService(ek, status="degraded")
        return _StubService(ek)

    http_client = None
    model_fn = None

    if model_available:
        http_client = object()
        if model_fail:
            async def _model_fail_fn(client, payload, **kw):
                raise RuntimeError("Model endpoint unavailable")
            model_fn = _model_fail_fn
        else:
            async def _model_ok_fn(client, payload, **kw):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({
                                    "executive_summary": "Stub market analysis",
                                    "regime_breakdown": "Neutral regime",
                                    "primary_fit": "Credit spreads",
                                    "avoid_rationale": "High vol strategies",
                                    "change_triggers": "VIX spike above 25",
                                    "confidence_caveats": "Limited data",
                                    "key_drivers": ["breadth", "volatility"],
                                    "confidence": 0.7,
                                }),
                            },
                        },
                    ],
                }
            model_fn = _model_ok_fn

    return MarketIntelligenceDeps(
        market_context_service=_StubMarketContextService(fail=context_fail),
        breadth_service=_svc(engine_keys[0]),
        volatility_options_service=_svc(engine_keys[1]),
        cross_asset_macro_service=_svc(engine_keys[2]),
        flows_positioning_service=_svc(engine_keys[3]),
        liquidity_conditions_service=_svc(engine_keys[4]),
        news_sentiment_service=_svc(engine_keys[5]),
        http_client=http_client,
        model_request_fn=model_fn,
    )


# ═══════════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_successful_run_valid_publication(tmp_path: Path):
    """Happy path: all engines pass → valid market_state artifact + pointer."""
    config = RunnerConfig(data_dir=tmp_path)
    deps = _make_deps()

    result = await run_market_intelligence(config, deps)

    assert result.status == "completed"
    assert result.publication_status == "valid"
    assert result.artifact_filename is not None
    assert result.artifact_filename.startswith("market_state_")
    assert result.error is None

    # Artifact file must exist on disk.
    ms_dir = get_market_state_dir(tmp_path)
    artifact_path = ms_dir / result.artifact_filename
    assert artifact_path.is_file()

    # Pointer must exist.
    pointer_path = ms_dir / POINTER_FILENAME
    assert pointer_path.is_file()

    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["artifact_filename"] == result.artifact_filename
    assert pointer["status"] == "valid"


@pytest.mark.asyncio
async def test_artifact_conforms_to_contract(tmp_path: Path):
    """Published artifact has all 15 required top-level keys and passes validation."""
    config = RunnerConfig(data_dir=tmp_path)
    deps = _make_deps()

    result = await run_market_intelligence(config, deps)
    assert result.status == "completed"

    ms_dir = get_market_state_dir(tmp_path)
    artifact = json.loads(
        (ms_dir / result.artifact_filename).read_text(encoding="utf-8"),
    )

    for key in REQUIRED_TOP_LEVEL_KEYS:
        assert key in artifact, f"Missing required key: {key}"

    validation = validate_market_state(artifact)
    assert validation.is_valid, (
        f"Validation failed: missing={validation.missing_keys}, "
        f"invalid={validation.invalid_sections}"
    )

    # Lineage must be present and correct.
    assert artifact["lineage"]["workflow_id"] == "market_intelligence"
    assert artifact["lineage"]["run_id"] == result.run_id

    # Consumer summary must have required fields.
    cs = artifact["consumer_summary"]
    assert "market_state" in cs
    assert "vix" in cs
    assert "is_degraded" in cs
    assert cs["is_degraded"] is False  # valid run


@pytest.mark.asyncio
async def test_degraded_engines_produce_degraded_state(tmp_path: Path):
    """Some engines fail/degrade → degraded publication, but pointer IS updated."""
    config = RunnerConfig(data_dir=tmp_path)
    deps = _make_deps(
        failing_engines={"breadth_participation", "news_sentiment"},
        degraded_engines={"flows_positioning"},
    )

    result = await run_market_intelligence(config, deps)

    assert result.status == "completed"
    assert result.publication_status == "degraded"

    # Pointer must be updated (degraded is consumable).
    ms_dir = get_market_state_dir(tmp_path)
    pointer_path = ms_dir / POINTER_FILENAME
    assert pointer_path.is_file()

    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["status"] == "degraded"

    # Quality in artifact must reflect degradation.
    artifact = json.loads(
        (ms_dir / result.artifact_filename).read_text(encoding="utf-8"),
    )
    assert artifact["quality"]["engines_failed"] >= 2
    assert artifact["consumer_summary"]["is_degraded"] is True


@pytest.mark.asyncio
async def test_all_engines_fail_no_valid_publish(tmp_path: Path):
    """All 6 engines fail → artifact written for diagnostics, pointer NOT updated."""
    config = RunnerConfig(data_dir=tmp_path)
    all_engines = {
        "breadth_participation",
        "volatility_options",
        "cross_asset_macro",
        "flows_positioning",
        "liquidity_financial_conditions",
        "news_sentiment",
    }
    deps = _make_deps(failing_engines=all_engines)

    result = await run_market_intelligence(config, deps)

    assert result.status == "completed"
    assert result.publication_status == "failed"

    # Artifact must still be written (for diagnostics).
    ms_dir = get_market_state_dir(tmp_path)
    assert result.artifact_filename is not None
    artifact_path = ms_dir / result.artifact_filename
    assert artifact_path.is_file()

    # Pointer must NOT be updated.
    pointer_path = ms_dir / POINTER_FILENAME
    assert not pointer_path.is_file()


@pytest.mark.asyncio
async def test_model_interpretation_skipped_when_no_model(tmp_path: Path):
    """Without model endpoint, model_interpretation.status=skipped, run still valid."""
    config = RunnerConfig(data_dir=tmp_path)
    deps = _make_deps(model_available=False)

    result = await run_market_intelligence(config, deps)

    assert result.status == "completed"
    assert result.publication_status == "valid"

    ms_dir = get_market_state_dir(tmp_path)
    artifact = json.loads(
        (ms_dir / result.artifact_filename).read_text(encoding="utf-8"),
    )
    mi = artifact["model_interpretation"]
    assert mi is not None
    assert mi["status"] == "skipped"


@pytest.mark.asyncio
async def test_model_interpretation_failure_honest(tmp_path: Path):
    """Model fails → model_interpretation.status=failed, run degrades but completes."""
    config = RunnerConfig(data_dir=tmp_path)
    deps = _make_deps(model_available=True, model_fail=True)

    result = await run_market_intelligence(config, deps)

    assert result.status == "completed"
    # Model failure degrades the publication status.
    assert result.publication_status == "degraded"

    ms_dir = get_market_state_dir(tmp_path)
    artifact = json.loads(
        (ms_dir / result.artifact_filename).read_text(encoding="utf-8"),
    )
    mi = artifact["model_interpretation"]
    assert mi["status"] == "failed"


@pytest.mark.asyncio
async def test_collect_inputs_failure_aborts_run(tmp_path: Path):
    """If market context service fails, run aborts — no artifacts written."""
    config = RunnerConfig(data_dir=tmp_path)
    deps = _make_deps(context_fail=True)

    result = await run_market_intelligence(config, deps)

    assert result.status == "failed"
    assert result.error is not None
    assert "collect_inputs" in result.error

    # No artifact should have been written.
    ms_dir = get_market_state_dir(tmp_path)
    assert result.artifact_filename is None
    assert not ms_dir.exists() or not any(ms_dir.iterdir())


@pytest.mark.asyncio
async def test_all_stages_recorded_in_result(tmp_path: Path):
    """All 6 MI stages appear in result.stages."""
    config = RunnerConfig(data_dir=tmp_path)
    deps = _make_deps()

    result = await run_market_intelligence(config, deps)

    assert len(result.stages) == len(MI_STAGES)
    recorded_keys = [s["stage_key"] for s in result.stages]
    assert recorded_keys == list(MI_STAGES)

    # Each stage must have at least status and started_at.
    for stage in result.stages:
        assert "status" in stage
        assert "started_at" in stage


@pytest.mark.asyncio
async def test_run_result_has_expected_structure(tmp_path: Path):
    """RunResult.to_dict() has all expected fields."""
    config = RunnerConfig(data_dir=tmp_path)
    deps = _make_deps()

    result = await run_market_intelligence(config, deps)
    d = result.to_dict()

    expected_keys = {
        "run_id",
        "workflow_id",
        "status",
        "publication_status",
        "started_at",
        "completed_at",
        "artifact_filename",
        "artifact_path",
        "stages",
        "warnings",
        "error",
    }
    assert set(d.keys()) == expected_keys
    assert d["workflow_id"] == "market_intelligence"
    assert d["run_id"].startswith("run_")
    assert d["started_at"] != ""
    assert d["completed_at"] != ""
