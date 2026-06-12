import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  List,
  ListItem,
  ListItemButton,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import CloseIcon from '@mui/icons-material/Close';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import ForumIcon from '@mui/icons-material/Forum';
import HistoryIcon from '@mui/icons-material/History';
import PersonIcon from '@mui/icons-material/Person';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ListTable, {
  ListTableColumn,
  listTableActionColumnSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
  listTableTruncateSx,
} from 'src/components/ListTable';
import ListPageHeader from 'src/components/ListPageHeader';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ScheduledChatDialog, {
  describeSchedule,
} from 'src/components/ScheduledChatDialog';
import { MarkdocRenderer } from 'src/components/markdoc/renderer';
import { useChatHistory } from 'src/hooks/useChatHistory';
import {
  ScheduledChat,
  ScheduledChatSession,
  useChatSchedules,
  useScheduledChatSessions,
} from 'src/hooks/useChatSchedules';
import { usePermissions } from 'src/hooks/usePermissions';
import { useFeature } from 'src/features.context';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

const triggerColumnSx = { ...listTableSecondaryCellSx, width: '20%' };
const statusColumnSx = { width: 136 };
const versionColumnSx = { ...listTableSecondaryCellSx, width: 96 };
const updatedAtColumnSx = { ...listTableSecondaryCellSx, width: 180 };

interface TranscriptMessage {
  id: string;
  role: string;
  text: string;
}

// ---------------------------------------------------------------------------
// Runs dialog: lists a schedule's run sessions; selecting one shows the
// read-only transcript (scheduled sessions cannot be continued from the UI).
// ---------------------------------------------------------------------------

interface RunsDialogProps {
  schedule: ScheduledChat;
  onClose: () => void;
}

function RunsDialog({ schedule, onClose }: RunsDialogProps) {
  const fetchSessions = useScheduledChatSessions();
  const fetchHistory = useChatHistory();
  const [sessions, setSessions] = useState<ScheduledChatSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [transcriptSession, setTranscriptSession] =
    useState<ScheduledChatSession | null>(null);
  const [transcript, setTranscript] = useState<TranscriptMessage[] | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    fetchSessions(schedule.scheduled_chat_id)
      .then((result) => {
        if (!cancelled) setSessions(result);
      })
      .catch(() => {
        if (!cancelled) setError('Failed to load run sessions.');
      });
    return () => {
      cancelled = true;
    };
  }, [schedule.scheduled_chat_id, fetchSessions]);

  const openTranscript = (session: ScheduledChatSession) => {
    setTranscriptSession(session);
    setTranscript(null);
    void fetchHistory(session.thread_id).then((messages) => {
      setTranscript(
        messages.map((message) => ({
          id: message.id,
          role: message.role,
          text: message.parts
            .filter((part) => part.type === 'text')
            .map((part) => ('text' in part ? part.text : ''))
            .join(''),
        })),
      );
    });
  };

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle
        sx={{
          alignItems: 'center',
          display: 'flex',
          justifyContent: 'space-between',
        }}
      >
        <Box sx={{ alignItems: 'center', display: 'flex', gap: 1 }}>
          {transcriptSession ? (
            <IconButton
              size="small"
              onClick={() => setTranscriptSession(null)}
              aria-label="Back to runs"
            >
              <ArrowBackIcon fontSize="small" />
            </IconButton>
          ) : null}
          {transcriptSession
            ? transcriptSession.title
            : `Runs – ${schedule.name}`}
          {transcriptSession ? (
            <Chip label="read-only" size="small" variant="outlined" />
          ) : null}
        </Box>
        <IconButton size="small" onClick={onClose} aria-label="Close">
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>
      <DialogContent dividers>
        {transcriptSession ? (
          transcript === null ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
              <ConstellationSpinner size={28} />
            </Box>
          ) : transcript.length === 0 ? (
            <Typography color="text.secondary">
              No messages recorded for this run.
            </Typography>
          ) : (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              {transcript.map((message) => (
                <Box key={message.id} sx={{ display: 'flex', gap: 1.5 }}>
                  {message.role === 'user' ? (
                    <PersonIcon
                      fontSize="small"
                      sx={{ color: 'text.secondary', mt: 0.5 }}
                    />
                  ) : (
                    <SmartToyIcon
                      fontSize="small"
                      sx={{ color: 'text.secondary', mt: 0.5 }}
                    />
                  )}
                  <Box sx={{ minWidth: 0 }}>
                    <MarkdocRenderer source={message.text} untrustedUrls />
                  </Box>
                </Box>
              ))}
            </Box>
          )
        ) : error ? (
          <Alert severity="error">{error}</Alert>
        ) : sessions === null ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
            <ConstellationSpinner size={28} />
          </Box>
        ) : sessions.length === 0 ? (
          <Typography color="text.secondary">
            This schedule has not produced any runs yet.
          </Typography>
        ) : (
          <List dense disablePadding>
            {sessions.map((session) => (
              <ListItem key={session.thread_id} disablePadding>
                <ListItemButton onClick={() => openTranscript(session)}>
                  <ForumIcon
                    fontSize="small"
                    sx={{ color: 'text.secondary', mr: 1.5 }}
                  />
                  <Box sx={{ minWidth: 0 }}>
                    <Typography variant="body2" noWrap>
                      {session.title}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {new Date(session.created_at).toLocaleString()}
                    </Typography>
                  </Box>
                </ListItemButton>
              </ListItem>
            ))}
          </List>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function ScheduledChats() {
  const navigate = useNavigate();
  const hasPermission = usePermissions();
  const chatSchedulesEnabled = useFeature('chat_schedules');
  const canSchedule = chatSchedulesEnabled && hasPermission('chat:schedule');
  const {
    schedules,
    loading,
    error,
    createSchedule,
    updateSchedule,
    deleteSchedule,
  } = useChatSchedules(canSchedule);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<ScheduledChat | null>(null);
  const [runsTarget, setRunsTarget] = useState<ScheduledChat | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ScheduledChat | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const handleConfirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteSchedule(deleteTarget.scheduled_chat_id);
      setDeleteTarget(null);
    } catch {
      setDeleteError('Failed to delete scheduled chat. Please try again.');
    } finally {
      setDeleting(false);
    }
  };

  const rowActions = (schedule: ScheduledChat): RowMenuAction[] => [
    {
      key: 'edit',
      label: 'Edit',
      icon: <EditIcon fontSize="small" />,
      onClick: () => {
        setEditTarget(schedule);
        setDialogOpen(true);
      },
    },
    {
      key: 'runs',
      label: 'View runs',
      icon: <ForumIcon fontSize="small" />,
      onClick: () => setRunsTarget(schedule),
    },
    {
      key: 'history',
      label: 'View history',
      icon: <HistoryIcon fontSize="small" />,
      onClick: () =>
        navigate(`/app/scheduled-chats/${schedule.scheduled_chat_id}/history`, {
          state: { fromLabel: 'Scheduled Chats' } satisfies BackState,
        }),
    },
    {
      key: 'delete',
      label: 'Delete',
      icon: <DeleteIcon fontSize="small" />,
      onClick: () => {
        setDeleteError(null);
        setDeleteTarget(schedule);
      },
      destructive: true,
      dividerBefore: true,
    },
  ];

  const columns: ListTableColumn<ScheduledChat>[] = [
    {
      key: 'name',
      label: 'Name',
      cellSx: listTablePrimaryCellSx,
      render: (schedule) => (
        <Typography
          sx={{
            ...listTableTruncateSx,
            cursor: 'pointer',
            '&:hover': { textDecoration: 'underline' },
          }}
          onClick={() => {
            setEditTarget(schedule);
            setDialogOpen(true);
          }}
        >
          {schedule.name}
        </Typography>
      ),
    },
    {
      key: 'trigger',
      label: 'Trigger',
      hideBelow: 'sm',
      cellSx: triggerColumnSx,
      render: (schedule) =>
        schedule.schedule
          ? describeSchedule(schedule.schedule)
          : 'On scan updates',
    },
    {
      key: 'status',
      label: 'Status',
      cellSx: statusColumnSx,
      render: (schedule) =>
        !schedule.enabled ? (
          <Chip label="disabled" size="small" variant="outlined" />
        ) : schedule.last_run_status ? (
          <Chip
            label={schedule.last_run_status}
            size="small"
            color={schedule.last_run_status === 'success' ? 'success' : 'error'}
            variant="outlined"
          />
        ) : (
          <Chip label="pending" size="small" variant="outlined" />
        ),
    },
    {
      key: 'version',
      label: 'Version',
      hideBelow: 'md',
      cellSx: versionColumnSx,
      render: (schedule) => `v${schedule.current_version}`,
    },
    {
      key: 'last_run_at',
      label: 'Last run',
      hideBelow: 'lg',
      cellSx: updatedAtColumnSx,
      render: (schedule) =>
        schedule.last_run_at
          ? new Date(schedule.last_run_at).toLocaleString()
          : '—',
    },
    {
      key: 'actions',
      align: 'right',
      cellSx: listTableActionColumnSx,
      render: (schedule) => <RowMenu actions={rowActions(schedule)} />,
    },
  ];

  if (!canSchedule) {
    return (
      <Box sx={pageContentSx}>
        <Alert severity="info">
          Scheduled chats are not available for your account.
        </Alert>
      </Box>
    );
  }

  return (
    <>
      <Helmet>
        <title>Scheduled Chats | Seizu</title>
      </Helmet>
      <Box sx={pageContentSx}>
        <ListPageHeader
          title="Scheduled Chats"
          action={
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={() => {
                setEditTarget(null);
                setDialogOpen(true);
              }}
            >
              New scheduled chat
            </Button>
          }
        />
        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load scheduled chats"
        >
          <ListTable
            rows={schedules}
            columns={columns}
            getRowKey={(schedule) => schedule.scheduled_chat_id}
            emptyMessage="No scheduled chats yet."
          />
        </ListViewState>
      </Box>

      {dialogOpen ? (
        <ScheduledChatDialog
          key={editTarget?.scheduled_chat_id ?? 'new'}
          open={dialogOpen}
          initial={editTarget}
          onClose={() => setDialogOpen(false)}
          onSave={(req) =>
            editTarget
              ? updateSchedule(editTarget.scheduled_chat_id, req)
              : createSchedule(req)
          }
        />
      ) : null}

      {runsTarget ? (
        <RunsDialog schedule={runsTarget} onClose={() => setRunsTarget(null)} />
      ) : null}

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void handleConfirmDelete()}
        deleting={deleting}
        error={deleteError}
      >
        Delete scheduled chat <strong>{deleteTarget?.name}</strong>? This cannot
        be undone.
      </ConfirmDeleteDialog>
    </>
  );
}

export default ScheduledChats;
