# Target User Personas

This document defines the primary users of APME and their needs.

## Personas

### Product Manager

**Role**: Needs visibility into migration progress and ROI metrics

**Goals**:
- Track modernization progress across repositories
- Report "time-saved" metrics to justify investment
- Understand blockers and risks

**Example Use Cases**:
```
"Show me how many playbooks have been modernized this quarter"
"What's our remediation rate across all teams?"
"How much time have we saved compared to manual modernization?"
```

### Automation Architect

**Role**: Defines corporate standards and custom policies

**Goals**:
- Create organization-wide Ansible standards
- Enforce policies across all teams
- Identify patterns of technical debt

**Example Use Cases**:
```
"Block any playbook using shell module where command suffices"
"Require all modules use FQCN format"
"Flag any playbook with hardcoded credentials"
```

### DevOps Engineer / Code Owner

**Role**: Needs actionable feedback and automated fixes

**Goals**:
- Get line-by-line remediation guidance
- Auto-fix simple issues
- Understand why changes are needed

**Example Use Cases**:
```
"Scan my playbook for AAP 2.5 compatibility"
"Auto-fix all FQCN issues in this repository"
"Show me what will break when we upgrade to Ansible 2.16"
```

## Persona-Driven Testing

When creating test cases for features, include scenarios for each persona:

| Persona | Test Focus |
|---------|------------|
| Product Manager | Dashboard, metrics, reports |
| Automation Architect | Policy creation, bulk analysis |
| DevOps Engineer | CLI, single-file scanning, auto-fix |
