import { useState } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import { Box, Button, Typography } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import HistoryIcon from '@mui/icons-material/History';
import RestoreIcon from '@mui/icons-material/Restore';
import VisibilityIcon from '@mui/icons-material/Visibility';
import {
  ToolVersion,
  useToolVersionsList,
  useToolMutations,
  mcpNameForTool,
} from 'src/hooks/useToolsetsApi';
import ListTable, {
  ListTableColumn,
  listTableActionColumnSx,
  listTableSecondaryCellSx,
} from 'src/components/ListTable';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import ToolDetailDialog, {
  ToolViewData,
} from 'src/components/ToolDetailDialog';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissions } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

const savedColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const authorColumnSx = { ...listTableSecondaryCellSx, width: 150 };
const commentColumnSx = { ...listTableSecondaryCellSx, width: '28%' };

function toolVersionViewData(version: ToolVersion): ToolViewData {
  return {
    name: version.name,
    slug: mcpNameForTool(version),
    version: version.version,
    description: version.description,
    cypher: version.cypher,
    parameters: version.parameters,
    enabled: version.enabled,
  };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function ToolHistory() {
  const { toolsetId, toolId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const hasPermission = usePermissions();
  const { fromLabel } = (location.state ?? {}) as BackState;

  const { versions, loading, error } = useToolVersionsList(
    toolsetId ?? null,
    toolId ?? null,
  );
  const mutations = useToolMutations(toolsetId ?? '');
  const [detailData, setDetailData] = useState<ToolViewData | null>(null);

  const sorted = [...versions].sort((a, b) => b.version - a.version);
  const latestVersion = sorted[0]?.version;
  const toolName = sorted[0]?.name;

  const rowActions = (version: ToolVersion): RowMenuAction[] => {
    const isCurrent = version.version === latestVersion;
    const canWrite = hasPermission('tools:write');
    return [
      {
        key: 'detail',
        label: 'View detail',
        icon: <VisibilityIcon fontSize="small" />,
        onClick: () => setDetailData(toolVersionViewData(version)),
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
            ? 'You do not have permission to restore tool versions'
            : undefined,
      },
    ];
  };
  const columns: ListTableColumn<ToolVersion>[] = [
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
              onClick={() => setDetailData(toolVersionViewData(version))}
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

  async function handleRestore(version: ToolVersion) {
    if (!toolId) return;
    await mutations.updateTool(toolId, {
      name: version.name,
      description: version.description,
      cypher: version.cypher,
      parameters: version.parameters,
      enabled: version.enabled,
      comment: `Restored from version ${version.version}`,
    });
    navigate(`/app/toolsets/${toolsetId}/tools`);
  }

  return (
    <>
      <Helmet>
        <title>
          {toolName ? `History – ${toolName} | Seizu` : 'History | Seizu'}
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
            Version history{toolName ? ` – ${toolName}` : ''}
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

      <ToolDetailDialog
        open={!!detailData}
        onClose={() => setDetailData(null)}
        data={detailData}
      />
    </>
  );
}

export default ToolHistory;
