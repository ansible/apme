"""Ansible validator: runs checks that require an ansible-core runtime (venv).

Rules are colocated under rules/ and follow the same pattern as native and OPA validators.
Each rule module exports a run() function that returns a list of violation dicts.
"""

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from apme_engine.engine.models import YAMLDict
from apme_engine.validators.base import ScanContext

from .cache import CacheSnapshot, plugin_cache
from .rules import L057_syntax, L058_argspec_doc, L059_argspec_mock, M001_M004_introspect

NodeLookup = dict[str, list[tuple[int, int, str]]]


@dataclass
class AnsibleRuleTiming:
    """Per-rule timing for ansible validator.

    Attributes:
        rule_id: Rule identifier.
        elapsed_ms: Elapsed time in milliseconds.
        violations: Number of violations found.
    """

    rule_id: str = ""
    elapsed_ms: float = 0.0
    violations: int = 0


@dataclass
class AnsibleRunResult:
    """Result of ansible validator run.

    Attributes:
        violations: List of violation dicts.
        rule_timings: Per-rule timing data.
        metadata: Extra metadata: cumulative ``cache_<store>_hits/misses``
            counters plus per-scan ``scan_cache_<store>_hits/misses``
            deltas (zero when the scan touched nothing).
    """

    violations: list[dict[str, object]] = field(default_factory=list)
    rule_timings: list[AnsibleRuleTiming] = field(default_factory=list)
    metadata: dict[str, int] = field(default_factory=dict)


def _extract_task_nodes(hierarchy_payload: YAMLDict | None) -> list[dict[str, object]]:
    """Extract all taskcall nodes from the hierarchy payload.

    Args:
        hierarchy_payload: Hierarchy payload from scan context.

    Returns:
        List of taskcall node dicts.
    """
    nodes: list[dict[str, object]] = []
    if hierarchy_payload is None:
        return nodes
    hierarchy = hierarchy_payload.get("hierarchy", [])
    if not isinstance(hierarchy, list | tuple):
        return nodes
    for tree in hierarchy:
        if not isinstance(tree, dict):
            continue
        raw_nodes = tree.get("nodes", [])
        if not isinstance(raw_nodes, list | tuple):
            continue
        for node in raw_nodes:
            if isinstance(node, dict) and node.get("type") == "taskcall":
                nodes.append(cast(dict[str, object], node))
    return nodes


def build_node_lookup(content_graph_data: bytes) -> NodeLookup:
    """Parse serialized ContentGraph JSON and build a file/line-to-node_id lookup.

    Args:
        content_graph_data: JSON-encoded ContentGraph (``to_dict(slim=True)``).

    Returns:
        Dict mapping ``file_path`` to sorted ``[(line_start, line_end, node_id)]``.
    """
    lookup: NodeLookup = {}
    if not content_graph_data:
        return lookup

    try:
        data = cast(dict[str, object], json.loads(content_graph_data))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return lookup

    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, list):
        return lookup

    for entry in raw_nodes:
        if not isinstance(entry, dict):
            continue
        node_id = str(entry.get("id", ""))
        node_data = entry.get("data")
        if not isinstance(node_data, dict) or not node_id:
            continue
        file_path = str(node_data.get("file_path", ""))
        line_start = node_data.get("line_start", 0)
        line_end = node_data.get("line_end", 0)
        if not file_path or not isinstance(line_start, int) or not isinstance(line_end, int):
            continue
        if line_start <= 0 or line_end <= 0:
            continue
        lookup.setdefault(file_path, []).append((line_start, line_end, node_id))

    for ranges in lookup.values():
        ranges.sort(key=lambda t: t[0])

    return lookup


def resolve_file_line_to_node(
    lookup: NodeLookup,
    file_path: str,
    line: int,
) -> str:
    """Find the narrowest node whose line range contains ``line``.

    Args:
        lookup: File-to-ranges lookup from ``build_node_lookup()``.
        file_path: File path to match.
        line: 1-based line number.

    Returns:
        Matching ``node_id``, or ``""`` if no range contains the line.
    """
    ranges = lookup.get(file_path)
    if not ranges:
        return ""

    best: str = ""
    best_span = float("inf")
    for ls, le, nid in ranges:
        if ls > line:
            break
        if ls <= line <= le:
            span = le - ls
            if span < best_span:
                best = nid
                best_span = span

    return best


def _cache_report(cache_snapshot: CacheSnapshot, violations_count: int) -> tuple[dict[str, int], str]:
    """Build merged cache metadata and the stderr summary fragment.

    Args:
        cache_snapshot: Counter snapshot taken at scan start.
        violations_count: Total violations for the log line.

    Returns:
        Merged cumulative + per-scan metadata and the full stderr line.
    """
    cache_stats = plugin_cache.stats()
    scan_stats = plugin_cache.stats_since(cache_snapshot)

    def _pair(store: str) -> str:
        """Format per-scan and cumulative hits/misses for a store.

        Args:
            store: Cache store name.

        Returns:
            Human-readable ``Ah/Bm (scan) Ch/Dm (total)`` fragment.
        """
        return (
            f"{scan_stats.get(f'scan_cache_{store}_hits', 0)}h/"
            f"{scan_stats.get(f'scan_cache_{store}_misses', 0)}m (scan) "
            f"{cache_stats.get(f'cache_{store}_hits', 0)}h/"
            f"{cache_stats.get(f'cache_{store}_misses', 0)}m (total)"
        )

    line = (
        f"Ansible validator: total {violations_count} violation(s), "
        f"cache introspect={_pair('introspect')}, "
        f"docspec={_pair('docspec')}, "
        f"mockspec={_pair('mockspec')}\n"
    )
    return {**cache_stats, **scan_stats}, line


class AnsibleValidator:
    """Validator that runs ansible-core checks via pre-built venvs.

    Rules:
      L057 - Syntax check (ansible-playbook --syntax-check)
      L058 - Argspec validation (docstring-based)
      L059 - Argspec validation (mock/patch-based)
      M001 - FQCN resolution
      M002 - Deprecated module
      M003 - Module redirect
      M004 - Removed/tombstoned module
    """

    def __init__(
        self,
        venv_root: Path,
        env_extra: dict[str, str] | None = None,
    ):
        """Initialize the ansible validator.

        Args:
            venv_root: Path to the ansible-core venv root.
            env_extra: Optional extra environment variables for subprocesses.
        """
        self._venv_root = venv_root
        self._env_extra = env_extra

    def run(
        self,
        context: ScanContext,
        content_graph_data: bytes = b"",
    ) -> list[dict[str, object]]:
        """Run all ansible checks and return violation dicts.

        Args:
            context: Scan context with hierarchy payload and root dir.
            content_graph_data: Serialized ContentGraph for L057 node resolution.

        Returns:
            List of violation dicts.
        """
        return self.run_with_timing(context, content_graph_data=content_graph_data).violations

    def run_with_timing(
        self,
        context: ScanContext,
        content_graph_data: bytes = b"",
    ) -> AnsibleRunResult:
        """Run all ansible checks and return violations + per-rule timing.

        Args:
            context: Scan context with hierarchy payload and root dir.
            content_graph_data: Serialized ContentGraph for L057 node resolution.

        Returns:
            AnsibleRunResult with violations and rule timings.
        """
        violations: list[dict[str, object]] = []
        rule_timings: list[AnsibleRuleTiming] = []
        root_dir = Path(context.root_dir) if context.root_dir else None
        # Cumulative counters span the daemon lifetime; snapshot for
        # per-scan deltas reported below alongside cumulative totals.
        cache_snapshot = plugin_cache.snapshot()

        node_lookup: NodeLookup = {}
        if content_graph_data:
            node_lookup = build_node_lookup(content_graph_data)

        if root_dir and root_dir.is_dir():
            t0 = time.monotonic()
            l057 = L057_syntax.run(
                venv_root=self._venv_root,
                root_dir=root_dir,
                env_extra=self._env_extra,
            )
            elapsed = (time.monotonic() - t0) * 1000
            if node_lookup:
                for v in l057:
                    fpath = str(v.get("file", ""))
                    line_val = v.get("line")
                    ln = int(line_val) if isinstance(line_val, int) else 0
                    if fpath and ln > 0:
                        nid = resolve_file_line_to_node(node_lookup, fpath, ln)
                        if nid:
                            v["path"] = nid
            violations.extend(l057)
            rule_timings.append(AnsibleRuleTiming(rule_id="L057", elapsed_ms=elapsed, violations=len(l057)))
            sys.stderr.write(f"  L057 (syntax): {len(l057)} issue(s) in {elapsed:.1f}ms\n")

        task_nodes = _extract_task_nodes(context.hierarchy_payload) if context.hierarchy_payload else []
        if not task_nodes:
            metadata, summary_line = _cache_report(cache_snapshot, len(violations))
            sys.stderr.write(summary_line)
            sys.stderr.flush()
            return AnsibleRunResult(
                violations=violations,
                rule_timings=rule_timings,
                metadata=metadata,
            )

        sys.stderr.write(f"Ansible validator: checking {len(task_nodes)} task(s)\n")

        t0 = time.monotonic()
        m_violations = M001_M004_introspect.run(
            task_nodes=task_nodes,
            venv_root=self._venv_root,
            env_extra=self._env_extra,
        )
        elapsed = (time.monotonic() - t0) * 1000
        violations.extend(m_violations)
        rule_timings.append(
            AnsibleRuleTiming(rule_id="M001-M004/M048", elapsed_ms=elapsed, violations=len(m_violations))
        )
        sys.stderr.write(f"  M001-M004/M048 (introspection): {len(m_violations)} issue(s) in {elapsed:.1f}ms\n")

        t0 = time.monotonic()
        l058 = L058_argspec_doc.run(
            task_nodes=task_nodes,
            venv_root=self._venv_root,
            env_extra=self._env_extra,
        )
        elapsed = (time.monotonic() - t0) * 1000
        violations.extend(l058)
        rule_timings.append(AnsibleRuleTiming(rule_id="L058", elapsed_ms=elapsed, violations=len(l058)))
        sys.stderr.write(f"  L058 (argspec-doc): {len(l058)} issue(s) in {elapsed:.1f}ms\n")

        t0 = time.monotonic()
        l059 = L059_argspec_mock.run(
            task_nodes=task_nodes,
            venv_root=self._venv_root,
            env_extra=self._env_extra,
        )
        elapsed = (time.monotonic() - t0) * 1000
        violations.extend(l059)
        rule_timings.append(AnsibleRuleTiming(rule_id="L059", elapsed_ms=elapsed, violations=len(l059)))
        sys.stderr.write(f"  L059 (argspec-mock): {len(l059)} issue(s) in {elapsed:.1f}ms\n")

        metadata, summary_line = _cache_report(cache_snapshot, len(violations))
        sys.stderr.write(summary_line)
        sys.stderr.flush()
        return AnsibleRunResult(
            violations=violations,
            rule_timings=rule_timings,
            metadata=metadata,
        )
