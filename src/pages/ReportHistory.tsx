import {
  useParams,
  useNavigate,
  useLocation,
  Link as RouterLink,
} from 'react-router-dom';
import PageTitle from 'src/components/PageTitle';
import { Box, Button, Link, Typography } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import HistoryIcon from '@mui/icons-material/History';
import RestoreIcon from '@mui/icons-material/Restore';
import VisibilityIcon from '@mui/icons-material/Visibility';

import {
  ReportVersion,
  useReportVersionsList,
  useReportsMutations,
} from 'src/hooks/useReportsApi';
import { Report } from 'src/config.context';
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
const authorColumnSx = { ...listTableSecondaryCellSx, width: 150 };
const commentColumnSx = listTableSecondaryCellSx;

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function ReportHistory() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const hasPermission = usePermissions();
  const { fromLabel, originReturnTo } = (location.state ?? {}) as BackState;

  const { versions, loading, error } = useReportVersionsList(id);
  const { saveReportVersion } = useReportsMutations();

  const sorted = [...versions].sort((a, b) => b.version - a.version);
  const latestVersion = sorted[0]?.version;
  const reportName = sorted[0]?.name;
  const historyBackTarget = originReturnTo ?? `/app/reports/${id}`;
  const versionBackState = {
    fromLabel: reportName ? `History – ${reportName}` : 'history',
    returnTo: `/app/reports/${id}/history`,
    originReturnTo: historyBackTarget,
  } satisfies BackState;

  const rowActions = (version: ReportVersion): RowMenuAction[] => {
    const isCurrent = version.version === latestVersion;
    const canWrite = hasPermission('reports:write');
    return [
      {
        key: 'view',
        label: 'View',
        icon: <VisibilityIcon fontSize="small" />,
        onClick: () =>
          navigate(`/app/reports/${id}/versions/${version.version}`, {
            state: versionBackState,
          }),
      },
      {
        key: 'restore',
        label: 'Restore',
        icon: <RestoreIcon fontSize="small" />,
        onClick: () => handleRestore(version),
        disabled: isCurrent || !canWrite,
        tooltip: isCurrent
          ? 'This is already the current version'
          : !canWrite
            ? 'You do not have permission to restore report versions'
            : undefined,
        dividerBefore: true,
      },
    ];
  };

  const columns: ListTableColumn<ReportVersion>[] = [
    {
      key: 'version',
      label: 'Version',
      cellSx: { width: 120 },
      render: (version) => {
        const isCurrent = version.version === latestVersion;
        return (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Link
              component={RouterLink}
              to={`/app/reports/${id}/versions/${version.version}`}
              state={versionBackState}
              underline="hover"
              color="inherit"
              sx={{ fontWeight: isCurrent ? 'bold' : 'medium' }}
            >
              v{version.version}
            </Link>
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
      key: 'saved',
      label: 'Saved',
      hideBelow: 'sm',
      cellSx: savedColumnSx,
      render: (version) => new Date(version.created_at).toLocaleString(),
    },
    {
      key: 'author',
      label: 'Author',
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

  async function handleRestore(version: ReportVersion) {
    if (!id) return;
    await saveReportVersion(
      id,
      version.config as Report,
      `Restored from version ${version.version}`,
    );
    navigate(`/app/reports/${id}`);
  }

  return (
    <>
      <PageTitle>
        {reportName ? `History – ${reportName} | Seizu` : `History | Seizu`}
      </PageTitle>
      <Box sx={pageContentSx}>
        {fromLabel && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <Button
              size="small"
              startIcon={<ArrowBackIcon />}
              onClick={() => navigate(historyBackTarget)}
            >
              Back to {fromLabel}
            </Button>
          </Box>
        )}

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 3 }}>
          <HistoryIcon color="action" />
          <Typography variant="h1">
            Version history{reportName ? ` – ${reportName}` : ''}
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

export default ReportHistory;
