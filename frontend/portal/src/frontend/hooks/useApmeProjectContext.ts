/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAsyncRetry } from "react-use";
import { useApi } from "@backstage/core-plugin-api";
import type { Entity } from "@backstage/catalog-model";
import type {
  Project,
  ProjectDependencies,
  Rule,
  Violation,
} from "../../common/types";
import { buildRulesById } from "../utils/gatewayRules";
import {
  normalizeRepoUrlFromEntity,
  defaultBranchFromEntity,
} from "@ansible/backstage-rhaap-common/catalogEntity";
import { projectHasActiveOperation } from "../../common/operationStatus";
import { apmeApiRef } from "../api";
import { ensureRepoBranchForScan } from "../utils/ensureRepoBranchForScan";
import { registerOrResolveApmeProject } from "../utils/registerOrResolveApmeProject";
import { fetchAllProjectViolations } from "../utils/fetchAllProjectViolations";

export interface ApmeProjectContext {
  repoUrl: string | null;
  branch: string;
  project: Project | null | undefined;
  violations: Violation[];
  rulesById: Map<string, Rule>;
  dependencies: ProjectDependencies | null;
  loading: boolean;
  violationsLoading: boolean;
  dependenciesLoading: boolean;
  error: Error | undefined;
  /** Keep transport failure distinct from a successful empty findings response. */
  violationsError?: Error;
  refresh: () => void;
  /** Refetch violations/rules/deps without re-loading the project record. */
  refreshViolations: () => void;
  registerAndScan: () => Promise<void>;
  registering: boolean;
  registerError: Error | null;
}

export function useApmeProjectContext(entity: Entity): ApmeProjectContext {
  const apmeApi = useApi(apmeApiRef);
  const repoUrl = normalizeRepoUrlFromEntity(entity);
  const branch = defaultBranchFromEntity(entity);
  const [refreshKey, setRefreshKey] = useState(0);
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState<Error | null>(null);

  const {
    value: project,
    loading: projectLoading,
    error,
    retry: retryProject,
  } = useAsyncRetry(async () => {
    if (!repoUrl) return null;
    return apmeApi.getProjectByRepoUrl(repoUrl, branch);
  }, [repoUrl, branch, apmeApi, refreshKey]);

  const {
    value: violations = [],
    loading: violationsLoading,
    error: violationsError,
  } = useAsyncRetry(async () => {
    if (!project?.id) return [];
    return fetchAllProjectViolations(
      apmeApi,
      project.id,
      project.latest_scan?.total_violations ?? project.total_violations,
    );
  }, [
    project?.id,
    apmeApi,
    refreshKey,
    project?.latest_scan?.total_violations,
    project?.total_violations,
  ]);

  const { value: rules = [] } = useAsyncRetry(
    async () => apmeApi.getRules(),
    [apmeApi, refreshKey],
  );

  const rulesById = useMemo(() => buildRulesById(rules), [rules]);

  const { value: dependencies = null, loading: dependenciesLoading } =
    useAsyncRetry(async () => {
      if (!project?.id || (project.scan_count ?? 0) === 0) return null;
      return apmeApi.getProjectDependencies(project.id);
    }, [project?.id, project?.scan_count, apmeApi, refreshKey]);

  const refreshViolations = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  const refresh = useCallback(() => {
    refreshViolations();
    retryProject();
  }, [refreshViolations, retryProject]);

  const registerAndScan = useCallback(async () => {
    if (!repoUrl) return;
    setRegistering(true);
    setRegisterError(null);
    try {
      await ensureRepoBranchForScan(apmeApi, repoUrl, branch);
      const name = entity.metadata.title || entity.metadata.name;
      const resolvedProject = await registerOrResolveApmeProject(apmeApi, {
        name,
        repo_url: repoUrl,
        branch,
      });
      await apmeApi.triggerScan(resolvedProject.id);
      refresh();
    } catch (err) {
      setRegisterError(err as Error);
    } finally {
      setRegistering(false);
    }
  }, [apmeApi, entity, repoUrl, branch, refresh]);

  // Poll only the project record during active operations to avoid
  // hammering violations/rules/deps endpoints every 3 seconds.
  // Full data refresh happens once when the operation completes.
  const [wasActive, setWasActive] = useState(false);

  useEffect(() => {
    if (!project?.id) return undefined;

    const isActive = projectHasActiveOperation(project);

    if (isActive) {
      setWasActive(true);
      const interval = setInterval(() => retryProject(), 3000);
      return () => clearInterval(interval);
    }

    // Operation just finished — do one full refresh of violations/rules/deps
    if (wasActive) {
      setWasActive(false);
      setRefreshKey((k) => k + 1);
    }

    return undefined;
  }, [project, retryProject, wasActive]);

  return {
    repoUrl,
    branch,
    project,
    violations,
    rulesById,
    dependencies,
    loading: projectLoading,
    violationsLoading,
    dependenciesLoading,
    error,
    violationsError,
    refresh,
    refreshViolations,
    registerAndScan,
    registering,
    registerError,
  };
}
