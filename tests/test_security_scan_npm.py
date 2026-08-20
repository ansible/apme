"""Tests for semver-aware npm security scan helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "security_scan_npm"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from security_scan_npm import (  # noqa: E402
    find_undici_in_lockfile,
    find_undici_in_package_json,
    scan_paths,
)


class TestUndiciSemverScan:
    """Undici advisories cover exact, caret, tilde, and lockfile forms."""

    def test_exact_version_flagged(self) -> None:
        """Flag exact undici 8.7.0 in package.json."""
        data = json.loads((FIXTURES / "package_exact_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "8.7.0"

    def test_caret_range_flagged(self) -> None:
        """Flag caret undici range ^8.7.0 in package.json."""
        data = json.loads((FIXTURES / "package_caret_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "^8.7.0"

    def test_tilde_range_flagged(self) -> None:
        """Flag tilde undici range ~8.7.0 in package.json."""
        data = json.loads((FIXTURES / "package_tilde_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "~8.7.0"

    def test_8x_range_flagged(self) -> None:
        """Flag npm 8.x range declarations in package.json."""
        data = json.loads((FIXTURES / "package_8x_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "8.x"

    def test_lt_range_flagged(self) -> None:
        """Flag npm <8.9.0 range declarations in package.json."""
        data = json.loads((FIXTURES / "package_lt_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "<8.9.0"

    def test_union_range_flagged(self) -> None:
        """Flag npm union ranges when any branch resolves to vulnerable 8.x."""
        data = json.loads((FIXTURES / "package_union_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "8.9.0 || 8.7.0"

    def test_lte_range_flagged(self) -> None:
        """Flag inclusive <=8.0.0 ranges that include vulnerable 8.0.0."""
        data = json.loads((FIXTURES / "package_lte_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "<=8.0.0"

    def test_hyphen_range_flagged(self) -> None:
        """Flag npm hyphen ranges such as 8.0.0 - 8.8.0."""
        data = json.loads((FIXTURES / "package_hyphen_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "8.0.0 - 8.8.0"

    def test_hyphen_boundary_range_flagged(self) -> None:
        """Flag inclusive hyphen ranges that include vulnerable 8.0.0."""
        data = json.loads((FIXTURES / "package_hyphen_boundary_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "8.0.0 - 8.0.0"

    def test_gte_lte_boundary_range_flagged(self) -> None:
        """Flag compound >=8.0.0 <=8.0.0 ranges that include vulnerable 8.0.0."""
        data = json.loads((FIXTURES / "package_gte_lte_boundary_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == ">=8.0.0 <=8.0.0"

    def test_reordered_comparator_range_flagged(self) -> None:
        """Flag reordered <=8.0.0 >=8.0.0 comparator sets that include vulnerable 8.0.0."""
        data = json.loads((FIXTURES / "package_reordered_comparator_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "<=8.0.0 >=8.0.0"

    def test_gt_lte_boundary_range_flagged(self) -> None:
        """Flag compound >7.0.0 <=8.0.0 ranges that include vulnerable 8.0.0."""
        data = json.loads((FIXTURES / "package_gt_lte_boundary_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == ">7.0.0 <=8.0.0"

    def test_comparator_interior_range_flagged(self) -> None:
        """Flag compound >8.0.0 <8.8.0 ranges that include vulnerable 8.7.0."""
        data = json.loads((FIXTURES / "package_comparator_interior_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == ">8.0.0 <8.8.0"

    def test_wildcard_star_range_flagged(self) -> None:
        """Flag npm 8.* wildcard ranges in package.json."""
        data = json.loads((FIXTURES / "package_wildcard_star_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "8.*"

    def test_wildcard_upper_x_range_flagged(self) -> None:
        """Flag npm 8.X wildcard ranges in package.json."""
        data = json.loads((FIXTURES / "package_wildcard_upper_x_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "8.X"

    def test_equality_comparator_flagged(self) -> None:
        """Flag npm =8.7.0 equality comparator ranges."""
        data = json.loads((FIXTURES / "package_equality_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "=8.7.0"

    def test_any_range_flagged(self) -> None:
        """Flag npm * ranges that may resolve to vulnerable 8.x."""
        data = json.loads((FIXTURES / "package_any_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "*"

    def test_safe_version_not_flagged(self) -> None:
        """Do not flag jsdom-compatible undici 7.29.0."""
        data = json.loads((FIXTURES / "package_safe.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is False

    def test_override_only_vuln_flagged(self) -> None:
        """Flag vulnerable undici pins declared only under overrides."""
        data = json.loads((FIXTURES / "package_override_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].location == "overrides.undici"
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "8.7.0"

    def test_nested_override_vuln_flagged(self) -> None:
        """Flag nested undici overrides under package-specific override maps."""
        data = json.loads((FIXTURES / "package_override_nested_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].location == "overrides.jsdom.undici"
        assert findings[0].vulnerable is True

    def test_npm_alias_spec_flagged(self) -> None:
        """Flag npm alias specs such as npm:undici@8.7.0."""
        data = json.loads((FIXTURES / "package_npm_alias_vuln.json").read_text())
        findings = find_undici_in_package_json(data)
        assert len(findings) == 1
        assert findings[0].vulnerable is True
        assert findings[0].version_or_range == "npm:undici@8.7.0"

    def test_lockfile_resolved_version_flagged(self) -> None:
        """Flag resolved undici 8.7.0 in package-lock.json."""
        data = json.loads((FIXTURES / "package-lock_vuln.json").read_text())
        findings = find_undici_in_lockfile(data)
        assert any(f.location == "node_modules/undici" and f.vulnerable for f in findings)

    def test_lockfile_v1_nested_undici_flagged(self) -> None:
        """Flag transitive undici in lockfile v1 nested dependencies."""
        data = json.loads((FIXTURES / "package-lock_v1_nested.json").read_text())
        findings = find_undici_in_lockfile(data)
        assert any(f.location == "dependencies.consumer.dependencies.undici" and f.vulnerable for f in findings)

    def test_scan_paths_with_fixture_directory(self, tmp_path: Path) -> None:
        """Combine manifest and lockfile scans from a temp directory.

        Args:
            tmp_path: Pytest-provided temporary directory.
        """
        pkg = tmp_path / "package.json"
        pkg.write_text((FIXTURES / "package_exact_vuln.json").read_text())
        lock = tmp_path / "package-lock.json"
        lock.write_text((FIXTURES / "package-lock_vuln.json").read_text())
        findings = scan_paths(pkg)
        assert any(f.vulnerable for f in findings)
