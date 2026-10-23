import { brand, hueFor } from 'src/theme/brand';
import {
  compareSeverity,
  isSeverityField,
  isSeverityScale,
  nonSeverityPalette,
  SEVERITY_LEVELS,
  severityColor,
  severityHues,
  severityOf,
} from 'src/theme/severity';
import { severityPieData } from 'src/components/reports/CypherPie';

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe('severity', () => {
  it('reads the spellings scanners use, in any case', () => {
    expect(severityOf('CRITICAL')).toBe('critical');
    expect(severityOf(' Moderate ')).toBe('medium');
    expect(severityOf('informational')).toBe('info');
    expect(severityOf('urgent')).toBeUndefined();
    expect(severityOf(3)).toBeUndefined();
  });

  it('orders levels most urgent first, unknown values last', () => {
    expect(
      ['LOW', 'other', 'CRITICAL', 'MEDIUM', 'HIGH'].sort(compareSeverity),
    ).toEqual(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'other']);
  });

  it('treats a breakdown as severity only when every label is a level', () => {
    expect(isSeverityScale(['HIGH', 'low'])).toBe(true);
    expect(isSeverityScale(['HIGH', 'data'])).toBe(false);
    expect(isSeverityScale([])).toBe(false);
  });

  it('recognises severity fields by name', () => {
    expect(isSeverityField('base_severity')).toBe(true);
    expect(isSeverityField('Criticality')).toBe(true);
    expect(isSeverityField('status')).toBe(false);
  });

  it.each([
    ['dark', [brand.space, brand.spaceRaised]],
    ['light', [brand.paperCanvas, '#FFFFFF']],
  ] as const)(
    'draws every level visibly on %s surfaces and holds Space labels',
    (mode, surfaces) => {
      for (const level of SEVERITY_LEVELS) {
        const color = severityColor(level, mode);
        for (const surface of surfaces) {
          expect(contrast(color, surface)).toBeGreaterThanOrEqual(3);
        }
        expect(contrast(color, brand.space)).toBeGreaterThanOrEqual(4.5);
      }
    },
  );

  it('keeps the levels that need action apart from each other', () => {
    for (const mode of ['light', 'dark'] as const) {
      const urgent = ['critical', 'high', 'medium', 'low'] as const;
      const colors = new Set(urgent.map((l) => severityColor(l, mode)));
      expect(colors.size).toBe(urgent.length);
    }
  });

  it('leaves other categories hues no level uses', () => {
    for (const mode of ['light', 'dark'] as const) {
      const palette = nonSeverityPalette(mode);
      expect(palette.length).toBeGreaterThan(0);
      for (const hue of Object.values(severityHues)) {
        if (hue !== 'night') expect(palette).not.toContain(hueFor(hue, mode));
      }
    }
  });

  it('orders and colors a severity pie, and leaves others alone', () => {
    const pie = severityPieData(
      [
        { id: 'MEDIUM', value: 5, label: 'MEDIUM' },
        { id: 'CRITICAL', value: 1, label: 'CRITICAL' },
      ],
      'dark',
    );
    expect(pie.map((item) => item.id)).toEqual(['CRITICAL', 'MEDIUM']);
    expect(pie[0].color).toBe(severityColor('critical', 'dark'));

    const other = [{ id: 'data', value: 1, label: 'data' }];
    expect(severityPieData(other, 'dark')).toBe(other);
  });
});
