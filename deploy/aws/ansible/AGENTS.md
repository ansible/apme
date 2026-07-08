# deploy/aws Ansible — agent notes

- Use **FQCNs** for modules (e.g. `cloud.terraform.terraform`, `ansible.builtin.assert`).
- Follow [ansible-creator agents.md](https://raw.githubusercontent.com/ansible/ansible-creator/refs/heads/main/docs/agents.md) for structure and style.
- Collection dependency pins live in **`collections/ansible_collections/apme/deploy_aws/galaxy.yml`**; sync **`collections/requirements.yml`** when they change.
