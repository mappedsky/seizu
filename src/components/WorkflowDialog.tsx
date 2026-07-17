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
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward';
import DeleteOutlineIcon from '@mui/icons-material/Delete';
import HelpOutlineIcon from '@mui/icons-material/HelpOutlineOutlined';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ScheduleSpecEditor from 'src/components/ScheduleSpecEditor';
import SyncMetadataAutocomplete from 'src/components/SyncMetadataAutocomplete';
import {
  ActionConfigDependentSchema,
  ActionConfigFieldDef,
  WorkflowActivityDefinition,
} from 'src/config.context';
import { useSyncMetadataValues } from 'src/hooks/useSyncMetadataValues';
import {
  WorkflowActivity,
  WorkflowItem,
  WorkflowRequest,
  WorkflowWatchScan,
} from 'src/hooks/useWorkflowsApi';
import { ScheduleSpec } from 'src/scheduleSpec';

let editorSequence = 0;
const editorId = (prefix: string) => `${prefix}-${++editorSequence}`;

type ParameterForm = { editorId: string; name: string; value: string };
type ActivityForm = WorkflowActivity & {
  editorId: string;
  queryParameters: ParameterForm[];
};
type StageForm = { editorId: string; activities: ActivityForm[] };

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

function activityForm(activity: WorkflowActivity): ActivityForm {
  const rawParameters = Array.isArray(activity.parameters.parameters)
    ? (activity.parameters.parameters as { name?: unknown; value?: unknown }[])
    : [];
  const parameters = { ...activity.parameters };
  delete parameters.parameters;
  return {
    ...activity,
    parameters,
    editorId: editorId('activity'),
    queryParameters: rawParameters.map((parameter) => ({
      editorId: editorId('parameter'),
      name: String(parameter.name ?? ''),
      value: displayValue(parameter.value),
    })),
  };
}

function stageForms(initial: WorkflowItem | null): StageForm[] {
  const forms = (initial?.stages ?? []).map((stage) => ({
    editorId: editorId('stage'),
    activities: stage.activities.map(activityForm),
  }));
  return clearInvalidInputs(forms);
}

function nextOutputName(stages: StageForm[], stageIndex: number): string {
  const used = new Set(
    stages.flatMap((stage) =>
      stage.activities.map((activity) => activity.output),
    ),
  );
  let activityPosition = stages[stageIndex].activities.length + 1;
  let output = `stage_${stageIndex + 1}_activity_${activityPosition}`;
  while (used.has(output)) {
    activityPosition += 1;
    output = `stage_${stageIndex + 1}_activity_${activityPosition}`;
  }
  return output;
}

function clearInvalidInputs(stages: StageForm[]): StageForm[] {
  const available = new Set<string>();
  return stages.map((stage) => {
    const activities = stage.activities.map((activity) => ({
      ...activity,
      input:
        activity.input !== null && available.has(activity.input)
          ? activity.input
          : null,
    }));
    activities.forEach((activity) => available.add(activity.output));
    return { ...stage, activities };
  });
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

function schemaSummary(schema: Record<string, unknown> | undefined): string {
  if (!schema || Object.keys(schema).length === 0) return 'any JSON value';
  const type = schema.type;
  if (type === 'array') return 'a list';
  if (type === 'object') return 'an object';
  return typeof type === 'string' ? type : 'a JSON value';
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
  if (field.type === 'parameters') return null;
  const label = field.required ? `${field.label} *` : field.label;
  const tooltip = [
    field.description,
    field.type === 'string_list' ? 'Enter comma-separated values.' : null,
  ]
    .filter(Boolean)
    .join(' ');
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
        onChange={(event) =>
          onChange(
            field.type === 'number'
              ? event.target.value === ''
                ? ''
                : Number(event.target.value)
              : event.target.value,
          )
        }
        fullWidth
        size="small"
      />
    );
  }
  if (tooltip) {
    control = (
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.5 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>{control}</Box>
        <Tooltip title={tooltip} placement="top" arrow describeChild>
          <IconButton aria-label={`Help for ${field.label}`} size="small">
            <HelpOutlineIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>
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

export default function WorkflowDialog({
  open,
  initial,
  activityTypes,
  activitySchemas,
  activityDefinitions = {},
  dependentSchemas,
  onClose,
  onSave,
}: {
  open: boolean;
  initial: WorkflowItem | null;
  activityTypes: string[];
  activitySchemas: Record<string, ActionConfigFieldDef[]>;
  activityDefinitions?: Record<string, WorkflowActivityDefinition>;
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
  const [stages, setStages] = useState<StageForm[]>(stageForms(initial));
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const syncValues = useSyncMetadataValues(open);

  const outputLocations = useMemo(() => {
    const result = new Map<string, number>();
    stages.forEach((stage, stageIndex) =>
      stage.activities.forEach((activity) =>
        result.set(activity.output, stageIndex),
      ),
    );
    return result;
  }, [stages]);

  const updateActivity = (
    activityId: string,
    update: (activity: ActivityForm) => ActivityForm,
  ) =>
    setStages((current) =>
      current.map((stage) => ({
        ...stage,
        activities: stage.activities.map((activity) =>
          activity.editorId === activityId ? update(activity) : activity,
        ),
      })),
    );

  const renameOutput = (activityId: string, output: string) =>
    setStages((current) => {
      let oldOutput = '';
      const renamed = current.map((stage) => ({
        ...stage,
        activities: stage.activities.map((activity) => {
          if (activity.editorId !== activityId) return activity;
          oldOutput = activity.output;
          return { ...activity, output };
        }),
      }));
      if (!oldOutput) return renamed;
      return renamed.map((stage) => ({
        ...stage,
        activities: stage.activities.map((activity) =>
          activity.input === oldOutput
            ? { ...activity, input: output || null }
            : activity,
        ),
      }));
    });

  const addStage = () =>
    setStages((current) => [
      ...current,
      { editorId: editorId('stage'), activities: [] },
    ]);

  const addActivity = (stageId: string) =>
    setStages((current) => {
      const stageIndex = current.findIndex(
        (stage) => stage.editorId === stageId,
      );
      if (stageIndex < 0) return current;
      const output = nextOutputName(current, stageIndex);
      return current.map((stage) =>
        stage.editorId === stageId
          ? {
              ...stage,
              activities: [
                ...stage.activities,
                {
                  editorId: editorId('activity'),
                  type: '',
                  input: null,
                  output,
                  parameters: {},
                  queryParameters: [],
                },
              ],
            }
          : stage,
      );
    });

  const moveStage = (index: number, offset: number) =>
    setStages((current) => {
      const target = index + offset;
      if (target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return clearInvalidInputs(next);
    });

  const moveActivity = (
    stageIndex: number,
    activityIndex: number,
    stageOffset: number,
  ) =>
    setStages((current) => {
      const targetStageIndex = stageIndex + stageOffset;
      if (targetStageIndex < 0 || targetStageIndex >= current.length)
        return current;
      const next = current.map((stage) => ({
        ...stage,
        activities: [...stage.activities],
      }));
      const [activity] = next[stageIndex].activities.splice(activityIndex, 1);
      next[targetStageIndex].activities.push(activity);
      return clearInvalidInputs(next);
    });

  const reorderActivity = (
    stageIndex: number,
    activityIndex: number,
    offset: number,
  ) =>
    setStages((current) => {
      const target = activityIndex + offset;
      if (target < 0 || target >= current[stageIndex].activities.length)
        return current;
      const next = current.map((stage) => ({
        ...stage,
        activities: [...stage.activities],
      }));
      [
        next[stageIndex].activities[activityIndex],
        next[stageIndex].activities[target],
      ] = [
        next[stageIndex].activities[target],
        next[stageIndex].activities[activityIndex],
      ];
      return next;
    });

  const subSchema = (activity: ActivityForm): ActionConfigFieldDef[] => {
    const dependent = dependentSchemas[activity.type];
    return dependent
      ? (dependent.schemas[
          String(activity.parameters[dependent.discriminator] ?? '')
        ] ?? [])
      : [];
  };

  const activityDefinition = (
    activity: ActivityForm,
  ): WorkflowActivityDefinition | undefined => {
    const base = activityDefinitions[activity.type];
    if (!base) return undefined;
    const variant = base.variants?.[String(activity.parameters.workflow ?? '')];
    return variant ? { ...base, ...variant } : base;
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!name.trim()) return setError('Name is required.');
    if (triggerType === 'schedule' && schedule === null)
      return setError('Complete the schedule before saving.');
    if (!stages.length || stages.some((stage) => !stage.activities.length))
      return setError('Every workflow requires at least one non-empty stage.');

    const seen = new Set<string>();
    const available = new Set<string>();
    for (let stageIndex = 0; stageIndex < stages.length; stageIndex += 1) {
      const stageOutputs: string[] = [];
      for (const activity of stages[stageIndex].activities) {
        if (!activity.type) return setError('Every activity requires a type.');
        if (!/^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/.test(activity.output))
          return setError(
            'Output names must use lower_snake_case and start with a letter.',
          );
        if (seen.has(activity.output))
          return setError(`Duplicate output '${activity.output}'.`);
        if (activity.input && !available.has(activity.input))
          return setError(
            `Input '${activity.input}' must come from an earlier stage.`,
          );
        if (activityDefinition(activity)?.input_required && !activity.input)
          return setError(`Activity '${activity.output}' requires an input.`);
        if (activity.type === 'query') {
          if (!String(activity.parameters.cypher ?? '').trim())
            return setError(
              `Query activity '${activity.output}' requires Cypher.`,
            );
          const names = activity.queryParameters.map((parameter) =>
            parameter.name.trim(),
          );
          if (names.some((parameter) => !parameter))
            return setError(
              `Query activity '${activity.output}' has an unnamed parameter.`,
            );
          if (names.includes('input'))
            return setError("Query parameter 'input' is reserved.");
          if (new Set(names).size !== names.length)
            return setError(
              `Query activity '${activity.output}' has duplicate parameters.`,
            );
        }
        seen.add(activity.output);
        stageOutputs.push(activity.output);
      }
      stageOutputs.forEach((output) => available.add(output));
    }

    const serializedStages = stages.map((stage) => ({
      activities: stage.activities.map((activity) => {
        const fields = [
          ...(activitySchemas[activity.type] ?? []),
          ...subSchema(activity),
        ];
        const fieldTypes = Object.fromEntries(
          fields.map((field) => [field.name, field.type]),
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
        if (activity.type === 'query') {
          parameters.parameters = activity.queryParameters.map((parameter) => ({
            name: parameter.name.trim(),
            value: parseValue(parameter.value),
          }));
        }
        return {
          type: activity.type,
          input: activity.input || null,
          output: activity.output.trim(),
          parameters,
        };
      }),
    }));

    setSaving(true);
    try {
      await onSave({
        name: name.trim(),
        enabled,
        schedule: triggerType === 'schedule' ? schedule : null,
        watch_scans: triggerType === 'watch_scans' ? watchScans : [],
        stages: serializedStages,
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
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
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
                    {(['grouptype', 'syncedtype', 'groupid'] as const).map(
                      (field) => (
                        <SyncMetadataAutocomplete
                          key={field}
                          label={field}
                          value={scan[field] ?? ''}
                          onChange={(value) =>
                            setWatchScans((current) =>
                              current.map((item, itemIndex) =>
                                itemIndex === index
                                  ? { ...item, [field]: value }
                                  : item,
                              ),
                            )
                          }
                          options={
                            field === 'grouptype'
                              ? syncValues.grouptypes
                              : field === 'syncedtype'
                                ? syncValues.syncedtypes
                                : syncValues.groupids
                          }
                          sx={{ flex: 1 }}
                        />
                      ),
                    )}
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
            <Box
              sx={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <Typography component="h2" variant="h6">
                Stages
              </Typography>
              <Button startIcon={<AddIcon />} onClick={addStage}>
                Add stage
              </Button>
            </Box>
            {stages.length === 0 && (
              <Typography color="text.secondary">
                Add a stage to build the workflow.
              </Typography>
            )}
            {stages.map((stage, stageIndex) => {
              const availableOutputs = [...outputLocations.entries()]
                .filter(
                  ([output, sourceStage]) => output && sourceStage < stageIndex,
                )
                .map(([output]) => output);
              return (
                <Box
                  key={stage.editorId}
                  component="fieldset"
                  sx={{
                    border: '1px solid',
                    borderColor: 'divider',
                    borderRadius: 1,
                    p: 2,
                  }}
                >
                  <Typography component="legend" variant="subtitle1">
                    Stage {stageIndex + 1}
                  </Typography>
                  <Box
                    sx={{
                      display: 'flex',
                      justifyContent: 'flex-end',
                      gap: 0.5,
                      mb: 1,
                    }}
                  >
                    <IconButton
                      aria-label={`Move stage ${stageIndex + 1} up`}
                      disabled={stageIndex === 0}
                      onClick={() => moveStage(stageIndex, -1)}
                    >
                      <ArrowUpwardIcon />
                    </IconButton>
                    <IconButton
                      aria-label={`Move stage ${stageIndex + 1} down`}
                      disabled={stageIndex === stages.length - 1}
                      onClick={() => moveStage(stageIndex, 1)}
                    >
                      <ArrowDownwardIcon />
                    </IconButton>
                    <IconButton
                      aria-label={`Remove stage ${stageIndex + 1}`}
                      onClick={() =>
                        setStages((current) =>
                          clearInvalidInputs(
                            current.filter(
                              (item) => item.editorId !== stage.editorId,
                            ),
                          ),
                        )
                      }
                    >
                      <DeleteOutlineIcon />
                    </IconButton>
                  </Box>
                  {stage.activities.map((activity, activityIndex) => {
                    const fields = [
                      ...(activitySchemas[activity.type] ?? []),
                      ...subSchema(activity),
                    ];
                    const definition = activityDefinition(activity);
                    const inputDisabled = availableOutputs.length === 0;
                    return (
                      <Box
                        key={activity.editorId}
                        component="fieldset"
                        sx={{
                          border: '1px solid',
                          borderColor: 'divider',
                          borderRadius: 1,
                          p: 2,
                          mb: 1.5,
                        }}
                      >
                        <Typography component="legend" variant="subtitle2">
                          Activity {activityIndex + 1}
                        </Typography>
                        <Box
                          sx={{
                            display: 'flex',
                            flexWrap: 'wrap',
                            gap: 1,
                            alignItems: 'center',
                            mb: 2,
                          }}
                        >
                          <FormControl size="small" sx={{ minWidth: 180 }}>
                            <InputLabel>Activity type</InputLabel>
                            <Select
                              label="Activity type"
                              value={activity.type}
                              onChange={(event) => {
                                const type = event.target.value;
                                updateActivity(activity.editorId, (item) => ({
                                  ...item,
                                  type,
                                  parameters: schemaDefaults(
                                    activitySchemas[type] ?? [],
                                  ),
                                  queryParameters: [],
                                }));
                              }}
                            >
                              {activityTypes.map((type) => (
                                <MenuItem key={type} value={type}>
                                  {type}
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                          {definition?.description && (
                            <Tooltip
                              title={definition.description}
                              arrow
                              describeChild
                            >
                              <IconButton
                                aria-label={`Help for ${activity.type || 'activity type'}`}
                                size="small"
                              >
                                <HelpOutlineIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          )}
                          <TextField
                            label="Output name"
                            value={activity.output}
                            onChange={(event) =>
                              renameOutput(
                                activity.editorId,
                                event.target.value,
                              )
                            }
                            size="small"
                            sx={{ minWidth: 200 }}
                          />
                          <Tooltip
                            title={`Produces ${schemaSummary(definition?.output_schema)}.`}
                            arrow
                            describeChild
                          >
                            <IconButton
                              aria-label="Help for Output name"
                              size="small"
                            >
                              <HelpOutlineIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                          <Tooltip
                            title={
                              inputDisabled
                                ? 'Inputs can only reference outputs from earlier stages; none are available here.'
                                : ''
                            }
                            arrow
                            describeChild
                          >
                            <Box
                              component="span"
                              aria-label={
                                inputDisabled ? 'Input unavailable' : undefined
                              }
                              tabIndex={inputDisabled ? 0 : undefined}
                              sx={{ display: 'inline-flex', minWidth: 200 }}
                            >
                              <FormControl
                                disabled={inputDisabled}
                                size="small"
                                fullWidth
                              >
                                <InputLabel
                                  id={`${activity.editorId}-input-label`}
                                >
                                  Input
                                </InputLabel>
                                <Select
                                  labelId={`${activity.editorId}-input-label`}
                                  label="Input"
                                  value={activity.input ?? ''}
                                  onChange={(event) =>
                                    updateActivity(
                                      activity.editorId,
                                      (item) => ({
                                        ...item,
                                        input: event.target.value || null,
                                      }),
                                    )
                                  }
                                >
                                  <MenuItem value="">
                                    <em>No input</em>
                                  </MenuItem>
                                  {availableOutputs.map((output) => (
                                    <MenuItem key={output} value={output}>
                                      {output}
                                    </MenuItem>
                                  ))}
                                  {activity.input &&
                                    !availableOutputs.includes(
                                      activity.input,
                                    ) && (
                                      <MenuItem value={activity.input} disabled>
                                        {activity.input} (not from an earlier
                                        stage)
                                      </MenuItem>
                                    )}
                                </Select>
                              </FormControl>
                            </Box>
                          </Tooltip>
                          <Box sx={{ flex: 1 }} />
                          <IconButton
                            aria-label={`Move activity ${activityIndex + 1} up`}
                            disabled={activityIndex === 0}
                            onClick={() =>
                              reorderActivity(stageIndex, activityIndex, -1)
                            }
                          >
                            <ArrowUpwardIcon />
                          </IconButton>
                          <IconButton
                            aria-label={`Move activity ${activityIndex + 1} down`}
                            disabled={
                              activityIndex === stage.activities.length - 1
                            }
                            onClick={() =>
                              reorderActivity(stageIndex, activityIndex, 1)
                            }
                          >
                            <ArrowDownwardIcon />
                          </IconButton>
                          <IconButton
                            aria-label={`Move activity ${activityIndex + 1} to previous stage`}
                            disabled={stageIndex === 0}
                            onClick={() =>
                              moveActivity(stageIndex, activityIndex, -1)
                            }
                          >
                            <ArrowBackIcon />
                          </IconButton>
                          <IconButton
                            aria-label={`Move activity ${activityIndex + 1} to next stage`}
                            disabled={stageIndex === stages.length - 1}
                            onClick={() =>
                              moveActivity(stageIndex, activityIndex, 1)
                            }
                          >
                            <ArrowForwardIcon />
                          </IconButton>
                          <IconButton
                            aria-label={`Remove activity ${activityIndex + 1}`}
                            onClick={() =>
                              setStages((current) =>
                                clearInvalidInputs(
                                  current.map((item) =>
                                    item.editorId === stage.editorId
                                      ? {
                                          ...item,
                                          activities: item.activities.filter(
                                            (candidate) =>
                                              candidate.editorId !==
                                              activity.editorId,
                                          ),
                                        }
                                      : item,
                                  ),
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
                                updateActivity(activity.editorId, (item) => {
                                  const dependent = dependentSchemas[item.type];
                                  const parameters = { ...item.parameters };
                                  if (dependent?.discriminator === field.name) {
                                    for (const oldField of dependent.schemas[
                                      String(parameters[field.name] ?? '')
                                    ] ?? [])
                                      delete parameters[oldField.name];
                                    Object.assign(
                                      parameters,
                                      schemaDefaults(
                                        dependent.schemas[String(fieldValue)] ??
                                          [],
                                      ),
                                    );
                                  }
                                  parameters[field.name] = fieldValue;
                                  return { ...item, parameters };
                                })
                              }
                            />
                          ))}
                          {activity.type === 'query' && (
                            <Box>
                              <Button
                                size="small"
                                startIcon={<AddIcon />}
                                onClick={() =>
                                  updateActivity(activity.editorId, (item) => ({
                                    ...item,
                                    queryParameters: [
                                      ...item.queryParameters,
                                      {
                                        editorId: editorId('parameter'),
                                        name: '',
                                        value: '',
                                      },
                                    ],
                                  }))
                                }
                              >
                                Add query parameter
                              </Button>
                              {activity.queryParameters.map(
                                (parameter, parameterIndex) => (
                                  <Box
                                    key={parameter.editorId}
                                    sx={{ display: 'flex', gap: 1, mt: 1 }}
                                  >
                                    <TextField
                                      label="Parameter name"
                                      value={parameter.name}
                                      onChange={(event) =>
                                        updateActivity(
                                          activity.editorId,
                                          (item) => ({
                                            ...item,
                                            queryParameters:
                                              item.queryParameters.map(
                                                (candidate) =>
                                                  candidate.editorId ===
                                                  parameter.editorId
                                                    ? {
                                                        ...candidate,
                                                        name: event.target
                                                          .value,
                                                      }
                                                    : candidate,
                                              ),
                                          }),
                                        )
                                      }
                                      size="small"
                                    />
                                    <TextField
                                      label="Value (JSON or text)"
                                      value={parameter.value}
                                      onChange={(event) =>
                                        updateActivity(
                                          activity.editorId,
                                          (item) => ({
                                            ...item,
                                            queryParameters:
                                              item.queryParameters.map(
                                                (candidate) =>
                                                  candidate.editorId ===
                                                  parameter.editorId
                                                    ? {
                                                        ...candidate,
                                                        value:
                                                          event.target.value,
                                                      }
                                                    : candidate,
                                              ),
                                          }),
                                        )
                                      }
                                      size="small"
                                      fullWidth
                                    />
                                    <IconButton
                                      aria-label={`Remove query parameter ${parameterIndex + 1}`}
                                      onClick={() =>
                                        updateActivity(
                                          activity.editorId,
                                          (item) => ({
                                            ...item,
                                            queryParameters:
                                              item.queryParameters.filter(
                                                (candidate) =>
                                                  candidate.editorId !==
                                                  parameter.editorId,
                                              ),
                                          }),
                                        )
                                      }
                                    >
                                      <DeleteOutlineIcon />
                                    </IconButton>
                                  </Box>
                                ),
                              )}
                            </Box>
                          )}
                        </Box>
                      </Box>
                    );
                  })}
                  <Button
                    startIcon={<AddIcon />}
                    onClick={() => addActivity(stage.editorId)}
                  >
                    Add activity
                  </Button>
                </Box>
              );
            })}
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
