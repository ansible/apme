"""Convert Ansible Galaxy tarballs into PEP 427 Python wheels."""

from __future__ import annotations

import io
import json
import logging
import tarfile
import zipfile
from typing import TYPE_CHECKING, Any

import yaml

from apme_engine.config_env import get_env_int
from galaxy_proxy.metadata import (
    galaxy_to_metadata_with_python_deps,
    generate_record,
    generate_top_level,
    generate_wheel_file,
    sha256_digest,
)
from galaxy_proxy.naming import dist_info_dirname, wheel_filename

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

GALAXY_META_FILES = {"FILES.json"}

# Aggregate extraction caps (fail-fast against decompression bombs). The
# tarball itself travels via ansible-galaxy download; this guards the
# extracted byte/file totals held in RAM during conversion.
_TARBALL_MAX_BYTES_DEFAULT = 256 * 1024 * 1024  # APME_GALAXY_TARBALL_MAX_BYTES (256MiB)
_TARBALL_MAX_FILES_DEFAULT = 10000  # APME_GALAXY_TARBALL_MAX_FILES
_TARBALL_MAX_FILE_BYTES_DEFAULT = 64 * 1024 * 1024  # APME_GALAXY_TARBALL_MAX_FILE_BYTES (64MiB)


def _tarball_max_bytes() -> int:
    """Return maximum aggregate extracted bytes per tarball.

    Returns:
        Byte cap from ``APME_GALAXY_TARBALL_MAX_BYTES``.
    """
    return get_env_int(
        "APME_GALAXY_TARBALL_MAX_BYTES",
        _TARBALL_MAX_BYTES_DEFAULT,
        min_value=1,
    )


def _tarball_max_files() -> int:
    """Return maximum extracted files per tarball.

    Returns:
        File-count cap from ``APME_GALAXY_TARBALL_MAX_FILES``.
    """
    return get_env_int(
        "APME_GALAXY_TARBALL_MAX_FILES",
        _TARBALL_MAX_FILES_DEFAULT,
        min_value=1,
    )


def _tarball_max_file_bytes() -> int:
    """Return maximum bytes for a single file inside a tarball.

    Returns:
        Per-file cap from ``APME_GALAXY_TARBALL_MAX_FILE_BYTES``.
    """
    return get_env_int(
        "APME_GALAXY_TARBALL_MAX_FILE_BYTES",
        _TARBALL_MAX_FILE_BYTES_DEFAULT,
        min_value=1,
    )


def tarball_to_wheel(tarball_data: bytes) -> tuple[str, bytes]:
    """Convert a Galaxy collection tarball to a Python wheel.

    Args:
        tarball_data: Raw bytes of the .tar.gz archive.

    Returns:
        A tuple of (wheel_filename, wheel_bytes).
    """
    galaxy, contents = _extract_tarball(tarball_data)

    namespace = galaxy["namespace"]
    name = galaxy["name"]
    version = galaxy["version"]

    req_txt_bytes = contents.pop("requirements.txt", None)
    req_txt = req_txt_bytes.decode("utf-8", errors="replace") if req_txt_bytes else None

    metadata_content = galaxy_to_metadata_with_python_deps(galaxy, req_txt)
    wheel_content = generate_wheel_file()
    top_level_content = generate_top_level(namespace)

    dist_info = dist_info_dirname(namespace, name, version)
    collection_prefix = f"ansible_collections/{namespace}/{name}"

    record_entries: list[tuple[str, str, int]] = []
    wheel_buf = io.BytesIO()

    with zipfile.ZipFile(wheel_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for relative_path, data in sorted(contents.items()):
            arc_path = f"{collection_prefix}/{relative_path}"
            zf.writestr(arc_path, data)
            record_entries.append((arc_path, sha256_digest(data), len(data)))

        for meta_name, meta_content in [
            ("METADATA", metadata_content.encode()),
            ("WHEEL", wheel_content.encode()),
            ("top_level.txt", top_level_content.encode()),
        ]:
            arc_path = f"{dist_info}/{meta_name}"
            zf.writestr(arc_path, meta_content)
            record_entries.append((arc_path, sha256_digest(meta_content), len(meta_content)))

        record_path = f"{dist_info}/RECORD"
        record_content = generate_record(record_entries)
        record_content += f"{record_path},,\n"
        zf.writestr(record_path, record_content)

    whl_name = wheel_filename(namespace, name, version)
    return whl_name, wheel_buf.getvalue()


def tarball_to_wheel_file(tarball_path: Path, output_dir: Path) -> Path:
    """Convert a Galaxy tarball file to a wheel file on disk.

    Args:
        tarball_path: Path to the collection ``.tar.gz`` archive.
        output_dir: Directory where the ``.whl`` file will be written.

    Returns:
        Path to the written .whl file.
    """
    tarball_data = tarball_path.read_bytes()
    whl_name, whl_data = tarball_to_wheel(tarball_data)
    output_path = output_dir / whl_name
    output_path.write_bytes(whl_data)
    return output_path


def _extract_tarball(tarball_data: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Extract a Galaxy tarball into metadata and file contents.

    Handles two Galaxy tarball layouts:
      - Flat (real Galaxy): files at root, metadata in MANIFEST.json
      - Prefixed (ansible-galaxy collection build): top-level {ns}-{name}-{ver}/

    Aggregate caps (``APME_GALAXY_TARBALL_MAX_BYTES`` default 256MiB,
    ``APME_GALAXY_TARBALL_MAX_FILES`` default 10000, per-file
    ``APME_GALAXY_TARBALL_MAX_FILE_BYTES`` default 64MiB) fail fast during
    extraction so a decompression bomb cannot balloon RAM. ``FILES.json``
    counts toward the aggregate caps. Members whose declared
    ``TarInfo.size`` exceeds the per-file cap are skipped without reading
    their content but still count toward the aggregate caps, so a flood
    of oversized entries cannot evade the file-count/byte limits.
    Iteration streams members (``for member in tf``) without
    preloading the full member/name list into RAM.

    Args:
        tarball_data: Raw bytes of the ``.tar.gz`` archive.

    Returns:
        A tuple of (galaxy_metadata_dict, {relative_path: bytes}).

    Raises:
        ValueError: When the archive contains neither ``MANIFEST.json`` nor
            ``galaxy.yml`` with extractable collection metadata, or when an
            aggregate extraction cap is exceeded.
    """
    contents: dict[str, bytes] = {}
    galaxy_data: dict[str, Any] | None = None
    has_prefix = False
    max_bytes = _tarball_max_bytes()
    max_files = _tarball_max_files()
    max_file_bytes = _tarball_max_file_bytes()
    total_bytes = 0
    file_count = 0

    def _count_member(size: int) -> None:
        """Account *size* bytes toward aggregate caps (fail-fast).

        Args:
            size: Member byte size to account.

        Raises:
            ValueError: When a file-count or aggregate byte cap is exceeded.
        """
        nonlocal file_count, total_bytes
        file_count += 1
        total_bytes += size
        if file_count > max_files:
            raise ValueError(f"Tarball file limit exceeded: {file_count} files (max {max_files})")
        if total_bytes > max_bytes:
            raise ValueError(f"Tarball size limit exceeded: {total_bytes} bytes (max {max_bytes} bytes)")

    # Detect layout streaming (no getnames()/getmembers() preload):
    # if every entry shares a common {ns}-{name}-{ver}/ prefix and none
    # are bare top-level files, it's the prefixed format. Streams names
    # with O(1) header memory instead of materializing the full list.
    has_prefix = False
    with tarfile.open(fileobj=io.BytesIO(tarball_data), mode="r:gz") as _tf:
        _first_prefix = ""
        _seen_first = False
        for _m in _tf:
            _name = _m.name
            if not _name:
                continue
            if not _seen_first:
                _seen_first = True
                if "/" not in _name:
                    break
                _first_prefix = _name.split("/")[0]
                if not _first_prefix:
                    break
                has_prefix = True
            if _name != _first_prefix and not _name.startswith(_first_prefix + "/"):
                has_prefix = False
                break

    with tarfile.open(fileobj=io.BytesIO(tarball_data), mode="r:gz") as tf:
        # Stream members one-by-one (tarfile iterates without
        # preloading all headers) so a many-member archive cannot
        # balloon RAM via an upfront member list.
        for member in tf:
            if not member.isfile():
                continue

            if has_prefix:
                parts = member.name.split("/", 1)
                relative = parts[1] if len(parts) == 2 and parts[1] else None
            else:
                relative = member.name

            if not relative:
                _count_member(member.size)
                continue

            # Reject unsafe archive paths (traversal, absolute, etc.).
            # Counted toward caps so a traversal-entry flood cannot spin
            # the loop without tripping the file-count limit.
            if ".." in relative.split("/") or relative.startswith("/") or "\\" in relative:
                _count_member(member.size)
                continue

            # Pre-check the declared size before reading content into RAM;
            # oversized members are skipped without a read but still
            # counted toward the aggregate caps (flood protection).
            if member.size > max_file_bytes:
                logger.warning(
                    "Skipping tarball member exceeding per-file cap: %s (%d bytes, max %d)",
                    relative,
                    member.size,
                    max_file_bytes,
                )
                _count_member(member.size)
                continue

            basename = relative.rsplit("/", 1)[-1]

            if basename in GALAXY_META_FILES:
                # FILES.json is Galaxy metadata, not wheel content — but it
                # still counts toward the aggregate caps.
                _count_member(member.size)
                continue
            data = tf.extractfile(member)
            if data is None:
                continue
            # Bound the read itself: a lying declared size could otherwise
            # stream unbounded bytes into RAM before the cap check below.
            file_bytes = data.read(max_file_bytes + 1)
            if len(file_bytes) > max_file_bytes:
                logger.warning(
                    "Skipping tarball member exceeding per-file cap: %s (%d bytes, max %d)",
                    relative,
                    len(file_bytes),
                    max_file_bytes,
                )
                _count_member(len(file_bytes))
                continue

            _count_member(len(file_bytes))

            if relative == "MANIFEST.json":
                manifest = json.loads(file_bytes)
                galaxy_data = manifest.get("collection_info", {})
                contents[relative] = file_bytes
            elif relative == "galaxy.yml":
                if galaxy_data is None:
                    galaxy_data = yaml.safe_load(file_bytes)
                contents[relative] = file_bytes
            else:
                contents[relative] = file_bytes

    if galaxy_data is None:
        msg = "Tarball contains neither MANIFEST.json nor galaxy.yml — cannot extract collection metadata"
        raise ValueError(msg)

    return galaxy_data, contents
