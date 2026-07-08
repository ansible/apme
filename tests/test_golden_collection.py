"""Integration tests using the ansible-creator scaffolded golden collection.

The ``tests/fixtures/golden-collection/`` directory contains a reference
Ansible collection scaffolded by ``ansible-creator``.  The baseline test
asserts it produces zero violations from collection-level rules.  Per-rule
tests copy the fixture into ``tmp_path``, remove or modify specific files,
and assert the corresponding rule fires.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from apme_engine.engine.graph_builder import GraphBuilder
from apme_engine.graph.content_graph import ContentGraph
from apme_engine.graph.rule_base import GraphRule
from apme_engine.graph.rules.L074_no_dashes_in_role_name_graph import NoDashesInRoleNameGraphRule
from apme_engine.graph.rules.L077_role_arg_specs_graph import RoleArgSpecsGraphRule
from apme_engine.graph.rules.L080_internal_var_prefix_graph import InternalVarPrefixGraphRule
from apme_engine.graph.rules.L081_numbered_names_graph import NumberedNamesGraphRule
from apme_engine.graph.rules.L083_hardcoded_group_graph import HardcodedGroupGraphRule
from apme_engine.graph.rules.L085_role_path_include_graph import RolePathIncludeGraphRule
from apme_engine.graph.rules.L087_collection_license_graph import CollectionLicenseGraphRule
from apme_engine.graph.rules.L088_collection_readme_graph import CollectionReadmeGraphRule
from apme_engine.graph.rules.L095_schema_validation_graph import SchemaValidationGraphRule
from apme_engine.graph.rules.L096_meta_runtime_graph import MetaRuntimeGraphRule
from apme_engine.graph.rules.L103_galaxy_changelog_graph import GalaxyChangelogGraphRule
from apme_engine.graph.rules.L105_galaxy_repository_graph import GalaxyRepositoryGraphRule
from apme_engine.graph.scanner import scan

GOLDEN_COLLECTION = Path(__file__).parent / "fixtures" / "golden-collection"

_COLLECTION_RULES: list[GraphRule] = [
    CollectionLicenseGraphRule(),
    CollectionReadmeGraphRule(),
    SchemaValidationGraphRule(),
    MetaRuntimeGraphRule(),
    GalaxyChangelogGraphRule(),
    GalaxyRepositoryGraphRule(),
]


def _build_graph(col_root: Path) -> ContentGraph:
    """Load a collection from disk and build a ContentGraph with full children.

    Uses ``load_children=True`` so that ROLE and MODULE nodes are built
    (required for rules like L077 that inspect role metadata).  Roles are
    extracted from ``coll.roles`` and placed in the top-level ``roles``
    definition key so that ``GraphBuilder._build_from_loaded`` processes
    them (``_build_collection`` only wires MODULE children, not roles).

    Args:
        col_root: Path to the collection root directory.

    Returns:
        ContentGraph populated from the collection on disk.
    """
    from apme_engine.engine.model_loader import load_collection
    from apme_engine.engine.models import Role

    coll = load_collection(
        str(col_root), basedir=str(col_root.parent.parent), load_children=True, use_ansible_doc=False
    )
    roles = [r for r in getattr(coll, "roles", []) if isinstance(r, Role)]
    defs: dict[str, object] = {
        "root": {
            "definitions": {
                "collections": [coll],
                "roles": roles,
            },
        },
    }
    builder = GraphBuilder(defs, {})
    return builder.build()


def _violations_for(graph: ContentGraph, rules: list[GraphRule]) -> list[str]:
    """Run rules on a graph and return rule_ids of all violations.

    Args:
        graph: ContentGraph to scan.
        rules: List of GraphRule instances.

    Returns:
        List of rule_id strings for violations found.
    """
    report = scan(graph, rules, owned_only=False)
    return [rr.rule.rule_id for nr in report.node_results for rr in nr.rule_results if rr.verdict and rr.rule]


@pytest.fixture()  # type: ignore[untyped-decorator]
def collection_root(tmp_path: Path) -> Path:
    """Copy the golden collection into an isolated tmp_path.

    Args:
        tmp_path: Pytest temporary directory fixture.

    Returns:
        Path to the collection root inside ``tmp_path``.
    """
    dest = tmp_path / "testns" / "testcol"
    shutil.copytree(GOLDEN_COLLECTION, dest)
    return dest


# ===========================================================================
# Baseline
# ===========================================================================


class TestGoldenBaseline:
    """The unmodified golden collection should pass all collection-level rules."""

    def test_no_collection_rule_violations(self, collection_root: Path) -> None:
        """Scanning the unmodified golden collection produces zero violations.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        graph = _build_graph(collection_root)
        violations = _violations_for(graph, _COLLECTION_RULES)
        assert not violations, f"Golden collection should be clean, got: {violations}"


# ===========================================================================
# Per-rule violation tests
# ===========================================================================


class TestL087License:
    """L087: collection root should have a LICENSE or COPYING file."""

    def test_fires_without_license(self, collection_root: Path) -> None:
        """Removing LICENSE causes L087 to fire.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        (collection_root / "LICENSE").unlink()
        graph = _build_graph(collection_root)
        assert "L087" in _violations_for(graph, [CollectionLicenseGraphRule()])

    def test_passes_with_license(self, collection_root: Path) -> None:
        """Unmodified golden collection passes L087.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        graph = _build_graph(collection_root)
        assert "L087" not in _violations_for(graph, [CollectionLicenseGraphRule()])


class TestL088Readme:
    """L088: collection root should have a README file."""

    def test_fires_without_readme(self, collection_root: Path) -> None:
        """Removing README.md causes L088 to fire.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        (collection_root / "README.md").unlink()
        graph = _build_graph(collection_root)
        assert "L088" in _violations_for(graph, [CollectionReadmeGraphRule()])

    def test_passes_with_readme(self, collection_root: Path) -> None:
        """Unmodified golden collection passes L088.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        graph = _build_graph(collection_root)
        assert "L088" not in _violations_for(graph, [CollectionReadmeGraphRule()])


class TestL103Changelog:
    """L103: collection should have a CHANGELOG file."""

    def test_fires_without_changelog(self, collection_root: Path) -> None:
        """Removing CHANGELOG.rst causes L103 to fire.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        (collection_root / "CHANGELOG.rst").unlink()
        graph = _build_graph(collection_root)
        assert "L103" in _violations_for(graph, [GalaxyChangelogGraphRule()])

    def test_passes_with_changelog(self, collection_root: Path) -> None:
        """Unmodified golden collection passes L103.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        graph = _build_graph(collection_root)
        assert "L103" not in _violations_for(graph, [GalaxyChangelogGraphRule()])


class TestL105Repository:
    """L105: galaxy.yml should have a repository key."""

    def test_fires_without_repository(self, collection_root: Path) -> None:
        """Rewriting galaxy.yml without repository causes L105 to fire.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        galaxy = collection_root / "galaxy.yml"
        galaxy.write_text("namespace: testns\nname: testcol\nversion: 1.0.0\n")
        graph = _build_graph(collection_root)
        assert "L105" in _violations_for(graph, [GalaxyRepositoryGraphRule()])

    def test_passes_with_repository(self, collection_root: Path) -> None:
        """Unmodified golden collection passes L105.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        graph = _build_graph(collection_root)
        assert "L105" not in _violations_for(graph, [GalaxyRepositoryGraphRule()])


class TestL095Schema:
    """L095: galaxy.yml should have required keys (namespace, name, version)."""

    def test_fires_without_namespace(self, collection_root: Path) -> None:
        """Rewriting galaxy.yml without namespace causes L095 to fire.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        galaxy = collection_root / "galaxy.yml"
        galaxy.write_text("name: testcol\nversion: 1.0.0\nrepository: http://example.com\n")
        graph = _build_graph(collection_root)
        assert "L095" in _violations_for(graph, [SchemaValidationGraphRule()])

    def test_passes_with_all_required_keys(self, collection_root: Path) -> None:
        """Unmodified golden collection passes L095.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        graph = _build_graph(collection_root)
        assert "L095" not in _violations_for(graph, [SchemaValidationGraphRule()])


class TestL096MetaRuntime:
    """L096: meta/runtime.yml should have requires_ansible version specifier."""

    def test_fires_without_requires_ansible(self, collection_root: Path) -> None:
        """Removing requires_ansible from meta/runtime.yml causes L096 to fire.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        runtime = collection_root / "meta" / "runtime.yml"
        runtime.write_text("---\nplugin_routing: {}\n")
        graph = _build_graph(collection_root)
        assert "L096" in _violations_for(graph, [MetaRuntimeGraphRule()])

    def test_passes_with_requires_ansible(self, collection_root: Path) -> None:
        """Unmodified golden collection passes L096.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        graph = _build_graph(collection_root)
        assert "L096" not in _violations_for(graph, [MetaRuntimeGraphRule()])


class TestL077RoleArgSpecs:
    """L077: roles should have argument_specs in metadata.

    The golden collection embeds ``argument_specs`` inline in
    ``roles/run/meta/main.yml`` so the loader populates ``role_metadata``
    correctly (the separate ``meta/argument_specs.yml`` file is not read
    by the loader today).
    """

    def test_fires_without_argument_specs(self, collection_root: Path) -> None:
        """Rewriting meta/main.yml without argument_specs causes L077 to fire.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        meta_main = collection_root / "roles" / "run" / "meta" / "main.yml"
        meta_main.write_text("---\ngalaxy_info:\n  author: foo\ndependencies: []\n")
        graph = _build_graph(collection_root)
        assert "L077" in _violations_for(graph, [RoleArgSpecsGraphRule()])

    def test_passes_with_argument_specs(self, collection_root: Path) -> None:
        """Unmodified golden collection passes L077.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        graph = _build_graph(collection_root)
        assert "L077" not in _violations_for(graph, [RoleArgSpecsGraphRule()])


# ===========================================================================
# L074 — NoDashesInRoleName
# ===========================================================================


class TestL074NoDashesInRoleName:
    """L074: role names should not contain dashes."""

    def test_fires_with_dashed_role_name(self, collection_root: Path) -> None:
        """Renaming role directory to include dashes causes L074 to fire.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        src = collection_root / "roles" / "run"
        dst = collection_root / "roles" / "my-web-role"
        shutil.move(str(src), str(dst))
        graph = _build_graph(collection_root)
        assert "L074" in _violations_for(graph, [NoDashesInRoleNameGraphRule()])

    def test_passes_without_dashes(self, collection_root: Path) -> None:
        """Unmodified golden collection passes L074 (role named ``run``).

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        graph = _build_graph(collection_root)
        assert "L074" not in _violations_for(graph, [NoDashesInRoleNameGraphRule()])


# ===========================================================================
# L080 — InternalVarPrefix
# ===========================================================================


class TestL080InternalVarPrefix:
    """L080: internal role variables set via set_fact should use a leading underscore prefix."""

    def test_fires_with_unprefixed_set_fact(self, collection_root: Path) -> None:
        """Adding an unprefixed ``set_fact`` in a role task causes L080 to fire.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        tasks = collection_root / "roles" / "run" / "tasks" / "main.yml"
        tasks.write_text(
            "---\n- name: Set unprefixed variable\n  ansible.builtin.set_fact:\n    temp_value: something\n"
        )
        graph = _build_graph(collection_root)
        assert "L080" in _violations_for(graph, [InternalVarPrefixGraphRule()])

    def test_passes_with_prefixed_set_fact(self, collection_root: Path) -> None:
        """``set_fact`` with underscore-prefixed key in a role passes L080.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        tasks = collection_root / "roles" / "run" / "tasks" / "main.yml"
        tasks.write_text(
            "---\n- name: Set prefixed variable\n  ansible.builtin.set_fact:\n    __temp_value: something\n"
        )
        graph = _build_graph(collection_root)
        assert "L080" not in _violations_for(graph, [InternalVarPrefixGraphRule()])


# ===========================================================================
# L081 — NumberedNames
# ===========================================================================


class TestL081NumberedNames:
    """L081: do not number roles or playbooks."""

    def test_fires_with_numbered_role(self, collection_root: Path) -> None:
        """Renaming role directory to ``01_setup`` causes L081 to fire on the ROLE node.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        src = collection_root / "roles" / "run"
        dst = collection_root / "roles" / "01_setup"
        shutil.move(str(src), str(dst))
        graph = _build_graph(collection_root)
        assert "L081" in _violations_for(graph, [NumberedNamesGraphRule()])

    def test_passes_with_descriptive_name(self, collection_root: Path) -> None:
        """Unmodified golden collection passes L081 (role named ``run``).

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        graph = _build_graph(collection_root)
        assert "L081" not in _violations_for(graph, [NumberedNamesGraphRule()])


# ===========================================================================
# L083 — HardcodedGroup
# ===========================================================================


class TestL083HardcodedGroup:
    """L083: do not hardcode host group names in roles."""

    def test_fires_with_hardcoded_group(self, collection_root: Path) -> None:
        """Adding ``groups['db_servers']`` in a role task causes L083 to fire.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        tasks = collection_root / "roles" / "run" / "tasks" / "main.yml"
        tasks.write_text(
            "---\n"
            "- name: Check group membership\n"
            "  ansible.builtin.debug:\n"
            "    msg: host is a db server\n"
            "  when: inventory_hostname in groups['db_servers']\n"
        )
        graph = _build_graph(collection_root)
        assert "L083" in _violations_for(graph, [HardcodedGroupGraphRule()])

    def test_passes_without_hardcoded_group(self, collection_root: Path) -> None:
        """Unmodified golden collection passes L083 (no hardcoded groups).

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        graph = _build_graph(collection_root)
        assert "L083" not in _violations_for(graph, [HardcodedGroupGraphRule()])


# ===========================================================================
# L085 — RolePathInclude
# ===========================================================================


class TestL085RolePathInclude:
    """L085: use explicit ``role_path`` prefix in include paths within roles."""

    def test_fires_without_role_path(self, collection_root: Path) -> None:
        """Include path with Jinja but no ``role_path`` causes L085 to fire.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        tasks = collection_root / "roles" / "run" / "tasks" / "main.yml"
        tasks.write_text(
            '---\n- name: Include platform vars\n  ansible.builtin.include_vars:\n    file: "{{ platform }}/vars.yml"\n'
        )
        graph = _build_graph(collection_root)
        assert "L085" in _violations_for(graph, [RolePathIncludeGraphRule()])

    def test_passes_with_role_path(self, collection_root: Path) -> None:
        """Include path containing ``role_path`` passes L085.

        Args:
            collection_root: Isolated copy of the golden collection.
        """
        tasks = collection_root / "roles" / "run" / "tasks" / "main.yml"
        tasks.write_text(
            "---\n"
            "- name: Include platform vars\n"
            "  ansible.builtin.include_vars:\n"
            '    file: "{{ role_path }}/vars/{{ platform }}.yml"\n'
        )
        graph = _build_graph(collection_root)
        assert "L085" not in _violations_for(graph, [RolePathIncludeGraphRule()])
