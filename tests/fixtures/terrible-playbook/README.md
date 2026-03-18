# terrible-playbook

A deliberately terrible Ansible playbook designed to trigger every possible
[APME](https://github.com/ansible/apme) validation rule. This repo exists
for demo and testing purposes — **do not use this as a reference for writing
Ansible content.**

## What's wrong with it?

Everything. This playbook violates ~50 lint, modernization, risk, policy,
and secrets-detection rules simultaneously, including:

- Short module names instead of FQCN
- `shell` where `command` suffices, `command` where a module exists
- Missing `changed_when`, `mode`, `name`, `no_log`
- Hardcoded secrets (API keys, passwords, private keys)
- Deprecated bare `include`, `with_items`, Python 2 interpreter
- Parameterized command execution, downloads from untrusted sources
- Argspec violations on `community.general` modules
- Tabs in YAML, bad Jinja spacing, implicit state
- A role with no `meta/main.yml`

## Usage

```bash
apme scan /path/to/terrible-playbook
```
