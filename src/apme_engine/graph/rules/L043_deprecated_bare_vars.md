---
rule_id: L043
validator: native
description: Bare variable in with_* loop directive; use {{ var }}.
scope: task
---

## Deprecated bare vars (L043)

Detects bare variable names in ``with_*`` loop directives that should be
wrapped in Jinja2 delimiters (``{{ }}``).  Using ``{{ var }}`` inside a
normal string value is standard Jinja2 and is **not** flagged.

### Example: violation

```yaml
- name: Bare var in loop
  ansible.builtin.debug:
    msg: "{{ item }}"
  with_items: mylist
```

### Example: pass

```yaml
- name: Wrapped var in loop
  ansible.builtin.debug:
    msg: "{{ item }}"
  with_items: "{{ mylist }}"

- name: Normal Jinja2 in string value
  ansible.builtin.debug:
    msg: "Hello {{ username }}"
```
