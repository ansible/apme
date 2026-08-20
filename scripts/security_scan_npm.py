#!/usr/bin/env python3
"""Semver-aware npm dependency checks for the security-scan skill.

Parses ``package.json`` ranges and ``package-lock.json`` resolved versions
instead of relying on exact-version grep patterns alone.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version

UNDICI_VULN_MIN = Version("8.0.0")
UNDICI_VULN_MAX_EXCLUSIVE = Version("8.9.0")


@dataclass(frozen=True)
class Finding:
    """A vulnerable or reviewed npm dependency occurrence.

    Attributes:
        package: npm package name.
        location: Manifest section or lockfile path.
        version_or_range: Declared range or resolved version.
        vulnerable: Whether the entry matches the advisory.
    """

    package: str
    location: str
    version_or_range: str
    vulnerable: bool


def _version_in_undici_vuln_range(version_str: str) -> bool:
    try:
        version = Version(version_str)
    except InvalidVersion:
        return False
    return UNDICI_VULN_MIN <= version < UNDICI_VULN_MAX_EXCLUSIVE


_COMPARATOR_OPS = ("<=", ">=", "<", ">")


def _parse_comparator_token(token: str) -> tuple[str, Version] | None:
    """Parse a single npm comparator token such as ``<=8.0.0``.

    Args:
        token: Comparator token from a space-separated range.

    Returns:
        Operator and bound version, or ``None`` when parsing fails.
    """
    for op in _COMPARATOR_OPS:
        if token.startswith(op):
            try:
                return op, Version(token[len(op) :].strip())
            except InvalidVersion:
                return None
    return None


def _version_satisfies_comparators(version: Version, comparators: list[tuple[str, Version]]) -> bool:
    """Return True when *version* satisfies every comparator in *comparators*.

    Args:
        version: Candidate semver version.
        comparators: Parsed npm comparator tokens.

    Returns:
        True when *version* satisfies all bounds.
    """
    for op, bound in comparators:
        if op == ">=" and version < bound:
            return False
        if op == ">" and version <= bound:
            return False
        if op == "<=" and version > bound:
            return False
        if op == "<" and version >= bound:
            return False
    return True


def _merge_lower_bound(
    current: tuple[Version, bool] | None,
    candidate: tuple[Version, bool],
) -> tuple[Version, bool]:
    """Return the stricter lower semver bound.

    Args:
        current: Existing lower bound, if any.
        candidate: Candidate lower bound to merge.

    Returns:
        The stricter of *current* and *candidate*.
    """
    if current is None:
        return candidate
    current_version, current_inclusive = current
    candidate_version, candidate_inclusive = candidate
    if candidate_version > current_version:
        return candidate
    if candidate_version < current_version:
        return current
    if current_inclusive and not candidate_inclusive:
        return candidate
    return current


def _merge_upper_bound(
    current: tuple[Version, bool] | None,
    candidate: tuple[Version, bool],
) -> tuple[Version, bool]:
    """Return the stricter upper semver bound.

    Args:
        current: Existing upper bound, if any.
        candidate: Candidate upper bound to merge.

    Returns:
        The stricter of *current* and *candidate*.
    """
    if current is None:
        return candidate
    current_version, current_inclusive = current
    candidate_version, candidate_inclusive = candidate
    if candidate_version < current_version:
        return candidate
    if candidate_version > current_version:
        return current
    if current_inclusive and not candidate_inclusive:
        return candidate
    return current


def _bounded_range_nonempty(lower: tuple[Version, bool], upper: tuple[Version, bool]) -> bool:
    """Return True when an inclusive/exclusive semver interval is non-empty.

    Args:
        lower: Lower bound version and inclusivity flag.
        upper: Upper bound version and inclusivity flag.

    Returns:
        True when at least one version satisfies both bounds.
    """
    lower_version, lower_inclusive = lower
    upper_version, upper_inclusive = upper
    if lower_version < upper_version:
        return True
    if lower_version > upper_version:
        return False
    return lower_inclusive and upper_inclusive


def _comparator_range_overlaps_vulnerable(comparators: list[tuple[str, Version]]) -> bool:
    """Return True when comparator bounds overlap the undici advisory range.

    Args:
        comparators: Parsed npm comparator tokens.

    Returns:
        True when the comparator range intersects ``>=8.0.0,<8.9.0``.
    """
    lower: tuple[Version, bool] | None = None
    upper: tuple[Version, bool] | None = None
    for op, bound in comparators:
        if op == ">=":
            lower = _merge_lower_bound(lower, (bound, True))
        elif op == ">":
            lower = _merge_lower_bound(lower, (bound, False))
        elif op == "<=":
            upper = _merge_upper_bound(upper, (bound, True))
        elif op == "<":
            upper = _merge_upper_bound(upper, (bound, False))

    effective_lower = _merge_lower_bound((UNDICI_VULN_MIN, True), lower) if lower else (UNDICI_VULN_MIN, True)
    effective_upper = (
        _merge_upper_bound((UNDICI_VULN_MAX_EXCLUSIVE, False), upper) if upper else (UNDICI_VULN_MAX_EXCLUSIVE, False)
    )
    return _bounded_range_nonempty(effective_lower, effective_upper)


def _compound_comparator_range_may_be_vulnerable(spec: str) -> bool | None:
    """Evaluate space-separated comparator sets in any order.

    Args:
        spec: Normalized npm range string.

    Returns:
        Vulnerability result when *spec* is a compound comparator set, else ``None``.
    """
    parts = spec.split()
    if len(parts) < 2:
        return None
    comparators: list[tuple[str, Version]] = []
    for part in parts:
        parsed = _parse_comparator_token(part)
        if parsed is None:
            return None
        comparators.append(parsed)
    return _comparator_range_overlaps_vulnerable(comparators)


def _npm_x_range_may_be_vulnerable(prefix: str) -> bool:
    """Return True for npm ``8.x`` / ``8.7.x`` style ranges overlapping the advisory.

    Args:
        prefix: Numeric prefix before the ``.x`` suffix (e.g. ``8`` or ``8.7``).

    Returns:
        True when the wildcard range may include vulnerable 8.x releases.
    """
    parts = prefix.split(".")
    if not parts or parts[0] != "8":
        return False
    if len(parts) == 1:
        return True
    try:
        minor = int(parts[1])
    except ValueError:
        return False
    return minor < UNDICI_VULN_MAX_EXCLUSIVE.minor


def _extract_npm_alias_spec(spec: str) -> str:
    """Extract the embedded range from npm alias specs such as ``npm:undici@8.7.0``.

    Args:
        spec: Raw range string from a manifest.

    Returns:
        Embedded semver range when *spec* is an npm alias, else *spec* unchanged.
    """
    if not spec.startswith("npm:"):
        return spec
    alias_body = spec[4:]
    if "@" not in alias_body:
        return spec
    return alias_body.rsplit("@", 1)[-1]


def _normalize_npm_range_spec(spec: str) -> str:
    """Normalize npm range syntax before vulnerability evaluation.

    Args:
        spec: Raw range string from a manifest.

    Returns:
        Normalized range string (empty input becomes ``*``).
    """
    spec = spec.strip()
    if not spec:
        return "*"
    if spec.startswith("="):
        spec = spec[1:].strip()
    if spec and spec[0].isdigit() and ("X" in spec or "*" in spec or "x" in spec):
        parts = [("x" if part in ("x", "X", "*") else part) for part in spec.split(".")]
        while len(parts) > 1 and parts[-1] == "x" and parts[-2] == "x":
            parts.pop()
        spec = ".".join(parts)
    return spec


def _npm_range_may_resolve_to_vulnerable(range_str: str) -> bool:
    """Return True when an npm range could resolve to vulnerable undici 8.x.

    Args:
        range_str: npm semver range or exact version from package.json.

    Returns:
        True when the range may include versions in ``>=8.0.0,<8.9.0``.
    """
    spec = _normalize_npm_range_spec(_extract_npm_alias_spec(range_str))

    if spec in ("*", "X", "x"):
        return True

    if "||" in spec:
        return any(_npm_range_may_resolve_to_vulnerable(part.strip()) for part in spec.split("||"))

    if spec.endswith(".*"):
        return _npm_x_range_may_be_vulnerable(spec[:-2])

    if spec.endswith(".x"):
        return _npm_x_range_may_be_vulnerable(spec[:-2])

    if " - " in spec:
        lower_str, upper_str = (part.strip() for part in spec.split(" - ", 1))
        try:
            lower = Version(lower_str)
            upper = Version(upper_str)
        except InvalidVersion:
            return False
        if lower.major > 8 or (lower.major == 8 and lower >= UNDICI_VULN_MAX_EXCLUSIVE):
            return False
        return lower < UNDICI_VULN_MAX_EXCLUSIVE and upper >= UNDICI_VULN_MIN

    if spec[0].isdigit():
        try:
            return _version_in_undici_vuln_range(spec)
        except InvalidVersion:
            return False

    compound = _compound_comparator_range_may_be_vulnerable(spec)
    if compound is not None:
        return compound

    if spec.startswith("<"):
        inclusive = spec.startswith("<=")
        bound = spec[1 + (1 if inclusive else 0) :].strip()
        try:
            upper = Version(bound)
        except InvalidVersion:
            return False
        if inclusive:
            return upper >= UNDICI_VULN_MIN and UNDICI_VULN_MIN < UNDICI_VULN_MAX_EXCLUSIVE
        return upper > UNDICI_VULN_MIN

    if spec.startswith(">") and not spec.startswith(">="):
        parts = spec.split()
        try:
            lower = Version(parts[0][1:].strip())
        except InvalidVersion:
            return False
        if lower.major > 8 or (lower.major == 8 and lower >= UNDICI_VULN_MAX_EXCLUSIVE):
            return False
        if len(parts) >= 2 and parts[1].startswith("<"):
            upper_inclusive = parts[1].startswith("<=")
            upper_bound = parts[1][1 + (1 if upper_inclusive else 0) :].strip()
            try:
                upper = Version(upper_bound)
            except InvalidVersion:
                return False
            if upper_inclusive:
                return lower < UNDICI_VULN_MAX_EXCLUSIVE and upper >= UNDICI_VULN_MIN
            return lower < UNDICI_VULN_MAX_EXCLUSIVE and upper > UNDICI_VULN_MIN
        return lower < UNDICI_VULN_MAX_EXCLUSIVE

    if spec.startswith("^"):
        try:
            base = Version(spec[1:])
        except InvalidVersion:
            return False
        if base.major != 8:
            return False
        return base < UNDICI_VULN_MAX_EXCLUSIVE

    if spec.startswith("~"):
        try:
            base = Version(spec[1:])
        except InvalidVersion:
            return False
        if base.major != 8:
            return False
        return base < UNDICI_VULN_MAX_EXCLUSIVE

    if spec.startswith(">="):
        parts = spec.split()
        try:
            lower = Version(parts[0][2:])
        except InvalidVersion:
            return False
        if lower.major > 8 or (lower.major == 8 and lower >= UNDICI_VULN_MAX_EXCLUSIVE):
            return False
        if len(parts) >= 2 and parts[1].startswith("<"):
            upper_inclusive = parts[1].startswith("<=")
            upper_bound = parts[1][1 + (1 if upper_inclusive else 0) :].strip()
            try:
                upper = Version(upper_bound)
            except InvalidVersion:
                return False
            if upper_inclusive:
                return lower < UNDICI_VULN_MAX_EXCLUSIVE and upper >= UNDICI_VULN_MIN
            return lower < UNDICI_VULN_MAX_EXCLUSIVE and upper > UNDICI_VULN_MIN
        return lower < UNDICI_VULN_MAX_EXCLUSIVE

    return False


def _append_undici_finding(
    findings: list[Finding],
    *,
    location: str,
    version_or_range: str,
) -> None:
    """Append a single undici manifest finding.

    Args:
        findings: Accumulator for discovered undici declarations.
        location: Manifest section or override path.
        version_or_range: Declared npm range or exact version.
    """
    findings.append(
        Finding(
            package="undici",
            location=location,
            version_or_range=version_or_range,
            vulnerable=_npm_range_may_resolve_to_vulnerable(version_or_range),
        )
    )


def _collect_override_undici(overrides: object, location_prefix: str, findings: list[Finding]) -> None:
    """Recursively collect undici pins from npm ``overrides`` sections.

    Args:
        overrides: Parsed overrides object from package.json.
        location_prefix: Dot-separated path prefix for nested entries.
        findings: Accumulator for discovered undici declarations.
    """
    if not isinstance(overrides, dict):
        return
    for key, value in overrides.items():
        location = f"{location_prefix}.{key}"
        if key == "undici" and isinstance(value, (str, int, float)):
            _append_undici_finding(findings, location=location, version_or_range=str(value))
        elif isinstance(value, dict):
            _collect_override_undici(value, location, findings)


def find_undici_in_package_json(data: dict[str, object]) -> list[Finding]:
    """Scan manifest dependency sections for undici declarations.

    Args:
        data: Parsed package.json object.

    Returns:
        Findings for each undici declaration in dependency sections.
    """
    findings: list[Finding] = []
    for section in (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
    ):
        deps = data.get(section, {})
        if not isinstance(deps, dict):
            continue
        value = deps.get("undici")
        if value is None:
            continue
        _append_undici_finding(findings, location=section, version_or_range=str(value))

    overrides = data.get("overrides")
    if overrides is not None:
        _collect_override_undici(overrides, "overrides", findings)

    return findings


def find_undici_in_lockfile(data: dict[str, object]) -> list[Finding]:
    """Scan lockfile resolved package entries for undici versions.

    Args:
        data: Parsed package-lock.json object.

    Returns:
        Findings for each resolved undici entry in the lockfile.
    """
    findings: list[Finding] = []

    packages = data.get("packages", {})
    if isinstance(packages, dict):
        for pkg_path, meta in packages.items():
            if not isinstance(meta, dict):
                continue
            name = meta.get("name")
            if name is None and "node_modules/" in pkg_path:
                name = pkg_path.rsplit("node_modules/", 1)[-1]
            if name != "undici":
                continue
            version = meta.get("version")
            if version is None:
                continue
            version_str = str(version)
            findings.append(
                Finding(
                    package="undici",
                    location=pkg_path,
                    version_or_range=version_str,
                    vulnerable=_version_in_undici_vuln_range(version_str),
                )
            )

    legacy_deps = data.get("dependencies", {})
    if isinstance(legacy_deps, dict):
        _collect_legacy_lockfile_undici(legacy_deps, "dependencies", findings)

    return findings


def _collect_legacy_lockfile_undici(
    deps: dict[str, object],
    location_prefix: str,
    findings: list[Finding],
) -> None:
    """Recursively collect undici versions from lockfile v1 dependency trees.

    Args:
        deps: Lockfile dependencies mapping at the current level.
        location_prefix: Dot-separated path prefix for nested entries.
        findings: Accumulator for discovered undici versions.
    """
    for name, meta in deps.items():
        if not isinstance(meta, dict):
            continue
        location = f"{location_prefix}.{name}"
        if name == "undici" and meta.get("version") is not None:
            version_str = str(meta["version"])
            findings.append(
                Finding(
                    package="undici",
                    location=location,
                    version_or_range=version_str,
                    vulnerable=_version_in_undici_vuln_range(version_str),
                )
            )
        nested = meta.get("dependencies")
        if isinstance(nested, dict):
            _collect_legacy_lockfile_undici(nested, f"{location}.dependencies", findings)


def scan_paths(package_json: Path, package_lock: Path | None = None) -> list[Finding]:
    """Scan manifest and optional lockfile for vulnerable undici.

    Args:
        package_json: Path to package.json.
        package_lock: Optional explicit package-lock.json path.

    Returns:
        Combined findings from the manifest and lockfile scans.
    """
    findings: list[Finding] = []
    pkg_data = json.loads(package_json.read_text(encoding="utf-8"))
    findings.extend(find_undici_in_package_json(pkg_data))

    lock_path = package_lock or package_json.parent / "package-lock.json"
    if lock_path.is_file():
        lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
        findings.extend(find_undici_in_lockfile(lock_data))

    return findings


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code: 0 when no vulnerable undici is found, else 1.
    """
    parser = argparse.ArgumentParser(description="Scan npm manifests for vulnerable undici.")
    parser.add_argument(
        "package_json",
        type=Path,
        help="Path to package.json (lockfile resolved from the same directory).",
    )
    parser.add_argument(
        "--package-lock",
        type=Path,
        default=None,
        help="Optional explicit package-lock.json path.",
    )
    args = parser.parse_args(argv)

    findings = scan_paths(args.package_json, args.package_lock)
    vulnerable = [f for f in findings if f.vulnerable]
    if not vulnerable:
        return 0

    for finding in vulnerable:
        print(f"VULN {finding.package} {finding.version_or_range} at {finding.location} in {args.package_json.parent}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
