// @vitest-environment node
import express from "express";
import { createServer, request as httpRequest, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { WebSocketServer } from "ws";
import { createUploadSessionRouter } from "../plugins/backstage-apme-backend/src/uploadRouter";
import {
  createDefaultApmeApiAdapter,
  createHostSessionSocket,
} from "@apme/client";

let gateway: Server;
let host: Server;
let sockets: WebSocketServer;
let transport: ReturnType<typeof createUploadSessionRouter>;
let base: string;
let targets: string[];
let authorizations: number;
let gatewayBaseUrl: () => Promise<string>;

beforeEach(async () => {
  targets = [];
  authorizations = 0;
  gateway = createServer();
  sockets = new WebSocketServer({ server: gateway });
  sockets.on("connection", (socket, req) => {
    targets.push(req.url!);
    socket.send(
      JSON.stringify({
        type: "session_created",
        session_id: "owned-session",
        scan_id: "owned-scan",
        ttl_seconds: 60,
      }),
    );
    socket.on("message", (data) => socket.send(data));
  });
  await new Promise<void>((resolve) => gateway.listen(0, "127.0.0.1", resolve));
  gatewayBaseUrl = async () =>
    `http://127.0.0.1:${(gateway.address() as AddressInfo).port}`;
  transport = createUploadSessionRouter({
    maxRequestsPerMinute: 20,
    authorize: async (req) => {
      authorizations++;
      if (!req.headers["x-user"]) throw new Error("Denied");
      return String(req.headers["x-user"]);
    },
    gatewayBaseUrl: () => gatewayBaseUrl(),
  });
  const app = express();
  app.use(transport.router);
  app.use(
    (
      error: { status?: number },
      _req: express.Request,
      res: express.Response,
      _next: express.NextFunction,
    ) => {
      res.status(error.status ?? 403).end();
    },
  );
  host = app.listen(0, "127.0.0.1");
  await new Promise<void>((resolve) => host.once("listening", resolve));
  base = `http://127.0.0.1:${(host.address() as AddressInfo).port}/upload-sessions`;
});

it.each(["", "/unknown/messages"])(
  "authorizes %s before waiting for the request body",
  async (path) => {
    const status = await new Promise<number>((resolve, reject) => {
      const req = httpRequest(
        base + path,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Content-Length": 1024 * 1024,
          },
          timeout: 2000,
        },
        (res) => {
          res.resume();
          resolve(res.statusCode!);
          req.destroy();
        },
      );
      req.on("error", reject);
      req.on("timeout", () =>
        req.destroy(new Error("Host waited for an unauthorized body")),
      );
      req.flushHeaders();
    });
    expect(status).toBe(403);
    expect(targets).toEqual([]);
  },
);

it("forwards JSON parse errors and uses a small creation limit", async () => {
  for (const [body, status] of [
    ["{", 400],
    [JSON.stringify({ resume: "x".repeat(9000) }), 413],
  ] as const) {
    const response = await fetch(base, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-user": "alice" },
      body,
    });
    expect(response.status).toBe(status);
    await response.body?.cancel();
  }
  expect(targets).toEqual([]);
});

it("rate-limits all upload routes before invoking authorization", async () => {
  for (let index = 0; index < 20; index++) {
    const response = await fetch(base + "/missing/events");
    expect(response.status).toBe(403);
    await response.body?.cancel();
  }
  for (const [path, method] of [
    ["", "POST"],
    ["/missing/events", "GET"],
    ["/missing/messages", "POST"],
    ["/missing", "DELETE"],
  ]) {
    const response = await fetch(base + path, { method });
    expect(response.status).toBe(429);
    expect(response.headers.get("Retry-After")).toBeTruthy();
    await response.body?.cancel();
  }
  expect(authorizations).toBe(20);
});
afterEach(async () => {
  transport.close();
  sockets.clients.forEach((socket) => socket.terminate());
  await Promise.all([
    new Promise<void>((resolve) => host.close(() => resolve())),
    new Promise<void>((resolve) => gateway.close(() => resolve())),
  ]);
  sockets.close();
});
async function create(user = "alice", body = {}) {
  const response = await fetch(base, {
    method: "POST",
    headers: { "x-user": user, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return {
    response,
    body:
      response.status === 201
        ? ((await response.json()) as { id: string })
        : undefined,
  };
}

it("denies unauthenticated creation without opening a Gateway socket", async () => {
  const response = await fetch(base, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  expect(response.status).toBe(403);
  expect(targets).toEqual([]);
});
it("checks ownership for events, messages and deletion", async () => {
  const { body } = await create();
  for (const [path, method] of [
    [`/${body!.id}/events`, "GET"],
    [`/${body!.id}/messages`, "POST"],
    [`/${body!.id}`, "DELETE"],
  ]) {
    const response = await fetch(base + path, {
      method,
      headers: { "x-user": "bob", "Content-Type": "application/json" },
      ...(method === "POST" ? { body: "{}" } : {}),
    });
    expect(response.status).toBe(404);
  }
});
it("streams Gateway frames and binds resume to the original owner and scan", async () => {
  const { body } = await create();
  const response = await fetch(`${base}/${body!.id}/events`, {
    headers: { "x-user": "alice" },
  });
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let text = "";
  while (!text.includes("owned-session")) {
    const chunk = await reader.read();
    text += decoder.decode(chunk.value);
  }
  expect(text).toContain('"event":"open"');
  const bad = await create("bob", { resume: "owned-session" });
  expect(bad.response.status).toBe(404);
  const resumed = await create("alice", {
    resume: "owned-session",
    scan_id: "forged-scan",
  });
  expect(resumed.response.status).toBe(201);
  await fetch(`${base}/${body!.id}`, {
    method: "DELETE",
    headers: { "x-user": "alice" },
  });
  await reader.cancel();
  // Opening the event stream waits for the resumed upstream connection.
  const events = await fetch(`${base}/${resumed.body!.id}/events`, {
    headers: { "x-user": "alice" },
  });
  const resumedReader = events.body!.getReader();
  await resumedReader.read();
  expect(targets[targets.length - 1]).toBe(
    "/api/v1/ws/session?resume=owned-session&scan_id=owned-scan",
  );
  await fetch(`${base}/${resumed.body!.id}`, {
    method: "DELETE",
    headers: { "x-user": "alice" },
  });
  await resumedReader.cancel();
});
it("fails closed for unknown resume bindings", async () => {
  const result = await create("alice", { resume: "unknown" });
  expect(result.response.status).toBe(404);
  expect(targets).toEqual([]);
});

it("carries the existing session protocol through authenticated fetch in both directions", async () => {
  const adapter = createDefaultApmeApiAdapter({
    apiBase: base.replace("/upload-sessions", ""),
    fetch: async (input, init) =>
      fetch(input, {
        ...init,
        headers: {
          ...Object.fromEntries(new Headers(init?.headers)),
          "x-user": "alice",
        },
      }),
  });
  const socket = createHostSessionSocket(
    adapter,
    "ws://unused.test/ws/session",
  );
  try {
    await new Promise<void>((resolve, reject) => {
      socket.onerror = () => reject(new Error("Channel failed"));
      socket.onopen = () => {
        void socket.send(
          JSON.stringify({ type: "start", options: { enable_ai: false } }),
        );
      };
      socket.onmessage = (event) => {
        if (JSON.parse(event.data).type === "start") resolve();
      };
    });
    expect(socket.readyState).toBe(1);
  } finally {
    socket.close();
  }
});

it("enforces channel quotas when concurrent creates await host settings", async () => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let lookups = 0;
  gatewayBaseUrl = async () => {
    lookups++;
    await pending;
    return `http://127.0.0.1:${(gateway.address() as AddressInfo).port}`;
  };
  const requests = Array.from({ length: 6 }, () => create("alice"));
  try {
    await vi.waitFor(() => expect(lookups).toBe(6));
    release();
    const results = await Promise.all(requests);
    expect(
      results.filter(({ response }) => response.status === 201),
    ).toHaveLength(4);
    expect(
      results.filter(({ response }) => response.status === 429),
    ).toHaveLength(2);
    await Promise.all(
      results
        .filter(({ response }) => !response.bodyUsed)
        .map(({ response }) => response.body?.cancel()),
    );
    await vi.waitFor(() => expect(targets).toHaveLength(4));
  } finally {
    release();
  }
});
