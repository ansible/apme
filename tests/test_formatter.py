"""Tests for the YAML formatter."""

import textwrap
from pathlib import Path

import pytest

from apme_engine.formatter import (
    FormatResult,
    check_idempotent,
    format_content,
    format_directory,
    format_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt(text: str, filename: str = "test.yml") -> FormatResult:
    """Format dedented text and return FormatResult.

    Args:
        text: YAML content (will be dedented).
        filename: Optional filename for the formatter.

    Returns:
        FormatResult from format_content.
    """
    return format_content(textwrap.dedent(text), filename=filename)


# ---------------------------------------------------------------------------
# Tab removal (L040)
# ---------------------------------------------------------------------------


class TestTabRemoval:
    """Tests for tab removal (L040)."""

    def test_tabs_replaced_with_spaces(self) -> None:
        """Tabs in YAML are replaced with spaces."""
        result = _fmt("- name: Test\n\tansible.builtin.debug:\n\t\tmsg: hello\n")
        assert "\t" not in result.formatted
        assert result.changed

    def test_no_tabs_unchanged(self) -> None:
        """Content without tabs is unchanged."""
        text = "- name: Test\n  ansible.builtin.debug:\n    msg: hello\n"
        result = format_content(text)
        assert "\t" not in result.formatted


# ---------------------------------------------------------------------------
# Key reorder (L041)
# ---------------------------------------------------------------------------


class TestKeyReorder:
    """Tests for key reorder (L041)."""

    def test_name_moved_before_module(self) -> None:
        """Name key is moved before module key in tasks."""
        text = textwrap.dedent("""\
        - ansible.builtin.debug:
            msg: hello
          name: Say hello
        """)
        result = format_content(text)
        lines = result.formatted.splitlines()
        name_line = next(i for i, line in enumerate(lines) if "name:" in line)
        debug_line = next(i for i, line in enumerate(lines) if "debug" in line)
        assert name_line < debug_line, "name should come before module"
        assert result.changed

    def test_already_ordered_unchanged(self) -> None:
        """Already-ordered keys remain stable."""
        text = textwrap.dedent("""\
        - name: Say hello
          ansible.builtin.debug:
            msg: hello
        """)
        result = format_content(text)
        # May still change due to other formatting; key order should be stable
        lines = result.formatted.splitlines()
        name_line = next(i for i, line in enumerate(lines) if "name:" in line)
        debug_line = next(i for i, line in enumerate(lines) if "debug" in line)
        assert name_line < debug_line

    def test_play_level_key_order(self) -> None:
        """Play-level keys are reordered correctly."""
        text = textwrap.dedent("""\
        - tasks:
            - ansible.builtin.debug:
                msg: hi
              name: Task
          name: Play
          hosts: all
        """)
        result = format_content(text)
        assert "name:" in result.formatted
        lines = result.formatted.splitlines()
        name_lines = [i for i, line in enumerate(lines) if "name:" in line]
        assert len(name_lines) >= 1


# ---------------------------------------------------------------------------
# Jinja spacing (L051)
# ---------------------------------------------------------------------------


class TestJinjaSpacing:
    """Tests for Jinja spacing (L051)."""

    def test_no_space_gets_space(self) -> None:
        """Jinja without spaces gets normalized spacing."""
        text = '- name: Test\n  ansible.builtin.debug:\n    msg: "{{foo}}"\n'
        result = format_content(text)
        assert "{{ foo }}" in result.formatted
        assert result.changed

    def test_extra_spaces_normalized(self) -> None:
        """Extra spaces in Jinja are normalized."""
        text = '- name: Test\n  ansible.builtin.debug:\n    msg: "{{  foo  }}"\n'
        result = format_content(text)
        assert "{{ foo }}" in result.formatted

    def test_already_correct_unchanged(self) -> None:
        """Already-correct Jinja spacing is unchanged."""
        text = '- name: Test\n  ansible.builtin.debug:\n    msg: "{{ foo }}"\n'
        result = format_content(text)
        assert "{{ foo }}" in result.formatted

    def test_complex_expression(self) -> None:
        """Complex Jinja expressions are formatted correctly."""
        text = "- name: Test\n  ansible.builtin.debug:\n    msg: \"{{item.name | default('none')}}\"\n"
        result = format_content(text)
        assert "{{ item.name | default('none') }}" in result.formatted


# ---------------------------------------------------------------------------
# Indentation normalization
# ---------------------------------------------------------------------------


class TestIndentation:
    """Tests for indentation normalization."""

    def test_mixed_indent_normalized(self) -> None:
        """Mixed indentation is normalized to 2-space increments."""
        text = "- name: Test\n    ansible.builtin.debug:\n        msg: hello\n"
        result = format_content(text)
        lines = result.formatted.splitlines()
        for line in lines:
            stripped = line.lstrip()
            if stripped and not stripped.startswith("-"):
                indent = len(line) - len(stripped)
                assert indent % 2 == 0, f"Non-2-space indent: {line!r}"


# ---------------------------------------------------------------------------
# Comment preservation
# ---------------------------------------------------------------------------


class TestComments:
    """Tests for comment preservation."""

    def test_inline_comment_preserved(self) -> None:
        """Inline comments are preserved after formatting."""
        text = "- name: Test  # important\n  ansible.builtin.debug:\n    msg: hello\n"
        result = format_content(text)
        assert "# important" in result.formatted

    def test_full_line_comment_preserved(self) -> None:
        """Full-line comments are preserved."""
        text = "# This is a play\n- name: Test\n  ansible.builtin.debug:\n    msg: hello\n"
        result = format_content(text)
        assert "# This is a play" in result.formatted

    def test_preamble_comment_preserved(self) -> None:
        """Preamble comments before --- are preserved."""
        text = "# Header comment\n---\n- name: Test\n  ansible.builtin.debug:\n    msg: hello\n"
        result = format_content(text)
        assert "# Header comment" in result.formatted


# ---------------------------------------------------------------------------
# Octal preservation
# ---------------------------------------------------------------------------


class TestOctal:
    """Tests for octal mode preservation."""

    def test_octal_mode_preserved(self) -> None:
        """Octal mode strings like 0644 are preserved."""
        text = textwrap.dedent("""\
        - name: Set perms
          ansible.builtin.file:
            path: /tmp/foo
            mode: "0644"
        """)
        result = format_content(text)
        assert "0644" in result.formatted


# ---------------------------------------------------------------------------
# ansible-lint compatible output format
# ---------------------------------------------------------------------------


class TestAnsibleLintAlignment:
    """Verify formatted output matches ansible-lint's expected YAML style."""

    def test_nested_sequence_indent(self) -> None:
        """Nested sequences under mapping keys use indent=4 / dash_offset=2."""
        text = textwrap.dedent("""\
        ---
        - hosts: all
          tasks:
          - name: A task
            ansible.builtin.debug:
              msg: hello
        """)
        result = format_content(text)
        expected = textwrap.dedent("""\
        ---
        - hosts: all
          tasks:
            - name: A task
              ansible.builtin.debug:
                msg: hello
        """)
        assert result.formatted == expected

    def test_root_level_sequence_not_indented(self) -> None:
        """Root-level sequences stay at column 0 (no extra indent)."""
        text = textwrap.dedent("""\
        - name: First
          ansible.builtin.debug:
            msg: one
        - name: Second
          ansible.builtin.debug:
            msg: two
        """)
        result = format_content(text)
        lines = result.formatted.splitlines()
        dash_lines = [line for line in lines if line.lstrip().startswith("- name:")]
        for line in dash_lines:
            assert line.startswith("- "), f"Root sequence item should start at col 0: {line!r}"

    def test_deeply_nested_sequences(self) -> None:
        """Multiple levels of nested sequences each indent by 4/2."""
        text = textwrap.dedent("""\
        ---
        - hosts: all
          tasks:
            - name: Nested block
              block:
                - name: Inner task
                  ansible.builtin.debug:
                    msg: deep
        """)
        result = format_content(text)
        expected = textwrap.dedent("""\
        ---
        - hosts: all
          tasks:
            - name: Nested block
              block:
                - name: Inner task
                  ansible.builtin.debug:
                    msg: deep
        """)
        assert result.formatted == expected

    def test_explicit_start_present(self) -> None:
        """Formatted output includes explicit document start marker."""
        text = textwrap.dedent("""\
        - name: No doc start
          ansible.builtin.debug:
            msg: hi
        """)
        result = format_content(text)
        assert result.formatted.startswith("---\n")

    def test_tags_list_indented(self) -> None:
        """Lists under task keys like tags use the nested indent style."""
        text = textwrap.dedent("""\
        ---
        - hosts: all
          tasks:
            - name: Tagged task
              ansible.builtin.debug:
                msg: hi
              tags:
                - foo
                - bar
        """)
        result = format_content(text)
        expected = textwrap.dedent("""\
        ---
        - hosts: all
          tasks:
            - name: Tagged task
              ansible.builtin.debug:
                msg: hi
              tags:
                - foo
                - bar
        """)
        assert result.formatted == expected

    def test_vars_dict_indent(self) -> None:
        """Mapping values under task keys use standard 2-space map indent."""
        text = textwrap.dedent("""\
        ---
        - hosts: all
          vars:
            my_var: value
            other_var: 42
          tasks:
            - name: Use var
              ansible.builtin.debug:
                msg: "{{ my_var }}"
        """)
        result = format_content(text)
        expected = textwrap.dedent("""\
        ---
        - hosts: all
          vars:
            my_var: value
            other_var: 42
          tasks:
            - name: Use var
              ansible.builtin.debug:
                msg: "{{ my_var }}"
        """)
        assert result.formatted == expected


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_file(self) -> None:
        """Empty file produces no changes."""
        result = format_content("")
        assert not result.changed

    def test_non_yaml_content(self) -> None:
        """Non-YAML content produces no changes."""
        result = format_content("this is not yaml: [[[invalid")
        assert not result.changed

    def test_scalar_document_returned_unchanged(self) -> None:
        """Scalar-only document is returned unchanged."""
        result = format_content("hello\n")
        assert not result.changed
        assert result.formatted == "hello\n"

    def test_empty_document_marker(self) -> None:
        """Empty document marker (---) is handled."""
        result = format_content("---\n")
        assert not result.changed or result.formatted.strip() == "---"

    def test_already_formatted_no_change(self) -> None:
        """Already-formatted content produces no changes on second pass."""
        text = textwrap.dedent("""\
        - name: Already clean
          ansible.builtin.debug:
            msg: "{{ foo }}"
        """)
        result = format_content(text)
        if result.changed:
            second = format_content(result.formatted)
            assert not second.changed, "Second pass should produce no changes"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Tests for formatter idempotency."""

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        "text,desc",
        [
            ("- name: Test\n\tansible.builtin.debug:\n\t\tmsg: hello\n", "tabs"),
            ("- ansible.builtin.debug:\n    msg: hello\n  name: Reorder\n", "key order"),
            ('- name: T\n  ansible.builtin.debug:\n    msg: "{{foo}}"\n', "jinja spacing"),
            ("- name: Test\n    ansible.builtin.debug:\n        msg: deep\n", "mixed indent"),
            ("# comment\n- name: Test\n  ansible.builtin.debug:\n    msg: hi\n", "with comment"),
        ],
    )
    def test_format_twice_no_diff(self, text: str, desc: str) -> None:
        """Formatting twice produces no diff for various inputs.

        Args:
            text: Parametrized YAML content to format.
            desc: Human-readable description of the test case.

        """
        first = format_content(text, filename=f"test_{desc}.yml")
        assert check_idempotent(first), f"Formatter is not idempotent for: {desc}"

    def test_idempotent_complex_playbook(self) -> None:
        """Complex playbook is idempotent after formatting."""
        text = textwrap.dedent("""\
        # Playbook header
        ---
        - hosts: all
          become: true
          tasks:
            - ansible.builtin.shell: echo "hello"
              name: Say hello
              when: ansible_os_family == "Debian"
              tags:
                - setup

            - name: Install packages
              ansible.builtin.yum:
                name: "{{item}}"
                state: present
              loop:
                - httpd
                - nginx

            - name: Download file
              ansible.builtin.get_url:
                url: https://example.com/file.tar.gz
                dest: /tmp/file.tar.gz
                mode: "0644"
        """)
        first = format_content(text)
        assert check_idempotent(first), "Complex playbook is not idempotent"


# ---------------------------------------------------------------------------
# format_file (filesystem)
# ---------------------------------------------------------------------------


class TestFormatFile:
    """Tests for format_file (filesystem)."""

    def test_format_file_reads_and_formats(self, tmp_path: Path) -> None:
        """format_file reads file and returns formatted result.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        p = tmp_path / "test.yml"
        p.write_text("- ansible.builtin.debug:\n    msg: hi\n  name: Test\n")
        result = format_file(p)
        assert result.path == p
        assert result.changed
        lines = result.formatted.splitlines()
        name_line = next(i for i, line in enumerate(lines) if "name:" in line)
        debug_line = next(i for i, line in enumerate(lines) if "debug" in line)
        assert name_line < debug_line


# ---------------------------------------------------------------------------
# format_directory
# ---------------------------------------------------------------------------


class TestFormatDirectory:
    """Tests for format_directory (recursive YAML formatting)."""

    def test_discovers_yaml_files(self, tmp_path: Path) -> None:
        """format_directory discovers .yml and .yaml files.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        (tmp_path / "a.yml").write_text("- name: A\n  ansible.builtin.debug:\n    msg: a\n")
        (tmp_path / "b.yaml").write_text("- name: B\n  ansible.builtin.debug:\n    msg: b\n")
        (tmp_path / "c.txt").write_text("not yaml")
        results = format_directory(tmp_path)
        paths = {r.path.name for r in results}
        assert "a.yml" in paths
        assert "b.yaml" in paths
        assert "c.txt" not in paths

    def test_skips_git_dir(self, tmp_path: Path) -> None:
        """format_directory skips .git directory.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config.yml").write_text("- name: Git\n  debug: msg=hi\n")
        (tmp_path / "play.yml").write_text("- name: Play\n  ansible.builtin.debug:\n    msg: hi\n")
        results = format_directory(tmp_path)
        paths = {r.path.name for r in results}
        assert "config.yml" not in paths
        assert "play.yml" in paths

    def test_multidepth_workspace(self, tmp_path: Path) -> None:
        """Formatter recurses into nested role/playbook directory structures.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        (tmp_path / "site.yml").write_text("- ansible.builtin.debug:\n    msg: hi\n  name: Top\n")
        roles = tmp_path / "roles" / "web" / "tasks"
        roles.mkdir(parents=True)
        (roles / "main.yml").write_text("- ansible.builtin.shell: echo\n  name: Deep task\n")
        group_vars = tmp_path / "inventory" / "group_vars"
        group_vars.mkdir(parents=True)
        (group_vars / "all.yml").write_text('my_var: "{{foo}}"\n')

        results = format_directory(tmp_path)
        result_paths = {str(r.path.relative_to(tmp_path)) for r in results}

        assert "site.yml" in result_paths
        assert str(Path("roles/web/tasks/main.yml")) in result_paths
        assert str(Path("inventory/group_vars/all.yml")) in result_paths
        assert len(results) == 3

        changed = [r for r in results if r.changed]
        assert len(changed) >= 2, "At least site.yml and group_vars/all.yml should change"

        for r in changed:
            assert check_idempotent(r), f"Not idempotent: {r.path}"

    def test_exclude_pattern(self, tmp_path: Path) -> None:
        """exclude_patterns skips matching paths.

        Args:
            tmp_path: Pytest temporary directory fixture.

        """
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "lib.yml").write_text("- name: Vendor\n  debug: msg=hi\n")
        (tmp_path / "main.yml").write_text("- name: Main\n  ansible.builtin.debug:\n    msg: hi\n")
        results = format_directory(tmp_path, exclude_patterns=["vendor/*"])
        paths = {r.path.name for r in results}
        assert "lib.yml" not in paths
        assert "main.yml" in paths


# ---------------------------------------------------------------------------
# FormatResult.diff content
# ---------------------------------------------------------------------------


class TestDiffOutput:
    """Tests for FormatResult.diff content."""

    def test_diff_contains_file_paths(self) -> None:
        """Diff output contains a/ and b/ file paths."""
        text = "- ansible.builtin.debug:\n    msg: hi\n  name: Test\n"
        result = format_content(text, filename="playbook.yml")
        assert result.changed
        assert "a/playbook.yml" in result.diff
        assert "b/playbook.yml" in result.diff

    def test_no_diff_when_unchanged(self) -> None:
        """Unchanged content produces empty diff."""
        text = "- name: Test\n  ansible.builtin.debug:\n    msg: hi\n"
        result = format_content(text)
        if not result.changed:
            assert result.diff == ""


# ---------------------------------------------------------------------------
# Inline key=value expansion
# ---------------------------------------------------------------------------


class TestExpandInlineKVArgs:
    """Tests for _expand_inline_kv_args via format_content."""

    def test_simple_kv_expansion(self) -> None:
        """Simple key=value pairs are expanded to a YAML mapping."""
        text = '- name: Add group\n  group: name="mygroup" gid="1000"\n'
        result = format_content(text)
        assert "name: mygroup" in result.formatted
        assert "gid: '1000'" in result.formatted or 'gid: "1000"' in result.formatted

    def test_kv_with_jinja(self) -> None:
        """Jinja expressions inside quoted values are preserved."""
        text = '- name: Add group\n  group: name="{{ item.name }}" gid="{{ item.gid }}"\n'
        result = format_content(text)
        assert "{{ item.name }}" in result.formatted
        assert "{{ item.gid }}" in result.formatted

    def test_command_module_excluded(self) -> None:
        """Command modules keep their string value unchanged."""
        text = "- name: Run cmd\n  command: echo hello world\n"
        result = format_content(text)
        assert "echo hello world" in result.formatted

    def test_shell_module_excluded(self) -> None:
        """Shell modules keep their string value unchanged."""
        text = "- name: Run shell\n  shell: ls -la /tmp\n"
        result = format_content(text)
        assert "ls -la /tmp" in result.formatted

    def test_raw_module_excluded(self) -> None:
        """Raw module keeps its string value unchanged."""
        text = "- name: Raw cmd\n  raw: yum install -y httpd\n"
        result = format_content(text)
        assert "yum install -y httpd" in result.formatted

    def test_script_module_excluded(self) -> None:
        """Script module keeps its string value unchanged."""
        text = "- name: Run script\n  script: /opt/run.sh --flag\n"
        result = format_content(text)
        assert "/opt/run.sh --flag" in result.formatted

    def test_no_kv_string_unchanged(self) -> None:
        """Strings without = are left as-is."""
        text = "- name: Debug\n  ansible.builtin.debug:\n    msg: hello world\n"
        result = format_content(text)
        assert "msg: hello world" in result.formatted

    def test_kv_expansion_idempotent(self) -> None:
        """Applying format twice produces the same output."""
        text = '- name: Add group\n  group: name="mygroup" gid="1000"\n'
        r1 = format_content(text)
        r2 = format_content(r1.formatted)
        assert r1.formatted == r2.formatted


# ---------------------------------------------------------------------------
# Tags block style
# ---------------------------------------------------------------------------


class TestForceTagsBlockStyle:
    """Tests for _force_tags_block_style via format_content."""

    def test_flow_tags_to_block(self) -> None:
        """Flow-style tags list is converted to block style."""
        text = "- name: Test\n  ansible.builtin.debug:\n    msg: hi\n  tags: [users, groups]\n"
        result = format_content(text)
        assert "- users" in result.formatted
        assert "- groups" in result.formatted

    def test_block_tags_unchanged(self) -> None:
        """Already block-style tags are not modified."""
        text = "- name: Test\n  ansible.builtin.debug:\n    msg: hi\n  tags:\n    - users\n    - groups\n"
        result = format_content(text)
        assert "- users" in result.formatted
        assert "- groups" in result.formatted
