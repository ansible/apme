"""Regression coverage for L057 discovery of Molecule scenario playbooks."""

from pathlib import Path
from unittest.mock import patch

import pytest

from apme_engine.validators.ansible.rules.L057_syntax import _find_playbooks, _looks_like_playbook, run


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "source",
    [
        "- name: Verify\n  hosts: all\n  tasks: []\n",
        "hosts: all\ntasks: []\n",
        "- hosts: all\n  vars:\n    secret: !vault |\n      encrypted-placeholder\n",
        "- hosts: all\n  tasks: [\n",
        "- hosts: all\n  vars:\n    nested: " + "[" * 1500 + "]" * 1500,
        "- import_playbook: ../../playbooks/site.yml\n",
        '- name: Converge\n  ansible.builtin.import_playbook: "{{ molecule_project_directory }}/site.yml"\n',
        "- import_playbook: [\n",
    ],
)
def test_L057_recognizes_scenario_playbooks(source: str) -> None:
    """Retain ordinary, tagged, and malformed runtime syntax candidates.

    Args:
        source: Candidate playbook source.
    """
    assert _looks_like_playbook(source)


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "source",
    [
        "provisioner:\n  name: ansible\n  inventory:\n    hosts:\n      all: {}\n",
        "all:\n  hosts:\n    testhost: {}\n",
        "settings:\n  tasks: []\n",
        "# hosts: all\nmessage: 'tasks: should stay data'\n",
        "- name: Include tasks\n  ansible.builtin.include_tasks: tasks.yml\n",
        "",
        "---\n",
    ],
)
def test_L057_excludes_configuration_and_task_data(source: str) -> None:
    """Avoid misclassifying Molecule configs, inventory, and task lists.

    Args:
        source: YAML configuration or task source.
    """
    assert not _looks_like_playbook(source)


def test_L057_discovers_imports_without_misclassifying_configuration(tmp_path: Path) -> None:
    """Discovery recognizes import candidates independently of dispatch policy.

    Args:
        tmp_path: Temporary project root.
    """
    scenario = tmp_path / "extensions" / "molecule" / "default"
    scenario.mkdir(parents=True)
    converge = scenario / "converge.yml"
    converge.write_text("- import_playbook: ../../../site.yml\n")
    (scenario / "molecule.yml").write_text("provisioner:\n  inventory:\n    hosts: {}\n")
    (scenario / "inventory.yml").write_text("all:\n  hosts: {}\n")
    assert _find_playbooks(tmp_path) == [converge]


@pytest.mark.parametrize("action", ["import_playbook", "ansible.builtin.import_playbook"])  # type: ignore[untyped-decorator]
def test_L057_ordinary_import_only_playbook_reaches_syntax_check(tmp_path: Path, action: str) -> None:
    """A missing ordinary import cannot disappear from runtime syntax coverage.

    Args:
        tmp_path: Temporary project and executable.
        action: Supported import-playbook spelling.
    """
    executable = tmp_path / "venv/bin/ansible-playbook"
    executable.parent.mkdir(parents=True)
    executable.touch()
    project = tmp_path / "project"
    project.mkdir()
    entrypoint = project / "site.yml"
    entrypoint.write_text(f"- name: Deploy\n  {action}: missing.yml\n")
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        invoke.return_value.returncode = 1
        invoke.return_value.stderr = "Unable to retrieve file missing.yml"
        findings = run(tmp_path / "venv", project)
    assert invoke.call_args.args[0] == [str(executable), "--syntax-check", str(entrypoint)]
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L057"
    assert findings[0]["file"] == "site.yml"


def test_L057_ordinary_playbook_reaches_syntax_check(tmp_path: Path) -> None:
    """Ordinary playbooks continue to receive runtime syntax diagnostics.

    Args:
        tmp_path: Temporary project and mock venv root.
    """
    venv = tmp_path / "venv"
    executable = venv / "bin" / "ansible-playbook"
    executable.parent.mkdir(parents=True)
    executable.touch()
    project = tmp_path / "project"
    project.mkdir()
    converge = project / "converge.yml"
    converge.write_text("- hosts: all\n  tasks: []\n")
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        invoke.return_value.returncode = 1
        invoke.return_value.stderr = "Unable to retrieve file missing.yml"
        findings = run(venv, project)

    assert invoke.call_args.args[0] == [str(executable), "--syntax-check", str(converge)]
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L057"
    assert findings[0]["file"] == "converge.yml"


def test_L057_never_executes_molecule_scenario_lookups(tmp_path: Path) -> None:
    """Molecule plays and imports receive Native checks without syntax execution.

    Args:
        tmp_path: Temporary uploaded project and mock venv.
    """
    executable = tmp_path / "venv" / "bin" / "ansible-playbook"
    executable.parent.mkdir(parents=True)
    executable.touch()
    scenario = tmp_path / "project" / "molecule" / "default"
    scenario.mkdir(parents=True)
    (scenario / "molecule.yml").write_text("")
    (scenario / "converge.yml").write_text(
        "- hosts: all\n  vars:\n    value: \"{{ lookup('pipe', 'unexpected-command') }}\"\n"
    )
    (scenario / "verify.yml").write_text('- import_playbook: "{{ molecule_project_directory }}/site.yml"\n')
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        snapshot = {str(path.relative_to(tmp_path / "project")): path.read_bytes() for path in scenario.iterdir()}
        assert run(tmp_path / "venv", tmp_path / "project", file_snapshot=snapshot) == []
    invoke.assert_not_called()


def test_L057_unknown_yaml_tag_is_never_executed() -> None:
    """Classifying YAML cannot invoke a Python constructor from a tag."""
    source = "- hosts: all\n  vars:\n    value: !!python/object/apply:os.system ['unexpected-command']\n"
    with patch("os.system") as execute:
        assert _looks_like_playbook(source)
    execute.assert_not_called()


def test_L057_shared_and_external_scenario_references_are_static(tmp_path: Path) -> None:
    """Resolved targets outside scenario directories cannot reach syntax execution.

    Args:
        tmp_path: Materialized project and mock venv.
    """
    executable = tmp_path / "venv/bin/ansible-playbook"
    executable.parent.mkdir(parents=True)
    executable.touch()
    root = tmp_path / "project"
    scenario = root / "molecule/default"
    scenario.mkdir(parents=True)
    (scenario / "molecule.yml").write_text("provisioner:\n  playbooks:\n    converge: ../shared/converge.yml\n")
    shared = root / "molecule/shared/converge.yml"
    shared.parent.mkdir()
    shared.write_text("- import_playbook: ../../playbooks/site.yml\n")
    external = root / "playbooks/site.yml"
    external.parent.mkdir()
    external.write_text("- hosts: all\n  tasks:\n    - import_tasks: \"{{ lookup('pipe', 'unexpected-command') }}\"\n")
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        snapshot = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        assert run(tmp_path / "venv", root, file_snapshot=snapshot) == []
    invoke.assert_not_called()


def test_L057_collection_project_imports_remain_static_with_manifest(tmp_path: Path) -> None:
    """Collection imports identified by JSON metadata cannot reach runtime checks.

    Args:
        tmp_path: Uploaded collection project and mock venv.
    """
    executable = tmp_path / "venv/bin/ansible-playbook"
    executable.parent.mkdir(parents=True)
    executable.touch()
    root = tmp_path / "project"
    scenario = root / "molecule/default"
    scenario.mkdir(parents=True)
    (scenario / "molecule.yml").write_text("")
    (scenario / "converge.yml").write_text("- import_playbook: example.testing.prepare\n")
    (root / "MANIFEST.json").write_text('{"collection_info": {"namespace": "example", "name": "testing"}}')
    target = root / "playbooks/prepare.yml"
    target.parent.mkdir()
    target.write_text("- hosts: all\n  tasks: []\n")
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        snapshot = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        assert run(tmp_path / "venv", root, file_snapshot=snapshot) == []
    invoke.assert_not_called()


@pytest.mark.parametrize("use_snapshot", [False, True])  # type: ignore[untyped-decorator]
def test_L057_extensionless_collection_imports_exclude_descendants(tmp_path: Path, use_snapshot: bool) -> None:
    """An extensionless intermediate import must not expose its YAML descendants.

    Args:
        tmp_path: Materialized project and mock venv.
        use_snapshot: Whether the caller supplies uploaded bytes directly.
    """
    executable = tmp_path / "venv/bin/ansible-playbook"
    executable.parent.mkdir(parents=True)
    executable.touch()
    root = tmp_path / "project"
    files = {
        "molecule/default/molecule.yml": b"",
        "molecule/default/converge.yml": b"- import_playbook: example.testing.prepare\n",
        "galaxy.yml": b"namespace: example\nname: testing\n",
        "playbooks/prepare": b"- import_playbook: final.yml\n",
        "playbooks/final.yml": b"- hosts: all\n  tasks: []\n",
    }
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        findings = run(tmp_path / "venv", root, file_snapshot=files if use_snapshot else None)
    if use_snapshot:
        assert findings == []
    else:
        assert len(findings) == 1
        assert findings[0]["rule_id"] == "L057"
        assert "complete uploaded snapshot" in str(findings[0]["message"])
    invoke.assert_not_called()


def test_ansible_service_forwards_complete_snapshot_to_static_exclusions(tmp_path: Path) -> None:
    """RPC adaptation passes non-YAML uploaded content into L057 exclusion lookup.

    Args:
        tmp_path: Mock session venv.
    """
    from apme.v1.common_pb2 import File
    from apme_engine.daemon.ansible_validator_server import _run_ansible_validate
    from apme_engine.validators.ansible import AnsibleValidator

    files = [
        File(path="./molecule/default/molecule.yml", content=b""),
        File(path="./playbooks/prepare", content=b"Earlier equivalent path"),
        File(path="playbooks//prepare", content=b"- import_playbook: final.yml\n"),
    ]
    with patch.object(AnsibleValidator, "run_with_timing") as run_checks:
        _run_ansible_validate(files, "2.19", {}, "snapshot-test", str(tmp_path))
    assert run_checks.call_args.kwargs["file_snapshot"] == {
        "molecule/default/molecule.yml": b"",
        "playbooks/prepare": b"- import_playbook: final.yml\n",
    }


@pytest.mark.parametrize("intermediate", ["venv/prepare.yml", "shared/prepare.ansible"])  # type: ignore[untyped-decorator]
def test_missing_snapshot_prevents_partial_import_exclusions(tmp_path: Path, intermediate: str) -> None:
    """Missing uploaded bytes are explicit; partial filesystem snapshots are unsafe.

    Args:
        tmp_path: Local project with references outside a typical YAML-only snapshot.
        intermediate: Import skipped by suffix or dependency-directory filtering.
    """
    executable = tmp_path / "venv/bin/ansible-playbook"
    executable.parent.mkdir(parents=True)
    executable.touch()
    root = tmp_path / "project"
    files = {
        "molecule/default/molecule.yml": b"",
        "molecule/default/converge.yml": f"- import_playbook: ../../{intermediate}\n".encode(),
        intermediate: b"- import_playbook: ../playbooks/final.yml\n",
        "playbooks/final.yml": b"- hosts: all\n  tasks: []\n",
        "large.bin": b"unrelated",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    with (
        patch.object(Path, "read_bytes", side_effect=AssertionError("Do not reconstruct a partial snapshot")),
        patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke,
    ):
        findings = run(tmp_path / "venv", root)
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L057"
    assert "complete uploaded snapshot" in str(findings[0]["message"])
    invoke.assert_not_called()
