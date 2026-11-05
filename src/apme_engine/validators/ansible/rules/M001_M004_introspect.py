"""L037, M001-M004 / M048: Plugin introspection via ansible-core's find_plugin_with_context().

Uses the venv's ansible-core to resolve modules and detect:
  M001 - FQCN resolution (module resolved to a different canonical name)
  M002 - Deprecated module (deprecation metadata in runtime.yml)
  M003 - Module redirect (module name redirected to new FQCN)
  M004 - Removed module (tombstoned, raises AnsiblePluginRemovedError)
  M048 - Prefer ansible.builtin when a non-builtin FQCN has a builtin twin
  L037 - Module name that Ansible's module and action loaders cannot resolve
"""

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from apme_engine.validators.ansible.cache import plugin_cache

_SCRIPT = textwrap.dedent("""\
import json, sys

data = json.loads(sys.stdin.read())
module_names = data.get("modules", [])
results = {}

from ansible.plugins.loader import action_loader, init_plugin_loader, module_loader

# Required so FQCN lookups (ansible.builtin.*, collections) resolve.
init_plugin_loader()

collection_list = data.get("collection_list")
if not isinstance(collection_list, list) or not collection_list:
    collection_list = None

for name in module_names:
    info = {
        "resolved": None,
        "action_resolved": None,
        "resolution_error": False,
        "fqcn": "",
        "deprecated": False,
        "warnings": [],
        "redirects": [],
        "removed": False,
        "removal_msg": "",
        "plugin_path": "",
        "builtin_alternative": "",
        "builtin_alternative_error": "",
    }
    try:
        ctx = module_loader.find_plugin_with_context(
            name, ignore_deprecated=False, collection_list=collection_list
        )
        info["resolved"] = bool(ctx.resolved)
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
        else:
            info["resolution_error"] = True

    try:
        action_ctx = action_loader.find_plugin_with_context(
            name, ignore_deprecated=False, collection_list=collection_list
        )
        info["action_resolved"] = bool(action_ctx.resolved)
    except Exception:
        info["resolution_error"] = True

    # Prefer-builtin twin: only when the original name resolved to a
    # non-builtin / non-legacy FQCN. Short-name lookup finds real
    # builtins even when ansible.builtin.<short> is a redirect out of core.
    resolved = info["fqcn"]
    if (
        resolved
        and resolved.count(".") >= 2
        and not resolved.startswith("ansible.builtin.")
        and not resolved.startswith("ansible.legacy.")
    ):
        short = resolved.rsplit(".", 1)[-1]
        try:
            ctx2 = module_loader.find_plugin_with_context(short, ignore_deprecated=False)
            if ctx2.resolved:
                alt = getattr(ctx2, "resolved_fqcn", "") or ""
                if alt.startswith("ansible.builtin."):
                    info["builtin_alternative"] = alt
        except Exception as e:
            info["builtin_alternative_error"] = f"{type(e).__name__}: {e}"

    results[name] = info

json.dump(results, sys.stdout)
""")


def _run_introspection(
    module_names: list[str],
    venv_root: Path,
    env_extra: dict[str, str] | None = None,
    collection_list: list[str] | None = None,
    project_root: Path | None = None,
) -> dict[str, object]:
    """Run plugin introspection in the venv's Python. Returns {name: info_dict}.

    Uses the plugin cache to skip subprocess calls for modules whose
    collection version is already cached from a prior scan.

    Args:
        module_names: List of module names to introspect.
        venv_root: Path to ansible venv root.
        env_extra: Optional extra environment variables.
        collection_list: Ansible collection search order for these tasks.
        project_root: Scan root used as the subprocess working directory so
            Ansible can load project-level configuration and plugin paths.

    Returns:
        Dict mapping module name to info dict (resolved, fqcn, deprecated, etc.).
    """
    if not module_names:
        return {}

    venv_str = str(venv_root)
    introspect_ctx = plugin_cache.introspect_context_suffix(venv_str, env_extra)
    if collection_list is not None:
        introspect_ctx = f"{introspect_ctx}|task-collections:{json.dumps(collection_list, separators=(',', ':'))}"
    config_override = (
        env_extra.get("ANSIBLE_CONFIG") if env_extra is not None and "ANSIBLE_CONFIG" in env_extra else None
    )
    if config_override is None:
        config_override = os.environ.get("ANSIBLE_CONFIG")
    home_override = env_extra.get("HOME") if env_extra is not None and "HOME" in env_extra else None
    if home_override is None:
        home_override = os.environ.get("HOME")
    home_directory = Path(home_override).expanduser() if home_override else Path.home()
    if not home_directory.is_absolute():
        home_directory = (project_root or Path.cwd()) / home_directory

    # Fingerprint existing Ansible config candidates because they affect plugin
    # resolution. Do not include a missing project ansible.cfg path: many scans
    # use distinct temporary roots, and that would defeat caching when they all
    # use the same home/system config. An explicit ANSIBLE_CONFIG path is kept
    # even when missing so creating it invalidates an earlier unresolved cache.
    config_candidates: list[Path] = []
    if config_override:
        if config_override == "~":
            override_path = home_directory
        elif config_override.startswith("~/"):
            override_path = home_directory / config_override[2:]
        else:
            override_path = Path(config_override).expanduser()
        if not override_path.is_absolute():
            override_path = (project_root or Path.cwd()) / override_path
        config_candidates.append(override_path)
    else:
        if project_root is not None:
            config_candidates.append(project_root / "ansible.cfg")
        config_candidates.extend((home_directory / ".ansible.cfg", Path("/etc/ansible/ansible.cfg")))

    config_fingerprints: list[str] = []
    for candidate in config_candidates:
        try:
            resolved_config = candidate.resolve()
        except (OSError, RuntimeError):
            resolved_config = candidate.absolute()
        try:
            contents = resolved_config.read_bytes()
        except OSError:
            if config_override and candidate == config_candidates[0]:
                config_fingerprints.append(f"missing:{resolved_config}")
            continue
        config_fingerprints.append(f"{resolved_config}:{hashlib.sha256(contents).hexdigest()}")
    if config_fingerprints:
        introspect_ctx = f"{introspect_ctx}|ansible-config:{'|'.join(config_fingerprints)}"
    cached_results, uncached = plugin_cache.partition(
        "introspect",
        venv_str,
        module_names,
        introspect_context=introspect_ctx,
    )

    if not uncached:
        return cached_results

    python = venv_root / "bin" / "python"
    if not python.is_file():
        sys.stderr.write(f"M001-M004/M048: venv python not found at {python}, skipping introspection\n")
        return cached_results

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    if env_extra:
        env.update(env_extra)

    payload: dict[str, object] = {
        "modules": uncached,
        "collection_list": collection_list or [],
    }

    try:
        result = subprocess.run(
            [str(python), "-c", _SCRIPT],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            cwd=project_root if project_root is not None and project_root.is_dir() else None,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write("Plugin introspection timed out\n")
        return cached_results

    if result.returncode != 0:
        sys.stderr.write(f"Plugin introspection failed: {result.stderr[:500]}\n")
        return cached_results

    try:
        fresh: dict[str, object] = json.loads(result.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(f"Plugin introspection returned invalid JSON: {result.stdout[:200]}\n")
        return cached_results

    for name, info in fresh.items():
        if not isinstance(info, dict):
            continue
        twin_err = str(info.pop("builtin_alternative_error", "") or "")
        if twin_err:
            sys.stderr.write(f"M048 twin lookup failed for {name}: {twin_err}\n")
            continue
        if info.get("resolution_error"):
            continue
        resolved_fqcn = str(info.get("fqcn", ""))
        plugin_cache.put(
            "introspect",
            venv_str,
            name,
            info,
            resolved_fqcn=resolved_fqcn,
            introspect_context=introspect_ctx,
        )

    merged: dict[str, object] = {}
    merged.update(cached_results)
    merged.update(fresh)
    return merged


def run(
    task_nodes: list[dict[str, object]],
    venv_root: Path,
    env_extra: dict[str, str] | None = None,
    project_root: Path | None = None,
    **_kwargs: object,
) -> list[dict[str, object]]:
    """Run plugin introspection and return L037, M001-M004, and M048 violations.

    Args:
        task_nodes: List of task node dicts.
        venv_root: Path to ansible venv root.
        env_extra: Optional extra environment variables.
        project_root: Root directory for Ansible configuration and plugin paths.
        **_kwargs: Ignored keyword arguments.

    Returns:
        List of violation dicts for L037, M001, M002, M003, M004, and M048.
    """
    modules_by_collection_list: dict[tuple[str, ...], set[str]] = {}
    for n in task_nodes:
        om = str(n.get("original_module", "") or n.get("module", ""))
        if om and "{{" not in om and "{%" not in om:
            raw_collections = n.get("collections_in_play", [])
            collections = (
                tuple(str(value) for value in raw_collections if value)
                if isinstance(raw_collections, list | tuple)
                else ()
            )
            modules_by_collection_list.setdefault(collections, set()).add(om)
    if not modules_by_collection_list:
        return []

    intro: dict[tuple[str, tuple[str, ...]], object] = {}
    for collections, module_names in modules_by_collection_list.items():
        group_results = _run_introspection(
            sorted(module_names),
            venv_root,
            env_extra,
            collection_list=list(collections),
            project_root=project_root,
        )
        intro.update({(name, collections): info for name, info in group_results.items()})
    if not intro:
        return []

    violations = []
    for node in task_nodes:
        module_name = str(node.get("original_module", "") or node.get("module", ""))
        if not module_name or "{{" in module_name or "{%" in module_name:
            continue

        raw_collections = node.get("collections_in_play", [])
        collections = (
            tuple(str(value) for value in raw_collections if value) if isinstance(raw_collections, list | tuple) else ()
        )
        info_raw = intro.get((module_name, collections))
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

        # L037: use Ansible's plugin loader result instead of guessing from
        # the module spelling. Valid short names and collection FQCNs resolve
        # through the same runtime used by the scan.
        if info.get("resolved") is False and info.get("action_resolved") is False and not info.get("resolution_error"):
            violations.append(
                {
                    "rule_id": "L037",
                    "severity": "medium",
                    "message": f"Module {module_name} could not be resolved by Ansible",
                    "file": file_path,
                    "line": line_num,
                    "path": node.get("key", ""),
                    "scope": "task",
                    "original_module": module_name,
                }
            )
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

        # M048: Prefer ansible.builtin when a non-builtin FQCN has a builtin twin.
        # Short names are owned by M001; only already-FQCN module names fire here.
        # resolved_fqcn is the rewrite target (builtin), matching M001/M003 polarity.
        builtin_alt = str(info.get("builtin_alternative", "") or "")
        if builtin_alt and str(module_name).count(".") >= 2:
            prefer_from = fqcn if fqcn.count(".") >= 2 else module_name
            if (
                prefer_from.count(".") >= 2
                and not prefer_from.startswith("ansible.builtin.")
                and not prefer_from.startswith("ansible.legacy.")
            ):
                violations.append(
                    {
                        "rule_id": "M048",
                        "severity": "low",
                        "message": f"Prefer builtin module: {prefer_from} -> {builtin_alt}",
                        "file": file_path,
                        "line": line_num,
                        "path": node.get("key", ""),
                        "scope": "task",
                        "resolved_fqcn": builtin_alt,
                        "original_module": module_name,
                        "builtin_alternative": builtin_alt,
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
