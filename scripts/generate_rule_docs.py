#!/usr/bin/env python3
"""Generate all rule .md files with frontmatter and Example: violation / pass. Run from repo root."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NATIVE_RULES = REPO_ROOT / "src" / "apme_engine" / "validators" / "native" / "rules"
OPA_BUNDLE = REPO_ROOT / "src" / "apme_engine" / "validators" / "opa" / "bundle"

PLAYBOOK_WRAP = """- name: Example play
  hosts: localhost
  connection: local
  tasks:
"""


def playbook(*tasks_yaml: str) -> str:
    lines = []
    for t in tasks_yaml:
        for line in t.strip().splitlines():
            lines.append("    " + line)
    return PLAYBOOK_WRAP + "\n".join(lines)


def write_native(name: str, rule_id: str, title: str, desc: str, violation: str, pass_yaml: str):
    path = NATIVE_RULES / f"{name}.md"
    path.write_text(
        f"""---
rule_id: {rule_id}
validator: native
description: {desc}
---

## {title}

{desc}

### Example: violation

```yaml
{violation}
```

### Example: pass

```yaml
{pass_yaml}
```
""",
        encoding="utf-8",
    )
    print(path.name)


def write_opa(num: int, rule_id: str, title: str, desc: str, violation: str, pass_yaml: str):
    path = OPA_BUNDLE / f"L{num:03d}.md"
    path.write_text(
        f"""---
rule_id: {rule_id}
validator: opa
description: {desc}
---

## {title}

{desc}

### Example: violation

```yaml
{violation}
```

### Example: pass

```yaml
{pass_yaml}
```
""",
        encoding="utf-8",
    )
    print(path.name)


def main():
    pb_v = playbook("- name: Bad\n  ansible.builtin.shell: whoami")
    pb_p = playbook("- name: Ok\n  ansible.builtin.command: whoami")

    # R103-R109, R111-R116 (convert to frontmatter)
    for stub, rid, title, desc in [
        ("R103_download_exec", "R103", "Download exec (R103)", "Task downloads and executes (annotation-based)."),
        ("R104_unauthorized_download_src", "R104", "Unauthorized download (R104)", "Download from unauthorized source (annotation-based)."),
        ("R105_outbound_transfer", "R105", "Outbound transfer (R105)", "Outbound transfer (annotation-based)."),
        ("R106_inbound_transfer", "R106", "Inbound transfer (R106)", "Inbound transfer (annotation-based)."),
        ("R107_pkg_install_with_insecure_option", "R107", "Pkg install insecure (R107)", "Package install with insecure option (annotation-based)."),
        ("R108_privilege_escalation", "R108", "Privilege escalation (R108)", "Privilege escalation (annotation-based)."),
        ("R109_key_config_change", "R109", "Key config change (R109)", "Key/config change (annotation-based)."),
        ("R111_parameterized_import_role", "R111", "Parameterized import role (R111)", "Parameterized role import (annotation-based)."),
        ("R112_parameterized_import_taskfile", "R112", "Parameterized import taskfile (R112)", "Parameterized taskfile import (annotation-based)."),
        ("R113_parameterized_pkg_install", "R113", "Parameterized pkg install (R113)", "Parameterized package install (annotation-based)."),
        ("R114_file_change", "R114", "File change (R114)", "File change (annotation-based)."),
        ("R115_file_deletion", "R115", "File deletion (R115)", "File deletion (annotation-based)."),
        ("R116_insecure_file_permission", "L031", "Insecure file permission (L031)", "File permission may be insecure (annotation-based)."),
    ]:
        write_native(stub, rid, title, desc, pb_v, pb_p)

    write_native(
        "R301_non_fqcn_use",
        "L026",
        "Non-FQCN use (L026)",
        "Tasks should use FQCN for modules.",
        playbook("- name: Install\n  ansible_galaxy_install:\n    name: foo"),
        playbook("- name: Install\n  community.general.ansible_galaxy_install:\n    name: foo"),
    )
    write_native(
        "R110_non_builtin_use",
        "L030",
        "Non-builtin use (L030)",
        "Prefer ansible.builtin modules when available.",
        playbook("- name: Copy\n  copy:\n    src: a\n    dest: /tmp/b"),
        playbook("- name: Copy\n  ansible.builtin.copy:\n    src: a\n    dest: /tmp/b"),
    )

    for stub, rid, title, desc in [
        ("R302_role_without_metadata", "L027", "Role without metadata (L027)", "Roles should have meta/main.yml with metadata."),
        ("R303_task_without_name", "L028", "Task without name (L028)", "Tasks should have a name."),
        ("R201_changed_data_dependence", "L032", "Changed data dependence (L032)", "Variable redefinition may cause confusion."),
        ("R202_unconditional_override", "L033", "Unconditional override (L033)", "Overriding vars without conditions."),
        ("R203_unused_override", "L034", "Unused override (L034)", "Lower-precedence override may be unused."),
        ("R204_unnecessary_set_fact", "L035", "Unnecessary set_fact (L035)", "set_fact with random in args."),
        ("R205_unnecessary_include_vars", "L036", "Unnecessary include_vars (L036)", "include_vars without when/tags."),
        ("R304_unresolved_module", "L037", "Unresolved module (L037)", "Module name could not be resolved."),
        ("R305_unresolved_role", "L038", "Unresolved role (L038)", "Role could not be resolved."),
        ("R306_undefined_variable", "L039", "Undefined variable (L039)", "Variable use may be undefined."),
        ("R117_external_role", "R117", "External role (R117)", "Role is from Galaxy/external source."),
        ("R401_list_all_inbound_src", "R401", "List inbound sources (R401)", "Report inbound transfer sources."),
        ("R402_list_all_used_variables", "R402", "List used variables (R402)", "Report variables used at end of sequence."),
        ("R404_show_variables", "R404", "Show variables (R404)", "Expose variable_set for the task."),
        ("R501_dependency_suggestion", "R501", "Dependency suggestion (R501)", "Suggest collection/role dependency."),
    ]:
        write_native(stub, rid, title, desc, pb_v, pb_p)

    for stub, rid, title, desc in [
        ("P001_module_name_validation", "P001", "Module name validation (P001)", "Validate module name (Ansible required)."),
        ("P002_module_argument_key_validation", "P002", "Module argument key (P002)", "Validate module argument keys (Ansible required)."),
        ("P003_module_argument_value_validation", "P003", "Module argument value (P003)", "Validate module argument values (Ansible required)."),
        ("P004_variable_validation", "P004", "Variable validation (P004)", "Validate variables (Ansible required)."),
        ("sample_rule", "Sample101", "Sample rule (Sample101)", "Example rule that returns task block."),
    ]:
        write_native(stub, rid, title, desc, pb_v, pb_p)

    for stub, rid, title, desc in [
        ("L040_no_tabs", "L040", "No tabs (L040)", "YAML should not contain tabs; use spaces."),
        ("L041_key_order", "L041", "Key order (L041)", "Task keys should follow canonical order (e.g. name before module)."),
        ("L042_complexity", "L042", "Complexity (L042)", "Play/block has high task count."),
        ("L043_deprecated_bare_vars", "L043", "Deprecated bare vars (L043)", "Avoid {{ foo }}; prefer explicit form."),
        ("L044_avoid_implicit", "L044", "Avoid implicit (L044)", "Set state explicitly where it matters."),
        ("L045_inline_env_var", "L045", "Inline env var (L045)", "Avoid inline environment in tasks."),
        ("L046_no_free_form", "L046", "No free form (L046)", "Avoid raw/command/shell without args key."),
        ("L047_no_log_password", "L047", "No log password (L047)", "Set no_log for password-like parameters."),
        ("L048_no_same_owner", "L048", "No same owner (L048)", "copy with remote_src should set owner."),
        ("L049_loop_var_prefix", "L049", "Loop var prefix (L049)", "Loop variable should use prefix (e.g. item_)."),
        ("L050_var_naming", "L050", "Var naming (L050)", "Variable names: lowercase, underscores."),
        ("L051_jinja", "L051", "Jinja (L051)", "Jinja spacing: {{ var }} not {{var}}."),
        ("L052_galaxy_version_incorrect", "L052", "Galaxy version (L052)", "Galaxy version in meta should be semantic."),
        ("L053_meta_incorrect", "L053", "Meta incorrect (L053)", "Role meta should have valid structure."),
        ("L054_meta_no_tags", "L054", "Meta no tags (L054)", "Role meta galaxy_info should include galaxy_tags."),
        ("L055_meta_video_links", "L055", "Meta video links (L055)", "Role meta video_links should be valid URLs."),
        ("L056_sanity", "L056", "Sanity (L056)", "Path may match ignore pattern."),
    ]:
        write_native(stub, rid, title, desc, pb_v, pb_p)

    for num, rid, title, desc in [
        (2, "L002", "Use FQCN", "Use fully qualified collection name for modules."),
        (3, "L003", "Play should have name", "Each play should have a name."),
        (4, "L004", "Deprecated module", "Do not use deprecated modules."),
        (5, "L005", "Only builtins", "Use ansible.builtin or ansible.legacy."),
        (6, "L006", "Command instead of module", "Use dedicated module instead of command."),
        (7, "L007", "Command instead of shell", "Prefer command when no shell features needed."),
        (8, "L008", "No local_action", "Use delegate_to: localhost instead of local_action."),
        (9, "L009", "Empty string compare", "Avoid comparison to empty string in when."),
        (10, "L010", "Ignore errors", "Use failed_when or register instead of ignore_errors."),
        (11, "L011", "Literal compare", "Avoid literal true/false in when."),
        (12, "L012", "Latest", "Avoid state=latest; pin package versions."),
        (13, "L013", "No changed_when", "command/shell/raw need changed_when or creates/removes."),
        (14, "L014", "No handler", "Use notify/handler instead of when: result.changed."),
        (15, "L015", "No Jinja in when", "Avoid Jinja in when; use variables."),
        (16, "L016", "No prompting", "pause without seconds/minutes prompts for input."),
        (17, "L017", "No relative paths", "Avoid relative path in src."),
        (18, "L018", "Partial become", "become_user should have corresponding become."),
        (19, "L019", "Playbook extension", "Playbook should have .yml or .yaml extension."),
        (20, "L020", "Risky octal", "mode should be string with leading zero."),
        (21, "L021", "Risky file permissions", "Set mode explicitly for file/copy/template."),
        (22, "L022", "Risky shell pipe", "Shell with pipe should set set -o pipefail."),
        (23, "L023", "Run once", "Consider whether run_once is appropriate."),
        (24, "L024", "Task should have name", "Task should have a name."),
        (25, "L025", "Name casing", "Task/play name should start with uppercase."),
    ]:
        write_opa(num, rid, title, desc, pb_v, pb_p)

    print("Done.")


if __name__ == "__main__":
    main()
