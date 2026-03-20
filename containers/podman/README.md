# Podman pod (6 app containers + 1 infra; CLI on-the-fly)

Backend services run in a single **pod** so they share a network (localhost). Podman creates one extra **infra** container per pod to hold the pod's shared network namespace, so `podman pod list` shows **7** containers (primary, native, ansible, opa, gitleaks, cache-maintainer, plus the infra container). That's expected. The **CLI is not part of the pod** and is run on-the-fly with your current directory mounted so you can scan any project without baking a path into the pod.

## Prerequisites

- Podman
- Run all commands from the **repo root** (or use absolute paths)

## Build and start

```bash
# From repo root — engine only
./containers/podman/build.sh   # build all engine images
./containers/podman/up.sh      # start the engine pod
./containers/podman/wait-for-pod.sh   # wait until pod status is Running

# With web UI (gateway + standalone dashboard)
./containers/podman/build.sh --ui   # build engine + gateway + UI images
./containers/podman/up.sh --ui      # start engine pod + gateway + UI containers
```

Only run the health-check once the pod is **Running**. Use `wait-for-pod.sh` to wait for that, then run the health-check (or use `wait-for-pod.sh --health-check` to wait and then run the check in one step).

The pod creates:

- **Cache directory** — defaults to `${XDG_CACHE_HOME:-$HOME/.cache}/apme` (persists across reboots). Override with `APME_CACHE_HOST_PATH=/my/cache ./up.sh`. The cache-maintainer writes here; the ansible validator reads it.
- OPA bundle is mounted from **src/apme_engine/validators/opa/bundle**.

## Web UI (--ui flag)

The `--ui` flag starts two additional containers **outside** the engine pod:

| Container | Port | Purpose |
|-----------|------|---------|
| `apme-gateway` | `:8080` | FastAPI — translates HTTP/WebSocket to gRPC, owns SQLite persistence |
| `apme-ui` | `:8081` | nginx — serves the PatternFly/React SPA, proxies `/api` to gateway |

```
Browser :8081 ──HTTP──► apme-ui (nginx) ──/api proxy──► apme-gateway :8080 ──gRPC──► apme-pod :50051
```

The gateway and UI containers are on a Podman network (`apme-ui-net`) and connect to Primary via the host-mapped port 50051. They are **not** part of the engine pod — they live outside it per ADR-029.

The gateway automatically subscribes to Primary's `SubscribeScanEvents` gRPC stream (ADR-020). **All scan results** — whether initiated from the CLI, CI, or the web UI — appear in the dashboard without any extra configuration.

- **Gateway data** (SQLite) is stored at `${XDG_DATA_HOME:-$HOME/.local/share}/apme/gateway`. Override with `APME_GATEWAY_DATA`.
- **Port overrides** — `APME_GATEWAY_PORT` (default 8080) and `APME_UI_PORT` (default 8081):

```bash
APME_GATEWAY_PORT=9080 APME_UI_PORT=9081 ./containers/podman/up.sh --ui
```

- Open the dashboard at **http://localhost:8081** (or your custom `APME_UI_PORT`).
- The gateway API (OpenAPI docs) is at **http://localhost:8080/docs** (or your custom `APME_GATEWAY_PORT`).

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

This checks **Primary**, **Native**, **Ansible**, **Gitleaks**, **Cache maintainer** (gRPC) and **OPA** (REST). Use `--json` for machine-readable output. Addresses for Ansible, Cache, and OPA are derived from the primary host (ports 50053, 50052, 8181) or set via env: `ANSIBLE_GRPC_ADDRESS`, `APME_CACHE_GRPC_ADDRESS`, `OPA_URL`.

## Stop

```bash
# Stop everything (engine pod + gateway + UI)
./containers/podman/down.sh

# Also wipe the gateway SQLite database
./containers/podman/down.sh --wipe
```

## Troubleshooting

If the **primary** container keeps restarting (pod stays Degraded), inspect its logs:

```bash
podman logs apme-pod-primary
```

Common causes:

- **Port in use** — Ensure no other process on the host is using 50051 (or 50052, 50053, 8181). Restart the pod after stopping any conflicting services.
- **Import or runtime error** — The primary process logs exceptions to stderr before exiting; the traceback in `podman logs` will show the cause.

To run the primary container interactively to see startup errors:

```bash
podman run --rm -it --pod apme-pod -e APME_PRIMARY_LISTEN=0.0.0.0:50051 -e OPA_URL=http://127.0.0.1:8181 -e ANSIBLE_GRPC_ADDRESS=127.0.0.1:50053 apme-primary:latest apme-primary
```

For **gateway** or **UI** issues:

```bash
podman logs apme-gateway
podman logs apme-ui
```
