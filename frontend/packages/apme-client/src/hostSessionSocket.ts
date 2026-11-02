/** Authenticated HTTP channel carrying the existing Gateway WebSocket frames. */
import {
  apmeApiUrl,
  type ApmeApiAdapter,
  type ApmeSessionSocket,
} from "./transport.js";

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
          const response = await adapter.fetch(
            `${base}/${encodeURIComponent(target)}/messages`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ data }),
              signal: controller.signal,
            },
          );
          try {
            if (!response.ok)
              throw new Error("Upload message rejected by host");
          } finally {
            await discard(response);
          }
        })
        .catch(fail);
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
      signal: controller.signal,
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
            state = 3;
            socket.onclose?.(
              new CloseEvent("close", { code: frame.code ?? 1000 }),
            );
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
