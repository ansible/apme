---
rule_id: R103
validator: native
description: Task executes content downloaded by a preceding task.
scope: task
ai_prompt: |
  This rule flags download-then-execute patterns — a supply chain risk.
  Check whether the download URL uses HTTPS from a trusted, pinned source
  and whether the downloaded artifact is verified (checksum, GPG). If the
  pattern is justified and verified, add "# noqa: R103" — but DO NOT
  modify the task itself. Your explanation MUST justify why the pattern is
  safe, e.g. "downloads from vendor HTTPS endpoint with checksum
  verification." If verification is missing, suggest adding checksum
  validation or using a package manager instead.
---

## Download exec (R103)

Checks APME's inbound and command module profiles. It searches preceding tasks in the same play for a templated download source, then checks whether that task's destination string appears in a later command. This is a local string-based analysis, not ARI annotation or path containment analysis.

### Example: violation

```yaml
- name: Test play
  hosts: localhost
  tasks:
    - name: Download script
      ansible.builtin.get_url:
        url: "{{ download_url }}"
        dest: /tmp/script.sh
    - name: Execute downloaded script
      ansible.builtin.command:
        cmd: /tmp/script.sh
```

### Example: pass

```yaml
- name: Simple command
  ansible.builtin.command:
    cmd: whoami
```
