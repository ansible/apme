import { renderHook, waitFor } from "@testing-library/react";
import { useApi } from "@backstage/core-plugin-api";
import type { Entity } from "@backstage/catalog-model";
import { useApmeProjectContext } from "./useApmeProjectContext";

jest.mock("@backstage/core-plugin-api", () => ({
  ...jest.requireActual("@backstage/core-plugin-api"),
  useApi: jest.fn(),
}));

describe("APME project findings transport state", () => {
  it("exposes findings failure independently of a successful project lookup", async () => {
    const failure = new Error("Findings unavailable");
    const api = {
      getProjectByRepoUrl: jest.fn().mockResolvedValue({
        id: "project",
        scan_count: 1,
        total_violations: 0,
      }),
      getViolations: jest.fn().mockRejectedValue(failure),
      getRules: jest.fn().mockResolvedValue([]),
      getProjectDependencies: jest.fn().mockResolvedValue(null),
    };
    jest.mocked(useApi).mockReturnValue(api);
    const entity: Entity = {
      apiVersion: "backstage.io/v1alpha1",
      kind: "Component",
      metadata: {
        name: "repo",
        annotations: {
          "backstage.io/source-location": "url:https://github.com/acme/repo",
        },
      },
      spec: { type: "git-repository", repository_default_branch: "main" },
    };
    const { result } = renderHook(() => useApmeProjectContext(entity));
    await waitFor(() => expect(result.current.violationsError).toBe(failure));
    expect(result.current.error).toBeUndefined();
    expect(result.current.violations).toEqual([]);
    expect(result.current.violationsLoading).toBe(false);
  });
});
