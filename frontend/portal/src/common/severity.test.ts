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

import {
  chipStyleForSeverity,
  effectiveFixType,
  getDependencySourceTokens,
  getDependencyViolationTokens,
  getCodePreviewTokens,
  getPreviewSurfaceTokens,
  getFixTypeColorTokens,
  getSeverityColorTokens,
  getWorstViolationLevel,
  hasNonZeroSeverityCounts,
  inlineTextColorForSeverity,
  isFixableViolation,
  normalizeRemediationClass,
  normalizeSeverity,
  normalizeSeverityBreakdown,
  projectNeedsSeverityEnrichment,
  projectWorstSeverity,
  proposalNeedsManualApproval,
  fixMethodLabel,
  SEVERITY_ORDER,
} from "./severity";

describe("getSeverityColorTokens", () => {
  it("defines light and dark token sets for every severity level", () => {
    for (const mode of ["light", "dark"] as const) {
      const tokens = getSeverityColorTokens(mode);
      for (const level of SEVERITY_ORDER) {
        const t = tokens[level];
        expect(t.pillBackground).toBeTruthy();
        expect(t.pillText).toBeTruthy();
        expect(t.inlineText).toBeTruthy();
        expect(t.barFill).toBeTruthy();
      }
    }
  });

  it("uses lifted inline tints in dark mode", () => {
    const dark = getSeverityColorTokens("dark");
    expect(dark.critical.inlineText).not.toBe(dark.critical.pillBackground);
    expect(dark.error.inlineText).toBe("#fe5149");
  });
});

describe("getFixTypeColorTokens", () => {
  it("defines auto, ai, and manual tokens for both modes", () => {
    for (const mode of ["light", "dark"] as const) {
      const tokens = getFixTypeColorTokens(mode);
      expect(tokens.auto.inlineText).toBeTruthy();
      expect(tokens.ai.inlineText).toBeTruthy();
      expect(tokens.manual.inlineText).toBeTruthy();
    }
  });
});

describe("getDependencySourceTokens", () => {
  it("includes learned and dependency source keys", () => {
    const light = getDependencySourceTokens("light");
    expect(light.learned.text).toBe("#3e8635");
    expect(getDependencySourceTokens("dark").learned.text).toBe("#7cc576");
  });
});

describe("getDependencyViolationTokens", () => {
  it("provides theme-aware violation status colors", () => {
    expect(getDependencyViolationTokens("light").countPillText).toBe("#c9190b");
    expect(getDependencyViolationTokens("dark").okCheckColor).toBe("#3fb950");
  });
});

describe("getCodePreviewTokens", () => {
  it("uses severity error bar color for highlight border", () => {
    const light = getCodePreviewTokens("light");
    expect(light.highlightBorder).toBe("#c9190b");
    expect(getCodePreviewTokens("dark").background).toBe("#212121");
  });
});

describe("getPreviewSurfaceTokens", () => {
  it("defines ADR-012 chip colors for light and dark", () => {
    expect(getPreviewSurfaceTokens("light").prominentBackground).toBe(
      "#fdeaea",
    );
    expect(getPreviewSurfaceTokens("dark").prominentText).toBe("#fe5149");
  });
});

describe("normalizeRemediationClass", () => {
  it("maps proto integers", () => {
    expect(normalizeRemediationClass(1)).toBe(1);
    expect(normalizeRemediationClass(2)).toBe(2);
    expect(normalizeRemediationClass(3)).toBe(3);
  });

  it("maps engine string values", () => {
    expect(normalizeRemediationClass("auto-fixable")).toBe(1);
    expect(normalizeRemediationClass("ai-candidate")).toBe(2);
    expect(normalizeRemediationClass("manual-review")).toBe(3);
  });

  it("defaults unknown values to manual", () => {
    expect(normalizeRemediationClass(undefined)).toBe(3);
    expect(normalizeRemediationClass("unknown")).toBe(3);
  });
});

describe("isFixableViolation", () => {
  it("treats auto as fixable", () => {
    expect(isFixableViolation(1, false)).toBe(true);
  });

  it("treats AI as fixable only when enableAi", () => {
    expect(isFixableViolation(2, true)).toBe(true);
    expect(isFixableViolation(2, false)).toBe(false);
  });

  it("treats manual as not fixable at scan", () => {
    expect(isFixableViolation(3, true)).toBe(false);
  });
});

describe("proposalNeedsManualApproval", () => {
  it("requires approval for AI tier when enabled", () => {
    expect(proposalNeedsManualApproval(2, true)).toBe(true);
  });

  it("does not require approval for auto or manual-at-scan", () => {
    expect(proposalNeedsManualApproval(1, true)).toBe(false);
    expect(proposalNeedsManualApproval(3, true)).toBe(false);
    expect(proposalNeedsManualApproval(2, false)).toBe(false);
  });
});

describe("fixMethodLabel", () => {
  it("uses scan-tier labels that do not imply fixes are already applied", () => {
    expect(fixMethodLabel("auto")).toBe("Rule-based fix");
    expect(fixMethodLabel("ai")).toBe("AI fix");
    expect(fixMethodLabel("manual")).toBe("Manual review");
    expect(fixMethodLabel(undefined)).toBe("Manual review");
  });
});

describe("normalizeSeverityBreakdown", () => {
  it("aggregates raw gateway levels into ADR-043 portal buckets", () => {
    expect(
      normalizeSeverityBreakdown({
        critical: 1,
        error: 2,
        warning: 3,
        low: 10,
        info: 1,
      }),
    ).toEqual({
      critical: 1,
      error: 2,
      high: 0,
      medium: 3,
      low: 10,
      info: 1,
    });
  });
});

describe("normalizeSeverity", () => {
  it("maps ADR-043 labels without collapsing error into high", () => {
    expect(normalizeSeverity("error")).toBe("error");
    expect(normalizeSeverity("high")).toBe("high");
    expect(normalizeSeverity("unknown-level")).toBe("medium");
  });
});

describe("severity presentation helpers", () => {
  it("chipStyleForSeverity maps pill colors from SEVERITY_STYLES", () => {
    expect(chipStyleForSeverity("critical")).toEqual({
      backgroundColor: "#a30000",
      color: "#ffffff",
    });
  });

  it("inlineTextColorForSeverity is theme-aware", () => {
    expect(inlineTextColorForSeverity("high", "light")).toBe("#c46100");
    expect(inlineTextColorForSeverity("high", "dark")).toBe("#ff8c00");
  });

  it("projectWorstSeverity delegates to resolveViolationCounts", () => {
    expect(
      projectWorstSeverity({
        severity_breakdown: { critical: 2, low: 5 },
      }),
    ).toEqual({ level: "critical", count: 2 });
  });

  it("projectWorstSeverity returns null when breakdown is missing but total > 0", () => {
    expect(
      projectWorstSeverity({
        total_violations: 4,
        severity_breakdown: {},
      }),
    ).toBeNull();
  });

  it("hasNonZeroSeverityCounts detects populated buckets", () => {
    expect(
      hasNonZeroSeverityCounts({
        critical: 0,
        error: 0,
        high: 1,
        medium: 0,
        low: 0,
        info: 0,
      }),
    ).toBe(true);
    expect(
      hasNonZeroSeverityCounts({
        critical: 0,
        error: 0,
        high: 0,
        medium: 0,
        low: 0,
        info: 0,
      }),
    ).toBe(false);
  });

  it("projectNeedsSeverityEnrichment when list rows omit breakdown", () => {
    expect(
      projectNeedsSeverityEnrichment({
        total_violations: 4,
        severity_breakdown: {},
      }),
    ).toBe(true);
    expect(
      projectNeedsSeverityEnrichment({
        total_violations: 4,
        severity_breakdown: { high: 4 },
      }),
    ).toBe(false);
    expect(
      projectNeedsSeverityEnrichment({
        total_violations: 0,
        severity_breakdown: {},
      }),
    ).toBe(false);
  });
});

describe("getWorstViolationLevel", () => {
  it("returns the highest non-zero bucket", () => {
    expect(
      getWorstViolationLevel({
        critical: 0,
        error: 0,
        high: 0,
        medium: 2,
        low: 5,
        info: 1,
      }),
    ).toEqual({ level: "medium", count: 2 });
  });

  it("prefers error over high", () => {
    expect(
      getWorstViolationLevel({
        critical: 0,
        error: 1,
        high: 4,
        medium: 0,
        low: 0,
        info: 0,
      }),
    ).toEqual({ level: "error", count: 1 });
  });

  it("falls back to medium when all buckets are zero", () => {
    expect(
      getWorstViolationLevel({
        critical: 0,
        error: 0,
        high: 0,
        medium: 0,
        low: 0,
        info: 0,
      }),
    ).toEqual({ level: "medium", count: 0 });
  });

  it("normalizes string remediation classes via normalizeRemediationClass", () => {
    expect(
      effectiveFixType(normalizeRemediationClass("auto-fixable"), true),
    ).toBe("auto");
    expect(
      effectiveFixType(normalizeRemediationClass("manual-review"), true),
    ).toBe("manual");
  });
});
