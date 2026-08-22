import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import FileUploadIcon from '@mui/icons-material/FileUpload';
import HistoryIcon from '@mui/icons-material/History';
import ToggleOffIcon from '@mui/icons-material/ToggleOff';
import ToggleOnIcon from '@mui/icons-material/ToggleOn';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ListPageHeader from 'src/components/ListPageHeader';
import ListTable, {
  ListTableColumn,
  ListTableFilterGroup,
  listTableActionColumnSx,
  listTableMonoCellSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
  listTableTruncateSx,
} from 'src/components/ListTable';
import ListViewState from 'src/components/ListViewState';
import PluginDetailDialog from 'src/components/PluginDetailDialog';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissions } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import {
  CreatePluginRequest,
  PluginListItem,
  usePluginMutations,
  usePluginsList,
} from 'src/hooks/usePluginsApi';
import { pageContentSx } from 'src/theme/layout';

const LOWER_SNAKE_ID = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;
const PORTABLE_NAME =
  /^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$/;
const MAX_SLUG_LEN = 31;

const descriptionColumnSx = { ...listTableSecondaryCellSx, width: '24%' };
const statusColumnSx = { width: 136 };
const versionColumnSx = { ...listTableSecondaryCellSx, width: 96 };
const packageColumnSx = { ...listTableSecondaryCellSx, width: 140 };
const diagnosticsColumnSx = { width: 120 };
const updatedAtColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const updatedByColumnSx = { ...listTableSecondaryCellSx, width: 150 };

// A count alone says something is wrong without saying what, and the messages
// are short enough to read in place. More than this and the tooltip is taller
// than the viewport, so the rest are left to the detail dialog.
const MAX_TOOLTIP_DIAGNOSTICS = 8;

function DiagnosticsCell({ plugin }: { plugin: PluginListItem }) {
  const { diagnostics } = plugin;
  if (diagnostics.length === 0) return <>—</>;
  const shown = diagnostics.slice(0, MAX_TOOLTIP_DIAGNOSTICS);
  const hidden = diagnostics.length - shown.length;
  return (
    <Tooltip
      title={
        <Stack spacing={0.5}>
          {shown.map((diagnostic) => (
            <Typography
              key={`${diagnostic.code}-${diagnostic.path ?? ''}-${diagnostic.skill ?? ''}-${diagnostic.message}`}
              variant="caption"
              component="span"
            >
              {diagnostic.severity === 'error' ? '✕' : '!'}{' '}
              {diagnostic.path ? `${diagnostic.path}: ` : ''}
              {diagnostic.message}
            </Typography>
          ))}
          {hidden > 0 && (
            <Typography variant="caption" component="span">
              and {hidden} more
            </Typography>
          )}
        </Stack>
      }
    >
      <Chip
        label={diagnostics.length}
        color={
          diagnostics.some((diagnostic) => diagnostic.severity === 'error')
            ? 'error'
            : 'warning'
        }
        size="small"
      />
    </Tooltip>
  );
}

function NewPluginDialog({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (request: CreatePluginRequest) => Promise<void>;
}) {
  const [pluginId, setPluginId] = useState('');
  const [name, setName] = useState('');
  const [version, setVersion] = useState('1.0.0');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    const id = pluginId.trim();
    const packageName = name.trim();
    if (!LOWER_SNAKE_ID.test(id) || id.length > MAX_SLUG_LEN) {
      setError(
        `Namespace must be lower_snake_case and at most ${MAX_SLUG_LEN} characters.`,
      );
      return;
    }
    if (!PORTABLE_NAME.test(packageName)) {
      setError(
        'Package name must use lowercase letters, numbers, dots, or hyphens.',
      );
      return;
    }
    if (!version.trim()) {
      setError('Version is required.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onCreate({
        plugin_id: id,
        name: packageName,
        version: version.trim(),
        description: description.trim(),
      });
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>New Agent Plugin</DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Stack spacing={2}>
          <TextField
            label="Namespace"
            value={pluginId}
            onChange={(event) => setPluginId(event.target.value)}
            helperText="Used in skill names, for example security_review__scan_repository"
            required
            fullWidth
          />
          <TextField
            label="Package name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            helperText="Agent Plugins name, such as security-review"
            required
            fullWidth
          />
          <TextField
            label="Version"
            value={version}
            onChange={(event) => setVersion(event.target.value)}
            required
            fullWidth
          />
          <TextField
            label="Description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            multiline
            minRows={2}
            fullWidth
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={() => void save()}
          disabled={saving}
        >
          {saving ? <ConstellationSpinner size={20} /> : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

export default function Plugins() {
  const navigate = useNavigate();
  const hasPermission = usePermissions();
  const canWrite = hasPermission('plugins:write');
  const canDelete = hasPermission('plugins:delete');
  const { plugins, loading, error, refresh } = usePluginsList();
  const mutations = usePluginMutations();
  const inputRef = useRef<HTMLInputElement>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [detailTarget, setDetailTarget] = useState<PluginListItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<PluginListItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const upload = async (file?: File) => {
    if (!file) return;
    setFailure(null);
    try {
      await mutations.install(file);
      refresh();
    } catch (reason) {
      setFailure((reason as Error).message);
    } finally {
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const edit = (plugin: PluginListItem) => {
    navigate(`/app/plugins/${plugin.plugin_id}/edit`);
  };

  const create = async (request: CreatePluginRequest) => {
    const plugin = await mutations.create(request);
    setCreateOpen(false);
    navigate(`/app/plugins/${plugin.plugin_id}/edit`);
  };

  const toggle = async (plugin: PluginListItem) => {
    setFailure(null);
    try {
      await mutations.setEnabled(plugin.plugin_id, !plugin.enabled);
      refresh();
    } catch (reason) {
      setFailure((reason as Error).message);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setFailure(null);
    try {
      await mutations.remove(deleteTarget.plugin_id);
      setDeleteTarget(null);
      refresh();
    } catch (reason) {
      setFailure((reason as Error).message);
    } finally {
      setDeleting(false);
    }
  };

  const rowActions = (plugin: PluginListItem): RowMenuAction[] => [
    {
      key: 'edit',
      label: 'Edit',
      icon: <EditIcon fontSize="small" />,
      onClick: () => edit(plugin),
      disabled: !canWrite,
      tooltip: canWrite
        ? undefined
        : 'You do not have permission to edit plugins',
    },
    {
      key: 'toggle',
      label: plugin.enabled ? 'Disable' : 'Enable',
      icon: plugin.enabled ? (
        <ToggleOffIcon fontSize="small" />
      ) : (
        <ToggleOnIcon fontSize="small" />
      ),
      onClick: () => void toggle(plugin),
      disabled: !canWrite,
      tooltip: canWrite
        ? undefined
        : 'You do not have permission to edit plugins',
    },
    {
      key: 'history',
      label: 'View history',
      icon: <HistoryIcon fontSize="small" />,
      onClick: () =>
        navigate(`/app/plugins/${plugin.plugin_id}/history`, {
          state: { fromLabel: 'Agent Plugins' } satisfies BackState,
        }),
    },
    {
      key: 'delete',
      label: 'Delete',
      icon: <DeleteIcon fontSize="small" />,
      onClick: () => setDeleteTarget(plugin),
      disabled: !canDelete,
      tooltip: canDelete
        ? undefined
        : 'You do not have permission to delete plugins',
      destructive: true,
      dividerBefore: true,
    },
  ];

  const columns: ListTableColumn<PluginListItem>[] = [
    {
      key: 'name',
      label: 'Name',
      cellSx: listTablePrimaryCellSx,
      // Opens the read-only detail dialog, as elsewhere in the app: editing a
      // plugin creates a server-side draft, so it stays a deliberate action.
      render: (plugin) => (
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
          onClick={() => setDetailTarget(plugin)}
        >
          {plugin.name}
        </Typography>
      ),
    },
    {
      key: 'slug',
      label: 'Slug',
      hideBelow: 'lg',
      cellSx: listTableMonoCellSx,
      render: (plugin) => plugin.plugin_id,
    },
    {
      key: 'description',
      label: 'Description',
      hideBelow: 'md',
      cellSx: descriptionColumnSx,
      render: (plugin) => (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={listTableTruncateSx}
        >
          {plugin.description || '—'}
        </Typography>
      ),
    },
    {
      key: 'status',
      label: 'Status',
      cellSx: statusColumnSx,
      render: (plugin) => (
        <Chip
          label={plugin.enabled ? 'Enabled' : 'Disabled'}
          color={plugin.enabled ? 'success' : 'default'}
          size="small"
        />
      ),
    },
    {
      key: 'version',
      label: 'Version',
      hideBelow: 'sm',
      cellSx: versionColumnSx,
      render: (plugin) => `v${plugin.current_revision}`,
    },
    {
      key: 'package_version',
      label: 'Package version',
      hideBelow: 'lg',
      cellSx: packageColumnSx,
      render: (plugin) => plugin.package_version || '—',
    },
    {
      key: 'diagnostics',
      label: 'Diagnostics',
      hideBelow: 'lg',
      cellSx: diagnosticsColumnSx,
      render: (plugin) => <DiagnosticsCell plugin={plugin} />,
    },
    {
      key: 'updated_at',
      label: 'Last updated',
      hideBelow: 'xl',
      cellSx: updatedAtColumnSx,
      render: (plugin) => new Date(plugin.updated_at).toLocaleString(),
    },
    {
      key: 'updated_by',
      label: 'Updated by',
      hideBelow: 'lg',
      cellSx: updatedByColumnSx,
      render: (plugin) => (
        <UserDisplay userId={plugin.updated_by || plugin.created_by} />
      ),
    },
    {
      key: 'actions',
      align: 'right',
      cellSx: listTableActionColumnSx,
      render: (plugin) => <RowMenu actions={rowActions(plugin)} />,
    },
  ];

  const filters: ListTableFilterGroup<PluginListItem>[] = [
    {
      key: 'enabled',
      label: 'Enabled',
      icon: <ToggleOnIcon fontSize="small" />,
      options: [
        {
          key: 'enabled',
          label: 'Enabled',
          matches: (plugin) => plugin.enabled,
        },
        {
          key: 'disabled',
          label: 'Disabled',
          matches: (plugin) => !plugin.enabled,
        },
      ],
    },
  ];

  return (
    <>
      <Helmet>
        <title>Agent Plugins | Seizu</title>
      </Helmet>
      <Box sx={pageContentSx}>
        <ListPageHeader
          title="Agent Plugins"
          action={
            canWrite && (
              <Stack direction="row" spacing={1}>
                <Button
                  variant="outlined"
                  startIcon={<FileUploadIcon />}
                  onClick={() => inputRef.current?.click()}
                >
                  Install ZIP
                </Button>
                <Button
                  variant="contained"
                  startIcon={<AddIcon />}
                  onClick={() => setCreateOpen(true)}
                >
                  New plugin
                </Button>
              </Stack>
            )
          }
        />
        <input
          ref={inputRef}
          hidden
          type="file"
          accept=".zip,application/zip"
          onChange={(event) => void upload(event.target.files?.[0])}
        />
        {failure && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {failure}
          </Alert>
        )}
        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load plugins"
        >
          <ListTable
            rows={plugins}
            columns={columns}
            getRowKey={(plugin) => plugin.plugin_id}
            emptyMessage="No plugins yet. Create one above or install a ZIP package."
            filterGroups={filters}
          />
        </ListViewState>
      </Box>

      <NewPluginDialog
        key={createOpen ? 'open' : 'closed'}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={create}
      />
      <PluginDetailDialog
        open={!!detailTarget}
        plugin={detailTarget}
        onClose={() => setDetailTarget(null)}
      />
      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete plugin?"
        deleting={deleting}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void confirmDelete()}
      >
        Permanently delete <strong>{deleteTarget?.name}</strong>, its draft, and
        all revisions? This cannot be undone.
      </ConfirmDeleteDialog>
    </>
  );
}
