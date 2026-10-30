/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useState } from "react";
import { Alert, Box, Button, Flex, Text } from "@backstage/ui";
import type { ApmeApiAdapter } from "@apme/client/transport";
import {
  ApmeApiProvider,
  useProjectWorkflow,
} from "@apme/ui-workflow/headless";
import { CheckOptionsForm, ProjectWorkflowPanel } from "@apme/ui-workflow";

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
    initiallyAttached: true,
  });

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
    <Box>
      {error && <Alert status="danger" title={error} />}
      {workflow.sessionTabVisible ? (
        <ProjectWorkflowPanel
          workflow={workflow}
          enableAi={aiAvailable && enableAi}
          feedbackEnabled={feedbackEnabled}
          onViewDetails={onViewDetails}
        />
      ) : (
        <Flex direction="column" gap="4">
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
