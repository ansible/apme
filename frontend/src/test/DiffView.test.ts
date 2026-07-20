import { describe, expect, it } from 'vitest';
import { textsFromUnifiedDiff } from '../components/DiffView';

describe('textsFromUnifiedDiff', () => {
  it('keeps encoded content that looks like file headers after a hunk', () => {
    // Content "-- foo" / "++ bar" encode as "--- foo" / "+++ bar" after the marker.
    const diff = [
      '--- a/play.yml',
      '+++ b/play.yml',
      '@@ -1,3 +1,3 @@',
      '--- foo',
      '+++ bar',
      ' hosts: all',
    ].join('\n');

    const { before, after } = textsFromUnifiedDiff(diff);
    expect(before.split('\n')).toEqual(['-- foo', ' hosts: all']);
    expect(after.split('\n')).toEqual(['++ bar', ' hosts: all']);
  });

  it('skips file headers before the first hunk', () => {
    const diff = [
      '--- a/play.yml',
      '+++ b/play.yml',
      '@@ -1 +1 @@',
      '-old',
      '+new',
    ].join('\n');

    const { before, after } = textsFromUnifiedDiff(diff);
    expect(before).toBe('old');
    expect(after).toBe('new');
  });
});
