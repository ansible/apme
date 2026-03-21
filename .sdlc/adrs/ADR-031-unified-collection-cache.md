# ADR-031: Unified Collection Cache as Single Authoritative Source

## Status

Accepted (Phases 1–4 complete)

## Date

2026-03-19

## Context

Today APME has **three independent systems** that download, store, and resolve Ansible collection/role content — each unaware of the others:

### 1. ARI engine dependency preparator (scanner path)

When `ARIScanner.evaluate()` encounters a dependency (e.g. `community.general`), `dependency_dir_preparator.py` calls `ansible-galaxy collection install` to download the tarball, extracts it into `{root_dir}/collections/src/ansible_collections/ns/coll/`, scans it, and writes findings + index JSON to `{root_dir}/{type}s/findings/`. In the daemon path, `root_dir` is a temp directory that is `shutil.rmtree()`'d after each request — so every scan re-downloads, re-extracts, and re-parses the same collections from scratch.

### 2. CacheMaintainer service (collection cache)

The CacheMaintainer gRPC service (:50052) downloads collections via `ansible-galaxy collection install` into a **persistent** cache at `~/.apme-data/collection-cache/galaxy/`. It also clones GitHub org repos into `collection-cache/github/`. This cache survives across scans and is managed via `PullGalaxy` / `PullRequirements` / `CloneOrg` RPCs.

### 3. Ansible validator venv builder

The Ansible validator builds per-session venvs with `ansible-core` installed, then **symlinks** collections from the CacheMaintainer's persistent cache into `site-packages/ansible_collections/`. This means the venv already has the full collection source tree available on `PYTHONPATH` — modules, roles, plugins, meta, docs — everything.

### The problem

- **Double download**: The same collection tarball is downloaded twice per scan — once by the scanner's dependency preparator into a throwaway temp dir, and once by CacheMaintainer into the persistent cache.
- **Triple storage**: Collection source may exist in three places simultaneously — scanner temp dir, persistent cache, and venv symlinks.
- **Wasted parse**: The scanner parses collection content to build `Findings` objects (module definitions, role specs, taskfile structures) that are immediately discarded when the temp dir is cleaned up. On the next scan of the same content, all that work is repeated.
- **RAMClient is a vestigial knowledge base**: `RAMClient` (`risk_assessment_model.py`) was designed for ARI's persistent local registry model. APME's daemon architecture made it ephemeral — the filesystem-backed "database" is written into a temp dir and destroyed after each request. Its management API (`list`, `search`, `diff`, `release`) is dead code.
- **The Ansible validator venv already has everything**: The venv built by `venv_builder.py` has `ansible-core` on the path and collections symlinked in. It can resolve FQCNs, load `argument_specs`, inspect `meta/runtime.yml` routing, and access `action_groups` — all things the native validator currently gets from RAMClient's parsed `Findings`.

### The proprietary format boundary problem

Ansible Galaxy collections use a **proprietary packaging format**: custom tarballs containing `galaxy.yml`, `MANIFEST.json`, `FILES.json`, and a non-standard metadata layout. Every piece of APME code that touches these tarballs — `ansible-galaxy collection install`, `dependency_dir_preparator.py`, `pull_galaxy_collection()` — must understand this format and its quirks (flat vs nested tarball layouts, version resolution via Galaxy REST API, dependency metadata in `galaxy.yml` rather than a standard resolver).

Meanwhile, Python has mature, battle-tested standards for exactly this problem: **PEP 427** (wheels), **PEP 503** (simple repository API), **PEP 440** (version specifiers), and **PEP 508** (dependency specifiers). Tools like `pip` and `uv` handle caching, version resolution, dependency trees, and concurrent installs out of the box.

By converting Galaxy tarballs to Python wheels **at the edge** (a proxy boundary), we contain the proprietary format to a single, small service. Everything downstream — the engine, validators, venv builder — operates entirely on standard Python ecosystem primitives. This keeps the engine standards-based and eliminates the need for custom Galaxy-specific caching, resolution, and installation logic throughout the codebase.

### Forces

- The scanner's dependency resolution (tree building) is the critical path — it needs module/role/taskfile definitions to resolve `import_role`, `include_tasks`, FQCN lookups, and action groups.
- The native validator's P-rules (P001–P004, currently excluded from native runs in daemon mode) need `argument_specs` and `action_groups` that come from RAMClient today.
- Performance: the scanner currently re-scans every dependency on every request because the parsed data isn't cached persistently.
- The web gateway (ADR-029) will add a persistence layer (SQLite V1) — a fourth potential store for scan-derived data.
- **Standards alignment**: the engine should not contain custom logic for a proprietary packaging format when standard equivalents exist. Containing the Galaxy format at the boundary reduces maintenance burden and lets us leverage the Python ecosystem's caching and resolution tooling.

## Decision

**We will contain the proprietary Galaxy collection format at a proxy boundary, converting tarballs to standard Python wheels, and use standard Python ecosystem tools for all downstream caching, versioning, and installation.**

The Galaxy proxy (`ansible-collection-proxy`) runs as a sidecar container in the APME pod. It implements PEP 503 (Simple Repository API) and converts Galaxy tarballs to PEP 427 wheels on demand. All downstream consumers install collections via `uv pip install --extra-index-url http://galaxy-proxy:8765/simple/` — standard Python tooling handles caching, version resolution, and dependency management.

Concretely:

1. **Proprietary format contained at the edge**: The Galaxy proxy is the only component that understands Galaxy's tarball format (`galaxy.yml`, `MANIFEST.json`, custom metadata layout). It converts tarballs to wheels with proper PEP 566 metadata, PEP 508 dependency specifiers, and deterministic package names (`ansible-collection-{namespace}-{name}`). Everything downstream operates on standard Python packages.

2. **Caching is the proxy's concern, not the engine's**: The engine has zero cache management code for Galaxy collections. No cache directories to create, no invalidation logic, no "is this collection already downloaded?" checks. The proxy maintains its own wheel cache internally; `uv` maintains its own HTTP and wheel cache per its standard behavior. Both are opaque to the engine — it simply runs `uv pip install` and the ecosystem handles the rest.

3. **Version control is embedded in Python tooling**: Wheel filenames are inherently version-keyed (`ansible_collection_ansible_posix-1.6.0-py3-none-any.whl`). PEP 440 version specifiers (`==`, `>=`, `~=`) work natively in `pip`/`uv`. Multiple versions coexist in the proxy cache without collision. The version-trampling bug in the flat `galaxy/ansible_collections/` layout — where `ansible-galaxy collection install` silently overwrites previous versions — is structurally impossible.

4. **Collection Python dependency resolution for free**: Galaxy collections declare Python dependencies in `requirements.txt` (e.g. `jmespath`, `netaddr`). The proxy embeds these as standard `Requires-Dist` entries in the wheel metadata. When `uv pip install ansible-collection-ansible-utils` runs, `uv` automatically resolves and installs the collection's Python dependencies — no `ansible-builder introspect`, no custom discovery step, no separate `_install_collection_python_deps()` function. Transitive *collection* dependencies (`community.general` depends on `ansible.utils`) are likewise expressed as `Requires-Dist` and resolved by the standard Python dependency resolver.

5. **Multi-Galaxy server support**: The proxy accepts multiple upstream Galaxy/Automation Hub servers with per-server auth tokens, tried in order (like `ansible.cfg`'s `galaxy_server_list`). This supports mixed public/private Galaxy topologies transparently.

6. **CacheMaintainer simplifies**: Galaxy collection management moves to the proxy. CacheMaintainer retains GitHub org cloning (`CloneOrg` RPC) for source-based collections not available on Galaxy.

7. **Persistent parsed metadata** (future): A metadata cache (initially filesystem-backed JSON, later migrated to the web gateway's SQLite) stores parsed `Findings`-equivalent data — module definitions, role specs, taskfile structures, action groups — so the scanner doesn't re-parse unchanged collections.

8. **Venv as a resolution oracle** (future): For P-rules that need `argument_specs` or FQCN resolution, the native validator can query the Ansible validator's venv (via subprocess or a thin gRPC call) rather than maintaining a parallel parsed copy.

## Alternatives Considered

### Alternative 1: Version-keyed tarball cache (no format conversion)

**Description**: Keep the Galaxy tarball format but fix the version-trampling bug by storing tarballs in versioned directories (`galaxy/{ns}/{coll}/{version}/`). The venv builder symlinks from these versioned paths.

**Pros**:
- Fixes the immediate version collision bug
- No new container or external dependency
- Simpler change (filesystem layout only)

**Cons**:
- Engine still contains custom Galaxy-specific caching and resolution logic
- Symlinks are fragile (broken when cache is cleaned while venvs exist)
- No dependency resolution — must manually track transitive collection deps
- Every consumer (scanner, venv builder, CacheMaintainer) needs custom Galaxy-format awareness

**Why not chosen**: Fixes the symptom (version trampling) without addressing the root cause (proprietary format leaking throughout the engine). The proxy approach eliminates the entire class of problems.

### Alternative 2: Keep the status quo

**Description**: Leave the three systems independent. The scanner downloads and parses its own copies; CacheMaintainer maintains the persistent cache; the Ansible validator builds venvs from the cache.

**Pros**:
- No cross-system coupling
- Each system is self-contained

**Cons**:
- Double download on every scan
- Wasted CPU re-parsing unchanged collections
- RAMClient's persistence model is incoherent (writes to temp dirs)
- Will become a quadruple-store problem when web gateway persistence lands

**Why not chosen**: The waste is measurable and grows with collection count. The incoherent persistence model creates confusion for contributors.

### Alternative 3: Make the scanner use the venv directly

**Description**: Instead of maintaining a separate metadata cache, have the scanner shell out to the Ansible validator's venv for all collection resolution (module lookup, role specs, etc.).

**Pros**:
- Single source of truth (the venv)
- `ansible-doc` is the authoritative resolver

**Cons**:
- Subprocess overhead per lookup would be prohibitive during tree building (hundreds of lookups per scan)
- Tight coupling between scanner and Ansible validator lifecycle
- Venv may not exist if Ansible validator is not enabled

**Why not chosen**: Performance characteristics are wrong for the scanner's hot loop. Better suited as a targeted optimization for P-rules (small number of lookups).

### Alternative 4: Merge all caching into the web gateway's SQLite

**Description**: Skip the filesystem metadata layer entirely and persist parsed collection data in the web gateway's database from the start.

**Pros**:
- Single store for everything
- Query-friendly

**Cons**:
- Web gateway doesn't exist yet
- Creates a hard dependency between the engine and the gateway
- Doesn't help the standalone CLI / daemon-without-gateway use case

**Why not chosen**: Premature — the web gateway is not built yet. The filesystem metadata layer can be migrated later.

## Consequences

### Positive

- **Standards-based engine**: The proprietary Galaxy format is contained at a single boundary (the proxy). All downstream code uses standard Python packaging primitives — no custom tarball parsing, no `ansible-galaxy` subprocess calls for installation.
- **Zero cache management in the engine**: The engine does not create cache directories, track downloaded versions, or implement invalidation logic. Caching is entirely the proxy's and `uv`'s concern — opaque to APME.
- **Version safety by design**: Wheels are immutable, version-keyed artifacts. Multiple versions of the same collection coexist naturally in the proxy's cache and in `uv`'s local cache. The flat-directory trampling bug is structurally impossible. PEP 440 version specifiers work natively.
- **Collection Python deps resolved automatically**: `requirements.txt` entries from collections are embedded as `Requires-Dist` in the wheel. `uv pip install` resolves both collection-to-collection and collection-to-Python dependencies in a single pass — eliminates the custom `_install_collection_python_deps()` / `ansible-builder introspect` step.
- **Each collection is downloaded once**: The proxy caches converted wheels persistently. Repeated installs across venvs and scans are instant cache hits served from `uv`'s local wheel cache.
- **Simplified venv builder**: `build_venv` replaces symlink management with a single `uv pip install --extra-index-url` call. No `_resolve_collection_path`, no `collection_path_in_cache`, no `_install_collection_python_deps` for Galaxy sources.
- **Multi-Galaxy support**: Private Automation Hub / Galaxy NG servers work via `--galaxy-server` flags on the proxy, transparent to all consumers.
- Foundation for web gateway persistence migration

### Negative

- **New container**: The proxy adds a sidecar to the pod (lightweight — Python + FastAPI + uvicorn)
- **Network dependency**: Collection installs require the proxy to be running. Mitigated by the pod topology (all containers share localhost)
- **Wheel conversion fidelity**: Edge cases in Galaxy tarball layouts (flat vs nested, missing `MANIFEST.json`) must be handled by the converter. Currently covered by `ansible-collection-proxy` test suite.
- Migration effort: `dependency_dir_preparator`, scanner wiring, and RAMClient still need refactoring (Phase 2+)

### Neutral

- CacheMaintainer retains GitHub clone functionality — only Galaxy collection management moves to the proxy
- OPA validator is unaffected — it consumes the hierarchy payload, not collection source
- The dead RAMClient management API (`list`, `search`, `diff`, `release`) should be removed regardless (cleanup, not architecture)

## Implementation Notes

### Phase 1: Dead code removal — COMPLETE (PR #49)
- Removed dead `engine/cli/` (ARICLI/RAMCLI), `ram_generator.py`, `key_test.py`, backup/sample annotators, empty `engine/rules/`
- Pruned dead RAMClient public methods (`list_all_ram_metadata`, `diff`, `release`); privatized internal search methods
- Removed orphan `main()` functions and `__main__` blocks from 6 live modules
- Removed dead utility functions (`diff_files_data`, `show_all_ram_metadata`, `show_diffs`, `version_to_num`)
- **~3,100 lines removed** across 25 files (14 deleted, 11 trimmed)

### Phase 2: Galaxy proxy integration — COMPLETE
- **Proxy repo** (`ansible-collection-proxy`): Multi-Galaxy URL support, Containerfile
- **APME venv builder**: `build_venv` uses `uv pip install --extra-index-url` when `APME_GALAXY_PROXY_URL` is set; falls back to symlink path otherwise
- **Primary orchestrator**: `_ensure_collections_cached` skips CacheMaintainer pre-pull when proxy is active (on-demand via pip)
- **Pod topology**: Galaxy proxy container added to `pod.yaml`, env var wired to ansible + primary containers

### Phase 3: Session-scoped venvs as shared assets — COMPLETE

Sessions are long-lived containers identified by a client-provided `session_id`. Within a session, venvs are keyed by `ansible_core_version` — like tox matrix entries. Collections are installed **incrementally** (additive, never destructive). Old core-version venvs are retained until TTL reaping.

#### Architecture: single writer, many readers

The **Primary orchestrator** is the sole venv authority. It calls `VenvSessionManager.acquire()` (which may install collections) **before** fanning out to validators. Validators mount the sessions volume **read-only** — they receive a `venv_path` in `ValidateRequest` and use it as-is. This eliminates concurrent validator writes and corruption risk.

```
Client → ScanRequest(session_id) → Primary
    Primary → VenvSessionManager.acquire(session_id, core_version, specs) → sessions volume (read-write)
    Primary → ValidateRequest(venv_path=...) → Ansible Validator (sessions volume read-only)
    Primary → run_scan(dependency_dir=venv_site_packages) → ARI Engine (reads from session venv)
```

#### Storage layout

```
$SESSIONS_ROOT/
  <session_id>/
    <core_version>/
      venv/           # full virtualenv
      meta.json       # {installed_collections, created_at, last_used_at}
    session.json      # session-level metadata
    .lock             # fcntl flock target
```

#### Key design properties

- **Additive, never destructive**: Collections are only added, never removed. A new core version creates a sibling, not a replacement.
- **Idempotent installs**: `uv pip install` is a no-op for already-installed packages. Warm sessions pay near-zero cost.
- **Client controls identity**: `session_id` is always client-provided. The CLI derives it automatically from the project root (SHA-256 hash of the resolved path to the nearest `.git`, `galaxy.yml`, `requirements.yml`, `ansible.cfg`, or `pyproject.toml` directory). Users can override with `--session <id>`.
- **TTL-based reaping**: Individual core-version venvs can expire independently.
- **Volume-shared**: One venv build serves all validators in the pod — no double-install.

#### Changes

- **Proto**: `session_id` added to `ScanRequest`, `ScanResponse`, `ScanOptions`, `FixOptions`; `session_id` + `venv_path` added to `ValidateRequest`
- **VenvSessionManager**: Refactored from flat `sessions/<sid>/venv/` to multi-version `sessions/<sid>/<version>/venv/` layout. `acquire()` does incremental installs. `reap_expired()` operates per core-version venv. New public helpers `create_base_venv()` and `install_collections_incremental()` in `venv_builder.py`.
- **Primary**: Creates `VenvSessionManager` singleton. Checks for warm session before ARI scan (passes `dependency_dir`). Calls `acquire()` after collection discovery for incremental install. Sets `venv_path` on `ValidateRequest`.
- **Ansible validator**: When `venv_path` is set in request, uses it directly (read-only). Falls back to `build_venv()` when empty.
- **ARI scanner**: `run_scan()` receives `dependency_dir` pointing to the session venv's site-packages. The `install_dependencies` parameter and the entire ARI dependency download pipeline have been removed (see Phase 4). ARI never downloads collections — the session manager is the sole authority.
- **Pod topology**: `sessions` volume added — read-write for Primary, read-only for Ansible validator.

#### Future work

- **Python version as a venv axis**: The venv key could expand from `(session_id, ansible_core_version)` to `(session_id, ansible_core_version, python_version)`, making the matrix three-dimensional (like tox's `{py310,py311} x {ansible2.17,ansible2.18}`). `uv` makes this trivial — `uv venv --python 3.12` downloads and pins the interpreter automatically. Not needed now (pod containers pin a single Python), but essential for scanning content destined for different EE Python versions.

### Phase 4: ARI dependency pipeline dead code removal — COMPLETE
With Phase 3's session-scoped venvs, the daemon path no longer needs ARI's collection downloading. `SingleScan._prepare_dependencies()` — the sole entry point to the `DependencyDirPreparator` pipeline — was **never reached**. The entire collection-downloading codepath in the vendored ARI engine was removed (~2,000+ lines):

- `dependency_dir_preparator.py` (~1,362 lines): `download_galaxy_collection()`, `install_galaxy_collection_from_targz()`, `install_galaxy_collection_from_reqfile()`, and all `ansible-galaxy` subprocess calls
- `dependency_loading.py` (~211 lines): Orchestrates the preparator
- `dependency_finder.py` (~497 lines): Discovers dependencies to download
- `utils.py` (partial): `install_galaxy_target()` helper

**No shim is needed.** The original shim approach (documented in the previous revision) tried to preserve ARI's internal contract by replacing the *implementation* behind `ansible-galaxy` calls. But since the session manager now handles all collection management *before* ARI runs, the entire pipeline is unreachable. Clean removal is simpler and safer than shimming.

- Removed `dependency_dir_preparator.py`, `dependency_loading.py`, `dependency_finder.py`
- Removed `install_galaxy_target()` and related helpers from `utils.py`
- Removed the `install_dependencies` parameter from `scanner.evaluate()` and `run_scan()`
- Removed `_cli_legacy.py` and its tests (`test_cli.py`, `test_cli_health_and_diagnostics.py`)
- `ansible-galaxy` is no longer required for collection operations (roles remain on the legacy path for now)
- **Net reduction: ~5,100 lines of dead code removed**

### Phase 5: Native validator leverages venv for P-rules
- For `search_action_group()` and argument spec validation, query the Ansible validator's venv (via subprocess or thin gRPC call)
- Re-enable P001–P004 in daemon mode (currently excluded because they require `ram_client` with populated data)

### Phase 6: Web gateway migration (future, ADR-029)
- Migrate the filesystem metadata layer to SQLite/PostgreSQL
- RAMClient becomes a DB-backed read-through cache

## Related Decisions

- [ADR-003](ADR-003-vendor-ari-engine.md): Vendor ARI Engine — established the engine as integrated code
- [ADR-009](ADR-009-remediation-engine.md): Separate Remediation Engine — validators are read-only
- [ADR-022](ADR-022-session-scoped-venvs.md): Session-Scoped Venvs — venv lifecycle
- [ADR-029](ADR-029-web-gateway-architecture.md): Web Gateway Architecture — future persistence layer

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-19 | AI-assisted | Initial proposal |
| 2026-03-19 | AI-assisted | Phase 1 complete (PR #49): ~3,100 lines of dead ARI code removed |
| 2026-03-21 | AI-assisted | Revised decision: Galaxy proxy approach replaces custom cache. Added proprietary format boundary justification, standards-based caching rationale, and Python ecosystem delegation arguments. |
| 2026-03-21 | AI-assisted | Phase 3: Session-scoped venvs as shared assets. Multi-version layout, incremental installs, single-writer/many-readers architecture, ARI dependency_dir integration. |
| 2026-03-21 | AI-assisted | Phase 4 revised: shim replaced with dead code removal (~2,000 lines). Session manager makes ARI's dependency pipeline unreachable. |