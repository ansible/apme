import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSessionStream } from "./useSessionStream";

const STORAGE_KEY = "apme_active_session";

/** Minimal WebSocket stand-in capturing instances and sent frames. */
class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.OPEN;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  send = vi.fn();
  close = vi.fn((_code?: number) => {
    this.readyState = MockWebSocket.CLOSED;
  });

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
}

function lastSocket(): MockWebSocket {
  const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1];
  if (!ws) throw new Error("expected a WebSocket to have been created");
  return ws;
}

function sendMsg(ws: MockWebSocket, msg: unknown): void {
  act(() => {
    ws.onmessage?.({ data: JSON.stringify(msg) });
  });
}

async function startSessionWithSocket() {
  const { result, unmount } = renderHook(() => useSessionStream());
  await act(async () => {
    await result.current.startSession([], {});
  });
  const ws = lastSocket();
  act(() => {
    ws.onopen?.(new Event("open"));
  });
  return { result, unmount, ws };
}

function validTier1() {
  return {
    type: "tier1_complete",
    idempotency_ok: true,
    patches: [],
    format_diffs: [],
    report: null,
  };
}

function proposalBase() {
  return {
    id: "p1",
    file: "site.yml",
    rule_id: "R1",
    before_text: "before",
    after_text: "after",
    diff_hunk: "@@",
    confidence: 0.9,
    explanation: "fix",
    tier: 2,
  };
}

describe("useSessionStream hardening", () => {
  beforeEach(() => {
    sessionStorage.clear();
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    sessionStorage.clear();
  });

  it("accepts proposals omitting line_start and normalizes to 0", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      sendMsg(lastSocket(), {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(lastSocket(), validTier1());
      sendMsg(lastSocket(), {
        type: "proposals",
        proposals: [{ ...proposalBase(), line_end: 5 }],
      });

      expect(result.current.error).toBeNull();
      expect(result.current.status).toBe("awaiting_approval");
      expect(result.current.proposals).toHaveLength(1);
      expect(result.current.proposals[0]?.line_start).toBe(0);
      expect(result.current.proposals[0]?.line_end).toBe(5);
    } finally {
      unmount();
    }
  });

  it("accepts proposals omitting line_end and both line fields", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      sendMsg(lastSocket(), {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(lastSocket(), validTier1());
      sendMsg(lastSocket(), {
        type: "proposals",
        proposals: [
          { ...proposalBase(), line_start: 3 },
          { ...proposalBase(), id: "p2" },
        ],
      });

      expect(result.current.error).toBeNull();
      expect(result.current.proposals).toHaveLength(2);
      expect(result.current.proposals[0]?.line_end).toBe(0);
      expect(result.current.proposals[1]?.line_start).toBe(0);
      expect(result.current.proposals[1]?.line_end).toBe(0);
    } finally {
      unmount();
    }
  });

  it("malformed proposals preserves the persisted session and reconnect affordance", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, validTier1());
      expect(result.current.status).toBe("tier1_done");

      sendMsg(ws, { type: "proposals", proposals: [{ id: 123 }] });

      expect(result.current.error).toBe("Received malformed proposals from server");
      // Server session is alive: resume material must survive.
      expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull();
      expect(ws.close).not.toHaveBeenCalled();
      // tier1_done is reconnectable → reconnect affordance per phase logic.
      expect(result.current.canReconnect).toBe(true);
      expect(result.current.status).toBe("disconnected");
    } finally {
      unmount();
    }
  });

  it("malformed tier1_complete closes the socket but preserves the persisted session", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });

      sendMsg(ws, {
        type: "tier1_complete",
        idempotency_ok: "yes",
        patches: [],
        format_diffs: [],
        report: null,
      });

      expect(result.current.error).toBe(
        "Received malformed tier1 result from server",
      );
      expect(result.current.tier1).toBeNull();
      // Non-reconnectable ("checking"): the socket must close so a later
      // valid frame cannot flip error→complete behind a dead UI.
      expect(ws.close).toHaveBeenCalledWith(1000);
      // The server session is still alive: resume material must survive
      // (unlike the terminal teardown).
      expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull();
      // "checking" is not reconnectable → error without reconnect.
      expect(result.current.canReconnect).toBe(false);
      expect(result.current.status).toBe("error");
    } finally {
      unmount();
    }
  });

  it("malformed terminal result still tears down the session", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, validTier1());
      sendMsg(ws, {
        type: "proposals",
        proposals: [{ ...proposalBase(), line_start: 1, line_end: 2 }],
      });
      expect(result.current.status).toBe("awaiting_approval");

      sendMsg(ws, {
        type: "result",
        scan_id: 123,
        patches: [],
        report: null,
        remaining_violations: [],
      });

      expect(result.current.error).toBe("Received malformed result from server");
      expect(result.current.status).toBe("error");
      expect(result.current.canReconnect).toBe(false);
      expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
      expect(ws.close).toHaveBeenCalledWith(1000);
    } finally {
      unmount();
    }
  });

  it("rejects an array report on tier1_complete", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, {
        type: "tier1_complete",
        idempotency_ok: true,
        patches: [],
        format_diffs: [],
        report: [],
      });

      expect(result.current.tier1).toBeNull();
      expect(result.current.error).toBe(
        "Received malformed tier1 result from server",
      );
      // Non-terminal: resume material survives, but the non-reconnectable
      // socket still closes so the dead UI cannot flip to complete later.
      expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull();
      expect(ws.close).toHaveBeenCalledWith(1000);
    } finally {
      unmount();
    }
  });

  it("rejects an array report on terminal result with teardown", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, {
        type: "result",
        scan_id: "scan-1",
        patches: [],
        report: [],
        remaining_violations: [],
      });

      expect(result.current.result).toBeNull();
      expect(result.current.error).toBe("Received malformed result from server");
      expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
      expect(ws.close).toHaveBeenCalledWith(1000);
    } finally {
      unmount();
    }
  });

  it("treats session_created with empty ids as malformed without persisting", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, { type: "session_created", session_id: "", scan_id: "scan-1" });

      expect(result.current.error).toBe("Received malformed session from server");
      expect(result.current.sessionId).toBeNull();
      expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();

      sendMsg(ws, { type: "session_created", session_id: "sess-1", scan_id: "" });

      expect(result.current.sessionId).toBeNull();
      expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
    } finally {
      unmount();
    }
  });

  it("persists valid session_created ids", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      sendMsg(lastSocket(), {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });

      expect(result.current.sessionId).toBe("sess-1");
      expect(result.current.scanId).toBe("scan-1");
      const raw = sessionStorage.getItem(STORAGE_KEY);
      expect(raw).not.toBeNull();
      expect(JSON.parse(raw as string)).toMatchObject({
        sessionId: "sess-1",
        scanId: "scan-1",
      });
    } finally {
      unmount();
    }
  });

  it("accepts explicit-null line fields (Python None) and normalizes to 0", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      sendMsg(lastSocket(), {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(lastSocket(), validTier1());
      sendMsg(lastSocket(), {
        type: "proposals",
        proposals: [
          { ...proposalBase(), line_start: null, line_end: null },
          { ...proposalBase(), id: "p2", line_start: null, line_end: 7 },
        ],
      });

      expect(result.current.error).toBeNull();
      expect(result.current.status).toBe("awaiting_approval");
      expect(result.current.proposals).toHaveLength(2);
      expect(result.current.proposals[0]?.line_start).toBe(0);
      expect(result.current.proposals[0]?.line_end).toBe(0);
      expect(result.current.proposals[1]?.line_start).toBe(0);
      expect(result.current.proposals[1]?.line_end).toBe(7);
    } finally {
      unmount();
    }
  });

  it("accepts patches omitting applied_rules and normalizes to []", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      // Old servers omit applied_rules on tier1_complete patches.
      sendMsg(ws, {
        type: "tier1_complete",
        idempotency_ok: true,
        patches: [{ file: "site.yml", diff: "@@ -1 +1 @@" }],
        format_diffs: [],
        report: null,
      });

      expect(result.current.error).toBeNull();
      expect(result.current.status).toBe("tier1_done");
      expect(result.current.tier1?.patches).toHaveLength(1);
      expect(result.current.tier1?.patches[0]?.applied_rules).toEqual([]);

      sendMsg(ws, {
        type: "proposals",
        proposals: [{ ...proposalBase(), line_start: 1, line_end: 2 }],
      });
      // Old servers omit applied_rules on terminal result patches too.
      sendMsg(ws, {
        type: "result",
        scan_id: "scan-1",
        patches: [{ file: "site.yml", diff: "@@ -1 +1 @@" }],
        report: null,
        remaining_violations: [],
      });

      expect(result.current.error).toBeNull();
      expect(result.current.status).toBe("complete");
      expect(result.current.result?.patches).toHaveLength(1);
      expect(result.current.result?.patches[0]?.applied_rules).toEqual([]);
    } finally {
      unmount();
    }
  });

  it("malformed proposals clears a previously accepted stale set", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, validTier1());
      sendMsg(ws, {
        type: "proposals",
        proposals: [{ ...proposalBase(), line_start: 1, line_end: 2 }],
      });
      expect(result.current.status).toBe("awaiting_approval");
      expect(result.current.proposals).toHaveLength(1);

      sendMsg(ws, { type: "proposals", proposals: [{ id: 123 }] });

      // Approving the stale set must be impossible.
      expect(result.current.proposals).toHaveLength(0);
      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
      expect(ws.close).not.toHaveBeenCalled();
    } finally {
      unmount();
    }
  });

  it("ignores a consecutive same-type malformed frame after taint (no re-error)", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, validTier1());
      sendMsg(ws, {
        type: "proposals",
        proposals: [{ ...proposalBase(), line_start: 1, line_end: 2 }],
      });

      // First malformed frame: surfaces and clears the stale set.
      sendMsg(ws, { type: "proposals", proposals: [{ id: 123 }] });
      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
      expect(result.current.proposals).toHaveLength(0);

      // A consecutive same-type malformed frame is tainted: fully ignored —
      // no re-error, no extra close, socket kept open.
      sendMsg(ws, { type: "proposals", proposals: [{ id: 456 }] });
      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
      expect(result.current.proposals).toHaveLength(0);
      expect(result.current.status).toBe("disconnected");
      expect(ws.close).not.toHaveBeenCalled();
    } finally {
      unmount();
    }
  });

  it("routes JSON.parse failures into the malformed path without closing", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, validTier1());
      expect(result.current.status).toBe("tier1_done");

      act(() => {
        ws.onmessage?.({ data: "{not-json" });
      });

      expect(result.current.error).toBe(
        "Received malformed message from server",
      );
      // Non-terminal: resume material survives and the socket stays open.
      expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull();
      expect(ws.close).not.toHaveBeenCalled();
      expect(result.current.canReconnect).toBe(true);
      expect(result.current.status).toBe("disconnected");
    } finally {
      unmount();
    }
  });

  it("routes non-record JSON into the malformed path", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });

      act(() => {
        ws.onmessage?.({ data: "[1,2]" });
      });

      expect(result.current.error).toBe(
        "Received malformed message from server",
      );
      // Non-reconnectable ("checking"): the socket closes so a later valid
      // frame cannot flip error→complete, but resume material survives.
      expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull();
      expect(ws.close).toHaveBeenCalledWith(1000);
      expect(result.current.status).toBe("error");
    } finally {
      unmount();
    }
  });

  it("taint is per-type: a different malformed type still surfaces", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, validTier1());

      act(() => {
        ws.onmessage?.({ data: "{not-json" });
      });
      expect(result.current.error).toBe(
        "Received malformed message from server",
      );

      sendMsg(ws, { type: "proposals", proposals: [{ id: 123 }] });
      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
    } finally {
      unmount();
    }
  });

  it("valid proposals after taint clears the error and untaints", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, validTier1());
      sendMsg(ws, {
        type: "proposals",
        proposals: [{ ...proposalBase(), line_start: 1, line_end: 2 }],
      });
      expect(result.current.status).toBe("awaiting_approval");

      // Malformed frame: surfaces, taints, clears the stale set.
      sendMsg(ws, { type: "proposals", proposals: [{ id: 123 }] });
      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
      expect(result.current.proposals).toHaveLength(0);

      // Valid recovery: clears the error and drops the taint.
      sendMsg(ws, {
        type: "proposals",
        proposals: [{ ...proposalBase(), line_start: 1, line_end: 2 }],
      });
      expect(result.current.error).toBeNull();
      expect(result.current.status).toBe("awaiting_approval");
      expect(result.current.proposals).toHaveLength(1);

      // Taint was dropped: a repeat malformed frame surfaces again and
      // clears the recovered set instead of being ignored forever.
      sendMsg(ws, { type: "proposals", proposals: [{ id: 456 }] });
      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
      expect(result.current.proposals).toHaveLength(0);
      expect(result.current.status).toBe("disconnected");
    } finally {
      unmount();
    }
  });

  it("normalizes explicit-null text fields (Python None) to empty strings", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      sendMsg(lastSocket(), {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(lastSocket(), validTier1());
      sendMsg(lastSocket(), {
        type: "proposals",
        proposals: [
          {
            ...proposalBase(),
            before_text: null,
            after_text: null,
            diff_hunk: null,
          },
        ],
      });

      expect(result.current.error).toBeNull();
      expect(result.current.status).toBe("awaiting_approval");
      expect(result.current.proposals).toHaveLength(1);
      expect(result.current.proposals[0]?.before_text).toBe("");
      expect(result.current.proposals[0]?.after_text).toBe("");
      expect(result.current.proposals[0]?.diff_hunk).toBe("");
    } finally {
      unmount();
    }
  });

  it("normalizes explicit-null tier/confidence to 0", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      sendMsg(lastSocket(), {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(lastSocket(), validTier1());
      sendMsg(lastSocket(), {
        type: "proposals",
        proposals: [{ ...proposalBase(), tier: null, confidence: null }],
      });

      expect(result.current.error).toBeNull();
      expect(result.current.status).toBe("awaiting_approval");
      expect(result.current.proposals).toHaveLength(1);
      expect(result.current.proposals[0]?.tier).toBe(0);
      expect(result.current.proposals[0]?.confidence).toBe(0);
    } finally {
      unmount();
    }
  });

  it("rejects NaN line_start as malformed", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, validTier1());
      // JSON cannot encode NaN, so bypass the stringify helper: the guard
      // is defense-in-depth for non-JSON producers reaching the validator.
      const spy = vi.spyOn(JSON, "parse").mockReturnValueOnce({
        type: "proposals",
        proposals: [{ ...proposalBase(), line_start: NaN, line_end: 2 }],
      });
      try {
        act(() => {
          ws.onmessage?.({ data: "ignored" });
        });
      } finally {
        spy.mockRestore();
      }

      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
      expect(result.current.proposals).toHaveLength(0);
      // tier1_done is reconnectable → reconnect affordance, socket open.
      expect(result.current.status).toBe("disconnected");
      expect(ws.close).not.toHaveBeenCalled();
    } finally {
      unmount();
    }
  });

  it("rejects NaN tier as malformed", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, validTier1());
      const spy = vi.spyOn(JSON, "parse").mockReturnValueOnce({
        type: "proposals",
        proposals: [{ ...proposalBase(), tier: NaN }],
      });
      try {
        act(() => {
          ws.onmessage?.({ data: "ignored" });
        });
      } finally {
        spy.mockRestore();
      }

      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
      expect(result.current.proposals).toHaveLength(0);
      expect(result.current.status).toBe("disconnected");
      expect(ws.close).not.toHaveBeenCalled();
    } finally {
      unmount();
    }
  });

  it("non-reconnectable malformed proposals closes the socket but preserves the session", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      // Still "checking" (non-reconnectable): no tier1_complete yet.
      sendMsg(ws, { type: "proposals", proposals: [{ id: 123 }] });

      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
      expect(result.current.status).toBe("error");
      expect(result.current.canReconnect).toBe(false);
      expect(ws.close).toHaveBeenCalledWith(1000);
      // The server session is still alive: resume material must survive
      // (unlike the terminal teardown).
      expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull();
    } finally {
      unmount();
    }
  });

  function seedPersistedSession(): void {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        sessionId: "dead-sess",
        scanId: "scan-9",
        timestamp: Date.now(),
        ttlSeconds: 1800,
      }),
    );
  }

  async function resumeToChecking() {
    const hook = renderHook(() => useSessionStream());
    act(() => {
      hook.result.current.resumeSession("dead-sess", "scan-9");
    });
    const ws = lastSocket();
    expect(ws.url).toContain("resume=dead-sess");
    act(() => {
      ws.onopen?.(new Event("open"));
    });
    expect(hook.result.current.status).toBe("checking");
    return { hook, ws };
  }

  it("clamps negative proposal numerics to 0 (hostile server)", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      sendMsg(lastSocket(), {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(lastSocket(), validTier1());
      sendMsg(lastSocket(), {
        type: "proposals",
        proposals: [
          {
            ...proposalBase(),
            line_start: -5,
            line_end: -1,
            tier: -2,
            confidence: -0.5,
          },
          {
            ...proposalBase(),
            id: "p2",
            line_start: 3,
            line_end: 7,
            tier: 1,
            confidence: 0.9,
          },
        ],
      });

      expect(result.current.error).toBeNull();
      expect(result.current.status).toBe("awaiting_approval");
      expect(result.current.proposals).toHaveLength(2);
      expect(result.current.proposals[0]?.line_start).toBe(0);
      expect(result.current.proposals[0]?.line_end).toBe(0);
      expect(result.current.proposals[0]?.tier).toBe(0);
      expect(result.current.proposals[0]?.confidence).toBe(0);
      // Non-negative values pass through untouched.
      expect(result.current.proposals[1]?.line_start).toBe(3);
      expect(result.current.proposals[1]?.line_end).toBe(7);
      expect(result.current.proposals[1]?.tier).toBe(1);
      expect(result.current.proposals[1]?.confidence).toBe(0.9);
    } finally {
      unmount();
    }
  });

  it("valid progress clears a transient JSON-parse error and untaints", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, validTier1());

      act(() => {
        ws.onmessage?.({ data: "{not-json" });
      });
      expect(result.current.error).toBe(
        "Received malformed message from server",
      );

      // Progress is the most frequent frame: it proves transport recovery
      // and must drop the parse-failure taint even though progress has no
      // typed validator.
      sendMsg(ws, { type: "progress", phase: "scan", message: "working" });
      expect(result.current.error).toBeNull();
      expect(result.current.progress).toHaveLength(1);

      // Taint was dropped: a repeat parse failure surfaces again instead of
      // being ignored forever.
      act(() => {
        ws.onmessage?.({ data: "{not-json" });
      });
      expect(result.current.error).toBe(
        "Received malformed message from server",
      );
    } finally {
      unmount();
    }
  });

  it("valid progress does not clear a typed proposals error", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, validTier1());
      sendMsg(ws, {
        type: "proposals",
        proposals: [{ ...proposalBase(), line_start: 1, line_end: 2 }],
      });
      expect(result.current.status).toBe("awaiting_approval");

      sendMsg(ws, { type: "proposals", proposals: [{ id: 123 }] });
      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
      expect(result.current.proposals).toHaveLength(0);

      // Progress clears only the parse-failure taint: the typed proposals
      // error and taint must survive.
      sendMsg(ws, { type: "progress", phase: "scan", message: "working" });
      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
      expect(result.current.proposals).toHaveLength(0);

      // Taint survived: a repeat malformed proposals frame is still ignored.
      sendMsg(ws, { type: "proposals", proposals: [{ id: 456 }] });
      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
      expect(result.current.proposals).toHaveLength(0);
    } finally {
      unmount();
    }
  });

  it("resume socket error before session_created clears the persisted session", async () => {
    seedPersistedSession();
    const { hook, ws } = await resumeToChecking();
    try {
      act(() => {
        ws.onerror?.();
      });

      expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
      expect(hook.result.current.status).toBe("error");
      expect(hook.result.current.error).toBe("WebSocket connection error");
    } finally {
      hook.unmount();
    }
  });

  it("resume error event before session_created clears the persisted session", async () => {
    seedPersistedSession();
    const { hook, ws } = await resumeToChecking();
    try {
      sendMsg(ws, { type: "error", message: "session not found" });

      expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
      expect(hook.result.current.status).toBe("error");
      expect(hook.result.current.error).toBe("session not found");
    } finally {
      hook.unmount();
    }
  });

  it("resume clean close before session_created clears storage and surfaces", async () => {
    seedPersistedSession();
    const { hook, ws } = await resumeToChecking();
    try {
      act(() => {
        ws.onclose?.({ code: 1000 });
      });

      expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
      expect(hook.result.current.status).toBe("error");
      expect(hook.result.current.error).toBe(
        "Connection closed unexpectedly",
      );
    } finally {
      hook.unmount();
    }
  });

  it("resume clears malformed taint so repeat frames surface again", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const ws = lastSocket();
      sendMsg(ws, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(ws, validTier1());
      sendMsg(ws, {
        type: "proposals",
        proposals: [{ ...proposalBase(), line_start: 1, line_end: 2 }],
      });
      expect(result.current.status).toBe("awaiting_approval");

      sendMsg(ws, { type: "proposals", proposals: [{ id: 123 }] });
      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );

      // Second malformed frame is tainted and ignored.
      sendMsg(ws, { type: "proposals", proposals: [{ id: 456 }] });
      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );

      act(() => {
        result.current.resumeSession("sess-1", "scan-1");
      });
      const resumeWs = lastSocket();
      expect(resumeWs).not.toBe(ws);
      act(() => {
        resumeWs.onopen?.(new Event("open"));
      });
      expect(result.current.error).toBeNull();

      sendMsg(resumeWs, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(resumeWs, validTier1());

      // Taint was cleared on resume: malformed proposals surface again.
      sendMsg(resumeWs, { type: "proposals", proposals: [{ id: 789 }] });
      expect(result.current.error).toBe(
        "Received malformed proposals from server",
      );
    } finally {
      unmount();
    }
  });

  it("stale socket onclose after resume does not clear storage or set error", async () => {
    const { result, unmount } = await startSessionWithSocket();
    try {
      const oldWs = lastSocket();
      sendMsg(oldWs, {
        type: "session_created",
        session_id: "sess-1",
        scan_id: "scan-1",
      });
      sendMsg(oldWs, validTier1());
      sendMsg(oldWs, {
        type: "proposals",
        proposals: [{ ...proposalBase(), line_start: 1, line_end: 2 }],
      });
      expect(result.current.status).toBe("awaiting_approval");
      expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull();

      act(() => {
        result.current.resumeSession("sess-1", "scan-1");
      });
      const newWs = lastSocket();
      expect(newWs).not.toBe(oldWs);
      act(() => {
        newWs.onopen?.(new Event("open"));
      });
      expect(result.current.status).toBe("checking");
      expect(result.current.error).toBeNull();

      // Old socket close fires after replacement: must not affect new connection.
      act(() => {
        oldWs.onclose?.({ code: 1006 });
      });

      expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull();
      expect(result.current.error).toBeNull();
      expect(result.current.status).toBe("checking");
    } finally {
      unmount();
    }
  });
});
