import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import type { SpaceListItem } from 'src/hooks/useSpacesApi';

interface SpaceDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (name: string, description: string) => Promise<void>;
  /** The space being edited, or null to create one. */
  initial: SpaceListItem | null;
}

/**
 * Create or edit a space's name and description.
 *
 * Callers key it per target so the fields initialise from `initial` rather
 * than being copied in by an effect.
 */
export default function SpaceDialog({
  open,
  onClose,
  onSave,
  initial,
}: SpaceDialogProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClose = () => {
    setError(null);
    onClose();
  };

  const handleSave = async () => {
    setError(null);
    if (!name.trim()) {
      setError('Name is required.');
      return;
    }
    setSaving(true);
    try {
      await onSave(name.trim(), description.trim());
      handleClose();
    } catch (err) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setError((err as any)?.message ?? 'Failed to save.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>{initial ? 'Edit space' : 'New space'}</DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <TextField
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            fullWidth
            required
            autoFocus
          />
          <TextField
            label="Description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            fullWidth
            multiline
            minRows={2}
            helperText="Shown on the spaces index and the space's contents page."
          />
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={handleSave} variant="contained" disabled={saving}>
          {saving ? (
            <ConstellationSpinner size={20} />
          ) : initial ? (
            'Save'
          ) : (
            'Create space'
          )}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
