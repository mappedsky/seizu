import { alpha, type Components, type Theme } from '@mui/material/styles';
import type {} from '@mui/x-data-grid/themeAugmentation';
import { brand } from 'src/theme/brand';

/**
 * The base corner radius, in px. `borderRadius: n` in `sx` is a multiple of
 * it, so controls (1) stay tighter than the panels and dialogs that hold them.
 */
export const shape = { borderRadius: 8 };

/** Radius of the surfaces that hold content: cards, report panels, dialogs. */
const SURFACE_RADIUS = shape.borderRadius * 1.5;

/** What a dark-mode card's wash looks like over the Space canvas, opaque. */
const CARD_ON_CANVAS_DARK = `color-mix(in srgb, ${brand.starlight} 4%, ${brand.space})`;

/**
 * Cards and outlined Papers are flat: a faint starlight wash and a hairline
 * edge instead of an opaque raised slab with a shadow, so a report panel sits
 * on the canvas the way the markdown beside it does. Light mode keeps white
 * cards on Paper.
 */
const framedSurface = (theme: Theme) => ({
  borderRadius: SURFACE_RADIUS,
  border: `1px solid ${
    theme.palette.mode === 'dark'
      ? alpha(brand.starlight, 0.12)
      : theme.palette.divider
  }`,
  ...(theme.palette.mode === 'dark' && {
    backgroundColor: alpha(brand.starlight, 0.04),
  }),
});

export const surfaceComponents: Components<Theme> = {
  MuiCard: {
    defaultProps: { elevation: 0 },
    styleOverrides: { root: ({ theme }) => framedSurface(theme) },
  },
  // An outlined Paper frames content the way a card does (the table panel,
  // list tables, markdown tables), so it takes the same surface.
  MuiPaper: {
    styleOverrides: { outlined: ({ theme }) => framedSurface(theme) },
  },
  // The top bar shares the sidebar's surface and edges the page with a
  // hairline, so the frame recedes and the content carries the color.
  MuiAppBar: {
    defaultProps: { color: 'transparent', elevation: 0 },
    styleOverrides: {
      root: ({ theme }) => ({
        backgroundColor: theme.palette.background.paper,
        borderBottom: `1px solid ${theme.palette.divider}`,
        color: theme.palette.text.primary,
      }),
    },
  },
  // A data grid paints its own opaque background, mixed from the paper color.
  // In dark mode that is lighter than the card around it, so it takes the
  // color a card renders on the canvas instead; it stays opaque because the
  // column headers are sticky over the rows.
  MuiDataGrid: {
    styleOverrides: {
      // Nested so it outranks the grid's own variables class, which is
      // injected after this one at the same specificity.
      root: ({ theme }) =>
        theme.palette.mode === 'dark'
          ? {
              '&.MuiDataGrid-root': {
                '--DataGrid-t-color-background-base': CARD_ON_CANVAS_DARK,
                '--DataGrid-t-header-background-base': CARD_ON_CANVAS_DARK,
                '--DataGrid-t-cell-background-pinned': CARD_ON_CANVAS_DARK,
              },
            }
          : {},
    },
  },
  MuiDialog: {
    styleOverrides: { paper: { borderRadius: SURFACE_RADIUS } },
  },
};
