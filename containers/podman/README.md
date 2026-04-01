# Podman pod (6 app containers + 1 infra; CLI on-the-fly)

Backend services run in a single **pod** so they share a network (localhost). Podman creates one extra **infra** container per pod to hold the pod's shared network namespace, so `podman pod list` shows **7** containers (primary, native, ansible, opa, gitleaks, galaxy-proxy, plus the infra container). That's expected. The **CLI is not part of the pod** and is run on-the-fly with your current directory mounted so you can scan any project without baking a path into the pod.

## Prerequisites

- Podman
- Run all commands from the **repo root** (or use absolute paths)
- **Recommended**: use tox (`uv tool install tox --with tox-uv`) — see `docs/DEVELOPMENT.md`

## Build and start

```bash
# Via tox (recommended)
tox -e up        # build images and start the pod
tox -e pm            # build + start + health-check + open browser

# Or directly
./containers/podman/build.sh
./containers/podman/up.sh
./containers/podman/wait-for-pod.sh
```

Only run the health-check once the pod is **Running**. Use `wait-for-pod.sh` to wait for that, then run the health-check (or use `wait-for-pod.sh --health-check` to wait and then run the check in one step).

The pod creates:

- **Sessions directory** — session-scoped venvs are stored under `/sessions` in the pod. The Primary writes here (rw); the Ansible validator reads it (ro).
- OPA bundle is mounted from **src/apme_engine/validators/opa/bundle**.

## Run CLI commands (on-the-fly container)

From **any directory** you want to work with:

```bash
# Via tox
tox -e cli                       # default: check .
tox -e cli -- check --json .     # JSON output
tox -e cli -- remediate .        # apply Tier 1 fixes
tox -e cli -- health-check       # health check

# Or directly
./containers/podman/run-cli.sh
./containers/podman/run-cli.sh check --json .
./containers/podman/run-cli.sh remediate .
./containers/podman/run-cli.sh health-check
```

The script mounts `$(pwd)` read-write at `/workspace` in the CLI container and joins the pod so the CLI can reach Primary at `127.0.0.1:50051`.

The `remediate` command uses a **bidirectional gRPC stream** (`FixSession`, ADR-028)
that streams progress in real-time and supports interactive review of AI
proposals when `--ai` is enabled.

## Health check

Run the health-check only after the pod is **Running** (not Degraded). Wait first, then check:

```bash
./containers/podman/wait-for-pod.sh --health-check
```

The health check probes all validators via **gRPC** through the Primary orchestrator. Each validator implements the `Validator.Health` RPC (unified contract). Use `--json` for machine-readable output.

## Stop the pod

```bash
tox -e down             # stop
tox -e wipe             # stop + wipe DB and session cache

# Or directly
podman pod stop apme-pod
podman pod rm -f apme-pod
```

## Troubleshooting

If the **primary** container keeps restarting (pod stays Degraded), inspect its logs:

```bash
podman logs apme-pod-primary
```

Common causes:

- **Port in use** — Ensure no other process on the host is using 50051 (or 50053–50056, 8765). Restart the pod after stopping any conflicting services.
- **Import or runtime error** — The primary process logs exceptions to stderr before exiting; the traceback in `podman logs` will show the cause.

To run the primary container interactively to see startup errors:

```bash
podman run --rm -it --pod apme-pod apme-primary:latest apme-primary
```
