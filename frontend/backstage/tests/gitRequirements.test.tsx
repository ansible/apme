import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BUIProvider } from "@backstage/ui";
import { createDefaultApmeApiAdapter, type ProjectDetail } from "@apme/client";
import { ApmeGitRequirementsSettings } from "../plugins/backstage-apme/src/components/ApmeGitRequirementsSettings";
import { ApmeProjectSettings } from "../plugins/backstage-apme/src/components/ApmeProjectSettings";

it("persists the global Git requirements policy", async () => {
  const fetch = vi.fn(
    async (_input: RequestInfo | URL, init?: RequestInit) =>
      new Response((init?.body as string) || '{"enabled":false}'),
  );
  const adapter = createDefaultApmeApiAdapter({ fetch, apiBase: "/host" });
  render(
    <BUIProvider>
      <ApmeGitRequirementsSettings adapter={adapter} canView canManage />
    </BUIProvider>,
  );
  const toggle = await screen.findByRole("checkbox", {
    name: "Allow Git requirements for all projects",
  });
  await waitFor(() =>
    expect((toggle as HTMLInputElement).disabled).toBe(false),
  );
  fireEvent.click(toggle);
  await waitFor(() =>
    expect(fetch.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
      true,
    ),
  );
  const write = fetch.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(write[1]!.body as string)).toEqual({ enabled: true });
});

it.each([false, true])(
  "shows requirements credentials only when global policy is %s",
  async (enabled) => {
    const project = {
      id: "p1",
      name: "Project",
      repo_url: "https://git.test/project",
      branch: "main",
      has_scm_token: true,
      has_requirements_token: true,
    } as ProjectDetail;
    const fetch = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) =>
        new Response(
          JSON.stringify(
            String(input).includes("git-requirements")
              ? { enabled }
              : {
                  ...project,
                  has_requirements_token: Boolean(
                    JSON.parse(init?.body as string).requirements_token,
                  ),
                },
          ),
        ),
    );
    const adapter = createDefaultApmeApiAdapter({ fetch, apiBase: "/host" });
    render(
      <BUIProvider>
        <ApmeProjectSettings project={project} adapter={adapter} canManage />
      </BUIProvider>,
    );
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    if (enabled) {
      const field = await screen.findByLabelText(
        "Git requirements credential (configured)",
      );
      fireEvent.change(field, { target: { value: "requirements-secret" } });
      fireEvent.click(screen.getByRole("button", { name: "Save project" }));
      await waitFor(() =>
        expect(
          fetch.mock.calls.some(([, init]) => init?.method === "PATCH"),
        ).toBe(true),
      );
      const write = fetch.mock.calls.find(
        ([, init]) => init?.method === "PATCH",
      )!;
      expect(JSON.parse(write[1]!.body as string)).toEqual({
        requirements_token: "requirements-secret",
      });
    } else {
      expect(
        screen.queryByLabelText("Git requirements credential (configured)"),
      ).toBeNull();
    }
  },
);
