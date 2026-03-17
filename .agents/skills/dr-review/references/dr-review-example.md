# DR Review Example Session

This is a reference example for the `/dr-review` skill.

```
User: /dr-review

Claude: Found 10 open Decision Requests:

| Priority | DR | Question |
|----------|-----|----------|
| Blocking | DR-004 | AAP Pre-Flight Integration |
| Blocking | DR-008 | Scan Result Persistence |
| High | DR-001 | Version-Specific Analysis Behavior |
| High | DR-007 | Target Persona Priority |
| Medium | DR-002 | SBOM Format |
| Low | DR-006 | Success Metrics |

Which DR would you like to review? (Enter DR number or "next" for highest priority)

User: DR-001

Claude: ## DR-001: Version-Specific Analysis Behavior

**Question**: How should APME handle version-specific analysis when users don't specify a target version?

**Context**: Different Ansible versions have different module availability and deprecation states...

**Options**:
1. **Single Target Version Flag** (recommended) - User specifies one target version
2. **Matrix Scan** - Scan against multiple versions simultaneously
3. **Default to Latest** - Always analyze for latest AAP version

**Recommendation**: Option 1 - Single Target Version Flag

What is your decision?
1. Option A: Single Target Version Flag (recommended)
2. Option B: Matrix Scan
3. Option C: Default to Latest
4. Defer for later
5. Need more information

User: 1

Claude: Recording decision: Option A (Single Target Version Flag)

Please provide rationale (or press Enter to use recommendation text):

User: Simpler UX, users can run multiple times for different versions if needed

Claude: Action items for this decision (one per line, or press Enter to skip):

User: Update REQ-001 with version flag acceptance criteria

Claude: This decision affects architecture. Would you like me to create an ADR?

User: yes

Claude: Done! Summary:
- DR-001 decided: Single Target Version Flag
- Moved to closed/decided/DR-001-version-specific-analysis.md
- README.md index updated
- ADR-014-version-specific-analysis.md created (status: Proposed)

Action items:
- [ ] Update REQ-001 with version flag acceptance criteria

Review another DR? (Y/n or DR number)

User: n

Claude: All done. 9 open DRs remaining (2 blocking).
```

## Quick Mode Example

```
User: /dr-review DR-001 --quick

Claude: ## DR-001: Version-Specific Analysis Behavior

Using recommended option: **Single Target Version Flag**
Rationale: [from recommendation text]

Confirm? (Y to accept, N for full review)

User: Y

Claude: Done! DR-001 decided and moved to closed/decided/.
```
