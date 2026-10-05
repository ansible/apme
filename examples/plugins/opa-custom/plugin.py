r"""OPA Plugin sidecar: evaluate a private Rego bundle (ADR-042).

This is not the built-in OPA validator. Custom policy must not be copied
into ``src/apme_engine/validators/opa/bundle``.

Run on the host (``opa`` on PATH)::

    APME_OPA_PLUGIN_BUNDLE=examples/plugins/opa-custom/bundle \
      APME_PLUGIN_LISTEN=0.0.0.0:50100 python examples/plugins/opa-custom/plugin.py

Point Engine at it::

    export APME_PLUGIN_OPACUSTOM_ADDRESS=127.0.0.1:50100
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

_SRC = Path(__file__).resolve().parents[3] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from apme_plugin_sdk import PluginBase  # noqa: E402

logger = logging.getLogger("apme.plugin.opacustom")

_DEFAULT_BUNDLE = Path(__file__).resolve().parent / "bundle"
_DEFAULT_ENTRYPOINT = "data.apme.plugin.violations"
_OPA_TIMEOUT = 60


class OpaCustomPlugin(PluginBase):
    """Run ``opa eval`` on Engine hierarchy JSON with a sidecar bundle."""

    name = "opacustom"
    version = "1.0.0"

    def transform_rule_ids(self) -> list[str]:
        """Detection-only — org policy rewrites are not deterministic here.

        Returns:
            Empty list.
        """
        return []

    def health(self) -> str:
        """Fail Health when ``opa`` or the bundle directory is missing.

        Returns:
            ``ok`` or an error string.
        """
        import shutil

        if shutil.which("opa") is None:
            return "error: opa binary not found"
        bundle = Path(os.environ.get("APME_OPA_PLUGIN_BUNDLE", str(_DEFAULT_BUNDLE)))
        if not bundle.is_dir():
            return f"error: bundle missing: {bundle}"
        return "ok"

    def validate(
        self,
        files: Sequence[tuple[str, bytes]],
        hierarchy: object,
    ) -> list[dict[str, str | int]]:
        """Evaluate the sidecar bundle; map OPA rows to violation dicts.

        Args:
            files: Unused (Rego reads hierarchy).
            hierarchy: Parsed ``hierarchy_payload`` JSON.

        Returns:
            ``EXT-opacustom-*`` violation dicts.
        """
        del files
        bundle = Path(os.environ.get("APME_OPA_PLUGIN_BUNDLE", str(_DEFAULT_BUNDLE)))
        entrypoint = os.environ.get("APME_OPA_PLUGIN_ENTRYPOINT", _DEFAULT_ENTRYPOINT)
        raw_rows = eval_bundle(bundle, entrypoint, hierarchy)
        return [map_opa_row(self, row) for row in raw_rows]


def opa_input_document(hierarchy: object) -> dict[str, object]:
    """Wrap hierarchy JSON as the OPA ``input`` document.

    Args:
        hierarchy: Parsed Engine payload (dict, list, or None).

    Returns:
        Document with an ``hierarchy`` key when needed.
    """
    if hierarchy is None:
        return {"hierarchy": []}
    if isinstance(hierarchy, dict):
        if "hierarchy" in hierarchy:
            return cast(dict[str, object], hierarchy)
        return {"hierarchy": hierarchy}
    if isinstance(hierarchy, list):
        return {"hierarchy": list(hierarchy)}
    return {"hierarchy": []}


def parse_opa_eval_stdout(stdout: str) -> list[dict[str, object]]:
    """Extract the violation array from ``opa eval --format json``.

    Args:
        stdout: Raw OPA JSON.

    Returns:
        List of violation objects; empty on missing/invalid output.
    """
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("OPA plugin: invalid JSON from opa eval")
        return []
    expressions = result.get("result", [])
    if not isinstance(expressions, list) or not expressions:
        return []
    first = expressions[0]
    if not isinstance(first, dict):
        return []
    inner = first.get("expressions", [{}])
    if not isinstance(inner, list) or not inner:
        return []
    expr0 = inner[0]
    if not isinstance(expr0, dict):
        return []
    value = expr0.get("value")
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def map_opa_row(plugin: PluginBase, row: dict[str, object]) -> dict[str, str | int]:
    """Convert one OPA violation object to an SDK dict.

    Args:
        plugin: Plugin instance (for prefixing).
        row: OPA violation map.

    Returns:
        Dict consumed by ``PluginBase.violation``.
    """
    line_raw: Any = row.get("line")
    line = 0
    if isinstance(line_raw, int):
        line = line_raw
    elif isinstance(line_raw, list) and line_raw:
        try:
            line = int(line_raw[0])
        except (TypeError, ValueError):
            line = 0
    guidance = row.get("ai_guidance")
    return plugin.violation(
        rule_id=str(row.get("rule_id") or "001"),
        message=str(row.get("message") or "OPA plugin violation"),
        file=str(row.get("file") or ""),
        line=line,
        path=str(row.get("path") or ""),
        severity=str(row.get("severity") or "high"),
        scope=str(row.get("scope") or "task"),
        ai_guidance=str(guidance) if isinstance(guidance, str) else "",
    )


def eval_bundle(bundle: Path, entrypoint: str, hierarchy: object) -> list[dict[str, object]]:
    """Run ``opa eval -I`` against ``bundle``.

    Args:
        bundle: Directory of ``.rego`` / ``data.json``.
        entrypoint: OPA query (for example ``data.apme.plugin.violations``).
        hierarchy: Engine hierarchy JSON.

    Returns:
        Violation objects from OPA.

    Raises:
        RuntimeError: If the bundle is missing or ``opa eval`` cannot run.
    """
    if not bundle.is_dir():
        raise RuntimeError(f"OPA plugin bundle is not a directory: {bundle}")
    input_str = json.dumps(opa_input_document(hierarchy))
    try:
        completed = subprocess.run(
            ["opa", "eval", "-I", "-d", str(bundle), entrypoint, "--format", "json"],
            input=input_str,
            capture_output=True,
            text=True,
            timeout=_OPA_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("opa binary not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"opa eval timed out after {_OPA_TIMEOUT}s") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"opa eval failed (exit {completed.returncode})")
    return parse_opa_eval_stdout(completed.stdout or "")


if __name__ == "__main__":
    OpaCustomPlugin.serve()
