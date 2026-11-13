"""Native validator: graph analysis and built-in static file checks.

Graph rules run through ``apme_engine.graph.scanner.scan()`` (ADR-059).
Molecule file rules assess the uploaded snapshot independently (ADR-076).
"""

from apme_engine.graph.scanner import native_rules_dir


def _default_rules_dir() -> str:
    """Return default path to native rules directory.

    Returns:
        Path to graph/rules directory (ADR-059).
    """
    return native_rules_dir()
