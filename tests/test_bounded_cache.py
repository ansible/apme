"""Unit tests for apme_engine.cache.BoundedCache."""

from __future__ import annotations

import pytest

from apme_engine.cache import BoundedCache


def test_lru_evicts_oldest_on_overflow() -> None:
    """Inserts past maxsize drop the least-recently-used entry."""
    cache: BoundedCache[str, int] = BoundedCache(maxsize=3)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3
    _ = cache["a"]  # Refresh "a" so "b" is oldest.
    cache["d"] = 4
    assert "b" not in cache
    assert cache["a"] == 1
    assert cache["c"] == 3
    assert cache["d"] == 4


def test_update_refreshes_without_eviction() -> None:
    """Re-setting an existing key refreshes recency and keeps all entries."""
    cache: BoundedCache[str, int] = BoundedCache(maxsize=2)
    cache["a"] = 1
    cache["b"] = 2
    cache["a"] = 10
    cache["c"] = 3
    assert cache["a"] == 10
    assert "b" not in cache
    assert cache["c"] == 3


def test_get_or_create_caches_factory_result() -> None:
    """Miss runs the factory once; hits return the cached value."""
    calls: list[str] = []
    cache: BoundedCache[str, str] = BoundedCache(maxsize=8)

    def _factory() -> str:
        calls.append("made")
        return "v"

    assert cache.get_or_create("k", _factory) == "v"
    assert cache.get_or_create("k", _factory) == "v"
    assert calls == ["made"]


def test_ttl_expires_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entries older than the TTL read as misses (fake clock, no sleeps).

    Args:
        monkeypatch: Pytest monkeypatch fixture (fake monotonic clock).
    """
    import time

    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    cache: BoundedCache[str, int] = BoundedCache(maxsize=8, ttl=0.05)
    cache["a"] = 1
    assert cache.get("a") == 1
    now[0] += 0.08  # advance past the TTL
    assert cache.get("a") is None
    assert "a" not in cache
    assert len(cache) == 0


def test_idle_predicate_skips_busy_entries() -> None:
    """Eviction drops the oldest IDLE entry, never a busy one."""
    cache: BoundedCache[str, str] = BoundedCache(maxsize=2, is_idle=lambda v: v != "busy")
    cache["busy-key"] = "busy"
    cache["idle-key"] = "idle"
    cache["new-key"] = "new"
    assert cache["busy-key"] == "busy"
    assert "idle-key" not in cache
    assert cache["new-key"] == "new"


def test_all_busy_allows_over_capacity() -> None:
    """When every entry is busy the insert succeeds over cap."""
    cache: BoundedCache[str, str] = BoundedCache(maxsize=2, is_idle=lambda _v: False)
    cache["a"] = "x"
    cache["b"] = "y"
    cache["c"] = "z"
    assert len(cache) == 3
    assert cache["a"] == "x"


def test_invalid_bounds_rejected() -> None:
    """Non-positive maxsize and non-positive TTL raise ValueError."""
    with pytest.raises(ValueError):
        BoundedCache(maxsize=0)
    with pytest.raises(ValueError):
        BoundedCache(ttl=0.0)
    with pytest.raises(ValueError):
        BoundedCache(ttl=-1.0)


def test_insert_purges_expired_before_eviction(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTL-dead rows do not consume the cap on insert (fake clock).

    Args:
        monkeypatch: Pytest monkeypatch fixture (fake monotonic clock).
    """
    import time

    now = [2000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    cache: BoundedCache[str, int] = BoundedCache(maxsize=2, ttl=0.05)
    cache["a"] = 1
    cache["b"] = 2
    now[0] += 0.08  # both rows are dead past the TTL
    # Both rows are dead; the insert must purge them instead of
    # evicting a live entry (none live) or growing past the cap.
    cache["c"] = 3
    assert cache.get("c") == 3
    assert len(cache) == 1


def test_is_idle_raise_retains_entry_fail_closed() -> None:
    """An ``is_idle`` raise keeps the entry (fail-closed).

    When the idle check raises on the oldest entry, eviction must retain
    it and drop a later idle entry instead of propagating the error or
    dropping live state.
    """

    def _is_idle(value: str) -> bool:
        if value == "boom":
            raise RuntimeError("idle check failed")
        return True

    cache: BoundedCache[str, str] = BoundedCache(maxsize=2, is_idle=_is_idle)
    cache["old"] = "boom"  # oldest entry; the idle check raises on it
    cache["mid"] = "idle"
    # Insert past maxsize triggers eviction: "old" raises so it is
    # retained fail-closed and "mid" is evicted instead.
    cache["new"] = "x"
    assert cache["old"] == "boom"
    assert "mid" not in cache
    assert cache["new"] == "x"
