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

/** True when an APME API/proxy error indicates the gateway is unreachable. */
export function isApmeConnectionError(message: string): boolean {
  const lower = message.toLowerCase();
  return (
    lower.includes("failed to connect to apme") ||
    lower.includes("fetch failed") ||
    lower.includes("econnrefused") ||
    lower.includes("network error") ||
    lower.includes("this operation was aborted") ||
    (lower.includes("apme api error") && lower.includes("failed to connect"))
  );
}

export const APME_GATEWAY_UNAVAILABLE_MESSAGE =
  "Ansible content modernization is temporarily unavailable. Check that the modernization service is running, then try again.";

export const APME_REMEDIATE_CONNECTION_TITLE =
  "Cannot reach modernization service";

/**
 * Turns backend/proxy error strings into short user-facing copy.
 * Strips JSON envelopes and stack traces when present.
 */
export function formatApmeUserFacingError(raw: string): string {
  if (!raw?.trim()) {
    return APME_GATEWAY_UNAVAILABLE_MESSAGE;
  }
  if (isApmeConnectionError(raw)) {
    return APME_GATEWAY_UNAVAILABLE_MESSAGE;
  }

  // Bare JSON bodies: {"detail":"Submit requires a remediate activity"}
  const trimmed = raw.trim();
  if (
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    trimmed.includes('"detail"')
  ) {
    try {
      const start = trimmed.indexOf("{");
      const end = trimmed.lastIndexOf("}");
      if (start >= 0 && end > start) {
        const parsed = JSON.parse(trimmed.slice(start, end + 1)) as {
          detail?: unknown;
          message?: unknown;
        };
        let detail: string | null = null;
        if (typeof parsed.detail === "string") {
          detail = parsed.detail;
        } else if (typeof parsed.message === "string") {
          detail = parsed.message;
        }
        if (detail?.trim()) {
          if (/remediate activity/i.test(detail)) {
            return "Push needs a remediation run, not a quality scan. Select fixable issues and try Push branch again.";
          }
          if (isPrAlreadyCreatedError(detail)) {
            const existing = extractExistingPrUrlFromError(detail);
            return existing
              ? `A pull request already exists for this remediation — continue there: ${existing}`
              : "A pull request already exists for this remediation. Open it from scan history or GitHub.";
          }
          return detail.trim();
        }
      }
    } catch {
      // fall through
    }
  }

  // Prefer nested InputError.message from Backstage error JSON bodies.
  const nestedMessage = /"message"\s*:\s*"((?:\\.|[^"\\])*)"/.exec(raw);
  if (nestedMessage?.[1]) {
    const decoded = nestedMessage[1]
      .replaceAll(String.raw`\n`, " ")
      .replaceAll(String.raw`\"`, '"')
      .trim();
    if (isApmeConnectionError(decoded)) {
      return APME_GATEWAY_UNAVAILABLE_MESSAGE;
    }
    if (/remediate activity/i.test(decoded)) {
      return "Push needs a remediation run, not a quality scan. Select fixable issues and try Push branch again.";
    }
    // Drop stack / path noise after the first sentence-ish chunk.
    // NOSONAR - Simple split pattern; no backtracking risk
    const withoutStack = decoded.split(/\s+at\s+/)[0]?.trim() ?? decoded; // NOSONAR
    if (withoutStack && withoutStack.length < 280) {
      return (
        withoutStack.replace(/^Failed to connect to APME:\s*/i, "").trim() ||
        APME_GATEWAY_UNAVAILABLE_MESSAGE
      );
    }
  }

  // "APME API error: 400 - …" → keep status + short body when small
  // Match API error prefix, then extract rest separately to avoid regex backtracking
  const apiPrefixPattern = /^APME API error:\s*(\d{3})\s*-\s*/i;
  const apiPrefixMatch = apiPrefixPattern.exec(raw);
  const apiMatch = apiPrefixMatch
    ? [
        apiPrefixMatch[0],
        apiPrefixMatch[1],
        raw.slice(apiPrefixMatch[0].length),
      ]
    : null;
  if (apiMatch) {
    const status = apiMatch[1];
    const rest = apiMatch[2].trim();
    if (/remediate activity/i.test(rest)) {
      return "Push needs a remediation run, not a quality scan. Select fixable issues and try Push branch again.";
    }
    if (rest.length > 200 || rest.startsWith("{")) {
      // Try detail inside JSON after status
      const formatted = formatApmeUserFacingError(rest);
      if (formatted !== rest && !formatted.startsWith("Request failed")) {
        return formatted;
      }
      return `Request failed (${status}). Try again, or check the modernization service if the problem continues.`;
    }
    return `Request failed (${status}): ${rest}`;
  }

  if (/remediate activity/i.test(raw)) {
    return "Push needs a remediation run, not a quality scan. Select fixable issues and try Push branch again.";
  }

  if (isPrAlreadyCreatedError(raw)) {
    const existing = extractExistingPrUrlFromError(raw);
    return existing
      ? `A pull request already exists for this remediation — continue there: ${existing}`
      : "A pull request already exists for this remediation. Open it from scan history or GitHub.";
  }

  if (raw.length > 280 || raw.includes("\n    at ")) {
    return "Something went wrong preparing fixes. Try again, or check the modernization service if the problem continues.";
  }
  return raw;
}

/**
 * Gateway reject when createPr/submit is called again for an activity that already has a PR.
 */
export function isPrAlreadyCreatedError(raw: string): boolean {
  return /PR already created for this activity/i.test(raw);
}

/** Pull request URL embedded in a gateway "PR already created…" error, if any. */
export function extractExistingPrUrlFromError(raw: string): string | null {
  const parsed = parseExistingPrFromError(raw);
  return parsed?.url ?? null;
}

/** GitHub `/owner/repo/pull/N` or GitLab `/.../-/merge_requests/N` pathname. */
const GITHUB_PR_PATH = /^\/[^/]+\/[^/]+\/pull\/(\d+)(?:\/|$)/i;
const GITLAB_MR_PATH = /^(?:\/[^/]+)+\/-\/merge_requests\/(\d+)(?:\/|$)/i;

/** Numeric PR/MR id from an SCM URL, if present. */
export function prNumberFromUrl(url: string): number | undefined {
  let pathname: string;
  try {
    pathname = new URL(url).pathname;
  } catch {
    pathname = url;
  }
  const numberMatch =
    GITHUB_PR_PATH.exec(pathname) ?? GITLAB_MR_PATH.exec(pathname);
  return numberMatch?.[1] ? Number.parseInt(numberMatch[1], 10) : undefined;
}

/** Parsed PR link from a gateway "PR already created…" error, if any. */
export function parseExistingPrFromError(
  raw: string,
): { url: string; prNumber?: number } | null {
  if (!raw?.trim() || !isPrAlreadyCreatedError(raw)) {
    return null;
  }
  const urlMatch =
    /https?:\/\/[^\s"'\\}\]]+\/(?:pull|merge_requests)\/\d+/i.exec(raw);
  if (!urlMatch?.[0]) {
    return null;
  }
  // NOSONAR - Simple trailing punctuation pattern; no backtracking risk
  const url = urlMatch[0].replace(/[.,;]+$/, ""); // NOSONAR
  return {
    url,
    prNumber: prNumberFromUrl(url),
  };
}

export function apmeRemediationErrorTitle(raw: string): string {
  if (isApmeConnectionError(raw)) {
    return APME_REMEDIATE_CONNECTION_TITLE;
  }
  if (/remediate activity/i.test(raw)) {
    return "Cannot push branch";
  }
  if (isPrAlreadyCreatedError(raw)) {
    return "Pull request already exists";
  }
  return "No automated patches";
}
