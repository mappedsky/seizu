import { Fragment, useState, type MouseEvent, type ReactNode } from 'react';
import {
  Divider,
  IconButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Tooltip,
} from '@mui/material';
import MoreVertIcon from '@mui/icons-material/MoreVert';

// Shared per-row overflow menu for list views. This component owns the
// conventions that used to be re-implemented on every list page: the three-dot
// trigger in a tooltip, the bottom/top-right anchor, the minimum menu width,
// destructive actions fenced off below a divider and rendered in error color,
// and disabled items wrapped in a tooltip + span so the reason still shows on
// hover. Callers describe the menu declaratively via `actions`.

export interface RowMenuAction {
  key: string;
  label: ReactNode;
  icon?: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  /** Tooltip shown on hover — typically the reason a disabled item is blocked. */
  tooltip?: string;
  /** Destructive action: rendered in error color (when enabled). */
  destructive?: boolean;
  /** Render a divider above this item — used to separate destructive actions. */
  dividerBefore?: boolean;
}

interface RowMenuProps {
  actions: RowMenuAction[];
  /**
   * Trigger tooltip + aria-label. A per-row label inside a ListTable needs
   * `textual: false` on that column, or the cell's own hover tooltip repeats it.
   */
  label?: string;
  /** Minimum menu width in px (180 default; reports use 200 for longer labels). */
  menuMinWidth?: number;
}

export default function RowMenu({
  actions,
  label = 'More actions',
  menuMinWidth = 180,
}: RowMenuProps) {
  const [anchor, setAnchor] = useState<null | HTMLElement>(null);
  const close = () => setAnchor(null);

  return (
    <>
      <Tooltip title={label}>
        <IconButton
          size="small"
          aria-label={label}
          onClick={(event: MouseEvent<HTMLElement>) =>
            setAnchor(event.currentTarget)
          }
        >
          <MoreVertIcon fontSize="small" />
        </IconButton>
      </Tooltip>

      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={close}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        slotProps={{ paper: { sx: { minWidth: menuMinWidth } } }}
      >
        {actions.map((action) => {
          const showError = Boolean(action.destructive && !action.disabled);
          const errorSx = showError ? { color: 'error.main' } : undefined;
          const item = (
            <MenuItem
              onClick={() => {
                action.onClick();
                close();
              }}
              disabled={action.disabled}
              sx={errorSx}
            >
              {action.icon !== undefined && (
                <ListItemIcon sx={errorSx}>{action.icon}</ListItemIcon>
              )}
              <ListItemText>{action.label}</ListItemText>
            </MenuItem>
          );

          return (
            <Fragment key={action.key}>
              {action.dividerBefore && <Divider />}
              {action.tooltip ? (
                <Tooltip title={action.tooltip} placement="left">
                  <span>{item}</span>
                </Tooltip>
              ) : (
                item
              )}
            </Fragment>
          );
        })}
      </Menu>
    </>
  );
}
