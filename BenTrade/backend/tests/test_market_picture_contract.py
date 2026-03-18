"""Tests for the Market Picture contract — normalised engine card builder.

Covers:
  - normalize_engine_card for all status combinations
  - build_engine_cards stable ordering and completeness
  - engine_status normalisation (_resolve_engine_status)
  - model_status derivation (_resolve_model_status)
  - backward-compat: legacy "status" alias mirrors engine_status
"""

from __future__ import annotations

import pytest

from app.services.market_picture_contract import (
    ENGINE_DISPLAY,
    build_engine_cards,
    normalize_engine_card,
    _resolve_engine_status,
    _resolve_model_status,
)


# ── Fixtures ──

def _engine_data(
    score: float = 68.5,
    label: str = "NEUTRAL",
    summary: str = "Test engine summary.",
    confidence: float = 0.75,
    engine_status: str = "ok",
) -> dict:
    return {
        "score": score,
        "short_label": label,
        "summary": summary,
        "confidence": confidence,
        "engine_status": engine_status,
    }


def _model_entry(
    model_score: float = 72.0,
    model_label: str = "BROAD_RALLY",
    confidence: float = 0.80,
    model_summary: str = "Model summary text.",
    captured_at: str = "2025-06-01T12:00:00+00:00",
    is_fresh: bool = True,
) -> dict:
    return {
        "model_score": model_score,
        "model_label": model_label,
        "confidence": confidence,
        "model_summary": model_summary,
        "captured_at": captured_at,
        "is_fresh": is_fresh,
    }


# ── _resolve_engine_status ──

class TestResolveEngineStatus:
    def test_none_defaults_ok(self):
        assert _resolve_engine_status(None) == "ok"

    def test_known_values(self):
        assert _resolve_engine_status("ok") == "ok"
        assert _resolve_engine_status("missing") == "missing"
        assert _resolve_engine_status("degraded") == "degraded"

    def test_unknown_maps_to_degraded(self):
        assert _resolve_engine_status("error") == "degraded"
        assert _resolve_engine_status("partial") == "degraded"

    def test_case_insensitive(self):
        assert _resolve_engine_status("OK") == "ok"
        assert _resolve_engine_status("Missing") == "missing"

    def test_whitespace_stripped(self):
        assert _resolve_engine_status("  ok  ") == "ok"


# ── _resolve_model_status ──

class TestResolveModelStatus:
    def test_missing_when_score_is_none(self):
        assert _resolve_model_status(None, True) == "missing"
        assert _resolve_model_status(None, False) == "missing"

    def test_fresh_when_score_present_and_fresh(self):
        assert _resolve_model_status(72.0, True) == "fresh"

    def test_stale_when_score_present_and_not_fresh(self):
        assert _resolve_model_status(72.0, False) == "stale"

    def test_zero_score_counts_as_present(self):
        assert _resolve_model_status(0.0, True) == "fresh"


# ── normalize_engine_card ──

class TestNormalizeEngineCard:
    def test_full_card_all_present(self):
        card = normalize_engine_card("breadth_participation", "Breadth", _engine_data(), _model_entry())
        assert card["key"] == "breadth_participation"
        assert card["name"] == "Breadth"
        assert card["engine_score"] == 68.5
        assert card["engine_label"] == "NEUTRAL"
        assert card["engine_summary"] == "Test engine summary."
        assert card["model_score"] == 72.0
        assert card["model_label"] == "BROAD_RALLY"
        assert card["model_summary"] == "Model summary text."
        assert card["model_captured_at"] == "2025-06-01T12:00:00+00:00"
        assert card["model_fresh"] is True
        assert card["confidence"] == 0.75
        assert card["engine_status"] == "ok"
        assert card["model_status"] == "fresh"
        assert card["status"] == "ok"  # legacy alias

    def test_missing_engine(self):
        card = normalize_engine_card("test_key", "Test", None, _model_entry())
        assert card["engine_score"] is None
        assert card["engine_label"] is None
        assert card["engine_summary"] is None
        assert card["confidence"] is None
        assert card["engine_status"] == "missing"
        assert card["status"] == "missing"
        # Model still present
        assert card["model_score"] == 72.0
        assert card["model_status"] == "fresh"

    def test_missing_model(self):
        card = normalize_engine_card("test_key", "Test", _engine_data(), None)
        assert card["engine_score"] == 68.5
        assert card["model_score"] is None
        assert card["model_label"] is None
        assert card["model_summary"] is None
        assert card["model_captured_at"] is None
        assert card["model_fresh"] is False
        assert card["model_status"] == "missing"

    def test_stale_model(self):
        entry = _model_entry(is_fresh=False)
        card = normalize_engine_card("test_key", "Test", _engine_data(), entry)
        assert card["model_fresh"] is False
        assert card["model_status"] == "stale"

    def test_both_missing(self):
        card = normalize_engine_card("test_key", "Test", None, None)
        assert card["engine_status"] == "missing"
        assert card["model_status"] == "missing"
        assert card["engine_score"] is None
        assert card["model_score"] is None

    def test_engine_label_falls_back_to_label(self):
        eng = {"score": 50.0, "label": "FALLBACK_LABEL", "summary": "s", "confidence": 0.5}
        card = normalize_engine_card("k", "N", eng, None)
        assert card["engine_label"] == "FALLBACK_LABEL"

    def test_engine_short_label_preferred(self):
        eng = {"score": 50.0, "short_label": "SHORT", "label": "LONG", "summary": "s", "confidence": 0.5}
        card = normalize_engine_card("k", "N", eng, None)
        assert card["engine_label"] == "SHORT"

    def test_degraded_engine_status(self):
        eng = _engine_data(engine_status="partial_failure")
        card = normalize_engine_card("k", "N", eng, None)
        assert card["engine_status"] == "degraded"
        assert card["status"] == "degraded"

    def test_model_fresh_derived_from_model_status(self):
        """model_fresh must always equal (model_status == 'fresh')."""
        card_fresh = normalize_engine_card("k", "N", None, _model_entry(is_fresh=True))
        assert card_fresh["model_fresh"] is True
        assert card_fresh["model_status"] == "fresh"

        card_stale = normalize_engine_card("k", "N", None, _model_entry(is_fresh=False))
        assert card_stale["model_fresh"] is False
        assert card_stale["model_status"] == "stale"

        card_missing = normalize_engine_card("k", "N", None, None)
        assert card_missing["model_fresh"] is False
        assert card_missing["model_status"] == "missing"


# ── build_engine_cards ──

class TestBuildEngineCards:
    def test_returns_all_six_engines_in_order(self):
        raw_engines = {key: _engine_data() for key, _ in ENGINE_DISPLAY}
        cards = build_engine_cards(raw_engines, {})
        assert len(cards) == 6
        assert [c["key"] for c in cards] == [k for k, _ in ENGINE_DISPLAY]

    def test_missing_engines_still_produce_cards(self):
        cards = build_engine_cards({}, {})
        assert len(cards) == 6
        assert all(c["engine_status"] == "missing" for c in cards)
        assert all(c["model_status"] == "missing" for c in cards)

    def test_model_scores_hydrated(self):
        raw_engines = {"breadth_participation": _engine_data()}
        model_scores = {"breadth_participation": _model_entry()}
        cards = build_engine_cards(raw_engines, model_scores)
        bp = cards[0]
        assert bp["model_score"] == 72.0
        assert bp["model_status"] == "fresh"

    def test_partial_data(self):
        """Only some engines have data; all 6 cards still produced."""
        raw_engines = {"breadth_participation": _engine_data()}
        model_scores = {"volatility_options": _model_entry()}
        cards = build_engine_cards(raw_engines, model_scores)
        assert len(cards) == 6
        bp = next(c for c in cards if c["key"] == "breadth_participation")
        assert bp["engine_score"] == 68.5
        assert bp["model_status"] == "missing"
        vo = next(c for c in cards if c["key"] == "volatility_options")
        assert vo["engine_status"] == "missing"
        assert vo["model_score"] == 72.0


# ── Backward compatibility: ENGINE_DISPLAY re-export ──

class TestEngineDisplayReExport:
    def test_routes_re_exports_engine_display(self):
        from app.api.routes_market_picture import ENGINE_DISPLAY as route_ed
        assert route_ed is ENGINE_DISPLAY

    def test_has_six_entries(self):
        assert len(ENGINE_DISPLAY) == 6

    def test_all_keys_are_strings(self):
        for key, name in ENGINE_DISPLAY:
            assert isinstance(key, str)
            assert isinstance(name, str)
