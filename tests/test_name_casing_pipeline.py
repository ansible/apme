"""Exercise formatting, real OPA L025 findings, remediation and file splicing."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from apme.v1.engine_pb2 import RuleConfig
from apme_engine.daemon.engine_server import _apply_rule_configs
from apme_engine.engine.graph_builder import GraphBuilder
from apme_engine.engine.graph_opa_payload import build_hierarchy_from_graph
from apme_engine.engine.model_loader import load_repository
from apme_engine.engine.models import ViolationDict
from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.formatter import format_content
from apme_engine.graph.content_graph import ContentGraph
from apme_engine.opa_client import run_opa
from apme_engine.remediation.graph_engine import GraphRemediationEngine, splice_modifications
from apme_engine.remediation.transforms import build_default_registry

_SOURCES = {
    "galaxy.yml": "namespace: acme\nname: my_collection\nversion: 1.0.0\n",
    "group_vars/all.yml": "name: production\n",
    "roles/web/defaults/main.yml": "name: web_server\n",
    "roles/requirements.yml": "- name: web_role\n  src: acme.web\n  version: 1.0.0\n",
    "playbooks/requirements.yml": "- name: extra_role\n  src: acme.extra\n",
    "tasks/inline.yml": "- name: outer\n  block: [{name: inner, ansible.builtin.debug: {msg: ok}}]\n",
    "meta/requirements.yaml": "- src: acme.common\n  name: common_role\n",
    "site.yml": """\
- name: deploy app
  hosts: all
  vars:
    name: production
  tasks:
    - name: configure app
      block:
        - name: install nginx
          ansible.builtin.package:
            name: nginx
            state: present
      rescue:
        - name: recover app
          ansible.builtin.debug:
            msg: recovering
      always:
        - name: final cleanup
          ansible.builtin.debug:
            msg: done
  handlers:
    - name: reload nginx
      ansible.builtin.debug:
        msg: reloading
""",
    "tasks/standalone.yml": "- name: standalone task\n  ansible.builtin.debug:\n    msg: hello\n",
}
_TASK_SOURCES = {"site.yml", "tasks/standalone.yml", "tasks/inline.yml"}
_LABELS = {
    "outer",
    "inner",
    "deploy app",
    "configure app",
    "install nginx",
    "recover app",
    "final cleanup",
    "reload nginx",
    "standalone task",
}


def _format_and_build(root: Path) -> tuple[ContentGraph, dict[str, str]]:
    """Format mixed project sources and load only discovered Ansible content.

    Args:
        root: Temporary project root.

    Returns:
        Parsed graph and formatted originals indexed by project-relative filename.
    """
    originals = {}
    yaml = FormattedYAML(typ="rt", pure=True, version=(1, 1))
    for relative, source in _SOURCES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        formatted = format_content(source, filename=str(path))
        assert formatted.error is None
        assert yaml.load(formatted.formatted) == yaml.load(source)
        path.write_text(formatted.formatted)
        originals[relative] = formatted.formatted
    return _build_from_files(root), originals


def _build_from_files(root: Path) -> ContentGraph:
    """Use repository discovery, including directory-based task loading.

    Args:
        root: Project directory holding formatted or remediated source files.

    Returns:
        Graph reconstructed from actual files on disk.
    """
    repository = load_repository(str(root), use_ansible_doc=False, load_children=True)
    definitions = {"playbooks": repository.playbooks, "taskfiles": repository.taskfiles, "roles": repository.roles}
    return GraphBuilder({"root": {"definitions": definitions}}, {}).build()


def _scan_names(graph: ContentGraph, bundle: Path, enabled: bool) -> list[ViolationDict]:
    """Evaluate real L025 policy and apply the engine's rule configuration.

    Args:
        graph: Parsed project graph.
        bundle: Built-in OPA policy directory.
        enabled: Whether L025 is enabled in the request.

    Returns:
        L025 findings eligible for deterministic remediation.
    """
    findings = run_opa(build_hierarchy_from_graph(graph, "project", "test"), str(bundle))
    names = [finding for finding in findings if finding["rule_id"] == "L025"]
    return _apply_rule_configs(names, [RuleConfig(rule_id="L025", enabled=enabled)])


@pytest.mark.skipif(  # type: ignore[untyped-decorator]
    shutil.which("opa") is None, reason="requires the OPA binary installed in CI"
)
@pytest.mark.parametrize("enabled", [True, False])  # type: ignore[untyped-decorator]
async def test_name_casing_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opa_bundle_path: Path, enabled: bool
) -> None:
    """Only enabled L025 findings change actual play/task/handler/block labels.

    Args:
        tmp_path: Temporary project root.
        monkeypatch: Fixture selecting the local OPA transport.
        opa_bundle_path: Built-in policy directory.
        enabled: Whether L025 should produce and remediate findings.
    """
    monkeypatch.setenv("OPA_USE_PODMAN", "0")
    monkeypatch.chdir(tmp_path)
    graph, originals = _format_and_build(tmp_path)
    findings = await asyncio.to_thread(_scan_names, graph, opa_bundle_path, enabled)
    assert len(findings) == (len(_LABELS) if enabled else 0)

    async def rescan(current: ContentGraph, dirty: frozenset[str]) -> list[ViolationDict]:
        return await asyncio.to_thread(_scan_names, current, opa_bundle_path, enabled)

    engine = GraphRemediationEngine(build_default_registry(), graph, [], rescan_fn=rescan)
    report = await engine.remediate(initial_violations=findings)
    assert not report.remaining_violations
    assert report.fixed == len(findings)
    patches = splice_modifications(graph, originals)
    assert {Path(patch.path).as_posix() for patch in patches} == (_TASK_SOURCES if enabled else set())
    final = dict(originals)
    for patch in patches:
        formatted = format_content(patch.patched, filename=patch.path)
        assert formatted.error is None
        final[patch.path] = formatted.formatted
    for relative in _SOURCES:
        if relative not in _TASK_SOURCES:
            assert final[relative] == originals[relative]
    combined = "".join(final[relative] for relative in _TASK_SOURCES)
    for label in _LABELS:
        assert f"name: {label[0].upper() + label[1:] if enabled else label}" in combined
    assert "name: nginx" in combined
    assert "name: production" in combined
    for filename, content in final.items():
        (tmp_path / filename).write_text(content)
    reloaded = _build_from_files(tmp_path)
    assert not await asyncio.to_thread(_scan_names, reloaded, opa_bundle_path, enabled)
