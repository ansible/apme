---
rule_id: R103
validator: native
description: Task downloads and executes (annotation-based).
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
