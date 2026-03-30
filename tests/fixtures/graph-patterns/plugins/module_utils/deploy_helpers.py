"""Shared utility for deploy_artifact module.

GraphBuilder should detect a ``py_imports`` edge from
``deploy_artifact.py`` to this module.
"""

from __future__ import annotations


def validate_artifact(name: str, version: str) -> bool:
    """Check artifact spec is well-formed.

    Args:
        name: Artifact name.
        version: Artifact version.

    Returns:
        True if both name and version are non-empty.
    """
    return bool(name) and bool(version)
