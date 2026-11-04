/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import "@testing-library/jest-dom";
import type { ComponentProps } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@material-ui/core";
import { MemoryRouter } from "react-router-dom";
import type { Project } from "../../../common/types";
import {
  chipStyleForSeverity,
  SEVERITY_STYLES,
} from "../../../common/severity";
import { ApmeRepoStatusChip } from "./ApmeRepoStatusChip";

const mockNavigate = jest.fn();
const mockGetProjectByRepoUrl = jest.fn();
const mockGetOperationState = jest.fn();
const mockUseApmeEnabled = jest.fn(() => true);
const mockApmeApi = {
  getProjectByRepoUrl: mockGetProjectByRepoUrl,
  getOperationState: mockGetOperationState,
};

jest.mock("../../api", () => ({
  apmeApiRef: { id: "plugin.apme.api" },
}));

jest.mock("@backstage/core-plugin-api", () => ({
  useApi: () => mockApmeApi,
}));

jest.mock("react-router-dom", () => ({
  ...jest.requireActual("react-router-dom"),
  useNavigate: () => mockNavigate,
}));

jest.mock("../../hooks/useApmeEnabled", () => ({
  useApmeEnabled: () => mockUseApmeEnabled(),
}));

function project(
  partial: Partial<Project> & Pick<Project, "id" | "total_violations">,
): Project {
  return {
    name: "demo",
    repo_url: "https://github.com/acme/demo",
    branch: "main",
    created_at: "2026-01-01T00:00:00Z",
    health_score: 50,
    violation_trend: "stable",
    scan_count: 1,
    last_scanned_at: "2026-01-02T00:00:00Z",
    has_scm_token: false,
    has_new_commits: false,
    ...partial,
  };
}

function renderChip(
  props?: Partial<ComponentProps<typeof ApmeRepoStatusChip>>,
) {
  return render(
    <MemoryRouter>
      <ThemeProvider theme={createTheme()}>
        <ApmeRepoStatusChip
          repoUrl="https://github.com/acme/demo"
          branch="main"
          projectDetailPath="/self-service/repositories/catalog-demo?tab=quality"
          {...props}
        />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("ApmeRepoStatusChip", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUseApmeEnabled.mockReturnValue(true);
    mockGetOperationState.mockResolvedValue(null);
  });

  it("renders nothing when APME is disabled", () => {
    mockUseApmeEnabled.mockReturnValue(false);
    const { container } = renderChip();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders Not scanned when no project exists", async () => {
    mockGetProjectByRepoUrl.mockResolvedValue(null);
    renderChip();
    expect(await screen.findByText("Not scanned")).toBeInTheDocument();
  });

  it("renders Clean when there are no violations", async () => {
    mockGetProjectByRepoUrl.mockResolvedValue(
      project({ id: "p1", total_violations: 0 }),
    );
    renderChip();
    expect(await screen.findByText("Clean")).toBeInTheDocument();
  });

  it("renders Scanning when the project has an active operation", async () => {
    mockGetProjectByRepoUrl.mockResolvedValue(
      project({
        id: "p1",
        total_violations: 0,
        active_operation: { operation_id: "op-1", status: "scanning" },
      }),
    );
    renderChip();
    expect(await screen.findByText("Scanning…")).toBeInTheDocument();
  });

  it("renders neutral chip when violations exist but breakdown is missing", async () => {
    mockGetProjectByRepoUrl.mockResolvedValue(
      project({ id: "p1", total_violations: 3 }),
    );
    renderChip();
    const chip = await screen.findByText("3 violations");
    expect(chip).toBeInTheDocument();
    expect(chip).not.toHaveStyle({
      backgroundColor: SEVERITY_STYLES.critical.background,
    });
  });

  it("colors the chip by worst severity when breakdown is present", async () => {
    mockGetProjectByRepoUrl.mockResolvedValue(
      project({
        id: "p1",
        total_violations: 4,
        severity_breakdown: { high: 4 },
      }),
    );
    renderChip();
    const label = await screen.findByText("4 violations");
    expect(label.closest(".MuiChip-root")).toHaveStyle(
      chipStyleForSeverity("high"),
    );
  });

  it("navigates to the quality tab when clicked", async () => {
    mockGetProjectByRepoUrl.mockResolvedValue(
      project({
        id: "p1",
        name: "demo-repo",
        total_violations: 2,
        severity_breakdown: { medium: 2 },
      }),
    );
    renderChip();
    fireEvent.click(await screen.findByText("2 violations"));
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith(
        "/self-service/repositories/catalog-demo?tab=quality",
      );
    });
  });

  it("uses projectDetailPath when provided", async () => {
    mockGetProjectByRepoUrl.mockResolvedValue(
      project({
        id: "p1",
        total_violations: 1,
        severity_breakdown: { low: 1 },
      }),
    );
    renderChip({ projectDetailPath: "/custom/path" });
    fireEvent.click(await screen.findByText("1 violation"));
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/custom/path");
    });
  });
});
