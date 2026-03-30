import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

describe("api service", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function jsonResponse(data: unknown, status = 200) {
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(data),
      text: () => Promise.resolve(JSON.stringify(data)),
    });
  }

  it("getHealth calls /api/v1/health", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({ status: "ok", database: "ok", components: [] }));
    const { getHealth } = await import("../services/api");
    const result = await getHealth();
    expect(result.status).toBe("ok");
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/health", expect.objectContaining({ headers: { Accept: "application/json" } }));
  });

  it("listActivity calls /api/v1/activity with pagination", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({ total: 0, limit: 10, offset: 0, items: [] }));
    const { listActivity } = await import("../services/api");
    await listActivity(10, 5);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/activity?limit=10&offset=5", expect.anything());
  });

  it("listActivity appends session_id filter", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({ total: 0, limit: 10, offset: 0, items: [] }));
    const { listActivity } = await import("../services/api");
    await listActivity(10, 0, "abc123");
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/activity?limit=10&offset=0&session_id=abc123", expect.anything());
  });

  it("getActivity calls correct path", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({ scan_id: "s1" }));
    const { getActivity } = await import("../services/api");
    await getActivity("s1");
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/activity/s1", expect.anything());
  });

  it("deleteActivity calls DELETE", async () => {
    mockFetch.mockReturnValueOnce(Promise.resolve({ ok: true, status: 204 }));
    const { deleteActivity } = await import("../services/api");
    await deleteActivity("s1");
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/activity/s1", { method: "DELETE" });
  });

  it("deleteActivity throws on failure", async () => {
    mockFetch.mockReturnValueOnce(Promise.resolve({ ok: false, status: 404 }));
    const { deleteActivity } = await import("../services/api");
    await expect(deleteActivity("s1")).rejects.toThrow("404");
  });

  it("getSession calls correct path", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({ session_id: "sess1", scans: [] }));
    const { getSession } = await import("../services/api");
    const result = await getSession("sess1");
    expect(result.session_id).toBe("sess1");
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/sessions/sess1", expect.anything());
  });

  it("getSessionTrend calls correct path", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse([{ scan_id: "s1", total_violations: 10 }]));
    const { getSessionTrend } = await import("../services/api");
    const result = await getSessionTrend("sess1");
    expect(result).toHaveLength(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/sessions/sess1/trend", expect.anything());
  });

  it("request throws on non-ok response", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse("Not Found", 404));
    const { getHealth } = await import("../services/api");
    await expect(getHealth()).rejects.toThrow("404");
  });

  it("listAiModels calls /api/v1/ai/models", async () => {
    const models = [
      { id: "openai/gpt-4o", provider: "openai", name: "gpt-4o" },
      { id: "anthropic/claude-sonnet-4", provider: "anthropic", name: "claude-sonnet-4" },
    ];
    mockFetch.mockReturnValueOnce(jsonResponse(models));
    const { listAiModels } = await import("../services/api");
    const result = await listAiModels();
    expect(result).toHaveLength(2);
    expect(result[0]!.id).toBe("openai/gpt-4o");
    expect(result[1]!.provider).toBe("anthropic");
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/ai/models", expect.objectContaining({ headers: { Accept: "application/json" } }));
  });

  it("listAiModels returns empty array on empty response", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse([]));
    const { listAiModels } = await import("../services/api");
    const result = await listAiModels();
    expect(result).toHaveLength(0);
  });

  // ADR-040 Dependencies API tests

  it("getProjectDependencies calls correct path", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({
      ansible_core_version: "2.16.0",
      collections: [],
      python_packages: [],
      requirements_files: [],
      dependency_tree: "",
    }));
    const { getProjectDependencies } = await import("../services/api");
    const result = await getProjectDependencies("proj-123");
    expect(result.ansible_core_version).toBe("2.16.0");
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/projects/proj-123/dependencies", expect.anything());
  });

  it("getProjectDependencies encodes project name in path", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({
      ansible_core_version: "2.16.0",
      collections: [],
      python_packages: [],
      requirements_files: [],
      dependency_tree: "",
    }));
    const { getProjectDependencies } = await import("../services/api");
    await getProjectDependencies("My Project/01");
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/projects/My%20Project%2F01/dependencies",
      expect.anything(),
    );
  });

  it("listCollections calls correct path with pagination", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse([
      { fqcn: "community.general", version: "8.0.0", source: "galaxy", project_count: 5 },
    ]));
    const { listCollections } = await import("../services/api");
    const result = await listCollections(100, 10);
    expect(result).toHaveLength(1);
    expect(result[0]!.fqcn).toBe("community.general");
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/collections?limit=100&offset=10", expect.anything());
  });

  it("getCollectionDetail encodes FQCN in path", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({
      fqcn: "community/general",
      versions: ["8.0.0"],
      source: "galaxy",
      project_count: 3,
      projects: [],
    }));
    const { getCollectionDetail } = await import("../services/api");
    await getCollectionDetail("community/general");
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/collections/community%2Fgeneral", expect.anything());
  });

  it("listPythonPackages calls correct path with pagination", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse([
      { name: "jmespath", version: "1.0.1", project_count: 2 },
    ]));
    const { listPythonPackages } = await import("../services/api");
    const result = await listPythonPackages(50, 0);
    expect(result).toHaveLength(1);
    expect(result[0]!.name).toBe("jmespath");
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/python-packages?limit=50&offset=0", expect.anything());
  });

  it("getPythonPackageDetail encodes package name in path", async () => {
    mockFetch.mockReturnValueOnce(jsonResponse({
      name: "my package",
      versions: ["2.16.0"],
      project_count: 5,
      projects: [],
    }));
    const { getPythonPackageDetail } = await import("../services/api");
    await getPythonPackageDetail("my package");
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/python-packages/my%20package", expect.anything());
  });
});
