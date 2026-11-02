/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { discoveryApiRef, fetchApiRef } from "@backstage/core-plugin-api";
import { TestApiProvider, renderInTestApp } from "@backstage/test-utils";
import { Entity } from "@backstage/catalog-model";
import { gitRepositoriesCatalogApiRef } from "@ansible/backstage-rhaap-common/gitRepositoriesCatalog";
import { DeregisterRepositoryDialog } from "./DeregisterRepositoryDialog";

const mockEntity: Entity = {
  apiVersion: "backstage.io/v1alpha1",
  kind: "Component",
  metadata: {
    name: "test-org-test-repo-github-manual",
    title: "Test Repository",
    annotations: {
      "ansible.io/registration-method": "manual",
    },
  },
  spec: {
    type: "git-repository",
  },
};

const mockDiscoveryApi = {
  getBaseUrl: jest
    .fn()
    .mockResolvedValue("http://localhost:7007/api/portal-operations"),
};

const mockFetchApi = {
  fetch: jest.fn(),
};

const mockInvalidateCatalogCache = jest.fn();

const renderDialog = async (
  props: Partial<React.ComponentProps<typeof DeregisterRepositoryDialog>> = {},
) => {
  const defaultProps = {
    open: true,
    entity: mockEntity,
    onClose: jest.fn(),
    onConfirm: jest.fn(),
  };
  const mergedProps = { ...defaultProps, ...props };

  return renderInTestApp(
    <TestApiProvider
      apis={[
        [discoveryApiRef, mockDiscoveryApi],
        [fetchApiRef, mockFetchApi],
        [
          gitRepositoriesCatalogApiRef,
          { invalidateCatalogCache: mockInvalidateCatalogCache },
        ],
      ]}
    >
      <DeregisterRepositoryDialog {...mergedProps} />
    </TestApiProvider>,
  );
};

describe("DeregisterRepositoryDialog", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("renders dialog with entity name", async () => {
    await renderDialog();
    expect(screen.getByText("Remove repository?")).toBeInTheDocument();
    expect(screen.getByText(/Test Repository/)).toBeInTheDocument();
  });

  it("calls onClose when Cancel is clicked", async () => {
    const onClose = jest.fn();
    await renderDialog({ onClose });
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it("calls deregister API and onConfirm on success", async () => {
    const onConfirm = jest.fn();
    mockFetchApi.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ success: true }),
    });

    await renderDialog({ onConfirm });
    fireEvent.click(screen.getByRole("button", { name: /^remove$/i }));

    await waitFor(() => {
      expect(mockFetchApi.fetch).toHaveBeenCalledWith(
        "http://localhost:7007/api/portal-operations/apme.repository.deregister",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
        }),
      );
    });

    await waitFor(() => {
      expect(onConfirm).toHaveBeenCalled();
    });
    expect(mockInvalidateCatalogCache).toHaveBeenCalled();
  });

  it("does not invalidate catalog cache when deregister fails", async () => {
    mockFetchApi.fetch.mockResolvedValueOnce({
      ok: false,
      text: () =>
        Promise.resolve(JSON.stringify({ error: "Something went wrong" })),
      statusText: "Bad Request",
    });

    await renderDialog();
    fireEvent.click(screen.getByRole("button", { name: /^remove$/i }));

    await waitFor(() => {
      expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
    });
    expect(mockInvalidateCatalogCache).not.toHaveBeenCalled();
  });

  it("does not render when open is false", async () => {
    await renderDialog({ open: false });
    expect(screen.queryByText("Remove repository?")).not.toBeInTheDocument();
  });

  it("does not call onClose when loading", async () => {
    const onClose = jest.fn();
    let resolveFetch: (value: unknown) => void;
    mockFetchApi.fetch.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveFetch = resolve;
      }),
    );

    await renderDialog({ onClose });
    fireEvent.click(screen.getByRole("button", { name: /^remove$/i }));

    await waitFor(() => {
      expect(screen.getByText("Removing...")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).not.toHaveBeenCalled();

    resolveFetch!({
      ok: true,
      json: () => Promise.resolve({ success: true }),
    });
  });

  it("displays error for non-JSON error responses", async () => {
    mockFetchApi.fetch.mockResolvedValueOnce({
      ok: false,
      text: () => Promise.resolve("Plain text error"),
      statusText: "Internal Server Error",
    });

    await renderDialog();
    fireEvent.click(screen.getByRole("button", { name: /^remove$/i }));

    await waitFor(() => {
      expect(screen.getByText("Plain text error")).toBeInTheDocument();
    });
  });

  it("falls back to displayName from spec.repository_name", async () => {
    const entityWithSpecName: Entity = {
      ...mockEntity,
      metadata: { ...mockEntity.metadata, title: undefined },
      spec: { type: "git-repository", repository_name: "My Custom Repo" },
    };
    await renderDialog({ entity: entityWithSpecName });
    expect(screen.getByText(/My Custom Repo/)).toBeInTheDocument();
  });

  it("falls back to metadata.name when no title or repository_name", async () => {
    const entityNoTitle: Entity = {
      ...mockEntity,
      metadata: {
        ...mockEntity.metadata,
        title: undefined,
      },
    };
    await renderDialog({ entity: entityNoTitle });
    expect(
      screen.getByText(/test-org-test-repo-github-manual/),
    ).toBeInTheDocument();
  });
});
