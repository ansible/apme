---
rule_id: L097
validator: native
description: Task names should be unique within a play.
scope: playbook
---

## Name unique (L097)

Each task name should be unique within the same play to make log output and debugging clear. Duplicate names make it hard to identify which task failed in `ansible-playbook` output.

Maps to ansible-lint `name[unique]`.

### Example: violation

```yaml
- name: Deploy web application
  hosts: webservers
  tasks:
    - name: Install packages
      ansible.builtin.dnf:
        name: nginx
        state: present

    - name: Install packages
      ansible.builtin.dnf:
        name: php-fpm
        state: present

    - name: Start services
      ansible.builtin.systemd:
        name: nginx
        state: started
        enabled: true
```

### Example: pass

```yaml
- name: Deploy web application
  hosts: webservers
  tasks:
    - name: Install nginx
      ansible.builtin.dnf:
        name: nginx
        state: present

    - name: Install php-fpm
      ansible.builtin.dnf:
        name: php-fpm
        state: present

    - name: Start nginx
      ansible.builtin.systemd:
        name: nginx
        state: started
        enabled: true
```
