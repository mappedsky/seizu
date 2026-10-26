import type { Panel, PanelThreshold } from 'src/config.context';

import { HUES, hueFor, tones, type Hue } from 'src/theme/brand';

/** A threshold color that names a brand hue rather than a fixed value. */
export type ThresholdPaletteName = Hue | 'night';

/**
 * The quick-select swatches in the ThresholdsEditor, red/yellow/green first.
 * Each stores a palette name, so the same report reads correctly in light
 * and dark mode; the editor still accepts any CSS color as a custom pick.
 */
export const THRESHOLD_PRESETS: ReadonlyArray<{
  label: string;
  name: ThresholdPaletteName;
}> = [
  { label: 'Red', name: 'flare' },
  { label: 'Yellow', name: 'ember' },
  { label: 'Green', name: 'aurora' },
  { label: 'Blue', name: 'starlight' },
  { label: 'Cyan', name: 'beacon' },
  { label: 'Violet', name: 'pulsar' },
  { label: 'Grey', name: 'night' },
];

// The swatches offered before the palette names existed. Reports saved with
// them render in the matching brand hue instead.
const LEGACY_PRESET_HEXES: Readonly<Record<string, ThresholdPaletteName>> = {
  '#f44336': 'flare',
  '#e91e63': 'flare',
  '#ff9800': 'ember',
  '#ffc107': 'ember',
  '#4caf50': 'aurora',
  '#009688': 'aurora',
  '#2196f3': 'starlight',
  '#9c27b0': 'pulsar',
  '#9e9e9e': 'night',
};

function paletteName(color: string): ThresholdPaletteName | undefined {
  const key = color.trim().toLowerCase();
  if (key === 'night' || (HUES as readonly string[]).includes(key)) {
    return key as ThresholdPaletteName;
  }
  return LEGACY_PRESET_HEXES[key];
}

/**
 * The CSS color to paint a stored threshold color with on the current
 * theme: a palette name (or a legacy preset hex) becomes that hue's text-safe
 * tone for the mode, and any other value passes through unchanged.
 */
export function thresholdCss(color: string, mode: 'light' | 'dark'): string;
export function thresholdCss(
  color: string | undefined,
  mode: 'light' | 'dark',
): string | undefined;
export function thresholdCss(
  color: string | undefined,
  mode: 'light' | 'dark',
): string | undefined {
  if (!color) return color;
  const name = paletteName(color);
  if (name === undefined) return color;
  if (name === 'night')
    return mode === 'dark' ? tones.night[300] : tones.night[600];
  return hueFor(name, mode);
}

/** Whether two stored threshold colors name the same swatch. */
export function sameThresholdColor(a: string, b: string): boolean {
  const left = paletteName(a) ?? a.trim().toLowerCase();
  const right = paletteName(b) ?? b.trim().toLowerCase();
  return left === right;
}

const LEGACY_PROGRESS_LOW: ThresholdPaletteName = 'flare';
const LEGACY_PROGRESS_FULL: ThresholdPaletteName = 'aurora';
const LEGACY_COUNT_HIGH: ThresholdPaletteName = 'flare';

/**
 * Pick the color of the threshold with the highest ``value`` that is less
 * than or equal to ``metric``, as stored. Returns ``undefined`` when no
 * threshold matches — callers should fall back to the panel's default text
 * color. Pass the result through ``thresholdCss`` before painting with it.
 */
export function resolveThresholdColor(
  metric: number,
  thresholds: PanelThreshold[] | undefined,
): string | undefined {
  if (!thresholds || thresholds.length === 0) return undefined;
  let best: PanelThreshold | undefined;
  for (const t of thresholds) {
    if (t.value <= metric && (best === undefined || t.value > best.value)) {
      best = t;
    }
  }
  return best?.color;
}

/**
 * Convert a panel's legacy single ``threshold`` value into the equivalent
 * multi-threshold list.
 *
 * For ``count`` panels, the legacy semantic is "color the value when it
 * exceeds the threshold". This maps to a single threshold at the legacy
 * value with a red color.
 *
 * For ``progress`` panels, the legacy semantic is "below the threshold is
 * bad, 100% is great". This maps to a red threshold at 0 and a green
 * threshold at 100, with the legacy value sitting in the middle as a
 * neutral marker (no color override).
 */
export function migrateLegacyThreshold(panel: Panel): PanelThreshold[] {
  if (panel.threshold == null) return [];
  if (panel.type === 'progress') {
    const out: PanelThreshold[] = [{ value: 0, color: LEGACY_PROGRESS_LOW }];
    if (panel.threshold > 0 && panel.threshold < 100) {
      out.push({ value: panel.threshold, color: '' });
    }
    out.push({ value: 100, color: LEGACY_PROGRESS_FULL });
    return out.filter((t) => t.color !== '');
  }
  return [{ value: panel.threshold, color: LEGACY_COUNT_HIGH }];
}
