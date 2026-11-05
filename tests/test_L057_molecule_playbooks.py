"""Regression coverage for L057 discovery of Molecule scenario playbooks."""

from pathlib import Path
from unittest.mock import patch

import pytest

from apme_engine.validators.ansible.rules.L057_syntax import _find_playbooks, _looks_like_playbook, run


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "source",
    [
        "- import_playbook: ../../playbooks/site.yml\n",
        '- name: Converge\n  ansible.builtin.import_playbook: "{{ molecule_project_directory }}/site.yml"\n',
        "- name: Verify\n  hosts: all\n  tasks: []\n",
        "- hosts: all\n  vars:\n    secret: !vault |\n      encrypted-placeholder\n",
        "- hosts: all\n  tasks: [\n",
    ],
)
def test_L057_recognizes_scenario_playbooks(source: str) -> None:
    """Retain ordinary, import-only, tagged, and malformed playbooks.

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


def test_L057_discovers_imports_without_selecting_molecule_config(tmp_path: Path) -> None:
    """Scenario discovery includes imports and ignores nested config hosts.

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


def test_L057_import_only_playbook_reaches_syntax_check(tmp_path: Path) -> None:
    """Discovery dispatches an import-only playbook and retains its diagnostic.

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
    converge.write_text("- import_playbook: missing.yml\n")
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        invoke.return_value.returncode = 1
        invoke.return_value.stderr = "Unable to retrieve file missing.yml"
        findings = run(venv, project)

    assert invoke.call_args.args[0] == [str(executable), "--syntax-check", str(converge)]
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L057"
    assert findings[0]["file"] == "converge.yml"


def test_L057_unknown_yaml_tag_is_never_executed() -> None:
    """Classifying YAML cannot invoke a Python constructor from a tag."""
    source = "- hosts: all\n  vars:\n    value: !!python/object/apply:os.system ['unexpected-command']\n"
    with patch("os.system") as execute:
        assert _looks_like_playbook(source)
    execute.assert_not_called()
