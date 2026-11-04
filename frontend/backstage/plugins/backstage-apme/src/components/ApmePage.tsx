/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { ApmeButton as Button } from "./layout";
import { lazy, Suspense, useCallback, useState } from "react";
import { useApi } from "@backstage/core-plugin-api";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Box,
  Cell,
  Column,
  Flex,
  Row,
  SearchField,
  Select,
  TableBody,
  TableHeader,
  TableRoot,
  Text,
} from "@backstage/ui";
import { listProjects } from "@apme/client";
import { apmeHostApiRef } from "../api/apmeApi";
import {
  ApmeProjectTabs,
  isApmeProjectTab,
  type ApmeProjectTab,
} from "./ApmeProjectTabs";
import { CreateProjectDialog } from "./CreateProjectDialog";
import { ProjectDetails } from "./ProjectDetails";
import { PageControls } from "./DataStatus";
import { useLoad } from "./useLoad";
import { SectionHeader } from "./layout";
import type { ApmeSettingsProps } from "./ApmeSettings";
import {
  allApmeProjectViews,
  apmeProjectViews,
  type ApmeProjectView,
} from "../capabilities";

const ApmeRules = lazy(() =>
  import("./ApmeRules").then((module) => ({ default: module.ApmeRules })),
);
const ApmeHealth = lazy(() =>
  import("./ApmeHealth").then((module) => ({ default: module.ApmeHealth })),
);
const ApmeSettings = lazy(() =>
  import("./ApmeSettings").then((module) => ({ default: module.ApmeSettings })),
);

export function ApmePageContent({
  showGlobalSettings = true,
  section,
  showSectionNavigation = true,
}: {
  showGlobalSettings?: boolean;
  section?: ApmeSection;
  showSectionNavigation?: boolean;
}) {
  const hostApi = useApi(apmeHostApiRef);
  const load = useCallback(() => hostApi.getAdapter(), [hostApi]);
  const { value: adapter, error } = useLoad(load);
  if (error)
    return (
      <Alert
        status="danger"
        title="Could not connect to APME"
        description={error}
      />
    );
  if (!adapter) return <Text role="status">Connecting to APME…</Text>;
  return (
    <ApmeWorkbench
      adapter={adapter}
      projectViews={hostApi.projectViews}
      canManageRules={hostApi.canManageRules === true}
      canManageProjects={hostApi.canManageProjects === true}
      canDeleteProjects={hostApi.canDeleteProjects === true}
      showGlobalSettings={showGlobalSettings}
      section={section}
      showSectionNavigation={showSectionNavigation}
      canAcknowledgeFindings={hostApi.canAcknowledgeFindings === true}
      canViewSettings={hostApi.canViewSettings === true}
      canManageSettings={hostApi.canManageSettings === true}
      supportsGalaxyCertificateSettings={
        hostApi.supportsGalaxyCertificateSettings === true
      }
    />
  );
}

export interface ApmeWorkbenchProps extends ApmeSettingsProps {
  /** Host-evaluated permission; defaults to read-only for embedded hosts. */
  canManageRules?: boolean;
  canManageProjects?: boolean;
  canDeleteProjects?: boolean;
  canAcknowledgeFindings?: boolean;
  /** Hosts can place global settings on their own route. */
  showGlobalSettings?: boolean;
  /** Hosts with separate section routes can fix the displayed section. */
  section?: ApmeSection;
  showSectionNavigation?: boolean;
  projectViews?: readonly ApmeProjectView[];
}

export type ApmeSection = "repositories" | "rules" | "health";

/** Shared page; embedding hosts can supply their own authorized adapter. */
export function ApmeWorkbench({
  adapter,
  canManageRules = false,
  canManageProjects = false,
  canDeleteProjects = false,
  showGlobalSettings = true,
  section,
  showSectionNavigation = true,
  projectViews = allApmeProjectViews,
  canAcknowledgeFindings = false,
  canViewSettings = false,
  canManageSettings = false,
  supportsGalaxyCertificateSettings = false,
}: ApmeWorkbenchProps) {
  const [params, setParams] = useSearchParams();
  const requestedView = params.get("view");
  const view =
    section ??
    (requestedView === "rules" ||
    requestedView === "health" ||
    (requestedView === "settings" && showGlobalSettings)
      ? requestedView
      : "repositories");
  const selected = params.get("project");
  const requestedTab = params.get("tab");
  const canAccessProjectSettings = canManageProjects || canDeleteProjects;
  const tab =
    isApmeProjectTab(requestedTab) &&
    (requestedTab === "settings"
      ? canAccessProjectSettings
      : projectViews.some(
          (view) => apmeProjectViews[view].tab === requestedTab,
        ))
      ? requestedTab
      : projectViews[0]
        ? apmeProjectViews[projectViews[0]].tab
        : canAccessProjectSettings
          ? "settings"
          : "workflow";
  const activityId = params.get("scan") || undefined;
  const [adding, setAdding] = useState(false);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("created_at:desc");
  const load = useCallback(
    () =>
      listProjects(25, offset, sort.split(":")[0], sort.split(":")[1], adapter),
    [adapter, offset, sort],
  );
  const {
    value: projects,
    loading,
    error,
    reload,
  } = useLoad(load, view === "repositories" && !selected);

  function selectView(value: string) {
    setAdding(false);
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value === "repositories") next.delete("view");
      else next.set("view", value);
      return next;
    });
  }

  function select(projectId: string | null) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (projectId) next.set("project", projectId);
      else next.delete("project");
      next.delete("tab");
      next.delete("scan");
      return next;
    });
  }

  function selectTab(value: ApmeProjectTab) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("tab", value);
      if (value !== "history") next.delete("scan");
      return next;
    });
  }

  function openActivity(scanId?: string) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("tab", "history");
      if (scanId) next.set("scan", scanId);
      else next.delete("scan");
      return next;
    });
  }

  return (
    <Box className="apme-ui apme-page apme-stack">
      {view === "repositories" && !selected && (
        <SectionHeader
          title={section ? undefined : "Projects"}
          description={
            loading
              ? "Loading repositories…"
              : error
                ? "Repositories unavailable"
                : `${projects?.total ?? 0} ${projects?.total_is_exact === false ? "available repositories" : "repositories"}`
          }
          actions={
            <>
              <Button
                variant="secondary"
                onPress={reload}
              >
                Refresh
              </Button>
              {canManageProjects && (
                <Button onPress={() => setAdding(true)}>Add repository</Button>
              )}
            </>
          }
        />
      )}
      {showSectionNavigation && (
        <nav aria-label="APME sections">
          <Flex gap="2" style={{ flexWrap: "wrap" }}>
            <Button
              variant="secondary"
              aria-current={view === "repositories" ? "page" : undefined}
              onPress={() => selectView("repositories")}
            >
              Repositories
            </Button>
            <Button
              variant="secondary"
              aria-current={view === "rules" ? "page" : undefined}
              onPress={() => selectView("rules")}
            >
              Rules
            </Button>
            <Button
              variant="secondary"
              aria-current={view === "health" ? "page" : undefined}
              onPress={() => selectView("health")}
            >
              Health
            </Button>
            {showGlobalSettings && (
              <Button
                variant="secondary"
                aria-current={view === "settings" ? "page" : undefined}
                onPress={() => selectView("settings")}
              >
                Settings
              </Button>
            )}
          </Flex>
        </nav>
      )}
      <Suspense fallback={<Text role="status">Loading APME view…</Text>}>
        {view === "rules" ? (
          <ApmeRules adapter={adapter} canManage={canManageRules} />
        ) : view === "health" ? (
          <ApmeHealth adapter={adapter} />
        ) : view === "settings" ? (
          <ApmeSettings
            adapter={adapter}
            canViewSettings={canViewSettings}
            canManageSettings={canManageSettings}
            supportsGalaxyCertificateSettings={
              supportsGalaxyCertificateSettings
            }
          />
        ) : selected ? (
          <Flex direction="column" gap="4">
            <Button
              variant="tertiary"
              onPress={() => select(null)}
              style={{ alignSelf: "flex-start" }}
            >
              All projects
            </Button>
            <ProjectDetails
              key={selected}
              projectId={selected}
              adapter={adapter}
            >
              {(project, onProjectUpdated) => (
                <ApmeProjectTabs
                  key={selected}
                  projectId={project.id}
                  project={project}
                  canManageProject={canManageProjects}
                  canDeleteProject={canDeleteProjects}
                  onProjectDeleted={() => {
                    select(null);
                    reload();
                  }}
                  onProjectUpdated={() => {
                    // A name alias stops resolving after a rename. Keep the
                    // selected tab, but refresh via the canonical project ID.
                    if (selected !== project.id) {
                      setParams(
                        (previous) => {
                          const next = new URLSearchParams(previous);
                          next.set("project", project.id);
                          return next;
                        },
                        { replace: true },
                      );
                    } else {
                      onProjectUpdated();
                    }
                  }}
                  adapter={adapter}
                  projectViews={projectViews}
                  canAcknowledgeFindings={canAcknowledgeFindings}
                  tab={tab}
                  activityId={activityId}
                  onTabChange={selectTab}
                  onOpenActivity={openActivity}
                  onCloseActivity={() => openActivity()}
                />
              )}
            </ProjectDetails>
          </Flex>
        ) : (
          <Flex direction="column" gap="4">
            <div className="apme-project-list-filters">
              <SearchField
                label="Filter this page"
                placeholder="Project name, URL or branch"
                value={search}
                onChange={setSearch}
              />
              <Select
                selectionMode="single"
                label="Sort projects"
                value={sort}
                options={[
                  { id: "created_at:desc", label: "Newest first" },
                  { id: "created_at:asc", label: "Oldest first" },
                  { id: "name:asc", label: "Name (A–Z)" },
                  { id: "name:desc", label: "Name (Z–A)" },
                  { id: "health_score:asc", label: "Lowest health first" },
                  { id: "health_score:desc", label: "Highest health first" },
                ]}
                onChange={(value) => {
                  if (typeof value === "string") {
                    setSort(value);
                    setOffset(0);
                  }
                }}
              />
            </div>
            {error && (
              <Alert
                status="danger"
                title="Could not load repositories"
                description={error}
              />
            )}
            {!error && (
              <div className="apme-table-scroll">
                <TableRoot aria-label="APME repositories" isPending={loading}>
                  <TableHeader>
                    <Column isRowHeader>Repository</Column>
                    <Column>Branch</Column>
                    <Column>Findings</Column>
                    <Column>Health</Column>
                    <Column>Checks</Column>
                    <Column>Last checked</Column>
                    <Column>Operation</Column>
                  </TableHeader>
                  <TableBody
                    items={(projects?.items ?? []).filter((project) =>
                      `${project.name} ${project.repo_url} ${project.branch}`
                        .toLowerCase()
                        .includes(search.trim().toLowerCase()),
                    )}
                    renderEmptyState={() =>
                      search.trim()
                        ? "No matching repositories on this page."
                        : "Add a repository to start a check."
                    }
                  >
                    {(project) => (
                      <Row id={project.id}>
                        <Cell>
                          <Button
                            variant="tertiary"
                            className="apme-table-link"
                            onPress={() => select(project.id)}
                          >
                            {project.name}
                          </Button>
                        </Cell>
                        <Cell>{project.branch}</Cell>
                        <Cell>{project.total_violations}</Cell>
                        <Cell>
                          {typeof project.health_score === "number"
                            ? `${Math.round(project.health_score)}%`
                            : "Not recorded"}
                        </Cell>
                        <Cell>{project.scan_count}</Cell>
                        <Cell>
                          {project.last_scanned_at
                            ? new Date(
                                project.last_scanned_at,
                              ).toLocaleDateString()
                            : "Never"}
                        </Cell>
                        <Cell>
                          {project.active_operation?.status ?? "Idle"}
                        </Cell>
                      </Row>
                    )}
                  </TableBody>
                </TableRoot>
              </div>
            )}
            <PageControls
              offset={offset}
              size={25}
              busy={loading}
              hasNext={!!projects && offset + 25 < projects.total}
              onChange={setOffset}
              subject="repositories"
            />
          </Flex>
        )}
      </Suspense>
      {adding && canManageProjects && view === "repositories" && !selected && (
        <CreateProjectDialog
          adapter={adapter}
          onClose={() => setAdding(false)}
          onCreated={(project) => {
            setAdding(false);
            reload();
            select(project.id);
          }}
        />
      )}
    </Box>
  );
}
