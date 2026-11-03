import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BUIProvider } from "@backstage/ui";
import { createDefaultApmeApiAdapter } from "@apme/client";
import { ApmeRules } from "../plugins/backstage-apme/src/components/ApmeRules";

function setup(canManage = true, denied = false) {
  const row = {
    rule_id: "L050",
    description: "Use descriptive task names",
    category: "lint",
    source: "native",
    scope: 1,
    default_severity: 2,
    default_severity_label: "low",
    resolved_severity: 2,
    resolved_severity_label: "low",
    enabled: true,
    resolved_enabled: true,
    registered_at: "2026-11-02",
    override: null as null | Record<string, unknown>,
  };
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "PUT") {
      if (denied)
        return new Response('{"detail":"Rule management denied"}', {
          status: 403,
        });
      const patch = JSON.parse(String(init.body));
      row.override = { ...row.override, ...patch };
      if (patch.enabled_override !== undefined)
        row.resolved_enabled = patch.enabled_override;
      if (patch.severity_override !== undefined) {
        row.resolved_severity = patch.severity_override;
        row.resolved_severity_label = [
          "",
          "info",
          "low",
          "medium",
          "high",
          "error",
          "critical",
        ][patch.severity_override];
      }
      return new Response("{}");
    }
    if (init?.method === "DELETE") {
      row.override = null;
      row.resolved_enabled = true;
      row.resolved_severity = 2;
      return new Response(null, { status: 204 });
    }
    return new Response(JSON.stringify([row]));
  });
  render(
    <BUIProvider>
      <ApmeRules
        canManage={canManage}
        adapter={createDefaultApmeApiAdapter({ apiBase: "/host", fetch })}
      />
    </BUIProvider>,
  );
  return fetch;
}

it("exposes enable/severity/enforcement controls directly in the rule table and refreshes saved state", async () => {
  const fetch = setup();
  const enabled = await screen.findByRole("switch", { name: "Enable L050" });
  expect((enabled as HTMLInputElement).checked).toBe(true);
  fireEvent.click(enabled);
  await waitFor(() =>
    expect(
      (screen.getByRole("switch", { name: "Enable L050" }) as HTMLInputElement)
        .checked,
    ).toBe(false),
  );
  expect(fetch).toHaveBeenCalledWith(
    "/host/rules/L050/config",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ enabled_override: false }),
    }),
  );
  fireEvent.click(
    screen.getByRole("switch", { name: "Enforce L050 despite inline ignores" }),
  );
  await screen.findByRole("switch", { name: "Enable L050" });
  expect(fetch).toHaveBeenCalledWith(
    "/host/rules/L050/config",
    expect.objectContaining({ body: JSON.stringify({ enforced: true }) }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Reset overrides for L050" }),
  );
  await waitFor(() =>
    expect(
      (screen.getByRole("switch", { name: "Enable L050" }) as HTMLInputElement)
        .checked,
    ).toBe(true),
  );
});

it("sends the exact six-level severity value selected in the table", async () => {
  const fetch = setup();
  const trigger = await screen.findByRole("button", {
    name: /Severity for L050/,
  });
  fireEvent.click(trigger);
  fireEvent.click(await screen.findByRole("option", { name: "Critical" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/host/rules/L050/config",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ severity_override: 6 }),
      }),
    ),
  );
  await screen.findByRole("button", { name: /Critical/ });
});

it("keeps rule controls read-only without host authorization", async () => {
  const fetch = setup(false);
  fireEvent.click(await screen.findByRole("button", { name: "L050" }));
  await screen.findByRole("dialog");
  expect(screen.queryByRole("switch")).toBeNull();
  expect(screen.queryByRole("button", { name: "Disable rule" })).toBeNull();
  expect(
    fetch.mock.calls.every(
      ([, init]) => !init?.method || init.method === "GET",
    ),
  ).toBe(true);
});

it("shows a denied rule mutation and retains the server's enabled state", async () => {
  setup(true, true);
  fireEvent.click(await screen.findByRole("switch", { name: "Enable L050" }));
  await screen.findByText("Rule management denied");
  expect(
    (screen.getByRole("switch", { name: "Enable L050" }) as HTMLInputElement)
      .checked,
  ).toBe(true);
});
