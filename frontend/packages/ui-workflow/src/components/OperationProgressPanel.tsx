import { useEffect, useRef } from 'react';
import { Button, Card, CardBody, Badge, Flex } from '@backstage/ui';
import { RiAlertLine, RiErrorWarningLine } from '@remixicon/react';
import { WorkflowProgress } from './WorkflowProgress';
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
  cloning: { start: 0, end: 5 },
  format: { start: 5, end: 15 },
  tier1: { start: 15, end: 90 },
  ai: { start: 15, end: 90 },
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
    return (
      weight.start + (weight.end - weight.start) * Math.min(current / total, 1)
    );
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
        <Flex gap="3" align="center">
          <div style={{ flex: 1 }}>
            <h2>{label}</h2>
          </div>
          <div>
            <Button variant="secondary" onPress={onCancel}>
              Cancel
            </Button>
          </div>
        </Flex>
        <WorkflowProgress value={pct} label={label} style={{ marginTop: 16 }} />
        <div
          className="apme-timeline"
          style={{ marginTop: 16, maxHeight: 200, overflowY: 'auto' }}
        >
          {progress.map((entry, i) => {
            const level = entry.level ?? 2;
            const labelColor =
              level >= 4
                ? 'var(--bui-fg-danger)'
                : level >= 3
                  ? 'var(--bui-fg-warning)'
                  : undefined;
            const msgStyle: React.CSSProperties =
              level >= 4
                ? { color: 'var(--bui-fg-danger)' }
                : level >= 3
                  ? { color: 'var(--bui-fg-warning)' }
                  : level <= 1
                    ? { opacity: 0.6 }
                    : {};
            const icon =
              level >= 4 ? (
                <RiErrorWarningLine
                  size={16}
                  aria-label="Error"
                  style={{ color: 'var(--bui-fg-danger)' }}
                />
              ) : level >= 3 ? (
                <RiAlertLine
                  size={16}
                  aria-label="Warning"
                  style={{ color: 'var(--bui-fg-warning)' }}
                />
              ) : null;
            return (
              <div key={i} className="apme-timeline-entry">
                <Badge size="small" style={{ color: labelColor }}>
                  {entry.phase}
                </Badge>
                {icon && <span style={{ marginLeft: 4 }}>{icon}</span>}
                <span style={{ marginLeft: 8, ...msgStyle }}>
                  {entry.message}
                </span>
              </div>
            );
          })}
          <div ref={endRef} />
        </div>
      </CardBody>
    </Card>
  );
}
