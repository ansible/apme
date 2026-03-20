"""L058: Argspec validation (docstring-based).

Parses the module's DOCUMENTATION string to extract the argument spec.
Safe (no code execution), fast, but may drift from the actual argument_spec in code.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

RULE_ID = "L058"

_SCRIPT = textwrap.dedent("""\
import json, sys

data = json.loads(sys.stdin.read())
module_names = data.get("modules", [])
tasks = data.get("tasks", [])

specs = {}
try:
    from ansible.plugins.loader import module_loader
    from ansible.utils.plugin_docs import get_docstring

    for name in module_names:
        ctx = module_loader.find_plugin_with_context(name, ignore_deprecated=True)
        if not ctx.resolved or not getattr(ctx, "plugin_resolved_path", None):
            continue
        path = ctx.plugin_resolved_path
        try:
            doc, _, _, _ = get_docstring(path, fragment_loader=None, is_module=True)
        except Exception:
            continue
        if not doc:
            continue
        options = doc.get("options") or {}
        specs[name] = {
            "options": options,
            "required_together": doc.get("required_together", []),
            "mutually_exclusive": doc.get("mutually_exclusive", []),
            "required_one_of": doc.get("required_one_of", []),
        }
        fqcn = getattr(ctx, "resolved_fqcn", "") or ""
        if fqcn and fqcn != name:
            specs[fqcn] = specs[name]
except Exception as e:
    sys.stderr.write(f"L058: failed to load specs: {e}\\n")

violations = []
for task in tasks:
    module = task.get("module", "")
    module_options = task.get("module_options", {})
    if not module or not isinstance(module_options, dict):
        continue

    spec = specs.get(module)
    if not spec:
        continue

    options = spec.get("options", {})
    user_keys = set(module_options.keys())
    if "free_form" in options:
        continue
    if any("{{" in str(v) for v in module_options.values()):
        continue

    valid_params = set(options.keys())
    valid_params.update({
        "_raw_params", "_ansible_check_mode", "_ansible_debug",
        "_ansible_diff", "_ansible_keep_remote_files", "_ansible_module_name",
        "_ansible_no_log", "_ansible_remote_tmp", "_ansible_shell_executable",
        "_ansible_socket", "_ansible_syslog_facility", "_ansible_tmpdir",
        "_ansible_verbosity", "_ansible_version",
    })
    for pname, pspec in options.items():
        for alias in (pspec.get("aliases") or []):
            valid_params.add(alias)

    unknown = user_keys - valid_params
    if unknown:
        violations.append({
            "module": module,
            "message": f"Unsupported parameters for {module}: {', '.join(sorted(unknown))}",
            "task_key": task.get("key", ""),
        })

    for pname, pspec in options.items():
        if pspec.get("required") and pname not in user_keys:
            aliases = pspec.get("aliases") or []
            if not any(a in user_keys for a in aliases):
                violations.append({
                    "module": module,
                    "message": f"Missing required parameter '{pname}' for {module}",
                    "task_key": task.get("key", ""),
                })

    for pname, pspec in options.items():
        if pname in user_keys and pspec.get("choices"):
            val = module_options[pname]
            choices = pspec["choices"]
            if val not in choices:
                violations.append({
                    "module": module,
                    "message": (
                        f"Value '{val}' for parameter '{pname}' of {module} "
                        f"is not one of: {', '.join(str(c) for c in choices)}"
                    ),
                    "task_key": task.get("key", ""),
                })

json.dump(violations, sys.stdout)
""")


def run(
    task_nodes: list[dict[str, object]],
    venv_root: Path,
    env_extra: dict[str, str] | None = None,
    **_kwargs: object,
) -> list[dict[str, object]]:
    """Run docstring-based argspec validation in the venv's Python.

    Args:
        task_nodes: List of task node dicts.
        venv_root: Path to ansible venv root.
        env_extra: Optional extra environment variables.
        **_kwargs: Ignored keyword arguments.

    Returns:
        List of violation dicts.
    """
    return _run_argspec_script(_SCRIPT, task_nodes, venv_root, env_extra)


def _run_argspec_script(
    script: str,
    task_nodes: list[dict[str, object]],
    venv_root: Path,
    env_extra: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """Run argspec validation script in venv Python.

    Args:
        script: Python script to execute.
        task_nodes: List of task node dicts.
        venv_root: Path to ansible venv root.
        env_extra: Optional extra environment variables.

    Returns:
        List of violation dicts.
    """
    task_modules: dict[str, bool] = {}
    tasks_for_check: list[dict[str, object]] = []
    for node in task_nodes:
        module = node.get("module", "")
        module_options = node.get("module_options")
        if not module or not isinstance(module_options, dict) or not module_options:
            continue
        module_str = str(module)
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

    python = venv_root / "bin" / "python"
    if not python.is_file():
        return []

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    if env_extra:
        env.update(env_extra)

    try:
        result = subprocess.run(
            [str(python), "-c", script],
            input=json.dumps({"modules": list(task_modules.keys()), "tasks": tasks_for_check}),
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"{RULE_ID} check timed out\n")
        return []

    if result.returncode != 0:
        sys.stderr.write(f"{RULE_ID} check failed: {result.stderr[:500]}\n")
        return []

    try:
        raw_violations = json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(f"{RULE_ID} returned invalid JSON: {result.stdout[:200]}\n")
        return []

    task_by_key = {t["key"]: t for t in tasks_for_check}
    violations = []
    for rv in raw_violations:
        task_key = rv.get("task_key", "")
        task = task_by_key.get(task_key, {})
        line = task.get("line")
        line_num = line[0] if isinstance(line, list | tuple) and line else 1
        violations.append(
            {
                "rule_id": RULE_ID,
                "level": "error",
                "message": rv.get("message", "argument validation failed"),
                "file": task.get("file", ""),
                "line": line_num,
                "path": task_key,
                "scope": "task",
            }
        )

    return violations
