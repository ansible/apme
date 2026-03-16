# PHASE-002: Rewrite Engine

## Status

Not Started

## Overview

Automated "Rewrite" engine for the 50 most common module deprecations. Safe auto-fixes with diff generation.

## Goals

- Implement safe auto-fixes for renamed parameters and module redirects
- Support iterative processing for nested issues
- Generate before/after diff views for user approval
- Cover 50 most common deprecation patterns

## Success Criteria

- [ ] Auto-fix capability for 50+ common deprecations
- [ ] Iterative rewrite passes uncover nested issues
- [ ] Diff generation shows before/after changes
- [ ] No destructive changes without user approval

## Requirements

| REQ | Name | Status |
|-----|------|--------|
| — | No requirements (moved to PHASE-004) | — |

## Dependencies

- PHASE-001: CLI Scanner (must be complete)

## Timeline

- **Target Start**: TBD
- **Target Complete**: TBD
