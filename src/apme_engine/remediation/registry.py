"""Transform registry — maps rule IDs to deterministic fix functions."""

from __future__ import annotations

from typing import Callable, NamedTuple


class TransformResult(NamedTuple):
    content: str
    applied: bool


TransformFn = Callable[[str, dict], TransformResult]


class TransformRegistry:
    """Maps rule IDs to deterministic fix functions.

    A transform receives (file_content, violation_dict) and returns a
    TransformResult with the (possibly modified) content and a flag
    indicating whether a change was made.
    """

    def __init__(self) -> None:
        self._transforms: dict[str, TransformFn] = {}

    def register(self, rule_id: str, fn: TransformFn) -> None:
        self._transforms[rule_id] = fn

    def __contains__(self, rule_id: str) -> bool:
        return rule_id in self._transforms

    def __len__(self) -> int:
        return len(self._transforms)

    def __iter__(self):
        return iter(self._transforms)

    @property
    def rule_ids(self) -> list[str]:
        return sorted(self._transforms)

    def apply(self, rule_id: str, content: str, violation: dict) -> TransformResult:
        fn = self._transforms.get(rule_id)
        if fn is None:
            return TransformResult(content=content, applied=False)
        return fn(content, violation)
