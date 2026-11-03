/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@material-ui/core/styles";
import { TestApiProvider } from "@backstage/test-utils";
import { EntityProvider } from "@backstage/plugin-catalog-react";
import {
  configApiRef,
  discoveryApiRef,
  fetchApiRef,
} from "@backstage/core-plugin-api";
import { ConfigReader } from "@backstage/config";
import type { Entity } from "@backstage/catalog-model";
import { apmeApiRef } from "../../api";
import { ApmeQualityActivityTab } from "./ApmeQualityActivityTab";

jest.mock("../../api/createApmeUiWorkflowAdapter", () => ({
  createApmeUiWorkflowAdapter: jest.fn(async () => ({})),
}));

jest.mock("../../utils/ensureRepoBranchForScan", () => ({
  ensureRepoBranchForScan: jest.fn(async () => undefined),
}));

jest.mock("../../utils/registerOrResolveApmeProject", () => ({
  registerOrResolveApmeProject: jest.fn(async () => ({
    id: "proj-1",
    name: "demo",
  })),
}));

jest.mock("@apme/ui-workflow", () => ({
  ApmeApiProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  AssessFindingsPanel: ({ findings }: { findings: unknown[] }) => (
    <div data-testid="assess-findings">{findings.length} findings</div>
  ),
  toPrFilesDiffUrl: (url: string) => `${url}/files`,
}));

function expectActivityDetailLoaded() {
  return screen.findByText(/1 violations/i);
}

jest.mock("../../hooks/useSyncPatternFlyTheme", () => ({
  useSyncPatternFlyTheme: jest.fn(),
}));

const entity: Entity = {
  apiVersion: "backstage.io/v1alpha1",
  kind: "Component",
  metadata: {
    name: "demo-repo",
    annotations: {
      "backstage.io/source-location":
        "url:https://github.com/example/demo-repo",
    },
  },
  spec: {
    type: "git-repository",
    owner: "user",
    repository_default_branch: "main",
  },
};

describe("ApmeQualityActivityTab", () => {
  const getActivity = jest.fn();
  const getActivityDetail = jest.fn();
  const createSuppression = jest.fn();

  beforeEach(() => {
    jest.clearAllMocks();
    getActivity.mockResolvedValue([
      {
        scan_id: "scan-1",
        session_id: "s1",
        project_path: "/demo",
        source: "portal",
        created_at: new Date().toISOString(),
        scan_type: "check",
        total_violations: 2,
        fixable: 1,
        ai_candidate: 0,
        ai_proposed: 0,
        ai_declined: 0,
        ai_accepted: 0,
        manual_review: 1,
        remediated_count: 0,
      },
      {
        scan_id: "scan-2",
        session_id: "s2",
        project_path: "/demo",
        source: "portal",
        created_at: new Date().toISOString(),
        scan_type: "remediate",
        total_violations: 0,
        fixable: 0,
        ai_candidate: 0,
        ai_proposed: 0,
        ai_declined: 0,
        ai_accepted: 0,
        manual_review: 0,
        remediated_count: 3,
        pr_url: "https://github.com/example/demo-repo/pull/7",
        branch_name: "apme/remediate-scan-2",
        commit_sha: "abc12345deadbeef",
      },
    ]);
    getActivityDetail.mockResolvedValue({
      scan_id: "scan-1",
      session_id: "s1",
      project_path: "/demo",
      source: "portal",
      created_at: new Date().toISOString(),
      scan_type: "check",
      total_violations: 1,
      fixable: 1,
      ai_candidate: 0,
      ai_proposed: 0,
      ai_declined: 0,
      ai_accepted: 0,
      manual_review: 0,
      remediated_count: 0,
      violations: [
        {
          id: 9,
          rule_id: "L001",
          level: "medium",
          message: "use FQCN",
          file: "play.yml",
          line: 1,
          remediation_class: 1,
          validator_source: "native",
        },
      ],
      proposals: [],
    });
  });

  function renderTab(initialEntry = "/") {
    return render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <TestApiProvider
          apis={[
            [apmeApiRef, { getActivity, getActivityDetail, createSuppression }],
            [
              configApiRef,
              new ConfigReader({
                ansible: {
                  apme: { enabled: true },
                  devSpaces: { baseUrl: "https://devspaces.example.com" },
                },
              }),
            ],
            [discoveryApiRef, { getBaseUrl: async () => "http://localhost" }],
            [fetchApiRef, { fetch: jest.fn() }],
          ]}
        >
          <ThemeProvider theme={createTheme()}>
            <EntityProvider entity={entity}>
              <ApmeQualityActivityTab />
            </EntityProvider>
          </ThemeProvider>
        </TestApiProvider>
      </MemoryRouter>,
    );
  }

  it("lists quality activity rows with sortable headers", async () => {
    renderTab();
    expect(
      await screen.findByText(/Quality activity \(2\)/i),
    ).toBeInTheDocument();
    expect(getActivity).toHaveBeenCalledWith("proj-1");
    expect(screen.getByText("Type")).toBeInTheDocument();
    expect(screen.getByText(/Time/)).toBeInTheDocument();
    expect(screen.getByText("Published")).toBeInTheDocument();
    expect(screen.getByText("check")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view pr/i })).toHaveAttribute(
      "href",
      "https://github.com/example/demo-repo/pull/7/files",
    );
    const devSpacesLink = screen.getByRole("link", {
      name: /open in dev spaces/i,
    });
    expect(devSpacesLink).toHaveAttribute(
      "href",
      "https://devspaces.example.com/#https://github.com/example/demo-repo/tree/main",
    );

    fireEvent.click(screen.getByText(/Violations/));
    expect(screen.getByText(/Violations/)).toHaveTextContent(/Violations/);
  });

  it("opens detail via ?activity= and closes with Close", async () => {
    renderTab("/?activity=scan-1");
    await expectActivityDetailLoaded();
    expect(screen.getByTestId("assess-findings")).toHaveTextContent(
      "1 findings",
    );
    expect(getActivityDetail).toHaveBeenCalledWith("scan-1");
    expect(
      screen.queryByText(/Quality activity detail/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Early access preview/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Acknowledge$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\bvia\b/i)).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /close activity detail/i }),
    );
    await waitFor(() => {
      expect(screen.getByText(/Quality activity \(2\)/i)).toBeInTheDocument();
    });
  });

  it("shows branch and SHA in list when remediate row has no PR", async () => {
    getActivity.mockResolvedValue([
      {
        scan_id: "scan-3",
        session_id: "s3",
        project_path: "/demo",
        source: "portal",
        created_at: new Date().toISOString(),
        scan_type: "remediate",
        total_violations: 0,
        fixable: 0,
        ai_candidate: 0,
        ai_proposed: 0,
        ai_declined: 0,
        ai_accepted: 0,
        manual_review: 0,
        remediated_count: 2,
        branch_name: "apme/remediate-only",
        commit_sha: "feedface12345678",
      },
    ]);

    renderTab();
    expect(
      await screen.findByText(/apme\/remediate-only @ feedface/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /view pr/i }),
    ).not.toBeInTheDocument();
  });

  it("shows PR link in activity detail for remediate rows", async () => {
    getActivityDetail.mockResolvedValue({
      scan_id: "scan-2",
      session_id: "s2",
      project_path: "/demo",
      source: "portal",
      created_at: new Date().toISOString(),
      scan_type: "remediate",
      total_violations: 0,
      fixable: 0,
      ai_candidate: 0,
      ai_proposed: 0,
      ai_declined: 0,
      ai_accepted: 0,
      manual_review: 0,
      remediated_count: 3,
      pr_url: "https://github.com/example/demo-repo/pull/7",
      branch_name: "apme/remediate-scan-2",
      commit_sha: "abc12345deadbeef",
      violations: [],
      proposals: [],
    });

    renderTab("/?activity=scan-2");
    expect(
      await screen.findByRole("link", { name: /view pr/i }),
    ).toHaveAttribute(
      "href",
      "https://github.com/example/demo-repo/pull/7/files",
    );
    expect(
      screen.getByText(/apme\/remediate-scan-2 @ abc12345/),
    ).toBeInTheDocument();
  });

  it("fleet ?rule= opens the latest activity automatically", async () => {
    // Filter must match a violation in the mock detail (QualityFindingsSection
    // hides non-matching rules — empty state is "No open findings…").
    renderTab("/?rule=L001");
    await waitFor(() => {
      expect(getActivityDetail).toHaveBeenCalledWith("scan-1");
    });
    await expectActivityDetailLoaded();
    expect(screen.getByTestId("assess-findings")).toHaveTextContent(
      "1 findings",
    );
  });
});
