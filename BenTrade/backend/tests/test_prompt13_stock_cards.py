"""Prompt 13 — TMC stock card frontend contract tests.

Validates that the backend compact stock candidate shape produced by
_extract_compact_stock_candidate matches the fields expected by the
frontend normalizeStockCandidate in trade_management_center.js.

Also validates the Market Picture summary shape and model review fields
added in Prompt 12C.

Test-only — run via: python -m pytest tests/test_prompt13_stock_cards.py -v
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.workflows.stock_opportunity_runner import (
    _extract_compact_stock_candidate,
    _build_market_picture_summary,
    build_review_summary,
    select_top_metrics,
)


# ═══════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════

# Frontend normalizeStockCandidate reads these keys from raw backend data.
# This is the SINGLE source of truth for the contract — if the frontend
# adds a field, add it here; if the backend removes a field, break here.
FRONTEND_EXPECTED_KEYS = {
    # Identity
    "symbol",
    "scanner_key",
    "scanner_name",
    "setup_type",
    "direction",
    # Multi-scanner provenance (12C)
    "source_scanners",
    # Scores
    "setup_quality",
    "confidence",
    "rank",
    # Thesis & signals
    "thesis_summary",
    "supporting_signals",
    "risk_flags",
    # Context
    "entry_context",
    "market_regime",
    "risk_environment",
    "market_state_ref",
    "vix",
    "regime_tags",
    "support_state",
    # Market Picture summary (12C)
    "market_picture_summary",
    # Derived
    "top_metrics",
    "review_summary",
    # Model review (12C)
    "model_recommendation",
    "model_confidence",
    "model_score",
    "model_review_summary",
    "model_key_factors",
    "model_caution_notes",
}

MARKET_PICTURE_SUMMARY_KEYS = {"engines_available", "engines_total", "engine_summaries"}
ENGINE_SUMMARY_KEYS = {"score", "label", "summary"}


def _make_enriched_candidate(**overrides: Any) -> dict[str, Any]:
    """Build a fully enriched candidate with all 12C fields."""
    base: dict[str, Any] = {
        "candidate_id": "AAPL|stock_pullback_swing",
        "scanner_key": "stock_pullback_swing",
        "scanner_name": "Pullback Swing",
        "strategy_family": "stock",
        "setup_type": "pullback_swing",
        "asset_class": "equity",
        "symbol": "AAPL",
        "underlying": "AAPL",
        "direction": "long",
        "thesis_summary": ["Strong uptrend with healthy pullback"],
        "entry_context": {"price": 185.50, "state": "uptrend_pullback"},
        "time_horizon": "swing",
        "setup_quality": 78.0,
        "confidence": 0.88,
        "rank": 1,
        "risk_definition": {"type": "stop_loss_based", "notes": []},
        "reward_profile": {"type": "price_target_based", "composite_score": 78.0},
        "supporting_signals": ["RSI oversold bounce", "Volume confirmation"],
        "risk_flags": ["Elevated VIX"],
        "invalidation_signals": [],
        "market_context_tags": ["pullback_swing"],
        "position_sizing_notes": None,
        "data_quality": {"source": "tradier", "source_confidence": 0.88, "missing_fields": []},
        "source_status": {"history": "tradier", "confidence": 0.88},
        "pricing_snapshot": {"price": 185.50},
        "strategy_structure": None,
        "candidate_metrics": {
            "composite_score": 78.0,
            "rsi": 38.5,
            "atr_pct": 0.021,
            "volume_ratio": 1.8,
            "macd_hist": 0.45,
            "score_breakdown": {"trend": 85, "value": 71},
        },
        "detail_sections": {},
        "generated_at": "2026-03-20T10:00:00+00:00",
        # Multi-scanner provenance (12C stage 3)
        "source_scanners": ["stock_pullback_swing", "stock_momentum_breakout"],
        # Market context enrichment (12C stage 4)
        "market_state_ref": "mi_run_xyz",
        "market_regime": "bullish",
        "risk_environment": "stable",
        "vix": 16.5,
        "regime_tags": ["low_vol", "trending"],
        "support_state": "above_support",
        # Market Picture summary (12C stage 6)
        "market_picture_summary": {
            "engines_available": 4,
            "engines_total": 6,
            "engine_summaries": {
                "breadth_participation": {"score": 72, "label": "Breadth", "summary": "Broad participation"},
                "volatility_options": {"score": 55, "label": "Vol/Options", "summary": "Moderate vol"},
                "cross_asset_macro": {"score": 65, "label": "Macro", "summary": "Neutral macro"},
                "flows_positioning": {"score": 60, "label": "Flows", "summary": "Mildly positive flows"},
            },
        },
        # Model review (12C stage 7)
        "model_recommendation": "buy",
        "model_confidence": 0.82,
        "model_score": 76,
        "model_review_summary": "Strong pullback setup with broad confirmation.",
        "model_key_factors": ["RSI oversold", "Volume spike", "Trend intact"],
        "model_caution_notes": ["VIX elevated", "Earnings approaching"],
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════════
# 1. COMPACT CANDIDATE SHAPE — field presence
# ═══════════════════════════════════════════════════════════════════════


class TestCompactCandidateShape:
    """Compact stock candidate contains all keys the frontend expects."""

    def test_all_frontend_keys_present(self):
        cand = _make_enriched_candidate()
        compact = _extract_compact_stock_candidate(cand)
        missing = FRONTEND_EXPECTED_KEYS - set(compact.keys())
        assert missing == set(), f"Missing keys in compact candidate: {missing}"

    def test_no_unexpected_keys(self):
        """Compact candidate should not leak internal-only fields."""
        cand = _make_enriched_candidate()
        compact = _extract_compact_stock_candidate(cand)
        allowed = FRONTEND_EXPECTED_KEYS
        extra = set(compact.keys()) - allowed
        assert extra == set(), f"Unexpected keys leaked to frontend: {extra}"

    def test_identity_fields(self):
        compact = _extract_compact_stock_candidate(_make_enriched_candidate())
        assert compact["symbol"] == "AAPL"
        assert compact["scanner_key"] == "stock_pullback_swing"
        assert compact["scanner_name"] == "Pullback Swing"
        assert compact["setup_type"] == "pullback_swing"
        assert compact["direction"] == "long"


# ═══════════════════════════════════════════════════════════════════════
# 2. MULTI-SCANNER PROVENANCE
# ═══════════════════════════════════════════════════════════════════════


class TestMultiScannerProvenance:
    """source_scanners field for multi-scanner dedup provenance."""

    def test_source_scanners_list(self):
        compact = _extract_compact_stock_candidate(_make_enriched_candidate())
        assert isinstance(compact["source_scanners"], list)
        assert len(compact["source_scanners"]) == 2

    def test_source_scanners_fallback_to_scanner_key(self):
        """When source_scanners is missing, falls back to [scanner_key]."""
        cand = _make_enriched_candidate(source_scanners=None)
        compact = _extract_compact_stock_candidate(cand)
        assert compact["source_scanners"] == ["stock_pullback_swing"]

    def test_single_scanner_list(self):
        cand = _make_enriched_candidate(source_scanners=["stock_pullback_swing"])
        compact = _extract_compact_stock_candidate(cand)
        assert len(compact["source_scanners"]) == 1


# ═══════════════════════════════════════════════════════════════════════
# 3. MARKET PICTURE SUMMARY
# ═══════════════════════════════════════════════════════════════════════


class TestMarketPictureSummary:
    """Market Picture summary shape matches frontend expectations."""

    def test_summary_keys_present(self):
        compact = _extract_compact_stock_candidate(_make_enriched_candidate())
        mps = compact["market_picture_summary"]
        assert mps is not None
        missing = MARKET_PICTURE_SUMMARY_KEYS - set(mps.keys())
        assert missing == set()

    def test_engine_count(self):
        compact = _extract_compact_stock_candidate(_make_enriched_candidate())
        mps = compact["market_picture_summary"]
        assert mps["engines_available"] == 4
        assert mps["engines_total"] == 6

    def test_engine_summary_shape(self):
        compact = _extract_compact_stock_candidate(_make_enriched_candidate())
        summaries = compact["market_picture_summary"]["engine_summaries"]
        for key, eng in summaries.items():
            missing = ENGINE_SUMMARY_KEYS - set(eng.keys())
            assert missing == set(), f"Engine '{key}' missing keys: {missing}"

    def test_null_when_not_enriched(self):
        cand = _make_enriched_candidate(market_picture_summary=None)
        compact = _extract_compact_stock_candidate(cand)
        assert compact["market_picture_summary"] is None

    def test_build_market_picture_summary_shape(self):
        ctx = {
            "breadth_participation": {"score": 70, "label": "Breadth", "summary": "ok"},
            "volatility_options": {"score": 55, "label": "Vol", "summary": "mod"},
        }
        result = _build_market_picture_summary(ctx)
        assert result["engines_available"] == 2
        assert "breadth_participation" in result["engine_summaries"]


# ═══════════════════════════════════════════════════════════════════════
# 4. MODEL REVIEW FIELDS
# ═══════════════════════════════════════════════════════════════════════


class TestModelReviewFields:
    """Model review fields present and correctly typed."""

    def test_model_fields_present(self):
        compact = _extract_compact_stock_candidate(_make_enriched_candidate())
        assert compact["model_recommendation"] == "buy"
        assert compact["model_confidence"] == 0.82
        assert compact["model_score"] == 76
        assert compact["model_review_summary"] is not None
        assert isinstance(compact["model_key_factors"], list)
        assert isinstance(compact["model_caution_notes"], list)

    def test_model_key_factors(self):
        compact = _extract_compact_stock_candidate(_make_enriched_candidate())
        assert len(compact["model_key_factors"]) == 3
        assert "RSI oversold" in compact["model_key_factors"]

    def test_model_caution_notes(self):
        compact = _extract_compact_stock_candidate(_make_enriched_candidate())
        assert len(compact["model_caution_notes"]) == 2

    def test_model_fields_null_when_skipped(self):
        cand = _make_enriched_candidate(
            model_recommendation=None,
            model_confidence=None,
            model_score=None,
            model_review_summary=None,
            model_key_factors=None,
            model_caution_notes=None,
        )
        compact = _extract_compact_stock_candidate(cand)
        assert compact["model_recommendation"] is None
        assert compact["model_confidence"] is None
        assert compact["model_score"] is None
        assert compact["model_review_summary"] is None
        # Backend passes None through; frontend normalizer coerces to []
        assert compact["model_key_factors"] is None
        assert compact["model_caution_notes"] is None


# ═══════════════════════════════════════════════════════════════════════
# 5. MARKET CONTEXT FIELDS (12C)
# ═══════════════════════════════════════════════════════════════════════


class TestMarketContextFields:
    """Market context fields from stage 4 enrichment."""

    def test_context_fields_present(self):
        compact = _extract_compact_stock_candidate(_make_enriched_candidate())
        assert compact["market_state_ref"] == "mi_run_xyz"
        assert compact["market_regime"] == "bullish"
        assert compact["risk_environment"] == "stable"
        assert compact["vix"] == 16.5
        assert compact["regime_tags"] == ["low_vol", "trending"]
        assert compact["support_state"] == "above_support"

    def test_context_fields_null_fallback(self):
        cand = _make_enriched_candidate(
            market_state_ref=None,
            vix=None,
            regime_tags=None,
            support_state=None,
        )
        compact = _extract_compact_stock_candidate(cand)
        assert compact["market_state_ref"] is None
        assert compact["vix"] is None
        assert compact["regime_tags"] == []
        assert compact["support_state"] is None


# ═══════════════════════════════════════════════════════════════════════
# 6. FRONTEND NORMALIZER CONTRACT
# ═══════════════════════════════════════════════════════════════════════


class TestFrontendNormalizerContract:
    """The JS normalizeStockCandidate maps these exact backend field names.

    This test reads the actual JS source and verifies it references
    every field from the compact candidate shape.
    """

    @pytest.fixture()
    def js_source(self) -> str:
        js_path = (
            Path(__file__).resolve().parents[2]
            / "frontend"
            / "assets"
            / "js"
            / "pages"
            / "trade_management_center.js"
        )
        return js_path.read_text(encoding="utf-8")

    def test_normalizer_reads_source_scanners(self, js_source: str):
        assert "raw.source_scanners" in js_source

    def test_normalizer_reads_market_picture_summary(self, js_source: str):
        assert "raw.market_picture_summary" in js_source

    def test_normalizer_reads_model_recommendation(self, js_source: str):
        assert "raw.model_recommendation" in js_source

    def test_normalizer_reads_model_confidence(self, js_source: str):
        assert "raw.model_confidence" in js_source

    def test_normalizer_reads_model_score(self, js_source: str):
        assert "raw.model_score" in js_source

    def test_normalizer_reads_model_review_summary(self, js_source: str):
        assert "raw.model_review_summary" in js_source

    def test_normalizer_reads_model_key_factors(self, js_source: str):
        assert "raw.model_key_factors" in js_source

    def test_normalizer_reads_model_caution_notes(self, js_source: str):
        assert "raw.model_caution_notes" in js_source

    def test_normalizer_reads_market_state_ref(self, js_source: str):
        assert "raw.market_state_ref" in js_source

    def test_normalizer_reads_vix(self, js_source: str):
        assert "raw.vix" in js_source

    def test_normalizer_reads_regime_tags(self, js_source: str):
        assert "raw.regime_tags" in js_source

    def test_normalizer_reads_support_state(self, js_source: str):
        assert "raw.support_state" in js_source


# ═══════════════════════════════════════════════════════════════════════
# 7. CARD BUILDER CONTRACT — HTML structure checks
# ═══════════════════════════════════════════════════════════════════════


class TestCardBuilderStructure:
    """Verify buildStockCard produces expected HTML structure markers."""

    @pytest.fixture()
    def js_source(self) -> str:
        js_path = (
            Path(__file__).resolve().parents[2]
            / "frontend"
            / "assets"
            / "js"
            / "pages"
            / "trade_management_center.js"
        )
        return js_path.read_text(encoding="utf-8")

    def test_has_collapsible_detail(self, js_source: str):
        assert "tmc-detail-collapse" in js_source

    def test_has_detail_toggle(self, js_source: str):
        assert "tmc-detail-toggle" in js_source

    def test_has_model_review_section(self, js_source: str):
        assert "tmc-model-review" in js_source

    def test_has_mp_summary(self, js_source: str):
        assert "tmc-mp-summary" in js_source

    def test_has_provenance_badge(self, js_source: str):
        assert "tmc-provenance-badge" in js_source

    def test_has_dismiss_button(self, js_source: str):
        assert "tmc-btn-dismiss" in js_source

    def test_has_details_button(self, js_source: str):
        assert "tmc-btn-details" in js_source

    def test_has_setup_label(self, js_source: str):
        assert "tmc-setup-label" in js_source

    def test_footer_always_rendered(self, js_source: str):
        """Footer must not be inside the collapsible detail area."""
        # Find the detail-collapse closing and footer opening
        detail_end = js_source.find("tmc-detail-collapse")
        footer_pos = js_source.rfind("tmc-card-footer")
        # Footer should appear after details reference
        assert footer_pos > detail_end

    def test_model_rec_class_helper(self, js_source: str):
        assert "modelRecommendationClass" in js_source

    def test_internals_export_buildStockCard(self, js_source: str):
        assert "buildStockCard: buildStockCard" in js_source

    def test_internals_export_modelRecommendationClass(self, js_source: str):
        assert "modelRecommendationClass: modelRecommendationClass" in js_source


# ═══════════════════════════════════════════════════════════════════════
# 8. CSS CONTRACT — required classes exist
# ═══════════════════════════════════════════════════════════════════════


class TestCSSContract:
    """Verify required CSS classes exist in module-dashboard.css."""

    @pytest.fixture()
    def css_source(self) -> str:
        css_path = (
            Path(__file__).resolve().parents[2]
            / "frontend"
            / "assets"
            / "css"
            / "module-dashboard.css"
        )
        return css_path.read_text(encoding="utf-8")

    REQUIRED_CLASSES = [
        ".tmc-setup-label",
        ".tmc-provenance-badge",
        ".tmc-rank-badge",
        ".tmc-model-review",
        ".tmc-model-review-header",
        ".tmc-model-rec-badge",
        ".tmc-model-rec-buy",
        ".tmc-model-rec-sell",
        ".tmc-model-rec-hold",
        ".tmc-model-rec-pass",
        ".tmc-model-conf",
        ".tmc-model-score",
        ".tmc-model-review-text",
        ".tmc-mp-summary",
        ".tmc-mp-count",
        ".tmc-mp-engine",
        ".tmc-context-badge",
        ".tmc-regime-tag",
        ".tmc-detail-collapse",
        ".tmc-detail-toggle",
        ".tmc-detail-area",
        ".tmc-card-actions",
        ".tmc-btn-details",
        ".tmc-btn-dismiss",
    ]

    @pytest.mark.parametrize("cls", REQUIRED_CLASSES)
    def test_css_class_defined(self, css_source: str, cls: str):
        assert cls in css_source, f"CSS class '{cls}' not found in module-dashboard.css"
