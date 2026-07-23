import { useState } from 'react';
import {
  Button,
  Card,
  CardBody,
  Label,
  Split,
  SplitItem,
} from '@patternfly/react-core';
import type { Tier1Result } from '../hooks/useSessionStream';
import { DiffView } from './DiffView';

export interface Tier1ResultsPanelProps {
  tier1: Tier1Result;
}

export function Tier1ResultsPanel({ tier1 }: Tier1ResultsPanelProps) {
  const patchCount = tier1.patches.length;
  const formatCount = tier1.format_diffs.length;
  const [expanded, setExpanded] = useState(false);

  if (patchCount === 0 && formatCount === 0) return null;

  return (
    <Card style={{ marginBottom: 16 }}>
      <CardBody>
        <Split hasGutter>
          <SplitItem>
            <Label color="green" isCompact>Auto-Fix</Label>
          </SplitItem>
          <SplitItem isFilled>
            <h3>
              Quick-fix — {patchCount} fix{patchCount !== 1 ? 'es' : ''} applied
              {formatCount > 0 && `, ${formatCount} formatted`}
            </h3>
          </SplitItem>
          <SplitItem>
            <Button variant="secondary" onClick={() => setExpanded(!expanded)} size="sm">
              {expanded ? 'Collapse' : 'Show Diffs'}
            </Button>
          </SplitItem>
        </Split>
        {expanded && (
          <div className="apme-tier1-diffs" style={{ marginTop: 16 }}>
            {tier1.patches.map((p, i) => (
              <div key={i} className="apme-diff-block">
                <div className="apme-diff-file">
                  <span className="apme-file-name">{p.file}</span>
                  {p.applied_rules.length > 0 && (
                    <span className="apme-diff-rules">{p.applied_rules.join(', ')}</span>
                  )}
                </div>
                {p.diff && <DiffView diff={p.diff} className="apme-diff-content" />}
              </div>
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
