"""ansible-security-scanner Plugin sidecar (ADR-042).

Wraps https://github.com/cpeoples/ansible-security-scanner (Apache-2.0,
Chris Peoples) behind ``Plugin.Validate``. Detection only — the scanner's
unified-diff autofix is whole-file; Engine Transform is node YAML.

Run on the host::

    APME_PLUGIN_LISTEN=0.0.0.0:50101 python examples/plugins/secscan/plugin.py

Point Engine at it::

    export APME_PLUGIN_SECSCAN_ADDRESS=127.0.0.1:50101
"""

from __future__ import annotations

import logging
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast

_SRC = Path(__file__).resolve().parents[3] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from apme_plugin_sdk import PluginBase  # noqa: E402

logger = logging.getLogger("apme.plugin.secscan")

try:
    from ansible_security_scanner import AnsibleSecurityScanner as _Scanner
except ImportError:  # pragma: no cover - optional extra for the example image
    _Scanner = None  # type: ignore[assignment,misc]


class _FindingLike(Protocol):
    """Subset of ansible_security_scanner.SecurityFinding used by the wrapper."""

    file_path: str
    line_number: int
    rule_id: str
    severity: str
    title: str
    description: str
    recommendation: str


class SecscanPlugin(PluginBase):
    """Run Ansible Security Scanner on the files Engine sent in Validate."""

    name = "secscan"
    version = "1.0.0"

    def transform_rule_ids(self) -> list[str]:
        """No node-scoped Transform (scanner patches are unified diffs).

        Returns:
            Empty list.
        """
        return []

    def health(self) -> str:
        """Fail Health when ansible-security-scanner is not installed.

        Returns:
            ``ok`` or an error string.
        """
        if _Scanner is None:
            return "error: ansible-security-scanner is not installed"
        return "ok"

    def validate(
        self,
        files: Sequence[tuple[str, bytes]],
        hierarchy: object,
    ) -> list[dict[str, str | int]]:
        """Write files to a temp tree and scan them.

        Args:
            files: ``(path, content)`` from ``ValidateRequest.files``.
            hierarchy: Unused (the scanner reads YAML text, not OPA payload).

        Returns:
            ``EXT-secscan-*`` violation dicts.
        """
        del hierarchy
        if _Scanner is None:
            raise RuntimeError("ansible-security-scanner is not installed")
        findings = scan_files(files)
        return [map_finding(self, finding) for finding in findings]


def safe_relpath(raw: str) -> Path | None:
    """Return a relative path that cannot escape the scan root.

    Args:
        raw: Path from ``ValidateRequest.files``.

    Returns:
        Relative ``Path``, or ``None`` if absolute or parent-escaping.
    """
    text = raw.strip()
    if not text:
        return None
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def scan_relpath(root: Path, file_path: str) -> str:
    """Map a scanner path back to the Engine-relative path under ``root``.

    Args:
        root: Temporary scan directory.
        file_path: Path reported by the scanner (often absolute under ``root``).

    Returns:
        Relative path, or empty string when the path is outside ``root``.
    """
    text = (file_path or "").strip()
    if not text:
        return ""
    path = Path(text)
    try:
        rel = path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        safe = safe_relpath(text)
        return str(safe) if safe is not None else ""
    if ".." in rel.parts:
        return ""
    return str(rel)


def write_file_tree(root: Path, files: Sequence[tuple[str, bytes]]) -> list[str]:
    """Materialize Engine files under ``root``.

    Args:
        root: Empty temporary directory.
        files: Path/content pairs.

    Returns:
        Relative paths that were written.
    """
    written: list[str] = []
    for raw_path, content in files:
        rel = safe_relpath(raw_path)
        if rel is None:
            logger.warning("secscan plugin: skipped unsafe path %s", raw_path)
            continue
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        written.append(str(rel))
    return written


def map_finding(plugin: PluginBase, finding: _FindingLike) -> dict[str, str | int]:
    """Map a scanner finding to an SDK violation dict.

    Args:
        plugin: Plugin instance (for prefixing).
        finding: Scanner finding (duck-typed).

    Returns:
        Dict consumed by ``PluginBase.violation``.
    """
    title = (finding.title or finding.rule_id).strip()
    detail = (finding.description or "").strip()
    message = title if not detail else f"{title}: {detail}"
    rec = (finding.recommendation or "").strip()
    line = finding.line_number if isinstance(finding.line_number, int) else 0
    rel = str(finding.file_path or "")
    safe = safe_relpath(rel)
    return plugin.violation(
        rule_id=str(finding.rule_id or "unknown"),
        message=message[:2000],
        file=str(safe) if safe is not None else "",
        line=line if line > 0 else 0,
        severity=str(finding.severity or "high"),
        scope="task",
        ai_guidance=rec,
    )


def scan_files(files: Sequence[tuple[str, bytes]]) -> list[_FindingLike]:
    """Run the scanner on a materialized file tree.

    Args:
        files: Engine path/content pairs.

    Returns:
        Scanner findings (possibly empty).
    """
    if _Scanner is None:
        return []
    with tempfile.TemporaryDirectory(prefix="apme-secscan-") as tmp:
        root = Path(tmp)
        written = write_file_tree(root, files)
        if not written:
            return []
        scanner = _Scanner(directory=str(root), target_files=written, jobs=1)
        report = scanner.scan_directory()
        raw = getattr(report, "findings", []) or []
        remapped: list[_FindingLike] = []
        for item in raw:
            if item is None:
                continue
            rel = scan_relpath(root, str(getattr(item, "file_path", "") or ""))
            remapped.append(
                cast(
                    _FindingLike,
                    SimpleNamespace(
                        file_path=rel,
                        line_number=getattr(item, "line_number", 0),
                        rule_id=getattr(item, "rule_id", ""),
                        severity=getattr(item, "severity", ""),
                        title=getattr(item, "title", ""),
                        description=getattr(item, "description", ""),
                        recommendation=getattr(item, "recommendation", ""),
                    ),
                )
            )
        return remapped


if __name__ == "__main__":
    SecscanPlugin.serve()
