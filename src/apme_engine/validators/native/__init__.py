"""ARI native validator: graph-based rule evaluation.

Native rule evaluation runs ``GraphRule`` instances via
``apme_engine.graph.scanner.scan()`` exclusively (ADR-059).
"""

from apme_engine.graph.scanner import native_rules_dir


def _default_rules_dir() -> str:
    """Return default path to native rules directory.

    Returns:
        Path to graph/rules directory (ADR-059).
    """
    return native_rules_dir()
