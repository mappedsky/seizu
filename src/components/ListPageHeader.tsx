import type { ReactNode } from 'react';
import { Box, Typography } from '@mui/material';

// Standard list-view top bar: an h1 title on the left and an optional action
// (typically a `<Button variant="contained">`) on the right.

interface ListPageHeaderProps {
  /** A string is wrapped in `<Typography variant="h1">`; a node is rendered as-is. */
  title: ReactNode;
  /** Right-aligned action(s), e.g. a "New …" button. */
  action?: ReactNode;
}

export default function ListPageHeader({ title, action }: ListPageHeaderProps) {
  return (
    <Box
      sx={{
        display: 'flex',
        flexWrap: 'wrap',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: 2,
        mb: 3,
      }}
    >
      {typeof title === 'string' ? (
        <Typography variant="h1">{title}</Typography>
      ) : (
        title
      )}
      {action && (
        // Wraps below the title when both don't fit, rather than squeezing
        // the action's label onto several lines.
        <Box
          sx={{ flexShrink: 0, '& .MuiButton-root': { whiteSpace: 'nowrap' } }}
        >
          {action}
        </Box>
      )}
    </Box>
  );
}
