# Codebase Concerns

**Analysis Date:** 2026-03-25

## Tech Debt

**Incomplete Loop Support in Model Loader:**
- Issue: Multiple loop syntax variants (`with_indexed_items`, `with_flattened`, `with_together`, `with_sequence`, `with_subelements`, `with_nested`, `with_cartesian`, `with_random_choice`) are not yet supported
- Files: `src/apme_engine/engine/model_loader.py` (lines 101-118)
- Impact: Playbooks using these loop constructs will not be fully parsed; violations in these patterns may be missed
- Fix approach: Add parsers for each unsupported loop type alongside existing `loop`, `with_list`, `with_items`, `with_dict` handlers

**Module and Dependency Resolution Incomplete:**
- Issue: `TODO: dependency check, especially for collection dependencies for role` at `src/apme_engine/engine/tree.py:850`
- Files: `src/apme_engine/engine/tree.py`
- Impact: Collection-level dependencies may not be properly validated; role references to collection modules could be unresolved
- Fix approach: Implement full dependency graph traversal for roles → collections → modules

**Missing Taskfile Variable Resolution:**
- Issue: Taskfile references with variables are not supported
- Files: `src/apme_engine/engine/risk_assessment_model.py:996`
- Impact: Dynamic taskfile includes (e.g., `include_tasks: "{{ var }}/tasks.yml"`) will fail to resolve; violations in these files will not be found
- Fix approach: Implement variable interpolation in taskfile path resolution

**YAML Label Assignment Incomplete:**
- Issue: `TODO: need more-detailed labels like 'vars'? (currently use the passed one as is)` suggests YAML labels for complex structures are underutilized
- Files: `src/apme_engine/engine/model_loader.py:391`
- Impact: Certain YAML structures may not have meaningful semantic labels, affecting rule targeting
- Fix approach: Expand label system to distinguish between vars blocks, task definitions, play headers, etc.

**Partial INI File Parsing:**
- Issue: `TODO: parse it as INI file` indicates inventory/config INI parsing is not implemented
- Files: `src/apme_engine/engine/model_loader.py:349`
- Impact: INI-formatted inventory files will not be parsed; violations in INI inventory will be missed
- Fix approach: Add ruamel.yaml-compatible INI parser or use configparser

**Pre-computed YAML Label List Assumption:**
- Issue: `TODO: support loading without pre-computed yaml_label_list` and `TODO: support non-YAML files`
- Files: `src/apme_engine/engine/model_loader.py:474-475`
- Impact: Engine assumes labels are pre-computed; dynamic content scanning is limited
- Fix approach: Implement on-demand label computation during repository loading

## Known Bugs

**WebSocket State Emission Ordering:**
- Bug: "started" message with scan_id must be sent before "result", "proposals", or "progress" events
- Symptoms: UI may not receive scan_id if Primary sends result without prior progress updates
- Files: `src/apme_gateway/api/router.py`
- Trigger: Scans that complete quickly without progress updates
- Status: Fixed in commit b90a0d4; tracked with `_ensure_started()` helper to guarantee emission ordering
- Workaround: Now automatically emitted before any state message

**Multi-threading YAML Serialization Issue:**
- Bug: ruamel.yaml has a known multi-threading bug that can cause EmitterError
- Symptoms: Sporadic serialization failures in concurrent file processing
- Files: `src/apme_engine/engine/yaml.py:75-81` (includes retry logic up to 2 times)
- Trigger: Concurrent YAML dump operations from multiple threads
- Current mitigation: Retry up to 2 times with fresh YAML instance per thread-local context
- Risk: If retries exhaust, exception propagates; should add telemetry to monitor retry frequency

## Security Considerations

**Broad Exception Handlers Masking Errors:**
- Risk: Multiple `except Exception:` handlers in gateway and scanner code silently swallow all exceptions
- Files: `src/apme_gateway/api/router.py:88-89, 109, 147, 185, 1010-1012`, `src/apme_engine/remediation/abbenay_provider.py:748, 806, 889`
- Impact: Authentication failures, network errors, or configuration problems may go unnoticed; health checks and preflight tests return False without logging root cause
- Current mitigation: Some handlers log via logger.exception() before returning False (e.g., `abbenay_provider.py:749`)
- Recommendations:
  - Replace broad `except Exception:` with specific exception types (e.g., `except (grpc.aio.AioRpcError, TimeoutError, ConnectionError)`)
  - Ensure all exception handlers log the root cause with logger.exception()
  - Add structured error codes for operational debugging

**Silent JSON Parsing Failures:**
- Risk: JSON decode and type errors in Abbenay response parsing catch exceptions and return None without logging details
- Files: `src/apme_engine/remediation/abbenay_provider.py:383-384, 437, 503`
- Impact: AI fix proposals silently fail to parse; users see no fixes with no indication of why
- Current mitigation: `_parse_batch_response()` should log raw response on failure; confirm implementation
- Recommendations: Add structured logging of failed JSON with line/column information for debugging

**Subprocess Command Construction:**
- Risk: Git clone command uses `subprocess.run()` with `# noqa: S603` to suppress bandit warning
- Files: `src/apme_gateway/scan/driver.py:84`
- Impact: Command is constructed safely (no shell injection) but suppression bypasses security linting
- Current mitigation: Command is hardcoded as list (not shell=True); user input is validated beforehand
- Recommendations: Keep current approach; ensure git clone path validation is tested

**AI Model Token Leakage Risk:**
- Risk: Abbenay provider logs response text up to 500 chars; if token/credential embedded in response, could leak
- Files: `src/apme_engine/remediation/abbenay_provider.py:822-827`
- Impact: Model output or error messages containing bearer tokens could be logged
- Current mitigation: None; logging assumes response is safe
- Recommendations: Add redaction filter for bearer tokens in all log statements; use `[REDACTED]` per SECURITY.md

**Unvalidated File Paths in Session State:**
- Risk: Session file paths stored directly from uploaded content without normalization checks
- Files: `src/apme_engine/daemon/primary_server.py:1067-1068`
- Impact: Symlink attacks or path traversal in uploaded tar/zip could write outside project root
- Current mitigation: Scanner runs in isolated venv; filesystem is sandboxed
- Recommendations: Validate paths with `Path.resolve()` and ensure they resolve within project root

## Performance Bottlenecks

**Large Models.py File (5218 lines):**
- Problem: Monolithic data model file makes imports slow and code navigation difficult
- Files: `src/apme_engine/engine/models.py`
- Cause: All engine model classes (Rule, Violation, RuleScope, RemediationClass, etc.) defined in single file
- Risk: Circular imports become likely if split without care; import time increases with each new model
- Improvement path:
  - Extract enum/value types to separate modules (e.g., `models/enums.py`, `models/violations.py`)
  - Use `__init__.py` barrel import to maintain API compatibility
  - Profile import time with `python -X importtime`

**Model Loader Recursion Depth:**
- Problem: `load_repository()` and related functions use deep recursion for role/collection traversal
- Files: `src/apme_engine/engine/model_loader.py` (2498 lines, multiple recursive functions)
- Cause: ARI engine design uses recursion for tree building
- Risk: Deep project hierarchies (nested roles, includes, imports) may hit Python recursion limit
- Improvement path:
  - Add recursion depth limit check with clear error message
  - Consider iterative traversal with explicit stack for large codebases
  - Profile with large ansible-tower-setup-like projects

**Hierarchy Tree Building N² Lookups:**
- Problem: `_recursive_get_calls()` and similar tree-building methods perform repeated O(n) searches
- Files: `src/apme_engine/engine/tree.py:850-900` (playbook_mappings loop with nested lookups)
- Cause: No indexing of taskfile, role, or module definitions
- Risk: Projects with >100 playbooks or roles show noticeable slowdown
- Improvement path:
  - Pre-build hash maps of taskfile/role/module keys
  - Use dict lookups instead of list comprehensions with O(n) searches
  - Add caching layer for resolved paths

**Abbenay LLM API Timeout and Retry Strategy:**
- Problem: Fixed 120-second timeout for AI fix proposals; no exponential backoff or retry
- Files: `src/apme_engine/remediation/abbenay_provider.py:792`
- Cause: Single timeout value regardless of project size or model response time
- Risk: High-latency models or large violation batches fail silently; no mechanism to retry transient failures
- Improvement path:
  - Add timeout scaling based on violation count
  - Implement exponential backoff for transient errors (5XX, timeouts)
  - Add telemetry for timeout frequency and average latency

**Database Query N+1 Problem in Project/Scan APIs:**
- Problem: Queries for projects and scans use selectinload but may not cover all nested relationships
- Files: `src/apme_gateway/db/queries.py`, `src/apme_gateway/api/router.py`
- Cause: Relationship loading strategy may vary per endpoint
- Risk: Dashboards listing many projects with violation counts may execute separate queries per project
- Improvement path:
  - Audit each API endpoint for N+1 queries
  - Use SQLAlchemy relationship loading (selectinload, joinedload) consistently
  - Add query logging with profiling in dev mode

## Fragile Areas

**Session State Mutation During Async Operations:**
- Files: `src/apme_engine/daemon/primary_server.py:1065-1150`
- Why fragile: Session object (original_files, working_files, fix_options) is mutated directly by multiple async handlers (upload, scan, fix, proposal)
- Risk:
  - Race condition if two uploads happen concurrently
  - State corruption if exception occurs mid-mutation
  - No locking mechanism to prevent concurrent modification
- Safe modification:
  - Add asyncio.Lock to SessionState; acquire before any mutation
  - Consider immutable data structures or copy-on-write semantics
  - Add validation after each mutation to ensure consistency
- Test coverage: Check for concurrent upload/fix scenarios in `test_session.py`

**Primary Server FixSession State Machine:**
- Files: `src/apme_engine/daemon/primary_server.py:960-1046`
- Why fragile: Complex state transitions (open → upload → scan → fix → close) with many edge cases
- Risk:
  - Missing state validation (e.g., proposing a fix without scanning first)
  - Orphaned sessions if client disconnects during processing
  - Session expiry not enforced (see TODO at line 1029)
- Safe modification:
  - Formalize state machine with explicit transition rules
  - Add assertions to verify legal state transitions
  - Implement background cleanup task for expired sessions
- Test coverage: `test_session.py` covers normal flow; edge cases (network partitions, expiry) under-tested

**Validator Error Response Handling:**
- Files: `src/apme_gateway/api/router.py:86-91` (gRPC probe), `src/apme_gateway/api/router.py:109, 185` (health checks)
- Why fragile: All exceptions treated as "service down" without distinguishing between network, config, or logic errors
- Risk:
  - Validator startup delays appear as permanent failures
  - Misconfigured addresses silently return unhealthy
  - Client has no way to distinguish transient from permanent failures
- Safe modification:
  - Separate error types: network timeout → ServiceUnavailable, config error → ConfigError, logic error → InternalError
  - Return detailed error reason in health endpoint
  - Add exponential backoff and retry in gateway for startup phase

**OPA Client Subprocess Management:**
- Files: `src/apme_engine/opa_client.py:270-303`
- Why fragile: OPA binary invoked via subprocess with 10-second timeout; no cleanup on timeout
- Risk:
  - Orphaned OPA processes if timeout occurs
  - stderr not checked for warnings (may indicate query compilation issues)
  - Large payloads could exceed OPA memory limits
- Safe modification:
  - Use asyncio.create_subprocess_exec with explicit cleanup
  - Capture and log stderr separately (rego compilation errors)
  - Add input size validation before invoking OPA
- Test coverage: `test_opa_client.py` should test timeout and stderr scenarios

**Remediation Engine AI Proposal Parsing:**
- Files: `src/apme_engine/remediation/abbenay_provider.py:376-450` (_parse_batch_response)
- Why fragile: JSON parsing assumes specific response structure; malformed LLM output causes silent failure
- Risk:
  - LLM response format drift breaks proposal generation
  - Invalid patches are silently dropped (no audit trail)
  - Batch contains mixed success/failure (some patches valid, others not) with no per-patch error tracking
- Safe modification:
  - Validate response JSON schema upfront with jsonschema library
  - Log all malformed responses with context (model, violations, full text)
  - Return both valid and invalid patches separately so caller can decide handling
  - Add unit tests with realistic LLM response variations
- Test coverage: `test_remediation_engine.py` should include malformed JSON test cases

## Scaling Limits

**Repository Parsing Memory Usage:**
- Current capacity: Tested on ansible-tower-setup (~5000 files, 200+ roles)
- Limit: Deep nesting or massive task counts could exhaust memory during tree building
- Scaling path:
  - Profile memory with largest known repos (AWX, Molecule, etc.)
  - Consider lazy loading for roles/taskfiles not in scan path
  - Stream tree building instead of loading all into memory upfront

**Session Storage (In-Memory):**
- Current capacity: SessionStore holds all active sessions in RAM
- Limit: ~100 concurrent sessions with 10MB average files each = 1GB memory
- Scaling path:
  - Add optional Redis/persistent backend for session storage
  - Implement session eviction policy (LRU, TTL)
  - Add metrics for session count and memory usage

**OPA Concurrent Policy Evaluations:**
- Current capacity: Single OPA process via subprocess; queries are serialized
- Limit: High-concurrency scans block on OPA calls (10s timeout per query)
- Scaling path:
  - Run multiple OPA instances (docker network or local ports)
  - Pool OPA connections with health checks
  - Consider OPA's native gRPC API instead of subprocess

**Abbenay AI Token Budget:**
- Current capacity: No rate limiting on LLM calls
- Limit: High-violation projects could exhaust token budget or hit API rate limits
- Scaling path:
  - Add configurable concurrency limit (max_concurrent in remediation/engine.py:527)
  - Implement token counting and budget enforcement
  - Add queuing and backoff for over-limit requests

**Database I/O for Scan Results:**
- Current capacity: Scan with 10K violations writes one row per violation (10K inserts)
- Limit: Dashboard queries with aggregation may become slow as historical scans accumulate
- Scaling path:
  - Add database indexes on (project_id, created_at, rule_id)
  - Partition scan results by date
  - Pre-compute aggregates (violations per rule, trend) asynchronously

## Dependencies at Risk

**ARI Engine Maintenance Burden:**
- Risk: Engine code is fully integrated, not vendored. Must manually port upstream improvements to ARI.
- Impact: Bug fixes, new Ansible module specs, or performance improvements in ARI require manual merge
- Current status: ARI is internal tool; little/no public upstream, but parser bugs affect all validators
- Migration plan:
  - If ARI becomes public again, evaluate switching to pip dependency with custom parser hooks
  - Document all deviations from ARI upstream in `.sdlc/adrs/ADR-003-vendor-ari-engine.md`
  - Monitor ARI releases for critical bugfixes

**Protobuf Version Lock:**
- Risk: pyproject.toml pins `protobuf>=6.31.1,<7`; protobuf 7.x will require gRPC code regeneration
- Impact: Blocking security updates or performance improvements in future protobuf releases
- Current status: grpcio 1.78.0+ may require protobuf 7.x eventually
- Migration plan:
  - Test with protobuf 7.x in CI early
  - Regenerate gRPC stubs via `scripts/gen_grpc.sh`
  - Update proto files if API surface changes

**Abbenay Client Dependency:**
- Risk: Abbenay client installed from GitHub release URL with SHA256 pin; if URL becomes unavailable, builds fail
- Impact: AI escalation feature becomes unavailable if Abbenay releases stop
- Current status: Release URL: `https://github.com/redhat-developer/abbenay/releases/download/v2026.3.8-alpha/`
- Migration plan:
  - Mirror Abbenay wheel to internal artifact repository
  - Add fallback URL configuration
  - Implement graceful degradation if AI import fails (proposals become unavailable, not fatal)

**Vendored Ansible UI Framework:**
- Risk: `frontend/vendor/ansible-ui-framework/` is a full copy of external code with many TODO markers
- Impact: Security patches or UX improvements to upstream are not automatically received
- Current status: Contains many unresolved TODOs in PageTable, PageForm components
- Migration plan:
  - Evaluate switching to npm package if available
  - Document all customizations to make merging easier
  - Run security audits on vendored code regularly

## Missing Critical Features

**Session Expiry Enforcement:**
- Problem: Sessions have `ttl_seconds` and `expiring_soon` flag but no background cleanup
- Blocks: Cannot guarantee sessions are cleaned up; leaked resources and stale data in store
- Requirement: ADR-028 mentions TTL but implementation is incomplete
- Fix approach:
  - Add background asyncio task to purge expired sessions periodically
  - Emit ExpirationWarning when session.expiring_soon becomes True (TODO at primary_server.py:1029)
  - Add session cleanup on client disconnect (WebSocketDisconnect handler)

**Proposal Audit Trail:**
- Problem: AI proposals are applied without audit trail; no log of which fixes were accepted vs rejected
- Blocks: Cannot track proposal acceptance rate or debug why proposals fail
- Current state: Proposals are stored in scan results but acceptance/rejection not recorded
- Fix approach:
  - Add ProposalAudit table with (proposal_id, status, feedback, applied_at)
  - Track which patches were accepted in FixSession events
  - Display acceptance metrics in dashboard

**Concurrent Project Operations:**
- Problem: Multiple scans on same project not coordinated; could interfere or duplicate work
- Blocks: Cannot run incremental scans or parallel jobs on same project safely
- Current state: Gateway allows multiple simultaneous scans per project
- Fix approach:
  - Add project-level locking to queue scans sequentially
  - Implement queue with configurable concurrency per project
  - Track scan lineage and dependencies

## Test Coverage Gaps

**Untested: WebSocket Disconnection During Streaming:**
- What's not tested: Client websocket disconnect mid-scan; ensure session cleanup and no orphaned resources
- Files: `src/apme_gateway/api/router.py:1010-1012` (WebSocketDisconnect handler) is minimal
- Risk: Sessions or subprocess might remain running after client closes browser
- Priority: High (affects user experience and resource leaks)

**Untested: Concurrent Validator Probes:**
- What's not tested: Multiple health checks running simultaneously; race conditions in `_probe_grpc` or `_probe_http`
- Files: `src/apme_gateway/api/router.py:64-110` (probe functions)
- Risk: Channel reuse or async context issues could cause spurious failures
- Priority: Medium (health endpoints are called periodically)

**Untested: Large Playbook Parsing:**
- What's not tested: Model loader behavior with projects >500 roles, >5000 files, or deep nesting
- Files: `src/apme_engine/engine/model_loader.py`, `src/apme_engine/engine/tree.py`
- Risk: Stack overflow, memory exhaustion, or timeouts on real-world large repositories
- Priority: High (many enterprise projects exceed these sizes)

**Untested: OPA Timeout Scenarios:**
- What's not tested: OPA subprocess timeout; ensure no orphaned processes and graceful error handling
- Files: `src/apme_engine/opa_client.py:275` (subprocess.TimeoutExpired)
- Risk: Timeout exception not caught; process hangs or crashes validator
- Priority: High (OPA is critical validator)

**Untested: Session State Mutation Under Load:**
- What's not tested: Concurrent uploads, scans, fixes on same session; mutation race conditions
- Files: `src/apme_engine/daemon/primary_server.py:1065-1150` (session mutation)
- Risk: State corruption, lost updates, or crashes under concurrent load
- Priority: Critical (primary data path)

**Untested: AI Proposal Malformed JSON:**
- What's not tested: Abbenay returns unparseable JSON, truncated response, or encoding issues
- Files: `src/apme_engine/remediation/abbenay_provider.py:380-450` (JSON parsing)
- Risk: Proposal generation silently fails with no error indication
- Priority: High (AI fixes are core feature)

**Untested: Missing Validators in Health Check:**
- What's not tested: Behavior when a required validator is unreachable at startup or during scan
- Files: `src/apme_gateway/api/router.py:143-187` (health endpoint)
- Risk: Scans proceed with incomplete validator set; violations are missed
- Priority: Medium (should gracefully degrade or warn)

**Untested: Database Connection Loss During Long Scan:**
- What's not tested: Database connection drops mid-scan; ensure rollback and retry
- Files: `src/apme_gateway/db/queries.py` (all query functions)
- Risk: Partial scan results stored; data corruption
- Priority: High (data integrity issue)

---

*Concerns audit: 2026-03-25*
