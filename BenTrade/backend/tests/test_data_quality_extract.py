"""Tests for extract_value, extract_quality, and build_data_quality_summary
in data_quality_utils.

Covers:
  - extract_value: None, bare int/float, dict envelope, missing 'value' key
  - extract_quality: None, bare scalar, dict with/without observation_date
  - build_data_quality_summary: multiple metrics, _summary aggregation
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.data_quality_utils import (
    build_data_quality_summary,
    extract_quality,
    extract_value,
)


# ── extract_value ────────────────────────────────────────────────────

class TestExtractValue:
    def test_none_returns_none(self):
        assert extract_value(None) is None

    def test_int(self):
        assert extract_value(42) == 42.0

    def test_float(self):
        assert extract_value(3.14) == 3.14

    def test_dict_envelope(self):
        metric = {"value": 25.1, "source": "fred", "observation_date": "2025-01-10"}
        assert extract_value(metric) == 25.1

    def test_dict_missing_value_key(self):
        metric = {"source": "fred"}
        assert extract_value(metric) is None

    def test_dict_value_none(self):
        metric = {"value": None, "source": "fred"}
        assert extract_value(metric) is None

    def test_string_returns_none(self):
        assert extract_value("not a metric") is None  # type: ignore[arg-type]


# ── extract_quality ──────────────────────────────────────────────────

class TestExtractQuality:
    def test_none_returns_unknown(self):
        q = extract_quality(None)
        assert q["source"] == "unknown"
        assert q["tier"] == "unknown"
        assert q["penalty"] == 0.05
        assert q["age_days"] is None
        assert q["is_proxy"] is False
        assert q["observation_date"] is None

    def test_bare_scalar_returns_unknown(self):
        q = extract_quality(42.0)  # type: ignore[arg-type]
        assert q["tier"] == "unknown"

    def test_dict_with_recent_observation_date(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        metric = {"value": 4.25, "source": "fred_dgs10", "observation_date": yesterday}
        q = extract_quality(metric)
        assert q["source"] == "fred_dgs10"
        assert q["tier"] in ("live", "recent", "delayed")
        assert q["age_days"] is not None
        assert q["age_days"] <= 2
        assert q["observation_date"] == yesterday

    def test_dict_with_stale_observation_date(self):
        old = (date.today() - timedelta(days=10)).isoformat()
        metric = {"value": 1.5, "source": "fred_copper", "observation_date": old}
        q = extract_quality(metric)
        assert q["age_days"] >= 9
        assert q["tier"] in ("stale", "very_stale")
        assert q["penalty"] > 0

    def test_dict_without_observation_date(self):
        metric = {"value": 18.5, "source": "tradier"}
        q = extract_quality(metric)
        assert q["source"] == "tradier"
        assert q["observation_date"] is None
        # tradier without date → compute_data_currency handles as unknown
        assert q["tier"] in ("unknown", "live", "recent")


# ── build_data_quality_summary ───────────────────────────────────────

class TestBuildDataQualitySummary:
    def test_empty_metrics(self):
        result = build_data_quality_summary({})
        assert "_summary" in result
        assert result["_summary"]["metric_count"] == 0
        assert result["_summary"]["stale_count"] == 0
        assert result["_summary"]["max_age_days"] is None
        assert result["_summary"]["total_freshness_penalty"] == 0.0

    def test_single_fresh_metric(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        metrics = {
            "vix": {"value": 18.5, "source": "tradier", "observation_date": yesterday}
        }
        result = build_data_quality_summary(metrics)
        assert "vix" in result
        assert "_summary" in result
        assert result["_summary"]["metric_count"] == 1
        assert result["_summary"]["stale_count"] == 0

    def test_mixed_freshness(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        old = (date.today() - timedelta(days=15)).isoformat()
        metrics = {
            "ten_year_yield": {"value": 4.3, "source": "fred_dgs10", "observation_date": yesterday},
            "copper": {"value": 9500, "source": "fred_copper", "observation_date": old},
        }
        result = build_data_quality_summary(metrics)
        assert result["_summary"]["metric_count"] == 2
        assert result["_summary"]["stale_count"] >= 1
        assert result["_summary"]["max_age_days"] >= 14
        assert result["_summary"]["total_freshness_penalty"] > 0

    def test_none_metrics_handled(self):
        metrics = {
            "vix": None,
            "oil": {"value": 70.0, "source": "fred_wti"},
        }
        result = build_data_quality_summary(metrics)
        assert result["_summary"]["metric_count"] == 2
        assert result["vix"]["tier"] == "unknown"
        assert result["oil"]["tier"] == "unknown"  # no observation_date

    def test_summary_penalty_is_rounded(self):
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        metrics = {f"m{i}": {"value": i, "source": "fred", "observation_date": yesterday} for i in range(5)}
        result = build_data_quality_summary(metrics)
        penalty = result["_summary"]["total_freshness_penalty"]
        # Penalty should be a float with at most 3 decimal places
        assert penalty == round(penalty, 3)
