/*
 * Copyright Red Hat
 */

import type { ApmeAiProviderSummary } from "./types";

/** Abbenay chat model id: virtual provider + model (e.g. `test/gpt-4o`). */
export function formatApmeAbbenayChatModelId(
  providerId: string,
  modelId: string,
): string {
  const provider = providerId.trim();
  const model = modelId.trim();
  if (!model) {
    return provider;
  }
  if (model.includes("/")) {
    return model;
  }
  return `${provider}/${model}`;
}

/** First enabled model from configured Abbenay providers. */
export function resolveApmeChatModelIdFromProviders(
  providers: ApmeAiProviderSummary[],
): string | undefined {
  const sorted = [...providers].sort((a, b) =>
    a.id.localeCompare(b.id, undefined, { sensitivity: "base" }),
  );
  for (const provider of sorted) {
    const firstModel = provider.models.find((m) => m.trim());
    if (firstModel) {
      return formatApmeAbbenayChatModelId(provider.id, firstModel);
    }
  }
  return undefined;
}

/** Prefer a stored id when it is still listed by the gateway. */
export function pickApmeChatModelId(
  preferred: string | undefined | null,
  availableIds: string[],
): string | undefined {
  const ids = new Set(availableIds.filter((id) => id.trim()));
  const trimmed = preferred?.trim();
  if (trimmed && ids.has(trimmed)) {
    return trimmed;
  }
  return availableIds.find((id) => id.trim()) ?? undefined;
}
