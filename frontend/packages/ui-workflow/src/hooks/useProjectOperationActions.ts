/**
 * REST action hooks for project operations (ADR-052).
 *
 * Returns functions to initiate, approve, cancel, and create PRs for
 * project operations via the REST API.
 */

import { useCallback } from "react";
import { apmeApiUrl, getApmeApiAdapter } from "../api/apmeApiAdapter";

/** Raised when remediate would discard an interactive draft working set. */
export class WorkingSetConflictError extends Error {
  readonly code = "working_set_in_progress" as const;

  constructor(message: string) {
    super(message);
    this.name = "WorkingSetConflictError";
  }
}

/** Raised when assess-pause session can no longer accept begin-remediate. */
export class SessionExpiredError extends Error {
  readonly code = "session_expired" as const;

  constructor(message: string) {
    super(message);
    this.name = "SessionExpiredError";
  }
}

function parseErrorBody(status: number, text: string): Error {
  if (status === 409) {
    try {
      const parsed = JSON.parse(text) as {
        detail?: { code?: string; message?: string } | string;
      };
      const detail = parsed.detail;
      if (typeof detail === "string" && detail.trim()) {
        return new Error(detail);
      }
      if (detail && typeof detail === "object") {
        if (detail.code === "working_set_in_progress") {
          return new WorkingSetConflictError(
            detail.message ||
              "Project has an interactive draft working set.",
          );
        }
        if (detail.code === "session_expired") {
          return new SessionExpiredError(
            detail.message ||
              "Assessment session expired; start a new remediate.",
          );
        }
        if (detail.message) {
          return new Error(detail.message);
        }
      }
    } catch {
      /* fall through */
    }
  }
  return new Error(`${status}: ${text}`);
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const { fetch: doFetch } = getApmeApiAdapter();
  const res = await doFetch(apmeApiUrl(path), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text();
    throw parseErrorBody(res.status, text);
  }
  return res.json() as Promise<T>;
}

async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const { fetch: doFetch } = getApmeApiAdapter();
  const res = await doFetch(apmeApiUrl(path), {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(body),
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
  /** ADR-062 Option C: when true, pause for Quick-fix review. */
  interactive?: boolean;
  /** ADR-064: pause after findings before Gate 1 (Scan → Remediate). */
  assess_pause?: boolean;
  /** Top-level OperateRequest flag — discard draft working set on conflict. */
  abandon_working_set?: boolean;
}

export function useProjectOperationActions(projectId: string) {
  const start = useCallback(
    async (action: "check" | "remediate", options: StartOperationOptions = {}) => {
      const { abandon_working_set, ...opOptions } = options;
      return postJson<{ operation_id: string }>(
        `/projects/${projectId}/operation`,
        {
          action,
          options: opOptions,
          ...(abandon_working_set ? { abandon_working_set: true } : {}),
        },
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

  const beginRemediate = useCallback(async () => {
    return postJson<{ status: string }>(
      `/projects/${projectId}/operation/begin-remediate`,
    );
  }, [projectId]);

  const patchProposals = useCallback(
    async (
      updates: Array<{ proposal_id: string; status: string }>,
    ) => {
      return patchJson<{ updated: unknown[] }>(
        `/projects/${projectId}/operation/proposals`,
        { updates },
      );
    },
    [projectId],
  );

  const cancel = useCallback(async () => {
    return postJson<{ status: string }>(
      `/projects/${projectId}/operation/cancel`,
    );
  }, [projectId]);

  /** ADR-062: leave awaiting_ai_triage — empty targets skips AI. */
  const escalateAi = useCallback(
    async (targets: Array<{ path: string; rule_ids?: string[] }> = []) => {
      return postJson<{ status?: string }>(
        `/projects/${projectId}/operation/escalate-ai`,
        { targets },
      );
    },
    [projectId],
  );

  const createPR = useCallback(
    async (options?: {
      create_pr?: boolean;
      branch_name?: string;
      title?: string;
      body?: string;
    }) => {
      return postJson<{
        branch_name: string;
        commit_sha: string;
        pr_url: string | null;
        provider: string;
      }>(`/projects/${projectId}/operation/submit`, options);
    },
    [projectId],
  );

  return {
    start,
    approve,
    beginRemediate,
    cancel,
    createPR,
    patchProposals,
    escalateAi,
  };
}
