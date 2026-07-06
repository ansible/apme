# ADR-056: APME Owns SCM Commit and Push

## Status

Proposed

## Date

2026-07-06

## Context

When APME remediates Ansible content, it produces patched files. The question is:
who commits and pushes those changes to the source repository?

PR #359 introduced endpoints for the portal (Backstage) to export a remediation
bundle and record its own PRs — meaning the portal performed the git commit/push
itself. This pattern splits SCM responsibility across consumers, creating three
problems:

1. **Broken attribution.** If the portal commits on APME's behalf, `git blame`
   and `git log` show the portal as the author — not the system that actually
   produced the changes. Provenance is lost.

2. **No clean handoff boundary.** When multiple systems can commit, there is no
   reliable way to distinguish "APME did this" from "a human did this" or "the
   portal did this" in the repository history. Post-remediation workflows
   (review, refinement, extension) cannot trust the commit boundary.

3. **Duplicated logic and inconsistency.** Each consumer reimplements branch
   naming, commit message formatting, and PR body generation. Results diverge
   across entry points.

### Constraints

- **Engine stays stateless** (ADR-020, invariant 5). The engine produces patches;
  it does not perform SCM operations.
- **Engine never queries out** (invariant 11). All external API calls (including
  SCM provider APIs) are the Gateway's responsibility.
- **Gateway owns external integrations** (ADR-029, ADR-050). PR creation is
  already scoped to the Gateway.
- **Multiple consumers exist.** The UI, portal (Backstage), CI integrations, and
  direct API clients all trigger remediation. The commit path must be uniform.

### Decision Drivers

- **Attribution**: APME as commit author makes `git blame` / `git log --author`
  meaningful for tracking automated vs. human changes.
- **Provenance**: The commit is the immutable record of what APME changed, when,
  and why. It must be authored by the system that produced the change.
- **Handoff**: SCM is the natural boundary between machine work and human work.
  After APME commits, humans iterate via normal branch/PR workflows.
- **Consistency**: All consumers get the same commit format, branch naming, and
  PR body regardless of entry point.
- **Security (Principle of Least Privilege)**: APME delivers only what
  consumers need to act — violation summaries, diffs, and PR URLs. Raw file
  bodies are never served across the API boundary because consumers do not need
  them; APME handles the commit/push internally.

## Decision

**APME (via the Gateway's SCM provider abstraction) is the sole system that
commits and pushes remediation changes to source repositories. The Gateway REST
API never serves raw file bodies to external consumers.**

Three principles follow:

### 1. APME Is a Code Author

Like any contributor, APME commits its changes under its own identity. The commit
message, author field, and branch name are APME-controlled. Consumers cannot
override the author — APME is always the committer of its own changes.

### 2. The SCM Commit Is the Handoff Boundary

Once APME commits, that commit cleanly separates machine-authored changes from
subsequent human work. Consumers that want to build on APME's remediation
(review, refine, extend) do so via normal SCM workflows — branches, PRs, reviews.
The commit history is the provenance record.

### 3. No Raw File Bodies Cross the External API Boundary

The Gateway REST API never returns raw file bodies (patched or original) to
external consumers. There is no endpoint that serves complete file content as a
downloadable blob or bundle. API responses contain structured results:

- Violation lists with file paths and line numbers
- Unified diffs showing what changed (change representation, not raw content)
- Summaries and statistics
- PR URLs after commit/push completes

Raw file content stays within the APME system boundary (engine → Gateway over
internal gRPC). This enforces the "APME owns the push" boundary by construction —
consumers cannot reconstruct a committable file tree from diff excerpts alone
without independently cloning the repository, at which point they already have
access to the source through normal SCM channels.

### Consumer Workflow

```
Consumer triggers remediation (UI / portal / CI / API)
        │
        ▼
Gateway runs FixSession → receives patched files internally
        │
        ▼
Gateway persists patches (internal, never exposed as raw files)
        │
        ▼
Consumer receives: violation summary, diff preview, activity status
        │
        ▼
Consumer triggers "Create PR" via Gateway API
        │
        ▼
Gateway:
  1. Resolves SCM token (project → global fallback)
  2. Determines SCM provider from repo_url
  3. Creates branch, commits as APME author, pushes
  4. Opens PR against target branch
  5. Stores PR URL in activity record
  6. Returns PR URL to consumer
        │
        ▼
Consumer receives PR URL — all SCM work is done
```

## Alternatives Considered

### Alternative 1: Consumer-Side Commit/Push

**Description**: The Gateway exposes a "remediation bundle" endpoint that returns
full patched files. Each consumer (portal, CI runner, API client) takes the
bundle and performs its own git commit/push/PR creation.

**Pros**:
- Consumers have full flexibility over commit messages and branch names
- No SCM token configuration needed in APME
- Works with any SCM provider the consumer already integrates with

**Cons**:
- Breaks attribution — the consumer is the git author, not APME
- Destroys provenance — cannot reliably distinguish APME changes from
  consumer changes in git history
- Duplicates SCM logic across every consumer
- Inconsistent commit formats, branch naming, PR bodies across entry points
- Exposes full source code over the REST API (exfiltration surface)
- Each consumer must handle SCM errors, retries, and edge cases independently

**Why not chosen**: Attribution and provenance are non-negotiable. If you cannot
answer "did APME make this change?" from `git log`, the audit trail is broken.
Shipping full files across the API boundary is the enablement mechanism for this
anti-pattern and must be prevented.

### Alternative 2: Hybrid — APME Provides Patches, Consumer May Push

**Description**: The Gateway returns unified diffs or patched files. Consumers
can either ask APME to push (preferred) or apply the patches themselves.

**Pros**:
- Offers flexibility for consumers with existing SCM integrations
- Graceful migration path from consumer-side to APME-side

**Cons**:
- Destroys provenance when consumers choose to push themselves
- "Optional" invariants are not invariants — they erode over time
- Cannot enforce consistent attribution if both paths exist
- Still requires exposing file content for the self-push path

**Why not chosen**: A provenance boundary must be absolute. If even one consumer
can bypass it, the invariant is meaningless. "You can trust git history to show
APME authorship" only works if there is no alternative path.

### Alternative 3: Commit in the Engine

**Description**: The engine performs git commit/push directly after remediation.

**Pros**:
- Simplest path — no Gateway involvement for SCM

**Cons**:
- Violates invariant 5 (engine stateless) and invariant 11 (engine never
  queries out)
- Adds SCM API dependencies to the engine package
- Engine would need SCM tokens, crossing security boundaries
- CLI daemon mode has no SCM context (local files, user's own repo)

**Why not chosen**: The engine's job is to produce patches. External API calls —
including SCM — belong in the Gateway per established invariants.

## Consequences

### Positive

- **Clear provenance**: `git log --author=apme` shows exactly what APME changed
  across all projects. No ambiguity about machine vs. human authorship.
- **Clean handoff**: The commit is the boundary. Post-remediation human work
  builds on top via normal SCM workflows. No interleaving of responsibilities.
- **Consistent interface**: All consumers (portal, UI, CI, API) get the same
  experience — trigger remediation, receive a PR URL.
- **No raw file serving**: Raw file bodies never leave the APME system
  boundary via the REST API. Consumers see diffs and summaries only.
- **Single implementation**: SCM logic (branch creation, commit, push, PR) is
  implemented once in the Gateway, not duplicated across consumers.
- **Audit trail**: The Gateway DB records every PR created, linking activity →
  commit → PR URL. One place to query for compliance.

### Negative

- **APME must support target SCM providers**: GitHub (Phase 1), GitLab, Bitbucket
  (Phase 2) per ADR-050. This is implementation work.
- **SCM tokens required in APME**: Consumers must configure SCM credentials in
  APME rather than using their own. Mitigated by hierarchical auth model
  (ADR-050: project-level overrides global).
- **Consumers lose flexibility**: Cannot customize commit authorship or use their
  own SCM tooling. This is intentional — consistency over flexibility.
- **Diff-only responses may limit some consumer UIs**: Consumers that want to
  show "before/after" file views must reconstruct from diffs. Mitigated by
  providing rich unified diffs in the API response.

### Neutral

- **CLI mode is unaffected.** The CLI writes patched files to the local
  filesystem. The user commits via their own git workflow. The CLI is the user's
  local tool — attribution is clear (the user ran the tool and commits the
  result). This ADR applies to the server/Gateway path where multiple consumers
  share a remote deployment.
- **Internal gRPC is unaffected.** The engine continues to return full patched
  file content to the Gateway over internal gRPC (`SessionResult`). The
  invariant applies to the external REST API boundary only.
- **ADR-050 is refined, not superseded.** ADR-050's design (Gateway SCM provider
  abstraction, hierarchical auth, phased rollout) remains valid. This ADR
  strengthens it from "the Gateway can create PRs" to "only the Gateway creates
  PRs, and file content never crosses the external boundary."

## Implementation Notes

### API Response Design

Remediation results exposed via the REST API include:

- `violations`: List of findings with rule ID, message, file path, line number
- `diff`: Unified diff format showing what changed
- `summary`: Counts by severity, files modified, rules triggered
- `pr_url`: After PR creation, the URL to the opened pull request

Endpoints that previously returned or accepted raw file bodies (e.g.,
remediation bundle export) must be removed or redesigned to return diffs only.

### Commit Identity

APME commits use a consistent identity:

```
Author: APME <apme@redhat.com>
```

The commit message follows a standard format:

```
fix: APME remediation — N findings resolved

Findings resolved:
- L002: Use FQCN (3 files)
- M001: Remove deprecated syntax (1 file)

Activity: {activity_id}
APME version: {version}
```

### Architectural Invariant

After acceptance, this ADR introduces a new architectural invariant for
`AGENTS.md`:

> **No raw file bodies cross the Gateway REST API boundary** (ADR-056).
> The Gateway never serves complete file content as downloadable blobs or
> bundles. API responses contain structured results: violation lists, unified
> diffs, summaries, and PR URLs. Raw file content stays within the APME system
> boundary. There is no endpoint that enables consumers to reconstruct a
> committable file tree, enforcing APME's exclusive ownership of SCM
> commit/push.

## Related Decisions

- ADR-009: Separate Remediation Engine (validators read-only; remediation
  produces patches)
- ADR-020: Reporting Service (engine stays stateless)
- ADR-029: Web Gateway Architecture (Gateway owns external integrations)
- ADR-038: Public Data API (defines Gateway's external contract)
- ADR-050: Post-Remediation PR Creation (Gateway SCM provider abstraction,
  hierarchical auth model, phased provider rollout)
- ADR-053: GitHub Integration Strategy (CI-side SARIF, not SCM writes)

## References

- [PR #359](https://github.com/ansible/apme/pull/359) — portal integration that
  motivated this decision (SCM bundle export being removed)
- ADR-050 implementation notes for SCM provider protocol and auth model

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-07-06 | B. Thornton | Initial proposal |
