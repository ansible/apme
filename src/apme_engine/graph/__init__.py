"""Shared graph analysis library for APME (ADR-059).

This package contains the ``ContentGraph`` data structure, graph rules,
scanning infrastructure, and shared type definitions used by both the
engine (Primary) and the native validator.  The dependency arrow points
from ``engine`` → ``graph``, never the reverse.  A small number of
lazy imports back into ``engine`` are allowed for implementation reasons
(e.g. ``engine.yaml_utils`` from ``content_graph.py``); these are
enumerated in ``tools/check_graph_boundary.py`` and enforced by a
pre-commit hook.
"""
