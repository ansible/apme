/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import type { IncomingHttpHeaders } from "node:http";
import type { LoggerService } from "@backstage/backend-plugin-api";
import { getApmeConfig } from "../common/index";
import type { Config } from "@backstage/config";
import { InputError } from "@backstage/errors";

/**
 * Minimal request/response shapes so we do not depend on a single
 * `@types/express` revision (PromiseRouter params vs root `Request` often
 * disagree on deprecated `param` / `sendfile`).
 */
export type OperationProxyRequest = {
  method: string;
  url: string;
  headers: IncomingHttpHeaders;
  body?: unknown;
  on?: (event: "aborted", listener: () => void) => unknown;
  off?: (event: "aborted", listener: () => void) => unknown;
};

export type OperationProxyResponse = {
  status(code: number): OperationProxyResponse;
  setHeader(name: string, value: string): void;
  write(chunk: string | Buffer): unknown;
  end(): void;
  once?: (event: "drain", listener: () => void) => unknown;
  on?: (event: "close", listener: () => void) => unknown;
  off?: (event: "close" | "drain", listener: () => void) => unknown;
  writableEnded?: boolean;
  destroyed?: boolean;
  /** Present when compression middleware is installed. */
  flushHeaders?: () => void;
  flush?: () => void;
};

function assertSafeOperationSuffix(suffix: string): void {
  for (const segment of suffix.split("/")) {
    let decoded: string;
    try {
      decoded = segment;
      for (let pass = 0; pass < 4; pass += 1) {
        const next = decodeURIComponent(decoded);
        if (next === decoded) break;
        decoded = next;
      }
    } catch {
      throw new InputError("Invalid operation path");
    }
    if (
      decoded === "." ||
      decoded === ".." ||
      decoded.includes("/") ||
      decoded.includes("\\") ||
      /%(?:2e|2f|5c)/i.test(decoded)
    ) {
      throw new InputError("Invalid operation path");
    }
  }
}

/**
 * Transparent proxy of `/apme/projects/:projectId/operation*` to Gateway
 * `/api/v1/projects/:projectId/operation*` for `@apme/ui-workflow`.
 *
 * Injects `scm_token` into JSON bodies when the portal resolved one, so
 * Gateway can own commit/push (ADR-056). Never forwards `file_overrides`.
 */
export async function proxyProjectOperation(options: {
  req: OperationProxyRequest;
  res: OperationProxyResponse;
  rootConfig: Config;
  logger: LoggerService;
  projectId: string;
  /** Path after `/operation` (e.g. ``, `/approve`, `/events`). */
  operationSuffix: string;
  scmToken?: string;
  /** Effective Gateway URL (portal-settings override, else app-config). */
  gatewayBaseUrl?: string;
}): Promise<void> {
  const {
    req,
    res,
    rootConfig,
    logger,
    projectId,
    operationSuffix,
    scmToken,
    gatewayBaseUrl,
  } = options;
  const baseUrl = (gatewayBaseUrl ?? getApmeConfig(rootConfig).baseUrl).replace(
    /\/$/,
    "",
  );
  assertSafeOperationSuffix(operationSuffix);
  const targetPath = `/api/v1/projects/${encodeURIComponent(
    projectId,
  )}/operation${operationSuffix}`;
  const qs = req.url.includes("?") ? req.url.slice(req.url.indexOf("?")) : "";
  const targetUrl = `${baseUrl.replace(/\/$/, "")}${targetPath}${qs}`;

  const headers: Record<string, string> = {
    Accept: req.headers.accept?.toString() || "application/json",
  };
  if (req.headers["content-type"]) {
    headers["Content-Type"] = String(req.headers["content-type"]);
  }
  if (scmToken) {
    headers["X-SCM-Token"] = scmToken;
  }

  let body: string | undefined;
  if (req.method !== "GET" && req.method !== "HEAD") {
    const incoming =
      req.body && typeof req.body === "object" ? { ...req.body } : req.body;
    if (incoming && typeof incoming === "object") {
      delete (incoming as { file_overrides?: unknown }).file_overrides;
      if (scmToken && !(incoming as { scm_token?: string }).scm_token) {
        (incoming as { scm_token?: string }).scm_token = scmToken;
      }
      body = JSON.stringify(incoming);
      headers["Content-Type"] = "application/json";
    } else if (typeof incoming === "string") {
      body = incoming;
    }
  }

  logger.debug(`APME operation proxy ${req.method} ${targetUrl}`);

  const controller = new AbortController();
  const onRequestAborted = () => controller.abort();
  const onResponseClosed = () => {
    if (!res.writableEnded) controller.abort();
  };
  req.on?.("aborted", onRequestAborted);
  res.on?.("close", onResponseClosed);

  let upstream: Response;
  try {
    upstream = await fetch(targetUrl, {
      method: req.method,
      headers,
      body,
      signal: controller.signal,
    });
  } catch (error) {
    req.off?.("aborted", onRequestAborted);
    res.off?.("close", onResponseClosed);
    throw error;
  }

  res.status(upstream.status);
  const contentType = upstream.headers.get("content-type");
  if (contentType) {
    res.setHeader("Content-Type", contentType);
  }

  // SSE / streaming — must flush each chunk. Without flushHeaders/flush,
  // compression (and some proxies) buffer until the Gateway closes the
  // stream, so Portal UI hangs on "Cloning…" until remount re-polls GET
  // /operation (tab switch). Gateway already sets X-Accel-Buffering: no.
  const isEventStream = Boolean(contentType?.includes("text/event-stream"));
  if (isEventStream) {
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("X-Accel-Buffering", "no");
    res.flushHeaders?.();
    if (!upstream.body) {
      res.end();
      return;
    }
  }
  const reader = upstream.body?.getReader();
  if (!reader) {
    res.end();
    req.off?.("aborted", onRequestAborted);
    res.off?.("close", onResponseClosed);
    return;
  }
  const cancelReader = () => {
    void reader.cancel().catch(() => undefined);
  };
  controller.signal.addEventListener("abort", cancelReader, { once: true });
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value?.byteLength) continue;
      const canContinue = res.write(Buffer.from(value));
      if (isEventStream) res.flush?.();
      if (!canContinue && res.once) {
        const drained = await new Promise<boolean>((resolve) => {
          let settled = false;
          const finish = (value: boolean) => {
            if (settled) return;
            settled = true;
            res.off?.("drain", onDrain);
            controller.signal.removeEventListener("abort", onAbort);
            resolve(value);
          };
          const onDrain = () => finish(true);
          const onAbort = () => finish(false);
          res.once?.("drain", onDrain);
          controller.signal.addEventListener("abort", onAbort, {
            once: true,
          });
          if (controller.signal.aborted) onAbort();
        });
        if (!drained) break;
      }
    }
  } catch (error) {
    if (!controller.signal.aborted) throw error;
  } finally {
    controller.signal.removeEventListener("abort", cancelReader);
    req.off?.("aborted", onRequestAborted);
    res.off?.("close", onResponseClosed);
    if (!res.writableEnded && !res.destroyed) res.end();
  }
}
