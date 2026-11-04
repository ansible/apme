/*
 * Copyright Red Hat
 */

import express from "express";
import request from "supertest";
import os from "os";
import path from "path";
import fs from "fs/promises";
import { ConfigReader } from "@backstage/config";
import { InputError, NotAllowedError } from "@backstage/errors";
import { AuthorizeResult } from "@backstage/plugin-permission-common";
import { createRouter } from "./router";
import { ApmePortalSettingsStore } from "./apmePortalSettingsStore";

describe("catalog-backend-module-apme router", () => {
  const mockApmeService = {
    getHealth: jest.fn(),
    getProjects: jest.fn(),
    getProject: jest.fn(),
    getProjectByRepoUrl: jest.fn(),
    getViolations: jest.fn(),
    getProjectDependencies: jest.fn(),
    getRules: jest.fn(),
    updateRuleConfig: jest.fn(),
    deleteRuleConfig: jest.fn(),
    createSuppression: jest.fn(),
    deleteSuppression: jest.fn(),
    getSuppressions: jest.fn(),
    triggerScan: jest.fn(),
    createProject: jest.fn(),
    deleteProject: jest.fn(),
    getActivity: jest.fn(),
    getActivityDetail: jest.fn(),
    getOperationState: jest.fn(),
    triggerRemediate: jest.fn(),
    approveProposals: jest.fn(),
    submitRemediation: jest.fn(),
    createPullRequest: jest.fn(),
    getAiModels: jest.fn(),
    getAiConfig: jest.fn(),
    updateAiConfig: jest.fn(),
    getAiProviders: jest.fn(),
    getAiEngines: jest.fn(),
    configureAiProvider: jest.fn(),
    deleteAiProvider: jest.fn(),
    listGalaxyServers: jest.fn(),
    createGalaxyServer: jest.fn(),
    updateGalaxyServer: jest.fn(),
    deleteGalaxyServer: jest.fn(),
  };

  const logger = {
    child: () => logger,
    info: jest.fn(),
    debug: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
  };

  const mockPermissions = {
    authorize: jest.fn().mockResolvedValue([{ result: AuthorizeResult.ALLOW }]),
    authorizeConditional: jest.fn().mockResolvedValue([]),
  };
  const authorizeProjectOperation = jest.fn();
  const authorizeRepositoryOperation = jest.fn();

  let app: express.Express;
  let settingsPath: string;
  let portalSettingsStore: ApmePortalSettingsStore;

  it("authorizes shared graph access before contacting the Gateway", async () => {
    authorizeProjectOperation.mockRejectedValueOnce(
      new NotAllowedError("Denied"),
    );
    (global.fetch as jest.Mock).mockReset();
    const response = await request(app).get(
      "/apme/shared/projects/proj-1/graph",
    );
    expect(response.status).toBe(403);
    expect(authorizeProjectOperation).toHaveBeenCalledWith("proj-1", {}, false);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("preserves certificate policy when updating a Galaxy server", async () => {
    mockApmeService.updateGalaxyServer.mockResolvedValue({
      id: 1,
      name: "hub",
      validate_certs: null,
    });
    const response = await request(app)
      .patch("/apme/settings/galaxy-servers/1")
      .send({ validate_certs: null });
    expect(response.status).toBe(200);
    expect(mockApmeService.updateGalaxyServer).toHaveBeenCalledWith(1, {
      validate_certs: null,
    });
  });

  it("rejects an invalid certificate policy rather than silently dropping it", async () => {
    const response = await request(app)
      .patch("/apme/settings/galaxy-servers/1")
      .send({ validate_certs: "false" });
    expect(response.status).toBe(400);
    expect(mockApmeService.updateGalaxyServer).not.toHaveBeenCalled();
  });

  it("rejects activity outside the authorized project", async () => {
    (global.fetch as jest.Mock).mockReset().mockResolvedValue({
      ok: true,
      json: async () => ({ project_id: "other-project", scan_id: "scan" }),
    });
    const response = await request(app).get(
      "/apme/shared/projects/proj-1/activity/scan",
    );
    expect(response.status).toBe(404);
    expect(response.body).not.toHaveProperty("scan_id");
    expect(authorizeProjectOperation).toHaveBeenCalledWith("proj-1", {}, false);
  });

  beforeEach(async () => {
    jest.resetAllMocks();
    mockPermissions.authorize.mockResolvedValue([
      { result: AuthorizeResult.ALLOW },
    ]);
    mockPermissions.authorizeConditional.mockResolvedValue([]);
    authorizeProjectOperation.mockReset().mockResolvedValue(undefined);
    authorizeRepositoryOperation.mockReset().mockResolvedValue(undefined);
    mockApmeService.getProject.mockResolvedValue({
      id: "proj-1",
      name: "terrible-playbook",
      repo_url: "https://github.com/acme/terrible-playbook",
      branch: "main",
    });
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ default_branch: "main" }),
      })
      .mockResolvedValueOnce({ ok: true }) as jest.Mock;
    settingsPath = path.join(
      os.tmpdir(),
      `apme-portal-settings-${Date.now()}-${Math.random()}.json`,
    );
    portalSettingsStore = new ApmePortalSettingsStore(settingsPath);
    const router = await createRouter({
      apmeService: mockApmeService as never,
      logger: logger as never,
      httpAuth: {
        credentials: jest.fn().mockResolvedValue({}),
      } as never,
      rootConfig: new ConfigReader({
        ansible: { apme: { enabled: true, baseUrl: "http://localhost:8080" } },
        integrations: {
          github: [{ host: "github.com", token: "test-token" }],
        },
      }),
      portalSettingsStore,
      permissions: mockPermissions as never,
      authorizeProjectOperation,
      authorizeRepositoryOperation,
    });
    app = express().use(router);
    app.use(
      (
        err: unknown,
        _req: express.Request,
        res: express.Response,
        next: express.NextFunction,
      ) => {
        if (err instanceof InputError) {
          res.status(400).json({ error: err.message });
          return;
        }
        if (err instanceof NotAllowedError) {
          res.status(403).json({ error: err.message });
          return;
        }
        next(err);
      },
    );
  });

  afterEach(async () => {
    await fs.unlink(settingsPath).catch(() => undefined);
  });

  describe("request validation and endpoint contracts", () => {
    it.each([
      [{}, "At least one"],
      [{ targetAnsibleCoreVersion: "" }, "non-empty string"],
      [{ targetAnsibleCoreVersion: 17 }, "non-empty string"],
      [
        { targetAnsibleCoreVersion: "not-supported" },
        "Unsupported ansible-core version",
      ],
      [{ defaultAiModelId: 42 }, "string or null"],
      [{ enableAi: "true" }, "must be a boolean"],
      [{ gatewayBaseUrl: 17 }, "string or null"],
    ])("rejects malformed global settings %j", async (body, message) => {
      const response = await request(app).put("/apme/settings").send(body);
      expect(response.status).toBe(400);
      expect(response.body.error).toContain(message);
    });

    it.each([
      [{}, "name is required"],
      [
        { name: "bad name", url: "https://galaxy.example.com" },
        "name must match",
      ],
      [{ name: "valid" }, "url is required"],
    ])("rejects malformed Galaxy server creation %j", async (body, message) => {
      const response = await request(app)
        .post("/apme/settings/galaxy-servers")
        .send(body);
      expect(response.status).toBe(400);
      expect(response.body.error).toContain(message);
      expect(mockApmeService.createGalaxyServer).not.toHaveBeenCalled();
    });

    it.each([
      ["invalid", {}, "serverId must be a number"],
      ["3", {}, "At least one field"],
      ["3", { name: "bad name" }, "name must match"],
      ["3", { url: "file:///etc/passwd" }, "url"],
      ["3", { auth_url: "file:///etc/passwd" }, "auth_url"],
    ])(
      "rejects invalid Galaxy updates to %s with %j",
      async (id, body, message) => {
        const response = await request(app)
          .patch(`/apme/settings/galaxy-servers/${id}`)
          .send(body);
        expect(response.status).toBe(400);
        expect(response.body.error).toContain(message);
        expect(mockApmeService.updateGalaxyServer).not.toHaveBeenCalled();
      },
    );

    it("forwards validated and normalized Galaxy updates and permits clearing authentication", async () => {
      const updated = {
        id: 3,
        name: "private",
        url: "https://galaxy.example.com/api/",
      };
      mockApmeService.updateGalaxyServer.mockResolvedValue(updated);
      const response = await request(app)
        .patch("/apme/settings/galaxy-servers/3")
        .send({
          name: " private ",
          url: "https://galaxy.example.com/api/",
          token: "test-token",
          auth_url: "https://login.example.com/token",
        });
      expect(response.status).toBe(200);
      expect(response.body).toEqual(updated);
      expect(mockApmeService.updateGalaxyServer).toHaveBeenCalledWith(3, {
        name: "private",
        url: updated.url,
        token: "test-token",
        auth_url: "https://login.example.com/token",
      });
      await request(app)
        .patch("/apme/settings/galaxy-servers/3")
        .send({ auth_url: "" })
        .expect(200);
      expect(mockApmeService.updateGalaxyServer).toHaveBeenLastCalledWith(3, {
        auth_url: "",
      });
    });

    it("validates optional Galaxy authentication on creation", async () => {
      mockApmeService.createGalaxyServer.mockResolvedValue({ id: 7 });
      await request(app)
        .post("/apme/settings/galaxy-servers")
        .send({
          name: "private",
          url: "https://galaxy.example.com",
          auth_url: "https://login.example.com/token",
        })
        .expect(201);
      expect(mockApmeService.createGalaxyServer).toHaveBeenCalledWith(
        expect.objectContaining({
          auth_url: "https://login.example.com/token",
        }),
      );
      await request(app)
        .delete("/apme/settings/galaxy-servers/invalid")
        .expect(400);
      expect(mockApmeService.deleteGalaxyServer).not.toHaveBeenCalled();
    });

    it.each([
      {},
      { targetAnsibleCoreVersion: "" },
      { targetAnsibleCoreVersion: 17 },
      { targetAnsibleCoreVersion: "not-supported" },
    ])("rejects malformed per-project scan targets %j", async (body) => {
      await request(app)
        .put("/apme/projects/proj-1/scan-target")
        .send(body)
        .expect(400);
    });

    it.each([
      ["", undefined],
      ["?limit=10", { limit: 10 }],
      ["?offset=20", { offset: 20 }],
      ["?limit=10&offset=20", { limit: 10, offset: 20 }],
      ["?limit=invalid&offset=invalid", undefined],
      ["?limit=10&offset=invalid", { limit: 10 }],
      ["?limit=invalid&offset=20", { offset: 20 }],
      ["?limit=1&limit=2&offset=0", { offset: 0 }],
    ])(
      "forwards only numeric violation pagination for %s",
      async (query, options) => {
        mockApmeService.getViolations.mockResolvedValue([
          { id: 1, rule_id: "R001" },
        ]);
        const response = await request(app).get(
          `/apme/projects/proj-1/violations${query}`,
        );
        expect(response.status).toBe(200);
        expect(response.body).toEqual([{ id: 1, rule_id: "R001" }]);
        expect(mockApmeService.getViolations).toHaveBeenCalledWith(
          "proj-1",
          options,
        );
      },
    );

    it("forwards authenticated reads and denies deletion without a host policy", async () => {
      mockApmeService.getHealth.mockResolvedValue({ status: "ok" });
      const health = await request(app).get("/apme/health").expect(200);
      expect(health.body).toEqual({ status: "ok" });
      const project = await request(app)
        .get("/apme/projects/proj-1")
        .expect(200);
      expect(project.body.id).toBe("proj-1");
      expect(authorizeProjectOperation).toHaveBeenCalledWith(
        "proj-1",
        {},
        false,
      );
      await request(app).delete("/apme/projects/proj-1").expect(403);
      expect(mockApmeService.deleteProject).not.toHaveBeenCalled();
    });

    it.each([undefined, "org/repo"])(
      "lists suppressions with scope %s",
      async (scope) => {
        mockApmeService.getSuppressions.mockResolvedValue([{ id: 3 }]);
        const response = await request(app)
          .get("/apme/suppressions")
          .query(scope === undefined ? {} : { scope });
        expect(response.status).toBe(200);
        expect(response.body).toEqual([{ id: 3 }]);
        expect(mockApmeService.getSuppressions).toHaveBeenCalledWith(scope);
      },
    );

    it("validates suppression identifiers before removing a suppression", async () => {
      await request(app).delete("/apme/suppressions/invalid").expect(400);
      expect(mockApmeService.deleteSuppression).not.toHaveBeenCalled();
      mockApmeService.getSuppressions.mockResolvedValueOnce([
        { id: 3, scope: "global" },
      ]);
      await request(app).delete("/apme/suppressions/3").expect(204);
      expect(mockApmeService.deleteSuppression).toHaveBeenCalledWith(3);
    });

    it("authorizes project-scoped suppression changes against that project", async () => {
      authorizeProjectOperation.mockRejectedValueOnce(
        new NotAllowedError("Project access denied"),
      );
      const create = await request(app).post("/apme/suppressions").send({
        rule_id: "R001",
        scope: "project:private-project",
      });
      expect(create.status).toBe(403);
      expect(mockApmeService.createSuppression).not.toHaveBeenCalled();

      mockApmeService.getSuppressions.mockResolvedValueOnce([
        { id: 7, scope: "project:private-project" },
      ]);
      authorizeProjectOperation.mockRejectedValueOnce(
        new NotAllowedError("Project access denied"),
      );
      const remove = await request(app).delete("/apme/suppressions/7");
      expect(remove.status).toBe(403);
      expect(mockApmeService.deleteSuppression).not.toHaveBeenCalled();
    });

    it("requires settings permission for global suppression mutations", async () => {
      mockPermissions.authorize.mockResolvedValueOnce([
        { result: AuthorizeResult.DENY },
      ]);
      const response = await request(app).post("/apme/suppressions").send({
        rule_id: "R001",
        scope: "global",
      });
      expect(response.status).toBe(403);
      expect(mockApmeService.createSuppression).not.toHaveBeenCalled();
    });

    it.each([
      {},
      { rule_id: 1 },
      { rule_id: "R001" },
      { rule_id: "R001", scope: 1 },
    ])("rejects malformed suppression creation %j", async (body) => {
      await request(app).post("/apme/suppressions").send(body).expect(400);
      expect(mockApmeService.createSuppression).not.toHaveBeenCalled();
    });

    it("rejects rule configuration without a supported field", async () => {
      await request(app)
        .put("/apme/rules/R001/config")
        .send({ unexpected: true })
        .expect(400);
      expect(mockApmeService.updateRuleConfig).not.toHaveBeenCalled();
    });

    it.each([
      ["/apme/lookup", {}],
      ["/apme/repos/branch-check", {}],
      [
        "/apme/repos/branch-check",
        { repo_url: "https://github.com/acme/repo" },
      ],
    ])(
      "rejects a missing repository/branch query on %s",
      async (url, query) => {
        await request(app).get(url).query(query).expect(400);
      },
    );

    it.each([
      {},
      { name: 17 },
      { name: "repo" },
      { name: "repo", repo_url: 17 },
      { name: "repo", repo_url: "https://github.com/acme/repo" },
    ])("rejects incomplete project registration %j", async (body) => {
      await request(app).post("/apme/projects").send(body).expect(400);
      expect(mockApmeService.createProject).not.toHaveBeenCalled();
    });

    it("registers a validated project and accepts an explicitly supplied SCM token", async () => {
      const body = {
        name: "repo",
        repo_url: "https://github.com/acme/repo",
        branch: "main",
        scm_token: "scoped-test-token",
      };
      mockApmeService.createProject.mockResolvedValue({
        id: "registered",
        ...body,
      });
      const response = await request(app)
        .post("/apme/projects")
        .send(body)
        .expect(201);
      expect(response.body.id).toBe("registered");
      expect(mockApmeService.createProject).toHaveBeenCalledWith(body);
    });
  });

  it("denies workflow forwarding when the host authorizer is missing", async () => {
    const router = await createRouter({
      apmeService: mockApmeService as never,
      logger: logger as never,
      httpAuth: { credentials: jest.fn().mockResolvedValue({}) } as never,
      rootConfig: new ConfigReader({
        ansible: { apme: { enabled: true, baseUrl: "http://localhost:8080" } },
      }),
      permissions: mockPermissions as never,
    });
    const isolatedApp = express()
      .use(router)
      .use(
        (
          error: Error,
          _req: express.Request,
          res: express.Response,
          _next: express.NextFunction,
        ) => {
          res
            .status(error instanceof NotAllowedError ? 403 : 500)
            .json({ error: error.message });
        },
      );
    const response = await request(isolatedApp)
      .post("/apme/projects/proj-1/operation")
      .send({});
    expect(response.status).toBe(403);
    expect(mockApmeService.getProject).not.toHaveBeenCalled();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it.each([
    ["post", "/apme/projects/proj-1/operation"],
    ["post", "/apme/projects/proj-1/operation/approve"],
    ["get", "/apme/projects/proj-1/operation/events"],
    ["post", "/apme/projects/proj-1/submit"],
  ] as const)(
    "denies %s %s before credential resolution or gateway forwarding",
    async (method, route) => {
      authorizeProjectOperation.mockRejectedValue(
        new NotAllowedError("Entitlement denied"),
      );
      const response = await request(app)
        [method](route)
        .send({ scm_token: "untrusted-token", activity_id: "activity-1" });
      expect(response.status).toBe(403);
      expect(authorizeProjectOperation).toHaveBeenCalledWith(
        "proj-1",
        expect.anything(),
        method !== "get",
      );
      expect(mockApmeService.getProject).not.toHaveBeenCalled();
      expect(mockApmeService.submitRemediation).not.toHaveBeenCalled();
      expect(global.fetch).not.toHaveBeenCalled();
    },
  );

  it("looks up projects by repo URL and branch", async () => {
    const project = {
      id: "proj-main",
      name: "terrible-playbook-main",
      repo_url: "https://github.com/acme/terrible-playbook",
      branch: "main",
    };
    mockApmeService.getProjectByRepoUrl.mockResolvedValueOnce(project);

    const response = await request(app).get(
      "/apme/lookup?repo_url=https%3A%2F%2Fgithub.com%2Facme%2Fterrible-playbook&branch=main",
    );

    expect(response.status).toBe(200);
    expect(mockApmeService.getProjectByRepoUrl).toHaveBeenCalledWith(
      "https://github.com/acme/terrible-playbook",
      "main",
    );
    expect(response.body).toEqual(project);
  });

  it("conceals a project lookup when its catalog resource is inaccessible", async () => {
    mockApmeService.getProjectByRepoUrl.mockResolvedValueOnce({
      id: "private-project",
      repo_url: "https://github.com/acme/private",
      branch: "main",
    });
    authorizeProjectOperation.mockRejectedValueOnce(
      new NotAllowedError("Repository access denied"),
    );
    const response = await request(app).get(
      "/apme/lookup?repo_url=https%3A%2F%2Fgithub.com%2Facme%2Fprivate",
    );
    expect(response.status).toBe(404);
    expect(response.body).toEqual({ error: "Project not found" });
  });

  it("returns 404 when lookup misses", async () => {
    mockApmeService.getProjectByRepoUrl.mockResolvedValueOnce(null);

    const response = await request(app).get(
      "/apme/lookup?repo_url=https%3A%2F%2Fgithub.com%2Facme%2Fmissing",
    );

    expect(response.status).toBe(404);
    expect(response.body).toEqual({ error: "Project not found" });
  });

  it("returns project dependencies", async () => {
    const deps = {
      ansible_core_version: "2.16.0",
      collections: [
        { fqcn: "ansible.posix", version: "1.5.4", source: "specified" },
      ],
      python_packages: [{ name: "cryptography", version: "41.0.0" }],
      requirements_files: ["requirements.txt"],
      dependency_tree: "",
    };
    mockApmeService.getProjectDependencies.mockResolvedValueOnce(deps);

    const response = await request(app).get(
      "/apme/projects/proj-1/dependencies",
    );

    expect(response.status).toBe(200);
    expect(mockApmeService.getProjectDependencies).toHaveBeenCalledWith(
      "proj-1",
    );
    expect(response.body).toEqual(deps);
  });

  it("creates a suppression", async () => {
    const suppression = {
      id: 1,
      fingerprint_hash: "abc",
      fingerprint_mode: "rule_only",
      rule_id: "R200",
      scope: "project:proj-1",
      reason: "Acknowledged from portal",
      created_by: "",
      created_at: "2026-07-06T00:00:00Z",
    };
    mockApmeService.createSuppression.mockResolvedValueOnce(suppression);

    const response = await request(app).post("/apme/suppressions").send({
      rule_id: "R200",
      fingerprint_mode: "rule_only",
      original_yaml: "",
      scope: "project:proj-1",
      reason: "Acknowledged from portal",
    });

    expect(response.status).toBe(201);
    expect(mockApmeService.createSuppression).toHaveBeenCalledWith({
      rule_id: "R200",
      fingerprint_mode: "rule_only",
      original_yaml: "",
      scope: "project:proj-1",
      reason: "Acknowledged from portal",
    });
    expect(response.body).toEqual(suppression);
  });

  it("submits remediation via gateway SCM endpoint", async () => {
    const submitResult = {
      branch_name: "apme/remediate-abc12345",
      commit_sha: "deadbeef",
      pr_url: "https://github.com/org/repo/pull/1",
      provider: "github",
    };
    mockApmeService.submitRemediation.mockResolvedValueOnce(submitResult);

    const response = await request(app)
      .post("/apme/projects/proj-1/submit")
      .set("X-SCM-Token", "ghp_test")
      .send({
        activity_id: "scan-1",
        create_pr: true,
      });

    expect(response.status).toBe(200);
    expect(mockApmeService.submitRemediation).toHaveBeenCalledWith("proj-1", {
      activity_id: "scan-1",
      branch_name: undefined,
      create_pr: true,
      title: undefined,
      body: undefined,
      scm_token: "ghp_test",
    });
    expect(response.body).toEqual(submitResult);

    const stored = await portalSettingsStore.read();
    expect(stored.activities?.["scan-1"]).toEqual({
      branch_name: "apme/remediate-abc12345",
      pr_url: "https://github.com/org/repo/pull/1",
      commit_sha: "deadbeef",
    });
  });

  it("merges persisted activity outcomes into GET activity", async () => {
    await portalSettingsStore.updateActivityOutcome("scan-rem-1", {
      branch_name: "apme/remediate-stored",
      pr_url: null,
    });

    const activity = [
      {
        scan_id: "scan-rem-1",
        scan_type: "remediate",
        created_at: "2026-07-08T00:00:00Z",
        remediated_count: 5,
      },
    ];
    mockApmeService.getActivity.mockResolvedValueOnce(activity);

    const response = await request(app).get("/apme/projects/proj-1/activity");

    expect(response.status).toBe(200);
    expect(response.body[0]).toEqual(
      expect.objectContaining({
        scan_id: "scan-rem-1",
        branch_name: "apme/remediate-stored",
        pr_url: null,
      }),
    );
  });

  it("resolves integration token when X-SCM-Token is absent", async () => {
    const submitResult = {
      branch_name: "apme/remediate-abc12345",
      commit_sha: "deadbeef",
      pr_url: "https://github.com/org/repo/pull/2",
      provider: "github",
    };
    mockApmeService.submitRemediation.mockResolvedValueOnce(submitResult);
    mockApmeService.getProject.mockResolvedValueOnce({
      id: "proj-1",
      name: "terrible-playbook",
      repo_url: "https://github.com/acme/terrible-playbook",
      branch: "main",
    });

    const response = await request(app)
      .post("/apme/projects/proj-1/submit")
      .send({
        activity_id: "scan-1",
        create_pr: true,
      });

    expect(response.status).toBe(200);
    expect(mockApmeService.getProject).toHaveBeenCalledWith("proj-1");
    expect(mockApmeService.submitRemediation).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({
        activity_id: "scan-1",
        create_pr: true,
        // ConfigReader in beforeEach has integrations.github token: test-token
        scm_token: "test-token",
      }),
    );
    expect(response.body).toEqual(submitResult);
  });

  it("returns project activity history", async () => {
    const activity = [
      {
        scan_id: "scan-1",
        scan_type: "check",
        status: "completed",
        created_at: "2026-07-08T00:00:00Z",
      },
    ];
    mockApmeService.getActivity.mockResolvedValueOnce(activity);

    const response = await request(app).get("/apme/projects/proj-1/activity");

    expect(response.status).toBe(200);
    expect(mockApmeService.getActivity).toHaveBeenCalledWith("proj-1");
    expect(response.body).toEqual(activity);
  });

  it("returns activity detail with proposals", async () => {
    const detail = {
      project_id: "proj-1",
      scan_id: "scan-1",
      scan_type: "remediate",
      status: "completed",
      proposals: [{ id: "p1", tier: 2 }],
      violations: [],
    };
    mockApmeService.getActivityDetail.mockResolvedValueOnce(detail);

    const response = await request(app).get("/apme/activity/scan-1");

    expect(response.status).toBe(200);
    expect(mockApmeService.getActivityDetail).toHaveBeenCalledWith("scan-1");
    expect(response.body).toEqual(detail);
  });

  it("merges persisted activity outcomes into GET activity detail", async () => {
    await portalSettingsStore.updateActivityOutcome("scan-rem-1", {
      branch_name: "apme/remediate-stored",
      pr_url: "https://github.com/org/repo/pull/9",
      commit_sha: "feedface",
    });

    const detail = {
      project_id: "proj-1",
      scan_id: "scan-rem-1",
      scan_type: "remediate",
      status: "completed",
      proposals: [],
      violations: [],
    };
    mockApmeService.getActivityDetail.mockResolvedValueOnce(detail);

    const response = await request(app).get("/apme/activity/scan-rem-1");

    expect(response.status).toBe(200);
    expect(response.body).toEqual(
      expect.objectContaining({
        scan_id: "scan-rem-1",
        branch_name: "apme/remediate-stored",
        pr_url: "https://github.com/org/repo/pull/9",
        commit_sha: "feedface",
      }),
    );
  });

  it("returns merged portal settings with persisted global default", async () => {
    await portalSettingsStore.updateGlobal("2.17");

    const response = await request(app).get("/apme/settings");

    expect(response.status).toBe(200);
    expect(response.body.targetAnsibleCoreVersion).toBe("2.17");
    expect(response.body.gatewayBaseUrl).toBe("http://localhost:8080");
  });

  it("persists global default via PUT /apme/settings", async () => {
    const response = await request(app)
      .put("/apme/settings")
      .send({ targetAnsibleCoreVersion: "2.18" });

    expect(response.status).toBe(200);
    expect(response.body.targetAnsibleCoreVersion).toBe("2.18");

    const stored = await portalSettingsStore.read();
    expect(stored.global?.targetAnsibleCoreVersion).toBe("2.18");
  });

  it("persists Gateway URL override via PUT /apme/settings", async () => {
    const response = await request(app)
      .put("/apme/settings")
      .send({ gatewayBaseUrl: "http://host.containers.internal:8080/" });

    expect(response.status).toBe(200);
    expect(response.body.gatewayBaseUrl).toBe(
      "http://host.containers.internal:8080",
    );

    const stored = await portalSettingsStore.read();
    expect(stored.global?.gatewayBaseUrl).toBe(
      "http://host.containers.internal:8080",
    );
  });

  it("rejects an invalid Gateway URL", async () => {
    const response = await request(app)
      .put("/apme/settings")
      .send({ gatewayBaseUrl: "ftp://not-valid" });

    expect(response.status).toBe(400);
  });

  it("clears Gateway URL override with null", async () => {
    await portalSettingsStore.updateGlobalSettings({
      gatewayBaseUrl: "http://host.containers.internal:8080",
    });

    const response = await request(app)
      .put("/apme/settings")
      .send({ gatewayBaseUrl: null });

    expect(response.status).toBe(200);
    expect(response.body.gatewayBaseUrl).toBe("http://localhost:8080");

    const stored = await portalSettingsStore.read();
    expect(stored.global?.gatewayBaseUrl).toBeUndefined();
  });

  it("persists default AI model via PUT /apme/settings", async () => {
    const response = await request(app)
      .put("/apme/settings")
      .send({ defaultAiModelId: "test/gpt-oss-120b" });

    expect(response.status).toBe(200);
    expect(response.body.defaultAiModelId).toBe("test/gpt-oss-120b");

    const stored = await portalSettingsStore.read();
    expect(stored.global?.defaultAiModelId).toBe("test/gpt-oss-120b");
  });

  it("persists enableAi override via PUT /apme/settings", async () => {
    const response = await request(app)
      .put("/apme/settings")
      .send({ enableAi: false });

    expect(response.status).toBe(200);
    expect(response.body.enableAi).toBe(false);
    const stored = await portalSettingsStore.read();
    expect(stored.global?.enableAi).toBe(false);
  });

  it("GET /apme/settings defaults enableAi to false when store unset", async () => {
    const unset = await request(app).get("/apme/settings");
    expect(unset.status).toBe(200);
    expect(unset.body.enableAi).toBe(false);

    await portalSettingsStore.updateGlobalSettings({ enableAi: true });

    const response = await request(app).get("/apme/settings");

    expect(response.status).toBe(200);
    expect(response.body.enableAi).toBe(true);
  });

  it("GET /apme/settings coerces invalid persisted enableAi to false", async () => {
    await fs.writeFile(
      settingsPath,
      JSON.stringify({ global: { enableAi: "false" } }),
    );
    portalSettingsStore.clearCache();

    const response = await request(app).get("/apme/settings");

    expect(response.status).toBe(200);
    expect(response.body.enableAi).toBe(false);
  });

  it("lists galaxy servers via GET /apme/settings/galaxy-servers", async () => {
    const servers = [
      {
        id: 1,
        name: "galaxy",
        url: "https://galaxy.ansible.com/api/",
        auth_url: "",
        has_token: false,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ];
    mockApmeService.listGalaxyServers.mockResolvedValueOnce(servers);

    const response = await request(app).get("/apme/settings/galaxy-servers");

    expect(response.status).toBe(200);
    expect(mockApmeService.listGalaxyServers).toHaveBeenCalled();
    expect(response.body).toEqual(servers);
  });

  it("creates galaxy server via POST /apme/settings/galaxy-servers", async () => {
    const created = {
      id: 2,
      name: "automation_hub",
      url: "https://console.redhat.com/api/automation-hub/",
      auth_url: "",
      has_token: true,
      created_at: "2026-01-02T00:00:00Z",
      updated_at: "2026-01-02T00:00:00Z",
    };
    mockApmeService.createGalaxyServer.mockResolvedValueOnce(created);

    const response = await request(app)
      .post("/apme/settings/galaxy-servers")
      .send({
        name: "automation_hub",
        url: "https://console.redhat.com/api/automation-hub/",
        token: "secret",
      });

    expect(response.status).toBe(201);
    expect(mockApmeService.createGalaxyServer).toHaveBeenCalledWith({
      name: "automation_hub",
      url: "https://console.redhat.com/api/automation-hub/",
      token: "secret",
      auth_url: undefined,
    });
    expect(response.body).toEqual(created);
  });

  it("deletes galaxy server via DELETE /apme/settings/galaxy-servers/:id", async () => {
    mockApmeService.deleteGalaxyServer.mockResolvedValueOnce(undefined);

    const response = await request(app).delete(
      "/apme/settings/galaxy-servers/3",
    );

    expect(response.status).toBe(204);
    expect(mockApmeService.deleteGalaxyServer).toHaveBeenCalledWith(3);
  });

  it("resolves per-project scan target with override", async () => {
    await portalSettingsStore.updateGlobal("2.17");
    await portalSettingsStore.updateProjectTarget("proj-1", "2.16");

    const response = await request(app).get(
      "/apme/projects/proj-1/scan-target",
    );

    expect(response.status).toBe(200);
    expect(response.body).toEqual({
      effective: "2.16",
      source: "project",
      globalDefault: "2.17",
      projectOverride: "2.16",
    });
  });

  it("clears project override when target is null", async () => {
    await portalSettingsStore.updateGlobal("2.17");
    await portalSettingsStore.updateProjectTarget("proj-1", "2.16");

    const response = await request(app)
      .put("/apme/projects/proj-1/scan-target")
      .send({ targetAnsibleCoreVersion: null });

    expect(response.status).toBe(200);
    expect(response.body.effective).toBe("2.17");
    expect(response.body.projectOverride).toBeUndefined();
  });

  it("passes resolved ansible_version when triggering scan", async () => {
    await portalSettingsStore.updateProjectTarget("proj-1", "2.16");
    (global.fetch as jest.Mock)
      .mockReset()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ default_branch: "main" }),
      })
      .mockResolvedValueOnce({ ok: true });
    (global.fetch as jest.Mock).mockReset().mockResolvedValueOnce({
      ok: true,
      status: 201,
      headers: new Headers({ "content-type": "application/json" }),
      body: new ReadableStream({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(JSON.stringify({ operation_id: "scan-1" })),
          );
          controller.close();
        },
      }),
    });

    const response = await request(app)
      .post("/apme/projects/proj-1/operation")
      .send({ action: "check" });

    expect(response.status).toBe(201);
    expect(response.body).toEqual({ operation_id: "scan-1" });
    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8080/api/v1/projects/proj-1/operation",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "X-SCM-Token": "test-token",
        }),
      }),
    );
    const body = JSON.parse(
      (global.fetch as jest.Mock).mock.calls[0][1].body as string,
    );
    expect(body.scm_token).toBe("test-token");
    expect(body.file_overrides).toBeUndefined();
  });

  it("proxies operation subpaths to the Gateway", async () => {
    (global.fetch as jest.Mock).mockReset().mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      text: async () => JSON.stringify({ status: "ok" }),
      body: null,
    });

    const response = await request(app)
      .post("/apme/projects/proj-1/operation/approve")
      .send({ approved_ids: ["p1"] });

    expect(response.status).toBe(200);
    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8080/api/v1/projects/proj-1/operation/approve",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("proxies operations to a stored Gateway URL override", async () => {
    await portalSettingsStore.updateGlobalSettings({
      gatewayBaseUrl: "http://host.containers.internal:8080",
    });
    (global.fetch as jest.Mock).mockReset().mockResolvedValueOnce({
      ok: true,
      status: 201,
      headers: new Headers({ "content-type": "application/json" }),
      text: async () => JSON.stringify({ operation_id: "scan-1" }),
      body: null,
    });

    const response = await request(app)
      .post("/apme/projects/proj-1/operation")
      .send({ action: "check" });

    expect(response.status).toBe(201);
    expect(global.fetch).toHaveBeenCalledWith(
      "http://host.containers.internal:8080/api/v1/projects/proj-1/operation",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("rejects file_overrides on legacy submit (ADR-056)", async () => {
    const response = await request(app)
      .post("/apme/projects/proj-1/submit")
      .send({
        activity_id: "act-1",
        file_overrides: { "playbook.yml": "---\n" },
      });

    expect(response.status).toBe(400);
    expect(mockApmeService.submitRemediation).not.toHaveBeenCalled();
  });

  it("enriches project list rows with severity_breakdown from detail", async () => {
    mockApmeService.getProjects.mockResolvedValueOnce([
      {
        id: "proj-1",
        name: "demo",
        repo_url: "https://github.com/acme/demo",
        branch: "main",
        total_violations: 5,
      },
    ]);
    mockApmeService.getProject.mockResolvedValueOnce({
      id: "proj-1",
      severity_breakdown: { critical: 5 },
    });

    const response = await request(app).get("/apme/projects");

    expect(response.status).toBe(200);
    expect(mockApmeService.getProject).toHaveBeenCalledWith("proj-1");
    expect(response.body.items[0].severity_breakdown).toEqual({
      critical: 5,
    });
  });

  it("filters project lists to catalog resources the user can read", async () => {
    mockApmeService.getProjects.mockResolvedValueOnce([
      {
        id: "visible",
        repo_url: "https://github.com/acme/visible",
        branch: "main",
        severity_breakdown: {},
      },
      {
        id: "private",
        repo_url: "https://github.com/acme/private",
        branch: "main",
        severity_breakdown: {},
      },
    ]);
    authorizeProjectOperation.mockImplementation(async (id: string) => {
      if (id === "private") throw new NotAllowedError("denied");
    });
    const response = await request(app).get("/apme/projects");
    expect(response.status).toBe(200);
    expect(response.body.items.map((item: { id: string }) => item.id)).toEqual([
      "visible",
    ]);
  });

  it("rejects createProject when the branch does not exist", async () => {
    (global.fetch as jest.Mock)
      .mockReset()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ default_branch: "master" }),
      })
      .mockResolvedValueOnce({ ok: false });

    const response = await request(app).post("/apme/projects").send({
      name: "test-repo",
      repo_url: "https://github.com/acme/terrible-playbook",
      branch: "main",
    });

    expect(response.status).toBe(400);
    expect(response.body.error).toContain("Requested branch is unavailable");
    expect(mockApmeService.createProject).not.toHaveBeenCalled();
  });

  it("validates branch via branch-check endpoint", async () => {
    (global.fetch as jest.Mock)
      .mockReset()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ default_branch: "main" }),
      })
      .mockResolvedValueOnce({ ok: true });

    const response = await request(app).get(
      "/apme/repos/branch-check?repo_url=https%3A%2F%2Fgithub.com%2Facme%2Fterrible-playbook&branch=main",
    );

    expect(response.status).toBe(200);
    expect(response.body).toEqual({ valid: true });
    expect(authorizeRepositoryOperation).toHaveBeenCalledWith(
      "https://github.com/acme/terrible-playbook",
      "main",
      {},
      "read",
    );
  });

  it("authorizes repository visibility before branch-check network access", async () => {
    authorizeRepositoryOperation.mockRejectedValueOnce(
      new NotAllowedError("Repository access denied"),
    );
    (global.fetch as jest.Mock).mockReset();
    const response = await request(app).get(
      "/apme/repos/branch-check?repo_url=https%3A%2F%2Fgithub.com%2Facme%2Frepo&branch=main",
    );
    expect(response.status).toBe(403);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("requires repository registration permission before creating a project", async () => {
    authorizeRepositoryOperation.mockRejectedValueOnce(
      new NotAllowedError("Registration denied"),
    );
    (global.fetch as jest.Mock).mockReset();
    const response = await request(app).post("/apme/projects").send({
      name: "repo",
      repo_url: "https://github.com/acme/repo",
      branch: "main",
    });
    expect(response.status).toBe(403);
    expect(authorizeRepositoryOperation).toHaveBeenCalledWith(
      "https://github.com/acme/repo",
      "main",
      {},
      "register",
    );
    expect(global.fetch).not.toHaveBeenCalled();
    expect(mockApmeService.createProject).not.toHaveBeenCalled();
  });

  it("does not return activity detail without a project authorization subject", async () => {
    mockApmeService.getActivityDetail.mockResolvedValueOnce({
      scan_id: "unknown",
      violations: [],
      proposals: [],
    });
    const response = await request(app).get("/apme/activity/unknown");
    expect(response.status).toBe(404);
    expect(response.body).toEqual({ error: "Activity not found" });
  });

  // US-016: AI provider CRUD routes
  it("returns normalised AI providers list", async () => {
    mockApmeService.getAiProviders.mockResolvedValueOnce([
      { id: "my-openrouter", engine: "openrouter", models: { "gpt-4o": {} } },
    ]);

    const response = await request(app).get("/apme/ai/providers");

    expect(response.status).toBe(200);
    expect(mockApmeService.getAiProviders).toHaveBeenCalled();
    expect(response.body).toEqual([
      { id: "my-openrouter", engine: "openrouter", models: ["gpt-4o"] },
    ]);
  });

  it("proxies configureAiProvider with apiKey + secretStore file (Abbenay ≥ v2026.8.6)", async () => {
    mockApmeService.configureAiProvider.mockResolvedValueOnce({ ok: true });

    const response = await request(app)
      .post("/apme/ai/provider/my-openrouter/configure")
      .send({
        engine: "openrouter",
        apiKey: "sk-xxx",
        secretStore: "file",
        secretName: "OPENROUTER_API_KEY",
      });

    expect(response.status).toBe(200);
    expect(mockApmeService.configureAiProvider).toHaveBeenCalledWith(
      "my-openrouter",
      {
        engine: "openrouter",
        apiKey: "sk-xxx",
        secretStore: "file",
        secretName: "OPENROUTER_API_KEY",
      },
    );
  });

  it("proxies configureAiProvider envVarName body through (deploy-time secrets)", async () => {
    mockApmeService.configureAiProvider.mockResolvedValueOnce({ ok: true });

    const response = await request(app)
      .post("/apme/ai/provider/my-openrouter/configure")
      .send({
        engine: "openrouter",
        envVarName: "OPENROUTER_API_KEY",
      });

    expect(response.status).toBe(200);
    expect(mockApmeService.configureAiProvider).toHaveBeenCalledWith(
      "my-openrouter",
      {
        engine: "openrouter",
        envVarName: "OPENROUTER_API_KEY",
      },
    );
  });

  it("rejects unsafe AI provider ids on configure", async () => {
    const response = await request(app)
      .post("/apme/ai/provider/bad;id/configure")
      .send({ engine: "openrouter", envVarName: "OPENROUTER_API_KEY" });

    expect(response.status).toBe(400);
    expect(mockApmeService.configureAiProvider).not.toHaveBeenCalled();
  });

  it("deletes an AI provider and returns 204", async () => {
    mockApmeService.deleteAiProvider.mockResolvedValueOnce(undefined);

    const response = await request(app).delete(
      "/apme/ai/provider/my-openrouter",
    );

    expect(response.status).toBe(204);
    expect(mockApmeService.deleteAiProvider).toHaveBeenCalledWith(
      "my-openrouter",
    );
  });

  it("returns AI status connected from Abbenay health when models list is empty", async () => {
    mockApmeService.getAiModels.mockResolvedValueOnce([]);
    mockApmeService.getAiConfig.mockRejectedValueOnce(new Error("config down"));
    mockApmeService.getAiEngines.mockRejectedValueOnce(
      new Error("engines down"),
    );
    mockApmeService.getHealth.mockResolvedValueOnce({
      status: "healthy",
      components: [{ name: "Abbenay AI", status: "ok" }],
    });

    const response = await request(app).get("/apme/ai/status");

    expect(response.status).toBe(200);
    expect(response.body.connected).toBe(true);
    expect(response.body.modelCount).toBe(0);
  });

  it("reports configuredModelCount without using it as inference modelCount", async () => {
    mockApmeService.getAiModels.mockResolvedValueOnce([]);
    mockApmeService.getAiConfig.mockResolvedValueOnce({
      config: {
        providers: {
          test: {
            engine: "openai",
            models: {
              "gpt-oss-120b": {},
              "deepseek-r1-distill-qwen-14b": {},
            },
          },
        },
      },
    });

    const response = await request(app).get("/apme/ai/status");

    expect(response.status).toBe(200);
    expect(response.body.connected).toBe(true);
    expect(response.body.modelCount).toBe(0);
    expect(response.body.configuredModelCount).toBe(2);
  });

  it("falls back to config models when inference list is empty (AAP-89202)", async () => {
    mockApmeService.getAiModels.mockResolvedValueOnce([]);
    mockApmeService.getAiConfig.mockResolvedValueOnce({
      config: {
        providers: {
          ollama: { engine: "ollama", models: { "qwen2.5:0.5b": {} } },
        },
      },
    });

    const response = await request(app).get("/apme/ai/models");

    expect(response.status).toBe(200);
    expect(response.body).toEqual([
      { id: "ollama/qwen2.5:0.5b", provider: "ollama", name: "qwen2.5:0.5b" },
    ]);
    expect(mockApmeService.getAiConfig).toHaveBeenCalled();
  });

  it("returns empty AI models when both inference and config are empty", async () => {
    mockApmeService.getAiModels.mockResolvedValueOnce([]);
    mockApmeService.getAiConfig.mockResolvedValueOnce({
      config: { providers: {} },
    });

    const response = await request(app).get("/apme/ai/models");

    expect(response.status).toBe(200);
    expect(response.body).toEqual([]);
  });

  it("returns AI config", async () => {
    const cfg = { config: { providers: {} } };
    mockApmeService.getAiConfig.mockResolvedValueOnce(cfg);

    const response = await request(app).get("/apme/ai/config");

    expect(response.status).toBe(200);
    expect(response.body).toEqual(cfg);
  });

  it("returns AI engines list from GET /apme/ai/engines", async () => {
    const engines = {
      engines: [
        { id: "openai", requiresKey: true, defaultEnvVar: "OPENAI_API_KEY" },
        {
          id: "ollama",
          requiresKey: false,
          defaultBaseUrl: "http://localhost:11434",
        },
        { id: "redhat", requiresKey: true },
      ],
    };
    mockApmeService.getAiEngines.mockResolvedValueOnce(engines);

    const response = await request(app).get("/apme/ai/engines");

    expect(response.status).toBe(200);
    expect(mockApmeService.getAiEngines).toHaveBeenCalled();
    expect(response.body).toEqual(engines);
  });

  /**
   * Regression: APME shares the catalog plugin HTTP stack. A global
   * `router.use(json())` consumes POST bodies before core catalog routes
   * (e.g. /entities/by-refs) can parse them → 500 "stream is not readable".
   * This test fails if that anti-pattern is reintroduced.
   */
  it("does not consume request bodies for non-APME catalog POSTs (by-refs)", async () => {
    const composed = express();
    composed.use(app);
    composed.post("/entities/by-refs", express.json(), (req, res) => {
      res.status(200).json({ entityRefs: req.body?.entityRefs ?? null });
    });

    const response = await request(composed)
      .post("/entities/by-refs")
      .set("Content-Type", "application/json")
      .send({ entityRefs: ["component:default/demo-repo"] });

    expect(response.status).toBe(200);
    expect(response.body.entityRefs).toEqual(["component:default/demo-repo"]);
  });

  it("updates rule config via PUT /apme/rules/:ruleId/config", async () => {
    const updated = { id: "M001", enabled: false, hasOverride: true };
    mockApmeService.updateRuleConfig.mockResolvedValueOnce(updated);

    const response = await request(app)
      .put("/apme/rules/M001/config")
      .send({ enabled_override: false });

    expect(response.status).toBe(200);
    expect(mockApmeService.updateRuleConfig).toHaveBeenCalledWith("M001", {
      enabled_override: false,
    });
    expect(response.body).toEqual(updated);
  });

  it("resets rule config via DELETE /apme/rules/:ruleId/config", async () => {
    mockApmeService.deleteRuleConfig.mockResolvedValueOnce(undefined);

    const response = await request(app).delete("/apme/rules/M001/config");

    expect(response.status).toBe(204);
    expect(mockApmeService.deleteRuleConfig).toHaveBeenCalledWith("M001");
  });

  it("returns 403 on PUT /apme/settings when permission is DENY", async () => {
    mockPermissions.authorize.mockResolvedValueOnce([
      { result: AuthorizeResult.DENY },
    ]);

    const response = await request(app)
      .put("/apme/settings")
      .send({ targetAnsibleCoreVersion: "2.18" });

    expect(response.status).toBe(403);
    expect(response.body.error).toContain(
      "ansible.settings.edit permission required for capability apme",
    );
  });

  it("returns 403 on POST /apme/settings/galaxy-servers when permission is DENY", async () => {
    mockPermissions.authorize.mockResolvedValueOnce([
      { result: AuthorizeResult.DENY },
    ]);

    const response = await request(app)
      .post("/apme/settings/galaxy-servers")
      .send({ name: "test", url: "https://galaxy.example.com/api/" });

    expect(response.status).toBe(403);
    expect(response.body.error).toContain(
      "ansible.settings.edit permission required for capability apme",
    );
  });

  it("returns 403 on PUT /apme/rules/:ruleId/config when permission is DENY", async () => {
    mockPermissions.authorize.mockResolvedValueOnce([
      { result: AuthorizeResult.DENY },
    ]);

    const response = await request(app)
      .put("/apme/rules/M001/config")
      .send({ enabled_override: false });

    expect(response.status).toBe(403);
    expect(mockApmeService.updateRuleConfig).not.toHaveBeenCalled();
    expect(response.body.error).toContain(
      "ansible.settings.edit permission required for capability apme",
    );
  });

  it("returns 403 on DELETE /apme/rules/:ruleId/config when permission is DENY", async () => {
    mockPermissions.authorize.mockResolvedValueOnce([
      { result: AuthorizeResult.DENY },
    ]);

    const response = await request(app).delete("/apme/rules/M001/config");

    expect(response.status).toBe(403);
    expect(mockApmeService.deleteRuleConfig).not.toHaveBeenCalled();
    expect(response.body.error).toContain(
      "ansible.settings.edit permission required for capability apme",
    );
  });

  it("still allows GET /apme/rules regardless of settings permission", async () => {
    mockPermissions.authorize.mockResolvedValueOnce([
      { result: AuthorizeResult.DENY },
    ]);
    mockApmeService.getRules.mockResolvedValueOnce([{ id: "M001" }]);

    const response = await request(app).get("/apme/rules");

    expect(response.status).toBe(200);
    expect(response.body).toEqual({ items: [{ id: "M001" }] });
  });

  it.each([
    "/apme/settings/galaxy-servers",
    "/apme/ai/config",
    "/apme/ai/engines",
  ])(
    "returns 403 on GET %s when view permission is DENY",
    async (route: string) => {
      mockPermissions.authorize.mockResolvedValueOnce([
        { result: AuthorizeResult.DENY },
      ]);

      const response = await request(app).get(route);

      expect(response.status).toBe(403);
      expect(response.body.error).toContain(
        "ansible.settings.view permission required for capability apme",
      );
    },
  );

  it("still allows shared GET routes regardless of settings view permission", async () => {
    mockPermissions.authorize.mockResolvedValue([
      { result: AuthorizeResult.DENY },
    ]);

    mockApmeService.getAiModels.mockResolvedValueOnce([]);
    const modelsRes = await request(app).get("/apme/ai/models");
    expect(modelsRes.status).toBe(200);

    mockApmeService.getAiProviders.mockResolvedValueOnce([]);
    const providersRes = await request(app).get("/apme/ai/providers");
    expect(providersRes.status).toBe(200);

    const settingsRes = await request(app).get("/apme/settings");
    expect(settingsRes.status).toBe(200);

    // Restore default ALLOW for subsequent tests
    mockPermissions.authorize.mockResolvedValue([
      { result: AuthorizeResult.ALLOW },
    ]);
  });
});

describe("project deletion authorization", () => {
  async function setupDeletion(
    authorizeProjectDeletion?: (
      projectId: string,
      credentials: unknown,
    ) => Promise<void>,
  ) {
    const credentials = {
      principal: { type: "user", userEntityRef: "user:default/owner" },
    };
    const authenticate = jest.fn().mockResolvedValue(credentials);
    const deleteProject = jest.fn().mockResolvedValue(undefined);
    const logger = {
      info: jest.fn(),
      debug: jest.fn(),
      warn: jest.fn(),
      error: jest.fn(),
    };
    const router = await createRouter({
      apmeService: { deleteProject } as never,
      logger: logger as never,
      httpAuth: { credentials: authenticate } as never,
      rootConfig: new ConfigReader({
        ansible: { apme: { enabled: true, baseUrl: "http://localhost:8080" } },
      }),
      permissions: {} as never,
      authorizeProjectDeletion,
    });
    const app = express().use(router);
    app.use(
      (
        error: unknown,
        _req: express.Request,
        res: express.Response,
        _next: express.NextFunction,
      ) => {
        res
          .status(error instanceof NotAllowedError ? 403 : 500)
          .json({ error: String(error) });
      },
    );
    return { app, credentials, authenticate, deleteProject };
  }

  it("denies deletion without a dedicated host authorization policy", async () => {
    const { app, deleteProject } = await setupDeletion();
    await request(app).delete("/apme/projects/other-project").expect(403);
    expect(deleteProject).not.toHaveBeenCalled();
  });

  it("does not forward a deletion denied by the host", async () => {
    const authorize = jest
      .fn()
      .mockRejectedValue(new NotAllowedError("Repository deletion denied"));
    const { app, credentials, deleteProject } = await setupDeletion(authorize);
    await request(app).delete("/apme/projects/other-project").expect(403);
    expect(authorize).toHaveBeenCalledWith("other-project", credentials);
    expect(deleteProject).not.toHaveBeenCalled();
  });

  it("authenticates before invoking the deletion policy", async () => {
    const authorize = jest.fn().mockResolvedValue(undefined);
    const { app, authenticate, deleteProject } = await setupDeletion(authorize);
    authenticate.mockRejectedValueOnce(new NotAllowedError("User required"));
    await request(app).delete("/apme/projects/proj-1").expect(403);
    expect(authorize).not.toHaveBeenCalled();
    expect(deleteProject).not.toHaveBeenCalled();
  });

  it("awaits the dedicated policy before forwarding an authorized deletion", async () => {
    let deleteProject: jest.Mock;
    const authorize = jest.fn(async () => {
      await Promise.resolve();
      expect(deleteProject).not.toHaveBeenCalled();
    });
    const state = await setupDeletion(authorize);
    deleteProject = state.deleteProject;
    await request(state.app).delete("/apme/projects/proj-1").expect(204);
    expect(authorize).toHaveBeenCalledWith("proj-1", state.credentials);
    expect(deleteProject).toHaveBeenCalledWith("proj-1");
    expect(deleteProject).toHaveBeenCalledTimes(1);
  });
});
