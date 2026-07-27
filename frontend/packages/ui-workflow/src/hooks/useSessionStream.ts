/**
 * WebSocket hook for the FixSession lifecycle.
 *
 * Manages the full check+remediate flow over a single WS connection:
 *   connect → upload files → progress → tier1 results →
 *   AI proposals → approval → final result
 *
 * Supports session reconnection: if the WebSocket drops during an
 * interactive phase (e.g. awaiting_approval), ``canReconnect`` becomes
 * true and ``resumeSession`` can re-establish the connection using the
 * ``?resume=<session_id>`` gateway endpoint.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { apmeWsUrl } from "../api/apmeApiAdapter";

// ── Types ──────────────────────────────────────────────────────────

export interface ProgressEntry {
  phase: string;
  message: string;
  level: number;
  timestamp: number;
}

export interface Patch {
  file: string;
  diff: string;
  applied_rules: string[];
  patched?: string;
}

export interface Tier1Result {
  idempotency_ok: boolean;
  patches: Patch[];
  format_diffs: Array<{ file: string; diff: string }>;
  report: Record<string, unknown> | null;
}

export interface Proposal {
  id: string;
  file: string;
  rule_id: string;
  line_start: number;
  line_end: number;
  before_text: string;
  after_text: string;
  diff_hunk: string;
  confidence: number;
  explanation: string;
  tier: number;
  status?: "proposed" | "declined" | "pending" | "approved" | "rejected";
  suggestion?: string;
  path?: string;
  /** ContentGraph NodeType (task, block, play, …); empty when not graph-backed. */
  node_type?: string;
  source?: string;
}

export interface RemainingViolation {
  rule_id: string;
  level: string;
  message: string;
  file: string;
}

export interface SessionResult {
  scan_id: string;
  patches: Patch[];
  report: Record<string, unknown> | null;
  remaining_violations: RemainingViolation[];
}

export type SessionStatus =
  | "idle"
  | "connecting"
  | "uploading"
  | "checking"
  | "tier1_done"
  | "awaiting_approval"
  | "applying"
  | "complete"
  | "disconnected"
  | "error";

export interface SessionOptions {
  ansibleVersion?: string;
  collections?: string[];
  enableAi?: boolean;
  aiModel?: string;
  /** ADR-062 Option C: Gate 1 review when true (SPA remediate default). */
  interactive?: boolean;
}

// ── Helpers ────────────────────────────────────────────────────────

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = reader.result as string;
      const idx = url.indexOf(",");
      resolve(idx >= 0 ? url.slice(idx + 1) : url);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

/** Phases where a dropped WS can be recovered via session resume. */
const RECONNECTABLE_PHASES: ReadonlySet<SessionStatus> = new Set([
  "tier1_done",
  "awaiting_approval",
]);

// ── Session persistence ────────────────────────────────────────────

const SESSION_STORAGE_KEY = "apme_active_session";
const DEFAULT_TTL_SECONDS = 1800;

export interface PersistedSession {
  sessionId: string;
  scanId: string;
  timestamp: number;
  ttlSeconds: number;
}

function isPersistedSession(v: unknown): v is PersistedSession {
  if (typeof v !== "object" || v === null) return false;
  const o = v as Record<string, unknown>;
  return (
    typeof o.sessionId === "string" &&
    typeof o.scanId === "string" &&
    typeof o.timestamp === "number" &&
    typeof o.ttlSeconds === "number"
  );
}

function persistSession(
  sessionId: string,
  scanId: string,
  ttlSeconds?: number,
): void {
  try {
    const data: PersistedSession = {
      sessionId,
      scanId,
      timestamp: Date.now(),
      ttlSeconds: ttlSeconds ?? DEFAULT_TTL_SECONDS,
    };
    sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(data));
  } catch {
    // sessionStorage may be unavailable (private browsing, quota)
  }
}

function clearPersistedSession(): void {
  try {
    sessionStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // ignore
  }
}

/**
 * Check for an active session persisted across navigation.
 * Returns null if none exists, is malformed, or has exceeded its TTL.
 */
export function getPersistedSession(): PersistedSession | null {
  try {
    const raw = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return null;
    const data: unknown = JSON.parse(raw);
    if (!isPersistedSession(data)) {
      clearPersistedSession();
      return null;
    }
    if (Date.now() - data.timestamp > data.ttlSeconds * 1000) {
      clearPersistedSession();
      return null;
    }
    return data;
  } catch {
    clearPersistedSession();
    return null;
  }
}

// ── Hook ───────────────────────────────────────────────────────────

export function useSessionStream() {
  const [status, setStatus] = useState<SessionStatus>("idle");
  const [progress, setProgress] = useState<ProgressEntry[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [scanId, setScanId] = useState<string | null>(null);
  const [tier1, setTier1] = useState<Tier1Result | null>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [result, setResult] = useState<SessionResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [canReconnect, setCanReconnect] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const statusRef = useRef<SessionStatus>("idle");
  const sessionIdRef = useRef<string | null>(null);

  const updateStatus = useCallback((s: SessionStatus) => {
    statusRef.current = s;
    setStatus(s);
  }, []);

  const reset = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    updateStatus("idle");
    setProgress([]);
    setSessionId(null);
    setScanId(null);
    setTier1(null);
    setProposals([]);
    setResult(null);
    setError(null);
    setCanReconnect(false);
    sessionIdRef.current = null;
    clearPersistedSession();
  }, [updateStatus]);

  /** Wire shared WS event handlers (used by both start and resume). */
  const wireHandlers = useCallback(
    (ws: WebSocket) => {
      ws.onmessage = (event) => {
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(event.data as string);
        } catch {
          return;
        }

        switch (msg.type) {
          case "session_created":
            setSessionId(msg.session_id as string);
            sessionIdRef.current = msg.session_id as string;
            setScanId(msg.scan_id as string);
            persistSession(
              msg.session_id as string,
              msg.scan_id as string,
              typeof msg.ttl_seconds === "number"
                ? msg.ttl_seconds
                : undefined,
            );
            updateStatus("checking");
            break;

          case "progress":
            setProgress((prev) => [
              ...prev,
              {
                phase: (msg.phase as string) || "",
                message: (msg.message as string) || "",
                level: (msg.level as number) ?? 2,
                timestamp: Date.now(),
              },
            ]);
            break;

          case "tier1_complete":
            setTier1(msg as unknown as Tier1Result);
            updateStatus("tier1_done");
            break;

          case "proposals":
            setProposals(msg.proposals as Proposal[]);
            updateStatus("awaiting_approval");
            break;

          case "approval_ack":
            // Two-gate interactive: do not treat ack as terminal; another
            // proposals event may follow. COMPLETE is driven by result.
            if (msg.status === "COMPLETE") {
              updateStatus("applying");
            }
            break;

          case "result":
            setResult(msg as unknown as SessionResult);
            setCanReconnect(false);
            clearPersistedSession();
            updateStatus("complete");
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: "close" }));
              setTimeout(() => {
                if (
                  ws.readyState === WebSocket.OPEN ||
                  ws.readyState === WebSocket.CLOSING
                ) {
                  ws.close(1000);
                }
              }, 100);
            }
            break;

          case "expiring":
            break;

          case "error":
            setError((msg.message as string) || "Unknown error");
            if (RECONNECTABLE_PHASES.has(statusRef.current) && sessionIdRef.current) {
              setCanReconnect(true);
              updateStatus("disconnected");
            } else {
              updateStatus("error");
            }
            break;

          case "closed":
            clearPersistedSession();
            if (
              statusRef.current !== "complete" &&
              statusRef.current !== "error"
            ) {
              updateStatus("complete");
            }
            break;
        }
      };

      ws.onerror = () => {
        if (RECONNECTABLE_PHASES.has(statusRef.current) && sessionIdRef.current) {
          setError("Connection lost. Your session is still active on the server.");
          setCanReconnect(true);
          updateStatus("disconnected");
        } else {
          setError("WebSocket connection error");
          updateStatus("error");
        }
      };

      ws.onclose = (event) => {
        if (
          event.code !== 1000 &&
          statusRef.current !== "complete" &&
          statusRef.current !== "error" &&
          statusRef.current !== "disconnected"
        ) {
          if (RECONNECTABLE_PHASES.has(statusRef.current) && sessionIdRef.current) {
            setError("Connection lost. Your session is still active on the server.");
            setCanReconnect(true);
            updateStatus("disconnected");
          } else {
            setError("Connection closed unexpectedly");
            updateStatus("error");
          }
        }
      };
    },
    [updateStatus],
  );

  const startSession = useCallback(
    async (files: File[], options: SessionOptions = {}) => {
      reset();
      updateStatus("connecting");

      const ws = new WebSocket(apmeWsUrl("/api/v1/ws/session"));
      wsRef.current = ws;

      ws.onopen = async () => {
        updateStatus("uploading");

        const startOptions: Record<string, unknown> = {
          ansible_version: options.ansibleVersion || "",
          collections: options.collections || [],
          enable_ai: options.enableAi ?? true,
          interactive: options.interactive ?? true,
        };
        if (options.aiModel) {
          startOptions.ai_model = options.aiModel;
        }
        ws.send(JSON.stringify({ type: "start", options: startOptions }));

        for (const file of files) {
          const content = await fileToBase64(file);
          const path =
            (file as File & { webkitRelativePath?: string })
              .webkitRelativePath || file.name;
          ws.send(JSON.stringify({ type: "file", path, content }));
        }

        ws.send(JSON.stringify({ type: "files_done" }));
      };

      wireHandlers(ws);
    },
    [reset, updateStatus, wireHandlers],
  );

  const resumeSession = useCallback(
    (sid: string, originalScanId?: string) => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setError(null);
      setCanReconnect(false);
      updateStatus("connecting");

      let url = `/api/v1/ws/session?resume=${encodeURIComponent(sid)}`;
      if (originalScanId) {
        url += `&scan_id=${encodeURIComponent(originalScanId)}`;
      }
      const ws = new WebSocket(apmeWsUrl(url));
      wsRef.current = ws;

      ws.onopen = () => {
        updateStatus("checking");
      };

      wireHandlers(ws);
    },
    [updateStatus, wireHandlers],
  );

  const approve = useCallback(
    (approvedIds: string[]) => {
      const ws = wsRef.current;
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({ type: "approve", approved_ids: approvedIds }),
        );
      } else {
        setError(
          "Connection lost — cannot send approval. Try reconnecting.",
        );
        if (sessionIdRef.current) {
          setCanReconnect(true);
          updateStatus("disconnected");
        } else {
          updateStatus("error");
        }
      }
    },
    [updateStatus],
  );

  const extend = useCallback(() => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "extend" }));
    }
  }, []);

  const closeSession = useCallback(() => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "close" }));
    }
  }, []);

  const cancel = useCallback(() => {
    wsRef.current?.close();
    clearPersistedSession();
    updateStatus("idle");
  }, [updateStatus]);

  // Close WebSocket on unmount (navigation away) but keep the persisted
  // session reference so the user can resume when they navigate back.
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  return {
    status,
    progress,
    sessionId,
    scanId,
    tier1,
    proposals,
    result,
    error,
    canReconnect,
    startSession,
    resumeSession,
    approve,
    extend,
    closeSession,
    cancel,
    reset,
  };
}
