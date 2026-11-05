import { ApmeApiClient } from "./ApmeApi";

describe("APME frontend HTTP contracts", () => {
  const discoveryApi = { getBaseUrl: jest.fn() };
  const fetchApi = { fetch: jest.fn() };
  let client: ApmeApiClient;
  beforeEach(() => {
    jest.resetAllMocks();
    discoveryApi.getBaseUrl.mockResolvedValue("/catalog");
    client = new ApmeApiClient({ discoveryApi, fetchApi });
  });
  afterEach(() => {
    jest.clearAllTimers();
    jest.useRealTimers();
  });

  const endpoints: Array<{
    name: string;
    path: string;
    method?: string;
    body?: unknown;
    payload: unknown;
    call: (api: ApmeApiClient) => Promise<unknown>;
  }> = [
    {
      name: "Galaxy list",
      path: "/settings/galaxy-servers",
      payload: [],
      call: (api) => api.listGalaxyServers(),
    },
    {
      name: "Galaxy create",
      path: "/settings/galaxy-servers",
      method: "POST",
      body: { name: "private", url: "https://galaxy.test" },
      payload: { id: 7 },
      call: (api) =>
        api.createGalaxyServer({ name: "private", url: "https://galaxy.test" }),
    },
    {
      name: "Galaxy update",
      path: "/settings/galaxy-servers/7",
      method: "PATCH",
      body: { name: "private" },
      payload: { id: 7 },
      call: (api) => api.updateGalaxyServer(7, { name: "private" }),
    },
    {
      name: "scan target",
      path: "/projects/team%2Frepo/scan-target",
      payload: { effective: "2.18" },
      call: (api) => api.getProjectScanTarget("team/repo"),
    },
    {
      name: "scan target update",
      path: "/projects/team%2Frepo/scan-target",
      method: "PUT",
      body: { targetAnsibleCoreVersion: null },
      payload: { effective: "2.18" },
      call: (api) =>
        api.updateProjectScanTarget("team/repo", {
          targetAnsibleCoreVersion: null,
        }),
    },
    {
      name: "AI status",
      path: "/ai/status",
      payload: { connected: true },
      call: (api) => api.getAiStatus(),
    },
    {
      name: "AI models",
      path: "/ai/models",
      payload: [{ id: "model", provider: "openai", name: "Model" }],
      call: (api) => api.getAiModels(),
    },
    {
      name: "AI config",
      path: "/ai/config",
      payload: { providers: {} },
      call: (api) => api.getAiConfig(),
    },
    {
      name: "AI config update",
      path: "/ai/config",
      method: "POST",
      body: { providers: {} },
      payload: { providers: {} },
      call: (api) => api.updateAiConfig({ providers: {} }),
    },
    {
      name: "AI providers",
      path: "/ai/providers",
      payload: [],
      call: (api) => api.getAiProviders(),
    },
    {
      name: "AI provider configure",
      path: "/ai/provider/team%2Fprovider/configure",
      method: "POST",
      body: { engine: "openai", secretStore: "file" },
      payload: { status: "ok" },
      call: (api) =>
        api.configureAiProvider("team/provider", {
          engine: "openai",
          secretStore: "file",
        }),
    },
    {
      name: "AI engines",
      path: "/ai/engines",
      payload: { engines: [] },
      call: (api) => api.getAiEngines(),
    },
    {
      name: "project",
      path: "/projects/team%2Frepo",
      payload: { id: "team/repo" },
      call: (api) => api.getProject("team/repo"),
    },
    {
      name: "dependencies",
      path: "/projects/team%2Frepo/dependencies",
      payload: { collections: [], python: [] },
      call: (api) => api.getProjectDependencies("team/repo"),
    },
    {
      name: "suppression create",
      path: "/suppressions",
      method: "POST",
      body: { rule_id: "R001", scope: "team/repo" },
      payload: { id: 7 },
      call: (api) =>
        api.createSuppression({ rule_id: "R001", scope: "team/repo" }),
    },
    {
      name: "suppressions",
      path: "/suppressions",
      payload: [],
      call: (api) => api.getSuppressions(),
    },
    {
      name: "empty suppression scope",
      path: "/suppressions",
      payload: [],
      call: (api) => api.getSuppressions(""),
    },
    {
      name: "scoped suppressions",
      path: "/suppressions?scope=team%2Frepo",
      payload: [],
      call: (api) => api.getSuppressions("team/repo"),
    },
    {
      name: "activity",
      path: "/projects/team%2Frepo/activity",
      payload: [],
      call: (api) => api.getActivity("team/repo"),
    },
    {
      name: "operation state",
      path: "/projects/team%2Frepo/operation",
      payload: { status: "running" },
      call: (api) => api.getOperationState("team/repo"),
    },
  ];
  it.each(endpoints)(
    "binds the $name endpoint and serializes only the request body",
    async ({ path, method, body, payload, call }) => {
      fetchApi.fetch.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => payload,
      });
      await expect(call(client)).resolves.toEqual(payload);
      expect(discoveryApi.getBaseUrl).toHaveBeenCalledWith("catalog");
      expect(fetchApi.fetch).toHaveBeenCalledWith(
        `/catalog/apme${path}`,
        expect.objectContaining({
          headers: { "Content-Type": "application/json" },
          signal: expect.any(AbortSignal),
          ...(method ? { method, body: JSON.stringify(body) } : {}),
        }),
      );
    },
  );

  it.each([
    {
      path: "/settings/galaxy-servers/7",
      call: (api: ApmeApiClient) => api.deleteGalaxyServer(7),
    },
    {
      path: "/ai/provider/team%2Fprovider",
      call: (api: ApmeApiClient) => api.deleteAiProvider("team/provider"),
    },
    {
      path: "/rules/R%2F001/config",
      call: (api: ApmeApiClient) => api.deleteRuleConfig("R/001"),
    },
    {
      path: "/suppressions/7",
      call: (api: ApmeApiClient) => api.deleteSuppression(7),
    },
    {
      path: "/projects/team%2Frepo",
      call: (api: ApmeApiClient) => api.deleteProject("team/repo"),
    },
  ])(
    "handles a no-content DELETE $path without parsing JSON",
    async ({ path, call }) => {
      const json = jest.fn();
      fetchApi.fetch.mockResolvedValue({ ok: true, status: 204, json });
      await expect(call(client)).resolves.toBeUndefined();
      expect(json).not.toHaveBeenCalled();
      expect(fetchApi.fetch).toHaveBeenCalledWith(
        `/catalog/apme${path}`,
        expect.objectContaining({ method: "DELETE" }),
      );
    },
  );

  it.each([{ limit: 0, offset: 0 }, { limit: 10 }, { offset: 20 }])(
    "preserves violation pagination including zero values %j",
    async (options) => {
      fetchApi.fetch.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => null,
      });
      await expect(client.getViolations("repo", options)).resolves.toEqual([]);
      const query = new URLSearchParams(
        Object.entries(options).map(([key, value]) => [key, String(value)]),
      );
      expect(fetchApi.fetch).toHaveBeenCalledWith(
        `/catalog/apme/projects/repo/violations?${query}`,
        expect.any(Object),
      );
    },
  );

  it.each([{ ids: undefined }, { ids: [] }, { ids: [1, 2] }])(
    "serializes remediation selection $ids and returns a scan reference",
    async ({ ids }) => {
      fetchApi.fetch.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ operation_id: "scan" }),
      });
      await expect(client.triggerRemediate("repo", ids)).resolves.toEqual({
        scanId: "scan",
        projectId: "repo",
        status: "running",
      });
      expect(fetchApi.fetch).toHaveBeenCalledWith(
        "/catalog/apme/projects/repo/operation",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            action: "remediate",
            options: ids?.length ? { violation_ids: ids } : {},
          }),
        }),
      );
    },
  );

  it("forwards proposal approval and SCM publication context without dropping the create-PR flag", async () => {
    fetchApi.fetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        pr_url: "https://github.com/team/repo/pull/1",
        branch_name: "fix",
        provider: "github",
        commit_sha: "abc",
      }),
    });
    await client.approveProposals("team/repo", ["proposal"]);
    expect(fetchApi.fetch).toHaveBeenLastCalledWith(
      "/catalog/apme/projects/team%2Frepo/operation/approve",
      expect.objectContaining({
        method: "POST",
        body: '{"approved_ids":["proposal"]}',
      }),
    );
    await expect(
      client.createPullRequest("repo", "activity", {
        scmToken: " token ",
        branchName: "fix",
      }),
    ).resolves.toMatchObject({
      pr_url: "https://github.com/team/repo/pull/1",
      commit_sha: "abc",
    });
    expect(fetchApi.fetch).toHaveBeenLastCalledWith(
      "/catalog/apme/projects/repo/submit",
      expect.objectContaining({
        headers: { "Content-Type": "application/json", "X-SCM-Token": "token" },
        body: '{"activity_id":"activity","branch_name":"fix","create_pr":true}',
      }),
    );
    await client.pushRemediationBranch("repo", "activity", { scmToken: " " });
    expect(fetchApi.fetch).toHaveBeenLastCalledWith(
      "/catalog/apme/projects/repo/submit",
      expect.objectContaining({
        headers: { "Content-Type": "application/json" },
        body: '{"activity_id":"activity","create_pr":false}',
      }),
    );
  });

  it("does not claim a pull request was created when submission returned no URL", async () => {
    fetchApi.fetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    await expect(client.createPullRequest("repo", "activity")).rejects.toThrow(
      "Submit completed without a PR URL",
    );
  });

  it.each([
    "conflict-detail",
    "conflict-empty",
    "error-empty",
    "original-error",
  ] as const)("preserves the correct error for %s", async (scenario) => {
    const original = new Error("Gateway rejected the request");
    if (scenario === "original-error")
      fetchApi.fetch.mockRejectedValue(original);
    else
      fetchApi.fetch.mockResolvedValue({
        ok: false,
        status: scenario === "error-empty" ? 500 : 409,
        statusText: "Server Error",
        text: async () =>
          scenario === "conflict-detail" ? "Policy conflict" : "",
      });
    const operation = client.getAiConfig();
    const messages = {
      "original-error": original.message,
      "conflict-detail": "APME API conflict: Policy conflict",
      "conflict-empty": "APME API conflict: request could not be completed",
      "error-empty": "APME API error: 500 - Server Error",
    };
    const expected = messages[scenario];
    await expect(operation).rejects.toThrow(expected);
  });

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY, 23.9])(
    "enforces a validated submission timeout for %s",
    async (timeout) => {
      jest.useFakeTimers();
      client = new ApmeApiClient({
        discoveryApi,
        fetchApi,
        submitTimeoutMs: timeout,
      });
      let started!: () => void;
      const requestStarted = new Promise<void>((resolve) => {
        started = resolve;
      });
      fetchApi.fetch.mockImplementation(
        (_url: string, options: RequestInit) => {
          started();
          return new Promise((_resolve, reject) => {
            options.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError")),
            );
          });
        },
      );
      const outcome = client
        .submitRemediation("repo", "activity")
        .catch((error: unknown) => error);
      await requestStarted;
      const expected = timeout === 23.9 ? 23 : 300_000;
      await jest.advanceTimersByTimeAsync(expected - 1);
      expect(fetchApi.fetch.mock.calls[0][1].signal.aborted).toBe(false);
      await jest.advanceTimersByTimeAsync(1);
      await expect(outcome).resolves.toMatchObject({
        message: expect.stringContaining("timed out or was aborted"),
      });
      expect(jest.getTimerCount()).toBe(0);
    },
  );
});
