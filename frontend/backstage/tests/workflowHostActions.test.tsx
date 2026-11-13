import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BUIProvider } from "@backstage/ui";
import { MemoryRouter } from "react-router-dom";
import {
  ProjectWorkflowPanel,
  type ProjectOperationState,
  type ProjectWorkflowController,
} from "@apme/ui-workflow";
import {
  CommitChangesPanel,
  type CommitSubmitResult,
} from "../../packages/ui-workflow/src/components/CommitChangesPanel";

const hostActions = <button>Open in Dev Spaces</button>;
const published: CommitSubmitResult = {
  branch_name: "apme/remediate-scan-1",
  commit_sha: "12345678",
  pr_url: "https://github.com/acme/repo/pull/12",
  provider: "github",
};

function operation(
  overrides: Partial<ProjectOperationState> = {},
): ProjectOperationState {
  return {
    operation_id: "op-1",
    project_id: "proj-1",
    scan_id: "scan-1",
    status: "completed",
    scan_type: "remediate",
    started_at: "2026-01-01T00:00:00Z",
    progress: [],
    result: {
      total_violations: 2,
      fixable: 2,
      manual_review: 0,
      ai_proposed: 0,
      ai_declined: 0,
      ai_accepted: 0,
      remediated_count: 2,
      patches: [],
      fixed_violations: [],
    },
    ...overrides,
  };
}

function workflow(
  overrides: Partial<ProjectWorkflowController> = {},
): ProjectWorkflowController {
  return {
    attachOp: false,
    setAttachOp: vi.fn(),
    opState: operation(),
    isRunning: false,
    isCancelling: false,
    operationActive: true,
    sessionTabVisible: true,
    refreshOp: vi.fn(),
    clearOp: vi.fn(),
    startScan: vi.fn(),
    beginRemediate: vi.fn(),
    escalateAi: vi.fn(),
    approve: vi.fn(),
    cancel: vi.fn(),
    createPR: vi.fn().mockResolvedValue(published),
    patchProposals: vi.fn().mockResolvedValue(undefined),
    dismiss: vi.fn(),
    resumeSession: vi.fn(),
    startOver: vi.fn(),
    findResumableScanId: vi.fn(),
    ...overrides,
  };
}

function host(body: ReactNode) {
  return (
    <MemoryRouter>
      <BUIProvider>{body}</BUIProvider>
    </MemoryRouter>
  );
}

function panel(
  controller: ProjectWorkflowController,
  actions: ReactNode = hostActions,
) {
  return host(
    <ProjectWorkflowPanel
      workflow={controller}
      enableAi={false}
      feedbackEnabled={false}
      hostShipActions={actions}
    />,
  );
}

function shipAction() {
  return screen.queryByRole("button", { name: "Open in Dev Spaces" });
}

it("hides host actions until publishing succeeds, then shows them once beside the completed PR", async () => {
  const controller = workflow();
  render(panel(controller));
  expect(shipAction()).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Commit & open PR" }));

  await screen.findByText("Operation Complete");
  const pr = screen.getByRole("link", { name: /View pull request/i });
  expect(pr.getAttribute("href")).toBe(`${published.pr_url}/files`);
  expect(controller.createPR).toHaveBeenCalledWith(
    expect.objectContaining({ create_pr: true }),
  );
  expect(
    screen.getAllByRole("button", { name: "Open in Dev Spaces" }),
  ).toHaveLength(1);
  expect(shipAction()?.parentElement).toBe(pr.parentElement?.parentElement);
});

it("keeps host actions hidden after a rejected first submit", async () => {
  render(
    panel(
      workflow({
        createPR: vi.fn().mockRejectedValue(new Error("Push rejected")),
      }),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Commit & open PR" }));
  await screen.findByText("Push rejected");
  expect(shipAction()).toBeNull();
  expect(screen.queryByText("Operation Complete")).toBeNull();
});

it("shows host actions beside Open PR after push and keeps them after a rejected PR retry", async () => {
  const submit = vi
    .fn()
    .mockResolvedValueOnce({ ...published, pr_url: null })
    .mockRejectedValueOnce(new Error("PR unavailable"));
  render(
    host(
      <CommitChangesPanel
        remediatedCount={2}
        onSubmit={submit}
        onFinish={vi.fn()}
        hostShipActions={hostActions}
      />,
    ),
  );
  expect(shipAction()).toBeNull();
  fireEvent.click(
    screen.getByRole("checkbox", { name: "Open a pull request after push" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Commit & push" }));
  const openPr = await screen.findByRole("button", {
    name: "Open pull request",
  });
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({ create_pr: false }),
  );
  expect(shipAction()?.parentElement).toBe(openPr.parentElement);

  fireEvent.click(openPr);
  await screen.findByText("PR unavailable");
  expect(shipAction()).not.toBeNull();
  expect(
    screen.getByRole("button", { name: "Open pull request" }),
  ).not.toBeNull();
});

it("shows host actions beside a previously published PR in the commit panel", () => {
  render(
    host(
      <CommitChangesPanel
        remediatedCount={2}
        onSubmit={vi.fn()}
        onFinish={vi.fn()}
        prUrl={published.pr_url}
        hostShipActions={hostActions}
      />,
    ),
  );
  const pr = screen.getByRole("link", { name: /View pull request/i });
  expect(shipAction()?.parentElement).toBe(pr.parentElement);
});

it("renders a completed PR snapshot even when operationActive is false", () => {
  render(
    panel(
      workflow({
        operationActive: false,
        opState: operation({
          status: "pr_submitted",
          pr_url: published.pr_url!,
        }),
      }),
    ),
  );
  expect(screen.getByText("Operation Complete")).not.toBeNull();
  expect(shipAction()).not.toBeNull();
  expect(
    screen.getByRole("link", { name: /View pull request/i }),
  ).not.toBeNull();
});

it("keeps host actions hidden when finishing without publishing", async () => {
  render(panel(workflow()));
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Operation Complete");
  expect(shipAction()).toBeNull();
  expect(screen.queryByRole("link", { name: /View pull request/i })).toBeNull();
});

it("renders the commit and completion steps without a host node", async () => {
  render(panel(workflow(), null));
  fireEvent.click(screen.getByRole("button", { name: "Commit & open PR" }));
  await screen.findByText("Operation Complete");
  expect(
    screen.getByRole("link", { name: /View pull request/i }),
  ).not.toBeNull();
  expect(shipAction()).toBeNull();
});

it("shows host actions beside a PR even when the completed snapshot has no result payload", () => {
  render(
    panel(
      workflow({
        opState: operation({
          status: "pr_submitted",
          pr_url: published.pr_url!,
          result: undefined,
        }),
      }),
    ),
  );
  expect(screen.getByText("Operation Complete")).not.toBeNull();
  expect(shipAction()).not.toBeNull();
  expect(
    screen.getByRole("link", { name: /View pull request/i }),
  ).not.toBeNull();
});

it("prioritizes stopping over the cleared operation snapshot and allows dismissal", async () => {
  const controller = workflow({ isCancelling: true, opState: null });
  render(panel(controller));
  expect(screen.getByText("Stopping session…")).not.toBeNull();
  expect(screen.queryByText("Starting scan…")).toBeNull();
  expect(shipAction()).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  await waitFor(() => expect(controller.dismiss).toHaveBeenCalledOnce());
});
