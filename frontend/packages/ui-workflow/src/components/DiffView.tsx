import type React from 'react';
import { useEffect, useMemo, useRef } from 'react';
import { YamlLine } from './yamlHighlight';

export interface DiffViewProps {
  /** Unified diff hunk (used when before/after are absent, or for unified mode). */
  diff?: string;
  before?: string;
  after?: string;
  /** Default side-by-side when before/after (or parseable diff) is available. */
  mode?: 'unified' | 'side-by-side';
  className?: string;
  /** 1-based line in the Current (before) pane to highlight. */
  highlightLine?: number | null;
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
  const oldLines = before.split('\n');
  const newLines = after.split('\n');
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
  className,
  highlightLine,
}: {
  before: string;
  after: string;
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

export function DiffView({
  diff,
  before,
  after,
  mode = 'side-by-side',
  className,
  highlightLine,
}: DiffViewProps) {
  const resolved = useMemo(() => {
    const b = before?.trim() ? before : undefined;
    const a = after?.trim() ? after : undefined;
    if (b !== undefined && a !== undefined) {
      return { before: b, after: a };
    }
    if (diff?.trim()) {
      return textsFromUnifiedDiff(diff);
    }
    return null;
  }, [before, after, diff]);

  if (mode === 'unified') {
    if (diff?.trim()) {
      return <UnifiedDiff diff={diff} className={className} />;
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

  if (!resolved || (!resolved.before && !resolved.after)) {
    if (diff?.trim()) {
      return <UnifiedDiff diff={diff} className={className} />;
    }
    return null;
  }

  return (
    <SideBySideDiff
      before={resolved.before}
      after={resolved.after}
      className={className}
      highlightLine={highlightLine}
    />
  );
}
