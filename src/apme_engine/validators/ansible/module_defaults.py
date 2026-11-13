"""Resolve literal module defaults for argument checks without templating plays."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

_MAX_CONTEXTS = 64
_MAX_LAYERS = 256
_MAX_REQUESTS = 4096
_MAX_INPUT_BYTES = 8 * 1024 * 1024

_SCRIPT = textwrap.dedent("""\
import json, sys
from ansible.playbook.play import Play
from ansible.plugins.loader import action_loader, init_plugin_loader, module_loader

init_plugin_loader()
results = []
for request in json.load(sys.stdin):
    try:
        play = Play()
        defaults = {}
        for layer in request['layers']:
            for entry in play._load_module_defaults('module_defaults', layer) or []:
                defaults.update(entry)
        name = request['module']
        collections = request.get('collections', [])
        context = module_loader.find_plugin_with_context(name, collection_list=collections)
        if context.resolved and not context.action_plugin:
            preferred = action_loader.find_plugin_with_context(name, collection_list=collections)
            if preferred.resolved:
                context = preferred
        elif not context.resolved:
            context = action_loader.find_plugin_with_context(name, collection_list=collections)
        if not context.resolved:
            raise ValueError('unavailable task action')
        action = context.resolved_fqcn
        groups = play._action_groups.get(action, [])
        selected = []
        for name, value in defaults.items():
            if name.startswith('group/') and name[6:] in groups:
                selected.append(value)
        if action in defaults:
            selected.append(defaults[action])
        results.append({'defaults': selected, 'module': action})
    except Exception:
        # Resolution can fail because a collection was not supplied. Do not
        # expose exception text, paths, or uploaded default values.
        results.append({'unavailable': True})
json.dump(results, sys.stdout)
""")


@dataclass
class DefaultsResult:
    """Prepared arguments and explicit defaults-coverage findings.

    Attributes:
        tasks: Copies of task payloads, one per assessable invocation context.
        violations: L058 errors or coverage warnings for excluded contexts.
    """

    tasks: list[dict[str, object]] = field(default_factory=list)
    violations: list[dict[str, object]] = field(default_factory=list)


def _literal_properties(value: object) -> tuple[bool, bool]:
    """Inspect template markers with bounded iterative YAML traversal.

    Args:
        value: Literal or nested YAML value.

    Returns:
        Whether templates are present and whether traversal was complete.
    """
    pending: list[tuple[object, int, bool]] = [(value, 0, False)]
    visited: set[int] = set()
    active: set[int] = set()
    dynamic = False
    remaining = 50_000
    while pending:
        current, depth, exiting = pending.pop()
        if exiting:
            active.remove(id(current))
            visited.add(id(current))
            continue
        remaining -= 1
        if remaining < 0 or depth > _MAX_LAYERS:
            return True, False
        if isinstance(current, str):
            dynamic |= any(marker in current for marker in ("{{", "{%", "{#"))
        elif isinstance(current, dict | list | tuple):
            if id(current) in active:
                return True, False
            if id(current) in visited:
                continue
            active.add(id(current))
            pending.append((current, depth, True))
            count = len(current) * (2 if isinstance(current, dict) else 1)
            if count > remaining:
                return True, False
            if isinstance(current, dict):
                pending.extend((key, depth + 1, False) for key in current)
                pending.extend((item, depth + 1, False) for item in current.values())
            else:
                pending.extend((item, depth + 1, False) for item in current)
    return dynamic, True


def is_dynamic(value: object) -> bool:
    """Return whether a value needs templates or exceeds literal assessment limits.

    Args:
        value: Literal or nested YAML value.

    Returns:
        True when static value checks cannot safely establish the literal value.
    """
    return _literal_properties(value)[0]


def _shape_error(layers: list[object]) -> bool:
    """Identify literal defaults that Ansible cannot interpret as mappings.

    Args:
        layers: Ordered raw defaults values from the task's ancestor scopes.

    Returns:
        True for a statically invalid defaults shape or nonstatic name.
    """
    for layer in layers:
        if layer is None:
            continue
        entries = layer if isinstance(layer, list) else [layer]
        for entry in entries:
            if not isinstance(entry, dict):
                return True
            for name, value in entry.items():
                if not isinstance(name, str) or not name or is_dynamic(name):
                    return True
                if not isinstance(value, dict) and not (isinstance(value, str) and is_dynamic(value)):
                    return True
                if isinstance(value, dict) and any(not isinstance(parameter, str) for parameter in value):
                    return True
    return False


def apply_parameter_aliases(arguments: dict[str, object], spec: dict[str, object]) -> dict[str, object]:
    """Apply Ansible's alias precedence after merging defaults and task args.

    Args:
        arguments: Merged literal argument mapping.
        spec: Parameter definitions including any aliases.

    Returns:
        A copy with alias values also assigned to their canonical parameters.
    """
    normalized = dict(arguments)
    for name, definition in spec.items():
        if not isinstance(definition, dict):
            continue
        for alias in definition.get("aliases") or []:
            if alias in normalized:
                normalized[name] = normalized[alias]
    return normalized


def has_invalid_choice(value: object, choices: object) -> bool:
    """Check literal choice values, including lists and mapping-form choices.

    Args:
        value: Supplied parameter value, possibly a list or template.
        choices: Spec choices represented by a sequence or mapping keys.

    Returns:
        Whether a statically known choice is outside the allowed values.
    """
    if not isinstance(choices, dict | list | tuple):
        return False
    allowed = list(choices)
    values = value if isinstance(value, list | tuple) else [value]
    return any(not is_dynamic(item) and item not in allowed for item in values)


def _finding(task: dict[str, object], message: str, *, error: bool = False) -> dict[str, object]:
    """Build a task-scoped defaults finding without disclosing parameter values.

    Args:
        task: Serialized task with source identity.
        message: Diagnostic summary without uploaded values.
        error: Whether the input is provably malformed rather than unavailable.

    Returns:
        L058 finding using the affected task's source location.
    """
    line = task.get("line")
    return {
        "rule_id": "L058",
        "severity": "error" if error else "warning",
        "message": message,
        "file": task.get("file", ""),
        "line": line[0] if isinstance(line, list | tuple) and line else 1,
        "path": str(task.get("key", "")),
        "scope": "task",
        "remediation_class": "manual-review",
    }


def _resolve(requests: list[dict[str, object]], venv_root: Path, env_extra: dict[str, str] | None) -> object:
    """Normalize names and group membership using installed Ansible metadata.

    Args:
        requests: Unique module and ordered defaults-layer requests.
        venv_root: Session environment whose installed metadata is authoritative.
        env_extra: Administrator-provided environment overrides.

    Returns:
        Per-request literal default mappings, or None if resolution failed.
    """
    python = venv_root / "bin" / "python"
    if not python.is_file():
        return None
    payload = json.dumps(requests)
    if len(payload.encode("utf-8")) > _MAX_INPUT_BYTES:
        return None
    env = {**os.environ, **(env_extra or {})}
    for key in (
        "PYTHONPATH",
        "ANSIBLE_COLLECTIONS_PATH",
        "ANSIBLE_COLLECTIONS_PATHS",
        "ANSIBLE_LIBRARY",
        "ANSIBLE_ACTION_PLUGINS",
        "ANSIBLE_COLLECTIONS_SCAN_SYS_PATH",
    ):
        env.pop(key, None)
    try:
        with tempfile.TemporaryDirectory(prefix="apme-module-defaults-") as directory:
            isolated = Path(directory)
            env["HOME"] = str(isolated)
            env["ANSIBLE_HOME"] = str(isolated / ".ansible")
            env["ANSIBLE_CONFIG"] = str(isolated / "ansible.cfg")
            Path(env["ANSIBLE_CONFIG"]).write_text("[defaults]\n", encoding="utf-8")
            process = subprocess.run(
                [str(python), "-I", "-c", _SCRIPT],
                input=payload,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                cwd=isolated,
            )
        if process.returncode != 0:
            return None
        return json.loads(process.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def resolve_module_defaults(
    task_nodes: list[dict[str, object]],
    venv_root: Path,
    env_extra: dict[str, str] | None = None,
) -> DefaultsResult:
    """Prepare copied task arguments for each statically assessable context.

    Defaults keys are normalized by Ansible, but values are never templated.
    Later module/group entries replace earlier entries; matching groups precede
    module defaults and explicit arguments. Unavailable contexts are excluded
    from both argument checks and receive an explicit L058 coverage warning.
    Inactive role inventory is skipped without a finding; incomplete ancestry
    takes precedence over that marker and still receives a coverage warning.

    Args:
        task_nodes: Task payloads with ordered module_defaults_contexts.
        venv_root: Session Ansible environment, accessed read-only.
        env_extra: Optional administrator-provided runtime environment.

    Returns:
        Prepared task copies and defaults-related findings.
    """
    result = DefaultsResult()
    requests: list[dict[str, object]] = []
    request_indices: dict[str, int] = {}
    pending: list[tuple[dict[str, object], int]] = []
    for task in task_nodes:
        if task.get("module_defaults_incomplete"):
            result.violations.append(
                _finding(task, "Module defaults invocation context is incomplete; argument checks are incomplete")
            )
            continue
        if task.get("module_defaults_inactive"):
            continue
        if task.get("args_serialization_incomplete"):
            result.violations.append(
                _finding(task, "Task arguments exceed serialization limits; argument checks are incomplete")
            )
            continue
        if not _literal_properties(task.get("module_options"))[1]:
            result.violations.append(
                _finding(task, "Task arguments exceed literal assessment limits; argument checks are incomplete")
            )
            continue
        contexts = task.get("module_defaults_contexts")
        if contexts is None:
            options = task.get("options")
            contexts = (
                [[options["module_defaults"]]] if isinstance(options, dict) and "module_defaults" in options else [[]]
            )
        if not isinstance(contexts, list) or not contexts or len(contexts) > _MAX_CONTEXTS:
            result.violations.append(
                _finding(task, "Module defaults invocation context is unavailable; argument checks are incomplete")
            )
            continue
        if not _literal_properties(contexts)[1]:
            result.violations.append(
                _finding(task, "Module defaults exceed literal assessment limits; argument checks are incomplete")
            )
            continue
        for layers in contexts:
            if isinstance(layers, list) and len(layers) > _MAX_LAYERS:
                result.violations.append(
                    _finding(
                        task, "Module defaults exceed the context assessment limit; argument checks are incomplete"
                    )
                )
                continue
            if not isinstance(layers, list) or _shape_error(layers):
                result.violations.append(
                    _finding(
                        task,
                        "Invalid module_defaults: expected static module/group names with parameter mappings",
                        error=True,
                    )
                )
                continue
            if not layers or all(layer is None or layer == {} or layer == [] for layer in layers):
                result.tasks.append(dict(task))
                continue
            collections = task.get("collections_in_play", [])
            if not isinstance(collections, list | tuple) or any(not isinstance(name, str) for name in collections):
                result.violations.append(
                    _finding(task, "Task collection search context is unavailable; argument checks are incomplete")
                )
                continue
            request: dict[str, object] = {
                "module": str(task.get("module", "")),
                "layers": layers,
                "collections": list(collections),
            }
            # Group precedence follows authored mapping order.
            signature = json.dumps(request)
            if signature not in request_indices:
                if len(requests) >= _MAX_REQUESTS:
                    result.violations.append(
                        _finding(
                            task, "Module defaults exceed the context assessment limit; argument checks are incomplete"
                        )
                    )
                    continue
                request_indices[signature] = len(requests)
                requests.append(request)
            pending.append((task, request_indices[signature]))
    resolved = _resolve(requests, venv_root, env_extra) if pending else None
    for task, index in pending:
        entry = resolved[index] if isinstance(resolved, list) and index < len(resolved) else None
        defaults = entry.get("defaults") if isinstance(entry, dict) else None
        module = entry.get("module") if isinstance(entry, dict) else None
        if (
            not isinstance(module, str)
            or not module
            or not isinstance(defaults, list)
            or any(not isinstance(value, dict) or any(is_dynamic(key) for key in value) for value in defaults)
        ):
            message = (
                "Module defaults need unavailable collection, group, or template context; "
                "argument checks are incomplete"
            )
            if resolved is None:
                message = (
                    "Module defaults metadata resolution failed or exceeded its assessment limits; "
                    "argument checks are incomplete"
                )
            result.violations.append(_finding(task, message))
            continue
        effective: dict[str, object] = {}
        for value in defaults:
            effective.update(value)
        inherited = bool(effective)
        explicit = task.get("module_options")
        if isinstance(explicit, dict):
            effective.update(explicit)
        result.tasks.append(
            {
                **task,
                "module": module,
                "module_options": effective,
                "module_defaults_used": inherited,
            }
        )
    # A shared task can occur repeatedly with identical unavailable contexts.
    result.violations = list({json.dumps(finding, sort_keys=True): finding for finding in result.violations}.values())
    return result
