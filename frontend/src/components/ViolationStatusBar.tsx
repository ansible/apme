import { Badge, Flex, FlexItem, Label, Split, SplitItem } from '@patternfly/react-core';
import { severityClass, SEV_CSS_VAR } from './severity';

export { severityClass, SEV_CSS_VAR };

export interface ViolationStatusBarDetail {
  project_path: string;
  total_violations: number;
  fixable: number;
  ai_candidate: number;
  manual_review: number;
  remediated_count: number;
  scan_type: string;
  violations: { file: string }[];
}

interface ViolationStatusBarProps {
  detail: ViolationStatusBarDetail;
}

function Count({ label, count }: { label: string; count?: number }) {
  if (!count) return null;
  return (
    <FlexItem>
      {label} <Badge isRead>{count}</Badge>
    </FlexItem>
  );
}

export function ViolationStatusBar({ detail }: ViolationStatusBarProps) {
  const isRemediate = detail.scan_type === 'fix' || detail.scan_type === 'remediate';
  return (
    <Split hasGutter className="apme-status-bar">
      <SplitItem isFilled>
        <Flex alignItems={{ default: 'alignItemsCenter' }}>
          <FlexItem>
            <Label
              color={detail.total_violations > 0 ? 'red' : 'green'}
              isCompact={false}
              style={{ fontSize: 14, fontWeight: 600 }}
            >
              {detail.project_path}
            </Label>
          </FlexItem>
          <FlexItem>
            <Label
              color={detail.total_violations > 0 ? 'red' : 'green'}
              variant="outline"
            >
              {detail.total_violations > 0 ? `${detail.total_violations} Violations` : 'Clean'}
            </Label>
          </FlexItem>
        </Flex>
      </SplitItem>
      <SplitItem>
        <Flex>
          <Count label="Violations" count={detail.total_violations} />
          {!isRemediate && <Count label="Fixable" count={detail.fixable} />}
          <Count label="AI" count={detail.ai_candidate} />
          <Count label="Manual" count={detail.manual_review} />
          {isRemediate && <Count label="Remediated" count={detail.remediated_count} />}
          <Count label="Files" count={new Set(detail.violations.map(v => v.file)).size || undefined} />
        </Flex>
      </SplitItem>
    </Split>
  );
}
