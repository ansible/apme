import { useState } from 'react';
import { Button, Card, CardBody, Badge, Flex } from '@backstage/ui';
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
        <Flex gap="3" align="center">
          <div>
            <Badge size="small" style={{ color: 'var(--bui-fg-success)' }}>
              Rule-based fix
            </Badge>
          </div>
          <div style={{ flex: 1 }}>
            <h3>
              Rule-based fix — {patchCount} fix{patchCount !== 1 ? 'es' : ''}{' '}
              applied
              {formatCount > 0 && `, ${formatCount} formatted`}
            </h3>
          </div>
          <div>
            <Button
              variant="secondary"
              onPress={() => setExpanded(!expanded)}
              aria-expanded={expanded}
              size="small"
            >
              {expanded ? 'Collapse' : 'Show Diffs'}
            </Button>
          </div>
        </Flex>
        {expanded && (
          <div className="apme-tier1-diffs" style={{ marginTop: 16 }}>
            {tier1.patches.map((p, i) => (
              <div key={i} className="apme-diff-block">
                <div className="apme-diff-file">
                  <span className="apme-file-name">{p.file}</span>
                  {p.applied_rules.length > 0 && (
                    <span className="apme-diff-rules">
                      {p.applied_rules.join(', ')}
                    </span>
                  )}
                </div>
                {p.diff && (
                  <DiffView diff={p.diff} className="apme-diff-content" />
                )}
              </div>
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
