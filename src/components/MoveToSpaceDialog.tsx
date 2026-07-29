import { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import {
  useSpacesList,
  useSubspacesList,
  type SpaceListItem,
} from 'src/hooks/useSpacesApi';

const NONE = '__none__';

interface MoveToSpaceDialogProps {
  open: boolean;
  reportName: string;
  currentSpaceId: string | null;
  currentSubspaceId: string | null;
  onClose: () => void;
  onConfirm: (
    spaceId: string | null,
    subspaceId: string | null,
  ) => Promise<void>;
}

/**
 * Space/sub-space picker shared by the reports list and the report view.
 *
 * The sub-space select is disabled until a space is chosen and resets whenever
 * the space changes, mirroring the API rules so a user cannot construct a
 * request the backend will reject.
 */
function MoveToSpaceDialog({
  open,
  reportName,
  currentSpaceId,
  currentSubspaceId,
  onClose,
  onConfirm,
}: MoveToSpaceDialogProps) {
  const { spaces, loading: spacesLoading } = useSpacesList();
  const [spaceId, setSpaceId] = useState<string | null>(currentSpaceId);
  const [subspaceId, setSubspaceId] = useState<string | null>(
    currentSubspaceId,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { subspaces, loading: subspacesLoading } = useSubspacesList(spaceId);

  useEffect(() => {
    if (open) {
      setSpaceId(currentSpaceId);
      setSubspaceId(currentSubspaceId);
      setError(null);
    }
  }, [open, currentSpaceId, currentSubspaceId]);

  const handleSpaceChange = (value: string) => {
    const next = value === NONE ? null : value;
    setSpaceId(next);
    // Replace semantics: a sub-space only means something inside its own
    // space, so changing space always drops it.
    setSubspaceId(null);
  };

  const handleConfirm = async () => {
    setSaving(true);
    setError(null);
    try {
      await onConfirm(spaceId, spaceId ? subspaceId : null);
      onClose();
    } catch (err) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setError((err as any)?.message ?? 'Failed to move report.');
    } finally {
      setSaving(false);
    }
  };

  const spaceLabel = (space: SpaceListItem) => space.name;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Move “{reportName}”</DialogTitle>
      <DialogContent>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <FormControl fullWidth sx={{ mt: 1 }}>
          <InputLabel id="move-space-label">Space</InputLabel>
          <Select
            labelId="move-space-label"
            label="Space"
            value={spaceId ?? NONE}
            disabled={spacesLoading || saving}
            onChange={(e) => handleSpaceChange(e.target.value)}
          >
            <MenuItem value={NONE}>None</MenuItem>
            {spaces.map((space) => (
              <MenuItem key={space.space_id} value={space.space_id}>
                {spaceLabel(space)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl fullWidth sx={{ mt: 2 }}>
          <InputLabel id="move-subspace-label">Sub-space</InputLabel>
          <Select
            labelId="move-subspace-label"
            label="Sub-space"
            value={subspaceId ?? NONE}
            disabled={!spaceId || subspacesLoading || saving}
            onChange={(e) =>
              setSubspaceId(e.target.value === NONE ? null : e.target.value)
            }
          >
            <MenuItem value={NONE}>None</MenuItem>
            {subspaces.map((subspace) => (
              <MenuItem key={subspace.subspace_id} value={subspace.subspace_id}>
                {subspace.name}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button variant="contained" onClick={handleConfirm} disabled={saving}>
          {saving ? <ConstellationSpinner size={20} /> : 'Move'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default MoveToSpaceDialog;
