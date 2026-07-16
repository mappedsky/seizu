import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { Helmet } from 'react-helmet';
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
  Snackbar,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { Highlight, themes } from 'prism-react-renderer';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import HistoryIcon from '@mui/icons-material/History';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ListPageHeader from 'src/components/ListPageHeader';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import RunDetailPre from 'src/components/RunDetailPre';
import ScheduledQueryDialog from 'src/components/ScheduledQueryDialog';
import UserDisplay from 'src/components/UserDisplay';
import { ActionConfigFieldDef, SeizuConfig } from 'src/config.context';
import {
  ScheduledQueryItem,
  WorkflowRunActivity,
  WorkflowRunDetail,
  WorkflowRunSummary,
  useScheduledQuery,
  useScheduledQueriesMutations,
  useScheduledQueryWorkflowRuns,
  useWorkflowRunDetail,
} from 'src/hooks/useScheduledQueriesApi';
import { usePermissions } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { describeSchedule } from 'src/scheduleSpec';
import { pageContentSx } from 'src/theme/layout';

function triggerLabel(item: ScheduledQueryItem): string {
  if (item.watch_scans.length > 0)
    return `Watch scans (${item.watch_scans.length})`;
  if (item.schedule) return describeSchedule(item.schedule);
  if (item.frequency != null) return `Every ${item.frequency} min`;
  return 'Not configured';
}

function DetailItem({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <Box sx={{ mb: 1.5 }}>
      <Typography
        variant="caption"
        sx={{ color: 'text.secondary', display: 'block' }}
      >
        {label}
      </Typography>
      <Typography component="div" variant="body2">
        {children}
      </Typography>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Temporal workflow runs
// ---------------------------------------------------------------------------

function runStatusColor(
  status: string,
): 'success' | 'info' | 'warning' | 'error' | 'default' {
  switch (status) {
    case 'completed':
      return 'success';
    case 'running':
    case 'scheduled':
      return 'info';
    case 'canceled':
    case 'cancel_requested':
    case 'paused':
      return 'warning';
    case 'failed':
    case 'timed_out':
    case 'terminated':
      return 'error';
    default:
      return 'default';
  }
}

// Activity input/result previews are JSON-encoded by the backend. Pretty-print
// when parseable (truncated previews and the undecodable sentinel are shown
// as-is) and syntax-highlight either way.
function JsonDetailPre({ label, value }: { label: string; value: string }) {
  const muiTheme = useTheme();
  let formatted = value;
  try {
    formatted = JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    // Not valid JSON — keep the raw text.
  }
  return (
    <Box sx={{ mb: 0.5 }}>
      <Typography variant="caption" sx={{ color: 'text.secondary' }}>
        {label}
      </Typography>
      <Highlight
        code={formatted}
        language="json"
        theme={
          muiTheme.palette.mode === 'dark'
            ? themes.nightOwl
            : themes.nightOwlLight
        }
      >
        {({ style, tokens, getLineProps, getTokenProps }) => (
          <Box
            component="pre"
            style={style}
            sx={{
              borderRadius: 1,
              fontFamily: 'monospace',
              fontSize: 12,
              m: 0,
              maxHeight: 240,
              overflow: 'auto',
              p: 1,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {tokens.map((line, lineIndex) => (
              // eslint-disable-next-line @eslint-react/no-array-index-key -- lines have no identity beyond position
              <div key={lineIndex} {...getLineProps({ line })}>
                {line.map((token, tokenIndex) => (
                  // eslint-disable-next-line @eslint-react/no-array-index-key -- tokens have no identity beyond position
                  <span key={tokenIndex} {...getTokenProps({ token })} />
                ))}
              </div>
            ))}
          </Box>
        )}
      </Highlight>
    </Box>
  );
}

function ActivityAccordion({ activity }: { activity: WorkflowRunActivity }) {
  const times: [string, string | null][] = [
    ['Scheduled', activity.scheduled_at],
    ['Started', activity.started_at],
    ['Closed', activity.closed_at],
  ];
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
          minHeight: 30,
          px: 1,
          py: 0,
          '& .MuiAccordionSummary-content': {
            alignItems: 'center',
            gap: 0.75,
            my: 0.5,
          },
        }}
      >
        <Typography
          variant="caption"
          sx={{ fontWeight: 600, minWidth: 0, wordBreak: 'break-word' }}
        >
          {activity.activity_type}
        </Typography>
        <Typography
          variant="caption"
          sx={{ color: 'text.secondary', flexShrink: 0 }}
        >
          #{activity.activity_id}
        </Typography>
        <Box
          sx={{
            alignItems: 'center',
            display: 'flex',
            gap: 0.5,
            ml: 'auto',
          }}
        >
          {activity.attempts > 1 && (
            <Chip
              label={`${activity.attempts} attempts`}
              size="small"
              variant="outlined"
              color="warning"
              sx={{ height: 18, fontSize: 11 }}
            />
          )}
          <Chip
            label={activity.status}
            size="small"
            variant="outlined"
            color={runStatusColor(activity.status)}
            sx={{ height: 18, fontSize: 11 }}
          />
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ px: 1, pt: 0, pb: 1 }}>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, mb: 0.5 }}>
          {times.map(([label, value]) =>
            value ? (
              <Box key={label}>
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  {label}
                </Typography>
                <Typography variant="body2">
                  {new Date(value).toLocaleString()}
                </Typography>
              </Box>
            ) : null,
          )}
          {activity.maximum_attempts ? (
            <Box>
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Attempts
              </Typography>
              <Typography variant="body2">
                {activity.attempts} of {activity.maximum_attempts}
              </Typography>
            </Box>
          ) : null}
          {activity.retry_state ? (
            <Box>
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Retry state
              </Typography>
              <Typography variant="body2">{activity.retry_state}</Typography>
            </Box>
          ) : null}
        </Box>
        {activity.failure ? (
          <RunDetailPre label="Failure" value={activity.failure} />
        ) : null}
        {activity.last_attempt_failure ? (
          <RunDetailPre
            label="Previous attempt failure"
            value={activity.last_attempt_failure}
          />
        ) : null}
        {activity.input_preview ? (
          <JsonDetailPre label="Input" value={activity.input_preview} />
        ) : null}
        {activity.result_preview ? (
          <JsonDetailPre label="Result" value={activity.result_preview} />
        ) : null}
      </AccordionDetails>
    </Accordion>
  );
}

function WorkflowRunAccordion({
  sqId,
  run,
}: {
  sqId: string;
  run: WorkflowRunSummary;
}) {
  const fetchDetail = useWorkflowRunDetail();
  const [detail, setDetail] = useState<WorkflowRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleExpand = (_event: unknown, expanded: boolean) => {
    if (!expanded || detail !== null) return;
    fetchDetail(sqId, run.workflow_id, run.run_id)
      .then(setDetail)
      .catch(() => setError('Failed to load this run.'));
  };

  return (
    <Accordion disableGutters onChange={handleExpand}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Box
          sx={{
            alignItems: 'center',
            display: 'flex',
            gap: 1,
            minWidth: 0,
            width: '100%',
          }}
        >
          <AccountTreeIcon fontSize="small" sx={{ color: 'text.secondary' }} />
          <Typography variant="body2" noWrap>
            {run.workflow_name}
          </Typography>
          {run.start_time && (
            <Typography
              variant="caption"
              sx={{ color: 'text.secondary', flexShrink: 0 }}
            >
              {new Date(run.start_time).toLocaleString()}
            </Typography>
          )}
          <Chip
            label={run.status}
            size="small"
            variant="outlined"
            color={runStatusColor(run.status)}
            sx={{ ml: 'auto', mr: 1, flexShrink: 0 }}
          />
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        {error ? (
          <Alert severity="error">{error}</Alert>
        ) : detail === null ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
            <ConstellationSpinner size={24} />
          </Box>
        ) : (
          <>
            {detail.failure ? (
              <Alert severity="error" sx={{ mb: 1 }}>
                {detail.failure}
              </Alert>
            ) : null}
            {detail.activities.length === 0 ? (
              <Typography color="text.secondary" variant="body2">
                No activities recorded for this run.
              </Typography>
            ) : (
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
                {detail.activities.map((activity) => (
                  <ActivityAccordion
                    key={activity.activity_id}
                    activity={activity}
                  />
                ))}
              </Box>
            )}
          </>
        )}
      </AccordionDetails>
    </Accordion>
  );
}

function WorkflowRunsSection({ sqId }: { sqId: string }) {
  const { runs, error } = useScheduledQueryWorkflowRuns(sqId, true);
  return (
    <Card variant="outlined" sx={{ mb: 2 }}>
      <CardContent>
        <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
          Workflow runs
        </Typography>
        {error ? (
          <Alert severity="error">
            Failed to load workflow runs. Temporal may be unavailable.
          </Alert>
        ) : runs === null ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
            <ConstellationSpinner size={28} />
          </Box>
        ) : runs.length === 0 ? (
          <Typography color="text.secondary" variant="body2">
            This query has not started any workflow runs yet.
          </Typography>
        ) : (
          runs.map((run) => (
            <WorkflowRunAccordion key={run.run_id} sqId={sqId} run={run} />
          ))
        )}
      </CardContent>
    </Card>
  );
}

function QueryPanels({ query }: { query: ScheduledQueryItem }) {
  return (
    <>
      <Card variant="outlined" sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
            Details
          </Typography>
          <Box sx={{ columnGap: 4, display: 'flex', flexWrap: 'wrap' }}>
            <DetailItem label="Status">
              <Chip
                label={query.enabled ? 'Enabled' : 'Disabled'}
                color={query.enabled ? 'success' : 'default'}
                size="small"
              />
            </DetailItem>
            <DetailItem label="Trigger">{triggerLabel(query)}</DetailItem>
            {query.watch_scans.length > 0 && (
              <DetailItem label="Watch scans">
                {query.watch_scans.map((ws, i) => (
                  <Box key={i} component="span" sx={{ display: 'block' }}>
                    {[ws.grouptype, ws.syncedtype, ws.groupid]
                      .map((v) => v || '.*')
                      .join(' / ')}
                  </Box>
                ))}
              </DetailItem>
            )}
            <DetailItem label="Version">v{query.current_version}</DetailItem>
            <DetailItem label="Owner">
              <UserDisplay userId={query.created_by} />
            </DetailItem>
            <DetailItem label="Last run">
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                <FiberManualRecordIcon
                  sx={{
                    fontSize: 12,
                    color:
                      query.last_run_status === 'success'
                        ? 'success.main'
                        : query.last_run_status === 'failure'
                          ? 'error.main'
                          : 'warning.main',
                  }}
                />
                <Typography variant="body2" component="span">
                  {query.last_run_status === 'success'
                    ? 'Success'
                    : query.last_run_status === 'failure'
                      ? 'Failed'
                      : 'No runs yet'}
                </Typography>
                {query.last_run_at && (
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    component="span"
                  >
                    {new Date(query.last_run_at).toLocaleString()}
                  </Typography>
                )}
              </Box>
            </DetailItem>
            <DetailItem label="Updated">
              {new Date(query.updated_at).toLocaleString()}
            </DetailItem>
          </Box>
          <Typography variant="subtitle2" sx={{ mb: 1.5, mt: 1 }}>
            Cypher
          </Typography>
          <Box
            component="pre"
            sx={{
              bgcolor: 'action.hover',
              borderRadius: 1,
              fontFamily: 'monospace',
              fontSize: 13,
              m: 0,
              maxHeight: 240,
              overflow: 'auto',
              p: 1.5,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {query.cypher}
          </Box>
          {query.params.length > 0 && (
            <>
              <Typography variant="subtitle2" sx={{ mb: 1.5, mt: 2 }}>
                Parameters
              </Typography>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
                {query.params.map((p, i) => (
                  <Box key={i} sx={{ display: 'flex', gap: 1 }}>
                    <Typography
                      variant="body2"
                      sx={{
                        fontFamily: 'monospace',
                        fontWeight: 600,
                        minWidth: 120,
                      }}
                    >
                      {p.name}
                    </Typography>
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ fontFamily: 'monospace' }}
                    >
                      {Array.isArray(p.value)
                        ? (p.value as unknown[]).join(', ')
                        : String(p.value ?? '')}
                      {Array.isArray(p.value) && (
                        <Typography
                          component="span"
                          variant="caption"
                          color="text.disabled"
                          sx={{ ml: 0.5 }}
                        >
                          (list)
                        </Typography>
                      )}
                    </Typography>
                  </Box>
                ))}
              </Box>
            </>
          )}
          {query.actions.length > 0 && (
            <>
              <Typography variant="subtitle2" sx={{ mb: 1.5, mt: 2 }}>
                Actions
              </Typography>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                {query.actions.map((a, i) => (
                  <Box
                    key={i}
                    sx={{
                      border: '1px solid',
                      borderColor: 'divider',
                      borderRadius: 1,
                      p: 1.5,
                    }}
                  >
                    <Typography
                      variant="body2"
                      sx={{ fontWeight: 600, mb: 0.5 }}
                    >
                      {a.action_type}
                    </Typography>
                    {Object.keys(a.action_config).length > 0 && (
                      <Box
                        sx={{
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 0.25,
                        }}
                      >
                        {Object.entries(a.action_config).map(([k, v]) => (
                          <Box key={k} sx={{ display: 'flex', gap: 1 }}>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ fontFamily: 'monospace', minWidth: 140 }}
                            >
                              {k}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ fontFamily: 'monospace' }}
                            >
                              {Array.isArray(v)
                                ? (v as unknown[]).join(', ')
                                : String(v ?? '')}
                            </Typography>
                          </Box>
                        ))}
                      </Box>
                    )}
                  </Box>
                ))}
              </Box>
            </>
          )}
        </CardContent>
      </Card>
      {query.last_errors.length > 0 && (
        <Card variant="outlined" sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
              Recent errors
            </Typography>
            {query.last_errors.map((entry, i) => (
              <Alert key={i} severity="error" sx={{ mb: 1 }}>
                <Typography variant="caption" sx={{ display: 'block' }}>
                  {new Date(entry.timestamp).toLocaleString()}
                </Typography>
                {entry.error}
              </Alert>
            ))}
          </CardContent>
        </Card>
      )}
    </>
  );
}

function ScheduledQueryView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const hasPermission = usePermissions();
  const { fromLabel } = (location.state ?? {}) as BackState;

  const { query, loading, error, refresh } = useScheduledQuery(id ?? null);
  const { updateScheduledQuery, deleteScheduledQuery, runScheduledQuery } =
    useScheduledQueriesMutations();

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [runMessage, setRunMessage] = useState<string | null>(null);
  const [actionConfig, setActionConfig] = useState<SeizuConfig>({
    scheduled_query_action_types: [],
    scheduled_query_action_schemas: {} as Record<
      string,
      ActionConfigFieldDef[]
    >,
    scheduled_query_action_dependent_schemas: {},
  });

  useEffect(() => {
    let cancelled = false;
    fetch('/api/v1/config')
      .then((res) => res.json())
      .then((config: SeizuConfig) => {
        if (!cancelled) {
          setActionConfig({
            scheduled_query_action_types:
              config.scheduled_query_action_types ?? [],
            scheduled_query_action_schemas:
              config.scheduled_query_action_schemas ?? {},
            scheduled_query_action_dependent_schemas:
              config.scheduled_query_action_dependent_schemas ?? {},
          });
        }
      })
      .catch(() => {
        /* action config is optional; dialog will allow freetext entry */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const canWrite = hasPermission('scheduled_queries:write');
  const canDelete = hasPermission('scheduled_queries:delete');

  const handleConfirmDelete = async () => {
    if (!id) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteScheduledQuery(id);
      navigate('/app/scheduled-queries');
    } catch {
      setDeleteError('Failed to delete scheduled query. Please try again.');
      setDeleting(false);
    }
  };

  const handleRunNow = async () => {
    if (!query) return;
    try {
      await runScheduledQuery(query.scheduled_query_id);
      setRunMessage(
        `Run requested for "${query.name}". The worker will pick it up on its next poll.`,
      );
    } catch {
      setRunMessage('Failed to request run. Please try again.');
    }
  };

  const menuActions: RowMenuAction[] = [
    {
      key: 'run',
      label: 'Run now',
      icon: <PlayArrowIcon fontSize="small" />,
      onClick: () => void handleRunNow(),
      disabled: !canWrite,
      tooltip: canWrite
        ? 'Runs on the worker’s next poll, even if disabled'
        : 'You do not have permission to run scheduled queries',
    },
    {
      key: 'history',
      label: 'View history',
      icon: <HistoryIcon fontSize="small" />,
      onClick: () =>
        navigate(`/app/scheduled-queries/${id}/history`, {
          state: {
            fromLabel: query?.name ?? 'Scheduled Query',
          } satisfies BackState,
        }),
    },
    {
      key: 'delete',
      label: 'Delete',
      icon: <DeleteIcon fontSize="small" />,
      onClick: () => {
        setDeleteError(null);
        setDeleteOpen(true);
      },
      disabled: !canDelete,
      tooltip: canDelete
        ? undefined
        : 'You do not have permission to delete scheduled queries',
      destructive: true,
      dividerBefore: true,
    },
  ];

  return (
    <>
      <Helmet>
        <title>
          {query ? `${query.name} | Seizu` : 'Scheduled Query | Seizu'}
        </title>
      </Helmet>
      <Box sx={pageContentSx}>
        {fromLabel && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <Button
              size="small"
              startIcon={<ArrowBackIcon />}
              onClick={() => navigate(-1)}
            >
              Back to {fromLabel}
            </Button>
          </Box>
        )}
        <ListPageHeader
          title={query?.name ?? 'Scheduled Query'}
          action={
            <Box sx={{ alignItems: 'center', display: 'flex', gap: 1 }}>
              <Button
                variant="contained"
                startIcon={<EditIcon />}
                onClick={() => setEditOpen(true)}
                disabled={!canWrite}
                title={
                  canWrite
                    ? undefined
                    : 'You do not have permission to edit scheduled queries'
                }
              >
                Edit
              </Button>
              <RowMenu actions={menuActions} />
            </Box>
          }
        />
        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load scheduled query"
        >
          {query ? (
            <>
              <QueryPanels query={query} />
              {query.actions.some((a) => a.action_type === 'temporal') && (
                <WorkflowRunsSection sqId={query.scheduled_query_id} />
              )}
            </>
          ) : (
            <Alert severity="warning">Scheduled query not found.</Alert>
          )}
        </ListViewState>
      </Box>

      {editOpen && query ? (
        <ScheduledQueryDialog
          key={query.scheduled_query_id}
          open={editOpen}
          initial={query}
          onClose={() => setEditOpen(false)}
          onSave={async (req) => {
            await updateScheduledQuery(query.scheduled_query_id, req);
            await refresh();
          }}
          actionTypes={actionConfig.scheduled_query_action_types}
          actionSchemas={actionConfig.scheduled_query_action_schemas}
          dependentSchemas={
            actionConfig.scheduled_query_action_dependent_schemas
          }
        />
      ) : null}

      <ConfirmDeleteDialog
        open={deleteOpen}
        title="Delete scheduled query?"
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => void handleConfirmDelete()}
        deleting={deleting}
        error={deleteError}
      >
        Permanently delete <strong>{query?.name}</strong> and all its versions?
        This cannot be undone.
      </ConfirmDeleteDialog>

      <Snackbar
        open={runMessage !== null}
        autoHideDuration={6000}
        onClose={() => setRunMessage(null)}
        message={runMessage}
      />
    </>
  );
}

export default ScheduledQueryView;
