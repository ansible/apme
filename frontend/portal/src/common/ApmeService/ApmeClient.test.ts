/*
 * Copyright Red Hat
 */

import { ConfigReader } from "@backstage/config";
import { InputError } from "@backstage/errors";
import { Agent } from "undici";
import { ApmeClient } from "./ApmeClient";

jest.mock("undici", () => {
  const MockAgent = jest.fn();
  return { Agent: MockAgent };
});

describe("ApmeClient", () => {
  const logger = {
    child: () => logger,
    info: jest.fn(),
    debug: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
  };

  const rootConfig = new ConfigReader({
    ansible: {
      apme: {
        enabled: true,
        baseUrl: "http://localhost:8080",
      },
    },
  });

  beforeEach(() => {
    jest.clearAllMocks();
    global.fetch = jest.fn();
  });

  it("looks up projects by repo URL and branch", async () => {
    const mockProject = {
      id: "proj-backup",
      name: "terrible-playbook-backup",
      repo_url: "https://github.com/acme/terrible-playbook",
      branch: "backup",
    };
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockProject),
    });

    const client = new ApmeClient({ rootConfig, logger: logger as never });
    const result = await client.getProjectByRepoUrl(
      "https://github.com/acme/terrible-playbook",
      "backup",
    );

    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8080/api/v1/projects/lookup?repo_url=https%3A%2F%2Fgithub.com%2Facme%2Fterrible-playbook&branch=backup",
      expect.objectContaining({
        headers: { "Content-Type": "application/json" },
      }),
    );
    expect(result).toEqual(mockProject);
  });

  it("returns null when branch lookup misses", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 404,
      text: () => Promise.resolve("Not found"),
    });

    const client = new ApmeClient({ rootConfig, logger: logger as never });
    await expect(
      client.getProjectByRepoUrl(
        "https://github.com/acme/terrible-playbook",
        "missing",
      ),
    ).resolves.toBeNull();
  });

  it("rethrows non-404 lookup failures", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: () => Promise.resolve("Server error"),
    });

    const client = new ApmeClient({ rootConfig, logger: logger as never });
    await expect(
      client.getProjectByRepoUrl("https://github.com/acme/terrible-playbook"),
    ).rejects.toBeInstanceOf(InputError);
  });

  it("fetches activity detail and normalizes remediation classes", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          scan_id: "scan-1",
          scan_type: "remediate",
          status: "completed",
          violations: [{ id: 1, remediation_class: "auto-fixable" }],
          proposals: [{ id: "p1", tier: 2 }],
        }),
    });

    const client = new ApmeClient({ rootConfig, logger: logger as never });
    const detail = await client.getActivityDetail("scan-1");

    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8080/api/v1/activity/scan-1",
      expect.objectContaining({
        headers: { "Content-Type": "application/json" },
      }),
    );
    expect(detail.violations[0].remediation_class).toBe(1);
    expect(detail.proposals).toHaveLength(1);
  });

  it("includes ansible_version in scan operation payload", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ operation_id: "op-1" }),
    });

    const client = new ApmeClient({ rootConfig, logger: logger as never });
    await client.triggerScan("proj-1", { ansibleVersion: "2.17" });

    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8080/api/v1/projects/proj-1/operation",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          action: "check",
          options: { enable_ai: false, ansible_version: "2.17" },
        }),
      }),
    );
  });

  describe("submitRemediation timeout", () => {
    function mockAbortableFetch() {
      (global.fetch as jest.Mock).mockImplementation(
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

    it("uses default submitTimeoutMs of 300_000 (not 30_000)", async () => {
      mockAbortableFetch();
      const client = new ApmeClient({ rootConfig, logger: logger as never });
      const promise = client.submitRemediation("proj-1", {
        activity_id: "act-1",
      });

      jest.advanceTimersByTime(30_000);
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

      jest.advanceTimersByTime(270_000);
      await expect(promise).rejects.toThrow(/Failed to connect to APME/);
    });

    it("honors custom ansible.apme.submitTimeoutMs", async () => {
      mockAbortableFetch();
      const customConfig = new ConfigReader({
        ansible: {
          apme: {
            enabled: true,
            baseUrl: "http://localhost:8080",
            submitTimeoutMs: 5_000,
          },
        },
      });
      const client = new ApmeClient({
        rootConfig: customConfig,
        logger: logger as never,
      });
      const promise = client.submitRemediation("proj-1", {
        activity_id: "act-1",
      });

      jest.advanceTimersByTime(5_000);
      await expect(promise).rejects.toThrow(/Failed to connect to APME/);
    });
  });

  describe("checkSSL / TLS dispatcher", () => {
    it("creates undici Agent and passes dispatcher when checkSSL is false", async () => {
      const sslOffConfig = new ConfigReader({
        ansible: {
          apme: {
            enabled: true,
            baseUrl: "http://localhost:8080",
            checkSSL: false,
          },
        },
      });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ status: "ok" }),
      });

      const client = new ApmeClient({
        rootConfig: sslOffConfig,
        logger: logger as never,
      });
      await client.getHealth();

      expect(Agent).toHaveBeenCalledWith({
        connect: { rejectUnauthorized: false },
      });
      expect(global.fetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({ dispatcher: expect.anything() }),
      );
    });

    it("does not create dispatcher when checkSSL is true", async () => {
      const sslOnConfig = new ConfigReader({
        ansible: {
          apme: {
            enabled: true,
            baseUrl: "http://localhost:8080",
            checkSSL: true,
          },
        },
      });
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ status: "ok" }),
      });

      const client = new ApmeClient({
        rootConfig: sslOnConfig,
        logger: logger as never,
      });
      await client.getHealth();

      expect(Agent).not.toHaveBeenCalled();
      const fetchOpts = (global.fetch as jest.Mock).mock.calls[0][1];
      expect(fetchOpts.dispatcher).toBeUndefined();
    });

    it("defaults to checkSSL true (no dispatcher) when checkSSL is omitted", async () => {
      (global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ status: "ok" }),
      });

      const client = new ApmeClient({ rootConfig, logger: logger as never });
      await client.getHealth();

      expect(Agent).not.toHaveBeenCalled();
      const fetchOpts = (global.fetch as jest.Mock).mock.calls[0][1];
      expect(fetchOpts.dispatcher).toBeUndefined();
    });
  });

  it("uses resolveBaseUrl for Gateway calls when set", async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ status: "ok" }),
    });

    const client = new ApmeClient({
      rootConfig,
      logger: logger as never,
      resolveBaseUrl: async () => "http://host.containers.internal:8080/",
    });
    await client.getHealth();

    expect(global.fetch).toHaveBeenCalledWith(
      "http://host.containers.internal:8080/api/v1/health",
      expect.anything(),
    );
  });
});
