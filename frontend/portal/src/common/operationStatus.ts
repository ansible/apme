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

import type { OperationState } from "./types";

/** Gateway ADR-052 terminal operation statuses. */
const TERMINAL_OPERATION_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
  "expired",
  "pr_submitted",
]);

/** In-flight gateway and legacy mock statuses (not terminal). */
const ACTIVE_OPERATION_STATUSES = new Set([
  "running",
  "queued",
  "cloning",
  "scanning",
  "generating",
  "pending",
  "in_progress",
  "applying",
  "submitting_pr",
]);

/** Statuses that may appear on project.active_operation but are not in-flight work. */
const PROJECT_IDLE_OPERATION_STATUSES = new Set(["awaiting_approval"]);

export function isActiveOperationStatus(status: string | undefined): boolean {
  if (!status) {
    return false;
  }
  const normalized = status.toLowerCase();
  if (TERMINAL_OPERATION_STATUSES.has(normalized)) {
    return false;
  }
  if (PROJECT_IDLE_OPERATION_STATUSES.has(normalized)) {
    return false;
  }
  return ACTIVE_OPERATION_STATUSES.has(normalized);
}

/** True when the gateway reports an in-flight check or remediate operation. */
export function projectHasActiveOperation(
  project: { active_operation?: unknown } | null | undefined,
): boolean {
  const active = project?.active_operation;
  if (!active) {
    return false;
  }
  if (typeof active === "string") {
    return isActiveOperationStatus(active);
  }
  if (typeof active === "object" && "status" in active) {
    return isActiveOperationStatus(
      String((active as { status?: string }).status),
    );
  }
  return true;
}

/** True when polling can stop (gateway mock uses running; live gateway uses scanning/queued). */
export function isTerminalOperationState(
  state: OperationState | null,
  pollCount: number,
  minPollsBeforeNull = 2,
  waitForExplicitState = false,
): boolean {
  if (!state) {
    if (waitForExplicitState) {
      return false;
    }
    return pollCount > minPollsBeforeNull;
  }
  const status = state.status?.toLowerCase() ?? "";
  if (TERMINAL_OPERATION_STATUSES.has(status)) {
    return true;
  }
  // Proposals delivered — generate step can move to review.
  if (status === "awaiting_approval") {
    return true;
  }
  if (isActiveOperationStatus(status)) {
    return false;
  }
  return !waitForExplicitState;
}

/** True when the Quality tab should resume Scanning UI for a gateway op. */
export function shouldResumeScanUi(
  state: OperationState | null | undefined,
): boolean {
  return isActiveOperationStatus(state?.status);
}

/** Extract a short user-facing message from a gateway operation error string. */
export function formatOperationError(error?: string | null): string {
  if (!error) {
    return "Scan failed";
  }
  const detailsMatch = /details = "([^"]+)"/.exec(error);
  if (detailsMatch?.[1]) {
    return detailsMatch[1];
  }
  const grpcMessage = /grpc_message:"([^"]+)"/.exec(error);
  if (grpcMessage?.[1]) {
    return grpcMessage[1];
  }
  return error.split("\n")[0].slice(0, 300);
}

export function latestOperationProgressMessage(
  state: OperationState | null,
): string | null {
  const entries = state?.progress;
  if (!entries?.length) {
    return state?.latest_message ?? null;
  }
  return entries.at(-1)?.message ?? null;
}

export function latestOperationProgressPercent(
  state: OperationState | null,
): number | undefined {
  if (state?.progress_pct !== undefined) {
    return state.progress_pct;
  }
  const entries = state?.progress;
  if (!entries?.length) {
    return undefined;
  }
  for (let i = entries.length - 1; i >= 0; i -= 1) {
    const progress = entries[i]?.progress;
    if (typeof progress === "number" && !Number.isNaN(progress)) {
      return progress;
    }
  }
  return undefined;
}
