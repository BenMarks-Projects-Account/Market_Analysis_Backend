"""Tests for Market Picture Scoreboard API route — Prompt 3B.

Run with:
    cd BenTrade/backend
    python -m pytest tests/test_routes_market_picture.py -v --tb=short
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_market_picture import router, ENGINE_DISPLAY


# ═══════════════════════════════════════════════════════════════════
# FIXTURES / HELPERS
# ═══════════════════════════════════════════════════════════════════


def _create_app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    backend_dir = tmp_path / "backend"
    (backend_dir / "data").mkdir(parents=True, exist_ok=True)
    app.state.backend_dir = backend_dir
    return app


@dataclass
class FakeConsumer:
    loaded: bool = False
    artifact: dict[str, Any] | None = None
    error: str | None = None


def _minimal_engine(key: str, score: float = 55.0, label: str = "Mixed") -> dict[str, Any]:
    return {
        "score": score,
        "short_label": label,
        "label": f"{label} (full)",
        "confidence": 80.0,
        "summary": f"Engine summary for {key}.",
        "trader_takeaway": f"Takeaway for {key}.",
        "engine_status": "ok",
    }


def _full_artifact(
    engines: dict[str, dict] | None = None,
    composite: dict | None = None,
    model_interpretation: dict | None = None,
) -> dict[str, Any]:
    if engines is None:
        engines = {key: _minimal_engine(key) for key, _ in ENGINE_DISPLAY}
    if composite is None:
        composite = {
            "market_state": "neutral",
            "support_state": "mixed",
            "stability_state": "orderly",
            "confidence": 0.48,
            "summary": "Composite summary.",
        }
    if model_interpretation is None:
        model_interpretation = {"status": "failed"}
    return {
        "artifact_id": "test_run",
        "generated_at": "2026-03-18T06:00:00+00:00",
        "engines": engines,
        "composite": composite,
        "model_interpretation": model_interpretation,
    }


def _mock_consumer(artifact: dict[str, Any] | None) -> FakeConsumer:
    if artifact is None:
        return FakeConsumer(loaded=False, error="No artifact")
    return FakeConsumer(loaded=True, artifact=artifact)


CONSUMER_PATH = "app.workflows.market_state_consumer.load_market_state_for_consumer"


# ═══════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════


class TestScoreboardEndpoint:
    """Tests for GET /api/market-picture/scoreboard."""

    def test_returns_ok_with_full_artifact(self, tmp_path: Path) -> None:
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact())):
            resp = client.get("/api/market-picture/scoreboard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert len(body["engines"]) == 6
        assert body["composite"]["market_state"] == "neutral"
        assert body["model_status"] == "failed"
        assert body["generated_at"] is not None

    def test_card_shape_has_paired_fields(self, tmp_path: Path) -> None:
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact())):
            resp = client.get("/api/market-picture/scoreboard")
        card = resp.json()["engines"][0]

        # Required paired fields
        assert "engine_score" in card
        assert "engine_label" in card
        assert "engine_summary" in card
        assert "model_score" in card
        assert "model_summary" in card
        assert "key" in card
        assert "name" in card
        assert "confidence" in card
        assert "status" in card

        # Old single-score fields must NOT be present
        assert "score" not in card
        assert "label" not in card
        assert "summary" not in card
        assert "trader_takeaway" not in card

    def test_engine_fields_populated_model_null_when_no_store(self, tmp_path: Path) -> None:
        """When no durable model scores exist, model_score is None."""
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact())):
            resp = client.get("/api/market-picture/scoreboard")
        for card in resp.json()["engines"]:
            assert card["engine_score"] is not None, f"{card['key']} missing engine_score"
            assert card["engine_summary"] is not None, f"{card['key']} missing engine_summary"
            assert card["model_score"] is None, f"{card['key']} should have null model_score without durable store"
            assert card["model_summary"] is None, f"{card['key']} should have null model_summary"
            assert card["model_fresh"] is False, f"{card['key']} should report model_fresh=False"
            assert card["model_captured_at"] is None, f"{card['key']} should have null model_captured_at"

    def test_stable_engine_order(self, tmp_path: Path) -> None:
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact())):
            resp = client.get("/api/market-picture/scoreboard")
        keys = [c["key"] for c in resp.json()["engines"]]
        expected = [key for key, _ in ENGINE_DISPLAY]
        assert keys == expected

    def test_missing_engine_handled(self, tmp_path: Path) -> None:
        engines = {"breadth_participation": _minimal_engine("breadth_participation")}
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact(engines=engines))):
            resp = client.get("/api/market-picture/scoreboard")
        cards = resp.json()["engines"]
        assert len(cards) == 6

        present = cards[0]
        assert present["engine_score"] is not None
        assert present["status"] == "ok"

        missing = cards[1]
        assert missing["engine_score"] is None
        assert missing["model_score"] is None
        assert missing["status"] == "missing"

    def test_no_artifact_returns_ok_false(self, tmp_path: Path) -> None:
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(None)):
            resp = client.get("/api/market-picture/scoreboard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["engines"] == []

    def test_composite_fields(self, tmp_path: Path) -> None:
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact())):
            resp = client.get("/api/market-picture/scoreboard")
        c = resp.json()["composite"]
        assert c["market_state"] == "neutral"
        assert c["support_state"] == "mixed"
        assert c["stability_state"] == "orderly"
        assert c["confidence"] == 0.48
        assert c["summary"] == "Composite summary."

    def test_model_status_surfaced(self, tmp_path: Path) -> None:
        mi = {"status": "succeeded", "raw_content": "Some analysis"}
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact(model_interpretation=mi))):
            resp = client.get("/api/market-picture/scoreboard")
        assert resp.json()["model_status"] == "succeeded"

    def test_no_model_excerpt_in_contract(self, tmp_path: Path) -> None:
        """model_excerpt was removed in Prompt 3B — verify it's gone."""
        mi = {"status": "succeeded", "raw_content": "Some longer analysis text."}
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact(model_interpretation=mi))):
            resp = client.get("/api/market-picture/scoreboard")
        assert "model_excerpt" not in resp.json()

    def test_short_label_preferred_over_label(self, tmp_path: Path) -> None:
        eng = _minimal_engine("breadth_participation", label="Short")
        eng["label"] = "Full Label"
        eng["short_label"] = "Short"
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(
            _full_artifact(engines={"breadth_participation": eng})
        )):
            resp = client.get("/api/market-picture/scoreboard")
        card = resp.json()["engines"][0]
        assert card["engine_label"] == "Short"


class TestScoreboardDurableModelScores:
    """Tests verifying scoreboard hydrates model scores from durable store."""

    def _write_model_scores(self, tmp_path: Path, scores: dict) -> None:
        """Write model_scores_latest.json to the data dir used by _create_app."""
        ms_dir = tmp_path / "backend" / "data" / "market_state"
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "model_scores_latest.json").write_text(
            json.dumps(scores, ensure_ascii=False), encoding="utf-8",
        )

    def test_fresh_model_scores_hydrated_into_cards(self, tmp_path: Path) -> None:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        self._write_model_scores(tmp_path, {
            "breadth_participation": {
                "model_score": 72.5, "model_label": "BROAD_RALLY",
                "confidence": 0.8, "model_summary": "Breadth is strong.",
                "captured_at": now_iso,
            },
        })
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact())):
            resp = client.get("/api/market-picture/scoreboard")
        bp = next(c for c in resp.json()["engines"] if c["key"] == "breadth_participation")
        assert bp["model_score"] == 72.5
        assert bp["model_label"] == "BROAD_RALLY"
        assert bp["model_summary"] == "Breadth is strong."
        assert bp["model_fresh"] is True
        assert bp["model_captured_at"] is not None

    def test_stale_model_scores_returned_with_fresh_false(self, tmp_path: Path) -> None:
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        self._write_model_scores(tmp_path, {
            "volatility_options": {
                "model_score": 45.0, "model_label": "ELEVATED",
                "confidence": 0.7, "captured_at": old_ts,
            },
        })
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact())):
            resp = client.get("/api/market-picture/scoreboard")
        vo = next(c for c in resp.json()["engines"] if c["key"] == "volatility_options")
        assert vo["model_score"] == 45.0
        assert vo["model_fresh"] is False

    def test_missing_engine_still_gets_model_score(self, tmp_path: Path) -> None:
        """If engine data is missing from artifact but model score exists in store."""
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        self._write_model_scores(tmp_path, {
            "volatility_options": {
                "model_score": 60.0, "model_label": "NEUTRAL",
                "confidence": 0.6, "captured_at": now_iso,
            },
        })
        engines = {"breadth_participation": _minimal_engine("breadth_participation")}
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact(engines=engines))):
            resp = client.get("/api/market-picture/scoreboard")
        vo = next(c for c in resp.json()["engines"] if c["key"] == "volatility_options")
        assert vo["status"] == "missing"
        assert vo["model_score"] == 60.0
        assert vo["model_fresh"] is True

    def test_no_session_storage_dependency(self, tmp_path: Path) -> None:
        """Scores come from durable store, not from any session/cache mechanism."""
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        self._write_model_scores(tmp_path, {
            "cross_asset_macro": {
                "model_score": 55.0, "model_label": "MIXED",
                "confidence": 0.5, "captured_at": now_iso,
            },
        })
        app = _create_app(tmp_path)
        client = TestClient(app)
        # Simulates a fresh session — no sessionStorage
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact())):
            resp = client.get("/api/market-picture/scoreboard")
        cam = next(c for c in resp.json()["engines"] if c["key"] == "cross_asset_macro")
        assert cam["model_score"] == 55.0

    def test_card_shape_includes_model_freshness_fields(self, tmp_path: Path) -> None:
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact())):
            resp = client.get("/api/market-picture/scoreboard")
        card = resp.json()["engines"][0]
        assert "model_score" in card
        assert "model_label" in card
        assert "model_captured_at" in card
        assert "model_fresh" in card

    def test_consistent_across_reloads(self, tmp_path: Path) -> None:
        """Two consecutive requests return the same model scores."""
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        self._write_model_scores(tmp_path, {
            "breadth_participation": {
                "model_score": 80.0, "model_label": "STRONG",
                "confidence": 0.9, "captured_at": now_iso,
            },
        })
        app = _create_app(tmp_path)
        client = TestClient(app)
        with patch(CONSUMER_PATH, return_value=_mock_consumer(_full_artifact())):
            r1 = client.get("/api/market-picture/scoreboard").json()
            r2 = client.get("/api/market-picture/scoreboard").json()
        bp1 = next(c for c in r1["engines"] if c["key"] == "breadth_participation")
        bp2 = next(c for c in r2["engines"] if c["key"] == "breadth_participation")
        assert bp1["model_score"] == bp2["model_score"]
        assert bp1["model_fresh"] == bp2["model_fresh"]


class TestModelScoresEndpoint:
    """Tests for GET /api/market-picture/model-scores."""

    def _write_model_scores(self, tmp_path: Path, scores: dict) -> None:
        ms_dir = tmp_path / "backend" / "data" / "market_state"
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "model_scores_latest.json").write_text(
            json.dumps(scores, ensure_ascii=False), encoding="utf-8",
        )

    def test_returns_empty_when_no_scores(self, tmp_path: Path) -> None:
        app = _create_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/market-picture/model-scores")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["scores"] == {}

    def test_returns_scores_with_freshness(self, tmp_path: Path) -> None:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        self._write_model_scores(tmp_path, {
            "breadth_participation": {
                "model_score": 72.5, "model_label": "BROAD_RALLY",
                "confidence": 0.8, "captured_at": now_iso,
            },
        })
        app = _create_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/market-picture/model-scores")
        body = resp.json()
        assert body["ok"] is True
        bp = body["scores"]["breadth_participation"]
        assert bp["model_score"] == 72.5
        assert bp["is_fresh"] is True
        assert bp["age_seconds"] is not None

    def test_stale_score_flagged(self, tmp_path: Path) -> None:
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        self._write_model_scores(tmp_path, {
            "volatility_options": {
                "model_score": 45.0, "model_label": "ELEVATED",
                "confidence": 0.7, "captured_at": old_ts,
            },
        })
        app = _create_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/market-picture/model-scores")
        vo = resp.json()["scores"]["volatility_options"]
        assert vo["model_score"] == 45.0
        assert vo["is_fresh"] is False
