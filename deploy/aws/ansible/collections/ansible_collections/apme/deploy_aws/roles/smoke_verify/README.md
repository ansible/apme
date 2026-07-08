# smoke_verify

Loads Terraform outputs, asserts required output names exist, and HTTP-probes the ALB until the expected status is returned (default **200** for the EC2+Podman deployment; configurable via `smoke_verify_alb_expected_status`).
