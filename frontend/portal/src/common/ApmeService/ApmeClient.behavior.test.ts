/*
 * Copyright Red Hat
 */

import { ConfigReader } from "@backstage/config";
import { ConflictError, InputError, NotFoundError } from "@backstage/errors";
import { ApmeClient } from "./ApmeClient";

jest.mock("undici", () => ({ Agent: jest.fn() }));

describe("ApmeClient request and response behavior", () => {
  const originalFetch = global.fetch;
  const logger = {
    child: () => logger,
    info: jest.fn(),
    debug: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
  };
  let client: ApmeClient;
  let fetchMock: jest.Mock;

  function respond(payload: unknown) {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => payload,
    });
  }

  function fail(status: number, body = "", statusText = "Failure") {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status,
      statusText,
      text: async () => body,
    });
  }

  beforeEach(() => {
    jest.clearAllMocks();
    fetchMock = jest.fn();
    global.fetch = fetchMock;
    client = new ApmeClient({
      rootConfig: new ConfigReader({
        ansible: { apme: { baseUrl: "https://gateway.test///" } },
      }),
      logger: logger as never,
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    jest.useRealTimers();
  });

  it.each([
    { name: "scan", call: (service: ApmeClient) => service.triggerScan("p") },
    {
      name: "remediation",
      call: (service: ApmeClient) => service.triggerRemediate("p"),
    },
  ])("reports an active operation conflict for $name", async ({ call }) => {
    fail(409, "internal conflict details");
    await expect(call(client)).rejects.toEqual(
      new ConflictError("A scan is already in progress for this project"),
    );
    expect(logger.error).not.toHaveBeenCalled();
  });

  it.each([
    {
      body: "Project already registered",
      message: "Project already registered",
    },
    { body: "", message: "Request conflicted with existing state" },
  ])("preserves create conflicts: $message", async ({ body, message }) => {
    fail(409, body);
    await expect(
      client.createProject({ name: "repo", repo_url: "https://git.test/repo" }),
    ).rejects.toEqual(new ConflictError(message));
    expect(logger.error).not.toHaveBeenCalled();
  });

  it("preserves operation-state GET conflict details", async () => {
    fail(409, "State is locked");
    await expect(client.getOperationState("p")).rejects.toEqual(
      new ConflictError("State is locked"),
    );
  });

  it("returns null only for missing operation state", async () => {
    fail(404);
    await expect(client.getOperationState("p")).resolves.toBeNull();
    fail(403, "Access denied", "Forbidden");
    await expect(client.getOperationState("p")).rejects.toEqual(
      new InputError("APME request failed: 403 Forbidden - Access denied"),
    );
  });

  it("propagates missing resources outside supported lookup fallbacks", async () => {
    fail(404);
    await expect(client.getProject("missing")).rejects.toEqual(
      new NotFoundError("Resource not found: /api/v1/projects/missing"),
    );
    expect(logger.error).not.toHaveBeenCalled();
  });

  it("reports server failures with their status and body and logs the cause", async () => {
    fail(503, "Gateway unavailable", "Service Unavailable");
    await expect(client.getHealth()).rejects.toEqual(
      new InputError(
        "Failed to connect to APME: APME request failed: 503 Service Unavailable - Gateway unavailable",
      ),
    );
    expect(logger.error).toHaveBeenCalledWith(
      "APME request failed: /api/v1/health",
      new Error(
        "APME request failed: 503 Service Unavailable - Gateway unavailable",
      ),
    );
  });

  it("wraps transport failures and logs the original cause", async () => {
    const error = new Error("Connection refused");
    fetchMock.mockRejectedValueOnce(error);
    await expect(client.getHealth()).rejects.toEqual(
      new InputError("Failed to connect to APME: Connection refused"),
    );
    expect(logger.error).toHaveBeenCalledWith(
      "APME request failed: /api/v1/health",
      error,
    );
  });

  it("propagates JSON parsing failures", async () => {
    const error = new SyntaxError("Malformed JSON");
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => {
        throw error;
      },
    });
    await expect(client.getHealth()).rejects.toBe(error);
  });

  it("propagates URL resolver failures without making a network request", async () => {
    const error = new Error("Settings unavailable");
    client.setResolveBaseUrl(async () => {
      throw error;
    });
    await expect(client.getHealth()).rejects.toBe(error);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("falls back to the configured URL when the resolver has no override", async () => {
    client.setResolveBaseUrl(async () => undefined);
    respond({ status: "healthy" });
    await expect(client.getHealth()).resolves.toEqual({ status: "healthy" });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/v1/health",
      expect.anything(),
    );
  });

  it("aborts ordinary requests after 30 seconds and releases their timer", async () => {
    jest.useFakeTimers();
    fetchMock.mockImplementationOnce(
      (_url: string, options: RequestInit) =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener("abort", () =>
            reject(new Error("Aborted")),
          );
        }),
    );
    const outcome = client.getHealth().catch((error) => error);
    jest.advanceTimersByTime(29_999);
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    expect(signal.aborted).toBe(false);
    jest.advanceTimersByTime(1);
    expect(await outcome).toEqual(
      new InputError("Failed to connect to APME: Aborted"),
    );
    expect(signal.aborted).toBe(true);
    expect(jest.getTimerCount()).toBe(0);
  });

  it("clears the timeout after a successful response", async () => {
    jest.useFakeTimers();
    respond({ status: "healthy" });
    await client.getHealth();
    expect(jest.getTimerCount()).toBe(0);
    jest.advanceTimersByTime(30_000);
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(false);
  });

  it.each([undefined, ""])(
    "omits an absent lookup branch (%s)",
    async (branch) => {
      const project = { id: "p", branch: "main" };
      respond(project);
      await expect(
        client.getProjectByRepoUrl("https://git.test/a b", branch),
      ).resolves.toEqual(project);
      expect(fetchMock).toHaveBeenCalledWith(
        "https://gateway.test/api/v1/projects/lookup?repo_url=https%3A%2F%2Fgit.test%2Fa%20b",
        expect.anything(),
      );
    },
  );

  it("omits an empty suppression scope", async () => {
    respond([]);
    await expect(client.getSuppressions("")).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/v1/suppressions",
      expect.anything(),
    );
  });

  it("preserves zero pagination values and normalizes violation classes", async () => {
    respond([
      { id: 1, remediation_class: "auto-fixable" },
      { id: 2, remediation_class: 3 },
    ]);
    await expect(
      client.getViolations("p", { limit: 0, offset: 0 }),
    ).resolves.toEqual([
      { id: 1, remediation_class: 1 },
      { id: 2, remediation_class: 3 },
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/v1/projects/p/violations?limit=0&offset=0",
      expect.anything(),
    );
  });

  it.each([
    {
      name: "array",
      payload: [{ rule_id: "R1", resolved_severity_label: "HIGH" }],
    },
    {
      name: "items",
      payload: { items: [{ rule_id: "R1", resolved_severity_label: "HIGH" }] },
    },
  ])("normalizes rules from the $name response", async ({ payload }) => {
    respond(payload);
    await expect(client.getRules()).resolves.toEqual([
      expect.objectContaining({ id: "R1", name: "R1", severity: "high" }),
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/v1/rules",
      expect.anything(),
    );
  });

  it("returns an empty list for rules without items", async () => {
    respond({});
    await expect(client.getRules()).resolves.toEqual([]);
  });

  it("serializes rule overrides and normalizes the updated rule", async () => {
    const body = {
      enabled_override: false,
      severity_override: null,
      enforced: true,
    };
    respond({
      rule_id: "rule/name",
      resolved_enabled: false,
      resolved_severity_label: "LOW",
      override: body,
    });
    await expect(client.updateRuleConfig("rule/name", body)).resolves.toEqual(
      expect.objectContaining({
        id: "rule/name",
        enabled: false,
        severity: "low",
        enforced: true,
        hasOverride: true,
      }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/v1/rules/rule%2Fname/config",
      expect.objectContaining({ method: "PUT", body: JSON.stringify(body) }),
    );
  });

  it("creates suppressions with their scope and reason", async () => {
    const body = {
      rule_id: "R1",
      scope: "org/repo",
      reason: "Reviewed exception",
    };
    respond({ id: 7, ...body });
    await expect(client.createSuppression(body)).resolves.toEqual({
      id: 7,
      ...body,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/v1/suppressions",
      expect.objectContaining({ method: "POST", body: JSON.stringify(body) }),
    );
  });

  it("creates projects with their branch and SCM credentials", async () => {
    const body = {
      name: "repo",
      repo_url: "https://git.test/repo",
      branch: "feature/check",
      scm_token: "test-token",
    };
    respond({ id: "p", ...body });
    await expect(client.createProject(body)).resolves.toEqual({
      id: "p",
      ...body,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/v1/projects",
      expect.objectContaining({ method: "POST", body: JSON.stringify(body) }),
    );
  });

  it("unwraps project and activity lists and returns operation state", async () => {
    respond({ items: [{ id: "p" }] });
    await expect(client.getProjects()).resolves.toEqual([{ id: "p" }]);
    respond({ items: [{ scan_id: "scan" }] });
    await expect(client.getActivity("p")).resolves.toEqual([
      { scan_id: "scan" },
    ]);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://gateway.test/api/v1/projects/p/activity",
      expect.anything(),
    );
    respond({ operation_id: "op", status: "running" });
    await expect(client.getOperationState("p")).resolves.toEqual({
      operation_id: "op",
      status: "running",
    });
    expect(fetchMock).toHaveBeenLastCalledWith(
      "https://gateway.test/api/v1/projects/p/operation",
      expect.anything(),
    );
  });

  it.each([undefined, null])(
    "normalizes absent activity violations (%s)",
    async (violations) => {
      respond({ scan_id: "scan", violations, proposals: [] });
      await expect(client.getActivityDetail("scan")).resolves.toEqual({
        scan_id: "scan",
        violations: [],
        proposals: [],
      });
    },
  );

  it("sends scan identity, organization, AI, version and SCM options", async () => {
    respond({ operation_id: "op" });
    await expect(
      client.triggerScan("p", {
        ansibleVersion: " 2.18 ",
        enableAi: true,
        scmToken: "test-token",
        userIdentity: {
          userEntityRef: "user:default/alice",
          orgEntityRef: "group:default/team",
        },
      }),
    ).resolves.toEqual({ scanId: "op", projectId: "p", status: "running" });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/v1/projects/p/operation",
      expect.objectContaining({
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-User": "user:default/alice",
          "X-Org": "group:default/team",
        },
        body: JSON.stringify({
          action: "check",
          options: { enable_ai: true, ansible_version: "2.18" },
          scm_token: "test-token",
        }),
      }),
    );
  });

  it("sends only the user header when no organization is supplied", async () => {
    respond({ operation_id: "op" });
    await client.triggerScan("p", {
      ansibleVersion: " ",
      enableAi: false,
      userIdentity: { userEntityRef: "user:default/alice" },
    });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        headers: {
          "Content-Type": "application/json",
          "X-User": "user:default/alice",
        },
        body: JSON.stringify({
          action: "check",
          options: { enable_ai: false },
        }),
      }),
    );
  });

  it("remediates selected violations with trimmed SCM and version options", async () => {
    respond({ operation_id: "op" });
    await expect(
      client.triggerRemediate("p", [4, 8], {
        scmToken: " test-token ",
        ansibleVersion: " 2.18 ",
        enableAi: true,
      }),
    ).resolves.toEqual({ scanId: "op", projectId: "p", status: "running" });
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/v1/projects/p/operation",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          action: "remediate",
          options: {
            enable_ai: true,
            ansible_version: "2.18",
            violation_ids: [4, 8],
          },
          scm_token: "test-token",
        }),
      }),
    );
  });

  it.each([undefined, []])(
    "remediates all violations when selection is %j",
    async (ids) => {
      respond({ operation_id: "op" });
      await client.triggerRemediate("p", ids, {
        scmToken: " ",
        ansibleVersion: " ",
      });
      expect(fetchMock).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          body: JSON.stringify({
            action: "remediate",
            options: { enable_ai: false },
          }),
        }),
      );
    },
  );

  it("approves the selected proposals and accepts a no-content response", async () => {
    const json = jest.fn();
    fetchMock.mockResolvedValueOnce({ ok: true, status: 204, json });
    await expect(
      client.approveProposals("p", ["proposal-1", "proposal-2"]),
    ).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(
      "https://gateway.test/api/v1/projects/p/operation/approve",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ approved_ids: ["proposal-1", "proposal-2"] }),
      }),
    );
    expect(json).not.toHaveBeenCalled();
  });

  it.each([
    { token: undefined, branch: undefined, extra: {} },
    {
      token: "test-token",
      branch: "fix/quality",
      extra: { scm_token: "test-token", branch_name: "fix/quality" },
    },
  ])(
    "creates a pull request with optional arguments %j",
    async ({ token, branch, extra }) => {
      const result = {
        pr_url: "https://git.test/repo/pull/1",
        branch_name: "fix/quality",
        provider: "github",
        commit_sha: "abc123",
      };
      respond(result);
      await expect(
        client.createPullRequest("p", "activity", token, branch),
      ).resolves.toEqual(result);
      expect(fetchMock).toHaveBeenCalledWith(
        "https://gateway.test/api/v1/projects/p/operation/submit",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            activity_id: "activity",
            create_pr: true,
            ...extra,
          }),
        }),
      );
    },
  );

  it("rejects a completed submit without a pull request URL", async () => {
    respond({ pr_url: null, branch_name: "fix/quality" });
    await expect(client.createPullRequest("p", "activity")).rejects.toEqual(
      new InputError("Submit completed without a PR URL"),
    );
  });

  it("propagates submit failures without returning a pull request", async () => {
    fail(403, "SCM permission denied", "Forbidden");
    await expect(client.createPullRequest("p", "activity")).rejects.toEqual(
      new InputError(
        "APME request failed: 403 Forbidden - SCM permission denied",
      ),
    );
  });
});
