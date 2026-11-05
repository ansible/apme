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

import { configApiRef, useApi } from "@backstage/core-plugin-api";
import { useSyncExternalStore } from "react";
import { useAsync } from "react-use";
import type { ApmeAiStatus, ApmePortalSettings } from "../../common/types";
import { apmeApiRef } from "../api";

const SETTINGS_CACHE_TTL_MS = 5 * 60 * 1000;

let portalSettingsCache: ApmePortalSettings | undefined;
let portalSettingsCacheTime = 0;
let portalSettingsPromise: Promise<ApmePortalSettings> | undefined;
let portalSettingsIsError = false;
let portalSettingsRevision = 0;
const portalSettingsSubscribers = new Set<() => void>();

function subscribeToPortalSettings(listener: () => void): () => void {
  portalSettingsSubscribers.add(listener);
  return () => portalSettingsSubscribers.delete(listener);
}

function getPortalSettingsRevision(): number {
  return portalSettingsRevision;
}

/** Drop cached portal settings after Quality settings save. */
export function invalidateApmePortalSettingsCache(): void {
  portalSettingsCache = undefined;
  portalSettingsCacheTime = 0;
  portalSettingsPromise = undefined;
  portalSettingsIsError = false;
  portalSettingsRevision += 1;
  portalSettingsSubscribers.forEach((listener) => listener());
}

async function loadPortalSettings(
  apmeApi: { getPortalSettings(): Promise<ApmePortalSettings> },
  configFallback: ApmePortalSettings,
): Promise<ApmePortalSettings> {
  const now = Date.now();
  if (
    portalSettingsCache &&
    now - portalSettingsCacheTime < SETTINGS_CACHE_TTL_MS
  ) {
    return portalSettingsCache;
  }
  if (portalSettingsIsError) {
    portalSettingsPromise = undefined;
    portalSettingsIsError = false;
  }
  portalSettingsPromise ??= apmeApi
    .getPortalSettings()
    .then((settings) => {
      portalSettingsCache = settings;
      portalSettingsCacheTime = Date.now();
      return settings;
    })
    .catch(() => {
      portalSettingsIsError = true;
      return configFallback;
    });
  return portalSettingsPromise;
}

/** Returns whether APME is enabled in app configuration (ADR-011). */
export function useApmeEnabled(): boolean {
  const configApi = useApi(configApiRef);
  return configApi.getOptionalBoolean("ansible.apme.enabled") ?? false;
}

/**
 * Portal AI tier flag from Quality settings (`GET /apme/settings`).
 */
export function useApmeAiEnabled(): boolean {
  const configApi = useApi(configApiRef);
  const apmeApi = useApi(apmeApiRef);
  const settingsRevision = useSyncExternalStore(
    subscribeToPortalSettings,
    getPortalSettingsRevision,
    getPortalSettingsRevision,
  );
  const { value } = useAsync(
    () =>
      loadPortalSettings(apmeApi, {
        enableAi: false,
        publishViaGateway:
          configApi.getOptionalBoolean("ansible.apme.publishViaGateway") ??
          true,
      }),
    [apmeApi, configApi, settingsRevision],
  );
  return value?.enableAi ?? false;
}

/** Gateway Abbenay reachability (separate from portal enableAi). */
export function useApmeAiStatus(): {
  status: ApmeAiStatus | undefined;
  loading: boolean;
} {
  const apmeApi = useApi(apmeApiRef);
  const enableAi = useApmeAiEnabled();
  const { value, loading } = useAsync(async () => {
    if (!enableAi) {
      return undefined;
    }
    try {
      return await apmeApi.getAiStatus();
    } catch {
      return { enableAi: true, connected: false, modelCount: 0 };
    }
  }, [apmeApi, enableAi]);
  return { status: value, loading };
}
