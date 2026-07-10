import asyncio
from collections import defaultdict
from typing import Awaitable, Callable, Hashable, TypeVar

from cachetools import TTLCache

T = TypeVar("T")


class AsyncTTLCache:
    """Cache-aside wrapper with TTL expiry and per-key coalescing of concurrent misses."""

    def __init__(self, maxsize: int, ttl: float) -> None:
        self._store: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._locks: dict[Hashable, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get_or_fetch(
        self, key: Hashable, fetch: Callable[[], Awaitable[T]], *, refresh: bool = False
    ) -> T:
        if not refresh and key in self._store:
            return self._store[key]
        async with self._locks[key]:
            if not refresh and key in self._store:
                return self._store[key]
            value = await fetch()
            self._store[key] = value
            return value
