/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { ThemeProvider, createTheme } from "@material-ui/core/styles";
import { TestApiProvider, mockApis } from "@backstage/test-utils";
import { EntityProvider } from "@backstage/plugin-catalog-react";
import { configApiRef, errorApiRef } from "@backstage/core-plugin-api";
import { translationApiRef } from "@backstage/core-plugin-api/alpha";
import { ConfigReader } from "@backstage/config";
import type { Activity, ActivityDetail } from "../../../common/types";
import { apmeApiRef } from "../../api";
import { useResolveApmeProject } from "../../hooks/useResolveApmeProject";
import { ApmeQualityActivityTab } from "./ApmeQualityActivityTab";

jest.mock("../../hooks/useResolveApmeProject");
jest.mock("../../hooks/useSyncPatternFlyTheme", () => ({
  useSyncPatternFlyTheme: jest.fn(),
}));
jest.mock("@apme/ui-workflow", () => ({
  ApmeApiProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  AssessFindingsPanel: () => <div>Historical findings</div>,
  toPrFilesDiffUrl: (url: string) => `${url}/files`,
}));

const resolveProject = jest.mocked(useResolveApmeProject);
const getActivity = jest.fn();
const getActivityDetail = jest.fn();
const getRules = jest.fn();

function activity(
  scan_id: string,
  overrides: Partial<Activity> = {},
): Activity {
  return {
    scan_id,
    session_id: scan_id,
    project_path: "/demo",
    source: "portal",
    scan_type: "check",
    created_at: "2026-10-16T09:00:00Z",
    total_violations: 1,
    fixable: 1,
    remediated_count: 0,
    manual_review: 0,
    ai_candidate: 0,
    ai_proposed: 0,
    ai_declined: 0,
    ai_accepted: 0,
    ...overrides,
  };
}

function detail(
  scan_id: string,
  overrides: Partial<ActivityDetail> = {},
): ActivityDetail {
  return { ...activity(scan_id), violations: [], proposals: [], ...overrides };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function Navigation() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="query">{location.search}</output>
      <button onClick={() => navigate("/?activity=next&keep=yes")}>
        Next activity
      </button>
      <button onClick={() => navigate("/?keep=yes")}>Activity list</button>
    </>
  );
}

function renderTab(entry = "/", dark = false) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <TestApiProvider
        apis={[
          [apmeApiRef, { getActivity, getActivityDetail, getRules }],
          [configApiRef, new ConfigReader({})],
          [errorApiRef, { post: jest.fn() }],
          [translationApiRef, mockApis.translation()],
        ]}
      >
        <ThemeProvider
          theme={createTheme({ palette: { type: dark ? "dark" : "light" } })}
        >
          <EntityProvider
            entity={{
              apiVersion: "backstage.io/v1alpha1",
              kind: "Component",
              metadata: { name: "demo" },
            }}
          >
            <Navigation />
            <ApmeQualityActivityTab />
          </EntityProvider>
        </ThemeProvider>
      </TestApiProvider>
    </MemoryRouter>,
  );
}

function tableRows() {
  return within(screen.getByRole("grid")).getAllByRole("row").slice(1);
}

describe("ApmeQualityActivityTab interactions and edge cases", () => {
  beforeEach(() => {
    jest.resetAllMocks();
    resolveProject.mockReturnValue({
      adapter: {} as NonNullable<
        ReturnType<typeof useResolveApmeProject>["adapter"]
      >,
      projectId: "project-1",
      error: null,
      unavailable: false,
    });
    getActivity.mockResolvedValue([]);
    getActivityDetail.mockResolvedValue(detail("scan-1"));
    getRules.mockResolvedValue([]);
    jest
      .spyOn(Date, "now")
      .mockReturnValue(new Date("2026-10-16T10:00:00Z").getTime());
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it.each(["Violations", "Fixable", "Remediated", "Manual"])(
    "sorts %s numerically in both directions",
    async (column) => {
      getActivity.mockResolvedValue([
        activity("small", {
          total_violations: 2,
          fixable: 2,
          remediated_count: 2,
          manual_review: 2,
          branch_name: "small",
        }),
        activity("large", {
          total_violations: 10,
          fixable: 10,
          remediated_count: 10,
          manual_review: 10,
          branch_name: "large",
        }),
      ]);
      renderTab();
      await screen.findByRole("grid");
      fireEvent.click(screen.getByRole("columnheader", { name: column }));
      expect(
        screen.getByRole("columnheader", { name: `${column} ↓` }),
      ).toBeInTheDocument();
      expect(
        tableRows().map(
          (row) => within(row).getByText(/^(small|large)$/).textContent,
        ),
      ).toEqual(["large", "small"]);
      fireEvent.click(
        screen.getByRole("columnheader", { name: `${column} ↓` }),
      );
      expect(
        screen.getByRole("columnheader", { name: `${column} ↑` }),
      ).toBeInTheDocument();
      expect(
        tableRows().map(
          (row) => within(row).getByText(/^(small|large)$/).textContent,
        ),
      ).toEqual(["small", "large"]);
    },
  );

  it("sorts legacy scan types by displayed label and time chronologically", async () => {
    getActivity.mockResolvedValue([
      activity("older", {
        scan_type: "fix" as Activity["scan_type"],
        created_at: "2026-10-15T10:00:00Z",
        branch_name: "older",
      }),
      activity("newer", {
        scan_type: "scan" as Activity["scan_type"],
        branch_name: "newer",
      }),
    ]);
    renderTab();
    await screen.findByRole("grid");
    expect(tableRows()[0]).toHaveTextContent("newer");
    fireEvent.click(screen.getByRole("columnheader", { name: "Time ↓" }));
    expect(tableRows()[0]).toHaveTextContent("older");
    fireEvent.click(screen.getByRole("columnheader", { name: "Type" }));
    expect(tableRows()[0]).toHaveTextContent("check");
    fireEvent.click(screen.getByRole("columnheader", { name: "Type ↑" }));
    expect(tableRows()[0]).toHaveTextContent("remediate");
    fireEvent.click(screen.getByRole("columnheader", { name: "Time" }));
    expect(tableRows()[0]).toHaveTextContent("newer");
  });

  it.each([
    ["2026-10-16T09:59:45Z", "just now"],
    ["2026-10-16T09:35:00Z", "25m ago"],
    ["2026-10-15T09:00:00Z", "25h ago"],
    ["2026-10-13T10:00:00Z", "3d ago"],
    ["2026-10-17T10:00:00Z", "2026-10-17T10:00:00Z"],
    ["invalid timestamp", "invalid timestamp"],
  ])("displays activity time %s as %s", async (created_at, label) => {
    getActivity.mockResolvedValue([activity("scan-1", { created_at })]);
    renderTab();
    expect(await screen.findByText(label)).toBeInTheDocument();
  });

  it.each(["Enter", " "])(
    "opens rows with keyboard %s while preserving unrelated query parameters",
    async (key) => {
      getActivity.mockResolvedValue([activity("scan-1")]);
      renderTab("/?keep=yes");
      await screen.findByRole("grid");
      fireEvent.keyDown(tableRows()[0], { key: "ArrowDown" });
      expect(getActivityDetail).not.toHaveBeenCalled();
      fireEvent.keyDown(tableRows()[0], { key });
      await screen.findByRole("button", { name: "Close activity detail" });
      expect(getActivityDetail).toHaveBeenCalledWith("scan-1");
      expect(screen.getByTestId("query")).toHaveTextContent(
        "?keep=yes&activity=scan-1",
      );
      fireEvent.click(
        screen.getByRole("button", { name: "Close activity detail" }),
      );
      await screen.findByRole("grid");
      expect(screen.getByTestId("query")).toHaveTextContent("?keep=yes");
    },
  );

  it("does not open detail when the published cell is clicked", async () => {
    getActivity.mockResolvedValue([
      activity("scan-1", { branch_name: "published-branch" }),
    ]);
    renderTab();
    fireEvent.click(await screen.findByText("published-branch"));
    expect(getActivityDetail).not.toHaveBeenCalled();
    fireEvent.click(within(tableRows()[0]).getByText("check"));
    await screen.findByRole("button", { name: "Close activity detail" });
    expect(getActivityDetail).toHaveBeenCalledWith("scan-1");
  });

  it("renders check and remediation metrics with dark theme chips", async () => {
    getActivity.mockResolvedValue([
      activity("scan-1"),
      activity("scan-2", {
        scan_type: "remediate",
        fixable: 7,
        remediated_count: 4,
      }),
    ]);
    renderTab("/", true);
    await screen.findByRole("grid");
    const [check, remediate] = tableRows();
    expect(
      within(check)
        .getAllByRole("cell")
        .slice(1, 5)
        .map((cell) => cell.textContent),
    ).toEqual(["1", "1", "—", "0"]);
    expect(
      within(remediate)
        .getAllByRole("cell")
        .slice(1, 5)
        .map((cell) => cell.textContent),
    ).toEqual(["1", "—", "4", "0"]);
    expect(within(check).getByText("check")).toHaveStyle({ color: "#8EC8F7" });
    expect(within(remediate).getByText("remediate")).toHaveStyle({
      color: "#BDE5B8",
    });
  });

  it("shows an empty activity list and does not auto-open for a rule with no scans", async () => {
    renderTab("/?rule=L001");
    expect(
      await screen.findByText(/No quality activity recorded yet/),
    ).toBeInTheDocument();
    expect(getActivityDetail).not.toHaveBeenCalled();
  });

  it.each([new Error("Activity service failed"), "Activity service failed"])(
    "shows list errors for Error and non-Error rejections",
    async (error) => {
      getActivity.mockRejectedValue(error);
      renderTab();
      expect(
        await screen.findByText("Activity service failed"),
      ).toBeInTheDocument();
      expect(screen.queryByRole("grid")).not.toBeInTheDocument();
    },
  );

  it.each([new Error("Detail service failed"), "Detail service failed"])(
    "closes failed detail and returns to the loaded list",
    async (error) => {
      getActivityDetail.mockRejectedValue(error);
      renderTab("/?activity=scan-1&keep=yes");
      expect(
        await screen.findByText("Detail service failed"),
      ).toBeInTheDocument();
      fireEvent.click(
        screen.getByRole("button", { name: "Close activity detail" }),
      );
      expect(
        await screen.findByText(/No quality activity recorded yet/),
      ).toBeInTheDocument();
      expect(screen.getByTestId("query")).toHaveTextContent("?keep=yes");
      expect(
        screen.queryByText("Detail service failed"),
      ).not.toBeInTheDocument();
    },
  );

  it("shows a missing activity and permits closing it", async () => {
    getActivityDetail.mockResolvedValue(null);
    renderTab("/?activity=missing");
    expect(await screen.findByText("Activity not found.")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Close activity detail" }),
    );
    expect(await screen.findByText("Quality activity (0)")).toBeInTheDocument();
  });

  it("ignores an obsolete successful detail response after switching activities", async () => {
    const pending = deferred<ActivityDetail>();
    getActivityDetail
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(detail("next", { branch_name: "current-branch" }));
    renderTab("/?activity=old");
    expect(await screen.findByRole("progressbar")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next activity" }));
    expect(await screen.findByText("current-branch")).toBeInTheDocument();
    await act(async () => {
      pending.resolve(detail("old", { branch_name: "obsolete-branch" }));
    });
    expect(screen.queryByText("obsolete-branch")).not.toBeInTheDocument();
    expect(screen.getByText("current-branch")).toBeInTheDocument();
  });

  it("ignores an obsolete detail failure after returning to the list", async () => {
    const pending = deferred<ActivityDetail>();
    getActivityDetail.mockReturnValue(pending.promise);
    renderTab("/?activity=old");
    fireEvent.click(screen.getByRole("button", { name: "Activity list" }));
    await screen.findByText("Quality activity (0)");
    await act(async () => {
      pending.reject(new Error("Obsolete failure"));
    });
    expect(screen.queryByText("Obsolete failure")).not.toBeInTheDocument();
    expect(screen.getByText("Quality activity (0)")).toBeInTheDocument();
  });

  it("opens the newest scan for a rule even when activities arrive out of order", async () => {
    getActivity.mockResolvedValue([
      activity("old", { created_at: "2026-10-10T10:00:00Z" }),
      activity("latest"),
    ]);
    renderTab("/?rule=L001&keep=yes");
    await screen.findByRole("button", { name: "Close activity detail" });
    expect(getActivityDetail).toHaveBeenCalledWith("latest");
    expect(screen.getByTestId("query")).toHaveTextContent(
      "?rule=L001&keep=yes&activity=latest",
    );
  });

  it("honors an explicit activity instead of auto-opening the latest for a rule", async () => {
    getActivity.mockResolvedValue([activity("latest")]);
    renderTab("/?activity=selected&rule=L001");
    await screen.findByRole("button", { name: "Close activity detail" });
    expect(getActivityDetail).toHaveBeenCalledTimes(1);
    expect(getActivityDetail).toHaveBeenCalledWith("selected");
  });

  it("shows unavailable service state without requesting activity", () => {
    resolveProject.mockReturnValue({
      adapter: null,
      projectId: null,
      error: null,
      unavailable: true,
    });
    renderTab();
    expect(screen.getByText("APME unavailable")).toBeInTheDocument();
    expect(getActivity).not.toHaveBeenCalled();
  });

  it("shows project resolution errors without requesting activity", async () => {
    resolveProject.mockReturnValue({
      adapter: null,
      projectId: null,
      error: new Error("Project access denied"),
      unavailable: false,
    });
    renderTab();
    expect(
      await screen.findByText("Project access denied"),
    ).toBeInTheDocument();
    expect(getActivity).not.toHaveBeenCalled();
  });

  it.each([
    { adapter: null, projectId: "project-1" },
    {
      adapter: {} as NonNullable<
        ReturnType<typeof useResolveApmeProject>["adapter"]
      >,
      projectId: null,
    },
  ])(
    "waits for both project and adapter before fetching activity: $projectId",
    async (state) => {
      resolveProject.mockReturnValue({
        ...state,
        error: null,
        unavailable: false,
      });
      renderTab();
      expect(await screen.findByRole("progressbar")).toBeInTheDocument();
      expect(getActivity).not.toHaveBeenCalled();
    },
  );

  it("shows progress while fetching the list and ignores the response after unmount", async () => {
    const pending = deferred<Activity[]>();
    getActivity.mockReturnValue(pending.promise);
    const { unmount } = renderTab();
    expect(await screen.findByRole("progressbar")).toBeInTheDocument();
    expect(getActivity).toHaveBeenCalledWith("project-1");
    unmount();
    await act(async () => {
      pending.resolve([activity("late")]);
    });
    expect(screen.queryByRole("grid")).not.toBeInTheDocument();
  });

  it("keeps a selected detail available when the list request fails", async () => {
    getActivity.mockRejectedValue(new Error("List unavailable"));
    getActivityDetail.mockResolvedValue(
      detail("scan-1", { branch_name: "detail-only-branch" }),
    );
    renderTab("/?activity=scan-1");
    expect(await screen.findByText("detail-only-branch")).toBeInTheDocument();
    expect(screen.queryByText("List unavailable")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /view pr/i }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Close activity detail" }),
    );
    expect(await screen.findByText("List unavailable")).toBeInTheDocument();
  });
});
