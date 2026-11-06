"""L058: Argspec validation (docstring-based).

Parses the module's DOCUMENTATION string to extract the argument spec.
The target module is not imported, but resolving documentation fragments can
import fragment plugin code. Fast, but may drift from the actual
argument_spec in code. Do not use with untrusted collections.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import cast

from apme_engine.validators.ansible.cache import plugin_cache

RULE_ID = "L058"

_SPEC_SCRIPT = textwrap.dedent("""\
import json, sys

data = json.loads(sys.stdin.read())
module_names = data.get("modules", [])

specs = {}
try:
    from ansible.plugins.loader import fragment_loader, init_plugin_loader, module_loader
    from ansible.utils.plugin_docs import get_docstring

    init_plugin_loader()

    for name in module_names:
        ctx = module_loader.find_plugin_with_context(name, ignore_deprecated=True)
        if not ctx.resolved or not getattr(ctx, "plugin_resolved_path", None):
            continue
        path = ctx.plugin_resolved_path
        try:
            doc, _, _, _ = get_docstring(path, fragment_loader=fragment_loader, is_module=True)
        except Exception:
            continue
        if not doc:
            continue
        options = doc.get("options") or {}
        entry = {
            "options": options,
            "required_together": doc.get("required_together", []),
            "mutually_exclusive": doc.get("mutually_exclusive", []),
            "required_one_of": doc.get("required_one_of", []),
        }
        specs[name] = entry
        fqcn = getattr(ctx, "resolved_fqcn", "") or ""
        if fqcn and fqcn != name:
            specs[fqcn] = entry
except Exception as e:
    sys.stderr.write(f"L058: failed to load specs: {e}\\n")

json.dump(specs, sys.stdout)
""")

_ANSIBLE_INTERNAL_PARAMS = frozenset(
    {
        "_raw_params",
        "_ansible_check_mode",
        "_ansible_debug",
        "_ansible_diff",
        "_ansible_keep_remote_files",
        "_ansible_module_name",
        "_ansible_no_log",
        "_ansible_remote_tmp",
        "_ansible_shell_executable",
        "_ansible_socket",
        "_ansible_syslog_facility",
        "_ansible_tmpdir",
        "_ansible_verbosity",
        "_ansible_version",
    }
)


def _check_tasks_against_specs(
    specs: dict[str, object],
    tasks: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Validate task arguments against docstring-derived specs.

    Args:
        specs: Per-module spec dicts (options, required_together, etc.).
        tasks: Task dicts with module, module_options, key, file, line.

    Returns:
        List of raw violation dicts (module, message, task_key).
    """
    violations: list[dict[str, object]] = []
    for task in tasks:
        module = task.get("module", "")
        module_options = task.get("module_options", {})
        if not module or not isinstance(module_options, dict):
            continue

        spec_raw = specs.get(str(module))
        if not isinstance(spec_raw, dict):
            continue
        spec = cast(dict[str, object], spec_raw)

        options_raw = spec.get("options", {})
        if not isinstance(options_raw, dict):
            continue
        options = cast(dict[str, object], options_raw)

        user_keys = set(module_options.keys())
        if "free_form" in options:
            continue
        if any("{{" in str(v) for v in module_options.values()):
            continue

        valid_params: set[str] = set(options.keys())
        valid_params.update(_ANSIBLE_INTERNAL_PARAMS)
        for _pname, pspec_raw in options.items():
            if isinstance(pspec_raw, dict):
                for alias in pspec_raw.get("aliases") or []:
                    valid_params.add(str(alias))

        unknown = user_keys - valid_params
        if unknown:
            violations.append(
                {
                    "module": module,
                    "message": f"Unsupported parameters for {module}: {', '.join(sorted(unknown))}",
                    "task_key": task.get("key", ""),
                }
            )

        for pname, pspec_raw in options.items():
            if not isinstance(pspec_raw, dict):
                continue
            if pspec_raw.get("required") and pname not in user_keys:
                aliases = pspec_raw.get("aliases") or []
                if not any(str(a) in user_keys for a in aliases):
                    violations.append(
                        {
                            "module": module,
                            "message": f"Missing required parameter '{pname}' for {module}",
                            "task_key": task.get("key", ""),
                        }
                    )

        for pname, pspec_raw in options.items():
            if not isinstance(pspec_raw, dict):
                continue
            if pname in user_keys and pspec_raw.get("choices"):
                val = module_options[pname]
                choices = pspec_raw["choices"]
                if val not in choices:
                    violations.append(
                        {
                            "module": module,
                            "message": (
                                f"Value '{val}' for parameter '{pname}' of {module} "
                                f"is not one of: {', '.join(str(c) for c in choices)}"
                            ),
                            "task_key": task.get("key", ""),
                        }
                    )
    return violations


def run(
    task_nodes: list[dict[str, object]],
    venv_root: Path,
    env_extra: dict[str, str] | None = None,
    **_kwargs: object,
) -> list[dict[str, object]]:
    """Run docstring-based argspec validation in the venv's Python.

    Uses the plugin cache to avoid re-fetching specs for modules whose
    collection version is already cached from a prior scan.

    Args:
        task_nodes: List of task node dicts.
        venv_root: Path to ansible venv root.
        env_extra: Optional extra environment variables.
        **_kwargs: Ignored keyword arguments.

    Returns:
        List of violation dicts.
    """
    task_modules: dict[str, bool] = {}
    tasks_for_check: list[dict[str, object]] = []
    for node in task_nodes:
        if node.get("args_templated") is True:
            continue
        module = node.get("module", "")
        module_options = node.get("module_options")
        if not module or not isinstance(module_options, dict):
            continue
        module_str = str(module)
        collections = node.get("collections_in_play", [])
        if (
            module_str.count(".") < 2
            and isinstance(collections, list | tuple)
            and any(str(name) not in {"ansible.builtin", "ansible.legacy"} for name in collections)
        ):
            continue
        task_modules[module_str] = True
        tasks_for_check.append(
            {
                "module": module_str,
                "module_options": module_options,
                "key": node.get("key", ""),
                "file": node.get("file", ""),
                "line": node.get("line"),
            }
        )

    if not tasks_for_check:
        return []

    venv_str = str(venv_root)
    all_modules = list(task_modules.keys())
    cached_specs, uncached = plugin_cache.partition("docspec", venv_str, all_modules)
    all_specs: dict[str, object] = dict(cached_specs)

    if uncached:
        fresh_specs = _fetch_specs(uncached, venv_root, env_extra)
        for name, spec in fresh_specs.items():
            plugin_cache.put("docspec", venv_str, name, spec)
        all_specs.update(fresh_specs)

    raw_violations = _check_tasks_against_specs(all_specs, tasks_for_check)

    task_by_key = {t["key"]: t for t in tasks_for_check}
    violations: list[dict[str, object]] = []
    for rv in raw_violations:
        task_key = rv.get("task_key", "")
        task = task_by_key.get(str(task_key), {})
        line = task.get("line")
        line_num = line[0] if isinstance(line, list | tuple) and line else 1
        violations.append(
            {
                "rule_id": RULE_ID,
                "severity": "error",
                "message": rv.get("message", "argument validation failed"),
                "file": task.get("file", ""),
                "line": line_num,
                "path": str(task_key),
                "scope": "task",
            }
        )

    return violations


def _fetch_specs(
    module_names: list[str],
    venv_root: Path,
    env_extra: dict[str, str] | None = None,
) -> dict[str, object]:
    """Fetch docstring-derived specs via subprocess for uncached modules.

    Args:
        module_names: Module names to fetch specs for.
        venv_root: Path to ansible venv root.
        env_extra: Optional extra environment variables.

    Returns:
        Dict mapping module name to spec dict.
    """
    if not module_names:
        return {}

    python = venv_root / "bin" / "python"
    if not python.is_file():
        sys.stderr.write(f"{RULE_ID}: venv python not found at {python}, skipping\n")
        return {}

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    if env_extra:
        env.update(env_extra)

    try:
        result = subprocess.run(
            [str(python), "-c", _SPEC_SCRIPT],
            input=json.dumps({"modules": module_names}),
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"{RULE_ID} spec fetch timed out\n")
        return {}

    if result.returncode != 0:
        sys.stderr.write(f"{RULE_ID} spec fetch failed: {result.stderr[:500]}\n")
        return {}

    try:
        return cast(dict[str, object], json.loads(result.stdout))
    except json.JSONDecodeError:
        sys.stderr.write(f"{RULE_ID} returned invalid JSON: {result.stdout[:200]}\n")
        return {}
