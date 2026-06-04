import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import AddCircleOutlineIcon from '@mui/icons-material/AddCircle';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import BadgeIcon from '@mui/icons-material/Badge';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import VisibilityIcon from '@mui/icons-material/Visibility';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircle';
import PersonOutlineIcon from '@mui/icons-material/Person';
import ToggleOnIcon from '@mui/icons-material/ToggleOn';
import ToggleOffIcon from '@mui/icons-material/ToggleOff';
import {
  useToolsList,
  useToolMutations,
  mcpNameForTool,
  ToolItem,
  ToolParamDef,
  CreateToolRequest,
  UpdateToolRequest,
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
import ToolDetailDialog, {
  ToolViewData,
} from 'src/components/ToolDetailDialog';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissions } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

// ---------------------------------------------------------------------------
// Built-in toolset detection
// ---------------------------------------------------------------------------
// The backend returns built-in groups through the same /api/v1/toolsets/<id>/tools
// endpoint; their toolset_id is prefixed with `__builtin_`.  All we need here is
// a prefix check to gate edit/delete actions.

const BUILTIN_PREFIX = '__builtin_';

const isBuiltinToolset = (id: string | undefined | null): boolean =>
  !!id && id.startsWith(BUILTIN_PREFIX) && id.endsWith('__');

const LOWER_SNAKE_ID = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;
// Cap each slug component so the full "toolset__tool" MCP name stays <= 64 chars
// (31 + "__" + 31). Mirrors MAX_SLUG_COMPONENT_LEN in mcp_config.py.
const MAX_SLUG_LEN = 31;

const descriptionColumnSx = { ...listTableSecondaryCellSx, width: '22%' };
const paramsColumnSx = { ...listTableSecondaryCellSx, width: 96 };
const versionColumnSx = { ...listTableSecondaryCellSx, width: 96 };
const updatedAtColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const updatedByColumnSx = { ...listTableSecondaryCellSx, width: 150 };
const statusColumnSx = { width: 176 };

function toolStatus(item: ToolItem): { enabled: boolean; label: string } {
  const effectiveEnabled = item.effective_enabled ?? item.enabled;
  if (effectiveEnabled) return { enabled: true, label: 'Enabled' };
  if (item.disabled_reason === 'toolset_disabled')
    return { enabled: false, label: 'Disabled by toolset' };
  return { enabled: false, label: 'Disabled' };
}

function toolViewData(item: ToolItem): ToolViewData {
  return {
    name: item.name,
    slug: mcpNameForTool(item),
    description: item.description,
    cypher: item.cypher,
    parameters: item.parameters,
    enabled: item.enabled,
    effective_enabled: item.effective_enabled,
    disabled_reason: item.disabled_reason,
  };
}

// ---------------------------------------------------------------------------
// Param form state
// ---------------------------------------------------------------------------

type ParamFormState = {
  name: string;
  type: ToolParamDef['type'];
  description: string;
  required: boolean;
  default_str: string;
};

const EMPTY_PARAM: ParamFormState = {
  name: '',
  type: 'string',
  description: '',
  required: true,
  default_str: '',
};

function paramToFormState(p: ToolParamDef): ParamFormState {
  return {
    name: p.name,
    type: p.type,
    description: p.description ?? '',
    required: p.required,
    default_str:
      p.default !== null && p.default !== undefined ? String(p.default) : '',
  };
}

function formStateToParam(p: ParamFormState): ToolParamDef {
  let defaultVal: unknown = null;
  if (p.default_str.trim() !== '') {
    if (p.type === 'integer') {
      defaultVal = parseInt(p.default_str, 10);
    } else if (p.type === 'float') {
      defaultVal = parseFloat(p.default_str);
    } else if (p.type === 'boolean') {
      defaultVal = p.default_str.toLowerCase() === 'true';
    } else {
      defaultVal = p.default_str;
    }
  }
  return {
    name: p.name,
    type: p.type,
    description: p.description,
    required: p.required,
    default: defaultVal,
  };
}

// ---------------------------------------------------------------------------
// Create/Edit dialog
// ---------------------------------------------------------------------------

interface ToolDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (req: CreateToolRequest | UpdateToolRequest) => Promise<void>;
  initial: ToolItem | null;
}

function ToolDialog({ open, onClose, onSave, initial }: ToolDialogProps) {
  const [toolId, setToolId] = useState(initial?.tool_id ?? '');
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [cypher, setCypher] = useState(initial?.cypher ?? '');
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [params, setParams] = useState<ParamFormState[]>(
    (initial?.parameters ?? []).map(paramToFormState),
  );
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
    if (!cypher.trim()) {
      setError('Cypher query is required.');
      return;
    }
    if (!initial && !LOWER_SNAKE_ID.test(toolId.trim())) {
      setError('ID must be lower_snake_case.');
      return;
    }
    if (!initial && toolId.trim().length > MAX_SLUG_LEN) {
      setError(`ID must be at most ${MAX_SLUG_LEN} characters.`);
      return;
    }
    for (const p of params) {
      if (!LOWER_SNAKE_ID.test(p.name.trim())) {
        setError('All parameter names must be lower_snake_case.');
        return;
      }
    }
    const req: CreateToolRequest | UpdateToolRequest = {
      ...(initial ? {} : { tool_id: toolId.trim() }),
      name: name.trim(),
      description: description.trim(),
      cypher: cypher.trim(),
      parameters: params.map(formStateToParam),
      enabled,
      ...(initial ? { comment: comment.trim() || null } : {}),
    };
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

  const addParam = () => setParams((ps) => [...ps, { ...EMPTY_PARAM }]);
  const removeParam = (i: number) =>
    setParams((ps) => ps.filter((_, idx) => idx !== i));
  const updateParam = <K extends keyof ParamFormState>(
    i: number,
    key: K,
    val: ParamFormState[K],
  ) =>
    setParams((ps) =>
      ps.map((p, idx) => (idx === i ? { ...p, [key]: val } : p)),
    );

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle>{initial ? 'Edit Tool' : 'New Tool'}</DialogTitle>
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
              value={toolId}
              onChange={(e) => setToolId(e.target.value)}
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
          <TextField
            label="Cypher Query"
            value={cypher}
            onChange={(e) => setCypher(e.target.value)}
            fullWidth
            required
            multiline
            minRows={5}
            slotProps={{
              htmlInput: { style: { fontFamily: 'monospace', fontSize: 13 } },
            }}
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

          <Divider />

          <Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
              <Typography variant="subtitle2">Parameters</Typography>
              <IconButton size="small" onClick={addParam}>
                <AddCircleOutlineIcon fontSize="small" />
              </IconButton>
            </Box>
            {params.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                No parameters. Click + to add one.
              </Typography>
            )}
            {params.map((p, i) => (
              <Box
                key={i}
                sx={{
                  border: '1px solid',
                  borderColor: 'divider',
                  borderRadius: 1,
                  p: 1.5,
                  mb: 1.5,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 1,
                }}
              >
                <Box sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
                  <TextField
                    label="Name"
                    value={p.name}
                    onChange={(e) => updateParam(i, 'name', e.target.value)}
                    size="small"
                    sx={{ flex: 2 }}
                    required
                  />
                  <FormControl size="small" sx={{ flex: 1 }}>
                    <InputLabel>Type</InputLabel>
                    <Select
                      label="Type"
                      value={p.type}
                      onChange={(e) =>
                        updateParam(
                          i,
                          'type',
                          e.target.value as ToolParamDef['type'],
                        )
                      }
                    >
                      <MenuItem value="string">string</MenuItem>
                      <MenuItem value="integer">integer</MenuItem>
                      <MenuItem value="float">float</MenuItem>
                      <MenuItem value="boolean">boolean</MenuItem>
                    </Select>
                  </FormControl>
                  <TextField
                    label="Default"
                    value={p.default_str}
                    onChange={(e) =>
                      updateParam(i, 'default_str', e.target.value)
                    }
                    size="small"
                    sx={{ flex: 1 }}
                    placeholder="optional"
                  />
                  <FormControlLabel
                    control={
                      <Checkbox
                        checked={p.required}
                        onChange={(e) =>
                          updateParam(i, 'required', e.target.checked)
                        }
                        size="small"
                      />
                    }
                    label="Required"
                    sx={{ flexShrink: 0 }}
                  />
                  <IconButton
                    size="small"
                    onClick={() => removeParam(i)}
                    sx={{ mt: 0.5, flexShrink: 0 }}
                  >
                    <RemoveCircleOutlineIcon fontSize="small" />
                  </IconButton>
                </Box>
                <TextField
                  label="Description"
                  value={p.description}
                  onChange={(e) =>
                    updateParam(i, 'description', e.target.value)
                  }
                  size="small"
                  fullWidth
                />
              </Box>
            ))}
          </Box>

          {initial && (
            <>
              <Divider />
              <TextField
                label="Comment (optional)"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                fullWidth
                size="small"
                placeholder="Describe what changed…"
              />
            </>
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

function ToolsetTools() {
  const { toolsetId } = useParams();
  const navigate = useNavigate();
  const hasPermission = usePermissions();

  const isBuiltin = isBuiltinToolset(toolsetId);
  const { tools, loading, error, refresh } = useToolsList(toolsetId ?? null);
  const mutations = useToolMutations(toolsetId ?? '');

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<ToolItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ToolItem | null>(null);
  const [detailItem, setDetailItem] = useState<ToolViewData | null>(null);
  const [deleting, setDeleting] = useState(false);

  const openCreate = () => {
    setEditTarget(null);
    setDialogOpen(true);
  };

  const openEdit = (item: ToolItem) => {
    setEditTarget(item);
    setDialogOpen(true);
  };

  const handleSave = async (req: CreateToolRequest | UpdateToolRequest) => {
    if (editTarget) {
      await mutations.updateTool(editTarget.tool_id, req as UpdateToolRequest);
    } else {
      await mutations.createTool(req as CreateToolRequest);
    }
    refresh();
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await mutations.deleteTool(deleteTarget.tool_id);
      setDeleteTarget(null);
      refresh();
    } catch {
      // dialog stays open so user can retry
    } finally {
      setDeleting(false);
    }
  };

  const canWrite = hasPermission('tools:write');
  const canDelete = hasPermission('tools:delete');

  const rowActions = (item: ToolItem): RowMenuAction[] => {
    const itemIsBuiltin = isBuiltinToolset(item.toolset_id);
    return [
      {
        key: 'detail',
        label: 'View detail',
        icon: <VisibilityIcon fontSize="small" />,
        onClick: () => setDetailItem(toolViewData(item)),
      },
      {
        key: 'edit',
        label: 'Edit',
        icon: <EditIcon fontSize="small" />,
        onClick: () => openEdit(item),
        disabled: itemIsBuiltin || !canWrite,
        tooltip: itemIsBuiltin
          ? 'Built-in tools cannot be edited'
          : !canWrite
            ? 'You do not have permission to edit tools'
            : undefined,
      },
      {
        key: 'history',
        label: 'View history',
        icon: <HistoryIcon fontSize="small" />,
        onClick: () =>
          navigate(`/app/toolsets/${toolsetId}/tools/${item.tool_id}/history`, {
            state: { fromLabel: 'Tools' } satisfies BackState,
          }),
        disabled: itemIsBuiltin,
        tooltip: itemIsBuiltin
          ? 'Built-in tools have no version history'
          : undefined,
      },
      {
        key: 'delete',
        label: 'Delete',
        icon: <DeleteIcon fontSize="small" />,
        onClick: () => setDeleteTarget(item),
        disabled: itemIsBuiltin || !canDelete,
        tooltip: itemIsBuiltin
          ? 'Built-in tools cannot be deleted'
          : !canDelete
            ? 'You do not have permission to delete tools'
            : undefined,
        destructive: true,
        dividerBefore: true,
      },
    ];
  };

  const displayTools: ToolItem[] = tools;
  const columns: ListTableColumn<ToolItem>[] = [
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
          onClick={() => setDetailItem(toolViewData(item))}
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
      render: (item) => mcpNameForTool(item),
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
          label={toolStatus(item).label}
          color={toolStatus(item).enabled ? 'success' : 'default'}
          size="small"
        />
      ),
    },
    {
      key: 'parameters',
      label: 'Parameters',
      hideBelow: 'md',
      cellSx: paramsColumnSx,
      render: (item) =>
        item.parameters.length === 0 ? '—' : item.parameters.length,
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
  const filterGroups: ListTableFilterGroup<ToolItem>[] = [
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
          matches: (item) => toolStatus(item).enabled,
        },
        {
          key: 'disabled',
          label: 'Disabled',
          icon: <ToggleOffIcon fontSize="small" />,
          matches: (item) => !toolStatus(item).enabled,
        },
      ],
    },
  ];

  return (
    <>
      <Box sx={pageContentSx}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <Button
            size="small"
            startIcon={<ArrowBackIcon />}
            onClick={() => navigate('/app/toolsets')}
          >
            Back to toolsets
          </Button>
        </Box>

        <ListPageHeader
          title={
            <Typography variant="h1">
              Tools
              {isBuiltin && (
                <Chip
                  label="Built-in"
                  size="small"
                  variant="outlined"
                  color="primary"
                  sx={{ ml: 1, verticalAlign: 'middle' }}
                />
              )}
            </Typography>
          }
          action={
            !isBuiltin &&
            canWrite && (
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={openCreate}
              >
                New tool
              </Button>
            )
          }
        />

        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load tools"
        >
          <ListTable
            rows={displayTools}
            columns={columns}
            getRowKey={(item) => item.tool_id}
            emptyMessage="No tools yet. Create one above."
            filterGroups={filterGroups}
          />
        </ListViewState>
      </Box>

      {!isBuiltin && (
        <ToolDialog
          key={editTarget?.tool_id ?? 'new'}
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          onSave={handleSave}
          initial={editTarget}
        />
      )}

      <ToolDetailDialog
        open={!!detailItem}
        onClose={() => setDetailItem(null)}
        data={detailItem}
      />

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete tool?"
        deleting={deleting}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
      >
        Permanently delete <strong>{deleteTarget?.name}</strong> and all its
        versions? This cannot be undone.
      </ConfirmDeleteDialog>
    </>
  );
}

export default ToolsetTools;
