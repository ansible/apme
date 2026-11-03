/*
 * Copyright Red Hat
 *
 * Resolve inference-only AI status/models for the catalog proxy.
 * Config-listed models are reported separately and must not imply Connected.
 */

import type { LoggerService } from "@backstage/backend-plugin-api";
import {
  IApmeService,
  normalizeApmeAiProviders,
  formatApmeAbbenayChatModelId,
} from "../common/index";

export type ApmeAiModelRow = {
  id: string;
  provider: string;
  name: string;
};

export type ResolvedApmeAiStatus = {
  connected: boolean;
  modelCount: number;
  configuredModelCount: number;
};

/**
 * AI models for the Quality tab model picker.
 * Prefers live inference list; falls back to configured provider models
 * when inference returns empty (AAP-89202).
 */
export async function listApmeInferenceModels(
  apmeService: IApmeService,
): Promise<ApmeAiModelRow[]> {
  const live = await apmeService.getAiModels();
  if (live.length > 0) {
    return live;
  }

  try {
    const config = await apmeService.getAiConfig();
    const providers = normalizeApmeAiProviders(config);
    const rows: ApmeAiModelRow[] = [];
    for (const provider of providers) {
      for (const model of provider.models) {
        if (model.trim()) {
          rows.push({
            id: formatApmeAbbenayChatModelId(provider.id, model),
            provider: provider.id,
            name: model,
          });
        }
      }
    }
    return rows;
  } catch {
    return [];
  }
}

/**
 * connected = Abbenay control plane reachable (inference models, engines catalog,
 * or successful admin config read). modelCount is inference-only.
 * configuredModelCount is admin-config models (may be > 0 while modelCount is 0
 * when provider keys are missing from the Abbenay process env).
 */
export async function resolveApmeAiStatus(
  apmeService: IApmeService,
  logger: LoggerService,
): Promise<ResolvedApmeAiStatus> {
  let modelCount = 0;
  let connected = false;

  try {
    const models = await apmeService.getAiModels();
    modelCount = models.length;
    connected = modelCount > 0;
  } catch (err) {
    logger.debug(`APME AI models check failed: ${String(err)}`);
  }

  let configuredModelCount = 0;
  try {
    const config = await apmeService.getAiConfig();
    const providers = normalizeApmeAiProviders(config);
    configuredModelCount = providers.reduce(
      (sum, p) => sum + p.models.length,
      0,
    );
    // Admin HTTP reachable — treat as connected even if ListAIModels is empty.
    if (!connected) {
      connected = true;
    }
  } catch (err) {
    logger.debug(`APME AI config check failed: ${String(err)}`);
  }

  if (!connected) {
    try {
      const engines = await apmeService.getAiEngines();
      if ((engines?.engines ?? []).length > 0) {
        connected = true;
      }
    } catch (err) {
      logger.debug(`APME AI engines check failed: ${String(err)}`);
    }
  }

  if (!connected) {
    try {
      const health = await apmeService.getHealth();
      const abbenay = health.components?.find((c) =>
        /abbenay/i.test(c.name ?? ""),
      );
      connected =
        abbenay?.status === "ok" ||
        abbenay?.status === "healthy" ||
        abbenay?.status === "up";
    } catch (err) {
      logger.debug(`APME health check for AI status failed: ${String(err)}`);
    }
  }

  return { connected, modelCount, configuredModelCount };
}
