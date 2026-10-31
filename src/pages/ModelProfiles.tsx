import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Button, Chip, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import ToggleOnIcon from '@mui/icons-material/ToggleOn';
import ToggleOffIcon from '@mui/icons-material/ToggleOff';
import ConfirmDeleteDialog from 'src/components/ConfirmDeleteDialog';
import ModelProfileDetailDialog from 'src/components/ModelProfileDetailDialog';
import ListPageHeader from 'src/components/ListPageHeader';
import ListTable, {
  type ListTableColumn,
  type ListTableFilterGroup,
  listTableActionColumnSx,
  listTableMonoCellSx,
  listTablePrimaryCellSx,
  listTableSecondaryCellSx,
  listTableTruncateSx,
} from 'src/components/ListTable';
import ListViewState from 'src/components/ListViewState';
import PageTitle from 'src/components/PageTitle';
import RowMenu, { type RowMenuAction } from 'src/components/RowMenu';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissionState } from 'src/hooks/usePermissions';
import {
  type ModelProfile,
  useModelProfileMutations,
  useModelProfilesList,
} from 'src/hooks/useModelProfilesApi';
import { pageContentSx } from 'src/theme/layout';

function formatUsd(value: number): string {
  return `$${value}`;
}

export default function ModelProfiles() {
  const navigate = useNavigate();
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const canRead = hasPermission('model_profiles:read');
  const canWrite = hasPermission('model_profiles:write');
  const canDelete = hasPermission('model_profiles:delete');
  const { profiles, globalRunCostBudgetUsd, loading, error, refresh } =
    useModelProfilesList(canRead);
  const mutations = useModelProfileMutations();
  const [detail, setDetail] = useState<ModelProfile | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ModelProfile | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const deleteProfile = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await mutations.remove(deleteTarget.profile_id);
      setDeleteTarget(null);
      await refresh();
    } catch (reason) {
      setDeleteError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDeleting(false);
    }
  };
  const rowActions = (profile: ModelProfile): RowMenuAction[] => [
    {
      key: 'edit',
      label: 'Edit',
      icon: <EditIcon fontSize="small" />,
      onClick: () =>
        navigate(
          `/app/model-profiles/${encodeURIComponent(profile.profile_id)}/edit`,
        ),
      disabled: !canWrite,
      tooltip: !canWrite
        ? 'You do not have permission to edit model profiles'
        : undefined,
    },
    {
      key: 'history',
      label: 'View history',
      icon: <HistoryIcon fontSize="small" />,
      onClick: () =>
        navigate(
          `/app/model-profiles/${encodeURIComponent(profile.profile_id)}/history`,
        ),
    },
    {
      key: 'delete',
      label: 'Delete',
      icon: <DeleteIcon fontSize="small" />,
      onClick: () => {
        setDeleteError(null);
        setDeleteTarget(profile);
      },
      disabled: !canDelete,
      tooltip: !canDelete
        ? 'You do not have permission to delete model profiles'
        : undefined,
      destructive: true,
      dividerBefore: true,
    },
  ];
  const columns: ListTableColumn<ModelProfile>[] = [
    {
      key: 'name',
      label: 'Name',
      cellSx: listTablePrimaryCellSx,
      render: (profile) => (
        <Box
          sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 0 }}
        >
          <Typography
            variant="body2"
            sx={[
              {
                cursor: 'pointer',
                flex: 1,
                fontWeight: 500,
                '&:hover': { textDecoration: 'underline' },
              },
              listTableTruncateSx,
            ]}
            onClick={() => setDetail(profile)}
          >
            {profile.name}
          </Typography>
          {profile.is_default ? (
            <Chip
              label="Default"
              size="small"
              color="primary"
              variant="outlined"
              sx={{ flexShrink: 0, height: 20, fontSize: '0.7rem' }}
            />
          ) : null}
        </Box>
      ),
    },
    {
      key: 'primary',
      label: 'Primary',
      cellSx: listTableMonoCellSx,
      render: (profile) => profile.primary.model_id,
    },
    {
      key: 'economy',
      label: 'Economy',
      hideBelow: 'md',
      cellSx: listTableMonoCellSx,
      render: (profile) => profile.economy.model_id,
    },
    {
      key: 'cost',
      label: 'Cost cap',
      cellSx: { ...listTableSecondaryCellSx, width: 160 },
      render: (profile) => (
        <>
          {formatUsd(profile.run_cost_budget_usd)}
          {globalRunCostBudgetUsd > 0 &&
          profile.run_cost_budget_usd > globalRunCostBudgetUsd ? (
            <Typography
              color="warning.main"
              variant="caption"
              sx={{ display: 'block' }}
            >
              Limited to {formatUsd(globalRunCostBudgetUsd)} globally
            </Typography>
          ) : null}
        </>
      ),
    },
    {
      key: 'status',
      label: 'Status',
      cellSx: { width: 120 },
      render: (profile) => (
        <Chip
          label={profile.enabled ? 'Enabled' : 'Disabled'}
          color={profile.enabled ? 'success' : 'default'}
          size="small"
        />
      ),
    },
    {
      key: 'version',
      stacked: 'meta',
      label: 'Version',
      hideBelow: 'sm',
      cellSx: { ...listTableSecondaryCellSx, width: 96 },
      render: (profile) => `v${profile.current_version}`,
    },
    {
      key: 'updated_at',
      stacked: 'meta',
      label: 'Latest Update',
      hideBelow: 'xl',
      cellSx: { ...listTableSecondaryCellSx, width: 180 },
      render: (profile) => new Date(profile.updated_at).toLocaleString(),
    },
    {
      key: 'updated_by',
      stacked: 'meta',
      label: 'Updated By',
      hideBelow: 'lg',
      cellSx: { ...listTableSecondaryCellSx, width: 150 },
      render: (profile) => (
        <UserDisplay userId={profile.updated_by ?? profile.created_by} />
      ),
    },
    {
      key: 'actions',
      align: 'right',
      cellSx: listTableActionColumnSx,
      textual: false,
      render: (profile) => (
        <RowMenu
          label={`Actions for ${profile.name}`}
          actions={rowActions(profile)}
        />
      ),
    },
  ];
  const filterGroups: ListTableFilterGroup<ModelProfile>[] = [
    {
      key: 'status',
      label: 'Status',
      icon: <ToggleOnIcon fontSize="small" />,
      options: [
        {
          key: 'enabled',
          label: 'Enabled',
          icon: <ToggleOnIcon fontSize="small" />,
          matches: (profile) => profile.enabled,
        },
        {
          key: 'disabled',
          label: 'Disabled',
          icon: <ToggleOffIcon fontSize="small" />,
          matches: (profile) => !profile.enabled,
        },
      ],
    },
  ];
  return (
    <Box sx={pageContentSx}>
      <PageTitle>Model profiles | Seizu</PageTitle>
      <ListPageHeader
        title="Model profiles"
        action={
          canWrite && !permissionsLoading ? (
            <Button
              startIcon={<AddIcon />}
              variant="contained"
              onClick={() => navigate('/app/model-profiles/new')}
            >
              New profile
            </Button>
          ) : null
        }
      />
      <ListViewState
        loading={permissionsLoading || loading}
        error={error}
        errorMessage="Failed to load model profiles"
      >
        {canRead ? (
          <ListTable
            rows={profiles}
            columns={columns}
            getRowKey={(profile) => profile.profile_id}
            filterGroups={filterGroups}
            emptyMessage="No model profiles are configured. Chat uses environment settings until the first enabled profile is created."
          />
        ) : (
          <Typography>You do not have access to model profiles.</Typography>
        )}
      </ListViewState>
      <ConfirmDeleteDialog
        open={deleteTarget !== null}
        title="Delete model profile?"
        deleting={deleting}
        error={deleteError}
        onClose={() => {
          if (!deleting) setDeleteTarget(null);
        }}
        onConfirm={() => void deleteProfile()}
      >
        Permanently delete <strong>{deleteTarget?.name}</strong> and its version
        history? This cannot be undone.
      </ConfirmDeleteDialog>
      <ModelProfileDetailDialog
        profile={detail}
        version={detail?.current_version}
        onClose={() => setDetail(null)}
      />
    </Box>
  );
}
