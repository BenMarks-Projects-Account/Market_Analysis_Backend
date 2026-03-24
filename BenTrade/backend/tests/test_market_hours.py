"""Tests for app.utils.market_hours — shared market-hours utility."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.utils.market_hours import (
    _FIXED_HOLIDAYS,
    is_extended_hours,
    is_market_open,
    is_trading_day,
    last_close_date,
    market_status,
)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


# ── helpers ──────────────────────────────────────────────────────────────
def _et(year, month, day, hour=12, minute=0):
    return dt.datetime(year, month, day, hour, minute, tzinfo=ET)


def _utc(year, month, day, hour=12, minute=0):
    return dt.datetime(year, month, day, hour, minute, tzinfo=UTC)


# ── is_trading_day ───────────────────────────────────────────────────────
class TestIsTradingDay:
    def test_regular_weekday(self):
        # Wednesday 2025-06-11
        assert is_trading_day(_et(2025, 6, 11)) is True

    def test_saturday(self):
        assert is_trading_day(_et(2025, 6, 14)) is False

    def test_sunday(self):
        assert is_trading_day(_et(2025, 6, 15)) is False

    def test_holiday_christmas(self):
        assert is_trading_day(_et(2025, 12, 25)) is False

    def test_holiday_mlk_2026(self):
        assert is_trading_day(_et(2026, 1, 19)) is False

    def test_day_after_holiday_is_trading_day(self):
        # Day after Christmas 2025 (Dec 26, Friday)
        assert is_trading_day(_et(2025, 12, 26)) is True


# ── is_market_open ───────────────────────────────────────────────────────
class TestIsMarketOpen:
    def test_mid_session(self):
        assert is_market_open(_et(2025, 6, 11, 12, 0)) is True

    def test_at_open(self):
        assert is_market_open(_et(2025, 6, 11, 9, 30)) is True

    def test_just_before_open(self):
        assert is_market_open(_et(2025, 6, 11, 9, 29)) is False

    def test_at_close(self):
        # 16:00 is NOT open (exclusive boundary)
        assert is_market_open(_et(2025, 6, 11, 16, 0)) is False

    def test_just_before_close(self):
        assert is_market_open(_et(2025, 6, 11, 15, 59)) is True

    def test_weekend(self):
        assert is_market_open(_et(2025, 6, 14, 12, 0)) is False

    def test_holiday(self):
        assert is_market_open(_et(2025, 7, 4, 12, 0)) is False

    def test_utc_during_est_session(self):
        # 18:00 UTC = 14:00 ET (EST) in winter → market open
        assert is_market_open(_utc(2025, 1, 6, 18, 0)) is True

    def test_utc_outside_session(self):
        # 22:00 UTC = 17:00 ET → market closed
        assert is_market_open(_utc(2025, 6, 11, 22, 0)) is False

    def test_dst_transition_spring(self):
        # March 10, 2025 — clocks spring forward, market opens 09:30 EDT = 13:30 UTC
        assert is_market_open(_utc(2025, 3, 10, 14, 0)) is True  # 10:00 EDT
        assert is_market_open(_utc(2025, 3, 10, 13, 0)) is False  # 09:00 EDT

    def test_naive_datetime_treated_as_et(self):
        # Naive datetime is treated as ET per _to_et
        naive = dt.datetime(2025, 6, 11, 12, 0)
        assert is_market_open(naive) is True


# ── is_extended_hours ────────────────────────────────────────────────────
class TestIsExtendedHours:
    def test_pre_market(self):
        assert is_extended_hours(_et(2025, 6, 11, 7, 0)) is True

    def test_pre_market_boundary_start(self):
        assert is_extended_hours(_et(2025, 6, 11, 4, 0)) is True

    def test_before_pre_market(self):
        assert is_extended_hours(_et(2025, 6, 11, 3, 59)) is False

    def test_post_market(self):
        assert is_extended_hours(_et(2025, 6, 11, 17, 0)) is True

    def test_post_market_boundary_end(self):
        # 20:00 is NOT extended (exclusive boundary)
        assert is_extended_hours(_et(2025, 6, 11, 20, 0)) is False

    def test_during_regular_session(self):
        assert is_extended_hours(_et(2025, 6, 11, 12, 0)) is False

    def test_weekend_no_extended(self):
        assert is_extended_hours(_et(2025, 6, 14, 7, 0)) is False

    def test_holiday_no_extended(self):
        assert is_extended_hours(_et(2025, 12, 25, 7, 0)) is False


# ── market_status ────────────────────────────────────────────────────────
class TestMarketStatus:
    def test_open(self):
        assert market_status(_et(2025, 6, 11, 12, 0)) == "open"

    def test_extended_pre(self):
        assert market_status(_et(2025, 6, 11, 8, 0)) == "extended"

    def test_extended_post(self):
        assert market_status(_et(2025, 6, 11, 17, 30)) == "extended"

    def test_closed_overnight(self):
        assert market_status(_et(2025, 6, 11, 2, 0)) == "closed"

    def test_closed_weekend(self):
        assert market_status(_et(2025, 6, 14, 12, 0)) == "closed"

    def test_closed_holiday(self):
        assert market_status(_et(2025, 7, 4, 12, 0)) == "closed"


# ── last_close_date ──────────────────────────────────────────────────────
class TestLastCloseDate:
    def test_after_close_returns_today(self):
        # Wednesday after close → returns Wednesday
        assert last_close_date(_et(2025, 6, 11, 17, 0)) == dt.date(2025, 6, 11)

    def test_during_session_returns_previous_day(self):
        # Wednesday during session → returns Tuesday
        assert last_close_date(_et(2025, 6, 11, 12, 0)) == dt.date(2025, 6, 10)

    def test_before_open_returns_previous_day(self):
        # Wednesday 08:00 → returns Tuesday
        assert last_close_date(_et(2025, 6, 11, 8, 0)) == dt.date(2025, 6, 10)

    def test_saturday_returns_friday(self):
        # Saturday → returns Friday
        assert last_close_date(_et(2025, 6, 14, 12, 0)) == dt.date(2025, 6, 13)

    def test_sunday_returns_friday(self):
        assert last_close_date(_et(2025, 6, 15, 12, 0)) == dt.date(2025, 6, 13)

    def test_monday_before_close_returns_friday(self):
        assert last_close_date(_et(2025, 6, 16, 10, 0)) == dt.date(2025, 6, 13)

    def test_holiday_returns_previous_trading_day(self):
        # July 4 2025 (Friday) → returns Thursday July 3
        assert last_close_date(_et(2025, 7, 4, 12, 0)) == dt.date(2025, 7, 3)

    def test_day_after_holiday_before_close(self):
        # Monday Jan 20, 2025 is MLK holiday. Tues Jan 21 before close → returns Fri Jan 17
        assert last_close_date(_et(2025, 1, 21, 10, 0)) == dt.date(2025, 1, 17)


# ── holiday calendar integrity ───────────────────────────────────────────
class TestHolidayCalendar:
    def test_all_holidays_are_weekdays(self):
        for d in _FIXED_HOLIDAYS:
            assert d.weekday() < 5, f"Holiday {d} falls on weekend (day {d.weekday()})"

    def test_has_2024_2025_2026(self):
        years = {d.year for d in _FIXED_HOLIDAYS}
        assert {2024, 2025, 2026}.issubset(years)

    def test_at_least_9_per_year(self):
        for year in (2024, 2025, 2026):
            count = sum(1 for d in _FIXED_HOLIDAYS if d.year == year)
            assert count >= 9, f"Year {year} has only {count} holidays"
