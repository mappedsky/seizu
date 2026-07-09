import { useState } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import { Box, Button, Typography } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import HistoryIcon from '@mui/icons-material/History';
import RestoreIcon from '@mui/icons-material/Restore';

import {
  ScheduledQueryVersion,
  useScheduledQueryVersionsList,
  useScheduledQueriesMutations,
} from 'src/hooks/useScheduledQueriesApi';
import ListTable, {
  ListTableColumn,
  listTableActionColumnSx,
  listTableSecondaryCellSx,
} from 'src/components/ListTable';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import UserDisplay from 'src/components/UserDisplay';
import ScheduledQueryDetailDialog, {
  ScheduledQueryViewData,
} from 'src/components/ScheduledQueryDetailDialog';
import { usePermissions } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

const savedColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const authorColumnSx = { ...listTableSecondaryCellSx, width: 150 };
const commentColumnSx = { ...listTableSecondaryCellSx, width: '28%' };

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function ScheduledQueryHistory() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const hasPermission = usePermissions();
  const { fromLabel } = (location.state ?? {}) as BackState;

  const { versions, loading, error } = useScheduledQueryVersionsList(
    id ?? null,
  );
  const { updateScheduledQuery } = useScheduledQueriesMutations();
  const [detailData, setDetailData] = useState<ScheduledQueryViewData | null>(
    null,
  );

  const sorted = [...versions].sort((a, b) => b.version - a.version);
  const latestVersion = sorted[0]?.version;
  const queryName = sorted[0]?.name;

  const rowActions = (version: ScheduledQueryVersion): RowMenuAction[] => {
    const isCurrent = version.version === latestVersion;
    const canWrite = hasPermission('scheduled_queries:write');
    return [
      {
        key: 'restore',
        label: 'Restore',
        icon: <RestoreIcon fontSize="small" />,
        onClick: () => handleRestore(version),
        disabled: isCurrent || !canWrite,
        tooltip: isCurrent
          ? 'This is already the current version'
          : !canWrite
            ? 'You do not have permission to restore scheduled query versions'
            : undefined,
      },
    ];
  };

  const columns: ListTableColumn<ScheduledQueryVersion>[] = [
    {
      key: 'version',
      label: 'Version',
      cellSx: { width: 120 },
      render: (version) => {
        const isCurrent = version.version === latestVersion;
        return (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography
              sx={{
                cursor: 'pointer',
                fontWeight: isCurrent ? 'bold' : 'medium',
                '&:hover': { textDecoration: 'underline' },
              }}
              onClick={() =>
                setDetailData({
                  name: version.name,
                  version: version.version,
                  cypher: version.cypher,
                  params: version.params,
                  frequency: version.frequency,
                  schedule: version.schedule,
                  watch_scans: version.watch_scans,
                  enabled: version.enabled,
                  actions: version.actions,
                })
              }
            >
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
      cellSx: { width: '24%' },
      render: (version) => version.name,
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

  async function handleRestore(version: ScheduledQueryVersion) {
    if (!id) return;
    await updateScheduledQuery(id, {
      name: version.name,
      cypher: version.cypher,
      params: version.params,
      frequency: version.frequency,
      schedule: version.schedule,
      watch_scans: version.watch_scans,
      enabled: version.enabled,
      actions: version.actions,
      comment: `Restored from version ${version.version}`,
    });
    navigate(`/app/scheduled-queries/${id}`);
  }

  return (
    <>
      <Helmet>
        <title>
          {queryName ? `History – ${queryName} | Seizu` : `History | Seizu`}
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

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 3 }}>
          <HistoryIcon color="action" />
          <Typography variant="h1">
            Version history{queryName ? ` – ${queryName}` : ''}
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

      <ScheduledQueryDetailDialog
        open={!!detailData}
        onClose={() => setDetailData(null)}
        data={detailData}
      />
    </>
  );
}

export default ScheduledQueryHistory;
