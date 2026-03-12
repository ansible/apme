---
name: prd-import
description: >-
  Import a Product Requirements Document and create SDLC artifacts. Use when
  you have a PRD to break down, "import this PRD", "create specs from
  requirements doc", or starting a new project from a PRD. Do NOT use for
  updating existing artifacts or creating individual requirements (use
  req-new instead).
argument-hint: "[path/to/prd.pdf or URL]"
user-invocable: true
disable-model-invocation: true
metadata:
  author: APME Team
  version: 1.0.0
---

# PRD Import

Import a Product Requirements Document and create SDLC artifacts.

## Arguments

If `$ARGUMENTS` is provided, treat it as the PRD file path or URL.
Otherwise, prompt the user for the PRD source.

## Usage

```
/prd-import                     # Interactive - will ask for PRD
/prd-import /path/to/prd.pdf    # Import from file
/prd-import --url <url>         # Import from URL
```

## Behavior

### 1. Obtain PRD

If no path provided:
```
Please provide the PRD:
1. Paste the file path (PDF, MD, or TXT)
2. Paste the content directly
3. Provide a URL

How would you like to provide the PRD?
```

For PDFs, use `pdftotext` to extract content. For URLs, fetch and parse.

### 2. Parse PRD Sections

Identify and extract these sections:

| Section | Destination |
|---------|-------------|
| Executive Summary | `/README.md` |
| Problem Statement | `/README.md` |
| Target User Personas | `.sdlc/context/personas.md` |
| Functional Requirements | `.sdlc/specs/REQ-*` |
| Technical Requirements | `.sdlc/context/technical-requirements.md` |
| Success Metrics / KPIs | `.sdlc/context/success-metrics.md` |
| Capability Roadmap / Phases | `.sdlc/phases/PHASE-*` |

Show extracted sections for confirmation:
```
Found these sections:
- Executive Summary: ✓
- Problem Statement: ✓
- Personas: 3 found
- Functional Requirements: 4 sections (4.1-4.4)
- Technical Requirements: ✓
- Success Metrics: 3 KPIs
- Phases: 4 phases

Proceed with import? (Y/n)
```

### 3. Update README.md

Create or update top-level `/README.md` with:
- Project name and description (from Executive Summary)
- Problem Statement section
- Quick start / usage section (preserve existing)
- Link to `.sdlc/` for full documentation

### 4. Create/Update Personas

Write to `.sdlc/context/personas.md`:
- One section per persona
- Include role description
- Include goals
- Generate example use cases based on persona needs

### 5. Create Phases

For each phase in Capability Roadmap:

1. Create directory: `.sdlc/phases/PHASE-NNN-slug/`
2. Create `phase.md` from template
3. Extract goals from phase description
4. Link to requirements (created in next step)

**Phase numbering:** PHASE-001, PHASE-002, etc.

### 6. Create Requirements

For each functional requirement section (4.1, 4.2, etc.):

1. Determine which phase it belongs to (by matching descriptions)
2. Create REQ using `/req-new` logic
3. Add `Phase: PHASE-NNN` to requirement metadata
4. Extract sub-bullets as acceptance criteria

**Mapping example:**
```
4.1 Core Scanning Engine → PHASE-001 → REQ-001-scanning-engine
4.2 Automated Remediation → PHASE-002 → REQ-002-automated-remediation
4.3 Security & Compliance → PHASE-003 → REQ-003-security-compliance
4.4 Enterprise Integration → PHASE-003 → REQ-004-enterprise-integration
```

### 7. Create Technical Requirements

Write to `.sdlc/context/technical-requirements.md`:
- Parse requirements table or list
- Group by category (Parsing, Auth, Output, etc.)
- Format as tables

### 8. Create Success Metrics

Write to `.sdlc/context/success-metrics.md`:
- Extract KPIs with targets
- Define measurement formula
- Add tracking checklist

### 9. Update Indexes

Update all README files:
- `.sdlc/phases/README.md` — Add phase entries
- `.sdlc/specs/README.md` — Add REQ entries
- `.sdlc/README.md` — Update if needed

### 10. Summary

```
PRD Import Complete!

Created:
- Updated /README.md with executive summary
- 3 personas in .sdlc/context/personas.md
- 4 phases in .sdlc/phases/
- 4 requirements in .sdlc/specs/
- Technical requirements in .sdlc/context/technical-requirements.md
- Success metrics in .sdlc/context/success-metrics.md

Phase Overview:
| Phase | Name | Requirements |
|-------|------|--------------|
| PHASE-001 | CLI Scanner | REQ-001 |
| PHASE-002 | Rewrite Engine | REQ-002 |
| PHASE-003 | Enterprise Dashboard | REQ-003, REQ-004 |
| PHASE-004 | AI Remediation | REQ-005 |

Next steps:
1. Review generated artifacts
2. Refine requirements with /req-new edits
3. Create tasks with /task-new REQ-NNN
4. Check status with /sdlc-status
```

## Phase-Requirement Mapping

When creating requirements, use heuristics to assign to phases:

| Keywords in Requirement | Likely Phase |
|------------------------|--------------|
| scan, detect, parse, analyze | Phase 1 (Foundation) |
| fix, remediate, rewrite, auto | Phase 2 (Remediation) |
| dashboard, report, metrics, enterprise | Phase 3 (Enterprise) |
| AI, ML, intelligent, complex | Phase 4 (AI) |

If unclear, ask user.

## Incremental Import

If SDLC artifacts already exist:
```
Found existing SDLC artifacts:
- 13 ADRs
- 11 open DRs
- 0 REQs

Import mode:
1. Merge (preserve existing, add new)
2. Replace (overwrite with PRD content)
3. Cancel

Choose mode:
```

For merge mode:
- Preserve existing ADRs, DRs
- Add new phases/REQs with next available numbers
- Append to context files rather than overwrite

## Edge Cases

| Situation | Handling |
|-----------|----------|
| PDF extraction fails | Ask for alternative format |
| No phases defined | Create single "Phase 1: MVP" |
| Requirement has no phase match | Ask user to assign |
| README.md exists | Merge executive summary, preserve other sections |
| Duplicate persona names | Merge or ask user |
