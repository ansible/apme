/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
/** Host-neutral project views. Scope, entitlements and write permissions belong to the host. */
export const apmeProjectViews = {
  workflow: {
    id: "apme.project.workflow",
    entryPoint: "@apme/backstage-plugin/workflow",
    tab: "workflow",
    label: "Workflow",
  },
  findings: {
    id: "apme.project.findings",
    entryPoint: "@apme/backstage-plugin/findings",
    tab: "findings",
    label: "Findings",
  },
  activity: {
    id: "apme.project.activity",
    entryPoint: "@apme/backstage-plugin/activity",
    tab: "history",
    label: "History",
  },
  dependencies: {
    id: "apme.project.dependencies",
    entryPoint: "@apme/backstage-plugin/dependencies",
    tab: "dependencies",
    label: "Dependencies",
  },
  graph: {
    id: "apme.project.graph",
    entryPoint: "@apme/backstage-plugin/graph",
    tab: "graph",
    label: "Graph",
  },
} as const;

export type ApmeProjectView = keyof typeof apmeProjectViews;
/** Compatibility default for existing hosts; hosts can explicitly advertise a subset. */
export const allApmeProjectViews: readonly ApmeProjectView[] = Object.keys(
  apmeProjectViews,
) as ApmeProjectView[];
