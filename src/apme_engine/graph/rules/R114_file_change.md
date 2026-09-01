---
rule_id: R114
validator: native
description: File change (annotation-based).
scope: task
ai_prompt: |
  R114 flags file operations (copy, template, file, etc.) where path or src
  contains Jinja variables. This is a security risk when variables come from
  untrusted sources — attackers could manipulate paths to access/modify
  unintended files.

  RESOLUTION OPTIONS:

  1. If the variable comes from TRUSTED sources (role defaults, group_vars,
     playbook vars defined by the author):
     - Add "# noqa: R114" inline comment with justification
     - Example: "# noqa: R114 - path from role defaults, not user input"

  2. If the variable COULD come from external/untrusted input (inventory,
     extra_vars, API responses, registered output from commands):
     - Add an ansible.builtin.assert task BEFORE this task to validate
       the path is under an allowed base directory
       - Example validation (rejects '..' traversal; does not follow
         symlinks — do not use an allowed base that contains
         user-controlled symlinks):
       ```yaml
       - name: Validate path is under allowed base
         ansible.builtin.assert:
           that:
             - my_path is abs
             - "'..' not in my_path"
             - my_path is match('^/opt/server/')
           fail_msg: "Invalid path: {{ my_path }}"
       ```

  3. If path components come from a loop variable over trusted data:
     - The exemption applies if the loop source is trusted
     - Add "# noqa: R114 - loop over role-defined sites dict"

  When adding noqa, your explanation MUST state WHY the variable is trusted.
  "Variable is defined in defaults" is acceptable. "Variable is needed" is not.
---

## File change (R114)

File change with mutable path/src (annotation-based). Depends on FILE_CHANGE + is_mutable_path/is_mutable_src annotation.

### Example: pass

```yaml
- name: Copy file
  ansible.builtin.copy:
    src: files/config.yml
    dest: /etc/app/config.yml
```

### Example: fail

```yaml
- name: Copy file to user-specified path
  ansible.builtin.copy:
    src: files/config.yml
    dest: "{{ user_provided_path }}"  # R114 - path from variable
```

### Remediation

If the variable is trusted (from role defaults/vars):
```yaml
- name: Copy file to configured path
  ansible.builtin.copy:
    src: files/config.yml
    dest: "{{ app_config_path }}"  # noqa: R114 - path from role defaults
```

If the variable could be untrusted, add validation. Prefix match alone
is not enough — `/opt/myapp/../../etc/passwd` still matches
`^/opt/myapp/`. Reject `..` and require an absolute path. This example
does not resolve symlinks; do not use an allowed base that contains
user-controlled symlinks.

```yaml
- name: Validate destination path
  ansible.builtin.assert:
    that:
      - dest_path is abs
      - "'..' not in dest_path"
      - dest_path is match('^/opt/myapp/')
    fail_msg: "Invalid destination: {{ dest_path }}"

- name: Copy file to validated path
  ansible.builtin.copy:
    src: files/config.yml
    dest: "{{ dest_path }}"
```
