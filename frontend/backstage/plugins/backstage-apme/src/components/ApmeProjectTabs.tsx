/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { lazy, Suspense } from "react";
import { Text, Tab, TabList, TabPanel, Tabs } from "@backstage/ui";
import type { ApmeApiAdapter } from "@apme/client/transport";
import type { ProjectDetail } from "@apme/client";
const ApmeProjectWorkflow = lazy(() =>
  import("./ApmeProjectWorkflow").then((module) => ({
    default: module.ApmeProjectWorkflow,
  })),
);
const ApmeProjectFindings = lazy(() =>
  import("./ApmeProjectFindings").then((module) => ({
    default: module.ApmeProjectFindings,
  })),
);
const ApmeProjectHistory = lazy(() =>
  import("./ApmeProjectHistory").then((module) => ({
    default: module.ApmeProjectHistory,
  })),
);
const ApmeActivityDetails = lazy(() =>
  import("./ApmeActivityDetails").then((module) => ({
    default: module.ApmeActivityDetails,
  })),
);
const ApmeProjectDependencies = lazy(() =>
  import("./ApmeProjectDependencies").then((module) => ({
    default: module.ApmeProjectDependencies,
  })),
);
const ApmeProjectGraph = lazy(() =>
  import("./ApmeProjectGraph").then((module) => ({
    default: module.ApmeProjectGraph,
  })),
);
const ApmeProjectSettings = lazy(() =>
  import("./ApmeProjectSettings").then((module) => ({
    default: module.ApmeProjectSettings,
  })),
);

export type ApmeProjectTab =
  "workflow" | "findings" | "history" | "dependencies" | "graph" | "settings";
export function isApmeProjectTab(
  value: string | null,
): value is ApmeProjectTab {
  return (
    value === "workflow" ||
    value === "findings" ||
    value === "history" ||
    value === "dependencies" ||
    value === "graph" ||
    value === "settings"
  );
}

/** Hosts own tab/activity navigation; individual panels can also be embedded. */
export function ApmeProjectTabs({
  projectId,
  adapter,
  tab,
  activityId,
  onTabChange,
  onOpenActivity,
  onCloseActivity,
  project,
  canManageProject = false,
  onProjectUpdated,
}: {
  projectId: string;
  adapter: ApmeApiAdapter;
  tab: ApmeProjectTab;
  activityId?: string;
  onTabChange: (tab: ApmeProjectTab) => void;
  onOpenActivity: (scanId: string) => void;
  onCloseActivity: () => void;
  project?: ProjectDetail;
  canManageProject?: boolean;
  onProjectUpdated?: () => void;
}) {
  return (
    <Tabs
      selectedKey={tab}
      onSelectionChange={(key) => {
        if (isApmeProjectTab(String(key)))
          onTabChange(String(key) as ApmeProjectTab);
      }}
    >
      <TabList aria-label="Project views">
        <Tab id="workflow">Workflow</Tab>
        <Tab id="findings">Findings</Tab>
        <Tab id="history">History</Tab>
        <Tab id="dependencies">Dependencies</Tab>
        <Tab id="graph">Graph</Tab>
        {canManageProject && project && <Tab id="settings">Settings</Tab>}
      </TabList>
      <TabPanel id="workflow">
        <Suspense fallback={<Text role="status">Loading project view…</Text>}>
          {tab === "workflow" && (
            <ApmeProjectWorkflow
              projectId={projectId}
              adapter={adapter}
              onViewDetails={onOpenActivity}
            />
          )}
        </Suspense>
      </TabPanel>
      <TabPanel id="findings">
        <Suspense fallback={<Text role="status">Loading project view…</Text>}>
          {tab === "findings" && (
            <ApmeProjectFindings projectId={projectId} adapter={adapter} />
          )}
        </Suspense>
      </TabPanel>
      <TabPanel id="history">
        <Suspense fallback={<Text role="status">Loading project view…</Text>}>
          {tab === "history" &&
            (activityId ? (
              <ApmeActivityDetails
                projectId={projectId}
                activityId={activityId}
                adapter={adapter}
                onClose={onCloseActivity}
              />
            ) : (
              <ApmeProjectHistory
                projectId={projectId}
                adapter={adapter}
                onOpenActivity={onOpenActivity}
              />
            ))}
        </Suspense>
      </TabPanel>
      <TabPanel id="dependencies">
        <Suspense fallback={<Text role="status">Loading project view…</Text>}>
          {tab === "dependencies" && (
            <ApmeProjectDependencies projectId={projectId} adapter={adapter} />
          )}
        </Suspense>
      </TabPanel>
      <TabPanel id="graph">
        <Suspense fallback={<Text role="status">Loading graph view…</Text>}>
          {tab === "graph" && (
            <ApmeProjectGraph projectId={projectId} adapter={adapter} />
          )}
        </Suspense>
      </TabPanel>
      {canManageProject && project && (
        <TabPanel id="settings">
          <Suspense
            fallback={<Text role="status">Loading project settings…</Text>}
          >
            {tab === "settings" && (
              <ApmeProjectSettings
                project={project}
                adapter={adapter}
                canManage={canManageProject}
                onSaved={onProjectUpdated}
              />
            )}
          </Suspense>
        </TabPanel>
      )}
    </Tabs>
  );
}
