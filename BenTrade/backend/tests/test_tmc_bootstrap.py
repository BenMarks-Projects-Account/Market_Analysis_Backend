"""Prompt 10.5 — TMC bootstrap wiring tests.

Validates that:
1. build_tmc_stock_deps() returns a valid StockOpportunityDeps
2. build_tmc_options_deps() returns a valid OptionsOpportunityDeps
3. App state has tmc_stock_deps and tmc_options_deps after startup
4. TMC routes no longer return "dependencies not configured" when deps are wired

Run with:
    cd BenTrade/backend
    python -m pytest tests/test_tmc_bootstrap.py -v --tb=short
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_tmc import router
from app.workflows.tmc_bootstrap import build_tmc_stock_deps, build_tmc_options_deps


# ═══════════════════════════════════════════════════════════════════
# STUBS
# ═══════════════════════════════════════════════════════════════════


def _stub_stock_engine_service() -> MagicMock:
    """Minimal mock for StockEngineService."""
    svc = MagicMock()
    svc.scan = AsyncMock(return_value={
        "candidates": [],
        "scanner_counts": {},
        "warnings": [],
    })
    return svc


def _stub_base_data_service() -> MagicMock:
    """Minimal mock for BaseDataService."""
    svc = MagicMock()
    svc.tradier_client = MagicMock()
    svc.tradier_client.get_expirations = AsyncMock(return_value=[])
    svc.get_underlying_price = AsyncMock(return_value=500.0)
    svc.get_analysis_inputs = AsyncMock(return_value={"contracts": []})
    return svc


# ═══════════════════════════════════════════════════════════════════
# 1. DEPENDENCY BUILDER TESTS
# ═══════════════════════════════════════════════════════════════════


class TestStockDepsBuilder:
    """build_tmc_stock_deps() should return a StockOpportunityDeps."""

    def test_returns_deps_with_engine(self):
        engine = _stub_stock_engine_service()
        deps = build_tmc_stock_deps(stock_engine_service=engine)

        from app.workflows.stock_opportunity_runner import StockOpportunityDeps
        assert isinstance(deps, StockOpportunityDeps)
        assert deps.stock_engine_service is engine

    def test_deps_not_none(self):
        engine = _stub_stock_engine_service()
        deps = build_tmc_stock_deps(stock_engine_service=engine)
        assert deps is not None


class TestOptionsDepsBuilder:
    """build_tmc_options_deps() should return an OptionsOpportunityDeps."""

    def test_returns_deps_with_scanner_service(self):
        bds = _stub_base_data_service()
        deps = build_tmc_options_deps(base_data_service=bds)

        from app.workflows.options_opportunity_runner import OptionsOpportunityDeps
        assert isinstance(deps, OptionsOpportunityDeps)
        assert deps.options_scanner_service is not None

    def test_scanner_service_has_scan_method(self):
        bds = _stub_base_data_service()
        deps = build_tmc_options_deps(base_data_service=bds)
        assert hasattr(deps.options_scanner_service, "scan")
        assert callable(deps.options_scanner_service.scan)


# ═══════════════════════════════════════════════════════════════════
# 2. APP STATE WIRING TESTS
# ═══════════════════════════════════════════════════════════════════


class TestAppStateWiring:
    """TMC deps should be accessible on app.state after wiring."""

    @pytest.fixture
    def wired_app(self, tmp_path):
        """Build a FastAPI app with TMC deps wired into state."""
        app = FastAPI()
        app.include_router(router)
        app.state.backend_dir = tmp_path

        # Wire deps exactly as main.py does
        stock_engine = _stub_stock_engine_service()
        bds = _stub_base_data_service()

        app.state.tmc_stock_deps = build_tmc_stock_deps(
            stock_engine_service=stock_engine,
        )
        app.state.tmc_options_deps = build_tmc_options_deps(
            base_data_service=bds,
        )
        return app

    def test_stock_deps_on_state(self, wired_app):
        assert hasattr(wired_app.state, "tmc_stock_deps")
        assert wired_app.state.tmc_stock_deps is not None

    def test_options_deps_on_state(self, wired_app):
        assert hasattr(wired_app.state, "tmc_options_deps")
        assert wired_app.state.tmc_options_deps is not None

    def test_deps_are_correct_types(self, wired_app):
        from app.workflows.stock_opportunity_runner import StockOpportunityDeps
        from app.workflows.options_opportunity_runner import OptionsOpportunityDeps

        assert isinstance(wired_app.state.tmc_stock_deps, StockOpportunityDeps)
        assert isinstance(wired_app.state.tmc_options_deps, OptionsOpportunityDeps)


# ═══════════════════════════════════════════════════════════════════
# 3. TMC ROUTE NO LONGER RETURNS "dependencies not configured"
# ═══════════════════════════════════════════════════════════════════


class TestTMCRouteDepsConfigured:
    """When deps are wired, the trigger endpoints should NOT return
    the 'dependencies not configured' error.
    """

    @pytest.fixture
    def wired_client(self, tmp_path):
        app = FastAPI()
        app.include_router(router)
        app.state.backend_dir = tmp_path

        stock_engine = _stub_stock_engine_service()
        bds = _stub_base_data_service()

        app.state.tmc_stock_deps = build_tmc_stock_deps(
            stock_engine_service=stock_engine,
        )
        app.state.tmc_options_deps = build_tmc_options_deps(
            base_data_service=bds,
        )
        return TestClient(app)

    @pytest.fixture
    def unwired_client(self, tmp_path):
        """App WITHOUT TMC deps — should still return the error."""
        app = FastAPI()
        app.include_router(router)
        app.state.backend_dir = tmp_path
        return TestClient(app)

    def test_unwired_stock_returns_deps_error(self, unwired_client):
        """Baseline: unwired app should return the deps error."""
        resp = unwired_client.post("/api/tmc/workflows/stock/run")
        body = resp.json()
        assert body["status"] == "failed"
        assert "not configured" in (body.get("error") or "")

    def test_unwired_options_returns_deps_error(self, unwired_client):
        resp = unwired_client.post("/api/tmc/workflows/options/run")
        body = resp.json()
        assert body["status"] == "failed"
        assert "not configured" in (body.get("error") or "")

    def test_wired_stock_no_deps_error(self, wired_client):
        """With deps wired, trigger should NOT return 'not configured'.
        It may fail for other reasons (no market state, etc.) but the
        deps check should pass.
        """
        resp = wired_client.post("/api/tmc/workflows/stock/run")
        body = resp.json()
        error_msg = body.get("error") or ""
        assert "not configured" not in error_msg

    def test_wired_options_no_deps_error(self, wired_client):
        resp = wired_client.post("/api/tmc/workflows/options/run")
        body = resp.json()
        error_msg = body.get("error") or ""
        assert "not configured" not in error_msg


# ═══════════════════════════════════════════════════════════════════
# 4. OPTIONS SCANNER SERVICE UNIT TESTS
# ═══════════════════════════════════════════════════════════════════


class TestOptionsScannerService:
    """Validate the OptionsScannerService adapter behaves correctly."""

    @pytest.mark.asyncio
    async def test_scan_returns_expected_shape(self):
        from app.services.options_scanner_service import OptionsScannerService

        bds = _stub_base_data_service()
        svc = OptionsScannerService(base_data_service=bds)

        result = await svc.scan(
            symbols=["SPY"],
            scanner_keys=["put_credit_spread"],
            context={},
        )

        assert "scan_results" in result
        assert "warnings" in result
        assert "scanners_total" in result
        assert "scanners_ok" in result
        assert "scanners_failed" in result
        assert isinstance(result["scan_results"], list)

    @pytest.mark.asyncio
    async def test_scan_with_no_expirations_returns_empty(self):
        from app.services.options_scanner_service import OptionsScannerService

        bds = _stub_base_data_service()
        bds.tradier_client.get_expirations = AsyncMock(return_value=[])
        svc = OptionsScannerService(base_data_service=bds)

        result = await svc.scan(
            symbols=["SPY"],
            scanner_keys=["put_credit_spread"],
        )

        assert result["scanners_total"] == 1
        assert result["scanners_ok"] == 1
        assert result["scanners_failed"] == 0
        # Empty result (no expirations → no chain → empty candidates)
        assert len(result["scan_results"]) == 1
        assert result["scan_results"][0]["candidates"] == []

    @pytest.mark.asyncio
    async def test_scan_unsupported_key_warns(self):
        from app.services.options_scanner_service import OptionsScannerService

        bds = _stub_base_data_service()
        svc = OptionsScannerService(base_data_service=bds)

        result = await svc.scan(
            symbols=["SPY"],
            scanner_keys=["nonexistent_scanner"],
        )

        assert result["scanners_total"] == 0
        assert len(result["warnings"]) > 0
        assert "nonexistent_scanner" in result["warnings"][0]
