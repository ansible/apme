"""``apme sbom`` — Gateway SBOM or local ``--path`` SBOM (additive)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
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
    from urllib.parse import quote

    from packaging.requirements import InvalidRequirement, Requirement

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
        # A requirement range is not an installed version. Keep exact pins as
        # component versions, and represent ranges/other expressions as unknown.
        if version.startswith("=="):
            version = version[2:].strip() or "unknown"
        elif re.search(r"[<>=!~,|*]", version):
            version = "unknown"
        encoded_name = quote(name, safe="._-")
        encoded_version = quote(version, safe="._-+!")
        repo_url = (source or "").strip() or "https://galaxy.ansible.com"
        encoded_repo_url = quote(repo_url, safe=":")
        purl = f"pkg:generic/{encoded_name}@{encoded_version}?repository_url={encoded_repo_url}"
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
                name, _, version = item.strip().partition(":")
                _add_collection(name.strip(), version.strip() or "unknown")
            elif isinstance(item, dict) and item.get("name"):
                raw_version = item.get("version")
                _add_collection(
                    str(item["name"]),
                    str(raw_version).strip() if raw_version not in (None, "") else "unknown",
                    str(item["source"]) if item.get("source") else None,
                )

    # Python requirements (ansible-core exact pin doubles as core component).
    ansible_core_version = ""
    requirements_root = root.resolve()
    visited_requirement_files: set[Path] = set()
    requirement_bytes_read = 0
    max_requirement_files = 64
    max_requirement_depth = 16
    max_requirement_file_bytes = 2 * 1024 * 1024
    max_requirement_total_bytes = 8 * 1024 * 1024

    def _iter_requirement_lines(path: Path, depth: int = 0) -> Iterator[str]:
        """Read bounded local requirement files and their relative includes.

        Args:
            path: Requirement file or include path.
            depth: Current include nesting depth.

        Yields:
            str: Non-empty requirement lines from files under the project root.
        """
        nonlocal requirement_bytes_read
        try:
            resolved = path.resolve()
            resolved.relative_to(requirements_root)
        except (OSError, ValueError):
            return
        if (
            depth > max_requirement_depth
            or resolved in visited_requirement_files
            or len(visited_requirement_files) >= max_requirement_files
        ):
            return
        try:
            read_limit = min(max_requirement_file_bytes, max_requirement_total_bytes - requirement_bytes_read)
            if read_limit <= 0:
                return
            with resolved.open("rb") as source:
                content = source.read(read_limit + 1)
            if len(content) > read_limit:
                return
            text = content.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return
        visited_requirement_files.add(resolved)
        requirement_bytes_read += len(content)
        pending_requirement = ""
        for raw_line in text.splitlines():
            line = re.split(r"\s+#", raw_line.strip(), maxsplit=1)[0].strip()
            if not line or line.startswith("#"):
                continue
            if re.match(r"^--hash(?:=|\s)", line):
                # pip-compile emits hashes on continued lines after a
                # requirement. The final hash line closes that requirement.
                if pending_requirement and not line.endswith("\\"):
                    yield pending_requirement.strip()
                    pending_requirement = ""
                continue
            line = re.split(r"\s+--hash(?:=|\s)", line, maxsplit=1)[0].rstrip()
            continued = line.endswith("\\")
            if continued:
                line = line[:-1].rstrip()
            pending_requirement = f"{pending_requirement} {line}".strip()
            if continued:
                continue
            line = pending_requirement
            pending_requirement = ""
            include = re.match(r"^(?:-r|--requirement)(?:\s+|=)?(.+)$", line)
            if include:
                include_path = include.group(1).strip().strip("\"'")
                yield from _iter_requirement_lines(resolved.parent / include_path, depth + 1)
            elif not line.startswith("-"):
                yield line
        if pending_requirement:
            yield pending_requirement.strip()

    for candidate in (root / "requirements.txt", root / "requirements" / "base.txt"):
        for line in _iter_requirement_lines(candidate):
            try:
                requirement = Requirement(line)
            except InvalidRequirement:
                continue
            name = requirement.name
            specifiers = list(requirement.specifier)
            exact_versions = [
                spec.version for spec in specifiers if spec.operator == "==" and not spec.version.endswith(".*")
            ]
            version = exact_versions[0] if len(specifiers) == 1 and len(exact_versions) == 1 else ""
            if name.lower() == "ansible-core" and version:
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
