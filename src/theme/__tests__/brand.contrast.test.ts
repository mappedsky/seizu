import { brand, chartColorsFor, HUES, hueFor, tones } from 'src/theme/brand';

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

const WHITE = '#FFFFFF';
const DARK_SURFACES = [brand.space, brand.spaceRaised];
const LIGHT_SURFACES = [brand.paper, WHITE];

// The usage rules stated above `tones` in brand.ts.
describe('brand tonal ramps', () => {
  it.each(HUES)('%s: steps 200–400 read on dark surfaces', (hue) => {
    for (const step of [200, 300, 400] as const) {
      for (const surface of DARK_SURFACES) {
        expect(contrast(tones[hue][step], surface)).toBeGreaterThanOrEqual(7);
      }
    }
  });

  it.each(HUES)('%s: steps 300–500 carry Space text', (hue) => {
    for (const step of [300, 400, 500] as const) {
      expect(contrast(tones[hue][step], brand.space)).toBeGreaterThanOrEqual(
        4.5,
      );
    }
  });

  it.each(HUES)(
    '%s: steps 700–900 read on light surfaces and carry white text',
    (hue) => {
      for (const step of [700, 800, 900] as const) {
        for (const surface of LIGHT_SURFACES) {
          expect(contrast(tones[hue][step], surface)).toBeGreaterThanOrEqual(
            4.5,
          );
        }
      }
    },
  );

  it.each(HUES)('%s: hueFor clears AA on its own surface', (hue) => {
    for (const surface of DARK_SURFACES) {
      expect(contrast(hueFor(hue, 'dark'), surface)).toBeGreaterThanOrEqual(
        4.5,
      );
    }
    for (const surface of LIGHT_SURFACES) {
      expect(contrast(hueFor(hue, 'light'), surface)).toBeGreaterThanOrEqual(
        4.5,
      );
    }
  });

  it('light chart colors carry white segment labels', () => {
    for (const color of chartColorsFor('light')) {
      expect(contrast(color, WHITE)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it('dark chart colors carry Space segment labels', () => {
    for (const color of chartColorsFor('dark')) {
      expect(contrast(color, brand.space)).toBeGreaterThanOrEqual(4.5);
    }
  });
});
