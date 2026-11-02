/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { lazy, Suspense } from "react";
import { Text, Tab, TabList, TabPanel, Tabs } from "@backstage/ui";
import type { ApmeApiAdapter } from "@apme/client/transport";
import type { ProjectDetail } from "@apme/client";
import {
  allApmeProjectViews,
  apmeProjectViews,
  type ApmeProjectView,
} from "../capabilities";
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
  projectViews = allApmeProjectViews,
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
  projectViews?: readonly ApmeProjectView[];
}) {
  const tabs = projectViews.map((view) => apmeProjectViews[view]);
  const canEdit = canManageProject && project !== undefined;
  const visible = (view: ApmeProjectView) => projectViews.includes(view);
  const activeTab =
    tabs.some((item) => item.tab === tab) || (tab === "settings" && canEdit)
      ? tab
      : (tabs[0]?.tab ?? (canEdit ? "settings" : undefined));
  if (!activeTab)
    return <Text>No project views are enabled by this host.</Text>;
  return (
    <Tabs
      selectedKey={activeTab}
      onSelectionChange={(key) => {
        if (isApmeProjectTab(String(key)))
          onTabChange(String(key) as ApmeProjectTab);
      }}
    >
      <TabList aria-label="Project views">
        {tabs.map((item) => (
          <Tab key={item.id} id={item.tab}>
            {item.label}
          </Tab>
        ))}
        {canManageProject && project && <Tab id="settings">Settings</Tab>}
      </TabList>
      {visible("workflow") && (
        <TabPanel id="workflow">
          <Suspense fallback={<Text role="status">Loading project view…</Text>}>
            {activeTab === "workflow" && (
              <ApmeProjectWorkflow
                projectId={projectId}
                adapter={adapter}
                onViewDetails={onOpenActivity}
              />
            )}
          </Suspense>
        </TabPanel>
      )}
      {visible("findings") && (
        <TabPanel id="findings">
          <Suspense fallback={<Text role="status">Loading project view…</Text>}>
            {activeTab === "findings" && (
              <ApmeProjectFindings projectId={projectId} adapter={adapter} />
            )}
          </Suspense>
        </TabPanel>
      )}
      {visible("activity") && (
        <TabPanel id="history">
          <Suspense fallback={<Text role="status">Loading project view…</Text>}>
            {activeTab === "history" &&
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
      )}
      {visible("dependencies") && (
        <TabPanel id="dependencies">
          <Suspense fallback={<Text role="status">Loading project view…</Text>}>
            {activeTab === "dependencies" && (
              <ApmeProjectDependencies
                projectId={projectId}
                adapter={adapter}
              />
            )}
          </Suspense>
        </TabPanel>
      )}
      {visible("graph") && (
        <TabPanel id="graph">
          <Suspense fallback={<Text role="status">Loading graph view…</Text>}>
            {activeTab === "graph" && (
              <ApmeProjectGraph projectId={projectId} adapter={adapter} />
            )}
          </Suspense>
        </TabPanel>
      )}
      {canManageProject && project && (
        <TabPanel id="settings">
          <Suspense
            fallback={<Text role="status">Loading project settings…</Text>}
          >
            {activeTab === "settings" && (
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
