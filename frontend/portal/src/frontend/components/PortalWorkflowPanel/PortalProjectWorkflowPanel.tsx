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
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import {
  Alert,
  AlertActionCloseButton,
  Button,
  Card,
  CardBody,
  Spinner,
} from "@patternfly/react-core";
import {
  OperationPanel,
  type ProjectWorkflowController,
} from "@apme/ui-workflow";

export interface PortalProjectWorkflowPanelProps {
  workflow: ProjectWorkflowController;
  enableAi: boolean;
  feedbackEnabled: boolean;
  onViewDetails?: (scanId: string) => void;
  /** Rendered beside View pull request in commit/result steps (US-008). */
  hostShipActions?: ReactNode;
}

const PR_LINK_SELECTOR = 'a[href*="/pull/"], a[href*="/merge_requests/"]';

/**
 * Locate the View pull request control rendered by `@apme/ui-workflow`.
 * Prefer stable PR/MR hrefs; fall back to English button copy.
 *
 * ponytail: replace with ui-workflow `hostShipActions` when upstream adds it.
 */
export function findPullRequestControl(root: HTMLElement): HTMLElement | null {
  const prAnchor = root.querySelector<HTMLElement>(PR_LINK_SELECTOR);
  if (prAnchor) {
    return prAnchor;
  }

  return (
    Array.from(
      root.querySelectorAll<HTMLElement>(".pf-v6-c-button, a, button"),
    ).find((el) => /view pull request/i.test(el.textContent ?? "")) ?? null
  );
}

export function PortalProjectWorkflowPanel({
  workflow,
  enableAi,
  feedbackEnabled,
  onViewDetails,
  hostShipActions,
}: Readonly<PortalProjectWorkflowPanelProps>) {
  const {
    operationActive,
    opState,
    isCancelling,
    approve,
    beginRemediate,
    escalateAi,
    patchProposals,
    cancel,
    createPR,
    dismiss,
  } = workflow;
  const [draftError, setDraftError] = useState<string | null>(null);
  const draftGenRef = useRef(0);
  const panelRef = useRef<HTMLDivElement>(null);
  const injectedSlotRef = useRef<HTMLElement | null>(null);
  const locateFrameRef = useRef(0);
  const [actionHost, setActionHost] = useState<HTMLElement | null>(null);
  const operationId = opState?.operation_id;
  const opStatus = opState?.status;

  useEffect(() => {
    draftGenRef.current += 1;
    setDraftError(null);
  }, [operationId, opStatus]);

  useLayoutEffect(() => {
    const root = panelRef.current;
    if (!root || !hostShipActions) {
      injectedSlotRef.current?.remove();
      injectedSlotRef.current = null;
      setActionHost(null);
      return undefined;
    }

    const locateActionHost = () => {
      const prControl = findPullRequestControl(root);
      if (!prControl?.parentElement) {
        injectedSlotRef.current?.remove();
        injectedSlotRef.current = null;
        setActionHost(null);
        return;
      }

      let slot = injectedSlotRef.current;
      if (!slot?.isConnected) {
        slot = prControl.parentElement.querySelector<HTMLElement>(
          "[data-apme-host-ship-actions]",
        );
      }
      if (!slot) {
        slot = document.createElement("span");
        slot.dataset.apmeHostShipActions = "true";
        slot.style.display = "inline-flex";
        prControl.before(slot);
      }
      injectedSlotRef.current = slot;
      setActionHost(slot);
    };

    const scheduleLocateActionHost = () => {
      cancelAnimationFrame(locateFrameRef.current);
      locateFrameRef.current = requestAnimationFrame(locateActionHost);
    };

    scheduleLocateActionHost();
    const observer = new MutationObserver(scheduleLocateActionHost);
    observer.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      characterData: true,
    });

    return () => {
      cancelAnimationFrame(locateFrameRef.current);
      observer.disconnect();
      injectedSlotRef.current?.remove();
      injectedSlotRef.current = null;
      setActionHost(null);
    };
  }, [hostShipActions, operationId, opStatus, opState?.pr_url]);

  // Cancel clears the op snapshot before dismiss — show stopping, not starting.
  if (isCancelling) {
    return (
      <Card>
        <CardBody style={{ textAlign: "center", padding: "48px 24px" }}>
          <Spinner size="lg" />
          <div style={{ marginTop: 12, fontSize: 16 }}>Stopping session…</div>
          <Button variant="link" onClick={dismiss} style={{ marginTop: 16 }}>
            Dismiss
          </Button>
        </CardBody>
      </Card>
    );
  }

  if (!operationActive || !opState) {
    return (
      <Card>
        <CardBody style={{ textAlign: "center", padding: "48px 24px" }}>
          <Spinner size="lg" />
          <div style={{ marginTop: 12, fontSize: 16 }}>Starting scan…</div>
          <Button variant="link" onClick={dismiss} style={{ marginTop: 16 }}>
            Dismiss
          </Button>
        </CardBody>
      </Card>
    );
  }

  return (
    <div ref={panelRef}>
      {actionHost && hostShipActions
        ? createPortal(hostShipActions, actionHost)
        : null}
      {draftError ? (
        <Alert
          variant="danger"
          title="Could not save draft proposals"
          isInline
          style={{ marginBottom: 12 }}
          actionClose={
            <AlertActionCloseButton onClose={() => setDraftError(null)} />
          }
        >
          {draftError}
        </Alert>
      ) : null}
      <OperationPanel
        state={opState}
        onApprove={approve}
        onBeginRemediate={beginRemediate}
        onEscalateAi={escalateAi}
        onDraftUpdate={(updates) => {
          const gen = draftGenRef.current;
          setDraftError(null);
          patchProposals(updates).catch((err) => {
            if (gen !== draftGenRef.current) {
              return;
            }
            setDraftError(
              err instanceof Error ? err.message : "Draft update failed.",
            );
          });
        }}
        onCancel={cancel}
        onCreatePR={createPR}
        onDismiss={dismiss}
        feedbackEnabled={feedbackEnabled}
        enableAi={enableAi}
        onViewDetails={onViewDetails}
      />
    </div>
  );
}
