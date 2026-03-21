"""Targeted test: OptionContract → dict conversion in OptionsScannerService.

Verifies that the scanner service correctly converts Pydantic OptionContract
objects to raw dicts before passing them into V2 scanners, which expect
dict.get()-style access.

This test covers the specific bug where all 44 scanners failed with:
    'OptionContract' object has no attribute 'get'
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import OptionContract
from app.services.options_scanner_service import OptionsScannerService


# ── Fixture: minimal OptionContract objects ──────────────────────────

def _make_option_contract(**overrides: Any) -> OptionContract:
    defaults = {
        "option_type": "put",
        "strike": 560.0,
        "expiration": "2026-04-17",
        "bid": 2.50,
        "ask": 2.70,
        "open_interest": 1200,
        "volume": 340,
        "delta": -0.25,
        "iv": 0.22,
        "symbol": "SPY260417P00560000",
    }
    defaults.update(overrides)
    return OptionContract(**defaults)


def _make_scan_result_mock(passed: int = 1, rejected: int = 0) -> MagicMock:
    """Build a mock V2 scanner result with to_dict()."""
    result = MagicMock()
    result.to_dict.return_value = {
        "scanner_key": "put_credit_spread",
        "strategy_id": "put_credit_spread",
        "family_key": "vertical_spreads",
        "symbol": "SPY",
        "candidates": [],
        "rejected": [],
        "total_constructed": passed + rejected,
        "total_passed": passed,
        "total_rejected": rejected,
        "reject_reason_counts": {},
        "warning_counts": {},
        "phase_counts": [],
        "elapsed_ms": 10.0,
    }
    return result


# ── Tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_option_contracts_converted_to_dicts():
    """Pydantic OptionContract objects must be converted to dicts
    before being passed to V2 scanners as chain data.

    This is the root-cause regression test for the
    'OptionContract object has no attribute get' failure.
    """
    contracts = [
        _make_option_contract(strike=555.0),
        _make_option_contract(strike=560.0, option_type="call"),
    ]

    # Mock base_data_service
    mock_bds = MagicMock()
    mock_bds.tradier_client = MagicMock()
    mock_bds.tradier_client.get_expirations = AsyncMock(return_value=["2026-04-17"])
    mock_bds.get_underlying_price = AsyncMock(return_value=565.0)
    mock_bds.get_analysis_inputs = AsyncMock(return_value={
        "underlying_price": 565.0,
        "contracts": contracts,  # Pydantic objects, not dicts
        "prices_history": [],
        "vix": 20.0,
        "notes": [],
    })

    # Mock V2 scanner
    mock_scanner = MagicMock()
    mock_scan_result = _make_scan_result_mock()
    mock_scanner.run = MagicMock(return_value=mock_scan_result)

    service = OptionsScannerService(base_data_service=mock_bds)

    with patch("app.services.scanner_v2.registry.is_v2_supported", return_value=True), \
         patch("app.services.scanner_v2.registry.get_v2_family") as mock_family, \
         patch("app.services.scanner_v2.registry.get_v2_scanner", return_value=mock_scanner):

        mock_family.return_value = MagicMock(family_key="vertical_spreads")

        result = await service.scan(
            symbols=["SPY"],
            scanner_keys=["put_credit_spread"],
        )

    # Scanner should have succeeded, not failed
    assert result["scanners_ok"] == 1, f"Scanner should succeed, got: {result}"
    assert result["scanners_failed"] == 0

    # Verify the chain passed to scanner.run contains dicts, not OptionContract
    call_kwargs = mock_scanner.run.call_args
    chain_arg = call_kwargs.kwargs.get("chain") or call_kwargs[1].get("chain")
    option_list = chain_arg["options"]["option"]

    for item in option_list:
        assert isinstance(item, dict), (
            f"Expected dict in chain, got {type(item).__name__}. "
            "OptionContract objects must be converted to dicts before scanner."
        )
        # Verify key fields survived conversion
        assert "strike" in item
        assert "option_type" in item
        assert "bid" in item


@pytest.mark.asyncio
async def test_already_dict_contracts_pass_through():
    """If contracts are already dicts (e.g. from snapshot), they pass through unchanged."""
    contracts = [
        {"option_type": "put", "strike": 555.0, "expiration": "2026-04-17",
         "bid": 2.5, "ask": 2.7, "symbol": "SPY260417P00555000"},
    ]

    mock_bds = MagicMock()
    mock_bds.tradier_client = MagicMock()
    mock_bds.tradier_client.get_expirations = AsyncMock(return_value=["2026-04-17"])
    mock_bds.get_underlying_price = AsyncMock(return_value=565.0)
    mock_bds.get_analysis_inputs = AsyncMock(return_value={
        "underlying_price": 565.0,
        "contracts": contracts,
        "prices_history": [],
        "vix": 20.0,
        "notes": [],
    })

    mock_scanner = MagicMock()
    mock_scanner.run = MagicMock(return_value=_make_scan_result_mock())

    service = OptionsScannerService(base_data_service=mock_bds)

    with patch("app.services.scanner_v2.registry.is_v2_supported", return_value=True), \
         patch("app.services.scanner_v2.registry.get_v2_family") as mock_family, \
         patch("app.services.scanner_v2.registry.get_v2_scanner", return_value=mock_scanner):

        mock_family.return_value = MagicMock(family_key="vertical_spreads")

        result = await service.scan(
            symbols=["SPY"],
            scanner_keys=["put_credit_spread"],
        )

    assert result["scanners_ok"] == 1
    assert result["scanners_failed"] == 0

    # Verify dicts passed through
    call_kwargs = mock_scanner.run.call_args
    chain_arg = call_kwargs.kwargs.get("chain") or call_kwargs[1].get("chain")
    option_list = chain_arg["options"]["option"]
    assert all(isinstance(item, dict) for item in option_list)


def test_extract_scan_diagnostics_includes_narrowing_and_phases():
    """The _extract_scan_diagnostics function must include narrowing
    diagnostics and phase counts in family_summaries for debug visibility."""
    from app.workflows.options_opportunity_runner import _extract_scan_diagnostics

    scan_results = [
        {
            "scanner_key": "put_credit_spread",
            "family_key": "vertical_spreads",
            "symbol": "SPY",
            "total_constructed": 50,
            "total_passed": 12,
            "total_rejected": 38,
            "reject_reason_counts": {"v2_quote_missing": 10, "v2_math_nan": 28},
            "warning_counts": {"wide_spread": 5},
            "phase_counts": [
                {"phase": "constructed", "remaining": 50},
                {"phase": "structural_validation", "remaining": 48},
                {"phase": "quote_liquidity_sanity", "remaining": 20},
                {"phase": "recomputed_math", "remaining": 12},
            ],
            "narrowing_diagnostics": {
                "total_contracts_loaded": 800,
                "expirations_kept": 6,
                "expirations_dropped": 14,
                "contracts_final": 200,
                "contracts_missing_bid": 5,
                "contracts_missing_ask": 3,
                "contracts_missing_delta": 10,
            },
            "elapsed_ms": 120.0,
        },
    ]

    diag = _extract_scan_diagnostics(scan_results)

    assert diag["total_constructed"] == 50
    assert diag["total_passed"] == 12
    assert diag["total_rejected"] == 38
    assert diag["reject_reason_counts"]["v2_quote_missing"] == 10

    # Family summary must include narrowing and phase_counts
    fam = diag["family_summaries"][0]
    assert fam["scanner_key"] == "put_credit_spread"
    assert fam["total_constructed"] == 50
    assert fam["reject_reason_counts"]["v2_math_nan"] == 28
    assert len(fam["phase_counts"]) == 4
    assert fam["narrowing"]["contracts_loaded"] == 800
    assert fam["narrowing"]["expirations_kept"] == 6
    assert fam["narrowing"]["contracts_final"] == 200
    assert fam["narrowing"]["missing_bid"] == 5


# ── Credibility gate tests ───────────────────────────────────────────

def _make_enrichment_candidate(
    *,
    symbol: str = "SPY",
    net_credit: float | None = 0.50,
    net_debit: float | None = None,
    pop: float = 0.72,
    ev: float = 12.0,
    ror: float = 1.0,
    leg_bids: tuple[float, ...] = (1.20, 0.55),
    leg_deltas: tuple[float, ...] = (-0.28, -0.18),
) -> dict[str, Any]:
    """Build a candidate dict suitable for _stage_enrich_evaluate."""
    return {
        "candidate_id": f"{symbol}|test|2026-04-17|400/395|001",
        "scanner_key": "put_credit_spread",
        "strategy_id": "put_credit_spread",
        "family_key": "vertical_spreads",
        "symbol": symbol,
        "underlying_price": 450.0,
        "expiration": "2026-04-17",
        "dte": 28,
        "legs": [
            {"strike": 400.0, "side": "short", "option_type": "put",
             "bid": leg_bids[0], "ask": leg_bids[0] + 0.15, "delta": leg_deltas[0]},
            {"strike": 395.0, "side": "long", "option_type": "put",
             "bid": leg_bids[1], "ask": leg_bids[1] + 0.15, "delta": leg_deltas[1]},
        ],
        "math": {
            "net_credit": net_credit,
            "net_debit": net_debit,
            "max_profit": 50.0,
            "max_loss": 450.0,
            "width": 5.0,
            "pop": pop,
            "pop_source": "delta_approx",
            "ev": ev,
            "ev_per_day": ev / 28,
            "ror": ror,
        },
        "diagnostics": {"structural_checks": [], "quote_checks": [],
                        "liquidity_checks": [], "math_checks": [],
                        "reject_reasons": [], "warnings": [], "pass_reasons": []},
        "passed": True,
        "downstream_usable": True,
    }


def test_credibility_gate_rejects_penny_premium():
    """Candidates with both net_credit and net_debit < $0.05 should be rejected."""
    from app.workflows.options_opportunity_runner import _stage_enrich_evaluate

    penny = _make_enrichment_candidate(net_credit=None, net_debit=0.01, pop=0.5, leg_bids=(0.01, 0.0))
    credible = _make_enrichment_candidate(net_credit=0.50, pop=0.72)

    stage_data = {
        "validated_candidates": [penny, credible],
        "market_state_ref": "test_ref",
        "consumer_summary": {"market_state": "neutral", "stability_state": "calm"},
    }
    outcome = _stage_enrich_evaluate(stage_data, [])
    assert outcome.status == "completed"

    enriched = stage_data["enriched_candidates"]
    assert len(enriched) == 1
    assert enriched[0]["symbol"] == "SPY"  # credible one passed
    assert stage_data["credibility_filter"]["rejected_count"] == 1
    assert "penny_premium" in stage_data["credibility_filter"]["rejection_reasons"]


def test_credibility_gate_rejects_zero_delta_short():
    """Candidates with pop >= 0.995 (delta=0 on short) should be rejected."""
    from app.workflows.options_opportunity_runner import _stage_enrich_evaluate

    garbage = _make_enrichment_candidate(net_credit=None, net_debit=0.10, pop=1.0, leg_bids=(0.05, 0.0), leg_deltas=(0.0, 0.0))
    credible = _make_enrichment_candidate(net_credit=0.50, pop=0.72)

    stage_data = {
        "validated_candidates": [garbage, credible],
        "market_state_ref": "test_ref",
        "consumer_summary": {},
    }
    outcome = _stage_enrich_evaluate(stage_data, [])
    assert outcome.status == "completed"
    assert len(stage_data["enriched_candidates"]) == 1
    assert "zero_delta_short" in stage_data["credibility_filter"]["rejection_reasons"]


def test_credibility_gate_rejects_all_legs_zero_bid():
    """Candidates where all legs have bid=0 should be rejected as unfillable."""
    from app.workflows.options_opportunity_runner import _stage_enrich_evaluate

    unfillable = _make_enrichment_candidate(net_credit=0.10, pop=0.80, leg_bids=(0.0, 0.0))
    fillable = _make_enrichment_candidate(net_credit=0.50, pop=0.72)

    stage_data = {
        "validated_candidates": [unfillable, fillable],
        "market_state_ref": "test_ref",
        "consumer_summary": {},
    }
    outcome = _stage_enrich_evaluate(stage_data, [])
    assert outcome.status == "completed"
    assert len(stage_data["enriched_candidates"]) == 1
    assert "all_legs_zero_bid" in stage_data["credibility_filter"]["rejection_reasons"]


def test_credibility_gate_passes_credible_trades():
    """All credible trades should pass through the credibility gate."""
    from app.workflows.options_opportunity_runner import _stage_enrich_evaluate

    cands = [
        _make_enrichment_candidate(symbol="SPY", net_credit=0.50, pop=0.72),
        _make_enrichment_candidate(symbol="QQQ", net_credit=1.20, pop=0.65),
        _make_enrichment_candidate(symbol="IWM", net_credit=None, net_debit=2.50, pop=0.45, leg_bids=(3.0, 0.50)),
    ]

    stage_data = {
        "validated_candidates": cands,
        "market_state_ref": "test_ref",
        "consumer_summary": {},
    }
    outcome = _stage_enrich_evaluate(stage_data, [])
    assert outcome.status == "completed"
    assert len(stage_data["enriched_candidates"]) == 3
    cf = stage_data["credibility_filter"]
    assert cf["rejected_count"] == 0
    assert cf["passed_count"] == 3
