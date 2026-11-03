/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import type { Config } from "@backstage/config";
import { InputError } from "@backstage/errors";
import {
  getApmeConfig,
  resolveScanTarget,
  resolveGatewayBaseUrl,
  isAllowedAnsibleCoreVersion,
  normalizeGatewayBaseUrl,
  InvalidGatewayBaseUrlError,
} from "../common/index";
import type { ApmePortalSettingsStore } from "./apmePortalSettingsStore";

/** One validation/persistence implementation for legacy HTTP and Portal operations. */
export function createPortalSettingsService(
  rootConfig: Config,
  portalSettingsStore: ApmePortalSettingsStore,
) {
  const configSnapshot = getApmeConfig(rootConfig);
  async function get() {
    const store = await portalSettingsStore.read();
    const resolved = resolveScanTarget({
      store,
      configTargetAnsibleCoreVersion: configSnapshot.targetAnsibleCoreVersion,
    });
    return {
      enableAi: store.global?.enableAi ?? false,
      publishViaGateway: configSnapshot.publishViaGateway,
      targetAnsibleCoreVersion: resolved.effective,
      defaultAiModelId: store.global?.defaultAiModelId,
      gatewayBaseUrl: resolveGatewayBaseUrl({
        store,
        configBaseUrl: configSnapshot.baseUrl,
      }).effective,
    };
  }
  async function save(body: Record<string, unknown>) {
    const {
      targetAnsibleCoreVersion,
      defaultAiModelId,
      enableAi,
      gatewayBaseUrl,
    } = body;
    const updates: {
      targetAnsibleCoreVersion?: string;
      defaultAiModelId?: string | null;
      enableAi?: boolean;
      gatewayBaseUrl?: string | null;
    } = {};

    if (targetAnsibleCoreVersion !== undefined) {
      if (
        typeof targetAnsibleCoreVersion !== "string" ||
        !targetAnsibleCoreVersion.trim()
      ) {
        throw new InputError(
          "targetAnsibleCoreVersion must be a non-empty string",
        );
      }
      if (!isAllowedAnsibleCoreVersion(targetAnsibleCoreVersion)) {
        throw new InputError(
          `Unsupported ansible-core version: ${targetAnsibleCoreVersion}`,
        );
      }
      updates.targetAnsibleCoreVersion = targetAnsibleCoreVersion.trim();
    }

    if (defaultAiModelId !== undefined) {
      if (defaultAiModelId !== null && typeof defaultAiModelId !== "string") {
        throw new InputError("defaultAiModelId must be a string or null");
      }
      updates.defaultAiModelId =
        typeof defaultAiModelId === "string" ? defaultAiModelId : null;
    }

    if (enableAi !== undefined) {
      if (typeof enableAi !== "boolean") {
        throw new InputError("enableAi must be a boolean");
      }
      updates.enableAi = enableAi;
    }

    if (gatewayBaseUrl !== undefined) {
      if (gatewayBaseUrl !== null && typeof gatewayBaseUrl !== "string") {
        throw new InputError("gatewayBaseUrl must be a string or null");
      }
      if (gatewayBaseUrl === null || !gatewayBaseUrl.trim()) {
        updates.gatewayBaseUrl = null;
      } else {
        try {
          const normalized = normalizeGatewayBaseUrl(gatewayBaseUrl);
          if (!normalized) {
            throw new InputError("gatewayBaseUrl must be a non-empty string");
          }
          updates.gatewayBaseUrl = normalized;
        } catch (error) {
          if (error instanceof InputError) {
            throw error;
          }
          const message =
            error instanceof InvalidGatewayBaseUrlError
              ? error.message
              : "gatewayBaseUrl is invalid";
          throw new InputError(message);
        }
      }
    }

    if (
      updates.targetAnsibleCoreVersion === undefined &&
      updates.defaultAiModelId === undefined &&
      updates.enableAi === undefined &&
      updates.gatewayBaseUrl === undefined
    ) {
      throw new InputError(
        "At least one of targetAnsibleCoreVersion, defaultAiModelId, enableAi, or gatewayBaseUrl is required",
      );
    }

    await portalSettingsStore.updateGlobalSettings(updates);
    return get();
  }
  return { get, save };
}
