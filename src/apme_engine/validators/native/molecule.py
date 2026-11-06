"""Read-only Molecule file checks for legacy and native configuration, 24.9.0+.

All inputs come from the uploaded snapshot. This module never imports Molecule,
executes project code, reads host configuration, or resolves network schemas.
"""

from __future__ import annotations

import copy
import json
import posixpath
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import cast

import yaml
from jsonschema import Draft7Validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from apme_engine.graph.types import RuleMetadata, RuleScope, Severity, ViolationDict, YAMLDict, YAMLValue

PROFILE_VERSION = "24.9.0"
NATIVE_PROFILE_VERSION = "26.11.0"
_ASSETS = Path(__file__).parent / "molecule_data"
_VIRTUAL_ROOT = "/__apme_project__"
_ENV = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?:(:-|-)([^{}]*))?\}|([A-Za-z_][A-Za-z0-9_]*))")
_ACTIONS = frozenset(
    {
        "check",
        "cleanup",
        "converge",
        "create",
        "dependency",
        "destroy",
        "idempotence",
        "prepare",
        "side_effect",
        "syntax",
        "test",
        "verify",
    }
)
_STAGES = frozenset({"cleanup", "create", "converge", "destroy", "prepare", "side_effect", "verify"})
_NAMES = {
    "L112": "Molecule configuration does not match the supported profile",
    "L113": "Molecule file reference is unavailable in the scan",
    "L114": "Molecule scenario sequence contains an invalid action",
    "L115": "Molecule validation needs additional context",
    "L116": "Molecule scenario playbook has invalid YAML or play structure",
}
MOLECULE_RULES = tuple(
    RuleMetadata(
        rule_id=rule_id,
        name=f"Molecule{rule_id}",
        description=description,
        version="v0.0.1",
        severity=Severity.MEDIUM,
        tags=("quality", "molecule"),
        scope=RuleScope.COLLECTION,
    )
    for rule_id, description in _NAMES.items()
)

YamlPath = tuple[str | int, ...]


class _NeedsContext(ValueError):
    """Configuration structure depends on an unresolved substitution."""


@dataclass(frozen=True)
class _Location:
    """Original source of an effective config value.

    Attributes:
        file: Snapshot-relative source file.
        line: One-based source line.
        path: YAML path in that source file.
    """

    file: str
    line: int = 1
    path: YamlPath = ()


@dataclass
class _Document:
    """Parsed data and source origins for one scenario.

    Attributes:
        data: Effective config mapping.
        origins: Effective paths mapped to their original source.
    """

    data: YAMLDict = field(default_factory=dict)
    origins: dict[YamlPath, _Location] = field(default_factory=dict)


@lru_cache(maxsize=2)
def _profile(version: str = PROFILE_VERSION) -> tuple[Draft7Validator, YAMLDict]:
    """Load trusted bundled schema and defaults.

    Args:
        version: Bundled configuration-family profile version.

    Returns:
        Schema validator and immutable-by-convention baseline defaults.
    """
    schema = json.loads((_ASSETS / f"molecule-{version}.json").read_text())
    defaults = cast(YAMLDict, json.loads((_ASSETS / f"defaults-{version}.json").read_text()))
    return Draft7Validator(schema), defaults


def _yaml_path(path: YamlPath) -> str:
    """Format a source path for diagnostics.

    Args:
        path: YAML mapping keys and list offsets.

    Returns:
        A readable JSON-style path.
    """
    return "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in path)


def _finding(rule_id: str, origin: _Location, message: str) -> ViolationDict:
    """Create a normal Native finding without exposing source values.

    Args:
        rule_id: Stable lint rule identifier.
        origin: Source location.
        message: Explanation excluding interpolated secrets.

    Returns:
        A finding with manual-review scope.
    """
    return {
        "rule_id": rule_id,
        "severity": "medium",
        "message": message,
        "file": origin.file,
        "line": origin.line,
        "path": _yaml_path(origin.path),
        "scope": RuleScope.COLLECTION.value,
        "remediation_class": "manual-review",
        "source": "native",
    }


def _locations(
    node: yaml.Node,
    file: str,
    path: YamlPath = (),
    active: frozenset[int] = frozenset(),
    budget: list[int] | None = None,
) -> dict[YamlPath, _Location]:
    """Collect source locations and reject recursive or excessively deep YAML.

    Args:
        node: Parsed YAML representation node.
        file: Source path.
        path: Current YAML path.
        active: Ancestor node identities for alias-cycle detection.
        budget: Remaining traversal budget shared across alias expansions.

    Returns:
        Source location map.

    Raises:
        ValueError: If the YAML is recursive or exceeds the depth limit.
    """
    if budget is None:
        budget = [20_000]
    budget[0] -= 1
    if id(node) in active or len(path) > 64 or budget[0] < 0:
        raise ValueError("Recursive or excessively deep YAML")
    result = {path: _Location(file, node.start_mark.line + 1, path)}
    active = active | {id(node)}
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode):
                continue
            child_path = (*path, key.value)
            result.update(_locations(value, file, child_path, active, budget))
            result[child_path] = _Location(file, key.start_mark.line + 1, child_path)
    elif isinstance(node, yaml.SequenceNode):
        for index, value in enumerate(node.value):
            result.update(_locations(value, file, (*path, index), active, budget))
    return result


def _expand(source: str, variables: Mapping[str, str]) -> str:
    """Expand known values while preserving unknown context and escaped dollars.

    Args:
        source: Raw config source.
        variables: Statically known scenario/environment-file values.

    Returns:
        Partially expanded source with unknown variables intact.
    """
    escaped = source.replace("$$", "\x00APME_DOLLAR\x00")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(4)
        if name not in variables:
            return match.group(0)
        value = variables[name]
        if not value and match.group(2) == ":-":
            return match.group(3) or ""
        return value

    expanded = _ENV.sub(replace, escaped)
    return expanded.replace("\x00APME_DOLLAR\x00", "$$")


def _load(file: str, content: bytes, variables: Mapping[str, str]) -> _Document:
    """Parse one config with duplicate-key detection and original locations.

    Args:
        file: Snapshot path.
        content: File bytes.
        variables: Known static substitutions.

    Returns:
        Parsed mapping and its source map.

    Raises:
        ValueError: If config is not a mapping or has an unsafe structure.
        _NeedsContext: If an unresolved substitution determines the root mapping.
    """
    source = content.decode("utf-8")
    node = yaml.compose(source, Loader=yaml.SafeLoader)
    locations: dict[YamlPath, _Location] = _locations(node, file) if node is not None else {(): _Location(file)}
    loader = YAML(typ="safe", pure=True)
    loader.version = (1, 1)
    data: object = loader.load(_expand(source, variables))
    if data is None:
        data = {}
    if isinstance(data, str) and _unknowns(data):
        raise _NeedsContext("Configuration root needs environment context")
    if not isinstance(data, dict) or any(not isinstance(key, str) for key in data):
        raise ValueError("Molecule configuration must be a mapping with string keys")
    return _Document(cast(YAMLDict, data), locations)


def _merge(target: _Document, layer: _Document, path: YamlPath = ()) -> None:
    """Recursively merge a config layer, replacing sequences and source origins.

    Args:
        target: Effective configuration to update.
        layer: New configuration layer.
        path: Prefix for this recursive merge.
    """
    for key, value in layer.data.items():
        current = (*path, key)
        previous = target.data.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            child = _Document(previous, target.origins)
            _merge(child, _Document(value, layer.origins), current)
        else:
            target.data[key] = copy.deepcopy(value)
            for old_path in list(target.origins):
                if old_path[: len(current)] == current:
                    del target.origins[old_path]
        for source_path, origin in layer.origins.items():
            if source_path[: len(current)] == current:
                target.origins[source_path] = origin


def _origin(document: _Document, path: YamlPath, fallback: str) -> _Location:
    """Locate the nearest supplied source for a defaulted or merged path.

    Args:
        document: Effective config and source map.
        path: Effective YAML path.
        fallback: Scenario file for baseline defaults.

    Returns:
        Best available original location.
    """
    while path:
        if path in document.origins:
            return document.origins[path]
        path = path[:-1]
    return document.origins.get((), _Location(fallback))


def _unknowns(value: YAMLValue, path: YamlPath = ()) -> list[YamlPath]:
    """Find unresolved substitutions without interpreting Jinja or shell syntax.

    Args:
        value: YAML value to inspect.
        path: YAML path of the value.

    Returns:
        Paths whose values require unavailable context.
    """
    if isinstance(value, str):
        escaped = value.replace("$$", "")
        return [path] if _ENV.search(escaped) or "{{" in value or "{%" in value else []
    if isinstance(value, dict):
        result: list[YamlPath] = []
        for key, child in value.items():
            current = (*path, key)
            if _ENV.search(str(key)):
                result.append(current)
            result.extend(_unknowns(child, current))
        return result
    if isinstance(value, list):
        return [item for index, child in enumerate(value) for item in _unknowns(child, (*path, index))]
    return []


def _project_root(scenario: str) -> str:
    """Infer a role or collection root from a scenario snapshot path.

    Args:
        scenario: Molecule config file path.

    Returns:
        Project root relative to the snapshot.
    """
    parts = PurePosixPath(scenario).parts
    if "molecule" not in parts:
        return "."
    index = parts.index("molecule")
    if index and parts[index - 1] == "extensions":
        index -= 1
    return str(PurePosixPath(*parts[:index]))


def _reference(base: str, raw: str) -> str | None:
    """Resolve a literal reference within the uploaded snapshot only.

    Args:
        base: Snapshot-relative scenario or project directory.
        raw: Known literal or virtual-root path.

    Returns:
        Normalized snapshot-relative path, or None for external/dynamic paths.
    """
    if _ENV.search(raw.replace("$$", "")) or "{{" in raw or "{%" in raw:
        return None
    raw = raw.replace("$$", "$")
    if raw.startswith(_VIRTUAL_ROOT + "/"):
        raw = raw[len(_VIRTUAL_ROOT) + 1 :]
        base = "."
    elif raw.startswith(("/", "~")) or "://" in raw:
        return None
    resolved = posixpath.normpath(posixpath.join(base, raw))
    return None if resolved == ".." or resolved.startswith("../") else resolved


def _playbook(file: str, content: bytes) -> list[ViolationDict]:
    """Inspect scenario YAML structure without evaluating Ansible plugins.

    Args:
        file: Snapshot-relative playbook path.
        content: Playbook bytes.

    Returns:
        Structural/YAML findings for the referenced playbook.
    """
    try:
        node = yaml.compose(content.decode("utf-8"), Loader=yaml.BaseLoader)
        if node is not None:
            _locations(node, file)
        if not isinstance(node, yaml.SequenceNode):
            return [_finding("L116", _Location(file), "Scenario playbook must be a list of plays")]
        findings: list[ViolationDict] = []
        for index, play in enumerate(node.value):
            keys = (
                {key.value for key, _ in play.value if isinstance(key, yaml.ScalarNode)}
                if isinstance(play, yaml.MappingNode)
                else set()
            )
            if not keys.intersection({"hosts", "import_playbook", "ansible.builtin.import_playbook"}):
                findings.append(
                    _finding(
                        "L116",
                        _Location(file, play.start_mark.line + 1, (index,)),
                        "Scenario play must define hosts or import_playbook",
                    )
                )
        return findings
    except (yaml.YAMLError, UnicodeError, ValueError, RecursionError) as error:
        mark = getattr(error, "problem_mark", None)
        return [
            _finding(
                "L116",
                _Location(file, mark.line + 1 if mark is not None else 1),
                "Scenario playbook contains invalid YAML",
            )
        ]


def _migrate_native(document: _Document) -> None:
    """Apply upstream legacy-to-native precedence while retaining source paths.

    Molecule merges user layers before migration; explicitly supplied legacy
    fields replace their native equivalents. Defaults must be applied later.
    Invalid parent structures are left intact for schema diagnostics.

    Args:
        document: Merged user configuration, before profile defaults.
    """
    provisioner = document.data.get("provisioner")
    if not isinstance(provisioner, dict):
        return
    mappings: dict[str, tuple[str, ...]] = {
        "ansible_args": ("executor", "args", "ansible_playbook"),
        "config_options": ("cfg",),
        "env": ("env",),
        "playbooks": ("playbooks",),
    }
    if not any(key in provisioner for key in mappings):
        return
    native = document.data.setdefault("ansible", {})
    if not isinstance(native, dict):
        return
    for key, destination in mappings.items():
        if key not in provisioner:
            continue
        target = native
        for part in destination[:-1]:
            child = target.setdefault(part, {})
            if not isinstance(child, dict):
                break
            target = child
        else:
            target[destination[-1]] = provisioner.pop(key)
            old_path: YamlPath = ("provisioner", key)
            new_path: YamlPath = ("ansible", *destination)
            source_origins = {
                new_path + path[len(old_path) :]: origin
                for path, origin in document.origins.items()
                if path[: len(old_path)] == old_path
            }
            for path in list(document.origins):
                if path[: len(new_path)] == new_path or path[: len(old_path)] == old_path:
                    del document.origins[path]
            document.origins.update(source_origins)


def _references(document: _Document, scenario: str, files: Mapping[str, bytes]) -> list[ViolationDict]:
    """Check configured playbooks, inventory links, and Galaxy requirement files.

    Args:
        document: Effective scenario config.
        scenario: Molecule config path.
        files: Complete supplied snapshot, including non-YAML artifacts.

    Returns:
        Reference, context, and playbook findings.
    """
    provisioner = document.data.get("provisioner")
    native = "ansible" in document.data
    section = "ansible" if native else "provisioner"
    ansible = document.data.get(section)
    paths: list[tuple[YamlPath, YAMLValue, bool]] = []
    dependency = document.data.get("dependency")
    if isinstance(dependency, dict) and dependency.get("name") == "galaxy" and dependency.get("enabled") is not False:
        options = dependency.get("options")
        if isinstance(options, dict):
            paths.extend(
                (("dependency", "options", key), options[key], False)
                for key in ("role-file", "requirements-file")
                if key in options
            )
    playbooks = ansible.get("playbooks") if isinstance(ansible, dict) else None
    if isinstance(playbooks, dict):
        driver = document.data.get("driver")
        driver_name = driver.get("name") if isinstance(driver, dict) else "default"
        selected = playbooks.get(driver_name) if isinstance(driver_name, str) else None
        for stage, value in playbooks.items():
            path: YamlPath = (section, "playbooks", stage)
            if isinstance(value, dict):
                if not native and stage == driver_name:
                    paths.extend(((*path, name), item, True) for name, item in value.items() if name in _STAGES)
                elif native:
                    paths.append((path, value, True))
            elif stage in _STAGES:
                if not isinstance(selected, dict) or stage not in selected:
                    paths.append((path, value, True))
            elif path in document.origins:
                paths.append((path, value, True))
    inventory = provisioner.get("inventory") if isinstance(provisioner, dict) else None
    if isinstance(inventory, dict) and isinstance(inventory.get("links"), dict):
        links = cast(YAMLDict, inventory["links"])
        paths.extend((("provisioner", "inventory", "links", key), value, False) for key, value in links.items())
    findings: list[ViolationDict] = []
    checked: set[str] = set()
    for path, value, is_playbook in paths:
        verifier = document.data.get("verifier")
        if (
            path[-1] == "verify"
            and isinstance(verifier, dict)
            and (verifier.get("name") != "ansible" or verifier.get("enabled") is False)
        ):
            continue
        explicit = path in document.origins
        if value is None:
            continue
        origin = _origin(document, path, scenario)
        if not isinstance(value, str) or not value.strip():
            if explicit:
                findings.append(
                    _finding("L113", origin, "Configured file reference must be a non-empty string or null")
                )
            continue
        base = _project_root(scenario) if path[0] == "dependency" else str(PurePosixPath(scenario).parent)
        reference = _reference(base, value)
        if reference is None:
            if explicit and not _unknowns(value):
                findings.append(
                    _finding("L115", origin, "Configured reference cannot be resolved within the supplied snapshot")
                )
            continue
        if reference not in files and reference.endswith("/converge.yml"):
            fallback = reference.removesuffix("converge.yml") + "playbook.yml"
            if fallback in files:
                reference = fallback
        present = reference in files or (
            not is_playbook and any(name.startswith(reference.rstrip("/") + "/") for name in files)
        )
        if not present:
            if explicit:
                findings.append(
                    _finding(
                        "L113",
                        origin,
                        "Configured reference is absent from the supplied scan; it may be missing or excluded",
                    )
                )
            continue
        if is_playbook and reference in files and reference not in checked:
            checked.add(reference)
            findings.extend(_playbook(reference, files[reference]))
    return findings


def _sequences(document: _Document, scenario: str, files: Mapping[str, bytes]) -> list[ViolationDict]:
    """Validate action names and references for supported action arguments.

    Args:
        document: Effective scenario.
        scenario: Config file path.
        files: Supplied snapshot.

    Returns:
        Invalid-action and action-reference findings.
    """
    config = document.data.get("scenario")
    if not isinstance(config, dict):
        return []
    findings: list[ViolationDict] = []
    for key, sequence in config.items():
        if not isinstance(key, str) or not key.endswith("_sequence") or not isinstance(sequence, list):
            continue
        for index, value in enumerate(sequence):
            path = ("scenario", key, index)
            origin = _origin(document, path, scenario)
            if not isinstance(value, str) or _unknowns(value):
                continue
            action, *arguments = value.split(" ")
            if action not in _ACTIONS:
                findings.append(
                    _finding(
                        "L114",
                        origin,
                        "Unknown Molecule action; use a supported lifecycle action",
                    )
                )
                continue
            if action not in {"side_effect", "verify"}:
                continue
            verifier = document.data.get("verifier")
            if (
                action == "verify"
                and isinstance(verifier, dict)
                and (verifier.get("name") != "ansible" or verifier.get("enabled") is False)
            ):
                if verifier.get("enabled") is not False:
                    findings.append(
                        _finding("L115", origin, "Non-Ansible verifier code was not assessed as Ansible playbooks")
                    )
                continue
            for argument in arguments:
                if not argument:
                    continue
                reference = _reference(str(PurePosixPath(scenario).parent), argument)
                if reference is None:
                    findings.append(_finding("L115", origin, "Action argument requires unavailable path context"))
                elif reference not in files:
                    findings.append(
                        _finding("L113", origin, "Action playbook reference is absent from the supplied scan")
                    )
                else:
                    findings.extend(_playbook(reference, files[reference]))
    return findings


def validate_molecule(files: Mapping[str, bytes]) -> list[ViolationDict]:
    """Validate all supplied Molecule scenarios without requiring a graph.

    Args:
        files: Uploaded paths mapped to bytes; never server filesystem paths.

    Returns:
        Deterministically ordered, deduplicated L112-L116 findings.
    """
    scenarios = sorted(file for file in files if PurePosixPath(file).name in {"molecule.yml", "molecule.yaml"})
    if not scenarios:
        return []
    findings: list[ViolationDict] = []
    for scenario in scenarios:
        root = _project_root(scenario)
        directory = str(PurePosixPath(scenario).parent)
        variables = {
            "MOLECULE_PROJECT_DIRECTORY": posixpath.normpath(f"{_VIRTUAL_ROOT}/{root}"),
            "MOLECULE_SCENARIO_DIRECTORY": posixpath.normpath(f"{_VIRTUAL_ROOT}/{directory}"),
            "MOLECULE_SCENARIO_NAME": PurePosixPath(directory).name,
        }
        environment_file = posixpath.normpath(posixpath.join(root, ".env.yml"))
        if environment_file in files:
            try:
                environment = _load(environment_file, files[environment_file], {})
                variables.update(
                    (key, str(value))
                    for key, value in environment.data.items()
                    if isinstance(value, str | int | float | bool)
                )
            except (YAMLError, yaml.YAMLError, UnicodeError, ValueError, RecursionError):
                findings.append(
                    _finding(
                        "L115",
                        _Location(environment_file),
                        "Environment file could not be assessed; substitutions remain unknown",
                    )
                )
        supplied = _Document({}, {(): _Location(scenario)})
        base = posixpath.normpath(posixpath.join(root, ".config/molecule/config.yml"))
        layers = ([base] if base in files else []) + [scenario]
        valid = True
        for file in layers:
            try:
                _merge(supplied, _load(file, files[file], variables))
            except _NeedsContext:
                findings.append(
                    _finding("L115", _Location(file), "Configuration root requires unavailable environment context")
                )
                valid = False
            except (YAMLError, yaml.YAMLError, UnicodeError, ValueError, RecursionError) as error:
                mark = getattr(error, "problem_mark", None)
                findings.append(
                    _finding(
                        "L112",
                        _Location(file, mark.line + 1 if mark is not None else 1),
                        "Molecule config must contain valid YAML with unique keys and mapping structure",
                    )
                )
                valid = False
        if not valid:
            continue
        version = NATIVE_PROFILE_VERSION if {"ansible", "shared_state"} & supplied.data.keys() else PROFILE_VERSION
        if version == NATIVE_PROFILE_VERSION:
            _migrate_native(supplied)
        validator, defaults = _profile(version)
        effective = _Document(copy.deepcopy(defaults), {(): _Location(scenario)})
        _merge(effective, supplied)
        unknown = _unknowns(effective.data)
        for path in unknown:
            findings.append(
                _finding(
                    "L115",
                    _origin(effective, path, scenario),
                    "Value needs environment or template context; dependent checks are incomplete",
                )
            )
        for schema_error in validator.iter_errors(effective.data):
            path = tuple(schema_error.absolute_path)
            if any(path[: len(item)] == item for item in unknown):
                continue
            if (
                schema_error.validator == "additionalProperties"
                and isinstance(schema_error.instance, dict)
                and isinstance(schema_error.schema, dict)
            ):
                allowed = schema_error.schema.get("properties", {})
                for key in schema_error.instance:
                    if key not in allowed:
                        key_path = (*path, key)
                        if key_path in unknown:
                            continue
                        findings.append(
                            _finding(
                                "L112",
                                _origin(effective, key_path, scenario),
                                f"Configuration key is not supported by the Molecule {version} profile",
                            )
                        )
            else:
                findings.append(
                    _finding(
                        "L112",
                        _origin(effective, path, scenario),
                        f"Configuration violates Molecule {version} schema ({schema_error.validator})",
                    )
                )
        driver = effective.data.get("driver")
        if isinstance(driver, dict) and driver.get("name") != "default" and not _unknowns(driver.get("name")):
            findings.append(
                _finding(
                    "L115",
                    _origin(effective, ("driver", "name"), scenario),
                    "Core schema checked; external driver installation and driver-specific schema were not assessed",
                )
            )
        findings.extend(_references(effective, scenario, files))
        findings.extend(_sequences(effective, scenario, files))
    unique = {
        (str(item["rule_id"]), str(item["file"]), str(item["line"]), str(item["path"]), str(item["message"])): item
        for item in findings
    }
    return [unique[key] for key in sorted(unique)]
