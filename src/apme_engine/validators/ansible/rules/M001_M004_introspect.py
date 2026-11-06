"""L037, M001-M004 / M048: Plugin introspection via ansible-core's find_plugin_with_context().

Uses the venv's ansible-core to resolve modules and detect:
  M001 - FQCN resolution (module resolved to a different canonical name)
  M002 - Deprecated module (deprecation metadata in runtime.yml)
  M003 - Module redirect (module name redirected to new FQCN)
  M004 - Removed module (tombstoned, raises AnsiblePluginRemovedError)
  M048 - Prefer ansible.builtin when a non-builtin FQCN has a builtin twin
  L037 - Module name that Ansible's module and action loaders cannot resolve
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from apme_engine.validators.ansible.cache import plugin_cache

_SCRIPT = textwrap.dedent("""\
import json, os, re, sys

data = json.loads(sys.stdin.read())
module_names = data.get("modules", [])
results = {}

from ansible.plugins.loader import action_loader, init_plugin_loader, module_loader

# Required so FQCN lookups (ansible.builtin.*, collections) resolve.
init_plugin_loader()

collection_list = data.get("collection_list")
if not isinstance(collection_list, list) or not collection_list:
    collection_list = None

def lookup(loader, name):
    return loader.find_plugin_with_context(
        name, ignore_deprecated=False, check_aliases=True, collection_list=collection_list
    )

def same_plugin(original, replacement):
    if bool(original.resolved) != bool(replacement.resolved):
        return False
    if not original.resolved:
        return True
    before = getattr(original, "plugin_resolved_path", "") or ""
    after = getattr(replacement, "plugin_resolved_path", "") or ""
    return bool(before and after and os.path.realpath(before) == os.path.realpath(after))

for name in module_names:
    info = {
        "resolved": None,
        "action_resolved": None,
        "resolution_error": False,
        "fqcn": "",
        "resolution_reason": "",
        "deprecated": False,
        "warnings": [],
        "redirects": [],
        "removed": False,
        "removal_msg": "",
        "plugin_path": "",
        "builtin_alternative": "",
        "builtin_alternative_error": "",
    }
    ctx = action_ctx = None
    try:
        ctx = lookup(module_loader, name)
        info["resolved"] = bool(ctx.resolved)
        if ctx.resolved:
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
        action_ctx = lookup(action_loader, name)
        info["action_resolved"] = bool(action_ctx.resolved)
    except Exception:
        info["resolution_error"] = True

    # Prefer the action's canonical name (including action-only plugins),
    # then the module's. A usable spelling must preserve BOTH selections:
    # qualifying a builtin action must not bypass a local module override.
    if ctx is not None and action_ctx is not None and not info["resolution_error"]:
        contexts = [c for c in (action_ctx, ctx) if c.resolved]
        candidates = [getattr(c, "resolved_fqcn", "") or "" for c in contexts]
        if contexts and "." not in name:
            candidates.append("ansible.legacy." + name)
        for candidate in dict.fromkeys(candidates):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:[.][A-Za-z_][A-Za-z0-9_]*){2,}", candidate):
                continue
            try:
                if not same_plugin(action_ctx, lookup(action_loader, candidate)):
                    continue
                if not same_plugin(ctx, lookup(module_loader, candidate)):
                    continue
            except Exception:
                continue
            info["fqcn"] = candidate
            selected = contexts[0]
            info["plugin_path"] = getattr(selected, "plugin_resolved_path", "") or ""
            info["deprecated"] = any(bool(getattr(c, "deprecated", False)) for c in contexts)
            info["warnings"] = list(dict.fromkeys(
                warning for c in contexts for warning in (getattr(c, "deprecation_warnings", None) or [])
            ))
            info["redirects"] = list(getattr(selected, "redirect_list", None) or [])
            break
        if contexts and not info["fqcn"]:
            info["resolution_reason"] = "No FQCN preserves the selected module and action plugins"

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

_TRUST_PROJECT_CODE_ENV = "APME_ANSIBLE_TRUST_PROJECT_CODE"
_PROJECT_PLUGIN_PATH_ENV = (
    "ANSIBLE_COLLECTIONS_PATH",
    "ANSIBLE_COLLECTIONS_PATHS",
    "ANSIBLE_LIBRARY",
    "ANSIBLE_ACTION_PLUGINS",
    "ANSIBLE_COLLECTIONS_SCAN_SYS_PATH",
)


def _trusts_project_code(env_extra: dict[str, str] | None) -> bool:
    """Return whether the administrator explicitly trusts project plugins.

    Args:
        env_extra: Optional trusted validator environment overrides.

    Returns:
        True when project-controlled Ansible config and plugin paths are enabled.
    """
    value = os.environ.get(_TRUST_PROJECT_CODE_ENV, "")
    if env_extra is not None and _TRUST_PROJECT_CODE_ENV in env_extra:
        value = env_extra[_TRUST_PROJECT_CODE_ENV]
    return value.strip().lower() in {"1", "true", "yes"}


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

    trust_project_code = _trusts_project_code(env_extra)
    venv_str = str(venv_root)
    introspect_ctx = ""
    cached_results: dict[str, object]
    if trust_project_code:
        # Local plugins and project collections can change without a version
        # bump. Re-resolve them rather than reusing a daemon-wide result.
        cached_results, uncached = {}, module_names
    else:
        introspect_ctx = plugin_cache.introspect_context_suffix(venv_str, env_extra)
        if collection_list is not None:
            introspect_ctx = f"{introspect_ctx}|task-collections:{json.dumps(collection_list, separators=(',', ':'))}"
        introspect_ctx += "|project-code:isolated"
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
        if trust_project_code:
            result = subprocess.run(
                [str(python), "-c", _SCRIPT],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                cwd=project_root if project_root is not None and project_root.is_dir() else None,
            )
        else:
            # Do not let untrusted scan-root config or local plugin paths make
            # the resolver execute project-controlled Python in this process.
            with tempfile.TemporaryDirectory(prefix="apme-ansible-introspect-") as isolated_dir:
                isolated_path = Path(isolated_dir)
                isolated_home = isolated_path / "home"
                isolated_home.mkdir()
                for variable in _PROJECT_PLUGIN_PATH_ENV:
                    env.pop(variable, None)
                env["HOME"] = str(isolated_home)
                env["ANSIBLE_HOME"] = str(isolated_home / ".ansible")
                env["ANSIBLE_CONFIG"] = str(isolated_path / "ansible.cfg")
                Path(env["ANSIBLE_CONFIG"]).write_text("[defaults]\n", encoding="utf-8")
                result = subprocess.run(
                    [str(python), "-c", _SCRIPT],
                    input=json.dumps(payload),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=env,
                    cwd=isolated_path,
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
        if info.get("resolution_error") or trust_project_code:
            continue
        # Redirect/deprecation metadata belongs to the requested spelling.
        # Backfilling it under the target would taint a later canonical lookup.
        plugin_cache.put(
            "introspect",
            venv_str,
            name,
            info,
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

        if not fqcn and info.get("resolution_reason") and str(module_name).count(".") < 2:
            violations.append(
                {
                    "rule_id": "M001",
                    "severity": "high",
                    "message": f"Cannot qualify module {module_name}: {info['resolution_reason']}",
                    "file": file_path,
                    "line": line_num,
                    "path": node.get("key", ""),
                    "scope": "task",
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
        if len(redirects) > 1 and fqcn and fqcn != module_name:
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
                    "resolved_fqcn": fqcn,
                    "redirect_chain": [str(r) for r in redirects],
                }
            )

    return violations
