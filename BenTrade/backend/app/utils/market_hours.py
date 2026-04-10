"""Shared US equity market-hours awareness utility.

DST-aware via ``zoneinfo`` (stdlib 3.9+).  Uses the IANA
``America/New_York`` zone so regular-session boundaries (09:30-16:00 ET)
are always correct regardless of UTC offset.

NYSE holiday calendar is maintained as a set of ``datetime.date`` objects.
Extend ``_FIXED_HOLIDAYS`` each December when the NYSE publishes the
following year's schedule.
"""

from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

# Regular session: 09:30 – 16:00 ET
_OPEN = _dt.time(9, 30)
_CLOSE = _dt.time(16, 0)

# Extended / pre-market: 04:00 – 09:30 ET, post-market: 16:00 – 20:00 ET
_EXT_PRE = _dt.time(4, 0)
_EXT_POST = _dt.time(20, 0)

# NYSE observed holidays — keep at least current + next year.
# Source: https://www.nyse.com/markets/hours-calendars
_FIXED_HOLIDAYS: set[_dt.date] = {
    # 2024
    _dt.date(2024, 1, 1),    # New Year
    _dt.date(2024, 1, 15),   # MLK
    _dt.date(2024, 2, 19),   # Presidents' Day
    _dt.date(2024, 3, 29),   # Good Friday
    _dt.date(2024, 5, 27),   # Memorial Day
    _dt.date(2024, 6, 19),   # Juneteenth
    _dt.date(2024, 7, 4),    # Independence Day
    _dt.date(2024, 9, 2),    # Labor Day
    _dt.date(2024, 11, 28),  # Thanksgiving
    _dt.date(2024, 12, 25),  # Christmas
    # 2025
    _dt.date(2025, 1, 1),    # New Year
    _dt.date(2025, 1, 20),   # MLK
    _dt.date(2025, 2, 17),   # Presidents' Day
    _dt.date(2025, 4, 18),   # Good Friday
    _dt.date(2025, 5, 26),   # Memorial Day
    _dt.date(2025, 6, 19),   # Juneteenth
    _dt.date(2025, 7, 4),    # Independence Day
    _dt.date(2025, 9, 1),    # Labor Day
    _dt.date(2025, 11, 27),  # Thanksgiving
    _dt.date(2025, 12, 25),  # Christmas
    # 2026
    _dt.date(2026, 1, 1),    # New Year
    _dt.date(2026, 1, 19),   # MLK
    _dt.date(2026, 2, 16),   # Presidents' Day
    _dt.date(2026, 4, 3),    # Good Friday
    _dt.date(2026, 5, 25),   # Memorial Day
    _dt.date(2026, 6, 19),   # Juneteenth
    _dt.date(2026, 7, 3),    # Independence Day (observed)
    _dt.date(2026, 9, 7),    # Labor Day
    _dt.date(2026, 11, 26),  # Thanksgiving
    _dt.date(2026, 12, 25),  # Christmas
}


def _to_et(ts: _dt.datetime | None = None) -> _dt.datetime:
    """Return *ts* (or now) as a tz-aware Eastern Time datetime."""
    if ts is None:
        return _dt.datetime.now(_ET)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=_ET)
    return ts.astimezone(_ET)


def is_trading_day(ts: _dt.datetime | None = None) -> bool:
    """True if the date portion of *ts* is a weekday and not an NYSE holiday."""
    et = _to_et(ts)
    return et.weekday() < 5 and et.date() not in _FIXED_HOLIDAYS


def is_market_open(ts: _dt.datetime | None = None) -> bool:
    """True during NYSE regular session (09:30-16:00 ET) on a trading day."""
    et = _to_et(ts)
    if not is_trading_day(et):
        return False
    t = et.time()
    return _OPEN <= t < _CLOSE


def is_extended_hours(ts: _dt.datetime | None = None) -> bool:
    """True during pre-market (04:00-09:30) or post-market (16:00-20:00) on a trading day."""
    et = _to_et(ts)
    if not is_trading_day(et):
        return False
    t = et.time()
    if _EXT_PRE <= t < _OPEN:
        return True
    if _CLOSE <= t < _EXT_POST:
        return True
    return False


def market_status(ts: _dt.datetime | None = None) -> str:
    """Return one of ``"open"`` | ``"extended"`` | ``"closed"``.

    Covers regular session, pre/post-market, weekends, and holidays.
    """
    et = _to_et(ts)
    if is_market_open(et):
        return "open"
    if is_extended_hours(et):
        return "extended"
    return "closed"


def next_market_event(ts: _dt.datetime | None = None) -> dict[str, str]:
    """Return the next market open or close event relative to *ts* (or now).

    Returns ``{"event": "close", "time": <iso>}`` during regular session,
    or ``{"event": "open", "time": <iso>}`` otherwise.
    """
    et = _to_et(ts)
    if is_market_open(et):
        close_time = et.replace(hour=_CLOSE.hour, minute=_CLOSE.minute, second=0, microsecond=0)
        return {"event": "close", "time": close_time.isoformat()}

    # Find next open: advance to next trading day if past open time today
    candidate = et
    t = candidate.time()
    # If before open on a trading day, next open is today
    if is_trading_day(candidate) and t < _OPEN:
        open_time = candidate.replace(hour=_OPEN.hour, minute=_OPEN.minute, second=0, microsecond=0)
        return {"event": "open", "time": open_time.isoformat()}

    # Otherwise walk forward to next trading day
    candidate += _dt.timedelta(days=1)
    while candidate.weekday() >= 5 or candidate.date() in _FIXED_HOLIDAYS:
        candidate += _dt.timedelta(days=1)
    open_time = candidate.replace(hour=_OPEN.hour, minute=_OPEN.minute, second=0, microsecond=0)
    return {"event": "open", "time": open_time.isoformat()}


def last_close_date(ts: _dt.datetime | None = None) -> _dt.date:
    """Most recent completed regular-session date.

    If called during a trading day before market close, returns the
    *previous* trading day (the last session that fully closed).
    If called after 16:00 ET on a trading day, returns today.
    """
    et = _to_et(ts)
    d = et.date()
    t = et.time()

    # If today is a trading day AND it's after market close, today counts
    if is_trading_day(et) and t >= _CLOSE:
        return d

    # Otherwise walk backwards to the most recent trading day
    d -= _dt.timedelta(days=1)
    while d.weekday() >= 5 or d in _FIXED_HOLIDAYS:
        d -= _dt.timedelta(days=1)
    return d
