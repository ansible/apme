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
  /**
   * Host-supplied rule definition URL. When it returns a string, the rule chip
   * navigates in the same tab. When it returns undefined, render plain text.
   */
  resolveRuleHref?: (bareId: string) => string | undefined;
}

function SingleRuleId({
  ruleId,
  className,
  onHoverChange,
  onRuleClick,
  resolveRuleHref,
}: {
  ruleId: string;
  className?: string;
  onHoverChange?: (hovering: boolean) => void;
  onRuleClick?: (bareId: string) => void;
  resolveRuleHref?: (bareId: string) => string | undefined;
}) {
  const bare = bareRuleId(ruleId);
  const href = resolveRuleHref?.(bare);
  const filterClickable = href == null && onRuleClick != null;
  const hoverable = onHoverChange != null;
  const interactive = href != null || filterClickable || hoverable;
  const spanClassName = [
    className ?? 'apme-rule-id',
    interactive ? 'apme-rule-id-hoverable' : '',
    href != null ? 'apme-rule-id-link' : '',
    filterClickable ? 'apme-rule-id-clickable' : '',
  ]
    .filter(Boolean)
    .join(' ');

  const hoverHandlers = hoverable
    ? {
        onMouseEnter: () => onHoverChange!(true),
        onMouseLeave: () => onHoverChange!(false),
        onFocus: () => onHoverChange!(true),
        onBlur: () => onHoverChange!(false),
      }
    : undefined;

  if (href) {
    return (
      <a
        href={href}
        className={spanClassName}
        aria-label={`View rule definition: ${bare}`}
        {...hoverHandlers}
        onClick={(e) => e.stopPropagation()}
      >
        {bare}
      </a>
    );
  }

  if (filterClickable) {
    return (
      <button
        type="button"
        className={spanClassName}
        title={`Toggle filter: ${bare}`}
        {...hoverHandlers}
        onClick={(e) => {
          e.stopPropagation();
          onRuleClick(bare);
        }}
      >
        {bare}
      </button>
    );
  }

  return (
    <span
      className={spanClassName}
      tabIndex={hoverable ? 0 : undefined}
      {...hoverHandlers}
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
  resolveRuleHref,
}: RuleIdProps) {
  const ids = ruleId.split(',').map((s) => s.trim()).filter(Boolean);
  if (ids.length <= 1) {
    return (
      <SingleRuleId
        ruleId={ruleId}
        className={className}
        onHoverChange={onHoverChange}
        onRuleClick={onRuleClick}
        resolveRuleHref={resolveRuleHref}
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
            resolveRuleHref={resolveRuleHref}
          />
        </span>
      ))}
    </>
  );
}
