"""Behavioral coverage for REQ-022 lint rules and runtime risk findings."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from apme_engine.engine.graph_opa_payload import content_node_to_opa_dict
from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeScope, NodeType
from apme_engine.graph.rules.L112_latest_vcs_version_graph import LatestVCSVersionGraphRule
from apme_engine.graph.rules.L113_role_name_graph import RoleNameGraphRule
from apme_engine.graph.rules.L114_collection_version_graph import CollectionVersionGraphRule
from apme_engine.graph.sensitivity import redact_urls_in_text
from apme_engine.graph.types import YAMLDict
from apme_engine.validators.ansible.rules import L058_argspec_doc, L059_argspec_mock, M001_M004_introspect


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("source", "remote"),
    [
        ("github.com:org/repo", True),
        ("host:repo", True),
        ("git@host:org/repo", True),
        ("https://host/repo", True),
        ("ssh://git@host/repo", True),
        ("{{ repository }}", True),
        ("file:///tmp/repo", False),
        ("file:relative/repo", False),
        ("/tmp/repo", False),
        ("./host:repo", False),
        ("C:/repo", False),
        ("host:", False),
        ("unsupported://host/repo", False),
    ],
)
def test_r118_git_source_classification(source: str, remote: bool) -> None:
    """Preserve Git's remote syntax while excluding explicit local sources.

    Args:
        source: Git repository source.
        remote: Whether an inbound annotation is expected.
    """
    node = ContentNode(
        identity=NodeIdentity(path="task", node_type=NodeType.TASK),
        module="ansible.builtin.git",
        module_options={"repo": source, "dest": "/tmp/checkout"},
        line_start=3,
    )
    annotations = content_node_to_opa_dict(node)["annotations"]
    assert isinstance(annotations, list)
    assert bool(annotations) is remote
    if remote:
        annotation = annotations[0]
        assert isinstance(annotation, dict)
        assert annotation["risk_type"] == "inbound_transfer"
        location = annotation["src"]
        assert isinstance(location, dict)
        assert location["type"] == "url"


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("source", "expected_source"),
    [
        ("https://u:p@host:8080/x", "https://[REDACTED]@host:8080/x"),
        ("https://user@host:8080/x", "https://[REDACTED]@host:8080/x"),
    ],
)
def test_r118_url_userinfo_preserves_scheme_and_type(source: str, expected_source: str) -> None:
    """Redacted URL userinfo must not turn an HTTP URL into SCP syntax.

    Args:
        source: URL with userinfo, with or without a password.
        expected_source: URL after userinfo redaction.
    """
    node = ContentNode(
        identity=NodeIdentity(path="task", node_type=NodeType.TASK),
        module="ansible.builtin.git",
        module_options={"repo": source, "dest": "/tmp/checkout"},
    )

    annotations = content_node_to_opa_dict(node)["annotations"]

    assert isinstance(annotations, list) and len(annotations) == 1
    annotation = annotations[0]
    assert isinstance(annotation, dict)
    location = annotation["src"]
    assert isinstance(location, dict)
    assert location["type"] == "url"
    assert location["value"] == expected_source


def test_r118_and_argspec_payload_include_task_level_args() -> None:
    """Task-level args feed both inbound risk and runtime argspec checks."""
    node = ContentNode(
        identity=NodeIdentity(path="task", node_type=NodeType.TASK),
        module="ansible.builtin.get_url",
        options={"args": {"url": "https://host/file?token=secret", "dest": "/tmp/file"}},
    )

    payload = content_node_to_opa_dict(node)

    assert payload["module_options"] == {"url": "https://host/file?token=secret", "dest": "/tmp/file"}
    annotations = payload["annotations"]
    assert isinstance(annotations, list) and len(annotations) == 1
    annotation = annotations[0]
    assert isinstance(annotation, dict)
    src = annotation["src"]
    assert isinstance(src, dict)
    assert src["value"] == "https://host/file?[REDACTED]"


def test_task_level_args_have_lower_precedence_than_module_options() -> None:
    """Module-mapping values override colliding task-level args as in Ansible."""
    node = ContentNode(
        identity=NodeIdentity(path="task", node_type=NodeType.TASK),
        module="ansible.builtin.get_url",
        module_options={"url": "file:///tmp/local"},
        options={"args": {"url": "https://host/file", "dest": "/tmp/file"}},
    )

    payload = content_node_to_opa_dict(node)

    assert payload["module_options"] == {"url": "file:///tmp/local", "dest": "/tmp/file"}
    assert payload["annotations"] == []


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "remote_src", [True, "yes", "on", 1, False, "false", None]
)
def test_r118_unarchive_requires_remote_src(remote_src: object) -> None:
    """Only annotate an archive fetched by the managed node.

    Args:
        remote_src: Ansible boolean option value.
    """
    options: YAMLDict = {"src": "https://host/archive.tgz", "dest": "/tmp"}
    if isinstance(remote_src, str | bool | int):
        options["remote_src"] = remote_src
    node = ContentNode(
        identity=NodeIdentity(path="task", node_type=NodeType.TASK),
        module="ansible.builtin.unarchive",
        module_options=options,
    )
    assert bool(content_node_to_opa_dict(node)["annotations"]) is (remote_src in (True, "yes", "on", 1))


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("source", "expected"),
    [
        ("https://user:secret@host/path?token=hidden#private", "https://[REDACTED]@host/path?[REDACTED]#[REDACTED]"),
        ("git@host:repo?token=hidden", "git@host:repo?[REDACTED]"),
        ("user:secret@host:repo", "[REDACTED]@host:repo"),
        ("https://{{ user }}:{{ password }}@host/path", "https://[REDACTED]@host/path"),
    ],
)
def test_risk_url_redaction(source: str, expected: str) -> None:
    """Strip credentials and tokens without losing the source location.

    Args:
        source: Sensitive source string.
        expected: Sanitized source string.
    """
    assert redact_urls_in_text(source) == expected


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("module", "options", "collections", "violation"),
    [
        ("git", {}, [], True),
        ("ansible.builtin.git", {"version": "HEAD"}, [], True),
        ("git", {"version": "v1.0.0"}, [], False),
        ("git", {"version": "{{ revision }}"}, [], False),
        ("community.general.hg", {"revision": "default"}, [], True),
        ("community.general.hg", {"revision": "v1"}, [], False),
        ("git", {}, ["acme.custom"], False),
        ("git", {}, ["ansible.builtin", "acme.custom"], True),
        ("hg", {}, ["community.general"], True),
        ("hg", {}, ["acme.custom", "community.general"], False),
    ],
)
def test_l112_vcs_defaults(module: str, options: YAMLDict, collections: list[str], violation: bool) -> None:
    """Flag moving defaults only for supported, unambiguous VCS plugins.

    Args:
        module: Authored module name.
        options: Module arguments.
        collections: Collection search order.
        violation: Whether the revision is an unpinned default.
    """
    graph = ContentGraph()
    node = ContentNode(
        identity=NodeIdentity(path="task", node_type=NodeType.TASK),
        module=module,
        module_options=options,
        collections_in_play=collections,
    )
    graph.add_node(node)
    result = LatestVCSVersionGraphRule().process(graph, node.node_id)
    assert bool(result and result.verdict) is violation


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("module_options", "task_args", "violation"),
    [
        ({}, {"version": "v1.0.0"}, False),
        ({}, {"version": "HEAD"}, True),
        ({"version": "HEAD"}, {"version": "v1.0.0"}, True),
    ],
)
def test_l112_uses_task_args_with_module_option_precedence(
    module_options: YAMLDict,
    task_args: YAMLDict,
    violation: bool,
) -> None:
    """Read task-level args while preserving Ansible's merge precedence.

    Args:
        module_options: Arguments specified directly on the module.
        task_args: Arguments from the task-level ``args`` mapping.
        violation: Whether the moving revision should be reported.
    """
    graph = ContentGraph()
    node = ContentNode(
        identity=NodeIdentity(path="task", node_type=NodeType.TASK),
        module="ansible.builtin.git",
        module_options=module_options,
        options={"args": task_args},
    )
    graph.add_node(node)

    result = LatestVCSVersionGraphRule().process(graph, node.node_id)

    assert bool(result and result.verdict) is violation


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("path", "metadata", "violation"),
    [
        ("roles/good_role", {}, False),
        ("roles/Bad-Role", {}, True),
        ("roles/ansible-role-good_role", {}, False),
        ("roles/Bad-Role/meta/main.yml", {"galaxy_info": {"role_name": "good_role"}}, False),
        ("roles/good_role", {"galaxy_info": {"role_name": "Bad-Role"}}, True),
    ],
)
def test_l113_role_name(path: str, metadata: YAMLDict, violation: bool) -> None:
    """Check metadata names before falling back to role directory names.

    Args:
        path: Role directory or metadata path.
        metadata: Role metadata.
        violation: Whether the effective name is invalid.
    """
    graph = ContentGraph()
    node = ContentNode(
        identity=NodeIdentity(path=path, node_type=NodeType.ROLE),
        file_path=path,
        role_metadata=metadata,
        scope=NodeScope.OWNED,
    )
    graph.add_node(node)
    result = RoleNameGraphRule().process(graph, node.node_id)
    assert result is not None
    assert result.verdict is violation


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("version", "violation"),
    [
        ("1.0.0", False),
        ("2.0.0-rc.1+build.01", False),
        ("0.9.9", True),
        ("1.0.0-rc.1", True),
        ("1.0", True),
        ("01.0.0", True),
        ("1.0.0-01", True),
        ("1.0.0-a..b", True),
        ("1.0.0+a..b", True),
    ],
)
def test_l114_semver(version: str, violation: bool) -> None:
    """Apply SemVer identifier rules and prerelease precedence.

    Args:
        version: Collection version.
        violation: Whether the version violates collection policy.
    """
    graph = ContentGraph()
    node = ContentNode(
        identity=NodeIdentity(path="collection", node_type=NodeType.COLLECTION),
        collection_metadata={"collection_info": {"version": version}},
        scope=NodeScope.OWNED,
    )
    graph.add_node(node)
    result = CollectionVersionGraphRule().process(graph, node.node_id)
    assert result is not None
    assert result.verdict is violation


def test_l037_runtime_resolution(tmp_path: Path) -> None:
    """Use real module/action loaders and a project-configured local action.

    Args:
        tmp_path: Temporary project root.
    """
    actions = tmp_path / "actions"
    actions.mkdir()
    (actions / "custom_action.py").write_text(
        "# Resolution must not execute actions.\nraise RuntimeError('executed')\n"
    )
    (tmp_path / "ansible.cfg").write_text("[defaults]\naction_plugins = ./actions\n")
    nodes: list[dict[str, object]] = [
        {"module": name, "file": "site.yml", "line": [3, 4], "key": name}
        for name in ("debug", "ansible.legacy.custom_action", "nonexistent_collection.missing.plugin")
    ]
    violations = M001_M004_introspect.run(nodes, Path(sys.prefix), project_root=tmp_path)
    unresolved = [v for v in violations if v["rule_id"] == "L037"]
    assert len(unresolved) == 1
    assert unresolved[0]["original_module"] == "nonexistent_collection.missing.plugin"
    assert unresolved[0]["file"] == "site.yml"
    assert unresolved[0]["line"] == 3


def test_argspec_omitted_arguments() -> None:
    """Both runtime argspec validators report required args on empty tasks."""
    nodes: list[dict[str, object]] = [
        {"module": "ansible.builtin.get_url", "module_options": {}, "key": "task", "line": [2, 3]},
    ]
    for validator in (L058_argspec_doc, L059_argspec_mock):
        violations = validator.run(nodes, Path(sys.prefix))
        assert any("url" in str(v["message"]) and "required" in str(v["message"]).lower() for v in violations)
