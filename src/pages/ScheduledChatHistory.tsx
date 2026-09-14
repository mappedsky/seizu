import { useParams, useNavigate, useLocation } from 'react-router-dom';
import PageTitle from 'src/components/PageTitle';
import { Box, Button, Typography } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import HistoryIcon from '@mui/icons-material/History';
import RestoreIcon from '@mui/icons-material/Restore';
import {
  ScheduledChatVersion,
  useChatSchedules,
  useChatScheduleVersions,
} from 'src/hooks/useChatSchedules';
import { describeSchedule } from 'src/scheduleSpec';
import ListTable, {
  ListTableColumn,
  listTableActionColumnSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
} from 'src/components/ListTable';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissions } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

// Sized in pixels, never percentages: the table's minimum width is solved
// against the percentage share, so a percentage column divides every pixel
// column by what it leaves over. The columns that should absorb the slack
// carry no width at all.
const savedColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const triggerColumnSx = { width: 160 };
const authorColumnSx = { ...listTableSecondaryCellSx, width: 150 };
const commentColumnSx = listTableSecondaryCellSx;

function ScheduledChatHistory() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const hasPermission = usePermissions();
  const { fromLabel } = (location.state ?? {}) as BackState;

  const { versions, loading, error } = useChatScheduleVersions(id ?? null);
  const { updateSchedule } = useChatSchedules(false);

  const sorted = [...versions].sort((a, b) => b.version - a.version);
  const latestVersion = sorted[0]?.version;
  const scheduleName = sorted[0]?.name;

  async function handleRestore(version: ScheduledChatVersion) {
    if (!id) return;
    await updateSchedule(id, {
      name: version.name,
      prompt: version.prompt,
      schedule: version.schedule,
      watch_scans: version.watch_scans,
      enabled: version.enabled,
      comment: `Restored from version ${version.version}`,
    });
    navigate('/app/scheduled-chats');
  }

  const rowActions = (version: ScheduledChatVersion): RowMenuAction[] => {
    const isCurrent = version.version === latestVersion;
    const canWrite = hasPermission('chat:schedule');
    return [
      {
        key: 'restore',
        label: 'Restore',
        icon: <RestoreIcon fontSize="small" />,
        onClick: () => void handleRestore(version),
        disabled: isCurrent || !canWrite,
        tooltip: isCurrent
          ? 'This is already the current version'
          : !canWrite
            ? 'You do not have permission to restore scheduled chat versions'
            : undefined,
      },
    ];
  };

  const columns: ListTableColumn<ScheduledChatVersion>[] = [
    {
      key: 'version',
      label: 'Version',
      cellSx: { width: 120 },
      render: (version) => {
        const isCurrent = version.version === latestVersion;
        return (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography sx={{ fontWeight: isCurrent ? 'bold' : 'medium' }}>
              v{version.version}
            </Typography>
            {isCurrent && (
              <Typography component="span" variant="caption" color="primary">
                current
              </Typography>
            )}
          </Box>
        );
      },
    },
    {
      key: 'name',
      label: 'Name',
      cellSx: listTablePrimaryCellSx,
      render: (version) => version.name,
    },
    {
      key: 'trigger',
      label: 'Trigger',
      hideBelow: 'md',
      cellSx: triggerColumnSx,
      render: (version) =>
        version.schedule
          ? describeSchedule(version.schedule)
          : 'On scan updates',
    },
    {
      key: 'saved',
      label: 'Saved',
      hideBelow: 'sm',
      cellSx: savedColumnSx,
      render: (version) => new Date(version.created_at).toLocaleString(),
    },
    {
      key: 'created_by',
      label: 'Created by',
      hideBelow: 'md',
      cellSx: authorColumnSx,
      render: (version) => <UserDisplay userId={version.created_by} />,
    },
    {
      key: 'comment',
      label: 'Comment',
      hideBelow: 'lg',
      cellSx: commentColumnSx,
      render: (version) => version.comment || '—',
    },
    {
      key: 'actions',
      align: 'right',
      cellSx: listTableActionColumnSx,
      render: (version) => <RowMenu actions={rowActions(version)} />,
    },
  ];

  return (
    <>
      <PageTitle>
        {scheduleName ? `History – ${scheduleName} | Seizu` : 'History | Seizu'}
      </PageTitle>
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

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 3 }}>
          <HistoryIcon color="action" />
          <Typography variant="h1">
            Version history{scheduleName ? ` – ${scheduleName}` : ''}
          </Typography>
        </Box>

        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load version history"
        >
          <ListTable
            rows={sorted}
            columns={columns}
            getRowKey={(version) => version.version}
            emptyMessage="No versions found."
            pagination={false}
          />
        </ListViewState>
      </Box>
    </>
  );
}

export default ScheduledChatHistory;
