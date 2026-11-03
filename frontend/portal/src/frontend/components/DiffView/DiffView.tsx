/*
 * Copyright Red Hat
 */

import { useMemo } from "react";
import { Box, Typography, makeStyles } from "@material-ui/core";

const useStyles = makeStyles((theme) => ({
  root: {
    fontFamily: '"JetBrains Mono", "Fira Code", "Cascadia Code", monospace',
    fontSize: 12,
    borderRadius: theme.shape.borderRadius,
    overflow: "auto",
    maxHeight: 320,
    border: `1px solid ${theme.palette.divider}`,
    backgroundColor: theme.palette.background.paper,
  },
  rootFillHeight: {
    maxHeight: "none",
    height: "100%",
    flex: 1,
    minHeight: 0,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  linesScroll: {
    flex: 1,
    minHeight: 0,
    overflow: "auto",
  },
  title: {
    padding: theme.spacing(0.75, 1.5),
    borderBottom: `1px solid ${theme.palette.divider}`,
    backgroundColor:
      theme.palette.type === "dark" ? "rgba(255,255,255,0.04)" : "#f5f5f5",
    fontWeight: 600,
    fontSize: 11,
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    color: theme.palette.text.secondary,
  },
  line: {
    display: "flex",
    alignItems: "stretch",
    minHeight: 20,
    lineHeight: "20px",
  },
  lineNumber: {
    width: 36,
    textAlign: "right",
    padding: "0 6px",
    color: theme.palette.text.disabled,
    userSelect: "none",
    flexShrink: 0,
    borderRight: `1px solid ${theme.palette.divider}`,
    fontSize: 11,
  },
  linePrefix: {
    width: 18,
    textAlign: "center",
    flexShrink: 0,
    userSelect: "none",
    fontWeight: 700,
  },
  lineContent: {
    padding: "0 8px",
    whiteSpace: "pre",
    flex: 1,
    color: theme.palette.text.primary,
  },
  added: {
    backgroundColor:
      theme.palette.type === "dark"
        ? "rgba(46, 160, 67, 0.15)"
        : "rgba(46, 160, 67, 0.10)",
    "& $linePrefix": { color: "#2ea043" },
    "& $lineNumber": {
      backgroundColor:
        theme.palette.type === "dark"
          ? "rgba(46, 160, 67, 0.10)"
          : "rgba(46, 160, 67, 0.06)",
    },
  },
  removed: {
    backgroundColor:
      theme.palette.type === "dark"
        ? "rgba(248, 81, 73, 0.15)"
        : "rgba(248, 81, 73, 0.10)",
    "& $linePrefix": { color: "#f85149" },
    "& $lineNumber": {
      backgroundColor:
        theme.palette.type === "dark"
          ? "rgba(248, 81, 73, 0.10)"
          : "rgba(248, 81, 73, 0.06)",
    },
  },
  sideBySideRoot: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: theme.spacing(1),
    marginTop: theme.spacing(1),
    [theme.breakpoints.down("sm")]: {
      gridTemplateColumns: "1fr",
    },
  },
  sideBySidePanel: {
    border: `1px solid ${theme.palette.divider}`,
    borderRadius: theme.shape.borderRadius,
    overflow: "hidden",
    minWidth: 0,
  },
  sideBySideHeader: {
    padding: theme.spacing(0.75, 1.5),
    borderBottom: `1px solid ${theme.palette.divider}`,
    backgroundColor:
      theme.palette.type === "dark" ? "rgba(255,255,255,0.04)" : "#f5f5f5",
    fontWeight: 600,
    fontSize: 11,
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    color: theme.palette.text.secondary,
  },
  sideBySideHeaderAfter: {
    backgroundColor:
      theme.palette.type === "dark"
        ? "rgba(46, 160, 67, 0.12)"
        : "rgba(46, 160, 67, 0.08)",
  },
  sideBySideHeaderBefore: {
    backgroundColor:
      theme.palette.type === "dark"
        ? "rgba(248, 81, 73, 0.12)"
        : "rgba(248, 81, 73, 0.08)",
  },
  sideBySideBody: {
    fontFamily: '"JetBrains Mono", "Fira Code", "Cascadia Code", monospace',
    fontSize: 12,
    padding: theme.spacing(1),
    whiteSpace: "pre-wrap",
    overflow: "auto",
    maxHeight: 280,
    color: theme.palette.text.primary,
    backgroundColor: theme.palette.background.paper,
  },
  context: {
    backgroundColor:
      theme.palette.type === "dark" ? "rgba(255,255,255,0.02)" : "#fafafa",
  },
}));

function diffLineClassName(
  classes: ReturnType<typeof useStyles>,
  lineType: DiffLine["type"],
): string {
  if (lineType === "added") return classes.added;
  if (lineType === "removed") return classes.removed;
  return classes.context;
}

interface DiffLine {
  type: "added" | "removed" | "context";
  content: string;
  oldNum?: number;
  newNum?: number;
}

function diffLineReactKey(line: DiffLine): string {
  return `${line.type}:${line.oldNum ?? "x"}:${line.newNum ?? "x"}:${
    line.content
  }`;
}

interface DiffParseState {
  oldNum: number;
  newNum: number;
}

/** Parse hunk header and extract line numbers */
function parseHunkHeader(
  raw: string,
  state: DiffParseState,
): { line: DiffLine; state: DiffParseState } {
  const match = /@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
  const newState = match
    ? {
        oldNum: Number.parseInt(match[1], 10),
        newNum: Number.parseInt(match[2], 10),
      }
    : state;
  return { line: { type: "context", content: raw }, state: newState };
}

function parseAddedDiffLine(
  raw: string,
  state: DiffParseState,
): { line: DiffLine; state: DiffParseState } {
  const line: DiffLine = {
    type: "added",
    content: raw.slice(1),
    newNum: state.newNum > 0 ? state.newNum : undefined,
  };
  return {
    line,
    state: {
      ...state,
      newNum: state.newNum > 0 ? state.newNum + 1 : 0,
    },
  };
}

function parseRemovedDiffLine(
  raw: string,
  state: DiffParseState,
): { line: DiffLine; state: DiffParseState } {
  const line: DiffLine = {
    type: "removed",
    content: raw.slice(1),
    oldNum: state.oldNum > 0 ? state.oldNum : undefined,
  };
  return {
    line,
    state: {
      ...state,
      oldNum: state.oldNum > 0 ? state.oldNum + 1 : 0,
    },
  };
}

function parseContextDiffLine(
  raw: string,
  state: DiffParseState,
): { line: DiffLine; state: DiffParseState } {
  const content = raw.startsWith(" ") ? raw.slice(1) : raw;
  const line: DiffLine = {
    type: "context",
    content,
    oldNum: state.oldNum > 0 ? state.oldNum : undefined,
    newNum: state.newNum > 0 ? state.newNum : undefined,
  };
  return {
    line,
    state: {
      oldNum: state.oldNum > 0 ? state.oldNum + 1 : 0,
      newNum: state.newNum > 0 ? state.newNum + 1 : 0,
    },
  };
}

/** Parse a single diff line and return the DiffLine with updated state */
function parseDiffLine(
  raw: string,
  state: DiffParseState,
): { line: DiffLine; state: DiffParseState } | null {
  if (!raw) return null;

  if (raw.startsWith("@@")) {
    return parseHunkHeader(raw, state);
  }

  if (raw.startsWith("+++") || raw.startsWith("---")) {
    return { line: { type: "context", content: raw }, state };
  }

  if (raw.startsWith("+")) {
    return parseAddedDiffLine(raw, state);
  }

  if (raw.startsWith("-")) {
    return parseRemovedDiffLine(raw, state);
  }

  return parseContextDiffLine(raw, state);
}

function parseUnifiedDiffHunk(diff: string): DiffLine[] {
  const result: DiffLine[] = [];
  let state: DiffParseState = { oldNum: 0, newNum: 0 };

  for (const raw of diff.split("\n")) {
    // Skip empty lines only when we already have results
    if (!raw && result.length > 0) continue;

    const parsed = parseDiffLine(raw, state);
    if (parsed) {
      result.push(parsed.line);
      state = parsed.state;
    }
  }

  return result;
}

const MAX_DIFF_LINES = 2000;

function computeUnifiedDiff(before: string, after: string): DiffLine[] {
  const oldLines = before.split("\n");
  const newLines = after.split("\n");

  const n = oldLines.length;
  const m = newLines.length;

  if (n > MAX_DIFF_LINES || m > MAX_DIFF_LINES) {
    return [
      ...oldLines.map((l, idx) => ({
        type: "removed" as const,
        content: l,
        oldNum: idx + 1,
      })),
      ...newLines.map((l, idx) => ({
        type: "added" as const,
        content: l,
        newNum: idx + 1,
      })),
    ];
  }

  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      dp[i][j] =
        oldLines[i - 1] === newLines[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  const result: DiffLine[] = [];
  let i = n;
  let j = m;
  const stack: DiffLine[] = [];
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      stack.push({
        type: "context",
        content: oldLines[i - 1],
        oldNum: i,
        newNum: j,
      });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      stack.push({ type: "added", content: newLines[j - 1], newNum: j });
      j--;
    } else {
      stack.push({ type: "removed", content: oldLines[i - 1], oldNum: i });
      i--;
    }
  }
  while (stack.length > 0) {
    result.push(stack.pop()!);
  }
  return result;
}

export interface DiffViewProps {
  before?: string;
  after?: string;
  /** Unified diff from the gateway (`diff_hunk`). Takes precedence over before/after. */
  diff?: string;
  title?: string;
  /** Side-by-side Before/After panels (design checkbox review flow). */
  layout?: "unified" | "sideBySide";
  afterLabel?: string;
  /** Expand to fill a flex parent (e.g. patch viewer modal). */
  fillHeight?: boolean;
}

function SideBySideDiff({
  before,
  after,
  afterLabel = "After (proposed)",
}: Readonly<{
  before?: string;
  after?: string;
  afterLabel?: string;
}>) {
  const classes = useStyles();
  if (!before?.trim() && !after?.trim()) return null;
  return (
    <Box className={classes.sideBySideRoot}>
      <Box className={classes.sideBySidePanel}>
        <Typography
          className={`${classes.sideBySideHeader} ${classes.sideBySideHeaderBefore}`}
        >
          Before
        </Typography>
        <Box className={classes.sideBySideBody}>{before ?? ""}</Box>
      </Box>
      <Box className={classes.sideBySidePanel}>
        <Typography
          className={`${classes.sideBySideHeader} ${classes.sideBySideHeaderAfter}`}
        >
          {afterLabel}
        </Typography>
        <Box className={classes.sideBySideBody}>{after ?? ""}</Box>
      </Box>
    </Box>
  );
}

export const DiffView = ({
  before,
  after,
  diff,
  title,
  layout = "unified",
  afterLabel,
  fillHeight = false,
}: DiffViewProps) => {
  const classes = useStyles();
  const showSideBySide =
    layout === "sideBySide" &&
    !diff?.trim() &&
    Boolean(before?.trim() || after?.trim());

  const lines = useMemo(() => {
    if (showSideBySide) {
      return [];
    }
    if (diff?.trim()) {
      return parseUnifiedDiffHunk(diff);
    }
    if (before && after) return computeUnifiedDiff(before, after);
    if (before)
      return before.split("\n").map((c, i): DiffLine => ({
        type: "removed",
        content: c,
        oldNum: i + 1,
      }));
    if (after)
      return after.split("\n").map((c, i): DiffLine => ({
        type: "added",
        content: c,
        newNum: i + 1,
      }));
    return [];
  }, [before, after, diff, showSideBySide]);

  if (showSideBySide) {
    return (
      <Box>
        {title && <Typography className={classes.title}>{title}</Typography>}
        <SideBySideDiff before={before} after={after} afterLabel={afterLabel} />
      </Box>
    );
  }

  if (lines.length === 0) return null;

  const prefixChar = (type: DiffLine["type"]) => {
    if (type === "added") return "+";
    if (type === "removed") return "\u2212";
    return " ";
  };

  return (
    <Box
      className={
        fillHeight ? `${classes.root} ${classes.rootFillHeight}` : classes.root
      }
    >
      {title && <Typography className={classes.title}>{title}</Typography>}
      <Box className={fillHeight ? classes.linesScroll : undefined}>
        {lines.map((line) => (
          <div
            key={diffLineReactKey(line)}
            className={`${classes.line} ${diffLineClassName(
              classes,
              line.type,
            )}`}
          >
            <span className={classes.lineNumber}>{line.oldNum ?? ""}</span>
            <span className={classes.lineNumber}>{line.newNum ?? ""}</span>
            <span className={classes.linePrefix}>{prefixChar(line.type)}</span>
            <span className={classes.lineContent}>{line.content}</span>
          </div>
        ))}
      </Box>
    </Box>
  );
};
