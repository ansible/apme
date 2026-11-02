/*
 * Copyright Red Hat
 */

import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import type { ProjectWorkflowController } from "@apme/ui-workflow";
import {
  findPullRequestControl,
  PortalProjectWorkflowPanel,
} from "./PortalProjectWorkflowPanel";

jest.mock("@apme/ui-workflow", () => ({
  OperationPanel: () => (
    <div data-testid="operation-panel">
      <a href="https://github.com/acme/repo/pull/12">View pull request</a>
    </div>
  ),
}));

function workflowStub(
  overrides: Partial<ProjectWorkflowController>,
): ProjectWorkflowController {
  return {
    attachOp: false,
    setAttachOp: jest.fn(),
    opState: null,
    isRunning: false,
    isCancelling: false,
    operationActive: false,
    sessionTabVisible: false,
    refreshOp: jest.fn(),
    clearOp: jest.fn(),
    startScan: jest.fn(),
    beginRemediate: jest.fn(),
    escalateAi: jest.fn(),
    approve: jest.fn(),
    cancel: jest.fn(),
    createPR: jest.fn(),
    patchProposals: jest.fn(),
    dismiss: jest.fn(),
    resumeSession: jest.fn(),
    startOver: jest.fn(),
    findResumableScanId: jest.fn(),
    ...overrides,
  };
}

describe("findPullRequestControl", () => {
  it("prefers a pull request href over button copy", () => {
    const root = document.createElement("div");
    root.innerHTML = `
      <button type="button">View pull request</button>
      <a href="https://github.com/acme/repo/pull/12">Open PR</a>
    `;

    expect(findPullRequestControl(root)?.getAttribute("href")).toBe(
      "https://github.com/acme/repo/pull/12",
    );
  });

  it("falls back to English button copy when no PR href exists", () => {
    const root = document.createElement("div");
    root.innerHTML = `<button type="button">View pull request</button>`;

    expect(findPullRequestControl(root)?.textContent).toBe("View pull request");
  });
});

describe("PortalProjectWorkflowPanel", () => {
  it("renders the stopping spinner while cancel is in progress", () => {
    render(
      <PortalProjectWorkflowPanel
        workflow={workflowStub({
          isCancelling: true,
          operationActive: true,
          opState: null,
        })}
        enableAi={false}
        feedbackEnabled={false}
      />,
    );

    expect(screen.getByText("Stopping session…")).toBeInTheDocument();
    expect(screen.queryByTestId("operation-panel")).not.toBeInTheDocument();
  });

  it("renders the starting spinner when the workflow session is not active", () => {
    render(
      <PortalProjectWorkflowPanel
        workflow={workflowStub({
          operationActive: false,
          opState: null,
        })}
        enableAi={false}
        feedbackEnabled={false}
      />,
    );

    expect(screen.getByText("Starting scan…")).toBeInTheDocument();
  });

  it("renders OperationPanel when the workflow session is active", () => {
    render(
      <PortalProjectWorkflowPanel
        workflow={workflowStub({
          operationActive: true,
          opState: {
            operation_id: "op-1",
            project_id: "proj-1",
            scan_id: "scan-1",
            status: "scanning",
            scan_type: "check",
            started_at: "2026-01-01T00:00:00Z",
            progress: [],
          },
        })}
        enableAi={false}
        feedbackEnabled={false}
      />,
    );

    expect(screen.getByTestId("operation-panel")).toBeInTheDocument();
  });

  it("removes the injected host-action slot on unmount", async () => {
    const { unmount } = render(
      <PortalProjectWorkflowPanel
        workflow={workflowStub({
          operationActive: true,
          opState: {
            operation_id: "op-1",
            project_id: "proj-1",
            scan_id: "scan-1",
            status: "pr_submitted",
            scan_type: "remediate",
            started_at: "2026-01-01T00:00:00Z",
            progress: [],
            pr_url: "https://github.com/acme/repo/pull/12",
          },
        })}
        enableAi={false}
        feedbackEnabled={false}
        hostShipActions={<button type="button">Open in Dev Spaces</button>}
      />,
    );

    await waitFor(() => {
      expect(
        document.querySelector("[data-apme-host-ship-actions]"),
      ).toBeInTheDocument();
    });

    unmount();

    expect(
      document.querySelector("[data-apme-host-ship-actions]"),
    ).not.toBeInTheDocument();
  });
});
