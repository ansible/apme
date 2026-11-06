"""L059: Argspec validation (mock/patch-based).

Loads the actual module code, patches AnsibleModule.__init__ to capture
the real argument_spec, then validates user args against it. More accurate
than docstring (L058) for mutually_exclusive and required_together groups;
it does not enforce required_if or required_one_of and imports module code.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import cast

from apme_engine.validators.ansible.cache import plugin_cache

RULE_ID = "L059"

_SPEC_SCRIPT = textwrap.dedent("""\
import json, sys, importlib, importlib.util, os

data = json.loads(sys.stdin.read())
module_names = data.get("modules", [])

class _CapturedSpec(Exception):
    pass

specs = {}
try:
    from ansible.plugins.loader import init_plugin_loader, module_loader
    from unittest.mock import patch

    init_plugin_loader()

    for name in module_names:
        try:
            ctx = module_loader.find_plugin_with_context(name, ignore_deprecated=True)
            if not ctx.resolved or not getattr(ctx, "plugin_resolved_path", None):
                continue
            mod_path = ctx.plugin_resolved_path
            if not mod_path or mod_path.endswith(".ps1"):
                continue

            spec = importlib.util.spec_from_file_location(f"_argcheck_{name}", mod_path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)

            captured = {}

            def mock_init(self, *args, **kwargs):
                captured["argument_spec"] = kwargs.get("argument_spec", {})
                captured["add_file_common_args"] = kwargs.get("add_file_common_args", False)
                captured["required_together"] = kwargs.get("required_together", [])
                captured["mutually_exclusive"] = kwargs.get("mutually_exclusive", [])
                captured["required_one_of"] = kwargs.get("required_one_of", [])
                captured["required_if"] = kwargs.get("required_if", [])
                raise _CapturedSpec()

            stdin_data = json.dumps({"ANSIBLE_MODULE_ARGS": {}})
            with patch.dict(os.environ, {"ANSIBLE_MODULE_ARGS": stdin_data}):
                with patch("ansible.module_utils.basic.AnsibleModule.__init__", mock_init):
                    try:
                        spec.loader.exec_module(mod)
                        if hasattr(mod, "main"):
                            mod.main()
                    except _CapturedSpec:
                        pass
                    except SystemExit:
                        pass
                    except Exception:
                        pass

            if captured.get("argument_spec"):
                entry = {
                    "argument_spec": captured["argument_spec"],
                    "add_file_common_args": captured.get("add_file_common_args", False),
                    "required_together": captured.get("required_together", []),
                    "mutually_exclusive": captured.get("mutually_exclusive", []),
                    "required_one_of": captured.get("required_one_of", []),
                    "required_if": captured.get("required_if", []),
                }
                specs[name] = entry
                fqcn = getattr(ctx, "resolved_fqcn", "") or ""
                if fqcn and fqcn != name:
                    specs[fqcn] = entry
        except Exception:
            continue
except Exception as e:
    sys.stderr.write(f"L059: failed to load specs: {e}\\n")

json.dump(specs, sys.stdout)
""")

_ANSIBLE_INTERNAL_PARAMS = frozenset(
    {
        "_raw_params",
        "_raw",
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

# Parameters that Ansible merges into a module's argument_spec at runtime when
# the module passes ``add_file_common_args=True`` to ``AnsibleModule.__init__``
# (see ``FILE_COMMON_ARGUMENTS`` in ``ansible.module_utils.basic``). Our mock
# captures the argspec at call time — before Ansible performs that merge — so
# these names never appear in the captured dict and must be whitelisted here
# to avoid false positives for tasks that legitimately use them (e.g. ``mode``,
# ``owner``). Keep in sync with upstream ``FILE_COMMON_ARGUMENTS``.
_FILE_COMMON_ARGUMENTS = frozenset(
    {
        "mode",
        "owner",
        "group",
        "seuser",
        "serole",
        "setype",
        "selevel",
        "follow",
        "unsafe_writes",
        "attributes",
        "attr",
    }
)


def _check_tasks_against_specs(
    specs: dict[str, object],
    tasks: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Validate task arguments against mock-captured argspecs.

    Args:
        specs: Per-module spec dicts (argument_spec, mutually_exclusive, etc.).
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

        arg_spec_raw = spec.get("argument_spec", {})
        if not isinstance(arg_spec_raw, dict):
            continue
        arg_spec = cast(dict[str, object], arg_spec_raw)

        user_keys = set(module_options.keys())

        if any("{{" in str(v) for v in module_options.values()):
            continue

        valid_params: set[str] = set(arg_spec.keys())
        valid_params.update(_ANSIBLE_INTERNAL_PARAMS)
        if spec.get("add_file_common_args"):
            valid_params.update(_FILE_COMMON_ARGUMENTS)
        for _pname, pdef in arg_spec.items():
            if isinstance(pdef, dict):
                for alias in pdef.get("aliases") or []:
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

        for pname, pdef in arg_spec.items():
            if isinstance(pdef, dict) and pdef.get("required") and pname not in user_keys:
                aliases = pdef.get("aliases") or []
                if not any(str(a) in user_keys for a in aliases):
                    violations.append(
                        {
                            "module": module,
                            "message": f"Missing required parameter '{pname}' for {module}",
                            "task_key": task.get("key", ""),
                        }
                    )

        for pname, pdef in arg_spec.items():
            if isinstance(pdef, dict) and pname in user_keys and pdef.get("choices"):
                val = module_options[pname]
                choices = pdef["choices"]
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

        me = spec.get("mutually_exclusive", [])
        if isinstance(me, list):
            for group in me:
                if not isinstance(group, list):
                    continue
                present = [p for p in group if p in user_keys]
                if len(present) > 1:
                    violations.append(
                        {
                            "module": module,
                            "message": f"Parameters are mutually exclusive for {module}: {', '.join(present)}",
                            "task_key": task.get("key", ""),
                        }
                    )

        rt = spec.get("required_together", [])
        if isinstance(rt, list):
            for group in rt:
                if not isinstance(group, list):
                    continue
                present = [p for p in group if p in user_keys]
                if present and len(present) != len(group):
                    missing = [p for p in group if p not in user_keys]
                    violations.append(
                        {
                            "module": module,
                            "message": (
                                f"Parameters must be used together for {module}: "
                                f"{', '.join(str(g) for g in group)} (missing: {', '.join(missing)})"
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
    """Run mock/patch-based argspec validation in the venv's Python.

    Uses the plugin cache to avoid re-capturing argspecs for modules whose
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
    cached_specs, uncached = plugin_cache.partition("mockspec", venv_str, all_modules)
    all_specs: dict[str, object] = dict(cached_specs)

    if uncached:
        fresh_specs = _fetch_specs(uncached, venv_root, env_extra)
        for name, spec in fresh_specs.items():
            plugin_cache.put("mockspec", venv_str, name, spec)
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
    """Fetch mock-captured argspecs via subprocess for uncached modules.

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
