"""``apme sbom`` — Gateway SBOM or local ``--path`` SBOM (additive)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from apme_engine.cli.gateway_client import GatewayClient


def run_sbom(args: argparse.Namespace) -> None:
    """Retrieve and display SBOM for a project.

    Two modes (CLI-local XOR Gateway-managed):
    - Gateway (default): ``apme sbom PROJECT_ID`` via REST.
    - Local (``--path``): build a CycloneDX SBOM from local manifests
      (galaxy/requirements files + ansible-core pin) without a
      registered project. Recipe alternative: register -> scan -> sbom.

    Args:
        args: Parsed CLI arguments with ``project_id``, ``format``,
            ``output``, ``gateway_url``, and ``path`` attributes.
    """
    local_path = getattr(args, "path", None)
    if local_path:
        bom = _build_local_sbom(Path(local_path))
        payload = json.dumps(bom, indent=2)
        if args.output:
            Path(args.output).write_text(payload + "\n", encoding="utf-8")
            print(f"SBOM written to {args.output}", file=sys.stderr)
        else:
            print(payload)
        return
    if not getattr(args, "project_id", None):
        print("Error: PROJECT_ID is required without --path (or use --path <local>)", file=sys.stderr)
        sys.exit(2)
    client = GatewayClient(base_url=args.gateway_url)
    try:
        bom = client.get_sbom(args.project_id, format=args.format)
    except httpx.HTTPStatusError as exc:
        print(
            f"Error: {exc.response.status_code} — {exc.response.text}",
            file=sys.stderr,
        )
        sys.exit(1)
    except httpx.RequestError:
        print(
            f"Error: could not connect to Gateway at {client.base_url} — is it running?",
            file=sys.stderr,
        )
        sys.exit(1)

    payload = json.dumps(bom, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(f"SBOM written to {args.output}", file=sys.stderr)
    else:
        print(payload)


def _build_local_sbom(project_path: Path) -> dict[str, object]:
    """Build a minimal CycloneDX SBOM from local manifests (additive).

    Manifest-derived only (not the installed session venv): reads Galaxy
    collection requirements (``requirements.yml``, ``requirements.yaml``,
    ``collections/requirements.yml``) and Python requirements
    (``requirements.txt``, ``requirements/base.txt``) plus an
    ``ansible-core`` pin from ``requirements.txt``. ``galaxy.yml`` and
    ``.apme`` config are not consulted. No Engine or Gateway needed.
    Exits with code 2 (via ``sys.exit``) when *project_path* does not
    exist or is not a directory — a file path is rejected instead of
    silently scanning its parent.

    Args:
        project_path: Local project directory (must exist and be a directory).

    Returns:
        CycloneDX 1.5 JSON-compatible dict labeled manifest-derived via a
        top-level ``properties`` entry (``apme:sbom-source``).
    """
    import re
    from datetime import UTC, datetime

    try:
        from apme_engine.engine._version import __version__ as _engine_version
    except ImportError:
        _engine_version = "0.1.0"

    if not project_path.exists():
        print(f"Error: --path does not exist: {project_path}", file=sys.stderr)
        sys.exit(2)
    if not project_path.is_dir():
        print(
            f"Error: --path must be a directory, got file: {project_path} (pass its containing directory instead)",
            file=sys.stderr,
        )
        sys.exit(2)
    root = project_path
    components: list[dict[str, object]] = []
    seen_refs: set[str] = set()

    def _add_collection(name: str, version: str, source: str | None = None) -> None:
        repo_url = (source or "").strip() or "https://galaxy.ansible.com"
        purl = f"pkg:generic/{name}@{version}?repository_url={repo_url}"
        if purl in seen_refs:
            return
        seen_refs.add(purl)
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": purl,
                "bom-ref": purl,
            }
        )

    def _add_package(name: str, version: str) -> None:
        norm = re.sub(r"[-_.]+", "-", name).lower()
        purl = f"pkg:pypi/{norm}@{version}" if version else f"pkg:pypi/{norm}"
        if purl in seen_refs:
            return
        seen_refs.add(purl)
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": purl,
                "bom-ref": purl,
            }
        )

    # Galaxy collections from requirements.yml files (best-effort YAML parse).
    for candidate in (
        root / "requirements.yml",
        root / "requirements.yaml",
        root / "collections" / "requirements.yml",
    ):
        if not candidate.is_file():
            continue
        try:
            import yaml  # noqa: PLC0415

            data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = data.get("collections", []) if isinstance(data, dict) else []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, str):
                _add_collection(item, "unknown")
            elif isinstance(item, dict) and item.get("name"):
                _add_collection(
                    str(item["name"]),
                    str(item.get("version", "unknown")),
                    str(item["source"]) if item.get("source") else None,
                )

    # Python requirements (ansible-core pin doubles as core component).
    ansible_core_version = ""
    for candidate in (root / "requirements.txt", root / "requirements" / "base.txt"):
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"^([A-Za-z0-9_.\-]+)\s*==\s*([^;\s]+)", line)
            if not match:
                continue
            name, version = match.group(1), match.group(2)
            if name.lower() == "ansible-core":
                # The framework component below owns the ansible-core pin;
                # skipping the generic library entry keeps it singular (#4).
                ansible_core_version = version
                continue
            _add_package(name, version)

    if ansible_core_version:
        core_purl = f"pkg:pypi/ansible-core@{ansible_core_version}"
        if core_purl not in seen_refs:
            seen_refs.add(core_purl)
            components.insert(
                0,
                {
                    "type": "framework",
                    "name": "ansible-core",
                    "version": ansible_core_version,
                    "purl": core_purl,
                    "bom-ref": core_purl,
                },
            )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "tools": {"components": [{"type": "application", "name": "apme", "version": _engine_version}]},
        },
        "properties": [
            {
                "name": "apme:sbom-source",
                "value": "manifest-derived (requirements files only; not the installed session venv)",
            }
        ],
        "components": components,
    }
