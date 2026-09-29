"""Tests for Ansible plugin introspection LRU cache.

Covers PluginCache, version discovery helpers, and the cached code paths
in M001_M004_introspect, L058_argspec_doc, and L059_argspec_mock.
"""

import json
from pathlib import Path
from unittest.mock import patch

from apme_engine.validators.ansible.cache import (
    PluginCache,
    _ansible_core_version,
    _collection_version,
    _parse_fqcn,
)


class TestParseFqcn:
    """Tests for _parse_fqcn helper."""

    def test_full_fqcn(self) -> None:
        """Three-part FQCN returns (namespace, name, short_name)."""
        assert _parse_fqcn("ansible.builtin.copy") == ("ansible", "builtin", "copy")

    def test_deep_fqcn(self) -> None:
        """FQCN with dots in short name preserves them."""
        assert _parse_fqcn("community.general.a.b") == ("community", "general", "a.b")

    def test_short_form_returns_none(self) -> None:
        """Short-form names (< 3 parts) return None."""
        assert _parse_fqcn("ping") is None
        assert _parse_fqcn("my.module") is None

    def test_empty_string(self) -> None:
        """Empty string returns None."""
        assert _parse_fqcn("") is None


class TestCollectionVersion:
    """Tests for _collection_version reading MANIFEST.json."""

    def test_reads_manifest(self, tmp_path: Path) -> None:
        """Reads version from MANIFEST.json in the expected path.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_dir = tmp_path / "lib" / "python3.11" / "site-packages" / "ansible_collections" / "community" / "general"
        col_dir.mkdir(parents=True)
        manifest = {"collection_info": {"version": "5.8.0"}}
        (col_dir / "MANIFEST.json").write_text(json.dumps(manifest))

        assert _collection_version(str(tmp_path), "community", "general") == "5.8.0"

    def test_missing_manifest_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty string when MANIFEST.json doesn't exist.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert _collection_version(str(tmp_path), "community", "general") == ""

    def test_malformed_manifest(self, tmp_path: Path) -> None:
        """Returns empty string when MANIFEST.json is malformed.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_dir = tmp_path / "lib" / "python3.11" / "site-packages" / "ansible_collections" / "ns" / "col"
        col_dir.mkdir(parents=True)
        (col_dir / "MANIFEST.json").write_text("not json")

        assert _collection_version(str(tmp_path), "ns", "col") == ""

    def test_manifest_missing_version_key(self, tmp_path: Path) -> None:
        """Returns empty string when version key is absent.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        col_dir = tmp_path / "lib" / "python3.11" / "site-packages" / "ansible_collections" / "ns" / "col"
        col_dir.mkdir(parents=True)
        (col_dir / "MANIFEST.json").write_text(json.dumps({"collection_info": {}}))

        assert _collection_version(str(tmp_path), "ns", "col") == ""


class TestAnsibleCoreVersion:
    """Tests for _ansible_core_version reading release.py."""

    def test_reads_release_py(self, tmp_path: Path) -> None:
        """Reads __version__ from ansible/release.py.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        release_dir = tmp_path / "lib" / "python3.11" / "site-packages" / "ansible"
        release_dir.mkdir(parents=True)
        (release_dir / "release.py").write_text("__version__ = '2.16.3'\n")

        assert _ansible_core_version(str(tmp_path)) == "2.16.3"

    def test_double_quoted(self, tmp_path: Path) -> None:
        """Handles double-quoted version strings.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        release_dir = tmp_path / "lib" / "python3.11" / "site-packages" / "ansible"
        release_dir.mkdir(parents=True)
        (release_dir / "release.py").write_text('__version__ = "2.17.0"\n')

        assert _ansible_core_version(str(tmp_path)) == "2.17.0"

    def test_missing_release_py(self, tmp_path: Path) -> None:
        """Returns empty string when release.py doesn't exist.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert _ansible_core_version(str(tmp_path)) == ""


class TestPluginCacheCore:
    """Tests for the PluginCache get/put/partition/stats lifecycle."""

    def _make_venv(self, tmp_path: Path, namespace: str, name: str, version: str) -> str:
        """Create a fake venv with a MANIFEST.json for version discovery.

        Args:
            tmp_path: Temporary directory.
            namespace: Collection namespace.
            name: Collection name.
            version: Collection version.

        Returns:
            Venv root path string.
        """
        col_dir = tmp_path / "lib" / "python3.11" / "site-packages" / "ansible_collections" / namespace / name
        col_dir.mkdir(parents=True)
        manifest = {"collection_info": {"version": version}}
        (col_dir / "MANIFEST.json").write_text(json.dumps(manifest))
        return str(tmp_path)

    def _make_builtin_venv(self, tmp_path: Path, core_version: str) -> str:
        """Create a fake venv with ansible/release.py.

        Args:
            tmp_path: Temporary directory.
            core_version: ansible-core version string.

        Returns:
            Venv root path string.
        """
        release_dir = tmp_path / "lib" / "python3.11" / "site-packages" / "ansible"
        release_dir.mkdir(parents=True)
        (release_dir / "release.py").write_text(f"__version__ = '{core_version}'\n")
        return str(tmp_path)

    def test_put_and_get_fqcn(self, tmp_path: Path) -> None:
        """FQCN put/get round-trips correctly.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()
        venv = self._make_venv(tmp_path, "community", "general", "5.8.0")

        cache.put("introspect", venv, "community.general.ping", {"fqcn": "community.general.ping"})
        result = cache.get("introspect", venv, "community.general.ping")

        assert result == {"fqcn": "community.general.ping"}

    def test_get_miss_returns_none(self, tmp_path: Path) -> None:
        """Cache miss returns None.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()
        venv = self._make_venv(tmp_path, "community", "general", "5.8.0")

        assert cache.get("introspect", venv, "community.general.ping") is None

    def test_short_form_always_misses(self, tmp_path: Path) -> None:
        """Short-form names can't be resolved without subprocess, so always miss.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()
        venv = self._make_venv(tmp_path, "community", "general", "5.8.0")

        cache.put("introspect", venv, "ping", {"fqcn": "ansible.builtin.ping"})
        assert cache.get("introspect", venv, "ping") is None

    def test_put_with_resolved_fqcn_backfills(self, tmp_path: Path) -> None:
        """Put with resolved_fqcn stores under both short-form key and FQCN.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()
        venv = self._make_builtin_venv(tmp_path, "2.16.3")
        info = {"fqcn": "ansible.builtin.ping"}

        cache.put("introspect", venv, "ping", info, resolved_fqcn="ansible.builtin.ping")
        result = cache.get("introspect", venv, "ansible.builtin.ping")

        assert result == info

    def test_ansible_builtin_uses_core_version(self, tmp_path: Path) -> None:
        """ansible.builtin.* modules key by ansible-core version.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()
        venv = self._make_builtin_venv(tmp_path, "2.16.3")

        cache.put("introspect", venv, "ansible.builtin.copy", {"fqcn": "ansible.builtin.copy"})
        assert cache.get("introspect", venv, "ansible.builtin.copy") == {"fqcn": "ansible.builtin.copy"}

    def test_different_collection_versions_miss(self, tmp_path: Path) -> None:
        """Different collection versions are separate cache entries.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()

        venv1_dir = tmp_path / "venv1"
        venv1 = self._make_venv(venv1_dir, "community", "general", "5.8.0")
        cache.put("introspect", venv1, "community.general.ping", {"v": "5.8.0"})

        venv2_dir = tmp_path / "venv2"
        venv2 = self._make_venv(venv2_dir, "community", "general", "6.0.0")
        assert cache.get("introspect", venv2, "community.general.ping") is None

    def test_same_version_cross_venv_hits(self, tmp_path: Path) -> None:
        """Same collection version in different venvs gets cache hits.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()

        venv1_dir = tmp_path / "venv1"
        venv1 = self._make_venv(venv1_dir, "community", "general", "5.8.0")
        cache.put("introspect", venv1, "community.general.ping", {"v": "5.8.0"})

        venv2_dir = tmp_path / "venv2"
        venv2 = self._make_venv(venv2_dir, "community", "general", "5.8.0")
        assert cache.get("introspect", venv2, "community.general.ping") == {"v": "5.8.0"}

    def test_partition_splits_cached_and_uncached(self, tmp_path: Path) -> None:
        """Partition returns cached results and uncached module names.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()
        venv = self._make_venv(tmp_path, "community", "general", "5.8.0")

        cache.put("docspec", venv, "community.general.ping", {"options": {}})

        cached, uncached = cache.partition(
            "docspec",
            venv,
            ["community.general.ping", "community.general.uri", "shortform"],
        )

        assert "community.general.ping" in cached
        assert "community.general.uri" in uncached
        assert "shortform" in uncached

    def test_stats_tracks_hits_and_misses(self, tmp_path: Path) -> None:
        """Stats correctly count hits and misses.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()
        venv = self._make_venv(tmp_path, "community", "general", "5.8.0")

        cache.put("introspect", venv, "community.general.ping", {"data": True})
        cache.get("introspect", venv, "community.general.ping")  # hit
        cache.get("introspect", venv, "community.general.uri")  # miss
        cache.get("introspect", venv, "shortform")  # miss (short-form)

        s = cache.stats()
        assert s["cache_introspect_hits"] == 1
        assert s["cache_introspect_misses"] == 2
        assert s["cache_docspec_hits"] == 0

    def test_stats_since_reports_deltas(self, tmp_path: Path) -> None:
        """stats_since returns per-scan deltas against a snapshot.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()
        venv = self._make_venv(tmp_path, "community", "general", "5.8.0")

        cache.put("introspect", venv, "community.general.ping", {"data": True})
        cache.get("introspect", venv, "community.general.ping")  # hit (before snapshot)
        snap = cache.snapshot()
        cache.get("introspect", venv, "community.general.ping")  # hit (after snapshot)
        cache.get("introspect", venv, "community.general.uri")  # miss (after snapshot)

        delta = cache.stats_since(snap)
        assert delta["scan_cache_introspect_hits"] == 1
        assert delta["scan_cache_introspect_misses"] == 1
        assert delta["scan_cache_docspec_hits"] == 0
        # Cumulative counters are unaffected by snapshotting.
        total = cache.stats()
        assert total["cache_introspect_hits"] == 2
        assert total["cache_introspect_misses"] == 1

    def test_stats_since_missing_keys_treated_as_zero(self, tmp_path: Path) -> None:
        """Unknown snapshot keys and newer snapshots clamp to zero.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()
        delta = cache.stats_since({})
        assert delta["scan_cache_introspect_hits"] == 0
        assert delta["scan_cache_mockspec_misses"] == 0
        future = {f"cache_{s}_{k}": 10**9 for s in ("introspect", "docspec", "mockspec") for k in ("hits", "misses")}
        clamped = cache.stats_since(future)
        assert all(v == 0 for v in clamped.values())

    def test_lru_eviction(self, tmp_path: Path) -> None:
        """Oldest entries are evicted when max_entries is exceeded.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache(max_entries=3)
        venv = self._make_venv(tmp_path, "community", "general", "5.8.0")

        for i in range(5):
            cache.put("introspect", venv, f"community.general.mod{i}", {"i": i})

        assert cache.get("introspect", venv, "community.general.mod0") is None
        assert cache.get("introspect", venv, "community.general.mod1") is None
        assert cache.get("introspect", venv, "community.general.mod4") == {"i": 4}

    def test_stores_are_independent(self, tmp_path: Path) -> None:
        """Each store (introspect, docspec, mockspec) is independent.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()
        venv = self._make_venv(tmp_path, "community", "general", "5.8.0")

        cache.put("introspect", venv, "community.general.ping", {"store": "introspect"})
        cache.put("docspec", venv, "community.general.ping", {"store": "docspec"})

        assert cache.get("introspect", venv, "community.general.ping") == {"store": "introspect"}
        assert cache.get("docspec", venv, "community.general.ping") == {"store": "docspec"}
        assert cache.get("mockspec", venv, "community.general.ping") is None

    def test_no_version_means_no_cache(self, tmp_path: Path) -> None:
        """FQCN with no discoverable version returns None (no cache).

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cache = PluginCache()
        venv = str(tmp_path)

        cache.put("introspect", venv, "community.general.ping", {"data": True})
        assert cache.get("introspect", venv, "community.general.ping") is None


class TestM001M004CachedPath:
    """Tests for the cache-integrated M001-M004 introspection."""

    def _make_venv_with_python(self, tmp_path: Path) -> Path:
        """Create a fake venv directory with a python binary stub.

        Args:
            tmp_path: Temporary directory.

        Returns:
            Path to venv root.
        """
        venv = tmp_path / "venv"
        bin_dir = venv / "bin"
        bin_dir.mkdir(parents=True)
        python = bin_dir / "python"
        python.write_text("#!/bin/sh\necho 'fake'")
        python.chmod(0o755)

        col_dir = venv / "lib" / "python3.11" / "site-packages" / "ansible_collections" / "community" / "general"
        col_dir.mkdir(parents=True)
        manifest = {"collection_info": {"version": "5.8.0"}}
        (col_dir / "MANIFEST.json").write_text(json.dumps(manifest))
        return venv

    def test_second_call_uses_cache(self, tmp_path: Path) -> None:
        """Second introspection call for same FQCN hits cache, no subprocess.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.validators.ansible.cache import PluginCache
        from apme_engine.validators.ansible.rules import M001_M004_introspect

        venv = self._make_venv_with_python(tmp_path)
        fresh_cache = PluginCache()

        subprocess_result = json.dumps(
            {
                "community.general.ping": {
                    "fqcn": "community.general.ping",
                    "deprecated": False,
                    "warnings": [],
                    "redirects": [],
                    "removed": False,
                    "removal_msg": "",
                    "plugin_path": "/some/path",
                }
            }
        )

        with (
            patch.object(M001_M004_introspect, "plugin_cache", fresh_cache),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = type("Result", (), {"returncode": 0, "stdout": subprocess_result, "stderr": ""})()

            result1 = M001_M004_introspect._run_introspection(
                ["community.general.ping"],
                venv,
            )
            assert "community.general.ping" in result1
            assert mock_run.call_count == 1

            result2 = M001_M004_introspect._run_introspection(
                ["community.general.ping"],
                venv,
            )
            assert "community.general.ping" in result2
            assert mock_run.call_count == 1  # no additional call

    def test_short_form_backfills_fqcn(self, tmp_path: Path) -> None:
        """Short-form names always go to subprocess but FQCN gets backfilled.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.validators.ansible.cache import PluginCache
        from apme_engine.validators.ansible.rules import M001_M004_introspect

        venv = self._make_venv_with_python(tmp_path)

        release_dir = venv / "lib" / "python3.11" / "site-packages" / "ansible"
        release_dir.mkdir(parents=True)
        (release_dir / "release.py").write_text("__version__ = '2.16.3'\n")

        fresh_cache = PluginCache()
        subprocess_result = json.dumps(
            {
                "ping": {
                    "fqcn": "ansible.builtin.ping",
                    "deprecated": False,
                    "warnings": [],
                    "redirects": [],
                    "removed": False,
                    "removal_msg": "",
                    "plugin_path": "/some/path",
                }
            }
        )

        with (
            patch.object(M001_M004_introspect, "plugin_cache", fresh_cache),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = type("Result", (), {"returncode": 0, "stdout": subprocess_result, "stderr": ""})()

            M001_M004_introspect._run_introspection(["ping"], venv)

            hit = fresh_cache.get("introspect", str(venv), "ansible.builtin.ping")
            assert hit is not None
            assert isinstance(hit, dict)
            assert hit["fqcn"] == "ansible.builtin.ping"

    def test_mixed_cached_and_uncached(self, tmp_path: Path) -> None:
        """Partition splits modules; only uncached ones go to subprocess.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.validators.ansible.cache import PluginCache
        from apme_engine.validators.ansible.rules import M001_M004_introspect

        venv = self._make_venv_with_python(tmp_path)
        fresh_cache = PluginCache()

        pre_cached = {
            "fqcn": "community.general.ping",
            "deprecated": False,
            "warnings": [],
            "redirects": [],
            "removed": False,
            "removal_msg": "",
            "plugin_path": "",
        }
        fresh_cache.put("introspect", str(venv), "community.general.ping", pre_cached)

        subprocess_result = json.dumps(
            {
                "community.general.uri": {
                    "fqcn": "community.general.uri",
                    "deprecated": False,
                    "warnings": [],
                    "redirects": [],
                    "removed": False,
                    "removal_msg": "",
                    "plugin_path": "",
                }
            }
        )

        with (
            patch.object(M001_M004_introspect, "plugin_cache", fresh_cache),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = type("Result", (), {"returncode": 0, "stdout": subprocess_result, "stderr": ""})()

            result = M001_M004_introspect._run_introspection(
                ["community.general.ping", "community.general.uri"],
                venv,
            )

            assert "community.general.ping" in result
            assert "community.general.uri" in result
            assert mock_run.call_count == 1

            call_input = mock_run.call_args[1].get("input") or mock_run.call_args[0][0] if mock_run.call_args[0] else ""
            if isinstance(call_input, str):
                sent = json.loads(call_input)
                assert "community.general.ping" not in sent["modules"]
                assert "community.general.uri" in sent["modules"]


class TestL058HostSideChecking:
    """Tests for L058 _check_tasks_against_specs (host-side validation)."""

    def test_unknown_param(self) -> None:
        """Detects unsupported parameters."""
        from apme_engine.validators.ansible.rules.L058_argspec_doc import _check_tasks_against_specs

        specs: dict[str, object] = {
            "community.general.ping": {"options": {"data": {"type": "str"}}},
        }
        tasks: list[dict[str, object]] = [
            {"module": "community.general.ping", "module_options": {"bogus": "val"}, "key": "t1"},
        ]

        violations = _check_tasks_against_specs(specs, tasks)
        assert len(violations) == 1
        assert "Unsupported parameters" in str(violations[0]["message"])

    def test_missing_required(self) -> None:
        """Detects missing required parameters."""
        from apme_engine.validators.ansible.rules.L058_argspec_doc import _check_tasks_against_specs

        specs: dict[str, object] = {
            "my.mod.x": {"options": {"name": {"type": "str", "required": True}}},
        }
        tasks: list[dict[str, object]] = [
            {"module": "my.mod.x", "module_options": {"other": "val"}, "key": "t1"},
        ]

        violations = _check_tasks_against_specs(specs, tasks)
        assert any("Missing required" in str(v["message"]) for v in violations)

    def test_invalid_choice(self) -> None:
        """Detects invalid choice values."""
        from apme_engine.validators.ansible.rules.L058_argspec_doc import _check_tasks_against_specs

        specs: dict[str, object] = {
            "my.mod.x": {"options": {"state": {"type": "str", "choices": ["present", "absent"]}}},
        }
        tasks: list[dict[str, object]] = [
            {"module": "my.mod.x", "module_options": {"state": "running"}, "key": "t1"},
        ]

        violations = _check_tasks_against_specs(specs, tasks)
        assert any("not one of" in str(v["message"]) for v in violations)

    def test_free_form_skipped(self) -> None:
        """Modules with free_form option skip validation."""
        from apme_engine.validators.ansible.rules.L058_argspec_doc import _check_tasks_against_specs

        specs: dict[str, object] = {
            "my.mod.x": {"options": {"free_form": {"type": "str"}, "name": {"required": True}}},
        }
        tasks: list[dict[str, object]] = [
            {"module": "my.mod.x", "module_options": {"anything": "goes"}, "key": "t1"},
        ]

        violations = _check_tasks_against_specs(specs, tasks)
        assert len(violations) == 0

    def test_jinja_skipped(self) -> None:
        """Tasks with Jinja templates in values skip validation."""
        from apme_engine.validators.ansible.rules.L058_argspec_doc import _check_tasks_against_specs

        specs: dict[str, object] = {
            "my.mod.x": {"options": {"name": {"type": "str", "required": True}}},
        }
        tasks: list[dict[str, object]] = [
            {"module": "my.mod.x", "module_options": {"name": "{{ var }}"}, "key": "t1"},
        ]

        violations = _check_tasks_against_specs(specs, tasks)
        assert len(violations) == 0


class TestL059HostSideChecking:
    """Tests for L059 _check_tasks_against_specs (host-side validation)."""

    def test_unknown_param(self) -> None:
        """Detects unsupported parameters."""
        from apme_engine.validators.ansible.rules.L059_argspec_mock import _check_tasks_against_specs

        specs: dict[str, object] = {
            "my.mod.x": {"argument_spec": {"name": {"type": "str"}}},
        }
        tasks: list[dict[str, object]] = [
            {"module": "my.mod.x", "module_options": {"bogus": "val"}, "key": "t1"},
        ]

        violations = _check_tasks_against_specs(specs, tasks)
        assert len(violations) == 1
        assert "Unsupported parameters" in str(violations[0]["message"])

    def test_mutually_exclusive(self) -> None:
        """Detects mutually exclusive parameter violations."""
        from apme_engine.validators.ansible.rules.L059_argspec_mock import _check_tasks_against_specs

        specs: dict[str, object] = {
            "my.mod.x": {
                "argument_spec": {"src": {"type": "str"}, "content": {"type": "str"}},
                "mutually_exclusive": [["src", "content"]],
            },
        }
        tasks: list[dict[str, object]] = [
            {"module": "my.mod.x", "module_options": {"src": "a", "content": "b"}, "key": "t1"},
        ]

        violations = _check_tasks_against_specs(specs, tasks)
        assert any("mutually exclusive" in str(v["message"]) for v in violations)

    def test_required_together(self) -> None:
        """Detects required_together violations."""
        from apme_engine.validators.ansible.rules.L059_argspec_mock import _check_tasks_against_specs

        specs: dict[str, object] = {
            "my.mod.x": {
                "argument_spec": {"host": {"type": "str"}, "port": {"type": "int"}},
                "required_together": [["host", "port"]],
            },
        }
        tasks: list[dict[str, object]] = [
            {"module": "my.mod.x", "module_options": {"host": "localhost"}, "key": "t1"},
        ]

        violations = _check_tasks_against_specs(specs, tasks)
        assert any("must be used together" in str(v["message"]) for v in violations)

    def test_aliases_accepted(self) -> None:
        """Parameter aliases are recognized as valid."""
        from apme_engine.validators.ansible.rules.L059_argspec_mock import _check_tasks_against_specs

        specs: dict[str, object] = {
            "my.mod.x": {
                "argument_spec": {"name": {"type": "str", "aliases": ["hostname"]}},
            },
        }
        tasks: list[dict[str, object]] = [
            {"module": "my.mod.x", "module_options": {"hostname": "box1"}, "key": "t1"},
        ]

        violations = _check_tasks_against_specs(specs, tasks)
        assert len(violations) == 0


class TestL058CachedRun:
    """Tests for L058 run() with the cache path."""

    def test_cached_specs_skip_subprocess(self, tmp_path: Path) -> None:
        """When specs are already cached, no subprocess is spawned.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.validators.ansible.cache import PluginCache
        from apme_engine.validators.ansible.rules import L058_argspec_doc

        venv = tmp_path / "venv"
        (venv / "bin").mkdir(parents=True)

        col_dir = venv / "lib" / "python3.11" / "site-packages" / "ansible_collections" / "community" / "general"
        col_dir.mkdir(parents=True)
        (col_dir / "MANIFEST.json").write_text(json.dumps({"collection_info": {"version": "5.8.0"}}))

        fresh_cache = PluginCache()
        spec = {"options": {"data": {"type": "str"}}}
        fresh_cache.put("docspec", str(venv), "community.general.ping", spec)

        task_nodes: list[dict[str, object]] = [
            {
                "module": "community.general.ping",
                "module_options": {"bogus_param": "val"},
                "key": "task-1",
                "file": "playbook.yml",
                "line": [10],
            },
        ]

        with (
            patch.object(L058_argspec_doc, "plugin_cache", fresh_cache),
            patch("subprocess.run") as mock_run,
        ):
            violations = L058_argspec_doc.run(task_nodes, venv)

            assert mock_run.call_count == 0
            assert len(violations) == 1
            assert violations[0]["rule_id"] == "L058"
            assert "Unsupported parameters" in str(violations[0]["message"])


class TestL059CachedRun:
    """Tests for L059 run() with the cache path."""

    def test_cached_specs_skip_subprocess(self, tmp_path: Path) -> None:
        """When argspecs are already cached, no subprocess is spawned.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        from apme_engine.validators.ansible.cache import PluginCache
        from apme_engine.validators.ansible.rules import L059_argspec_mock

        venv = tmp_path / "venv"
        (venv / "bin").mkdir(parents=True)

        col_dir = venv / "lib" / "python3.11" / "site-packages" / "ansible_collections" / "community" / "general"
        col_dir.mkdir(parents=True)
        (col_dir / "MANIFEST.json").write_text(json.dumps({"collection_info": {"version": "5.8.0"}}))

        fresh_cache = PluginCache()
        spec = {
            "argument_spec": {"name": {"type": "str"}},
            "mutually_exclusive": [],
            "required_together": [],
        }
        fresh_cache.put("mockspec", str(venv), "community.general.ping", spec)

        task_nodes: list[dict[str, object]] = [
            {
                "module": "community.general.ping",
                "module_options": {"bad_param": "val"},
                "key": "task-1",
                "file": "playbook.yml",
                "line": [10],
            },
        ]

        with (
            patch.object(L059_argspec_mock, "plugin_cache", fresh_cache),
            patch("subprocess.run") as mock_run,
        ):
            violations = L059_argspec_mock.run(task_nodes, venv)

            assert mock_run.call_count == 0
            assert len(violations) == 1
            assert violations[0]["rule_id"] == "L059"
            assert "Unsupported parameters" in str(violations[0]["message"])
