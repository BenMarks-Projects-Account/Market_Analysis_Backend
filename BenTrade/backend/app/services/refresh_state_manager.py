"""
Refresh state manager — owns the pause flag and computes market hours.

The actual refresh timers live in the frontend (home.js). This service
provides them with the canonical pause state and market-hours flag so
they can adjust their own interval and respect manual pauses.
"""

import asyncio
import json
import logging
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)

# Market hours definition (simple — no holidays)
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
EASTERN = ZoneInfo("America/New_York")

# Off-hours scaling multiplier
OFF_HOURS_MULTIPLIER = 3

# Persistence — matches BenTrade data dir convention (backend/data/)
STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "refresh_state.json"


class RefreshStateManager:
    """Singleton state for the home dashboard refresh control."""

    def __init__(self):
        self._paused: bool = False
        self._wakeup_event: asyncio.Event = asyncio.Event()
        self._load_persisted_state()

    def _load_persisted_state(self):
        try:
            if STATE_FILE.exists():
                with open(STATE_FILE) as f:
                    data = json.load(f)
                    self._paused = bool(data.get("paused", False))
                    _log.info(f"[refresh_state] loaded persisted state: paused={self._paused}")
        except Exception as e:
            _log.warning(f"[refresh_state] failed to load persisted state: {e}")
            self._paused = False

    def _persist_state(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump({"paused": self._paused}, f)
        except Exception as e:
            _log.warning(f"[refresh_state] failed to persist state: {e}")

    def is_market_hours(self, now: Optional[datetime] = None) -> bool:
        """
        9:30 AM - 4:00 PM Eastern, Monday-Friday. No holidays.
        """
        if now is None:
            now = datetime.now(tz=EASTERN)
        else:
            now = now.astimezone(EASTERN)

        if now.weekday() > 4:  # 5=Sat, 6=Sun
            return False

        return MARKET_OPEN <= now.time() < MARKET_CLOSE

    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> dict:
        self._paused = True
        self._persist_state()
        self._wakeup_event.set()
        _log.info("[refresh_state] paused by user")
        return self.get_state()

    def resume(self) -> dict:
        self._paused = False
        self._persist_state()
        self._wakeup_event.set()
        _log.info("[refresh_state] resumed by user")
        return self.get_state()

    async def wait_for_interval_or_wakeup(self, interval_seconds: float):
        """Sleep for *interval_seconds* or until pause/resume is signaled."""
        self._wakeup_event.clear()
        try:
            await asyncio.wait_for(self._wakeup_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass  # Normal: interval elapsed

    def get_state(self) -> dict:
        """
        Returns refresh state for the frontend. The frontend uses
        `interval_multiplier` to scale its own hardcoded base intervals.
        """
        market_open = self.is_market_hours()
        return {
            "paused": self._paused,
            "market_hours_active": market_open,
            "interval_multiplier": 1 if market_open else OFF_HOURS_MULTIPLIER,
            "off_hours_multiplier": OFF_HOURS_MULTIPLIER,
        }


# Module-level singleton
_state_manager: Optional[RefreshStateManager] = None


def get_state_manager() -> RefreshStateManager:
    global _state_manager
    if _state_manager is None:
        _state_manager = RefreshStateManager()
    return _state_manager
