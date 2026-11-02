/*
 * Copyright Red Hat
 *
 * Effective APME Gateway URL: portal-settings override, then app-config.
 */

import type { ApmePortalSettingsData } from "./resolveScanTarget";
import { trimTrailingSlashes } from "./trimTrailingSlashes";

export type GatewayBaseUrlSource = "global" | "config";

export class InvalidGatewayBaseUrlError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidGatewayBaseUrlError";
  }
}

/**
 * Normalize an http(s) Gateway URL: trim, drop trailing slash, reject
 * credentials / query / fragment. Empty input is unset (undefined).
 */
export function normalizeGatewayBaseUrl(
  raw?: string | null,
): string | undefined {
  if (raw === undefined || raw === null) {
    return undefined;
  }
  const trimmed = raw.trim();
  if (!trimmed) {
    return undefined;
  }

  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new InvalidGatewayBaseUrlError(
      "APME service URL must be a valid http(s) URL",
    );
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new InvalidGatewayBaseUrlError(
      "APME service URL must use http or https",
    );
  }
  if (parsed.username || parsed.password) {
    throw new InvalidGatewayBaseUrlError(
      "APME service URL must not include credentials",
    );
  }
  if (parsed.search || parsed.hash) {
    throw new InvalidGatewayBaseUrlError(
      "APME service URL must not include a query string or fragment",
    );
  }

  const path = trimTrailingSlashes(parsed.pathname);
  return `${parsed.origin}${path === "/" ? "" : path}`;
}

export interface ResolveGatewayBaseUrlInput {
  store?: ApmePortalSettingsData;
  configBaseUrl: string;
}

export interface GatewayBaseUrlResolution {
  effective: string;
  source: GatewayBaseUrlSource;
}

/** Resolve the Gateway URL the backend should call (store > app-config). */
export function resolveGatewayBaseUrl(
  input: ResolveGatewayBaseUrlInput,
): GatewayBaseUrlResolution {
  let fromStore: string | undefined;
  try {
    fromStore = normalizeGatewayBaseUrl(input.store?.global?.gatewayBaseUrl);
  } catch {
    fromStore = undefined;
  }
  if (fromStore) {
    return { effective: fromStore, source: "global" };
  }

  let fromConfig: string | undefined;
  try {
    fromConfig = normalizeGatewayBaseUrl(input.configBaseUrl);
  } catch {
    fromConfig = undefined;
  }
  return {
    effective: fromConfig ?? trimTrailingSlashes(input.configBaseUrl),
    source: "config",
  };
}
