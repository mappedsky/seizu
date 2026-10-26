import '@fontsource-variable/inter';
import '@fontsource-variable/jetbrains-mono';

// Brand faces (AGENTS.MD → Branding), bundled rather than fetched from Google
// Fonts so they load under the app's `default-src 'self'` CSP.
export const fontFamilySans =
  '"Inter Variable", Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
export const fontFamilyMono =
  '"JetBrains Mono Variable", "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

/**
 * The smallest size any text in the app is set at: metadata chips, badges and
 * timestamps. Below this, labels stop being legible at normal viewing distance.
 */
export const fontSizeMin = '0.6875rem'; // 11px

const heading = {
  fontWeight: 600,
  textWrap: 'balance',
} as const;

const typography = {
  fontFamily: fontFamilySans,
  h1: { ...heading, fontSize: 35, lineHeight: 1.2, letterSpacing: '-0.02em' },
  h2: { ...heading, fontSize: 29, lineHeight: 1.25, letterSpacing: '-0.018em' },
  h3: { ...heading, fontSize: 24, lineHeight: 1.3, letterSpacing: '-0.014em' },
  h4: { ...heading, fontSize: 20, lineHeight: 1.35, letterSpacing: '-0.01em' },
  h5: { ...heading, fontSize: 16, lineHeight: 1.4, letterSpacing: '-0.006em' },
  h6: { ...heading, fontSize: 14, lineHeight: 1.4, letterSpacing: '-0.003em' },
  body1: { textWrap: 'pretty' },
  body2: { textWrap: 'pretty' },
  overline: {
    fontWeight: 500,
  },
};

export default typography;
