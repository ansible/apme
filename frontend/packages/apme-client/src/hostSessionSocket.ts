/** Authenticated HTTP channel carrying the existing Gateway WebSocket frames. */
import {
  apmeApiUrl,
  type ApmeApiAdapter,
  type ApmeSessionSocket,
} from "./transport.js";

const MAX_CONTENTION_RETRIES = 5;
const MAX_RETRY_AFTER_MS = 5_000;

/** Parse Retry-After values only when the requested delay fits the retry bound. */
function retryAfterMilliseconds(value: string | null): number | undefined {
  if (value?.trim()) {
    const seconds = Number(value);
    if (Number.isFinite(seconds) && seconds >= 0) {
      const delay = seconds * 1_000;
      return delay <= MAX_RETRY_AFTER_MS ? delay : undefined;
    }
    const date = Date.parse(value);
    if (!Number.isNaN(date)) {
      const delay = Math.max(0, date - Date.now());
      return delay <= MAX_RETRY_AFTER_MS ? delay : undefined;
    }
  }
  return 250;
}

/** Wait for contention to clear, while allowing socket closure to cancel the wait. */
function waitForRetry(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, delayMs);
    function abort() {
      clearTimeout(timer);
      reject(signal.reason ?? new DOMException("Aborted", "AbortError"));
    }
    signal.addEventListener("abort", abort, { once: true });
  });
}

/** Adapt the Gateway's HTTP upload channel to the session socket interface. */
export function createHostSessionSocket(
  adapter: ApmeApiAdapter,
  sessionUrl: string,
): ApmeSessionSocket {
  const requested = new URL(sessionUrl);
  const controller = new AbortController();
  let state = 0;
  let channel: string | undefined;
  let writes: Promise<void> = Promise.resolve();
  const base = apmeApiUrl("/upload-sessions", adapter);
  async function discard(response: Response) {
    await response.body?.cancel().catch(() => undefined);
  }
  async function deleteChannel(id: string) {
    const response = await adapter.fetch(`${base}/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    await discard(response);
  }
  const socket: ApmeSessionSocket = {
    get readyState() {
      return state;
    },
    onopen: null,
    onmessage: null,
    onerror: null,
    onclose: null,
    send(data) {
      if (state !== 1 || !channel)
        throw new Error("Upload channel is not open");
      const target = channel;
      writes = writes
        .then(async () => {
          if (state !== 1) return;
          for (let attempt = 0; ; attempt++) {
            const response = await adapter.fetch(
              `${base}/${encodeURIComponent(target)}/messages`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ data }),
                signal: controller.signal,
              },
            );
            if (response.status === 429 && attempt < MAX_CONTENTION_RETRIES) {
              const delay = retryAfterMilliseconds(
                response.headers.get("Retry-After"),
              );
              if (delay !== undefined) {
                await discard(response);
                await waitForRetry(delay, controller.signal);
                if (state !== 1) return;
                continue;
              }
            }
            try {
              if (!response.ok)
                throw new Error("Upload message rejected by host");
            } finally {
              await discard(response);
            }
            return;
          }
        })
        .catch((error: unknown) => {
          fail();
          throw error;
        });
      return writes;
    },
    close() {
      if (state === 3) return;
      state = 3;
      controller.abort();
      if (channel) void deleteChannel(channel).catch(() => undefined);
    },
  };
  function fail() {
    if (state === 3) return;
    socket.onerror?.(new Event("error"));
    socket.close();
    socket.onclose?.(new CloseEvent("close", { code: 1006 }));
  }
  async function connect() {
    const response = await adapter.fetch(base, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resume: requested.searchParams.get("resume"),
        scan_id: requested.searchParams.get("scan_id"),
      }),
    });
    let body: unknown;
    try {
      if (!response.ok) throw new Error("Upload session denied by host");
      body = await response.json();
    } finally {
      await discard(response);
    }
    if (
      !body ||
      typeof body !== "object" ||
      !("id" in body) ||
      typeof body.id !== "string"
    )
      throw new Error("Invalid upload channel");
    channel = body.id;
    if (state === 3) {
      void deleteChannel(channel).catch(() => undefined);
      return;
    }
    const events = await adapter.fetch(
      `${base}/${encodeURIComponent(channel)}/events`,
      { signal: controller.signal },
    );
    if (!events.ok || !events.body) {
      await discard(events);
      throw new Error("Upload stream denied by host");
    }
    const reader = events.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (state !== 3) {
        const next = await reader.read();
        if (next.done) break;
        buffer += decoder.decode(next.value, { stream: true });
        let boundary: number;
        while ((boundary = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 1);
          if (!line.trim()) continue;
          const frame = JSON.parse(line) as {
            event: string;
            data?: string;
            code?: number;
          };
          if (frame.event === "open") {
            state = 1;
            socket.onopen?.(new Event("open"));
          } else if (
            frame.event === "message" &&
            typeof frame.data === "string"
          )
            socket.onmessage?.(
              new MessageEvent("message", { data: frame.data }),
            );
          else if (frame.event === "close") {
            if (state !== 3) {
              socket.close();
              socket.onclose?.(
                new CloseEvent("close", { code: frame.code ?? 1000 }),
              );
            }
          } else throw new Error("Invalid upload stream frame");
        }
      }
      if (state !== 3) fail();
    } finally {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
  }
  void connect().catch(fail);
  return socket;
}
