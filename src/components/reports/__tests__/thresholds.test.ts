import type { Panel } from 'src/config.context';
import {
  migrateLegacyThreshold,
  resolveThresholdColor,
  sameThresholdColor,
  THRESHOLD_PRESETS,
  thresholdCss,
} from '../thresholds';
import { brand, tones } from 'src/theme/brand';

describe('resolveThresholdColor', () => {
  it('returns undefined when no thresholds are configured', () => {
    expect(resolveThresholdColor(50, undefined)).toBeUndefined();
    expect(resolveThresholdColor(50, [])).toBeUndefined();
  });

  it('returns undefined when the metric is below all thresholds', () => {
    const thresholds = [
      { value: 50, color: '#FF0000' },
      { value: 100, color: '#00FF00' },
    ];
    expect(resolveThresholdColor(10, thresholds)).toBeUndefined();
  });

  it('returns the color of the highest matching threshold', () => {
    const thresholds = [
      { value: 0, color: '#FF0000' },
      { value: 70, color: '#FFC107' },
      { value: 100, color: '#4CAF50' },
    ];
    expect(resolveThresholdColor(0, thresholds)).toBe('#FF0000');
    expect(resolveThresholdColor(50, thresholds)).toBe('#FF0000');
    expect(resolveThresholdColor(70, thresholds)).toBe('#FFC107');
    expect(resolveThresholdColor(99, thresholds)).toBe('#FFC107');
    expect(resolveThresholdColor(100, thresholds)).toBe('#4CAF50');
    expect(resolveThresholdColor(120, thresholds)).toBe('#4CAF50');
  });

  it('does not assume thresholds are pre-sorted', () => {
    const thresholds = [
      { value: 100, color: '#00FF00' },
      { value: 0, color: '#FF0000' },
    ];
    expect(resolveThresholdColor(100, thresholds)).toBe('#00FF00');
    expect(resolveThresholdColor(50, thresholds)).toBe('#FF0000');
  });
});

describe('migrateLegacyThreshold', () => {
  it('returns an empty list when no legacy threshold is set', () => {
    const panel: Panel = { type: 'count' };
    expect(migrateLegacyThreshold(panel)).toEqual([]);
  });

  it('maps a count threshold to a single red threshold', () => {
    const panel: Panel = { type: 'count', threshold: 1000 };
    expect(migrateLegacyThreshold(panel)).toEqual([
      { value: 1000, color: 'flare' },
    ]);
  });

  it('maps a progress threshold to red-at-zero plus green-at-hundred', () => {
    const panel: Panel = { type: 'progress', threshold: 70 };
    expect(migrateLegacyThreshold(panel)).toEqual([
      { value: 0, color: 'flare' },
      { value: 100, color: 'aurora' },
    ]);
  });
});

describe('THRESHOLD_PRESETS', () => {
  it('leads with red, yellow and green', () => {
    expect(THRESHOLD_PRESETS.slice(0, 3).map((p) => p.label)).toEqual([
      'Red',
      'Yellow',
      'Green',
    ]);
  });

  it('resolves every preset to a color in both modes', () => {
    for (const preset of THRESHOLD_PRESETS) {
      expect(thresholdCss(preset.name, 'dark')).toMatch(/^#[0-9A-F]{6}$/);
      expect(thresholdCss(preset.name, 'light')).toMatch(/^#[0-9A-F]{6}$/);
    }
  });
});

describe('thresholdCss', () => {
  it('paints a palette name in the tone for the mode', () => {
    expect(thresholdCss('flare', 'dark')).toBe(brand.flare);
    expect(thresholdCss('flare', 'light')).toBe(tones.flare[700]);
  });

  it('maps the old preset hexes onto their brand hue', () => {
    expect(thresholdCss('#F44336', 'dark')).toBe(brand.flare);
    expect(thresholdCss('#4caf50', 'light')).toBe(tones.aurora[700]);
  });

  it('passes any other color through unchanged', () => {
    expect(thresholdCss('#123456', 'dark')).toBe('#123456');
    expect(thresholdCss('rebeccapurple', 'light')).toBe('rebeccapurple');
    expect(thresholdCss(undefined, 'light')).toBeUndefined();
  });

  it('treats an old preset hex and its palette name as the same swatch', () => {
    expect(sameThresholdColor('#FFC107', 'ember')).toBe(true);
    expect(sameThresholdColor('flare', 'aurora')).toBe(false);
  });
});
