"""Regression coverage for bounded Molecule discovery and L057 dispatch."""

from pathlib import Path
from unittest.mock import patch

from apme_engine.molecule import analysis as molecule
from apme_engine.validators.ansible.rules import L057_syntax


def test_legacy_import_is_in_static_closure() -> None:
    """Recognize legacy-qualified playbook imports in scenario closures."""
    files = {
        "molecule/default/molecule.yml": b"",
        "molecule/default/converge.yml": b"- ansible.legacy.import_playbook: ../../shared.yml\n",
        "shared.yml": b"- hosts: all\n  tasks: []\n",
    }
    paths, incomplete = molecule.molecule_playbook_closure(files)
    assert "shared.yml" in paths
    assert not incomplete
    assert not molecule.validate_molecule(files)


def test_truncated_closure_skips_all_runtime_checks(tmp_path: Path) -> None:
    """Skip syntax checks when a scenario import chain exceeds the depth limit.

    Args:
        tmp_path: Temporary project and executable directory.
    """
    root = tmp_path / "project"
    root.mkdir()
    executable = tmp_path / "venv/bin/ansible-playbook"
    executable.parent.mkdir(parents=True)
    executable.touch()
    files = {
        "molecule/default/molecule.yml": b"",
        "molecule/default/converge.yml": b"- import_playbook: ../../chain0.yml\n",
        "site.yml": b"- import_playbook: chain67.yml\n",
        "chain67.yml": b"- hosts: all\n  tasks: []\n",
    }
    files.update({f"chain{i}.yml": f"- import_playbook: chain{i + 1}.yml\n".encode() for i in range(67)})
    paths, incomplete = molecule.molecule_playbook_closure(files)
    assert incomplete and "chain67.yml" not in paths
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        findings = L057_syntax.run(tmp_path / "venv", root, file_snapshot=files)
    assert not invoke.called
    assert len(findings) == 1 and "incomplete" in str(findings[0]["message"])


def test_unknown_unrelated_configuration_keeps_closure_complete() -> None:
    """Unrelated environment substitutions do not block runtime validation."""
    files = {
        "molecule/default/molecule.yml": b"provisioner:\n  env:\n    MY_TOKEN: ${UNKNOWN_TOKEN}\n",
        "molecule/default/converge.yml": b"- hosts: all\n  tasks: []\n",
    }
    paths, incomplete = molecule.molecule_playbook_closure(files)
    assert "molecule/default/converge.yml" in paths
    assert not incomplete


def test_shared_playbook_is_parsed_once_across_scenarios() -> None:
    """Reuse context-independent playbook parsing across scenario contexts."""
    files = {
        f"molecule/s{i}/molecule.yml": b"provisioner:\n  playbooks:\n    converge: ../../shared.yml\n" for i in range(8)
    }
    files["shared.yml"] = b"- hosts: all\n  tasks: []\n"
    with patch.object(molecule, "_playbook", wraps=molecule._playbook) as parse:
        paths, incomplete = molecule.molecule_playbook_closure(files)
    assert paths == frozenset({"shared.yml"}) and not incomplete
    assert parse.call_count == 1


def test_composition_and_request_work_are_bounded() -> None:
    """Apply per-file composition limits and the shared work budget.

    Raises:
        AssertionError: If the shared budget does not stop repeated work.
    """
    with patch.object(molecule, "_MAX_YAML_NODES", 20):
        files = {
            "molecule/default/molecule.yml": b"x: [" + b"0," * 30 + b"0]",
            "molecule/good/molecule.yml": b"scenario:\n  test_sequence: [not_an_action]\n",
        }
        findings = molecule.validate_molecule(files)
        assert {(item["rule_id"], item["file"]) for item in findings} == {
            ("L115", "molecule/default/molecule.yml"),
            ("L117", "molecule/good/molecule.yml"),
        }
        assert molecule.molecule_playbook_closure(files)[1]
    budget = molecule._WorkBudget(steps_left=10)
    try:
        for i in range(20):
            molecule._load(f"scenario{i}", b"x: 1\n", {}, budget)
    except molecule.MoleculeAssessmentLimitError:
        pass
    else:
        raise AssertionError("request work limit was not enforced")


def test_deep_playbook_does_not_abort_other_scenario() -> None:
    """Report deeply nested playbooks while assessing independent scenarios."""
    files = {
        "molecule/default/molecule.yml": b"",
        "molecule/default/converge.yml": b"- hosts: all\n  vars:\n    nested: " + b"[" * 200 + b"]" * 200,
        "molecule/good/molecule.yml": b"scenario:\n  test_sequence: [not_an_action]\n",
    }
    findings = molecule.validate_molecule(files)
    assert {(item["rule_id"], item["file"]) for item in findings} == {
        ("L119", "molecule/default/converge.yml"),
        ("L117", "molecule/good/molecule.yml"),
    }
    assert molecule.molecule_playbook_closure(files)[1]


def test_aggregate_budget_exhaustion_still_fails_closed() -> None:
    """Propagate request-wide budget exhaustion as an incomplete assessment.

    Raises:
        AssertionError: If assessment suppresses the exhausted budget.
    """
    lookup = molecule._CollectionLookup
    files = {f"molecule/s{i}/molecule.yml": b"" for i in range(20)}
    with patch.object(
        molecule,
        "_CollectionLookup",
        side_effect=lambda snapshot: lookup(snapshot, budget=molecule._WorkBudget(steps_left=20)),
    ):
        try:
            molecule.validate_molecule(files)
        except molecule.MoleculeAssessmentLimitError:
            pass
        else:
            raise AssertionError("aggregate budget exhaustion was suppressed")


def test_limit_failure_skips_runtime_checks(tmp_path: Path) -> None:
    """Skip runtime checks if scenario discovery exhausts its work budget.

    Args:
        tmp_path: Temporary project and executable directory.
    """
    executable = tmp_path / "venv/bin/ansible-playbook"
    executable.parent.mkdir(parents=True)
    executable.touch()
    with (
        patch.object(L057_syntax, "molecule_playbook_closure", side_effect=molecule.MoleculeAssessmentLimitError),
        patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke,
    ):
        findings = L057_syntax.run(tmp_path / "venv", tmp_path, file_snapshot={"molecule.yml": b""})
    assert not invoke.called
    assert len(findings) == 1 and "incomplete" in str(findings[0]["message"])
