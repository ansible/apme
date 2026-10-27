"""L007: Replace ansible.builtin.shell with ansible.builtin.command when no shell features."""

from __future__ import annotations

import re

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import ViolationDict
from apme_engine.remediation.transforms._helpers import get_module_key, rename_key

# Inspected after Jinja ``{{ }}`` is stripped so ``|quote`` is not a pipe.
# Non-greedy so dict literals like ``{{ {'k': 'v'} }}`` are stripped too.
_JINJA_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_CHARS = (
    "|",
    "&&",
    "||",
    ";",
    ">",
    ">>",
    "<",
    "$(",
    "`",
    "*",
    "?",
    "&",
    "!",
    "(",
    ")",
    "[",
    "]",
    "$",
    "\n",
)
_SHELL_BUILTINS = frozenset(
    {
        ".",
        ":",
        "alias",
        "bg",
        "builtin",
        "break",
        "cd",
        "command",
        "continue",
        "declare",
        "eval",
        "exit",
        "exec",
        "export",
        "fg",
        "getopts",
        "hash",
        "help",
        "jobs",
        "let",
        "local",
        "logout",
        "mapfile",
        "popd",
        "pushd",
        "read",
        "readarray",
        "readonly",
        "return",
        "set",
        "shopt",
        "source",
        "suspend",
        "time",
        "trap",
        "times",
        "type",
        "typeset",
        "ulimit",
        "umask",
        "unalias",
        "unset",
        "wait",
    }
)

_SHELL_TO_COMMAND = {
    "ansible.builtin.shell": "ansible.builtin.command",
    "ansible.legacy.shell": "ansible.legacy.command",
    "shell": "ansible.builtin.command",
}


def _uses_shell_features(cmd: str) -> bool:
    """Check if command string uses shell features (pipes, redirects, etc).

    Jinja ``{{ }}`` expressions are stripped first so a filter such as
    ``{{ path | quote }}`` is not treated as a shell pipe.  A command that
    is only Jinja (nothing inspectable after the strip) is treated as
    using shell features — the rendered value is unknown.  Leftover
    ``{{`` after the strip (unclosed or nested beyond the pattern) is
    also uninspectable.

    Args:
        cmd: Command string to check.

    Returns:
        True if the non-Jinja remainder is empty, still contains Jinja
        delimiters, or contains shell-specific characters.
    """
    stripped = _JINJA_RE.sub(" ", cmd)
    if "{{" in stripped or "}}" in stripped:
        return True
    normalized = " ".join(stripped.split())
    if not normalized:
        return True
    if _ENV_ASSIGNMENT_RE.match(normalized):
        return True
    first_token = normalized.split(" ", 1)[0]
    # Quoted or escaped command names are interpreted by the shell. Decline
    # conversion instead of trying to approximate shell tokenization.
    if any(char in first_token for char in ("'", '"', "\\")):
        return True
    if first_token in _SHELL_BUILTINS:
        return True
    return any(ch in stripped for ch in _SHELL_CHARS)


def _extract_command_string(module_args: object) -> str:
    """Return the inspectable command string from shell module arguments.

    Prefers ``cmd``, then free-form ``_raw_params``, then ``argv``. Returns
    empty when the command cannot be inspected, in which case the transform
    must not convert shell to command.

    Args:
        module_args: Scalar command string or argument mapping.

    Returns:
        Command string, or empty if none is available.
    """
    if isinstance(module_args, str):
        return module_args
    if not isinstance(module_args, dict):
        return ""
    cmd = module_args.get("cmd", "")
    if isinstance(cmd, str) and cmd.strip():
        return cmd
    raw_params = module_args.get("_raw_params", "")
    if isinstance(raw_params, str) and raw_params.strip():
        return raw_params
    argv = module_args.get("argv")
    if isinstance(argv, list) and argv:
        return " ".join(str(part) for part in argv)
    return ""


def fix_shell_to_command(task: CommentedMap, violation: ViolationDict) -> bool:
    """Replace shell with command when the command string uses no shell features.

    Inspects free-form, ``cmd``, ``_raw_params``, and ``argv``. Refuses to
    convert when the command cannot be inspected or contains shell
    metacharacters.

    Args:
        task: Task CommentedMap to modify in-place.
        violation: Violation dict with line.

    Returns:
        True if a change was applied.
    """
    module_key = get_module_key(task)
    if module_key is None or module_key not in _SHELL_TO_COMMAND:
        return False

    module_args = task.get(module_key)
    cmd = _extract_command_string(module_args)
    if not cmd or _uses_shell_features(cmd):
        return False

    new_key = _SHELL_TO_COMMAND[module_key]
    rename_key(task, module_key, new_key)
    return True
