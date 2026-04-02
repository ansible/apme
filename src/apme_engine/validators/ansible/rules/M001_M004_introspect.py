"""M001-M004: Plugin introspection via ansible-core's find_plugin_with_context().

Uses the venv's ansible-core to resolve modules and detect:
  M001 - FQCN resolution (module resolved to a different canonical name)
  M002 - Deprecated module (deprecation metadata in runtime.yml)
  M003 - Module redirect (module name redirected to new FQCN)
  M004 - Removed module (tombstoned, raises AnsiblePluginRemovedError)
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import cast

_SCRIPT = textwrap.dedent("""\
import json, sys

data = json.loads(sys.stdin.read())
module_names = data.get("modules", [])
results = {}

from ansible.plugins.loader import module_loader

for name in module_names:
    info = {
        "fqcn": "",
        "deprecated": False,
        "warnings": [],
        "redirects": [],
        "removed": False,
        "removal_msg": "",
        "plugin_path": "",
    }
    try:
        ctx = module_loader.find_plugin_with_context(name, ignore_deprecated=False)
        if ctx.resolved:
            info["fqcn"] = getattr(ctx, "resolved_fqcn", "") or ""
            info["deprecated"] = bool(getattr(ctx, "deprecated", False))
            info["warnings"] = list(getattr(ctx, "deprecation_warnings", None) or [])
            info["redirects"] = list(getattr(ctx, "redirect_list", None) or [])
            info["plugin_path"] = getattr(ctx, "plugin_resolved_path", "") or ""
    except Exception as e:
        err_name = type(e).__name__
        if "Removed" in err_name or "removed" in str(e).lower():
            info["removed"] = True
            info["removal_msg"] = str(e)

    results[name] = info

json.dump(results, sys.stdout)
""")


def _run_introspection(
    module_names: list[str],
    venv_root: Path,
    env_extra: dict[str, str] | None = None,
) -> dict[str, object]:
    """Run plugin introspection in the venv's Python. Returns {name: info_dict}.

    Args:
        module_names: List of module names to introspect.
        venv_root: Path to ansible venv root.
        env_extra: Optional extra environment variables.

    Returns:
        Dict mapping module name to info dict (fqcn, deprecated, etc.).
    """
    if not module_names:
        return {}

    python = venv_root / "bin" / "python"
    if not python.is_file():
        sys.stderr.write(f"M001-M004: venv python not found at {python}, skipping introspection\n")
        return {}

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    if env_extra:
        env.update(env_extra)

    payload: dict[str, object] = {"modules": module_names}

    try:
        result = subprocess.run(
            [str(python), "-c", _SCRIPT],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write("Plugin introspection timed out\n")
        return {}

    if result.returncode != 0:
        sys.stderr.write(f"Plugin introspection failed: {result.stderr[:500]}\n")
        return {}

    try:
        return cast(dict[str, object], json.loads(result.stdout))
    except json.JSONDecodeError:
        sys.stderr.write(f"Plugin introspection returned invalid JSON: {result.stdout[:200]}\n")
        return {}


def run(
    task_nodes: list[dict[str, object]],
    venv_root: Path,
    env_extra: dict[str, str] | None = None,
    **_kwargs: object,
) -> list[dict[str, object]]:
    """Run plugin introspection and return M001-M004 violations.

    Args:
        task_nodes: List of task node dicts.
        venv_root: Path to ansible venv root.
        env_extra: Optional extra environment variables.
        **_kwargs: Ignored keyword arguments.

    Returns:
        List of violation dicts for M001, M002, M003, M004.
    """
    module_set: set[str] = set()
    for n in task_nodes:
        om = str(n.get("original_module", "") or n.get("module", ""))
        if om:
            module_set.add(om)
    unique_modules = list(module_set)
    if not unique_modules:
        return []

    intro = _run_introspection(unique_modules, venv_root, env_extra)
    if not intro:
        return []

    violations = []
    for node in task_nodes:
        module_name = str(node.get("original_module", "") or node.get("module", ""))
        if not module_name:
            continue

        info_raw = intro.get(module_name)
        if not isinstance(info_raw, dict):
            continue
        info = info_raw

        file_path = str(node.get("file", ""))
        line = node.get("line")
        line_num = line[0] if isinstance(line, list | tuple) and line else 1

        # M004: Tombstoned / removed module
        if info.get("removed"):
            removal_fqcn = str(info.get("fqcn", ""))
            m004: dict[str, object] = {
                "rule_id": "M004",
                "severity": "error",
                "message": str(info.get("removal_msg", "")) or f"Module {module_name} has been removed",
                "file": file_path,
                "line": line_num,
                "path": node.get("key", ""),
                "scope": "task",
                "original_module": module_name,
                "removal_msg": str(info.get("removal_msg", "")),
            }
            if removal_fqcn:
                m004["resolved_fqcn"] = removal_fqcn
            violations.append(m004)
            continue

        # M001: FQCN resolution
        fqcn = str(info.get("fqcn", ""))
        if fqcn and fqcn != module_name and str(module_name).count(".") < 2:
            violations.append(
                {
                    "rule_id": "M001",
                    "severity": "high",
                    "message": f"Use FQCN for module: {module_name} -> {fqcn}",
                    "file": file_path,
                    "line": line_num,
                    "path": node.get("key", ""),
                    "scope": "task",
                    "resolved_fqcn": fqcn,
                    "original_module": module_name,
                }
            )

        # M002: Deprecation
        if info.get("deprecated") or info.get("warnings"):
            warnings = info.get("warnings", [])
            w_list = warnings if isinstance(warnings, list) else []
            msg = str(w_list[0]) if w_list else f"Module {module_name} is deprecated"
            m002: dict[str, object] = {
                "rule_id": "M002",
                "severity": "high",
                "message": msg,
                "file": file_path,
                "line": line_num,
                "path": node.get("key", ""),
                "scope": "task",
                "original_module": module_name,
            }
            if fqcn:
                m002["resolved_fqcn"] = fqcn
            violations.append(m002)

        # M003: Redirects
        redirects = info.get("redirects", [])
        if len(redirects) > 1:
            chain = " -> ".join(redirects)
            violations.append(
                {
                    "rule_id": "M003",
                    "severity": "high",
                    "message": f"Module has been redirected: {chain}",
                    "file": file_path,
                    "line": line_num,
                    "path": node.get("key", ""),
                    "scope": "task",
                    "original_module": module_name,
                    "resolved_fqcn": str(redirects[-1]) if redirects else "",
                    "redirect_chain": [str(r) for r in redirects],
                }
            )

    return violations
