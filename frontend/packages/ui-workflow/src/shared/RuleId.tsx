import { bareRuleId } from './severity';

export interface RuleIdProps {
  ruleId: string;
  className?: string;
  /** When set, hover/focus reports true/false (e.g. YAML line highlight). */
  onHoverChange?: (hovering: boolean) => void;
  /**
   * When set, each bare rule chip is clickable (e.g. add to rule filter).
   * Receives the bare rule ID that was clicked.
   */
  onRuleClick?: (bareId: string) => void;
}

function SingleRuleId({
  ruleId,
  className,
  onHoverChange,
  onRuleClick,
}: {
  ruleId: string;
  className?: string;
  onHoverChange?: (hovering: boolean) => void;
  onRuleClick?: (bareId: string) => void;
}) {
  const bare = bareRuleId(ruleId);
  const clickable = onRuleClick != null;
  const hoverable = onHoverChange != null;
  const interactive = clickable || hoverable;
  const spanClassName = [
    className ?? 'apme-rule-id',
    interactive ? 'apme-rule-id-hoverable' : '',
    clickable ? 'apme-rule-id-clickable' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <span
      className={spanClassName}
      tabIndex={interactive ? 0 : undefined}
      title={clickable ? `Toggle filter: ${bare}` : undefined}
      role={clickable ? 'button' : undefined}
      onMouseEnter={hoverable ? () => onHoverChange(true) : undefined}
      onMouseLeave={hoverable ? () => onHoverChange(false) : undefined}
      onFocus={hoverable ? () => onHoverChange(true) : undefined}
      onBlur={hoverable ? () => onHoverChange(false) : undefined}
      onClick={
        clickable
          ? (e) => {
              e.stopPropagation();
              onRuleClick(bare);
            }
          : undefined
      }
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                e.stopPropagation();
                onRuleClick(bare);
              }
            }
          : undefined
      }
    >
      {bare}
    </span>
  );
}

export function RuleId({
  ruleId,
  className,
  onHoverChange,
  onRuleClick,
}: RuleIdProps) {
  const ids = ruleId.split(',').map((s) => s.trim()).filter(Boolean);
  if (ids.length <= 1) {
    return (
      <SingleRuleId
        ruleId={ruleId}
        className={className}
        onHoverChange={onHoverChange}
        onRuleClick={onRuleClick}
      />
    );
  }
  return (
    <>
      {ids.map((id, i) => (
        <span key={`${id}-${i}`}>
          {i > 0 && ','}
          <SingleRuleId
            ruleId={id}
            className={className}
            onHoverChange={onHoverChange}
            onRuleClick={onRuleClick}
          />
        </span>
      ))}
    </>
  );
}
