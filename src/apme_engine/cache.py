"""Bounded in-memory caches with LRU eviction, optional TTL, and idle-aware eviction.

This module is the single home for the ad-hoc LRU maps scattered across the
codebase (generation locks, proxy download locks / wheel hashes, gateway
idempotency entries).  New bounded maps should use :class:`BoundedCache`
instead of growing another bespoke ``OrderedDict`` + evict helper.

The cache is **not thread-safe** — like the dicts it replaces, it is meant
to be touched from a single event-loop thread only.  All methods are
synchronous and never block, so use from async code is safe.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, MutableMapping
from typing import TypeVar

K = TypeVar("K")
V = TypeVar("V")


class BoundedCache(MutableMapping[K, V]):
    """LRU cache with a hard entry cap, optional TTL, and idle-aware eviction.

    Entries are ordered least-recently-used first; every hit (read or
    ``get_or_create``) refreshes the key to most-recently-used.

    Eviction policy on insert past ``maxsize``:

    * no ``is_idle`` predicate — evict the least-recently-used entry;
    * with ``is_idle`` — evict the oldest entry proving idle, so evicting a
      lock still held/waited elsewhere never splits mutual exclusion.  When
      every entry is busy the insert still succeeds (temporary
      over-capacity) instead of dropping live state.

    TTL (when set) is absolute from last write: reads refresh LRU order but
    do not extend the deadline.  Expired entries are purged lazily on
    access and before insert-cap checks, and raise ``KeyError`` like a miss.
    """

    def __init__(
        self,
        maxsize: int = 512,
        ttl: float | None = None,
        is_idle: Callable[[V], bool] | None = None,
    ) -> None:
        """Initialise an empty bounded cache.

        Args:
            maxsize: Maximum entries before eviction (must be >= 1).
            ttl: Optional seconds an entry stays live after its last write.
            is_idle: Optional predicate marking a value safe to evict.

        Raises:
            ValueError: If ``maxsize`` < 1 or ``ttl`` is not positive finite.
        """
        if maxsize < 1:
            raise ValueError(f"maxsize must be >= 1, got {maxsize!r}")
        if ttl is not None and (not math.isfinite(ttl) or ttl <= 0):
            raise ValueError(f"ttl must be a positive finite number, got {ttl!r}")
        self._maxsize = maxsize
        self._ttl = ttl
        self._is_idle = is_idle
        # Key -> (value, monotonic timestamp of last write). OrderedDict
        # order is LRU order (oldest first); hits move keys to the end.
        self._data: OrderedDict[K, tuple[V, float]] = OrderedDict()

    @property
    def maxsize(self) -> int:
        """Maximum entries retained before eviction kicks in."""
        return self._maxsize

    @property
    def ttl(self) -> float | None:
        """Entry lifetime in seconds from last write (None = no expiry)."""
        return self._ttl

    def _expired(self, stamp: float, now: float) -> bool:
        """Return whether a write timestamp has passed the TTL deadline.

        Args:
            stamp: Monotonic timestamp captured at write time.
            now: Current monotonic clock reading.

        Returns:
            True when a TTL is configured and the entry is stale.
        """
        return self._ttl is not None and now - stamp > self._ttl

    def purge_expired(self) -> None:
        """Drop all TTL-expired entries (no-op without a TTL)."""
        if self._ttl is None:
            return
        now = time.monotonic()
        for key, (_value, stamp) in list(self._data.items()):
            if self._expired(stamp, now):
                del self._data[key]

    def _evict_one(self) -> None:
        """Make room for one new entry under the eviction policy.

        Evicts the oldest entry (or oldest idle entry when ``is_idle`` is
        set).  When every entry is busy the map is left untouched so the
        caller inserts over capacity instead of dropping live state.
        """
        if not self._data:
            return
        if self._is_idle is None:
            self._data.popitem(last=False)
            return
        for key, (value, _stamp) in self._data.items():
            try:
                idle = self._is_idle(value)
            except Exception:  # noqa: BLE001 — fail closed, keep the entry
                continue
            if idle:
                del self._data[key]
                return
        # All entries busy — insert over capacity (no eviction).

    def get_or_create(self, key: K, factory: Callable[[], V]) -> V:
        """Return the live entry for ``key``, creating it on miss.

        A hit refreshes LRU order.  On miss ``factory()`` runs (outside any
        lock — callers must stay on the event-loop thread, as with the
        dicts this replaces) and the result is inserted under the normal
        eviction policy.

        Args:
            key: Cache key to look up.
            factory: Zero-arg callable producing the value on miss.

        Returns:
            The cached (or newly created) value.
        """
        try:
            return self[key]
        except KeyError:
            pass
        value = factory()
        self[key] = value
        return value

    def __getitem__(self, key: K) -> V:
        """Return the live value for ``key``.

        Args:
            key: Cache key to look up.

        Returns:
            The cached value (refreshes LRU order).

        Raises:
            KeyError: On miss or TTL expiry.
        """
        try:
            value, stamp = self._data[key]
        except KeyError:
            raise KeyError(key) from None
        if self._expired(stamp, time.monotonic()):
            del self._data[key]
            raise KeyError(key) from None
        self._data.move_to_end(key)
        return value

    def __setitem__(self, key: K, value: V) -> None:
        """Store ``value`` under ``key``, evicting under policy on overflow.

        Args:
            key: Cache key to store.
            value: Value to associate with ``key``.
        """
        if key in self._data:
            self._data[key] = (value, time.monotonic())
            self._data.move_to_end(key)
            return
        # Drop TTL-dead rows first so they never consume the cap and
        # force eviction of live entries.
        self.purge_expired()
        if len(self._data) >= self._maxsize:
            self._evict_one()
        self._data[key] = (value, time.monotonic())

    def __delitem__(self, key: K) -> None:
        """Remove ``key`` from the cache.

        Args:
            key: Cache key to remove.

        Raises:
            KeyError: When ``key`` is absent.
        """
        try:
            del self._data[key]
        except KeyError:
            raise KeyError(key) from None

    def __iter__(self) -> Iterator[K]:
        """Iterate live keys (purging expired entries first).

        Returns:
            Iterator over live cache keys, oldest first.
        """
        self.purge_expired()
        return iter(list(self._data.keys()))

    def __len__(self) -> int:
        """Return the number of live entries (purging expired first).

        Returns:
            Count of live entries.
        """
        self.purge_expired()
        return len(self._data)
