import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { BUIProvider } from "@backstage/ui";
import { createDefaultApmeApiAdapter } from "@apme/client";
import type { ProjectDetail } from "@apme/client";
import { ApmeProjectTabs } from "../plugins/backstage-apme/src/components/ApmeProjectTabs";

it("composes host-declared views and falls back from an unavailable deep link without loading it", async () => {
  const fetch = vi.fn(
    async (_input: RequestInfo | URL) =>
      new Response(JSON.stringify({ items: [], total: 0 })),
  );
  render(
    <MemoryRouter>
      <BUIProvider>
        <ApmeProjectTabs
          projectId="repo"
          adapter={createDefaultApmeApiAdapter({ apiBase: "/host", fetch })}
          projectViews={["activity"]}
          tab="graph"
          onTabChange={vi.fn()}
          onOpenActivity={vi.fn()}
          onCloseActivity={vi.fn()}
        />
      </BUIProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("tab", { name: "History" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Graph" })).toBeNull();
  await screen.findByText(
    "No history yet. Check this repository to record a run.",
  );
  expect(
    fetch.mock.calls.every(([url]) => !String(url).includes("graph")),
  ).toBe(true);
});

it("does not load any project view when the host advertises none", async () => {
  const fetch = vi.fn();
  render(
    <MemoryRouter>
      <BUIProvider>
        <ApmeProjectTabs
          projectId="repo"
          adapter={createDefaultApmeApiAdapter({ apiBase: "/host", fetch })}
          projectViews={[]}
          tab="workflow"
          onTabChange={vi.fn()}
          onOpenActivity={vi.fn()}
          onCloseActivity={vi.fn()}
        />
      </BUIProvider>
    </MemoryRouter>,
  );
  expect(
    screen.getByText("No project views are enabled by this host."),
  ).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
});

it("exposes project settings for a delete-only host without exposing edit fields", async () => {
  const project = {
    id: "repo",
    name: "Automation",
    repo_url: "https://example.test/repo",
    branch: "main",
    active_operation: null,
  } as ProjectDetail;
  render(
    <MemoryRouter>
      <BUIProvider>
        <ApmeProjectTabs
          projectId="repo"
          project={project}
          adapter={createDefaultApmeApiAdapter({
            apiBase: "/host",
            fetch: vi.fn(),
          })}
          projectViews={[]}
          tab="settings"
          canDeleteProject
          onTabChange={vi.fn()}
          onOpenActivity={vi.fn()}
          onCloseActivity={vi.fn()}
        />
      </BUIProvider>
    </MemoryRouter>,
  );

  expect(await screen.findByRole("tab", { name: "Settings" })).toBeTruthy();
  expect(
    await screen.findByRole("heading", { name: "Delete project" }),
  ).toBeTruthy();
  expect(screen.queryByRole("form", { name: "Project settings" })).toBeNull();
  expect(screen.queryByLabelText("Project name")).toBeNull();
});
