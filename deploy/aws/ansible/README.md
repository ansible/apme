# Ansible (`deploy/aws/ansible`)

Playbooks: **`deploy.yml`**, **`destroy.yml`** (repo root of this project).

Install **`ade`**: **`uv tool install ansible-dev-environment`** (or use **`uv run --extra dev ade`** from the repo root after **`uv sync --extra dev`**).

```bash
ade install -r collections/requirements.yml
source .venv/bin/activate
ansible-playbook deploy.yml
```

See [deploy/aws/README.md](../README.md) for the full picture.

## Layout

- **Adjacent collection**: [collections/ansible_collections/apme/deploy_aws/](collections/ansible_collections/apme/deploy_aws/)

## AI / authoring guidelines

Follow [Ansible Coding Guidelines for AI Agents](https://raw.githubusercontent.com/ansible/ansible-creator/refs/heads/main/docs/agents.md).
