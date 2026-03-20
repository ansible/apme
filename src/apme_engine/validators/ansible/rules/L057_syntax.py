"""L057: Syntax check via ansible-playbook --syntax-check."""

import os
import re
import subprocess
from pathlib import Path

RULE_ID = "L057"


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
                "level": "error",
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
                    "level": "error",
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
            line = 1
            line_match = re.search(r"\bline\s+(\d+)\b", stderr, re.I)
            if line_match:
                line = int(line_match.group(1))
            violations.append(
                {
                    "rule_id": RULE_ID,
                    "level": "error",
                    "message": stderr or "syntax check failed",
                    "file": rel_path,
                    "line": line,
                    "path": "",
                    "scope": "playbook",
                }
            )

    return violations
