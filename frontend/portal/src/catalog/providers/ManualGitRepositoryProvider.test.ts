import type { Entity } from "@backstage/catalog-model";
import { parseLocationRef } from "@backstage/catalog-model";
import type { EntityProviderConnection } from "@backstage/plugin-catalog-node";
import { ManualGitRepositoryProvider } from "./ManualGitRepositoryProvider";

const entity: Entity = {
  apiVersion: "backstage.io/v1alpha1",
  kind: "Component",
  metadata: {
    name: "repo",
    namespace: "acme",
    annotations: { "ansible.io/registration-method": "manual" },
  },
  spec: { type: "git-repository" },
};
describe("manual repository lifecycle", () => {
  const applyMutation = jest.fn();
  let provider: ManualGitRepositoryProvider;
  beforeEach(async () => {
    jest.resetAllMocks();
    provider = new ManualGitRepositoryProvider();
    await provider.connect({
      applyMutation,
    } as unknown as EntityProviderConnection);
  });
  it("adds trusted, parseable processing provenance without mutating the input", async () => {
    const submitted: Entity = {
      ...entity,
      metadata: {
        ...entity.metadata,
        annotations: {
          ...entity.metadata.annotations,
          "backstage.io/managed-by-location":
            "url:https://evil.example/catalog-info.yaml",
          "backstage.io/managed-by-origin-location":
            "url:https://evil.example/origin.yaml",
          "backstage.io/source-location": "url:https://github.com/acme/repo",
          "backstage.io/view-url": "https://github.com/acme/repo",
        },
      },
    };
    await provider.registerRepository(submitted);
    const added = applyMutation.mock.calls[0][0].added[0];
    expect(added.locationKey).toBe("ManualGitRepositoryProvider");
    const annotations = added.entity.metadata.annotations;
    for (const key of [
      "backstage.io/managed-by-location",
      "backstage.io/managed-by-origin-location",
    ]) {
      expect(annotations[key]).toBe("apme-manual:ManualGitRepositoryProvider");
      expect(parseLocationRef(annotations[key])).toEqual({
        type: "apme-manual",
        target: "ManualGitRepositoryProvider",
      });
    }
    expect(annotations["backstage.io/source-location"]).toBe(
      "url:https://github.com/acme/repo",
    );
    expect(annotations["backstage.io/view-url"]).toBe(
      "https://github.com/acme/repo",
    );
    expect(
      submitted.metadata.annotations?.["backstage.io/managed-by-location"],
    ).toBe("url:https://evil.example/catalog-info.yaml");
  });
  it("removes only its provider entry and supports re-registration", async () => {
    await provider.registerRepository(entity);
    await provider.deregisterRepository(entity);
    expect(applyMutation).toHaveBeenLastCalledWith({
      type: "delta",
      added: [],
      removed: [{ entity, locationKey: "ManualGitRepositoryProvider" }],
    });
    expect(provider.isTrackingStopped(entity)).toBe(true);
    await provider.registerRepository(entity);
    expect(provider.isTrackingStopped(entity)).toBe(false);
    expect(
      applyMutation.mock.calls.every(([mutation]) => mutation.type === "delta"),
    ).toBe(true);
  });
  it("stops stale tracking before awaiting removal and rolls back on failure", async () => {
    applyMutation.mockImplementationOnce(() => {
      expect(provider.isTrackingStopped(entity)).toBe(true);
      return Promise.reject(new Error("catalog unavailable"));
    });
    await expect(provider.deregisterRepository(entity)).rejects.toThrow(
      "catalog unavailable",
    );
    expect(provider.isTrackingStopped(entity)).toBe(false);
  });
  it("rejects crawler-owned entities and unconnected mutations", async () => {
    await expect(
      provider.deregisterRepository({ ...entity, metadata: { name: "auto" } }),
    ).rejects.toThrow("manually");
    await expect(
      new ManualGitRepositoryProvider().registerRepository(entity),
    ).rejects.toThrow("connected");
    expect(applyMutation).not.toHaveBeenCalled();
  });
});
