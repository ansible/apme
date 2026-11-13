"""L057: Syntax check via ansible-playbook --syntax-check."""

import os
import posixpath
import re
import shlex
import subprocess
from collections import defaultdict, deque
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import yaml

from apme_engine.validators.native.molecule import molecule_playbook_paths

RULE_ID = "L057"

_MAX_SOURCE_BYTES = 1024 * 1024
_MAX_YAML_NODES = 50_000
_MAX_YAML_DEPTH = 128
_MAX_IMPORT_FILES = 4096
_MAX_IMPORT_BYTES = 32 * 1024 * 1024
_MAX_IMPORT_NODES = 200_000
_IMPORT_TASKS = frozenset({"import_tasks", "ansible.builtin.import_tasks", "ansible.legacy.import_tasks"})
_IMPORT_PLAYS = frozenset({"import_playbook", "ansible.builtin.import_playbook", "ansible.legacy.import_playbook"})
_IMPORT_ROLES = frozenset({"import_role", "ansible.builtin.import_role", "ansible.legacy.import_role"})
_TASK_SECTIONS = frozenset({"tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"})

_TASK_DIRS = {"tasks", "handlers"}
_PLAY_KEYS = frozenset(
    {
        "hosts",
        "tasks",
        "pre_tasks",
        "post_tasks",
        "handlers",
        "roles",
        *_IMPORT_PLAYS,
    }
)
_PLAY_MARKER = re.compile(
    r"^(?:-\s+| {0,2})(?:hosts|tasks|pre_tasks|post_tasks|handlers|roles|"
    r"(?:ansible\.(?:builtin|legacy)\.)?import_playbook)\s*:",
    re.MULTILINE,
)

_PLAY_STRUCTURE_PATTERNS = (
    "is not a valid attribute for a play",
    "hosts is not set",
    "a play is missing",
    "playbook must be a list of plays",
)


class _BoundedLoader(yaml.SafeLoader):
    """Compose inert YAML with limits enforced before recursive allocation."""

    def __init__(self, source: str, limit: int) -> None:
        """Initialize per-document traversal limits.

        Args:
            source: Untrusted YAML source, already bounded in size.
            limit: Remaining node allowance for this document.
        """
        super().__init__(source)
        self.nodes = 0
        self.depth = 0
        self.limit = limit

    def compose_node(self, parent: yaml.Node | None, index: object) -> yaml.Node:
        """Charge nodes and nesting before composing children or aliases.

        Args:
            parent: Parent YAML node.
            index: Mapping key or sequence offset supplied by PyYAML.

        Returns:
            An inert YAML node retaining aliases without expanding them.

        Raises:
            yaml.YAMLError: If composition exceeds its assessment limits.
        """
        self.nodes += 1
        if self.nodes > self.limit or self.depth >= _MAX_YAML_DEPTH:
            raise yaml.YAMLError("Static import assessment exceeds YAML limits")
        self.depth += 1
        try:
            return cast(yaml.Node, super().compose_node(parent, cast(int, index)))
        finally:
            self.depth -= 1


def _load_yaml(source: str, budget: list[int] | None = None) -> yaml.Node | None:
    """Parse bounded YAML without constructing tagged objects.

    Args:
        source: Unrendered YAML source.
        budget: Optional shared remaining node allowance for import assessment.

    Returns:
        Composed document, or None for an empty document.

    Raises:
        yaml.YAMLError: If source or parsing exceeds assessment limits.
    """
    if len(source.encode("utf-8")) > _MAX_SOURCE_BYTES:
        raise yaml.YAMLError("Static import source exceeds assessment limits")
    loader = _BoundedLoader(source, min(_MAX_YAML_NODES, budget[0]) if budget is not None else _MAX_YAML_NODES)
    try:
        return loader.get_single_node()
    finally:
        if budget is not None:
            budget[0] = max(0, budget[0] - loader.nodes)
        loader.dispose()


def _static_imports(source: str, budget: list[int], task_imports: set[str] | None = None) -> tuple[set[str], bool]:
    """Collect static imports only from executable YAML scopes.

    Args:
        source: Playbook or task-file YAML, with tags left inert.
        budget: Shared node allowance across all imported files.
        task_imports: Optional output set identifying task imports, whose relative
            resolution can depend on an invoking include stack.

    Returns:
        Literal import targets and whether any import cannot be assessed.
    """
    imports: set[str] = set()
    unknown = False
    mappings: dict[int, dict[str, yaml.Node]] = {}
    active: set[int] = set()
    remaining = _MAX_YAML_NODES

    def entries(node: yaml.Node | None) -> dict[str, yaml.Node]:
        """Read effective mappings with bounded, memoized YAML merge handling.

        Args:
            node: Mapping node, possibly containing merge aliases.

        Returns:
            Effective keys and their source nodes.

        Raises:
            yaml.YAMLError: If merges cycle or exceed the work allowance.
        """
        nonlocal remaining
        if not isinstance(node, yaml.MappingNode):
            return {}
        if id(node) in active:
            raise yaml.YAMLError("Cyclic static import mapping")
        if id(node) in mappings:
            return mappings[id(node)]
        active.add(id(node))
        result: dict[str, yaml.Node] = {}
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode) and key.value == "<<":
                layers = value.value if isinstance(value, yaml.SequenceNode) else [value]
                for layer in reversed(layers):
                    merged = entries(layer)
                    remaining -= len(merged)
                    if remaining < 0:
                        raise yaml.YAMLError("Static import merges exceed assessment limits")
                    result.update(merged)
        remaining -= len(node.value)
        if remaining < 0:
            raise yaml.YAMLError("Static import mappings exceed assessment limits")
        result.update(
            (key.value, value) for key, value in node.value if isinstance(key, yaml.ScalarNode) and key.value != "<<"
        )
        active.remove(id(node))
        mappings[id(node)] = result
        return result

    def words_args(words: list[str]) -> dict[str, yaml.Node]:
        """Normalize literal key/value arguments and free-form filenames.

        Args:
            words: Shell-split argument words; no shell or template is evaluated.

        Returns:
            Normalized arguments with free-form words assigned to _raw_params.
        """
        result: dict[str, yaml.Node] = {}
        raw: list[str] = []
        for word in words:
            if "=" in word:
                key, value = word.split("=", 1)
                result[key] = yaml.ScalarNode("tag:yaml.org,2002:str", value)
            else:
                raw.append(word)
        if raw:
            result["_raw_params"] = yaml.ScalarNode("tag:yaml.org,2002:str", " ".join(raw))
        return result

    def arguments(value: yaml.Node | None) -> dict[str, yaml.Node]:
        """Normalize direct argument mappings or scalar key/value syntax.

        Args:
            value: Module arguments before task-level args are merged.

        Returns:
            Literal argument nodes.
        """
        nonlocal unknown
        if value is None or value.tag == "tag:yaml.org,2002:null":
            return {}
        if isinstance(value, yaml.MappingNode):
            return dict(entries(value))
        if isinstance(value, yaml.ScalarNode) and value.tag == "tag:yaml.org,2002:str":
            if any(marker in value.value for marker in ("{{", "{%", "{#", "\\")):
                unknown = True
                return {}
            if "=" not in value.value and not any(quote in value.value for quote in ("'", '"')):
                # Ansible preserves raw filename spacing (including trailing
                # and repeated spaces) after removing leading separator spaces.
                return {"_raw_params": yaml.ScalarNode("tag:yaml.org,2002:str", value.value.lstrip(" "))}
            return words_args(shlex.split(value.value))
        unknown = True
        return {}

    def target(direct: dict[str, yaml.Node], args: yaml.Node | None = None) -> None:
        """Apply Ansible's direct-argument and raw-parameter precedence.

        Args:
            direct: Normalized direct arguments, which override task-level args.
            args: Lower-priority task-level argument mapping.
        """
        nonlocal unknown
        values = dict(entries(args))
        if args is not None and not isinstance(args, yaml.MappingNode) and args.tag != "tag:yaml.org,2002:null":
            unknown = True
        values.update(direct)
        raw = values.get("_raw_params")
        raw_empty = (
            raw is None
            or raw.tag == "tag:yaml.org,2002:null"
            or (
                isinstance(raw, yaml.ScalarNode)
                and (
                    not raw.value
                    or (raw.tag == "tag:yaml.org,2002:bool" and raw.value.lower() in {"false", "no", "off"})
                )
            )
        )
        selected = values.get("file") if raw_empty else raw
        if isinstance(selected, yaml.ScalarNode) and selected.tag == "tag:yaml.org,2002:str" and selected.value:
            imports.add(selected.value)
            if task_imports is not None:
                task_imports.add(selected.value)
        else:
            unknown = True

    try:
        document = _load_yaml(source, budget)
        pending = list(document.value) if isinstance(document, yaml.SequenceNode) else [document]
        seen: set[int] = set()
        while pending:
            scope = pending.pop()
            if scope is None or id(scope) in seen:
                continue
            seen.add(id(scope))
            values = entries(scope)
            if _IMPORT_ROLES.intersection(values) or (
                "roles" in values
                and values["roles"].tag != "tag:yaml.org,2002:null"
                and not (isinstance(values["roles"], yaml.SequenceNode) and not values["roles"].value)
            ):
                # Static roles load tasks, handlers and dependencies. Without
                # a complete role invocation index their closure is unknown.
                unknown = True
            for action in _IMPORT_PLAYS:
                if action in values:
                    value = values[action]
                    if isinstance(value, yaml.ScalarNode) and value.tag == "tag:yaml.org,2002:str" and value.value:
                        imports.add(value.value)
                    else:
                        unknown = True
            for action in _IMPORT_TASKS:
                if action in values:
                    target(arguments(values[action]), values.get("args"))
            for action_key in ("action", "local_action"):
                action_node = values.get(action_key)
                if isinstance(action_node, yaml.MappingNode):
                    action_args = entries(action_node)
                    module = action_args.get("module")
                    if isinstance(module, yaml.ScalarNode):
                        if "\\" in module.value:
                            unknown = True
                            continue
                        words = shlex.split(module.value)
                        if words and words[0] in _IMPORT_TASKS:
                            direct = {key: value for key, value in action_args.items() if key not in {"module", "args"}}
                            tail = module.value.lstrip(" ")[len(words[0]) :].lstrip(" ")
                            direct.update(arguments(yaml.ScalarNode("tag:yaml.org,2002:str", tail)))
                            direct.update(arguments(action_args.get("args")))
                            target(direct, values.get("args"))
                        elif words and words[0] in _IMPORT_ROLES:
                            unknown = True
                elif isinstance(action_node, yaml.ScalarNode):
                    if "\\" in action_node.value:
                        unknown = True
                        continue
                    words = shlex.split(action_node.value)
                    if words and words[0] in _IMPORT_TASKS:
                        tail = action_node.value.lstrip(" ")[len(words[0]) :].lstrip(" ")
                        target(arguments(yaml.ScalarNode("tag:yaml.org,2002:str", tail)), values.get("args"))
                    elif words and words[0] in _IMPORT_ROLES:
                        unknown = True
            for section in _TASK_SECTIONS:
                child = values.get(section)
                if isinstance(child, yaml.SequenceNode):
                    pending.extend(child.value)
        return imports, unknown
    except (yaml.YAMLError, ValueError, RecursionError):
        return imports, True


def _excluded_importers(
    playbooks: list[Path], root: Path, static_paths: frozenset[str], files: Mapping[str, bytes]
) -> tuple[set[str], set[str]]:
    """Propagate static-only exclusions backwards over a bounded import graph.

    Args:
        playbooks: Runtime candidates found on disk.
        root: Uploaded project root.
        static_paths: Scenario playbooks and their Native-discovered imports.
        files: Complete uploaded snapshot used for dependency reads.

    Returns:
        Excluded paths and paths whose closure could not be assessed completely.
    """
    scenario_dirs = {
        posixpath.dirname(name) for name in files if posixpath.basename(name) in {"molecule.yml", "molecule.yaml"}
    }
    if not static_paths and not scenario_dirs:
        return set(), set()
    lexical_root = root.absolute()
    root = root.resolve()
    parents: dict[str, set[str]] = defaultdict(set)
    blocked = set(static_paths)
    incomplete: set[str] = set()
    imported_task_files: set[str] = set()
    relative_task_sources: set[str] = set()
    entrypoints = {path.absolute().relative_to(lexical_root).as_posix() for path in playbooks}
    pending = deque(sorted(entrypoints))
    visited: set[str] = set()
    remaining_bytes = _MAX_IMPORT_BYTES
    budget = [_MAX_IMPORT_NODES]

    def scenario_content(name: str) -> bool:
        """Check indexed ancestors without scanning every scenario per import.

        Args:
            name: Normalized project-relative file path.

        Returns:
            Whether a parent directory contains scenario configuration.
        """
        directory = posixpath.dirname(name)
        while directory:
            if directory in scenario_dirs:
                return True
            directory = posixpath.dirname(directory)
        return "" in scenario_dirs

    while pending:
        name = pending.popleft()
        if name in visited:
            continue
        visited.add(name)
        if name in blocked or scenario_content(name):
            blocked.add(name)
            continue
        source = files.get(name)
        if source is None:
            incomplete.add(name)
            continue
        materialized = (root / name).resolve()
        if not materialized.is_relative_to(root):
            incomplete.add(name)
            continue
        materialized_name = materialized.relative_to(root).as_posix()
        if materialized_name in static_paths or scenario_content(materialized_name):
            blocked.add(name)
            continue
        remaining_bytes -= len(source)
        if len(visited) > _MAX_IMPORT_FILES or remaining_bytes < 0 or len(source) > _MAX_SOURCE_BYTES:
            incomplete.add(name)
            continue
        task_imports: set[str] = set()
        targets, unknown = _static_imports(source.decode("utf-8", errors="replace"), budget, task_imports)
        if unknown:
            incomplete.add(name)
        for raw in targets:
            if any(marker in raw for marker in ("{{", "{%", "{#", "$", "~", "\\")):
                incomplete.add(name)
                continue
            if raw in task_imports and not Path(raw).is_absolute():
                relative_task_sources.add(name)
                # Ansible resolves nested task imports using the invoking
                # include stack, not necessarily this file's physical parent.
                # Even an existing sibling or ancestor is insufficient proof.
                if name not in entrypoints:
                    incomplete.add(name)
            try:
                candidate = Path(raw) if Path(raw).is_absolute() else root / posixpath.dirname(name) / raw
                candidate = candidate.resolve()
            except (OSError, RuntimeError, ValueError):
                incomplete.add(name)
                continue
            if not candidate.is_relative_to(root):
                incomplete.add(name)
                continue
            dependency = candidate.relative_to(root).as_posix()
            dependencies = {dependency}
            if dependency not in files and not Path(raw).is_absolute():
                if raw in task_imports:
                    incomplete.add(name)
                # Ansible can fall back to enclosing include/playbook bases.
                # Shared files lack invocation metadata here, so inspect each
                # existing ancestor candidate rather than guessing one base.
                directory = posixpath.dirname(name)
                while directory:
                    directory = posixpath.dirname(directory)
                    try:
                        fallback = (root / directory / raw).resolve()
                    except (OSError, RuntimeError, ValueError):
                        incomplete.add(name)
                        continue
                    if fallback.is_relative_to(root):
                        fallback_name = fallback.relative_to(root).as_posix()
                        if fallback_name in files:
                            dependencies.add(fallback_name)
                if len(dependencies) == 1 and re.fullmatch(r"\w+\.\w+\.\w+(?:\.\w+)*", raw):
                    # Collection-qualified imports need unavailable loader context.
                    incomplete.add(name)
                    continue
            for dependency in dependencies:
                parents[dependency].add(name)
                if raw in task_imports:
                    imported_task_files.add(dependency)
                if dependency in files or dependency in static_paths or scenario_content(dependency):
                    pending.append(dependency)
    # Candidate discovery is heuristic: a task file can also look like a
    # playbook. An incoming task-import edge still makes its relative imports
    # caller-dependent, even if it was parsed earlier as a candidate.
    incomplete.update(imported_task_files & relative_task_sources)
    for excluded in (blocked, incomplete):
        pending.extend(excluded)
        while pending:
            for parent in parents.get(pending.popleft(), ()):
                if parent not in excluded:
                    excluded.add(parent)
                    pending.append(parent)
    return blocked | incomplete, incomplete


def _is_task_file(path: Path) -> bool:
    """Return True if the file lives in a tasks/ or handlers/ directory.

    Args:
        path: File path to check.

    Returns:
        True if the file's immediate parent directory matches a known task directory.
    """
    return path.parent.name in _TASK_DIRS


def _is_play_structure_error(stderr: str) -> bool:
    """Return True if the error is about play structure (expected for task files).

    Args:
        stderr: Captured stderr from ansible-playbook --syntax-check.

    Returns:
        True if the error matches a known play-structure pattern.
    """
    lower = stderr.lower()
    return any(p in lower for p in _PLAY_STRUCTURE_PATTERNS)


def _find_playbooks(root: Path) -> list[Path]:
    """Return paths to YAML files that look like playbooks.

    Args:
        root: Root directory to search.

    Returns:
        List of paths to playbook-like YAML files.
    """
    playbooks = []
    resolved_root = root.resolve()
    for ext in ("*.yml", "*.yaml"):
        for path in sorted(root.rglob(ext)):
            if not path.is_file() or not path.resolve().is_relative_to(resolved_root):
                continue
            try:
                with path.open("rb") as stream:
                    text = stream.read(_MAX_SOURCE_BYTES + 1).decode("utf-8", errors="replace")
                if _looks_like_playbook(text):
                    playbooks.append(path)
            except OSError:
                pass
    return playbooks


def _looks_like_playbook(text: str) -> bool:
    """Recognize runtime syntax candidates without confusing config with plays.

    BaseLoader treats Ansible YAML tags as inert data. Malformed candidates
    with play markers are retained so the syntax checker can diagnose them.
    Import-only playbooks are candidates too; dispatch separately excludes
    Molecule content so those files receive static Native checks instead.

    Args:
        text: YAML document source.

    Returns:
        Whether the document is a playbook or a malformed playbook candidate.
    """
    try:
        document = _load_yaml(text)
    except (yaml.YAMLError, RecursionError):
        return bool(_PLAY_MARKER.search(text))
    if isinstance(document, yaml.MappingNode):
        return any(isinstance(key, yaml.ScalarNode) and key.value == "hosts" for key, _ in document.value)
    plays = document.value if isinstance(document, yaml.SequenceNode) else [document]
    return any(
        isinstance(play, yaml.MappingNode)
        and any(isinstance(key, yaml.ScalarNode) and key.value in _PLAY_KEYS for key, _ in play.value)
        for play in plays
    )


def _is_molecule_content(path: Path, root: Path) -> bool:
    """Identify scenario content that must receive only static file checks.

    Args:
        path: Candidate playbook.
        root: Uploaded project root.

    Returns:
        Whether an ancestor within the project contains Molecule config.
    """
    for directory in path.parents:
        if not directory.is_relative_to(root):
            break
        if any((directory / name).is_file() for name in ("molecule.yml", "molecule.yaml")):
            return True
    return False


def run(
    venv_root: Path,
    root_dir: Path,
    env_extra: dict[str, str] | None = None,
    file_snapshot: Mapping[str, bytes] | None = None,
    **_kwargs: object,
) -> list[dict[str, object]]:
    """Run runtime syntax checks on ordinary playbooks under root_dir.

    Molecule scenario content is assessed statically by Native: syntax-check
    can evaluate project lookups and requires Molecule execution context.

    Args:
        venv_root: Path to ansible venv root.
        root_dir: Root directory containing playbooks.
        env_extra: Optional extra environment variables.
        file_snapshot: Uploaded bytes for complete static scenario exclusions.
        **_kwargs: Ignored keyword arguments.

    Returns:
        List of violation dicts.
    """
    ansible_playbook = venv_root / "bin" / "ansible-playbook"
    violations: list[dict[str, object]] = []

    if not ansible_playbook.exists():
        violations.append(
            {
                "rule_id": RULE_ID,
                "severity": "error",
                "message": f"ansible-playbook not found: {ansible_playbook}",
                "file": "",
                "line": 1,
                "path": "",
                "scope": "playbook",
            }
        )
        return violations

    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)

    static_paths: frozenset[str] = frozenset()
    if file_snapshot is not None:
        static_paths = molecule_playbook_paths(file_snapshot)
    elif any(path.is_file() for name in ("molecule.yml", "molecule.yaml") for path in root_dir.rglob(name)):
        return [
            {
                "rule_id": RULE_ID,
                "severity": "error",
                "file": "",
                "line": 1,
                "path": "",
                "scope": "playbook",
                "message": "Molecule runtime exclusions require the complete uploaded snapshot; "
                "runtime syntax checks were skipped",
            }
        ]

    playbooks = _find_playbooks(root_dir)
    excluded, incomplete = (
        _excluded_importers(playbooks, root_dir, static_paths, file_snapshot)
        if file_snapshot is not None
        else (set(), set())
    )
    for playbook_path in playbooks:
        rel_path = str(playbook_path.relative_to(root_dir)) if root_dir in playbook_path.parents else str(playbook_path)
        if rel_path in incomplete:
            violations.append(
                {
                    "rule_id": RULE_ID,
                    "severity": "error",
                    "file": rel_path,
                    "line": 1,
                    "path": "",
                    "scope": "playbook",
                    "message": "Runtime syntax assessment is incomplete: the static import or role closure "
                    "could not be established safely; syntax-check was skipped",
                }
            )
            continue
        if rel_path in static_paths or rel_path in excluded or _is_molecule_content(playbook_path, root_dir):
            continue
        try:
            result = subprocess.run(
                [str(ansible_playbook), "--syntax-check", str(playbook_path)],
                capture_output=True,
                text=True,
                cwd=str(root_dir),
                timeout=60,
                env=env,
            )
        except subprocess.TimeoutExpired:
            violations.append(
                {
                    "rule_id": RULE_ID,
                    "severity": "error",
                    "message": "ansible-playbook --syntax-check timed out",
                    "file": rel_path,
                    "line": 1,
                    "path": "",
                    "scope": "playbook",
                }
            )
            continue

        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip()
            if _is_task_file(playbook_path) and _is_play_structure_error(stderr):
                continue
            line = 1
            line_match = re.search(r"\bline\s+(\d+)\b", stderr, re.I)
            if line_match:
                line = int(line_match.group(1))
            violations.append(
                {
                    "rule_id": RULE_ID,
                    "severity": "error",
                    "message": stderr or "syntax check failed",
                    "file": rel_path,
                    "line": line,
                    "path": "",
                    "scope": "playbook",
                }
            )

    return violations
