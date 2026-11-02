/*
 * Copyright Red Hat
 */

import type { Entity } from "@backstage/catalog-model";
import { isGitRepositoryEntity } from "./isGitRepositoryEntity";

describe("isGitRepositoryEntity", () => {
  it("matches Component git-repository entities", () => {
    expect(
      isGitRepositoryEntity({
        apiVersion: "backstage.io/v1alpha1",
        kind: "Component",
        metadata: { name: "my-repo" },
        spec: { type: "git-repository", owner: "user" },
      }),
    ).toBe(true);
  });

  it("is case-insensitive on kind", () => {
    expect(
      isGitRepositoryEntity({
        apiVersion: "backstage.io/v1alpha1",
        kind: "component",
        metadata: { name: "my-repo" },
        spec: { type: "git-repository" },
      }),
    ).toBe(true);
  });

  it.each([
    ["User", undefined],
    ["Group", undefined],
    ["Component", "service"],
    ["Component", "website"],
    ["API", "openapi"],
    ["Resource", "git-repository"],
  ] as const)("rejects kind=%s type=%s", (kind, type) => {
    const entity: Entity = {
      apiVersion: "backstage.io/v1alpha1",
      kind,
      metadata: { name: "example" },
      spec: type ? { type } : {},
    };
    expect(isGitRepositoryEntity(entity)).toBe(false);
  });
});
