import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    value: T
    timestamp: datetime


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: int):
        self.ttl = timedelta(seconds=ttl_seconds)
        self._entries: dict[str, CacheEntry[T]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, key: str) -> CacheEntry[T] | None:
        return self._entries.get(key)

    def is_fresh(self, entry: CacheEntry[T] | None) -> bool:
        if entry is None:
            return False
        return datetime.now(timezone.utc) - entry.timestamp < self.ttl

    def set(self, key: str, value: T, timestamp: datetime | None = None) -> CacheEntry[T]:
        entry = CacheEntry(value, timestamp or datetime.now(timezone.utc))
        self._entries[key] = entry
        return entry

    def lock_for(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def clear(self) -> None:
        self._entries.clear()
