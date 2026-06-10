import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import { Box, Button, Chip, Tooltip, Typography } from '@mui/material';
import ConstellationSpinner from 'src/components/ConstellationSpinner';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import HistoryIcon from '@mui/icons-material/History';
import RestoreIcon from '@mui/icons-material/Restore';
import {
  RoleVersion,
  isBuiltinRole,
  useRoleMutations,
  useRoleVersionsList,
} from 'src/hooks/useRolesApi';
import ListTable, {
  ListTableColumn,
  listTableActionColumnSx,
  listTableSecondaryCellSx,
} from 'src/components/ListTable';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissionState } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

const savedColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const authorColumnSx = { ...listTableSecondaryCellSx, width: 150 };
const permissionsColumnSx = { width: '28%' };
const commentColumnSx = { ...listTableSecondaryCellSx, width: '24%' };

function permissionSummary(version: RoleVersion) {
  const visible = version.permissions.slice(0, 4);
  const remaining = version.permissions.length - visible.length;
  const hidden = version.permissions.slice(visible.length);
  return (
    <Box
      sx={{ display: 'flex', flexWrap: 'nowrap', gap: 0.5, overflow: 'hidden' }}
    >
      {visible.map((permission) => (
        <Chip
          key={permission}
          label={permission}
          size="small"
          variant="outlined"
        />
      ))}
      {remaining > 0 && (
        <Tooltip
          title={
            <Box
              sx={{
                display: 'flex',
                flexDirection: 'column',
                gap: 0.5,
                py: 0.5,
              }}
            >
              {hidden.map((permission) => (
                <Typography key={permission} variant="body2">
                  {permission}
                </Typography>
              ))}
            </Box>
          }
          placement="top"
        >
          <Chip label={`+${remaining}`} size="small" variant="outlined" />
        </Tooltip>
      )}
    </Box>
  );
}

function RoleHistory() {
  const { roleId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { fromLabel } = (location.state ?? {}) as BackState;
  const { hasPermission, loading: permissionsLoading } = usePermissionState();
  const canRead = hasPermission('roles:read');
  const builtin = !!roleId && isBuiltinRole(roleId);

  const { versions, loading, error } = useRoleVersionsList(
    roleId ?? null,
    canRead && !builtin,
  );
  const { updateRole } = useRoleMutations();

  const sorted = [...versions].sort((a, b) => b.version - a.version);
  const latestVersion = sorted[0]?.version;
  const roleName = sorted[0]?.name;

  const rowActions = (version: RoleVersion): RowMenuAction[] => {
    const isCurrent = version.version === latestVersion;
    const canWrite = hasPermission('roles:write');
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
            ? 'You do not have permission to restore role versions'
            : undefined,
      },
    ];
  };

  const columns: ListTableColumn<RoleVersion>[] = [
    {
      key: 'version',
      label: 'Version',
      cellSx: { width: 120 },
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
      key: 'permissions',
      label: 'Permissions',
      hideBelow: 'lg',
      cellSx: permissionsColumnSx,
      render: (version) => permissionSummary(version),
    },
    {
      key: 'comment',
      label: 'Comment',
      hideBelow: 'xl',
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

  async function handleRestore(version: RoleVersion) {
    if (!roleId) return;
    await updateRole(roleId, {
      name: version.name,
      description: version.description,
      permissions: version.permissions,
      comment: `Restored from version ${version.version}`,
    });
    navigate('/app/roles');
  }

  if (permissionsLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <ConstellationSpinner size={48} />
      </Box>
    );
  }

  if (!canRead) {
    return (
      <Box sx={pageContentSx}>
        <Typography>You do not have access to role management.</Typography>
      </Box>
    );
  }

  if (builtin) {
    return (
      <Box sx={pageContentSx}>
        <Button
          size="small"
          startIcon={<ArrowBackIcon />}
          onClick={() => navigate('/app/roles')}
          sx={{ mb: 2 }}
        >
          Back to roles
        </Button>
        <Typography>Built-in roles do not have version history.</Typography>
      </Box>
    );
  }

  return (
    <>
      <Helmet>
        <title>
          {roleName ? `History - ${roleName} | Seizu` : 'History | Seizu'}
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
            Version history{roleName ? ` - ${roleName}` : ''}
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

export default RoleHistory;
