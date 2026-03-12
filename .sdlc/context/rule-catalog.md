# Rule Catalog

This comprehensive rule catalogue outlines ninety-three individual guidelines designed to ensure quality, security, and standardisation across automated workflows. The framework is organised into four distinct validation categories—OPA, Native, Ansible, and Gitleaks—covering critical areas such as code syntax, module deprecation, and the detection of sensitive credentials. A vital feature of this system is the inclusion of twenty deterministic fixers, which allow for the automatic correction of specific common errors. Ultimately, these rules serve as a structured roadmap for developers to maintain robust and secure configurations by transitioning from manual reviews to efficient, machine-led validations.

---

**93 rules across 4 validators | 20 deterministic fixers registered**

---

## All Rules

| Rule ID | Validator | Description | Fixer |
|---------|-----------|-------------|-------|
| L002 | OPA | Use fully qualified collection name for modules. | Yes |
| L003 | OPA | Each play should have a name. | |
| L004 | OPA | Do not use deprecated modules. | |
| L005 | OPA | Use ansible.builtin or ansible.legacy. | |
| L006 | OPA | Use dedicated module instead of command. | |
| L007 | OPA | Prefer command when no shell features needed. | Yes |
| L008 | OPA | Use delegate_to: localhost instead of local_action. | Yes |
| L009 | OPA | Avoid comparison to empty string in when. | Yes |
| L010 | OPA | Use failed_when or register instead of ignore_errors. | |
| L011 | OPA | Avoid literal true/false in when. | Yes |
| L012 | OPA | Avoid state=latest; pin package versions. | Yes |
| L013 | OPA | command/shell/raw need changed_when or creates/removes. | Yes |
| L014 | OPA | Use notify/handler instead of when: result.changed. | |
| L015 | OPA | Avoid Jinja in when; use variables. | Yes |
| L016 | OPA | pause without seconds/minutes prompts for input. | |
| L017 | OPA | Avoid relative path in src. | |
| L018 | OPA | become_user should have corresponding become. | Yes |
| L019 | OPA | Playbook should have .yml or .yaml extension. | |
| L020 | OPA | mode should be string with leading zero. | Yes |
| L021 | OPA | Set mode explicitly for file/copy/template. | Yes |
| L022 | OPA | Shell with pipe should set set -o pipefail. | Yes |
| L023 | OPA | Consider whether run_once is appropriate. | |
| L024 | OPA | Task should have a name. | |
| L025 | OPA | Task/play name should start with uppercase. | Yes |
| L026 | Native | Tasks should use FQCN for modules. | |
| L027 | Native | Roles should have meta/main.yml with metadata. | |
| L028 | Native | Tasks should have a name. | |
| L029 | Native | Prefer command over shell when no shell features are needed | |
| L030 | Native | Prefer ansible.builtin modules when available. | |
| L031 | Native | File permission may be insecure (annotation-based). | |
| L032 | Native | Variable redefinition may cause confusion. | |
| L033 | Native | Overriding vars without conditions. | |
| L034 | Native | Lower-precedence override may be unused. | |
| L035 | Native | set_fact with random in args. | |
| L036 | Native | include_vars without when/tags. | |
| L037 | Native | Module name could not be resolved. | |
| L038 | Native | Role could not be resolved. | |
| L039 | Native | Variable use may be undefined. | |
| L040 | Native | YAML should not contain tabs; use spaces. | |
| L041 | Native | Task keys should follow canonical order (e.g. name before module). | |
| L042 | Native | Play/block has high task count. | |
| L043 | Native | Avoid {{ foo }}; prefer explicit form. | Yes |
| L044 | Native | Set state explicitly where it matters. | |
| L045 | Native | Avoid inline environment in tasks. | |
| L046 | Native | Avoid raw/command/shell without args key. | Yes |
| L047 | Native | Set no_log for password-like parameters. | |
| L048 | Native | copy with remote_src should set owner. | |
| L049 | Native | Loop variable should use prefix (e.g. item_). | |
| L050 | Native | Variable names: lowercase, underscores. | |
| L051 | Native | Jinja spacing: {{ var }} not {{var}}. | |
| L052 | Native | Galaxy version in meta should be semantic. | |
| L053 | Native | Role meta should have valid structure. | |
| L054 | Native | Role meta galaxy_info should include galaxy_tags. | |
| L055 | Native | Role meta video_links should be valid URLs. | |
| L056 | Native | Path may match ignore pattern. | |
| L057 | Ansible | Syntax check via ansible-playbook --syntax-check. | |
| L058 | Ansible | Argspec validation (docstring-based). | |
| L059 | Ansible | Argspec validation (mock/patch-based). | |
| M001 | Ansible | FQCN resolution — module resolved to a different canonical name. | Yes |
| M002 | Ansible | Deprecated module — module has deprecation metadata. | |
| M003 | Ansible | Module redirect — module name was redirected to a new FQCN. | Yes |
| M004 | Ansible | Removed module — tombstoned module that raises AnsiblePluginRemovedError. | |
| M005 | Native | Registered variable used in Jinja template may be untrusted in 2.19+. | |
| M006 | OPA | become with ignore_errors will not catch timeout in 2.19+. | Yes |
| M008 | OPA | Bare include is removed in 2.19+; use include_tasks or import_tasks. | Yes |
| M009 | OPA | with_* loops are deprecated; use loop instead. | Yes |
| M010 | Native | ansible_python_interpreter set to Python 2; dropped in 2.18+. | |
| M011 | OPA | Network module may require collection upgrade for 2.19+ compatibility. | |
| P001 | Native | Validate module name (Ansible required). | |
| P002 | Native | Validate module argument keys (Ansible required). | |
| P003 | Native | Validate module argument values (Ansible required). | |
| P004 | Native | Validate variables (Ansible required). | |
| R101 | Native | Task executes parameterized command (annotation-based) | |
| R103 | Native | Task downloads and executes (annotation-based). | |
| R104 | Native | Download from unauthorized source (annotation-based). | |
| R105 | Native | Outbound transfer (annotation-based). | |
| R106 | Native | Inbound transfer (annotation-based). | |
| R107 | Native | Package install with insecure option (annotation-based). | |
| R108 | Native | Privilege escalation (annotation-based). | |
| R109 | Native | Key/config change (annotation-based). | |
| R111 | Native | Parameterized role import (annotation-based). | |
| R112 | Native | Parameterized taskfile import (annotation-based). | |
| R113 | Native | Parameterized package install (annotation-based). | |
| R114 | Native | File change (annotation-based). | |
| R115 | Native | File deletion (annotation-based). | |
| R117 | Native | Role is from Galaxy/external source. | |
| R118 | OPA | Task downloads from an external source (inbound transfer). | |
| R401 | Native | Report inbound transfer sources. | |
| R402 | Native | Report variables used at end of sequence. | |
| R404 | Native | Expose variable_set for the task. | |
| R501 | Native | Suggest collection/role dependency. | |
| SEC:* | Gitleaks | Secret/credential detection (delegated to Gitleaks binary). | |
| Sample101 | Native | Example rule that returns task block. | |

---

## By Validator

### OPA (29 rules, 16 fixers)

| Rule ID | Description | Fixer |
|---------|-------------|-------|
| L002 | Use fully qualified collection name for modules. | Yes |
| L003 | Each play should have a name. | |
| L004 | Do not use deprecated modules. | |
| L005 | Use ansible.builtin or ansible.legacy. | |
| L006 | Use dedicated module instead of command. | |
| L007 | Prefer command when no shell features needed. | Yes |
| L008 | Use delegate_to: localhost instead of local_action. | Yes |
| L009 | Avoid comparison to empty string in when. | Yes |
| L010 | Use failed_when or register instead of ignore_errors. | |
| L011 | Avoid literal true/false in when. | Yes |
| L012 | Avoid state=latest; pin package versions. | Yes |
| L013 | command/shell/raw need changed_when or creates/removes. | Yes |
| L014 | Use notify/handler instead of when: result.changed. | |
| L015 | Avoid Jinja in when; use variables. | Yes |
| L016 | pause without seconds/minutes prompts for input. | |
| L017 | Avoid relative path in src. | |
| L018 | become_user should have corresponding become. | Yes |
| L019 | Playbook should have .yml or .yaml extension. | |
| L020 | mode should be string with leading zero. | Yes |
| L021 | Set mode explicitly for file/copy/template. | Yes |
| L022 | Shell with pipe should set set -o pipefail. | Yes |
| L023 | Consider whether run_once is appropriate. | |
| L024 | Task should have a name. | |
| L025 | Task/play name should start with uppercase. | Yes |
| M006 | become with ignore_errors will not catch timeout in 2.19+. | Yes |
| M008 | Bare include is removed in 2.19+; use include_tasks or import_tasks. | Yes |
| M009 | with_* loops are deprecated; use loop instead. | Yes |
| M011 | Network module may require collection upgrade for 2.19+ compatibility. | |
| R118 | Task downloads from an external source (inbound transfer). | |

### Native (56 rules, 2 fixers)

| Rule ID | Description | Fixer |
|---------|-------------|-------|
| L026 | Tasks should use FQCN for modules. | |
| L027 | Roles should have meta/main.yml with metadata. | |
| L028 | Tasks should have a name. | |
| L029 | Prefer command over shell when no shell features are needed | |
| L030 | Prefer ansible.builtin modules when available. | |
| L031 | File permission may be insecure (annotation-based). | |
| L032 | Variable redefinition may cause confusion. | |
| L033 | Overriding vars without conditions. | |
| L034 | Lower-precedence override may be unused. | |
| L035 | set_fact with random in args. | |
| L036 | include_vars without when/tags. | |
| L037 | Module name could not be resolved. | |
| L038 | Role could not be resolved. | |
| L039 | Variable use may be undefined. | |
| L040 | YAML should not contain tabs; use spaces. | |
| L041 | Task keys should follow canonical order (e.g. name before module). | |
| L042 | Play/block has high task count. | |
| L043 | Avoid {{ foo }}; prefer explicit form. | Yes |
| L044 | Set state explicitly where it matters. | |
| L045 | Avoid inline environment in tasks. | |
| L046 | Avoid raw/command/shell without args key. | Yes |
| L047 | Set no_log for password-like parameters. | |
| L048 | copy with remote_src should set owner. | |
| L049 | Loop variable should use prefix (e.g. item_). | |
| L050 | Variable names: lowercase, underscores. | |
| L051 | Jinja spacing: {{ var }} not {{var}}. | |
| L052 | Galaxy version in meta should be semantic. | |
| L053 | Role meta should have valid structure. | |
| L054 | Role meta galaxy_info should include galaxy_tags. | |
| L055 | Role meta video_links should be valid URLs. | |
| L056 | Path may match ignore pattern. | |
| M005 | Registered variable used in Jinja template may be untrusted in 2.19+. | |
| M010 | ansible_python_interpreter set to Python 2; dropped in 2.18+. | |
| P001 | Validate module name (Ansible required). | |
| P002 | Validate module argument keys (Ansible required). | |
| P003 | Validate module argument values (Ansible required). | |
| P004 | Validate variables (Ansible required). | |
| R101 | Task executes parameterized command (annotation-based) | |
| R103 | Task downloads and executes (annotation-based). | |
| R104 | Download from unauthorized source (annotation-based). | |
| R105 | Outbound transfer (annotation-based). | |
| R106 | Inbound transfer (annotation-based). | |
| R107 | Package install with insecure option (annotation-based). | |
| R108 | Privilege escalation (annotation-based). | |
| R109 | Key/config change (annotation-based). | |
| R111 | Parameterized role import (annotation-based). | |
| R112 | Parameterized taskfile import (annotation-based). | |
| R113 | Parameterized package install (annotation-based). | |
| R114 | File change (annotation-based). | |
| R115 | File deletion (annotation-based). | |
| R117 | Role is from Galaxy/external source. | |
| R401 | Report inbound transfer sources. | |
| R402 | Report variables used at end of sequence. | |
| R404 | Expose variable_set for the task. | |
| R501 | Suggest collection/role dependency. | |
| Sample101 | Example rule that returns task block. | |

### Ansible (7 rules, 2 fixers)

| Rule ID | Description | Fixer |
|---------|-------------|-------|
| L057 | Syntax check via ansible-playbook --syntax-check. | |
| L058 | Argspec validation (docstring-based). | |
| L059 | Argspec validation (mock/patch-based). | |
| M001 | FQCN resolution — module resolved to a different canonical name. | Yes |
| M002 | Deprecated module — module has deprecation metadata. | |
| M003 | Module redirect — module name was redirected to a new FQCN. | Yes |
| M004 | Removed module — tombstoned module that raises AnsiblePluginRemovedError. | |

### Gitleaks (1 rule, 0 fixers)

| Rule ID | Description | Fixer |
|---------|-------------|-------|
| SEC:* | Secret/credential detection (delegated to Gitleaks binary). | |

---

## Fixer Summary

Deterministic fixers (Tier 1) are auto-applied by `apme-scan fix --apply`. Rules without fixers fall to Tier 2 (AI-proposable) or Tier 3 (manual review).

| Rule ID | Transform |
|---------|-----------|
| L002 | Use fully qualified collection name for modules. |
| L007 | Prefer command when no shell features needed. |
| L008 | Use delegate_to: localhost instead of local_action. |
| L009 | Avoid comparison to empty string in when. |
| L011 | Avoid literal true/false in when. |
| L012 | Avoid state=latest; pin package versions. |
| L013 | command/shell/raw need changed_when or creates/removes. |
| L015 | Avoid Jinja in when; use variables. |
| L018 | become_user should have corresponding become. |
| L020 | mode should be string with leading zero. |
| L021 | Set mode explicitly for file/copy/template. |
| L022 | Shell with pipe should set set -o pipefail. |
| L025 | Task/play name should start with uppercase. |
| L043 | Avoid {{ foo }}; prefer explicit form. |
| L046 | Avoid raw/command/shell without args key. |
| M001 | FQCN resolution — module resolved to a different canonical name. |
| M003 | Module redirect — module name was redirected to a new FQCN. |
| M006 | become with ignore_errors will not catch timeout in 2.19+. |
| M008 | Bare include is removed in 2.19+; use include_tasks or import_tasks. |
| M009 | with_* loops are deprecated; use loop instead. |

---

## Related Documents

- [ADR-008: Rule ID Conventions](/.sdlc/adrs/ADR-008-rule-id-conventions.md) — Rule ID prefix meanings (L/M/R/P/SEC)
- [ADR-009: Remediation Engine](/.sdlc/adrs/ADR-009-remediation-engine.md) — Tiered remediation architecture
- [rule-doc-format.md](rule-doc-format.md) — Documentation format for individual rules
- [architecture.md](architecture.md) — Validator service architecture
