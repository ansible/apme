"""Remediation engine — convergence loop that applies Tier 1 transforms.

Uses ``StructuredFile`` to parse each YAML file once, apply all transforms
on the in-memory ``CommentedMap``/``CommentedSeq``, and serialize only when
needed (once per convergence pass, for re-scanning).
"""

from __future__ import annotations

import difflib
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from apme_engine.engine.models import RemediationClass, RemediationResolution, ViolationDict
from apme_engine.remediation.partition import (
    add_classification_to_violations,
    normalize_rule_id,
    partition_violations,
)
from apme_engine.remediation.registry import TransformRegistry
from apme_engine.remediation.structured import StructuredFile
from apme_engine.remediation.transforms._helpers import violation_line_to_int

logger = logging.getLogger(__name__)


@dataclass
class FilePatch:
    """A single file patch with diff and applied rule IDs.

    Attributes:
        path: File path that was patched.
        original: Original file content before patching.
        patched: Content after applying transforms.
        diff: Unified diff string (original -> patched).
        rule_ids: List of rule IDs applied to this file.
    """

    path: str
    original: str
    patched: str
    diff: str
    rule_ids: list[str] = field(default_factory=list)


@dataclass
class FixReport:
    """Summary of remediation run with patches and remaining violations.

    Attributes:
        passes: Number of convergence passes executed.
        fixed: Count of violations fixed.
        applied_patches: List of file patches applied.
        remaining_ai: Violations with no transform but ai_proposable.
        remaining_manual: Violations requiring manual fix.
        ai_proposed: Violations proposed by AI (unused in Tier 1).
        oscillation_detected: True if oscillation was detected and loop bailed.
    """

    passes: int
    fixed: int
    applied_patches: list[FilePatch]
    remaining_ai: list[ViolationDict]
    remaining_manual: list[ViolationDict]
    ai_proposed: list[ViolationDict]
    oscillation_detected: bool


ScanFn = Callable[[list[str]], list[ViolationDict]]


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
        """Initialize the remediation engine.

        Args:
            registry: Transform registry mapping rule IDs to fix functions.
            scan_fn: Callable that scans file paths and returns violations.
            max_passes: Maximum convergence passes (default 5).
            verbose: If True, log progress to stderr.
        """
        self._registry = registry
        self._scan_fn = scan_fn
        self._max_passes = max_passes
        self._verbose = verbose

    def _log(self, msg: str) -> None:
        """Write message to stderr if verbose mode is enabled.

        Args:
            msg: Message to log.
        """
        if self._verbose:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()

    def _write_files(self, file_contents: dict[str, str]) -> None:
        """Write file contents to disk.

        Args:
            file_contents: Map of file path to content string.
        """
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

        Args:
            file_paths: List of file paths to remediate.
            apply: If True, write fixes in place; if False, restore originals.

        Returns:
            FixReport with passes, patches, and remaining violations.
        """
        file_contents: dict[str, str] = {}
        for fp in file_paths:
            file_contents[fp] = Path(fp).read_text(encoding="utf-8")

        # Build StructuredFile wrappers — parse each file once.
        # Files that fail to parse are left in file_contents as raw strings
        # and handled via the legacy string path in the registry.
        structured: dict[str, StructuredFile] = {}
        for fp, content in file_contents.items():
            sf = StructuredFile.from_content(fp, content)
            if sf is not None:
                structured[fp] = sf

        # Violations may report relative filenames (e.g. "site.yml") while
        # file_contents keys are absolute paths.  Build a reverse lookup so we
        # can resolve either form to the canonical key.  When multiple files
        # share a basename the entry is set to None so we skip rather than
        # resolving to the wrong file.
        _basename_to_key: dict[str, str | None] = {}
        for fp in file_paths:
            bn = Path(fp).name
            if bn in _basename_to_key and _basename_to_key[bn] != fp:
                _basename_to_key[bn] = None
            else:
                _basename_to_key[bn] = fp
            _basename_to_key[fp] = fp

        def _resolve_file(vf: str) -> str | None:
            if vf in file_contents:
                return vf
            candidate = _basename_to_key.get(vf) or _basename_to_key.get(Path(vf).name)
            if candidate is None and vf:
                self._log(f"  Skipping violation: ambiguous or unknown file '{vf}'")
            return candidate

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

            tier1.sort(key=violation_line_to_int, reverse=True)

            applied_this_pass = 0
            for v in tier1:
                rule_id = normalize_rule_id(str(v.get("rule_id", "")))
                vf_raw = str(v.get("file", ""))
                vf = _resolve_file(vf_raw)

                if vf is None:
                    continue

                sf = structured.get(vf)
                if sf is not None:
                    applied = self._registry.apply_structured(rule_id, sf, v)
                    if applied:
                        all_applied_rules[vf].append(rule_id)
                        applied_this_pass += 1
                    else:
                        v["remediation_class"] = RemediationClass.AI_CANDIDATE
                        v["remediation_resolution"] = RemediationResolution.TRANSFORM_FAILED
                else:
                    result = self._registry.apply(rule_id, file_contents[vf], v)
                    if result.applied:
                        file_contents[vf] = result.content
                        all_applied_rules[vf].append(rule_id)
                        applied_this_pass += 1
                    else:
                        v["remediation_class"] = RemediationClass.AI_CANDIDATE
                        v["remediation_resolution"] = RemediationResolution.TRANSFORM_FAILED

            # Serialize dirty structured files once per pass
            for fp, sf in structured.items():
                if sf.dirty:
                    file_contents[fp] = sf.serialize()
                    sf.reset_dirty()

            self._log(f"  Pass {pass_num}: applied {applied_this_pass}")

            if applied_this_pass == 0:
                self._log(f"  Pass {pass_num}: transforms produced no changes -> bail")
                break

            self._write_files(file_contents)
            new_violations = self._scan_fn(file_paths)
            new_tier1, _, _ = partition_violations(new_violations, self._registry)
            new_fixable = len(new_tier1)

            if new_fixable >= prev_count:
                self._log(f"  Pass {pass_num}: oscillation ({new_fixable} fixable >= {prev_count})")
                oscillation = True
                for v in new_tier1:
                    v["remediation_class"] = RemediationClass.AI_CANDIDATE
                    v["remediation_resolution"] = RemediationResolution.OSCILLATION
                break

            prev_count = new_fixable

            if new_fixable == 0:
                self._log(f"  Pass {pass_num}: fully converged (0 fixable)")
                break

            # Re-parse structured files from the serialized content
            # so line numbers match the on-disk state for the next pass
            for fp in list(structured):
                sf = StructuredFile.from_content(fp, file_contents[fp])
                if sf is not None:
                    structured[fp] = sf

        # Final partition of remaining violations
        self._write_files(file_contents)
        final_violations = self._scan_fn(file_paths)
        add_classification_to_violations(final_violations, self._registry)
        _, tier2, tier3 = partition_violations(final_violations, self._registry)

        # Build patches
        patches: list[FilePatch] = []
        for fp in file_paths:
            if file_contents[fp] != originals[fp]:
                diff = "".join(
                    difflib.unified_diff(
                        originals[fp].splitlines(keepends=True),
                        file_contents[fp].splitlines(keepends=True),
                        fromfile=f"a/{fp}",
                        tofile=f"b/{fp}",
                    )
                )
                patches.append(
                    FilePatch(
                        path=fp,
                        original=originals[fp],
                        patched=file_contents[fp],
                        diff=diff,
                        rule_ids=all_applied_rules.get(fp, []),
                    )
                )

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
