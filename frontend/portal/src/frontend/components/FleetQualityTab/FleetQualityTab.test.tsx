/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { TestApiProvider } from "@backstage/test-utils";
import { ThemeProvider, createTheme } from "@material-ui/core/styles";
import { configApiRef } from "@backstage/core-plugin-api";
import { ConfigReader } from "@backstage/config";
import { catalogApiRef } from "@backstage/plugin-catalog-react";
import { Entity } from "@backstage/catalog-model";
import { inlineTextColorForSeverity } from "../../../common/severity";
import { FleetQualityTab } from "./FleetQualityTab";
import { apmeApiRef } from "../../api";
import { MockApmeApiClient } from "../../api/mock/MockApmeApiClient";

jest.mock("@backstage/core-components", () => {
  const actual = jest.requireActual("@backstage/core-components");
  return {
    ...actual,
    Progress: () => <div data-testid="progress">Loading...</div>,
  };
});

const theme = createTheme();

function catalogEntity(name: string, repoUrl: string): Entity {
  return {
    apiVersion: "backstage.io/v1alpha1",
    kind: "Component",
    metadata: {
      name,
      annotations: {
        "backstage.io/source-location": `url:${repoUrl}`,
      },
    },
    spec: { type: "git-repository" },
  };
}

describe("FleetQualityTab", () => {
  const mockApmeApi = new MockApmeApiClient();
  const repositoryDetailPath = (entityName: string, ruleId?: string) => {
    const base = `/self-service/repositories/${entityName}?tab=quality-activity`;
    return ruleId ? `${base}&rule=${encodeURIComponent(ruleId)}` : base;
  };

  const renderTab = () =>
    render(
      <MemoryRouter>
        <TestApiProvider
          apis={[
            [
              configApiRef,
              new ConfigReader({ ansible: { apme: { enabled: true } } }),
            ],
            [apmeApiRef, mockApmeApi],
            [
              catalogApiRef,
              {
                getEntities: async () => ({
                  items: [
                    catalogEntity(
                      "amazon-aws-acme-scm-github-com",
                      "https://github.com/acme-scm/amazon.aws",
                    ),
                    catalogEntity(
                      "network-firewall-ansible-demo-github-com",
                      "https://github.com/ansible-demo/network-firewall",
                    ),
                  ],
                }),
              },
            ],
          ]}
        >
          <ThemeProvider theme={theme}>
            <FleetQualityTab repositoryDetailPath={repositoryDetailPath} />
          </ThemeProvider>
        </TestApiProvider>
      </MemoryRouter>,
    );

  it("colors the headline violation total by worst fleet severity", async () => {
    renderTab();

    const summary = await screen.findByText(
      /violations · .* rules · .* repositories/i,
      {},
      { timeout: 5000 },
    );
    const headline = summary.previousElementSibling;
    expect(headline).not.toBeNull();
    expect(headline).toHaveStyle({
      color: inlineTextColorForSeverity("critical", "light"),
    });
  });

  it("renders fleet summary, Preview chip, and grouped violations from mock fixtures", async () => {
    renderTab();

    expect(
      await screen.findByText("Fleet quality", {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("preview-chip")).toHaveTextContent(
      "Early access preview",
    );
    expect(screen.getByTestId("preview-feedback-link")).toBeInTheDocument();
    expect(
      await screen.findByText(
        /violations · .* rules · .* repositories/i,
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();

    await waitFor(
      () => {
        expect(
          screen.queryByText("No violations match the current filter."),
        ).not.toBeInTheDocument();
      },
      { timeout: 5000 },
    );
  });

  it("shows no-scans empty state when repos exist but none have been scanned", async () => {
    const unscannedApmeApi = {
      ...mockApmeApi,
      getProjects: async () => [
        {
          id: "proj-new",
          name: "my-repo",
          repo_url: "https://github.com/acme-scm/amazon.aws",
          branch: "main",
          created_at: new Date().toISOString(),
          health_score: 0,
          total_violations: 0,
          scan_count: 0,
          has_scm_token: false,
          has_new_commits: false,
        },
      ],
    };

    render(
      <MemoryRouter>
        <TestApiProvider
          apis={[
            [
              configApiRef,
              new ConfigReader({ ansible: { apme: { enabled: true } } }),
            ],
            [apmeApiRef, unscannedApmeApi],
            [
              catalogApiRef,
              {
                getEntities: async () => ({
                  items: [
                    catalogEntity(
                      "amazon-aws-acme-scm-github-com",
                      "https://github.com/acme-scm/amazon.aws",
                    ),
                  ],
                }),
              },
            ],
          ]}
        >
          <ThemeProvider theme={theme}>
            <FleetQualityTab repositoryDetailPath={repositoryDetailPath} />
          </ThemeProvider>
        </TestApiProvider>
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("No scans yet", {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /Start a scan to check your Git repositories for quality issues/i,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Fleet quality")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Start scan/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("All repositories are clean"),
    ).not.toBeInTheDocument();
  });

  it("shows APME unavailable when gateway is down but catalog has repositories", async () => {
    const gatewayDownApmeApi = {
      ...mockApmeApi,
      getProjects: async () => {
        throw new Error("Failed to connect to APME: fetch failed");
      },
    };

    render(
      <MemoryRouter>
        <TestApiProvider
          apis={[
            [
              configApiRef,
              new ConfigReader({ ansible: { apme: { enabled: true } } }),
            ],
            [apmeApiRef, gatewayDownApmeApi],
            [
              catalogApiRef,
              {
                getEntities: async () => ({
                  items: [
                    catalogEntity(
                      "amazon-aws-acme-scm-github-com",
                      "https://github.com/acme-scm/amazon.aws",
                    ),
                  ],
                }),
              },
            ],
          ]}
        >
          <ThemeProvider theme={theme}>
            <FleetQualityTab repositoryDetailPath={repositoryDetailPath} />
          </ThemeProvider>
        </TestApiProvider>
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("APME unavailable", {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("No Git Repositories Found"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("No scans yet")).not.toBeInTheDocument();
  });

  it('shows empty state with "Add repository" button when no repos exist', async () => {
    const emptyApmeApi = {
      ...mockApmeApi,
      getProjects: async () => [],
    };

    render(
      <MemoryRouter>
        <TestApiProvider
          apis={[
            [
              configApiRef,
              new ConfigReader({ ansible: { apme: { enabled: true } } }),
            ],
            [apmeApiRef, emptyApmeApi],
            [catalogApiRef, { getEntities: async () => ({ items: [] }) }],
          ]}
        >
          <ThemeProvider theme={theme}>
            <FleetQualityTab repositoryDetailPath={repositoryDetailPath} />
          </ThemeProvider>
        </TestApiProvider>
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(
        "No Git Repositories Found",
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/No git repositories were retrieved/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Add repository/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("All repositories are clean"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Fleet quality")).not.toBeInTheDocument();
  });

  it("shows empty state when catalog has no repos but APME still has projects", async () => {
    render(
      <MemoryRouter>
        <TestApiProvider
          apis={[
            [
              configApiRef,
              new ConfigReader({ ansible: { apme: { enabled: true } } }),
            ],
            [apmeApiRef, mockApmeApi],
            [catalogApiRef, { getEntities: async () => ({ items: [] }) }],
          ]}
        >
          <ThemeProvider theme={theme}>
            <FleetQualityTab repositoryDetailPath={repositoryDetailPath} />
          </ThemeProvider>
        </TestApiProvider>
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(
        "No Git Repositories Found",
        {},
        { timeout: 5000 },
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Fleet quality")).not.toBeInTheDocument();
    expect(screen.queryByText(/violations ·/i)).not.toBeInTheDocument();
  });

  it("shows disabled message when APME is not enabled", async () => {
    render(
      <MemoryRouter>
        <TestApiProvider
          apis={[
            [
              configApiRef,
              new ConfigReader({ ansible: { apme: { enabled: false } } }),
            ],
            [apmeApiRef, mockApmeApi],
            [catalogApiRef, { getEntities: async () => ({ items: [] }) }],
          ]}
        >
          <ThemeProvider theme={theme}>
            <FleetQualityTab repositoryDetailPath={repositoryDetailPath} />
          </ThemeProvider>
        </TestApiProvider>
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(/Content quality scanning is disabled/i),
    ).toBeInTheDocument();
  });
});
