import { useEffect, useRef } from 'react';
import {
  Button,
  Card,
  CardBody,
  Icon,
  Label,
  Progress,
  Split,
  SplitItem,
} from '@patternfly/react-core';
import { ExclamationTriangleIcon, ExclamationCircleIcon } from '@patternfly/react-icons';
import type { OperationProgress, OperationStatus } from '../types/operation';

export interface OperationProgressPanelProps {
  status: OperationStatus;
  progress: OperationProgress[];
  onCancel: () => void;
}

const STATUS_LABELS: Partial<Record<OperationStatus, string>> = {
  connecting: 'Connecting...',
  preparing: 'Preparing...',
  cloning: 'Cloning repository...',
  checking: 'Checking...',
  applying: 'Applying approved fixes...',
};

const PHASE_WEIGHTS: Record<string, { start: number; end: number }> = {
  cloning:    { start: 0,  end: 5 },
  format:     { start: 5,  end: 15 },
  tier1:      { start: 15, end: 90 },
  ai:         { start: 15, end: 90 },
};

function entryProgress(entry: OperationProgress): number | null {
  const weight = PHASE_WEIGHTS[entry.phase];
  if (!weight) return null;

  if (entry.progress != null && entry.progress > 0 && entry.progress <= 1) {
    return weight.start + (weight.end - weight.start) * entry.progress;
  }

  const passMatch = entry.message.match(/Pass (\d+)\/(\d+)/);
  if (passMatch) {
    const current = parseInt(passMatch[1]!, 10);
    const total = parseInt(passMatch[2]!, 10);
    return weight.start + (weight.end - weight.start) * Math.min(current / total, 1);
  }

  if (/[Cc]onverged|[Ff]ully converged|Final scan/.test(entry.message)) {
    return weight.end;
  }

  return weight.start + 1;
}

export function OperationProgressPanel({
  status,
  progress,
  onCancel,
}: OperationProgressPanelProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const highWaterRef = useRef(0);

  if (status === 'connecting' || status === 'preparing') {
    highWaterRef.current = 0;
  }

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [progress.length]);

  const label = STATUS_LABELS[status] ?? 'Processing...';

  let pct: number | undefined;
  if (status === 'complete') {
    pct = 100;
    highWaterRef.current = 0;
  } else if (status === 'error') {
    pct = undefined;
  } else if (status === 'cloning') {
    pct = 2;
    highWaterRef.current = 2;
  } else {
    for (const entry of progress) {
      const val = entryProgress(entry);
      if (val != null && val > highWaterRef.current) {
        highWaterRef.current = val;
      }
    }
    pct = Math.max(highWaterRef.current, 1);
    if (pct >= 100) pct = 99;
  }

  return (
    <Card style={{ marginBottom: 16 }}>
      <CardBody>
        <Split hasGutter>
          <SplitItem isFilled><h2>{label}</h2></SplitItem>
          <SplitItem><Button variant="secondary" onClick={onCancel}>Cancel</Button></SplitItem>
        </Split>
        <Progress
          value={pct}
          measureLocation={pct != null ? 'outside' : 'none'}
          style={{ marginTop: 16 }}
        />
        <div className="apme-timeline" style={{ marginTop: 16, maxHeight: 200, overflowY: 'auto' }}>
          {progress.map((entry, i) => {
            const level = entry.level ?? 2;
            const labelColor = level >= 4 ? 'red' : level >= 3 ? 'orange' : undefined;
            const msgStyle: React.CSSProperties =
              level >= 4 ? { color: 'var(--pf-t--global--color--status--danger--default)' }
              : level >= 3 ? { color: 'var(--pf-t--global--color--status--warning--default)' }
              : level <= 1 ? { opacity: 0.6 } : {};
            const icon =
              level >= 4 ? <Icon status="danger"><ExclamationCircleIcon /></Icon>
              : level >= 3 ? <Icon status="warning"><ExclamationTriangleIcon /></Icon>
              : null;
            return (
              <div key={i} className="apme-timeline-entry">
                <Label isCompact color={labelColor}>{entry.phase}</Label>
                {icon && <span style={{ marginLeft: 4 }}>{icon}</span>}
                <span style={{ marginLeft: 8, ...msgStyle }}>{entry.message}</span>
              </div>
            );
          })}
          <div ref={endRef} />
        </div>
      </CardBody>
    </Card>
  );
}
