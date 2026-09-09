# packages/backend/app/core/cache.py
"""A minimal in-process TTL cache.

Deliberately not Redis. The values worth caching here are small, derived, and
cheap to recompute (region metadata, the dataset catalog, simplified
boundaries); the expensive part is the query or the GEE round-trip, not the
bytes. Introducing a cache server for that would add an operational dependency,
a failure mode and a deployment step for no measurable gain — see the project
brief's "no unnecessary technology" rule.

What this means on serverless: the cache is per-instance and dies with the
instance. That is correct for this data. A cold instance simply recomputes, and
two instances briefly disagreeing about a cached boundary polygon is harmless.
Anything that must be consistent across instances belongs in Postgres, not
here.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")


@dataclass
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    """Async-safe cache keyed by string, with a per-entry expiry."""

    def __init__(self, default_ttl: float = 300.0, max_entries: int = 256):
        self._store: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._default_ttl = default_ttl
        self._max_entries = max_entries

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        if len(self._store) >= self._max_entries:
            self._evict_expired()
            if len(self._store) >= self._max_entries:
                # Still full: drop whatever expires soonest. Crude, but this
                # cache holds tens of entries in practice, not thousands.
                oldest = min(self._store, key=lambda k: self._store[k].expires_at)
                self._store.pop(oldest, None)
        self._store[key] = _Entry(
            value=value,
            expires_at=time.monotonic() + (ttl if ttl is not None else self._default_ttl),
        )

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
        ttl: Optional[float] = None,
    ) -> T:
        """Return the cached value, computing it at most once concurrently.

        The per-key lock matters for the expensive entries: without it, N
        simultaneous cold requests for the region geometry would each fire
        their own Earth Engine export.
        """
        hit = self.get(key)
        if hit is not None:
            return hit

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Re-check: another coroutine may have filled it while we waited.
            hit = self.get(key)
            if hit is not None:
                return hit
            value = await factory()
            self.set(key, value, ttl)
            return value

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        """Drop a whole family of keys, e.g. everything derived from
        observations after an ingestion run."""
        for key in [k for k in self._store if k.startswith(prefix)]:
            self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def _evict_expired(self) -> None:
        now = time.monotonic()
        for key in [k for k, v in self._store.items() if v.expires_at <= now]:
            self._store.pop(key, None)

    @property
    def size(self) -> int:
        return len(self._store)


# TTLs reflect how fast each thing can actually change.
#
# Observations only change when the pipeline runs (nightly), so a short TTL on
# derived aggregates costs little and bounds staleness after a manual run.
# Boundaries change essentially never, so they are cached for the process
# lifetime in practical terms.
OVERVIEW_TTL = 120.0
CATALOG_TTL = 300.0
GEOMETRY_TTL = 86_400.0

# One shared instance; prefixes keep the namespaces apart.
cache = TTLCache(default_ttl=OVERVIEW_TTL)

OBSERVATION_PREFIX = "obs:"


def invalidate_observation_caches() -> None:
    """Call after an ingestion run so the dashboard reflects new data.

    Boundaries are intentionally left alone: an ingestion run does not change
    them, and re-exporting them from Earth Engine is the one genuinely
    expensive thing this cache prevents.
    """
    cache.invalidate_prefix(OBSERVATION_PREFIX)
