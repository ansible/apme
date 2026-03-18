# Terrible-playbook scan: expected vs reported

Comparison of what the playbook was designed to trigger (from inline comments) vs what the latest CLI scan reports.

## Current scan summary

- **Latest run:** terrible-playbook dir, chunked ScanStream; 124 issues (9 error, 80 warning, 21 info, 14 hint); **49 unique rule IDs** reported.
- **Sources:** Native + OPA + Ansible (Gitleaks 0).
- **Note:** Native validator was fixed (jsonpickle handler for AnsibleRunContext); it now contributes violations.

## Expected from playbook (from inline `# Lxxx` / `# Rxxx` / etc.)

Rules the playbook was designed to trigger, as documented in comments:

| Rule   | Comment / location |
|--------|---------------------|
| L003   | play without name (both plays) |
| L040   | tabs in YAML (playbook-l040-tabs.yml only) |
| L057   | wrong module FQCN (playbook-l057-wrong-module.yml + intended in site before move) |
| L020   | mode not string with leading zero (site.yml ~40) |
| L007   | shell → command (multiple) |
| L029   | shell instead of command |
| L025   | task name casing (many) |
| L021   | no mode on file/copy/template (multiple) |
| L013   | no changed_when (shell/command) |
| L046   | free-form command |
| L006   | command where a module exists |
| L002   | short module name / not FQCN |
| L026   | short module name |
| L030   | non-builtin (first copy task) |
| L017   | relative path in `src` |
| L012   | state=latest |
| L010   | ignore_errors |
| M006   | become + ignore_errors without ignore_unreachable |
| L015   | Jinja in when |
| L051   | bad Jinja spacing |
| L009   | comparison to empty string |
| L011   | literal true/false in when |
| L008   | local_action instead of delegate_to |
| L016   | pause without seconds |
| L014   | when result.changed instead of handler |
| L022   | shell pipe without pipefail |
| M009   | with_items / with_dict (deprecated loop) |
| R101   | parameterized command |
| R108   | privilege escalation |
| L047   | password without no_log |
| L048   | copy remote_src without owner |
| L044   | implicit state (user) |
| L045   | inline environment |
| L049   | loop var without prefix |
| R111   | parameterized role import |
| R112   | parameterized taskfile import |
| R113   | parameterized package install |
| R118   | download from external source |
| R103   | download and execute |
| R115   | file deletion |
| L043   | deprecated bare vars |
| L042   | high task count block |
| R107   | disable_gpg_check |
| L033   | unconditional override |
| R109   | key/config change |
| R114   | file change |
| R104   | unauthorized download source |
| R105   | outbound transfer |
| L036   | include_vars without when/tags |
| R106   | inbound transfer |
| L027   | role without metadata (broken_role) |
| L050   | non-lowercase variable names |
| M010   | Python 2 interpreter |
| L018   | become_user without become |
| L024   | task without name (bare shell) |
| L028   | task without name |
| P002/P003 | module argument validation (community.general.ini_file, cronvar) |
| L058/L059 | Ansible argspec (Ansible validator) |
| SEC / Gitleaks | hardcoded secrets (api_key, db_password, group_vars) |
| M011   | cisco.ios (optional; network module) |

## Reported in latest scan (unique rule IDs)

From `run-cli.sh --json .` on terrible-playbook (chunked gRPC), rule_id with native: prefix stripped:

**Reported (49):** L002, L003, L005, L006, L007, L008, L009, L010, L011, L012, L013, L014, L015, L016, L018, L020, L021, L022, L024, L025, L027, L028, L029, L032, L033, L036, L037, L038, L039, L042, L043, L044, L045, L046, L047, L048, L049, L050, L051, L057, M006, M009, M010, R104, R108, R111, R112, R113, R402

## Comparison: expected vs reported

### Reported and expected (we have these)

| Rule   | Status |
|--------|--------|
| L002   | ✓ reported |
| L005   | ✓ reported (related to L002/L030) |
| L007   | ✓ reported |
| L008   | ✓ reported |
| L009   | ✓ reported |
| L010   | ✓ reported |
| L011   | ✓ reported |
| L012   | ✓ reported |
| L014   | ✓ reported |
| L015   | ✓ reported |
| L016   | ✓ reported |
| L020   | ✓ reported |
| L021   | ✓ reported |
| L025   | ✓ reported |
| L027   | ✓ reported |
| L028   | ✓ reported |
| L029   | ✓ reported |
| L032   | ✓ reported (may overlap L033-style) |
| L033   | ✓ reported |
| L036   | ✓ reported |
| L039   | ✓ reported |
| L042   | ✓ reported |
| L043   | ✓ reported |
| L044   | ✓ reported |
| L045   | ✓ reported |
| L049   | ✓ reported |
| L050   | ✓ reported |
| L051   | ✓ reported |
| L057   | ✓ reported (from playbook-l040-tabs, playbook-l057-wrong-module, and/or site) |
| M006   | ✓ reported |
| R104   | ✓ reported |
| R108   | ✓ reported |
| R111   | ✓ reported |
| R112   | ✓ reported |
| R113   | ✓ reported |
| R118   | ✓ reported |
| R402   | ✓ reported (info-style; may be list-all-variables or similar) |

### Expected but NOT reported (gaps) — as of latest terrible-playbook run

**Now reported (previously gaps, now fixed):** L003, L006, L013, L018, L022, L024, L046, L047, L048, M009, M010.

| Rule   | Likely reason |
|--------|----------------|
| **L017**  | Extended OPA rule for relative `src`; playbook may use only role-relative `files/` (allowed) — confirm if any task has bad relative path. |
| **L026**  | Short module name — native rule exists; may overlap L002/L005. |
| **L030**  | Non-builtin — native rule exists; may overlap L002/L005. |
| **L040**  | Tabs in `playbook-l040-tabs.yml`; Ansible reports L057 (syntax) there; L040 may be subsumed or not in bundle. |
| **R101**  | Parameterized command — depends on annotator adding CMD_EXEC + is_mutable_cmd. |
| **R103**  | Download and execute — depends on CMD_EXEC + INBOUND annotations. |
| **R105**  | Outbound transfer — may not be implemented. |
| **R106**  | Inbound transfer — different criterion from R118. |
| **R107**  | disable_gpg_check — depends on package-install + insecure-option annotations. |
| **R109**  | Key/config change — depends on key/config annotator. |
| **R114**  | File change — depends on file-change annotator. |
| **R115**  | File deletion — depends on file-deletion annotator. |
| **R118**  | Download from external source — was in older run; if missing, confirm playbook has trigger. |
| **P002/P003** | Ansible validator module argument validation; needs argspec/collections. |
| **L058/L059** | Ansible argspec — Ansible validator. |
| **SEC / Gitleaks** | No findings — secrets may be below threshold or not scanned. |
| **M011**  | cisco.ios (optional). |

### Reported but not in “expected” list above

- **L005** — Use only ansible.builtin/ansible.legacy (related to L002/L030).
- **L032, L037, L038, L039** — Additional native/OPA rules that fired (e.g. undefined variable, naming, or structural).
- **R402** — Appears in native output (e.g. list-all-used-variables style).

## Do we have all expected?

**No, but we get most of them.**

- **Reported and expected:** 49 unique rule IDs in latest run; of the ~55 expected rules, **38 are reported** (L002, L003, L005, L006, L007, L008, L009, L010, L011, L012, L013, L014, L015, L016, L018, L020, L021, L022, L024, L025, L027, L028, L029, L032, L033, L036, L042, L043, L044, L045, L046, L047, L048, L049, L050, L051, L057, M006, M009, M010, R104, R108, R111, R112, R113; plus L037, L038, L039, R402 which overlap or are extra).
- **Expected but still not reported:** L017 (maybe no trigger), L026, L030, L040, R101, R103, R105, R106, R107, R109, R114, R115, R118, P002, P003, L058, L059, SEC/Gitleaks, M011 — see table above.

## Next steps to get “all expected”

1. **Native/OPA rule coverage** — For each gap (L003, L006, L013, L017, L018, L022, L024, L026, L030, L046, L047, L048, M009, M010, R101, R103, R105–R107, R109, R114, R115): confirm whether the rule exists in the bundle or native rules and is enabled; add or enable if missing.
2. **Ansible validator** — L058, L059, P002, P003 require Ansible argspec/collections; ensure community.general (and any other used collections) are available and that these rules run on the intended tasks.
3. **Gitleaks/SEC** — Ensure demo secrets (api_key, db_password, group_vars, etc.) are in scanned content and that patterns/thresholds would flag them.
4. **L040** — Confirm whether L040 is in the OPA/native set and whether it’s expected to fire on `playbook-l040-tabs.yml` or is subsumed by Ansible’s syntax error.
