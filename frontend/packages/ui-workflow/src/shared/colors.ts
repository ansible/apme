import type { FixType } from '../remediation/fixTypes';
import { healthLabelColor } from './severity';

export type ThemeMode = 'light' | 'dark';

const colors = {
  red: { light: '#9f2427', dark: '#ffaaa5' },
  violet: { light: '#692b90', dark: '#dcacff' },
  orange: { light: '#963e00', dark: '#ffba86' },
  amber: { light: '#735400', dark: '#f5ce6f' },
  blue: { light: '#155fa9', dark: '#9ccaff' },
  neutral: { light: '#4f5b68', dark: '#bac5d2' },
  green: { light: '#21683b', dark: '#98d9a9' },
  indigo: { light: '#4847a2', dark: '#bebdff' },
  teal: { light: '#12696f', dark: '#8bd9dc' },
  pink: { light: '#963d6e', dark: '#f4a9d2' },
};

type Color = keyof typeof colors;

const severityColors = {
  critical: 'red',
  error: 'violet',
  high: 'orange',
  medium: 'amber',
  low: 'blue',
  info: 'neutral',
} as const satisfies Record<string, Color>;

export type SeverityLevel = keyof typeof severityColors;

const fixColors = {
  auto: 'green',
  ai: 'indigo',
  manual: 'neutral',
} as const satisfies Record<FixType, Color>;

// The same hues identify objects in review filters, findings, and graphs.
const nodeColors: Readonly<Record<string, Color>> = {
  playbook: 'red',
  play: 'orange',
  role: 'violet',
  taskfile: 'teal',
  task: 'blue',
  handler: 'green',
  block: 'amber',
  vars_file: 'neutral',
  module: 'green',
  collection: 'indigo',
  python_package: 'blue',
  rulebook: 'pink',
  ruleset: 'violet',
};

const dependencySources = {
  specified: 'blue',
  dependency: 'amber',
  learned: 'green',
  galaxy: 'blue',
  local: 'amber',
  git: 'green',
} as const satisfies Record<string, Color>;

/**
 * Color for a dependency license that needs legal review.
 *
 * Only positively non-open-source licenses (`proprietary`, which includes
 * custom `LicenseRef-` references) are highlighted; open-source and unknown
 * licenses render in the inherited text color so the table stays readable.
 */
export function licenseStatusColor(status: string | undefined): string | undefined {
  return status === 'proprietary' ? themeColor('red') : undefined;
}

export function dependencySourceColor(source: string): string {
  return themeColor(
    Object.prototype.hasOwnProperty.call(dependencySources, source)
      ? dependencySources[source as keyof typeof dependencySources]
      : 'neutral',
  );
}

export function dependencySourceColorTokens(mode: ThemeMode) {
  return Object.fromEntries(
    Object.entries(dependencySources).map(([source, color]) => {
      const { pillBackground, pillText } = colorTokens(color, mode);
      return [source, { background: pillBackground, text: pillText }];
    }),
  );
}

/**
 * Resolves to the light or dark half of a hue pair via `light-dark()`.
 *
 * Host contract: `light-dark()` uses the inherited `color-scheme`, not the OS
 * preference, so every host must declare `color-scheme: light` or `dark` at a
 * scope that workflow content, graph SVGs and body-portaled overlays inherit —
 * matching whatever theme it selected. Without that declaration browsers use
 * the light half, which is why badges and filter pills washed out on dark
 * surfaces. The standalone SPA declares it on `:root`/`.pf-v6-theme-dark` and
 * the BUI hosts on `[data-theme-mode]` in their theme stylesheet.
 */
export function themeColor(color: Color): string {
  return `light-dark(${colors[color].light}, ${colors[color].dark})`;
}

export function severityColor(level: SeverityLevel): string {
  return themeColor(severityColors[level]);
}

export function fixColor(fix: FixType = 'manual'): string {
  return themeColor(fixColors[fix]);
}

export function nodeColor(type: string): string {
  return themeColor(Object.prototype.hasOwnProperty.call(nodeColors, type) ? nodeColors[type]! : 'neutral');
}

export function healthScoreColor(score: number): string {
  const color = healthLabelColor(score);
  return themeColor(color === 'yellow' ? 'amber' : color);
}

/** Mode-specific values for hosts whose theme is supplied by React. */
export function colorTokens(color: Color, mode: ThemeMode) {
  const foreground = colors[color][mode];
  const rgb = Number.parseInt(foreground.slice(1), 16);
  return {
    pillBackground: `rgba(${rgb >> 16}, ${(rgb >> 8) & 255}, ${rgb & 255}, 0.13)`,
    pillText: foreground,
    inlineText: foreground,
  };
}

export function severityColorTokens(level: SeverityLevel, mode: ThemeMode) {
  const color = severityColors[level];
  return { ...colorTokens(color, mode), barFill: colors[color][mode] };
}

export function fixColorTokens(fix: FixType, mode: ThemeMode) {
  return colorTokens(fixColors[fix], mode);
}
