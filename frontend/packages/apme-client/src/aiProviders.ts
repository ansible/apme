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

// Imported from ansible-backstage-plugins 6ab7fce5a37406c8958d1dc0291a4bc96eb1d3f5.
export interface ApmeAiEngineInfo {
  id: string;
  requiresKey: boolean;
  defaultBaseUrl?: string;
  defaultEnvVar?: string;
}

/** Response shape of GET /api/v1/ai/engines. */
export interface ApmeAiEnginesResponse {
  engines: ApmeAiEngineInfo[];
}

/** Origin of an AI provider row in Quality settings. */
export type ApmeAiProviderSource = "managed" | "config";

/** Provider summary returned by the portal catalog proxy — no secrets. */
export interface ApmeAiProviderSummary {
  id: string;
  engine: string;
  /**
   * Real model data only when normalized from GET /apme/ai/config. When
   * normalized from GET /apme/ai/providers, always []. Abbenay's provider
   * registry doesn't carry model IDs. Use mergeApmeAiProviderLists to combine
   * both sources into rows with populated models.
   */
  models: string[];
  /**
   * `managed` = portal / Abbenay file-store (editable).
   * `config` = deploy-time ConfigMap only (read-only).
   */
  source?: ApmeAiProviderSource;
}

/** Model row from portal-side provider discovery (Ollama / OpenAI-compatible APIs). */
export interface DiscoveredAiModel {
  id: string;
  name: string;
  provider: string;
  engine: string;
}

/** Raw shape of GET /api/v1/ai/config from the Gateway. */
export interface ApmeAiConfigResponse {
  config?: {
    providers?: unknown;
  };
  path?: string;
  [key: string]: unknown;
}

/** Request body for POST …/provider/{id}/configure (Abbenay via Gateway ADR-070). */
export interface ApmeAiProviderConfigureRequest {
  engine: string;
  /**
   * Write-only API key. With ``secretStore: "file"`` (Abbenay ≥ v2026.8.6),
   * Abbenay persists the key on the config volume — Gateway does not store it.
   */
  apiKey?: string;
  baseUrl?: string;
  /** Logical Abbenay secret name; defaults to ``{PROVIDER}_API_KEY`` when omitted. */
  secretName?: string;
  /**
   * Abbenay secret backend. Portal uses ``file`` for durable container keys.
   * ``env`` + ``envVarName`` remains for deploy-time secrets.
   */
  secretStore?: "file" | "memory" | "keychain" | "env";
  /** Deploy-time process env var name (sets ``secretStore: env``). */
  envVarName?: string;
  /** @deprecated Prefer ``secretStore``. */
  secretStorage?: "env" | "keychain" | "file" | "memory";
}

function normalizeProviderEntry(p: unknown): ApmeAiProviderSummary {
  if (!p || typeof p !== "object") {
    const id = typeof p === "string" || typeof p === "number" ? String(p) : "";
    return { id, engine: "", models: [] };
  }
  const obj = p as Record<string, unknown>;
  let id = "";
  if (typeof obj.id === "string") {
    id = obj.id;
  } else if (typeof obj.id === "number") {
    id = String(obj.id);
  }
  const engine = typeof obj.engine === "string" ? obj.engine : "";
  let models: string[] = [];
  if (Array.isArray(obj.models)) {
    models = obj.models.filter((m): m is string => typeof m === "string");
  } else if (obj.models && typeof obj.models === "object") {
    models = Object.keys(obj.models as Record<string, unknown>);
  }
  return { id, engine, models };
}

/**
 * Normalise the raw AI providers response from the Gateway into a flat array.
 * Handles: bare array, `{providers: array}`, `{providers: record}`,
 * and `{config: {providers: record}}`. Never exposes secrets.
 */
export function normalizeApmeAiProviders(
  raw: unknown,
): ApmeAiProviderSummary[] {
  if (Array.isArray(raw)) {
    return raw.map((p) => normalizeProviderEntry(p));
  }
  if (raw && typeof raw === "object") {
    const obj = raw as Record<string, unknown>;
    let providersSource: unknown;
    if ("providers" in obj) {
      providersSource = obj.providers;
    } else if (obj.config && typeof obj.config === "object") {
      providersSource = (obj.config as Record<string, unknown>).providers;
    }
    if (Array.isArray(providersSource)) {
      return providersSource.map((p) => normalizeProviderEntry(p));
    }
    if (providersSource && typeof providersSource === "object") {
      return Object.entries(providersSource as Record<string, unknown>).map(
        ([id, val]) =>
          normalizeProviderEntry({
            id,
            ...(typeof val === "object" && val !== null ? val : {}),
          }),
      );
    }
  }
  return [];
}

/**
 * Merge HTTP `/providers` (portal-managed) with `/config` providers (ConfigMap).
 * Managed wins on id collision; config-only rows are tagged read-only.
 */
export function mergeApmeAiProviderLists(
  fromProviders: ApmeAiProviderSummary[],
  fromConfig: ApmeAiProviderSummary[],
): ApmeAiProviderSummary[] {
  const configById = new Map(fromConfig.map((p) => [p.id, p]));
  const managedIds = new Set(fromProviders.map((p) => p.id));

  const managed = fromProviders.map((p) => {
    const cfg = configById.get(p.id);
    return {
      ...p,
      models: p.models.length > 0 ? p.models : (cfg?.models ?? p.models ?? []),
      source: "managed" as const,
    };
  });

  const configOnly = fromConfig
    .filter((p) => !managedIds.has(p.id))
    .map((p) => ({ ...p, source: "config" as const }));

  return [...managed, ...configOnly].sort((a, b) =>
    a.id.localeCompare(b.id, undefined, { sensitivity: "base" }),
  );
}
