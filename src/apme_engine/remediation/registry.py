"""Transform registry — maps rule IDs to deterministic fix functions.

Supports two transform signatures:

- **Structured** (preferred): ``(StructuredFile, ViolationDict) -> bool``
  — operates on the already-parsed ruamel.yaml data in-place.
- **Legacy string**: ``(str, ViolationDict) -> TransformResult``
  — re-parses the file on every call.  Retained for backward compat.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, NamedTuple

from apme_engine.engine.models import ViolationDict

if TYPE_CHECKING:
    from apme_engine.remediation.structured import StructuredFile


class TransformResult(NamedTuple):
    """Result of applying a legacy (string-based) transform.

    Attributes:
        content: File content (possibly modified).
        applied: True if the transform made a change.
    """

    content: str
    applied: bool


TransformFn = Callable[[str, ViolationDict], TransformResult]
StructuredTransformFn = Callable[["StructuredFile", ViolationDict], bool]


class TransformRegistry:
    """Maps rule IDs to deterministic fix functions.

    The registry stores both structured and legacy transforms.
    ``apply_structured`` is preferred when a ``StructuredFile`` is
    available; ``apply`` falls back to re-parsing.
    """

    def __init__(self) -> None:
        """Initialize an empty transform registry."""
        self._transforms: dict[str, TransformFn] = {}
        self._structured: dict[str, StructuredTransformFn] = {}

    def register(
        self,
        rule_id: str,
        fn: TransformFn | None = None,
        *,
        structured: StructuredTransformFn | None = None,
    ) -> None:
        """Register transform function(s) for a rule ID.

        At least one of *fn* or *structured* must be provided.

        Args:
            rule_id: Rule identifier (e.g. L007, M001).
            fn: Legacy transform (content, violation) -> TransformResult.
            structured: Structured transform (StructuredFile, violation) -> bool.

        Raises:
            ValueError: If both *fn* and *structured* are None.
        """
        if fn is None and structured is None:
            msg = f"register({rule_id!r}): at least one of fn or structured required"
            raise ValueError(msg)
        if fn is not None:
            self._transforms[rule_id] = fn
        if structured is not None:
            self._structured[rule_id] = structured

    def __contains__(self, rule_id: str) -> bool:
        """Check if a rule has a registered transform.

        Args:
            rule_id: Rule identifier to look up.

        Returns:
            True if rule_id is registered (structured or legacy).
        """
        return rule_id in self._transforms or rule_id in self._structured

    def __len__(self) -> int:
        """Return the number of unique registered rule IDs.

        Returns:
            Count of registered rule IDs.
        """
        return len(set(self._transforms) | set(self._structured))

    def __iter__(self) -> Iterator[str]:
        """Iterate over registered rule IDs in sorted order.

        Returns:
            Iterator of rule ID strings (deterministic, sorted).
        """
        return iter(sorted(set(self._transforms) | set(self._structured)))

    @property
    def rule_ids(self) -> list[str]:
        """Return sorted list of registered rule IDs.

        Returns:
            Sorted list of rule ID strings.
        """
        return sorted(set(self._transforms) | set(self._structured))

    def apply_structured(
        self,
        rule_id: str,
        sf: StructuredFile,
        violation: ViolationDict,
    ) -> bool:
        """Apply a structured transform in-place on a StructuredFile.

        Falls back to the legacy string path if no structured transform
        is registered (re-parses from ``sf.serialize()``).

        Args:
            rule_id: Rule identifier.
            sf: StructuredFile to modify in-place.
            violation: Violation dict for context.

        Returns:
            True if the transform was applied.
        """
        sfn = self._structured.get(rule_id)
        if sfn is not None:
            applied = sfn(sf, violation)
            if applied:
                sf.mark_dirty()
            return applied

        fn = self._transforms.get(rule_id)
        if fn is None:
            return False
        result = fn(sf.serialize(), violation)
        if result.applied:
            new_sf = type(sf).from_content(sf.file_path, result.content)
            if new_sf is None:
                return False
            sf.data = new_sf.data
            sf._yaml = new_sf._yaml  # noqa: SLF001
            sf.mark_dirty()
            return True
        return False

    def apply(self, rule_id: str, content: str, violation: ViolationDict) -> TransformResult:
        """Apply the legacy string-based transform for a rule.

        Retained for backward compatibility; prefer ``apply_structured``.

        Args:
            rule_id: Rule identifier.
            content: File content string.
            violation: Violation dict for context.

        Returns:
            TransformResult with possibly modified content and applied flag.
        """
        fn = self._transforms.get(rule_id)
        if fn is None:
            return TransformResult(content=content, applied=False)
        return fn(content, violation)
