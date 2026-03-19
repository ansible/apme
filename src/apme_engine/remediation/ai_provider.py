"""AIProvider protocol and data models for Tier 2 AI escalation.

See ADR-024 for the rationale behind this abstraction.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Protocol

from apme_engine.engine.models import ViolationDict


@dataclass
class AIPatch:
    """A single task-level fix proposed by an AI provider.

    Attributes:
        rule_id: Rule that was fixed.
        line_start: 1-based start line in the original file.
        line_end: 1-based end line (inclusive) in the original file.
        fixed_lines: Replacement text for the line range.
        explanation: What was changed.
        confidence: 0.0-1.0 confidence score.
        diff_hunk: Unified diff hunk for this patch only.
    """

    rule_id: str
    line_start: int
    line_end: int
    fixed_lines: str
    explanation: str
    confidence: float
    diff_hunk: str = ""


@dataclass
class AISkipped:
    """A violation the AI could not fix, with an explanation.

    Attributes:
        rule_id: Rule ID that was not fixed.
        line: 1-based line number of the violation.
        reason: Why the AI could not fix it.
        suggestion: Manual remediation guidance for the user.
    """

    rule_id: str
    line: int
    reason: str
    suggestion: str


@dataclass
class AIProposal:
    """AI-generated fixes for a single file (batch of patches).

    Attributes:
        file: Absolute path to the file being fixed.
        original_yaml: Original file content before any patches.
        fixed_yaml: File content after all accepted patches applied.
        patches: Individual task-level patches from the AI.
        diff: Combined unified diff (original -> proposed).
        skipped: Violations the AI could not fix, with reasons.
        hybrid_transforms_applied: Count of Tier 1 transforms applied
            to clean up the AI output during hybrid validation.
    """

    file: str
    original_yaml: str
    fixed_yaml: str
    patches: list[AIPatch]
    diff: str
    skipped: list[AISkipped] = field(default_factory=list)
    hybrid_transforms_applied: int = 0

    @property
    def rule_ids(self) -> list[str]:
        """Rule IDs covered by patches in this proposal."""
        return [p.rule_id for p in self.patches]

    @property
    def confidence(self) -> float:
        """Minimum confidence across all patches."""
        if not self.patches:
            return 0.0
        return min(p.confidence for p in self.patches)


def _resolve_overlaps(patches: list[AIPatch]) -> list[AIPatch]:
    """Remove overlapping patches, keeping the higher-confidence one.

    When two patches overlap in line ranges, the one with higher confidence
    wins.  On a tie, the patch that covers more lines is preferred.

    Args:
        patches: Unsorted list of patches.

    Returns:
        Non-overlapping subset sorted by line_start ascending.
    """
    if not patches:
        return []

    by_start = sorted(
        patches,
        key=lambda p: (p.line_start, -(p.line_end - p.line_start)),
    )

    kept: list[AIPatch] = [by_start[0]]
    for patch in by_start[1:]:
        prev = kept[-1]
        if patch.line_start <= prev.line_end:
            prev_span = prev.line_end - prev.line_start
            cur_span = patch.line_end - patch.line_start
            if (patch.confidence, cur_span) > (prev.confidence, prev_span):
                kept[-1] = patch
        else:
            kept.append(patch)

    return kept


def apply_patches(file_content: str, patches: list[AIPatch]) -> str:
    """Apply task-level patches to file content.

    Overlapping patches are resolved first (higher confidence wins),
    then applied bottom-up to preserve line numbers.

    Args:
        file_content: Original file content.
        patches: Patches to apply (overlaps resolved internally).

    Returns:
        File content with all patches applied.
    """
    lines = file_content.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    clean = _resolve_overlaps(patches)

    for patch in reversed(clean):
        start_idx = patch.line_start - 1
        end_idx = patch.line_end

        replacement = patch.fixed_lines
        if replacement and not replacement.endswith("\n"):
            replacement += "\n"
        replacement_lines = replacement.splitlines(keepends=True)

        lines[start_idx:end_idx] = replacement_lines

    return "".join(lines)


def generate_patch_hunks(
    original: str,
    patches: list[AIPatch],
    file_path: str = "",
) -> list[AIPatch]:
    """Populate diff_hunk on each patch by diffing against original.

    Args:
        original: Original file content.
        patches: Patches (modified in place).
        file_path: File path for diff headers.

    Returns:
        The same patches with diff_hunk populated.
    """
    orig_lines = original.splitlines(keepends=True)

    for patch in patches:
        start_idx = patch.line_start - 1
        end_idx = patch.line_end
        old_chunk = orig_lines[start_idx:end_idx]

        new_text = patch.fixed_lines
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        new_chunk = new_text.splitlines(keepends=True)

        hunk = "".join(
            difflib.unified_diff(
                old_chunk,
                new_chunk,
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path} (AI proposed)",
                lineterm="",
            )
        )
        patch.diff_hunk = hunk

    return patches


class AIProvider(Protocol):
    """Protocol for AI-powered fix proposal providers.

    The engine depends only on this protocol, never on a concrete
    LLM client library.  See ADR-024.
    """

    async def propose_fixes(
        self,
        violations: list[ViolationDict],
        file_content: str,
        *,
        model: str | None = None,
        feedback: str | None = None,
    ) -> tuple[list[AIPatch] | None, list[AISkipped]]:
        """Propose fixes for multiple violations in a single file.

        The provider receives the full file for context and all violations.
        It returns task-level patches (line-range replacements) and a list
        of violations it could not fix with explanations.

        Args:
            violations: All violations for this file.
            file_content: Full content of the file.
            model: Optional model identifier (e.g. 'openai/gpt-4o').
            feedback: Validation failure context for retry attempts.

        Returns:
            Tuple of (patches or None on failure, skipped violations).
        """
        ...

    async def propose_unit_fixes(
        self,
        violations: list[ViolationDict],
        snippet: str,
        file_path: str,
        line_start: int,
        line_end: int,
        *,
        model: str | None = None,
        feedback: str | None = None,
    ) -> tuple[list[AIPatch] | None, list[AISkipped]]:
        """Propose fixes for violations within a single fixable unit.

        Receives only the code snippet for the unit (a single task or
        block), reducing token usage and improving fix quality.
        Returned patches use file-level line numbers.

        Args:
            violations: Violations scoped to this unit.
            snippet: YAML text of just this unit.
            file_path: Path to the file (for display).
            line_start: 1-based first line of the unit in the file.
            line_end: 1-based last line of the unit in the file.
            model: Optional model identifier.
            feedback: Validation failure context for retry.

        Returns:
            Tuple of (patches or None on failure, skipped violations).
        """
        ...
