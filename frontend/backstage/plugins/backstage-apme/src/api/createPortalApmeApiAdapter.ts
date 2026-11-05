/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import type { DiscoveryApi, FetchApi } from "@backstage/core-plugin-api";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { createApmeUiWorkflowAdapter } from "./createApmeUiWorkflowAdapter";

/**
 * Project views consume Gateway-shaped lists. The current Portal catalog routes
 * strip pagination and return a bounded array/items envelope. Normalize those
 * explicit routes locally without changing operation/SSE responses or auth.
 */
export async function createPortalApmeApiAdapter(options: {
  discoveryApi: DiscoveryApi;
  fetchApi: FetchApi;
}): Promise<ApmeApiAdapter> {
  const adapter = await createApmeUiWorkflowAdapter(options);
  const base = new URL(adapter.apiBase, adapter.origin || "http://localhost");
  const prefix = base.pathname.replace(/\/$/, "");
  return {
    ...adapter,
    async fetch(input, init) {
      const response = await adapter.fetch(input, init);
      const request = input instanceof Request ? input : undefined;
      const url = new URL(request?.url ?? String(input), base);
      const method = (init?.method ?? request?.method ?? "GET").toUpperCase();
      const path = url.pathname.slice(prefix.length);
      const listRoute =
        path === "/projects" || /^\/projects\/[^/]+\/activity$/.test(path);
      const rulesRoute = path === "/rules";
      if (
        method !== "GET" ||
        !response.ok ||
        response.status === 204 ||
        url.origin !== base.origin ||
        !url.pathname.startsWith(`${prefix}/`) ||
        (!listRoute && !rulesRoute)
      ) {
        return response;
      }
      const body: unknown = await response.clone().json();
      if (rulesRoute) {
        const rows = Array.isArray(body)
          ? body
          : body &&
              typeof body === "object" &&
              "items" in body &&
              Array.isArray(body.items)
            ? body.items
            : undefined;
        if (!rows) throw new Error("Portal returned an invalid rule catalog");
        return jsonResponse(response, rows.map(normalizePortalRule));
      }
      if (
        !Array.isArray(body) &&
        body &&
        typeof body === "object" &&
        "total" in body &&
        typeof body.total === "number"
      )
        return response;
      const items = Array.isArray(body)
        ? body
        : body &&
            typeof body === "object" &&
            "items" in body &&
            Array.isArray(body.items)
          ? body.items
          : undefined;
      if (!items)
        throw new Error("Portal returned an invalid repository/history list");
      const limit = Number(url.searchParams.get("limit") ?? 50);
      const offset = Number(url.searchParams.get("offset") ?? 0);
      if (
        !Number.isInteger(limit) ||
        limit < 1 ||
        !Number.isInteger(offset) ||
        offset < 0
      ) {
        throw new Error("Invalid repository/history pagination");
      }
      return jsonResponse(response, {
        items: items.slice(offset, offset + limit),
        total: items.length,
        total_is_exact: false,
        limit,
        offset,
      });
    },
  };
}

function jsonResponse(response: Response, body: unknown) {
  const headers = new Headers(response.headers);
  headers.set("Content-Type", "application/json");
  headers.delete("Content-Length");
  headers.delete("Content-Encoding");
  headers.delete("ETag");
  return new Response(JSON.stringify(body), {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

/** Portal's existing catalog contract loses proto severity, scope and timestamp.
 * Keep those unspecified; critical may represent Gateway ERROR or CRITICAL.
 * The view displays the label and writes only the explicit user-selected field.
 */
function normalizePortalRule(row: unknown) {
  if (!row || typeof row !== "object")
    throw new Error("Portal returned an invalid rule");
  if ("rule_id" in row && typeof row.rule_id === "string") return row;
  if (
    !("id" in row) ||
    typeof row.id !== "string" ||
    !("severity" in row) ||
    typeof row.severity !== "string" ||
    !("enabled" in row) ||
    typeof row.enabled !== "boolean"
  )
    throw new Error("Portal returned an invalid rule");
  return {
    rule_id: row.id,
    description: optionalText(row, "description"),
    category: optionalText(row, "category"),
    source: optionalText(row, "source"),
    default_severity_label: optionalText(row, "defaultSeverity"),
    resolved_severity_label: row.severity,
    default_severity: 0,
    resolved_severity: 0,
    resolved_enabled: row.enabled,
    scope: "",
    registered_at: "",
    override:
      "hasOverride" in row && row.hasOverride
        ? { enforced: "enforced" in row && row.enforced === true }
        : null,
  };
}

function optionalText(row: object, field: string): string {
  const value: unknown = Reflect.get(row, field);
  if (value === undefined || value === null) return "";
  if (typeof value !== "string")
    throw new Error(`Portal returned an invalid rule ${field}`);
  return value;
}
