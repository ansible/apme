"""Project-relative path comparison helpers shared by Engine and graph.

Keep slash and ``./`` normalization in one place so binders and
remediation agree on whether two plugin ``file`` values name the same
path.
"""

from __future__ import annotations


def norm_relpath(path: str) -> str:
    """Normalize slashes and a leading ``./`` prefix without stripping dots.

    Args:
        path: Project-relative or plugin finding path.

    Returns:
        Normalized relative path.
    """
    text = path.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text
