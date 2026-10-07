"""Pure collection-reconcile helpers for session venvs (no locking, no I/O side effects).

Extracted from :mod:`apme_engine.venv_manager.session` so pin/spec fixes land
without touching venv lifecycle, locking, or metrics code. Every function
here is pure and directly unit-testable; ``VenvSessionManager.acquire``
imports them.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_SAFE_VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


_RANGE_PREFIXES = ("==", "!=", ">=", "<=", "~=", "===", ">", "<", "*")


def _spec_to_pip(spec: str) -> str:
    """Convert a collection spec to a pip package name.

    ``community.general:9.0.0`` -> ``ansible-collection-community-general==9.0.0``
    ``ansible.posix``           -> ``ansible-collection-ansible-posix``
    ``community.general:>=1.0.0`` -> ``ansible-collection-community-general>=1.0.0``
    ``community.general:>=1.0.0,<2.0.0`` -> ``ansible-collection-community-general>=1.0.0,<2.0.0``

    Bare pins (``1.2.3``) become ``==1.2.3``; PEP 440 range constraints
    (``>=``, ``>``, ``<``, ``<=``, ``!=``, ``~=``, ``==`` with compound
    ``,`` ranges) are passed through verbatim.  ``"*"`` means any version
    and maps to the bare package name.

    The spec is first validated with Galaxy's
    :func:`galaxy_proxy.collection_downloader.validate_collection_spec`
    (spaces normalized before validation, identically to the download
    path) so option-injection and shell-metachar specs fail here before
    ever reaching pip.

    Args:
        spec: Collection specifier (namespace.collection or namespace.collection:version).

    Returns:
        pip-installable package specifier.

    Raises:
        ValueError: If spec does not contain a dot (expected namespace.collection),
            fails collection-spec validation, or the version constraint is
            not valid PEP 440.
    """
    from galaxy_proxy.collection_downloader import validate_collection_spec  # noqa: PLC0415

    normalized = validate_collection_spec(spec)
    base = normalized.split(":")[0].strip()
    if "." not in base:
        raise ValueError(f"Invalid collection spec (expected namespace.collection): {spec}")
    namespace, collection = base.split(".", 1)
    pkg = f"ansible-collection-{namespace}-{collection}"
    if ":" in normalized:
        version = normalized.split(":", 1)[1].strip().replace(" ", "")
        if not version or version == "*":
            return pkg
        if version.startswith(_RANGE_PREFIXES):
            _validate_pep440_specifier(version, spec)
            return f"{pkg}{version}"
        _validate_pep440_specifier(f"=={version}", spec)
        pkg = f"{pkg}=={version}"
    return pkg


def _validate_pep440_specifier(version_spec: str, original: str) -> None:
    """Validate a PEP 440 version specifier, raising ValueError if invalid.

    Args:
        version_spec: Specifier string such as ``==1.2.3`` or ``>=1.0.0,<2.0.0``.
        original: Original collection spec (for the error message).

    Raises:
        ValueError: If the specifier is not valid PEP 440.
    """
    from packaging.specifiers import SpecifierSet

    try:
        SpecifierSet(version_spec)
    except Exception as exc:
        raise ValueError(f"Invalid collection version constraint {original!r}: {exc}") from exc


def _requirements_hash(collection_specs: list[str]) -> str:
    """Hash the requested collection set for reconcile detection (PE-32).

    The hash is stored on session meta and updated when the requested set
    fully converges. A mismatch means missing collections still need install
    (or a prior install failed and must be retried). Stale specs that are no
    longer requested are retained append-only during acquire and do not block
    hash convergence once installs succeed.

    Args:
        collection_specs: Collection specifiers requested for the venv.

    Returns:
        Short hex digest stable under spec reordering.
    """
    material = "\n".join(sorted(collection_specs))
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _spec_bare_fqcn(spec: str) -> str:
    """Return the namespace.collection portion of a collection spec.

    Args:
        spec: Collection specifier (namespace.collection or namespace.collection:version).

    Returns:
        Bare FQCN without a version pin.
    """
    return spec.split(":")[0].strip()


def _spec_version_pin(spec: str) -> str | None:
    """Return the version pin from a collection spec, if any.

    Args:
        spec: Collection specifier.

    Returns:
        Version string when ``:version`` is present, else ``None``.
    """
    if ":" not in spec:
        return None
    pin = spec.split(":", 1)[1].strip()
    return pin or None


def _installed_satisfies_spec(installed: set[str], spec: str) -> bool:
    """Return whether ``installed`` metadata satisfies a requested spec.

    A different pin for the same bare FQCN does not satisfy a pinned request
    because pip keeps only one version per collection in the venv.

    Args:
        installed: Installed collection specifiers from session meta.
        spec: Requested collection specifier.

    Returns:
        True when ``spec`` is already recorded as satisfied.
    """
    if spec in installed:
        return True
    bare = _spec_bare_fqcn(spec)
    matching = [s for s in installed if _spec_bare_fqcn(s) == bare]
    if not matching:
        return False
    return _spec_version_pin(spec) is None


def _merge_installed_collections(installed: set[str], succeeded: set[str]) -> set[str]:
    """Merge newly installed specs, replacing prior pins for the same bare FQCN.

    Args:
        installed: Previously recorded collection specifiers.
        succeeded: Specifiers installed in the current reconcile round.

    Returns:
        Updated installed set with at most one entry per bare FQCN from
        ``succeeded``.
    """
    bare_from_succeeded = {_spec_bare_fqcn(s) for s in succeeded}
    retained = {s for s in installed if _spec_bare_fqcn(s) not in bare_from_succeeded}
    return retained | succeeded


def _spec_to_bare_pip(spec: str) -> str:
    """Map a collection spec to its bare pip package name (no version pin).

    Args:
        spec: Collection specifier (namespace.collection or namespace.collection:version).

    Returns:
        Bare pip package name for uninstall, e.g. ``ansible-collection-community-general``.
    """
    import re

    pip_spec = _spec_to_pip(spec)
    return re.split(r"[=<>!~]", pip_spec, maxsplit=1)[0]


def _has_valid_meta(version_dir: Path) -> bool:
    """Return whether a sibling version directory holds a readable session meta.

    Orphaned directories from crashed or failed cold builds carry no (or
    unreadable) ``meta.json``; they must not count toward the sibling cap
    (PE-36) or one poisoned index bricks the session after eight failures.

    Args:
        version_dir: Sibling version directory under the session dir.

    Returns:
        True when ``meta.json`` exists and parses as a JSON object.
    """
    meta_path = version_dir / "meta.json"
    if not meta_path.is_file() or meta_path.is_symlink():
        return False
    try:
        return isinstance(json.loads(meta_path.read_text(encoding="utf-8")), dict)
    except (OSError, ValueError):
        return False


def count_sibling_venvs(session_dir: Path, pip_version: str) -> int:
    """Count live sibling venvs, excluding the version being created.

    Skips dotfiles, symlinks (never removed here), non-version directory
    names, and orphaned directories without a valid ``meta.json`` (failed or
    crashed cold builds must not consume cap).

    Args:
        session_dir: Session directory holding per-version subdirectories.
        pip_version: Normalised version about to be created (excluded).

    Returns:
        Number of sibling venvs counting toward the cap.
    """
    siblings = 0
    for child in session_dir.iterdir():
        if (
            not child.is_dir()
            or child.is_symlink()
            or child.name.startswith(".")
            or child.name == pip_version
            or _SAFE_VERSION_RE.match(child.name) is None
            or not _has_valid_meta(child)
        ):
            continue
        siblings += 1
    return siblings


def enforce_sibling_cap(session_dir: Path, pip_version: str, session_id: str, *, max_venvs: int) -> None:
    """Refuse new sibling venvs once the session holds the cap (PE-36).

    Any ``X.Y[.Z]`` client version string spawns a sibling venv; without a
    cap, version-matrix probing grows the session directory without bound.

    Eviction is deliberately *not* automatic: deleting a sibling while
    another in-flight scan's validator is executing inside it would
    corrupt that scan (ENOENT mid-run, blamed on flaky infra), and only
    the manager knows which venvs are in use with no refcounting yet.
    Refusing loudly (naming the cap) bounds growth without risking live
    scans; per-use pinning is a tracked follow-up.

    Args:
        session_dir: Session directory holding per-version subdirectories.
        pip_version: Normalised version about to be created.
        session_id: Session identifier (for log/error messages).
        max_venvs: Maximum sibling venvs allowed (from
            ``APME_SESSION_MAX_VENVS_PER_SESSION``).

    Raises:
        ValueError: If creating the new sibling would exceed the cap.
            Names the cap and the remedy.
    """
    siblings = count_sibling_venvs(session_dir, pip_version)
    if siblings >= max_venvs:
        msg = (
            f"Session {session_id} already holds {siblings} sibling venvs "
            f"(APME_SESSION_MAX_VENVS_PER_SESSION={max_venvs}); refusing to "
            f"create core={pip_version}. Reuse a pinned version, clean the "
            "session, or increase the cap."
        )
        raise ValueError(msg)
