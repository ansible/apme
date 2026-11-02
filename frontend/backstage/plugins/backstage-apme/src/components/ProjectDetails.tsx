/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, type ReactNode } from "react";
import { Alert, Flex, Text } from "@backstage/ui";
import { getProject, type ProjectDetail } from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import "./layout.css";
import { useLoad } from "./useLoad";

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
  const { value: project, error, reload } = useLoad(load);
  if (error)
    return (
      <Alert
        status="danger"
        title="Could not load project"
        description={error}
      />
    );
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
        </div>
      </div>
      {typeof children === "function" ? children(project, reload) : children}
    </Flex>
  );
}
