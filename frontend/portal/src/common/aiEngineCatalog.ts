/*
 * Copyright Red Hat
 *
 * Canonical Abbenay engine metadata for portal UI when the Gateway
 * settings API is unavailable (older gateway images).
 */

import type { ApmeAiEngineInfo } from "./types";

/** Engines supported by Abbenay provider adapters (portal fallback catalog). */
export const DEFAULT_AI_ENGINES: ApmeAiEngineInfo[] = [
  {
    id: "openrouter",
    requiresKey: true,
    defaultBaseUrl: "https://openrouter.ai/api/v1",
    defaultEnvVar: "OPENROUTER_API_KEY",
  },
  {
    id: "openai",
    requiresKey: true,
    defaultBaseUrl: "https://api.openai.com/v1",
    defaultEnvVar: "OPENAI_API_KEY",
  },
  {
    id: "ollama",
    requiresKey: false,
    // NOSONAR - HTTP is intentional for container-internal communication; local Ollama doesn't support HTTPS by default
    defaultBaseUrl: "http://host.containers.internal:11434", // NOSONAR
    defaultEnvVar: "",
  },
  {
    id: "vllm",
    requiresKey: false,
    // NOSONAR - HTTP is intentional for container-internal communication; local vLLM doesn't support HTTPS by default
    defaultBaseUrl: "http://host.containers.internal:8000/v1", // NOSONAR
    defaultEnvVar: "",
  },
];
