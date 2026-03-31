"""Disk I/O helpers for saving ARI scan artifacts (definitions, rule results)."""

from __future__ import annotations

import os
from pathlib import Path

import jsonpickle

from .findings import Findings


def save_rule_result(findings: Findings, out_dir: str) -> None:
    """Save rule result JSON to out_dir.

    Args:
        findings: Findings containing rule results.
        out_dir: Output directory path.

    Raises:
        ValueError: If out_dir is empty.
    """
    if out_dir == "":
        raise ValueError("output dir must be a non-empty value")

    if not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    findings.save_rule_result(fpath=os.path.join(out_dir, "rule_result.json"))


def save_definitions(definitions: dict[str, object], out_dir: str) -> None:
    """Save definition objects to objects.json in out_dir.

    Args:
        definitions: Dict with definitions key containing serializable objects.
        out_dir: Output directory path.

    Raises:
        ValueError: If out_dir is empty.
    """
    if out_dir == "":
        raise ValueError("output dir must be a non-empty value")

    if not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    objects_json_str = jsonpickle.encode(definitions["definitions"], make_refs=False)
    fpath = os.path.join(out_dir, "objects.json")
    Path(fpath).write_text(objects_json_str)
