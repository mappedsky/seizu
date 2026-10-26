import type { ReactNode } from 'react';
import { Box } from '@mui/material';
import type { SxProps, Theme } from '@mui/material';
import { DASHBOARD_NAVBAR_HEIGHT } from 'src/components/dashboardLayoutConstants';

export const SIDE_PANEL_WIDTH = 260;
export const SIDE_PANEL_RAIL_WIDTH = 40;

interface SidePanelFrameProps {
  open: boolean;
  /** Float over the page rather than sit beside it (see `useSidePanel`). */
  overlay: boolean;
  /** Called on a backdrop tap or Escape while floating. */
  onClose: () => void;
  children: ReactNode;
}

const columnSx = {
  display: 'flex',
  flexDirection: 'column',
  height: '100%',
  overflow: 'hidden',
  borderRight: 1,
  borderColor: 'divider',
  transition: 'width 0.2s ease',
} as const;

/**
 * The container of a page's collapsible left panel, which collapses to a
 * 40px rail. Beside the page it takes its own width out of the row. As an
 * overlay only the rail is held in the row, and the open panel covers the
 * content to its right behind a backdrop.
 */
export default function SidePanelFrame({
  open,
  overlay,
  onClose,
  children,
}: SidePanelFrameProps) {
  if (!overlay) {
    return (
      <Box
        sx={{
          ...columnSx,
          flexShrink: 0,
          width: open ? SIDE_PANEL_WIDTH : SIDE_PANEL_RAIL_WIDTH,
        }}
      >
        {children}
      </Box>
    );
  }

  const panelSx: SxProps<Theme> = {
    ...columnSx,
    position: 'absolute',
    inset: '0 auto 0 0',
    zIndex: (theme) => theme.zIndex.drawer,
    width: open
      ? `min(${SIDE_PANEL_WIDTH + 60}px, calc(100vw - 48px))`
      : SIDE_PANEL_RAIL_WIDTH,
    bgcolor: 'background.default',
    boxShadow: open ? 8 : 'none',
  };

  return (
    <Box
      sx={{
        position: 'relative',
        flexShrink: 0,
        width: SIDE_PANEL_RAIL_WIDTH,
        height: '100%',
      }}
      onKeyDown={(event) => {
        if (open && event.key === 'Escape') onClose();
      }}
    >
      {open && (
        <Box
          aria-hidden
          onClick={onClose}
          sx={{
            position: 'fixed',
            inset: `${DASHBOARD_NAVBAR_HEIGHT}px 0 0 0`,
            zIndex: (theme) => theme.zIndex.drawer - 1,
            bgcolor: 'rgba(0, 0, 0, 0.5)',
          }}
        />
      )}
      <Box sx={panelSx}>{children}</Box>
    </Box>
  );
}
