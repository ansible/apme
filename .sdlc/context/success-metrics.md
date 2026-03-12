# Success Metrics (KPIs)

Key performance indicators for measuring APME project success.

## Primary Metrics

### Remediation Rate

**Definition**: Percentage of identified errors that can be fixed automatically

**Target**: >90% for standard modules

**Measurement**:
```
remediation_rate = (auto_fixed_issues / total_fixable_issues) × 100
```

**Tracking**:
- [ ] Baseline established
- [ ] Tracking implemented
- [ ] Target achieved

### Time Savings

**Definition**: Average reduction in hours per playbook modernized

**Target**: 8x faster than manual modernization

**Measurement**:
```
time_savings = manual_hours / apme_hours
```

**Baseline** (manual effort):
- Simple playbook: 2 hours
- Complex playbook: 8 hours
- Role: 4 hours
- Collection: 16 hours

**Tracking**:
- [ ] Baseline established
- [ ] Tracking implemented
- [ ] Target achieved

### Code Quality

**Definition**: Reduction in "High Risk" security findings post-scan

**Target**: 95% reduction in high-risk findings

**Measurement**:
```
quality_improvement = (pre_scan_high_risk - post_scan_high_risk) / pre_scan_high_risk × 100
```

**Tracking**:
- [ ] Baseline established
- [ ] Tracking implemented
- [ ] Target achieved

## Secondary Metrics

### Scan Coverage

**Definition**: Percentage of Ansible content scanned

**Target**: 100% of repositories in scope

### User Adoption

**Definition**: Number of active users per month

**Target**: 80% of target users actively using APME

### Policy Compliance

**Definition**: Percentage of code passing custom policies

**Target**: 95% compliance within 30 days of policy creation

## Reporting

Metrics should be visible in:
- [ ] CLI output (summary)
- [ ] Dashboard (detailed)
- [ ] CI/CD reports (JUnit/SARIF)

## Review Cadence

| Frequency | Review |
|-----------|--------|
| Weekly | Development team reviews progress |
| Monthly | Stakeholder metrics report |
| Quarterly | KPI target assessment |
