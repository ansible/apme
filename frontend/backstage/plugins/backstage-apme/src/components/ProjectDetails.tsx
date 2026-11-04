/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, type ReactNode } from "react";
import { Flex, Text } from "@backstage/ui";
import { getProject, type ProjectDetail } from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { severityOrder } from "@apme/ui-workflow/headless";
import "./layout.css";
import { useLoad } from "./useLoad";
import { DataStatus } from "./DataStatus";

export function ProjectDetails({
  projectId,
  adapter,
  children,
}: {
  projectId: string;
  adapter: ApmeApiAdapter;
  children?:
    ReactNode | ((project: ProjectDetail, onUpdated: () => void) => ReactNode);
}) {
  const load = useCallback(
    () => getProject(projectId, adapter),
    [projectId, adapter],
  );
  const state = useLoad(load);
  const { value: project, error, reload } = state;
  if (error) return <DataStatus {...state} subject="project" />;
  if (!project) return <Text role="status">Loading project…</Text>;
  return (
    <Flex className="apme-ui" direction="column" gap="4">
      <div className="apme-stack apme-project-heading" style={{ gap: 6 }}>
        <Text as="h2" variant="title-medium">
          {project.name}
        </Text>
        <div className="apme-project-meta">
          <Text color="secondary">{project.repo_url}</Text>
          <Text color="secondary">Branch: {project.branch}</Text>
          <Text color="secondary">
            {project.total_violations} findings · {project.scan_count} checks
          </Text>
          {typeof project.health_score === "number" && (
            <Text color="secondary">
              Health: {Math.round(project.health_score)}%
            </Text>
          )}
          {project.last_scanned_at && (
            <Text color="secondary">
              Last checked:{" "}
              <time dateTime={project.last_scanned_at}>
                {new Date(project.last_scanned_at).toLocaleString()}
              </time>
            </Text>
          )}
          {project.violation_trend && (
            <Text color="secondary">Trend: {project.violation_trend}</Text>
          )}
          {project.has_new_commits && (
            <Text color="secondary">New commits since the last check</Text>
          )}
        </div>
        {project.severity_breakdown &&
          Object.entries(project.severity_breakdown).some(
            ([, count]) => count > 0,
          ) && (
            <div
              className="apme-project-meta"
              aria-label="Finding severity summary"
            >
              {Object.entries(project.severity_breakdown)
                .filter(([, count]) => count > 0)
                .sort(
                  ([left], [right]) =>
                    severityOrder(left) - severityOrder(right),
                )
                .map(([level, count]) => (
                  <span key={level}>
                    {count} {level}
                  </span>
                ))}
            </div>
          )}
      </div>
      {typeof children === "function" ? children(project, reload) : children}
    </Flex>
  );
}
