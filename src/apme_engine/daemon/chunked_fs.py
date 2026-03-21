"""Build a ScanRequest from a local path (chunked filesystem)."""

import fnmatch
import os
from collections.abc import Iterator
from pathlib import Path

from apme.v1.common_pb2 import File
from apme.v1.primary_pb2 import ScanChunk, ScanOptions, ScanRequest

# Max bytes per ScanChunk message to stay under typical gRPC max message size (e.g. 4 MiB).
CHUNK_MAX_BYTES = 1024 * 1024  # 1 MiB

# Skip these dirs when walking (same kind of ignores as many linters)
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".tox", "htmlcov", ".github", "archives"}

SKIP_FILENAMES = {".travis.yml", ".travis.yaml"}

# Max file size to include (bytes); skip binary-ish or huge files
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MiB

# Extensions we care about for Ansible (include these; exclude or skip binary)
TEXT_EXTENSIONS = {
    ".yml",
    ".yaml",
    ".json",
    ".j2",
    ".jinja2",
    ".md",
    ".py",
    ".sh",
    ".cfg",
    ".ini",
    ".toml",
    ".yml.sample",
    ".yaml.sample",
    ".tf",
    ".tfvars",
}


def _load_apmeignore(root: Path) -> list[str]:
    """Load ignore patterns from a .apmeignore file in the project root.

    Each non-blank, non-comment line is a glob pattern matched against the
    relative path (directories match with a trailing ``/``).

    Args:
        root: Project root directory containing the .apmeignore file.

    Returns:
        List of glob pattern strings.
    """
    ignore_file = root / ".apmeignore"
    if not ignore_file.is_file():
        return []
    patterns: list[str] = []
    try:
        for line in ignore_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                patterns.append(stripped)
    except OSError:
        pass
    return patterns


def _matches_ignore(rel_path: str, patterns: list[str]) -> bool:
    """Return True if rel_path matches any of the ignore patterns.

    Args:
        rel_path: Relative file path to check.
        patterns: Glob patterns from .apmeignore.

    Returns:
        True if the path matches at least one pattern.
    """
    rel_parts = Path(rel_path).parts
    for pat in patterns:
        is_dir_pattern = pat.endswith("/")
        normalized = pat.rstrip("/") if is_dir_pattern else pat

        if is_dir_pattern:
            dir_parts = Path(normalized).parts
            if len(rel_parts) >= len(dir_parts) and rel_parts[: len(dir_parts)] == dir_parts:
                return True

        if fnmatch.fnmatch(rel_path, pat):
            return True
        if "/" in pat and fnmatch.fnmatch(rel_path, normalized):
            return True

        for part in rel_parts:
            if fnmatch.fnmatch(part, normalized):
                return True
    return False


def _should_include(path: Path, root: Path, ignore_patterns: list[str] | None = None) -> bool:
    """Determine if a file should be included in the scan based on size, dirs, extensions.

    Args:
        path: Absolute path to the file.
        root: Project root path for relative path checks.
        ignore_patterns: Optional list of glob patterns from .apmeignore.

    Returns:
        True if the file should be included, False otherwise.
    """
    if not path.is_file():
        return False
    try:
        if path.stat().st_size > MAX_FILE_SIZE:
            return False
    except OSError:
        return False
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    parts = rel.parts
    if any(p in SKIP_DIRS for p in parts):
        return False
    if path.name in SKIP_FILENAMES:
        return False
    if ignore_patterns and _matches_ignore(str(rel), ignore_patterns):
        return False
    # Include known text extensions; include files with no extension (e.g. playbook)
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return True
    if path.name in ("playbook", "main", "meta", "handlers", "tasks", "vars", "defaults"):
        return True
    # Include small text files under roles/ and playbooks/
    return bool("roles" in parts or "playbooks" in parts or suffix in (".yml", ".yaml", ".j2"))


def build_scan_request(
    target_path: str | Path,
    scan_id: str | None = None,
    project_root_name: str = "project",
    ansible_core_version: str | None = None,
    collection_specs: list[str] | None = None,
    session_id: str = "",
) -> ScanRequest:
    """Walk target_path (file or directory) and build a ScanRequest with chunked files.

    Paths in File messages are relative to the project root (target_path if dir, else parent).

    Args:
        target_path: File or directory to scan.
        scan_id: Optional scan identifier.
        project_root_name: Name for project root in the request.
        ansible_core_version: Optional Ansible core version.
        collection_specs: Optional list of collection specifiers.
        session_id: Session ID for venv reuse across scans.

    Returns:
        ScanRequest with files and options populated.

    Raises:
        FileNotFoundError: If target_path does not exist.
    """
    target = Path(target_path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Target does not exist: {target_path}")

    if target.is_file():
        root = target.parent
        to_visit = [target]
    else:
        root = target
        to_visit = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                to_visit.append(Path(dirpath) / name)

    ignore_patterns = _load_apmeignore(root)

    files = []
    for path in to_visit:
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if not _should_include(path, root, ignore_patterns):
            continue
        try:
            content = path.read_bytes()
        except (OSError, UnicodeDecodeError):
            continue
        # Skip if looks binary
        if b"\x00" in content[:8192]:
            continue
        files.append(File(path=str(rel), content=content))

    options = ScanOptions()
    if ansible_core_version:
        options.ansible_core_version = ansible_core_version
    if collection_specs:
        options.collection_specs.extend(collection_specs)
    if session_id:
        options.session_id = session_id

    return ScanRequest(
        scan_id=scan_id or "",
        project_root=project_root_name,
        files=files,
        options=options,
        session_id=session_id,
    )


def yield_scan_chunks(
    target_path: str | Path,
    scan_id: str | None = None,
    project_root_name: str = "project",
    ansible_core_version: str | None = None,
    collection_specs: list[str] | None = None,
    chunk_max_bytes: int = CHUNK_MAX_BYTES,
    session_id: str = "",
) -> Iterator[ScanChunk]:
    """Yield ScanChunk messages for ScanStream so the total request stays under gRPC message limits.

    First chunk includes scan_id, project_root, options; subsequent chunks carry only files.
    Last chunk has last=True.

    Args:
        target_path: File or directory to scan.
        scan_id: Optional scan identifier.
        project_root_name: Name for project root.
        ansible_core_version: Optional Ansible core version.
        collection_specs: Optional list of collection specifiers.
        chunk_max_bytes: Max serialized size per chunk (default 1 MiB).
        session_id: Session ID for venv reuse across scans.

    Yields:
        ScanChunk: ScanChunk messages for streaming.
    """
    req = build_scan_request(
        target_path,
        scan_id=scan_id,
        project_root_name=project_root_name,
        ansible_core_version=ansible_core_version,
        collection_specs=collection_specs,
        session_id=session_id,
    )
    files: list[File] = list(req.files)  # type: ignore[arg-type]
    if not files:
        yield ScanChunk(
            scan_id=req.scan_id or "",
            project_root=req.project_root,
            options=req.options,
            files=[],
            last=True,
        )
        return
    opts = req.options
    batch: list[File] = []
    batch_bytes = 0
    first_chunk = True
    for f in files:
        msg_size = len(f.path.encode()) + len(f.content)
        if batch and batch_bytes + msg_size > chunk_max_bytes:
            chunk_kwargs: dict[str, object] = {
                "files": batch,
                "last": False,
            }
            if first_chunk:
                chunk_kwargs["scan_id"] = req.scan_id or ""
                chunk_kwargs["project_root"] = req.project_root
                chunk_kwargs["options"] = opts
            yield ScanChunk(**chunk_kwargs)
            first_chunk = False
            batch = []
            batch_bytes = 0
        batch.append(f)
        batch_bytes += msg_size
    final_kwargs: dict[str, object] = {
        "files": batch,
        "last": True,
    }
    if first_chunk:
        final_kwargs["scan_id"] = req.scan_id or ""
        final_kwargs["project_root"] = req.project_root
        final_kwargs["options"] = opts
    yield ScanChunk(**final_kwargs)
