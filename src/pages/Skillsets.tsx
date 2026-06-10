import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import AddIcon from '@mui/icons-material/Add';
import PsychologyIcon from '@mui/icons-material/Psychology';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import ToggleOnIcon from '@mui/icons-material/ToggleOn';
import ToggleOffIcon from '@mui/icons-material/ToggleOff';
import {
  useSkillsetsList,
  useSkillsetMutations,
  SkillsetListItem,
  CreateSkillsetRequest,
  UpdateSkillsetRequest,
} from 'src/hooks/useSkillsetsApi';
import ListTable, {
  ListTableColumn,
  ListTableFilterGroup,
  listTableActionColumnSx,
  listTableMonoCellSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
  listTableTruncateSx,
} from 'src/components/ListTable';
import ListPageHeader from 'src/components/ListPageHeader';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissions } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

const LOWER_SNAKE_ID = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;
// Cap each slug component so the full "skillset__skill" MCP name stays <= 64
// chars (31 + "__" + 31). Mirrors MAX_SLUG_COMPONENT_LEN in mcp_config.py.
const MAX_SLUG_LEN = 31;

const descriptionColumnSx = { ...listTableSecondaryCellSx, width: '24%' };
const versionColumnSx = { ...listTableSecondaryCellSx, width: 96 };
const updatedAtColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const updatedByColumnSx = { ...listTableSecondaryCellSx, width: 150 };
const statusColumnSx = { width: 128 };

interface SkillsetDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (req: CreateSkillsetRequest | UpdateSkillsetRequest) => Promise<void>;
  initial: SkillsetListItem | null;
}

function SkillsetDialog({
  open,
  onClose,
  onSave,
  initial,
}: SkillsetDialogProps) {
  const [skillsetId, setSkillsetId] = useState(initial?.skillset_id ?? '');
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [comment, setComment] = useState('');
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
    if (!initial && !LOWER_SNAKE_ID.test(skillsetId.trim())) {
      setError('ID must be lower_snake_case.');
      return;
    }
    if (!initial && skillsetId.trim().length > MAX_SLUG_LEN) {
      setError(`ID must be at most ${MAX_SLUG_LEN} characters.`);
      return;
    }
    const req = initial
      ? ({
          name: name.trim(),
          description: description.trim(),
          enabled,
          comment: comment.trim() || null,
        } as UpdateSkillsetRequest)
      : ({
          skillset_id: skillsetId.trim(),
          name: name.trim(),
          description: description.trim(),
          enabled,
        } as CreateSkillsetRequest);
    setSaving(true);
    try {
      await onSave(req);
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
      <DialogTitle>{initial ? 'Edit Skillset' : 'New Skillset'}</DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {!initial && (
            <TextField
              label="ID"
              value={skillsetId}
              onChange={(e) => setSkillsetId(e.target.value)}
              fullWidth
              required
              helperText="lower_snake_case"
            />
          )}
          <TextField
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            fullWidth
            required
          />
          <TextField
            label="Description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            fullWidth
            multiline
            minRows={2}
          />
          <FormControlLabel
            control={
              <Switch
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
            }
            label="Enabled"
          />
          {initial && (
            <TextField
              label="Comment (optional)"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              fullWidth
              size="small"
              placeholder="Describe what changed..."
            />
          )}
        </Box>
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

function Skillsets() {
  const navigate = useNavigate();
  const { skillsets, loading, error, refresh } = useSkillsetsList();
  const { createSkillset, updateSkillset, deleteSkillset } =
    useSkillsetMutations();
  const hasPermission = usePermissions();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<SkillsetListItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SkillsetListItem | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);

  const canWrite = hasPermission('skillsets:write');
  const canDelete = hasPermission('skillsets:delete');

  const handleSave = async (
    req: CreateSkillsetRequest | UpdateSkillsetRequest,
  ) => {
    if (editTarget)
      await updateSkillset(
        editTarget.skillset_id,
        req as UpdateSkillsetRequest,
      );
    else await createSkillset(req as CreateSkillsetRequest);
    refresh();
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteSkillset(deleteTarget.skillset_id);
      setDeleteTarget(null);
      refresh();
    } finally {
      setDeleting(false);
    }
  };

  const rowActions = (item: SkillsetListItem): RowMenuAction[] => [
    {
      key: 'edit',
      label: 'Edit',
      icon: <EditIcon fontSize="small" />,
      onClick: () => {
        setEditTarget(item);
        setDialogOpen(true);
      },
      disabled: !canWrite,
      tooltip: canWrite
        ? undefined
        : 'You do not have permission to edit skillsets',
    },
    {
      key: 'skills',
      label: 'View skills',
      icon: <PsychologyIcon fontSize="small" />,
      onClick: () => navigate(`/app/skillsets/${item.skillset_id}/skills`),
    },
    {
      key: 'history',
      label: 'View history',
      icon: <HistoryIcon fontSize="small" />,
      onClick: () =>
        navigate(`/app/skillsets/${item.skillset_id}/history`, {
          state: { fromLabel: 'Skillsets' } satisfies BackState,
        }),
    },
    {
      key: 'delete',
      label: 'Delete',
      icon: <DeleteIcon fontSize="small" />,
      onClick: () => setDeleteTarget(item),
      disabled: !canDelete,
      tooltip: canDelete
        ? undefined
        : 'You do not have permission to delete skillsets',
      destructive: true,
      dividerBefore: true,
    },
  ];

  const columns: ListTableColumn<SkillsetListItem>[] = [
    {
      key: 'name',
      label: 'Name',
      cellSx: listTablePrimaryCellSx,
      render: (item) => (
        <Typography
          variant="body2"
          sx={[
            {
              cursor: 'pointer',
              fontWeight: 500,
              '&:hover': { textDecoration: 'underline' },
            },
            listTableTruncateSx,
          ]}
          onClick={() => navigate(`/app/skillsets/${item.skillset_id}/skills`)}
        >
          {item.name}
        </Typography>
      ),
    },
    {
      key: 'slug',
      label: 'Slug',
      hideBelow: 'lg',
      cellSx: listTableMonoCellSx,
      render: (item) => item.skillset_id,
    },
    {
      key: 'description',
      label: 'Description',
      hideBelow: 'md',
      cellSx: descriptionColumnSx,
      render: (item) => (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={listTableTruncateSx}
        >
          {item.description || '-'}
        </Typography>
      ),
    },
    {
      key: 'status',
      label: 'Status',
      cellSx: statusColumnSx,
      render: (item) => (
        <Chip
          label={item.enabled ? 'Enabled' : 'Disabled'}
          color={item.enabled ? 'success' : 'default'}
          size="small"
        />
      ),
    },
    {
      key: 'version',
      label: 'Version',
      hideBelow: 'sm',
      cellSx: versionColumnSx,
      render: (item) => `v${item.current_version}`,
    },
    {
      key: 'updated_at',
      label: 'Last updated',
      hideBelow: 'xl',
      cellSx: updatedAtColumnSx,
      render: (item) =>
        item.updated_at ? new Date(item.updated_at).toLocaleString() : '-',
    },
    {
      key: 'updated_by',
      label: 'Updated by',
      hideBelow: 'lg',
      cellSx: updatedByColumnSx,
      render: (item) =>
        item.updated_by ? (
          <UserDisplay userId={item.updated_by} />
        ) : (
          <UserDisplay userId={item.created_by} />
        ),
    },
    {
      key: 'actions',
      align: 'right',
      cellSx: listTableActionColumnSx,
      render: (item) => <RowMenu actions={rowActions(item)} />,
    },
  ];
  const filterGroups: ListTableFilterGroup<SkillsetListItem>[] = [
    {
      key: 'enabled',
      label: 'Enabled',
      icon: <ToggleOnIcon fontSize="small" />,
      options: [
        {
          key: 'enabled',
          label: 'Enabled',
          icon: <ToggleOnIcon fontSize="small" />,
          matches: (item) => item.enabled,
        },
        {
          key: 'disabled',
          label: 'Disabled',
          icon: <ToggleOffIcon fontSize="small" />,
          matches: (item) => !item.enabled,
        },
      ],
    },
  ];

  return (
    <>
      <Box sx={pageContentSx}>
        <ListPageHeader
          title="MCP Skillsets"
          action={
            canWrite && (
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={() => {
                  setEditTarget(null);
                  setDialogOpen(true);
                }}
              >
                New skillset
              </Button>
            )
          }
        />
        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load skillsets"
        >
          <ListTable
            rows={skillsets}
            columns={columns}
            getRowKey={(item) => item.skillset_id}
            emptyMessage="No skillsets yet. Create one above."
            filterGroups={filterGroups}
          />
        </ListViewState>
      </Box>
      <SkillsetDialog
        key={editTarget?.skillset_id ?? 'new'}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onSave={handleSave}
        initial={editTarget}
      />
      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete skillset?"
        deleting={deleting}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
      >
        Permanently delete <strong>{deleteTarget?.name}</strong> and all its
        skills and versions?
      </ConfirmDeleteDialog>
    </>
  );
}

export default Skillsets;
