"""Neutral static analysis for Molecule scenarios (legacy 24.9.0+, native 26.11.0+).

Molecule configuration describes more than what any single validator can see:
Native owns the file rules (L115-L119), while Ansible ``L057`` must know which
scenario playbooks are already assessed statically so it does not syntax-check
them a second time. Both read the same parsed/merged/provenance-annotated view
of the uploaded snapshot, so that view lives here rather than in a validator.

This package holds no validator wiring: it imports only
``apme_engine.graph.types`` and the vendored ``data`` snapshots. Rule metadata
(``MOLECULE_RULES``) is exported so the catalog can list the rules; the Native
validator remains the only executor (ADR-076).
"""

from __future__ import annotations

from apme_engine.molecule.analysis import (
    MOLECULE_RULES,
    MoleculeAssessmentLimitError,
    molecule_playbook_closure,
    molecule_playbook_paths,
    validate_molecule,
)

__all__ = [
    "MOLECULE_RULES",
    "MoleculeAssessmentLimitError",
    "molecule_playbook_closure",
    "molecule_playbook_paths",
    "validate_molecule",
]
