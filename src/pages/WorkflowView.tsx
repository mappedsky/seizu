import { useEffect, useState } from 'react';
import { Helmet } from 'react-helmet';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Stack,
  Typography,
} from '@mui/material';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import EditIcon from '@mui/icons-material/Edit';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExtensionIcon from '@mui/icons-material/Extension';
import HistoryIcon from '@mui/icons-material/History';
import ManageSearchIcon from '@mui/icons-material/ManageSearch';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import ListPageHeader from 'src/components/ListPageHeader';
import TemporalRunList from 'src/components/TemporalRunList';
import UserDisplay from 'src/components/UserDisplay';
import WorkflowDialog from 'src/components/WorkflowDialog';
import {
  ActionConfigDependentSchema,
  ActionConfigFieldDef,
  SeizuConfig,
  WorkflowActivityDefinition,
} from 'src/config.context';
import { usePermissions } from 'src/hooks/usePermissions';
import { useCurrentUser } from 'src/hooks/useCurrentUser';
import {
  WorkflowActivity,
  WorkflowItem,
  WorkflowRequest,
  WorkflowRunSummary,
  useWorkflow,
  useWorkflowMutations,
  useWorkflowRunDetail,
  useWorkflowRuns,
} from 'src/hooks/useWorkflowsApi';
import { describeSchedule } from 'src/scheduleSpec';
import { temporalStatusColor, temporalStatusLabel } from 'src/temporalStatus';
import { pageContentSx } from 'src/theme/layout';

function triggerLabel(workflow: WorkflowItem): string {
  if (workflow.watch_scans.length > 0)
    return `${workflow.watch_scans.length} watch scan${workflow.watch_scans.length === 1 ? '' : 's'}`;
  return workflow.schedule
    ? describeSchedule(workflow.schedule)
    : 'Manual only';
}

function DetailItem({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <Box sx={{ minWidth: 140 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography component="div" variant="body2" sx={{ mt: 0.25 }}>
        {children}
      </Typography>
    </Box>
  );
}

function parameterValue(value: unknown): string {
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

function ActivityRow({ activity }: { activity: WorkflowActivity }) {
  const ActivityIcon =
    activity.type === 'query'
      ? ManageSearchIcon
      : activity.type === 'workflow'
        ? AccountTreeIcon
        : ExtensionIcon;
  const parameters = Object.entries(activity.parameters);
  return (
    <Accordion
      disableGutters
      elevation={0}
      square
      slotProps={{ transition: { timeout: 0, unmountOnExit: true } }}
      sx={{
        border: 1,
        borderColor: 'divider',
        borderRadius: 1,
        '&:before': { display: 'none' },
      }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreIcon fontSize="small" />}
        sx={{
          minHeight: 42,
          px: 1.25,
          '& .MuiAccordionSummary-content': {
            alignItems: 'center',
            gap: 1,
            my: 0.75,
          },
        }}
      >
        <ActivityIcon fontSize="small" color="action" />
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {activity.type}
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontFamily: 'monospace', wordBreak: 'break-word' }}
          >
            {activity.input ?? 'No input'} → {activity.output}
          </Typography>
        </Box>
        <Chip
          label={`Output: ${activity.output}`}
          size="small"
          variant="outlined"
          sx={{ ml: 'auto', mr: 1, maxWidth: 260 }}
        />
      </AccordionSummary>
      <AccordionDetails sx={{ px: 1.5, pt: 0, pb: 1.5 }}>
        <Box sx={{ display: 'flex', gap: 3, mb: parameters.length ? 1.5 : 0 }}>
          <DetailItem label="Input">{activity.input ?? 'None'}</DetailItem>
          <DetailItem label="Named output">{activity.output}</DetailItem>
        </Box>
        {parameters.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No parameters.
          </Typography>
        ) : (
          <Stack spacing={1}>
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
                    fontFamily: 'monospace',
                    fontSize: 12,
                    m: 0,
                    maxHeight: name === 'cypher' ? 260 : 180,
                    overflow: 'auto',
                    p: 1,
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
      </AccordionDetails>
    </Accordion>
  );
}

function WorkflowStages({ workflow }: { workflow: WorkflowItem }) {
  return (
    <Box>
      {workflow.stages.map((stage, stageIndex) => (
        <Box key={`stage-${stageIndex + 1}`}>
          <Card variant="outlined">
            <CardContent>
              <Box
                sx={{
                  alignItems: 'center',
                  display: 'flex',
                  gap: 1,
                  mb: 1.5,
                }}
              >
                <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
                  Stage {stageIndex + 1}
                </Typography>
                <Chip
                  label={
                    stage.activities.length === 1
                      ? '1 activity'
                      : `${stage.activities.length} parallel activities`
                  }
                  size="small"
                  variant="outlined"
                />
              </Box>
              <Stack spacing={0.75}>
                {stage.activities.map((activity) => (
                  <ActivityRow key={activity.output} activity={activity} />
                ))}
              </Stack>
            </CardContent>
          </Card>
          {stageIndex < workflow.stages.length - 1 && (
            <Box
              aria-hidden="true"
              sx={{
                bgcolor: 'divider',
                height: 20,
                mx: 'auto',
                width: 2,
              }}
            />
          )}
        </Box>
      ))}
    </Box>
  );
}

export default function WorkflowView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const hasPermission = usePermissions();
  const currentUser = useCurrentUser();
  const { workflow, loading, error, refresh } = useWorkflow(id ?? null);
  const {
    runs,
    error: runsError,
    refresh: refreshRuns,
  } = useWorkflowRuns(id ?? null);
  const fetchRunDetail = useWorkflowRunDetail();
  const { updateWorkflow, runWorkflow } = useWorkflowMutations();
  const [editOpen, setEditOpen] = useState(false);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [activityConfig, setActivityConfig] = useState<{
    types: string[];
    schemas: Record<string, ActionConfigFieldDef[]>;
    definitions: Record<string, WorkflowActivityDefinition>;
    dependent: Record<string, ActionConfigDependentSchema>;
  }>({ types: [], schemas: {}, definitions: {}, dependent: {} });

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
          dependent: config.workflow_activity_dependent_schemas ?? {},
        }),
      )
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  if (loading)
    return (
      <Box sx={pageContentSx}>
        <Typography>Loading workflow…</Typography>
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
  const canMutate =
    workflow.created_by === currentUser?.user_id &&
    hasPermission('workflows:write');
  const loadRunDetail = (run: WorkflowRunSummary) =>
    fetchRunDetail(workflow.workflow_id, run.workflow_id, run.run_id);

  return (
    <Box sx={pageContentSx}>
      <Helmet>
        <title>{workflow.name} | Workflows | Seizu</title>
      </Helmet>
      <Button
        size="small"
        startIcon={<ArrowBackIcon />}
        onClick={() => navigate('/app/workflows')}
        sx={{ mb: 1 }}
      >
        Back to workflows
      </Button>
      <ListPageHeader
        title={workflow.name}
        action={
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button
              startIcon={<HistoryIcon />}
              onClick={() =>
                navigate(`/app/workflows/${workflow.workflow_id}/history`)
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
        }
      />
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

      <Card variant="outlined" sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
            Details
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
            <DetailItem label="Status">
              <Chip
                label={temporalStatusLabel(currentStatus)}
                color={temporalStatusColor(currentStatus)}
                size="small"
                variant="outlined"
              />
            </DetailItem>
            <DetailItem label="Availability">
              <Chip
                label={workflow.enabled ? 'Enabled' : 'Disabled'}
                color={workflow.enabled ? 'success' : 'default'}
                size="small"
              />
            </DetailItem>
            <DetailItem label="Trigger">{triggerLabel(workflow)}</DetailItem>
            <DetailItem label="Version">v{workflow.current_version}</DetailItem>
            <DetailItem label="Pipeline">
              {workflow.stages.length} stages · {activityCount} activities
            </DetailItem>
            <DetailItem label="Owner">
              <UserDisplay userId={workflow.created_by} />
            </DetailItem>
            <DetailItem label="Last run">
              {workflow.last_run_at
                ? new Date(workflow.last_run_at).toLocaleString()
                : 'Never'}
            </DetailItem>
            <DetailItem label="Updated">
              {new Date(workflow.updated_at).toLocaleString()}
            </DetailItem>
          </Box>
        </CardContent>
      </Card>

      {workflow.last_errors.length > 0 && (
        <Card variant="outlined" sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Recent errors
            </Typography>
            {workflow.last_errors.map((entry) => (
              <Alert key={entry.timestamp} severity="error" sx={{ mb: 1 }}>
                <Typography variant="caption" sx={{ display: 'block' }}>
                  {new Date(entry.timestamp).toLocaleString()}
                </Typography>
                {entry.error}
              </Alert>
            ))}
          </CardContent>
        </Card>
      )}

      <Card variant="outlined">
        <CardContent>
          <Typography component="h2" variant="h5" sx={{ mb: 1.5 }}>
            Workflow stages
          </Typography>
          <WorkflowStages workflow={workflow} />
        </CardContent>
      </Card>

      <Card variant="outlined" sx={{ mt: 3 }}>
        <CardContent>
          <Typography component="h2" variant="h5" sx={{ mb: 1.5 }}>
            Recent Temporal runs
          </Typography>
          <TemporalRunList
            runs={runs}
            error={runsError}
            loadDetail={loadRunDetail}
          />
        </CardContent>
      </Card>

      {editOpen && (
        <WorkflowDialog
          open
          initial={workflow}
          activityTypes={activityConfig.types}
          activitySchemas={activityConfig.schemas}
          activityDefinitions={activityConfig.definitions}
          dependentSchemas={activityConfig.dependent}
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
