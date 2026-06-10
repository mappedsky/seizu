import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControl,
  FormControlLabel,
  FormLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Switch,
  TextField,
  Tooltip,
  Typography,
  Alert,
} from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import AddIcon from '@mui/icons-material/Add';
import BadgeIcon from '@mui/icons-material/Badge';
import CancelOutlinedIcon from '@mui/icons-material/CancelOutlined';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import ErrorOutlineIcon from '@mui/icons-material/Error';
import HelpOutlineIcon from '@mui/icons-material/Help';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircle';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import HistoryIcon from '@mui/icons-material/History';
import AddCircleOutlineIcon from '@mui/icons-material/AddCircle';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircle';
import ToggleOnIcon from '@mui/icons-material/ToggleOn';
import ToggleOffIcon from '@mui/icons-material/ToggleOff';
import {
  useScheduledQueriesList,
  useScheduledQueriesMutations,
  ScheduledQueryItem,
  ScheduledQueryRequest,
  ScheduledQueryParam,
  ScheduledQueryWatchScan,
  ScheduledQueryAction,
} from 'src/hooks/useScheduledQueriesApi';
import { ActionConfigFieldDef, SeizuConfig } from 'src/config.context';
import ScheduledQueryDetailDialog, {
  ScheduledQueryViewData,
} from 'src/components/ScheduledQueryDetailDialog';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissions } from 'src/hooks/usePermissions';
import ListTable, {
  ListTableColumn,
  ListTableFilterGroup,
  listTableActionColumnSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
  listTableTruncateSx,
} from 'src/components/ListTable';
import ListPageHeader from 'src/components/ListPageHeader';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

const actionsColumnSx = { width: '18%' };
const statusColumnSx = { width: 128 };

const EMPTY_FORM: ScheduledQueryRequest = {
  name: '',
  cypher: '',
  params: [],
  frequency: 60,
  watch_scans: [],
  enabled: true,
  actions: [],
  comment: null,
};

type TriggerType = 'frequency' | 'watch_scans';

type ParamValueType = 'string' | 'list';

type ParamFormState = {
  name: string;
  value_str: string;
  value_type: ParamValueType;
};

type ActionFormState = {
  action_type: string;
  action_config: Record<string, unknown>;
};

function paramToFormState(p: ScheduledQueryParam): ParamFormState {
  if (Array.isArray(p.value)) {
    return {
      name: p.name,
      value_str: (p.value as unknown[]).join(', '),
      value_type: 'list',
    };
  }
  return {
    name: p.name,
    value_str: String(p.value ?? ''),
    value_type: 'string',
  };
}

function schemaDefaults(
  fields: ActionConfigFieldDef[],
): Record<string, unknown> {
  const defaults: Record<string, unknown> = {};
  for (const field of fields) {
    if (field.default !== undefined && field.default !== null) {
      defaults[field.name] = field.default;
    }
  }
  return defaults;
}

function actionToFormState(a: ScheduledQueryAction): ActionFormState {
  return { action_type: a.action_type, action_config: { ...a.action_config } };
}

interface ActionConfigFieldProps {
  field: ActionConfigFieldDef;
  value: unknown;
  onChange: (val: unknown) => void;
}

function ActionConfigField(props: ActionConfigFieldProps) {
  if (props.field.warning) {
    return (
      <Box>
        <Alert severity="warning" sx={{ mb: 1 }}>
          {props.field.warning}
        </Alert>
        <ActionConfigFieldControl {...props} />
      </Box>
    );
  }
  return <ActionConfigFieldControl {...props} />;
}

function ActionConfigFieldControl({
  field,
  value,
  onChange,
}: ActionConfigFieldProps) {
  const label = field.required ? `${field.label} *` : field.label;

  if (field.type === 'boolean') {
    return (
      <FormControlLabel
        control={
          <Checkbox
            checked={Boolean(value ?? field.default ?? false)}
            onChange={(e) => onChange(e.target.checked)}
            size="small"
          />
        }
        label={label}
      />
    );
  }

  if (field.type === 'select') {
    return (
      <FormControl size="small" fullWidth>
        <InputLabel>{label}</InputLabel>
        <Select
          label={label}
          value={String(value ?? field.default ?? '')}
          onChange={(e) => onChange(e.target.value)}
        >
          {(field.options ?? []).map((opt) => (
            <MenuItem key={opt} value={opt}>
              {opt}
            </MenuItem>
          ))}
        </Select>
        {field.description && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ mt: 0.5, ml: 1.5 }}
          >
            {field.description}
          </Typography>
        )}
      </FormControl>
    );
  }

  if (field.type === 'string_list') {
    // Store as comma-separated string; serialize to array on submit
    const displayVal = Array.isArray(value)
      ? (value as string[]).join(', ')
      : String(value ?? '');
    return (
      <TextField
        label={label}
        value={displayVal}
        onChange={(e) => onChange(e.target.value)}
        size="small"
        fullWidth
        helperText={field.description ?? 'Comma-separated values'}
      />
    );
  }

  if (field.type === 'text') {
    return (
      <TextField
        label={label}
        value={String(value ?? field.default ?? '')}
        onChange={(e) => onChange(e.target.value)}
        size="small"
        fullWidth
        multiline
        minRows={3}
        helperText={field.description}
        slotProps={{
          htmlInput: { style: { fontFamily: 'monospace', fontSize: 12 } },
        }}
      />
    );
  }

  if (field.type === 'number') {
    return (
      <TextField
        label={label}
        type="number"
        value={String(value ?? field.default ?? '')}
        onChange={(e) =>
          onChange(e.target.value === '' ? '' : Number(e.target.value))
        }
        size="small"
        fullWidth
        helperText={field.description}
      />
    );
  }

  // default: string
  return (
    <TextField
      label={label}
      value={String(value ?? field.default ?? '')}
      onChange={(e) => onChange(e.target.value)}
      size="small"
      fullWidth
      helperText={field.description}
    />
  );
}

interface ScheduledQueryDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (req: ScheduledQueryRequest) => Promise<void>;
  initial: ScheduledQueryItem | null;
  actionTypes: string[];
  actionSchemas: Record<string, ActionConfigFieldDef[]>;
}

function ScheduledQueryDialog({
  open,
  onClose,
  onSave,
  initial,
  actionTypes,
  actionSchemas,
}: ScheduledQueryDialogProps) {
  const [name, setName] = useState(initial?.name ?? EMPTY_FORM.name);
  const [cypher, setCypher] = useState(initial?.cypher ?? EMPTY_FORM.cypher);
  const [enabled, setEnabled] = useState(
    initial?.enabled ?? EMPTY_FORM.enabled,
  );
  const [triggerType, setTriggerType] = useState<TriggerType>(
    initial && initial.watch_scans.length > 0 ? 'watch_scans' : 'frequency',
  );
  const [frequency, setFrequency] = useState<string>(
    initial?.frequency != null ? String(initial.frequency) : '60',
  );
  const [watchScans, setWatchScans] = useState<ScheduledQueryWatchScan[]>(
    initial?.watch_scans ?? [],
  );
  const [params, setParams] = useState<ParamFormState[]>(
    (initial?.params ?? []).map(paramToFormState),
  );
  const [actions, setActions] = useState<ActionFormState[]>(
    (initial?.actions ?? []).map(actionToFormState),
  );
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset form when dialog opens/closes or initial changes
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

    // Validate and serialize action configs
    const parsedActions: ScheduledQueryAction[] = [];
    for (const a of actions) {
      if (!a.action_type.trim()) {
        setError('All actions must have an action type.');
        return;
      }
      // Serialize string_list fields: convert comma-separated strings to arrays
      const serialized: Record<string, unknown> = {};
      const schema = actionSchemas[a.action_type] ?? [];
      const schemaMap = Object.fromEntries(schema.map((f) => [f.name, f]));
      for (const [key, val] of Object.entries(a.action_config)) {
        const fieldDef = schemaMap[key];
        if (fieldDef?.type === 'string_list' && typeof val === 'string') {
          serialized[key] = val
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean);
        } else {
          serialized[key] = val;
        }
      }
      parsedActions.push({
        action_type: a.action_type,
        action_config: serialized,
      });
    }

    const req: ScheduledQueryRequest = {
      name: name.trim(),
      cypher: cypher.trim(),
      params: params.map((p) => ({
        name: p.name,
        value:
          p.value_type === 'list'
            ? p.value_str
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean)
            : p.value_str,
      })),
      frequency:
        triggerType === 'frequency' ? parseInt(frequency, 10) || null : null,
      watch_scans: triggerType === 'watch_scans' ? watchScans : [],
      enabled,
      actions: parsedActions,
      comment: comment.trim() || null,
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

  const addParam = () =>
    setParams((ps) => [
      ...ps,
      { name: '', value_str: '', value_type: 'string' },
    ]);
  const removeParam = (i: number) =>
    setParams((ps) => ps.filter((_, idx) => idx !== i));
  const updateParamName = (i: number, val: string) =>
    setParams((ps) =>
      ps.map((p, idx) => (idx === i ? { ...p, name: val } : p)),
    );
  const updateParamValueStr = (i: number, val: string) =>
    setParams((ps) =>
      ps.map((p, idx) => (idx === i ? { ...p, value_str: val } : p)),
    );
  const toggleParamType = (i: number) =>
    setParams((ps) =>
      ps.map((p, idx) =>
        idx === i
          ? { ...p, value_type: p.value_type === 'string' ? 'list' : 'string' }
          : p,
      ),
    );

  const addWatchScan = () =>
    setWatchScans((ws) => [
      ...ws,
      { grouptype: '.*', syncedtype: '.*', groupid: '.*' },
    ]);
  const removeWatchScan = (i: number) =>
    setWatchScans((ws) => ws.filter((_, idx) => idx !== i));
  const updateWatchScan = (
    i: number,
    field: keyof ScheduledQueryWatchScan,
    val: string,
  ) =>
    setWatchScans((ws) =>
      ws.map((w, idx) => (idx === i ? { ...w, [field]: val } : w)),
    );

  const addAction = () =>
    setActions((as) => [...as, { action_type: '', action_config: {} }]);
  const removeAction = (i: number) =>
    setActions((as) => as.filter((_, idx) => idx !== i));
  const updateActionType = (i: number, val: string) => {
    const defaults = schemaDefaults(actionSchemas[val] ?? []);
    setActions((as) =>
      as.map((a, idx) =>
        idx === i ? { action_type: val, action_config: defaults } : a,
      ),
    );
  };
  const updateActionConfigField = (
    i: number,
    fieldName: string,
    val: unknown,
  ) =>
    setActions((as) =>
      as.map((a, idx) =>
        idx === i
          ? { ...a, action_config: { ...a.action_config, [fieldName]: val } }
          : a,
      ),
    );

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle>
        {initial ? 'Edit Scheduled Query' : 'New Scheduled Query'}
      </DialogTitle>
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
          />
          <TextField
            label="Cypher Query"
            value={cypher}
            onChange={(e) => setCypher(e.target.value)}
            fullWidth
            required
            multiline
            minRows={4}
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

          <FormControl>
            <FormLabel>Trigger</FormLabel>
            <RadioGroup
              row
              value={triggerType}
              onChange={(e) => setTriggerType(e.target.value as TriggerType)}
            >
              <FormControlLabel
                value="frequency"
                control={<Radio />}
                label="Fixed frequency"
              />
              <FormControlLabel
                value="watch_scans"
                control={<Radio />}
                label="Watch scans"
              />
            </RadioGroup>
          </FormControl>

          {triggerType === 'frequency' && (
            <TextField
              label="Frequency (minutes)"
              type="number"
              value={frequency}
              onChange={(e) => setFrequency(e.target.value)}
              slotProps={{ htmlInput: { min: 1 } }}
              sx={{ maxWidth: 220 }}
            />
          )}

          {triggerType === 'watch_scans' && (
            <Box>
              <Box
                sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}
              >
                <Typography variant="subtitle2">Watch Scans</Typography>
                <IconButton size="small" onClick={addWatchScan}>
                  <AddCircleOutlineIcon fontSize="small" />
                </IconButton>
              </Box>
              {watchScans.length === 0 && (
                <Typography variant="body2" color="text.secondary">
                  No watch scans defined. Click + to add one.
                </Typography>
              )}
              {watchScans.map((ws, i) => (
                <Box
                  key={i}
                  sx={{ display: 'flex', gap: 1, mb: 1, alignItems: 'center' }}
                >
                  <TextField
                    label="grouptype"
                    value={ws.grouptype ?? ''}
                    onChange={(e) =>
                      updateWatchScan(i, 'grouptype', e.target.value)
                    }
                    size="small"
                    sx={{ flex: 1 }}
                  />
                  <TextField
                    label="syncedtype"
                    value={ws.syncedtype ?? ''}
                    onChange={(e) =>
                      updateWatchScan(i, 'syncedtype', e.target.value)
                    }
                    size="small"
                    sx={{ flex: 1 }}
                  />
                  <TextField
                    label="groupid"
                    value={ws.groupid ?? ''}
                    onChange={(e) =>
                      updateWatchScan(i, 'groupid', e.target.value)
                    }
                    size="small"
                    sx={{ flex: 1 }}
                  />
                  <IconButton size="small" onClick={() => removeWatchScan(i)}>
                    <RemoveCircleOutlineIcon fontSize="small" />
                  </IconButton>
                </Box>
              ))}
            </Box>
          )}

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
                  display: 'flex',
                  gap: 1,
                  mb: 1,
                  alignItems: 'flex-start',
                }}
              >
                <TextField
                  label="Name"
                  value={p.name}
                  onChange={(e) => updateParamName(i, e.target.value)}
                  size="small"
                  sx={{ flex: 1 }}
                />
                <TextField
                  label={
                    p.value_type === 'list'
                      ? 'Values (comma-separated)'
                      : 'Value'
                  }
                  value={p.value_str}
                  onChange={(e) => updateParamValueStr(i, e.target.value)}
                  size="small"
                  sx={{ flex: 2 }}
                />
                <Tooltip
                  title={
                    p.value_type === 'list'
                      ? 'Switch to single value'
                      : 'Switch to list'
                  }
                >
                  <Button
                    size="small"
                    variant={p.value_type === 'list' ? 'contained' : 'outlined'}
                    onClick={() => toggleParamType(i)}
                    sx={{
                      minWidth: 44,
                      px: 1,
                      mt: 0.25,
                      flexShrink: 0,
                      fontSize: 11,
                    }}
                  >
                    list
                  </Button>
                </Tooltip>
                <IconButton
                  size="small"
                  onClick={() => removeParam(i)}
                  sx={{ mt: 0.25 }}
                >
                  <RemoveCircleOutlineIcon fontSize="small" />
                </IconButton>
              </Box>
            ))}
          </Box>

          <Divider />

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

          <Divider />

          <Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
              <Typography variant="subtitle2">Actions</Typography>
              <IconButton size="small" onClick={addAction}>
                <AddCircleOutlineIcon fontSize="small" />
              </IconButton>
            </Box>
            {actions.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                No actions. Click + to add one.
              </Typography>
            )}
            {actions.map((a, i) => {
              const schema = actionSchemas[a.action_type] ?? [];
              return (
                <Box
                  key={i}
                  sx={{
                    border: '1px solid',
                    borderColor: 'divider',
                    borderRadius: 1,
                    p: 1.5,
                    mb: 1.5,
                  }}
                >
                  <Box
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 1,
                      mb: schema.length > 0 ? 1.5 : 0,
                    }}
                  >
                    {actionTypes.length > 0 ? (
                      <FormControl size="small" sx={{ width: 220 }}>
                        <InputLabel>Action type</InputLabel>
                        <Select
                          label="Action type"
                          value={a.action_type}
                          onChange={(e) => updateActionType(i, e.target.value)}
                        >
                          {actionTypes.map((t) => (
                            <MenuItem key={t} value={t}>
                              {t}
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                    ) : (
                      <TextField
                        label="Action type"
                        value={a.action_type}
                        onChange={(e) => updateActionType(i, e.target.value)}
                        size="small"
                        sx={{ width: 220 }}
                      />
                    )}
                    <Box sx={{ flex: 1 }} />
                    <IconButton size="small" onClick={() => removeAction(i)}>
                      <RemoveCircleOutlineIcon fontSize="small" />
                    </IconButton>
                  </Box>
                  {schema.length > 0 && (
                    <Box
                      sx={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 1.5,
                      }}
                    >
                      {schema.map((field) => (
                        <ActionConfigField
                          key={field.name}
                          field={field}
                          value={a.action_config[field.name]}
                          onChange={(val) =>
                            updateActionConfigField(i, field.name, val)
                          }
                        />
                      ))}
                    </Box>
                  )}
                </Box>
              );
            })}
          </Box>
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

function triggerSummary(item: ScheduledQueryItem): string {
  if (item.watch_scans.length > 0)
    return `Watch scans (${item.watch_scans.length})`;
  if (item.frequency != null) return `Every ${item.frequency} min`;
  return 'Not configured';
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function ScheduledQueries() {
  const navigate = useNavigate();
  const { scheduledQueries, loading, error, refresh } =
    useScheduledQueriesList();
  const { createScheduledQuery, updateScheduledQuery, deleteScheduledQuery } =
    useScheduledQueriesMutations();
  const hasPermission = usePermissions();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<ScheduledQueryItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ScheduledQueryItem | null>(
    null,
  );
  const [detailItem, setDetailItem] = useState<ScheduledQueryViewData | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);
  const [actionConfig, setActionConfig] = useState<SeizuConfig>({
    scheduled_query_action_types: [],
    scheduled_query_action_schemas: {},
  });

  useEffect(() => {
    let cancelled = false;
    fetch('/api/v1/config')
      .then((res) => {
        if (!res.ok)
          throw new globalThis.Error(
            `Failed to load scheduled query action config: ${res.status}`,
          );
        return res.json();
      })
      .then((config: SeizuConfig) => {
        if (!cancelled) {
          setActionConfig({
            scheduled_query_action_types:
              config.scheduled_query_action_types ?? [],
            scheduled_query_action_schemas:
              config.scheduled_query_action_schemas ?? {},
          });
        }
      })
      .catch((err: unknown) => {
        console.log('Scheduled query action config fetch error', err);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const openCreate = () => {
    setEditTarget(null);
    setDialogOpen(true);
  };

  const openEdit = (item: ScheduledQueryItem) => {
    setEditTarget(item);
    setDialogOpen(true);
  };

  const handleSave = async (req: ScheduledQueryRequest) => {
    if (editTarget) {
      await updateScheduledQuery(editTarget.scheduled_query_id, req);
    } else {
      await createScheduledQuery(req);
    }
    refresh();
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteScheduledQuery(deleteTarget.scheduled_query_id);
      setDeleteTarget(null);
      refresh();
    } catch {
      // dialog stays open so user can retry
    } finally {
      setDeleting(false);
    }
  };

  const rowActions = (item: ScheduledQueryItem): RowMenuAction[] => {
    const canWrite = hasPermission('scheduled_queries:write');
    const canDelete = hasPermission('scheduled_queries:delete');
    return [
      {
        key: 'edit',
        label: 'Edit',
        icon: <EditIcon fontSize="small" />,
        onClick: () => openEdit(item),
        disabled: !canWrite,
        tooltip: canWrite
          ? undefined
          : 'You do not have permission to edit scheduled queries',
      },
      {
        key: 'history',
        label: 'View history',
        icon: <HistoryIcon fontSize="small" />,
        onClick: () =>
          navigate(
            `/app/scheduled-queries/${item.scheduled_query_id}/history`,
            { state: { fromLabel: 'Scheduled Queries' } satisfies BackState },
          ),
      },
      {
        key: 'delete',
        label: 'Delete',
        icon: <DeleteIcon fontSize="small" />,
        onClick: () => setDeleteTarget(item),
        disabled: !canDelete,
        tooltip: canDelete
          ? undefined
          : 'You do not have permission to delete scheduled queries',
        destructive: true,
        dividerBefore: true,
      },
    ];
  };

  const columns: ListTableColumn<ScheduledQueryItem>[] = [
    {
      key: 'name',
      label: 'Name',
      cellSx: listTablePrimaryCellSx,
      render: (item) => (
        <Box sx={{ minWidth: 0 }}>
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
            onClick={() =>
              setDetailItem({
                name: item.name,
                cypher: item.cypher,
                params: item.params,
                frequency: item.frequency,
                watch_scans: item.watch_scans,
                enabled: item.enabled,
                actions: item.actions,
                last_run_status: item.last_run_status,
                last_run_at: item.last_run_at,
                last_errors: item.last_errors,
              })
            }
          >
            {item.name}
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{
              ...listTableTruncateSx,
              fontFamily: 'monospace',
              display: 'block',
            }}
          >
            {item.cypher.split('\n')[0]}
          </Typography>
        </Box>
      ),
    },
    {
      key: 'trigger',
      label: 'Trigger',
      hideBelow: 'md',
      cellSx: { ...listTableSecondaryCellSx, width: 150 },
      render: (item) => triggerSummary(item),
    },
    {
      key: 'configured_actions',
      label: 'Actions',
      hideBelow: 'lg',
      cellSx: actionsColumnSx,
      render: (item) =>
        item.actions.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            None
          </Typography>
        ) : (
          <Box
            sx={{
              display: 'flex',
              flexWrap: 'nowrap',
              gap: 0.5,
              overflow: 'hidden',
            }}
          >
            {item.actions.map((action, index) => (
              <Chip
                key={`${action.action_type}-${index}`}
                label={action.action_type}
                size="small"
              />
            ))}
          </Box>
        ),
    },
    {
      key: 'status',
      label: 'Status',
      cellSx: statusColumnSx,
      render: (item) => (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Chip
            label={item.enabled ? 'Enabled' : 'Disabled'}
            color={item.enabled ? 'success' : 'default'}
            size="small"
          />
          <Tooltip
            title={
              item.last_run_status === 'success'
                ? `Last run succeeded${item.last_run_at ? ` at ${new Date(item.last_run_at).toLocaleString()}` : ''}`
                : item.last_run_status === 'failure'
                  ? `Last run failed${item.last_run_at ? ` at ${new Date(item.last_run_at).toLocaleString()}` : ''}`
                  : 'No runs yet'
            }
          >
            <FiberManualRecordIcon
              sx={{
                fontSize: 12,
                color:
                  item.last_run_status === 'success'
                    ? 'success.main'
                    : item.last_run_status === 'failure'
                      ? 'error.main'
                      : 'warning.main',
              }}
            />
          </Tooltip>
        </Box>
      ),
    },
    {
      key: 'version',
      label: 'Version',
      hideBelow: 'sm',
      cellSx: { ...listTableSecondaryCellSx, width: 96 },
      render: (item) => `v${item.current_version}`,
    },
    {
      key: 'updated_at',
      label: 'Last updated',
      hideBelow: 'xl',
      cellSx: { ...listTableSecondaryCellSx, width: 180 },
      render: (item) => new Date(item.updated_at).toLocaleString(),
    },
    {
      key: 'updated_by',
      label: 'Updated by',
      hideBelow: 'lg',
      cellSx: { ...listTableSecondaryCellSx, width: 150 },
      render: (item) =>
        item.updated_by ? (
          <UserDisplay userId={item.updated_by} />
        ) : (
          <UserDisplay userId={item.created_by} />
        ),
    },
    {
      key: 'row_actions',
      align: 'right',
      cellSx: listTableActionColumnSx,
      render: (item) => <RowMenu actions={rowActions(item)} />,
    },
  ];
  const actionTypes = useMemo(
    () =>
      Array.from(
        new Set(
          scheduledQueries
            .flatMap((item) => item.actions.map((action) => action.action_type))
            .filter(Boolean),
        ),
      ).sort(),
    [scheduledQueries],
  );
  const filterGroups: ListTableFilterGroup<ScheduledQueryItem>[] = useMemo(
    () => [
      {
        key: 'actions',
        label: 'Actions',
        icon: <BadgeIcon fontSize="small" />,
        options: [
          {
            key: 'none',
            label: 'No actions',
            icon: <HelpOutlineIcon fontSize="small" />,
            matches: (item) => item.actions.length === 0,
          },
          ...actionTypes.map((actionType) => ({
            key: actionType,
            label: actionType,
            icon: <BadgeIcon fontSize="small" />,
            matches: (item) =>
              item.actions.some((action) => action.action_type === actionType),
          })),
        ],
      },
      {
        key: 'status',
        label: 'Status',
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
          {
            key: 'success',
            label: 'Last run succeeded',
            icon: <CheckCircleOutlineIcon fontSize="small" />,
            matches: (item) => item.last_run_status === 'success',
          },
          {
            key: 'failure',
            label: 'Last run failed',
            icon: <ErrorOutlineIcon fontSize="small" />,
            matches: (item) => item.last_run_status === 'failure',
          },
          {
            key: 'none',
            label: 'No runs yet',
            icon: <CancelOutlinedIcon fontSize="small" />,
            matches: (item) => item.last_run_status === null,
          },
        ],
      },
    ],
    [actionTypes],
  );

  return (
    <>
      <Box sx={pageContentSx}>
        <ListPageHeader
          title="Scheduled Queries"
          action={
            hasPermission('scheduled_queries:write') && (
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={openCreate}
              >
                New scheduled query
              </Button>
            )
          }
        />

        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load scheduled queries"
        >
          <ListTable
            rows={scheduledQueries}
            columns={columns}
            getRowKey={(item) => item.scheduled_query_id}
            emptyMessage="No scheduled queries yet. Create one above."
            filterGroups={filterGroups}
          />
        </ListViewState>
      </Box>

      <ScheduledQueryDialog
        key={editTarget?.scheduled_query_id ?? 'new'}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onSave={handleSave}
        initial={editTarget}
        actionTypes={actionConfig.scheduled_query_action_types}
        actionSchemas={actionConfig.scheduled_query_action_schemas}
      />

      <ScheduledQueryDetailDialog
        open={!!detailItem}
        onClose={() => setDetailItem(null)}
        data={detailItem}
      />

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete scheduled query?"
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

export default ScheduledQueries;
