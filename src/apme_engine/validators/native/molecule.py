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
from keyword import iskeyword
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
_RUNTIME_ROOT = "/__apme_molecule_runtime__"
_RUNTIME_PATHS = {
    "MOLECULE_EPHEMERAL_DIRECTORY": "",
    "MOLECULE_FILE": "molecule.yml",
    "MOLECULE_STATE_FILE": "state.yml",
    "MOLECULE_INVENTORY_FILE": "inventory/ansible_inventory.yml",
    "MOLECULE_INSTANCE_CONFIG": "instance_config.yml",
}
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
# Repeated protobuf strings do not retain Python's shared-string identity.
# Leave headroom below the validator's 50 MiB message limit for graph findings,
# metadata, and diagnostics. Charge escaped JSON as well as UTF-8 wire content.
_MAX_DIAGNOSTIC_BYTES = 32 * 1024 * 1024
_DIAGNOSTIC_OVERHEAD_BYTES = 1024
_NAMES = {
    "L115": "Molecule configuration does not match the supported profile",
    "L116": "Molecule file reference is unavailable in the scan",
    "L117": "Molecule scenario sequence contains an invalid action",
    "L118": "Molecule validation needs additional context",
    "L119": "Molecule scenario playbook has invalid YAML or play structure",
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


class MoleculeAssessmentLimitError(RuntimeError):
    """Complete Molecule findings cannot be serialized within safe limits."""


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


@dataclass
class _PlaybookAssessment:
    """Inert playbook structure and import references from one source.

    Attributes:
        findings: Structural diagnostics.
        imports: Source locations and literal, unrendered import values.
    """

    findings: list[ViolationDict] = field(default_factory=list)
    imports: list[tuple[_Location, str]] = field(default_factory=list)


PlaybookCache = dict[tuple[str, str], list[ViolationDict]]


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


@lru_cache(maxsize=2)
def _driver_profile(version: str) -> Draft7Validator:
    """Load the bundled built-in driver schema for a configuration family.

    Args:
        version: Bundled profile version.

    Returns:
        Offline default/delegated driver validator.
    """
    return Draft7Validator(json.loads((_ASSETS / f"driver-{version}.json").read_text()))


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


def _source_excerpt(lines: list[str], line: int) -> str:
    """Select original source at a diagnostic for content-based suppression.

    Args:
        lines: Unexpanded source lines, decoded once per source file.
        line: One-based diagnostic line.

    Returns:
        Original line and its indented children, or the document fallback.
    """
    start = max(0, line - 1)
    if start >= len(lines):
        return "\n".join(lines)
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent):
        end += 1
    return "\n".join(lines[start:end])


def _diagnostic_string_size(text: str) -> int:
    """Count escaped diagnostic bytes without allocating a large encoding.

    ASCII JSON escaping also bounds the UTF-8 strings copied into protobuf.
    Work is bounded by the output allowance even for a single large source line.

    Args:
        text: Full diagnostic text or original source excerpt.

    Returns:
        Escaped JSON string size including quotes.

    Raises:
        MoleculeAssessmentLimitError: If the string alone exceeds the allowance.
    """
    size = len(text) + 2
    if size > _MAX_DIAGNOSTIC_BYTES:
        raise MoleculeAssessmentLimitError(
            "Molecule diagnostics exceed safe serialization limits; assessment is incomplete"
        )
    size = 2
    for offset in range(0, len(text), 4096):
        size += len(json.encoder.encode_basestring_ascii(text[offset : offset + 4096])) - 2
        if size > _MAX_DIAGNOSTIC_BYTES:
            raise MoleculeAssessmentLimitError(
                "Molecule diagnostics exceed safe serialization limits; assessment is incomplete"
            )
    return size


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
            if isinstance(key, yaml.ScalarNode) and key.value == "<<":
                merged = value.value if isinstance(value, yaml.SequenceNode) else [value]
                for mapping in reversed(merged):
                    result.update(_locations(mapping, file, path, active, budget))
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode):
                raise ValueError("Complex YAML keys are not supported")
            if key.value == "<<":
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
            if match.group(2):
                return match.group(3) or ""
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
        yaml.YAMLError: If substituted content contains invalid YAML.
    """
    source = content.decode("utf-8")
    expanded = _expand(source, variables)
    try:
        expanded_node = yaml.compose(expanded, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        masked_valid = False
        if _ENV.search(expanded):
            try:
                yaml.compose(_ENV.sub("APME_ENV_VALUE", expanded), Loader=yaml.SafeLoader)
            except yaml.YAMLError:
                pass
            else:
                masked_valid = True
        if masked_valid:
            raise _NeedsContext("Configuration structure requires unavailable environment context") from None
        raise
    if expanded_node is not None:
        _locations(expanded_node, file)
    try:
        node = yaml.compose(source, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        # Interpolation precedes parsing in Molecule; flow-style ${VAR}
        # placeholders can be invalid YAML until expanded. Mask them only
        # for the original line map, never for validation.
        try:
            node = yaml.compose(_ENV.sub("APME_ENV_VALUE", source), Loader=yaml.SafeLoader)
        except yaml.YAMLError:
            node = None
    locations: dict[YamlPath, _Location] = _locations(node, file) if node is not None else {(): _Location(file)}
    loader = YAML(typ="safe", pure=True)
    loader.version = (1, 1)
    data: object = loader.load(expanded)
    if data is None:
        data = {}
    if isinstance(data, str) and _unknowns(data):
        raise _NeedsContext("Configuration root needs environment context")
    if not isinstance(data, dict) or any(not isinstance(key, str) for key in data):
        raise ValueError("Molecule configuration must be a mapping with string keys")
    return _Document(cast(YAMLDict, data), locations)


def _merge_values(target: YAMLDict, layer: YAMLDict, path: YamlPath, replaced: set[YamlPath]) -> None:
    """Merge values while recording subtrees whose previous origins are obsolete.

    Args:
        target: Effective configuration mapping to update.
        layer: New configuration mapping.
        path: Prefix for this recursive merge.
        replaced: Replaced subtree paths, updated in place.
    """
    for key, value in layer.items():
        current = (*path, key)
        previous = target.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            _merge_values(previous, value, current, replaced)
        else:
            target[key] = copy.deepcopy(value)
            replaced.add(current)


def _merge(target: _Document, layer: _Document) -> None:
    """Merge a config layer with one origin traversal per input document.

    Args:
        target: Effective configuration to update.
        layer: New configuration layer, which is not mutated.
    """
    replaced: set[YamlPath] = set()
    _merge_values(target.data, layer.data, (), replaced)
    for old_path in list(target.origins):
        if any(old_path[:depth] in replaced for depth in range(1, len(old_path) + 1)):
            del target.origins[old_path]
    target.origins.update((path, origin) for path, origin in layer.origins.items() if path)


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


def _mapping_items(node: yaml.MappingNode) -> dict[str, tuple[yaml.ScalarNode, yaml.Node]]:
    """Read effective mapping entries after the caller checks YAML limits.

    Args:
        node: Mapping already assessed for recursion and traversal limits.

    Returns:
        Effective scalar keys and source nodes, including safe YAML merges.
    """
    entries: dict[str, tuple[yaml.ScalarNode, yaml.Node]] = {}
    for key, value in node.value:
        if isinstance(key, yaml.ScalarNode) and key.value == "<<":
            merged = value.value if isinstance(value, yaml.SequenceNode) else [value]
            for mapping in reversed(merged):
                if isinstance(mapping, yaml.MappingNode):
                    entries.update(_mapping_items(mapping))
    entries.update(
        (key.value, (key, value)) for key, value in node.value if isinstance(key, yaml.ScalarNode) and key.value != "<<"
    )
    return entries


@dataclass
class _CollectionLookup:
    """Request-local collection lookup using only uploaded paths and metadata.

    Attributes:
        files: Uploaded snapshot, including collection metadata.
        directories: Directory membership index built once from uploaded paths.
        identities: Cached source-project identities, including failed parses.
    """

    files: Mapping[str, bytes]
    directories: set[str] = field(init=False)
    identities: dict[str, tuple[str, str] | None] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        """Index uploaded parent paths without reading a filesystem."""
        self.directories = {str(parent) for name in self.files for parent in PurePosixPath(name).parents}

    def _identity(self, project: str) -> tuple[str, str] | None:
        """Read a source collection identity once, caching unavailable metadata.

        Args:
            project: Uploaded project root.

        Returns:
            Declared namespace/name pair, if available.
        """
        if project in self.identities:
            return self.identities[project]
        self.identities[project] = None
        for name in ("galaxy.yml", "MANIFEST.json"):
            metadata_file = str(PurePosixPath(project, name))
            if metadata_file not in self.files:
                continue
            try:
                if name == "galaxy.yml":
                    metadata: object = _load(metadata_file, self.files[metadata_file], {}).data
                else:
                    manifest = json.loads(self.files[metadata_file])
                    metadata = manifest.get("collection_info") if isinstance(manifest, dict) else None
                if isinstance(metadata, dict):
                    namespace, collection = metadata.get("namespace"), metadata.get("name")
                    if isinstance(namespace, str) and isinstance(collection, str):
                        self.identities[project] = (namespace, collection)
                        break
            except (YAMLError, yaml.YAMLError, UnicodeError, ValueError, RecursionError):
                continue
        return self.identities[project]

    def resolve(self, value: str, scenario: str) -> tuple[bool, str | None]:
        """Resolve a qualified import inside the first available collection copy.

        Args:
            value: Literal import reference, possibly with a YAML extension.
            scenario: Scenario supplying the project root.

        Returns:
            Whether this is collection syntax and an available playbook path.
        """
        extension = next((suffix for suffix in (".yml", ".yaml") if value.endswith(suffix)), "")
        stem = value.removesuffix(extension) if extension else value
        match = re.fullmatch(r"(\w+)\.(\w+)\.(\w+(?:\.\w+)*)", stem)
        if match is None:
            return False, None
        namespace, collection, resource = match.groups()
        if any(not part.isidentifier() or iskeyword(part) for part in (namespace, collection)):
            return False, None
        project = _project_root(scenario)
        roots = [
            str(PurePosixPath(project, "collections/ansible_collections", namespace, collection)),
            str(PurePosixPath(project, "ansible_collections", namespace, collection)),
        ]
        parts = PurePosixPath(project).parts
        if "ansible_collections" in parts:
            index = parts.index("ansible_collections")
            roots.append(str(PurePosixPath(*parts[: index + 1], namespace, collection)))
        if self._identity(project) == (namespace, collection):
            roots.append(project)
        root = next((candidate for candidate in roots if candidate in self.directories), None)
        if root is None:
            return True, None
        target = str(PurePosixPath(root, "playbooks", resource.replace(".", "/") + extension))
        for candidate in (target,) if extension else (target, target + ".yml", target + ".yaml"):
            if candidate in self.files:
                return True, candidate
        return True, None


def _playbook_defaults(node: yaml.SequenceNode, file: str) -> list[ViolationDict]:
    """Check defaults and task-section shapes in executable playbook scopes.

    Args:
        node: Play list already checked for safe YAML traversal bounds.
        file: Snapshot-relative source file.

    Returns:
        L119 findings for malformed defaults or sections, without resolving plugins.
    """
    findings: list[ViolationDict] = []
    pending: list[tuple[yaml.Node, YamlPath]] = [(play, (index,)) for index, play in enumerate(node.value)]
    while pending:
        scope, path = pending.pop()
        if not isinstance(scope, yaml.MappingNode):
            continue
        entries = _mapping_items(scope)
        defaults_entry = entries.get("module_defaults")
        if defaults_entry is not None:
            key, value = defaults_entry
            if value.tag != "tag:yaml.org,2002:null":
                layers = value.value if isinstance(value, yaml.SequenceNode) else [value]
                valid = True
                for layer in layers:
                    if not isinstance(layer, yaml.MappingNode):
                        valid = False
                        break
                    for name, parameters in _mapping_items(layer).values():
                        if (
                            not isinstance(name, yaml.ScalarNode)
                            or name.tag != "tag:yaml.org,2002:str"
                            or not name.value
                            or any(marker in name.value for marker in ("{{", "{%", "{#"))
                            or not (
                                (
                                    isinstance(parameters, yaml.MappingNode)
                                    and all(
                                        parameter.tag == "tag:yaml.org,2002:str"
                                        for parameter, _ in _mapping_items(parameters).values()
                                    )
                                )
                                or (
                                    isinstance(parameters, yaml.ScalarNode)
                                    and any(marker in parameters.value for marker in ("{{", "{%", "{#"))
                                )
                            )
                        ):
                            valid = False
                            break
                if not valid:
                    findings.append(
                        _finding(
                            "L119",
                            _Location(file, key.start_mark.line + 1, (*path, "module_defaults")),
                            "Invalid module_defaults: expected static module/group names with parameter mappings",
                        )
                    )
        if len(path) == 3 and path[1] == "roles":
            # Other role-declaration keys may be role parameters, not task scopes.
            continue
        if len(path) == 1:
            roles = entries.get("roles")
            if roles is not None and isinstance(roles[1], yaml.SequenceNode):
                pending.extend((role, (*path, "roles", index)) for index, role in enumerate(roles[1].value))
        for section in ("pre_tasks", "tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            section_entry = entries.get(section)
            if section_entry is None:
                continue
            section_key, section_value = section_entry
            if isinstance(section_value, yaml.SequenceNode):
                pending.extend((task, (*path, section, index)) for index, task in enumerate(section_value.value))
            elif (
                len(path) == 1
                and section in {"pre_tasks", "tasks", "post_tasks", "handlers"}
                and section_value.tag == "tag:yaml.org,2002:null"
            ):
                # Plays use load_list_of_blocks, which accepts None; nested block
                # sections use load_list_of_tasks and require an actual list.
                continue
            else:
                findings.append(
                    _finding(
                        "L119",
                        _Location(file, section_key.start_mark.line + 1, (*path, section)),
                        f"Scenario task section '{section}' must be a list",
                    )
                )
        include_actions = {
            prefix + action
            for prefix in ("", "ansible.builtin.", "ansible.legacy.")
            for action in ("include_role", "include_tasks")
        }
        invocations = [(entries[action][1], (*path, action), False) for action in include_actions if action in entries]
        for form in ("action", "local_action"):
            if form not in entries:
                continue
            value = entries[form][1]
            if isinstance(value, yaml.MappingNode):
                module = _mapping_items(value).get("module")
                if (
                    module is not None
                    and isinstance(module[1], yaml.ScalarNode)
                    and module[1].value.split(maxsplit=1)[:1]
                    and module[1].value.split(maxsplit=1)[0] in include_actions
                ):
                    invocations.append((value, (*path, form), True))
            elif (
                isinstance(value, yaml.ScalarNode)
                and value.value.split(maxsplit=1)[:1]
                and value.value.split(maxsplit=1)[0] in include_actions
            ):
                invocations.append((value, (*path, form), True))
        for arguments, origin, old_style in invocations:
            apply_entry = _mapping_items(arguments).get("apply") if isinstance(arguments, yaml.MappingNode) else None
            if old_style and isinstance(arguments, yaml.MappingNode):
                nested = _mapping_items(arguments).get("args")
                if nested is not None and isinstance(nested[1], yaml.MappingNode):
                    nested_apply = _mapping_items(nested[1]).get("apply")
                    if nested_apply is not None:
                        apply_entry = nested_apply
                        origin = (*origin, "args")
            if apply_entry is None:
                task_args = entries.get("args")
                if task_args is not None and isinstance(task_args[1], yaml.MappingNode):
                    apply_entry = _mapping_items(task_args[1]).get("apply")
                    origin = (*path, "args")
            if apply_entry is not None:
                pending.append((apply_entry[1], (*origin, "apply")))
    return findings


def _playbook(file: str, content: bytes) -> _PlaybookAssessment:
    """Inspect scenario YAML structure without evaluating Ansible plugins.

    Args:
        file: Snapshot-relative playbook path.
        content: Playbook bytes.

    Returns:
        Structural findings and unrendered imports for static reference checks.
    """
    try:
        node = yaml.compose(content.decode("utf-8"), Loader=yaml.SafeLoader)
        if node is not None:
            _locations(node, file)
        if not isinstance(node, yaml.SequenceNode):
            return _PlaybookAssessment([_finding("L119", _Location(file), "Scenario playbook must be a list of plays")])
        findings = _playbook_defaults(node, file)
        imports: list[tuple[_Location, str]] = []
        for index, play in enumerate(node.value):
            entries = _mapping_items(play) if isinstance(play, yaml.MappingNode) else {}
            if not entries.keys() & {"hosts", "import_playbook", "ansible.builtin.import_playbook"}:
                findings.append(
                    _finding(
                        "L119",
                        _Location(file, play.start_mark.line + 1, (index,)),
                        "Scenario play must define hosts or import_playbook",
                    )
                )
            for key, value in entries.values():
                if isinstance(key, yaml.ScalarNode) and key.value in {
                    "import_playbook",
                    "ansible.builtin.import_playbook",
                }:
                    origin = _Location(file, key.start_mark.line + 1, (index, key.value))
                    if (
                        isinstance(value, yaml.ScalarNode)
                        and (value.tag == "tag:yaml.org,2002:str" or not value.tag.startswith("tag:yaml.org,2002:"))
                        and value.value.strip()
                    ):
                        imports.append((origin, value.value))
                    else:
                        findings.append(
                            _finding("L119", origin, "Scenario play import_playbook must be a non-empty string path")
                        )
        return _PlaybookAssessment(findings, imports)
    except (yaml.YAMLError, UnicodeError, ValueError, RecursionError) as error:
        mark = getattr(error, "problem_mark", None)
        return _PlaybookAssessment(
            [
                _finding(
                    "L119",
                    _Location(file, mark.line + 1 if mark is not None else 1),
                    "Scenario playbook contains invalid YAML",
                )
            ]
        )


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


def _assess_playbook(
    file: str, cache: PlaybookCache, scenario: str, collections: _CollectionLookup, active: frozenset[str] = frozenset()
) -> list[ViolationDict]:
    """Assess each referenced snapshot playbook at most once per request.

    Args:
        file: Snapshot-relative playbook.
        cache: Request-local findings keyed by source and scenario context.
        scenario: Scenario supplying the known Molecule context.
        collections: Request-local snapshot and collection lookup.
        active: Current import chain for cycle and depth limits.

    Returns:
        Cached structural findings, treated as immutable by callers.
    """
    if file in active:
        return [_finding("L119", _Location(file), "Scenario playbook imports form a cycle")]
    if len(active) > 64:
        return [_finding("L118", _Location(file), "Playbook import depth exceeds the static assessment limit")]
    key = (file, scenario)
    if key not in cache:
        files = collections.files
        assessment = _playbook(file, files[file])
        cache[key] = assessment.findings
        directories = {
            "project": posixpath.normpath(f"{_VIRTUAL_ROOT}/{_project_root(scenario)}"),
            "scenario": posixpath.normpath(f"{_VIRTUAL_ROOT}/{PurePosixPath(scenario).parent}"),
        }
        for origin, value in assessment.imports:
            value = re.sub(
                r"{{\s*molecule_(project|scenario)_directory\s*}}",
                lambda match: directories[match.group(1)],
                value,
            )
            reference = _reference(str(PurePosixPath(file).parent), value)
            is_collection, collection_reference = collections.resolve(value, scenario)
            if collection_reference is not None:
                reference = collection_reference
            elif is_collection and reference not in files:
                cache[key].append(
                    _finding(
                        "L118",
                        origin,
                        "Collection playbook content is unavailable in the supplied scan; "
                        "collection resolution was not assessed",
                    )
                )
                continue
            if reference is None:
                cache[key].append(
                    _finding(
                        "L118", origin, "Playbook import needs unavailable context; expressions were not evaluated"
                    )
                )
            elif reference not in files:
                cache[key].append(_finding("L116", origin, "Imported playbook is absent from the supplied scan"))
            else:
                # Keep each file's findings once in the request cache. Copying
                # transitive findings into each parent expands import DAGs.
                if reference in active | {file}:
                    cache[key].append(_finding("L119", origin, "Scenario playbook imports form a cycle"))
                elif len(active) >= 64:
                    cache[(reference, scenario)] = [
                        _finding("L118", origin, "Playbook import depth exceeds the static assessment limit")
                    ]
                else:
                    _assess_playbook(reference, cache, scenario, collections, active | {file})
    return cache[key]


def _references(
    document: _Document, scenario: str, files: Mapping[str, bytes], cache: PlaybookCache, collections: _CollectionLookup
) -> list[ViolationDict]:
    """Check configured playbooks, inventory links, and Galaxy requirement files.

    Args:
        document: Effective scenario config.
        scenario: Molecule config path.
        files: Complete supplied snapshot, including non-YAML artifacts.
        cache: Request-local playbook assessment cache.
        collections: Request-local collection lookup.

    Returns:
        Reference, context, and playbook findings.
    """
    provisioner = document.data.get("provisioner")
    native = "ansible" in document.data
    section = "ansible" if native else "provisioner"
    ansible = document.data.get(section)
    paths: list[tuple[YamlPath, YAMLValue, bool]] = []
    findings: list[ViolationDict] = []
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
            if _unknowns(str(stage)):
                continue
            path: YamlPath = (section, "playbooks", stage)
            if isinstance(value, dict):
                if not native and stage == driver_name:
                    paths.extend(((*path, name), item, True) for name, item in value.items() if name in _STAGES)
                elif native or stage in _STAGES:
                    paths.append((path, value, True))
            elif stage in _STAGES:
                if not isinstance(selected, dict) or stage not in selected:
                    paths.append((path, value, True))
            elif path in document.origins and not _unknowns(str(stage)):
                findings.append(_finding("L115", _origin(document, path, scenario), "Unknown Molecule playbook stage"))
    inventory = provisioner.get("inventory") if isinstance(provisioner, dict) else None
    if isinstance(inventory, dict) and isinstance(inventory.get("links"), dict):
        links = cast(YAMLDict, inventory["links"])
        paths.extend((("provisioner", "inventory", "links", key), value, False) for key, value in links.items())
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
                    _finding("L116", origin, "Configured file reference must be a non-empty string or null")
                )
            continue
        base = _project_root(scenario) if path[0] == "dependency" else str(PurePosixPath(scenario).parent)
        reference = _reference(base, value)
        if reference is None:
            if explicit and not _unknowns(value):
                findings.append(
                    _finding("L118", origin, "Configured reference cannot be resolved within the supplied snapshot")
                )
            continue
        if reference not in files and reference.endswith("/converge.yml"):
            fallback = reference.removesuffix("converge.yml") + "playbook.yml"
            if fallback in files:
                reference = fallback
        present = reference in files or (not is_playbook and reference.rstrip("/") in collections.directories)
        if not present:
            if explicit:
                message = "Configured reference is absent from the supplied scan; it may be missing or excluded"
                if is_playbook:
                    message += f" (scenario: {scenario})"
                    if path[-1] != "converge":
                        message += "; Molecule may skip this optional stage"
                findings.append(
                    _finding(
                        "L116",
                        origin,
                        message,
                    )
                )
            continue
        if is_playbook and reference in files and reference not in checked:
            checked.add(reference)
            findings.extend(_assess_playbook(reference, cache, scenario, collections))
    return findings


def _sequences(
    document: _Document, scenario: str, files: Mapping[str, bytes], cache: PlaybookCache, collections: _CollectionLookup
) -> list[ViolationDict]:
    """Validate action names and references for supported action arguments.

    Args:
        document: Effective scenario.
        scenario: Config file path.
        files: Supplied snapshot.
        cache: Request-local playbook assessment cache.
        collections: Request-local collection lookup.

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
                        "L117",
                        origin,
                        "Unknown Molecule action; use a supported lifecycle action",
                    )
                )
                continue
            if action not in {"side_effect", "verify"}:
                if any(arguments):
                    findings.append(_finding("L117", origin, "Playbook arguments are ignored by this lifecycle action"))
                continue
            verifier = document.data.get("verifier")
            if (
                action == "verify"
                and isinstance(verifier, dict)
                and (verifier.get("name") != "ansible" or verifier.get("enabled") is False)
            ):
                if verifier.get("enabled") is not False:
                    findings.append(
                        _finding("L118", origin, "Non-Ansible verifier code was not assessed as Ansible playbooks")
                    )
                continue
            for argument in arguments:
                if not argument:
                    continue
                reference = _reference(str(PurePosixPath(scenario).parent), argument)
                if reference is None:
                    findings.append(_finding("L118", origin, "Action argument requires unavailable path context"))
                elif reference not in files:
                    findings.append(
                        _finding("L116", origin, "Action playbook reference is absent from the supplied scan")
                    )
                else:
                    findings.extend(_assess_playbook(reference, cache, scenario, collections))
    return findings


def _molecule_assessment(files: Mapping[str, bytes]) -> tuple[list[ViolationDict], frozenset[str]]:
    """Validate all supplied Molecule scenarios without requiring a graph.

    Args:
        files: Uploaded paths mapped to bytes; never server filesystem paths.

    Returns:
        Deterministically ordered file findings and resolved static playbook paths.

    Raises:
        MoleculeAssessmentLimitError: If complete diagnostics exceed safe serialization limits.
    """
    scenarios = sorted(file for file in files if PurePosixPath(file).name in {"molecule.yml", "molecule.yaml"})
    if not scenarios:
        return [], frozenset()
    findings: list[ViolationDict] = []
    playbook_cache: PlaybookCache = {}
    collections = _CollectionLookup(files)
    for scenario in scenarios:
        root = _project_root(scenario)
        directory = str(PurePosixPath(scenario).parent)
        variables = {
            "MOLECULE_PROJECT_DIRECTORY": posixpath.normpath(f"{_VIRTUAL_ROOT}/{root}"),
            "MOLECULE_SCENARIO_DIRECTORY": posixpath.normpath(f"{_VIRTUAL_ROOT}/{directory}"),
            "MOLECULE_SCENARIO_NAME": PurePosixPath(directory).name,
            **{name: str(PurePosixPath(_RUNTIME_ROOT, suffix)) for name, suffix in _RUNTIME_PATHS.items()},
        }
        environment_values: dict[str, str] = {}
        environment_file = posixpath.normpath(posixpath.join(root, ".env.yml"))
        if environment_file in files:
            try:
                environment = _load(environment_file, files[environment_file], {})
                environment_values.update(
                    (key, str(value))
                    for key, value in environment.data.items()
                    if isinstance(value, str | int | float | bool)
                )
                variables.update(environment_values)
            except (YAMLError, yaml.YAMLError, UnicodeError, ValueError, RecursionError):
                findings.append(
                    _finding(
                        "L118",
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
                    _finding("L118", _Location(file), "Configuration root requires unavailable environment context")
                )
                valid = False
            except (YAMLError, yaml.YAMLError, UnicodeError, ValueError, RecursionError) as error:
                mark = getattr(error, "problem_mark", None)
                findings.append(
                    _finding(
                        "L115",
                        _Location(file, mark.line + 1 if mark is not None else 1),
                        "Molecule config must contain valid YAML with unique keys and mapping structure",
                    )
                )
                valid = False
        if not valid:
            continue
        version = NATIVE_PROFILE_VERSION if {"ansible", "shared_state"} & supplied.data.keys() else PROFILE_VERSION
        scenario_config = supplied.data.get("scenario")
        derived_name = PurePosixPath(directory).name
        if version == NATIVE_PROFILE_VERSION and "extensions/molecule/" in directory:
            derived_name = directory.rsplit("extensions/molecule/", 1)[-1] or derived_name
        name = scenario_config.get("name", derived_name) if isinstance(scenario_config, dict) else derived_name
        if (
            "MOLECULE_SCENARIO_NAME" not in environment_values
            and isinstance(name, str)
            and name != variables["MOLECULE_SCENARIO_NAME"]
        ):
            variables["MOLECULE_SCENARIO_NAME"] = name
            supplied = _Document({}, {(): _Location(scenario)})
            try:
                for file in layers:
                    _merge(supplied, _load(file, files[file], variables))
            except _NeedsContext:
                findings.append(
                    _finding("L118", _Location(file), "Configuration root requires unavailable environment context")
                )
                continue
            except (YAMLError, yaml.YAMLError, UnicodeError, ValueError, RecursionError):
                findings.append(
                    _finding(
                        "L115",
                        _Location(file),
                        "Molecule config must contain valid YAML with unique keys and mapping structure",
                    )
                )
                continue
        if version == NATIVE_PROFILE_VERSION:
            _migrate_native(supplied)
        validator, defaults = _profile(version)
        effective = _Document(copy.deepcopy(defaults), {(): _Location(scenario)})
        _merge(effective, supplied)
        unknown = _unknowns(effective.data)
        for path in unknown:
            findings.append(
                _finding(
                    "L118",
                    _origin(effective, path, scenario),
                    "Value needs environment or template context; dependent checks are incomplete",
                )
            )
        validators = [validator]
        driver = effective.data.get("driver")
        if isinstance(driver, dict) and driver.get("name") in ("default", "delegated"):
            validators.append(_driver_profile(version))
        for schema_error in (error for profile in validators for error in profile.iter_errors(effective.data)):
            path = tuple(schema_error.absolute_path)
            if any(path[: len(item)] == item for item in unknown):
                continue
            if (
                path[:1] == ("dependency",)
                and ("dependency", "name") in unknown
                and {"then", "else"}.intersection(schema_error.absolute_schema_path)
            ):
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
                                "L115",
                                _origin(effective, key_path, scenario),
                                f"Configuration key is not supported by the Molecule {version} profile",
                            )
                        )
            else:
                findings.append(
                    _finding(
                        "L115",
                        _origin(effective, path, scenario),
                        f"Configuration violates Molecule {version} schema ({schema_error.validator})",
                    )
                )
        if (
            isinstance(driver, dict)
            and driver.get("name") not in ("default", "delegated")
            and not _unknowns(driver.get("name"))
        ):
            findings.append(
                _finding(
                    "L118",
                    _origin(effective, ("driver", "name"), scenario),
                    "Core schema checked; external driver installation and driver-specific schema were not assessed",
                )
            )
        findings.extend(_references(effective, scenario, files, playbook_cache, collections))
        findings.extend(_sequences(effective, scenario, files, playbook_cache, collections))
    findings.extend(finding for cached in playbook_cache.values() for finding in cached)
    unique = {
        (str(item["rule_id"]), str(item["file"]), str(item["line"]), str(item["path"]), str(item["message"])): item
        for item in findings
    }
    result = [unique[key] for key in sorted(unique)]
    source_lines: dict[str, list[str]] = {}
    excerpts: dict[tuple[str, int], tuple[str, int]] = {}
    remaining = _MAX_DIAGNOSTIC_BYTES
    for finding in result:
        source_file = str(finding["file"])
        if source_file not in source_lines:
            source_lines[source_file] = files.get(source_file, b"").decode("utf-8", errors="replace").splitlines()
        line = int(str(finding.get("line") or 1))
        key = (source_file, line)
        if key not in excerpts:
            excerpt = _source_excerpt(source_lines[source_file], line)
            excerpts[key] = (excerpt, _diagnostic_string_size(excerpt))
        excerpt, excerpt_bytes = excerpts[key]
        # Charge every occurrence, even though Python findings share one string.
        remaining -= _DIAGNOSTIC_OVERHEAD_BYTES + excerpt_bytes
        for value in finding.values():
            if isinstance(value, str):
                remaining -= _diagnostic_string_size(value)
        if remaining < 0:
            raise MoleculeAssessmentLimitError(
                "Molecule diagnostics exceed safe serialization limits; assessment is incomplete"
            )
        finding["original_yaml"] = excerpt
    return result, frozenset(file for file, _scenario in playbook_cache)


def validate_molecule(files: Mapping[str, bytes]) -> list[ViolationDict]:
    """Validate supplied Molecule scenarios using only uploaded bytes.

    Oversized diagnostics propagate ``MoleculeAssessmentLimitError`` rather
    than returning partial findings or truncated suppression source.

    Args:
        files: Uploaded snapshot paths mapped to bytes.

    Returns:
        Deterministically ordered Native file findings.

    """
    return _molecule_assessment(files)[0]


def molecule_playbook_paths(files: Mapping[str, bytes]) -> frozenset[str]:
    """Identify resolved scenario references to exclude from runtime checks.

    Oversized diagnostics propagate ``MoleculeAssessmentLimitError`` so failed
    static assessment cannot silently fall back to runtime playbook execution.

    Args:
        files: Uploaded snapshot paths mapped to bytes.

    Returns:
        Snapshot-relative playbooks assessed statically, including shared files.

    """
    return _molecule_assessment(files)[1]
