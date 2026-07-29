import { useState } from 'react';
import { Helmet } from 'react-helmet';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ListTable, {
  ListTableColumn,
  listTableActionColumnSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
  listTableTruncateSx,
} from 'src/components/ListTable';
import ListPageHeader from 'src/components/ListPageHeader';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import UserDisplay from 'src/components/UserDisplay';
import {
  useSpacesList,
  useSpaceMutations,
  SpaceListItem,
} from 'src/hooks/useSpacesApi';
import { usePermissions } from 'src/hooks/usePermissions';
import { pageContentSx } from 'src/theme/layout';

const descriptionColumnSx = { ...listTableSecondaryCellSx, width: '32%' };
const updatedAtColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const updatedByColumnSx = { ...listTableSecondaryCellSx, width: 150 };

// ---------------------------------------------------------------------------
// Create/Edit dialog
// ---------------------------------------------------------------------------

interface SpaceDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (name: string, description: string) => Promise<void>;
  initial: SpaceListItem | null;
}

function SpaceDialog({ open, onClose, onSave, initial }: SpaceDialogProps) {
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
      <DialogTitle>{initial ? 'Edit Space' : 'New Space'}</DialogTitle>
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
          />
        </Box>
        {!initial && (
          <Alert severity="info" sx={{ mt: 2 }}>
            An overview report named after the space is created with it, and
            becomes the space&apos;s landing page.
          </Alert>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={handleSave} variant="contained" disabled={saving}>
          {saving ? <ConstellationSpinner size={20} /> : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function Spaces() {
  const navigate = useNavigate();
  const hasPermission = usePermissions();
  const { spaces, loading, error } = useSpacesList();
  const { createSpace, updateSpace, deleteSpace } = useSpaceMutations();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<SpaceListItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SpaceListItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const canWrite = hasPermission('spaces:write');
  const canDelete = hasPermission('spaces:delete');

  const openCreate = () => {
    setEditTarget(null);
    setDialogOpen(true);
  };

  const openEdit = (space: SpaceListItem) => {
    setEditTarget(space);
    setDialogOpen(true);
  };

  const handleSave = async (name: string, description: string) => {
    if (editTarget) {
      await updateSpace(editTarget.space_id, name, description);
    } else {
      const created = await createSpace(name, description);
      navigate(`/app/spaces/${created.space_id}`);
    }
  };

  const openDelete = (space: SpaceListItem) => {
    setDeleteError(null);
    setDeleteTarget(space);
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteSpace(deleteTarget.space_id);
      setDeleteTarget(null);
    } catch (err) {
      // A non-empty space returns 409 with an actionable message; surface it
      // rather than a generic failure.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setDeleteError((err as any)?.message ?? 'Failed to delete space.');
    } finally {
      setDeleting(false);
    }
  };

  const rowActions = (space: SpaceListItem): RowMenuAction[] => [
    {
      key: 'open',
      label: 'Open',
      icon: <OpenInNewIcon fontSize="small" />,
      onClick: () => navigate(`/app/spaces/${space.space_id}`),
    },
    {
      key: 'edit',
      label: 'Edit',
      icon: <EditIcon fontSize="small" />,
      onClick: () => openEdit(space),
      disabled: !canWrite,
      tooltip: canWrite
        ? undefined
        : 'You do not have permission to edit spaces',
    },
    {
      key: 'delete',
      label: 'Delete',
      icon: <DeleteIcon fontSize="small" />,
      onClick: () => openDelete(space),
      disabled: !canDelete,
      tooltip: canDelete
        ? undefined
        : 'You do not have permission to delete spaces',
      destructive: true,
      dividerBefore: true,
    },
  ];

  const columns: ListTableColumn<SpaceListItem>[] = [
    {
      key: 'name',
      label: 'Name',
      minWidth: 180,
      render: (space) => (
        <Typography
          sx={{
            ...listTablePrimaryCellSx,
            ...listTableTruncateSx,
            cursor: 'pointer',
            '&:hover': { textDecoration: 'underline' },
          }}
          onClick={() => navigate(`/app/spaces/${space.space_id}`)}
        >
          {space.name}
        </Typography>
      ),
    },
    {
      key: 'description',
      label: 'Description',
      hideBelow: 'md',
      cellSx: descriptionColumnSx,
      render: (space) => space.description || '—',
    },
    {
      key: 'updated_at',
      label: 'Latest Update',
      hideBelow: 'xl',
      cellSx: updatedAtColumnSx,
      render: (space) => new Date(space.updated_at).toLocaleString(),
    },
    {
      key: 'updated_by',
      label: 'Updated By',
      hideBelow: 'lg',
      cellSx: updatedByColumnSx,
      render: (space) => (
        <UserDisplay userId={space.updated_by ?? space.created_by} />
      ),
    },
    {
      key: 'actions',
      align: 'right',
      resizable: false,
      cellSx: listTableActionColumnSx,
      headerSx: listTableActionColumnSx,
      render: (space) => <RowMenu actions={rowActions(space)} />,
    },
  ];

  return (
    <>
      <Helmet>
        <title>Spaces | Seizu</title>
      </Helmet>
      <Box sx={pageContentSx}>
        <ListPageHeader
          title="Spaces"
          action={
            canWrite && (
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={openCreate}
              >
                New space
              </Button>
            )
          }
        />

        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load spaces"
        >
          <ListTable
            rows={spaces}
            columns={columns}
            getRowKey={(space) => space.space_id}
            emptyMessage="No spaces yet. Create one above."
          />
        </ListViewState>
      </Box>

      <SpaceDialog
        key={editTarget?.space_id ?? 'new'}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onSave={handleSave}
        initial={editTarget}
      />

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete space?"
        deleting={deleting}
        error={deleteError}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
      >
        Permanently delete <strong>{deleteTarget?.name}</strong>, its overview
        report, and its sub-spaces? Any other reports must be moved out first.
        This cannot be undone.
      </ConfirmDeleteDialog>
    </>
  );
}

export default Spaces;
