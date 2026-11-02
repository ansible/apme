import "@testing-library/jest-dom";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@material-ui/core/styles";
import { FleetQualityTab } from "./FleetQualityTab";
import type { FleetQualityData } from "./useFleetQualityData";
import { MOCK_PROJECTS } from "../../api/mock/fixtures";

let mockEnabled = true;
let mockAiEnabled = true;
let mockLoading = false;
let mockData: FleetQualityData | undefined;
jest.mock("../../hooks/useApmeEnabled", () => ({
  useApmeEnabled: () => mockEnabled,
  useApmeAiEnabled: () => mockAiEnabled,
}));
jest.mock("./useFleetQualityData", () => ({
  useFleetQualityData: () => ({ value: mockData, loading: mockLoading }),
}));
jest.mock("../PreviewChip", () => ({
  PreviewLabelRow: () => <div>Preview</div>,
}));

describe("Fleet quality interactions", () => {
  const repositoryDetailPath = jest.fn(
    (name: string, rule?: string) => `/repos/${name}?rule=${rule}`,
  );
  const repo = (
    name: string,
    count: number,
    remediationClass: number,
    scanned?: string,
  ) => ({
    project: { ...MOCK_PROJECTS[0], id: name, name },
    entityName: name,
    count,
    remediationClass,
    lastScannedAt: scanned,
  });
  beforeEach(() => {
    jest.clearAllMocks();
    mockEnabled = true;
    mockAiEnabled = true;
    mockLoading = false;
    mockData = {
      groups: [
        {
          ruleId: "R001",
          message: "Critical finding",
          level: "critical",
          category: "Category A",
          totalCount: 3,
          repos: [repo("first", 2, 1, "2026-01-01"), repo("second", 1, 2)],
        },
        {
          ruleId: "R002",
          message: "Medium finding",
          level: "medium",
          category: "Category B",
          totalCount: 4,
          repos: [repo("first", 4, 3)],
        },
        {
          ruleId: "R003",
          message: "Informational finding",
          level: "info",
          totalCount: 1,
          repos: [repo("second", 1, 0)],
        },
      ],
      reposWithIssues: 2,
      totalRepos: 3,
      hasAnyScan: true,
      gatewayUnavailable: false,
      violationTotal: 8,
      severityCounts: {
        critical: 3,
        error: 0,
        high: 0,
        medium: 4,
        low: 0,
        info: 1,
      },
    };
  });
  const mount = (dark = false) =>
    render(
      <MemoryRouter>
        <ThemeProvider
          theme={createTheme({ palette: { type: dark ? "dark" : "light" } })}
        >
          <FleetQualityTab repositoryDetailPath={repositoryDetailPath} />
        </ThemeProvider>
      </MemoryRouter>,
    );
  const severity = (name: string) =>
    screen.getByText(name).closest('[role="button"]') as HTMLElement;

  it.each(["Severity", "Rule", "Category", "Repos", "Occurrences"])(
    "sorts by %s in both directions and retains all findings",
    (heading) => {
      mount();
      const column = screen.getByRole("columnheader", {
        name: new RegExp(`^${heading}`),
      });
      fireEvent.click(column);
      const firstDirection = column.textContent;
      expect(firstDirection).toMatch(/[↑↓]/);
      expect(
        screen.getAllByText(/^(Critical|Medium|Informational) finding$/),
      ).toHaveLength(3);
      fireEvent.click(column);
      expect(column.textContent).not.toBe(firstDirection);
      expect(
        screen.getAllByText(/^(Critical|Medium|Informational) finding$/),
      ).toHaveLength(3);
    },
  );

  it("orders rules by occurrence count rather than severity when requested", () => {
    mount();
    fireEvent.click(screen.getByRole("columnheader", { name: /^Occurrences/ }));
    const rows = screen.getAllByRole("row").slice(1);
    expect(
      rows.map((row) => within(row).getByText(/finding$/).textContent),
    ).toEqual(["Medium finding", "Critical finding", "Informational finding"]);
  });

  it("toggles severity filters by mouse and keyboard without treating unrelated keys as activation", () => {
    mount();
    fireEvent.click(severity("critical"));
    expect(screen.getByText("Showing 3 of 8 violations")).toBeInTheDocument();
    expect(screen.queryByText("Medium finding")).not.toBeInTheDocument();
    fireEvent.keyDown(severity("critical"), { key: "Escape" });
    expect(screen.queryByText("Medium finding")).not.toBeInTheDocument();
    fireEvent.keyDown(severity("critical"), { key: "Enter" });
    expect(screen.getByText("Medium finding")).toBeInTheDocument();
    fireEvent.keyDown(severity("medium"), { key: " " });
    expect(screen.getByText("Showing 4 of 8 violations")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("button", { name: "Clear filters" }), {
      key: " ",
    });
    expect(screen.getByText("Critical finding")).toBeInTheDocument();
  });

  it("combines category and severity filters, displays an honest empty result, and clears all filters", () => {
    mount();
    fireEvent.click(screen.getByText("Category A"));
    expect(screen.getByText("Showing 3 of 8 violations")).toBeInTheDocument();
    fireEvent.click(severity("medium"));
    expect(
      screen.getByText("No violations match the current filters."),
    ).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("button", { name: "Clear filters" }), {
      key: "Escape",
    });
    expect(
      screen.getByText("No violations match the current filters."),
    ).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("button", { name: "Clear filters" }), {
      key: "Enter",
    });
    expect(screen.getByText("Informational finding")).toBeInTheDocument();
    fireEvent.click(severity("info"));
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByText("Critical finding")).toBeInTheDocument();
  });

  it("removes the category filter through its chip", () => {
    mount();
    fireEvent.click(screen.getByText("Category A"));
    const chip = screen
      .getAllByText("Category A")
      .find((el) => el.closest(".MuiChip-root"))!
      .closest(".MuiChip-root")!;
    fireEvent.keyUp(chip, { key: "Backspace", keyCode: 8 });
    expect(
      screen.queryByRole("button", { name: "Clear filters" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Medium finding")).toBeInTheDocument();
  });

  it.each([false, true])(
    "expands rule repositories and scopes drill-down links in dark mode %s",
    (dark) => {
      mockAiEnabled = !dark;
      mount(dark);
      fireEvent.click(screen.getByText("Critical finding"));
      expect(screen.getByText("first")).toBeInTheDocument();
      expect(screen.getByText("×2")).toBeInTheDocument();
      expect(screen.getByText("2026-01-01")).toBeInTheDocument();
      const links = screen.getAllByRole("link", { name: "View details →" });
      expect(links.map((link) => link.getAttribute("href"))).toEqual([
        "/repos/first?rule=R001",
        "/repos/second?rule=R001",
      ]);
      links[0].addEventListener("click", (event) => event.preventDefault());
      fireEvent.click(links[0]);
      expect(screen.getByText("first")).toBeInTheDocument();
      fireEvent.click(screen.getByText("Critical finding"));
      expect(screen.queryByText("first")).not.toBeInTheDocument();
    },
  );

  it("does not render the fleet while scanning is disabled or data is loading", async () => {
    mockEnabled = false;
    const { unmount } = mount();
    expect(
      screen.getByText(/Content quality scanning is disabled/),
    ).toBeInTheDocument();
    unmount();
    mockEnabled = true;
    mockLoading = true;
    mount();
    expect(await screen.findByRole("progressbar")).toBeInTheDocument();
  });

  it("shows no repositories when data is absent", () => {
    mockData = undefined;
    mount();
    expect(screen.getByText("No Git Repositories Found")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add repository" }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining("apme-register-git-repository"),
    );
  });

  it("shows a clean fleet without interpreting missing findings as a service failure", () => {
    mockData = {
      ...mockData!,
      groups: [],
      violationTotal: 0,
      reposWithIssues: 0,
      severityCounts: {
        critical: 0,
        error: 0,
        high: 0,
        medium: 0,
        low: 0,
        info: 0,
      },
    };
    mount();
    expect(screen.getByText("All repositories are clean")).toBeInTheDocument();
    expect(screen.getByText("3 clean")).toBeInTheDocument();
  });
});
