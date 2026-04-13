from typing import Any

import httpx

from app.config import Settings
from app.utils.cache import TTLCache
from app.utils.http import request_json


class FredClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient, cache: TTLCache) -> None:
        self.settings = settings
        self.http_client = http_client
        self.cache = cache

    async def _fetch_latest_observation(self, series_id: str) -> dict[str, Any] | None:
        """Fetch the most recent observation for a FRED series.

        Returns {"value": float, "observation_date": "YYYY-MM-DD"} or None.
        """
        url = f"{self.settings.FRED_BASE_URL}/series/observations"
        payload = await request_json(
            self.http_client,
            "GET",
            url,
            params={
                "series_id": series_id,
                "sort_order": "desc",
                "limit": 1,
                "api_key": self.settings.FRED_KEY,
                "file_type": "json",
            },
        )
        observations = payload.get("observations") or []
        if not observations:
            return None
        row = observations[0]
        raw_value = row.get("value")
        if raw_value in (None, "."):
            return None
        try:
            return {
                "value": float(raw_value),
                "observation_date": row.get("date", ""),
            }
        except (TypeError, ValueError):
            return None

    async def get_series_with_date(self, series_id: str | None = None) -> dict[str, Any] | None:
        """Return {"value": float, "observation_date": str} with cache."""
        sid = series_id or self.settings.FRED_VIX_SERIES_ID
        key = f"fred:series:{sid}:obs"

        async def _load() -> dict[str, Any] | None:
            return await self._fetch_latest_observation(sid)

        return await self.cache.get_or_set(key, self.settings.FRED_CACHE_TTL_SECONDS, _load)

    async def get_latest_series_value(self, series_id: str | None = None) -> float | None:
        """Backward-compatible: returns the numeric value only."""
        obs = await self.get_series_with_date(series_id)
        return obs["value"] if obs else None

    async def health(self) -> bool:
        try:
            _ = await self.get_latest_series_value()
            return True
        except Exception:
            return False

    async def get_observation_series(self, series_id: str, limit: int = 30) -> list[dict] | None:
        """Fetch the last *limit* observations for a FRED series.

        Returns a list of {"date": "YYYY-MM-DD", "value": float} dicts
        (most recent last), or None on failure.
        """
        key = f"fred:series:{series_id}:list:{limit}"

        async def _load() -> list[dict] | None:
            url = f"{self.settings.FRED_BASE_URL}/series/observations"
            payload = await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "series_id": series_id,
                    "sort_order": "desc",
                    "limit": limit,
                    "api_key": self.settings.FRED_KEY,
                    "file_type": "json",
                },
            )
            observations = payload.get("observations") or []
            result: list[dict] = []
            for row in reversed(observations):  # oldest first
                raw = row.get("value")
                if raw in (None, "."):
                    continue
                try:
                    result.append({"date": row.get("date", ""), "value": float(raw)})
                except (TypeError, ValueError):
                    continue
            return result or None

        return await self.cache.get_or_set(key, self.settings.FRED_CACHE_TTL_SECONDS, _load)

    async def get_release_dates(self, realtime_start: str, realtime_end: str) -> list[dict] | None:
        """Fetch upcoming FRED release dates for economic data.

        Returns list of {"release_id": int, "date": str} dicts, or None on failure.
        """
        key = f"fred:release_dates:{realtime_start}:{realtime_end}"

        async def _load() -> list[dict] | None:
            url = f"{self.settings.FRED_BASE_URL}/releases/dates"
            payload = await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "realtime_start": realtime_start,
                    "realtime_end": realtime_end,
                    "include_release_dates_with_no_data": "false",
                    "limit": 100,
                    "sort_order": "asc",
                    "api_key": self.settings.FRED_KEY,
                    "file_type": "json",
                },
            )
            return payload.get("release_dates") or None

        return await self.cache.get_or_set(key, 300, _load)

    async def get_releases(self) -> dict[int, str] | None:
        """Fetch all FRED releases for ID → name mapping. Cached aggressively."""
        key = "fred:releases:all"

        async def _load() -> dict[int, str] | None:
            url = f"{self.settings.FRED_BASE_URL}/releases"
            payload = await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "api_key": self.settings.FRED_KEY,
                    "file_type": "json",
                },
            )
            releases = payload.get("releases") or []
            if not releases:
                return None
            return {r["id"]: r.get("name", "") for r in releases if "id" in r}

        return await self.cache.get_or_set(key, 3600, _load)  # 1-hour cache
