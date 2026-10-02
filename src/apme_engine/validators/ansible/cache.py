"""Collection-version-aware LRU cache for Ansible plugin introspection.

Caches expensive subprocess results (plugin resolution, docstring specs,
mock argspecs) keyed by ``(collection_fqcn, collection_version, plugin_name)``
so that repeat scans and cross-venv calls with identical collection versions
avoid redundant subprocess invocations.

The cache is in-memory only — it lives in the Ansible validator daemon
process and dies with it.
"""

from __future__ import annotations

import glob as _glob
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Mapping
from typing import Literal

logger = logging.getLogger(__name__)

CacheType = Literal["introspect", "docspec", "mockspec"]
CacheKey = tuple[str, str, str]  # (collection_fqcn, version, plugin_name)

# Snapshot of cumulative counters, as returned by ``PluginCache.snapshot()``.
# Keys match ``stats()`` (``cache_<store>_hits`` / ``cache_<store>_misses``).
CacheSnapshot = dict[str, int]

_STORES: tuple[CacheType, ...] = ("introspect", "docspec", "mockspec")

_MAX_ENTRIES = 1024
_MAX_VERSION_ENTRIES = 256


def _collection_version(venv_root: str, namespace: str, name: str) -> str:
    """Read a collection's installed version from its MANIFEST.json.

    Args:
        venv_root: Path to the venv root directory.
        namespace: Collection namespace (e.g. ``community``).
        name: Collection name (e.g. ``general``).

    Returns:
        Version string, or ``""`` if the manifest cannot be read.
    """
    pattern = f"{venv_root}/lib/python*/site-packages/ansible_collections/{namespace}/{name}/MANIFEST.json"
    for path in sorted(_glob.glob(pattern)):
        try:
            with open(path) as fh:
                manifest = json.load(fh)
            return str(manifest.get("collection_info", {}).get("version", ""))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return ""


def _ansible_core_version(venv_root: str) -> str:
    """Read the ansible-core version installed in a venv.

    Args:
        venv_root: Path to the venv root directory.

    Returns:
        Version string, or ``""`` if it cannot be determined.
    """
    pattern = f"{venv_root}/lib/python*/site-packages/ansible/release.py"
    for path in sorted(_glob.glob(pattern)):
        try:
            with open(path) as fh:
                for line in fh:
                    if line.startswith("__version__"):
                        return line.split("=", 1)[1].strip().strip("'\"")
        except OSError:
            continue
    return ""


def _parse_fqcn(plugin_name: str) -> tuple[str, str, str] | None:
    """Parse a FQCN into (namespace, collection_name, plugin_short_name).

    Returns ``None`` for short-form names (fewer than 3 dot-separated parts).

    Args:
        plugin_name: Fully qualified collection name or short-form name.

    Returns:
        ``(namespace, name, short_name)`` or ``None``.
    """
    parts = plugin_name.split(".")
    if len(parts) < 3:
        return None
    return (parts[0], parts[1], ".".join(parts[2:]))


class PluginCache:
    """In-memory LRU cache for plugin introspection results.

    Thread-safe — guarded by a single lock since the validator daemon
    may serve concurrent RPCs.
    """

    def __init__(self, max_entries: int = _MAX_ENTRIES) -> None:
        """Initialize the cache.

        Args:
            max_entries: Maximum entries per LRU store before eviction.
        """
        self._max = max_entries
        self._stores: dict[CacheType, OrderedDict[CacheKey, object]] = {
            "introspect": OrderedDict(),
            "docspec": OrderedDict(),
            "mockspec": OrderedDict(),
        }
        self._hits: dict[CacheType, int] = {"introspect": 0, "docspec": 0, "mockspec": 0}
        self._misses: dict[CacheType, int] = {"introspect": 0, "docspec": 0, "mockspec": 0}
        self._version_cache: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        self._core_version_cache: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def _resolve_version(self, venv_root: str, namespace: str, name: str) -> str:
        """Resolve collection version with an internal memo cache.

        Args:
            venv_root: Venv root path.
            namespace: Collection namespace.
            name: Collection name.

        Returns:
            Version string.
        """
        memo_key = (venv_root, namespace, name)
        cached = self._version_cache.get(memo_key)
        if cached is not None:
            self._version_cache.move_to_end(memo_key)
            return cached
        if namespace == "ansible" and name == "builtin":
            version = self._core_version_cache.get(venv_root)
            if version is None:
                version = _ansible_core_version(venv_root)
                self._core_version_cache[venv_root] = version
                while len(self._core_version_cache) > _MAX_VERSION_ENTRIES:
                    self._core_version_cache.popitem(last=False)
            else:
                self._core_version_cache.move_to_end(venv_root)
        else:
            version = _collection_version(venv_root, namespace, name)
        self._version_cache[memo_key] = version
        while len(self._version_cache) > _MAX_VERSION_ENTRIES:
            self._version_cache.popitem(last=False)
        return version

    def _make_key(self, venv_root: str, plugin_name: str) -> CacheKey | None:
        """Build a cache key from a plugin name.

        Returns ``None`` for short-form names that can't be resolved to
        a collection without subprocess help.

        Args:
            venv_root: Venv root path.
            plugin_name: FQCN or short-form module name.

        Returns:
            Cache key tuple or ``None``.
        """
        parsed = _parse_fqcn(plugin_name)
        if parsed is None:
            return None
        namespace, name, _ = parsed
        version = self._resolve_version(venv_root, namespace, name)
        if not version:
            return None
        collection_fqcn = f"{namespace}.{name}"
        return (collection_fqcn, version, plugin_name)

    def get(self, store: CacheType, venv_root: str, plugin_name: str) -> object | None:
        """Look up a cached result.

        Args:
            store: Which cache store to query.
            venv_root: Venv root path.
            plugin_name: Module FQCN or short-form name.

        Returns:
            Cached result or ``None`` on miss.
        """
        with self._lock:
            key = self._make_key(venv_root, plugin_name)
            if key is None:
                self._misses[store] += 1
                return None
            lru = self._stores[store]
            result = lru.get(key)
            if result is not None:
                lru.move_to_end(key)
                self._hits[store] += 1
                return result
            self._misses[store] += 1
            return None

    def put(
        self,
        store: CacheType,
        venv_root: str,
        plugin_name: str,
        result: object,
        *,
        resolved_fqcn: str = "",
    ) -> None:
        """Store a result in the cache.

        If ``resolved_fqcn`` is provided and differs from ``plugin_name``,
        the result is also stored under the FQCN key so that later lookups
        by FQCN (e.g. from L058/L059 after M001 resolves short names) hit.

        Args:
            store: Which cache store to use.
            venv_root: Venv root path.
            plugin_name: Module name as provided to the subprocess.
            result: Subprocess result to cache.
            resolved_fqcn: Resolved FQCN if different from plugin_name.
        """
        with self._lock:
            lru = self._stores[store]

            key = self._make_key(venv_root, plugin_name)
            if key is not None:
                lru[key] = result
                lru.move_to_end(key)

            if resolved_fqcn and resolved_fqcn != plugin_name:
                fqcn_key = self._make_key(venv_root, resolved_fqcn)
                if fqcn_key is not None:
                    lru[fqcn_key] = result
                    lru.move_to_end(fqcn_key)

            while len(lru) > self._max:
                lru.popitem(last=False)

    def partition(
        self,
        store: CacheType,
        venv_root: str,
        modules: list[str],
    ) -> tuple[dict[str, object], list[str]]:
        """Split a module list into cached results and uncached names.

        Args:
            store: Which cache store to query.
            venv_root: Venv root path.
            modules: Module names to check.

        Returns:
            ``(cached_results, uncached_modules)`` where cached_results maps
            module name to its cached result.
        """
        cached: dict[str, object] = {}
        uncached: list[str] = []
        for module in modules:
            result = self.get(store, venv_root, module)
            if result is not None:
                cached[module] = result
            else:
                uncached.append(module)
        return cached, uncached

    def _stats_locked(self) -> dict[str, int]:
        """Compute cumulative counters; caller must hold ``self._lock``.

        Returns:
            Dict with keys like ``cache_introspect_hits``,
            ``cache_introspect_misses``, etc.
        """
        result: dict[str, int] = {}
        for store_name in _STORES:
            result[f"cache_{store_name}_hits"] = self._hits[store_name]
            result[f"cache_{store_name}_misses"] = self._misses[store_name]
        return result

    def stats(self) -> dict[str, int]:
        """Return hit/miss counts for diagnostics.

        Counters are cumulative for the daemon process lifetime; use
        :meth:`snapshot` + :meth:`stats_since` for per-scan deltas.

        Returns:
            Dict with keys like ``cache_introspect_hits``,
            ``cache_introspect_misses``, etc.
        """
        with self._lock:
            return self._stats_locked()

    def snapshot(self) -> CacheSnapshot:
        """Capture cumulative counters for later per-scan differencing.

        Deltas are approximate under concurrent scans against the shared
        daemon cache: interleaved scans share the counters, so attribute
        per-scan numbers exactly only for sequential scans (CLI daemon,
        convergence-loop rescans).

        Returns:
            Counter snapshot to pass to :meth:`stats_since` at scan end.
        """
        return self.stats()

    def stats_since(self, snapshot: CacheSnapshot) -> dict[str, int]:
        """Return hit/miss counts accumulated since a snapshot.

        Missing or non-integer keys are treated as zero; negative deltas
        (snapshot newer than current, e.g. concurrent scans or a counter
        reset) are clamped to zero. Never raises on a malformed snapshot —
        diagnostics must not fail the scan.

        Args:
            snapshot: Earlier counter mapping from :meth:`snapshot`.

        Returns:
            Dict with keys like ``scan_cache_introspect_hits``,
            ``scan_cache_introspect_misses``, etc.
        """
        with self._lock:
            current = self._stats_locked()
        if not isinstance(snapshot, Mapping):
            snapshot = {}
        result: dict[str, int] = {}
        for store_name in _STORES:
            for kind in ("hits", "misses"):
                base_key = f"cache_{store_name}_{kind}"
                now = current.get(base_key, 0)
                then = snapshot.get(base_key, 0)
                now_int = now if isinstance(now, int) else 0
                then_int = then if isinstance(then, int) else 0
                result[f"scan_{base_key}"] = max(now_int - then_int, 0)
        return result


plugin_cache = PluginCache()
