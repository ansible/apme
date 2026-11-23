"""Materialize gateway-fetched dependency snapshots in Engine-owned venvs."""

import fcntl
import json
from collections.abc import Mapping
from pathlib import Path

import yaml

from apme_engine.daemon.engine_upload import _hash_upload_files, validate_dependency_path
from apme_engine.venv_manager.session import _venv_site_packages
from apme_engine.venv_manager.venv_collections import LocalCollectionWheel
from galaxy_proxy.converter import collection_to_wheel
from galaxy_proxy.metadata import normalize_source_collection_metadata


def _build_git_wheel(fqcn: str, contents: dict[str, bytes]) -> LocalCollectionWheel:
    """Build an installable wheel from one validated collection snapshot.

    Args:
        fqcn: Collection identity supplied by the snapshot layout.
        contents: Files relative to the collection root.

    Returns:
        Wheel artifact and its Galaxy dependency constraints.

    Raises:
        ValueError: Invalid collection metadata or dependency constraints.
    """
    try:
        if "MANIFEST.json" in contents:
            metadata = json.loads(contents["MANIFEST.json"])["collection_info"]
        else:
            if "galaxy.yml" not in contents and "galaxy.yaml" in contents:
                contents["galaxy.yml"] = contents.pop("galaxy.yaml")
            metadata = yaml.safe_load(contents["galaxy.yml"])
    except (KeyError, TypeError, ValueError, yaml.YAMLError):
        raise ValueError(f"Invalid metadata or dependency constraints for Git collection {fqcn}") from None

    # Only validated fields enter wheel headers. Ansible reads the original
    # collection metadata, which is retained with the collection files.
    normalized = normalize_source_collection_metadata(metadata, fqcn)
    filename, content = collection_to_wheel(normalized, contents, resolve_python_requirements=False)
    return LocalCollectionWheel(
        filename,
        content,
        metadata["version"],
        tuple(f"{dep}:{constraint}" if constraint else dep for dep, constraint in normalized["dependencies"].items()),
    )


def build_git_collection_wheels(files: Mapping[str, bytes]) -> dict[str, LocalCollectionWheel]:
    """Package uploaded Git collections using the Galaxy wheel builder.

    Args:
        files: Dependency paths and contents received from the Gateway.

    Returns:
        Local wheels keyed by collection FQCN.
    """
    collections: dict[str, dict[str, bytes]] = {}
    for path, content in files.items():
        layout, namespace, name, *relative = validate_dependency_path(path).split("/")
        if layout == "ansible_collections":
            collections.setdefault(f"{namespace}.{name}", {})["/".join(relative)] = content
    return {fqcn: _build_git_wheel(fqcn, contents) for fqcn, contents in collections.items()}


def install_git_dependencies(venv_root: Path, files: Mapping[str, bytes]) -> None:
    """Materialize Git roles and index the Git collections installed as wheels.

    Args:
        venv_root: Venv dedicated to this dependency payload hash.
        files: Validated dependency layout and bytes received over gRPC.

    Raises:
        ValueError: A dependency path escapes the venv or its install layout.
    """
    site = _venv_site_packages(venv_root).resolve()
    with (venv_root / ".git-dependencies.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = venv_root / ".git-dependencies-ready"
        if marker.exists():
            return
        for path, content in files.items():
            normalized = validate_dependency_path(path)
            if not normalized.startswith("roles/"):
                continue
            dest = site / normalized
            if not dest.resolve().is_relative_to(site):
                raise ValueError("Dependency path escapes venv")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        index = {
            "collections": sorted({".".join(p.split("/")[1:3]) for p in files if p.startswith("ansible_collections/")}),
            "roles": sorted({p.split("/")[1] for p in files if p.startswith("roles/")}),
        }
        (site / ".apme-git-dependencies.json").write_text(json.dumps(index))
        marker.write_text(_hash_upload_files(files))
