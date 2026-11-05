---
rule_id: M031
validator: native
description: Register sensitive-looking string values with register_secret for output masking (ansible-core 2.22+).
scope: task
ansible_core_version: ">=2.22"
---

## Register sensitive values for output masking (M031)

In ansible-core 2.22+, `ansible.builtin.register_secret` registers a string
with Ansible's secret masker. The real value remains usable in tasks, while
Ansible masks it at supported output boundaries. M031 uses sensitive-looking
variable names as a heuristic; it does not inspect values to determine whether
they are secrets.

### Example: violation

`no_log` hides the result of this task, but it does not register the fact for
masking when a later task uses it.

```yaml
- name: Keep the API token for later tasks
  ansible.builtin.set_fact:
    api_token: "{{ raw_token }}"
  no_log: true
```

### Example: pass

Apply the filter to the secret string assigned by `set_fact`. For a task
result, use dictionary-form `register` to extract and register the secret
string rather than passing the entire result mapping to the filter.

```yaml
- name: Store the database password
  ansible.builtin.set_fact:
    db_password: "{{ raw_password | ansible.builtin.register_secret }}"

- name: Fetch and register an API token
  ansible.builtin.uri:
    url: https://example.com/token
    return_content: true
  register:
    api_token: _task.result.json.token | ansible.builtin.register_secret
    task_result: _task.result
  no_log: true
```

Use `no_log: true` on tasks that produce sensitive results as defense in depth.
Task-level `no_log` hides that task's result; it does not register its values
for masking in later tasks. Module options declared with `no_log: true` by a
module author are handled separately by ansible-core.

Ansible automatically registers some values, including values loaded from
Vault-encrypted vars files. M031 does not infer whether a value was already
registered and still recommends explicit registration for sensitive-looking
variables. Registering a value explicitly makes the playbook's intent visible;
the filter returns the value unchanged.

`register_secret` accepts a non-empty string. Check optional or non-string
values before applying it; its `validation_action` option can change the
default error behavior, but ignored values are not registered.

Masking applies at Ansible-controlled output boundaries. It does not sanitize
arbitrary files, external logging, caches, or transformed copies such as
base64-encoded values. See the [ansible-core secret masking guide](https://docs.ansible.com/projects/ansible-core/2.22/playbook_guide/playbooks_secret_masking.html)
for the behavior and limitations.
