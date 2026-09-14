import { useState } from 'react';
import {
  Box,
  Button,
  ButtonBase,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import HistoryIcon from '@mui/icons-material/History';
import RestoreIcon from '@mui/icons-material/Restore';
import { useNavigate, useLocation, useParams } from 'react-router-dom';
import PageTitle from 'src/components/PageTitle';
import {
  SkillsetVersion,
  useSkillsetMutations,
  useSkillsetVersionsList,
} from 'src/hooks/useSkillsetsApi';
import ListTable, {
  ListTableColumn,
  listTableActionColumnSx,
  listTableSecondaryCellSx,
} from 'src/components/ListTable';
import ListViewState from 'src/components/ListViewState';
import RowMenu, { RowMenuAction } from 'src/components/RowMenu';
import UserDisplay from 'src/components/UserDisplay';
import { usePermissions } from 'src/hooks/usePermissions';
import type { BackState } from 'src/navigation';
import { pageContentSx } from 'src/theme/layout';

const savedColumnSx = { ...listTableSecondaryCellSx, width: 180 };
const authorColumnSx = { ...listTableSecondaryCellSx, width: 150 };
const commentColumnSx = { ...listTableSecondaryCellSx, width: '28%' };

function SkillsetVersionDetailDialog({
  version,
  onClose,
}: {
  version: SkillsetVersion | null;
  onClose: () => void;
}) {
  if (!version) return null;

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{version.name}</DialogTitle>
      <DialogContent dividers>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Box>
            <Typography
              variant="subtitle2"
              color="text.secondary"
              sx={{ mb: 0.5 }}
            >
              Version
            </Typography>
            <Typography variant="body2">v{version.version}</Typography>
          </Box>
          <Box>
            <Typography
              variant="subtitle2"
              color="text.secondary"
              sx={{ mb: 0.5 }}
            >
              Status
            </Typography>
            <Chip
              label={version.enabled ? 'Enabled' : 'Disabled'}
              color={version.enabled ? 'success' : 'default'}
              size="small"
            />
          </Box>
          {version.description && (
            <Box>
              <Typography
                variant="subtitle2"
                color="text.secondary"
                sx={{ mb: 0.5 }}
              >
                Description
              </Typography>
              <Typography variant="body2">{version.description}</Typography>
            </Box>
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}

function SkillsetHistory() {
  const { skillsetId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const hasPermission = usePermissions();
  const { fromLabel } = (location.state ?? {}) as BackState;
  const { versions, loading, error } = useSkillsetVersionsList(
    skillsetId ?? null,
  );
  const { updateSkillset } = useSkillsetMutations();
  const [detailVersion, setDetailVersion] = useState<SkillsetVersion | null>(
    null,
  );
  const sorted = [...versions].sort((a, b) => b.version - a.version);
  const latestVersion = sorted[0]?.version;
  const name = sorted[0]?.name;

  const rowActions = (version: SkillsetVersion): RowMenuAction[] => {
    const isCurrent = version.version === latestVersion;
    const canWrite = hasPermission('skillsets:write');
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
            ? 'You do not have permission to restore skillset versions'
            : undefined,
      },
    ];
  };

  const columns: ListTableColumn<SkillsetVersion>[] = [
    {
      key: 'version',
      label: 'Version',
      cellSx: { width: 120 },
      render: (version) => {
        const isCurrent = version.version === latestVersion;
        return (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <ButtonBase
              onClick={() => setDetailVersion(version)}
              sx={{
                font: 'inherit',
                fontWeight: isCurrent ? 'bold' : 'medium',
                color: 'inherit',
                borderRadius: 0,
                textAlign: 'left',
                '&:hover': { textDecoration: 'underline' },
              }}
            >
              <Typography component="span" sx={{ lineHeight: 1.4 }}>
                v{version.version}
              </Typography>
            </ButtonBase>
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

  async function handleRestore(version: SkillsetVersion) {
    if (!skillsetId) return;
    await updateSkillset(skillsetId, {
      name: version.name,
      description: version.description,
      enabled: version.enabled,
      comment: `Restored from version ${version.version}`,
    });
    navigate('/app/skillsets');
  }

  return (
    <Box sx={pageContentSx}>
      <PageTitle>
        {name ? `History - ${name} | Seizu` : 'History | Seizu'}
      </PageTitle>
      {fromLabel && (
        <Button
          startIcon={<ArrowBackIcon />}
          onClick={() => navigate(-1)}
          sx={{ mb: 2 }}
        >
          Back to {fromLabel}
        </Button>
      )}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 3 }}>
        <HistoryIcon color="action" />
        <Typography variant="h1">
          Version history{name ? ` - ${name}` : ''}
        </Typography>
      </Box>
      <ListViewState
        loading={loading}
        error={error}
        errorMessage="Failed to load history"
      >
        <ListTable
          rows={sorted}
          columns={columns}
          getRowKey={(version) => version.version}
          emptyMessage="No versions found."
          pagination={false}
        />
      </ListViewState>
      <SkillsetVersionDetailDialog
        version={detailVersion}
        onClose={() => setDetailVersion(null)}
      />
    </Box>
  );
}

export default SkillsetHistory;
