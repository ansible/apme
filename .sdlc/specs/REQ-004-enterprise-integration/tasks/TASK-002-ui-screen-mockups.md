# TASK-002: UI Screen Mockups

## Parent Requirement

REQ-004: Enterprise Integration

## Status

Complete

## Description

Create Figma mockups for the enterprise web dashboard UI. Design the key screens that will display scan results, aggregated metrics, and ROI data.

## Prerequisites

- [x] TASK-001: CLI Reporting Options Research (informs output data structure)

## Screens to Design

| Screen | Purpose | Key Elements |
|--------|---------|--------------|
| Dashboard Home | Landing page with overview | Summary cards, recent scans list, quick actions |
| Scan Results List | Browse all scans | Table with status, violation counts, timestamps, filters |
| Scan Detail | Deep dive into single scan | Violations tree, severity badges, file paths, line numbers |
| ROI Metrics | Business value visualization | Charts for errors resolved, hours saved, trend lines |

## Design Requirements

1. **Dashboard Home**
   - Summary cards: Total scans, Open violations, Resolved this week
   - Recent scans table (5-10 rows)
   - Quick action buttons: New Scan, View Reports

2. **Scan Results List**
   - Sortable/filterable table
   - Columns: Scan ID, Target, Status, Errors, Warnings, Hints, Date
   - Pagination or infinite scroll
   - Bulk actions (export, delete)

3. **Scan Detail**
   - Header: Scan metadata (target, date, duration)
   - Summary box: PASSED/FAILED with counts
   - Violations grouped by file (tree view)
   - Each violation: severity badge, rule ID, message, line number
   - Code snippet preview (optional)

4. **ROI Metrics**
   - "Total Errors Resolved" counter
   - "Hours Saved" calculation
   - Trend charts (weekly/monthly)
   - Comparison to baseline

## Files/Deliverables

| Deliverable | Location | Purpose |
|-------------|----------|---------|
| Figma file | (add link when created) | Source design file |
| Exported PNGs | `docs/mockups/dashboard-*.png` | Static references |
| Design notes | `docs/mockups/README.md` | Design decisions, component notes |

## Verification

Before marking complete:

- [ ] All 4 screens designed in Figma
- [ ] Figma link added to this task
- [ ] Screens exported as PNGs to `docs/mockups/`
- [ ] Design reviewed with stakeholders
- [ ] Feedback incorporated

## Acceptance Criteria Reference

From REQ-004 (Web Dashboard):
- [ ] GIVEN enterprise-wide scan data
- [ ] WHEN dashboard is accessed
- [ ] THEN aggregated metrics are displayed (errors resolved, hours saved)

## Design Constraints

- **Consistency**: Match AAP UI / PatternFly 6 design language
- **Accessibility**: WCAG 2.1 AA compliance (contrast, focus states)
- **Responsive**: Design for 1280px+ (desktop-first, enterprise tool)
- **Dark mode**: Required (PatternFly built-in support)
- **Charts**: Simple bar charts (PatternFly react-charts style)
- **Navigation**: Sidebar nav (match AAP platform layout)

## AAP UI Reference

**Tech Stack** (from `aap-ui` repo):
- React 18 + TypeScript
- PatternFly 6 (`@patternfly/react-core`, `@patternfly/react-table`, `@patternfly/react-charts`)
- `@ansible/ansible-ui-framework` (PageLayout, PageDashboard, PageTable)
- styled-components
- SWR for data fetching

**Key Framework Components**:
| Component | Use For |
|-----------|---------|
| `PageLayout` | Page wrapper with sidebar nav |
| `PageHeader` | Title + description + action buttons |
| `PageDashboard` | Responsive card grid |
| `PageDashboardCard` | Card containers |
| `PageDashboardCount` | Summary metric cards |
| `PageTable` | Data tables with sort/filter/pagination |

**Reference Files**:
- `platform/overview/PlatformOverview.tsx` — Dashboard layout pattern
- `platform/overview/cards/` — Card component examples
- `framework/PageDashboard/` — Dashboard grid components
- `framework/PageTable/` — Table components

**Figma Resources**:
- [PatternFly Design Kit](https://www.patternfly.org/get-started/design) — Use PF6 components
- Match AAP color palette (Red Hat brand colors)

---

## Design Plan

### Screen 1: Dashboard Home
```
┌─────────────────────────────────────────────────────────────┐
│ [Sidebar]  │  APME Dashboard                    [New Scan]  │
│            ├────────────────────────────────────────────────│
│ Dashboard  │  ┌────────────┐ ┌────────────┐ ┌────────────┐  │
│ Scans      │  │  47 Scans  │ │  12 Open   │ │  35 Fixed  │  │
│ ROI        │  │   Total    │ │   Issues   │ │  This Week │  │
│            │  └────────────┘ └────────────┘ └────────────┘  │
│            │                                                 │
│            │  Recent Scans                        [View All] │
│            │  ┌─────────────────────────────────────────┐   │
│            │  │ playbooks/ │ ✓ PASSED │ 0/2/5 │ 2m ago │   │
│            │  │ roles/web  │ ✗ FAILED │ 3/8/2 │ 1h ago │   │
│            │  └─────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Screen 2: Scan Results List
```
┌─────────────────────────────────────────────────────────────┐
│ [Sidebar]  │  All Scans                [Filter] [Export]    │
│            ├────────────────────────────────────────────────│
│            │  Target      │ Status │ E │ W │ H │ Date       │
│            │  ─────────────────────────────────────────────  │
│            │  playbooks/  │ PASSED │ 0 │ 2 │ 5 │ Mar 16     │
│            │  roles/web   │ FAILED │ 3 │ 8 │ 2 │ Mar 16     │
│            │  roles/db    │ FAILED │ 1 │ 4 │ 0 │ Mar 15     │
│            │  inventory/  │ PASSED │ 0 │ 0 │ 1 │ Mar 15     │
│            │                                                 │
│            │                         [< 1 2 3 ... 12 >]     │
└─────────────────────────────────────────────────────────────┘
```

### Screen 3: Scan Detail
```
┌─────────────────────────────────────────────────────────────┐
│ [Sidebar]  │  ← All Scans    roles/web                      │
│            ├────────────────────────────────────────────────│
│            │  ┌─────────────────────────────────────────┐   │
│            │  │ ✗ FAILED │ 3 errors │ 8 warnings │ 2 hints │
│            │  └─────────────────────────────────────────┘   │
│            │                                                 │
│            │  tasks/main.yml (3 issues)                      │
│            │  ├─ [ERROR] L026 :12 - Use FQCN for module     │
│            │  ├─ [WARN]  L003 :15 - Task should have name   │
│            │  └─ [WARN]  R101 :18 - Risky file permissions  │
│            │                                                 │
│            │  handlers/main.yml (2 issues)                   │
│            │  ├─ [WARN]  L003 :5  - Task should have name   │
│            │  └─ [HINT]  M001 :8  - Consider using FQCN     │
└─────────────────────────────────────────────────────────────┘
```

### Screen 4: ROI Metrics
```
┌─────────────────────────────────────────────────────────────┐
│ [Sidebar]  │  ROI Dashboard                                 │
│            ├────────────────────────────────────────────────│
│            │  ┌──────────────────┐  ┌──────────────────┐    │
│            │  │      1,247       │  │     156 hrs      │    │
│            │  │  Errors Resolved │  │      Saved       │    │
│            │  └──────────────────┘  └──────────────────┘    │
│            │                                                 │
│            │  Weekly Trend              Errors by Category   │
│            │  ┌────────────────┐       ┌────────────────┐   │
│            │  │ ▁▂▄▆█▆▄▃▅▇    │       │ ████ FQCN  45% │   │
│            │  │ M T W T F S S  │       │ ███  Deps  30% │   │
│            │  └────────────────┘       │ ██   Other 25% │   │
│            │                           └────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Color Palette (Match CLI ANSI + PatternFly)

| Element | Light Mode | Dark Mode |
|---------|------------|-----------|
| Error badge | `#c9190b` (PF danger) | `#c9190b` |
| Warning badge | `#f0ab00` (PF warning) | `#f0ab00` |
| Hint badge | `#06c` (PF info) | `#73bcf7` |
| Passed status | `#3e8635` (PF success) | `#5ba352` |
| Failed status | `#c9190b` (PF danger) | `#c9190b` |

## Figma Link

**Link**: https://www.figma.com/design/4mmk2Q6F4KZMnuX4E5zseX

## Completion Checklist

- [x] Figma file created
- [x] Dashboard Home screen
- [x] Scan Results List screen
- [x] Scan Detail screen
- [x] ROI Metrics screen
- [x] HTML mockups created (docs/mockups/*.html)
- [x] README written (docs/mockups/README.md)
- [x] Status updated to Complete
