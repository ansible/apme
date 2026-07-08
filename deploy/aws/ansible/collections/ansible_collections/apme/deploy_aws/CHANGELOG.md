# Changelog

## 0.2.0

- **Breaking:** Terraform switched from ECS/Fargate to **EC2 + Podman**. Required outputs changed (`ecs_cluster_name` and `ecs_cluster_arn` removed; `instance_public_ip` and `instance_id` added).
- **`smoke_verify`**: configurable expected HTTP status via `smoke_verify_alb_expected_status` (default **200**). Retries increased to 24 with 10s delay for EC2 bootstrap time. Hardcoded 503 reference removed.

## 0.1.3

- **`smoke_verify`**: fix ALB HTTP retry loop — with **`failed_when: false`**, **`until: result is succeeded`** always matched after one attempt; now **`until`** waits for **HTTP 503** so listener propagation / transient connection errors can retry.

## 0.1.2

- **`smoke_verify`**: default required outputs no longer include **`ecs_task_execution_role_arn`** (Terraform may omit it when no role is created or supplied). ALB HTTP probe unchanged.

## 0.1.1

- **`smoke_verify`**: require **`alb_dns_name`** and **`ecs_task_execution_role_arn`** outputs; optional ALB HTTP probe (503 placeholder listener) with retries.

## 0.1.0

Initial `apme.deploy_aws` collection: Terraform apply/destroy wrappers and smoke role for Phase 1 AWS deploy.
