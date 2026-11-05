import { lastPathSegment, MockApmeApiClient } from "./MockApmeApiClient";
import { MOCK_ACTIVITY, MOCK_PROJECTS, MOCK_RULES } from "./fixtures";

async function settle<T>(promise: Promise<T>): Promise<T> {
  const outcome = promise.then(
    (value) => ({ value }),
    (error) => ({ error }),
  );
  await jest.runAllTimersAsync();
  const result = await outcome;
  if ("error" in result) throw result.error;
  return result.value;
}

describe("lastPathSegment", () => {
  it("returns the last item or an empty string", () => {
    expect(lastPathSegment([])).toBe("");
    expect(lastPathSegment(["prop", "12"])).toBe("12");
  });
});

describe("MockApmeApiClient behavior", () => {
  let client: MockApmeApiClient;
  const project = MOCK_PROJECTS[0];

  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    client = new MockApmeApiClient();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it("exposes health, project summaries, and defensive project copies", async () => {
    expect(await settle(client.getHealth())).toHaveProperty("status");
    const projects = await settle(client.getProjects());
    expect(projects.length).toBeGreaterThan(0);
    expect(projects[0]).toHaveProperty("violationCounts");
    const copy = await settle(client.getProject(project.id));
    copy.name = "modified copy";
    expect((await settle(client.getProject(project.id))).name).toBe(
      project.name,
    );
    await expect(settle(client.getProject("missing"))).rejects.toThrow(
      "Project not found",
    );
  });

  it("resolves repositories by normalized URL and branch", async () => {
    expect(
      await settle(
        client.getProjectByRepoUrl(
          `url:${project.repo_url}.git`,
          project.branch,
        ),
      ),
    ).toMatchObject({ id: project.id });
    expect(
      await settle(
        client.getProjectByRepoUrl(project.repo_url, "missing-branch"),
      ),
    ).toBeNull();
    expect(
      await settle(
        client.getProjectByRepoUrl("https://example.test/no/such-repo"),
      ),
    ).toBeNull();
    expect(
      await settle(client.getProjectByRepoUrl(project.repo_url)),
    ).toMatchObject({ id: project.id });
  });

  it("supports violation pagination and dependency results", async () => {
    const rows = await settle(client.getViolations(project.id));
    expect(
      await settle(client.getViolations(project.id, { offset: 1, limit: 2 })),
    ).toEqual(rows.slice(1, 3));
    expect(
      await settle(client.getViolations(project.id, { offset: 2 })),
    ).toEqual(rows.slice(2));
    expect(await settle(client.getViolations("missing"))).toEqual([]);
    expect(
      await settle(client.getProjectDependencies(project.id)),
    ).toHaveProperty("collections");
    expect(
      (await settle(client.getProjectDependencies("missing"))).python_packages,
    ).toEqual([]);
  });

  it("updates global settings and project overrides without affecting other projects", async () => {
    expect((await settle(client.getProjectScanTarget(project.id))).source).toBe(
      "global",
    );
    const settings = await settle(
      client.updatePortalSettings({
        targetAnsibleCoreVersion: "2.18",
        enableAi: false,
        defaultAiModelId: null,
        gatewayBaseUrl: "  ",
      }),
    );
    expect(settings).toMatchObject({
      targetAnsibleCoreVersion: "2.18",
      enableAi: false,
      defaultAiModelId: undefined,
      gatewayBaseUrl: undefined,
    });
    expect(
      await settle(
        client.updateProjectScanTarget(project.id, {
          targetAnsibleCoreVersion: "2.17",
        }),
      ),
    ).toMatchObject({ source: "project", effective: "2.17" });
    expect(
      await settle(
        client.updateProjectScanTarget(project.id, {
          targetAnsibleCoreVersion: null,
        }),
      ),
    ).toMatchObject({ source: "global", effective: "2.18" });
    expect(
      await settle(
        client.updatePortalSettings({
          gatewayBaseUrl: " https://gateway.test/ ",
          defaultAiModelId: "provider/model",
        }),
      ),
    ).toMatchObject({
      gatewayBaseUrl: "https://gateway.test/",
      defaultAiModelId: "provider/model",
    });
  });

  it("exposes the AI fallback configuration and provider operations", async () => {
    expect(await settle(client.getAiStatus())).toMatchObject({
      connected: true,
      modelCount: 1,
    });
    expect(await settle(client.getAiModels())).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: "mock-model" })]),
    );
    expect(await settle(client.getAiConfig())).toHaveProperty("providers");
    expect(await settle(client.getAiProviders())).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "mock-provider" }),
      ]),
    );
    expect(await settle(client.getAiEngines())).toHaveProperty("engines");
    const config = { providers: { test: {} } };
    expect(await settle(client.updateAiConfig(config))).toEqual(config);
    expect(await settle(client.configureAiProvider("test", config))).toEqual(
      config,
    );
    await expect(
      settle(client.deleteAiProvider("test")),
    ).resolves.toBeUndefined();
  });

  it("creates and deletes projects and validates repository branches", async () => {
    const created = await settle(
      client.createProject({
        name: "new",
        repo_url: "https://example.test/org/repo",
        scm_token: "test-token",
        branch: "devel",
      }),
    );
    expect(created).toMatchObject({ branch: "devel", has_scm_token: true });
    const defaults = await settle(
      client.createProject({
        name: "defaults",
        repo_url: "https://example.test/org/defaults",
      }),
    );
    expect(defaults).toMatchObject({ branch: "main", has_scm_token: false });
    await settle(client.validateRepoBranch(created.repo_url, "devel"));
    await settle(client.deleteProject(created.id));
    await expect(settle(client.getProject(created.id))).rejects.toThrow(
      "Project not found",
    );
  });

  it("creates, updates, and deletes Galaxy servers including token changes", async () => {
    expect(await settle(client.listGalaxyServers())).toHaveLength(1);
    const server = await settle(
      client.createGalaxyServer({
        name: "private",
        url: "https://galaxy.test/api/",
        token: "token",
        auth_url: "https://galaxy.test/auth",
      }),
    );
    expect(server.has_token).toBe(true);
    expect(
      await settle(client.updateGalaxyServer(server.id, {})),
    ).toMatchObject({ name: "private", has_token: true });
    expect(
      await settle(
        client.updateGalaxyServer(server.id, {
          name: "renamed",
          url: "https://other.test/api/",
          auth_url: "",
          token: " ",
        }),
      ),
    ).toMatchObject({ name: "renamed", has_token: false });
    await expect(settle(client.updateGalaxyServer(-1, {}))).rejects.toThrow(
      "not found",
    );
    await settle(client.deleteGalaxyServer(server.id));
    expect(await settle(client.listGalaxyServers())).toHaveLength(1);
    expect(
      (
        await settle(
          client.createGalaxyServer({
            name: "public",
            url: "https://public.test/",
          }),
        )
      ).has_token,
    ).toBe(false);
  });

  it("maintains suppression scope and deletion", async () => {
    const suppression = await settle(
      client.createSuppression({ rule_id: "RULE001", scope: project.id }),
    );
    expect(suppression).toMatchObject({
      fingerprint_hash: "mock",
      fingerprint_mode: "full",
      reason: "",
    });
    await settle(
      client.createSuppression({
        rule_id: "RULE002",
        scope: "global",
        fingerprint_hash: "hash",
        fingerprint_mode: "rule_only",
        reason: "Reviewed",
      }),
    );
    expect(await settle(client.getSuppressions())).toHaveLength(2);
    expect(await settle(client.getSuppressions(project.id))).toHaveLength(1);
    await settle(client.deleteSuppression(suppression.id));
    expect(await settle(client.getSuppressions(project.id))).toEqual([]);
  });

  it("applies and clears rule overrides", async () => {
    const rule = MOCK_RULES[0];
    const original = { ...rule };
    try {
      expect((await settle(client.getRules())).length).toBeGreaterThan(0);
      expect(
        await settle(
          client.updateRuleConfig(rule.id, {
            enabled_override: false,
            enforced: true,
            severity_override: 2,
          }),
        ),
      ).toMatchObject({ enabled: false, enforced: true, hasOverride: true });
      await settle(client.deleteRuleConfig(rule.id));
      expect(rule).toMatchObject({ hasOverride: false, enforced: false });
      await expect(
        settle(client.updateRuleConfig("missing", {})),
      ).rejects.toThrow("not found");
      await settle(client.deleteRuleConfig("missing"));
    } finally {
      for (const key of Object.keys(rule))
        delete (rule as unknown as Record<string, unknown>)[key];
      Object.assign(rule, original);
    }
  });

  it("returns activity details and a safe empty detail for unknown scans", async () => {
    const activities = await settle(client.getActivity(project.id));
    expect(activities).toEqual(MOCK_ACTIVITY[project.id] ?? []);
    const activity = Object.values(MOCK_ACTIVITY).flat()[0];
    expect(activity).toBeDefined();
    expect(
      await settle(client.getActivityDetail(activity.scan_id)),
    ).toMatchObject({ scan_id: activity.scan_id });
    expect(await settle(client.getActivity("missing"))).toEqual([]);
    expect(await settle(client.getActivityDetail("missing"))).toMatchObject({
      scan_id: "missing",
      violations: [],
      proposals: [],
    });
  });

  it("reports scan phases and commits completion state", async () => {
    expect(await settle(client.getOperationState(project.id))).toBeNull();
    expect(await settle(client.triggerScan(project.id))).toMatchObject({
      projectId: project.id,
      status: "running",
    });
    expect(await settle(client.getOperationState(project.id))).toMatchObject({
      phase: "cloning",
    });
    await jest.advanceTimersByTimeAsync(2500);
    expect(await settle(client.getOperationState(project.id))).toMatchObject({
      phase: "scanning",
    });
    await jest.advanceTimersByTimeAsync(4000);
    expect(await settle(client.getOperationState(project.id))).toMatchObject({
      phase: "analyzing",
    });
    await jest.advanceTimersByTimeAsync(10000);
    expect(await settle(client.getOperationState(project.id))).toBeNull();
    expect(
      (await settle(client.getProject(project.id))).active_operation,
    ).toBeNull();
  });

  it("generates remediation proposals, applies approvals, and completes", async () => {
    await settle(client.triggerRemediate(project.id));
    expect(await settle(client.getOperationState(project.id))).toMatchObject({
      phase: "generating",
    });
    await jest.advanceTimersByTimeAsync(5000);
    const state = await settle(client.getOperationState(project.id));
    expect(state?.proposals).toEqual(expect.any(Array));
    const ids = state?.proposals?.map((proposal) => proposal.id) ?? [];
    await settle(client.approveProposals(project.id, ids));
    await jest.advanceTimersByTimeAsync(10000);
    expect(await settle(client.getOperationState(project.id))).toBeNull();
    await settle(client.approveProposals("missing", ["prop-1"]));
  });

  it("publishes either a branch or a PR with the requested branch name", async () => {
    expect(
      await settle(
        client.pushRemediationBranch(project.id, "activity-123", {
          branchName: "review/fix",
        }),
      ),
    ).toMatchObject({ branch_name: "review/fix", pr_url: null });
    expect(
      await settle(client.createPullRequest(project.id, "activity-123")),
    ).toMatchObject({
      branch_name: "apme/remediate-activity",
      provider: "github",
    });
    expect(
      (await settle(client.submitRemediation("missing", "another"))).pr_url,
    ).toContain("/rhel-patching/pull/");
  });
});
