/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useState } from "react";
import { Button, Checkbox, Flex, Link, Text } from "@backstage/ui";
import { getActivity } from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import {
  AssessFindingsPanel,
  DiffView,
  violationToFinding,
} from "@apme/ui-workflow";
import { DataStatus } from "./DataStatus";
import { DisclosureSection } from "./DisclosureSection";
import { useLoad } from "./useLoad";

export interface ApmeActivityDetailsProps {
  projectId: string;
  activityId: string;
  adapter: ApmeApiAdapter;
  onClose?: () => void;
  resolveRuleHref?: (ruleId: string) => string | undefined;
}

function externalUrl(value: string | null | undefined): string | undefined {
  try {
    const url = new URL(value ?? "");
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

/** Project-scoped durable results. It never submits or reapplies a past run. */
export function ApmeActivityDetails(props: ApmeActivityDetailsProps) {
  return (
    <ApmeActivityDetailsContent
      key={`${props.projectId}:${props.activityId}`}
      {...props}
    />
  );
}

function ApmeActivityDetailsContent({
  projectId,
  activityId,
  adapter,
  onClose,
  resolveRuleHref,
}: ApmeActivityDetailsProps) {
  const [includeAcknowledged, setIncludeAcknowledged] = useState(false);
  const load = useCallback(async () => {
    const detail = await getActivity(activityId, adapter);
    if (detail.project_id !== projectId)
      throw new Error("This activity does not belong to the selected project.");
    return detail;
  }, [activityId, projectId, adapter]);
  const state = useLoad(load);
  const detail = state.value;
  const findings = (detail?.violations ?? [])
    .filter((row) => includeAcknowledged || !row.suppressed)
    .map(violationToFinding);
  const prUrl = externalUrl(detail?.pr_url);
  return (
    <Flex direction="column" gap="4">
      <Flex justify="between" align="center">
        <Text as="h3" variant="title-small">
          Run {activityId}
        </Text>
        <Flex gap="2">
          <Button
            variant="secondary"
            onPress={state.reload}
            isDisabled={state.loading}
          >
            Refresh run
          </Button>
          {onClose && (
            <Button variant="secondary" onPress={onClose}>
              Close activity
            </Button>
          )}
        </Flex>
      </Flex>
      <DataStatus {...state} subject="activity" />
      {detail && !state.error && !state.loading && (
        <>
          <Text>
            {detail.total_violations} findings · {detail.remediated_count} fixed
            · {detail.manual_review} need manual review
          </Text>
          <Text>
            {detail.scan_type} ·{" "}
            <time dateTime={detail.created_at}>
              {new Date(detail.created_at).toLocaleString()}
            </time>
          </Text>
          {detail.branch_name && <Text>Branch: {detail.branch_name}</Text>}
          {detail.commit_sha && <Text>Commit: {detail.commit_sha}</Text>}
          {prUrl && (
            <Link href={prUrl} target="_blank" rel="noopener noreferrer">
              View pull request
            </Link>
          )}
          <Checkbox
            isSelected={includeAcknowledged}
            onChange={setIncludeAcknowledged}
          >
            Show acknowledged findings
          </Checkbox>
          {findings.length ? (
            <AssessFindingsPanel
              findings={findings}
              resolveRuleHref={resolveRuleHref}
            />
          ) : (
            <Text>No open findings in this run.</Text>
          )}
          {(detail.proposals ?? []).length > 0 && (
            <DisclosureSection title={`Proposals (${detail.proposals.length})`}>
              <Flex direction="column" gap="4">
                {detail.proposals.map((proposal) => (
                  <section
                    key={proposal.id}
                    aria-label={`Proposal ${proposal.proposal_id}`}
                  >
                    <Text as="h4" variant="title-x-small">
                      {proposal.rule_id} · {proposal.file}
                    </Text>
                    <Text>
                      {proposal.status} · Tier {proposal.tier} ·{" "}
                      {Math.round(proposal.confidence * 100)}% confidence
                    </Text>
                    {proposal.explanation && (
                      <Text>{proposal.explanation}</Text>
                    )}
                    {proposal.suggestion && <Text>{proposal.suggestion}</Text>}
                    {proposal.diff_hunk && (
                      <DiffView diff={proposal.diff_hunk} />
                    )}
                  </section>
                ))}
              </Flex>
            </DisclosureSection>
          )}
          {(detail.patches ?? []).length > 0 && (
            <DisclosureSection title={`Changes (${detail.patches.length})`}>
              {detail.patches.map((patch) => (
                <section key={patch.id} aria-label={`Changes to ${patch.file}`}>
                  <Text as="h4" variant="title-x-small">
                    {patch.file}
                  </Text>
                  <DiffView diff={patch.diff} />
                </section>
              ))}
            </DisclosureSection>
          )}
          {(detail.logs ?? []).length > 0 && (
            <DisclosureSection title={`Run log (${detail.logs.length})`}>
              <pre
                style={{
                  maxHeight: 360,
                  overflow: "auto",
                  whiteSpace: "pre-wrap",
                }}
              >
                {detail.logs
                  .map((log) => `[${log.phase}] ${log.message}`)
                  .join("\n")}
              </pre>
            </DisclosureSection>
          )}
        </>
      )}
    </Flex>
  );
}
