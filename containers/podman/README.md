# Podman pod (6 app containers + 1 infra; CLI on-the-fly)

Backend services run in a single **pod** so they share a network (localhost). Podman creates one extra **infra** container per pod to hold the pod’s shared network namespace, so `podman pod list` shows **7** containers (primary, native, ansible, opa, gitleaks, galaxy-proxy, plus the infra container). That’s expected. The **CLI is not part of the pod** and is run on-the-fly with your current directory mounted so you can scan any project without baking a path into the pod.

## Prerequisites

- Podman
- Run all commands from the **repo root** (or use absolute paths)

## Build and start

```bash
# From repo root
./containers/podman/build.sh   # build all images
./containers/podman/up.sh      # start the pod (primary, native, ansible, opa, gitleaks, galaxy-proxy)
./containers/podman/wait-for-pod.sh   # wait until pod status is Running (not Degraded)
```

Only run the health-check once the pod is **Running**. Use `wait-for-pod.sh` to wait for that, then run the health-check (or use `wait-for-pod.sh --health-check` to wait and then run the check in one step).

The pod creates:

- **Sessions directory** — session-scoped venvs are stored under `/sessions` in the pod. The Primary writes here (rw); the Ansible validator reads it (ro).
- OPA bundle is mounted from **src/apme_engine/validators/opa/bundle**.

## Run CLI commands (on-the-fly container)

From **any directory** you want to work with:

```bash
# Scan (default: scan .)
./containers/podman/run-cli.sh
./containers/podman/run-cli.sh scan --json .

# Fix (Tier 1 deterministic fixes, --check for dry-run)
./containers/podman/run-cli.sh fix --check .
./containers/podman/run-cli.sh fix .

# Format (YAML normalization)
./containers/podman/run-cli.sh format --check .

# Health check
./containers/podman/run-cli.sh health-check
```

The script mounts `$(pwd)` read-write at `/workspace` in the CLI container and joins the pod so the CLI can reach Primary at `127.0.0.1:50051`.

The `fix` command uses a **bidirectional gRPC stream** (`FixSession`, ADR-028)
that streams progress in real-time and supports interactive review of AI
proposals when `--ai` is enabled.

## Health check

Run the health-check only after the pod is **Running** (not Degraded). Wait first, then check:

```bash
./containers/podman/wait-for-pod.sh              # wait until pod is Running
APME_PRIMARY_ADDRESS=127.0.0.1:50051 .venv/bin/apme-scan health-check
```

Or wait and run the health-check in one step:

```bash
./containers/podman/wait-for-pod.sh --health-check
```

This checks **Primary**, **Native**, **Ansible**, **Gitleaks** (gRPC) and **OPA** (REST). Use `--json` for machine-readable output. Addresses for Ansible and OPA are derived from the primary host (ports 50053, 8181) or set via env: `ANSIBLE_GRPC_ADDRESS`, `OPA_URL`.

## Stop the pod

```bash
podman pod stop apme-pod
podman pod rm -f apme-pod
```

## Troubleshooting

If the **primary** container keeps restarting (pod stays Degraded), inspect its logs:

```bash
podman logs apme-pod-primary
```

Common causes:

- **Port in use** — Ensure no other process on the host is using 50051 (or 50053, 8181, 8765). Restart the pod after stopping any conflicting services.
- **Import or runtime error** — The primary process logs exceptions to stderr before exiting; the traceback in `podman logs` will show the cause.

To run the primary container interactively to see startup errors:

```bash
podman run --rm -it --pod apme-pod -e APME_PRIMARY_LISTEN=0.0.0.0:50051 -e OPA_URL=http://127.0.0.1:8181 -e ANSIBLE_GRPC_ADDRESS=127.0.0.1:50053 apme-primary:latest apme-primary
```
