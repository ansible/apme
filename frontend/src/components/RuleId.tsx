import { bareRuleId } from './severity';

export interface RuleIdProps {
  ruleId: string;
  className?: string;
  /** When set, hover/focus reports true/false (e.g. YAML line highlight). */
  onHoverChange?: (hovering: boolean) => void;
}

function SingleRuleId({
  ruleId,
  className,
  onHoverChange,
}: {
  ruleId: string;
  className?: string;
  onHoverChange?: (hovering: boolean) => void;
}) {
  const bare = bareRuleId(ruleId);
  const interactive = onHoverChange != null;
  const spanClassName = [
    className ?? 'apme-rule-id',
    interactive ? 'apme-rule-id-hoverable' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <span
      className={spanClassName}
      tabIndex={interactive ? 0 : undefined}
      onMouseEnter={interactive ? () => onHoverChange(true) : undefined}
      onMouseLeave={interactive ? () => onHoverChange(false) : undefined}
      onFocus={interactive ? () => onHoverChange(true) : undefined}
      onBlur={interactive ? () => onHoverChange(false) : undefined}
    >
      {bare}
    </span>
  );
}

export function RuleId({ ruleId, className, onHoverChange }: RuleIdProps) {
  const ids = ruleId.split(',').map((s) => s.trim()).filter(Boolean);
  if (ids.length <= 1) {
    return (
      <SingleRuleId
        ruleId={ruleId}
        className={className}
        onHoverChange={onHoverChange}
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
          />
        </span>
      ))}
    </>
  );
}
