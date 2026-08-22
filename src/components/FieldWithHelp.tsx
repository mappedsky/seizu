import type { ReactNode } from 'react';
import { Box, IconButton, Tooltip } from '@mui/material';
import HelpOutlineIcon from '@mui/icons-material/HelpOutlineOutlined';

/**
 * A form control with a trailing help affordance.
 *
 * The tooltip carries guidance that would crowd a `helperText` line. `label`
 * names the control for assistive technology; it is not rendered, so the
 * control keeps its own visible label.
 */
export default function FieldWithHelp({
  label,
  tooltip,
  children,
}: {
  label: string;
  tooltip: ReactNode;
  children: ReactNode;
}) {
  return (
    <Box
      sx={{
        alignItems: 'flex-start',
        display: 'flex',
        flex: 1,
        gap: 0.5,
        // The help button is a fixed-width sibling of the control, so the
        // control itself must be free to shrink rather than push the row wide.
        minWidth: 0,
        width: '100%',
      }}
    >
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
      <Tooltip title={tooltip} placement="top" arrow describeChild>
        <IconButton aria-label={`Help for ${label}`} size="small">
          <HelpOutlineIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Box>
  );
}
