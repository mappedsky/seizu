import Info from '@mui/icons-material/Info';
import { IconButton, Tooltip } from '@mui/material';
import type { SxProps, Theme } from '@mui/material';

/**
 * Spread into the panel container's `sx` so the info button appears while
 * the panel is hovered or holds focus. Devices without hover show it always,
 * so there the caption clears it: a card header is inset on both sides, so a
 * centered caption stays centered, or drops below the button in a panel too
 * narrow to give up the width; a `.panel-info-caption` is inset on the right.
 *
 * The width check is a container query on the header's own wrapper, never on
 * the card: a container is a containing block for fixed-position descendants,
 * and on the card it would trap and clip the chart tooltips inside it.
 */
export const panelInfoRevealSx = {
  '&:hover .panel-info-btn, &:focus-within .panel-info-btn': { opacity: 1 },
  '@media (hover: none)': {
    '& :has(> .MuiCardHeader-root)': { containerType: 'inline-size' },
    '& .MuiCardHeader-root': { px: 5 },
    '& .panel-info-caption': { pr: 5 },
    '@container (max-width: 240px)': {
      '& .MuiCardHeader-root': { px: 2, pt: 5 },
    },
  },
} as const;

interface PanelInfoButtonProps {
  onClick: () => void;
  /** Distance from the panel's top-right corner, in px. */
  inset?: number;
}

/**
 * The "Show query details" button pinned to a panel's corner. It stays out of
 * sight until the panel is hovered or focused, but is always reachable and
 * visible from the keyboard.
 */
export default function PanelInfoButton({
  onClick,
  inset = 8,
}: PanelInfoButtonProps) {
  const sx: SxProps<Theme> = {
    position: 'absolute',
    top: inset,
    right: inset,
    opacity: 0,
    transition: 'opacity 0.2s',
    '&:focus-visible': { opacity: 1 },
    '@media (hover: none)': { opacity: 1 },
  };
  return (
    <Tooltip title="Show query details">
      <IconButton
        className="panel-info-btn"
        size="small"
        onClick={onClick}
        aria-label="Show query details"
        sx={sx}
      >
        <Info fontSize="small" />
      </IconButton>
    </Tooltip>
  );
}
