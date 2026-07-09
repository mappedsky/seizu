import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Button,
  Chip,
  Snackbar,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import BadgeIcon from '@mui/icons-material/Badge';
import CancelOutlinedIcon from '@mui/icons-material/CancelOutlined';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import ErrorOutlineIcon from '@mui/icons-material/Error';
import HelpOutlineIcon from '@mui/icons-material/Help';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircle';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import HistoryIcon from '@mui/icons-material/History';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import ToggleOnIcon from '@mui/icons-material/ToggleOn';
import ToggleOffIcon from '@mui/icons-material/ToggleOff';
import VisibilityIcon from '@mui/icons-material/Visibility';
import {
  useScheduledQueriesList,
  useScheduledQueriesMutations,
  ScheduledQueryItem,
  ScheduledQueryRequest,
} from 'src/hooks/useScheduledQueriesApi';
import { SeizuConfig } from 'src/config.context';
import ScheduledQueryDialog from 'src/components/ScheduledQueryDialog';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissions } from 'src/hooks/usePermissions';
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
import type { BackState } from 'src/navigation';
import { describeSchedule } from 'src/scheduleSpec';
import { pageContentSx } from 'src/theme/layout';

const actionsColumnSx = { width: '18%' };
const statusColumnSx = { width: 128 };

function triggerSummary(item: ScheduledQueryItem): string {
  if (item.watch_scans.length > 0)
    return `Watch scans (${item.watch_scans.length})`;
  if (item.schedule) return describeSchedule(item.schedule);
  if (item.frequency != null) return `Every ${item.frequency} min`;
  return 'Not configured';
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function ScheduledQueries() {
  const navigate = useNavigate();
  const { scheduledQueries, loading, error, refresh } =
    useScheduledQueriesList();
  const {
    createScheduledQuery,
    updateScheduledQuery,
    deleteScheduledQuery,
    runScheduledQuery,
  } = useScheduledQueriesMutations();
  const hasPermission = usePermissions();
  const [runMessage, setRunMessage] = useState<string | null>(null);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<ScheduledQueryItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ScheduledQueryItem | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);
  const [actionConfig, setActionConfig] = useState<SeizuConfig>({
    scheduled_query_action_types: [],
    scheduled_query_action_schemas: {},
  });

  useEffect(() => {
    let cancelled = false;
    fetch('/api/v1/config')
      .then((res) => {
        if (!res.ok)
          throw new globalThis.Error(
            `Failed to load scheduled query action config: ${res.status}`,
          );
        return res.json();
      })
      .then((config: SeizuConfig) => {
        if (!cancelled) {
          setActionConfig({
            scheduled_query_action_types:
              config.scheduled_query_action_types ?? [],
            scheduled_query_action_schemas:
              config.scheduled_query_action_schemas ?? {},
          });
        }
      })
      .catch((err: unknown) => {
        console.log('Scheduled query action config fetch error', err);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const openCreate = () => {
    setEditTarget(null);
    setDialogOpen(true);
  };

  const openEdit = (item: ScheduledQueryItem) => {
    setEditTarget(item);
    setDialogOpen(true);
  };

  const handleSave = async (req: ScheduledQueryRequest) => {
    if (editTarget) {
      await updateScheduledQuery(editTarget.scheduled_query_id, req);
    } else {
      await createScheduledQuery(req);
    }
    refresh();
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteScheduledQuery(deleteTarget.scheduled_query_id);
      setDeleteTarget(null);
      refresh();
    } catch {
      // dialog stays open so user can retry
    } finally {
      setDeleting(false);
    }
  };

  const openView = (item: ScheduledQueryItem) =>
    navigate(`/app/scheduled-queries/${item.scheduled_query_id}`, {
      state: { fromLabel: 'Scheduled Queries' } satisfies BackState,
    });

  const handleRunNow = async (item: ScheduledQueryItem) => {
    try {
      await runScheduledQuery(item.scheduled_query_id);
      setRunMessage(
        `Run requested for "${item.name}". The worker will pick it up on its next poll.`,
      );
    } catch {
      setRunMessage('Failed to request run. Please try again.');
    }
  };

  const rowActions = (item: ScheduledQueryItem): RowMenuAction[] => {
    const canWrite = hasPermission('scheduled_queries:write');
    const canDelete = hasPermission('scheduled_queries:delete');
    return [
      {
        key: 'view',
        label: 'View',
        icon: <VisibilityIcon fontSize="small" />,
        onClick: () => openView(item),
      },
      {
        key: 'edit',
        label: 'Edit',
        icon: <EditIcon fontSize="small" />,
        onClick: () => openEdit(item),
        disabled: !canWrite,
        tooltip: canWrite
          ? undefined
          : 'You do not have permission to edit scheduled queries',
      },
      {
        key: 'run',
        label: 'Run now',
        icon: <PlayArrowIcon fontSize="small" />,
        onClick: () => void handleRunNow(item),
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
          navigate(
            `/app/scheduled-queries/${item.scheduled_query_id}/history`,
            { state: { fromLabel: 'Scheduled Queries' } satisfies BackState },
          ),
      },
      {
        key: 'delete',
        label: 'Delete',
        icon: <DeleteIcon fontSize="small" />,
        onClick: () => setDeleteTarget(item),
        disabled: !canDelete,
        tooltip: canDelete
          ? undefined
          : 'You do not have permission to delete scheduled queries',
        destructive: true,
        dividerBefore: true,
      },
    ];
  };

  const columns: ListTableColumn<ScheduledQueryItem>[] = [
    {
      key: 'name',
      label: 'Name',
      cellSx: listTablePrimaryCellSx,
      render: (item) => (
        <Box sx={{ minWidth: 0 }}>
          <Typography
            variant="body2"
            sx={[
              {
                cursor: 'pointer',
                fontWeight: 500,
                '&:hover': { textDecoration: 'underline' },
              },
              listTableTruncateSx,
            ]}
            onClick={() => openView(item)}
          >
            {item.name}
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{
              ...listTableTruncateSx,
              fontFamily: 'monospace',
              display: 'block',
            }}
          >
            {item.cypher.split('\n')[0]}
          </Typography>
        </Box>
      ),
    },
    {
      key: 'trigger',
      label: 'Trigger',
      hideBelow: 'md',
      cellSx: { ...listTableSecondaryCellSx, width: 150 },
      render: (item) => triggerSummary(item),
    },
    {
      key: 'configured_actions',
      label: 'Actions',
      hideBelow: 'lg',
      cellSx: actionsColumnSx,
      render: (item) =>
        item.actions.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            None
          </Typography>
        ) : (
          <Box
            sx={{
              display: 'flex',
              flexWrap: 'nowrap',
              gap: 0.5,
              overflow: 'hidden',
            }}
          >
            {item.actions.map((action, index) => (
              <Chip
                key={`${action.action_type}-${index}`}
                label={action.action_type}
                size="small"
              />
            ))}
          </Box>
        ),
    },
    {
      key: 'status',
      label: 'Status',
      cellSx: statusColumnSx,
      render: (item) => (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Chip
            label={item.enabled ? 'Enabled' : 'Disabled'}
            color={item.enabled ? 'success' : 'default'}
            size="small"
          />
          <Tooltip
            title={
              item.last_run_status === 'success'
                ? `Last run succeeded${item.last_run_at ? ` at ${new Date(item.last_run_at).toLocaleString()}` : ''}`
                : item.last_run_status === 'failure'
                  ? `Last run failed${item.last_run_at ? ` at ${new Date(item.last_run_at).toLocaleString()}` : ''}`
                  : 'No runs yet'
            }
          >
            <FiberManualRecordIcon
              sx={{
                fontSize: 12,
                color:
                  item.last_run_status === 'success'
                    ? 'success.main'
                    : item.last_run_status === 'failure'
                      ? 'error.main'
                      : 'warning.main',
              }}
            />
          </Tooltip>
        </Box>
      ),
    },
    {
      key: 'version',
      label: 'Version',
      hideBelow: 'sm',
      cellSx: { ...listTableSecondaryCellSx, width: 96 },
      render: (item) => `v${item.current_version}`,
    },
    {
      key: 'updated_at',
      label: 'Last updated',
      hideBelow: 'xl',
      cellSx: { ...listTableSecondaryCellSx, width: 180 },
      render: (item) => new Date(item.updated_at).toLocaleString(),
    },
    {
      key: 'updated_by',
      label: 'Updated by',
      hideBelow: 'lg',
      cellSx: { ...listTableSecondaryCellSx, width: 150 },
      render: (item) =>
        item.updated_by ? (
          <UserDisplay userId={item.updated_by} />
        ) : (
          <UserDisplay userId={item.created_by} />
        ),
    },
    {
      key: 'row_actions',
      align: 'right',
      cellSx: listTableActionColumnSx,
      render: (item) => <RowMenu actions={rowActions(item)} />,
    },
  ];
  const actionTypes = useMemo(
    () =>
      Array.from(
        new Set(
          scheduledQueries
            .flatMap((item) => item.actions.map((action) => action.action_type))
            .filter(Boolean),
        ),
      ).sort(),
    [scheduledQueries],
  );
  const filterGroups: ListTableFilterGroup<ScheduledQueryItem>[] = useMemo(
    () => [
      {
        key: 'actions',
        label: 'Actions',
        icon: <BadgeIcon fontSize="small" />,
        options: [
          {
            key: 'none',
            label: 'No actions',
            icon: <HelpOutlineIcon fontSize="small" />,
            matches: (item) => item.actions.length === 0,
          },
          ...actionTypes.map((actionType) => ({
            key: actionType,
            label: actionType,
            icon: <BadgeIcon fontSize="small" />,
            matches: (item) =>
              item.actions.some((action) => action.action_type === actionType),
          })),
        ],
      },
      {
        key: 'status',
        label: 'Status',
        icon: <ToggleOnIcon fontSize="small" />,
        options: [
          {
            key: 'enabled',
            label: 'Enabled',
            icon: <ToggleOnIcon fontSize="small" />,
            matches: (item) => item.enabled,
          },
          {
            key: 'disabled',
            label: 'Disabled',
            icon: <ToggleOffIcon fontSize="small" />,
            matches: (item) => !item.enabled,
          },
          {
            key: 'success',
            label: 'Last run succeeded',
            icon: <CheckCircleOutlineIcon fontSize="small" />,
            matches: (item) => item.last_run_status === 'success',
          },
          {
            key: 'failure',
            label: 'Last run failed',
            icon: <ErrorOutlineIcon fontSize="small" />,
            matches: (item) => item.last_run_status === 'failure',
          },
          {
            key: 'none',
            label: 'No runs yet',
            icon: <CancelOutlinedIcon fontSize="small" />,
            matches: (item) => item.last_run_status === null,
          },
        ],
      },
    ],
    [actionTypes],
  );

  return (
    <>
      <Box sx={pageContentSx}>
        <ListPageHeader
          title="Scheduled Queries"
          action={
            hasPermission('scheduled_queries:write') && (
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={openCreate}
              >
                New scheduled query
              </Button>
            )
          }
        />

        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load scheduled queries"
        >
          <ListTable
            rows={scheduledQueries}
            columns={columns}
            getRowKey={(item) => item.scheduled_query_id}
            emptyMessage="No scheduled queries yet. Create one above."
            filterGroups={filterGroups}
          />
        </ListViewState>
      </Box>

      <ScheduledQueryDialog
        key={editTarget?.scheduled_query_id ?? 'new'}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onSave={handleSave}
        initial={editTarget}
        actionTypes={actionConfig.scheduled_query_action_types}
        actionSchemas={actionConfig.scheduled_query_action_schemas}
      />

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete scheduled query?"
        deleting={deleting}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
      >
        Permanently delete <strong>{deleteTarget?.name}</strong> and all its
        versions? This cannot be undone.
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

export default ScheduledQueries;
