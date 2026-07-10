import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import {
  Alert,
  Box,
  Button,
  Chip,
  FormControlLabel,
  Snackbar,
  Switch,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import PersonIcon from '@mui/icons-material/Person';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import VisibilityIcon from '@mui/icons-material/Visibility';
import ListTable, {
  ListTableColumn,
  ListTableFilterGroup,
  listTableActionColumnSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
  listTableTruncateSx,
} from 'src/components/ListTable';
import ListPageHeader from 'src/components/ListPageHeader';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ScheduledChatDialog from 'src/components/ScheduledChatDialog';
import { describeSchedule } from 'src/scheduleSpec';
import UserDisplay from 'src/components/UserDisplay';
import { ScheduledChat, useChatSchedules } from 'src/hooks/useChatSchedules';
import { usePermissionState } from 'src/hooks/usePermissions';
import { useFeature } from 'src/features.context';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

const triggerColumnSx = { ...listTableSecondaryCellSx, width: '20%' };
const statusColumnSx = { width: 136 };
const versionColumnSx = { ...listTableSecondaryCellSx, width: 96 };
const updatedAtColumnSx = { ...listTableSecondaryCellSx, width: 180 };

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function ScheduledChats() {
  const navigate = useNavigate();
  const { hasPermission, currentUser } = usePermissionState();
  const chatSchedulesEnabled = useFeature('chat_schedules');
  const canSchedule = chatSchedulesEnabled && hasPermission('chat:schedule');
  const canReadAll = hasPermission('chat:schedule:read_all');
  const [showAllUsers, setShowAllUsers] = useState(false);
  const allMode = canReadAll && showAllUsers;
  const {
    schedules,
    loading,
    error,
    createSchedule,
    updateSchedule,
    deleteSchedule,
    runSchedule,
  } = useChatSchedules(canSchedule, { all: allMode });
  const owners = Array.from(
    new Set(schedules.map((schedule) => schedule.created_by)),
  );
  // Same facet-filter UI as the reports list: in all-users mode the table
  // gains a User filter group with one option per schedule owner.
  const filterGroups: ListTableFilterGroup<ScheduledChat>[] = allMode
    ? [
        {
          key: 'owner',
          label: 'User',
          icon: <PersonIcon fontSize="small" />,
          options: owners.map((owner) => ({
            key: owner,
            label: <UserDisplay userId={owner} />,
            matches: (schedule: ScheduledChat) => schedule.created_by === owner,
          })),
        },
      ]
    : [];

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<ScheduledChat | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ScheduledChat | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [runMessage, setRunMessage] = useState<string | null>(null);

  const handleRunNow = async (schedule: ScheduledChat) => {
    try {
      await runSchedule(schedule.scheduled_chat_id);
      setRunMessage(
        `Run requested for "${schedule.name}". The worker will pick it up on its next poll.`,
      );
    } catch {
      setRunMessage('Failed to request run. Please try again.');
    }
  };

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

  const openView = (schedule: ScheduledChat) =>
    navigate(`/app/scheduled-chats/${schedule.scheduled_chat_id}`, {
      state: { fromLabel: 'Scheduled Chats' } satisfies BackState,
    });

  const rowActions = (schedule: ScheduledChat): RowMenuAction[] => {
    const isOwner = currentUser?.user_id === schedule.created_by;
    const ownerOnlyTooltip = isOwner
      ? undefined
      : 'Only the schedule owner can modify this scheduled chat.';
    return [
      {
        key: 'view',
        label: 'View',
        icon: <VisibilityIcon fontSize="small" />,
        onClick: () => openView(schedule),
      },
      {
        key: 'edit',
        label: 'Edit',
        icon: <EditIcon fontSize="small" />,
        onClick: () => {
          setEditTarget(schedule);
          setDialogOpen(true);
        },
        disabled: !isOwner,
        tooltip: ownerOnlyTooltip,
      },
      {
        key: 'run',
        label: 'Run now',
        icon: <PlayArrowIcon fontSize="small" />,
        onClick: () => void handleRunNow(schedule),
        disabled: !isOwner,
        tooltip: isOwner
          ? 'Runs on the worker’s next poll, even if disabled'
          : 'Only the schedule owner can run this scheduled chat.',
      },
      {
        key: 'history',
        label: 'View history',
        icon: <HistoryIcon fontSize="small" />,
        onClick: () =>
          navigate(
            `/app/scheduled-chats/${schedule.scheduled_chat_id}/history`,
            {
              state: { fromLabel: 'Scheduled Chats' } satisfies BackState,
            },
          ),
      },
      {
        key: 'delete',
        label: 'Delete',
        icon: <DeleteIcon fontSize="small" />,
        onClick: () => {
          setDeleteError(null);
          setDeleteTarget(schedule);
        },
        disabled: !isOwner,
        tooltip: ownerOnlyTooltip,
        destructive: true,
        dividerBefore: true,
      },
    ];
  };

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
          onClick={() => openView(schedule)}
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
    ...(allMode
      ? [
          {
            key: 'owner',
            label: 'Owner',
            hideBelow: 'sm',
            cellSx: { ...listTableSecondaryCellSx, width: 150 },
            render: (schedule) => <UserDisplay userId={schedule.created_by} />,
          } satisfies ListTableColumn<ScheduledChat>,
        ]
      : []),
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
        {canReadAll ? (
          <Box sx={{ alignItems: 'center', display: 'flex', mb: 1.5 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={showAllUsers}
                  onChange={(e) => setShowAllUsers(e.target.checked)}
                  size="small"
                />
              }
              label="Show all users"
            />
          </Box>
        ) : null}
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
            filterGroups={filterGroups}
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

      <Snackbar
        open={runMessage !== null}
        autoHideDuration={6000}
        onClose={() => setRunMessage(null)}
        message={runMessage}
      />
    </>
  );
}

export default ScheduledChats;
