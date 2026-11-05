/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import "@testing-library/jest-dom";
import type { ComponentProps } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@material-ui/core";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import type { Entity } from "@backstage/catalog-model";
import type { ApmeProjectContext } from "../../hooks/useApmeProjectContext";
import type { Project, Violation } from "../../../common/types";
import { ApmeRepositoryOverviewCard } from "./ApmeRepositoryOverviewCard";
import {
  ApmeRepositoryOverviewClean,
  ApmeRepositoryOverviewFindingsUnavailable,
  ApmeRepositoryOverviewLoading,
  ApmeRepositoryOverviewNoScan,
  ApmeRepositoryOverviewViolations,
} from "./ApmeRepositoryOverviewCardStates";
import {
  getCodePreviewTokens,
  getDependencySourceTokens,
  getDependencyViolationTokens,
  getFixTypeColorTokens,
  getPreviewSurfaceTokens,
  getSeverityColorTokens,
  SEVERITY_ORDER,
  type SeverityLevel,
} from "../../../common/severity";

const mockUseApmeProjectContext = jest.fn<ApmeProjectContext, [Entity]>();
const mockUseApmeAiEnabled = jest.fn(() => true);

jest.mock("../../hooks/useApmeProjectContext", () => ({
  useApmeProjectContext: (entity: Entity) => mockUseApmeProjectContext(entity),
}));

jest.mock("../../hooks/useApmeEnabled", () => ({
  useApmeAiEnabled: () => mockUseApmeAiEnabled(),
}));

const entity: Entity = {
  apiVersion: "backstage.io/v1alpha1",
  kind: "Component",
  metadata: {
    name: "terrible-playbook",
    annotations: {
      "backstage.io/source-location":
        "url:https://github.com/acme/terrible-playbook",
    },
  },
  spec: {
    type: "git-repository",
    repository_default_branch: "main",
  },
};

function project(
  partial: Partial<Project> & Pick<Project, "id" | "total_violations">,
): Project {
  return {
    name: "terrible-playbook",
    repo_url: "https://github.com/acme/terrible-playbook",
    branch: "main",
    created_at: "2026-01-01T00:00:00Z",
    health_score: 40,
    violation_trend: "stable",
    scan_count: 1,
    last_scanned_at: "2026-08-27T20:05:26Z",
    has_scm_token: false,
    has_new_commits: false,
    latest_scan: {
      scan_id: "scan-416",
      scan_type: "check",
      total_violations: partial.total_violations,
      fixable: 188,
      ai_candidate: 188,
      manual_review: 226,
      remediated_count: 0,
    },
    ...partial,
  };
}

const violations: Violation[] = [
  {
    id: 1,
    rule_id: "L003",
    level: "low",
    message: "Play should have a name",
    file: "playbook-L057-wrong-module.yml",
    line: 4,
    remediation_class: 3,
    validator_source: "native",
  },
  {
    id: 2,
    rule_id: "R108",
    level: "high",
    message: "Unsafe permissions",
    file: "tasks/main.yml",
    line: 12,
    remediation_class: 2,
    validator_source: "native",
  },
];

function baseContext(
  overrides: Partial<ApmeProjectContext> = {},
): ApmeProjectContext {
  return {
    repoUrl: "https://github.com/acme/terrible-playbook",
    branch: "main",
    project: project({ id: "proj-1", total_violations: 2 }),
    violations,
    rulesById: new Map(),
    dependencies: null,
    loading: false,
    violationsLoading: false,
    dependenciesLoading: false,
    error: undefined,
    refresh: jest.fn(),
    refreshViolations: jest.fn(),
    registerAndScan: jest.fn(),
    registering: false,
    registerError: null,
    ...overrides,
  };
}

function SearchParamsProbe() {
  const [searchParams] = useSearchParams();
  return <div data-testid="search-params">{searchParams.toString()}</div>;
}

function renderCard(
  initialSearch = "?tab=overview",
  contextOverrides: Partial<ApmeProjectContext> = {},
  navigation: Partial<ComponentProps<typeof ApmeRepositoryOverviewCard>> = {},
) {
  mockUseApmeProjectContext.mockReturnValue(baseContext(contextOverrides));

  return render(
    <MemoryRouter
      initialEntries={[`/repositories/terrible-playbook${initialSearch}`]}
    >
      <ThemeProvider theme={createTheme()}>
        <Routes>
          <Route
            path="/repositories/:name"
            element={
              <>
                <SearchParamsProbe />
                <ApmeRepositoryOverviewCard
                  {...navigation}
                  context={
                    { entity } as ComponentProps<
                      typeof ApmeRepositoryOverviewCard
                    >["context"]
                  }
                />
              </>
            }
          />
        </Routes>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("ApmeRepositoryOverviewCard", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUseApmeAiEnabled.mockReturnValue(true);
  });
  it("uses host callbacks without altering legacy route parameters", () => {
    const open = jest.fn();
    renderCard("?portalFrontend=nfs", {}, { onOpenLatestScan: open });
    fireEvent.click(screen.getByRole("button", { name: /quality summary/i }));
    expect(open).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("search-params")).toHaveTextContent(
      "portalFrontend=nfs",
    );
  });
  it("opens Quality through the host from the unscanned state without starting a scan", () => {
    const open = jest.fn();
    renderCard(
      "?portalFrontend=nfs",
      { project: null, violations: [] },
      { onOpenQuality: open },
    );
    fireEvent.click(screen.getByRole("button", { name: "Open Quality" }));
    expect(open).toHaveBeenCalledTimes(1);
    expect(
      mockUseApmeProjectContext.mock.results[
        mockUseApmeProjectContext.mock.results.length - 1
      ].value.registerAndScan,
    ).not.toHaveBeenCalled();
    expect(screen.getByTestId("search-params")).toHaveTextContent(
      "portalFrontend=nfs",
    );
  });
  it("does not fall back to private URL navigation when host navigation is unavailable", () => {
    renderCard("?portalFrontend=nfs", {}, { navigationDisabled: true });
    fireEvent.click(
      screen.getByText("Details unavailable").closest("[aria-disabled]")!,
    );
    expect(screen.getByTestId("search-params")).toHaveTextContent(
      "portalFrontend=nfs",
    );
  });
  it("distinguishes a registered but unscanned project from a clean scan", () => {
    renderCard("?tab=overview", {
      project: project({
        id: "new",
        total_violations: 0,
        scan_count: 0,
        latest_scan: undefined,
        last_scanned_at: undefined,
      }),
      violations: [],
    });
    expect(screen.getByText(/No quality scans yet/)).toBeInTheDocument();
    expect(
      screen.queryByText(/No violations detected/),
    ).not.toBeInTheDocument();
  });
  it("does not claim a clean result when reported findings are missing", () => {
    renderCard("?tab=overview", { violations: [] });
    expect(
      screen.getByText(/Quality findings unavailable/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/No violations detected/),
    ).not.toBeInTheDocument();
  });
  it("labels partial findings honestly", () => {
    renderCard("?tab=overview", {
      project: project({ id: "partial", total_violations: 5 }),
    });
    expect(
      screen.getByText("Partial findings: showing 2 of 5."),
    ).toBeInTheDocument();
  });
  it("does not report clean when fetching an apparently empty assessment fails", () => {
    renderCard("?tab=overview", {
      project: project({ id: "clean", total_violations: 0 }),
      violations: [],
      violationsError: new Error("Findings request failed"),
    });
    expect(
      screen.getByText(/Quality findings unavailable/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/No violations detected/),
    ).not.toBeInTheDocument();
  });

  it("navigates to the latest scan detail when the card is clicked", () => {
    renderCard();

    fireEvent.click(
      screen.getByRole("button", {
        name: /quality summary — view latest scan details/i,
      }),
    );

    expect(screen.getByTestId("search-params")).toHaveTextContent(
      "tab=quality-activity&activity=scan-416",
    );
  });

  it("navigates to quality activity list when latest_scan id is missing", () => {
    renderCard("?tab=overview", {
      project: project({
        id: "proj-1",
        total_violations: 2,
        latest_scan: undefined,
      }),
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: /quality summary — view latest scan details/i,
      }),
    );

    expect(screen.getByTestId("search-params")).toHaveTextContent(
      "tab=quality-activity",
    );
  });

  it("does not navigate when a category help icon is clicked", () => {
    renderCard();

    fireEvent.click(
      screen.getByRole("button", {
        name: /code quality issues — naming conventions, fqcn usage, jinja spacing, and changed_when/i,
      }),
    );

    expect(screen.getByTestId("search-params")).toHaveTextContent(
      "tab=overview",
    );
  });

  it("does not navigate when a category help icon is activated via keyboard", () => {
    renderCard();

    const helpButton = screen.getByRole("button", {
      name: /code quality issues — naming conventions, fqcn usage, jinja spacing, and changed_when/i,
    });
    helpButton.focus();
    fireEvent.keyDown(helpButton, { key: "Enter", code: "Enter" });
    fireEvent.keyDown(helpButton, { key: " ", code: "Space" });

    expect(screen.getByTestId("search-params")).toHaveTextContent(
      "tab=overview",
    );
  });

  it("opens the Quality tab from the unscanned Scan action", () => {
    renderCard("?tab=overview", {
      project: null,
      violations: [],
    });

    fireEvent.click(screen.getByRole("button", { name: /^scan$/i }));

    expect(screen.getByTestId("search-params")).toHaveTextContent(
      "tab=quality",
    );
  });

  it("navigates to the latest scan from the clean-scan card", () => {
    renderCard("?tab=overview", {
      project: project({ id: "proj-1", total_violations: 0 }),
      violations: [],
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: /quality summary — view latest scan details/i,
      }),
    );

    expect(screen.getByTestId("search-params")).toHaveTextContent(
      "tab=quality-activity&activity=scan-416",
    );
  });

  it("shows loading while project context is loading", () => {
    renderCard("?tab=overview", { loading: true });
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("shows gateway unavailable for connection errors", () => {
    renderCard("?tab=overview", {
      error: new Error("Failed to connect to APME"),
      project: null,
      violations: [],
    });
    expect(screen.getByText(/temporarily unavailable/i)).toBeInTheDocument();
  });

  it("opens Quality from View details when findings are unavailable", () => {
    renderCard("?tab=overview", { violations: [] });
    fireEvent.click(screen.getByRole("button", { name: "View details" }));
    expect(screen.getByTestId("search-params")).toHaveTextContent(
      "tab=quality",
    );
  });

  it("uses host callback for View details when findings are unavailable", () => {
    const open = jest.fn();
    renderCard("?tab=overview", { violations: [] }, { onOpenQuality: open });
    fireEvent.click(screen.getByRole("button", { name: "View details" }));
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("navigates from violations card via keyboard", () => {
    renderCard();
    const card = screen.getByRole("button", {
      name: /quality summary — view latest scan details/i,
    });
    fireEvent.keyDown(card, { key: " " });
    expect(screen.getByTestId("search-params")).toHaveTextContent(
      "tab=quality-activity&activity=scan-416",
    );
  });

  it("renders policy category breakdown when policy violations exist", () => {
    renderCard("?tab=overview", {
      violations: [
        ...violations,
        {
          id: 3,
          rule_id: "POL001",
          level: "medium",
          message: "OPA policy",
          file: "policy.yml",
          line: 1,
          remediation_class: 3,
          validator_source: "native",
        },
      ],
      project: project({ id: "proj-1", total_violations: 3 }),
    });
    expect(screen.getByText("Policy")).toBeInTheDocument();
  });
});

describe("ApmeRepositoryOverviewCardStates", () => {
  const stateClasses = {
    card: "card",
    cardClickable: "cardClickable",
    categoryRow: "categoryRow",
    categoryHelp: "categoryHelp",
  };
  const colorTokens = {
    mode: "light" as const,
    severity: getSeverityColorTokens("light"),
    fixType: getFixTypeColorTokens("light"),
    dependencySource: getDependencySourceTokens("light"),
    dependencyViolation: getDependencyViolationTokens("light"),
    codePreview: getCodePreviewTokens("light"),
    preview: getPreviewSurfaceTokens("light"),
  };

  it("renders exported loading state", () => {
    render(<ApmeRepositoryOverviewLoading />);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("calls onViewDetails from findings unavailable state", () => {
    const onViewDetails = jest.fn();
    render(
      <ApmeRepositoryOverviewFindingsUnavailable
        classes={stateClasses}
        navigationDisabled={false}
        onViewDetails={onViewDetails}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "View details" }));
    expect(onViewDetails).toHaveBeenCalledTimes(1);
  });

  it("disables scan in no-scan state without repo URL", () => {
    render(
      <ApmeRepositoryOverviewNoScan
        classes={stateClasses}
        navigationDisabled={false}
        repoUrl={null}
        onScan={jest.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /^scan$/i })).toBeDisabled();
  });

  it("activates clean state from keyboard", () => {
    const onOpenLatestScan = jest.fn();
    render(
      <ThemeProvider theme={createTheme()}>
        <ApmeRepositoryOverviewClean
          classes={stateClasses}
          navigationDisabled={false}
          colorTokens={colorTokens}
          onOpenLatestScan={onOpenLatestScan}
        />
      </ThemeProvider>,
    );
    const card = screen.getByRole("button", {
      name: /quality summary — view latest scan details/i,
    });
    fireEvent.keyDown(card, { key: "Enter" });
    expect(onOpenLatestScan).toHaveBeenCalledTimes(1);
  });

  it("renders violations state with disabled navigation, partial findings, and category rows", () => {
    const onOpenLatestScan = jest.fn();
    const theme = createTheme();
    const sevCounts = SEVERITY_ORDER.reduce(
      (acc, sev) => {
        acc[sev] = 0;
        return acc;
      },
      {} as Record<SeverityLevel, number>,
    );
    sevCounts.critical = 1;
    sevCounts.low = 1;

    const lintViolation: Violation = {
      id: 10,
      rule_id: "L003",
      level: "critical",
      message: "lint issue",
      file: "play.yml",
      line: 1,
      remediation_class: 1,
      validator_source: "native",
    };
    const riskViolation: Violation = {
      id: 11,
      rule_id: "R108",
      level: "low",
      message: "risk issue",
      file: "tasks.yml",
      line: 2,
      remediation_class: 3,
      validator_source: "native",
    };

    render(
      <ThemeProvider theme={theme}>
        <ApmeRepositoryOverviewViolations
          classes={stateClasses}
          theme={theme}
          colorTokens={colorTokens}
          navigationDisabled
          categoryMeta={[
            { key: "lint", label: "Lint", tip: "Lint help" },
            { key: "risk", label: "Risk", tip: "Risk help" },
          ]}
          totalViolations={2}
          reportedViolations={5}
          fixable={1}
          sevCounts={sevCounts}
          catCounts={{
            lint: 1,
            risk: 1,
            modernize: 0,
            secrets: 0,
            policy: 0,
            dependencies: 0,
          }}
          violations={[lintViolation, riskViolation]}
          rulesById={new Map()}
          onOpenLatestScan={onOpenLatestScan}
        />
      </ThemeProvider>,
    );

    expect(screen.getByText("Details unavailable")).toBeInTheDocument();
    expect(
      screen.getByText("Partial findings: showing 2 of 5."),
    ).toBeInTheDocument();
    expect(screen.getByText("Lint")).toBeInTheDocument();
    expect(screen.getByText("Risk")).toBeInTheDocument();

    fireEvent.click(
      screen.getByLabelText("Quality summary — view latest scan details"),
    );
    expect(onOpenLatestScan).not.toHaveBeenCalled();
  });

  it("does not open latest scan when category help is activated", () => {
    const onOpenLatestScan = jest.fn();
    const theme = createTheme();
    const sevCounts = SEVERITY_ORDER.reduce(
      (acc, sev) => {
        acc[sev] = 0;
        return acc;
      },
      {} as Record<SeverityLevel, number>,
    );
    sevCounts.critical = 1;

    const lintViolation: Violation = {
      id: 10,
      rule_id: "L003",
      level: "critical",
      message: "lint issue",
      file: "play.yml",
      line: 1,
      remediation_class: 1,
      validator_source: "native",
    };

    render(
      <ThemeProvider theme={theme}>
        <ApmeRepositoryOverviewViolations
          classes={stateClasses}
          theme={theme}
          colorTokens={colorTokens}
          navigationDisabled={false}
          categoryMeta={[{ key: "lint", label: "Lint", tip: "Lint help" }]}
          totalViolations={1}
          reportedViolations={1}
          fixable={1}
          sevCounts={sevCounts}
          catCounts={{
            lint: 1,
            risk: 0,
            modernize: 0,
            secrets: 0,
            policy: 0,
            dependencies: 0,
          }}
          violations={[lintViolation]}
          rulesById={new Map()}
          onOpenLatestScan={onOpenLatestScan}
        />
      </ThemeProvider>,
    );

    const lintHelp = screen.getByRole("button", { name: "Lint help" });
    fireEvent.click(lintHelp);
    expect(onOpenLatestScan).not.toHaveBeenCalled();
    fireEvent.keyDown(lintHelp, { key: "Enter" });
    expect(onOpenLatestScan).not.toHaveBeenCalled();
  });
});
