"""Keep distinct Molecule diagnostics through Native, engine, and CLI output."""

from __future__ import annotations

from typing import cast

import pytest
from google.protobuf.message import Message

from apme.v1 import common_pb2, validate_pb2
from apme_engine.cli._convert import violation_proto_to_dict as cli_violation_to_dict
from apme_engine.cli.output import deduplicate_violations
from apme_engine.daemon.engine_server import _deduplicate_violations, _sort_violations
from apme_engine.daemon.native_validator_server import NativeValidatorServicer
from apme_engine.daemon.violation_convert import violation_dict_to_proto, violation_proto_to_dict
from apme_engine.graph.content_graph import _violation_key
from apme_engine.graph.types import ViolationDict
from apme_engine.violation_identity import diagnostic_discriminator

_CONFIG = "molecule/default/molecule.yml"
_PLAYBOOK = "molecule/default/converge.yml"


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("rule_id", "config", "playbook", "paths"),
    [
        ("L115", "{unknown_a: true, unknown_b: false}", "- hosts: all\n", {"$.unknown_a", "$.unknown_b"}),
        (
            "L116",
            "ansible: {playbooks: {converge: missing.yml, verify: absent.yml}}",
            "- hosts: all\n",
            {"$.ansible.playbooks.converge", "$.ansible.playbooks.verify"},
        ),
        (
            "L117",
            "scenario: {test_sequence: [invalid_a, invalid_b]}",
            "- hosts: all\n",
            {"$.scenario.test_sequence[0]", "$.scenario.test_sequence[1]"},
        ),
        (
            "L118",
            "ansible: {playbooks: {converge: '{{ playbook_a }}', verify: '{{ playbook_b }}'}}",
            "- hosts: all\n",
            {"$.ansible.playbooks.converge", "$.ansible.playbooks.verify"},
        ),
        (
            "L119",
            "{}",
            "- {hosts: all, tasks: wrong, handlers: false}\n",
            {"$[0].tasks", "$[0].handlers"},
        ),
    ],
)
async def test_flow_map_findings_survive_native_engine_and_cli(
    rule_id: str, config: str, playbook: str, paths: set[str]
) -> None:
    """Real same-line findings survive transport and both deduplication stages.

    Args:
        rule_id: Molecule rule exercised by the supplied snapshot.
        config: Single-line scenario configuration.
        playbook: Supplied scenario playbook, optionally malformed.
        paths: Distinct diagnostic locations expected from Native.
    """
    request = validate_pb2.ValidateRequest(
        request_id="molecule-flow-identity",
        files=[
            common_pb2.File(path=_CONFIG, content=config.encode()),
            common_pb2.File(path=_PLAYBOOK, content=playbook.encode()),
        ],
    )
    response = await NativeValidatorServicer().Validate(request, None)  # type: ignore[arg-type]
    transported = validate_pb2.ValidateResponse()
    cast(Message, transported).ParseFromString(cast(Message, response).SerializeToString())
    raw = [violation_proto_to_dict(finding) for finding in transported.violations]  # type: ignore[attr-defined]
    assert len(raw) == 2
    assert {finding["rule_id"] for finding in raw} == {rule_id}
    assert {finding["path"] for finding in raw} == paths
    assert {finding["line"] for finding in raw} == {1}
    assert {finding["file"] for finding in raw} == {_PLAYBOOK if rule_id == "L119" else _CONFIG}
    engine = _deduplicate_violations(_sort_violations(raw + [dict(finding) for finding in raw]))
    assert len(engine) == 2
    assert {finding["path"] for finding in engine} == paths
    cli = [cli_violation_to_dict(violation_dict_to_proto(finding)) for finding in engine]
    output = deduplicate_violations(cli + [dict(finding) for finding in cli])
    assert len(output) == 2
    assert {finding["path"] for finding in output} == paths
    assert all(finding["remediation_class"] == "manual-review" for finding in output)
    assert all(finding["scope"] == "collection" for finding in output)


@pytest.mark.parametrize("rule_id", ["L115", "L116", "L117", "L118", "L119", "native:L115"])  # type: ignore[untyped-decorator]
def test_molecule_identity_preserves_path_and_message(rule_id: str) -> None:
    """Separate paths and separate reasons survive while presentation changes collapse.

    Args:
        rule_id: Bare or legacy-prefixed Molecule rule.
    """
    original: ViolationDict = {
        "rule_id": rule_id,
        "file": _CONFIG,
        "line": 1,
        "path": "$.driver.name",
        "message": "Configuration violates schema (type)",
        "severity": "medium",
    }
    other_reason = {**original, "message": "Configuration violates schema (enum)"}
    other_path = {**original, "path": "$.verifier.name"}
    findings = [original, other_reason, other_path]
    repeated = findings + [{**finding, "severity": "high"} for finding in findings]
    assert _deduplicate_violations(repeated) == findings
    assert deduplicate_violations(repeated) == findings
    assert len({_violation_key(finding) for finding in findings}) == 3


@pytest.mark.parametrize("rule_id", ["L021", "M001", "R108"])  # type: ignore[untyped-decorator]
def test_other_rules_retain_existing_deduplication(rule_id: str) -> None:
    """Unrelated rules continue to use their original source/node identities.

    Args:
        rule_id: Rule outside Molecule and argspec diagnostic handling.
    """
    first: ViolationDict = {"rule_id": rule_id, "file": "site.yml", "line": 1, "path": "task", "message": "first"}
    second = {**first, "path": "other", "message": "second"}
    assert diagnostic_discriminator(first) == diagnostic_discriminator(second) == ""
    assert _deduplicate_violations([first, second]) == [first]
    assert deduplicate_violations([first, second]) == [first]
    assert _violation_key(first) == ("task", rule_id)
