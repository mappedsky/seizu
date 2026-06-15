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
  Grid,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ForumIcon from '@mui/icons-material/Forum';
import HistoryIcon from '@mui/icons-material/History';
import PersonIcon from '@mui/icons-material/Person';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ListPageHeader from 'src/components/ListPageHeader';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import ScheduledChatDialog, {
  describeSchedule,
} from 'src/components/ScheduledChatDialog';
import UserDisplay from 'src/components/UserDisplay';
import { MarkdocRenderer } from 'src/components/markdoc/renderer';
import {
  ScheduledChat,
  ScheduledChatRunDetail,
  ScheduledChatSession,
  ScheduledChatTranscriptMessage,
  useChatSchedule,
  useChatSchedules,
  useScheduledChatSessionHistory,
  useScheduledChatSessions,
} from 'src/hooks/useChatSchedules';
import { useCurrentUserState } from 'src/hooks/useCurrentUser';
import { usePermissions } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

// ---------------------------------------------------------------------------
// Run transcript rendering
// ---------------------------------------------------------------------------

function RunDetailRow({ detail }: { detail: ScheduledChatRunDetail }) {
  const hasContent = Boolean(detail.arguments || detail.body);
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
        expandIcon={hasContent ? <ExpandMoreIcon fontSize="small" /> : null}
        sx={{
          minHeight: 30,
          px: 1,
          py: 0,
          cursor: hasContent ? 'pointer' : 'default',
          '& .MuiAccordionSummary-content': {
            alignItems: 'center',
            gap: 0.75,
            my: 0.5,
          },
        }}
      >
        <Typography
          variant="caption"
          sx={{ color: 'text.secondary', flexShrink: 0 }}
        >
          {detail.kind}
        </Typography>
        <Typography
          variant="caption"
          sx={{ fontWeight: 600, minWidth: 0, wordBreak: 'break-word' }}
        >
          {detail.title}
        </Typography>
        {detail.status ? (
          <Chip
            label={detail.status}
            size="small"
            variant="outlined"
            color={
              detail.status === 'completed'
                ? 'success'
                : detail.status === 'blocked' || detail.status === 'failed'
                  ? 'error'
                  : 'default'
            }
            sx={{ ml: 'auto', height: 18, fontSize: 11 }}
          />
        ) : null}
      </AccordionSummary>
      {hasContent ? (
        <AccordionDetails sx={{ px: 1, pt: 0, pb: 1 }}>
          {detail.arguments ? (
            <RunDetailPre label="Arguments" value={detail.arguments} />
          ) : null}
          {detail.body ? (
            <RunDetailPre label="Output" value={detail.body} />
          ) : null}
        </AccordionDetails>
      ) : null}
    </Accordion>
  );
}

function RunDetailPre({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ mb: 0.5 }}>
      <Typography variant="caption" sx={{ color: 'text.secondary' }}>
        {label}
      </Typography>
      <Box
        component="pre"
        sx={{
          bgcolor: 'action.hover',
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
        {value}
      </Box>
    </Box>
  );
}

function RunMessage({ message }: { message: ScheduledChatTranscriptMessage }) {
  const details = message.metadata?.details ?? [];
  return (
    <Box sx={{ mb: 2 }}>
      <Box
        sx={{
          alignItems: 'center',
          color: 'text.secondary',
          display: 'flex',
          gap: 0.75,
          mb: 0.5,
        }}
      >
        {message.role === 'user' ? (
          <PersonIcon fontSize="small" />
        ) : (
          <SmartToyIcon fontSize="small" />
        )}
        <Typography variant="caption">
          {message.role === 'user' ? 'Prompt' : 'Assistant'}
        </Typography>
      </Box>
      {message.role === 'assistant' && details.length > 0 ? (
        <Accordion
          disableGutters
          elevation={0}
          square
          slotProps={{ transition: { timeout: 0, unmountOnExit: true } }}
          sx={{
            border: 1,
            borderColor: 'divider',
            borderRadius: 1,
            mb: 1,
            '&:before': { display: 'none' },
          }}
        >
          <AccordionSummary
            expandIcon={<ExpandMoreIcon fontSize="small" />}
            sx={{ minHeight: 34, px: 1 }}
          >
            <Typography variant="caption" sx={{ fontWeight: 600 }}>
              Details ({details.length})
            </Typography>
          </AccordionSummary>
          <AccordionDetails
            sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, p: 1 }}
          >
            {details.map((detail, index) => (
              <RunDetailRow key={index} detail={detail} />
            ))}
          </AccordionDetails>
        </Accordion>
      ) : null}
      <Box
        sx={{
          bgcolor: 'action.hover',
          borderRadius: 1,
          px: 1.5,
          py: 1,
        }}
      >
        <MarkdocRenderer source={message.text} untrustedUrls />
      </Box>
    </Box>
  );
}

function RunAccordion({
  scheduleId,
  session,
}: {
  scheduleId: string;
  session: ScheduledChatSession;
}) {
  const fetchHistory = useScheduledChatSessionHistory();
  const [transcript, setTranscript] = useState<
    ScheduledChatTranscriptMessage[] | null
  >(null);
  const [error, setError] = useState<string | null>(null);

  const handleExpand = (_event: unknown, expanded: boolean) => {
    if (!expanded || transcript !== null) return;
    fetchHistory(scheduleId, session.thread_id)
      .then(setTranscript)
      .catch(() => setError('Failed to load this run.'));
  };
  const status = session.run_status ?? 'unknown';
  const statusColor =
    status === 'completed' || status === 'success'
      ? 'success'
      : status === 'running'
        ? 'info'
        : status === 'partial' || status === 'budget_exhausted'
          ? 'warning'
          : 'error';

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
          <ForumIcon fontSize="small" sx={{ color: 'text.secondary' }} />
          <Typography variant="body2" noWrap>
            {session.title}
          </Typography>
          <Typography
            variant="caption"
            sx={{ color: 'text.secondary', flexShrink: 0 }}
          >
            {new Date(session.created_at).toLocaleString()}
          </Typography>
          <Chip
            label={status}
            size="small"
            variant="outlined"
            color={statusColor}
            sx={{ ml: 'auto', mr: 1, flexShrink: 0 }}
          />
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        {(session.run_errors ?? []).map((runError) => (
          <Alert key={runError} severity="error" sx={{ mb: 1 }}>
            {runError}
          </Alert>
        ))}
        {error ? (
          <Alert severity="error">{error}</Alert>
        ) : transcript === null ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
            <ConstellationSpinner size={24} />
          </Box>
        ) : transcript.length === 0 ? (
          <Typography color="text.secondary" variant="body2">
            No messages recorded for this run.
          </Typography>
        ) : (
          transcript.map((message) => (
            <RunMessage key={message.id} message={message} />
          ))
        )}
      </AccordionDetails>
    </Accordion>
  );
}

// ---------------------------------------------------------------------------
// Detail panels
// ---------------------------------------------------------------------------

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

function SchedulePanels({ schedule }: { schedule: ScheduledChat }) {
  return (
    <>
      <Card variant="outlined" sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
            Details
          </Typography>
          <DetailItem label="Status">
            {schedule.enabled ? (
              schedule.last_run_status ? (
                <Chip
                  label={schedule.last_run_status}
                  size="small"
                  variant="outlined"
                  color={
                    schedule.last_run_status === 'success' ? 'success' : 'error'
                  }
                />
              ) : (
                <Chip label="pending" size="small" variant="outlined" />
              )
            ) : (
              <Chip label="disabled" size="small" variant="outlined" />
            )}
          </DetailItem>
          <DetailItem label="Trigger">
            {schedule.schedule
              ? describeSchedule(schedule.schedule)
              : 'On scan updates'}
          </DetailItem>
          {schedule.watch_scans.length > 0 ? (
            <DetailItem label="Watch scans">
              {schedule.watch_scans.map((scan, index) => (
                <Box key={index} component="span" sx={{ display: 'block' }}>
                  {[scan.grouptype, scan.syncedtype, scan.groupid]
                    .map((value) => value || '.*')
                    .join(' / ')}
                </Box>
              ))}
            </DetailItem>
          ) : null}
          <DetailItem label="Owner">
            <UserDisplay userId={schedule.created_by} />
          </DetailItem>
          <DetailItem label="Version">v{schedule.current_version}</DetailItem>
          <DetailItem label="Last run">
            {schedule.last_run_at
              ? new Date(schedule.last_run_at).toLocaleString()
              : '—'}
          </DetailItem>
          <DetailItem label="Updated">
            {new Date(schedule.updated_at).toLocaleString()}
          </DetailItem>
        </CardContent>
      </Card>
      {schedule.last_errors.length > 0 ? (
        <Card variant="outlined" sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
              Recent errors
            </Typography>
            {schedule.last_errors.map((entry, index) => (
              <Alert key={index} severity="error" sx={{ mb: 1 }}>
                <Typography variant="caption" sx={{ display: 'block' }}>
                  {new Date(entry.timestamp).toLocaleString()}
                </Typography>
                {entry.error}
              </Alert>
            ))}
          </CardContent>
        </Card>
      ) : null}
      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
            Prompt
          </Typography>
          <Box
            component="pre"
            sx={{
              bgcolor: 'action.hover',
              borderRadius: 1,
              fontFamily: 'monospace',
              fontSize: 13,
              m: 0,
              overflow: 'auto',
              p: 1.5,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {schedule.prompt}
          </Box>
        </CardContent>
      </Card>
    </>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function ScheduledChatView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const hasPermission = usePermissions();
  const { fromLabel } = (location.state ?? {}) as BackState;
  const { currentUser } = useCurrentUserState();
  const { schedule, loading, error, refresh } = useChatSchedule(id ?? null);
  const { updateSchedule, deleteSchedule } = useChatSchedules(false);
  const fetchSessions = useScheduledChatSessions();

  const [sessions, setSessions] = useState<ScheduledChatSession[] | null>(null);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!id) return undefined;
    fetchSessions(id)
      .then((result) => {
        if (!cancelled) setSessions(result);
      })
      .catch(() => {
        if (!cancelled) setSessionsError('Failed to load runs.');
      });
    return () => {
      cancelled = true;
    };
  }, [id, fetchSessions]);

  const isOwner = Boolean(
    schedule && currentUser && schedule.created_by === currentUser.user_id,
  );
  const canEdit = hasPermission('chat:schedule') && isOwner;

  const handleConfirmDelete = async () => {
    if (!id) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteSchedule(id);
      navigate('/app/scheduled-chats');
    } catch {
      setDeleteError('Failed to delete scheduled chat. Please try again.');
      setDeleting(false);
    }
  };

  const menuActions: RowMenuAction[] = [
    {
      key: 'history',
      label: 'View history',
      icon: <HistoryIcon fontSize="small" />,
      onClick: () =>
        navigate(`/app/scheduled-chats/${id}/history`, {
          state: {
            fromLabel: schedule?.name ?? 'Scheduled Chat',
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
      disabled: !canEdit,
      tooltip: canEdit
        ? undefined
        : 'Only the owner can delete this scheduled chat',
      destructive: true,
      dividerBefore: true,
    },
  ];

  return (
    <>
      <Helmet>
        <title>
          {schedule ? `${schedule.name} | Seizu` : 'Scheduled Chat | Seizu'}
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
          title={schedule?.name ?? 'Scheduled Chat'}
          action={
            <Box sx={{ alignItems: 'center', display: 'flex', gap: 1 }}>
              <Button
                variant="contained"
                startIcon={<EditIcon />}
                onClick={() => setEditOpen(true)}
                disabled={!canEdit}
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
          errorMessage="Failed to load scheduled chat"
        >
          {schedule ? (
            <Grid container spacing={2}>
              <Grid size={{ xs: 12, md: 4 }}>
                <SchedulePanels schedule={schedule} />
              </Grid>
              <Grid size={{ xs: 12, md: 8 }}>
                <Typography variant="subtitle2" sx={{ mb: 1.5 }}>
                  Runs
                </Typography>
                {sessionsError ? (
                  <Alert severity="error">{sessionsError}</Alert>
                ) : sessions === null ? (
                  <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
                    <ConstellationSpinner size={28} />
                  </Box>
                ) : sessions.length === 0 ? (
                  <Typography color="text.secondary" variant="body2">
                    This schedule has not produced any runs yet.
                  </Typography>
                ) : (
                  sessions.map((session) => (
                    <RunAccordion
                      key={session.thread_id}
                      scheduleId={schedule.scheduled_chat_id}
                      session={session}
                    />
                  ))
                )}
              </Grid>
            </Grid>
          ) : (
            <Alert severity="warning">Scheduled chat not found.</Alert>
          )}
        </ListViewState>
      </Box>

      {editOpen && schedule ? (
        <ScheduledChatDialog
          key={schedule.scheduled_chat_id}
          open={editOpen}
          initial={schedule}
          onClose={() => setEditOpen(false)}
          onSave={async (req) => {
            await updateSchedule(schedule.scheduled_chat_id, req);
            await refresh();
          }}
        />
      ) : null}

      <ConfirmDeleteDialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => void handleConfirmDelete()}
        deleting={deleting}
        error={deleteError}
      >
        Delete scheduled chat <strong>{schedule?.name}</strong>? This cannot be
        undone.
      </ConfirmDeleteDialog>
    </>
  );
}

export default ScheduledChatView;
