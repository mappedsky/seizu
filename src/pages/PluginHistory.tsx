import { useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import { Alert, Box, Button, Typography } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import HistoryIcon from '@mui/icons-material/History';
import RestoreIcon from '@mui/icons-material/Restore';
import VisibilityIcon from '@mui/icons-material/Visibility';
import ListTable, {
  ListTableColumn,
  listTableActionColumnSx,
  listTableSecondaryCellSx,
} from 'src/components/ListTable';
import ListViewState from 'src/components/ListViewState';
import PluginVersionDialog from 'src/components/PluginVersionDialog';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissions } from 'src/hooks/usePermissions';
import {
  PluginVersion,
  usePluginMutations,
  usePluginVersionsList,
} from 'src/hooks/usePluginsApi';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

const savedColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const authorColumnSx = { ...listTableSecondaryCellSx, width: 150 };
const commentColumnSx = { ...listTableSecondaryCellSx, width: '28%' };
const packageColumnSx = { ...listTableSecondaryCellSx, width: 140 };

function packageVersion(version: PluginVersion): string {
  const value = version.manifest?.version;
  return typeof value === 'string' && value ? value : '—';
}

function PluginHistory() {
  const { pluginId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const hasPermission = usePermissions();
  const { fromLabel } = (location.state ?? {}) as BackState;
  const { versions, loading, error } = usePluginVersionsList(pluginId ?? null);
  const { restore } = usePluginMutations();
  const [failure, setFailure] = useState<string | null>(null);
  const [viewTarget, setViewTarget] = useState<PluginVersion | null>(null);

  const sorted = [...versions].sort((a, b) => b.revision - a.revision);
  const latestRevision = sorted[0]?.revision;

  const handleRestore = async (version: PluginVersion) => {
    if (!pluginId || latestRevision === undefined) return;
    setFailure(null);
    try {
      await restore(
        pluginId,
        version.revision,
        latestRevision,
        `Restored from version ${version.revision}`,
      );
      navigate('/app/plugins');
    } catch (reason) {
      setFailure((reason as Error).message);
    }
  };

  const rowActions = (version: PluginVersion): RowMenuAction[] => {
    const isCurrent = version.revision === latestRevision;
    const canWrite = hasPermission('plugins:write');
    return [
      {
        key: 'view',
        label: 'View contents',
        icon: <VisibilityIcon fontSize="small" />,
        onClick: () => setViewTarget(version),
      },
      {
        key: 'restore',
        label: 'Restore',
        icon: <RestoreIcon fontSize="small" />,
        onClick: () => void handleRestore(version),
        disabled: isCurrent || !canWrite,
        tooltip: isCurrent
          ? 'This is already the current version'
          : !canWrite
            ? 'You do not have permission to restore plugin versions'
            : undefined,
      },
    ];
  };

  const columns: ListTableColumn<PluginVersion>[] = [
    {
      key: 'version',
      label: 'Version',
      cellSx: { width: 120 },
      render: (version) => {
        const isCurrent = version.revision === latestRevision;
        return (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Typography
              sx={{
                cursor: 'pointer',
                fontWeight: isCurrent ? 'bold' : 'medium',
                '&:hover': { textDecoration: 'underline' },
              }}
              onClick={() => setViewTarget(version)}
            >
              v{version.revision}
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
      key: 'package_version',
      label: 'Package version',
      hideBelow: 'md',
      cellSx: packageColumnSx,
      render: packageVersion,
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
      <Helmet>
        <title>
          {pluginId ? `History – ${pluginId} | Seizu` : 'History | Seizu'}
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
            Version history{pluginId ? ` – ${pluginId}` : ''}
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
            getRowKey={(version) => version.revision}
            emptyMessage="No versions found."
            pagination={false}
          />
        </ListViewState>
      </Box>

      <PluginVersionDialog
        open={!!viewTarget}
        version={viewTarget}
        isCurrent={viewTarget?.revision === latestRevision}
        onClose={() => setViewTarget(null)}
      />
    </>
  );
}

export default PluginHistory;
