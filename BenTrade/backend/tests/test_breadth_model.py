"""Tests for breadth model analysis (LLM layer) and service model method.

Covers:
  - _extract_breadth_raw_evidence: field inclusion/exclusion
  - _coerce_breadth_model_output: normalization, clamping, missing fields
  - BreadthService.run_model_analysis: cache behavior, error handling
  - Route registration for POST /api/breadth-participation/model
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ── Evidence Extraction ─────────────────────────────────────────


class TestExtractBreadthRawEvidence:
    """Verify raw evidence excludes derived fields and includes only raw inputs."""

    def _make_engine_result(self):
        return {
            "engine": "breadth_participation",
            "score": 55.0,
            "label": "Mixed Breadth",
            "short_label": "Mixed",
            "summary": "Breadth is mixed.",
            "confidence_score": 72.0,
            "signal_quality": "medium",
            "positive_contributors": ["Volume is constructive"],
            "negative_contributors": ["Trend is weak"],
            "conflicting_signals": ["Something conflicts"],
            "trader_takeaway": "Hold steady.",
            "pillar_scores": {
                "participation_breadth": 45.0,
                "trend_breadth": 30.0,
                "volume_breadth": 60.0,
                "leadership_quality": 50.0,
                "participation_stability": 55.0,
            },
            "pillar_weights": {
                "participation_breadth": 25,
                "trend_breadth": 25,
                "volume_breadth": 20,
                "leadership_quality": 20,
                "participation_stability": 10,
            },
            "raw_inputs": {
                "participation": {"advancing": 80, "declining": 57},
                "trend": {"pct_above_200dma": 0.55},
                "volume": {"up_volume": 1e9, "down_volume": 8e8},
                "leadership": {"ew_return": 0.01, "cw_return": 0.015},
                "stability": {"breadth_streak_days": 3},
            },
            "universe": {"name": "SP500_Proxy", "coverage_pct": 98.0},
            "warnings": ["Some warning"],
            "missing_inputs": ["some_missing"],
        }

    def test_includes_raw_inputs(self):
        from common.model_analysis import _extract_breadth_raw_evidence
        evidence = _extract_breadth_raw_evidence(self._make_engine_result())
        assert "raw_inputs" in evidence
        assert "participation" in evidence["raw_inputs"]
        assert "trend" in evidence["raw_inputs"]
        assert "volume" in evidence["raw_inputs"]
        assert "leadership" in evidence["raw_inputs"]
        assert "stability" in evidence["raw_inputs"]

    def test_includes_pillar_scores(self):
        from common.model_analysis import _extract_breadth_raw_evidence
        evidence = _extract_breadth_raw_evidence(self._make_engine_result())
        assert "pillar_scores" in evidence
        assert evidence["pillar_scores"]["participation_breadth"] == 45.0

    def test_includes_universe(self):
        from common.model_analysis import _extract_breadth_raw_evidence
        evidence = _extract_breadth_raw_evidence(self._make_engine_result())
        assert evidence["universe"]["coverage_pct"] == 98.0

    def test_includes_warnings_and_missing(self):
        from common.model_analysis import _extract_breadth_raw_evidence
        evidence = _extract_breadth_raw_evidence(self._make_engine_result())
        assert len(evidence["warnings"]) == 1
        assert len(evidence["missing_inputs"]) == 1

    def test_excludes_derived_score(self):
        from common.model_analysis import _extract_breadth_raw_evidence
        evidence = _extract_breadth_raw_evidence(self._make_engine_result())
        assert "score" not in evidence
        assert "label" not in evidence
        assert "summary" not in evidence
        assert "trader_takeaway" not in evidence
        assert "positive_contributors" not in evidence
        assert "negative_contributors" not in evidence
        assert "conflicting_signals" not in evidence
        assert "confidence_score" not in evidence
        assert "signal_quality" not in evidence

    def test_handles_empty_engine_result(self):
        from common.model_analysis import _extract_breadth_raw_evidence
        evidence = _extract_breadth_raw_evidence({})
        assert evidence["raw_inputs"]["participation"] == {}
        assert evidence["pillar_scores"] == {}
        assert evidence["universe"] == {}


# ── Output Coercion ──────────────────────────────────────────────


class TestCoerceBreadthModelOutput:
    """Verify LLM output normalization/validation."""

    def test_valid_output(self):
        from common.model_analysis import _coerce_breadth_model_output
        raw = {
            "label": "WEAK",
            "score": 32.5,
            "confidence": 0.7,
            "summary": "Breadth is weak.",
            "pillar_analysis": {"participation": "Low A/D ratio"},
            "breadth_drivers": {
                "constructive_factors": ["Volume OK"],
                "warning_factors": ["Trend weak"],
                "conflicting_factors": [],
            },
            "market_implications": {
                "directional_bias": "bearish",
                "position_sizing": "reduce",
                "strategy_recommendation": "iron condors",
                "risk_level": "elevated",
                "sector_tilt": "defensives",
            },
            "uncertainty_flags": ["Low coverage"],
            "trader_takeaway": "Be cautious.",
        }
        result = _coerce_breadth_model_output(raw)
        assert result is not None
        assert result["label"] == "WEAK"
        assert result["score"] == 32.5
        assert result["confidence"] == 0.7
        assert result["summary"] == "Breadth is weak."
        assert result["trader_takeaway"] == "Be cautious."
        assert len(result["breadth_drivers"]["constructive_factors"]) == 1
        assert result["market_implications"]["risk_level"] == "elevated"

    def test_clamps_score(self):
        from common.model_analysis import _coerce_breadth_model_output
        result = _coerce_breadth_model_output({
            "label": "STRONG", "score": 150, "confidence": 1.5,
            "summary": "Test"
        })
        assert result["score"] == 100.0
        assert result["confidence"] == 1.0

    def test_clamps_negative_score(self):
        from common.model_analysis import _coerce_breadth_model_output
        result = _coerce_breadth_model_output({
            "label": "WEAK", "score": -10, "confidence": -0.5,
            "summary": "Test"
        })
        assert result["score"] == 0.0
        assert result["confidence"] == 0.0

    def test_returns_none_for_missing_label(self):
        from common.model_analysis import _coerce_breadth_model_output
        assert _coerce_breadth_model_output({"score": 50, "summary": "X"}) is None

    def test_returns_none_for_missing_score(self):
        from common.model_analysis import _coerce_breadth_model_output
        assert _coerce_breadth_model_output({"label": "WEAK", "summary": "X"}) is None

    def test_returns_none_for_missing_summary(self):
        from common.model_analysis import _coerce_breadth_model_output
        assert _coerce_breadth_model_output({"label": "WEAK", "score": 50}) is None

    def test_returns_none_for_non_dict(self):
        from common.model_analysis import _coerce_breadth_model_output
        assert _coerce_breadth_model_output("not a dict") is None
        assert _coerce_breadth_model_output(None) is None

    def test_returns_none_for_invalid_score(self):
        from common.model_analysis import _coerce_breadth_model_output
        assert _coerce_breadth_model_output({
            "label": "WEAK", "score": "abc", "summary": "X"
        }) is None

    def test_defaults_confidence(self):
        from common.model_analysis import _coerce_breadth_model_output
        result = _coerce_breadth_model_output({
            "label": "WEAK", "score": 40, "summary": "X"
        })
        assert result["confidence"] == 0.5

    def test_empty_breadth_drivers(self):
        from common.model_analysis import _coerce_breadth_model_output
        result = _coerce_breadth_model_output({
            "label": "WEAK", "score": 40, "summary": "X"
        })
        assert result["breadth_drivers"]["constructive_factors"] == []
        assert result["breadth_drivers"]["warning_factors"] == []

    def test_label_uppercased(self):
        from common.model_analysis import _coerce_breadth_model_output
        result = _coerce_breadth_model_output({
            "label": "narrow_rally", "score": 45, "summary": "X"
        })
        assert result["label"] == "NARROW_RALLY"


# ── Route Registration ───────────────────────────────────────────


class TestBreadthRouteRegistration:
    """Verify all breadth routes including model endpoint are registered."""

    @pytest.fixture(scope="class")
    def routes(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        client = TestClient(create_app())
        paths = client.get("/openapi.json").json().get("paths", {})
        return [p for p in paths if "breadth" in p]

    def test_engine_route_registered(self, routes):
        assert "/api/breadth-participation" in routes

    def test_engine_only_route_registered(self, routes):
        assert "/api/breadth-participation/engine" in routes

    def test_model_route_registered(self, routes):
        assert "/api/breadth-participation/model" in routes


# ── Service Cache Fix Verification ───────────────────────────────


class TestBreadthServiceCacheAwait:
    """Verify the cache.get/set are properly awaited in the service."""

    def test_cache_get_is_awaited(self):
        """The cached value should be returned (not a coroutine)."""
        import asyncio
        from app.services.breadth_service import BreadthService

        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value={"engine_result": {"score": 50}, "as_of": "now"})
        mock_provider = MagicMock()

        service = BreadthService(data_provider=mock_provider, cache=mock_cache)
        result = asyncio.run(service.get_breadth_analysis(force=False))

        mock_cache.get.assert_awaited_once()
        assert isinstance(result, dict)
        assert result["engine_result"]["score"] == 50

    def test_model_cache_get_is_awaited(self):
        """Model analysis should await cache correctly."""
        import asyncio
        from app.services.breadth_service import BreadthService

        cached_model = {"model_analysis": {"score": 45}, "as_of": "now"}
        mock_cache = AsyncMock()
        mock_cache.get = AsyncMock(return_value=cached_model)
        mock_provider = MagicMock()

        service = BreadthService(data_provider=mock_provider, cache=mock_cache)
        result = asyncio.run(service.run_model_analysis(force=False))

        assert isinstance(result, dict)
        assert result["model_analysis"]["score"] == 45
