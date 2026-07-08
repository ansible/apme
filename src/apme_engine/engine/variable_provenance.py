"""Re-export shim — canonical location is ``apme_engine.graph.variable_provenance`` (ADR-059)."""

from apme_engine.graph.variable_provenance import PropertyOrigin as PropertyOrigin  # noqa: F401
from apme_engine.graph.variable_provenance import ProvenanceSource as ProvenanceSource  # noqa: F401
from apme_engine.graph.variable_provenance import VariableProvenance as VariableProvenance  # noqa: F401
from apme_engine.graph.variable_provenance import (  # noqa: F401
    VariableProvenanceResolver as VariableProvenanceResolver,
)
