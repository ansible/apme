# AWS deployment (EC2 + Podman)

Terraform under **`terraform/`** provisions a VPC, ALB, and a single EC2 instance (RHEL 9) that runs the full APME pod via Podman. An Ansible playbook project under **`ansible/`** drives apply/destroy and smoke checks.

## Architecture

```
┌─ Internet ─────────────────────────────────────────────────────────┐
│                       ALB :80 (HTTP)                               │
│              ┌────────────────────────────────┐                    │
│              │  default → UI :8081            │                    │
│              │  /api/*  → Gateway :8080       │                    │
│              └──────────┬─────────────────────┘                    │
│                         │                                          │
│              ┌──────────▼──────────────────────┐                   │
│              │   EC2 (RHEL 9 + Podman)         │                   │
│              │   ┌──────────────────────────┐  │                   │
│              │   │      apme-pod            │  │                   │
│              │   │  primary, native, opa,   │  │                   │
│              │   │  ansible, gitleaks,      │  │                   │
│              │   │  collection-health,      │  │                   │
│              │   │  dep-audit, gateway,     │  │                   │
│              │   │  ui, galaxy-proxy,       │  │                   │
│              │   │  abbenay                 │  │                   │
│              │   │  [oauth2-proxy optional] │  │                   │
│              │   └──────────────────────────┘  │                   │
│              └─────────────────────────────────┘                   │
└────────────────────────────────────────────────────────────────────┘
```

## Layout

| Path | Purpose |
|------|---------|
| [terraform/](terraform/) | Root module: VPC, subnets, IGW, ALB, EC2 instance (RHEL 9 + Podman), optional S3+DynamoDB state backend, optional oauth2-proxy. |
| [ansible/](ansible/) | Playbooks **`deploy.yml`** / **`destroy.yml`**, inventory, **`collections/requirements.yml`**, adjacent **`apme.deploy_aws`** collection. |

## Prerequisites

- **Terraform >= 1.5** and **AWS credentials** with permissions for EC2, VPC, ELB, CloudWatch Logs (no `iam:CreateRole` needed).
- **SSH public key** — required for the `ssh_public_key` variable (e.g. `~/.ssh/id_ed25519.pub`).
- **Ansible** — install via `ade` (see below) for the `cloud.terraform` collection.

## Quick start

### 1. Terraform

```bash
cd deploy/aws/terraform
terraform init

# Provide your SSH public key (required)
terraform plan -var 'ssh_public_key=ssh-ed25519 AAAA...'
terraform apply -var 'ssh_public_key=ssh-ed25519 AAAA...'
```

Or create a **`terraform.tfvars`** file:

```hcl
ssh_public_key = "ssh-ed25519 AAAA..."
```

### 2. Ansible

```bash
cd deploy/aws/ansible
ade install -r collections/requirements.yml
source .venv/bin/activate
ansible-playbook deploy.yml
```

The deploy playbook runs `terraform apply` and then a smoke check (waits for ALB to return HTTP 200).

### 3. SSH into the instance

After deploy, the `ssh_command` output gives you a ready-to-use command:

```bash
ssh ec2-user@<instance_public_ip>
# Check pod status:
sudo podman pod ps
sudo podman ps
# View bootstrap log:
sudo cat /var/log/apme-setup.log
```

### 4. Destroy

```bash
cd deploy/aws/ansible
ansible-playbook destroy.yml
```

## Tags and Resource Groups

The AWS provider **`default_tags`** set **`Project = var.project_name`** (default **`apme-dev`**). Create a Resource Group with tag **`Project=apme-dev`** to list all stack resources.

## Container images

Images are pulled from **`ghcr.io/ansible/apme-<service>:latest`** (public, pushed by CI on main-branch merges). No registry authentication needed. Configurable via `ghcr_image_prefix` and `ghcr_image_tag` variables.

## OAuth2-proxy (optional)

Restrict access to members of a GitHub organization (e.g. **`ansible-employees`**).

### Setup (two-step)

1. **First deploy** without OAuth variables — the app is open on the ALB. Note the `alb_dns_name` and `oauth2_proxy_callback_url` outputs.

2. **Register a GitHub OAuth App** at [github.com/settings/applications/new](https://github.com/settings/applications/new):

   | Field | Value |
   |-------|-------|
   | Application name | `apme` |
   | Homepage URL | `http://<alb_dns_name>/` |
   | Authorization callback URL | `http://<alb_dns_name>/oauth2/callback` |

3. **Second deploy** with OAuth variables:

   ```bash
   terraform apply \
     -var 'ssh_public_key=ssh-ed25519 AAAA...' \
     -var 'oauth2_proxy_client_id=Iv1.abc123...' \
     -var 'oauth2_proxy_client_secret=secret...'
   ```

   This recreates the EC2 instance with oauth2-proxy in the pod. The ALB routes all traffic through oauth2-proxy on port 4180, which authenticates against GitHub and restricts to `ansible-employees` org members.

   Override the org: `-var 'oauth2_proxy_github_org=my-org'`.

## Optional remote state (S3 + DynamoDB)

Set `create_state_backend = true`, apply once with local state, then migrate:

```bash
terraform apply -var 'create_state_backend=true' -var 'ssh_public_key=...'
# Note terraform_state_bucket_name and terraform_state_lock_table_name outputs
# Copy backend.hcl.example to backend.hcl, fill in values, then:
terraform init -migrate-state -backend-config=backend.hcl
```

## Variables reference

| Variable | Default | Description |
|----------|---------|-------------|
| `aws_region` | `us-east-1` | AWS region |
| `project_name` | `apme-dev` | Name prefix and `Project=` tag value |
| `vpc_cidr` | `10.42.0.0/16` | VPC CIDR |
| `instance_type` | `t3.large` | EC2 instance type |
| `ssh_public_key` | *(required)* | SSH public key for EC2 key pair |
| `ssh_cidr_blocks` | `["0.0.0.0/0"]` | CIDRs allowed to SSH |
| `ghcr_image_prefix` | `ghcr.io/ansible` | GHCR image prefix |
| `ghcr_image_tag` | `latest` | Image tag to pull |
| `oauth2_proxy_client_id` | `""` | GitHub OAuth App client ID (empty = disabled) |
| `oauth2_proxy_client_secret` | `""` | GitHub OAuth App client secret |
| `oauth2_proxy_github_org` | `ansible-employees` | GitHub org for access control |
| `oauth2_proxy_cookie_secret` | `""` | Cookie secret (auto-generated if empty) |
| `create_state_backend` | `false` | Create S3+DynamoDB for remote state |
| `state_backend_bucket_name` | `""` | Override state bucket name |
