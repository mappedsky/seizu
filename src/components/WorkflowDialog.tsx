import { FormEvent, ReactNode, useMemo, useState } from 'react';
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
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteOutlineIcon from '@mui/icons-material/Delete';
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ScheduleSpecEditor from 'src/components/ScheduleSpecEditor';
import SyncMetadataAutocomplete from 'src/components/SyncMetadataAutocomplete';
import {
  ActionConfigDependentSchema,
  ActionConfigFieldDef,
} from 'src/config.context';
import { useSyncMetadataValues } from 'src/hooks/useSyncMetadataValues';
import {
  WorkflowActivity,
  WorkflowItem,
  WorkflowQueryInput,
  WorkflowRequest,
  WorkflowWatchScan,
} from 'src/hooks/useWorkflowsApi';
import { ScheduleSpec } from 'src/scheduleSpec';

let editorSequence = 0;
const editorId = (prefix: string) => `${prefix}-${++editorSequence}`;

type QueryForm = {
  editorId: string;
  id: string;
  cypher: string;
  maxRows: string;
  parameters: { editorId: string; name: string; value: string }[];
};

type ActivityForm = WorkflowActivity & { editorId: string };

function displayValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value);
}

function parseValue(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return '';
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function inputForms(initial: WorkflowItem | null): QueryForm[] {
  return Object.entries(initial?.inputs ?? {}).map(([id, input]) => ({
    editorId: editorId('input'),
    id,
    cypher: input.cypher,
    maxRows: input.max_rows == null ? '' : String(input.max_rows),
    parameters: input.parameters.map((parameter) => ({
      editorId: editorId('parameter'),
      name: parameter.name,
      value: displayValue(parameter.value),
    })),
  }));
}

function activityForms(initial: WorkflowItem | null): ActivityForm[] {
  return (initial?.activities ?? []).map((activity) => ({
    ...activity,
    parameters: { ...activity.parameters },
    editorId: editorId('activity'),
  }));
}

function schemaDefaults(
  fields: ActionConfigFieldDef[],
): Record<string, unknown> {
  return Object.fromEntries(
    fields
      .filter((field) => field.default !== undefined && field.default !== null)
      .map((field) => [field.name, field.default]),
  );
}

function ConfigField({
  field,
  value,
  onChange,
}: {
  field: ActionConfigFieldDef;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const label = field.required ? `${field.label} *` : field.label;
  let control: ReactNode;
  if (field.type === 'boolean') {
    control = (
      <FormControlLabel
        control={
          <Checkbox
            checked={Boolean(value ?? field.default ?? false)}
            onChange={(event) => onChange(event.target.checked)}
          />
        }
        label={label}
      />
    );
  } else if (field.type === 'select') {
    control = (
      <FormControl fullWidth size="small">
        <InputLabel>{label}</InputLabel>
        <Select
          label={label}
          value={String(value ?? field.default ?? '')}
          onChange={(event) => onChange(event.target.value)}
        >
          {(field.options ?? []).map((option) => (
            <MenuItem key={option} value={option}>
              {option}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
    );
  } else {
    const listValue =
      field.type === 'string_list' && Array.isArray(value)
        ? value.join(', ')
        : String(value ?? field.default ?? '');
    control = (
      <TextField
        label={label}
        value={listValue}
        type={field.type === 'number' ? 'number' : 'text'}
        multiline={field.type === 'text'}
        minRows={field.type === 'text' ? 3 : undefined}
        helperText={
          field.description ??
          (field.type === 'string_list' ? 'Comma-separated values' : undefined)
        }
        onChange={(event) => {
          if (field.type === 'number') {
            onChange(
              event.target.value === '' ? '' : Number(event.target.value),
            );
          } else {
            onChange(event.target.value);
          }
        }}
        fullWidth
        size="small"
      />
    );
  }
  return field.warning ? (
    <Box>
      <Alert severity="warning" sx={{ mb: 1 }}>
        {field.warning}
      </Alert>
      {control}
    </Box>
  ) : (
    control
  );
}

function SortableActivity({
  activity,
  children,
}: {
  activity: ActivityForm;
  children: (dragHandle: Record<string, unknown>) => ReactNode;
}) {
  const sortable = useSortable({ id: activity.editorId });
  return (
    <Box
      ref={sortable.setNodeRef}
      sx={{
        transform: CSS.Transform.toString(sortable.transform),
        transition: sortable.transition,
        opacity: sortable.isDragging ? 0.65 : 1,
      }}
    >
      {children({ ...sortable.attributes, ...sortable.listeners })}
    </Box>
  );
}

export default function WorkflowDialog({
  open,
  initial,
  activityTypes,
  activitySchemas,
  dependentSchemas,
  onClose,
  onSave,
}: {
  open: boolean;
  initial: WorkflowItem | null;
  activityTypes: string[];
  activitySchemas: Record<string, ActionConfigFieldDef[]>;
  dependentSchemas: Record<string, ActionConfigDependentSchema>;
  onClose: () => void;
  onSave: (request: WorkflowRequest) => Promise<void>;
}) {
  const [name, setName] = useState(initial?.name ?? '');
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [triggerType, setTriggerType] = useState<'schedule' | 'watch_scans'>(
    initial?.watch_scans.length ? 'watch_scans' : 'schedule',
  );
  const defaultSchedule: ScheduleSpec =
    initial?.schedule ??
    ({ type: 'interval', interval_minutes: 60 } as ScheduleSpec);
  const [schedule, setSchedule] = useState<ScheduleSpec | null>(
    defaultSchedule,
  );
  const [watchScans, setWatchScans] = useState<WorkflowWatchScan[]>(
    initial?.watch_scans ?? [],
  );
  const [inputs, setInputs] = useState<QueryForm[]>(inputForms(initial));
  const [activities, setActivities] = useState<ActivityForm[]>(
    activityForms(initial),
  );
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const syncValues = useSyncMetadataValues(open);
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );
  const inputIds = useMemo(
    () => inputs.map((input) => input.id).filter(Boolean),
    [inputs],
  );

  const subSchema = (activity: ActivityForm): ActionConfigFieldDef[] => {
    const dependent = dependentSchemas[activity.type];
    if (!dependent) return [];
    return (
      dependent.schemas[
        String(activity.parameters[dependent.discriminator] ?? '')
      ] ?? []
    );
  };

  const addInput = () =>
    setInputs((value) => [
      ...value,
      {
        editorId: editorId('input'),
        id: '',
        cypher: '',
        maxRows: '',
        parameters: [],
      },
    ]);
  const addActivity = () =>
    setActivities((value) => [
      ...value,
      { editorId: editorId('activity'), type: '', input: null, parameters: {} },
    ]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!name.trim()) return setError('Name is required.');
    if (triggerType === 'schedule' && schedule === null)
      return setError('Complete the schedule before saving.');
    const ids = inputs.map((input) => input.id.trim());
    if (ids.some((id) => !/^[a-z][a-z0-9_]*$/.test(id)))
      return setError(
        'Input IDs must use lower_snake_case and start with a letter.',
      );
    if (new Set(ids).size !== ids.length)
      return setError('Input IDs must be unique.');
    if (inputs.some((input) => !input.cypher.trim()))
      return setError('Every input requires a Cypher query.');
    if (activities.some((activity) => !activity.type))
      return setError('Every activity requires a type.');

    const serializedInputs: Record<string, WorkflowQueryInput> = {};
    for (const input of inputs) {
      serializedInputs[input.id.trim()] = {
        type: 'query',
        cypher: input.cypher.trim(),
        parameters: input.parameters.map((parameter) => ({
          name: parameter.name.trim(),
          value: parseValue(parameter.value),
        })),
        max_rows: input.maxRows ? Number(input.maxRows) : null,
      };
    }
    const serializedActivities = activities.map((activity) => {
      const schema = [
        ...(activitySchemas[activity.type] ?? []),
        ...subSchema(activity),
      ];
      const fieldTypes = Object.fromEntries(
        schema.map((field) => [field.name, field.type]),
      );
      const parameters = Object.fromEntries(
        Object.entries(activity.parameters).map(([key, value]) => [
          key,
          fieldTypes[key] === 'string_list' && typeof value === 'string'
            ? value
                .split(',')
                .map((part) => part.trim())
                .filter(Boolean)
            : value,
        ]),
      );
      return { type: activity.type, input: activity.input || null, parameters };
    });
    setSaving(true);
    try {
      await onSave({
        name: name.trim(),
        enabled,
        schedule: triggerType === 'schedule' ? schedule : null,
        watch_scans: triggerType === 'watch_scans' ? watchScans : [],
        inputs: serializedInputs,
        activities: serializedActivities,
        comment: comment.trim() || null,
      });
      onClose();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : 'Failed to save workflow.',
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <Box component="form" onSubmit={submit} noValidate>
        <DialogTitle>{initial ? 'Edit Workflow' : 'New Workflow'}</DialogTitle>
        <DialogContent dividers>
          {error && (
            <Alert severity="error" sx={{ mb: 2 }}>
              {error}
            </Alert>
          )}
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <TextField
              label="Name"
              name="name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
            <FormControlLabel
              control={
                <Switch
                  checked={enabled}
                  onChange={(event) => setEnabled(event.target.checked)}
                />
              }
              label="Enabled"
            />
            <Divider />
            <FormControl component="fieldset">
              <FormLabel component="legend">Trigger</FormLabel>
              <RadioGroup
                row
                value={triggerType}
                onChange={(event) =>
                  setTriggerType(
                    event.target.value as 'schedule' | 'watch_scans',
                  )
                }
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
            {triggerType === 'schedule' ? (
              <ScheduleSpecEditor
                initial={defaultSchedule}
                onChange={setSchedule}
                allowMinutes
              />
            ) : (
              <Box>
                <Button
                  startIcon={<AddIcon />}
                  onClick={() =>
                    setWatchScans((value) => [
                      ...value,
                      { grouptype: '.*', syncedtype: '.*', groupid: '.*' },
                    ])
                  }
                >
                  Add watch scan
                </Button>
                {watchScans.map((scan, index) => (
                  <Box
                    key={index}
                    sx={{
                      display: 'flex',
                      gap: 1,
                      mt: 1,
                      alignItems: 'center',
                    }}
                  >
                    <SyncMetadataAutocomplete
                      label="grouptype"
                      value={scan.grouptype ?? ''}
                      onChange={(value) =>
                        setWatchScans((current) =>
                          current.map((item, itemIndex) =>
                            itemIndex === index
                              ? { ...item, grouptype: value }
                              : item,
                          ),
                        )
                      }
                      options={syncValues.grouptypes}
                      sx={{ flex: 1 }}
                    />
                    <SyncMetadataAutocomplete
                      label="syncedtype"
                      value={scan.syncedtype ?? ''}
                      onChange={(value) =>
                        setWatchScans((current) =>
                          current.map((item, itemIndex) =>
                            itemIndex === index
                              ? { ...item, syncedtype: value }
                              : item,
                          ),
                        )
                      }
                      options={syncValues.syncedtypes}
                      sx={{ flex: 1 }}
                    />
                    <SyncMetadataAutocomplete
                      label="groupid"
                      value={scan.groupid ?? ''}
                      onChange={(value) =>
                        setWatchScans((current) =>
                          current.map((item, itemIndex) =>
                            itemIndex === index
                              ? { ...item, groupid: value }
                              : item,
                          ),
                        )
                      }
                      options={syncValues.groupids}
                      sx={{ flex: 1 }}
                    />
                    <IconButton
                      aria-label={`Remove watch scan ${index + 1}`}
                      onClick={() =>
                        setWatchScans((value) =>
                          value.filter((_, itemIndex) => itemIndex !== index),
                        )
                      }
                    >
                      <DeleteOutlineIcon />
                    </IconButton>
                  </Box>
                ))}
              </Box>
            )}
            <Divider />
            <Box>
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <Typography component="h2" variant="h6">
                  Query inputs
                </Typography>
                <Button startIcon={<AddIcon />} onClick={addInput}>
                  Add input
                </Button>
              </Box>
              {inputs.length === 0 && (
                <Typography color="text.secondary">
                  No query inputs. Input-free code workflows can run without
                  one.
                </Typography>
              )}
              {inputs.map((input, inputIndex) => (
                <Box
                  key={input.editorId}
                  component="fieldset"
                  sx={{
                    border: '1px solid',
                    borderColor: 'divider',
                    borderRadius: 1,
                    p: 2,
                    mt: 1,
                  }}
                >
                  <Typography component="legend" variant="subtitle2">
                    Input {inputIndex + 1}
                  </Typography>
                  <Box sx={{ display: 'flex', gap: 1, mb: 1 }}>
                    <TextField
                      label="Input ID"
                      value={input.id}
                      onChange={(event) =>
                        setInputs((value) =>
                          value.map((item) =>
                            item.editorId === input.editorId
                              ? { ...item, id: event.target.value }
                              : item,
                          ),
                        )
                      }
                      required
                      fullWidth
                      size="small"
                      helperText="lower_snake_case"
                    />
                    <TextField
                      label="Max rows"
                      type="number"
                      value={input.maxRows}
                      onChange={(event) =>
                        setInputs((value) =>
                          value.map((item) =>
                            item.editorId === input.editorId
                              ? { ...item, maxRows: event.target.value }
                              : item,
                          ),
                        )
                      }
                      size="small"
                      sx={{ width: 150 }}
                    />
                    <IconButton
                      aria-label={`Remove input ${input.id || inputIndex + 1}`}
                      onClick={() =>
                        setInputs((value) =>
                          value.filter(
                            (item) => item.editorId !== input.editorId,
                          ),
                        )
                      }
                    >
                      <DeleteOutlineIcon />
                    </IconButton>
                  </Box>
                  <TextField
                    label="Cypher query"
                    value={input.cypher}
                    onChange={(event) =>
                      setInputs((value) =>
                        value.map((item) =>
                          item.editorId === input.editorId
                            ? { ...item, cypher: event.target.value }
                            : item,
                        ),
                      )
                    }
                    multiline
                    minRows={4}
                    required
                    fullWidth
                  />
                  <Button
                    size="small"
                    startIcon={<AddIcon />}
                    sx={{ mt: 1 }}
                    onClick={() =>
                      setInputs((value) =>
                        value.map((item) =>
                          item.editorId === input.editorId
                            ? {
                                ...item,
                                parameters: [
                                  ...item.parameters,
                                  {
                                    editorId: editorId('parameter'),
                                    name: '',
                                    value: '',
                                  },
                                ],
                              }
                            : item,
                        ),
                      )
                    }
                  >
                    Add query parameter
                  </Button>
                  {input.parameters.map((parameter, parameterIndex) => (
                    <Box
                      key={parameter.editorId}
                      sx={{ display: 'flex', gap: 1, mt: 1 }}
                    >
                      <TextField
                        label="Parameter name"
                        value={parameter.name}
                        onChange={(event) =>
                          setInputs((value) =>
                            value.map((item) =>
                              item.editorId === input.editorId
                                ? {
                                    ...item,
                                    parameters: item.parameters.map(
                                      (candidate) =>
                                        candidate.editorId ===
                                        parameter.editorId
                                          ? {
                                              ...candidate,
                                              name: event.target.value,
                                            }
                                          : candidate,
                                    ),
                                  }
                                : item,
                            ),
                          )
                        }
                        size="small"
                      />
                      <TextField
                        label="Value (JSON or text)"
                        value={parameter.value}
                        onChange={(event) =>
                          setInputs((value) =>
                            value.map((item) =>
                              item.editorId === input.editorId
                                ? {
                                    ...item,
                                    parameters: item.parameters.map(
                                      (candidate) =>
                                        candidate.editorId ===
                                        parameter.editorId
                                          ? {
                                              ...candidate,
                                              value: event.target.value,
                                            }
                                          : candidate,
                                    ),
                                  }
                                : item,
                            ),
                          )
                        }
                        size="small"
                        fullWidth
                      />
                      <IconButton
                        aria-label={`Remove parameter ${parameterIndex + 1} from input ${input.id || inputIndex + 1}`}
                        onClick={() =>
                          setInputs((value) =>
                            value.map((item) =>
                              item.editorId === input.editorId
                                ? {
                                    ...item,
                                    parameters: item.parameters.filter(
                                      (candidate) =>
                                        candidate.editorId !==
                                        parameter.editorId,
                                    ),
                                  }
                                : item,
                            ),
                          )
                        }
                      >
                        <DeleteOutlineIcon />
                      </IconButton>
                    </Box>
                  ))}
                </Box>
              ))}
            </Box>
            <Divider />
            <Box>
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <Typography component="h2" variant="h6">
                  Activities
                </Typography>
                <Button startIcon={<AddIcon />} onClick={addActivity}>
                  Add activity
                </Button>
              </Box>
              {activities.length === 0 && (
                <Typography color="text.secondary">
                  No activities. The workflow will only execute its inputs.
                </Typography>
              )}
              <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                onDragEnd={({ active, over }) => {
                  if (!over || active.id === over.id) return;
                  setActivities((value) =>
                    arrayMove(
                      value,
                      value.findIndex((item) => item.editorId === active.id),
                      value.findIndex((item) => item.editorId === over.id),
                    ),
                  );
                }}
              >
                <SortableContext
                  items={activities.map((activity) => activity.editorId)}
                  strategy={verticalListSortingStrategy}
                >
                  {activities.map((activity, index) => {
                    const fields = [
                      ...(activitySchemas[activity.type] ?? []),
                      ...subSchema(activity),
                    ];
                    return (
                      <SortableActivity
                        key={activity.editorId}
                        activity={activity}
                      >
                        {(dragHandle) => (
                          <Box
                            component="fieldset"
                            sx={{
                              border: '1px solid',
                              borderColor: 'divider',
                              borderRadius: 1,
                              p: 2,
                              mt: 1,
                            }}
                          >
                            <Typography component="legend" variant="subtitle2">
                              Activity {index + 1}
                            </Typography>
                            <Box
                              sx={{
                                display: 'flex',
                                gap: 1,
                                alignItems: 'center',
                                mb: fields.length ? 2 : 0,
                              }}
                            >
                              <IconButton
                                {...dragHandle}
                                aria-label={`Reorder activity ${index + 1}`}
                              >
                                <DragIndicatorIcon />
                              </IconButton>
                              <FormControl size="small" sx={{ minWidth: 200 }}>
                                <InputLabel>Activity type</InputLabel>
                                <Select
                                  label="Activity type"
                                  value={activity.type}
                                  onChange={(event) => {
                                    const type = event.target.value;
                                    setActivities((value) =>
                                      value.map((item) =>
                                        item.editorId === activity.editorId
                                          ? {
                                              ...item,
                                              type,
                                              parameters: schemaDefaults(
                                                activitySchemas[type] ?? [],
                                              ),
                                            }
                                          : item,
                                      ),
                                    );
                                  }}
                                >
                                  {activityTypes.map((type) => (
                                    <MenuItem key={type} value={type}>
                                      {type}
                                    </MenuItem>
                                  ))}
                                </Select>
                              </FormControl>
                              <FormControl size="small" sx={{ minWidth: 200 }}>
                                <InputLabel>Query input</InputLabel>
                                <Select
                                  label="Query input"
                                  value={activity.input ?? ''}
                                  onChange={(event) =>
                                    setActivities((value) =>
                                      value.map((item) =>
                                        item.editorId === activity.editorId
                                          ? {
                                              ...item,
                                              input: event.target.value || null,
                                            }
                                          : item,
                                      ),
                                    )
                                  }
                                >
                                  <MenuItem value="">
                                    <em>No input</em>
                                  </MenuItem>
                                  {inputIds.map((id) => (
                                    <MenuItem key={id} value={id}>
                                      {id}
                                    </MenuItem>
                                  ))}
                                </Select>
                              </FormControl>
                              <Box sx={{ flex: 1 }} />
                              <IconButton
                                aria-label={`Remove activity ${index + 1}`}
                                onClick={() =>
                                  setActivities((value) =>
                                    value.filter(
                                      (item) =>
                                        item.editorId !== activity.editorId,
                                    ),
                                  )
                                }
                              >
                                <DeleteOutlineIcon />
                              </IconButton>
                            </Box>
                            <Box
                              sx={{
                                display: 'flex',
                                flexDirection: 'column',
                                gap: 1.5,
                              }}
                            >
                              {fields.map((field) => (
                                <ConfigField
                                  key={field.name}
                                  field={field}
                                  value={activity.parameters[field.name]}
                                  onChange={(fieldValue) =>
                                    setActivities((value) =>
                                      value.map((item) => {
                                        if (item.editorId !== activity.editorId)
                                          return item;
                                        const dependent =
                                          dependentSchemas[item.type];
                                        const parameters = {
                                          ...item.parameters,
                                        };
                                        if (
                                          dependent?.discriminator ===
                                          field.name
                                        ) {
                                          for (const oldField of dependent
                                            .schemas[
                                            String(parameters[field.name] ?? '')
                                          ] ?? [])
                                            delete parameters[oldField.name];
                                          Object.assign(
                                            parameters,
                                            schemaDefaults(
                                              dependent.schemas[
                                                String(fieldValue)
                                              ] ?? [],
                                            ),
                                          );
                                        }
                                        parameters[field.name] = fieldValue;
                                        return { ...item, parameters };
                                      }),
                                    )
                                  }
                                />
                              ))}
                            </Box>
                          </Box>
                        )}
                      </SortableActivity>
                    );
                  })}
                </SortableContext>
              </DndContext>
            </Box>
            {initial && (
              <TextField
                label="Version comment (optional)"
                value={comment}
                onChange={(event) => setComment(event.target.value)}
              />
            )}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" variant="contained" disabled={saving}>
            {saving ? <ConstellationSpinner size={20} /> : 'Save workflow'}
          </Button>
        </DialogActions>
      </Box>
    </Dialog>
  );
}
