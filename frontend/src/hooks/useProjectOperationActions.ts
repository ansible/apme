/**
 * REST action hooks for project operations (ADR-052).
 *
 * Returns functions to initiate, approve, cancel, and create PRs for
 * project operations via the REST API.
 */

import { useCallback } from "react";

const BASE = "/api/v1";

async function postJson<T>(
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export interface StartOperationOptions {
  ansible_version?: string;
  collection_specs?: string[];
  enable_ai?: boolean;
  ai_model?: string;
}

export function useProjectOperationActions(projectId: string) {
  const start = useCallback(
    async (action: "check" | "remediate", options: StartOperationOptions = {}) => {
      return postJson<{ operation_id: string }>(
        `/projects/${projectId}/operation`,
        { action, options },
      );
    },
    [projectId],
  );

  const approve = useCallback(
    async (approvedIds: string[]) => {
      return postJson<{ status: string }>(
        `/projects/${projectId}/operation/approve`,
        { approved_ids: approvedIds },
      );
    },
    [projectId],
  );

  const cancel = useCallback(async () => {
    return postJson<{ status: string }>(
      `/projects/${projectId}/operation/cancel`,
    );
  }, [projectId]);

  const createPR = useCallback(async (options?: { create_pr?: boolean; branch_name?: string }) => {
    return postJson<{ branch_name: string; commit_sha: string; pr_url: string | null; provider: string }>(
      `/projects/${projectId}/operation/submit`,
      options,
    );
  }, [projectId]);

  return { start, approve, cancel, createPR };
}
