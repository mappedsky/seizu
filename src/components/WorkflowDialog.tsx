import { FormEvent, ReactNode, useMemo, useState } from 'react';
import {
  Alert,
  Autocomplete,
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
type ModuleRunForm = {
  editorId: string;
  module: string;
  params: Record<string, unknown>;
};
type ActivityForm = WorkflowActivity & {
  editorId: string;
  queryParameters: ParameterForm[];
  moduleRuns: ModuleRunForm[];
};
type StageForm = { editorId: string; activities: ActivityForm[] };
type TriggerWorkflowForm = { editorId: string; workflowId: string };

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
  const rawModuleRuns = Array.isArray(activity.parameters.module_runs)
    ? (activity.parameters.module_runs as {
        module?: unknown;
        params?: unknown;
      }[])
    : [];
  const parameters = { ...activity.parameters };
  delete parameters.parameters;
  delete parameters.module_runs;
  return {
    ...activity,
    parameters,
    editorId: editorId('activity'),
    queryParameters: rawParameters.map((parameter) => ({
      editorId: editorId('parameter'),
      name: String(parameter.name ?? ''),
      value: displayValue(parameter.value),
    })),
    moduleRuns: rawModuleRuns.map((run) => ({
      editorId: editorId('module-run'),
      module: String(run.module ?? ''),
      params:
        run.params && typeof run.params === 'object'
          ? { ...(run.params as Record<string, unknown>) }
          : {},
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

function triggerWorkflowForms(
  initial: WorkflowItem | null,
): TriggerWorkflowForm[] {
  return (initial?.trigger_workflows ?? []).map((workflowId) => ({
    editorId: editorId('trigger-workflow'),
    workflowId,
  }));
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
  if (field.type === 'parameters' || field.type === 'module_runs') return null;
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
        slotProps={
          field.type === 'number'
            ? { htmlInput: { min: field.minimum, max: field.maximum } }
            : undefined
        }
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
  workflowOptions = [],
  onClose,
  onSave,
}: {
  open: boolean;
  initial: WorkflowItem | null;
  activityTypes: string[];
  activitySchemas: Record<string, ActionConfigFieldDef[]>;
  activityDefinitions?: Record<string, WorkflowActivityDefinition>;
  workflowOptions?: WorkflowItem[];
  onClose: () => void;
  onSave: (request: WorkflowRequest) => Promise<void>;
}) {
  const [name, setName] = useState(initial?.name ?? '');
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [triggerType, setTriggerType] = useState<
    'manual' | 'schedule' | 'watch_scans'
  >(
    initial === null
      ? 'schedule'
      : initial.watch_scans.length
        ? 'watch_scans'
        : initial.schedule
          ? 'schedule'
          : 'manual',
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
  const [triggerWorkflows, setTriggerWorkflows] = useState<
    TriggerWorkflowForm[]
  >(triggerWorkflowForms(initial));
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
                  moduleRuns: [],
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

  const activityDefinition = (
    activity: ActivityForm,
  ): WorkflowActivityDefinition | undefined =>
    activityDefinitions[activity.type];

  const updateModuleRun = (
    activityId: string,
    runId: string,
    update: (run: ModuleRunForm) => ModuleRunForm,
  ) =>
    updateActivity(activityId, (item) => ({
      ...item,
      moduleRuns: item.moduleRuns.map((run) =>
        run.editorId === runId ? update(run) : run,
      ),
    }));

  const reorderModuleRun = (
    activityId: string,
    runId: string,
    offset: number,
  ) =>
    updateActivity(activityId, (item) => {
      const index = item.moduleRuns.findIndex((run) => run.editorId === runId);
      const target = index + offset;
      if (index < 0 || target < 0 || target >= item.moduleRuns.length)
        return item;
      const moduleRuns = [...item.moduleRuns];
      [moduleRuns[index], moduleRuns[target]] = [
        moduleRuns[target],
        moduleRuns[index],
      ];
      return { ...item, moduleRuns };
    });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!name.trim()) return setError('Name is required.');
    if (triggerType === 'schedule' && schedule === null)
      return setError('Complete the schedule before saving.');
    if (triggerWorkflows.some((workflow) => !workflow.workflowId))
      return setError('Select a workflow for every completion trigger.');
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
        const moduleRunsField = (activitySchemas[activity.type] ?? []).find(
          (field) => field.type === 'module_runs',
        );
        if (moduleRunsField) {
          if (moduleRunsField.required && !activity.moduleRuns.length)
            return setError(
              `Activity '${activity.output}' requires at least one intel module.`,
            );
          if (activity.moduleRuns.some((run) => !run.module))
            return setError(
              `Activity '${activity.output}' has an intel module without a selection.`,
            );
        }
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
        const fields = activitySchemas[activity.type] ?? [];
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
        if (fields.some((field) => field.type === 'module_runs')) {
          parameters.module_runs = activity.moduleRuns.map((run) => ({
            module: run.module,
            params: Object.fromEntries(
              Object.entries(run.params).filter(([, value]) => value !== ''),
            ),
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
        trigger_workflows: triggerWorkflows.map(
          (workflow) => workflow.workflowId,
        ),
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
                    event.target.value as 'manual' | 'schedule' | 'watch_scans',
                  )
                }
              >
                <FormControlLabel
                  value="manual"
                  control={<Radio />}
                  label="Manual"
                />
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
            {triggerType === 'manual' ? (
              <Typography color="text.secondary" variant="body2">
                This workflow runs only when started manually or triggered by
                another workflow.
              </Typography>
            ) : triggerType === 'schedule' ? (
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
            <Box>
              <Box
                sx={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <FormLabel component="div">
                    On successful completion
                  </FormLabel>
                  <Tooltip
                    title="Trigger any selected workflow after all stages finish."
                    placement="top"
                    arrow
                    describeChild
                  >
                    <IconButton
                      aria-label="Help for On successful completion"
                      size="small"
                    >
                      <HelpOutlineIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Box>
                <Button
                  startIcon={<AddIcon />}
                  disabled={
                    triggerWorkflows.some((workflow) => !workflow.workflowId) ||
                    workflowOptions.filter(
                      (workflow) =>
                        workflow.workflow_id !== initial?.workflow_id &&
                        !triggerWorkflows.some(
                          (selected) =>
                            selected.workflowId === workflow.workflow_id,
                        ),
                    ).length === 0
                  }
                  onClick={() =>
                    setTriggerWorkflows((current) => [
                      ...current,
                      {
                        editorId: editorId('trigger-workflow'),
                        workflowId: '',
                      },
                    ])
                  }
                >
                  Add workflow
                </Button>
              </Box>
              {triggerWorkflows.length === 0 ? (
                <Typography color="text.secondary" variant="body2">
                  No workflows to be triggered.
                </Typography>
              ) : null}
              {triggerWorkflows.map((trigger, triggerIndex) => {
                const selectedByAnotherRow = new Set(
                  triggerWorkflows
                    .filter((item) => item.editorId !== trigger.editorId)
                    .map((item) => item.workflowId)
                    .filter(Boolean),
                );
                const optionIds = workflowOptions
                  .filter(
                    (workflow) =>
                      workflow.workflow_id !== initial?.workflow_id &&
                      !selectedByAnotherRow.has(workflow.workflow_id),
                  )
                  .map((workflow) => workflow.workflow_id);
                if (
                  trigger.workflowId &&
                  !optionIds.includes(trigger.workflowId)
                ) {
                  optionIds.unshift(trigger.workflowId);
                }
                const workflowName = (workflowId: string) =>
                  workflowOptions.find(
                    (workflow) => workflow.workflow_id === workflowId,
                  )?.name ?? 'Unavailable workflow';
                return (
                  <Box
                    key={trigger.editorId}
                    sx={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 1,
                      mt: 1,
                    }}
                  >
                    <Autocomplete
                      options={optionIds}
                      value={trigger.workflowId || null}
                      getOptionLabel={workflowName}
                      getOptionDisabled={(workflowId) =>
                        !workflowOptions.some(
                          (workflow) => workflow.workflow_id === workflowId,
                        )
                      }
                      onChange={(_event, workflowId) =>
                        setTriggerWorkflows((current) =>
                          current.map((item) =>
                            item.editorId === trigger.editorId
                              ? { ...item, workflowId: workflowId ?? '' }
                              : item,
                          ),
                        )
                      }
                      renderInput={(params) => (
                        <TextField
                          {...params}
                          label={`Workflow ${triggerIndex + 1}`}
                          size="small"
                        />
                      )}
                      size="small"
                      sx={{ flex: 1 }}
                    />
                    <IconButton
                      aria-label={`Remove triggered workflow ${triggerIndex + 1}`}
                      onClick={() =>
                        setTriggerWorkflows((current) =>
                          current.filter(
                            (item) => item.editorId !== trigger.editorId,
                          ),
                        )
                      }
                    >
                      <DeleteOutlineIcon />
                    </IconButton>
                  </Box>
                );
              })}
            </Box>
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
                    const fields = activitySchemas[activity.type] ?? [];
                    const moduleRunsField = fields.find(
                      (field) => field.type === 'module_runs',
                    );
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
                                  moduleRuns: [],
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
                                updateActivity(activity.editorId, (item) => ({
                                  ...item,
                                  parameters: {
                                    ...item.parameters,
                                    [field.name]: fieldValue,
                                  },
                                }))
                              }
                            />
                          ))}
                          {moduleRunsField && (
                            <Box>
                              <Box
                                sx={{
                                  display: 'flex',
                                  alignItems: 'center',
                                  gap: 0.5,
                                }}
                              >
                                <Typography variant="subtitle2">
                                  {moduleRunsField.label}
                                </Typography>
                                {moduleRunsField.description && (
                                  <Tooltip
                                    title={moduleRunsField.description}
                                    placement="top"
                                    arrow
                                    describeChild
                                  >
                                    <IconButton
                                      aria-label={`Help for ${moduleRunsField.label}`}
                                      size="small"
                                    >
                                      <HelpOutlineIcon fontSize="small" />
                                    </IconButton>
                                  </Tooltip>
                                )}
                              </Box>
                              {activity.moduleRuns.map((run, runIndex) => {
                                const paramFields =
                                  moduleRunsField.item_schemas?.[run.module] ??
                                  [];
                                return (
                                  <Box
                                    key={run.editorId}
                                    component="fieldset"
                                    sx={{
                                      border: '1px solid',
                                      borderColor: 'divider',
                                      borderRadius: 1,
                                      p: 1.5,
                                      mt: 1,
                                    }}
                                  >
                                    <Box
                                      sx={{
                                        display: 'flex',
                                        gap: 1,
                                        alignItems: 'center',
                                      }}
                                    >
                                      <FormControl
                                        size="small"
                                        sx={{ minWidth: 220 }}
                                      >
                                        <InputLabel
                                          id={`${run.editorId}-module-label`}
                                        >
                                          Intel module
                                        </InputLabel>
                                        <Select
                                          labelId={`${run.editorId}-module-label`}
                                          label="Intel module"
                                          value={run.module}
                                          onChange={(event) =>
                                            updateModuleRun(
                                              activity.editorId,
                                              run.editorId,
                                              (item) => ({
                                                ...item,
                                                module: event.target.value,
                                                params: schemaDefaults(
                                                  moduleRunsField
                                                    .item_schemas?.[
                                                    event.target.value
                                                  ] ?? [],
                                                ),
                                              }),
                                            )
                                          }
                                        >
                                          {(moduleRunsField.options ?? []).map(
                                            (option) => (
                                              <MenuItem
                                                key={option}
                                                value={option}
                                              >
                                                {option}
                                              </MenuItem>
                                            ),
                                          )}
                                        </Select>
                                      </FormControl>
                                      <Box sx={{ flex: 1 }} />
                                      <IconButton
                                        aria-label={`Move intel module ${runIndex + 1} up`}
                                        disabled={runIndex === 0}
                                        onClick={() =>
                                          reorderModuleRun(
                                            activity.editorId,
                                            run.editorId,
                                            -1,
                                          )
                                        }
                                      >
                                        <ArrowUpwardIcon />
                                      </IconButton>
                                      <IconButton
                                        aria-label={`Move intel module ${runIndex + 1} down`}
                                        disabled={
                                          runIndex ===
                                          activity.moduleRuns.length - 1
                                        }
                                        onClick={() =>
                                          reorderModuleRun(
                                            activity.editorId,
                                            run.editorId,
                                            1,
                                          )
                                        }
                                      >
                                        <ArrowDownwardIcon />
                                      </IconButton>
                                      <IconButton
                                        aria-label={`Remove intel module ${runIndex + 1}`}
                                        onClick={() =>
                                          updateActivity(
                                            activity.editorId,
                                            (item) => ({
                                              ...item,
                                              moduleRuns:
                                                item.moduleRuns.filter(
                                                  (candidate) =>
                                                    candidate.editorId !==
                                                    run.editorId,
                                                ),
                                            }),
                                          )
                                        }
                                      >
                                        <DeleteOutlineIcon />
                                      </IconButton>
                                    </Box>
                                    {paramFields.length > 0 && (
                                      <Box
                                        sx={{
                                          display: 'flex',
                                          flexDirection: 'column',
                                          gap: 1.5,
                                          mt: 1.5,
                                        }}
                                      >
                                        {paramFields.map((paramField) => (
                                          <ConfigField
                                            key={paramField.name}
                                            field={paramField}
                                            value={run.params[paramField.name]}
                                            onChange={(paramValue) =>
                                              updateModuleRun(
                                                activity.editorId,
                                                run.editorId,
                                                (item) => ({
                                                  ...item,
                                                  params: {
                                                    ...item.params,
                                                    [paramField.name]:
                                                      paramValue,
                                                  },
                                                }),
                                              )
                                            }
                                          />
                                        ))}
                                      </Box>
                                    )}
                                  </Box>
                                );
                              })}
                              <Button
                                size="small"
                                startIcon={<AddIcon />}
                                sx={{ mt: 1 }}
                                onClick={() =>
                                  updateActivity(activity.editorId, (item) => ({
                                    ...item,
                                    moduleRuns: [
                                      ...item.moduleRuns,
                                      {
                                        editorId: editorId('module-run'),
                                        module: '',
                                        params: {},
                                      },
                                    ],
                                  }))
                                }
                              >
                                Add intel module
                              </Button>
                            </Box>
                          )}
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
