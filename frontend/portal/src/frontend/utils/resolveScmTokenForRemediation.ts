/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

/**
 * Resolves an SCM write token for APME remediation push/PR.
 *
 * Order:
 *  1. Cached user credentials via scmAuthApi with `optional: true` (no OAuth popup)
 *  2. Return undefined so the catalog backend / gateway can use GitHub App,
 *     integration PAT, or APME_SCM_TOKEN
 */

import { errorMessageFromUnknown } from "./toError";

export interface ScmAuthCredentialsApi {
  getCredentials(options: {
    url: string;
    optional?: boolean;
    additionalScope?: { repoWrite?: boolean };
  }): Promise<{ token?: string }>;
}

export type ResolveScmTokenMode = "optional" | "interactive";

const SCM_WRITE_ACCESS_FAILED =
  "GitHub write access failed for this repository owner. The portal GitHub App is not installed (or OAuth could not complete). Install the configured App on this account/org with Contents and Pull requests write, then retry.";

/**
 * Returns a user SCM token when available.
 * With `optional` mode, never forces an OAuth popup — may return undefined.
 * With `interactive` mode, prompts for GitHub authorization (repoWrite).
 */
export async function resolveScmTokenForRemediation(
  scmAuthApi: ScmAuthCredentialsApi,
  repoUrl: string,
  mode: ResolveScmTokenMode = "optional",
): Promise<string | undefined> {
  const trimmedUrl = repoUrl?.trim();
  if (!trimmedUrl) {
    throw new Error("No repository URL on this catalog entity.");
  }

  const creds = await scmAuthApi.getCredentials({
    url: trimmedUrl,
    optional: mode === "optional",
    additionalScope: { repoWrite: true },
  });
  const token = creds.token?.trim();
  if (token) {
    return token;
  }

  if (mode === "interactive") {
    throw new Error(SCM_WRITE_ACCESS_FAILED);
  }

  return undefined;
}

/** True when submit failed because no usable SCM write token was available. */
export function isScmTokenRequiredError(err: unknown): boolean {
  if (!(err instanceof Error)) {
    return false;
  }
  const message = err.message;
  const lower = message.toLowerCase();

  // Never treat client timeouts/aborts as auth failures (URLs may contain "401"
  // as a substring of a project UUID, e.g. ...c401c...).
  if (/timed out|aborted|failed to fetch|could not reach/i.test(message)) {
    return false;
  }

  // Match HTTP status codes from ApmeApiClient only — not hex IDs in URLs.
  const statusMatch = /APME API error:\s*(\d{3})\b/i.exec(message);
  const status = statusMatch ? Number(statusMatch[1]) : undefined;

  if (status === 401 || status === 403) {
    return true;
  }
  if (
    status === 422 &&
    (lower.includes("scm token") || lower.includes("no scm token"))
  ) {
    return true;
  }
  if (
    status === 502 &&
    (lower.includes("scm") ||
      lower.includes("github") ||
      lower.includes("token"))
  ) {
    return true;
  }

  return (
    lower.includes("unauthorized") ||
    lower.includes("bad credentials") ||
    lower.includes("authentication failed") ||
    lower.includes("failed to obtain access token") ||
    lower.includes("login failed") ||
    lower.includes("could not read from remote") ||
    lower.includes("no github token") ||
    lower.includes("no scm token configured")
  );
}

/** User-facing message when GitHub OAuth or token resolution fails. */
export function formatScmAuthFailureMessage(err: unknown): string {
  const raw = errorMessageFromUnknown(err);
  if (
    /authentication failed|failed to obtain access token|login failed|bad credentials|github write access failed/i.test(
      raw,
    ) ||
    isScmTokenRequiredError(err)
  ) {
    return SCM_WRITE_ACCESS_FAILED;
  }
  return raw;
}
