import type { ReactNode } from 'react';
import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Tooltip,
  Typography,
} from '@mui/material';
import type { Breakpoint } from '@mui/material/styles';
import CloseIcon from '@mui/icons-material/Close';
import { fontFamilyMono } from 'src/theme/typography';

// Shared read-only detail dialog: a title (with optional secondary label such
// as a version tag), a close button, a divider, and a content area. Use the
// exported `DetailSection` for labeled blocks and `DetailCodeBlock` for code.

export function DetailSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <Box sx={{ mb: 2 }}>
      <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 0.5 }}>
        {title}
      </Typography>
      {children}
    </Box>
  );
}

export function DetailCodeBlock({ children }: { children: ReactNode }) {
  return (
    <Box
      component="pre"
      sx={{
        m: 0,
        p: 1.5,
        borderRadius: 1,
        bgcolor: 'action.hover',
        fontSize: 12,
        fontFamily: fontFamilyMono,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        overflowX: 'auto',
      }}
    >
      {children}
    </Box>
  );
}

interface DetailDialogProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  /** Optional secondary label beside the title, e.g. a version tag. */
  secondary?: ReactNode;
  maxWidth?: Breakpoint;
  children: ReactNode;
}

export default function DetailDialog({
  open,
  onClose,
  title,
  secondary,
  maxWidth = 'md',
  children,
}: DetailDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth={maxWidth} fullWidth>
      <DialogTitle
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          pr: 1,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          {title}
          {secondary != null && (
            <Typography component="span" variant="body2" color="text.secondary">
              {secondary}
            </Typography>
          )}
        </Box>
        <Tooltip title="Close">
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <CloseIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </DialogTitle>

      <Divider />

      <DialogContent>{children}</DialogContent>
    </Dialog>
  );
}
