"""CLI's contract with the engine — type definitions only.

This is the single file that bridges the CLI to engine types. Every CLI module
that needs engine type definitions imports from here, making the boundary
explicit and auditable.
"""

from apme_engine.engine.models import (
    ViolationDict,
    YAMLDict,
)

__all__ = [
    "ViolationDict",
    "YAMLDict",
]
