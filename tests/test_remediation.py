"""Tests for the remediation engine: registry, partition, transforms, convergence."""

import textwrap

import pytest

from apme_engine.remediation.registry import TransformRegistry, TransformResult
from apme_engine.remediation.partition import is_finding_resolvable, partition_violations
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
from apme_engine.remediation.engine import RemediationEngine, FixReport


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestTransformRegistry:
    def test_register_and_contains(self):
        reg = TransformRegistry()
        reg.register("L021", lambda c, v: TransformResult(c, False))
        assert "L021" in reg
        assert "L999" not in reg

    def test_apply_known_rule(self):
        reg = TransformRegistry()
        reg.register("TEST", lambda c, v: TransformResult("fixed", True))
        result = reg.apply("TEST", "original", {})
        assert result.content == "fixed"
        assert result.applied is True

    def test_apply_unknown_rule(self):
        reg = TransformRegistry()
        result = reg.apply("UNKNOWN", "original", {})
        assert result.content == "original"
        assert result.applied is False

    def test_len_and_iter(self):
        reg = TransformRegistry()
        reg.register("A", lambda c, v: TransformResult(c, False))
        reg.register("B", lambda c, v: TransformResult(c, False))
        assert len(reg) == 2
        assert set(reg) == {"A", "B"}

    def test_rule_ids_sorted(self):
        reg = TransformRegistry()
        reg.register("Z", lambda c, v: TransformResult(c, False))
        reg.register("A", lambda c, v: TransformResult(c, False))
        assert reg.rule_ids == ["A", "Z"]


# ---------------------------------------------------------------------------
# Partition
# ---------------------------------------------------------------------------

class TestPartition:
    def test_is_finding_resolvable(self):
        reg = TransformRegistry()
        reg.register("L021", lambda c, v: TransformResult(c, False))
        assert is_finding_resolvable({"rule_id": "L021"}, reg) is True
        assert is_finding_resolvable({"rule_id": "L999"}, reg) is False

    def test_partition_three_tiers(self):
        reg = TransformRegistry()
        reg.register("L021", lambda c, v: TransformResult(c, False))

        violations = [
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


# ---------------------------------------------------------------------------
# Default registry
# ---------------------------------------------------------------------------

class TestDefaultRegistry:
    def test_build_default_registry(self):
        reg = build_default_registry()
        for rule_id in ("L002", "L007", "L008", "L009", "L011", "L012", "L013",
                        "L015", "L018", "L020", "L021", "L022", "L025",
                        "L043", "L046", "M001", "M003",
                        "M006", "M008", "M009"):
            assert rule_id in reg, f"{rule_id} missing from default registry"
        assert len(reg) == 20


# ---------------------------------------------------------------------------
# L021 transform: missing mode
# ---------------------------------------------------------------------------

class TestL021MissingMode:
    def test_adds_mode_to_file_module(self):
        content = textwrap.dedent("""\
        - name: Copy a file
          ansible.builtin.copy:
            src: /tmp/foo
            dest: /tmp/bar
        """)
        result = fix_missing_mode(content, {"rule_id": "L021", "line": 1})
        assert result.applied is True
        assert "mode:" in result.content
        assert "0644" in result.content

    def test_no_change_when_mode_present(self):
        content = textwrap.dedent("""\
        - name: Copy a file
          ansible.builtin.copy:
            src: /tmp/foo
            dest: /tmp/bar
            mode: "0755"
        """)
        result = fix_missing_mode(content, {"rule_id": "L021", "line": 1})
        assert result.applied is False

    def test_no_change_for_non_file_module(self):
        content = textwrap.dedent("""\
        - name: Run command
          ansible.builtin.command: echo hello
        """)
        result = fix_missing_mode(content, {"rule_id": "L021", "line": 1})
        assert result.applied is False

    def test_idempotent(self):
        content = textwrap.dedent("""\
        - name: Template it
          ansible.builtin.template:
            src: foo.j2
            dest: /etc/foo.conf
        """)
        r1 = fix_missing_mode(content, {"rule_id": "L021", "line": 1})
        assert r1.applied is True
        r2 = fix_missing_mode(r1.content, {"rule_id": "L021", "line": 1})
        assert r2.applied is False

    def test_handles_invalid_yaml(self):
        result = fix_missing_mode("{{{{invalid", {"rule_id": "L021", "line": 1})
        assert result.applied is False


# ---------------------------------------------------------------------------
# L007 transform: shell to command
# ---------------------------------------------------------------------------

class TestL007ShellToCommand:
    def test_replaces_shell_with_command(self):
        content = textwrap.dedent("""\
        - name: List files
          ansible.builtin.shell: ls -la /tmp
        """)
        result = fix_shell_to_command(content, {"rule_id": "L007", "line": 1})
        assert result.applied is True
        assert "ansible.builtin.command" in result.content
        assert "ansible.builtin.shell" not in result.content

    def test_no_change_when_pipe_present(self):
        content = textwrap.dedent("""\
        - name: Grep output
          ansible.builtin.shell: cat /tmp/log | grep error
        """)
        result = fix_shell_to_command(content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_when_and_present(self):
        content = textwrap.dedent("""\
        - name: Chain commands
          ansible.builtin.shell:
            cmd: mkdir /tmp/foo && touch /tmp/foo/bar
        """)
        result = fix_shell_to_command(content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_when_redirect_present(self):
        content = textwrap.dedent("""\
        - name: Write output
          ansible.builtin.shell: echo hello > /tmp/out
        """)
        result = fix_shell_to_command(content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_no_change_for_command_module(self):
        content = textwrap.dedent("""\
        - name: Already command
          ansible.builtin.command: echo hello
        """)
        result = fix_shell_to_command(content, {"rule_id": "L007", "line": 1})
        assert result.applied is False

    def test_idempotent(self):
        content = textwrap.dedent("""\
        - name: Simple command
          ansible.builtin.shell: whoami
        """)
        r1 = fix_shell_to_command(content, {"rule_id": "L007", "line": 1})
        assert r1.applied is True
        r2 = fix_shell_to_command(r1.content, {"rule_id": "L007", "line": 1})
        assert r2.applied is False

    def test_dict_form_cmd(self):
        content = textwrap.dedent("""\
        - name: Simple command
          ansible.builtin.shell:
            cmd: whoami
        """)
        result = fix_shell_to_command(content, {"rule_id": "L007", "line": 1})
        assert result.applied is True
        assert "ansible.builtin.command" in result.content


# ---------------------------------------------------------------------------
# M001/L002 transform: FQCN
# ---------------------------------------------------------------------------

class TestFQCNTransform:
    def test_rewrites_short_name_with_resolved_fqcn(self):
        content = textwrap.dedent("""\
        - name: Debug message
          debug:
            msg: hello
        """)
        violation = {
            "rule_id": "M001",
            "line": 1,
            "resolved_fqcn": "ansible.builtin.debug",
            "original_module": "debug",
        }
        result = fix_fqcn(content, violation)
        assert result.applied is True
        assert "ansible.builtin.debug" in result.content
        assert "\n  debug:" not in result.content

    def test_falls_back_to_static_map_for_l002(self):
        content = textwrap.dedent("""\
        - name: Copy file
          copy:
            src: /a
            dest: /b
        """)
        violation = {"rule_id": "L002", "line": 1}
        result = fix_fqcn(content, violation)
        assert result.applied is True
        assert "ansible.builtin.copy" in result.content

    def test_no_change_when_already_fqcn(self):
        content = textwrap.dedent("""\
        - name: Debug message
          ansible.builtin.debug:
            msg: hello
        """)
        violation = {"rule_id": "L002", "line": 1}
        result = fix_fqcn(content, violation)
        assert result.applied is False

    def test_no_change_for_unknown_short_name(self):
        content = textwrap.dedent("""\
        - name: Custom module
          my_custom_module:
            param: value
        """)
        violation = {"rule_id": "L002", "line": 1}
        result = fix_fqcn(content, violation)
        assert result.applied is False

    def test_idempotent(self):
        content = textwrap.dedent("""\
        - name: Install package
          yum:
            name: httpd
            state: present
        """)
        r1 = fix_fqcn(content, {"rule_id": "L002", "line": 1})
        assert r1.applied is True
        r2 = fix_fqcn(r1.content, {"rule_id": "L002", "line": 1})
        assert r2.applied is False

    def test_m003_redirect_uses_resolved_fqcn(self):
        content = textwrap.dedent("""\
        - name: Install package
          yum:
            name: httpd
        """)
        violation = {
            "rule_id": "M003",
            "line": 1,
            "original_module": "yum",
            "resolved_fqcn": "ansible.builtin.dnf",
            "redirect_chain": ["ansible.builtin.yum", "ansible.builtin.dnf"],
        }
        result = fix_fqcn(content, violation)
        assert result.applied is True
        assert "ansible.builtin.dnf" in result.content


# ---------------------------------------------------------------------------
# RemediationEngine convergence loop
# ---------------------------------------------------------------------------

class TestRemediationEngine:
    def test_converges_in_one_pass(self, tmp_path):
        playbook = tmp_path / "play.yml"
        playbook.write_text(textwrap.dedent("""\
        - name: Copy file
          ansible.builtin.copy:
            src: /a
            dest: /b
        """))

        def scan_fn(paths):
            content = playbook.read_text()
            if "mode:" not in content:
                return [{"rule_id": "L021", "file": str(playbook), "line": 1}]
            return []

        reg = TransformRegistry()
        reg.register("L021", fix_missing_mode)
        engine = RemediationEngine(reg, scan_fn, max_passes=5)

        report = engine.remediate([str(playbook)], apply=True)
        assert report.fixed >= 1
        assert report.oscillation_detected is False
        assert "mode:" in playbook.read_text()

    def test_no_apply_restores_originals(self, tmp_path):
        playbook = tmp_path / "play.yml"
        original = textwrap.dedent("""\
        - name: Copy file
          ansible.builtin.copy:
            src: /a
            dest: /b
        """)
        playbook.write_text(original)

        def scan_fn(paths):
            content = playbook.read_text()
            if "mode:" not in content:
                return [{"rule_id": "L021", "file": str(playbook), "line": 1}]
            return []

        reg = TransformRegistry()
        reg.register("L021", fix_missing_mode)
        engine = RemediationEngine(reg, scan_fn, max_passes=5)

        report = engine.remediate([str(playbook)], apply=False)
        assert report.fixed >= 1
        assert len(report.applied_patches) == 1
        assert report.applied_patches[0].diff != ""
        assert playbook.read_text() == original

    def test_oscillation_detection(self, tmp_path):
        playbook = tmp_path / "play.yml"
        playbook.write_text("- name: test\n  debug: msg=hi\n")

        call_count = [0]

        def scan_fn(paths):
            call_count[0] += 1
            return [{"rule_id": "FLIP", "file": str(playbook), "line": 1}]

        def flip_transform(content, violation):
            return TransformResult(content + "\n# flipped", True)

        reg = TransformRegistry()
        reg.register("FLIP", flip_transform)
        engine = RemediationEngine(reg, scan_fn, max_passes=3)

        report = engine.remediate([str(playbook)], apply=True)
        assert report.oscillation_detected is True
        assert report.passes <= 3

    def test_empty_scan_no_changes(self, tmp_path):
        playbook = tmp_path / "play.yml"
        playbook.write_text("- name: Clean\n  ansible.builtin.debug:\n    msg: hi\n")

        def scan_fn(paths):
            return []

        reg = build_default_registry()
        engine = RemediationEngine(reg, scan_fn, max_passes=5)

        report = engine.remediate([str(playbook)], apply=True)
        assert report.fixed == 0
        assert report.passes == 1
        assert report.oscillation_detected is False

    def test_report_tiers(self, tmp_path):
        playbook = tmp_path / "play.yml"
        playbook.write_text("- name: test\n  debug: msg=hi\n")

        def scan_fn(paths):
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


# ---------------------------------------------------------------------------
# L008 transform: local_action
# ---------------------------------------------------------------------------

class TestL008LocalAction:
    def test_string_form(self):
        content = textwrap.dedent("""\
        - name: Run locally
          local_action: command echo hello
        """)
        result = fix_local_action(content, {"rule_id": "L008", "line": 1})
        assert result.applied is True
        assert "local_action" not in result.content
        assert "delegate_to: localhost" in result.content
        assert "command:" in result.content or "ansible.builtin.command:" in result.content

    def test_dict_form(self):
        content = textwrap.dedent("""\
        - name: Run locally
          local_action:
            module: ansible.builtin.debug
            msg: hi
        """)
        result = fix_local_action(content, {"rule_id": "L008", "line": 1})
        assert result.applied is True
        assert "local_action" not in result.content
        assert "delegate_to: localhost" in result.content
        assert "ansible.builtin.debug" in result.content

    def test_no_change_without_local_action(self):
        content = textwrap.dedent("""\
        - name: Normal task
          ansible.builtin.debug:
            msg: hi
        """)
        result = fix_local_action(content, {"rule_id": "L008", "line": 1})
        assert result.applied is False

    def test_idempotent(self):
        content = textwrap.dedent("""\
        - name: Run locally
          local_action: command whoami
        """)
        r1 = fix_local_action(content, {"rule_id": "L008", "line": 1})
        assert r1.applied is True
        r2 = fix_local_action(r1.content, {"rule_id": "L008", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# L009 transform: empty string comparison
# ---------------------------------------------------------------------------

class TestL009EmptyString:
    def test_double_quote_equality(self):
        content = textwrap.dedent("""\
        - name: Check var
          ansible.builtin.debug:
            msg: empty
          when: myvar == ""
        """)
        result = fix_empty_string(content, {"rule_id": "L009", "line": 1})
        assert result.applied is True
        assert "myvar | length == 0" in result.content

    def test_single_quote_inequality(self):
        content = textwrap.dedent("""\
        - name: Check var
          ansible.builtin.debug:
            msg: not empty
          when: myvar != ''
        """)
        result = fix_empty_string(content, {"rule_id": "L009", "line": 1})
        assert result.applied is True
        assert "myvar | length > 0" in result.content

    def test_no_change_without_pattern(self):
        content = textwrap.dedent("""\
        - name: Check var
          ansible.builtin.debug:
            msg: ok
          when: myvar is defined
        """)
        result = fix_empty_string(content, {"rule_id": "L009", "line": 1})
        assert result.applied is False

    def test_idempotent(self):
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: empty
          when: myvar == ""
        """)
        r1 = fix_empty_string(content, {"rule_id": "L009", "line": 1})
        r2 = fix_empty_string(r1.content, {"rule_id": "L009", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# L011 transform: literal bool comparison
# ---------------------------------------------------------------------------

class TestL011LiteralBool:
    def test_eq_true(self):
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: enabled == true
        """)
        result = fix_literal_bool(content, {"rule_id": "L011", "line": 1})
        assert result.applied is True
        assert "enabled" in result.content
        assert "== true" not in result.content

    def test_eq_false(self):
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: enabled == false
        """)
        result = fix_literal_bool(content, {"rule_id": "L011", "line": 1})
        assert result.applied is True
        assert "not enabled" in result.content

    def test_is_true(self):
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: enabled is true
        """)
        result = fix_literal_bool(content, {"rule_id": "L011", "line": 1})
        assert result.applied is True
        assert "is true" not in result.content

    def test_python_True(self):
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: enabled == True
        """)
        result = fix_literal_bool(content, {"rule_id": "L011", "line": 1})
        assert result.applied is True

    def test_no_change_without_pattern(self):
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: enabled
        """)
        result = fix_literal_bool(content, {"rule_id": "L011", "line": 1})
        assert result.applied is False


# ---------------------------------------------------------------------------
# L015 transform: Jinja in when
# ---------------------------------------------------------------------------

class TestL015JinjaWhen:
    def test_strips_jinja_delimiters(self):
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: "{{ my_var }}"
        """)
        result = fix_jinja_when(content, {"rule_id": "L015", "line": 1})
        assert result.applied is True
        assert "{{" not in result.content
        assert "}}" not in result.content
        assert "my_var" in result.content

    def test_no_change_without_jinja(self):
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: my_var is defined
        """)
        result = fix_jinja_when(content, {"rule_id": "L015", "line": 1})
        assert result.applied is False

    def test_idempotent(self):
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.debug:
            msg: hi
          when: "{{ some_flag }}"
        """)
        r1 = fix_jinja_when(content, {"rule_id": "L015", "line": 1})
        r2 = fix_jinja_when(r1.content, {"rule_id": "L015", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# L020 transform: octal mode
# ---------------------------------------------------------------------------

class TestL020OctalMode:
    def test_octal_literal_to_string(self):
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

    def test_decimal_int_to_octal_string(self):
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

    def test_no_change_when_already_string(self):
        content = textwrap.dedent("""\
        - name: Set perms
          ansible.builtin.file:
            path: /tmp/foo
            mode: "0644"
        """)
        result = fix_octal_mode(content, {"rule_id": "L020", "line": 1})
        assert result.applied is False

    def test_string_without_leading_zero(self):
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
    def test_capitalizes_task_name(self):
        content = textwrap.dedent("""\
        - name: install packages
          ansible.builtin.debug:
            msg: hi
        """)
        result = fix_name_casing(content, {"rule_id": "L025", "line": 1})
        assert result.applied is True
        assert "Install packages" in result.content

    def test_no_change_when_already_uppercase(self):
        content = textwrap.dedent("""\
        - name: Install packages
          ansible.builtin.debug:
            msg: hi
        """)
        result = fix_name_casing(content, {"rule_id": "L025", "line": 1})
        assert result.applied is False

    def test_idempotent(self):
        content = textwrap.dedent("""\
        - name: setup network
          ansible.builtin.debug:
            msg: hi
        """)
        r1 = fix_name_casing(content, {"rule_id": "L025", "line": 1})
        r2 = fix_name_casing(r1.content, {"rule_id": "L025", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# L046 transform: free-form to dict
# ---------------------------------------------------------------------------

class TestL046FreeForm:
    def test_converts_string_to_dict(self):
        content = textwrap.dedent("""\
        - name: Run it
          ansible.builtin.command: echo hello
        """)
        result = fix_free_form(content, {"rule_id": "L046", "line": 1})
        assert result.applied is True
        assert "cmd:" in result.content
        assert "echo hello" in result.content

    def test_no_change_when_already_dict(self):
        content = textwrap.dedent("""\
        - name: Run it
          ansible.builtin.command:
            cmd: echo hello
        """)
        result = fix_free_form(content, {"rule_id": "L046", "line": 1})
        assert result.applied is False

    def test_no_change_for_non_command_module(self):
        content = textwrap.dedent("""\
        - name: Debug
          ansible.builtin.debug:
            msg: hi
        """)
        result = fix_free_form(content, {"rule_id": "L046", "line": 1})
        assert result.applied is False

    def test_shell_module(self):
        content = textwrap.dedent("""\
        - name: Run shell
          ansible.builtin.shell: cat /etc/hosts | grep localhost
        """)
        result = fix_free_form(content, {"rule_id": "L046", "line": 1})
        assert result.applied is True
        assert "cmd:" in result.content


# ---------------------------------------------------------------------------
# L043 transform: bare vars
# ---------------------------------------------------------------------------

class TestL043BareVars:
    def test_wraps_bare_var_in_with_items(self):
        content = textwrap.dedent("""\
        - name: Loop
          ansible.builtin.debug:
            msg: "{{ item }}"
          with_items: packages
        """)
        result = fix_bare_vars(content, {"rule_id": "L043", "line": 1})
        assert result.applied is True
        assert "{{ packages }}" in result.content

    def test_no_change_when_already_jinja(self):
        content = textwrap.dedent("""\
        - name: Loop
          ansible.builtin.debug:
            msg: "{{ item }}"
          with_items: "{{ packages }}"
        """)
        result = fix_bare_vars(content, {"rule_id": "L043", "line": 1})
        assert result.applied is False

    def test_no_change_without_loop(self):
        content = textwrap.dedent("""\
        - name: Simple
          ansible.builtin.debug:
            msg: hi
        """)
        result = fix_bare_vars(content, {"rule_id": "L043", "line": 1})
        assert result.applied is False


# ---------------------------------------------------------------------------
# L013 transform: changed_when
# ---------------------------------------------------------------------------

class TestL013ChangedWhen:
    def test_adds_changed_when(self):
        content = textwrap.dedent("""\
        - name: Check version
          ansible.builtin.command: python --version
        """)
        result = fix_changed_when(content, {"rule_id": "L013", "line": 1})
        assert result.applied is True
        assert "changed_when:" in result.content

    def test_no_change_when_creates_present(self):
        content = textwrap.dedent("""\
        - name: Create file
          ansible.builtin.command:
            cmd: touch /tmp/foo
            creates: /tmp/foo
        """)
        result = fix_changed_when(content, {"rule_id": "L013", "line": 1})
        assert result.applied is False

    def test_no_change_when_already_set(self):
        content = textwrap.dedent("""\
        - name: Check
          ansible.builtin.command: echo test
          changed_when: false
        """)
        result = fix_changed_when(content, {"rule_id": "L013", "line": 1})
        assert result.applied is False

    def test_no_change_for_non_command_module(self):
        content = textwrap.dedent("""\
        - name: Debug
          ansible.builtin.debug:
            msg: hi
        """)
        result = fix_changed_when(content, {"rule_id": "L013", "line": 1})
        assert result.applied is False


# ---------------------------------------------------------------------------
# L018 transform: become
# ---------------------------------------------------------------------------

class TestL018Become:
    def test_adds_become(self):
        content = textwrap.dedent("""\
        - name: Switch user
          ansible.builtin.command: whoami
          become_user: postgres
        """)
        result = fix_become(content, {"rule_id": "L018", "line": 1})
        assert result.applied is True
        assert "become: true" in result.content or "become: True" in result.content

    def test_no_change_when_become_present(self):
        content = textwrap.dedent("""\
        - name: Switch user
          ansible.builtin.command: whoami
          become: true
          become_user: postgres
        """)
        result = fix_become(content, {"rule_id": "L018", "line": 1})
        assert result.applied is False

    def test_no_change_without_become_user(self):
        content = textwrap.dedent("""\
        - name: Normal
          ansible.builtin.debug:
            msg: hi
        """)
        result = fix_become(content, {"rule_id": "L018", "line": 1})
        assert result.applied is False

    def test_become_inserted_after_become_user(self):
        content = textwrap.dedent("""\
        - name: Switch user
          ansible.builtin.command: whoami
          become_user: postgres
        """)
        result = fix_become(content, {"rule_id": "L018", "line": 1})
        assert result.applied is True
        lines = result.content.splitlines()
        bu_line = next(i for i, l in enumerate(lines) if "become_user" in l)
        b_line = next(i for i, l in enumerate(lines) if l.strip().startswith("become:"))
        assert b_line == bu_line + 1


# ---------------------------------------------------------------------------
# L022 transform: pipefail
# ---------------------------------------------------------------------------

class TestL022Pipefail:
    def test_prepends_pipefail_string_form(self):
        content = textwrap.dedent("""\
        - name: Grep logs
          ansible.builtin.shell: cat /var/log/syslog | grep error
        """)
        result = fix_pipefail(content, {"rule_id": "L022", "line": 1})
        assert result.applied is True
        assert "set -o pipefail &&" in result.content

    def test_prepends_pipefail_dict_form(self):
        content = textwrap.dedent("""\
        - name: Grep logs
          ansible.builtin.shell:
            cmd: cat /var/log/syslog | grep error
        """)
        result = fix_pipefail(content, {"rule_id": "L022", "line": 1})
        assert result.applied is True
        assert "set -o pipefail &&" in result.content

    def test_no_change_without_pipe(self):
        content = textwrap.dedent("""\
        - name: Simple
          ansible.builtin.shell: echo hello
        """)
        result = fix_pipefail(content, {"rule_id": "L022", "line": 1})
        assert result.applied is False

    def test_no_change_when_already_set(self):
        content = textwrap.dedent("""\
        - name: Grep logs
          ansible.builtin.shell: set -o pipefail && cat /var/log/syslog | grep error
        """)
        result = fix_pipefail(content, {"rule_id": "L022", "line": 1})
        assert result.applied is False

    def test_idempotent(self):
        content = textwrap.dedent("""\
        - name: Grep
          ansible.builtin.shell: cat log | grep err
        """)
        r1 = fix_pipefail(content, {"rule_id": "L022", "line": 1})
        r2 = fix_pipefail(r1.content, {"rule_id": "L022", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# L012 transform: latest → present
# ---------------------------------------------------------------------------

class TestL012Latest:
    def test_replaces_latest_with_present(self):
        content = textwrap.dedent("""\
        - name: Install httpd
          ansible.builtin.yum:
            name: httpd
            state: latest
        """)
        result = fix_latest(content, {"rule_id": "L012", "line": 1})
        assert result.applied is True
        assert "state: present" in result.content
        assert "state: latest" not in result.content

    def test_no_change_when_present(self):
        content = textwrap.dedent("""\
        - name: Install httpd
          ansible.builtin.yum:
            name: httpd
            state: present
        """)
        result = fix_latest(content, {"rule_id": "L012", "line": 1})
        assert result.applied is False

    def test_no_change_for_absent(self):
        content = textwrap.dedent("""\
        - name: Remove httpd
          ansible.builtin.yum:
            name: httpd
            state: absent
        """)
        result = fix_latest(content, {"rule_id": "L012", "line": 1})
        assert result.applied is False

    def test_idempotent(self):
        content = textwrap.dedent("""\
        - name: Install
          ansible.builtin.apt:
            name: nginx
            state: latest
        """)
        r1 = fix_latest(content, {"rule_id": "L012", "line": 1})
        r2 = fix_latest(r1.content, {"rule_id": "L012", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# M006 transform: become + ignore_errors -> add ignore_unreachable
# ---------------------------------------------------------------------------

class TestM006BecomeUnreachable:
    def test_adds_ignore_unreachable(self):
        content = textwrap.dedent("""\
        - name: Risky task
          ansible.builtin.command: whoami
          become: true
          ignore_errors: true
        """)
        result = fix_become_unreachable(content, {"rule_id": "M006", "line": 1})
        assert result.applied is True
        assert "ignore_unreachable: true" in result.content or "ignore_unreachable: True" in result.content

    def test_no_change_when_already_set(self):
        content = textwrap.dedent("""\
        - name: Safe task
          ansible.builtin.command: whoami
          become: true
          ignore_errors: true
          ignore_unreachable: true
        """)
        result = fix_become_unreachable(content, {"rule_id": "M006", "line": 1})
        assert result.applied is False

    def test_no_change_without_become(self):
        content = textwrap.dedent("""\
        - name: Normal task
          ansible.builtin.command: whoami
          ignore_errors: true
        """)
        result = fix_become_unreachable(content, {"rule_id": "M006", "line": 1})
        assert result.applied is False

    def test_inserted_after_ignore_errors(self):
        content = textwrap.dedent("""\
        - name: Risky task
          ansible.builtin.command: whoami
          become: true
          ignore_errors: true
        """)
        result = fix_become_unreachable(content, {"rule_id": "M006", "line": 1})
        lines = result.content.splitlines()
        ie_line = next(i for i, l in enumerate(lines) if "ignore_errors" in l)
        iu_line = next(i for i, l in enumerate(lines) if "ignore_unreachable" in l)
        assert iu_line == ie_line + 1


# ---------------------------------------------------------------------------
# M008 transform: bare include -> include_tasks
# ---------------------------------------------------------------------------

class TestM008BareInclude:
    def test_replaces_include(self):
        content = textwrap.dedent("""\
        - include: tasks/setup.yml
        """)
        result = fix_bare_include(content, {"rule_id": "M008", "line": 1})
        assert result.applied is True
        assert "ansible.builtin.include_tasks" in result.content
        assert "\n- include:" not in result.content

    def test_no_change_for_include_tasks(self):
        content = textwrap.dedent("""\
        - ansible.builtin.include_tasks: tasks/setup.yml
        """)
        result = fix_bare_include(content, {"rule_id": "M008", "line": 1})
        assert result.applied is False

    def test_idempotent(self):
        content = textwrap.dedent("""\
        - include: tasks/setup.yml
        """)
        r1 = fix_bare_include(content, {"rule_id": "M008", "line": 1})
        r2 = fix_bare_include(r1.content, {"rule_id": "M008", "line": 1})
        assert r2.applied is False


# ---------------------------------------------------------------------------
# M009 transform: with_items -> loop
# ---------------------------------------------------------------------------

class TestM009WithToLoop:
    def test_with_items_to_loop(self):
        content = textwrap.dedent("""\
        - name: Install packages
          ansible.builtin.yum:
            name: "{{ item }}"
            state: present
          with_items:
            - httpd
            - nginx
        """)
        result = fix_with_to_loop(content, {"rule_id": "M009", "line": 1, "with_key": "with_items"})
        assert result.applied is True
        assert "loop:" in result.content
        assert "with_items" not in result.content

    def test_no_change_for_loop(self):
        content = textwrap.dedent("""\
        - name: Install packages
          ansible.builtin.yum:
            name: "{{ item }}"
            state: present
          loop:
            - httpd
            - nginx
        """)
        result = fix_with_to_loop(content, {"rule_id": "M009", "line": 1, "with_key": "with_items"})
        assert result.applied is False

    def test_with_dict_not_handled(self):
        content = textwrap.dedent("""\
        - name: Create users
          ansible.builtin.user:
            name: "{{ item.key }}"
          with_dict: "{{ users }}"
        """)
        result = fix_with_to_loop(content, {"rule_id": "M009", "line": 1, "with_key": "with_dict"})
        assert result.applied is False

    def test_idempotent(self):
        content = textwrap.dedent("""\
        - name: Install
          ansible.builtin.yum:
            name: "{{ item }}"
          with_items:
            - httpd
        """)
        r1 = fix_with_to_loop(content, {"rule_id": "M009", "line": 1, "with_key": "with_items"})
        r2 = fix_with_to_loop(r1.content, {"rule_id": "M009", "line": 1, "with_key": "with_items"})
        assert r2.applied is False
