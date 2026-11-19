"""Convert Ansible Galaxy tarballs into PEP 427 Python wheels."""

from __future__ import annotations

import gzip
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

# ``tarfile`` buffers an ``x``/``g``/``L``/``K`` header payload at the size the
# header declares, *before* any member-level cap can run. Real PAX/GNU headers
# only carry a path or a few attributes, so 1 MiB is far above legitimate use
# and far below an allocation an attacker can use to exhaust memory.
_MAX_EXTENSION_HEADER_BYTES = 1024 * 1024
_EXTENSION_HEADER_TYPES = frozenset({b"x", b"g", b"L", b"K"})
_TAR_BLOCK = 512

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


def _header_declared_size(header: bytes) -> int | None:
    """Decode the size field of a raw tar header block.

    Args:
        header: One 512-byte header block.

    Returns:
        Declared payload size, or ``None`` when the field is unparseable
        (a corrupt archive that ``tarfile`` rejects on its own).
    """
    raw = header[124:136].rstrip(b"\0 ")
    if not raw:
        return 0
    if raw[0] & 0x80:
        # GNU base-256 encoding: the high bit is the marker.
        return int.from_bytes(raw, "big") & ((1 << (8 * len(raw) - 1)) - 1)
    try:
        return int(raw, 8)
    except ValueError:
        return None


def _header_checksum_ok(header: bytes) -> bool:
    """Validate a raw tar header checksum the way ``tarfile`` does.

    Args:
        header: One 512-byte header block.

    Returns:
        ``True`` when the stored checksum matches the unsigned or signed sum.
    """
    raw = header[148:156].rstrip(b"\0 ")
    try:
        expected = int(raw, 8)
    except ValueError:
        return False
    unsigned = sum(header[:148]) + 8 * 0x20 + sum(header[156:])
    signed = unsigned - 256 * sum(1 for byte in header if byte > 0x7F)
    return expected in (unsigned, signed)


def _assert_extension_headers_bounded(
    tarball_data: bytes,
    *,
    max_bytes: int,
    max_header_bytes: int = _MAX_EXTENSION_HEADER_BYTES,
) -> None:
    """Reject oversized PAX/GNU extension headers before ``tarfile`` reads them.

    ``tarfile`` consumes ``x``/``g``/``L``/``K`` payloads while iterating, at
    the size the header declares — which happens before any member-level cap
    can inspect the member. This walks the raw header blocks of the
    decompressed stream first and rejects extension headers that declare more
    than *max_header_bytes*, with a flat memory profile (payloads are skipped,
    never materialized).

    The walk shares ``tarfile``'s framing (512-byte blocks, octal/base-256 size
    fields, checksum validation) and stops at the first block ``tarfile`` would
    reject, so it cannot end up checking a different byte stream than the
    parser it protects.

    Args:
        tarball_data: Raw bytes of the gzipped archive.
        max_bytes: Aggregate decompressed byte cap.
        max_header_bytes: Largest extension-header payload allowed.

    Raises:
        ValueError: When the decompressed stream exceeds *max_bytes* or an
            extension header declares more than *max_header_bytes*.
    """
    with gzip.GzipFile(fileobj=io.BytesIO(tarball_data)) as stream:
        total = 0
        while True:
            header = stream.read(_TAR_BLOCK)
            if len(header) < _TAR_BLOCK:
                return
            if header.strip(b"\0") == b"":
                # End-of-archive marker (or padding); nothing else to parse.
                return
            if not _header_checksum_ok(header):
                # ``tarfile`` raises InvalidHeaderError before reading payload.
                return
            total += _TAR_BLOCK
            if total > max_bytes:
                raise ValueError(f"Tarball size limit exceeded: {total} bytes (max {max_bytes} bytes)")
            size = _header_declared_size(header)
            if size is None:
                return
            if header[156:157] in _EXTENSION_HEADER_TYPES and size > max_header_bytes:
                raise ValueError(f"Tarball extension header too large: {size} bytes (max {max_header_bytes} bytes)")
            padded = (size + _TAR_BLOCK - 1) // _TAR_BLOCK * _TAR_BLOCK
            total += padded
            if total > max_bytes:
                raise ValueError(f"Tarball size limit exceeded: {total} bytes (max {max_bytes} bytes)")
            remaining = padded
            while remaining > 0:
                chunk = stream.read(min(remaining, 64 * 1024))
                if not chunk:
                    return
                remaining -= len(chunk)


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
    ``TarInfo.size`` exceeds the per-file cap still count toward the
    aggregate caps (so a flood of oversized entries cannot evade the
    file-count/byte limits) and are rejected with ``ValueError`` — except
    for intentionally excluded Galaxy metadata, which is skipped without
    reading its content.
    The layout scan is capped, and extraction counts every member header,
    including directories and special entries. This bounds ``tarfile``'s
    internal member cache while iterating. PAX/GNU extension-header payloads
    are bounded separately, before ``tarfile`` reads them, since those reads
    happen ahead of every cap above.

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
    # Do this before ``tarfile`` sees the bytes: it buffers PAX/GNU extension
    # payloads at their declared size while iterating, ahead of every cap below.
    _assert_extension_headers_bounded(tarball_data, max_bytes=max_bytes)
    total_bytes = 0
    member_count = 0

    def _count_member() -> None:
        """Account one archive header toward the member-count cap.

        Raises:
            ValueError: When the member-count cap is exceeded.
        """
        nonlocal member_count
        member_count += 1
        if member_count > max_files:
            raise ValueError(f"Tarball file limit exceeded: {member_count} members (max {max_files})")

    def _count_bytes(size: int) -> None:
        """Account *size* bytes toward the aggregate byte cap (fail-fast).

        Args:
            size: Member bytes to account.

        Raises:
            ValueError: When the aggregate byte cap is exceeded.
        """
        nonlocal total_bytes
        total_bytes += size
        if total_bytes > max_bytes:
            raise ValueError(f"Tarball size limit exceeded: {total_bytes} bytes (max {max_bytes} bytes)")

    # Detect layout without an explicit getnames()/getmembers() preload:
    # if every entry shares a common {ns}-{name}-{ver}/ prefix and none
    # are bare top-level files, it's the prefixed format. TarFile caches
    # headers during iteration, so bound this pass before it can retain an
    # unbounded list of TarInfo objects.
    has_prefix = False
    with tarfile.open(fileobj=io.BytesIO(tarball_data), mode="r:gz") as _tf:
        _first_prefix = ""
        _seen_first = False
        scanned_members = 0
        # Also bound the declared bytes seen in this pass: it stops at the
        # first mismatch, so without a byte bound a tarball of many small
        # members before the mismatch is only capped by the member count,
        # and the real byte cap is not reached until the second pass.
        layout_bytes = 0
        for _m in _tf:
            scanned_members += 1
            if scanned_members > max_files:
                raise ValueError(f"Tarball file limit exceeded: {scanned_members} members (max {max_files})")
            layout_bytes += _m.size
            if layout_bytes > max_bytes:
                raise ValueError(f"Tarball size limit exceeded: {layout_bytes} bytes (max {max_bytes} bytes)")
            _name = _m.name
            if not _name:
                continue
            if not _seen_first:
                _seen_first = True
                if "/" not in _name and not _m.isdir():
                    break
                _first_prefix = _name.split("/")[0]
                if not _first_prefix:
                    break
                has_prefix = True
            if _name != _first_prefix and not _name.startswith(_first_prefix + "/"):
                has_prefix = False
                break

    with tarfile.open(fileobj=io.BytesIO(tarball_data), mode="r:gz") as tf:
        # Count every header: tarfile retains iterated TarInfo objects,
        # and non-file entries must not bypass the same cap.
        for member in tf:
            _count_member()
            if not member.isfile():
                continue

            if has_prefix:
                parts = member.name.split("/", 1)
                relative = parts[1] if len(parts) == 2 and parts[1] else None
            else:
                relative = member.name

            if not relative:
                _count_bytes(member.size)
                continue

            # Reject unsafe archive paths (traversal, absolute, etc.).
            # Counted toward caps so a traversal-entry flood cannot spin
            # the loop without tripping the file-count limit.
            if ".." in relative.split("/") or relative.startswith("/") or "\\" in relative:
                _count_bytes(member.size)
                continue

            # Pre-check the declared size before reading content into RAM.
            # Only intentionally excluded Galaxy metadata may be dropped: an
            # installable member that exceeds the per-file cap fails the
            # conversion instead of being skipped, because the published
            # wheel's RECORD lists just the files present — installation
            # would succeed with a file silently missing and fail later, far
            # from the cause.
            basename = relative.rsplit("/", 1)[-1]
            oversized = member.size > max_file_bytes
            if oversized:
                _count_bytes(member.size)
                if basename in GALAXY_META_FILES:
                    logger.debug(
                        "Skipping oversized Galaxy metadata member: %s (%d bytes, max %d)",
                        relative,
                        member.size,
                        max_file_bytes,
                    )
                    continue
                msg = (
                    f"Tarball member exceeds per-file cap: {relative} ({member.size} bytes, max {max_file_bytes} bytes)"
                )
                raise ValueError(msg)

            if basename in GALAXY_META_FILES:
                # FILES.json is Galaxy metadata, not wheel content — but it
                # still counts toward the aggregate caps.
                _count_bytes(member.size)
                continue
            data = tf.extractfile(member)
            if data is None:
                continue
            # Bound the read itself: a lying declared size could otherwise
            # stream unbounded bytes into RAM before the cap check below.
            file_bytes = data.read(max_file_bytes + 1)
            if len(file_bytes) > max_file_bytes:
                _count_bytes(len(file_bytes))
                msg = (
                    f"Tarball member exceeds per-file cap: {relative} "
                    f"({len(file_bytes)} bytes, max {max_file_bytes} bytes)"
                )
                raise ValueError(msg)

            _count_bytes(len(file_bytes))

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
