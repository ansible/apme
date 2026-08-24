"""Scan installed Ansible collections with a curated rule subset (ADR-051).

Discovers installed collection paths inside a session venv, builds a
``ContentGraph`` per collection using the same loader pipeline the engine
uses, and runs a curated subset of native GraphRules against each.
Results are annotated with ``RuleScope.COLLECTION`` metadata.
"""

from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path

from apme_engine.engine.models import ViolationDict

logger = logging.getLogger("apme.collection_health")

CURATED_RULE_IDS: tuple[str, ...] = (
    # Deprecated patterns (native GraphRule implementations only)
    "M005",
    "M010",
    # Risk / security indicators
    "R101",
    "R103",
    "R104",
    "R105",
    "R106",
    "R107",
    "R108",
    "R109",
    "R111",
    "R112",
    "R113",
    "R114",
    "R115",
    "R117",
    "R401",
)


def _discover_collection_dirs(venv_dir: Path) -> list[tuple[str, str, Path]]:
    """Find installed collection directories in a venv's site-packages.

    Args:
        venv_dir: Root of the virtual environment.

    Returns:
        List of ``(fqcn, version, collection_path)`` tuples.
    """
    lib_dir = venv_dir / "lib"
    if not lib_dir.is_dir():
        return []

    results: list[tuple[str, str, Path]] = []
    for pydir in sorted(lib_dir.iterdir()):
        ac_root = pydir / "site-packages" / "ansible_collections"
        if not ac_root.is_dir():
            continue
        for ns_dir in sorted(ac_root.iterdir()):
            if not ns_dir.is_dir() or ns_dir.name.startswith(("_", ".")):
                continue
            for name_dir in sorted(ns_dir.iterdir()):
                if not name_dir.is_dir() or name_dir.name.startswith(("_", ".")):
                    continue
                fqcn = f"{ns_dir.name}.{name_dir.name}"
                version = _read_collection_version(name_dir)
                results.append((fqcn, version, name_dir))

    return results


def _read_collection_version(collection_dir: Path) -> str:
    """Read collection version from MANIFEST.json or galaxy.yml.

    Args:
        collection_dir: Root of the installed collection.

    Returns:
        Version string, or empty string if unreadable.
    """
    import json

    manifest = collection_dir / "MANIFEST.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text())
            info = data.get("collection_info", {})
            return str(info.get("version", ""))
        except (json.JSONDecodeError, OSError):
            pass

    for galaxy_file in ("galaxy.yml", "galaxy.yaml"):
        gf = collection_dir / galaxy_file
        if gf.is_file():
            try:
                from ruamel.yaml import YAML

                yaml = YAML()
                data = yaml.load(gf)
                return str(data.get("version", "")) if isinstance(data, dict) else ""
            except Exception:
                pass

    return ""


def _scan_collection(
    fqcn: str,
    version: str,
    collection_dir: Path,
) -> list[ViolationDict]:
    """Build a ContentGraph for one collection and run curated rules.

    Args:
        fqcn: Fully-qualified collection name.
        version: Collection version string.
        collection_dir: Filesystem path to the installed collection root.

    Returns:
        List of ViolationDicts with collection metadata attached.
    """
    from apme_engine.graph.scanner import (
        graph_report_to_violations,
        load_graph_rules,
        native_rules_dir,
    )
    from apme_engine.graph.scanner import scan as graph_scan
    from apme_engine.runner import run_scan

    t0 = time.monotonic()

    try:
        context = run_scan(
            target_path=str(collection_dir),
            project_root=str(collection_dir),
            include_scandata=True,
            include_test_contents=False,
        )
    except Exception as exc:
        logger.warning("Failed to load collection %s %s: %s", fqcn, version, exc)
        return []

    cg = None
    if context.scandata and hasattr(context.scandata, "content_graph"):
        cg = context.scandata.content_graph

    if cg is None:
        logger.debug("No ContentGraph for collection %s %s", fqcn, version)
        return []

    rules, _ = load_graph_rules(
        rules_dir=native_rules_dir(),
        rule_id_list=list(CURATED_RULE_IDS),
    )
    if not rules:
        logger.debug("No curated rules loaded for collection scanning")
        return []

    report = graph_scan(cg, rules, owned_only=False)
    violations = graph_report_to_violations(report)

    for v in violations:
        node_id = str(v.get("path", ""))
        node = cg.get_node(node_id) if node_id else None
        if node and node.yaml_lines:
            v["original_yaml"] = node.yaml_lines
            v["node_line_start"] = node.line_start

        v["source"] = "collection_health"
        v["scope"] = "collection"
        v["path"] = fqcn
        v["collection_fqcn"] = fqcn
        v["collection_version"] = version
        file_path = str(v.get("file", ""))
        if file_path:
            with contextlib.suppress(ValueError):
                v["file"] = str(Path(file_path).relative_to(collection_dir))

    elapsed = (time.monotonic() - t0) * 1000
    logger.info(
        "Collection %s %s: scanned in %.0fms, %d findings",
        fqcn,
        version,
        elapsed,
        len(violations),
    )

    return violations


def scan_collections(
    venv_dir: Path,
    *,
    rescan: bool = False,
) -> list[ViolationDict]:
    """Scan all collections installed in a session venv.

    Uses a persistent cache keyed on ``(fqcn, version, cache_schema)``
    to avoid re-scanning immutable collection content.

    Args:
        venv_dir: Root of the session virtual environment.
        rescan: If True, ignore cached results (cache bust).

    Returns:
        Aggregated list of ViolationDicts from all collections.
    """
    from .cache import compute_cache_schema, get_cached, put_cached

    collections = _discover_collection_dirs(venv_dir)
    if not collections:
        logger.debug("No collections found in %s", venv_dir)
        return []

    cache_schema = compute_cache_schema(CURATED_RULE_IDS)
    all_violations: list[ViolationDict] = []

    for fqcn, version, coll_dir in collections:
        if not rescan:
            cached = get_cached(fqcn, version, cache_schema)
            if cached is not None:
                logger.debug("Cache hit for %s %s", fqcn, version)
                all_violations.extend(cached)
                continue

        findings = _scan_collection(fqcn, version, coll_dir)
        serializable = [dict(v) for v in findings]
        put_cached(fqcn, version, cache_schema, serializable)
        all_violations.extend(findings)

    logger.info(
        "Collection health scan: %d collections, %d total findings",
        len(collections),
        len(all_violations),
    )
    return all_violations
