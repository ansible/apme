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
  // Additive: old servers and third-party producers may omit these (or send
  // JSON null for Python None). State always holds finite numbers
  // post-normalization (see the proposals handler), so readers can treat 0
  // as unknown.
  line_start?: number;
  line_end?: number;
  // Same additive contract as the line fields: missing/null tier/confidence
  // normalize to 0 and missing/null text normalizes to "". State always
  // holds a finite number / string post-normalization, so readers can treat
  // 0 as unknown and "" as absent without null checks.
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

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isPatchArray(v: unknown): v is Patch[] {
  return (
    Array.isArray(v) &&
    v.every(
      (p) =>
        isRecord(p) &&
        typeof p.file === "string" &&
        typeof p.diff === "string" &&
        // applied_rules is additive: old servers omit it. Accept
        // undefined/null here and normalize to [] where patches enter state
        // so mixed-version rollouts degrade instead of tearing down.
        (typeof p.applied_rules === "undefined" ||
          p.applied_rules === null ||
          (Array.isArray(p.applied_rules) &&
            (p.applied_rules as unknown[]).every(
              (r) => typeof r === "string",
            ))),
    )
  );
}

/** Fill additive patch fields old servers omit so state always holds arrays. */
function normalizePatches(patches: Patch[]): Patch[] {
  return patches.map((p) => ({
    ...p,
    applied_rules: Array.isArray(p.applied_rules) ? p.applied_rules : [],
  }));
}

/** Validate a tier1_complete payload before it reaches state. */
function isTier1Result(v: unknown): v is Tier1Result {
  if (!isRecord(v)) return false;
  return (
    typeof v.idempotency_ok === "boolean" &&
    isPatchArray(v.patches) &&
    Array.isArray(v.format_diffs) &&
    (v.report === null || isRecord(v.report))
  );
}

/** Finite number or an additive missing marker (undefined / JSON null). */
function isFiniteOrNullish(v: unknown): boolean {
  return (
    typeof v === "undefined" ||
    v === null ||
    (typeof v === "number" && Number.isFinite(v))
  );
}

/** String or an additive missing marker (undefined / JSON null). */
function isStringOrNullish(v: unknown): boolean {
  return typeof v === "undefined" || v === null || typeof v === "string";
}

/** Validate a proposals payload before it reaches state. */
function isProposalArray(v: unknown): v is Proposal[] {
  return (
    Array.isArray(v) &&
    v.every(
      (p) =>
        isRecord(p) &&
        typeof p.id === "string" &&
        typeof p.file === "string" &&
        typeof p.rule_id === "string" &&
        // line_start/line_end/tier/confidence are additive: old servers and
        // third-party producers may omit them, and Python None serializes as
        // JSON null (not omission). Accept undefined/null here and normalize
        // to 0 at setProposals so mixed-version rollouts degrade instead of
        // tearing down. Numbers must be finite: NaN/Infinity would poison
        // downstream math (confidence %) and gate comparisons (tier).
        isFiniteOrNullish(p.line_start) &&
        isFiniteOrNullish(p.line_end) &&
        isFiniteOrNullish(p.tier) &&
        isFiniteOrNullish(p.confidence) &&
        // Text fields follow the same additive pattern: JSON null (Python
        // None) normalizes to "" at setProposals so state typed `string`
        // never holds null into downstream string ops.
        isStringOrNullish(p.before_text) &&
        isStringOrNullish(p.after_text) &&
        isStringOrNullish(p.diff_hunk),
    )
  );
}

/** Validate a result payload before it reaches state. */
function isSessionResult(v: unknown): v is SessionResult {
  if (!isRecord(v)) return false;
  return (
    typeof v.scan_id === "string" &&
    isPatchArray(v.patches) &&
    (v.report === null || isRecord(v.report)) &&
    Array.isArray(v.remaining_violations)
  );
}

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
  // True once session_created arrived on the current socket. Distinguishes a
  // live session (keep resume material on failure) from a resume/start that
  // never established (drop the persisted key: the id is dead).
  const sessionEstablishedRef = useRef(false);
  // Malformed-frame taint per frame type: only the first malformed frame of
  // a given type surfaces via setError; repeats are ignored so a looping
  // server cannot spam errors while the socket stays open.
  const malformedTaintRef = useRef<Set<string>>(new Set());
  // Which tainted frame type produced the currently surfaced malformed-frame
  // error (null when the error came from elsewhere or was cleared). The next
  // valid frame of that type clears both the taint and the error.
  const errorSourceRef = useRef<string | null>(null);

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
    sessionEstablishedRef.current = false;
    malformedTaintRef.current = new Set();
    errorSourceRef.current = null;
    clearPersistedSession();
  }, [updateStatus]);

  /** Wire shared WS event handlers (used by both start and resume). */
  const wireHandlers = useCallback(
    (ws: WebSocket) => {
      // A malformed TERMINAL result means the server stream cannot be
      // trusted: surface the error and close the socket instead of leaving
      // the UI on a non-terminal spinner with no recovery path.
      const failMalformedTerminal = (message: string) => {
        setError(message);
        errorSourceRef.current = null;
        setCanReconnect(false);
        clearPersistedSession();
        updateStatus("error");
        try {
          ws.close(1000);
        } catch {
          // ignore close errors
        }
        if (wsRef.current === ws) {
          wsRef.current = null;
        }
      };
      // A malformed NON-terminal frame (tier1_complete, proposals,
      // session_created) must not destroy resume: the server session is
      // still alive and the phase may be reconnectable. Surface the error
      // but preserve the persisted session and the reconnect affordance,
      // and keep the socket open so the server can continue the stream.
      const failMalformedNonTerminal = (message: string, kind: string) => {
        // Taint tracking: only the first malformed frame of a given type
        // surfaces. Further same-type frames are fully ignored (no setError
        // spam loop) while the socket stays open in reconnectable phases.
        if (malformedTaintRef.current.has(kind)) {
          return;
        }
        malformedTaintRef.current.add(kind);
        errorSourceRef.current = kind;
        if (kind === "proposals") {
          // Drop any previously accepted set: approving a stale set after a
          // malformed frame must be impossible.
          setProposals([]);
        }
        setError(message);
        if (
          RECONNECTABLE_PHASES.has(statusRef.current) &&
          sessionIdRef.current
        ) {
          setCanReconnect(true);
          updateStatus("disconnected");
        } else {
          // Non-reconnectable: an open socket behind an "error" UI is dead
          // (a later valid frame would flip error→complete). Close like the
          // terminal path, but keep the persisted session: the server
          // session is still alive, unlike a malformed terminal result.
          setCanReconnect(false);
          updateStatus("error");
          try {
            ws.close(1000);
          } catch {
            // ignore close errors
          }
          if (wsRef.current === ws) {
            wsRef.current = null;
          }
        }
      };
      // A valid frame proves the stream recovered for its type: drop that
      // type's taint so a later malformed frame surfaces again, and clear
      // the error if it came from this type. Parse-failure ("message") taint
      // has no typed valid frame, so any valid frame clears it — including
      // progress (which has no typed validator and therefore never carries
      // its own taint; see the progress case below).
      const noteValidFrame = (kind: string) => {
        malformedTaintRef.current.delete(kind);
        malformedTaintRef.current.delete("message");
        if (
          errorSourceRef.current === kind ||
          errorSourceRef.current === "message"
        ) {
          errorSourceRef.current = null;
          setError(null);
        }
      };
      // True while the current socket never delivered session_created: a
      // resume/start that fails here points at a dead id, so the persisted
      // key must go (reloads stop re-offering resume to it).
      const isPreSession = () =>
        !sessionEstablishedRef.current &&
        (statusRef.current === "connecting" ||
          statusRef.current === "checking");
      ws.onmessage = (event) => {
        if (wsRef.current !== ws) return;
        let raw: unknown;
        try {
          raw = JSON.parse(event.data as string);
        } catch {
          failMalformedNonTerminal(
            "Received malformed message from server",
            "message",
          );
          return;
        }
        if (!isRecord(raw)) {
          failMalformedNonTerminal(
            "Received malformed message from server",
            "message",
          );
          return;
        }
        const msg = raw;

        switch (msg.type) {
          case "session_created": {
            const sid = msg.session_id;
            const scid = msg.scan_id;
            if (
              typeof sid !== "string" ||
              sid.length === 0 ||
              typeof scid !== "string" ||
              scid.length === 0
            ) {
              failMalformedNonTerminal(
                "Received malformed session from server",
                "session_created",
              );
              break;
            }
            setSessionId(sid);
            sessionIdRef.current = sid;
            sessionEstablishedRef.current = true;
            noteValidFrame("session_created");
            setScanId(scid);
            persistSession(
              sid,
              scid,
              typeof msg.ttl_seconds === "number"
                ? msg.ttl_seconds
                : undefined,
            );
            updateStatus("checking");
            break;
          }

          case "progress":
            // Progress has no typed validator (fields degrade to ""/2), so
            // it cannot prove recovery for a typed taint. Clearing via
            // noteValidFrame("progress") is still safe: it drops only the
            // "progress" (never-tainted, noop) and "message" parse-failure
            // taints, and clears the surfaced error only when its source is
            // "progress" (impossible) or "message". Typed taints/errors
            // (proposals, tier1_complete, …) survive until their own valid
            // frame arrives. Without this, a transient JSON-parse error
            // would pin the error UI even while progress frames prove the
            // stream recovered (progress is the most frequent frame).
            noteValidFrame("progress");
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
            if (isTier1Result(msg)) {
              noteValidFrame("tier1_complete");
              setTier1({ ...msg, patches: normalizePatches(msg.patches) });
              updateStatus("tier1_done");
            } else {
              failMalformedNonTerminal(
                "Received malformed tier1 result from server",
                "tier1_complete",
              );
            }
            break;

          case "proposals":
            if (isProposalArray(msg.proposals)) {
              noteValidFrame("proposals");
              setProposals(
                msg.proposals.map((p) => ({
                  ...p,
                  // Mirror backend _to_int clamping (>= 0): a hostile server
                  // must not inject negative lines/tiers into state.
                  // Validator still accepts finite negatives; clamp here so
                  // mixed-version degradation (null → 0) and hardening share
                  // one normalization path.
                  line_start: Math.max(
                    0,
                    typeof p.line_start === "number" &&
                      Number.isFinite(p.line_start)
                      ? p.line_start
                      : 0,
                  ),
                  line_end: Math.max(
                    0,
                    typeof p.line_end === "number" &&
                      Number.isFinite(p.line_end)
                      ? p.line_end
                      : 0,
                  ),
                  tier: Math.max(
                    0,
                    typeof p.tier === "number" && Number.isFinite(p.tier)
                      ? p.tier
                      : 0,
                  ),
                  confidence: Math.min(
                    1,
                    Math.max(
                      0,
                      typeof p.confidence === "number" &&
                        Number.isFinite(p.confidence)
                        ? p.confidence
                        : 0,
                    ),
                  ),
                  before_text:
                    typeof p.before_text === "string" ? p.before_text : "",
                  after_text:
                    typeof p.after_text === "string" ? p.after_text : "",
                  diff_hunk:
                    typeof p.diff_hunk === "string" ? p.diff_hunk : "",
                })),
              );
              updateStatus("awaiting_approval");
            } else {
              failMalformedNonTerminal(
                "Received malformed proposals from server",
                "proposals",
              );
            }
            break;

          case "approval_ack":
            // Two-gate interactive: do not treat ack as terminal; another
            // proposals event may follow. COMPLETE is driven by result.
            if (msg.status === "COMPLETE") {
              updateStatus("applying");
            }
            break;

          case "result":
            if (!isSessionResult(msg)) {
              failMalformedTerminal("Received malformed result from server");
              break;
            }
            setResult({ ...msg, patches: normalizePatches(msg.patches) });
            noteValidFrame("result");
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
            // Transport/server errors are not taint-tracked: neutralize any
            // stale malformed source so a later valid tainted-type frame
            // cannot clear an unrelated error.
            errorSourceRef.current = null;
            if (RECONNECTABLE_PHASES.has(statusRef.current) && sessionIdRef.current) {
              setCanReconnect(true);
              updateStatus("disconnected");
            } else {
              // A resume that fails before session_created points at a dead
              // id: drop the persisted key so reloads stop offering it.
              if (isPreSession()) {
                clearPersistedSession();
              }
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
        if (wsRef.current !== ws) return;
        errorSourceRef.current = null;
        if (RECONNECTABLE_PHASES.has(statusRef.current) && sessionIdRef.current) {
          setError("Connection lost. Your session is still active on the server.");
          setCanReconnect(true);
          updateStatus("disconnected");
        } else {
          // A resume that fails before session_created points at a dead id:
          // drop the persisted key so reloads stop offering resume to it.
          if (isPreSession()) {
            clearPersistedSession();
          }
          setError("WebSocket connection error");
          updateStatus("error");
        }
      };

      ws.onclose = (event) => {
        if (wsRef.current !== ws) return;
        // A close before session_created means the resume/start never
        // established: the persisted id is dead, drop it so reloads stop
        // re-offering resume to it.
        const preSession = isPreSession();
        if (preSession) {
          clearPersistedSession();
        }
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
            errorSourceRef.current = null;
            updateStatus("error");
          }
        } else if (event.code === 1000 && preSession) {
          // Clean close with no session_created and no other signal (e.g. a
          // resume the server rejected): surface it instead of hanging on a
          // spinner with resume material already dropped.
          setError("Connection closed unexpectedly");
          errorSourceRef.current = null;
          updateStatus("error");
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
      errorSourceRef.current = null;
      malformedTaintRef.current = new Set();
      setCanReconnect(false);
      updateStatus("connecting");
      // New socket: nothing established on it yet.
      sessionEstablishedRef.current = false;

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
        errorSourceRef.current = null;
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
