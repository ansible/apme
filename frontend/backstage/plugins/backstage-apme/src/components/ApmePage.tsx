/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { lazy, Suspense, useCallback, useState } from "react";
import { useApi } from "@backstage/core-plugin-api";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Cell,
  Column,
  Flex,
  Row,
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
import { useLoad } from "./useLoad";
import type { ApmeSettingsProps } from "./ApmeSettings";

const ApmeRules = lazy(() =>
  import("./ApmeRules").then((module) => ({ default: module.ApmeRules })),
);
const ApmeHealth = lazy(() =>
  import("./ApmeHealth").then((module) => ({ default: module.ApmeHealth })),
);
const ApmeSettings = lazy(() =>
  import("./ApmeSettings").then((module) => ({ default: module.ApmeSettings })),
);
const ApmeUploadWorkbench = lazy(() =>
  import("./ApmeUploadWorkbench").then((module) => ({
    default: module.ApmeUploadWorkbench,
  })),
);

export function ApmePageContent({
  showGlobalSettings = true,
}: {
  showGlobalSettings?: boolean;
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
      canManageRules={hostApi.canManageRules === true}
      canManageProjects={hostApi.canManageProjects === true}
      showGlobalSettings={showGlobalSettings}
      canUpload={hostApi.canUpload === true}
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
  canUpload?: boolean;
  /** Local contributes these to the existing host Settings page instead. */
  showGlobalSettings?: boolean;
}

/** Shared page; embedding hosts can supply their own authorized adapter. */
export function ApmeWorkbench({
  adapter,
  canManageRules = false,
  canManageProjects = false,
  showGlobalSettings = true,
  canUpload = false,
  canViewSettings = false,
  canManageSettings = false,
  supportsGalaxyCertificateSettings = false,
}: ApmeWorkbenchProps) {
  const [params, setParams] = useSearchParams();
  const requestedView = params.get("view");
  const view =
    requestedView === "rules" ||
    requestedView === "health" ||
    (requestedView === "settings" && showGlobalSettings) ||
    requestedView === "upload"
      ? requestedView
      : "repositories";
  const selected = params.get("project");
  const requestedTab = params.get("tab");
  const tab =
    isApmeProjectTab(requestedTab) &&
    (requestedTab !== "settings" || canManageProjects)
      ? requestedTab
      : "workflow";
  const activityId = params.get("scan") || undefined;
  const [adding, setAdding] = useState(false);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const load = useCallback(
    () => listProjects(25, offset, "created_at", "desc", adapter),
    [adapter, offset, revision],
  );
  const {
    value: projects,
    loading,
    error,
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
    <Box p="6">
      <Flex justify="between" align="center" mb="6">
        <Text as="h1" variant="title-large">
          Ansible Policy &amp; Modernization
        </Text>
        {view === "repositories" && (
          <Button onPress={() => setAdding(true)}>Add repository</Button>
        )}
      </Flex>
      <nav aria-label="APME sections">
        <Flex gap="2" mb="6">
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
          {canUpload && (
            <Button
              variant="secondary"
              aria-current={view === "upload" ? "page" : undefined}
              onPress={() => selectView("upload")}
            >
              Paste / upload
            </Button>
          )}
        </Flex>
      </nav>
      <Suspense fallback={<Text role="status">Loading APME view…</Text>}>
        {view === "upload" ? (
          <ApmeUploadWorkbench adapter={adapter} canUpload={canUpload} />
        ) : view === "rules" ? (
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
          <Flex direction="column" gap="6">
            <Button variant="secondary" onPress={() => select(null)}>
              All repositories
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
            {error && (
              <Alert
                status="danger"
                title="Could not load repositories"
                description={error}
              />
            )}
            <Flex justify="between">
              <Text>
                {loading
                  ? "Loading repositories…"
                  : error
                    ? "Repositories unavailable"
                    : `${projects?.total ?? 0} ${projects?.total_is_exact === false ? "available repositories" : "repositories"}`}
              </Text>
              <Button
                variant="secondary"
                onPress={() => setRevision((n) => n + 1)}
              >
                Refresh
              </Button>
            </Flex>
            {!error && (
              <TableRoot aria-label="APME repositories" isPending={loading}>
                <TableHeader>
                  <Column isRowHeader>Repository</Column>
                  <Column>Branch</Column>
                  <Column>Findings</Column>
                  <Column>Operation</Column>
                </TableHeader>
                <TableBody
                  items={projects?.items ?? []}
                  renderEmptyState={() => "Add a repository to start a check."}
                >
                  {(project) => (
                    <Row id={project.id}>
                      <Cell>
                        <Button
                          variant="tertiary"
                          onPress={() => select(project.id)}
                        >
                          {project.name}
                        </Button>
                      </Cell>
                      <Cell>{project.branch}</Cell>
                      <Cell>{project.total_violations}</Cell>
                      <Cell>{project.active_operation?.status ?? "Idle"}</Cell>
                    </Row>
                  )}
                </TableBody>
              </TableRoot>
            )}
            <Flex gap="2" justify="end">
              <Button
                variant="secondary"
                isDisabled={offset === 0 || loading}
                onPress={() => setOffset((n) => Math.max(0, n - 25))}
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                isDisabled={
                  loading || !projects || offset + 25 >= projects.total
                }
                onPress={() => setOffset((n) => n + 25)}
              >
                Next
              </Button>
            </Flex>
          </Flex>
        )}
      </Suspense>
      {adding && view === "repositories" && (
        <CreateProjectDialog
          adapter={adapter}
          onClose={() => setAdding(false)}
          onCreated={(project) => {
            setAdding(false);
            setRevision((n) => n + 1);
            select(project.id);
          }}
        />
      )}
    </Box>
  );
}
