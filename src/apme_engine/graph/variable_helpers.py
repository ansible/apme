"""Shared Jinja variable-extraction and sensitivity helpers for graph rules.

Root-level Jinja reference extraction (``collect_strings``, ``extract_jinja_refs``,
``extract_bare_refs``) is used by L039 and R402.  L110 keeps a local
path-aware extractor (``_extract_jinja_vars``) because it must match dotted
paths such as ``vault.db_password``; the shared extractors intentionally return
root identifiers only.

``no_log_true_in_scope`` is shared by L110 and R404.  Sensitivity name/value
checks (``var_looks_sensitive``, ``value_looks_sensitive``) live in
``apme_engine.graph.sensitivity`` and are imported directly by L110/R404.
"""

from __future__ import annotations

import re

from apme_engine.graph.content_graph import ContentGraph, EdgeType, NodeType
from apme_engine.graph.sensitivity import var_looks_sensitive

TASK_TYPES: frozenset[NodeType] = frozenset({NodeType.TASK, NodeType.HANDLER})

_JINJA_VAR_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_JINJA_DOTTED_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)")
_JINJA_ATTR_RE = re.compile(r"\[['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]")
_DOTTED_PATH_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)\b")
_BARE_BRACKET_PATH_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\[['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]\]")
_BARE_IDENT_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")

_JINJA_BUILTINS: frozenset[str] = frozenset(
    {
        "and",
        "or",
        "not",
        "in",
        "is",
        "if",
        "else",
        "elif",
        "for",
        "import",
        "as",
        "with",
        "bool",
        "true",
        "false",
        "True",
        "False",
        "none",
        "None",
        "null",
        "Null",
        "int",
        "float",
        "string",
        "list",
        "dict",
        "length",
        "lower",
        "upper",
        "default",
        "defined",
        "undefined",
        "sameas",
        "mapping",
        "iterable",
        "sequence",
        "number",
        "match",
        "search",
        "regex",
        "select",
        "reject",
        "map",
        "sort",
        "join",
        "first",
        "last",
        "min",
        "max",
        "abs",
        "round",
        "trim",
        "replace",
        "split",
        "unique",
        "flatten",
        "combine",
        "mandatory",
        "ternary",
        "from_json",
        "from_yaml",
        "to_json",
        "to_yaml",
        "to_nice_json",
        "to_nice_yaml",
        "to_datetime",
        "to_uuid",
        "b64encode",
        "b64decode",
        "hash",
        "type_debug",
        "ipaddr",
        "ipv4",
        "ipv6",
        "basename",
        "dirname",
        "realpath",
        "relpath",
        "expanduser",
        "expandvars",
        "fileglob",
        "splitext",
        "win_basename",
        "win_dirname",
        "win_splitdrive",
        "regex_replace",
        "regex_search",
        "regex_findall",
        "regex_escape",
        "password_hash",
        "comment",
        "subelements",
        "product",
        "zip",
        "zip_longest",
        "json_query",
        "items2dict",
        "dict2items",
        "groupby",
        "selectattr",
        "rejectattr",
        "extract",
        "symmetric_difference",
        "difference",
        "intersect",
        "union",
        "community",
        "succeeded",
        "failed",
        "changed",
        "skipped",
        "success",
        "failure",
        "unreachable",
        "human_readable",
        "human_to_bytes",
        "shuffle",
        "log",
        "pow",
        "root",
        "urlsplit",
        "urlencode",
        "ansible_native",
        "checksum",
        "strftime",
        "wordcount",
        "xmlattr",
    }
)

_QUOTED_STRING_RE = re.compile(r"""(?:'[^']*'|"[^"]*")""")
_DOTTED_ATTR_RE = re.compile(r"\.([a-zA-Z_][a-zA-Z0-9_]*)")
_PIPE_FILTER_RE = re.compile(r"\|\s*([a-zA-Z_][a-zA-Z0-9_]*)")


def extract_jinja_refs(texts: list[str]) -> set[str]:
    """Extract variable identifiers from Jinja expressions.

    Parses each complete ``{{ ... }}`` body with the same bare-expression
    extractor used for ``when`` / ``changed_when`` so filter arguments
    (for example ``default(fallback)``) and multiline expressions are
    included.

    Args:
        texts: Strings that may contain ``{{ ... }}`` expressions.

    Returns:
        Set of variable names referenced in the Jinja expressions.
    """
    refs: set[str] = set()
    for text in texts:
        for m in _JINJA_VAR_RE.findall(text):
            refs.update(extract_bare_refs([m.strip()]))
    return refs


def sensitive_root_refs(texts: list[str]) -> set[str]:
    """Return root identifiers whose Jinja usage includes a sensitive path.

    Captures dotted access (``vault.db_password``) and bracket keys
    (``credentials['token']``) in addition to simple variable names.

    Args:
        texts: Strings that may contain ``{{ ... }}`` expressions.

    Returns:
        Root variable names that should be redacted in audit output.
    """
    roots: set[str] = set()
    for text in texts:
        for match in _JINJA_DOTTED_VAR_RE.finditer(text):
            path = match.group(1)
            if var_looks_sensitive(path):
                roots.add(path.split(".")[0].split("[")[0])
        for block_match in _JINJA_VAR_RE.finditer(text):
            block_content = block_match.group(1)
            for dotted_match in _DOTTED_PATH_RE.finditer(block_content):
                path = dotted_match.group(1)
                if var_looks_sensitive(path):
                    roots.add(path.split(".")[0].split("[")[0])
            for attr_match in _JINJA_ATTR_RE.finditer(block_content):
                if var_looks_sensitive(attr_match.group(1)):
                    root = block_content.split("|")[0].split(".")[0].split("[")[0].strip()
                    if root and root.isidentifier():
                        roots.add(root)
    return roots


def sensitive_roots_from_bare(texts: list[str]) -> set[str]:
    """Return root identifiers referenced via sensitive dotted/bare expressions.

    Args:
        texts: Bare Jinja expression strings (``when``, ``changed_when``, etc.).

    Returns:
        Root variable names that should be redacted in audit output.
    """
    roots: set[str] = set()
    for text in texts:
        for match in _DOTTED_PATH_RE.finditer(text):
            path = match.group(1)
            if var_looks_sensitive(path):
                roots.add(path.split(".")[0].split("[")[0])
        for match in _BARE_BRACKET_PATH_RE.finditer(text):
            root = match.group(1)
            key = match.group(2)
            if var_looks_sensitive(key) or var_looks_sensitive(f"{root}.{key}"):
                roots.add(root)
    return roots


def extract_bare_refs(texts: list[str]) -> set[str]:
    """Extract identifiers from bare Jinja expressions (no ``{{ }}``).

    Used for ``when``, ``changed_when``, ``failed_when`` which are
    implicitly Jinja — Ansible evaluates them as expressions without
    requiring ``{{ }}`` wrappers.

    Strips quoted strings, dotted attribute names, and Jinja filter names
    (identifiers following ``|``) before extraction so that ``'RedHat'``,
    ``.rc``, and ``| to_datetime`` are not treated as variables.

    Args:
        texts: Bare expression strings.

    Returns:
        Set of identifier names minus Jinja builtins/operators.
    """
    refs: set[str] = set()
    for text in texts:
        stripped = _QUOTED_STRING_RE.sub("", text)
        dotted_attrs = {m.group(1) for m in _DOTTED_ATTR_RE.finditer(stripped)}
        pipe_filters = {m.group(1) for m in _PIPE_FILTER_RE.finditer(stripped)}
        for ident in _BARE_IDENT_RE.findall(stripped):
            if (
                ident not in _JINJA_BUILTINS
                and ident not in dotted_attrs
                and ident not in pipe_filters
                and not ident[0].isdigit()
            ):
                refs.add(ident)
    return refs


def collect_timed_strings(
    node: object,
    *,
    include_loop: bool = True,
    include_task_variables: bool = True,
) -> tuple[list[str], list[str], list[str]]:
    """Gather string fields split by evaluation timing (pre-task vs post-task).

    Args:
        node: A ContentNode (duck-typed to avoid circular import in tests).
        include_loop: When False, skip ``loop`` expressions (L039 avoids
            flagging loop iteration sources as undefined uses).
        include_task_variables: When False, skip task-level ``variables``.

    Returns:
        Tuple of (pre_task_templates, pre_task_bare, post_task_bare).
        Pre-task fields are evaluated before the task result exists.
        ``changed_when`` and ``failed_when`` are post-task and may reference
        ``register``.
    """
    pre_templates: list[str] = []
    pre_bare: list[str] = []
    post_bare: list[str] = []

    when_expr = getattr(node, "when_expr", None)
    if when_expr:
        if isinstance(when_expr, list):
            pre_bare.extend(str(w) for w in when_expr)
        else:
            pre_bare.append(str(when_expr))

    name = getattr(node, "name", None)
    if isinstance(name, str):
        pre_templates.append(name)

    mo = getattr(node, "module_options", None)
    if isinstance(mo, dict):
        _collect_dict_strings(mo, pre_templates)

    for attr in ("changed_when", "failed_when"):
        val = getattr(node, attr, None)
        if isinstance(val, str):
            post_bare.append(val)
        elif isinstance(val, list):
            post_bare.extend(str(v) for v in val)

    env = getattr(node, "environment", None)
    if isinstance(env, dict):
        _collect_dict_strings(env, pre_templates)

    loop = getattr(node, "loop", None)
    if include_loop:
        if isinstance(loop, str):
            pre_templates.append(loop)
        elif isinstance(loop, list):
            pre_templates.extend(str(item) for item in loop)

    variables = getattr(node, "variables", None)
    if include_task_variables and isinstance(variables, dict):
        _collect_dict_strings(variables, pre_templates)

    return pre_templates, pre_bare, post_bare


def collect_strings(
    node: object,
    *,
    include_loop: bool = True,
    include_task_variables: bool = True,
) -> tuple[list[str], list[str]]:
    """Gather string fields from a node, split by expression type.

    Args:
        node: A ContentNode (duck-typed to avoid circular import in tests).
        include_loop: When False, skip ``loop`` expressions (L039 avoids
            flagging loop iteration sources as undefined uses).
        include_task_variables: When False, skip task-level ``variables``.

    Returns:
        Tuple of (template_strings, bare_expression_strings).
        Template strings may contain ``{{ }}``; bare expression strings
        are implicitly Jinja (``when``, ``changed_when``, ``failed_when``).
    """
    pre_templates, pre_bare, post_bare = collect_timed_strings(
        node,
        include_loop=include_loop,
        include_task_variables=include_task_variables,
    )
    return pre_templates, pre_bare + post_bare


def _collect_dict_strings(d: dict[str, object], out: list[str]) -> None:
    """Recursively collect string values from a nested dict.

    Args:
        d: Dictionary to traverse.
        out: Accumulator list for discovered strings.
    """
    for v in d.values():
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            _collect_dict_strings(v, out)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    _collect_dict_strings(item, out)


_POSITIONAL_EDGE_TYPES = frozenset(
    {
        EdgeType.CONTAINS.value,
        EdgeType.INCLUDE.value,
        EdgeType.IMPORT.value,
    }
)


def _sorted_positional_parent_ids(graph: ContentGraph, node_id: str) -> list[str]:
    """Return sorted positional parent node IDs for ``node_id``.

    Args:
        graph: ContentGraph for the scan.
        node_id: Node whose incoming positional edges are collected.

    Returns:
        Parent node IDs sorted lexicographically.
    """
    return sorted(
        src for src, _, data in graph.g.in_edges(node_id, data=True) if data.get("edge_type") in _POSITIONAL_EDGE_TYPES
    )


def _no_log_any_play_scoped_path(graph: ContentGraph, node_id: str, play_scope: set[str]) -> bool:
    """Return True when any ancestor path within ``play_scope`` has effective no_log.

    Shared included tasks can have multiple include parents within one play.
    Redact when any in-scope path inherits ``no_log: true``.

    Args:
        graph: ContentGraph for the scan.
        node_id: Task or handler node id.
        play_scope: Precomputed play-scoped node IDs.

    Returns:
        True when any in-scope ancestor path resolves ``no_log`` to true.
    """
    seen_paths: set[tuple[str, ...]] = set()

    def walk(current_id: str, path: tuple[str, ...]) -> bool:
        if current_id in path:
            return False
        next_path = path + (current_id,)
        if next_path in seen_paths:
            return False
        seen_paths.add(next_path)

        node = graph.get_node(current_id)
        if node is None:
            return False
        if node.no_log is False:
            return False
        if node.no_log is True:
            return True

        scoped_parents = [parent for parent in _sorted_positional_parent_ids(graph, current_id) if parent in play_scope]
        if not scoped_parents:
            return False
        return any(walk(parent, next_path) for parent in scoped_parents)

    scoped_parents = [parent for parent in _sorted_positional_parent_ids(graph, node_id) if parent in play_scope]
    if not scoped_parents:
        return False
    return any(walk(parent, (node_id,)) for parent in scoped_parents)


def no_log_true_in_scope(
    graph: ContentGraph,
    node_id: str,
    *,
    play_context_id: str | None = None,
    play_scope: set[str] | None = None,
) -> bool:
    """Return True if no_log is effectively True at this node.

    Ansible allows more-specific scopes to override inherited keywords. A task
    with no_log: false can opt out of a block/play with no_log: true. We walk
    the chain from the task outward (closest to farthest) and return on the
    first explicit no_log setting.

    When ``play_context_id`` is set, only positional ancestors within that play
    are considered. If a shared task has multiple include parents in the play,
    ``no_log`` is true when any in-scope path inherits it.

    Args:
        graph: ContentGraph for the scan.
        node_id: Task or handler node id.
        play_context_id: Optional play node id used to resolve ``no_log`` for
            shared included task files reached from multiple plays.
        play_scope: Optional precomputed play scope for ``play_context_id``.

    Returns:
        True when no_log is effectively true at this scope.
    """
    node = graph.get_node(node_id)
    if node is None:
        return False
    if node.no_log is False:
        return False
    if node.no_log is True:
        return True
    if play_context_id is not None:
        scope = play_scope if play_scope is not None else graph.play_scoped_node_ids(play_context_id)
        return _no_log_any_play_scoped_path(graph, node_id, scope)
    for ancestor in graph.positional_ancestors(node_id):
        if ancestor.no_log is False:
            return False
        if ancestor.no_log is True:
            return True
    return False
