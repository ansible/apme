/*
 * Copyright Red Hat
 */

import { FieldValidation } from "@rjsf/utils";
import { parseGitHubComRepoUrl } from "./parseGitHubComRepoUrl";

export function githubRepoUrlValidation(
  value: string,
  validation: FieldValidation,
) {
  const raw = value ?? "";
  const asUrl = raw.startsWith("github.com?")
    ? (() => {
        try {
          const q = new URLSearchParams(raw.split("?")[1] ?? "");
          const owner = q.get("owner");
          const repo = q.get("repo");
          return owner && repo ? `https://github.com/${owner}/${repo}` : raw;
        } catch {
          return raw;
        }
      })()
    : raw;
  const result = parseGitHubComRepoUrl(asUrl);
  if (!result.ok) {
    validation.addError(result.error);
  }
}
