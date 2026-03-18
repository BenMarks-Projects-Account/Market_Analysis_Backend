"""Tests for Model Score Store and model-enriched history snapshots.

Run with:
    cd BenTrade/backend
    python -m pytest tests/test_model_score_store.py -v --tb=short
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.services.model_score_store import (
    DEFAULT_MAX_AGE_SECONDS,
    MAX_SUMMARY_LENGTH,
    STORE_FILENAME,
    load_all_scores,
    load_fresh_scores,
    sanitize_model_summary,
    save_model_score,
)
from app.services.market_picture_history import build_snapshot
from app.api.routes_market_picture import ENGINE_DISPLAY


# ═══════════════════════════════════════════════════════════════════
# FIXTURES / HELPERS
# ═══════════════════════════════════════════════════════════════════

def _model_analysis(score: float = 72.5, label: str = "BROAD_RALLY", confidence: float = 0.8) -> dict:
    return {"score": score, "label": label, "confidence": confidence, "summary": "Test summary."}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past_iso(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _store_path(data_dir: Path) -> Path:
    return data_dir / "market_state" / STORE_FILENAME


def _minimal_artifact(artifact_id: str = "run_test_001") -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "generated_at": "2026-03-18T10:00:00+00:00",
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


def _engine_cards(artifact: dict) -> list[dict[str, Any]]:
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


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — save_model_score
# ═══════════════════════════════════════════════════════════════════


class TestSaveModelScore:
    """Verify save_model_score writes correct JSON and handles edge cases."""

    def test_save_creates_file(self, tmp_path: Path):
        result = save_model_score(str(tmp_path), "breadth_participation", _model_analysis())
        assert result is True
        path = _store_path(tmp_path)
        assert path.exists()
        store = json.loads(path.read_text(encoding="utf-8"))
        assert "breadth_participation" in store
        entry = store["breadth_participation"]
        assert entry["model_score"] == 72.5
        assert entry["model_label"] == "BROAD_RALLY"
        assert entry["confidence"] == 0.8
        assert entry["model_summary"] == "Test summary."
        assert "captured_at" in entry

    def test_save_preserves_other_engines(self, tmp_path: Path):
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis(60.0))
        save_model_score(str(tmp_path), "volatility_options", _model_analysis(45.0, "ELEVATED"))
        store = json.loads(_store_path(tmp_path).read_text(encoding="utf-8"))
        assert store["breadth_participation"]["model_score"] == 60.0
        assert store["volatility_options"]["model_score"] == 45.0

    def test_save_overwrites_same_engine(self, tmp_path: Path):
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis(60.0))
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis(75.0))
        store = json.loads(_store_path(tmp_path).read_text(encoding="utf-8"))
        assert store["breadth_participation"]["model_score"] == 75.0

    def test_save_uses_as_of_when_provided(self, tmp_path: Path):
        ts = "2026-03-18T12:00:00+00:00"
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis(), as_of=ts)
        store = json.loads(_store_path(tmp_path).read_text(encoding="utf-8"))
        assert store["breadth_participation"]["captured_at"] == ts

    def test_save_returns_false_for_none(self, tmp_path: Path):
        assert save_model_score(str(tmp_path), "breadth_participation", None) is False
        assert not _store_path(tmp_path).exists()

    def test_save_returns_false_for_empty_dict(self, tmp_path: Path):
        assert save_model_score(str(tmp_path), "breadth_participation", {}) is False

    def test_save_handles_corrupt_existing_file(self, tmp_path: Path):
        path = _store_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NOT VALID JSON", encoding="utf-8")
        result = save_model_score(str(tmp_path), "breadth_participation", _model_analysis())
        assert result is True
        store = json.loads(path.read_text(encoding="utf-8"))
        assert store["breadth_participation"]["model_score"] == 72.5


# ═══════════════════════════════════════════════════════════════════
# UNIT    TESTS — load_fresh_scores
# ═══════════════════════════════════════════════════════════════════


class TestLoadFreshScores:
    """Verify freshness filtering and edge cases."""

    def test_load_returns_fresh_entries(self, tmp_path: Path):
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis(60.0))
        fresh = load_fresh_scores(str(tmp_path))
        assert "breadth_participation" in fresh
        assert fresh["breadth_participation"]["model_score"] == 60.0

    def test_load_excludes_stale_entries(self, tmp_path: Path):
        # Save with a timestamp 7 hours ago (beyond 6h default)
        stale_ts = _past_iso(25200)
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis(), as_of=stale_ts)
        fresh = load_fresh_scores(str(tmp_path))
        assert "breadth_participation" not in fresh

    def test_load_custom_max_age(self, tmp_path: Path):
        ts = _past_iso(600)  # 10 min ago
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis(), as_of=ts)
        # With 5 min max age → stale
        assert "breadth_participation" not in load_fresh_scores(str(tmp_path), max_age_seconds=300)
        # With 15 min max age → fresh
        assert "breadth_participation" in load_fresh_scores(str(tmp_path), max_age_seconds=900)

    def test_load_returns_empty_when_no_file(self, tmp_path: Path):
        assert load_fresh_scores(str(tmp_path)) == {}

    def test_load_handles_corrupt_file(self, tmp_path: Path):
        path = _store_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NOT VALID JSON", encoding="utf-8")
        assert load_fresh_scores(str(tmp_path)) == {}

    def test_load_skips_entries_without_captured_at(self, tmp_path: Path):
        path = _store_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"breadth_participation": {"model_score": 60.0}}), encoding="utf-8")
        assert load_fresh_scores(str(tmp_path)) == {}

    def test_load_multiple_engines_mixed_freshness(self, tmp_path: Path):
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis(60.0))
        save_model_score(str(tmp_path), "volatility_options", _model_analysis(45.0), as_of=_past_iso(25200))
        fresh = load_fresh_scores(str(tmp_path))
        assert "breadth_participation" in fresh
        assert "volatility_options" not in fresh


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — load_all_scores
# ═══════════════════════════════════════════════════════════════════


class TestLoadAllScores:
    """Verify load_all_scores returns all entries with freshness metadata."""

    def test_returns_all_entries_with_freshness(self, tmp_path: Path):
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis(60.0))
        save_model_score(str(tmp_path), "volatility_options", _model_analysis(45.0), as_of=_past_iso(25200))
        result = load_all_scores(str(tmp_path))
        assert "breadth_participation" in result
        assert "volatility_options" in result
        assert result["breadth_participation"]["is_fresh"] is True
        assert result["volatility_options"]["is_fresh"] is False

    def test_includes_age_seconds(self, tmp_path: Path):
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis(60.0))
        result = load_all_scores(str(tmp_path))
        assert result["breadth_participation"]["age_seconds"] is not None
        assert result["breadth_participation"]["age_seconds"] >= 0

    def test_stale_entry_has_age_and_score(self, tmp_path: Path):
        stale_ts = _past_iso(25200)  # 7 hours ago
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis(72.5), as_of=stale_ts)
        result = load_all_scores(str(tmp_path))
        entry = result["breadth_participation"]
        assert entry["model_score"] == 72.5
        assert entry["is_fresh"] is False
        assert entry["age_seconds"] >= 25000

    def test_returns_empty_when_no_file(self, tmp_path: Path):
        assert load_all_scores(str(tmp_path)) == {}

    def test_handles_corrupt_file(self, tmp_path: Path):
        path = _store_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("NOT VALID JSON", encoding="utf-8")
        assert load_all_scores(str(tmp_path)) == {}

    def test_handles_entry_without_captured_at(self, tmp_path: Path):
        path = _store_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"bp": {"model_score": 60.0}}), encoding="utf-8")
        result = load_all_scores(str(tmp_path))
        assert "bp" in result
        assert result["bp"]["age_seconds"] is None
        assert result["bp"]["is_fresh"] is False


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — build_snapshot with model_scores
# ═══════════════════════════════════════════════════════════════════


class TestSnapshotModelEnrichment:
    """Verify build_snapshot merges model scores from durable store."""

    def test_snapshot_without_model_scores_has_null(self):
        artifact = _minimal_artifact()
        cards = _engine_cards(artifact)
        snap = build_snapshot(
            artifact=artifact,
            engine_cards=cards,
            composite=artifact["composite"],
            model_status="failed",
            generated_at=artifact["generated_at"],
        )
        for eng in snap["engines"]:
            assert eng["model_score"] is None

    def test_snapshot_with_model_scores_populated(self):
        artifact = _minimal_artifact()
        cards = _engine_cards(artifact)
        model_scores = {
            "breadth_participation": {"model_score": 72.5, "model_label": "BROAD_RALLY", "confidence": 0.8},
            "volatility_options": {"model_score": 45.0, "model_label": "ELEVATED", "confidence": 0.7},
        }
        snap = build_snapshot(
            artifact=artifact,
            engine_cards=cards,
            composite=artifact["composite"],
            model_status="analyzed",
            generated_at=artifact["generated_at"],
            model_scores=model_scores,
        )
        bp = next(e for e in snap["engines"] if e["key"] == "breadth_participation")
        assert bp["model_score"] == 72.5
        vo = next(e for e in snap["engines"] if e["key"] == "volatility_options")
        assert vo["model_score"] == 45.0
        # Engines without model scores remain null
        cam = next(e for e in snap["engines"] if e["key"] == "cross_asset_macro")
        assert cam["model_score"] is None

    def test_snapshot_model_scores_override_card_null(self):
        """model_scores from store take priority over card's null model_score."""
        artifact = _minimal_artifact()
        cards = _engine_cards(artifact)
        # Card has model_score=None (default), store has a value
        model_scores = {
            "flows_positioning": {"model_score": 55.0},
        }
        snap = build_snapshot(
            artifact=artifact,
            engine_cards=cards,
            composite=artifact["composite"],
            model_status="analyzed",
            generated_at=artifact["generated_at"],
            model_scores=model_scores,
        )
        fp = next(e for e in snap["engines"] if e["key"] == "flows_positioning")
        assert fp["model_score"] == 55.0

    def test_snapshot_all_six_engines_with_model_scores(self):
        artifact = _minimal_artifact()
        cards = _engine_cards(artifact)
        model_scores = {
            key: {"model_score": 50.0 + i * 5, "model_label": "MIXED", "confidence": 0.7}
            for i, (key, _) in enumerate(ENGINE_DISPLAY)
        }
        snap = build_snapshot(
            artifact=artifact,
            engine_cards=cards,
            composite=artifact["composite"],
            model_status="analyzed",
            generated_at=artifact["generated_at"],
            model_scores=model_scores,
        )
        for i, eng in enumerate(snap["engines"]):
            assert eng["model_score"] == 50.0 + i * 5

    def test_snapshot_schema_unchanged(self):
        """model_scores param does not alter the schema shape."""
        artifact = _minimal_artifact()
        cards = _engine_cards(artifact)
        snap = build_snapshot(
            artifact=artifact,
            engine_cards=cards,
            composite=artifact["composite"],
            model_status="analyzed",
            generated_at=artifact["generated_at"],
            model_scores={"breadth_participation": {"model_score": 72.5}},
        )
        required = [
            "schema_version", "captured_at", "artifact_id", "generated_at",
            "regime_state", "engines", "model_status",
        ]
        for field in required:
            assert field in snap

    def test_snapshot_backward_compat_no_model_scores_param(self):
        """build_snapshot still works when model_scores is not passed."""
        artifact = _minimal_artifact()
        cards = _engine_cards(artifact)
        snap = build_snapshot(
            artifact=artifact,
            engine_cards=cards,
            composite=artifact["composite"],
            model_status="failed",
            generated_at=artifact["generated_at"],
        )
        assert snap["schema_version"] == 1
        assert len(snap["engines"]) == 6


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — sanitize_model_summary
# ═══════════════════════════════════════════════════════════════════


class TestSanitizeModelSummary:
    """Verify summary hygiene helper."""

    def test_none_returns_none(self):
        assert sanitize_model_summary(None) is None

    def test_empty_string_returns_none(self):
        assert sanitize_model_summary("") is None

    def test_whitespace_only_returns_none(self):
        assert sanitize_model_summary("   \n\t  ") is None

    def test_normal_text_preserved(self):
        assert sanitize_model_summary("Market is bullish.") == "Market is bullish."

    def test_collapses_whitespace(self):
        assert sanitize_model_summary("Market  is   \n bullish.") == "Market is bullish."

    def test_truncates_long_text(self):
        long = "x " * (MAX_SUMMARY_LENGTH + 100)
        result = sanitize_model_summary(long)
        assert result is not None
        assert len(result) <= MAX_SUMMARY_LENGTH + 5  # account for "…"
        assert result.endswith("…")

    def test_non_string_returns_none(self):
        assert sanitize_model_summary(42) is None
        assert sanitize_model_summary(["list"]) is None


# ═══════════════════════════════════════════════════════════════════
# UNIT TESTS — model_summary save/load round-trip
# ═══════════════════════════════════════════════════════════════════


class TestModelSummaryPersistence:
    """Verify model_summary saves to and loads from the durable store."""

    def test_summary_persisted_on_save(self, tmp_path: Path):
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis())
        store = json.loads(_store_path(tmp_path).read_text(encoding="utf-8"))
        assert store["breadth_participation"]["model_summary"] == "Test summary."

    def test_summary_loaded_via_load_all(self, tmp_path: Path):
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis())
        result = load_all_scores(str(tmp_path))
        assert result["breadth_participation"]["model_summary"] == "Test summary."

    def test_summary_loaded_via_load_fresh(self, tmp_path: Path):
        save_model_score(str(tmp_path), "breadth_participation", _model_analysis())
        result = load_fresh_scores(str(tmp_path))
        assert result["breadth_participation"]["model_summary"] == "Test summary."

    def test_missing_summary_backward_compat(self, tmp_path: Path):
        """Old entries without model_summary still load safely."""
        path = _store_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        old_entry = {
            "breadth_participation": {
                "model_score": 72.5,
                "model_label": "BROAD_RALLY",
                "confidence": 0.8,
                "captured_at": _now_iso(),
            }
        }
        path.write_text(json.dumps(old_entry), encoding="utf-8")
        result = load_all_scores(str(tmp_path))
        assert "breadth_participation" in result
        assert result["breadth_participation"]["model_score"] == 72.5
        # model_summary absent — should not raise, just be missing from dict
        assert result["breadth_participation"].get("model_summary") is None

    def test_none_summary_saved_when_absent(self, tmp_path: Path):
        analysis = {"score": 72.5, "label": "BROAD_RALLY", "confidence": 0.8}
        save_model_score(str(tmp_path), "breadth_participation", analysis)
        store = json.loads(_store_path(tmp_path).read_text(encoding="utf-8"))
        assert store["breadth_participation"]["model_summary"] is None
