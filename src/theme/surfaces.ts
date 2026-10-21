import { alpha, type Components, type Theme } from '@mui/material/styles';
import { brand } from 'src/theme/brand';

/**
 * The base corner radius, in px. `borderRadius: n` in `sx` is a multiple of
 * it, so controls (1) stay tighter than the panels and dialogs that hold them.
 */
export const shape = { borderRadius: 8 };

/** Radius of the surfaces that hold content: cards, report panels, dialogs. */
const SURFACE_RADIUS = shape.borderRadius * 1.5;

/**
 * Cards are flat: a faint starlight wash and a hairline edge instead of an
 * opaque raised slab with a shadow, so a report panel sits on the canvas the
 * way the markdown beside it does. Light mode keeps white cards on Paper.
 */
export const surfaceComponents: Components<Theme> = {
  MuiCard: {
    defaultProps: { elevation: 0 },
    styleOverrides: {
      root: ({ theme }) => ({
        borderRadius: SURFACE_RADIUS,
        border: `1px solid ${
          theme.palette.mode === 'dark'
            ? alpha(brand.starlight, 0.12)
            : theme.palette.divider
        }`,
        ...(theme.palette.mode === 'dark' && {
          backgroundColor: alpha(brand.starlight, 0.04),
        }),
      }),
    },
  },
  MuiDialog: {
    styleOverrides: { paper: { borderRadius: SURFACE_RADIUS } },
  },
};
