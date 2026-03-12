# PHASE-001: CLI Scanner

## Status

In Progress

## Overview

Static CLI scanner with FQCN conversion and basic error reporting. Foundation for all scanning capabilities.

## Goals

- Implement version-specific analysis against target Ansible versions
- Categorize issues as Errors, Warnings, and Hints
- Transform short-form module names to FQCN format
- Provide CLI tooling for local developer use

## Success Criteria

- [ ] Scanner detects compatibility issues for Ansible 2.16+
- [ ] Issues categorized by severity (Error/Warning/Hint)
- [ ] FQCN transformation suggestions provided
- [ ] CLI outputs JSON, JUnit, and text formats

## Requirements

| REQ | Name | Status |
|-----|------|--------|
| REQ-001 | Core Scanning Engine | In Progress |

## Dependencies

- None (foundation phase)

## Timeline

- **Target Start**: 2024-Q1
- **Target Complete**: TBD
