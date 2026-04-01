"""L057: Syntax check via ansible-playbook --syntax-check."""

import os
import re
import subprocess
from pathlib import Path

RULE_ID = "L057"

_TASK_DIRS = {"tasks", "handlers"}

_PLAY_STRUCTURE_PATTERNS = (
    "is not a valid attribute for a play",
    "hosts is not set",
    "a play is missing",
    "playbook must be a list of plays",
)


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
    for ext in ("*.yml", "*.yaml"):
        for path in root.rglob(ext):
            if not path.is_file():
                continue
            try:
                text = path.read_text(errors="replace")
                if "hosts:" in text or "tasks:" in text:
                    playbooks.append(path)
            except Exception:
                pass
    return playbooks


def run(
    venv_root: Path,
    root_dir: Path,
    env_extra: dict[str, str] | None = None,
    **_kwargs: object,
) -> list[dict[str, object]]:
    """Run ansible-playbook --syntax-check on all playbooks under root_dir.

    Args:
        venv_root: Path to ansible venv root.
        root_dir: Root directory containing playbooks.
        env_extra: Optional extra environment variables.
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

    for playbook_path in _find_playbooks(root_dir):
        rel_path = str(playbook_path.relative_to(root_dir)) if root_dir in playbook_path.parents else str(playbook_path)
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
