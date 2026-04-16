"""Phase 1 smoke tests for the Flows & Positioning engine.

Exercises the public entry ``compute_flows_positioning_scores`` with a
stubbed FMPClient and stubbed LLM interpreter. Validates:

  * Dealer Hedging pillar is marked as deferred.
  * Normalized engine output retains ``pillar_status``.
  * LLM-disabled path yields ``narrative=None``.
  * Total pillar-failure path returns a neutral-unavailable payload
    rather than raising.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.engine_output_contract import normalize_engine_output
from app.services.flows_positioning_engine import compute_flows_positioning_scores


class _StubFMP:
    """Minimal FMPClient stub. Methods called by pillars return empty data.

    Both pillars catch empty-data conditions and return
    unavailable SubSignals, producing a valid composite with
    pillar_status reflecting graceful degradation.
    """

    async def get_commitments_of_traders(self, *args, **kwargs):
        return []

    async def get_historical_price_full(self, *args, **kwargs):
        return []

    async def get_historical_daily_prices(self, *args, **kwargs):
        return []


class _RaisingFMP:
    async def get_commitments_of_traders(self, *args, **kwargs):
        raise RuntimeError("FMP unavailable")

    async def get_historical_price_full(self, *args, **kwargs):
        raise RuntimeError("FMP unavailable")

    async def get_historical_daily_prices(self, *args, **kwargs):
        raise RuntimeError("FMP unavailable")


async def _disabled_llm(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_phase1_smoke_stub_returns_valid_shape():
    result = await compute_flows_positioning_scores(
        _StubFMP(), llm_interpret_fn=_disabled_llm
    )
    assert isinstance(result, dict)
    assert "pillar_status" in result
    assert result["pillar_status"].get("dealer_hedging") == "deferred"
    # LLM was disabled → narrative must be None, not fabricated.
    assert result.get("narrative") is None
    # Pillar scores dict always contains the three canonical keys.
    assert set(result["pillar_scores"].keys()) == {"positioning", "flows", "dealer_hedging"}


@pytest.mark.asyncio
async def test_phase1_smoke_normalizer_preserves_pillar_status():
    engine_result = await compute_flows_positioning_scores(
        _StubFMP(), llm_interpret_fn=_disabled_llm
    )
    payload = {"engine_result": engine_result}
    normalized = normalize_engine_output("flows_positioning", payload)
    # Fix 1: pillar_status must survive normalization.
    assert "pillar_status" in normalized
    assert normalized["pillar_status"].get("dealer_hedging") == "deferred"


@pytest.mark.asyncio
async def test_phase1_smoke_raising_fmp_returns_neutral_unavailable():
    """Even when every FMP call raises, engine must not bubble the exception."""
    result = await compute_flows_positioning_scores(
        _RaisingFMP(), llm_interpret_fn=_disabled_llm
    )
    assert isinstance(result, dict)
    assert "pillar_status" in result
    # Either all unavailable or partial + still-deferred dealer hedging.
    assert result["pillar_status"].get("dealer_hedging") == "deferred"
