import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Box, Button, Chip, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import BadgeIcon from '@mui/icons-material/Badge';
import HubIcon from '@mui/icons-material/Hub';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import VisibilityIcon from '@mui/icons-material/Visibility';
import PersonOutlineIcon from '@mui/icons-material/Person';
import ToggleOnIcon from '@mui/icons-material/ToggleOn';
import ToggleOffIcon from '@mui/icons-material/ToggleOff';
import {
  useToolsList,
  useToolMutations,
  mcpNameForTool,
  ToolItem,
} from 'src/hooks/useToolsetsApi';
import ListTable, {
  ListTableColumn,
  ListTableFilterGroup,
  listTableActionColumnSx,
  listTableMonoCellSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
  listTableTruncateSx,
} from 'src/components/ListTable';
import ListPageHeader from 'src/components/ListPageHeader';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ToolDetailDialog, {
  ToolViewData,
} from 'src/components/ToolDetailDialog';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissions } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

// ---------------------------------------------------------------------------
// Built-in toolset detection
// ---------------------------------------------------------------------------
// The backend returns built-in groups through the same /api/v1/toolsets/<id>/tools
// endpoint; their toolset_id is prefixed with `__builtin_`.  All we need here is
// a prefix check to gate edit/delete actions.

const BUILTIN_PREFIX = '__builtin_';
const EXTERNAL_PREFIX = '__external_';

const isBuiltinToolset = (id: string | undefined | null): boolean =>
  !!id && id.startsWith(BUILTIN_PREFIX) && id.endsWith('__');

const isExternalToolset = (id: string | undefined | null): boolean =>
  !!id && id.startsWith(EXTERNAL_PREFIX) && id.endsWith('__');

const isReadOnlyToolset = (id: string | undefined | null): boolean =>
  isBuiltinToolset(id) || isExternalToolset(id);

const descriptionColumnSx = { ...listTableSecondaryCellSx, width: '22%' };
const paramsColumnSx = { ...listTableSecondaryCellSx, width: 96 };
const versionColumnSx = { ...listTableSecondaryCellSx, width: 96 };
const updatedAtColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const updatedByColumnSx = { ...listTableSecondaryCellSx, width: 150 };
const statusColumnSx = { width: 176 };

function toolStatus(item: ToolItem): { enabled: boolean; label: string } {
  const effectiveEnabled = item.effective_enabled ?? item.enabled;
  if (effectiveEnabled) return { enabled: true, label: 'Enabled' };
  if (item.disabled_reason === 'toolset_disabled')
    return { enabled: false, label: 'Disabled by toolset' };
  return { enabled: false, label: 'Disabled' };
}

function toolViewData(item: ToolItem): ToolViewData {
  return {
    name: item.name,
    slug: mcpNameForTool(item),
    description: item.description,
    cypher: item.cypher,
    parameters: item.parameters,
    enabled: item.enabled,
    effective_enabled: item.effective_enabled,
    disabled_reason: item.disabled_reason,
  };
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function ToolsetTools() {
  const { toolsetId } = useParams();
  const navigate = useNavigate();
  const hasPermission = usePermissions();

  const isBuiltin = isBuiltinToolset(toolsetId);
  const isExternal = isExternalToolset(toolsetId);
  const isReadOnly = isReadOnlyToolset(toolsetId);
  const { tools, loading, error, refresh } = useToolsList(toolsetId ?? null);
  const mutations = useToolMutations(toolsetId ?? '');

  const [deleteTarget, setDeleteTarget] = useState<ToolItem | null>(null);
  const [detailItem, setDetailItem] = useState<ToolViewData | null>(null);
  const [deleting, setDeleting] = useState(false);

  const openCreate = () => navigate(`/app/toolsets/${toolsetId}/tools/new`);

  const openEdit = (item: ToolItem) =>
    navigate(`/app/toolsets/${toolsetId}/tools/${item.tool_id}/edit`);

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await mutations.deleteTool(deleteTarget.tool_id);
      setDeleteTarget(null);
      refresh();
    } catch {
      // dialog stays open so user can retry
    } finally {
      setDeleting(false);
    }
  };

  const canWrite = hasPermission('tools:write');
  const canDelete = hasPermission('tools:delete');

  const rowActions = (item: ToolItem): RowMenuAction[] => {
    const itemIsReadOnly = isReadOnlyToolset(item.toolset_id);
    const readOnlyLabel = isExternalToolset(item.toolset_id)
      ? 'External MCP'
      : 'Built-in';
    return [
      {
        key: 'detail',
        label: 'View detail',
        icon: <VisibilityIcon fontSize="small" />,
        onClick: () => setDetailItem(toolViewData(item)),
      },
      {
        key: 'edit',
        label: 'Edit',
        icon: <EditIcon fontSize="small" />,
        onClick: () => openEdit(item),
        disabled: itemIsReadOnly || !canWrite,
        tooltip: itemIsReadOnly
          ? `${readOnlyLabel} tools cannot be edited`
          : !canWrite
            ? 'You do not have permission to edit tools'
            : undefined,
      },
      {
        key: 'history',
        label: 'View history',
        icon: <HistoryIcon fontSize="small" />,
        onClick: () =>
          navigate(`/app/toolsets/${toolsetId}/tools/${item.tool_id}/history`, {
            state: { fromLabel: 'Tools' } satisfies BackState,
          }),
        disabled: itemIsReadOnly,
        tooltip: itemIsReadOnly
          ? `${readOnlyLabel} tools have no version history`
          : undefined,
      },
      {
        key: 'delete',
        label: 'Delete',
        icon: <DeleteIcon fontSize="small" />,
        onClick: () => setDeleteTarget(item),
        disabled: itemIsReadOnly || !canDelete,
        tooltip: itemIsReadOnly
          ? `${readOnlyLabel} tools cannot be deleted`
          : !canDelete
            ? 'You do not have permission to delete tools'
            : undefined,
        destructive: true,
        dividerBefore: true,
      },
    ];
  };

  const displayTools: ToolItem[] = tools;
  const columns: ListTableColumn<ToolItem>[] = [
    {
      key: 'name',
      label: 'Name',
      cellSx: listTablePrimaryCellSx,
      render: (item) => (
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
          onClick={() => setDetailItem(toolViewData(item))}
        >
          {item.name}
        </Typography>
      ),
    },
    {
      key: 'slug',
      label: 'Slug',
      hideBelow: 'lg',
      cellSx: listTableMonoCellSx,
      render: (item) => mcpNameForTool(item),
    },
    {
      key: 'description',
      label: 'Description',
      hideBelow: 'md',
      cellSx: descriptionColumnSx,
      render: (item) => (
        <Typography
          variant="body2"
          color="text.secondary"
          sx={listTableTruncateSx}
        >
          {item.description || '—'}
        </Typography>
      ),
    },
    {
      key: 'status',
      label: 'Status',
      cellSx: statusColumnSx,
      render: (item) => (
        <Chip
          label={toolStatus(item).label}
          color={toolStatus(item).enabled ? 'success' : 'default'}
          size="small"
        />
      ),
    },
    {
      key: 'parameters',
      label: 'Parameters',
      hideBelow: 'md',
      cellSx: paramsColumnSx,
      render: (item) =>
        item.parameters.length === 0 ? '—' : item.parameters.length,
    },
    {
      key: 'version',
      stacked: 'meta',
      label: 'Version',
      hideBelow: 'sm',
      cellSx: versionColumnSx,
      render: (item) =>
        isReadOnlyToolset(item.toolset_id) ? '—' : `v${item.current_version}`,
    },
    {
      key: 'updated_at',
      stacked: 'meta',
      label: 'Last updated',
      hideBelow: 'xl',
      cellSx: updatedAtColumnSx,
      render: (item) =>
        isReadOnlyToolset(item.toolset_id) || !item.updated_at
          ? '—'
          : new Date(item.updated_at).toLocaleString(),
    },
    {
      key: 'updated_by',
      stacked: 'meta',
      label: 'Updated by',
      hideBelow: 'lg',
      cellSx: updatedByColumnSx,
      render: (item) =>
        isReadOnlyToolset(item.toolset_id) ? (
          '—'
        ) : item.updated_by ? (
          <UserDisplay userId={item.updated_by} />
        ) : (
          <UserDisplay userId={item.created_by} />
        ),
    },
    {
      key: 'actions',
      align: 'right',
      cellSx: listTableActionColumnSx,
      render: (item) => <RowMenu actions={rowActions(item)} />,
    },
  ];
  const filterGroups: ListTableFilterGroup<ToolItem>[] = [
    {
      key: 'type',
      label: 'Type',
      icon: <BadgeIcon fontSize="small" />,
      options: [
        {
          key: 'builtin',
          label: 'Built-in',
          icon: <BadgeIcon fontSize="small" />,
          matches: (item) => isBuiltinToolset(item.toolset_id),
        },
        {
          key: 'external',
          label: 'External MCP',
          icon: <HubIcon fontSize="small" />,
          matches: (item) => isExternalToolset(item.toolset_id),
        },
        {
          key: 'user_defined',
          label: 'User-defined',
          icon: <PersonOutlineIcon fontSize="small" />,
          matches: (item) => !isReadOnlyToolset(item.toolset_id),
        },
      ],
    },
    {
      key: 'enabled',
      label: 'Enabled',
      icon: <ToggleOnIcon fontSize="small" />,
      options: [
        {
          key: 'enabled',
          label: 'Enabled',
          icon: <ToggleOnIcon fontSize="small" />,
          matches: (item) => toolStatus(item).enabled,
        },
        {
          key: 'disabled',
          label: 'Disabled',
          icon: <ToggleOffIcon fontSize="small" />,
          matches: (item) => !toolStatus(item).enabled,
        },
      ],
    },
  ];

  return (
    <>
      <Box sx={pageContentSx}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <Button
            size="small"
            startIcon={<ArrowBackIcon />}
            onClick={() => navigate('/app/toolsets')}
          >
            Back to toolsets
          </Button>
        </Box>

        <ListPageHeader
          title={
            <Typography variant="h1">
              Tools
              {isBuiltin && (
                <Chip
                  label="Built-in"
                  size="small"
                  variant="outlined"
                  color="primary"
                  sx={{ ml: 1, verticalAlign: 'middle' }}
                />
              )}
              {isExternal && (
                <Chip
                  label="External MCP"
                  size="small"
                  variant="outlined"
                  color="secondary"
                  sx={{ ml: 1, verticalAlign: 'middle' }}
                />
              )}
            </Typography>
          }
          action={
            !isReadOnly &&
            canWrite && (
              <Button
                variant="contained"
                startIcon={<AddIcon />}
                onClick={openCreate}
              >
                New tool
              </Button>
            )
          }
        />

        <ListViewState
          loading={loading}
          error={error}
          errorMessage="Failed to load tools"
        >
          <ListTable
            rows={displayTools}
            columns={columns}
            getRowKey={(item) => item.tool_id}
            emptyMessage={
              isExternal
                ? 'No tools were discovered from this external MCP proxy.'
                : 'No tools yet. Create one above.'
            }
            filterGroups={filterGroups}
          />
        </ListViewState>
      </Box>

      <ToolDetailDialog
        open={!!detailItem}
        onClose={() => setDetailItem(null)}
        data={detailItem}
      />

      <ConfirmDeleteDialog
        open={!!deleteTarget}
        title="Delete tool?"
        deleting={deleting}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
      >
        Permanently delete <strong>{deleteTarget?.name}</strong> and all its
        versions? This cannot be undone.
      </ConfirmDeleteDialog>
    </>
  );
}

export default ToolsetTools;
