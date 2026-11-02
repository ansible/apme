import express from "express";
import request from "supertest";
import { ConfigReader } from "@backstage/config";
import { mockServices } from "@backstage/backend-test-utils";
import type { CatalogClient } from "@backstage/catalog-client";
import type { EntityProviderConnection } from "@backstage/plugin-catalog-node";
import { AuthorizeResult } from "@backstage/plugin-permission-common";
import { createManualRepositoriesRouter } from "./router";
import { ManualGitRepositoryProvider } from "../providers/ManualGitRepositoryProvider";
import { PortalOperations } from "@ansible/portal-plugin-node";
import { registerRepositoryOperation } from "./registerOperation";
import { ScmClientFactory } from "@ansible/backstage-rhaap-common";

jest.mock("@ansible/backstage-rhaap-common", () => ({
  ScmClientFactory: jest.fn(),
}));

describe("manual registration router", () => {
  const permissions = mockServices.permissions.mock();
  const httpAuth = mockServices.httpAuth.mock();
  const userInfo = mockServices.userInfo.mock();
  const applyMutation = jest.fn();
  const getEntities = jest.fn();
  const getEntityByRef = jest.fn();
  const repositoryExists = jest.fn();
  const entity = {
    apiVersion: "backstage.io/v1alpha1",
    kind: "Component",
    metadata: {
      name: "repo",
      namespace: "spoofed",
      annotations: {
        "ansible.io/registration-method": "manual",
        "ansible.io/scm-provider": "github",
        "ansible.io/scm-organization": "acme",
        "ansible.io/scm-repository": "repo",
        "backstage.io/view-url": "https://github.com/acme/repo",
        "backstage.io/managed-by-location": "url:https://evil.example",
      },
    },
    spec: { type: "git-repository", owner: "user:other/person" },
  };
  async function app() {
    const provider = new ManualGitRepositoryProvider();
    await provider.connect({
      applyMutation,
    } as unknown as EntityProviderConnection);
    const application = express();
    const operations = new PortalOperations(
      mockServices.logger.mock(),
      { resolve: async () => "acme" },
      { check: jest.fn() },
    );
    registerRepositoryOperation({
      operations,
      auth: mockServices.auth(),
      logger: mockServices.logger.mock(),
      provider,
      catalogClient: {
        getEntities,
        getEntityByRef,
      } as unknown as CatalogClient,
      rootConfig: new ConfigReader({
        integrations: { github: [{ host: "github.com" }] },
      }),
    });
    application.use(
      createManualRepositoriesRouter({
        permissions,
        httpAuth,
        userInfo,
        operations,
        organizations: { resolve: async () => "acme" },
      }),
    );
    application.use(
      (
        error: Error,
        _req: express.Request,
        res: express.Response,
        _next: express.NextFunction,
      ) => {
        const status =
          { NotAllowedError: 403, ConflictError: 409, InputError: 400 }[
            error.name
          ] ?? 500;
        res.status(status).json({ error: error.message });
      },
    );
    return application;
  }
  beforeEach(async () => {
    jest.resetAllMocks();
    permissions.authorize.mockResolvedValue([
      { result: AuthorizeResult.ALLOW },
    ]);
    httpAuth.credentials.mockResolvedValue({
      $$type: "@backstage/BackstageCredentials",
      principal: { type: "user", userEntityRef: "user:acme/alice" },
      expiresAt: undefined,
    });
    userInfo.getUserInfo.mockResolvedValue({
      userEntityRef: "user:acme/alice",
      ownershipEntityRefs: [],
    });
    getEntities.mockResolvedValue({ items: [] });
    repositoryExists.mockResolvedValue(true);
    (ScmClientFactory as jest.Mock).mockImplementation(() => ({
      createClient: async () => ({ repositoryExists }),
    }));
  });
  it("derives owner from identity while keeping shared catalog placement and trusted provenance", async () => {
    const response = await request(await app())
      .post("/apme/repositories")
      .send({ entity });
    expect(response.status).toBe(201);
    expect(response.body.entityRef).toBe(
      "component:default/acme-repo-github-manual",
    );
    const registered = applyMutation.mock.calls[0][0].added[0].entity;
    expect(registered.metadata.namespace).toBe("default");
    expect(registered.metadata.annotations).not.toHaveProperty(
      "ansible.io/portal-organization",
    );
    expect(registered.spec.owner).toBe("user:acme/alice");
    expect(
      registered.metadata.annotations["backstage.io/managed-by-location"],
    ).toBe("apme-manual:ManualGitRepositoryProvider");
    expect(
      registered.metadata.annotations[
        "backstage.io/managed-by-origin-location"
      ],
    ).toBe("apme-manual:ManualGitRepositoryProvider");
    expect(httpAuth.credentials).toHaveBeenCalledWith(expect.anything(), {
      allow: ["user"],
    });
  });
  it("denies mutation without registration permission", async () => {
    permissions.authorize.mockResolvedValue([{ result: AuthorizeResult.DENY }]);
    expect(
      (
        await request(await app())
          .post("/apme/repositories")
          .send({ entity })
      ).status,
    ).toBe(403);
    expect(applyMutation).not.toHaveBeenCalled();
  });
  it("rejects duplicate identities or colliding entity refs", async () => {
    getEntities.mockResolvedValue({ items: [entity] });
    expect(
      (
        await request(await app())
          .post("/apme/repositories")
          .send({ entity })
      ).status,
    ).toBe(409);
    getEntities.mockResolvedValue({ items: [] });
    getEntityByRef.mockResolvedValue(entity);
    expect(
      (
        await request(await app())
          .post("/apme/repositories")
          .send({ entity })
      ).status,
    ).toBe(409);
    expect(applyMutation).not.toHaveBeenCalled();
  });
  it("rejects unconfigured hosts and mismatched URL metadata", async () => {
    const invalid = {
      ...entity,
      metadata: {
        ...entity.metadata,
        annotations: {
          ...entity.metadata.annotations,
          "backstage.io/view-url": "https://evil.example/acme/repo",
        },
      },
    };
    expect(
      (
        await request(await app())
          .post("/apme/repositories")
          .send({ entity: invalid })
      ).status,
    ).toBe(400);
    expect(applyMutation).not.toHaveBeenCalled();
  });
  it("does not consume JSON bodies of core catalog routes", async () => {
    const application = await app();
    application.post("/entities/by-refs", express.json(), (req, res) => {
      res.json(req.body);
    });
    const body = { entityRefs: ["component:acme/repo"] };
    expect(
      (await request(application).post("/entities/by-refs").send(body)).body,
    ).toEqual(body);
  });
});
