import type React from 'react';
import { useEffect, useMemo, useRef } from 'react';
import { YamlLine } from './yamlHighlight';

export interface DiffViewProps {
  /** Unified diff (used when full before/after text is absent, or for unified mode). */
  diff?: string;
  before?: string;
  after?: string;
  /** Default side-by-side when complete before/after text is available. */
  mode?: 'unified' | 'side-by-side';
  className?: string;
  /** 1-based line in the Current (before) pane to highlight. */
  highlightLine?: number | null;
  /** Whether to warn that a unified diff may omit unchanged source lines. */
  partialSourceWarning?: boolean;
}

/**
 * Classify a unified-diff line. File headers (`--- `/`+++ `) are only valid
 * before the first hunk; after `@@`, those prefixes are encoded content
 * (e.g. removed `-- foo` → `--- foo`).
 */
function classifyLine(
  line: string,
  seenHunk: boolean,
): 'add' | 'remove' | 'header' | 'context' {
  if (line.startsWith('@@') || line.startsWith('\\ No newline')) {
    return 'header';
  }
  if (!seenHunk && (line.startsWith('--- ') || line.startsWith('+++ '))) {
    return 'header';
  }
  if (line.startsWith('+')) return 'add';
  if (line.startsWith('-')) return 'remove';
  return 'context';
}

const lineStyles: Record<string, React.CSSProperties> = {
  add: { backgroundColor: 'rgba(46, 160, 67, 0.15)', color: 'inherit' },
  remove: { backgroundColor: 'rgba(248, 81, 73, 0.15)', color: 'inherit' },
  header: { color: 'var(--pf-t--global--color--status--info--default)', fontWeight: 600 },
  context: {},
};

function escapedTokenEnd(text: string, start: number): number | undefined {
  let cursor = start;
  while (text[cursor] === '\\') cursor++;
  if (cursor === start) return undefined;
  if (text[cursor] === 'n' || text[cursor] === 't') return cursor + 1;
  if (text[cursor] !== 'r') return undefined;

  cursor++;
  const newlineSlashStart = cursor;
  while (text[cursor] === '\\') cursor++;
  return cursor > newlineSlashStart && text[cursor] === 'n' ? cursor + 1 : undefined;
}

/** Decode candidate transport escapes; callers must verify against full source. */
function decodeEscapedProposalText(text: string): string {
  let out = '';
  for (let cursor = 0; cursor < text.length; ) {
    const end = text[cursor] === '\\' ? escapedTokenEnd(text, cursor) : undefined;
    if (end === undefined) {
      if (text[cursor] === '\\') {
        let runEnd = cursor + 1;
        while (text[runEnd] === '\\') runEnd++;
        out += text.slice(cursor, runEnd);
        cursor = runEnd;
      } else {
        out += text[cursor]!;
        cursor++;
      }
      continue;
    }
    out += text[end - 1] === 't' ? '\t' : '\n';
    cursor = end;
  }
  return out;
}

function normalizeLineEndings(text: string): string {
  return text.replace(/\r\n/g, '\n');
}

function sourceLineCount(text: string): number {
  if (!text) return 0;
  return text.replace(/\n$/, '').split('\n').length;
}

function diffCoversCompleteSide(
  diff: string,
  side: 'before' | 'after',
  expectedLineCount: number,
): boolean {
  const hunkPattern = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/gm;
  const startIndex = side === 'before' ? 1 : 3;
  const countIndex = side === 'before' ? 2 : 4;
  let nextLine = 1;
  let totalLines = 0;
  let foundHunk = false;
  for (const match of diff.matchAll(hunkPattern)) {
    const startLine = Number(match[startIndex]);
    const lineCount = Number(match[countIndex] ?? 1);
    const expectedStart = lineCount === 0 ? nextLine - 1 : nextLine;
    if (startLine !== expectedStart) return false;
    foundHunk = true;
    totalLines += lineCount;
    if (lineCount > 0) nextLine = startLine + lineCount;
  }
  return foundHunk && totalLines === expectedLineCount;
}

function sourceLines(text: string): string[] {
  if (!text) return [];
  const lines = normalizeLineEndings(text).split('\n');
  if (text.endsWith('\n')) lines.pop();
  return lines;
}

/** Check that every escape-bearing source line is present and unchanged in the diff. */
function diffVerifiesEscapeLines(
  beforeText: string,
  afterText: string | undefined,
  diff: string | undefined,
): boolean {
  if (!diff?.trim()) return false;
  const beforeLines = sourceLines(beforeText);
  const afterLines = afterText === undefined ? undefined : sourceLines(afterText);
  const escapeLines = new Set<number>();
  beforeLines.forEach((line, index) => {
    if (decodeEscapedProposalText(line) !== line) escapeLines.add(index + 1);
  });
  const lines = diff.split('\n');
  let oldLine = 1;
  let newLine = 1;
  let sawHunk = false;
  let expectedOldCount = 0;
  let expectedNewCount = 0;
  let consumedOldCount = 0;
  let consumedNewCount = 0;
  const verifiedEscapeLines = new Set<number>();

  for (let index = 0; index < lines.length; index++) {
    const line = lines[index]!;
    const hunk = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(line);
    if (hunk) {
      if (
        sawHunk &&
        (consumedOldCount !== expectedOldCount || consumedNewCount !== expectedNewCount)
      ) {
        return false;
      }
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[3]);
      expectedOldCount = Number(hunk[2] ?? 1);
      expectedNewCount = Number(hunk[4] ?? 1);
      consumedOldCount = 0;
      consumedNewCount = 0;
      sawHunk = true;
      continue;
    }
    if (!sawHunk || (line === '' && index === lines.length - 1)) continue;
    if (line.startsWith('\\ No newline')) continue;

    const prefix = line[0];
    const content = line.slice(1);
    if (prefix === ' ' || prefix === '-') {
      if (beforeLines[oldLine - 1] !== content) return false;
      if (escapeLines.has(oldLine)) verifiedEscapeLines.add(oldLine);
      oldLine++;
      consumedOldCount++;
    } else if (prefix !== '+') {
      return false;
    }
    if (prefix === ' ' || prefix === '+') {
      if (afterLines && afterLines[newLine - 1] !== content) return false;
      newLine++;
      consumedNewCount++;
    }
  }
  return (
    sawHunk &&
    consumedOldCount === expectedOldCount &&
    consumedNewCount === expectedNewCount &&
    verifiedEscapeLines.size === escapeLines.size
  );
}

function diffMatchesCompleteProposal(
  beforeText: string,
  afterText: string,
  diff: string | undefined,
): boolean {
  if (!diff?.trim()) return false;
  const expectedBefore = normalizeLineEndings(beforeText);
  const expectedAfter = normalizeLineEndings(afterText);
  const { before, after } = textsFromUnifiedDiff(diff);
  return (
    diffCoversCompleteSide(diff, 'before', sourceLineCount(expectedBefore)) &&
    diffCoversCompleteSide(diff, 'after', sourceLineCount(expectedAfter)) &&
    normalizeLineEndings(before) === expectedBefore &&
    normalizeLineEndings(after) === expectedAfter
  );
}

function recoverProposalYamlText(
  text: string,
  diff?: string,
  completeAfterText?: string,
): string | undefined {
  if (!diff?.trim()) return undefined;
  if (completeAfterText === undefined) return undefined;

  const candidate = normalizeLineEndings(decodeEscapedProposalText(text));
  if (candidate === normalizeLineEndings(text)) return undefined;
  return diffMatchesCompleteProposal(candidate, completeAfterText, diff)
    ? candidate
    : undefined;
}

/** True when encoded line breaks remain unverified and must be shown verbatim. */
export function hasUnresolvedProposalEscapes(
  text: string,
  diff?: string,
  completeAfterText?: string,
): boolean {
  const candidate = decodeEscapedProposalText(text);
  if (candidate === text) return false;
  if (
    completeAfterText !== undefined &&
    diffMatchesCompleteProposal(text, completeAfterText, diff)
  ) {
    return false;
  }
  if (diffVerifiesEscapeLines(text, completeAfterText, diff)) return false;
  return recoverProposalYamlText(text, diff, completeAfterText) === undefined;
}

/** Recover escaped before_text only when a complete diff verifies both sides. */
export function resolveProposalYamlText(
  text: string,
  diff?: string,
  role: 'before' | 'after' = 'before',
  completeAfterText?: string,
): string {
  if (!text.trim() || role === 'after') return text;
  return recoverProposalYamlText(text, diff, completeAfterText) ?? text;
}

/** Recover before/after text from a unified diff when the API omits them. */
export function textsFromUnifiedDiff(diff: string): { before: string; after: string } {
  const before: string[] = [];
  const after: string[] = [];
  let seenHunk = false;
  for (const raw of diff.split('\n')) {
    if (raw.startsWith('@@')) {
      seenHunk = true;
      continue;
    }
    if (raw.startsWith('\\ No newline')) {
      continue;
    }
    // File headers only appear before the first hunk.
    if (!seenHunk && (raw.startsWith('--- ') || raw.startsWith('+++ '))) {
      continue;
    }
    if (raw.startsWith('-')) {
      before.push(raw.slice(1));
      continue;
    }
    if (raw.startsWith('+')) {
      after.push(raw.slice(1));
      continue;
    }
    const content = raw.startsWith(' ') ? raw.slice(1) : raw;
    before.push(content);
    after.push(content);
  }
  return { before: before.join('\n'), after: after.join('\n') };
}

type PairKind = 'context' | 'change' | 'remove-only' | 'add-only';

interface AlignedRow {
  kind: PairKind;
  left: string | null;
  right: string | null;
  leftNum?: number;
  rightNum?: number;
}

/** Cap LCS DP size; above this, fall back to naive line pairing. */
const MAX_DIFF_LINES = 400;

function alignSideBySide(before: string, after: string): AlignedRow[] {
  const oldLines = before === '' ? [] : before.split('\n');
  const newLines = after === '' ? [] : after.split('\n');
  const n = oldLines.length;
  const m = newLines.length;

  if (n > MAX_DIFF_LINES || m > MAX_DIFF_LINES) {
    const rows: AlignedRow[] = [];
    const max = Math.max(n, m);
    for (let i = 0; i < max; i++) {
      rows.push({
        kind: 'change',
        left: i < n ? oldLines[i]! : null,
        right: i < m ? newLines[i]! : null,
        leftNum: i < n ? i + 1 : undefined,
        rightNum: i < m ? i + 1 : undefined,
      });
    }
    return rows;
  }

  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      dp[i]![j] =
        oldLines[i - 1] === newLines[j - 1]
          ? dp[i - 1]![j - 1]! + 1
          : Math.max(dp[i - 1]![j]!, dp[i]![j - 1]!);
    }
  }

  const stack: AlignedRow[] = [];
  let i = n;
  let j = m;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      stack.push({
        kind: 'context',
        left: oldLines[i - 1]!,
        right: newLines[j - 1]!,
        leftNum: i,
        rightNum: j,
      });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i]![j - 1]! >= dp[i - 1]![j]!)) {
      stack.push({
        kind: 'add-only',
        left: null,
        right: newLines[j - 1]!,
        rightNum: j,
      });
      j--;
    } else {
      stack.push({
        kind: 'remove-only',
        left: oldLines[i - 1]!,
        right: null,
        leftNum: i,
      });
      i--;
    }
  }

  const rows: AlignedRow[] = [];
  while (stack.length > 0) {
    rows.push(stack.pop()!);
  }

  // Pair full runs of adjacent remove-only + add-only into change rows.
  const merged: AlignedRow[] = [];
  let k = 0;
  while (k < rows.length) {
    if (rows[k]!.kind !== 'remove-only') {
      merged.push(rows[k]!);
      k++;
      continue;
    }
    let removeEnd = k;
    while (removeEnd < rows.length && rows[removeEnd]!.kind === 'remove-only') {
      removeEnd++;
    }
    let addEnd = removeEnd;
    while (addEnd < rows.length && rows[addEnd]!.kind === 'add-only') {
      addEnd++;
    }
    const removeCount = removeEnd - k;
    const addCount = addEnd - removeEnd;
    const pairCount = Math.min(removeCount, addCount);
    for (let p = 0; p < pairCount; p++) {
      const r = rows[k + p]!;
      const a = rows[removeEnd + p]!;
      merged.push({
        kind: 'change',
        left: r.left,
        right: a.right,
        leftNum: r.leftNum,
        rightNum: a.rightNum,
      });
    }
    for (let p = pairCount; p < removeCount; p++) {
      merged.push(rows[k + p]!);
    }
    for (let p = pairCount; p < addCount; p++) {
      merged.push(rows[removeEnd + p]!);
    }
    k = addEnd;
  }
  return merged;
}

function UnifiedDiff({ diff, className }: { diff: string; className?: string }) {
  const lines = diff.split('\n');
  let seenHunk = false;
  return (
    <pre
      className={className}
      style={{ margin: 0, fontSize: '0.85em', lineHeight: 1.5, overflow: 'auto' }}
    >
      {lines.map((line, i) => {
        if (line.startsWith('@@')) {
          seenHunk = true;
        }
        const kind = classifyLine(line, seenHunk);
        return (
          <span
            key={i}
            style={{ display: 'block', ...lineStyles[kind], paddingLeft: 4, paddingRight: 4 }}
          >
            {line || '\u00A0'}
          </span>
        );
      })}
    </pre>
  );
}

function useScrollHighlight(
  containerRef: React.RefObject<HTMLElement | null>,
  highlightLine: number | null | undefined,
) {
  useEffect(() => {
    if (highlightLine == null || highlightLine < 1) return;
    const el = containerRef.current?.querySelector(
      `[data-line="${highlightLine}"]`,
    );
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [containerRef, highlightLine]);
}

function SideBySideDiff({
  before,
  after,
  sourceWarning,
  className,
  highlightLine,
}: {
  before: string;
  after: string;
  sourceWarning?: boolean;
  className?: string;
  highlightLine?: number | null;
}) {
  const rows = useMemo(() => alignSideBySide(before, after), [before, after]);
  const containerRef = useRef<HTMLDivElement>(null);
  useScrollHighlight(containerRef, highlightLine);

  return (
    <div
      ref={containerRef}
      className={`apme-side-by-side apme-yaml-hl ${className ?? ''}`.trim()}
    >
      {sourceWarning ? (
        <div className="apme-diff-warning" role="status">
          Current source may contain escaped text that could not be safely reconstructed, so it is shown as received.
        </div>
      ) : null}
      <div className="apme-diff-pane">
        <div className="apme-diff-pane-header">Current</div>
        <pre className="apme-diff-content">
          {rows.map((row, i) => {
            const hl =
              highlightLine != null &&
              row.leftNum != null &&
              row.leftNum === highlightLine;
            return (
              <span
                key={`L-${i}`}
                data-line={row.leftNum}
                className={`apme-diff-line ${
                  row.kind === 'remove-only' || row.kind === 'change'
                    ? 'apme-diff-remove'
                    : ''
                }${hl ? ' apme-diff-line-highlight' : ''}`}
              >
                <span className="apme-diff-linenum">{row.leftNum ?? ''}</span>
                <YamlLine text={row.left} />
              </span>
            );
          })}
        </pre>
      </div>
      <div className="apme-diff-pane">
        <div className="apme-diff-pane-header">Proposed</div>
        <pre className="apme-diff-content">
          {rows.map((row, i) => (
            <span
              key={`R-${i}`}
              className={`apme-diff-line ${
                row.kind === 'add-only' || row.kind === 'change'
                  ? 'apme-diff-add'
                  : ''
              }`}
            >
              <span className="apme-diff-linenum">{row.rightNum ?? ''}</span>
              <YamlLine text={row.right} />
            </span>
          ))}
        </pre>
      </div>
    </div>
  );
}

/** Single-pane current YAML (assessment review — no proposed side). */
export function CurrentYamlView({
  text,
  className,
  highlightLine,
}: {
  text: string;
  className?: string;
  highlightLine?: number | null;
}) {
  const lines = text.replace(/\n$/, '').split('\n');
  const containerRef = useRef<HTMLDivElement>(null);
  useScrollHighlight(containerRef, highlightLine);

  return (
    <div
      ref={containerRef}
      className={`apme-side-by-side apme-current-only apme-yaml-hl ${className ?? ''}`.trim()}
    >
      <div className="apme-diff-pane">
        <div className="apme-diff-pane-header">Current</div>
        <pre className="apme-diff-content">
          {lines.map((line, i) => {
            const num = i + 1;
            const hl = highlightLine != null && num === highlightLine;
            return (
              <span
                key={`C-${i}`}
                data-line={num}
                className={`apme-diff-line${hl ? ' apme-diff-line-highlight' : ''}`}
              >
                <span className="apme-diff-linenum">{num}</span>
                <YamlLine text={line} />
              </span>
            );
          })}
        </pre>
      </div>
    </div>
  );
}

/** Side-by-side or unified proposal diff with optional line highlight (apme#752). */
export function DiffView({
  diff,
  before,
  after,
  mode = 'side-by-side',
  className,
  highlightLine,
  partialSourceWarning = true,
}: DiffViewProps) {
  const resolved = useMemo(() => {
    const beforeText = before?.trim() ? before : '';
    const afterText = after?.trim() ? after : '';
    const bothTextsMatchDiff =
      before !== undefined &&
      after !== undefined &&
      diffMatchesCompleteProposal(beforeText, afterText, diff);
    const beforeAvailable =
      before !== undefined && (Boolean(before.trim()) || bothTextsMatchDiff);
    const afterAvailable =
      after !== undefined && (Boolean(after.trim()) || bothTextsMatchDiff);
    if (!beforeAvailable || !afterAvailable) return null;
    return {
      before: resolveProposalYamlText(beforeText, diff, 'before', afterText),
      after: afterText,
      sourceWarning: hasUnresolvedProposalEscapes(beforeText, diff, afterText),
    };
  }, [before, after, diff]);

  if (mode === 'unified') {
    if (diff?.trim()) {
      const completeDiff =
        resolved !== null &&
        diffMatchesCompleteProposal(resolved.before, resolved.after, diff);
      return (
        <div>
          {partialSourceWarning && !completeDiff ? (
            <div className="apme-diff-warning" role="status">
              Only the supplied diff context is shown; unchanged source lines may be omitted.
            </div>
          ) : null}
          <UnifiedDiff diff={diff} className={className} />
        </div>
      );
    }
    if (resolved) {
      // Build a minimal unified view from before/after for callers that insist.
      const lines = [
        '--- a/file',
        '+++ b/file',
        ...resolved.before.split('\n').map((l) => `-${l}`),
        ...resolved.after.split('\n').map((l) => `+${l}`),
      ];
      return <UnifiedDiff diff={lines.join('\n')} className={className} />;
    }
    return null;
  }

  if (!resolved) {
    if (diff?.trim()) {
      return (
        <div>
          {partialSourceWarning ? (
            <div className="apme-diff-warning" role="status">
              Only the supplied diff context is shown; unchanged source lines may be omitted.
            </div>
          ) : null}
          <UnifiedDiff diff={diff} className={className} />
        </div>
      );
    }
    return null;
  }

  return (
    <SideBySideDiff
      before={resolved.before}
      after={resolved.after}
      sourceWarning={resolved.sourceWarning}
      className={className}
      highlightLine={highlightLine}
    />
  );
}
