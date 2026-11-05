import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BUIProvider } from "@backstage/ui";
import { createDefaultApmeApiAdapter } from "@apme/client";
import { ApmeProjectWorkflow } from "../plugins/backstage-apme/src/components/ApmeProjectWorkflow";

// Exercise the real controller/transport and page lifecycle; keep the specialist
// panel small so this regression isolates attachment rather than proposal UI.
vi.mock("@apme/ui-workflow", async (original) => ({
  ...(await original<object>()),
  ProjectWorkflowPanel: ({
    workflow,
  }: {
    workflow: { dismiss: () => void; opState: unknown };
  }) =>
    workflow.opState ? (
      <div>
        Create branch<button onClick={workflow.dismiss}>Dismiss</button>
      </div>
    ) : (
      <p>Connecting workflow…</p>
    ),
}));

function fixture(status = "completed", fixed = 1) {
  let op = {
    operation_id: "op-one",
    scan_id: "run-one",
    status,
    project_id: "p1",
    result: { remediated_count: fixed, patches: [] },
    findings: [],
    proposals: [],
    progress: [],
  };
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") {
      op = { ...op, operation_id: "op-two", scan_id: "run-two" };
      return new Response(JSON.stringify({ operation_id: "op-two" }));
    }
    return new Response(JSON.stringify(op));
  });
  const adapter = createDefaultApmeApiAdapter({ apiBase: "/host", fetch });
  const mount = () =>
    render(
      <BUIProvider>
        <ApmeProjectWorkflow projectId="p1" adapter={adapter} />
      </BUIProvider>,
    );
  return { fetch, mount };
}

it("does not reopen Create branch after dismissing and leaving/reopening the project", async () => {
  const { mount, fetch } = fixture();
  const first = mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "Resume workflow" }),
  );
  await screen.findByText("Create branch");
  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  await screen.findByRole("button", { name: "Check repository" });
  first.unmount();
  mount();
  await screen.findByRole("button", { name: "Resume workflow" });
  expect(screen.queryByText("Create branch")).toBeNull();
  expect(
    fetch.mock.calls.every(
      ([, init]) => !init?.method || init.method === "GET",
    ),
  ).toBe(true);
});

it("clears an attached workflow when the host adapter changes", async () => {
  const operation = {
    operation_id: "op-one",
    scan_id: "run-one",
    status: "completed",
    project_id: "p1",
    result: { remediated_count: 1, patches: [] },
    findings: [],
    proposals: [],
    progress: [],
  };
  const oldFetch = vi.fn(async () => new Response(JSON.stringify(operation)));
  const newFetch = vi.fn(
    async (_input: RequestInfo | URL) => new Response(null, { status: 404 }),
  );
  const view = render(
    <BUIProvider>
      <ApmeProjectWorkflow
        projectId="p1"
        adapter={createDefaultApmeApiAdapter({
          apiBase: "/host",
          fetch: oldFetch,
        })}
      />
    </BUIProvider>,
  );

  fireEvent.click(
    await screen.findByRole("button", { name: "Resume workflow" }),
  );
  await screen.findByText("Create branch");

  view.rerender(
    <BUIProvider>
      <ApmeProjectWorkflow
        projectId="p1"
        adapter={createDefaultApmeApiAdapter({
          apiBase: "/host",
          fetch: newFetch,
        })}
      />
    </BUIProvider>,
  );

  await screen.findByRole("button", { name: "Check repository" });
  expect(screen.queryByText("Create branch")).toBeNull();
  expect(
    newFetch.mock.calls.some(([input]) =>
      String(input).endsWith("/projects/p1/operation"),
    ),
  ).toBe(true);
});

it("offers explicit resume for a live operation without attaching on project entry", async () => {
  const { mount } = fixture("assessed", 0);
  mount();
  await screen.findByRole("button", { name: "Resume workflow" });
  expect(screen.queryByText("Create branch")).toBeNull();
  expect(screen.getByRole("button", { name: "Check repository" })).toBeTruthy();
});
it("does not offer a finished check with no applied changes as a workflow to resume", async () => {
  const { mount, fetch } = fixture("completed", 0);
  mount();
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  await waitFor(() =>
    expect(screen.queryByText(/Loading available workflow/)).toBeNull(),
  );
  expect(screen.queryByRole("button", { name: "Resume workflow" })).toBeNull();
  expect(screen.queryByText("Create branch")).toBeNull();
});
it("starting a new check still opens its workflow", async () => {
  const { mount, fetch } = fixture();
  mount();
  await screen.findByRole("button", { name: "Resume workflow" });
  fireEvent.click(screen.getByRole("button", { name: "Check repository" }));
  await screen.findByText("Create branch");
  expect(fetch).toHaveBeenCalledWith(
    "/host/projects/p1/operation",
    expect.objectContaining({ method: "POST" }),
  );
});
it("keeps failed discovery visible and allows retry without starting a check", async () => {
  let denied = true;
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/ai/models")) return new Response("[]");
    return denied
      ? new Response("Denied", { status: 403 })
      : new Response(null, { status: 404 });
  });
  render(
    <BUIProvider>
      <ApmeProjectWorkflow
        projectId="p1"
        adapter={createDefaultApmeApiAdapter({ fetch })}
      />
    </BUIProvider>,
  );
  const retry = await screen.findByRole("button", { name: "Retry" });
  denied = false;
  fireEvent.click(retry);
  await waitFor(() =>
    expect(screen.queryByText("Could not load available workflow")).toBeNull(),
  );
  expect(
    fetch.mock.calls.filter(([input]) => String(input).endsWith("/operation")),
  ).toHaveLength(2);
});
