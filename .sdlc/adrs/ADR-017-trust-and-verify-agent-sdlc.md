# ADR-017: Trust-and-verify Model for Agent SDLC Invocation

## Status

Accepted

## Date

2026-03

## Context

The SDLC skills (adr-new, dr-new, req-new, task-new, phase-new, dr-review, prd-import) were originally configured with `disable-model-invocation: true`, meaning only explicit user commands (`/adr-new`, `/dr-new`, etc.) could trigger them. The rationale was a safety guard: prevent the agent from autonomously creating or modifying SDLC artifacts.

In practice this placed the entire burden of SDLC compliance on the user. Architectural decisions made during development sessions went unrecorded unless the user remembered to invoke `/adr-new`. Decision requests, requirement updates, and task tracking all required manual intervention. The SDLC process became overhead rather than an integrated part of development.

Additionally, the skills were located in `.claude/skills/`, tying them to a specific tool. Moving them to `.agents/skills/` makes them tool-agnostic and discoverable by any agent runtime.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Keep `disable-model-invocation: true` (user-only) | No risk of unwanted artifacts | SDLC burden on user, decisions go unrecorded, process friction |
| Remove safeguard entirely (agent auto-invokes) | Zero user friction, decisions captured in real-time | Agent may create artifacts the user didn't intend |
| Trust-and-verify (agent invokes + informs user) | Low friction, user retains oversight via PR review | Slightly more PR diff to review |

## Decision

**Adopt a trust-and-verify model.** Remove `disable-model-invocation: true` from all SDLC skills, allowing the agent to invoke them proactively when context warrants it. The agent must inform the user whenever it creates an SDLC artifact. All artifacts are reviewed as part of the normal PR diff before merge.

The human remains in the loop — that is foundational and non-negotiable. What changes is the *initiation model*: the human no longer needs to remember to invoke `/adr-new` or `/dr-new` at the right moment. The agent recognizes when an SDLC artifact is warranted and creates it. The human reviews, modifies, or rejects the artifact through the existing PR review process. The shift is from human-initiated to agent-initiated, human-verified.

## Rationale

- The human-in-the-loop is preserved through PR review — every artifact the agent creates appears in the diff and must be approved before merge
- The agent is already trusted to modify source code, create commits, push branches, and open PRs — creating a markdown ADR is lower risk than any of those operations
- The change removes the *initiation* burden, not the *oversight* — the user still reviews and approves every artifact
- SDLC artifacts are version-controlled, so unwanted artifacts are trivially reverted
- The user is informed in real-time when an artifact is created, and can reject or modify it before the PR is even opened
- Removing the initiation requirement shifts SDLC from "process the user must remember" to "process the agent handles, user verifies"
- Consolidating skills under `.agents/skills/` makes them tool-agnostic — not tied to Claude Code, Cursor, or any specific agent runtime

## Consequences

### Positive
- Human oversight is preserved — every artifact is reviewed in the PR diff
- Architectural decisions are captured as they happen, not after the fact
- SDLC compliance becomes automatic rather than manual
- Reduced cognitive load on the user — no need to remember which skill to invoke and when
- Skills are tool-agnostic under `.agents/skills/`

### Negative
- PR diffs may include SDLC artifacts the user needs to review (mitigated by the agent informing the user and the low cost of reviewing or deleting a markdown file)
- Agent may occasionally create artifacts that aren't needed (mitigated by PR review — unwanted artifacts are trivially reverted)

## Implementation Notes

- Removed `disable-model-invocation: true` from: adr-new, dr-new, dr-review, req-new, task-new, phase-new, prd-import
- Moved all skills from `.claude/skills/` to `.agents/skills/`
- Updated `submit-pr` skill to reference `.sdlc/adrs/` and SDLC skills
- `workflow` and `sdlc-status` were already agent-invocable (read-only)

## Related Decisions

- ADR-014: Ruff linter and prek pre-commit hooks (established agent-managed quality gates)
- ADR-015: GitHub Actions CI with prek (trust CI to enforce, verify in PR)
