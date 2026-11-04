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

import { ApmeApiClient } from "./ApmeApi";

describe("ApmeApiClient", () => {
  const mockDiscoveryApi = {
    getBaseUrl: jest
      .fn()
      .mockResolvedValue("http://localhost:7007/api/catalog"),
  };

  const mockFetchApi = {
    fetch: jest.fn(),
  };

  let client: ApmeApiClient;

  beforeEach(() => {
    jest.clearAllMocks();
    client = new ApmeApiClient({
      discoveryApi: mockDiscoveryApi,
      fetchApi: mockFetchApi,
    });
  });

  describe("getHealth", () => {
    it("should fetch health status", async () => {
      const mockHealth = {
        status: "ok",
        database: "ok",
        components: [
          { name: "Primary", status: "ok", address: "127.0.0.1:50051" },
        ],
      };

      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockHealth),
      });

      const result = await client.getHealth();

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        "http://localhost:7007/api/catalog/apme/health",
        expect.objectContaining({
          headers: { "Content-Type": "application/json" },
        }),
      );
      expect(result).toEqual(mockHealth);
    });
  });

  describe("portal settings", () => {
    it("getPortalSettings GETs /settings", async () => {
      const settings = {
        enableAi: false,
        publishViaGateway: true,
        targetAnsibleCoreVersion: "2.17",
      };
      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(settings),
      });

      const result = await client.getPortalSettings();

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        "http://localhost:7007/api/catalog/apme/settings",
        expect.objectContaining({
          headers: { "Content-Type": "application/json" },
        }),
      );
      expect(result).toEqual(settings);
    });

    it("updatePortalSettings PUTs /settings with version body", async () => {
      const settings = {
        enableAi: false,
        publishViaGateway: true,
        targetAnsibleCoreVersion: "2.18",
      };
      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(settings),
      });

      const result = await client.updatePortalSettings({
        targetAnsibleCoreVersion: "2.18",
      });

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        "http://localhost:7007/api/catalog/apme/settings",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ targetAnsibleCoreVersion: "2.18" }),
        }),
      );
      expect(result).toEqual(settings);
    });

    it("maps network TypeError to a reachable-backend message", async () => {
      mockFetchApi.fetch.mockRejectedValueOnce(
        new TypeError("Failed to fetch"),
      );

      let message = "";
      try {
        await client.getPortalSettings();
      } catch (err) {
        message = err instanceof Error ? err.message : String(err);
      }

      expect(message).toMatch(
        /Could not reach APME catalog API.*Check that the portal backend is available/,
      );
      expect(message).not.toMatch(/port 7007/i);
    });
  });

  describe("getProjects", () => {
    it("should fetch projects list", async () => {
      const mockProjects = {
        items: [
          { id: "1", name: "Project 1", repo_url: "https://github.com/test/1" },
          { id: "2", name: "Project 2", repo_url: "https://github.com/test/2" },
        ],
      };

      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockProjects),
      });

      const result = await client.getProjects();

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        "http://localhost:7007/api/catalog/apme/projects",
        expect.any(Object),
      );
      expect(result).toEqual(mockProjects.items);
    });

    it("should return empty array when no projects", async () => {
      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ items: null }),
      });

      const result = await client.getProjects();
      expect(result).toEqual([]);
    });
  });

  describe("getProjectByRepoUrl", () => {
    it("should find project by repo URL with branch", async () => {
      const mockProject = {
        id: "proj-backup",
        name: "terrible-playbook-backup",
        repo_url: "https://github.com/test/1",
        branch: "backup",
      };

      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockProject),
      });

      const result = await client.getProjectByRepoUrl(
        "https://github.com/test/1",
        "backup",
      );

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        expect.stringContaining("lookup?repo_url="),
        expect.any(Object),
      );
      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        expect.stringContaining("&branch=backup"),
        expect.any(Object),
      );
      expect(result).toEqual(mockProject);
    });

    it("should find project by repo URL", async () => {
      const mockProject = {
        id: "1",
        name: "Project 1",
        repo_url: "https://github.com/test/1",
      };

      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockProject),
      });

      const result = await client.getProjectByRepoUrl(
        "https://github.com/test/1",
      );

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        expect.stringContaining("lookup?repo_url="),
        expect.any(Object),
      );
      expect(result).toEqual(mockProject);
    });

    it("should return null when project not found", async () => {
      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: false,
        status: 404,
        text: () => Promise.resolve("Not found"),
      });

      const result = await client.getProjectByRepoUrl(
        "https://github.com/nonexistent",
      );
      expect(result).toBeNull();
    });
  });

  describe("triggerScan", () => {
    it("should trigger a scan operation", async () => {
      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ operation_id: "op-123" }),
      });

      const result = await client.triggerScan("project-1");

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        "http://localhost:7007/api/catalog/apme/projects/project-1/operation",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ action: "check", options: {} }),
        }),
      );
      expect(result).toEqual({
        scanId: "op-123",
        projectId: "project-1",
        status: "running",
      });
    });

    it("should handle 409 conflict when scan in progress", async () => {
      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: false,
        status: 409,
        text: () => Promise.resolve("Conflict"),
      });

      await expect(client.triggerScan("project-1")).rejects.toThrow(
        "A scan is already in progress",
      );
    });

    it("includes ansible_version in scan operation payload when provided", async () => {
      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ operation_id: "op-456" }),
      });

      await client.triggerScan("project-1", { ansibleVersion: "2.17" });

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        "http://localhost:7007/api/catalog/apme/projects/project-1/operation",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            action: "check",
            options: { ansible_version: "2.17" },
          }),
        }),
      );
    });

    it("forwards AI overrides and SCM tokens through the authenticated Portal route", async () => {
      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ operation_id: "op-789" }),
      });

      await client.triggerScan("project-1", {
        enableAi: true,
        scmToken: "user-token",
        userIdentity: { userEntityRef: "user:default/alice" },
      });

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        "http://localhost:7007/api/catalog/apme/projects/project-1/operation",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({ "X-SCM-Token": "user-token" }),
          body: JSON.stringify({
            action: "check",
            options: { enable_ai: true },
          }),
        }),
      );
    });
  });

  describe("validateRepoBranch", () => {
    it("calls the branch-check endpoint", async () => {
      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ valid: true }),
      });

      await client.validateRepoBranch("https://github.com/test/new", "main");

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        "http://localhost:7007/api/catalog/apme/repos/branch-check?repo_url=https%3A%2F%2Fgithub.com%2Ftest%2Fnew&branch=main",
        expect.any(Object),
      );
    });
  });

  describe("createProject", () => {
    it("should create a new project", async () => {
      const newProject = {
        id: "new-1",
        name: "New Project",
        repo_url: "https://github.com/test/new",
        branch: "main",
      };

      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(newProject),
      });

      const result = await client.createProject({
        name: "New Project",
        repo_url: "https://github.com/test/new",
        branch: "main",
      });

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        "http://localhost:7007/api/catalog/apme/projects",
        expect.objectContaining({
          method: "POST",
        }),
      );
      expect(result).toEqual(newProject);
    });
  });

  describe("getViolations", () => {
    it("should fetch violations for a project", async () => {
      const mockViolations = [
        { id: 1, rule_id: "L001", message: "Test violation", level: "medium" },
      ];

      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockViolations),
      });

      const result = await client.getViolations("project-1");

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        "http://localhost:7007/api/catalog/apme/projects/project-1/violations",
        expect.any(Object),
      );
      expect(result).toEqual(mockViolations);
    });
  });

  describe("getActivityDetail", () => {
    it("should fetch activity detail from catalog backend", async () => {
      const detail = {
        scan_id: "scan-1",
        scan_type: "remediate",
        status: "completed",
        proposals: [{ id: "p1", tier: 2 }],
        violations: [],
      };

      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(detail),
      });

      const result = await client.getActivityDetail("scan-1");

      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        "http://localhost:7007/api/catalog/apme/activity/scan-1",
        expect.any(Object),
      );
      expect(result).toEqual(detail);
    });
  });

  describe("getRules", () => {
    it("returns catalog Rule items without re-normalizing", async () => {
      const portalRules = {
        items: [
          {
            id: "M009",
            name: "M009",
            description: "Use loop instead of with_items",
            severity: "low",
            defaultSeverity: "high",
            category: "lint",
            remediationClass: 1,
            enabled: true,
            hasOverride: true,
          },
        ],
      };

      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(portalRules),
      });

      const result = await client.getRules();

      expect(result).toEqual(portalRules.items);
      expect(result[0]?.severity).toBe("low");
    });
  });

  describe("updateRuleConfig", () => {
    it("returns catalog Rule response without re-normalizing severity", async () => {
      const updatedRule = {
        id: "M009",
        name: "M009",
        description: "Use loop instead of with_items",
        severity: "low",
        defaultSeverity: "high",
        category: "lint",
        remediationClass: 1,
        enabled: true,
        hasOverride: true,
      };

      mockFetchApi.fetch.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(updatedRule),
      });

      const result = await client.updateRuleConfig("M009", {
        severity_override: 2,
      });

      expect(result).toEqual(updatedRule);
      expect(result.severity).toBe("low");
    });
  });

  describe("submitTimeoutMs", () => {
    function mockAbortableFetch() {
      mockFetchApi.fetch.mockImplementation(
        (_url: string, options?: RequestInit) =>
          new Promise((_resolve, reject) => {
            const signal = options?.signal;
            const abort = () => {
              const err = new Error("The operation was aborted");
              err.name = "AbortError";
              reject(err);
            };
            if (signal?.aborted) {
              abort();
              return;
            }
            signal?.addEventListener("abort", abort);
          }),
      );
    }

    beforeEach(() => {
      jest.useFakeTimers();
    });

    afterEach(() => {
      jest.useRealTimers();
    });

    it("defaults submitTimeoutMs to 300_000", async () => {
      mockAbortableFetch();
      const promise = client.submitRemediation("proj-1", "act-1");
      await Promise.resolve();
      await Promise.resolve();

      jest.advanceTimersByTime(299_999);
      let settled = false;
      promise.then(
        () => {
          settled = true;
        },
        () => {
          settled = true;
        },
      );
      await Promise.resolve();
      expect(settled).toBe(false);

      jest.advanceTimersByTime(1);
      await expect(promise).rejects.toThrow(
        /APME API request timed out or was aborted/,
      );
    });

    it("falls back to 300_000 for non-positive submitTimeoutMs", async () => {
      client = new ApmeApiClient({
        discoveryApi: mockDiscoveryApi,
        fetchApi: mockFetchApi,
        submitTimeoutMs: 0,
      });
      mockAbortableFetch();
      const promise = client.submitRemediation("proj-1", "act-1");
      await Promise.resolve();
      await Promise.resolve();

      jest.advanceTimersByTime(300_000);
      await expect(promise).rejects.toThrow(
        /APME API request timed out or was aborted/,
      );
    });

    it("honors custom submitTimeoutMs for pushRemediationBranch", async () => {
      client = new ApmeApiClient({
        discoveryApi: mockDiscoveryApi,
        fetchApi: mockFetchApi,
        submitTimeoutMs: 5_000,
      });
      mockAbortableFetch();
      const promise = client.pushRemediationBranch("proj-1", "act-1");
      await Promise.resolve();
      await Promise.resolve();

      jest.advanceTimersByTime(4_999);
      let settled = false;
      promise.then(
        () => {
          settled = true;
        },
        () => {
          settled = true;
        },
      );
      await Promise.resolve();
      expect(settled).toBe(false);

      jest.advanceTimersByTime(1);
      await expect(promise).rejects.toThrow(
        /APME API request timed out or was aborted/,
      );
    });
  });
});
