import { useContext, useEffect, useState } from 'react';
import { Helmet } from 'react-helmet';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  Divider,
  Paper,
  Stack,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import WorkflowDialog from 'src/components/WorkflowDialog';
import { AuthContext } from 'src/auth.context';
import {
  ActionConfigDependentSchema,
  ActionConfigFieldDef,
  SeizuConfig,
} from 'src/config.context';
import { usePermissions } from 'src/hooks/usePermissions';
import {
  WorkflowRequest,
  useWorkflow,
  useWorkflowMutations,
} from 'src/hooks/useWorkflowsApi';
import { pageContentSx } from 'src/theme/layout';

type Run = {
  workflow_id: string;
  run_id: string;
  workflow_name: string;
  status: string;
  start_time: string | null;
  close_time: string | null;
};

export default function WorkflowView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { accessToken } = useContext(AuthContext);
  const hasPermission = usePermissions();
  const { workflow, loading, error, refresh } = useWorkflow(id ?? null);
  const { updateWorkflow, runWorkflow } = useWorkflowMutations();
  const [editOpen, setEditOpen] = useState(false);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [activityConfig, setActivityConfig] = useState<{
    types: string[];
    schemas: Record<string, ActionConfigFieldDef[]>;
    dependent: Record<string, ActionConfigDependentSchema>;
  }>({ types: [], schemas: {}, dependent: {} });

  useEffect(() => {
    fetch('/api/v1/config')
      .then((response) => response.json() as Promise<SeizuConfig>)
      .then((config) =>
        setActivityConfig({
          types: config.workflow_activity_types ?? [],
          schemas: config.workflow_activity_schemas ?? {},
          dependent: config.workflow_activity_dependent_schemas ?? {},
        }),
      )
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!id) return;
    fetch(`/api/v1/workflows/${encodeURIComponent(id)}/runs`, {
      headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
    })
      .then((response) => (response.ok ? response.json() : { runs: [] }))
      .then((data: { runs: Run[] }) => setRuns(data.runs ?? []))
      .catch(() => setRuns([]));
  }, [accessToken, id, workflow?.last_run_at]);

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

  return (
    <Box sx={pageContentSx}>
      <Helmet>
        <title>{workflow.name} | Workflows | Seizu</title>
      </Helmet>
      <Button
        startIcon={<ArrowBackIcon />}
        onClick={() => navigate('/app/workflows')}
      >
        Back to workflows
      </Button>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, my: 2 }}>
        <Typography variant="h1" sx={{ flex: 1 }}>
          {workflow.name}
        </Typography>
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
          disabled={!hasPermission('workflows:write')}
          onClick={() => setEditOpen(true)}
        >
          Edit
        </Button>
        <Button
          variant="contained"
          startIcon={<PlayArrowIcon />}
          disabled={!hasPermission('workflows:write')}
          onClick={async () => {
            setOperationError(null);
            try {
              await runWorkflow(workflow.workflow_id);
              refresh();
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
      <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
          <Chip
            label={workflow.enabled ? 'Enabled' : 'Disabled'}
            color={workflow.enabled ? 'success' : 'default'}
          />
          <Chip label={`Version ${workflow.current_version}`} />
          <Chip label={`${Object.keys(workflow.inputs).length} inputs`} />
          <Chip label={`${workflow.activities.length} activities`} />
        </Stack>
        <Typography color="text.secondary">
          Last run:{' '}
          {workflow.last_run_at
            ? new Date(workflow.last_run_at).toLocaleString()
            : 'Never'}{' '}
          ({workflow.last_run_status ?? 'no status'})
        </Typography>
      </Paper>
      <Typography component="h2" variant="h5" sx={{ mb: 1 }}>
        Query inputs
      </Typography>
      {Object.entries(workflow.inputs).map(([inputId, input]) => (
        <Paper key={inputId} variant="outlined" sx={{ p: 2, mb: 2 }}>
          <Typography variant="h6">{inputId}</Typography>
          <Box
            component="pre"
            sx={{
              whiteSpace: 'pre-wrap',
              overflowWrap: 'anywhere',
              bgcolor: 'action.hover',
              p: 1.5,
              borderRadius: 1,
            }}
          >
            {input.cypher}
          </Box>
          {input.parameters.length > 0 && (
            <Typography variant="body2">
              Parameters: {JSON.stringify(input.parameters)}
            </Typography>
          )}
        </Paper>
      ))}
      <Typography component="h2" variant="h5" sx={{ mb: 1 }}>
        Ordered activities
      </Typography>
      <Stack
        divider={<Divider flexItem />}
        component={Paper}
        variant="outlined"
        sx={{ mb: 3 }}
      >
        {workflow.activities.length === 0 && (
          <Typography sx={{ p: 2 }} color="text.secondary">
            No activities.
          </Typography>
        )}
        {workflow.activities.map((activity, index) => (
          <Box key={`${activity.type}-${index}`} sx={{ p: 2 }}>
            <Typography variant="subtitle1">
              {index + 1}. {activity.type}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Input: {activity.input ?? 'none'}
            </Typography>
            <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>
              {JSON.stringify(activity.parameters)}
            </Typography>
          </Box>
        ))}
      </Stack>
      <Typography component="h2" variant="h5" sx={{ mb: 1 }}>
        Recent runs
      </Typography>
      <Paper variant="outlined">
        {runs.length === 0 && (
          <Typography sx={{ p: 2 }} color="text.secondary">
            No Temporal runs are visible yet.
          </Typography>
        )}
        {runs.map((run) => (
          <Box
            key={run.run_id}
            sx={{ p: 2, borderBottom: '1px solid', borderColor: 'divider' }}
          >
            <Typography>
              {run.status} · {run.workflow_name}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {run.start_time
                ? new Date(run.start_time).toLocaleString()
                : 'Unknown start time'}
            </Typography>
          </Box>
        ))}
      </Paper>
      {editOpen && (
        <WorkflowDialog
          open
          initial={workflow}
          activityTypes={activityConfig.types}
          activitySchemas={activityConfig.schemas}
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
