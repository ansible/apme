/**
 * SSE hook for real-time project operation state (ADR-052).
 *
 * Connects to GET /api/v1/projects/{id}/operation/events and returns
 * the current OperationState.  On initial connect receives a full
 * snapshot, then applies delta events.  Automatically reconnects on
 * disconnect.  Returns null when no operation is active.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  apmeApiUrl,
  apmeSseUrl,
  getApmeApiAdapter,
} from "../api/apmeApiAdapter";

export type ProjectOperationStatus =
  | "queued"
  | "cloning"
  | "scanning"
  | "assessed"
  | "awaiting_ai_triage"
  | "awaiting_approval"
  | "applying"
  | "completed"
  | "submitting_pr"
  | "pr_submitted"
  | "failed"
  | "expired"
  | "cancelled";

const TERMINAL_STATUSES = new Set<ProjectOperationStatus>([
  "completed",
  "pr_submitted",
  "failed",
  "expired",
  "cancelled",
]);

/** Non-terminal statuses where Activity can offer Resume / Start over. */
export const LIVE_OPERATION_STATUSES = new Set<ProjectOperationStatus>([
  "queued",
  "cloning",
  "scanning",
  "assessed",
  "awaiting_ai_triage",
  "awaiting_approval",
  "applying",
  "submitting_pr",
]);

export interface ProgressEntry {
  phase: string;
  message: string;
  timestamp: string;
  progress?: number | null;
  level?: number | null;
}

export interface Proposal {
  id: string;
  rule_id: string;
  file: string;
  tier: number;
  confidence: number;
  explanation?: string;
  diff_hunk?: string;
  status?: "proposed" | "declined" | "pending" | "approved" | "rejected";
  suggestion?: string;
  line_start?: number;
  path?: string;
  /** ContentGraph NodeType (task, block, play, …); empty when not graph-backed. */
  node_type?: string;
  source?: string;
  before_text?: string;
  after_text?: string;
}

export interface OperationResultData {
  total_violations: number;
  fixable: number;
  ai_proposed: number;
  ai_declined: number;
  ai_accepted: number;
  manual_review: number;
  remediated_count: number;
  fixed_violations: Array<Record<string, unknown>>;
  patches: Array<{ file: string; diff: string }>;
}

export interface AssessFinding {
  rule_id: string;
  severity?: string;
  message: string;
  file: string;
  line?: number | null;
  path?: string;
  /** ContentGraph NodeType (task, block, play, …); empty when not graph-backed. */
  node_type?: string;
  remediation_class?: number;
  source?: string;
  original_yaml?: string;
  fixed_yaml?: string;
  co_fixes?: string[];
  /** File line where the node YAML snippet starts (maps ``line`` into the snippet). */
  node_line_start?: number | null;
  /** Human/gate decision from durable activity (ADR-062). */
  review_status?: string | null;
}

/** Map a file-absolute finding line into 1-based snippet line numbers. */
export function snippetHighlightLine(
  fileLine: number | null | undefined,
  nodeLineStart: number | null | undefined,
  snippetLineCount: number,
): number | null {
  if (fileLine == null || fileLine < 1 || snippetLineCount < 1) {
    return null;
  }
  // Without node_line_start we cannot safely map absolute file lines into the
  // snippet — fall through to message-based refine instead of guessing.
  if (nodeLineStart == null || nodeLineStart < 1) {
    return null;
  }
  const rel = fileLine - nodeLineStart + 1;
  if (rel < 1 || rel > snippetLineCount) {
    return null;
  }
  return rel;
}

const _HIGHLIGHT_STOPWORDS = new Set([
  'the',
  'and',
  'for',
  'with',
  'from',
  'that',
  'this',
  'should',
  'have',
  'has',
  'are',
  'was',
  'use',
  'using',
  'into',
  'your',
  'when',
  'without',
  'avoid',
  'consider',
  'missing',
  'found',
  'play',
  'task',
  'role',
  'file',
  'name',
  'value',
  'true',
  'false',
  'null',
  'instead',
  'collection',
  'module',
  'variable',
  'variables',
  'deprecated',
  'setting',
  'output',
]);

function _escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Find a 1-based snippet line by matching distinctive tokens from the
 * finding message (quoted strings, paths, FQCNs, YAML keys named in the
 * message). No rule-id-specific lookups.
 *
 * Many rules stamp ``line`` as the node start, so the file-line mapping
 * alone always lands on snippet line 1; this recovers a better target when
 * the message itself names the offending content.
 */
export function refineHighlightFromMessage(
  snippet: string,
  message: string,
): number | null {
  if (!snippet.trim() || !message.trim()) {
    return null;
  }
  const lines = snippet.replace(/\n$/, '').split('\n');
  if (lines.length === 0) {
    return null;
  }

  const tokens: string[] = [];
  for (const m of message.matchAll(/`([^`]+)`|"([^"]+)"|'([^']+)'/g)) {
    const t = m[1] || m[2] || m[3];
    if (t && t.length >= 2) {
      tokens.push(t);
    }
  }
  for (const m of message.matchAll(/\/[\w./-]{4,}/g)) {
    tokens.push(m[0]);
  }
  const afterColon = message.match(
    /:\s*([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*)+)/,
  );
  if (afterColon?.[1]) {
    for (const part of afterColon[1].split(',')) {
      const t = part.trim();
      if (t.length > 1) {
        tokens.push(t);
      }
    }
  }
  for (const m of message.matchAll(/\b[a-z_]+\.[a-z_]+\.[a-z0-9_]+\b/g)) {
    tokens.push(m[0]);
  }

  let bestLine: number | null = null;
  let bestLen = 0;
  for (const token of tokens) {
    for (let i = 0; i < lines.length; i++) {
      if (lines[i]!.includes(token) && token.length > bestLen) {
        bestLen = token.length;
        bestLine = i + 1;
      }
    }
  }
  if (bestLine != null) {
    return bestLine;
  }

  for (const key of message.matchAll(/\b([a-z_][a-z0-9_]{2,})\b/g)) {
    const k = key[1]!;
    if (_HIGHLIGHT_STOPWORDS.has(k)) {
      continue;
    }
    const re = new RegExp(`^\\s*${_escapeRegExp(k)}\\s*:`);
    for (let i = 0; i < lines.length; i++) {
      if (re.test(lines[i]!)) {
        return i + 1;
      }
    }
  }
  return null;
}

/** Resolve the best snippet line to highlight for a finding. */
export function resolveSnippetHighlight(opts: {
  fileLine?: number | null;
  nodeLineStart?: number | null;
  snippet: string;
  message?: string;
}): number | null {
  const snippet = opts.snippet ?? '';
  const count = snippet.trim()
    ? snippet.replace(/\n$/, '').split('\n').length
    : 0;
  const mapped = snippetHighlightLine(
    opts.fileLine,
    opts.nodeLineStart,
    count,
  );
  const refined = refineHighlightFromMessage(snippet, opts.message ?? '');

  // Engine gave a mid-snippet line — trust it.
  if (mapped != null && mapped > 1) {
    return mapped;
  }
  // Node-start (or missing) line: prefer a message match inside the snippet.
  if (refined != null) {
    return refined;
  }
  return mapped;
}

export interface ProjectOperationState {
  operation_id: string;
  project_id: string;
  scan_id: string;
  status: ProjectOperationStatus;
  scan_type: "check" | "remediate";
  started_at: string;
  progress: ProgressEntry[];
  proposals?: Proposal[];
  findings?: AssessFinding[];
  /** ADR-062: class-2 findings eligible for AI escalation triage. */
  ai_triage_candidates?: AssessFinding[];
  result?: OperationResultData;
  pr_url?: string;
  error?: string;
  clone_commit?: string;
}

/**
 * Poll for current operation state. Returns the state snapshot, or null
 * if no operation exists (404). Non-OK responses and network failures
 * reject so callers can distinguish "absent" from "probe failed".
 */
export async function fetchProjectOperationState(
  projectId: string,
): Promise<ProjectOperationState | null> {
  const { fetch: doFetch } = getApmeApiAdapter();
  const res = await doFetch(apmeApiUrl(`/projects/${projectId}/operation`));
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(
      `Failed to fetch project operation (${res.status} ${res.statusText})`,
    );
  }
  return (await res.json()) as ProjectOperationState;
}

export interface UseProjectOperationStateOptions {
  /** When false, skip poll/SSE and clear local state. Default true. */
  enabled?: boolean;
}

export function useProjectOperationState(
  projectId: string,
  options: UseProjectOperationStateOptions = {},
) {
  const enabled = options.enabled ?? true;
  const [state, setState] = useState<ProjectOperationState | null>(null);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const cleanup = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    setConnected(false);
  }, []);

  const connect = useCallback(() => {
    cleanup();

    const es = new EventSource(
      apmeSseUrl(`/projects/${projectId}/operation/events`),
    );
    esRef.current = es;

    es.addEventListener("snapshot", (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        const data = JSON.parse(e.data) as ProjectOperationState;
        setState(data);
        setConnected(true);
      } catch {
        /* ignore parse errors */
      }
    });

    es.addEventListener("status_changed", (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        const data = JSON.parse(e.data) as {
          status: string;
          previous: string;
          error?: string;
        };
        setState((prev) =>
          prev
            ? {
                ...prev,
                status: data.status as ProjectOperationStatus,
                ...(data.error ? { error: data.error } : {}),
              }
            : prev,
        );
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("progress", (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        const entry = JSON.parse(e.data) as ProgressEntry;
        setState((prev) =>
          prev
            ? { ...prev, progress: [...(prev.progress ?? []), entry] }
            : prev,
        );
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("proposals", (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        const data = JSON.parse(e.data) as { proposals: Proposal[] };
        setState((prev) =>
          prev ? { ...prev, proposals: data.proposals } : prev,
        );
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("findings", (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        const data = JSON.parse(e.data) as { findings: AssessFinding[] };
        setState((prev) =>
          prev
            ? {
                ...prev,
                status: "assessed",
                findings: data.findings,
              }
            : prev,
        );
      } catch {
        /* ignore */
      }
    });

    // Gateway broadcasts status_changed(awaiting_ai_triage) then ai_triage
    // with {candidates}. Without this handler the Session triage UI stays empty.
    es.addEventListener("ai_triage", (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        const data = JSON.parse(e.data) as { candidates?: AssessFinding[] };
        setState((prev) =>
          prev
            ? {
                ...prev,
                status: "awaiting_ai_triage",
                ai_triage_candidates: data.candidates ?? [],
              }
            : prev,
        );
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("proposal_updated", (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        const data = JSON.parse(e.data) as {
          proposals?: Array<Record<string, unknown>>;
        };
        const updates = data.proposals ?? [];
        if (updates.length === 0) return;
        setState((prev) => {
          if (!prev?.proposals) return prev;
          const byId = new Map(prev.proposals.map((p) => [p.id, p]));
          for (const item of updates) {
            const eng = String(
              item.engine_proposal_id || item.id || "",
            );
            if (!eng || !byId.has(eng)) continue;
            const cur = byId.get(eng)!;
            byId.set(eng, {
              ...cur,
              status: (String(item.status || cur.status) as Proposal["status"]),
              path:
                typeof item.path === "string" ? item.path : cur.path,
              source:
                typeof item.source === "string" ? item.source : cur.source,
            });
          }
          return { ...prev, proposals: Array.from(byId.values()) };
        });
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("result", (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        const result = JSON.parse(e.data) as OperationResultData;
        setState((prev) => (prev ? { ...prev, result } : prev));
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("approval_ack", (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        // Do not force status to "applying" — two-gate interactive flow may
        // return to awaiting_approval; status_changed owns transitions.
        const data = JSON.parse(e.data) as { applied_count: number };
        setState((prev) =>
          prev
            ? {
                ...prev,
                result: prev.result
                  ? { ...prev.result, ai_accepted: data.applied_count }
                  : prev.result,
              }
            : prev,
        );
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("pr_created", (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        const data = JSON.parse(e.data) as { pr_url: string };
        setState((prev) => (prev ? { ...prev, pr_url: data.pr_url } : prev));
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("error_event", (e: MessageEvent) => {
      if (!mountedRef.current) return;
      try {
        const data = JSON.parse(e.data) as { error: string };
        setState((prev) => (prev ? { ...prev, error: data.error } : prev));
      } catch {
        /* ignore */
      }
    });

    es.onerror = () => {
      if (!mountedRef.current) return;
      es.close();
      esRef.current = null;
      setConnected(false);
      reconnectTimer.current = setTimeout(() => {
        if (mountedRef.current) connect();
      }, 3000);
    };
  }, [projectId, cleanup]);

  const poll = useCallback(async () => {
    if (!enabled || !projectId) {
      setState(null);
      return;
    }
    try {
      const s = await fetchProjectOperationState(projectId);
      if (!mountedRef.current) return;
      if (!s) {
        setState(null);
        return;
      }
      setState(s);
      if (!TERMINAL_STATUSES.has(s.status)) {
        connect();
      }
    } catch {
      // Transient Gateway/network error — keep prior state; SSE reconnect
      // or a later refresh can recover.
    }
  }, [projectId, connect, enabled]);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled || !projectId) {
      cleanup();
      setState(null);
      return () => {
        mountedRef.current = false;
        cleanup();
      };
    }
    poll();
    return () => {
      mountedRef.current = false;
      cleanup();
    };
  }, [poll, cleanup, enabled, projectId]);

  const refresh = useCallback(() => {
    if (enabled) poll();
  }, [poll, enabled]);

  const clear = useCallback(() => {
    cleanup();
    setState(null);
  }, [cleanup]);

  return { state, connected, refresh, clear };
}
