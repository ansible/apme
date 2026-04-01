"""ScanContext and Validator protocol for all validation backends."""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from apme_engine.engine.models import ViolationDict, YAMLDict


@dataclass
class EngineDiagnostics:
    """Timing data collected during engine phases (populated by run_scan).

    Attributes:
        parse_ms: Time spent parsing.
        annotate_ms: Time spent annotating.
        tree_build_ms: Time spent building the content graph.
        variable_resolution_ms: Time spent on variable resolution.
        total_ms: Total time.
        files_scanned: Number of files scanned.
        graph_nodes_built: Number of ContentGraph nodes built.
    """

    parse_ms: float = 0.0
    annotate_ms: float = 0.0
    tree_build_ms: float = 0.0
    variable_resolution_ms: float = 0.0
    total_ms: float = 0.0
    files_scanned: int = 0
    graph_nodes_built: int = 0


@dataclass
class ScanContext:
    """What validators receive. Extensible so different backends get what they need.

    Attributes:
        hierarchy_payload: Payload with hierarchy data.
        scandata: jsonpickle-decoded scan context.
        root_dir: Root directory for scan.
        engine_diagnostics: Timing data from engine phases.
    """

    hierarchy_payload: YAMLDict
    scandata: object = None  # jsonpickle-decoded scan context
    root_dir: str = ""
    engine_diagnostics: EngineDiagnostics = field(default_factory=EngineDiagnostics)


@runtime_checkable
class Validator(Protocol):
    """Any backend that can produce violations from a scan."""

    def run(self, context: ScanContext) -> list[ViolationDict]:
        """Return list of violation dicts (rule_id, level, message, file, line, path).

        Args:
            context: Scan context passed to validator.

        Returns:
            List of violation dicts.
        """
        ...
