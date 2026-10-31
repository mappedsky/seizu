import type { ReactNode } from 'react';
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';

// Standard destructive confirmation dialog (maxWidth="xs"). Body is passed as
// children so callers can highlight the target name with <strong>.

interface ConfirmDeleteDialogProps {
  open: boolean;
  title?: string;
  children: ReactNode;
  confirmLabel?: string;
  /** The dismiss button, when "Cancel" would read as the action itself. */
  cancelLabel?: string;
  deleting?: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: () => void;
}

export default function ConfirmDeleteDialog({
  open,
  title = 'Delete?',
  children,
  confirmLabel = 'Delete',
  cancelLabel = 'Cancel',
  deleting = false,
  error = null,
  onClose,
  onConfirm,
}: ConfirmDeleteDialogProps) {
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <DialogContentText>{children}</DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={deleting}>
          {cancelLabel}
        </Button>
        <Button
          variant="contained"
          color="error"
          onClick={onConfirm}
          disabled={deleting}
        >
          {deleting ? <ConstellationSpinner size={20} /> : confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
