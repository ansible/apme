---
rule_id: M031
validator: native
description: Variables with sensitive names should use Sensitive tag (ansible-core 2.19+).
scope: task
ansible_core_version: ">=2.19"
---

## Sensitive tag recommendation (M031)

In ansible-core 2.19+, the Sensitive data tag enables automatic redaction of variable values in job output. Variables containing passwords, tokens, secrets, or API keys should use this tag for value-based redaction that propagates through Jinja templating.

### Example: violation

```yaml
- name: Set database credentials
  ansible.builtin.set_fact:
    db_password: "{{ vault_db_password }}"  # M031: sensitive variable should use Sensitive tag
```

### Example: compliant

```yaml
- name: Set database credentials
  ansible.builtin.set_fact:
    db_password: "{{ vault_db_password | ansible.builtin.sensitive }}"
```

### Alternative: no_log

Setting `no_log: true` on the task suppresses all output, which also addresses the concern:

```yaml
- name: Set database credentials
  ansible.builtin.set_fact:
    db_password: "{{ vault_db_password }}"
  no_log: true
```

### References

- [ansible-core 2.19 data tagging](https://docs.ansible.com/ansible/devel/)
- ANSTRAT-1720: Selective Redaction of Sensitive Variables in Job Output
