# Project Overview: APME

## What is APME?

The **Ansible Playbook Modernization Engine (APME)** is an automated tool designed to help organizations modernize their Ansible playbooks for compatibility with Ansible Automation Platform (AAP) 2.5 and beyond.

## Problem Statement

Organizations with large Ansible codebases face significant challenges when upgrading to AAP 2.5+:

1. **FQCN Requirements**: AAP 2.5 requires Fully Qualified Collection Names for all modules
2. **Deprecated Modules**: Many legacy modules are deprecated or removed
3. **Syntax Changes**: Various syntax changes between Ansible versions
4. **Scale**: Manual remediation doesn't scale for thousands of playbooks

## Solution

APME provides:

1. **Automated Scanning**: Wraps ARI to detect all compatibility issues
2. **Intelligent Fixes**: Uses LangGraph agents to apply appropriate fixes
3. **Progress Tracking**: Dashboard showing modernization status
4. **CI/CD Integration**: Automated pre-flight checks

## Scope

### In Scope

- FQCN detection and automatic conversion
- Deprecated module detection and replacement suggestions
- Syntax modernization for AAP 2.5 compatibility
- Scan result aggregation and reporting
- Interactive dashboard for progress tracking
- GitHub Actions integration
- AAP pre-flight check integration

### Out of Scope

- Custom module development
- Playbook logic changes (only syntax/module updates)
- Performance optimization of playbooks
- Secret management or vault handling
- Inventory management

## Target Users

1. **Platform Engineers**: Managing AAP infrastructure upgrades
2. **DevOps Teams**: Maintaining playbook repositories
3. **Automation Architects**: Planning migration strategies

## Success Metrics

- Scan accuracy matches ARI baseline
- 90%+ of FQCN issues automatically fixable
- Dashboard loads 1000+ results in < 3 seconds
- CI integration runs in < 60 seconds per playbook

## Project Timeline

- **Week 1**: Foundation + Scanner implementation
- **Week 2**: Rewriter + Dashboard + Integration

## Related Projects

- **ARI (Ansible Risk Insights)**: The underlying scanning engine
- **x2a-convertor**: Reference implementation for conversion patterns
- **ansible-lint**: Integrated as Phase 5 of the fix pipeline (`ansible-lint --fix --profile production`)
