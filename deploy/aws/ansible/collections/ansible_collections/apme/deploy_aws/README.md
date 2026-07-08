# apme.deploy_aws

Adjacent collection for the `deploy/aws` Ansible playbook project.

## Roles

| Role | Purpose |
|------|---------|
| `apme.deploy_aws.terraform_run` | `terraform apply` via `cloud.terraform.terraform` (`state: present`). |
| `apme.deploy_aws.terraform_destroy` | `terraform destroy` via `cloud.terraform.terraform` (`state: absent`). |
| `apme.deploy_aws.smoke_verify` | Read outputs with `cloud.terraform.terraform_output` and assert required keys exist. |

Each role exposes variables via `meta/argument_specs.yml`.

## Dependencies

Galaxy deps are installed from the playbook project with **`ade install -r collections/requirements.yml`** (see [deploy/aws/README.md](../../../../../README.md)). **`galaxy.yml`** `dependencies` should stay aligned with that file.
