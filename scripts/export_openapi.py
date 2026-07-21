#!/usr/bin/env python3
"""Export or check the committed Gateway OpenAPI v1 artifact.

Generates the schema from ``create_app().openapi()`` (no running server)
and writes a stable JSON document for Portal / Backstage consumers
(ADR-060).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = REPO_ROOT / "docs" / "api" / "openapi.v1.json"


def _canonical_openapi_json() -> str:
    """Return the OpenAPI schema as canonical JSON text.

    Returns:
        Pretty-printed JSON with sorted keys and a trailing newline.
    """
    from apme_gateway.app import create_app

    schema = create_app().openapi()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def export_openapi(*, check: bool) -> int:
    """Write or verify ``docs/api/openapi.v1.json``.

    Args:
        check: When True, compare against the committed file and exit 1
            on drift without writing.

    Returns:
        Process exit code (0 success, 1 check failure).
    """
    generated = _canonical_openapi_json()
    if check:
        if not ARTIFACT.is_file():
            print(
                f"Missing {ARTIFACT.relative_to(REPO_ROOT)}; run: tox -e openapi",
                file=sys.stderr,
            )
            return 1
        current = ARTIFACT.read_text(encoding="utf-8")
        if current != generated:
            print(
                f"{ARTIFACT.relative_to(REPO_ROOT)} is out of date. Regenerate with: tox -e openapi",
                file=sys.stderr,
            )
            return 1
        print(f"{ARTIFACT.relative_to(REPO_ROOT)} is up to date.")
        return 0

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="openapi.v1.",
        suffix=".json",
        dir=ARTIFACT.parent,
        delete=False,
    ) as tmp:
        tmp.write(generated)
        tmp_path = Path(tmp.name)
    tmp_path.replace(ARTIFACT)
    print(f"Wrote {ARTIFACT.relative_to(REPO_ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if the committed OpenAPI artifact is stale (do not write).",
    )
    args = parser.parse_args(argv)
    return export_openapi(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
