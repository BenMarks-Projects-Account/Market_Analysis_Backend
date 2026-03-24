"""Tests for compute_data_currency() — unified freshness vocabulary."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from app.services.data_quality_utils import (
    FRESHNESS_PENALTIES,
    compute_data_currency,
    days_stale,
)


# ── FRESHNESS_PENALTIES alignment ────────────────────────────────────────
class TestFreshnessPenaltiesAlignment:
    def test_matches_confidence_framework(self):
        from app.services.confidence_framework import (
            FRESHNESS_PENALTIES as CF_PENALTIES,
        )
        # Every key in the confidence framework must exist in our table
        for key, val in CF_PENALTIES.items():
            assert key in FRESHNESS_PENALTIES, f"Missing key: {key}"
            assert FRESHNESS_PENALTIES[key] == val, (
                f"Mismatch for {key}: {FRESHNESS_PENALTIES[key]} != {val}"
            )

    def test_has_delayed_tier(self):
        assert "delayed" in FRESHNESS_PENALTIES
        assert FRESHNESS_PENALTIES["delayed"] == 0.03


# ── helpers ──────────────────────────────────────────────────────────────
def _date_ago(days: int) -> str:
    """Return ISO date string for N days ago."""
    return (date.today() - timedelta(days=days)).isoformat()


# ── Tradier source ───────────────────────────────────────────────────────
class TestTradierCurrency:
    def test_tradier_market_open(self):
        r = compute_data_currency(source_type="tradier", is_market_open=True)
        assert r == {"tier": "live", "penalty": 0.00, "age_days": 0, "source_type": "tradier"}

    def test_tradier_market_closed(self):
        r = compute_data_currency(source_type="tradier", is_market_open=False)
        assert r == {"tier": "recent", "penalty": 0.00, "age_days": 0, "source_type": "tradier"}

    def test_tradier_ignores_observation_date(self):
        r = compute_data_currency(
            observation_date=_date_ago(5),
            source_type="tradier",
            is_market_open=True,
        )
        assert r["tier"] == "live"


# ── FRED source (observation_date-based) ─────────────────────────────────
class TestFredCurrency:
    def test_fred_today_market_open(self):
        r = compute_data_currency(
            observation_date=_date_ago(0),
            source_type="fred",
            is_market_open=True,
        )
        assert r["tier"] == "live"
        assert r["penalty"] == 0.00
        assert r["age_days"] == 0

    def test_fred_today_market_closed(self):
        r = compute_data_currency(
            observation_date=_date_ago(0),
            source_type="fred",
            is_market_open=False,
        )
        assert r["tier"] == "recent"
        assert r["penalty"] == 0.00

    def test_fred_1_day_ago(self):
        r = compute_data_currency(
            observation_date=_date_ago(1),
            source_type="fred",
            is_market_open=False,
        )
        assert r["tier"] == "recent"
        assert r["age_days"] == 1

    def test_fred_3_days_ago(self):
        r = compute_data_currency(
            observation_date=_date_ago(3),
            source_type="fred",
            is_market_open=False,
        )
        assert r["tier"] == "recent"
        assert r["penalty"] == 0.00

    def test_fred_5_days_ago(self):
        r = compute_data_currency(
            observation_date=_date_ago(5),
            source_type="fred",
            is_market_open=False,
        )
        assert r["tier"] == "stale"
        assert r["penalty"] == 0.10

    def test_fred_10_days_ago(self):
        r = compute_data_currency(
            observation_date=_date_ago(10),
            source_type="fred",
            is_market_open=False,
        )
        assert r["tier"] == "stale"
        assert r["penalty"] == 0.10

    def test_fred_15_days_ago(self):
        r = compute_data_currency(
            observation_date=_date_ago(15),
            source_type="fred",
            is_market_open=False,
        )
        assert r["tier"] == "very_stale"
        assert r["penalty"] == 0.25

    def test_fred_unparseable_date(self):
        r = compute_data_currency(
            observation_date="not-a-date",
            source_type="fred",
            is_market_open=False,
        )
        assert r["tier"] == "unknown"
        assert r["penalty"] == 0.05
        assert r["age_days"] is None

    def test_fred_monthly_5_days_gets_delayed(self):
        """Monthly series (copper) tolerates more staleness → 'delayed' not 'stale'."""
        r = compute_data_currency(
            observation_date=_date_ago(5),
            source_type="fred_monthly",
            is_market_open=False,
        )
        assert r["tier"] == "delayed"
        assert r["penalty"] == 0.03

    def test_fred_monthly_10_days_gets_stale(self):
        r = compute_data_currency(
            observation_date=_date_ago(10),
            source_type="fred_monthly",
            is_market_open=False,
        )
        assert r["tier"] == "stale"


# ── Unknown / missing ───────────────────────────────────────────────────
class TestUnknownCurrency:
    def test_no_observation_date_unknown_source(self):
        r = compute_data_currency(source_type="unknown", is_market_open=False)
        assert r == {"tier": "unknown", "penalty": 0.05, "age_days": None, "source_type": "unknown"}

    def test_no_args(self):
        r = compute_data_currency(is_market_open=False)
        assert r["tier"] == "unknown"
        assert r["source_type"] == "unknown"


# ── Auto-detect market status ───────────────────────────────────────────
class TestAutoDetectMarket:
    @patch("app.services.data_quality_utils.market_status", return_value="open")
    def test_auto_detects_open(self, _mock):
        r = compute_data_currency(
            observation_date=_date_ago(0),
            source_type="fred",
        )
        assert r["tier"] == "live"

    @patch("app.services.data_quality_utils.market_status", return_value="closed")
    def test_auto_detects_closed(self, _mock):
        r = compute_data_currency(
            observation_date=_date_ago(0),
            source_type="fred",
        )
        assert r["tier"] == "recent"
