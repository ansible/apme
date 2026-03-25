"""Tests for Ansible role inventory collector."""

from __future__ import annotations

from pathlib import Path

import pytest

from apme_engine.sbom.collect_roles import collect_roles
from apme_engine.sbom.models import APME_PROPERTY_NAMESPACE, ComponentType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_role(
    roles_dir: Path,
    name: str,
    *,
    meta_content: str | None = None,
    bare: bool = False,
) -> Path:
    """Create a fake role directory.

    Args:
        roles_dir: Parent roles/ directory.
        name: Role directory name.
        meta_content: YAML content for meta/main.yml.
        bare: If True, create only tasks/main.yml (no meta).

    Returns:
        Path to the role directory.
    """
    role_dir = roles_dir / name
    role_dir.mkdir(parents=True, exist_ok=True)
    if meta_content is not None:
        meta_dir = role_dir / "meta"
        meta_dir.mkdir()
        (meta_dir / "main.yml").write_text(meta_content)
    if bare or meta_content is None:
        tasks_dir = role_dir / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        (tasks_dir / "main.yml").write_text("---\n- name: Hello\n  debug:\n    msg: hello\n")
    return role_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCollectRolesWithMeta:
    """Tests for roles with meta/main.yml."""

    def test_role_with_full_galaxy_info(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        roles = target / "roles"
        roles.mkdir(parents=True)
        _make_role(roles, "my_role", meta_content="""\
---
galaxy_info:
  role_name: my_role
  version: 1.2.0
  author: Jane Doe
  company: Acme Inc
  license: MIT
  description: A test role
  min_ansible_version: "2.9"
""")
        components = collect_roles(target)
        assert len(components) == 1
        c = components[0]
        assert c.name == "my_role"
        assert c.version == "1.2.0"
        assert c.type == ComponentType.LIBRARY
        assert c.author == "Jane Doe"
        assert c.supplier.name == "Acme Inc"
        assert c.description == "A test role"
        assert len(c.licenses) == 1
        assert c.licenses[0].license_id == "MIT"
        assert "pkg:generic/my_role@1.2.0" in c.purl
        # min_ansible_version as property
        props = {p.name: p.value for p in c.properties}
        assert props.get(f"{APME_PROPERTY_NAMESPACE}:min-ansible-version") == "2.9"

    def test_role_name_fallback_to_dir_name(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        roles = target / "roles"
        roles.mkdir(parents=True)
        # galaxy_info without role_name
        _make_role(roles, "webserver", meta_content="""\
---
galaxy_info:
  author: Bob
  version: 0.5.0
""")
        components = collect_roles(target)
        assert len(components) == 1
        assert components[0].name == "webserver"

    def test_version_fallback_to_unversioned(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        roles = target / "roles"
        roles.mkdir(parents=True)
        _make_role(roles, "norole", meta_content="""\
---
galaxy_info:
  role_name: norole
  author: Bob
""")
        components = collect_roles(target)
        assert components[0].version == "unversioned"


class TestCollectBareRoles:
    """Tests for bare roles (tasks/main.yml only, no meta)."""

    def test_bare_role_included(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        roles = target / "roles"
        roles.mkdir(parents=True)
        _make_role(roles, "simple_task", bare=True)
        components = collect_roles(target)
        assert len(components) == 1
        c = components[0]
        assert c.name == "simple_task"
        assert c.version == "unversioned"
        # Should have name-inferred property
        props = {p.name: p.value for p in c.properties}
        assert f"{APME_PROPERTY_NAMESPACE}:name-source" in props

    def test_bare_role_uses_dir_name(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        roles = target / "roles"
        roles.mkdir(parents=True)
        _make_role(roles, "my_bare_role", bare=True)
        components = collect_roles(target)
        assert components[0].name == "my_bare_role"


class TestCollectRolesEdgeCases:
    """Edge cases for role collection."""

    def test_no_roles_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        target.mkdir()
        components = collect_roles(target)
        assert components == []

    def test_empty_roles_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        (target / "roles").mkdir(parents=True)
        components = collect_roles(target)
        assert components == []

    def test_collection_embedded_roles_not_scanned(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        target.mkdir()
        # Roles inside ansible_collections should NOT be found
        coll_roles = target / "ansible_collections" / "ns" / "coll" / "roles"
        coll_roles.mkdir(parents=True)
        _make_role(coll_roles, "embedded_role", meta_content="""\
---
galaxy_info:
  role_name: embedded_role
  version: 1.0.0
""")
        components = collect_roles(target)
        assert components == []

    def test_malformed_meta_falls_back(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        roles = target / "roles"
        roles.mkdir(parents=True)
        role_dir = roles / "broken_role"
        role_dir.mkdir()
        (role_dir / "meta").mkdir()
        (role_dir / "meta" / "main.yml").write_text("{{{{not valid yaml at all")
        (role_dir / "tasks").mkdir()
        (role_dir / "tasks" / "main.yml").write_text("---\n- debug: msg=hi\n")
        components = collect_roles(target)
        assert len(components) == 1
        assert components[0].name == "broken_role"
        assert components[0].version == "unversioned"

    def test_multiple_roles(self, tmp_path: Path) -> None:
        target = tmp_path / "project"
        roles = target / "roles"
        roles.mkdir(parents=True)
        _make_role(roles, "role_a", meta_content="""\
---
galaxy_info:
  role_name: role_a
  version: 1.0.0
""")
        _make_role(roles, "role_b", bare=True)
        components = collect_roles(target)
        names = {c.name for c in components}
        assert names == {"role_a", "role_b"}

    def test_non_role_subdir_ignored(self, tmp_path: Path) -> None:
        """Subdirectories without meta/main.yml or tasks/main.yml are skipped."""
        target = tmp_path / "project"
        roles = target / "roles"
        roles.mkdir(parents=True)
        # Just a random directory, not a role
        random_dir = roles / "not_a_role"
        random_dir.mkdir()
        (random_dir / "README.md").write_text("Not a role")
        components = collect_roles(target)
        assert components == []
