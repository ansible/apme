"""Run the integrated scan engine and return a ScanContext."""

import os
import tempfile
import time
from pathlib import Path

from apme_engine.engine.scanner import ARIScanner
from apme_engine.validators.base import EngineDiagnostics, ScanContext


def run_scan_playbook_yaml(
    yaml_content: str,
    project_root: str | None = None,
    include_scandata: bool = True,
) -> ScanContext:
    """Run the engine on a playbook given as a YAML string (e.g. for integration tests).

    Writes content to a temporary playbook file and runs the scanner.

    Args:
        yaml_content: Full playbook YAML string (e.g. a list of plays with hosts and tasks).
        project_root: Root directory for the scan. If None, a temp directory is used.
        include_scandata: If True, attach the SingleScan to context for native validator.

    Returns:
        ScanContext with hierarchy_payload and optionally scandata.

    """
    with tempfile.TemporaryDirectory(prefix="apme_rule_doc_") as tmpdir:
        playbook_path = os.path.join(tmpdir, "playbook.yml")
        with open(playbook_path, "w") as f:
            f.write(yaml_content)
        # Use temp dir as project root so scanner writes under tmpdir (works in sandbox)
        return run_scan(playbook_path, tmpdir, include_scandata=include_scandata)


def run_scan(
    target_path: str,
    project_root: str,
    include_scandata: bool = True,
    dependency_dir: str = "",
) -> ScanContext:
    """Run the engine on target_path and return a ScanContext for validators.

    ARI never downloads collections. When a session venv is available,
    pass its site-packages as ``dependency_dir`` so ARI can resolve
    external collection definitions.

    Args:
        target_path: Path to playbook file, taskfile, or project directory.
        project_root: Root directory for the scan (data dir).
        include_scandata: If True, attach the SingleScan to context for ARI native validator.
        dependency_dir: Pre-installed dependency directory (e.g. session venv
            site-packages).  ARI reads from this path but never writes to it.

    Returns:
        ScanContext with hierarchy_payload and optionally scandata.

    Raises:
        FileNotFoundError: If target_path does not exist.

    """
    root_dir = project_root or os.path.expanduser("~/.apme-data")
    scanner = ARIScanner(
        root_dir=root_dir,
        rules_dir="",  # no native rules at scan time; ARI validator runs rules
        silent=True,
    )
    path = Path(target_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Target path does not exist: {target_path}")
    if path.is_file():
        name = str(path)
        base_dir = str(path.parent)
        scan_type = "playbook"
    else:
        name = str(path)
        base_dir = str(path)
        scan_type = "project"

    t0 = time.monotonic()
    scanner.evaluate(
        type=scan_type,
        name=name,
        path=name,
        base_dir=base_dir,
        dependency_dir=dependency_dir,
        skip_dependency=False,
        load_all_taskfiles=True,
        include_test_contents=True,
    )
    engine_total_ms = (time.monotonic() - t0) * 1000

    scandata = scanner._current
    diag = _extract_engine_diagnostics(scandata, engine_total_ms)

    if not scandata or not getattr(scandata, "hierarchy_payload", None):
        return ScanContext(
            hierarchy_payload={},
            scandata=scandata if include_scandata else None,
            root_dir=root_dir,
            engine_diagnostics=diag,
        )
    return ScanContext(
        hierarchy_payload=scandata.hierarchy_payload,
        scandata=scandata if include_scandata else None,
        root_dir=root_dir,
        engine_diagnostics=diag,
    )


def _extract_engine_diagnostics(scandata: object, engine_total_ms: float) -> EngineDiagnostics:
    """Pull per-phase elapsed times from the scanner's time_records.

    Args:
        scandata: Scanner result object with findings metadata.
        engine_total_ms: Total engine wall-clock time in milliseconds.

    Returns:
        EngineDiagnostics populated from time_records.

    """
    diag = EngineDiagnostics(total_ms=engine_total_ms)
    if not scandata:
        return diag

    tr = {}
    if hasattr(scandata, "findings") and scandata.findings:
        tr = getattr(scandata.findings, "metadata", {}).get("time_records", {})

    def _ms(key: str) -> float:
        return float(tr.get(key, {}).get("elapsed", 0.0)) * 1000

    diag.parse_ms = _ms("target_load") + _ms("prm_load") + _ms("metadata_load")
    diag.annotate_ms = _ms("module_annotators") + _ms("variable_resolution")
    diag.tree_build_ms = _ms("tree_construction")

    trees = getattr(scandata, "trees", None)
    if trees:
        diag.trees_built = len(trees)

    root_defs = getattr(scandata, "root_definitions", None)
    if root_defs:
        diag.files_scanned = len(root_defs)

    return diag
