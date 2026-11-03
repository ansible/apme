import { ConfigReader } from "@backstage/config";
import { ApmeClient } from "./ApmeClient";

jest.mock("undici", () => ({ Agent: jest.fn() }));

describe("APME client endpoint contracts", () => {
  const logger = {
    child: () => logger,
    info: jest.fn(),
    debug: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
  };
  const originalFetch = global.fetch;
  let client: ApmeClient;
  let fetchMock: jest.Mock;
  const config = new ConfigReader({
    ansible: { apme: { enabled: true, baseUrl: "https://gateway.test" } },
  });

  beforeEach(() => {
    fetchMock = jest
      .fn()
      .mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    global.fetch = fetchMock;
    client = new ApmeClient({ rootConfig: config, logger: logger as never });
  });
  afterEach(() => {
    global.fetch = originalFetch;
  });

  const reads: Array<{
    name: string;
    path: string;
    payload: unknown;
    call: (service: ApmeClient) => Promise<unknown>;
  }> = [
    {
      name: "health",
      path: "/api/v1/health",
      payload: { status: "healthy", version: "1.0.0" },
      call: (service) => service.getHealth(),
    },
    {
      name: "AI models",
      path: "/api/v1/ai/models",
      payload: [{ id: "model", name: "Test model", provider: "openai" }],
      call: (service) => service.getAiModels(),
    },
    {
      name: "AI config",
      path: "/api/v1/ai/config",
      payload: { providers: {} },
      call: (service) => service.getAiConfig(),
    },
    {
      name: "AI providers",
      path: "/api/v1/ai/providers",
      payload: [{ id: "openai", name: "OpenAI", configured: true }],
      call: (service) => service.getAiProviders(),
    },
    {
      name: "AI engines",
      path: "/api/v1/ai/engines",
      payload: { engines: [] },
      call: (service) => service.getAiEngines(),
    },
    {
      name: "dependencies",
      path: "/api/v1/projects/project/dependencies",
      payload: {
        collections: [],
        python: [],
        requirements_files: [],
        dependency_tree: "",
      },
      call: (service) => service.getProjectDependencies("project"),
    },
    {
      name: "scan status",
      path: "/api/v1/projects/project/scans/scan",
      payload: { scan_id: "scan", status: "completed" },
      call: (service) => service.getScanStatus("project", "scan"),
    },
    {
      name: "all suppressions",
      path: "/api/v1/suppressions",
      payload: [],
      call: (service) => service.getSuppressions(),
    },
    {
      name: "scoped suppressions",
      path: "/api/v1/suppressions?scope=org%2Frepo",
      payload: [],
      call: (service) => service.getSuppressions("org/repo"),
    },
    {
      name: "Galaxy servers",
      path: "/api/v1/settings/galaxy-servers",
      payload: [{ id: 7, name: "private", url: "https://galaxy.test/" }],
      call: (service) => service.listGalaxyServers(),
    },
  ];
  it.each(reads)(
    "uses the documented $name endpoint",
    async ({ path, call, payload }) => {
      fetchMock.mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => payload,
      });
      await expect(call(client)).resolves.toEqual(payload);
      expect(fetchMock).toHaveBeenCalledWith(
        `https://gateway.test${path}`,
        expect.objectContaining({
          headers: { "Content-Type": "application/json" },
        }),
      );
    },
  );

  it.each([
    {
      name: "AI models",
      call: (service: ApmeClient) => service.getAiModels(),
      fallback: [],
    },
    {
      name: "AI config",
      call: (service: ApmeClient) => service.getAiConfig(),
      fallback: {},
    },
    {
      name: "AI providers",
      call: (service: ApmeClient) => service.getAiProviders(),
      fallback: [],
    },
    {
      name: "AI engines",
      call: (service: ApmeClient) => service.getAiEngines(),
      fallback: { engines: [] },
    },
  ])(
    "returns its supported fallback when $name is unavailable",
    async ({ call, fallback }) => {
      fetchMock.mockRejectedValueOnce(new Error("Gateway offline"));
      await expect(call(client)).resolves.toEqual(fallback);
    },
  );

  it("serializes provider and Galaxy-server writes and encodes identifiers", async () => {
    const configure = { engine: "openai", secretStore: "file" as const };
    await client.configureAiProvider("team/provider", configure);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://gateway.test/api/v1/ai/provider/team%2Fprovider/configure",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(configure),
      }),
    );
    await client.updateAiConfig({ providers: {} });
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://gateway.test/api/v1/ai/config",
      expect.objectContaining({ method: "POST", body: '{"providers":{}}' }),
    );
    await client.createGalaxyServer({
      name: "private",
      url: "https://galaxy.test/",
    });
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://gateway.test/api/v1/settings/galaxy-servers",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ name: "private", url: "https://galaxy.test/" }),
      }),
    );
    await client.updateGalaxyServer(7, { name: "renamed" });
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://gateway.test/api/v1/settings/galaxy-servers/7",
      expect.objectContaining({ method: "PATCH", body: '{"name":"renamed"}' }),
    );
  });

  it("handles successful no-content deletes", async () => {
    const json = jest.fn();
    fetchMock.mockResolvedValue({ ok: true, status: 204, json });
    await client.deleteAiProvider("team/provider");
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://gateway.test/api/v1/ai/provider/team%2Fprovider",
      expect.objectContaining({ method: "DELETE" }),
    );
    await client.deleteRuleConfig("rule/name");
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://gateway.test/api/v1/rules/rule%2Fname/config",
      expect.objectContaining({ method: "DELETE" }),
    );
    await client.deleteSuppression(3);
    await client.deleteGalaxyServer(7);
    await client.deleteProject("project");
    expect(json).not.toHaveBeenCalled();
  });

  it("normalizes missing list payloads and violations", async () => {
    await expect(client.getProjects()).resolves.toEqual([]);
    await expect(client.getActivity("project")).resolves.toEqual([]);
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => undefined,
    });
    await expect(
      client.getViolations("project", { limit: 2, offset: 1 }),
    ).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://gateway.test/api/v1/projects/project/violations?limit=2&offset=1",
      expect.anything(),
    );
  });

  it("applies late-bound gateway overrides and falls back for empty overrides", async () => {
    client.setResolveBaseUrl(async () => " https://override.test/// ");
    await client.getHealth();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://override.test/api/v1/health",
      expect.anything(),
    );
    client.setResolveBaseUrl(async () => " ");
    await client.getHealth();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://gateway.test/api/v1/health",
      expect.anything(),
    );
  });
});
