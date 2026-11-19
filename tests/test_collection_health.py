"""Unit and integration tests for the Collection Health Validator (ADR-051)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from apme_engine.validators.collection_health.cache import (
    _cache_key,
    compute_cache_schema,
    get_cached,
    put_cached,
)
from apme_engine.validators.collection_health.scanner import (
    CURATED_RULE_IDS,
    _discover_collection_dirs,
    _read_collection_version,
    _scan_collection,
    scan_collections,
)


class TestCacheKey:
    """Tests for cache key generation."""

    def test_deterministic(self) -> None:
        """Same inputs produce same key."""
        k1 = _cache_key("community.general", "8.0.0", "schema1")
        k2 = _cache_key("community.general", "8.0.0", "schema1")
        assert k1 == k2

    def test_different_versions_different_keys(self) -> None:
        """Different versions produce different keys."""
        k1 = _cache_key("community.general", "8.0.0", "schema1")
        k2 = _cache_key("community.general", "9.0.0", "schema1")
        assert k1 != k2

    def test_different_schemas_different_keys(self) -> None:
        """Different schemas produce different keys."""
        k1 = _cache_key("community.general", "8.0.0", "schema1")
        k2 = _cache_key("community.general", "8.0.0", "schema2")
        assert k1 != k2


class TestCacheOperations:
    """Tests for persistent cache read/write."""

    def test_roundtrip(self, tmp_path: Path) -> None:
        """Put then get returns the same findings.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        findings: list[dict[str, str | int | list[int] | bool | None]] = [{"rule_id": "L089", "message": "test"}]
        with patch("apme_engine.validators.collection_health.cache._CACHE_DIR", tmp_path):
            put_cached("ns.coll", "1.0.0", "s1", findings)
            result = get_cached("ns.coll", "1.0.0", "s1")
        assert result == findings

    def test_miss_returns_none(self, tmp_path: Path) -> None:
        """Cache miss returns None.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        with patch("apme_engine.validators.collection_health.cache._CACHE_DIR", tmp_path):
            assert get_cached("ns.coll", "1.0.0", "s1") is None

    def test_schema_mismatch_returns_none(self, tmp_path: Path) -> None:
        """Stale schema entry returns None.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        findings: list[dict[str, str | int | list[int] | bool | None]] = [{"rule_id": "L089"}]
        with patch("apme_engine.validators.collection_health.cache._CACHE_DIR", tmp_path):
            put_cached("ns.coll", "1.0.0", "old_schema", findings)
            assert get_cached("ns.coll", "1.0.0", "new_schema") is None


class TestComputeCacheSchema:
    """Tests for cache schema computation."""

    def test_deterministic(self) -> None:
        """Same rule set produces same schema."""
        s1 = compute_cache_schema(("L089", "M001"))
        compute_cache_schema.cache_clear()
        s2 = compute_cache_schema(("L089", "M001"))
        compute_cache_schema.cache_clear()
        assert s1 == s2

    def test_different_rules_different_schema(self) -> None:
        """Different rule sets produce different schemas."""
        s1 = compute_cache_schema(("L089",))
        compute_cache_schema.cache_clear()
        s2 = compute_cache_schema(("L089", "M001"))
        compute_cache_schema.cache_clear()
        assert s1 != s2


class TestDiscoverCollectionDirs:
    """Tests for collection directory discovery."""

    def test_finds_collections(self, tmp_path: Path) -> None:
        """Discovers namespace.name directories under ansible_collections.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        coll_dir = tmp_path / "lib" / "python3.12" / "site-packages" / "ansible_collections" / "community" / "general"
        coll_dir.mkdir(parents=True)
        manifest = coll_dir / "MANIFEST.json"
        manifest.write_text(json.dumps({"collection_info": {"version": "8.0.0"}}))

        result = _discover_collection_dirs(tmp_path)
        assert len(result) == 1
        fqcn, version, path = result[0]
        assert fqcn == "community.general"
        assert version == "8.0.0"
        assert path == coll_dir

    def test_skips_internal(self, tmp_path: Path) -> None:
        """Skips directories starting with underscore.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        internal = tmp_path / "lib" / "python3.12" / "site-packages" / "ansible_collections" / "ansible" / "_internal"
        internal.mkdir(parents=True)
        result = _discover_collection_dirs(tmp_path)
        assert len(result) == 0

    def test_no_lib(self, tmp_path: Path) -> None:
        """Returns empty list when lib/ doesn't exist.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert _discover_collection_dirs(tmp_path) == []


class TestReadCollectionVersion:
    """Tests for reading collection version from metadata files."""

    def test_manifest_json(self, tmp_path: Path) -> None:
        """Reads version from MANIFEST.json.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "MANIFEST.json").write_text(json.dumps({"collection_info": {"version": "3.2.1"}}))
        assert _read_collection_version(tmp_path) == "3.2.1"

    def test_galaxy_yml(self, tmp_path: Path) -> None:
        """Reads version from galaxy.yml fallback.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        (tmp_path / "galaxy.yml").write_text("version: 2.5.0\n")
        assert _read_collection_version(tmp_path) == "2.5.0"

    def test_no_metadata(self, tmp_path: Path) -> None:
        """Returns empty string when no metadata files exist.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert _read_collection_version(tmp_path) == ""


class TestScanCollections:
    """Tests for the aggregated collection scanning function."""

    def test_no_collections_empty(self, tmp_path: Path) -> None:
        """Returns empty list when no collections are installed.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        assert scan_collections(tmp_path) == []

    def test_uses_cache(self, tmp_path: Path) -> None:
        """Uses cached results when available.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        coll_dir = tmp_path / "lib" / "python3.12" / "site-packages" / "ansible_collections" / "ns" / "coll"
        coll_dir.mkdir(parents=True)
        (coll_dir / "MANIFEST.json").write_text(json.dumps({"collection_info": {"version": "1.0.0"}}))

        cached_findings: list[dict[str, str | int | list[int] | bool | None]] = [
            {"rule_id": "L089", "message": "cached"},
        ]

        with (
            patch(
                "apme_engine.validators.collection_health.cache.get_cached",
                return_value=cached_findings,
            ),
            patch("apme_engine.validators.collection_health.cache.compute_cache_schema", return_value="s1"),
        ):
            result = scan_collections(tmp_path)

        assert len(result) == 1
        assert result[0]["message"] == "cached"

    def test_cache_bust_on_rescan(self, tmp_path: Path) -> None:
        """Skips cache when rescan=True.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        coll_dir = tmp_path / "lib" / "python3.12" / "site-packages" / "ansible_collections" / "ns" / "coll"
        coll_dir.mkdir(parents=True)
        (coll_dir / "MANIFEST.json").write_text(json.dumps({"collection_info": {"version": "1.0.0"}}))

        with (
            patch("apme_engine.validators.collection_health.cache.get_cached") as mock_get,
            patch("apme_engine.validators.collection_health.cache.put_cached"),
            patch("apme_engine.validators.collection_health.scanner._scan_collection", return_value=[]),
            patch("apme_engine.validators.collection_health.cache.compute_cache_schema", return_value="s1"),
        ):
            scan_collections(tmp_path, rescan=True)
            mock_get.assert_not_called()


class TestCollectionHealthServicer:
    """Tests for the collection health gRPC servicer."""

    async def test_validate_no_venv_path(self) -> None:
        """Returns empty violations when no venv_path provided."""
        from apme.v1.validate_pb2 import ValidateRequest
        from apme_engine.daemon.collection_health_server import CollectionHealthValidatorServicer

        servicer = CollectionHealthValidatorServicer()
        request = ValidateRequest(request_id="test-1", venv_path="")
        ctx = MagicMock()
        resp = await servicer.Validate(request, ctx)
        assert len(resp.violations) == 0

    async def test_validate_with_findings(self) -> None:
        """Returns violations from collection scanning."""
        from apme.v1.validate_pb2 import ValidateRequest
        from apme_engine.daemon.collection_health_server import CollectionHealthValidatorServicer

        violations = [
            {
                "rule_id": "L089",
                "severity": "medium",
                "message": "Missing type hints",
                "file": "plugins/modules/my_module.py",
                "line": 10,
                "path": "",
                "scope": "collection",
                "source": "native",
            }
        ]

        servicer = CollectionHealthValidatorServicer()
        request = ValidateRequest(request_id="test-2", venv_path="/tmp/fakevenv")
        ctx = MagicMock()

        with patch("apme_engine.daemon.collection_health_server._run_scan", return_value=violations):
            resp = await servicer.Validate(request, ctx)

        assert len(resp.violations) == 1
        assert resp.HasField("diagnostics")  # type: ignore[attr-defined]
        assert resp.diagnostics.validator_name == "collection_health"

    async def test_health(self) -> None:
        """Health returns ok (collection scanner is pure Python)."""
        from apme.v1.common_pb2 import HealthRequest
        from apme_engine.daemon.collection_health_server import CollectionHealthValidatorServicer

        servicer = CollectionHealthValidatorServicer()
        ctx = MagicMock()
        resp = await servicer.Health(HealthRequest(), ctx)
        assert resp.status == "ok"


class TestCuratedRuleIds:
    """Tests for the curated rule ID set."""

    def test_curated_rules_cover_deprecated_and_risk(self) -> None:
        """Curated rules cover deprecated patterns and risk/security indicators only."""
        assert "M005" in CURATED_RULE_IDS
        assert "M010" in CURATED_RULE_IDS
        assert "R101" in CURATED_RULE_IDS
        assert "R108" in CURATED_RULE_IDS

    def test_no_play_level_rules(self) -> None:
        """Play-level rules are excluded from collection scanning."""
        play_rules = {"L001", "L002", "L003", "L004", "L005"}
        assert not play_rules.intersection(CURATED_RULE_IDS)

    def test_curated_rules_sorted_and_unique(self) -> None:
        """CURATED_RULE_IDS is sorted and contains no duplicates."""
        assert list(CURATED_RULE_IDS) == sorted(CURATED_RULE_IDS)
        assert len(CURATED_RULE_IDS) == len(set(CURATED_RULE_IDS))

    def test_no_authoring_lint_rules(self) -> None:
        """Galaxy metadata, module/role quality, and FQCN hygiene rules are excluded."""
        authoring_lint = {
            "L027",
            "L053",
            "L077",
            "L079",  # role quality
            "L089",
            "L090",  # module quality
            "L095",
            "L103",
            "L104",
            "L105",  # galaxy metadata
            "M001",
            "M002",
            "M003",  # FQCN hygiene
        }
        assert not authoring_lint.intersection(CURATED_RULE_IDS)


def _scaffold_collection(
    base: Path,
    *,
    namespace: str = "testns",
    name: str = "testcol",
    version: str = "1.0.0",
) -> Path:
    """Create a minimal Ansible collection with a role that triggers R108.

    Args:
        base: Parent directory (used as a fake venv root).
        namespace: Collection namespace.
        name: Collection name.
        version: Collection version string.

    Returns:
        Path to the collection root directory.
    """
    coll_dir = base / "lib" / "python3.12" / "site-packages" / "ansible_collections" / namespace / name
    coll_dir.mkdir(parents=True)
    (coll_dir / "MANIFEST.json").write_text(
        json.dumps({"collection_info": {"namespace": namespace, "name": name, "version": version}})
    )
    (coll_dir / "galaxy.yml").write_text(f"namespace: {namespace}\nname: {name}\nversion: {version}\n")

    tasks_dir = coll_dir / "roles" / "escalated" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "main.yml").write_text(
        "---\n- name: Escalated task\n  become: true\n  ansible.builtin.command: whoami\n"
    )

    meta_dir = coll_dir / "roles" / "escalated" / "meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "main.yml").write_text("---\ngalaxy_info:\n  role_name: escalated\n")

    return coll_dir


class TestCollectionHealthIntegration:
    """Integration: scan a scaffolded collection through the real engine pipeline."""

    def test_scan_collection_finds_violations(self, tmp_path: Path) -> None:
        """A role with ``become: true`` should trigger R108 via the scanner.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        coll_dir = _scaffold_collection(tmp_path)

        violations = _scan_collection("testns.testcol", "1.0.0", coll_dir)

        r108_hits = [v for v in violations if v.get("rule_id") == "R108"]
        assert len(r108_hits) >= 1, (
            f"Expected R108 (privilege escalation) from become:true, "
            f"got rule_ids: {[v.get('rule_id') for v in violations]}"
        )
        assert r108_hits[0]["source"] == "collection_health"
        assert r108_hits[0]["collection_fqcn"] == "testns.testcol"
        assert r108_hits[0]["collection_version"] == "1.0.0"

    def test_scan_collections_aggregates(self, tmp_path: Path) -> None:
        """Full ``scan_collections()`` discovers and scans the scaffolded collection.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        _scaffold_collection(tmp_path)

        with patch("apme_engine.validators.collection_health.cache._CACHE_DIR", tmp_path / "cache"):
            violations = scan_collections(tmp_path, rescan=True)

        assert len(violations) >= 1, "scan_collections should find violations in scaffolded collection"
        assert all(v.get("source") == "collection_health" for v in violations)
        assert all(v.get("scope") == "collection" for v in violations)
