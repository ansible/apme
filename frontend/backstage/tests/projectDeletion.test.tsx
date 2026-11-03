import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
function setup(canDelete = false, denied = false, active = false) {
  const onDeleted = vi.fn();
  const fetch = vi.fn(async () =>
    denied
      ? new Response('{"detail":"Delete denied"}', { status: 403 })
      : new Response(null, { status: 204 }),
  );
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
  return { fetch, onDeleted };
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
  expect(fetch).not.toHaveBeenCalled();
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
  expect(
    (
      screen.getByRole("button", {
        name: "Delete project",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(fetch).not.toHaveBeenCalled();
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
