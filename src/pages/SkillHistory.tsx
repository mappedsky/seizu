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
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import HistoryIcon from '@mui/icons-material/History';
import RestoreIcon from '@mui/icons-material/Restore';
import { useNavigate, useLocation, useParams } from 'react-router-dom';
import { Helmet } from 'react-helmet';
import {
  SkillVersion,
  useSkillMutations,
  useSkillVersionsList,
  mcpNameForSkill,
} from 'src/hooks/useSkillsetsApi';
import { useToolCatalog } from 'src/hooks/useToolsetsApi';
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

function SkillVersionDetailDialog({
  version,
  onClose,
}: {
  version: SkillVersion | null;
  onClose: () => void;
}) {
  if (!version) return null;

  return (
    <Dialog open onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{version.name}</DialogTitle>
      <DialogContent dividers>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Box>
            <Typography
              variant="subtitle2"
              color="text.secondary"
              sx={{ mb: 0.5 }}
            >
              Slug
            </Typography>
            <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
              {mcpNameForSkill(version)}
            </Typography>
          </Box>
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
          {version.triggers.length > 0 && (
            <Box>
              <Typography
                variant="subtitle2"
                color="text.secondary"
                sx={{ mb: 0.5 }}
              >
                Triggers
              </Typography>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                {version.triggers.map((trigger) => (
                  <Chip key={trigger} label={trigger} size="small" />
                ))}
              </Box>
            </Box>
          )}
          {version.tools_required.length > 0 && (
            <Box>
              <Typography
                variant="subtitle2"
                color="text.secondary"
                sx={{ mb: 0.5 }}
              >
                Tools Required
              </Typography>
              <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                {version.tools_required.map((tool) => (
                  <Chip
                    key={tool}
                    label={tool}
                    size="small"
                    variant="outlined"
                  />
                ))}
              </Box>
            </Box>
          )}
          <Box>
            <Typography
              variant="subtitle2"
              color="text.secondary"
              sx={{ mb: 0.5 }}
            >
              Template
            </Typography>
            <Box
              component="pre"
              sx={{
                bgcolor: 'action.hover',
                p: 2,
                borderRadius: 1,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                m: 0,
                fontFamily: 'monospace',
                fontSize: 13,
              }}
            >
              {version.template}
            </Box>
          </Box>
          <Box>
            <Typography
              variant="subtitle2"
              color="text.secondary"
              sx={{ mb: 0.5 }}
            >
              Parameters
            </Typography>
            {version.parameters.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No parameters.
              </Typography>
            ) : (
              <TableContainer component={Paper} variant="outlined">
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Name</TableCell>
                      <TableCell>Type</TableCell>
                      <TableCell>Required</TableCell>
                      <TableCell>Default</TableCell>
                      <TableCell>Description</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {version.parameters.map((param) => (
                      <TableRow key={param.name}>
                        <TableCell
                          sx={{ fontFamily: 'monospace', fontWeight: 600 }}
                        >
                          {param.name}
                        </TableCell>
                        <TableCell>{param.type}</TableCell>
                        <TableCell sx={{ color: 'text.secondary' }}>
                          {param.required ? 'Yes' : 'No'}
                        </TableCell>
                        <TableCell
                          sx={{
                            color: 'text.secondary',
                            fontFamily: 'monospace',
                          }}
                        >
                          {param.default !== null && param.default !== undefined
                            ? String(param.default)
                            : '-'}
                        </TableCell>
                        <TableCell sx={{ color: 'text.secondary' }}>
                          {param.description || '-'}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Box>
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}

function SkillHistory() {
  const { skillsetId, skillId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { fromLabel } = (location.state ?? {}) as BackState;
  const { versions, loading, error } = useSkillVersionsList(
    skillsetId ?? null,
    skillId ?? null,
  );
  const { updateSkill } = useSkillMutations(skillsetId ?? '');
  const { tools: catalog } = useToolCatalog();
  const hasPermission = usePermissions();
  const [detailVersion, setDetailVersion] = useState<SkillVersion | null>(null);
  const [missingToolsTarget, setMissingToolsTarget] =
    useState<SkillVersion | null>(null);
  const [missingToolNames, setMissingToolNames] = useState<string[]>([]);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const sorted = [...versions].sort((a, b) => b.version - a.version);
  const latestVersion = sorted[0]?.version;
  const name = sorted[0]?.name;

  const rowActions = (version: SkillVersion): RowMenuAction[] => {
    const isCurrent = version.version === latestVersion;
    const canWrite = hasPermission('skills:write');
    return [
      {
        key: 'restore',
        label: 'Restore',
        icon: <RestoreIcon fontSize="small" />,
        onClick: () => handleRestoreClick(version),
        disabled: isCurrent || !canWrite,
        tooltip: isCurrent
          ? 'This is already the current version'
          : !canWrite
            ? 'You do not have permission to restore skill versions'
            : undefined,
      },
    ];
  };

  const columns: ListTableColumn<SkillVersion>[] = [
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

  const handleRestoreClick = (version: SkillVersion) => {
    const catalogSet = new Set(catalog.map((tool) => tool.mcp_name));
    const missing = (version.tools_required ?? []).filter(
      (tool) => !catalogSet.has(tool),
    );
    if (missing.length > 0) {
      setMissingToolsTarget(version);
      setMissingToolNames(missing);
      return;
    }
    void handleRestore(version).catch((err: Error) =>
      setRestoreError(err.message),
    );
  };

  async function handleRestore(
    version: SkillVersion,
    toolsRequired: string[] = version.tools_required ?? [],
  ) {
    if (!skillId) return;
    await updateSkill(skillId, {
      name: version.name,
      description: version.description,
      template: version.template,
      parameters: version.parameters,
      triggers: version.triggers,
      tools_required: toolsRequired,
      enabled: version.enabled,
      comment: `Restored from version ${version.version}`,
    });
    navigate(`/app/skillsets/${skillsetId}/skills`);
  }

  const handleMissingToolsConfirm = () => {
    if (!missingToolsTarget) return;
    const catalogSet = new Set(catalog.map((tool) => tool.mcp_name));
    const filtered = (missingToolsTarget.tools_required ?? []).filter((tool) =>
      catalogSet.has(tool),
    );
    setMissingToolsTarget(null);
    setMissingToolNames([]);
    void handleRestore(missingToolsTarget, filtered).catch((err: Error) =>
      setRestoreError(err.message),
    );
  };

  return (
    <Box sx={pageContentSx}>
      <Helmet>
        <title>{name ? `History - ${name} | Seizu` : 'History | Seizu'}</title>
      </Helmet>
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
      {restoreError && <Typography color="error">{restoreError}</Typography>}
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
      <SkillVersionDetailDialog
        version={detailVersion}
        onClose={() => setDetailVersion(null)}
      />
      <Dialog
        open={!!missingToolsTarget}
        onClose={() => {
          setMissingToolsTarget(null);
          setMissingToolNames([]);
        }}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Remove missing tool references?</DialogTitle>
        <DialogContent>
          <Typography color="text.secondary">
            The following tools are no longer available and will be removed
            before restore:
          </Typography>
          <Box component="ul" sx={{ mt: 1, mb: 0, pl: 2 }}>
            {missingToolNames.map((tool) => (
              <Box
                component="li"
                key={tool}
                sx={{ fontFamily: 'monospace', fontSize: 13 }}
              >
                {tool}
              </Box>
            ))}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => {
              setMissingToolsTarget(null);
              setMissingToolNames([]);
            }}
          >
            Cancel
          </Button>
          <Button variant="contained" onClick={handleMissingToolsConfirm}>
            Restore anyway
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}

export default SkillHistory;
