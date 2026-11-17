import { useId, type CSSProperties } from 'react';

/**
 * Explicit BUI 0.17.1 exception: this release has no public Spinner or Progress.
 * Use native progress/status semantics and BUI theme tokens until BUI provides
 * these primitives. This is not a PatternFly compatibility component.
 *
 * Import { LoadingIndicator, WorkflowProgress } from './WorkflowProgress' in
 * components, or '../components/WorkflowProgress' in shared presentation files.
 */
export interface LoadingIndicatorProps {
  label?: string;
  size?: 'small' | 'medium';
  className?: string;
  style?: CSSProperties;
}

const visuallyHidden: CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clipPath: 'inset(50%)',
  whiteSpace: 'nowrap',
  border: 0,
};

/** An indeterminate loading status with a screen reader announcement. */
export function LoadingIndicator({
  label = 'Loading',
  size = 'medium',
  className,
  style,
}: LoadingIndicatorProps) {
  return (
    <span
      role="status"
      className={className}
      style={{ display: 'inline-flex', alignItems: 'center', ...style }}
    >
      <progress
        aria-hidden="true"
        style={{
          width: size === 'small' ? 20 : 48,
          height: 8,
          accentColor: 'var(--bui-fg-info)',
          borderRadius: 'var(--bui-radius-2)',
        }}
      />
      <span style={visuallyHidden}>{label}</span>
    </span>
  );
}

export interface WorkflowProgressProps {
  label: string;
  /** Percentage from 0–100. Omit for indeterminate progress. */
  value?: number;
  className?: string;
  style?: CSSProperties;
}

/** A named progress bar; missing or non-finite values are indeterminate. */
export function WorkflowProgress({
  label,
  value,
  className,
  style,
}: WorkflowProgressProps) {
  const labelId = useId();
  const percentage =
    value != null && Number.isFinite(value)
      ? Math.max(0, Math.min(100, value))
      : undefined;

  return (
    <div className={className} style={style}>
      <span id={labelId} style={visuallyHidden}>
        {label}
      </span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <progress
          aria-labelledby={labelId}
          max={100}
          value={percentage}
          style={{
            width: '100%',
            flex: 1,
            accentColor: 'var(--bui-fg-info)',
            borderRadius: 'var(--bui-radius-2)',
          }}
        />
        {percentage != null && (
          <span aria-hidden="true">{Math.round(percentage)}%</span>
        )}
      </div>
    </div>
  );
}
