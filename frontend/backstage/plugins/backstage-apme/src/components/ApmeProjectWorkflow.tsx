/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import "./layout.css";
import { useCallback, useState } from "react";
import { Alert, Box, Button, Flex, Text } from "@backstage/ui";
import type { ApmeApiAdapter } from "@apme/client/transport";
import {
  ApmeApiProvider,
  useProjectWorkflow,
  fetchProjectOperationState,
  LIVE_OPERATION_STATUSES,
} from "@apme/ui-workflow/headless";
import { CheckOptionsForm, ProjectWorkflowPanel } from "@apme/ui-workflow";
import { ApmeButton } from "./layout";
import { DataStatus } from "./DataStatus";
import { useLoad } from "./useLoad";

export interface ApmeProjectWorkflowProps {
  projectId: string;
  adapter: ApmeApiAdapter;
  enableAi?: boolean;
  feedbackEnabled?: boolean;
  onViewDetails?: (scanId: string) => void;
}

/** Embeddable in Local or a Portal-authorized project surface. */
export function ApmeProjectWorkflow(props: ApmeProjectWorkflowProps) {
  return (
    <ApmeApiProvider adapter={props.adapter}>
      <ProjectWorkflow key={props.projectId} {...props} />
    </ApmeApiProvider>
  );
}

function ProjectWorkflow({
  projectId,
  adapter,
  enableAi: aiAvailable = true,
  feedbackEnabled = false,
  onViewDetails,
}: ApmeProjectWorkflowProps) {
  const [ansibleVersion, setAnsibleVersion] = useState("");
  const [collections, setCollections] = useState("");
  const [enableAi, setEnableAi] = useState(false);
  const [autoApplyTier1, setAutoApplyTier1] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string>();
  const workflow = useProjectWorkflow(projectId, {
    checkOptions: {
      ansibleVersion,
      collections,
      enableAi: aiAvailable && enableAi,
      autoApplyTier1,
    },
    // Gateway snapshots outlive this view. Only Start/Resume attaches, per ADR-065.
    initiallyAttached: false,
  });
  const discover = useCallback(
    () => fetchProjectOperationState(projectId, adapter),
    [projectId, adapter],
  );
  const available = useLoad(discover, !workflow.attachOp);
  const resumable =
    available.value &&
    (LIVE_OPERATION_STATUSES.has(available.value.status) ||
      (available.value.status === "completed" &&
        !available.value.pr_url &&
        ((available.value.result?.remediated_count ?? 0) > 0 ||
          (available.value.result?.patches?.length ?? 0) > 0)));

  async function start() {
    setStarting(true);
    setError(undefined);
    try {
      await workflow.startScan();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start check");
    } finally {
      setStarting(false);
    }
  }

  return (
    <Box className="apme-ui">
      {error && <Alert status="danger" title={error} />}
      {workflow.sessionTabVisible ? (
        <ProjectWorkflowPanel
          workflow={workflow}
          enableAi={aiAvailable && enableAi}
          feedbackEnabled={feedbackEnabled}
          onViewDetails={onViewDetails}
        />
      ) : (
        <Flex className="apme-form apme-panel" direction="column" gap="4">
          <DataStatus {...available} subject="available workflow" />
          {resumable && (
            <div className="apme-stack">
              <Text>
                A workflow is available to resume (
                {available.value?.status.replace(/_/g, " ")}). Starting a new
                check may require discarding its draft changes.
              </Text>
              <ApmeButton variant="secondary" onPress={workflow.resumeSession}>
                Resume workflow
              </ApmeButton>
            </div>
          )}
          <Text>
            Check this repository, then review findings and proposed fixes.
          </Text>
          <CheckOptionsForm
            ansibleVersion={ansibleVersion}
            onAnsibleVersionChange={setAnsibleVersion}
            collections={collections}
            onCollectionsChange={setCollections}
            enableAi={enableAi}
            onEnableAiChange={setEnableAi}
            autoApplyTier1={autoApplyTier1}
            onAutoApplyTier1Change={setAutoApplyTier1}
            showAiOptions={aiAvailable}
            idPrefix={`apme-${projectId}`}
          />
          <Button onPress={() => void start()} isDisabled={starting}>
            {starting ? "Starting check…" : "Check repository"}
          </Button>
        </Flex>
      )}
    </Box>
  );
}
