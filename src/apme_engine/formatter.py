"""YAML formatter for Ansible content.

Phase 1 of the remediation pipeline: normalize YAML formatting so that
subsequent semantic fixes (modernize) produce clean, minimal diffs.

Uses FormattedYAML (ruamel.yaml round-trip) for comment-preserving
load/dump, plus targeted transforms for tab removal, key reordering,
and jinja spacing normalization.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from apme_engine.engine.yaml_utils import FormattedYAML

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".tox", "htmlcov", ".eggs"}
YAML_EXTENSIONS = {".yml", ".yaml"}

JINJA_NORMALIZE_RE = re.compile(r"\{\{(\s*)(.*?)(\s*)\}\}")

_JINJA_PIPE_RE = re.compile(r"(?<!\|)\|(?!\|)")

TASK_KEY_ORDER = [
    "name",
    "when",
    "changed_when",
    "failed_when",
    "loop",
    "loop_control",
    "with_items",
    "with_dict",
    "with_fileglob",
    "with_subelements",
    "with_sequence",
    "with_nested",
    "with_first_found",
    "register",
    "notify",
    "listen",
    "become",
    "become_user",
    "become_method",
    "delegate_to",
    "run_once",
    "ignore_errors",
    "ignore_unreachable",
    "no_log",
    "tags",
    "environment",
    "vars",
    "args",
    "block",
    "rescue",
    "always",
]

_TASK_KEY_SET = set(TASK_KEY_ORDER)
_BLOCK_BODY_KEYS = frozenset({"block", "rescue", "always"})


@dataclass
class FormatResult:
    """Result of formatting a YAML file or content string.

    Attributes:
        path: Path to the file (or placeholder for stdin).
        original: Original content before formatting.
        formatted: Content after formatting.
        changed: True if formatting changed the content.
        diff: Unified diff string (empty if unchanged).
        error: When set, formatting was skipped and ``formatted`` is the
            original content (load/dump failure). Callers must not treat
            this as "already formatted."
    """

    path: Path
    original: str
    formatted: str
    changed: bool
    diff: str = field(default="", repr=False)
    error: str | None = None


def _normalize_jinja_pipes(inner: str) -> str:
    """Normalize pipe operator spacing inside a Jinja expression.

    Ensures single ``|`` operators (Jinja filters) have exactly one space
    on each side.  Does not touch ``||`` (logical or).

    Args:
        inner: Inner content of a ``{{ ... }}`` expression (already stripped).

    Returns:
        Inner content with normalized pipe spacing.
    """
    parts = _JINJA_PIPE_RE.split(inner)
    return " | ".join(part.strip() for part in parts)


def _normalize_jinja(match: re.Match[str]) -> str:
    """Normalize ``{{ foo }}`` spacing: braces and filter pipes.

    Args:
        match: Regex match for Jinja expression with optional inner spacing.

    Returns:
        Normalized Jinja string with correct spacing.
    """
    inner: str = match.group(2).strip()
    if not inner:
        return "{{ }}"
    inner = _normalize_jinja_pipes(inner)
    return "{{ " + inner + " }}"


_BARE_JINJA_KEYS = frozenset({"when", "changed_when", "failed_when", "until", "that"})
_BARE_JINJA_LINE_RE = re.compile(r"^(?P<prefix>\s+(?:" + "|".join(_BARE_JINJA_KEYS) + r"):\s+)(?P<value>.+)$")


def _normalize_bare_jinja_pipes(text: str) -> str:
    """Normalize ``|`` filter spacing in bare-Jinja keys like ``when:``.

    Ansible evaluates ``when:`` values as Jinja without ``{{ }}``, so
    ``foo|bool`` should become ``foo | bool``.  Only applies to known
    keys that take Jinja expressions, to avoid touching values in other
    contexts (e.g. shell pipes).

    Args:
        text: Raw YAML text to process.

    Returns:
        Text with normalized pipe spacing in bare-Jinja keys.
    """
    lines = text.split("\n")
    changed = False
    for i, line in enumerate(lines):
        m = _BARE_JINJA_LINE_RE.match(line)
        if m is None:
            continue
        value = m.group("value")
        new_value = _JINJA_PIPE_RE.sub(
            lambda pm, _v=value: (
                ("" if pm.start() > 0 and _v[pm.start() - 1] == " " else " ")
                + "|"
                + ("" if pm.end() < len(_v) and _v[pm.end()] == " " else " ")
            ),
            value,
        )
        if new_value != value:
            lines[i] = m.group("prefix") + new_value
            changed = True
    if not changed:
        return text
    return "\n".join(lines)


def _fix_jinja_spacing(text: str) -> str:
    text = JINJA_NORMALIZE_RE.sub(_normalize_jinja, text)
    text = _normalize_bare_jinja_pipes(text)
    return text


def _fix_tabs(text: str) -> str:
    return text.replace("\t", "  ")


_FREE_FORM_MODULE_NAMES = frozenset(
    {
        "ansible.builtin.command",
        "ansible.builtin.shell",
        "ansible.builtin.raw",
        "ansible.builtin.script",
        "ansible.legacy.command",
        "ansible.legacy.shell",
        "ansible.legacy.raw",
        "command",
        "shell",
        "raw",
        "script",
    }
)

_FREE_FORM_LINE_RE = re.compile(
    r"^(?P<indent>\s*-?\s*)(?P<module>"
    + "|".join(re.escape(m) for m in sorted(_FREE_FORM_MODULE_NAMES, key=len, reverse=True))
    + r"):\s+(?P<value>.+)$"
)


def _quote_free_form_args(text: str) -> str:
    """Quote free-form module arguments that contain YAML-unsafe characters.

    Runs before ``yaml.load()`` to prevent parse failures on lines like::

        ansible.builtin.shell: cat /etc/passwd | cut -d: -f1

    The colon inside the value is valid for Ansible but invalid YAML.
    This wraps such values in double quotes so ruamel can parse the file.

    Args:
        text: Raw YAML content.

    Returns:
        Text with free-form values quoted where necessary.
    """
    lines = text.split("\n")
    changed = False
    for i, line in enumerate(lines):
        m = _FREE_FORM_LINE_RE.match(line)
        if m is None:
            continue
        value = m.group("value")
        if value.startswith(('"', "'")):
            continue
        if ":" not in value:
            continue
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines[i] = f'{m.group("indent")}{m.group("module")}: "{escaped}"'
        changed = True
    if not changed:
        return text
    return "\n".join(lines)


def _strip_stray_blanks(text: str) -> str:
    """Remove interior blank lines inserted by ruamel round-trip dumping.

    Keeps blank lines before document markers (``---``) and at EOF.
    Intentional task-level spacing is re-inserted by ``_add_task_spacing``.

    Args:
        text: YAML text possibly containing stray blank lines.

    Returns:
        Cleaned text with interior blank lines removed.
    """
    lines = text.split("\n")
    result: list[str] = []
    for i, line in enumerate(lines):
        if line.strip() == "" and i > 0 and i < len(lines) - 1:
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            if next_line.startswith("---") or next_line.strip() == "":
                result.append(line)
                continue
            continue
        result.append(line)
    return "\n".join(result)


_TASK_LIST_KEYS = frozenset({"tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"})
_TASK_ITEM_RE = re.compile(r"^(\s*)- \S")


_PLAY_KEYS = frozenset({"hosts", "import_playbook"})


def _is_bare_task_list(lines: list[str]) -> bool:
    """Return True if the file is a bare task list (no ``tasks:`` parent key).

    Bare task lists start with ``---`` followed by ``- name:`` or ``- module:``
    at indent 0, typical of role ``tasks/main.yml`` or included task files.
    Returns False for playbooks (first list item has ``hosts:``).

    Args:
        lines: Split lines of the YAML file.

    Returns:
        True if the file is a bare task list.
    """
    first_item_lines: list[str] = []
    found_first = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "---":
            continue
        if not found_first:
            if not _TASK_ITEM_RE.match(line):
                return False
            found_first = True
            first_item_lines.append(stripped)
            continue
        if _TASK_ITEM_RE.match(line):
            break
        first_item_lines.append(stripped)

    if not found_first:
        return False

    for fl in first_item_lines:
        key = fl.lstrip("- ").split(":")[0].strip()
        if key in _PLAY_KEYS:
            return False
    return True


def _add_task_spacing(text: str) -> str:
    """Insert a blank line between task list items when missing.

    Applies under known task-list keys (tasks, handlers, block, etc.)
    and also to bare task-list files (top-level YAML sequence of tasks,
    e.g. role ``tasks/main.yml``) so that non-task sequences (vars lists,
    loop items) are not affected.

    Args:
        text: Formatted YAML text.

    Returns:
        Text with blank lines between task-level list items.
    """
    lines = text.split("\n")
    result: list[str] = [lines[0]] if lines else []
    in_task_list = _is_bare_task_list(lines)
    task_list_indent = -1 if not in_task_list else 0
    for i in range(1, len(lines)):
        line = lines[i]
        stripped = line.rstrip()

        if stripped.endswith(":") and stripped.lstrip("- ").rstrip(":").strip() in _TASK_LIST_KEYS:
            in_task_list = True
            task_list_indent = len(line) - len(line.lstrip())
            result.append(line)
            continue

        if in_task_list and stripped and not stripped.startswith("#"):
            line_indent = len(line) - len(line.lstrip())
            if line_indent <= task_list_indent and not _TASK_ITEM_RE.match(line):
                in_task_list = False

        m = _TASK_ITEM_RE.match(line)
        if in_task_list and m and result and result[-1].strip() != "":
            indent = m.group(1)
            item_indent = len(indent)
            if task_list_indent >= 0 and item_indent > task_list_indent + 2:
                result.append(line)
                continue
            is_mapping = i + 1 < len(lines) and lines[i + 1].startswith(indent + "  ")
            is_action = ":" in line
            prev_stripped = result[-1].rstrip()
            prev_key = prev_stripped.lstrip("- ").rstrip(":").strip()
            prev_is_list_header = prev_stripped == "---" or (
                prev_stripped.endswith(":") and prev_key in _TASK_LIST_KEYS
            )
            if (is_mapping or is_action) and not prev_is_list_header:
                result.append("")
        result.append(line)
    return "\n".join(result)


def _capitalize_task_name(name: str) -> str:
    """Capitalize the meaningful part of a task name, respecting ``name[prefix]``.

    If the name contains `` | `` separators (from the ``name[prefix]`` rule),
    the prefix portion is left as-is and only the first letter of the final
    segment is capitalized.

    Args:
        name: Task name string, possibly with ``name[prefix]`` separators.

    Returns:
        Name with the meaningful segment capitalized.
    """
    sep = " | "
    if sep in name:
        idx = name.rfind(sep) + len(sep)
        prefix_part = name[:idx]
        rest = name[idx:]
        if rest and not rest[0].isupper():
            return prefix_part + rest[0].upper() + rest[1:]
        return name
    if name and not name[0].isupper():
        return name[0].upper() + name[1:]
    return name


def _capitalize_name(data: object) -> None:
    """Walk task/play mappings and capitalize the first letter of ``name`` values.

    Args:
        data: CommentedSeq, CommentedMap, or nested structure with tasks.
    """
    if isinstance(data, CommentedSeq):
        for item in data:
            _capitalize_name(item)
    elif isinstance(data, CommentedMap):
        name = data.get("name")
        if isinstance(name, str) and name:
            capitalized = _capitalize_task_name(name)
            if capitalized != name:
                data["name"] = capitalized

        for nested_key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            if nested_key in data:
                _capitalize_name(data[nested_key])


def _reorder_task_keys(data: object) -> None:
    """Reorder keys in task mappings so name comes first, then action, then meta keys.

    Args:
        data: CommentedSeq, CommentedMap, or nested structure with tasks.
    """
    if isinstance(data, CommentedSeq):
        for item in data:
            _reorder_task_keys(item)
    elif isinstance(data, CommentedMap):
        if "tasks" in data:
            _reorder_task_keys(data["tasks"])
        if "pre_tasks" in data:
            _reorder_task_keys(data["pre_tasks"])
        if "post_tasks" in data:
            _reorder_task_keys(data["post_tasks"])
        if "handlers" in data:
            _reorder_task_keys(data["handlers"])
        if "block" in data:
            _reorder_task_keys(data["block"])
        if "rescue" in data:
            _reorder_task_keys(data["rescue"])
        if "always" in data:
            _reorder_task_keys(data["always"])
        if "roles" in data:
            _reorder_task_keys(data["roles"])

        _reorder_single_task(data)


def _reorder_single_task(mapping: CommentedMap) -> None:
    """Reorder a single task/play CommentedMap: name first, then action, then known keys, then rest.

    For block tasks, ``block``/``rescue``/``always`` are treated as the action
    body and placed after metadata keys (matching ansible-lint key-order).

    Args:
        mapping: CommentedMap for a task or play block.
    """
    keys = list(mapping.keys())
    if len(keys) <= 1:
        return

    has_name = "name" in keys
    if not has_name:
        return

    is_block_task = "block" in keys

    action_key = None
    for k in keys:
        if k not in _TASK_KEY_SET and k != "name":
            action_key = k
            break

    desired: list[str] = []
    if has_name:
        desired.append("name")
    if action_key:
        desired.append(action_key)

    for k in TASK_KEY_ORDER:
        if k in keys and k != "name":
            if is_block_task and k in _BLOCK_BODY_KEYS:
                continue
            desired.append(k)

    if is_block_task:
        for k in ("block", "rescue", "always"):
            if k in keys:
                desired.append(k)

    for k in keys:
        if k not in desired:
            desired.append(k)

    if desired == keys:
        return

    items = [(k, mapping[k]) for k in desired]

    mapping.clear()
    for k, v in items:
        mapping[k] = v


_FORMAT_META_KEYS = frozenset(
    {
        "name",
        "when",
        "changed_when",
        "failed_when",
        "register",
        "notify",
        "listen",
        "become",
        "become_user",
        "become_method",
        "become_flags",
        "delegate_to",
        "run_once",
        "connection",
        "ignore_errors",
        "ignore_unreachable",
        "no_log",
        "tags",
        "environment",
        "vars",
        "args",
        "loop",
        "loop_control",
        "with_items",
        "with_dict",
        "with_fileglob",
        "with_subelements",
        "with_sequence",
        "with_nested",
        "with_first_found",
        "block",
        "rescue",
        "always",
        "any_errors_fatal",
        "max_fail_percentage",
        "check_mode",
        "diff",
        "throttle",
        "timeout",
        "retries",
        "delay",
        "until",
        "debugger",
        "module_defaults",
        "collections",
        "local_action",
    }
)

_KV_RE = re.compile(r"(\w+)=")

_COMMAND_MODULES = frozenset(
    {
        "command",
        "shell",
        "raw",
        "script",
        "ansible.builtin.command",
        "ansible.builtin.shell",
        "ansible.builtin.raw",
        "ansible.builtin.script",
        "ansible.legacy.command",
        "ansible.legacy.shell",
        "ansible.legacy.raw",
    }
)


def _find_closing_quote(s: str, quote_char: str) -> int:
    """Return index of the unescaped closing quote in *s*, or -1.

    Args:
        s: String starting with the opening quote character.
        quote_char: The quote character to match (``"`` or ``'``).

    Returns:
        Index of the closing quote, or -1 if not found.
    """
    i = 1
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            i += 2
            continue
        if s[i] == quote_char:
            return i
        i += 1
    return -1


def _parse_kv_string(value: str) -> CommentedMap | None:
    """Parse an old-style ``key=value key2=value2`` string into a CommentedMap.

    Handles quoted values (double and single quotes) and values containing
    spaces/Jinja expressions.

    Args:
        value: String containing key=value pairs (e.g. ``name="foo" gid="bar"``).

    Returns:
        CommentedMap with parsed key-value pairs, or None if the string
        doesn't look like key=value pairs.
    """
    if "=" not in value:
        return None

    result = CommentedMap()
    remaining = value.strip()

    while remaining:
        m = _KV_RE.match(remaining)
        if m is None:
            return None

        key = m.group(1)
        remaining = remaining[m.end() :]

        if remaining.startswith('"') or remaining.startswith("'"):
            quote_char = remaining[0]
            end = _find_closing_quote(remaining, quote_char)
            if end == -1:
                return None
            val = remaining[1:end]
            remaining = remaining[end + 1 :].lstrip()
        elif remaining.startswith("{{"):
            end = remaining.find("}}")
            if end == -1:
                return None
            val = remaining[: end + 2]
            remaining = remaining[end + 2 :].lstrip()
        else:
            parts = remaining.split(None, 1)
            val = parts[0]
            remaining = parts[1] if len(parts) > 1 else ""

        result[key] = val

    return result if len(result) > 0 else None


def _expand_inline_kv_args(data: object) -> None:
    """Walk task mappings and expand old-style ``module: key=val key2=val2`` to dict form.

    Skips command/shell/raw/script modules whose string value is a command,
    not key=value pairs.

    Args:
        data: CommentedSeq, CommentedMap, or nested structure with tasks.
    """
    if isinstance(data, CommentedSeq):
        for item in data:
            _expand_inline_kv_args(item)
    elif isinstance(data, CommentedMap):
        for task_list_key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            if task_list_key in data:
                _expand_inline_kv_args(data[task_list_key])

        _expand_single_task_kv(data)


def _expand_single_task_kv(mapping: CommentedMap) -> None:
    """Expand key=value string on a module key to a proper YAML mapping.

    Args:
        mapping: CommentedMap for a single task.
    """
    module_key = None
    for k in mapping:
        if k not in _FORMAT_META_KEYS:
            module_key = k
            break

    if module_key is None:
        return

    if module_key in _COMMAND_MODULES:
        return

    value = mapping.get(module_key)
    if not isinstance(value, str) or "=" not in value:
        return

    parsed = _parse_kv_string(value)
    if parsed is not None and len(parsed) > 0:
        mapping[module_key] = parsed


def _force_tags_block_style(data: object) -> None:
    """Walk task/play mappings and convert flow-style ``tags`` lists to block style.

    Args:
        data: CommentedSeq, CommentedMap, or nested structure with tasks.
    """
    if isinstance(data, CommentedSeq):
        for item in data:
            _force_tags_block_style(item)
    elif isinstance(data, CommentedMap):
        tags = data.get("tags")
        if isinstance(tags, CommentedSeq) and tags.fa.flow_style():
            tags.fa.set_block_style()

        for nested_key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            if nested_key in data:
                _force_tags_block_style(data[nested_key])


_MIN_ANSIBLE_VERSION_TUPLE = (2, 14, 0)
_MIN_ANSIBLE_VERSION_STR = "2.14.0"


def _parse_loose_version(raw: object) -> tuple[int, ...] | None:
    """Parse a version string or float into a comparable tuple.

    Handles ``"2.14.0"``, ``"2.5"``, and bare floats like ``2.5``.

    Args:
        raw: Version value (string, float, or int).

    Returns:
        Tuple of ints for comparison, or None if not parseable.
    """
    try:
        parts = [int(p) for p in str(raw).split(".")]
    except (ValueError, AttributeError):
        return None
    return tuple(parts)


def _normalize_min_ansible_version(data: CommentedMap) -> None:
    """Ensure ``galaxy_info.min_ansible_version`` is at least 2.14.0.

    Role meta files targeting AAP 2.5+ should declare a minimum Ansible
    version that is actually supported.  This bumps any value below 2.14.0
    and coerces bare floats (e.g. ``2.5``) to proper semver strings.

    Args:
        data: Top-level CommentedMap (role meta/main.yml content).
    """
    galaxy_info = data.get("galaxy_info")
    if not isinstance(galaxy_info, CommentedMap):
        return
    raw = galaxy_info.get("min_ansible_version")
    if raw is None:
        return
    current = _parse_loose_version(raw)
    if current is None:
        return
    if current < _MIN_ANSIBLE_VERSION_TUPLE:
        galaxy_info["min_ansible_version"] = _MIN_ANSIBLE_VERSION_STR
    elif not isinstance(raw, str):
        galaxy_info["min_ansible_version"] = ".".join(str(p) for p in current)


def format_content(text: str, filename: str = "<stdin>") -> FormatResult:
    """Format a YAML string.

    Load and dump failures are best-effort: the original content is returned
    unchanged (for example empty flow sequences with preceding comments that
    trip a ruamel.yaml emitter IndexError on dump).

    Args:
        text: Raw YAML content.
        filename: Filename for diff output (default: "<stdin>").

    Returns:
        FormatResult with original, formatted, diff.
    """
    original = text

    text = _fix_tabs(text)
    text = _quote_free_form_args(text)

    yaml = FormattedYAML(typ="rt", pure=True, version=(1, 1))

    try:
        data = yaml.load(text)
    except Exception as exc:
        return FormatResult(
            path=Path(filename),
            original=original,
            formatted=original,
            changed=False,
            diff="",
            error=f"YAML load failed: {exc}",
        )

    if data is None or not isinstance(data, CommentedMap | CommentedSeq | list | dict):
        return FormatResult(
            path=Path(filename),
            original=original,
            formatted=original,
            changed=False,
            diff="",
        )

    if isinstance(data, CommentedSeq):
        for item in data:
            _expand_inline_kv_args(item)
            _force_tags_block_style(item)
            _capitalize_name(item)
            _reorder_task_keys(item)
    elif isinstance(data, CommentedMap):
        _expand_inline_kv_args(data)
        _force_tags_block_style(data)
        _capitalize_name(data)
        _reorder_task_keys(data)
        _normalize_min_ansible_version(data)

    try:
        formatted = yaml.dumps(data)
        formatted = _fix_jinja_spacing(formatted)
        formatted = _strip_stray_blanks(formatted)
        formatted = _add_task_spacing(formatted)
    except Exception as exc:
        return FormatResult(
            path=Path(filename),
            original=original,
            formatted=original,
            changed=False,
            diff="",
            error=f"YAML dump/post-process failed: {exc}",
        )

    changed = formatted != original
    diff = ""
    if changed:
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                formatted.splitlines(keepends=True),
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
            )
        )

    return FormatResult(
        path=Path(filename),
        original=original,
        formatted=formatted,
        changed=changed,
        diff=diff,
    )


def format_file(path: Path) -> FormatResult:
    """Format a single YAML file on disk.

    Args:
        path: Path to the YAML file.

    Returns:
        FormatResult with original, formatted, diff.
    """
    text = path.read_text(encoding="utf-8")
    result = format_content(text, filename=str(path))
    result.path = path
    return result


def format_directory(
    root: Path,
    exclude_patterns: list[str] | None = None,
) -> list[FormatResult]:
    """Walk a directory and format all .yml/.yaml files.

    Args:
        root: Root directory to walk.
        exclude_patterns: Optional glob patterns to exclude (e.g. ["vendor/*"]).

    Returns:
        List of FormatResult for each formatted file.
    """
    results: list[FormatResult] = []
    exclude = set(exclude_patterns or [])

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        rel_dir = Path(dirpath).relative_to(root)
        if any(_matches_glob(str(rel_dir / "*"), pat) for pat in exclude):
            dirnames.clear()
            continue

        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            if fpath.suffix not in YAML_EXTENSIONS:
                continue

            rel = fpath.relative_to(root)
            if any(_matches_glob(str(rel), pat) for pat in exclude):
                continue

            results.append(format_file(fpath))

    return results


def _matches_glob(path_str: str, pattern: str) -> bool:
    """Simple glob matching using fnmatch.

    Args:
        path_str: Path string to match.
        pattern: Glob pattern (e.g. "vendor/*").

    Returns:
        True if path matches pattern.
    """
    import fnmatch

    return fnmatch.fnmatch(path_str, pattern)


def check_idempotent(result: FormatResult) -> bool:
    """Verify that formatting the formatted output produces no further changes.

    Args:
        result: FormatResult from a previous format run.

    Returns:
        True if second format produces no changes.
    """
    second = format_content(result.formatted, filename=str(result.path))
    return not second.changed
