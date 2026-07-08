# APME Single-Machine Sizing Guide

Resource estimates for running **25 concurrent scans** with content the size of
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

The default concurrency limits **cannot support 25 concurrent scans** without
tuning:

| Setting | Default | Required for 25 scans |
|---------|---------|----------------------|
| `APME_PRIMARY_MAX_RPCS` | 16 | **32** |
| `APME_SESSION_MAX` | 10 | **25-30** |
| `APME_ANSIBLE_MAX_RPCS` | 8 | **25-32** |
| `APME_NATIVE_MAX_RPCS` | 32 | 32 (sufficient) |
| `APME_OPA_MAX_RPCS` | 32 | 32 (sufficient) |
| `APME_GITLEAKS_MAX_RPCS` | 16 | **25-32** |

## Disk

| Component | Per-scan | 25 concurrent | Notes |
|-----------|----------|---------------|-------|
| Session venvs (`/sessions`) | ~300 MB | **7.5 GB** | Worst case: 25 distinct `session_id`s, cold start. Same session IDs reuse venvs. |
| Galaxy Proxy wheel cache (`/cache`) | — | **~200 MB** | Shared across all sessions; one wheel per (collection, version). |
| Primary temp dirs | ~25 MB | **625 MB** | Uploaded files materialized to `tmpfs` per scan. |
| Ansible validator temp dirs | ~25 MB | **625 MB** | Duplicates request files per validation. |
| Container images (11 containers) | — | **~3 GB** | Based on `uv:python3.12-bookworm-slim` + deps. |
| OS + runtime + logs | — | **~3 GB** | |
| Gateway SQLite DB | — | **~500 MB** | Grows with scan history. |
| **Headroom for venv churn** | — | **~5 GB** | Venv reaping is **not automated** in daemon code; venvs accumulate. |

### Disk recommendation: 50 GB minimum, 100 GB recommended

The default AWS deploy uses 30 GB — that will fill within hours under this
load. The main risk is venv accumulation since `reap_expired()` exists but is
never called by the daemon. You would need external cleanup (cron) or to wire
up the reaper.

## Memory

| Component | Idle baseline | Per concurrent scan | 25 concurrent |
|-----------|--------------|---------------------|---------------|
| **11 container processes** (Python 3.12 + grpcio + deps) | **~2.5 GB** | — | 2.5 GB baseline |
| **Primary** — ContentGraph + hierarchy payload + serialized graph | — | ~150-300 MB | **3.75-7.5 GB** |
| **Native validator** — graph deserialization + rule evaluation | — | ~50-100 MB | **1.25-2.5 GB** |
| **OPA validator** — `opa eval` subprocess + JSON input | — | ~80-150 MB | **2-3.75 GB** |
| **Ansible validator** — temp files + ansible subprocess | — | ~150-250 MB | **3.75-6.25 GB** |
| **Gitleaks** — subprocess (stdin pipe, no temp files) | — | ~30-50 MB | **0.75-1.25 GB** |
| **Galaxy Proxy** — FastAPI + wheel conversion | — | ~20 MB (cache hit) | **0.5 GB** |
| **Thread pool stacks** — default pool `min(32, cpu+4)` per container | — | — | **~1 GB** |
| **Gateway + UI + Abbenay** | **~1 GB** | — | 1 GB |

### Memory recommendation: 32 GB minimum, 48-64 GB recommended

Peak memory occurs when all 25 scans are in the **validator fan-out phase
simultaneously** — each scan's ContentGraph lives in Primary memory while
copies are also deserialized in each validator. The 50 MiB gRPC message limit
means a single scan's wire payloads alone can consume ~200 MB across the
validator tier.

## CPU

| Workload | Pattern | CPU impact |
|----------|---------|------------|
| **Primary engine** (parse → annotate → graph) | `run_in_executor()`, CPU-bound | Heavy — YAML parsing, graph construction, annotation |
| **OPA eval** | Subprocess per scan, 60s timeout | Moderate — Rego evaluation on hierarchy JSON |
| **Ansible validator** | Subprocess calls into session venv | Moderate — argspec introspection, syntax checks |
| **Native validator** | In-process graph traversal | Moderate — Python rule evaluation on deserialized graph |
| **Gitleaks** | Subprocess, stdin pipe | Light per scan |
| **Galaxy Proxy** | Wheel conversion on cache miss | Burst on cold start, negligible on cache hit |
| **Venv creation** (`uv pip install`) | Subprocess, disk I/O bound | Burst — 25 cold starts will serialize on disk I/O |
| **Thread pool contention** | Default pool = `min(32, cpu+4)` | With 4 CPUs, pool = 8 threads shared across 25 scans |

### CPU recommendation: 8 vCPUs minimum, 16 vCPUs recommended

The bottleneck is the thread pool executor. With the default pool size of
`min(32, cpu+4)`, you need enough cores so the pool is large enough to avoid
serializing blocking work. At 8 vCPUs the pool is 12 threads per container;
at 16 vCPUs it is 20 threads — much closer to the 25-scan target.

## Instance Sizing Summary

| Tier | CPU | Memory | Disk | AWS equivalent | Notes |
|------|-----|--------|------|----------------|-------|
| **Minimum viable** | 8 vCPU | 32 GB | 50 GB gp3 | `m6i.2xlarge` | Expect queuing at Ansible validator; scans may timeout under full load |
| **Recommended** | 16 vCPU | 64 GB | 100 GB gp3 | `m6i.4xlarge` | Comfortable headroom; handles burst cold-start venv builds |
| **Comfortable** | 16 vCPU | 64 GB | 200 GB gp3 | `m6i.4xlarge` + larger vol | Long-running deployments where venv accumulation is a concern |

## Key Risks at This Scale

1. **Venv accumulation** — the reaper is not wired up in daemon code. Without
   external cleanup, `/sessions` grows unbounded.

2. **Ansible validator is the bottleneck** — default 8 RPCs means 17 scans
   queue. Raise `APME_ANSIBLE_MAX_RPCS` but expect proportional memory growth.

3. **Cold-start storm** — 25 simultaneous first-time scans all building venvs
   will saturate disk I/O. The Galaxy Proxy deduplicates downloads
   (per-collection `asyncio.Lock`), but `uv pip install` into 25 separate
   venvs is parallelized and I/O heavy.

4. **No container resource limits** — a single runaway scan can OOM the host.
   Consider adding `--memory` limits to container definitions.

5. **gRPC 50 MiB message cap** — cisco.ios-scale content should fit well within
   this, but scan targets with thousands of files could hit it.
