import { useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Checkbox,
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
} from '@mui/material';
import AddCircleOutlineIcon from '@mui/icons-material/AddCircle';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircle';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ScheduleSpecEditor from 'src/components/ScheduleSpecEditor';
import SyncMetadataAutocomplete from 'src/components/SyncMetadataAutocomplete';
import {
  ActionConfigDependentSchema,
  ActionConfigFieldDef,
} from 'src/config.context';
import {
  ScheduledQueryAction,
  ScheduledQueryItem,
  ScheduledQueryParam,
  ScheduledQueryRequest,
  ScheduledQueryWatchScan,
} from 'src/hooks/useScheduledQueriesApi';
import { useSyncMetadataValues } from 'src/hooks/useSyncMetadataValues';
import { ScheduleSpec } from 'src/scheduleSpec';

const EMPTY_FORM: ScheduledQueryRequest = {
  name: '',
  cypher: '',
  params: [],
  frequency: null,
  schedule: { type: 'interval', interval_minutes: 60 },
  watch_scans: [],
  enabled: true,
  actions: [],
  comment: null,
};

type TriggerType = 'schedule' | 'watch_scans';

// The editor seed: an existing schedule wins; a legacy frequency (minutes)
// maps onto the equivalent interval schedule; new queries default to every
// 60 minutes.
function initialSchedule(initial: ScheduledQueryItem | null): ScheduleSpec {
  if (initial?.schedule) return initial.schedule;
  if (initial?.frequency != null) {
    return { type: 'interval', interval_minutes: initial.frequency };
  }
  return EMPTY_FORM.schedule as ScheduleSpec;
}

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

export interface ScheduledQueryDialogProps {
  open: boolean;
  onClose: () => void;
  onSave: (req: ScheduledQueryRequest) => Promise<void>;
  initial: ScheduledQueryItem | null;
  actionTypes: string[];
  actionSchemas: Record<string, ActionConfigFieldDef[]>;
  dependentSchemas?: Record<string, ActionConfigDependentSchema>;
}

export default function ScheduledQueryDialog({
  open,
  onClose,
  onSave,
  initial,
  actionTypes,
  actionSchemas,
  dependentSchemas = {},
}: ScheduledQueryDialogProps) {
  const [name, setName] = useState(initial?.name ?? EMPTY_FORM.name);
  const [cypher, setCypher] = useState(initial?.cypher ?? EMPTY_FORM.cypher);
  const [enabled, setEnabled] = useState(
    initial?.enabled ?? EMPTY_FORM.enabled,
  );
  const [triggerType, setTriggerType] = useState<TriggerType>(
    initial && initial.watch_scans.length > 0 ? 'watch_scans' : 'schedule',
  );
  const [schedule, setSchedule] = useState<ScheduleSpec | null>(
    initialSchedule(initial),
  );
  const [watchScans, setWatchScans] = useState<ScheduledQueryWatchScan[]>(
    initial?.watch_scans ?? [],
  );
  const syncValues = useSyncMetadataValues(open);
  const [params, setParams] = useState<ParamFormState[]>(
    (initial?.params ?? []).map(paramToFormState),
  );
  const [actions, setActions] = useState<ActionFormState[]>(
    (initial?.actions ?? []).map(actionToFormState),
  );
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Extra fields shown once the action's discriminator field (e.g. the
  // temporal action's workflow select) takes a value with its own sub-schema.
  const subSchemaFor = (a: ActionFormState): ActionConfigFieldDef[] => {
    const dep = dependentSchemas[a.action_type];
    if (!dep) return [];
    return dep.schemas[String(a.action_config[dep.discriminator] ?? '')] ?? [];
  };

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
    if (triggerType === 'schedule' && schedule === null) {
      setError('Complete the schedule before saving.');
      return;
    }

    const parsedActions: ScheduledQueryAction[] = [];
    for (const a of actions) {
      if (!a.action_type.trim()) {
        setError('All actions must have an action type.');
        return;
      }
      const serialized: Record<string, unknown> = {};
      const schema = [
        ...(actionSchemas[a.action_type] ?? []),
        ...subSchemaFor(a),
      ];
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
      // Saving always writes the structured schedule; a legacy frequency is
      // migrated to the equivalent interval schedule on the next edit.
      frequency: null,
      schedule: triggerType === 'schedule' ? schedule : null,
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
      as.map((a, idx) => {
        if (idx !== i) {
          return a;
        }
        const dep = dependentSchemas[a.action_type];
        if (dep && fieldName === dep.discriminator) {
          // Changing the discriminator swaps the sub-schema: drop the old
          // sub-schema's values and seed the new one's defaults.
          const config: Record<string, unknown> = { ...a.action_config };
          const oldSub =
            dep.schemas[String(config[dep.discriminator] ?? '')] ?? [];
          for (const f of oldSub) {
            delete config[f.name];
          }
          Object.assign(config, schemaDefaults(dep.schemas[String(val)] ?? []));
          config[fieldName] = val;
          return { ...a, action_config: config };
        }
        return {
          ...a,
          action_config: { ...a.action_config, [fieldName]: val },
        };
      }),
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
                value="schedule"
                control={<Radio />}
                label="Schedule"
              />
              <FormControlLabel
                value="watch_scans"
                control={<Radio />}
                label="Watch scans"
              />
            </RadioGroup>
          </FormControl>

          {triggerType === 'schedule' && (
            <ScheduleSpecEditor
              initial={initialSchedule(initial)}
              onChange={setSchedule}
              allowMinutes
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
                  <SyncMetadataAutocomplete
                    label="grouptype"
                    value={ws.grouptype ?? ''}
                    onChange={(value) => updateWatchScan(i, 'grouptype', value)}
                    options={syncValues.grouptypes}
                    sx={{ flex: 1 }}
                  />
                  <SyncMetadataAutocomplete
                    label="syncedtype"
                    value={ws.syncedtype ?? ''}
                    onChange={(value) =>
                      updateWatchScan(i, 'syncedtype', value)
                    }
                    options={syncValues.syncedtypes}
                    sx={{ flex: 1 }}
                  />
                  <SyncMetadataAutocomplete
                    label="groupid"
                    value={ws.groupid ?? ''}
                    onChange={(value) => updateWatchScan(i, 'groupid', value)}
                    options={syncValues.groupids}
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
              const subSchema = subSchemaFor(a);
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
                      mb: schema.length + subSchema.length > 0 ? 1.5 : 0,
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
                  {schema.length + subSchema.length > 0 && (
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
                      {subSchema.length > 0 && (
                        <>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ mt: 0.5 }}
                          >
                            {String(
                              a.action_config[
                                dependentSchemas[a.action_type]
                                  ?.discriminator ?? ''
                              ],
                            )}{' '}
                            options
                          </Typography>
                          {subSchema.map((field) => (
                            <ActionConfigField
                              key={field.name}
                              field={field}
                              value={a.action_config[field.name]}
                              onChange={(val) =>
                                updateActionConfigField(i, field.name, val)
                              }
                            />
                          ))}
                        </>
                      )}
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
