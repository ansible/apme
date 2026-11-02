/*
 * Copyright Red Hat
 *
 * Fleet quality data loading — kept separate from FleetQualityTab presentation.
 */

import { useAsync } from "react-use";
import { useApi } from "@backstage/core-plugin-api";
import { catalogApiRef } from "@backstage/plugin-catalog-react";
import { Entity } from "@backstage/catalog-model";
import type { Project } from "../../../common/types";
import {
  SEVERITY_ORDER,
  normalizeSeverity,
  type SeverityLevel,
} from "../../../common/severity";
import { projectLookupKey } from "@ansible/backstage-rhaap-common/catalogEntity";
import { apmeApiRef } from "../../api";
import { isApmeConnectionError } from "../../utils/apmeConnectionError";
import { fetchAllProjectViolations } from "../../utils/fetchAllProjectViolations";

export type FleetRepoRow = {
  project: Project;
  entityName: string;
  count: number;
  remediationClass: number;
  lastScannedAt?: string;
};

export type RuleAggregate = {
  ruleId: string;
  message: string;
  level: string;
  category?: string;
  repos: FleetRepoRow[];
  totalCount: number;
};

export type FleetQualityData = {
  groups: RuleAggregate[];
  reposWithIssues: number;
  totalRepos: number;
  hasAnyScan: boolean;
  gatewayUnavailable: boolean;
  violationTotal: number;
  severityCounts: Record<SeverityLevel, number>;
};

function entityProjectLookupKey(entity: Entity): string | undefined {
  const loc =
    entity.metadata?.annotations?.["backstage.io/source-location"] ??
    entity.metadata?.annotations?.["ansible.com/repository-url"];
  if (!loc) return undefined;
  const match = /url:(https?:\/\/[^\s]+)/.exec(loc);
  const repoUrl = match ? match[1] : loc.replace(/^url:/, "");
  const spec = entity.spec as
    { repository_default_branch?: string } | undefined;
  const branch = spec?.repository_default_branch ?? "main";
  return projectLookupKey(repoUrl, branch);
}

/** Fleet Quality is scoped to catalog git-repository entities, not orphan APME projects. */
function projectsLinkedToCatalog(
  projects: Project[],
  entityByProjectKey: Map<string, string>,
): Project[] {
  return projects.filter((project) =>
    entityByProjectKey.has(projectLookupKey(project.repo_url, project.branch)),
  );
}

const emptyData = (): FleetQualityData => ({
  groups: [],
  reposWithIssues: 0,
  totalRepos: 0,
  hasAnyScan: false,
  gatewayUnavailable: false,
  violationTotal: 0,
  severityCounts: {} as Record<SeverityLevel, number>,
});

export function useFleetQualityData(enabled: boolean) {
  const apmeApi = useApi(apmeApiRef);
  const catalogApi = useApi(catalogApiRef);

  return useAsync(async (): Promise<FleetQualityData> => {
    if (!enabled) {
      return emptyData();
    }

    const catalogResponse = await catalogApi.getEntities({
      filter: [{ kind: "Component", "spec.type": "git-repository" }],
    });

    const entities = Array.isArray(catalogResponse)
      ? catalogResponse
      : (catalogResponse.items ?? []);

    let projects: Project[] = [];
    let gatewayUnavailable = false;
    try {
      projects = await apmeApi.getProjects();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (isApmeConnectionError(message)) {
        gatewayUnavailable = true;
      } else {
        throw err;
      }
    }

    const entityByProjectKey = new Map<string, string>();
    for (const entity of entities) {
      const key = entityProjectLookupKey(entity);
      if (key && entity.metadata?.name) {
        entityByProjectKey.set(key, entity.metadata.name);
      }
    }

    const catalogProjects = projectsLinkedToCatalog(
      projects,
      entityByProjectKey,
    );
    const totalRepos = entities.length;
    const hasAnyScan = catalogProjects.some(
      (p) => (p.scan_count ?? 0) > 0 || Boolean(p.last_scanned_at),
    );
    const scanned = gatewayUnavailable
      ? []
      : catalogProjects.filter((p) => (p.total_violations ?? 0) > 0);
    // Fan-out per scanned repo; prefer a fleet-summary API when fleets grow large.
    const violationsByProject = gatewayUnavailable
      ? []
      : await Promise.all(
          scanned.map(async (project) => ({
            project,
            violations: await fetchAllProjectViolations(
              apmeApi,
              project.id,
              project.total_violations,
            ),
          })),
        );

    const ruleMap = new Map<string, RuleAggregate>();
    const severityCounts = SEVERITY_ORDER.reduce(
      (acc, sev) => {
        acc[sev] = 0;
        return acc;
      },
      {} as Record<SeverityLevel, number>,
    );

    for (const { project, violations } of violationsByProject) {
      const projectKey = projectLookupKey(project.repo_url, project.branch);
      const entityName =
        entityByProjectKey.get(projectKey) ??
        project.name.replace(/[^a-zA-Z0-9-_]/g, "-").toLowerCase();

      for (const v of violations) {
        const sev = normalizeSeverity(v.level);
        severityCounts[sev] = (severityCounts[sev] ?? 0) + 1;

        const existing = ruleMap.get(v.rule_id);
        if (!existing) {
          ruleMap.set(v.rule_id, {
            ruleId: v.rule_id,
            message: v.message,
            level: v.level,
            category: v.category,
            repos: [
              {
                project,
                entityName,
                count: 1,
                remediationClass: v.remediation_class,
                lastScannedAt: project.last_scanned_at,
              },
            ],
            totalCount: 1,
          });
        } else {
          existing.totalCount += 1;
          const repo = existing.repos.find((r) => r.project.id === project.id);
          if (repo) {
            repo.count += 1;
          } else {
            existing.repos.push({
              project,
              entityName,
              count: 1,
              remediationClass: v.remediation_class,
              lastScannedAt: project.last_scanned_at,
            });
          }
        }
      }
    }

    const groups = Array.from(ruleMap.values());
    const violationTotal = groups.reduce((sum, g) => sum + g.totalCount, 0);
    const reposWithIssues = new Set(
      groups.flatMap((g) => g.repos.map((r) => r.project.id)),
    ).size;

    return {
      groups,
      reposWithIssues,
      totalRepos,
      hasAnyScan,
      gatewayUnavailable,
      violationTotal,
      severityCounts,
    };
  }, [enabled, apmeApi, catalogApi]);
}
