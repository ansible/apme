"""CLI integration tests for the format and fix subcommands.

Exercises the full CLI pipeline via subprocess against a messy YAML fixture.
No containers required — runs in the normal test suite.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "integration" / "test_format_playbook.yml"


def _cli(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run apme-scan CLI via ``python -m apme_engine.cli``.

    Args:
        *args: CLI arguments (e.g. 'format', '--check', path).
        cwd: Working directory; defaults to REPO_ROOT.

    Returns:
        CompletedProcess with stdout, stderr, returncode.
    """
    return subprocess.run(
        [sys.executable, "-m", "apme_engine.cli", *args],
        capture_output=True,
        text=True,
        cwd=cwd or str(REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture  # type: ignore[untyped-decorator]
def messy_dir(tmp_path: Path) -> Path:
    """Copy the messy fixture into a temp directory so tests can mutate it.

    Args:
        tmp_path: Pytest temporary directory fixture.

    Returns:
        Path to temp directory containing playbook.yml.
    """
    dest = tmp_path / "project"
    dest.mkdir()
    shutil.copy2(FIXTURE, dest / "playbook.yml")
    return dest


@pytest.fixture  # type: ignore[untyped-decorator]
def messy_file(messy_dir: Path) -> Path:
    """Path to playbook.yml in messy_dir.

    Args:
        messy_dir: Fixture providing a directory with messy YAML files.

    Returns:
        Path to playbook.yml.
    """
    return messy_dir / "playbook.yml"


# ---------------------------------------------------------------------------
# format subcommand
# ---------------------------------------------------------------------------


class TestFormatDiff:
    """format (no flags) — show diff on stdout, exit 0."""

    def test_produces_diff_output(self, messy_file: Path) -> None:
        """Format produces unified diff on stdout.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        r = _cli("format", str(messy_file))
        assert r.returncode == 0
        assert "---" in r.stdout or "@@" in r.stdout, "Expected unified diff in stdout"

    def test_diff_contains_filename(self, messy_file: Path) -> None:
        """Diff output contains playbook filename.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        r = _cli("format", str(messy_file))
        assert "playbook.yml" in r.stdout

    def test_diff_shows_jinja_fix(self, messy_file: Path) -> None:
        """Diff shows Jinja spacing fixes.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        r = _cli("format", str(messy_file))
        assert "{{ inventory_hostname }}" in r.stdout or "{{inventory_hostname}}" in r.stdout

    def test_file_not_modified(self, messy_file: Path) -> None:
        """Format without --apply does not modify file.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        original = messy_file.read_text()
        _cli("format", str(messy_file))
        assert messy_file.read_text() == original, "format without --apply should not modify file"


class TestFormatCheck:
    """format --check — exit 1 if files need formatting."""

    def test_exits_1_on_messy_file(self, messy_file: Path) -> None:
        """Format --check on messy file exits 1.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        r = _cli("format", "--check", str(messy_file))
        assert r.returncode == 1

    def test_message_mentions_reformatted(self, messy_file: Path) -> None:
        """Format --check stderr mentions reformatted.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        r = _cli("format", "--check", str(messy_file))
        assert "reformatted" in r.stderr.lower() or "would be" in r.stderr.lower()

    def test_exits_0_after_apply(self, messy_file: Path) -> None:
        """Format --check exits 0 after format --apply.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        _cli("format", "--apply", str(messy_file))
        r = _cli("format", "--check", str(messy_file))
        assert r.returncode == 0


class TestFormatApply:
    """format --apply — write files in place."""

    def test_modifies_file(self, messy_file: Path) -> None:
        """Format --apply modifies file content.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        original = messy_file.read_text()
        r = _cli("format", "--apply", str(messy_file))
        assert r.returncode == 0
        assert messy_file.read_text() != original

    def test_tabs_removed(self, messy_file: Path) -> None:
        """Format --apply removes tabs.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        _cli("format", "--apply", str(messy_file))
        assert "\t" not in messy_file.read_text()

    def test_jinja_normalized(self, messy_file: Path) -> None:
        """Format --apply normalizes Jinja spacing.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        _cli("format", "--apply", str(messy_file))
        content = messy_file.read_text()
        assert "{{ inventory_hostname }}" in content
        assert "{{ some_var }}" in content
        assert "{{ item.name | default('none') }}" in content

    def test_name_before_module(self, messy_file: Path) -> None:
        """Format --apply moves name before module.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        _cli("format", "--apply", str(messy_file))
        content = messy_file.read_text()
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if "name: Say hello" in line:
                for j in range(i + 1, min(i + 5, len(lines))):
                    if "ansible.builtin.debug" in lines[j]:
                        break
                else:
                    pytest.fail("Expected ansible.builtin.debug after 'name: Say hello'")
                break

    def test_comments_preserved(self, messy_file: Path) -> None:
        """Format --apply preserves comments.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        _cli("format", "--apply", str(messy_file))
        content = messy_file.read_text()
        assert "# Play-level comment" in content
        assert "# keep this" in content
        assert "# Misordered keys" in content

    def test_octal_preserved(self, messy_file: Path) -> None:
        """Format --apply preserves octal mode strings.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        _cli("format", "--apply", str(messy_file))
        assert "0644" in messy_file.read_text()

    def test_idempotent_after_apply(self, messy_file: Path) -> None:
        """Second format pass produces no changes.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        _cli("format", "--apply", str(messy_file))
        first_pass = messy_file.read_text()
        _cli("format", "--apply", str(messy_file))
        assert messy_file.read_text() == first_pass, "Second format pass changed the file"


class TestFormatDirectory:
    """format on a directory discovers all YAML files."""

    def test_formats_all_yaml_in_dir(self, messy_dir: Path) -> None:
        """Format on directory processes all YAML files.

        Args:
            messy_dir: Fixture providing a directory with messy YAML files.

        """
        (messy_dir / "extra.yml").write_text("- ansible.builtin.debug:\n    msg: hi\n  name: Reorder me\n")
        r = _cli("format", "--check", str(messy_dir))
        assert r.returncode == 1
        assert "2 file(s)" in r.stderr or "file(s) would be" in r.stderr

    def test_skips_non_yaml(self, messy_dir: Path) -> None:
        """Format skips non-YAML files.

        Args:
            messy_dir: Fixture providing a directory with messy YAML files.

        """
        (messy_dir / "readme.txt").write_text("not yaml at all")
        r = _cli("format", "--apply", str(messy_dir))
        assert r.returncode == 0
        assert (messy_dir / "readme.txt").read_text() == "not yaml at all"


class TestFormatExclude:
    """format --exclude skips matching files."""

    def test_exclude_pattern(self, messy_dir: Path) -> None:
        """Format --exclude skips matching paths.

        Args:
            messy_dir: Fixture providing a directory with messy YAML files.

        """
        vendor = messy_dir / "vendor"
        vendor.mkdir()
        shutil.copy2(FIXTURE, vendor / "lib.yml")

        r = _cli("format", "--check", "--exclude", "vendor/*", str(messy_dir))
        combined = r.stdout + r.stderr
        assert "vendor" not in combined or "lib.yml" not in combined


# ---------------------------------------------------------------------------
# fix subcommand
# ---------------------------------------------------------------------------


class TestFixApply:
    """fix --apply — format → idempotency check → (modernize stub)."""

    def test_formats_and_passes_idempotency(self, messy_file: Path) -> None:
        """Fix --apply formats and passes idempotency check.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        r = _cli("fix", "--apply", str(messy_file))
        assert r.returncode == 0
        assert "updated" in r.stderr.lower() or "no changes" in r.stderr.lower()

    def test_file_is_formatted_after_fix(self, messy_file: Path) -> None:
        """File passes format --check after fix --apply.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        _cli("fix", "--apply", str(messy_file))
        r = _cli("format", "--check", str(messy_file))
        assert r.returncode == 0, "File should pass format --check after fix --apply"

    def test_remediation_runs_full_pipeline(self, messy_file: Path) -> None:
        """Fix --apply runs full remediation pipeline.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        r = _cli("fix", "--apply", str(messy_file))
        assert r.returncode == 0
        assert "remediation" in r.stderr.lower()


class TestFixCheck:
    """fix --check — exit 1 if formatting changes are needed."""

    def test_exits_1_on_messy_file(self, messy_file: Path) -> None:
        """Fix --check on messy file exits 1.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        r = _cli("fix", "--check", str(messy_file))
        assert r.returncode == 1

    def test_exits_0_after_apply(self, messy_file: Path) -> None:
        """Fix --check exits 0 after fix --apply.

        Args:
            messy_file: Fixture providing a messy YAML file.

        """
        _cli("fix", "--apply", str(messy_file))
        r = _cli("fix", "--check", str(messy_file))
        assert r.returncode == 0
