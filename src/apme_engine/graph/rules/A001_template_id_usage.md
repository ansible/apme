---
rule_id: A001
validator: native
description: Tasks should use named_url instead of hardcoded template IDs.
scope: task
---

## Template ID Usage (A001)

Tasks interacting with AAP should use named_url references instead of hardcoded numeric template IDs. Numeric IDs are environment-specific and break when content is promoted across environments (dev → staging → production).

AAP 2.5+ supports named_url for job templates and workflow templates, enabling portable automation content.

### Example: fail

```yaml
- name: Launch job with hardcoded ID
  ansible.builtin.uri:
    url: "https://controller.example.com/api/v2/job_templates/42/launch/"
    method: POST

- name: Launch job via controller module with ID
  ansible.controller.job_launch:
    job_template_id: 42
```

### Example: pass

```yaml
- name: Launch job with named_url
  ansible.builtin.uri:
    url: "https://controller.example.com/api/controller/v2/job_templates/Deploy+App++Default/launch/"
    method: POST

- name: Launch job via controller module with name
  ansible.controller.job_launch:
    job_template: "Deploy App"
```

### Rationale

- Template IDs differ across AAP environments
- Content promotion fails when IDs are hardcoded
- named_url format: `<name>++<organization>` or `<name>++Default`
- Enables GitOps workflows with environment-agnostic automation
