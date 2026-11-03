/*
 * Copyright Red Hat
 *
 * Shared Violations catalog column cell + project map cache.
 */

import { useState, useEffect, useCallback } from "react";
import { Box, CircularProgress, Typography, useTheme } from "@material-ui/core";
import { useApi } from "@backstage/core-plugin-api";
import { Entity } from "@backstage/catalog-model";
import {
  normalizeRepoUrlFromEntity,
  defaultBranchFromEntity,
  projectLookupKey,
} from "@ansible/backstage-rhaap-common/catalogEntity";
import type { Project } from "../../common/types";
import {
  inlineTextColorForSeverity,
  projectNeedsSeverityEnrichment,
  projectWorstSeverity,
} from "../../common/severity";
import { projectHasActiveOperation } from "../../common/operationStatus";
import { apmeApiRef } from "../api";
import { useApmeColorTokens } from "../hooks/useApmeColorTokens";

let projectsFetchPromise: Promise<Map<string, Project>> | null = null;

type ProjectMapListener = (map: Map<string, Project>) => void;
const projectMapListeners = new Set<ProjectMapListener>();
let sharedPollInterval: ReturnType<typeof setInterval> | null = null;
let activeFetchFn: (() => Promise<Map<string, Project>>) | null = null;

function notifyProjectMapListeners(map: Map<string, Project>): void {
  for (const listener of projectMapListeners) {
    listener(map);
  }
}

function stopSharedProjectMapPoller(): void {
  if (sharedPollInterval !== null) {
    clearInterval(sharedPollInterval);
    sharedPollInterval = null;
  }
}

function ensureSharedProjectMapPoller(
  map: Map<string, Project>,
  fetchProjectMap: () => Promise<Map<string, Project>>,
): void {
  activeFetchFn = fetchProjectMap;
  const hasActiveScan = [...map.values()].some((project) =>
    projectHasActiveOperation(project),
  );
  if (!hasActiveScan) {
    stopSharedProjectMapPoller();
    return;
  }
  if (sharedPollInterval !== null) {
    return;
  }
  sharedPollInterval = setInterval(() => {
    if (!activeFetchFn) {
      return;
    }
    projectsFetchPromise = activeFetchFn().catch(
      () => new Map<string, Project>(),
    );
    void projectsFetchPromise.then((updatedMap) => {
      notifyProjectMapListeners(updatedMap);
      if (
        ![...updatedMap.values()].some((project) =>
          projectHasActiveOperation(project),
        )
      ) {
        stopSharedProjectMapPoller();
      }
    });
  }, 5000);
}

export function resetApmeProjectsFetchPromise(): void {
  projectsFetchPromise = null;
  stopSharedProjectMapPoller();
}

function buildProjectMap(projects: Project[]): Map<string, Project> {
  const map = new Map<string, Project>();
  for (const project of projects) {
    map.set(projectLookupKey(project.repo_url, project.branch), project);
  }
  return map;
}

function violationCountStyle(
  project: Project,
  mode: "light" | "dark",
  mutedColor: string,
): { color: string; fontWeight: number } {
  const worst = projectWorstSeverity(project);
  if (!worst) {
    return { color: mutedColor, fontWeight: 600 };
  }
  return {
    color: inlineTextColorForSeverity(worst.level, mode),
    fontWeight: 600,
  };
}

export function ApmeViolationsCell({ entity }: Readonly<{ entity: Entity }>) {
  const theme = useTheme();
  const mode = theme.palette.type === "dark" ? "dark" : "light";
  const colorTokens = useApmeColorTokens();
  const mutedStatusStyle = {
    fontWeight: 500,
    color: theme.palette.text.primary,
  };
  const apmeApi = useApi(apmeApiRef);
  const [projectMap, setProjectMap] = useState<Map<string, Project> | null>(
    null,
  );
  const [enrichedProject, setEnrichedProject] = useState<Project | null>(null);

  const fetchProjectMap = useCallback(async () => {
    const projects = await apmeApi.getProjects();
    return buildProjectMap(projects);
  }, [apmeApi]);

  useEffect(() => {
    const listener: ProjectMapListener = (map) => setProjectMap(map);
    projectMapListeners.add(listener);
    return () => {
      projectMapListeners.delete(listener);
      if (projectMapListeners.size === 0) {
        stopSharedProjectMapPoller();
      }
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    projectsFetchPromise ??= fetchProjectMap().catch(
      () => new Map<string, Project>(),
    );
    void projectsFetchPromise.then((map) => {
      if (!cancelled) {
        setProjectMap(map);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [fetchProjectMap]);

  useEffect(() => {
    if (!projectMap) {
      return undefined;
    }
    ensureSharedProjectMapPoller(projectMap, fetchProjectMap);
    return undefined;
  }, [projectMap, fetchProjectMap]);

  const repoUrl = normalizeRepoUrlFromEntity(entity);
  const branch = defaultBranchFromEntity(entity);
  const mapProject = repoUrl
    ? projectMap?.get(projectLookupKey(repoUrl, branch))
    : undefined;
  const project = enrichedProject ?? mapProject;

  useEffect(() => {
    setEnrichedProject(null);
    if (!mapProject || !projectNeedsSeverityEnrichment(mapProject)) {
      return undefined;
    }
    let cancelled = false;
    void apmeApi
      .getProjectByRepoUrl(mapProject.repo_url, mapProject.branch)
      .then((detail) => {
        if (!cancelled && detail) {
          setEnrichedProject(detail);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [apmeApi, mapProject]);

  if (!projectMap) {
    return null;
  }

  if (!project) {
    return (
      <Typography variant="body2" style={mutedStatusStyle}>
        Not scanned
      </Typography>
    );
  }

  if (projectHasActiveOperation(project)) {
    return (
      <Box display="flex" alignItems="center" gridGap={6}>
        <CircularProgress size={14} />
        <Typography
          variant="body2"
          style={{ color: "#1976d2", fontWeight: 500 }}
        >
          Scanning…
        </Typography>
      </Box>
    );
  }

  const neverScanned =
    (project.scan_count ?? 0) === 0 && !project.last_scanned_at;

  if (neverScanned) {
    return (
      <Typography variant="body2" style={mutedStatusStyle}>
        Not scanned
      </Typography>
    );
  }

  if (project.total_violations === 0) {
    return (
      <Typography
        variant="body2"
        style={{
          color: colorTokens.dependencyViolation.okCheckColor,
          fontWeight: 500,
        }}
      >
        ✓ No violations
      </Typography>
    );
  }

  return (
    <Typography
      variant="body2"
      style={violationCountStyle(project, mode, theme.palette.text.secondary)}
    >
      {project.total_violations}
    </Typography>
  );
}
