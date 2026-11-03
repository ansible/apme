import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { BUIProvider } from "@backstage/ui";
import {
  createDefaultApmeApiAdapter,
  type ViolationDetail,
} from "@apme/client";
import { ApmeProjectDependencies } from "../plugins/backstage-apme/src/components/ApmeProjectDependencies";
import { findingsForDependency } from "../plugins/backstage-apme/src/components/dependencyIssueMatching";

const inventory = {
  ansible_core_version: "2.19",
  collections: [{ fqcn: "ansible.posix", version: "2.1", source: "galaxy" }],
  python_packages: [
    { name: "requests", version: "2.32" },
    { name: "requests_oauthlib", version: "1.0" },
  ],
  requirements_files: [],
  dependency_tree: "",
};
function finding(
  id: number,
  message = "requests==2.32 has known vulnerability CVE-2026-1",
  source = "dep_audit",
): ViolationDetail {
  return {
    id,
    rule_id: "R200",
    level: "medium",
    message,
    validator_source: source,
    file: "",
    line: 0,
    path: "",
    scope: 1,
    remediation_class: 0,
    remediation_resolution: 0,
    original_yaml: "",
  };
}
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
function open(fetch: typeof globalThis.fetch, canAcknowledge = false) {
  return render(
    <MemoryRouter>
      <BUIProvider>
        <ApmeProjectDependencies
          projectId="p1"
          adapter={createDefaultApmeApiAdapter({
            apiBase: "/host/apme",
            fetch,
          })}
          canAcknowledge={canAcknowledge}
        />
      </BUIProvider>
    </MemoryRouter>,
  );
}

it("matches canonical package names without attributing a similarly named package's CVEs", () => {
  const rows = [
    finding(1),
    finding(2, "requests-oauthlib==1.0 has known vulnerability CVE-2026-2"),
  ];
  expect(
    findingsForDependency(rows, "python", "requests").map((row) => row.id),
  ).toEqual([1]);
  expect(
    findingsForDependency(rows, "python", "requests_oauthlib").map(
      (row) => row.id,
    ),
  ).toEqual([2]);
  expect(
    findingsForDependency(
      [
        {
          ...finding(3, "ansible.posix_extra issue", "collection_health"),
          path: "ansible.posix_extra",
        },
      ],
      "collection",
      "ansible.posix",
    ),
  ).toEqual([]);
});

it("loads every findings page before reporting complete dependency counts", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://localhost");
    if (url.pathname.endsWith("/dependencies")) return json(inventory);
    return json(
      url.searchParams.get("offset") === "0"
        ? Array.from({ length: 500 }, (_, i) =>
            finding(i, "Task finding", "native"),
          )
        : [
            {
              ...finding(501, "Collection problem", "collection_health"),
              path: "ansible.posix",
            },
            finding(502),
          ],
    );
  });
  open(fetch);
  await screen.findByText("2 open dependency issues · 0 acknowledged");
  expect(
    fetch.mock.calls.some(([url]) => String(url).includes("offset=500")),
  ).toBe(true);
  fireEvent.click(
    screen.getByRole("button", {
      name: "View issues for ansible.posix: 1 open, 0 acknowledged",
    }),
  );
  await screen.findByText("Collection problem");
  expect(screen.queryByRole("button", { name: /Acknowledge R200/ })).toBeNull();
});

it("confirms project-wide rule scope before acknowledging CVEs and refreshes all affected counts", async () => {
  let acknowledged = false;
  const rows = [
    finding(1),
    finding(2, "requests-oauthlib==1.0 has known vulnerability CVE-2026-2"),
  ];
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/dependencies")) return json(inventory);
    if (init?.method === "POST") {
      acknowledged = true;
      return json({ id: 9 });
    }
    return json(rows.map((row) => ({ ...row, suppressed: acknowledged })));
  });
  open(fetch, true);
  await screen.findByText("2 open dependency issues · 0 acknowledged");
  fireEvent.click(
    screen.getByRole("button", {
      name: "View issues for requests: 1 open, 0 acknowledged",
    }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: /Acknowledge R200:/ }),
  );
  await screen.findByText(
    "Applies to all R200 findings in this project, including future checks.",
  );
  expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(
    false,
  );
  fireEvent.change(screen.getByLabelText("Reason for acknowledgment"), {
    target: { value: "Reviewed compensating controls" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Confirm acknowledgment" }),
  );
  await screen.findByText("0 open dependency issues · 2 acknowledged");
  expect(fetch).toHaveBeenCalledWith(
    "/host/apme/suppressions",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        rule_id: "R200",
        original_yaml: "",
        fingerprint_mode: "rule_only",
        scope: "project:p1",
        reason: "Reviewed compensating controls",
      }),
    }),
  );
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Show acknowledged findings" }),
  );
  await screen.findByText("Acknowledged");
});

it("keeps denied writes visible and leaves issue counts unchanged", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    String(input).endsWith("/dependencies")
      ? json(inventory)
      : init?.method === "POST"
        ? json({ detail: "Acknowledgment denied" }, 403)
        : json([finding(1)]),
  );
  open(fetch, true);
  fireEvent.click(
    await screen.findByRole("button", {
      name: "View issues for requests: 1 open, 0 acknowledged",
    }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: /Acknowledge R200:/ }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Confirm acknowledgment" }),
  );
  await screen.findByText("Acknowledgment denied");
  expect(
    screen.getByText("1 open dependency issue · 0 acknowledged"),
  ).toBeTruthy();
});

it("does not report unavailable findings as healthy dependencies", async () => {
  const fetch = vi.fn(async (input: RequestInfo | URL) =>
    String(input).endsWith("/dependencies")
      ? json(inventory)
      : json({ detail: "Findings unavailable" }, 503),
  );
  open(fetch);
  await screen.findByText("Findings unavailable");
  const table = screen.getByRole("grid", { name: "Project collections" });
  expect(within(table).getByText("Unavailable")).toBeTruthy();
  expect(within(table).queryByText("0 issues")).toBeNull();
});

it("aborts finding pagination when the dependency view is unmounted", async () => {
  let finish!: (response: Response) => void;
  let signal: AbortSignal | undefined;
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/dependencies")) return json(inventory);
    signal = init?.signal as AbortSignal;
    return new Promise<Response>((resolve) => {
      finish = resolve;
    });
  });
  const view = open(fetch);
  await waitFor(() => expect(finish).toBeDefined());
  view.unmount();
  expect(signal?.aborted).toBe(true);
  await act(async () =>
    finish(json(Array.from({ length: 500 }, (_, id) => finding(id)))),
  );
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("uses content-scoped acknowledgment when a dependency finding includes task YAML", async () => {
  const yaml = "- name: Example\n  ansible.builtin.file:\n    mode: '0777'";
  let acknowledged = false;
  const row = {
    ...finding(1, "Collection content issue", "collection_health"),
    rule_id: "R108",
    path: "ansible.posix",
    original_yaml: yaml,
  };
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith("/dependencies")) return json(inventory);
    if (init?.method === "POST") {
      acknowledged = true;
      return json({ id: 9 });
    }
    return json([{ ...row, suppressed: acknowledged }]);
  });
  open(fetch, true);
  fireEvent.click(
    await screen.findByRole("button", {
      name: "View issues for ansible.posix: 1 open, 0 acknowledged",
    }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: /Acknowledge R108:/ }),
  );
  await screen.findByText(
    "Applies to this rule and matching task content within this project, including future checks.",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Confirm acknowledgment" }),
  );
  await screen.findByText("0 open dependency issues · 1 acknowledged");
  const saved = fetch.mock.calls.find(([, init]) => init?.method === "POST");
  expect(JSON.parse(String(saved?.[1]?.body))).toMatchObject({
    rule_id: "R108",
    original_yaml: yaml,
    fingerprint_mode: "full",
    scope: "project:p1",
  });
});

it.each(["adapter", "project"])(
  "discards an open acknowledgment confirmation when the %s changes",
  async (changed) => {
    const oldFetch = vi.fn(
      async (input: RequestInfo | URL, _init?: RequestInit) =>
        String(input).endsWith("/dependencies")
          ? json(inventory)
          : json([finding(1, "requests==2.32 has a private finding")]),
    );
    const oldAdapter = createDefaultApmeApiAdapter({
      apiBase: "/host/apme",
      fetch: oldFetch,
    });
    const view = render(
      <MemoryRouter>
        <BUIProvider>
          <ApmeProjectDependencies
            projectId="p1"
            adapter={oldAdapter}
            canAcknowledge
          />
        </BUIProvider>
      </MemoryRouter>,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: "View issues for requests: 1 open, 0 acknowledged",
      }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Acknowledge R200:/ }),
    );
    await screen.findByText("requests==2.32 has a private finding");
    const nextFetch = vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith("/dependencies") ? json(inventory) : json([]),
    );
    view.rerender(
      <MemoryRouter>
        <BUIProvider>
          <ApmeProjectDependencies
            projectId={changed === "project" ? "p2" : "p1"}
            adapter={
              changed === "project"
                ? oldAdapter
                : createDefaultApmeApiAdapter({
                    apiBase: "/host/next",
                    fetch: nextFetch,
                  })
            }
            canAcknowledge
          />
        </BUIProvider>
      </MemoryRouter>,
    );
    expect(
      screen.queryByText("requests==2.32 has a private finding"),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Confirm acknowledgment" }),
    ).toBeNull();
    await screen.findByText(
      changed === "project"
        ? "1 open dependency issue · 0 acknowledged"
        : "0 open dependency issues · 0 acknowledged",
    );
    expect(
      oldFetch.mock.calls.every(
        ([, init]) => !init?.method || init.method === "GET",
      ),
    ).toBe(true);
  },
);
