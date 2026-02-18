import asyncio
import time
from typing import Any, Awaitable, Callable


class TTLCache:
    def __init__(self, maxsize: int = 1024) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self._maxsize = maxsize

    def _is_expired(self, expires_at: float) -> bool:
        return expires_at <= time.time()

    def _evict_expired(self) -> None:
        """Remove all expired entries (must be called under lock)."""
        now = time.time()
        expired = [k for k, (exp, _) in self._store.items() if exp <= now]
        for k in expired:
            del self._store[k]

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires_at, value = item
            if self._is_expired(expires_at):
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        async with self._lock:
            if len(self._store) >= self._maxsize and key not in self._store:
                self._evict_expired()
                # If still at capacity after purging expired, drop oldest entry
                if len(self._store) >= self._maxsize:
                    oldest_key = min(self._store, key=lambda k: self._store[k][0])
                    del self._store[oldest_key]
            self._store[key] = (time.time() + ttl_seconds, value)

    async def get_or_set(
        self,
        key: str,
        ttl_seconds: int,
        loader: Callable[[], Awaitable[Any]],
    ) -> Any:
        cached = await self.get(key)
        if cached is not None:
            return cached

        loaded = await loader()
        await self.set(key, loaded, ttl_seconds)
        return loaded
