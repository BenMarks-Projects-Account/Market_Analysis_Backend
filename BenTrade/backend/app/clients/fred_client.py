import httpx

from app.config import Settings
from app.utils.cache import TTLCache
from app.utils.http import request_json


class FredClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient, cache: TTLCache) -> None:
        self.settings = settings
        self.http_client = http_client
        self.cache = cache

    async def get_latest_series_value(self, series_id: str | None = None) -> float | None:
        sid = series_id or self.settings.FRED_VIX_SERIES_ID
        key = f"fred:series:{sid}:latest"
        url = f"{self.settings.FRED_BASE_URL}/series/observations"

        async def _load() -> float | None:
            payload = await request_json(
                self.http_client,
                "GET",
                url,
                params={
                    "series_id": sid,
                    "sort_order": "desc",
                    "limit": 1,
                    "api_key": self.settings.FRED_KEY,
                    "file_type": "json",
                },
            )
            observations = payload.get("observations") or []
            if not observations:
                return None
            value = observations[0].get("value")
            if value in (None, "."):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        return await self.cache.get_or_set(key, self.settings.FRED_CACHE_TTL_SECONDS, _load)

    async def health(self) -> bool:
        try:
            _ = await self.get_latest_series_value()
            return True
        except Exception:
            return False
