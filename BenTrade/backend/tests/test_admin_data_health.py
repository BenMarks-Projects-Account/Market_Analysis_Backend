from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import create_app  # noqa: E402
from app.services.validation_events import ValidationEventsService  # noqa: E402


class _DummyBaseDataService:
    def get_source_health_snapshot(self) -> dict:
        return {
            "tradier": {"status": "green", "message": "ok"},
            "polygon": {"status": "green", "message": "healthy"},
            "finnhub": {"status": "red", "message": "down"},
        }


def _build_client(tmp_path: Path) -> TestClient:
    app = create_app()
    app.state.base_data_service = _DummyBaseDataService()
    app.state.results_dir = tmp_path
    app.state.validation_events = ValidationEventsService(results_dir=tmp_path)
    return TestClient(app)


def test_data_health_endpoint_handles_missing_file(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    events_file = tmp_path / "validation_events.jsonl"
    if events_file.exists():
        events_file.unlink()

    response = client.get("/api/admin/data-health")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("source_health"), dict)
    assert payload.get("validation_events") == []
    assert payload.get("rollups", {}).get("counts_by_code") == {}


def test_data_health_endpoint_returns_rollups(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    service: ValidationEventsService = client.app.state.validation_events

    service.append_event(
        severity="warn",
        code="ANNUALIZE_SHORT_DTE",
        message="short dte",
        context={"symbol": "QQQ"},
    )
    service.append_event(
        severity="error",
        code="NUMERIC_NONFINITE",
        message="nan sanitized",
        context={"path": "payload.ev"},
    )

    response = client.get("/api/admin/data-health")
    assert response.status_code == 200
    payload = response.json()

    events = payload.get("validation_events")
    assert isinstance(events, list)
    assert len(events) == 2

    rollups = payload.get("rollups") or {}
    assert rollups.get("counts_by_code", {}).get("ANNUALIZE_SHORT_DTE") == 1
    assert rollups.get("counts_by_code", {}).get("NUMERIC_NONFINITE") == 1
    assert rollups.get("counts_by_severity", {}).get("warn") == 1
    assert rollups.get("counts_by_severity", {}).get("error") == 1
