# PR #751 — merge + full review report

Repo: `ansible/apme` · PR: `fix: resolve net-new full-tree review findings across gateway, engine, proxy, and CLI`
Base `main` @ `d5948747` · PR head `d66a2773` · merge-base `64e2e72e` · PR diff **113 files, +19,915 / −833**

Local merge branch: `pr751-merge`, merge commit `a8449c20 Merge origin/main into pr751 (conflict resolution)` (239 files, +27,225 / −2,646).

---

## 1. Verdict

The merge is complete and behaviour-preserving, and the PR's substance is real and generally well built:
input-trust boundaries (repo URL SSRF, Galaxy collection specs, upload path/byte caps), idempotent submit
claims with durable replay, a durable notification outbox, bounded concurrency primitives (`BoundedCache`,
`VenvGenerationLock`, proxy lock → 503), and splice-outcome accounting all match their stated intent and are
covered by tests.

**One blocking defect** was found that the PR's own test plan could not see (it never had a working
PostgreSQL): a `SELECT … FOR UPDATE` the PR added to notification generation **deadlocks an existing
`main` test indefinitely** under PostgreSQL, and introduces an unbounded row-lock wait on the `scans` table in
production. One-line fix, proven below (§4.1).

Three further low-severity findings (§4.2–4.4) and a set of nits (§5).

**All four findings have since been fixed** on this branch (commit "fix: address review findings"), each with a
regression test that fails before the fix; §7 lists the changes and the re-verification. The sections below keep
the original analysis (and the pre-fix measurements) as the rationale of record.

---

## 2. Merge: conflicts and how they were resolved

`git merge --no-commit --no-ff origin/main` → 10 conflicted files. Resolution principle throughout: **union of
intent** — keep `main`'s newer machinery and layer the PR's accounting/typing on top; never silently drop a
side's invariant.

| File | Resolution |
|---|---|
| `.github/workflows/test.yml` | Took `main`. The PR edited a job `main` had deleted. |
| `src/apme/v1/engine_pb2.py` | **Regenerated** with `scripts/gen_grpc.sh` (grpcio-tools 1.84.0 / protobuf 7.35.1) instead of hand-merging. Proto auto-merged cleanly, so generation is authoritative. Verified by descriptor round-trip: `ScanChunk.dependency_files`, `ApprovalRequest.approval_token`, `ProposalsReady.approval_token`, `SessionError.recoverable`, `ApprovalAck.unpatched_count/unpatched_files`. |
| `src/apme/v1/engine_pb2.pyi` | Hand-written file (not produced by `gen_grpc.sh`). Added the two declarations the PR forgot: `ScanChunk.dependency_files` and `FormatResponse.diffs`. AST-vs-descriptor check now reports *stub matches descriptors* (see finding §4.2). |
| `src/apme_engine/daemon/engine_server.py` | Kept `main`'s preview transaction + `SpliceConflictError` handling; layered the PR's splice-outcome accounting (`splice_with_outcome`, `unpatched` warning, `remediation_failed` `SessionError`). Ruff collapsed the imports to `from apme_engine.remediation.graph_engine import AIPolicyContext, SpliceConflictError`. Verified `main`'s `seen_names` Galaxy-server de-duplication survived as `_normalized_session_galaxy_servers` (now case-insensitive), and that the PR's removal of the process-wide `ANSIBLE_CONFIG` override is intentional (per-request Galaxy env forwarding). |
| `src/apme_engine/graph/content_graph.py` | `main`'s pinned baseline/eviction **plus** the PR's `_state_seq` monotonic ids and serialization docs. `_violation_key` gains the `diagnostic_discriminator` third tuple component; `_violation_record_from_dict` recomputes it and migrates legacy 3-tuples. |
| `src/apme_engine/remediation/graph_engine.py` | Layered the PR's `SpliceResult` / `SpliceOutcome` / `splice_with_outcome` on `main`'s source-line merge core (`_SourceEdit`, `_merge_source_edits`, `_yaml_line_roles`, `SpliceConflictError`). `splice_modifications` kept as a list-compatible wrapper so existing callers/tests still work. |
| `src/apme_engine/remediation/partition.py` | Both routing guards everywhere: `MANUAL_REVIEW` first, then `_has_explicit_scope`, so Tier-1 classification agrees with `classify_violation` (scope-less findings stay non-AI-routable, ADR-026). |
| `src/apme_gateway/scan/driver.py` | Union of both import blocks (13 names, each grep-verified as used). |
| `src/apme_gateway/session_client.py` | Docstring union: proposals carry `tier` **and** `approval_token`; ack keeps `status` plus `unpatched_count` / `unpatched_files`. |
| `tests/test_graph_remediation.py` | Kept HEAD's `TestGraphEngineCancellation` + `main`'s `test_splicing_retains_early_fixes_after_progression_rollover`. |
| `tests/test_session.py` | Kept HEAD's `test_gate1_approval_preserves_unpatched_without_pre_gate2_snapshot` + `main`'s autouse `_mock_native_for_upstream_transaction_tests`; adopted `main`'s `splice_with_outcome` call form while keeping `side_effect=SpliceConflictError(...)` so the conflict path stays exercised. |

Pre-commit on the merge commit: **all hooks pass** — ruff, ruff format, mypy, pydoclint, skillmark,
rule-catalog, ADR-059 graph-boundary, SKILL.md frontmatter, OpenAPI check.

---

## 3. Verification

Environment was built from scratch (gcc + libonig-dev, `uv sync --frozen --extra dev --extra gateway`,
PostgreSQL 17 on `127.0.0.1:5432`).

| Check | Result |
|---|---|
| Full suite, `origin/main` (fresh DBs) | **74 failed, 6,044 passed, 103 skipped, 88 deselected** (122.9s) |
| Full suite, merged tree (fresh DBs) | **74 failed, 6,380 passed, 103 skipped, 89 deselected** (181.2s) |
| Failure-set diff merged vs main | **identical** (`diff /tmp/m3.txt /tmp/mn3.txt` → empty) |
| `ruff check .` / `ruff format --check .` | clean / 540 files already formatted |
| `pydoclint` 0.11.1 (pinned to `.pre-commit-config.yaml`) | no violations |
| mypy | merged **938** errors/130 files vs main **874** → **+64** (§5.5) |
| `engine_pb2.pyi` vs descriptors | matches |
| Splice/remediation/ledger suites | 645 passed (`test_graph_remediation`, `test_session`, `test_remediation`, `test_node_state`, `test_content_graph`) |

Baseline failures (identical on both sides, all environmental): 63 × `tests/rule_doc_integration_test.py`
(needs the OPA binary), 6 × `tests/test_observability_metrics.py`, 3 × `tests/test_engine_pipeline_coverage.py`,
1 × `tests/test_git_requirements.py`, 1 × `tests/test_container_volume_permissions.py`.

> The two full runs above **exclude `tests/test_gateway_notifications.py`**, which hangs on the merged tree —
> that exclusion is what finding §4.1 turns on. Post-fix re-verification (both trees, same command, file included)
> is in §7: **71 failed / 6,438 passed / 103 skipped**, failure set **identical to `origin/main`'s 71**, and the
> notification suite runs in 3.7 s instead of hanging.

---

## 4. Findings

### 4.1 BLOCKING — PR-added `SELECT … FOR UPDATE` deadlocks an existing `main` test (and adds an unbounded row-lock wait)

**Where:** `src/apme_gateway/grpc_reporting/servicer.py:885`

```python
scan = (await db.execute(sa_select(Scan).where(Scan.scan_id == scan_id).with_for_update())).scalar_one_or_none()
```

`with_for_update()` is **new in this PR** (`e631aee0`, "serialize per-scan notification generation by locking
the Scan row"). `main` has the same line without the lock.

**Symptom:** `tests/test_gateway_notifications.py::TestGenerateNotifications::test_unlinked_grpc_notification_is_replaced_after_scan_link`
— a test that already exists on `main` — **never finishes**. The suite stops after 12 dots with no summary
and no timeout (there is no `pytest-timeout` in this project), i.e. it burns the whole CI job.

**Proof of deadlock** (PostgreSQL, mid-hang):

```
 pid   | datname           | state               | wait_event_type | wait_event    | q
-------+-------------------+---------------------+-----------------+---------------+-------------------------------------------
 178540| apme_test_master  | idle in transaction | Client          | ClientRead    | SELECT notifications.id, notifications.type, …
 178817| apme_test_master  | active              | Lock            | transactionid | UPDATE scans SET project_id=$1::VARCHAR, …
```

The gRPC writer holds the `scans` row lock inside its open transaction and is parked in Python waiting for a
second writer; the test's main coroutine is blocked in `q.link_scan_to_project()` doing `UPDATE scans`; the
second writer is only *created after* that update returns. Circular wait → indefinite.

**Cross-check that it is PR-caused, not environmental:**

| Tree | Result on that test (fresh DB) |
|---|---|
| `origin/main` (`d5948747`) | **1 passed** in 0.47s |
| `pr751` (`d66a2773`) | **hangs** (killed at 90s) |
| `pr751-merge` (`a8449c20`) | **hangs** (killed at 300s) |
| `pr751-merge` with `with_for_update()` removed | **29 passed** in 3.55s |

**The lock is redundant**, which is why removing it is safe:

* `Notification.__table_args__` already has `Index("uq_notifications_scan_id_type", "scan_id", "type", unique=True)`
  — the DB already prevents the duplicate-key race the lock was added for.
* The second producer, the operate/WebSocket path (`api/router.py:3194`), calls `generate_notifications()`
  **without** taking the lock, so the serialization was never complete anyway. The PR's own concurrency test
  (`test_concurrent_grpc_and_operate_writers_emit_one_attributed_notification`) passes with the lock removed.

**Production impact (beyond the test):** `scans` is written by submit claims, `link_scan_to_project`, operation
state transitions and the rule-catalog/health paths. A notification generation that is slow or stalled holds a
row lock with **no `lock_timeout`** anywhere in the gateway, so those writers wait indefinitely *while holding a
pooled DB connection* — a plausible pool-exhaustion cascade under notification backlogs.

**Recommended fix (either):**

1. Delete `.with_for_update()` — rely on `uq_notifications_scan_id_type` plus the existing replace-unattributed
   logic (proven green: 29/29).
2. If a scan-level critical section is genuinely wanted, make it bounded and universal: `SET LOCAL lock_timeout`
   (or `with_for_update(nowait=True)` + retry) **and** take the same lock in the operate path, otherwise the
   lock only adds latency and one deadlock.

---

### 4.2 LOW — `engine_pb2.pyi` is hand-maintained and had already drifted

`src/apme/v1/engine_pb2.pyi` ("Stub for generated engine_pb2 (proto types).") is not produced by
`scripts/gen_grpc.sh`, so it silently drifts from `engine.proto`. At PR head it was missing
`ScanChunk.dependency_files` and `FormatResponse.diffs`. Both were added during this merge so the tree is
consistent, but the next proto change will drift again.

**Fix:** generate the stub in `gen_grpc.sh` (`protoc --pyi_out`) and fail CI when it differs, exactly like
`docs/api/openapi.v1.json` is checked.

### 4.3 LOW — two upload-path canonicalizers with different semantics; docstrings overstate consistency

* `apme_engine/daemon/fs_utils.canonicalize_upload_relpath` — strict: rejects leading `/`, `C:` drive paths,
  root escapes; used by `write_chunked_fs` and the Gateway WebSocket upload path (`session_client`), which also
  fails fast on backslash collisions via `is_trivial_respelling`.
* `apme_engine/daemon/engine_upload._normalize_upload_path` — permissive: `lstrip("/")`, silently rebases
  absolute paths; used by `Engine._accumulate_chunks`, `Format`, `_stage_files`, `_session_upload_append`.

Consequences: an `/etc/passwd`-style path is *rejected* on one ingress and *rebased* on another; and on Linux
`a\b.yml` and `a/b.yml` are two genuinely different files, but the engine ingresses normalize-then-last-wins and
**silently drop one** instead of erroring, while the Gateway path errors. That contradicts the docstring claims
"one payload is accepted-or-rejected identically on every ingress path" (`engine_server.py:2386`, `:2463`).

No path escape is possible (both helpers stay inside the session root), so this is a correctness/consistency
defect, not a security hole. **Fix:** one canonicalizer, with collision detection factored into the shared
helper.

### 4.4 LOW — `apme submit` cannot replay: it mints a fresh idempotency key per invocation

`cli/gateway_client.py:submit_operation` sends `Idempotency-Key: uuid.uuid4().hex` on **every** call, and
`cli/submit_cmd.py` exposes no flag to supply or print a token. So the durable replay machinery the PR built
(for exactly this: a CLI that timed out while the Gateway was still pushing) is unreachable from the CLI:

* Retry after the 420s timeout → new token → no durable replay match → the request either 503s (scan lock still
  held by the first attempt) or 409s once `scan.pr_url` is set.
* The user is told to "check the activity or target branch before retrying" and gets no way to replay.

**Fix:** accept `--idempotency-key` (or generate-and-print, then reuse from `--json` output) so a retry replays
the stored `SubmitResponse`.

---

## 5. Notes, nits, observations

1. **Splice merge semantics** — `SpliceOutcome.unpatched` includes approximate-baseline files that still emitted
   a best-effort patch, so `unpatched_count == 0` can never mask a truncated diff; `strict=True` escalates skips
   to `ValueError`; `SpliceConflictError` subclasses `ValueError` and covers ambiguous/conflicting edits and
   invalid YAML. Production (`engine_server`) uses `splice_with_outcome` exclusively; `splice_modifications`
   remains a list-compatible wrapper. Intact after the merge.
2. **Trust boundaries re-verified by hand** — `validate_collection_spec` rejects `-rf`, `; rm -rf /`, `&&`,
   `\n--extra-index-url`, `@`, `[]`, and `>=1.0.*`; downloads use `create_subprocess_exec` (no shell) with a
   `--` separator. `validate_repo_url` is https-only, no userinfo, implicit/443 only, blocks
   `169.254.169.254` + loopback/link-local/multicast/unspecified/reserved, with `_ssrf_allow_private()` opt-in
   and a fail-closed per-host token allowlist.
3. **Upload/ingress caps** — 256 MiB / 2,000 files per session, 16 MiB pre-parse import body (`413`),
   `APME_SCAN_MAX_FILES` / `APME_SCAN_MAX_BYTES` enforced in `build_scan_bundle` *before* reading a candidate's
   remainder. Note `to_visit` is still materialized eagerly, so the cap bounds bytes read, not traversal memory
   (bounded only by `SKIP_DIRS`/`.hg` pruning) — acceptable, but a pathological tree still allocates a full path
   list.
4. **DB migrations** — there is no Alembic; `db/__init__.py` keeps idempotent `ALTER TABLE` migrations. The PR
   correctly extended them (`scans.submit_token`, `scans.scm_provider`) and added a fail-loud duplicate check
   before creating `uq_scans_project_submit_token`. Nit: `CREATE UNIQUE INDEX IF NOT EXISTS` is racy between two
   Gateway instances starting at the same instant (Postgres duplicate-`pg_class` error); a `try/except` +
   re-inspect would be safer.
5. **mypy delta (+64)** — merged 938 vs main 874 errors, concentrated in: `tests/test_gateway_ingress_caps.py`
   (+25), `src/apme_gateway/api/schemas.py` (+11), `tests/test_submit_idempotency.py` (+5),
   `src/galaxy_proxy/proxy/server.py` (+4), `tests/test_session.py` (+3), `api/router.py` (+3). All in
   `mypy`-advisory files (the repo's mypy gate is not clean on `main` either), but the new code adds ~64.
6. **`assert` in production paths** — two new ones (`session_client.py:443`, `galaxy_proxy/proxy/server.py:1290`)
   are invariant documentation only; behaviour is identical under `python -O`.
7. **ADR/OpenAPI** — ADR-060 additive-only contract holds: `detail` stays a dict, new error codes are additive,
   `docs/api/openapi.v1.json` is current (`tox -e openapi -- --check` passes via the pre-commit hook).
8. **PR test-plan gap (process)** — the PR body repeatedly states PostgreSQL was unavailable locally
   ("localhost:5432 refused"), and the last full-suite green predates `e631aee0`. That is precisely why §4.1
   escaped review. Any change touching `with_for_update`, row locks or the outbox needs a PG-backed run.
9. **Blue-green check:** every PR-added `except Exception` in the reviewed surface is either best-effort
   (notifications/outbox, mirroring ADR-020/029) or followed by `rollback()` + a warning; no silent swallowing of
   control flow.

---

## 6. Recommended before merge

1. **Apply the §4.1 fix** (drop `with_for_update()`, ideally with a regression test that runs the
   `_generate_scan_notifications` + `link_scan_to_project` interleaving under a bounded `lock_timeout`).
2. Add `tests/test_gateway_notifications.py` back to the full-suite command in CI and make sure `pytest-timeout`
   (or a per-test `asyncio.wait_for`) exists, so a future hang fails fast instead of burning the job.
3. Optional, cheap: regenerate `engine_pb2.pyi` in `gen_grpc.sh` (§4.2), unify the upload-path canonicalizers
   (§4.3), add `--idempotency-key` to `apme submit` (§4.4).

---

## 7. Fixes applied (post-review)

All four findings are fixed on branch `pr751-merge`, one commit on top of the merge commit. Each fix is paired
with a test that was verified to fail before the change.

### 7.1 §4.1 — deadlock removed

* `src/apme_gateway/grpc_reporting/servicer.py` — dropped `.with_for_update()` from the `scans` reload in
  `_generate_scan_notifications`; the docstring now records *why* (unique `uq_notifications_scan_id_type` already
  closes the duplicate race, the operate path never took the lock, and the lock was unbounded).
* `tests/test_gateway_notifications.py::test_generation_does_not_lock_the_scan_row` — parks generation inside its
  open transaction (via a `_should_write_scan_complete` hook), then requires `link_scan_to_project` to complete
  under a 10 s `asyncio.wait_for`, and finally joins the generation task. With `with_for_update()` restored the
  test fails in ~10 s with `TimeoutError` (verified); without it, it passes.
* Releasing the lock does not reopen the race: `test_concurrent_grpc_and_operate_writers_emit_one_attributed_notification`
  (the PR's own two-writer test) and `test_unlinked_grpc_notification_is_replaced_after_scan_link` (the `main`
  test that hung) both pass.

### 7.2 §4.2 — stub drift is now a test failure

The four `src/apme/v1/*_pb2.pyi` files are a deliberate hand-written typing layer (protoc's `--pyi_out` variant
uses container/`_ClassVar` spellings the codebase does not type against), so instead of replacing them the drift
itself is now guarded:

* `tests/test_pb2_stub_consistency.py` — AST-parses each stub and compares it with the live descriptors in both
  directions: every proto field must be annotated, every annotation must still exist, every message must have a
  stub class, and no stub class may be orphaned (`map<>` entry messages are excluded as synthetic). 9 tests.
* The guard immediately found a **second, pre-existing drift** the review missed: `validate_pb2.pyi::ValidateResponse`
  declared only `logs`, while production code reads/writes `violations`, `request_id` and `diagnostics`. Fixed in
  `src/apme/v1/validate_pb2.pyi` (now `list[Violation]` / `str` / `ValidatorDiagnostics`).

### 7.3 §4.3 — one canonicalizer, collision detection everywhere

* `src/apme_engine/daemon/fs_utils.py` — new `canonicalize_upload_entries()` owns the ingress policy: strict
  `canonicalize_upload_relpath` keys, last-wins for respellings, and `ValueError` on a genuine
  post-canonicalization collision. `write_chunked_fs()` now delegates to it (its duplicate copy is gone).
* `src/apme_engine/daemon/engine_upload.py` — `_normalize_upload_path()` now *is*
  `canonicalize_upload_relpath` (single source of truth, no re-implementation); `_hash_upload_chunk()` and
  `_session_upload_append()` use `canonicalize_upload_entries()`, so `Scan`/`Format`/`FixSession` and the Gateway
  WebSocket path now accept and reject exactly the same payloads.
* Behaviour change (deliberate, fail-closed): the engine no longer silently rebases absolute upload paths
  (`/a.yml`, `C:a.yml`, `\a.yml`) — they are rejected with the same message the Gateway and
  `write_chunked_fs` already used. Previously `/a.yml` was stripped to `a.yml` on `Scan`/`Format` but rejected on
  the Gateway, which was the inconsistency being fixed. Docstrings at `engine_server.py` and `engine_upload.py`
  now state the shared semantics instead of overstating them.
* Tests: `tests/test_upload_relpath.py` (+4: dedupe/collision/escape on the shared helper, and a
  Scan-ingress-vs-materializer parity matrix), `tests/test_review_fixes_engine_trust.py` updated for the strict
  absolute-path rule.

### 7.4 §4.4 — `apme submit` can replay

* `src/apme_engine/submit_token.py` (new) — `is_valid_submit_token()` / `generate_submit_token()`, the single
  predicate both sides use. Allowed to live in `apme_engine` because the Gateway already imports that package;
  the reverse would drag the server stack into engine-only installs.
* `src/apme_gateway/api/submit_idempotency.py` — `_is_valid_submit_token` and `_SUBMIT_TOKEN_MAX_CHARS` now
  delegate to it, so the CLI cannot drift from the server's entropy rule.
* `src/apme_engine/cli/parser.py` — `apme submit --idempotency-key <key>`.
* `src/apme_engine/cli/submit_cmd.py` — resolves the key *before* the request (generating one if absent) and
  prints `Re-run with --idempotency-key <token> to replay the same submit instead of pushing again.` on timeout,
  409 and 503 (and on transport errors, which can surface after the request reached the Gateway). A weak
  explicit key exits 1 without a round trip.
* `src/apme_engine/cli/gateway_client.py` — `submit_operation(..., idempotency_key=...)` validates the token and
  sends it as the header.
* Tests: `tests/test_submit_cli_idempotency.py` (12) cover the predicate, the parser flag, caller-supplied token
  reuse, pre-flight rejection of a weak key, and the replay hint on each ambiguous failure path. Docs updated in
  `docs/guides/CLI.md` and `.agents/skills/apme-submit/SKILL.md`.

### 7.5 Re-verification

| Check | Result |
|---|---|
| Full suite, `origin/main` (fresh DBs, `-n 8`, PATH incl. `.venv/bin`) | **71 failed, 6,076 passed, 103 skipped** |
| Full suite, merged + fixes (same command) | **71 failed, 6,438 passed, 103 skipped** |
| Failure-set diff | **identical, both directions empty** (no new failures; 3 order-dependent `test_engine_pipeline_coverage` cases now pass on both trees) |
| `tests/test_gateway_notifications.py` | **30 passed in 3.7 s** (was: hang) |
| Targeted suites (`test_gateway_notifications`, `test_pb2_stub_consistency`, `test_submit_cli_idempotency`, `test_upload_relpath`, `test_review_fixes_engine_trust`) | 114 passed |
| Upload/ingress/session group (`test_session`, `test_gateway_ingress_caps`, `test_scan_driver`, `test_review_fixes_*`, …) | 444 passed |
| CLI/gateway submit group (`test_cli_surface_coverage`, `test_submit_idempotency`, `test_sbom_cli`) | 175 passed |
| `ruff check .` / `ruff format .` | clean / 543 files already formatted |
| mypy `--strict src/ tests/ scripts/` | **0 errors in any file touched by the fixes** (pre-existing repo-wide debt unchanged) |
| `pydoclint` 0.11.1 (pinned rev) | 🎉 no violations |
| `validate_pb2.pyi` / all four stubs vs descriptors | consistent (enforced by the new test) |

One caveat on failure parity: `tests/test_session_venv_e2e.py::test_sibling_core_version` is order-dependent
under `pytest -n 8` — it reads a session venv created by the module-scoped `session_venv` fixture without requesting
it, so it only passes when xdist happens to place it after a test that triggered the fixture. It fails identically
on `origin/main` when run in isolation, and it passed on both trees in the run above; it appears in the merged
column only when the scheduling lands that way. Not a regression.

Remaining §5 nits (Alembic-less migrations, racy `CREATE UNIQUE INDEX IF NOT EXISTS`, eager `to_visit`
materialization, two new invariant-documenting `assert`s) are unchanged — they are pre-existing style/tooling
debt, not regressions, and each is recorded with its reasoning above.
