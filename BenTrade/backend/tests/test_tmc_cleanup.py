"""Prompt 10 — TMC cleanup / consolidation contract tests.

Validates that the P10 normalization layer and status handling
correctly absorb field-name variation without breaking the
backend contract.  Pure Python tests — no browser, no JS engine.

Run with:
    cd BenTrade/backend
    python -m pytest tests/test_tmc_cleanup.py -v --tb=short
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_tmc import router
from app.workflows.tmc_service import TMCStatus


# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "backend" / "data"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def app(data_dir):
    app = FastAPI()
    app.include_router(router)
    app.state.backend_dir = data_dir.parent
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _write_pointer(data_dir: Path, workflow_id: str, run_id: str, status: str = "completed") -> None:
    pointer_dir = data_dir / "workflows" / workflow_id
    pointer_dir.mkdir(parents=True, exist_ok=True)
    (pointer_dir / "latest.json").write_text(
        json.dumps({
            "run_id": run_id,
            "workflow_id": workflow_id,
            "completed_at": "2025-01-15T18:00:00+00:00",
            "status": status,
            "output_filename": "output.json",
            "contract_version": "1.0",
        }),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════════
# 1. STATUS VOCABULARY — P10 consolidation guarantee
# ═══════════════════════════════════════════════════════════════════


class TestStatusVocabularyP10:
    """JS TMC_STATUS_MAP must cover every TMCStatus value."""

    # The exact strings the JS TMC_STATUS_MAP defines as keys
    JS_STATUS_MAP_KEYS = {"completed", "degraded", "failed", "no_output", "unavailable"}

    def test_backend_statuses_match_js_map(self):
        """Every backend TMCStatus must have a JS mapping."""
        backend_vals = {s.value for s in TMCStatus}
        assert backend_vals == self.JS_STATUS_MAP_KEYS

    def test_no_output_returns_null_data(self, client):
        resp = client.get("/api/tmc/workflows/stock/latest")
        body = resp.json()
        assert body["status"] == "no_output"
        assert body["data"] is None

    def test_no_output_options(self, client):
        resp = client.get("/api/tmc/workflows/options/latest")
        body = resp.json()
        assert body["status"] == "no_output"
        assert body["data"] is None


# ═══════════════════════════════════════════════════════════════════
# 2. STOCK NORMALIZATION — field mapping validation
# ═══════════════════════════════════════════════════════════════════


class TestStockNormalizationContract:
    """Fields the P10 normalizeStockCandidate() reads must be present
    in the backend response.  The JS normalizer absorbs aliases like:
      symbol ← symbol
      action ← action | recommendation
      conviction ← conviction
      rationale ← rationale_summary | rationale
      points ← key_supporting_points
      risks ← key_risks
      strategy ← strategy_type | scanner_key
    """

    STOCK_OUTPUT = {
        "run_id": "run_sn_001",
        "workflow_id": "stock_opportunity",
        "generated_at": "2025-01-15T18:00:00+00:00",
        "publication_status": "completed",
        "total_candidates": 1,
        "selected_count": 1,
        "quality_level": "full",
        "candidates": [
            {
                "symbol": "SPY",
                "action": "buy",
                "conviction": 0.77,
                "rationale_summary": "Trend continuation",
                "key_supporting_points": ["Above 200 SMA"],
                "key_risks": ["Earnings risk"],
                "strategy_type": "trend_follow",
                "scanner_key": "top_trend",
            },
        ],
        "warnings": [],
    }

    def _setup(self, data_dir):
        run_id = "run_sn_001"
        _write_pointer(data_dir, "stock_opportunity", run_id)
        d = data_dir / "workflows" / "stock_opportunity" / run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "output.json").write_text(json.dumps(self.STOCK_OUTPUT), encoding="utf-8")

    def test_primary_fields_present(self, client, data_dir):
        self._setup(data_dir)
        c = client.get("/api/tmc/workflows/stock/latest").json()["data"]["candidates"][0]
        # Primary path for normalizeStockCandidate
        assert "symbol" in c
        assert "action" in c
        assert "conviction" in c
        assert "rationale_summary" in c

    def test_list_fields_are_arrays(self, client, data_dir):
        self._setup(data_dir)
        c = client.get("/api/tmc/workflows/stock/latest").json()["data"]["candidates"][0]
        assert isinstance(c["key_supporting_points"], list)
        assert isinstance(c["key_risks"], list)

    def test_strategy_field_available(self, client, data_dir):
        self._setup(data_dir)
        c = client.get("/api/tmc/workflows/stock/latest").json()["data"]["candidates"][0]
        # normalizer reads strategy_type first, scanner_key as fallback
        assert c.get("strategy_type") or c.get("scanner_key")

    def test_quality_level_at_top(self, client, data_dir):
        self._setup(data_dir)
        data = client.get("/api/tmc/workflows/stock/latest").json()["data"]
        assert "quality_level" in data


# ═══════════════════════════════════════════════════════════════════
# 3. OPTIONS NORMALIZATION — field mapping validation
# ═══════════════════════════════════════════════════════════════════


class TestOptionsNormalizationContract:
    """Fields the P10 normalizeOptionsCandidate() reads must be present.
    JS normalizer absorbs aliases:
      symbol ← underlying | symbol
      strategy ← strategy_id | strategy_type | family
      ev ← ev
      pop ← pop
      maxLoss ← max_loss
      credit ← credit | net_premium | debit
      dte ← dte
      width ← width
      legs ← legs
    """

    OPTIONS_OUTPUT = {
        "run_id": "run_on_001",
        "workflow_id": "options_opportunity",
        "generated_at": "2025-01-15T18:00:00+00:00",
        "publication_status": "completed",
        "total_candidates": 1,
        "selected_count": 1,
        "quality_level": "full",
        "candidates": [
            {
                "underlying": "SPY",
                "strategy_id": "bull_put_spread",
                "ev": 15.0,
                "pop": 0.70,
                "max_loss": -85.0,
                "credit": 0.50,
                "dte": 14,
                "width": 5.0,
                "legs": [
                    {"side": "sell", "strike": 540, "option_type": "put", "expiration": "2025-02-07"},
                    {"side": "buy", "strike": 535, "option_type": "put", "expiration": "2025-02-07"},
                ],
            },
        ],
        "scan_diagnostics": {"total_scanned": 80, "passed": 1},
        "validation_summary": {"valid": 1, "invalid": 0},
        "warnings": [],
    }

    def _setup(self, data_dir):
        run_id = "run_on_001"
        _write_pointer(data_dir, "options_opportunity", run_id)
        d = data_dir / "workflows" / "options_opportunity" / run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "output.json").write_text(json.dumps(self.OPTIONS_OUTPUT), encoding="utf-8")

    def test_quantitative_fields_present(self, client, data_dir):
        self._setup(data_dir)
        c = client.get("/api/tmc/workflows/options/latest").json()["data"]["candidates"][0]
        for field in ("ev", "pop", "max_loss", "credit", "dte", "width"):
            assert field in c, f"Missing field: {field}"

    def test_symbol_via_underlying(self, client, data_dir):
        self._setup(data_dir)
        c = client.get("/api/tmc/workflows/options/latest").json()["data"]["candidates"][0]
        # normalizer reads underlying first, symbol as fallback
        assert c.get("underlying") or c.get("symbol")

    def test_strategy_via_strategy_id(self, client, data_dir):
        self._setup(data_dir)
        c = client.get("/api/tmc/workflows/options/latest").json()["data"]["candidates"][0]
        assert c.get("strategy_id") or c.get("strategy_type") or c.get("family")

    def test_legs_have_required_fields(self, client, data_dir):
        self._setup(data_dir)
        c = client.get("/api/tmc/workflows/options/latest").json()["data"]["candidates"][0]
        legs = c["legs"]
        assert isinstance(legs, list)
        assert len(legs) > 0
        for leg in legs:
            assert "side" in leg
            assert "strike" in leg
            # normalizer reads option_type first, type as fallback
            assert leg.get("option_type") or leg.get("type")

    def test_diagnostics_present(self, client, data_dir):
        self._setup(data_dir)
        data = client.get("/api/tmc/workflows/options/latest").json()["data"]
        assert "scan_diagnostics" in data
        assert "validation_summary" in data


# ═══════════════════════════════════════════════════════════════════
# 4. EMPTY STATE HANDLING — consolidated guarantees
# ═══════════════════════════════════════════════════════════════════


class TestEmptyStateHandling:
    """handleWorkflowResponse depends on these shapes for empty/error paths."""

    def test_no_pointer_gives_null_data(self, client):
        """No pointer → status=no_output, data=null → showEmptyGrid()."""
        for path in ("/api/tmc/workflows/stock/latest", "/api/tmc/workflows/options/latest"):
            body = client.get(path).json()
            assert body["status"] == "no_output"
            assert body["data"] is None

    def test_failed_status_accessible(self):
        """JS getStatusInfo('failed').isError must be true."""
        assert "failed" in {s.value for s in TMCStatus}

    def test_degraded_status_accessible(self):
        """JS getStatusInfo('degraded').isError must be false."""
        assert "degraded" in {s.value for s in TMCStatus}


# ═══════════════════════════════════════════════════════════════════
# 5. RESPONSE ENVELOPE STABILITY
# ═══════════════════════════════════════════════════════════════════


class TestResponseEnvelopeStability:
    """handleWorkflowResponse reads resp.status and resp.data.
    These shapes must remain stable.
    """

    def test_stock_envelope_has_status_and_data(self, client):
        body = client.get("/api/tmc/workflows/stock/latest").json()
        assert "status" in body
        assert "data" in body

    def test_options_envelope_has_status_and_data(self, client):
        body = client.get("/api/tmc/workflows/options/latest").json()
        assert "status" in body
        assert "data" in body

    def test_summary_envelope_has_status_and_data(self, client):
        body = client.get("/api/tmc/workflows/stock/summary").json()
        assert "status" in body
        assert "data" in body
