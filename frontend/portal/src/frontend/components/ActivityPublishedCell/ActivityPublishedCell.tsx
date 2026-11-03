/*
 * Copyright Red Hat
 *
 * Compact SCM publish summary for quality activity lists (AAP-90183).
 */

import { Button } from "@patternfly/react-core";
import { ExternalLinkAltIcon } from "@patternfly/react-icons";
import { toPrFilesDiffUrl } from "@apme/ui-workflow";

export interface ActivityPublishedCellProps {
  pr_url?: string | null;
  branch_name?: string | null;
  commit_sha?: string | null;
}

function shortSha(sha: string): string {
  return sha.length > 8 ? sha.slice(0, 8) : sha;
}

/** PR link, or branch + short SHA, or em dash when nothing was published. */
export function ActivityPublishedCell({
  pr_url,
  branch_name,
  commit_sha,
}: Readonly<ActivityPublishedCellProps>) {
  if (pr_url) {
    return (
      <Button
        variant="link"
        isInline
        component="a"
        href={toPrFilesDiffUrl(pr_url)}
        target="_blank"
        rel="noopener noreferrer"
        icon={<ExternalLinkAltIcon />}
        iconPosition="end"
        size="sm"
        onClick={(e) => e.stopPropagation()}
      >
        View PR
      </Button>
    );
  }
  if (branch_name) {
    return (
      <span
        style={{
          fontFamily: "var(--pf-t--global--font--family--mono)",
          fontSize: "0.875em",
        }}
      >
        {branch_name}
        {commit_sha ? ` @ ${shortSha(commit_sha)}` : ""}
      </span>
    );
  }
  return <span style={{ opacity: 0.3 }}>&mdash;</span>;
}
