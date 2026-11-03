/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import type { ViolationDetail } from "@apme/client";

export type DependencyKind = "collection" | "python";
export function isDependencyFinding(finding: ViolationDetail) {
  return (
    finding.validator_source === "collection_health" ||
    finding.validator_source === "dep_audit"
  );
}
export function findingsForDependency(
  findings: readonly ViolationDetail[],
  kind: DependencyKind,
  name: string,
) {
  if (kind === "python") {
    const canonical = (value: string) =>
      value.toLowerCase().replace(/[-_.]+/g, "-");
    return findings.filter(
      (finding) =>
        finding.validator_source === "dep_audit" &&
        canonical(/^([A-Za-z0-9_.-]+)==/.exec(finding.message)?.[1] ?? "") ===
          canonical(name),
    );
  }
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const boundary = new RegExp(
    `(?:^|[^a-z0-9_.-])${escaped}(?:$|[^a-z0-9_.-])`,
    "i",
  );
  return findings.filter(
    (finding) =>
      finding.validator_source === "collection_health" &&
      (boundary.test(finding.path) ||
        boundary.test(finding.file) ||
        boundary.test(finding.message)),
  );
}
