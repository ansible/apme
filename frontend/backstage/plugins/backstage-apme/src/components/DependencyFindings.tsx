/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import { useCallback, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Cell,
  Checkbox,
  Column,
  Dialog,
  DialogBody,
  DialogFooter,
  DialogHeader,
  Row,
  TableBody,
  TableHeader,
  Text,
  TextField,
} from "@backstage/ui";
import {
  createSuppression,
  listProjectViolations,
  type ViolationDetail,
} from "@apme/client";
import type { ApmeApiAdapter } from "@apme/client/transport";
import { DataStatus } from "./DataStatus";
import { ApmeTable } from "./layout";
import { useLoad } from "./useLoad";
import { useMutation } from "./useMutation";
import {
  findingsForDependency,
  isDependencyFinding,
  type DependencyKind,
} from "./dependencyIssueMatching";

export function DependencyFindings({
  projectId,
  adapter,
  canAcknowledge = false,
  children,
}: {
  projectId: string;
  adapter: ApmeApiAdapter;
  canAcknowledge?: boolean;
  children: (
    renderIssues: (kind: DependencyKind, name: string) => ReactNode,
  ) => ReactNode;
}) {
  const load = useCallback(
    async (signal: AbortSignal) => {
      const findings: ViolationDetail[] = [];
      const seen = new Set<number>();
      for (let offset = 0; ; offset += 500) {
        signal.throwIfAborted();
        const page = await listProjectViolations(
          projectId,
          500,
          offset,
          undefined,
          undefined,
          adapter,
          signal,
        );
        if (!Array.isArray(page))
          throw new Error("Unexpected findings response.");
        const fresh = page.filter((finding) => !seen.has(finding.id));
        if (page.length && !fresh.length)
          throw new Error(
            "Finding pagination did not advance. Refresh to try again.",
          );
        for (const finding of fresh) seen.add(finding.id);
        findings.push(...fresh.filter(isDependencyFinding));
        if (page.length < 500) return findings;
      }
    },
    [projectId, adapter],
  );
  const state = useLoad(load);
  const mutation = useMutation(load);
  const [target, setTarget] = useState<{
    adapter: ApmeApiAdapter;
    projectId: string;
    kind?: DependencyKind;
    name: string;
  }>();
  const [includeAcknowledged, setIncludeAcknowledged] = useState(false);
  const [pending, setPending] = useState<ViolationDetail>();
  const [reason, setReason] = useState("");
  const currentTarget =
    target?.adapter === adapter && target.projectId === projectId
      ? target
      : undefined;
  const all = state.value ?? [];
  const active = all.filter((finding) => !finding.suppressed);
  const selected = currentTarget?.kind
    ? findingsForDependency(all, currentTarget.kind, currentTarget.name)
    : all;
  const shown = selected.filter(
    (finding) => includeAcknowledged || !finding.suppressed,
  );
  const [pageOffset, setPageOffset] = useState(0);
  const shownOffset = Math.min(
    pageOffset,
    Math.max(0, Math.ceil(shown.length / 25) - 1) * 25,
  );
  function open(name: string, kind?: DependencyKind) {
    setTarget({ name, kind, adapter, projectId });
    setIncludeAcknowledged(false);
    setPageOffset(0);
    setPending(undefined);
  }
  function close() {
    if (!mutation.saving) {
      setTarget(undefined);
      setPending(undefined);
    }
  }
  function issueButton(kind: DependencyKind, name: string) {
    if (!state.value)
      return (
        <Text color="secondary">
          {state.loading ? "Loading…" : "Unavailable"}
        </Text>
      );
    const findings = findingsForDependency(all, kind, name);
    const count = findings.filter((finding) => !finding.suppressed).length;
    const acknowledged = findings.length - count;
    if (!findings.length) return <Text color="secondary">0 issues</Text>;
    return (
      <Button
        variant="tertiary"
        className="apme-table-link apme-issue-count"
        data-has-issues={count > 0}
        aria-label={`View issues for ${name}: ${count} open, ${acknowledged} acknowledged`}
        onPress={() => open(name, kind)}
      >
        {count} {count === 1 ? "issue" : "issues"}
        {acknowledged ? ` · ${acknowledged} acknowledged` : ""}
      </Button>
    );
  }
  async function acknowledge() {
    if (!canAcknowledge || !pending || !currentTarget) return;
    const finding = pending;
    await mutation.run(
      async () => {
        try {
          await createSuppression(
            {
              rule_id: finding.rule_id,
              original_yaml: finding.original_yaml?.trim()
                ? finding.original_yaml
                : "",
              fingerprint_mode: finding.original_yaml?.trim()
                ? "full"
                : "rule_only",
              scope: `project:${projectId}`,
              reason: reason.trim() || "Reviewed in dependency findings",
            },
            adapter,
          );
        } catch (error) {
          if (!(
            error &&
            typeof error === "object" &&
            "status" in error &&
            error.status === 409
          ))
            throw error;
        }
      },
      () => {
        setPending(undefined);
        state.reload();
      },
    );
  }
  return (
    <>
      <DataStatus {...state} subject="dependency findings" />
      {state.value && (
        <div className="apme-dependency-summary">
          <Text>
            {active.length} open dependency{" "}
            {active.length === 1 ? "issue" : "issues"} ·{" "}
            {all.length - active.length} acknowledged
          </Text>
          {all.length > 0 && (
            <Button
              variant="tertiary"
              onPress={() => open("All dependency findings")}
            >
              View all dependency findings
            </Button>
          )}
          <Button
            variant="secondary"
            isDisabled={state.loading}
            onPress={state.reload}
          >
            Refresh issues
          </Button>
        </div>
      )}
      {children(issueButton)}
      {currentTarget && (
        <Dialog
          width="min(70rem, calc(100vw - 32px))"
          isOpen
          onOpenChange={(open) => {
            if (!open) close();
          }}
        >
          <DialogHeader>
            {pending ? `Acknowledge ${pending.rule_id}?` : currentTarget.name}
          </DialogHeader>
          <DialogBody>
            <div className="apme-ui apme-stack apme-findings-dialog">
              {mutation.error && (
                <Alert
                  status="danger"
                  title="Could not acknowledge finding"
                  description={mutation.error}
                />
              )}
              {pending ? (
                <>
                  <Text>{pending.message}</Text>
                  <Alert
                    status="warning"
                    title="Acknowledgment scope"
                    description={
                      pending.original_yaml?.trim()
                        ? "Applies to this rule and matching task content within this project, including future checks."
                        : `Applies to all ${pending.rule_id} findings in this project, including future checks.`
                    }
                  />
                  <TextField
                    label="Reason for acknowledgment"
                    value={reason}
                    onChange={setReason}
                    maxLength={1000}
                    isDisabled={mutation.saving}
                  />
                </>
              ) : (
                <>
                  <DataStatus {...state} subject="dependency findings" />
                  <Checkbox
                    isSelected={includeAcknowledged}
                    onChange={(value) => {
                      setIncludeAcknowledged(value);
                      setPageOffset(0);
                    }}
                  >
                    Show acknowledged findings
                  </Checkbox>
                  <ApmeTable aria-label="Dependency findings">
                    <TableHeader>
                      <Column isRowHeader>Rule</Column>
                      <Column>Severity</Column>
                      <Column style={{ width: "50%" }}>Finding</Column>
                      <Column>Status</Column>
                    </TableHeader>
                    <TableBody
                      items={shown.slice(shownOffset, shownOffset + 25)}
                      renderEmptyState={() =>
                        "No open findings. Enable Show acknowledged findings to review acknowledged items."
                      }
                    >
                      {(finding) => (
                        <Row id={finding.id}>
                          <Cell>{finding.rule_id}</Cell>
                          <Cell>{finding.level}</Cell>
                          <Cell>
                            <Text>{finding.message}</Text>
                            {finding.file && (
                              <Text as="p" color="secondary">
                                {finding.file}
                                {finding.line ? `:${finding.line}` : ""}
                              </Text>
                            )}
                          </Cell>
                          <Cell>
                            {finding.suppressed ? (
                              "Acknowledged"
                            ) : canAcknowledge ? (
                              <Button
                                variant="secondary"
                                isDisabled={state.loading || mutation.saving}
                                aria-label={`Acknowledge ${finding.rule_id}: ${finding.message}`}
                                onPress={() => {
                                  setPending(finding);
                                  setReason("");
                                }}
                              >
                                Acknowledge
                              </Button>
                            ) : (
                              "Open"
                            )}
                          </Cell>
                        </Row>
                      )}
                    </TableBody>
                  </ApmeTable>
                  <div className="apme-actions">
                    <Button
                      variant="secondary"
                      isDisabled={!shownOffset}
                      onPress={() => setPageOffset(shownOffset - 25)}
                    >
                      Previous findings
                    </Button>
                    <Button
                      variant="secondary"
                      isDisabled={shownOffset + 25 >= shown.length}
                      onPress={() => setPageOffset(shownOffset + 25)}
                    >
                      Next findings
                    </Button>
                  </div>
                </>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            {pending ? (
              <>
                <Button
                  variant="secondary"
                  isDisabled={mutation.saving}
                  onPress={() => setPending(undefined)}
                >
                  Back to findings
                </Button>
                <Button
                  isDisabled={mutation.saving}
                  onPress={() => void acknowledge()}
                >
                  {mutation.saving ? "Saving…" : "Confirm acknowledgment"}
                </Button>
              </>
            ) : (
              <Button variant="secondary" onPress={close}>
                Close findings
              </Button>
            )}
          </DialogFooter>
        </Dialog>
      )}
    </>
  );
}
