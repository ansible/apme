/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { randomUUID } from "node:crypto";
import express, {
  type Request,
  type Response,
  type NextFunction,
} from "express";
import WebSocket from "ws";

type Frame = {
  event: "open" | "message" | "close";
  data?: string;
  code?: number;
};
interface Channel {
  owner: string;
  socket: WebSocket;
  pending: string[];
  pendingBytes: number;
  response?: Response;
  orphan: ReturnType<typeof setTimeout>;
}
export interface UploadRouterOptions {
  /** Must authenticate and authorize upload access on EVERY request. */
  authorize(request: Request): Promise<string>;
  /** Trusted host configuration; never take a target URL from the request. */
  gatewayBaseUrl(): Promise<string>;
  maxChannels?: number;
}

/** Ephemeral transport only. The Gateway retains all FixSession domain behavior. */
export function createUploadSessionRouter(options: UploadRouterOptions) {
  const router = express.Router();
  const channels = new Map<string, Channel>();
  const owners = new Map<
    string,
    { owner: string; scanId: string; expires: number }
  >();
  const maxChannels = options.maxChannels ?? 64;
  const maxPayload = 70 * 1024 * 1024;
  function dispose(id: string) {
    const channel = channels.get(id);
    if (!channel) return;
    channels.delete(id);
    clearTimeout(channel.orphan);
    channel.response?.end();
    channel.socket.close();
    channel.socket.terminate();
  }
  function emit(id: string, frame: Frame) {
    const channel = channels.get(id);
    if (!channel) return;
    const line = JSON.stringify(frame) + "\n";
    if (channel.response) {
      if (!channel.response.write(line)) channel.socket.pause();
    } else {
      channel.pendingBytes += Buffer.byteLength(line);
      if (channel.pendingBytes > 256 * 1024) {
        dispose(id);
        return;
      }
      channel.pending.push(line);
    }
  }
  function route(
    action: (req: Request, res: Response, owner: string) => Promise<void>,
  ) {
    return (req: Request, res: Response, next: NextFunction) => {
      void options
        .authorize(req)
        .then((owner) => action(req, res, owner))
        .catch(next);
    };
  }
  function owned(
    req: Request,
    res: Response,
    owner: string,
  ): Channel | undefined {
    const channel = channels.get(req.params.id);
    if (!channel || channel.owner !== owner) {
      res.status(404).json({ error: "Upload channel unavailable" });
      return;
    }
    return channel;
  }
  const parse = express.json({ limit: maxPayload });
  router.post(
    "/upload-sessions",
    parse,
    route(async (req, res, owner) => {
      for (const [id, binding] of owners)
        if (binding.expires < Date.now()) owners.delete(id);
      if (
        channels.size >= maxChannels ||
        [...channels.values()].filter((channel) => channel.owner === owner)
          .length >= 4
      ) {
        res.status(429).json({ error: "Too many upload sessions" });
        return;
      }
      const resume: unknown = req.body?.resume;
      if (
        resume != null &&
        (typeof resume !== "string" || owners.get(resume)?.owner !== owner)
      ) {
        res
          .status(404)
          .json({ error: "Upload session unavailable; start a new upload" });
        return;
      }
      const target = new URL(
        "/api/v1/ws/session",
        await options.gatewayBaseUrl(),
      );
      if (!["http:", "https:"].includes(target.protocol))
        throw new Error("Invalid Gateway protocol");
      target.protocol = target.protocol === "https:" ? "wss:" : "ws:";
      if (typeof resume === "string") {
        target.searchParams.set("resume", resume);
        target.searchParams.set("scan_id", owners.get(resume)!.scanId);
      }
      const id = randomUUID();
      const socket = new WebSocket(target, { maxPayload });
      const orphan = setTimeout(() => dispose(id), 30_000);
      orphan.unref();
      channels.set(id, { owner, socket, pending: [], pendingBytes: 0, orphan });
      socket.on("open", () => emit(id, { event: "open" }));
      socket.on("message", (data) => {
        const text = data.toString();
        try {
          const frame = JSON.parse(text) as {
            type?: string;
            session_id?: string;
            scan_id?: string;
            ttl_seconds?: number;
          };
          if (
            frame.type === "session_created" &&
            typeof frame.session_id === "string" &&
            typeof frame.scan_id === "string"
          ) {
            const existing = owners.get(frame.session_id);
            if (existing && existing.owner !== owner) {
              dispose(id);
              return;
            }
            if (owners.size >= 8192 && !owners.has(frame.session_id)) {
              dispose(id);
              return;
            }
            const seconds =
              typeof frame.ttl_seconds === "number" &&
              Number.isFinite(frame.ttl_seconds)
                ? Math.max(1, Math.min(frame.ttl_seconds, 86400))
                : 1800;
            owners.set(frame.session_id, {
              owner,
              scanId: frame.scan_id,
              expires: Date.now() + seconds * 1000,
            });
          }
        } catch {
          /* The existing client validates Gateway frames. */
        }
        emit(id, { event: "message", data: text });
      });
      socket.on("close", (code) => {
        emit(id, { event: "close", code });
        if (channels.get(id)?.response) dispose(id);
      });
      socket.on("error", () => {
        emit(id, { event: "close", code: 1006 });
        if (channels.get(id)?.response) dispose(id);
      });
      res.status(201).json({ id });
    }),
  );
  router.get(
    "/upload-sessions/:id/events",
    route(async (req, res, owner) => {
      const channel = owned(req, res, owner);
      if (!channel) return;
      if (channel.response) {
        res.status(409).end();
        return;
      }
      channel.response = res;
      clearTimeout(channel.orphan);
      res.setHeader("Content-Type", "application/x-ndjson");
      res.setHeader("Cache-Control", "no-store");
      res.setHeader("X-Accel-Buffering", "no");
      res.flushHeaders();
      res.on("drain", () => channel.socket.resume());
      for (const line of channel.pending)
        if (!res.write(line)) channel.socket.pause();
      channel.pending = [];
      channel.pendingBytes = 0;
      if (channel.socket.readyState === WebSocket.CLOSED) {
        dispose(req.params.id);
        return;
      }
      res.on("close", () => dispose(req.params.id));
    }),
  );
  router.post(
    "/upload-sessions/:id/messages",
    parse,
    route(async (req, res, owner) => {
      const channel = owned(req, res, owner);
      if (!channel) return;
      if (channel.socket.readyState !== WebSocket.OPEN) {
        res.status(409).end();
        return;
      }
      if (typeof req.body?.data !== "string") {
        res.status(400).end();
        return;
      }
      let frame: { type?: string };
      try {
        frame = JSON.parse(req.body.data);
      } catch {
        res.status(400).end();
        return;
      }
      if (
        !frame ||
        typeof frame !== "object" ||
        ![
          "start",
          "file",
          "files_done",
          "approve",
          "escalate_ai",
          "extend",
          "close",
        ].includes(frame.type ?? "")
      ) {
        res.status(400).end();
        return;
      }
      await new Promise<void>((resolve, reject) =>
        channel.socket.send(req.body.data, (error) =>
          error ? reject(error) : resolve(),
        ),
      );
      res.status(204).end();
    }),
  );
  router.delete(
    "/upload-sessions/:id",
    route(async (req, res, owner) => {
      if (!owned(req, res, owner)) return;
      dispose(req.params.id);
      res.status(204).end();
    }),
  );
  return {
    router,
    close() {
      for (const id of channels.keys()) dispose(id);
      owners.clear();
    },
  };
}
