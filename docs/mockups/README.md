# APME Dashboard UI Mockups

## Figma File

**Live Figma Design**: https://www.figma.com/design/4mmk2Q6F4KZMnuX4E5zseX

## Screens

| Screen | HTML Mockup | Figma Node |
|--------|-------------|------------|
| Dashboard Home | [dashboard-home.html](dashboard-home.html) | [Node 1:2](https://www.figma.com/design/4mmk2Q6F4KZMnuX4E5zseX?node-id=1-2) |
| Scan Results List | [scan-results.html](scan-results.html) | [Node 3:2](https://www.figma.com/design/4mmk2Q6F4KZMnuX4E5zseX?node-id=3-2) |
| Scan Detail | [scan-detail.html](scan-detail.html) | [Node 4:2](https://www.figma.com/design/4mmk2Q6F4KZMnuX4E5zseX?node-id=4-2) |
| ROI Metrics | [roi-metrics.html](roi-metrics.html) | [Node 2:2](https://www.figma.com/design/4mmk2Q6F4KZMnuX4E5zseX?node-id=2-2) |

## Design System

### Tech Stack
- **Framework**: PatternFly 6 (dark mode)
- **Target**: React 18 + TypeScript (matching AAP UI)
- **Layout**: `@ansible/ansible-ui-framework` components

### Color Palette

| Element | Hex | Usage |
|---------|-----|-------|
| Error/Failed | `#c9190b` | Error badges, failed status |
| Warning | `#f0ab00` | Warning badges, open issues |
| Hint/Info | `#73bcf7` | Hint badges, links |
| Success/Passed | `#5ba352` | Success badges, passed status, resolved metrics |
| Background | `#1b1d21` | Main content area |
| Sidebar | `#151515` | Navigation sidebar |
| Card | `#212427` | Cards and table backgrounds |
| Border | `#3c3f42` | Dividers and borders |

### Components Used

| Component | PatternFly/AAP |
|-----------|----------------|
| Page layout | `PageLayout` |
| Metric cards | `PageDashboardCount` |
| Data tables | `PageTable` |
| Status badges | Custom (PF Label variant) |
| Sidebar nav | `PageNavigation` |

## Local Development

To view HTML mockups locally:

```bash
cd docs/mockups
python3 -m http.server 8765
# Open http://localhost:8765/dashboard-home.html
```

## Design Decisions

1. **Dark Mode First**: Enterprise dashboards are often viewed for extended periods; dark mode reduces eye strain.

2. **Monospace Paths**: File paths and rule IDs use monospace font for readability and copy-paste accuracy.

3. **Color-Coded Severity**: Consistent red/yellow/blue mapping for error/warning/hint across all screens.

4. **Sidebar Navigation**: Matches AAP platform layout for user familiarity.

5. **Progressive Disclosure**: Dashboard shows summary → Scans list shows all → Detail shows violations per file.
EOF < /dev/null