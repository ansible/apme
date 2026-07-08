---
rule_id: R103
validator: native
description: Task downloads and executes (annotation-based).
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

Task downloads and executes (annotation-based). Depends on annotations for INBOUND (mutable src) and CMD_EXEC.

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
