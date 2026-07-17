import { useEffect, useMemo, useState } from 'react';
import { Helmet } from 'react-helmet';
import { useNavigate } from 'react-router-dom';
import { Alert, Box, Button, Chip, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import SyncProblemIcon from '@mui/icons-material/SyncProblem';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ListPageHeader from 'src/components/ListPageHeader';
import ListTable, { ListTableColumn } from 'src/components/ListTable';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import UserDisplay from 'src/components/UserDisplay';
import WorkflowDialog from 'src/components/WorkflowDialog';
import {
  ActionConfigDependentSchema,
  ActionConfigFieldDef,
  SeizuConfig,
  WorkflowActivityDefinition,
} from 'src/config.context';
import { usePermissions } from 'src/hooks/usePermissions';
import {
  WorkflowItem,
  WorkflowRequest,
  useWorkflowMutations,
  useWorkflowsList,
} from 'src/hooks/useWorkflowsApi';
import { pageContentSx } from 'src/theme/layout';

function triggerLabel(item: WorkflowItem): string {
  if (item.watch_scans.length)
    return `${item.watch_scans.length} watch scan(s)`;
  if (!item.schedule) return 'Manual only';
  if (item.schedule.type === 'interval')
    return `Every ${item.schedule.interval_minutes} min`;
  if (item.schedule.type === 'hourly')
    return `Every ${item.schedule.interval_hours} hour(s)`;
  return item.schedule.type === 'daily' ? 'Daily schedule' : 'Monthly schedule';
}

export default function Workflows() {
  const navigate = useNavigate();
  const hasPermission = usePermissions();
  const { workflows, loading, error, refresh } = useWorkflowsList();
  const { createWorkflow, updateWorkflow, deleteWorkflow, runWorkflow } =
    useWorkflowMutations();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<WorkflowItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<WorkflowItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [activityConfig, setActivityConfig] = useState<{
    types: string[];
    schemas: Record<string, ActionConfigFieldDef[]>;
    definitions: Record<string, WorkflowActivityDefinition>;
    dependent: Record<string, ActionConfigDependentSchema>;
  }>({ types: [], schemas: {}, definitions: {}, dependent: {} });

  useEffect(() => {
    fetch('/api/v1/config')
      .then((response) => response.json() as Promise<SeizuConfig>)
      .then((config) =>
        setActivityConfig({
          types: config.workflow_activity_types ?? [],
          schemas: config.workflow_activity_schemas ?? {},
          definitions: config.workflow_activity_definitions ?? {},
          dependent: config.workflow_activity_dependent_schemas ?? {},
        }),
      )
      .catch(() => undefined);
  }, []);

  const openCreate = () => {
    setEditTarget(null);
    setDialogOpen(true);
  };
  const openEdit = (item: WorkflowItem) => {
    setEditTarget(item);
    setDialogOpen(true);
  };
  const save = async (request: WorkflowRequest) => {
    if (editTarget) await updateWorkflow(editTarget.workflow_id, request);
    else await createWorkflow(request);
    refresh();
  };

  const columns: ListTableColumn<WorkflowItem>[] = useMemo(
    () => [
      {
        key: 'name',
        label: 'Name',
        render: (item) => (
          <Typography
            sx={{
              cursor: 'pointer',
              '&:hover': { textDecoration: 'underline' },
            }}
            onClick={() => navigate(`/app/workflows/${item.workflow_id}`)}
          >
            {item.name}
          </Typography>
        ),
      },
      { key: 'trigger', label: 'Trigger', render: triggerLabel },
      {
        key: 'pipeline',
        label: 'Pipeline',
        render: (item) =>
          `${item.stages.length} stage(s), ${item.stages.reduce((total, stage) => total + stage.activities.length, 0)} activity(ies)`,
      },
      {
        key: 'status',
        label: 'Status',
        render: (item) => (
          <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
            <Chip
              size="small"
              label={item.enabled ? 'Enabled' : 'Disabled'}
              color={item.enabled ? 'success' : 'default'}
            />
            {item.schedule_sync_status !== 'synced' && (
              <Chip
                size="small"
                icon={<SyncProblemIcon />}
                label={`Schedule ${item.schedule_sync_status}`}
                color={
                  item.schedule_sync_status === 'error' ? 'error' : 'warning'
                }
              />
            )}
          </Box>
        ),
      },
      {
        key: 'version',
        label: 'Version',
        render: (item) => item.current_version,
      },
      {
        key: 'updated',
        label: 'Latest Update',
        render: (item) => new Date(item.updated_at).toLocaleString(),
      },
      {
        key: 'updated_by',
        label: 'Updated By',
        render: (item) => (
          <UserDisplay userId={item.updated_by ?? item.created_by} />
        ),
      },
      {
        key: 'actions',
        label: '',
        render: (item) => {
          const actions: RowMenuAction[] = [
            {
              key: 'run',
              label: 'Run now',
              icon: <PlayArrowIcon />,
              disabled: !hasPermission('workflows:write'),
              onClick: async () => {
                setOperationError(null);
                try {
                  await runWorkflow(item.workflow_id);
                  refresh();
                } catch (reason) {
                  setOperationError(
                    reason instanceof Error
                      ? reason.message
                      : 'Failed to run workflow.',
                  );
                }
              },
            },
            {
              key: 'edit',
              label: 'Edit',
              icon: <EditIcon />,
              disabled: !hasPermission('workflows:write'),
              onClick: () => openEdit(item),
            },
            {
              key: 'history',
              label: 'View history',
              icon: <HistoryIcon />,
              onClick: () =>
                navigate(`/app/workflows/${item.workflow_id}/history`),
            },
            {
              key: 'delete',
              label: 'Delete',
              icon: <DeleteIcon />,
              destructive: true,
              disabled: !hasPermission('workflows:delete'),
              onClick: () => setDeleteTarget(item),
            },
          ];
          return <RowMenu actions={actions} />;
        },
      },
    ],
    [hasPermission, navigate, refresh, runWorkflow],
  );

  return (
    <Box sx={pageContentSx}>
      <Helmet>
        <title>Workflows | Seizu</title>
      </Helmet>
      <ListPageHeader
        title="Workflows"
        action={
          hasPermission('workflows:write') ? (
            <Button
              variant="contained"
              startIcon={<AddIcon />}
              onClick={openCreate}
            >
              New workflow
            </Button>
          ) : undefined
        }
      />
      {operationError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {operationError}
        </Alert>
      )}
      {workflows.some((item) => item.schedule_sync_status === 'error') && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          One or more Temporal Schedules could not be synchronized. Definitions
          are saved and the worker will retry automatically.
        </Alert>
      )}
      <ListViewState
        loading={loading}
        error={error}
        errorMessage="Failed to load workflows"
      >
        <ListTable
          rows={workflows}
          columns={columns}
          getRowKey={(item) => item.workflow_id}
          emptyMessage="No workflows have been created."
          searchLabel="Search workflows"
        />
      </ListViewState>
      {dialogOpen && (
        <WorkflowDialog
          open
          initial={editTarget}
          activityTypes={activityConfig.types}
          activitySchemas={activityConfig.schemas}
          activityDefinitions={activityConfig.definitions}
          dependentSchemas={activityConfig.dependent}
          onClose={() => setDialogOpen(false)}
          onSave={save}
        />
      )}
      <ConfirmDeleteDialog
        open={deleteTarget !== null}
        title="Delete workflow?"
        deleting={deleting}
        error={operationError}
        onClose={() => setDeleteTarget(null)}
        onConfirm={async () => {
          if (!deleteTarget) return;
          setDeleting(true);
          setOperationError(null);
          try {
            await deleteWorkflow(deleteTarget.workflow_id);
            setDeleteTarget(null);
            refresh();
          } catch (reason) {
            setOperationError(
              reason instanceof Error
                ? reason.message
                : 'Failed to delete workflow.',
            );
          } finally {
            setDeleting(false);
          }
        }}
      >
        Delete <strong>{deleteTarget?.name}</strong> and its version history?
        Existing Temporal run history is retained by Temporal.
      </ConfirmDeleteDialog>
    </Box>
  );
}
