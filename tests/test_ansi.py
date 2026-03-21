"""Tests for ANSI color/style abstraction module."""

from __future__ import annotations

from collections.abc import Iterator
from unittest import mock

import pytest

from apme_engine.cli.ansi import (
    Style,
    bold,
    box,
    dim,
    force_no_color,
    green,
    ljust_ansi,
    red,
    remediation_badge,
    reset_color_detection,
    rjust_ansi,
    section_header,
    severity_badge,
    severity_indicator,
    strip_ansi,
    style,
    table,
    tree_prefix,
    visible_width,
    yellow,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
def reset_color_cache() -> Iterator[None]:
    """Reset color detection before each test.

    Yields:
        None: No value is yielded.
    """
    reset_color_detection()
    yield
    reset_color_detection()


@pytest.fixture  # type: ignore[untyped-decorator]
def force_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force color output on.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    reset_color_detection()


@pytest.fixture  # type: ignore[untyped-decorator]
def no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force color output off.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    reset_color_detection()


# ─────────────────────────────────────────────────────────────────────────────
# Core styling tests
# ─────────────────────────────────────────────────────────────────────────────


class TestStyle:
    """Test style() function and convenience wrappers."""

    def test_style_with_color(self, force_color: None) -> None:
        """Style wraps text with ANSI codes when color enabled.

        Args:
            force_color: Fixture that enables color output.
        """
        result = style("hello", Style.RED)
        assert result == "\033[31mhello\033[0m"

    def test_style_multiple_codes(self, force_color: None) -> None:
        """Multiple style codes are concatenated.

        Args:
            force_color: Fixture that enables color output.
        """
        result = style("hello", Style.BOLD, Style.RED)
        assert result == "\033[1m\033[31mhello\033[0m"

    def test_style_no_color_env(self, no_color: None) -> None:
        """NO_COLOR env var suppresses ANSI codes.

        Args:
            no_color: Fixture that disables color output.
        """
        result = style("hello", Style.RED)
        assert result == "hello"
        assert "\033[" not in result

    def test_bold(self, force_color: None) -> None:
        """bold() applies bold style.

        Args:
            force_color: Fixture that enables color output.
        """
        assert bold("test") == "\033[1mtest\033[0m"

    def test_red(self, force_color: None) -> None:
        """red() applies red foreground.

        Args:
            force_color: Fixture that enables color output.
        """
        assert red("error") == "\033[31merror\033[0m"

    def test_green(self, force_color: None) -> None:
        """green() applies green foreground.

        Args:
            force_color: Fixture that enables color output.
        """
        assert green("ok") == "\033[32mok\033[0m"

    def test_yellow(self, force_color: None) -> None:
        """yellow() applies yellow foreground.

        Args:
            force_color: Fixture that enables color output.
        """
        assert yellow("warn") == "\033[33mwarn\033[0m"

    def test_dim(self, force_color: None) -> None:
        """dim() applies dim style.

        Args:
            force_color: Fixture that enables color output.
        """
        assert dim("faded") == "\033[2mfaded\033[0m"

    def test_composability(self, force_color: None) -> None:
        """Styles can be composed (nested calls).

        Args:
            force_color: Fixture that enables color output.
        """
        result = bold(red("important"))
        # The inner red is applied first, then bold wraps it
        assert "\033[1m" in result
        assert "\033[31m" in result
        assert "important" in result


class TestNoColorCompliance:
    """Test NO_COLOR standard compliance (https://no-color.org)."""

    def test_no_color_any_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """NO_COLOR with any value (including empty string) disables color.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
        """
        monkeypatch.setenv("NO_COLOR", "")
        reset_color_detection()
        assert style("x", Style.RED) == "x"

    def test_no_color_zero_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """NO_COLOR set to '0' also disables color.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
        """
        monkeypatch.setenv("NO_COLOR", "0")
        reset_color_detection()
        assert style("x", Style.RED) == "x"

    def test_force_color_overrides_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FORCE_COLOR enables color even without TTY.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
        """
        monkeypatch.setenv("FORCE_COLOR", "1")
        monkeypatch.delenv("NO_COLOR", raising=False)
        reset_color_detection()

        with mock.patch("sys.stdout.isatty", return_value=False):
            result = style("x", Style.RED)
            assert "\033[31m" in result

    def test_no_color_beats_force_color(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """NO_COLOR takes precedence over FORCE_COLOR.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
        """
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setenv("FORCE_COLOR", "1")
        reset_color_detection()
        assert style("x", Style.RED) == "x"


class TestForceNoColor:
    """Test programmatic color disable (for --no-ansi)."""

    def test_force_no_color_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """force_no_color() disables color output.

        Args:
            monkeypatch: Pytest fixture for modifying environment.
        """
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        reset_color_detection()
        force_no_color()
        assert style("x", Style.RED) == "x"
        reset_color_detection()


# ─────────────────────────────────────────────────────────────────────────────
# ANSI width tests
# ─────────────────────────────────────────────────────────────────────────────


class TestAnsiWidth:
    """Test ANSI-aware string width functions."""

    def test_strip_ansi_removes_codes(self) -> None:
        """strip_ansi removes all ANSI escape sequences."""
        styled = "\033[1m\033[31mhello\033[0m"
        assert strip_ansi(styled) == "hello"

    def test_strip_ansi_plain_text(self) -> None:
        """strip_ansi leaves plain text unchanged."""
        assert strip_ansi("hello world") == "hello world"

    def test_visible_width_plain(self) -> None:
        """visible_width returns length of plain text."""
        assert visible_width("hello") == 5

    def test_visible_width_styled(self) -> None:
        """visible_width excludes ANSI codes."""
        styled = "\033[1m\033[31mhello\033[0m"
        assert visible_width(styled) == 5

    def test_ljust_ansi_plain(self) -> None:
        """ljust_ansi pads plain text correctly."""
        assert ljust_ansi("hi", 5) == "hi   "

    def test_ljust_ansi_styled(self, force_color: None) -> None:
        """ljust_ansi accounts for invisible ANSI codes.

        Args:
            force_color: Fixture that enables color output.
        """
        styled = red("hi")
        result = ljust_ansi(styled, 5)
        # Should have 3 spaces of padding
        assert result.endswith("   ")
        assert visible_width(result) == 5

    def test_rjust_ansi_plain(self) -> None:
        """rjust_ansi pads plain text correctly."""
        assert rjust_ansi("hi", 5) == "   hi"

    def test_rjust_ansi_styled(self, force_color: None) -> None:
        """rjust_ansi accounts for invisible ANSI codes.

        Args:
            force_color: Fixture that enables color output.
        """
        styled = red("hi")
        result = rjust_ansi(styled, 5)
        assert result.startswith("   ")
        assert visible_width(result) == 5


# ─────────────────────────────────────────────────────────────────────────────
# Severity badge tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSeverityBadge:
    """Test severity badge rendering."""

    def test_badge_high(self, force_color: None) -> None:
        """High severity shows ERROR badge.

        Args:
            force_color: Fixture that enables color output.
        """
        badge = severity_badge("high")
        assert "ERROR" in strip_ansi(badge)
        assert Style.BG_RED in badge

    def test_badge_medium(self, force_color: None) -> None:
        """Medium severity shows WARN badge.

        Args:
            force_color: Fixture that enables color output.
        """
        badge = severity_badge("medium")
        assert "WARN" in strip_ansi(badge)
        assert Style.BG_YELLOW in badge

    def test_badge_low(self, force_color: None) -> None:
        """Low severity shows WARN badge.

        Args:
            force_color: Fixture that enables color output.
        """
        badge = severity_badge("low")
        assert "WARN" in strip_ansi(badge)

    def test_badge_very_low(self, force_color: None) -> None:
        """Very low severity shows HINT badge.

        Args:
            force_color: Fixture that enables color output.
        """
        badge = severity_badge("very_low")
        assert "HINT" in strip_ansi(badge)
        assert Style.BG_CYAN in badge

    def test_badge_case_insensitive(self, force_color: None) -> None:
        """Badge lookup is case-insensitive.

        Args:
            force_color: Fixture that enables color output.
        """
        assert strip_ansi(severity_badge("HIGH")) == strip_ansi(severity_badge("high"))
        assert strip_ansi(severity_badge("Medium")) == strip_ansi(severity_badge("medium"))

    def test_badge_unknown_level(self, force_color: None) -> None:
        """Unknown level shows ? badge.

        Args:
            force_color: Fixture that enables color output.
        """
        badge = severity_badge("unknown")
        assert "?" in strip_ansi(badge)

    def test_badge_no_color(self, no_color: None) -> None:
        """Badge shows label without styling when NO_COLOR.

        Args:
            no_color: Fixture that disables color output.
        """
        badge = severity_badge("high")
        assert badge == " ERROR "
        assert "\033[" not in badge

    def test_severity_indicator_error(self, force_color: None) -> None:
        """Error indicator is red x.

        Args:
            force_color: Fixture that enables color output.
        """
        indicator = severity_indicator("high")
        assert "x" in strip_ansi(indicator)
        assert Style.RED in indicator

    def test_severity_indicator_warn(self, force_color: None) -> None:
        """Warning indicator is yellow triangle.

        Args:
            force_color: Fixture that enables color output.
        """
        indicator = severity_indicator("medium")
        assert "△" in strip_ansi(indicator)
        assert Style.YELLOW in indicator

    def test_severity_indicator_hint(self, force_color: None) -> None:
        """Hint indicator is cyan i.

        Args:
            force_color: Fixture that enables color output.
        """
        indicator = severity_indicator("very_low")
        assert "i" in strip_ansi(indicator)
        assert Style.CYAN in indicator


# ─────────────────────────────────────────────────────────────────────────────
# Remediation badge tests
# ─────────────────────────────────────────────────────────────────────────────


class TestRemediationBadge:
    """Test remediation badge rendering."""

    def test_badge_auto_fixable(self, force_color: None) -> None:
        """Auto-fixable shows FIX badge with green background.

        Args:
            force_color: Fixture that enables color output.
        """
        badge = remediation_badge("auto-fixable")
        assert "FIX" in strip_ansi(badge)
        assert Style.BG_GREEN in badge

    def test_badge_ai_candidate(self, force_color: None) -> None:
        """AI-candidate shows AI badge with blue background.

        Args:
            force_color: Fixture that enables color output.
        """
        badge = remediation_badge("ai-candidate")
        assert "AI" in strip_ansi(badge)
        assert Style.BG_BLUE in badge

    def test_badge_manual_review(self, force_color: None) -> None:
        """Manual-review shows MANUAL badge with magenta background.

        Args:
            force_color: Fixture that enables color output.
        """
        badge = remediation_badge("manual-review")
        assert "MANUAL" in strip_ansi(badge)
        assert Style.BG_MAGENTA in badge

    def test_badge_case_insensitive(self, force_color: None) -> None:
        """Badge lookup is case-insensitive.

        Args:
            force_color: Fixture that enables color output.
        """
        assert strip_ansi(remediation_badge("AUTO-FIXABLE")) == strip_ansi(remediation_badge("auto-fixable"))
        assert strip_ansi(remediation_badge("AI-Candidate")) == strip_ansi(remediation_badge("ai-candidate"))

    def test_badge_unknown_class(self, force_color: None) -> None:
        """Unknown class shows ? badge.

        Args:
            force_color: Fixture that enables color output.
        """
        badge = remediation_badge("unknown")
        assert "?" in strip_ansi(badge)

    def test_badge_no_color(self, no_color: None) -> None:
        """Badge shows label without styling when NO_COLOR.

        Args:
            no_color: Fixture that disables color output.
        """
        badge = remediation_badge("auto-fixable")
        assert badge == " FIX "
        assert "\033[" not in badge


# ─────────────────────────────────────────────────────────────────────────────
# Box drawing tests
# ─────────────────────────────────────────────────────────────────────────────


class TestBox:
    """Test Unicode box drawing."""

    def test_box_simple(self) -> None:
        """Simple box around content."""
        result = box("hello")
        lines = result.split("\n")
        assert len(lines) == 3  # top, content, bottom
        assert "┌" in lines[0]
        assert "┐" in lines[0]
        assert "│" in lines[1]
        assert "hello" in lines[1]
        assert "└" in lines[2]
        assert "┘" in lines[2]

    def test_box_multiline(self) -> None:
        """Box around multiline content."""
        result = box("line1\nline2\nline3")
        lines = result.split("\n")
        assert len(lines) == 5  # top, 3 content, bottom
        assert "line1" in lines[1]
        assert "line2" in lines[2]
        assert "line3" in lines[3]

    def test_box_with_title(self, force_color: None) -> None:
        """Box with title in top border.

        Args:
            force_color: Fixture that enables color output.
        """
        result = box("content", title="Title")
        lines = result.split("\n")
        assert "Title" in strip_ansi(lines[0])

    def test_box_width(self) -> None:
        """Box respects specified width."""
        result = box("hi", width=20)
        lines = result.split("\n")
        # Content line should be 22 chars (20 inner + 2 borders)
        assert visible_width(lines[1]) == 22

    def test_box_minimum_width(self) -> None:
        """Box has minimum width."""
        result = box("x")
        lines = result.split("\n")
        assert visible_width(lines[1]) >= 12  # 10 inner + 2 borders


class TestSectionHeader:
    """Test section header rendering."""

    def test_section_header_centered(self, force_color: None) -> None:
        """Section header has centered title.

        Args:
            force_color: Fixture that enables color output.
        """
        result = section_header("Test", width=20)
        assert "Test" in strip_ansi(result)
        assert "─" in result
        assert visible_width(result) == 20


# ─────────────────────────────────────────────────────────────────────────────
# Table tests
# ─────────────────────────────────────────────────────────────────────────────


class TestTable:
    """Test table formatting."""

    def test_table_simple(self, force_color: None) -> None:
        """Simple table with headers and rows.

        Args:
            force_color: Fixture that enables color output.
        """
        result = table(
            headers=["Name", "Value"],
            rows=[["foo", "1"], ["bar", "2"]],
        )
        lines = result.split("\n")
        assert len(lines) == 4  # header, underline, 2 data rows
        assert "Name" in strip_ansi(lines[0])
        assert "Value" in strip_ansi(lines[0])
        assert "─" in lines[1]
        assert "foo" in lines[2]
        assert "bar" in lines[3]

    def test_table_auto_width(self) -> None:
        """Table auto-calculates column widths."""
        result = table(
            headers=["A", "LongerHeader"],
            rows=[["short", "x"]],
        )
        lines = result.split("\n")
        # LongerHeader should determine second column width
        assert "LongerHeader" in strip_ansi(lines[0])

    def test_table_explicit_widths(self) -> None:
        """Table respects explicit column widths."""
        result = table(
            headers=["A", "B"],
            rows=[["1", "2"]],
            col_widths=[10, 10],
        )
        lines = result.split("\n")
        # Each column should be padded to 10
        # Header line should be "A" padded to 10 + sep + "B" padded to 10
        assert visible_width(lines[0]) == 10 + 2 + 10  # 2 = default sep

    def test_table_empty(self) -> None:
        """Empty table returns empty string."""
        assert table(headers=[], rows=[]) == ""

    def test_table_with_styled_content(self, force_color: None) -> None:
        """Table handles styled cell content.

        Args:
            force_color: Fixture that enables color output.
        """
        result = table(
            headers=["Status"],
            rows=[[red("ERROR")]],
        )
        lines = result.split("\n")
        assert "ERROR" in strip_ansi(lines[2])


# ─────────────────────────────────────────────────────────────────────────────
# Tree prefix tests
# ─────────────────────────────────────────────────────────────────────────────


class TestTreePrefix:
    """Test tree connector generation."""

    def test_tree_last_item(self) -> None:
        """Last item uses └──."""
        assert tree_prefix(is_last=True) == "└── "

    def test_tree_middle_item(self) -> None:
        """Middle item uses ├──."""
        assert tree_prefix(is_last=False) == "├── "

    def test_tree_nested_last(self) -> None:
        """Nested last item has correct prefix."""
        result = tree_prefix(is_last=True, depth=1, parent_prefixes=[False])
        assert "│" in result
        assert "└" in result

    def test_tree_nested_after_last_parent(self) -> None:
        """Item after last parent has space prefix."""
        result = tree_prefix(is_last=True, depth=1, parent_prefixes=[True])
        assert "│" not in result
        assert "└" in result


class TestNoAnsiFlag:
    """Test --no-ansi CLI flag with real CLI parser."""

    def test_no_ansi_flag_before_subcommand(self) -> None:
        """--no-ansi is accepted after the subcommand via shared parent parser."""
        import argparse  # noqa: PLC0415

        parser = argparse.ArgumentParser()
        global_opts = argparse.ArgumentParser(add_help=False)
        global_opts.add_argument("--na", "--no-ansi", action="store_true", default=False, dest="no_ansi")
        subs = parser.add_subparsers(dest="command", required=True)
        scan = subs.add_parser("scan", parents=[global_opts])
        scan.add_argument("target", nargs="?", default=".")
        args = parser.parse_args(["scan", "--no-ansi", "."])
        assert args.no_ansi is True

    def test_no_ansi_disables_color_via_main(self) -> None:
        """--no-ansi flag triggers force_no_color when processed by main()."""
        from unittest.mock import patch  # noqa: PLC0415

        import apme_engine.cli as cli_module  # noqa: PLC0415

        with (
            patch("apme_engine.cli.ansi.force_no_color") as mock_fnc,
            patch("apme_engine.cli.scan.run_scan"),
            patch("sys.argv", ["apme-scan", "scan", "--no-ansi", "."]),
        ):
            cli_module.main()
        mock_fnc.assert_called_once()
