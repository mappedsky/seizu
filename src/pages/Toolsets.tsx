import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import BuildIcon from '@mui/icons-material/Build';
import BadgeIcon from '@mui/icons-material/Badge';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import PersonOutlineIcon from '@mui/icons-material/Person';
import ToggleOnIcon from '@mui/icons-material/ToggleOn';
import ToggleOffIcon from '@mui/icons-material/ToggleOff';
import {
  useToolsetsList,
  useToolsetMutations,
  ToolsetListItem,
  CreateToolsetRequest,
  UpdateToolsetRequest,
} from 'src/hooks/useToolsetsApi';
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

// ---------------------------------------------------------------------------
// Built-in synthetic toolsets
// ---------------------------------------------------------------------------
// The backend surfaces built-in groups through the same /api/v1/toolsets
// endpoint with ids prefixed by `__builtin_`.  We only need the prefix here
// to hide edit/delete actions on those rows.

const BUILTIN_PREFIX = '__builtin_';

const isBuiltinToolset = (id: string): boolean =>
  id.startsWith(BUILTIN_PREFIX) && id.endsWith('__');

const LOWER_SNAKE_ID = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;
// Cap each slug component so the full "toolset__tool" MCP name stays <= 64 chars
// (31 + "__" + 31). Mirrors MAX_SLUG_COMPONENT_LEN in reporting/schema/mcp_config.py.
const MAX_SLUG_LEN = 31;

const descriptionColumnSx = { ...listTableSecondaryCellSx, width: '24%' };
const typeColumnSx = { width: 144 };
const statusColumnSx = { width: 136 };
const versionColumnSx = { ...listTableSecondaryCellSx, width: 96 };
const updatedAtColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const updatedByColumnSx = { ...listTableSecondaryCellSx, width: 150 };

// ---------------------------------------------------------------------------
// Create/Edit dialog
// ---------------------------------------------------------------------------

interface ToolsetDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (req: CreateToolsetRequest | UpdateToolsetRequest) => Promise<void>;
  initial: ToolsetListItem | null;
}

function ToolsetDialog({ open, onClose, onSave, initial }: ToolsetDialogProps) {
  const [toolsetId, setToolsetId] = useState(initial?.toolset_id ?? '');
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
    if (!initial && !LOWER_SNAKE_ID.test(toolsetId.trim())) {
      setError('ID must be lower_snake_case.');
      return;
    }
    if (!initial && toolsetId.trim().length > MAX_SLUG_LEN) {
      setError(`ID must be at most ${MAX_SLUG_LEN} characters.`);
      return;
    }
    const req = initial
      ? ({
          name: name.trim(),
          description: description.trim(),
          enabled,
          comment: comment.trim() || null,
        } as UpdateToolsetRequest)
      : ({
          toolset_id: toolsetId.trim(),
          name: name.trim(),
          description: description.trim(),
          enabled,
        } as CreateToolsetRequest);
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
      <DialogTitle>{initial ? 'Edit Toolset' : 'New Toolset'}</DialogTitle>
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
              value={toolsetId}
              onChange={(e) => setToolsetId(e.target.value)}
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
              placeholder="Describe what changed…"
            />
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={handleSave} variant="contained" disabled={saving}>
          {saving ? <CircularProgress size={20} /> : 'Save'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function Toolsets() {
  const navigate = useNavigate();
  const { toolsets, loading, error, refresh } = useToolsetsList();
  const { createToolset, updateToolset, deleteToolset } = useToolsetMutations();
  const hasPermission = usePermissions();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<ToolsetListItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ToolsetListItem | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);

  const canWrite = hasPermission('toolsets:write');
  const canDelete = hasPermission('toolsets:delete');

  const openCreate = () => {
    setEditTarget(null);
    setDialogOpen(true);
  };

  const openEdit = (item: ToolsetListItem) => {
    setEditTarget(item);
    setDialogOpen(true);
  };

  const handleSave = async (
    req: CreateToolsetRequest | UpdateToolsetRequest,
  ) => {
    if (editTarget) {
      await updateToolset(editTarget.toolset_id, req as UpdateToolsetRequest);
    } else {
      await createToolset(req as CreateToolsetRequest);
    }
    refresh();
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteToolset(deleteTarget.toolset_id);
      setDeleteTarget(null);
      refresh();
    } catch {
      // dialog stays open so user can retry
    } finally {
      setDeleting(false);
    }
  };

  const rowActions = (item: ToolsetListItem): RowMenuAction[] => {
    const isBuiltin = isBuiltinToolset(item.toolset_id);
    return [
      {
        key: 'edit',
        label: 'Edit',
        icon: <EditIcon fontSize="small" />,
        onClick: () => openEdit(item),
        disabled: isBuiltin || !canWrite,
        tooltip: isBuiltin
          ? 'Built-in toolsets cannot be edited'
          : !canWrite
            ? 'You do not have permission to edit toolsets'
            : undefined,
      },
      {
        key: 'tools',
        label: 'View tools',
        icon: <BuildIcon fontSize="small" />,
        onClick: () => navigate(`/app/toolsets/${item.toolset_id}/tools`),
      },
      {
        key: 'history',
        label: 'View history',
        icon: <HistoryIcon fontSize="small" />,
        onClick: () =>
          navigate(`/app/toolsets/${item.toolset_id}/history`, {
            state: { fromLabel: 'Toolsets' } satisfies BackState,
          }),
        disabled: isBuiltin,
        tooltip: isBuiltin
          ? 'Built-in toolsets have no version history'
          : undefined,
      },
      {
        key: 'delete',
        label: 'Delete',
        icon: <DeleteIcon fontSize="small" />,
        onClick: () => setDeleteTarget(item),
        disabled: isBuiltin || !canDelete,
        tooltip: isBuiltin
          ? 'Built-in toolsets cannot be deleted'
          : !canDelete
            ? 'You do not have permission to delete toolsets'
            : undefined,
        destructive: true,
        dividerBefore: true,
      },
    ];
  };

  const allRows: ToolsetListItem[] = toolsets;
  const columns: ListTableColumn<ToolsetListItem>[] = [
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
          onClick={() => navigate(`/app/toolsets/${item.toolset_id}/tools`)}
        >
          {item.name}
        </Typography>
      ),
    },
    {
      key: 'type',
      label: 'Type',
      cellSx: typeColumnSx,
      render: (item) => {
        const isBuiltin = isBuiltinToolset(item.toolset_id);
        return (
          <Chip
            label={isBuiltin ? 'Built-in' : 'User-defined'}
            size="small"
            variant={isBuiltin ? 'outlined' : 'filled'}
            color={isBuiltin ? 'primary' : 'default'}
          />
        );
      },
    },
    {
      key: 'slug',
      label: 'Slug',
      hideBelow: 'lg',
      cellSx: listTableMonoCellSx,
      render: (item) => item.toolset_id,
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
          {item.description || '—'}
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
      render: (item) =>
        isBuiltinToolset(item.toolset_id) ? '—' : `v${item.current_version}`,
    },
    {
      key: 'updated_at',
      label: 'Last updated',
      hideBelow: 'xl',
      cellSx: updatedAtColumnSx,
      render: (item) =>
        isBuiltinToolset(item.toolset_id) || !item.updated_at
          ? '—'
          : new Date(item.updated_at).toLocaleString(),
    },
    {
      key: 'updated_by',
      label: 'Updated by',
      hideBelow: 'lg',
      cellSx: updatedByColumnSx,
      render: (item) =>
        isBuiltinToolset(item.toolset_id) ? (
          '—'
        ) : item.updated_by ? (
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
  const filterGroups: ListTableFilterGroup<ToolsetListItem>[] = [
    {
      key: 'type',
      label: 'Type',
      icon: <BadgeIcon fontSize="small" />,
      options: [
        {
          key: 'builtin',
          label: 'Built-in',
          icon: <BadgeIcon fontSize="small" />,
          matches: (item) => isBuiltinToolset(item.toolset_id),
        },
        {
          key: 'user_defined',
          label: 'User-defined',
          icon: <PersonOutlineIcon fontSize="small" />,
          matches: (item) => !isBuiltinToolset(item.toolset_id),
        },
      ],
    },
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
          title="MCP Toolsets"
          action={
            canWrite && (
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={openCreate}
              >
                New toolset
              </Button>
            )
          }
        />

        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load toolsets"
        >
          <ListTable
            rows={allRows}
            columns={columns}
            getRowKey={(item) => item.toolset_id}
            emptyMessage="No toolsets yet. Create one above."
            filterGroups={filterGroups}
          />
        </ListViewState>
      </Box>

      <ToolsetDialog
        key={editTarget?.toolset_id ?? 'new'}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onSave={handleSave}
        initial={editTarget}
      />

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete toolset?"
        deleting={deleting}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
      >
        Permanently delete <strong>{deleteTarget?.name}</strong> and all its
        tools and versions? This cannot be undone.
      </ConfirmDeleteDialog>
    </>
  );
}

export default Toolsets;
