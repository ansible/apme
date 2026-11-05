/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import type { Project } from "./types";
import { projectNeedsSeverityEnrichment } from "./severity";

/** Cap parallel gateway getProject calls during list enrichment. */
export const DEFAULT_SEVERITY_ENRICHMENT_CONCURRENCY = 10;

export interface EnrichProjectsWithSeverityOptions {
  concurrency?: number;
}

async function mapWithConcurrency<T, R>(
  items: readonly T[],
  concurrency: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  if (items.length === 0) {
    return [];
  }
  const results = new Array<R>(items.length);
  let nextIndex = 0;
  const workerCount = Math.min(Math.max(1, concurrency), items.length);

  const workers = Array.from({ length: workerCount }, async () => {
    let index = nextIndex;
    nextIndex += 1;
    while (index < items.length) {
      results[index] = await fn(items[index], index); // NOSONAR - bounded worker pool concurrency
      index = nextIndex;
      nextIndex += 1;
    }
  });

  await Promise.all(workers);
  return results;
}

/**
 * Gateway list_projects returns totals without severity_breakdown; enrich rows
 * that have violations so portal consumers (Violations column, fleet rollups)
 * can colorize by worst severity instead of defaulting to medium/amber.
 */
export async function enrichProjectsWithSeverityBreakdown(
  projects: Project[],
  fetchDetail: (
    projectId: string,
  ) => Promise<Pick<Project, "severity_breakdown">>,
  options?: EnrichProjectsWithSeverityOptions,
): Promise<Project[]> {
  const concurrency =
    options?.concurrency ?? DEFAULT_SEVERITY_ENRICHMENT_CONCURRENCY;

  return mapWithConcurrency(projects, concurrency, async (project) => {
    if (!projectNeedsSeverityEnrichment(project)) {
      return project;
    }
    try {
      const detail = await fetchDetail(project.id);
      if (!detail.severity_breakdown) {
        return project;
      }
      return {
        ...project,
        severity_breakdown: detail.severity_breakdown,
      };
    } catch {
      return project;
    }
  });
}
