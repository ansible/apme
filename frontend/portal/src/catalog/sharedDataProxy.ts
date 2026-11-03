/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { Readable, Writable } from "node:stream";
import type { ReadableStream } from "node:stream/web";
import { pipeline } from "node:stream/promises";

interface DataProxyResponse extends Writable {
  status(code: number): DataProxyResponse;
  json(body: unknown): unknown;
  setHeader(name: string, value: string): unknown;
}

/** Called only after host authorization, with a fixed route-derived API path. */
export async function proxySharedData(options: {
  gatewayBaseUrl: string;
  path: string;
  query?: string;
  response: DataProxyResponse;
  verifyProjectId?: string;
}) {
  const {
    gatewayBaseUrl,
    path,
    query = "",
    response,
    verifyProjectId,
  } = options;
  const controller = new AbortController();
  response.setHeader("Cache-Control", "no-store");
  const timeout = setTimeout(() => controller.abort(), 60_000);
  const disconnect = () => controller.abort();
  response.on("close", disconnect);
  try {
    const upstream = await fetch(
      `${gatewayBaseUrl.replace(/\/$/, "")}/api/v1${path}${query}`,
      { signal: controller.signal },
    );
    if (verifyProjectId && upstream.ok) {
      const body = (await upstream.json()) as { project_id?: string };
      if (body.project_id !== verifyProjectId) {
        response
          .status(404)
          .json({ error: "Activity not found in this project" });
        return;
      }
      response.status(upstream.status).json(body);
      return;
    }
    response.status(upstream.status);
    const contentType = upstream.headers.get("content-type");
    if (contentType) response.setHeader("Content-Type", contentType);
    response.setHeader("Cache-Control", "no-store");
    if (!upstream.body) {
      response.end();
      return;
    }
    await pipeline(
      Readable.fromWeb(upstream.body as unknown as ReadableStream),
      response,
    );
  } finally {
    clearTimeout(timeout);
    response.off("close", disconnect);
  }
}
