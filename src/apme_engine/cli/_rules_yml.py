"""Load per-rule overrides from ``.apme/rules.yml`` for CLI scans (ADR-041)."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from apme.v1.primary_pb2 import RuleConfig
from apme_engine.severity_defaults import severity_from_label, severity_to_proto

RULES_YML_PATH = Path(".apme") / "rules.yml"

_SEVERITY_LABELS_LOWER = frozenset(
    ("unspecified", "info", "low", "medium", "high", "error", "critical"),
)


def load_rule_configs_from_project(project_root: Path) -> list[RuleConfig]:
    """Parse ``<project_root>/.apme/rules.yml`` into ``RuleConfig`` protos.

    The file is optional. On missing path, returns an empty list. On YAML or
    structural errors, writes a warning to stderr and returns an empty list.

    Expected shape::

        rules:
          L026:
            enabled: false
          L047:
            severity: critical
            enforced: true

    If ``enabled`` is omitted for a rule, it defaults to ``true`` so partial
    overrides (severity / enforced only) keep the rule active — proto3 bool
    fields otherwise default to false.

    Args:
        project_root: Resolved project root (e.g. from ``discover_project_root``).

    Returns:
        List of ``RuleConfig`` messages, possibly empty.
    """
    path = project_root / RULES_YML_PATH
    if not path.is_file():
        return []

    try:
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except OSError as e:
        sys.stderr.write(f"Warning: could not read {path}: {e}\n")
        return []
    except yaml.YAMLError as e:
        sys.stderr.write(f"Warning: could not parse {path}: {e}\n")
        return []

    if data is None:
        return []

    if not isinstance(data, dict):
        sys.stderr.write(f"Warning: {path}: expected a mapping at top level, ignoring rule configs\n")
        return []

    rules = data.get("rules")
    if rules is None:
        return []

    if not isinstance(rules, dict):
        sys.stderr.write(f"Warning: {path}: 'rules' must be a mapping, ignoring rule configs\n")
        return []

    configs: list[RuleConfig] = []
    for rule_id, block in rules.items():
        if not isinstance(rule_id, str) or not rule_id.strip():
            sys.stderr.write(f"Warning: {path}: skipping invalid rule id {rule_id!r}\n")
            continue
        rid = rule_id.strip()

        if block is None:
            block = {}
        if not isinstance(block, dict):
            sys.stderr.write(f"Warning: {path}: rule {rid!r} must be a mapping, skipping\n")
            continue

        enabled = bool(block["enabled"]) if "enabled" in block else True

        sev_arg: dict[str, object] = {}
        if "severity" in block:
            raw = block["severity"]
            label = str(raw).lower()
            if label == "unspecified":
                sys.stderr.write(
                    f"Warning: {path}: 'severity: unspecified' for rule {rid} has no effect — omit the key instead\n",
                )
            elif label not in _SEVERITY_LABELS_LOWER:
                sys.stderr.write(
                    f"Warning: {path}: unknown severity {raw!r} for rule {rid}, using 'medium'\n",
                )
                sev_arg["severity"] = severity_to_proto(severity_from_label(str(raw)))
            else:
                sev_arg["severity"] = severity_to_proto(severity_from_label(str(raw)))

        enforced = bool(block["enforced"]) if "enforced" in block else False

        configs.append(
            RuleConfig(
                rule_id=rid,
                enabled=enabled,
                enforced=enforced,
                **sev_arg,  # type: ignore[arg-type]
            ),
        )

    return configs
