"""Tests for POST /api/export/on-demand-pdf route.

Uses TestClient + a minimal FastAPI app including only the export router
(avoids booting the full BenTrade app with its large dependency graph).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes_export import router as export_router  # noqa: E402
from app.services.on_demand_pdf_service import (  # noqa: E402
    CEJobNotFoundError,
    CEUnreachableError,
    PDFTooLargeError,
)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(export_router)
    return TestClient(app)


def _payload_body() -> dict:
    return {
        "job_id": "job-abc",
        "symbol": "AAPL",
        "appended_analyses": [],
        "user_notes": None,
        "display_context": {
            "account_mode": "paper",
            "generated_at_iso": datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc).isoformat(),
        },
    }


def test_post_returns_pdf_on_happy_path(client):
    with patch(
        "app.api.routes_export.render_on_demand_pdf",
        new=AsyncMock(return_value=b"%PDF-1.4\n...fake pdf bytes..."),
    ):
        resp = client.post("/api/export/on-demand-pdf", json=_payload_body())
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].endswith('.pdf"')
    assert resp.content.startswith(b"%PDF-")


def test_post_404_when_job_not_found(client):
    with patch(
        "app.api.routes_export.render_on_demand_pdf",
        new=AsyncMock(side_effect=CEJobNotFoundError("missing")),
    ):
        resp = client.post("/api/export/on-demand-pdf", json=_payload_body())
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "JOB_NOT_FOUND"
    assert detail["job_id"] == "job-abc"


def test_post_502_when_ce_unreachable(client):
    with patch(
        "app.api.routes_export.render_on_demand_pdf",
        new=AsyncMock(side_effect=CEUnreachableError("conn refused")),
    ):
        resp = client.post("/api/export/on-demand-pdf", json=_payload_body())
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "CE_UNREACHABLE"


def test_post_500_when_pdf_too_large(client):
    with patch(
        "app.api.routes_export.render_on_demand_pdf",
        new=AsyncMock(side_effect=PDFTooLargeError("too big")),
    ):
        resp = client.post("/api/export/on-demand-pdf", json=_payload_body())
    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "PDF_TOO_LARGE"


def test_post_500_on_unexpected_error(client):
    with patch(
        "app.api.routes_export.render_on_demand_pdf",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        resp = client.post("/api/export/on-demand-pdf", json=_payload_body())
    assert resp.status_code == 500
    assert resp.json()["detail"]["code"] == "RENDER_FAILED"


def test_post_422_on_invalid_symbol(client):
    body = _payload_body()
    body["symbol"] = "lowercase!"  # fails regex
    resp = client.post("/api/export/on-demand-pdf", json=body)
    assert resp.status_code == 422


def test_post_422_on_missing_job_id(client):
    body = _payload_body()
    del body["job_id"]
    resp = client.post("/api/export/on-demand-pdf", json=body)
    assert resp.status_code == 422


def test_post_422_rejects_extra_fields(client):
    body = _payload_body()
    body["unknown_field"] = "reject me"
    resp = client.post("/api/export/on-demand-pdf", json=body)
    assert resp.status_code == 422


def test_filename_contains_symbol_and_timestamp(client):
    with patch(
        "app.api.routes_export.render_on_demand_pdf",
        new=AsyncMock(return_value=b"%PDF-1.4\n"),
    ):
        resp = client.post("/api/export/on-demand-pdf", json=_payload_body())
    cd = resp.headers["content-disposition"]
    assert "AAPL_on_demand_" in cd
