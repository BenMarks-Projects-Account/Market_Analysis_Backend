"""Tests for Market Picture History — snapshot schema, persistence, retrieval.

Run with:
    cd BenTrade/backend
    python -m pytest tests/test_market_picture_history.py -v --tb=short
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.market_picture_history import (
    HISTORY_FILENAME,
    MAX_SNAPSHOTS,
    SCHEMA_VERSION,
    append_snapshot,
    build_snapshot,
    load_history,
)
from app.api.routes_market_picture import router, ENGINE_DISPLAY


# ═══════════════════════════════════════════════════════════════════
# FIXTURES / HELPERS
# ═══════════════════════════════════════════════════════════════════


def _minimal_artifact(
    artifact_id: str = "run_test_001",
    generated_at: str = "2026-03-18T10:00:00+00:00",
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "generated_at": generated_at,
        "composite": {
            "market_state": "neutral",
            "support_state": "mixed",
            "stability_state": "orderly",
            "confidence": 0.48,
            "summary": "Composite summary.",
        },
        "consumer_summary": {
            "regime_label": "NEUTRAL",
            "regime_score": 52.0,
        },
        "model_interpretation": {"status": "failed"},
        "engines": {
            key: {
                "score": 55.0 + i,
                "short_label": "Mixed",
                "label": "Mixed (full)",
                "summary": f"Engine summary for {key}.",
                "confidence": 80.0,
                "engine_status": "ok",
            }
            for i, (key, _) in enumerate(ENGINE_DISPLAY)
        },
    }


def _engine_cards_from_artifact(artifact: dict) -> list[dict[str, Any]]:
    raw = artifact.get("engines") or {}
    cards = []
    for key, name in ENGINE_DISPLAY:
        eng = raw.get(key) or {}
        cards.append({
            "key": key,
            "name": name,
            "engine_score": eng.get("score"),
            "engine_label": eng.get("short_label") or eng.get("label"),
            "engine_summary": eng.get("summary"),
            "model_score": None,
            "model_summary": None,
            "confidence": eng.get("confidence"),
            "status": eng.get("engine_status", "ok"),
        })
    return cards


def _build_test_snapshot(
    artifact_id: str = "run_test_001",
    captured_at: str | None = None,
) -> dict[str, Any]:
    artifact = _minimal_artifact(artifact_id=artifact_id)
    cards = _engine_cards_from_artifact(artifact)
    comp = artifact["composite"]
    snap = build_snapshot(
        artifact=artifact,
        engine_cards=cards,
        composite=comp,
        model_status="failed",
        generated_at=artifact["generated_at"],
    )
    if captured_at:
        snap["captured_at"] = captured_at
    return snap


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Schema
# ═══════════════════════════════════════════════════════════════════


class TestSnapshotSchema:
    """Verify build_snapshot produces the correct compact shape."""

    def test_schema_has_required_fields(self):
        snap = _build_test_snapshot()
        required = [
            "schema_version", "captured_at", "artifact_id", "generated_at",
            "regime_state", "regime_support", "regime_stability",
            "regime_confidence", "regime_summary",
            "consumer_regime_label", "consumer_regime_score",
            "engines", "model_status",
        ]
        for field in required:
            assert field in snap, f"Missing field: {field}"

    def test_schema_version(self):
        snap = _build_test_snapshot()
        assert snap["schema_version"] == SCHEMA_VERSION

    def test_engines_compact_shape(self):
        snap = _build_test_snapshot()
        assert len(snap["engines"]) == 6
        eng = snap["engines"][0]
        assert "key" in eng
        assert "engine_score" in eng
        assert "engine_label" in eng
        assert "model_score" in eng
        assert "confidence" in eng
        assert "status" in eng
        # Must NOT include bulky fields
        assert "engine_summary" not in eng
        assert "model_summary" not in eng
        assert "name" not in eng

    def test_regime_fields_populated(self):
        snap = _build_test_snapshot()
        assert snap["regime_state"] == "neutral"
        assert snap["regime_support"] == "mixed"
        assert snap["regime_stability"] == "orderly"
        assert snap["regime_confidence"] == 0.48
        assert snap["consumer_regime_label"] == "NEUTRAL"
        assert snap["consumer_regime_score"] == 52.0

    def test_missing_consumer_summary_handled(self):
        artifact = _minimal_artifact()
        del artifact["consumer_summary"]
        cards = _engine_cards_from_artifact(artifact)
        snap = build_snapshot(
            artifact=artifact,
            engine_cards=cards,
            composite=artifact["composite"],
            model_status=None,
            generated_at=artifact["generated_at"],
        )
        assert snap["consumer_regime_label"] is None
        assert snap["consumer_regime_score"] is None
        assert snap["model_status"] is None

    def test_missing_engine_produces_null_scores(self):
        artifact = _minimal_artifact()
        artifact["engines"] = {"breadth_participation": artifact["engines"]["breadth_participation"]}
        cards = _engine_cards_from_artifact(artifact)
        snap = build_snapshot(
            artifact=artifact,
            engine_cards=cards,
            composite=artifact["composite"],
            model_status="failed",
            generated_at=artifact["generated_at"],
        )
        present = snap["engines"][0]
        assert present["engine_score"] is not None

        missing = snap["engines"][1]
        assert missing["engine_score"] is None
        assert missing["status"] == "ok"  # default from empty dict


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — Persistence
# ═══════════════════════════════════════════════════════════════════


class TestPersistence:
    """Verify append/dedup/retrieval/trim behavior."""

    def test_append_creates_file(self, tmp_path: Path):
        snap = _build_test_snapshot()
        result = append_snapshot(str(tmp_path), snap)
        assert result is True
        path = tmp_path / "market_state" / HISTORY_FILENAME
        assert path.exists()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["artifact_id"] == "run_test_001"

    def test_append_multiple_entries(self, tmp_path: Path):
        for i in range(5):
            snap = _build_test_snapshot(
                artifact_id=f"run_{i}",
                captured_at=f"2026-03-18T0{i}:00:00+00:00",
            )
            append_snapshot(str(tmp_path), snap)
        entries = load_history(str(tmp_path))
        assert len(entries) == 5
        assert entries[0]["artifact_id"] == "run_0"
        assert entries[4]["artifact_id"] == "run_4"

    def test_dedup_skips_same_artifact_within_window(self, tmp_path: Path):
        snap1 = _build_test_snapshot(captured_at="2026-03-18T10:00:00+00:00")
        snap2 = _build_test_snapshot(captured_at="2026-03-18T10:01:00+00:00")
        assert append_snapshot(str(tmp_path), snap1) is True
        assert append_snapshot(str(tmp_path), snap2) is False
        entries = load_history(str(tmp_path))
        assert len(entries) == 1

    def test_dedup_allows_different_artifact(self, tmp_path: Path):
        snap1 = _build_test_snapshot(artifact_id="run_A", captured_at="2026-03-18T10:00:00+00:00")
        snap2 = _build_test_snapshot(artifact_id="run_B", captured_at="2026-03-18T10:00:30+00:00")
        append_snapshot(str(tmp_path), snap1)
        result = append_snapshot(str(tmp_path), snap2)
        assert result is True
        entries = load_history(str(tmp_path))
        assert len(entries) == 2

    def test_dedup_allows_same_artifact_outside_window(self, tmp_path: Path):
        snap1 = _build_test_snapshot(captured_at="2026-03-18T10:00:00+00:00")
        snap2 = _build_test_snapshot(captured_at="2026-03-18T10:05:00+00:00")  # 5 min later
        append_snapshot(str(tmp_path), snap1)
        result = append_snapshot(str(tmp_path), snap2)
        assert result is True

    def test_load_with_limit(self, tmp_path: Path):
        for i in range(10):
            snap = _build_test_snapshot(
                artifact_id=f"run_{i}",
                captured_at=f"2026-03-18T{10 + i}:00:00+00:00",
            )
            append_snapshot(str(tmp_path), snap)
        entries = load_history(str(tmp_path), limit=3)
        assert len(entries) == 3
        assert entries[0]["artifact_id"] == "run_7"
        assert entries[2]["artifact_id"] == "run_9"

    def test_load_empty_returns_empty(self, tmp_path: Path):
        entries = load_history(str(tmp_path))
        assert entries == []

    def test_trim_enforces_max(self, tmp_path: Path):
        """Write more than MAX_SNAPSHOTS and verify trim occurs."""
        path = tmp_path / "market_state" / HISTORY_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write MAX_SNAPSHOTS + 50 lines directly
        with open(path, "w", encoding="utf-8") as f:
            for i in range(MAX_SNAPSHOTS + 50):
                entry = {"schema_version": 1, "artifact_id": f"old_{i}", "captured_at": f"2026-01-01T00:{i:04d}:00+00:00"}
                f.write(json.dumps(entry) + "\n")
        # Append one more to trigger trim
        snap = _build_test_snapshot(artifact_id="new_final", captured_at="2026-12-31T23:59:59+00:00")
        append_snapshot(str(tmp_path), snap)
        entries = load_history(str(tmp_path))
        assert len(entries) <= MAX_SNAPSHOTS
        assert entries[-1]["artifact_id"] == "new_final"

    def test_corrupt_line_skipped_on_load(self, tmp_path: Path):
        path = tmp_path / "market_state" / HISTORY_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"artifact_id": "good_1", "captured_at": "2026-01-01T00:00:00+00:00"}) + "\n")
            f.write("NOT VALID JSON\n")
            f.write(json.dumps({"artifact_id": "good_2", "captured_at": "2026-01-01T01:00:00+00:00"}) + "\n")
        entries = load_history(str(tmp_path))
        assert len(entries) == 2
        assert entries[0]["artifact_id"] == "good_1"
        assert entries[1]["artifact_id"] == "good_2"


# ═══════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — API endpoints
# ═══════════════════════════════════════════════════════════════════


class _FakeConsumer:
    def __init__(self, loaded, artifact=None, error=None):
        self.loaded = loaded
        self.artifact = artifact
        self.error = error


CONSUMER_PATH = "app.workflows.market_state_consumer.load_market_state_for_consumer"


class TestHistoryEndpoint:
    """Tests for GET /api/market-picture/history."""

    def _create_app(self, tmp_path: Path) -> FastAPI:
        app = FastAPI()
        app.include_router(router)
        backend_dir = tmp_path / "backend"
        (backend_dir / "data").mkdir(parents=True, exist_ok=True)
        app.state.backend_dir = backend_dir
        return app

    def test_history_returns_empty_initially(self, tmp_path: Path):
        app = self._create_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/market-picture/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["entries"] == []
        assert body["count"] == 0

    def test_scoreboard_captures_snapshot(self, tmp_path: Path):
        app = self._create_app(tmp_path)
        client = TestClient(app)
        artifact = _minimal_artifact()
        consumer = _FakeConsumer(loaded=True, artifact=artifact)
        with patch(CONSUMER_PATH, return_value=consumer):
            resp = client.get("/api/market-picture/scoreboard")
        assert resp.json()["ok"] is True

        # Now check history was captured
        resp2 = client.get("/api/market-picture/history")
        body = resp2.json()
        assert body["ok"] is True
        assert body["count"] == 1
        entry = body["entries"][0]
        assert entry["artifact_id"] == "run_test_001"
        assert entry["regime_state"] == "neutral"
        assert len(entry["engines"]) == 6

    def test_history_limit_param(self, tmp_path: Path):
        app = self._create_app(tmp_path)
        client = TestClient(app)
        # Write multiple snapshots via scoreboard calls with different artifacts
        for i in range(5):
            artifact = _minimal_artifact(
                artifact_id=f"run_{i}",
                generated_at=f"2026-03-18T0{i}:00:00+00:00",
            )
            consumer = _FakeConsumer(loaded=True, artifact=artifact)
            with patch(CONSUMER_PATH, return_value=consumer):
                client.get("/api/market-picture/scoreboard")

        resp = client.get("/api/market-picture/history?limit=2")
        body = resp.json()
        assert body["count"] == 2
        assert body["entries"][0]["artifact_id"] == "run_3"
        assert body["entries"][1]["artifact_id"] == "run_4"
