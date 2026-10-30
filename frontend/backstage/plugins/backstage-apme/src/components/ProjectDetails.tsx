/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, type ReactNode } from "react";
import { Alert, Flex, Text } from "@backstage/ui";
import { getProject, type ProjectDetail } from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { useLoad } from "./useLoad";

export function ProjectDetails({
  projectId,
  adapter,
  children,
}: {
  projectId: string;
  adapter: ApmeApiAdapter;
  children?: ReactNode | ((project: ProjectDetail) => ReactNode);
}) {
  const load = useCallback(
    () => getProject(projectId, adapter),
    [projectId, adapter],
  );
  const { value: project, error } = useLoad(load);
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
    <Flex direction="column" gap="2">
      <Text as="h2" variant="title-medium">
        {project.name}
      </Text>
      <Text>
        {project.repo_url} · {project.branch}
      </Text>
      <Text>
        {project.total_violations} findings · {project.scan_count} checks
      </Text>
      {typeof children === "function" ? children(project) : children}
    </Flex>
  );
}
