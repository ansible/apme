/*
 * Copyright Red Hat
 */

import { ConfigReader } from "@backstage/config";
import { InputError } from "@backstage/errors";
import {
  lastPathSegment,
  parseGitRepoUrl,
  validateRepoBranch,
} from "./branchLookup";

describe("branchLookup", () => {
  describe("parseGitRepoUrl", () => {
    it("parses GitHub URLs", () => {
      expect(
        parseGitRepoUrl("https://github.com/craig-br/ans-tower-devsecops.git"),
      ).toEqual({
        provider: "github",
        host: "github.com",
        owner: "craig-br",
        repo: "ans-tower-devsecops",
      });
    });

    it("parses GitLab group URLs", () => {
      expect(
        parseGitRepoUrl("https://gitlab.com/my-group/subgroup/my-repo"),
      ).toEqual({
        provider: "gitlab",
        host: "gitlab.com",
        owner: "my-group/subgroup",
        repo: "my-repo",
      });
    });

    it("throws for invalid URLs", () => {
      expect(() => parseGitRepoUrl("not-a-url")).toThrow(InputError);
    });

    it("returns an empty last path segment for empty lists", () => {
      expect(lastPathSegment([])).toBe("");
      expect(lastPathSegment(["group", "repo"])).toBe("repo");
    });
  });

  describe("validateRepoBranch", () => {
    const rootConfig = new ConfigReader({
      integrations: {
        github: [{ host: "github.com", token: "test-token" }],
      },
    });

    beforeEach(() => {
      global.fetch = jest.fn();
    });

    it("passes when the branch exists", async () => {
      (global.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          json: async () => ({ default_branch: "master" }),
        })
        .mockResolvedValueOnce({ ok: true, status: 200 });

      await expect(
        validateRepoBranch({
          rootConfig,
          repoUrl: "https://github.com/craig-br/ans-tower-devsecops",
          branch: "master",
        }),
      ).resolves.toBeUndefined();
    });

    it("throws when the branch does not exist", async () => {
      (global.fetch as jest.Mock)
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          json: async () => ({ default_branch: "master" }),
        })
        .mockResolvedValueOnce({ ok: false, status: 404 });

      await expect(
        validateRepoBranch({
          rootConfig,
          repoUrl: "https://github.com/craig-br/ans-tower-devsecops",
          branch: "main",
        }),
      ).rejects.toThrow(
        "Branch 'main' was not found in craig-br/ans-tower-devsecops",
      );
    });

    it("includes SCM HTTP status when metadata cannot be loaded", async () => {
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 403,
      });

      await expect(
        validateRepoBranch({
          rootConfig,
          repoUrl: "https://github.com/acme-scm/apme",
          branch: "main",
          scmToken: "bad-token",
        }),
      ).rejects.toThrow(/SCM HTTP 403/);
    });
  });
});
