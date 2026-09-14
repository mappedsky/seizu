import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Alert, Box, Button, Typography } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import HistoryIcon from '@mui/icons-material/History';
import RestoreIcon from '@mui/icons-material/Restore';
import VisibilityIcon from '@mui/icons-material/Visibility';
import ListPageHeader from 'src/components/ListPageHeader';
import ListTable, {
  type ListTableColumn,
  listTableActionColumnSx,
  listTableSecondaryCellSx,
} from 'src/components/ListTable';
import ListViewState from 'src/components/ListViewState';
import ModelProfileDetailDialog from 'src/components/ModelProfileDetailDialog';
import PageTitle from 'src/components/PageTitle';
import RowMenu, { type RowMenuAction } from 'src/components/RowMenu';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissionState } from 'src/hooks/usePermissions';
import {
  type ModelProfileVersion,
  useModelProfileMutations,
  useModelProfileVersionsList,
} from 'src/hooks/useModelProfilesApi';
import { pageContentSx } from 'src/theme/layout';
import { modelProfilePayload } from 'src/utils/modelProfilePayload';

function HistoryPage({ profileId }: { profileId: string }) {
  const navigate = useNavigate();
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const canRead = hasPermission('model_profiles:read');
  const canWrite = hasPermission('model_profiles:write');
  const { versions, loading, error, refresh } = useModelProfileVersionsList(
    canRead && !permissionsLoading ? profileId : null,
  );
  const { update } = useModelProfileMutations();
  const [viewing, setViewing] = useState<ModelProfileVersion | null>(null);
  const [restoring, setRestoring] = useState<number | null>(null);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const sorted = [...versions].sort((a, b) => b.version - a.version);
  const latest = sorted[0];

  const restore = async (version: ModelProfileVersion) => {
    if (!canWrite || restoring !== null || version.version === latest?.version)
      return;
    setRestoring(version.version);
    setRestoreError(null);
    try {
      await update(profileId, {
        ...modelProfilePayload(version),
        comment: `Restored from version ${version.version}`,
      });
      refresh();
    } catch (reason) {
      setRestoreError(
        reason instanceof Error
          ? reason.message
          : 'Failed to restore model profile',
      );
    } finally {
      setRestoring(null);
    }
  };
  const rowActions = (version: ModelProfileVersion): RowMenuAction[] => [
    {
      key: 'view',
      label: 'View',
      icon: <VisibilityIcon fontSize="small" />,
      onClick: () => setViewing(version),
    },
    {
      key: 'restore',
      label: restoring === version.version ? 'Restoring…' : 'Restore',
      icon: <RestoreIcon fontSize="small" />,
      onClick: () => void restore(version),
      disabled:
        version.version === latest?.version || !canWrite || restoring !== null,
      tooltip:
        version.version === latest?.version
          ? 'This is already the current version'
          : !canWrite
            ? 'You do not have permission to restore model profiles'
            : undefined,
    },
  ];
  const columns: ListTableColumn<ModelProfileVersion>[] = [
    {
      key: 'version',
      label: 'Version',
      cellSx: { width: 120 },
      render: (version) => (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography
            sx={{
              fontWeight:
                version.version === latest?.version ? 'bold' : 'medium',
            }}
          >
            v{version.version}
          </Typography>
          {version.version === latest?.version ? (
            <Typography component="span" variant="caption" color="primary">
              current
            </Typography>
          ) : null}
        </Box>
      ),
    },
    {
      key: 'name',
      label: 'Name',
      render: (version) => (
        <Typography
          variant="body2"
          sx={{ cursor: 'pointer', '&:hover': { textDecoration: 'underline' } }}
          onClick={() => setViewing(version)}
        >
          {version.name}
        </Typography>
      ),
    },
    {
      key: 'saved',
      label: 'Saved',
      hideBelow: 'sm',
      cellSx: { ...listTableSecondaryCellSx, width: 180 },
      render: (version) => new Date(version.created_at).toLocaleString(),
    },
    {
      key: 'created_by',
      label: 'Created By',
      hideBelow: 'md',
      cellSx: { ...listTableSecondaryCellSx, width: 150 },
      render: (version) => <UserDisplay userId={version.created_by} />,
    },
    {
      key: 'comment',
      label: 'Comment',
      hideBelow: 'lg',
      cellSx: { ...listTableSecondaryCellSx, width: '28%' },
      render: (version) => version.comment || '—',
    },
    {
      key: 'actions',
      align: 'right',
      cellSx: listTableActionColumnSx,
      textual: false,
      render: (version) => (
        <RowMenu
          label={`Actions for version ${version.version}`}
          actions={rowActions(version)}
        />
      ),
    },
  ];

  return (
    <Box sx={pageContentSx}>
      <PageTitle>
        {latest ? `History – ${latest.name} | Seizu` : 'History | Seizu'}
      </PageTitle>
      <Button
        startIcon={<ArrowBackIcon />}
        size="small"
        sx={{ mb: 1 }}
        onClick={() => navigate('/app/model-profiles')}
      >
        Back to Model profiles
      </Button>
      <ListPageHeader
        title={
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <HistoryIcon color="action" />
            <Typography variant="h1">
              Version history{latest ? ` – ${latest.name}` : ''}
            </Typography>
          </Box>
        }
      />
      {restoreError ? (
        <Alert severity="error" sx={{ mb: 2 }}>
          {restoreError}
        </Alert>
      ) : null}
      <ListViewState
        loading={permissionsLoading || loading}
        error={error}
        errorMessage="Failed to load version history"
      >
        {canRead ? (
          <ListTable
            rows={sorted}
            columns={columns}
            getRowKey={(version) => version.version}
            emptyMessage="No versions found."
          />
        ) : (
          <Typography>You do not have access to model profiles.</Typography>
        )}
      </ListViewState>
      <ModelProfileDetailDialog
        profile={viewing}
        version={viewing?.version}
        onClose={() => setViewing(null)}
      />
    </Box>
  );
}

export default function ModelProfileHistory() {
  const { profileId } = useParams();
  return <HistoryPage key={profileId} profileId={profileId ?? ''} />;
}
