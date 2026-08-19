"""Tests for the remediation engine: registry, partition, transforms."""

import textwrap
from collections.abc import Callable
from typing import NamedTuple, cast

from ruamel.yaml.comments import CommentedMap

from apme_engine.engine.models import RemediationClass, RemediationResolution, RuleScope, ViolationDict
from apme_engine.engine.yaml_utils import FormattedYAML
from apme_engine.remediation.partition import (
    add_classification_to_violations,
    classify_violation,
    count_by_remediation_class,
    count_by_resolution,
    is_finding_resolvable,
    normalize_rule_id,
    partition_violations,
)
from apme_engine.remediation.registry import TransformRegistry
from apme_engine.remediation.transforms import build_default_registry
from apme_engine.remediation.transforms._helpers import (
    find_task_at_line,
    find_task_by_index,
    violation_line_to_int,
    violation_task_index,
)
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
from apme_engine.remediation.transforms.L084_subtask_prefix import fix_subtask_prefix
from apme_engine.remediation.transforms.M001_fqcn import fix_fqcn
from apme_engine.remediation.transforms.M006_become_unreachable import fix_become_unreachable
from apme_engine.remediation.transforms.M008_bare_include import fix_bare_include
from apme_engine.remediation.transforms.M009_with_to_loop import fix_with_to_loop


class _ApplyResult(NamedTuple):
    """Result of applying a transform in tests.

    Attributes:
        content: File content (possibly modified).
        applied: True if the transform made a change.
    """

    content: str
    applied: bool


def _apply_node(
    fn: Callable[[CommentedMap, ViolationDict], bool],
    content: str,
    violation: ViolationDict,
) -> _ApplyResult:
    """Adapter: parse YAML, find task, call node transform, serialize.

    Args:
        fn: Node transform function.
        content: YAML file content string.
        violation: Violation dict.

    Returns:
        _ApplyResult with serialized content and applied flag.
    """
    yaml = FormattedYAML(typ="rt", pure=True, version=(1, 1))
    try:
        data = yaml.load(content)
    except Exception:  # noqa: BLE001
        return _ApplyResult(content, False)

    line = violation_line_to_int(violation)
    task = find_task_at_line(data, line) if line > 0 else None
    if task is None:
        idx = violation_task_index(violation)
        if idx is not None:
            task = find_task_by_index(data, idx)
    if task is None:
        return _ApplyResult(content, False)

    applied = fn(task, violation)
    return _ApplyResult(yaml.dumps(data) if applied else content, applied)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestTransformRegistry:
    """Tests for TransformRegistry register, apply_node, len, iter, rule_ids."""

    def test_register_and_contains(self) -> None:
        """Verifies register adds rule and contains checks membership."""
        reg = TransformRegistry()
        reg.register("L021", node=lambda t, v: False)
        assert "L021" in reg
        assert "L999" not in reg

    def test_apply_node_known_rule(self) -> None:
        """Verifies apply_node calls the transform and returns True."""
        reg = TransformRegistry()
        reg.register("TEST", node=lambda t, v: True)
        task = CommentedMap({"name": "test", "ansible.builtin.debug": {"msg": "hi"}})
        assert reg.apply_node("TEST", task, {}) is True

    def test_apply_node_unknown_rule(self) -> None:
        """Verifies apply_node returns False for unknown rule."""
        reg = TransformRegistry()
        task = CommentedMap({"name": "test"})
        assert reg.apply_node("UNKNOWN", task, {}) is False

    def test_len_and_iter(self) -> None:
        """Verifies len and iteration over registered rule IDs."""
        reg = TransformRegistry()
        reg.register("A", node=lambda t, v: False)
        reg.register("B", node=lambda t, v: False)
        assert len(reg) == 2
        assert set(reg) == {"A", "B"}

    def test_rule_ids_sorted(self) -> None:
        """Verifies rule_ids returns sorted list of registered IDs."""
        reg = TransformRegistry()
        reg.register("Z", node=lambda t, v: False)
        reg.register("A", node=lambda t, v: False)
        assert reg.rule_ids == ["A", "Z"]


# ---------------------------------------------------------------------------
# Partition
# ---------------------------------------------------------------------------


class TestPartition:
    """Tests for is_finding_resolvable and partition_violations tiers."""

    def test_is_finding_resolvable(self) -> None:
        """Verifies resolvable when rule_id in registry, not otherwise."""
        reg = TransformRegistry()
        reg.register("L021", node=lambda t, v: False)
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
        reg.register("L021", node=lambda t, v: False)
        assert is_finding_resolvable({"rule_id": "native:L021"}, reg) is True
        assert is_finding_resolvable({"rule_id": "native:L999"}, reg) is False

    def test_partition_native_prefix_to_tier1(self) -> None:
        """Verifies native:-prefixed violations partition into Tier 1."""
        reg = TransformRegistry()
        reg.register("L021", node=lambda t, v: False)

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
        reg.register("L021", node=lambda t, v: False)

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

    def test_classify_remaining_never_auto_fixable(self) -> None:
        """Remaining violations are never AUTO_FIXABLE — even if a transform exists."""
        assert classify_violation({"rule_id": "L021"}) == RemediationClass.AI_CANDIDATE
        assert classify_violation({"rule_id": "native:L021"}) == RemediationClass.AI_CANDIDATE

    def test_classify_violation_ai_candidate(self) -> None:
        """Verifies classify_violation returns ai-candidate for task-scoped rules."""
        assert classify_violation({"rule_id": "R118"}) == RemediationClass.AI_CANDIDATE
        assert classify_violation({"rule_id": "L999", "ai_proposable": True}) == RemediationClass.AI_CANDIDATE

    def test_classify_violation_manual_review(self) -> None:
        """Verifies classify_violation returns manual-review when ai_proposable is False."""
        assert classify_violation({"rule_id": "POLICY", "ai_proposable": False}) == RemediationClass.MANUAL_REVIEW

    def test_add_classification_to_violations(self) -> None:
        """Verifies add_classification_to_violations classifies remaining violations."""
        violations: list[ViolationDict] = [
            {"rule_id": "L021"},
            {"rule_id": "R118"},
            {"rule_id": "POLICY", "ai_proposable": False},
        ]
        add_classification_to_violations(violations)
        assert violations[0]["remediation_class"] == RemediationClass.AI_CANDIDATE
        assert violations[1]["remediation_class"] == RemediationClass.AI_CANDIDATE
        assert violations[2]["remediation_class"] == RemediationClass.MANUAL_REVIEW
        for v in violations:
            assert "remediation_resolution" not in v

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
        assert RemediationResolution.AI_ABSTAINED.value == "ai-abstained"
        assert len(list(RemediationResolution)) == 11

    def test_all_registered_rules_classify_as_remaining(self) -> None:
        """Remaining violations for registered rules classify as AI_CANDIDATE, not AUTO_FIXABLE.

        AUTO_FIXABLE is only for violations proven fixed during convergence.
        """
        reg = build_default_registry()
        for rule_id in reg:
            v: ViolationDict = {"rule_id": rule_id}
            assert classify_violation(v) == RemediationClass.AI_CANDIDATE, (
                f"Remaining {rule_id} should classify as AI_CANDIDATE"
            )

    def test_partition_play_scope_to_tier3(self) -> None:
        """Verifies play-scoped violations route to tier3 via scope metadata."""
        reg = TransformRegistry()
        violations: list[ViolationDict] = [
            {"rule_id": "L042", "scope": RuleScope.PLAY},
            {"rule_id": "M010", "scope": "play"},
            {"rule_id": "R108", "scope": RuleScope.PLAY},
        ]
        t1, t2, t3 = partition_violations(violations, reg)
        assert len(t1) == 0
        assert len(t2) == 0
        assert len(t3) == 3
        for v in t3:
            assert v["remediation_resolution"] == RemediationResolution.MANUAL

    def test_partition_role_scope_to_tier3(self) -> None:
        """Verifies role-scoped violations route to tier3."""
        reg = TransformRegistry()
        violations: list[ViolationDict] = [
            {"rule_id": "L027", "scope": RuleScope.ROLE},
            {"rule_id": "L052", "scope": "role"},
        ]
        t1, t2, t3 = partition_violations(violations, reg)
        assert len(t3) == 2

    def test_partition_task_scope_to_tier2(self) -> None:
        """Verifies task-scoped violations route to tier2 (AI proposable)."""
        reg = TransformRegistry()
        violations: list[ViolationDict] = [
            {"rule_id": "L026", "scope": RuleScope.TASK},
            {"rule_id": "L026", "scope": "task"},
        ]
        t1, t2, t3 = partition_violations(violations, reg)
        assert len(t2) == 2

    def test_partition_block_scope_to_tier2(self) -> None:
        """Verifies block-scoped violations are AI-proposable."""
        reg = TransformRegistry()
        violations: list[ViolationDict] = [
            {"rule_id": "BLOCK001", "scope": RuleScope.BLOCK},
        ]
        t1, t2, t3 = partition_violations(violations, reg)
        assert len(t2) == 1

    def test_partition_cross_file_rules_still_tier3(self) -> None:
        """Verifies cross-file and data-flow rules route to tier3."""
        reg = TransformRegistry()
        violations: list[ViolationDict] = [
            {"rule_id": "R111", "scope": RuleScope.TASK},
            {"rule_id": "R112", "scope": "task"},
            {"rule_id": "L006", "scope": RuleScope.TASK},
            {"rule_id": "L080", "scope": "task"},
        ]
        t1, t2, t3 = partition_violations(violations, reg)
        assert len(t1) == 0
        assert len(t2) == 0
        assert len(t3) == 4
        for v in t3:
            assert v["remediation_resolution"] == RemediationResolution.NEEDS_CROSS_FILE

    def test_cross_file_rules_beat_registry(self) -> None:
        """Verifies L006/L080 stay Tier 3 even if a transform is registered."""
        reg = TransformRegistry()
        reg.register("L006", node=lambda t, v: False)
        reg.register("L080", node=lambda t, v: False)
        violations: list[ViolationDict] = [
            {"rule_id": "L006", "scope": RuleScope.TASK},
            {"rule_id": "L080", "scope": "task"},
        ]
        assert is_finding_resolvable(violations[0], reg) is False
        assert is_finding_resolvable(violations[1], reg) is False
        t1, t2, t3 = partition_violations(violations, reg)
        assert t1 == []
        assert t2 == []
        assert len(t3) == 2
        for v in t3:
            assert v["remediation_resolution"] == RemediationResolution.NEEDS_CROSS_FILE

    def test_classify_data_flow_rules_manual_review(self) -> None:
        """Verifies L006/L080 classify as manual-review despite task scope."""
        assert classify_violation({"rule_id": "L006", "scope": RuleScope.TASK}) == RemediationClass.MANUAL_REVIEW
        assert classify_violation({"rule_id": "L080", "scope": "task"}) == RemediationClass.MANUAL_REVIEW

    def test_partition_missing_scope_defaults_to_task(self) -> None:
        """Verifies violations without scope default to task (AI proposable)."""
        reg = TransformRegistry()
        violations: list[ViolationDict] = [
            {"rule_id": "L999"},
        ]
        t1, t2, t3 = partition_violations(violations, reg)
        assert len(t2) == 1

    def test_classify_play_scope_manual_review(self) -> None:
        """Verifies play-scoped violations classify as manual-review."""
        assert classify_violation({"rule_id": "L042", "scope": RuleScope.PLAY}) == RemediationClass.MANUAL_REVIEW

    def test_classify_collection_scope_manual_review(self) -> None:
        """Verifies collection-scoped violations classify as manual-review."""
        assert classify_violation({"rule_id": "L037", "scope": RuleScope.COLLECTION}) == RemediationClass.MANUAL_REVIEW

    def test_classify_task_scope_ai_candidate(self) -> None:
        """Verifies task-scoped violations classify as AI candidate."""
        assert classify_violation({"rule_id": "L026", "scope": RuleScope.TASK}) == RemediationClass.AI_CANDIDATE


# ---------------------------------------------------------------------------
# Default registry
# ---------------------------------------------------------------------------


class TestDefaultRegistry:
    """Tests for build_default_registry rule coverage."""

    def test_build_default_registry(self) -> None:
        """Verifies default registry contains expected rule IDs and count."""
        reg = build_default_registry()
        for rule_id in (
            "L002",
            "L005",
            "L007",
            "L008",
            "L009",
            "L010",
            "L011",
            "L012",
            "L013",
            "L015",
            "L018",
            "L020",
            "L021",
            "L022",
            "L025",
            "L026",
            "L043",
            "L046",
            "L084",
            "M001",
            "M003",
            "M006",
            "M008",
            "M009",
        ):
            assert rule_id in reg, f"{rule_id} missing from default registry"
        assert len(reg) == 26


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
        result = _apply_node(fix_missing_mode, content, {"rule_id": "L021", "line": 1})
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
        result = _apply_node(fix_missing_mode, content, {"rule_id": "L021", "line": 1})
        assert result.applied is False

    def test_no_change_for_non_file_module(self) -> None:
        """Verifies no change for command module."""
        content = textwrap.dedent("""\
        - name: Run command
          ansible.builtin.command: echo hello
        """)
        result = _apply_node(fix_missing_mode, content, {"rule_id": "L021", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Template it
          ansible.builtin.template:
            src: foo.j2
            dest: /etc/foo.conf
        """)
        r1 = _apply_node(fix_missing_mode, content, {"rule_id": "L021", "line": 1})
        assert r1.applied is True
        r2 = _apply_node(fix_missing_mode, r1.content, {"rule_id": "L021", "line": 1})
        assert r2.applied is False

    def test_handles_invalid_yaml(self) -> None:
        """Verifies invalid YAML returns applied False without raising."""
        result = _apply_node(fix_missing_mode, "{{{{invalid", {"rule_id": "L021", "line": 1})
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
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is True
        assert "ansible.builtin.command" in result.content
        assert "ansible.builtin.shell" not in result.content

    def test_no_change_when_pipe_present(self) -> None:
        """Verifies no change when pipe in command."""
        content = textwrap.dedent("""\
        - name: Grep output
          ansible.builtin.shell: cat /tmp/log | grep error
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_when_and_present(self) -> None:
        """Verifies no change when && in command."""
        content = textwrap.dedent("""\
        - name: Chain commands
          ansible.builtin.shell:
            cmd: mkdir /tmp/foo && touch /tmp/foo/bar
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_when_redirect_present(self) -> None:
        """Verifies no change when redirect in command."""
        content = textwrap.dedent("""\
        - name: Write output
          ansible.builtin.shell: echo hello > /tmp/out
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_for_command_module(self) -> None:
        """Verifies no change when already using command module."""
        content = textwrap.dedent("""\
        - name: Already command
          ansible.builtin.command: echo hello
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Simple command
          ansible.builtin.shell: whoami
        """)
        r1 = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert r1.applied is True
        r2 = _apply_node(fix_shell_to_command, r1.content, {"rule_id": "L007", "line": 1})
        assert r2.applied is False

    def test_dict_form_cmd(self) -> None:
        """Verifies shell with cmd dict form converted to command."""
        content = textwrap.dedent("""\
        - name: Simple command
          ansible.builtin.shell:
            cmd: whoami
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is True
        assert "ansible.builtin.command" in result.content

    def test_converts_argv_without_shell_features(self) -> None:
        """Verifies argv-only shell without pipes is converted to command."""
        content = textwrap.dedent("""\
        - name: Simple argv
          ansible.builtin.shell:
            argv:
              - whoami
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is True
        assert "ansible.builtin.command" in result.content

    def test_no_change_when_argv_has_pipe(self) -> None:
        """Verifies argv containing a pipe is not converted to command."""
        content = textwrap.dedent("""\
        - name: Piped argv
          ansible.builtin.shell:
            argv:
              - cat
              - /proc/meminfo
              - "|"
              - grep
              - MemTotal
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_when_command_uninspectable(self) -> None:
        """Verifies shell without cmd or argv is left unchanged."""
        content = textwrap.dedent("""\
        - name: Only chdir
          ansible.builtin.shell:
            chdir: /tmp
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_converts_when_pipe_is_jinja_filter(self) -> None:
        """Verifies Jinja |quote is not treated as a shell pipe."""
        content = textwrap.dedent("""\
        - name: Cat quoted path
          ansible.builtin.shell: "cat {{ myfile | quote }}"
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is True
        assert "ansible.builtin.command" in result.content

    def test_no_change_when_background_ampersand(self) -> None:
        """Verifies standalone & is treated as a shell feature."""
        content = textwrap.dedent("""\
        - name: Background sleep
          ansible.builtin.shell:
            cmd: sleep 10 &
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_when_argv_has_ampersand(self) -> None:
        """Verifies argv containing & is not converted to command."""
        content = textwrap.dedent("""\
        - name: Background argv
          ansible.builtin.shell:
            argv:
              - sleep
              - "10"
              - "&"
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_when_shell_variable(self) -> None:
        """Verifies $VAR expansion is treated as a shell feature."""
        content = textwrap.dedent("""\
        - name: Echo home
          ansible.builtin.shell: "echo $HOME"
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_when_command_is_only_jinja(self) -> None:
        """Verifies a whole-command Jinja expression is not converted."""
        content = textwrap.dedent("""\
        - name: Dynamic command
          ansible.builtin.shell: "{{ command }}"
        """)
        result = _apply_node(fix_shell_to_command, content, {"rule_id": "L007", "line": 1})
        assert result.applied is False


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
        result = _apply_node(fix_fqcn, content, violation)
        assert result.applied is True
        assert "ansible.builtin.debug" in result.content
        assert "\n  debug:" not in result.content

    def test_escalates_without_resolved_fqcn(self) -> None:
        """Verifies no fix when violation lacks resolved_fqcn (escalates to AI)."""
        content = textwrap.dedent("""\
        - name: Copy file
          copy:
            src: /a
            dest: /b
        """)
        violation = cast(ViolationDict, {"rule_id": "M001", "line": 1})
        result = _apply_node(fix_fqcn, content, violation)
        assert result.applied is False

    def test_no_change_when_already_fqcn(self) -> None:
        """Verifies no change when module already FQCN."""
        content = textwrap.dedent("""\
        - name: Debug message
          ansible.builtin.debug:
            msg: hello
        """)
        violation = cast(ViolationDict, {"rule_id": "M001", "line": 1})
        result = _apply_node(fix_fqcn, content, violation)
        assert result.applied is False

    def test_no_change_for_unknown_short_name(self) -> None:
        """Verifies no change for unknown short module name."""
        content = textwrap.dedent("""\
        - name: Custom module
          my_custom_module:
            param: value
        """)
        violation = cast(ViolationDict, {"rule_id": "M001", "line": 1})
        result = _apply_node(fix_fqcn, content, violation)
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Install package
          yum:
            name: httpd
            state: present
        """)
        violation = cast(
            ViolationDict,
            {"rule_id": "M001", "line": 1, "resolved_fqcn": "ansible.builtin.yum"},
        )
        r1 = _apply_node(fix_fqcn, content, violation)
        assert r1.applied is True
        r2 = _apply_node(fix_fqcn, r1.content, violation)
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
        result = _apply_node(fix_fqcn, content, violation)
        assert result.applied is True
        assert "ansible.builtin.dnf" in result.content


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
        result = _apply_node(fix_local_action, content, {"rule_id": "L008", "line": 1})
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
        result = _apply_node(fix_local_action, content, {"rule_id": "L008", "line": 1})
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
        result = _apply_node(fix_local_action, content, {"rule_id": "L008", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Run locally
          local_action: command whoami
        """)
        r1 = _apply_node(fix_local_action, content, {"rule_id": "L008", "line": 1})
        assert r1.applied is True
        r2 = _apply_node(fix_local_action, r1.content, {"rule_id": "L008", "line": 1})
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
        result = _apply_node(fix_empty_string, content, {"rule_id": "L009", "line": 1})
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
        result = _apply_node(fix_empty_string, content, {"rule_id": "L009", "line": 1})
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
        result = _apply_node(fix_empty_string, content, {"rule_id": "L009", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: empty
          when: myvar == ""
        """)
        r1 = _apply_node(fix_empty_string, content, {"rule_id": "L009", "line": 1})
        r2 = _apply_node(fix_empty_string, r1.content, {"rule_id": "L009", "line": 1})
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
        result = _apply_node(fix_literal_bool, content, {"rule_id": "L011", "line": 1})
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
        result = _apply_node(fix_literal_bool, content, {"rule_id": "L011", "line": 1})
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
        result = _apply_node(fix_literal_bool, content, {"rule_id": "L011", "line": 1})
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
        result = _apply_node(fix_literal_bool, content, {"rule_id": "L011", "line": 1})
        assert result.applied is True

    def test_no_change_without_pattern(self) -> None:
        """Verifies no change when when clause has no literal bool comparison."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: enabled
        """)
        result = _apply_node(fix_literal_bool, content, {"rule_id": "L011", "line": 1})
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
        result = _apply_node(fix_jinja_when, content, {"rule_id": "L015", "line": 1})
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
        result = _apply_node(fix_jinja_when, content, {"rule_id": "L015", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: "{{ some_flag }}"
        """)
        r1 = _apply_node(fix_jinja_when, content, {"rule_id": "L015", "line": 1})
        r2 = _apply_node(fix_jinja_when, r1.content, {"rule_id": "L015", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# L020 transform: octal mode
# ---------------------------------------------------------------------------


class TestL020OctalMode:
    """Tests for fix_octal_mode L020 node transform."""

    @staticmethod
    def _parse_task(yaml_str: str) -> CommentedMap:
        """Parse YAML and return the first task CommentedMap.

        Args:
            yaml_str: Indented YAML task list string.

        Returns:
            First task CommentedMap from the parsed list.
        """
        yaml = FormattedYAML(typ="rt", pure=True, version=(1, 1))
        data = yaml.load(textwrap.dedent(yaml_str))
        return cast(CommentedMap, data[0])  # type: ignore[index]

    def test_octal_literal_to_string(self) -> None:
        """YAML 1.1 parses 0644 as octal int 420; should become '0644'."""
        task = self._parse_task("""\
        - name: Set perms
          ansible.builtin.file:
            path: /tmp/foo
            mode: 0644
        """)
        assert fix_octal_mode(task, {"rule_id": "L020", "line": 1}) is True
        assert task["ansible.builtin.file"]["mode"] == "0644"

    def test_decimal_int_to_octal_string(self) -> None:
        """Bare 644 is decimal in YAML; digits are all valid octal, treated as intended octal."""
        task = self._parse_task("""\
        - name: Set perms
          ansible.builtin.file:
            path: /tmp/foo
            mode: 644
        """)
        assert fix_octal_mode(task, {"rule_id": "L020", "line": 1}) is True
        assert task["ansible.builtin.file"]["mode"] == "0644"

    def test_no_change_when_already_string(self) -> None:
        """Verifies no change when mode already quoted string."""
        task = self._parse_task("""\
        - name: Set perms
          ansible.builtin.file:
            path: /tmp/foo
            mode: "0644"
        """)
        assert fix_octal_mode(task, {"rule_id": "L020", "line": 1}) is False

    def test_string_without_leading_zero(self) -> None:
        """Verifies string 644 converted to 0644."""
        task = self._parse_task("""\
        - name: Set perms
          ansible.builtin.file:
            path: /tmp/foo
            mode: "644"
        """)
        assert fix_octal_mode(task, {"rule_id": "L020", "line": 1}) is True
        assert task["ansible.builtin.file"]["mode"] == "0644"

    def test_octal_0755(self) -> None:
        """0755 → int 493 → should become '0755'."""
        task = self._parse_task("""\
        - name: Set perms
          ansible.builtin.file:
            path: /tmp/foo
            mode: 0755
        """)
        assert fix_octal_mode(task, {"rule_id": "L020", "line": 1}) is True
        assert task["ansible.builtin.file"]["mode"] == "0755"

    def test_bare_755(self) -> None:
        """Bare 755, all digits valid octal → '0755'."""
        task = self._parse_task("""\
        - name: Set perms
          ansible.builtin.file:
            path: /tmp/foo
            mode: 755
        """)
        assert fix_octal_mode(task, {"rule_id": "L020", "line": 1}) is True
        assert task["ansible.builtin.file"]["mode"] == "0755"


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
        result = _apply_node(fix_name_casing, content, {"rule_id": "L025", "line": 1})
        assert result.applied is True
        assert "Install packages" in result.content

    def test_no_change_when_already_uppercase(self) -> None:
        """Verifies no change when name already capitalized."""
        content = textwrap.dedent("""\
        - name: Install packages
          ansible.builtin.debug:
            msg: hi
        """)
        result = _apply_node(fix_name_casing, content, {"rule_id": "L025", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: setup network
          ansible.builtin.debug:
            msg: hi
        """)
        r1 = _apply_node(fix_name_casing, content, {"rule_id": "L025", "line": 1})
        r2 = _apply_node(fix_name_casing, r1.content, {"rule_id": "L025", "line": 1})
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
        result = _apply_node(fix_free_form, content, {"rule_id": "L046", "line": 1})
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
        result = _apply_node(fix_free_form, content, {"rule_id": "L046", "line": 1})
        assert result.applied is False

    def test_no_change_for_non_command_module(self) -> None:
        """Verifies no change for non-command/shell modules."""
        content = textwrap.dedent("""\
        - name: Debug
          ansible.builtin.debug:
            msg: hi
        """)
        result = _apply_node(fix_free_form, content, {"rule_id": "L046", "line": 1})
        assert result.applied is False

    def test_shell_module(self) -> None:
        """Verifies shell module free-form converted to cmd dict."""
        content = textwrap.dedent("""\
        - name: Run shell
          ansible.builtin.shell: cat /etc/hosts | grep localhost
        """)
        result = _apply_node(fix_free_form, content, {"rule_id": "L046", "line": 1})
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
        result = _apply_node(fix_bare_vars, content, {"rule_id": "L043", "line": 1})
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
        result = _apply_node(fix_bare_vars, content, {"rule_id": "L043", "line": 1})
        assert result.applied is False

    def test_no_change_without_loop(self) -> None:
        """Verifies no change when task has no loop."""
        content = textwrap.dedent("""\
        - name: Simple
          ansible.builtin.debug:
            msg: hi
        """)
        result = _apply_node(fix_bare_vars, content, {"rule_id": "L043", "line": 1})
        assert result.applied is False


# ---------------------------------------------------------------------------
# L084 transform: subtask prefix
# ---------------------------------------------------------------------------


class TestL084SubtaskPrefix:
    """Tests for fix_subtask_prefix L084 transform."""

    def test_adds_filename_prefix(self) -> None:
        """Verifies filename stem is prepended as prefix."""
        content = textwrap.dedent("""\
        - name: Validate SSL certificate credentials
          ansible.builtin.assert:
            that:
              - ssl_cert_content is defined
        """)
        violation: ViolationDict = {
            "rule_id": "L084",
            "line": 1,
            "file": "ansible/roles/nginx_multisite/tasks/validate_credentials.yml",
        }
        result = _apply_node(fix_subtask_prefix, content, violation)
        assert result.applied is True
        assert "validate_credentials | Validate SSL certificate credentials" in result.content

    def test_no_change_when_already_prefixed(self) -> None:
        """Verifies no change when name already has a pipe prefix."""
        content = textwrap.dedent("""\
        - name: validate_credentials | Validate SSL certificate credentials
          ansible.builtin.assert:
            that:
              - ssl_cert_content is defined
        """)
        violation: ViolationDict = {
            "rule_id": "L084",
            "line": 1,
            "file": "ansible/roles/nginx_multisite/tasks/validate_credentials.yml",
        }
        result = _apply_node(fix_subtask_prefix, content, violation)
        assert result.applied is False

    def test_no_change_without_file(self) -> None:
        """Verifies no change when violation has no file path."""
        content = textwrap.dedent("""\
        - name: Install nginx
          ansible.builtin.apt:
            name: nginx
        """)
        violation: ViolationDict = {"rule_id": "L084", "line": 1, "file": ""}
        result = _apply_node(fix_subtask_prefix, content, violation)
        assert result.applied is False

    def test_no_change_for_main_yml(self) -> None:
        """Verifies no change when file is main.yml."""
        content = textwrap.dedent("""\
        - name: Install nginx
          ansible.builtin.apt:
            name: nginx
        """)
        violation: ViolationDict = {
            "rule_id": "L084",
            "line": 1,
            "file": "ansible/roles/web/tasks/main.yml",
        }
        result = _apply_node(fix_subtask_prefix, content, violation)
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Install nginx
          ansible.builtin.apt:
            name: nginx
        """)
        violation: ViolationDict = {
            "rule_id": "L084",
            "line": 1,
            "file": "project/roles/web/tasks/install.yml",
        }
        r1 = _apply_node(fix_subtask_prefix, content, violation)
        assert r1.applied is True
        assert "install | Install nginx" in r1.content
        r2 = _apply_node(fix_subtask_prefix, r1.content, violation)
        assert r2.applied is False

    def test_replaces_wrong_prefix(self) -> None:
        """Legacy 'sub | ...' name gets the prefix replaced, not stacked."""
        content = textwrap.dedent("""\
        - name: sub | Install nginx
          ansible.builtin.apt:
            name: nginx
        """)
        violation: ViolationDict = {
            "rule_id": "L084",
            "line": 1,
            "file": "project/roles/web/tasks/install.yml",
        }
        result = _apply_node(fix_subtask_prefix, content, violation)
        assert result.applied is True
        assert "install | Install nginx" in result.content
        assert "sub" not in result.content

    def test_replaces_wrong_prefix_preserves_description(self) -> None:
        """Wrong prefix is replaced while full description is preserved."""
        content = textwrap.dedent("""\
        - name: setup | Configure app for production
          ansible.builtin.template:
            src: app.conf.j2
            dest: /etc/app.conf
        """)
        violation: ViolationDict = {
            "rule_id": "L084",
            "line": 1,
            "file": "project/roles/myapp/tasks/configure.yml",
        }
        result = _apply_node(fix_subtask_prefix, content, violation)
        assert result.applied is True
        assert "configure | Configure app for production" in result.content

    def test_no_change_for_main_yaml(self) -> None:
        """Verifies no change when file is main.yaml (not just main.yml)."""
        content = textwrap.dedent("""\
        - name: Install nginx
          ansible.builtin.apt:
            name: nginx
        """)
        violation: ViolationDict = {
            "rule_id": "L084",
            "line": 1,
            "file": "ansible/roles/web/tasks/main.yaml",
        }
        result = _apply_node(fix_subtask_prefix, content, violation)
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
        result = _apply_node(fix_changed_when, content, {"rule_id": "L013", "line": 1})
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
        result = _apply_node(fix_changed_when, content, {"rule_id": "L013", "line": 1})
        assert result.applied is False

    def test_no_change_when_already_set(self) -> None:
        """Verifies no change when changed_when already present."""
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.command: echo test
          changed_when: false
        """)
        result = _apply_node(fix_changed_when, content, {"rule_id": "L013", "line": 1})
        assert result.applied is False

    def test_no_change_for_non_command_module(self) -> None:
        """Verifies no change for non-command modules."""
        content = textwrap.dedent("""\
        - name: Debug
          ansible.builtin.debug:
            msg: hi
        """)
        result = _apply_node(fix_changed_when, content, {"rule_id": "L013", "line": 1})
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
        result = _apply_node(fix_become, content, {"rule_id": "L018", "line": 1})
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
        result = _apply_node(fix_become, content, {"rule_id": "L018", "line": 1})
        assert result.applied is False

    def test_no_change_without_become_user(self) -> None:
        """Verifies no change when become_user not present."""
        content = textwrap.dedent("""\
        - name: Normal
          ansible.builtin.debug:
            msg: hi
        """)
        result = _apply_node(fix_become, content, {"rule_id": "L018", "line": 1})
        assert result.applied is False

    def test_become_inserted_after_become_user(self) -> None:
        """Verifies become inserted immediately after become_user line."""
        content = textwrap.dedent("""\
        - name: Switch user
          ansible.builtin.command: whoami
          become_user: postgres
        """)
        result = _apply_node(fix_become, content, {"rule_id": "L018", "line": 1})
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
        result = _apply_node(fix_pipefail, content, {"rule_id": "L022", "line": 1})
        assert result.applied is True
        assert "set -o pipefail &&" in result.content

    def test_prepends_pipefail_dict_form(self) -> None:
        """Verifies set -o pipefail && prepended to dict form shell cmd."""
        content = textwrap.dedent("""\
        - name: Grep logs
          ansible.builtin.shell:
            cmd: cat /var/log/syslog | grep error
        """)
        result = _apply_node(fix_pipefail, content, {"rule_id": "L022", "line": 1})
        assert result.applied is True
        assert "set -o pipefail &&" in result.content

    def test_no_change_without_pipe(self) -> None:
        """Verifies no change when shell command has no pipe."""
        content = textwrap.dedent("""\
        - name: Simple
          ansible.builtin.shell: echo hello
        """)
        result = _apply_node(fix_pipefail, content, {"rule_id": "L022", "line": 1})
        assert result.applied is False

    def test_no_change_when_already_set(self) -> None:
        """Verifies no change when pipefail already in cmd."""
        content = textwrap.dedent("""\
        - name: Grep logs
          ansible.builtin.shell: set -o pipefail && cat /var/log/syslog | grep error
        """)
        result = _apply_node(fix_pipefail, content, {"rule_id": "L022", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Grep
          ansible.builtin.shell: cat log | grep err
        """)
        r1 = _apply_node(fix_pipefail, content, {"rule_id": "L022", "line": 1})
        r2 = _apply_node(fix_pipefail, r1.content, {"rule_id": "L022", "line": 1})
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
        result = _apply_node(fix_latest, content, {"rule_id": "L012", "line": 1})
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
        result = _apply_node(fix_latest, content, {"rule_id": "L012", "line": 1})
        assert result.applied is False

    def test_no_change_for_absent(self) -> None:
        """Verifies no change when state is absent."""
        content = textwrap.dedent("""\
        - name: Remove httpd
          ansible.builtin.yum:
            name: httpd
            state: absent
        """)
        result = _apply_node(fix_latest, content, {"rule_id": "L012", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - name: Install
          ansible.builtin.apt:
            name: nginx
            state: latest
        """)
        r1 = _apply_node(fix_latest, content, {"rule_id": "L012", "line": 1})
        r2 = _apply_node(fix_latest, r1.content, {"rule_id": "L012", "line": 1})
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
        result = _apply_node(fix_become_unreachable, content, {"rule_id": "M006", "line": 1})
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
        result = _apply_node(fix_become_unreachable, content, {"rule_id": "M006", "line": 1})
        assert result.applied is False

    def test_no_change_without_become(self) -> None:
        """Verifies no change when become not present."""
        content = textwrap.dedent("""\
        - name: Normal task
          ansible.builtin.command: whoami
          ignore_errors: true
        """)
        result = _apply_node(fix_become_unreachable, content, {"rule_id": "M006", "line": 1})
        assert result.applied is False

    def test_inserted_after_ignore_errors(self) -> None:
        """Verifies ignore_unreachable inserted after ignore_errors line."""
        content = textwrap.dedent("""\
        - name: Risky task
          ansible.builtin.command: whoami
          become: true
          ignore_errors: true
        """)
        result = _apply_node(fix_become_unreachable, content, {"rule_id": "M006", "line": 1})
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
        result = _apply_node(fix_bare_include, content, {"rule_id": "M008", "line": 1})
        assert result.applied is True
        assert "ansible.builtin.include_tasks" in result.content
        assert "\n- include:" not in result.content

    def test_no_change_for_include_tasks(self) -> None:
        """Verifies no change when already using include_tasks."""
        content = textwrap.dedent("""\
        - ansible.builtin.include_tasks: tasks/setup.yml
        """)
        result = _apply_node(fix_bare_include, content, {"rule_id": "M008", "line": 1})
        assert result.applied is False

    def test_idempotent(self) -> None:
        """Verifies second pass produces no change after first fix."""
        content = textwrap.dedent("""\
        - include: tasks/setup.yml
        """)
        r1 = _apply_node(fix_bare_include, content, {"rule_id": "M008", "line": 1})
        r2 = _apply_node(fix_bare_include, r1.content, {"rule_id": "M008", "line": 1})
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
        result = _apply_node(fix_with_to_loop, content, {"rule_id": "M009", "line": 1, "with_key": "with_items"})
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
        result = _apply_node(fix_with_to_loop, content, {"rule_id": "M009", "line": 1, "with_key": "with_items"})
        assert result.applied is False

    def test_with_dict_not_handled(self) -> None:
        """Verifies with_dict not converted (not supported)."""
        content = textwrap.dedent("""\
        - name: Create users
          ansible.builtin.user:
            name: "{{ item.key }}"
          with_dict: "{{ users }}"
        """)
        result = _apply_node(fix_with_to_loop, content, {"rule_id": "M009", "line": 1, "with_key": "with_dict"})
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
        r1 = _apply_node(fix_with_to_loop, content, {"rule_id": "M009", "line": 1, "with_key": "with_items"})
        r2 = _apply_node(fix_with_to_loop, r1.content, {"rule_id": "M009", "line": 1, "with_key": "with_items"})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# violation_task_index / find_task_by_index
# ---------------------------------------------------------------------------


class TestViolationTaskIndex:
    """Tests for violation_task_index()."""

    def test_extracts_index_from_path(self) -> None:
        """Extracts task index from a native validator path field."""
        v: ViolationDict = {"rule_id": "L007", "path": "task:playbook.yml#task:[2]"}
        assert violation_task_index(v) == 2

    def test_returns_none_without_path(self) -> None:
        """Returns None when violation has no path field."""
        v: ViolationDict = {"rule_id": "L007"}
        assert violation_task_index(v) is None

    def test_returns_none_for_non_task_path(self) -> None:
        """Returns None when path does not contain task:[N]."""
        v: ViolationDict = {"rule_id": "L007", "path": "playbook.yml"}
        assert violation_task_index(v) is None

    def test_returns_none_for_non_string_path(self) -> None:
        """Returns None when path is not a string."""
        v: ViolationDict = {"rule_id": "L007", "path": 42}
        assert violation_task_index(v) is None


class TestFindTaskByIndex:
    """Tests for find_task_by_index()."""

    @staticmethod
    def _load(content: str) -> CommentedMap:
        yaml = FormattedYAML(typ="rt", pure=True, version=(1, 1))
        return yaml.load(content)

    def test_finds_task_in_seq(self) -> None:
        """Finds a task by index in a bare CommentedSeq."""
        data = self._load(
            textwrap.dedent("""\
        - name: First
          ansible.builtin.debug:
            msg: one
        - name: Second
          ansible.builtin.debug:
            msg: two
        """)
        )
        task = find_task_by_index(data, 1)
        assert task is not None
        assert task["name"] == "Second"

    def test_finds_task_in_play_tasks(self) -> None:
        """Finds a task by index in a play's tasks list."""
        data = self._load(
            textwrap.dedent("""\
        tasks:
          - name: First
            ansible.builtin.debug:
              msg: one
          - name: Second
            ansible.builtin.debug:
              msg: two
        """)
        )
        task = find_task_by_index(data, 0)
        assert task is not None
        assert task["name"] == "First"

    def test_returns_none_for_out_of_range(self) -> None:
        """Returns None when index is out of range."""
        data = self._load(
            textwrap.dedent("""\
        - name: Only task
          ansible.builtin.debug:
            msg: hi
        """)
        )
        assert find_task_by_index(data, 5) is None

    def test_finds_task_in_handlers(self) -> None:
        """Finds a task by index in a play's handlers list."""
        data = self._load(
            textwrap.dedent("""\
        handlers:
          - name: Restart service
            ansible.builtin.service:
              name: httpd
              state: restarted
        """)
        )
        task = find_task_by_index(data, 0)
        assert task is not None
        assert task["name"] == "Restart service"
