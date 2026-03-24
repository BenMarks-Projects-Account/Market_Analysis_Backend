"""Tests for cross-series date alignment check in market_context_service."""

import logging
import pytest

from app.services.market_context_service import _check_date_alignment, _metric


# ═══════════════════════════════════════════════════════════════════════
# UNIT TESTS: _check_date_alignment()
# ═══════════════════════════════════════════════════════════════════════


class TestCheckDateAlignment:
    """Direct unit tests for the date alignment helper."""

    def test_aligned_same_date(self):
        """Both series share the same observation date → aligned."""
        metrics = {
            "DGS10": _metric(3.5, "fred", observation_date="2026-03-20"),
            "DGS2": _metric(4.1, "fred", observation_date="2026-03-20"),
        }
        result = _check_date_alignment(metrics, ["DGS10", "DGS2"])
        assert result["aligned"] is True
        assert result["detail"] is None
        assert result["oldest_date"] == "2026-03-20"

    def test_mismatched_dates(self):
        """Series have different observation dates → not aligned."""
        metrics = {
            "DGS10": _metric(3.5, "fred", observation_date="2026-03-20"),
            "DGS2": _metric(4.1, "fred", observation_date="2026-03-19"),
        }
        result = _check_date_alignment(metrics, ["DGS10", "DGS2"])
        assert result["aligned"] is False
        assert "DGS10=2026-03-20" in result["detail"]
        assert "DGS2=2026-03-19" in result["detail"]
        assert result["oldest_date"] == "2026-03-19"

    def test_one_missing_observation_date(self):
        """One series has no observation_date → only one date collected, aligned."""
        metrics = {
            "DGS10": _metric(3.5, "fred", observation_date="2026-03-20"),
            "DGS2": _metric(None, "fred"),  # no observation_date
        }
        result = _check_date_alignment(metrics, ["DGS10", "DGS2"])
        assert result["aligned"] is True  # only 1 date found
        assert result["oldest_date"] == "2026-03-20"

    def test_both_missing_observation_date(self):
        """Neither series has observation_date → aligned (vacuously)."""
        metrics = {
            "DGS10": _metric(None, "fred"),
            "DGS2": _metric(None, "fred"),
        }
        result = _check_date_alignment(metrics, ["DGS10", "DGS2"])
        assert result["aligned"] is True
        assert result["oldest_date"] is None

    def test_three_series_all_aligned(self):
        """Three series, all same date → aligned."""
        metrics = {
            "A": _metric(1.0, "fred", observation_date="2026-03-20"),
            "B": _metric(2.0, "fred", observation_date="2026-03-20"),
            "C": _metric(3.0, "fred", observation_date="2026-03-20"),
        }
        result = _check_date_alignment(metrics, ["A", "B", "C"])
        assert result["aligned"] is True

    def test_three_series_one_off(self):
        """Three series, one differs → not aligned, oldest_date is earliest."""
        metrics = {
            "A": _metric(1.0, "fred", observation_date="2026-03-20"),
            "B": _metric(2.0, "fred", observation_date="2026-03-19"),
            "C": _metric(3.0, "fred", observation_date="2026-03-20"),
        }
        result = _check_date_alignment(metrics, ["A", "B", "C"])
        assert result["aligned"] is False
        assert result["oldest_date"] == "2026-03-19"

    def test_warning_logged_on_mismatch(self, caplog):
        """Mismatch should produce a warning log."""
        metrics = {
            "DGS10": _metric(3.5, "fred", observation_date="2026-03-20"),
            "DGS2": _metric(4.1, "fred", observation_date="2026-03-19"),
        }
        with caplog.at_level(logging.WARNING):
            _check_date_alignment(metrics, ["DGS10", "DGS2"])
        assert any("cross_series_date_mismatch" in r.message for r in caplog.records)

    def test_no_warning_when_aligned(self, caplog):
        """Aligned dates should produce no warning."""
        metrics = {
            "DGS10": _metric(3.5, "fred", observation_date="2026-03-20"),
            "DGS2": _metric(4.1, "fred", observation_date="2026-03-20"),
        }
        with caplog.at_level(logging.WARNING):
            _check_date_alignment(metrics, ["DGS10", "DGS2"])
        assert not any("cross_series_date_mismatch" in r.message for r in caplog.records)

    def test_dates_dict_populated(self):
        """The dates dict should contain all series with observation dates."""
        metrics = {
            "DGS10": _metric(3.5, "fred", observation_date="2026-03-20"),
            "DGS2": _metric(4.1, "fred", observation_date="2026-03-19"),
        }
        result = _check_date_alignment(metrics, ["DGS10", "DGS2"])
        assert result["dates"] == {"DGS10": "2026-03-20", "DGS2": "2026-03-19"}


# ═══════════════════════════════════════════════════════════════════════
# INTEGRATION: yield spread gets mismatch flag
# ═══════════════════════════════════════════════════════════════════════


class TestYieldSpreadMismatchFlag:
    """Verify _metric + _check_date_alignment integration pattern."""

    def test_aligned_no_flag(self):
        """When aligned, metric should NOT have mismatch flag."""
        ten_year = _metric(3.5, "fred", observation_date="2026-03-20")
        two_year = _metric(4.1, "fred", observation_date="2026-03-20")

        alignment = _check_date_alignment(
            {"DGS10": ten_year, "DGS2": two_year}, ["DGS10", "DGS2"]
        )
        spread = _metric(
            round(ten_year["value"] - two_year["value"], 3),
            source="derived (10Y-2Y)",
            observation_date=alignment["oldest_date"],
        )
        if not alignment["aligned"]:
            spread["cross_series_date_mismatch"] = True
            spread["date_mismatch_detail"] = alignment["detail"]

        assert "cross_series_date_mismatch" not in spread
        assert spread["observation_date"] == "2026-03-20"

    def test_mismatched_has_flag(self):
        """When mismatched, metric should have mismatch flag and oldest date."""
        ten_year = _metric(3.5, "fred", observation_date="2026-03-20")
        two_year = _metric(4.1, "fred", observation_date="2026-03-19")

        alignment = _check_date_alignment(
            {"DGS10": ten_year, "DGS2": two_year}, ["DGS10", "DGS2"]
        )
        spread = _metric(
            round(ten_year["value"] - two_year["value"], 3),
            source="derived (10Y-2Y)",
            observation_date=alignment["oldest_date"],
        )
        if not alignment["aligned"]:
            spread["cross_series_date_mismatch"] = True
            spread["date_mismatch_detail"] = alignment["detail"]

        assert spread["cross_series_date_mismatch"] is True
        assert "DGS10=2026-03-20" in spread["date_mismatch_detail"]
        assert "DGS2=2026-03-19" in spread["date_mismatch_detail"]
        assert spread["observation_date"] == "2026-03-19"  # oldest/conservative

    def test_spread_value_still_computed(self):
        """Mismatch is flag-only — spread value should still be computed."""
        ten_year = _metric(3.5, "fred", observation_date="2026-03-20")
        two_year = _metric(4.1, "fred", observation_date="2026-03-19")

        alignment = _check_date_alignment(
            {"DGS10": ten_year, "DGS2": two_year}, ["DGS10", "DGS2"]
        )
        spread_val = round(ten_year["value"] - two_year["value"], 3)
        assert spread_val == -0.6  # Computation proceeds regardless
