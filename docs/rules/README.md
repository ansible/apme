# Rule Reference

This directory contains **rule documentation** — the catalog of validation
rules, ID conventions, severity assignments, and coverage analysis.

## Contents

| Document | Description |
|----------|-------------|
| [RULE_CATALOG.md](RULE_CATALOG.md) | **Canonical reference** — all rules with severity (ADR-043), scope, remediation tier, implementation/test/doc/fixer status |
| [REMEDIATION_TIER_REPORT.md](REMEDIATION_TIER_REPORT.md) | Auto-generated remediation tier analysis and promotion candidates |
| [LINT_RULE_MAPPING.md](LINT_RULE_MAPPING.md) | ansible-lint name cross-references and historical renumbering |
| [RULE_DOC_FORMAT.md](RULE_DOC_FORMAT.md) | Frontmatter format for per-rule `.md` files |
| [ANSIBLELINT_COVERAGE.md](ANSIBLELINT_COVERAGE.md) | Coverage vs ansible-lint, gap analysis |

## Authoritative sources

| Concern | Source |
|---------|--------|
| Default severity | `src/apme_engine/severity_defaults.py` (ADR-043) |
| Remediation routing | `src/apme_engine/remediation/partition.py` (ADR-026) |
| Rule discovery / docs | `tools/generate_rule_catalog.py` |

Regenerate catalog and remediation report after rule changes:

```bash
python tools/generate_rule_catalog.py
```

## When to Add a Document Here

Add a document here when it relates to the rule system itself — new rule
categories, mapping tables, coverage reports, or documentation standards
for rule files. Per-rule examples belong next to the rule source (see
[RULE_DOC_FORMAT.md](RULE_DOC_FORMAT.md)).
