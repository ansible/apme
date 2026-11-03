/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import type { Severity } from "./types";

export type SeverityLevel =
  "critical" | "error" | "high" | "medium" | "low" | "info";

export interface ViolationCountsBySeverity {
  critical: number;
  error: number;
  high: number;
  medium: number;
  low: number;
  info: number;
}

export type FixType = "auto" | "ai" | "manual";

export interface SeverityStyle {
  background: string;
  text: string;
  sortOrder: number;
  label: string;
}

export interface FixTypeStyle {
  background: string;
  text: string;
  label: string;
  tooltip: string;
}

/**
 * Centralized severity-to-color mapping (ADR-043).
 *
 * This is the **single source of truth** for violation severity colors across
 * the portal. Every component that renders severity indicators — pills, badges,
 * inline counts, bar charts — must reference these styles or the theme-aware
 * {@link SEVERITY_COLOR_TOKENS} below. Do NOT hardcode hex color values in
 * components; use the helpers listed below instead.
 *
 * ### Severity color convention
 *
 * | Level    | Color   | Background | Tab / badge behavior              |
 * |----------|---------|------------|-----------------------------------|
 * | critical | Red     | `#a30000`  | Colored pill — highest urgency    |
 * | error    | Red     | `#c9190b`  | Colored pill                      |
 * | high     | Orange  | `#f56a00`  | Colored pill                      |
 * | medium   | Yellow  | `#c58c00`  | Colored pill                      |
 * | low      | Blue    | `#2b9af3`  | Default/neutral where pill omitted |
 * | info     | Gray    | `#6a6e73`  | Default/neutral where pill omitted |
 *
 * Summary chips (e.g. catalog status chip) use colored pills for
 * critical / error / high / medium. Low and informational violations
 * display the count in the default (non-highlighted) text style.
 *
 * Detailed views (fleet table, violation modal, rules tab) still use the
 * full palette including low and info colors for pills and bar segments.
 *
 * ### How to adopt in a new component
 *
 * - **Pill / chip backgrounds:** `chipStyleForSeverity(level)`
 * - **Inline text coloring:** `inlineTextColorForSeverity(level, mode)`
 * - **Full theme-aware tokens:** `useApmeColorTokens().severity[level]`
 * - **Worst severity from a project:** `projectWorstSeverity(project)`
 */
export const SEVERITY_STYLES: Record<SeverityLevel, SeverityStyle> = {
  critical: {
    background: "#a30000",
    text: "#ffffff",
    sortOrder: 0,
    label: "Critical",
  },
  error: {
    background: "#c9190b",
    text: "#ffffff",
    sortOrder: 1,
    label: "Error",
  },
  high: {
    background: "#f56a00",
    text: "#ffffff",
    sortOrder: 2,
    label: "High",
  },
  medium: {
    background: "#c58c00",
    text: "#ffffff",
    sortOrder: 3,
    label: "Medium",
  },
  low: { background: "#2b9af3", text: "#ffffff", sortOrder: 4, label: "Low" },
  info: { background: "#6a6e73", text: "#ffffff", sortOrder: 5, label: "Info" },
};

export const FIX_TYPE_STYLES: Record<FixType, FixTypeStyle> = {
  auto: {
    background: "#4caf50",
    text: "#ffffff",
    label: "Rule-based fix",
    tooltip:
      "Rule-based fix available at scan — applied when you run Generate fixes",
  },
  ai: {
    background: "#2196f3",
    text: "#ffffff",
    label: "AI fix",
    tooltip:
      "AI fix at scan — Generate fixes may produce a proposal for your review",
  },
  manual: {
    background: "#6a6e73",
    text: "#ffffff",
    label: "Manual review",
    tooltip:
      "Manual at scan — auto-generated fixes may still apply; hand-edit in Dev Spaces if not",
  },
};

export type ThemeMode = "light" | "dark";

export interface SeverityColorTokens {
  pillBackground: string;
  pillText: string;
  inlineText: string;
  barFill: string;
}

export interface FixTypeColorTokens {
  pillBackground: string;
  pillText: string;
  inlineText: string;
}

export interface DependencySourceTokens {
  background: string;
  text: string;
}

export interface DependencyViolationTokens {
  countPillBackground: string;
  countPillText: string;
  okCheckColor: string;
}

export interface CodePreviewTokens {
  background: string;
  text: string;
  lineNumber: string;
  divider: string;
  highlightBackground: string;
  highlightBorder: string;
}

/** ADR-012 early-access preview chip (Plugin Factory non-GA surfaces). */
export interface PreviewSurfaceTokens {
  prominentBackground: string;
  prominentText: string;
  prominentBorder: string;
  outlinedBackground: string;
  outlinedText: string;
  outlinedBorder: string;
}

/**
 * Theme-aware severity color tokens (light / dark).
 *
 * Each severity level provides four token slots:
 * - `pillBackground` / `pillText` — for chip and badge backgrounds
 * - `inlineText` — for colored counts and links in body text
 * - `barFill` — for stacked severity bars and progress indicators
 *
 * Access via `getSeverityColorTokens(mode)` or the React hook
 * `useApmeColorTokens().severity`.
 *
 * @see SEVERITY_STYLES for the theme-agnostic (static) palette.
 */
const SEVERITY_COLOR_TOKENS: Record<
  ThemeMode,
  Record<SeverityLevel, SeverityColorTokens>
> = {
  light: {
    critical: {
      pillBackground: "#a30000",
      pillText: "#ffffff",
      inlineText: "#a30000",
      barFill: "#a30000",
    },
    error: {
      pillBackground: "#c9190b",
      pillText: "#ffffff",
      inlineText: "#c9190b",
      barFill: "#c9190b",
    },
    high: {
      pillBackground: "#f56a00",
      pillText: "#ffffff",
      inlineText: "#c46100",
      barFill: "#f56a00",
    },
    medium: {
      pillBackground: "#c58c00",
      pillText: "#ffffff",
      inlineText: "#8a6900",
      barFill: "#f0ab00",
    },
    low: {
      pillBackground: "#2b9af3",
      pillText: "#ffffff",
      inlineText: "#0066cc",
      barFill: "#2b9af3",
    },
    info: {
      pillBackground: "#6a6e73",
      pillText: "#ffffff",
      inlineText: "#4d5358",
      barFill: "#6a6e73",
    },
  },
  dark: {
    critical: {
      pillBackground: "#7d1007",
      pillText: "#ffffff",
      inlineText: "#fe5149",
      barFill: "#a30000",
    },
    error: {
      pillBackground: "#a30000",
      pillText: "#ffffff",
      inlineText: "#fe5149",
      barFill: "#c9190b",
    },
    high: {
      pillBackground: "#8f4700",
      pillText: "#ffffff",
      inlineText: "#ff8c00",
      barFill: "#f56a00",
    },
    medium: {
      pillBackground: "#7a5c00",
      pillText: "#ffffff",
      inlineText: "#f0ab00",
      barFill: "#c58c00",
    },
    low: {
      pillBackground: "#004368",
      pillText: "#ffffff",
      inlineText: "#92d5ff",
      barFill: "#2b9af3",
    },
    info: {
      pillBackground: "#4d5358",
      pillText: "#ffffff",
      inlineText: "#b8bbbe",
      barFill: "#6a6e73",
    },
  },
};

const FIX_TYPE_COLOR_TOKENS: Record<
  ThemeMode,
  Record<FixType, FixTypeColorTokens>
> = {
  light: {
    auto: {
      pillBackground: "#4caf50",
      pillText: "#ffffff",
      inlineText: "#1a7f37",
    },
    ai: {
      pillBackground: "#2196f3",
      pillText: "#ffffff",
      inlineText: "#6753ac",
    },
    manual: {
      pillBackground: "#6a6e73",
      pillText: "#ffffff",
      inlineText: "#6a6e73",
    },
  },
  dark: {
    auto: {
      pillBackground: "#1a7f37",
      pillText: "#ffffff",
      inlineText: "#3fb950",
    },
    ai: {
      pillBackground: "#6753ac",
      pillText: "#ffffff",
      inlineText: "#a78bfa",
    },
    manual: {
      pillBackground: "#4d5358",
      pillText: "#ffffff",
      inlineText: "#9ca3af",
    },
  },
};

const DEPENDENCY_SOURCE_TOKENS: Record<
  ThemeMode,
  Record<string, DependencySourceTokens>
> = {
  light: {
    specified: { background: "#e7f1fa", text: "#0066cc" },
    dependency: { background: "#f4f0e6", text: "#795600" },
    learned: { background: "#e9f5e9", text: "#3e8635" },
    galaxy: { background: "#e7f1fa", text: "#0066cc" },
    local: { background: "#f4f0e6", text: "#795600" },
    git: { background: "#e9f5e9", text: "#3e8635" },
  },
  dark: {
    specified: { background: "rgba(43, 154, 243, 0.12)", text: "#6cb8f7" },
    dependency: { background: "rgba(197, 140, 0, 0.12)", text: "#e6b800" },
    learned: { background: "rgba(62, 134, 53, 0.12)", text: "#7cc576" },
    galaxy: { background: "rgba(43, 154, 243, 0.12)", text: "#6cb8f7" },
    local: { background: "rgba(197, 140, 0, 0.12)", text: "#e6b800" },
    git: { background: "rgba(62, 134, 53, 0.12)", text: "#7cc576" },
  },
};

const DEPENDENCY_VIOLATION_TOKENS: Record<
  ThemeMode,
  DependencyViolationTokens
> = {
  light: {
    countPillBackground: "#fdeaea",
    countPillText: "#c9190b",
    okCheckColor: "#3e8635",
  },
  dark: {
    countPillBackground: "rgba(201, 25, 11, 0.15)",
    countPillText: "#fe5149",
    okCheckColor: "#3fb950",
  },
};

function buildCodePreviewTokens(mode: ThemeMode): CodePreviewTokens {
  const error = SEVERITY_COLOR_TOKENS[mode].error;
  return {
    background: mode === "dark" ? "#212121" : "#f5f5f5",
    text: mode === "dark" ? "#e0e0e0" : "#151515",
    lineNumber: mode === "dark" ? "#9e9e9e" : "#757575",
    divider: mode === "dark" ? "rgba(255,255,255,0.12)" : "rgba(0,0,0,0.12)",
    highlightBackground:
      mode === "dark" ? "rgba(201, 25, 11, 0.15)" : "rgba(201, 25, 11, 0.10)",
    highlightBorder: error.barFill,
  };
}

const CODE_PREVIEW_TOKENS: Record<ThemeMode, CodePreviewTokens> = {
  light: buildCodePreviewTokens("light"),
  dark: buildCodePreviewTokens("dark"),
};

const PREVIEW_SURFACE_TOKENS: Record<ThemeMode, PreviewSurfaceTokens> = {
  light: {
    prominentBackground: "#fdeaea",
    prominentText: "#7d1007",
    prominentBorder: "#c9190b",
    outlinedBackground: "transparent",
    outlinedText: "#c9190b",
    outlinedBorder: "#c9190b",
  },
  dark: {
    prominentBackground: "rgba(250, 120, 120, 0.22)",
    prominentText: "#fe5149",
    prominentBorder: "rgba(250, 120, 120, 0.45)",
    outlinedBackground: "transparent",
    outlinedText: "#fe5149",
    outlinedBorder: "rgba(250, 120, 120, 0.45)",
  },
};

export function getSeverityColorTokens(
  mode: ThemeMode,
): Record<SeverityLevel, SeverityColorTokens> {
  return SEVERITY_COLOR_TOKENS[mode];
}

export function getFixTypeColorTokens(
  mode: ThemeMode,
): Record<FixType, FixTypeColorTokens> {
  return FIX_TYPE_COLOR_TOKENS[mode];
}

export function getDependencySourceTokens(
  mode: ThemeMode,
): Record<string, DependencySourceTokens> {
  return DEPENDENCY_SOURCE_TOKENS[mode];
}

export function getDependencyViolationTokens(
  mode: ThemeMode,
): DependencyViolationTokens {
  return DEPENDENCY_VIOLATION_TOKENS[mode];
}

export function getCodePreviewTokens(mode: ThemeMode): CodePreviewTokens {
  return CODE_PREVIEW_TOKENS[mode];
}

export function getPreviewSurfaceTokens(mode: ThemeMode): PreviewSurfaceTokens {
  return PREVIEW_SURFACE_TOKENS[mode];
}

const APME_TO_PORTAL_SEVERITY: Record<string, SeverityLevel> = {
  critical: "critical",
  fatal: "critical",
  blocker: "critical",
  error: "error",
  high: "high",
  very_high: "high",
  medium: "medium",
  warning: "medium",
  warn: "medium",
  low: "low",
  very_low: "low",
  info: "info",
  none: "info",
  unspecified: "medium",
};

/** ADR-043 severity order (worst first) for filters and summaries. */
export const SEVERITY_ORDER: SeverityLevel[] = [
  "critical",
  "error",
  "high",
  "medium",
  "low",
  "info",
];

const REMEDIATION_CLASS_TO_FIX_TYPE: Record<number, FixType> = {
  1: "auto",
  2: "ai",
  3: "manual",
};

const REMEDIATION_CLASS_STRING_TO_NUMBER: Record<string, number> = {
  "auto-fixable": 1,
  "ai-candidate": 2,
  "manual-review": 3,
};

// Category labels as shown in the live portal prototype
export const APME_CATEGORY_MAP: Record<string, string> = {
  lint: "Lint",
  modernize: "Modernize",
  risk: "Risk",
  secrets: "Secrets",
  dependencies: "Dependencies",
  aap: "Modernize", // AAP compat = modernize
};

export function normalizeSeverity(level: string): SeverityLevel {
  return APME_TO_PORTAL_SEVERITY[level.toLowerCase()] ?? "medium";
}

/** Aggregate raw gateway severity keys into ADR-043 portal buckets. */
export function normalizeSeverityBreakdown(
  breakdown: Record<string, number> | undefined,
): ViolationCountsBySeverity {
  const result: ViolationCountsBySeverity = {
    critical: 0,
    error: 0,
    high: 0,
    medium: 0,
    low: 0,
    info: 0,
  };
  if (!breakdown) {
    return result;
  }
  for (const [level, count] of Object.entries(breakdown)) {
    if (!count) {
      continue;
    }
    const bucket = normalizeSeverity(level);
    result[bucket] += count;
  }
  return result;
}

/** True when at least one ADR-043 severity bucket has a non-zero count. */
export function hasNonZeroSeverityCounts(
  counts: ViolationCountsBySeverity,
): boolean {
  return SEVERITY_ORDER.some((level) => counts[level] > 0);
}

/** Pill/chip background + text from the shared severity palette. */
export function chipStyleForSeverity(level: SeverityLevel): {
  backgroundColor: string;
  color: string;
} {
  const style = SEVERITY_STYLES[level];
  return { backgroundColor: style.background, color: style.text };
}

/** Theme-aware inline text color for counts, tab labels, and links. */
export function inlineTextColorForSeverity(
  level: SeverityLevel,
  mode: ThemeMode,
): string {
  return getSeverityColorTokens(mode)[level].inlineText;
}

/**
 * Worst open-severity bucket for a project summary or detail row.
 * Returns null when violations exist but severity breakdown is missing/empty
 * (avoids painting counts medium/amber before enrichment completes).
 */
export function projectWorstSeverity(project: {
  violationCounts?: ViolationCountsBySeverity;
  severity_breakdown?: Record<string, number>;
  total_violations?: number;
}): { level: SeverityLevel; count: number } | null {
  const counts = resolveViolationCounts(project);
  if (!hasNonZeroSeverityCounts(counts)) {
    return null;
  }
  return getWorstViolationLevel(counts);
}

/**
 * List API summaries omit severity_breakdown; detail/lookup include it.
 * Returns true when we should fetch project detail to colorize correctly.
 */
export function projectNeedsSeverityEnrichment(project: {
  total_violations?: number;
  violationCounts?: ViolationCountsBySeverity;
  severity_breakdown?: Record<string, number>;
}): boolean {
  if ((project.total_violations ?? 0) <= 0) {
    return false;
  }
  return !hasNonZeroSeverityCounts(resolveViolationCounts(project));
}

export function getWorstViolationLevel(counts: ViolationCountsBySeverity): {
  level: SeverityLevel;
  count: number;
} {
  if (counts.critical > 0) {
    return { level: "critical", count: counts.critical };
  }
  if (counts.error > 0) {
    return { level: "error", count: counts.error };
  }
  if (counts.high > 0) {
    return { level: "high", count: counts.high };
  }
  if (counts.medium > 0) {
    return { level: "medium", count: counts.medium };
  }
  if (counts.low > 0) {
    return { level: "low", count: counts.low };
  }
  if (counts.info > 0) {
    return { level: "info", count: counts.info };
  }
  return { level: "medium", count: 0 };
}

export function resolveViolationCounts(project: {
  violationCounts?: ViolationCountsBySeverity;
  severity_breakdown?: Record<string, number>;
}): ViolationCountsBySeverity {
  if (project.violationCounts) {
    return project.violationCounts;
  }
  return normalizeSeverityBreakdown(project.severity_breakdown);
}

export function severityColor(level: string): string {
  return SEVERITY_STYLES[normalizeSeverity(level)].background;
}

export function severityLabel(level: string): string {
  return SEVERITY_STYLES[normalizeSeverity(level)].label;
}

export function severitySortOrder(level: string): number {
  return SEVERITY_STYLES[normalizeSeverity(level)].sortOrder;
}

/** Normalizes gateway remediation class (proto int or engine string) to portal 1/2/3. */
export function normalizeRemediationClass(value: unknown): number {
  if (typeof value === "number" && !Number.isNaN(value)) {
    if (value >= 1 && value <= 3) {
      return value;
    }
    return 3;
  }
  if (typeof value === "string") {
    const trimmed = value.trim().toLowerCase();
    const mapped = REMEDIATION_CLASS_STRING_TO_NUMBER[trimmed];
    if (mapped !== undefined) {
      return mapped;
    }
    const parsed = Number.parseInt(trimmed, 10);
    if (!Number.isNaN(parsed) && parsed >= 1 && parsed <= 3) {
      return parsed;
    }
  }
  return 3;
}

export function remediationClassToFixType(
  remClass: number,
): FixType | undefined {
  return REMEDIATION_CLASS_TO_FIX_TYPE[normalizeRemediationClass(remClass)];
}

/** Maps engine remediation class to UI fix type, respecting portal AI toggle. */
export function effectiveFixType(
  remClass: number,
  enableAi: boolean,
): FixType | undefined {
  const base = remediationClassToFixType(normalizeRemediationClass(remClass));
  if (base === "ai" && !enableAi) {
    return "manual";
  }
  return base;
}

/** True when scan classification allows auto or AI proposal generation in the UI. */
export function isFixableViolation(
  remClass: number,
  enableAi: boolean,
): boolean {
  const ft = effectiveFixType(remClass, enableAi);
  return ft === "auto" || ft === "ai";
}

/** True when a proposal requires explicit user approval (AI tier). */
export function proposalNeedsManualApproval(
  remClass: number,
  enableAi: boolean,
): boolean {
  return effectiveFixType(remClass, enableAi) === "ai";
}

export function fixMethodLabel(fixType: FixType | undefined): string {
  if (fixType === "auto") {
    return "Rule-based fix";
  }
  if (fixType === "ai") {
    return "AI fix";
  }
  return "Manual review";
}

/** Tooltip for fix-method column (scan classification, not post-remediate status). */
export function fixMethodTooltip(fixType: FixType | undefined): string {
  if (!fixType) {
    return FIX_TYPE_STYLES.manual.tooltip;
  }
  return FIX_TYPE_STYLES[fixType].tooltip;
}

/** Short fix-tier label for fleet repo rows (design: QualityOverviewContent). */
export function fixTierShortLabel(remClass: number, enableAi: boolean): string {
  const fixType = effectiveFixType(remClass, enableAi);
  if (fixType === "auto") {
    return "Rule-based fix";
  }
  if (fixType === "ai") {
    return "AI fix";
  }
  return "Manual review";
}

export function categoryLabel(apmeCategory: string): string {
  return APME_CATEGORY_MAP[apmeCategory] ?? apmeCategory;
}

/** Proto severity enum values from APME gateway (ADR-043). */
const SEVERITY_LABEL_TO_PROTO: Record<string, number> = {
  info: 1,
  low: 2,
  medium: 3,
  high: 4,
  error: 5,
  critical: 6,
  blocker: 6,
};

const SEVERITY_PROTO_TO_LABEL: SeverityLevel[] = [
  "medium",
  "info",
  "low",
  "medium",
  "high",
  "error",
  "critical",
];

/** Map portal severity label to gateway proto int for rule config overrides. */
export function severityLabelToProto(label: string): number {
  const normalized = label.toLowerCase();
  return SEVERITY_LABEL_TO_PROTO[normalized] ?? 3;
}

/** Map gateway proto int to portal severity label. */
export function severityProtoToLabel(value: number): SeverityLevel {
  const index = Math.min(
    Math.max(value, 0),
    SEVERITY_PROTO_TO_LABEL.length - 1,
  );
  return SEVERITY_PROTO_TO_LABEL[index] ?? "medium";
}

/** Map violation/UI severity labels to catalog Rule severity (no separate "error"). */
export function severityLevelToCatalogSeverity(level: SeverityLevel): Severity {
  if (level === "error") {
    return "critical";
  }
  return level;
}
