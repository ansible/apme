"""Behavioral coverage for automatic Molecule L115-L119 validation."""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from ansible.errors import AnsibleParserError
from ansible.playbook.play import Play

from apme.v1 import common_pb2, validate_pb2
from apme_engine.daemon.native_validator_server import _run_native
from apme_engine.graph.types import ViolationDict
from apme_engine.molecule import MOLECULE_RULES, validate_molecule

CONFIG = "molecule/default/molecule.yml"
PLAYBOOK = b"- hosts: all\n  tasks: []\n"


def _aliased_invalid_plays(count: int, text: str, *, noqa: bool = False) -> bytes:
    """Create repeated diagnostic paths sharing one full source location.

    Args:
        count: Number of plays referencing the same anchored mapping.
        text: Literal scalar content of an invalid tasks section.
        noqa: Whether the diagnostic line includes an inline suppression.

    Returns:
        Unexpanded playbook bytes containing one source excerpt.
    """
    marker = " # noqa: L119" if noqa else ""
    return (f"- &bad\n  hosts: all\n  tasks: |{marker}\n    {text}\n" + "- *bad\n" * (count - 1)).encode()


def test_aliased_findings_reuse_full_excerpts_and_preserve_suppression() -> None:
    """Aliases retain distinct paths while sharing untruncated source strings."""
    from apme_engine.daemon.engine_server import _filter_file_noqa
    from apme_engine.fingerprint import compute_fingerprint
    from apme_engine.molecule import analysis as molecule

    text = "x" * (128 * 1024) + "full-excerpt-tail"
    source = _aliased_invalid_plays(12, text, noqa=True)
    with patch.object(molecule, "_source_excerpt", wraps=molecule._source_excerpt) as excerpt:
        findings = _scan("", {"molecule/default/converge.yml": source})
    assert len(findings) == 12
    assert len({finding["path"] for finding in findings}) == 12
    assert {finding["line"] for finding in findings} == {3}
    assert excerpt.call_count == 1
    original = findings[0]["original_yaml"]
    expected = f"  tasks: | # noqa: L119\n    {text}"
    assert original == expected
    assert all(finding["original_yaml"] is original for finding in findings)
    assert compute_fingerprint("L119", str(original)) == compute_fingerprint("L119", expected)
    assert _filter_file_noqa(findings) == []


def test_excerpt_cache_keeps_different_source_files_distinct() -> None:
    """Equal line numbers in separate source files must not share excerpts."""
    from apme_engine.molecule import analysis as molecule

    with patch.object(molecule, "_source_excerpt", wraps=molecule._source_excerpt) as excerpt:
        findings = _scan(
            "",
            {
                "molecule/default/converge.yml": (
                    b"- import_playbook: ../../playbooks/first.yml\n- import_playbook: ../../playbooks/second.yml\n"
                ),
                "playbooks/first.yml": _aliased_invalid_plays(2, "first"),
                "playbooks/second.yml": _aliased_invalid_plays(2, "second"),
            },
        )
    assert len(findings) == 4
    assert excerpt.call_count == 2
    originals = {str(finding["file"]): finding["original_yaml"] for finding in findings}
    assert originals["playbooks/first.yml"] != originals["playbooks/second.yml"]


def test_aliased_excerpts_cannot_expand_into_an_oversized_response() -> None:
    """The reported sub-MiB upload fails before allocating GiB of proto strings."""
    from apme_engine.molecule import analysis as molecule

    source = _aliased_invalid_plays(4000, "private-source-value-" + "x" * (512 * 1024))
    assert len(source) < 1024 * 1024
    with (
        patch.object(molecule, "_source_excerpt", wraps=molecule._source_excerpt) as excerpt,
        pytest.raises(molecule.MoleculeAssessmentLimitError, match="assessment is incomplete") as error,
    ):
        _scan("", {"molecule/default/converge.yml": source})
    assert excerpt.call_count == 1
    assert "private-source-value" not in str(error.value)


@pytest.mark.parametrize("text", ["ASCII", 'é🐍\n"\\'])  # type: ignore[untyped-decorator]
def test_diagnostic_size_accounts_for_unicode_and_json_escaping(text: str) -> None:
    """Both JSON and protobuf representation sizes fit within the allowance.

    Args:
        text: ASCII or Unicode text including JSON escape characters.
    """
    import json

    from apme_engine.molecule import analysis as molecule

    assert molecule._diagnostic_string_size(text) == len(json.dumps(text))
    assert molecule._diagnostic_string_size(text) >= len(text.encode())


async def test_native_rpc_rejects_expanded_excerpts_before_proto_conversion() -> None:
    """Oversized file diagnostics produce a bounded explicit infrastructure error."""
    from apme_engine.daemon import native_validator_server
    from apme_engine.daemon.validator_errors import RULE_VALIDATOR_FAILURE
    from apme_engine.molecule import analysis as molecule

    source = _aliased_invalid_plays(30, "private-source-value-" + "x" * 1024)
    request = validate_pb2.ValidateRequest(
        request_id="excerpt-expansion",
        files=[
            common_pb2.File(path=CONFIG, content=b""),
            common_pb2.File(path="molecule/default/converge.yml", content=source),
        ],
    )
    with (
        patch.object(molecule, "_MAX_DIAGNOSTIC_BYTES", 8192),
        patch.object(native_validator_server, "violation_dict_to_proto", side_effect=AssertionError) as convert,
    ):
        response = await native_validator_server.NativeValidatorServicer().Validate(request, None)  # type: ignore[arg-type]
    convert.assert_not_called()
    assert response.request_id == "excerpt-expansion"  # type: ignore[attr-defined]
    assert len(response.violations) == 1  # type: ignore[attr-defined]
    assert response.violations[0].rule_id == RULE_VALIDATOR_FAILURE  # type: ignore[attr-defined]
    assert response.violations[0].severity == common_pb2.SEVERITY_ERROR  # type: ignore[attr-defined]
    assert response.violations[0].original_yaml == ""  # type: ignore[attr-defined]
    assert response.ByteSize() < 8192  # type: ignore[attr-defined]
    assert "private-source-value" not in str(response)


def test_shared_excerpts_preserve_complete_protobuf_strings() -> None:
    """Accepted aliases retain full suppression source in every wire finding."""
    from apme_engine.daemon.violation_convert import violation_dict_to_proto

    text = "é🐍" * 2000 + "full-excerpt-tail"
    findings = _scan("", {"molecule/default/converge.yml": _aliased_invalid_plays(4, text)})
    response = validate_pb2.ValidateResponse(violations=[violation_dict_to_proto(finding) for finding in findings])
    restored = validate_pb2.ValidateResponse.FromString(response.SerializeToString())  # type: ignore[attr-defined]
    assert len(restored.violations) == 4
    assert all(finding.original_yaml == findings[0]["original_yaml"] for finding in restored.violations)
    assert all(text in finding.original_yaml for finding in restored.violations)


@pytest.mark.parametrize("section", ["pre_tasks", "tasks", "post_tasks", "handlers"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("value", ["{}", "wrong", "42", "false", "''"])  # type: ignore[untyped-decorator]
def test_l119_rejects_nonlist_play_task_sections(section: str, value: str) -> None:
    """Shared imported playbooks cannot hide malformed task sections.

    Args:
        section: Play-level task section rejected by Ansible when not a list.
        value: Invalid mapping, scalar, or empty string section value.
    """
    source = f"- hosts: all\n  {section}: {value}\n".encode()
    with pytest.raises(AnsibleParserError):
        Play.load(yaml.safe_load(source)[0])
    findings = _scan(
        "",
        {
            "molecule/default/converge.yml": b"- import_playbook: ../../playbooks/shared.yml\n",
            "playbooks/shared.yml": source,
        },
    )
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L119"
    assert findings[0]["file"] == "playbooks/shared.yml"
    assert findings[0]["line"] == 2
    assert findings[0]["path"] == f"$[0].{section}"
    assert "must be a list" in str(findings[0]["message"])


@pytest.mark.parametrize("section", ["pre_tasks", "tasks", "post_tasks", "handlers"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("value", ["", "null", "~", "[]"])  # type: ignore[untyped-decorator]
def test_l119_accepts_ansible_empty_play_task_sections(section: str, value: str) -> None:
    """Null and empty-list play sections remain compatible with Ansible.

    Args:
        section: Play section accepting a list or YAML null.
        value: Empty section spelling accepted by the installed Ansible loader.
    """
    source = f"- hosts: all\n  {section}: {value}\n".encode()
    Play.load(yaml.safe_load(source)[0])
    assert _scan("", {"molecule/default/converge.yml": source}) == []


@pytest.mark.parametrize("section", ["block", "rescue", "always"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("value", ["{}", "wrong", "42", "false", "''", "", "null", "~"])  # type: ignore[untyped-decorator]
def test_l119_rejects_nonlist_nested_task_sections(section: str, value: str) -> None:
    """Nested block sections require lists, including when a value is empty.

    Args:
        section: Nested task section checked within a block.
        value: Non-list section value, including YAML null.
    """
    if section == "block":
        source = f"- hosts: all\n  tasks:\n    - block: {value}\n".encode()
        line = 3
    else:
        source = f"- hosts: all\n  tasks:\n    - block: []\n      {section}: {value}\n".encode()
        line = 4
    with pytest.raises(AnsibleParserError):
        Play.load(yaml.safe_load(source)[0])
    findings = _scan("", {"molecule/default/converge.yml": source})
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L119"
    assert findings[0]["line"] == line
    assert findings[0]["path"] == f"$[0].tasks[0].{section}"


@pytest.mark.parametrize("section", ["block", "rescue", "always"])  # type: ignore[untyped-decorator]
def test_l119_accepts_empty_nested_task_lists(section: str) -> None:
    """Empty nested lists retain Ansible's valid no-task behavior.

    Args:
        section: Block section with a valid empty list.
    """
    extra = f"      {section}: []\n" if section != "block" else ""
    source = ("- hosts: all\n  tasks:\n    - block: []\n" + extra).encode()
    Play.load(yaml.safe_load(source)[0])
    assert _scan("", {"molecule/default/converge.yml": source}) == []


def test_l119_reports_deep_sections_without_inspecting_task_data() -> None:
    """Nested execution scopes are checked while task and role data stay inert."""
    source = b"""- hosts: all
  vars:
    tasks: {}
    block: null
  roles:
    - role: demo
      tasks: {}
  tasks:
    - ansible.builtin.debug:
        msg:
          tasks: {}
          rescue: null
    - block: []
      rescue:
        - block: {}
      always:
        - block: []
          always: false
"""
    findings = _scan("", {"molecule/default/converge.yml": source})
    assert {(finding["path"], finding["line"]) for finding in findings} == {
        ("$[0].tasks[1].rescue[0].block", 15),
        ("$[0].tasks[1].always[0].always", 18),
    }
    assert all(finding["rule_id"] == "L119" for finding in findings)


def test_l119_checks_yaml_merged_task_sections_at_their_source() -> None:
    """Merged task sections keep the originating key's line number."""
    source = b"""- hosts: all
  vars:
    inherited: &sections
      tasks: {}
  <<: *sections
"""
    findings = _scan("", {"molecule/default/converge.yml": source})
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L119"
    assert findings[0]["path"] == "$[0].tasks"
    assert findings[0]["line"] == 4


@pytest.mark.parametrize("action", ["import_playbook", "ansible.builtin.import_playbook"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("target", ["[]", "{}", "null", "true", "42", "''"])  # type: ignore[untyped-decorator]
def test_l119_rejects_invalid_import_targets(action: str, target: str) -> None:
    """Malformed imports cannot pass static checks by supplying only the key.

    Args:
        action: Import-playbook spelling.
        target: Literal invalid import path.
    """
    source = f"- name: Import\n  {action}: {target}\n".encode()
    findings = _scan("", {"molecule/default/converge.yml": source})
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L119"
    assert findings[0]["line"] == 2
    assert findings[0]["path"] == f"$[0].{action}"


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "defaults",
    ["[]", "{}", "null", "{file: {}}", "{file: '{{ file_args }}'}", "[{file: {mode: '0644'}}]"],
)
def test_l119_accepts_module_defaults_shapes(defaults: str) -> None:
    """Defaults mappings, ordered entries, empty resets, and templates are data.

    Args:
        defaults: Valid literal defaults declaration.
    """
    playbook = f"- hosts: all\n  module_defaults: {defaults}\n  tasks: []\n".encode()
    assert _scan("", {"molecule/default/converge.yml": playbook}) == []


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "defaults", ["42", "'{{ defaults }}'", "[null]", "{file: []}", "{'{{ module }}': {}}", "{file: {42: value}}"]
)
def test_l119_rejects_malformed_module_defaults(defaults: str) -> None:
    """Native checks provable defaults errors without loading Ansible plugins.

    Args:
        defaults: Invalid literal defaults declaration.
    """
    playbook = f"- hosts: all\n  module_defaults: {defaults}\n  tasks: []\n".encode()
    findings = _scan("", {"molecule/default/converge.yml": playbook})
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L119"
    assert findings[0]["line"] == 2
    assert findings[0]["path"] == "$[0].module_defaults"


def test_l119_defaults_in_block_rescue_and_include_apply() -> None:
    """Inspect execution scopes while ignoring similarly named variable data."""
    playbook = b"""- hosts: all
  vars:
    module_defaults: variable_data
  tasks:
    - block:
        - ansible.builtin.debug: {}
          module_defaults: bad_task
      rescue:
        - ansible.builtin.debug: {}
          module_defaults: bad_rescue
      always:
        - ansible.builtin.debug: {}
          module_defaults: bad_always
      module_defaults: bad_block
    - ansible.builtin.include_role:
        name: example
        apply:
          module_defaults: bad_apply
"""
    findings = _scan("", {"molecule/default/converge.yml": playbook})
    assert {finding["path"] for finding in findings} == {
        "$[0].tasks[0].module_defaults",
        "$[0].tasks[0].block[0].module_defaults",
        "$[0].tasks[0].rescue[0].module_defaults",
        "$[0].tasks[0].always[0].module_defaults",
        "$[0].tasks[1].ansible.builtin.include_role.apply.module_defaults",
    }
    assert all(finding["rule_id"] == "L119" for finding in findings)


def test_l119_defaults_allow_yaml_merges_and_inert_ansible_tags() -> None:
    """Resolve structural YAML merges without evaluating encrypted values."""
    playbook = b"""- hosts: all
  vars:
    defaults: &defaults
      ansible.builtin.uri:
        password: !vault |
          $ANSIBLE_VAULT;1.1;AES256
          inert
  module_defaults:
    <<: *defaults
  tasks: []
"""
    assert _scan("", {"molecule/default/converge.yml": playbook}) == []


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "action",
    [
        "ansible.builtin.include_tasks: inner.yml",
        "include_role: demo",
        "action: include_tasks inner.yml",
        "local_action: include_role name=demo",
    ],
)
def test_l119_checks_defaults_from_task_args_apply(action: str) -> None:
    """Free-form include args retain the true source of invalid applied defaults.

    Args:
        action: Include task action syntax.
    """
    source = (
        f"- hosts: all\n  tasks:\n    - {action}\n      args:\n        apply:\n"
        "          module_defaults:\n            ansible.builtin.file: []\n"
    ).encode()
    findings = _scan("", {"molecule/default/converge.yml": source})
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L119"
    assert findings[0]["path"] == "$[0].tasks[0].args.apply.module_defaults"
    assert findings[0]["line"] == 6


def test_l119_action_mapping_apply_overrides_task_args_apply() -> None:
    """Task-level args cannot override a module-owned apply mapping."""
    source = b"""- hosts: all
  tasks:
    - ansible.builtin.include_tasks:
        file: inner.yml
        apply:
          module_defaults:
            ansible.builtin.file: {}
      args:
        apply:
          module_defaults:
            ansible.builtin.file: []
"""
    assert _scan("", {"molecule/default/converge.yml": source}) == []


def test_l119_role_declarations_check_defaults_without_parsing_role_parameters_as_tasks() -> None:
    """Roles inherit defaults but their other parameters remain ordinary data."""
    source = b"""- hosts: all
  roles:
    - role: demo
      module_defaults:
        file: []
      tasks:
        - module_defaults: role_parameter_data
"""
    findings = _scan("", {"molecule/default/converge.yml": source})
    assert len(findings) == 1
    assert findings[0]["path"] == "$[0].roles[0].module_defaults"
    assert findings[0]["line"] == 4


@pytest.mark.parametrize("form", ["action", "local_action"])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize("nested_wins", [False, True])  # type: ignore[untyped-decorator]
def test_l119_nested_action_args_apply_has_ansible_precedence(form: str, nested_wins: bool) -> None:
    """Old-style action.args overrides direct action fields and task-level args.

    Args:
        form: Supported old-style action keyword.
        nested_wins: Whether the invalid declaration comes from nested action args.
    """
    direct = "{}" if nested_wins else "[]"
    nested = "[]" if nested_wins else "{}"
    source = (
        f"- hosts: all\n  tasks:\n    - {form}:\n        module: include_tasks\n"
        f"        apply:\n          module_defaults:\n            file: {direct}\n"
        "        args:\n          file: inner.yml\n          apply:\n"
        f"            module_defaults:\n              file: {nested}\n"
        "      args:\n        apply:\n          module_defaults:\n            file: []\n"
    ).encode()
    findings = _scan("", {"molecule/default/converge.yml": source})
    if nested_wins:
        assert len(findings) == 1
        assert findings[0]["path"] == f"$[0].tasks[0].{form}.args.apply.module_defaults"
        assert findings[0]["line"] == 11
    else:
        assert findings == []


def _scan(source: str, extra: dict[str, bytes] | None = None) -> list[ViolationDict]:
    """Assess one scenario with an optional snapshot.

    Args:
        source: Scenario config source.
        extra: Other supplied files.

    Returns:
        Native Molecule findings.
    """
    return validate_molecule({CONFIG: source.encode(), **(extra or {})})


def test_l115_accepts_empty_scenario_using_profile_defaults() -> None:
    """Molecule defaults make an empty scenario a valid config fragment."""
    assert _scan("") == []


def test_l115_reports_duplicate_keys_at_the_source_line() -> None:
    """Duplicate mapping keys cannot silently override a scenario setting."""
    findings = _scan("platforms: []\nplatforms: []\n")
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L115"
    assert findings[0]["line"] == 2


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "source", ["- not-a-config\n", "provisioner: [\n", "x: &cycle\n  child: *cycle\n"]
)
def test_l115_handles_malformed_and_recursive_configuration(source: str) -> None:
    """Malformed scenarios produce config findings without crashing validation.

    Args:
        source: Invalid or recursive config.
    """
    assert any(item["rule_id"] == "L115" for item in _scan(source))


def test_l115_attributes_inherited_error_to_shared_config() -> None:
    """Errors in inherited values retain their shared-file location."""
    shared = ".config/molecule/config.yml"
    findings = _scan("", {shared: b"provisioner:\n  name: unsupported\n"})
    schema = [item for item in findings if item["rule_id"] == "L115"]
    assert len(schema) == 1
    assert schema[0]["file"] == shared
    assert schema[0]["line"] == 2


def test_l115_scenario_override_replaces_inherited_error() -> None:
    """A supported scenario override replaces a conflicting inherited scalar."""
    assert (
        _scan("provisioner:\n  name: ansible\n", {".config/molecule/config.yml": b"provisioner:\n  name: bad\n"}) == []
    )


def test_l115_accepts_native_example_from_ansible_lint_issue_5119() -> None:
    """The original native example passes schema, reference, and sequence checks."""
    source = (
        "dependency:\n  name: galaxy\n"
        "ansible:\n  executor:\n    backend: ansible-playbook\n"
        "  playbooks:\n    converge: converge.yml\n"
        "scenario:\n  name: default\n  test_sequence:\n    - converge\n"
    )
    assert _scan(source, {"molecule/default/converge.yml": PLAYBOOK}) == []


def test_l115_one_bad_scenario_does_not_prevent_others() -> None:
    """Independent scenarios are assessed even when another YAML document fails."""
    findings = _scan("x: [", {"molecule/second/molecule.yml": b"provisioner:\n  name: bad\n"})
    assert {str(item["file"]) for item in findings} == {CONFIG, "molecule/second/molecule.yml"}


def test_l115_non_string_structural_keys_do_not_abort_validation() -> None:
    """Non-string schema keys produce config diagnostics instead of RPC failure."""
    findings = _scan("scenario:\n  42: []\n", {"molecule/second/molecule.yml": b""})
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L115"


def test_l116_reports_missing_explicit_reference_without_asserting_runtime_failure() -> None:
    """An absent snapshot reference might be missing or deliberately excluded."""
    findings = _scan("provisioner:\n  playbooks:\n    converge: missing.yml\n")
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L116"
    assert findings[0]["line"] == 3
    assert "supplied scan" in str(findings[0]["message"])


def test_l116_allows_sibling_playbooks_and_optional_defaults() -> None:
    """Shared local playbooks resolve relative to the scenario directory."""
    assert (
        _scan("provisioner:\n  playbooks:\n    converge: ../shared/site.yml\n", {"molecule/shared/site.yml": PLAYBOOK})
        == []
    )


def test_l116_resolves_known_molecule_path_variables() -> None:
    """Static Molecule scenario variables refer to the uploaded snapshot."""
    assert (
        _scan(
            "provisioner:\n  playbooks:\n    converge: ${MOLECULE_SCENARIO_DIRECTORY}/converge.yml\n",
            {"molecule/default/converge.yml": PLAYBOOK},
        )
        == []
    )


def test_l116_uses_converge_filename_fallback_from_supported_profile() -> None:
    """Molecule 24.9 accepts playbook.yml as a converge.yml fallback."""
    assert (
        _scan("provisioner:\n  playbooks:\n    converge: converge.yml\n", {"molecule/default/playbook.yml": PLAYBOOK})
        == []
    )


def test_l116_accepts_inventory_directory_links() -> None:
    """An inventory link may target a directory represented by snapshot children."""
    assert (
        _scan(
            "provisioner:\n  inventory:\n    links:\n      group_vars: ../group_vars\n",
            {"molecule/group_vars/all.yml": b"value: 1\n"},
        )
        == []
    )


def test_l116_resolves_galaxy_requirements_relative_to_project() -> None:
    """Galaxy requirement paths use the project root, unlike stage playbooks."""
    source = "dependency:\n  name: galaxy\n  options:\n    requirements-file: requirements.yml\n"
    assert _scan(source, {"requirements.yml": b"collections: []\n"}) == []
    assert any(item["rule_id"] == "L116" for item in _scan(source))


def test_l116_does_not_require_disabled_dependency_inputs() -> None:
    """Disabled dependency management does not consume its configured files."""
    assert _scan("dependency:\n  name: galaxy\n  enabled: false\n  options:\n    requirements-file: absent.yml\n") == []


def test_l117_detects_action_typos_beyond_permissive_schema() -> None:
    """The schema permits arbitrary strings, so action names need semantic checks."""
    findings = _scan("scenario:\n  test_sequence:\n    - convergge\n")
    assert any(item["rule_id"] == "L117" and item["line"] == 3 for item in findings)


def test_l117_accepts_repeated_actions_and_explicit_verify_playbooks() -> None:
    """Repeated verification and supported action arguments are valid."""
    assert (
        _scan(
            "scenario:\n  test_sequence:\n    - converge\n    - verify check.yml\n    - verify check.yml\n",
            {"molecule/default/check.yml": PLAYBOOK},
        )
        == []
    )


def test_l117_checks_explicit_action_argument_references() -> None:
    """Explicit verify and side-effect paths are checked even without playbook config."""
    findings = _scan("scenario:\n  test_sequence:\n    - side_effect missing.yml\n")
    assert any(item["rule_id"] == "L116" for item in findings)


def test_l118_unknown_context_does_not_become_missing_reference() -> None:
    """Unresolved values are incomplete assessment rather than definite bad paths."""
    findings = _scan("provisioner:\n  playbooks:\n    converge: ${EXTERNAL_PLAYBOOK}\n")
    assert findings
    assert {item["rule_id"] for item in findings} == {"L118"}
    assert len(findings) == 1


def test_l118_unknown_root_or_key_is_not_definitively_invalid() -> None:
    """Whole documents and mapping keys can depend on unavailable variables."""
    for source in ("${CONFIG}", "${CONFIG_KEY}: value\n"):
        findings = _scan(source)
        assert {item["rule_id"] for item in findings} == {"L118"}


def test_l115_extreme_nesting_does_not_abort_independent_scenarios() -> None:
    """A parser recursion limit becomes a config finding rather than RPC failure."""
    findings = _scan("x: " + "[" * 1500 + "]" * 1500, {"molecule/good/molecule.yml": b""})
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L115"


def test_l118_does_not_use_daemon_environment() -> None:
    """Local environment secrets must not influence remote snapshot validation."""
    source = "provisioner:\n  playbooks:\n    converge: ${EXTERNAL_PLAYBOOK}\n"
    before = _scan(source)
    with patch.dict("os.environ", {"EXTERNAL_PLAYBOOK": "daemon-secret-value"}):
        after = _scan(source)
    assert before == after
    assert "daemon-secret-value" not in str(after)


def test_l118_treats_external_paths_as_unavailable_context(tmp_path: Path) -> None:
    """A real host file outside the snapshot is never used to satisfy references.

    Args:
        tmp_path: Directory outside the uploaded file mapping.
    """
    host_file = tmp_path / "host.yml"
    host_file.write_bytes(PLAYBOOK)
    findings = _scan(f"provisioner:\n  playbooks:\n    converge: {host_file}\n")
    assert {item["rule_id"] for item in findings} == {"L118"}


def test_l118_uses_supplied_environment_file_without_exposing_values() -> None:
    """Known environment-file values can resolve references reproducibly."""
    source = "provisioner:\n  playbooks:\n    converge: ${PLAYBOOK}\n"
    assert _scan(source, {".env.yml": b"PLAYBOOK: converge.yml\n", "molecule/default/converge.yml": PLAYBOOK}) == []


def test_l118_honors_default_for_known_empty_environment_value() -> None:
    """A supplied empty value uses the shell-style nonempty default."""
    assert (
        _scan(
            "provisioner:\n  playbooks:\n    converge: ${PLAYBOOK:-converge.yml}\n",
            {".env.yml": b"PLAYBOOK: ''\n", "molecule/default/converge.yml": PLAYBOOK},
        )
        == []
    )


@pytest.mark.parametrize("operator", [":-", "-"])  # type: ignore[untyped-decorator]
def test_environment_default_without_supplied_value(operator: str) -> None:
    """Static defaults resolve when the snapshot supplies no variable value.

    Args:
        operator: Molecule's supported default substitution operator.
    """
    assert (
        _scan(
            f"provisioner:\n  playbooks:\n    converge: ${{PLAYBOOK{operator}converge.yml}}\n",
            {"molecule/default/converge.yml": PLAYBOOK},
        )
        == []
    )


def test_expanded_recursive_yaml_does_not_abort_independent_scenarios() -> None:
    """Environment substitution cannot bypass the YAML alias guard."""
    findings = _scan(
        "x: ${INJECT}\n",
        {
            ".env.yml": b"INJECT: '&loop [*loop]'\n",
            "molecule/good/molecule.yml": b"scenario:\n  test_sequence: [invalid]\n",
        },
    )
    assert {item["rule_id"] for item in findings} == {"L115", "L117"}


def test_l118_preserves_escaped_dollar_in_literal_filename() -> None:
    """Escaped dollars remain literal rather than becoming environment inputs."""
    assert (
        _scan(
            "provisioner:\n  playbooks:\n    converge: $$PLAYBOOK.yml\n",
            {"molecule/default/$PLAYBOOK.yml": PLAYBOOK},
        )
        == []
    )


def test_l115_does_not_expose_interpolated_secret_keys() -> None:
    """Schema diagnostics cannot echo secrets interpolated into mapping keys."""
    findings = _scan("${SECRET}: value\n", {".env.yml": b"SECRET: very-private-value\n"})
    assert any(item["rule_id"] == "L115" for item in findings)
    assert "very-private-value" not in str(findings)


def test_l116_ignores_inactive_driver_overrides() -> None:
    """An unused driver's playbook mapping does not determine active references."""
    assert (
        _scan(
            "provisioner:\n  playbooks:\n    docker:\n      converge: absent.yml\n",
        )
        == []
    )


def test_l118_does_not_parse_testinfra_python_as_ansible_yaml() -> None:
    """Verifier-specific arguments do not become Ansible structural errors."""
    findings = _scan(
        "verifier:\n  name: testinfra\nscenario:\n  test_sequence:\n    - verify tests/\n",
        {"molecule/default/tests/test_service.py": b"def test_service():\n    assert True\n"},
    )
    assert findings
    assert {item["rule_id"] for item in findings} == {"L118"}


def test_l118_reports_external_driver_coverage_limit() -> None:
    """Core schema success does not imply plugin-specific validation or support."""
    findings = _scan("driver:\n  name: podman\n")
    assert any(item["rule_id"] == "L118" and "driver-specific" in str(item["message"]) for item in findings)


def test_l119_reports_referenced_playbook_yaml_errors() -> None:
    """Malformed referenced playbooks receive file-specific diagnostics."""
    findings = _scan(
        "provisioner:\n  playbooks:\n    converge: converge.yml\n",
        {"molecule/default/converge.yml": b"- hosts: all\n  tasks: [\n"},
    )
    assert any(item["rule_id"] == "L119" and item["file"] == "molecule/default/converge.yml" for item in findings)


def test_l119_reports_task_lists_used_as_playbooks() -> None:
    """A referenced task list requires a wrapping playbook."""
    findings = _scan(
        "provisioner:\n  playbooks:\n    converge: converge.yml\n",
        {"molecule/default/converge.yml": b"- name: Task\n  ansible.builtin.debug:\n    msg: test\n"},
    )
    assert any(item["rule_id"] == "L119" for item in findings)


def test_l119_accepts_import_only_playbooks_without_evaluating_jinja() -> None:
    """An import-only scenario is structurally valid without rendering its target."""
    assert (
        _scan(
            "provisioner:\n  playbooks:\n    converge: converge.yml\n",
            {
                "molecule/default/converge.yml": (
                    b'- ansible.builtin.import_playbook: "{{ molecule_project_directory }}/site.yml"\n'
                ),
                "site.yml": PLAYBOOK,
            },
        )
        == []
    )


def test_l115_l119_work_without_graph_or_molecule_execution() -> None:
    """The Native transport runs config checks even when graph data is absent."""
    request = validate_pb2.ValidateRequest(files=[common_pb2.File(path=CONFIG, content=b"provisioner: [\n")])
    with patch("subprocess.run", side_effect=AssertionError("No project commands may run")):
        result = _run_native(request)
    assert len(result.violations) == 1
    assert result.violations[0]["rule_id"] == "L115"
    assert result.violations[0]["scope"] == "collection"


def test_l115_l119_metadata_and_catalog() -> None:
    """Automatic file rules have stable IDs, Molecule tags, and manual scope."""
    from apme_engine.rule_catalog import collect_all_rules, get_rule_documentation

    catalog = {rule.rule_id: rule for rule in collect_all_rules()}
    for rule in MOLECULE_RULES:
        assert "molecule" in rule.tags
        assert rule.scope == "collection"
        assert catalog[rule.rule_id].enabled
        assert catalog[rule.rule_id].category == "lint"
        assert get_rule_documentation(rule.rule_id)


async def test_l115_native_rpc_checks_files_without_graph() -> None:
    """Real RPC adaptation returns file diagnostics without requiring a graph."""
    from apme_engine.daemon.native_validator_server import NativeValidatorServicer

    request = validate_pb2.ValidateRequest(
        request_id="molecule-config-only",
        files=[common_pb2.File(path=CONFIG, content=b"provisioner: [\n")],
    )
    response = await NativeValidatorServicer().Validate(request, None)  # type: ignore[arg-type]
    assert response.request_id == "molecule-config-only"  # type: ignore[attr-defined]
    assert response.diagnostics.files_received == 1  # type: ignore[attr-defined]
    assert response.diagnostics.violations_found == 1  # type: ignore[attr-defined]
    assert response.violations[0].rule_id == "L115"  # type: ignore[attr-defined]


def test_l115_l119_use_existing_rule_overrides() -> None:
    """Rules can be disabled or assigned severity using ordinary config."""
    from apme.v1.engine_pb2 import RuleConfig
    from apme_engine.daemon.engine_server import _apply_rule_configs

    findings = _scan("provisioner: [\n")
    assert _apply_rule_configs(findings, [RuleConfig(rule_id="L115", enabled=False)]) == []


def test_l115_l119_survive_approval_reconciliation() -> None:
    """Approval reconciliation retains config findings outside the graph ledger."""
    from apme.v1.engine_pb2 import FixReport
    from apme_engine.daemon.engine_server import _reconcile_after_approval
    from apme_engine.daemon.session import SessionState
    from apme_engine.graph.content_graph import ContentGraph

    session = SessionState(session_id="molecule-preserve")
    session.file_violations = _scan("provisioner: [\n")
    session.report = FixReport(passes=1)
    _reconcile_after_approval(session, ContentGraph(), set())
    assert len(session.remaining_manual) == 1
    assert session.remaining_manual[0]["rule_id"] == "L115"
    assert session.report.remaining_manual == 1
    session.reset_partial_run_state()
    assert session.file_violations == []


def test_l115_malformed_configuration_can_pass_the_project_loading_stage(tmp_path: Path) -> None:
    """Malformed config does not need a successful playbook graph to reach Native.

    Args:
        tmp_path: Isolated project and loader data directory.
    """
    from apme_engine.runner import run_scan

    config = tmp_path / CONFIG
    config.parent.mkdir(parents=True)
    config.write_text("provisioner: [\n")
    run_scan(str(tmp_path), str(tmp_path / ".apme-data"))
    result = _run_native(
        validate_pb2.ValidateRequest(files=[common_pb2.File(path=CONFIG, content=config.read_bytes())])
    )
    assert any(item["rule_id"] == "L115" for item in result.violations)


async def test_l115_preserved_in_full_assessment_result_with_empty_graph(tmp_path: Path) -> None:
    """File-only findings survive the real graph assessment/reporting workflow.

    Args:
        tmp_path: Isolated project directory.
    """
    import asyncio
    from types import SimpleNamespace

    from apme_engine.daemon.engine_server import EngineServicer
    from apme_engine.daemon.session import SessionState
    from apme_engine.graph.content_graph import ContentGraph
    from apme_engine.remediation.registry import TransformRegistry

    config = tmp_path / CONFIG
    config.parent.mkdir(parents=True)
    config.write_text("provisioner: [\n")
    session = SessionState(session_id="molecule-assessment")
    session.working_files = {CONFIG: config.read_bytes()}
    session.original_files = dict(session.working_files)

    async def scan(paths: list[str]) -> list[ViolationDict]:
        """Return actual config findings for the supplied working snapshot.

        Args:
            paths: Requested assessment paths.

        Returns:
            File-based findings from the Native checker.
        """
        assert paths == [str(config)]
        return validate_molecule(session.working_files)

    async def heartbeat() -> None:
        """Provide a no-op heartbeat for the synchronous test assessment."""

    servicer = EngineServicer.__new__(EngineServicer)
    events = [
        event
        async for event in servicer._session_graph_remediate(
            session=session,
            scan_id="molecule-assessment",
            registry=TransformRegistry(),
            scan_fn=scan,
            captured_graph=[ContentGraph()],
            yaml_paths=[str(config)],
            temp_dir=tmp_path,
            max_passes=1,
            progress_queue=asyncio.Queue(),
            progress_callback=lambda *args: None,
            _heartbeat=heartbeat,
            format_content=lambda *args, **kwargs: SimpleNamespace(changed=False),
            format_diffs=[],
        )
    ]
    assert any(event.WhichOneof("event") == "result" for event in events)
    assert len(session.remaining_manual) == 1
    assert session.remaining_manual[0]["rule_id"] == "L115"
    assert session.report is not None
    assert session.report.remaining_manual == 1


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "directory", ["molecule/default", "extensions/molecule/group/default"]
)
def test_molecule_playbooks_retain_ordinary_native_lint_rules(tmp_path: Path, directory: str) -> None:
    """Role and collection scenario playbooks retain ordinary task lint coverage.

    Args:
        tmp_path: Isolated project directory.
        directory: Role or collection scenario layout.
    """
    import json

    from apme_engine.graph.content_graph import ContentGraph
    from apme_engine.runner import run_scan

    scenario = tmp_path / directory
    scenario.mkdir(parents=True)
    config = scenario / "molecule.yml"
    config.write_text("platforms: []\n")
    playbook = scenario / "converge.yml"
    playbook.write_text("- hosts: all\n  tasks:\n    - name: Probe\n      debug:\n        msg: example\n")
    context = run_scan(str(tmp_path), str(tmp_path / ".apme-data"))
    graph = getattr(context.scandata, "content_graph", None)
    assert isinstance(graph, ContentGraph)
    request = validate_pb2.ValidateRequest(
        content_graph_data=json.dumps(graph.to_dict()).encode(),
        files=[
            common_pb2.File(path=f"{directory}/molecule.yml", content=config.read_bytes()),
            common_pb2.File(path=f"{directory}/converge.yml", content=playbook.read_bytes()),
        ],
    )
    result = _run_native(request)
    assert any(item["rule_id"] == "L026" and "converge.yml" in str(item["file"]) for item in result.violations)


def test_native_inherited_empty_scenario_and_shared_state() -> None:
    """An empty scenario inherits native config without legacy required fields."""
    shared = b"ansible:\n  playbooks:\n    converge: ../shared/site.yml\nshared_state: true\n"
    assert _scan("", {".config/molecule/config.yml": shared, "molecule/shared/site.yml": PLAYBOOK}) == []


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "source",
    [
        "ansible: []\n",
        "ansible:\n  executor:\n    backend: unknown-backend\n",
        "ansible:\n  executor:\n    args:\n      ansible_playbook: wrong-type\n",
        "ansible:\n  executor:\n    unexpected_option: true\n",
        "ansible: {}\nshared_state: wrong-type\n",
    ],
)
def test_native_schema_rejects_invalid_structure(source: str) -> None:
    """Native support validates actual executor and shared-state structure.

    Args:
        source: Native configuration with an invalid setting.
    """
    findings = _scan(source)
    assert any(item["rule_id"] == "L115" for item in findings)


def test_native_missing_playbook_has_native_source_location() -> None:
    """Native paths participate in reference checks with precise attribution."""
    findings = _scan("ansible:\n  playbooks:\n    converge: missing.yml\n")
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L116"
    assert findings[0]["path"] == "$.ansible.playbooks.converge"
    assert findings[0]["line"] == 3


def test_native_referenced_playbook_structure_is_validated() -> None:
    """Native referenced YAML is assessed without invoking Molecule."""
    findings = _scan(
        "ansible:\n  playbooks:\n    converge: converge.yml\n",
        {"molecule/default/converge.yml": b"settings: data\n"},
    )
    assert any(item["rule_id"] == "L119" for item in findings)


def test_native_retains_upstream_converge_filename_fallback() -> None:
    """The newer provisioner still accepts playbook.yml as converge.yml fallback."""
    assert (
        _scan(
            "ansible:\n  playbooks:\n    converge: converge.yml\n",
            {"molecule/default/playbook.yml": PLAYBOOK},
        )
        == []
    )


def test_native_unknown_reference_remains_incomplete() -> None:
    """Dynamic native references produce one coverage notice, not schema errors."""
    findings = _scan("ansible:\n  playbooks:\n    converge: ${PLAYBOOK_PATH}\n")
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L118"


def test_native_reference_overrides_inherited_value() -> None:
    """Native overrides replace inherited references without stale diagnostics."""
    assert (
        _scan(
            "ansible:\n  playbooks:\n    converge: converge.yml\n",
            {
                ".config/molecule/config.yml": b"ansible:\n  playbooks:\n    converge: missing.yml\n",
                "molecule/default/converge.yml": PLAYBOOK,
            },
        )
        == []
    )


def test_native_migration_preserves_upstream_legacy_precedence_and_origins() -> None:
    """Explicit legacy settings replace native ones after layers are merged."""
    findings = _scan(
        "ansible:\n  playbooks:\n    converge: converge.yml\n",
        {
            ".config/molecule/config.yml": b"provisioner:\n  playbooks:\n    converge: missing.yml\n",
            "molecule/default/converge.yml": PLAYBOOK,
        },
    )
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L116"
    assert findings[0]["file"] == ".config/molecule/config.yml"
    assert findings[0]["path"] == "$.provisioner.playbooks.converge"
    assert findings[0]["line"] == 3


def test_native_migrated_args_retain_source_for_schema_error() -> None:
    """Errors after migration point to supplied legacy settings rather than defaults."""
    findings = _scan("shared_state: true\nprovisioner:\n  ansible_args: wrong-type\n")
    schema = [item for item in findings if item["rule_id"] == "L115"]
    assert len(schema) == 1
    assert schema[0]["path"] == "$.provisioner.ansible_args"
    assert schema[0]["line"] == 3


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "source",
    [
        "ansible: []\nprovisioner:\n  playbooks:\n    converge: missing.yml\n",
        "ansible:\n  executor: []\nprovisioner:\n  ansible_args: []\n",
        "ansible:\n  executor:\n    args: []\nprovisioner:\n  ansible_args: []\n",
        "ansible: {}\nprovisioner: []\n",
    ],
)
def test_native_invalid_migration_parents_do_not_abort_validation(source: str) -> None:
    """Malformed mixed configuration remains a schema diagnostic.

    Args:
        source: Native/legacy combination with a malformed parent node.
    """
    assert any(item["rule_id"] == "L115" for item in _scan(source))


def test_native_sequences_and_playbook_arguments_receive_checks() -> None:
    """Native scenarios retain action checks and support repeated verify stages."""
    source = "ansible: {}\nscenario:\n  test_sequence:\n    - verify check.yml\n    - verify check.yml\n"
    assert _scan(source, {"molecule/default/check.yml": PLAYBOOK}) == []
    assert any(item["rule_id"] == "L116" for item in _scan(source))
    assert any(item["rule_id"] == "L117" for item in _scan(source.replace("verify check.yml", "veriffy")))


def test_native_and_legacy_scenarios_are_independent() -> None:
    """Mixed repositories can contain both families in one uploaded snapshot."""
    assert (
        _scan(
            "provisioner:\n  playbooks:\n    converge: converge.yml\n",
            {
                "molecule/default/converge.yml": PLAYBOOK,
                "molecule/native/molecule.yml": b"ansible:\n  playbooks:\n    converge: converge.yml\n",
                "molecule/native/converge.yml": PLAYBOOK,
            },
        )
        == []
    )


def test_bundled_molecule_profiles_have_verified_provenance() -> None:
    """Both offline schemas are immutable snapshots with matching hashes."""
    import hashlib
    import json

    from apme_engine import molecule

    assets = Path(molecule.__file__).parent / "data"
    manifest = json.loads((assets / "provenance.json").read_text())
    assert manifest["minimum_version"] == "24.9.0"
    assert {profile["version"] for profile in manifest["profiles"]} == {"24.9.0", "26.11.0"}
    for profile in manifest["profiles"]:
        assert len(profile["commit"]) == 40
        blob = (assets / f"molecule-{profile['version']}.json").read_bytes()
        assert hashlib.sha256(blob).hexdigest() == profile["schema_sha256"]
        assert profile["commit"] in profile["schema_url"]
        driver_blob = (assets / f"driver-{profile['version']}.json").read_bytes()
        assert hashlib.sha256(driver_blob).hexdigest() == profile["driver_schema_sha256"]
        assert profile["commit"] in profile["driver_schema_url"]


def test_source_identity_and_inline_suppression_remain_local() -> None:
    """Independent missing references retain distinct full-mode suppressions."""
    from apme.v1.engine_pb2 import RuleConfig
    from apme_engine.daemon.engine_server import _apply_rule_configs, _filter_file_noqa
    from apme_engine.fingerprint import compute_fingerprint

    findings = _scan("provisioner:\n  playbooks:\n    converge: missing.yml  # noqa: L116\n    prepare: other.yml\n")
    assert len(findings) == 2
    fingerprints = {compute_fingerprint(str(item["rule_id"]), str(item["original_yaml"])) for item in findings}
    assert len(fingerprints) == 2
    kept = _filter_file_noqa(findings)
    assert len(kept) == 1
    assert kept[0]["line"] == 4
    assert (
        len(_filter_file_noqa(_apply_rule_configs(findings, [RuleConfig(rule_id="L116", enabled=True, enforced=True)])))
        == 2
    )


def test_yaml_merge_preserves_explicit_reference_origins() -> None:
    """An anchored missing reference retains its original source and line."""
    findings = _scan("provisioner:\n  env: &playbooks\n    converge: missing.yml\n  playbooks:\n    <<: *playbooks\n")
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L116"
    assert findings[0]["line"] == 3


def test_l119_accepts_yaml_merged_play_keys() -> None:
    """A hosts key provided through a safe YAML merge is a valid play."""
    assert not _scan(
        "provisioner:\n  playbooks:\n    converge: converge.yml\n",
        {
            "molecule/default/converge.yml": b"- <<: {hosts: all}\n  tasks: []\n",
        },
    )


def test_interpolation_precedes_flow_sequence_parsing() -> None:
    """Molecule placeholders can become valid YAML only after substitution."""
    assert not _scan("provisioner:\n  ansible_args: [${ARG}]\n", {".env.yml": b"ARG: --check\n"})


def test_unresolved_flow_sequence_needs_context() -> None:
    """Unexpanded flow placeholders cannot prove that config YAML is invalid."""
    findings = _scan("provisioner:\n  ansible_args: [${ARG}]\n")
    assert {item["rule_id"] for item in findings} == {"L118"}


def test_legacy_stage_mapping_is_not_a_path() -> None:
    """Only driver override sections may map stage names to references."""
    findings = _scan("provisioner:\n  playbooks:\n    converge: {}\n")
    assert any(item["rule_id"] == "L116" for item in findings)


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "source",
    [
        "driver:\n  options:\n    managed: wrong\n",
        "platforms:\n  - groups: [test]\n",
    ],
)
def test_default_driver_schema_is_assessed(source: str) -> None:
    """The built-in driver contract supplements the permissive core schema.

    Args:
        source: Invalid default-driver fragment.
    """
    assert any(item["rule_id"] == "L115" for item in _scan(source))


def test_unknown_conditional_selector_does_not_prove_branch_invalid() -> None:
    """An unresolved dependency type cannot select a definitive schema branch."""
    findings = _scan("dependency:\n  name: ${DEPENDENCY}\n")
    assert {item["rule_id"] for item in findings} == {"L118"}


@pytest.mark.parametrize("section", ["provisioner", "ansible"])  # type: ignore[untyped-decorator]
def test_misspelled_playbook_stage_is_not_silently_accepted(section: str) -> None:
    """A present file does not make an unused stage name valid.

    Args:
        section: Legacy or native configuration family.
    """
    findings = _scan(
        f"{section}:\n  playbooks:\n    convergge: converge.yml\n", {"molecule/default/converge.yml": PLAYBOOK}
    )
    assert any(item["rule_id"] == "L115" for item in findings)


@pytest.mark.parametrize("section", ["provisioner", "ansible"])  # type: ignore[untyped-decorator]
def test_unknown_playbook_stage_needs_context(section: str) -> None:
    """An unresolved key cannot prove a playbook stage is invalid.

    Args:
        section: Legacy or native configuration family.
    """
    findings = _scan(f'{section}:\n  playbooks:\n    "${{STAGE}}": converge.yml\n')
    assert {item["rule_id"] for item in findings} == {"L118"}


def test_ignored_lifecycle_arguments_have_a_semantic_warning() -> None:
    """Unused converge arguments are diagnosed without treating them as files."""
    findings = _scan('scenario:\n  test_sequence: ["converge ignored.yml"]\n')
    assert {item["rule_id"] for item in findings} == {"L117"}
    assert "ignored" in str(findings[0]["message"])


def test_scenario_name_uses_merged_override() -> None:
    """Explicit and inherited scenario names resolve before the second load."""
    for shared in (False, True):
        name = "scenario:\n  name: renamed\n"
        source = "provisioner:\n  playbooks:\n    converge: ${MOLECULE_SCENARIO_NAME}.yml\n"
        extra = {"molecule/default/renamed.yml": PLAYBOOK}
        if shared:
            extra[".config/molecule/config.yml"] = name.encode()
        else:
            source += name
        assert not _scan(source, extra)


def test_native_collection_scenario_name_includes_nested_path() -> None:
    """Native collection names are relative to extensions/molecule."""
    root = "extensions/molecule/group/default/"
    assert not validate_molecule(
        {
            root + "molecule.yml": b"ansible:\n  playbooks:\n    converge: ${MOLECULE_SCENARIO_NAME}.yml\n",
            root + "group/default.yml": PLAYBOOK,
        }
    )


def test_repeated_action_playbooks_are_parsed_once() -> None:
    """Repeated valid verification steps do not repeat structural parsing."""
    from apme_engine.molecule import analysis

    with patch.object(analysis, "_playbook", wraps=analysis._playbook) as assess:
        assert not _scan(
            'scenario:\n  test_sequence: ["verify check.yml", "verify check.yml"]\n',
            {
                "molecule/default/check.yml": PLAYBOOK,
            },
        )
    assert assess.call_count == 1


def test_dynamic_import_context_is_never_evaluated() -> None:
    """Unknown import expressions produce a coverage notice without execution."""
    with patch("subprocess.run", side_effect=AssertionError("No expressions may execute")):
        findings = _scan(
            "",
            {"molecule/default/converge.yml": b"- import_playbook: \"{{ lookup('pipe', 'unexpected-command') }}\"\n"},
        )
    assert {item["rule_id"] for item in findings} == {"L118"}


async def test_fix_session_passes_non_yaml_reference_targets() -> None:
    """The real session scan closure retains uploaded INI inventory content."""
    from collections.abc import AsyncIterator, Awaitable, Callable
    from types import SimpleNamespace
    from typing import cast

    from apme.v1.engine_pb2 import SessionEvent
    from apme_engine.daemon.engine_server import EngineServicer
    from apme_engine.daemon.session import SessionState

    session = SessionState(session_id="molecule-full-snapshot")
    session.working_files = {
        CONFIG: b"provisioner:\n  inventory:\n    links:\n      hosts: inventory.ini\n",
        "molecule/default/inventory.ini": b"[all]\ninstance\n",
    }
    session.original_files = dict(session.working_files)
    seen: list[str] = []

    async def pipeline(
        _self: EngineServicer, root: Path, files: list[common_pb2.File], scan_id: str, **options: object
    ) -> tuple[object, ...]:
        """Exercise Native against precisely the files dispatched by the session.

        Args:
            _self: Patched servicer instance.
            root: Materialized project root.
            files: Actual dispatched snapshot.
            scan_id: Scan correlation ID.
            **options: Scan options passed by the session.

        Returns:
            Empty pipeline metadata with real Native file findings.
        """
        seen.extend(file.path for file in files)
        findings = _run_native(validate_pb2.ValidateRequest(files=files)).violations
        return findings, None, None, [], {}, None, [], set(), set(), None

    async def assess(_self: EngineServicer, **options: object) -> AsyncIterator[SessionEvent]:
        """Invoke the real scan closure without graph remediation machinery.

        Args:
            _self: Patched servicer instance.
            **options: Session assessment inputs, including the scan callback.

        Yields:
            SessionEvent: Marker after successful snapshot assessment.
        """
        scan = cast(Callable[[list[str]], Awaitable[list[ViolationDict]]], options["scan_fn"])
        assert await scan(cast(list[str], options["yaml_paths"])) == []
        yield SessionEvent()

    servicer = EngineServicer()
    try:
        with (
            patch.object(EngineServicer, "_format_files", return_value=SimpleNamespace(diffs=[], skipped=[])),
            patch.object(EngineServicer, "_scan_pipeline", pipeline),
            patch.object(EngineServicer, "_session_graph_remediate", assess),
        ):
            _ = [event async for event in servicer._session_process(session, "molecule-full-snapshot")]
    finally:
        session.cleanup()
    assert set(seen) == set(session.working_files)


def test_complex_yaml_keys_cannot_hide_alias_cycles() -> None:
    """Complex-key values cannot bypass structural limits or lose other findings."""
    findings = _scan(
        "provisioner:\n  env:\n    ? [a, b]\n    : &cycle [*cycle]\n",
        {
            "molecule/other/molecule.yml": b"scenario:\n  test_sequence: [invalid]\n",
        },
    )
    assert {item["rule_id"] for item in findings} == {"L115", "L117"}


def test_literal_playbook_imports_are_checked_recursively() -> None:
    """Local import targets receive static structure and missing-file checks."""
    findings = _scan(
        "",
        {
            "molecule/default/converge.yml": b"- import_playbook: ../shared/site.yml\n",
            "molecule/shared/site.yml": b"- import_playbook: missing.yml\n",
        },
    )
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L116"
    assert findings[0]["file"] == "molecule/shared/site.yml"


def test_playbook_import_cycles_terminate_statically() -> None:
    """Recursive imports produce a diagnostic without execution or recursion failure."""
    findings = _scan(
        "",
        {
            "molecule/default/converge.yml": b"- import_playbook: ../shared/site.yml\n",
            "molecule/shared/site.yml": b"- import_playbook: ../default/converge.yml\n",
        },
    )
    assert any(item["rule_id"] == "L119" for item in findings)


def test_yaml_merged_imports_are_checked_statically() -> None:
    """Imports inherited through YAML merge keys cannot bypass traversal."""
    findings = _scan(
        "",
        {
            "molecule/default/converge.yml": b"- <<: {import_playbook: ../../site.yml}\n",
            "site.yml": b"- import_playbook: missing.yml\n",
        },
    )
    assert any(item["rule_id"] == "L116" and item["file"] == "site.yml" for item in findings)


def test_projects_without_molecule_are_unchanged() -> None:
    """Both public helpers accept ordinary projects without scenario files."""
    from apme_engine.molecule import molecule_playbook_paths

    files = {"site.yml": PLAYBOOK}
    assert validate_molecule(files) == []
    assert molecule_playbook_paths(files) == frozenset()


def test_recursive_yaml_findings_support_full_fingerprints() -> None:
    """Rejected alias cycles cannot crash downstream content suppression."""
    from apme_engine.fingerprint import compute_fingerprint

    findings = _scan("x: &cycle [*cycle]\n")
    assert findings[0]["rule_id"] == "L115"
    assert compute_fingerprint("L115", str(findings[0]["original_yaml"]))


@pytest.mark.parametrize("action", ["create", "destroy", "prepare"])  # type: ignore[untyped-decorator]
def test_collection_import_without_uploaded_collection_is_context(action: str) -> None:
    """Collection imports need collection context rather than a local file.

    Args:
        action: Collection playbook named in the reported deployed output.
    """
    findings = _scan(
        "",
        {
            "molecule/default/converge.yml": (
                f"- ansible.builtin.import_playbook: david_igou.molecule_provisioners.{action}\n".encode()
            )
        },
    )
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L118"
    assert "collection" in str(findings[0]["message"]).lower()


@pytest.mark.parametrize("reference", ["example.testing.create", "example.testing.nested.create.yaml"])  # type: ignore[untyped-decorator]
def test_uploaded_collection_import_is_assessed(reference: str) -> None:
    """Available collection playbooks get the same structural checks as local files.

    Args:
        reference: FQCN with or without subdirectories and an extension.
    """
    target = "collections/ansible_collections/example/testing/playbooks/"
    target += "nested/create.yaml" if reference.endswith(".yaml") else "create.yml"
    findings = _scan(
        "",
        {
            "molecule/default/converge.yml": f"- import_playbook: {reference}\n".encode(),
            target: b"settings: not-a-playbook\n",
        },
    )
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L119"
    assert findings[0]["file"] == target


def test_collection_project_import_uses_supplied_metadata() -> None:
    """A source collection can refer to its own playbooks through its FQCN."""
    assert not _scan(
        "",
        {
            "galaxy.yml": b"namespace: example\nname: testing\n",
            "molecule/default/converge.yml": b"- import_playbook: example.testing.create\n",
            "playbooks/create.yml": PLAYBOOK,
        },
    )


@pytest.mark.parametrize("section", ["ansible", "provisioner"])  # type: ignore[untyped-decorator]
def test_molecule_generated_inventory_argument_is_valid(section: str) -> None:
    """Molecule provides ephemeral paths; argument use does not need their contents.

    Args:
        section: Native or legacy argument layout.
    """
    arguments = "['--inventory=${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/']"
    source = (
        f"ansible:\n  executor:\n    args:\n      ansible_playbook: {arguments}\n"
        if section == "ansible"
        else f"provisioner:\n  ansible_args: {arguments}\n"
    )
    assert not _scan(source)


def test_generated_playbook_reference_still_needs_context() -> None:
    """A symbolic generated path must never turn into a missing local file."""
    findings = _scan("ansible:\n  playbooks:\n    converge: ${MOLECULE_EPHEMERAL_DIRECTORY}/generated.yml\n")
    assert {item["rule_id"] for item in findings} == {"L118"}


def test_shared_optional_reference_identifies_each_affected_scenario() -> None:
    """Shared file origins must not collapse distinct scenario-relative absences."""
    findings = _scan(
        "",
        {
            ".config/molecule/config.yml": b"ansible:\n  playbooks:\n    side_effect: side_effect.yml\n",
            "molecule/other/molecule.yml": b"",
            "molecule/present/molecule.yml": b"",
            "molecule/present/side_effect.yml": PLAYBOOK,
        },
    )
    assert len(findings) == 2
    assert all(item["rule_id"] == "L116" for item in findings)
    assert all(item["file"] == ".config/molecule/config.yml" and item["line"] == 3 for item in findings)
    assert all("optional" in str(item["message"]) and "skip" in str(item["message"]) for item in findings)
    assert any("molecule/default/molecule.yml" in str(item["message"]) for item in findings)
    assert any("molecule/other/molecule.yml" in str(item["message"]) for item in findings)
    assert not any("molecule/present/molecule.yml" in str(item["message"]) for item in findings)


@pytest.mark.parametrize("extension", ["", ".yml", ".yaml"])  # type: ignore[untyped-decorator]
def test_collection_import_supports_uploaded_install_layout(extension: str) -> None:
    """Qualified targets preserve Ansible's extension lookup conventions.

    Args:
        extension: Explicit import extension, if supplied.
    """
    assert not _scan(
        "",
        {
            "molecule/default/converge.yml": f"- import_playbook: example.testing.prepare{extension}\n".encode(),
            "ansible_collections/example/testing/playbooks/prepare" + (extension or ".yaml"): PLAYBOOK,
        },
    )


def test_dotted_local_playbook_is_not_assumed_to_be_a_collection() -> None:
    """Explicit local dotted paths still get missing-file checks."""
    findings = _scan("", {"molecule/default/converge.yml": b"- import_playbook: ./example.testing.prepare.yml\n"})
    assert {item["rule_id"] for item in findings} == {"L116"}
    assert not _scan(
        "",
        {
            "molecule/default/converge.yml": b"- import_playbook: example.testing.prepare.yml\n",
            "molecule/default/example.testing.prepare.yml": PLAYBOOK,
        },
    )


def test_unrelated_collection_metadata_cannot_satisfy_an_import() -> None:
    """Uploaded playbooks are not assigned to a collection with a different name."""
    findings = _scan(
        "",
        {
            "galaxy.yml": b"namespace: unrelated\nname: collection\n",
            "molecule/default/converge.yml": b"- import_playbook: example.testing.prepare\n",
            "playbooks/prepare.yml": PLAYBOOK,
        },
    )
    assert {item["rule_id"] for item in findings} == {"L118"}


def test_collection_playbook_imports_keep_missing_local_targets() -> None:
    """Resolving an uploaded collection does not skip its ordinary local imports."""
    target = "collections/ansible_collections/example/testing/playbooks/prepare.yml"
    findings = _scan(
        "",
        {
            "molecule/default/converge.yml": b"- import_playbook: example.testing.prepare\n",
            target: b"- import_playbook: missing.yml\n",
        },
    )
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L116"
    assert findings[0]["file"] == target


def test_runtime_path_recognition_preserves_unknown_variables_and_host_isolation() -> None:
    """Only documented generated variables are recognized, without host lookup."""
    source = (
        "ansible:\n  executor:\n    args:\n      ansible_playbook:\n"
        "        - --inventory=${MOLECULE_EPHEMERAL_DIRECTORY}/${EXTERNAL}\n"
    )
    with patch.dict("os.environ", {"EXTERNAL": "host-secret", "MOLECULE_EPHEMERAL_DIRECTORY": "host-private"}):
        findings = _scan(source)
    assert {item["rule_id"] for item in findings} == {"L118"}
    assert "host-secret" not in str(findings)
    assert "host-private" not in str(findings)


@pytest.mark.parametrize(
    "variable", ["MOLECULE_FILE", "MOLECULE_STATE_FILE", "MOLECULE_INSTANCE_CONFIG", "MOLECULE_INVENTORY_FILE"]
)  # type: ignore[untyped-decorator]
def test_generated_file_paths_remain_unavailable_references(variable: str) -> None:
    """Generated files cannot be claimed present merely by recognizing their names.

    Args:
        variable: Documented Molecule-generated runtime file variable.
    """
    findings = _scan(f"ansible:\n  playbooks:\n    converge: ${{{variable}}}\n")
    assert {item["rule_id"] for item in findings} == {"L118"}


def test_noqa_on_child_data_does_not_suppress_an_unrelated_import() -> None:
    """Only the diagnostic line controls inline suppression, not its child data."""
    from apme_engine.daemon.engine_server import _filter_file_noqa

    findings = _scan(
        "",
        {
            "molecule/default/converge.yml": (
                b"- import_playbook: first-missing.yml\n  vars:\n    note: value # noqa: L116\n"
                b"- import_playbook: second-missing.yml # noqa: L116\n"
            )
        },
    )
    assert len(findings) == 2
    kept = _filter_file_noqa(findings)
    assert len(kept) == 1
    assert kept[0]["line"] == 1
    assert "note:" in str(kept[0]["original_yaml"])


def test_l115_uses_the_profile_message_for_rejected_values() -> None:
    """Report the accepted set from the bundled profile instead of a keyword."""
    findings = _scan("driver:\n  name: ansible\n")
    assert [item["rule_id"] for item in findings] == ["L115"]
    message = str(findings[0]["message"])
    assert "24.9.0 schema: is not one of [" in message
    assert "anyOf" not in message


def test_l118_external_driver_notice_is_advisory() -> None:
    """A supported external driver needs a coverage notice, not a warning."""
    findings = _scan("driver:\n  name: docker\n")
    assert [(item["rule_id"], item["severity"]) for item in findings] == [("L118", "info")]


def test_l118_non_ansible_verifier_notice_is_advisory() -> None:
    """Verifier code outside Ansible playbooks is reported without gating CI."""
    findings = _scan("verifier:\n  name: testinfra\n")
    assert [item["severity"] for item in findings if item["rule_id"] == "L118"] == ["info"]


def test_escaped_dollar_suppresses_variable_context_notice() -> None:
    """`$$` stays an escaped literal, so it cannot be an unresolved reference."""
    findings = _scan("driver:\n  name: $$literal\n")
    assert [item["rule_id"] for item in findings] == ["L115"]


def test_equivalent_snapshot_spellings_resolve_shared_configuration() -> None:
    """`./`, doubled, and trailing-separator spellings address the same file."""
    findings = validate_molecule(
        {
            "./molecule/default/molecule.yml": b"driver:\n  name: default\n",
            "./.config//molecule/config.yml": b"verifier:\n  name: bogus\n",
        }
    )
    assert [(item["rule_id"], item["file"], item["line"]) for item in findings if item["rule_id"] == "L115"] == [
        ("L115", ".config/molecule/config.yml", 2)
    ]


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("path", "expected"),
    [
        ("$", True),
        ("$.driver.name", True),
        ("$[0].tasks", True),
        ("", False),
        ("3f1c9b7e-node-id", False),
        ("$x", False),
    ],
)
def test_file_finding_detection_matches_yaml_path_shape(path: str, expected: bool) -> None:
    """Only source positions are treated as file findings, not node identifiers.

    Args:
        path: Finding path field.
        expected: Whether the finding is a file diagnostic.
    """
    from apme_engine.daemon.engine_server import _is_file_finding

    assert _is_file_finding({"path": path}) is expected


def test_wide_config_merge_does_not_rescan_all_origins_per_key() -> None:
    """Wide config layers have a bounded number of full source-map traversals."""
    from typing import cast
    from unittest.mock import MagicMock

    from apme_engine.molecule import analysis

    layer = analysis._load("config.yml", "".join(f"key_{index}: {index}\n" for index in range(2000)).encode(), {})
    origins = MagicMock(wraps=layer.origins)
    layer.origins = cast(dict[analysis.YamlPath, analysis._Location], origins)
    target = analysis._Document()
    analysis._merge(target, layer)
    assert len(target.data) == 2000
    assert len(target.origins) == 2000
    assert origins.items.call_count <= 4


async def test_native_rpc_reported_multi_scenario_pattern() -> None:
    """The deployed output pattern retains real coverage limits without false paths."""
    from apme_engine.daemon.native_validator_server import NativeValidatorServicer

    names = [
        "armbian-netboot",
        "codex-desktop",
        "default",
        "devenv",
        "ghapp",
        "grafana-kiosk",
        "kubernetes-k3s",
        "kubernetes-kubeadm",
        "kubernetes-vps",
        "linux-maintenance",
        "upsmonitor",
        "windows-domain",
        "windows-general",
        "windows-image-build",
    ]
    arguments = (
        "  executor:\n    args:\n      ansible_playbook:\n"
        "        - --inventory=${MOLECULE_EPHEMERAL_DIRECTORY}/inventory/\n"
    )
    files = {
        ".config/molecule/config.yml": (
            "ansible:\n"
            + arguments
            + "  playbooks:\n    create: ../shared/create.yml\n    destroy: ../shared/destroy.yml\n"
            "    prepare: ../shared/prepare.yml\n    side_effect: side_effect.yml\n"
        ).encode()
    }
    for name in names:
        files[f"molecule/{name}/molecule.yml"] = b""
        if name != "armbian-netboot":
            files[f"molecule/{name}/side_effect.yml"] = PLAYBOOK
    files["molecule/armbian-netboot/molecule.yml"] = (
        "ansible:\n"
        + arguments
        + "  playbooks:\n    create: create.yml\n    destroy: destroy.yml\n    prepare: prepare.yml\n"
    ).encode()
    for directory in ("shared", "armbian-netboot"):
        for action in ("create", "destroy", "prepare"):
            files[f"molecule/{directory}/{action}.yml"] = (
                f"- ansible.builtin.import_playbook: david_igou.molecule_provisioners.{action}\n".encode()
            )
    request = validate_pb2.ValidateRequest(
        files=[common_pb2.File(path=path, content=content) for path, content in files.items()]
    )
    with patch("subprocess.run", side_effect=AssertionError("Static checks cannot execute project code")):
        response = await NativeValidatorServicer().Validate(request, None)  # type: ignore[arg-type]
    findings = response.violations  # type: ignore[attr-defined]
    assert sum(finding.rule_id == "L116" for finding in findings) == 1
    assert sum(finding.rule_id == "L118" for finding in findings) == 6
    assert len(findings) == 7
    missing = next(finding for finding in findings if finding.rule_id == "L116")
    assert "molecule/armbian-netboot/molecule.yml" in missing.message
    assert "optional" in missing.message
    assert all("Collection" in finding.message for finding in findings if finding.rule_id == "L118")
    assert response.diagnostics.files_received == len(files)  # type: ignore[attr-defined]


@pytest.mark.parametrize("metadata", [b"namespace: example\nname: testing\n", b"namespace: [\n"])  # type: ignore[untyped-decorator]
def test_collection_metadata_is_parsed_once_per_request(metadata: bytes) -> None:
    """Repeated imports cache both valid and malformed project metadata.

    Args:
        metadata: Valid or malformed uploaded collection metadata.
    """
    from apme_engine.molecule import analysis

    source = "- import_playbook: example.testing.prepare\n" * 25
    with patch.object(analysis, "_load", wraps=analysis._load) as load:
        _scan(
            "",
            {
                "molecule/default/converge.yml": source.encode(),
                "galaxy.yml": metadata,
                "playbooks/prepare.yml": PLAYBOOK,
            },
        )
    assert sum(call.args[0] == "galaxy.yml" for call in load.call_args_list) == 1


def test_first_collection_copy_cannot_be_bypassed_to_find_a_playbook() -> None:
    """A later collection copy must not hide missing content in the first one."""
    findings = _scan(
        "",
        {
            "molecule/default/converge.yml": b"- import_playbook: example.testing.prepare\n",
            "collections/ansible_collections/example/testing/README.md": b"First installed copy",
            "ansible_collections/example/testing/playbooks/prepare.yml": PLAYBOOK,
        },
    )
    assert {item["rule_id"] for item in findings} == {"L118"}


@pytest.mark.parametrize("reference", ["123.testing.prepare", "class.testing.prepare", "example.class.prepare"])  # type: ignore[untyped-decorator]
def test_invalid_collection_identifiers_are_not_resolved_as_collections(reference: str) -> None:
    """Python-invalid collection names must retain local missing-reference checks.

    Args:
        reference: Identifier rejected by Ansible's collection loader.
    """
    namespace, collection, _ = reference.split(".")
    findings = _scan(
        "",
        {
            "molecule/default/converge.yml": f"- import_playbook: {reference}\n".encode(),
            f"ansible_collections/{namespace}/{collection}/playbooks/prepare.yml": PLAYBOOK,
        },
    )
    assert {item["rule_id"] for item in findings} == {"L116"}


def test_nested_collection_scenario_can_resolve_sibling_collection() -> None:
    """Scenarios in an installed source collection can inspect uploaded siblings."""
    root = "ansible_collections/example/testing/"
    assert not validate_molecule(
        {
            root + "extensions/molecule/default/molecule.yml": b"",
            root + "extensions/molecule/default/converge.yml": b"- import_playbook: other.tools.prepare\n",
            "ansible_collections/other/tools/playbooks/prepare.yml": PLAYBOOK,
        }
    )


def test_validators_share_neutral_molecule_analysis_without_importing_each_other() -> None:
    """L057 reuses the neutral assessment instead of the Native validator module.

    Ansible ``L057`` must exclude statically assessed scenario playbooks, and the
    Native validator must run L115-L119. Both read the same shared analysis, so
    that analysis lives in ``apme_engine.molecule`` and validator packages stay
    independent of one another (ADR-076).
    """
    import ast

    from apme_engine.molecule import molecule_playbook_paths

    validators_dir = Path(__file__).resolve().parent.parent / "src" / "apme_engine" / "validators"
    sibling_packages = {entry.name for entry in validators_dir.iterdir() if entry.is_dir()}
    sibling_packages = {name for name in sibling_packages if not name.startswith("_")}
    offenders: list[str] = []
    for module in sorted(validators_dir.rglob("*.py")):
        package = module.relative_to(validators_dir).parts[0]
        imported: list[str] = []
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        for name in imported:
            parts = name.split(".")
            if parts[:2] != ["apme_engine", "validators"] or len(parts) < 3:
                continue
            if parts[2] in sibling_packages - {package}:
                offenders.append(f"{module.relative_to(validators_dir)} imports {name}")
    assert offenders == []

    assert molecule_playbook_paths({"molecule/default/molecule.yml": b""}) == frozenset()
