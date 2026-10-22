import { alpha, type PaletteOptions } from '@mui/material/styles';
import { brand, hueFor, roles, type Hue } from 'src/theme/brand';

declare module '@mui/material/styles' {
  interface Palette {
    tertiary: Palette['primary'];
  }
  interface PaletteOptions {
    tertiary?: PaletteOptions['primary'];
  }
}

function role(hue: Hue, mode: 'light' | 'dark') {
  return { main: hueFor(hue, mode) };
}

/**
 * Hover, selected and disabled washes drawn from the primary hue at the brand
 * opacity rungs, instead of MUI's neutral black or white, so every tinted
 * surface (code wells, selected rows, disabled fills) shares the canvas's hue.
 */
function tintedActions(
  hue: string,
  [hover, selected, strong]: [number, number, number],
): PaletteOptions['action'] {
  return {
    // Disabled controls stay legible as controls, just quieter.
    disabled: alpha(hue, 0.55),
    hover: alpha(hue, hover),
    selected: alpha(hue, selected),
    focus: alpha(hue, strong),
    disabledBackground: alpha(hue, strong),
  };
}

/**
 * The MUI palette for one mode, composed from the brand roles. Every color
 * here comes from `brand.ts`; status colors use the same hues as the rest of
 * the UI rather than MUI's defaults.
 */
export function paletteFor(mode: 'light' | 'dark'): PaletteOptions {
  const status = {
    error: role(roles.danger, mode),
    warning: role(roles.warning, mode),
    success: role(roles.success, mode),
    info: role(roles.info, mode),
    tertiary: role(roles.tertiary, mode),
  };
  return {
    // getContrastText (filled chips, buttons, graph nodes) picks dark text
    // wherever white would fall below WCAG AA for normal text.
    contrastThreshold: 4.5,
    ...status,
    ...(mode === 'dark'
      ? {
          mode,
          primary: { main: brand.starlight, contrastText: brand.space },
          secondary: { main: brand.ember, contrastText: brand.space },
          background: { default: brand.space, paper: brand.spaceRaised },
          text: {
            primary: brand.paper,
            secondary: brand.mist,
            disabled: alpha(brand.mist, 0.5),
          },
          divider: alpha(brand.starlight, 0.15),
          action: tintedActions(brand.starlight, [0.08, 0.12, 0.12]),
        }
      : {
          mode,
          primary: { main: brand.starlightDark, contrastText: '#fff' },
          // White text on emberDark falls below AA; Space clears it.
          secondary: { main: brand.emberDark, contrastText: brand.space },
          background: { default: brand.paperCanvas, paper: '#fff' },
          text: {
            primary: brand.space,
            secondary: brand.slate,
            disabled: alpha(brand.slate, 0.5),
          },
          divider: alpha(brand.starlightDark, 0.16),
          action: tintedActions(brand.starlightDark, [0.04, 0.08, 0.12]),
        }),
  };
}
