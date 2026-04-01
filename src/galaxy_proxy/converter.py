"""Convert Ansible Galaxy tarballs into PEP 427 Python wheels."""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from typing import TYPE_CHECKING, Any

import yaml

from galaxy_proxy.metadata import (
    galaxy_to_metadata,
    generate_record,
    generate_top_level,
    generate_wheel_file,
    sha256_digest,
)
from galaxy_proxy.naming import dist_info_dirname, wheel_filename

if TYPE_CHECKING:
    from pathlib import Path

GALAXY_META_FILES = {"FILES.json"}


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

    contents.pop("requirements.txt", None)

    metadata_content = galaxy_to_metadata(galaxy)
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

    Args:
        tarball_data: Raw bytes of the ``.tar.gz`` archive.

    Returns:
        A tuple of (galaxy_metadata_dict, {relative_path: bytes}).

    Raises:
        ValueError: When the archive contains neither ``MANIFEST.json`` nor
            ``galaxy.yml`` with extractable collection metadata.
    """
    contents: dict[str, bytes] = {}
    galaxy_data: dict[str, Any] | None = None
    has_prefix = False

    with tarfile.open(fileobj=io.BytesIO(tarball_data), mode="r:gz") as tf:
        names = tf.getnames()

        # Detect layout: if every entry shares a common {ns}-{name}-{ver}/ prefix
        # and none are bare top-level files, it's the prefixed format.
        if names and "/" in names[0]:
            first_prefix = names[0].split("/")[0]
            has_prefix = all(n == first_prefix or n.startswith(first_prefix + "/") for n in names if n)

        for member in tf.getmembers():
            if not member.isfile():
                continue

            if has_prefix:
                parts = member.name.split("/", 1)
                relative = parts[1] if len(parts) == 2 and parts[1] else None
            else:
                relative = member.name

            if not relative:
                continue

            # Reject unsafe archive paths (traversal, absolute, etc.)
            if ".." in relative.split("/") or relative.startswith("/") or "\\" in relative:
                continue

            data = tf.extractfile(member)
            if data is None:
                continue
            file_bytes = data.read()

            basename = relative.rsplit("/", 1)[-1]

            if relative == "MANIFEST.json":
                manifest = json.loads(file_bytes)
                galaxy_data = manifest.get("collection_info", {})
                contents[relative] = file_bytes
            elif relative == "galaxy.yml":
                if galaxy_data is None:
                    galaxy_data = yaml.safe_load(file_bytes)
                contents[relative] = file_bytes
            elif basename in GALAXY_META_FILES:
                continue
            else:
                contents[relative] = file_bytes

    if galaxy_data is None:
        msg = "Tarball contains neither MANIFEST.json nor galaxy.yml — cannot extract collection metadata"
        raise ValueError(msg)

    return galaxy_data, contents
