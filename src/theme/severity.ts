import { brand, HUES, hueFor, tones, type Hue } from 'src/theme/brand';

/**
 * Severity levels, most urgent first. A value that names one of these renders
 * in the same color in every panel — pie, bar, graph and table — so CRITICAL
 * never depends on where it happens to fall in a query's results.
 */
export const SEVERITY_LEVELS = [
  'critical',
  'high',
  'medium',
  'low',
  'info',
] as const;

export type Severity = (typeof SEVERITY_LEVELS)[number];

// The spellings scanners and advisories use for each level.
const ALIASES: Readonly<Record<string, Severity>> = {
  critical: 'critical',
  high: 'high',
  medium: 'medium',
  moderate: 'medium',
  low: 'low',
  info: 'info',
  informational: 'info',
  none: 'info',
};

/**
 * The hue each level is drawn in. Urgency runs warm to cool: Flare and Ember
 * carry the levels that need action, Starlight the one to plan for, and the
 * neutral ramp the ones that don't, so only the urgent levels draw the eye.
 */
export const severityHues: Readonly<Record<Severity, Hue | 'night'>> = {
  critical: 'flare',
  high: 'ember',
  medium: 'starlight',
  low: 'night',
  info: 'night',
};

/** The severity a value names, ignoring case and surrounding space. */
export function severityOf(value: unknown): Severity | undefined {
  if (typeof value !== 'string') return undefined;
  return ALIASES[value.trim().toLowerCase()];
}

/**
 * A level's color on the current theme. Every level clears 3:1 against the
 * theme's surface and holds Space text at 4.5:1, so labels drawn on a
 * severity fill use Space in both modes. Light mode uses step 600, not the
 * text tone 700.
 */
export function severityColor(
  severity: Severity,
  mode: 'light' | 'dark',
): string {
  const hue = severityHues[severity];
  if (mode === 'dark') {
    return hue === 'night' ? tones.night[300] : brand[hue];
  }
  return hue === 'night' ? tones.night[500] : tones[hue][600];
}

/** Orders severities most urgent first; anything else sorts after them. */
export function compareSeverity(a: unknown, b: unknown): number {
  const rank = (value: unknown) => {
    const severity = severityOf(value);
    return severity === undefined
      ? SEVERITY_LEVELS.length
      : SEVERITY_LEVELS.indexOf(severity);
  };
  return rank(a) - rank(b);
}

/**
 * Whether a set of category labels is a severity breakdown: every label names
 * a level. One unknown label means the categories are something else, and the
 * chart keeps its ordinary palette.
 */
export function isSeverityScale(labels: readonly unknown[]): boolean {
  return labels.length > 0 && labels.every((label) => severityOf(label));
}

/** Whether a column or property name holds a severity. */
export function isSeverityField(name: string): boolean {
  return /severity|criticality/i.test(name);
}

/**
 * The chart hues no severity level uses, for the other categories of a chart
 * that also shows severities, so no category passes for a level.
 */
export function nonSeverityPalette(mode: 'light' | 'dark'): string[] {
  const taken = new Set<string>(Object.values(severityHues));
  return HUES.filter((hue) => !taken.has(hue)).map((hue) => hueFor(hue, mode));
}
