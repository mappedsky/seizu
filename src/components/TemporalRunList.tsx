import { useEffect, useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Chip,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { Highlight, themes } from 'prism-react-renderer';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import CancelIcon from '@mui/icons-material/Cancel';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import RunDetailPre from 'src/components/RunDetailPre';
import {
  WorkflowRunActivity,
  WorkflowRunDetail,
  WorkflowRunSummary,
} from 'src/hooks/useWorkflowsApi';
import { temporalStatusColor, temporalStatusLabel } from 'src/temporalStatus';

function JsonDetailPre({ label, value }: { label: string; value: string }) {
  const muiTheme = useTheme();
  let formatted = value;
  try {
    formatted = JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    // Truncated or non-JSON Temporal previews are shown verbatim.
  }
  return (
    <Box sx={{ mb: 0.5 }}>
      <Typography variant="caption" color="text.secondary">
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
              // eslint-disable-next-line @eslint-react/no-array-index-key -- highlighted lines are positional
              <div key={lineIndex} {...getLineProps({ line })}>
                {line.map((token, tokenIndex) => (
                  // eslint-disable-next-line @eslint-react/no-array-index-key -- highlighted tokens are positional
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
          minHeight: 34,
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
          color="text.secondary"
          sx={{ minWidth: 0, wordBreak: 'break-all' }}
        >
          {activity.activity_id}
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
              sx={{ height: 20, fontSize: 11 }}
            />
          )}
          <Chip
            label={temporalStatusLabel(activity.status)}
            size="small"
            variant="outlined"
            color={temporalStatusColor(activity.status)}
            sx={{ height: 20, fontSize: 11 }}
          />
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ px: 1, pt: 0.5, pb: 1 }}>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, mb: 1 }}>
          {times.map(([label, value]) =>
            value ? (
              <Box key={label}>
                <Typography variant="caption" color="text.secondary">
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
              <Typography variant="caption" color="text.secondary">
                Attempts
              </Typography>
              <Typography variant="body2">
                {activity.attempts} of {activity.maximum_attempts}
              </Typography>
            </Box>
          ) : null}
          {activity.retry_state ? (
            <Box>
              <Typography variant="caption" color="text.secondary">
                Retry state
              </Typography>
              <Typography variant="body2">
                {temporalStatusLabel(activity.retry_state)}
              </Typography>
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

function RunAccordion({
  run,
  loadDetail,
  cancelRun,
}: {
  run: WorkflowRunSummary;
  loadDetail: (run: WorkflowRunSummary) => Promise<WorkflowRunDetail>;
  cancelRun?: (run: WorkflowRunSummary) => Promise<void>;
}) {
  const [detail, setDetail] = useState<WorkflowRunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [canceling, setCanceling] = useState(false);
  const [displayStatus, setDisplayStatus] = useState(run.status);

  useEffect(() => {
    setDisplayStatus(run.status);
  }, [run.status]);

  const handleExpand = (_event: unknown, expanded: boolean) => {
    if (!expanded || detail !== null || loading) return;
    setLoading(true);
    setError(null);
    loadDetail(run)
      .then(setDetail)
      .catch(() => setError('Failed to load this run.'))
      .finally(() => setLoading(false));
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
          <AccountTreeIcon fontSize="small" color="action" />
          <Box sx={{ minWidth: 0 }}>
            <Typography variant="body2" noWrap sx={{ fontWeight: 600 }}>
              {run.workflow_name}
            </Typography>
            {run.start_time && (
              <Typography variant="caption" color="text.secondary">
                {new Date(run.start_time).toLocaleString()}
              </Typography>
            )}
          </Box>
          <Chip
            label={temporalStatusLabel(displayStatus)}
            size="small"
            variant="outlined"
            color={temporalStatusColor(displayStatus)}
            sx={{ ml: 'auto', mr: 1, flexShrink: 0 }}
          />
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        {displayStatus === 'waiting' && cancelRun ? (
          <Button
            color="warning"
            disabled={canceling}
            size="small"
            startIcon={
              canceling ? <ConstellationSpinner size={18} /> : <CancelIcon />
            }
            variant="outlined"
            onClick={() => {
              setCanceling(true);
              setError(null);
              cancelRun(run)
                .then(() => setDisplayStatus('cancel_requested'))
                .catch(() => setError('Failed to cancel this waiting run.'))
                .finally(() => setCanceling(false));
            }}
            sx={{ mb: 1.5 }}
          >
            {canceling ? 'Canceling…' : 'Cancel waiting run'}
          </Button>
        ) : null}
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 3, mb: 1.5 }}>
          <Box>
            <Typography variant="caption" color="text.secondary">
              Run ID
            </Typography>
            <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>
              {run.run_id}
            </Typography>
          </Box>
          {run.close_time && (
            <Box>
              <Typography variant="caption" color="text.secondary">
                Closed
              </Typography>
              <Typography variant="body2">
                {new Date(run.close_time).toLocaleString()}
              </Typography>
            </Box>
          )}
          {run.history_length !== null && (
            <Box>
              <Typography variant="caption" color="text.secondary">
                History events
              </Typography>
              <Typography variant="body2">{run.history_length}</Typography>
            </Box>
          )}
        </Box>
        {error ? (
          <Alert severity="error">{error}</Alert>
        ) : loading || detail === null ? (
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
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Activities ({detail.activities.length})
            </Typography>
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

export default function TemporalRunList({
  runs,
  error,
  loadDetail,
  cancelRun,
  emptyMessage = 'No Temporal runs are visible yet.',
}: {
  runs: WorkflowRunSummary[] | null;
  error: Error | null;
  loadDetail: (run: WorkflowRunSummary) => Promise<WorkflowRunDetail>;
  cancelRun?: (run: WorkflowRunSummary) => Promise<void>;
  emptyMessage?: string;
}) {
  if (error)
    return (
      <Alert severity="error">
        Failed to load Temporal runs. Temporal may be unavailable.
      </Alert>
    );
  if (runs === null)
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
        <ConstellationSpinner size={28} />
      </Box>
    );
  if (runs.length === 0)
    return (
      <Typography color="text.secondary" variant="body2">
        {emptyMessage}
      </Typography>
    );
  return (
    <Box>
      {runs.map((run) => (
        <RunAccordion
          key={run.run_id}
          run={run}
          loadDetail={loadDetail}
          cancelRun={cancelRun}
        />
      ))}
    </Box>
  );
}
