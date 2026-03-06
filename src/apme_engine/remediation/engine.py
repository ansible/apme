"""Remediation engine — convergence loop that applies Tier 1 transforms."""

from __future__ import annotations

import difflib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from apme_engine.remediation.partition import partition_violations
from apme_engine.remediation.registry import TransformRegistry


@dataclass
class FilePatch:
    path: str
    original: str
    patched: str
    diff: str
    rule_ids: list[str] = field(default_factory=list)


@dataclass
class FixReport:
    passes: int
    fixed: int
    applied_patches: list[FilePatch]
    remaining_ai: list[dict]
    remaining_manual: list[dict]
    ai_proposed: list[dict]
    oscillation_detected: bool


ScanFn = Callable[[list[str]], list[dict]]


class RemediationEngine:
    """Scan -> transform -> re-scan convergence loop.

    The engine does NOT own scanning — it receives a callable ``scan_fn``
    that accepts a list of file paths and returns violations.  The scan_fn
    reads file contents from disk, so when ``apply=False`` the engine
    writes temp content before each scan pass and restores afterwards.
    """

    def __init__(
        self,
        registry: TransformRegistry,
        scan_fn: ScanFn,
        *,
        max_passes: int = 5,
        verbose: bool = False,
    ) -> None:
        self._registry = registry
        self._scan_fn = scan_fn
        self._max_passes = max_passes
        self._verbose = verbose

    def _log(self, msg: str) -> None:
        if self._verbose:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()

    def _write_files(self, file_contents: dict[str, str]) -> None:
        for fp, content in file_contents.items():
            Path(fp).write_text(content, encoding="utf-8")

    def remediate(
        self,
        file_paths: list[str],
        *,
        apply: bool = False,
    ) -> FixReport:
        """Run the convergence loop on the given files.

        If ``apply`` is True, fixed files are written in place.
        If ``apply`` is False, content is written temporarily for each
        scan pass and originals are restored at the end; the returned
        ``FixReport`` carries diffs for review.
        """
        file_contents: dict[str, str] = {}
        for fp in file_paths:
            file_contents[fp] = Path(fp).read_text(encoding="utf-8")

        originals = dict(file_contents)
        all_applied_rules: dict[str, list[str]] = {fp: [] for fp in file_paths}
        prev_count = float("inf")
        oscillation = False
        passes = 0

        for pass_num in range(1, self._max_passes + 1):
            passes = pass_num

            self._write_files(file_contents)
            violations = self._scan_fn(file_paths)
            tier1, _, _ = partition_violations(violations, self._registry)

            self._log(f"  Pass {pass_num}: {len(tier1)} fixable (Tier 1)")

            if not tier1:
                self._log(f"  Pass {pass_num}: 0 fixable -> converged")
                break

            applied_this_pass = 0
            for v in tier1:
                rule_id = v.get("rule_id", "")
                vf = v.get("file", "")

                if vf not in file_contents:
                    continue

                result = self._registry.apply(rule_id, file_contents[vf], v)
                if result.applied:
                    file_contents[vf] = result.content
                    all_applied_rules[vf].append(rule_id)
                    applied_this_pass += 1

            self._log(f"  Pass {pass_num}: applied {applied_this_pass}")

            if applied_this_pass == 0:
                self._log(f"  Pass {pass_num}: transforms produced no changes -> bail")
                break

            self._write_files(file_contents)
            new_violations = self._scan_fn(file_paths)
            new_count = len(new_violations)

            if new_count >= prev_count:
                self._log(f"  Pass {pass_num}: oscillation ({new_count} >= {prev_count})")
                oscillation = True
                break

            prev_count = new_count

            if new_count == 0:
                self._log(f"  Pass {pass_num}: fully converged (0 violations)")
                break

        # Final partition of remaining violations
        self._write_files(file_contents)
        final_violations = self._scan_fn(file_paths)
        _, tier2, tier3 = partition_violations(final_violations, self._registry)

        # Build patches
        patches: list[FilePatch] = []
        for fp in file_paths:
            if file_contents[fp] != originals[fp]:
                diff = "".join(difflib.unified_diff(
                    originals[fp].splitlines(keepends=True),
                    file_contents[fp].splitlines(keepends=True),
                    fromfile=f"a/{fp}",
                    tofile=f"b/{fp}",
                ))
                patches.append(FilePatch(
                    path=fp,
                    original=originals[fp],
                    patched=file_contents[fp],
                    diff=diff,
                    rule_ids=all_applied_rules.get(fp, []),
                ))

        # If not applying, restore originals
        if not apply:
            self._write_files(originals)

        fixed_count = sum(len(p.rule_ids) for p in patches)

        return FixReport(
            passes=passes,
            fixed=fixed_count,
            applied_patches=patches,
            remaining_ai=tier2,
            remaining_manual=tier3,
            ai_proposed=[],
            oscillation_detected=oscillation,
        )
