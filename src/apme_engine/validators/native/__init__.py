"""ARI native validator: graph-based rule evaluation.

The legacy ``NativeValidator`` class (which used ``risk_detector.detect``
with ``AnsibleRunContext``) has been removed.  The native validator daemon
now runs ``GraphRule`` instances via ``graph_scanner.scan()`` exclusively.
"""

import os


def _default_rules_dir() -> str:
    """Return default path to native rules directory.

    Returns:
        Path to rules directory.
    """
    return os.path.join(os.path.dirname(__file__), "rules")
