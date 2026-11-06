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
        "- import_playbook: ../../playbooks/site.yml\n",
        '- name: Converge\n  ansible.builtin.import_playbook: "{{ molecule_project_directory }}/site.yml"\n',
    ],
)
def test_L057_excludes_configuration_and_task_data(source: str) -> None:
    """Avoid misclassifying Molecule configs, inventory, and task lists.

    Args:
        source: YAML configuration or task source.
    """
    assert not _looks_like_playbook(source)


def test_L057_leaves_imports_and_configuration_to_static_checks(tmp_path: Path) -> None:
    """Runtime discovery excludes imports and nested configuration hosts.

    Args:
        tmp_path: Temporary project root.
    """
    scenario = tmp_path / "extensions" / "molecule" / "default"
    scenario.mkdir(parents=True)
    converge = scenario / "converge.yml"
    converge.write_text("- import_playbook: ../../../site.yml\n")
    (scenario / "molecule.yml").write_text("provisioner:\n  inventory:\n    hosts: {}\n")
    (scenario / "inventory.yml").write_text("all:\n  hosts: {}\n")
    assert _find_playbooks(tmp_path) == []


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
        assert run(tmp_path / "venv", tmp_path / "project") == []
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
        assert run(tmp_path / "venv", root) == []
    invoke.assert_not_called()
