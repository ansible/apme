/**
 * Shared filter pill row for workflow review steps (Assessment, Gate 1/2).
 */

import { Flex, ToggleButton } from '@backstage/ui';
import type { CSSProperties } from 'react';

export interface ReviewFilterOption {
  id: string;
  label: string;
  count?: number;
  color?: string;
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
    <Flex gap="4" align="center" style={{ marginBottom: 12, flexWrap: 'wrap' }}>
      {visible.map((group) => (
        <Flex key={group.ariaLabel} gap="2" align="center">
          <span style={{ fontSize: 12, opacity: 0.7 }}>{group.label}</span>
          <div
            className="apme-filter-pills"
            role="group"
            aria-label={group.ariaLabel}
          >
            {group.options.map((opt) => (
              <ToggleButton
                key={opt.id}
                size="small"
                isSelected={opt.selected}
                onChange={opt.onToggle}
                style={
                  opt.color
                    ? ({
                        color: opt.color,
                        '--bui-bg-solid': `color-mix(in srgb, ${opt.color} 18%, var(--bui-bg-neutral-1))`,
                      } as CSSProperties)
                    : undefined
                }
              >
                {opt.count != null ? `${opt.label} (${opt.count})` : opt.label}
              </ToggleButton>
            ))}
          </div>
        </Flex>
      ))}
    </Flex>
  );
}
