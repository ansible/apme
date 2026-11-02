/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { screen, fireEvent, waitFor, act } from "@testing-library/react";
import { discoveryApiRef, fetchApiRef } from "@backstage/core-plugin-api";
import { TestApiProvider, renderInTestApp } from "@backstage/test-utils";
import { Entity } from "@backstage/catalog-model";
import { gitRepositoriesCatalogApiRef } from "@ansible/backstage-rhaap-common/gitRepositoriesCatalog";
import { ApmeDeregisterRepositoryOverlay } from "./ApmeDeregisterRepositoryOverlay";
import { deregisterRepositoryDialogStore } from "./deregisterRepositoryDialogStore";

const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => mockNavigate,
}));

const mockEntity: Entity = {
  apiVersion: "backstage.io/v1alpha1",
  kind: "Component",
  metadata: {
    name: "test-repo",
    namespace: "default",
    annotations: {
      "ansible.io/registration-method": "manual",
    },
  },
  spec: { type: "git-repository" },
};

const otherEntity: Entity = {
  apiVersion: "backstage.io/v1alpha1",
  kind: "Component",
  metadata: {
    name: "other-repo",
    namespace: "default",
    annotations: {},
  },
  spec: { type: "git-repository" },
};

const mockDiscoveryApi = {
  getBaseUrl: jest.fn().mockResolvedValue("http://localhost:7007/api/catalog"),
};

const mockFetchApi = {
  fetch: jest.fn(),
};

const renderOverlay = async (entity: Entity = mockEntity) => {
  const context = {
    entity,
    repoUrl: "https://github.com/org/repo",
  };

  return renderInTestApp(
    <TestApiProvider
      apis={[
        [discoveryApiRef, mockDiscoveryApi],
        [fetchApiRef, mockFetchApi],
        [gitRepositoriesCatalogApiRef, { invalidateCatalogCache: jest.fn() }],
      ]}
    >
      <ApmeDeregisterRepositoryOverlay context={context} />
    </TestApiProvider>,
  );
};

describe("ApmeDeregisterRepositoryOverlay", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    deregisterRepositoryDialogStore.close();
  });

  it("renders dialog closed when store has no open entity", async () => {
    await renderOverlay();
    expect(screen.queryByText("Remove repository?")).not.toBeInTheDocument();
  });

  it("opens dialog when store entity matches context entity", async () => {
    await renderOverlay();

    act(() => {
      deregisterRepositoryDialogStore.open(
        mockEntity,
        "/self-service/repositories/catalog",
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Remove repository?")).toBeInTheDocument();
    });
  });

  it("does not open dialog when store entity differs from context entity", async () => {
    await renderOverlay();

    act(() => {
      deregisterRepositoryDialogStore.open(
        otherEntity,
        "/self-service/repositories/catalog",
      );
    });

    expect(screen.queryByText("Remove repository?")).not.toBeInTheDocument();
  });

  it("closes dialog and clears store on Cancel", async () => {
    await renderOverlay();

    act(() => {
      deregisterRepositoryDialogStore.open(
        mockEntity,
        "/self-service/repositories/catalog",
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Remove repository?")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    await waitFor(() => {
      expect(screen.queryByText("Remove repository?")).not.toBeInTheDocument();
    });
    expect(deregisterRepositoryDialogStore.getState().open).toBe(false);
  });

  it("opens dialog without context entity (catalog overlay)", async () => {
    await renderInTestApp(
      <TestApiProvider
        apis={[
          [discoveryApiRef, mockDiscoveryApi],
          [fetchApiRef, mockFetchApi],
          [gitRepositoriesCatalogApiRef, { invalidateCatalogCache: jest.fn() }],
        ]}
      >
        <ApmeDeregisterRepositoryOverlay />
      </TestApiProvider>,
    );

    act(() => {
      deregisterRepositoryDialogStore.open(
        mockEntity,
        "/self-service/repositories/catalog",
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Remove repository?")).toBeInTheDocument();
    });
  });

  it("navigates to redirectPath and closes store on confirm", async () => {
    mockFetchApi.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ success: true }),
    });

    await renderOverlay();

    act(() => {
      deregisterRepositoryDialogStore.open(
        mockEntity,
        "/self-service/repositories/catalog",
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Remove repository?")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /^remove$/i }));

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith(
        "/self-service/repositories/catalog",
      );
    });
    expect(deregisterRepositoryDialogStore.getState().open).toBe(false);
  });

  it("does not navigate when redirectPath is null", async () => {
    mockFetchApi.fetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ success: true }),
    });

    await renderOverlay();

    act(() => {
      deregisterRepositoryDialogStore.open(
        mockEntity,
        null as unknown as string,
      );
    });

    await waitFor(() => {
      expect(screen.getByText("Remove repository?")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /^remove$/i }));

    await waitFor(() => {
      expect(deregisterRepositoryDialogStore.getState().open).toBe(false);
    });
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});
