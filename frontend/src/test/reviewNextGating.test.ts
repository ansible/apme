import { describe, expect, it } from 'vitest';
import {
  applyImplicitDeclinesToMap,
  hiddenUndecidedCount,
} from '@apme/ui-workflow';

describe('reviewNextGating', () => {
  it('hiddenUndecidedCount subtracts visible pending', () => {
    expect(hiddenUndecidedCount(12, 3)).toBe(9);
    expect(hiddenUndecidedCount(3, 3)).toBe(0);
  });

  it('applyImplicitDeclinesToMap marks pending keys declined', () => {
    const map = new Map([
      ['a', 'accepted' as const],
      ['b', 'pending' as const],
    ]);
    const next = applyImplicitDeclinesToMap(map, ['b', 'c']);
    expect(next.get('a')).toBe('accepted');
    expect(next.get('b')).toBe('declined');
  });
});
