"""Helpers for parsing role ``argument_specs`` metadata (ADR-059)."""

from __future__ import annotations

import os
from typing import cast

import yaml

from apme_engine.graph.types import YAMLDict

_META_MAIN_KEYS = frozenset(
    {
        "galaxy_info",
        "dependencies",
        "collections",
        "allows_duplicates",
    }
)


def _is_valid_argument_specs_mapping(specs: object) -> YAMLDict | None:
    """Return specs when mapping entry points are non-empty dict values.

    Args:
        specs: Parsed argument-spec mapping candidate.

    Returns:
        The validated mapping, or ``None`` when shape is invalid.
    """
    if not isinstance(specs, dict) or not specs:
        return None
    if not all(isinstance(entry, dict) for entry in specs.values()):
        return None
    return cast(YAMLDict, specs)


def get_argument_specs_from_metadata(metadata: object) -> YAMLDict | None:
    """Return the inline ``argument_specs`` mapping from parsed role metadata.

    Args:
        metadata: Parsed ``meta/main.yml`` contents.

    Returns:
        The ``argument_specs`` dict when present and well-formed, else ``None``.
    """
    if not isinstance(metadata, dict):
        return None
    return _is_valid_argument_specs_mapping(metadata.get("argument_specs"))


def extract_argument_specs_from_standalone_yaml(data: object) -> YAMLDict | None:
    """Return argument specs parsed from standalone ``meta/argument_specs`` YAML.

    Standalone files may wrap specs under ``argument_specs`` (as in some fixtures)
    or declare entry points such as ``main`` at the top level (Ansible 2.11+).

    Args:
        data: Parsed standalone argument-spec file contents.

    Returns:
        The argument-spec mapping when well-formed, else ``None``.
    """
    if not isinstance(data, dict):
        return None
    if "argument_specs" in data:
        specs = data["argument_specs"]
    elif data.keys() & _META_MAIN_KEYS:
        return None
    else:
        specs = data
    return _is_valid_argument_specs_mapping(specs)


def load_standalone_argument_specs(role_path: str) -> YAMLDict | None:
    """Load argument specs from ``meta/argument_specs.yml`` or ``.yaml``.

    Args:
        role_path: Path to the role root directory.

    Returns:
        Parsed argument-spec mapping when a valid standalone file exists, else ``None``.
    """
    meta_dir = os.path.join(role_path, "meta")
    for name in ("argument_specs.yml", "argument_specs.yaml"):
        path = os.path.join(meta_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as file:
                data = yaml.safe_load(file)
        except (OSError, yaml.YAMLError):
            return None
        return extract_argument_specs_from_standalone_yaml(data)
    return None
