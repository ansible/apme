#!/usr/bin/env python3
"""Render bootc Quadlet files pinned to one APME release's image digests."""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
from pathlib import Path

_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_LINE_RE = re.compile(r"^(Image=)(\S+)([ \t]*)$", re.MULTILINE)


def _digest_for(repository: str, version: str) -> str:
    """Resolve an image's multi-architecture release tag to its manifest digest.

    Args:
        repository: Fully qualified container image repository.
        version: A release version without the leading ``v``.

    Returns:
        The OCI index or manifest digest for ``repository:version``.
    """
    image_ref = f"{repository}:{version}"
    try:
        result = subprocess.run(
            [
                "docker",
                "buildx",
                "imagetools",
                "inspect",
                "--format",
                "{{json .Manifest}}",
                image_ref,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise ValueError(f"failed to inspect {image_ref}: {detail}") from exc
    manifest = json.loads(result.stdout)
    if not isinstance(manifest, dict):
        raise ValueError(f"{image_ref}: registry returned invalid manifest metadata")
    digest = manifest.get("digest", "")
    if not _DIGEST_RE.fullmatch(digest):
        raise ValueError(f"{image_ref}: registry returned invalid digest {digest!r}")
    return digest


def _render_file(source: Path, destination: Path, version: str) -> int:
    """Copy one Quadlet file, resolving each APME image to a digest.

    Args:
        source: Source Quadlet file.
        destination: Output path.
        version: Release version without the leading ``v``.

    Returns:
        Number of image references pinned in the file.
    """
    content = source.read_text(encoding="utf-8")
    replacements = 0

    def pin_image(match: re.Match[str]) -> str:
        nonlocal replacements
        prefix, reference, suffix = match.groups()
        if not reference.startswith("ghcr.io/ansible/apme-"):
            raise ValueError(f"{source}: unexpected non-APME image reference {reference!r}")
        repository = reference.split("@", maxsplit=1)[0].rsplit(":", maxsplit=1)[0]
        digest = _digest_for(repository, version)
        replacements += 1
        print(f"{source.name}: {repository}:{version} -> {digest}")
        return f"{prefix}{repository}@{digest}{suffix}"

    rendered = _IMAGE_LINE_RE.sub(pin_image, content)
    if source.suffix == ".container" and replacements != 1:
        raise ValueError(f"{source}: expected exactly one Image= line, found {replacements}")
    destination.write_text(rendered, encoding="utf-8")
    destination.chmod(stat.S_IMODE(source.stat().st_mode))
    return replacements


def main() -> int:
    """Render release Quadlets into an output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release version (for example 2026.10.0)")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory for the generated Quadlet overlay")
    args = parser.parse_args()
    if not _VERSION_RE.fullmatch(args.version):
        parser.error("--version must be a stable X.Y.Z version without a leading v")

    repo_root = Path(__file__).resolve().parents[2]
    source_dir = repo_root / "deploy" / "bootc" / "quadlet"
    sources = sorted((*source_dir.glob("*.container"), *source_dir.glob("*.pod")))
    if not sources:
        parser.error(f"no Quadlet files found in {source_dir}")

    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("--output-dir must be empty to avoid retaining stale Quadlet files")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        _render_file(source, args.output_dir / source.name, args.version)
    print(f"Pinned {len(sources)} Quadlet files into {args.output_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
