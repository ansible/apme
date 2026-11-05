/*
 * Copyright Red Hat
 */

import type { CreateProjectRequest, Project } from "./types";
import { projectLookupKey } from "./catalogEntity";
import type { IApmeService } from "./ApmeService";

export type ApmeProjectResolver = Pick<
  IApmeService,
  "getProjectByRepoUrl" | "getProjects" | "createProject"
>;

export function isApmeProjectConflictError(err: unknown): boolean {
  if (!(err instanceof Error)) {
    return false;
  }
  return (
    err.message.includes("409") ||
    err.message.toLowerCase().includes("already exists")
  );
}

/** Finds an existing APME project when gateway lookup by repo URL misses. */
export async function resolveApmeProject(
  apmeService: ApmeProjectResolver,
  repoUrl: string,
  branch: string,
): Promise<Project | null> {
  const branchValue = branch || "main";
  const byRepo = await apmeService.getProjectByRepoUrl(repoUrl, branchValue);
  if (byRepo) {
    return byRepo;
  }

  const lookupKey = projectLookupKey(repoUrl, branchValue);
  const projects = await apmeService.getProjects();
  const byKey = projects.find(
    (project) =>
      projectLookupKey(project.repo_url, project.branch ?? "main") ===
      lookupKey,
  );
  if (byKey) {
    return byKey;
  }

  return null;
}

/** Creates a project or returns the existing record when the gateway reports a conflict. */
export async function registerOrResolveApmeProject(
  apmeService: ApmeProjectResolver,
  request: CreateProjectRequest,
): Promise<Project> {
  const branch = request.branch ?? "main";

  const existing = await resolveApmeProject(
    apmeService,
    request.repo_url,
    branch,
  );
  if (existing) {
    return existing;
  }

  try {
    const created = await apmeService.createProject(request);
    return created;
  } catch (err) {
    if (!isApmeProjectConflictError(err)) {
      throw err;
    }
    const resolved = await resolveApmeProject(
      apmeService,
      request.repo_url,
      branch,
    );
    if (!resolved) {
      throw err;
    }
    return resolved;
  }
}
