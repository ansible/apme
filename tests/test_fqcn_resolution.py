"""Resolve and apply FQCNs using real ansible-core plugin metadata."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from ruamel.yaml import YAML

from apme.v1.common_pb2 import File
from apme_engine.daemon.ansible_validator_server import _run_ansible_validate
from apme_engine.daemon.violation_convert import violation_dict_to_proto, violation_proto_to_dict
from apme_engine.engine.models import ViolationDict, YAMLDict
from apme_engine.graph.content_graph import ContentGraph, ContentNode, NodeIdentity, NodeType
from apme_engine.graph.rule_base import GraphRule
from apme_engine.graph.rules.L026_non_fqcn_use_graph import NonFQCNUseGraphRule
from apme_engine.graph.scanner import graph_report_to_violations, scan
from apme_engine.remediation.graph_engine import GraphRemediationEngine
from apme_engine.remediation.transforms import build_default_registry
from apme_engine.remediation.transforms.M001_fqcn import enrich_fqcn_targets, fix_fqcn
from apme_engine.validators.ansible.cache import PluginCache
from apme_engine.validators.ansible.rules import L057_syntax, L058_argspec_doc, L059_argspec_mock, M001_M004_introspect


@pytest.fixture()  # type: ignore[untyped-decorator]
def runtime_project(tmp_path: Path) -> Path:
    """Create collections with duplicate short names, redirects and an action.

    Args:
        tmp_path: Temporary project directory.

    Returns:
        Project root containing discoverable Ansible plugins.
    """
    for collection in ("alpha", "beta"):
        root = tmp_path / "collections" / "ansible_collections" / "demo" / collection
        modules = root / "plugins" / "modules"
        modules.mkdir(parents=True)
        (modules / "probe.py").write_text("raise RuntimeError('modules must not execute during resolution')\n")
        actions = root / "plugins" / "action"
        actions.mkdir()
        (actions / "only_action.py").write_text("raise RuntimeError('actions must not execute during resolution')\n")
        meta = root / "meta"
        meta.mkdir()
        (meta / "runtime.yml").write_text(
            "plugin_routing:\n  modules:\n    old_probe:\n      redirect: demo." + collection + ".probe\n"
        )
    (tmp_path / "ansible.cfg").write_text("[defaults]\n")
    return tmp_path


def _resolve(project: Path, nodes: list[dict[str, object]]) -> list[ViolationDict]:
    """Resolve task nodes with the project's installed test collections.

    Args:
        project: Test project root.
        nodes: Task payloads with original names and collection context.

    Returns:
        Runtime findings without executing plugin code.
    """
    return cast(
        list[ViolationDict],
        M001_M004_introspect.run(
            nodes,
            Path(sys.prefix),
            env_extra={
                "APME_ANSIBLE_TRUST_PROJECT_CODE": "true",
                "ANSIBLE_COLLECTIONS_PATH": str(project / "collections"),
                "ANSIBLE_CONFIG": str(project / "ansible.cfg"),
            },
            project_root=project,
        ),
    )


def _task(module: str, collections: list[str] | None = None, key: str = "task") -> dict[str, object]:
    """Build a task payload for runtime resolution.

    Args:
        module: Original module or action name.
        collections: Collection search order in scope.
        key: Stable task ID.

    Returns:
        Task payload with source location and search context.
    """
    return {
        "type": "taskcall",
        "module": module,
        "collections_in_play": collections or [],
        "file": "site.yml",
        "line": [1, 3],
        "key": key,
    }


def test_collection_search_order_is_per_task(runtime_project: Path) -> None:
    """The same short name resolves differently in different collection scopes.

    Args:
        runtime_project: Project with two collections defining probe.
    """
    findings = _resolve(
        runtime_project,
        [
            _task("probe", ["demo.alpha", "demo.beta"], "alpha-task"),
            _task("probe", ["demo.beta", "demo.alpha"], "beta-task"),
        ],
    )
    targets = {v["path"]: v["resolved_fqcn"] for v in findings if v["rule_id"] == "M001"}
    assert targets == {"alpha-task": "demo.alpha.probe", "beta-task": "demo.beta.probe"}


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("module", "collections", "target"),
    [
        ("copy", [], "ansible.builtin.copy"),
        ("only_action", ["demo.alpha"], "demo.alpha.only_action"),
        ("old_probe", ["demo.alpha"], "demo.alpha.probe"),
        ("demo.alpha.old_probe", [], "demo.alpha.probe"),
    ],
)
def test_canonical_targets_from_runtime(
    runtime_project: Path, module: str, collections: list[str], target: str
) -> None:
    """Builtins, action-only plugins and runtime redirects provide fix targets.

    Args:
        runtime_project: Project containing test collections.
        module: Original plugin spelling.
        collections: Applicable search order.
        target: Expected canonical FQCN.
    """
    findings = _resolve(runtime_project, [_task(module, collections)])
    fixes = [v for v in findings if v["rule_id"] in {"M001", "M003"}]
    assert fixes
    assert {v["resolved_fqcn"] for v in fixes} == {target}


def test_module_redirect_target_is_not_replaced_by_action_fqcn(runtime_project: Path) -> None:
    """M003 follows the module redirect while M001 keeps action qualification.

    Args:
        runtime_project: Temporary project containing redirect metadata and plugins.
    """
    action_dir = runtime_project / "collections" / "ansible_collections" / "demo" / "alpha" / "plugins" / "action"
    (action_dir / "old_probe.py").write_text("raise RuntimeError('action must not execute during resolution')\n")

    findings = _resolve(
        runtime_project,
        [_task("old_probe", ["demo.alpha"]), _task("demo.alpha.old_probe", ["demo.alpha"])],
    )
    m001 = next(v for v in findings if v["rule_id"] == "M001")
    m003 = next((v for v in findings if v["rule_id"] == "M003"), None)
    assert m003 is not None, findings

    assert m001["resolved_fqcn"] == "demo.alpha.old_probe"
    redirect_chain = cast(list[object], m003["redirect_chain"])
    assert redirect_chain[-1] == "demo.alpha.probe"
    assert "resolved_fqcn" not in m003


def test_local_module_override_is_preserved(runtime_project: Path) -> None:
    """A local copy module must not become ansible.builtin.copy.

    Args:
        runtime_project: Test project root.
    """
    library = runtime_project / "library"
    library.mkdir()
    (library / "copy.py").write_text("raise RuntimeError('module must not execute')\n")
    (runtime_project / "ansible.cfg").write_text(f"[defaults]\nlibrary = {library}\n")
    findings = _resolve(runtime_project, [_task("copy")])
    qualification = [v for v in findings if v["rule_id"] == "M001"]
    assert len(qualification) == 1
    assert qualification[0]["resolved_fqcn"] == "ansible.legacy.copy"


def test_local_action_override_is_preserved(runtime_project: Path) -> None:
    """Qualifying a local action must preserve the action and module pairing.

    Args:
        runtime_project: Test project root.
    """
    actions = runtime_project / "actions"
    actions.mkdir()
    (actions / "copy.py").write_text("raise RuntimeError('action must not execute')\n")
    (runtime_project / "ansible.cfg").write_text(f"[defaults]\naction_plugins = {actions}\n")
    findings = _resolve(runtime_project, [_task("copy")])
    qualification = [v for v in findings if v["rule_id"] == "M001"]
    assert len(qualification) == 1
    assert qualification[0]["resolved_fqcn"] == "ansible.legacy.copy"


def test_unknown_plugin_and_dynamic_name_are_not_guessed(runtime_project: Path) -> None:
    """Missing plugins produce diagnostics and templates never become targets.

    Args:
        runtime_project: Test project root.
    """
    findings = _resolve(runtime_project, [_task("not_installed"), _task("{{ chosen_module }}")])
    assert [v["rule_id"] for v in findings] == ["L037"]
    assert all("resolved_fqcn" not in v for v in findings)


def test_rescan_reconstructs_project_context_without_syntax_checks(
    runtime_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rescan uses uploaded configuration and plugins in its temporary root.

    Args:
        runtime_project: Source project whose files are uploaded.
        monkeypatch: Environment patcher enabling project plugin context.
    """
    monkeypatch.setenv("APME_ANSIBLE_TRUST_PROJECT_CODE", "true")
    monkeypatch.delenv("ANSIBLE_COLLECTIONS_PATH", raising=False)
    monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
    (runtime_project / "ansible.cfg").write_text("[defaults]\ncollections_path = ./collections\n")
    files = [
        File(path=path.relative_to(runtime_project).as_posix(), content=path.read_bytes())
        for path in runtime_project.rglob("*")
        if path.is_file()
    ]
    payload = cast(YAMLDict, {"hierarchy": [{"nodes": [_task("only_action", ["demo.alpha"])]}]})
    with (
        patch.object(L057_syntax, "run") as syntax_check,
        patch.object(L058_argspec_doc, "run", return_value=[]),
        patch.object(L059_argspec_mock, "run", return_value=[]),
    ):
        result = _run_ansible_validate(files, "2.19", payload, "rescan", sys.prefix, check_syntax=False)
    syntax_check.assert_not_called()
    qualification = next(v for v in result.run_result.violations if v["rule_id"] == "M001")
    assert qualification["resolved_fqcn"] == "demo.alpha.only_action"


def test_incompatible_module_and_action_pair_stays_unresolved(runtime_project: Path) -> None:
    """Do not qualify a name when every FQCN changes one selected plugin.

    Args:
        runtime_project: Test project root.
    """
    module = (
        runtime_project / "collections" / "ansible_collections" / "demo" / "alpha" / "plugins" / "modules" / "copy.py"
    )
    module.write_text("raise RuntimeError('module must not execute')\n")
    findings = _resolve(runtime_project, [_task("copy", ["demo.alpha", "ansible.legacy"])])
    qualification = [v for v in findings if v["rule_id"] == "M001"]
    assert len(qualification) == 1
    assert "resolved_fqcn" not in qualification[0]
    assert "preserves the selected module and action" in str(qualification[0]["message"])


async def test_runtime_target_reaches_l026_and_remediation(runtime_project: Path) -> None:
    """The real resolver target survives transport and fixes a native finding.

    Args:
        runtime_project: Project containing the test collection.
    """
    graph = ContentGraph()
    node = ContentNode(
        identity=NodeIdentity(path="task", node_type=NodeType.TASK),
        file_path="site.yml",
        module="probe",
        yaml_lines="- name: Probe\n  probe: # keep comment\n    value: unchanged\n",
        line_start=1,
        line_end=3,
    )
    graph.add_node(node)
    rules: list[GraphRule] = [NonFQCNUseGraphRule()]
    violations = graph_report_to_violations(scan(graph, rules))
    violations.extend(
        violation_proto_to_dict(violation_dict_to_proto(v))
        for v in _resolve(runtime_project, [_task("probe", ["demo.alpha"])])
    )
    engine = GraphRemediationEngine(build_default_registry(), graph, rules)
    report = await engine.remediate(initial_violations=violations)
    assert not report.remaining_violations
    fixed_native = next(v for v in report.fixed_violations if v["rule_id"] == "L026")
    assert fixed_native["resolved_fqcn"] == "demo.alpha.probe"
    assert "demo.alpha.probe: # keep comment" in node.yaml_lines
    assert "value: unchanged" in node.yaml_lines


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("field", "different"),
    [("file", "other.yml"), ("path", "another-task"), ("original_module", "other"), ("rule_id", "M048")],
)
def test_targets_cannot_leak_between_findings(field: str, different: str) -> None:
    """Another task's resolution or a builtin twin must not authorize a rewrite.

    Args:
        field: Matching field or provider rule to vary.
        different: Value that must prevent target propagation.
    """
    finding: ViolationDict = {"rule_id": "L026", "file": "site.yml", "path": "task", "original_module": "probe"}
    provider: ViolationDict = {
        **finding,
        "rule_id": "M001",
        "resolved_fqcn": "demo.alpha.probe",
        field: different,
    }
    enrich_fqcn_targets([finding, provider])
    assert "resolved_fqcn" not in finding


def test_conflicting_resolutions_do_not_authorize_a_rewrite() -> None:
    """Conflicting runtime evidence keeps the native finding unresolved."""
    finding: ViolationDict = {"rule_id": "L026", "file": "site.yml", "path": "task", "original_module": "probe"}
    providers: list[ViolationDict] = [
        {**finding, "rule_id": "M001", "resolved_fqcn": f"demo.{name}.probe"} for name in ("alpha", "beta")
    ]
    enrich_fqcn_targets([finding, *providers])
    assert "resolved_fqcn" not in finding


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "declaration",
    [
        "  copy: # keep comment\n    src: a\n    dest: b\n",
        "  action: copy src=a dest=b # keep comment\n",
        "  action:\n    module: copy\n    src: a\n    dest: b # keep comment\n",
        "  local_action: copy src=a dest=b # keep comment\n",
    ],
)
def test_fqcn_fixes_preserve_arguments_comments_and_action_syntax(declaration: str) -> None:
    """Both module keys and action forms qualify without changing arguments.

    Args:
        declaration: YAML task action syntax to preserve.
    """
    yaml = YAML()
    task = yaml.load("- name: Copy\n" + declaration)[0]
    finding: ViolationDict = {"rule_id": "M001", "original_module": "copy", "resolved_fqcn": "ansible.builtin.copy"}
    assert fix_fqcn(task, finding)
    expected = yaml.load("- name: Copy\n" + declaration.replace("copy", "ansible.builtin.copy", 1))[0]
    assert task == expected
    output = io.StringIO()
    yaml.dump([task], output)
    text = output.getvalue()
    assert "ansible.builtin.copy" in text
    assert "# keep comment" in text
    assert yaml.load(text)[0] == task
    assert not fix_fqcn(task, finding)


def test_stale_target_does_not_undo_another_transform() -> None:
    """A shell finding must not rename a command produced by an earlier fix."""
    task = YAML().load("ansible.builtin.command: whoami\n")
    finding: ViolationDict = {"rule_id": "L026", "original_module": "shell", "resolved_fqcn": "ansible.builtin.shell"}
    assert not fix_fqcn(task, finding)
    assert "ansible.builtin.command" in task


def test_dependency_inventory_invalidates_resolution_cache(tmp_path: Path) -> None:
    """Updating a redirect's destination collection invalidates cached targets.

    Args:
        tmp_path: Temporary fake venv root.
    """
    packages = tmp_path / "lib" / "python3.12" / "site-packages"
    core = packages / "ansible"
    core.mkdir(parents=True)
    (core / "release.py").write_text("__version__ = '2.19.0'\n")
    for name in ("alpha", "beta"):
        collection = packages / "ansible_collections" / "demo" / name
        collection.mkdir(parents=True)
        (collection / "MANIFEST.json").write_text(json.dumps({"collection_info": {"version": "1.0.0"}}))
    cache = PluginCache()
    before = cache.introspect_context_suffix(str(tmp_path))
    cache.put("introspect", str(tmp_path), "demo.alpha.probe", {"fqcn": "demo.beta.probe"}, introspect_context=before)
    destination = packages / "ansible_collections" / "demo" / "beta" / "MANIFEST.json"
    destination.write_text(json.dumps({"collection_info": {"version": "2.0.0"}}))
    after = cache.introspect_context_suffix(str(tmp_path))
    assert before != after
    assert cache.get("introspect", str(tmp_path), "demo.alpha.probe", introspect_context=after) is None
