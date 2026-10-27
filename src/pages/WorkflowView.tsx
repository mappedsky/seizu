import { useEffect, useState } from 'react';
import PageTitle from 'src/components/PageTitle';
import { useNavigate, useParams } from 'react-router-dom';
import { Alert, Box, Button, Stack, Typography } from '@mui/material';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import EditIcon from '@mui/icons-material/Edit';
import ExtensionIcon from '@mui/icons-material/Extension';
import HistoryIcon from '@mui/icons-material/History';
import ManageSearchIcon from '@mui/icons-material/ManageSearch';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import ListViewState from 'src/components/ListViewState';
import TemporalRunList from 'src/components/TemporalRunList';
import UserDisplay from 'src/components/UserDisplay';
import WorkflowDialog from 'src/components/WorkflowDialog';
import WorkflowStageLanes, {
  LaneTile,
  StatusMark,
} from 'src/components/WorkflowStageLanes';
import {
  ActionConfigFieldDef,
  SeizuConfig,
  WorkflowActivityDefinition,
} from 'src/config.context';
import { usePermissions } from 'src/hooks/usePermissions';
import {
  WorkflowActivity,
  WorkflowItem,
  WorkflowRequest,
  WorkflowRunSummary,
  useWorkflow,
  useWorkflowMutations,
  useWorkflowRunDetail,
  useWorkflowRuns,
  useWorkflowsList,
} from 'src/hooks/useWorkflowsApi';
import type { BackState } from 'src/navigation';
import { describeSchedule } from 'src/scheduleSpec';
import { pageContentSx } from 'src/theme/layout';
import { fontFamilyMono } from 'src/theme/typography';

function triggerLabel(workflow: WorkflowItem): string {
  if (workflow.watch_scans.length > 0)
    return `${workflow.watch_scans.length} watch scan${workflow.watch_scans.length === 1 ? '' : 's'}`;
  return workflow.schedule
    ? describeSchedule(workflow.schedule)
    : 'Manual only';
}

function Fact({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <Box sx={{ py: 1.25 }}>
      <Typography component="dt" variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography component="dd" variant="body2" sx={{ m: 0, mt: 0.25 }}>
        {children}
      </Typography>
    </Box>
  );
}

function parameterValue(value: unknown): string {
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

function ActivityIcon({ type, selected }: { type: string; selected: boolean }) {
  const color = selected ? 'primary' : 'action';
  if (type === 'query')
    return <ManageSearchIcon fontSize="small" color={color} />;
  if (type === 'workflow')
    return <AccountTreeIcon fontSize="small" color={color} />;
  return <ExtensionIcon fontSize="small" color={color} />;
}

function ActivityTile({
  activity,
  selected,
  onSelect,
}: {
  activity: WorkflowActivity;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <LaneTile selected={selected} onClick={onSelect}>
      <Box sx={{ alignItems: 'center', display: 'flex', gap: 1 }}>
        <ActivityIcon type={activity.type} selected={selected} />
        <Typography
          variant="body2"
          sx={{ fontWeight: 600, wordBreak: 'break-word' }}
        >
          {activity.type}
        </Typography>
      </Box>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ fontFamily: fontFamilyMono, wordBreak: 'break-word' }}
      >
        {activity.input ?? 'No input'} → {activity.output}
      </Typography>
    </LaneTile>
  );
}

function ActivityParameters({ activity }: { activity: WorkflowActivity }) {
  const parameters = Object.entries(activity.parameters);
  return (
    <Box
      sx={{ border: 1, borderColor: 'divider', borderRadius: 1, mt: 2, p: 2 }}
    >
      <Box
        sx={{
          alignItems: 'baseline',
          display: 'flex',
          flexWrap: 'wrap',
          gap: 1.5,
          mb: 1.5,
        }}
      >
        <Typography component="h3" variant="h5">
          {activity.type}
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontFamily: fontFamilyMono }}
        >
          {activity.input ? `reads ${activity.input} · ` : ''}writes{' '}
          {activity.output}
        </Typography>
      </Box>
      {parameters.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No parameters.
        </Typography>
      ) : (
        <Stack spacing={1.25}>
          {parameters.map(([name, value]) => (
            <Box key={name}>
              <Typography variant="caption" color="text.secondary">
                {name}
              </Typography>
              <Box
                component="pre"
                sx={{
                  bgcolor: 'action.hover',
                  borderRadius: 1,
                  fontFamily: fontFamilyMono,
                  fontSize: 12,
                  m: 0,
                  maxHeight: name === 'cypher' ? 320 : 180,
                  overflow: 'auto',
                  p: 1.25,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {parameterValue(value)}
              </Box>
            </Box>
          ))}
        </Stack>
      )}
    </Box>
  );
}

function WorkflowPipeline({ workflow }: { workflow: WorkflowItem }) {
  const [selected, setSelected] = useState<string | null>(null);
  const selectedActivity = workflow.stages
    .flatMap((stage) => stage.activities)
    .find((activity) => activity.output === selected);
  return (
    <>
      <WorkflowStageLanes
        label="Workflow stages"
        lanes={workflow.stages.map((stage, stageIndex) => ({
          key: `stage-${stageIndex + 1}`,
          title: `Stage ${stageIndex + 1}`,
          meta:
            stage.activities.length > 1
              ? `${stage.activities.length} in parallel`
              : undefined,
          children: stage.activities.map((activity) => (
            <ActivityTile
              key={activity.output}
              activity={activity}
              selected={activity.output === selected}
              onSelect={() =>
                setSelected((current) =>
                  current === activity.output ? null : activity.output,
                )
              }
            />
          )),
        }))}
      />
      {selectedActivity && <ActivityParameters activity={selectedActivity} />}
    </>
  );
}

function SectionHeading({
  title,
  meta,
}: {
  title: string;
  meta?: React.ReactNode;
}) {
  return (
    <Box
      sx={{
        alignItems: 'baseline',
        display: 'flex',
        flexWrap: 'wrap',
        gap: 1.5,
        mb: 2,
      }}
    >
      <Typography component="h2" variant="h4">
        {title}
      </Typography>
      {meta && (
        <Typography variant="body2" color="text.secondary">
          {meta}
        </Typography>
      )}
    </Box>
  );
}

export default function WorkflowView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const hasPermission = usePermissions();
  const { workflow, loading, error, refresh } = useWorkflow(id ?? null);
  const { workflows: workflowOptions } = useWorkflowsList({ poll: false });
  const {
    runs,
    error: runsError,
    refresh: refreshRuns,
  } = useWorkflowRuns(id ?? null);
  const fetchRunDetail = useWorkflowRunDetail();
  const { updateWorkflow, runWorkflow, cancelWorkflowRun } =
    useWorkflowMutations();
  const [editOpen, setEditOpen] = useState(false);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [activityConfig, setActivityConfig] = useState<{
    types: string[];
    schemas: Record<string, ActionConfigFieldDef[]>;
    definitions: Record<string, WorkflowActivityDefinition>;
  }>({ types: [], schemas: {}, definitions: {} });

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/v1/config', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('Failed to load activity config.');
        return response.json() as Promise<SeizuConfig>;
      })
      .then((config) =>
        setActivityConfig({
          types: config.workflow_activity_types ?? [],
          schemas: config.workflow_activity_schemas ?? {},
          definitions: config.workflow_activity_definitions ?? {},
        }),
      )
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  if (loading)
    return (
      <Box sx={pageContentSx}>
        <ListViewState loading>{null}</ListViewState>
      </Box>
    );
  if (error || !workflow)
    return (
      <Box sx={pageContentSx}>
        <Alert severity="error">Failed to load workflow.</Alert>
      </Box>
    );

  const activeRun = runs?.find((run) =>
    ['running', 'scheduled', 'waiting'].includes(run.status),
  );
  const currentStatus = activeRun?.status ?? workflow.last_run_status;
  const activityCount = workflow.stages.reduce(
    (total, stage) => total + stage.activities.length,
    0,
  );
  const canMutate = hasPermission('workflows:write');
  const loadRunDetail = (run: WorkflowRunSummary) =>
    fetchRunDetail(workflow.workflow_id, run.workflow_id, run.run_id);

  return (
    <Box sx={pageContentSx}>
      <PageTitle>{workflow.name} | Workflows | Seizu</PageTitle>
      <Button
        size="small"
        startIcon={<ArrowBackIcon />}
        onClick={() => navigate('/app/workflows')}
        sx={{ mb: 1 }}
      >
        Back to workflows
      </Button>
      <Box
        sx={{
          alignItems: 'flex-start',
          display: 'flex',
          flexWrap: 'wrap',
          gap: 2,
          justifyContent: 'space-between',
          mb: 3,
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="h1">{workflow.name}</Typography>
          <Box
            sx={{
              alignItems: 'center',
              color: 'text.secondary',
              display: 'flex',
              flexWrap: 'wrap',
              columnGap: 1.5,
              rowGap: 0.5,
              mt: 1,
            }}
          >
            <StatusMark status={currentStatus} size="medium" />
            {/* Disabled changes what the status means, so it stays beside it;
                trigger and version live in the details column. */}
            {!workflow.enabled && (
              <>
                <Typography variant="body2" aria-hidden="true">
                  ·
                </Typography>
                <Typography variant="body2">Disabled</Typography>
              </>
            )}
          </Box>
        </Box>
        <Box
          sx={{
            display: 'flex',
            flexShrink: 0,
            gap: 1,
            '& .MuiButton-root': { whiteSpace: 'nowrap' },
          }}
        >
          <Button
            startIcon={<HistoryIcon />}
            onClick={() =>
              navigate(`/app/workflows/${workflow.workflow_id}/history`, {
                state: {
                  fromLabel: workflow.name,
                } satisfies BackState,
              })
            }
          >
            History
          </Button>
          <Button
            startIcon={<EditIcon />}
            disabled={!canMutate}
            onClick={() => setEditOpen(true)}
          >
            Edit
          </Button>
          <Button
            variant="contained"
            startIcon={<PlayArrowIcon />}
            disabled={!canMutate}
            onClick={async () => {
              setOperationError(null);
              try {
                await runWorkflow(workflow.workflow_id);
                refresh();
                refreshRuns();
              } catch (reason) {
                setOperationError(
                  reason instanceof Error
                    ? reason.message
                    : 'Failed to run workflow.',
                );
              }
            }}
          >
            Run now
          </Button>
        </Box>
      </Box>
      {operationError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {operationError}
        </Alert>
      )}
      {workflow.schedule_sync_status !== 'synced' && (
        <Alert
          severity={
            workflow.schedule_sync_status === 'error' ? 'error' : 'warning'
          }
          sx={{ mb: 2 }}
        >
          Temporal Schedule synchronization is {workflow.schedule_sync_status}.
          {workflow.schedule_sync_error
            ? ` ${workflow.schedule_sync_error}`
            : ' The worker will retry automatically.'}
        </Alert>
      )}
      {workflow.last_errors.map((entry) => (
        <Alert key={entry.timestamp} severity="error" sx={{ mb: 2 }}>
          <Typography variant="caption" sx={{ display: 'block' }}>
            {new Date(entry.timestamp).toLocaleString()}
          </Typography>
          {entry.error}
        </Alert>
      ))}

      <Box
        sx={{
          display: 'grid',
          gap: { xs: 4, lg: 5 },
          gridTemplateColumns: {
            xs: 'minmax(0, 1fr)',
            lg: 'minmax(0, 1fr) 16rem',
          },
          alignItems: 'start',
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Box component="section" sx={{ mb: 5 }}>
            <SectionHeading
              title="Pipeline"
              meta={`${workflow.stages.length} stages · ${activityCount} activities`}
            />
            <WorkflowPipeline workflow={workflow} />
          </Box>
          <Box component="section">
            <SectionHeading
              title="Runs"
              meta={
                runs && runs.length > 0 ? `Latest ${runs.length}` : undefined
              }
            />
            <TemporalRunList
              runs={runs}
              error={runsError}
              loadDetail={loadRunDetail}
              cancelRun={
                canMutate
                  ? async (run) => {
                      await cancelWorkflowRun(
                        workflow.workflow_id,
                        run.workflow_id,
                        run.run_id,
                      );
                      refreshRuns();
                    }
                  : undefined
              }
            />
          </Box>
        </Box>
        <Box
          component="aside"
          aria-label="Workflow details"
          sx={{
            gridRow: { xs: 1, lg: 'auto' },
            position: { lg: 'sticky' },
            top: { lg: 88 },
          }}
        >
          <Box
            component="dl"
            sx={{
              display: { xs: 'grid', lg: 'block' },
              gridTemplateColumns: {
                xs: 'repeat(auto-fill, minmax(10rem, 1fr))',
              },
              columnGap: 3,
              m: 0,
              '& > div + div': {
                // Width, style and color set separately: the responsive
                // shorthand would reset the color to the text color.
                borderTopColor: 'divider',
                borderTopStyle: 'solid',
                borderTopWidth: { xs: 0, lg: 1 },
              },
            }}
          >
            <Fact label="Trigger">{triggerLabel(workflow)}</Fact>
            <Fact label="Availability">
              {workflow.enabled ? 'Enabled' : 'Disabled'}
            </Fact>
            <Fact label="Then run">
              {workflow.trigger_workflows.length
                ? workflow.trigger_workflows
                    .map(
                      (workflowId) =>
                        workflowOptions.find(
                          (option) => option.workflow_id === workflowId,
                        )?.name ?? 'Unavailable workflow',
                    )
                    .join(', ')
                : 'None'}
            </Fact>
            <Fact label="Last run">
              {workflow.last_run_at
                ? new Date(workflow.last_run_at).toLocaleString()
                : 'Never'}
            </Fact>
            <Fact label="Owner">
              <UserDisplay userId={workflow.created_by} />
            </Fact>
            <Fact label="Version">v{workflow.current_version}</Fact>
            <Fact label="Updated">
              {new Date(workflow.updated_at).toLocaleString()}
            </Fact>
          </Box>
        </Box>
      </Box>

      {editOpen && (
        <WorkflowDialog
          open
          initial={workflow}
          activityTypes={activityConfig.types}
          activitySchemas={activityConfig.schemas}
          activityDefinitions={activityConfig.definitions}
          workflowOptions={workflowOptions}
          onClose={() => setEditOpen(false)}
          onSave={async (request: WorkflowRequest) => {
            await updateWorkflow(workflow.workflow_id, request);
            refresh();
          }}
        />
      )}
    </Box>
  );
}
