"""Translate galaxy.yml metadata into Python wheel metadata files."""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from typing import TYPE_CHECKING, Any

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from galaxy_proxy.naming import fqcn_to_python

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)
_COLLECTION_NAME = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*")
_PRERELEASE_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    rf"(?:-{_PRERELEASE_IDENTIFIER}(?:\.{_PRERELEASE_IDENTIFIER})*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def normalize_source_collection_metadata(metadata: object, fqcn: str) -> dict[str, Any]:
    """Validate fields from a checkout before they become wheel headers.

    Args:
        metadata: Parsed collection metadata from the source checkout.
        fqcn: Expected collection identity in the dependency upload.

    Returns:
        Collection identity, version and dependency constraints for the wheel builder.

    Raises:
        ValueError: Invalid collection metadata or dependency constraints.
    """
    try:
        if not _COLLECTION_NAME.fullmatch(fqcn) or not isinstance(metadata, dict):
            raise ValueError("Invalid collection metadata")
        namespace, name = fqcn.split(".")
        if metadata["namespace"] != namespace or metadata["name"] != name:
            raise ValueError("Metadata identity does not match snapshot layout")
        raw_version = metadata["version"]
        if not isinstance(raw_version, str) or not _SEMVER.fullmatch(raw_version):
            raise ValueError("Collection version must use SemVer")
        version = Version(raw_version)
        if "-" in raw_version.partition("+")[0] and not version.is_prerelease:
            raise ValueError("A SemVer prerelease must remain a prerelease in wheel metadata")
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Git collection {fqcn} must have matching metadata and a wheel-compatible version") from None
    try:
        dependencies: dict[str, str] = {}
        raw_dependencies = metadata.get("dependencies")
        if raw_dependencies is not None and not isinstance(raw_dependencies, dict):
            raise ValueError("Collection dependencies must be a mapping")
        for dep, constraint in (raw_dependencies or {}).items():
            if not isinstance(dep, str) or not _COLLECTION_NAME.fullmatch(dep) or not isinstance(constraint, str):
                raise ValueError("Invalid dependency")
            constraint = constraint.strip()
            if not constraint or constraint == "*":
                dependencies[dep] = ""
            else:
                constraint = re.sub(r"(^|,)\s*=(?!=)", r"\1==", constraint)
                if constraint[0] not in "<>=!~":
                    constraint = f"=={constraint}"
                specifiers = SpecifierSet(constraint)
                if any(specifier.operator in {"~=", "==="} for specifier in specifiers):
                    raise ValueError("Unsupported Galaxy version operator")
                dependencies[dep] = str(specifiers)
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Invalid metadata or dependency constraints for Git collection {fqcn}") from None
    normalized = {"namespace": namespace, "name": name, "version": str(version), "dependencies": dependencies}
    if (requires_ansible := metadata.get("requires_ansible")) is not None:
        try:
            if not isinstance(requires_ansible, str):
                raise ValueError("Expected a version specifier")
            normalized["requires_ansible"] = str(SpecifierSet(requires_ansible))
        except ValueError:
            raise ValueError(f"Invalid requires_ansible for Git collection {fqcn}") from None
    return normalized


def galaxy_to_metadata(galaxy: dict[str, Any]) -> str:
    """Generate PEP 566 METADATA content from parsed galaxy.yml fields.

    Returns the full text of the METADATA file suitable for inclusion in a
    .dist-info directory.

    Args:
        galaxy: Parsed galaxy.yml fields as a dictionary.

    Returns:
        Full METADATA file contents as a string.
    """
    namespace = galaxy["namespace"]
    name = galaxy["name"]
    version = galaxy["version"]

    lines = [
        "Metadata-Version: 2.1",
        f"Name: ansible-collection-{namespace}-{name}",
        f"Version: {version}",
    ]

    if summary := galaxy.get("description"):
        lines.append(f"Summary: {summary}")

    authors = galaxy.get("authors", [])
    if authors:
        lines.append(f"Author: {authors[0]}")

    if license_val := galaxy.get("license"):
        if isinstance(license_val, list):
            license_val = license_val[0] if license_val else ""
        lines.append(f"License: {license_val}")

    if homepage := galaxy.get("homepage") or galaxy.get("repository"):
        lines.append(f"Home-page: {homepage}")

    lines.append("Requires-Python: >=3.12")

    if requires_ansible := galaxy.get("requires_ansible"):
        lines.append(f"Requires-Dist: ansible-core{requires_ansible}")

    for dep_fqcn, version_spec in galaxy.get("dependencies", {}).items():
        python_name = fqcn_to_python(dep_fqcn)
        if version_spec and version_spec != "*":
            lines.append(f"Requires-Dist: {python_name}{version_spec}")
        else:
            lines.append(f"Requires-Dist: {python_name}")

    return "\n".join(lines) + "\n"


def galaxy_to_metadata_with_python_deps(
    galaxy: dict[str, Any],
    requirements_txt: str | None = None,
) -> str:
    """Generate METADATA including Python deps from requirements.txt.

    Args:
        galaxy: Parsed galaxy.yml fields as a dictionary.
        requirements_txt: Optional requirements.txt body; non-comment lines
            become ``Requires-Dist`` entries.

    Returns:
        Full METADATA file contents as a string.
    """
    base = galaxy_to_metadata(galaxy)
    if not requirements_txt:
        return base

    from packaging.requirements import InvalidRequirement, Requirement

    extra_lines = []
    for line in requirements_txt.splitlines():
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        if "://" in line or line.startswith((".", "/")):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            logger.debug("Skipping non-PEP-508 collection requirement")
            continue
        if requirement.url is None:
            extra_lines.append(f"Requires-Dist: {line}")

    if extra_lines:
        return base + "\n".join(extra_lines) + "\n"
    return base


def generate_wheel_file() -> str:
    """Generate the static WHEEL metadata file.

    Returns:
        WHEEL file contents as a string.
    """
    return "Wheel-Version: 1.0\nGenerator: ansible-collection-proxy\nRoot-Is-Purelib: true\nTag: py3-none-any\n"


def generate_top_level(namespace: str) -> str:
    """Generate top_level.txt listing the top-level package.

    Args:
        namespace: Collection namespace (included for API symmetry with callers).

    Returns:
        Contents for ``top_level.txt`` (the static top-level package name line).
    """
    return "ansible_collections\n"


def generate_record(file_entries: list[tuple[str, str, int]]) -> str:
    """Generate RECORD content from a list of (path, sha256_digest, size) tuples.

    Args:
        file_entries: List of ``(wheel-relative path, SHA256 urlsafe-base64 digest, size)``.

    Returns:
        CSV-formatted RECORD file body as a string.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for path, digest, size in file_entries:
        writer.writerow([path, f"sha256={digest}", str(size)])
    return buf.getvalue()


def sha256_digest(data: bytes) -> str:
    """Return urlsafe-base64 (no padding) SHA256 digest of raw bytes.

    Args:
        data: Raw bytes to hash.

    Returns:
        URL-safe base64 SHA256 digest string (no padding), per PEP 427.
    """
    import base64

    raw = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sha256_file(path: Path) -> str:
    """Return urlsafe-base64 (no padding) SHA256 digest of a file.

    Args:
        path: Path to the file to read and hash.

    Returns:
        URL-safe base64 SHA256 digest string (no padding), per PEP 427.
    """
    import base64

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return base64.urlsafe_b64encode(h.digest()).rstrip(b"=").decode("ascii")


def sha256_file_hex(path: Path) -> str:
    """Return hex-encoded SHA256 digest of a file (PEP 503 URL fragments).

    Args:
        path: Path to the file to read and hash.

    Returns:
        Lowercase hex SHA256 digest string, per PEP 503.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
