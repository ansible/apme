# `@apme/ui-workflow`

Shared APME **check → review → remediate → publish** presentation for personal
RHDH Local and Portal hosts, using `@backstage/ui` (BUI).

## Host contract

The presentation targets the shared host baseline: BUI 0.17.1, React/DOM 18.3,
React types 18, and React Router DOM 6. Hosts load `@backstage/ui/css/styles.css`,
provide their BUI integration and router, and set BUI's `data-theme-mode` to
`light` or `dark` on the host theme root. Workflow CSS is imported by the package
entry. Dialogs follow the host theme, including when rendered in portals.

The legacy native SPA's React 19/Router 8 presentation is no longer a supported
consumer. This does not mark its replacement accepted or remove it; that work
remains in #833 and REQ-021/TASK-006. No second presentation implementation or
native compatibility adapter is maintained.

## Install

Product releases attach compiled ESM/declarations and CSS as immutable tarballs
(ADR-066). Pin the workflow and client from the same product release:

```json
"@apme/ui-workflow": "https://github.com/ansible/apme/releases/download/vX.Y.Z/apme-ui-workflow-X.Y.Z.tgz"
```

Inside the repository, use the root npm workspace and the frontend tox commands.

## Host publishing actions

`ProjectWorkflowPanel`, `OperationPanel` and the standard Backstage
`ApmeProjectWorkflow` accept an optional `hostShipActions` React node. It appears
beside the pull-request action after a successful push, and on completed results
that have a pull request. An action is not shown before successful publication.
It remains host-owned: the host controls its visibility and authorization.

```tsx
<ProjectWorkflowPanel
  workflow={workflow}
  enableAi={enableAi}
  feedbackEnabled={feedbackEnabled}
  hostShipActions={<OpenInDevSpaces url={devSpacesUrl} />}
/>
```

Portal delegates to this component; no markup queries, MutationObserver, or DOM
insertion is needed to place the host action. Cancellation and draft-save errors
are handled by the shared panel.

## Other entries

- `@apme/ui-workflow/headless`: CSS-free controllers, transport and model
  preference key. Runtime imports do not load BUI or the specialist renderers.
- `@apme/ui-workflow/graph`: the existing D3/Dagre SVG renderer and export tools.
  It loads graph dependencies only for this entry. Its specialist dark palette
  remains an explicit styling exception.

## BUI availability and exceptions

The map is verified against the pinned **0.17.1** package declarations, rather
than the latest documentation. Interaction APIs are migrated with the controls.

| Existing control | BUI presentation |
| --- | --- |
| Card/CardBody | Card/CardBody |
| Button, link button | Button, ButtonLink/Link |
| Alert and close action | Alert with host-owned close action |
| Label/LabelGroup | Tag/TagGroup or status Text |
| Flex, Split, item wrappers | Flex/Grid and layout CSS |
| TextInput/TextArea | TextField/TextAreaField |
| FormGroup | FieldLabel and field label/description props |
| Searchable Select/MenuToggle | Select/Combobox search and selection API |
| Modal header/body/footer | Dialog, DialogHeader, DialogBody, DialogFooter |
| Checkbox, toggle groups | Checkbox, ToggleButtonGroup/ToggleButton |
| ExpandableSection | Accordion or existing disclosure controls |
| Icons | Remix Icon (`@remixicon/react`), used by BUI |
| Progress, Spinner | Accessible workflow progress/loading exception |

`components/WorkflowProgress.tsx` is a narrow temporary exception: BUI 0.17.1
exports no public progress bar or spinner. It uses native accessible progress/status
semantics and BUI styling; it introduces no alternate component kit. Replace it
when the shared host baseline exposes those primitives.

The diff renderer's parsing, YAML tokenization and graph behavior are preserved.
Their custom styles use BUI theme bindings rather than PatternFly variables or
`pf-v6-theme-dark`. Custom YAML syntax colors remain specialist presentation.

Portal still renders PatternFly outside the shared workflow. Its remaining
bridge and dependencies are tracked by ansible/ansible-backstage-plugins#1076;
removing the workflow's dependency does not authorize deleting that bridge early.
