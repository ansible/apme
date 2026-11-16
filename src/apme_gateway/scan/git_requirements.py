"""Resolve Git requirements at the gateway; credentials never reach Engine."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from apme.v1.common_pb2 import File
from apme_engine.git_requirements import REQUIREMENTS_PATHS, git_entries
from apme_gateway.config import GatewayConfig
from apme_gateway.db import get_session
from apme_gateway.db.models import GitRequirementsSettings, Project
from apme_gateway.scm.safe_fetch import git_snapshot
from apme_gateway.scm.tokens import select_usable_scm_token


async def resolve_git_requirements(
    project_id: str, project_root: str, *, scm_token: str | None = None, scm_provider: str | None = None
) -> list[File]:
    """Fetch declared Git dependencies using the project credential policy.

    Args:
        project_id: Owning project UUID.
        project_root: Temporary project clone.
        scm_token: Effective project clone credential, including per-operation credentials.
        scm_provider: Effective project SCM provider.

    Returns:
        Dependency files in collection/role install layout.

    Raises:
        ValueError: Git requirements are disabled or a declaration is invalid.
        RuntimeError: A repository cannot be fetched or exceeds upload bounds.
    """
    declarations = await asyncio.to_thread(_read_declarations, Path(project_root))
    if not declarations:
        return []
    async with get_session() as db:
        policy = await db.get(GitRequirementsSettings, 1)
        if policy is None or not policy.enabled:
            raise ValueError("Git collection/role requirements are disabled in global Settings")
        project = await db.get(Project, project_id)
        if project is None:
            raise ValueError("Project not found while resolving Git requirements")
        token = select_usable_scm_token(
            project.requirements_token, scm_token, project.scm_token, GatewayConfig().scm_token
        )
        project_origin = _origin(project.repo_url)
        project_provider = scm_provider or project.scm_provider
        from apme_gateway.scan.driver import _url_embedded_userpass

        url_userpass = _url_embedded_userpass(project.repo_url) if not token else None
    files: dict[str, File] = {}
    total = 0
    for kind, entry in declarations:
        source = str(entry.get("src", entry.get("name", ""))).removeprefix("git+")
        source, _, inline_ref = source.partition(",")
        source, _, subdir = source.partition("#")
        parsed = urlsplit(source)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Git requirements must use HTTPS URLs without embedded credentials")
        ref = str(entry.get("version") or inline_ref or "HEAD")
        if ref.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_./-]+", ref) or ".." in ref:
            raise ValueError("Invalid Git requirement version")
        if subdir and (Path(subdir).is_absolute() or ".." in Path(subdir).parts):
            raise ValueError("Invalid Git collection subdirectory")
        same_origin = _origin(source) == project_origin
        async with git_snapshot(
            source,
            ref,
            token if same_origin else None,
            scm_provider=project_provider if same_origin else None,
            url_userpass=url_userpass if same_origin else None,
        ) as tmp:
            collected = await asyncio.to_thread(
                _collect_files, kind, entry, Path(tmp) / subdir, Path(tmp), Path(parsed.path).name
            )
            for dependency in collected:
                if dependency.path in files:
                    raise ValueError("Conflicting Git requirement install paths")
                total += len(dependency.content)
                files[dependency.path] = dependency
                if len(files) > 2000 or total > 256 * 1024 * 1024:
                    raise RuntimeError("Git requirements exceed session upload limits")
    return list(files.values())


def _read_declarations(root: Path) -> list[tuple[str, dict[str, object]]]:
    """Read project requirements on an executor thread.

    Args:
        root: Disposable project checkout.

    Returns:
        Explicit Git collection and role declarations.
    """
    declarations: list[tuple[str, dict[str, object]]] = []
    for name in REQUIREMENTS_PATHS:
        path = root / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            data = yaml.safe_load(path.read_text())
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        declarations.extend(git_entries(data, roles_file=name.startswith("roles/") or isinstance(data, list)))
    return declarations


def _collect_files(
    kind: str, entry: dict[str, object], content_root: Path, repository_root: Path, repository_name: str
) -> list[File]:
    """Collect a bounded dependency bundle without blocking the event loop.

    Args:
        kind: Collections or roles.
        entry: Requirement declaration.
        content_root: Selected repository subdirectory.
        repository_root: Disposable repository checkout.
        repository_name: Repository basename for default role naming.

    Returns:
        Dependency files in installation layout.

    Raises:
        ValueError: Invalid metadata, path or symbolic link.
        RuntimeError: Dependency exceeds upload bounds.
    """
    files: dict[str, File] = {}
    total = 0
    if (
        not content_root.is_dir()
        or content_root.is_symlink()
        or not content_root.resolve().is_relative_to(repository_root.resolve())
    ):
        raise ValueError("Git requirement content directory does not exist")
    if kind == "collections":
        galaxy = content_root / "galaxy.yml"
        if not galaxy.exists():
            galaxy = content_root / "galaxy.yaml"
        if galaxy.is_file() and not galaxy.is_symlink():
            if galaxy.stat().st_size > 2 * 1024 * 1024:
                raise RuntimeError("Git requirement metadata exceeds 2 MiB")
            metadata = yaml.safe_load(galaxy.read_text())
        else:
            manifest = content_root / "MANIFEST.json"
            if manifest.is_symlink():
                raise ValueError("Symlinked collection metadata is not supported")
            if not manifest.is_file():
                raise ValueError("Git collection metadata (galaxy.yml or MANIFEST.json) not found")
            if manifest.stat().st_size > 2 * 1024 * 1024:
                raise RuntimeError("Git requirement metadata exceeds 2 MiB")
            manifest_data = json.loads(manifest.read_text())
            metadata = manifest_data.get("collection_info") if isinstance(manifest_data, dict) else None
        if not isinstance(metadata, dict) or not metadata.get("namespace") or not metadata.get("name"):
            raise ValueError("Git collection metadata must include namespace and name")
        namespace, name = str(metadata["namespace"]), str(metadata["name"])
        if not all(re.fullmatch(r"[a-z][a-z0-9_]*", value) for value in (namespace, name)):
            raise ValueError("Invalid Git collection namespace or name")
        prefix = f"ansible_collections/{namespace}/{name}"
    else:
        name = str(entry.get("name") or repository_name.removesuffix(".git"))
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name):
            raise ValueError("Invalid Git role name")
        prefix = f"roles/{name}"
    for base, dirs, names in os.walk(content_root, followlinks=False):
        if any((Path(base) / directory).is_symlink() for directory in dirs):
            raise ValueError("Symlinks are not supported in Git requirements")
        dirs[:] = [d for d in dirs if d not in {".git", ".venv", "__pycache__"} and not (Path(base) / d).is_symlink()]
        for filename in names:
            path = Path(base) / filename
            if path.is_symlink():
                raise ValueError("Symlinks are not supported in Git requirements")
            if path.stat().st_size > 2 * 1024 * 1024:
                raise RuntimeError("Git requirement file exceeds 2 MiB")
            key = f"{prefix}/{path.relative_to(content_root).as_posix()}"
            if key in files:
                raise ValueError("Conflicting Git requirement install paths")
            content = path.read_bytes()
            total += len(content)
            if len(files) >= 2000 or total > 256 * 1024 * 1024:
                raise RuntimeError("Git requirements exceed session upload limits")
            files[key] = File(path=key, content=content)
    return list(files.values())


def _origin(url: str) -> tuple[str, str | None, int]:
    """Normalize an HTTPS origin for credential scoping.

    Args:
        url: Project or dependency URL.

    Returns:
        Scheme, hostname and effective port.
    """
    parsed = urlsplit(url)
    return parsed.scheme.lower(), parsed.hostname, parsed.port or 443
