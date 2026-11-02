/*
 * Copyright Red Hat
 *
 * Parse github.com repository URLs for the APME register template.
 * Host is fixed to github.com for now (US-003).
 */

export type ParsedGitHubComRepo = {
  owner: string;
  repo: string;
  /** From /tree/{ref}/ or /blob/{ref}/ when present. */
  suggestedBranch?: string;
  /** Canonical https://github.com/{owner}/{repo} (no .git). */
  httpsUrl: string;
  /**
   * RepoUrlPicker-compatible value for scaffolder `parseRepoUrl`
   * (`github.com?owner=…&repo=…`).
   */
  repoUrlPicker: string;
};

export type ParseGitHubComRepoResult =
  { ok: true; value: ParsedGitHubComRepo } | { ok: false; error: string };

const GITHUB_HOST = "github.com";

function stripGitSuffix(name: string): string {
  return name.replace(/\.git$/i, "");
}

/**
 * Accepts https://github.com/o/r, github.com/o/r, git@github.com:o/r.git,
 * and tree/blob URLs. Rejects non-github.com hosts.
 */
export function parseGitHubComRepoUrl(raw: string): ParseGitHubComRepoResult {
  const trimmed = raw.trim();
  if (!trimmed) {
    return { ok: false, error: "Enter a GitHub repository URL." };
  }

  let url: URL;
  try {
    if (/^git@github\.com:/i.test(trimmed)) {
      const path = trimmed
        .replace(/^git@github\.com:/i, "")
        .replace(/\.git$/i, "");
      url = new URL(`https://github.com/${path}`);
    } else if (/^github\.com\//i.test(trimmed)) {
      url = new URL(`https://${trimmed}`);
    } else {
      url = new URL(trimmed);
    }
  } catch {
    return {
      ok: false,
      error: "Invalid URL. Paste a github.com repository link.",
    };
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { ok: false, error: "URL must use https:// (github.com only)." };
  }

  if (url.hostname.toLowerCase() !== GITHUB_HOST) {
    return {
      ok: false,
      error: "Only github.com repositories are supported right now.",
    };
  }
  if (url.username || url.password || url.port) {
    return {
      ok: false,
      error: "Use a GitHub URL without credentials or a custom port.",
    };
  }

  const parts = url.pathname.split("/").filter(Boolean);
  if (parts.length < 2) {
    return {
      ok: false,
      error: "URL must include owner and repository (github.com/owner/repo).",
    };
  }

  const owner = parts[0];
  const repo = stripGitSuffix(parts[1]);
  if (!owner || !repo) {
    return {
      ok: false,
      error: "Could not read owner and repository from the URL.",
    };
  }

  let suggestedBranch: string | undefined;
  if (
    parts.length >= 4 &&
    (parts[2] === "tree" || parts[2] === "blob" || parts[2] === "edit")
  ) {
    try {
      suggestedBranch = decodeURIComponent(parts[3]);
    } catch {
      return { ok: false, error: "Invalid branch encoding in repository URL." };
    }
  }

  const httpsUrl = `https://github.com/${owner}/${repo}`;
  const repoUrlPicker = `${GITHUB_HOST}?owner=${encodeURIComponent(
    owner,
  )}&repo=${encodeURIComponent(repo)}`;

  return {
    ok: true,
    value: {
      owner,
      repo,
      ...(suggestedBranch ? { suggestedBranch } : {}),
      httpsUrl,
      repoUrlPicker,
    },
  };
}
