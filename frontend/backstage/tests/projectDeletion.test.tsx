import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { BUIProvider } from "@backstage/ui";
import { createDefaultApmeApiAdapter, type ProjectDetail } from "@apme/client";
import { ApmeProjectSettings } from "../plugins/backstage-apme/src/components/ApmeProjectSettings";
import { ProjectDetails } from "../plugins/backstage-apme/src/components/ProjectDetails";

const project = {
  id: "p1",
  name: "Automation",
  repo_url: "https://example.test/repo",
  branch: "main",
  active_operation: null,
} as ProjectDetail;
function setup(
  canDelete = false,
  denied = false,
  active = false,
  activeOnConfirm = false,
) {
  const onDeleted = vi.fn();
  let readCount = 0;
  const request = vi.fn(
    async (_input: RequestInfo | URL, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (method === "GET") {
        readCount += 1;
        const operationIsActive = active || (activeOnConfirm && readCount > 1);
        return new Response(
          JSON.stringify({
            ...project,
            active_operation: operationIsActive ? { status: "running" } : null,
          }),
        );
      }
      return denied
        ? new Response('{"detail":"Delete denied"}', { status: 403 })
        : new Response(null, { status: 204 });
    },
  );
  const fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/settings/git-requirements")) {
      return new Response(JSON.stringify({ enabled: false }));
    }
    return request(input, init);
  };
  render(
    <BUIProvider>
      <ApmeProjectSettings
        project={
          active
            ? {
                ...project,
                active_operation: {
                  status: "running",
                } as ProjectDetail["active_operation"],
              }
            : project
        }
        adapter={createDefaultApmeApiAdapter({ apiBase: "/host", fetch })}
        canManage
        canDelete={canDelete}
        onDeleted={onDeleted}
      />
    </BUIProvider>,
  );
  return { fetch: request, onDeleted };
}
it("requires an independent host deletion permission", () => {
  const { fetch } = setup();
  expect(screen.queryByRole("button", { name: "Delete project" })).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});
it("requires name confirmation, then deletes the canonical project and tells its host", async () => {
  const { fetch, onDeleted } = setup(true);
  fireEvent.click(screen.getByRole("button", { name: "Delete project" }));
  const confirm = await screen.findByRole("button", {
    name: "Delete permanently",
  });
  expect((confirm as HTMLButtonElement).disabled).toBe(true);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByLabelText("Type Automation to confirm"), {
    target: { value: "Automation" },
  });
  fireEvent.click(confirm);
  await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
  expect(fetch).toHaveBeenCalledWith(
    "/host/projects/p1",
    expect.objectContaining({ method: "DELETE" }),
  );
});
it("keeps denied deletion visible without navigating away", async () => {
  const { onDeleted } = setup(true, true);
  fireEvent.click(screen.getByRole("button", { name: "Delete project" }));
  fireEvent.change(await screen.findByLabelText("Type Automation to confirm"), {
    target: { value: "Automation" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
  await screen.findByText("Delete denied");
  expect(onDeleted).not.toHaveBeenCalled();
});
it("prevents deleting a project while an operation is active", () => {
  const { fetch } = setup(true, false, true);
  fireEvent.click(screen.getByRole("button", { name: "Delete project" }));
  expect(
    screen.getByRole("button", { name: "Delete permanently" }),
  ).toBeTruthy();
  return screen.findByText(/Project operation is active/).then(() => {
    expect(
      (
        screen.getByRole("button", {
          name: "Delete permanently",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe("/host/projects/p1");
  });
});

it("rechecks operation state immediately before deleting", async () => {
  const { fetch, onDeleted } = setup(true, false, false, true);
  fireEvent.click(screen.getByRole("button", { name: "Delete project" }));
  fireEvent.change(await screen.findByLabelText("Type Automation to confirm"), {
    target: { value: "Automation" },
  });
  await screen.findByRole("button", { name: "Delete permanently" });
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Delete permanently",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );

  fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
  await screen.findByText(/An operation became active/);
  expect(onDeleted).not.toHaveBeenCalled();
  expect(
    fetch.mock.calls.filter(([, init]) => init?.method === "DELETE"),
  ).toHaveLength(0);
});
it("lets a user retry a failed project load", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response('{"detail":"Temporarily unavailable"}', { status: 503 }),
    )
    .mockResolvedValueOnce(new Response(JSON.stringify(project)));
  render(
    <BUIProvider>
      <ProjectDetails
        projectId="p1"
        adapter={createDefaultApmeApiAdapter({ fetch })}
      />
    </BUIProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
  await screen.findByRole("heading", { name: "Automation" });
});

it("keeps the loaded project and focused control when a refresh fails", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(project)))
    .mockResolvedValueOnce(
      new Response('{"detail":"Temporarily unavailable"}', { status: 503 }),
    );
  render(
    <BUIProvider>
      <ProjectDetails
        projectId="p1"
        adapter={createDefaultApmeApiAdapter({ fetch })}
      >
        {(_loadedProject, reload) => (
          <button type="button" onClick={reload}>
            Refresh project
          </button>
        )}
      </ProjectDetails>
    </BUIProvider>,
  );

  const refreshButton = await screen.findByRole("button", {
    name: "Refresh project",
  });
  refreshButton.focus();
  fireEvent.click(refreshButton);

  expect(await screen.findByText("Temporarily unavailable")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Automation" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Refresh project" })).toBeTruthy();
  expect(document.activeElement).toBe(refreshButton);
});

it.each([
  {
    breakdown: {
      medium: 100,
      info: 33,
      high: 8,
      low: 115,
      critical: 0,
      error: 0,
    },
    expected: ["8 high", "100 medium", "115 low", "33 info"],
  },
  {
    breakdown: { medium: 0, info: 3, high: 8, low: 0, critical: 2, error: 1 },
    expected: ["2 critical", "1 error", "8 high", "3 info"],
  },
])(
  "orders nonzero project finding counts by severity ($expected)",
  async ({ breakdown, expected }) => {
    const fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ ...project, severity_breakdown: breakdown }),
        ),
    );
    render(
      <BUIProvider>
        <ProjectDetails
          projectId="p1"
          adapter={createDefaultApmeApiAdapter({ fetch })}
        />
      </BUIProvider>,
    );
    const summary = await screen.findByLabelText("Finding severity summary");
    expect(
      Array.from(summary.children, (element) => element.textContent),
    ).toEqual(expected);
    expect(within(summary).queryByText(/^0 /)).toBeNull();
  },
);

it("omits the severity summary when all counts are zero", async () => {
  const fetch = vi.fn(
    async () =>
      new Response(
        JSON.stringify({
          ...project,
          severity_breakdown: { high: 0, medium: 0, low: 0, info: 0 },
        }),
      ),
  );
  render(
    <BUIProvider>
      <ProjectDetails
        projectId="p1"
        adapter={createDefaultApmeApiAdapter({ fetch })}
      />
    </BUIProvider>,
  );
  await screen.findByRole("heading", { name: "Automation" });
  expect(screen.queryByLabelText("Finding severity summary")).toBeNull();
});
