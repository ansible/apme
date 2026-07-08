"""ARI native validator: graph-based rule evaluation.

The legacy ``NativeValidator`` class (which used ``risk_detector.detect``
with ``AnsibleRunContext``) has been removed.  The native validator daemon
now runs ``GraphRule`` instances via ``apme_engine.graph.scanner.scan()``
exclusively (ADR-059).
"""

from apme_engine.graph.scanner import native_rules_dir


def _default_rules_dir() -> str:
    """Return default path to native rules directory.

    Returns:
        Path to graph/rules directory (ADR-059).
    """
    return native_rules_dir()
