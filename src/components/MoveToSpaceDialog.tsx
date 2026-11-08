import { useState } from 'react';
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
  Typography,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import {
  useSpacesList,
  useSubspacesList,
  type SpaceListItem,
} from 'src/hooks/useSpacesApi';

const NONE = '__none__';

/** Display names of the chosen placement, for a caller's confirmation. */
export interface MovePlacementNames {
  spaceName: string | null;
  subspaceName: string | null;
}

interface MoveToSpaceDialogProps {
  open: boolean;
  reportName: string;
  /** Overrides the default “Move “name”” title, e.g. for a batch. */
  title?: string;
  currentSpaceId: string | null;
  currentSubspaceId: string | null;
  /**
   * Offer only spaces other than this one, and no “None”: the picker is for
   * moving out of a space the caller already lets the user rearrange and
   * leave by other means.
   */
  excludeSpaceId?: string;
  onClose: () => void;
  onConfirm: (
    spaceId: string | null,
    subspaceId: string | null,
    names: MovePlacementNames,
  ) => Promise<void>;
}

/**
 * Space/sub-space picker shared by the reports list and the report view.
 *
 * The sub-space select is disabled until a space is chosen and resets whenever
 * the space changes, mirroring the API rules so a user cannot construct a
 * request the backend will reject.
 *
 * Callers mount this per open, so the selection starts from the report's
 * current placement by initialisation rather than by an effect copying the
 * props into state after the first render has already shown the last one.
 */
function MoveToSpaceDialog({
  open,
  reportName,
  title,
  currentSpaceId,
  currentSubspaceId,
  excludeSpaceId,
  onClose,
  onConfirm,
}: MoveToSpaceDialogProps) {
  const { spaces: allSpaces, loading: spacesLoading } = useSpacesList();
  const elsewhere = excludeSpaceId !== undefined;
  const spaces = elsewhere
    ? allSpaces.filter((space) => space.space_id !== excludeSpaceId)
    : allSpaces;
  const [spaceId, setSpaceId] = useState<string | null>(currentSpaceId);
  const [subspaceId, setSubspaceId] = useState<string | null>(
    currentSubspaceId,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { subspaces, loading: subspacesLoading } = useSubspacesList(spaceId);

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
      const space = spaces.find((s) => s.space_id === spaceId);
      const subspace = subspaces.find((s) => s.subspace_id === subspaceId);
      await onConfirm(spaceId, spaceId ? subspaceId : null, {
        spaceName: space?.name ?? null,
        subspaceName: spaceId ? (subspace?.name ?? null) : null,
      });
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
      <DialogTitle>{title ?? `Move “${reportName}”`}</DialogTitle>
      <DialogContent>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        {elsewhere && !spacesLoading && spaces.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            There are no other spaces to move to. Create one from the Spaces
            page first.
          </Typography>
        )}
        <FormControl fullWidth sx={{ mt: 1 }}>
          <InputLabel id="move-space-label">Space</InputLabel>
          <Select
            labelId="move-space-label"
            label="Space"
            // Empty rather than a placeholder item, so the label is the prompt.
            value={spaceId ?? (elsewhere ? '' : NONE)}
            disabled={spacesLoading || saving}
            onChange={(e) => handleSpaceChange(e.target.value)}
          >
            {!elsewhere && <MenuItem value={NONE}>None</MenuItem>}
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
            <MenuItem value={NONE}>{elsewhere ? 'Top level' : 'None'}</MenuItem>
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
        <Button
          variant="contained"
          onClick={handleConfirm}
          disabled={saving || (elsewhere && !spaceId)}
        >
          {saving ? <ConstellationSpinner size={20} /> : 'Move'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default MoveToSpaceDialog;
