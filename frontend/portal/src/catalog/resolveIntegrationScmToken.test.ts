/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

jest.mock("@ansible/backstage-rhaap-common", () => ({
  ScmClientFactory: jest.fn(),
  resolveGithubToken: jest.fn(),
}));

import { ConfigReader } from "@backstage/config";
import {
  ScmClientFactory,
  resolveGithubToken,
} from "@ansible/backstage-rhaap-common";
import { resolveIntegrationScmToken } from "./resolveIntegrationScmToken";

const MockScmClientFactory = ScmClientFactory as jest.MockedClass<
  typeof ScmClientFactory
>;
const mockResolveGithubToken = resolveGithubToken as jest.MockedFunction<
  typeof resolveGithubToken
>;

describe("resolveIntegrationScmToken", () => {
  const logger = {
    debug: jest.fn(),
    info: jest.fn(),
    warn: jest.fn(),
    error: jest.fn(),
    child: jest.fn(),
  };
  logger.child.mockReturnValue(logger);

  const rootConfig = new ConfigReader({
    integrations: {
      github: [
        { host: "github.com", token: "test-token" },
        { host: "github.enterprise.test", token: "enterprise-token" },
      ],
    },
  });

  beforeEach(() => {
    jest.clearAllMocks();
    MockScmClientFactory.mockImplementation(
      () =>
        ({
          integrations: {},
          githubCredentialsProvider: {},
        }) as never,
    );
  });

  it("returns undefined for an invalid repo URL", async () => {
    await expect(
      resolveIntegrationScmToken({
        rootConfig,
        logger: logger as never,
        repoUrl: "not-a-url",
      }),
    ).resolves.toBeUndefined();
    expect(MockScmClientFactory).not.toHaveBeenCalled();
  });

  it("returns undefined for non-GitHub providers", async () => {
    await expect(
      resolveIntegrationScmToken({
        rootConfig,
        logger: logger as never,
        repoUrl: "https://gitlab.com/acme/playbooks",
      }),
    ).resolves.toBeUndefined();
    expect(MockScmClientFactory).not.toHaveBeenCalled();
  });

  it("returns a trimmed GitHub integration token on success", async () => {
    mockResolveGithubToken.mockResolvedValue({
      token: " ghp_abc123 ",
    } as never);

    await expect(
      resolveIntegrationScmToken({
        rootConfig,
        logger: logger as never,
        repoUrl: "https://github.com/acme/playbooks",
      }),
    ).resolves.toBe("ghp_abc123");

    expect(mockResolveGithubToken).toHaveBeenCalledWith(
      expect.objectContaining({
        host: "github.com",
        organization: "acme",
        repository: "playbooks",
      }),
    );
  });

  it("resolves credentials for a configured GitHub Enterprise host", async () => {
    mockResolveGithubToken.mockResolvedValue({
      token: " enterprise-token ",
    } as never);

    await expect(
      resolveIntegrationScmToken({
        rootConfig,
        logger: logger as never,
        repoUrl: "https://github.enterprise.test/acme/playbooks",
      }),
    ).resolves.toBe("enterprise-token");

    expect(mockResolveGithubToken).toHaveBeenCalledWith(
      expect.objectContaining({
        host: "github.enterprise.test",
        organization: "acme",
        repository: "playbooks",
      }),
    );
  });

  it("returns undefined when resolveGithubToken throws", async () => {
    mockResolveGithubToken.mockRejectedValue(new Error("no creds"));

    await expect(
      resolveIntegrationScmToken({
        rootConfig,
        logger: logger as never,
        repoUrl: "https://github.com/acme/playbooks",
      }),
    ).resolves.toBeUndefined();
    expect(logger.debug).toHaveBeenCalledWith(
      expect.stringContaining("No integration SCM token"),
    );
  });
});
