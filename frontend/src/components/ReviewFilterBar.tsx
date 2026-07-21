/**
 * Shared filter pill row for workflow review steps (Assessment, Gate 1/2).
 */

import { Flex, Label } from '@patternfly/react-core';

export type ReviewFilterLabelColor =
  | 'red'
  | 'orange'
  | 'yellow'
  | 'blue'
  | 'grey'
  | 'green'
  | 'teal'
  | 'purple'
  | 'orangered';

export interface ReviewFilterOption {
  id: string;
  label: string;
  count?: number;
  color?: ReviewFilterLabelColor;
  selected: boolean;
  onToggle: () => void;
}

export interface ReviewFilterGroup {
  label: string;
  ariaLabel: string;
  options: ReviewFilterOption[];
}

export interface ReviewFilterBarProps {
  groups: ReviewFilterGroup[];
}

/** Toggle membership; refuse to clear the last selected value (avoids empty lists). */
export function toggleInFilterSet<T>(prev: Set<T>, value: T): Set<T> {
  const next = new Set(prev);
  if (next.has(value)) {
    if (next.size <= 1) return prev;
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

export function ReviewFilterBar({ groups }: ReviewFilterBarProps) {
  const visible = groups.filter((g) => g.options.length > 0);
  if (visible.length === 0) return null;

  return (
    <Flex
      gap={{ default: 'gapMd' }}
      alignItems={{ default: 'alignItemsCenter' }}
      style={{ marginBottom: 12, flexWrap: 'wrap' }}
    >
      {visible.map((group) => (
        <Flex
          key={group.ariaLabel}
          gap={{ default: 'gapSm' }}
          alignItems={{ default: 'alignItemsCenter' }}
        >
          <span style={{ fontSize: 12, opacity: 0.7 }}>{group.label}</span>
          <div
            className="apme-filter-pills"
            role="group"
            aria-label={group.ariaLabel}
          >
            {group.options.map((opt) => (
              <Label
                key={opt.id}
                isCompact
                variant={opt.selected ? 'filled' : 'outline'}
                color={opt.color}
                onClick={opt.onToggle}
              >
                {opt.count != null ? `${opt.label} (${opt.count})` : opt.label}
              </Label>
            ))}
          </div>
        </Flex>
      ))}
    </Flex>
  );
}
