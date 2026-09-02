import { describe, expect, it } from 'vitest';
import { toPrFilesDiffUrl } from '../../packages/ui-workflow/src/utils/prDiffUrl';

describe('toPrFilesDiffUrl', () => {
  it('maps GitHub PR URLs to the files tab', () => {
    expect(
      toPrFilesDiffUrl('https://github.com/org/repo/pull/123'),
    ).toBe('https://github.com/org/repo/pull/123/files');
  });

  it('maps GitHub Enterprise PR URLs to the files tab', () => {
    expect(
      toPrFilesDiffUrl('https://github.example.com/org/repo/pull/99'),
    ).toBe('https://github.example.com/org/repo/pull/99/files');
  });

  it('maps GitLab MR URLs to the diffs tab', () => {
    expect(
      toPrFilesDiffUrl('https://gitlab.com/group/project/-/merge_requests/42'),
    ).toBe('https://gitlab.com/group/project/-/merge_requests/42/diffs');
  });

  it('maps Bitbucket Cloud PR URLs to the diff tab', () => {
    expect(
      toPrFilesDiffUrl('https://bitbucket.org/workspace/repo/pull-requests/7'),
    ).toBe('https://bitbucket.org/workspace/repo/pull-requests/7/diff');
  });

  it('maps Bitbucket Server PR URLs to the diff tab', () => {
    expect(
      toPrFilesDiffUrl(
        'https://bitbucket.example.com/projects/PROJ/repos/my-repo/pull-requests/5',
      ),
    ).toBe(
      'https://bitbucket.example.com/projects/PROJ/repos/my-repo/pull-requests/5/diff',
    );
  });

  it('is idempotent when already on a diff view', () => {
    const github =
      'https://github.com/org/repo/pull/123/files';
    const gitlab =
      'https://gitlab.com/group/project/-/merge_requests/42/diffs';
    const bitbucket =
      'https://bitbucket.org/workspace/repo/pull-requests/7/diff';

    expect(toPrFilesDiffUrl(github)).toBe(github);
    expect(toPrFilesDiffUrl(gitlab)).toBe(gitlab);
    expect(toPrFilesDiffUrl(bitbucket)).toBe(bitbucket);
  });

  it('preserves query parameters on GitHub URLs', () => {
    expect(
      toPrFilesDiffUrl('https://github.com/org/repo/pull/1?foo=bar'),
    ).toBe('https://github.com/org/repo/pull/1/files?foo=bar');
  });

  it('returns unrecognized URLs unchanged', () => {
    const unknown = 'https://example.com/pr/1';
    expect(toPrFilesDiffUrl(unknown)).toBe(unknown);
  });

  it('returns empty input unchanged', () => {
    expect(toPrFilesDiffUrl('')).toBe('');
  });
});
