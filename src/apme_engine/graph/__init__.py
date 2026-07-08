"""Shared graph analysis library for APME (ADR-059).

This package contains the ``ContentGraph`` data structure, graph rules,
scanning infrastructure, and shared type definitions used by both the
engine (Primary) and the native validator.  The package has **no**
dependency on ``apme_engine.engine`` — the dependency arrow points
from ``engine`` → ``graph``, never the reverse.
"""
