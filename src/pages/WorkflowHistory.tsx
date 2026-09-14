import { useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { Alert, Box, Button, Typography } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import HistoryIcon from '@mui/icons-material/History';
import RestoreIcon from '@mui/icons-material/Restore';
import ListTable, {
  ListTableColumn,
  listTableActionColumnSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
} from 'src/components/ListTable';
import ListViewState from 'src/components/ListViewState';
import PageTitle from 'src/components/PageTitle';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissions } from 'src/hooks/usePermissions';
import {
  WorkflowVersion,
  useWorkflowMutations,
  useWorkflowVersionsList,
} from 'src/hooks/useWorkflowsApi';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';
import {
  workflowPipelineLabel,
  workflowTriggerLabel,
} from 'src/workflowTrigger';

// Sized in pixels, never percentages: the table's minimum width is solved
// against the percentage share, so a percentage column divides every pixel
// column by what it leaves over. Name and Comment carry no width and take the
// slack between them.
const versionColumnSx = { width: 120 };
const triggerColumnSx = { width: 150 };
const pipelineColumnSx = { width: 170 };
const savedColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const authorColumnSx = { ...listTableSecondaryCellSx, width: 150 };
const commentColumnSx = listTableSecondaryCellSx;

function WorkflowHistory() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const hasPermission = usePermissions();
  const { fromLabel } = (location.state ?? {}) as BackState;

  const { versions, loading, error } = useWorkflowVersionsList(id ?? null);
  const { updateWorkflow } = useWorkflowMutations();
  const [failure, setFailure] = useState<string | null>(null);

  const sorted = [...versions].sort((a, b) => b.version - a.version);
  const latestVersion = sorted[0]?.version;
  const workflowName = sorted[0]?.name;

  const handleRestore = async (version: WorkflowVersion) => {
    if (!id) return;
    setFailure(null);
    try {
      await updateWorkflow(id, {
        name: version.name,
        stages: version.stages,
        trigger_workflows: version.trigger_workflows,
        schedule: version.schedule,
        watch_scans: version.watch_scans,
        enabled: version.enabled,
        comment: `Restored from version ${version.version}`,
      });
      navigate(`/app/workflows/${id}`);
    } catch (reason) {
      setFailure(
        reason instanceof Error
          ? reason.message
          : 'Failed to restore workflow version.',
      );
    }
  };

  const rowActions = (version: WorkflowVersion): RowMenuAction[] => {
    const isCurrent = version.version === latestVersion;
    const canWrite = hasPermission('workflows:write');
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
            ? 'You do not have permission to restore workflow versions'
            : undefined,
      },
    ];
  };

  const columns: ListTableColumn<WorkflowVersion>[] = [
    {
      key: 'version',
      label: 'Version',
      cellSx: versionColumnSx,
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
      hideBelow: 'xl',
      cellSx: triggerColumnSx,
      render: (version) => workflowTriggerLabel(version),
    },
    {
      key: 'pipeline',
      label: 'Pipeline',
      hideBelow: 'xl',
      cellSx: pipelineColumnSx,
      render: (version) => workflowPipelineLabel(version),
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
      label: 'Created By',
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
        {workflowName ? `History – ${workflowName} | Seizu` : 'History | Seizu'}
      </PageTitle>
      <Box sx={pageContentSx}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <Button
            size="small"
            startIcon={<ArrowBackIcon />}
            onClick={() =>
              fromLabel ? navigate(-1) : navigate(`/app/workflows/${id}`)
            }
          >
            Back to {fromLabel ?? 'workflow'}
          </Button>
        </Box>

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 3 }}>
          <HistoryIcon color="action" />
          <Typography variant="h1">
            Version history{workflowName ? ` – ${workflowName}` : ''}
          </Typography>
        </Box>

        {failure && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {failure}
          </Alert>
        )}

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

export default WorkflowHistory;
