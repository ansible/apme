/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import {
  formatOperationError,
  isActiveOperationStatus,
  isTerminalOperationState,
  latestOperationProgressMessage,
  latestOperationProgressPercent,
  projectHasActiveOperation,
  shouldResumeScanUi,
} from "./operationStatus";

describe("operationStatus", () => {
  it("treats gateway in-flight statuses as active", () => {
    expect(isActiveOperationStatus(undefined)).toBe(false);
    expect(isActiveOperationStatus("scanning")).toBe(true);
    expect(isActiveOperationStatus("cloning")).toBe(true);
    expect(isActiveOperationStatus("applying")).toBe(true);
    expect(isActiveOperationStatus("awaiting_approval")).toBe(false);
    expect(isActiveOperationStatus("completed")).toBe(false);
    expect(isActiveOperationStatus("pr_submitted")).toBe(false);
  });

  it("does not treat cloning as terminal during polling", () => {
    expect(
      isTerminalOperationState(
        {
          operation_id: "op",
          project_id: "p",
          status: "cloning",
        },
        1,
      ),
    ).toBe(false);
  });

  it("terminal when null after min polls", () => {
    expect(isTerminalOperationState(null, 1)).toBe(false);
    expect(isTerminalOperationState(null, 3)).toBe(true);
  });

  it("waits for explicit state when requested", () => {
    expect(isTerminalOperationState(null, 10, 2, true)).toBe(false);
  });

  it("terminal when status is failed or completed", () => {
    expect(
      isTerminalOperationState(
        {
          operation_id: "op",
          project_id: "p",
          status: "failed",
          progress_pct: 0,
        },
        1,
      ),
    ).toBe(true);
    expect(
      isTerminalOperationState(
        {
          operation_id: "op",
          project_id: "p",
          status: "awaiting_approval",
        },
        1,
      ),
    ).toBe(true);
  });

  it("does not treat awaiting_approval on project snapshot as in-flight", () => {
    expect(
      projectHasActiveOperation({
        active_operation: { status: "awaiting_approval" },
      }),
    ).toBe(false);
    expect(
      projectHasActiveOperation({
        active_operation: { status: "scanning" },
      }),
    ).toBe(true);
  });

  it("reads progress percent from gateway progress log", () => {
    expect(
      latestOperationProgressPercent({
        operation_id: "op",
        project_id: "p",
        status: "scanning",
        progress: [
          { phase: "scan", message: "Starting", timestamp: "t1" },
          { phase: "scan", message: "Halfway", timestamp: "t2", progress: 42 },
        ],
      }),
    ).toBe(42);
  });

  it("prefers progress_pct when present", () => {
    expect(
      latestOperationProgressPercent({
        operation_id: "op",
        project_id: "p",
        status: "scanning",
        progress_pct: 88,
        progress: [
          { phase: "scan", message: "x", timestamp: "t1", progress: 5 },
        ],
      }),
    ).toBe(88);
  });

  it("reads latest progress message from log or latest_message", () => {
    expect(
      latestOperationProgressMessage({
        operation_id: "op",
        project_id: "p",
        status: "scanning",
        latest_message: "fallback",
        progress: [
          { phase: "scan", message: "Starting", timestamp: "t1" },
          { phase: "scan", message: "Almost done", timestamp: "t2" },
        ],
      }),
    ).toBe("Almost done");
    expect(
      latestOperationProgressMessage({
        operation_id: "op",
        project_id: "p",
        status: "scanning",
        latest_message: "fallback",
      }),
    ).toBe("fallback");
    expect(
      latestOperationProgressMessage({
        operation_id: "op",
        project_id: "p",
        status: "scanning",
        progress: [{ phase: "scan", message: "", timestamp: "t1" }],
      }),
    ).toBe("");
  });

  it("treats legacy string active_operation as in-flight only for active statuses", () => {
    expect(projectHasActiveOperation({ active_operation: "scanning" })).toBe(
      true,
    );
    expect(projectHasActiveOperation({ active_operation: "completed" })).toBe(
      false,
    );
    expect(projectHasActiveOperation({ active_operation: "failed" })).toBe(
      false,
    );
    expect(projectHasActiveOperation(null)).toBe(false);
  });

  it("does not resume Scanning UI for completed ops that still have progress logs", () => {
    expect(
      shouldResumeScanUi({
        operation_id: "op",
        project_id: "p",
        status: "completed",
        progress: [
          {
            phase: "scan",
            message: "Graph Tier 1 converged: 2 pass(es)",
            timestamp: "t1",
          },
        ],
      }),
    ).toBe(false);
    expect(
      shouldResumeScanUi({
        operation_id: "op",
        project_id: "p",
        status: "scanning",
        progress: [{ phase: "scan", message: "Running…", timestamp: "t1" }],
      }),
    ).toBe(true);
    expect(shouldResumeScanUi(null)).toBe(false);
  });

  it("formats gateway operation errors for display", () => {
    expect(formatOperationError(null)).toBe("Scan failed");
    expect(
      formatOperationError('grpc_message:"clone failed" details = "bad ref"'),
    ).toBe("bad ref");
    expect(formatOperationError("plain error line")).toBe("plain error line");
  });

  it("terminal for unknown status when not waiting for explicit state", () => {
    expect(
      isTerminalOperationState(
        {
          operation_id: "op",
          project_id: "p",
          status: "mystery",
        },
        1,
      ),
    ).toBe(true);
  });
});
