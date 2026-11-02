/*
 * Copyright Red Hat
 */

import { useApi } from "@backstage/core-plugin-api";
import { useAsync } from "react-use";
import { useRef } from "react";
import {
  formatApmeAbbenayChatModelId,
  pickApmeChatModelId,
  resolveApmeChatModelIdFromProviders,
} from "../../common/apmeChatModel";
import { normalizeApmeAiProviders } from "../../common/types";
import { AI_MODEL_STORAGE_KEY } from "@apme/ui-workflow";
import { apmeApiRef } from "../api";
import { useApmeAiEnabled } from "./useApmeEnabled";

/**
 * Resolve the Abbenay chat model id for remediate / escalate-ai.
 * Prefers portal settings, validates against live model list, then provider config.
 */
export function useApmeWorkflowAiModel(): () => string | undefined {
  const apmeApi = useApi(apmeApiRef);
  const portalAiEnabled = useApmeAiEnabled();
  const modelRef = useRef<string | undefined>(undefined);

  const { value } = useAsync(async () => {
    if (!portalAiEnabled) {
      return undefined;
    }

    const [settings, models, providersRaw] = await Promise.all([
      apmeApi.getPortalSettings(),
      apmeApi.getAiModels().catch(() => []),
      apmeApi.getAiProviders().catch(() => []),
    ]);

    const modelIds = models.map((m) => m.id).filter(Boolean);
    const fromPortal = settings.defaultAiModelId;
    const fromStorage =
      typeof localStorage !== "undefined"
        ? localStorage.getItem(AI_MODEL_STORAGE_KEY)
        : null;

    const picked = pickApmeChatModelId(fromPortal ?? fromStorage, modelIds);
    if (picked) {
      return picked;
    }

    const providers = normalizeApmeAiProviders(providersRaw);
    return resolveApmeChatModelIdFromProviders(providers);
  }, [apmeApi, portalAiEnabled]);

  modelRef.current = value;
  return () => modelRef.current;
}

/** Persist default chat model after provider setup in Quality settings. */
export async function persistApmeDefaultAiModel(
  apmeApi: {
    updatePortalSettings(body: {
      defaultAiModelId?: string | null;
    }): Promise<unknown>;
  },
  providerId: string,
  modelId: string,
): Promise<string> {
  const chatModelId = formatApmeAbbenayChatModelId(providerId, modelId);
  if (typeof localStorage !== "undefined") {
    localStorage.setItem(AI_MODEL_STORAGE_KEY, chatModelId);
  }
  try {
    await apmeApi.updatePortalSettings({ defaultAiModelId: chatModelId });
  } catch {
    // Portal settings persistence is best-effort; localStorage still applies.
  }
  return chatModelId;
}
