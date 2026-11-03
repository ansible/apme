/*
 * Copyright Red Hat
 */

import { act, renderHook } from "@testing-library/react";
import { TestApiProvider } from "@backstage/test-utils";
import { discoveryApiRef, fetchApiRef } from "@backstage/core-plugin-api";
import type { ReactNode } from "react";
import {
  isManuallyRegisteredRepository,
  useDeregisterRepository,
} from "./useDeregisterRepository";
import type { Entity } from "@backstage/catalog-model";

const manualEntity: Entity = {
  apiVersion: "backstage.io/v1alpha1",
  kind: "Component",
  metadata: {
    name: "test-org-test-repo-github-manual",
    namespace: "default",
    annotations: {
      "ansible.io/registration-method": "manual",
    },
  },
  spec: { type: "git-repository" },
};

describe("useDeregisterRepository", () => {
  const mockFetch = jest.fn();
  const mockGetBaseUrl = jest
    .fn()
    .mockResolvedValue("http://localhost:7007/api/portal-operations");

  const wrapper = ({ children }: { children: ReactNode }) => (
    <TestApiProvider
      apis={[
        [discoveryApiRef, { getBaseUrl: mockGetBaseUrl }],
        [fetchApiRef, { fetch: mockFetch }],
      ]}
    >
      {children}
    </TestApiProvider>
  );

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("calls the deregistration operation with a subject and resolves on success", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true });

    const { result } = renderHook(() => useDeregisterRepository(manualEntity), {
      wrapper,
    });

    await act(async () => {
      await result.current.deregister();
    });

    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:7007/api/portal-operations/apme.repository.deregister",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subject: {
            entityRef: "component:default/test-org-test-repo-github-manual",
          },
        }),
      }),
    );
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("parses JSON error body from a failed response", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      text: async () => JSON.stringify({ error: "Not a manual repo" }),
      statusText: "Bad Request",
    });

    const { result } = renderHook(() => useDeregisterRepository(manualEntity), {
      wrapper,
    });

    await act(async () => {
      await expect(result.current.deregister()).rejects.toThrow(
        "Not a manual repo",
      );
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.error?.message).toBe("Not a manual repo");
  });

  it("falls back to plain text when response body is not JSON", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      text: async () => "plain text error",
      statusText: "Internal Server Error",
    });

    const { result } = renderHook(() => useDeregisterRepository(manualEntity), {
      wrapper,
    });

    await act(async () => {
      await expect(result.current.deregister()).rejects.toThrow(
        "plain text error",
      );
    });

    expect(result.current.error?.message).toBe("plain text error");
  });
  it("reads Backstage serialized operation errors", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      text: async () =>
        JSON.stringify({
          error: {
            name: "NotAllowedError",
            message: "Repository access denied",
          },
        }),
      statusText: "Forbidden",
    });
    const { result } = renderHook(() => useDeregisterRepository(manualEntity), {
      wrapper,
    });
    await act(async () => {
      await expect(result.current.deregister()).rejects.toThrow(
        "Repository access denied",
      );
    });
  });
});

describe("isManuallyRegisteredRepository", () => {
  it("returns true for manually registered git repository", () => {
    const entity: Entity = {
      apiVersion: "backstage.io/v1alpha1",
      kind: "Component",
      metadata: {
        name: "test-repo",
        annotations: {
          "ansible.io/registration-method": "manual",
        },
      },
      spec: {
        type: "git-repository",
      },
    };
    expect(isManuallyRegisteredRepository(entity)).toBe(true);
  });

  it("returns false for crawler-discovered repository", () => {
    const entity: Entity = {
      apiVersion: "backstage.io/v1alpha1",
      kind: "Component",
      metadata: {
        name: "test-repo",
        annotations: {
          "ansible.io/discovery-source-id": "github:github.com:my-org",
        },
      },
      spec: {
        type: "git-repository",
      },
    };
    expect(isManuallyRegisteredRepository(entity)).toBe(false);
  });

  it("returns false for non-git-repository type", () => {
    const entity: Entity = {
      apiVersion: "backstage.io/v1alpha1",
      kind: "Component",
      metadata: {
        name: "test-component",
        annotations: {
          "ansible.io/registration-method": "manual",
        },
      },
      spec: {
        type: "service",
      },
    };
    expect(isManuallyRegisteredRepository(entity)).toBe(false);
  });

  it("returns false when annotation is missing", () => {
    const entity: Entity = {
      apiVersion: "backstage.io/v1alpha1",
      kind: "Component",
      metadata: {
        name: "test-repo",
      },
      spec: {
        type: "git-repository",
      },
    };
    expect(isManuallyRegisteredRepository(entity)).toBe(false);
  });

  it("returns false when spec is missing", () => {
    const entity: Entity = {
      apiVersion: "backstage.io/v1alpha1",
      kind: "Component",
      metadata: {
        name: "test-repo",
        annotations: {
          "ansible.io/registration-method": "manual",
        },
      },
    };
    expect(isManuallyRegisteredRepository(entity)).toBe(false);
  });
});
