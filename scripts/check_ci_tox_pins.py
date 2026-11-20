#!/usr/bin/env python3
"""Keep the CI uvx toolchain pins consistent with the project manifest.

``ci/tox-requirements.txt`` pins the tox toolchain for CI ``uvx`` invocations
because ``uvx`` ignores the repository ``uv.lock`` (ADR-047). The same tool is
therefore pinned twice: in the CI requirements file and in the project
manifest resolved into ``uv.lock``. Dependabot manages the CI file through the
``pip /ci`` entry and can bump one side without the other, so this hook makes
the drift fail loudly instead of splitting CI from local tooling.

Invariants:
    - ``tox`` carries the same version in ``ci/tox-requirements.txt``, the
      ``dev`` dependency group, and ``uv.lock``.
    - ``tox-uv`` and ``virtualenv`` are exactly pinned in
      ``ci/tox-requirements.txt`` (neither is a project dependency; ``tox-uv``
      is only needed inside ``uvx``).
    - ``virtualenv`` in ``ci/tox-requirements.txt`` satisfies the
      ``[tool.uv] constraint-dependencies`` floor in ``pyproject.toml``.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_REQUIREMENTS = REPO_ROOT / "ci" / "tox-requirements.txt"
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"
TOOLCHAIN_PACKAGES = ("tox", "tox-uv", "virtualenv")


def parse_ci_pins() -> dict[str, str]:
    """Read exact pins for the tox toolchain from the CI requirements file.

    Exits with a diagnostic when a toolchain package uses a non-exact
    specifier.

    Returns:
        Mapping of package name to pinned version for ``==`` lines.
    """
    pins: dict[str, str] = {}
    for line in CI_REQUIREMENTS.read_text().splitlines():
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        match = re.fullmatch(r"([A-Za-z0-9._-]+)\s*(.*)", text)
        if match is None or not match.group(2):
            continue
        name, spec = match.group(1), match.group(2)
        if name in TOOLCHAIN_PACKAGES and not spec.startswith("=="):
            sys.exit(f"ci-tox-pins: {name} must be pinned exactly (==) in ci/tox-requirements.txt, got {spec!r}")
        if spec.startswith("=="):
            pins[name] = spec[2:]
    return pins


def _load_toml(path: Path) -> dict[str, object]:
    """Parse a TOML file into its top-level mapping.

    Args:
        path: File to parse.

    Returns:
        The parsed top-level mapping.
    """
    with path.open("rb") as handle:
        return tomllib.load(handle)


def project_tox_version(pyproject: dict[str, object]) -> str | None:
    """Return the ``tox`` pin from the ``dev`` dependency group.

    Args:
        pyproject: Parsed ``pyproject.toml`` mapping.

    Returns:
        The pinned version, or ``None`` when ``tox`` is absent or unpinned.
    """
    groups = pyproject.get("dependency-groups", {})
    if not isinstance(groups, dict):
        return None
    requirements = groups.get("dev", [])
    if not isinstance(requirements, list):
        return None
    for requirement in requirements:
        match = re.fullmatch(r"tox==(\S+)", str(requirement).strip())
        if match:
            return match.group(1)
    return None


def locked_tox_version(lock: dict[str, object]) -> str | None:
    """Return the ``tox`` version resolved in ``uv.lock``.

    Args:
        lock: Parsed ``uv.lock`` mapping.

    Returns:
        The locked version, or ``None`` when ``tox`` is not locked.
    """
    packages = lock.get("package", [])
    if not isinstance(packages, list):
        return None
    for package in packages:
        if isinstance(package, dict) and package.get("name") == "tox":
            version = package.get("version")
            if isinstance(version, str):
                return version
    return None


def virtualenv_floor(pyproject: dict[str, object]) -> str | None:
    """Return the ``virtualenv`` constraint floor from ``pyproject.toml``.

    Args:
        pyproject: Parsed ``pyproject.toml`` mapping.

    Returns:
        The floor version, or ``None`` when ``virtualenv`` is unconstrained.
    """
    tool = pyproject.get("tool", {})
    if not isinstance(tool, dict):
        return None
    tool_uv = tool.get("uv", {})
    if not isinstance(tool_uv, dict):
        return None
    constraints = tool_uv.get("constraint-dependencies", [])
    if not isinstance(constraints, list):
        return None
    for constraint in constraints:
        match = re.fullmatch(r"virtualenv>=(\S+)", str(constraint).strip())
        if match:
            return match.group(1)
    return None


def version_tuple(version: str) -> tuple[int, ...]:
    """Convert a dotted numeric version string to a comparable tuple.

    Args:
        version: Dotted numeric version (e.g. ``21.15.9``).

    Returns:
        Tuple of integer components.
    """
    return tuple(int(part) for part in version.split("."))


def main() -> int:
    """Check CI uvx toolchain pin consistency across the three sources.

    Returns:
        0 when all invariants hold, 1 with diagnostics on stderr otherwise.
    """
    pyproject = _load_toml(PYPROJECT)
    lock = _load_toml(UV_LOCK)

    errors: list[str] = []
    pins = parse_ci_pins()
    for name in TOOLCHAIN_PACKAGES:
        if name not in pins:
            errors.append(f"{name} is not pinned in ci/tox-requirements.txt")

    dev_tox = project_tox_version(pyproject)
    if dev_tox is None:
        errors.append("tox is not exactly pinned in the pyproject.toml dev group")

    locked_tox = locked_tox_version(lock)
    if locked_tox is None:
        errors.append("tox is missing from uv.lock")

    for label, value in (("dev group", dev_tox), ("uv.lock", locked_tox)):
        if "tox" in pins and value is not None and pins["tox"] != value:
            errors.append(f"tox pin drift: ci/tox-requirements.txt has {pins['tox']}, {label} has {value}")
    if dev_tox is not None and locked_tox is not None and dev_tox != locked_tox:
        errors.append(f"tox pin drift: dev group has {dev_tox}, uv.lock has {locked_tox}")

    floor = virtualenv_floor(pyproject)
    if floor is not None and "virtualenv" in pins and version_tuple(pins["virtualenv"]) < version_tuple(floor):
        errors.append(f"virtualenv pin {pins['virtualenv']} violates the pyproject.toml constraint >={floor}")

    if errors:
        for error in errors:
            print(f"ci-tox-pins: {error}", file=sys.stderr)
        print(
            "ci-tox-pins: keep ci/tox-requirements.txt, pyproject.toml and uv.lock in sync; "
            "bump the dev group pin and run 'uv lock' together with the CI file.",
            file=sys.stderr,
        )
        return 1

    print(f"ci-tox-pins: OK (tox {pins['tox']}, tox-uv {pins['tox-uv']}, virtualenv {pins['virtualenv']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
