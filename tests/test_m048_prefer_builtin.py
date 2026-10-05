"""Unit tests for M048 prefer-builtin FQCN coverage (issue #714)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from apme_engine.validators.ansible.rules import M001_M004_introspect


def _task(
    module: str,
    *,
    file: str = "playbook.yml",
    line: int = 10,
    key: str = "tasks[0]",
) -> dict[str, object]:
    """Build a minimal task node for introspect.run().

    Args:
        module: Module name (short or FQCN).
        file: Source file path.
        line: Starting line number.
        key: Graph/node key path.

    Returns:
        Task node dict accepted by ``M001_M004_introspect.run``.
    """
    return {
        "original_module": module,
        "module": module,
        "file": file,
        "line": (line, line),
        "key": key,
    }


def _run_with_intro(
    tasks: list[dict[str, object]],
    intro: dict[str, object],
) -> list[dict[str, object]]:
    """Invoke ``run`` with a mocked introspection map.

    Args:
        tasks: Task nodes to evaluate.
        intro: Mapping of module name → introspect info dict.

    Returns:
        Violation list from ``run``.
    """
    with patch.object(M001_M004_introspect, "_run_introspection", return_value=intro):
        return M001_M004_introspect.run(tasks, venv_root=Path("/nonexistent"))


def _m048(violations: list[dict[str, object]]) -> list[dict[str, object]]:
    """Filter M048 findings.

    Args:
        violations: Full violation list.

    Returns:
        Violations whose ``rule_id`` is ``M048``.
    """
    return [v for v in violations if v.get("rule_id") == "M048"]


def _m001(violations: list[dict[str, object]]) -> list[dict[str, object]]:
    """Filter M001 findings.

    Args:
        violations: Full violation list.

    Returns:
        Violations whose ``rule_id`` is ``M001``.
    """
    return [v for v in violations if v.get("rule_id") == "M001"]


class TestM048PreferBuiltin:
    """M048 fires for non-builtin FQCNs with an authoritative builtin twin."""

    def test_posix_mount_with_builtin_twin(self) -> None:
        """ansible.posix.mount with builtin_alternative emits M048."""
        intro: dict[str, object] = {
            "ansible.posix.mount": {
                "fqcn": "ansible.posix.mount",
                "deprecated": False,
                "warnings": [],
                "redirects": [],
                "removed": False,
                "builtin_alternative": "ansible.builtin.mount",
            }
        }
        violations = _run_with_intro([_task("ansible.posix.mount")], intro)
        m048 = _m048(violations)
        assert len(m048) == 1
        assert m048[0]["severity"] == "low"
        assert m048[0]["builtin_alternative"] == "ansible.builtin.mount"
        assert m048[0]["resolved_fqcn"] == "ansible.posix.mount"
        assert "Prefer builtin module" in str(m048[0]["message"])

    def test_community_copy_with_builtin_twin(self) -> None:
        """community.general.copy with builtin twin emits M048 (L030 parity)."""
        intro: dict[str, object] = {
            "community.general.copy": {
                "fqcn": "community.general.copy",
                "deprecated": False,
                "warnings": [],
                "redirects": [],
                "removed": False,
                "builtin_alternative": "ansible.builtin.copy",
            }
        }
        violations = _run_with_intro([_task("community.general.copy")], intro)
        m048 = _m048(violations)
        assert len(m048) == 1
        assert m048[0]["builtin_alternative"] == "ansible.builtin.copy"

    def test_no_builtin_equivalent(self) -> None:
        """community.general.timezone without builtin twin does not emit M048."""
        intro: dict[str, object] = {
            "community.general.timezone": {
                "fqcn": "community.general.timezone",
                "deprecated": False,
                "warnings": [],
                "redirects": [],
                "removed": False,
                "builtin_alternative": "",
            }
        }
        violations = _run_with_intro([_task("community.general.timezone")], intro)
        assert _m048(violations) == []

    def test_already_builtin(self) -> None:
        """ansible.builtin.copy does not emit M048."""
        intro: dict[str, object] = {
            "ansible.builtin.copy": {
                "fqcn": "ansible.builtin.copy",
                "deprecated": False,
                "warnings": [],
                "redirects": [],
                "removed": False,
                "builtin_alternative": "",
            }
        }
        violations = _run_with_intro([_task("ansible.builtin.copy")], intro)
        assert _m048(violations) == []

    def test_short_name_m001_only(self) -> None:
        """Short name copy → ansible.builtin.copy emits M001, not M048."""
        intro: dict[str, object] = {
            "copy": {
                "fqcn": "ansible.builtin.copy",
                "deprecated": False,
                "warnings": [],
                "redirects": [],
                "removed": False,
                "builtin_alternative": "",
            }
        }
        violations = _run_with_intro([_task("copy")], intro)
        assert len(_m001(violations)) == 1
        assert _m048(violations) == []

    def test_posix_sysctl_no_builtin(self) -> None:
        """ansible.posix.sysctl with no builtin resolve does not emit M048."""
        intro: dict[str, object] = {
            "ansible.posix.sysctl": {
                "fqcn": "ansible.posix.sysctl",
                "deprecated": False,
                "warnings": [],
                "redirects": [],
                "removed": False,
                "builtin_alternative": "",
            }
        }
        violations = _run_with_intro([_task("ansible.posix.sysctl")], intro)
        assert _m048(violations) == []

    def test_removed_module_skips_m048(self) -> None:
        """Tombstoned modules emit M004 and skip M048 even if alt is set."""
        intro: dict[str, object] = {
            "some.collection.gone": {
                "fqcn": "some.collection.gone",
                "deprecated": False,
                "warnings": [],
                "redirects": [],
                "removed": True,
                "removal_msg": "Module removed",
                "builtin_alternative": "ansible.builtin.copy",
            }
        }
        violations = _run_with_intro([_task("some.collection.gone")], intro)
        assert any(v.get("rule_id") == "M004" for v in violations)
        assert _m048(violations) == []
