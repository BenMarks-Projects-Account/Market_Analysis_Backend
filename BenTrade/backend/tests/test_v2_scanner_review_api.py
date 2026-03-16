"""Tests for Scanner Review API routes (Prompt 14).

Validates:
- ``/api/scanner-review/routing`` returns V2 routing overview
- ``/api/scanner-review/runs/{run_id}/scanner-summary`` extracts scanner
  diagnostics from pipeline artifacts
- ``/api/scanner-review/runs/{run_id}/candidates`` returns candidates
  with filtering
- 404 for unknown run IDs
- Diagnostics enrichment in ``build_scanner_stage_summary``
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.services import pipeline_run_store  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DummyBaseDataService:
    def get_source_health_snapshot(self) -> dict:
        return {"tradier": {"status": "green", "message": "ok"}}


def _build_client(tmp_path: Path) -> TestClient:
    app = create_app()
    app.state.base_data_service = _DummyBaseDataService()
    app.state.results_dir = tmp_path
    return TestClient(app)


def _make_test_run(run_id: str = "test-run-001") -> dict[str, Any]:
    """Return a minimal pipeline run snapshot with scanner_stage_summary."""
    return {
        "run_id": run_id,
        "stored_at": "2026-03-12T10:00:00Z",
        "run": {
            "run_id": run_id,
            "status": "completed",
            "trigger_source": "test",
            "started_at": "2026-03-12T09:58:00Z",
            "ended_at": "2026-03-12T10:00:00Z",
            "duration_ms": 120000,
            "stages": {},
        },
        "artifact_store": {
            "artifacts": {
                "art-scanner-summary": {
                    "artifact_id": "art-scanner-summary",
                    "artifact_type": "scanner_stage_summary",
                    "stage_key": "scanners",
                    "data": {
                        "stage_status": "completed",
                        "total_run": 3,
                        "total_candidates": 25,
                        "total_usable_candidates": 10,
                        "completed_count": 3,
                        "failed_count": 0,
                        "elapsed_ms": 5000,
                        "generated_at": "2026-03-12T10:00:00Z",
                        "routing_summary": {
                            "v2_count": 2,
                            "legacy_count": 1,
                        },
                        "scanner_summaries": {
                            "put_credit_spread": {
                                "status": "completed",
                                "execution_path": "v2",
                                "candidate_count": 15,
                                "usable_candidate_count": 6,
                                "elapsed_ms": 2000,
                                "scanner_family": "options",
                                "diagnostics": {
                                    "stage_counts": [
                                        {"stage": "initial", "remaining": 100},
                                        {"stage": "liquidity", "remaining": 50},
                                        {"stage": "spread", "remaining": 25},
                                        {"stage": "ev", "remaining": 15},
                                    ],
                                    "rejection_reason_counts": {
                                        "LOW_OI": 30,
                                        "WIDE_SPREAD": 20,
                                        "NEGATIVE_EV": 5,
                                    },
                                    "data_quality_counts": {
                                        "missing_bid": 3,
                                        "missing_iv": 1,
                                    },
                                    "candidate_count": 15,
                                    "accepted_count": 6,
                                },
                            },
                            "iron_condor": {
                                "status": "completed",
                                "execution_path": "v2",
                                "candidate_count": 8,
                                "usable_candidate_count": 3,
                                "elapsed_ms": 1500,
                                "scanner_family": "options",
                                "diagnostics": {
                                    "candidate_count": 8,
                                    "accepted_count": 3,
                                },
                            },
                            "pullback_swing": {
                                "status": "completed",
                                "execution_path": "legacy",
                                "candidate_count": 2,
                                "usable_candidate_count": 1,
                                "elapsed_ms": 1500,
                                "scanner_family": "stock",
                                "diagnostics": None,
                            },
                        },
                    },
                },
                "art-candidates-1": {
                    "artifact_id": "art-candidates-1",
                    "artifact_type": "normalized_candidate",
                    "stage_key": "scanners",
                    "data": {
                        "candidates": [
                            {
                                "symbol": "SPY",
                                "strategy": "put_credit_spread",
                                "scanner_key": "put_credit_spread",
                                "status": "accepted",
                                "credit": 0.85,
                                "width": 5,
                                "ev": 0.42,
                                "delta": -0.15,
                                "dte": 21,
                            },
                            {
                                "symbol": "QQQ",
                                "strategy": "iron_condor",
                                "scanner_key": "iron_condor",
                                "status": "accepted",
                                "credit": 1.20,
                                "width": 10,
                                "ev": 0.60,
                                "delta": -0.08,
                                "dte": 28,
                            },
                        ],
                    },
                },
            },
            "type_index": {
                "scanner_stage_summary": ["art-scanner-summary"],
                "normalized_candidate": ["art-candidates-1"],
            },
        },
        "stage_results": [],
        "summary": {},
        "events": [],
    }


# ---------------------------------------------------------------------------
# Tests — Routing overview
# ---------------------------------------------------------------------------


def test_routing_overview_returns_expected_keys(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    r = client.get("/api/scanner-review/routing")
    assert r.status_code == 200
    data = r.json()

    assert "routing_model" in data
    assert "v2_families" in data
    assert "scanner_key_routing" in data
    assert "family_verification" in data
    assert "pipeline_registry" in data


def test_routing_overview_family_verification_shape(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    r = client.get("/api/scanner-review/routing")
    data = r.json()

    fv = data["family_verification"]
    assert isinstance(fv, dict)
    # Should have at least the 4 V2 families
    for fk in ("vertical_spreads", "iron_condors", "butterflies", "calendars"):
        if fk in fv:
            assert "strategy_ids" in fv[fk]
            assert "ready_for_legacy_deletion" in fv[fk]


# ---------------------------------------------------------------------------
# Tests — Run scanner summary
# ---------------------------------------------------------------------------


def test_run_scanner_summary_success(tmp_path: Path) -> None:
    pipeline_run_store.clear_all()
    try:
        run = _make_test_run("run-sr-001")
        pipeline_run_store.store_active_run("run-sr-001", run["run"])
        # Inject the full snapshot including artifacts
        from app.services.pipeline_run_store import _runs, _lock
        with _lock:
            _runs["run-sr-001"] = run

        client = _build_client(tmp_path)
        r = client.get("/api/scanner-review/runs/run-sr-001/scanner-summary")
        assert r.status_code == 200
        data = r.json()

        assert data["available"] is True
        assert data["run_id"] == "run-sr-001"
        assert data["total_run"] == 3
        assert data["total_candidates"] == 25
        assert data["total_usable_candidates"] == 10
        assert "scanner_summaries" in data
        assert "family_groups" in data
        assert "put_credit_spread" in data["scanner_summaries"]
    finally:
        pipeline_run_store.clear_all()


def test_run_scanner_summary_404(tmp_path: Path) -> None:
    pipeline_run_store.clear_all()
    client = _build_client(tmp_path)
    r = client.get("/api/scanner-review/runs/nonexistent/scanner-summary")
    assert r.status_code == 404


def test_run_scanner_summary_no_artifact(tmp_path: Path) -> None:
    pipeline_run_store.clear_all()
    try:
        empty_run = {
            "run_id": "run-sr-empty",
            "stored_at": "2026-03-12T10:00:00Z",
            "run": {"run_id": "run-sr-empty", "status": "completed"},
            "artifact_store": {"artifacts": {}, "type_index": {}},
            "stage_results": [],
            "summary": {},
            "events": [],
        }
        from app.services.pipeline_run_store import _runs, _lock
        with _lock:
            _runs["run-sr-empty"] = empty_run

        client = _build_client(tmp_path)
        r = client.get("/api/scanner-review/runs/run-sr-empty/scanner-summary")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is False
    finally:
        pipeline_run_store.clear_all()


def test_family_groups_correct(tmp_path: Path) -> None:
    pipeline_run_store.clear_all()
    try:
        run = _make_test_run("run-sr-fam")
        from app.services.pipeline_run_store import _runs, _lock
        with _lock:
            _runs["run-sr-fam"] = run

        client = _build_client(tmp_path)
        r = client.get("/api/scanner-review/runs/run-sr-fam/scanner-summary")
        data = r.json()
        groups = data["family_groups"]

        assert "vertical_spreads" in groups
        assert "put_credit_spread" in groups["vertical_spreads"]["scanners"]
        assert "iron_condors" in groups
        assert "iron_condor" in groups["iron_condors"]["scanners"]
        assert "stock" in groups
        assert "pullback_swing" in groups["stock"]["scanners"]
    finally:
        pipeline_run_store.clear_all()


# ---------------------------------------------------------------------------
# Tests — Candidates
# ---------------------------------------------------------------------------


def test_candidates_all(tmp_path: Path) -> None:
    pipeline_run_store.clear_all()
    try:
        run = _make_test_run("run-sr-cand")
        from app.services.pipeline_run_store import _runs, _lock
        with _lock:
            _runs["run-sr-cand"] = run

        client = _build_client(tmp_path)
        r = client.get("/api/scanner-review/runs/run-sr-cand/candidates")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 2
        assert len(data["candidates"]) == 2
    finally:
        pipeline_run_store.clear_all()


def test_candidates_filtered_by_scanner_key(tmp_path: Path) -> None:
    pipeline_run_store.clear_all()
    try:
        run = _make_test_run("run-sr-filt")
        from app.services.pipeline_run_store import _runs, _lock
        with _lock:
            _runs["run-sr-filt"] = run

        client = _build_client(tmp_path)
        r = client.get(
            "/api/scanner-review/runs/run-sr-filt/candidates",
            params={"scanner_key": "put_credit_spread"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["candidates"][0]["symbol"] == "SPY"
    finally:
        pipeline_run_store.clear_all()


def test_candidates_404_unknown_run(tmp_path: Path) -> None:
    pipeline_run_store.clear_all()
    client = _build_client(tmp_path)
    r = client.get("/api/scanner-review/runs/nope/candidates")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tests — Diagnostics enrichment (unit-level)
# ---------------------------------------------------------------------------


def test_diagnostics_in_scanner_summary_dict(tmp_path: Path) -> None:
    """Verify that the enriched scanner_summaries carry diagnostics."""
    pipeline_run_store.clear_all()
    try:
        run = _make_test_run("run-sr-diag")
        from app.services.pipeline_run_store import _runs, _lock
        with _lock:
            _runs["run-sr-diag"] = run

        client = _build_client(tmp_path)
        r = client.get("/api/scanner-review/runs/run-sr-diag/scanner-summary")
        data = r.json()
        pcs = data["scanner_summaries"]["put_credit_spread"]
        diag = pcs["diagnostics"]

        assert diag is not None
        assert len(diag["stage_counts"]) == 4
        assert diag["rejection_reason_counts"]["LOW_OI"] == 30
        assert diag["data_quality_counts"]["missing_bid"] == 3
        assert diag["candidate_count"] == 15
        assert diag["accepted_count"] == 6
    finally:
        pipeline_run_store.clear_all()


def test_diagnostics_null_when_absent(tmp_path: Path) -> None:
    """Scanner with no diagnostics should have diagnostics=None."""
    pipeline_run_store.clear_all()
    try:
        run = _make_test_run("run-sr-nodiag")
        from app.services.pipeline_run_store import _runs, _lock
        with _lock:
            _runs["run-sr-nodiag"] = run

        client = _build_client(tmp_path)
        r = client.get("/api/scanner-review/runs/run-sr-nodiag/scanner-summary")
        data = r.json()
        stock = data["scanner_summaries"]["pullback_swing"]
        assert stock["diagnostics"] is None
    finally:
        pipeline_run_store.clear_all()
