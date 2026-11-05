import { act, renderHook, waitFor } from "@testing-library/react";

const mockApmeApi = {};
let mockEntity = {
  metadata: {
    name: "first-repo",
    annotations: {
      "backstage.io/source-location": "url:https://github.com/acme/first",
    },
  },
};
const mockRegisterOrResolve = jest.fn();

jest.mock("@backstage/core-plugin-api", () => ({
  discoveryApiRef: { id: "discovery" },
  fetchApiRef: { id: "fetch" },
  useApi: (ref: { id: string }) =>
    ref.id === "plugin.apme.api" ? mockApmeApi : {},
}));

jest.mock("@backstage/plugin-catalog-react", () => ({
  useEntity: () => ({ entity: mockEntity }),
}));

jest.mock("../api", () => ({
  apmeApiRef: { id: "plugin.apme.api" },
}));

jest.mock("../api/createApmeUiWorkflowAdapter", () => ({
  createApmeUiWorkflowAdapter: jest.fn().mockResolvedValue({}),
}));

jest.mock("../utils/registerOrResolveApmeProject", () => ({
  registerOrResolveApmeProject: (...args: unknown[]) =>
    mockRegisterOrResolve(...args),
}));

jest.mock("../utils/ensureRepoBranchForScan", () => ({
  ensureRepoBranchForScan: jest.fn().mockResolvedValue(undefined),
}));

import { useResolveApmeProject } from "./useResolveApmeProject";

describe("useResolveApmeProject entity changes", () => {
  beforeEach(() => {
    mockEntity = {
      metadata: {
        name: "first-repo",
        annotations: {
          "backstage.io/source-location": "url:https://github.com/acme/first",
        },
      },
    };
    mockRegisterOrResolve.mockReset();
  });

  it("clears the previous project while resolving a different entity", async () => {
    mockRegisterOrResolve
      .mockResolvedValueOnce({ id: "project-first" })
      .mockImplementationOnce(() => new Promise(() => undefined));
    const { result, rerender } = renderHook(() => useResolveApmeProject());

    await waitFor(() => expect(result.current.projectId).toBe("project-first"));
    await waitFor(() => expect(result.current.adapter).not.toBeNull());

    mockEntity = {
      metadata: {
        name: "second-repo",
        annotations: {
          "backstage.io/source-location": "url:https://github.com/acme/second",
        },
      },
    };
    act(() => rerender());

    expect(result.current.projectId).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.unavailable).toBe(false);
  });
});
