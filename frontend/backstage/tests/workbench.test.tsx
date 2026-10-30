import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { BUIProvider } from "@backstage/ui";
import { createDefaultApmeApiAdapter } from "@apme/client";
import { ApmeWorkbench } from "../plugins/backstage-apme/src/components/ApmePage";

const project = {
  id: "project-one",
  name: "Automation",
  repo_url: "https://github.com/team/automation",
  branch: "stable",
  total_violations: 3,
  scan_count: 1,
  active_operation: null,
};

function open(fetch: typeof globalThis.fetch) {
  render(
    <MemoryRouter>
      <BUIProvider>
        <ApmeWorkbench
          adapter={createDefaultApmeApiAdapter({
            apiBase: "/host/apme",
            fetch,
          })}
        />
      </BUIProvider>
    </MemoryRouter>,
  );
}

it("selects a repository and starts the existing Gateway assess-pause workflow", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/operation") && init?.method === "POST")
      return new Response('{"operation_id":"op"}');
    if (url.includes("/operation")) return new Response("", { status: 404 });
    if (url.endsWith("/ai/models")) return new Response("[]");
    return new Response(
      JSON.stringify(
        url.includes("/projects?") ? { total: 1, items: [project] } : project,
      ),
    );
  });
  open(fetch);
  fireEvent.click(await screen.findByRole("button", { name: "Automation" }));
  expect(
    await screen.findByRole("heading", { name: "Automation" }),
  ).toBeTruthy();
  fireEvent.click(
    await screen.findByRole("button", { name: "Check repository" }),
  );
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/host/apme/projects/project-one/operation",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          action: "check",
          options: { enable_ai: false, assess_pause: true, interactive: true },
        }),
      }),
    ),
  );
});

it("registers a repository with its branch, then opens the returned project identity", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/projects") && init?.method === "POST")
      return new Response(JSON.stringify(project));
    if (String(input).includes("/operation"))
      return new Response("", { status: 404 });
    if (String(input).endsWith("/ai/models")) return new Response("[]");
    return new Response(
      JSON.stringify(
        String(input).includes("/projects?")
          ? { total: 0, items: [] }
          : project,
      ),
    );
  });
  open(fetch);
  fireEvent.click(screen.getByRole("button", { name: "Add repository" }));
  fireEvent.change(await screen.findByLabelText(/Project name/), {
    target: { value: "Automation" },
  });
  fireEvent.change(screen.getByLabelText(/Repository URL/), {
    target: { value: project.repo_url },
  });
  fireEvent.change(screen.getByLabelText(/^Branch/), {
    target: { value: "stable" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add repository" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/host/apme/projects",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          name: "Automation",
          repo_url: project.repo_url,
          branch: "stable",
        }),
      }),
    ),
  );
  expect(
    await screen.findByRole("heading", { name: "Automation" }),
  ).toBeTruthy();
});

it("shows an authorization failure without presenting it as an empty repository list", async () => {
  const fetch = vi.fn(
    async () => new Response("Permission denied", { status: 403 }),
  );
  open(fetch);
  await screen.findByText("Could not load repositories");
  expect(screen.getByText(/Permission denied/)).toBeTruthy();
  expect(screen.queryByRole("grid")).toBeNull();
});
