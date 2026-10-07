import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { createElement } from 'react';
import {
  CurrentYamlView,
  DiffView,
  hasUnresolvedProposalEscapes,
  resolveProposalYamlText,
  textsFromUnifiedDiff,
} from '../../packages/ui-workflow/src/components/DiffView';

function unifiedDiff(before: string, after: string): string {
  const beforeLines = before.split('\n');
  const afterLines = after.split('\n');
  return [
    `@@ -1,${beforeLines.length} +1,${afterLines.length} @@`,
    ...beforeLines.map((line) => `-${line}`),
    ...afterLines.map((line) => `+${line}`),
  ].join('\n');
}

describe('resolveProposalYamlText', () => {
  it('recovers escaped YAML only when the complete diff confirms the source', () => {
    const source = '- name: foo\n  ansible.builtin.debug:\n    msg: hi';
    const flat = '- name: foo\\n  ansible.builtin.debug:\\n    msg: hi';
    const after = source.replace('msg: hi', 'msg: hello');
    const diff = unifiedDiff(source, after);

    expect(resolveProposalYamlText(flat, diff, 'before', after)).toBe(source);
    expect(hasUnresolvedProposalEscapes(flat, diff, after)).toBe(false);
  });

  it('recovers repeated escaped line breaks and indentation only with a full diff', () => {
    const source = 'name: x\n\t\tdebug: {}';
    const flat = 'name: x\\\\n\\\\t\\\\tdebug: {}';
    const after = source.replace('{}', 'msg: hi');
    const diff = unifiedDiff(source, after);

    expect(resolveProposalYamlText(flat, diff, 'before', after)).toBe(source);
  });

  it('matches unified-diff line counts when the source ends in a newline', () => {
    const flat = 'name: old\\ndebug: true\\n';
    const diff = [
      '@@ -1,2 +1,2 @@',
      '-name: old',
      '-debug: true',
      '+name: new',
      '+debug: false',
      '',
    ].join('\n');
    const after = 'name: new\ndebug: false\n';

    expect(resolveProposalYamlText(flat, diff, 'before', after)).toBe(
      'name: old\ndebug: true\n',
    );
  });

  it('recovers deletion-only proposals whose proposed side has zero lines', () => {
    const flat = 'name: old\\ndebug: true';
    const before = 'name: old\ndebug: true';
    const diff = [
      '@@ -1,2 +0,0 @@',
      '-name: old',
      '-debug: true',
    ].join('\n');

    expect(resolveProposalYamlText(flat, diff, 'before', '')).toBe(before);
    expect(hasUnresolvedProposalEscapes(flat, diff, '')).toBe(false);
  });

  it('does not treat a context-limited diff hunk as the complete source', () => {
    const flat = 'name: old\n  literal\\ndebug: true';
    const partialDiff = [
      '@@ -2 +2 @@',
      '-  literal',
      '+  replacement',
    ].join('\n');

    expect(resolveProposalYamlText(flat, partialDiff, 'before')).toBe(flat);
    expect(hasUnresolvedProposalEscapes(flat, partialDiff)).toBe(true);

    const escapedTab = 'value: a\\tb';
    const tabSourceDiff = ['@@ -2 +2 @@', '-value: a\tb', '+value: c'].join('\n');
    expect(
      resolveProposalYamlText(escapedTab, tabSourceDiff, 'before', 'value: c'),
    ).toBe(escapedTab);
  });

  it('rejects a hunk that matches before_text but omits lines from after_text', () => {
    const flat = 'old\\nkey: value';
    const after = 'new\nkey: value\nunchanged: true';
    const diff = unifiedDiff('old\nkey: value', 'new\nkey: value');

    expect(resolveProposalYamlText(flat, diff, 'before', after)).toBe(flat);
    expect(hasUnresolvedProposalEscapes(flat, diff, after)).toBe(true);
  });

  it('preserves valid path, comment, and quoted-scalar escapes when the diff confirms them', () => {
    const cases = [
      'path: C:\\new:archive',
      'name: foo # example \\ndebug: false',
      'msg: "a\\nb"',
      "msg: &m 'a\\ndebug: false'",
      'msg: !!str "a\\ndebug: false"',
    ];

    for (const source of cases) {
      const diff = unifiedDiff(source, `${source} `);
      expect(resolveProposalYamlText(source, diff, 'before', `${source} `)).toBe(source);
      expect(hasUnresolvedProposalEscapes(source, diff, `${source} `)).toBe(false);
    }
  });

  it('keeps the warning when a partial hunk does not cover an escape-bearing line', () => {
    const before = 'first: true\nmsg: "a\\nb"\nlast: old';
    const after = 'first: true\nmsg: "a\\nb"\nlast: new';
    const diff = ['@@ -3 +3 @@', '-last: old', '+last: new'].join('\n');

    expect(hasUnresolvedProposalEscapes(before, diff, after)).toBe(true);
    expect(resolveProposalYamlText(before, diff, 'before', after)).toBe(before);

    const escapeLineDiff = [
      '@@ -2,2 +2,2 @@',
      ' msg: "a\\nb"',
      '-last: old',
      '+last: new',
    ].join('\n');
    expect(hasUnresolvedProposalEscapes(before, escapeLineDiff, after)).toBe(false);
  });

  it('preserves block-scalar content when literal and structural escapes are ambiguous', () => {
    const source = "script: |\n  printf 'hello\\n  world'\ndebug: false";
    const flat = "script: |\\n  printf 'hello\\n  world'\\ndebug: false";
    const after = source.replace('debug: false', 'debug: true');
    const diff = unifiedDiff(source, after);

    expect(resolveProposalYamlText(flat, diff, 'before', after)).toBe(flat);
    expect(hasUnresolvedProposalEscapes(flat, diff, after)).toBe(true);
  });

  it('reconstructs partially normalized text only when the diff is complete', () => {
    const source = 'name: old\n  literal\ndebug: true';
    const partial = 'name: old\n  literal\\ndebug: true';
    const after = source.replace('old', 'new');
    const diff = unifiedDiff(source, after);

    expect(resolveProposalYamlText(partial, diff, 'before', after)).toBe(source);
  });

  it('does not decode the Proposed pane from the Current diff', () => {
    const proposed = 'msg: "a\\nb"';
    expect(resolveProposalYamlText(proposed, unifiedDiff('old', 'new'), 'after')).toBe(
      proposed,
    );
  });

  it('does not recover escaped text without a diff', () => {
    const flat = '--- \\n- name: foo';
    expect(resolveProposalYamlText(flat)).toBe(flat);
    expect(hasUnresolvedProposalEscapes(flat)).toBe(true);
  });

  it('warns when proposal source is unverified while keeping panes aligned', () => {
    const text = 'msg: "a\\nb"';
    const { container } = render(createElement(CurrentYamlView, { text }));

    expect(screen.queryByRole('status')).toBeNull();
    expect(container.querySelector('.apme-diff-content')?.textContent).toContain(text);

    const proposal = render(
      createElement(DiffView, {
        before: 'name: old\\nkey: value',
        after: 'name: new\nkey: value\nunchanged: true',
        diff: unifiedDiff('name: old\nkey: value', 'name: new\nkey: value'),
      }),
    );
    const root = proposal.container.querySelector('.apme-side-by-side');
    expect(root?.firstElementChild?.classList.contains('apme-diff-warning')).toBe(true);
    expect(root?.querySelectorAll('.apme-diff-pane')).toHaveLength(2);
    expect(screen.getByRole('status').textContent).toContain('shown as received');
  });

  it('shows an explicitly partial unified diff when either complete source is absent', () => {
    const { container } = render(
      createElement(DiffView, {
        before: 'name: old\\nkey: value',
        diff: ['@@ -2 +2 @@', '-key: value', '+key: newer'].join('\n'),
      }),
    );

    expect(container.querySelector('.apme-side-by-side')).toBeNull();
    expect(screen.getByRole('status').textContent).toContain(
      'unchanged source lines may be omitted',
    );
    expect(container.querySelector('pre')?.textContent).toContain('@@ -2 +2 @@');
  });

  it('allows callers with a known complete unified diff to suppress the context warning', () => {
    const { container } = render(
      createElement(DiffView, {
        diff: ['@@ -1 +1 @@', '-old', '+new'].join('\n'),
        partialSourceWarning: false,
      }),
    );

    expect(container.querySelector('.apme-diff-warning')).toBeNull();
    expect(container.querySelector('pre')?.textContent).toContain('-old');
  });

  it('warns for context-limited unified mode but not a verified full diff', () => {
    const partial = render(
      createElement(DiffView, {
        mode: 'unified',
        diff: ['@@ -2 +2 @@', '-old', '+new'].join('\n'),
      }),
    );
    expect(screen.getByRole('status').textContent).toContain('unchanged source lines may be omitted');
    partial.unmount();

    const before = 'old';
    const after = 'new';
    render(
      createElement(DiffView, {
        mode: 'unified',
        before,
        after,
        diff: unifiedDiff(before, after),
      }),
    );
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('treats empty API text as missing unless the diff verifies an empty side', () => {
    const replacement = ['@@ -1 +1 @@', '-old', '+new'].join('\n');
    const emptyPayload = render(
      createElement(DiffView, { before: '', after: '', diff: replacement }),
    );
    expect(emptyPayload.container.querySelector('.apme-side-by-side')).toBeNull();
    expect(emptyPayload.container.querySelector('pre')?.textContent).toContain('-old');
    emptyPayload.unmount();

    const deletionDiff = ['@@ -1 +0,0 @@', '-old'].join('\n');
    const deletion = render(
      createElement(DiffView, { before: 'old', after: '', diff: deletionDiff }),
    );
    expect(deletion.container.querySelectorAll('.apme-diff-pane')).toHaveLength(2);
    expect(deletion.container.querySelector('.apme-diff-remove')?.textContent).toContain('old');
  });

  it('does not infer a complete empty side from a partial zero-line hunk', () => {
    const insertion = render(
      createElement(DiffView, {
        before: '',
        after: 'new\nexisting',
        diff: ['@@ -0,0 +1 @@', '+new'].join('\n'),
      }),
    );
    expect(insertion.container.querySelector('.apme-side-by-side')).toBeNull();
    expect(screen.getByRole('status').textContent).toContain('unchanged source lines may be omitted');
    insertion.unmount();

    const deletion = render(
      createElement(DiffView, {
        before: 'old\nexisting',
        after: '',
        diff: ['@@ -1 +0,0 @@', '-old'].join('\n'),
      }),
    );
    expect(deletion.container.querySelector('.apme-side-by-side')).toBeNull();
    expect(screen.getByRole('status').textContent).toContain('unchanged source lines may be omitted');
  });

  it('handles long unmatched backslash runs without changing their content', () => {
    const text = `msg: ${'\\'.repeat(100_000)}x`;
    expect(resolveProposalYamlText(text)).toBe(text);
    expect(hasUnresolvedProposalEscapes(text)).toBe(false);
  });
});

describe('textsFromUnifiedDiff', () => {
  it('keeps encoded content that looks like file headers after a hunk', () => {
    const diff = [
      '--- a/play.yml',
      '+++ b/play.yml',
      '@@ -1,3 +1,3 @@',
      '--- foo',
      '+++ bar',
      ' hosts: all',
    ].join('\n');

    const { before, after } = textsFromUnifiedDiff(diff);
    expect(before.split('\n')).toEqual(['-- foo', 'hosts: all']);
    expect(after.split('\n')).toEqual(['++ bar', 'hosts: all']);
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
