/*
 * Copyright Red Hat
 */

import { parseGitHubComRepoUrl } from "./parseGitHubComRepoUrl";

describe("parseGitHubComRepoUrl", () => {
  it.each(["", ".git"])(
    "normalizes the reported repository URL with suffix %s",
    (suffix) => {
      const result = parseGitHubComRepoUrl(
        `https://github.com/test-rhaap-portal-3/test-amazon-aws${suffix}`,
      );
      expect(result.ok && result.value.repoUrlPicker).toBe(
        "github.com?owner=test-rhaap-portal-3&repo=test-amazon-aws",
      );
    },
  );
  it("parses https github.com owner/repo", () => {
    const result = parseGitHubComRepoUrl("https://github.com/ansible/apme");
    expect(result).toEqual({
      ok: true,
      value: {
        owner: "ansible",
        repo: "apme",
        httpsUrl: "https://github.com/ansible/apme",
        repoUrlPicker: "github.com?owner=ansible&repo=apme",
      },
    });
  });

  it("parses git@ and .git suffix", () => {
    const result = parseGitHubComRepoUrl("git@github.com:ansible/apme.git");
    expect(result).toEqual({
      ok: true,
      value: {
        owner: "ansible",
        repo: "apme",
        httpsUrl: "https://github.com/ansible/apme",
        repoUrlPicker: "github.com?owner=ansible&repo=apme",
      },
    });
  });

  it("parses tree URL suggested branch", () => {
    const result = parseGitHubComRepoUrl(
      "https://github.com/ansible/apme/tree/devel",
    );
    expect(result).toEqual({
      ok: true,
      value: {
        owner: "ansible",
        repo: "apme",
        httpsUrl: "https://github.com/ansible/apme",
        repoUrlPicker: "github.com?owner=ansible&repo=apme",
        suggestedBranch: "devel",
      },
    });
  });

  it("rejects gitlab and other hosts", () => {
    const result = parseGitHubComRepoUrl("https://gitlab.com/o/r");
    expect(result).toEqual({
      ok: false,
      error: "Only github.com repositories are supported right now.",
    });
  });

  it("rejects incomplete paths", () => {
    const result = parseGitHubComRepoUrl("https://github.com/only-owner");
    expect(result.ok).toBe(false);
  });

  it("rejects empty", () => {
    expect(parseGitHubComRepoUrl("").ok).toBe(false);
  });
});
