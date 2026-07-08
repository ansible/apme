# APME Single-Machine Sizing Guide — 5 Concurrent Scans

Resource estimates for running **5 concurrent scans** with content the size of
the `cisco.ios` Ansible collection (~150+ modules, 300-500 parseable files),
all containers on one machine.

## Reference: cisco.ios Collection Profile

The `cisco.ios` collection (~v11.x) contains roughly **150+ modules**,
**40+ resource modules**, plus plugins, docs, and tests. As a scan target,
that translates to approximately:

- **300-500 parseable files** (YAML + Python)
- **15-25 MB** installed collection size
- **~300 MB** full session venv (ansible-core 2.20 + cisco.ios + transitive
  deps like `ansible.netcommon`, `ansible.utils`)

## Configuration Prerequisites

All default concurrency limits are **sufficient for 5 concurrent scans** — no
tuning required:

| Setting | Default | Status |
|---------|---------|--------|
| `APME_PRIMARY_MAX_RPCS` | 16 | Sufficient |
| `APME_SESSION_MAX` | 10 | Sufficient |
| `APME_ANSIBLE_MAX_RPCS` | 8 | Sufficient |
| `APME_NATIVE_MAX_RPCS` | 32 | Sufficient |
| `APME_OPA_MAX_RPCS` | 32 | Sufficient |
| `APME_GITLEAKS_MAX_RPCS` | 16 | Sufficient |

## Disk

| Component | Per-scan | 5 concurrent | Notes |
|-----------|----------|--------------|-------|
| Session venvs (`/sessions`) | ~300 MB | **1.5 GB** | Worst case: 5 distinct `session_id`s, cold start. Same session IDs reuse venvs. |
| Galaxy Proxy wheel cache (`/cache`) | — | **~200 MB** | Shared across all sessions; one wheel per (collection, version). |
| Primary temp dirs | ~25 MB | **125 MB** | Uploaded files materialized to `tmpfs` per scan. |
| Ansible validator temp dirs | ~25 MB | **125 MB** | Duplicates request files per validation. |
| Container images (11 containers) | — | **~3 GB** | Based on `uv:python3.12-bookworm-slim` + deps. |
| OS + runtime + logs | — | **~3 GB** | |
| Gateway SQLite DB | — | **~200 MB** | Grows with scan history. |
| **Headroom for venv churn** | — | **~2 GB** | Venv reaping is **not automated** in daemon code; venvs accumulate over time. |

### Disk recommendation: 30 GB minimum, 50 GB recommended

The default AWS deploy of 30 GB is viable for 5 concurrent scans but leaves
limited headroom for venv accumulation over time. 50 GB provides comfortable
runway for longer-running deployments.

## Memory

| Component | Idle baseline | Per concurrent scan | 5 concurrent |
|-----------|--------------|---------------------|--------------|
| **11 container processes** (Python 3.12 + grpcio + deps) | **~2.5 GB** | — | 2.5 GB baseline |
| **Primary** — ContentGraph + hierarchy payload + serialized graph | — | ~150-300 MB | **0.75-1.5 GB** |
| **Native validator** — graph deserialization + rule evaluation | — | ~50-100 MB | **250-500 MB** |
| **OPA validator** — `opa eval` subprocess + JSON input | — | ~80-150 MB | **400-750 MB** |
| **Ansible validator** — temp files + ansible subprocess | — | ~150-250 MB | **0.75-1.25 GB** |
| **Gitleaks** — subprocess (stdin pipe, no temp files) | — | ~30-50 MB | **150-250 MB** |
| **Galaxy Proxy** — FastAPI + wheel conversion | — | ~20 MB (cache hit) | **100 MB** |
| **Thread pool stacks** — default pool `min(32, cpu+4)` per container | — | — | **~500 MB** |
| **Gateway + UI + Abbenay** | **~1 GB** | — | 1 GB |

### Memory recommendation: 16 GB minimum, 32 GB recommended

At 5 concurrent scans, peak memory during validator fan-out is manageable.
The 16 GB minimum assumes scans do not all hit peak simultaneously. 32 GB
provides headroom for burst scenarios and prevents OOM risk from the lack of
per-container memory limits.

## CPU

| Workload | Pattern | CPU impact |
|----------|---------|------------|
| **Primary engine** (parse → annotate → graph) | `run_in_executor()`, CPU-bound | Heavy — YAML parsing, graph construction, annotation |
| **OPA eval** | Subprocess per scan, 60s timeout | Moderate — Rego evaluation on hierarchy JSON |
| **Ansible validator** | Subprocess calls into session venv | Moderate — argspec introspection, syntax checks |
| **Native validator** | In-process graph traversal | Moderate — Python rule evaluation on deserialized graph |
| **Gitleaks** | Subprocess, stdin pipe | Light per scan |
| **Galaxy Proxy** | Wheel conversion on cache miss | Burst on cold start, negligible on cache hit |
| **Venv creation** (`uv pip install`) | Subprocess, disk I/O bound | Burst — 5 cold starts are manageable |
| **Thread pool contention** | Default pool = `min(32, cpu+4)` | With 4 CPUs, pool = 8 threads — adequate for 5 scans |

### CPU recommendation: 4 vCPUs minimum, 8 vCPUs recommended

At 5 concurrent scans the default thread pool is not a bottleneck. With
4 vCPUs the executor pool is 8 threads per container, comfortably above
the concurrency target. 8 vCPUs reduces scan latency during validator
fan-out when all 5 scans overlap.

## Instance Sizing Summary

| Tier | CPU | Memory | Disk | AWS equivalent | Notes |
|------|-----|--------|------|----------------|-------|
| **Minimum viable** | 4 vCPU | 16 GB | 30 GB gp3 | `m6i.xlarge` | Works within all defaults; tight on disk for long-running deployments |
| **Recommended** | 8 vCPU | 32 GB | 50 GB gp3 | `m6i.2xlarge` | Comfortable headroom; handles cold-start bursts well |

## Key Risks at This Scale

1. **Venv accumulation** — the reaper is not wired up in daemon code. Without
   external cleanup, `/sessions` grows unbounded. Less urgent at 5 concurrent
   than at 25, but still relevant for long-running deployments.

2. **Cold-start burst** — 5 simultaneous first-time scans building venvs will
   cause a brief I/O spike. The Galaxy Proxy deduplicates downloads
   (per-collection `asyncio.Lock`), and 5 parallel `uv pip install` operations
   are well within the capability of gp3 baseline IOPS (3000).

3. **No container resource limits** — not critical at 5 concurrent scans, but
   adding `--memory` limits to container definitions is still good practice.
