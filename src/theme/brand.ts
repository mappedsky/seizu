// Seizu brand tokens — the source of truth for brand color hex values.
// (AGENTS.MD > Branding describes the brand narrative and points here for
// the values; the design file "Seizu Brand.html" is the upstream reference.)
//
// Core tokens (Space/Starlight/Ember/Paper) mirror the design file. Each
// accent is defined as a pair: a light tone for dark surfaces and a dark tone
// for light surfaces. On Paper the dark tones are not all text-safe:
// starlightDark, flareDark and pulsarDark clear 4.5:1, while emberDark (3.1),
// auroraDark (4.0) and beaconDark (4.2) are for fills, marks and large text.
// The chart palettes below build on these pairs — use them instead of
// introducing new one-off colors for data visualization.

export const brand = {
  // Core surfaces
  space: '#0A0F22',
  paper: '#F5F6FB',
  // Raised surface (cards, panels) on Space.
  spaceRaised: '#111A33',
  // Secondary text: on Space, and on Paper.
  mist: '#AAB8D6',
  slate: '#3B4A6B',

  // Primary — Starlight blue
  starlight: '#8FB4FF',
  starlightDark: '#3A5AA5',

  // Warm secondary — Ember amber
  ember: '#F5C16C',
  emberDark: '#C77B2A',

  // Extended constellation accents (for charts and supporting UI).
  // Each name corresponds to an astronomical object/phenomenon to keep
  // the brand vocabulary cohesive.
  aurora: '#7AD4B3', // teal-green
  auroraDark: '#2E8866',

  pulsar: '#C9A8FF', // violet
  pulsarDark: '#7352B8',

  flare: '#F08B7B', // coral / mars
  flareDark: '#B5463A',

  beacon: '#5DD1F0', // cyan
  beaconDark: '#1F7FA3',
} as const;

export type BrandToken = keyof typeof brand;

// Tonal ramps. Every hue shares one OKLCH lightness per step, so a step means
// the same thing in every hue and any two hues at the same step sit together:
//
//   on Space / spaceRaised  text and marks 200–400 (≥8.5:1)
//                           fills behind Space text 300–500 (≥6.4:1)
//   on Paper / white        text and marks 700–900 (≥5.0:1)
//                           fills behind white text 700–900 (≥5.0:1)
//
// The named tokens above are the brand's own values and stay canonical for
// logos and the docs; the ramps are what the app composes UI color from.
// `night` is the neutral ramp, tinted toward Space.
export const tones = {
  starlight: {
    50: '#F3F7FE',
    100: '#E4EDFE',
    200: '#CCDDFE',
    300: '#AEC9FE',
    400: '#8CB2FF',
    500: '#7199ED',
    600: '#5E81CA',
    700: '#4B6AA9',
    800: '#3A5389',
    900: '#293E6B',
  },
  ember: {
    50: '#FFF5E7',
    100: '#FFEACA',
    200: '#FFD595',
    300: '#F2C06E',
    400: '#DEA84B',
    500: '#C88F1F',
    600: '#AB7911',
    700: '#8E6202',
    800: '#714E01',
    900: '#553A01',
  },
  aurora: {
    50: '#E2FFF2',
    100: '#B8FFE3',
    200: '#A1F0D1',
    300: '#87DEBD',
    400: '#67C8A6',
    500: '#45B28E',
    600: '#369777',
    700: '#277D62',
    800: '#18634D',
    900: '#094B39',
  },
  pulsar: {
    50: '#F8F5FE',
    100: '#F0E8FF',
    200: '#E3D4FE',
    300: '#D3BBFF',
    400: '#C19EFA',
    500: '#AB85E5',
    600: '#916FC4',
    700: '#785AA4',
    800: '#5F4685',
    900: '#483367',
  },
  flare: {
    50: '#FEF4F2',
    100: '#FEE7E3',
    200: '#FED0C7',
    300: '#FEB3A6',
    400: '#FC907F',
    500: '#E67565',
    600: '#C56153',
    700: '#A44E42',
    800: '#853C31',
    900: '#672A22',
  },
  beacon: {
    50: '#ECFAFE',
    100: '#D1F4FE',
    200: '#A2E9FE',
    300: '#6CDAF8',
    400: '#42C5E5',
    500: '#03ADCE',
    600: '#0C92AE',
    700: '#077890',
    800: '#045F73',
    900: '#024857',
  },
  night: {
    50: '#F4F7FE',
    100: '#E6EBF9',
    200: '#D2D7E5',
    300: '#B9BDCB',
    400: '#9A9EAB',
    500: '#7B808D',
    600: '#5F636F',
    700: '#40475B',
    800: '#2A2F42',
    900: '#191E30',
    950: '#0E1323',
  },
} as const;

export type Hue = Exclude<keyof typeof tones, 'night'>;
export const HUES: readonly Hue[] = [
  'starlight',
  'ember',
  'aurora',
  'pulsar',
  'flare',
  'beacon',
];

/**
 * Which hue plays each role. Starlight leads; Ember is its warm complement;
 * Pulsar, the violet neighbour of Starlight, is the third voice. The status
 * roles reuse the same hues so state color never leaves the brand.
 */
export const roles = {
  primary: 'starlight',
  secondary: 'ember',
  tertiary: 'pulsar',
  danger: 'flare',
  warning: 'ember',
  success: 'aurora',
  info: 'beacon',
} as const satisfies Record<string, Hue>;

/**
 * A hue as text, marks or a filled surface on the current theme's background:
 * the brand's own light tone on dark surfaces, step 700 on light ones. Both
 * clear 4.5:1 against their surface, and step 700 clears it under white text.
 */
export function hueFor(hue: Hue, mode: 'light' | 'dark'): string {
  return mode === 'dark' ? brand[hue] : tones[hue][700];
}

// Chart palette — ordered for maximum hue separation between adjacent
// series. Six entries cover most dashboard panels; MUI X Charts cycles
// if more series are present.
export const chartPalette = {
  dark: [
    brand.starlight,
    brand.ember,
    brand.aurora,
    brand.pulsar,
    brand.flare,
    brand.beacon,
  ] as readonly string[],
  // Step 700 on light surfaces, so white labels on any segment read.
  light: [
    tones.starlight[700],
    tones.ember[700],
    tones.aurora[700],
    tones.pulsar[700],
    tones.flare[700],
    tones.beacon[700],
  ] as readonly string[],
} as const;

export const chartColorsFor = (mode: 'light' | 'dark'): readonly string[] =>
  mode === 'dark' ? chartPalette.dark : chartPalette.light;
