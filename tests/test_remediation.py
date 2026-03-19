"""Tests for the remediation engine: registry, partition, transforms, convergence."""

import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import cast

from apme_engine.engine.models import RemediationClass, RemediationResolution, ViolationDict
from apme_engine.remediation.engine import RemediationEngine
from apme_engine.remediation.partition import (
    add_classification_to_violations,
    classify_violation,
    count_by_remediation_class,
    count_by_resolution,
    is_finding_resolvable,
    normalize_rule_id,
    partition_violations,
)
from apme_engine.remediation.registry import TransformRegistry, TransformResult
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms import build_default_registry
from apme_engine.remediation.transforms.L007_shell_to_command import fix_shell_to_command
from apme_engine.remediation.transforms.L008_local_action import fix_local_action
from apme_engine.remediation.transforms.L009_empty_string import fix_empty_string
from apme_engine.remediation.transforms.L011_literal_bool import fix_literal_bool
from apme_engine.remediation.transforms.L012_latest import fix_latest
from apme_engine.remediation.transforms.L013_changed_when import fix_changed_when
from apme_engine.remediation.transforms.L015_jinja_when import fix_jinja_when
from apme_engine.remediation.transforms.L018_become import fix_become
from apme_engine.remediation.transforms.L020_octal_mode import fix_octal_mode
from apme_engine.remediation.transforms.L021_missing_mode import fix_missing_mode
from apme_engine.remediation.transforms.L022_pipefail import fix_pipefail
from apme_engine.remediation.transforms.L025_name_casing import fix_name_casing
from apme_engine.remediation.transforms.L043_bare_vars import fix_bare_vars
from apme_engine.remediation.transforms.L046_no_free_form import fix_free_form
from apme_engine.remediation.transforms.M001_fqcn import fix_fqcn
from apme_engine.remediation.transforms.M006_become_unreachable import fix_become_unreachable
from apme_engine.remediation.transforms.M008_bare_include import fix_bare_include
from apme_engine.remediation.transforms.M009_with_to_loop import fix_with_to_loop


def _apply(
    fn: Callable[[StructuredFile, ViolationDict], bool],
    content: str,
    violation: ViolationDict,
) -> TransformResult:
    """Adapter: call a structured transform using the old (content, violation) interface.

    Args:
        fn: Structured transform function.
        content: YAML file content string.
        violation: Violation dict.

    Returns:
        TransformResult with serialized content and applied flag.
    """
    sf = StructuredFile.from_content("test.yml", content)
    if sf is None:
        return TransformResult(content, False)
    applied = fn(sf, violation)
    return TransformResult(sf.serialize() if applied else content, applied)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestTransformRegistry:
    """Tests for TransformRegistry register, apply, len, iter, rule_ids."""

    def test_register_and_contains(self) -> None:
        """Verifies register adds rule and contains checks membership."""
        reg = TransformRegistry()
        reg.register("L021", lambda c, v: TransformResult(c, False))
        assert "L021" in reg
        assert "L999" not in reg

    def test_apply_known_rule(self) -> None:
        """Verifies apply returns TransformResult with content and applied flag."""
        reg = TransformRegistry()
        reg.register("TEST", lambda c, v: TransformResult("fixed", True))
        result = reg.apply("TEST", "original", {})
        assert result.content == "fixed"
        assert result.applied is True

    def test_apply_unknown_rule(self) -> None:
        """Verifies apply returns original content and applied False for unknown rule."""
        reg = TransformRegistry()
        result = reg.apply("UNKNOWN", "original", {})
        assert result.content == "original"
        assert result.applied is False

    def test_len_and_iter(self) -> None:
        """Verifies len and iteration over registered rule IDs."""
        reg = TransformRegistry()
        reg.register("A", lambda c, v: TransformResult(c, False))
        reg.register("B", lambda c, v: TransformResult(c, False))
        assert len(reg) == 2
        assert set(reg) == {"A", "B"}

    def test_rule_ids_sorted(self) -> None:
        """Verifies rule_ids returns sorted list of registered IDs."""
        reg = TransformRegistry()
        reg.register("Z", lambda c, v: TransformResult(c, False))
        reg.register("A", lambda c, v: TransformResult(c, False))
        assert reg.rule_ids == ["A", "Z"]


# ---------------------------------------------------------------------------
# Partition
# ---------------------------------------------------------------------------


class TestPartition:
    """Tests for is_finding_resolvable and partition_violations tiers."""

    def test_is_finding_resolvable(self) -> None:
        """Verifies resolvable when rule_id in registry, not otherwise."""
        reg = TransformRegistry()
        reg.register("L021", lambda c, v: TransformResult(c, False))
        assert is_finding_resolvable({"rule_id": "L021"}, reg) is True
        assert is_finding_resolvable({"rule_id": "L999"}, reg) is False

    def test_normalize_rule_id_strips_native_prefix(self) -> None:
        """Verifies normalize_rule_id strips 'native:' prefix."""
        assert normalize_rule_id("native:L021") == "L021"
        assert normalize_rule_id("L021") == "L021"
        assert normalize_rule_id("native:M001") == "M001"
        assert normalize_rule_id("") == ""

    def test_is_finding_resolvable_with_native_prefix(self) -> None:
        """Verifies native:L021 is resolvable when L021 is registered."""
        reg = TransformRegistry()
        reg.register("L021", lambda c, v: TransformResult(c, False))
        assert is_finding_resolvable({"rule_id": "native:L021"}, reg) is True
        assert is_finding_resolvable({"rule_id": "native:L999"}, reg) is False

    def test_partition_native_prefix_to_tier1(self) -> None:
        """Verifies native:-prefixed violations partition into Tier 1."""
        reg = TransformRegistry()
        reg.register("L021", lambda c, v: TransformResult(c, False))

        violations: list[ViolationDict] = [
            {"rule_id": "native:L021"},
            {"rule_id": "R118"},
        ]
        t1, t2, t3 = partition_violations(violations, reg)
        assert len(t1) == 1
        assert t1[0]["rule_id"] == "native:L021"
        assert len(t2) == 1
        assert len(t3) == 0

    def test_partition_three_tiers(self) -> None:
        """Verifies partition_violations splits into resolvable, AI, manual tiers."""
        reg = TransformRegistry()
        reg.register("L021", lambda c, v: TransformResult(c, False))

        violations: list[ViolationDict] = [
            {"rule_id": "L021"},
            {"rule_id": "R118"},
            {"rule_id": "POLICY", "ai_proposable": False},
        ]
        t1, t2, t3 = partition_violations(violations, reg)
        assert len(t1) == 1
        assert t1[0]["rule_id"] == "L021"
        assert len(t2) == 1
        assert t2[0]["rule_id"] == "R118"
        assert len(t3) == 1
        assert t3[0]["rule_id"] == "POLICY"

    def test_classify_violation_auto_fixable(self) -> None:
        """Verifies classify_violation returns auto-fixable for registered rules."""
        reg = TransformRegistry()
        reg.register("L021", lambda c, v: TransformResult(c, False))
        assert classify_violation({"rule_id": "L021"}, reg) == RemediationClass.AUTO_FIXABLE
        assert classify_violation({"rule_id": "native:L021"}, reg) == RemediationClass.AUTO_FIXABLE

    def test_classify_violation_ai_candidate(self) -> None:
        """Verifies classify_violation returns ai-candidate for unregistered rules."""
        reg = TransformRegistry()
        assert classify_violation({"rule_id": "R118"}, reg) == RemediationClass.AI_CANDIDATE
        assert classify_violation({"rule_id": "L999", "ai_proposable": True}, reg) == RemediationClass.AI_CANDIDATE

    def test_classify_violation_manual_review(self) -> None:
        """Verifies classify_violation returns manual-review when ai_proposable is False."""
        reg = TransformRegistry()
        assert classify_violation({"rule_id": "POLICY", "ai_proposable": False}, reg) == RemediationClass.MANUAL_REVIEW

    def test_add_classification_to_violations(self) -> None:
        """Verifies add_classification_to_violations mutates violations in place."""
        reg = TransformRegistry()
        reg.register("L021", lambda c, v: TransformResult(c, False))

        violations: list[ViolationDict] = [
            {"rule_id": "L021"},
            {"rule_id": "R118"},
            {"rule_id": "POLICY", "ai_proposable": False},
        ]
        add_classification_to_violations(violations, reg)
        assert violations[0]["remediation_class"] == RemediationClass.AUTO_FIXABLE
        assert violations[1]["remediation_class"] == RemediationClass.AI_CANDIDATE
        assert violations[2]["remediation_class"] == RemediationClass.MANUAL_REVIEW
        for v in violations:
            assert v["remediation_resolution"] == RemediationResolution.UNRESOLVED

    def test_count_by_remediation_class(self) -> None:
        """Verifies count_by_remediation_class returns correct counts."""
        violations: list[ViolationDict] = [
            {"rule_id": "L021", "remediation_class": RemediationClass.AUTO_FIXABLE},
            {"rule_id": "L022", "remediation_class": RemediationClass.AUTO_FIXABLE},
            {"rule_id": "R118", "remediation_class": RemediationClass.AI_CANDIDATE},
            {"rule_id": "POLICY", "remediation_class": RemediationClass.MANUAL_REVIEW},
        ]
        counts = count_by_remediation_class(violations)
        assert counts[RemediationClass.AUTO_FIXABLE.value] == 2
        assert counts[RemediationClass.AI_CANDIDATE.value] == 1
        assert counts[RemediationClass.MANUAL_REVIEW.value] == 1

    def test_count_by_resolution(self) -> None:
        """Verifies count_by_resolution returns correct counts."""
        violations: list[ViolationDict] = [
            {"rule_id": "L021", "remediation_resolution": RemediationResolution.UNRESOLVED},
            {"rule_id": "L022", "remediation_resolution": RemediationResolution.TRANSFORM_FAILED},
            {"rule_id": "R118", "remediation_resolution": RemediationResolution.TRANSFORM_FAILED},
            {"rule_id": "POLICY", "remediation_resolution": RemediationResolution.OSCILLATION},
        ]
        counts = count_by_resolution(violations)
        assert counts[RemediationResolution.UNRESOLVED.value] == 1
        assert counts[RemediationResolution.TRANSFORM_FAILED.value] == 2
        assert counts[RemediationResolution.OSCILLATION.value] == 1

    def test_remediation_class_is_str_enum(self) -> None:
        """Verifies RemediationClass is iterable and members have string values."""
        assert list(RemediationClass) == [
            RemediationClass.AUTO_FIXABLE,
            RemediationClass.AI_CANDIDATE,
            RemediationClass.MANUAL_REVIEW,
        ]
        assert RemediationClass.AUTO_FIXABLE.value == "auto-fixable"

    def test_remediation_resolution_is_str_enum(self) -> None:
        """Verifies RemediationResolution members have string values."""
        assert RemediationResolution.UNRESOLVED.value == "unresolved"
        assert RemediationResolution.TRANSFORM_FAILED.value == "transform-failed"
        assert len(list(RemediationResolution)) == 8

    def test_all_registered_rules_classify(self) -> None:
        """Verifies every registered rule ID classifies as AUTO_FIXABLE."""
        reg = build_default_registry()
        for rule_id in reg:
            v: ViolationDict = {"rule_id": rule_id}
            assert classify_violation(v, reg) == RemediationClass.AUTO_FIXABLE, (
                f"Rule {rule_id} should classify as AUTO_FIXABLE"
            )


# ---------------------------------------------------------------------------
# Default registry
# ---------------------------------------------------------------------------


class TestDefaultRegistry:
    """Tests for build_default_registry rule coverage."""

    def test_build_default_registry(self) -> None:
        """Verifies default registry contains expected rule IDs and count."""
        reg = build_default_registry()
        for rule_id in (
            "L007",
            "L008",
            "L009",
            "L011",
            "L012",
            "L013",
            "L015",
            "L018",
            "L020",
            "L021",
            "L022",
            "L025",
            "L043",
            "L046",
            "M001",
            "M003",
            "M006",
            "M008",
            "M009",
        ):
            assert rule_id in reg, f"{rule_id} missing from default registry"
        assert len(reg) == 21


# ---------------------------------------------------------------------------
# L021 transform: missing mode
# ---------------------------------------------------------------------------


class TestL021MissingMode:
    """Tests for fix_missing_mode L021 transform."""

    def test_adds_mode_to_file_module(self) -> None:
        """Verifies adds mode 0644 to copy module without mode."""
        content = textwrap.dedent("""\
        - name: Copy a file
          ansible.builtin.copy:
            src: /tmp/foo
            dest: /tmp/bar
        """)
        result = _apply(fix_missing_mode, content, {"rule_id": "L021", "line": 1})
        assert result.applied is True
        assert "mode:" in result.content
        assert "0644" in result.content

    def test_no_change_when_mode_present(self) -> None:
        """Verifies no change when mode already set."""
        content = textwrap.dedent("""\
        - name: Copy a file
          ansible.builtin.copy:
            src: /tmp/foo
            dest: /tmp/bar
            mode: "0755"
        """)
        result = _apply(fix_missing_mode, content, {"rule_id": "L021", "line": 1})
        assert result.applied is False

    def test_no_change_for_non_file_module(self) -> None:
        """Verifies no change for command module."""
        content = textwrap.dedent("""\
        - name: Run command
          ansible.builtin.command: echo hello
        """)
        result = _apply(fix_missing_mode, content, {"rule_id": "L021", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Template it
          ansible.builtin.template:
            src: foo.j2
            dest: /etc/foo.conf
        """)
        r1 = _apply(fix_missing_mode, content, {"rule_id": "L021", "line": 1})
        assert r1.applied is True
        r2 = _apply(fix_missing_mode, r1.content, {"rule_id": "L021", "line": 1})
        assert r2.applied is False

    def test_handles_invalid_yaml(self) -> None:
        """Verifies invalid YAML returns applied False without raising."""
        result = _apply(fix_missing_mode, "{{{{invalid", {"rule_id": "L021", "line": 1})
        assert result.applied is False


# ---------------------------------------------------------------------------
# L007 transform: shell to command
# ---------------------------------------------------------------------------


class TestL007ShellToCommand:
    """Tests for fix_shell_to_command L007 transform."""

    def test_replaces_shell_with_command(self) -> None:
        """Verifies shell replaced with command for simple command."""
        content = textwrap.dedent("""\
        - name: List files
          ansible.builtin.shell: ls -la /tmp
        """)
        result = _apply(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is True
        assert "ansible.builtin.command" in result.content
        assert "ansible.builtin.shell" not in result.content

    def test_no_change_when_pipe_present(self) -> None:
        """Verifies no change when pipe in command."""
        content = textwrap.dedent("""\
        - name: Grep output
          ansible.builtin.shell: cat /tmp/log | grep error
        """)
        result = _apply(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_when_and_present(self) -> None:
        """Verifies no change when && in command."""
        content = textwrap.dedent("""\
        - name: Chain commands
          ansible.builtin.shell:
            cmd: mkdir /tmp/foo && touch /tmp/foo/bar
        """)
        result = _apply(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_when_redirect_present(self) -> None:
        """Verifies no change when redirect in command."""
        content = textwrap.dedent("""\
        - name: Write output
          ansible.builtin.shell: echo hello > /tmp/out
        """)
        result = _apply(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_for_command_module(self) -> None:
        """Verifies no change when already using command module."""
        content = textwrap.dedent("""\
        - name: Already command
          ansible.builtin.command: echo hello
        """)
        result = _apply(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Simple command
          ansible.builtin.shell: whoami
        """)
        r1 = _apply(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert r1.applied is True
        r2 = _apply(fix_shell_to_command, r1.content, {"rule_id": "L007", "line": 1})
        assert r2.applied is False

    def test_dict_form_cmd(self) -> None:
        """Verifies shell with cmd dict form converted to command."""
        content = textwrap.dedent("""\
        - name: Simple command
          ansible.builtin.shell:
            cmd: whoami
        """)
        result = _apply(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is True
        assert "ansible.builtin.command" in result.content


# ---------------------------------------------------------------------------
# M001/M003 transform: FQCN
# ---------------------------------------------------------------------------


class TestFQCNTransform:
    """Tests for fix_fqcn M001/M003 transforms."""

    def test_rewrites_short_name_with_resolved_fqcn(self) -> None:
        """Verifies short module name replaced with resolved_fqcn from violation."""
        content = textwrap.dedent("""\
        - name: Debug message
          debug:
            msg: hello
        """)
        violation = cast(
            ViolationDict,
            {
                "rule_id": "M001",
                "line": 1,
                "resolved_fqcn": "ansible.builtin.debug",
                "original_module": "debug",
            },
        )
        result = _apply(fix_fqcn, content, violation)
        assert result.applied is True
        assert "ansible.builtin.debug" in result.content
        assert "\n  debug:" not in result.content

    def test_falls_back_to_static_map(self) -> None:
        """Verifies static FQCN map used when no resolved_fqcn."""
        content = textwrap.dedent("""\
        - name: Copy file
          copy:
            src: /a
            dest: /b
        """)
        violation = cast(ViolationDict, {"rule_id": "M001", "line": 1})
        result = _apply(fix_fqcn, content, violation)
        assert result.applied is True
        assert "ansible.builtin.copy" in result.content

    def test_no_change_when_already_fqcn(self) -> None:
        """Verifies no change when module already FQCN."""
        content = textwrap.dedent("""\
        - name: Debug message
          ansible.builtin.debug:
            msg: hello
        """)
        violation = cast(ViolationDict, {"rule_id": "M001", "line": 1})
        result = _apply(fix_fqcn, content, violation)
        assert result.applied is False

    def test_no_change_for_unknown_short_name(self) -> None:
        """Verifies no change for unknown short module name."""
        content = textwrap.dedent("""\
        - name: Custom module
          my_custom_module:
            param: value
        """)
        violation = cast(ViolationDict, {"rule_id": "M001", "line": 1})
        result = _apply(fix_fqcn, content, violation)
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Install package
          yum:
            name: httpd
            state: present
        """)
        r1 = _apply(fix_fqcn, content, cast(ViolationDict, {"rule_id": "M001", "line": 1}))
        assert r1.applied is True
        r2 = _apply(fix_fqcn, r1.content, cast(ViolationDict, {"rule_id": "M001", "line": 1}))
        assert r2.applied is False

    def test_m003_redirect_uses_resolved_fqcn(self) -> None:
        """Verifies M003 uses resolved_fqcn for module redirect."""
        content = textwrap.dedent("""\
        - name: Install package
          yum:
            name: httpd
        """)
        violation = cast(
            ViolationDict,
            {
                "rule_id": "M003",
                "line": 1,
                "original_module": "yum",
                "resolved_fqcn": "ansible.builtin.dnf",
                "redirect_chain": ["ansible.builtin.yum", "ansible.builtin.dnf"],
            },
        )
        result = _apply(fix_fqcn, content, violation)
        assert result.applied is True
        assert "ansible.builtin.dnf" in result.content


# ---------------------------------------------------------------------------
# RemediationEngine convergence loop
# ---------------------------------------------------------------------------


class TestRemediationEngine:
    """Tests for RemediationEngine convergence, apply, oscillation, report tiers."""

    def test_converges_in_one_pass(self, tmp_path: Path) -> None:
        """Verifies engine fixes violations and converges in one pass when apply=True.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        playbook = tmp_path / "play.yml"
        playbook.write_text(
            textwrap.dedent("""\
        - name: Copy file
          ansible.builtin.copy:
            src: /a
            dest: /b
        """)
        )

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            content = playbook.read_text()
            if "mode:" not in content:
                return [{"rule_id": "L021", "file": str(playbook), "line": 1}]
            return []

        reg = TransformRegistry()
        reg.register("L021", structured=fix_missing_mode)
        engine = RemediationEngine(reg, scan_fn, max_passes=5)

        report = engine.remediate([str(playbook)], apply=True)
        assert report.fixed >= 1
        assert report.oscillation_detected is False
        assert "mode:" in playbook.read_text()

    def test_no_apply_restores_originals(self, tmp_path: Path) -> None:
        """Verifies apply=False leaves file unchanged but reports patches.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        playbook = tmp_path / "play.yml"
        original = textwrap.dedent("""\
        - name: Copy file
          ansible.builtin.copy:
            src: /a
            dest: /b
        """)
        playbook.write_text(original)

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            content = playbook.read_text()
            if "mode:" not in content:
                return [{"rule_id": "L021", "file": str(playbook), "line": 1}]
            return []

        reg = TransformRegistry()
        reg.register("L021", structured=fix_missing_mode)
        engine = RemediationEngine(reg, scan_fn, max_passes=5)

        report = engine.remediate([str(playbook)], apply=False)
        assert report.fixed >= 1
        assert len(report.applied_patches) == 1
        assert report.applied_patches[0].diff != ""
        assert playbook.read_text() == original

    def test_oscillation_detection(self, tmp_path: Path) -> None:
        """Verifies oscillation_detected when transforms cause infinite loop.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        playbook = tmp_path / "play.yml"
        playbook.write_text("- name: test\n  debug: msg=hi\n")

        call_count = [0]

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            call_count[0] += 1
            return [{"rule_id": "FLIP", "file": str(playbook), "line": 1}]

        def flip_transform(content: str, violation: ViolationDict) -> TransformResult:
            return TransformResult(content + "\n# flipped", True)

        reg = TransformRegistry()
        reg.register("FLIP", flip_transform)
        engine = RemediationEngine(reg, scan_fn, max_passes=3)

        report = engine.remediate([str(playbook)], apply=True)
        assert report.oscillation_detected is True
        assert report.passes <= 3

    def test_empty_scan_no_changes(self, tmp_path: Path) -> None:
        """Verifies no fixes when scan returns no violations.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        playbook = tmp_path / "play.yml"
        playbook.write_text("- name: Clean\n  ansible.builtin.debug:\n    msg: hi\n")

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            return []

        reg = build_default_registry()
        engine = RemediationEngine(reg, scan_fn, max_passes=5)

        report = engine.remediate([str(playbook)], apply=True)
        assert report.fixed == 0
        assert report.passes == 1
        assert report.oscillation_detected is False

    def test_resolves_relative_file_path(self, tmp_path: Path) -> None:
        """Verifies violations with relative file paths are resolved to absolute paths.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        playbook = tmp_path / "play.yml"
        playbook.write_text(
            textwrap.dedent("""\
        - name: Copy file
          ansible.builtin.copy:
            src: /a
            dest: /b
        """)
        )

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            content = playbook.read_text()
            if "mode:" not in content:
                return [{"rule_id": "L021", "file": "play.yml", "line": 1}]
            return []

        reg = TransformRegistry()
        reg.register("L021", structured=fix_missing_mode)
        engine = RemediationEngine(reg, scan_fn, max_passes=5)

        report = engine.remediate([str(playbook)], apply=True)
        assert report.fixed >= 1
        assert "mode:" in playbook.read_text()

    def test_resolves_native_prefixed_rule_id(self, tmp_path: Path) -> None:
        """Verifies violations with native:-prefixed rule IDs are matched to transforms.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        playbook = tmp_path / "play.yml"
        playbook.write_text(
            textwrap.dedent("""\
        - name: Copy file
          ansible.builtin.copy:
            src: /a
            dest: /b
        """)
        )

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            content = playbook.read_text()
            if "mode:" not in content:
                return [{"rule_id": "native:L021", "file": str(playbook), "line": 1}]
            return []

        reg = TransformRegistry()
        reg.register("L021", structured=fix_missing_mode)
        engine = RemediationEngine(reg, scan_fn, max_passes=5)

        report = engine.remediate([str(playbook)], apply=True)
        assert report.fixed >= 1
        assert "mode:" in playbook.read_text()

    def test_ambiguous_basename_skipped(self, tmp_path: Path) -> None:
        """Verifies violations with ambiguous basenames are skipped, not misapplied.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        play_a = dir_a / "main.yml"
        play_b = dir_b / "main.yml"
        play_a.write_text(
            textwrap.dedent("""\
        - name: Copy A
          ansible.builtin.copy:
            src: /a
            dest: /b
        """)
        )
        play_b.write_text(
            textwrap.dedent("""\
        - name: Copy B
          ansible.builtin.copy:
            src: /c
            dest: /d
        """)
        )

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            return [{"rule_id": "L021", "file": "main.yml", "line": 1}]

        reg = TransformRegistry()
        reg.register("L021", structured=fix_missing_mode)
        engine = RemediationEngine(reg, scan_fn, max_passes=2)

        report = engine.remediate([str(play_a), str(play_b)], apply=False)
        assert report.fixed == 0
        assert "mode:" not in play_a.read_text()
        assert "mode:" not in play_b.read_text()

    def test_report_tiers(self, tmp_path: Path) -> None:
        """Verifies remaining_ai and remaining_manual populated from partition.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        playbook = tmp_path / "play.yml"
        playbook.write_text("- name: test\n  debug: msg=hi\n")

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            return [
                {"rule_id": "UNKNOWN_AI", "file": str(playbook), "line": 1},
                {"rule_id": "MANUAL", "file": str(playbook), "line": 1, "ai_proposable": False},
            ]

        reg = TransformRegistry()
        engine = RemediationEngine(reg, scan_fn, max_passes=1)

        report = engine.remediate([str(playbook)], apply=False)
        assert len(report.remaining_ai) == 1
        assert len(report.remaining_manual) == 1
        assert report.remaining_ai[0]["rule_id"] == "UNKNOWN_AI"
        assert report.remaining_manual[0]["rule_id"] == "MANUAL"

    def test_transform_failure_sets_resolution(self, tmp_path: Path) -> None:
        """Verifies transform returning applied=False sets TRANSFORM_FAILED resolution.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        playbook = tmp_path / "play.yml"
        playbook.write_text("- name: test\n  debug: msg=hi\n")

        captured: list[ViolationDict] = []

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            return [{"rule_id": "STUB", "file": str(playbook), "line": 1}]

        def noop_transform(content: str, violation: ViolationDict) -> TransformResult:
            captured.append(violation)
            return TransformResult(content, False)

        reg = TransformRegistry()
        reg.register("STUB", noop_transform)
        engine = RemediationEngine(reg, scan_fn, max_passes=2)

        engine.remediate([str(playbook)], apply=False)
        assert len(captured) >= 1
        assert captured[0].get("remediation_class") == RemediationClass.AI_CANDIDATE
        assert captured[0].get("remediation_resolution") == RemediationResolution.TRANSFORM_FAILED

    def test_oscillation_sets_resolution(self, tmp_path: Path) -> None:
        """Verifies oscillation sets OSCILLATION resolution on remaining Tier 1 violations.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        playbook = tmp_path / "play.yml"
        playbook.write_text("- name: test\n  debug: msg=hi\n")

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            return [{"rule_id": "FLIP", "file": str(playbook), "line": 1}]

        def flip_transform(content: str, violation: ViolationDict) -> TransformResult:
            return TransformResult(content + "\n# flipped", True)

        reg = TransformRegistry()
        reg.register("FLIP", flip_transform)
        engine = RemediationEngine(reg, scan_fn, max_passes=3)

        report = engine.remediate([str(playbook)], apply=True)
        assert report.oscillation_detected is True

    def test_remaining_violations_have_classification(self, tmp_path: Path) -> None:
        """Verifies remaining violations carry remediation_class and remediation_resolution.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        playbook = tmp_path / "play.yml"
        playbook.write_text("- name: test\n  debug: msg=hi\n")

        def scan_fn(paths: list[str]) -> list[ViolationDict]:
            return [
                {"rule_id": "UNKNOWN", "file": str(playbook), "line": 1},
                {"rule_id": "MANUAL", "file": str(playbook), "line": 2, "ai_proposable": False},
            ]

        reg = TransformRegistry()
        engine = RemediationEngine(reg, scan_fn, max_passes=1)

        report = engine.remediate([str(playbook)], apply=False)
        for v in report.remaining_ai + report.remaining_manual:
            assert "remediation_class" in v
            assert "remediation_resolution" in v
            assert v["remediation_resolution"] == RemediationResolution.UNRESOLVED


# ---------------------------------------------------------------------------
# L008 transform: local_action
# ---------------------------------------------------------------------------


class TestL008LocalAction:
    """Tests for fix_local_action L008 transform."""

    def test_string_form(self) -> None:
        """Verifies local_action string form converted to delegate_to + module."""
        content = textwrap.dedent("""\
        - name: Run locally
          local_action: command echo hello
        """)
        result = _apply(fix_local_action, content, {"rule_id": "L008", "line": 1})
        assert result.applied is True
        assert "local_action" not in result.content
        assert "delegate_to: localhost" in result.content
        assert "command:" in result.content or "ansible.builtin.command:" in result.content

    def test_dict_form(self) -> None:
        """Verifies local_action dict form converted to delegate_to + module."""
        content = textwrap.dedent("""\
        - name: Run locally
          local_action:
            module: ansible.builtin.debug
            msg: hi
        """)
        result = _apply(fix_local_action, content, {"rule_id": "L008", "line": 1})
        assert result.applied is True
        assert "local_action" not in result.content
        assert "delegate_to: localhost" in result.content
        assert "ansible.builtin.debug" in result.content

    def test_no_change_without_local_action(self) -> None:
        """Verifies no change when task has no local_action."""
        content = textwrap.dedent("""\
        - name: Normal task
          ansible.builtin.debug:
            msg: hi
        """)
        result = _apply(fix_local_action, content, {"rule_id": "L008", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Run locally
          local_action: command whoami
        """)
        r1 = _apply(fix_local_action, content, {"rule_id": "L008", "line": 1})
        assert r1.applied is True
        r2 = _apply(fix_local_action, r1.content, {"rule_id": "L008", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# L009 transform: empty string comparison
# ---------------------------------------------------------------------------


class TestL009EmptyString:
    """Tests for fix_empty_string L009 transform."""

    def test_double_quote_equality(self) -> None:
        """Verifies == '' replaced with | length == 0."""
        content = textwrap.dedent("""\
        - name: Check var
          ansible.builtin.debug:
            msg: empty
          when: myvar == ""
        """)
        result = _apply(fix_empty_string, content, {"rule_id": "L009", "line": 1})
        assert result.applied is True
        assert "myvar | length == 0" in result.content

    def test_single_quote_inequality(self) -> None:
        """Verifies != '' replaced with | length > 0."""
        content = textwrap.dedent("""\
        - name: Check var
          ansible.builtin.debug:
            msg: not empty
          when: myvar != ''
        """)
        result = _apply(fix_empty_string, content, {"rule_id": "L009", "line": 1})
        assert result.applied is True
        assert "myvar | length > 0" in result.content

    def test_no_change_without_pattern(self) -> None:
        """Verifies no change when when clause has no empty string comparison."""
        content = textwrap.dedent("""\
        - name: Check var
          ansible.builtin.debug:
            msg: ok
          when: myvar is defined
        """)
        result = _apply(fix_empty_string, content, {"rule_id": "L009", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: empty
          when: myvar == ""
        """)
        r1 = _apply(fix_empty_string, content, {"rule_id": "L009", "line": 1})
        r2 = _apply(fix_empty_string, r1.content, {"rule_id": "L009", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# L011 transform: literal bool comparison
# ---------------------------------------------------------------------------


class TestL011LiteralBool:
    """Tests for fix_literal_bool L011 transform."""

    def test_eq_true(self) -> None:
        """Verifies == true replaced with bare variable."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: enabled == true
        """)
        result = _apply(fix_literal_bool, content, {"rule_id": "L011", "line": 1})
        assert result.applied is True
        assert "enabled" in result.content
        assert "== true" not in result.content

    def test_eq_false(self) -> None:
        """Verifies == false replaced with not variable."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: enabled == false
        """)
        result = _apply(fix_literal_bool, content, {"rule_id": "L011", "line": 1})
        assert result.applied is True
        assert "not enabled" in result.content

    def test_is_true(self) -> None:
        """Verifies is true replaced with bare variable."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: enabled is true
        """)
        result = _apply(fix_literal_bool, content, {"rule_id": "L011", "line": 1})
        assert result.applied is True
        assert "is true" not in result.content

    def test_python_True(self) -> None:
        """Verifies == True (Python) replaced with bare variable."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: enabled == True
        """)
        result = _apply(fix_literal_bool, content, {"rule_id": "L011", "line": 1})
        assert result.applied is True

    def test_no_change_without_pattern(self) -> None:
        """Verifies no change when when clause has no literal bool comparison."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: enabled
        """)
        result = _apply(fix_literal_bool, content, {"rule_id": "L011", "line": 1})
        assert result.applied is False


# ---------------------------------------------------------------------------
# L015 transform: Jinja in when
# ---------------------------------------------------------------------------


class TestL015JinjaWhen:
    """Tests for fix_jinja_when L015 transform."""

    def test_strips_jinja_delimiters(self) -> None:
        """Verifies {{ }} stripped from when clause."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: "{{ my_var }}"
        """)
        result = _apply(fix_jinja_when, content, {"rule_id": "L015", "line": 1})
        assert result.applied is True
        assert "{{" not in result.content
        assert "}}" not in result.content
        assert "my_var" in result.content

    def test_no_change_without_jinja(self) -> None:
        """Verifies no change when when clause has no Jinja delimiters."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: my_var is defined
        """)
        result = _apply(fix_jinja_when, content, {"rule_id": "L015", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: "{{ some_flag }}"
        """)
        r1 = _apply(fix_jinja_when, content, {"rule_id": "L015", "line": 1})
        r2 = _apply(fix_jinja_when, r1.content, {"rule_id": "L015", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# L020 transform: octal mode
# ---------------------------------------------------------------------------


class TestL020OctalMode:
    """Tests for fix_octal_mode L020 transform."""

    def test_octal_literal_to_string(self) -> None:
        """YAML 1.1 parses 0644 as octal int 420; should become '0644'."""
        content = textwrap.dedent("""\
        - name: Set perms
          ansible.builtin.file:
            path: /tmp/foo
            mode: 0644
        """)
        result = fix_octal_mode(content, {"rule_id": "L020", "line": 1})
        assert result.applied is True
        assert "0644" in result.content

    def test_decimal_int_to_octal_string(self) -> None:
        """Bare 644 is decimal in YAML; digits are all valid octal, treated as intended octal."""
        content = textwrap.dedent("""\
        - name: Set perms
          ansible.builtin.file:
            path: /tmp/foo
            mode: 644
        """)
        result = fix_octal_mode(content, {"rule_id": "L020", "line": 1})
        assert result.applied is True
        assert "0644" in result.content

    def test_no_change_when_already_string(self) -> None:
        """Verifies no change when mode already quoted string."""
        content = textwrap.dedent("""\
        - name: Set perms
          ansible.builtin.file:
            path: /tmp/foo
            mode: "0644"
        """)
        result = fix_octal_mode(content, {"rule_id": "L020", "line": 1})
        assert result.applied is False

    def test_string_without_leading_zero(self) -> None:
        """Verifies string 644 converted to 0644."""
        content = textwrap.dedent("""\
        - name: Set perms
          ansible.builtin.file:
            path: /tmp/foo
            mode: "644"
        """)
        result = fix_octal_mode(content, {"rule_id": "L020", "line": 1})
        assert result.applied is True
        assert "0644" in result.content


# ---------------------------------------------------------------------------
# L025 transform: name casing
# ---------------------------------------------------------------------------


class TestL025NameCasing:
    """Tests for fix_name_casing L025 transform."""

    def test_capitalizes_task_name(self) -> None:
        """Verifies task name capitalized (first letter uppercase)."""
        content = textwrap.dedent("""\
        - name: install packages
          ansible.builtin.debug:
            msg: hi
        """)
        result = _apply(fix_name_casing, content, {"rule_id": "L025", "line": 1})
        assert result.applied is True
        assert "Install packages" in result.content

    def test_no_change_when_already_uppercase(self) -> None:
        """Verifies no change when name already capitalized."""
        content = textwrap.dedent("""\
        - name: Install packages
          ansible.builtin.debug:
            msg: hi
        """)
        result = _apply(fix_name_casing, content, {"rule_id": "L025", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: setup network
          ansible.builtin.debug:
            msg: hi
        """)
        r1 = _apply(fix_name_casing, content, {"rule_id": "L025", "line": 1})
        r2 = _apply(fix_name_casing, r1.content, {"rule_id": "L025", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# L046 transform: free-form to dict
# ---------------------------------------------------------------------------


class TestL046FreeForm:
    """Tests for fix_free_form L046 transform."""

    def test_converts_string_to_dict(self) -> None:
        """Verifies free-form string converted to cmd: dict form."""
        content = textwrap.dedent("""\
        - name: Run it
          ansible.builtin.command: echo hello
        """)
        result = _apply(fix_free_form, content, {"rule_id": "L046", "line": 1})
        assert result.applied is True
        assert "cmd:" in result.content
        assert "echo hello" in result.content

    def test_no_change_when_already_dict(self) -> None:
        """Verifies no change when command already uses dict form."""
        content = textwrap.dedent("""\
        - name: Run it
          ansible.builtin.command:
            cmd: echo hello
        """)
        result = _apply(fix_free_form, content, {"rule_id": "L046", "line": 1})
        assert result.applied is False

    def test_no_change_for_non_command_module(self) -> None:
        """Verifies no change for non-command/shell modules."""
        content = textwrap.dedent("""\
        - name: Debug
          ansible.builtin.debug:
            msg: hi
        """)
        result = _apply(fix_free_form, content, {"rule_id": "L046", "line": 1})
        assert result.applied is False

    def test_shell_module(self) -> None:
        """Verifies shell module free-form converted to cmd dict."""
        content = textwrap.dedent("""\
        - name: Run shell
          ansible.builtin.shell: cat /etc/hosts | grep localhost
        """)
        result = _apply(fix_free_form, content, {"rule_id": "L046", "line": 1})
        assert result.applied is True
        assert "cmd:" in result.content


# ---------------------------------------------------------------------------
# L043 transform: bare vars
# ---------------------------------------------------------------------------


class TestL043BareVars:
    """Tests for fix_bare_vars L043 transform."""

    def test_wraps_bare_var_in_with_items(self) -> None:
        """Verifies bare var in with_items wrapped in {{ }}."""
        content = textwrap.dedent("""\
        - name: Loop
          ansible.builtin.debug:
            msg: "{{ item }}"
          with_items: packages
        """)
        result = _apply(fix_bare_vars, content, {"rule_id": "L043", "line": 1})
        assert result.applied is True
        assert "{{ packages }}" in result.content

    def test_no_change_when_already_jinja(self) -> None:
        """Verifies no change when with_items already uses Jinja."""
        content = textwrap.dedent("""\
        - name: Loop
          ansible.builtin.debug:
            msg: "{{ item }}"
          with_items: "{{ packages }}"
        """)
        result = _apply(fix_bare_vars, content, {"rule_id": "L043", "line": 1})
        assert result.applied is False

    def test_no_change_without_loop(self) -> None:
        """Verifies no change when task has no loop."""
        content = textwrap.dedent("""\
        - name: Simple
          ansible.builtin.debug:
            msg: hi
        """)
        result = _apply(fix_bare_vars, content, {"rule_id": "L043", "line": 1})
        assert result.applied is False


# ---------------------------------------------------------------------------
# L013 transform: changed_when
# ---------------------------------------------------------------------------


class TestL013ChangedWhen:
    """Tests for fix_changed_when L013 transform."""

    def test_adds_changed_when(self) -> None:
        """Verifies changed_when added to command without creates."""
        content = textwrap.dedent("""\
        - name: Check version
          ansible.builtin.command: python --version
        """)
        result = _apply(fix_changed_when, content, {"rule_id": "L013", "line": 1})
        assert result.applied is True
        assert "changed_when:" in result.content

    def test_no_change_when_creates_present(self) -> None:
        """Verifies no change when command has creates parameter."""
        content = textwrap.dedent("""\
        - name: Create file
          ansible.builtin.command:
            cmd: touch /tmp/foo
            creates: /tmp/foo
        """)
        result = _apply(fix_changed_when, content, {"rule_id": "L013", "line": 1})
        assert result.applied is False

    def test_no_change_when_already_set(self) -> None:
        """Verifies no change when changed_when already present."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.command: echo test
          changed_when: false
        """)
        result = _apply(fix_changed_when, content, {"rule_id": "L013", "line": 1})
        assert result.applied is False

    def test_no_change_for_non_command_module(self) -> None:
        """Verifies no change for non-command modules."""
        content = textwrap.dedent("""\
        - name: Debug
          ansible.builtin.debug:
            msg: hi
        """)
        result = _apply(fix_changed_when, content, {"rule_id": "L013", "line": 1})
        assert result.applied is False


# ---------------------------------------------------------------------------
# L018 transform: become
# ---------------------------------------------------------------------------


class TestL018Become:
    """Tests for fix_become L018 transform."""

    def test_adds_become(self) -> None:
        """Verifies become: true added when become_user present without become."""
        content = textwrap.dedent("""\
        - name: Switch user
          ansible.builtin.command: whoami
          become_user: postgres
        """)
        result = _apply(fix_become, content, {"rule_id": "L018", "line": 1})
        assert result.applied is True
        assert "become: true" in result.content or "become: True" in result.content

    def test_no_change_when_become_present(self) -> None:
        """Verifies no change when become already set."""
        content = textwrap.dedent("""\
        - name: Switch user
          ansible.builtin.command: whoami
          become: true
          become_user: postgres
        """)
        result = _apply(fix_become, content, {"rule_id": "L018", "line": 1})
        assert result.applied is False

    def test_no_change_without_become_user(self) -> None:
        """Verifies no change when become_user not present."""
        content = textwrap.dedent("""\
        - name: Normal
          ansible.builtin.debug:
            msg: hi
        """)
        result = _apply(fix_become, content, {"rule_id": "L018", "line": 1})
        assert result.applied is False

    def test_become_inserted_after_become_user(self) -> None:
        """Verifies become inserted immediately after become_user line."""
        content = textwrap.dedent("""\
        - name: Switch user
          ansible.builtin.command: whoami
          become_user: postgres
        """)
        result = _apply(fix_become, content, {"rule_id": "L018", "line": 1})
        assert result.applied is True
        lines = result.content.splitlines()
        bu_line = next(i for i, line in enumerate(lines) if "become_user" in line)
        b_line = next(i for i, line in enumerate(lines) if line.strip().startswith("become:"))
        assert b_line == bu_line + 1


# ---------------------------------------------------------------------------
# L022 transform: pipefail
# ---------------------------------------------------------------------------


class TestL022Pipefail:
    """Tests for fix_pipefail L022 transform."""

    def test_prepends_pipefail_string_form(self) -> None:
        """Verifies set -o pipefail && prepended to string form shell cmd."""
        content = textwrap.dedent("""\
        - name: Grep logs
          ansible.builtin.shell: cat /var/log/syslog | grep error
        """)
        result = _apply(fix_pipefail, content, {"rule_id": "L022", "line": 1})
        assert result.applied is True
        assert "set -o pipefail &&" in result.content

    def test_prepends_pipefail_dict_form(self) -> None:
        """Verifies set -o pipefail && prepended to dict form shell cmd."""
        content = textwrap.dedent("""\
        - name: Grep logs
          ansible.builtin.shell:
            cmd: cat /var/log/syslog | grep error
        """)
        result = _apply(fix_pipefail, content, {"rule_id": "L022", "line": 1})
        assert result.applied is True
        assert "set -o pipefail &&" in result.content

    def test_no_change_without_pipe(self) -> None:
        """Verifies no change when shell command has no pipe."""
        content = textwrap.dedent("""\
        - name: Simple
          ansible.builtin.shell: echo hello
        """)
        result = _apply(fix_pipefail, content, {"rule_id": "L022", "line": 1})
        assert result.applied is False

    def test_no_change_when_already_set(self) -> None:
        """Verifies no change when pipefail already in cmd."""
        content = textwrap.dedent("""\
        - name: Grep logs
          ansible.builtin.shell: set -o pipefail && cat /var/log/syslog | grep error
        """)
        result = _apply(fix_pipefail, content, {"rule_id": "L022", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Grep
          ansible.builtin.shell: cat log | grep err
        """)
        r1 = _apply(fix_pipefail, content, {"rule_id": "L022", "line": 1})
        r2 = _apply(fix_pipefail, r1.content, {"rule_id": "L022", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# L012 transform: latest → present
# ---------------------------------------------------------------------------


class TestL012Latest:
    """Tests for fix_latest L012 transform."""

    def test_replaces_latest_with_present(self) -> None:
        """Verifies state: latest replaced with state: present."""
        content = textwrap.dedent("""\
        - name: Install httpd
          ansible.builtin.yum:
            name: httpd
            state: latest
        """)
        result = _apply(fix_latest, content, {"rule_id": "L012", "line": 1})
        assert result.applied is True
        assert "state: present" in result.content
        assert "state: latest" not in result.content

    def test_no_change_when_present(self) -> None:
        """Verifies no change when state already present."""
        content = textwrap.dedent("""\
        - name: Install httpd
          ansible.builtin.yum:
            name: httpd
            state: present
        """)
        result = _apply(fix_latest, content, {"rule_id": "L012", "line": 1})
        assert result.applied is False

    def test_no_change_for_absent(self) -> None:
        """Verifies no change when state is absent."""
        content = textwrap.dedent("""\
        - name: Remove httpd
          ansible.builtin.yum:
            name: httpd
            state: absent
        """)
        result = _apply(fix_latest, content, {"rule_id": "L012", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Install
          ansible.builtin.apt:
            name: nginx
            state: latest
        """)
        r1 = _apply(fix_latest, content, {"rule_id": "L012", "line": 1})
        r2 = _apply(fix_latest, r1.content, {"rule_id": "L012", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# M006 transform: become + ignore_errors -> add ignore_unreachable
# ---------------------------------------------------------------------------


class TestM006BecomeUnreachable:
    """Tests for fix_become_unreachable M006 transform."""

    def test_adds_ignore_unreachable(self) -> None:
        """Verifies ignore_unreachable added when become and ignore_errors present."""
        content = textwrap.dedent("""\
        - name: Risky task
          ansible.builtin.command: whoami
          become: true
          ignore_errors: true
        """)
        result = _apply(fix_become_unreachable, content, {"rule_id": "M006", "line": 1})
        assert result.applied is True
        assert "ignore_unreachable: true" in result.content or "ignore_unreachable: True" in result.content

    def test_no_change_when_already_set(self) -> None:
        """Verifies no change when ignore_unreachable already present."""
        content = textwrap.dedent("""\
        - name: Safe task
          ansible.builtin.command: whoami
          become: true
          ignore_errors: true
          ignore_unreachable: true
        """)
        result = _apply(fix_become_unreachable, content, {"rule_id": "M006", "line": 1})
        assert result.applied is False

    def test_no_change_without_become(self) -> None:
        """Verifies no change when become not present."""
        content = textwrap.dedent("""\
        - name: Normal task
          ansible.builtin.command: whoami
          ignore_errors: true
        """)
        result = _apply(fix_become_unreachable, content, {"rule_id": "M006", "line": 1})
        assert result.applied is False

    def test_inserted_after_ignore_errors(self) -> None:
        """Verifies ignore_unreachable inserted after ignore_errors line."""
        content = textwrap.dedent("""\
        - name: Risky task
          ansible.builtin.command: whoami
          become: true
          ignore_errors: true
        """)
        result = _apply(fix_become_unreachable, content, {"rule_id": "M006", "line": 1})
        lines = result.content.splitlines()
        ie_line = next(i for i, line in enumerate(lines) if "ignore_errors" in line)
        iu_line = next(i for i, line in enumerate(lines) if "ignore_unreachable" in line)
        assert iu_line == ie_line + 1


# ---------------------------------------------------------------------------
# M008 transform: bare include -> include_tasks
# ---------------------------------------------------------------------------


class TestM008BareInclude:
    """Tests for fix_bare_include M008 transform."""

    def test_replaces_include(self) -> None:
        """Verifies include replaced with ansible.builtin.include_tasks."""
        content = textwrap.dedent("""\
        - include: tasks/setup.yml
        """)
        result = _apply(fix_bare_include, content, {"rule_id": "M008", "line": 1})
        assert result.applied is True
        assert "ansible.builtin.include_tasks" in result.content
        assert "\n- include:" not in result.content

    def test_no_change_for_include_tasks(self) -> None:
        """Verifies no change when already using include_tasks."""
        content = textwrap.dedent("""\
        - ansible.builtin.include_tasks: tasks/setup.yml
        """)
        result = _apply(fix_bare_include, content, {"rule_id": "M008", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - include: tasks/setup.yml
        """)
        r1 = _apply(fix_bare_include, content, {"rule_id": "M008", "line": 1})
        r2 = _apply(fix_bare_include, r1.content, {"rule_id": "M008", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# M009 transform: with_items -> loop
# ---------------------------------------------------------------------------


class TestM009WithToLoop:
    """Tests for fix_with_to_loop M009 transform."""

    def test_with_items_to_loop(self) -> None:
        """Verifies with_items replaced with loop."""
        content = textwrap.dedent("""\
        - name: Install packages
          ansible.builtin.yum:
            name: "{{ item }}"
            state: present
          with_items:
            - httpd
            - nginx
        """)
        result = _apply(fix_with_to_loop, content, {"rule_id": "M009", "line": 1, "with_key": "with_items"})
        assert result.applied is True
        assert "loop:" in result.content
        assert "with_items" not in result.content

    def test_no_change_for_loop(self) -> None:
        """Verifies no change when already using loop."""
        content = textwrap.dedent("""\
        - name: Install packages
          ansible.builtin.yum:
            name: "{{ item }}"
            state: present
          loop:
            - httpd
            - nginx
        """)
        result = _apply(fix_with_to_loop, content, {"rule_id": "M009", "line": 1, "with_key": "with_items"})
        assert result.applied is False

    def test_with_dict_not_handled(self) -> None:
        """Verifies with_dict not converted (not supported)."""
        content = textwrap.dedent("""\
        - name: Create users
          ansible.builtin.user:
            name: "{{ item.key }}"
          with_dict: "{{ users }}"
        """)
        result = _apply(fix_with_to_loop, content, {"rule_id": "M009", "line": 1, "with_key": "with_dict"})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Install
          ansible.builtin.yum:
            name: "{{ item }}"
          with_items:
            - httpd
        """)
        r1 = _apply(fix_with_to_loop, content, {"rule_id": "M009", "line": 1, "with_key": "with_items"})
        r2 = _apply(fix_with_to_loop, r1.content, {"rule_id": "M009", "line": 1, "with_key": "with_items"})
        assert r2.applied is False
