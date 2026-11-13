"""L057 dispatch must not reach Molecule content through ordinary entrypoints."""

from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from ansible.playbook.task_include import TaskInclude

from apme_engine.validators.ansible.rules import L057_syntax

_SCENARIO = {
    "molecule/default/molecule.yml": b"",
    "molecule/default/converge.yml": b"- hosts: all\n  tasks: []\n",
}
_ORDINARY = b"- hosts: ordinary\n  tasks: []\n"


def _dispatch(tmp_path: Path, files: dict[str, bytes], *, incomplete: set[str] | None = None) -> set[str]:
    """Materialize an upload and return the playbooks dispatched to Ansible.

    Args:
        tmp_path: Temporary workspace.
        files: Complete uploaded snapshot.
        incomplete: Candidates requiring explicit incomplete-assessment errors.

    Returns:
        Snapshot-relative paths passed to syntax checking.
    """
    root = tmp_path / "project"
    executable = tmp_path / "venv/bin/ansible-playbook"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.touch()
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        invoke.return_value.returncode = 0
        findings = L057_syntax.run(tmp_path / "venv", root, file_snapshot=files)
    _assert_coverage(findings, incomplete or set())
    return {Path(call.args[0][-1]).relative_to(root).as_posix() for call in invoke.call_args_list}


def _assert_coverage(findings: list[dict[str, object]], expected: set[str]) -> None:
    """Require explicit, source-addressed errors for unassessable closures.

    Args:
        findings: L057 output after successful mocked runtime checks.
        expected: Entrypoints whose static coverage is incomplete.
    """
    assert {str(finding["file"]) for finding in findings} == expected
    assert len(findings) == len(expected)
    assert all(finding["rule_id"] == "L057" and finding["severity"] == "error" for finding in findings)
    assert all("assessment is incomplete" in str(finding["message"]) for finding in findings)


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "action",
    [
        "import_playbook",
        "ansible.builtin.import_playbook",
        "ansible.legacy.import_playbook",
        "import_tasks",
        "ansible.builtin.import_tasks",
        "ansible.legacy.import_tasks",
    ],
)
def test_direct_static_import_excludes_only_its_entrypoint(tmp_path: Path, action: str) -> None:
    """An ordinary entrypoint cannot expose a scenario play or task file.

    Args:
        tmp_path: Temporary uploaded project.
        action: Short or qualified static import action.
    """
    source = f"- {action}: molecule/default/converge.yml\n"
    if action.endswith("import_tasks"):
        source = f"- hosts: all\n  tasks:\n    - {action}: molecule/default/converge.yml\n"
    assert _dispatch(tmp_path, {**_SCENARIO, "site.yml": source.encode(), "ordinary.yml": _ORDINARY}) == {
        "ordinary.yml"
    }


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "task",
    [
        r"import_tasks: file=molecule\x2fdefault\x2fconverge.yml",
        r"action: import_tasks file=molecule\x2fdefault\x2fconverge.yml",
        r"action: {module: 'import_tasks file=molecule\x2fdefault\x2fconverge.yml'}",
        r"import_tasks: {file: 'molecule\x2fdefault\x2fconverge.yml'}",
    ],
)
def test_ambiguous_import_escapes_are_not_dispatched(tmp_path: Path, task: str) -> None:
    """Ansible escape decoding must not hide a scenario import from the guard.

    Args:
        tmp_path: Temporary uploaded project.
        task: Import spelling containing potentially decoded escape sequences.
    """
    source = f"- hosts: all\n  tasks:\n    - {task}\n".encode()
    files = {**_SCENARIO, "site.yml": source, "ordinary.yml": _ORDINARY}
    assert _dispatch(tmp_path, files, incomplete={"site.yml"}) == {"ordinary.yml"}


def test_mixed_imports_resolve_relative_extensionless_task_files(tmp_path: Path) -> None:
    """Mixed play/task edges use each importing file's directory.

    Args:
        tmp_path: Temporary uploaded project.
    """
    files = {
        **_SCENARIO,
        "site.yml": b"- import_playbook: plays/main.yml\n",
        "plays/main.yml": b"- hosts: all\n  tasks:\n    - import_tasks: ../shared/outer\n",
        "shared/outer": b"- import_tasks: ./nested/../bridge.ansible\n",
        "shared/bridge.ansible": b"- ansible.builtin.import_tasks: ../molecule/default/converge.yml\n",
        "ordinary.yml": _ORDINARY,
    }
    assert _dispatch(tmp_path, files, incomplete={"site.yml", "plays/main.yml"}) == {"ordinary.yml"}


def test_nested_task_import_can_fall_back_to_enclosing_playbook_directory(tmp_path: Path) -> None:
    """Task imports may use the containing playbook base after local lookup fails.

    Args:
        tmp_path: Temporary uploaded project.
    """
    files = {
        **_SCENARIO,
        "site.yml": b"- import_playbook: plays/main.yml\n",
        "plays/main.yml": b"- hosts: all\n  tasks:\n    - import_tasks: tasks/outer.yml\n",
        "plays/tasks/outer.yml": b"- import_tasks: bridge.yml\n",
        "plays/bridge.yml": b"- import_tasks: ../molecule/default/converge.yml\n",
        "ordinary.yml": _ORDINARY,
    }
    assert _dispatch(tmp_path, files, incomplete={"site.yml", "plays/main.yml"}) == {"ordinary.yml"}


def test_cycles_propagate_exclusion_without_blocking_independent_cycles(tmp_path: Path) -> None:
    """A cycle with an excluded exit is tainted; an unrelated cycle stays eligible.

    Args:
        tmp_path: Temporary uploaded project.
    """
    files = {
        **_SCENARIO,
        "site.yml": b"- import_playbook: tainted.yml\n",
        "tainted.yml": b"- import_playbook: site.yml\n- import_playbook: molecule/default/converge.yml\n",
        "clean_a.yml": b"- import_playbook: clean_b.yml\n",
        "clean_b.yml": b"- import_playbook: clean_a.yml\n",
        "ordinary.yml": _ORDINARY,
    }
    assert _dispatch(tmp_path, files) == {"ordinary.yml", "clean_a.yml", "clean_b.yml"}


@pytest.mark.parametrize("section", ["pre_tasks", "tasks", "post_tasks", "handlers"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("branch", ["block", "rescue", "always"])  # type: ignore[untyped-decorator]
def test_nested_task_imports_are_followed(tmp_path: Path, section: str, branch: str) -> None:
    """Static imports inside executable blocks remain part of syntax loading.

    Args:
        tmp_path: Temporary uploaded project.
        section: Play task section.
        branch: Nested block section.
    """
    source = f"- hosts: all\n  {section}:\n    - {branch}:\n        - import_tasks: molecule/default/converge.yml\n"
    assert _dispatch(tmp_path, {**_SCENARIO, "site.yml": source.encode(), "ordinary.yml": _ORDINARY}) == {
        "ordinary.yml"
    }


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "statement",
    [
        "import_tasks: {file: molecule/default/converge.yml}",
        "import_tasks:\n      args: {file: molecule/default/converge.yml}",
        "action: import_tasks molecule/default/converge.yml",
        "local_action: ansible.builtin.import_tasks file=molecule/default/converge.yml",
        "action: {module: import_tasks, file: molecule/default/converge.yml}",
    ],
)
def test_task_action_forms_do_not_bypass_exclusion(tmp_path: Path, statement: str) -> None:
    """Action syntax and mapping arguments preserve static import edges.

    Args:
        tmp_path: Temporary uploaded project.
        statement: Supported task action spelling.
    """
    source = f"- hosts: all\n  tasks:\n    - {statement}\n"
    assert _dispatch(tmp_path, {**_SCENARIO, "site.yml": source.encode(), "ordinary.yml": _ORDINARY}) == {
        "ordinary.yml"
    }


@pytest.mark.parametrize("action", ["import_tasks", "import_playbook"])  # type: ignore[untyped-decorator]
def test_dynamic_paths_never_evaluate_lookups(tmp_path: Path, action: str) -> None:
    """Unknown import targets cannot establish separation from Molecule content.

    Args:
        tmp_path: Temporary uploaded project.
        action: Static import with a runtime-only target.
    """
    statement = f"{action}: \"{{{{ lookup('pipe', 'unexpected-command') }}}}\""
    source = f"- {statement}\n" if action == "import_playbook" else f"- hosts: all\n  tasks:\n    - {statement}\n"
    files = {**_SCENARIO, "site.yml": source.encode(), "ordinary.yml": _ORDINARY}
    with patch("os.system", side_effect=AssertionError("Do not evaluate YAML")):
        assert _dispatch(tmp_path, files, incomplete={"site.yml"}) == {"ordinary.yml"}


def test_data_named_like_imports_does_not_taint_ordinary_candidates(tmp_path: Path) -> None:
    """Module arguments and variables are data, rather than execution scopes.

    Args:
        tmp_path: Temporary uploaded project.
    """
    source = b"""- hosts: all
  vars:
    import_tasks: molecule/default/converge.yml
  tasks:
    - ansible.builtin.debug:
        msg:
          import_playbook: molecule/default/converge.yml
          tasks:
            - import_tasks: molecule/default/converge.yml
"""
    assert _dispatch(tmp_path, {**_SCENARIO, "site.yml": source}) == {"site.yml"}


def test_shared_import_graph_is_parsed_linearly(tmp_path: Path) -> None:
    """Many entrypoints share one cached dependency graph rather than rescanning closures.

    Args:
        tmp_path: Temporary uploaded project.
    """
    files = {**_SCENARIO, "ordinary.yml": _ORDINARY}
    for index in range(80):
        files[f"entry_{index}.yml"] = f"- name: Entry {index}\n  import_playbook: shared.yml\n".encode()
    files["shared.yml"] = b"- hosts: all\n  tasks:\n    - import_tasks: molecule/default/converge.yml\n"
    counts: Counter[str] = Counter()
    original = L057_syntax._static_imports

    def record(source: str, budget: list[int], task_imports: set[str] | None = None) -> tuple[set[str], bool]:
        counts[source] += 1
        return original(source, budget, task_imports)

    with patch.object(L057_syntax, "_static_imports", side_effect=record):
        assert _dispatch(tmp_path, files) == {"ordinary.yml"}
    assert max(counts.values()) == 1
    assert len(counts) == 82


@pytest.mark.parametrize("override", [False, True])  # type: ignore[untyped-decorator]
def test_yaml_merge_imports_respect_explicit_overrides(tmp_path: Path, override: bool) -> None:
    """Merge aliases expose executable imports, while explicit keys take precedence.

    Args:
        tmp_path: Temporary uploaded project.
        override: Whether the task overrides a merged import with an ordinary path.
    """
    source = """- hosts: all
  vars:
    task: &import {import_tasks: molecule/default/converge.yml}
  tasks:
    - <<: *import
"""
    if override:
        source += "      import_tasks: ordinary_tasks.yml\n"
    expected = {"ordinary.yml", "site.yml"} if override else {"ordinary.yml"}
    files = {
        **_SCENARIO,
        "site.yml": source.encode(),
        "ordinary.yml": _ORDINARY,
        "ordinary_tasks.yml": b"- ansible.builtin.debug: {msg: harmless}\n",
    }
    assert _dispatch(tmp_path, files) == expected


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "source",
    [
        b"- hosts: all\n  tasks: [\n",
        b"- &loop {hosts: all, <<: *loop}\n",
        b"- hosts: all\n  vars:\n    nested: " + b"[" * 1500 + b"]" * 1500,
        b"- hosts: all\n#" + b"x" * (1024 * 1024),
    ],
)
def test_unassessable_import_closures_are_not_dispatched(tmp_path: Path, source: bytes) -> None:
    """Malformed, cyclic, deep, and oversized sources cannot bypass assessment.

    Args:
        tmp_path: Temporary uploaded project.
        source: YAML whose complete import closure cannot be established safely.
    """
    assert _dispatch(
        tmp_path, {**_SCENARIO, "site.yml": source, "ordinary.yml": _ORDINARY}, incomplete={"site.yml"}
    ) == {"ordinary.yml"}


def test_yaml_tags_and_alias_fanout_remain_inert_and_bounded(tmp_path: Path) -> None:
    """Tag constructors never run and repeated aliases do not expand traversal.

    Args:
        tmp_path: Temporary uploaded project.
    """
    source = b"""- hosts: all
  vars:
    secret: !!python/object/apply:os.system ['unexpected-command']
    a: &a [value, value]
    b: &b [*a, *a, *a, *a]
    c: &c [*b, *b, *b, *b]
    d: &d [*c, *c, *c, *c]
  tasks:
    - &task {import_tasks: molecule/default/converge.yml}
    - *task
    - *task
"""
    with patch("os.system", side_effect=AssertionError("YAML tags must remain inert")):
        assert _dispatch(tmp_path, {**_SCENARIO, "site.yml": source, "ordinary.yml": _ORDINARY}) == {"ordinary.yml"}


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("setting", "limit"), [("_MAX_IMPORT_FILES", 1), ("_MAX_IMPORT_BYTES", 80), ("_MAX_IMPORT_NODES", 20)]
)
def test_aggregate_import_budgets_exclude_unassessed_candidates(tmp_path: Path, setting: str, limit: int) -> None:
    """Budget exhaustion excludes affected closures without undoing assessed candidates.

    Args:
        tmp_path: Temporary uploaded project.
        setting: Aggregate import assessment limit.
        limit: Small allowance sufficient for one unrelated playbook.
    """
    files = {
        **_SCENARIO,
        "a_ordinary.yml": _ORDINARY,
        "z_site.yml": b"- hosts: all\n  tasks:\n" + b"    - debug: {}\n" * 20,
    }
    with patch.object(L057_syntax, setting, limit):
        assert _dispatch(tmp_path, files, incomplete={"z_site.yml"}) == {"a_ordinary.yml"}


def test_relative_project_root_and_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Relative caller paths normalize consistently without relative_to failures.

    Args:
        tmp_path: Temporary uploaded project.
        monkeypatch: Temporary working-directory override.
    """
    files = {**_SCENARIO, "site.yml": b"- import_playbook: molecule/default/converge.yml\n", "ordinary.yml": _ORDINARY}
    _dispatch(tmp_path, files)
    monkeypatch.chdir(tmp_path)
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        invoke.return_value.returncode = 0
        assert L057_syntax.run(Path("venv"), Path("project"), file_snapshot=files) == []
    assert [Path(call.args[0][-1]).as_posix() for call in invoke.call_args_list] == ["project/ordinary.yml"]


def test_symlinked_root_and_internal_scenario_alias(tmp_path: Path) -> None:
    """A root alias is supported and a scenario file alias cannot bypass exclusion.

    Args:
        tmp_path: Temporary uploaded project.
    """
    files = {**_SCENARIO, "ordinary.yml": _ORDINARY}
    _dispatch(tmp_path, files)
    root = tmp_path / "project"
    (root / "alias.yml").symlink_to("molecule/default/converge.yml")
    files["alias.yml"] = _SCENARIO["molecule/default/converge.yml"]
    alias = tmp_path / "project_alias"
    alias.symlink_to(root, target_is_directory=True)
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        invoke.return_value.returncode = 0
        assert L057_syntax.run(tmp_path / "venv", alias, file_snapshot=files) == []
    assert {Path(call.args[0][-1]).relative_to(alias).as_posix() for call in invoke.call_args_list} == {"ordinary.yml"}


def test_import_symlink_cannot_escape_uploaded_root(tmp_path: Path) -> None:
    """An imported symlink outside the upload is neither read nor dispatched.

    Args:
        tmp_path: Temporary uploaded project.
    """
    files = {**_SCENARIO, "ordinary.yml": _ORDINARY, "site.yml": b"- import_playbook: alias.yml\n"}
    _dispatch(tmp_path, files)
    outside = tmp_path / "outside.yml"
    outside.write_bytes(b'- hosts: all\n  vars:\n    value: \'{{ lookup("pipe", "unexpected-command") }}\'\n')
    root = tmp_path / "project"
    (root / "alias.yml").symlink_to(outside)
    files["alias.yml"] = outside.read_bytes()
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        invoke.return_value.returncode = 0
        _assert_coverage(L057_syntax.run(tmp_path / "venv", root, file_snapshot=files), {"site.yml"})
    assert {Path(call.args[0][-1]).relative_to(root).as_posix() for call in invoke.call_args_list} == {"ordinary.yml"}


def test_symlink_cycle_in_import_path_is_not_dispatched(tmp_path: Path) -> None:
    """A symlink loop is an unknown dependency, not an unhandled resolver error.

    Args:
        tmp_path: Temporary uploaded project.
    """
    files = {**_SCENARIO, "ordinary.yml": _ORDINARY, "site.yml": b"- import_playbook: loop.yml\n"}
    _dispatch(tmp_path, files)
    root = tmp_path / "project"
    (root / "loop.yml").symlink_to("loop.yml")
    with patch("apme_engine.validators.ansible.rules.L057_syntax.subprocess.run") as invoke:
        invoke.return_value.returncode = 0
        _assert_coverage(L057_syntax.run(tmp_path / "venv", root, file_snapshot=files), {"site.yml"})
    assert {Path(call.args[0][-1]).relative_to(root).as_posix() for call in invoke.call_args_list} == {"ordinary.yml"}


@pytest.mark.parametrize("outside", [False, True])  # type: ignore[untyped-decorator]
def test_absolute_imports_cannot_bypass_scenario_or_root_exclusions(tmp_path: Path, outside: bool) -> None:
    """Absolute imports are resolved against the same uploaded-root boundary.

    Args:
        tmp_path: Temporary uploaded project.
        outside: Whether the import escapes the project instead of reaching a scenario.
    """
    destination = tmp_path / ("outside.yml" if outside else "project/molecule/default/converge.yml")
    files = {**_SCENARIO, "ordinary.yml": _ORDINARY, "site.yml": f"- import_playbook: {destination}\n".encode()}
    assert _dispatch(tmp_path, files, incomplete={"site.yml"} if outside else set()) == {"ordinary.yml"}


@pytest.mark.parametrize("target", ["missing.yml", "{{ ordinary_playbook }}"])  # type: ignore[untyped-decorator]
def test_projects_without_molecule_keep_ordinary_import_candidates(tmp_path: Path, target: str) -> None:
    """Molecule-specific exclusions do not change independent ordinary projects.

    Args:
        tmp_path: Temporary uploaded project.
        target: Missing literal or dynamic ordinary import.
    """
    assert _dispatch(tmp_path, {"site.yml": f"- import_playbook: '{target}'\n".encode()}) == {"site.yml"}


def test_missing_ordinary_import_stays_eligible_alongside_molecule(tmp_path: Path) -> None:
    """A missing literal import remains a runtime diagnostic rather than a scenario edge.

    Args:
        tmp_path: Temporary uploaded project.
    """
    assert _dispatch(tmp_path, {**_SCENARIO, "site.yml": b"- import_playbook: missing.yml\n"}) == {"site.yml"}


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("statement", "selected"),
    [
        (
            "import_tasks: molecule/default/converge.yml\n      args: {file: ordinary_tasks.yml}",
            "molecule/default/converge.yml",
        ),
        (
            "import_tasks: {file: molecule/default/converge.yml}\n      args: {file: ordinary_tasks.yml}",
            "molecule/default/converge.yml",
        ),
        (
            "import_tasks: {_raw_params: molecule/default/converge.yml, file: ordinary_tasks.yml}",
            "molecule/default/converge.yml",
        ),
        (
            "import_tasks: {file: molecule/default/converge.yml}\n      args: {_raw_params: ordinary_tasks.yml}",
            "ordinary_tasks.yml",
        ),
        ("import_tasks: file=molecule/default/converge.yml", "molecule/default/converge.yml"),
        ("import_tasks: {_raw_params: '', file: molecule/default/converge.yml}", "molecule/default/converge.yml"),
        (
            "action: {module: 'import_tasks molecule/default/converge.yml', file: ordinary_tasks.yml}",
            "molecule/default/converge.yml",
        ),
        (
            "local_action: {module: 'ansible.builtin.import_tasks file=molecule/default/converge.yml', "
            "file: ordinary_tasks.yml}",
            "molecule/default/converge.yml",
        ),
        ("action: {module: import_tasks, args: 'file=molecule/default/converge.yml'}", "molecule/default/converge.yml"),
        ("import_tasks: ordinary_tasks.yml\n      args: {file: molecule/default/converge.yml}", "ordinary_tasks.yml"),
    ],
)
def test_import_argument_precedence_matches_ansible(tmp_path: Path, statement: str, selected: str) -> None:
    """Dispatch follows the target selected by the installed Ansible task loader.

    Args:
        tmp_path: Temporary uploaded project.
        statement: Direct, mapping, action, or key/value import syntax.
        selected: Filename Ansible actually selects after argument normalization.
    """
    source = f"- hosts: all\n  tasks:\n    - {statement}\n"
    task = TaskInclude.load(yaml.safe_load(source)[0]["tasks"][0])
    assert task.args["_raw_params"] == selected
    files = {
        **_SCENARIO,
        "site.yml": source.encode(),
        "ordinary.yml": _ORDINARY,
        "ordinary_tasks.yml": b"- ansible.builtin.debug: {msg: ordinary}\n",
    }
    expected = {"ordinary.yml"} if selected.startswith("molecule/") else {"ordinary.yml", "site.yml"}
    assert _dispatch(tmp_path, files) == expected


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "body",
    [
        "roles: [demo]",
        "roles: [{role: demo}]",
        "tasks: [{import_role: {name: demo}}]",
        "tasks: [{ansible.builtin.import_role: {name: demo}}]",
        "tasks: [{ansible.legacy.import_role: {name: demo}}]",
        "tasks: [{action: 'import_role name=demo'}]",
        "tasks: [{action: {module: 'import_role name=demo'}}]",
    ],
)
def test_static_role_closures_require_explicit_coverage(tmp_path: Path, body: str) -> None:
    """Static role loading cannot bypass exclusion through tasks or dependencies.

    Args:
        tmp_path: Temporary uploaded project.
        body: Static role declaration or import action.
    """
    files = {
        **_SCENARIO,
        "site.yml": f"- hosts: all\n  {body}\n".encode(),
        "ordinary.yml": _ORDINARY,
        "roles/demo/tasks/main.yml": b"- import_tasks: ../../../molecule/default/converge.yml\n",
    }
    assert _dispatch(tmp_path, files, incomplete={"site.yml"}) == {"ordinary.yml"}


@pytest.mark.parametrize("roles", ["[]", "null"])  # type: ignore[untyped-decorator]
def test_empty_role_sections_preserve_ordinary_dispatch(tmp_path: Path, roles: str) -> None:
    """Empty or null role lists introduce no static role dependencies.

    Args:
        tmp_path: Temporary uploaded project.
        roles: Empty role declaration accepted by Ansible.
    """
    assert _dispatch(tmp_path, {**_SCENARIO, "site.yml": f"- hosts: all\n  roles: {roles}\n".encode()}) == {"site.yml"}


def test_incomplete_dependency_reports_coverage_for_its_callers(tmp_path: Path) -> None:
    """Coverage failures propagate to every candidate that would load the dependency.

    Args:
        tmp_path: Temporary uploaded project.
    """
    files = {
        **_SCENARIO,
        "site.yml": b"- import_playbook: child.yml\n",
        "child.yml": b"- hosts: all\n  roles: [demo]\n",
        "ordinary.yml": _ORDINARY,
    }
    assert _dispatch(tmp_path, files, incomplete={"site.yml", "child.yml"}) == {"ordinary.yml"}


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("statement", "filename"),
    [
        ("import_tasks: {file: 'bridge.yml '}", "bridge.yml "),
        ("import_tasks: {file: ' bridge.yml'}", " bridge.yml"),
        ("import_tasks: {_raw_params: 'bridge.yml '}", "bridge.yml "),
        ("import_tasks:\n      args: {file: 'bridge.yml '}", "bridge.yml "),
        ("action: {module: import_tasks, file: 'bridge.yml '}", "bridge.yml "),
        ('import_tasks: file="bridge.yml "', "bridge.yml "),
        ('action: import_tasks file="bridge.yml "', "bridge.yml "),
        ("import_tasks: 'bridge.yml '", "bridge.yml "),
        ("action: 'import_tasks bridge.yml '", "bridge.yml "),
        ("import_tasks: 'bridge  name.yml'", "bridge  name.yml"),
    ],
)
def test_import_filename_whitespace_matches_ansible(tmp_path: Path, statement: str, filename: str) -> None:
    """Significant filename whitespace cannot redirect assessment to a benign file.

    Args:
        tmp_path: Temporary uploaded project.
        statement: Mapping or quoted key/value import arguments.
        filename: Exact filename selected by installed Ansible.
    """
    source = f"- hosts: all\n  tasks:\n    - {statement}\n"
    task = TaskInclude.load(yaml.safe_load(source)[0]["tasks"][0])
    assert task.args["_raw_params"] == filename
    files = {
        **_SCENARIO,
        "site.yml": source.encode(),
        "ordinary.yml": _ORDINARY,
        "bridge.yml": b"- ansible.builtin.debug: {msg: harmless}\n",
        filename: b"- import_tasks: molecule/default/converge.yml\n",
    }
    targets, unknown = L057_syntax._static_imports(source, [50_000])
    assert targets == {filename}
    assert not unknown
    assert _dispatch(tmp_path, files, incomplete={"site.yml"}) == {"ordinary.yml"}


@pytest.mark.parametrize("ancestor_exists", [False, True])  # type: ignore[untyped-decorator]
def test_invoking_playbook_base_remains_unknown_even_with_ancestor_match(tmp_path: Path, ancestor_exists: bool) -> None:
    """Ansible can prefer an invoking playbook base outside the importer's ancestors.

    Args:
        tmp_path: Temporary uploaded project.
        ancestor_exists: Whether a harmless ancestor file would mislead the index.
    """
    from ansible.parsing.dataloader import DataLoader

    files = {
        **_SCENARIO,
        "site.yml": b"- import_playbook: plays/main.yml\n",
        "plays/main.yml": b"- hosts: all\n  tasks:\n    - import_tasks: ../shared/outer.yml\n",
        "shared/outer.yml": b"- import_tasks: bridge.yml\n",
        "plays/bridge.yml": b"- import_tasks: ../molecule/default/converge.yml\n",
        "ordinary.yml": _ORDINARY,
    }
    if ancestor_exists:
        files["bridge.yml"] = b"- ansible.builtin.debug: {msg: harmless}\n"
    assert _dispatch(tmp_path, files, incomplete={"site.yml", "plays/main.yml"}) == {"ordinary.yml"}
    root = tmp_path / "project"
    loader = DataLoader()
    loader.set_basedir(str(root / "plays"))
    actual = loader.path_dwim_relative(str(root / "plays"), "../shared", "bridge.yml")
    assert Path(actual) == root / "plays/bridge.yml"


def test_nested_include_stack_can_prefer_invoking_base_over_existing_sibling(tmp_path: Path) -> None:
    """A physical sibling cannot establish a nested task import's actual target.

    Args:
        tmp_path: Temporary uploaded project.
    """
    from ansible.parsing.dataloader import DataLoader

    files = {
        **_SCENARIO,
        "site.yml": b"- import_playbook: plays/main.yml\n",
        "plays/main.yml": b"- hosts: all\n  tasks:\n    - import_tasks: ../shared/outer.yml\n",
        "shared/outer.yml": b"- import_tasks: inner.yml\n",
        "shared/inner.yml": b"- import_tasks: bridge.yml\n",
        "shared/bridge.yml": b"- ansible.builtin.debug: {msg: harmless}\n",
        "plays/bridge.yml": b"- import_tasks: ../molecule/default/converge.yml\n",
        "ordinary.yml": _ORDINARY,
    }
    assert _dispatch(tmp_path, files, incomplete={"site.yml", "plays/main.yml"}) == {"ordinary.yml"}
    root = tmp_path / "project"
    assert (root / "shared/bridge.yml").is_file()
    loader = DataLoader()
    loader.set_basedir(str(root / "plays"))
    # The nearest include was authored as `inner.yml`, whose dirname is empty.
    actual = loader.path_dwim_relative(str(root / "plays"), "", "bridge.yml")
    assert Path(actual) == root / "plays/bridge.yml"


def test_playbook_like_task_candidate_still_has_unknown_caller_stack(tmp_path: Path) -> None:
    """Discovery heuristics do not establish invocation context for imported tasks.

    Args:
        tmp_path: Temporary uploaded project.
    """
    files = {
        **_SCENARIO,
        "site.yml": b"- hosts: all\n  tasks:\n    - import_tasks: a_shared.yml\n",
        "a_shared.yml": b"- tasks: {custom: data}\n- import_tasks: bridge.yml\n",
        "bridge.yml": b"- ansible.builtin.debug: {msg: harmless}\n",
        "ordinary.yml": _ORDINARY,
    }
    assert _dispatch(tmp_path, files, incomplete={"site.yml", "a_shared.yml"}) == {"ordinary.yml"}
