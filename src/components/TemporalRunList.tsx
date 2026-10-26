import { useEffect, useEffectEvent, useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Collapse,
  Typography,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import { Highlight, themes } from 'prism-react-renderer';
import CancelIcon from '@mui/icons-material/Cancel';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import RunDetailPre from 'src/components/RunDetailPre';
import WorkflowStageLanes, {
  LaneTile,
  StatusMark,
  useStatusTone,
} from 'src/components/WorkflowStageLanes';
import {
  WorkflowRunActivity,
  WorkflowRunDetail,
  WorkflowRunSummary,
} from 'src/hooks/useWorkflowsApi';
import { temporalStatusLabel } from 'src/temporalStatus';
import { fontFamilyMono } from 'src/theme/typography';
import {
  ACTIVE_RUN_STATUSES,
  RunStep,
  formatDuration,
  groupRunActivities,
  internalActivityLabel,
  runTrigger,
} from 'src/workflowRunStages';

// Matches the run list's own refresh interval.
const DETAIL_POLL_MS = 5000;

const RUN_GRID = {
  display: 'grid',
  gridTemplateColumns: {
    xs: 'auto minmax(0, 1fr)',
    sm: '9.5rem minmax(0, 1fr) 6rem 9rem',
  },
  columnGap: 2,
  alignItems: 'center',
} as const;

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
              fontFamily: fontFamilyMono,
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

function Fact({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary" component="div">
        {label}
      </Typography>
      <Typography
        variant="body2"
        component="div"
        sx={{ wordBreak: 'break-word' }}
      >
        {children}
      </Typography>
    </Box>
  );
}

/** Timing, attempts, failures and payloads of one Temporal record. */
function ActivityRecord({ activity }: { activity: WorkflowRunActivity }) {
  const times: [string, string | null][] = [
    ['Scheduled', activity.scheduled_at],
    ['Started', activity.started_at],
    ['Closed', activity.closed_at],
  ];
  return (
    <>
      <Box
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          columnGap: 3,
          rowGap: 1,
          mb: 1,
        }}
      >
        {times.map(([label, value]) =>
          value ? (
            <Fact key={label} label={label}>
              {new Date(value).toLocaleString()}
            </Fact>
          ) : null,
        )}
        {activity.maximum_attempts ? (
          <Fact label="Attempts">
            {activity.attempts} of {activity.maximum_attempts}
          </Fact>
        ) : null}
        {activity.retry_state ? (
          <Fact label="Retry state">
            {temporalStatusLabel(activity.retry_state)}
          </Fact>
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
    </>
  );
}

function ActivityAccordion({
  activity,
  label,
}: {
  activity: WorkflowRunActivity;
  label: string;
}) {
  return (
    <Accordion
      disableGutters
      elevation={0}
      square
      slotProps={{ transition: { timeout: 0, unmountOnExit: true } }}
      sx={{
        bgcolor: 'transparent',
        borderBottom: 1,
        borderColor: 'divider',
        '&:before': { display: 'none' },
        '&:last-of-type': { borderBottom: 0 },
      }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreIcon fontSize="small" />}
        sx={{
          minHeight: 36,
          px: 1,
          '& .MuiAccordionSummary-content': {
            alignItems: 'center',
            columnGap: 1,
            flexWrap: { xs: 'wrap', sm: 'nowrap' },
            my: 0.5,
            minWidth: 0,
          },
        }}
      >
        <Typography variant="body2" sx={{ fontWeight: 600, flexShrink: 0 }}>
          {label}
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{
            flexBasis: { xs: '100%', sm: 'auto' },
            fontFamily: fontFamilyMono,
            minWidth: 0,
            order: { xs: 3, sm: 0 },
            wordBreak: 'break-all',
          }}
        >
          {activity.activity_type}
          {' · '}
          {activity.activity_id}
        </Typography>
        <Box
          sx={{
            ml: 'auto',
            pl: 1,
            display: 'flex',
            gap: 1,
            alignItems: 'center',
          }}
        >
          {activity.attempts > 1 && (
            <Typography variant="caption" color="warning.main">
              {activity.attempts} attempts
            </Typography>
          )}
          <StatusMark status={activity.status} />
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ px: 1, pt: 0, pb: 1.5 }}>
        <ActivityRecord activity={activity} />
      </AccordionDetails>
    </Accordion>
  );
}

function StepTile({
  step,
  selected,
  onSelect,
}: {
  step: RunStep;
  selected: boolean;
  onSelect: () => void;
}) {
  const tone = useStatusTone(step.status);
  const duration = formatDuration(step.startedAt, step.closedAt);
  return (
    <LaneTile selected={selected} onClick={onSelect} tone={tone}>
      <Box
        sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}
      >
        <StatusMark status={step.status} />
        {duration && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ ml: 'auto', fontVariantNumeric: 'tabular-nums' }}
          >
            {duration}
          </Typography>
        )}
      </Box>
      <Typography
        variant="body2"
        sx={{ fontWeight: 600, wordBreak: 'break-word' }}
      >
        {step.kind}
      </Typography>
      <Box
        sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}
      >
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{
            fontFamily: fontFamilyMono,
            minWidth: 0,
            wordBreak: 'break-all',
          }}
        >
          → {step.output}
        </Typography>
        {step.attempts > 1 && (
          <Typography
            variant="caption"
            color="warning.main"
            sx={{ ml: 'auto', flexShrink: 0 }}
          >
            {step.attempts} attempts
          </Typography>
        )}
      </Box>
    </LaneTile>
  );
}

function StepDetail({ step }: { step: RunStep }) {
  return (
    <Box
      sx={{
        border: 1,
        borderColor: 'divider',
        borderRadius: 1,
        mt: 2,
        p: 2,
      }}
    >
      <Box
        sx={{
          display: 'flex',
          alignItems: 'baseline',
          gap: 1.5,
          mb: 1.5,
          flexWrap: 'wrap',
        }}
      >
        <Typography component="h4" variant="h5">
          {step.kind}
        </Typography>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontFamily: fontFamilyMono }}
        >
          → {step.output}
        </Typography>
        <Box sx={{ ml: 'auto' }}>
          <StatusMark status={step.status} />
        </Box>
      </Box>
      {step.parts.length === 1 ? (
        <ActivityRecord activity={step.parts[0]} />
      ) : (
        step.parts.map((part) => (
          <Box
            key={part.activity_id}
            sx={{
              '& + &': { mt: 2, pt: 2, borderTop: 1, borderColor: 'divider' },
            }}
          >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {part.activity_type}
              </Typography>
              <StatusMark status={part.status} />
            </Box>
            <ActivityRecord activity={part} />
          </Box>
        ))
      )}
    </Box>
  );
}

function InternalActivities({
  activities,
  openByDefault,
}: {
  activities: WorkflowRunActivity[];
  openByDefault: boolean;
}) {
  const [open, setOpen] = useState(openByDefault);
  if (activities.length === 0) return null;
  return (
    <Box sx={{ mt: 2.5 }}>
      <Button
        size="small"
        color="inherit"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        endIcon={
          <ExpandMoreIcon
            sx={{
              transform: open ? 'rotate(180deg)' : 'none',
              transition: 'transform 150ms',
            }}
          />
        }
        sx={{ color: 'text.secondary', px: 1, textTransform: 'none' }}
      >
        Engine activities ({activities.length})
      </Button>
      <Collapse in={open} unmountOnExit>
        <Typography
          variant="caption"
          color="text.secondary"
          component="p"
          sx={{ px: 1, mb: 1 }}
        >
          Temporal bookkeeping around the stages: loading the definition, the
          per-workflow lock, and recording the result. Useful for debugging.
        </Typography>
        <Box sx={{ border: 1, borderColor: 'divider', borderRadius: 1 }}>
          {activities.map((activity) => (
            <ActivityAccordion
              key={activity.activity_id}
              activity={activity}
              label={internalActivityLabel(activity)}
            />
          ))}
        </Box>
      </Collapse>
    </Box>
  );
}

function RunBody({
  run,
  detail,
}: {
  run: WorkflowRunSummary;
  detail: WorkflowRunDetail;
}) {
  const { stages, internal } = groupRunActivities(detail.activities);
  const allSteps = stages.flatMap((stage) => stage.steps);
  const failing = allSteps.find((step) =>
    ['failed', 'failure', 'timed_out', 'terminated'].includes(step.status),
  );
  // Selection keyed per run; a failing step is what someone opened the run for.
  const [selectedKey, setSelectedKey] = useState<string | null>(
    failing?.key ?? null,
  );
  const selected = allSteps.find((step) => step.key === selectedKey) ?? null;
  // Open, but its history holds nothing a worker has done: the run is
  // queued behind a missing or overloaded worker, not behind this workflow.
  const unclaimed =
    detail.activities.length === 0 && ACTIVE_RUN_STATUSES.has(detail.status);
  const waitingOnLock = internal.some(
    (activity) =>
      activity.activity_type === 'seizu_configured_workflow_execution' &&
      activity.status === 'waiting',
  );

  return (
    <>
      {detail.failure ? (
        <Alert severity="error" sx={{ mb: 2 }}>
          {detail.failure}
        </Alert>
      ) : null}
      {stages.length === 0 ? (
        <Typography color="text.secondary" variant="body2">
          {waitingOnLock
            ? run.status === 'skipped'
              ? 'Skipped: another run of this workflow was already queued.'
              : 'Waiting for an earlier run of this workflow to finish.'
            : unclaimed
              ? 'No worker has picked up this run yet. If runs stay here, check that the Temporal worker is running and keeping up.'
              : 'No stage has started in this run.'}
        </Typography>
      ) : (
        <WorkflowStageLanes
          label="Stages in this run"
          lanes={stages.map((stage) => ({
            key: String(stage.position),
            title: `Stage ${stage.position}`,
            meta:
              stage.steps.length > 1
                ? `${stage.steps.length} in parallel`
                : undefined,
            children: stage.steps.map((step) => (
              <StepTile
                key={step.key}
                step={step}
                selected={step.key === selectedKey}
                onSelect={() =>
                  setSelectedKey((current) =>
                    current === step.key ? null : step.key,
                  )
                }
              />
            )),
          }))}
        />
      )}
      {selected && <StepDetail step={selected} />}
      <InternalActivities
        activities={internal}
        openByDefault={stages.length === 0}
      />
    </>
  );
}

function RunRow({
  run,
  loadDetail,
  cancelRun,
}: {
  run: WorkflowRunSummary;
  loadDetail: (run: WorkflowRunSummary) => Promise<WorkflowRunDetail>;
  cancelRun?: (run: WorkflowRunSummary) => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<WorkflowRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [canceling, setCanceling] = useState(false);
  // Optimistic status after a cancel request, held only until the polled run
  // reports something new. Carrying the status it was requested against is
  // what retires it, rather than an effect overwriting it a render later.
  const [cancelRequestedFor, setCancelRequestedFor] = useState<string | null>(
    null,
  );
  const displayStatus =
    cancelRequestedFor === run.status ? 'cancel_requested' : run.status;
  const trigger = runTrigger(run.workflow_id);
  const duration = formatDuration(run.start_time, run.close_time);

  const fetchDetail = useEffectEvent(() => loadDetail(run));

  // Detail is read only while the row is open, and re-read every
  // DETAIL_POLL_MS until the run reaches a final status. Collapsing the row
  // stops the poll; reopening it reads the run afresh.
  useEffect(() => {
    if (!expanded) return undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = () => {
      fetchDetail()
        .then((next) => {
          if (cancelled) return;
          setDetail(next);
          setError(null);
          if (ACTIVE_RUN_STATUSES.has(next.status))
            timer = setTimeout(poll, DETAIL_POLL_MS);
        })
        .catch(() => {
          if (!cancelled) setError('Failed to load this run.');
        });
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [expanded, run.workflow_id, run.run_id]);

  return (
    <Accordion
      disableGutters
      elevation={0}
      square
      expanded={expanded}
      onChange={(_event, open) => setExpanded(open)}
      slotProps={{ transition: { unmountOnExit: true } }}
      sx={{
        bgcolor: 'transparent',
        borderTop: 1,
        borderColor: 'divider',
        '&:before': { display: 'none' },
        '&.Mui-expanded': { bgcolor: 'action.hover' },
      }}
    >
      <AccordionSummary
        expandIcon={<ExpandMoreIcon fontSize="small" />}
        sx={{
          px: 1.5,
          minHeight: 48,
          '& .MuiAccordionSummary-content': { my: 1, minWidth: 0 },
        }}
      >
        <Box sx={{ ...RUN_GRID, width: '100%' }}>
          <StatusMark status={displayStatus} />
          <Typography
            variant="body2"
            sx={{
              fontVariantNumeric: 'tabular-nums',
              textAlign: { xs: 'right', sm: 'left' },
            }}
          >
            {run.start_time ? new Date(run.start_time).toLocaleString() : '—'}
          </Typography>
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{
              fontVariantNumeric: 'tabular-nums',
              display: { xs: 'none', sm: 'block' },
            }}
          >
            {duration ?? '—'}
          </Typography>
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ display: { xs: 'none', sm: 'block' } }}
            noWrap
          >
            {trigger ?? run.workflow_name}
          </Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ px: 1.5, pt: 0.5, pb: 2.5 }}>
        <Box
          sx={{
            alignItems: 'flex-end',
            display: 'flex',
            flexWrap: 'wrap',
            columnGap: 3,
            rowGap: 1,
            mb: 2.5,
          }}
        >
          <Fact label="Started">
            {run.start_time ? new Date(run.start_time).toLocaleString() : '—'}
          </Fact>
          {run.close_time && (
            <Fact label="Closed">
              {new Date(run.close_time).toLocaleString()}
            </Fact>
          )}
          {trigger && <Fact label="Trigger">{trigger}</Fact>}
          <Fact label="Run ID">
            <Box
              component="span"
              sx={{ fontFamily: fontFamilyMono, fontSize: 12 }}
            >
              {run.run_id}
            </Box>
          </Fact>
          {run.history_length !== null && (
            <Fact label="History events">{run.history_length}</Fact>
          )}
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
                  .then(() => setCancelRequestedFor(run.status))
                  .catch(() => setError('Failed to cancel this waiting run.'))
                  .finally(() => setCanceling(false));
              }}
              sx={{ ml: 'auto' }}
            >
              {canceling ? 'Canceling…' : 'Cancel waiting run'}
            </Button>
          ) : null}
        </Box>
        {error && (
          <Alert severity="error" sx={{ mb: detail ? 2 : 0 }}>
            {error}
          </Alert>
        )}
        {detail !== null ? (
          <RunBody run={run} detail={detail} />
        ) : error ? null : (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
            <ConstellationSpinner size={24} />
          </Box>
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
  emptyMessage = 'No runs yet.',
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
        Failed to load runs. Temporal may be unavailable.
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
    <Box
      sx={{
        border: 1,
        borderColor: 'divider',
        borderRadius: 1,
        overflow: 'hidden',
      }}
    >
      <Box
        aria-hidden="true"
        sx={{
          ...RUN_GRID,
          display: { xs: 'none', sm: 'grid' },
          // Matches the summary's padding plus the expand icon's column.
          pl: 1.5,
          pr: 4,
          py: 1,
        }}
      >
        {['Status', 'Started', 'Duration', 'Trigger'].map((heading) => (
          <Typography
            key={heading}
            variant="caption"
            color="text.secondary"
            sx={{ fontWeight: 600 }}
          >
            {heading}
          </Typography>
        ))}
      </Box>
      {runs.map((run) => (
        <RunRow
          key={run.run_id}
          run={run}
          loadDetail={loadDetail}
          cancelRun={cancelRun}
        />
      ))}
    </Box>
  );
}
