---
rule_id: L112
validator: native
description: Git and Mercurial tasks should select an explicit version.
scope: task
---

## Latest VCS version (L112)

Flags built-in `git` tasks that omit `version` or set it to `HEAD`, and
`hg` tasks (including `community.general.hg`) that omit `revision` or set it
to `default`. These values follow a moving branch or
default revision, so repeated runs can retrieve different content. Set the
option to the intended commit or revision. Templated values are left to runtime
resolution. Short `git` and `hg` names are skipped when a play's declared
collection search order could resolve them to an unmapped custom plugin. The
known `community.general.hg` association is checked for short `hg` when that
collection is first in search order; explicit FQCNs are also checked.

### Example: violation

```yaml
- name: Clone a moving branch
  ansible.builtin.git:
    repo: https://example.com/project.git
    dest: /srv/project
```

### Example: pass

```yaml
- name: Clone a fixed commit
  ansible.builtin.git:
    repo: https://example.com/project.git
    dest: /srv/project
    version: 0123456789abcdef0123456789abcdef01234567
```
