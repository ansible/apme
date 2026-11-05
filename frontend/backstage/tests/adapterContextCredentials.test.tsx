import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BUIProvider } from "@backstage/ui";
import { createDefaultApmeApiAdapter } from "@apme/client";
import type { ProjectDetail } from "@apme/client";
import { ApmeGalaxySettings } from "../plugins/backstage-apme/src/components/ApmeGalaxySettings";
import { ApmeProjectSettings } from "../plugins/backstage-apme/src/components/ApmeProjectSettings";
import { CreateProjectDialog } from "../plugins/backstage-apme/src/components/CreateProjectDialog";

const project = {
  id: "p1",
  name: "Automation",
  repo_url: "https://git.test/repo",
  branch: "main",
  active_operation: null,
  has_scm_token: true,
} as ProjectDetail;

function json(value: unknown) {
  return new Response(JSON.stringify(value));
}

it("clears a repository creation token when the host adapter changes", async () => {
  const oldFetch = vi.fn();
  const newFetch = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      json({ ...project, id: "p2", has_scm_token: false }),
  );
  const onCreated = vi.fn();
  const onClose = vi.fn();
  const view = render(
    <BUIProvider>
      <CreateProjectDialog
        adapter={createDefaultApmeApiAdapter({
          apiBase: "/old",
          fetch: oldFetch,
        })}
        onCreated={onCreated}
        onClose={onClose}
      />
    </BUIProvider>,
  );
  fireEvent.change(screen.getByRole("textbox", { name: /Repository URL/ }), {
    target: { value: "https://git.test/new" },
  });
  const token = screen.getByLabelText(/SCM token \(optional\)/);
  fireEvent.change(token, { target: { value: "old-host-secret" } });

  view.rerender(
    <BUIProvider>
      <CreateProjectDialog
        adapter={createDefaultApmeApiAdapter({
          apiBase: "/new",
          fetch: newFetch,
        })}
        onCreated={onCreated}
        onClose={onClose}
      />
    </BUIProvider>,
  );

  const newToken = screen.getByLabelText(/SCM token \(optional\)/);
  expect((newToken as HTMLInputElement).value).toBe("");
  fireEvent.change(screen.getByRole("textbox", { name: /Repository URL/ }), {
    target: { value: "https://git.test/new" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add repository" }));

  await waitFor(() => expect(newFetch).toHaveBeenCalledOnce());
  expect(
    JSON.parse(newFetch.mock.calls[0][1]?.body as string),
  ).not.toHaveProperty("scm_token");
  expect(oldFetch).not.toHaveBeenCalled();
});

it("clears project settings credentials when the host adapter changes", async () => {
  const oldFetch = vi.fn();
  const newFetch = vi.fn(
    async (_input: RequestInfo | URL, _init?: RequestInit) =>
      json({ ...project, branch: "next" }),
  );
  const view = render(
    <BUIProvider>
      <ApmeProjectSettings
        project={project}
        adapter={createDefaultApmeApiAdapter({
          apiBase: "/old",
          fetch: oldFetch,
        })}
        canManage
      />
    </BUIProvider>,
  );
  fireEvent.change(screen.getByLabelText(/SCM token \(configured\)/), {
    target: { value: "old-host-secret" },
  });

  view.rerender(
    <BUIProvider>
      <ApmeProjectSettings
        project={project}
        adapter={createDefaultApmeApiAdapter({
          apiBase: "/new",
          fetch: newFetch,
        })}
        canManage
      />
    </BUIProvider>,
  );

  const newToken = screen.getByLabelText(/SCM token \(configured\)/);
  expect((newToken as HTMLInputElement).value).toBe("");
  fireEvent.change(screen.getByRole("textbox", { name: "Branch" }), {
    target: { value: "next" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save project" }));

  await waitFor(() => expect(newFetch).toHaveBeenCalledOnce());
  expect(
    JSON.parse(newFetch.mock.calls[0][1]?.body as string),
  ).not.toHaveProperty("scm_token");
  expect(oldFetch).not.toHaveBeenCalled();
});

it("does not retain a Galaxy token when its adapter changes", async () => {
  const server = {
    id: 1,
    name: "Galaxy",
    url: "https://galaxy.test",
    auth_url: "",
    validate_certs: true,
    has_token: true,
  };
  const oldFetch = vi.fn(async () => json([server]));
  const newFetch = vi.fn(async () => json([server]));
  const view = render(
    <BUIProvider>
      <ApmeGalaxySettings
        adapter={createDefaultApmeApiAdapter({
          apiBase: "/old",
          fetch: oldFetch,
        })}
        canView
        canManage
      />
    </BUIProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Edit Galaxy" }));
  fireEvent.change(
    screen.getByLabelText(/Token \(leave blank to keep current\)/),
    {
      target: { value: "old-host-secret" },
    },
  );

  view.rerender(
    <BUIProvider>
      <ApmeGalaxySettings
        adapter={createDefaultApmeApiAdapter({
          apiBase: "/new",
          fetch: newFetch,
        })}
        canView
        canManage
      />
    </BUIProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Edit Galaxy" }));

  expect(
    (
      screen.getByLabelText(
        /Token \(leave blank to keep current\)/,
      ) as HTMLInputElement
    ).value,
  ).toBe("");
  expect(oldFetch).toHaveBeenCalledOnce();
  expect(newFetch).toHaveBeenCalledOnce();
});
