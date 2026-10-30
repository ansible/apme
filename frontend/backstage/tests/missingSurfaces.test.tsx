import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BUIProvider } from "@backstage/ui";
import {
  createDefaultApmeApiAdapter,
  type ApmeSessionSocket,
} from "@apme/client";
import { ApmeAiProviders } from "../plugins/backstage-apme/src/components/ApmeAiProviders";
import { ApmeUploadWorkbench } from "../plugins/backstage-apme/src/components/ApmeUploadWorkbench";

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});
it("makes no provider or upload requests without host permission", () => {
  const fetch = vi.fn();
  const adapter = createDefaultApmeApiAdapter({ fetch });
  render(
    <BUIProvider>
      <ApmeAiProviders adapter={adapter} />
      <ApmeUploadWorkbench adapter={adapter} />
    </BUIProvider>,
  );
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Add AI provider" })).toBeNull();
});
it("shows deployment providers without editing actions", async () => {
  const adapter = createDefaultApmeApiAdapter({
    fetch: vi.fn(
      async (input) =>
        new Response(
          JSON.stringify(
            String(input).endsWith("/providers")
              ? []
              : {
                  config: {
                    providers: {
                      system: {
                        engine: "openai",
                        models: { model: {} },
                        apiKey: "never-render-this",
                      },
                    },
                  },
                },
          ),
        ),
    ),
  });
  render(
    <BUIProvider>
      <ApmeAiProviders adapter={adapter} canView canManage />
    </BUIProvider>,
  );
  await screen.findByText("Deployment configuration (read only)");
  expect(screen.queryByRole("button", { name: "Edit system" })).toBeNull();
  expect(document.body.textContent).not.toContain("never-render-this");
});
it("pastes content into the existing upload session protocol and shows its result", async () => {
  const sent: Array<Record<string, unknown>> = [];
  const socket: ApmeSessionSocket = {
    readyState: 1,
    onopen: null,
    onmessage: null,
    onerror: null,
    onclose: null,
    send(text) {
      const frame = JSON.parse(text);
      sent.push(frame);
      if (frame.type === "files_done") {
        socket.onmessage?.(
          new MessageEvent("message", {
            data: JSON.stringify({
              type: "session_created",
              session_id: "session",
              scan_id: "scan",
              ttl_seconds: 60,
            }),
          }),
        );
        socket.onmessage?.(
          new MessageEvent("message", {
            data: JSON.stringify({
              type: "result",
              scan_id: "scan",
              patches: [],
              report: null,
              remaining_violations: [],
            }),
          }),
        );
      }
    },
    close() {},
  };
  const adapter = createDefaultApmeApiAdapter({
    fetch: vi.fn(async () => new Response("[]")),
    openSessionSocket: async () => {
      setTimeout(() => socket.onopen?.(new Event("open")), 0);
      return socket;
    },
  });
  render(
    <BUIProvider>
      <ApmeUploadWorkbench adapter={adapter} canUpload />
    </BUIProvider>,
  );
  fireEvent.change(
    screen.getByRole("textbox", { name: "Paste Ansible content" }),
    { target: { value: "- hosts: all\n  tasks: []\n" } },
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Check uploaded content" }),
  );
  await screen.findByText("Check complete");
  expect(sent.map((frame) => frame.type)).toEqual([
    "start",
    "file",
    "files_done",
    "close",
  ]);
  expect(sent[1]).toMatchObject({ path: "playbook.yml" });
  expect(atob(String(sent[1].content))).toBe("- hosts: all\n  tasks: []\n");
  await waitFor(() =>
    expect(screen.getByText("0 remaining findings")).toBeTruthy(),
  );
});
