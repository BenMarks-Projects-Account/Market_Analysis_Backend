"""Tests for portfolio_balancing_runner workflow."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.workflows.portfolio_balancing_runner import (
    run_portfolio_balance_workflow,
    _elapsed_ms,
    _resolve_settings,
    _resolve_regime_service,
    _build_portfolio_state,
)


def _run(coro):
    return asyncio.run(coro)


# ─── Helpers ───

def _make_active_results(recs=None):
    return {"recommendations": recs or [], "ok": True}


def _make_close_rec(symbol="SPY"):
    return {
        "symbol": symbol,
        "strategy": "put_credit_spread",
        "recommendation": "CLOSE",
        "conviction": 80,
        "trade_health_score": 30,
        "rationale_summary": "Deteriorated",
        "position_snapshot": {"max_loss": 300, "cost_basis_total": 300},
        "live_greeks": {"trade_delta": 0.15, "trade_gamma": 0.01, "trade_theta": -2, "trade_vega": 0.5},
        "suggested_close_order": {"ready_for_preview": True},
    }


def _make_hold_rec(symbol="IWM"):
    return {
        "symbol": symbol,
        "strategy": "put_credit_spread",
        "recommendation": "HOLD",
        "conviction": 70,
        "trade_health_score": 75,
        "position_snapshot": {"max_loss": 250},
        "live_greeks": {"trade_delta": 0.1, "trade_gamma": 0.005, "trade_theta": -1, "trade_vega": 0.3},
    }


def _make_options_candidate(symbol="QQQ"):
    return {
        "symbol": symbol,
        "scanner_key": "put_credit_spread",
        "math": {"max_loss": -20000, "ev": 45, "ror": 0.12, "pop": 72, "max_profit": 8000},
        "regime_alignment": "aligned",
        "rank": 1,
    }


# ─── Unit tests for helpers ───

class TestElapsedMs:
    def test_returns_int(self):
        import time
        s = time.time()
        result = _elapsed_ms(s)
        assert isinstance(result, int)
        assert result >= 0


class TestResolveSettings:
    def test_from_request(self):
        mock_settings = MagicMock()
        mock_request = MagicMock()
        mock_request.app.state.trading_service.settings = mock_settings
        assert _resolve_settings(mock_request) is mock_settings

    def test_fallback_no_request(self):
        with patch("app.config.get_settings") as mock_gs:
            mock_gs.return_value = {"test": True}
            result = _resolve_settings(None)
            mock_gs.assert_called_once()


class TestResolveRegimeService:
    def test_from_request(self):
        mock_rs = MagicMock()
        mock_request = MagicMock()
        mock_request.app.state.regime_service = mock_rs
        assert _resolve_regime_service(mock_request) is mock_rs

    def test_none_when_no_request(self):
        assert _resolve_regime_service(None) is None


class TestBuildPortfolioState:
    def test_empty_recommendations(self):
        greeks, conc = _build_portfolio_state({"recommendations": []}, {})
        assert greeks["delta"] == 0
        assert "by_underlying" in conc

    def test_with_recommendations(self):
        recs = [_make_hold_rec("SPY"), _make_close_rec("QQQ")]
        greeks, conc = _build_portfolio_state({"recommendations": recs}, {"equity": 50000})
        # Should produce valid structures (actual values depend on portfolio_risk_engine)
        assert isinstance(greeks, dict)
        assert isinstance(conc, dict)


# ─── Integration: run_portfolio_balance_workflow ───

class TestWorkflowWithPrecomputedResults:
    """When all sub-results are pre-provided, the workflow skips pipelines."""

    def test_precomputed_all(self):
        active = _make_active_results([_make_close_rec("SPY"), _make_hold_rec("IWM")])
        options = {"candidates": [_make_options_candidate("QQQ")]}
        stock = {"candidates": []}

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("no creds in test"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
                stock_results=stock,
                options_results=options,
            ))

        # Should still produce a plan (account fetch errors are non-fatal)
        assert "rebalance_plan" in result
        assert "stages" in result
        assert "errors" in result
        assert result["account_mode"] == "paper"
        assert result["run_id"].startswith("pb_")

    def test_active_trade_summary_populated(self):
        active = _make_active_results([_make_close_rec("SPY"), _make_hold_rec("IWM")])

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("skip"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
                stock_results={"candidates": []},
                options_results={"candidates": []},
            ))

        summary = result["active_trade_summary"]
        assert summary["total"] == 2
        assert summary["close"] == 1  # SPY CLOSE
        assert summary["hold"] == 1   # IWM HOLD


class TestWorkflowStageTracking:
    """Every stage has timing and status."""

    def test_all_stages_present(self):
        active = _make_active_results([])

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("skip"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
                stock_results={"candidates": []},
                options_results={"candidates": []},
            ))

        stages = result["stages"]
        expected_stages = [
            "account_state", "regime", "risk_policy",
            "active_trades", "stock_candidates", "options_candidates",
            "portfolio_state", "portfolio_balance",
        ]
        for stage_name in expected_stages:
            assert stage_name in stages, f"Missing stage: {stage_name}"
            assert "duration_ms" in stages[stage_name] or "error" in stages[stage_name]

    def test_duration_ms_is_nonneg(self):
        active = _make_active_results([])

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("skip"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
            ))

        assert result["duration_ms"] >= 0


class TestWorkflowErrorResilience:
    """Failures in one stage don't crash the workflow."""

    def test_account_fetch_failure_nonfatal(self):
        active = _make_active_results([_make_hold_rec()])

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=RuntimeError("No credentials"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
                stock_results={"candidates": []},
                options_results={"candidates": []},
            ))

        # Errors logged but plan still produced
        assert len(result["errors"]) >= 1
        assert "Account state" in result["errors"][0]
        assert result["rebalance_plan"] is not None

    def test_regime_failure_nonfatal(self):
        active = _make_active_results([])

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("skip"),
        ), patch(
            "app.workflows.portfolio_balancing_runner._resolve_regime_service",
            return_value=None,
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
            ))

        assert result["regime_label"] is None
        assert result["rebalance_plan"] is not None


class TestWorkflowOutputShape:
    """Full output contains all required keys."""

    def test_all_top_level_keys(self):
        active = _make_active_results([])

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("skip"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
            ))

        required_keys = [
            "ok", "run_id", "account_mode", "timestamp",
            "duration_ms", "account_equity", "regime_label",
            "rebalance_plan", "active_trade_summary",
            "risk_policy", "stages", "errors",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_risk_policy_included(self):
        active = _make_active_results([])

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("skip"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
            ))

        rp = result["risk_policy"]
        assert isinstance(rp, dict)
        assert "max_risk_per_trade" in rp


class TestWorkflowCandidateStages:
    """Candidate stages report correct status."""

    def test_provided_status(self):
        active = _make_active_results([])

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("skip"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
                stock_results={"candidates": [{"symbol": "AAPL"}]},
                options_results={"candidates": [{"symbol": "SPY"}]},
            ))

        assert result["stages"]["stock_candidates"]["status"] == "provided"
        assert result["stages"]["stock_candidates"]["count"] == 1
        assert result["stages"]["options_candidates"]["status"] == "provided"
        assert result["stages"]["options_candidates"]["count"] == 1

    def test_skipped_status(self):
        active = _make_active_results([])

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("skip"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
            ))

        assert result["stages"]["stock_candidates"]["status"] == "skipped"
        assert result["stages"]["options_candidates"]["status"] == "skipped"


class TestWorkflowNoRequest:
    """Workflow works without a request object (for testing/standalone)."""

    def test_no_request_still_produces_plan(self):
        active = _make_active_results([_make_hold_rec()])

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("skip"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
                stock_results={"candidates": []},
                options_results={"candidates": []},
            ))

        assert result["rebalance_plan"] is not None
        assert result["rebalance_plan"]["hold_positions"][0]["symbol"] == "IWM"


class TestWorkflowEnvelopeUnwrap:
    """Runner unwraps {status, data: {candidates}} envelope from /latest responses."""

    def test_stock_envelope_unwrapped(self):
        active = _make_active_results([])
        # Simulate response shape from /api/tmc/workflows/stock/latest
        stock_envelope = {"status": "completed", "data": {"candidates": [{"symbol": "AAPL"}]}}

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("skip"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
                stock_results=stock_envelope,
            ))

        assert result["stages"]["stock_candidates"]["count"] == 1

    def test_options_envelope_unwrapped(self):
        active = _make_active_results([])
        # Simulate response shape from /api/tmc/workflows/options/latest
        opts_envelope = {"status": "completed", "data": {"candidates": [{"symbol": "SPY"}]}}

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("skip"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
                options_results=opts_envelope,
            ))

        assert result["stages"]["options_candidates"]["count"] == 1

    def test_flat_results_still_work(self):
        """Direct {candidates: [...]} format continues to work."""
        active = _make_active_results([])
        flat = {"candidates": [{"symbol": "QQQ"}, {"symbol": "DIA"}]}

        with patch(
            "app.trading.tradier_credentials.get_tradier_context",
            side_effect=Exception("skip"),
        ):
            result = _run(run_portfolio_balance_workflow(
                account_mode="paper",
                active_trade_results=active,
                stock_results=flat,
            ))

        assert result["stages"]["stock_candidates"]["count"] == 2
