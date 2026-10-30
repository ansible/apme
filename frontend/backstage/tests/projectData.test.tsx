import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { BUIProvider } from "@backstage/ui";
import {
  createDefaultApmeApiAdapter,
  type ActivityDetail,
  type ProjectDependencies,
} from "@apme/client";
import { ApmeWorkbench } from "../plugins/backstage-apme/src/components/ApmePage";
import { ApmeActivityDetails } from "../plugins/backstage-apme/src/components/ApmeActivityDetails";
import { ApmeProjectDependencies } from "../plugins/backstage-apme/src/components/ApmeProjectDependencies";
import { ApmeProjectFindings } from "../plugins/backstage-apme/src/components/ApmeProjectFindings";

const project = {
  id: "p1",
  name: "Automation",
  repo_url: "https://git.test/repo",
  branch: "main",
  total_violations: 1,
  scan_count: 1,
  active_operation: null,
};
const finding = {
  id: 1,
  rule_id: "L050",
  level: "high",
  message: "Use FQCN",
  file: "play.yml",
  line: 2,
  path: "play.yml/plays[0]/tasks[0]",
  node_type: "task",
  remediation_class: 1,
  remediation_resolution: 0,
  scope: 1,
  original_yaml: "- debug: msg=hi",
  review_status: "accepted",
};
function run(id = "run-one"): ActivityDetail {
  return {
    scan_id: id,
    project_id: "p1",
    session_id: "session",
    project_path: "/repo",
    source: "gateway",
    created_at: "2026-10-30T00:00:00Z",
    scan_type: "check",
    total_violations: 1,
    fixable: 1,
    ai_candidate: 0,
    ai_proposed: 0,
    ai_declined: 0,
    ai_accepted: 0,
    manual_review: 0,
    remediated_count: 0,
    pr_url: null,
    diagnostics_json: null,
    violations: [finding],
    proposals: [],
    patches: [],
    logs: [],
  };
}
const deps: ProjectDependencies = {
  ansible_core_version: "2.19",
  collections: [
    {
      fqcn: "ansible.posix",
      version: "2.1",
      source: "galaxy",
      license: "GPL-3.0",
      supplier: "Ansible",
    },
  ],
  python_packages: [
    {
      name: "requests",
      version: "2.32",
      license: "Apache-2.0",
      supplier: "PSF",
    },
  ],
  requirements_files: ["collections/requirements.yml"],
  dependency_tree: "ansible-core\n  requests",
};
function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status });
}
function adapter(fetch: typeof globalThis.fetch) {
  return createDefaultApmeApiAdapter({ apiBase: "/host/apme", fetch });
}
function Location() {
  return <output data-testid="location">{useLocation().search}</output>;
}
function workbench(fetch: typeof globalThis.fetch, entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <BUIProvider>
        <ApmeWorkbench adapter={adapter(fetch)} />
        <Location />
      </BUIProvider>
    </MemoryRouter>,
  );
}

it("deep-links to history without loading the repository list or starting a workflow", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/host/apme/projects/p1") return json(project);
    if (path === "/host/apme/activity/run-one") return json(run());
    if (path.includes("/activity?"))
      return json({ items: [run()], total: 1, offset: 0, limit: 25 });
    throw new Error(`Unexpected request: ${path}`);
  });
  workbench(fetch, "/apme?project=p1&tab=history&catalogTab=quality");
  fireEvent.click(
    await screen.findByRole("button", { name: "Check · run-one" }),
  );
  expect(screen.getByTestId("location").textContent).toContain("scan=run-one");
  expect(screen.getByTestId("location").textContent).toContain(
    "catalogTab=quality",
  );
  expect(
    await screen.findByRole("heading", { name: "Run run-one" }),
  ).toBeTruthy();
  await screen.findAllByRole("button", { name: "L050" });
  expect(screen.queryByRole("button", { name: /Remediate/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close activity" }));
  await screen.findByRole("grid", { name: "Project history" });
  expect(screen.getByTestId("location").textContent).not.toContain("scan=");
  expect(
    fetch.mock.calls.every(
      ([url]) =>
        !String(url).includes("/operation") &&
        !String(url).includes("/projects?"),
    ),
  ).toBe(true);
});

it("paginates history and keeps the project identity when switching to dependencies", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname === "/host/apme/projects/p1") return json(project);
    if (url.pathname.endsWith("/activity")) {
      const offset = Number(url.searchParams.get("offset"));
      return json({
        items: [run(offset ? "older-run" : "latest-run")],
        total: 26,
        limit: 25,
        offset,
      });
    }
    if (url.pathname.endsWith("/dependencies")) return json(deps);
    throw new Error(`Unexpected request: ${url.pathname}`);
  });
  workbench(fetch, "/apme?project=p1&tab=history");
  await screen.findByRole("button", { name: "Check · latest-run" });
  fireEvent.click(
    await screen.findByRole("button", { name: "Next history page" }),
  );
  await screen.findByRole("button", { name: "Check · older-run" });
  expect(
    fetch.mock.calls.some(([url]) => String(url).includes("offset=25")),
  ).toBe(true);
  fireEvent.click(screen.getByRole("tab", { name: "Dependencies" }));
  await screen.findByRole("grid", { name: "Project collections" });
  expect(screen.getByText("ansible.posix")).toBeTruthy();
  expect(screen.getByText("PSF")).toBeTruthy();
  expect(screen.getByTestId("location").textContent).toContain("project=p1");
  expect(screen.getByTestId("location").textContent).toContain(
    "tab=dependencies",
  );
  expect(screen.queryByRole("grid", { name: "Project history" })).toBeNull();
});

it("rejects a deep-linked run from another project before rendering its findings", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) =>
    String(input).endsWith("/projects/p1")
      ? json(project)
      : json({
          ...run(),
          project_id: "private-project",
          violations: [{ ...finding, message: "Private finding" }],
        }),
  );
  workbench(fetch, "/apme?project=p1&tab=history&scan=run%2Fone");
  await screen.findByText(
    "This activity does not belong to the selected project.",
  );
  expect(screen.queryByText("Private finding")).toBeNull();
  expect(
    fetch.mock.calls.some(
      ([url]) => String(url) === "/host/apme/activity/run%2Fone",
    ),
  ).toBe(true);
});

it("uses the Gateway-resolved canonical project ID when a deep link names the project", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/host/apme/projects/Automation") return json(project);
    if (path === "/host/apme/activity/run-one") return json(run());
    throw new Error(`Unexpected request: ${path}`);
  });
  workbench(fetch, "/apme?project=Automation&tab=history&scan=run-one");
  await screen.findAllByRole("button", { name: "L050" });
  expect(
    screen.queryByText(
      "This activity does not belong to the selected project.",
    ),
  ).toBeNull();
  expect(
    fetch.mock.calls.every(([url]) => !String(url).includes("/operation")),
  ).toBe(true);
});

it("keeps a denied history response visible and retries only through the same host", async () => {
  let allowed = false;
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/projects/p1")) return json(project);
    return allowed
      ? json({ items: [], total: 0, limit: 25, offset: 0 })
      : json({ detail: "History access denied" }, 403);
  });
  workbench(fetch, "/apme?project=p1&tab=history");
  await screen.findByText("History access denied");
  expect(screen.queryByRole("grid", { name: "Project history" })).toBeNull();
  allowed = true;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText(
    "No history yet. Check this repository to record a run.",
  );
  expect(
    fetch.mock.calls.every(([url]) => String(url).startsWith("/host/apme/")),
  ).toBe(true);
});

it("does not show a late activity response after the selected activity changes", async () => {
  let finishOld!: (response: Response) => void;
  const fetch = vi.fn(async (input: RequestInfo | URL) =>
    String(input).endsWith("/old")
      ? new Promise<Response>((resolve) => {
          finishOld = resolve;
        })
      : json({ ...run("new"), branch_name: "current-branch" }),
  );
  const sharedAdapter = adapter(fetch);
  const view = render(
    <MemoryRouter>
      <BUIProvider>
        <ApmeActivityDetails
          projectId="p1"
          activityId="old"
          adapter={sharedAdapter}
        />
      </BUIProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(finishOld).toBeDefined());
  view.rerender(
    <MemoryRouter>
      <BUIProvider>
        <ApmeActivityDetails
          projectId="p1"
          activityId="new"
          adapter={sharedAdapter}
        />
      </BUIProvider>
    </MemoryRouter>,
  );
  await screen.findByText("Branch: current-branch");
  await act(async () =>
    finishOld(json({ ...run("old"), branch_name: "stale-branch" })),
  );
  expect(screen.queryByText("Branch: stale-branch")).toBeNull();
});

it("pages dependency tables locally and searches without repeating the Gateway request", async () => {
  const fetch = vi.fn(async () =>
    json({
      ...deps,
      collections: Array.from({ length: 30 }, (_, index) => ({
        fqcn: `test.collection${index}`,
        version: "1",
        source: "galaxy",
      })),
    }),
  );
  render(
    <MemoryRouter>
      <BUIProvider>
        <ApmeProjectDependencies projectId="p1" adapter={adapter(fetch)} />
      </BUIProvider>
    </MemoryRouter>,
  );
  const table = await screen.findByRole("grid", {
    name: "Project collections",
  });
  expect(within(table).queryByText("test.collection29")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Next collections page" }),
  );
  expect(within(table).getByText("test.collection29")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Search dependencies"), {
    target: { value: "collection2" },
  });
  expect(within(table).getByText("test.collection2")).toBeTruthy();
  expect(within(table).queryByText("test.collection1")).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("keeps acknowledged findings opt-in and requests a sentinel row for finding pagination", async () => {
  const fetch = vi.fn(async () => json([{ ...finding, suppressed: true }]));
  render(
    <MemoryRouter>
      <BUIProvider>
        <ApmeProjectFindings
          projectId="p1"
          ruleId="L050&evil=1"
          adapter={adapter(fetch)}
        />
      </BUIProvider>
    </MemoryRouter>,
  );
  await screen.findByText("All findings on this page are acknowledged.");
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Show acknowledged findings" }),
  );
  expect(
    screen.getAllByRole("button", { name: "L050" }).length,
  ).toBeGreaterThan(0);
  expect(fetch).toHaveBeenCalledWith(
    "/host/apme/projects/p1/violations?limit=51&offset=0&rule_id=L050%26evil%3D1",
    expect.anything(),
  );
});

it("opens stored change previews on demand and rejects unsafe PR link schemes", async () => {
  const fetch = vi.fn(async () =>
    json({
      ...run(),
      pr_url: "javascript:alert(1)",
      violations: [],
      patches: [
        { id: 1, file: "play.yml", diff: "@@ -1 +1 @@\n-old-line\n+new-line" },
      ],
      logs: [
        {
          id: 1,
          phase: "check",
          message: "Scan complete",
          progress: 100,
          level: 1,
        },
      ],
    }),
  );
  render(
    <MemoryRouter>
      <BUIProvider>
        <ApmeActivityDetails
          projectId="p1"
          activityId="run-one"
          adapter={adapter(fetch)}
        />
      </BUIProvider>
    </MemoryRouter>,
  );
  const changes = await screen.findByRole("button", { name: "Changes (1)" });
  expect(screen.queryByRole("link", { name: "View pull request" })).toBeNull();
  expect(
    screen.queryByRole("region", { name: "Changes to play.yml" }),
  ).toBeNull();
  fireEvent.click(changes);
  await screen.findByRole("region", { name: "Changes to play.yml" });
  fireEvent.click(screen.getByRole("button", { name: "Run log (1)" }));
  expect(await screen.findByText("[check] Scan complete")).toBeTruthy();
});
