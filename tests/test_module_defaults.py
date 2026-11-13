"""Module defaults resolution, invocation isolation, and argspec regressions."""

from __future__ import annotations

import copy
import importlib
import json
import shlex
import subprocess
import sys
import sysconfig
import venv
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from apme_engine.validators.ansible.cache import PluginCache
from apme_engine.validators.ansible.module_defaults import is_dynamic, resolve_module_defaults


def _task(
    layers: list[object],
    *,
    module: str = "ansible.builtin.file",
    arguments: dict[str, object] | None = None,
    key: str = "task#defaults",
) -> dict[str, object]:
    """Build a source-attributed task with one ordered invocation context.

    Args:
        layers: Raw root-to-task defaults layers.
        module: Authored module or action name.
        arguments: Explicit task arguments.
        key: Stable source task identity.

    Returns:
        A task accepted by the shared resolver and both argspec rules.
    """
    return {
        "key": key,
        "file": "playbooks/site.yml",
        "line": [12, 15],
        "module": module,
        "module_options": dict(arguments or {}),
        "module_defaults_contexts": [layers],
    }


def _arguments(task: dict[str, object]) -> dict[str, object]:
    """Read prepared arguments while rejecting malformed helper output.

    Args:
        task: Prepared task payload.

    Returns:
        The effective argument mapping.
    """
    arguments = task["module_options"]
    assert isinstance(arguments, dict)
    return cast(dict[str, object], arguments)


@pytest.mark.parametrize("manager", [["auto"], ["auto", "invalid-manager"], {"invalid-manager": True}])  # type: ignore[untyped-decorator]
def test_list_valued_defaults_validate_each_documented_choice(runtime_venv: Path, manager: object) -> None:
    """Real package_facts metadata includes list arguments and mapping-form choices.

    Args:
        runtime_venv: Installed Ansible metadata environment.
        manager: Valid list or invalid literal choices, including an unhashable value.
    """
    from apme_engine.validators.ansible.rules import L058_argspec_doc

    # package_facts declares manager choices in its documentation; its runtime
    # argument_spec checks managers after AnsibleModule initialization instead.
    task = _task([{"ansible.builtin.package_facts": {"manager": manager}}], module="ansible.builtin.package_facts")
    findings = L058_argspec_doc.run([task], runtime_venv)
    if manager == ["auto"]:
        assert findings == []
    else:
        assert any("not one of" in str(finding["message"]) for finding in findings)
        assert all(finding["remediation_class"] == "manual-review" for finding in findings)


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("choices", [["auto", "other"], {"auto": "description", "other": "description"}])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("value", [["auto"], ["auto", "bad"], {"bad": True}])  # type: ignore[untyped-decorator]
def test_argspec_list_and_mapping_choices(rule_id: str, choices: object, value: object) -> None:
    """Both metadata sources accept valid list elements and reject invalid literals.

    Args:
        rule_id: Docstring or captured-spec validator.
        choices: Allowed values represented as a list or documentation mapping.
        value: List-valued argument or an invalid unhashable literal.
    """
    rule = importlib.import_module(
        "apme_engine.validators.ansible.rules." + ("L058_argspec_doc" if rule_id == "L058" else "L059_argspec_mock")
    )
    field = "options" if rule_id == "L058" else "argument_spec"
    specs: dict[str, object] = {"example.test.module": {field: {"manager": {"type": "list", "choices": choices}}}}
    task: dict[str, object] = {"module": "example.test.module", "module_options": {"manager": value}, "key": "task"}
    findings = rule._check_tasks_against_specs(specs, [task])
    assert bool(findings) is (value != ["auto"])
    assert all("not one of" in str(finding["message"]) for finding in findings)


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("value", [[], ["present", "absent"], ["present"]])  # type: ignore[untyped-decorator]
def test_scalar_choice_rejects_list_values(rule_id: str, value: object) -> None:
    """A list of individually valid choices is invalid for a scalar parameter.

    Args:
        rule_id: Docstring or captured-spec validator.
        value: List supplied to a scalar parameter.
    """
    rule = importlib.import_module(
        "apme_engine.validators.ansible.rules." + ("L058_argspec_doc" if rule_id == "L058" else "L059_argspec_mock")
    )
    field = "options" if rule_id == "L058" else "argument_spec"
    specs: dict[str, object] = {
        "example.test.module": {field: {"state": {"type": "str", "choices": ["present", "absent"]}}}
    }
    task: dict[str, object] = {"module": "example.test.module", "module_options": {"state": value}, "key": "task"}
    findings = rule._check_tasks_against_specs(specs, [task])
    assert len(findings) == 1
    assert "not one of" in str(findings[0]["message"])


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("value", [[1, 2], "1,2"])  # type: ignore[untyped-decorator]
def test_choice_checks_apply_ansible_list_conversion(rule_id: str, value: object) -> None:
    """Ansible converts comma-separated lists and string elements before choices.

    Args:
        rule_id: Docstring or captured-spec validator.
        value: Value accepted after Ansible's list and element conversion.
    """
    rule = importlib.import_module(
        "apme_engine.validators.ansible.rules." + ("L058_argspec_doc" if rule_id == "L058" else "L059_argspec_mock")
    )
    field = "options" if rule_id == "L058" else "argument_spec"
    definition = {"type": "list", "elements": "str", "choices": ["1", "2"]}
    specs = {"example.test.module": {field: {"items": definition}}}
    task = {"module": "example.test.module", "module_options": {"items": value}, "key": "task"}
    assert rule._check_tasks_against_specs(specs, [task]) == []


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("value,has_error", [("2", False), ("3", True)])  # type: ignore[untyped-decorator]
def test_choice_checks_apply_scalar_conversion(rule_id: str, value: str, has_error: bool) -> None:
    """Integer choice checks use the converted numeric value.

    Args:
        rule_id: Docstring or captured-spec validator.
        value: String convertible to an integer.
        has_error: Whether the converted value is outside the choices.
    """
    rule = importlib.import_module(
        "apme_engine.validators.ansible.rules." + ("L058_argspec_doc" if rule_id == "L058" else "L059_argspec_mock")
    )
    field = "options" if rule_id == "L058" else "argument_spec"
    specs = {"example.test.module": {field: {"level": {"type": "int", "choices": [1, 2]}}}}
    task = {"module": "example.test.module", "module_options": {"level": value}, "key": "task"}
    assert bool(rule._check_tasks_against_specs(specs, [task])) is has_error


def _write_collection(site: Path, namespace: str, name: str, runtime: str) -> None:
    """Install inert module/action candidates and runtime metadata in a test venv.

    Args:
        site: Temporary venv's installed site-packages directory.
        namespace: Collection namespace.
        name: Collection name.
        runtime: Literal runtime metadata, including action groups and redirects.
    """
    root = site / "ansible_collections" / namespace / name
    metadata = root / "meta/runtime.yml"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(runtime, encoding="utf-8")
    for relative in ("plugins/modules/sample.py", "plugins/action/action_only.py"):
        candidate = root / relative
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(
            "raise AssertionError('Defaults resolution must never execute plugin code')\n", encoding="utf-8"
        )


@pytest.fixture(scope="module")  # type: ignore[untyped-decorator]
def runtime_venv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Provide real Ansible plus synthetic collections under an installed root.

    Args:
        tmp_path_factory: Factory for a module-scoped temporary environment.

    Returns:
        Venv root suitable for the resolver's isolated subprocess.
    """
    root = cast(Path, tmp_path_factory.mktemp("module-defaults-runtime")) / "venv"
    venv.EnvBuilder(system_site_packages=True, with_pip=False, symlinks=True).create(root)
    site = root / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    # Reuse installed Ansible/dependencies, without exposing the project root.
    (site / "installed-test-runtime.pth").write_text(sysconfig.get_path("purelib") + "\n", encoding="utf-8")
    _write_collection(
        site,
        "review",
        "defaults",
        "plugin_routing:\n"
        "  modules:\n"
        "    old_sample:\n"
        "      redirect: review.defaults.sample\n"
        "action_groups:\n"
        "  first:\n"
        "    - sample\n"
        "  second:\n"
        "    - review.defaults.old_sample\n"
        "  nested:\n"
        "    - metadata:\n"
        "        extend_group: [first, review.defaults.second]\n"
        "  action_group:\n"
        "    - action_only\n",
    )
    _write_collection(site, "amazon", "aws", "action_groups:\n  aws:\n    - sample\n")
    return root


@pytest.mark.parametrize("contexts", [None, [[]], [[{}]], [[None]]])  # type: ignore[untyped-decorator]
def test_no_defaults_copies_tasks_without_starting_a_process(contexts: object, tmp_path: Path) -> None:
    """Absent and empty defaults need neither Ansible nor a usable venv.

    Args:
        contexts: Empty or absent inherited context representation.
        tmp_path: Deliberately missing runtime environment.
    """
    task = _task([], arguments={"path": "/tmp/example"})
    if contexts is None:
        task.pop("module_defaults_contexts")
    else:
        task["module_defaults_contexts"] = contexts
    original = copy.deepcopy(task)
    with patch("subprocess.run", side_effect=AssertionError("No defaults require no subprocess")):
        result = resolve_module_defaults([task], tmp_path / "missing")
    assert result.violations == []
    assert result.tasks == [original]
    assert result.tasks[0] is not task
    assert task == original


def test_later_module_entry_replaces_whole_parameter_mapping(runtime_venv: Path) -> None:
    """Root, block list entries, and task layers replace canonical entries.

    Args:
        runtime_venv: Installed Ansible runtime.
    """
    task = _task(
        [
            {"file": {"owner": "root", "mode": "0644"}},
            [{"ansible.builtin.file": {"group": "wheel"}}, {"file": {"state": "touch"}}],
            {"ansible.builtin.file": {"mode": "0600"}},
        ],
        arguments={"path": "/tmp/example", "mode": "0640"},
    )
    before = copy.deepcopy(task)
    result = resolve_module_defaults([task], runtime_venv)
    assert result.violations == []
    assert len(result.tasks) == 1
    assert _arguments(result.tasks[0]) == {"path": "/tmp/example", "mode": "0640"}
    assert task == before


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "reset", ["file", "ansible.legacy.file", "ansible.builtin.file"]
)
def test_empty_module_entry_resets_inherited_defaults(runtime_venv: Path, reset: str) -> None:
    """Short, legacy, and builtin names reset the same installed builtin module.

    Args:
        runtime_venv: Installed Ansible runtime.
        reset: Module spelling used by the inner reset.
    """
    result = resolve_module_defaults(
        [_task([{"file": {"owner": "root", "mode": "0644"}}, {reset: {}}], arguments={"path": "/tmp/a"})],
        runtime_venv,
    )
    assert result.violations == []
    assert [_arguments(task) for task in result.tasks] == [{"path": "/tmp/a"}]


def test_empty_outer_mapping_does_not_reset_a_module(runtime_venv: Path) -> None:
    """An empty module_defaults mapping leaves inherited module entries intact.

    Args:
        runtime_venv: Installed Ansible runtime.
    """
    result = resolve_module_defaults([_task([{"file": {"owner": "root"}}, {}])], runtime_venv)
    assert result.violations == []
    assert [_arguments(task) for task in result.tasks] == [{"owner": "root"}]


@pytest.mark.parametrize("reverse", [False, True])  # type: ignore[untyped-decorator]
def test_module_alias_collisions_follow_authored_order(runtime_venv: Path, reverse: bool) -> None:
    """Canonical collisions use the final authored entry within a layer.

    Args:
        runtime_venv: Installed Ansible runtime.
        reverse: Reverse the short-name and FQCN insertion order.
    """
    entries = [("file", {"owner": "short"}), ("ansible.builtin.file", {"owner": "qualified"})]
    if reverse:
        entries.reverse()
    result = resolve_module_defaults([_task([dict(entries)])], runtime_venv)
    assert result.violations == []
    assert [_arguments(task) for task in result.tasks] == [{"owner": "short" if reverse else "qualified"}]


def test_invocation_contexts_are_assessed_independently(runtime_venv: Path) -> None:
    """A shared task cannot borrow required arguments from a different caller.

    Args:
        runtime_venv: Installed Ansible runtime.
    """
    task = _task([])
    task["module_defaults_contexts"] = [
        [{"file": {"path": "/tmp/first", "owner": "root"}}],
        [{"file": {"group": "wheel"}}],
    ]
    before = copy.deepcopy(task)
    result = resolve_module_defaults([task], runtime_venv)
    assert result.violations == []
    assert [_arguments(prepared) for prepared in result.tasks] == [
        {"path": "/tmp/first", "owner": "root"},
        {"group": "wheel"},
    ]
    assert all(prepared is not task for prepared in result.tasks)
    assert task == before


@pytest.mark.parametrize("group", ["first", "nested"])  # type: ignore[untyped-decorator]
def test_installed_action_groups_resolve_relative_redirected_and_extended_members(
    runtime_venv: Path, group: str
) -> None:
    """Runtime metadata resolves short members, redirects, and nested groups.

    Args:
        runtime_venv: Installed Ansible and synthetic collection runtime.
        group: Direct or extended collection action group.
    """
    result = resolve_module_defaults(
        [_task([{f"group/review.defaults.{group}": {"region": "test"}}], module="review.defaults.old_sample")],
        runtime_venv,
    )
    assert result.violations == []
    assert [_arguments(task) for task in result.tasks] == [{"region": "test"}]


def test_historical_group_alias_uses_builtin_extension_metadata(runtime_venv: Path) -> None:
    """group/aws expands through installed Ansible's historical group metadata.

    Args:
        runtime_venv: Runtime containing a synthetic installed amazon.aws group.
    """
    result = resolve_module_defaults(
        [_task([{"group/aws": {"region": "test"}}], module="amazon.aws.sample")], runtime_venv
    )
    assert result.violations == []
    assert [_arguments(task) for task in result.tasks] == [{"region": "test"}]


def test_action_only_group_members_are_resolved_without_import(runtime_venv: Path) -> None:
    """An action-only plugin participates without executing its top-level code.

    Args:
        runtime_venv: Runtime with an action candidate that raises if imported.
    """
    result = resolve_module_defaults(
        [_task([{"group/review.defaults.action_group": {"enabled": True}}], module="review.defaults.action_only")],
        runtime_venv,
    )
    assert result.violations == []
    assert [_arguments(task) for task in result.tasks] == [{"enabled": True}]


def test_task_collection_scope_resolves_action_but_not_short_defaults_names(runtime_venv: Path) -> None:
    """Task collection search order cannot qualify short module_defaults keys.

    Args:
        runtime_venv: Installed synthetic collection runtime.
    """
    qualified = _task([{"review.defaults.sample": {"region": "installed"}}], module="sample", key="qualified")
    qualified["collections_in_play"] = ["review.defaults"]
    short_defaults = _task([{"sample": {"region": "ambiguous"}}], module="sample", key="short-defaults")
    short_defaults["collections_in_play"] = ["review.defaults"]
    result = resolve_module_defaults([qualified, short_defaults], runtime_venv)
    assert len(result.tasks) == 1
    assert result.tasks[0]["module"] == "review.defaults.sample"
    assert _arguments(result.tasks[0]) == {"region": "installed"}
    assert len(result.violations) == 1
    assert result.violations[0]["path"] == "short-defaults"
    assert result.violations[0]["severity"] == "warning"


def test_group_mapping_order_survives_request_deduplication(runtime_venv: Path) -> None:
    """Equivalent key sets with different authored order are different requests.

    Args:
        runtime_venv: Runtime with two groups containing the same module.
    """
    first = {"group/review.defaults.first": {"region": "first"}, "group/review.defaults.second": {"region": "second"}}
    second = dict(reversed(list(first.items())))
    result = resolve_module_defaults(
        [
            _task([first], module="review.defaults.sample", key="first"),
            _task([second], module="review.defaults.sample", key="second"),
            _task([copy.deepcopy(first)], module="review.defaults.sample", key="repeat"),
        ],
        runtime_venv,
    )
    assert result.violations == []
    assert [_arguments(task) for task in result.tasks] == [
        {"region": "second"},
        {"region": "first"},
        {"region": "second"},
    ]


def test_module_and_explicit_arguments_override_matching_groups(runtime_venv: Path) -> None:
    """Module entries beat all groups, and explicit task values beat both.

    Args:
        runtime_venv: Installed synthetic collection runtime.
    """
    result = resolve_module_defaults(
        [
            _task(
                [
                    {
                        "review.defaults.sample": {"region": "module", "mode": "module"},
                        "group/review.defaults.first": {"region": "group", "mode": "group", "group_only": True},
                    }
                ],
                module="review.defaults.sample",
                arguments={"region": "explicit"},
            )
        ],
        runtime_venv,
    )
    assert result.violations == []
    assert [_arguments(task) for task in result.tasks] == [{"region": "explicit", "mode": "module", "group_only": True}]


def test_module_reset_does_not_clear_group_defaults(runtime_venv: Path) -> None:
    """Resetting one module removes its defaults while matching groups remain.

    Args:
        runtime_venv: Installed synthetic collection runtime.
    """
    result = resolve_module_defaults(
        [
            _task(
                [
                    {
                        "group/review.defaults.first": {"region": "group"},
                        "review.defaults.sample": {"region": "module"},
                    },
                    {"review.defaults.old_sample": {}},
                ],
                module="review.defaults.sample",
            )
        ],
        runtime_venv,
    )
    assert result.violations == []
    assert [_arguments(task) for task in result.tasks] == [{"region": "group"}]


def test_group_reset_clears_only_that_groups_defaults(runtime_venv: Path) -> None:
    """An empty group entry removes its parameters while module defaults remain.

    Args:
        runtime_venv: Installed synthetic collection runtime.
    """
    result = resolve_module_defaults(
        [
            _task(
                [
                    {
                        "group/review.defaults.first": {"region": "group", "owner": "group"},
                        "review.defaults.sample": {"region": "module"},
                    },
                    {"group/review.defaults.first": {}},
                ],
                module="review.defaults.sample",
            )
        ],
        runtime_venv,
    )
    assert result.violations == []
    assert [_arguments(task) for task in result.tasks] == [{"region": "module"}]


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "layers", [["literal"], [[{"file": {}}, 42]], [{"file": []}], [{42: {}}], [{"{{ name }}": {}}]]
)
def test_malformed_literal_defaults_are_source_attributed_errors(layers: list[object], tmp_path: Path) -> None:
    """Provably invalid defaults are errors without invoking Ansible.

    Args:
        layers: Invalid literal structure or nonstatic defaults name.
        tmp_path: Missing runtime, unnecessary for structural checks.
    """
    task = _task(layers)
    with patch("subprocess.run", side_effect=AssertionError("Literal shape errors need no subprocess")):
        result = resolve_module_defaults([task], tmp_path / "missing")
    assert result.tasks == []
    assert len(result.violations) == 1
    finding = result.violations[0]
    assert finding["rule_id"] == "L058"
    assert finding["severity"] == "error"
    assert finding["remediation_class"] == "manual-review"
    assert finding["file"] == task["file"]
    assert finding["line"] == 12
    assert finding["path"] == task["key"]


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "contexts", ["not-contexts", {}, [], ["not-layers"], [[], "not-layers"]]
)
def test_malformed_context_payload_never_silently_passes(contexts: object, tmp_path: Path) -> None:
    """Bad transport shapes produce explicit diagnostics without a subprocess.

    Args:
        contexts: Invalid invocation-context envelope.
        tmp_path: Deliberately unavailable runtime.
    """
    task = _task([])
    task["module_defaults_contexts"] = contexts
    with patch("subprocess.run", side_effect=AssertionError("Malformed context needs no subprocess")):
        result = resolve_module_defaults([task], tmp_path / "missing")
    assert result.violations
    assert all(finding["rule_id"] == "L058" for finding in result.violations)


@pytest.mark.parametrize("truncated", [False, True])  # type: ignore[untyped-decorator]
def test_incomplete_or_excess_contexts_skip_only_the_affected_task(tmp_path: Path, truncated: bool) -> None:
    """Context caps cannot make partial assessment appear complete.

    Args:
        tmp_path: Missing runtime, unnecessary for cap detection.
        truncated: Whether the producer already flagged truncation.
    """
    affected = _task([], key="affected")
    if truncated:
        affected["module_defaults_incomplete"] = True
    else:
        affected["module_defaults_contexts"] = [[] for _ in range(65)]
    independent = _task([], key="independent")
    with patch("subprocess.run", side_effect=AssertionError("Incomplete contexts need no subprocess")):
        result = resolve_module_defaults([affected, independent], tmp_path / "missing")
    assert result.tasks == [independent]
    assert len(result.violations) == 1
    assert result.violations[0]["path"] == "affected"
    assert result.violations[0]["severity"] == "warning"
    assert result.violations[0]["remediation_class"] == "manual-review"


def test_excess_layers_skip_only_the_affected_context(tmp_path: Path) -> None:
    """One invocation cannot bypass the layer bound with many small mappings.

    Args:
        tmp_path: Missing runtime, unnecessary for detecting excessive layers.
    """
    task = _task([])
    task["module_defaults_contexts"] = [[{"file": {}} for _ in range(257)], []]
    with patch("subprocess.run", side_effect=AssertionError("Layer limits need no subprocess")):
        result = resolve_module_defaults([task], tmp_path / "missing")
    assert len(result.tasks) == 1
    assert _arguments(result.tasks[0]) == {}
    assert len(result.violations) == 1
    assert result.violations[0]["severity"] == "warning"


@pytest.mark.parametrize(
    ("module", "entry"),
    [("unavailable.collection.module", "file"), ("ansible.builtin.file", "group/review.defaults.unknown")],
)  # type: ignore[untyped-decorator]
def test_unavailable_module_or_group_is_coverage_not_malformed(runtime_venv: Path, module: str, entry: str) -> None:
    """Unavailable runtime metadata gets an explicit warning and no argspec task.

    Args:
        runtime_venv: Installed Ansible runtime.
        module: Task action whose resolution may be unavailable.
        entry: Defaults entry whose group may be unavailable.
    """
    result = resolve_module_defaults([_task([{entry: {"mode": "0644"}}], module=module)], runtime_venv)
    assert result.tasks == []
    assert len(result.violations) == 1
    assert result.violations[0]["rule_id"] == "L058"
    assert result.violations[0]["severity"] == "warning"
    assert result.violations[0]["file"] == "playbooks/site.yml"
    assert result.violations[0]["line"] == 12
    assert result.violations[0]["path"] == "task#defaults"
    assert result.violations[0]["remediation_class"] == "manual-review"


def test_dynamic_selected_mapping_warns_but_does_not_block_independent_context(runtime_venv: Path) -> None:
    """An unknown mapping cannot supply required keys to another invocation.

    Args:
        runtime_venv: Installed Ansible runtime.
    """
    task = _task([])
    task["module_defaults_contexts"] = [
        [{"file": "{{ unknown_defaults }}"}],
        [{"file": {"path": "/tmp/known"}}],
    ]
    result = resolve_module_defaults([task], runtime_venv)
    assert [_arguments(prepared) for prepared in result.tasks] == [{"path": "/tmp/known"}]
    assert len(result.violations) == 1
    assert result.violations[0]["severity"] == "warning"


def test_dynamic_mapping_replaced_by_literal_reset_is_no_longer_needed(runtime_venv: Path) -> None:
    """A later reset removes a templated mapping before parameter assessment.

    Args:
        runtime_venv: Installed Ansible runtime.
    """
    result = resolve_module_defaults([_task([{"file": "{{ unknown }}"}, {"file": {}}])], runtime_venv)
    assert result.violations == []
    assert [_arguments(task) for task in result.tasks] == [{}]


def test_dynamic_parameter_names_require_mapping_context(runtime_venv: Path) -> None:
    """Unknown parameter names must not be diagnosed as literal unsupported keys.

    Args:
        runtime_venv: Installed Ansible runtime.
    """
    result = resolve_module_defaults([_task([{"file": {"{{ parameter_name }}": "value"}}])], runtime_venv)
    assert result.tasks == []
    assert len(result.violations) == 1
    assert result.violations[0]["severity"] == "warning"


def test_resolver_preserves_parameter_alias_spellings(runtime_venv: Path) -> None:
    """Parameter aliases remain separate until argspec validation applies them.

    Args:
        runtime_venv: Installed Ansible runtime.
    """
    task = _task([{"file": {"dest": "/tmp/default"}}], arguments={"path": "/tmp/explicit"})
    result = resolve_module_defaults([task], runtime_venv)
    assert result.violations == []
    assert [_arguments(prepared) for prepared in result.tasks] == [{"dest": "/tmp/default", "path": "/tmp/explicit"}]


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("alias_in_defaults", [False, True])  # type: ignore[untyped-decorator]
def test_both_argspec_rules_apply_alias_precedence_after_defaults(
    runtime_venv: Path, rule_id: str, alias_in_defaults: bool
) -> None:
    """An alias wins over a canonical spelling after the exact-key merge.

    Args:
        runtime_venv: Installed Ansible runtime.
        rule_id: Docstring or captured-spec argspec rule.
        alias_in_defaults: Whether the winning alias originated in defaults.
    """
    rule = importlib.import_module(
        "apme_engine.validators.ansible.rules." + ("L058_argspec_doc" if rule_id == "L058" else "L059_argspec_mock")
    )
    defaults = {"status": "bad"} if alias_in_defaults else {"state": "bad"}
    explicit: dict[str, object] = {"state": "present"} if alias_in_defaults else {"status": "present"}
    spec = {"state": {"type": "str", "choices": ["present", "absent"], "aliases": ["status"]}}
    spec_field = "options" if rule_id == "L058" else "argument_spec"
    with (
        patch.object(rule, "plugin_cache", PluginCache()),
        patch.object(rule, "_fetch_specs", return_value={"ansible.builtin.file": {spec_field: spec}}),
    ):
        findings = rule.run([_task([{"file": defaults}], arguments=explicit)], runtime_venv)
    if alias_in_defaults:
        assert any("not one of" in str(finding["message"]) for finding in findings)
    else:
        assert findings == []


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("provide_required", [False, True])  # type: ignore[untyped-decorator]
def test_dynamic_leaf_values_preserve_known_key_validation(
    runtime_venv: Path, rule_id: str, provide_required: bool
) -> None:
    """A dynamic choice value does not hide unsupported or missing static keys.

    Args:
        runtime_venv: Installed Ansible runtime.
        rule_id: Docstring or captured-spec argspec rule.
        provide_required: Whether defaults supply the required path parameter.
    """
    rule = importlib.import_module(
        "apme_engine.validators.ansible.rules." + ("L058_argspec_doc" if rule_id == "L058" else "L059_argspec_mock")
    )
    values: dict[str, object] = {"state": "{{ desired_state }}", "bogus": "known"}
    if provide_required:
        values["path"] = "/tmp/known"
    task = _task([{"file": values}])
    prepared = resolve_module_defaults([task], runtime_venv)
    assert prepared.violations == []
    assert _arguments(prepared.tasks[0])["state"] == "{{ desired_state }}"
    spec = {"path": {"required": True}, "state": {"choices": ["present", "absent"]}}
    spec_field = "options" if rule_id == "L058" else "argument_spec"
    findings = rule._check_tasks_against_specs({"ansible.builtin.file": {spec_field: spec}}, prepared.tasks)
    messages = [str(finding["message"]) for finding in findings]
    assert any("Unsupported parameters" in message and "bogus" in message for message in messages)
    assert any("Missing required" in message for message in messages) is (not provide_required)
    assert not any("not one of" in message for message in messages)


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("inherited", [False, True])  # type: ignore[untyped-decorator]
def test_choice_diagnostics_never_echo_uploaded_values(runtime_venv: Path, rule_id: str, inherited: bool) -> None:
    """Invalid literal choices remain actionable without disclosing their values.

    Args:
        runtime_venv: Installed Ansible runtime for inherited defaults resolution.
        rule_id: Docstring or captured-spec argspec rule.
        inherited: Whether the sensitive value originates in module defaults.
    """
    rule = importlib.import_module(
        "apme_engine.validators.ansible.rules." + ("L058_argspec_doc" if rule_id == "L058" else "L059_argspec_mock")
    )
    sensitive = "uploaded-private-value-never-include-in-diagnostics"
    layers: list[object] = [{"file": {"state": sensitive}}] if inherited else []
    arguments: dict[str, object] = {"path": "/tmp/example"}
    if not inherited:
        arguments["state"] = sensitive
    field = "options" if rule_id == "L058" else "argument_spec"
    specification = {"path": {"required": True}, "state": {"choices": ["present", "absent"]}}
    with (
        patch.object(rule, "plugin_cache", PluginCache()),
        patch.object(rule, "_fetch_specs", return_value={"ansible.builtin.file": {field: specification}}),
    ):
        findings = rule.run([_task(layers, arguments=arguments)], runtime_venv)
    assert len(findings) == 1
    assert findings[0]["rule_id"] == rule_id
    assert "state" in str(findings[0]["message"])
    assert "not one of" in str(findings[0]["message"])
    assert sensitive not in json.dumps(findings)


@pytest.mark.parametrize("location", ["arguments", "defaults"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("shape", ["depth", "nodes", "cycle"])  # type: ignore[untyped-decorator]
def test_literal_resource_limits_isolate_unassessable_tasks(tmp_path: Path, location: str, shape: str) -> None:
    """Deep, wide, or recursive payloads warn without breaking independent tasks.

    Args:
        tmp_path: Missing runtime, unnecessary for literal traversal checks.
        location: Explicit arguments or inherited defaults containing the value.
        shape: Excessive depth, excessive node count, or a recursive container.
    """
    value: object
    if shape == "depth":
        value = "leaf"
        for _ in range(1_500):
            value = [value]
    elif shape == "nodes":
        value = list(range(50_001))
    else:
        recursive: list[object] = []
        recursive.append(recursive)
        value = recursive
    # Budget exhaustion is conservative and must itself avoid recursion.
    assert is_dynamic(value)
    affected = _task([], key="affected")
    if location == "arguments":
        affected["module_options"] = {"value": value}
    else:
        affected["module_defaults_contexts"] = [[{"file": {"value": value}}]]
    independent = _task([], key="independent", arguments={"path": "/tmp/known"})
    with patch("subprocess.run", side_effect=AssertionError("Unbounded values must not reach a subprocess")):
        result = resolve_module_defaults([affected, independent], tmp_path / "missing")
    assert result.tasks == [independent]
    assert len(result.violations) == 1
    finding = result.violations[0]
    assert finding["rule_id"] == "L058"
    assert finding["severity"] == "warning"
    assert finding["path"] == "affected"
    assert finding["remediation_class"] == "manual-review"


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("value", [{"child": "{{ omit }}"}, ["{{ value }}"]])  # type: ignore[untyped-decorator]
def test_nested_templates_do_not_hide_unknown_argument(rule_id: str, value: object) -> None:
    """Nested dynamic values leave their containing unsupported key present.

    Args:
        rule_id: Docstring or captured-spec argspec rule.
        value: Dynamic mapping or list value.
    """
    rule = importlib.import_module(
        "apme_engine.validators.ansible.rules." + ("L058_argspec_doc" if rule_id == "L058" else "L059_argspec_mock")
    )
    field = "options" if rule_id == "L058" else "argument_spec"
    specs: dict[str, object] = {"ansible.builtin.file": {field: {"path": {}}}}
    findings = rule._check_tasks_against_specs(specs, [_task([], arguments={"path": "/tmp/a", "bad": value})])
    assert len(findings) == 1
    assert "Unsupported parameters" in str(findings[0]["message"])


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
def test_nested_template_finding_retains_unassessed_coverage(rule_id: str, tmp_path: Path) -> None:
    """Report definite unsupported keys while marking dynamic values unassessed.

    Args:
        rule_id: Docstring or captured-spec argspec rule.
        tmp_path: Isolated runtime path for the mocked metadata lookup.
    """
    rule = importlib.import_module(
        "apme_engine.validators.ansible.rules." + ("L058_argspec_doc" if rule_id == "L058" else "L059_argspec_mock")
    )
    field = "options" if rule_id == "L058" else "argument_spec"
    task = _task([], arguments={"path": "/tmp/a", "bad": {"child": "{{ omit }}"}})
    unassessed: set[str] = set()
    with (
        patch.object(rule, "plugin_cache", PluginCache()),
        patch.object(rule, "_fetch_specs", return_value={"ansible.builtin.file": {field: {"path": {}}}}),
    ):
        findings = rule.run([task], tmp_path, unassessed_tasks=unassessed)
    assert len(findings) == 1
    assert "Unsupported parameters" in str(findings[0]["message"])
    assert unassessed == {"task#defaults"}


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "case", ["unknown-omit", "exclusive-omit", "together-omit", "literal-unknown", "nested-unknown"]
)
def test_omit_capable_values_do_not_prove_argument_violations(rule_id: str, case: str) -> None:
    """Potentially omitted values cannot prove unsupported or related-key errors.

    Args:
        rule_id: Docstring or captured-spec argspec rule.
        case: Unsupported, exclusive, together, or independently invalid input.
    """
    rule = importlib.import_module(
        "apme_engine.validators.ansible.rules." + ("L058_argspec_doc" if rule_id == "L058" else "L059_argspec_mock")
    )
    parameters: dict[str, object] = {
        "path": {"required": True},
        "src": {},
        "content": {},
        "username": {},
        "password": {},
    }
    arguments: dict[str, object] = {"path": "/tmp/example"}
    constraints: dict[str, object] = {}
    if case == "unknown-omit":
        arguments["unsupported"] = "{{ omit }}"
    elif case == "exclusive-omit":
        arguments.update({"src": "/tmp/source", "content": "{{ omit }}"})
        constraints["mutually_exclusive"] = [["src", "content"]]
    elif case == "together-omit":
        arguments["username"] = "{{ omit }}"
        constraints["required_together"] = [["username", "password"]]
    elif case == "literal-unknown":
        arguments.update({"bogus": "literal", "content": "{{ omit }}"})
    else:
        arguments["bogus"] = {"child": "{{ value }}"}
    field = "options" if rule_id == "L058" else "argument_spec"
    findings = rule._check_tasks_against_specs(
        {"ansible.builtin.file": {field: parameters, **constraints}}, [_task([], arguments=arguments)]
    )
    if case in {"literal-unknown", "nested-unknown"}:
        assert len(findings) == 1
        assert "Unsupported parameters" in str(findings[0]["message"])
        assert "bogus" in str(findings[0]["message"])
    else:
        assert findings == []


@pytest.mark.parametrize("constraint", ["mutually_exclusive", "required_together"])  # type: ignore[untyped-decorator]
def test_nested_template_does_not_omit_outer_argument(constraint: str) -> None:
    """A nested template does not remove its containing argument from a group.

    Args:
        constraint: The argument relationship assessed by L059.
    """
    from apme_engine.validators.ansible.rules import L059_argspec_mock

    options: dict[str, object] = {"path": "/tmp/example", "src": {"child": "{{ value }}"}}
    if constraint == "mutually_exclusive":
        options["content"] = "literal"
        group = ["src", "content"]
    else:
        group = ["src", "content"]
    specs: dict[str, object] = {
        "ansible.builtin.file": {"argument_spec": {"path": {}, "src": {}, "content": {}}, constraint: [group]}
    }
    findings = L059_argspec_mock._check_tasks_against_specs(specs, [_task([], arguments=options)])
    assert len(findings) == 1
    expected = "mutually exclusive" if constraint == "mutually_exclusive" else "used together"
    assert expected in str(findings[0]["message"])


@pytest.mark.parametrize("rule_id", ["L058", "L059"])  # type: ignore[untyped-decorator]
def test_real_argspec_inherited_findings_require_manual_review_for_every_context(
    runtime_venv: Path, rule_id: str
) -> None:
    """A final empty context cannot make inherited task findings AI candidates.

    Args:
        runtime_venv: Installed Ansible runtime used to retrieve real file specs.
        rule_id: Docstring or captured-spec argspec rule.
    """
    rule = importlib.import_module(
        "apme_engine.validators.ansible.rules." + ("L058_argspec_doc" if rule_id == "L058" else "L059_argspec_mock")
    )
    task = _task(
        [{"file": {"defaults_only_typo": "inherited"}}],
        arguments={"path": "/tmp/example", "authored_only_typo": "explicit"},
    )
    task["module_defaults_contexts"] = [
        [{"file": {"defaults_only_typo": "inherited"}}],
        [{"file": {}}],
    ]
    before = copy.deepcopy(task)
    prepared = resolve_module_defaults([task], runtime_venv)
    assert prepared.violations == []
    assert [context["module_defaults_used"] for context in prepared.tasks] == [True, False]
    assert task == before
    independent = _task([], arguments={"path": "/tmp/independent", "independent_typo": "explicit"}, key="independent")
    # Fetch real installed specs; no fake docs or captured argument specs here.
    with patch.object(rule, "plugin_cache", PluginCache()):
        findings = rule.run([task, independent], runtime_venv)
    inherited_findings = [finding for finding in findings if finding["path"] == task["key"]]
    assert len(inherited_findings) >= 2
    assert any(
        "Unsupported parameters" in str(finding["message"]) and "defaults_only_typo" in str(finding["message"])
        for finding in inherited_findings
    )
    assert any(
        "authored_only_typo" in str(finding["message"]) and "defaults_only_typo" not in str(finding["message"])
        for finding in inherited_findings
    )
    assert all(finding["remediation_class"] == "manual-review" for finding in inherited_findings)
    independent_findings = [finding for finding in findings if finding["path"] == "independent"]
    assert independent_findings
    assert all(finding.get("remediation_class") != "manual-review" for finding in independent_findings)


@pytest.mark.parametrize("whole_mapping", [False, True])  # type: ignore[untyped-decorator]
def test_malicious_lookup_is_never_evaluated(runtime_venv: Path, tmp_path: Path, whole_mapping: bool) -> None:
    """Lookup expressions remain inert for both mapping and individual values.

    Args:
        runtime_venv: Installed Ansible runtime.
        tmp_path: Location of the marker an evaluated lookup would create.
        whole_mapping: Whether the expression represents an entire mapping.
    """
    marker = tmp_path / "lookup-executed"
    expression = "{{ lookup('pipe', " + json.dumps("touch " + shlex.quote(str(marker))) + ") }}"
    value: object = expression if whole_mapping else {"owner": expression}
    result = resolve_module_defaults([_task([{"file": value}])], runtime_venv)
    assert not marker.exists()
    if whole_mapping:
        assert result.tasks == []
        assert len(result.violations) == 1
        assert result.violations[0]["severity"] == "warning"
    else:
        assert result.violations == []
        assert [_arguments(task) for task in result.tasks] == [{"owner": expression}]


def test_project_configuration_and_plugin_paths_cannot_override_installed_resolution(
    runtime_venv: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project environment overrides cannot contribute defaults metadata or code.

    Args:
        runtime_venv: Installed synthetic collection runtime.
        tmp_path: Untrusted project containing a config and shadow collection.
        monkeypatch: Changes the parent process cwd and hostile environment.
    """
    project = tmp_path / "project"
    project.mkdir()
    library = project / "library"
    library.mkdir()
    marker = project / "plugin-executed"
    (library / "file.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\nraise RuntimeError('untrusted module')\n",
        encoding="utf-8",
    )
    config = project / "ansible.cfg"
    config.write_text(f"[defaults]\nlibrary = {library}\ncollections_path = {project}\n", encoding="utf-8")
    _write_collection(project, "review", "defaults", "action_groups:\n  first: []\n")
    hostile = {
        "ANSIBLE_CONFIG": str(config),
        "ANSIBLE_LIBRARY": str(library),
        "ANSIBLE_ACTION_PLUGINS": str(library),
        "ANSIBLE_COLLECTIONS_PATH": str(project),
        "ANSIBLE_COLLECTIONS_PATHS": str(project),
        "ANSIBLE_COLLECTIONS_SCAN_SYS_PATH": "False",
        "PYTHONPATH": str(project),
        "HOME": str(project),
        "ANSIBLE_HOME": str(project),
    }
    monkeypatch.chdir(project)
    for name, value in hostile.items():
        monkeypatch.setenv(name, value)
    result = resolve_module_defaults(
        [_task([{"group/review.defaults.first": {"region": "installed"}}], module="review.defaults.sample")],
        runtime_venv,
        env_extra=hostile,
    )
    assert result.violations == []
    assert [_arguments(task) for task in result.tasks] == [{"region": "installed"}]
    assert not marker.exists()


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "failure", ["timeout", "invalid-json", "nonzero", "short-response"]
)
def test_runtime_failure_produces_explicit_coverage_warning(runtime_venv: Path, failure: str) -> None:
    """Resolver failure cannot produce apparently clean prepared arguments.

    Args:
        runtime_venv: Existing runtime executable, bypassed by the mock.
        failure: Representative timeout, invalid response, or process failure.
    """
    process = subprocess.CompletedProcess(["python"], 1 if failure == "nonzero" else 0, "[]", "")
    if failure == "invalid-json":
        process.stdout = "not JSON"
    with patch("subprocess.run") as execute:
        if failure == "timeout":
            execute.side_effect = subprocess.TimeoutExpired("python", 30)
        else:
            execute.return_value = process
        result = resolve_module_defaults([_task([{"file": {"owner": "root"}}])], runtime_venv)
    assert result.tasks == []
    assert len(result.violations) == 1
    assert result.violations[0]["rule_id"] == "L058"
    assert result.violations[0]["severity"] == "warning"
    assert result.violations[0]["remediation_class"] == "manual-review"
