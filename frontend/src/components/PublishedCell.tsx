import { ExternalLinkAltIcon } from '@patternfly/react-icons';

interface PublishedCellProps {
  pr_url?: string | null;
  branch_name?: string | null;
  commit_sha?: string | null;
}

function shortSha(sha: string): string {
  return sha.length > 8 ? sha.slice(0, 8) : sha;
}

/** Compact SCM publish summary for activity lists (PR link, or branch + SHA). */
export function PublishedCell({ pr_url, branch_name, commit_sha }: PublishedCellProps) {
  if (pr_url) {
    return (
      <a
        href={pr_url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={(e) => e.stopPropagation()}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
      >
        View PR
        <ExternalLinkAltIcon style={{ width: 12, height: 12 }} />
      </a>
    );
  }
  if (branch_name) {
    return (
      <span style={{ fontFamily: 'var(--pf-t--global--font--family--mono)', fontSize: '0.875em' }}>
        {branch_name}
        {commit_sha ? ` @ ${shortSha(commit_sha)}` : ''}
      </span>
    );
  }
  return <span style={{ opacity: 0.3 }}>&mdash;</span>;
}
