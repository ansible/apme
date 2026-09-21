# Podman pod (app containers + infra + OTel collector; CLI on-the-fly)

Backend services run in a single **pod** so they share a network (localhost). Podman creates one extra **infra** container per pod to hold the pod's shared network namespace. The **CLI is not part of the pod** and is run on-the-fly with your current directory mounted so you can scan any project without baking a path into the pod.

The pod includes an **otel-collector** sidecar that receives OTLP metrics from Engine, Gateway, and Galaxy Proxy and exposes Prometheus scrape on **http://localhost:8889/metrics**. Optional Grafana/Prometheus companion: [`containers/observability/README.md`](../observability/README.md).

## Prerequisites

- Podman
- Run all commands from the **repo root** (or use absolute paths)
- **Recommended**: use tox (`uv tool install tox --with tox-uv`) — see `docs/guides/DEVELOPMENT.md`

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

- **Sessions directory** — session-scoped venvs are stored under `/sessions` in the pod. The Engine writes here (rw); the Ansible validator reads it (ro).
- OPA Rego bundle is **copied into the image** at build time from `src/apme_engine/validators/opa/bundle` (no runtime volume mount).

The local Abbenay UI prefers `http://127.0.0.1:8787`. If that host port is
already occupied, `tox -e up` selects an available port in the range
`8787-8887` and prints the actual UI URL after startup. Abbenay still listens
on port `8787` inside the pod, so the Gateway's in-pod proxy is unchanged.
Use `APME_ABBENAY_HOST_PORT=<port> tox -e up` to request an exact host port;
startup fails if that port is invalid or unavailable. The printed URL is the
authoritative host URL. This behavior applies to the local Podman pod only;
Helm deployments keep Abbenay loopback-only inside the Kubernetes pod.

`tox -e up` mounts the writable Abbenay configuration directory from
`${XDG_CONFIG_HOME:-~/.config}/abbenay` and prints the effective `config.yaml`
path at the end of startup. This file is the runtime source of truth for
provider configuration; an existing legacy cache config is migrated there once
when the user config does not yet exist.

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

The script mounts `$(pwd)` read-write at `/workspace` in the CLI container and joins the pod so the CLI can reach Engine at `127.0.0.1:50051`.

The `remediate` command uses a **bidirectional gRPC stream** (`FixSession`, ADR-028)
that streams progress in real-time and supports interactive review of AI
proposals when `--ai` is enabled.

## Health check

Run the health-check only after the pod is **Running** (not Degraded). Wait first, then check:

```bash
./containers/podman/wait-for-pod.sh --health-check
```

The health check probes the Engine and each validator directly via **gRPC** (not routed through Engine). Each validator implements the `Validator.Health` RPC (unified contract). Use `--json` for machine-readable output.

## Stop the pod

```bash
tox -e down             # stop
tox -e wipe             # stop + wipe DB and session cache; preserve Abbenay config/secrets

# Or directly
podman pod stop apme-pod
podman pod rm -f apme-pod
```

## Troubleshooting

If the **engine** container keeps restarting (pod stays Degraded), inspect its logs:

```bash
podman logs apme-pod-engine
```

Common causes:

- **Port in use** — Ensure no other process on the host is using 50051 (or 50053–50056, 8765). Restart the pod after stopping any conflicting services.
- **Import or runtime error** — The Engine process logs exceptions to stderr before exiting; the traceback in `podman logs` will show the cause.

To follow engine startup logs (routine CLI use remains `tox -e cli`):

```bash
podman logs -f apme-pod-engine
```
