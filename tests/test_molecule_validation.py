"""Behavioral coverage for automatic Molecule L112-L116 validation."""

from pathlib import Path
from unittest.mock import patch

import pytest

from apme.v1 import common_pb2, validate_pb2
from apme_engine.daemon.native_validator_server import _run_native
from apme_engine.graph.types import ViolationDict
from apme_engine.validators.native.molecule import MOLECULE_RULES, validate_molecule

CONFIG = "molecule/default/molecule.yml"
PLAYBOOK = b"- hosts: all\n  tasks: []\n"


def _scan(source: str, extra: dict[str, bytes] | None = None) -> list[ViolationDict]:
    """Assess one scenario with an optional snapshot.

    Args:
        source: Scenario config source.
        extra: Other supplied files.

    Returns:
        Native Molecule findings.
    """
    return validate_molecule({CONFIG: source.encode(), **(extra or {})})


def test_l112_accepts_empty_scenario_using_profile_defaults() -> None:
    """Molecule defaults make an empty scenario a valid config fragment."""
    assert _scan("") == []


def test_l112_reports_duplicate_keys_at_the_source_line() -> None:
    """Duplicate mapping keys cannot silently override a scenario setting."""
    findings = _scan("platforms: []\nplatforms: []\n")
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L112"
    assert findings[0]["line"] == 2


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "source", ["- not-a-config\n", "provisioner: [\n", "x: &cycle\n  child: *cycle\n"]
)
def test_l112_handles_malformed_and_recursive_configuration(source: str) -> None:
    """Malformed scenarios produce config findings without crashing validation.

    Args:
        source: Invalid or recursive config.
    """
    assert any(item["rule_id"] == "L112" for item in _scan(source))


def test_l112_attributes_inherited_error_to_shared_config() -> None:
    """Errors in inherited values retain their shared-file location."""
    shared = ".config/molecule/config.yml"
    findings = _scan("", {shared: b"provisioner:\n  name: unsupported\n"})
    schema = [item for item in findings if item["rule_id"] == "L112"]
    assert len(schema) == 1
    assert schema[0]["file"] == shared
    assert schema[0]["line"] == 2


def test_l112_scenario_override_replaces_inherited_error() -> None:
    """A supported scenario override replaces a conflicting inherited scalar."""
    assert (
        _scan("provisioner:\n  name: ansible\n", {".config/molecule/config.yml": b"provisioner:\n  name: bad\n"}) == []
    )


def test_l112_does_not_mislabel_new_native_syntax_as_red_hat_baseline() -> None:
    """Native syntax is outside the verified 24.9.0 compatibility profile."""
    findings = _scan("ansible:\n  playbooks:\n    converge: converge.yml\n")
    assert any(item["rule_id"] == "L112" and "24.9.0" in str(item["message"]) for item in findings)
    assert any(item["path"] == "$.ansible" for item in findings)


def test_l112_one_bad_scenario_does_not_prevent_others() -> None:
    """Independent scenarios are assessed even when another YAML document fails."""
    findings = _scan("x: [", {"molecule/second/molecule.yml": b"provisioner:\n  name: bad\n"})
    assert {str(item["file"]) for item in findings} == {CONFIG, "molecule/second/molecule.yml"}


def test_l112_non_string_structural_keys_do_not_abort_validation() -> None:
    """Non-string schema keys produce config diagnostics instead of RPC failure."""
    findings = _scan("scenario:\n  42: []\n", {"molecule/second/molecule.yml": b""})
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L112"


def test_l113_reports_missing_explicit_reference_without_asserting_runtime_failure() -> None:
    """An absent snapshot reference might be missing or deliberately excluded."""
    findings = _scan("provisioner:\n  playbooks:\n    converge: missing.yml\n")
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L113"
    assert findings[0]["line"] == 3
    assert "supplied scan" in str(findings[0]["message"])


def test_l113_allows_sibling_playbooks_and_optional_defaults() -> None:
    """Shared local playbooks resolve relative to the scenario directory."""
    assert (
        _scan("provisioner:\n  playbooks:\n    converge: ../shared/site.yml\n", {"molecule/shared/site.yml": PLAYBOOK})
        == []
    )


def test_l113_resolves_known_molecule_path_variables() -> None:
    """Static Molecule scenario variables refer to the uploaded snapshot."""
    assert (
        _scan(
            "provisioner:\n  playbooks:\n    converge: ${MOLECULE_SCENARIO_DIRECTORY}/converge.yml\n",
            {"molecule/default/converge.yml": PLAYBOOK},
        )
        == []
    )


def test_l113_uses_converge_filename_fallback_from_supported_profile() -> None:
    """Molecule 24.9 accepts playbook.yml as a converge.yml fallback."""
    assert (
        _scan("provisioner:\n  playbooks:\n    converge: converge.yml\n", {"molecule/default/playbook.yml": PLAYBOOK})
        == []
    )


def test_l113_accepts_inventory_directory_links() -> None:
    """An inventory link may target a directory represented by snapshot children."""
    assert (
        _scan(
            "provisioner:\n  inventory:\n    links:\n      group_vars: ../group_vars\n",
            {"molecule/group_vars/all.yml": b"value: 1\n"},
        )
        == []
    )


def test_l113_resolves_galaxy_requirements_relative_to_project() -> None:
    """Galaxy requirement paths use the project root, unlike stage playbooks."""
    source = "dependency:\n  name: galaxy\n  options:\n    requirements-file: requirements.yml\n"
    assert _scan(source, {"requirements.yml": b"collections: []\n"}) == []
    assert any(item["rule_id"] == "L113" for item in _scan(source))


def test_l113_does_not_require_disabled_dependency_inputs() -> None:
    """Disabled dependency management does not consume its configured files."""
    assert _scan("dependency:\n  name: galaxy\n  enabled: false\n  options:\n    requirements-file: absent.yml\n") == []


def test_l114_detects_action_typos_beyond_permissive_schema() -> None:
    """The schema permits arbitrary strings, so action names need semantic checks."""
    findings = _scan("scenario:\n  test_sequence:\n    - convergge\n")
    assert any(item["rule_id"] == "L114" and item["line"] == 3 for item in findings)


def test_l114_accepts_repeated_actions_and_explicit_verify_playbooks() -> None:
    """Repeated verification and supported action arguments are valid."""
    assert (
        _scan(
            "scenario:\n  test_sequence:\n    - converge\n    - verify check.yml\n    - verify check.yml\n",
            {"molecule/default/check.yml": PLAYBOOK},
        )
        == []
    )


def test_l114_checks_explicit_action_argument_references() -> None:
    """Explicit verify and side-effect paths are checked even without playbook config."""
    findings = _scan("scenario:\n  test_sequence:\n    - side_effect missing.yml\n")
    assert any(item["rule_id"] == "L113" for item in findings)


def test_l115_unknown_context_does_not_become_missing_reference() -> None:
    """Unresolved values are incomplete assessment rather than definite bad paths."""
    findings = _scan("provisioner:\n  playbooks:\n    converge: ${EXTERNAL_PLAYBOOK}\n")
    assert findings
    assert {item["rule_id"] for item in findings} == {"L115"}
    assert len(findings) == 1


def test_l115_unknown_root_or_key_is_not_definitively_invalid() -> None:
    """Whole documents and mapping keys can depend on unavailable variables."""
    for source in ("${CONFIG}", "${CONFIG_KEY}: value\n"):
        findings = _scan(source)
        assert {item["rule_id"] for item in findings} == {"L115"}


def test_l112_extreme_nesting_does_not_abort_independent_scenarios() -> None:
    """A parser recursion limit becomes a config finding rather than RPC failure."""
    findings = _scan("x: " + "[" * 1500 + "]" * 1500, {"molecule/good/molecule.yml": b""})
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "L112"


def test_l115_does_not_use_daemon_environment() -> None:
    """Local environment secrets must not influence remote snapshot validation."""
    source = "provisioner:\n  playbooks:\n    converge: ${EXTERNAL_PLAYBOOK}\n"
    before = _scan(source)
    with patch.dict("os.environ", {"EXTERNAL_PLAYBOOK": "daemon-secret-value"}):
        after = _scan(source)
    assert before == after
    assert "daemon-secret-value" not in str(after)


def test_l115_treats_external_paths_as_unavailable_context(tmp_path: Path) -> None:
    """A real host file outside the snapshot is never used to satisfy references.

    Args:
        tmp_path: Directory outside the uploaded file mapping.
    """
    host_file = tmp_path / "host.yml"
    host_file.write_bytes(PLAYBOOK)
    findings = _scan(f"provisioner:\n  playbooks:\n    converge: {host_file}\n")
    assert {item["rule_id"] for item in findings} == {"L115"}


def test_l115_uses_supplied_environment_file_without_exposing_values() -> None:
    """Known environment-file values can resolve references reproducibly."""
    source = "provisioner:\n  playbooks:\n    converge: ${PLAYBOOK}\n"
    assert _scan(source, {".env.yml": b"PLAYBOOK: converge.yml\n", "molecule/default/converge.yml": PLAYBOOK}) == []


def test_l115_honors_default_for_known_empty_environment_value() -> None:
    """A supplied empty value uses the shell-style nonempty default."""
    assert (
        _scan(
            "provisioner:\n  playbooks:\n    converge: ${PLAYBOOK:-converge.yml}\n",
            {".env.yml": b"PLAYBOOK: ''\n", "molecule/default/converge.yml": PLAYBOOK},
        )
        == []
    )


def test_l115_preserves_escaped_dollar_in_literal_filename() -> None:
    """Escaped dollars remain literal rather than becoming environment inputs."""
    assert (
        _scan(
            "provisioner:\n  playbooks:\n    converge: $$PLAYBOOK.yml\n",
            {"molecule/default/$PLAYBOOK.yml": PLAYBOOK},
        )
        == []
    )


def test_l112_does_not_expose_interpolated_secret_keys() -> None:
    """Schema diagnostics cannot echo secrets interpolated into mapping keys."""
    findings = _scan("${SECRET}: value\n", {".env.yml": b"SECRET: very-private-value\n"})
    assert any(item["rule_id"] == "L112" for item in findings)
    assert "very-private-value" not in str(findings)


def test_l113_ignores_inactive_driver_overrides() -> None:
    """An unused driver's playbook mapping does not determine active references."""
    assert (
        _scan(
            "provisioner:\n  playbooks:\n    docker:\n      converge: absent.yml\n",
        )
        == []
    )


def test_l115_does_not_parse_testinfra_python_as_ansible_yaml() -> None:
    """Verifier-specific arguments do not become Ansible structural errors."""
    findings = _scan(
        "verifier:\n  name: testinfra\nscenario:\n  test_sequence:\n    - verify tests/\n",
        {"molecule/default/tests/test_service.py": b"def test_service():\n    assert True\n"},
    )
    assert findings
    assert {item["rule_id"] for item in findings} == {"L115"}


def test_l115_reports_external_driver_coverage_limit() -> None:
    """Core schema success does not imply plugin-specific validation or support."""
    findings = _scan("driver:\n  name: podman\n")
    assert any(item["rule_id"] == "L115" and "driver-specific" in str(item["message"]) for item in findings)


def test_l116_reports_referenced_playbook_yaml_errors() -> None:
    """Malformed referenced playbooks receive file-specific diagnostics."""
    findings = _scan(
        "provisioner:\n  playbooks:\n    converge: converge.yml\n",
        {"molecule/default/converge.yml": b"- hosts: all\n  tasks: [\n"},
    )
    assert any(item["rule_id"] == "L116" and item["file"] == "molecule/default/converge.yml" for item in findings)


def test_l116_reports_task_lists_used_as_playbooks() -> None:
    """A referenced task list requires a wrapping playbook."""
    findings = _scan(
        "provisioner:\n  playbooks:\n    converge: converge.yml\n",
        {"molecule/default/converge.yml": b"- name: Task\n  ansible.builtin.debug:\n    msg: test\n"},
    )
    assert any(item["rule_id"] == "L116" for item in findings)


def test_l116_accepts_import_only_playbooks_without_evaluating_jinja() -> None:
    """An import-only scenario is structurally valid without rendering its target."""
    assert (
        _scan(
            "provisioner:\n  playbooks:\n    converge: converge.yml\n",
            {
                "molecule/default/converge.yml": (
                    b'- ansible.builtin.import_playbook: "{{ molecule_project_directory }}/site.yml"\n'
                )
            },
        )
        == []
    )


def test_l112_l116_work_without_graph_or_molecule_execution() -> None:
    """The Native transport runs config checks even when graph data is absent."""
    request = validate_pb2.ValidateRequest(files=[common_pb2.File(path=CONFIG, content=b"provisioner: [\n")])
    with patch("subprocess.run", side_effect=AssertionError("No project commands may run")):
        result = _run_native(request)
    assert len(result.violations) == 1
    assert result.violations[0]["rule_id"] == "L112"
    assert result.violations[0]["scope"] == "collection"


def test_l112_l116_metadata_and_catalog() -> None:
    """Automatic file rules have stable IDs, Molecule tags, and manual scope."""
    from apme_engine.rule_catalog import collect_all_rules, get_rule_documentation

    catalog = {rule.rule_id: rule for rule in collect_all_rules()}
    for rule in MOLECULE_RULES:
        assert "molecule" in rule.tags
        assert rule.scope == "collection"
        assert catalog[rule.rule_id].enabled
        assert catalog[rule.rule_id].category == "lint"
        assert get_rule_documentation(rule.rule_id)


async def test_l112_native_rpc_checks_files_without_graph() -> None:
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
    assert response.violations[0].rule_id == "L112"  # type: ignore[attr-defined]


def test_l112_l116_use_existing_rule_overrides() -> None:
    """Rules can be disabled or assigned severity using ordinary config."""
    from apme.v1.engine_pb2 import RuleConfig
    from apme_engine.daemon.engine_server import _apply_rule_configs

    findings = _scan("provisioner: [\n")
    assert _apply_rule_configs(findings, [RuleConfig(rule_id="L112", enabled=False)]) == []


def test_l112_l116_survive_approval_reconciliation() -> None:
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
    assert session.remaining_manual[0]["rule_id"] == "L112"
    assert session.report.remaining_manual == 1
    session.reset_partial_run_state()
    assert session.file_violations == []


def test_l112_malformed_configuration_can_pass_the_project_loading_stage(tmp_path: Path) -> None:
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
    assert any(item["rule_id"] == "L112" for item in result.violations)


async def test_l112_preserved_in_full_assessment_result_with_empty_graph(tmp_path: Path) -> None:
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
    assert session.remaining_manual[0]["rule_id"] == "L112"
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
