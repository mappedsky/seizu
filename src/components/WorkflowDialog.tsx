import { FormEvent, ReactNode, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Checkbox,
  Dialog,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Radio,
  RadioGroup,
  Select,
  Slide,
  Switch,
  TextField,
  Tooltip,
  Typography,
  useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import AddIcon from '@mui/icons-material/Add';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import ArrowDownwardIcon from '@mui/icons-material/ArrowDownward';
import ArrowForwardIcon from '@mui/icons-material/ArrowForward';
import ArrowUpwardIcon from '@mui/icons-material/ArrowUpward';
import CloseIcon from '@mui/icons-material/Close';
import DeleteOutlineIcon from '@mui/icons-material/Delete';
import HelpOutlineIcon from '@mui/icons-material/HelpOutlineOutlined';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ScheduleSpecEditor from 'src/components/ScheduleSpecEditor';
import SyncMetadataAutocomplete from 'src/components/SyncMetadataAutocomplete';
import { LaneTile } from 'src/components/WorkflowStageLanes';
import {
  ActionConfigFieldDef,
  WorkflowActivityDefinition,
} from 'src/config.context';
import { useSyncMetadataValues } from 'src/hooks/useSyncMetadataValues';
import { useSelectableModelProfiles } from 'src/hooks/useModelProfilesApi';
import {
  WorkflowActivity,
  WorkflowItem,
  WorkflowRequest,
  WorkflowWatchScan,
} from 'src/hooks/useWorkflowsApi';
import { ScheduleSpec, describeSchedule } from 'src/scheduleSpec';
import { fontFamilyMono } from 'src/theme/typography';

const TRIGGER = 'trigger';
const COMPLETION = 'completion';

// DOM id of a flow tile, so focus can return to it from the inspector.
const nodeDomId = (node: string) => `workflow-node-${node}`;

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

/** A cheap identity for the form's content, for unsaved-change detection. */
function formFingerprint(values: {
  name: string;
  enabled: boolean;
  triggerType: string;
  schedule: ScheduleSpec | null;
  watchScans: WorkflowWatchScan[];
  triggerWorkflows: TriggerWorkflowForm[];
  stages: StageForm[];
  comment: string;
}): string {
  return JSON.stringify({
    ...values,
    // The schedule editor may re-emit an equivalent spec on mount; compare
    // what it means rather than how it is spelled.
    schedule: values.schedule ? describeSchedule(values.schedule) : null,
    triggerWorkflows: values.triggerWorkflows.map((item) => item.workflowId),
  });
}

function blankActivity(id: string, output: string): ActivityForm {
  return {
    editorId: id,
    type: '',
    input: null,
    output,
    parameters: {},
    queryParameters: [],
    moduleRuns: [],
  };
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
  optionLabels,
}: {
  field: ActionConfigFieldDef;
  value: unknown;
  onChange: (value: unknown) => void;
  optionLabels?: Record<string, string>;
}) {
  if (field.type === 'parameters' || field.type === 'module_runs') return null;
  const isCode = field.name === 'cypher';
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
              {optionLabels?.[option] ?? option}
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
        minRows={field.type === 'text' ? (isCode ? 6 : 3) : undefined}
        slotProps={{
          htmlInput: {
            ...(field.type === 'number'
              ? { min: field.minimum, max: field.maximum }
              : {}),
            ...(isCode
              ? {
                  spellCheck: false,
                  sx: { fontFamily: fontFamilyMono, fontSize: 13 },
                }
              : {}),
          },
        }}
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

function ModelProfileConfigField({
  field,
  value,
  onChange,
}: {
  field: ActionConfigFieldDef;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const { profiles } = useSelectableModelProfiles();
  return (
    <ConfigField
      field={{
        ...field,
        type: 'select',
        options: profiles.map((profile) => profile.profile_id),
      }}
      optionLabels={Object.fromEntries(
        profiles.map((profile) => [
          profile.profile_id,
          `${profile.name}${profile.is_default ? ' (default)' : ''}`,
        ]),
      )}
      value={value}
      onChange={onChange}
    />
  );
}

type Problem = { target: string | null; message: string };

/**
 * One row of the editor's flow map: a rail on the left carrying a node
 * marker, and the row's content beside it. Rows read top to bottom in the
 * order the workflow runs.
 */
function FlowRow({
  node,
  first = false,
  last = false,
  children,
}: {
  node: 'anchor' | 'stage' | 'end' | 'add';
  first?: boolean;
  last?: boolean;
  children: ReactNode;
}) {
  const theme = useTheme();
  const nodeColor =
    node === 'anchor'
      ? theme.palette.secondary.main
      : node === 'add'
        ? theme.palette.text.disabled
        : theme.palette.primary.main;
  return (
    <Box
      sx={{
        display: 'grid',
        gridTemplateColumns: '1.75rem minmax(0, 1fr)',
        columnGap: 1.5,
      }}
    >
      <Box aria-hidden="true" sx={{ position: 'relative' }}>
        <Box
          sx={{
            position: 'absolute',
            left: '50%',
            top: first ? 18 : 0,
            bottom: last ? 'calc(100% - 18px)' : 0,
            borderLeft: 1,
            borderColor: 'divider',
            borderLeftStyle: node === 'add' || last ? 'dashed' : 'solid',
          }}
        />
        <Box
          sx={{
            position: 'absolute',
            left: '50%',
            top: 12,
            width: 12,
            height: 12,
            ml: '-6px',
            borderRadius: '50%',
            bgcolor:
              node === 'anchor' ? nodeColor : theme.palette.background.default,
            border: 2,
            borderColor: nodeColor,
          }}
        />
      </Box>
      <Box sx={{ pb: last ? 0 : 3, minWidth: 0 }}>{children}</Box>
    </Box>
  );
}

function FlowHeading({
  title,
  meta,
  actions,
  problem,
}: {
  title: string;
  meta?: string;
  actions?: ReactNode;
  problem?: string;
}) {
  return (
    <Box sx={{ mb: 1.25 }}>
      <Box
        sx={{ alignItems: 'center', display: 'flex', gap: 1, minHeight: 36 }}
      >
        <Typography component="h3" variant="h5">
          {title}
        </Typography>
        {meta && (
          <Typography variant="caption" color="text.secondary">
            {meta}
          </Typography>
        )}
        {actions && (
          <Box sx={{ display: 'flex', gap: 0.25, ml: 'auto' }}>{actions}</Box>
        )}
      </Box>
      {problem && (
        <Typography variant="caption" color="error.main" component="p">
          {problem}
        </Typography>
      )}
    </Box>
  );
}

function InspectorSection({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Box component="section" sx={{ '& + &': { mt: 3.5 } }}>
      <Box
        sx={{
          alignItems: 'center',
          display: 'flex',
          gap: 1,
          justifyContent: 'space-between',
          mb: 1.5,
          minHeight: 30,
        }}
      >
        <Typography component="h3" variant="subtitle2">
          {title}
        </Typography>
        {action}
      </Box>
      {children}
    </Box>
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
  >(() => triggerWorkflowForms(initial));
  const [stages, setStages] = useState<StageForm[]>(() => stageForms(initial));
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  // Problems are marked only once a save has been tried, so a workflow being
  // built is not covered in errors for fields nobody has reached yet.
  const [attempted, setAttempted] = useState(false);
  const currentFingerprint = formFingerprint({
    name,
    enabled,
    triggerType,
    schedule,
    watchScans,
    triggerWorkflows,
    stages,
    comment,
  });
  const [baseline] = useState(() => currentFingerprint);
  const [discardOpen, setDiscardOpen] = useState(false);
  const requestClose = () => {
    if (saving) return;
    if (currentFingerprint === baseline) onClose();
    else setDiscardOpen(true);
  };
  const [selected, setSelected] = useState<string>(
    () => stages[0]?.activities[0]?.editorId ?? TRIGGER,
  );
  const syncValues = useSyncMetadataValues(open);
  const theme = useTheme();
  // Below md the inspector is a second screen that slides over the flow.
  const narrow = useMediaQuery(theme.breakpoints.down('md'));
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const backRef = useRef<HTMLButtonElement>(null);
  const select = (node: string) => {
    setSelected(node);
    setInspectorOpen(true);
  };

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

  const addStage = () => {
    const activityId = editorId('activity');
    setStages((current) => {
      const next = [
        ...current,
        { editorId: editorId('stage'), activities: [] },
      ];
      const stageIndex = next.length - 1;
      next[stageIndex] = {
        ...next[stageIndex],
        activities: [
          blankActivity(activityId, nextOutputName(next, stageIndex)),
        ],
      };
      return next;
    });
    select(activityId);
  };

  const addActivity = (stageId: string) => {
    const activityId = editorId('activity');
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
                blankActivity(activityId, output),
              ],
            }
          : stage,
      );
    });
    select(activityId);
  };

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

  const activityProblem = (
    activity: ActivityForm,
    seen: Set<string>,
    available: Set<string>,
  ): string | null => {
    if (!activity.type) return 'Choose an activity type.';
    if (!/^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/.test(activity.output))
      return 'Output names must use lower_snake_case and start with a letter.';
    if (seen.has(activity.output))
      return `Duplicate output '${activity.output}'.`;
    if (activity.input && !available.has(activity.input))
      return `Input '${activity.input}' must come from an earlier stage.`;
    if (activityDefinition(activity)?.input_required && !activity.input)
      return `Activity '${activity.output}' requires an input.`;
    const moduleRunsField = (activitySchemas[activity.type] ?? []).find(
      (field) => field.type === 'module_runs',
    );
    if (moduleRunsField) {
      if (moduleRunsField.required && !activity.moduleRuns.length)
        return `Activity '${activity.output}' requires at least one intel module.`;
      if (activity.moduleRuns.some((run) => !run.module))
        return `Activity '${activity.output}' has an intel module without a selection.`;
    }
    if (activity.type === 'query') {
      if (!String(activity.parameters.cypher ?? '').trim())
        return `Query activity '${activity.output}' requires Cypher.`;
      const names = activity.queryParameters.map((parameter) =>
        parameter.name.trim(),
      );
      if (names.some((parameter) => !parameter))
        return `Query activity '${activity.output}' has an unnamed parameter.`;
      if (names.includes('input'))
        return "Query parameter 'input' is reserved.";
      if (new Set(names).size !== names.length)
        return `Query activity '${activity.output}' has duplicate parameters.`;
    }
    return null;
  };

  const problems: Problem[] = [];
  if (!name.trim())
    problems.push({ target: 'name', message: 'Name is required.' });
  if (triggerType === 'schedule' && schedule === null)
    problems.push({
      target: TRIGGER,
      message: 'Complete the schedule before saving.',
    });
  if (triggerWorkflows.some((workflow) => !workflow.workflowId))
    problems.push({
      target: COMPLETION,
      message: 'Select a workflow for every completion trigger.',
    });
  if (!stages.length)
    problems.push({
      target: null,
      message: 'Every workflow requires at least one non-empty stage.',
    });
  {
    const seen = new Set<string>();
    const available = new Set<string>();
    for (const stage of stages) {
      if (!stage.activities.length)
        problems.push({
          target: `stage:${stage.editorId}`,
          message: 'Add an activity or remove this stage.',
        });
      for (const activity of stage.activities) {
        const message = activityProblem(activity, seen, available);
        if (message) problems.push({ target: activity.editorId, message });
        seen.add(activity.output);
      }
      stage.activities.forEach((activity) => available.add(activity.output));
    }
  }
  const problemFor = (target: string): string | undefined =>
    attempted
      ? problems.find((problem) => problem.target === target)?.message
      : undefined;

  const activityIds = stages.flatMap((stage) =>
    stage.activities.map((activity) => activity.editorId),
  );
  // A removed selection falls back to the trigger rather than to nothing.
  const current =
    selected === COMPLETION || activityIds.includes(selected)
      ? selected
      : TRIGGER;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaveError(null);
    setAttempted(true);
    const first = problems[0];
    if (first) {
      if (
        first.target &&
        first.target !== 'name' &&
        !first.target.startsWith('stage:')
      )
        select(first.target);
      return;
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
      setSaveError(
        reason instanceof Error ? reason.message : 'Failed to save workflow.',
      );
    } finally {
      setSaving(false);
    }
  };

  const workflowName = (workflowId: string) =>
    workflowOptions.find((workflow) => workflow.workflow_id === workflowId)
      ?.name ?? 'Unavailable workflow';

  const triggerSummary =
    triggerType === 'manual'
      ? 'Manual only'
      : triggerType === 'schedule'
        ? schedule
          ? describeSchedule(schedule)
          : 'Schedule incomplete'
        : `${watchScans.length} watch scan${watchScans.length === 1 ? '' : 's'}`;
  const completionSummary = triggerWorkflows.length
    ? triggerWorkflows
        .map((trigger) =>
          trigger.workflowId ? workflowName(trigger.workflowId) : 'Not chosen',
        )
        .join(', ')
    : 'Nothing';

  const updateParameter = (activityId: string, name: string, value: unknown) =>
    updateActivity(activityId, (item) => ({
      ...item,
      parameters: { ...item.parameters, [name]: value },
    }));

  const removeActivity = (stageId: string, activityId: string) =>
    setStages((current) =>
      clearInvalidInputs(
        current.map((item) =>
          item.editorId === stageId
            ? {
                ...item,
                activities: item.activities.filter(
                  (candidate) => candidate.editorId !== activityId,
                ),
              }
            : item,
        ),
      ),
    );

  const renderTriggerInspector = () => (
    <>
      <Typography component="h2" variant="h4">
        Trigger
      </Typography>
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ mt: 0.5, mb: 3 }}
      >
        What starts a run. Any workflow can also be run by hand.
      </Typography>
      <InspectorSection title="Starts on">
        <RadioGroup
          aria-label="Trigger"
          value={triggerType}
          onChange={(event) =>
            setTriggerType(
              event.target.value as 'manual' | 'schedule' | 'watch_scans',
            )
          }
        >
          <FormControlLabel value="manual" control={<Radio />} label="Manual" />
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
      </InspectorSection>
      {triggerType === 'manual' ? (
        <Typography color="text.secondary" variant="body2">
          This workflow runs only when started manually or triggered by another
          workflow.
        </Typography>
      ) : triggerType === 'schedule' ? (
        <InspectorSection title="Schedule">
          <ScheduleSpecEditor
            initial={schedule ?? defaultSchedule}
            onChange={setSchedule}
            allowMinutes
          />
        </InspectorSection>
      ) : (
        <InspectorSection
          title="Watch scans"
          action={
            <Button
              size="small"
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
          }
        >
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            Runs when a matching sync finishes. Each field is a regular
            expression.
          </Typography>
          {watchScans.map((scan, index) => (
            <Box
              // eslint-disable-next-line @eslint-react/no-array-index-key -- watch scans carry no identity of their own
              key={index}
              role="group"
              aria-label={`Watch scan ${index + 1}`}
              sx={{
                border: 1,
                borderColor: 'divider',
                borderRadius: 1,
                display: 'grid',
                gap: 1.25,
                mb: 1.25,
                p: 1.5,
              }}
            >
              <Box sx={{ alignItems: 'center', display: 'flex' }}>
                <Typography variant="caption" color="text.secondary">
                  Watch scan {index + 1}
                </Typography>
                <IconButton
                  aria-label={`Remove watch scan ${index + 1}`}
                  size="small"
                  sx={{ ml: 'auto' }}
                  onClick={() =>
                    setWatchScans((value) =>
                      value.filter((_, itemIndex) => itemIndex !== index),
                    )
                  }
                >
                  <DeleteOutlineIcon fontSize="small" />
                </IconButton>
              </Box>
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
                  />
                ),
              )}
            </Box>
          ))}
        </InspectorSection>
      )}
    </>
  );

  const renderCompletionInspector = () => {
    const canAdd =
      !triggerWorkflows.some((workflow) => !workflow.workflowId) &&
      workflowOptions.some(
        (workflow) =>
          workflow.workflow_id !== initial?.workflow_id &&
          !triggerWorkflows.some(
            (chosen) => chosen.workflowId === workflow.workflow_id,
          ),
      );
    return (
      <>
        <Typography component="h2" variant="h4">
          Then run
        </Typography>
        <Typography
          variant="body2"
          color="text.secondary"
          sx={{ mt: 0.5, mb: 3 }}
        >
          Workflows started after every stage of this one succeeds.
        </Typography>
        <InspectorSection
          title="Workflows"
          action={
            <Button
              size="small"
              startIcon={<AddIcon />}
              disabled={!canAdd}
              onClick={() =>
                setTriggerWorkflows((value) => [
                  ...value,
                  { editorId: editorId('trigger-workflow'), workflowId: '' },
                ])
              }
            >
              Add workflow
            </Button>
          }
        >
          {triggerWorkflows.length === 0 && (
            <Typography color="text.secondary" variant="body2">
              No workflows run after this one.
            </Typography>
          )}
          {triggerWorkflows.map((trigger, triggerIndex) => {
            const chosenElsewhere = new Set(
              triggerWorkflows
                .filter((item) => item.editorId !== trigger.editorId)
                .map((item) => item.workflowId)
                .filter(Boolean),
            );
            const optionIds = workflowOptions
              .filter(
                (workflow) =>
                  workflow.workflow_id !== initial?.workflow_id &&
                  !chosenElsewhere.has(workflow.workflow_id),
              )
              .map((workflow) => workflow.workflow_id);
            if (trigger.workflowId && !optionIds.includes(trigger.workflowId))
              optionIds.unshift(trigger.workflowId);
            return (
              <Box
                key={trigger.editorId}
                sx={{ alignItems: 'center', display: 'flex', gap: 1, mb: 1.25 }}
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
                    setTriggerWorkflows((value) =>
                      value.map((item) =>
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
                    setTriggerWorkflows((value) =>
                      value.filter(
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
        </InspectorSection>
      </>
    );
  };

  const renderActivityInspector = (
    stageIndex: number,
    activityIndex: number,
  ) => {
    const stage = stages[stageIndex];
    const activity = stage.activities[activityIndex];
    const fields = activitySchemas[activity.type] ?? [];
    const moduleRunsField = fields.find(
      (field) => field.type === 'module_runs',
    );
    const definition = activityDefinition(activity);
    const availableOutputs = [...outputLocations.entries()]
      .filter(([output, sourceStage]) => output && sourceStage < stageIndex)
      .map(([output]) => output);
    const inputDisabled = availableOutputs.length === 0;
    const settingFields = fields.filter(
      (field) => field.type !== 'parameters' && field.type !== 'module_runs',
    );
    return (
      <>
        <Typography
          component="h2"
          variant="h4"
          sx={{ wordBreak: 'break-word' }}
        >
          {activity.type || 'New activity'}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          Stage {stageIndex + 1}, activity {activityIndex + 1}
          {stage.activities.length > 1
            ? ` of ${stage.activities.length} running in parallel`
            : ''}
        </Typography>
        <Box
          sx={{
            alignItems: 'center',
            display: 'flex',
            gap: 0.25,
            mt: 1.5,
            mb: 3,
          }}
        >
          <Tooltip title="Earlier in this stage" describeChild>
            <span>
              <IconButton
                size="small"
                aria-label={`Move activity ${activityIndex + 1} up`}
                disabled={activityIndex === 0}
                onClick={() => reorderActivity(stageIndex, activityIndex, -1)}
              >
                <ArrowUpwardIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Later in this stage" describeChild>
            <span>
              <IconButton
                size="small"
                aria-label={`Move activity ${activityIndex + 1} down`}
                disabled={activityIndex === stage.activities.length - 1}
                onClick={() => reorderActivity(stageIndex, activityIndex, 1)}
              >
                <ArrowDownwardIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Move to the previous stage" describeChild>
            <span>
              <IconButton
                size="small"
                aria-label={`Move activity ${activityIndex + 1} to previous stage`}
                disabled={stageIndex === 0}
                onClick={() => moveActivity(stageIndex, activityIndex, -1)}
              >
                <ArrowBackIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Move to the next stage" describeChild>
            <span>
              <IconButton
                size="small"
                aria-label={`Move activity ${activityIndex + 1} to next stage`}
                disabled={stageIndex === stages.length - 1}
                onClick={() => moveActivity(stageIndex, activityIndex, 1)}
              >
                <ArrowForwardIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Button
            size="small"
            color="error"
            startIcon={<DeleteOutlineIcon />}
            sx={{ ml: 'auto' }}
            onClick={() => removeActivity(stage.editorId, activity.editorId)}
          >
            Remove activity
          </Button>
        </Box>

        <InspectorSection title="Activity">
          <FormControl size="small" fullWidth>
            <InputLabel id={`${activity.editorId}-type-label`}>
              Activity type
            </InputLabel>
            <Select
              labelId={`${activity.editorId}-type-label`}
              label="Activity type"
              value={activity.type}
              onChange={(event) => {
                const type = event.target.value;
                updateActivity(activity.editorId, (item) => ({
                  ...item,
                  type,
                  parameters: schemaDefaults(activitySchemas[type] ?? []),
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
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              {definition.description}
            </Typography>
          )}
        </InspectorSection>

        <InspectorSection title="Data">
          <Box sx={{ display: 'grid', gap: 1.5 }}>
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
                aria-label={inputDisabled ? 'Input unavailable' : undefined}
                tabIndex={inputDisabled ? 0 : undefined}
                sx={{ display: 'flex' }}
              >
                <FormControl disabled={inputDisabled} size="small" fullWidth>
                  <InputLabel id={`${activity.editorId}-input-label`}>
                    Input
                  </InputLabel>
                  <Select
                    labelId={`${activity.editorId}-input-label`}
                    label="Input"
                    value={activity.input ?? ''}
                    onChange={(event) =>
                      updateActivity(activity.editorId, (item) => ({
                        ...item,
                        input: event.target.value || null,
                      }))
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
                      !availableOutputs.includes(activity.input) && (
                        <MenuItem value={activity.input} disabled>
                          {activity.input} (not from an earlier stage)
                        </MenuItem>
                      )}
                  </Select>
                </FormControl>
              </Box>
            </Tooltip>
            <Box sx={{ alignItems: 'flex-start', display: 'flex', gap: 0.5 }}>
              <TextField
                label="Output name"
                value={activity.output}
                onChange={(event) =>
                  renameOutput(activity.editorId, event.target.value)
                }
                size="small"
                fullWidth
                slotProps={{
                  htmlInput: { sx: { fontFamily: fontFamilyMono } },
                }}
              />
              <Tooltip
                title={`Produces ${schemaSummary(definition?.output_schema)}.`}
                arrow
                describeChild
              >
                <IconButton aria-label="Help for Output name" size="small">
                  <HelpOutlineIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Box>
          </Box>
        </InspectorSection>

        {settingFields.length > 0 && (
          <InspectorSection title="Settings">
            <Box sx={{ display: 'grid', gap: 1.5 }}>
              {settingFields.map((field) =>
                field.name === 'model_profile_id' ? (
                  <ModelProfileConfigField
                    key={field.name}
                    field={field}
                    value={activity.parameters[field.name]}
                    onChange={(value) =>
                      updateParameter(activity.editorId, field.name, value)
                    }
                  />
                ) : (
                  <ConfigField
                    key={field.name}
                    field={field}
                    value={activity.parameters[field.name]}
                    onChange={(value) =>
                      updateParameter(activity.editorId, field.name, value)
                    }
                  />
                ),
              )}
            </Box>
          </InspectorSection>
        )}

        {moduleRunsField && (
          <InspectorSection
            title={moduleRunsField.label}
            action={
              <Button
                size="small"
                startIcon={<AddIcon />}
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
            }
          >
            {moduleRunsField.description && (
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mb: 1.5 }}
              >
                {moduleRunsField.description}
              </Typography>
            )}
            <Box component="ol" sx={{ listStyle: 'none', m: 0, p: 0 }}>
              {activity.moduleRuns.map((run, runIndex) => {
                const paramFields =
                  moduleRunsField.item_schemas?.[run.module] ?? [];
                return (
                  <Box
                    component="li"
                    key={run.editorId}
                    sx={{
                      border: 1,
                      borderColor: 'divider',
                      borderRadius: 1,
                      mb: 1,
                      p: 1.25,
                    }}
                  >
                    <Box sx={{ alignItems: 'center', display: 'flex', gap: 1 }}>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{
                          fontVariantNumeric: 'tabular-nums',
                          minWidth: '1.25rem',
                        }}
                      >
                        {runIndex + 1}.
                      </Typography>
                      <FormControl size="small" sx={{ flex: 1, minWidth: 0 }}>
                        <InputLabel id={`${run.editorId}-module-label`}>
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
                                  moduleRunsField.item_schemas?.[
                                    event.target.value
                                  ] ?? [],
                                ),
                              }),
                            )
                          }
                        >
                          {(moduleRunsField.options ?? []).map((option) => (
                            <MenuItem key={option} value={option}>
                              {option}
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                      <IconButton
                        size="small"
                        aria-label={`Move intel module ${runIndex + 1} up`}
                        disabled={runIndex === 0}
                        onClick={() =>
                          reorderModuleRun(activity.editorId, run.editorId, -1)
                        }
                      >
                        <ArrowUpwardIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        size="small"
                        aria-label={`Move intel module ${runIndex + 1} down`}
                        disabled={runIndex === activity.moduleRuns.length - 1}
                        onClick={() =>
                          reorderModuleRun(activity.editorId, run.editorId, 1)
                        }
                      >
                        <ArrowDownwardIcon fontSize="small" />
                      </IconButton>
                      <IconButton
                        size="small"
                        aria-label={`Remove intel module ${runIndex + 1}`}
                        onClick={() =>
                          updateActivity(activity.editorId, (item) => ({
                            ...item,
                            moduleRuns: item.moduleRuns.filter(
                              (candidate) =>
                                candidate.editorId !== run.editorId,
                            ),
                          }))
                        }
                      >
                        <DeleteOutlineIcon fontSize="small" />
                      </IconButton>
                    </Box>
                    {paramFields.length > 0 && (
                      <Box
                        sx={{ display: 'grid', gap: 1.25, mt: 1.25, pl: 3.5 }}
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
                                    [paramField.name]: paramValue,
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
            </Box>
          </InspectorSection>
        )}

        {activity.type === 'query' && (
          <InspectorSection
            title="Query parameters"
            action={
              <Button
                size="small"
                startIcon={<AddIcon />}
                onClick={() =>
                  updateActivity(activity.editorId, (item) => ({
                    ...item,
                    queryParameters: [
                      ...item.queryParameters,
                      { editorId: editorId('parameter'), name: '', value: '' },
                    ],
                  }))
                }
              >
                Add query parameter
              </Button>
            }
          >
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              {activity.input
                ? `Referenced as $name in the Cypher. ${activity.input} arrives as $input.`
                : 'Referenced as $name in the Cypher.'}
            </Typography>
            {activity.queryParameters.map((parameter, parameterIndex) => {
              const setParameter = (update: Partial<ParameterForm>) =>
                updateActivity(activity.editorId, (item) => ({
                  ...item,
                  queryParameters: item.queryParameters.map((candidate) =>
                    candidate.editorId === parameter.editorId
                      ? { ...candidate, ...update }
                      : candidate,
                  ),
                }));
              return (
                <Box
                  key={parameter.editorId}
                  sx={{
                    display: 'grid',
                    gap: 1,
                    gridTemplateColumns: '10rem minmax(0, 1fr) auto',
                    mb: 1,
                  }}
                >
                  <TextField
                    label="Parameter name"
                    value={parameter.name}
                    onChange={(event) =>
                      setParameter({ name: event.target.value })
                    }
                    size="small"
                  />
                  <TextField
                    label="Value (JSON or text)"
                    value={parameter.value}
                    onChange={(event) =>
                      setParameter({ value: event.target.value })
                    }
                    size="small"
                  />
                  <IconButton
                    aria-label={`Remove query parameter ${parameterIndex + 1}`}
                    onClick={() =>
                      updateActivity(activity.editorId, (item) => ({
                        ...item,
                        queryParameters: item.queryParameters.filter(
                          (candidate) =>
                            candidate.editorId !== parameter.editorId,
                        ),
                      }))
                    }
                  >
                    <DeleteOutlineIcon />
                  </IconButton>
                </Box>
              );
            })}
          </InspectorSection>
        )}
      </>
    );
  };

  const selectedPosition = (() => {
    for (let stageIndex = 0; stageIndex < stages.length; stageIndex += 1) {
      const activityIndex = stages[stageIndex].activities.findIndex(
        (activity) => activity.editorId === current,
      );
      if (activityIndex >= 0) return { stageIndex, activityIndex };
    }
    return null;
  })();

  const inspectorBody =
    current === TRIGGER
      ? renderTriggerInspector()
      : current === COMPLETION
        ? renderCompletionInspector()
        : selectedPosition
          ? renderActivityInspector(
              selectedPosition.stageIndex,
              selectedPosition.activityIndex,
            )
          : null;

  const bannerMessage =
    saveError ?? (attempted && problems.length ? problems[0].message : null);

  return (
    <Dialog
      open={open}
      onClose={requestClose}
      fullScreen
      aria-labelledby="workflow-editor-title"
      // Sized to the dynamic viewport: on mobile browsers `100%` of a fixed
      // container can be the toolbar-hidden height, which puts the footer
      // past the visible edge while the toolbar is showing.
      sx={{
        '& .MuiDialog-container': {
          alignItems: 'flex-start',
          '@supports (height: 100dvh)': { height: '100dvh' },
        },
      }}
      slotProps={{
        paper: {
          sx: {
            bgcolor: 'background.default',
            backgroundImage: 'none',
            '@supports (height: 100dvh)': {
              height: '100dvh',
              maxHeight: '100dvh',
            },
          },
        },
      }}
    >
      <Box
        component="form"
        onSubmit={submit}
        noValidate
        sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}
      >
        <Box
          component="header"
          sx={{
            alignItems: 'center',
            borderBottom: 1,
            borderColor: 'divider',
            columnGap: 2,
            display: 'flex',
            flexShrink: 0,
            flexWrap: 'wrap',
            px: { xs: 1.5, sm: 2.5 },
            py: 1.25,
            rowGap: 1,
          }}
        >
          <IconButton
            aria-label="Close editor"
            onClick={requestClose}
            edge="start"
          >
            <CloseIcon />
          </IconButton>
          <Typography
            id="workflow-editor-title"
            component="h1"
            variant="h4"
            sx={{ whiteSpace: 'nowrap' }}
          >
            {initial ? 'Edit workflow' : 'New workflow'}
          </Typography>
          <TextField
            label="Name"
            name="name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            size="small"
            error={Boolean(problemFor('name'))}
            // On a phone the name takes the second row to itself.
            sx={{
              flex: { xs: '1 1 100%', sm: '1 1 12rem' },
              maxWidth: { sm: 480 },
              order: { xs: 1, sm: 0 },
            }}
          />
          <FormControlLabel
            control={
              <Switch
                checked={enabled}
                onChange={(event) => setEnabled(event.target.checked)}
              />
            }
            label="Enabled"
            sx={{ ml: { xs: 'auto', sm: 0 }, mr: 0 }}
          />
        </Box>
        {bannerMessage && (
          <Alert severity="error" square sx={{ flexShrink: 0 }}>
            <span>{bannerMessage}</span>
            {!saveError && problems.length > 1 && (
              <Box component="span" sx={{ color: 'text.secondary' }}>
                {` · ${problems.length - 1} more marked below`}
              </Box>
            )}
          </Alert>
        )}

        <Box
          sx={{
            display: 'grid',
            flex: 1,
            gridTemplateColumns: {
              xs: 'minmax(0, 1fr)',
              // Grows with the screen: Cypher and config fields need the
              // width more than the flow does.
              md: 'minmax(0, 1fr) minmax(24rem, 42%)',
              lg: 'minmax(0, 1fr) minmax(28rem, 38%)',
            },
            gridTemplateRows: 'minmax(0, 1fr)',
            minHeight: 0,
            overflow: 'hidden',
            position: 'relative',
          }}
        >
          <Box
            inert={narrow && inspectorOpen}
            sx={{
              overflow: 'auto',
              px: { xs: 1.5, sm: 3 },
              py: 3,
            }}
          >
            <Box sx={{ maxWidth: 960 }}>
              <FlowRow node="anchor" first>
                <FlowHeading title="Trigger" />
                <Box sx={{ maxWidth: 320 }}>
                  <LaneTile
                    selected={current === TRIGGER}
                    id={nodeDomId(TRIGGER)}
                    onClick={() => select(TRIGGER)}
                    invalid={Boolean(problemFor(TRIGGER))}
                  >
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {triggerSummary}
                    </Typography>
                    <Typography
                      variant="caption"
                      color={
                        problemFor(TRIGGER) ? 'error.main' : 'text.secondary'
                      }
                    >
                      {problemFor(TRIGGER) ??
                        (triggerType === 'manual'
                          ? 'Runs when started by hand'
                          : triggerType === 'schedule'
                            ? 'Schedule'
                            : 'After matching syncs')}
                    </Typography>
                  </LaneTile>
                </Box>
              </FlowRow>

              {stages.length === 0 && (
                <FlowRow node="stage">
                  <Typography
                    variant="body2"
                    color={attempted ? 'error.main' : 'text.secondary'}
                    sx={{ pt: 1 }}
                  >
                    A workflow needs at least one stage. Add one to start.
                  </Typography>
                </FlowRow>
              )}

              {stages.map((stage, stageIndex) => (
                <FlowRow key={stage.editorId} node="stage">
                  <Box role="group" aria-label={`Stage ${stageIndex + 1}`}>
                    <FlowHeading
                      title={`Stage ${stageIndex + 1}`}
                      meta={
                        stage.activities.length > 1
                          ? `${stage.activities.length} in parallel`
                          : undefined
                      }
                      problem={problemFor(`stage:${stage.editorId}`)}
                      actions={
                        <>
                          <IconButton
                            size="small"
                            aria-label={`Move stage ${stageIndex + 1} up`}
                            disabled={stageIndex === 0}
                            onClick={() => moveStage(stageIndex, -1)}
                          >
                            <ArrowUpwardIcon fontSize="small" />
                          </IconButton>
                          <IconButton
                            size="small"
                            aria-label={`Move stage ${stageIndex + 1} down`}
                            disabled={stageIndex === stages.length - 1}
                            onClick={() => moveStage(stageIndex, 1)}
                          >
                            <ArrowDownwardIcon fontSize="small" />
                          </IconButton>
                          <IconButton
                            size="small"
                            aria-label={`Remove stage ${stageIndex + 1}`}
                            onClick={() =>
                              setStages((value) =>
                                clearInvalidInputs(
                                  value.filter(
                                    (item) => item.editorId !== stage.editorId,
                                  ),
                                ),
                              )
                            }
                          >
                            <DeleteOutlineIcon fontSize="small" />
                          </IconButton>
                        </>
                      }
                    />
                    <Box
                      sx={{
                        display: 'grid',
                        gap: 1,
                        gridTemplateColumns:
                          'repeat(auto-fill, minmax(min(100%, 13.5rem), 1fr))',
                      }}
                    >
                      {stage.activities.map((activity) => {
                        const problem = problemFor(activity.editorId);
                        return (
                          <LaneTile
                            key={activity.editorId}
                            selected={current === activity.editorId}
                            id={nodeDomId(activity.editorId)}
                            onClick={() => select(activity.editorId)}
                            invalid={Boolean(problem)}
                          >
                            <Typography
                              variant="body2"
                              sx={{
                                color: activity.type
                                  ? 'text.primary'
                                  : 'text.secondary',
                                fontStyle: activity.type ? 'normal' : 'italic',
                                fontWeight: 600,
                                wordBreak: 'break-word',
                              }}
                            >
                              {activity.type || 'New activity'}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{
                                fontFamily: fontFamilyMono,
                                wordBreak: 'break-word',
                              }}
                            >
                              {activity.input ?? 'No input'} →{' '}
                              {activity.output || '—'}
                            </Typography>
                            {problem && (
                              <Typography variant="caption" color="error.main">
                                {problem}
                              </Typography>
                            )}
                          </LaneTile>
                        );
                      })}
                      <Button
                        onClick={() => addActivity(stage.editorId)}
                        startIcon={<AddIcon />}
                        sx={{
                          border: 1,
                          borderColor: 'divider',
                          borderStyle: 'dashed',
                          color: 'text.secondary',
                          minHeight: 64,
                          '&:hover': {
                            borderColor: 'primary.main',
                            borderStyle: 'dashed',
                          },
                        }}
                      >
                        Add activity
                      </Button>
                    </Box>
                  </Box>
                </FlowRow>
              ))}

              <FlowRow node="add">
                <Button
                  startIcon={<AddIcon />}
                  onClick={addStage}
                  sx={{ mt: 0.25 }}
                >
                  Add stage
                </Button>
              </FlowRow>

              <FlowRow node="end" last>
                <FlowHeading title="Then run" />
                <Box sx={{ maxWidth: 320 }}>
                  <LaneTile
                    selected={current === COMPLETION}
                    id={nodeDomId(COMPLETION)}
                    onClick={() => select(COMPLETION)}
                    invalid={Boolean(problemFor(COMPLETION))}
                  >
                    <Typography
                      variant="body2"
                      sx={{ fontWeight: 600, wordBreak: 'break-word' }}
                    >
                      {completionSummary}
                    </Typography>
                    <Typography
                      variant="caption"
                      color={
                        problemFor(COMPLETION) ? 'error.main' : 'text.secondary'
                      }
                    >
                      {problemFor(COMPLETION) ?? 'After every stage succeeds'}
                    </Typography>
                  </LaneTile>
                </Box>
              </FlowRow>
            </Box>
          </Box>

          {narrow ? (
            <Slide
              in={inspectorOpen}
              direction="left"
              timeout={reduceMotion ? 0 : undefined}
              mountOnEnter
              unmountOnExit
              onEntered={() => backRef.current?.focus()}
              onExited={() =>
                document.getElementById(nodeDomId(current))?.focus()
              }
            >
              <Box
                component="aside"
                aria-label="Inspector"
                sx={{
                  bgcolor: 'background.default',
                  inset: 0,
                  overflow: 'auto',
                  position: 'absolute',
                  zIndex: 1,
                }}
              >
                <Box
                  sx={{
                    bgcolor: 'background.default',
                    borderBottom: 1,
                    borderColor: 'divider',
                    position: 'sticky',
                    px: 1,
                    py: 0.75,
                    top: 0,
                    zIndex: 1,
                  }}
                >
                  <Button
                    ref={backRef}
                    startIcon={<ArrowBackIcon />}
                    onClick={() => setInspectorOpen(false)}
                  >
                    Back to workflow
                  </Button>
                </Box>
                <Box sx={{ px: 1.5, py: 2.5 }}>{inspectorBody}</Box>
              </Box>
            </Slide>
          ) : (
            <Box
              component="aside"
              aria-label="Inspector"
              sx={{
                bgcolor: 'background.paper',
                borderRadius: 2,
                m: 1.5,
                ml: 0,
                overflow: 'auto',
                px: 3,
                py: 3,
              }}
            >
              {inspectorBody}
            </Box>
          )}
        </Box>
        <Box
          component="footer"
          sx={{
            alignItems: 'center',
            // The shorthand resets the color, so it must come first.
            borderTop: 1,
            borderColor: 'divider',
            columnGap: 2,
            display: 'flex',
            flexShrink: 0,
            pb: 'max(10px, env(safe-area-inset-bottom))',
            pt: 1.25,
            px: { xs: 1.5, sm: 2.5 },
            rowGap: 1.25,
            flexWrap: { xs: 'nowrap', sm: 'wrap' },
          }}
        >
          {initial && (
            <TextField
              label="Version comment"
              placeholder="Optional: what changed"
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              size="small"
              // Shares the row with Save on a phone rather than taking its own.
              sx={{
                flex: { xs: '1 1 0', sm: '1 1 20rem' },
                maxWidth: 560,
                minWidth: 0,
              }}
            />
          )}
          <Box sx={{ display: 'flex', gap: 1, ml: 'auto' }}>
            {/* On a phone the header's close button is the way out. */}
            <Button
              onClick={requestClose}
              disabled={saving}
              sx={{ display: { xs: 'none', sm: 'inline-flex' } }}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="contained"
              disabled={saving}
              sx={{ whiteSpace: 'nowrap' }}
            >
              {saving ? <ConstellationSpinner size={20} /> : 'Save workflow'}
            </Button>
          </Box>
        </Box>
      </Box>
      <ConfirmDeleteDialog
        open={discardOpen}
        title="Discard unsaved changes?"
        confirmLabel="Discard"
        onClose={() => setDiscardOpen(false)}
        onConfirm={() => {
          setDiscardOpen(false);
          onClose();
        }}
      >
        Your edits to{' '}
        <strong>
          {name.trim() || (initial ? initial.name : 'this workflow')}
        </strong>{' '}
        have not been saved. Closing the editor discards them.
      </ConfirmDeleteDialog>
    </Dialog>
  );
}
