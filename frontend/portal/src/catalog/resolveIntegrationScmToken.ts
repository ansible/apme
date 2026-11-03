/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import type { LoggerService } from "@backstage/backend-plugin-api";
import type { Config } from "@backstage/config";
import {
  ScmClientFactory,
  resolveGithubToken,
} from "@ansible/backstage-rhaap-common";
import { parseGitRepoUrl } from "./branchLookup";

/**
 * Resolves a server-side SCM write token when the request has no user token.
 * Uses GitHub App / integration PAT via {@link resolveGithubToken}.
 * Returns undefined when no credentials can be resolved (gateway may still
 * use APME_SCM_TOKEN).
 */
export async function resolveIntegrationScmToken(options: {
  rootConfig: Config;
  logger: LoggerService;
  repoUrl: string;
}): Promise<string | undefined> {
  const { rootConfig, logger, repoUrl } = options;
  let parsed;
  try {
    parsed = parseGitRepoUrl(repoUrl);
  } catch {
    logger.debug(
      `Cannot parse repo URL for integration token fallback: ${repoUrl}`,
    );
    return undefined;
  }

  if (parsed.provider !== "github") {
    // Portal integration fallback currently supports GitHub only for PR publish.
    return undefined;
  }

  try {
    const scmFactory = new ScmClientFactory({ rootConfig, logger });
    const resolved = await resolveGithubToken({
      integrations: scmFactory.integrations,
      credentialsProvider: scmFactory.githubCredentialsProvider,
      logger,
      host: parsed.host,
      organization: parsed.owner,
      repository: parsed.repo,
    });
    return resolved.token?.trim() || undefined;
  } catch (err) {
    logger.debug(
      `No integration SCM token for ${repoUrl}: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
    return undefined;
  }
}
